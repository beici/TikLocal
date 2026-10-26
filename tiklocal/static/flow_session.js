(function (global) {
  'use strict';

  function toKey(item, keyOf) {
    try {
      var key = keyOf(item);
      return String(key || '');
    } catch (error) {
      return '';
    }
  }

  function createFlowSession(options) {
    var opts = options || {};
    var keyOf = typeof opts.keyOf === 'function'
      ? opts.keyOf
      : function (item) { return String((item && item.name) || ''); };

    var items = [];
    var keys = new Set();
    var index = Number.isInteger(opts.initialIndex) ? opts.initialIndex : -1;
    var hasMore = opts.initialHasMore !== false;
    var loading = false;
    var cursor = opts.initialCursor || null;
    // Bumped by reset(): a loader that was already in flight when the list was
    // rebuilt must not append its (now stale) page or overwrite the new cursor.
    var epoch = 0;

    function appendUnique(batch) {
      var list = Array.isArray(batch) ? batch : [];
      var appendedIndices = [];
      for (var i = 0; i < list.length; i += 1) {
        var item = list[i];
        var key = toKey(item, keyOf);
        if (!key || keys.has(key)) continue;
        keys.add(key);
        items.push(item);
        appendedIndices.push(items.length - 1);
      }
      return {
        appended: appendedIndices.length,
        appendedIndices: appendedIndices,
      };
    }

    function currentItem() {
      if (index < 0 || index >= items.length) return null;
      return items[index];
    }

    function setIndex(next) {
      if (!Number.isInteger(next)) return index;
      index = Math.max(-1, Math.min(next, items.length - 1));
      return index;
    }

    function dropHead(count) {
      var removeCount = Math.max(0, Math.min(Number(count) || 0, items.length));
      if (!removeCount) return [];
      var removed = items.splice(0, removeCount);
      for (var i = 0; i < removed.length; i += 1) {
        var key = toKey(removed[i], keyOf);
        if (key) keys.delete(key);
      }
      index = Math.max(-1, index - removeCount);
      if (items.length === 0) index = -1;
      return removed;
    }

    async function loadMore(loader) {
      if (loading || !hasMore || typeof loader !== 'function') {
        return { skipped: true, appended: 0, appendedIndices: [] };
      }

      loading = true;
      var myEpoch = epoch;
      try {
        var payload = await loader(cursor);
        if (myEpoch !== epoch) {
          return { skipped: true, stale: true, appended: 0, appendedIndices: [] };
        }
        var incoming = Array.isArray(payload && payload.items) ? payload.items : [];
        var appendResult = appendUnique(incoming);

        if (payload && Object.prototype.hasOwnProperty.call(payload, 'hasMore')) {
          hasMore = !!payload.hasMore;
        }
        if (payload && Object.prototype.hasOwnProperty.call(payload, 'cursor')) {
          cursor = payload.cursor;
        }

        return {
          skipped: false,
          payload: payload || {},
          appended: appendResult.appended,
          appendedIndices: appendResult.appendedIndices,
        };
      } finally {
        // A stale call must not clear the flag owned by the current list.
        if (myEpoch === epoch) loading = false;
      }
    }

    function reset(next) {
      var conf = next || {};
      epoch += 1;
      // Drop the in-flight flag so the next load for the rebuilt list is not
      // swallowed by a request that is about to be discarded as stale.
      loading = false;
      items.length = 0;
      keys.clear();
      index = Number.isInteger(conf.index) ? conf.index : -1;
      hasMore = conf.hasMore !== false;
      cursor = Object.prototype.hasOwnProperty.call(conf, 'cursor') ? conf.cursor : null;
    }

    appendUnique(opts.initialItems || []);
    index = Math.max(-1, Math.min(index, items.length - 1));

    return {
      items: items,
      currentItem: currentItem,
      getIndex: function () { return index; },
      setIndex: setIndex,
      hasMore: function () { return hasMore; },
      setHasMore: function (next) { hasMore = !!next; },
      isLoading: function () { return loading; },
      loadMore: loadMore,
      dropHead: dropHead,
      reset: reset,
    };
  }

  global.createFlowSession = createFlowSession;
})(window);
