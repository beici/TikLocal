"""Regressions for the round-22 findings: detail-page neighbours, a cancel that lands
after a file was moved into the library, an unreadable password store, and the queue
rewrite that could swallow a URL."""

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.auth import LoginAttemptLimiter
from tiklocal.i18n import t
from tiklocal.services import url_list
from tiklocal.services.auth import AuthStore
from tiklocal.services.database import AppDatabase
from tiklocal.services.library_index import MediaIndexStore
from tiklocal.services.url_list import UrlListStore


PASSWORD = 'private-screening-room'
NEW_PASSWORD = 'replacement-private-password'


def _record(uri: str, *, media_type: str, mtime: float) -> dict:
    name = uri.rsplit('/', 1)[-1]
    extension = '.' + name.rsplit('.', 1)[-1]
    return {
        'uri': uri,
        'source_id': '@default',
        'rel_path': uri.split('/', 1)[-1],
        'filename': name,
        'parent_path': '',
        'media_type': media_type,
        'extension': extension,
        'size_bytes': 1,
        'mtime': mtime,
        'captured_at': mtime,
        'captured_local_date': '2024-05-04',
    }


def _store(tmp_path) -> MediaIndexStore:
    database = AppDatabase(tmp_path / 'index.sqlite3')
    database.migrate()
    return MediaIndexStore(database)


def test_detail_neighbours_follow_the_index_order(tmp_path):
    """The page order is ``mtime DESC, uri``, and a neighbour must match it."""
    store = _store(tmp_path)
    store.replace_snapshot([
        _record('@default/a.mp4', media_type='video', mtime=300.0),
        _record('@default/b.mp4', media_type='video', mtime=200.0),
        _record('@default/c.mp4', media_type='video', mtime=200.0),
        _record('@default/d.mp4', media_type='video', mtime=100.0),
        _record('@default/photo.jpg', media_type='image', mtime=250.0),
    ])

    # A tie on mtime falls back to the uri, so b precedes c.
    assert store.neighbors('video', '@default/c.mp4') == ('@default/b.mp4', '@default/d.mp4')
    assert store.neighbors('video', '@default/a.mp4') == (None, '@default/b.mp4')
    assert store.neighbors('video', '@default/d.mp4') == ('@default/c.mp4', None)
    # An item of another type never becomes a neighbour, and an unknown uri is safe.
    assert store.neighbors('image', '@default/photo.jpg') == (None, None)
    assert store.neighbors('video', '@default/photo.jpg') == (None, None)
    assert store.neighbors('video', '@default/missing.mp4') == (None, None)


def test_a_cancel_keeps_the_files_that_already_landed(tmp_path, monkeypatch):
    """Cancelling after a move must leave the job, the index and the queue agreeing."""
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    monkeypatch.setattr('tiklocal.services.downloader.probe_binary', lambda engine: (True, 'fake'))
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    manager = app.extensions['download_manager']
    manager.start()
    started = threading.Event()

    def cancel_after_a_file(self, job_id):
        started.set()
        self._cancel_events[job_id].wait(3.0)
        return 1, t('download.error.canceled'), ['clip.mp4']

    monkeypatch.setattr(type(manager), '_execute_download', cancel_after_a_file)
    indexed = []
    manager.on_outputs = indexed.extend
    try:
        job = manager.enqueue('https://example.com/clip.mp4')
        assert started.wait(3.0)
        manager.cancel(job['id'])
        final = _wait_for_status(manager, job['id'], {'canceled'})
    finally:
        manager.close()

    # The file is already in the library: the job must report it and hand it to the
    # indexer, or the media stays invisible until someone rescans by hand.
    assert final['file_count'] == 1
    assert final['output_files_rel'] == ['@default/clip.mp4']
    assert final['partial'] is True
    assert indexed == ['@default/clip.mp4']


def _wait_for_status(manager, job_id, statuses, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and job['status'] in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f'{job_id} never reached {statuses}: {manager.get_job(job_id)}')


def _login(client, password=PASSWORD):
    page = client.get('/login', query_string={'next': '/'})
    token = page.data.split(b'name="_csrf_token" value="', 1)[1].split(b'"', 1)[0].decode()
    client.post('/login', data={'_csrf_token': token, 'password': password, 'next': '/'})
    with client.session_transaction() as session:
        return session['_csrf_token']


