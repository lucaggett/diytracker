"""Backfill venue.coords in an events.db copy.

Tries the Swiss federal geocoder (api3.geo.admin.ch) for venues with a street
address, then falls back to OSM Nominatim by venue name + city (covers venues
without an address, plus non-Swiss ones like Annemasse/FR and Vaduz/LI).

Coordinates are stored as "lat,lon" (WGS84, 5 decimals) in venue.coords.
Venues that cannot be resolved are printed at the end and written to a JSON
report next to the database.

Usage: uv run python scripts/geocode_venues.py [path/to/events.db]
"""

import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

GEOADMIN_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "diytracker-geocoder/1.0 (lucaggett@gmail.com)"


def http_json(url, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"       lookup error for {params!r}: {exc}")
        return None


def geoadmin_address(query):
    data = http_json(
        GEOADMIN_URL,
        {"searchText": query, "type": "locations", "origins": "address", "limit": 1},
    )
    results = (data or {}).get("results", [])
    if not results:
        return None
    attrs = results[0]["attrs"]
    return attrs["lat"], attrs["lon"]


def nominatim(query, countrycodes="ch,li,fr"):
    data = http_json(
        NOMINATIM_URL,
        {"q": query, "format": "json", "limit": 1, "countrycodes": countrycodes},
    )
    time.sleep(1.1)  # Nominatim usage policy: max 1 request/second
    if not data:
        return None
    if isinstance(data, dict):  # error payload
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def normalize(venue):
    """Return (address, city, plz), fixing rows where city and plz are swapped."""
    address = (venue["address"] or "").strip()
    city = (venue["city"] or "").strip()
    plz = (venue["plz"] or "").strip()
    if city.isdigit() and plz and not plz.isdigit():
        city, plz = plz, city
    return address, city, plz


def geocode(venue):
    """Return (coords, source) or (None, attempted-queries)."""
    name = (venue["name"] or "").strip()
    address, city, plz = normalize(venue)
    attempts = []

    if address and city:
        query = " ".join(p for p in (address, plz, city) if p)
        attempts.append(f"geoadmin: {query}")
        hit = geoadmin_address(query)
        if hit:
            return hit, f"geoadmin address ({query})"

    for query in filter(None, (
        f"{name}, {city}" if name and city else None,
        f"{address}, {plz} {city}".strip(", ") if address and city else None,
    )):
        attempts.append(f"nominatim: {query}")
        hit = nominatim(query)
        if hit:
            return hit, f"nominatim ({query})"

    return None, attempts


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("events.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    venues = conn.execute(
        "SELECT id, name, address, city, canton, plz FROM venue"
        " WHERE coords IS NULL OR coords = '' ORDER BY id"
    ).fetchall()

    resolved, unresolved = [], []
    for venue in venues:
        result, info = geocode(venue)
        if result:
            lat, lon = result
            coords = f"{lat:.5f},{lon:.5f}"
            conn.execute("UPDATE venue SET coords = ? WHERE id = ?", (coords, venue["id"]))
            conn.commit()
            resolved.append({"id": venue["id"], "name": venue["name"], "coords": coords, "source": info})
            print(f"[ok]   #{venue['id']:>3} {venue['name']}: {coords}  via {info}")
        else:
            unresolved.append({**dict(venue), "attempts": info})
            print(f"[FAIL] #{venue['id']:>3} {venue['name']} ({venue['city']})")

    conn.close()
    report = db_path.with_name("geocode_report.json")
    report.write_text(json.dumps({"resolved": resolved, "unresolved": unresolved}, indent=2, ensure_ascii=False))
    print(f"\n{len(resolved)} resolved, {len(unresolved)} unresolved. Report: {report}")


if __name__ == "__main__":
    main()
