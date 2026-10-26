(function (global) {
  'use strict';

  // Douyin short-video mode is a per-device preference stored by the settings page.
  // When it is on, every entry point that would open the native player or the image
  // viewer is redirected into the vertical flow feed instead, so the whole app keeps
  // the same Douyin-style experience.
  var STORAGE_KEY = 'douyin_mode';
  var DETAIL_PREFIX = '/detail/';
  var IMAGE_PREFIX = '/image';

  function enabled() {
    try {
      return global.localStorage.getItem(STORAGE_KEY) === '1';
    } catch (error) {
      return false;
    }
  }

  function sourceId(uri) {
    var match = /^@([^/]+)\//.exec(String(uri || ''));
    return match ? match[1] : '';
  }

  function flowUrl(uri) {
    var source = sourceId(uri);
    if (!source) return '';
    return '/flow?blog=' + encodeURIComponent(source) + '&start=' + encodeURIComponent(uri);
  }

  function uriFromHref(href) {
    var value = String(href || '');
    if (!value) return '';
    try {
      var url = new URL(value, global.location.origin);
      if (url.origin !== global.location.origin) return '';
      if (url.pathname.indexOf(DETAIL_PREFIX) === 0) {
        return decodeURIComponent(url.pathname.slice(DETAIL_PREFIX.length));
      }
      if (url.pathname === IMAGE_PREFIX) {
        return url.searchParams.get('uri') || '';
      }
    } catch (error) {
      return '';
    }
    return '';
  }

  function redirect(uri) {
    var target = flowUrl(uri);
    if (!target) return false;
    global.location.replace(target);
    return true;
  }

  function intercept(event) {
    if (!enabled() || event.defaultPrevented) return;
    if (event.button !== undefined && event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    // The feed itself already is Douyin mode; never bounce its own links.
    if (global.location.pathname === '/flow') return;
    var anchor = event.target && event.target.closest ? event.target.closest('a[href]') : null;
    if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
    var uri = uriFromHref(anchor.getAttribute('href'));
    if (!uri) return;
    if (redirect(uri)) event.preventDefault();
  }

  // 常驻挂载：intercept 自己会查 enabled()，这样在设置页刚打开开关、不刷新也能立刻生效。
  document.addEventListener('click', intercept, false);

  global.TikLocalDouyinMode = {
    enabled: enabled,
    flowUrl: flowUrl,
    uriFromHref: uriFromHref,
    redirect: redirect,
    sourceId: sourceId
  };
})(window);
