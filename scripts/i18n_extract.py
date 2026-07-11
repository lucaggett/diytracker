#!/usr/bin/env python
"""Extract translatable strings into translations/messages.pot.

Wraps pybabel so that underscore-prefixed directories (e.g. templates/_partials)
are scanned. Babel's default directory filter skips them.
"""

import os
import sys

from babel.messages.catalog import Catalog
from babel.messages.extract import DEFAULT_KEYWORDS, extract_from_dir
from babel.messages.frontend import parse_mapping_cfg
from babel.messages.pofile import write_po

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def keep_dir(path: str) -> bool:
    base = os.path.basename(path)
    if base.startswith("."):
        return False
    if base in (
        "__pycache__",
        "node_modules",
        "venv",
        "instance",
        "static",
        "migrations",
        "logs",
        "uploads",
    ):
        return False
    return True


def build_source_catalog() -> Catalog:
    """Extract every translatable string from the source tree.

    Shared with scripts/i18n_status.py so extraction and the coverage check
    can never disagree on what counts as a source string.
    """
    with open(os.path.join(ROOT, "babel.cfg")) as f:
        method_map, options_map = parse_mapping_cfg(f)
    keywords = dict(DEFAULT_KEYWORDS)
    keywords["_l"] = None
    catalog = Catalog(project="diytracker", version="0.1", charset="utf-8")
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


def main() -> int:
    catalog = build_source_catalog()
    out = os.path.join(ROOT, "translations", "messages.pot")
    with open(out, "wb") as f:
        write_po(f, catalog)
    print(f"wrote {out} with {len(catalog)} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
