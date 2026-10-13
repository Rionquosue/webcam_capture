#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v xdg-user-dir >/dev/null 2>&1; then
    desktop_dir=$(xdg-user-dir DESKTOP)
else
    desktop_dir="${HOME}/Desktop"
fi
[ -n "$desktop_dir" ] || { printf '%s\n' 'Your desktop directory could not be found.' >&2; exit 1; }
mkdir -p "$desktop_dir"
exec_path=$(printf '%s' "$app_dir/AppRun" | sed 's/\\/\\\\/g; s/"/\\"/g; s/`/\\`/g; s/\$/\\$/g; s/%/%%/g')
icon_path=$(printf '%s' "$app_dir/webcam_capture.png" | sed 's/\\/\\\\/g')
shortcut="$desktop_dir/webcam_capture.desktop"
printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=Webcam Capture' \
    'Comment=Capture photos and export a labeled image set' "Exec=\"$exec_path\"" \
    "Icon=$icon_path" 'Terminal=false' 'Categories=Graphics;Photography;' > "$shortcut"
chmod +x "$shortcut"
if command -v gio >/dev/null 2>&1; then
    gio set "$shortcut" metadata::trusted true >/dev/null 2>&1 || :
fi
printf '%s\n' "Created $shortcut"
