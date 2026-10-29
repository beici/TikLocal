import datetime
import logging
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from flask import make_response, redirect, render_template, request, session, url_for

from tiklocal.i18n import t


UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

# Access control used to be the one subsystem with no log lines at all: a user
# locked out by the rate limiter, a stale CSRF token and a password typo all
# looked identical from the outside, and an exposed instance had no login audit.
_LOGGER = logging.getLogger(__name__)


class LoginAttemptLimiter:
    # An exposed instance sees one key per source address, so the table needs a
    # ceiling: it used to grow by one entry for every address that ever failed.
    MAX_KEYS = 1024

    def __init__(self, limit: int = 5, window_seconds: int = 300):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        with self._lock:
            # A read must not create a bucket: retry_after() runs on every login
            # attempt, including the unauthenticated pairing endpoints.
            attempts = self._active(key, create=False)
            if len(attempts) < self.limit:
                return 0
            return max(1, int(self.window_seconds - (time.monotonic() - attempts[0])))

    def record_failure(self, key: str) -> None:
        with self._lock:
            if key not in self._attempts and not self._prune():
                # Every bucket is still blocking an address inside the window.
                # Evicting one would hand that attacker a clean slate, so the
                # newcomer goes unrecorded instead: it costs one extra 401, not a
                # reset of a block that already exists.
                return
            self._active(key).append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def _prune(self) -> bool:
        """Drop buckets whose last failure left the window; report room for one more."""
        if len(self._attempts) < self.MAX_KEYS:
            return True
        cutoff = time.monotonic() - self.window_seconds
        for key in [name for name, attempts in self._attempts.items() if not attempts or attempts[-1] < cutoff]:
            del self._attempts[key]
        return len(self._attempts) < self.MAX_KEYS

    def _active(self, key: str, *, create: bool = True) -> deque[float]:
        attempts = self._attempts.get(key)
        if attempts is None:
            if not create:
                return deque()
            attempts = self._attempts[key] = deque()
        cutoff = time.monotonic() - self.window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts


