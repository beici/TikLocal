(() => {
  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
  const themeButtons = Array.from(document.querySelectorAll('[data-theme-preference]'));
  const refreshButton = document.getElementById('refresh-library');
  const clearCacheButton = document.getElementById('clear-cache');
  const resetButton = document.getElementById('reset-recommendations');
  const credentialMask = document.getElementById('credential-mask');
  const pairingMask = document.getElementById('pairing-mask');
  const pairingServerUrl = document.getElementById('pairing-server-url');
  const pairingResult = document.getElementById('pairing-result');
  const pairingQr = document.getElementById('pairing-qr');
  const pairingLink = document.getElementById('pairing-link');
  const pairingExpiry = document.getElementById('pairing-expiry');
  const copyPairingButton = document.getElementById('copy-radio-pairing');
  const openRadioLink = document.getElementById('open-radio-link');
  const radioDeviceList = document.getElementById('radio-device-list');
  const mediaSourceList = document.getElementById('media-source-list');
  const mediaSourceMask = document.getElementById('media-source-mask');
  const mediaSourceForm = document.getElementById('media-source-form');
  const mediaSourcePath = document.getElementById('media-source-path');
  const mediaSourceName = document.getElementById('media-source-name');
  const mediaSourceHint = document.getElementById('media-source-hint');
  const mediaSourceTitle = document.getElementById('media-source-title');
  const addMediaSourceButton = document.getElementById('add-media-source');
  let editingSourceId = '';
  let mediaSourceTrigger = null;
  let pairingTimer = 0;
  let concurrencyBusy = false;

  // One place decides how the concurrency levels look, so a click and a reload
  // cannot disagree with each other or with the server value.
  function applyConcurrency(configured) {
    const known = [1, 2, 4];
    const shown = known.includes(configured) ? configured : 2;
    document.querySelectorAll('[data-concurrency]').forEach((button) => {
      const active = Number(button.dataset.concurrency) === shown;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    const copy = document.getElementById('download-levels-copy');
    if (!copy) return;
    copy.textContent = known.includes(configured)
      ? T('settings.download.concurrent_copy')
      : T('settings.download.concurrent_custom', { n: configured });
  }

  function formatDateTime(date) {
    const locale = document.documentElement.lang === 'en' ? 'en' : 'zh-CN';
    try {
      return date.toLocaleString(locale);
    } catch (_) {
      return date.toISOString();
    }
  }

  function currentThemePreference() {
    let saved = null;
    try {
      saved = localStorage.getItem('theme');
    } catch (_) {
      // Reading storage throws when it is blocked; fall back to the default
      // instead of breaking the whole settings controller.
      saved = null;
    }
    return ['light', 'dark', 'system'].includes(saved) ? saved : 'system';
  }

  function syncThemeButtons(preference = currentThemePreference()) {
    themeButtons.forEach((button) => {
      const active = button.dataset.themePreference === preference;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
  }

  function setThemePreference(preference) {
    if (window.themeManager?.setPreference) {
      window.themeManager.setPreference(preference);
    } else {
      localStorage.setItem('theme', preference);
      const resolved = preference === 'system'
        ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
        : preference;
      document.body.setAttribute('data-theme', resolved);
    }
    syncThemeButtons(preference);
  }


  async function readJson(response) {
    // A proxy or a crashed worker answers 502/504 with an HTML page; response.json()
    // then threw "Unexpected token '<'", which the toast showed verbatim instead of
    // saying the server did not answer in JSON.
    try {
      return await response.json();
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error(T('settings.request_failed'));
      }
      throw error;
    }
  }

  async function requestJson(url, options = {}) {
    // 无超时兜底时设置页会一直转圈（尤其大库扫描期间），用户只能整页刷新。
    const response = await fetch(url, options.signal ? options : { ...options, signal: tikTimeoutSignal(20000) });
    const payload = await readJson(response);
    if (!response.ok || payload.success === false) {
      const message = typeof payload.error === 'object' ? payload.error?.message : payload.error;
      throw new Error(message || T('settings.request_failed'));
    }
    return payload;
  }

  async function requestForm(url, formData) {
    const response = await fetch(url, { method: 'POST', body: formData, signal: tikTimeoutSignal(30000) });
    const payload = await readJson(response);
    if (!response.ok || payload.success === false) throw new Error(payload.error || T('settings.request_failed'));
    return payload;
  }

  function formatSyncTime(value) {
    if (!value) return T('settings.sync.no_scan');
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return T('settings.sync.recent');
    return T('settings.sync.last', { time: formatDateTime(date) });
  }

  async function loadStats() {
    try {
      const payload = await requestJson('/api/library/stats');
      const videos = Number(payload.videos || 0);
      const images = Number(payload.images || 0);
      document.getElementById('media-total').textContent = Number(payload.indexed_total || videos + images);
      document.getElementById('videos-count').textContent = videos;
      document.getElementById('images-count').textContent = images;
      const cacheCopy = document.getElementById('cache-copy');
      if (cacheCopy) {
        // The size lives inside a translated sentence; writing a bare #cache-size
        // span threw once the template stopped rendering it, and the throw flipped
        // the sync card into its error state on every settings visit.
        cacheCopy.textContent = T('settings.library.cache_copy', {
          size: `${Number(payload.cache_mb || 0)} MB`,
        });
      }
      document.getElementById('index-sync-copy').textContent = formatSyncTime(payload.last_synced_at);
      document.getElementById('index-sync-state').classList.remove('is-error');
    } catch (error) {
      document.getElementById('index-sync-copy').textContent = T('settings.sync.unavailable');
      document.getElementById('index-sync-state').classList.add('is-error');
    }
  }

  function formatDeviceTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? T('settings.device.pair_unknown') : T('settings.device.paired', { time: formatDateTime(date) });
  }

  function renderRadioDevices(devices = []) {
    radioDeviceList.replaceChildren();
    if (!devices.length) {
      const row = document.createElement('div');
      row.className = 'settings-row';
      row.innerHTML = `<span class="setting-icon"><i data-feather="radio"></i></span><div class="settings-row-main"><div class="settings-row-title"></div><div class="settings-row-copy"></div></div>`;
      row.querySelector('.settings-row-title').textContent = T('settings.device.none_title');
      row.querySelector('.settings-row-copy').textContent = T('settings.device.none_copy');
      radioDeviceList.appendChild(row);
      feather.replace();
      return;
    }

    devices.forEach((device) => {
      const row = document.createElement('div');
      row.className = 'settings-row';
      const icon = document.createElement('span');
      icon.className = 'setting-icon';
      icon.innerHTML = '<i data-feather="smartphone"></i>';
      const main = document.createElement('div');
      main.className = 'settings-row-main';
      const title = document.createElement('div');
      title.className = 'settings-row-title';
      title.textContent = device.name || 'TikLocal Radio';
      const copy = document.createElement('div');
      copy.className = `settings-row-copy radio-device-state${device.active ? '' : ' is-expired'}`;
      copy.textContent = `${device.active ? T('settings.device.token_active') : T('settings.device.invalidated')} · ${formatDeviceTime(device.created_at)}`;
      const revoke = document.createElement('button');
      revoke.className = 'settings-action is-danger';
      revoke.type = 'button';
      revoke.textContent = T('settings.device.revoke');
      revoke.addEventListener('click', () => {
        confirmAction(
          T('settings.device.revoke_title'),
          T('settings.device.revoke_copy', { name: device.name || T('settings.device.this_device') }),
          T('settings.device.revoke_action'),
          'danger',
          () => {
            runButton(revoke, T('settings.device.revoking'), async () => {
              await requestJson(`/api/radio/devices/${encodeURIComponent(device.id)}`, { method: 'DELETE' });
              await loadRadioDevices();
              TikLocalUI.toast(T('settings.device.revoked_toast'));
            }).catch(() => TikLocalUI.toast(T('settings.device.revoke_failed'), 'error'));
          },
        );
      });
      main.append(title, copy);
      row.append(icon, main, revoke);
      radioDeviceList.appendChild(row);
    });
    feather.replace();
  }

  async function loadRadioDevices() {
    try {
      const payload = await requestJson('/api/radio/devices');
      renderRadioDevices(payload.data?.devices || []);
    } catch (_) {
      radioDeviceList.querySelector('.settings-row-copy').textContent = T('settings.device.unavailable');
    }
  }

  async function confirmAction(title, copy, actionLabel, tone, onConfirm) {
    if (await TikLocalUI.confirm({ title, message: copy, confirmLabel: actionLabel, tone })) onConfirm();
  }

  async function runButton(button, busyLabel, task) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = busyLabel;
    try { await task(); } finally { button.disabled = false; button.textContent = original; }
  }

  function renderCredentials(files = []) {
    const list = document.getElementById('credential-list');
    document.getElementById('credential-summary').textContent = files.length
      ? T('settings.cred.saved_summary', { count: files.length })
      : T('settings.cred.none_summary');
    if (!files.length) {
      list.innerHTML = '<div class="credential-empty"></div>';
      list.querySelector('.credential-empty').textContent = T('settings.cred.none');
    } else {
      list.innerHTML = files.map((name) => `
        <div class="credential-item"><span class="credential-mark"><i data-feather="key"></i></span><span class="credential-name"></span></div>`).join('');
      list.querySelectorAll('.credential-name').forEach((element, index) => { element.textContent = files[index]; });
    }
    feather.replace();
  }

  async function loadDownloadSettings() {
    try {
      const [probePayload, configPayload, cookiePayload] = await Promise.all([
        requestJson('/api/download/probe', { method: 'POST' }),
        requestJson('/api/download/config'),
        requestJson('/api/download/cookies'),
      ]);
      const probe = probePayload.data || {};
      const missing = [
        !probe.yt_dlp_available ? T('settings.dl.part_video') : '',
        !probe.gallery_dl_available ? T('settings.dl.part_image') : '',
        !probe.ffmpeg_available ? T('settings.dl.part_merge') : '',
      ].filter(Boolean);
      document.getElementById('download-dependency-copy').textContent = missing.length
        ? T('settings.dl.not_ready', { parts: missing.join(', ') })
        : T('settings.dl.ready');
      const configured = Number(configPayload.data?.effective?.max_concurrent ?? 2);
      applyConcurrency(configured);
      renderCredentials(cookiePayload.data?.files || []);
    } catch (_) {
      document.getElementById('download-dependency-copy').textContent = T('settings.dl.status_unavailable');
      document.getElementById('credential-summary').textContent = T('settings.dl.cred_unavailable');
      // The list body is server-rendered with "正在加载凭据…" and only a successful
      // response replaces it, so a failed request left that line there for good.
      const list = document.getElementById('credential-list');
      if (list) {
        list.innerHTML = '<div class="credential-empty"></div>';
        list.querySelector('.credential-empty').textContent = T('settings.dl.cred_unavailable');
      }
    }
  }

  function openCredentials() {
    credentialMask.hidden = false;
    document.body.style.overflow = 'hidden';
    document.getElementById('close-credentials').focus();
  }

  const tiktokDownloaderMask = document.getElementById('tiktok-downloader-mask');

  function renderTikTokDownloaderStatus(status) {
    const copy = document.getElementById('tiktok-downloader-status');
    if (!copy) return;
    if (!status || !status.configured) {
      copy.textContent = T('settings.tiktok.not_configured');
      return;
    }
    copy.textContent = status.reachable
      ? T('settings.tiktok.connected', { url: status.base_url })
      : T('settings.tiktok.unreachable', { url: status.base_url });
  }

  async function loadTikTokDownloaderStatus() {
    try {
      const payload = await requestJson('/api/download/tiktok/status');
      renderTikTokDownloaderStatus(payload.data || {});
    } catch (_) {
      renderTikTokDownloaderStatus(null);
    }
  }

  async function openTikTokDownloader() {
    try {
      const payload = await requestJson('/api/download/tiktok/config');
      const section = payload.data || {};
      document.getElementById('tiktok-downloader-base-url').value = section.base_url || '';
      document.getElementById('tiktok-downloader-token').value = section.token || '';
    } catch (_) { /* keep the inputs empty when config is unavailable */ }
    document.getElementById('tiktok-downloader-hint').textContent = '';
    tiktokDownloaderMask.hidden = false;
    document.body.style.overflow = 'hidden';
    document.getElementById('tiktok-downloader-base-url').focus();
  }

  function closeTikTokDownloader() {
    tiktokDownloaderMask.hidden = true;
    document.body.style.overflow = '';
    document.getElementById('tiktok-downloader-edit').focus();
  }

  function closeCredentials() {
    credentialMask.hidden = true;
    document.body.style.overflow = '';
    document.getElementById('manage-credentials').focus();
  }

  // ---- access password ----
  // The dialog is the only place a password is typed, so the checks live here and
  // the server repeats them: a busy minlength attribute alone is not a guarantee.
  const passwordMask = document.getElementById('password-mask');
  const changePasswordButton = document.getElementById('change-password');
  const savePasswordButton = document.getElementById('save-password');

  function passwordInput(id) {
    return document.getElementById(id);
  }

  function setPasswordHint(message = '') {
    const hint = document.getElementById('password-hint');
    if (hint) hint.textContent = message;
  }

  function resetPasswordFields() {
    ['password-current', 'password-new', 'password-confirm'].forEach((id) => {
      const field = passwordInput(id);
      if (field) field.value = '';
    });
    setPasswordHint('');
  }

  function openPasswordDialog() {
    if (!passwordMask) return;
    resetPasswordFields();
    passwordMask.hidden = false;
    document.body.style.overflow = 'hidden';
    passwordInput('password-current')?.focus();
  }

  function closePasswordDialog() {
    if (!passwordMask) return;
    passwordMask.hidden = true;
    document.body.style.overflow = '';
    resetPasswordFields();
    changePasswordButton?.focus();
  }

  async function submitPasswordChange() {
    const current = passwordInput('password-current')?.value || '';
    const next = passwordInput('password-new')?.value || '';
    const confirmation = passwordInput('password-confirm')?.value || '';
    const minimum = Number(passwordInput('password-new')?.minLength || 8);
    if (!current || !next) { setPasswordHint(T('settings.password.required')); return; }
    if (next.length < minimum) { setPasswordHint(T('settings.password.too_short', { n: minimum })); return; }
    if (next === current) { setPasswordHint(T('settings.password.same')); return; }
    if (next !== confirmation) { setPasswordHint(T('settings.password.mismatch')); return; }
    setPasswordHint('');
    await runButton(savePasswordButton, T('settings.password.saving'), async () => {
      try {
        await requestJson('/api/auth/password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: current, new_password: next }),
        });
        closePasswordDialog();
        TikLocalUI.toast(T('settings.password.done'));
      } catch (error) {
        setPasswordHint(error.message || T('settings.password.failed'));
      }
    });
  }

  function updatePairingWarning() {
    let warning = '';
    try {
      const hostname = new URL(pairingServerUrl.value).hostname;
      if (['localhost', '127.0.0.1', '::1'].includes(hostname)) {
        warning = T('settings.pairing.lan_warning');
      }
    } catch (_) {
      warning = T('settings.pairing.incomplete');
    }
    document.getElementById('pairing-warning').textContent = warning;
  }

  function stopPairingTimer() {
    if (pairingTimer) window.clearInterval(pairingTimer);
    pairingTimer = 0;
  }

  function startPairingTimer(expiresIn) {
    stopPairingTimer();
    const deadline = Date.now() + Math.max(0, Number(expiresIn || 0)) * 1000;
    const render = () => {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      const minutes = Math.floor(seconds / 60);
      pairingExpiry.textContent = seconds
        ? T('settings.pairing.expires', { time: `${minutes}:${String(seconds % 60).padStart(2, '0')}` })
        : T('settings.pairing.expired');
      copyPairingButton.disabled = !seconds;
      openRadioLink.setAttribute('aria-disabled', String(!seconds));
      if (!seconds) stopPairingTimer();
    };
    render();
    pairingTimer = window.setInterval(render, 1000);
  }

  function openRadioPairing() {
    pairingServerUrl.value = window.location.origin;
    pairingResult.hidden = true;
    copyPairingButton.hidden = true;
    openRadioLink.hidden = true;
    pairingLink.value = '';
    pairingQr.removeAttribute('src');
    stopPairingTimer();
    updatePairingWarning();
    pairingMask.hidden = false;
    document.body.style.overflow = 'hidden';
    pairingServerUrl.focus();
  }

  function closeRadioPairing() {
    stopPairingTimer();
    pairingMask.hidden = true;
    document.body.style.overflow = '';
    document.getElementById('open-radio-pairing').focus();
  }

  async function copyPairingLink() {
    try {
      await navigator.clipboard.writeText(pairingLink.value);
    } catch (_) {
      pairingLink.focus();
      pairingLink.select();
      document.execCommand('copy');
    }
    TikLocalUI.toast(T('settings.pairing.copied'));
  }

  themeButtons.forEach((button) => button.addEventListener('click', () => setThemePreference(button.dataset.themePreference)));
  window.addEventListener('tiklocal:theme-changed', (event) => syncThemeButtons(event.detail?.preference));
  syncThemeButtons();

  const douyinModeToggle = document.getElementById('douyin-mode-toggle');
  if (douyinModeToggle) {
    // Storage throws when the browser blocks it; an unguarded read here would abort the
    // rest of this controller (folder list, first paint) before any of it runs.
    const storedDouyinMode = () => {
      try {
        return localStorage.getItem('douyin_mode') === '1';
      } catch (_) {
        return false;
      }
    };
    douyinModeToggle.checked = storedDouyinMode();
    douyinModeToggle.addEventListener('change', () => {
      try {
        localStorage.setItem('douyin_mode', douyinModeToggle.checked ? '1' : '0');
      } catch (_) {
        // Without storage the switch still applies to this page.
      }
      TikLocalUI.toast(douyinModeToggle.checked ? T('settings.douyin.enabled') : T('settings.douyin.disabled'));
    });
    // Other pages read the stored flag live, so this switch must follow a change made in
    // another tab instead of showing the opposite of what is actually stored.
    window.addEventListener('storage', (event) => {
      if (event.key !== 'douyin_mode') return;
      douyinModeToggle.checked = storedDouyinMode();
    });
  }

  // Language: persist to localStorage (per device) + cookie (so SSR matches), then reload.
  const languageButtons = [...document.querySelectorAll('.language-option')];
  const currentLocale = document.documentElement.lang || 'zh';
  function syncLanguageButtons() {
    languageButtons.forEach((button) => {
      const active = button.dataset.localeChoice === currentLocale;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }
  languageButtons.forEach((button) => button.addEventListener('click', () => {
    const locale = button.dataset.localeChoice;
    if (!locale || locale === currentLocale) return;
    try {
      window.localStorage.setItem('tiklocal:lang', locale);
    } catch (_) {
      // The cookie below is what the server renders from; storage is only a device hint.
    }
    const cookie = 'lang=' + encodeURIComponent(locale) + ';path=/;max-age=31536000;samesite=lax';
    document.cookie = cookie;
    if (document.cookie.indexOf('lang=' + encodeURIComponent(locale)) === -1) {
      // A blocked cookie means the reload comes straight back in the old language:
      // say so instead of reloading into the same page with no explanation.
      window.TikLocalUI?.toast(
        window.TikLocalI18n?.t('settings.language.cookie_blocked') || 'settings.language.cookie_blocked',
        'error'
      );
      return;
    }
    window.location.reload();
  }));
  syncLanguageButtons();

  // Media folders: the library roots the user points TikLocal at. Saving rewrites
  // the instance folder list and rescans, then the server returns the list it now
  // serves so the UI shows server truth rather than the last click.
  function mediaSourceBadge(text, variant) {
    const badge = document.createElement('span');
    badge.className = 'media-source-badge' + (variant ? ' is-' + variant : '');
    badge.textContent = text;
    return badge;
  }

  function folderName(path) {
    const segments = String(path || '').split(/[\\/]+/).filter(Boolean);
    return segments.length ? segments[segments.length - 1] : String(path || '');
  }

  // The launch folder arrives as the placeholder name "Default"; its folder name
  // says more, and the badge already marks which folder is the default one.
  function sourceDisplayName(source) {
    const name = String(source?.name || '');
    if (source?.is_default && /^default$/i.test(name)) return folderName(source.path);
    return name || String(source?.id || '');
  }

  function buildMediaSourceRow(source) {
    const row = document.createElement('div');
    row.className = 'media-source-row';
    row.dataset.mediaSourceId = source.id;
    if (!source.available) row.classList.add('is-unavailable');

    const main = document.createElement('div');
    main.className = 'media-source-main';
    const head = document.createElement('div');
    head.className = 'media-source-head';
    const name = document.createElement('span');
    name.className = 'media-source-name';
    name.textContent = sourceDisplayName(source);
    head.append(name);
    if (source.is_default) head.append(mediaSourceBadge(T('settings.library.source_default'), 'default'));
    head.append(mediaSourceBadge(T('settings.library.source_items', { count: Number(source.item_count) || 0 })));
    if (!source.available) head.append(mediaSourceBadge(T('settings.library.source_unavailable'), 'danger'));
    const path = document.createElement('div');
    path.className = 'media-source-path';
    path.textContent = source.path;
    path.title = source.path;
    main.append(head, path);

    const actions = document.createElement('div');
    actions.className = 'media-source-actions';
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'settings-action';
    edit.textContent = T('settings.library.source_edit');
    edit.addEventListener('click', () => openMediaSourceDialog(source, edit));
    actions.append(edit);
    if (source.removable) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'settings-action is-danger';
      remove.textContent = T('settings.library.source_remove');
      remove.addEventListener('click', () => removeMediaSource(source));
      actions.append(remove);
    }
    row.append(main, actions);
    return row;
  }

  function renderMediaSources(data) {
    if (!mediaSourceList) return;
    const sources = Array.isArray(data?.sources) ? data.sources : [];
    mediaSourceList.replaceChildren();
    if (!sources.length) {
      const empty = document.createElement('div');
      empty.className = 'media-source-empty';
      empty.textContent = T('settings.library.sources_empty');
      mediaSourceList.append(empty);
      return;
    }
    sources.forEach((source) => mediaSourceList.append(buildMediaSourceRow(source)));
  }

  function showMediaSourceError(message) {
    if (!mediaSourceList) return;
    mediaSourceList.replaceChildren();
    const box = document.createElement('div');
    box.className = 'media-source-empty';
    box.textContent = message;
    mediaSourceList.append(box);
  }

  async function loadMediaSources(options) {
    const payload = await requestJson('/api/library/sources', options);
    renderMediaSources(payload.data || {});
    return payload.data || {};
  }

  function openMediaSourceDialog(source, trigger) {
    if (!mediaSourceMask) return;
    editingSourceId = source ? String(source.id || '') : '';
    mediaSourceTrigger = trigger || addMediaSourceButton;
    mediaSourceTitle.textContent = T(editingSourceId
      ? 'settings.library.source_edit_title'
      : 'settings.library.source_add_title');
    mediaSourcePath.value = source ? source.path || '' : '';
    // Prefill what the list shows, so editing the default folder starts from the
    // folder name rather than the placeholder the launcher passed in.
    mediaSourceName.value = source ? sourceDisplayName(source) : '';
    mediaSourceHint.textContent = '';
    mediaSourceMask.hidden = false;
    document.body.style.overflow = 'hidden';
    mediaSourcePath.focus();
    mediaSourcePath.select();
  }

  function closeMediaSourceDialog() {
    if (!mediaSourceMask) return;
    mediaSourceMask.hidden = true;
    document.body.style.overflow = '';
    mediaSourceHint.textContent = '';
    editingSourceId = '';
    if (mediaSourceTrigger) mediaSourceTrigger.focus();
    mediaSourceTrigger = null;
  }

  function applyMediaSourceResult(payload) {
    const data = payload?.data || {};
    renderMediaSources(data);
    void loadStats();
    const scan = data.scan || {};
    if (scan.busy) {
      // The folder list is saved even when a scan was already running; say so
      // instead of implying the new items are visible already.
      TikLocalUI.toast(T('settings.library.source_scan_pending'), 'warning');
    } else if (scan.error) {
      // The folder was saved but the scan itself failed (full disk, corrupt index).
      // Reporting payload.message here announced success over an index that never
      // received the new files.
      TikLocalUI.toast(T('settings.library.source_scan_failed'), 'error');
    } else {
      TikLocalUI.toast(typeof payload?.message === 'string' && payload.message
        ? payload.message
        : T('settings.library.source_scan_done', { count: Number(scan.indexed) || 0 }));
    }
    // A folder that could not be reached (unplugged drive, wrong path) used to be
    // reported as "rescanned 0 items", which reads like success.
    const unavailable = Array.isArray(scan.unavailable_sources) ? scan.unavailable_sources : [];
    const sourceErrors = scan.source_errors && typeof scan.source_errors === 'object' ? scan.source_errors : {};
    const failedNames = unavailable.length ? unavailable : Object.keys(sourceErrors);
    if (failedNames.length) {
      TikLocalUI.toast(T('settings.library.source_scan_unavailable', { names: failedNames.join(', ') }), 'warning');
    }
    return data;
  }

  async function submitMediaSource() {
    if (!mediaSourcePath) return;
    const path = mediaSourcePath.value.trim();
    const name = mediaSourceName.value.trim();
    if (!path) {
      mediaSourceHint.textContent = T('settings.library.source_error_required');
      mediaSourcePath.focus();
      return;
    }
    const saveButton = document.getElementById('save-media-source');
    const editing = Boolean(editingSourceId);
    const options = {
      method: editing ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, name }),
      // A folder added for the first time is scanned before the response, so the
      // default 20 s budget is too small for a large library; a cold NAS scan can
      // outlast even this, hence the timeout-specific message below.
      signal: tikTimeoutSignal(180000),
    };
    const url = editing
      ? '/api/library/sources/' + encodeURIComponent(editingSourceId)
      : '/api/library/sources';
    try {
      await runButton(saveButton, T('settings.library.source_saving'), async () => {
        const payload = await requestJson(url, options);
        applyMediaSourceResult(payload);
        closeMediaSourceDialog();
      });
    } catch (error) {
      if (error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
        mediaSourceHint.textContent = T('settings.library.source_timeout');
        return;
      }
      mediaSourceHint.textContent = error instanceof Error && error.message
        ? error.message
        : T('settings.library.source_error_generic');
    }
  }

  function removeMediaSource(source) {
    const label = source.name || source.id;
    confirmAction(
      T('settings.library.source_remove_title'),
      T('settings.library.source_remove_copy', { name: label }),
      T('settings.library.source_remove_action'),
      'danger',
      async () => {
        try {
          const payload = await requestJson('/api/library/sources/' + encodeURIComponent(source.id), {
            method: 'DELETE',
            signal: tikTimeoutSignal(120000),
          });
          applyMediaSourceResult(payload);
        } catch (error) {
          TikLocalUI.toast(error instanceof Error && error.message
            ? error.message
            : T('settings.library.source_error_generic'), 'error');
        }
      },
    );
  }

  addMediaSourceButton?.addEventListener('click', () => openMediaSourceDialog(null, addMediaSourceButton));
  document.getElementById('close-media-source')?.addEventListener('click', closeMediaSourceDialog);
  document.getElementById('cancel-media-source')?.addEventListener('click', closeMediaSourceDialog);
  mediaSourceMask?.addEventListener('click', (event) => { if (event.target === mediaSourceMask) closeMediaSourceDialog(); });
  mediaSourceMask?.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMediaSourceDialog(); });
  mediaSourceForm?.addEventListener('submit', (event) => { event.preventDefault(); void submitMediaSource(); });
  if (mediaSourceList) {
    loadMediaSources().catch(() => showMediaSourceError(T('settings.library.source_error_generic')));
  }

  refreshButton.addEventListener('click', () => {
    confirmAction(T('settings.scan.confirm_title'), T('settings.scan.confirm_copy'), T('settings.scan.action'), 'primary', () => {
      runButton(refreshButton, T('settings.scan.busy'), async () => {
        // A cold scan of a large NAS folder outlives the 20 s default, and a
        // concurrent scan answers 409 with a reason; both used to collapse into
        // "scan failed" because the catch threw the error away.
        const payload = await requestJson('/api/library/sync', {
          method: 'POST',
          signal: tikTimeoutSignal(180000),
        });
        await loadStats();
        const data = payload.data || {};
        const unavailable = Array.isArray(data.unavailable_sources) ? data.unavailable_sources : [];
        const failed = data.source_errors && typeof data.source_errors === 'object' ? Object.keys(data.source_errors) : [];
        const sources = unavailable.concat(failed.filter((id) => unavailable.indexOf(id) < 0));
        if (data.busy) {
          // A concurrent scan reports indexed=0; saying "scan complete" made the
          // library look empty when it was simply still being built.
          TikLocalUI.toast(T('settings.scan.running'), 'warning');
          return;
        }
        if (sources.length) {
          TikLocalUI.toast(T('settings.scan.unavailable', {
            count: Number(data.indexed || 0),
            sources: sources.map((id) => '@' + id).join(', '),
          }), 'error');
          return;
        }
        TikLocalUI.toast(T('settings.scan.done', { count: Number(data.indexed || 0) }));
      }).catch((error) => {
        if (error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
          TikLocalUI.toast(T('settings.scan.timeout'), 'error');
          return;
        }
        TikLocalUI.toast((error && error.message) || T('settings.scan.failed'), 'error');
      });
    });
  });

  clearCacheButton.addEventListener('click', () => {
    confirmAction(T('settings.cache.confirm_title'), T('settings.cache.confirm_copy'), T('settings.cache.action'), 'warning', () => {
      runButton(clearCacheButton, T('settings.cache.busy'), async () => {
        const payload = await requestJson('/api/cache/clear', { method: 'POST' });
        await loadStats();
        TikLocalUI.toast(T('settings.cache.freed', { size: Number(payload.freed_mb || 0) }));
      }).catch(() => TikLocalUI.toast(T('settings.cache.failed'), 'error'));
    });
  });

  resetButton.addEventListener('click', () => {
    confirmAction(T('settings.reset.confirm_title'), T('settings.reset.confirm_copy'), T('settings.reset.action'), 'danger', () => {
      runButton(resetButton, T('settings.reset.busy'), async () => {
        await requestJson('/api/activity', { method: 'DELETE' });
        TikLocalUI.toast(T('settings.reset.done'));
      }).catch(() => TikLocalUI.toast(T('settings.reset.failed'), 'error'));
    });
  });

  document.getElementById('download-levels').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-concurrency]');
    if (!button || concurrencyBusy) return;
    const maxConcurrent = Number(button.dataset.concurrency);
    concurrencyBusy = true;
    document.querySelectorAll('[data-concurrency]').forEach((item) => { item.disabled = true; });
    try {
      const payload = await requestJson('/api/download/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_concurrent: maxConcurrent }),
      });
      // Trust the server value, not the last response that happened to arrive.
      const effective = Number(payload?.data?.effective?.max_concurrent ?? maxConcurrent);
      applyConcurrency(effective);
      TikLocalUI.toast(T('settings.conc.set', { n: effective }));
    } catch (_) { TikLocalUI.toast(T('settings.conc.failed'), 'error'); }
    finally {
      concurrencyBusy = false;
      document.querySelectorAll('[data-concurrency]').forEach((item) => { item.disabled = false; });
    }
  });

  document.getElementById('manage-credentials').addEventListener('click', openCredentials);
  document.getElementById('close-credentials').addEventListener('click', closeCredentials);
  credentialMask.addEventListener('click', (event) => { if (event.target === credentialMask) closeCredentials(); });
  credentialMask.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeCredentials(); });
  document.getElementById('tiktok-downloader-edit')?.addEventListener('click', () => void openTikTokDownloader());
  document.getElementById('close-tiktok-downloader')?.addEventListener('click', closeTikTokDownloader);
  tiktokDownloaderMask?.addEventListener('click', (event) => { if (event.target === tiktokDownloaderMask) closeTikTokDownloader(); });
  tiktokDownloaderMask?.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeTikTokDownloader(); });
  document.getElementById('save-tiktok-downloader')?.addEventListener('click', (event) => {
    const baseUrl = document.getElementById('tiktok-downloader-base-url').value.trim();
    const token = document.getElementById('tiktok-downloader-token').value.trim();
    const hint = document.getElementById('tiktok-downloader-hint');
    if (baseUrl && !/^https?:\/\//i.test(baseUrl)) {
      hint.textContent = T('settings.tiktok.bad_url');
      return;
    }
    runButton(event.currentTarget, T('settings.tiktok.saving'), async () => {
      const payload = await requestJson('/api/download/tiktok/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tiktok_downloader: { base_url: baseUrl, token } }),
      });
      renderTikTokDownloaderStatus({
        configured: Boolean(payload.data?.base_url),
        base_url: payload.data?.base_url || '',
        reachable: false,
      });
      await loadTikTokDownloaderStatus();
      TikLocalUI.toast(T('settings.tiktok.saved'));
    }).catch((error) => {
      document.getElementById('tiktok-downloader-hint').textContent = error.message || T('settings.tiktok.save_failed');
    });
  });
  changePasswordButton?.addEventListener('click', openPasswordDialog);
  document.getElementById('close-password')?.addEventListener('click', closePasswordDialog);
  document.getElementById('cancel-password')?.addEventListener('click', closePasswordDialog);
  passwordMask?.addEventListener('click', (event) => { if (event.target === passwordMask) closePasswordDialog(); });
  passwordMask?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closePasswordDialog(); }
  });
  document.getElementById('password-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!savePasswordButton?.disabled) void submitPasswordChange();
  });
  document.getElementById('open-radio-pairing').addEventListener('click', openRadioPairing);
  document.getElementById('close-radio-pairing').addEventListener('click', closeRadioPairing);
  pairingMask.addEventListener('click', (event) => { if (event.target === pairingMask) closeRadioPairing(); });
  pairingMask.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeRadioPairing(); });
  pairingServerUrl.addEventListener('input', updatePairingWarning);
  copyPairingButton.addEventListener('click', () => void copyPairingLink());
  document.getElementById('generate-radio-pairing').addEventListener('click', (event) => {
    runButton(event.currentTarget, T('settings.pairing.generating'), async () => {
      const payload = await requestJson('/api/radio/pairing-grants', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_url: pairingServerUrl.value }),
      });
      const data = payload.data || {};
      pairingLink.value = data.pairing_uri || '';
      pairingQr.src = data.qr_data_uri || '';
      pairingResult.hidden = false;
      copyPairingButton.hidden = false;
      openRadioLink.hidden = false;
      openRadioLink.href = pairingLink.value;
      openRadioLink.setAttribute('aria-disabled', 'false');
      startPairingTimer(data.expires_in);
    }).catch((error) => TikLocalUI.toast(error.message || T('settings.pairing.failed'), 'error'));
  });
  document.getElementById('upload-credential').addEventListener('click', (event) => {
    const input = document.getElementById('credential-file');
    const file = input.files?.[0];
    if (!file) { TikLocalUI.toast(T('settings.cred.select_file'), 'error'); return; }
    runButton(event.currentTarget, T('settings.cred.uploading'), async () => {
      const formData = new FormData();
      formData.append('file', file);
      await requestForm('/api/download/cookies/upload', formData);
      input.value = '';
      const payload = await requestJson('/api/download/cookies');
      renderCredentials(payload.data?.files || []);
      TikLocalUI.toast(T('settings.cred.updated'));
    }).catch(() => TikLocalUI.toast(T('settings.cred.upload_failed'), 'error'));
  });

  function formatBytes(bytes) {
    const mb = Number(bytes || 0) / 1048576;
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
  }

  function renderTrash(items, retentionDays) {
    const list = document.getElementById('trash-list');
    const total = items.reduce((sum, item) => sum + Number(item.size || 0), 0);
    document.getElementById('trash-summary').textContent = items.length
      ? T(items.length === 1 ? 'settings.trash.summary_one' : 'settings.trash.summary_other', {
        count: items.length,
        size: formatBytes(total),
        days: retentionDays,
      })
      : T('settings.trash.retention', { days: retentionDays });
    document.getElementById('empty-trash').hidden = !items.length;
    list.querySelectorAll('.trash-item').forEach((row) => row.remove());
    items.forEach((item) => {
      const days = Math.max(0, retentionDays - Math.floor((Date.now() - new Date(item.deleted_at)) / 86400000));
      const row = document.createElement('div');
      row.className = 'settings-row trash-item';
      row.innerHTML = `<span class="setting-icon"><i data-feather="${/\.(jpe?g|png|gif|webp|bmp)$/i.test(item.name) ? 'image' : 'film'}"></i></span><div class="settings-row-main"><div class="settings-row-title trash-name"></div><div class="settings-row-copy"></div></div>`;
      row.querySelector('.trash-name').textContent = item.name;
      const daysKey = days === 1 ? 'settings.trash.days_left_one' : 'settings.trash.days_left';
      row.querySelector('.settings-row-copy').textContent = `${formatBytes(item.size)} · ${T(daysKey, { days })}`;
      const restore = document.createElement('button');
      restore.className = 'settings-action';
      restore.type = 'button';
      restore.textContent = T('settings.trash.restore');
      restore.addEventListener('click', () => {
        runButton(restore, T('settings.trash.restoring'), async () => {
          const data = await requestJson(`/api/trash/${encodeURIComponent(item.id)}/restore`, { method: 'POST' });
          await Promise.all([loadTrash(), loadStats()]);
          TikLocalUI.toast(T('settings.trash.restored', { name: item.name }), 'success', { label: T('settings.trash.open'), run: () => { location.href = data.url; } });
        }).catch((error) => TikLocalUI.toast(error.message || T('settings.trash.restore_failed'), 'error'));
      });
      const purge = document.createElement('button');
      purge.className = 'settings-action is-danger';
      purge.type = 'button';
      purge.textContent = T('common.delete');
      purge.addEventListener('click', () => {
        confirmAction(T('settings.trash.delete_title', { name: item.name }), T('settings.trash.delete_copy'), T('common.delete'), 'danger', () => {
          runButton(purge, T('settings.trash.deleting'), async () => {
            await requestJson(`/api/trash/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
            await loadTrash();
          }).catch(() => TikLocalUI.toast(T('settings.trash.delete_failed'), 'error'));
        });
      });
      row.querySelector('.settings-row-main').after(restore, purge);
      list.appendChild(row);
    });
    feather.replace();
  }

  async function loadTrash() {
    try {
      const data = await requestJson('/api/trash');
      renderTrash(data.items || [], data.retention_days || 30);
    } catch (_) {
      document.getElementById('trash-summary').textContent = T('settings.trash.unavailable');
    }
  }

  document.getElementById('empty-trash').addEventListener('click', (event) => {
    const button = event.currentTarget;
    confirmAction(T('settings.trash.empty_title'), T('settings.trash.empty_copy'), T('settings.trash.empty_action'), 'danger', () => {
      runButton(button, T('settings.trash.emptying'), async () => {
        const data = await requestJson('/api/trash', { method: 'DELETE' });
        await loadTrash();
        if (data.failed) {
          // A locked file stays in the trash; "emptied N" alone read as success and
          // the rows that survived looked like a rendering bug.
          TikLocalUI.toast(T('settings.trash.empty_partial', { purged: data.purged, failed: data.failed }), 'error');
          return;
        }
        TikLocalUI.toast(T('settings.trash.emptied', { count: data.purged }));
      }).catch(() => TikLocalUI.toast(T('settings.trash.empty_failed'), 'error'));
    });
  });

  // Author profiles: every media source keeps its own home-page link, so the
  // profile sync and the "download what is missing" update are one click per
  // row — and one click for the whole library at the top of the card.
  const authorSyncResult = document.getElementById('author-sync-result');
  const authorSyncList = document.getElementById('author-sync-source-list');
  const authorSyncSummary = document.getElementById('author-sync-summary');
  const authorSyncCount = document.getElementById('author-sync-count');
  let authorRows = [];

  function authorSyncSummaryText(items) {
    const total = items.length;
    const synced = items.filter((item) => item.has_profile).length;
    return total
      ? T('settings.sync.author_summary', { total, synced })
      : T('settings.sync.author_summary_empty');
  }

  function rowUrl(row, item) {
    const input = row.querySelector('.author-sync-url');
    return ((input && input.value) || '').trim() || (item && item.profile_url) || '';
  }

  function setRowStatus(row, text, isError) {
    const status = row.querySelector('.author-sync-row-status');
    if (!status) return;
    status.textContent = text || '';
    status.hidden = !text;
    status.classList.toggle('is-error', Boolean(isError));
  }

  async function postAuthor(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      // These endpoints fetch from the outside world, so they are the slowest on the
      // page and the ones most likely to hang. Without a deadline the caller's
      // finally never ran and the row stayed "syncing" with a disabled button until
      // the page was reloaded.
      signal: tikTimeoutSignal(60000),
    });
    const payload = await response.json().catch(() => ({}));
    return { status: response.status, payload };
  }

  function rowMeta(item) {
    return [
      item.douyin_id ? T('author.douyin_id', { id: item.douyin_id }) : '',
      item.has_profile ? T('settings.sync.author_synced') : T('settings.sync.author_unsynced'),
      Number(item.local_count || 0) ? T('settings.sync.author_local_count', { count: Number(item.local_count) }) : '',
    ].filter(Boolean).join(' · ');
  }

  function renderAuthorSources(items) {
    authorRows = Array.isArray(items) ? items : [];
    authorSyncSummary.textContent = authorSyncSummaryText(authorRows);
    authorSyncCount.textContent = authorRows.length ? authorSyncSummaryText(authorRows) : '';
    if (!authorRows.length) {
      authorSyncList.innerHTML = '<div class="settings-row-copy author-sync-empty"></div>';
      authorSyncList.querySelector('.author-sync-empty').textContent = T('settings.sync.author_sources_empty');
      return;
    }
    authorSyncList.innerHTML = authorRows.map(() => `
      <div class="author-sync-row">
        <img class="author-sync-avatar" src="" alt="">
        <div class="author-sync-main">
          <div class="settings-row-title author-sync-name"></div>
          <div class="settings-row-copy author-sync-meta"></div>
          <input class="author-sync-url" type="url" inputmode="url" spellcheck="false" autocomplete="off">
          <div class="author-sync-row-actions">
            <button class="settings-action author-sync-profile" type="button"></button>
            <button class="settings-action author-sync-videos" type="button"></button>
          </div>
          <p class="author-sync-row-status" aria-live="polite" hidden></p>
        </div>
      </div>`).join('');
    authorSyncList.querySelectorAll('.author-sync-row').forEach((row, index) => {
      const item = authorRows[index];
      const avatar = row.querySelector('.author-sync-avatar');
      avatar.src = item.avatar_url || '';
      avatar.alt = item.display_name || '';
      avatar.loading = 'lazy';
      row.querySelector('.author-sync-name').textContent = item.display_name || item.folder_name || item.source_id;
      row.querySelector('.author-sync-meta').textContent = rowMeta(item);
      const input = row.querySelector('.author-sync-url');
      input.value = item.profile_url || '';
      input.placeholder = T('settings.sync.author_url_placeholder');
      input.addEventListener('change', () => void bindAuthorUrl(item, row, input));
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          input.blur();
        }
      });
      const profileButton = row.querySelector('.author-sync-profile');
      profileButton.textContent = T('settings.sync.author_sync_profile');
      profileButton.addEventListener('click', () => void syncAuthorProfile(item, row, input));
      const videosButton = row.querySelector('.author-sync-videos');
      videosButton.textContent = T('settings.sync.author_update_videos');
      videosButton.addEventListener('click', () => void updateAuthorVideos(item, row, input));
    });
    feather.replace();
  }

  async function bindAuthorUrl(item, row, input) {
    const url = (input.value || '').trim();
    if (!url) return;
    try {
      const { status, payload } = await postAuthor(`/api/author/${encodeURIComponent(item.source_id)}/bind`, { profile_url: url });
      if (status === 200 && payload.ok) {
        item.profile_url = payload.profile_url || url;
        setRowStatus(row, T('settings.sync.author_bound'), false);
        return;
      }
      setRowStatus(row, payload.error || T('settings.sync.author_failed'), true);
    } catch (_) {
      setRowStatus(row, T('settings.sync.author_failed'), true);
    }
  }

  async function syncAuthorProfile(item, row, input) {
    const url = (input.value || '').trim() || item.profile_url || '';
    if (!url) {
      TikLocalUI.toast(T('settings.sync.author_need_url'), 'error');
      input.focus();
      return;
    }
    const button = row.querySelector('.author-sync-profile');
    await runButton(button, T('settings.sync.author_working'), async () => {
      const { status, payload } = await postAuthor(`/api/author/${encodeURIComponent(item.source_id)}/sync`, { profile_url: url });
      if (status === 200 && payload.ok) {
        const profile = payload.profile || {};
        const name = profile.nickname || item.display_name || item.source_id;
        setRowStatus(row, T('settings.sync.author_profile_done', { nickname: name }), false);
        TikLocalUI.toast(T('settings.sync.author_profile_done', { nickname: name }));
        await loadAuthorSources();
        return;
      }
      setRowStatus(row, payload.error || T('settings.sync.author_failed'), true);
      TikLocalUI.toast(payload.error || T('settings.sync.author_failed'), 'error');
    }).catch(() => {});
  }

  async function updateAuthorVideos(item, row, input) {
    const url = (input.value || '').trim() || item.profile_url || '';
    if (!url) {
      TikLocalUI.toast(T('settings.sync.author_need_url'), 'error');
      input.focus();
      return;
    }
    const button = row.querySelector('.author-sync-videos');
    await runButton(button, T('settings.sync.author_videos_running'), async () => {
      const { status, payload } = await postAuthor(`/api/author/${encodeURIComponent(item.source_id)}/update-videos`, { profile_url: url });
      if (status === 200 && payload.ok) {
        const queued = Number(payload.queued || 0);
        const skipped = Number(payload.skipped || 0);
        const text = queued
          ? T('settings.sync.author_videos_done', { queued, skipped })
          : T('settings.sync.author_videos_none', { skipped });
        item.profile_url = url;
        setRowStatus(row, text, false);
        TikLocalUI.toast(text);
        return;
      }
      setRowStatus(row, payload.error || T('settings.sync.author_failed'), true);
      TikLocalUI.toast(payload.error || T('settings.sync.author_failed'), 'error');
    }).catch(() => {});
  }

  function authorRowsWithUrl() {
    const rows = Array.from(authorSyncList.querySelectorAll('.author-sync-row'));
    return rows
      .map((row, index) => ({ row, item: authorRows[index] }))
      .filter(({ row, item }) => Boolean(item && rowUrl(row, item)));
  }

  async function syncAllAuthorProfiles() {
    const entries = authorRowsWithUrl().map(({ row, item }) => ({ source_id: item.source_id, profile_url: rowUrl(row, item) }));
    if (!entries.length) {
      TikLocalUI.toast(T('settings.sync.author_need_url'), 'error');
      return;
    }
    const button = document.getElementById('author-sync-all-profiles');
    await runButton(button, T('settings.sync.author_busy'), async () => {
      // A partial batch answers 502 while still listing the authors that synced,
      // so read the body instead of letting requestJson drop it.
      let payload = {};
      try {
        const response = await fetch('/api/author/sync', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items: entries }),
          // Every author is fetched over the network; without a deadline the
          // caller's finally never ran and the button stayed disabled on "syncing".
          signal: tikTimeoutSignal(120000),
        });
        payload = await response.json();
      } catch (_) {
        payload = { ok: false, error: T('settings.sync.author_failed') };
      }
      renderAuthorSyncResult(payload.items || [], payload.synced);
      if (!payload.ok) TikLocalUI.toast(payload.error || T('settings.sync.author_failed'), 'error');
      await loadAuthorSources();
    }).catch(() => {});
  }

  async function updateAllAuthorVideos() {
    const targets = authorRowsWithUrl();
    if (!targets.length) {
      TikLocalUI.toast(T('settings.sync.author_need_url'), 'error');
      return;
    }
    const button = document.getElementById('author-sync-all-videos');
    await runButton(button, T('settings.sync.author_videos_running'), async () => {
      let queued = 0;
      let skipped = 0;
      let failed = 0;
      for (const { row, item } of targets) {
        const url = rowUrl(row, item);
        setRowStatus(row, T('settings.sync.author_working'), false);
        try {
          const { status, payload } = await postAuthor(`/api/author/${encodeURIComponent(item.source_id)}/update-videos`, { profile_url: url });
          if (status === 200 && payload.ok) {
            queued += Number(payload.queued || 0);
            skipped += Number(payload.skipped || 0);
            item.profile_url = url;
            setRowStatus(row, Number(payload.queued || 0)
              ? T('settings.sync.author_videos_done', { queued: Number(payload.queued), skipped: Number(payload.skipped || 0) })
              : T('settings.sync.author_videos_none', { skipped: Number(payload.skipped || 0) }), false);
          } else {
            failed += 1;
            setRowStatus(row, payload.error || T('settings.sync.author_failed'), true);
          }
        } catch (_) {
          failed += 1;
          setRowStatus(row, T('settings.sync.author_failed'), true);
        }
      }
      const text = T('settings.sync.author_videos_summary', { queued, skipped });
      TikLocalUI.toast(failed ? `${text} · ${T('settings.sync.author_done', { synced: targets.length - failed, failed })}` : text, failed ? 'error' : undefined);
      await loadAuthorSources();
    }).catch(() => {});
  }

  document.getElementById('author-sync-all-profiles').addEventListener('click', () => void syncAllAuthorProfiles());
  document.getElementById('author-sync-all-videos').addEventListener('click', () => void updateAllAuthorVideos());
  document.getElementById('author-sync-reload').addEventListener('click', (event) => {
    runButton(event.currentTarget, T('settings.sync.author_busy'), loadAuthorSources).catch(() => {});
  });

  function renderAuthorSyncResult(items, synced) {
    if (!items.length) {
      authorSyncResult.hidden = true;
      return;
    }
    const failed = items.filter((item) => !item.ok).length;
    authorSyncResult.hidden = false;
    authorSyncResult.innerHTML = '<p class="author-sync-result-copy"></p><ul class="author-sync-result-list"></ul>';
    authorSyncResult.querySelector('.author-sync-result-copy').textContent = failed
      ? T('settings.sync.author_done', { synced: Number(synced || 0), failed })
      : T('settings.sync.author_done_clean', { synced: Number(synced || items.length) });
    const list = authorSyncResult.querySelector('.author-sync-result-list');
    list.innerHTML = items.map(() => '<li class="author-sync-result-item"></li>').join('');
    list.querySelectorAll('.author-sync-result-item').forEach((row, index) => {
      const item = items[index];
      row.textContent = item.ok
        ? [item.nickname || item.source_id, item.unique_id].filter(Boolean).join(' · ')
        : [item.source_id, item.error].filter(Boolean).join(' · ');
      row.classList.toggle('is-error', !item.ok);
    });
  }

  async function loadAuthorSources() {
    try {
      const payload = await requestJson('/api/author/sources');
      renderAuthorSources(payload.items || []);
    } catch (_) {
      document.getElementById('author-sync-summary').textContent = T('settings.sync.author_summary_failed');
      authorSyncList.innerHTML = '<div class="settings-row-copy author-sync-empty"></div>';
      authorSyncList.querySelector('.author-sync-empty').textContent = T('settings.sync.author_sources_empty');
    }
  }

  // 设置页会被 bfcache 原样恢复（no-cache 不阻止 bfcache），回来时表单还是旧值，
  // 此时点保存会把旧配置写回去。恢复时整页重载一次最稳妥。
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) window.location.reload();
  });

  // 设置页的统计/下载/回收站在断网时只会显示「暂不可用」，恢复后自动补一次。
  // 回收站卡片漏在这里时，恢复网络后仍停在「无法获取回收站状态」，恢复与清空整块不可用。
  window.addEventListener('tiklocal:online', () => {
    loadStats();
    loadDownloadSettings();
    loadTrash();
  });

  loadStats();
  loadRadioDevices();
  loadTrash();
  loadDownloadSettings();
  loadTikTokDownloaderStatus();
  // The author-sync card renders from this call; without it the card sits on its
  // "loading" placeholder until the user presses 刷新.
  loadAuthorSources();
})();
