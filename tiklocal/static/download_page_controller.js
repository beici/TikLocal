(() => {
  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
  const ACTIVE_STATUSES = new Set(['queued', 'running']);
  const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']);
  let pollTimer = null;
  let lastJobsSignature = '';
  let dependencyMeta = { yt_dlp_available: false, gallery_dl_available: false, ffmpeg_available: false, tiktok_downloader: null };
  let selectedEngine = 'yt-dlp';
  let selectedTab = 'post';
  const DEFAULT_PLACEHOLDER = T('download.url_placeholder');
  const TIKTOK_PLACEHOLDER = T('download.tiktok_placeholder');

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || T('download.request_failed', { status: response.status }));
    }
    return payload.data || {};
  }

  function safeHostname(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, '') || T('download.external_link');
    } catch (_) {
      return T('download.external_link');
    }
  }

  function platformMark(url) {
    const hostname = safeHostname(url);
    const knownMarks = [
      ['douyin', 'DY'], ['instagram', 'IG'], ['youtube', 'YT'], ['youtu.be', 'YT'],
      ['tiktok', 'TT'], ['x.com', 'X'], ['twitter', 'X'],
      ['pinterest', 'P'],
    ];
    const match = knownMarks.find(([part]) => hostname.includes(part));
    return match ? match[1] : hostname.slice(0, 2).toUpperCase();
  }

  function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const today = new Date();
    if (date.toDateString() === today.toDateString()) {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function buildMediaHref(file) {
    const value = String(file || '').trim();
    const dot = value.lastIndexOf('.');
    const extension = dot >= 0 ? value.slice(dot).toLowerCase() : '';
    if (IMAGE_EXTENSIONS.has(extension)) return `/image?uri=${encodeURIComponent(value)}`;
    return `/detail/${encodeURIComponent(value)}`;
  }

  function outputFiles(job) {
    const files = Array.isArray(job.output_files_rel)
      ? job.output_files_rel.filter((item) => String(item || '').trim())
      : [];
    if (!files.length && job.output_path_rel) files.push(job.output_path_rel);
    return files;
  }

  function statusCopy(job) {
    const fileCount = Number.isInteger(job.file_count) ? job.file_count : outputFiles(job).length;
    const labels = {
      queued: T('download.status.queued'),
      running: typeof job.progress_percent === 'number'
        ? T('download.status.progress', { pct: Math.round(job.progress_percent) })
        : T('download.status.downloading'),
      success: fileCount > 0 ? T('download.status.saved_files', { n: fileCount }) : T('download.status.saved'),
      failed: T('download.status.failed'),
      canceled: T('download.status.canceled'),
    };
    return labels[job.status] || T('download.status.unknown');
  }

  function renderJob(job) {
    const files = outputFiles(job);
    const firstFile = files[0] || '';
    const isActive = ACTIVE_STATUSES.has(job.status);
    const progress = typeof job.progress_percent === 'number'
      ? Math.max(0, Math.min(100, job.progress_percent))
      : null;
    const className = job.status === 'running' || job.status === 'queued'
      ? 'is-running'
      : (job.status === 'failed' ? 'is-failed' : '');
    const time = formatTime(job.finished_at || job.created_at);
    const sourceAction = `<a class="download-action" href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer"><i data-feather="external-link"></i><span>${escapeHtml(T('download.visit_source'))}</span></a>`;
    const menu = !isActive ? `
      <div class="download-job-menu">
        <button class="download-icon-btn" type="button" data-action="menu" data-job-id="${escapeHtml(job.id)}" aria-label="${escapeHtml(T('common.more_actions'))}" aria-expanded="false"><i data-feather="more-horizontal"></i></button>
        <div class="download-job-menu-panel" hidden>
          <button class="download-menu-action" type="button" data-action="delete" data-job-id="${escapeHtml(job.id)}">${escapeHtml(T('download.remove_record'))}</button>
        </div>
      </div>` : '';

    let actions = '';
    if (isActive) {
      actions = `${sourceAction}<button class="download-action" type="button" data-action="cancel" data-job-id="${escapeHtml(job.id)}">${escapeHtml(T('common.cancel'))}</button>`;
    } else if (job.status === 'success' && firstFile) {
      actions = `
        <a class="download-action" href="${buildMediaHref(firstFile)}"><i data-feather="eye"></i><span>${escapeHtml(T('download.view_media'))}</span></a>
        ${sourceAction}${menu}`;
    } else {
      actions = `
        ${(job.status === 'failed' || job.status === 'canceled') ? `<button class="download-action is-retry" type="button" data-action="retry" data-job-id="${escapeHtml(job.id)}"><i data-feather="rotate-ccw"></i><span>${escapeHtml(job.failure_stage === 'index' ? T('download.retry_register') : T('download.retry_download'))}</span></button>` : ''}
        ${sourceAction}${menu}`;
    }

    return `
      <article class="download-job ${className}" data-job-id="${escapeHtml(job.id)}">
        <div class="download-job-mark" aria-hidden="true">${escapeHtml(platformMark(job.url))}</div>
        <div class="download-job-main">
          <div class="download-job-top">
            <div class="download-job-identity"><span class="download-job-domain">${escapeHtml(safeHostname(job.url))}</span><span class="download-job-status">${escapeHtml(engineLabel(job.engine))}</span></div>
            ${time ? `<span class="download-job-time">${escapeHtml(time)}</span>` : ''}
          </div>
          <p class="download-job-copy">${escapeHtml(statusCopy(job))}</p>
          ${progress !== null && isActive ? `<div class="download-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.toFixed(0)}"><div class="download-progress-bar" style="width:${progress.toFixed(1)}%"></div></div>` : ''}
          ${files.length > 1 ? `<div class="download-job-meta"><span>${escapeHtml(T('download.items_count', { n: files.length }))}</span></div>` : ''}
          ${job.error_message ? `<p class="download-error">${escapeHtml(job.error_message)}</p>` : ''}
          <div class="download-job-actions">${actions}</div>
        </div>
      </article>`;
  }

  function emptyHistory() {
    return `
      <div class="download-empty">
        <div class="download-empty-icon"><i data-feather="download-cloud"></i></div>
        <div class="download-empty-title">${escapeHtml(T('download.empty_title'))}</div>
        <div class="download-empty-copy">${escapeHtml(T('download.empty_copy'))}</div>
      </div>`;
  }

  function jobsSignature(jobs) {
    return jobs.map((job) => [
      job.id, job.status, job.progress_percent, job.finished_at,
      job.file_count, job.error_message,
    ].join(':')).join('|');
  }

  function renderJobs(jobs, { force = false } = {}) {
    const signature = jobsSignature(jobs);
    if (!force && signature === lastJobsSignature) return;
    lastJobsSignature = signature;

    const activeJobs = jobs.filter((job) => ACTIVE_STATUSES.has(job.status));
    const historyJobs = jobs.filter((job) => !ACTIVE_STATUSES.has(job.status));
    const activeSection = document.getElementById('active-section');
    const activeList = document.getElementById('active-list');
    const historyList = document.getElementById('history-list');
    const historyFooter = document.getElementById('history-footer');

    activeSection.hidden = activeJobs.length === 0;
    document.getElementById('active-count').textContent = activeJobs.length ? T('download.items_count', { n: activeJobs.length }) : '';
    activeList.innerHTML = activeJobs.map(renderJob).join('');
    document.getElementById('history-count').textContent = historyJobs.length ? T('download.items_count', { n: historyJobs.length }) : '';
    historyList.innerHTML = historyJobs.length ? historyJobs.map(renderJob).join('') : emptyHistory();
    historyFooter.hidden = historyJobs.length === 0;
    window.feather?.replace();
    schedulePolling(activeJobs.length > 0);
  }

  async function refreshJobs({ force = false } = {}) {
    try {
      const data = await api('/api/download/jobs?limit=80');
      const jobs = data.jobs || [];
      renderJobs(jobs, { force });
    } catch (error) {
      TikLocalUI.toast(error.message, 'error');
      schedulePolling(false);
    }
  }

  function schedulePolling(hasActiveJobs) {
    if (pollTimer) window.clearTimeout(pollTimer);
    const delay = document.hidden ? 15000 : (hasActiveJobs ? 2200 : 12000);
    pollTimer = window.setTimeout(() => refreshJobs(), delay);
  }

  async function refreshSetup() {
    try {
      dependencyMeta = await api('/api/download/probe', { method: 'POST' });
    } catch (_) {}
  }

  function updateDetectedSite(input) {
    const value = input.value.trim();
    const status = document.getElementById('download-detected');
    status.classList.remove('is-error');
    if (!value) {
      status.textContent = '';
      return true;
    }
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error();
      status.textContent = T('download.detected', { host: safeHostname(value) });
      return true;
    } catch (_) {
      status.textContent = T('download.invalid_url');
      status.classList.add('is-error');
      return false;
    }
  }

  function engineLabel(engine) {
    if (engine === 'gallery-dl') return T('download.engine.images');
    if (engine === 'tiktok-downloader') return T('download.engine.tiktok');
    return T('download.engine.video');
  }

  function tiktokStatus() {
    return dependencyMeta.tiktok_downloader || null;
  }

  function selectEngine(engine) {
    selectedEngine = ['gallery-dl', 'tiktok-downloader'].includes(engine) ? engine : 'yt-dlp';
    const switchElement = document.getElementById('media-switch');
    switchElement.dataset.value = selectedEngine;
    switchElement.querySelectorAll('[data-engine]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.engine === selectedEngine));
    });
    const optionsRow = document.getElementById('download-tiktok-options');
    if (optionsRow) optionsRow.hidden = selectedEngine !== 'tiktok-downloader';
    const urlInput = document.getElementById('download-url');
    if (urlInput) urlInput.placeholder = selectedEngine === 'tiktok-downloader' ? TIKTOK_PLACEHOLDER : DEFAULT_PLACEHOLDER;

    const alert = document.getElementById('download-inline-alert');
    const alertCopy = document.getElementById('download-inline-alert-copy');
    let unavailable = false;
    if (selectedEngine === 'gallery-dl' && !dependencyMeta.gallery_dl_available) {
      unavailable = true;
      alertCopy.textContent = T('download.alert.image_not_ready');
    } else if (selectedEngine === 'tiktok-downloader') {
      const status = tiktokStatus();
      if (!status || !status.configured) {
        unavailable = true;
        alertCopy.textContent = T('download.alert.tiktok_unconfigured');
      } else if (!status.reachable) {
        unavailable = true;
        alertCopy.textContent = T('download.alert.tiktok_unreachable');
      }
    }
    alert.hidden = !unavailable;
  }

  function selectTab(tab) {
    selectedTab = ['favorite', 'collection'].includes(tab) ? tab : 'post';
    const switchElement = document.getElementById('tiktok-tab-switch');
    if (!switchElement) return;
    switchElement.dataset.value = selectedTab;
    switchElement.querySelectorAll('[data-tab]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.tab === selectedTab));
    });
  }

  function closeJobMenus(except = null) {
    document.querySelectorAll('.download-job-menu-panel').forEach((panel) => {
      if (panel === except) return;
      panel.hidden = true;
      panel.parentElement.querySelector('[data-action="menu"]')?.setAttribute('aria-expanded', 'false');
    });
  }

  function confirmClear({ title = T('download.confirm_clear_title'), copy = T('download.confirm_clear_copy') } = {}) {
    const mask = document.getElementById('download-confirm');
    const titleElement = document.getElementById('download-confirm-title');
    const copyElement = document.getElementById('download-confirm-copy');
    titleElement.textContent = title;
    copyElement.textContent = copy;
    mask.hidden = false;
    document.body.style.overflow = 'hidden';
    const cancel = mask.querySelector('[data-confirm-cancel]');
    const accept = mask.querySelector('[data-confirm-accept]');
    cancel.focus();
    return new Promise((resolve) => {
      const finish = (result) => {
        mask.hidden = true;
        document.body.style.overflow = '';
        cancel.removeEventListener('click', onCancel);
        accept.removeEventListener('click', onAccept);
        mask.removeEventListener('click', onMaskClick);
        mask.removeEventListener('keydown', onKeydown);
        resolve(result);
      };
      const onCancel = () => finish(false);
      const onAccept = () => finish(true);
      const onMaskClick = (event) => { if (event.target === mask) finish(false); };
      const onKeydown = (event) => { if (event.key === 'Escape') finish(false); };
      cancel.addEventListener('click', onCancel);
      accept.addEventListener('click', onAccept);
      mask.addEventListener('click', onMaskClick);
      mask.addEventListener('keydown', onKeydown);
    });
  }

  async function handleJobAction(button) {
    const action = button.dataset.action;
    const jobId = button.dataset.jobId;
    if (!action || !jobId) return;
    if (action === 'menu') {
      const panel = button.parentElement.querySelector('.download-job-menu-panel');
      const willOpen = panel.hidden;
      closeJobMenus(panel);
      panel.hidden = !willOpen;
      button.setAttribute('aria-expanded', String(willOpen));
      return;
    }
    if (action === 'delete' && !await confirmClear()) return;
    button.disabled = true;
    try {
      if (action === 'cancel') await api(`/api/download/jobs/${jobId}/cancel`, { method: 'POST' });
      if (action === 'retry') {
        await api(`/api/download/jobs/${jobId}/retry`, { method: 'POST' });
        TikLocalUI.toast(T('download.retry_submitted'));
      }
      if (action === 'delete') await api(`/api/download/jobs/${jobId}`, { method: 'DELETE' });
      lastJobsSignature = '';
      await refreshJobs({ force: true });
    } catch (error) {
      TikLocalUI.toast(error.message, 'error');
      button.disabled = false;
    }
  }

  const URL_LIST_COLLAPSED_LIMIT = 5;
  let urlListExpanded = false;

  function urlListElements() {
    return {
      section: document.getElementById('url-list-section'),
      items: document.getElementById('url-list-items'),
      empty: document.getElementById('url-list-empty'),
      count: document.getElementById('url-list-count'),
      toggle: document.getElementById('url-list-toggle'),
      toggleText: document.getElementById('url-list-toggle-text'),
      sourcePath: document.getElementById('url-list-source-path'),
      importText: document.getElementById('url-list-import-text'),
    };
  }

  function renderUrlList(data) {
    const els = urlListElements();
    if (!els.items) return;
    const lines = Array.isArray(data.lines) ? data.lines : [];
    els.count.textContent = T('download.urls.summary', { count: lines.length });
    els.empty.hidden = lines.length > 0;
    els.items.innerHTML = lines.map((url) => `
      <li class="url-list-item">
        <span class="url-list-mark">${escapeHtml(platformMark(url))}</span>
        <span class="url-list-url" title="${escapeHtml(url)}">${escapeHtml(url)}</span>
        <button class="url-list-remove" type="button" data-url-remove="${escapeHtml(url)}">${escapeHtml(T('download.urls.remove'))}</button>
      </li>
    `).join('');
    const overflow = lines.length > URL_LIST_COLLAPSED_LIMIT;
    if (!overflow) urlListExpanded = false;
    const collapsed = overflow && !urlListExpanded;
    els.items.classList.toggle('is-collapsed', collapsed);
    if (els.toggle && els.toggleText) {
      els.toggle.hidden = !overflow;
      els.toggle.setAttribute('aria-expanded', String(overflow && urlListExpanded));
      els.toggle.classList.toggle('is-expanded', overflow && urlListExpanded);
      els.toggleText.textContent = collapsed
        ? T('download.urls.expand', { count: lines.length })
        : T('download.urls.collapse');
    }
    if (els.sourcePath && document.activeElement !== els.sourcePath) {
      els.sourcePath.value = data.source_path || '';
    }
  }

  async function refreshUrlList() {
    try {
      const data = await api('/api/download/urls');
      renderUrlList(data);
      return data;
    } catch (error) {
      TikLocalUI.toast(error.message, 'error');
      return null;
    }
  }

  function urlListSourcePath() {
    const els = urlListElements();
    return els.sourcePath ? els.sourcePath.value.trim() : '';
  }

  function initUrlList() {
    const els = urlListElements();
    if (!els.section) return;

    els.toggle?.addEventListener('click', async () => {
      urlListExpanded = !urlListExpanded;
      await refreshUrlList();
    });

    els.items?.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-url-remove]');
      if (!button) return;
      button.disabled = true;
      try {
        const data = await api('/api/download/urls', {
          method: 'POST',
          body: JSON.stringify({ action: 'remove', url: button.dataset.urlRemove }),
        });
        renderUrlList(data);
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
        button.disabled = false;
      }
    });

    document.getElementById('url-list-import')?.addEventListener('click', async () => {
      const text = els.importText ? els.importText.value : '';
      if (!text.trim()) return;
      try {
        const data = await api('/api/download/urls', {
          method: 'POST',
          body: JSON.stringify({ action: 'add', text }),
        });
        renderUrlList(data);
        if (els.importText) els.importText.value = '';
        const added = Array.isArray(data.lines) ? data.lines.length : 0;
        TikLocalUI.toast(added ? T('download.urls.imported', { count: added }) : T('download.urls.imported_none'));
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
      }
    });

    document.getElementById('url-list-clear')?.addEventListener('click', async () => {
      const confirmed = await confirmClear({
        title: T('download.urls.confirm_clear_title'),
        copy: T('download.urls.confirm_clear_copy'),
      });
      if (!confirmed) return;
      try {
        const data = await api('/api/download/urls', {
          method: 'POST',
          body: JSON.stringify({ action: 'clear' }),
        });
        renderUrlList(data);
        TikLocalUI.toast(T('download.urls.cleared'));
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
      }
    });

    document.getElementById('url-list-queue')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const data = await api('/api/download/urls/queue', {
          method: 'POST',
          body: JSON.stringify({ engine: selectedEngine, save_mode: 'root' }),
        });
        const failed = Array.isArray(data.failed) ? data.failed.length : 0;
        TikLocalUI.toast(failed
          ? T('download.urls.queued_partial', { count: data.queued || 0, failed })
          : T('download.urls.queued', { count: data.queued || 0 }));
        lastJobsSignature = '';
        await refreshJobs({ force: true });
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
      } finally {
        button.disabled = false;
      }
    });

    const syncWithServer = async (direction) => {
      try {
        const data = await api('/api/download/urls/sync', {
          method: 'POST',
          body: JSON.stringify({ direction, source_path: urlListSourcePath() }),
        });
        renderUrlList(data);
        const count = Array.isArray(data.lines) ? data.lines.length : 0;
        TikLocalUI.toast(direction === 'push'
          ? T('download.urls.pushed', { count })
          : T('download.urls.pulled', { count }));
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
      }
    };

    document.getElementById('url-list-save-path')?.addEventListener('click', async () => {
      try {
        const data = await api('/api/download/urls', {
          method: 'POST',
          body: JSON.stringify({ action: 'source', source_path: urlListSourcePath() }),
        });
        renderUrlList(data);
        TikLocalUI.toast(T('download.urls.path_saved'));
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
      }
    });
    document.getElementById('url-list-pull')?.addEventListener('click', () => syncWithServer('pull'));
    document.getElementById('url-list-push')?.addEventListener('click', () => syncWithServer('push'));
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const form = document.getElementById('download-form');
    const urlInput = document.getElementById('download-url');
    const submitButton = document.getElementById('download-submit');
    urlInput?.addEventListener('input', () => {
      updateDetectedSite(urlInput);
    });
    document.getElementById('media-switch')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-engine]');
      if (button) selectEngine(button.dataset.engine);
    });
    document.getElementById('tiktok-tab-switch')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-tab]');
      if (button) selectTab(button.dataset.tab);
    });

    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const url = urlInput.value.trim();
      if (!url) return;
      if (!updateDetectedSite(urlInput)) return;
      if (selectedEngine === 'gallery-dl' && !dependencyMeta.gallery_dl_available) {
        TikLocalUI.toast(T('download.error.image_missing'), 'error');
        return;
      }
      if (selectedEngine === 'tiktok-downloader') {
        const status = tiktokStatus();
        if (!status || !status.configured) {
          TikLocalUI.toast(T('download.alert.tiktok_unconfigured'), 'error');
          return;
        }
        if (!status.reachable) {
          TikLocalUI.toast(T('download.alert.tiktok_unreachable'), 'error');
          return;
        }
      }
      submitButton.disabled = true;
      submitButton.textContent = T('download.adding');
      try {
        if (selectedEngine === 'tiktok-downloader') {
          const countSelect = document.getElementById('tiktok-count');
          const data = await api('/api/download/tiktok', {
            method: 'POST',
            body: JSON.stringify({ url, tab: selectedTab, count: Number(countSelect?.value || '18') }),
          });
          TikLocalUI.toast(T('download.tiktok_queued', { n: data.queued || 0 }));
        } else {
          const payload = { url, save_mode: 'root', engine: selectedEngine, cookie_mode: 'auto' };
          await api('/api/download/jobs', { method: 'POST', body: JSON.stringify(payload) });
          TikLocalUI.toast(T('download.added'));
        }
        urlInput.value = '';
        updateDetectedSite(urlInput);
        lastJobsSignature = '';
        await refreshJobs({ force: true });
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = T('download.submit_start');
        urlInput.focus();
      }
    });

    document.querySelector('.download-stack')?.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action]');
      if (button) handleJobAction(button);
    });
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.download-job-menu')) closeJobMenus();
    });

    document.getElementById('clear-history-btn')?.addEventListener('click', async () => {
      if (!await confirmClear({ title: T('download.confirm_clear_all_title'), copy: T('download.confirm_clear_all_copy') })) return;
      try {
        const data = await api('/api/download/jobs/clear', { method: 'POST' });
        TikLocalUI.toast(T('download.cleared', { n: data.deleted || 0 }));
        lastJobsSignature = '';
        await refreshJobs({ force: true });
      } catch (error) {
        TikLocalUI.toast(error.message, 'error');
      }
    });

    document.addEventListener('visibilitychange', () => schedulePolling(
      !document.getElementById('active-section').hidden,
    ));

    await Promise.all([refreshSetup(), refreshJobs({ force: true })]);
    initUrlList();
    await refreshUrlList();
    selectEngine(selectedEngine);
    urlInput?.focus({ preventScroll: true });
  });
})();
