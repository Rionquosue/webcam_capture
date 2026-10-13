"""Capture/export regression checks, using synthetic image data."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from capture_core import CaptureSet, CapturedPhoto, MAX_JPEG_BYTES, Photo, compress_jpeg, export_photos, safe_label

class CompressionTests(unittest.TestCase):
    def test_jpeg_keeps_color_dimensions_and_source(self):
        source = Image.new("RGB", (640, 480), "red")
        photo = compress_jpeg(source)
        self.assertLess(len(photo.jpeg), MAX_JPEG_BYTES)
        with Image.open(io.BytesIO(photo.jpeg)) as result:
            self.assertEqual(result.format, "JPEG")
            self.assertEqual(result.size, (640, 480))
            red, green, blue = result.getpixel((100, 100))
            self.assertGreater(red, 240)
            self.assertLess(green, 10)
            self.assertLess(blue, 10)
        self.assertEqual(source.size, (640, 480))

    def test_noisy_large_image_is_strictly_under_decimal_2mb(self):
        noise = np.random.default_rng(1).integers(
            0, 256, (3600, 4800, 3), dtype=np.uint8)
        source = Image.fromarray(noise)
        photo = compress_jpeg(source)
        self.assertLess(len(photo.jpeg), 2_000_000)
        self.assertLessEqual(max(photo.width, photo.height), 2560)
        self.assertAlmostEqual(photo.width / photo.height, 4 / 3, places=2)
        with Image.open(io.BytesIO(photo.jpeg)) as result:
            result.load()
            self.assertEqual(result.size, (photo.width, photo.height))
        self.assertEqual(source.size, (4800, 3600))

    def test_quality_reduction_without_unnecessary_resizing(self):
        noise = np.random.default_rng(2).integers(
            0, 256, (1920, 1920, 3), dtype=np.uint8)
        photo = compress_jpeg(Image.fromarray(noise))
        self.assertLess(len(photo.jpeg), MAX_JPEG_BYTES)
        self.assertLess(photo.quality, 95)
        self.assertEqual((photo.width, photo.height), (1920, 1920))

    def test_dimensions_shrink_when_quality_is_not_enough(self):
        noise = np.random.default_rng(3).integers(
            0, 256, (600, 800, 3), dtype=np.uint8)
        photo = compress_jpeg(Image.fromarray(noise), max_bytes=10_000)
        self.assertLess(len(photo.jpeg), 10_000)
        self.assertLess(photo.width, 800)
        self.assertLess(photo.height, 600)
        self.assertAlmostEqual(photo.width / photo.height, 4 / 3, places=1)

    def test_invalid_or_impossible_limits_fail(self):
        source = Image.new("RGB", (10, 10), "blue")
        for kwargs in ({"max_bytes": 0}, {"max_edge": 0}, {"max_bytes": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                compress_jpeg(source, **kwargs)


class StorageAndExportTests(unittest.TestCase):
    def setUp(self):
        self.photo = CapturedPhoto.from_image(Image.new("RGB", (64, 48), "blue"))

    def test_full_resolution_pixels_are_retained_without_encoding(self):
        noise = np.random.default_rng(51).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
        source = Image.fromarray(noise)
        reference = source.tobytes()
        with patch("capture_core.compress_jpeg", side_effect=AssertionError("Early encoding")):
            photo = CapturedPhoto.from_image(source)
            session = CaptureSet()
            session.add(photo)
        source.paste("black", (0, 0, source.width, source.height))
        self.assertEqual(photo.rgb, reference)
        self.assertEqual((photo.width, photo.height), (1920, 1080))
        self.assertGreater(len(photo.rgb), MAX_JPEG_BYTES)
        self.assertEqual(photo.to_image().tobytes(), reference)

    def test_limit_remove_clear_and_export_revision(self):
        session = CaptureSet()
        for _ in range(15):
            session.add(self.photo)
        with self.assertRaises(ValueError):
            session.add(self.photo)
        self.assertTrue(session.dirty)
        session.mark_exported()
        self.assertFalse(session.dirty)
        session.remove(0)
        session.add(self.photo)
        self.assertTrue(session.dirty)
        self.assertEqual(len(session.photos), 15)
        session.clear()
        self.assertFalse(session.dirty)
        self.assertFalse(session.photos)

    def test_invalid_capture_and_encoded_input_rejected(self):
        for rgb, width, height in ((b"", 0, 0), (b"x", 2, 2), (bytearray(3), 1, 1)):
            with self.subTest(width=width), self.assertRaises(ValueError):
                CapturedPhoto(rgb, width, height)
        with self.assertRaises(ValueError):
            CaptureSet().add(Photo(b"jpeg", 1, 1, 95))

    def test_safe_labels_reserved_names_and_utf8_length(self):
        for label in ("", " . ", "/\\:*?"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                safe_label(label)
        for label in ("CON", "LPT1", "aux.txt", "COM¹"):
            self.assertTrue(safe_label(label).startswith("set_"))
        result = safe_label(" ../My: test/set? ")
        for character in ('/', '\\', ':', '?'):
            self.assertNotIn(character, result)
        self.assertLessEqual(len(safe_label("測" * 100).encode("utf-8")), 140)

    def test_export_files_and_collision_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("capture_core.datetime") as clock:
                from datetime import datetime
                clock.now.return_value = datetime(2026, 10, 13, 13, 6, 0)
                first = export_photos(Path(temporary), "Sample", (self.photo,) * 15)
                (first / "keep.txt").write_text("existing")
                second = export_photos(Path(temporary), "Sample", (self.photo,))
            self.assertEqual(second.name, "Sample_20261013_130600_2")
            self.assertEqual((first / "keep.txt").read_text(), "existing")
            files = list(first.glob("*.jpg"))
            self.assertEqual(len(files), 15)
            for filename in files:
                self.assertLess(filename.stat().st_size, MAX_JPEG_BYTES)
                with Image.open(filename) as image:
                    self.assertEqual(image.size, (64, 48))
                    self.assertGreater(image.getpixel((15, 15))[2], 240)

    def test_repeat_export_preserves_original_and_jpeg_pixels(self):
        source = Image.fromarray(np.random.default_rng(1).integers(
            0, 256, (720, 1280, 3), dtype=np.uint8))
        photo = CapturedPhoto.from_image(source)
        original = photo.rgb
        with tempfile.TemporaryDirectory() as temporary:
            with patch("capture_core.compress_jpeg", wraps=compress_jpeg) as encoder:
                first = export_photos(Path(temporary), "Test", (photo,))
                second = export_photos(Path(temporary), "Test", (photo,))
            self.assertEqual(encoder.call_count, 2)
            self.assertEqual((first / "Test_01.jpg").read_bytes(),
                             (second / "Test_01.jpg").read_bytes())
        self.assertEqual(photo.rgb, original)

    def test_exact_two_mb_is_rejected_and_failure_cleans_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("capture_core.compress_jpeg",
                       return_value=Photo(b"x" * MAX_JPEG_BYTES, 1, 1, 95)):
                with self.assertRaises(ValueError):
                    export_photos(Path(temporary), "Test", (self.photo,))
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(self.photo.to_image().getpixel((0, 0)), (0, 0, 255))

    def test_failed_second_encoder_retains_set_and_cleans_partial_output(self):
        session = CaptureSet()
        session.add(self.photo)
        session.add(self.photo)
        valid = compress_jpeg(self.photo.to_image())
        with tempfile.TemporaryDirectory() as temporary:
            with patch("capture_core.compress_jpeg", side_effect=[valid, OSError("Encoder failed")]):
                with self.assertRaisesRegex(OSError, "Encoder failed"):
                    export_photos(Path(temporary), "Test", tuple(session.photos))
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertTrue(session.dirty)
        self.assertEqual(len(session.photos), 2)

    def test_invalid_export_has_no_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            for photos in ((), (self.photo,) * 16, (object(),)):
                with self.subTest(count=len(photos)), self.assertRaises(ValueError):
                    export_photos(Path(temporary), "Test", photos)
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
