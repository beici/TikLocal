  (async () => {
    const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
    document.body.classList.add('immersive');
    // Douyin-style chrome follows the settings toggle; the class gates flow.css.
    const douyinModeOn = window.localStorage.getItem('douyin_mode') === '1';
    if (douyinModeOn) document.body.classList.add('douyin');

    const feedContainer = document.getElementById('feed-container');
    const videoStartCover = document.getElementById('video-start-cover');
    const overlayLayer = document.getElementById('overlay-layer');
    const speedBtn = document.getElementById('speed-btn');
    const speedWrap = document.getElementById('speed-tool-wrap');
    const speedChoices = document.getElementById('speed-options');
    const hud = document.getElementById('flow-hud');
    const captionBtn = document.getElementById('caption-btn');
    const magnifierToolWrap = document.getElementById('magnifier-tool-wrap');
    const favoriteBtn = document.getElementById('favorite-btn');
    const collectionBtn = document.getElementById('collection-btn');
    const infoBtn = document.getElementById('info-btn');

    const douyinTopbar = document.getElementById('douyin-topbar');
    const douyinTopBack = document.getElementById('douyin-top-back');
    const douyinTopic = document.getElementById('douyin-topic');
    const douyinTopicName = document.getElementById('douyin-topic-name');
    const douyinTopicCount = document.getElementById('douyin-topic-count');
    const douyinAvatar = document.getElementById('douyin-avatar');
    const douyinAvatarImg = document.getElementById('douyin-avatar-img');
    const douyinRail = document.getElementById('douyin-rail');
    const douyinFavoriteBtn = document.getElementById('douyin-favorite-btn');
    const douyinCollectionBtn = document.getElementById('douyin-collection-btn');
    const douyinCollectionCount = document.getElementById('douyin-collection-count');
    const douyinInfoBtn = document.getElementById('douyin-info-btn');
    const douyinAuthor = document.getElementById('douyin-author');
    const douyinCaption = document.getElementById('douyin-caption');
    const douyinCaptionText = document.getElementById('douyin-caption-text');
    const douyinCaptionExpand = document.getElementById('douyin-caption-expand');
    const douyinCaptionFull = document.getElementById('douyin-caption-full');
    const douyinCaptionFullText = document.getElementById('douyin-caption-full-text');
    const douyinCaptionCollapse = document.getElementById('douyin-caption-collapse');

    // The topic header renders before the feed so /flow?tag= looks right at once.
    syncTopicHeader();

    const playStatusIcon = document.getElementById('play-status-icon');
    const customControls = document.getElementById('custom-controls');
    const progressBar = document.getElementById('video-progress');
    const progressFill = document.getElementById('progress-fill');
    const timeCurrent = document.getElementById('time-current');
    const timeTotal = document.getElementById('time-total');

    const captionPanel = document.getElementById('caption-panel');
    const captionTitle = document.getElementById('caption-title');
    const captionTags = document.getElementById('caption-tags');
    const flowStatus = document.getElementById('flow-state');
    const flowStatusTitle = document.getElementById('flow-state-title');
    const flowStatusDetail = document.getElementById('flow-state-detail');
    const flowStatusActions = document.getElementById('flow-state-actions');
    const flowStatusRetry = document.getElementById('flow-state-retry');
    const flowStatusNext = document.getElementById('flow-state-next');
    const flowStatusLibrary = document.getElementById('flow-state-library');
    const uiShared = window.FlowUIShared || {};
    const actionsShared = window.FlowActionsShared || {};

    // Holding a video plays it at this rate until the finger lifts.
    const holdRate = 2;
    const keySeekSeconds = 5;
    // A full-width drag covers the whole clip, but never more than this many seconds.
    const scrubSpanSeconds = 90;

    const flowSession = window.createFlowSession({
      initialItems: [],
      initialHasMore: true,
      initialCursor: { page: 1 },
      keyOf: (item) => String(item?.name || ''),
    });
    const feedItems = flowSession.items;
    let seed = '';
    // The server freezes one cutoff per session and echoes it back; sending it on
    // every page is what keeps page 2 from repeating cards that page 1 already
    // showed (the duplicates were silently dropped by appendUnique).
    let feedSince = '';
    // /flow?mode=forgotten plays only old media that has not been shown for a long time.
    const feedMode = new URLSearchParams(window.location.search).get('mode') === 'forgotten' ? 'forgotten' : '';
    document.getElementById('flow-mode').hidden = !feedMode;
    const activitySessionId = globalThis.crypto?.randomUUID?.()
      || `flow-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    let activeActivity = null;

    function postActivity(events, useBeacon = false) {
      const valid = events.filter(Boolean);
      if (!valid.length) return;
      const body = JSON.stringify({
        events: valid,
        _csrf_token: window.TikLocalSecurity?.csrfToken || '',
      });
      if (useBeacon && navigator.sendBeacon) {
        navigator.sendBeacon('/api/activity', new Blob([body], { type: 'application/json' }));
        return;
      }
      fetch('/api/activity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
      }).catch(() => {});
    }

    function activityTarget(item) {
      if (!item) return null;
      if (item.type === 'image_group') {
        const entries = Array.isArray(item.items) ? item.items : [];
        const index = Math.max(0, Math.min(Number(item.activeChildIndex) || 0, entries.length - 1));
        const entry = entries[index];
        if (!entry?.name) return null;
        // A live-photo album can hold a video cover, so report the child's own
        // type: the impression penalty is keyed by media_type, and marking a
        // video as an image leaves it unpenalised for the next page.
        return { uri: entry.name, type: entry.type === 'video' ? 'video' : 'image', el: item.el };
      }
      if (!['video', 'image'].includes(item.type) || !item.name) return null;
      return { uri: item.name, type: item.type, el: item.el };
    }

    function beginActivity(item) {
      const target = activityTarget(item);
      if (!target) return null;
      if (target.type === 'video' && target.el) {
        target.el._completedInView = false;
        target.el._activityPlayedSeconds = 0;
        target.el._activityLastPosition = null;
      }
      activeActivity = { target, startedAt: performance.now() };
      return {
        session_id: activitySessionId,
        uri: target.uri,
        media_type: target.type,
        surface: 'flow',
        event: 'impression',
      };
    }

    function finishActivity() {
      if (!activeActivity) return null;
      const { target, startedAt } = activeActivity;
      activeActivity = null;
      const visibleMs = Math.max(0, Math.round(performance.now() - startedAt));
      let ratio = null;
      if (target.type === 'video' && Number.isFinite(target.el?.duration) && target.el.duration > 0) {
        ratio = Math.max(0, Math.min(Number(target.el._activityPlayedSeconds || 0) / target.el.duration, 1));
      }
      const completed = target.type === 'video'
        ? !!target.el?._completedInView
        : visibleMs >= 5000;
      const skipped = target.type === 'video'
        ? visibleMs < 2200 && (ratio === null || ratio < 0.12)
        : visibleMs < 1600;
      return {
        session_id: activitySessionId,
        uri: target.uri,
        media_type: target.type,
        surface: 'flow',
        event: completed ? 'complete' : (skipped ? 'skip' : 'consumed'),
        ratio,
        visible_ms: visibleMs,
      };
    }

    let speedRate = 1;
    let boostedVideo = null;
    let hudTimer = null;
    let isDragging = false;
    let wasPlayingBeforeDrag = false;
    let flowLoadingTimer = null;
    let videoStartCoverTimer = null;
    let videoActivationId = 0;
    // Unplayable media is skipped with a short note; the full status only shows when skips repeat.
    const maxQuietSkips = 3;
    let quietSkips = 0;
    let skipTimer = null;
    let lastStep = 1;

    function isVideoStartReady(videoEl) {
      return videoEl?._startReady === true
        && videoEl.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA;
    }

    function showVideoStartCover(item) {
      clearTimeout(videoStartCoverTimer);
      // The thumbnail matches what the page slid in with, so the hand-off does not flash black.
      videoStartCover.style.backgroundImage = item?.thumb_url ? `url("${item.thumb_url}")` : '';
      videoStartCover.classList.remove('is-waiting');
      videoStartCover.classList.add('is-visible');
      videoStartCover.setAttribute('aria-hidden', 'false');
      videoStartCoverTimer = setTimeout(() => {
        videoStartCover.classList.add('is-waiting');
      }, 250);
    }

    function hideVideoStartCover() {
      clearTimeout(videoStartCoverTimer);
      videoStartCover.classList.remove('is-visible', 'is-waiting');
      videoStartCover.setAttribute('aria-hidden', 'true');
    }

    function showFlowStatus(kind, title, detail = '') {
      clearTimeout(flowLoadingTimer);
      flowLoadingTimer = null;
      flowStatus.dataset.kind = kind;
      flowStatusTitle.textContent = title;
      flowStatusDetail.textContent = detail;
      flowStatusRetry.hidden = !['error', 'media-error'].includes(kind);
      flowStatusNext.hidden = kind !== 'media-error';
      flowStatusLibrary.hidden = !['empty', 'error'].includes(kind);
      flowStatusActions.hidden = !['empty', 'error', 'media-error'].includes(kind);
      flowStatus.classList.add('is-visible');
      flowStatus.setAttribute('aria-hidden', 'false');
    }

    function hideFlowStatus() {
      clearTimeout(flowLoadingTimer);
      flowLoadingTimer = null;
      flowStatus.classList.remove('is-visible');
      flowStatus.setAttribute('aria-hidden', 'true');
    }

    function scheduleFlowLoadingStatus() {
      clearTimeout(flowLoadingTimer);
      flowLoadingTimer = setTimeout(() => {
        showFlowStatus('loading', T('flow.state.preparing'));
      }, 250);
    }

    const { formatTime } = uiShared;
    const currentItem = () => flowSession.currentItem();
    const getCurrentIndex = () => flowSession.getIndex();

    const magnifier = uiShared.createMagnifier({
      lens: document.getElementById('magnifier'),
      toggle: document.getElementById('magnifier-toggle'),
      zoomOptions: document.getElementById('zoom-options'),
      badge: document.getElementById('mag-badge'),
      zoomButtons: document.querySelectorAll('#zoom-options .zoom-btn'),
      largeClass: 'magnifier-large',
      settleTransition: 'transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)',
      setMagnifying: (enabled) => flowState.setMagnifying(enabled),
      target: () => {
        const item = currentItem();
        if (item?.type === 'video') return { type: 'video', el: item.el };
        if (item?.type === 'image') return { type: 'image', el: item.el };
        if (item?.type === 'image_group') return { type: 'image', el: item.el?.querySelector('.image-group-media') };
        return null;
      },
    });

    const flowState = window.createFlowStateController({
      getMediaType: () => currentItem()?.type || '',
      canMagnifyMedia: (mediaType) => mediaType === 'image' || mediaType === 'video',
      onImmersiveChange: (enabled) => document.body.classList.toggle('immersive-mode', !!enabled),
      onMagnifyingChange: (enabled) => magnifier.onStateChange(enabled),
    });

    const toggleUI = () => flowState.toggleImmersive();

    const collections = window.TikLocalCollections.createPicker({
      button: collectionBtn,
      countEl: document.getElementById('collection-count'),
      modal: document.getElementById('collection-modal'),
      closeBtn: document.getElementById('collection-modal-close'),
      meta: document.getElementById('collection-modal-meta'),
      createBtn: document.getElementById('collection-create-btn'),
      nameInput: document.getElementById('collection-name-input'),
      list: document.getElementById('collection-list'),
      itemClass: 'collection-modal-item',
      emptyClass: 'collection-modal-empty',
      currentUri: () => currentDisplayEntry()?.name,
    });

    function clearCaption() {
      captionTitle.textContent = '';
      captionTitle.classList.add('hidden');
      captionTags.innerHTML = '';
      captionPanel.classList.add('is-hidden');
    }

    function renderCaption(data) {
      if (!data) {
        clearCaption(false);
        return;
      }
      const title = String(data.title || '').trim();
      const tags = Array.isArray(data.tags) ? data.tags : [];
      if (!title && tags.length === 0) {
        clearCaption(false);
        return;
      }
      captionPanel.classList.remove('is-hidden');
      captionTitle.textContent = title;
      captionTitle.classList.toggle('hidden', !title);
      captionTags.innerHTML = '';
      tags.forEach((tag) => {
        const chip = document.createElement('span');
        chip.className = 'caption-tag';
        chip.textContent = String(tag || '');
        captionTags.appendChild(chip);
      });
    }

    function setCaptionLoading(loading) {
      captionBtn.disabled = !!loading;
      captionBtn.classList.toggle('is-loading', !!loading);
    }

    function flashCaptionError() {
      captionBtn.classList.add('is-error');
      setTimeout(() => captionBtn.classList.remove('is-error'), 1400);
    }

    const mediaActions = window.createFlowMediaActionsController({
      actions: actionsShared,
      onFavoriteChange: (value, name) => {
        if (favoriteBtn.dataset.value !== name) return;
        favoriteBtn.classList.toggle('is-active', !!value);
        favoriteBtn.setAttribute('aria-pressed', String(!!value));
        favoriteBtn.setAttribute('aria-label', value ? T('flow.favorite.remove') : T('flow.favorite.add'));
        favoriteBtn.title = value ? T('flow.favorite.remove') : T('flow.favorite.add');
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
      confirmCaptionReplace: () => TikLocalUI.confirm({ title: T('flow.caption.replace_title'), message: T('flow.caption.replace_copy'), confirmLabel: T('flow.caption.replace_action') }),
    });


    function escapeHtml(value) {
      return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    // --- Douyin-style chrome helpers ---

    const DEFAULT_AVATAR = '/static/default-avatar.jpg';

    function douyinSourceId(entry) {
      // build_author_caption points at /flow?blog=<source_id>; the author page
      // lives at /author/<source_id>, so the id is all we need from it.
      const match = /[?&]blog=([^&]+)/.exec(String(entry?.author_url || ''));
      return match ? decodeURIComponent(match[1]) : '';
    }

    function douyinAuthorHref(entry) {
      const sourceId = douyinSourceId(entry);
      return sourceId ? `/author/${encodeURIComponent(sourceId)}` : String(entry?.author_url || '/flow');
    }

    function douyinAuthorAvatar(entry) {
      const sourceId = douyinSourceId(entry);
      return sourceId ? `/author/${encodeURIComponent(sourceId)}/avatar` : DEFAULT_AVATAR;
    }

    function douyinCaptionHtml(text) {
      // Split on hashtags first, then escape each part: escaping before the
      // match turns an apostrophe into &#39; and the tag regex would then treat
      // the tail of that entity as a hashtag.
      return String(text || '').split(/(#[^\s#]+)/).map((part) => {
        if (part.length > 1 && part.charAt(0) === '#') {
          const name = part.slice(1);
          const href = `/flow?tag=${encodeURIComponent(name)}`;
          return `<a class="douyin-tag" href="${escapeHtml(href)}" data-tag="${escapeHtml(name)}">${escapeHtml(part)}</a>`;
        }
        return escapeHtml(part);
      }).join('');
    }

    function mirrorDouyinActions() {
      if (!douyinFavoriteBtn) return;
      const liked = favoriteBtn.classList.contains('is-active');
      douyinFavoriteBtn.classList.toggle('is-active', liked);
      douyinFavoriteBtn.setAttribute('aria-pressed', String(liked));
      const collected = collectionBtn.classList.contains('has-collection');
      douyinCollectionBtn.classList.toggle('is-active', collected);
      douyinCollectionBtn.classList.toggle('has-collection', collected);
      douyinCollectionBtn.setAttribute('aria-pressed', String(collected));
      const count = collectionBtn.querySelector('.collection-count')?.textContent || '0';
      if (douyinCollectionCount) douyinCollectionCount.textContent = count;
    }

    // Swiping to the next clip used to keep the previous clip's heart/star until
    // the new favourite state arrived, which read as a flicker. Clear both rails
    // immediately and mute the transitions until the state is known again.
    let douyinSyncTimer = 0;

    function resetDouyinActionState() {
      if (!douyinModeOn) return;
      favoriteBtn.classList.remove('is-active');
      favoriteBtn.setAttribute('aria-pressed', 'false');
      collectionBtn.classList.remove('has-collection');
      if (douyinFavoriteBtn) {
        douyinFavoriteBtn.classList.remove('is-active');
        douyinFavoriteBtn.setAttribute('aria-pressed', 'false');
      }
      if (douyinCollectionBtn) {
        douyinCollectionBtn.classList.remove('is-active', 'has-collection');
        douyinCollectionBtn.setAttribute('aria-pressed', 'false');
      }
      if (douyinRail) {
        douyinRail.classList.add('is-syncing');
        clearTimeout(douyinSyncTimer);
        douyinSyncTimer = setTimeout(() => douyinRail.classList.remove('is-syncing'), 360);
      }
    }

    function closeDouyinCaptionFull() {
      if (!douyinCaptionFull) return;
      douyinCaptionFull.classList.remove('is-open');
      douyinCaptionFull.hidden = true;
    }

    function syncDouyinChrome(item) {
      if (!douyinModeOn || !douyinAuthor) return;
      const entry = item || {};
      const author = String(entry.author_name || '');
      const caption = String(entry.caption_text || '');

      douyinAuthor.hidden = !author;
      douyinAvatar.hidden = !author;
      if (author) {
        const authorHref = douyinAuthorHref(entry);
        douyinAuthor.textContent = `@${author}`;
        douyinAuthor.href = authorHref;
        douyinAvatar.href = authorHref;
        const avatarUrl = douyinAuthorAvatar(entry);
        if (douyinAvatarImg.dataset.src !== avatarUrl) {
          douyinAvatarImg.dataset.src = avatarUrl;
          douyinAvatarImg.src = avatarUrl;
        }
      }

      douyinCaption.hidden = !caption;
      if (caption) {
        douyinCaptionText.innerHTML = douyinCaptionHtml(caption);
        douyinCaptionFullText.innerHTML = douyinCaptionHtml(caption);
        // Whether the two-line clamp truncated only settles after layout.
        requestAnimationFrame(() => {
          douyinCaptionExpand.hidden = douyinCaptionText.scrollHeight <= douyinCaptionText.clientHeight + 2;
        });
      } else {
        douyinCaptionExpand.hidden = true;
        closeDouyinCaptionFull();
      }

      douyinInfoBtn.href = infoBtn.href;
      mirrorDouyinActions();
    }

    // --- Topic (hashtag) feed header ---

    function currentTopicTag() {
      return new URLSearchParams(window.location.search).get('tag') || '';
    }

    function syncTopicHeader() {
      const tag = currentTopicTag();
      if (!tag) return;
      document.body.classList.add('tag-feed');
      if (douyinTopic) douyinTopic.hidden = false;
      if (douyinTopicName) douyinTopicName.textContent = tag;
      if (douyinTopBack) douyinTopBack.hidden = false;
      document.title = `#${tag} · tikLocal`;
    }

    function syncTopicCount(count) {
      if (!douyinTopicCount || !currentTopicTag()) return;
      const template = window.TikLocalI18n?.t('flow.tag.count', { count }) || `${count}`;
      douyinTopicCount.textContent = template;
    }

    // Deep links from the author page and the detail page land on one specific work.
    function flowStartUri() {
      return new URLSearchParams(window.location.search).get('start') || '';
    }

    function flowStartIndex() {
      const uri = flowStartUri();
      if (!uri) return 0;
      const direct = feedItems.findIndex((item) => item && item.name === uri);
      if (direct >= 0) return direct;
      // Album works collapse into one feed entry; jump to the requested child.
      const grouped = feedItems.findIndex(
        (item) => Array.isArray(item?.items) && item.items.some((child) => child && child.name === uri)
      );
      if (grouped >= 0) {
        const childIndex = feedItems[grouped].items.findIndex((child) => child && child.name === uri);
        if (childIndex >= 0) feedItems[grouped].activeChildIndex = childIndex;
        return grouped;
      }
      return 0;
    }

    async function goNext() {
      if (getCurrentIndex() >= feedItems.length - 1 && flowSession.hasMore()) {
        await loadFeed();
      }
      slidePage(1);
    }

    function goPrev() {
      slidePage(-1);
    }

    // Paging moves the current item and its neighbour together, so a swipe follows the finger.
    const pageEase = 'cubic-bezier(0.22, 1, 0.36, 1)';
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let pager = null;
    let settling = false;
    let queuedSwipe = 0;

    function setPeek(item, visible) {
      if (!item) return;
      item.el.classList.toggle('active', visible);
      item.el.style.display = visible ? 'block' : 'none';
      item.el.style.removeProperty('transition');
      item.el.style.translate = '';
    }

    function placePage(offset, duration = 0) {
      const transition = duration ? `translate ${duration}ms ${pageEase}` : 'none';
      const height = feedContainer.clientHeight;
      [currentItem()?.el, videoStartCover].forEach((el) => {
        if (!el) return;
        // Inline !important: the global stylesheet disables transitions on img/video.
        el.style.setProperty('transition', transition, 'important');
        el.style.translate = `0 ${offset}px`;
      });
      if (pager?.peer) {
        pager.peer.el.style.setProperty('transition', transition, 'important');
        pager.peer.el.style.translate = `0 ${offset + pager.dir * height}px`;
      }
    }

    function beginPage(dir) {
      const index = getCurrentIndex() + dir;
      const peer = feedItems[index] || null;
      if (pager?.peer && pager.peer !== peer) setPeek(pager.peer, false);
      pager = { dir, index, peer };
      if (peer) setPeek(peer, true);
    }

    function settlePage(commit, offset) {
      if (!pager) return;
      const { index, peer, dir } = pager;
      const height = feedContainer.clientHeight;
      const moving = commit && !!peer;
      const target = moving ? -dir * height : 0;
      const distance = Math.abs(target - offset) / Math.max(height, 1);
      const duration = reduceMotion.matches ? 0 : Math.round(Math.min(340, Math.max(170, distance * 380)));
      settling = true;
      if (duration) placePage(target, duration);
      window.setTimeout(() => {
        [currentItem()?.el, videoStartCover].forEach((el) => {
          if (!el) return;
          el.style.removeProperty('transition');
          el.style.translate = '';
        });
        if (moving) {
          peer.el.style.removeProperty('transition');
          peer.el.style.translate = '';
        } else {
          setPeek(peer, false);
        }
        pager = null;
        settling = false;
        if (moving) showItem(index);
        if (queuedSwipe) {
          const queuedDir = queuedSwipe;
          queuedSwipe = 0;
          if (feedItems[getCurrentIndex() + queuedDir]) slidePage(queuedDir);
        }
      }, duration);
    }

    function slidePage(dir) {
      if (settling || pager || !feedItems[getCurrentIndex() + dir]) return;
      // Neutralise the rail before the slide starts so the outgoing clip's heart/star
      // state never paints over the incoming one.
      resetDouyinActionState();
      beginPage(dir);
      placePage(0);
      void feedContainer.offsetHeight;
      settlePage(true, 0);
    }

    function goGroupNext() {
      const item = currentItem();
      if (!item || item.type !== 'image_group' || typeof item.renderChild !== 'function') return false;
      const entries = Array.isArray(item.items) ? item.items : [];
      const nextIndex = (Number(item.activeChildIndex) || 0) + 1;
      if (nextIndex >= entries.length) return false;
      const previousActivity = finishActivity();
      item.activeChildIndex = item.renderChild(nextIndex);
      postActivity([previousActivity, beginActivity(item)]);
      return true;
    }

    function goGroupPrev() {
      const item = currentItem();
      if (!item || item.type !== 'image_group' || typeof item.renderChild !== 'function') return false;
      const nextIndex = (Number(item.activeChildIndex) || 0) - 1;
      if (nextIndex < 0) return false;
      const previousActivity = finishActivity();
      item.activeChildIndex = item.renderChild(nextIndex);
      postActivity([previousActivity, beginActivity(item)]);
      return true;
    }

    // Dragging sideways on a gallery moves the current child with the finger
    // and pulls the neighbouring child in from the edge of the fixed stage;
    // releasing past a threshold (or with a flick) commits the switch.
    let groupDrag = null;

    function startGroupDrag(dx) {
      const item = currentItem();
      if (groupDrag || !item || item.type !== 'image_group') return !!groupDrag;
      const panel = item.el;
      if (!panel || typeof panel._createGroupChild !== 'function') return false;
      const dragStage = panel.querySelector('.image-group-stage');
      if (!dragStage) return false;
      const members = Array.isArray(item.items) ? item.items : [];
      const index = Number(item.activeChildIndex) || 0;
      const dir = dx < 0 ? 1 : -1;
      const target = index + dir;
      if (target < 0 || target >= members.length) return false;
      const entry = members[target];
      if (!entry || !entry.media_url) return false;
      const active = dragStage.querySelector('.image-group-media');
      if (!active) return false;
      const neighbor = panel._createGroupChild(entry, false);
      neighbor.dataset.groupGeneration = String(Number(panel._groupGeneration) || 1);
      neighbor.style.transition = 'none';
      neighbor.style.opacity = '1';
      neighbor.style.transform = `translateX(${dir * 100}%)`;
      dragStage.appendChild(neighbor);
      active.style.transition = 'none';
      groupDrag = {
        panel,
        stage: dragStage,
        active,
        neighbor,
        dir,
        target,
        width: dragStage.clientWidth || 1,
      };
      return true;
    }

    function applyGroupDrag(dx) {
      if (!groupDrag) return;
      const { dir, width } = groupDrag;
      // Past the rest position the strip resists instead of moving on.
      const travel = dir === 1 ? Math.min(dx, 0) : Math.max(dx, 0);
      const eased = travel + (dx - travel) * 0.25;
      groupDrag.active.style.transform = `translateX(${eased}px)`;
      groupDrag.neighbor.style.transform = `translateX(${eased + dir * width}px)`;
    }

    function finishGroupDragAnimated(commit) {
      const drag = groupDrag;
      if (!drag) return;
      groupDrag = null;
      const settleMs = reduceMotion.matches ? 0 : 220;
      const ease = 'cubic-bezier(0.22, 1, 0.36, 1)';
      const transition = settleMs ? `transform ${settleMs}ms ${ease}` : 'none';
      drag.active.style.transition = transition;
      drag.neighbor.style.transition = transition;
      if (commit) {
        drag.active.style.transform = `translateX(${-drag.dir * drag.width}px)`;
        drag.neighbor.style.transform = 'translateX(0)';
        window.setTimeout(() => {
          if (drag.active.parentNode === drag.stage) drag.active.remove();
        }, settleMs + 60);
      } else {
        drag.active.style.transform = 'translateX(0)';
        drag.neighbor.style.transform = `translateX(${drag.dir * drag.width}px)`;
        window.setTimeout(() => {
          if (drag.neighbor.parentNode === drag.stage) drag.neighbor.remove();
        }, settleMs + 60);
      }
    }

    function commitGroupDrag() {
      const drag = groupDrag;
      if (!drag) return;
      const { panel, neighbor, target } = drag;
      const item = currentItem();
      const members = Array.isArray(item?.items) ? item.items : [];
      panel._groupGeneration = (Number(panel._groupGeneration) || 0) + 1;
      neighbor.dataset.groupGeneration = String(panel._groupGeneration);
      // The outgoing child carries the new generation too, so the render safety
      // net lets it finish its exit animation instead of yanking it early.
      drag.active.dataset.groupGeneration = String(panel._groupGeneration);
      drag.active._liveVideo?.pause();
      panel._activeChildIndex = target;
      panel._activeLiveVideo = neighbor._liveVideo || null;
      // A commit swaps the child in place instead of re-rendering the group.
      panel._syncGroupCounter?.(target, members.length);
      const previousActivity = finishActivity();
      if (item) {
        item.activeChildIndex = target;
        updateControls(item);
      }
      postActivity([previousActivity, beginActivity(item)]);
      if (neighbor._liveVideo) playLiveVideo(neighbor._liveVideo);
      finishGroupDragAnimated(true);
    }

    function updateFavoriteState(name) {
      mediaActions.syncFavorite(name)
        .catch(() => {
          if (favoriteBtn.dataset.value === name) favoriteBtn.classList.remove('is-active');
        });
    }

    function updateControls(item) {
      const isVideo = item.type === 'video';
      const isImageGroup = item.type === 'image_group';
      const displayEntry = currentDisplayEntry();
      speedWrap.hidden = !isVideo;
      closeSpeedChoices();
      captionBtn.hidden = isVideo || isImageGroup;
      magnifierToolWrap.hidden = isImageGroup;
      customControls.hidden = !isVideo;

      const detailUrl = String(displayEntry?.detail_url || item.detail_url || '#');
      const mediaName = String(displayEntry?.name || item.name || '');
      const mediaChanged = favoriteBtn.dataset.value !== mediaName;
      infoBtn.href = detailUrl;
      favoriteBtn.dataset.value = mediaName;
      if (mediaChanged) resetDouyinActionState();
      if (mediaName) {
        updateFavoriteState(mediaName);
        collections.sync(mediaName);
      }

      if (isVideo) {
        progressBar.disabled = false;
        playStatusIcon.classList.remove('hidden');
      } else {
        progressBar.disabled = true;
        progressBar.value = 0;
        progressFill.style.width = '0%';
        timeCurrent.textContent = '';
        timeTotal.textContent = '';
        playStatusIcon.classList.add('hidden');
        playStatusIcon.classList.remove('visible');
      }

      syncDouyinChrome(item);
    }

    function ensureVideoSrc(videoEl) {
      if (!videoEl) return;
      if (!videoEl.src) {
        videoEl.src = videoEl.dataset.src || '';
        videoEl.load();
      }
    }

    function waitForVideoState(videoEl, eventNames, condition, timeoutMs = 8000) {
      if (condition()) return Promise.resolve();
      return new Promise((resolve, reject) => {
        let timeoutId = null;
        const names = Array.isArray(eventNames) ? eventNames : [eventNames];
        const cleanup = () => {
          names.forEach((name) => videoEl.removeEventListener(name, onStateChange));
          videoEl.removeEventListener('error', onError);
          clearTimeout(timeoutId);
        };
        const onStateChange = () => {
          if (!condition()) return;
          cleanup();
          resolve();
        };
        const onError = () => {
          cleanup();
          reject(new Error('Video failed while preparing random start'));
        };
        names.forEach((name) => videoEl.addEventListener(name, onStateChange));
        videoEl.addEventListener('error', onError, { once: true });
        timeoutId = setTimeout(() => {
          cleanup();
          reject(new Error('Timed out while preparing random start'));
        }, timeoutMs);
      });
    }

    function prepareVideoStart(videoEl) {
      if (!videoEl) return Promise.reject(new Error('Missing video element'));
      if (isVideoStartReady(videoEl)) return Promise.resolve(videoEl);
      if (videoEl._startPromise) return videoEl._startPromise;

      videoEl._startReady = false;
      videoEl.preload = 'auto';
      ensureVideoSrc(videoEl);
      videoEl._startPromise = (async () => {
        await waitForVideoState(
          videoEl,
          ['loadeddata', 'canplay'],
          () => videoEl.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA,
        );
        videoEl._startReady = true;
        videoEl._startPromise = null;
        videoEl._activityLastPosition = videoEl.currentTime;
        videoEl.preload = 'metadata';
        return videoEl;
      })().catch((error) => {
        videoEl._startPromise = null;
        videoEl.preload = 'metadata';
        throw error;
      });
      return videoEl._startPromise;
    }

    function preloadNextVideo() {
      for (let i = getCurrentIndex() + 1; i < feedItems.length; i++) {
        const item = feedItems[i];
        if (item.type !== 'video') continue;
        const v = item.el;
        if (v) prepareVideoStart(v).catch(() => {});
        break;
      }
    }

    function waitForPresentedVideoFrame(videoEl, timeoutMs = 350) {
      if (typeof videoEl.requestVideoFrameCallback !== 'function') return Promise.resolve();
      return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          clearTimeout(timeoutId);
          resolve();
        };
        const timeoutId = setTimeout(finish, timeoutMs);
        videoEl.requestVideoFrameCallback(finish);
      });
    }

    function updateVideoProgress(videoEl) {
      if (!videoEl || currentItem()?.el !== videoEl || isDragging) return;
      if (timeTotal.textContent === '00:00' || timeTotal.textContent === '') {
        const duration = videoEl.duration;
        if (duration && !isNaN(duration) && duration !== Infinity) {
          timeTotal.textContent = formatTime(duration);
        }
      }
      const pct = (videoEl.currentTime / videoEl.duration) * 100;
      progressBar.value = isNaN(pct) ? 0 : pct;
      progressFill.style.width = `${isNaN(pct) ? 0 : pct}%`;
      timeCurrent.textContent = formatTime(videoEl.currentTime);
    }

    function currentDisplayEntry() {
      const item = currentItem();
      if (!item) return null;
      if (item.type === 'image_group') {
        const entries = Array.isArray(item.items) ? item.items : [];
        const idx = Math.max(0, Math.min(Number(item.activeChildIndex) || 0, entries.length - 1));
        return entries[idx] || null;
      }
      return item;
    }

    function clearPreviousState(prevItem) {
      magnifier.toggle(false);
      endBoost();
      stopLivePlayback();

      if (!prevItem) return;
      prevItem.el.classList.remove('active');
      prevItem.el.style.display = 'none';
      if (prevItem.type === 'video') {
        prevItem.el.pause();
        prevItem.el.muted = true;
      } else {
        // 相册（image_group）里的子视频建 DOM 时就自动播了，切走必须一起暂停，
        // 否则后台会有多路视频持续解码/循环播放。
        prevItem.el.querySelectorAll('video').forEach((video) => {
          video.pause();
          video.muted = true;
        });
      }
    }

    async function showItem(nextIndex) {
      if (!feedItems.length) return;
      const safeIndex = Math.max(0, Math.min(nextIndex, feedItems.length - 1));
      const prevItem = currentItem();
      if (safeIndex !== getCurrentIndex()) lastStep = safeIndex < getCurrentIndex() ? -1 : 1;
      clearTimeout(skipTimer);
      skipTimer = null;
      const previousActivity = finishActivity();
      clearPreviousState(prevItem);

      flowSession.setIndex(safeIndex);
      const item = currentItem();
      if (!item) return;
      const activationId = ++videoActivationId;
      postActivity([previousActivity, beginActivity(item)]);
      flowState.onMediaChanged();
      // Holding a video speeds it up, so videos have no way to bring the interface back; show it.
      if (item.type === 'video') flowState.setImmersive(false);

      const needsVideoStartCover = item.type === 'video' && !isVideoStartReady(item.el);
      if (needsVideoStartCover) showVideoStartCover(item);
      else hideVideoStartCover();

      item.el.style.display = 'block';
      requestAnimationFrame(() => item.el.classList.add('active'));
      updateControls(item);
      preloadNextVideo();

      if (item.type === 'video') {
        progressBar.disabled = false;
        if (item.el._loadFailed) handleMediaFailure(item.el);
        item.el.playbackRate = speedRate;
        progressBar.value = 0;
        progressFill.style.width = '0%';
        timeCurrent.textContent = '00:00';
        timeTotal.textContent = formatTime(item.el.duration);
        mediaActions.clearCaption();

        try {
          await prepareVideoStart(item.el);
        } catch (error) {
          if (activationId !== videoActivationId || currentItem()?.el !== item.el) return;
          hideVideoStartCover();
          if (item.el._loadFailed) handleMediaFailure(item.el);
          item.el.muted = false;
          const fallbackPlayPromise = item.el.play();
          if (fallbackPlayPromise !== undefined) {
            fallbackPlayPromise
              .then(() => playStatusIcon.classList.remove('visible'))
              .catch(() => playStatusIcon.classList.add('visible'));
          } else {
            playStatusIcon.classList.remove('visible');
          }
          preloadNextVideo();
          return;
        }

        if (activationId !== videoActivationId || currentItem()?.el !== item.el) return;
        item.el.muted = false;
        try {
          const playPromise = item.el.play();
          if (playPromise !== undefined) await playPromise;
          await waitForPresentedVideoFrame(item.el);
          if (activationId !== videoActivationId || currentItem()?.el !== item.el) return;
          playStatusIcon.classList.remove('visible');
        } catch (error) {
          if (activationId !== videoActivationId || currentItem()?.el !== item.el) return;
          playStatusIcon.classList.add('visible');
        }
        if (needsVideoStartCover) hideVideoStartCover();
      } else if (item.type === 'image_group') {
        mediaActions.clearCaption();
        if (typeof item.renderChild === 'function') {
          item.activeChildIndex = item.renderChild(Number(item.activeChildIndex) || 0);
        }
        // Entering the group: prune leftovers from interrupted renders or drags.
        const groupStage = item.el ? item.el.querySelector('.image-group-stage') : null;
        if (groupStage && item.el._groupGeneration) {
          const currentGen = String(item.el._groupGeneration);
          groupStage.querySelectorAll('.image-group-media').forEach((element) => {
            if (element.dataset.groupGeneration !== currentGen && element.parentNode === groupStage) {
              element.remove();
            }
          });
        }
        // A live child that is already on stage (the slide-in was skipped) still
        // needs to start playing when its group becomes the current item.
        if (item.el?._activeLiveVideo) playLiveVideo(item.el._activeLiveVideo);
        playStatusIcon.classList.add('hidden');
        playStatusIcon.classList.remove('visible');
      } else if (item.type === 'live') {
        playStatusIcon.classList.remove('visible');
        mediaActions.loadCaption(item.name);
        if (item.el._loadFailed) handleMediaFailure(item.el);
        // A live photo plays as soon as it slides in, like a video.
        startLivePlayback();
      } else {
        playStatusIcon.classList.remove('visible');
        mediaActions.loadCaption(item.name);
        if (item.el._loadFailed) handleMediaFailure(item.el);
      }

      if (getCurrentIndex() >= feedItems.length - 4 && flowSession.hasMore()) {
        loadFeed();
      }
    }

    function buildMediaElement(item) {
      if (item.type === 'image_group') {
        const panel = document.createElement('section');
        panel.className = 'feed-media feed-image-group';
        panel.dataset.name = item.name;
        const children = Array.isArray(item.items) ? item.items : [];
        // A live post is a group whose members may pair with a video; the pill
        // keeps marking it outside Douyin mode, as the per-child badge used to.
        const badgeKind = children.some((child) => child && child.type === 'live') ? 'live' : 'album';
        // One pill for the kind and the position: Douyin's nav bar covers the
        // bottom overlay, so the counter rides along in the badge there.
        panel.innerHTML = `
          <div class="image-group-stage"></div>
          <div class="image-group-overlay">
            ${item.caption ? `<span>${escapeHtml(item.caption)}</span>` : ''}
            <span class="image-group-counter"></span>
          </div>
          <div class="image-group-badge" data-kind="${badgeKind}">
            <span class="image-group-badge-label">${escapeHtml(item.title || T('flow.group.album_title'))}</span>
            <span class="image-group-badge-counter"></span>
          </div>
        `;
        const stage = panel.querySelector('.image-group-stage');
        const counterEl = panel.querySelector('.image-group-counter');
        const badgeEl = panel.querySelector('.image-group-badge');
        const badgeCounterEl = panel.querySelector('.image-group-badge-counter');

        // One source of truth for "current/total": a drag commit, a re-render and
        // a deep link into child N must never leave the badge and the overlay
        // showing different positions.
        function syncGroupCounter(index, total) {
          if (counterEl) counterEl.textContent = `${index + 1} / ${total}`;
          if (badgeCounterEl) badgeCounterEl.textContent = `${index + 1}/${total}`;
          if (badgeEl) badgeEl.hidden = total < 2;
        }

        function createGroupChild(entry, force = false) {
          const media = document.createElement(entry.type === 'live' && entry.live_video_url ? 'div' : (entry.type === 'video' ? 'video' : 'img'));
          if (media instanceof HTMLDivElement) {
            // A live child: jpeg cover with the paired mp4 on hold.
            media.className = 'image-group-media image-group-live';
            const cover = document.createElement('img');
            cover.src = force
              ? `${entry.media_url}${entry.media_url.includes('?') ? '&' : '?'}retry=${Date.now()}`
              : entry.media_url;
            cover.alt = entry.name || '';
            const video = document.createElement('video');
            video.className = 'image-group-live-video';
            video.src = entry.live_video_url;
            video.preload = 'none';
            video.muted = false;
            video.loop = true;
            video.playsInline = true;
            media.appendChild(cover);
            media.appendChild(video);
            media._liveVideo = video;
            media._loadImage = cover;
          } else if (media instanceof HTMLVideoElement) {
            media.className = 'image-group-media';
            media.src = entry.media_url;
            media.muted = true;
            media.loop = true;
            media.playsInline = true;
            media.preload = 'metadata';
            if (entry.thumb_url) media.poster = entry.thumb_url;
            // 不在建 DOM 时自动播放：整页元素在加载时就全建好了，那样会让每个相册的
            // 子视频都在后台解码循环。真正的播放交给 renderImageGroup（可见时才播）。
          } else {
            media.className = 'image-group-media';
            media.src = force
              ? `${entry.media_url}${entry.media_url.includes('?') ? '&' : '?'}retry=${Date.now()}`
              : entry.media_url;
            media.alt = entry.name || '';
          }
          media.addEventListener('error', () => handleMediaFailure(panel));
          media.loading = 'eager';
          media.decoding = 'async';
          return media;
        }

        function renderImageGroup(index, force = false) {
          if (!stage || !counterEl || !children.length) return;
          const safeIndex = Math.max(0, Math.min(index, children.length - 1));
          const entry = children[safeIndex];
          if (!entry || !entry.media_url) return;
          if (groupDrag && groupDrag.panel === panel) {
            // A render mid-drag would fight the finger; drop the drag instantly.
            const drag = groupDrag;
            groupDrag = null;
            drag.neighbor.remove();
            drag.active.style.transition = 'none';
            drag.active.style.transform = 'translateX(0)';
          }
          const previousIndex = Number(panel._activeChildIndex);
          if (!force && stage.querySelector('.image-group-media') && safeIndex === previousIndex) {
            // Re-showing the child that is already on stage (e.g. when the page
            // slid in); re-rendering it would replay the slide animation.
            // 但它可能是被 clearPreviousState 暂停过的子视频，回到这一页要从头续播。
            if (currentItem()?.el === panel) {
              const staged = stage.querySelector('.image-group-media');
              if (staged instanceof HTMLVideoElement) {
                staged.currentTime = 0;
                staged.play().catch(() => {});
              }
            }
            return;
          }
          const direction = Number.isFinite(previousIndex) && safeIndex < previousIndex ? -1 : 1;
          const outgoing = Array.from(stage.querySelectorAll('.image-group-media'));
          // Stop the outgoing child's playback before swapping it out.
          outgoing.forEach((element) => {
            if (element._liveVideo) element._liveVideo.pause();
            if (element instanceof HTMLVideoElement) element.pause();
          });
          panel._activeLiveVideo = null;
          const media = createGroupChild(entry, force);
          if (media._liveVideo) panel._activeLiveVideo = media._liveVideo;
          panel._groupGeneration = (Number(panel._groupGeneration) || 0) + 1;
          const generation = panel._groupGeneration;
          media.dataset.groupGeneration = String(generation);
          // The incoming child waits offstage until its bytes are ready, then
          // slides across while the outgoing child slides out the other way -
          // a Douyin-style pan that never shows a half-loaded image.
          const slideMs = reduceMotion.matches ? 0 : 260;
          const slideEase = 'cubic-bezier(0.22, 1, 0.36, 1)';
          media.style.opacity = '0';
          media.style.transform = outgoing.length
            ? `translateX(${direction * 100}%)`
            : 'translateX(0)';
          stage.appendChild(media);
          // Live children start playing when they become the visible child of
          // the current item; off-screen groups keep their cover only.
          if (media._liveVideo && currentItem()?.el === panel) playLiveVideo(media._liveVideo);
          if (media instanceof HTMLVideoElement && currentItem()?.el === panel) {
            media.currentTime = 0;
            media.play().catch(() => {});
          }
          const revealMedia = () => {
            if (media.dataset.groupGeneration !== String(panel._groupGeneration)) return;
            if (outgoing.length && slideMs) {
              // Pan: both children travel one stage width, clipped by overflow.
              media.style.opacity = '1';
              // Double rAF: the offstage start position must be committed
              // before the transition begins, or the pan snaps instantly.
              requestAnimationFrame(() => requestAnimationFrame(() => {
                media.style.transition = `transform ${slideMs}ms ${slideEase}`;
                media.style.transform = 'translateX(0)';
                outgoing.forEach((element) => {
                  element.style.transition = `transform ${slideMs}ms ${slideEase}`;
                  element.style.transform = `translateX(${direction * -100}%)`;
                });
              }));
              window.setTimeout(() => {
                if (media.dataset.groupGeneration !== String(panel._groupGeneration)) return;
                outgoing.forEach((element) => {
                  if (element.parentNode === stage) element.remove();
                });
              }, slideMs + 80);
              return;
            }
            // Nothing to pan against (first child, or reduced motion): swap in place.
            outgoing.forEach((element) => {
              if (element.parentNode === stage) element.remove();
            });
            requestAnimationFrame(() => requestAnimationFrame(() => {
              media.style.transition = slideMs
                ? `opacity 0.16s ease, transform 0.2s ${slideEase}`
                : 'none';
              media.style.opacity = '1';
              media.style.transform = 'translateX(0)';
            }));
          };
          const loadImage = media instanceof HTMLImageElement ? media : media._loadImage || null;
          if (loadImage && !(loadImage.complete && loadImage.naturalWidth > 0)) {
            let settled = false;
            const onReady = () => {
              if (settled) return;
              settled = true;
              revealMedia();
            };
            loadImage.addEventListener('load', onReady, { once: true });
            loadImage.addEventListener('error', onReady, { once: true });
            window.setTimeout(onReady, 350);
          } else {
            revealMedia();
          }
          // Safety net: stale children from interrupted renders never linger.
          window.setTimeout(() => {
            stage.querySelectorAll('.image-group-media').forEach((element) => {
              if (element.dataset.groupGeneration === String(panel._groupGeneration)) return;
              if (element.parentNode === stage) element.remove();
            });
          }, 500);
          panel._activeChildIndex = safeIndex;
          syncGroupCounter(safeIndex, children.length);
          if (currentItem()?.el === panel) {
            currentItem().activeChildIndex = safeIndex;
            updateControls(currentItem());
          }
        }

        panel._renderImageGroup = renderImageGroup;
        panel._createGroupChild = createGroupChild;
        panel._syncGroupCounter = syncGroupCounter;
        panel._activeChildIndex = 0;
        renderImageGroup(0);
        return panel;
      }

      if (item.type === 'live' && item.live_video_url) {
        // A live photo shows its jpeg cover; press and hold plays the paired mp4.
        const wrap = document.createElement('div');
        wrap.className = 'feed-media feed-live';
        const cover = document.createElement('img');
        cover.className = 'feed-live-cover';
        cover.loading = 'lazy';
        cover.decoding = 'async';
        cover.alt = item.name;
        cover.src = item.media_url;
        cover.addEventListener('error', () => {
          cover._loadFailed = true;
          handleMediaFailure(wrap);
        });
        cover.addEventListener('load', () => {
          cover._loadFailed = false;
          quietSkips = 0;
        });
        const video = document.createElement('video');
        video.className = 'feed-live-video';
        video.muted = false;
        video.loop = true;
        video.playsInline = true;
        video.controls = false;
        video.preload = 'none';
        video.dataset.src = item.live_video_url;
        video.dataset.name = item.name;
        const badge = document.createElement('div');
        badge.className = 'feed-live-badge';
        badge.textContent = T('flow.badge.live');
        const hint = document.createElement('div');
        hint.className = 'feed-live-hint';
        hint.textContent = T('flow.hint.long_press_sound');
        wrap.appendChild(cover);
        wrap.appendChild(video);
        wrap.appendChild(badge);
        wrap.appendChild(hint);
        wrap._liveCover = cover;
        wrap._liveVideo = video;
        return wrap;
      }

      if (item.type === 'video') {
        const video = document.createElement('video');
        video.className = 'feed-media feed-video';
        video.muted = true;
        video.loop = true;
        video.playsInline = true;
        video.controls = false;
        video.preload = 'metadata';
        if (item.thumb_url) video.poster = item.thumb_url;
        video.dataset.src = item.media_url;
        video.dataset.name = item.name;
        video.addEventListener('error', () => {
          video._loadFailed = true;
          handleMediaFailure(video);
        });
        video.addEventListener('loadeddata', () => {
          video._loadFailed = false;
        });
        video.addEventListener('playing', () => {
          quietSkips = 0;
        });
        video.addEventListener('timeupdate', () => {
          updateVideoProgress(video);
          const duration = Number(video.duration || 0);
          const current = Number(video.currentTime || 0);
          const previous = video._activityLastPosition;
          const playedDelta = current - previous;
          if (Number.isFinite(previous) && playedDelta > 0 && playedDelta <= 2.5) {
            video._activityPlayedSeconds = Number(video._activityPlayedSeconds || 0) + playedDelta;
          }
          video._activityLastPosition = current;
          if (duration > 0 && previous > duration * 0.85 && current < 1.5) {
            video._completedInView = true;
            postActivity([{
              session_id: activitySessionId,
              uri: item.name,
              media_type: 'video',
              surface: 'flow',
              event: 'replay',
            }]);
          }
          video._lastActivityTime = current;
        });
        const updateDuration = () => {
          if (currentItem()?.el !== video) return;
          const duration = video.duration;
          if (duration && !isNaN(duration) && duration !== Infinity) {
            timeTotal.textContent = formatTime(duration);
          }
        };
        video.addEventListener('loadedmetadata', updateDuration);
        video.addEventListener('durationchange', updateDuration);
        video.addEventListener('play', () => magnifier.onVideoPlay(video));
        video.addEventListener('seeked', () => magnifier.onVideoSeeked(video));
        return video;
      }

      const img = document.createElement('img');
      img.className = 'feed-media feed-image';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.alt = item.name;
      img.addEventListener('error', () => {
        img._loadFailed = true;
        handleMediaFailure(img);
      });
      img.addEventListener('load', () => {
        img._loadFailed = false;
        quietSkips = 0;
      });
      img.src = item.media_url;
      img.dataset.name = item.name;
      return img;
    }

    async function loadFeed() {
      if (flowSession.isLoading() || !flowSession.hasMore()) return;
      const isInitialLoad = feedItems.length === 0;
      if (isInitialLoad) scheduleFlowLoadingStatus();
      try {
        const result = await flowSession.loadMore(async (cursor) => {
          const page = Number(cursor?.page || 1);
          const query = new URLSearchParams({
            page: String(page),
            size: '24',
          });
          if (seed) query.set('seed', seed);
          if (feedSince) query.set('since', feedSince);
          if (feedMode) query.set('mode', feedMode);
          const blogFilter = new URLSearchParams(window.location.search).get('blog');
          if (blogFilter) query.set('blog', blogFilter);
          const tagFilter = currentTopicTag();
          if (tagFilter) query.set('tag', tagFilter);
          const startUri = flowStartUri();
          if (startUri) query.set('start', startUri);

          // A hung request (sleeping NAS, stalled server) must fail loudly: the
          // feed has no other way out of isLoading().
          const res = await fetch(`/api/feed/mix?${query.toString()}`, {
            signal: AbortSignal.timeout(20000),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();

          if (!seed && data.seed) seed = String(data.seed);
          if (!feedSince && data.since) feedSince = String(data.since);
          const incoming = Array.isArray(data.items) ? data.items : [];
          const normalized = incoming.map((item) => {
            if (!item || !item.type || !item.name) return null;
            const mediaEl = buildMediaElement(item);
            if (!mediaEl) return null;
            return {
              type: item.type,
              name: item.name,
              media_url: item.media_url || '',
              detail_url: item.detail_url || '#',
              thumb_url: item.thumb_url || '',
              author_name: item.author_name || '',
              author_url: item.author_url || '',
              caption_text: item.caption_text || '',
              items: Array.isArray(item.items) ? item.items : [],
              renderChild: typeof mediaEl._renderImageGroup === 'function' ? (index, force = false) => {
                mediaEl._renderImageGroup(index, force);
                return Number(mediaEl._activeChildIndex) || 0;
              } : null,
              activeChildIndex: Number(mediaEl._activeChildIndex) || 0,
              el: mediaEl,
            };
          }).filter(Boolean);

          return {
            items: normalized,
            hasMore: data.has_more !== false,
            cursor: { page: page + 1 },
          };
        });

        (result.appendedIndices || []).forEach((itemIndex) => {
          const entry = feedItems[itemIndex];
          if (entry?.el) feedContainer.appendChild(entry.el);
        });

        if (feedItems.length > 100) {
          const removeCount = 30;
          const removed = flowSession.dropHead(removeCount);
          removed.forEach((entry) => entry.el.remove());
        }
        if (feedItems.length) {
          hideFlowStatus();
          syncTopicCount(feedItems.length);
        } else if (!flowSession.hasMore()) {
          if (currentTopicTag()) showFlowStatus('empty', T('flow.tag.empty'), '');
          else if (feedMode) showFlowStatus('empty', T('flow.state.forgotten_empty_title'), T('flow.state.forgotten_empty_detail'));
          else showFlowStatus('empty', T('flow.state.empty_title'), T('flow.state.empty_detail'));
        }
        return result;
      } catch (error) {
        clearTimeout(flowLoadingTimer);
        flowLoadingTimer = null;
        const reachedEnd = getCurrentIndex() >= feedItems.length - 1;
        if (isInitialLoad || feedItems.length === 0) {
          // 一条都没有时才用全屏错误页（它是 fixed 覆盖层，会吃掉所有手势）。
          showFlowStatus('error', T('flow.state.error_title'), T('flow.state.error_detail'));
        } else if (reachedEnd) {
          // 已经加载了几十条，页尾这一页失败不该把用户锁死在错误页里；
          // 保留手势可用，用户再上滑一次就会重新触发 loadFeed（即重试）。
          TikLocalUI.toast(T('flow.state.error_title'), 'error');
        }
        return null;
      }
    }

    function handleMediaFailure(mediaEl) {
      const item = currentItem();
      if (item?.el !== mediaEl || skipTimer) return;
      const code = mediaEl.error?.code;
      if (code === 1) return;
      if (code === 2 && !mediaEl._networkRetried) {
        mediaEl._networkRetried = true;
        retryCurrentMedia();
        return;
      }
      if (quietSkips >= maxQuietSkips) {
        showFlowStatus('media-error', T('flow.state.media_error_title'), T('flow.state.media_error_detail'));
        return;
      }
      if (item.type === 'image_group') {
        showHud(T('flow.hud.cant_show_image'), 1600);
        return;
      }
      quietSkips += 1;
      showHud(T('flow.hud.cant_play', { kind: item.type === 'video' ? T('flow.kind.video') : T('flow.kind.image') }), 1600);
      skipTimer = setTimeout(() => {
        skipTimer = null;
        if (currentItem() !== item) return;
        if (lastStep < 0 && getCurrentIndex() > 0) goPrev();
        else goNext();
      }, 700);
    }

    async function retryCurrentMedia() {
      const item = currentItem();
      if (!item) return;
      hideFlowStatus();
      const retryUrl = `${item.media_url}${item.media_url.includes('?') ? '&' : '?'}retry=${Date.now()}`;
      if (item.type === 'video') {
        item.el.pause();
        item.el._loadFailed = false;
        item.el._startReady = false;
        item.el._startPromise = null;
        item.el.src = retryUrl;
        item.el.load();
        await showItem(getCurrentIndex());
      } else if (item.type === 'image') {
        item.el._loadFailed = false;
        item.el.src = retryUrl;
      } else if (item.type === 'image_group' && typeof item.renderChild === 'function') {
        item.activeChildIndex = item.renderChild(Number(item.activeChildIndex) || 0, true);
      }
    }

    async function retryFlowLoad() {
      flowSession.setHasMore(true);
      showFlowStatus('loading', T('flow.state.reloading'));
      const result = await loadFeed();
      if (result && feedItems.length && getCurrentIndex() < 0) {
        await showItem(0);
      }
    }

    function togglePlay() {
      const item = currentItem();
      if (!item || item.type !== 'video') return;
      const video = item.el;
      if (video.paused) {
        video.play();
        playStatusIcon.classList.remove('visible');
      } else {
        video.pause();
        playStatusIcon.classList.add('visible');
      }
    }

    function currentVideo() {
      const item = currentItem();
      return item?.type === 'video' ? item.el : null;
    }

    function showHud(text, holdMs = 0) {
      clearTimeout(hudTimer);
      hud.textContent = text;
      hud.classList.add('is-visible');
      if (holdMs) hudTimer = setTimeout(hideHud, holdMs);
    }

    function hideHud() {
      clearTimeout(hudTimer);
      hud.classList.remove('is-visible');
    }

    function seekLabel(from, to, duration) {
      const delta = Math.round(to - from);
      return `${delta < 0 ? '−' : '+'}${Math.abs(delta)}s · ${formatTime(to)} / ${formatTime(duration)}`;
    }

    function showSeekProgress(time, duration) {
      const pct = duration > 0 ? (time / duration) * 100 : 0;
      progressBar.value = pct;
      progressFill.style.width = `${pct}%`;
      timeCurrent.textContent = formatTime(time);
    }

    // Hold a video to play it faster; lifting the finger restores the chosen speed and keeps playing.
    function beginBoost(video) {
      boostedVideo = video;
      video.playbackRate = holdRate;
      if (video.paused) {
        video.play().catch(() => {});
        playStatusIcon.classList.remove('visible');
      }
      showHud(`${holdRate}× ▸▸`);
      navigator.vibrate?.(10);
    }

    function endBoost() {
      if (!boostedVideo) return;
      boostedVideo.playbackRate = speedRate;
      boostedVideo = null;
      hideHud();
    }

    // Live photos: the paired mp4 plays while the item is on stage and stops
    // when it leaves; a long press (or the space bar) toggles its sound.
    let playingLiveVideo = null;

    function playLiveVideo(video) {
      if (!video) return false;
      if (!video.src && video.dataset.src) video.src = video.dataset.src;
      playingLiveVideo = video;
      video.classList.add('is-playing');
      video.currentTime = 0;
      const promise = video.play();
      if (promise) {
        promise.catch(() => {
          // Autoplay with sound can be refused; fall back to a muted playback.
          video.muted = true;
          video.play().then(() => {
            showHud(T('flow.hud.autoplay_muted'), 1600);
          }).catch(() => stopLivePlayback());
        });
      }
      navigator.vibrate?.(10);
      return true;
    }

    function activeLiveVideo() {
      const item = currentItem();
      if (!item) return null;
      if (item.type === 'live') return item.el?._liveVideo || null;
      if (item.type === 'image_group') return item.el?._activeLiveVideo || null;
      return null;
    }

    function toggleLiveSound() {
      const item = currentItem();
      const video = activeLiveVideo();
      if (!video) return false;
      video.muted = !video.muted;
      if (video.paused) {
        if (item?.type === 'live' && item.el) item.el.classList.add('live-playing');
        playLiveVideo(video);
      }
      showHud(video.muted ? T('flow.hud.live_muted') : T('flow.hud.live_sound_on'), 900);
      navigator.vibrate?.(10);
      return true;
    }

    function startLivePlayback() {
      const item = currentItem();
      if (!item) return false;
      if (item.type === 'live' && item.el?._liveVideo) {
        item.el.classList.add('live-playing');
        return playLiveVideo(item.el._liveVideo);
      }
      if (item.type === 'image_group' && item.el?._activeLiveVideo) {
        return playLiveVideo(item.el._activeLiveVideo);
      }
      return false;
    }

    function stopLivePlayback() {
      document.querySelectorAll('.feed-live.live-playing').forEach((element) => {
        element.classList.remove('live-playing');
      });
      if (playingLiveVideo) {
        playingLiveVideo.pause();
        playingLiveVideo.currentTime = 0;
        playingLiveVideo.classList.remove('is-playing');
        playingLiveVideo = null;
      }
    }

    function seekBy(seconds) {
      const video = currentVideo();
      const duration = Number(video?.duration);
      if (!Number.isFinite(duration) || duration <= 0) return;
      const from = video.currentTime;
      const to = Math.max(0, Math.min(duration, from + seconds));
      video.currentTime = to;
      showSeekProgress(to, duration);
      showHud(seekLabel(from, to, duration), 900);
    }

    // Dragging sideways on a video moves through it relative to where the drag began.
    function beginScrub(state) {
      const video = currentVideo();
      state.mode = 'scrub';
      state.video = video;
      state.from = video.currentTime;
      state.to = video.currentTime;
      isDragging = true;
      customControls.classList.add('is-seeking');
    }

    function moveScrub(state, dx) {
      const duration = Number(state.video.duration);
      if (!Number.isFinite(duration) || duration <= 0) return;
      const span = Math.min(duration, scrubSpanSeconds);
      state.to = Math.max(0, Math.min(duration, state.from + (dx / feedContainer.clientWidth) * span));
      showSeekProgress(state.to, duration);
      showHud(seekLabel(state.from, state.to, duration));
      // Safari and Firefox can show the nearby frame while dragging; elsewhere the seek lands on release.
      if (typeof state.video.fastSeek === 'function' && !state.frame) {
        state.frame = requestAnimationFrame(() => {
          state.frame = null;
          state.video.fastSeek(state.to);
        });
      }
    }

    function endScrub(state, cancelled) {
      cancelAnimationFrame(state.frame);
      isDragging = false;
      customControls.classList.remove('is-seeking');
      if (currentVideo() === state.video) state.video.currentTime = cancelled ? state.from : state.to;
      hideHud();
    }

    // One pointer gesture for the stage: drag to page, tap to play or reveal, double-tap to favorite,
    // hold a video to speed it up, drag a video sideways to seek.
    const tapSlop = 10;
    const doubleTapMs = 250;
    const longPressMs = 450;
    let gesture = null;
    let lastTap = { time: 0, x: 0, y: 0 };
    let tapTimer = null;

    function likeAt(x, y) {
      if (favoriteBtn.hidden || !favoriteBtn.dataset.value) return;
      const heart = document.createElement('div');
      heart.className = 'tap-heart';
      heart.style.left = `${x}px`;
      heart.style.top = `${y}px`;
      heart.style.setProperty('--tilt', `${Math.round(Math.random() * 28 - 14)}deg`);
      heart.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
      heart.addEventListener('animationend', () => heart.remove());
      document.body.appendChild(heart);
      if (!favoriteBtn.classList.contains('is-active')) favoriteBtn.click();
    }

    function handleTap(x, y) {
      const now = performance.now();
      const isDouble = now - lastTap.time < doubleTapMs && Math.hypot(x - lastTap.x, y - lastTap.y) < 40;
      lastTap = isDouble ? { time: 0, x: 0, y: 0 } : { time: now, x, y };
      clearTimeout(tapTimer);
      if (isDouble) {
        likeAt(x, y);
        return;
      }
      // A single tap waits out the double-tap window, so a double tap never touches playback.
      const item = currentItem();
      tapTimer = setTimeout(() => {
        if (currentItem() !== item) return;
        if (item?.type === 'video') togglePlay();
        else toggleUI();
      }, doubleTapMs);
    }

    function releaseVelocity(samples) {
      const last = samples[samples.length - 1];
      const first = samples.find(([time]) => last[0] - time <= 90) || last;
      return last[0] > first[0] ? (last[1] - first[1]) / (last[0] - first[0]) : 0;
    }

    // 翻页动画期间（settling）手指再来，手势整体被丢弃会让用户觉得「滑了没反应」。
    // 这里只探测一次方向，等动画结束再补一次翻页，既不打断动画也不丢操作。
    function queueSwipeWhileSettling(event) {
      const startY = event.clientY;
      const startX = event.clientX;
      const onMove = (moveEvent) => {
        if (!moveEvent.isPrimary) return;
        const dy = moveEvent.clientY - startY;
        const dx = moveEvent.clientX - startX;
        if (Math.abs(dy) < 24 || Math.abs(dy) < Math.abs(dx)) return;
        queuedSwipe = dy < 0 ? 1 : -1;
        cleanup();
      };
      const cleanup = () => {
        document.removeEventListener('pointermove', onMove, true);
        document.removeEventListener('pointerup', cleanup, true);
        document.removeEventListener('pointercancel', cleanup, true);
      };
      document.addEventListener('pointermove', onMove, true);
      document.addEventListener('pointerup', cleanup, true);
      document.addEventListener('pointercancel', cleanup, true);
    }

    overlayLayer.addEventListener('pointerdown', (event) => {
      if (!event.isPrimary || event.button !== 0 || magnifier.isActive()) return;
      if (settling) {
        queueSwipeWhileSettling(event);
        return;
      }
      overlayLayer.setPointerCapture(event.pointerId);
      gesture = {
        id: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        mode: 'pending',
        offset: 0,
        dx: 0,
        samples: [[event.timeStamp, event.clientY]],
        longPressed: false,
      };
      gesture.timer = setTimeout(() => {
        if (gesture?.mode !== 'pending') return;
        gesture.longPressed = true;
        clearTimeout(tapTimer);
        const video = currentVideo();
        if (video) beginBoost(video);
        else if (!toggleLiveSound()) toggleUI();
      }, longPressMs);
    });

    overlayLayer.addEventListener('pointermove', (event) => {
      if (!gesture || event.pointerId !== gesture.id) return;
      const dx = event.clientX - gesture.x;
      const dy = event.clientY - gesture.y;
      if (gesture.mode === 'pending') {
        if (Math.hypot(dx, dy) < tapSlop) return;
        clearTimeout(gesture.timer);
        clearTimeout(tapTimer);
        gesture.mode = gesture.longPressed ? 'none' : (Math.abs(dy) > Math.abs(dx) ? 'vertical' : 'horizontal');
        if (gesture.mode === 'horizontal' && currentVideo()) beginScrub(gesture);
        else if (gesture.mode === 'horizontal') startGroupDrag(dx);
      }
      if (gesture.mode === 'scrub') {
        moveScrub(gesture, dx);
        return;
      }
      if (gesture.mode === 'horizontal') {
        gesture.dx = dx;
        gesture.xsamples = gesture.xsamples || [[event.timeStamp, event.clientX]];
        gesture.xsamples.push([event.timeStamp, event.clientX]);
        if (gesture.xsamples.length > 6) gesture.xsamples.shift();
        // The drag latches on the first move that points at an existing
        // neighbour, so an initial wobble towards a boundary cannot lock the
        // gesture out of finger tracking.
        if (!groupDrag && !currentVideo()) startGroupDrag(dx);
        applyGroupDrag(dx);
      }
      if (gesture.mode !== 'vertical') return;
      const dir = dy < 0 ? 1 : -1;
      if (!pager || pager.dir !== dir) beginPage(dir);
      gesture.offset = pager.peer ? dy : dy * 0.25;
      gesture.samples.push([event.timeStamp, event.clientY]);
      if (gesture.samples.length > 6) gesture.samples.shift();
      placePage(gesture.offset);
    });

    function endGesture(event, cancelled) {
      if (!gesture || event.pointerId !== gesture.id) return;
      const current = gesture;
      gesture = null;
      clearTimeout(current.timer);
      endBoost();
      if (current.mode === 'scrub') {
        endScrub(current, cancelled);
        return;
      }
      if (current.mode === 'vertical' && pager) {
        const height = feedContainer.clientHeight;
        const toward = -current.offset * pager.dir;
        const fling = -releaseVelocity(current.samples) * pager.dir > 0.45;
        const commit = !cancelled && (toward > height * 0.22 || (fling && toward > 24));
        if (commit && !pager.peer && pager.dir > 0 && flowSession.hasMore()) loadFeed();
        settlePage(commit, current.offset);
        return;
      }
      if (current.mode === 'horizontal' && groupDrag) {
        // The strip already follows the finger; settle it by distance or flick.
        const xs = current.xsamples || [];
        const lastX = xs[xs.length - 1] || null;
        const firstX = (lastX && xs.find(([time]) => lastX[0] - time <= 90)) || lastX;
        const vx = lastX && firstX && lastX[0] > firstX[0]
          ? (lastX[1] - firstX[1]) / (lastX[0] - firstX[0])
          : 0;
        const commit = !cancelled && (
          Math.abs(current.dx) > groupDrag.width * 0.22
          || (Math.abs(vx) > 0.55 && Math.abs(current.dx) > 36)
        );
        if (commit) commitGroupDrag();
        else finishGroupDragAnimated(false);
        return;
      }
      if (cancelled) return;
      if (current.mode === 'horizontal' && Math.abs(current.dx) > 36) {
        if (current.dx < 0) goGroupNext();
        else goGroupPrev();
        return;
      }
      if (current.mode === 'pending' && !current.longPressed) handleTap(event.clientX, event.clientY);
    }

    overlayLayer.addEventListener('pointerup', (event) => endGesture(event, false));
    overlayLayer.addEventListener('pointercancel', (event) => endGesture(event, true));

    // Wheel and trackpad page once per gesture; momentum is absorbed until the wheel goes quiet.
    let wheelLocked = false;
    let wheelQuietTimer = null;
    overlayLayer.addEventListener('wheel', (event) => {
      event.preventDefault();
      if (magnifier.isActive()) return;
      clearTimeout(wheelQuietTimer);
      wheelQuietTimer = setTimeout(() => { wheelLocked = false; }, 200);
      if (wheelLocked || Math.abs(event.deltaY) < 12) return;
      wheelLocked = true;
      if (event.deltaY > 0) goNext();
      else goPrev();
    }, { passive: false });

    function closeSpeedChoices() {
      speedChoices.classList.remove('show');
      speedBtn.setAttribute('aria-expanded', 'false');
    }

    function setSpeed(rate) {
      speedRate = rate;
      speedBtn.textContent = `${rate}x`;
      speedBtn.classList.toggle('is-changed', rate !== 1);
      speedChoices.querySelectorAll('[data-rate]').forEach((btn) => {
        btn.classList.toggle('active', Number(btn.dataset.rate) === rate);
      });
      const video = currentVideo();
      if (video && video !== boostedVideo) video.playbackRate = rate;
    }

    speedBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      const open = !speedChoices.classList.contains('show');
      speedChoices.classList.toggle('show', open);
      speedBtn.setAttribute('aria-expanded', String(open));
    });
    speedChoices.addEventListener('click', (event) => {
      const choice = event.target.closest('[data-rate]');
      if (!choice) return;
      event.stopPropagation();
      setSpeed(Number(choice.dataset.rate));
      closeSpeedChoices();
    });
    document.addEventListener('click', (event) => {
      if (!speedWrap.contains(event.target)) closeSpeedChoices();
    });

    favoriteBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      const name = favoriteBtn.dataset.value || '';
      if (!name || favoriteBtn.disabled) return;
      const item = currentItem();
      favoriteBtn.disabled = true;
      try {
        const isFavorite = await mediaActions.toggleFavorite(name);
        postActivity([{
          session_id: activitySessionId,
          uri: name,
          media_type: item?.type === 'video' ? 'video' : 'image',
          surface: 'flow',
          event: isFavorite ? 'favorite' : 'unfavorite',
        }]);
      } catch (error) {
        if (favoriteBtn.dataset.value === name) {
          favoriteBtn.title = T('flow.favorite.save_failed');
          favoriteBtn.setAttribute('aria-label', favoriteBtn.title);
        }
      } finally {
        favoriteBtn.disabled = false;
      }
    });

    infoBtn.addEventListener('click', () => {
      const target = activityTarget(currentItem());
      if (!target) return;
      postActivity([{
        session_id: activitySessionId,
        uri: target.uri,
        media_type: target.type,
        surface: 'flow',
        event: 'open_detail',
      }]);
    });

    if (douyinModeOn && douyinFavoriteBtn) {
      // The Douyin chrome mirrors the existing controls, whose favorite and
      // collection state also flips asynchronously, so react to class changes
      // instead of wiring every producer.
      new MutationObserver(mirrorDouyinActions).observe(favoriteBtn, { attributes: true, attributeFilter: ['class'] });
      new MutationObserver(mirrorDouyinActions).observe(collectionBtn, { attributes: true, attributeFilter: ['class'] });

      douyinFavoriteBtn.addEventListener('click', (e) => {
        e.preventDefault();
        favoriteBtn.click();
      });

      douyinCollectionBtn.addEventListener('click', (e) => {
        e.preventDefault();
        collectionBtn.click();
      });

      // These are <a role="button">: Enter activates them for free, Space does not,
      // and Space is the key screen-reader users press for a button.
      [douyinFavoriteBtn, douyinCollectionBtn].forEach((element) => {
        element.addEventListener('keydown', (event) => {
          if (event.key !== ' ' && event.key !== 'Spacebar') return;
          event.preventDefault();
          element.click();
        });
      });

      douyinInfoBtn.addEventListener('click', () => {
        const target = activityTarget(currentItem());
        if (!target) return;
        postActivity([{
          session_id: activitySessionId,
          uri: target.uri,
          media_type: target.type,
          surface: 'flow',
          event: 'open_detail',
        }]);
      });

      douyinCaptionExpand.addEventListener('click', (e) => {
        e.preventDefault();
        douyinCaptionFull.hidden = false;
        douyinCaptionFull.classList.add('is-open');
      });

      douyinCaptionCollapse.addEventListener('click', closeDouyinCaptionFull);

      douyinCaptionFull.addEventListener('click', (event) => {
        if (event.target === douyinCaptionFull) closeDouyinCaptionFull();
      });

      // A missing avatar falls back to the bundled placeholder once.
      douyinAvatarImg.addEventListener('error', () => {
        if (douyinAvatarImg.dataset.fallback === '1') return;
        douyinAvatarImg.dataset.fallback = '1';
        douyinAvatarImg.src = DEFAULT_AVATAR;
      });
    }

    if (douyinTopBack) {
      douyinTopBack.addEventListener('click', () => {
        if (window.history.length > 1) window.history.back();
        else window.location.href = '/flow';
      });
    }

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && collections.isOpen()) {
        collections.close();
        return;
      }
      if (event.key === 'Escape' && douyinCaptionFull?.classList.contains('is-open')) {
        closeDouyinCaptionFull();
        return;
      }
      // While an overlay owns the screen, arrows and Space must not reach the card
      // underneath: the panel is what the user is looking at.
      if (collections.isOpen() || douyinCaptionFull?.classList.contains('is-open')) {
        return;
      }
      const target = event.target;
      if (target && typeof target.closest === 'function') {
        if (target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) {
          return;
        }
      }
      if ((event.key === 'ArrowRight' || event.key === 'ArrowLeft') && currentVideo()) {
        event.preventDefault();
        seekBy(event.key === 'ArrowRight' ? keySeekSeconds : -keySeekSeconds);
        return;
      }
      if (event.key === 'ArrowRight') {
        if (!goGroupNext()) goNext();
      }
      if (event.key === 'ArrowLeft') {
        if (!goGroupPrev()) goPrev();
      }
      if (event.key === 'ArrowDown') goNext();
      if (event.key === 'ArrowUp') goPrev();
      if (event.key === ' ') {
        event.preventDefault();
        // On a live photo, the space bar toggles its sound, matching a long
        // press; elsewhere it is the usual play/pause toggle.
        if (!toggleLiveSound()) togglePlay();
      }
    });

    flowStatusRetry.addEventListener('click', async () => {
      if (flowStatus.dataset.kind === 'media-error') {
        await retryCurrentMedia();
      } else {
        await retryFlowLoad();
      }
    });

    flowStatusNext.addEventListener('click', async () => {
      hideFlowStatus();
      await goNext();
    });

    progressBar.addEventListener('input', () => {
      const item = currentItem();
      if (!item || item.type !== 'video') return;
      const video = item.el;

      if (!isDragging) {
        isDragging = true;
        wasPlayingBeforeDrag = !video.paused;
        video.pause();
        customControls.classList.add('is-seeking');
      }

      const seekTime = (progressBar.value / 100) * video.duration;
      timeCurrent.textContent = formatTime(seekTime);
      progressFill.style.width = `${progressBar.value}%`;
    });

    progressBar.addEventListener('change', () => {
      const item = currentItem();
      if (!item || item.type !== 'video') return;
      const video = item.el;

      const seekTime = (progressBar.value / 100) * video.duration;
      video.currentTime = seekTime;
      if (wasPlayingBeforeDrag) {
        video.play();
        playStatusIcon.classList.remove('visible');
      }
      isDragging = false;
      customControls.classList.remove('is-seeking');
    });

    captionBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const item = currentItem();
      if (!item || item.type !== 'image') return;
      mediaActions.generateCaption(item.name, { confirmExisting: true });
    });

    window.addEventListener('pagehide', () => {
      postActivity([finishActivity()], true);
    });


    await loadFeed();
    if (feedItems.length > 0) {
      await showItem(flowStartIndex());
    }
  })();
