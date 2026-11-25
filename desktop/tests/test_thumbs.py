import sys
import tempfile
import unittest
from pathlib import Path

from photobridge.thumbs import _to_ppm, init_thread, windows_thumbnail


class PpmTest(unittest.TestCase):
    def test_opaque_pixels_are_reordered(self):
        bgra = bytes([10, 20, 30, 255, 40, 50, 60, 255])  # 2 píxeles BGRA
        self.assertEqual(_to_ppm(2, 1, bgra, (255, 255, 255)), b"P6 2 1 255\n" + bytes([30, 20, 10, 60, 50, 40]))

    def test_transparency_is_blended_with_background(self):
        # Píxel transparente + píxel al 50 % de azul (alfa premultiplicado)
        bgra = bytes([0, 0, 0, 0, 128, 0, 0, 128])
        ppm = _to_ppm(2, 1, bgra, (200, 100, 50))
        self.assertEqual(ppm[-6:], bytes([200, 100, 50, 99, 49, 152]))


@unittest.skipUnless(sys.platform == "win32", "las miniaturas las genera Windows")
class WindowsThumbnailTest(unittest.TestCase):
    def setUp(self):
        init_thread()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_file_has_no_thumbnail(self):
        path = self.dir / "algo.xyz"
        path.write_bytes(b"hola")
        self.assertIsNone(windows_thumbnail(path, 100))

    def test_missing_file(self):
        self.assertIsNone(windows_thumbnail(self.dir / "no-existe.jpg", 100))

    def test_photo_thumbnail(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow (requirements-dev) no está instalado")
        path = self.dir / "foto.jpg"
        Image.new("RGB", (800, 400), (0, 128, 255)).save(path, quality=95)
        thumb = windows_thumbnail(path, 100)
        self.assertIsNotNone(thumb)
        self.assertEqual((thumb.width, thumb.height), (100, 50))  # mantiene la proporción
        header = b"P6 100 50 255\n"
        self.assertTrue(thumb.ppm.startswith(header))
        self.assertEqual(len(thumb.ppm), len(header) + 100 * 50 * 3)
        r, g, b = thumb.ppm[len(header) : len(header) + 3]
        self.assertTrue(r < 20 and 110 < g < 145 and b > 235, (r, g, b))


if __name__ == "__main__":
    unittest.main()
