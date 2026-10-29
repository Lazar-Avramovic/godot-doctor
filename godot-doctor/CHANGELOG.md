# Changelog

Notable changes to Godot Doctor, newest first. The project is pre-1.0, so minor versions may change
behaviour; entries here cover only what a user can observe. History before 0.3.0 is not tracked here.
Dates are the date the version was built; only some of these versions were actually published — see
[Release history](#release-history) at the end.

## [0.3.5] - 2026-10-28

### Fixed

- A reference into a directory the scan could not list was reported as `broken-reference` even when
  the file was present. The directory is now reported as `unreadable-directory` and the reference is
  left unjudged, because absence inside an unreadable tree is not evidence.
- A directory without execute permission aborted the whole scan with an `io-error` and no report at
  all — `pathlib` re-raises `EACCES` where it ignores `ENOENT`. The scan now reports everything it
  could read. Reproduced as an unprivileged user on Linux, not by reading the code.
- `diff` with a baseline file that is not valid UTF-8 (UTF-16 or Latin-1, for example) printed a
  traceback and exited 3. It now returns the structured `baseline-error` and exit 2, like every
  other bad-baseline case.
- A failed `fix --apply` against a read-only `project.godot` left a `.project.godot.*.tmp` file
  behind in the project; the temporary file is now removed when the rename fails.

### Changed

- The README states scan cost from measurement rather than estimation — about 0.36 s per MB of scene
  text on Windows (8 MB 2.9 s, 32 MB 11.6 s, 64 MB 23.3 s) — and that text reads have no size cap.

## [0.3.4] - 2026-10-28

### Added

- `config-unclosed-value`: an unterminated `{` or `[` value swallowed every later line of
  `project.godot` and the scan still came back clean. It is now an error reported at the line that
  opened the value. Closed multi-line values, including `[input]` sections, stay clean.

## [0.3.3] - 2026-10-28

### Added

- `godot-doctor fix`: preview a repair, then opt in with `--apply`, in both the CLI and the
  workbench. Only leading `#` lines in `project.godot` are rewritten, the planned line is checked
  byte-for-byte before writing, and a file that changed since the preview aborts instead of being
  overwritten.
- Rules `config-hash-comment` (error: Godot does not treat `#` as a comment, so the line is silently
  lost) and `config-nonkey-line` (information, never rewritten). They replace the single
  `config-parse-error`, so harmless lines can be filtered without losing the hash check.

### Fixed

- Multi-line `[input]` values were parsed as malformed config, so a healthy project failed its scan.
  Continuation-aware parsing keeps the `res://` references inside them and drops the false errors.
- Report fingerprints now anchor to a stable subject, so rewording a message no longer marks every
  row as new in `diff`.

## [0.3.1] - 2026-10-28

### Added

- The desktop app installs a CLI on both platforms: the Windows Setup writes
  `bin\godot-doctor.cmd`, appends it to the user PATH and registers an uninstaller in Apps & Features;
  the Linux `.deb` registers `/usr/bin/godot-doctor` through `update-alternatives` plus
  `godot-doctor-uninstall`; the `.tar.gz` ships `install.sh` and `uninstall.sh` for a rootless
  install under `~/.local`.
- Official app icon across 16–512 px, the installer, the taskbar and the workbench rail.
- `godot-doctor/README.md`, covering installation, the CLI contract, safety and platform limits.

### Fixed

- The project picker could never show `project.godot`, because it opened a folder-only dialog. It now
  opens file selection filtered to Godot projects and resolves the containing directory.

## [0.3.0] - 2026-10-28

### Added

- Electron desktop app around the existing browser workbench: the same loopback page with a frozen
  backend, plus a Windows NSIS installer and a portable build.
- Native Linux packaging — AppImage, deb and a FUSE-free tar.gz. A pinned portable CPython keeps the
  frozen backend at a glibc 2.17 floor, and the build script refuses non-Linux hosts and non-ELF
  backends.
- System/Light/Dark theming across the workbench, with a light palette, a blocking theme script (no
  flash on load) and the chosen theme persisted.
- A project picker for choosing a project folder.
- Machine-readable report formats (JSON, NDJSON, SARIF, GitHub annotations) with stable
  fingerprints, exact filters, baseline diff, and `describe` as the CLI contract.

### Fixed

- Session recovery after a backend restart, which previously left the page answering 403 until a
  reload.
- Quoted or space-padded paths pasted from a file manager.
- `preload(r"res://...")` raw-string references, previously a false negative.
- Shader source previews for case-mismatched paths, which returned 415.
- Singular/plural copy across the workbench.

## Release history

Only two versions have been published as GitHub releases.

| Version | Published | Note |
|---|---|---|
| 0.3.5 | 2026-10-28 | current release |
| 0.3.4 | no | never published; its changes ship in 0.3.5 |
| 0.3.3 | no | never published; its changes ship in 0.3.5 |
| 0.3.2 | — | number skipped |
| 0.3.1 | 2026-10-28 | first published release |
| 0.3.0 | no | never published; its changes ship in 0.3.1 |
