"""Unified ingest core (services/ingest.py) and the /api/ingest push endpoint."""

import io
import json
import os
from datetime import date, datetime, timedelta

import pytest
from PIL import Image

from models import db, Event, ScrapedEvent
from services.ingest import ingest_event, parse_date, parse_time


def _future(days=14):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _payload(**overrides):
    base = {
        "source": "eventbot",
        "source_id": "abc123def456",
        "title": "Grind Night",
        "performers": "Archagathus, Skunk",
        "styles": "grindcore, crust",
        "start_date": _future(),
        "doors_open": "19:00",
        "start_time": "20:30",
        "venue_name": "Ebrietas",
        "city": "Zürich",
        "submitter": "Jonathan Schenker",
    }
    base.update(overrides)
    return base


def _png_bytes(size=(4, 5)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "PNG")
    return buf.getvalue()


class TestIngestCore:
    def test_creates_normalised_record(self, app):
        result = ingest_event(_payload())
        assert result.status == "created"
        rec = ScrapedEvent.query.one()
        assert rec.source == "eventbot"
        assert rec.source_id == "abc123def456"
        assert rec.title == "Grind Night"
        assert rec.region == "ZH"  # canton inferred from city
        assert rec.doors_open.strftime("%H:%M") == "19:00"
        assert rec.start_time.strftime("%H:%M") == "20:30"
        assert rec.submitter == "Jonathan Schenker"
        assert rec.approved is False  # lands in the queue, not on the site

    @pytest.mark.parametrize("missing", ["source", "title", "start_date"])
    def test_required_fields(self, app, missing):
        result = ingest_event(_payload(**{missing: None}))
        assert result.status == "invalid"
        assert missing in result.reason
        assert ScrapedEvent.query.count() == 0

    def test_malformed_date_is_invalid(self, app):
        assert ingest_event(_payload(start_date="next friday")).status == "invalid"

    def test_duplicate_by_source_id(self, app):
        assert ingest_event(_payload()).status == "created"
        result = ingest_event(_payload(title="Same event, richer parse"))
        assert result.status == "duplicate"
        assert ScrapedEvent.query.count() == 1

    def test_same_source_id_different_source_ok(self, app):
        assert ingest_event(_payload()).status == "created"
        assert ingest_event(_payload(source="petzi")).status == "created"

    def test_duplicate_by_url(self, app):
        url = "https://example.com/events/1"
        assert ingest_event(_payload(url=url, source_id=None)).status == "created"
        assert ingest_event(_payload(url=url, source_id=None)).status == "duplicate"

    def test_url_known_from_approved_event(self, app, make_event):
        ev = make_event()
        ev.source_url = "https://example.com/events/2"
        db.session.commit()
        result = ingest_event(_payload(url=ev.source_url, source_id=None))
        assert result.status == "duplicate"

    def test_in_batch_dedup_without_commit(self, app):
        assert ingest_event(_payload(), commit=False).status == "created"
        assert ingest_event(_payload(), commit=False).status == "duplicate"
        db.session.commit()
        assert ScrapedEvent.query.count() == 1

    def test_flyer_bytes_stored(self, app):
        result = ingest_event(
            _payload(),
            flyer=(_png_bytes(), "flyer.png"),
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        assert result.status == "created"
        assert result.record.flyer
        assert os.path.exists(result.record.flyer)

    def test_non_image_flyer_rejected(self, app):
        result = ingest_event(
            _payload(),
            flyer=(b"#!/bin/sh\nrm -rf /", "evil.png"),
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        assert result.status == "invalid"
        assert "flyer" in result.reason
        assert ScrapedEvent.query.count() == 0

    def test_oversized_fields_trimmed(self, app):
        result = ingest_event(_payload(title="x" * 500, ticket_price="y" * 200))
        assert result.status == "created"
        assert len(result.record.title) == 200
        assert len(result.record.ticket_price) == 50

    def test_parse_helpers_accept_native_types(self, app):
        assert parse_date(date(2026, 7, 4)) == date(2026, 7, 4)
        assert parse_date("2026-07-04") == date(2026, 7, 4)
        assert parse_date("") is None
        assert parse_time("19:30").strftime("%H:%M") == "19:30"
        assert parse_time(None) is None


@pytest.fixture
def ingest_token(app):
    app.config["INGEST_TOKEN"] = "test-ingest-token"
    yield "test-ingest-token"
    app.config["INGEST_TOKEN"] = None


def _auth(token="test-ingest-token"):
    return {"Authorization": f"Bearer {token}"}


class TestIngestApi:
    def test_disabled_without_token_config(self, client, app):
        app.config["INGEST_TOKEN"] = None
        resp = client.post("/api/ingest", json=_payload())
        assert resp.status_code == 503

    def test_wrong_token_rejected(self, client, ingest_token):
        resp = client.post("/api/ingest", json=_payload(), headers=_auth("nope"))
        assert resp.status_code == 401
        assert ScrapedEvent.query.count() == 0

    def test_missing_auth_header_rejected(self, client, ingest_token):
        assert client.post("/api/ingest", json=_payload()).status_code == 401

    def test_json_push_creates(self, client, ingest_token):
        resp = client.post("/api/ingest", json=_payload(), headers=_auth())
        assert resp.status_code == 201
        assert resp.get_json()["status"] == "created"
        assert ScrapedEvent.query.one().source_id == "abc123def456"

    def test_duplicate_push_returns_200(self, client, ingest_token):
        assert (
            client.post("/api/ingest", json=_payload(), headers=_auth()).status_code
            == 201
        )
        resp = client.post("/api/ingest", json=_payload(), headers=_auth())
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "duplicate"

    def test_invalid_payload_returns_422(self, client, ingest_token):
        resp = client.post(
            "/api/ingest", json=_payload(start_date=None), headers=_auth()
        )
        assert resp.status_code == 422

    def test_non_object_body_returns_400(self, client, ingest_token):
        resp = client.post(
            "/api/ingest",
            data="not json",
            headers=_auth(),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_multipart_push_with_flyer(self, client, app, ingest_token):
        resp = client.post(
            "/api/ingest",
            headers=_auth(),
            data={
                "event": json.dumps(_payload()),
                "flyer": (io.BytesIO(_png_bytes()), "flyer.jpg"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201
        rec = ScrapedEvent.query.one()
        assert rec.flyer and os.path.exists(rec.flyer)
        assert rec.flyer.startswith(app.config["UPLOAD_FOLDER"])

    def test_multipart_without_event_field_returns_400(self, client, ingest_token):
        resp = client.post(
            "/api/ingest", headers=_auth(), data={}, content_type="multipart/form-data"
        )
        assert resp.status_code == 400


class TestFlyerApproval:
    def test_approving_carries_flyer_onto_event(self, client, app, make_user, login):
        result = ingest_event(
            _payload(),
            flyer=(_png_bytes(), "flyer.png"),
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        rec = result.record
        login(make_user())
        resp = client.post("/queue", data={"scraped_id": str(rec.id)})
        assert resp.status_code == 302
        ev = Event.query.one()
        assert ev.flyer == rec.flyer
        assert rec.approved and rec.approved_event_id == ev.id

    def test_queue_shows_flyer_and_submitter(self, client, app, make_user, login):
        ingest_event(
            _payload(),
            flyer=(_png_bytes(), "flyer.png"),
            upload_folder=app.config["UPLOAD_FOLDER"],
        )
        login(make_user())
        resp = client.get("/queue")
        page = resp.data.decode()
        assert "Jonathan Schenker" in page
        assert ScrapedEvent.query.one().flyer in page


class TestForwarder:
    """scripts/eventbot_forwarder.py — the piece that runs on the eventbot box."""

    def test_multipart_encoding_accepted_by_endpoint(self, client, app, ingest_token):
        import scripts.eventbot_forwarder as fwd

        body, content_type = fwd.encode_multipart(_payload(), _png_bytes(), "flyer.jpg")
        resp = client.post(
            "/api/ingest", data=body, content_type=content_type, headers=_auth()
        )
        assert resp.status_code == 201
        rec = ScrapedEvent.query.one()
        assert rec.flyer and os.path.exists(rec.flyer)

    def test_multipart_encoding_without_flyer(self, client, ingest_token):
        import scripts.eventbot_forwarder as fwd

        body, content_type = fwd.encode_multipart(_payload(), None, None)
        resp = client.post(
            "/api/ingest", data=body, content_type=content_type, headers=_auth()
        )
        assert resp.status_code == 201
        assert ScrapedEvent.query.one().flyer is None

    @pytest.fixture
    def store(self, tmp_path):
        recs = [
            {
                "id": "good00000001",
                "submitter": "Jona",
                "event": {"title": "Show A", "date": _future(), "artists": []},
            },
            {
                "id": "bad000000002",
                "submitter": "Jona",
                "event": {"title": "Dateless", "date": "", "artists": []},
            },
            {
                "id": "good00000003",
                "submitter": "Jona",
                "event": {"title": "Show B", "date": _future(), "artists": []},
            },
        ]
        (tmp_path / "events.jsonl").write_text(
            "\n".join(json.dumps(r) for r in recs), encoding="utf-8"
        )
        return tmp_path

    def _run(self, monkeypatch, store, deliver):
        import scripts.eventbot_forwarder as fwd

        calls = []

        def _deliver(url, token, payload, flyer_bytes, flyer_name, timeout=30):
            calls.append(payload["source_id"])
            return deliver(payload)

        monkeypatch.setattr(fwd, "deliver", _deliver)
        monkeypatch.setenv("INGEST_TOKEN", "tok")
        monkeypatch.setattr(
            "sys.argv", ["eventbot_forwarder.py", "--store", str(store)]
        )
        return fwd, calls

    def test_sends_once_and_marks_invalid_permanently(self, monkeypatch, store):
        def responses(payload):
            if payload["source_id"] == "bad000000002":
                return 422, {"error": "missing start_date"}
            return 201, {"status": "created"}

        fwd, calls = self._run(monkeypatch, store, responses)
        fwd.main()
        assert calls == ["good00000001", "bad000000002", "good00000003"]
        state = json.loads((store / fwd.STATE_FILENAME).read_text())
        assert state["good00000001"] == "created"
        assert state["bad000000002"].startswith("invalid")

        calls.clear()
        fwd.main()  # second run: everything already forwarded
        assert calls == []

    def test_server_error_aborts_and_preserves_retry(self, monkeypatch, store):
        fwd, calls = self._run(monkeypatch, store, lambda p: (500, {}))
        with pytest.raises(SystemExit):
            fwd.main()
        assert calls == ["good00000001"]  # aborted on first failure
        state = json.loads((store / fwd.STATE_FILENAME).read_text())
        assert state == {}  # nothing marked; all retry next run


class TestEventbotMapping:
    def test_record_maps_to_payload(self, app):
        import scripts.import_eventbot as imp

        rec = {
            "source": "signal:abc123group",
            "submitter": "Maxine Muster",
            "id": "e229ea0c5de4",
            "flyer_image": "/home/user/eventbot/events/flyers/x.jpg",
            "event": {
                "title": "1-a-Grind",
                "artists": ["Archagathus", "Skunk"],
                "venue": "Quai du Bas 30",
                "city": "Biel/Bienne",
                "address": "Quai du Bas 30, Biel/Bienne",
                "date": _future(),
                "start_time": "19:30",
                "doors_time": "19:00",
                "price": "",
                "ticket_url": "",
                "genre": "grind, crust",
                "age_restriction": "18+",
                "description": "Grind night.",
                "links": [],
            },
        }
        payload = imp.eventbot_to_payload(rec)
        assert payload["source"] == "eventbot"
        assert payload["source_id"] == "e229ea0c5de4"
        assert payload["performers"] == "Archagathus, Skunk"
        assert payload["doors_open"] == "19:00"
        assert "Age restriction: 18+" in payload["description"]
        # and the payload round-trips through the core
        assert ingest_event(payload).status == "created"
