import secrets
from pathlib import Path

from PIL import Image
from werkzeug.datastructures import FileStorage

from diytracker.paths import ROOT

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
# Deliberately a ROOT-relative string: save_flyer_file() returns it joined with
# the filename, and that value is stored in the DB and rendered as the flyer
# URL (templates/calendar.html). Only the filesystem write resolves against
# ROOT below.
UPLOAD_FOLDER = "static/uploads"
MAGIC_BYTES = [b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a"]


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_image_content(file_storage):
    header = file_storage.read(8)
    file_storage.seek(0)
    return any(header.startswith(m) for m in MAGIC_BYTES)


_MAX_FLYER_SIDE = 800


def _verify_and_resize_flyer(path):
    """Fully decode the file to prove it is an image, then downscale it.

    The decode is half the point: validate_image_content() only inspects the
    first 8 bytes, so everything past the magic number is unverified until
    Pillow actually reads it. img.load() is what forces that — without it a
    GIF (which is never resized) was never decoded at all. Raises on anything
    undecodable; save_flyer_file turns that into a rejection.
    """
    with Image.open(path) as img:
        # Raises on truncated or corrupt data; Image.open() itself already
        # raises DecompressionBombError on an implausibly large canvas.
        img.load()
        fmt = img.format
        # GIFs are stored as uploaded — thumbnail() would flatten an animation
        # to its first frame. The load() above is what validates them.
        if fmt == "GIF":
            return
        if img.width <= _MAX_FLYER_SIDE and img.height <= _MAX_FLYER_SIDE:
            return
        img.thumbnail((_MAX_FLYER_SIDE, _MAX_FLYER_SIDE), Image.LANCZOS)
        save_kw = {"format": fmt, "optimize": True}
        if fmt == "JPEG":
            save_kw.update({"quality": 85, "progressive": True})
        img.save(path, **save_kw)


def save_flyer_file(file_storage, upload_folder):
    """Validate, save, and resize a flyer upload. Returns the saved path or None.

    Guards on FileStorage because a form built with obj=... can leak the
    stored path *string* into the field's data when the POST carries no
    file part at all (clients that omit the input entirely).

    Returning None is the only failure mode: every caller treats it as "no
    flyer". The magic-byte check only looks at the first 8 bytes, so a file
    that starts like a PNG and continues as garbage gets this far and then
    blows up inside Pillow — which used to surface as a 500 on /submit and
    on POST /api/ingest (whose documented contract is 422 for bad input, and
    whose pusher aborts its whole run on an unexpected status). A file we
    could not decode is not a flyer, so it is deleted and rejected here.
    """
    if not isinstance(file_storage, FileStorage):
        return None
    if not file_storage.filename:
        return None
    if allowed_file(file_storage.filename) and validate_image_content(file_storage):
        # Random server-side name: avoids collisions between uploads and any
        # filename-derived path issues; only the validated extension is kept.
        ext = file_storage.filename.rsplit(".", 1)[1].lower()
        filename = f"{secrets.token_hex(8)}.{ext}"
        path = str(Path(upload_folder) / filename)
        fs_path = path if Path(path).is_absolute() else str(ROOT / path)
        file_storage.save(fs_path)
        try:
            _verify_and_resize_flyer(fs_path)
        except Exception:  # noqa: BLE001 - any decode failure means "not a flyer"
            # Truncated data, a decompression bomb, an unreadable file: don't
            # leave the rejected bytes sitting in static/uploads/.
            Path(fs_path).unlink(missing_ok=True)
            return None
        return path
    return None
