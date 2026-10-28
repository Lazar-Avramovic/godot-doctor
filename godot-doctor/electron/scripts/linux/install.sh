#!/bin/sh
# Install Godot Doctor for the current user from the tar.gz build.
#   ./install.sh
#   PREFIX=/opt/godot-doctor-local ./install.sh
# Installs the app under <prefix>/opt/godot-doctor, the CLI in <prefix>/bin,
# and a desktop entry plus icon under <prefix>/share. No root required.
set -eu

src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
prefix=${PREFIX:-"$HOME/.local"}
app_dir="$prefix/opt/godot-doctor"
bin_dir="$prefix/bin"
desktop_dir="$prefix/share/applications"
icon_dir="$prefix/share/icons/hicolor/256x256/apps"

if [ ! -x "$src/godot-doctor" ] || [ ! -x "$src/resources/backend/godot-doctor-backend" ]; then
  printf '%s\n' "Run install.sh from the extracted Godot Doctor folder." >&2
  exit 1
fi
if [ "$src" = "$app_dir" ]; then
  printf 'Godot Doctor is already installed at %s\n' "$app_dir" >&2
  exit 1
fi

mkdir -p "$prefix/opt" "$bin_dir" "$desktop_dir" "$icon_dir"
rm -rf "$app_dir"
cp -a "$src" "$app_dir"
chmod 755 "$app_dir/godot-doctor" "$app_dir/install.sh" "$app_dir/uninstall.sh" \
  "$app_dir/resources/backend/godot-doctor-backend"

ln -sfn "$app_dir/resources/backend/godot-doctor-backend" "$bin_dir/godot-doctor"
cp -f "$app_dir/resources/icon.png" "$icon_dir/godot-doctor.png"

cat > "$desktop_dir/godot-doctor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Godot Doctor
GenericName=Godot Project Analyzer
Comment=Local, read-only static analysis for Godot projects
Exec="$app_dir/godot-doctor" %U
Icon=godot-doctor
Terminal=false
Categories=Development;Utility;
StartupNotify=true
EOF
chmod 644 "$desktop_dir/godot-doctor.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q "$desktop_dir" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t "$prefix/share/icons/hicolor" || true
fi

printf 'Installed Godot Doctor in %s\n' "$app_dir"
printf '  CLI:       %s/godot-doctor\n' "$bin_dir"
printf '  Desktop:   %s/godot-doctor.desktop\n' "$desktop_dir"
printf '  Uninstall: %s/uninstall.sh [--purge]\n' "$app_dir"
