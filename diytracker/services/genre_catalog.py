"""The genre catalog: the Genre table backing the event form's genre picker.

Replaces the old hardcoded list in forms.get_genre_choices(). SEED_GENRES
preserves those original entries; seed_genres() inserts whichever are
missing, so fresh and pre-existing databases converge on the same baseline.
Seed rows carry added_by_id NULL, marking them as curated — the management
page only lets their creator (or an admin) delete user-added rows.
"""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from diytracker.models import Genre, db

SEED_GENRES = [
    "Hardcore",
    "Punk",
    "Metal",
    "Post-punk",
    "EBM",
    "Industrial",
    "Synthpop",
    "Darkwave",
    "Goth",
    "New Wave",
    "Alternative",
    "Indie",
    "Rock",
    "Pop",
    "Hip Hop",
    "Reggae",
    "Dub",
    "Dancehall",
    "Drum & Bass",
    "Dubstep",
    "Techno",
    "House",
    "Trance",
    "Electro",
    "Ambient",
    "Experimental",
    "Noise",
    "Wave",
    "NDW",
    "Folk",
    "Neofolk",
    "Jazz",
    "Blues",
    "Ska",
    "Garage",
    "Hyperpop",
    "Emo",
    "Metalcore",
    "Synth",
    "Beatdown",
    "Doom",
    "Sludge",
    "Stoner",
    "Crustpunk",
    "Screamo",
    "Powerviolence",
    "Mathcore",
    "Shoegaze",
    "Gabber",
    "Goregrind",
    "Psycore",
]


def seed_genres():
    """Insert any missing seed genres (case-insensitive). Idempotent; runs at
    every app startup after create_all(), which is also how existing
    databases get their initial rows — no separate migration needed."""
    existing = {name.lower() for (name,) in db.session.query(Genre.name).all()}
    missing = [name for name in SEED_GENRES if name.lower() not in existing]
    if not missing:
        return
    for name in missing:
        db.session.add(Genre(name=name))
    try:
        db.session.commit()
    except IntegrityError:
        # Another gunicorn worker seeded concurrently; theirs won.
        db.session.rollback()


def all_genre_names():
    """All catalog genre names, sorted case-insensitively."""
    return sorted((g.name for g in Genre.query.all()), key=str.lower)


def find_genre(name):
    """Case-insensitive lookup; returns the Genre row or None."""
    return Genre.query.filter(func.lower(Genre.name) == (name or "").lower()).first()
