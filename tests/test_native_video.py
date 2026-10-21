"""Native Qt video/capture checks without a physical camera."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import weakref
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import QEvent, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent
from PySide6.QtMultimedia import QCamera, QVideoFrame, QVideoFrameFormat, QtVideo
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

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
        self.temporary = tempfile.TemporaryDirectory()
        preferences = tempfile.TemporaryDirectory()
        self.addCleanup(preferences.cleanup)
        self.settings = QSettings(str(Path(preferences.name) / "preferences.ini"), QSettings.Format.IniFormat)
        opener = patch("webcam_capture.QDesktopServices.openUrl", return_value=True)
        self.open_folder = opener.start()
        self.addCleanup(opener.stop)
        self.window = WebcamApp(devices=[], settings=self.settings)
        self.window.show()
        self.application.processEvents()

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
            self.assertEqual(self.window.session.photos, [])
            self.assertEqual(self.window.photo_list.count(), 0)
            self.assertEqual(self.window.count.text(), "0 / 15 photos")
            self.assertTrue(self.window.review.pixmap().isNull())
            self.assertFalse(self.window.export_button.isEnabled())
            output = list(Path(self.temporary.name).glob("*/*.jpg"))
            self.assertEqual(len(output), 1)
            self.open_folder.assert_called_once()
            self.assertTrue(Path(self.open_folder.call_args.args[0].toLocalFile()).samefile(output[0].parent))
            self.assertEqual(self.settings.value("export_directory"), str(Path(self.temporary.name).resolve()))
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
        self.open_folder.assert_not_called()
        self.window.export()
        self.wait_for(lambda: not self.window.busy)
        self.assertFalse(self.window.session.dirty)
        self.assertEqual(self.window.session.photos, [])
        self.open_folder.assert_called_once()

    def test_successful_export_releases_captures_even_if_folder_cannot_open(self):
        self.capture_one()
        original = weakref.ref(self.window.session.photos[0])
        self.window.label_input.setText("Folder open failure")
        self.window.directory_input.setText(self.temporary.name)
        self.open_folder.return_value = False
        self.window.export()
        self.wait_for(lambda: not self.window.busy)
        self.wait_for(lambda: original() is None)
        self.assertEqual(self.window.photo_list.count(), 0)
        self.assertIn("Open this folder manually", self.window.status.text())
        self.assertEqual(len(list(Path(self.temporary.name).glob("*/*.jpg"))), 1)

    def activate_capture_window(self):
        self.window.showNormal()
        self.window.activateWindow()
        self.window.capture_button.setFocus()
        self.application.processEvents()
        self.assertTrue(self.window.isActiveWindow())

    def test_space_captures_once_without_keyboard_auto_repeat(self):
        self.send_frame(width=320, height=180)
        self.activate_capture_window()
        QTest.keyClick(self.window.capture_button, Qt.Key.Key_Space)
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(len(self.window.session.photos), 1)
        with patch.object(self.window, "capture") as capture:
            for _ in range(5):
                event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                                  Qt.KeyboardModifier.NoModifier, " ", True, 1)
                self.application.sendEvent(self.window.capture_button, event)
            self.application.processEvents()
            capture.assert_not_called()

    def test_space_remains_text_in_label_and_directory_fields(self):
        self.send_frame(width=320, height=180)
        self.activate_capture_window()
        with patch.object(self.window, "capture") as capture:
            for field in (self.window.label_input, self.window.directory_input):
                field.setFocus()
                self.application.processEvents()
                QTest.keyClicks(field, "Text with spaces")
                self.assertEqual(field.text(), "Text with spaces")
            capture.assert_not_called()

    def test_space_does_not_capture_with_another_window_active_or_app_hidden(self):
        self.send_frame(width=320, height=180)
        self.activate_capture_window()
        other = QWidget()
        try:
            other.show()
            other.activateWindow()
            self.application.processEvents()
            self.assertFalse(self.window.isActiveWindow())
            with patch.object(self.window, "capture") as capture:
                QTest.keyClick(other, Qt.Key.Key_Space)
                self.window._capture_from_shortcut()
                self.window.showMinimized()
                self.application.processEvents()
                self.window._capture_from_shortcut()
                self.window.hide()
                QTest.keyClick(self.window, Qt.Key.Key_Space)
                self.window._capture_from_shortcut()
                capture.assert_not_called()
        finally:
            other.close()
            other.deleteLater()

    def test_space_does_not_capture_behind_a_modal_dialog(self):
        self.send_frame(width=320, height=180)
        self.activate_capture_window()
        dialog = QDialog(self.window)
        try:
            dialog.setModal(True)
            dialog.show()
            self.application.processEvents()
            with patch.object(self.window, "capture") as capture:
                QTest.keyClick(dialog, Qt.Key.Key_Space)
                self.window._capture_from_shortcut()
                capture.assert_not_called()
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_browse_persists_parent_directory_for_a_new_window(self):
        with patch("webcam_capture.QFileDialog.getExistingDirectory", return_value=self.temporary.name):
            self.window.browse()
        settings = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        reopened = WebcamApp(devices=[], settings=settings)
        try:
            self.assertEqual(reopened.directory_input.text(), str(Path(self.temporary.name).resolve()))
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_typed_directory_is_saved_on_close_but_invalid_edits_do_not_replace_it(self):
        self.window.directory_input.setText(self.temporary.name)
        self.assertTrue(self.window.close())
        settings = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        expected = str(Path(self.temporary.name).resolve())
        self.assertEqual(settings.value("export_directory"), expected)
        self.window.directory_input.setText(str(Path(self.temporary.name) / "does-not-exist"))
        self.window._remember_export_directory()
        settings.sync()
        self.assertEqual(settings.value("export_directory"), expected)

    def add_bookmark(self, name, directory):
        self.window.directory_input.setText(str(directory))
        self.window.bookmark_add_button.click()
        self.assertIs(self.window.bookmark_stack.currentWidget(), self.window.bookmark_editor)
        self.window.bookmark_name_input.setText(name)
        self.window.bookmark_save_button.click()

    def click_bookmark(self, row=0):
        item = self.window.bookmark_list.item(row)
        QTest.mouseClick(self.window.bookmark_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.window.bookmark_list.visualItemRect(item).center())

    def test_bookmarks_save_cancel_select_and_persist_without_changing_label(self):
        original = Path(self.temporary.name).resolve()
        other = original / "Other directory"
        other.mkdir()
        self.window.label_input.setText("Keep this label")
        self.window.directory_input.setText(str(original))
        self.window.bookmark_add_button.click()
        self.window.bookmark_name_input.setText("Project A / photos & scans")
        self.window.directory_input.setText(str(other))
        self.window.bookmark_save_button.click()
        self.assertEqual(self.window.bookmark_list.count(), 1)
        self.assertEqual(self.window.directory_input.text(), str(other))
        self.click_bookmark()
        self.assertEqual(self.window.directory_input.text(), str(original))
        self.assertEqual(self.window.label_input.text(), "Keep this label")
        self.open_folder.assert_not_called()
        self.window.bookmark_add_button.click()
        self.window.bookmark_name_input.clear()
        self.assertFalse(self.window.bookmark_save_button.isEnabled())
        self.window.bookmark_name_input.setText("Cancelled bookmark")
        self.window.bookmark_cancel_button.click()
        self.assertEqual(self.window.bookmark_list.count(), 1)
        reopened = WebcamApp(devices=[], settings=QSettings(self.settings.fileName(), QSettings.Format.IniFormat))
        try:
            item = reopened.bookmark_list.item(0)
            self.assertEqual(item.text(), "Project A / photos & scans")
            self.assertEqual(item.data(Qt.ItemDataRole.UserRole), str(original))
            self.assertEqual(reopened.bookmark_list.count(), 1)
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_bookmark_rejects_missing_directory_without_creating_it(self):
        missing = Path(self.temporary.name) / "missing"
        self.window.directory_input.setText(str(missing))
        self.window.bookmark_add_button.click()
        self.assertIs(self.window.bookmark_stack.currentWidget(), self.window.bookmark_list)
        self.assertEqual(self.window.bookmark_list.count(), 0)
        self.assertFalse(missing.exists())

    def test_bookmark_name_does_not_change_export_folder_or_jpeg_names(self):
        destination = Path(self.temporary.name) / "Output directory"
        destination.mkdir()
        self.add_bookmark("Completely different bookmark name", destination)
        self.window.directory_input.setText(self.temporary.name)
        self.click_bookmark()
        self.window.label_input.setText("Actual set label")
        self.capture_one()
        self.window.export()
        self.wait_for(lambda: not self.window.busy)
        folders = list(destination.iterdir())
        self.assertEqual(len(folders), 1)
        self.assertTrue(folders[0].name.startswith("Actual set label_"))
        self.assertEqual([path.name for path in folders[0].iterdir()], ["Actual set label_01.jpg"])
        self.assertEqual(self.window.session.photos, [])
        self.open_folder.assert_called_once()
        self.assertTrue(Path(self.open_folder.call_args.args[0].toLocalFile()).samefile(folders[0]))

    def test_escape_deselects_fields_without_changing_values_then_space_captures(self):
        self.add_bookmark("Capture directory", self.temporary.name)
        self.capture_one()
        self.activate_capture_window()
        self.click_bookmark()
        self.window.label_input.setText("Preserve this label")
        self.window.directory_input.selectAll()
        self.window.label_input.setFocus()
        self.window.label_input.selectAll()
        self.application.processEvents()
        directory = self.window.directory_input.text()
        QTest.keyClick(self.window.label_input, Qt.Key.Key_Escape)
        self.assertEqual(self.window.label_input.text(), "Preserve this label")
        self.assertEqual(self.window.directory_input.text(), directory)
        self.assertFalse(self.window.label_input.hasSelectedText())
        self.assertFalse(self.window.directory_input.hasSelectedText())
        self.assertEqual(self.window.photo_list.selectedItems(), [])
        self.assertEqual(self.window.bookmark_list.selectedItems(), [])
        self.assertIs(self.application.focusWidget(), self.window.centralWidget())
        QTest.keyClick(self.window.centralWidget(), Qt.Key.Key_Space)
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(len(self.window.session.photos), 2)

    def test_escape_cancels_inline_bookmark_editor_and_restores_space_capture(self):
        self.send_frame(width=320, height=180)
        self.activate_capture_window()
        self.window.directory_input.setText(self.temporary.name)
        self.window.bookmark_add_button.click()
        QTest.keyClicks(self.window.bookmark_name_input, "Unsaved bookmark with spaces")
        self.assertEqual(self.window.session.photos, [])
        QTest.keyClick(self.window.bookmark_name_input, Qt.Key.Key_Escape)
        self.assertIs(self.window.bookmark_stack.currentWidget(), self.window.bookmark_list)
        self.assertEqual(self.window.bookmark_list.count(), 0)
        QTest.keyClick(self.window.centralWidget(), Qt.Key.Key_Space)
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(len(self.window.session.photos), 1)

    def test_bookmarks_use_right_quarter_of_export_row_with_editor_open_or_closed(self):
        for width in (1120, 880):
            self.window.resize(width, 800)
            self.application.processEvents()
            self.assertGreater(self.window.bookmark_box.x(), self.window.export_box.x())
            total = self.window.export_box.width() + self.window.bookmark_box.width()
            self.assertAlmostEqual(self.window.export_box.width() / total, .75, delta=.015,
                                   msg=f"{width=}, export={self.window.export_box.size()}, "
                                       f"bookmarks={self.window.bookmark_box.size()}")
            self.window.directory_input.setText(self.temporary.name)
            self.window.bookmark_add_button.click()
            self.application.processEvents()
            total = self.window.export_box.width() + self.window.bookmark_box.width()
            self.assertAlmostEqual(self.window.export_box.width() / total, .75, delta=.015,
                                   msg=f"Editor open: {width=}, export={self.window.export_box.size()}, "
                                       f"bookmarks={self.window.bookmark_box.size()}")
            self.assertTrue(self.window.bookmark_save_button.isVisible())
            self.assertTrue(self.window.bookmark_cancel_button.isVisible())
            self.window.bookmark_cancel_button.click()

    def test_bookmark_selection_does_not_change_directory_during_an_operation(self):
        self.add_bookmark("Saved", self.temporary.name)
        other = Path(self.temporary.name) / "Other"
        other.mkdir()
        self.window.directory_input.setText(str(other))
        self.window.busy = True
        try:
            self.window._sync_controls()
            self.assertFalse(self.window.bookmark_list.isEnabled())
            self.assertFalse(self.window.bookmark_add_button.isEnabled())
            self.window._select_bookmark(self.window.bookmark_list.item(0))
            self.assertEqual(self.window.directory_input.text(), str(other))
        finally:
            self.window.busy = False
            self.window._sync_controls()

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

    def test_entrypoint_loads_icon_and_closes_cleanly_from_another_directory(self):
        entrypoint = Path(__file__).resolve().parents[1] / "webcam_capture.py"
        result = subprocess.run([sys.executable, str(entrypoint), "--smoke-test"],
                                cwd=self.temporary.name, capture_output=True, text=True,
                                timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def running_camera(self, features):
        camera = Mock(spec=QCamera)
        camera.supportedFeatures.return_value = features
        camera.isActive.return_value = True
        camera.minimumExposureTime.return_value = 1 / 1000
        camera.maximumExposureTime.return_value = 1
        self.window.camera = camera
        self.window._sync_controls()
        return camera

    def test_running_camera_with_manual_exposure_receives_frames_and_changes_exposure(self):
        camera = self.running_camera(QCamera.Feature.ManualExposureTime)
        self.assertTrue(self.window.exposure_input.isEnabled())
        self.send_frame(width=320, height=180)
        self.assertTrue(self.window.capture_button.isEnabled())
        self.assertEqual(self.window.actual_size, (320, 180))
        self.window.exposure_input.setCurrentIndex(2)
        camera.setManualExposureTime.assert_called_once_with(1 / 120)
        self.window.exposure_input.setCurrentIndex(0)
        camera.setAutoExposureTime.assert_called_once_with()

    def test_running_camera_without_manual_exposure_still_receives_frames(self):
        camera = self.running_camera(QCamera.Feature.ExposureCompensation)
        self.assertFalse(self.window.exposure_input.isEnabled())
        self.send_frame(width=320, height=180)
        self.assertTrue(self.window.capture_button.isEnabled())
        self.assertEqual(self.window.actual_size, (320, 180))
        self.window.exposure_input.setCurrentIndex(2)
        camera.setManualExposureTime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
