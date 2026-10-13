Webcam Capture for Linux (64-bit Intel/AMD)

Built on Ubuntu 22.04 for desktop Linux systems with glibc 2.35 or newer.
Requires a graphical desktop, graphics drivers, and access to the webcam.
Python is included in the package.

Portable TAR.GZ package:
1. Extract the whole archive into a permanent folder.
2. Open AppRun, or run ./AppRun from that folder.
3. To create a desktop shortcut, run ./create-desktop-shortcut.sh once.
   Moving the folder requires creating the shortcut again.

AppImage package:
chmod +x WebcamCapture-Linux-x86_64.AppImage
./WebcamCapture-Linux-x86_64.AppImage

If FUSE is unavailable, use the portable TAR.GZ package, or launch:
./WebcamCapture-Linux-x86_64.AppImage --appimage-extract-and-run

Close other camera applications. Select a camera and a supported video mode,
then Open camera. Capture up to 15 images, enter a label, choose an export
directory, and Export set. Compression is applied only during export.
