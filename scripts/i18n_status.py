#!/usr/bin/env python
"""Report translation coverage for every locale and fail on gaps.

Extracts a fresh catalog from the source tree (same rules as
scripts/i18n_extract.py), then checks:
  - translations/messages.pot is in sync with the source
  - every locale .po has an entry for every source string
  - no entry is untranslated (empty msgstr) or fuzzy

Obsolete entries (in a .po but no longer in the source) are reported but do
not fail the check. Writes a Markdown summary to $GITHUB_STEP_SUMMARY when
set. Exits 1 on any stale, missing, untranslated, or fuzzy string.
"""

import os
import sys

from babel.messages.catalog import Catalog
from babel.messages.extract import DEFAULT_KEYWORDS, extract_from_dir
from babel.messages.frontend import parse_mapping_cfg
from babel.messages.pofile import read_po

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n_extract import ROOT, keep_dir  # noqa: E402

MAX_LISTED = 20


def build_source_catalog() -> Catalog:
    with open(os.path.join(ROOT, "babel.cfg")) as f:
        method_map, options_map = parse_mapping_cfg(f)
    keywords = dict(DEFAULT_KEYWORDS)
    keywords["_l"] = None
    catalog = Catalog(project="diytracker", charset="utf-8")
    for filename, lineno, message, comments, context in extract_from_dir(
        ROOT,
        method_map=method_map,
        options_map=options_map,
        keywords=keywords,
        directory_filter=keep_dir,
    ):
        rel = os.path.relpath(filename, ROOT).replace(os.sep, "/")
        catalog.add(
            message, None, [(rel, lineno)], auto_comments=comments, context=context
        )
    return catalog


def message_ids(catalog) -> set:
    return {m.id for m in catalog if m.id}


def check_locale(locale: str, source_ids: set) -> dict:
    po_path = os.path.join(ROOT, "translations", locale, "LC_MESSAGES", "messages.po")
    with open(po_path, "rb") as f:
        po = read_po(f, locale=locale)
    po_ids = message_ids(po)
    untranslated, fuzzy = [], []
    for msg in po:
        if not msg.id or msg.id not in source_ids:
            continue
        if msg.fuzzy:
            fuzzy.append(msg.id)
        elif not msg.string:
            untranslated.append(msg.id)
    return {
        "locale": locale,
        "missing": sorted(source_ids - po_ids),
        "obsolete": sorted(po_ids - source_ids),
        "untranslated": sorted(untranslated),
        "fuzzy": sorted(fuzzy),
    }


def listing(title: str, ids: list) -> list:
    lines = [f"  {title} ({len(ids)}):"]
    for msgid in ids[:MAX_LISTED]:
        text = msgid if isinstance(msgid, str) else msgid[0]
        lines.append(f"    - {text}")
    if len(ids) > MAX_LISTED:
        lines.append(f"    ... and {len(ids) - MAX_LISTED} more")
    return lines


def write_github_summary(total: int, results: list, pot_ok: bool) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## i18n status",
        "",
        f"{total} translatable strings in source. "
        f"messages.pot is {'in sync' if pot_ok else '**stale** — run scripts/i18n_extract.py'}.",
        "",
        "| Locale | Coverage | Translated | Untranslated | Fuzzy | Missing | Obsolete |",
        "|--------|----------|------------|--------------|-------|---------|----------|",
    ]
    for r in results:
        translated = (
            total - len(r["missing"]) - len(r["untranslated"]) - len(r["fuzzy"])
        )
        pct = 100.0 * translated / total if total else 100.0
        ok = not (r["missing"] or r["untranslated"] or r["fuzzy"])
        lines.append(
            f"| {'✅' if ok else '❌'} {r['locale']} | {pct:.1f}% | {translated} "
            f"| {len(r['untranslated'])} | {len(r['fuzzy'])} "
            f"| {len(r['missing'])} | {len(r['obsolete'])} |"
        )
    for r in results:
        problems = [
            (name, r[key])
            for name, key in (
                ("Missing", "missing"),
                ("Untranslated", "untranslated"),
                ("Fuzzy", "fuzzy"),
            )
            if r[key]
        ]
        if not problems:
            continue
        lines += [
            "",
            f"<details><summary>{r['locale']}: strings needing work</summary>",
            "",
        ]
        for name, ids in problems:
            lines.append(f"**{name}** ({len(ids)})")
            lines += [
                f"- `{i if isinstance(i, str) else i[0]}`" for i in ids[:MAX_LISTED]
            ]
            if len(ids) > MAX_LISTED:
                lines.append(f"- ... and {len(ids) - MAX_LISTED} more")
            lines.append("")
        lines.append("</details>")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    source = build_source_catalog()
    source_ids = message_ids(source)
    total = len(source_ids)
    print(f"{total} translatable strings in source\n")

    pot_path = os.path.join(ROOT, "translations", "messages.pot")
    with open(pot_path, "rb") as f:
        pot_ids = message_ids(read_po(f))
    pot_ok = pot_ids == source_ids
    if not pot_ok:
        print("STALE: translations/messages.pot does not match the source tree.")
        stale_missing = sorted(source_ids - pot_ids)
        stale_extra = sorted(pot_ids - source_ids)
        if stale_missing:
            print("\n".join(listing("not in .pot", stale_missing)))
        if stale_extra:
            print("\n".join(listing("in .pot but not in source", stale_extra)))
        print("Run: uv run python scripts/i18n_extract.py\n")

    locales = sorted(
        d
        for d in os.listdir(os.path.join(ROOT, "translations"))
        if os.path.isdir(os.path.join(ROOT, "translations", d))
    )
    results = [check_locale(loc, source_ids) for loc in locales]

    print(
        f"{'locale':8} {'coverage':>8} {'transl.':>8} {'untransl.':>9} {'fuzzy':>6} {'missing':>8} {'obsolete':>9}"
    )
    failed = not pot_ok
    for r in results:
        translated = (
            total - len(r["missing"]) - len(r["untranslated"]) - len(r["fuzzy"])
        )
        pct = 100.0 * translated / total if total else 100.0
        print(
            f"{r['locale']:8} {pct:7.1f}% {translated:8} {len(r['untranslated']):9} "
            f"{len(r['fuzzy']):6} {len(r['missing']):8} {len(r['obsolete']):9}"
        )
        if r["missing"] or r["untranslated"] or r["fuzzy"]:
            failed = True

    for r in results:
        problems = [
            (name, r[key])
            for name, key in (
                ("missing", "missing"),
                ("untranslated", "untranslated"),
                ("fuzzy", "fuzzy"),
            )
            if r[key]
        ]
        if problems:
            print(f"\n{r['locale']}:")
            for name, ids in problems:
                print("\n".join(listing(name, ids)))

    write_github_summary(total, results, pot_ok)

    if failed:
        print("\nFAIL: incomplete translations (see above).")
        return 1
    print("\nOK: all locales fully translated and catalogs in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
