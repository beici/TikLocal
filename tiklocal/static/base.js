// Theme Manager
class ThemeManager {
  constructor() {
    this.init();
  }
  init() {
    this.mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    let savedTheme = null;
    try {
      savedTheme = localStorage.getItem('theme');
    } catch (error) {
      // 隐私模式/禁用存储时不要让整个初始化回调中断（否则抽屉菜单不会绑定）。
      savedTheme = null;
    }
    this.preference = ['light', 'dark', 'system'].includes(savedTheme) ? savedTheme : 'system';
    this.apply();
    const onSystemThemeChange = () => {
      if (this.preference === 'system') this.apply();
    };
    if (this.mediaQuery.addEventListener) {
      this.mediaQuery.addEventListener('change', onSystemThemeChange);
    } else {
      this.mediaQuery.addListener?.(onSystemThemeChange);
    }
    feather.replace();
  }
  resolvedTheme() {
    return this.preference === 'system'
      ? (this.mediaQuery.matches ? 'dark' : 'light')
      : this.preference;
  }
  apply() {
    const resolved = this.resolvedTheme();
    document.body.setAttribute('data-theme', resolved);
    document.querySelectorAll('[data-theme-sync]').forEach((element) => {
      element.setAttribute('data-theme', resolved);
    });
    window.dispatchEvent(new CustomEvent('tiklocal:theme-changed', {
      detail: { preference: this.preference, resolved }
    }));
  }
  setPreference(preference) {
    if (!['light', 'dark', 'system'].includes(preference)) return;
    this.preference = preference;
    localStorage.setItem('theme', preference);
    this.apply();
  }
}

class MoreMenu {
  constructor() {
    this.menu = document.getElementById('app-nav-menu');
    this.trigger = document.getElementById('app-nav-menu-trigger');
    this.onKeydown = null;
    if (this.menu) {
      this.menu.setAttribute('role', 'dialog');
      this.menu.setAttribute('aria-modal', 'true');
    }
    this.bindEvents();
  }
  isOpen() {
    return this.menu?.classList.contains('is-open');
  }
  focusables() {
    if (!this.menu) return [];
    return Array.from(this.menu.querySelectorAll('a[href], button:not([disabled])'))
      .filter((element) => element.offsetParent !== null || element === document.activeElement);
  }
  trapFocus(event) {
    if (event.key !== 'Tab') return;
    const focusables = this.focusables();
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  open() {
    if (!this.menu || !this.trigger) return;
    this.menu.classList.add('is-open');
    this.menu.removeAttribute('inert');
    this.menu.setAttribute('aria-hidden', 'false');
    this.trigger.setAttribute('aria-expanded', 'true');
    document.body.classList.add('app-nav-menu-open');
    this.onKeydown = (event) => this.trapFocus(event);
    document.addEventListener('keydown', this.onKeydown, true);
    this.menu.querySelector('a[href], button')?.focus();
  }
  close({ restoreFocus = true } = {}) {
    if (!this.menu || !this.trigger) return;
    this.menu.classList.remove('is-open');
    this.menu.setAttribute('inert', '');
    this.menu.setAttribute('aria-hidden', 'true');
    this.trigger.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('app-nav-menu-open');
    if (this.onKeydown) {
      document.removeEventListener('keydown', this.onKeydown, true);
      this.onKeydown = null;
    }
    if (restoreFocus) this.trigger.focus();
  }
  bindEvents() {
    this.trigger?.addEventListener('click', () => this.isOpen() ? this.close() : this.open());
    this.menu?.addEventListener('click', (event) => {
      if (event.target === this.menu) this.close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.isOpen()) this.close();
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.themeManager = new ThemeManager();
  window.moreMenu = new MoreMenu();
});

// bfcache 恢复时 DOM/JS 状态原样回来，DOMContentLoaded 不会再触发，抽屉会卡在展开态并锁住滚动。
window.addEventListener('pageshow', (event) => {
  if (!event.persisted) return;
  if (window.moreMenu) window.moreMenu.close({ restoreFocus: false });
  document.body.classList.remove('app-nav-menu-open');
});