def test_a_password_store_that_cannot_be_written_keeps_the_old_password(tmp_path, monkeypatch):
    auth_path = tmp_path / 'auth.json'
    store = AuthStore(auth_path)
    store.ensure(PASSWORD)
    real_replace = os.replace

    def refuse_the_swap(source, target):
        if str(target) == str(auth_path):
            raise PermissionError(13, 'sharing violation')
        return real_replace(source, target)

    monkeypatch.setattr(os, 'replace', refuse_the_swap)
    with pytest.raises(OSError):
        store.set_password(NEW_PASSWORD)
    monkeypatch.undo()

    # A half-written pair must not survive the failure, and the old password stays.
    assert sorted(path.name for path in tmp_path.iterdir()) == ['auth.json']
    assert AuthStore(auth_path).verify(PASSWORD)


def test_a_password_change_that_cannot_be_saved_answers_503(tmp_path, monkeypatch):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    auth_path = tmp_path / 'auth.json'
    AuthStore(auth_path).ensure(PASSWORD)
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media_root,
        'AUTH_ENABLED': True,
        'AUTH_PATH': auth_path,
    })
    client = app.test_client()
    csrf = _login(client)

    def boom(password):
        raise PermissionError(13, 'sharing violation')

    monkeypatch.setattr(app.extensions['auth_store'], 'set_password', boom)
    response = client.post('/api/auth/password', json={
        'current_password': PASSWORD,
        'new_password': NEW_PASSWORD,
        '_csrf_token': csrf,
    })

    assert response.status_code == 503
    assert response.get_json()['error'] == t('common.error.storage_unavailable')


def test_a_password_change_that_cannot_read_the_store_answers_503(tmp_path, monkeypatch):
    """The read half of the same fault: the password cannot even be checked."""
    media_root = tmp_path / 'media'
    media_root.mkdir()
    auth_path = tmp_path / 'auth.json'
    AuthStore(auth_path).ensure(PASSWORD)
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media_root,
        'AUTH_ENABLED': True,
        'AUTH_PATH': auth_path,
    })
    client = app.test_client()
    csrf = _login(client)

    def unreadable(password):
        raise RuntimeError(f'Authentication configuration cannot be read: {auth_path}')

    monkeypatch.setattr(app.extensions['auth_store'], 'verify', unreadable)
    response = client.post('/api/auth/password', json={
        'current_password': PASSWORD,
        'new_password': NEW_PASSWORD,
        '_csrf_token': csrf,
    })

    assert response.status_code == 503
    assert response.get_json()['error'] == t('common.error.storage_unavailable')


def test_a_login_limiter_read_never_grows_the_table():
    """retry_after() runs on every attempt, including unauthenticated pairing."""
    limiter = LoginAttemptLimiter(limit=2, window_seconds=60)

    for index in range(50):
        assert limiter.retry_after(f'10.0.0.{index}') == 0
    assert len(limiter._attempts) == 0

    limiter.record_failure('10.0.0.1')
    limiter.record_failure('10.0.0.1')
    assert limiter.retry_after('10.0.0.1') > 0
    assert limiter.retry_after('10.0.0.2') == 0

    for index in range(LoginAttemptLimiter.MAX_KEYS + 50):
        limiter.record_failure(f'172.16.0.{index}')
    assert len(limiter._attempts) <= LoginAttemptLimiter.MAX_KEYS + 1


def test_pulling_the_queue_waits_for_a_concurrent_add(tmp_path, monkeypatch):
    """``pull`` rewrites the whole list, so it must hold the same lock as ``add``."""
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'urls.json')
    server_file = tmp_path / 'server-urls.txt'
    server_file.write_text('https://example.com/first.mp4\n', encoding='utf-8')
    monkeypatch.setattr(store, 'source_path', lambda: str(server_file))
    holding = threading.Event()
    release = threading.Event()

    def hold_the_lock():
        with url_list._LIST_LOCK:
            holding.set()
            release.wait(5.0)

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    try:
        assert holding.wait(5.0)
        pulled = []
        reader = threading.Thread(target=lambda: pulled.append(store.pull()['lines']))
        reader.start()
        # The pull must still be blocked while the lock is held: an add() that read
        # the list in the window where _write() has moved the store aside would merge
        # into an empty list and drop the URL it was asked to keep.
        reader.join(0.2)
        assert reader.is_alive(), 'pull() rewrote the store without the list lock'
        release.set()
        reader.join(5.0)
        assert pulled and pulled[0] == ['https://example.com/first.mp4']
    finally:
        release.set()
        holder.join(5.0)


