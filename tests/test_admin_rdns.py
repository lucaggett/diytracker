"""Tests for the cached reverse-DNS helper (diytracker/admin/rdns.py).

Nothing here touches a resolver: _lookup is monkeypatched in every test, and
a call that reached the network would show up as a failure rather than a
slow run.
"""

from datetime import timedelta

import pytest

from diytracker.admin import rdns
from diytracker.models import IpHostname, db, utcnow


@pytest.fixture
def resolver(monkeypatch):
    """Record what gets looked up; answers come from the given map."""
    calls = []

    def install(answers):
        def fake(ip):
            calls.append(ip)
            return answers.get(ip)

        monkeypatch.setattr(rdns, "_lookup", fake)
        return calls

    return install


class TestResolveMany:
    def test_resolves_and_caches(self, app, resolver):
        calls = resolver({"1.1.1.1": "one.example.com"})
        with app.app_context():
            assert rdns.resolve_many(["1.1.1.1"]) == {"1.1.1.1": "one.example.com"}
            assert db.session.get(IpHostname, "1.1.1.1").hostname == "one.example.com"
            # Second call is served from the cache.
            assert rdns.resolve_many(["1.1.1.1"]) == {"1.1.1.1": "one.example.com"}
        assert calls == ["1.1.1.1"]

    def test_failures_are_cached_as_null(self, app, resolver):
        calls = resolver({})
        with app.app_context():
            assert rdns.resolve_many(["9.9.9.9"]) == {"9.9.9.9": None}
            row = db.session.get(IpHostname, "9.9.9.9")
            assert row.hostname is None
            rdns.resolve_many(["9.9.9.9"])
        assert calls == ["9.9.9.9"], "a cached miss must not be re-probed"

    def test_stale_entries_are_re_resolved(self, app, resolver):
        calls = resolver({"1.1.1.1": "new.example.com"})
        with app.app_context():
            db.session.add(
                IpHostname(
                    ip="1.1.1.1",
                    hostname="old.example.com",
                    checked_at=utcnow() - rdns.MAX_AGE - timedelta(days=1),
                )
            )
            db.session.commit()
            assert rdns.resolve_many(["1.1.1.1"]) == {"1.1.1.1": "new.example.com"}
        assert calls == ["1.1.1.1"]

    def test_lookup_budget_is_respected(self, app, resolver):
        ips = [f"10.0.0.{i}" for i in range(5)]
        calls = resolver({ip: f"host{ip}.example.com" for ip in ips})
        with app.app_context():
            result = rdns.resolve_many(ips, max_lookups=2)
        assert len(calls) == 2
        # Only what was asked comes back; the rest is absent, not None, so
        # callers can tell "no PTR" from "not asked yet".
        assert set(result) == set(ips[:2])

    def test_order_decides_who_gets_the_budget(self, app, resolver):
        calls = resolver({})
        with app.app_context():
            rdns.resolve_many(["1.1.1.1", "2.2.2.2", "3.3.3.3"], max_lookups=1)
        assert calls == ["1.1.1.1"]

    def test_empty_input(self, app, resolver):
        calls = resolver({})
        with app.app_context():
            assert rdns.resolve_many([]) == {}
        assert calls == []

    def test_lookup_swallows_resolver_errors(self, monkeypatch):
        def boom(ip):
            raise OSError("resolver down")

        monkeypatch.setattr(rdns.socket, "gethostbyaddr", boom)
        assert rdns._lookup("1.1.1.1") is None

    def test_module_level_budget_is_read_at_call_time(self, app, resolver, monkeypatch):
        """MAX_LOOKUPS bound as a default argument would ignore this."""
        calls = resolver({})
        monkeypatch.setattr(rdns, "MAX_LOOKUPS", 1)
        with app.app_context():
            rdns.resolve_many(["1.1.1.1", "2.2.2.2"])
        assert len(calls) == 1
