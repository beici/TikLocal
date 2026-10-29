import json
import os
import re
import stat

import pytest

from tiklocal.app import create_app
from tiklocal.i18n import t
from tiklocal.services.auth import LEGACY_DEFAULT_PASSWORD, MIN_PASSWORD_LENGTH, AuthStore


PASSWORD = 'private-screening-room'


@pytest.fixture
def authenticated_app(tmp_path, monkeypatch):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'clip.mp4').write_bytes(b'private-video')
    auth_path = tmp_path / 'auth.json'
    AuthStore(auth_path).ensure(PASSWORD)
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media_root,
        'AUTH_ENABLED': True,
        'AUTH_PATH': auth_path,
    })
    return app, auth_path


def _csrf_from(response) -> str:
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def _login(client, password=PASSWORD, next_url='/'):
    login_page = client.get('/login', query_string={'next': next_url})
    csrf_token = _csrf_from(login_page)
    return client.post('/login', data={
        '_csrf_token': csrf_token,
        'password': password,
        'next': next_url,
        'remember': '1',
    })


def test_auth_store_hashes_password_and_increments_revision(tmp_path):
    auth_path = tmp_path / 'auth.json'
    store = AuthStore(auth_path)

    bootstrap = store.ensure(PASSWORD)
    data = json.loads(auth_path.read_text(encoding='utf-8'))

    assert bootstrap.created is True
    assert bootstrap.generated_password is None
    assert PASSWORD not in auth_path.read_text(encoding='utf-8')
    assert data['password_hash'].startswith('scrypt:')
    assert data['created_at'].endswith('+00:00')
    assert data['updated_at'].endswith('+00:00')
    assert store.verify(PASSWORD)
    if os.name != 'nt':
        # Windows keeps only the read-only flag, so POSIX mode bits cannot be asserted there.
        assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600

    previous_secret = store.secret_key
    store.set_password('another-private-password')
    assert store.revision == 2
    assert store.secret_key == previous_secret
    assert not store.verify(PASSWORD)
    assert store.verify('another-private-password')


def test_first_start_generates_a_password_instead_of_the_published_default(tmp_path):
    store = AuthStore(tmp_path / 'auth.json')
    bootstrap = store.ensure()

    assert bootstrap.created is True
    password = bootstrap.generated_password
    assert password is not None
    assert len(password) >= MIN_PASSWORD_LENGTH
    assert store.verify(password)
    # The constant every earlier release installed must no longer work.
    assert not store.verify(LEGACY_DEFAULT_PASSWORD)
    assert not store.verify(PASSWORD)


def test_each_fresh_instance_gets_a_different_password(tmp_path):
    first = AuthStore(tmp_path / 'a' / 'auth.json').ensure().generated_password
    second = AuthStore(tmp_path / 'b' / 'auth.json').ensure().generated_password

    assert first and second and first != second


def test_uses_published_default_flags_an_install_from_before_the_change(tmp_path):
    store = AuthStore(tmp_path / 'auth.json')

    assert store.ensure().generated_password is not None
    assert store.uses_published_default() is False

    # What the old first start wrote: the hash of the constant from the docs.
    store.set_password(LEGACY_DEFAULT_PASSWORD)
    assert store.uses_published_default() is True


def test_supplied_first_start_password_wins_over_the_generated_one(tmp_path):
    store = AuthStore(tmp_path / 'auth.json')
    bootstrap = store.ensure(PASSWORD)

    assert bootstrap.created is True
    assert bootstrap.generated_password is None
    assert store.verify(PASSWORD)
    assert not store.verify(LEGACY_DEFAULT_PASSWORD)


def test_fresh_instance_signs_in_with_the_generated_password(tmp_path, monkeypatch):
    monkeypatch.delenv('TIKLOCAL_AUTH_PASSWORD', raising=False)
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'clip.mp4').write_bytes(b'private-video')
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media_root,
        'AUTH_ENABLED': True,
        'AUTH_PATH': tmp_path / 'auth.json',
    })
    client = app.test_client()

    generated = app.extensions['auth_bootstrap'].generated_password
    assert generated
    assert _login(client, generated).status_code == 302
    assert client.get('/').status_code == 200
    assert _login(app.test_client(), LEGACY_DEFAULT_PASSWORD).status_code == 401
    assert _login(app.test_client(), PASSWORD).status_code == 401


def test_authentication_protects_pages_apis_and_media(authenticated_app):
    app, _ = authenticated_app
    client = app.test_client()

    page = client.get('/')
    api = client.get('/api/library/stats')
    media = client.get('/media/clip.mp4')
    static_asset = client.get('/static/csrf_fetch.js')
    favicon = client.get('/favicon.ico')

    assert page.status_code == 302
    assert page.headers['Location'].endswith('/login?next=/')
    assert api.status_code == 401
    assert api.get_json()['error'] == t('auth.error.required')
    assert media.status_code == 401
    assert static_asset.status_code == 200
    assert favicon.status_code == 200
    assert favicon.mimetype == 'image/vnd.microsoft.icon'


