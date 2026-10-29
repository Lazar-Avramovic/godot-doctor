#!/bin/sh
# Remove Godot Doctor.
#   deb install:    sudo godot-doctor-uninstall [--purge]
#   tar.gz install: ~/.local/opt/godot-doctor/uninstall.sh [--purge]
# Settings live in ~/.config/Godot Doctor and survive unless --purge is given.
set -eu

purge=0
for arg in "$@"; do
  case "$arg" in
    --purge) purge=1 ;;
    -h|--help)
      printf '%s\n' "Usage: $0 [--purge]" \
        "Removes Godot Doctor. --purge also deletes local settings and caches."
      exit 0 ;;
    *)
      printf 'Unknown option: %s\n' "$arg" >&2
      exit 2 ;;
  esac
done

self=$(readlink -f -- "$0" 2>/dev/null || printf '%s' "$0")
app_dir=$(CDPATH= cd -- "$(dirname -- "$self")" && pwd -P)

stop_app() {
  pkill -f -- "$app_dir/godot-doctor" 2>/dev/null || true
  pkill -f -- "$app_dir/resources/backend/godot-doctor-backend" 2>/dev/null || true
}

remove_user_data() {
  [ "$purge" -eq 1 ] || return 0
  rm -rf "$HOME/.config/Godot Doctor" "$HOME/.cache/Godot Doctor"
}

dpkg_owner() {
  dpkg-query -S "$app_dir/godot-doctor" 2>/dev/null | head -n 1 | cut -d: -f1
}

pkg=$(dpkg_owner)
if [ "$app_dir" = "/opt/Godot Doctor" ] && [ -n "$pkg" ] \
    && dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
  stop_app
  if [ "$(id -u)" -eq 0 ]; then
    runner=""
  elif command -v pkexec >/dev/null 2>&1; then
    runner="pkexec"
  elif command -v sudo >/dev/null 2>&1; then
    runner="sudo"
  else
    printf 'Root is required to remove the system package. Run: sudo apt remove %s\n' "$pkg" >&2
    exit 1
  fi
  if [ "$purge" -eq 1 ]; then action=purge; else action=remove; fi
  if [ -n "$runner" ]; then
    "$runner" apt-get "$action" -y "$pkg"
  else
    apt-get "$action" -y "$pkg"
  fi
  remove_user_data
  exit 0
fi

case "$app_dir" in
  */opt/godot-doctor) prefix=${app_dir%/opt/godot-doctor} ;;
  *)
    printf 'Refusing to remove unexpected directory: %s\n' "$app_dir" >&2
    exit 1 ;;
esac
if [ -z "$prefix" ]; then
  printf 'Refusing to remove %s without a user prefix\n' "$app_dir" >&2
  exit 1
fi

stop_app
rm -f "$prefix/bin/godot-doctor"
rm -f "$prefix/share/applications/godot-doctor.desktop"
rm -f "$prefix/share/icons/hicolor/256x256/apps/godot-doctor.png"
rm -rf "$app_dir"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q "$prefix/share/applications" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t "$prefix/share/icons/hicolor" || true
fi
remove_user_data
printf 'Removed Godot Doctor from %s\n' "$prefix"
