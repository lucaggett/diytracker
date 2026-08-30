"""Tests for the admin logic layer (diytracker/admin/) and db_admin_cli.py CLI
contract. The TUI itself is exercised only as an import smoke test."""

import pytest

from diytracker.admin import events as admin_events
from diytracker.admin import invites, labels, queue_review, users
from diytracker.admin.core import AdminError
from diytracker.models import Event, Label, ScrapedEvent, Submitter, db


class TestInvites:
    @pytest.mark.parametrize("lang", invites.INVITE_LANGS)
    def test_sends_localized_invite(self, fake_smtp, lang):
        invites.send_invite_email("new@example.com", "tok123", lang)

        smtp = fake_smtp.instances[0]
        assert smtp.host == "smtp.example.com"
        assert smtp.port == 465
        assert smtp.login_args == ("bot@example.com", "hunter2")
        msg = smtp.sent_message
        assert msg["To"] == "new@example.com"
        assert msg["From"] == "info@diytracker.ch"
        assert msg["Subject"] == invites.INVITE_MESSAGES[lang]["subject"]
        body = msg.get_content()
        assert "https://diytracker.ch/set-password/tok123" in body
        assert "7" in body  # the 7-day validity note

    def test_default_language_is_schwiizerduetsch(self, fake_smtp):
        invites.send_invite_email("new@example.com", "tok123")
        msg = fake_smtp.instances[0].sent_message
        assert msg["Subject"] == invites.INVITE_MESSAGES["gsw"]["subject"]

    def test_unknown_language_raises(self, fake_smtp):
        with pytest.raises(AdminError):
            invites.send_invite_email("new@example.com", "tok123", "de")
        assert not fake_smtp.instances

    def test_missing_env_raises(self, monkeypatch):
        from diytracker.services.mail import MailNotConfigured

        monkeypatch.delenv("EMAIL_SERVER", raising=False)
        with pytest.raises(MailNotConfigured):
            invites.send_invite_email("new@example.com", "tok123", "en")

    def test_every_language_has_token_placeholder(self):
        for lang, text in invites.INVITE_MESSAGES.items():
            assert "{token}" in text["body"], lang
            assert text["subject"]


class TestUsers:
    def test_add_user_returns_valid_token(self, app):
        token = users.add_user("fresh@example.com")
        user = Submitter.query.filter_by(email="fresh@example.com").one()
        assert user.invite_token == token
        assert user.invite_token_expiry is not None

    def test_add_duplicate_raises(self, app, make_user):
        make_user(email="dupe@example.com")
        with pytest.raises(AdminError):
            users.add_user("dupe@example.com")

    def test_list_users(self, app, make_user):
        make_user(email="a@example.com", is_admin=True)
        make_user(email="b@example.com", password=None)
        rows = users.list_users()
        assert [(r.email, r.is_admin, r.has_password) for r in rows] == [
            ("a@example.com", True, True),
            ("b@example.com", False, False),
        ]

    def test_set_flag_toggles(self, app, make_user):
        make_user(email="u@example.com")
        row = users.set_flag("u@example.com", "is_promoter", True)
        assert row.is_promoter
        row = users.set_flag("u@example.com", "is_promoter", False)
        assert not row.is_promoter

    def test_set_flag_rejects_unknown_flag(self, app, make_user):
        make_user(email="u@example.com")
        with pytest.raises(AdminError):
            users.set_flag("u@example.com", "password_hash", True)

    def test_set_password(self, app, make_user):
        user = make_user(email="u@example.com", password=None)
        users.set_password("u@example.com", "s3cret-pass")
        assert user.check_password("s3cret-pass")

    def test_set_empty_password_raises(self, app, make_user):
        make_user(email="u@example.com")
        with pytest.raises(AdminError):
            users.set_password("u@example.com", "")

    def test_reissue_invite_unknown_email_raises(self, app):
        with pytest.raises(AdminError):
            users.reissue_invite("ghost@example.com")

    def test_delete_user(self, app, make_user):
        make_user(email="gone@example.com")
        users.delete_user("gone@example.com")
        assert Submitter.query.filter_by(email="gone@example.com").first() is None


