#!/bin/sh
# Debian postrm: unregister the CLI alternative and remove the uninstaller link.
# On upgrade the new postinst registers them again, so leave them alone there.
set -e

APP_DIR="/opt/Godot Doctor"

case "$1" in
  remove|purge)
    update-alternatives --remove godot-doctor \
      "$APP_DIR/resources/backend/godot-doctor-backend" >/dev/null 2>&1 || true
    # Also drop any alternative pointing into the removed app directory,
    # otherwise /usr/bin/godot-doctor would dangle after uninstall.
    update-alternatives --remove godot-doctor \
      "$APP_DIR/godot-doctor" >/dev/null 2>&1 || true
    rm -f /usr/bin/godot-doctor-uninstall
    if command -v update-desktop-database >/dev/null 2>&1; then
      update-desktop-database -q /usr/share/applications || true
    fi
    ;;
esac

exit 0
