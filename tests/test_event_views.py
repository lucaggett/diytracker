"""Tests for the per-event view count service (services/event_views.py)."""

from datetime import date, datetime, timedelta

from diytracker.models import EventDailyViews
from diytracker.services import event_views

UA = "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"


def _line(path="/events/12/", ip="1.2.3.4", day=None, status=200, method="GET", ua=UA):
    day = day or date(2026, 7, 10)
    stamp = day.strftime("%d/%b/%Y") + ":12:00:00 +0000"
    return (
        f'{ip} - - [{stamp}] "{method} {path} HTTP/1.1" {status} 1234 '
        f'"https://diytracker.ch/" "{ua}"'
    )


class TestParseLine:
    def test_counts_valid_event_page_hit(self):
        parsed = event_views._parse_line(_line())
        assert parsed == (12, date(2026, 7, 10), "1.2.3.4")

    def test_ignores_bot_user_agents(self):
        assert event_views._parse_line(_line(ua="Googlebot/2.1")) is None
        assert event_views._parse_line(_line(ua="python-requests/2.31")) is None
        assert event_views._parse_line(_line(ua="")) is None
        assert event_views._parse_line(_line(ua="-")) is None

    def test_ignores_non_200_and_non_get(self):
        assert event_views._parse_line(_line(status=404)) is None
        assert event_views._parse_line(_line(status=302)) is None
        assert event_views._parse_line(_line(method="POST")) is None

    def test_ignores_other_paths_and_honeypot(self):
        assert event_views._parse_line(_line(path="/")) is None
        assert event_views._parse_line(_line(path="/venues/3/")) is None
        # The scrape-detection honeypot lives at /events/archive/ — it must
        # never be counted as an event view.
        assert event_views._parse_line(_line(path="/events/archive/")) is None
        # Missing trailing slash (nginx logs the redirect separately).
        assert event_views._parse_line(_line(path="/events/12", status=308)) is None


class TestCollect:
    def test_counts_hits_and_distinct_ips_per_event_day(self, tmp_path):
        day1, day2 = date(2026, 7, 9), date(2026, 7, 10)
        log = tmp_path / "access.log"
        log.write_text(
            "\n".join(
                [
                    _line(ip="1.1.1.1", day=day1),
                    _line(ip="1.1.1.1", day=day1),
                    _line(ip="2.2.2.2", day=day1),
                    _line(ip="1.1.1.1", day=day2),
                    _line(path="/events/99/", ip="3.3.3.3", day=day2),
                    _line(ip="9.9.9.9", day=day1, ua="AhrefsBot/7.0"),
                ]
            )
            + "\n"
        )
        counts = event_views.collect_event_day_counts([log], valid_dates=None)
        assert counts == {
            (12, day1): (3, 2),
            (12, day2): (1, 1),
            (99, day2): (1, 1),
        }

    def test_respects_valid_dates_window(self, tmp_path):
        inside, outside = date(2026, 7, 10), date(2026, 1, 1)
        log = tmp_path / "access.log"
        log.write_text(_line(day=inside) + "\n" + _line(day=outside) + "\n")
        valid = {inside.strftime("%d/%b/%Y")}
        counts = event_views.collect_event_day_counts([log], valid_dates=valid)
        assert counts == {(12, inside): (1, 1)}


class TestPersist:
    def test_inserts_new_rows(self, app):
        day = date(2026, 7, 10)
        event_views.persist_event_day_counts({(12, day): (5, 3)})
        row = EventDailyViews.query.one()
        assert (row.event_id, row.date, row.hits, row.visitors) == (12, day, 5, 3)

    def test_rerun_is_idempotent_and_max_wins(self, app):
        day = date(2026, 7, 10)
        event_views.persist_event_day_counts({(12, day): (5, 3)})
        # Same counts again: unchanged, no duplicate row.
        event_views.persist_event_day_counts({(12, day): (5, 3)})
        # Lower counts (e.g. a rotated-away log file): MAX keeps stored values.
        event_views.persist_event_day_counts({(12, day): (2, 1)})
        row = EventDailyViews.query.one()
        assert (row.hits, row.visitors) == (5, 3)
        # Higher counts (today's partial day grew): updated.
        event_views.persist_event_day_counts({(12, day): (8, 4)})
        row = EventDailyViews.query.one()
        assert (row.hits, row.visitors) == (8, 4)

    def test_rows_outside_later_windows_survive(self, app):
        old_day = date(2026, 5, 1)
        event_views.persist_event_day_counts({(12, old_day): (7, 2)})
        # A later run only sees recent days — the old row must stay intact.
        event_views.persist_event_day_counts({(12, date(2026, 7, 10)): (1, 1)})
        old_row = EventDailyViews.query.filter_by(date=old_day).one()
        assert (old_row.hits, old_row.visitors) == (7, 2)
        assert EventDailyViews.query.count() == 2


class TestGenerateEventViewStats:
    def test_fails_without_logs(self, app, monkeypatch):
        monkeypatch.setattr(event_views, "find_log_files", lambda: [])
        success, message = event_views.generate_event_view_stats()
        assert success is False
        assert "No nginx access log files found" in message

    def test_parses_window_and_persists(self, app, tmp_path, monkeypatch):
        today = datetime.now().date()
        old = today - timedelta(days=200)  # outside the 60-day window
        log = tmp_path / "access.log"
        log.write_text(_line(day=today) + "\n" + _line(day=old) + "\n")
        monkeypatch.setattr(event_views, "find_log_files", lambda: [log])
        success, message = event_views.generate_event_view_stats()
        assert success is True
        row = EventDailyViews.query.one()
        assert (row.event_id, row.date, row.hits) == (12, today, 1)

    def test_no_views_is_success_without_rows(self, app, tmp_path, monkeypatch):
        log = tmp_path / "access.log"
        log.write_text(_line(path="/", day=datetime.now().date()) + "\n")
        monkeypatch.setattr(event_views, "find_log_files", lambda: [log])
        success, message = event_views.generate_event_view_stats()
        assert success is True
        assert EventDailyViews.query.count() == 0


class TestEventPopularity:
    def test_ranks_by_total_hits_and_joins_events(self, app, make_event):
        popular = make_event(name="Popular Show")
        quiet = make_event(name="Quiet Show")
        event_views.persist_event_day_counts(
            {
                (popular.id, date(2026, 7, 9)): (10, 4),
                (popular.id, date(2026, 7, 10)): (5, 2),
                (quiet.id, date(2026, 7, 9)): (3, 3),
            }
        )
        ranking = event_views.event_popularity()
        assert [row["event_id"] for row in ranking] == [popular.id, quiet.id]
        assert ranking[0]["hits"] == 15
        assert ranking[0]["visitors"] == 6
        assert ranking[0]["event"].name == "Popular Show"

    def test_tolerates_deleted_event_ids(self, app):
        event_views.persist_event_day_counts({(424242, date(2026, 7, 9)): (9, 9)})
        ranking = event_views.event_popularity()
        assert ranking[0]["event_id"] == 424242
        assert ranking[0]["event"] is None

    def test_respects_limit(self, app):
        counts = {(i, date(2026, 7, 9)): (i, 1) for i in range(1, 6)}
        event_views.persist_event_day_counts(counts)
        assert len(event_views.event_popularity(limit=2)) == 2
        assert event_views.event_popularity(limit=2)[0]["event_id"] == 5
