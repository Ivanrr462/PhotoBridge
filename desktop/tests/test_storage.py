import io
import tempfile
import unittest
from pathlib import Path

from photobridge.storage import IncompleteUpload, Storage, sanitize_filename


class SanitizeFilenameTest(unittest.TestCase):
    def test_keeps_normal_names(self):
        self.assertEqual(sanitize_filename("IMG_1234.JPG"), "IMG_1234.JPG")
        self.assertEqual(sanitize_filename("Vacaciones playa.heic"), "Vacaciones playa.heic")

    def test_strips_paths(self):
        self.assertEqual(sanitize_filename("../../Windows/system32/evil.jpg"), "evil.jpg")
        self.assertEqual(sanitize_filename("C:\\Users\\x\\foto.png"), "foto.png")

    def test_replaces_invalid_characters(self):
        self.assertEqual(sanitize_filename('a<b>c:"d|e?f*.jpg'), "a_b_c__d_e_f_.jpg")

    def test_reserved_and_empty_names(self):
        self.assertEqual(sanitize_filename("CON.jpg"), "_CON.jpg")
        self.assertEqual(sanitize_filename(""), "foto")
        self.assertEqual(sanitize_filename("..."), "foto")
        self.assertEqual(sanitize_filename(".jpg"), "foto.jpg")

    def test_long_names_are_truncated(self):
        result = sanitize_filename("a" * 400 + ".jpeg")
        self.assertLessEqual(len(result), 150)
        self.assertTrue(result.endswith(".jpeg"))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "fotos"
        self.storage = Storage(self.dest)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saves_file_and_preserves_date(self):
        data = b"x" * 3_000_000
        path = self.storage.save_stream(io.BytesIO(data), len(data), "foto.jpg", 1_600_000_000_000)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(int(path.stat().st_mtime), 1_600_000_000)

    def test_does_not_overwrite_existing_files(self):
        first = self.storage.save_stream(io.BytesIO(b"1"), 1, "foto.jpg")
        second = self.storage.save_stream(io.BytesIO(b"2"), 1, "foto.jpg")
        self.assertEqual(first.name, "foto.jpg")
        self.assertEqual(second.name, "foto (1).jpg")
        self.assertEqual(first.read_bytes(), b"1")

    def test_incomplete_upload_leaves_no_files(self):
        with self.assertRaises(IncompleteUpload):
            self.storage.save_stream(io.BytesIO(b"abc"), 10, "foto.jpg")
        self.assertEqual(list(self.dest.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
