// Site-wide feedback: TikLocalUI.toast(message, type, action), await TikLocalUI.confirm({...}),
// and TikLocalUI.trash(deleteUrl, nextUrl), which offers Undo on the page it lands on.
(function () {
  var T = function (key, params) { return (window.TikLocalI18n && window.TikLocalI18n.t(key, params)) || key; };

  function icon(name) {
    var el = document.createElement('i');
    el.setAttribute('data-feather', name);
    return el;
  }

  var UNDO_KEY = 'tiklocal:undo';

  // The live region has to exist before the first message is inserted: a region and
  // its first child landing in the same frame is commonly not announced, so the very
  // first error toast (often the only one the user gets) was silent.
  function ensureToastWrap() {
    var wrap = document.querySelector('.ui-toast-wrap');
    if (wrap) return wrap;
    wrap = document.createElement('div');
    wrap.className = 'ui-toast-wrap';
    wrap.setAttribute('aria-live', 'polite');
    wrap.setAttribute('aria-atomic', 'false');
    document.body.appendChild(wrap);
    return wrap;
  }

  if (document.body) ensureToastWrap();
  else document.addEventListener('DOMContentLoaded', ensureToastWrap);

  // Browser network failures arrive as English TypeError text ("Failed to fetch",
  // "Load failed", …) and used to reach a zh-CN toast verbatim. Anything that looks
  // like one is replaced with localized copy; real API messages pass through.
  var NETWORK_TEXT = /^(Failed to fetch|Load failed|NetworkError when attempting to fetch resource\.?|Network request failed|The network connection was lost\.?|The operation was aborted\.?)$/i;

  function errorText(error, fallbackKey) {
    var fallback = T(fallbackKey || 'common.action_failed');
    if (!error) return fallback;
    var name = error.name || '';
    var message = typeof error === 'string' ? error : (error.message || '');
    if (name === 'AbortError' || name === 'TimeoutError') return T('common.error.timeout');
    // Match the *text*, not the class: fetch throws TypeError, but so does a plain
    // bug ("Cannot read properties of undefined"), and telling the user to check the
    // network because our own code broke is worse than showing the message.
    if (NETWORK_TEXT.test(String(message).trim())) return T('common.error.offline');
    return message || fallback;
  }

  function toastMessage(message) {
    if (message && typeof message === 'object' && typeof message.message === 'string') {
      return errorText(message);
    }
    var text = String(message == null ? '' : message);
    return NETWORK_TEXT.test(text.trim()) ? T('common.error.offline') : text;
  }

  // action: optional { label, run } rendered as a button; such toasts stay a little longer.
  function toast(message, type, action) {
    type = type || 'success';
    var wrap = ensureToastWrap();
    var el = document.createElement('div');
    el.className = 'ui-toast ' + type;
    var copy = document.createElement('span');
    copy.textContent = toastMessage(message);
    el.append(
      icon(type === 'error' ? 'alert-circle' : type === 'warning' ? 'alert-triangle' : type === 'info' ? 'info' : 'check-circle'),
      copy,
    );
    function leave() {
      el.classList.add('is-leaving');
      setTimeout(function () { el.remove(); }, 180);
    }
    if (action) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'ui-toast-action';
      button.textContent = action.label;
      button.addEventListener('click', function once() {
        // The toast stays clickable for the 180 ms leave animation; a double
        // click (or a held Enter) fired the action twice, so the second restore
        // request answered 404 and its error toast covered the success one.
        button.removeEventListener('click', once);
        button.disabled = true;
        leave();
        action.run();
      });
      el.append(button);
    }
    // replaceChildren() dropped a pending Undo toast whenever a later
    // informational toast (e.g. "resumed at 0:30") arrived, taking the only way
    // back after a delete with it. Informational toasts now stack instead.
    if (!action) {
      Array.prototype.slice.call(wrap.children).forEach(function (child) {
        if (!child.querySelector('.ui-toast-action')) child.remove();
      });
    }
    wrap.appendChild(el);
    if (window.feather) feather.replace();
    setTimeout(leave, action ? 6000 : 3000);
  }

  function readJson(response) {
    return response.json().catch(function () { return {}; }).then(function (data) {
      if (!response.ok) throw new Error(data.error || '');
      return data;
    });
  }

  // Moves a file to the trash, then continues to nextUrl where the Undo toast appears.
  // Resolves true once the file is in the trash (the page is being replaced by then)
  // and false when the request failed and the toast was already shown: callers that
  // had to release a player to make the move possible rely on that answer.
  function trash(deleteUrl, nextUrl) {
    return fetch(deleteUrl, { method: 'POST', headers: { Accept: 'application/json' } })
      .then(readJson)
      .then(function (entry) {
        try { sessionStorage.setItem(UNDO_KEY, JSON.stringify({ id: entry.id, at: Date.now() })); } catch (_) {}
        location.replace(nextUrl || '/library');
        return true;
      })
      .catch(function (error) { toast(error.message || T('common.delete_failed'), 'error'); return false; });
  }

  function offerUndo() {
    var entry = null;
    try {
      entry = JSON.parse(sessionStorage.getItem(UNDO_KEY));
      sessionStorage.removeItem(UNDO_KEY);
    } catch (_) {}
    if (!entry || Date.now() - entry.at > 10000) return;
    toast(T('common.moved_to_trash'), 'info', {
      label: T('common.undo'),
      run: function () {
        fetch('/api/trash/' + encodeURIComponent(entry.id) + '/restore', { method: 'POST' })
          .then(readJson)
          .then(function (data) { location.href = data.url; })
          .catch(function (error) { toast(error.message || T('common.restore_failed'), 'error'); });
      },
    });
  }

  // Resolves true when confirmed; Escape, Cancel and the backdrop resolve false.
  function confirm(options) {
    options = options || {};
    return new Promise(function (resolve) {
      var previousFocus = document.activeElement;
      var mask = document.createElement('div');
      mask.className = 'ui-dialog-mask';
      mask.innerHTML =
        '<section class="ui-dialog" role="alertdialog" aria-modal="true" aria-labelledby="ui-dialog-title" aria-describedby="ui-dialog-copy">' +
          '<h2 id="ui-dialog-title" class="ui-dialog-title"></h2>' +
          '<p id="ui-dialog-copy" class="ui-dialog-copy"></p>' +
          '<div class="ui-dialog-actions">' +
            '<button type="button" class="ui-button" data-cancel></button>' +
            '<button type="button" class="ui-button" data-accept></button>' +
          '</div>' +
        '</section>';
      mask.querySelector('.ui-dialog-title').textContent = options.title || T('common.are_you_sure');
      var copy = mask.querySelector('.ui-dialog-copy');
      if (options.message) copy.textContent = options.message;
      else copy.remove();
      var cancel = mask.querySelector('[data-cancel]');
      var accept = mask.querySelector('[data-accept]');
      cancel.textContent = options.cancelLabel || T('common.cancel');
      accept.textContent = options.confirmLabel || T('common.confirm');
      accept.classList.add(options.tone === 'danger' ? 'is-danger' : 'is-primary');

      function close(result) {
        document.removeEventListener('keydown', onKey, true);
        mask.remove();
        if (previousFocus && previousFocus.focus) previousFocus.focus();
        resolve(result);
      }
      function onKey(event) {
        if (event.key === 'Escape') {
          event.stopPropagation();
          close(false);
        } else if (event.key === 'Tab') {
          event.preventDefault();
          (document.activeElement === cancel ? accept : cancel).focus();
        }
      }

      cancel.addEventListener('click', function () { close(false); });
      accept.addEventListener('click', function () { close(true); });
      mask.addEventListener('click', function (event) { if (event.target === mask) close(false); });
      document.addEventListener('keydown', onKey, true);
      document.body.appendChild(mask);
      cancel.focus();
    });
  }

  window.TikLocalUI = { toast: toast, confirm: confirm, trash: trash, errorText: errorText };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', offerUndo);
  else offerUndo();
})();