class TestEvents:
    def test_list_events_upcoming_filter(self, app, make_event):
        make_event(name="Future Show", days_from_now=5)
        make_event(name="Past Show", days_from_now=-5)
        upcoming = admin_events.list_events()
        assert [e.name for e in upcoming] == ["Future Show"]
        everything = admin_events.list_events(include_past=True, limit=0)
        assert {e.name for e in everything} == {"Future Show", "Past Show"}

    def test_list_events_uses_the_wall_clock_like_the_public_pages(
        self, app, make_event, monkeypatch, request
    ):
        # Event.date holds local Swiss time, so "upcoming" must be measured
        # against datetime.now(), not models.utcnow(). The clock is pinned to
        # Europe/Zurich because on a UTC host the two are identical and this
        # would assert nothing: the event below sits 90 minutes in the local
        # past but still an hour or two in the UTC future, so filtering on
        # utcnow() wrongly keeps it in the admin list.
        import time as _time
        from datetime import datetime, timedelta

        from diytracker.models import utcnow

        monkeypatch.setenv("TZ", "Europe/Zurich")
        _time.tzset()
        request.addfinalizer(_time.tzset)
        assert datetime.now() > utcnow(), "expected a UTC+x local clock"

        event = make_event(name="Already Started")
        event.date = datetime.now() - timedelta(minutes=90)
        db.session.commit()
        assert event.date > utcnow(), "event must straddle the two clocks"

        assert "Already Started" not in [e.name for e in admin_events.list_events()]
        assert event not in Event.query.filter(Event.date >= datetime.now()).all()

    def test_set_status(self, app, make_event):
        event = make_event()
        row = admin_events.set_status(event.id, "cancelled")
        assert row.status == "cancelled"
        assert db.session.get(Event, event.id).status == "cancelled"

    def test_set_status_rejects_unknown(self, app, make_event):
        event = make_event()
        with pytest.raises(AdminError):
            admin_events.set_status(event.id, "vaporized")

    def test_delete_event(self, app, make_event):
        event = make_event()
        admin_events.delete_event(event.id)
        assert db.session.get(Event, event.id) is None

    def test_delete_missing_event_raises(self, app):
        with pytest.raises(AdminError):
            admin_events.delete_event(99999)

    def test_genre_dedup_round_trip(self, app, make_event, make_venue):
        venue = make_venue()
        make_event(name="Show A", venue=venue, genre="punk")
        make_event(name="Show B", venue=venue, genre="Punk")
        plan = admin_events.scan_genre_dups()
        assert plan.mapping  # one spelling maps onto the canonical one
        changed = admin_events.apply_genre_merge(plan.mapping)
        assert changed >= 1
        genres = {e.genre for e in Event.query.all()}
        assert len(genres) == 1
        assert admin_events.scan_genre_dups().mapping == {}


def _make_scraped(**kw):
    from datetime import date, timedelta

    defaults = dict(
        source="konzibot",
        title="Dup Show",
        start_date=date.today() + timedelta(days=15),
        venue_name="Hall",
        city="Bern",
        approved=False,
    )
    defaults.update(kw)
    rec = ScrapedEvent(**defaults)
    db.session.add(rec)
    db.session.commit()
    return rec


