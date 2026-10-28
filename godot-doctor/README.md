# Godot Doctor

A local, read-only static analyzer and workbench for Godot 4 projects. It finds
broken resource references, eager dependency cycles, duplicate UIDs and related
project-health problems without launching Godot or modifying your files.

Current version: **0.3.1**.

## What it checks

| Rule | Severity | Meaning |
|---|---|---|
| `broken-reference` | error | A `res://` path does not exist |
| `case-mismatch` | warning | A path matches only on a case-insensitive filesystem |
| `invalid-resource-path` | error | A resource path escapes the project root |
| `resource-is-directory` | error | A resource path names a directory |
| `dependency-cycle` | warning | An eager dependency cycle |
| `duplicate-uid` | error | Two resources declare the same UID |
| `parse-error` | error | A file could not be parsed |
| `duplicate-ext-resource` | error | Duplicate `ext_resource` id |
| `invalid-ext-resource` | warning | An `ext_resource` path is not `res://` |
| `undefined-ext-resource` | error | An `ExtResource` id is not declared |
| `undefined-sub-resource` | error | A `SubResource` id is not declared |
| `config-parse-error` | warning | A `project.godot` line is not `key=value` |
| `unreadable-directory` | warning | A directory cannot be read |

The analyzer reads `project.godot`, scene and resource files, GDScript and shader
sources, `.uid` sidecars and adjacent `.import` metadata. It resolves `res://`
references, builds the eager dependency graph and checks UID declarations.
Run `godot-doctor explain RULE` for the cause, consequences and remediation of
any rule.

## Install

Requires Python 3.11 or newer. The analyzer and browser interface have no
runtime dependencies outside the standard library.

```sh
cd godot-doctor
python -m pip install .
godot-doctor --version
```

To run from a checkout without installing:

```sh
# Linux and macOS
PYTHONPATH=src python -m godot_doctor --version
```

```powershell
# Windows PowerShell
$env:PYTHONPATH = "src"; python -m godot_doctor --version
```

The package installs two entry points: `godot-doctor` (CLI) and
`godot-doctor-gui` (opens the graphical workbench).

## Desktop installers

**Windows x64.** Run `Godot Doctor Setup 0.3.1.exe` (per-user install) or the
portable exe. The Setup installs the GUI, a CLI shim at
`%LOCALAPPDATA%\Programs\Godot Doctor\bin\godot-doctor.cmd`, adds that `bin`
directory to your user PATH, and registers an uninstaller in Apps & Features.
Settings are stored in `%APPDATA%\Godot Doctor`. The installers are unsigned, so
SmartScreen may warn; verify the artifact hash before running a build you did
not produce.

The installer appends its `bin` directory to PATH. If an older `godot-doctor`
from pip appears earlier in PATH, that command wins; call the shim explicitly or
remove the older install. Restart open shells to pick up the new PATH entry.

Uninstall from Apps & Features, or run `uninstall.cmd /S` in the install
directory. Add `--purge` to also delete `%APPDATA%\Godot Doctor`:
`uninstall.cmd /S --purge`.

**Linux x64.**

- `.deb`: `sudo apt install ./godot-doctor_0.3.1_amd64.deb` installs the GUI
  under `/opt/Godot Doctor` and registers `/usr/bin/godot-doctor` through
  `update-alternatives`. The installed package is named `godot-doctor-desktop`;
  remove it with `godot-doctor-uninstall` run as your user (it elevates only the
  package removal) or `sudo apt remove godot-doctor-desktop`. `--purge` also
  removes settings.
- `.tar.gz`: extract and run `./install.sh` for a user-local install under
  `~/.local` (no root). Remove with
  `~/.local/opt/godot-doctor/uninstall.sh [--purge]`.
- `.AppImage`: portable; it does not install a system CLI.

Linux settings live in `~/.config/Godot Doctor` and `~/.cache/Godot Doctor`.
`--purge` deletes settings for the user running the uninstaller, so run the
Linux uninstaller as yourself rather than from a root shell. The Linux GUI was
verified as a normal user on Debian 12. On distributions that restrict
unprivileged user namespaces, Electron may report a sandbox error; in that case
launch the app with `--no-sandbox`.


## CLI

