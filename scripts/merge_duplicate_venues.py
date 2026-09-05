"""Merge venues that are the same place stored twice, keeping the older row.

Approving a queue entry used to look up the venue by the exact
``(name, city, plz)`` tuple, so a scraped row with no postal code — which is
most of what petzi and eventbot send — could never match the venue already in
the database and minted a second one beside it. That is fixed in
``services.venue.resolve_existing_venue``; this repairs the rows it already
produced, and stays around for the next time something slips through.

Only the unambiguous groups are merged unasked: same normalized name (casing,
diacritics and stray whitespace folded) with compatible cities, which is what
``find_dedup_candidates`` calls an auto-group. Everything it merely *suspects* —
similar names, one name containing another, a shared street address — needs a
human, because Provitreff, Boschbar and Planet5 all sit at Sihlquai 240 in
Zürich and are three different venues. Those are printed by default, and
``--apply --interactive`` walks them one at a time with the facts that decide
the answer (address, canton, event count, whether accessibility answers would
be lost) and takes yes / no / quit.

**The survivor is always the lowest id**, i.e. the oldest row, which is the one
public links and external references already point at. That deliberately
overrides ``select_survivor``'s completeness-first ranking; where the two
disagree the difference is printed, and ``merge_group`` backfills any field the
survivor is missing from the rows being deleted, so nothing is lost either way.

    uv run python scripts/merge_duplicate_venues.py                  # dry run
    uv run python scripts/merge_duplicate_venues.py --apply
    uv run python scripts/merge_duplicate_venues.py --apply --interactive
    uv run python scripts/merge_duplicate_venues.py --db-path /tmp/copy.db

Idempotent: a second run finds nothing left to merge automatically, and re-asks
only the suspects still standing. Take a database backup before --apply, it
deletes rows.
"""

import argparse
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from diytracker.app import create_app
from diytracker.config import Config
from diytracker.models import Event, Venue, VenueAccessibility, db
from diytracker.paths import ROOT
from diytracker.services.cache import bust_cache
from diytracker.services.venue import (
    find_dedup_candidates,
    merge_group,
    normalize_name,
    select_survivor,
)


@dataclass
class MergeResult:
    groups: int = 0
    venues_deleted: int = 0
    events_repointed: int = 0
    lines: list = field(default_factory=list)  # printable report, in order
    suspects: list = field(default_factory=list)  # (venue_a, venue_b, reason) labels
    declined: list = field(default_factory=list)  # suspects answered "no", same shape
    chained: int = 0  # suspects skipped because their middle row was merged away
    stopped_early: bool = False  # the reviewer quit part-way
    event_collisions: list = field(default_factory=list)  # post-merge leftovers


def _label(venue):
    return (
        f"#{venue.id} {venue.name.strip()!r} / {(venue.city or '').strip()} / "
        f"{(venue.plz or '').strip() or '(no plz)'}"
    )


def _event_counts():
    return dict(
        db.session.query(Event.venue_id, db.func.count(Event.id)).group_by(
            Event.venue_id
        )
    )


def _find_event_collisions(venue_remap):
    """Events that land on the same venue, day and name once the merge is done.

    ``merge_group`` repoints ``Event.venue_id`` without recomputing
    ``event_hash`` (which includes the venue), so two rows that were duplicates
    across the two venues end up at one venue with hashes that don't collide.
    Reported only — deleting events belongs to the admin tool's event dedup.
    """
    seen, collisions = {}, []
    for event in Event.query.order_by(Event.id).all():
        venue_id = venue_remap.get(event.venue_id, event.venue_id)
        key = (venue_id, event.date.date(), normalize_name(event.name))
        if key in seen:
            collisions.append((seen[key], event.id, event.name, event.date.date()))
        else:
            seen[key] = event.id
    return collisions


