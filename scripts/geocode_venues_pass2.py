"""Second geocoding pass for venues the first pass missed.

Retries Nominatim with looser heuristics: wider country set (adds AT/DE),
simplified venue names (parts of " - " compounds), and cleaned city names
(postal district suffixes and bilingual "A / B" forms stripped).

Usage: python3 scripts/geocode_venues_pass2.py [path/to/events.db]
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from geocode_venues import nominatim  # noqa: E402

COUNTRIES = "ch,li,fr,at,de"


def city_variants(city):
    city = city.strip()
    out = [city]
    out.append(re.sub(r"\s+\d+$", "", city))  # "Genève 8" -> "Genève"
    out.extend(p.strip() for p in re.split(r"[/-]", city) if p.strip())
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def name_variants(name):
    name = name.strip()
    out = [name]
    out.extend(p.strip() for p in name.split(" - ") if p.strip())
    seen, uniq = set(), []
    for n in out:
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("events.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    venues = conn.execute(
        "SELECT id, name, address, city, canton, plz FROM venue"
        " WHERE coords IS NULL OR coords = '' ORDER BY id"
    ).fetchall()

    still_unresolved = []
    for venue in venues:
        name = (venue["name"] or "").strip()
        city = (venue["city"] or "").strip()
        hit, used = None, None
        queries = []
        for n in name_variants(name):
            for c in city_variants(city):
                queries.append(f"{n}, {c}")
        if venue["address"] and city:
            for c in city_variants(city):
                queries.append(f"{venue['address']}, {c}")
        for query in queries:
            hit = nominatim(query, countrycodes=COUNTRIES)
            if hit:
                used = query
                break
        if hit:
            coords = f"{hit[0]:.5f},{hit[1]:.5f}"
            conn.execute(
                "UPDATE venue SET coords = ? WHERE id = ?", (coords, venue["id"])
            )
            conn.commit()
            print(f"[ok]   #{venue['id']:>3} {name}: {coords}  via nominatim ({used})")
        else:
            still_unresolved.append(dict(venue))
            print(f"[FAIL] #{venue['id']:>3} {name} ({city})")

    conn.close()
    print(f"\nStill unresolved: {len(still_unresolved)}")


if __name__ == "__main__":
    main()
