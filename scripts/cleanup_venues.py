"""One-off script: deduplicate Venue rows whose names match.

Groups venues by a whitespace-collapsed, case-folded name. Within each group
the most-complete row (fewest NULLs across address/canton/coords, tie-break
lowest id) is kept; events referencing the duplicates are reassigned via FK
update, then the duplicates are deleted. Blank fields on the keeper are
filled in from the duplicates where available; differing non-empty values
are reported as CONFLICTs but never overwritten.

Usage::

    python scripts/cleanup_venues.py            # dry-run preview
    python scripts/cleanup_venues.py --apply    # write changes

Preview-by-default is intentional: the name-only match deliberately merges
same-named venues in different cities, so the city/canton/plz printed for
every row let you veto cross-city merges before running --apply.

Before running with --apply, back up ``instance/events.db`` — the script
commits a single transaction at the end and does not track what it deleted.
"""
import os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Event, Venue

apply_changes = '--apply' in sys.argv

_FILL_FIELDS = ('address', 'canton', 'coords')
_CONFLICT_FIELDS = ('address', 'city', 'canton', 'plz', 'coords')


def _norm_name(name):
    return ' '.join((name or '').split()).casefold()


def _is_blank(val):
    return val is None or (isinstance(val, str) and not val.strip())


def _completeness(v):
    return sum(0 if _is_blank(getattr(v, f)) else 1 for f in _FILL_FIELDS)


def _describe(v):
    return (
        f"#{v.id}  {v.name!r}  {v.city}/{v.canton or '-'}/{v.plz}  "
        f"addr={v.address!r}  coords={v.coords!r}"
    )


def main():
    groups_merged = 0
    venues_deleted = 0
    events_reassigned = 0
    fields_filled = 0

    with app.app_context():
        all_venues = Venue.query.all()
        buckets = defaultdict(list)
        for v in all_venues:
            buckets[_norm_name(v.name)].append(v)

        dup_groups = [(k, vs) for k, vs in buckets.items() if len(vs) >= 2]
        dup_groups.sort(key=lambda kv: kv[0])

        if not dup_groups:
            print("No duplicate venue names found.")
            return

        for key, venues in dup_groups:
            venues.sort(key=lambda v: (-_completeness(v), v.id))
            keeper = venues[0]
            dups = venues[1:]

            print(f"\n=== Group: {key!r} ({len(venues)} venues) ===")
            print(f"  KEEP  {_describe(keeper)}")
            for d in dups:
                print(f"  DUP   {_describe(d)}")

            for field in _CONFLICT_FIELDS:
                keeper_val = getattr(keeper, field)
                if _is_blank(keeper_val):
                    continue
                for d in dups:
                    dup_val = getattr(d, field)
                    if not _is_blank(dup_val) and dup_val != keeper_val:
                        print(f"  CONFLICT {field}: keeper={keeper_val!r}  dup#{d.id}={dup_val!r}")

            for field in _FILL_FIELDS:
                if not _is_blank(getattr(keeper, field)):
                    continue
                for d in sorted(dups, key=lambda x: x.id):
                    dup_val = getattr(d, field)
                    if not _is_blank(dup_val):
                        print(f"  fill keeper.{field} ← #{d.id} ({dup_val!r})")
                        if apply_changes:
                            setattr(keeper, field, dup_val)
                        fields_filled += 1
                        break

            dup_ids = [d.id for d in dups]
            event_count = Event.query.filter(Event.venue_id.in_(dup_ids)).count()
            print(f"  reassign {event_count} event(s) to #{keeper.id}")
            if apply_changes and event_count:
                Event.query.filter(Event.venue_id.in_(dup_ids)).update(
                    {'venue_id': keeper.id}, synchronize_session=False
                )
            events_reassigned += event_count

            if apply_changes:
                remaining = Event.query.filter(Event.venue_id.in_(dup_ids)).count()
                if remaining:
                    db.session.rollback()
                    print(
                        f"ABORT: {remaining} event(s) still reference dup venues "
                        f"{dup_ids} after reassignment. No changes committed."
                    )
                    return
                for d in dups:
                    db.session.delete(d)

            groups_merged += 1
            venues_deleted += len(dups)

        if apply_changes:
            db.session.commit()
            print(
                f"\nApplied. {groups_merged} group(s) merged, {venues_deleted} venue(s) deleted, "
                f"{events_reassigned} event(s) reassigned, {fields_filled} field(s) filled."
            )
        else:
            print(
                f"\nDry run. {groups_merged} duplicate group(s), {venues_deleted} venue(s) would be deleted, "
                f"{events_reassigned} event(s) would be reassigned, {fields_filled} field(s) would be filled."
            )
            print("Re-run with --apply to write.")


if __name__ == '__main__':
    main()
