'use strict';

const electron = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

if (!electron || typeof electron !== 'object' || !electron.app) {
  console.error(
    'Godot Doctor must run under Electron. '
    + (process.env.ELECTRON_RUN_AS_NODE
      ? 'ELECTRON_RUN_AS_NODE is set; unset it and launch again.'
      : 'The Electron runtime was not available.'),
  );
  process.exit(2);
}

const {
  app, BrowserWindow, Menu, dialog, ipcMain, nativeTheme, session, shell,
} = electron;

const PARTITION = 'godot-doctor';
const ORIGIN_PATTERN = /^Godot Doctor GUI: (http:\/\/127\.0\.0\.1:\d+)$/;
const PICK_FOLDER_CHANNEL = 'godot-doctor:pick-folder';
const THEME_CHANNEL = 'godot-doctor:set-theme';
const THEME_GET_CHANNEL = 'godot-doctor:get-theme';
const THEMES = new Set(['system', 'light', 'dark']);
const WINDOW_BACKGROUND = { light: '#f6f7f9', dark: '#101219' };
const APP_ID = 'dev.godotdoctor.desktop';
const START_TIMEOUT_MS = 20000;
const SMOKE_TIMEOUT_MS = 30000;

function argumentAfter(flag) {
  const index = process.argv.indexOf(flag);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

const smokeTarget = argumentAfter('--smoke-test');
const isSmoke = process.argv.includes('--smoke-test');
const smokeExpectedErrors = argumentAfter('--smoke-expect-errors');
const smokeTheme = argumentAfter('--smoke-theme');
const smokeExpectTheme = argumentAfter('--smoke-expect-theme');
const smokeKeepProfile = process.argv.includes('--smoke-keep-profile');
const smokeShot = argumentAfter('--smoke-shot')
  || path.join(process.cwd(), 'godot-doctor-smoke.png');

if (isSmoke) {
  const smokeData = path.join(os.tmpdir(), 'godot-doctor-smoke');
  if (!smokeKeepProfile) fs.rmSync(smokeData, { recursive: true, force: true });
  app.setPath('userData', smokeData);
}

let mainWindow = null;
let backend = null;
let backendExited = false;
let backendTail = [];
let backendLog = null;
let backendLogPath = '';
let backendOrigin = '';
let quitting = false;
let settleStart = null;
let pickerOpen = false;
let themePreference = 'system';

function recordLog(line) {
  const text = String(line).replace(/\r$/, '');
  try {
    backendLog?.write(`${new Date().toISOString()} ${text}\n`);
  } catch {
    /* A failed log file must never take the app down. */
  }
  backendTail.push(text);
  if (backendTail.length > 40) backendTail.shift();
}

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
}

function readTheme() {
  try {
    const settings = JSON.parse(fs.readFileSync(settingsPath(), 'utf8'));
    return THEMES.has(settings?.theme) ? settings.theme : 'system';
  } catch {
    return 'system';
  }
}

function saveTheme(theme) {
  const file = settingsPath();
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const temporary = `${file}.tmp`;
    fs.writeFileSync(temporary, `${JSON.stringify({ theme }, null, 2)}\n`, 'utf8');
    fs.renameSync(temporary, file);
  } catch (error) {
    recordLog(`[settings] ${error.message}`);
  }
}

function themedBackground() {
  return nativeTheme.shouldUseDarkColors ? WINDOW_BACKGROUND.dark : WINDOW_BACKGROUND.light;
}

function fromAppFrame(event) {
  if (!mainWindow || mainWindow.isDestroyed()
      || event.sender !== mainWindow.webContents) {
    return false;
  }
  try {
    const frame = event.senderFrame;
    if (!frame || frame.parent) return false;
    return new URL(frame.url).origin === backendOrigin;
  } catch {
    return false;
  }
}

