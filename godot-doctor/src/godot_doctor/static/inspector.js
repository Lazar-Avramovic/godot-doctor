import { api } from './api.js';
import { byId, element, location, positiveLine, severityBadge, text } from './view.js';

export class Inspector {
  constructor() {
    this.generation = 0;
    this.sourceTicket = 0;
    byId('copy-location').addEventListener('click', () => this.copy());
    byId('source-retry').addEventListener('click', () => {
      if (this.source) this.loadSource(this.source.file, this.source.line);
    });
    this.reset();
  }

  reset(phase = 'idle') {
    this.controller?.abort();
    this.generation++;
    this.sourceTicket++;
    this.item = null;
    this.source = null;
    this.runId = null;
    byId('inspector-content').hidden = true;
    byId('inspector-empty').hidden = false;
    byId('source-code').hidden = true;
    byId('source-code').setAttribute('aria-busy', 'false');
    byId('source-retry').hidden = true;
    byId('excerpt-note').hidden = true;
    byId('related-section').hidden = true;
    byId('source-lines').replaceChildren();
    byId('related-list').replaceChildren();
    byId('copy-location').disabled = true;
    for (const id of ['detail-message', 'detail-rule', 'detail-location', 'source-file',
      'source-feedback', 'copy-feedback', 'excerpt-note']) text(id, '');
    const waiting = phase === 'running';
    text('inspector-empty-title', waiting ? 'A little context is on its way' : 'Context, not guesswork');
    text('inspector-empty-detail', waiting
      ? 'When the scan finishes, select a diagnostic to inspect the relevant source. Your files stay untouched.'
      : phase === 'failed' || phase === 'offline'
        ? 'No source is displayed. Complete a scan, then select a diagnostic to inspect its source.'
        : 'Select a diagnostic to see its message, location, source excerpt, and related files. Source is always read-only.');
  }

  select(diagnostic, runId) {
    this.reset();
    this.item = diagnostic;
    this.runId = runId;
    byId('inspector-empty').hidden = true;
    byId('inspector-content').hidden = false;
    severityBadge(byId('detail-severity'), diagnostic.severity);
    text('detail-rule', diagnostic.rule);
    text('detail-message', diagnostic.message);
    text('detail-location', location(diagnostic));
    byId('copy-location').disabled = false;
    const related = diagnostic.related || [];
    if (related.length) {
      byId('related-section').hidden = false;
      const choices = [
        { file: diagnostic.file, line: positiveLine(diagnostic.line), label: 'Diagnostic source' },
        ...related.map(file => ({ file, line: 1, label: file })),
      ];
      for (const choice of choices) {
        const button = element('button', 'button button-quiet related-button', `${choice.label} · line ${choice.line}`);
        button.type = 'button';
        button.setAttribute('aria-pressed', String(choice === choices[0]));
        button.addEventListener('click', () => {
          for (const sibling of byId('related-list').children) sibling.setAttribute('aria-pressed', 'false');
          button.setAttribute('aria-pressed', 'true');
          this.loadSource(choice.file, choice.line);
        });
        byId('related-list').append(button);
      }
    }
    this.loadSource(diagnostic.file, positiveLine(diagnostic.line));
    if (window.matchMedia('(max-width: 1180px)').matches) byId('inspector-heading').focus();
  }

  async loadSource(file, line) {
    // Every caller uses only the selected diagnostic's file or its related list.
    if (!this.item || (file !== this.item.file && !(this.item.related || []).includes(file))) return;
    this.controller?.abort();
    this.controller = new AbortController();
    const ticket = ++this.sourceTicket;
    const generation = this.generation;
    const runId = this.runId;
    const isCurrent = () => ticket === this.sourceTicket && generation === this.generation;
    this.source = { file, line: positiveLine(line) };
    text('source-file', `${file}:${this.source.line}`);
    text('source-feedback', 'Loading read-only source excerpt…');
    byId('source-feedback').hidden = false;
    byId('source-lines').replaceChildren();
    byId('source-code').hidden = true;
    byId('source-code').setAttribute('aria-busy', 'true');
    byId('source-retry').hidden = true;
    byId('excerpt-note').hidden = true;
    try {
      const source = await api.source(file, this.source.line, runId, this.controller.signal);
      if (!isCurrent()) return;
      if (source.run_id !== runId) throw new Error('The report changed. Run a new scan to refresh this source.');
      const fragment = document.createDocumentFragment();
      for (const entry of source.lines) {
        const row = element('span', 'source-line');
        row.classList.toggle('is-highlighted', entry.number === this.source.line);
        const number = element('span', 'line-number', String(entry.number));
        number.setAttribute('aria-hidden', 'true');
        row.append(number, element('span', 'source-text', entry.text));
        fragment.append(row);
      }
      byId('source-lines').replaceChildren(fragment);
      byId('source-code').hidden = source.lines.length === 0;
      text('source-feedback', source.lines.length ? '' : 'This file has no source lines to display.');
      byId('source-feedback').hidden = source.lines.length > 0;
      const first = source.lines[0]?.number;
      const last = source.lines.at(-1)?.number;
      const span = first === last ? `line ${first}` : `lines ${first}–${last}`;
      const outside = [source.has_more_before ? 'earlier' : '', source.has_more_after ? 'later' : ''].filter(Boolean);
      text('excerpt-note', source.lines.length
        ? `Read-only excerpt · ${span}. ${outside.length ? `Additional ${outside.join(' and ')} lines are not shown.` : 'All available lines are shown.'} Requested line: ${this.source.line}.`
        : 'Read-only excerpt. No source changes are made.');
      byId('excerpt-note').hidden = false;
      const highlighted = byId('source-lines').querySelector('.is-highlighted');
      if (highlighted) byId('source-code').scrollTop = highlighted.offsetTop - byId('source-lines').offsetTop - 70;
    } catch (error) {
      if (!isCurrent()) return;
      text('source-feedback', `Source unavailable: ${error.message} The diagnostic above is still available.`);
      byId('source-retry').hidden = false;
    } finally {
      if (isCurrent()) {
        byId('source-code').setAttribute('aria-busy', 'false');
        byId('source-feedback').hidden = !byId('source-feedback').textContent;
      }
    }
  }

  async copy() {
    if (!this.item) return;
    const generation = this.generation;
    byId('copy-location').disabled = true;
    text('copy-feedback', 'Copying location…');
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard access is not available.');
      await navigator.clipboard.writeText(location(this.item));
      if (generation === this.generation) text('copy-feedback', 'Location copied.');
    } catch (error) {
      if (generation === this.generation) text('copy-feedback', `Could not copy: ${error.message} Select the location text to copy it manually.`);
    } finally {
      if (generation === this.generation) byId('copy-location').disabled = false;
    }
  }
}
