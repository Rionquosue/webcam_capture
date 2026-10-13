#!/usr/bin/env python3
"""Native Qt camera preview; keep captures uncompressed until JPEG export."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Callable

from PySide6.QtCore import QEvent, QLibraryInfo, QPluginLoader, QTimer, Qt, Slot
from PySide6.QtGui import QCloseEvent, QIcon, QImage, QPixmap, QTransform
from PySide6.QtMultimedia import QCamera, QCameraFormat, QMediaCaptureSession, QMediaDevices, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)
from capture_core import CaptureSet, CapturedPhoto, MAX_PHOTOS, export_photos, safe_label


def configure_backend(requested: str = "auto") -> str:
    """Use Windows Media Foundation when the installed Qt build supplies it."""
    if requested != "auto":
        os.environ["QT_MEDIA_BACKEND"] = requested
    elif "QT_MEDIA_BACKEND" not in os.environ and sys.platform == "win32":
        plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "multimedia"
        for path in plugins.glob("*.dll"):
            keys = QPluginLoader(str(path)).metaData().get("MetaData", {}).get("Keys", [])
            if "windows" in keys:
                os.environ["QT_MEDIA_BACKEND"] = "windows"
                break
    return os.environ.get("QT_MEDIA_BACKEND", "Qt default")


def preferred_format(formats: list[QCameraFormat]) -> int:
    if not formats:
        return -1
    def score(index: int) -> tuple:
        mode = formats[index]
        size = mode.resolution()
        full_hd = (size.width(), size.height()) == (1920, 1080)
        fps = mode.maxFrameRate()
        return (full_hd, fps >= 45, -abs(fps - 60),
                -abs(size.width() * size.height() - 1920 * 1080), fps)
    return max(range(len(formats)), key=score)


def format_name(mode: QCameraFormat) -> str:
    size = mode.resolution()
    pixel_format = mode.pixelFormat().name.removeprefix("Format_")
    return f"{size.width()} x {size.height()} | up to {mode.maxFrameRate():g} FPS | {pixel_format}"


def frame_image(frame: QVideoFrame) -> QImage:
    """Snapshot pixels with the frame's presentation rotation and mirroring."""
    image = frame.toImage()
    if image.isNull():
        raise ValueError("The camera frame could not be converted to an image.")
    surface = frame.surfaceFormat()
    for rotation, mirrored in ((surface.rotation().value, surface.isMirrored()),
                               (frame.rotation().value, frame.mirrored())):
        if rotation:
            image = image.transformed(QTransform().rotate(rotation))
        if mirrored:
            image = image.flipped(Qt.Orientation.Horizontal)
    return image.copy()


def capture_from_image(source: QImage) -> CapturedPhoto:
    """Remove scanline padding before treating native storage as packed RGB."""
    image = source.convertToFormat(QImage.Format.Format_RGB888)
    width, height = image.width(), image.height()
    if image.isNull():
        raise ValueError("Cannot store an empty camera image.")
    stride, row_bytes = image.bytesPerLine(), width * 3
    data = image.constBits().tobytes()
    rgb = data if stride == row_bytes else b"".join(
        data[offset:offset + row_bytes] for offset in range(0, stride * height, stride))
    return CapturedPhoto(rgb, width, height)


