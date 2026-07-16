"""Genre-string normalization. Event.genre is a free-text, comma-joined
string rather than a separate table (see models.py), so the same genre can
end up stored under slightly different spellings (e.g. "Post-punk" vs
"post punk" vs "Post-Punk"). This groups those spellings by a
case/whitespace-insensitive key and rewrites events onto one canonical
spelling per group."""

from collections import defaultdict

from diytracker.forms import get_genre_choices
from diytracker.models import db


def _split_tokens(raw):
    if not raw:
        return []
    return [t.strip() for t in raw.replace("·", ",").split(",") if t.strip()]


def _canonical_key(token):
    return " ".join(token.split()).lower()


def find_genre_dedup_groups(events):
    """Group genre tokens in use across `events` by case/whitespace-
    insensitive key. Returns {key: {spelling: count}}, restricted to keys
    with more than one distinct spelling, sorted by key."""
    groups = defaultdict(lambda: defaultdict(int))
    for event in events:
        for token in _split_tokens(event.genre):
            groups[_canonical_key(token)][token] += 1
    return {
        key: dict(spellings)
        for key, spellings in sorted(groups.items())
        if len(spellings) > 1
    }


_SEED_SPELLINGS = {name.lower(): name for name, _label in get_genre_choices()}


def pick_canonical(spellings):
    """Pick the canonical spelling for a group of {spelling: count}: prefer
    the seed genre-list spelling if one of the variants matches it exactly,
    else the most-used spelling, tie-broken alphabetically."""
    for spelling in spellings:
        if _SEED_SPELLINGS.get(spelling.lower()) == spelling:
            return spelling
    return sorted(spellings.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def merge_genre_tokens(events, mapping):
    """Rewrite each event's genre string, replacing tokens per `mapping`
    ({spelling: canonical}) and de-duplicating within the event (keeping
    first occurrence order). Flushes but does not commit. Returns the number
    of events changed."""
    changed = 0
    for event in events:
        tokens = _split_tokens(event.genre)
        if not tokens:
            continue
        new_tokens = []
        seen = set()
        for token in tokens:
            canonical = mapping.get(token, token)
            key = canonical.lower()
            if key in seen:
                continue
            seen.add(key)
            new_tokens.append(canonical)
        new_genre = ", ".join(new_tokens)
        if new_genre != (event.genre or ""):
            event.genre = new_genre
            changed += 1
    db.session.flush()
    return changed