class TestQueueReview:
    def test_list_flagged_only_returns_flagged_unapproved(self, app):
        _make_scraped(title="Clean", needs_review=False)
        _make_scraped(title="Flagged", needs_review=True, url="u1")
        _make_scraped(title="Resolved", needs_review=True, status="rejected", url="u2")
        rows = queue_review.list_flagged()
        assert [r.title for r in rows] == ["Flagged"]

    def test_discard_drops_from_queue(self, app):
        rec = _make_scraped(needs_review=True)
        queue_review.discard(rec.id)
        db.session.refresh(rec)
        assert rec.status == ScrapedEvent.STATUS_REJECTED
        assert rec.approved_at is not None
        assert rec.approved_event_id is None
        assert queue_review.list_flagged() == []

    def test_unflag_returns_to_web_queue(self, app):
        from diytracker.blueprints.submissions import parse_scraped_events

        rec = _make_scraped(title="False Positive", needs_review=True)
        assert "False Positive" not in [e["title"] for e in parse_scraped_events()[0]]
        queue_review.unflag(rec.id)
        db.session.refresh(rec)
        assert rec.needs_review is False
        assert "False Positive" in [e["title"] for e in parse_scraped_events()[0]]

    def test_actions_reject_bad_id(self, app):
        with pytest.raises(AdminError):
            queue_review.discard(99999)
        with pytest.raises(AdminError):
            queue_review.unflag(99999)

    def test_actions_reject_already_resolved(self, app):
        rec = _make_scraped(needs_review=True, status="rejected")
        with pytest.raises(AdminError):
            queue_review.discard(rec.id)


class TestLabels:
    @pytest.fixture
    def promoter(self, make_user):
        return make_user(email="promo@example.com", is_promoter=True)

    def test_create_and_list(self, app, promoter, make_event):
        row = labels.create_label("Kalter Schweiss", "promo@example.com")
        assert row.slug == "kalter-schweiss"
        event = make_event()
        event.label_id = row.id
        db.session.commit()
        listed = labels.list_labels()
        assert [(r.name, r.promoter, r.n_events) for r in listed] == [
            ("Kalter Schweiss", "promo@example.com", 1)
        ]

    def test_create_requires_promoter_flag(self, app, make_user):
        make_user(email="plain@example.com")
        with pytest.raises(AdminError, match="not a promoter"):
            labels.create_label("Nope", "plain@example.com")

    def test_create_rejects_duplicate_name_case_insensitive(self, app, promoter):
        labels.create_label("Doom Corp", "promo@example.com")
        with pytest.raises(AdminError, match="already exists"):
            labels.create_label("doom corp", "promo@example.com")

    def test_rename_regenerates_slug(self, app, promoter):
        row = labels.create_label("Old Name", "promo@example.com")
        updated = labels.rename_label(row.id, "Neuer Name")
        assert (updated.name, updated.slug) == ("Neuer Name", "neuer-name")

    def test_set_description_blank_clears(self, app, promoter):
        row = labels.create_label("L", "promo@example.com")
        assert labels.set_description(row.id, "  hello  ").description == "hello"
        assert labels.set_description(row.id, "   ").description == ""
        assert db.session.get(Label, row.id).description is None

    def test_reassign(self, app, promoter, make_user):
        make_user(email="other@example.com", is_promoter=True)
        row = labels.create_label("L", "promo@example.com")
        assert labels.reassign_label(row.id, "other@example.com").promoter == (
            "other@example.com"
        )

    def test_delete_detaches_events(self, app, promoter, make_event):
        row = labels.create_label("Gone", "promo@example.com")
        event = make_event()
        event.label_id = row.id
        db.session.commit()
        name, n_events = labels.delete_label(row.id)
        assert (name, n_events) == ("Gone", 1)
        assert db.session.get(Label, row.id) is None
        assert db.session.get(Event, event.id).label_id is None

    def test_delete_missing_raises(self, app):
        with pytest.raises(AdminError):
            labels.delete_label(424242)


