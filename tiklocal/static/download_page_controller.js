(() => {
  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
  const ACTIVE_STATUSES = new Set(['queued', 'running']);
  const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']);
  let pollTimer = null;
  let lastJobsSignature = '';
  let lastHistorySignature = '';
  let lastActiveKey = '';
  let dependencyMeta = { yt_dlp_available: false, gallery_dl_available: false, ffmpeg_available: false, tiktok_downloader: null };
  let selectedEngine = 'yt-dlp';
  let selectedTab = 'post';
  // A batch delete can run for minutes (files mode moves every file to the trash).
  // `updateSelectionUI()` recomputed `disabled` from the selection size alone, so any
  // checkbox touch — or the 2.2 s history rebuild — re-enabled the button and a second
  // click posted the same ids again.
  let deleteSelectionInFlight = false;
  // job ids with an action request in flight; the row is rebuilt on every poll, so
  // the disabled state has to follow the id, not the button node.
  const jobActionBusy = new Set();
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
    // A request that never settles (server thread starved, NAS half-open) used to
    // freeze the polling chain with no message: refreshJobs awaits this, so its
    // catch/backoff never ran. A timeout turns the hang into a retryable error.
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      signal: options.signal || tikTimeoutSignal(20000),
      ...options,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok || !payload || payload.success !== true) {
      throw new Error((payload && payload.error) || T('download.request_failed', { status: response.status }));
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

  const PAGE_SIZES = [10, 20, 50, 100];
  // Lists stay un-paginated until they grow past ten rows.
  const PAGER_THRESHOLD = 10;
  const HISTORY_PAGE_KEY = 'tiklocal.download.historyPageSize';
  const URL_LIST_PAGE_KEY = 'tiklocal.download.urlListPageSize';

  let historyJobs = [];
  let historySelecting = false;
  const selectedJobIds = new Set();
  let historyPager = null;
  let urlListPager = null;
  let urlListLines = [];

  function readPageSize(key) {
    try {
      const stored = Number(window.localStorage.getItem(key));
      if (PAGE_SIZES.includes(stored)) return stored;
      // A value from an older build (or a hand edit) is not one of ours: rewrite it
      // once instead of silently discarding the user's choice on every page load.
      if (window.localStorage.getItem(key) !== null) {
        window.localStorage.setItem(key, String(PAGE_SIZES[0]));
      }
    } catch (_) {}
    return PAGE_SIZES[0];
  }

  function savePageSize(key, size) {
    try {
      window.localStorage.setItem(key, String(size));
    } catch (_) {}
  }

  function pageWindow(page, pages) {
    if (pages <= 5) return Array.from({ length: pages }, (_, index) => index + 1);
    const items = new Set([1, pages, page - 1, page, page + 1]);
    if (page <= 3) [2, 3, 4].forEach((value) => items.add(value));
    if (page >= pages - 2) [pages - 1, pages - 2, pages - 3].forEach((value) => items.add(value));
    const sorted = [...items].filter((value) => value >= 1 && value <= pages).sort((a, b) => a - b);
    const output = [];
    sorted.forEach((value, index) => {
      if (index > 0 && value - sorted[index - 1] > 1) output.push('…');
      output.push(value);
    });
    return output;
  }

  // One pager instance drives one list: it owns the page size, the current page
  // and the bar markup, and asks the owner to re-render through `onChange`.
  function createPager(host, { storageKey, onChange }) {
    const state = { page: 1, pageSize: readPageSize(storageKey), total: 0 };
    const totalPages = () => Math.max(1, Math.ceil(state.total / state.pageSize));
    let renderedKey = '';
    const stateKey = () => `${state.total}:${state.page}:${state.pageSize}`;

    function render() {
      const pages = totalPages();
      renderedKey = stateKey();
      host.hidden = state.total <= PAGER_THRESHOLD;
      if (host.hidden) {
        host.innerHTML = '';
        return;
      }
      host.innerHTML = `
        <div class="pager-bar">
          <label class="pager-size">
            <span class="pager-size-label">${escapeHtml(T('pager.page_size'))}</span>
            <span class="pager-size-field">
              <select class="pager-size-select" aria-label="${escapeHtml(T('pager.page_size'))}">
                ${PAGE_SIZES.map((size) => `<option value="${size}"${size === state.pageSize ? ' selected' : ''}>${escapeHtml(T('pager.size_option', { size }))}</option>`).join('')}
              </select>
              <i data-feather="chevron-down"></i>
            </span>
          </label>
          <div class="pager-nav">
            <button class="pager-btn" type="button" data-pager-step="-1" aria-label="${escapeHtml(T('pager.prev'))}"${state.page <= 1 ? ' disabled' : ''}><i data-feather="chevron-left"></i></button>
            <div class="pager-pages">
              ${pageWindow(state.page, pages).map((item) => (item === '…'
                ? '<span class="pager-ellipsis" aria-hidden="true">…</span>'
                : `<button class="pager-page${item === state.page ? ' is-current' : ''}" type="button" data-pager-page="${item}" aria-label="${escapeHtml(T('pager.page_aria', { page: item }))}"${item === state.page ? ' aria-current="page"' : ''}>${item}</button>`)).join('')}
            </div>
            <button class="pager-btn" type="button" data-pager-step="1" aria-label="${escapeHtml(T('pager.next'))}"${state.page >= pages ? ' disabled' : ''}><i data-feather="chevron-right"></i></button>
          </div>
          <div class="pager-jump">
            <label class="pager-jump-label" for="${host.id}-jump">${escapeHtml(T('pager.jump'))}</label>
            <input id="${host.id}-jump" class="pager-jump-input" type="text" inputmode="numeric" autocomplete="off" spellcheck="false" value="${state.page}" aria-label="${escapeHtml(T('pager.jump'))}">
            <span class="pager-jump-suffix">${escapeHtml(T('pager.jump_suffix'))}</span>
          </div>
          <span class="pager-summary">${escapeHtml(T('pager.summary', { page: state.page, pages, total: state.total }))}</span>
        </div>`;
      window.feather?.replace();
    }

    function goToPage(next) {
      const pages = totalPages();
      const clamped = Math.max(1, Math.min(pages, Math.trunc(next) || 1));
      if (clamped === state.page) {
        render();
        return;
      }
      state.page = clamped;
      render();
      onChange(state);
    }

    host.addEventListener('click', (event) => {
      const stepButton = event.target.closest('[data-pager-step]');
      if (stepButton) {
        goToPage(state.page + Number(stepButton.dataset.pagerStep));
        return;
      }
      const pageButton = event.target.closest('[data-pager-page]');
      if (pageButton) goToPage(Number(pageButton.dataset.pagerPage));
    });
    host.addEventListener('change', (event) => {
      const select = event.target.closest('.pager-size-select');
      if (!select) return;
      const size = Number(select.value);
      if (!PAGE_SIZES.includes(size) || size === state.pageSize) return;
      state.pageSize = size;
      state.page = 1;
      savePageSize(storageKey, size);
      render();
      onChange(state);
    });
    host.addEventListener('keydown', (event) => {
      const input = event.target.closest('.pager-jump-input');
      if (!input || event.key !== 'Enter') return;
      event.preventDefault();
      const value = Number(input.value);
      if (Number.isFinite(value) && value >= 1) goToPage(value);
      else render();
    });

    return {
      getState: () => ({ ...state }),
      update(total) {
        state.total = Math.max(0, Math.trunc(Number(total) || 0));
        const pages = totalPages();
        if (state.page > pages) state.page = pages;
        if (state.page < 1) state.page = 1;
        if (stateKey() !== renderedKey) render();
      },
    };
  }

  function historyPagerInstance() {
    if (!historyPager) {
      const host = document.getElementById('history-pager');
      if (host) {
        historyPager = createPager(host, {
          storageKey: HISTORY_PAGE_KEY,
          onChange: () => renderHistoryPage(),
        });
      }
    }
    return historyPager;
  }

  function urlListPagerInstance() {
    if (!urlListPager) {
      const host = document.getElementById('url-list-pager');
      if (host) {
        urlListPager = createPager(host, {
          storageKey: URL_LIST_PAGE_KEY,
          onChange: () => renderUrlListPage(),
        });
      }
    }
    return urlListPager;
  }

  function jobFileCount(job) {
    const files = outputFiles(job);
    return Math.max(files.length, Number(job.file_count) || 0);
  }

  function renderJob(job, { selectable = false } = {}) {
    const files = outputFiles(job);
    const firstFile = files[0] || '';
    const isActive = ACTIVE_STATUSES.has(job.status);
    const selected = selectable && selectedJobIds.has(job.id);
    const checkbox = selectable ? `
        <label class="download-job-check">
          <input type="checkbox" data-job-select="${escapeHtml(job.id)}"${selected ? ' checked' : ''} aria-label="${escapeHtml(T('download.select.row_aria', { name: safeHostname(job.url) }))}">
          <span class="download-job-check-box" aria-hidden="true"><i data-feather="check"></i></span>
        </label>` : '';
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

    const retryButton = `<button class="download-action is-retry" type="button" data-action="retry" data-job-id="${escapeHtml(job.id)}"><i data-feather="rotate-ccw"></i><span>${escapeHtml(job.failure_stage === 'index' ? T('download.retry_register') : T('download.retry_download'))}</span></button>`;
    let actions = '';
    // A job that already produced a follow-up retry keeps its "failed" status, so the
    // server records the link in retried_by: rendering the button again let the user
    // queue the same download a second time.
    const canRetry = (job.status === 'failed' || job.status === 'canceled') && !job.retried_by;
    if (isActive) {
      actions = `${sourceAction}<button class="download-action" type="button" data-action="cancel" data-job-id="${escapeHtml(job.id)}">${escapeHtml(T('common.cancel'))}</button>`;
    } else if (firstFile) {
      // A failed playlist may still have saved files (partial=True): those files
      // are already in the library, so the card must keep a way to open them
      // instead of only offering a retry that would download them again.
      const retry = canRetry ? retryButton : '';
      actions = `
        <a class="download-action" href="${buildMediaHref(firstFile)}"><i data-feather="eye"></i><span>${escapeHtml(T('download.view_media'))}</span></a>
        ${retry}${sourceAction}${menu}`;
    } else {
      actions = `
        ${canRetry ? retryButton : ''}
        ${sourceAction}${menu}`;
    }

    // yt-dlp's own failure text runs to hundreds of characters (issue template and
    // all); collapsed behind a summary it no longer pushes the retry button off a
    // phone screen.
    const errorText = job.error_message ? String(job.error_message) : '';
    const errorBlock = errorText
      ? (errorText.length > 120
        ? `<details class="download-error-details"><summary>${escapeHtml(T('download.error_details'))}</summary><p class="download-error">${escapeHtml(errorText)}</p></details>`
        : `<p class="download-error">${escapeHtml(errorText)}</p>`)
      : '';
    const savedFiles = Number(job.file_count) || 0;
    const fileMeta = files.length > 1
      ? T('download.items_count', { n: files.length })
      : (savedFiles > 0 ? T('download.job.saved_files', { n: savedFiles }) : '');

    return `
      <article class="download-job ${className}${selected ? ' is-selected' : ''}${selectable ? ' is-selectable' : ''}" data-job-id="${escapeHtml(job.id)}">
        ${checkbox}
        <div class="download-job-mark" aria-hidden="true">${escapeHtml(platformMark(job.url))}</div>
        <div class="download-job-main">
          <div class="download-job-top">
            <div class="download-job-identity"><span class="download-job-domain">${escapeHtml(safeHostname(job.url))}</span><span class="download-job-status">${escapeHtml(engineLabel(job.engine))}</span></div>
            ${time ? `<span class="download-job-time">${escapeHtml(time)}</span>` : ''}
          </div>
          <p class="download-job-copy">${escapeHtml(statusCopy(job))}</p>
          ${progress !== null && isActive ? `<div class="download-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.toFixed(0)}"><div class="download-progress-bar" style="width:${progress.toFixed(1)}%"></div></div>` : ''}
          ${fileMeta ? `<div class="download-job-meta"><span>${escapeHtml(fileMeta)}</span></div>` : ''}
          ${errorBlock}
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

  // The history rows never show progress, so a running job must not be allowed to
  // change this key: rebuilding the list on every 2.2 s poll destroyed the focused
  // row, collapsed an expanded error <details>, and made the aria-live region
  // announce the whole history again on each tick.
  function historySignature(jobs) {
    return jobs.map((job) => [
      job.id, job.status, job.finished_at, job.file_count, job.error_message,
    ].join(':')).join('|');
  }

  // The active list sits inside an aria-live region and is polled every 2.2 s while
  // something runs. Replacing its markup on each tick destroyed the focused "Cancel"
  // button — a keyboard user could never cancel a running job — and made the screen
  // reader announce every row again. Only a structural change rebuilds it; progress
  // is written into the nodes that are already there.
  function activeKey(jobs) {
    return jobs.map((job) => [
      job.id, job.status, job.engine, job.url, job.file_count,
    ].join(':')).join('|');
  }

  function updateActiveNode(node, job) {
    const copy = node.querySelector('.download-job-copy');
    if (copy) copy.textContent = statusCopy(job);
    const bar = node.querySelector('.download-progress');
    if (!bar) return;
    const percent = Math.max(0, Math.min(100, Number(job.progress_percent) || 0));
    bar.setAttribute('aria-valuenow', percent.toFixed(0));
    const fill = bar.querySelector('.download-progress-bar');
    if (fill) fill.style.width = `${percent.toFixed(1)}%`;
  }

  function syncActiveList(container, activeJobs, force) {
    const key = activeKey(activeJobs);
    if (!force && key === lastActiveKey) {
      [...container.querySelectorAll('article[data-job-id]')].forEach((node) => {
        const job = activeJobs.find((entry) => String(entry.id) === node.dataset.jobId);
        if (job) updateActiveNode(node, job);
      });
      return;
    }
    lastActiveKey = key;
    container.innerHTML = activeJobs.map((job) => renderJob(job)).join('');
  }

  function renderJobs(jobs, { force = false } = {}) {
    const signature = jobsSignature(jobs);
    const hasActiveJobs = jobs.some((job) => ACTIVE_STATUSES.has(job.status));
    historyLoaded = true;
    if (!force && signature === lastJobsSignature) {
      // 载荷没变也要续期：否则排队中/0% 阶段连续两轮相同就会让轮询链永久中断。
      schedulePolling(hasActiveJobs);
      return;
    }
    lastJobsSignature = signature;

    const activeJobs = jobs.filter((job) => ACTIVE_STATUSES.has(job.status));
    historyJobs = jobs.filter((job) => !ACTIVE_STATUSES.has(job.status));
    const historyKey = historySignature(historyJobs);
    const historyChanged = force || historyKey !== lastHistorySignature;
    lastHistorySignature = historyKey;
    const activeSection = document.getElementById('active-section');
    const activeList = document.getElementById('active-list');

    activeSection.hidden = activeJobs.length === 0;
    document.getElementById('active-count').textContent = activeJobs.length ? T('download.items_count', { n: activeJobs.length }) : '';
    syncActiveList(activeList, activeJobs, force);
    document.getElementById('history-count').textContent = historyJobs.length ? T('download.items_count', { n: historyJobs.length }) : '';

    const knownIds = new Set(historyJobs.map((job) => job.id));
    [...selectedJobIds].forEach((jobId) => {
      if (!knownIds.has(jobId)) selectedJobIds.delete(jobId);
    });
    if (historySelecting && historyJobs.length === 0) exitHistorySelection();
    if (historyChanged) renderHistoryPage();
    schedulePolling(activeJobs.length > 0);
  }

  function visibleHistoryJobs() {
    const pager = historyPagerInstance();
    const { page = 1, pageSize = PAGE_SIZES[0] } = pager ? pager.getState() : {};
    const start = (page - 1) * pageSize;
    return historyJobs.slice(start, start + pageSize);
  }

  function renderHistoryPage() {
    const list = document.getElementById('history-list');
    if (!list) return;
    const pager = historyPagerInstance();
    if (pager) pager.update(historyJobs.length);
    const visible = visibleHistoryJobs();
    list.classList.toggle('is-selecting', historySelecting);
    list.innerHTML = historyJobs.length
      ? visible.map((job) => renderJob(job, { selectable: historySelecting })).join('')
      : emptyHistory();
    const footer = document.getElementById('history-footer');
    if (footer) footer.hidden = historyJobs.length === 0 || historySelecting;
    const bar = document.getElementById('history-select-bar');
    if (bar) bar.hidden = !historySelecting;
    if (jobActionBusy.size) {
      // The row was rebuilt while its request was still running: keep the buttons of
      // those jobs disabled so the same action cannot be posted twice.
      list.querySelectorAll('[data-job-id]').forEach((item) => {
        if (jobActionBusy.has(item.dataset.jobId)) item.disabled = true;
      });
    }
    updateSelectionUI();
    window.feather?.replace();
  }

  function updateSelectionUI() {
    const count = selectedJobIds.size;
    const countElement = document.getElementById('history-select-count');
    if (countElement) {
      countElement.textContent = count
        ? T('download.select.selected', { n: count })
        : T('download.select.hint');
      countElement.classList.toggle('is-empty', count === 0);
    }
    const visibleIds = visibleHistoryJobs().map((job) => job.id);
    const pageSelected = visibleIds.length > 0 && visibleIds.every((jobId) => selectedJobIds.has(jobId));
    const allIds = historyJobs.map((job) => job.id);
    const allSelected = allIds.length > 0 && allIds.every((jobId) => selectedJobIds.has(jobId));
    const allButton = document.getElementById('history-select-all');
    if (allButton) {
      // Three steps: pick the current page, then everything, then clear.
      let label = T('download.select.select_all');
      if (allSelected) label = T('download.select.clear_all');
      else if (pageSelected) label = T('download.select.select_all_total', { total: historyJobs.length });
      allButton.textContent = label;
      allButton.setAttribute('aria-pressed', String(pageSelected || allSelected));
    }
    const deleteButton = document.getElementById('history-select-delete');
    if (deleteButton) deleteButton.disabled = count === 0 || deleteSelectionInFlight;
  }

  function enterHistorySelection() {
    if (!historyJobs.length) return;
    historySelecting = true;
    selectedJobIds.clear();
    document.body.classList.add('is-history-selecting');
    renderHistoryPage();
  }

  function exitHistorySelection() {
    historySelecting = false;
    selectedJobIds.clear();
    document.body.classList.remove('is-history-selecting');
    renderHistoryPage();
  }

  function toggleJobSelection(jobId, checked) {
    if (!jobId) return;
    if (checked) selectedJobIds.add(jobId);
    else selectedJobIds.delete(jobId);
    const row = document.querySelector(`.download-job[data-job-id="${CSS.escape(jobId)}"]`);
    if (row) {
      row.classList.toggle('is-selected', checked);
      const input = row.querySelector('[data-job-select]');
      if (input) input.checked = checked;
    }
    updateSelectionUI();
  }

  function chooseHistoryDeleteMode(fileCount) {
    const sheet = document.getElementById('history-delete-sheet');
    if (!sheet) return Promise.resolve(null);
    const copy = document.getElementById('history-sheet-copy');
    const filesCopy = document.getElementById('history-sheet-files-copy');
    if (copy) copy.textContent = T('download.select.delete_copy', { n: selectedJobIds.size });
    if (filesCopy) {
      filesCopy.textContent = fileCount
        ? T('download.select.mode_files_copy', { n: fileCount })
        : T('download.select.mode_files_none');
    }
    const modes = [...sheet.querySelectorAll('[data-delete-mode]')];
    modes.forEach((button) => {
      button.disabled = button.dataset.deleteMode === 'files' && fileCount === 0;
    });
    sheet.hidden = false;
    document.body.style.overflow = 'hidden';
    const previousFocus = document.activeElement;
    const cancel = sheet.querySelector('[data-sheet-cancel]');
    cancel?.focus();
    return new Promise((resolve) => {
      const finish = (result) => {
        sheet.hidden = true;
        document.body.style.overflow = '';
        modes.forEach((button) => button.removeEventListener('click', onMode));
        cancel?.removeEventListener('click', onCancel);
        sheet.removeEventListener('click', onMaskClick);
        sheet.removeEventListener('keydown', onKeydown);
        // aria-modal="true" means focus must come back to the trigger, not to body.
        if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
        resolve(result);
      };
      const onMode = (event) => finish(event.currentTarget.dataset.deleteMode);
      const onCancel = () => finish(null);
      const onMaskClick = (event) => {
        if (event.target === sheet) finish(null);
      };
      const onKeydown = (event) => {
        if (event.key === 'Escape') {
          // Keep the document-level Esc handler from also clearing the selection.
          event.stopPropagation();
          finish(null);
          return;
        }
        if (event.key !== 'Tab') return;
        // The sheet is modal: Tab has to cycle inside it instead of walking into
        // the grid behind the mask.
        const focusable = [...sheet.querySelectorAll('button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
          .filter((element) => element.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        // Clicking the sheet's own text (h2/p) blurs to <body>, which is neither
        // first nor last, so the two branches below used to let Tab escape the
        // modal. Pull focus back into the sheet whenever it is outside.
        if (!focusable.includes(document.activeElement)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      };
      modes.forEach((button) => button.addEventListener('click', onMode));
      cancel?.addEventListener('click', onCancel);
      sheet.addEventListener('click', onMaskClick);
      sheet.addEventListener('keydown', onKeydown);
    });
  }

  async function deleteSelectedJobs() {
    if (deleteSelectionInFlight) return;
    if (!selectedJobIds.size) {
      TikLocalUI.toast(T('download.select.none'), 'error');
      return;
    }
    const selected = historyJobs.filter((job) => selectedJobIds.has(job.id));
    const fileCount = selected.reduce((sum, job) => sum + jobFileCount(job), 0);
    const mode = await chooseHistoryDeleteMode(fileCount);
    if (!mode) return;

    const ids = selected.map((job) => job.id);
    const deleteButton = document.getElementById('history-select-delete');
    deleteSelectionInFlight = true;
    if (deleteButton) deleteButton.disabled = true;
    try {
      const data = await api('/api/download/jobs/delete', {
        method: 'POST',
        body: JSON.stringify({ ids, delete_files: mode === 'files' }),
        // With delete_files the server moves every file to the trash first; a
        // selection of large videos outlives the default 20 s, and aborting would
        // report failure after the files were already gone.
        signal: tikTimeoutSignal(120000),
      });
      const deleted = data.deleted || 0;
      const moved = (data.files && data.files.moved) || 0;
      TikLocalUI.toast(mode === 'files'
        ? T('download.select.done_files', { n: deleted, m: moved })
        : T('download.select.done', { n: deleted }));
      const skipped = (data.skipped || []).length;
      if (skipped) TikLocalUI.toast(T('download.select.skipped', { n: skipped }), 'error');
      const failedFiles = (data.files && data.files.failed ? data.files.failed.length : 0);
      if (failedFiles) TikLocalUI.toast(T('download.select.failed_files', { n: failedFiles }), 'error');
      exitHistorySelection();
      lastJobsSignature = '';
      await refreshJobs({ force: true });
    } catch (error) {
      TikLocalUI.toast(error.message, 'error');
    } finally {
      deleteSelectionInFlight = false;
      // Re-read the button: the list may have been rebuilt while the request was in
      // flight, so the captured node can already be detached.
      updateSelectionUI();
    }
  }

  let pollBackoffMs = 0;
  // The template ships a "loading" skeleton inside #history-list. Only a successful
  // render replaces it, so a first request that fails (server still starting, offline,
  // 500) left the page saying "loading" forever while the toast faded away.
  let historyLoaded = false;

  function renderHistoryError() {
    const list = document.getElementById('history-list');
    if (!list) return;
    list.innerHTML = `
      <div class="download-empty">
        <div class="download-empty-icon"><i data-feather="alert-circle"></i></div>
        <div class="download-empty-title">${escapeHtml(T('download.history_load_failed'))}</div>
        <div class="download-empty-copy">
          <button type="button" class="download-empty-retry" id="history-retry">${escapeHtml(T('common.retry'))}</button>
        </div>
      </div>`;
    const retry = document.getElementById('history-retry');
    if (retry) retry.addEventListener('click', () => refreshJobs({ force: true }));
    if (window.feather) window.feather.replace();
  }

  async function refreshJobs({ force = false } = {}) {
    try {
      const data = await api('/api/download/jobs?limit=80');
      const jobs = data.jobs || [];
      pollBackoffMs = 0;
      renderJobs(jobs, { force });
    } catch (error) {
      // 断网时后台轮询每 12 秒弹一条英文红条，还会把用户刚触发的提示挤掉；
      // 离线只静默等待恢复，非离线失败才退避提示。
      if (!historyLoaded) renderHistoryError();
      if (navigator.onLine === false) {
        schedulePolling(false);
        return;
      }
      pollBackoffMs = pollBackoffMs ? Math.min(pollBackoffMs * 2, 30000) : 2000;
      TikLocalUI.toast(error.message, 'error');
      schedulePolling(false, pollBackoffMs);
    }
  }

  function schedulePolling(hasActiveJobs, overrideDelay) {
    if (pollTimer) window.clearTimeout(pollTimer);
    const delay = overrideDelay || (document.hidden ? 15000 : (hasActiveJobs ? 2200 : 12000));
    pollTimer = window.setTimeout(() => refreshJobs(), delay);
  }

  window.addEventListener('tiklocal:online', () => {
    pollBackoffMs = 0;
    refreshJobs({ force: true });
  });

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

  function selectEngine(engine, { retry = true } = {}) {
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
    if (unavailable && retry && selectedEngine === 'gallery-dl') {
      // 首屏 probe 失败过一次就永远判定「组件未就绪」，选到图片引擎时补一次探测。
      refreshSetup().then(() => selectEngine('gallery-dl', { retry: false }));
    }
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
      // Keep the document-level Escape handler from also leaving multi-select: the
      // user asked to close this dialog, not to drop the rows they had ticked.
      const onKeydown = (event) => {
        if (event.key !== 'Escape') return;
        event.stopPropagation();
        finish(false);
      };
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
    // The row this button belongs to can be replaced while a request is in flight
    // (the history list is rebuilt on every poll), so the guard lives on the job id
    // rather than on the element: a detached button must not re-enable the action.
    if (jobActionBusy.has(jobId)) return;
    jobActionBusy.add(jobId);
    button.disabled = true;
    try {
      if (action === 'delete' && !await confirmClear({
        title: T('download.remove_record'),
        copy: T('download.confirm_clear_copy'),
      })) return;
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
    } finally {
      jobActionBusy.delete(jobId);
    }
  }

  function urlListElements() {
    return {
      section: document.getElementById('url-list-section'),
      items: document.getElementById('url-list-items'),
      empty: document.getElementById('url-list-empty'),
      count: document.getElementById('url-list-count'),
      sourcePath: document.getElementById('url-list-source-path'),
      importText: document.getElementById('url-list-import-text'),
    };
  }

  function renderUrlListPage() {
    const els = urlListElements();
    if (!els.items) return;
    const pager = urlListPagerInstance();
    if (pager) pager.update(urlListLines.length);
    const { page = 1, pageSize = PAGE_SIZES[0] } = pager ? pager.getState() : {};
    const start = (page - 1) * pageSize;
    const visible = urlListLines.slice(start, start + pageSize);
    els.items.innerHTML = visible.map((url) => `
      <li class="url-list-item">
        <span class="url-list-mark">${escapeHtml(platformMark(url))}</span>
        <span class="url-list-url" title="${escapeHtml(url)}">${escapeHtml(url)}</span>
        <button class="url-list-remove" type="button" data-url-remove="${escapeHtml(url)}">${escapeHtml(T('download.urls.remove'))}</button>
      </li>
    `).join('');
    window.feather?.replace();
  }

  function renderUrlList(data) {
    const els = urlListElements();
    if (!els.items) return;
    urlListLines = Array.isArray(data.lines) ? data.lines : [];
    els.count.textContent = T('download.urls.summary', { count: urlListLines.length });
    els.empty.hidden = urlListLines.length > 0;
    if (els.sourcePath && document.activeElement !== els.sourcePath) {
      els.sourcePath.value = data.source_path || '';
    }
    renderUrlListPage();
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
        // Report what this import actually added: the snapshot's line count is the
        // whole list, so it said "imported 8" for 5 new lines plus 3 dropped ones.
        const added = Number(data.added);
        const rejected = Number(data.rejected);
        const truncated = Number(data.truncated);
        if (Number.isFinite(added) && added >= 0) {
          const parts = [T('download.urls.imported', { count: added })];
          if (rejected > 0) parts.push(T('download.urls.import_rejected', { count: rejected }));
          if (truncated > 0) parts.push(T('download.urls.import_truncated', { count: truncated }));
          TikLocalUI.toast(parts.join(' '), rejected || truncated ? 'warning' : 'success');
        } else {
          const count = Array.isArray(data.lines) ? data.lines.length : 0;
          TikLocalUI.toast(count ? T('download.urls.imported', { count }) : T('download.urls.imported_none'));
        }
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
      if (selectedEngine === 'tiktok-downloader') {
        // 队列接口只创建不带 payload 的任务，博主引擎走这里必然全失败。
        TikLocalUI.toast(T('download.error.tiktok_queue_unsupported'), 'error');
        return;
      }
      if (selectedEngine === 'gallery-dl' && !dependencyMeta.gallery_dl_available) {
        TikLocalUI.toast(T('download.error.image_missing'), 'error');
        return;
      }
      button.disabled = true;
      try {
        const data = await api('/api/download/urls/queue', {
          method: 'POST',
          body: JSON.stringify({ engine: selectedEngine, save_mode: 'root' }),
          // Queueing writes the whole job list to disk once per URL; a few hundred
          // URLs outlive the default 20 s, and aborting would report failure while
          // the server kept creating jobs.
          signal: tikTimeoutSignal(180000),
        });
        // `failed`/`skipped` are lists in the payload: `Number(list)` is NaN, so the
        // "already queued" branch could never fire.
        const failedItems = Array.isArray(data.failed) ? data.failed : [];
        const skippedItems = Array.isArray(data.skipped) ? data.skipped : [];
        const queuedItems = Array.isArray(data.items) ? data.items : [];
        const failed = failedItems.length;
        const queued = Number(data.queued) || 0;
        const skipped = skippedItems.length;
        // Only the URLs this page turned into jobs may leave the list: `clear` wiped
        // links another tab or a phone had imported a second earlier, and
        // `replace(failed)` overwrote the whole table with this page's leftovers.
        const consumedUrls = queuedItems
          .concat(skippedItems)
          .map((item) => String((item && item.url) || ''))
          .filter(Boolean);
        const dropConsumed = async () => {
          if (!consumedUrls.length) return;
          try {
            renderUrlList(await api('/api/download/urls', {
              method: 'POST',
              body: JSON.stringify({ action: 'remove', lines: consumedUrls }),
            }));
          } catch (error) {
            // Keep the list as it is; the next queue call still dedupes on the server.
          }
        };
        if (!queued && !failed && skipped) {
          // Every link was already queued (a second click, or a list that was not
          // cleared): reporting "the list is empty" over a full list left the user
          // with nothing to do.
          TikLocalUI.toast(T('download.urls.already_queued'));
          await dropConsumed();
          return;
        }
        if (!queued && !failed) {
          // An empty list answered with success ("queued 0 jobs"), which reads as
          // if the action worked.
          TikLocalUI.toast(T('download.urls.queue_empty'), 'error');
          return;
        }
        TikLocalUI.toast(failed
          ? T('download.urls.queued_partial', { count: queued, failed })
          : T('download.urls.queued', { count: queued }));
        // Successes leave the list whether or not the batch was partial; the failures
        // stay so the user can retry them.
        await dropConsumed();
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
          const queuedCount = Number(data.queued) || 0;
          const failedCount = Number(data.failed) || 0;
          if (failedCount) {
            // The route answers 200 even when every work item was rejected, so a
            // green "queued 0" was the only thing the user saw.
            TikLocalUI.toast(
              T('download.urls.queued_partial', { count: queuedCount, failed: failedCount }),
              'error',
            );
          } else {
            TikLocalUI.toast(T('download.tiktok_queued', { n: queuedCount }));
          }
          if (data.truncated) {
            TikLocalUI.toast(T('download.tiktok_truncated'), 'error');
          }
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

    document.getElementById('clear-history-btn')?.addEventListener('click', () => enterHistorySelection());
    document.getElementById('history-select-all')?.addEventListener('click', () => {
      const visibleIds = visibleHistoryJobs().map((job) => job.id);
      const pageSelected = visibleIds.length > 0 && visibleIds.every((jobId) => selectedJobIds.has(jobId));
      const allIds = historyJobs.map((job) => job.id);
      const allSelected = allIds.length > 0 && allIds.every((jobId) => selectedJobIds.has(jobId));
      if (allSelected) {
        selectedJobIds.clear();
      } else if (pageSelected) {
        allIds.forEach((jobId) => selectedJobIds.add(jobId));
      } else {
        visibleIds.forEach((jobId) => selectedJobIds.add(jobId));
      }
      renderHistoryPage();
    });
    document.getElementById('history-select-cancel')?.addEventListener('click', () => exitHistorySelection());
    document.getElementById('history-select-delete')?.addEventListener('click', () => deleteSelectedJobs());

    const historyList = document.getElementById('history-list');
    historyList?.addEventListener('change', (event) => {
      const input = event.target.closest('[data-job-select]');
      if (!input) return;
      toggleJobSelection(input.dataset.jobSelect, input.checked);
    });
    historyList?.addEventListener('click', (event) => {
      if (!historySelecting) return;
      if (event.target.closest('a, button, input, label')) return;
      const row = event.target.closest('.download-job[data-job-id]');
      if (!row) return;
      toggleJobSelection(row.dataset.jobId, !selectedJobIds.has(row.dataset.jobId));
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && historySelecting) exitHistorySelection();
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
