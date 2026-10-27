"""Round-19 audit regressions: settings/runtime edges found by the audit team."""

import os
import re
from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.services.auth import AuthStore

PASSWORD = 'private-radio-password'


def _csrf_from(response) -> str:
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match, 'login page has no CSRF token'
    return match.group(1).decode()


def _app(tmp_path, **overrides):
    media_root = tmp_path / 'media'
    media_root.mkdir(exist_ok=True)
    config = {'TESTING': True, 'MEDIA_ROOT': media_root}
    config.update(overrides)
    return create_app(config)


def test_unreadable_url_list_answers_503_instead_of_500(tmp_path, monkeypatch):
    # A directory where the URL list file should be made os.listdir/read_text raise
    # OSError; the route used to let that bubble up as an unhandled 500.
    data_dir = Path(os.environ['TIKLOCAL_INSTANCE'])
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / 'download-urls.txt').mkdir()

    client = _app(tmp_path).test_client()
    assert client.get('/api/download/urls').status_code == 503
    assert client.get('/api/download/urls/export').status_code == 503


def test_tiktok_config_reports_the_field_specific_error(tmp_path):
    # update_config() already returns a localized message; the route used to map it
    # through the URL-list error table and answer the generic "invalid URL" text.
    client = _app(tmp_path).test_client()
    response = client.post('/api/download/tiktok/config', json={
        'tiktok_downloader': {'base_url': '', 'timeout_sec': 999},
    })
    assert response.status_code == 400
    payload = response.get_json()
    assert 'timeout' in payload['error'].lower() or '超时' in payload['error']


def test_tiktok_config_rejects_a_scheme_only_base_url(tmp_path):
    client = _app(tmp_path).test_client()
    response = client.post('/api/download/tiktok/config', json={
        'tiktok_downloader': {'base_url': 'http://'},
    })
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_pairing_and_login_share_one_attempt_budget(tmp_path):
    # Both endpoints validate the same access password. With two limiters an
    # attacker got 5 more guesses on /api/v1/pair while /login was locked out.
    data_dir = tmp_path / 'data'
    auth_path = data_dir / 'auth.json'
    AuthStore(auth_path).ensure(PASSWORD)
    client = _app(
        tmp_path,
        AUTH_ENABLED=True,
        AUTH_PATH=auth_path,
        DEVICE_AUTH_PATH=data_dir / 'radio_devices.json',
    ).test_client()

    for _ in range(5):
        client.post('/login', data={'password': 'wrong-password', '_csrf_token': _csrf_from(client.get('/login'))})

    locked = client.post('/api/v1/pair', json={'password': 'wrong-password', 'device_name': 'audit phone'})
    assert locked.status_code == 429
    assert locked.get_json()['error']['code'] == 'too_many_attempts'


def test_scan_result_surfaces_unavailable_sources(tmp_path):
    # A source on an unmounted drive was silently skipped while the UI announced
    # "scan complete", so the missing library looked like a successful empty scan.
    source_dir = tmp_path / 'missing-source'
    client = _app(tmp_path, MEDIA_SOURCES=[{'id': 'offline', 'path': str(source_dir)}]).test_client()
    payload = client.post('/api/library/sync').get_json()
    assert payload['success'] is True
    assert 'offline' in (payload['data'].get('unavailable_sources') or []) or \
        'offline' in (payload['data'].get('source_errors') or {})
