# Godot Doctor

A local, read-only static analyzer and workbench for **Godot 4** projects. It finds broken `res://`
references, eager dependency cycles, duplicate UIDs, undeclared `ExtResource`/`SubResource` ids and
related project-health problems — without launching Godot and without modifying your files.

Current release: **0.3.1**. Full documentation, including install and uninstall procedures, is in
[godot-doctor/README.md](godot-doctor/README.md).

- **Read-only.** Scans never write into your project. `--output` writes only the path you name.
- **Local only.** No account, no network service, no telemetry, no auto-update.
- **No dependencies.** The analyzer and the browser workbench are standard-library Python 3.11+.
- **Scriptable.** JSON, NDJSON, SARIF and GitHub-annotation output, stable fingerprints, a baseline
  diff tolerant of line drift, and machine-readable exit codes.

## Install

Grab a packaged build from the
[releases page](https://github.com/Lazar-Avramovic/godot-doctor/releases).

| Artifact | Platform | What it gives you |
|---|---|---|
| `Godot Doctor Setup 0.3.1.exe` | Windows 10/11 x64 | GUI + `godot-doctor` CLI shim on your user PATH + uninstaller |
| `Godot-Doctor-0.3.1-portable.exe` | Windows 10/11 x64 | GUI, no install and no admin rights |
| `godot-doctor_0.3.1_amd64.deb` | Linux x64 | GUI under `/opt` + `/usr/bin/godot-doctor` via `update-alternatives` |
| `Godot-Doctor-0.3.1-x64.tar.gz` | Linux x64 | Rootless user-local install under `~/.local` |
| `Godot-Doctor-0.3.1-x86_64.AppImage` | Linux x64 | Portable GUI; needs FUSE or `APPIMAGE_EXTRACT_AND_RUN` |

Windows installers are unsigned, so SmartScreen may warn; verify the artifact hash before running a
build you did not produce. Install, uninstall and purge procedures are in
[godot-doctor/README.md](godot-doctor/README.md).

From source:

```sh
cd godot-doctor
python -m pip install .
godot-doctor --version
```

## Use

```sh
godot-doctor scan path/to/project            # human-readable report
godot-doctor scan . --format sarif           # for CI and editor tooling
godot-doctor scan . --strict                 # warnings also fail the run
godot-doctor explain broken-reference        # cause, consequences, remediation
godot-doctor diff before.json .              # what changed since the last scan
```

The graphical workbench uses the same analyzer:

```sh
godot-doctor-gui
```

It starts a loopback-only server on `127.0.0.1`, requires a per-launch session cookie, enforces a
strict Content Security Policy, and opens your default browser. Source previews are limited to files
present in the current report and cannot escape the selected project.

## What it checks

Thirteen rules across `project.godot`, scenes, resource files, GDScript, shaders, `.uid` sidecars and
adjacent `.import` metadata: `broken-reference`, `case-mismatch`, `invalid-resource-path`,
`resource-is-directory`, `dependency-cycle`, `duplicate-uid`, `parse-error`,
`duplicate-ext-resource`, `invalid-ext-resource`, `undefined-ext-resource`, `undefined-sub-resource`,
`config-parse-error` and `unreadable-directory`. Each rule's severity and meaning are tabulated in
[godot-doctor/README.md](godot-doctor/README.md).

A clean report means no static defects of that kind were found. It is not runtime validation —
dynamic and engine-specific behaviour still needs testing in Godot.

## Platform support

| Platform | Status for 0.3.1 |
|---|---|
| Windows 10/11 x64 | Setup and portable built; packaged, installed, uninstalled and reinstalled checks passed |
| Linux x64 | deb, tar.gz and AppImage built; verified as a normal user on Debian 12 |
| Linux arm64 | Not built; the desktop build is host-architecture only |
| macOS | Not built |

## Development

```sh
cd godot-doctor
python scripts/run_tests.py          # analyzer, CLI and workbench suites
cd electron && npm install && npm run electron:build      # Windows desktop build
npm run electron:build:linux                              # Linux build, in a Linux checkout
```

## License

MIT. See [LICENSE](LICENSE).