def merge_duplicate_venues(dry_run=True, confirm=None):
    """Fold every auto-mergeable venue group into its oldest member.

    ``confirm`` opts into the suspect pairs — the containment / similar-name /
    same-address candidates that are never merged unasked. It is called as
    ``confirm(survivor, loser, reason)`` for each and returns True to merge,
    False to skip, or None to stop asking and keep what has been done so far.
    Left at None, suspects are only reported. Ignored on a dry run: prompting
    and then writing nothing would answer the same question twice.
    """
    result = MergeResult()
    auto_groups, pairs = find_dedup_candidates(Venue.query.all())
    counts = _event_counts()

    venue_remap = {}
    gone = set()
    for group in auto_groups:
        ordered = sorted(group, key=lambda v: v.id)
        survivor, losers = ordered[0], ordered[1:]
        result.groups += 1
        result.venues_deleted += len(losers)
        result.lines.append(f"keep {_label(survivor)}")

        complete_pick, _ = select_survivor(group, counts)
        if complete_pick.id != survivor.id:
            result.lines.append(
                f"     note: most complete row is #{complete_pick.id}, "
                f"keeping the older #{survivor.id} — missing fields are backfilled"
            )
        for loser in losers:
            venue_remap[loser.id] = survivor.id
            gone.add(loser.id)
            result.lines.append(
                f"     merge {_label(loser)} ({counts.get(loser.id, 0)} events)"
            )

        if dry_run:
            result.events_repointed += sum(counts.get(v.id, 0) for v in losers)
            continue
        stats = merge_group(survivor, losers)
        result.events_repointed += stats["events_repointed"]
        for field_name, _value, source_id in stats["backfilled"]:
            result.lines.append(f"     backfilled {field_name} from #{source_id}")
        for note in stats["accessibility"] + stats["warnings"]:
            result.lines.append(f"     {note}")

    # A suspect pair whose members the automatic phase just merged away is
    # moot, so the dry run predicts the same list --apply will print.
    pairs = [(a, b, r) for a, b, r in pairs if a.id not in gone and b.id not in gone]

    answered = set()
    if confirm is not None and not dry_run:
        for a, b, reason in pairs:
            survivor, loser = sorted((a, b), key=lambda v: v.id)
            if survivor.id in gone or loser.id in gone:
                # Chained pair (A/B and B/C) whose middle row is already gone.
                # Re-running picks the remainder up against the survivor.
                result.chained += 1
                continue
            answer = confirm(survivor, loser, reason)
            if answer is None:
                result.stopped_early = True
                break
            answered.add(frozenset((survivor.id, loser.id)))
            if not answer:
                result.declined.append((_label(survivor), _label(loser), reason))
                continue
            result.groups += 1
            result.venues_deleted += 1
            venue_remap[loser.id] = survivor.id
            gone.add(loser.id)
            result.lines.append(f"keep {_label(survivor)}")
            result.lines.append(f"     merge {_label(loser)} — confirmed ({reason})")
            stats = merge_group(survivor, [loser])
            result.events_repointed += stats["events_repointed"]
            for field_name, _value, source_id in stats["backfilled"]:
                result.lines.append(f"     backfilled {field_name} from #{source_id}")
            for note in stats["accessibility"] + stats["warnings"]:
                result.lines.append(f"     {note}")

    result.event_collisions = _find_event_collisions(venue_remap)
    # What is left for a human: still standing, and not already answered.
    result.suspects = [
        (_label(a), _label(b), reason)
        for a, b, reason in pairs
        if a.id not in gone
        and b.id not in gone
        and frozenset((a.id, b.id)) not in answered
    ]

    if dry_run:
        db.session.rollback()
    else:
        db.session.commit()
    return result


def _venue_detail(venue):
    """The facts that decide a merge, gathered per prompt so the counts are
    current even after earlier answers moved events around.
    """
    events = Event.query.filter_by(venue_id=venue.id).count()
    accessibility = (
        VenueAccessibility.query.filter_by(venue_id=venue.id).first() is not None
    )
    return (
        f"  {_label(venue)}\n"
        f"      address: {(venue.address or '').strip() or '—'}\n"
        f"      canton: {(venue.canton or '').strip() or '—'}"
        f"   events: {events}"
        f"   accessibility answers: {'yes' if accessibility else 'no'}"
    )


def _ask_terminal(survivor, loser, reason):
    """Prompt for one suspect pair. True merges, False skips, None quits."""
    print(f"\n  ── {reason} ──")
    print(_venue_detail(survivor) + "   <- would be kept (older)")
    print(_venue_detail(loser) + "   <- would be deleted")
    if VenueAccessibility.query.filter_by(venue_id=loser.id).first() is not None:
        keeps = VenueAccessibility.query.filter_by(venue_id=survivor.id).first()
        if keeps is not None:
            print("      warning: the deleted row's accessibility answers are lost")
    while True:
        answer = input("  merge these? [y]es / [n]o / [q]uit: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no", ""):
            return False
        if answer in ("q", "quit"):
            return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--apply", action="store_true", help="actually merge (default: dry run)"
    )
    ap.add_argument(
        "--db-path", default=None, help="run against a copy instead of instance/"
    )
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="also review the suspect pairs one by one (requires --apply)",
    )
    args = ap.parse_args()

    if args.interactive and not args.apply:
        ap.error("--interactive writes as you answer, so it needs --apply")
    if args.interactive and not sys.stdin.isatty():
        ap.error("--interactive needs a terminal to ask in")

    if args.db_path:
        load_dotenv(ROOT / ".env")
        config = Config.from_env()
        config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.abspath(args.db_path)}"
        app = create_app(config)
    else:
        app = create_app()

    with app.app_context():
        before = Venue.query.count()
        result = merge_duplicate_venues(
            dry_run=not args.apply,
            confirm=_ask_terminal if args.interactive else None,
        )
        for line in result.lines:
            print(line)
        if args.apply:
            bust_cache()

    if result.declined:
        print(f"\nLeft alone, answered no ({len(result.declined)}):")
        for a, b, reason in result.declined:
            print(f"  {a}\n  {b}\n     ({reason})\n")

    if result.suspects:
        if args.interactive:
            header = "\nStill unreviewed — you quit before these:"
        else:
            header = (
                "\nNot merged — these need a human. Re-run with --apply "
                "--interactive to review them here:"
            )
        print(header)
        for a, b, reason in result.suspects:
            print(f"  {a}\n  {b}\n     ({reason})\n")

    if result.chained:
        print(
            f"{result.chained} suspect pair(s) skipped: a row they name was "
            "merged away earlier in this run. Re-run to see the remainder."
        )

    if result.event_collisions:
        print("\nEvents that share a venue, day and name after merging:")
        for kept_id, dup_id, name, day in result.event_collisions:
            print(f"  #{kept_id} and #{dup_id}: {name!r} on {day}")
        print("  (reported only — merge or delete these in the admin tool)")

    verb = "would merge" if not args.apply else "merged"
    print(
        f"\n{verb} {result.groups} groups: {result.venues_deleted} venues deleted, "
        f"{result.events_repointed} events repointed, "
        f"{before} venues -> {before - result.venues_deleted}."
    )


if __name__ == "__main__":
    main()
