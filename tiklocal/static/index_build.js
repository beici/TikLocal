// Take down the "building the media index" banner once the scan reports it is done.
//
// The banner is rendered by the server so a first launch on a large library says
// what is happening instead of showing an empty grid. It has to disappear without a
// reload, though, and the poll has to stop: a page that keeps asking every few
// seconds forever is exactly the kind of background chatter that is hard to notice
// later.
(function () {
  'use strict';

  var banner = document.getElementById('index-building');
  if (!banner) return;

  var INTERVAL_MS = 5000;
  var timer = null;

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function remove() {
    stop();
    banner.remove();
  }

  function check() {
    fetch('/api/library/stats', { headers: { Accept: 'application/json' } })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (payload) {
        if (!payload) return;
        // Any answer that is not "still building" ends it, including a failure to
        // read the index: leaving a permanent banner over a working library is
        // worse than dropping it.
        if (payload.index_building === false) remove();
      })
      .catch(function () {
        // The 401 guard owns navigation; a dropped request just waits for the next
        // tick instead of turning into a second error message.
      });
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop();
    else if (banner.isConnected) timer = window.setInterval(check, INTERVAL_MS);
  });

  timer = window.setInterval(check, INTERVAL_MS);
})();
