"""Receive unhandled JavaScript errors so they reach the server log.

A controller that throws leaves the page half-initialised while the server log
stays clean: the request that served the page succeeded, so every earlier bug
report could only say "the page is blank". The browser already knows the
message, the file and the stack; this endpoint puts them next to the request
that served the page.
"""
import threading
import time

from flask import request

#: One page load can throw in a loop; 5 reports per load is plenty to identify
#: the first failure and keeps a broken page from filling the log file.
MAX_REPORTS_PER_MINUTE = 20
_FIELD_LIMITS = {'message': 300, 'source': 200, 'stack': 1500, 'url': 300}


def _clean(value, limit: int) -> str:
    # Newlines would let a crafted report forge extra log lines.
    text = str(value or '').replace('\r', ' ').replace('\n', ' ')
    return text[:limit]


def register_client_log_routes(app, logger=None):
    log = logger or app.logger
    guard = threading.Lock()
    window = {'started_at': 0.0, 'count': 0, 'dropped': False}

    @app.post('/api/client-log')
    def api_client_log():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return {'success': False, 'error': 'Invalid report'}, 400
        now = time.monotonic()
        with guard:
            if now - window['started_at'] > 60:
                window['started_at'] = now
                window['count'] = 0
                window['dropped'] = False
            window['count'] += 1
            allowed = window['count'] <= MAX_REPORTS_PER_MINUTE
            announce_drop = not allowed and not window['dropped']
            if announce_drop:
                window['dropped'] = True
        if allowed:
            fields = {key: _clean(payload.get(key), limit) for key, limit in _FIELD_LIMITS.items()}
            line = payload.get('line')
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = 0
            log.warning(
                'client error: %s at %s:%s (page %s, ua %s)%s',
                fields['message'] or '(empty message)',
                fields['source'] or '?',
                line or '?',
                fields['url'] or '?',
                _clean(request.headers.get('User-Agent'), 160) or '?',
                f' | stack: {fields["stack"]}' if fields['stack'] else '',
            )
        elif announce_drop:
            log.warning(
                'client error reports from %s exceeded %d/min; further reports are dropped',
                request.remote_addr, MAX_REPORTS_PER_MINUTE,
            )
        # Always answer success: a failing reporter would retry forever.
        return {'success': True}