```sh
godot-doctor scan path/to/project
godot-doctor scan . --format json
godot-doctor scan . --strict              # warnings also fail the run
godot-doctor scan . --fail-on warning     # same threshold, explicit
godot-doctor scan . --severity error --rule broken-reference
godot-doctor scan . --format json --output report.json

godot-doctor explain broken-reference
godot-doctor context scenes/player.tscn:12
godot-doctor rules
godot-doctor stats .
godot-doctor refs .
godot-doctor graph . --format dot
godot-doctor discover ~/projects
godot-doctor browse .
godot-doctor describe                     # machine-readable CLI and report contract
```

Scan formats are `text`, `json`, `ndjson`, `sarif` and `github`. JSON output is
deterministic and ASCII-safe, with stable fingerprints that ignore line and
column drift. Compare a scan with an earlier JSON report:

```sh
godot-doctor scan . --format json --output before.json
# ...make changes...
godot-doctor diff before.json .
```

The original `godot-doctor PATH [--json|--strict]` form is preserved.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No diagnostic at or above the failure threshold |
| 1 | Diagnostics at or above the failure threshold, or new findings for `diff` |
| 2 | Usage, project, baseline or I/O error |
| 3 | Unexpected internal error (traceback on standard error) |

The default threshold is `error`; `--strict` and `--fail-on warning` lower it to
warnings.

## Graphical workbench

```sh
godot-doctor --gui
# or
godot-doctor-gui
```

The browser workbench starts a loopback-only local server and opens your
default browser. It reuses the same analyzer as the CLI: choose a project with
the picker or paste a path, then inspect diagnostics, source excerpts, filters
and JSON/text exports. Options: `--port PORT`, `--no-browser`.

The installed desktop app packages the same page and a local backend; closing
its window stops the backend. In the browser workbench, closing the tab does not
stop the CLI server; stop it from the launching terminal.

## Safety and privacy

- **Read-only.** Scans never modify project files. `--output` writes only the
  path you name.
- **Local only.** No account, network service, telemetry or automatic update.
- **Loopback only.** The GUI binds to `127.0.0.1`, requires a per-launch session
  cookie and enforces a strict Content Security Policy.
- **Restricted previews.** In the GUI, source excerpts are limited to files
  present in the current report and cannot leave the selected project.

## Known limits

- Static analysis is not runtime validation. A clean report does not guarantee
  the game runs correctly; dynamic and engine-specific behavior still needs
  testing in Godot.
- Binary resources, custom loaders and generated caches are not inspected.
- There is no cancel button. Normal projects scan in milliseconds; a
  pathological project (for example, a multi-megabyte single line) can take
  several seconds.
- Source previews are capped at 64 KiB and return a clear error above that.
- Windows installers are currently unsigned, so SmartScreen may warn. Verify the
  artifact hash before running a build you did not produce.
- No auto-update and no crash reporting.

## Platform support

| Platform | Status |
|---|---|
| Windows 10/11 x64 | 0.3.1 Setup (GUI + CLI shim + uninstaller) and portable built; packaged, installed, uninstall and reinstall checks passed |
| Linux x64 | 0.3.1 deb (GUI + CLI alternative + uninstaller), tar.gz (install/uninstall scripts) and AppImage built; deb GUI/CLI as a normal user, tar.gz install/CLI/GUI/uninstall/purge and AppImage smoke passed on Debian 12 |
| Linux arm64 | Not built for 0.3.1; the stale 0.3.0 arm64 artifacts were removed. The 0.3.0 backend was qemu-verified; Electron was not run. |
| macOS | Not built |

Native Linux desktop (outside WSL) is not yet verified. Linux packages require
FUSE for the AppImage (or `APPIMAGE_EXTRACT_AND_RUN`). Linux artifacts are built
separately on a Linux host and may lag the Windows release; check the version in
the artifact name before publishing or installing.

## Development

```sh
# Python tests
python scripts/run_tests.py

# Desktop build (Windows)
cd electron
npm install
npm run electron:build

# Desktop build (Linux; must run in a Linux checkout)
npm run electron:build:linux

# Regenerate icons (developer tooling only; requires Pillow)
python scripts/make_icon.py
```

The desktop build uses PyInstaller for the backend. If `.build-venv` exists it
is used automatically; otherwise the current Python environment must provide
PyInstaller. The analyzer itself stays standard-library only.

## License

MIT. See [LICENSE](LICENSE).