def configure_auth(app, auth_store, *, enabled: bool, device_auth_store=None) -> None:
    app.extensions['auth_store'] = auth_store
    app.extensions['auth_enabled'] = enabled
    limiter = LoginAttemptLimiter()
    # Shared with the radio pairing endpoint: both validate the same access
    # password, so one bucket per process keeps the 5-attempt budget honest.
    app.extensions['login_limiter'] = limiter

    if not enabled:
        @app.context_processor
        def auth_disabled_context():
            return {'auth_enabled': False, 'csrf_token': lambda: ''}
        return

    app.secret_key = auth_store.secret_key
    app.config.update(
        SESSION_COOKIE_NAME='tiklocal_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=bool(app.config.get('AUTH_COOKIE_SECURE', False)),
        PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
    )

    def ensure_csrf_token() -> str:
        token = session.get('_csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['_csrf_token'] = token
        return str(token)

    def is_authenticated() -> bool:
        if session.get('authenticated') is not True:
            return False
        try:
            revision = auth_store.revision
        except RuntimeError:
            # auth.json is unreadable: locked by a virus scanner, half-written by a
            # crash, or a permissions mistake. Reading the revision used to raise on
            # every route, so the whole instance answered 500 and the login page was
            # unreachable too. Treating the session as signed out sends the user to a
            # page that explains the problem instead.
            _LOGGER.error('authentication store is unreadable; treating the session as signed out')
            return False
        return int(session.get('auth_revision') or 0) == revision

    def safe_next(value: str | None) -> str:
        candidate = str(value or '').strip()
        # Browsers read a backslash as a slash, so "/\evil.com" leaves the site
        # (it becomes "//evil.com") even though the value starts with "/".
        normalized = candidate.replace('\\', '/')
        if any(ch in candidate for ch in '\r\n\t') or normalized.startswith('//'):
            return '/'
        parsed = urlsplit(normalized)
        if not normalized.startswith('/') or parsed.scheme or parsed.netloc:
            return '/'
        return normalized

    def csrf_value_from_request() -> str:
        value = request.headers.get('X-CSRF-Token') or request.form.get('_csrf_token')
        if value:
            return str(value)
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            return str(payload.get('_csrf_token') or '')
        return ''

    def csrf_is_valid() -> bool:
        expected = str(session.get('_csrf_token') or '')
        supplied = csrf_value_from_request()
        if not expected or not supplied:
            return False
        # A header or form field can carry any character, and compare_digest()
        # raises TypeError on non-ASCII str: a bogus token would have answered 500
        # with a full traceback in the log instead of the 403 a bad token deserves.
        # Comparing bytes keeps the constant-time property; 'replace' only turns an
        # unencodable lone surrogate into bytes that cannot match the token.
        return secrets.compare_digest(
            expected.encode('utf-8'), supplied.encode('utf-8', 'replace'),
        )

    def unauthorized_response():
        # A stale session (server restarted, password changed) lands here on every
        # request; logging it at info is what makes "it keeps asking me to log in"
        # answerable from the log instead of by guesswork.
        _LOGGER.info('unauthorized %s %s from %s', request.method, request.path, request.remote_addr or 'unknown')
        if request.path.startswith('/api/'):
            return {'success': False, 'error': t('auth.error.required')}, 401
        if request.path.startswith('/media') or request.path == '/thumb':
            return t('auth.error.required'), 401
        target = request.full_path.rstrip('?') if request.method == 'GET' else '/'
        return redirect(url_for('login_view', next=target))

    def csrf_error_response():
        # A rejected token used to answer 403 with no trace at all: the browser
        # showed "your request expired" and the log could not confirm whether the
        # user had a stale tab or a script was posting without the header.
        _LOGGER.warning(
            'CSRF token rejected for %s %s from %s',
            request.method, request.path, request.remote_addr or 'unknown',
        )
        if request.path.startswith('/api/'):
            return {'success': False, 'error': t('auth.error.csrf')}, 403
        # A bare string rendered as an unstyled <html lang="en"> page: after a second
        # tab logged in (which rotates the token) the visible "log out" button landed
        # here with no way back, while the cookie stayed valid for 30 more days.
        return render_template('error.html', status=403, message=t('auth.error.csrf')), 403

    def native_token_is_valid() -> bool:
        authorization = str(request.headers.get('Authorization') or '')
        scheme, separator, token = authorization.partition(' ')
        return bool(
            device_auth_store
            and separator
            and scheme.lower() == 'bearer'
            and device_auth_store.verify(token, auth_revision=auth_store.revision)
        )

    @app.before_request
    def enforce_authentication():
        if request.endpoint in {
            'static',
            'favicon',
            'retired_service_worker',
        }:
            return None
        if request.endpoint == 'login_view':
            if request.method in UNSAFE_METHODS and not csrf_is_valid():
                return csrf_error_response()
            return None
        if request.path in {'/api/v1/pair', '/api/v1/pair/claim', '/api/v1/server'}:
            # /api/v1/server only reports the name, version and whether auth is
            # required; a client has to read it before it can pair, so requiring a
            # device token made the endpoint unreachable and `auth_required` a dead
            # field.
            return None
        if request.path.startswith('/api/v1/'):
            if native_token_is_valid():
                return None
            return {'success': False, 'error': {
                'code': 'authentication_required',
                'message': 'A valid device token is required',
            }}, 401
        if not is_authenticated():
            return unauthorized_response()
        if request.method in UNSAFE_METHODS and not csrf_is_valid():
            return csrf_error_response()
        return None

    @app.after_request
    def apply_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        if request.endpoint == 'login_view':
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.context_processor
    def auth_context():
        return {'auth_enabled': True, 'csrf_token': ensure_csrf_token}

    @app.route('/login', methods=['GET', 'POST'])
    def login_view():
        next_url = safe_next(request.values.get('next'))
        if is_authenticated():
            return redirect(next_url)

        error = ''
        status = 200
        retry_after = 0
        try:
            auth_store.revision
        except RuntimeError:
            # A POST here would call verify() and raise the same error out of the view,
            # turning "your auth.json is unreadable" into a 500 traceback with no hint.
            _LOGGER.error('authentication store is unreadable; refusing the login')
            error = t('login.error_store_unreadable')
            status = 503
            store_unreadable = True
        else:
            store_unreadable = False
        if request.method == 'POST' and not store_unreadable:
            client_key = request.remote_addr or 'unknown'
            retry_after = limiter.retry_after(client_key)
            if retry_after:
                minutes = max(1, (retry_after + 59) // 60)
                key = 'login.error_too_many_one' if minutes == 1 else 'login.error_too_many'
                error = t(key, minutes=minutes)
                status = 429
                # Every device behind one proxy shares remote_addr, so a lockout
                # here can mean "someone else on the network guessed wrong".
                _LOGGER.warning('login blocked by the rate limit for %s (%ss left)', client_key, retry_after)
            elif not auth_store.verify(request.form.get('password', '')):
                limiter.record_failure(client_key)
                error = t('login.error_wrong_password')
                status = 401
                _LOGGER.warning('login failed for %s', client_key)
            else:
                limiter.clear(client_key)
                remember = request.form.get('remember') == '1'
                session.clear()
                session['authenticated'] = True
                session['auth_revision'] = auth_store.revision
                session.permanent = remember
                ensure_csrf_token()
                _LOGGER.info('login succeeded for %s', client_key)
                return redirect(next_url)

        response = make_response(render_template(
            'login.html',
            csrf_value=ensure_csrf_token(),
            next_url=next_url,
            error=error,
        ), status)
        if status == 429:
            # A 429 without Retry-After gives clients nothing to back off on.
            response.headers['Retry-After'] = str(int(retry_after))
        return response

    @app.post('/logout')
    def logout_view():
        session.clear()
        response = redirect(url_for('login_view'))
        # session.clear() drops the server-side payload but leaves the cookie in the
        # browser; clearing it keeps a stale session id from being replayed.
        response.delete_cookie(app.config.get('SESSION_COOKIE_NAME') or 'session')
        # Clearing the cookie is not enough: the pages already in the back/forward
        # cache still hold this user's library. no-store keeps new pages out of it,
        # and this asks the browser to drop what it already cached (ignored where
        # unsupported).
        response.headers['Clear-Site-Data'] = '"cache"'
        return response
