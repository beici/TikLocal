/* Author profile page: back navigation and sidecar profile sync with an
   account picker, because Douyin nicknames are not unique. */
(function () {
  'use strict';

  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
  const page = document.querySelector('[data-author-page]');
  if (!page) return;

  const sourceId = page.dataset.sourceId || '';
  // One blogger inside a shared root: the API calls and the Flow deep link both
  // need the folder, or a folder page would sync into the root's cache entry and
  // tapping a work would hand the reader the whole root's timeline.
  const folder = page.dataset.authorFolder || '';
  const endpoint = (action) => {
    const base = `/api/author/${encodeURIComponent(sourceId)}/${action}`;
    return folder ? `${base}?folder=${encodeURIComponent(folder)}` : base;
  };

  // In Douyin mode a tap on a work should start that author's timeline and keep
  // scrolling it, exactly like tapping a work on a Douyin profile.
  if (window.localStorage?.getItem('douyin_mode') === '1') {
    page.querySelectorAll('[data-tile-uri]').forEach((tile) => {
      const uri = tile.dataset.tileUri || '';
      if (!uri) return;
      const query = new URLSearchParams({ start: uri });
      if (sourceId) query.set('blog', sourceId);
      if (folder) query.set('folder', folder);
      tile.href = `/flow?${query.toString()}`;
    });
  }

  const backButton = page.querySelector('[data-author-back]');
  if (backButton) {
    backButton.addEventListener('click', () => {
      if (window.history.length > 1) {
        window.history.back();
        return;
      }
      window.location.assign('/flow');
    });
  }

  const syncButton = page.querySelector('[data-author-sync]');
  const picker = page.querySelector('[data-author-picker]');
  const sheet = page.querySelector('[data-author-sheet]');
  if (!syncButton || !picker || !sheet) return;

  const alertBox = page.querySelector('[data-author-alert]');
  const form = picker.querySelector('[data-author-search-form]');
  const input = picker.querySelector('[data-author-keyword]');
  const searchButton = picker.querySelector('.author-picker-search');
  const urlInput = picker.querySelector('[data-author-profile-url]');
  const urlButton = picker.querySelector('[data-author-url-sync]');
  const list = picker.querySelector('[data-author-candidates]');
  const hint = picker.querySelector('[data-author-hint]');
  const sheetProfile = sheet.querySelector('[data-author-sheet-profile]');
  const sheetVideos = sheet.querySelector('[data-author-sheet-videos]');
  const sheetHint = sheet.querySelector('[data-author-sheet-hint]');
  const boundLine = sheet.querySelector('[data-author-bound]');
  let boundUrl = page.dataset.authorBoundUrl || '';
  let busy = false;

  function toast(message, type) {
    if (window.TikLocalUI?.toast) {
      window.TikLocalUI.toast(message, type);
      return;
    }
    if (type === 'error') showPageError(message);
  }

  function renderBound() {
    if (!boundLine) return;
    const template = boundLine.dataset.boundTemplate || '{url}';
    boundLine.textContent = boundUrl
      ? template.replace('{url}', boundUrl)
      : boundLine.dataset.unboundText || '';
    page.dataset.authorBoundUrl = boundUrl;
    if (urlInput && !urlInput.value.trim()) urlInput.value = boundUrl;
  }

  function setBusy(value) {
    busy = value;
    if (searchButton) searchButton.disabled = value;
    if (input) input.disabled = value;
    if (urlButton) urlButton.disabled = value;
    if (urlInput) urlInput.disabled = value;
    list?.classList.toggle('is-busy', value);
  }

  function showHint(text) {
    if (!hint) return;
    hint.textContent = text || '';
    hint.hidden = !text;
  }

  function showPageError(text) {
    if (!alertBox) return;
    alertBox.textContent = text || T('author.refresh.error');
    alertBox.hidden = false;
  }

  function compactCount(value) {
    const count = Number(value);
    if (!Number.isFinite(count) || count <= 0) return '0';
    if (count < 10000) return String(Math.round(count));
    if ((document.documentElement.lang || '').startsWith('zh')) {
      return `${(count / 10000).toFixed(1)}${T('author.unit.ten_thousand')}`;
    }
    if (count >= 1000000000) return `${(count / 1000000000).toFixed(1)}B`;
    if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`;
    return `${(count / 1000).toFixed(1)}K`;
  }

  async function postJSON(action, body) {
    // The sidecar fetches TikTok with its own timeouts, but a stalled proxy or a
    // half-open socket left the promise pending: setBusy(true) then disabled every
    // button until the page was reloaded.
    const response = await fetch(endpoint(action), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: window.tikTimeoutSignal ? window.tikTimeoutSignal(120000) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    return { status: response.status, payload };
  }

  function candidateRow(candidate) {
    const row = document.createElement('li');
    row.className = 'author-picker-row';

    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'author-picker-option';
    option.dataset.uniqueId = candidate.unique_id || '';
    option.dataset.uid = candidate.uid || '';

    const avatar = document.createElement('img');
    avatar.className = 'author-picker-avatar';
    avatar.alt = '';
    avatar.loading = 'lazy';
    avatar.decoding = 'async';
    // The candidate list carries the remote avatar URL straight from the site, so
    // the browser would send this page as Referer; and a URL that fails used to
    // leave a broken-image icon in the picker.
    avatar.referrerPolicy = 'no-referrer';
    avatar.addEventListener('error', () => {
      avatar.src = '/static/default-avatar.jpg';
      avatar.classList.add('is-empty');
    }, { once: true });
    if (candidate.avatar && /^https?:\/\//i.test(String(candidate.avatar))) {
      avatar.src = candidate.avatar;
    } else {
      avatar.classList.add('is-empty');
    }

    const body = document.createElement('span');
    body.className = 'author-picker-body';

    const name = document.createElement('span');
    name.className = 'author-picker-name';
    name.textContent = candidate.nickname || candidate.unique_id || candidate.uid || '';
    body.appendChild(name);

    const meta = [];
    const handle = candidate.unique_id || candidate.short_id || candidate.uid;
    if (handle) meta.push(T('author.douyin_id', { id: handle }));
    if (candidate.follower_count !== null && candidate.follower_count !== undefined) {
      meta.push(T('author.picker.followers', { count: compactCount(candidate.follower_count) }));
    }
    if (candidate.total_favorited !== null && candidate.total_favorited !== undefined) {
      meta.push(T('author.picker.likes', { count: compactCount(candidate.total_favorited) }));
    }
    const metaLine = document.createElement('span');
    metaLine.className = 'author-picker-meta';
    metaLine.textContent = meta.join(' · ');
    body.appendChild(metaLine);

    option.append(avatar, body);
    option.addEventListener('click', () => choose(option));
    row.appendChild(option);
    return row;
  }

  function renderCandidates(candidates) {
    if (!list) return;
    list.textContent = '';
    const rows = Array.isArray(candidates) ? candidates : [];
    for (const candidate of rows) list.appendChild(candidateRow(candidate));
    list.hidden = rows.length === 0;
  }

  async function choose(option) {
    if (busy) return;
    const keyword = (input?.value || page.dataset.authorName || '').trim();
    setBusy(true);
    showHint('');
    try {
      const { payload } = await postJSON('refresh', {
        keyword,
        unique_id: option.dataset.uniqueId || '',
        uid: option.dataset.uid || '',
      });
      if (payload.ok) {
        window.location.reload();
        return;
      }
      if (Array.isArray(payload.candidates) && payload.candidates.length) {
        renderCandidates(payload.candidates);
        showHint(payload.error || T('author.picker.ambiguous'));
        return;
      }
      // Nothing left to pick: close the modal and report on the profile header.
      closePicker();
      showPageError(payload.error);
    } catch (error) {
      closePicker();
      showPageError('');
    } finally {
      setBusy(false);
    }
  }

  async function search() {
    if (busy) return;
    const keyword = (input?.value || '').trim();
    setBusy(true);
    showHint(T('author.picker.searching'));
    try {
      const { status, payload } = await postJSON('search', { keyword });
      if (status === 200 && payload.ok) {
        const candidates = payload.candidates || [];
        renderCandidates(candidates);
        showHint(candidates.length ? '' : T('author.picker.empty'));
        return;
      }
      renderCandidates([]);
      showHint(payload.error || T('author.picker.error'));
    } catch (error) {
      renderCandidates([]);
      showHint(T('author.picker.error'));
    } finally {
      setBusy(false);
    }
  }

  async function syncWithUrl() {
    if (busy) return;
    const profileUrl = (urlInput?.value || '').trim();
    if (!profileUrl) {
      showHint(T('author.sync.error.profile_url'));
      urlInput?.focus();
      return;
    }
    setBusy(true);
    showHint(T('author.sync.busy'));
    try {
      // The exact chain: link → sec_uid → account → work → author block, and
      // the author uid has to match this export folder before anything is saved.
      const response = await fetch('/api/author/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: [{ source_id: sourceId, folder, profile_url: profileUrl }],
        }),
      });
      const payload = await response.json().catch(() => ({}));
      const result = Array.isArray(payload.items) ? payload.items[0] : null;
      if (payload.ok && result && result.ok) {
        window.location.reload();
        return;
      }
      showHint((result && result.error) || payload.error || T('author.sync.error.unknown'));
    } catch (error) {
      showHint(T('author.sync.error.unknown'));
    } finally {
      setBusy(false);
    }
  }

  function openSheet() {
    sheet.hidden = false;
    document.body.classList.add('author-picker-open');
    if (sheetHint) {
      sheetHint.hidden = true;
      sheetHint.textContent = '';
    }
    renderBound();
    sheetProfile?.focus();
  }

  function closeSheet() {
    sheet.hidden = true;
    document.body.classList.remove('author-picker-open');
  }

  async function updateVideos() {
    if (busy) return;
    setBusy(true);
    toast(T('author.sync.videos_busy'));
    try {
      // The bound link is read server-side, so the page only sends the source id.
      const { status, payload } = await postJSON('update-videos', {});
      if (status === 200 && payload.ok) {
        const queued = Number(payload.queued || 0);
        const skipped = Number(payload.skipped || 0);
        toast(queued
          ? T('author.sync.videos_done', { queued, skipped })
          : T('author.sync.videos_none'));
        return;
      }
      toast(payload.error || T('author.sync.videos_failed'), 'error');
      if (status === 400) {
        // No bound link yet: ask for it with the same dialog the sync uses.
        openPicker();
      }
    } catch (error) {
      toast(T('author.sync.videos_failed'), 'error');
    } finally {
      setBusy(false);
    }
  }

  function openPicker() {
    picker.hidden = false;
    document.body.classList.add('author-picker-open');
    renderBound();
    const fallback = page.dataset.authorName || '';
    if (input && !input.value.trim() && fallback) input.value = fallback;
    // The home-page link is the exact route; the nickname search is the fallback.
    const target = urlInput || input;
    if (target) {
      target.focus();
      target.select();
    }
  }

  function closePicker() {
    picker.hidden = true;
    document.body.classList.remove('author-picker-open');
    showHint('');
    renderCandidates([]);
  }

  syncButton.addEventListener('click', openSheet);
  sheet.querySelectorAll('[data-author-sheet-close]').forEach((element) => {
    element.addEventListener('click', closeSheet);
  });
  sheetProfile?.addEventListener('click', () => {
    closeSheet();
    openPicker();
  });
  sheetVideos?.addEventListener('click', () => {
    closeSheet();
    void updateVideos();
  });
  urlButton?.addEventListener('click', syncWithUrl);
  urlInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    syncWithUrl();
  });
  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    search();
  });
  picker.querySelectorAll('[data-author-picker-close]').forEach((element) => {
    element.addEventListener('click', closePicker);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!sheet.hidden) closeSheet();
    if (!picker.hidden) closePicker();
  });
  renderBound();
})();
