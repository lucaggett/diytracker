"""One-off script: convert flyer uploads to WebP and update DB references.

Walks static/uploads/, converts every JPEG/PNG to WebP, then updates any
Event.flyer rows that pointed at the original file to point at the new
.webp path. Animated GIFs and existing .webp files are skipped.

Usage::

    python scripts/convert_uploads_to_webp.py              # apply
    python scripts/convert_uploads_to_webp.py --dry-run    # preview only
    python scripts/convert_uploads_to_webp.py --keep       # keep originals
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from app import app, db
from models import Event

UPLOAD_FOLDER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "uploads"
)
SUPPORTED = {".jpg", ".jpeg", ".png"}
WEBP_QUALITY = 85

dry_run = "--dry-run" in sys.argv
keep_originals = "--keep" in sys.argv


def _unique_webp_path(folder, stem):
    """Pick a .webp filename in `folder` that doesn't already exist."""
    candidate = f"{stem}.webp"
    if not os.path.exists(os.path.join(folder, candidate)):
        return candidate
    n = 1
    while True:
        candidate = f"{stem}_{n}.webp"
        if not os.path.exists(os.path.join(folder, candidate)):
            return candidate
        n += 1


# old_filename → new_filename (basenames within UPLOAD_FOLDER)
renames = {}
processed = converted = skipped = errors = 0

for filename in sorted(os.listdir(UPLOAD_FOLDER)):
    src_path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.isfile(src_path):
        continue

    ext = os.path.splitext(filename)[1].lower()
    if ext == ".webp":
        skipped += 1
        continue
    if ext not in SUPPORTED:
        skipped += 1
        continue

    processed += 1
    stem = os.path.splitext(filename)[0]

    try:
        with Image.open(src_path) as img:
            if getattr(img, "is_animated", False):
                print(f"  skip (animated)  {filename}")
                skipped += 1
                continue

            new_filename = _unique_webp_path(UPLOAD_FOLDER, stem)
            dst_path = os.path.join(UPLOAD_FOLDER, new_filename)
            before_kb = os.path.getsize(src_path) // 1024

            if dry_run:
                print(f"  would convert  {filename} → {new_filename}  ({before_kb} KB)")
            else:
                save_img = img
                if img.mode in ("P", "LA"):
                    save_img = img.convert("RGBA")
                elif img.mode == "CMYK":
                    save_img = img.convert("RGB")
                save_img.save(dst_path, format="WEBP", quality=WEBP_QUALITY, method=6)
                after_kb = os.path.getsize(dst_path) // 1024
                print(
                    f"  converted  {filename} → {new_filename}  ({before_kb} KB → {after_kb} KB)"
                )

            renames[filename] = new_filename
            converted += 1

    except Exception as exc:
        print(f"  ERROR   {filename}: {exc}")
        errors += 1

# --- Update DB references ---
db_updates = 0
missing_on_disk = 0

with app.app_context():
    events = Event.query.filter(Event.flyer.isnot(None)).filter(Event.flyer != "").all()
    for ev in events:
        old_path = ev.flyer
        old_basename = os.path.basename(old_path)

        if old_basename in renames:
            new_path = old_path[: -len(old_basename)] + renames[old_basename]
            print(f"  DB Event #{ev.id}: flyer {old_path!r} → {new_path!r}")
            if not dry_run:
                ev.flyer = new_path
            db_updates += 1
        else:
            # Reference exists in DB but no matching file was converted — flag it
            # only if the file isn't already .webp and isn't on disk.
            ext = os.path.splitext(old_basename)[1].lower()
            if ext != ".webp":
                full = os.path.join(UPLOAD_FOLDER, old_basename)
                if not os.path.exists(full):
                    missing_on_disk += 1
                    print(f"  WARN  Event #{ev.id} references missing file: {old_path}")

    if not dry_run:
        db.session.commit()

# --- Delete originals (only after DB has been updated) ---
deleted = 0
if not dry_run and not keep_originals:
    for old_basename in renames:
        old_path = os.path.join(UPLOAD_FOLDER, old_basename)
        try:
            os.remove(old_path)
            deleted += 1
        except OSError as exc:
            print(f"  ERROR removing {old_basename}: {exc}")

print()
prefix = "Dry run — " if dry_run else ""
tail = ""
if not dry_run:
    if keep_originals:
        tail = ", originals kept"
    else:
        tail = f", {deleted} originals deleted"
print(
    f"{prefix}Done: {processed} images checked, {converted} {'would be ' if dry_run else ''}converted, "
    f"{skipped} skipped, {errors} errors. DB: {db_updates} flyer reference(s) {'would be ' if dry_run else ''}updated"
    f"{tail}."
)
if missing_on_disk:
    print(
        f"  {missing_on_disk} flyer reference(s) point at files not present on disk (left untouched)."
    )
