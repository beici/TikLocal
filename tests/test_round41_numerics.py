"""Regressions for the round-41 review: numbers that only go wrong over time."""
import sqlite3

import pytest

from tiklocal.app import create_app
from tiklocal.i18n import detect_locale
from tiklocal.paths import get_database_path
from tiklocal.services.database import AppDatabase
from tiklocal.services.downloader import DownloadHistoryStore
from tiklocal.services.library import LibraryService
from tiklocal.services.library_index import LibraryIndexer, MediaIndexStore


@pytest.fixture
def app(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root})


def test_library_offset_is_capped(app):
    """An offset past int64 raised OverflowError inside sqlite3, so the page 500'd.

    Nothing prevented ``?offset=10000000000000000000``: read_int_arg had a minimum
    and no maximum, and the value went straight into ``LIMIT ? OFFSET ?``.
    """
    response = app.test_client().get('/api/library/items?offset=10000000000000000000')

    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_a_zero_duration_position_does_not_break_the_grid(app, tmp_path):
    """``position / duration`` with a restored duration of 0 took every grid down.

    The row can come from a hand-edited trash snapshot, so the guard has to live in
    the renderer, not in the writer.
    """
    media_root = tmp_path / 'media'
    (media_root / 'clip.mp4').write_bytes(b'video')
    library = LibraryService(media_root)
    database = AppDatabase(get_database_path())
    index = MediaIndexStore(database)
    LibraryIndexer(library, index).register_uris(['@default/clip.mp4'])
    with database.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO playback_positions(uri, position, duration, updated_at)"
            " VALUES (?, ?, ?, ?)",
            ('@default/clip.mp4', 12.5, 0.0, '2026-01-01T00:00:00+00:00'),
        )

    response = app.test_client().get('/api/library/items')

    assert response.status_code == 200
    items = response.get_json()['data']['items']
    assert items, 'the indexed clip should be listed'
    assert 'progress' not in items[0]
    assert items[0]['resume'] == pytest.approx(12.5)


def test_unfinished_jobs_survive_the_history_limit(tmp_path, monkeypatch):
    """Truncating the tail of jobs.json dropped queued work without a trace.

    The in-memory trim stopped at the first unfinished job, so a queue longer than
    the limit kept growing; the save then cut it off at the same limit, and a job
    that was never written did not come back as "interrupted" either.
    """
    monkeypatch.setattr('tiklocal.services.downloader.DOWNLOAD_HISTORY_LIMIT', 2)
    store = DownloadHistoryStore(tmp_path / 'jobs.json')

    store.save([
        {'id': 'queued-1', 'status': 'queued'},
        {'id': 'done-1', 'status': 'success'},
        {'id': 'done-2', 'status': 'success'},
    ])

    kept = [item['id'] for item in store.get()]
    assert kept == ['queued-1', 'done-1']


def test_filesizeformat_carries_into_the_next_unit(app):
    """1048575 bytes printed as "1024.0 KB", the same digits a 1 MB file shows."""
    filesizeformat = app.jinja_env.filters['filesizeformat']

    assert filesizeformat(1048575) == '1.0 MB'
    assert filesizeformat(1048576) == '1.0 MB'
    assert filesizeformat(1023) == '1023 B'


def test_client_log_accepts_an_infinite_line_number(app):
    """JSON allows 1e400, which Python reads as inf; int(inf) raises OverflowError.

    That escaped the handler, so the endpoint that promises never to fail answered
    500 and logged an unhandled error instead of the report it was given.
    """
    response = app.test_client().post(
        '/api/client-log',
        json={'message': 'boom', 'source': 'app.js', 'line': 1e400},
    )

    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_accept_language_zero_quality_is_a_refusal():
    """``q=0`` means "not acceptable", not "lowest priority"."""
    assert detect_locale(lambda name: None, 'zh-CN;q=0, en;q=0.9') == 'en'
    assert detect_locale(lambda name: None, 'en;q=0, zh;q=0.5') == 'zh'
