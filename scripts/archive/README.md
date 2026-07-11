# Archived one-off scripts

Backfill scripts that have already been run against production and are kept
only for reference. Nothing imports them; delete freely once they stop being
useful as documentation.

- `geocode_venues.py` / `geocode_venues_pass2.py` — Nominatim backfill of
  `venue.coords` (July 2026). Pass 2 imports pass 1, so they must stay
  together.
