"""Flyer upload pipeline — validation, magic-byte checks, and resizing.

These run the real Pillow code path against generated images rather than
mocking it, since the resize/format handling is the point of the module.
"""

import io

from PIL import Image
from werkzeug.datastructures import FileStorage

from diytracker.services.uploads import save_flyer_file


def _png(size=(100, 100), color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    buf.seek(0)
    return buf


def _filestorage(buf, filename):
    return FileStorage(stream=buf, filename=filename, content_type="image/png")


class TestSaveFlyer:
    def test_saves_valid_png(self, tmp_path):
        fs = _filestorage(_png(), "flyer.png")
        path = save_flyer_file(fs, str(tmp_path))
        assert path is not None
        # Filenames are randomised server-side; only the extension survives.
        saved = list(tmp_path.iterdir())
        assert len(saved) == 1
        assert saved[0].suffix == ".png"

    def test_same_filename_does_not_overwrite(self, tmp_path):
        path_a = save_flyer_file(_filestorage(_png(), "flyer.png"), str(tmp_path))
        path_b = save_flyer_file(
            _filestorage(_png(color=(0, 255, 0)), "flyer.png"), str(tmp_path)
        )
        assert path_a != path_b
        assert len(list(tmp_path.iterdir())) == 2

    def test_rejects_disallowed_extension(self, tmp_path):
        fs = _filestorage(_png(), "flyer.txt")
        assert save_flyer_file(fs, str(tmp_path)) is None

    def test_rejects_extension_content_mismatch(self, tmp_path):
        """A .png whose bytes aren't an image must fail the magic-byte check."""
        fake = io.BytesIO(b"this is not an image")
        fs = _filestorage(fake, "evil.png")
        assert save_flyer_file(fs, str(tmp_path)) is None

    def test_no_file_returns_none(self, tmp_path):
        assert save_flyer_file(None, str(tmp_path)) is None
        empty = _filestorage(io.BytesIO(b""), "")
        assert save_flyer_file(empty, str(tmp_path)) is None

    def test_large_image_is_downscaled(self, tmp_path):
        fs = _filestorage(_png(size=(2000, 1500)), "big.png")
        path = save_flyer_file(fs, str(tmp_path))
        with Image.open(path) as img:
            assert max(img.size) <= 800

    def test_small_image_left_untouched(self, tmp_path):
        fs = _filestorage(_png(size=(200, 200)), "small.png")
        path = save_flyer_file(fs, str(tmp_path))
        with Image.open(path) as img:
            assert img.size == (200, 200)

    def test_filename_is_sanitised(self, tmp_path):
        fs = _filestorage(_png(), "../../etc/passwd.png")
        path = save_flyer_file(fs, str(tmp_path))
        # The stored name is generated server-side; traversal input is inert.
        assert path is not None
        assert str(tmp_path) in path
        assert ".." not in path.split(str(tmp_path), 1)[1]
