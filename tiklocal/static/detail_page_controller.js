const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
const detailBoot = window.__TIKLOCAL_DETAIL_BOOT__ || {};

// Douyin mode turns the whole app into a vertical feed, so a detail page opened
// directly by URL hands the work back to the flow feed instead of the native player.
if (window.TikLocalDouyinMode?.enabled() && detailBoot.file) {
  window.TikLocalDouyinMode.redirect(detailBoot.file);
}

document.addEventListener('DOMContentLoaded', () => {
  const mainVideo = document.getElementById('main-video');
  const fullscreenBtn = document.getElementById('fullscreen-btn');
  const pipBtn = document.getElementById('pip-btn');
  const favoriteBtn = document.getElementById('favorite-btn');
  const favoriteText = document.getElementById('favorite-text');
  const copyLinkBtn = document.getElementById('copy-link-btn');
  const speedBtns = document.querySelectorAll('.speed-btn');
  const setThumbBtn = document.getElementById('set-thumb-btn');
  const moreMenuBtn = document.getElementById('more-menu-btn');
  const moreMenu = document.getElementById('detail-action-menu');
  
  const fileName = detailBoot.file;
  let isFavorited = false;

  const closeMoreMenu = () => {
    if (!moreMenu || !moreMenuBtn) return;
    moreMenu.hidden = true;
    moreMenuBtn.setAttribute('aria-expanded', 'false');
  };

  const toggleMoreMenu = () => {
    if (!moreMenu || !moreMenuBtn) return;
    const nextOpen = moreMenu.hidden;
    moreMenu.hidden = !nextOpen;
    moreMenuBtn.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
  };

  moreMenuBtn?.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    toggleMoreMenu();
  });

  document.addEventListener('click', (event) => {
    if (!moreMenu || moreMenu.hidden) return;
    const target = event.target;
    if (moreMenu.contains(target) || moreMenuBtn?.contains(target)) return;
    closeMoreMenu();
  });

  // Initialize favorite state.
  async function initFavoriteStatus() {
    try {
      const response = await fetch(`/api/favorite/${encodeURIComponent(fileName)}`);
      const data = await response.json();
      if (!response.ok || typeof data.favorite !== 'boolean') throw new Error('Failed to load favorite state');
      isFavorited = data.favorite;
      updateFavoriteUI();
    } catch (error) {
      console.error('Failed to load favorite state:', error);
    }
  }

  // Update the favorite button.
  function updateFavoriteUI() {
    if (isFavorited) {
      favoriteBtn?.classList.add('active');
      favoriteBtn?.setAttribute('aria-label', T('detail.favorite_remove'));
      if (favoriteText) favoriteText.textContent = T('detail.favorite_remove');
      if (typeof feather !== 'undefined' && feather.icons && feather.icons.heart) {
        const icon = favoriteBtn?.querySelector('svg');
        if (icon) icon.innerHTML = feather.icons.heart.toSvg();
      }
    } else {
      favoriteBtn?.classList.remove('active');
      favoriteBtn?.setAttribute('aria-label', T('detail.favorite_add'));
      if (favoriteText) favoriteText.textContent = T('detail.favorite_add');
      if (typeof feather !== 'undefined' && feather.icons && feather.icons.heart) {
        const icon = favoriteBtn?.querySelector('svg');
        if (icon) icon.innerHTML = feather.icons.heart.toSvg();
      }
    }
  }

  // Favorite action.
  favoriteBtn?.addEventListener('click', async (e) => {
    e.preventDefault();
    if (favoriteBtn.disabled) return;
    favoriteBtn.disabled = true;
    try {
      const response = await fetch(`/api/favorite/${encodeURIComponent(fileName)}`, {
        method: 'POST'
      });
      const data = await response.json();
      if (!response.ok || typeof data.favorite !== 'boolean') throw new Error('Failed to save favorite');
      isFavorited = data.favorite;
      updateFavoriteUI();
      TikLocalUI.toast(isFavorited ? T('detail.favorite_added') : T('detail.favorite_removed'), 'success');
    } catch (error) {
      console.error('Failed to update favorite:', error);
      TikLocalUI.toast(T('common.action_failed'), 'error');
    } finally {
      favoriteBtn.disabled = false;
    }
  });

  const togglePlayback = () => {
    if (!mainVideo) return;
    if (mainVideo.paused) {
      mainVideo.play();
    } else {
      mainVideo.pause();
    }
  };

  // Fullscreen.
  fullscreenBtn?.addEventListener('click', () => {
    if (!mainVideo) return;
    if (mainVideo.requestFullscreen) {
      mainVideo.requestFullscreen();
    } else if (mainVideo.webkitRequestFullscreen) {
      mainVideo.webkitRequestFullscreen();
    } else if (mainVideo.msRequestFullscreen) {
      mainVideo.msRequestFullscreen();
    }
  });

  // Picture in picture.
  pipBtn?.addEventListener('click', async () => {
    if (!mainVideo) return;
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
      } else {
        await mainVideo.requestPictureInPicture();
      }
    } catch (error) {
      TikLocalUI.toast(T('detail.pip_unsupported'), 'error');
    }
  });

  // Playback speed.
  speedBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      if (!mainVideo) return;
      const speed = parseFloat(btn.dataset.speed);
      mainVideo.playbackRate = speed;
      speedBtns.forEach(b => b.classList.toggle('is-active', b === btn));
      TikLocalUI.toast(T('detail.speed_set', { speed }), 'info');
    });
  });

  // Copy link.
  copyLinkBtn?.addEventListener('click', async () => {
    closeMoreMenu();
    try {
      const url = window.location.origin + '/media?uri=' + encodeURIComponent(fileName);
      await navigator.clipboard.writeText(url);
      TikLocalUI.toast(T('common.link_copied'), 'success');
    } catch (err) {
      TikLocalUI.toast(T('common.copy_failed'), 'error');
    }
  });

  // Keyboard shortcuts.
  document.addEventListener('keydown', (e) => {
    // Keydown can target the document (no focused element) where tagName is undefined,
    // and Space on a focused button must activate the button, not toggle playback.
    const target = e.target instanceof Element ? e.target : null;
    if (target && target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
    if (target && target.closest('button, a[href], summary') && e.key === ' ') return;
    
    switch(e.key) {
      case 'Escape':
        if (moreMenu && !moreMenu.hidden) {
          e.preventDefault();
          closeMoreMenu();
          moreMenuBtn?.focus();
        }
        break;
      case ' ':
        e.preventDefault();
        togglePlayback();
        break;
      case 'f':
      case 'F':
        e.preventDefault();
        fullscreenBtn?.click();
        break;
      case 'p':
      case 'P':
        e.preventDefault();
        pipBtn?.click();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        if (!mainVideo) break;
        mainVideo.currentTime = Math.max(0, mainVideo.currentTime - 10);
        break;
      case 'ArrowRight':
        e.preventDefault();
        if (!mainVideo) break;
        mainVideo.currentTime = Math.min(mainVideo.duration, mainVideo.currentTime + 10);
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (!mainVideo) break;
        mainVideo.volume = Math.min(1, mainVideo.volume + 0.1);
        break;
      case 'ArrowDown':
        e.preventDefault();
        if (!mainVideo) break;
        mainVideo.volume = Math.max(0, mainVideo.volume - 0.1);
        break;
    }
  });

  TikLocalResume.track(mainVideo, fileName, detailBoot.resumePosition || 0);

  // The library indexes .mkv/.avi too, and a truncated file plays as a black
  // box with no explanation. Say what happened instead of leaving the poster up.
  mainVideo?.addEventListener('error', () => {
    const code = mainVideo.error?.code;
    const message = code === 4
      ? T('detail.error.play_unsupported')
      : T('detail.error.play_failed');
    TikLocalUI.toast(message, 'error');
  });

  // Initialize.
  initFavoriteStatus();

  // Use the current paused frame as the thumbnail.
  setThumbBtn?.addEventListener('click', async () => {
    closeMoreMenu();
    if (!mainVideo) return;
    try {
      const ts = Math.max(0, mainVideo.currentTime || 0);
      const resp = await fetch(`/api/thumbnail/${encodeURIComponent(fileName)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ time: ts })
      });
      const data = await resp.json();
      if (resp.ok && data.success) {
        // Force a poster cache refresh.
        mainVideo.setAttribute('poster', `/thumb?uri=${encodeURIComponent(fileName)}&v=${Date.now()}`);
        TikLocalUI.toast(T('detail.thumbnail_updated'), 'success');
      } else {
        TikLocalUI.toast(data.error || T('detail.thumbnail_failed'), 'error');
      }
    } catch (e) {
      TikLocalUI.toast(T('common.error_try_later'), 'error');
    }
  });
});

// Delete moves the file to the trash and continues to a neighbour, where Undo is offered.
function confirmDelete() {
  TikLocalUI.trash(detailBoot.deleteUrl, detailBoot.afterDeleteUrl);
}