def test_a_bulk_overwrite_never_hides_the_queue(tmp_path, monkeypatch):
    """The backup used to be made by renaming the store, so readers saw no file."""
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'urls.json')
    store.replace(['https://example.com/first.mp4'])
    seen = []
    real_replace = os.replace

    def observing_replace(source, target):
        if Path(target) == store.store_path:
            seen.append(store.store_path.exists())
        return real_replace(source, target)

    monkeypatch.setattr('tiklocal.services.url_list.os.replace', observing_replace)
    store.replace(['https://example.com/second.mp4'])

    # Copying the backup leaves the queue readable for the whole rewrite.
    assert seen == [True], 'the queue disappeared while it was being replaced'
    assert store.lines() == ['https://example.com/second.mp4']


def test_image_neighbours_include_live_photos(tmp_path):
    """Live photos pair with images in image-shaped views, so they are neighbours."""
    store = _store(tmp_path)
    store.replace_snapshot([
        _record('@default/a.jpg', media_type='image', mtime=300.0),
        _record('@default/b.jpg', media_type='live', mtime=250.0),
        _record('@default/c.jpg', media_type='image', mtime=200.0),
    ])

    assert store.neighbors('image', '@default/a.jpg') == (None, '@default/b.jpg')
    assert store.neighbors('image', '@default/b.jpg') == ('@default/a.jpg', '@default/c.jpg')
    assert store.neighbors('image', '@default/c.jpg') == ('@default/b.jpg', None)
    # A video walk must not pick up a live photo.
    assert store.neighbors('video', '@default/a.jpg') == (None, None)


def test_a_full_limiter_never_clears_a_live_block():
    """Evicting a bucket that is still blocking an address would reset the block."""
    limiter = LoginAttemptLimiter(limit=2, window_seconds=600)
    limiter.record_failure('blocked')
    limiter.record_failure('blocked')
    assert limiter.retry_after('blocked') > 0

    for index in range(LoginAttemptLimiter.MAX_KEYS + 50):
        limiter.record_failure(f'10.0.0.{index}')

    assert limiter.retry_after('blocked') > 0, 'the table ceiling unblocked a live block'
    assert len(limiter._attempts) <= LoginAttemptLimiter.MAX_KEYS


def test_a_locked_auth_file_falls_back_to_the_cached_copy(tmp_path, monkeypatch):
    """A sharing violation must not lock the owner out of their own instance."""
    auth_path = tmp_path / 'auth.json'
    store = AuthStore(auth_path)
    store.ensure(PASSWORD)
    assert store.verify(PASSWORD)
    revision = store.revision
    real_stat = Path.stat

    def sharing_violation(self, *args, **kwargs):
        if self == auth_path:
            raise PermissionError(13, 'sharing violation')
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', sharing_violation)

    assert store.verify(PASSWORD)
    assert store.revision == revision


@pytest.mark.skipif(os.name == 'nt', reason='os.chmod only toggles the read-only flag on Windows')
def test_the_password_file_is_private_before_it_is_published(tmp_path, monkeypatch):
    """The mode must travel with the temporary file, not be applied after the swap."""
    store = AuthStore(tmp_path / 'auth.json')
    store.ensure(PASSWORD)
    modes = []
    real_replace = os.replace

    def observing_replace(source, target):
        modes.append(stat.S_IMODE(Path(source).stat().st_mode))
        return real_replace(source, target)

    monkeypatch.setattr('tiklocal.services.json_storage.os.replace', observing_replace)
    store.set_password(NEW_PASSWORD)

    assert modes == [0o600]
