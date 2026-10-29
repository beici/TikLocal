(() => {
  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
  const grid = document.getElementById('collections-grid');
  const stateEl = document.getElementById('collections-state');
  const stateTitle = document.getElementById('collections-state-title');
  const stateCopy = document.getElementById('collections-state-copy');
  const stateAction = document.getElementById('collections-state-action');
  const openBtn = document.getElementById('open-create-collection');
  const modal = document.getElementById('create-modal');
  const cancelBtn = document.getElementById('cancel-create');
  const submitBtn = document.getElementById('submit-create');
  const nameInput = document.getElementById('create-name');
  const descInput = document.getElementById('create-description');
  const renameModal = document.getElementById('rename-modal');
  const renameCancelBtn = document.getElementById('cancel-rename');
  const renameSubmitBtn = document.getElementById('submit-rename');
  const renameInput = document.getElementById('rename-name');
  const collectionsById = new Map();
  let renamingCollectionId = '';
  let stateKind = 'loading';

  function showState(kind) {
    stateKind = kind;
    const states = {
      loading: [T('saved.state.loading_title'), T('saved.state.loading_copy'), '', 'loader'],
      empty: [T('saved.state.empty_title'), T('saved.state.empty_copy'), T('saved.state.empty_action'), 'layers'],
      error: [T('saved.state.error_title'), T('saved.state.error_copy'), T('library.status.reload'), 'wifi-off'],
    };
    const [title, copy, action, icon] = states[kind] || states.loading;
    stateTitle.textContent = title;
    stateCopy.textContent = copy;
    stateAction.textContent = action;
    stateAction.hidden = !action;
    const iconWrap = stateEl.querySelector('.collections-state-icon');
    iconWrap.innerHTML = `<i data-feather="${icon}"></i>`;
    stateEl.hidden = false;
    feather.replace();
  }

  async function api(url, options = {}) {
    // 连接挂起时没有超时兜底，首屏会永远停在 loading，重试入口也不会出现。
    const response = await fetch(url, options.signal ? options : { ...options, signal: AbortSignal.timeout(20000) });
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || T('saved.request_failed'));
    }
    return payload.data || {};
  }

  // aria-modal="true" promises that Tab cannot leave the dialog; without this the
  // second Tab landed on the masked page behind it, so keyboard users operated
  // controls they could not see.
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function trapFocus(container) {
    const onKeydown = (event) => {
      if (event.key !== 'Tab') return;
      const items = Array.from(container.querySelectorAll(FOCUSABLE))
        .filter((element) => element.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    container.addEventListener('keydown', onKeydown);
    return () => container.removeEventListener('keydown', onKeydown);
  }

  let releaseCreateTrap = null;
  let releaseRenameTrap = null;
  let createTrigger = null;
  let renameTrigger = null;

  function restoreFocus(element) {
    if (element && typeof element.focus === 'function' && document.contains(element)) {
      element.focus();
    }
  }

  function openCreateModal() {
    createTrigger = document.activeElement;
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    releaseCreateTrap = trapFocus(modal);
    nameInput.focus();
  }

  function closeCreateModal() {
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    nameInput.value = '';
    descInput.value = '';
    if (releaseCreateTrap) {
      releaseCreateTrap();
      releaseCreateTrap = null;
    }
    restoreFocus(createTrigger);
    createTrigger = null;
  }

  function openRenameModal(collectionId) {
    const id = String(collectionId || '').trim();
    const item = collectionsById.get(id);
    if (!id || !item) return;
    renamingCollectionId = id;
    renameTrigger = document.activeElement;
    renameInput.value = String(item.name || '').trim();
    renameModal.classList.add('active');
    renameModal.setAttribute('aria-hidden', 'false');
    releaseRenameTrap = trapFocus(renameModal);
    requestAnimationFrame(() => {
      renameInput.focus();
      renameInput.select();
    });
  }

  function closeRenameModal() {
    renameModal.classList.remove('active');
    renameModal.setAttribute('aria-hidden', 'true');
    renameInput.value = '';
    renamingCollectionId = '';
    if (releaseRenameTrap) {
      releaseRenameTrap();
      releaseRenameTrap = null;
    }
    restoreFocus(renameTrigger);
    renameTrigger = null;
  }

  function renderCards(items) {
    if (!Array.isArray(items) || !items.length) {
      grid.innerHTML = '';
      showState('empty');
      return;
    }
    stateEl.hidden = true;
    collectionsById.clear();
    grid.innerHTML = items.map((item) => {
      const id = String(item.id || '');
      collectionsById.set(id, item);
      const count = Number(item.item_count || 0);
      const detail = String(item.detail_url || '#');
      const rawName = String(item.name || '').trim() || T('saved.untitled');
      const name = escapeHtml(rawName);
      const previewItems = Array.isArray(item.preview_items) ? item.preview_items.slice(0, 4) : [];
      let coverHtml;
      if (!previewItems.length) {
        coverHtml = `<div class="collection-cover-empty"><i data-feather="folder"></i></div>`;
      } else {
        const tiles = previewItems.map((preview, index) => {
          const thumbUrl = escapeHtml(String(preview.thumb_url || ''));
          const isMainVideo = index === 0 && String(preview.type || '') === 'video';
          const videoMark = isMainVideo
            ? `<span class="collection-cover-video-mark" aria-hidden="true"><i data-feather="play"></i></span>`
            : '';
          return `<span class="collection-cover-tile"><img src="${thumbUrl}" alt="" loading="lazy" decoding="async" />${videoMark}</span>`;
        }).join('');
        coverHtml = `<div class="collection-cover-collage" data-count="${previewItems.length}">${tiles}</div>`;
      }
      return `
        <article class="collection-item" data-collection-id="${id}">
          <a class="collection-card" href="${detail}">
            <div class="collection-cover">${coverHtml}</div>
            <div class="collection-meta">
              <div class="collection-name">${name}</div>
              <div class="collection-sub">${T('library.media_items', { n: count })}</div>
            </div>
          </a>
          <div class="collection-menu" data-menu-id="${id}">
            <button class="collection-menu-btn" type="button" data-menu-toggle="1" data-id="${id}" aria-label="${escapeHtml(T('saved.more_actions', { name: rawName }))}" aria-expanded="false">
              <i data-feather="more-horizontal"></i>
            </button>
            <div class="collection-menu-panel">
              <button class="collection-menu-item" type="button" data-action="rename" data-id="${id}">${T('saved.rename.action')}</button>
              <button class="collection-menu-item danger" type="button" data-action="delete" data-id="${id}">${T('common.delete')}</button>
            </div>
          </div>
        </article>
      `;
    }).join('');
    feather.replace();
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function loadCollections() {
    showState('loading');
    try {
      const data = await api('/api/collections');
      renderCards(data.items || []);
    } catch (error) {
      // Leaving the spinner up turned a failed refresh into a dead page: the state
      // pane has a retry button only in the error state.
      showState('error');
      throw error;
    }
  }

  function closeMenus(exceptId = '') {
    grid.querySelectorAll('.collection-menu.open').forEach((node) => {
      if (exceptId && node.getAttribute('data-menu-id') === exceptId) return;
      node.classList.remove('open');
      node.querySelector('[data-menu-toggle]')?.setAttribute('aria-expanded', 'false');
    });
  }

  function toggleMenu(collectionId) {
    const id = String(collectionId || '').trim();
    if (!id) return;
    const target = grid.querySelector(`.collection-menu[data-menu-id="${id}"]`);
    if (!target) return;
    const shouldOpen = !target.classList.contains('open');
    closeMenus(id);
    target.classList.toggle('open', shouldOpen);
    target.querySelector('[data-menu-toggle]')?.setAttribute('aria-expanded', String(shouldOpen));
  }

  async function createCollection() {
    // The disabled button only stops mouse clicks; Enter in the name field calls this
    // directly, so two quick Enters used to POST twice and create two same-named
    // collections (the field is only cleared after the first response).
    if (submitBtn.disabled) return;
    const name = String(nameInput.value || '').trim();
    const description = String(descInput.value || '').trim();
    if (!name) return;
    submitBtn.disabled = true;
    submitBtn.textContent = T('saved.create.busy');
    try {
      await api('/api/collections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description }),
      });
      closeCreateModal();
      await loadCollections();
    } catch (error) {
      TikLocalUI.toast(error.message || T('saved.create.failed'), 'error');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = T('saved.create.submit');
    }
  }

  async function submitRenameCollection() {
    // Same Enter-vs-disabled gap as createCollection: a repeated PATCH is harmless
    // server-side but still two round trips.
    if (renameSubmitBtn.disabled) return;
    const collectionId = String(renamingCollectionId || '').trim();
    const name = String(renameInput.value || '').trim();
    if (!collectionId || !name) return;
    renameSubmitBtn.disabled = true;
    renameSubmitBtn.textContent = T('saved.rename.busy');
    try {
      await api(`/api/collections/${encodeURIComponent(collectionId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      closeRenameModal();
      await loadCollections();
    } catch (error) {
      TikLocalUI.toast(error.message || T('saved.rename.failed'), 'error');
    } finally {
      renameSubmitBtn.disabled = false;
      renameSubmitBtn.textContent = T('common.save');
    }
  }

  async function deleteCollection(collectionId) {
    const item = collectionsById.get(collectionId);
    if (!item) return;
    const ok = await TikLocalUI.confirm({
      title: T('saved.delete.title', { name: String(item.name || '').trim() || T('saved.untitled') }),
      message: T('saved.delete.copy'),
      confirmLabel: T('common.delete'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await api(`/api/collections/${encodeURIComponent(collectionId)}`, { method: 'DELETE' });
      await loadCollections();
    } catch (error) {
      TikLocalUI.toast(error.message || T('saved.delete.failed'), 'error');
    }
  }

  openBtn.addEventListener('click', openCreateModal);
  stateAction.addEventListener('click', () => {
    if (stateKind === 'empty') {
      openCreateModal();
      return;
    }
    loadCollections().catch(() => showState('error'));
  });
  cancelBtn.addEventListener('click', closeCreateModal);
  modal.addEventListener('click', (event) => {
    if (event.target === modal) closeCreateModal();
  });
  submitBtn.addEventListener('click', createCollection);
  nameInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      createCollection();
    }
  });
  grid.addEventListener('click', async (event) => {
    const menuToggle = event.target.closest('[data-menu-toggle][data-id]');
    if (menuToggle) {
      event.preventDefault();
      event.stopPropagation();
      const collectionId = String(menuToggle.getAttribute('data-id') || '').trim();
      toggleMenu(collectionId);
      return;
    }

    const button = event.target.closest('[data-action][data-id]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    closeMenus();
    const action = String(button.getAttribute('data-action') || '').trim();
    const collectionId = String(button.getAttribute('data-id') || '').trim();
    if (!action || !collectionId) return;
    if (action === 'rename') {
      openRenameModal(collectionId);
      return;
    }
    if (action === 'delete') {
      await deleteCollection(collectionId);
    }
  });

  document.addEventListener('click', (event) => {
    if (event.target.closest('.collection-menu')) return;
    closeMenus();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (renameModal.classList.contains('active')) {
      closeRenameModal();
      return;
    }
    if (modal.classList.contains('active')) {
      closeCreateModal();
      return;
    }
    closeMenus();
  });
  renameCancelBtn.addEventListener('click', closeRenameModal);
  renameModal.addEventListener('click', (event) => {
    if (event.target === renameModal) closeRenameModal();
  });
  renameSubmitBtn.addEventListener('click', submitRenameCollection);
  renameInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    submitRenameCollection();
  });

  // 断网时首屏只会停在错误态；网络恢复后自动重拉一次，不用用户手动点重试。
  window.addEventListener('tiklocal:online', () => {
    loadCollections().catch(() => showState('error'));
  });

  loadCollections().catch(() => showState('error'));
})();
