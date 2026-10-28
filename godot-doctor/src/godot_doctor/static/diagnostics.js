import { byId, element, location, number, setEmpty, severityBadge, text } from './view.js';

const PAGE_SIZE = 50;

export class Diagnostics {
  constructor(onSelect, onClear) {
    this.onSelect = onSelect;
    this.onClear = onClear;
    this.filters = [...document.querySelectorAll('[data-severity]')];
    for (const button of this.filters) {
      button.addEventListener('click', () => {
        this.severity = button.dataset.severity;
        this.updateFilter();
      });
    }
    byId('diagnostic-search').addEventListener('input', () => this.updateFilter());
    byId('clear-filters').addEventListener('click', () => {
      this.severity = 'all';
      byId('diagnostic-search').value = '';
      this.updateFilter();
      byId('diagnostic-search').focus();
    });
    byId('previous-page').addEventListener('click', () => this.changePage(-1));
    byId('next-page').addEventListener('click', () => this.changePage(1));
    this.setReport(null, null, 'idle');
  }

  setReport(report, runId, phase) {
    this.report = report;
    this.runId = runId;
    this.phase = phase;
    this.rows = (report?.diagnostics || []).map((diagnostic, index) => ({
      diagnostic, index,
      search: `${diagnostic.file}\n${diagnostic.message}\n${diagnostic.rule}`.toLowerCase(),
    }));
    this.severity = 'all';
    this.page = 0;
    this.selected = null;
    this.onClear(phase);
    byId('diagnostic-search').value = '';
    byId('diagnostic-search').disabled = !report;
    text('diagnostic-count', report ? number(this.rows.length) : '—');
    const counts = { all: this.rows.length, error: 0, warning: 0, info: 0 };
    for (const { diagnostic } of this.rows) counts[diagnostic.severity]++;
    for (const button of this.filters) {
      button.disabled = !report;
      button.querySelector('.filter-count').textContent = report ? number(counts[button.dataset.severity]) : '—';
    }
    this.render();
  }

  updateFilter() {
    this.page = 0;
    this.selected = null;
    this.onClear(this.phase);
    this.render();
  }

  changePage(delta) {
    const pages = Math.ceil(this.matches.length / PAGE_SIZE);
    this.page = Math.max(0, Math.min(pages - 1, this.page + delta));
    this.selected = null;
    this.onClear(this.phase);
    this.render();
    byId('diagnostic-list').querySelector('button')?.focus();
  }

  render() {
    const query = byId('diagnostic-search').value.trim().toLowerCase();
    this.matches = this.rows.filter(row => (this.severity === 'all' || row.diagnostic.severity === this.severity)
      && (!query || row.search.includes(query)));
    const activeFilter = this.severity !== 'all' || query !== '';
    byId('clear-filters').disabled = !this.report || !activeFilter;
    for (const button of this.filters) button.setAttribute('aria-pressed', String(button.dataset.severity === this.severity));
    const start = this.page * PAGE_SIZE;
    const visible = this.matches.slice(start, start + PAGE_SIZE);
    const fragment = document.createDocumentFragment();
    for (const row of visible) fragment.append(this.makeRow(row));
    byId('diagnostic-list').replaceChildren(fragment);
    byId('diagnostic-list').scrollTop = 0;
    byId('list-empty').hidden = visible.length > 0;
    byId('pagination').hidden = this.matches.length <= PAGE_SIZE;
    byId('previous-page').disabled = this.page === 0;
    byId('next-page').disabled = start + PAGE_SIZE >= this.matches.length;
    text('page-caption', `Page ${this.page + 1} of ${Math.max(1, Math.ceil(this.matches.length / PAGE_SIZE))}`);
    if (this.report) {
      text('list-caption', this.matches.length
        ? `Showing ${number(start + 1)}–${number(start + visible.length)} of ${number(this.matches.length)}${activeFilter ? ' matching' : ''} · ${number(this.rows.length)} total`
        : `${activeFilter ? '0 matching' : '0'} diagnostics · ${number(this.rows.length)} total`);
    } else {
      text('list-caption', this.phase === 'running' ? 'Waiting for scan results' : 'No active report');
    }
    if (!visible.length) this.renderEmpty(activeFilter);
  }

  makeRow({ diagnostic, index }) {
    const item = element('li');
    const button = element('button', 'diagnostic-row');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(this.selected === index));
    const top = element('span', 'row-top');
    const badge = element('span');
    severityBadge(badge, diagnostic.severity);
    top.append(badge, element('code', 'row-rule', diagnostic.rule));
    button.append(top, element('span', 'row-message', diagnostic.message),
      element('span', 'row-location', location(diagnostic)));
    button.addEventListener('click', () => {
      this.selected = index;
      for (const row of byId('diagnostic-list').querySelectorAll('button')) row.setAttribute('aria-pressed', String(row === button));
      this.onSelect(diagnostic, this.runId);
    });
    item.append(button);
    return item;
  }

  renderEmpty(activeFilter) {
    if (this.report && activeFilter) {
      setEmpty('filtered', 'No diagnostics match these filters', 'Try another search or clear the filters. Your complete report is still available to export.', '/');
    } else if (this.report) {
      setEmpty('clean', 'A clean report. A good place to start.', 'The analyzer found no diagnostics in this project. Keep testing in Godot: static analysis cannot validate runtime behavior.', '✓');
    } else if (this.phase === 'running') {
      setEmpty('running', 'Reading your project, not running it', 'Static analysis is in progress. Results will appear when the scan finishes; there is no estimated percentage.', '…');
    } else if (this.phase === 'failed') {
      setEmpty('failed', 'The scan didn’t finish', 'Review the error above, check the project path, and run the scan again. No previous diagnostics are shown.', '!');
    } else if (this.phase === 'offline') {
      setEmpty('offline', 'Waiting for the local analyzer', 'The local analyzer is not responding. Use Retry connection. Reconnecting restores the current scan without starting another.', '!');
    } else {
      setEmpty('idle', 'Your next clean build starts here', 'Choose a project and run a scan. Diagnostics will appear here, with the source context to help you investigate.');
    }
  }
}
