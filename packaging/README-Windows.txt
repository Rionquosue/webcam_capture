Webcam Capture for Windows 11 (64-bit Intel/AMD)

1. Extract the entire ZIP into a permanent folder.
2. Double-click Create-Desktop-Shortcut.cmd once.
3. Open Webcam Capture from the desktop shortcut.

You can also launch WebcamCapture.exe directly. Keep the _internal folder
beside it. No Python installation or administrator access is required.
Moving the app folder requires running the shortcut installer again.

Close other camera applications before opening the camera. Select the camera
and its supported video mode, then Open camera. Capture up to 15 images,
enter a set label, select an export directory, and Export set.

Captures stay at full stream resolution in memory. JPEG compression and size
reduction happen only during export. Each JPEG is below 2,000,000 bytes.

If the camera does not open with automatic backend selection, launch:
WebcamCapture.exe --backend ffmpeg
