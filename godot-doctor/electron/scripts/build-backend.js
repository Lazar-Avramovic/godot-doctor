'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const electronDir = path.resolve(__dirname, '..');
const projectDir = path.resolve(electronDir, '..');
const isWindows = process.platform === 'win32';
const venvPython = path.join(
  projectDir, '.build-venv', isWindows ? 'Scripts' : 'bin',
  isWindows ? 'python.exe' : 'python',
);
const python = process.env.GODOT_DOCTOR_BUILD_PYTHON
  || (fs.existsSync(venvPython) ? venvPython : (isWindows ? 'python' : 'python3'));
const distPath = path.join(projectDir, 'backend-dist');
const workPath = path.join(projectDir, 'backend-build');
const staticPath = path.join(projectDir, 'src', 'godot_doctor', 'static');
const iconPath = path.join(electronDir, 'build', 'icon.ico');

const args = [
  '-m', 'PyInstaller',
  path.join(__dirname, 'backend_entry.py'),
  '--name', 'godot-doctor-backend',
  '--onedir',
  '--noconfirm',
  '--clean',
  '--log-level', 'WARN',
  '--distpath', distPath,
  '--workpath', workPath,
  '--specpath', workPath,
  '--paths', path.join(projectDir, 'src'),
  '--add-data', `${staticPath}${path.delimiter}godot_doctor/static`,
];
if (isWindows && fs.existsSync(iconPath)) {
  args.push('--icon', iconPath);
}

console.log(`[backend] ${python} -m PyInstaller (${isWindows ? 'win' : 'posix'})`);
const result = spawnSync(python, args, { cwd: projectDir, stdio: 'inherit' });
if (result.error) {
  console.error(`[backend] could not run PyInstaller: ${result.error.message}`);
  console.error('[backend] create .build-venv and install pyinstaller first');
  process.exit(1);
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

const binary = path.join(
  distPath, 'godot-doctor-backend',
  isWindows ? 'godot-doctor-backend.exe' : 'godot-doctor-backend',
);
if (!fs.existsSync(binary)) {
  console.error(`[backend] expected binary missing: ${binary}`);
  process.exit(1);
}
console.log(`[backend] built ${binary}`);
