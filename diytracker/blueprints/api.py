import hmac
import json

from flask import Blueprint, current_app, jsonify, request

from diytracker.models import Event, Venue
from diytracker.services.cache import cache
from diytracker.services.genre_catalog import all_genre_names
from diytracker.services.ingest import ingest_event
from diytracker.services.limits import limiter

bp = Blueprint("api", __name__)


@bp.route("/get_genres")
@cache.cached()
def get_genres():
    catalog = all_genre_names()
    db_genres = []
    for event in (
        Event.query.with_entities(Event.genre).filter(Event.genre != None).all()  # noqa: E711 (SQLAlchemy needs `!= None` for IS NOT NULL, not `is not None`)
    ):
        for raw_genre in (event.genre or "").split(","):
            genre = raw_genre.strip()
            if genre:
                db_genres.append(genre)
    combined = sorted(set(catalog + db_genres), key=str.lower)
    return jsonify({"genres": combined})


@bp.route("/get_venues")
@cache.cached()
def get_venues():
    venues = Venue.query.order_by(Venue.name.asc()).all()
    venue_list = [
        {
            "id": venue.id,
            "name": venue.name,
            "address": venue.address,
            "city": venue.city,
            "plz": venue.plz,
            "canton": venue.canton,
            "coords": venue.coords or "N/A",
        }
        for venue in venues
    ]
    return jsonify({"venues": venue_list})


@bp.route("/api/ingest", methods=["POST"])
@limiter.limit("120 per hour")
def ingest():
    """Push one event into the staging queue (ScrapedEvent).

    The external ingest contract: any source (the Signal eventbot, future
    bots, other machines' scrapers) POSTs here instead of touching the DB.
    Auth is a shared token: `Authorization: Bearer <INGEST_TOKEN>`.

    Two body shapes:
      * application/json — the payload documented in services/ingest.py
      * multipart/form-data — field 'event' (that JSON as a string) plus
        an optional 'flyer' image file (png/jpg/gif, validated + resized)

    Responses: 201 created, 200 duplicate (safe to mark as delivered),
    422 invalid payload, 401 bad token, 503 ingest not configured.
    CSRF-exempt (token-authenticated, no session) — see app.py.
    """
    expected = current_app.config.get("INGEST_TOKEN")
    if not expected:
        return jsonify({"error": "ingest disabled: INGEST_TOKEN not configured"}), 503
    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
    if not hmac.compare_digest(token, expected):
        return jsonify({"error": "unauthorized"}), 401

    flyer = None
    if request.content_type and request.content_type.startswith("multipart/"):
        try:
            payload = json.loads(request.form.get("event") or "")
        except json.JSONDecodeError:
            return jsonify(
                {"error": "missing or malformed 'event' JSON form field"}
            ), 400
        flyer = request.files.get("flyer")
    else:
        payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object payload"}), 400

    result = ingest_event(
        payload, flyer=flyer, upload_folder=current_app.config.get("UPLOAD_FOLDER")
    )
    if result.status == "created":
        return jsonify({"status": "created", "id": result.record.id}), 201
    if result.status == "duplicate":
        return jsonify({"status": "duplicate", "reason": result.reason}), 200
    return jsonify({"status": "invalid", "error": result.reason}), 422
