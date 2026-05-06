import os

from PIL import Image
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
UPLOAD_FOLDER = 'static/uploads'
MAGIC_BYTES = [b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a']


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_image_content(file_storage):
    header = file_storage.read(8)
    file_storage.seek(0)
    return any(header.startswith(m) for m in MAGIC_BYTES)


_MAX_FLYER_SIDE = 800


def _resize_flyer(path):
    with Image.open(path) as img:
        if img.format == 'GIF':
            return
        if img.width <= _MAX_FLYER_SIDE and img.height <= _MAX_FLYER_SIDE:
            return
        fmt = img.format
        img.thumbnail((_MAX_FLYER_SIDE, _MAX_FLYER_SIDE), Image.LANCZOS)
        save_kw = {'format': fmt, 'optimize': True}
        if fmt == 'JPEG':
            save_kw.update({'quality': 85, 'progressive': True})
        img.save(path, **save_kw)


def save_flyer_file(file_storage, upload_folder):
    """Validate, save, and resize a flyer upload. Returns the saved path or None."""
    if not file_storage or not file_storage.filename:
        return None
    if allowed_file(file_storage.filename) and validate_image_content(file_storage):
        filename = secure_filename(file_storage.filename)
        path = os.path.join(upload_folder, filename)
        file_storage.save(path)
        _resize_flyer(path)
        return path
    return None