function launchInfo() {
  if (process.env.GODOT_DOCTOR_BACKEND) {
    return { command: process.env.GODOT_DOCTOR_BACKEND, args: [], env: {} };
  }
  if (app.isPackaged) {
    const name = process.platform === 'win32' ? 'godot-doctor-backend.exe' : 'godot-doctor-backend';
    return { command: path.join(process.resourcesPath, 'backend', name), args: [], env: {} };
  }
  const python = process.env.GODOT_DOCTOR_PYTHON
    || (process.platform === 'win32' ? 'python' : 'python3');
  return {
    command: python,
    args: ['-m', 'godot_doctor'],
    env: { PYTHONPATH: path.join(__dirname, '..', '..', 'src') },
  };
}

function registerNativePicker() {
  ipcMain.handle(PICK_FOLDER_CHANNEL, async (event, hint) => {
    if (pickerOpen || !fromAppFrame(event)) return null;
    if (typeof hint !== 'string' || hint.length > 4096 || hint.includes('\0')) {
      return null;
    }
    let defaultPath = app.getPath('home');
    if (hint.trim()) {
      try {
        const candidate = path.resolve(hint);
        defaultPath = fs.statSync(candidate).isDirectory()
          ? candidate : path.dirname(candidate);
      } catch {
        /* Keep the home folder when the hint cannot be inspected. */
      }
    }
    pickerOpen = true;
    try {
      const result = await dialog.showOpenDialog(mainWindow, {
        title: 'Select a Godot project (project.godot)',
        defaultPath,
        // A folder-only dialog cannot show project.godot, which is how a user
        // recognises a Godot project. List the file and use its folder as the root.
        properties: ['openFile'],
        filters: [{ name: 'Godot projects', extensions: ['godot'] }],
        buttonLabel: 'Select project',
      });
      return result.canceled || result.filePaths.length === 0
        ? null : path.dirname(result.filePaths[0]);
    } finally {
      pickerOpen = false;
    }
  });
}

function registerThemeHandler() {
  ipcMain.on(THEME_GET_CHANNEL, event => {
    event.returnValue = themePreference;
  });
  ipcMain.handle(THEME_CHANNEL, (event, theme) => {
    if (typeof theme !== 'string' || !THEMES.has(theme) || !fromAppFrame(event)) return null;
    themePreference = theme;
    nativeTheme.themeSource = theme;
    saveTheme(theme);
    return theme;
  });
}

function startBackend() {
  const initialPath = app.getPath('home');
  const launch = launchInfo();
  const args = [
    ...launch.args,
    '--gui', '--no-browser', '--port', '0', '--exit-on-stdin-close', initialPath,
  ];
  const child = spawn(launch.command, args, {
    cwd: initialPath,
    env: {
      ...process.env,
      ...launch.env,
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  });
  backend = child;
  child.stdin.on('error', () => {});
  child.stdout.setEncoding('utf8');
  child.stderr.setEncoding('utf8');

  const ready = new Promise((resolve, reject) => {
    settleStart = { resolve, reject };
  });
  const timer = setTimeout(() => {
    if (settleStart) {
      settleStart.reject(new Error(
        `The local analyzer did not start within ${START_TIMEOUT_MS / 1000} seconds.`,
      ));
      settleStart = null;
    }
  }, START_TIMEOUT_MS);

  let buffer = '';
  child.stdout.on('data', chunk => {
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      recordLog(line);
      const match = ORIGIN_PATTERN.exec(line.replace(/\r$/, ''));
      if (match && settleStart) {
        backendOrigin = match[1];
        clearTimeout(timer);
        settleStart.resolve(match[1]);
        settleStart = null;
      }
    }
  });
  child.stderr.on('data', chunk => {
    for (const line of chunk.split('\n')) {
      if (line.trim()) recordLog(`[stderr] ${line}`);
    }
  });
  child.on('error', error => {
    recordLog(`[spawn] ${error.message}`);
    if (settleStart) {
      clearTimeout(timer);
      settleStart.reject(error);
      settleStart = null;
    }
  });
  child.on('exit', (code, signal) => {
    backendExited = true;
    recordLog(`[exit] code=${code} signal=${signal}`);
    if (settleStart) {
      clearTimeout(timer);
      settleStart.reject(new Error(
        `The local analyzer exited during startup (code ${code}).`,
      ));
      settleStart = null;
      return;
    }
    if (!quitting) handleBackendExit(code);
  });
  return ready;
}

