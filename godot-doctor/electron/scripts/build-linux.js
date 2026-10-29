'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const electronDir = path.resolve(__dirname, '..');
const projectDir = path.resolve(electronDir, '..');
const hostVenvDir = path.join(projectDir, '.build-venv');
const hostVenvPython = path.join(hostVenvDir, 'bin', 'python');
const portableRoot = path.join(projectDir, '.build-python');
const portablePythonDir = path.join(portableRoot, 'python');
const portableVenvPython = path.join(portableRoot, 'venv', 'bin', 'python');
const backendBinary = path.join(
  projectDir, 'backend-dist', 'godot-doctor-backend', 'godot-doctor-backend',
);
const builder = path.join(electronDir, 'node_modules', '.bin', 'electron-builder');
const config = path.join(electronDir, 'electron-builder.linux.yml');
const outputDir = path.join(electronDir, 'dist-linux');
const PACKAGED_BACKEND = path.join(
  outputDir, 'linux-unpacked', 'resources', 'backend', 'godot-doctor-backend',
);

// Pinned so the frozen backend keeps its glibc 2.17 baseline regardless of host.
const PORTABLE = {
  release: '20261026',
  cpython: '3.12.16',
  arches: { x64: 'x86_64', arm64: 'aarch64' },
  glibcCeiling: [2, 28],
};

const hostPython = process.argv.includes('--host-python');
const dirOnly = process.argv.includes('--dir');
if (process.argv.includes('--help') || process.argv.includes('-h')) {
  console.log([
    'Build the Linux desktop app (AppImage, deb, tar.gz) natively.',
    '',
    'Usage: node scripts/build-linux.js [--dir] [--host-python]',
    '',
    '  --dir          produce only dist-linux/linux-unpacked',
    '  --host-python  freeze with the host Python instead of the pinned',
    '                 portable CPython (faster, but inherits host glibc)',
    '',
    'The analyzer backend is frozen with PyInstaller, which cannot cross-compile;',
    'the packaged architecture is the host architecture. Run the same command on an',
    'arm64 machine for an arm64 release.',
  ].join('\n'));
  process.exit(0);
}

function fail(message, hint) {
  console.error(`[linux] ${message}`);
  if (hint) console.error(`[linux] ${hint}`);
  process.exit(1);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || projectDir,
    stdio: 'inherit',
    env: options.env || process.env,
  });
  if (result.error) {
    fail(`could not run ${command}: ${result.error.message}`, options.hint);
  }
  if (result.status !== 0) {
    fail(`${command} exited with ${result.status}`, options.hint);
  }
}

function elfBinary(file) {
  try {
    return fs.readFileSync(file).subarray(0, 4).toString('latin1') === '\x7fELF';
  } catch {
    return false;
  }
}

function maxGlibc(directory) {
  let highest = [0, 0];
  const entries = fs.readdirSync(directory, { recursive: true, withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const file = path.join(entry.parentPath || entry.path, entry.name);
    if (!elfBinary(file)) continue;
    const result = spawnSync('objdump', ['-T', file], { encoding: 'utf8' });
    if (result.status !== 0) continue;
    for (const match of result.stdout.matchAll(/GLIBC_(\d+)\.(\d+)/g)) {
      const version = [Number(match[1]), Number(match[2])];
      if (version[0] > highest[0]
          || (version[0] === highest[0] && version[1] > highest[1])) {
        highest = version;
      }
    }
  }
  return `${highest[0]}.${highest[1]}`;
}

function versionParts(text) {
  return text.split('.').map(Number);
}

function aboveCeiling(floor, ceiling) {
  const left = versionParts(floor);
  const right = versionParts(ceiling);
  return left[0] > right[0] || (left[0] === right[0] && left[1] > right[1]);
}

function checkVersion() {
  const desktop = JSON.parse(
    fs.readFileSync(path.join(electronDir, 'package.json'), 'utf8'),
  ).version;
  const source = fs.readFileSync(
    path.join(projectDir, 'src', 'godot_doctor', '__init__.py'), 'utf8',
  );
  const match = /__version__\s*=\s*"([^"]+)"/.exec(source);
  if (!match) {
    fail('could not read __version__ from src/godot_doctor/__init__.py');
  }
  if (match[1] !== desktop) {
    fail(
      `version mismatch: electron/package.json is ${desktop}, `
      + `src/godot_doctor/__init__.py is ${match[1]}`,
      'keep the desktop version equal to the Python package version',
    );
  }
}

function ensureHostVenv() {
  if (!fs.existsSync(hostVenvPython)) {
    console.log(`[linux] creating ${path.relative(projectDir, hostVenvDir)}`);
    run('python3', ['-m', 'venv', hostVenvDir], {
      hint: 'install the venv module first: sudo apt install python3-venv',
    });
  }
  const probe = spawnSync(hostVenvPython, ['-m', 'PyInstaller', '--version'], {
    stdio: 'ignore',
  });
  if (probe.status !== 0) {
    console.log('[linux] installing PyInstaller into .build-venv');
    run(hostVenvPython, [
      '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pyinstaller',
    ], {
      hint: 'PyInstaller is downloaded from PyPI; check the network connection',
    });
  }
  return hostVenvPython;
}

