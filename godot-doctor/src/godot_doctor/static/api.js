export class APIError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'APIError';
    this.status = status;
  }
}

async function request(url, { method = 'GET', body, signal, blob = false } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timeout = setTimeout(abort, 30000);
  const headers = { 'X-Godot-Doctor': '1' };
  if (method === 'POST') headers['Content-Type'] = 'application/json';
  const send = () => fetch(url, {
    method, headers, credentials: 'same-origin', cache: 'no-store',
    signal: controller.signal,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  try {
    let response = await send();
    // A restarted local server has a new session. Refresh the cookie from the page
    // and retry once instead of stranding an open workbench on a 403.
    if (response.status === 403) {
      let expired = false;
      try {
        const data = await response.clone().json();
        expired = typeof data.error === 'string' && data.error.includes('session is missing');
      } catch { /* Another 403 kind is handled as a normal API error. */ }
      if (expired) {
        await fetch('/', {
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        });
        response = await send();
      }
    }
    if (!response.ok) {
      let message = `The local server returned HTTP ${response.status}.`;
      try {
        const data = await response.json();
        if (typeof data.error === 'string') message = data.error;
      } catch { /* Non-JSON server errors still have a useful HTTP status. */ }
      throw new APIError(message, response.status);
    }
    return blob ? await response.blob() : await response.json();
  } catch (error) {
    if (signal?.aborted || error instanceof APIError) throw error;
    if (controller.signal.aborted) {
      throw new APIError('The local server took too long to respond. Reconnect to check the scan state.');
    }
    throw new APIError('Could not reach the local analyzer or read its response. Retry the connection; if it does not recover, restart Godot Doctor.');
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

function query(path, values) {
  return `${path}?${new URLSearchParams(values)}`;
}

export const api = {
  config: () => request('/api/config'),
  status: () => request('/api/status'),
  analyze: path => request('/api/analyze', { method: 'POST', body: { path } }),
  browse: (path, signal) => request('/api/browse', { method: 'POST', body: { path }, signal }),
  fix: (runId, apply) => request('/api/fix', {
    method: 'POST', body: { run_id: runId, apply },
  }),
  source: (file, line, runId, signal) => request('/api/source', {
    method: 'POST', body: { file, line, run_id: runId }, signal,
  }),
  report: (format, runId) => request(query('/api/report', {
    format, run_id: runId,
  }), { blob: true }),
};

export function saveReport(blob, format) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `godot-doctor-report.${format === 'json' ? 'json' : 'txt'}`;
  anchor.hidden = true;
  try {
    document.body.append(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    // Allow the browser to consume the URL before releasing it.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