function stopBackend() {
  if (!backend || backendExited) return;
  try {
    backend.stdin.end();
  } catch {
    /* The pipe may already be closed. */
  }
  try {
    backend.kill();
  } catch {
    /* The process may already be gone. */
  }
}

function handleBackendExit(code) {
  if (isSmoke) {
    console.error(`SMOKE FAIL the local analyzer stopped (code ${code})`);
    quitting = true;
    app.exit(1);
    return;
  }
  const window = mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined;
  const detail = [
    `The local analyzer stopped with code ${code}.`,
    `Log: ${backendLogPath || '(not created)'}`,
  ].join('\n');
  if (window) {
    dialog.showMessageBox(window, {
      type: 'error',
      title: 'Godot Doctor stopped',
      message: 'The local analyzer stopped',
      detail,
      buttons: ['Close'],
      noLink: true,
    }).finally(() => app.quit());
  } else {
    dialog.showErrorBox('The local analyzer stopped', detail);
    app.quit();
  }
}

function configureSession() {
  const ses = session.fromPartition(PARTITION);
  ses.setPermissionRequestHandler((contents, permission, callback) => {
    callback(permission === 'clipboard-sanitized-write');
  });
  ses.setPermissionCheckHandler((contents, permission) => (
    permission === 'clipboard-sanitized-write'
  ));
  ses.on('will-download', (event, item) => {
    item.once('done', async (doneEvent, state) => {
      if (state !== 'completed') return;
      const window = mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined;
      if (!window) return;
      const { response } = await dialog.showMessageBox(window, {
        type: 'info',
        title: 'Report saved',
        message: 'The report was saved.',
        detail: item.getSavePath(),
        buttons: ['Show in folder', 'Close'],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      });
      if (response === 0) shell.showItemInFolder(item.getSavePath());
    });
  });
}

