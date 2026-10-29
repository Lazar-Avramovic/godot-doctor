import { api } from './api.js';
import { byId, element, number, text } from './view.js';

function folderIcon() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('class', 'folder-icon');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', 'M2 5a1 1 0 0 1 1-1h5l2 2h7a1 1 0 0 1 1 1v9H2Z');
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'currentColor');
  path.setAttribute('stroke-width', '1.5');
  svg.append(path);
  return svg;
}

export class DirectoryPicker {
  constructor(onSelect) {
    this.onSelect = onSelect;
    this.ticket = 0;
    this.current = null;
    this.dialog = byId('directory-dialog');
    byId('directory-form').addEventListener('submit', event => {
      event.preventDefault();
      this.load(byId('directory-path').value);
    });
    byId('directory-path').addEventListener('input', () => {
      this.updateSelection();
      if (this.current && byId('directory-path').value !== this.current.path) {
        this.notice('Location edited. Press Go to open it before selecting a folder.');
      }
    });
    byId('directory-up').addEventListener('click', () => {
      if (this.current?.parent) this.load(this.current.parent);
    });
    byId('directory-retry').addEventListener('click', () => this.load(byId('directory-path').value));
    byId('directory-select').addEventListener('click', () => {
      if (!this.current || byId('directory-select').disabled) return;
      this.onSelect(this.current.path);
      this.dialog.close();
    });
    this.dialog.addEventListener('close', () => {
      this.ticket++;
      this.controller?.abort();
      this.current = null;
    });
  }

  configure(config) {
    this.initialPath = config.initial_path;
    const fragment = document.createDocumentFragment();
    for (const root of config.roots) {
      const button = element('button', 'button button-quiet button-small', root.label);
      button.type = 'button';
      button.title = root.path;
      button.addEventListener('click', () => this.load(root.path));
      fragment.append(button);
    }
    byId('root-shortcuts').replaceChildren(fragment);
    byId('root-shortcuts').hidden = config.roots.length === 0;
  }

  open(path) {
    if (this.dialog.open) return;
    const initial = path.trim() ? path : this.initialPath || '';
    byId('directory-path').value = initial;
    this.dialog.showModal();
    byId('directory-path').focus();
    this.load(initial);
  }

  notice(message, error = false) {
    text('browse-notice', message);
    byId('browse-notice').dataset.error = String(error);
  }

  updateSelection() {
    byId('directory-select').disabled = !this.current || byId('directory-path').value !== this.current.path;
    byId('directory-up').disabled = !this.current?.parent;
  }

  async load(candidate) {
    const path = candidate;
    this.controller?.abort();
    this.controller = new AbortController();
    const ticket = ++this.ticket;
    const isCurrent = () => ticket === this.ticket && this.dialog.open;
    this.current = null;
    byId('directory-path').value = path;
    this.updateSelection();
    byId('directory-list').replaceChildren();
    byId('directory-list').setAttribute('aria-busy', 'true');
    byId('browse-current').hidden = true;
    byId('directory-retry').hidden = true;
    if (!path) {
      this.notice('Enter a folder path or choose a location shortcut. No folder has been opened.', true);
      byId('directory-list').setAttribute('aria-busy', 'false');
      return;
    }
    this.notice(`Opening ${path}…`);
    try {
      const result = await api.browse(path, this.controller.signal);
      if (!isCurrent()) return;
      this.current = result;
      // Do not overwrite a new location typed while this request was in flight.
      if (byId('directory-path').value === path) byId('directory-path').value = result.path;
      text('browse-current-path', result.path);
      byId('browse-current').hidden = false;
      byId('browse-project-badge').hidden = !result.is_project;
      const fragment = document.createDocumentFragment();
      for (const directory of result.directories) {
        const item = element('li');
        const button = element('button', 'directory-button');
        button.type = 'button';
        button.append(folderIcon(), element('span', 'directory-name', directory.name));
        if (directory.is_project) button.append(element('span', 'badge project-badge', 'Godot project'));
        if (directory.accessible === false) {
          button.disabled = true;
          button.append(element('span', 'badge', 'Unavailable'));
        }
        const arrow = element('span', 'folder-symbol', '›');
        arrow.setAttribute('aria-hidden', 'true');
        button.append(arrow);
        button.addEventListener('click', () => this.load(directory.path));
        item.append(button);
        fragment.append(item);
      }
      byId('directory-list').replaceChildren(fragment);
      const total = result.directories.length;
      const unreadable = result.unreadable_count
        ? ` ${number(result.unreadable_count)} ${result.unreadable_count === 1 ? 'entry' : 'entries'} could not be inspected.`
        : '';
      this.notice((total
        ? `${number(total)} ${total === 1 ? 'subfolder' : 'subfolders'}. Open a folder to explore, or select the current folder.`
        : 'No subfolders here. You can select this folder, go up, or enter a different location.') + unreadable);
      if (byId('directory-path').value !== result.path) {
        this.notice('Location edited. Press Go to open it before selecting a folder.');
      }
    } catch (error) {
      if (!isCurrent()) return;
      this.notice(`Could not open this folder: ${error.message} Edit the location or try a shortcut.`, true);
      byId('directory-retry').hidden = false;
    } finally {
      if (isCurrent()) {
        byId('directory-list').setAttribute('aria-busy', 'false');
        this.updateSelection();
      }
    }
  }
}
