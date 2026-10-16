# Webcam Capture

Python desktop app for Windows 11 and Linux. Display a native camera preview,
keep up to 15 full-resolution photos in memory, label the set, and export JPEGs
that are each strictly below 2,000,000 bytes. Requires Python 3.10 or newer and
a graphical desktop session.

The preview uses Qt Multimedia through PySide6. Camera frames go directly to
Qt's native video widget. Python keeps a shared frame handle for snapshots;
it does not resize, convert, or JPEG-encode every preview frame. There is no
software 30 FPS cap. This replaces the earlier Tkinter/OpenCV implementation.

## Packaged downloads

Get the packages from [Releases](https://github.com/Rionquosue/webcam_capture/releases/latest).
They include Python and the required Python libraries.

**Windows 11, Intel/AMD 64-bit:** extract `WebcamCapture-Windows-x64.zip` into
a permanent folder, then double-click `Create-Desktop-Shortcut.cmd`. Launch
the **Webcam Capture** desktop shortcut, or open `WebcamCapture.exe` directly.
Keep the `_internal` folder beside the executable. The custom icon is embedded
in the executable and assigned to the shortcut. No administrator access is
needed. Run the shortcut installer again if you move the app folder.

**Linux, Intel/AMD 64-bit:** the AppImage and portable TAR.GZ are built on
Ubuntu 22.04 for desktop systems with glibc 2.35 or newer.

```bash
chmod +x WebcamCapture-Linux-x86_64.AppImage
./WebcamCapture-Linux-x86_64.AppImage
```

The portable TAR.GZ also provides a desktop shortcut installer: extract it
into a permanent folder, then run `./create-desktop-shortcut.sh` inside the
`WebcamCapture` folder. Launch `AppRun` or the desktop shortcut. This package
works without FUSE. The AppImage can also run without FUSE using
`--appimage-extract-and-run`. Graphics drivers, a desktop session, and webcam
device access are supplied by the laptop.

The same camera and export features are available in both packages. Camera
compatibility and received frame rate still depend on each laptop's driver.

The icon was created using image generation; its source and Windows ICO are
in `assets/`. The icon prompt is saved in `assets/icon_prompt.txt`.

## Windows 11 setup

Open PowerShell in this repository's folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe webcam_capture.py
```

For an existing virtual environment, run the last two commands to install the
new dependencies and launch. OpenCV and Tkinter are no longer required.
Close Windows Camera or other applications using the webcam before opening it
here. Allow desktop apps to access the camera in Windows Settings if needed.

On Windows, automatic backend selection prefers Windows Media Foundation when
that plugin is supplied by the installed Qt build. Otherwise Qt selects its
default backend. If the camera does not start or the preview behaves poorly,
close the app and compare the FFmpeg backend:

```powershell
.\.venv\Scripts\python.exe webcam_capture.py --backend ffmpeg
```

`--backend windows` explicitly selects the Windows plugin, when installed.
`--backend auto` also respects an existing `QT_MEDIA_BACKEND` environment
variable. The active backend selection is shown beside the stream statistics.

## Linux setup

Use a desktop session with access to the webcam's `/dev/video*` device.
For Debian/Ubuntu, install Python virtual environment support and Qt's X11
cursor dependency if they are missing:

```bash
sudo apt install python3-venv libxcb-cursor0
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python webcam_capture.py
```

Other distributions need the equivalent Qt desktop runtime dependencies.
Qt chooses the multimedia backend on Linux; `--backend ffmpeg` is available
for an explicit selection. The Windows backend is specific to Windows.

## Use

1. Select the camera by name and a **Video mode**, then **Open camera**.
   Modes come from the camera's advertised formats. The app prefers an
   advertised 1920 x 1080 mode near 60 FPS when available. Close the camera
   before changing its mode. **Camera default** is also available.
2. Click **Capture photo** or press **Space**, up to 15 times. Space capture
   works only while this app window is active; it does not run while hidden,
   minimized, or behind another app. Typing spaces in the label and directory
   fields still works normally. Select a photo to review it;
   **Remove selected** lets you replace a photo.
3. Enter a **Set label**, choose an existing export **Directory**, and click
   **Export set**. JPEG encoding and any required size reduction happen now.
4. After a successful export, the saved image folder opens and the captured
   photo list is cleared from memory, ready for the next set. Export failure
   keeps the photos for retry.

The last valid export directory is remembered between launches. Both Browse
and typed directory paths are supported. The selected parent directory is
remembered, rather than the newly created image-set subfolder.

The app prefers the supplied webcam's USB vendor/product pair `1BCF:0050`
when the backend exposes it in the device ID. The hardware ID does not specify
the camera's supported resolutions or frame rates; these are read at runtime.

Example output:

```text
chosen-directory/
  Sample_20261013_130000/
    Sample_01.jpg
    Sample_02.jpg
```

## Preview performance and motion

The status reports actual received frame dimensions and **frames received/s**.
Allow a few seconds for the rate to settle. This measures frame delivery to
the app, not display refresh or unique sensor exposures. The requested mode
lists the driver's advertised maximum frame rate; it is not a guarantee of
actual delivery. If only 1080p/30 is advertised, the app cannot request
1080p/45 or 60 from that camera.

The 30–45 FPS at 1080p target must be checked on the physical Windows webcam.
Automated checks use synthetic native frames and cannot establish that the
target rate or motion quality is achieved on this device. Windows Camera's
good preview is a useful reference when comparing the two backends and modes.

The **Exposure** control is enabled only when the backend reports manual
exposure support. Shorter exposures may reduce motion blur if lighting is
sufficient. Exposure changes do not guarantee removal of sensor rolling
shutter skew. The app does not apply artificial motion correction or change
the camera's exposure automatically when opening it.

## Capture and export behavior

- Captures retain the complete active stream resolution as immutable RGB
  bytes. Small review thumbnails are separate; their resizing does not change
  the original pixels. Capture does not perform JPEG encoding or downscaling.
- Fifteen 1920 x 1080 RGB captures use about 93 MB, plus camera, preview,
  thumbnail, and export working memory. Photos stay in RAM until successful
  export, manual clearing, or closing the app; RAM storage does not survive
  a crash.
- Compression runs only during export. Images above a 2560-pixel longest edge
  are reduced with aspect ratio preserved. JPEG quality decreases from 95
  toward 60; dimensions decrease further if necessary. The actual encoded
  byte count must be **strictly below 2,000,000 bytes** for every file.
- Successful export opens the saved folder and releases the original captures
  and review thumbnails. The label and selected directory stay in place for
  the next set. If opening the folder fails, the exported files remain saved
  and the status shows their location. Export failure retains the uncompressed
  originals for retry. Camera-side processing or compressed transport, if used
  by the camera, has already occurred before the app receives the frame.
- Frame rotation and mirroring metadata are applied to snapshots so they
  match native presentation. Native row padding is removed when storing RGB
  pixels, preventing incorrectly packed rows in captured images.
- Each export creates a new labeled timestamp folder. Collisions receive a
  numerical suffix, and files are created exclusively. Existing files are
  never overwritten. Labels are made portable across Windows and Linux.
- Export failure retains the set for retry and attempts to remove the
  incomplete output folder. Clearing or closing an unexported set prompts
  before discarding photos. Closing during an active capture/export waits
  for that operation to finish.
- Snapshot storage and JPEG export run in worker threads. Qt drives the live
  preview independently; snapshot readback occurs only when Capture is used.

## Development checks

Install development dependencies and run the tests:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Tests use Qt's offscreen platform by default, so a physical camera is not
required. They cover native frame handling without Python pixel conversion
during preview, orientation and scanline padding, full-resolution raw capture,
the 15-photo limit, export-only encoding, JPEG size, portable labels, repeat
exports, collision handling, and failure recovery. Use `QT_QPA_PLATFORM=xcb`
or the platform's normal desktop plugin to run in a desktop session.

`webcam_capture.py` contains the Qt interface and native camera integration.
`capture_core.py` contains capture storage, labeling, and JPEG export.

## Building packages

The **Build desktop packages** GitHub Actions workflow builds on native
Windows and Ubuntu runners, runs the tests, verifies bundled startup and
shortcut creation, and publishes the successful packages as one release.
It runs on pushes to `main` and can be started manually.

For a local Windows build, from the source folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Python .\.venv\Scripts\python.exe
```

For a local Linux build, install `requirements-build.txt` in your Python
environment and run `bash packaging/build_linux.sh`. The CI workflow lists
the desktop library prerequisites. Packages are written to `dist/`.

Implementation references:

- [Qt Multimedia backends](https://doc.qt.io/qt-6/qtmultimedia-index.html)
- [QCamera](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QCamera.html)
- [QVideoWidget](https://doc.qt.io/qtforpython-6/PySide6/QtMultimediaWidgets/QVideoWidget.html)
- [QVideoFrame](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QVideoFrame.html)
- [Pillow JPEG options](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#jpeg)