function createWindow(origin) {
  const icon = app.isPackaged
    ? path.join(process.resourcesPath, 'icon.png')
    : path.join(__dirname, '..', 'build', 'icons', '256x256.png');
  const win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1000,
    minHeight: 680,
    show: false,
    backgroundColor: themedBackground(),
    title: 'Godot Doctor',
    autoHideMenuBar: true,
    icon,
    webPreferences: {
      partition: PARTITION,
      preload: path.join(__dirname, 'preload.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: false,
      devTools: !app.isPackaged,
      backgroundThrottling: false,
    },
  });
  mainWindow = win;
  win.setMenuBarVisibility(false);

  const allow = target => {
    try {
      return new URL(target).origin === origin;
    } catch {
      return false;
    }
  };
  win.webContents.on('will-navigate', (event, target) => {
    if (!allow(target)) event.preventDefault();
  });
  win.webContents.on('will-redirect', (event, target) => {
    if (!allow(target)) event.preventDefault();
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('did-fail-load', (event, code, description, url, isMainFrame) => {
    if (!isMainFrame || code === -3 || quitting) return;
    recordLog(`[load] ${code} ${description} ${url}`);
    if (isSmoke) {
      console.error(`SMOKE FAIL the interface did not load: ${description} (${code})`);
      quitting = true;
      app.exit(1);
      return;
    }
    dialog.showErrorBox(
      'Godot Doctor could not open its interface',
      `${description} (${code})\n\nAnalyzer log: ${backendLogPath}`,
    );
    app.quit();
  });
  win.webContents.on('render-process-gone', (event, details) => {
    recordLog(`[renderer] gone reason=${details.reason} exit=${details.exitCode}`);
    if (quitting || win.isDestroyed()) return;
    if (isSmoke) {
      console.error(`SMOKE FAIL the interface process stopped (${details.reason})`);
      quitting = true;
      stopBackend();
      app.exit(1);
      return;
    }
    const choice = dialog.showMessageBoxSync(win, {
      type: 'error',
      title: 'Godot Doctor stopped',
      message: 'The interface stopped unexpectedly',
      detail: `Reason: ${details.reason}\n\nReload the interface to keep the current scan, or quit.`,
      buttons: ['Reload interface', 'Quit'],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (choice === 0) win.reload();
    else app.quit();
  });
  win.once('ready-to-show', () => {
    if (!isSmoke) win.show();
  });
  win.on('closed', () => {
    mainWindow = null;
  });
  win.loadURL(`${origin}/?app=electron`);
  return win;
}

function buildMenu() {
  const template = [
    { label: 'File', submenu: [{ role: 'quit' }] },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' }, { role: 'redo' }, { type: 'separator' },
        { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' }, { role: 'forceReload' }, { type: 'separator' },
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' }, { role: 'togglefullscreen' },
        ...(app.isPackaged ? [] : [{ type: 'separator' }, { role: 'toggleDevTools' }]),
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'Open analyzer log',
          click: () => {
            if (backendLogPath) shell.openPath(backendLogPath);
          },
        },
        { type: 'separator' },
        {
          label: 'About Godot Doctor',
          click: () => {
            dialog.showMessageBox(mainWindow || undefined, {
              type: 'info',
              title: 'About Godot Doctor',
              message: 'Godot Doctor',
              detail: [
                `Version ${app.getVersion()}`,
                'Local, read-only static analysis for Godot projects.',
                `Electron ${process.versions.electron} · Chromium ${process.versions.chrome}`,
              ].join('\n'),
              buttons: ['Close'],
              noLink: true,
            });
          },
        },
      ],
    },
  ];
  return Menu.buildFromTemplate(template);
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitFor(check, timeoutMs, description) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await delay(200);
  }
  throw new Error(`Timed out waiting for ${description}.`);
}

async function capture(window) {
  try {
    const image = await window.webContents.capturePage();
    fs.mkdirSync(path.dirname(smokeShot), { recursive: true });
    fs.writeFileSync(smokeShot, image.toPNG());
    return smokeShot;
  } catch (error) {
    console.error(`SMOKE NOTE screenshot failed: ${error.message}`);
    return null;
  }
}

