"""Regressions for round 44: what the user is told when something fails.

The round's viewpoint was error messages, so these tests pin the contract the UI
depends on: a JSON error the browser can show, no absolute path in a message the
browser receives, and a log line that names the failure without leaking where the
user's files live.
"""
import logging

import pytest

from tiklocal.app import create_app
from tiklocal.i18n import TRANSLATIONS
from tiklocal.logging_setup import redact_text
from tiklocal.services.downloader import download_failure_message
from tiklocal.services.tiktok_downloader import TikTokDownloaderError, tiktok_user_message
from tiklocal.web.client_log import _clean


@pytest.fixture
def client(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'movie.mp4').write_bytes(b'video')
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()


def test_a_bad_playback_report_answers_with_a_json_error(client):
    response = client.post('/api/playback', json={'uri': 'movie.mp4', 'position': 'x', 'duration': 10})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
    assert payload['error'] == TRANSLATIONS['zh']['media.error.playback_fields']


def test_an_unknown_media_playback_report_says_why(client):
    response = client.post('/api/playback', json={'uri': 'gone.mp4', 'position': 10, 'duration': 100})
    assert response.status_code == 404
    payload = response.get_json()
    assert payload['success'] is False
    assert payload['error'] == TRANSLATIONS['zh']['media.error.playback_unknown']


def test_an_api_500_carries_the_reference_in_the_message(tmp_path):
    app = create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path})
    app.config['PROPAGATE_EXCEPTIONS'] = False

    @app.route('/api/round44-boom')
    def _boom():
        raise RuntimeError('the detail belongs in the log, not the browser')

    response = app.test_client().get('/api/round44-boom')
    assert response.status_code == 500
    payload = response.get_json()
    # The reference was in the JSON all along but every fetch helper reads only
    # `error`, so no user could ever quote it in a report.
    assert payload['reference']
    assert payload['reference'] in payload['error']
    assert 'the detail belongs in the log' not in payload['error']


def test_an_os_failure_message_has_no_absolute_path():
    text = download_failure_message(PermissionError(13, 'Permission denied', r'C:\Users\me\secret'))
    assert 'C:\\Users' not in text
    assert '13' in text


def test_a_browser_report_is_redacted_before_it_reaches_the_log():
    cleaned = _clean('https://example.test/page?token=supersecret&q=1', 300)
    assert 'supersecret' not in cleaned
    assert 'token=***' in cleaned
    assert '\\n' not in _clean('a\nb', 10)


def test_the_tiktok_error_text_never_carries_the_sidecar_url():
    error = TikTokDownloaderError('HTTPConnectionPool(host=127.0.0.1, port=9911): Max retries exceeded')
    assert '127.0.0.1' not in tiktok_user_message(error)


def test_logout_leaves_a_trace(tmp_path, caplog):
    import re

    from tiklocal.services.auth import AuthStore

    media_root = tmp_path / 'media'
    media_root.mkdir()
    auth_path = tmp_path / 'auth.json'
    AuthStore(auth_path).ensure('private-screening-room')
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root,
                      'AUTH_ENABLED': True, 'AUTH_PATH': auth_path})
    client = app.test_client()
    page = client.get('/login')
    token = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data).group(1).decode()
    client.post('/login', data={'password': 'private-screening-room', '_csrf_token': token})
    with caplog.at_level(logging.INFO, logger='tiklocal.auth'):
        client.post('/logout')
    assert any('logout' in record.getMessage() for record in caplog.records)


def test_every_round44_key_exists_in_both_languages():
    for key in ('media.error.playback_fields', 'media.error.playback_unknown',
                'download.error.os_failure', 'download.probe_failed', 'radio.retry',
                'radio.load_failed_hint', 'common.error.offline', 'common.error.timeout'):
        assert key in TRANSLATIONS['zh'], key
        assert key in TRANSLATIONS['en'], key
