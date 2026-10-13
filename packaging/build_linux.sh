#!/bin/bash
set -euo pipefail
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
python -m PyInstaller --noconfirm --clean webcam_capture.spec
app_dir="$project_dir/dist/AppDir"
rm -rf "$app_dir"
mkdir -p "$app_dir/usr/bin" "$app_dir/usr/share/applications" "$app_dir/usr/share/icons/hicolor/256x256/apps"
cp -a dist/WebcamCapture/. "$app_dir/usr/bin/"
cp packaging/AppRun "$app_dir/AppRun"
chmod +x "$app_dir/AppRun"
cp packaging/webcam_capture.desktop "$app_dir/usr/share/applications/"
python - "$app_dir" <<'PY'
from pathlib import Path
from PIL import Image
import sys
root = Path(sys.argv[1])
with Image.open('assets/webcam_capture.ico') as image:
    image.save(root / 'usr/share/icons/hicolor/256x256/apps/webcam_capture.png')
PY
deploy_tool="$project_dir/build/linuxdeploy-x86_64.AppImage"
mkdir -p build
curl --fail --location --retry 3 \
    https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage \
    --output "$deploy_tool"
chmod +x "$deploy_tool"
export APPIMAGE_EXTRACT_AND_RUN=1
export OUTPUT="$project_dir/dist/WebcamCapture-Linux-x86_64.AppImage"
export ARCH=x86_64
"$deploy_tool" --appdir "$app_dir" --output appimage
test -f "$OUTPUT"
QT_QPA_PLATFORM=offscreen "$OUTPUT" --appimage-extract-and-run --smoke-test
QT_QPA_PLATFORM=offscreen "$app_dir/AppRun" --smoke-test
portable_dir="$project_dir/dist/linux-portable/WebcamCapture"
mkdir -p "$portable_dir"
cp -a "$app_dir/." "$portable_dir/"
cp packaging/create-desktop-shortcut.sh "$portable_dir/"
cp packaging/README-Linux.txt "$portable_dir/README.txt"
cp "$app_dir/usr/share/icons/hicolor/256x256/apps/webcam_capture.png" "$portable_dir/webcam_capture.png"
chmod +x "$portable_dir/create-desktop-shortcut.sh"
tar -C dist/linux-portable -czf dist/WebcamCapture-Linux-x86_64.tar.gz WebcamCapture
printf '%s\n' 'Built Linux AppImage and portable TAR.GZ packages.'
