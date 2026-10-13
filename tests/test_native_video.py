"""Native Qt video/capture checks without a physical camera."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat, QtVideo
from PySide6.QtWidgets import QApplication

from capture_core import compress_jpeg
from webcam_capture import WebcamApp, capture_from_image, configure_backend, frame_image, preferred_format


class FakeFormat:
    def __init__(self, width, height, fps):
        self.size = QSize(width, height)
        self.fps = fps
    def resolution(self):
        return self.size
    def maxFrameRate(self):
        return self.fps


class NativeVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.window = WebcamApp(devices=[])
        self.window.show()
        self.application.processEvents()
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.wait_for(lambda: not self.window.busy)
        self.window.session.clear()
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 6
        while not predicate():
            self.application.processEvents()
            if time.monotonic() > deadline:
                self.fail("Native Qt operation timed out")
            time.sleep(0.002)
        self.application.processEvents()

    def send_frame(self, width=1920, height=1080, color="red"):
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(QColor(color))
        frame = QVideoFrame(image)
        self.window.video_sink.setVideoFrame(frame)
        self.application.processEvents()
        return frame

    def capture_one(self):
        self.send_frame()
        self.window.capture()
        self.wait_for(lambda: not self.window.busy)

    def test_supported_mode_prefers_real_1080p_60(self):
        formats = [FakeFormat(1280, 720, 120), FakeFormat(1920, 1080, 30),
                   FakeFormat(1920, 1080, 60), FakeFormat(3840, 2160, 30)]
        self.assertEqual(preferred_format(formats), 2)
        self.assertEqual(preferred_format(formats[:2]), 1)
        self.assertEqual(preferred_format([]), -1)

    def test_windows_auto_backend_uses_available_native_plugin(self):
        with patch.dict(os.environ, {}, clear=True), patch("webcam_capture.sys.platform", "win32"), patch(
                "webcam_capture.Path.glob", return_value=[Path("windows.dll")]), patch(
                "webcam_capture.QPluginLoader") as loader:
            loader.return_value.metaData.return_value = {"MetaData": {"Keys": ["windows"]}}
            self.assertEqual(configure_backend(), "windows")
            self.assertEqual(os.environ["QT_MEDIA_BACKEND"], "windows")

    def test_auto_backend_falls_back_and_preserves_explicit_choice(self):
        with patch.dict(os.environ, {}, clear=True), patch("webcam_capture.sys.platform", "win32"), patch(
                "webcam_capture.Path.glob", return_value=[]):
            self.assertEqual(configure_backend(), "Qt default")
            self.assertEqual(configure_backend("ffmpeg"), "ffmpeg")
            self.assertEqual(configure_backend(), "ffmpeg")

    def test_live_preview_never_maps_frames_or_runs_export_encoder(self):
        image = QImage(1920, 1080, QImage.Format.Format_RGBA8888)
        image.fill(QColor("red"))
        with patch.object(QVideoFrame, "toImage", side_effect=AssertionError("Live frame mapping")), patch(
                "capture_core.compress_jpeg", side_effect=AssertionError("Live encoding")):
            for index in range(120):
                frame = QVideoFrame(image)
                frame.setStartTime(index * 16_667)
                self.window.video_sink.setVideoFrame(frame)
                self.application.processEvents()
        self.assertEqual(self.window.actual_size, (1920, 1080))
        self.assertEqual(self.window.frame_count, 120)
        self.assertTrue(self.window.latest_frame.isValid())

    def test_capture_orientation_matches_native_frame_presentation(self):
        image = QImage(3, 2, QImage.Format.Format_RGB888)
        image.fill(QColor("black"))
        image.setPixelColor(0, 0, QColor("red"))
        image.setPixelColor(2, 1, QColor("blue"))
        original = capture_from_image(image).to_image()
        frame = QVideoFrame(image)
        frame.setRotation(QtVideo.Rotation.Clockwise90)
        frame.setMirrored(True)
        photo = capture_from_image(frame_image(frame))
        expected = original.transpose(Image.Transpose.ROTATE_270).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        self.assertEqual((photo.width, photo.height), expected.size)
        self.assertEqual(photo.rgb, expected.tobytes())

    def test_native_scanline_padding_is_removed_without_slant(self):
        image = QImage(321, 17, QImage.Format.Format_RGB888)
        image.fill(QColor("black"))
        for row in range(image.height()):
            image.setPixelColor(21, row, QColor("white"))
            image.setPixelColor(320, row, QColor(row, 80, 190))
        photo = capture_from_image(image)
        stored = photo.to_image()
        image.fill(QColor("red"))
        self.assertEqual(len(photo.rgb), 321 * 17 * 3)
        for row in range(17):
            self.assertEqual(stored.getpixel((21, row)), (255, 255, 255))
            self.assertEqual(stored.getpixel((320, row)), (row, 80, 190))

    def test_capture_is_full_resolution_and_export_is_the_only_encoding(self):
        with patch("capture_core.compress_jpeg", wraps=compress_jpeg) as encoder:
            self.capture_one()
            encoder.assert_not_called()
            photo = self.window.session.photos[0]
            self.assertEqual((photo.width, photo.height), (1920, 1080))
            original = photo.rgb
            self.send_frame(color="green")
            self.window.label_input.setText("Native capture")
            self.window.directory_input.setText(self.temporary.name)
            self.window.export()
            self.wait_for(lambda: not self.window.busy)
            encoder.assert_called_once()
            self.assertEqual(photo.rgb, original)
            self.assertFalse(self.window.session.dirty)
            output = list(Path(self.temporary.name).glob("*/*.jpg"))
            self.assertEqual(len(output), 1)
            self.assertLess(output[0].stat().st_size, 2_000_000)
            with Image.open(output[0]) as image:
                self.assertEqual(image.size, (1920, 1080))
                self.assertGreater(image.getpixel((0, 0))[0], 240)

    def test_fifteen_limit_remove_replacement_and_clear(self):
        self.send_frame(width=320, height=180)
        for _ in range(15):
            self.window.last_frame_at = time.monotonic()
            self.window.capture()
            self.wait_for(lambda: not self.window.busy)
        self.assertFalse(self.window.capture_button.isEnabled())
        self.window.capture()
        self.assertEqual(len(self.window.session.photos), 15)
        self.window.remove_selected()
        self.assertEqual(len(self.window.session.photos), 14)
        self.window.capture()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(self.window.photo_list.count(), 15)
        with patch.object(self.window, "_confirm_discard", return_value=True):
            self.window.clear_set()
        self.assertFalse(self.window.session.photos)

    def test_failed_export_retains_raw_pixels_for_retry(self):
        self.capture_one()
        original = self.window.session.photos[0].rgb
        self.window.label_input.setText("Retry")
        self.window.directory_input.setText(self.temporary.name)
        with patch("capture_core.os.fsync", side_effect=OSError("Disk full")):
            self.window.export()
            self.wait_for(lambda: not self.window.busy)
        self.assertIn("failed", self.window.status.text())
        self.assertTrue(self.window.session.dirty)
        self.assertEqual(self.window.session.photos[0].rgb, original)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])
        self.window.export()
        self.wait_for(lambda: not self.window.busy)
        self.assertFalse(self.window.session.dirty)

    def test_stop_and_stale_frames_disable_capture(self):
        self.send_frame()
        self.window.last_frame_at = time.monotonic() - 3
        self.window._sync_controls()
        self.assertFalse(self.window.capture_button.isEnabled())
        self.window.capture()
        self.assertFalse(self.window.busy)
        self.window.stop_camera()
        self.assertFalse(self.window.latest_frame.isValid())

    def test_cancel_close_preserves_unexported_set(self):
        self.capture_one()
        with patch.object(self.window, "_confirm_discard", return_value=False):
            self.assertFalse(self.window.close())
        self.assertEqual(len(self.window.session.photos), 1)
        self.assertTrue(self.window.isVisible())

    def test_no_camera_controls_do_not_open_a_fake_device(self):
        self.assertFalse(self.window.camera_button.isEnabled())
        self.assertFalse(self.window.capture_button.isEnabled())
        self.window.toggle_camera()
        self.assertIsNone(self.window.camera)


if __name__ == "__main__":
    unittest.main()
