"""The first scan must not hold the port hostage.

A library of 180k files takes minutes just to walk, and the scan used to run inside
``create_app()``: the server answered nothing until it finished, which reads as a
broken deployment rather than as "still starting". These tests pin the two halves of
the fix -- the port opens while the scan runs, and a page says so -- and the fact that
the banner goes away on its own once the scan lands.
"""

import os
import threading

import pytest

from tiklocal.app import create_app
from tiklocal.services import library_index as library_index_module

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def media_root(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"not really a photo")
    return root


@pytest.fixture()
def instance_root(monkeypatch):
    # The default instance directory is ~/.tiklocal, which a sandboxed test run may
    # not be allowed to write; keep every file inside the repository temp area.
    root = os.path.join(REPO_ROOT, f'.tmp-inst-index-build-{os.getpid()}')
    monkeypatch.setenv('TIKLOCAL_INSTANCE', root)
    yield root


def _blocking_sync(monkeypatch, started, release):
    """Replace the scan with one that parks until the test lets it finish."""
    original = library_index_module.LibraryIndexer.sync

    def slow_sync(self):
        started.set()
        assert release.wait(10), 'the test never released the blocked scan'
        return original(self)

    monkeypatch.setattr(library_index_module.LibraryIndexer, 'sync', slow_sync)


def _app(media_root, **overrides):
    config = {'TESTING': True, 'MEDIA_ROOT': media_root}
    config.update(overrides)
    return create_app(config)


def _wait_for_the_scan(app, timeout=10.0):
    """The scan runs on its own thread; wait for the flag it flips when it lands."""
    deadline = timeout
    while deadline > 0 and app.extensions.get('media_index_building') is not False:
        threading.Event().wait(0.05)
        deadline -= 0.05
    assert app.extensions.get('media_index_building') is False, 'the background scan never finished'


def test_the_server_answers_while_the_first_scan_is_still_walking(media_root, instance_root, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    _blocking_sync(monkeypatch, started, release)

    app = _app(media_root, INDEX_SYNC_BACKGROUND=True)
    client = app.test_client()
    try:
        assert started.wait(10), 'the background scan never started'

        # The scan is parked mid-walk and the app is already serving.
        stats = client.get('/api/library/stats')
        assert stats.status_code == 200
        assert stats.get_json()['index_building'] is True
        page = client.get('/')
        assert page.status_code == 200
        assert 'id="index-building"' in page.get_data(as_text=True)
        assert 'index_build.js' in page.get_data(as_text=True)
    finally:
        release.set()

    _wait_for_the_scan(app)
    assert app.extensions['media_index_sync'] is not None

    after = client.get('/api/library/stats').get_json()
    assert after['index_building'] is False
    assert after['indexed_total'] == 1
    # A finished scan must not leave the banner behind on a fresh render.
    assert 'id="index-building"' not in client.get('/').get_data(as_text=True)


def test_a_scan_that_never_started_says_nothing(media_root, instance_root):
    """Inline mode is what the tests and ``create_app`` in a shell use: by the time
    the caller holds the app, the index is already there and there is nothing to
    announce."""
    app = _app(media_root)
    assert app.extensions['media_index_building'] is False
    assert app.extensions['media_index_sync'] is not None
    client = app.test_client()
    body = client.get('/').get_data(as_text=True)
    assert 'id="index-building"' not in body
    assert client.get('/api/library/stats').get_json()['index_building'] is False


def test_a_failed_background_scan_reports_instead_of_hanging(media_root, instance_root, monkeypatch):
    def broken_sync(self):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(library_index_module.LibraryIndexer, 'sync', broken_sync)

    app = _app(media_root, INDEX_SYNC_BACKGROUND=True)
    _wait_for_the_scan(app)
    assert 'No space left on device' in app.extensions['media_index_error']
    # The server keeps serving the previous index rather than refusing to start.
    assert app.test_client().get('/api/library/stats').status_code == 200
    assert 'id="index-building"' not in app.test_client().get('/').get_data(as_text=True)
