/* Client-side i18n: reads the server-rendered catalog for the active locale. */
(function () {
  'use strict';
  const catalog = window.__TIKLOCAL_I18N__ || {};

  function t(key, params) {
    let text = catalog[key];
    if (text === undefined) return key;
    if (params && typeof params === 'object') {
      try {
        text = text.replace(/\{(\w+)\}/g, (match, name) =>
          Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
        );
      } catch (error) {
        /* keep the raw template on malformed params */
      }
    }
    return text;
  }

  // The server renders the catalog for the locale it resolved from the cookie, so
  // this key is only a hint for pages whose <html lang> is missing; it is checked
  // against the same whitelist as the server instead of being trusted verbatim.
  const SUPPORTED = ['zh', 'en'];
  function storedLocale() {
    try {
      const value = window.localStorage.getItem('tiklocal:lang');
      return SUPPORTED.indexOf(value) === -1 ? '' : value;
    } catch (error) {
      return '';
    }
  }

  window.TikLocalI18n = { t, locale: document.documentElement.lang || storedLocale() || 'zh' };
})();
