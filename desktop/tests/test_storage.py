import io
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from photobridge.storage import (
    PART_SUFFIX,
    STALE_PART_SECONDS,
    IncompleteUpload,
    InvalidUpload,
    OffsetMismatch,
    Storage,
    UnknownUpload,
    is_generic_name,
    sanitize_filename,
)

from .media_samples import jpeg_with_exif

ID_A = "a" * 32
ID_B = "b" * 32


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

    def test_generic_names(self):
        for name in ("image.jpg", "IMAGE.JPEG", "video.mov", "foto", "4F3A9C1E-2B7D-4E61-9A0B-7C2D1E3F4A5B.heic"):
            self.assertTrue(is_generic_name(name), name)
        for name in ("IMG_1234.HEIC", "Vacaciones.jpg", "cafe.mp4"):
            self.assertFalse(is_generic_name(name), name)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "fotos"
        self.index = Path(self.tmp.name) / "sent.json"
        self.storage = Storage(self.dest, index_path=self.index)

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, upload_id, name, data, chunk=1000, last_modified=0, storage=None):
        storage = storage or self.storage
        state = storage.start(upload_id, name, len(data), last_modified)
        while not state.done:
            piece = data[state.offset : state.offset + chunk]
            state = storage.append(upload_id, state.offset, io.BytesIO(piece), len(piece))
        return state

    def visible_files(self):
        return sorted(p.name for p in self.dest.iterdir() if not p.name.endswith(PART_SUFFIX))

    def test_upload_in_chunks(self):
        data = os.urandom(3_500_000)
        state = self.send(ID_A, "video.MOV", data, chunk=1_000_000, last_modified=1_600_000_000_000)
        self.assertEqual(state.saved.read_bytes(), data)
        self.assertEqual(state.offset, len(data))
        # video.MOV es un nombre genérico: se renombra con la fecha
        self.assertTrue(state.saved.name.startswith("IMG_2020"), state.saved.name)
        self.assertEqual(int(state.saved.stat().st_mtime), 1_600_000_000)
        self.assertEqual(self.visible_files(), [state.saved.name])

    def test_capture_date_from_exif_wins_over_browser_date(self):
        jpeg = jpeg_with_exif()
        state = self.send(ID_A, "IMG_0001.jpg", jpeg, last_modified=int(time.time() * 1000))
        expected = datetime(2024, 7, 14, 16, 30, tzinfo=timezone.utc).timestamp()
        self.assertEqual(state.saved.name, "IMG_0001.jpg")
        self.assertEqual(state.saved.stat().st_mtime, expected)

    def test_resume_after_interruption(self):
        data = os.urandom(10_000)
        self.storage.start(ID_A, "clip.mp4", len(data))
        self.storage.append(ID_A, 0, io.BytesIO(data[:4000]), 4000)
        # Corte a mitad del segundo trozo: solo llegan 1500 de 4000 bytes
        with self.assertRaises(IncompleteUpload):
            self.storage.append(ID_A, 4000, io.BytesIO(data[4000:5500]), 4000)
        # El móvil vuelve, pregunta y sigue desde donde se quedó (incluso con otro Storage)
        fresh = Storage(self.dest, index_path=self.index)
        state = fresh.start(ID_A, "clip.mp4", len(data))
        self.assertEqual(state.offset, 5500)
        state = fresh.append(ID_A, 5500, io.BytesIO(data[5500:]), len(data) - 5500)
        self.assertTrue(state.done)
        self.assertEqual(state.saved.read_bytes(), data)

    def test_wrong_offset_reports_the_real_one(self):
        self.storage.start(ID_A, "a.jpg", 100)
        self.storage.append(ID_A, 0, io.BytesIO(b"x" * 40), 40)
        with self.assertRaises(OffsetMismatch) as ctx:
            self.storage.append(ID_A, 0, io.BytesIO(b"x" * 40), 40)
        self.assertEqual(ctx.exception.offset, 40)

    def test_invalid_requests(self):
        with self.assertRaises(InvalidUpload):
            self.storage.start("../../etc", "a.jpg", 10)
        with self.assertRaises(InvalidUpload):
            self.storage.start(ID_A, "a.jpg", 0)
        with self.assertRaises(UnknownUpload):
            self.storage.append(ID_B, 0, io.BytesIO(b"x"), 1)
        self.storage.start(ID_A, "a.jpg", 10)
        with self.assertRaises(InvalidUpload):
            self.storage.append(ID_A, 0, io.BytesIO(b"x" * 20), 20)

    def test_already_sent_files_are_skipped(self):
        first = self.send(ID_A, "IMG_1.jpg", b"contenido")
        again = Storage(self.dest, index_path=self.index).start(ID_A, "IMG_1.jpg", len(b"contenido"))
        self.assertTrue(again.skipped)
        self.assertEqual(again.saved, first.saved)
        self.assertEqual(self.visible_files(), ["IMG_1.jpg"])

    def test_skipped_only_if_the_file_is_still_there(self):
        first = self.send(ID_A, "IMG_1.jpg", b"contenido")
        first.saved.unlink()
        state = self.storage.start(ID_A, "IMG_1.jpg", len(b"contenido"))
        self.assertFalse(state.skipped)
        self.assertEqual(state.offset, 0)

    def test_same_name_different_content_keeps_both(self):
        self.send(ID_A, "IMG_1.jpg", b"uno")
        self.send(ID_B, "IMG_1.jpg", b"dos")
        self.assertEqual(self.visible_files(), ["IMG_1 (1).jpg", "IMG_1.jpg"])

    def test_stale_parts_are_cleaned(self):
        self.dest.mkdir(parents=True)
        old = self.dest / f".{ID_A}{PART_SUFFIX}"
        old.write_bytes(b"x")
        past = time.time() - STALE_PART_SECONDS - 60
        os.utime(old, (past, past))
        Storage(self.dest)
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
