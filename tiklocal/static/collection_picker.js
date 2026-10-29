(function (global) {
  'use strict';

  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;

  var folderIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>';
  var checkIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
  // Ignore the click that opened the sheet so it does not land on the backdrop and close it again.
  var openGuardMs = 520;

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function request(url, options) {
    var response = await fetch(url, options || {});
    var payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok || !(payload && payload.success)) {
      throw new Error((payload && payload.error) || T('flow.error.request'));
    }
    return payload.data || {};
  }

  function sendUris(collectionId, method, uri) {
    return request('/api/collections/' + encodeURIComponent(collectionId) + '/items', {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uris: [uri] }),
    });
  }

  // One "Add to Collection" sheet bound to the media currently on screen.
  function createPicker(opts) {
    var button = opts.button;
    var countEl = opts.countEl;
    var modal = opts.modal;
    var meta = opts.meta;
    var createBtn = opts.createBtn;
    var nameInput = opts.nameInput;
    var list = opts.list;
    var itemClass = opts.itemClass;
    var emptyClass = opts.emptyClass;
    var currentUri = function () { return String(opts.currentUri() || ''); };

    var cache = new Map();
    var catalog = [];
    var selectedIds = new Set();
    var openedAt = 0;
    var lastFocused = null;

    function showNames(names) {
      var count = names.length;
      var preview = names.slice(0, 2).join(', ');
      var rest = count - 2;
      var label = rest > 0
        ? T('flow.collection.in_list_more', { names: preview, rest: rest })
        : T('flow.collection.in_list', { names: preview });
      button.classList.toggle('has-collection', count > 0);
      countEl.textContent = count > 99 ? '99+' : String(count);
      button.title = count > 0 ? label : T('flow.collection.add');
      if (meta) meta.textContent = count > 0 ? label : T('flow.collection.not_in_any');
    }

    function showState(state) {
      selectedIds = new Set(state.ids);
      showNames(state.names);
    }

    async function membership(uri, force) {
      if (!force && cache.has(uri)) return cache.get(uri);
      var data = await request('/api/collections/by-media?uri=' + encodeURIComponent(uri));
      var state = { ids: [], names: [] };
      (Array.isArray(data.items) ? data.items : []).forEach(function (entry) {
        var id = String((entry && entry.id) || '').trim();
        var name = String((entry && entry.name) || '').trim();
        if (id) state.ids.push(id);
        if (name) state.names.push(name);
      });
      cache.set(uri, state);
      return state;
    }

    async function sync(uri, force) {
      var key = String(uri || '');
      if (!key) {
        showState({ ids: [], names: [] });
        return;
      }
      var state = { ids: [], names: [] };
      try {
        state = await membership(key, !!force);
      } catch (error) {
        // Unknown membership shows as none rather than a stale collection count.
      }
      if (currentUri() === key) showState(state);
    }

    function isOpen() {
      return modal.classList.contains('active');
    }

    function close() {
      modal.classList.remove('active');
      modal.setAttribute('aria-hidden', 'true');
      createBtn.disabled = false;
      openedAt = 0;
      // Return focus the way the dialog took it: the trigger is otherwise lost to a
      // keyboard user, whose next Tab would start again from the top of the page.
      if (lastFocused && document.contains(lastFocused)) {
        try { lastFocused.focus(); } catch (error) { /* element went away */ }
      }
      lastFocused = null;
    }

    function showMessage(text) {
      list.innerHTML = '<div class="' + emptyClass + '">' + text + '</div>';
    }

    function render() {
      if (!catalog.length) {
        showMessage(T('flow.collection.empty'));
        return;
      }
      list.innerHTML = catalog.map(function (item) {
        var id = String(item.id || '');
        var count = Number(item.item_count || 0);
        var selected = selectedIds.has(id);
        return '<label class="' + itemClass + (selected ? ' is-selected' : '') + '" data-id="' + escapeHtml(id) + '">'
          + '<span class="coll-icon">' + folderIcon + '</span>'
          + '<span class="coll-name">' + escapeHtml(String(item.name || T('flow.collection.untitled'))) + '</span>'
          + '<em class="coll-count">' + (count > 0 ? count : '') + '</em>'
          + '<span class="coll-check">' + checkIcon + '</span>'
          + '<input type="checkbox"' + (selected ? ' checked' : '') + ' />'
          + '</label>';
      }).join('');
    }

    async function open() {
      var uri = currentUri();
      if (!uri) return;
      openedAt = Date.now();
      // The sheet is aria-modal; focus has to move into it or a screen-reader user
      // never learns it opened and keyboard focus stays behind the backdrop.
      lastFocused = document.activeElement;
      modal.classList.add('active');
      modal.setAttribute('aria-hidden', 'false');
      if (nameInput) {
        try { nameInput.focus(); } catch (error) { /* hidden card */ }
      }
      showMessage(T('flow.collection.loading'));
      try {
        var results = await Promise.all([request('/api/collections'), membership(uri, true)]);
        catalog = Array.isArray(results[0].items) ? results[0].items : [];
        showState(results[1]);
        render();
      } catch (error) {
        showMessage(T('flow.error.load'));
      }
    }

    async function create() {
      // Enter in the name field calls this directly, so the disabled button alone did
      // not stop a double submit: two Enters created two same-named collections.
      if (createBtn.disabled) return;
      var name = String(nameInput.value || '').trim();
      if (!name) return;
      createBtn.disabled = true;
      var createdId = '';
      try {
        var created = await request('/api/collections', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name }),
        });
        createdId = String((created.item && created.item.id) || '').trim();
        // Clear the field as soon as the collection exists: leaving the name in place
        // made the obvious "try again" create a second collection with the same name.
        nameInput.value = '';
        var uri = currentUri();
        if (createdId && uri) {
          try {
            await sendUris(createdId, 'POST', uri);
          } catch (error) {
            // The collection exists but this item is not in it; say so instead of
            // swallowing it, or the next attempt duplicates the collection.
            showMessage(T('flow.collection.add_failed'));
          }
        }
        await open();
      } catch (error) {
        if (!createdId) {
          // The POST itself failed: the typed name stays in place so it can be retried.
          nameInput.value = name;
        }
      } finally {
        createBtn.disabled = false;
      }
    }

    async function toggle(collectionId, checked, rowEl, inputEl) {
      var uri = currentUri();
      if (!uri) return;
      rowEl.classList.add('is-pending');
      inputEl.disabled = true;
      try {
        await sendUris(collectionId, checked ? 'POST' : 'DELETE', uri);
        await sync(uri, true);
        catalog = catalog.map(function (entry) {
          if (String((entry && entry.id) || '') !== collectionId) return entry;
          var count = Number(entry.item_count || 0);
          return Object.assign({}, entry, { item_count: checked ? count + 1 : Math.max(0, count - 1) });
        });
        render();
      } catch (error) {
        inputEl.checked = !checked;
      } finally {
        rowEl.classList.remove('is-pending');
        inputEl.disabled = false;
      }
    }

    button.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      open();
    });
    button.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      // Holding the key repeated open(): each open fires two GETs and rebuilds the
      // rows, which also wiped the pending state of a row mid-toggle.
      if (event.repeat) return;
      event.preventDefault();
      open();
    });
    opts.closeBtn.addEventListener('click', function (event) {
      event.preventDefault();
      close();
    });
    modal.addEventListener('click', function (event) {
      if (openedAt && Date.now() - openedAt < openGuardMs) return;
      if (event.target === modal) close();
    });
    createBtn.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      create();
    });
    nameInput.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      create();
    });
    list.addEventListener('change', function (event) {
      var inputEl = event.target.closest('input[type="checkbox"]');
      var rowEl = inputEl && inputEl.closest('[data-id]');
      var collectionId = String((rowEl && rowEl.getAttribute('data-id')) || '').trim();
      if (collectionId) toggle(collectionId, !!inputEl.checked, rowEl, inputEl);
    });

    return { sync: sync, open: open, close: close, isOpen: isOpen };
  }

  global.TikLocalCollections = { request: request, createPicker: createPicker };
})(window);
