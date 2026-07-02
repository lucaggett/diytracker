"""Single source of truth for repository-anchored paths.

Everything that reads or writes inside the repo checkout goes through these
constants so runtime behaviour never depends on the process CWD.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTANCE_DIR = ROOT / "instance"
CACHE_DIR = INSTANCE_DIR / "cache"
LOGS_DIR = ROOT / "logs"
STATIC_DIR = ROOT / "static"
UPLOADS_DIR = STATIC_DIR / "uploads"
TEMPLATES_DIR = ROOT / "templates"
TRANSLATIONS_DIR = ROOT / "translations"


def ensure_runtime_dirs():
    for d in (LOGS_DIR, UPLOADS_DIR, INSTANCE_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
