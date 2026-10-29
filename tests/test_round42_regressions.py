"""Regressions for the round-42 review: two processes, two tabs, one instance."""
import json
import os
import re
from pathlib import Path

import pytest

from tiklocal import app as app_module
from tiklocal import logging_setup
from tiklocal.app import create_app
from tiklocal.dedupe import _scan_files
from tiklocal.paths import DOWNLOAD_TMP_DIR
from tiklocal.services.auth import AuthStore
from tiklocal.services.library import VIDEO_EXTENSIONS
from tiklocal.services.url_list import UrlListStore


PASSWORD = 'private-screening-room'


def _csrf_from(response) -> str:
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


# --- one list, two tabs -------------------------------------------------------


def test_removing_consumed_urls_keeps_links_another_tab_imported(tmp_path):
    """The download page cleared the whole table after queueing.

    A second tab (or a phone) that had just imported twenty links lost them to the
    first tab's cleanup, with no way to get them back.
    """
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'config.yaml')
    store.add(['https://example.com/a', 'https://example.com/b', 'https://example.com/c'])

    kept = store.remove_many(['https://example.com/a'])

    assert kept == ['https://example.com/b', 'https://example.com/c']


def test_the_url_api_removes_only_the_named_lines(tmp_path, monkeypatch):
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'instance'))
    media_root = tmp_path / 'media'
    media_root.mkdir()
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'config.yaml')
    store.add(['https://example.com/a', 'https://example.com/b'])
    monkeypatch.setattr('tiklocal.web.downloads.UrlListStore', lambda: store)
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})

    response = app.test_client().post(
        '/api/download/urls',
        json={'action': 'remove', 'lines': ['https://example.com/a']},
    )

    assert response.status_code == 200
    assert response.get_json()['data']['lines'] == ['https://example.com/b']


# --- one session, two tabs ----------------------------------------------------


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'clip.mp4').write_bytes(b'private-video')
    auth_path = tmp_path / 'auth.json'
    AuthStore(auth_path).ensure(PASSWORD)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'instance'))
    return create_app({
        'TESTING': True,
        'MEDIA_ROOT': media_root,
        'AUTH_ENABLED': True,
        'AUTH_PATH': auth_path,
    })


def test_login_keeps_the_csrf_token_so_other_tabs_keep_working(auth_app):
    """Rotating the token at login broke every other open tab.

    The cookie is shared, so a second tab was authenticated but kept sending the old
    token from its <meta>: every write answered 403 for good, and even its logout form
    landed on the 403 page.
    """
    client = auth_app.test_client()
    token = _csrf_from(client.get('/login'))

    client.post('/login', data={'_csrf_token': token, 'password': PASSWORD, 'next': '/'})

    with client.session_transaction() as auth_session:
        assert auth_session['_csrf_token'] == token


def test_logging_out_works_even_with_a_stale_token(auth_app):
    client = auth_app.test_client()
    client.post('/login', data={'_csrf_token': _csrf_from(client.get('/login')),
                               'password': PASSWORD, 'next': '/'})

    response = client.post('/logout')

    assert response.status_code == 302
    assert client.get('/api/library/stats').status_code == 401


def test_the_csrf_rejection_carries_a_code_the_client_can_act_on(auth_app):
    client = auth_app.test_client()
    client.post('/login', data={'_csrf_token': _csrf_from(client.get('/login')),
                               'password': PASSWORD, 'next': '/'})

    response = client.post('/api/activity', json={'events': []})

    assert response.status_code == 403
    assert response.get_json()['code'] == 'csrf'


# --- one auth file, two processes --------------------------------------------


def test_only_one_process_creates_the_auth_file(tmp_path):
    """check-then-create let two fresh processes write two different secret keys.

    The server kept the key it read at start-up while the loser's write won on disk,
    so every session died at the next restart.
    """
    path = tmp_path / 'auth.json'

    first = AuthStore(path).ensure(PASSWORD)
    second = AuthStore(path).ensure('another-password-entirely')

    assert first.created is True
    assert second.created is False
    secret = json.loads(path.read_text(encoding='utf-8'))['secret_key']
    assert AuthStore(path)._load()['secret_key'] == secret
    assert not path.with_name(path.name + '.claim').exists()


# --- one database, a damaged file --------------------------------------------


def test_quarantine_moves_the_wal_before_the_database(tmp_path, monkeypatch):
    """Moving the main file first left a stale WAL next to an empty database.

    The next start replayed that WAL into a brand-new file, which is the permanent
    breakage the quarantine exists to prevent.
    """
    db_path = tmp_path / 'tiklocal.sqlite3'
    db_path.write_bytes(b'database')
    (tmp_path / 'tiklocal.sqlite3-wal').write_bytes(b'wal')
    calls = []
    real_replace = Path.replace

    def fake_replace(self, target):
        calls.append(self.name)
        if self.name == 'tiklocal.sqlite3':
            raise PermissionError(32, 'in use by another process')
        return real_replace(self, target)

    monkeypatch.setattr(Path, 'replace', fake_replace)
    monkeypatch.setattr(app_module.time, 'sleep', lambda _seconds: None)

    assert app_module._quarantine_database(db_path) is None
    assert calls[0] == 'tiklocal.sqlite3-wal'
    assert db_path.is_file()
    assert (tmp_path / 'tiklocal.sqlite3-wal').is_file()


# --- one log directory, many processes ---------------------------------------


def test_the_log_file_is_named_after_the_process():
    assert str(os.getpid()) in logging_setup.LOG_FILE_NAME


def test_old_process_logs_are_pruned(tmp_path):
    for index in range(6):
        path = tmp_path / f'tiklocal.{1000 + index}.log'
        path.write_text('x', encoding='utf-8')
        os.utime(path, (1000 + index, 1000 + index))

    logging_setup._prune_old_log_files(tmp_path)

    remaining = sorted(path.name for path in tmp_path.glob('tiklocal.*.log'))
    assert remaining == [f'tiklocal.{1002 + index}.log' for index in range(4)]


# --- one media root, one work folder -----------------------------------------


def test_dedupe_skips_the_download_work_folder(tmp_path):
    """Hashing a half-finished download read gigabytes for nothing."""
    (tmp_path / 'clip.mp4').write_bytes(b'video')
    work = tmp_path / DOWNLOAD_TMP_DIR / 'job-1'
    work.mkdir(parents=True)
    (work / 'partial.mp4').write_bytes(b'half')

    files = _scan_files(tmp_path, VIDEO_EXTENSIONS)

    assert [path.name for path in files] == ['clip.mp4']
