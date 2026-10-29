// 会话失效的统一处理。必须比任何页面控制器先执行：控制器在 <body> 里加载并立刻发
// 首屏请求（设置页一次就是 6 个），而 base.js 在 </body> 前才执行，所以这段逻辑留在
// base.js 里时，那批请求拿到的是没有任何处理的 401——会话过期后页面不会跳登录，只会
// 显示成「没有设备 / 没有来源 / 暂不可用」的空态。写请求不受影响（用户点下去时
// base.js 早已执行），但页面看起来像数据没了，一样会误导人。
//
// 放在 <head> 里就等于把「先装包装器，再发请求」这件事变成结构保证，而不是九个
// 控制器各自要遵守的约定。
(function (global) {
  'use strict';

  var nativeFetch = global.fetch && global.fetch.bind(global);
  if (!nativeFetch) return;

  global.fetch = function (resource, options) {
    return nativeFetch(resource, options).then(function (response) {
      if (response.status === 401 && !global.__tiklocalRedirecting) {
        var target = ''.concat(global.location.pathname, global.location.search);
        if (global.location.pathname.indexOf('/login') !== 0) {
          global.__tiklocalRedirecting = true;
          // The latch exists to stop one redirect per request in a burst. It used to be
          // released only by `pageshow`, so a navigation that never happened (a pending
          // download blocking it, Esc pressed at the wrong moment) left every later 401
          // ignored and the page silently dead. Release it on a timer too.
          global.setTimeout(function () { global.__tiklocalRedirecting = false; }, 3000);
          global.location.href = '/login?next='.concat(encodeURIComponent(target));
        }
        return response;
      }

      if (response.status === 403) {
        // A second tab logged in, which rotates the session's CSRF token: this tab's
        // pages still carry the old one in their <meta>, so every write answered 403
        // forever (and even the logout form landed on the 403 page). Reload once to
        // pick up the new token; the storage latch keeps a broken token from looping.
        return response.clone().json().then(function (payload) {
          if (payload && payload.code === 'csrf') {
            var KEY = 'tiklocal:csrf-reloaded-at';
            var last = Number(global.sessionStorage.getItem(KEY) || 0);
            if (Date.now() - last > 10000) {
              global.sessionStorage.setItem(KEY, String(Date.now()));
              global.location.reload();
            }
          }
          return response;
        }, function () {
          // Not JSON (a proxied error page): leave the response to the caller.
          return response;
        });
      }

      return response;
    });
  };

  // The latch stops a burst of 401s from firing one redirect per request, but nothing
  // ever released it: when the navigation did not happen (offline, blocked, a download
  // took over), every later 401 was ignored and the page sat on empty states until a
  // manual refresh. A bfcache restore means the page is live again, so release it.
  global.addEventListener('pageshow', function () {
    global.__tiklocalRedirecting = false;
  });
})(window);