async function download(url, destination) {
  const response = await fetch(url, { redirect: 'follow' });
  if (!response.ok) {
    fail(`download failed (${response.status} ${response.statusText})`, url);
  }
  fs.writeFileSync(destination, Buffer.from(await response.arrayBuffer()));
}

async function ensurePortablePython() {
  const arch = PORTABLE.arches[process.arch];
  if (!arch) fail(`no portable Python for host architecture ${process.arch}`);
  if (!elfBinary(path.join(portablePythonDir, 'bin', 'python3'))) {
    fs.mkdirSync(portableRoot, { recursive: true });
    const archive = path.join(portableRoot, 'cpython.tar.gz');
    const url = 'https://github.com/astral-sh/python-build-standalone/releases/download/'
      + `${PORTABLE.release}/cpython-${PORTABLE.cpython}%2B${PORTABLE.release}`
      + `-${arch}-unknown-linux-gnu-install_only.tar.gz`;
    console.log(`[linux] downloading portable CPython ${PORTABLE.cpython} (${arch})`);
    await download(url, archive);
    run('tar', ['-xzf', archive, '-C', portableRoot], {
      hint: 'tar is required to unpack the portable Python',
    });
    fs.rmSync(archive, { force: true });
  }
  if (!elfBinary(portableVenvPython)) {
    console.log('[linux] creating .build-python/venv');
    run(portablePythonDir + '/bin/python3', ['-m', 'venv', path.join(portableRoot, 'venv')]);
  }
  const probe = spawnSync(portableVenvPython, ['-m', 'PyInstaller', '--version'], {
    stdio: 'ignore',
  });
  if (probe.status !== 0) {
    console.log('[linux] installing PyInstaller into .build-python/venv');
    run(portableVenvPython, [
      '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pyinstaller',
    ], {
      hint: 'PyInstaller is downloaded from PyPI; check the network connection',
    });
  }
  return portableVenvPython;
}

async function build() {
  const objdump = spawnSync('objdump', ['--version'], { stdio: 'ignore' });
  if (objdump.status !== 0) {
    fail('PyInstaller needs objdump, which is missing', 'sudo apt install binutils');
  }
  checkVersion();
  const python = hostPython ? ensureHostVenv() : await ensurePortablePython();

  console.log(`[linux] freezing the analyzer backend with ${python}`);
  run(process.execPath, [path.join(__dirname, 'build-backend.js')], {
    env: { ...process.env, GODOT_DOCTOR_BUILD_PYTHON: python },
  });
  if (!elfBinary(backendBinary)) {
    fail(`backend is missing or not a Linux ELF binary: ${backendBinary}`);
  }
  const floor = maxGlibc(path.dirname(backendBinary));
  const ceiling = PORTABLE.glibcCeiling.join('.');
  if (!hostPython && aboveCeiling(floor, ceiling)) {
    fail(
      `frozen backend requires glibc ${floor}, above the ${ceiling} ceiling`,
      'the portable Python download may have changed its baseline',
    );
  }
  console.log(`[linux] backend glibc floor: ${floor}`);

  console.log('[linux] packaging with electron-builder');
  const args = [
    '--linux', `--${process.arch}`, '--config', config, '--publish', 'never',
  ];
  if (dirOnly) args.push('--dir');
  run(builder, args, { cwd: electronDir });

  if (!fs.existsSync(PACKAGED_BACKEND) || !elfBinary(PACKAGED_BACKEND)) {
    fail(`packaged backend is missing or not a Linux ELF binary: ${PACKAGED_BACKEND}`);
  }
  try {
    fs.accessSync(PACKAGED_BACKEND, fs.constants.X_OK);
  } catch {
    fail(`packaged backend is not executable: ${PACKAGED_BACKEND}`);
  }

  const names = dirOnly ? [] : fs.readdirSync(outputDir);
  const artifacts = names
    .filter(name => /\.(AppImage|deb|tar\.gz)$/.test(name))
    .map(name => path.join(outputDir, name));
  const appImage = artifacts.find(name => name.endsWith('.AppImage'));
  if (!dirOnly && !appImage) {
    fail(`no AppImage was produced in ${outputDir}`);
  }
  if (appImage && !elfBinary(appImage)) {
    fail(`AppImage is not an ELF executable: ${appImage}`);
  }
  console.log('[linux] artifacts:');
  for (const file of artifacts) {
    console.log(`[linux]   ${file} (${(fs.statSync(file).size / 1048576).toFixed(1)} MiB)`);
  }
  if (dirOnly) console.log(`[linux]   ${path.join(outputDir, 'linux-unpacked')}`);
}

if (process.platform !== 'linux') {
  fail(
    `this build runs on Linux only (host is ${process.platform})`,
    'the analyzer backend is frozen by PyInstaller on the host and cannot be '
      + 'cross-compiled; build inside WSL/Ubuntu or on a Linux machine',
  );
}
if (!['x64', 'arm64'].includes(process.arch)) {
  fail(`unsupported host architecture: ${process.arch}`);
}
if (!fs.existsSync(builder)) {
  fail('electron-builder is not installed', 'run npm install in godot-doctor/electron');
}
build().catch(error => fail(error.message));
