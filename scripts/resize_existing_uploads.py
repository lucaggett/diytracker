"""One-off script: resize existing flyer uploads that exceed 800px on either side.

Walks static/uploads/, skips GIFs, and rewrites any JPEG/PNG that is larger
than 800px. Reports what it changed (or would change in --dry-run mode).

Usage::

    python scripts/resize_existing_uploads.py           # apply
    python scripts/resize_existing_uploads.py --dry-run # preview only
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'uploads')
MAX_SIDE = 800
SUPPORTED = {'.jpg', '.jpeg', '.png'}

dry_run = '--dry-run' in sys.argv

processed = skipped = resized = errors = 0

for filename in sorted(os.listdir(UPLOAD_FOLDER)):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED:
        skipped += 1
        continue

    path = os.path.join(UPLOAD_FOLDER, filename)
    processed += 1

    try:
        with Image.open(path) as img:
            w, h = img.width, img.height
            if w <= MAX_SIDE and h <= MAX_SIDE:
                print(f'  ok      {filename} ({w}x{h})')
                continue

            fmt = img.format
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            new_w, new_h = img.width, img.height

            if dry_run:
                print(f'  would resize  {filename}  {w}x{h} → {new_w}x{new_h}')
            else:
                save_kw = {'format': fmt, 'optimize': True}
                if fmt == 'JPEG':
                    save_kw.update({'quality': 85, 'progressive': True})
                img.save(path, **save_kw)

                before_kb = os.path.getsize(path) // 1024
                print(f'  resized {filename}  {w}x{h} → {new_w}x{new_h}  ({before_kb} KB after)')

            resized += 1

    except Exception as exc:
        print(f'  ERROR   {filename}: {exc}')
        errors += 1

print()
print(f'{"Dry run — " if dry_run else ""}Done: {processed} images checked, {resized} {"would be " if dry_run else ""}resized, {skipped} skipped (non-image), {errors} errors.')