def test_login_creates_a_secure_session(authenticated_app):
    app, _ = authenticated_app
    client = app.test_client()

    login_page = client.get('/login', query_string={'next': '/flow'})
    assert login_page.status_code == 200
    assert b'no-store' in login_page.headers['Cache-Control'].encode()
    assert login_page.headers['X-Frame-Options'] == 'SAMEORIGIN'

    failed = _login(client, password='incorrect-password')
    assert failed.status_code == 401

    logged_in = _login(client, next_url='/flow')
    assert logged_in.status_code == 302
    assert logged_in.headers['Location'].endswith('/flow')
    cookie = logged_in.headers['Set-Cookie']
    assert 'HttpOnly' in cookie
    assert 'SameSite=Lax' in cookie
    assert 'Expires=' in cookie
    for url in ('/', '/settings/'):
        response = client.get(url)
        assert response.status_code == 200
        # no-store, not no-cache: Chrome and Safari keep no-cache pages in the
        # back/forward cache, and a bfcache restore never asks the server, so after
        # logging out on a shared machine Back showed the previous user's library.
        assert response.headers['Cache-Control'] == 'private, no-store'
    assert client.get('/media/clip.mp4').data == b'private-video'


def test_login_rejects_external_redirects(authenticated_app):
    app, _ = authenticated_app
    client = app.test_client()

    response = _login(client, next_url='https://example.com/steal-session')

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/')


def test_csrf_and_logout_are_enforced(authenticated_app):
    app, _ = authenticated_app
    client = app.test_client()
    _login(client)

    rejected = client.post('/api/activity', json={'events': []})
    with client.session_transaction() as auth_session:
        csrf_token = auth_session['_csrf_token']
    accepted = client.post(
        '/api/activity',
        json={'events': [], '_csrf_token': csrf_token},
    )
    rejected_logout = client.post('/logout')
    # A stale token must not trap the user in a logged-in state: a tab that still
    # carries the pre-login token used to land on the 403 page while its cookie
    # stayed valid for 30 more days. Logging out is safe, so it always goes through.
    assert rejected_logout.status_code == 302
    assert client.get('/api/library/stats').status_code == 401

    _login(client)
    with client.session_transaction() as auth_session:
        csrf_token = auth_session['_csrf_token']
    logged_out = client.post('/logout', data={'_csrf_token': csrf_token})

    assert accepted.status_code == 200
    assert logged_out.status_code == 302
    assert client.get('/api/library/stats').status_code == 401


def test_a_non_ascii_csrf_token_is_a_rejection_not_a_crash(authenticated_app):
    # compare_digest() raises TypeError on a non-ASCII str, so a bogus token used
    # to answer 500 and write a full traceback into the log instead of a 403.
    app, _ = authenticated_app
    client = app.test_client()
    _login(client)

    response = client.post(
        '/api/activity',
        json={'events': [], '_csrf_token': '日本語トークン'},
    )

    assert response.status_code == 403


def test_cli_password_change_invalidates_running_sessions(authenticated_app):
    app, auth_path = authenticated_app
    client = app.test_client()
    _login(client)
    for url in ('/', '/settings/'):
        response = client.get(url)
        assert response.status_code == 200
        # no-store, not no-cache: Chrome and Safari keep no-cache pages in the
        # back/forward cache, and a bfcache restore never asks the server, so after
        # logging out on a shared machine Back showed the previous user's library.
        assert response.headers['Cache-Control'] == 'private, no-store'

    AuthStore(auth_path).set_password('replacement-private-password')

    response = client.get('/')
    assert response.status_code == 302
    assert '/login?' in response.headers['Location']


def test_login_is_rate_limited_after_repeated_failures(authenticated_app):
    # The only brute-force guard in front of /login: five wrong passwords per
    # client, then 429 with a Retry-After inside the five-minute window.
    app, _ = authenticated_app
    client = app.test_client()

    for _ in range(5):
        assert _login(client, password='incorrect-password').status_code == 401

    blocked = _login(client, password='incorrect-password')
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers['Retry-After']) <= 300


def test_login_returns_to_the_requested_page_and_drops_external_targets(authenticated_app):
    app, _ = authenticated_app
    client = app.test_client()

    landed = _login(client, next_url='/flow?mode=album')
    assert landed.status_code == 302
    assert landed.headers['Location'] == '/flow?mode=album'

    stranger = app.test_client()
    hostile = _login(stranger, next_url='//evil.example/steal')
    assert hostile.status_code == 302
    assert hostile.headers['Location'] == '/'


def test_unsafe_api_request_without_session_is_401_not_403(authenticated_app):
    # require_auth must answer 401 before it inspects the CSRF token; otherwise
    # the client cannot tell "please log in" from "your token went stale".
    app, _ = authenticated_app
    client = app.test_client()

    response = client.post('/api/activity', json={'events': []})

    assert response.status_code == 401
    assert response.get_json()['success'] is False


def _api_csrf(client) -> str:
    with client.session_transaction() as session:
        return session['_csrf_token']


