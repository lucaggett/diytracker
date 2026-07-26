"""Tests for the ops logic (diytracker/admin/ops.py): cache purge, scrape
status, manual scrape trigger. No scrape is ever actually run here — the
import path is exercised, the network is not."""

from datetime import datetime, timedelta

import pytest

from diytracker.admin import ops
from diytracker.admin.core import AdminError
from diytracker.models import ScrapedEvent, SkippedUrl, db
from diytracker.services import scraper


class TestPurgeCache:
    def test_counts_and_clears(self, app, tmp_path, monkeypatch):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        for name in ("a", "b"):
            (cache_dir / name).write_text("cached")
        monkeypatch.setattr(ops, "CACHE_DIR", cache_dir)
        cleared = []
        monkeypatch.setattr(ops, "bust_cache", lambda: cleared.append(True))

        with app.app_context():
            result = ops.purge_cache()
        assert result.files_removed == 2
        assert cleared == [True]

    def test_missing_cache_dir_is_not_an_error(self, app, tmp_path, monkeypatch):
        monkeypatch.setattr(ops, "CACHE_DIR", tmp_path / "gone")
        monkeypatch.setattr(ops, "bust_cache", lambda: None)
        with app.app_context():
            assert ops.purge_cache().files_removed == 0


class TestScrapeStatus:
    def test_never_scraped(self, app, monkeypatch):
        monkeypatch.setattr(ops, "get_last_scrape_time", lambda: None)
        with app.app_context():
            status = ops.scrape_status()
        assert status.last_scrape == "never"
        assert status.next_due == "on next start"

    def test_reports_the_schedule_in_wall_clock_time(self, app, monkeypatch):
        last = datetime.now() - timedelta(hours=1)
        monkeypatch.setattr(ops, "get_last_scrape_time", lambda: last)
        with app.app_context():
            status = ops.scrape_status()
        assert status.last_ago.endswith("ago")
        assert status.next_due.startswith("in ")

    def test_overdue_run_says_so(self, app, monkeypatch):
        last = datetime.now() - timedelta(hours=scraper.SCRAPE_INTERVAL_HOURS + 1)
        monkeypatch.setattr(ops, "get_last_scrape_time", lambda: last)
        with app.app_context():
            assert ops.scrape_status().next_due == "due now"

    def test_queue_counts_come_from_the_status_column(self, app, monkeypatch):
        monkeypatch.setattr(ops, "get_last_scrape_time", lambda: None)
        with app.app_context():
            db.session.add_all(
                [
                    ScrapedEvent(title="pending", status=ScrapedEvent.STATUS_PENDING),
                    ScrapedEvent(
                        title="flagged",
                        status=ScrapedEvent.STATUS_PENDING,
                        needs_review=True,
                    ),
                    ScrapedEvent(
                        title="published", status=ScrapedEvent.STATUS_PUBLISHED
                    ),
                    ScrapedEvent(title="rejected", status=ScrapedEvent.STATUS_REJECTED),
                ]
            )
            db.session.add(SkippedUrl(url="https://example.com/x", reason="not a gig"))
            db.session.commit()
            status = ops.scrape_status()
        assert status.pending_queue == 2
        assert status.flagged_queue == 1
        assert status.published == 1
        assert status.rejected == 1
        assert status.recent_skipped[0][0] == "https://example.com/x"


class TestRunScrape:
    def test_refuses_while_one_is_running(self, app, monkeypatch):
        monkeypatch.setattr(scraper, "_scrape_running", True)
        with pytest.raises(AdminError, match="already running"):
            ops.run_scrape(app)

    def test_reports_the_counts(self, app, monkeypatch):
        monkeypatch.setattr(
            ops,
            "run_scrape_now",
            lambda flask_app: (True, {"created": 3, "duplicate": 1, "invalid": 0}),
        )
        run = ops.run_scrape(app)
        assert (run.created, run.duplicate, run.skipped) == (3, 1, 0)

    def test_failed_run_raises(self, app, monkeypatch):
        monkeypatch.setattr(ops, "run_scrape_now", lambda flask_app: (True, None))
        with pytest.raises(AdminError, match="scrape failed"):
            ops.run_scrape(app)

    def test_lock_is_released_after_a_failed_run(self, app, monkeypatch):
        """A crashing scrape must not leave the process permanently 'busy'."""

        def boom(flask_app):
            raise RuntimeError("network down")

        monkeypatch.setattr(scraper, "_scrape_and_import", boom)
        with pytest.raises(RuntimeError):
            scraper.run_scrape_now(app)
        assert scraper.is_running() is False
