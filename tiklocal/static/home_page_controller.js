(function () {
  'use strict';

  const T = (key, params) => window.TikLocalI18n?.t(key, params) ?? key;
  const dateLocale = () => (document.documentElement.lang === 'en' ? 'en' : 'zh-CN');

  function byId(id) { return document.getElementById(id); }

  function fetchJson(url) {
    const options = { headers: { Accept: 'application/json' } };
    options.signal = tikTimeoutSignal(15000);
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (payload) {
        // A 200 carrying {success: false} is still a failure. fetchJson used to look
        // at response.ok only, so a refused or half-broken request rendered as an
        // empty library and told the user to add a media source.
        if (!response.ok || (payload && payload.success === false)) {
          const detail = payload && (payload.error || payload.message);
          const text = typeof detail === 'string' ? detail : (detail && detail.message) || '';
          throw new Error(text || T('home.request_failed'));
        }
        return payload;
      });
    });
  }

  function errorText(error) {
    if (!error) return T('home.request_failed');
    if (error.name === 'TimeoutError' || error.name === 'AbortError') return T('home.error_timeout');
    if (error instanceof TypeError) return T('home.error_offline');
    return error.message || T('home.request_failed');
  }

  function apiData(payload) {
    return payload && payload.data ? payload.data : payload;
  }

  function mediaName(item) {
    var raw = String(item.name || '').split('/').pop() || T('home.untitled_media');
    return raw.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
  }

  function makeIcon(name) {
    var icon = document.createElement('i');
    icon.setAttribute('data-feather', name);
    icon.setAttribute('aria-hidden', 'true');
    return icon;
  }

  function makeMediaTile(item, extraClass) {
    var link = document.createElement('a');
    link.className = 'media-tile' + (extraClass ? ' ' + extraClass : '');
    link.href = item.detail_url || '/library';
    link.setAttribute('aria-label', T('home.view_media', { name: mediaName(item) }));

    var image = document.createElement('img');
    image.src = item.thumb_url;
    image.alt = '';
    image.loading = 'lazy';
    image.addEventListener('error', function () { image.style.opacity = '0'; });

    var kind = document.createElement('span');
    kind.className = 'media-kind';
    kind.appendChild(makeIcon(item.type === 'video' ? 'play' : 'image'));

    var copy = document.createElement('span');
    copy.className = 'media-tile-copy';
    copy.textContent = mediaName(item);

    link.appendChild(image);
    link.appendChild(kind);
    link.appendChild(copy);
    return link;
  }

  function renderFlow(items, stats) {
    var collage = byId('home-flow-collage');
    if (!collage) return;
    collage.replaceChildren();
    items.slice(0, 4).forEach(function (item) {
      var image = document.createElement('img');
      image.src = item.thumb_url;
      image.alt = '';
      image.loading = 'eager';
      collage.appendChild(image);
    });
    while (collage.children.length < 4) {
      var placeholder = document.createElement('span');
      placeholder.className = 'flow-collage-placeholder';
      collage.appendChild(placeholder);
    }
    var total = Number(stats && stats.indexed_total) || items.length;
    byId('home-flow-meta').textContent = total
      ? T('home.flow.meta_count', { n: total.toLocaleString('en') })
      : T('home.flow.meta');
  }

  function renderRibbon(containerId, items, extraClass) {
    var container = byId(containerId);
    if (!container) return;
    container.replaceChildren();
    if (!items.length) {
      var empty = document.createElement('div');
      empty.className = 'home-empty';
      var copy = document.createElement('span');
      copy.appendChild(document.createTextNode(T('home.empty.quiet')));
      copy.appendChild(document.createElement('br'));
      var source = document.createElement('a');
      source.href = '/settings/';
      source.textContent = T('home.empty.add_source');
      copy.appendChild(source);
      copy.appendChild(document.createTextNode(T('home.empty.grow')));
      empty.appendChild(copy);
      container.appendChild(empty);
      return;
    }
    items.forEach(function (item) { container.appendChild(makeMediaTile(item, extraClass)); });
  }

  // A rejected request leaves the template's skeleton tiles shimmering forever, so the
  // shelf is replaced with one honest line instead of pretending the library is empty.
  function renderShelfUnavailable(containerId) {
    var container = byId(containerId);
    if (!container) return;
    container.replaceChildren();
    var note = document.createElement('div');
    note.className = 'home-empty';
    note.appendChild(document.createTextNode(T('home.load_failed')));
    container.appendChild(note);
  }

  // Past years' media from today's date; the section stays hidden when there is nothing to remember.
  function renderMemories(data) {
    var years = (data && data.years) || [];
    if (!years.length) return;
    var date = new Date(data.date + 'T12:00:00');
    var day = date.toLocaleDateString(dateLocale(), { month: 'long', day: 'numeric' });
    var first = years[0];
    var firstDate = new Date(String(first.date || data.date) + 'T12:00:00');
    var firstAgo = years[0].years_ago;
    byId('memories-kicker').textContent = years.length === 1
      ? day + ' · ' + (firstAgo >= 1 ? agoLabel(firstAgo) : dayLabel(first.date))
      : T('home.memories.across', { date: day, n: years.length });
    byId('memories-title').textContent = data.window === 'day'
      ? T('home.memories.title_day')
      : T('home.memories.title_week');
    // The week window can reach into the neighbouring month, so both the link target
    // and its label follow the month the first memory was actually taken in.
    var month = String(first.date || data.date).slice(5, 7);
    var link = byId('memories-link');
    link.href = '/library?view=explore&month=' + first.year + '-' + month;
    link.firstChild.nodeValue = firstDate.toLocaleDateString(dateLocale(), { month: 'long' }) + ' ' + first.year + ' ';

    var container = byId('home-memories-list');
    container.replaceChildren();
    years.forEach(function (group) {
      group.items.forEach(function (item) {
        var tile = makeMediaTile(item, '');
        tile.querySelector('.media-tile-copy').textContent =
          (group.years_ago >= 1 ? agoLabel(group.years_ago) : dayLabel(group.date)) + ' · ' + group.year;
        container.appendChild(tile);
      });
    });
    byId('home-memories').hidden = false;
  }

  function dayLabel(value) {
    var date = new Date(String(value || '') + 'T12:00:00');
    if (isNaN(date.getTime())) return '';
    return date.toLocaleDateString(dateLocale(), { month: 'long', day: 'numeric' });
  }

  function agoLabel(years) {
    return years === 1 ? T('home.ago.year') : T('home.ago.years', { n: years });
  }

  function renderCollections(items) {
    var container = byId('home-collections');
    if (!container) return;
    container.replaceChildren();
    if (!items.length) {
      var empty = document.createElement('a');
      empty.className = 'collection-empty';
      empty.href = '/collections';
      empty.append(
        document.createTextNode(T('home.collections.empty')),
        document.createElement('br'),
        document.createTextNode(T('home.collections.empty_copy')),
      );
      container.appendChild(empty);
      return;
    }
    items.slice(0, 3).forEach(function (item) {
      var link = document.createElement('a');
      link.className = 'collection-card';
      link.href = item.detail_url || '/collections';

      var preview = document.createElement('span');
      preview.className = 'collection-preview';
      (item.preview_items || []).slice(0, 2).forEach(function (media) {
        var image = document.createElement('img');
        image.src = media.thumb_url;
        image.alt = '';
        image.loading = 'lazy';
        preview.appendChild(image);
      });

      var copy = document.createElement('span');
      copy.className = 'collection-copy';
      var title = document.createElement('strong');
      title.textContent = item.name || T('home.untitled_collection');
      var count = document.createElement('span');
      count.textContent = T('library.media_items', { n: Number(item.item_count) || 0 });
      copy.appendChild(title);
      copy.appendChild(count);

      link.appendChild(preview);
      link.appendChild(copy);
      link.appendChild(makeIcon('chevron-right'));
      container.appendChild(link);
    });
  }

  function renderStats(stats) {
    var total = Number(stats.indexed_total) || 0;
    var audio = Number(stats.audios) || 0;
    var summary = total
      ? T('library.media_items', { n: total.toLocaleString('en') })
      : T('home.stats.waiting');
    if (audio) summary += ' · ' + T('home.stats.audio_tracks', { n: audio.toLocaleString('en') });
    byId('home-library-summary').textContent = summary;
  }

  function hydrateRadio() {
    var stationId = localStorage.getItem('radio_station') || 'default';
    var saved = {};
    try { saved = JSON.parse(localStorage.getItem('radio_position') || '{}') || {}; } catch (_) { saved = {}; }

    fetchJson('/api/radio/stations').then(function (payload) {
      var stations = apiData(payload).stations || [];
      var station = stations.find(function (item) { return item.id === stationId; });
      if (station) byId('home-radio-station').textContent = station.name || T('home.radio.station_default');
    }).catch(function () {});

    if (!saved.name) return;
    fetchJson('/api/radio/metadata?uri=' + encodeURIComponent(saved.name)).then(function (payload) {
      var track = apiData(payload);
      byId('home-radio-title').textContent = track.title || mediaName({ name: saved.name });
      byId('home-radio-action').firstChild.nodeValue = T('home.radio.keep_listening') + ' ';
    }).catch(function () {});
  }

  function updateGreeting() {
    var hour = new Date().getHours();
    var title = T('home.greeting.default');
    if (hour >= 18 || hour < 5) title = T('home.greeting.evening');
    else if (hour < 11) title = T('home.greeting.morning');
    else if (hour < 14) title = T('home.greeting.afternoon');
    byId('home-title').textContent = title;
  }

  function renderStatsUnavailable() {
    var node = byId('home-library-summary');
    // Leaving the "loading" text would read as "still counting"; an em dash says
    // "unknown" and the banner above says why.
    if (node) node.textContent = T('home.stats.unavailable');
  }

  function showLoadError(errors) {
    var banner = byId('home-load-error');
    var message = byId('home-load-error-message');
    if (!banner || !message) return;
    var detail = errorText(errors[0]);
    message.textContent = errors.length > 1
      ? T('home.load_failed_multiple', { count: errors.length, detail: detail })
      : T('home.load_failed_detail', { detail: detail });
    banner.hidden = false;
  }

  function hideLoadError() {
    var banner = byId('home-load-error');
    if (banner) banner.hidden = true;
  }

  function bindRetry() {
    var button = byId('home-load-error-retry');
    if (!button || button.dataset.bound === '1') return;
    button.dataset.bound = '1';
    button.addEventListener('click', function () { init(); });
  }

  function init() {
    updateGreeting();
    hydrateRadio();
    bindRetry();
    hideLoadError();

    var now = new Date();
    var today = [now.getFullYear(), now.getMonth() + 1, now.getDate()].map(function (n) { return String(n).padStart(2, '0'); }).join('-');
    var requests = [
      fetchJson('/api/library/stats'),
      fetchJson('/api/library/items?scope=all&mode=all&offset=0&limit=12'),
      fetchJson('/api/feed/mix?mode=forgotten&size=8&snapshot=1'),
      fetchJson('/api/collections'),
      fetchJson('/api/library/on-this-day?date=' + today),
    ];

    // allSettled instead of four `.catch(() => empty)` fallbacks: a failed request
    // used to be indistinguishable from an empty library, so the page said
    // "add a media source" to someone with ten thousand items.
    Promise.allSettled(requests).then(function (results) {
      var failures = results.filter(function (result) {
        return result.status === 'rejected';
      }).map(function (result) { return result.reason; });

      var stats = results[0].status === 'fulfilled' ? (apiData(results[0].value) || {}) : null;
      if (stats) renderStats(stats);
      else renderStatsUnavailable();

      if (results[1].status === 'fulfilled') {
        var recent = ((apiData(results[1].value) || {}).items || []).filter(function (item) {
          return item.type === 'image' || item.type === 'video';
        });
        var rediscover = results[2].status === 'fulfilled'
          ? ((apiData(results[2].value) || {}).items || [])
          : [];
        renderFlow(recent, stats || {});
        renderRibbon('home-recent', recent.slice(0, 8), '');
        renderRibbon('home-rediscover', (rediscover.length ? rediscover : recent.slice().reverse()).slice(0, 3), 'rediscover-tile');
      } else {
        // The template ships six and three skeleton tiles; a rejected request used
        // to leave them shimmering forever under a "could not load" banner.
        renderShelfUnavailable('home-recent');
        renderShelfUnavailable('home-rediscover');
      }
      if (results[3].status === 'fulfilled') {
        renderCollections((apiData(results[3].value) || {}).items || []);
      } else {
        renderShelfUnavailable('home-collections');
      }
      if (results[4].status === 'fulfilled') {
        renderMemories(apiData(results[4].value));
      }
      if (window.feather) window.feather.replace();
      if (failures.length) showLoadError(failures);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
