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
    // Changing the theme in one tab left every other tab on the old theme until
    // a manual reload; mirror the stored value when another tab writes it.
    window.addEventListener('storage', (event) => {
      if (event.key !== 'theme') return;
      const next = ['light', 'dark', 'system'].includes(event.newValue) ? event.newValue : 'system';
      if (next === this.preference) return;
      this.preference = next;
      this.apply();
    });
    feather.replace();
  }
  resolvedTheme() {
    return this.preference === 'system'
      ? (this.mediaQuery.matches ? 'dark' : 'light')
      : this.preference;
  }
  apply() {
    const resolved = this.resolvedTheme();
    // The first-paint script in base.html writes the attribute on <html>; writing it
    // on <body> here left the page shell (and every var() resolved from html) on the
    // old theme while only the dock followed, so a switch looked half-applied.
    document.documentElement.dataset.theme = resolved;
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
    try {
      localStorage.setItem('theme', preference);
    } catch (error) {
      // Storage can be disabled (private mode, blocked cookies). Reading was
      // already guarded; writing threw and aborted the caller, so the theme
      // buttons never re-synced even though the theme itself was applied.
    }
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

// 会话过期（30 天到期，或 `tiklocal auth set-password` 把 revision 加一）之后，
// 每个请求都只拿到 401 JSON，页面静默失灵、必须手动刷新才会被门禁送回登录页。
// 在这里统一把 401 变成一次跳转，页面自己的 catch 分支照旧执行也不会卡住。
(() => {
  const nativeFetch = window.fetch?.bind(window);
  if (!nativeFetch) return;
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (response.status === 401 && !window.__tiklocalRedirecting) {
      const target = `${window.location.pathname}${window.location.search}`;
      if (!window.location.pathname.startsWith('/login')) {
        window.__tiklocalRedirecting = true;
        window.location.href = `/login?next=${encodeURIComponent(target)}`;
      }
    }
    return response;
  };
})();

// 退出登录只清了服务端 cookie，浏览器里还留着上一个账号的收听记录与最近曲目
// （`radio_position` / `radio_recent_tracks` 存的是媒体库里的文件名，也就是私有路径）。
// 同一浏览器换个账号登录后，首页「继续收听」会显示上一个人的文件，电台还会把这些
// 名字拼进 `?exclude=` 发给服务端。两个退出入口（停靠栏与抽屉菜单）都是普通表单提交，
// 在提交前清掉即可；主题/语言/抖音模式是这台设备的偏好，与账号无关，保留。
(() => {
  const ACCOUNT_KEYS = ['radio_position', 'radio_recent_tracks'];
  document.querySelectorAll('form[action$="/logout"]').forEach((form) => {
    form.addEventListener('submit', () => {
      ACCOUNT_KEYS.forEach((key) => {
        try {
          localStorage.removeItem(key);
        } catch (error) {
          // 存储被禁用时本来就没有可清的东西。
        }
      });
    });
  });
})();

// 断网/切网后各页只做「报错 + 等用户动作」，网络恢复也不会自动重拉，用户会以为应用坏了。
// 这里只负责广播：页面监听 `tiklocal:online` 后重跑自己的首屏加载。
window.addEventListener('online', () => {
  window.dispatchEvent(new CustomEvent('tiklocal:online'));
});
window.addEventListener('offline', () => {
  window.dispatchEvent(new CustomEvent('tiklocal:offline'));
});

// 缩略图/封面 404 时浏览器会显示破图图标（alt 为空，读屏也没有提示）。
// 用捕获阶段统一兜底：隐藏图片并给父节点留一个占位底色，覆盖所有网格与卡片。
document.addEventListener(
  'error',
  (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement)) return;
    if (img.dataset.fallbackApplied) return;
    img.dataset.fallbackApplied = '1';
    img.style.visibility = 'hidden';
    const parent = img.parentElement;
    if (parent && !parent.dataset.imageFallback) {
      parent.dataset.imageFallback = '1';
      parent.style.background = 'var(--surface-muted, rgba(127, 127, 127, 0.12))';
    }
  },
  true,
);

// 重试成功后同一个 <img> 会再触发 load（状态栏「重试」换 src、断网自动重试、搜索卡片
// 换兜底头像都走这条路）。隐藏和底色原本是单向的，于是图已经解码成功却永久空白；
// load 时把 error 那一步撤销即可。
document.addEventListener(
  'load',
  (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement)) return;
    if (!img.dataset.fallbackApplied) return;
    delete img.dataset.fallbackApplied;
    img.style.visibility = '';
    const parent = img.parentElement;
    if (parent && parent.dataset.imageFallback) {
      delete parent.dataset.imageFallback;
      parent.style.background = '';
    }
  },
  true,
);

