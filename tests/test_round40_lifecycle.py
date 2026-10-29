"""Regressions for the round-40 review: resources that outlive their owner."""
import json
import threading
import time

import pytest

from tiklocal.app import create_app
from tiklocal.services import radio as radio_module
from tiklocal.services.library_index import MediaProbeWorker
from tiklocal.services.radio import AudioMetadata, RadioService


@pytest.fixture
def app(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root})


class _AlwaysUnprobed:
    """A store whose only row can never be probed: the worst case for run_once."""

    def __init__(self):
        self.calls = 0

    def unprobed(self, limit, exclude=()):
        self.calls += 1
        return [{'uri': '@default/broken.jpg', 'media_type': 'image', 'mtime': 1.0}]

    def set_dimensions(self, results):
        raise AssertionError('a deferred row must never be written')


def test_probe_backoff_attempts_stay_bounded():
    """Doubling 2**(attempts-1) overflowed a float after ~1024 failed passes.

    The OverflowError escaped the per-row guard and aborted the whole pass, which
    meant one undecodable file could stop every other file from ever being probed.
    """
    worker = MediaProbeWorker(None, None)

    for _ in range(1500):
        worker._defer_probe('@default/broken.jpg', 'no probe tool', base_delay=60.0)

    attempts, retry_at = worker._backoff['@default/broken.jpg']
    assert attempts == 12
    assert retry_at > time.monotonic()


def test_a_fully_deferred_batch_stops_instead_of_spinning(monkeypatch):
    """Every row in the batch was backed off: re-querying returned the same rows."""
    worker = MediaProbeWorker(None, _AlwaysUnprobed())
    monkeypatch.setattr('tiklocal.services.library_index.probe_media_dims', lambda *args, **kwargs: (None, None))

    started = time.monotonic()
    assert worker.run_once(batch=1) == 0
    assert time.monotonic() - started < 5.0


def test_radio_metadata_cache_survives_concurrent_probes(tmp_path, monkeypatch):
    """8 radio-probe threads raced move_to_end() against popitem() and raised KeyError.

    A KeyError there escaped through pool.map into the view, so one unlucky page
    returned 500 and the radio list went blank.
    """
    monkeypatch.setattr(radio_module, 'METADATA_CACHE_LIMIT', 8)
    service = RadioService(None, None, None)
    monkeypatch.setattr(service, '_probe_audio_metadata', lambda path: AudioMetadata())
    paths = []
    for index in range(40):
        path = tmp_path / f'track-{index:02d}.mp3'
        path.write_bytes(b'x')
        paths.append(path)

    errors = []

    def hammer(chunk):
        try:
            for path in chunk:
                service.metadata_for(path)
        except Exception as error:  # noqa: BLE001 - the test asserts nothing escaped
            errors.append(error)

    threads = [threading.Thread(target=hammer, args=(paths[offset::8],)) for offset in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert len(service._metadata_cache) <= radio_module.METADATA_CACHE_LIMIT


def test_finished_jobs_do_not_persist_their_download_payload(app, monkeypatch):
    """A completed gallery job kept its CDN URLs in every later progress write."""
    monkeypatch.setattr('tiklocal.services.downloader.probe_binary', lambda engine: (True, 'fake'))
    manager = app.extensions['download_manager']
    monkeypatch.setattr(type(manager), '_execute_download', lambda self, job_id: (0, '', ['@default/one.jpg']))
    monkeypatch.setattr(manager, '_record_job_sources_on_success', lambda context: None)
    manager.start()
    try:
        job = manager.enqueue('https://example.com/gallery', payload={'images': ['https://cdn.example.com/a.jpg']})
        deadline = time.monotonic() + 10.0
        terminal = {'success', 'failed', 'canceled'}
        while time.monotonic() < deadline and manager.get_job(job['id'])['status'] not in terminal:
            time.sleep(0.02)
        assert manager.get_job(job['id'])['status'] == 'success'

        saved = json.loads(manager.history_store.store_path.read_text(encoding='utf-8'))
        entries = saved.get('jobs') if isinstance(saved, dict) else saved
        finished = [item for item in entries if item.get('id') == job['id']]
        assert finished and 'download_payload' not in finished[0]
    finally:
        manager.close()