async function runSmoke(window, target) {
  try {
    const expected = smokeExpectedErrors === undefined
      ? null
      : Number(smokeExpectedErrors);
    if (expected !== null && !Number.isInteger(expected)) {
      throw new Error('--smoke-expect-errors must be an integer.');
    }
    if (smokeExpectTheme !== undefined && !THEMES.has(smokeExpectTheme)) {
      throw new Error('--smoke-expect-theme must be system, light, or dark.');
    }
    await waitFor(
      () => window.webContents.executeJavaScript(
        'document.body.dataset.shell === "desktop"'
        + ' && (document.getElementById("rail-version")?.textContent || "").startsWith("v")',
      ),
      15000,
      'the interface to configure',
    );
    await waitFor(
      () => window.webContents.executeJavaScript(
        '!document.getElementById("scan-button").disabled',
      ),
      15000,
      'the scan button to become ready',
    );
    let appliedTheme = null;
    if (smokeTheme !== undefined) {
      if (!THEMES.has(smokeTheme)) {
        throw new Error('--smoke-theme must be system, light, or dark.');
      }
      appliedTheme = await window.webContents.executeJavaScript(`(() => {
        const button = document.querySelector('[data-theme-choice="${smokeTheme}"]');
        if (!button) return null;
        if (button.getAttribute('aria-pressed') !== 'true') button.click();
        return document.documentElement.dataset.theme;
      })()`);
      if (appliedTheme !== 'light' && appliedTheme !== 'dark') {
        throw new Error(`the theme did not apply (${appliedTheme})`);
      }
      await window.webContents.executeJavaScript(
        `window.godotDoctor.setTheme(${JSON.stringify(smokeTheme)})`,
      );
      await window.webContents.executeJavaScript(
        "localStorage.removeItem('godot-doctor-theme');"
        + " document.cookie = 'godot-doctor-theme=; Path=/; Max-Age=0; SameSite=Strict'",
      );
      window.webContents.reload();
      await waitFor(
        () => window.webContents.executeJavaScript(
          'document.body.dataset.shell === "desktop"'
          + ' && !document.getElementById("scan-button").disabled',
        ),
        15000,
        'the interface to reload',
      );
      const reloaded = await window.webContents.executeJavaScript(`(() => ({
        theme: document.documentElement.dataset.theme,
        pressed: [...document.querySelectorAll('[data-theme-choice]')]
          .find(button => button.getAttribute('aria-pressed') === 'true')?.dataset.themeChoice,
      }))()`);
      if (reloaded.theme !== appliedTheme || reloaded.pressed !== smokeTheme) {
        throw new Error(
          `the theme preference did not survive a reload ${JSON.stringify(reloaded)}`,
        );
      }
    }
    const entered = await window.webContents.executeJavaScript(`(() => {
      const input = document.getElementById('project-path');
      input.value = ${JSON.stringify(target)};
      input.dispatchEvent(new Event('input'));
      return input.value;
    })()`);
    if (entered !== target) {
      throw new Error(`the project path was not applied (${entered})`);
    }
    await window.webContents.executeJavaScript(
      'document.getElementById("scan-button").click()',
    );
    const state = await waitFor(
      async () => {
        const current = await window.webContents.executeJavaScript(`(() => ({
          state: document.getElementById('status-banner').dataset.state,
          errors: document.getElementById('error-count').textContent,
          project: document.getElementById('report-project').textContent,
          version: document.getElementById('rail-version').textContent,
          shell: document.body.dataset.shell,
          detail: document.getElementById('status-detail').textContent,
          theme: document.documentElement.dataset.theme,
          pressedTheme: [...document.querySelectorAll('[data-theme-choice]')]
            .find(button => button.getAttribute('aria-pressed') === 'true')?.dataset.themeChoice,
          bodyBackground: getComputedStyle(document.body).backgroundColor,
          panelBackground: getComputedStyle(document.querySelector('.project-card')).backgroundColor,
          primaryBackground: getComputedStyle(document.getElementById('scan-button')).backgroundColor,
          nativeBridge: typeof window.godotDoctor?.pickFolder === 'function',
          themeBridge: typeof window.godotDoctor?.setTheme === 'function',
          nativeButton: document.getElementById('native-folder')?.hidden === false,
          browseRemoved: document.getElementById('browse-open') === null,
          brandImage: (() => {
            const image = document.querySelector('.brand-mark');
            return Boolean(image && image.tagName === 'IMG' && image.complete && image.naturalWidth);
          })(),
        }))()`);
        return ['complete', 'failed'].includes(current.state) ? current : null;
      },
      SMOKE_TIMEOUT_MS,
      'the scan to finish',
    );
    const invalidPick = await window.webContents.executeJavaScript(
      'window.godotDoctor.pickFolder("x".repeat(4097))',
    );
    if (invalidPick !== null) {
      throw new Error('The native folder picker ignored an invalid hint.');
    }
    if (!window.isVisible()) window.showInactive();
    await delay(500);
    const rendered = await window.webContents.executeJavaScript(`(() => ({
      theme: document.documentElement.dataset.theme,
      switching: document.documentElement.hasAttribute('data-theme-switching'),
      bodyBackground: getComputedStyle(document.body).backgroundColor,
      panelBackground: getComputedStyle(document.querySelector('.project-card')).backgroundColor,
      primaryBackground: getComputedStyle(document.getElementById('scan-button')).backgroundColor,
    }))()`);
    if (rendered.switching) throw new Error('the theme transition guard did not clear');
    const shot = await capture(window);
    const checks = {
      completed: state.state === 'complete',
      version: state.version.startsWith(`v${app.getVersion()} `),
      desktop: state.shell === 'desktop',
      project: path.basename(state.project).toLowerCase()
        === path.basename(target).toLowerCase(),
      errors: expected === null || state.errors === String(expected),
      theme: appliedTheme === null || state.theme === appliedTheme,
      initialTheme: smokeExpectTheme === undefined
        || (state.pressedTheme === smokeExpectTheme
          && (smokeExpectTheme === 'system' || state.theme === smokeExpectTheme)),
      nativePicker: state.nativeBridge && state.nativeButton,
      singlePicker: state.browseRemoved,
      themeBridge: state.themeBridge,
      brandMark: state.brandImage,
    };
    if (Object.values(checks).some(value => !value)) {
      throw new Error(
        `unexpected interface state ${JSON.stringify({ checks, state })}`,
      );
    }
    console.log(`SMOKE OK ${JSON.stringify({
      version: state.version,
      project: state.project,
      errors: state.errors,
      theme: state.theme,
      pressedTheme: state.pressedTheme,
      rendered,
      screenshot: shot,
    })}`);
    quitting = true;
    stopBackend();
    app.exit(0);
  } catch (error) {
    await capture(window);
    console.error(`SMOKE FAIL ${error.message}`);
    console.error(`Analyzer log: ${backendLogPath}`);
    if (backendTail.length) {
      console.error(backendTail.slice(-10).join('\n'));
    }
    quitting = true;
    stopBackend();
    app.exit(1);
  }
}

