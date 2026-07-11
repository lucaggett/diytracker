import re
from difflib import SequenceMatcher

from diytracker.models import db, Event, Venue, VenueAccessibility, utcnow


def get_or_create_venue(name, address, city, canton, plz, coords=""):
    """Find venue by (name, city, plz) or create it. Returns (venue, created).
    Does not commit — caller is responsible for the transaction."""
    venue = Venue.query.filter_by(name=name, city=city, plz=plz).first()
    if venue:
        return venue, False
    venue = Venue(
        name=name,
        address=address,
        city=city,
        canton=canton,
        plz=plz,
        coords=coords or "",
    )
    db.session.add(venue)
    db.session.flush()
    return venue, True


# ── deduplication ─────────────────────────────────────────────────────────────

_LEADING_PLZ = re.compile(r"^\d{4}\s+")

# Same-city venues with different names only become merge candidates when one
# name contains the other (and the shorter isn't trivially short) or the names
# are this similar — and even then they need manual confirmation.
MIN_CONTAINMENT_LENGTH = 4
FUZZY_RATIO_THRESHOLD = 0.80

_BACKFILL_FIELDS = ("address", "city", "plz", "canton", "coords")


def normalize_name(name):
    """Lowercase, trim, and collapse internal whitespace."""
    return " ".join((name or "").split()).lower()


def normalize_city(city):
    """Like normalize_name, but also strips a leading 4-digit PLZ ("8005 Zürich")."""
    return " ".join(_LEADING_PLZ.sub("", (city or "").strip()).split()).lower()


def _filled(value):
    return bool((value or "").strip())


def completeness_score(venue):
    """How many optional fields are filled — used to pick merge survivors."""
    return sum(
        _filled(value)
        for value in (
            venue.address,
            venue.plz,
            venue.coords,
            venue.canton,
            venue.accessibility_token,
        )
    )


def find_dedup_candidates(venues):
    """Split duplicate venues into auto-mergeable groups and pairs for review.

    Returns (auto_groups, interactive_pairs):
      * auto_groups: lists of venues sharing a normalized name whose cities
        are compatible (equal after normalization, or empty) — safe to merge.
      * interactive_pairs: (venue_a, venue_b, reason) tuples for same-name
        venues in different cities (typo or genuinely distinct?) and same-city
        venues with similar names.
    """
    auto_groups = []
    pairs = []

    by_name = {}
    for venue in venues:
        by_name.setdefault(normalize_name(venue.name), []).append(venue)

    for group in by_name.values():
        if len(group) < 2:
            continue
        by_city = {}
        for venue in group:
            by_city.setdefault(normalize_city(venue.city), []).append(venue)
        if len([city for city in by_city if city]) <= 1:
            auto_groups.append(sorted(group, key=lambda v: v.id))
            continue
        # Same name across several cities: same-city subsets still merge
        # automatically, but anything across city lines needs a human.
        buckets = sorted(by_city.items())
        for city, bucket in buckets:
            if city and len(bucket) > 1:
                auto_groups.append(sorted(bucket, key=lambda v: v.id))
        for i, (_city_a, bucket_a) in enumerate(buckets):
            for _city_b, bucket_b in buckets[i + 1 :]:
                for a in bucket_a:
                    for b in bucket_b:
                        pairs.append((a, b, "same name, city differs"))

    by_city = {}
    for venue in venues:
        city = normalize_city(venue.city)
        if city:
            by_city.setdefault(city, []).append(venue)
    for bucket in by_city.values():
        bucket = sorted(bucket, key=lambda v: v.id)
        for i, a in enumerate(bucket):
            name_a = normalize_name(a.name)
            for b in bucket[i + 1 :]:
                name_b = normalize_name(b.name)
                if name_a == name_b:
                    continue  # handled by the exact-name pass above
                shorter, longer = sorted((name_a, name_b), key=len)
                if len(shorter) >= MIN_CONTAINMENT_LENGTH and shorter in longer:
                    pairs.append((a, b, "one name contains the other"))
                elif (
                    SequenceMatcher(None, name_a, name_b).ratio()
                    >= FUZZY_RATIO_THRESHOLD
                ):
                    pairs.append((a, b, "similar names"))

    return auto_groups, pairs


def select_survivor(group, event_counts):
    """Pick the venue to keep: most filled fields, then most events, then oldest."""
    ranked = sorted(
        group,
        key=lambda v: (-completeness_score(v), -event_counts.get(v.id, 0), v.id),
    )
    return ranked[0], ranked[1:]


def planned_backfills(survivor, losers):
    """The (field, value, source_venue_id) backfills merge_group would apply."""
    backfills = []
    for field in _BACKFILL_FIELDS:
        if _filled(getattr(survivor, field)):
            continue
        for loser in losers:
            value = getattr(loser, field)
            if _filled(value):
                backfills.append((field, value, loser.id))
                break
    return backfills


def merge_group(survivor, losers):
    """Fold `losers` into `survivor`: backfill empty fields, repoint events and
    accessibility data, then delete the losers. Flushes but does not commit.
    Returns a stats dict describing what happened."""
    stats = {
        "backfilled": planned_backfills(survivor, losers),
        "accessibility": [],
        "warnings": [],
        "events_repointed": 0,
        "venues_deleted": len(losers),
    }
    loser_ids = [loser.id for loser in losers]

    for field, value, _source_id in stats["backfilled"]:
        setattr(survivor, field, value)

    survivor_acc = VenueAccessibility.query.filter_by(venue_id=survivor.id).first()
    loser_accs = (
        VenueAccessibility.query.filter(VenueAccessibility.venue_id.in_(loser_ids))
        .order_by(VenueAccessibility.updated_at.desc())
        .all()
    )
    if loser_accs:
        keep = None
        if survivor_acc is None:
            keep = loser_accs[0]
            stats["accessibility"].append(
                f"moved accessibility data from venue #{keep.venue_id}"
            )
            keep.venue_id = survivor.id
        for acc in loser_accs:
            if acc is not keep:
                stats["warnings"].append(
                    f"dropped accessibility data of venue #{acc.venue_id}"
                )
                db.session.delete(acc)
        db.session.flush()

    if not _filled(survivor.accessibility_token):
        donor = next(
            (loser for loser in losers if _filled(loser.accessibility_token)), None
        )
        if donor:
            token = donor.accessibility_token
            donor.accessibility_token = None
            # Flush so the UNIQUE(accessibility_token) constraint never sees
            # the token on two rows at once (update order follows primary key).
            db.session.flush()
            survivor.accessibility_token = token
            stats["accessibility"].append(f"moved accessibility token from #{donor.id}")
    for loser in losers:
        if _filled(loser.accessibility_token):
            stats["warnings"].append(
                f"discarded accessibility token of venue #{loser.id} "
                "(survivor keeps its own; the old link stops working)"
            )

    # Bulk UPDATE bypasses the ORM onupdate, so bump updated_at by hand —
    # the sitemap <lastmod> must reflect the venue change on these events.
    stats["events_repointed"] = Event.query.filter(
        Event.venue_id.in_(loser_ids)
    ).update(
        {"venue_id": survivor.id, "updated_at": utcnow()},
        synchronize_session=False,
    )
    # The survivor's page changes even without backfills (it absorbs events).
    survivor.updated_at = utcnow()

    for loser in losers:
        db.session.delete(loser)
    db.session.flush()
    return stats