def thumbnail_pixmap(photo: CapturedPhoto) -> QPixmap:
    image = photo.thumbnail
    native = QImage(image.tobytes(), image.width, image.height, image.width * 3,
                    QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(native)


class WebcamApp(QMainWindow):
    def __init__(self, *, devices=None, backend_name: str = "Qt default") -> None:
        super().__init__()
        self.session = CaptureSet()
        self.camera: QCamera | None = None
        self.formats: list[QCameraFormat] = []
        self.backend_name = backend_name
        self.latest_frame = QVideoFrame()
        self.last_frame_at = 0.0
        self.frame_count = 0
        self.received_fps = 0.0
        self.rate_started = time.monotonic()
        self.actual_size = (0, 0)
        self.busy = False
        self.results: queue.Queue = queue.Queue()
        self._status_signature = None
        self._build_ui()
        self.media_devices = QMediaDevices(self)
        self.media_devices.videoInputsChanged.connect(self.refresh_devices)
        self.media_session = QMediaCaptureSession(self)
        self.media_session.setVideoOutput(self.video)
        self.video_sink = self.video.videoSink()
        self.video_sink.videoFrameChanged.connect(self._frame_received)
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(40)
        self._drain_timer.timeout.connect(self._drain_results)
        self._drain_timer.start()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start()
        self.refresh_devices(devices)

    def _build_ui(self) -> None:
        self.setWindowTitle("Webcam Capture - Native Video")
        self.resize(1120, 800)
        self.setMinimumSize(880, 680)
        center = QWidget(self)
        self.setCentralWidget(center)
        layout = QVBoxLayout(center)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("Webcam Capture")
        font = title.font()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        layout.addWidget(QLabel("Native preview. Keep up to 15 full-resolution captures, then export."))
        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel("Camera"))
        self.device_input = QComboBox()
        self.device_input.setMinimumWidth(230)
        self.device_input.currentIndexChanged.connect(self._device_changed)
        camera_row.addWidget(self.device_input, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self.refresh_devices())
        camera_row.addWidget(self.refresh_button)
        self.camera_button = QPushButton("Open camera")
        self.camera_button.clicked.connect(self.toggle_camera)
        camera_row.addWidget(self.camera_button)
        layout.addLayout(camera_row)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Video mode"))
        self.mode_input = QComboBox()
        mode_row.addWidget(self.mode_input, 1)
        mode_row.addWidget(QLabel("Exposure"))
        self.exposure_input = QComboBox()
        for label, value in (("Camera auto", -1), ("1/60 s", 1 / 60),
                             ("1/120 s", 1 / 120), ("1/240 s", 1 / 240)):
            self.exposure_input.addItem(label, value)
        self.exposure_input.currentIndexChanged.connect(self._change_exposure)
        self.exposure_input.setEnabled(False)
        mode_row.addWidget(self.exposure_input)
        layout.addLayout(mode_row)
        self.camera_status = QLabel("Camera closed.")
        self.camera_status.setWordWrap(True)
        layout.addWidget(self.camera_status)
        self.mode_status = QLabel()
        self.mode_status.setWordWrap(True)
        self.mode_input.currentIndexChanged.connect(self._mode_changed)
        layout.addWidget(self.mode_status)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.video = QVideoWidget()
        self.video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.video.setMinimumSize(320, 180)
        self.video.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        split.addWidget(self.video)
        side = QWidget()
        side.setMinimumWidth(280)
        sidebar = QVBoxLayout(side)
        sidebar.setContentsMargins(12, 0, 0, 0)
        self.count = QLabel("0 / 15 photos")
        sidebar.addWidget(self.count)
        self.photo_list = QListWidget()
        self.photo_list.currentRowChanged.connect(self._review_selected)
        sidebar.addWidget(self.photo_list, 1)
        self.review = QLabel("Select a captured photo")
        self.review.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.review.setFixedHeight(150)
        sidebar.addWidget(self.review)
        action_row = QHBoxLayout()
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected)
        action_row.addWidget(self.remove_button)
        self.clear_button = QPushButton("Clear set")
        self.clear_button.clicked.connect(self.clear_set)
        action_row.addWidget(self.clear_button)
        sidebar.addLayout(action_row)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([760, 290])
        layout.addWidget(split, 1)
        capture_row = QHBoxLayout()
        self.capture_button = QPushButton("Capture photo")
        self.capture_button.clicked.connect(self.capture)
        capture_row.addWidget(self.capture_button)
        capture_row.addWidget(QLabel("Captures stay uncompressed. JPEG encoding runs only at export."))
        capture_row.addStretch()
        layout.addLayout(capture_row)
        export_box = QGroupBox("Export image set")
        form = QFormLayout(export_box)
        self.label_input = QLineEdit()
        form.addRow("Set label", self.label_input)
        directory_row = QHBoxLayout()
        self.directory_input = QLineEdit()
        directory_row.addWidget(self.directory_input)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse)
        directory_row.addWidget(self.browse_button)
        form.addRow("Directory", directory_row)
        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("New labeled folder. Each exported JPEG is under 2 MB."))
        export_row.addStretch()
        self.export_button = QPushButton("Export set")
        self.export_button.clicked.connect(self.export)
        export_row.addWidget(self.export_button)
        form.addRow(export_row)
        layout.addWidget(export_box)
        self.status = QLabel("Select your camera and a supported mode, then open it.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._sync_controls()

    def refresh_devices(self, devices=None) -> None:
        previous = self.device_input.currentData()
        previous_id = bytes(previous.id()) if previous is not None else None
        available = list(QMediaDevices.videoInputs() if devices is None else devices)
        if self.camera is not None:
            if any(bytes(device.id()) == bytes(self.camera.cameraDevice().id()) for device in available):
                return
            self.stop_camera()
        self.device_input.blockSignals(True)
        self.device_input.clear()
        for device in available:
            self.device_input.addItem(device.description(), device)
        selected = 0
        for index, device in enumerate(available):
            identity = bytes(device.id()).lower()
            if b"vid_1bcf" in identity and b"pid_0050" in identity:
                selected = index
                break
            if identity == previous_id or device.isDefault():
                selected = index
        self.device_input.setCurrentIndex(selected if available else -1)
        self.device_input.blockSignals(False)
        self._device_changed()
        if not available:
            self.status.setText("No cameras found. Connect the webcam, close other camera apps, then Refresh.")
        self._sync_controls()

    def _device_changed(self, index=None) -> None:
        self.mode_input.blockSignals(True)
        self.mode_input.clear()
        device = self.device_input.currentData()
        self.formats = list(device.videoFormats()) if device is not None else []
        for mode in self.formats:
            self.mode_input.addItem(format_name(mode))
        self.mode_input.addItem("Camera default")
        preferred = preferred_format(self.formats)
        self.mode_input.setCurrentIndex(preferred if preferred >= 0 else len(self.formats))
        self.mode_input.blockSignals(False)
        self._mode_changed()
        self._sync_controls()

    def _mode_changed(self, index=None) -> None:
        index = self.mode_input.currentIndex()
        if 0 <= index < len(self.formats):
            mode = self.formats[index]
            size = mode.resolution()
            if (size.width(), size.height()) != (1920, 1080):
                message = "Selected mode is not 1080p. Choose an advertised 1920 x 1080 mode if available."
            elif mode.maxFrameRate() < 30:
                message = "This advertised 1080p mode cannot meet the 30 FPS minimum."
            elif mode.maxFrameRate() < 45:
                message = "This 1080p mode supports 30 FPS, but its advertised maximum is below 45 FPS."
            else:
                message = "1080p at 45+ FPS is advertised. Actual delivery depends on the camera and driver."
        else:
            message = "Camera default mode. Actual dimensions and delivery FPS appear while running."
        self.mode_status.setText(message)

    def toggle_camera(self) -> None:
        if self.camera is not None:
            self.stop_camera()
            return
        device = self.device_input.currentData()
        if device is None:
            self.status.setText("No camera is selected. Connect it and Refresh.")
            return
        self.latest_frame = QVideoFrame()
        self.last_frame_at = 0
        self.received_fps = 0
        self.frame_count = 0
        self.rate_started = time.monotonic()
        self.camera = QCamera(device, self)
        self.camera.errorOccurred.connect(self._camera_error)
        self.camera.activeChanged.connect(self._sync_controls)
        self.camera.supportedFeaturesChanged.connect(self._sync_controls)
        self.media_session.setCamera(self.camera)
        index = self.mode_input.currentIndex()
        if 0 <= index < len(self.formats):
            self.camera.setCameraFormat(self.formats[index])
        self.exposure_input.blockSignals(True)
        self.exposure_input.setCurrentIndex(0)
        self.exposure_input.blockSignals(False)
        self.status.setText("Opening camera using native video...")
        self.camera.start()
        self._sync_controls()
        QTimer.singleShot(6000, self._check_startup)

    def _check_startup(self) -> None:
        if self.camera is not None and self.last_frame_at == 0:
            self.status.setText("No video frames arrived. Close other camera apps and check permissions. "
                                "On Windows, also try launching with --backend ffmpeg.")

    def stop_camera(self) -> None:
        if self.camera is not None:
            old = self.camera
            self.camera = None
            old.stop()
            self.media_session.setCamera(None)
            old.deleteLater()
        self.latest_frame = QVideoFrame()
        self.last_frame_at = 0
        self.actual_size = (0, 0)
        self.received_fps = 0
        self.video_sink.setVideoFrame(QVideoFrame())
        self.camera_status.setText("Camera closed.")
        self._sync_controls()

    def _camera_error(self, error, message: str) -> None:
        self.status.setText(f"Camera error: {message}")
        self.stop_camera()

    @Slot(QVideoFrame)
    def _frame_received(self, frame: QVideoFrame) -> None:
        # Qt renders the frame directly; Python only holds a shared handle.
        if not frame.isValid():
            self.latest_frame = QVideoFrame()
            self.last_frame_at = 0
            self._sync_controls()
            return
        if self.camera is not None and not self.camera.isActive():
            return
        self.latest_frame = QVideoFrame(frame)
        self.last_frame_at = time.monotonic()
        self.actual_size = (frame.width(), frame.height())
        self.frame_count += 1
        if not self.capture_button.isEnabled():
            self._sync_controls()

    def _update_stats(self) -> None:
        now = time.monotonic()
        elapsed = now - self.rate_started
        self.received_fps = self.frame_count / elapsed if elapsed > 0 else 0
        self.frame_count = 0
        self.rate_started = now
        if self.last_frame_at:
            width, height = self.actual_size
            self.camera_status.setText(
                f"{width} x {height} | {self.received_fps:.1f} frames received/s | {self.backend_name}")
        self._sync_controls()

    def _change_exposure(self, index=None) -> None:
        if self.camera is None or not self.exposure_input.isEnabled():
            return
        seconds = self.exposure_input.currentData()
        if seconds < 0:
            self.camera.setAutoExposureTime()
            return
        minimum, maximum = self.camera.minimumExposureTime(), self.camera.maximumExposureTime()
        if (minimum > 0 and seconds < minimum) or (maximum > 0 and seconds > maximum):
            self.status.setText("That exposure time is outside the camera's reported range.")
            return
        self.camera.setManualExposureTime(seconds)
        self.status.setText(f"Requested exposure 1/{round(1 / seconds)} s. Check image brightness.")

    def _sync_controls(self, *args) -> None:
        opened = self.camera is not None
        fresh = self.latest_frame.isValid() and time.monotonic() - self.last_frame_at <= 2
        manual = bool(opened and self.camera.supportedFeatures() & QCamera.Feature.ManualExposureTime)
        signature = (opened, fresh, self.busy, manual,
                     len(self.session.photos), self.photo_list.currentRow(), self.device_input.count())
        if signature == self._status_signature:
            return
        self._status_signature = signature
        self.camera_button.setText("Close camera" if opened else "Open camera")
        self.camera_button.setEnabled(opened or self.device_input.count() > 0)
        self.device_input.setEnabled(not opened)
        self.mode_input.setEnabled(not opened)
        self.refresh_button.setEnabled(not opened)
        self.exposure_input.setEnabled(manual)
        self.capture_button.setEnabled(fresh and not self.busy and len(self.session.photos) < MAX_PHOTOS)
        self.export_button.setEnabled(bool(self.session.photos) and not self.busy)
        self.remove_button.setEnabled(self.photo_list.currentRow() >= 0 and not self.busy)
        self.clear_button.setEnabled(bool(self.session.photos) and not self.busy)
        for widget in (self.label_input, self.directory_input, self.browse_button):
            widget.setEnabled(not self.busy)

    def _submit(self, kind: str, operation: Callable) -> None:
        self.busy = True
        self._sync_controls()
        def run() -> None:
            try:
                self.results.put((kind, operation(), None))
            except Exception as error:
                self.results.put((kind, None, str(error)))
        threading.Thread(target=run, daemon=True, name=kind).start()

    def capture(self) -> None:
        if self.busy or len(self.session.photos) >= MAX_PHOTOS:
            return
        if not self.latest_frame.isValid() or time.monotonic() - self.last_frame_at > 2:
            self.status.setText("Wait for a current camera frame before capturing.")
            return
        try:
            owned_image = frame_image(self.latest_frame)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.status.setText("Storing full-resolution capture...")
        self._submit("capture", lambda: capture_from_image(owned_image))

    def _refresh_photos(self) -> None:
        self.photo_list.clear()
        for index, photo in enumerate(self.session.photos, 1):
            self.photo_list.addItem(f"{index:02d}   {photo.width} x {photo.height}"
                                    f"   {len(photo.rgb) / 1_000_000:.1f} MB")
        self.count.setText(f"{len(self.session.photos)} / 15 photos")
        self.review.clear()
        self.review.setText("Select a captured photo")
        if self.session.photos:
            self.photo_list.setCurrentRow(len(self.session.photos) - 1)
        self._sync_controls()

    def _review_selected(self, row=None) -> None:
        row = self.photo_list.currentRow()
        if 0 <= row < len(self.session.photos):
            self.review.setPixmap(thumbnail_pixmap(self.session.photos[row]))
        self._sync_controls()

    def remove_selected(self) -> None:
        row = self.photo_list.currentRow()
        if not self.busy and row >= 0:
            self.session.remove(row)
            self._refresh_photos()
            self.status.setText("Photo removed. Capture a replacement if needed.")

    def _confirm_discard(self, title: str) -> bool:
        return QMessageBox.question(self, title, "Discard unexported captures?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def clear_set(self) -> None:
        if self.busy or not self.session.photos:
            return
        if self.session.dirty and not self._confirm_discard("Clear set"):
            return
        self.session.clear()
        self._refresh_photos()
        self.status.setText("Set cleared. Ready for new photos.")

    def browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose export directory")
        if directory:
            self.directory_input.setText(directory)

    def export(self) -> None:
        if self.busy or not self.session.photos:
            return
        try:
            label = safe_label(self.label_input.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        if not self.directory_input.text().strip():
            self.browse()
        if not self.directory_input.text().strip():
            return
        destination = Path(self.directory_input.text().strip())
        photos = tuple(self.session.photos)
        progress = lambda current, total: self.results.put(("progress", (current, total), None))
        self.status.setText("Compressing and exporting photos...")
        self._submit("export", lambda: export_photos(destination, label, photos, progress))

    def _drain_results(self) -> None:
        try:
            while True:
                kind, result, error = self.results.get_nowait()
                if kind == "progress":
                    self.status.setText(f"Compressing and exporting photo {result[0]} of {result[1]}...")
                    continue
                self.busy = False
                if error is not None:
                    self.status.setText(f"{kind.capitalize()} failed: {error}. Original captures remain in memory.")
                elif kind == "capture":
                    self.session.add(result)
                    self._refresh_photos()
                    self.status.setText("Set complete. Label it and export." if len(self.session.photos) == 15
                                        else "Full-resolution photo captured without JPEG compression.")
                else:
                    self.session.mark_exported()
                    self.status.setText(f"Exported {len(self.session.photos)} photos to {result}")
                self._sync_controls()
        except queue.Empty:
            pass

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.busy:
            self.status.setText("Wait for the current operation before closing.")
            event.ignore()
            return
        if self.session.dirty and not self._confirm_discard("Unexported captures"):
            event.ignore()
            return
        self._drain_timer.stop()
        self._stats_timer.stop()
        self.stop_camera()
        self.session.clear()
        event.accept()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("auto", "windows", "ffmpeg"), default="auto",
                        help="Auto prefers Windows Media Foundation when installed.")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    backend = configure_backend(arguments.backend)
    if sys.platform == "win32":
        import ctypes
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id("WebcamCapture.Desktop")
    application = QApplication([sys.argv[0]])
    application.setApplicationName("Webcam Capture")
    application.setDesktopFileName("webcam_capture")
    icon = QIcon(str(Path(__file__).resolve().parent / "assets" / "webcam_capture.ico"))
    if arguments.smoke_test and icon.isNull():
        raise RuntimeError("The bundled application icon is missing or unreadable.")
    application.setWindowIcon(icon)
    window = WebcamApp(backend_name=backend)
    window.show()
    if arguments.smoke_test:
        QTimer.singleShot(250, window.close)
    result = application.exec()
    # Release multimedia widgets while QApplication and its backend still exist.
    window.deleteLater()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    raise SystemExit(result)


if __name__ == "__main__":
    main()
