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

  window.TikLocalI18n = { t, locale: document.documentElement.lang || 'zh' };
})();
