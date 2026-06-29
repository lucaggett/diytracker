"""Flyer upload pipeline — validation, magic-byte checks, and resizing.

These run the real Pillow code path against generated images rather than
mocking it, since the resize/format handling is the point of the module.
"""
import io

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from services.uploads import save_flyer_file


def _png(size=(100, 100), color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    buf.seek(0)
    return buf


def _filestorage(buf, filename):
    return FileStorage(stream=buf, filename=filename, content_type='image/png')


class TestSaveFlyer:
    def test_saves_valid_png(self, tmp_path):
        fs = _filestorage(_png(), 'flyer.png')
        path = save_flyer_file(fs, str(tmp_path))
        assert path is not None
        assert (tmp_path / 'flyer.png').exists()

    def test_rejects_disallowed_extension(self, tmp_path):
        fs = _filestorage(_png(), 'flyer.txt')
        assert save_flyer_file(fs, str(tmp_path)) is None

    def test_rejects_extension_content_mismatch(self, tmp_path):
        """A .png whose bytes aren't an image must fail the magic-byte check."""
        fake = io.BytesIO(b'this is not an image')
        fs = _filestorage(fake, 'evil.png')
        assert save_flyer_file(fs, str(tmp_path)) is None

    def test_no_file_returns_none(self, tmp_path):
        assert save_flyer_file(None, str(tmp_path)) is None
        empty = _filestorage(io.BytesIO(b''), '')
        assert save_flyer_file(empty, str(tmp_path)) is None

    def test_large_image_is_downscaled(self, tmp_path):
        fs = _filestorage(_png(size=(2000, 1500)), 'big.png')
        path = save_flyer_file(fs, str(tmp_path))
        with Image.open(path) as img:
            assert max(img.size) <= 800

    def test_small_image_left_untouched(self, tmp_path):
        fs = _filestorage(_png(size=(200, 200)), 'small.png')
        path = save_flyer_file(fs, str(tmp_path))
        with Image.open(path) as img:
            assert img.size == (200, 200)

    def test_filename_is_sanitised(self, tmp_path):
        fs = _filestorage(_png(), '../../etc/passwd.png')
        path = save_flyer_file(fs, str(tmp_path))
        # secure_filename strips the traversal; file lands inside the folder.
        assert path is not None
        assert str(tmp_path) in path
        assert '..' not in path.split(str(tmp_path), 1)[1]