class TestCliContract:
    """The scripted surface documented in README/MOTD must keep parsing."""

    @pytest.fixture(scope="class")
    def parser(self):
        import db_admin_cli

        return db_admin_cli.build_parser()

    @pytest.mark.parametrize(
        "argv",
        [
            ["logs", "-f"],
            ["logs", "-e", "-n", "100"],
            ["user", "list"],
            ["user", "add", "x@y.z", "--lang", "fr", "--no-email"],
            ["user", "add", "x@y.z"],
            ["user", "passwd", "x@y.z", "--password", "pw"],
            ["user", "admin", "x@y.z", "--grant"],
            ["user", "promoter", "x@y.z", "--revoke"],
            ["user", "invite", "x@y.z", "--lang", "gsw"],
            ["user", "delete", "x@y.z", "--yes"],
            ["db", "backup", "--dest", "/tmp/x.sqlite3"],
            ["db", "vacuum"],
        ],
    )
    def test_documented_invocations_parse(self, parser, argv):
        args = parser.parse_args(argv)
        assert callable(args.func)

    def test_add_defaults_to_gsw(self, parser):
        args = parser.parse_args(["user", "add", "x@y.z"])
        assert args.lang == "gsw"

    def test_invalid_lang_rejected(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["user", "add", "x@y.z", "--lang", "de"])


def test_tui_imports():
    from diytracker.admin.tui.app import ManageApp  # noqa: F401


class TestSearchFilters:
    """Every list screen filters through the same term-ANDing dialect as the
    site search — including the wildcard escaping, so a query of "%" filters
    rather than matching everything."""

    def test_events_search_spans_name_venue_and_submitter(
        self, app, make_event, make_venue, make_user
    ):
        user = make_user(email="promo@example.com")
        fabrik = make_venue(name="Rote Fabrik", city="Zürich")
        make_event(name="Noise Night", venue=fabrik, acts="Kälte, Blume")
        make_event(name="Quiet Night", submitter=user)

        assert [e.name for e in admin_events.list_events(search="fabrik")] == [
            "Noise Night"
        ]
        assert [e.name for e in admin_events.list_events(search="kälte")] == [
            "Noise Night"
        ]
        assert [e.name for e in admin_events.list_events(search="promo@")] == [
            "Quiet Night"
        ]

    def test_events_search_ands_terms(self, app, make_event, make_venue):
        fabrik = make_venue(name="Rote Fabrik", city="Zürich")
        bern = make_venue(name="Reitschule", city="Bern")
        make_event(name="Noise Night", venue=fabrik)
        make_event(name="Noise Night", venue=bern)
        rows = admin_events.list_events(search="noise bern")
        assert [e.venue for e in rows] == ["Reitschule"]

    def test_events_search_escapes_wildcards(self, app, make_event):
        make_event(name="Noise Night")
        assert admin_events.list_events(search="%") == []

    def test_users_search(self, app, make_user):
        make_user(email="alice@example.com")
        make_user(email="bob@other.test")
        assert [u.email for u in users.list_users("other")] == ["bob@other.test"]

    def test_labels_search_covers_slug_and_owner(self, app, make_user):
        promoter = make_user(email="owner@example.com", is_promoter=True)
        labels.create_label("Kaputt Records", promoter.email)
        labels.create_label("Sunny Tapes", promoter.email)
        assert [r.name for r in labels.list_labels("kaputt")] == ["Kaputt Records"]
        assert [r.name for r in labels.list_labels("sunny-tapes")] == ["Sunny Tapes"]
        assert len(labels.list_labels("owner@example.com")) == 2

    def test_audit_search_combines_with_the_action_filter(self, app):
        from diytracker.admin import audit
        from diytracker.services.audit import record

        record("queue.approve", "event", 1, actor="a@example.com", detail="Punk Fest")
        record("queue.reject", "scraped", 2, actor="b@example.com", detail="spam")
        db.session.commit()

        assert [r.action for r in audit.list_actions(search="punk")] == [
            "queue.approve"
        ]
        assert [r.action for r in audit.list_actions(search="b@example.com")] == [
            "queue.reject"
        ]
        assert audit.list_actions(action="queue.reject", search="punk") == []