def _change_password(client, current, new, csrf=None):
    payload = {'current_password': current, 'new_password': new}
    if csrf is not None:
        payload['_csrf_token'] = csrf
    return client.post('/api/auth/password', json=payload)


NEW_PASSWORD = 'replacement-private-password'


def test_settings_page_renders_the_password_dialog(authenticated_app):
    app, _ = authenticated_app
    client = app.test_client()
    _login(client)

    page = client.get('/settings/')
    body = page.get_data(as_text=True)

    assert page.status_code == 200
    assert 'id="change-password"' in body
    assert 'id="password-form"' in body
    assert 'id="password-current"' in body
    assert f'minlength="{MIN_PASSWORD_LENGTH}"' in body


def test_password_change_keeps_this_session_and_signs_out_the_others(authenticated_app):
    # The owner changes the password from their own browser: that session must
    # survive, while the revision bump invalidates every other cookie. Otherwise
    # the "change password" button logs the owner out of the page they clicked it on.
    app, auth_path = authenticated_app
    owner = app.test_client()
    _login(owner)
    other_device = app.test_client()
    _login(other_device)
    assert owner.get('/').status_code == 200
    assert other_device.get('/').status_code == 200

    response = _change_password(owner, PASSWORD, NEW_PASSWORD, csrf=_api_csrf(owner))

    assert response.status_code == 200
    assert response.get_json()['success'] is True
    assert response.get_json()['revision'] == 2
    assert owner.get('/settings/').status_code == 200
    assert other_device.get('/').status_code == 302
    store = AuthStore(auth_path)
    assert store.verify(NEW_PASSWORD)
    assert not store.verify(PASSWORD)
    assert _login(app.test_client(), NEW_PASSWORD).status_code == 302
    assert _login(app.test_client(), PASSWORD).status_code == 401


def test_password_change_rejects_the_wrong_current_password(authenticated_app):
    app, auth_path = authenticated_app
    client = app.test_client()
    _login(client)

    response = _change_password(client, 'not-my-password', NEW_PASSWORD, csrf=_api_csrf(client))

    assert response.status_code == 400
    assert response.get_json()['error'] == t('settings.password.wrong_current')
    assert AuthStore(auth_path).verify(PASSWORD)
    assert client.get('/').status_code == 200


def test_password_change_rejects_a_short_password(authenticated_app):
    app, auth_path = authenticated_app
    client = app.test_client()
    _login(client)

    response = _change_password(client, PASSWORD, 'short', csrf=_api_csrf(client))

    assert response.status_code == 400
    assert str(MIN_PASSWORD_LENGTH) in response.get_json()['error']
    assert AuthStore(auth_path).verify(PASSWORD)


def test_password_change_rejects_a_non_object_body(authenticated_app):
    # A JSON array used to reach payload.get() and answer 500 with a traceback.
    app, auth_path = authenticated_app
    client = app.test_client()
    _login(client)

    response = client.post(
        '/api/auth/password',
        json=[NEW_PASSWORD],
        headers={'X-CSRF-Token': _api_csrf(client)},
    )

    assert response.status_code == 400
    assert response.get_json()['error'] == t('settings.password.required')
    assert AuthStore(auth_path).verify(PASSWORD)


def test_password_change_requires_a_csrf_token(authenticated_app):
    app, auth_path = authenticated_app
    client = app.test_client()
    _login(client)

    response = _change_password(client, PASSWORD, NEW_PASSWORD)

    assert response.status_code == 403
    assert AuthStore(auth_path).verify(PASSWORD)


def test_password_change_is_unavailable_when_auth_is_off(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'clip.mp4').write_bytes(b'private-video')
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media_root,
        'AUTH_ENABLED': False,
        'AUTH_PATH': tmp_path / 'auth.json',
    })
    client = app.test_client()

    response = client.post('/api/auth/password', json={
        'current_password': PASSWORD,
        'new_password': NEW_PASSWORD,
    })

    assert response.status_code == 400
    assert response.get_json()['error'] == t('settings.password.disabled')
    # The settings page must not advertise a control it cannot honour.
    assert 'id="change-password"' not in client.get('/settings/').get_data(as_text=True)


def test_password_change_shares_the_login_attempt_budget(authenticated_app):
    # Both endpoints check the same secret, so a wrong current password in the
    # dialog must not hand an attacker a second, separate five-attempt budget.
    app, auth_path = authenticated_app
    client = app.test_client()
    _login(client)
    csrf = _api_csrf(client)

    for _ in range(5):
        assert _change_password(client, 'not-my-password', NEW_PASSWORD, csrf=csrf).status_code == 400

    blocked = _change_password(client, 'not-my-password', NEW_PASSWORD, csrf=csrf)
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers['Retry-After']) <= 300
    # Even the right password waits out the window, and the login page is blocked too.
    assert _change_password(client, PASSWORD, NEW_PASSWORD, csrf=csrf).status_code == 429
    assert _login(app.test_client(), PASSWORD).status_code == 429
    assert AuthStore(auth_path).verify(PASSWORD)


