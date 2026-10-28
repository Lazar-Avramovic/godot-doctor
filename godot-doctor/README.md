# Godot Doctor

A local static analyzer and workbench for Godot 4 projects. It finds broken
resource references, eager dependency cycles, duplicate UIDs and related
project-health problems without launching Godot. Scans never modify your files;
the opt-in fixer writes only after you preview and apply it.

Current version: **0.3.5**.

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
| `config-hash-comment` | error | A `#` line is folded into the next key and can silently lose the next setting |
| `config-nonkey-line` | info | A `project.godot` line is not `key=value`; Godot ignores it (multi-line values are parsed) |
| `config-unclosed-value` | error | A `{` or `[` value is never closed; Godot cannot parse the rest of the file |
| `unreadable-directory` | warning | A directory cannot be read |

The analyzer reads `project.godot`, scene and resource files, GDScript and shader
sources, `.uid` sidecars and adjacent `.import` metadata. It resolves `res://`
references, builds the eager dependency graph and checks UID declarations.
Run `godot-doctor explain RULE` for the cause, consequences and remediation of
any rule. The workbench shows the same explanation when you select a diagnostic.

## Automatic fixes (opt-in)

Godot's `ConfigFile` format treats only `;` as a comment. A line beginning with
`#` is parsed as key text and folded into the next key, so the setting can
disappear while `ProjectSettings.has_setting()` still answers true.

The fixer repairs those leading `#` lines in `project.godot` by changing them to
`;`. It never runs as part of a scan and writes nothing until you apply:

```sh
godot-doctor fix path/to/project            # preview: files, lines, before -> after
godot-doctor fix path/to/project --apply    # write the previewed changes
```

In the workbench, select a `config-hash-comment` diagnostic, choose **Preview
automatic fix**, review the exact lines, then **Apply**. Every previewed line is
re-verified before writing; if the file changed in between, the fix is refused
and a new scan is required. Only these `#` lines are automatic — other
non-`key=value` lines are informational and are never rewritten. The same
planner backs the CLI and GUI.

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

**Windows x64.** Run `Godot Doctor Setup 0.3.5.exe` (per-user install) or the
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

- `.deb`: `sudo apt install ./godot-doctor_0.3.5_amd64.deb` installs the GUI
  under `/opt/Godot Doctor` and registers `/usr/bin/godot-doctor` through
  `update-alternatives`. The
  installed package is named `godot-doctor-desktop`;
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
godot-doctor fix .                         # preview safe parser repairs
godot-doctor fix . --apply                 # write them after review
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
default browser. It reuses the same analyzer and fix planner as the CLI: choose
a project with the picker or paste a path, then inspect diagnostics, rule
explanations, source excerpts, filters and JSON/text exports. A
`config-hash-comment` offers a previewed, opt-in fix; applying it triggers a
rescan. Values that span lines with `{ }` or `[ ]` are parsed normally and
never appear as configuration errors. Options: `--port PORT`, `--no-browser`.

The installed desktop app packages the same page and a local backend; closing
its window stops the backend. In the browser workbench, closing the tab does not
stop the CLI server; stop it from the launching terminal.

## Safety and privacy

- **Read-only scans.** Scanning never modifies project files. `--output` writes
  only the path you name.
- **Opt-in fixes.** `fix` previews by default; only `fix --apply` (CLI) or the
  workbench's explicit Apply writes, and only leading `#` lines in
  `project.godot`. Each previewed line is re-verified first.
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
- Importer failures (for example a corrupt or unsupported image) and GDScript
  compile errors are engine-side and invisible to text analysis. Run
  `godot --headless --import` or open the project in the editor to surface them.
- Automatic fixes cover leading `#` lines in `project.godot` only. Other
  malformed lines are reported as information and are never rewritten.
- A reference into a directory the scan cannot list is not reported as broken:
  the directory is reported as `unreadable-directory` instead, because a file
  missing from an unreadable tree is not evidence that it is absent.
- There is no cancel button, and text files are read whole with no size cap.
  Normal projects scan in milliseconds. Very large scenes cost roughly 0.36
  seconds per megabyte, measured on Windows: 8 MB 2.9 s, 32 MB 11.6 s,
  64 MB 23.3 s.
- Source previews are capped at 64 KiB and return a clear error above that.
- Windows installers are currently unsigned, so SmartScreen may warn. Verify the
  artifact hash before running a build you did not produce.
- No auto-update and no crash reporting.

## Platform support

| Platform | Status |
|---|---|
| Windows 10/11 x64 | 0.3.5 Setup (GUI + CLI shim + uninstaller) and portable built; installed per-user and verified (shim `--version`, packaged backend scan, baseline error handling). The in-app GUI smoke was not re-run for this build |
| Linux x64 | 0.3.5 deb, tar.gz and AppImage built on Debian 12; deb installs and its CLI reports 0.3.5, the known-broken fixture gives 6 errors/2 warnings through it, tar.gz user-local install works, and the AppImage's bundled backend runs the same scan. GUI checked for launch liveness only |
| Linux arm64 | Not built for 0.3.5; the stale 0.3.0 arm64 artifacts were removed. The 0.3.0 backend was qemu-verified; Electron was not run. |
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
