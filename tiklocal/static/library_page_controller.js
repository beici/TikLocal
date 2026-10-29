(() => {
  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
  const dateLocale = () => (document.documentElement.lang === 'en' ? 'en' : 'zh-CN');
  const boot = window.__TIKLOCAL_LIBRARY_BOOT__ || {};
  delete window.__TIKLOCAL_LIBRARY_BOOT__;

  const scope = boot.scope;
  const collectionId = boot.collectionId;
  let collectionCoverUri = String(boot.collectionCoverUri || '');
  const pageSize = Number(boot.pageSize || 0);
  const initialItems = Array.isArray(boot.initialItems) ? boot.initialItems : [];
  const initialHasMore = !!boot.initialHasMore;
  const initialNextOffset = Number(boot.initialNextOffset || 0);
  const initialMode = String(boot.initialMode || 'all');
  const initialSeed = String(boot.initialSeed || '');
  const minMb = Number(boot.minMb || 0);
  const emptyMessage = String(boot.emptyMessage || '');
  const timelineMonth = String(boot.timelineMonth || '');
  const focusName = new URLSearchParams(window.location.search).get('focus') || '';
  let semanticSearch = false;
  let searchRequest = 0;
  let searchQuery = new URLSearchParams(window.location.search).get('q') || '';
  const blogFilter = new URLSearchParams(window.location.search).get('blog') || '';
  // Collection filter inside the saved view: a chip click narrows favorites to one collection.
  let activeScope = scope === 'favorite' && collectionId ? 'collection' : scope;
  let activeCollectionId = String(collectionId || '');

  const grid = document.getElementById('library-grid');
  const statusText = document.getElementById('library-status-text');
  const statusActions = document.getElementById('library-status-actions');
  const retryButton = document.getElementById('library-retry');
  const clearSearchButton = document.getElementById('library-clear-search');
  const sentinel = document.getElementById('library-sentinel');
  const searchInput = document.getElementById('library-search-input');
  const searchClearButton = document.getElementById('library-search-clear');
  const searchToggleButton = document.getElementById('library-search-toggle');
  const libraryToolbar = document.getElementById('library-toolbar');
  const discoveries = document.getElementById('library-discoveries');
  const collectionIdentityCover = document.getElementById('collection-identity-cover');

  const quickView = document.getElementById('quick-view');
  const quickOverlay = document.getElementById('quick-overlay');
  const quickVideo = document.getElementById('quick-video');
  const quickImage = document.getElementById('quick-image');
  const quickPlayStatus = document.getElementById('quick-play-status');
  const quickPlayIcon = document.getElementById('quick-play-icon');
  const quickCloseTop = document.getElementById('quick-close-top');
  const quickCounter = document.getElementById('quick-counter');
  const quickControls = document.getElementById('quick-controls');
  const quickProgress = document.getElementById('quick-progress');
  const quickProgressFill = document.getElementById('quick-progress-fill');
  const quickTimeCurrent = document.getElementById('quick-time-current');
  const quickTimeTotal = document.getElementById('quick-time-total');

  const quickSpeed = document.getElementById('quick-speed');
  const quickCaption = document.getElementById('quick-caption');
  const quickMagnifierToolWrap = document.getElementById('quick-magnifier-tool-wrap');
  const quickCaptionPanel = document.getElementById('quick-caption-panel');
  const quickCaptionTitle = document.getElementById('quick-caption-title');
  const quickCaptionTags = document.getElementById('quick-caption-tags');
  const uiShared = window.FlowUIShared || {};
  const actionsShared = window.FlowActionsShared || {};

  const quickFavorite = document.getElementById('quick-favorite');
  const quickSetCover = document.getElementById('quick-set-cover');
  const quickSource = document.getElementById('quick-source');
  const quickDetail = document.getElementById('quick-detail');

  let mode = scope === 'all' ? initialMode : 'all';
  let seed = initialSeed || '';
  const flowSession = window.createFlowSession({
    initialItems,
    initialHasMore: !!initialHasMore,
    initialCursor: { offset: Number(initialNextOffset || 0) },
    keyOf: (item) => String(item?.name || ''),
  });
  const items = flowSession.items;
  let currentSpeedIndex = 1;
  let clickTimer = null;
  let lastClickTime = 0;
  let wheelLocked = false;
  let isDragging = false;
  let wasPlayingBeforeDrag = false;
  let bodyOverflowBackup = '';
  let bodyScrollLocked = false;
  let viewerPreviousFocus = null;
  let focusHandled = false;
  let focusLoading = false;
  const speedOptions = [0.75, 1, 1.25, 1.5, 2];

  const { formatTime } = uiShared;

  function makeRandomSeed() {
    return `${Date.now()}-${Math.floor(Math.random() * 100000)}`;
  }

  const currentItem = () => flowSession.currentItem();
  const getCurrentIndex = () => flowSession.getIndex();

  const magnifier = uiShared.createMagnifier({
    lens: document.getElementById('quick-magnifier'),
    toggle: document.getElementById('quick-magnifier-toggle'),
    zoomOptions: document.getElementById('quick-zoom-options'),
    badge: document.getElementById('quick-mag-badge'),
    zoomButtons: document.querySelectorAll('.quick-zoom-btn'),
    activeClass: 'is-active',
    largeClass: 'quick-magnifier-large',
    settleTransition: 'transform 0.2s ease, width 0.2s ease, height 0.2s ease',
    setMagnifying: (enabled) => flowState.setMagnifying(enabled),
    target: () => {
      const type = currentItem()?.type;
      if (type === 'video') return { type, el: quickVideo };
      if (type === 'image') return { type, el: quickImage };
      return null;
    },
  });

  const flowState = window.createFlowStateController({
    getMediaType: () => currentItem()?.type || '',
    canMagnifyMedia: (mediaType) => mediaType === 'image' || mediaType === 'video',
    onImmersiveChange: (enabled) => quickView.classList.toggle('immersive', !!enabled),
    onMagnifyingChange: (enabled) => magnifier.onStateChange(enabled),
  });

  const collections = window.TikLocalCollections.createPicker({
    button: document.getElementById('quick-collection'),
    countEl: document.getElementById('quick-collection-count'),
    modal: document.getElementById('quick-collection-modal'),
    closeBtn: document.getElementById('quick-collection-close'),
    meta: document.getElementById('quick-collection-meta'),
    createBtn: document.getElementById('quick-collection-create-btn'),
    nameInput: document.getElementById('quick-collection-name'),
    list: document.getElementById('quick-collection-list'),
    itemClass: 'quick-collection-item',
    emptyClass: 'quick-collection-empty',
    currentUri: () => currentItem()?.name,
  });

  function showPlayStatus(paused) {
    const isPaused = !!paused;
    quickPlayIcon.innerHTML = isPaused
      ? '<rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect>'
      : '<polygon points="5 3 19 12 5 21 5 3"></polygon>';
    quickPlayStatus.classList.add('visible');
    setTimeout(() => quickPlayStatus.classList.remove('visible'), 220);
  }

  const toggleUI = () => flowState.toggleImmersive();

  function activeTab() {
    document.querySelectorAll('.mode-tab').forEach((tab) => {
      const isActive = tab.dataset.mode === mode;
      tab.classList.toggle('is-active', isActive);
      tab.setAttribute('aria-pressed', String(isActive));
    });
  }

  function syncSearchUI() {
    const hasQuery = !!String(searchInput?.value || '').trim();
    libraryToolbar?.classList.toggle('is-searching', hasQuery);
    if (searchClearButton) searchClearButton.hidden = !hasQuery;
  }

  function setSearchOpen(open, { focus = false } = {}) {
    const isOpen = !!open;
    libraryToolbar?.classList.toggle('is-search-open', isOpen);
    searchToggleButton?.setAttribute('aria-expanded', String(isOpen));
    if (isOpen && focus) requestAnimationFrame(() => searchInput?.focus());
  }

  function readLibraryStateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const nextMode = params.get('mode') || 'all';
    const allowed = new Set(['all', 'image_random', 'video_latest', 'big_files']);
    return {
      mode: allowed.has(nextMode) ? nextMode : 'all',
      seed: params.get('seed') || '',
      search: params.get('q') || '',
    };
  }

  function syncLibraryUrl(replace = false) {
    if (scope !== 'all') return;
    const params = new URLSearchParams(window.location.search);
    params.delete('focus');
    if (mode === 'all') {
      params.delete('mode');
      params.delete('seed');
    } else {
      params.set('mode', mode);
      if (mode === 'image_random' && seed) params.set('seed', seed);
      else params.delete('seed');
    }
    if (mode === 'big_files' && minMb) params.set('min_mb', String(minMb));
    else params.delete('min_mb');
    if (searchQuery) params.set('q', searchQuery);
    else params.delete('q');
    const query = params.toString();
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`;
    const method = replace ? 'replaceState' : 'pushState';
    window.history[method]({ mode, seed }, '', nextUrl);
  }

  function getTileAspectRatio(item) {
    const width = Number(item?.width || 0);
    const height = Number(item?.height || 0);
    if (width > 0 && height > 0) {
      return `${width} / ${height}`;
    }
    return item?.type === 'video' ? '16 / 9' : '4 / 5';
  }

  function getTileHeightRatio(item) {
    const width = Number(item?.width || 0);
    const height = Number(item?.height || 0);
    if (width > 0 && height > 0) {
      return height / width;
    }
    return item?.type === 'video' ? (9 / 16) : (5 / 4);
  }

  const waterfall = {
    count: 0,
    gap: 8,
    gridWidth: 0,
    columnWidth: 0,
    columns: [],
    heights: [],
  };
  let relayoutTimer = null;

  function getWaterfallColumnCount() {
    const width = window.innerWidth;
    if (width >= 1536) return 5;
    if (width >= 1100) return 4;
    if (width >= 768) return 3;
    return 2;
  }

  function getWaterfallGap() {
    return window.innerWidth >= 768 ? 10 : 8;
  }

  function updateWaterfallMetrics() {
    const count = Math.max(1, waterfall.count || getWaterfallColumnCount());
    const gap = getWaterfallGap();
    const gridWidth = Math.max(1, grid.clientWidth || window.innerWidth);
    waterfall.gap = gap;
    waterfall.gridWidth = gridWidth;
    waterfall.columnWidth = Math.max(1, (gridWidth - gap * (count - 1)) / count);
    grid.style.setProperty('--wf-gap', `${gap}px`);
  }

  function resetWaterfallLayout() {
    waterfall.count = 0;
    waterfall.gridWidth = 0;
    waterfall.columnWidth = 0;
    waterfall.columns = [];
    waterfall.heights = [];
    grid.innerHTML = '';
  }

  function ensureWaterfallLayout(force = false) {
    const nextCount = getWaterfallColumnCount();
    if (!force && waterfall.count === nextCount && waterfall.columns.length) {
      updateWaterfallMetrics();
      return;
    }

    waterfall.count = nextCount;
    grid.innerHTML = '';
    waterfall.columns = [];
    waterfall.heights = new Array(nextCount).fill(0);
    updateWaterfallMetrics();

    for (let i = 0; i < nextCount; i += 1) {
      const col = document.createElement('div');
      col.className = 'waterfall-col';
      grid.appendChild(col);
      waterfall.columns.push(col);
    }
  }

  function pickShortestColumnIndex() {
    if (!waterfall.heights.length) return 0;
    let target = 0;
    let minHeight = waterfall.heights[0];
    for (let i = 1; i < waterfall.heights.length; i += 1) {
      if (waterfall.heights[i] < minHeight) {
        minHeight = waterfall.heights[i];
        target = i;
      }
    }
    return target;
  }

  function estimateTileHeight(item) {
    return waterfall.columnWidth * getTileHeightRatio(item);
  }

  function createTile(item, index) {
    const tile = document.createElement('article');
    tile.className = 'media-tile';
    tile.dataset.index = String(index);
    // A mouse is not the only way into the grid: the cards have to be tabbable and
    // named, otherwise keyboard and screen-reader users cannot open any media here
    // (the timeline above already renders real <button>s for the same reason).
    tile.tabIndex = 0;
    tile.setAttribute('role', 'button');
    tile.setAttribute('aria-label', T('home.view_media', { name: String(item.name || '') }));
    const ratio = getTileAspectRatio(item);

    if (item.type === 'video') {
      const img = document.createElement('img');
      img.src = item.thumb_url;
      img.alt = '';
      img.loading = 'lazy';
      // Video posters are full-size JPEGs too; without async decoding a screenful
      // competes with the main thread while the grid scrolls.
      img.decoding = 'async';
      img.style.aspectRatio = ratio;
      tile.appendChild(img);

      const badge = document.createElement('span');
      badge.className = 'tile-video-badge';
      badge.innerHTML = feather.icons.play.toSvg({ width: 12, height: 12 });
      tile.appendChild(badge);

      if (item.progress) {
        const progress = document.createElement('span');
        progress.className = 'tile-progress';
        progress.style.setProperty('--progress', `${item.progress * 100}%`);
        tile.appendChild(progress);
      }
    } else {
      const img = document.createElement('img');
      img.src = item.thumb_url || item.media_url;
      img.alt = '';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.style.aspectRatio = ratio;
      tile.appendChild(img);
    }

    if (!(item.width && item.height)) {
      // Sizes are filled in the background; until then take the real ratio once loaded.
      const img = tile.querySelector('img');
      img.addEventListener('load', () => {
        if (img.naturalWidth && img.naturalHeight) img.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
      }, { once: true });
    }

    tile.dataset.name = String(item.name || '');
    tile.addEventListener('click', () => {
      if (selecting) {
        const name = String(item.name || '');
        if (selectedNames.has(name)) selectedNames.delete(name);
        else selectedNames.add(name);
        tile.classList.toggle('is-selected', selectedNames.has(name));
        updateOrganizeBar();
        return;
      }
      openViewer(index);
    });
    // Space and Enter are what a keyboard user presses on a card; .click() reuses the
    // branch above so selection mode keeps working from the keyboard too.
    tile.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
      event.preventDefault();
      tile.click();
    });
    return tile;
  }

  function appendIndexToWaterfall(index) {
    const item = items[index];
    if (!item) return;
    const colIndex = pickShortestColumnIndex();
    const col = waterfall.columns[colIndex];
    if (!col) return;
    col.appendChild(createTile(item, index));
    waterfall.heights[colIndex] += estimateTileHeight(item) + waterfall.gap;
  }

  function relayoutWaterfall() {
    if (!items.length) {
      resetWaterfallLayout();
      ensureWaterfallLayout(true);
      return;
    }
    ensureWaterfallLayout(true);
    updateWaterfallMetrics();
    for (let i = 0; i < items.length; i += 1) {
      appendIndexToWaterfall(i);
    }
    // 整表重建会丢掉 .is-selected 类，但 selectedNames 还在：不补这一步，整理模式
    // 会显示「已选 N 项」却没有任何高亮，批量操作作用在看不见的项上。
    refreshTileSelection();
    feather.replace();
  }

  function scheduleWaterfallRelayout() {
    if (relayoutTimer) clearTimeout(relayoutTimer);
    relayoutTimer = setTimeout(() => {
      relayoutTimer = null;
      const nextCount = getWaterfallColumnCount();
      const nextGap = getWaterfallGap();
      const nextGridWidth = Math.max(1, grid.clientWidth || window.innerWidth);
      const layoutChanged = waterfall.count !== nextCount
        || waterfall.gap !== nextGap
        || waterfall.gridWidth !== nextGridWidth;
      if (!layoutChanged) return;
      relayoutWaterfall();
    }, 180);
  }

  function setLoadingText(text, actions = {}) {
    statusText.textContent = text;
    retryButton.hidden = actions.retry !== true;
    clearSearchButton.hidden = actions.clearSearch !== true;
    statusActions.hidden = actions.retry !== true && actions.clearSearch !== true;
  }

  function findItemIndexByName(name) {
    const expected = String(name || '').trim();
    if (!expected) return -1;
    for (let i = 0; i < items.length; i += 1) {
      if (String(items[i]?.name || '') === expected) return i;
    }
    return -1;
  }

  function apiParams(offset) {
    const params = new URLSearchParams({
      scope: activeScope,
      mode,
      offset: String(offset),
      limit: String(pageSize),
    });
    if (blogFilter) params.set('blog', blogFilter);
    if (activeScope === 'collection' && activeCollectionId) {
      params.set('collection_id', activeCollectionId);
    }
    if (mode === 'big_files') {
      params.set('min_mb', String(minMb));
    }
    if (mode === 'image_random') {
      if (!seed) seed = makeRandomSeed();
      params.set('seed', seed);
    }
    if (searchQuery) params.set('q', searchQuery);
    if (timelineMonth) params.set('month', timelineMonth);
    return params;
  }

  async function loadNextPage() {
    if (semanticSearch || flowSession.isLoading() || !flowSession.hasMore()) return;
    setLoadingText(T('library.status.loading'));
    try {
      await flowSession.loadMore(async (cursor) => {
        const offset = Number(cursor?.offset || 0);
        const url = `/api/library/items?${apiParams(offset).toString()}`;
        // 没有超时兜底时，连接挂起会让列表永远停在「加载中…」，重试按钮也不会出现。
        const res = await fetch(url, { signal: tikTimeoutSignal(20000) });
        const data = await res.json();
        if (!data?.success) throw new Error('request failed');

        const payload = data.data || {};
        if (payload.seed && mode === 'image_random') seed = String(payload.seed);
        return {
          items: Array.isArray(payload.items) ? payload.items : [],
          hasMore: payload.has_more === true,
          cursor: { offset: Number(payload.next_offset || (offset + pageSize)) },
        };
      }).then((result) => {
        const appendedIndexes = result.appendedIndices || [];
        if (appendedIndexes.length) {
          ensureWaterfallLayout(!waterfall.columns.length);
          updateWaterfallMetrics();
          appendedIndexes.forEach((idx) => appendIndexToWaterfall(idx));
          feather.replace();
          if (quickView.classList.contains('active') && getCurrentIndex() >= 0) {
            quickCounter.textContent = `${getCurrentIndex() + 1} / ${items.length}`;
          }
        }
      });

      if (!items.length) {
        if (searchQuery) {
          setLoadingText(T('library.status.no_results', { query: searchQuery }), { clearSearch: true });
        } else {
          setLoadingText(currentEmptyMessage());
        }
      } else if (!flowSession.hasMore()) {
        setLoadingText(T('library.status.end'));
      } else {
        setLoadingText(T('library.status.keep_scrolling'));
      }
    } catch (error) {
      setLoadingText(T('library.status.failed'), { retry: true });
    }
  }

  async function maybeOpenFocusedItem() {
    if (!focusName || focusHandled || focusLoading) return;
    const directIndex = findItemIndexByName(focusName);
    if (directIndex >= 0) {
      focusHandled = true;
      openViewer(directIndex);
      return;
    }
    if (!flowSession.hasMore()) {
      focusHandled = true;
      return;
    }

    focusLoading = true;
    try {
      let guard = 0;
      while (!focusHandled && flowSession.hasMore() && guard < 200) {
        const before = items.length;
        await loadNextPage();
        guard += 1;
        const index = findItemIndexByName(focusName);
        if (index >= 0) {
          focusHandled = true;
          openViewer(index);
          return;
        }
        // No new page arrived (request in flight or failed): stop instead of
        // re-firing the same request in a tight loop.
        if (items.length === before) break;
      }
      if (!flowSession.hasMore()) {
        focusHandled = true;
      }
    } finally {
      focusLoading = false;
    }
  }

  async function reloadCurrentMode() {
    semanticSearch = false;
    if (discoveries) {
      discoveries.dataset.searching = 'false';
      discoveries.hidden = !discoveries.querySelector('#library-discoveries-grid')?.childElementCount || !!searchQuery;
    }
    searchRequest += 1;
    document.getElementById('image-search-reset')?.setAttribute('hidden', '');
    document.getElementById('image-search-setup')?.setAttribute('hidden', '');
    const imageSearchStatus = document.getElementById('image-search-status');
    if (imageSearchStatus) imageSearchStatus.textContent = T('library.semantic.status_hint');
    closeViewer();
    flowSession.reset({
      hasMore: true,
      cursor: { offset: 0 },
    });
    resetWaterfallLayout();
    if (selecting) {
      selectedNames.clear();
      updateOrganizeBar();
    }
    activeTab();
    setLoadingText(T('library.status.loading'));
    await loadNextPage();
  }

  async function reloadMode(nextMode) {
    mode = nextMode;
    searchQuery = '';
    if (searchInput) searchInput.value = '';
    syncSearchUI();
    setSearchOpen(false);
    if (mode === 'image_random') seed = makeRandomSeed();
    else seed = '';
    syncLibraryUrl(false);
    await reloadCurrentMode();
  }

  async function reloadModeFromUrl() {
    if (scope !== 'all') return;
    const state = readLibraryStateFromUrl();
    mode = state.mode;
    seed = state.seed;
    searchQuery = state.search;
    if (searchInput) searchInput.value = searchQuery;
    syncSearchUI();
    setSearchOpen(!!searchQuery);
    if (mode === 'image_random' && !seed) seed = makeRandomSeed();
    syncLibraryUrl(true);
    await reloadCurrentMode();
  }

  function updateCollectionCoverButton(item) {
    const shouldShow = scope === 'collection' && !!collectionId && !!item?.name;
    quickSetCover.classList.toggle('is-hidden', !shouldShow);
    if (!shouldShow) {
      quickSetCover.dataset.currentCover = 'false';
      quickSetCover.disabled = false;
      return;
    }
    const isCurrent = String(item.name) === collectionCoverUri;
    quickSetCover.dataset.currentCover = isCurrent ? 'true' : 'false';
    quickSetCover.title = isCurrent ? T('library.quick.current_cover') : T('library.quick.set_cover_short');
    quickSetCover.setAttribute('aria-label', quickSetCover.title);
    quickSetCover.disabled = isCurrent;
  }

  function updateCollectionIdentityCover(item) {
    if (!collectionIdentityCover || !item?.thumb_url) return;
    const collage = collectionIdentityCover.querySelector('.collection-identity-collage');
    const firstTile = collage?.querySelector('.collection-identity-tile');
    if (firstTile) {
      firstTile.dataset.uri = item.name;
      firstTile.dataset.type = item.type;
      const image = firstTile.querySelector('img');
      if (image) image.src = item.thumb_url;
      return;
    }
    const nextCollage = document.createElement('div');
    nextCollage.className = 'collection-identity-collage';
    nextCollage.dataset.count = '1';
    const tile = document.createElement('span');
    tile.className = 'collection-identity-tile';
    tile.dataset.uri = item.name;
    tile.dataset.type = item.type;
    const image = document.createElement('img');
    image.src = item.thumb_url;
    image.alt = '';
    image.decoding = 'async';
    tile.appendChild(image);
    nextCollage.appendChild(tile);
    collectionIdentityCover.replaceChildren(nextCollage);
  }

  async function syncFavoriteState(item) {
    if (!item?.name) return;
    try {
      await mediaActions.syncFavorite(item.name);
    } catch (error) {
      if (currentItem()?.name === item.name) updateFavoriteButton(false);
    }
  }

  function updateFavoriteButton(favorited) {
    quickFavorite.classList.toggle('is-favorited', !!favorited);
    quickFavorite.setAttribute('aria-pressed', String(!!favorited));
    quickFavorite.setAttribute('aria-label', favorited ? T('library.quick.favorite_remove') : T('library.quick.favorite_add'));
    quickFavorite.title = favorited ? T('library.quick.favorite_remove') : T('library.quick.favorite_add');
  }

  function updateSourceButton(sourceMeta) {
    const url = String(sourceMeta?.url || '').trim();
    if (!url) {
      quickSource.classList.add('is-hidden');
      quickSource.removeAttribute('href');
      quickSource.title = T('library.quick.source_missing');
      return;
    }
    const domain = String(sourceMeta?.source_domain || '').trim();
    quickSource.href = url;
    quickSource.title = domain ? T('library.quick.source_domain', { domain }) : T('library.quick.source');
    quickSource.classList.remove('is-hidden');
  }

  function clearCaption() {
    quickCaptionTitle.textContent = '';
    quickCaptionTags.innerHTML = '';
    quickCaptionPanel.classList.add('is-hidden');
  }

  function renderCaption(data) {
    const title = String(data?.title || '').trim();
    const tags = Array.isArray(data?.tags) ? data.tags : [];
    if (!title && tags.length === 0) {
      clearCaption();
      return;
    }
    quickCaptionPanel.classList.remove('is-hidden');
    quickCaptionTitle.textContent = title;
    quickCaptionTags.innerHTML = '';
    tags.forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'quick-caption-tag';
      chip.textContent = String(tag || '');
      quickCaptionTags.appendChild(chip);
    });
  }

  function setCaptionLoading(loadingState) {
    quickCaption.disabled = !!loadingState;
    quickCaption.classList.toggle('is-loading', !!loadingState);
  }

  function flashCaptionError() {
    quickCaption.classList.add('is-error');
    setTimeout(() => quickCaption.classList.remove('is-error'), 1400);
  }

  const mediaActions = window.createFlowMediaActionsController({
    actions: actionsShared,
    onFavoriteChange: (value, name) => {
      if (currentItem()?.name === name) updateFavoriteButton(value);
    },
    onSourceChange: (sourceMeta, name) => {
      // A slow lookup for the item that was open a moment ago must not relabel the
      // button of the item on screen now (the favorite callback guards the same way).
      if (currentItem()?.name === name) updateSourceButton(sourceMeta);
    },
    onCaptionClear: () => {
      clearCaption();
    },
    onCaptionRender: (payload) => {
      renderCaption(payload);
    },
    onCaptionLoading: (loading) => {
      setCaptionLoading(loading);
    },
    onError: (kind) => {
      if (kind === 'caption_load' || kind === 'caption_generate') {
        flashCaptionError();
      }
    },
  });

  function updateControls(item) {
    const isVideo = item?.type === 'video';
    quickControls.classList.toggle('is-hidden', !isVideo);
    quickSpeed.hidden = !isVideo;
    quickCaption.hidden = isVideo;
    quickMagnifierToolWrap.hidden = false;

    if (!isVideo) {
      quickProgress.disabled = true;
      quickProgress.value = 0;
      quickProgressFill.style.width = '0%';
      quickTimeCurrent.textContent = '';
      quickTimeTotal.textContent = '';
      quickPlayStatus.classList.remove('visible');
    } else {
      quickProgress.disabled = false;
    }
  }

  function updateVideoProgress() {
    const item = currentItem();
    if (!item || item.type !== 'video' || isDragging) return;
    const duration = quickVideo.duration;
    if (duration && !Number.isNaN(duration) && duration !== Infinity) {
      quickTimeTotal.textContent = formatTime(duration);
    }
    const pct = duration ? (quickVideo.currentTime / duration) * 100 : 0;
    const safePct = Number.isNaN(pct) ? 0 : pct;
    quickProgress.value = safePct;
    quickProgressFill.style.width = `${safePct}%`;
    quickTimeCurrent.textContent = formatTime(quickVideo.currentTime);
  }

  function togglePlay() {
    const item = currentItem();
    if (!item || item.type !== 'video') return;
    if (quickVideo.paused) {
      quickVideo.play().catch(() => {});
      showPlayStatus(false);
    } else {
      quickVideo.pause();
      showPlayStatus(true);
    }
  }

  function closeViewer() {
    flowState.reset();
    collections.close();
    quickView.classList.remove('active');
    quickView.setAttribute('aria-hidden', 'true');
    quickVideo.pause();
    quickVideo.removeAttribute('src');
    quickVideo.removeAttribute('poster');
    quickVideo.load();
    quickVideo.classList.remove('active');
    quickImage.classList.remove('active');
    quickImage.src = '';
    quickPlayStatus.classList.remove('visible');
    quickProgress.value = 0;
    quickProgressFill.style.width = '0%';
    quickTimeCurrent.textContent = '00:00';
    quickTimeTotal.textContent = '00:00';
    updateSourceButton(null);
    mediaActions.clearCaption();
    magnifier.toggle(false);
    updateCollectionCoverButton(null);
    collections.sync('');
    flowSession.setIndex(-1);
    if (bodyScrollLocked) {
      document.body.style.overflow = bodyOverflowBackup;
      bodyOverflowBackup = '';
      bodyScrollLocked = false;
    }
    if (viewerPreviousFocus && viewerPreviousFocus.isConnected
      && typeof viewerPreviousFocus.focus === 'function') {
      viewerPreviousFocus.focus({ preventScroll: true });
    }
    viewerPreviousFocus = null;
  }

  function showItem(index) {
    if (index < 0 || index >= items.length) return;
    flowSession.setIndex(index);
    const item = items[index];
    quickCounter.textContent = `${index + 1} / ${items.length}`;
    quickDetail.href = item.detail_url || '#';
    magnifier.toggle(false);
    flowState.onMediaChanged();
    updateControls(item);
    updateCollectionCoverButton(item);

    if (item.type === 'video') {
      mediaActions.clearCaption();
      quickImage.classList.remove('active');
      quickImage.src = '';
      const nextSrc = String(item.media_url || '');
      if (quickVideo.getAttribute('src') !== nextSrc) {
        quickVideo.src = nextSrc;
        quickVideo.load();
        TikLocalResume.track(quickVideo, item.name, item.resume || 0);
      }
      quickVideo.poster = item.thumb_url || '';
      quickVideo.classList.add('active');
      quickVideo.playbackRate = speedOptions[currentSpeedIndex];
      quickProgress.value = 0;
      quickProgressFill.style.width = '0%';
      quickTimeCurrent.textContent = '00:00';
      quickTimeTotal.textContent = formatTime(quickVideo.duration);
      quickVideo.play()
        .then(() => quickPlayStatus.classList.remove('visible'))
        .catch(() => showPlayStatus(true));
    } else {
      quickVideo.pause();
      quickVideo.classList.remove('active');
      quickVideo.removeAttribute('src');
      quickVideo.removeAttribute('poster');
      quickVideo.load();
      quickImage.src = item.media_url;
      // The grid tile's thumbnail is decorative (the card carries the title), but the
      // quick viewer is the full-size view: screen readers need the file name here.
      quickImage.alt = String(item.name || '');
      quickImage.classList.add('active');
      mediaActions.loadCaption(item.name);
    }

    syncFavoriteState(item);
    collections.sync(item.name);
    syncSourceState(item);
    if (index >= items.length - 8 && flowSession.hasMore()) loadNextPage();
  }

  function openViewer(index) {
    if (index < 0 || index >= items.length) return;
    const douyinMode = window.TikLocalDouyinMode;
    if (douyinMode && douyinMode.enabled()) {
      const name = String(items[index]?.name || '');
      if (name && douyinMode.redirect(name)) return;
    }
    if (!quickView.classList.contains('active')) {
      bodyOverflowBackup = document.body.style.overflow || '';
      document.body.style.overflow = 'hidden';
      bodyScrollLocked = true;
      // The viewer is a modal layer: remember where focus came from so closing it
      // returns the user to the card they opened, not to the top of the grid.
      viewerPreviousFocus = document.activeElement;
    }
    quickView.classList.add('active');
    quickView.setAttribute('aria-hidden', 'false');
    quickView.setAttribute('tabindex', '-1');
    showItem(index);
    if (typeof quickView.focus === 'function') quickView.focus({ preventScroll: true });
  }

  async function nextItem() {
    if (getCurrentIndex() >= items.length - 1 && flowSession.hasMore()) await loadNextPage();
    if (getCurrentIndex() < items.length - 1) await showItem(getCurrentIndex() + 1);
  }

  async function prevItem() {
    if (getCurrentIndex() > 0) await showItem(getCurrentIndex() - 1);
  }

  async function syncSourceState(item) {
    if (!item?.name) {
      updateSourceButton(null);
      return;
    }
    try {
      await mediaActions.syncSource(item.name);
    } catch (error) {
      updateSourceButton(null);
    }
  }

  quickFavorite.addEventListener('click', async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const idx = getCurrentIndex();
    if (idx < 0 || idx >= items.length) return;
    const item = items[idx];
    if (!item?.name) return;
    // Send the target state instead of a blind toggle: with a second tab open
    // the server value may have moved on, and a toggle would invert it.
    const desired = quickFavorite.getAttribute('aria-pressed') !== 'true';
    try {
      const favorited = await mediaActions.toggleFavorite(item.name, desired);
      if (currentItem()?.name === item.name) updateFavoriteButton(!!favorited);
    } catch (error) {
      await syncFavoriteState(item);
      if (currentItem()?.name === item.name) {
        quickFavorite.title = T('library.quick.favorite_failed');
        quickFavorite.setAttribute('aria-label', quickFavorite.title);
      }
    }
  });

  async function setCurrentAsCollectionCover() {
    const item = currentItem();
    if (!item || !item.name || scope !== 'collection' || !collectionId) return;
    if (item.name === collectionCoverUri) return;
    quickSetCover.disabled = true;
    try {
      await window.TikLocalCollections.request(`/api/collections/${encodeURIComponent(collectionId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cover_uri: item.name }),
      });
      collectionCoverUri = String(item.name);
      updateCollectionIdentityCover(item);
      updateCollectionCoverButton(item);
    } catch (error) {
      quickSetCover.disabled = false;
      return;
    }
  }

  quickSetCover.addEventListener('click', async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await setCurrentAsCollectionCover();
  });

  quickCloseTop.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeViewer();
  });

  document.addEventListener('keydown', (event) => {
    // 「加入合集」等弹层里的输入框是 Quick Viewer 的兄弟节点，不挡住会把空格和方向键
    // 透给下面的查看器：空格被吞、方向键换掉底层媒体，最后写进合集的是另一张图。
    // 按钮/链接/角色按钮同样要放行：查看器里的关闭、倍速、收藏按钮靠 Space 激活，
    // 第 12 轮在 radio/detail 修过同一个漏网点，这里当时漏了。
    if (event.target instanceof Element && event.target.closest(
      'input, textarea, select, [contenteditable="true"], button, a[href], summary, [role="button"]'
    )) {
      return;
    }
    if (event.key === 'Escape' && organizeSheet?.classList.contains('active')) {
      closeOrganizeSheet();
      return;
    }
    if (event.key === 'Escape' && selecting) {
      setSelecting(false);
      return;
    }
    if (event.key === 'Escape' && collections.isOpen()) {
      collections.close();
      return;
    }
    if (!quickView.classList.contains('active')) return;
    if (event.key === 'Escape') closeViewer();
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') nextItem();
    if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') prevItem();
    if (event.key === ' ') {
      event.preventDefault();
      togglePlay();
    }
  });

  // ---- Saved view: collection filter chips + multi-select organize ----
  const favoriteCollectionBar = document.getElementById('favorite-collection-bar');
  const organizeToggle = document.getElementById('organize-toggle');
  const organizeBar = document.getElementById('organize-bar');
  const organizeSheet = document.getElementById('organize-sheet');
  let selecting = false;
  let organizeSheetMode = 'add';
  let organizeBusy = false;
  const selectedNames = new Set();

  function escapeHtmlText(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function currentEmptyMessage() {
    if (scope === 'favorite' && activeCollectionId) return T('library.empty.collection');
    // The generic copy claims the library has nothing to show; after a filter
    // that is simply false, so name the filter that emptied the grid.
    if (mode === 'big_files') return T('library.empty.big_files', { n: minMb });
    if (mode === 'video_latest') return T('library.empty.video_latest');
    if (mode === 'image_random') return T('library.empty.image_random');
    return emptyMessage;
  }

  function showOrganizeToast(message, kind) {
    if (window.TikLocalUI && typeof window.TikLocalUI.toast === 'function') {
      window.TikLocalUI.toast(message, kind || 'success');
    }
  }

  function syncFavoriteCollectionChips() {
    if (!favoriteCollectionBar) return;
    favoriteCollectionBar.querySelectorAll('[data-collection-id]').forEach((chip) => {
      const isActive = String(chip.dataset.collectionId || '') === activeCollectionId;
      chip.classList.toggle('is-active', isActive);
      if (isActive) chip.setAttribute('aria-current', 'page');
      else chip.removeAttribute('aria-current');
    });
  }

  function syncFavoriteCollectionUrl(replace = false) {
    if (scope !== 'favorite') return;
    const params = new URLSearchParams(window.location.search);
    if (activeCollectionId) params.set('collection', activeCollectionId);
    else params.delete('collection');
    const query = params.toString();
    const nextUrl = `/favorite${query ? `?${query}` : ''}${window.location.hash}`;
    window.history[replace ? 'replaceState' : 'pushState']({ collection: activeCollectionId }, '', nextUrl);
  }

  async function applyCollectionFilter(nextId, { push = true } = {}) {
    const target = String(nextId || '');
    if (target === activeCollectionId) return;
    activeCollectionId = target;
    activeScope = target ? 'collection' : 'favorite';
    if (push) syncFavoriteCollectionUrl(false);
    syncFavoriteCollectionChips();
    if (selecting) setSelecting(false);
    await reloadCurrentMode();
  }

  function refreshTileSelection() {
    grid?.querySelectorAll('.media-tile').forEach((tile) => {
      tile.classList.toggle('is-selected', selectedNames.has(String(tile.dataset.name || '')));
    });
  }

  function updateOrganizeBar() {
    if (!organizeBar) return;
    const countEl = document.getElementById('organize-count');
    if (countEl) countEl.textContent = T('library.organize.selected', { n: selectedNames.size });
    const inCollection = activeScope === 'collection' && !!activeCollectionId;
    const moveBtn = document.getElementById('organize-move');
    const removeBtn = document.getElementById('organize-remove');
    if (moveBtn) moveBtn.hidden = !inCollection;
    if (removeBtn) removeBtn.hidden = !inCollection;
    const hasSelection = selectedNames.size > 0;
    ['organize-add', 'organize-move', 'organize-remove'].forEach((id) => {
      const btn = document.getElementById(id);
      if (btn) btn.disabled = !hasSelection;
    });
    const selectAllBtn = document.getElementById('organize-select-all');
    if (selectAllBtn) {
      const allSelected = items.length > 0 && selectedNames.size >= items.length;
      selectAllBtn.textContent = allSelected ? T('library.organize.deselect_all') : T('library.organize.select_all');
    }
  }

  function setSelecting(next) {
    selecting = !!next;
    grid?.classList.toggle('is-selecting', selecting);
    document.body.classList.toggle('is-organizing', selecting);
    if (organizeBar) organizeBar.hidden = !selecting;
    if (!selecting) selectedNames.clear();
    refreshTileSelection();
    updateOrganizeBar();
  }

  function toggleSelectAll() {
    const allSelected = items.length > 0 && selectedNames.size >= items.length;
    selectedNames.clear();
    if (!allSelected) items.forEach((item) => selectedNames.add(String(item?.name || '')));
    refreshTileSelection();
    updateOrganizeBar();
  }

  async function sendCollectionUris(collectionId, method, uris) {
    const targetId = encodeURIComponent(collectionId);
    for (let start = 0; start < uris.length; start += 200) {
      await window.TikLocalCollections.request(`/api/collections/${targetId}/items`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uris: uris.slice(start, start + 200) }),
      });
    }
  }

  function closeOrganizeSheet() {
    if (!organizeSheet) return;
    organizeSheet.classList.remove('active');
    organizeSheet.setAttribute('aria-hidden', 'true');
    // aria-modal opened without moving focus in, so closing must not leave the
    // keyboard nowhere: hand it back to whichever button opened the sheet.
    const opener = organizeSheetOpener;
    organizeSheetOpener = null;
    if (opener && document.contains(opener)) {
      try { opener.focus(); } catch (error) { /* trigger went away */ }
    }
  }

  let organizeSheetOpener = null;

  async function openOrganizeSheet(mode) {
    if (!organizeSheet || !selectedNames.size || organizeBusy) return;
    organizeSheetMode = mode === 'move' ? 'move' : 'add';
    organizeSheetOpener = document.activeElement;
    const title = document.getElementById('organize-sheet-title');
    if (title) title.textContent = organizeSheetMode === 'move' ? T('library.organize.move') : T('library.organize.add');
    const listEl = document.getElementById('organize-sheet-list');
    if (listEl) listEl.innerHTML = `<div class="organize-sheet-empty">${T('common.loading')}</div>`;
    organizeSheet.classList.add('active');
    organizeSheet.setAttribute('aria-hidden', 'false');
    const closeBtn = document.getElementById('organize-sheet-close');
    if (closeBtn) {
      // Focus the sheet's own control first: the row list is still loading and a
      // screen reader must hear the dialog it just opened.
      try { closeBtn.focus(); } catch (error) { /* hidden sheet */ }
    }
    try {
      const data = await window.TikLocalCollections.request('/api/collections');
      const rows = (data?.items || []).filter((item) => organizeSheetMode === 'add' || String(item.id) !== activeCollectionId);
      if (!listEl) return;
      if (!rows.length) {
        listEl.innerHTML = `<div class="organize-sheet-empty">${T('library.organize.empty')}</div>`;
        return;
      }
      listEl.innerHTML = rows.map((item) => `
        <label class="organize-sheet-item" data-id="${escapeHtmlText(item.id)}">
          <input type="checkbox" value="${escapeHtmlText(item.id)}">
          <span class="organize-sheet-item-name">${escapeHtmlText(item.name)}</span>
          <span class="organize-sheet-item-count">${Number(item.item_count || 0)}</span>
        </label>`).join('');
    } catch (error) {
      if (listEl) listEl.innerHTML = `<div class="organize-sheet-empty">${escapeHtmlText(error?.message || T('library.organize.load_failed'))}</div>`;
    }
  }

  async function refreshFavoriteCollectionCounts() {
    if (!favoriteCollectionBar) return;
    try {
      const data = await window.TikLocalCollections.request('/api/collections');
      (data?.items || []).forEach((item) => {
        const chip = favoriteCollectionBar.querySelector(`[data-collection-id="${CSS.escape(String(item.id))}"] .favorite-collection-count`);
        if (chip) chip.textContent = String(Number(item.item_count || 0));
      });
    } catch (error) {
      // Count refresh is cosmetic; failures are ignored until the next reload.
    }
  }

  function finishOrganizeAction(message) {
    closeOrganizeSheet();
    selectedNames.clear();
    refreshTileSelection();
    updateOrganizeBar();
    showOrganizeToast(message, 'success');
    refreshFavoriteCollectionCounts();
    return reloadCurrentMode();
  }

  async function confirmOrganizeSheet() {
    if (!organizeSheet || organizeBusy) return;
    const sheetItems = [...organizeSheet.querySelectorAll('.organize-sheet-item input:checked')];
    const targetIds = sheetItems.map((input) => String(input.value || '')).filter(Boolean);
    if (!targetIds.length || !selectedNames.size) return;
    organizeBusy = true;
    const confirmBtn = document.getElementById('organize-sheet-confirm');
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = T('library.organize.processing');
    }
    const uris = [...selectedNames];
    try {
      for (const targetId of targetIds) await sendCollectionUris(targetId, 'POST', uris);
      if (organizeSheetMode === 'move' && activeCollectionId) {
        await sendCollectionUris(activeCollectionId, 'DELETE', uris);
      }
      await finishOrganizeAction(organizeSheetMode === 'move' ? T('library.organize.moved') : T('library.organize.added'));
    } catch (error) {
      showOrganizeToast(error?.message || T('library.organize.failed'), 'error');
    } finally {
      organizeBusy = false;
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = T('common.confirm');
      }
    }
  }

  async function removeSelectedFromCurrentCollection() {
    if (!selectedNames.size || activeScope !== 'collection' || !activeCollectionId) return;
    const removeBtn = document.getElementById('organize-remove');
    if (removeBtn) removeBtn.disabled = true;
    try {
      await sendCollectionUris(activeCollectionId, 'DELETE', [...selectedNames]);
      await finishOrganizeAction(T('library.organize.removed'));
    } catch (error) {
      showOrganizeToast(error?.message || T('library.organize.failed'), 'error');
    } finally {
      if (removeBtn) removeBtn.disabled = false;
    }
  }

  favoriteCollectionBar?.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-collection-id]');
    if (!chip) return;
    event.preventDefault();
    applyCollectionFilter(chip.dataset.collectionId);
  });
  if (scope === 'favorite') {
    window.addEventListener('popstate', () => {
      const nextId = new URLSearchParams(window.location.search).get('collection') || '';
      if (nextId !== activeCollectionId) applyCollectionFilter(nextId, { push: false });
    });
  }
  organizeToggle?.addEventListener('click', () => setSelecting(!selecting));
  document.getElementById('organize-done')?.addEventListener('click', () => setSelecting(false));
  document.getElementById('organize-select-all')?.addEventListener('click', toggleSelectAll);
  document.getElementById('organize-add')?.addEventListener('click', () => openOrganizeSheet('add'));
  document.getElementById('organize-move')?.addEventListener('click', () => openOrganizeSheet('move'));
  document.getElementById('organize-remove')?.addEventListener('click', removeSelectedFromCurrentCollection);
  document.getElementById('organize-sheet-close')?.addEventListener('click', closeOrganizeSheet);
  document.getElementById('organize-sheet-cancel')?.addEventListener('click', closeOrganizeSheet);
  document.getElementById('organize-sheet-confirm')?.addEventListener('click', confirmOrganizeSheet);
  organizeSheet?.addEventListener('click', (event) => {
    if (event.target === organizeSheet) closeOrganizeSheet();
  });
  syncFavoriteCollectionChips();

  quickOverlay.addEventListener('click', () => {
    const item = currentItem();
    if (!item) return;
    const now = Date.now();
    const delta = now - lastClickTime;
    if (delta > 0 && delta < 250) {
      if (clickTimer) clearTimeout(clickTimer);
      clickTimer = null;
      if (item.type === 'video') {
        togglePlay();
      }
    } else {
      if (clickTimer) clearTimeout(clickTimer);
      clickTimer = setTimeout(() => {
        toggleUI();
        clickTimer = null;
      }, 250);
    }
    lastClickTime = now;
  });

  quickSpeed.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    currentSpeedIndex = (currentSpeedIndex + 1) % speedOptions.length;
    const rate = speedOptions[currentSpeedIndex];
    quickSpeed.textContent = `${rate}x`;
    const item = currentItem();
    if (item?.type === 'video') {
      quickVideo.playbackRate = rate;
    }
  });

  quickCaption.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    const item = currentItem();
    if (!item || item.type !== 'image') return;
    mediaActions.generateCaption(item.name);
  });

  quickProgress.addEventListener('input', () => {
    const item = currentItem();
    if (!item || item.type !== 'video') return;

    if (!isDragging) {
      isDragging = true;
      wasPlayingBeforeDrag = !quickVideo.paused;
      quickVideo.pause();
    }
    const duration = quickVideo.duration || 0;
    const seekTime = (quickProgress.value / 100) * duration;
    quickTimeCurrent.textContent = formatTime(seekTime);
    quickProgressFill.style.width = `${quickProgress.value}%`;
  });

  quickProgress.addEventListener('change', () => {
    const item = currentItem();
    if (!item || item.type !== 'video') return;
    const duration = quickVideo.duration || 0;
    quickVideo.currentTime = (quickProgress.value / 100) * duration;
    if (wasPlayingBeforeDrag) {
      quickVideo.play().catch(() => {});
    }
    isDragging = false;
  });

  // Keyboard users must not tab into the grid hidden behind the viewer; pull focus
  // back whenever it escapes the layer.
  const collectionModal = document.getElementById('quick-collection-modal');
  document.addEventListener('focusin', (event) => {
    if (!quickView.classList.contains('active')) return;
    if (event.target === quickView || quickView.contains(event.target)) return;
    // The collection picker lives beside the viewer, not inside it: without this
    // exception the trap pulled focus out of its name field, so the field stayed
    // empty and creating a collection from the viewer never worked.
    if (collectionModal && collectionModal.contains(event.target)) return;
    quickView.focus({ preventScroll: true });
  });

  quickView.addEventListener('wheel', (event) => {
    if (!quickView.classList.contains('active')) return;
    const target = event.target;
    if (target && typeof target.closest === 'function') {
      if (target.closest('.quick-actions, #quick-controls, #quick-close-top, #quick-caption-panel, .quick-magnifier')) {
        return;
      }
    }
    if (magnifier.isActive()) return;
    const deltaY = event.deltaY || 0;
    if (Math.abs(deltaY) < 24) return;
    event.preventDefault();
    if (wheelLocked) return;
    wheelLocked = true;
    if (deltaY > 0) nextItem();
    else prevItem();
    setTimeout(() => {
      wheelLocked = false;
    }, 260);
  }, { passive: false });

  const swipe = new Hammer(quickOverlay);
  swipe.get('swipe').set({ direction: Hammer.DIRECTION_VERTICAL });
  swipe.on('swipeup', () => {
    if (magnifier.isActive()) return;
    nextItem();
  });
  swipe.on('swipedown', () => {
    if (magnifier.isActive()) return;
    prevItem();
  });

  window.addEventListener('resize', scheduleWaterfallRelayout);

  quickVideo.addEventListener('timeupdate', updateVideoProgress);
  quickVideo.addEventListener('loadedmetadata', updateVideoProgress);
  quickVideo.addEventListener('durationchange', updateVideoProgress);
  quickVideo.addEventListener('play', () => {
    quickPlayStatus.classList.remove('visible');
    magnifier.onVideoPlay(quickVideo);
  });
  quickVideo.addEventListener('seeked', () => magnifier.onVideoSeeked(quickVideo));
  quickVideo.addEventListener('pause', () => {
    const item = currentItem();
    if (item?.type === 'video') showPlayStatus(true);
  });

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) loadNextPage();
    });
  }, { rootMargin: '1000px 0px' });
  observer.observe(sentinel);

  const imageSearchForm = document.getElementById('image-search-form');
  const imageSearchStatus = document.getElementById('image-search-status');
  const imageSearchSetup = document.getElementById('image-search-setup');
  imageSearchForm?.addEventListener('submit', async event => {
    event.preventDefault();
    if (flowSession.isLoading()) {
      imageSearchStatus.textContent = T('library.status.search_wait');
      return;
    }
    const query = document.getElementById('image-search-query').value.trim();
    const mediaType = document.getElementById('media-search-type')?.value || 'all';
    if (!query) return;
    const requestId = ++searchRequest;
    const button = imageSearchForm.querySelector('[type="submit"]');
    button.disabled = true;
    button.textContent = T('library.status.searching');
    imageSearchForm.setAttribute('aria-busy', 'true');
    semanticSearch = true;
    if (discoveries) {
      discoveries.dataset.searching = 'true';
      discoveries.hidden = true;
    }
    imageSearchSetup.hidden = true;
    imageSearchStatus.textContent = T('library.status.searching_hint');
    try {
      const response = await fetch('/api/library/media-search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, media_type: mediaType }),
      });
      const payload = await response.json();
      if (requestId !== searchRequest) return;
      if (!payload.success) {
        const error = new Error(payload.error || T('library.status.search_failed'));
        error.status = response.status;
        throw error;
      }
      closeViewer();
      flowSession.reset({ hasMore: true });
      await flowSession.loadMore(async () => ({ items: payload.data.items, hasMore: false }));
      resetWaterfallLayout();
      relayoutWaterfall();
      feather.replace();
      document.getElementById('image-search-reset').hidden = false;
      const resultCount = payload.data.items.length;
      imageSearchStatus.textContent = T('library.status.search_results', { n: resultCount, query });
      setLoadingText(resultCount ? T('library.status.search_end') : T('library.status.search_empty'));
    } catch (error) {
      if (requestId === searchRequest) {
        imageSearchStatus.textContent = error.message;
        imageSearchSetup.hidden = error.status !== 409 && error.status !== 503;
        semanticSearch = false;
      }
    } finally {
      button.disabled = false;
      button.textContent = T('common.search');
      imageSearchForm.removeAttribute('aria-busy');
    }
  });
  document.getElementById('image-search-reset')?.addEventListener('click', () => {
    document.getElementById('image-search-query').value = '';
    reloadCurrentMode();
  });

  if (scope === 'all') {
    document.querySelectorAll('.mode-tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        const targetMode = tab.dataset.mode || 'all';
        if (targetMode === mode) return;
        reloadMode(targetMode);
      });
    });
    activeTab();
    syncLibraryUrl(true);
    window.addEventListener('popstate', () => {
      reloadModeFromUrl();
    });
    let searchTimer = null;
    searchInput?.addEventListener('input', () => {
      clearTimeout(searchTimer);
      syncSearchUI();
      searchTimer = setTimeout(async () => {
        searchQuery = String(searchInput.value || '').trim();
        if (searchQuery && mode !== 'all') {
          mode = 'all';
          seed = '';
        }
        syncLibraryUrl(true);
        await reloadCurrentMode();
      }, 300);
    });
    searchClearButton?.addEventListener('click', async () => {
      clearTimeout(searchTimer);
      searchQuery = '';
      searchInput.value = '';
      syncSearchUI();
      syncLibraryUrl(true);
      await reloadCurrentMode();
      searchInput.focus();
    });
    searchToggleButton?.addEventListener('click', () => {
      const isOpen = libraryToolbar?.classList.contains('is-search-open');
      setSearchOpen(!isOpen, { focus: !isOpen });
    });
    searchInput?.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || String(searchInput.value || '').trim()) return;
      event.preventDefault();
      setSearchOpen(false);
      searchToggleButton?.focus();
    });
  }

  retryButton.addEventListener('click', async () => {
    // 时间线视图的重试由 library_timeline_controller.js 自己接管（它标记了 dataset），
    // 这里只负责网格视图，避免一次点击同时触发两套加载。
    if (retryButton.dataset.timelineBound === '1') return;
    setLoadingText(T('library.status.reloading'));
    await loadNextPage();
  });

  clearSearchButton.addEventListener('click', async () => {
    searchQuery = '';
    if (searchInput) searchInput.value = '';
    syncSearchUI();
    syncLibraryUrl(true);
    await reloadCurrentMode();
    searchInput?.focus();
  });

  quickSpeed.textContent = `${speedOptions[currentSpeedIndex]}x`;
  const monthHeading = document.querySelector('[data-month-heading]');
  if (monthHeading && /^\d{4}-\d{2}$/.test(timelineMonth)) {
    const [year, monthNumber] = timelineMonth.split('-');
    monthHeading.textContent = new Intl.DateTimeFormat(dateLocale(), { year: 'numeric', month: 'long' }).format(new Date(year, Number(monthNumber) - 1, 1));
  }
  relayoutWaterfall();
  if (!items.length && !flowSession.hasMore()) {
    if (searchQuery) {
      setLoadingText(T('library.status.no_results', { query: searchQuery }), { clearSearch: true });
    } else {
      // Scope-only filters (favorite + a collection, big files, latest videos) have
      // their own copy; the generic one claims the whole library is empty.
      setLoadingText(currentEmptyMessage());
    }
  } else if (!flowSession.hasMore()) {
    setLoadingText(T('library.status.end'));
  } else {
    setLoadingText(T('library.status.keep_scrolling'));
  }
  // 网络恢复后自动补拉：否则首屏失败时用户只看到「加载失败/重试」，
  // 明明网络已经恢复也会以为应用坏了。
  window.addEventListener('tiklocal:online', () => {
    if (flowSession.isLoading()) return;
    loadNextPage();
  });

  // 删除发生在详情页；浏览器从 bfcache 恢复本页时拿到的是删除前的网格与
  // `items` 数组，DOMContentLoaded 不会再触发，被删的卡片会留在原地并可点出 404。
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) location.reload();
  });

  feather.replace();
  syncSearchUI();
  maybeOpenFocusedItem();
})();
