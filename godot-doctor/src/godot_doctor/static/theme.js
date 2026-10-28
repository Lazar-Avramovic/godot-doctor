(() => {
  'use strict';

  const STORAGE_KEY = 'godot-doctor-theme';
  const THEMES = ['system', 'light', 'dark'];
  const CANVAS = { light: '#f6f7f9', dark: '#101219' };
  const media = window.matchMedia('(prefers-color-scheme: dark)');

  function bridge() {
    return window.godotDoctor && typeof window.godotDoctor === 'object'
      ? window.godotDoctor : null;
  }

  function cookieValue() {
    let raw = '';
    try {
      raw = document.cookie;
    } catch {
      return null;
    }
    const prefix = `${STORAGE_KEY}=`;
    for (const part of raw.split(';')) {
      const entry = part.trim();
      if (entry.startsWith(prefix)) {
        try {
          const value = decodeURIComponent(entry.slice(prefix.length));
          return THEMES.includes(value) ? value : null;
        } catch {
          return null;
        }
      }
    }
    return null;
  }

  function stored() {
    // The cookie is host-scoped, so it survives the GUI's new port on every launch.
    const cookie = cookieValue();
    if (cookie) return cookie;
    try {
      const value = window.localStorage.getItem(STORAGE_KEY);
      if (THEMES.includes(value)) return value;
    } catch {
      /* A blocked storage area leaves the default in place. */
    }
    return null;
  }

  function initial() {
    const injected = bridge()?.initialTheme;
    if (THEMES.includes(injected)) return injected;
    return stored() || 'system';
  }

  let preference = initial();
  let currentTheme = null;

  function resolved() {
    return preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
  }

  function apply() {
    const theme = resolved();
    const root = document.documentElement;
    if (theme !== currentTheme) {
      // Controls animate their colors; suppress that for the frame the whole palette changes.
      root.dataset.themeSwitching = '';
      root.dataset.theme = theme;
      currentTheme = theme;
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
        delete root.dataset.themeSwitching;
      }));
    }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = CANVAS[theme];
    for (const button of document.querySelectorAll('[data-theme-choice]')) {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === preference));
    }
  }

  function setPreference(next) {
    if (!THEMES.includes(next)) return;
    preference = next;
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* A blocked storage area only costs persistence, never the theme itself. */
    }
    try {
      const secure = window.location.protocol === 'https:' ? '; Secure' : '';
      document.cookie = `${STORAGE_KEY}=${encodeURIComponent(next)}; Path=/; Max-Age=31536000; SameSite=Strict${secure}`;
    } catch {
      /* Cookies are only a cross-port fallback for the browser workbench. */
    }
    const pending = bridge()?.setTheme?.(next);
    if (pending && typeof pending.catch === 'function') pending.catch(() => {});
    apply();
  }

  function wire() {
    for (const button of document.querySelectorAll('[data-theme-choice]')) {
      button.addEventListener('click', () => setPreference(button.dataset.themeChoice));
    }
    apply();
  }

  apply();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire, { once: true });
  } else {
    wire();
  }

  const systemChanged = () => {
    if (preference === 'system') apply();
  };
  if (typeof media.addEventListener === 'function') {
    media.addEventListener('change', systemChanged);
  } else if (typeof media.addListener === 'function') {
    media.addListener(systemChanged);
  }

  window.addEventListener('storage', event => {
    if (event.key !== STORAGE_KEY) return;
    preference = THEMES.includes(event.newValue) ? event.newValue : 'system';
    apply();
  });
})();
