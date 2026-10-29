export const byId = id => document.getElementById(id);
export const number = value => Number.isFinite(value) ? value.toLocaleString() : '—';
export const positiveLine = value => Number.isInteger(value) && value > 0 ? value : 1;
export const duration = ms => !Number.isFinite(ms) ? ''
  : ms < 1000 ? `${Math.max(0, Math.round(ms))} ms` : `${(ms / 1000).toFixed(1)} s`;
export const displayText = value => String(value).replace(/[\uD800-\uDFFF]/gu, char => JSON.stringify(char).slice(1, -1));

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = displayText(text);
  return node;
}

export function text(id, value) {
  const node = byId(id);
  const display = displayText(value);
  if (node.textContent !== display) node.textContent = display;
}

export function location(diagnostic) {
  let value = diagnostic.file;
  if (Number.isInteger(diagnostic.line) && diagnostic.line > 0) {
    value += `:${diagnostic.line}`;
    if (Number.isInteger(diagnostic.column) && diagnostic.column > 0) {
      value += `:${diagnostic.column}`;
    }
  }
  return displayText(value);
}

export function severityBadge(node, severity) {
  const safe = ['error', 'warning', 'info'].includes(severity) ? severity : 'info';
  node.className = `severity severity-${safe}`;
  node.textContent = safe;
}

let resolvedProject = '';
const rules = new Map();

export const desktop = new URLSearchParams(window.location.search).get('app') === 'electron';

export function ruleHelp(id) {
  return rules.get(id) || null;
}

export function configure(config) {
  document.body.dataset.shell = desktop ? 'desktop' : 'browser';
  text('rail-version', `v${config.version} / ${config.platform}`);
  text('about-version', `Godot Doctor ${config.version} · ${config.platform} · ${desktop ? 'Local desktop app' : 'Local browser session'}`);
  rules.clear();
  const list = byId('rule-list');
  list?.replaceChildren();
  for (const rule of config.rules || []) {
    rules.set(rule.id, rule);
    if (!list) continue;
    const item = element('li', 'rule-item');
    const top = element('span', 'rule-item-top');
    const badge = element('span');
    severityBadge(badge, rule.severity);
    top.append(element('code', 'row-rule', rule.id), badge, element('span', 'rule-title', rule.title));
    item.append(top, element('span', 'rule-description', rule.description));
    list.append(item);
  }
  if (desktop) {
    text('about-shell-title', 'Godot Doctor, on your desktop');
    text('about-shell-copy', 'This app runs the same static analyzer and fixes as the CLI. It reads selected local project files without launching Godot. Scans never change your source; an automatic fix writes only when you preview and apply it. JSON and text exports include the full report, regardless of filters.');
    byId('about-launch').hidden = true;
    text('about-session-copy', 'No internet connection, account, or external service is needed. Browsing lists only the folder you request. Source excerpts are restricted to files in the current scan report. Closing the window stops the local analyzer.');
  }
}

export function statusBanner(kind, title, detail) {
  byId('status-banner').dataset.state = kind;
  const loading = ['running', 'connecting'].includes(kind);
  byId('status-symbol').classList.toggle('is-loading', loading);
  text('status-symbol', loading ? '' : kind === 'complete' ? '✓' : ['failed', 'offline'].includes(kind) ? '!' : '·');
  text('status-message', title);
  text('status-detail', detail);
  byId('retry-connection').hidden = kind !== 'offline';
}

export function renderState(state) {
  const report = state.status === 'complete' ? state.report : null;
  if (report) resolvedProject = report.project;
  text('report-path-label', !report && resolvedProject ? 'Last completed project' : 'Report project');
  text('report-project', resolvedProject || 'Available after a completed scan');
  text('error-count', number(report?.summary.errors));
  text('warning-count', number(report?.summary.warnings));
  text('file-count', number(report?.summary.analyzed));
  const indexed = report?.summary.indexed;
  text('indexed-count', report
    ? `${number(indexed)} ${indexed === 1 ? 'file' : 'files'} indexed`
    : 'Waiting for a scan');
  byId('scan-target').hidden = state.status !== 'running';
  text('scan-target', state.status === 'running' ? `Scanning: ${state.path}` : '');
  const elapsed = duration(state.elapsed_ms);
  const run = Number.isInteger(state.run_id) && state.run_id > 0 ? `Run ${state.run_id}` : '';
  text('run-meta', ['running', 'complete', 'failed'].includes(state.status)
    ? [run, elapsed].filter(Boolean).join(' · ') : '');
  if (state.status === 'running') {
    statusBanner('running', 'Scan in progress', 'Reading project files. Godot is not launched and no source files are modified.');
  } else if (state.status === 'failed') {
    statusBanner('failed', 'Could not scan this project', state.error || 'The scan failed. Check the path and run the scan again.');
  } else if (report) {
    const clean = report.diagnostics.length === 0;
    const total = report.diagnostics.length;
    const informational = report.summary.infos > 0 ? ` · ${number(report.summary.infos)} informational` : '';
    statusBanner('complete', clean ? 'Scan complete. No diagnostics found.' : 'Scan complete. Your report is ready.',
      clean ? 'No issues were found by the static analyzer. Runtime testing in Godot is still important.'
        : `${number(total)} ${total === 1 ? 'diagnostic' : 'diagnostics'}${informational}. Select a result to inspect its source.`);
  } else {
    statusBanner('idle', 'Ready when you are', 'Choose a project and run a read-only scan. Nothing starts automatically.');
  }
}

export function connectionError(message) {
  statusBanner('offline', 'Local server unavailable', message);
  byId('scan-target').hidden = true;
  text('run-meta', '');
}

export function setEmpty(kind, title, detail, mark = '[ ]') {
  byId('list-empty').hidden = false;
  byId('list-empty').dataset.kind = kind;
  text('list-empty-mark', mark);
  text('list-empty-title', title);
  text('list-empty-detail', detail);
}
