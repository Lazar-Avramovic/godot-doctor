#!/bin/sh
# Debian postinst: expose the CLI (and the uninstaller) on PATH.
# The CLI is registered through alternatives so an existing godot-doctor
# alternative (for example a GUI binary) is restored automatically on removal.
set -e

APP_DIR="/opt/Godot Doctor"
CLI="$APP_DIR/resources/backend/godot-doctor-backend"

if [ -x "$CLI" ]; then
  update-alternatives --install /usr/bin/godot-doctor godot-doctor "$CLI" 200
  update-alternatives --set godot-doctor "$CLI"
fi

if [ -f "$APP_DIR/uninstall.sh" ]; then
  chmod 755 "$APP_DIR/uninstall.sh" || true
  ln -sfn "$APP_DIR/uninstall.sh" /usr/bin/godot-doctor-uninstall
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi

exit 0
