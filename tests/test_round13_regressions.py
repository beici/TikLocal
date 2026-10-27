"""Regressions for the round-13 findings (HTTP contract, probe fairness, index plan)."""

import sqlite3
import time

import pytest
from PIL import Image

from tiklocal.app import create_app
from tiklocal.services import library_index as library_index_module
from tiklocal.services.library_index import LibraryIndexer, MediaIndexStore, MediaProbeWorker


@pytest.fixture
def app(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    Image.new('RGB', (32, 32), 'red').save(media_root / 'photo.jpg')
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root})


@pytest.fixture
def client(app):
    return app.test_client()


def test_method_not_allowed_keeps_the_allow_header(client):
    response = client.get('/api/library/sync')
    assert response.status_code == 405
    assert response.get_json()['success'] is False
    assert 'POST' in response.headers.get('Allow', '')

    page = client.post('/library')
    assert page.status_code == 405
    assert 'GET' in page.headers.get('Allow', '')


def test_url_list_errors_are_localized_instead_of_raw_service_text(client):
    response = client.post('/api/download/urls/sync', json={'direction': 'push'})
    assert response.status_code == 400
    message = response.get_json()['error']
    assert 'No server path configured' not in message
    assert message.strip()


def test_url_list_export_sends_one_charset(client):
    response = client.get('/api/download/urls/export')
    assert response.status_code == 200
    assert response.headers['Content-Type'].count('charset=') == 1


def test_cache_clear_failure_does_not_echo_the_server_path(client, monkeypatch):
    from tiklocal.services.thumbnail import ThumbnailService

    def boom(self):
        raise OSError(r'C:\secret\cache\path is locked')

    monkeypatch.setattr(ThumbnailService, 'clear_cache', boom)
    response = client.post('/api/cache/clear')
    assert response.status_code == 500
    assert 'secret' not in response.get_json()['error']


def test_collection_name_must_be_a_string(client):
    for payload in ({'name': None}, {'name': []}, {'name': {'a': 1}}):
        response = client.post('/api/collections', json=payload)
        assert response.status_code == 400, payload


@pytest.fixture
def index_store(tmp_path):
    from tiklocal.services.database import AppDatabase

    database = AppDatabase(tmp_path / 'state' / 'app.db')
    database.migrate()
    return MediaIndexStore(database)


def test_preserved_prefix_does_not_protect_sibling_folders(index_store):
    store = index_store
    store.replace_snapshot(
        [
            {'uri': '@default/sub/keep.jpg', 'source_id': 'default', 'rel_path': 'sub/keep.jpg',
             'filename': 'keep.jpg', 'parent_path': 'sub', 'media_type': 'image',
             'extension': '.jpg', 'size_bytes': 1, 'mtime': 1.0},
            {'uri': '@default/subdir/gone.jpg', 'source_id': 'default', 'rel_path': 'subdir/gone.jpg',
             'filename': 'gone.jpg', 'parent_path': 'subdir', 'media_type': 'image',
             'extension': '.jpg', 'size_bytes': 1, 'mtime': 1.0},
        ],
        synced_source_ids={'default'},
    )
    result = store.replace_snapshot([], synced_source_ids={'default'},
                                    preserved_prefixes=['@default/sub'])
    assert result['deleted'] == 1, 'a sibling folder must not be shielded by the prefix'
    uris = [row['name'] for row in store.records()]
    assert uris == ['@default/sub/keep.jpg']


def test_library_sync_maps_a_locked_database_to_409(client, monkeypatch):
    def locked(self):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(LibraryIndexer, 'sync', locked)
    response = client.post('/api/library/sync')
    assert response.status_code == 409
    assert response.get_json()['success'] is False


def test_probe_pass_steps_over_rows_that_keep_failing(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.rows = [
                {'uri': '@default/bad1.jpg', 'media_type': 'image', 'mtime': 3.0},
                {'uri': '@default/bad2.jpg', 'media_type': 'image', 'mtime': 2.0},
                {'uri': '@default/good.jpg', 'media_type': 'image', 'mtime': 1.0},
            ]
            self.saved = []

        def unprobed(self, limit=50, exclude=()):
            return [row for row in self.rows if row['uri'] not in exclude][:limit]

        def set_dimensions(self, rows):
            self.saved.extend(rows)
            done = {row[3] for row in rows}
            self.rows = [row for row in self.rows if row['uri'] not in done]

    def fake_probe(library, uri, media_type):
        if 'bad' in uri:
            return None, None
        return 640, 480

    monkeypatch.setattr(library_index_module, 'probe_media_dims', fake_probe)
    monkeypatch.setattr(library_index_module, 'probe_tool_available', lambda: True)

    store = FakeStore()
    worker = MediaProbeWorker(None, store)
    started = time.monotonic()
    done = worker.run_once(batch=2, budget_sec=2.0)

    assert done == 1
    assert store.saved == [(640, 480, 1.0, '@default/good.jpg')]
    assert time.monotonic() - started < 5.0
    assert set(worker._backoff) == {'@default/bad1.jpg', '@default/bad2.jpg'}


def test_media_items_page_uses_the_mtime_index(index_store):
    with index_store.database.connect() as conn:
        plan = ' '.join(
            row[3] for row in conn.execute(
                'EXPLAIN QUERY PLAN SELECT uri FROM media_items '
                'ORDER BY mtime DESC, uri LIMIT 40 OFFSET 0'
            ).fetchall()
        )
    assert 'TEMP B-TREE' not in plan.upper()
