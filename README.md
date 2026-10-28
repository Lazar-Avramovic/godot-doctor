# Godot Doctor

A local static analyzer and workbench for **Godot 4** projects. It finds broken `res://`
references, eager dependency cycles, duplicate UIDs, undeclared `ExtResource`/`SubResource` ids and
related project-health problems — without launching Godot. Scans never modify your files; the opt-in
fixer writes only after you preview and apply it.

Current version: **0.3.5**. Full documentation, including install and uninstall procedures, is in
[godot-doctor/README.md](godot-doctor/README.md).

- **Read-only scans, opt-in fixes.** Scanning never writes into your project. The `fix` command and
  the workbench preview safe parser repairs and write only when you apply them. `--output` writes
  only the path you name.
- **Local only.** No account, no network service, no telemetry, no auto-update.
- **No dependencies.** The analyzer and the browser workbench are standard-library Python 3.11+.
- **Scriptable.** JSON, NDJSON, SARIF and GitHub-annotation output, stable fingerprints, a baseline
  diff tolerant of line drift, and machine-readable exit codes.

## Install

Grab a packaged build from the
[releases page](https://github.com/Lazar-Avramovic/godot-doctor/releases).

| Artifact | Platform | What it gives you |
|---|---|---|
| `Godot Doctor Setup 0.3.5.exe` | Windows 10/11 x64 | GUI + `godot-doctor` CLI shim on your user PATH + uninstaller |
| `Godot-Doctor-0.3.5-portable.exe` | Windows 10/11 x64 | GUI, no install and no admin rights |
| `godot-doctor_0.3.5_amd64.deb` | Linux x64 | GUI under `/opt` + `/usr/bin/godot-doctor` via `update-alternatives` |
| `Godot-Doctor-0.3.5-x64.tar.gz` | Linux x64 | Rootless user-local install under `~/.local` |
| `Godot-Doctor-0.3.5-x86_64.AppImage` | Linux x64 | Portable GUI; needs FUSE or `APPIMAGE_EXTRACT_AND_RUN` |

Both platforms are built at 0.3.5.

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
present in the current report and cannot escape the selected project. A `config-hash-comment` can be
previewed and fixed from the inspector; applying it triggers a rescan.

## What it checks

Fifteen rules across `project.godot`, scenes, resource files, GDScript, shaders, `.uid` sidecars and
adjacent `.import` metadata: `broken-reference`, `case-mismatch`, `invalid-resource-path`,
`resource-is-directory`, `dependency-cycle`, `duplicate-uid`, `parse-error`,
`duplicate-ext-resource`, `invalid-ext-resource`, `undefined-ext-resource`, `undefined-sub-resource`,
`config-hash-comment`, `config-nonkey-line`, `config-unclosed-value` and `unreadable-directory`.
Each rule's severity and meaning are tabulated in [godot-doctor/README.md](godot-doctor/README.md).

A clean report means no static defects of that kind were found. It is not runtime validation —
dynamic and engine-specific behaviour still needs testing in Godot. Importer failures and GDScript
compile errors are engine-side; run `godot --headless --import` to surface them.

## Platform support

| Platform | Status |
|---|---|
| Windows 10/11 x64 | 0.3.5 Setup and portable built; installed per-user at `%LOCALAPPDATA%\Programs\Godot Doctor` and verified (CLI shim, packaged backend scan, version stamps) |
| Linux x64 | 0.3.5 deb, tar.gz and AppImage built and verified on Debian 12 (install, CLI, scan through each package) |
| Linux arm64 | Not built for 0.3.5; the desktop build is host-architecture only |
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
