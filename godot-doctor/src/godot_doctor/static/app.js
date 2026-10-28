import { api, APIError, saveReport } from './api.js';
import { byId, configure, connectionError, renderState, statusBanner, text } from './view.js';
import { Diagnostics } from './diagnostics.js';
import { DirectoryPicker } from './directory.js';
import { Inspector } from './inspector.js';

let config = null;
let current = null;
let online = false;
let pending = false;
let exporting = false;
let pollTimer;
const inspector = new Inspector();
const diagnostics = new Diagnostics(
  (diagnostic, runId) => inspector.select(diagnostic, runId),
  phase => inspector.reset(phase),
);
const directory = new DirectoryPicker(path => {
  byId('project-path').value = path;
  updateControls();
});
const nativeFolder = byId('native-folder');
const nativePicker = typeof window.godotDoctor?.pickFolder === 'function';

function updateControls() {
  const running = current?.status === 'running';
  const locked = pending || !online || running;
  byId('project-path').disabled = locked;
  nativeFolder.disabled = locked;
  byId('scan-button').disabled = locked || !byId('project-path').value.trim();
  text('scan-button-label', running ? 'Scanning…' : pending && online ? 'Starting…' : 'Run scan');
  byId('retry-connection').disabled = pending;
  const canExport = !locked && !exporting && current?.status === 'complete' && current.report;
  byId('export-json').disabled = !canExport;
  byId('export-text').disabled = !canExport;
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (online && !pending && current?.status === 'running') pollTimer = setTimeout(refreshStatus, 350);
}

function accept(state) {
  if (!['idle', 'running', 'complete', 'failed'].includes(state.status)
      || (state.status === 'complete' && !state.report)) {
    throw new APIError('The local server returned an incomplete scan state. Reconnect to try again.');
  }
  const changed = !current || current.run_id !== state.run_id || current.status !== state.status;
  current = state;
  online = true;
  renderState(state);
  // Restore the path the server actually scanned, including after a reload or a failed scan.
  if (state.path && state.status !== 'idle') byId('project-path').value = state.path;
  if (changed) diagnostics.setReport(state.status === 'complete' ? state.report : null, state.run_id, state.status);
}

function disconnect(error) {
  online = false;
  current = null;
  clearTimeout(pollTimer);
  renderState({ status: 'idle', report: null });
  diagnostics.setReport(null, null, 'offline');
  connectionError(error.message);
}

async function refreshStatus() {
  try {
    accept(await api.status());
  } catch (error) {
    disconnect(error);
  } finally {
    updateControls();
    schedulePoll();
  }
}

async function connect() {
  if (pending) return;
  pending = true;
  clearTimeout(pollTimer);
  statusBanner('connecting', 'Connecting to the local analyzer', 'Restoring this session. No scan starts automatically.');
  updateControls();
  try {
    if (!config) {
      config = await api.config();
      configure(config);
      directory.configure(config);
      byId('project-path').value = config.initial_path;
    }
    accept(await api.status());
  } catch (error) {
    disconnect(error);
  } finally {
    pending = false;
    updateControls();
    schedulePoll();
  }
}

async function runScan(event) {
  event.preventDefault();
  const path = byId('project-path').value;
  if (!path.trim() || pending || !online || current?.status === 'running') return;
  pending = true;
  current = null;
  schedulePoll();
  updateControls();
  text('action-feedback', '');
  diagnostics.setReport(null, null, 'running');
  renderState({ status: 'running', path, report: null });
  statusBanner('running', 'Starting a read-only scan', 'Waiting for the local analyzer to accept the selected path.');
  // The submit button is now disabled; without this, keyboard focus would fall back to <body>.
  byId('status-banner').focus();
  try {
    accept(await api.analyze(path));
  } catch (error) {
    if (error.status === 409) {
      try {
        accept(await api.status());
        text('action-feedback', 'A scan was already active. Its current state has been restored; no duplicate scan was started.');
      } catch (statusError) { disconnect(statusError); }
    } else if (!error.status) {
      disconnect(error);
    } else {
      accept({ status: 'failed', run_id: null, path, error: error.message, report: null });
    }
  } finally {
    pending = false;
    updateControls();
    schedulePoll();
  }
}

async function exportReport(format) {
  if (exporting || pending || !online || current?.status !== 'complete' || !current.report) return;
  const runId = current.run_id;
  exporting = true;
  text('action-feedback', `Preparing the full ${format === 'json' ? 'JSON' : 'text'} report, independent of filters…`);
  updateControls();
  try {
    const blob = await api.report(format, runId);
    if (!online || pending || current?.run_id !== runId || current.status !== 'complete') {
      text('action-feedback', 'The active report changed. Export again when the current scan is complete.');
      return;
    }
    saveReport(blob, format);
    text('action-feedback', 'Full report download started. Check your downloads.');
  } catch (error) {
    text('action-feedback', `Could not export the report: ${error.message}`);
  } finally {
    exporting = false;
    updateControls();
  }
}

async function chooseSystemFolder() {
  nativeFolder.disabled = true;
  text('action-feedback', '');
  try {
    const selected = await window.godotDoctor.pickFolder(byId('project-path').value);
    if (typeof selected === 'string' && selected.trim()) {
      byId('project-path').value = selected;
      byId('project-path').dispatchEvent(new Event('input'));
    }
  } catch (error) {
    text('action-feedback', `Could not open the project picker: ${error.message || error}`);
  } finally {
    updateControls();
  }
}

byId('project-form').addEventListener('submit', runScan);
byId('project-path').addEventListener('input', updateControls);
byId('retry-connection').addEventListener('click', connect);
byId('export-json').addEventListener('click', () => exportReport('json'));
byId('export-text').addEventListener('click', () => exportReport('text'));
byId('about-open').addEventListener('click', () => byId('about-dialog').showModal());
nativeFolder.addEventListener('click', nativePicker
  ? chooseSystemFolder
  : () => directory.open(byId('project-path').value));
connect();