function fail(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (quitting) {
    app.exit(1);
    return;
  }
  quitting = true;
  stopBackend();
  if (isSmoke) {
    console.error(`SMOKE FAIL ${message}`);
    console.error(`Analyzer log: ${backendLogPath || '(not created)'}`);
    app.exit(1);
    return;
  }
  dialog.showErrorBox(
    'Godot Doctor could not start',
    `${message}\n\nAnalyzer log: ${backendLogPath || '(not created)'}`,
  );
  app.exit(1);
}

async function start() {
  app.setAppUserModelId(APP_ID);
  if (isSmoke && !smokeTarget) {
    throw new Error('--smoke-test needs a project directory');
  }
  registerNativePicker();
  registerThemeHandler();
  themePreference = readTheme();
  nativeTheme.themeSource = themePreference;
  const logDirectory = path.join(app.getPath('userData'), 'logs');
  fs.mkdirSync(logDirectory, { recursive: true });
  backendLogPath = path.join(logDirectory, 'backend.log');
  backendLog = fs.createWriteStream(backendLogPath, { flags: 'w' });
  backendLog.on('error', error => {
    console.error(`Godot Doctor: the analyzer log is unavailable: ${error.message}`);
  });
  configureSession();
  const origin = await startBackend();
  const window = createWindow(origin);
  let loadTimer;
  try {
    await new Promise((resolve, reject) => {
      window.webContents.once('did-finish-load', resolve);
      window.webContents.once('did-fail-load', () => reject(new Error('The interface did not load.')));
      loadTimer = setTimeout(
        () => reject(new Error('The interface did not load in time.')), 15000,
      );
    });
  } finally {
    clearTimeout(loadTimer);
  }
  if (isSmoke) {
    await runSmoke(window, path.resolve(smokeTarget));
  }
}

function bootstrap() {
  app.on('second-instance', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  nativeTheme.on('updated', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setBackgroundColor(themedBackground());
    }
  });
  app.whenReady().then(() => {
    Menu.setApplicationMenu(buildMenu());
    return start();
  }).catch(fail);
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => {
    quitting = true;
    stopBackend();
  });
  app.on('will-quit', () => {
    backendLog?.end();
  });
}

const gotLock = isSmoke || app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  bootstrap();
}
