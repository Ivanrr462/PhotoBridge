import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from photobridge.metadata import capture_time

from .media_samples import heic_with_exif, jpeg_with_exif, mp4_with_creation

# 2024-07-14 18:30:00 en +02:00 == 16:30:00 UTC
EXPECTED_UTC = datetime(2024, 7, 14, 16, 30, tzinfo=timezone.utc).timestamp()


class CaptureTimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_jpeg_exif_with_offset(self):
        self.assertEqual(capture_time(self.write("a.jpg", jpeg_with_exif())), EXPECTED_UTC)

    def test_jpeg_exif_without_offset_uses_local_time(self):
        path = self.write("b.jpg", jpeg_with_exif(offset=None))
        self.assertEqual(capture_time(path), datetime(2024, 7, 14, 18, 30).timestamp())

    def test_heic_exif(self):
        self.assertEqual(capture_time(self.write("c.heic", heic_with_exif())), EXPECTED_UTC)

    def test_mov_and_mp4_creation_time(self):
        epoch = int(EXPECTED_UTC)
        self.assertEqual(capture_time(self.write("d.mov", mp4_with_creation(epoch, b"qt  "))), epoch)
        self.assertEqual(capture_time(self.write("e.mp4", mp4_with_creation(epoch, mdat_first=False))), epoch)

    def test_unknown_or_broken_files_return_none(self):
        self.assertIsNone(capture_time(self.write("f.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 100)))
        self.assertIsNone(capture_time(self.write("g.jpg", b"\xff\xd8\xff\xe1\x00")))
        self.assertIsNone(capture_time(self.write("h.mp4", b"\0\0\0\x20ftypisom" + b"\xff" * 30)))
        self.assertIsNone(capture_time(self.write("i.jpg", jpeg_with_exif(date="0000:00:00 00:00:00"))))
        self.assertIsNone(capture_time(self.dir / "no-existe.jpg"))


if __name__ == "__main__":
    unittest.main()
