/* Douyin-style search page: fetches /api/search for the typed term and builds
   the user/video results as DOM nodes, so no server string ever reaches
   innerHTML. The form itself still works without JS (plain GET /search). */
(function () {
  'use strict';

  const page = document.querySelector('[data-search-page]');
  if (!page) return;

  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;

  const input = document.getElementById('search-input');
  const body = document.getElementById('search-body');
  if (!input || !body) return;

  const clearButton = document.getElementById('search-clear');
  const form = page.querySelector('.search-form');
  const tabButtons = Array.from(page.querySelectorAll('[data-tab]'));
  const tabInput = page.querySelector('input[name="tab"]');
  const apiEndpoint = page.dataset.api || '/api/search';
  const defaultAvatar = page.dataset.defaultAvatar || '/static/default-avatar.jpg';

  const TABS = ['general', 'video', 'user'];
  const USERS_IN_GENERAL = 3;
  const PAGE_SIZE = 24;
  // Works load a page at a time as the sentinel nears the viewport (Douyin does
  // the same); the margin starts the next request before the user runs out.
  const MORE_MARGIN_PX = 600;
  const MORE_ROOT_MARGIN = MORE_MARGIN_PX + 'px 0px';

  let activeTab = TABS.includes(page.dataset.tab) ? page.dataset.tab : 'general';
  let requestSeq = 0;

  // Endless-scroll state. `requestSeq` doubles as the generation counter: a new
  // query or tab switch bumps it, so a page that resolves late is dropped.
  let nextOffset = 0;
  let hasMore = false;
  let loadingMore = false;
  let currentGrid = null;
  const renderedKeys = new Set();

  // The term and tab the grid on screen was built for. Typing a new term does not
  // replace the results until the form is submitted, so paging must follow the
  // grid the user is looking at instead of whatever the input holds by then.
  let resultsQuery = '';
  let resultsTab = 'general';

  const reduceMotion = Boolean(
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );

  // ---------- small DOM helpers (text-only: never innerHTML) ----------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null && text !== '') node.textContent = String(text);
    return node;
  }

  function textValue(value) {
    return typeof value === 'string' ? value.trim() : '';
  }

  function safeHref(value) {
    const href = textValue(value);
    if (!href) return '#';
    if (href.charAt(0) === '/' && href.charAt(1) !== '/') return href;
    if (/^https?:\/\//i.test(href)) return href;
    return '#';
  }

  function imageSource(value) {
    return textValue(value) || defaultAvatar;
  }

  function toInt(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? Math.floor(number) : fallback;
  }

  function videoKey(video) {
    return textValue(video.open_url) || textValue(video.thumb_url) || textValue(video.title) || textValue(video.name);
  }

  function withFallback(image) {
    image.addEventListener('error', () => {
      if (image.dataset.fallbackApplied) return;
      image.dataset.fallbackApplied = '1';
      image.src = defaultAvatar;
    });
    return image;
  }

  // ---------- in-place states ----------
  function renderHint() {
    teardownPaging();
    body.replaceChildren(el('p', 'search-hint', T('search.hint')));
  }

  function stateBlock(kind, title) {
    const wrap = el('div', 'search-state');
    wrap.dataset.kind = kind;
    if (kind === 'loading') wrap.appendChild(el('div', 'search-spinner'));
    wrap.appendChild(el('p', 'search-state-title', title));
    return wrap;
  }

  function renderLoading() {
    teardownPaging();
    body.replaceChildren(stateBlock('loading', T('search.loading')));
  }

  function renderError() {
    teardownPaging();
    const wrap = stateBlock('error', T('search.error'));
    const retry = el('button', 'search-state-action', T('search.submit'));
    retry.type = 'button';
    retry.addEventListener('click', () => runSearch());
    wrap.appendChild(retry);
    body.replaceChildren(wrap);
  }

  function renderEmpty(query) {
    teardownPaging();
    const wrap = stateBlock('empty', T('search.empty', { query }));
    wrap.appendChild(el('p', 'search-state-detail', T('search.empty.hint')));
    body.replaceChildren(wrap);
  }

  // ---------- cards ----------
  function buildUserCard(user) {
    const card = el('a', 'su-card');
    card.href = safeHref(user.url);

    const avatar = withFallback(el('img', 'su-avatar'));
    avatar.src = imageSource(user.avatar_url);
    avatar.alt = '';
    avatar.loading = 'lazy';
    avatar.decoding = 'async';
    card.appendChild(avatar);

    const main = el('div', 'su-main');
    main.appendChild(el('p', 'su-name', textValue(user.name)));
    const metaParts = [textValue(user.fans_label), textValue(user.douyin_id_label)].filter(Boolean);
    if (metaParts.length) main.appendChild(el('p', 'su-meta', metaParts.join(' · ')));
    const works = textValue(user.works_label);
    if (works) main.appendChild(el('p', 'su-works', works));
    card.appendChild(main);

    const openLabel = textValue(user.open_label);
    if (openLabel) card.appendChild(el('span', 'su-open', openLabel));
    return card;
  }

  function buildVideoCard(video) {
    const card = el('a', 'sv-card');
    card.href = safeHref(video.open_url);

    const cover = el('div', 'sv-cover');
    const image = withFallback(el('img', 'sv-cover-img'));
    image.src = imageSource(video.thumb_url || video.media_url);
    image.alt = '';
    image.loading = 'lazy';
    image.decoding = 'async';
    cover.appendChild(image);
    const kindLabel = textValue(video.kind_label);
    if (kindLabel) cover.appendChild(el('span', 'sv-kind', kindLabel));
    card.appendChild(cover);

    // A work named only after its numeric id has no caption to show, and the
    // server sends an empty title for it; skip the line instead of printing the id.
    const title = textValue(video.title);
    if (title) card.appendChild(el('p', 'sv-title', title));

    const meta = el('div', 'sv-meta');
    const authorAvatar = withFallback(el('img', 'sv-avatar'));
    authorAvatar.src = imageSource(video.author_avatar_url);
    authorAvatar.alt = '';
    authorAvatar.loading = 'lazy';
    authorAvatar.decoding = 'async';
    meta.appendChild(authorAvatar);
    const authorName = textValue(video.author_name);
    if (authorName) meta.appendChild(el('span', 'sv-author', authorName));
    const dateLabel = textValue(video.date_label);
    if (dateLabel) meta.appendChild(el('span', 'sv-date', dateLabel));
    card.appendChild(meta);
    return card;
  }

  // ---------- sections ----------
  function buildUserSection(users, onMore) {
    const section = el('section', 'search-section search-section-users');
    section.setAttribute('aria-label', T('search.count.users', { n: users.length }));

    const head = el('div', 'search-section-head');
    head.appendChild(el('h2', 'search-section-title', T('search.section.user')));
    if (typeof onMore === 'function') {
      const more = el('button', 'search-section-more', T('search.section.more'));
      more.type = 'button';
      more.addEventListener('click', onMore);
      head.appendChild(more);
    }
    section.appendChild(head);

    const list = el('div', 'search-users');
    users.forEach((user) => list.appendChild(buildUserCard(user)));
    section.appendChild(list);
    return section;
  }

  function buildVideoSection(videos, withHeading) {
    const section = el('section', 'search-section search-section-videos');
    section.setAttribute('aria-label', T('search.count.videos', { n: videos.length }));

    if (withHeading) {
      const head = el('div', 'search-section-head');
      head.appendChild(el('h2', 'search-section-title', T('search.section.video')));
      section.appendChild(head);
    }

    const grid = el('div', 'search-grid');
    videos.forEach((video) => grid.appendChild(buildVideoCard(video)));
    section.appendChild(grid);
    return section;
  }

  function renderResults(payload) {
    // The server echoes the cleaned term it answered for; the input may already
    // hold the next thing the user is typing.
    const query = textValue(payload.query) || readQuery();
    const users = Array.isArray(payload.users) ? payload.users.filter(Boolean) : [];
    const videos = Array.isArray(payload.videos) ? payload.videos.filter(Boolean) : [];
    const tab = TABS.includes(payload.tab) ? payload.tab : activeTab;

    if (!users.length && !videos.length) {
      renderEmpty(query);
      return;
    }
    resultsQuery = query;
    resultsTab = tab;

    renderedKeys.clear();
    videos.forEach((video) => {
      const key = videoKey(video);
      if (key) renderedKeys.add(key);
    });

    const fragment = document.createDocumentFragment();
    if (tab === 'user') {
      const list = el('div', 'search-list');
      users.forEach((user) => list.appendChild(buildUserCard(user)));
      fragment.appendChild(list);
    } else if (tab === 'video') {
      fragment.appendChild(buildVideoSection(videos, false));
    } else {
      if (users.length) {
        fragment.appendChild(
          buildUserSection(users.slice(0, USERS_IN_GENERAL), () => switchTab('user'))
        );
      }
      if (videos.length) fragment.appendChild(buildVideoSection(videos, true));
    }
    body.replaceChildren(fragment);

    // Only the general and video tabs page: the user tab returns every match at
    // once (its payload has has_more=false), so there is nothing to scroll for.
    currentGrid = tab === 'user' ? null : body.querySelector('.search-grid');
    nextOffset = toInt(payload.next_offset, videos.length);
    hasMore = tab !== 'user' && Boolean(payload.has_more) && Boolean(currentGrid);
    syncSentinel();
  }

  // ---------- endless scroll ----------
  // A sentinel div after the grid drives loading; the observer's rootMargin
  // fetches early so the next page is usually already there when it arrives.
  let moreSentinel = null;
  let moreObserver = null;
  if (typeof IntersectionObserver === 'function') {
    moreObserver = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      },
      { rootMargin: MORE_ROOT_MARGIN }
    );
  }

  function removeSentinel() {
    if (moreSentinel && moreObserver) moreObserver.unobserve(moreSentinel);
    if (moreSentinel && moreSentinel.parentNode) {
      moreSentinel.parentNode.removeChild(moreSentinel);
    }
  }

  function syncSentinel() {
    if (!hasMore || !currentGrid) {
      removeSentinel();
      return;
    }
    if (!moreSentinel) {
      moreSentinel = el('div', 'search-more');
      moreSentinel.id = 'search-more';
    }
    setMoreStatus('loading');
    body.appendChild(moreSentinel); // appendChild also moves it back to the end
    if (moreObserver) moreObserver.observe(moreSentinel);
  }

  function setMoreStatus(kind) {
    if (!moreSentinel) return;
    moreSentinel.dataset.state = kind;
    moreSentinel.replaceChildren();
    if (kind === 'error') {
      // A failed page must never blank the cards already on screen.
      moreSentinel.appendChild(el('span', 'search-more-text', T('search.error')));
      const retry = el('button', 'search-state-action search-more-retry', T('search.submit'));
      retry.type = 'button';
      retry.addEventListener('click', () => loadMore());
      moreSentinel.appendChild(retry);
      return;
    }
    const spinner = el('span', 'search-more-spinner');
    spinner.setAttribute('aria-hidden', 'true');
    moreSentinel.appendChild(spinner);
    moreSentinel.appendChild(el('span', 'search-more-text', T('search.loading')));
  }

  function teardownPaging() {
    hasMore = false;
    loadingMore = false;
    currentGrid = null;
    resultsQuery = '';
    renderedKeys.clear();
    removeSentinel();
  }

  function resetPaging() {
    requestSeq += 1;
    nextOffset = 0;
    teardownPaging();
  }

  function canLoadMore() {
    return Boolean(
      hasMore && !loadingMore && currentGrid && resultsQuery &&
      TABS.includes(resultsTab) && resultsTab !== 'user'
    );
  }

  function sentinelInRange() {
    if (!moreSentinel) return false;
    const rect = moreSentinel.getBoundingClientRect();
    return rect.top <= window.innerHeight + MORE_MARGIN_PX;
  }

  function queueFillCheck() {
    // If a short page still leaves the sentinel inside the margin, the observer
    // would not fire again, so top up until the content actually overflows.
    window.setTimeout(() => {
      if (canLoadMore() && sentinelInRange()) loadMore();
    }, 0);
  }

  function appendVideos(videos) {
    if (!currentGrid || !videos.length) return 0;
    const fragment = document.createDocumentFragment();
    let appended = 0;
    videos.forEach((video) => {
      const key = videoKey(video);
      if (key && renderedKeys.has(key)) return; // backend repeated a record
      if (key) renderedKeys.add(key);
      fragment.appendChild(buildVideoCard(video));
      appended += 1;
    });
    if (appended) currentGrid.appendChild(fragment);
    return appended;
  }

  function loadMore() {
    if (!canLoadMore()) return;
    const seq = requestSeq; // bumped by any new query or tab switch
    const offset = nextOffset;
    loadingMore = true;
    setMoreStatus('loading');
    const params = new URLSearchParams({
      q: resultsQuery,
      tab: resultsTab,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    fetch(apiEndpoint + '?' + params.toString(), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
      .then((response) => {
        if (!response.ok) throw new Error('search request failed: ' + response.status);
        return response.json();
      })
      .then((payload) => {
        if (seq !== requestSeq) return; // superseded while in flight
        if (!payload || payload.success === false) throw new Error('search request failed');
        // The general tab's later pages carry users too; they are already
        // rendered from the first page, so only the new works are appended.
        const videos = Array.isArray(payload.videos) ? payload.videos.filter(Boolean) : [];
        const appended = appendVideos(videos);
        nextOffset = toInt(payload.next_offset, offset + videos.length);
        // Stop when the backend repeats itself instead of scrolling forever.
        hasMore = Boolean(payload.has_more) && appended > 0;
        syncSentinel();
        if (hasMore) queueFillCheck();
      })
      .catch(() => {
        if (seq !== requestSeq) return;
        setMoreStatus('error');
      })
      .finally(() => {
        if (seq === requestSeq) loadingMore = false;
      });
  }

  // ---------- query / tab / url ----------
  function readQuery() {
    return input.value.trim();
  }

  function updateClear() {
    if (clearButton) clearButton.hidden = readQuery() === '';
  }

  function updateTabs() {
    tabButtons.forEach((button) => {
      const isActive = button.dataset.tab === activeTab;
      button.classList.toggle('is-active', isActive);
      button.setAttribute('aria-selected', isActive ? 'true' : 'false');
      // Roving tabindex: the tab strip is one Tab stop, arrows move inside it.
      button.tabIndex = isActive ? 0 : -1;
    });
    if (tabInput) tabInput.value = activeTab;
  }

  function syncUrl() {
    const params = new URLSearchParams();
    const query = readQuery();
    if (query) params.set('q', query);
    if (activeTab !== 'general') params.set('tab', activeTab);
    const suffix = params.toString();
    try {
      window.history.replaceState(null, '', '/search' + (suffix ? '?' + suffix : ''));
    } catch (error) {
      /* History API can be unavailable in exotic embedding contexts. */
    }
  }

  function scrollToTop() {
    try {
      window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
    } catch (error) {
      window.scrollTo(0, 0);
    }
  }

  // ---------- search ----------
  function runSearch() {
    const query = readQuery();
    syncUrl();
    // A new term starts a new result set: drop the previous scroll state and
    // invalidate any page still in flight.
    resetPaging();
    if (!query) {
      renderHint();
      return;
    }

    const seq = requestSeq;
    renderLoading();
    const params = new URLSearchParams({ q: query, tab: activeTab, limit: String(PAGE_SIZE) });
    fetch(apiEndpoint + '?' + params.toString(), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
      .then((response) => {
        if (!response.ok) throw new Error('search request failed: ' + response.status);
        return response.json();
      })
      .then((payload) => {
        if (seq !== requestSeq) return;
        if (!payload || payload.success === false) throw new Error('search request failed');
        renderResults(payload);
      })
      .catch(() => {
        if (seq !== requestSeq) return;
        renderError();
      });
  }

  function switchTab(tab) {
    if (!TABS.includes(tab)) return;
    const changed = tab !== activeTab;
    activeTab = tab;
    updateTabs();
    syncUrl();
    if (!changed) {
      scrollToTop();
      return;
    }
    if (readQuery()) runSearch();
    else renderHint();
    scrollToTop();
  }

  function clearQuery() {
    input.value = '';
    resetPaging();
    updateClear();
    renderHint();
    syncUrl();
    input.focus();
  }

  // ---------- wiring ----------
  clearButton?.addEventListener('click', clearQuery);

  // The back arrow returns to wherever the search was opened from (the feed, a
  // tag feed, the library...), and only falls back to /flow when there is no
  // in-app page behind us -- a direct visit.
  const backLink = page.querySelector('.search-back');
  if (backLink) {
    backLink.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      try {
        const referrer = document.referrer ? new URL(document.referrer) : null;
        if (referrer && referrer.origin === window.location.origin && window.history.length > 1) {
          event.preventDefault();
          window.history.back();
        }
      } catch (error) {
        /* Keep the plain /flow link. */
      }
    });
  }

  input.addEventListener('input', () => {
    updateClear();
    if (readQuery() === '') {
      resetPaging();
      renderHint();
    }
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && input.value !== '') {
      event.preventDefault();
      clearQuery();
    }
  });

  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    if (readQuery() === '') {
      clearQuery();
      return;
    }
    runSearch();
  });

  tabButtons.forEach((button, index) => {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
    // Arrow keys walk the tab strip the way the WAI-ARIA tabs pattern expects.
    button.addEventListener('keydown', (event) => {
      const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const next = (index + step + tabButtons.length) % tabButtons.length;
      const target = tabButtons[next];
      switchTab(target.dataset.tab);
      target.focus();
    });
  });

  updateClear();
  updateTabs();
  if (readQuery()) runSearch();
  else {
    renderHint();
    // Douyin opens the keyboard on arrival; do the same, but never scroll the
    // page away from the hint while doing it.
    try {
      input.focus({ preventScroll: true });
    } catch (error) {
      /* Older browsers take no options object. */
      input.focus();
    }
  }
})();
