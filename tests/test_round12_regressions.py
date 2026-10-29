"""Regressions from the round-12 review: logging, probing and message localization."""

import io
import locale
import logging
import re
import sys
import threading
from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.logging_setup import RedactingFormatter, configure_logging
from tiklocal.services.auth import AuthStore
from tiklocal.services.download_sources import DownloadSourceStore
from tiklocal.services.downloader import DownloadConfigStore, DownloadHistoryStore, DownloadManager
from tiklocal.services.library_index import MediaProbeWorker
from tiklocal.web.author import format_count


def test_probe_pass_accepts_a_time_budget(tmp_path):
    """The budgeted pass used ``time.monotonic()`` without importing ``time``.

    Every wake of the background worker then raised NameError, so no file ever got
    its dimensions; the tests missed it because they never passed ``budget_sec``.
    """
    media = tmp_path / 'media'
    media.mkdir()
    from PIL import Image

    Image.new('RGB', (40, 30)).save(media / 'photo.jpg')
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media})
    probe = app.extensions['media_probe']

    assert isinstance(probe, MediaProbeWorker)
    assert probe.run_once(budget_sec=5.0) == 1


def test_download_output_survives_locale_encoded_bytes(tmp_path):
    """yt-dlp writes its pipe with the console encoding (cp936 on zh-CN Windows).

    Round 12 forced `encoding="utf-8"` on the child: every Chinese path in a
    `[MoveFiles]` line became replacement characters, so the job finished with
    `file_count=0` while the file sat on disk. Decode the pipe with the same
    locale the child uses, and keep `errors="replace"` so one stray byte cannot
    kill the pump again.
    """
    manager = DownloadManager(
        tmp_path / 'media',
        DownloadConfigStore(tmp_path / 'download_config.json'),
        DownloadHistoryStore(tmp_path / 'download_jobs.json'),
        DownloadSourceStore(tmp_path / 'download_sources.json'),
    )
    manager._cancel_events['job'] = threading.Event()
    lines: list[str] = []

    encoding = locale.getpreferredencoding(False)
    title = "中文标题"
    code = (
        'import sys; sys.stdout.buffer.write(b"\\xff\\xfe"); '
        'sys.stdout.buffer.write((' + repr(title + " downloaded") + ').encode('
        + repr(encoding) + ', "replace"))'
    )
    returncode, error = manager._run_tool('job', [sys.executable, '-c', code], lines.append)

    assert returncode == 0, lines
    assert error == ''
    assert any(title in line for line in lines), lines


def test_traceback_and_short_credentials_are_redacted():
    formatter = RedactingFormatter('%(message)s')

    try:
        raise RuntimeError('auth failed: https://api.example.com/v1?token=abc&api_key=AKIA1234567890')
    except RuntimeError:
        record = logging.LogRecord(
            'tiklocal.test', logging.ERROR, __file__, 0, 'download failed', (), sys.exc_info()
        )
    rendered = formatter.format(record)

    assert 'supersecret' not in rendered
    assert 'AKIA1234567890' not in rendered
    # A three-character token used to slip through the {4,} value rule.
    assert 'token=abc' not in rendered
    assert '***' in rendered


def test_log_file_is_created_under_the_data_dir(tmp_path):
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_flag = getattr(root, '_tiklocal_logging_configured', False)
    try:
        if previous_flag:
            delattr(root, '_tiklocal_logging_configured')
        root.handlers = []
        configure_logging(tmp_path)
        logging.getLogger('tiklocal.test').warning('hello token=abcd')
        target = tmp_path / 'tiklocal.log'
        assert target.exists()
        assert 'token=***' in target.read_text(encoding='utf-8')
    finally:
        root.handlers = previous_handlers
        if previous_flag:
            setattr(root, '_tiklocal_logging_configured', True)


def test_server_info_is_reachable_before_pairing(tmp_path):
    media = tmp_path / 'media'
    media.mkdir()
    auth_path = tmp_path / 'auth.json'
    AuthStore(auth_path).ensure('private-screening-room')
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media,
        'AUTH_ENABLED': True,
        'AUTH_PATH': auth_path,
    })

    response = app.test_client().get('/api/v1/server')

    assert response.status_code == 200
    payload = response.get_json()['data']
    assert payload['auth_required'] is True
    assert payload['server_version']


def test_csrf_failure_on_an_html_form_renders_a_page(tmp_path):
    media = tmp_path / 'media'
    media.mkdir()
    auth_path = tmp_path / 'auth.json'
    AuthStore(auth_path).ensure('private-screening-room')
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media,
        'AUTH_ENABLED': True,
        'AUTH_PATH': auth_path,
    })

    response = app.test_client().post('/login', data={'password': 'whatever', 'next': '/'})

    assert response.status_code == 403
    body = response.get_data(as_text=True)
    assert '<html' in body.lower()
    assert re.search(r'href="/(library|)"', body)


def test_english_compact_count_uses_a_thousand_based_unit(tmp_path):
    from tiklocal.app import create_app as _create_app

    # Only a request context is needed here; pointing MEDIA_ROOT at the repository
    # made every run index the whole checkout (and race the background scan of the
    # previous test, which surfaced as an "unable to open database file" flake).
    app = _create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path})
    with app.test_request_context(headers={'Accept-Language': 'en'}):
        assert format_count(9999) == '9999'
        assert format_count(12345) == '12.3K'
        assert format_count(1234567) == '1.2M'
    with app.test_request_context(headers={'Accept-Language': 'zh-CN'}):
        assert format_count(12345) == '1.2万'
