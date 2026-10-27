"""Regressions for the round-18 findings (HTTP caching, validators, compression,
radio loudness decoding, download process trees)."""

import gzip
import threading
import time
from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.services import radio_energy


def _client(tmp_path):
    (tmp_path / 'a.jpg').write_bytes(b'\xff\xd8\xff\xd9')
    (tmp_path / 'clip.mp4').write_bytes(b'0123456789' * 200)
    return create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()


def test_pages_are_gzipped_and_mark_accept_encoding(tmp_path):
    """Every response used to go out uncompressed, so a first visit moved four
    times the bytes it needed to."""
    client = _client(tmp_path)
    plain = client.get('/library')
    assert plain.headers.get('Content-Encoding') is None
    packed = client.get('/library', headers={'Accept-Encoding': 'gzip'})
    assert packed.headers.get('Content-Encoding') == 'gzip'
    assert 'Accept-Encoding' in packed.headers.get('Vary', '')
    assert len(packed.data) < len(plain.data)
    assert b'<html' in gzip.decompress(packed.data)


def test_pages_carry_an_etag_so_a_repeat_visit_is_a_304(tmp_path):
    """`no-cache` without a validator meant every navigation re-downloaded the
    whole page, including the ~73 KB inline i18n catalog."""
    client = _client(tmp_path)
    first = client.get('/library')
    etag = first.headers.get('ETag')
    assert etag
    again = client.get('/library', headers={'If-None-Match': etag})
    assert again.status_code == 304
    assert again.data == b''


def test_range_responses_carry_the_same_validators_as_the_full_response(tmp_path):
    """The 200 path goes through `send_file` and had ETag/Last-Modified; the
    hand-built 206/416 paths had neither, so a resumed download could not
    revalidate."""
    client = _client(tmp_path)
    full = client.get('/media/clip.mp4')
    assert full.headers.get('ETag')

    partial = client.get('/media/clip.mp4', headers={'Range': 'bytes=0-9'})
    assert partial.status_code == 206
    assert partial.headers.get('ETag') == full.headers.get('ETag')
    assert partial.headers.get('Last-Modified') == full.headers.get('Last-Modified')

    unsatisfiable = client.get('/media/clip.mp4', headers={'Range': 'bytes=999999-'})
    assert unsatisfiable.status_code == 416
    assert unsatisfiable.headers.get('ETag') == full.headers.get('ETag')
    assert unsatisfiable.headers.get('Last-Modified') == full.headers.get('Last-Modified')


def test_head_on_the_feed_does_not_build_the_feed(tmp_path):
    """Flask maps HEAD onto the GET view, so a probe paid for the whole candidate
    pool and JSON serialisation to learn nothing."""
    client = _client(tmp_path)
    head = client.head('/api/feed/mix')
    assert head.status_code == 200
    assert head.data == b''


def test_radio_energy_uses_the_ffmpeg_the_project_ships(monkeypatch):
    """Gating on `shutil.which('ffmpeg')` hid the bundled imageio-ffmpeg binary,
    so every non-WAV track lost its loudness envelope (the radio visuals then
    fell back to a flat curve)."""
    monkeypatch.setattr('tiklocal.services.download_tools.resolve_tool',
                        lambda command: [r'C:\bundled\ffmpeg.exe'])
    monkeypatch.setattr('shutil.which', lambda command: None)
    assert radio_energy._ffmpeg_argv() == [r'C:\bundled\ffmpeg.exe']


class _StalledProcess:
    """Stands in for an ffmpeg that never produces another byte."""

    def __init__(self):
        self.release = threading.Event()
        self.stdout = self
        self.returncode = None
        self.killed = False

    def read(self, size):
        self.release.wait(10.0)
        return b''

    def close(self):
        pass

    def kill(self):
        self.killed = True
        self.release.set()

    def wait(self, timeout=None):
        self.release.wait(timeout or 1.0)
        return 0


def test_radio_energy_abandons_a_stalled_decode(monkeypatch):
    """`stdout.read()` has no deadline of its own: a demuxer stuck on a dead
    share used to hold a waitress worker forever."""
    stalled = _StalledProcess()
    monkeypatch.setattr(radio_energy.sp, 'Popen', lambda *args, **kwargs: stalled)
    monkeypatch.setattr(radio_energy, 'DECODE_TIMEOUT_SEC', 0.2)

    started = time.monotonic()
    assert radio_energy._ffmpeg_powers(Path('tone.flac'), ['ffmpeg']) is None
    assert time.monotonic() - started < 5.0
    assert stalled.killed is True


def test_canceling_a_download_kills_the_whole_process_tree(monkeypatch):
    """TerminateProcess only reaches the direct child, so yt-dlp's ffmpeg merger
    kept writing into the job temp dir after a cancel."""
    from tiklocal.services.downloader import DownloadManager

    calls = []
    monkeypatch.setattr('tiklocal.services.downloader.sp.run',
                        lambda args, **kwargs: calls.append(args))
    DownloadManager._kill_process_tree_windows(4321)
    assert calls == [['taskkill', '/T', '/F', '/PID', '4321']]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    monkeypatch.setattr('tiklocal.services.downloader.probe_binary', lambda engine: (True, 'fake'))
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    download_manager = app.extensions['download_manager']
    download_manager.start()
    try:
        yield download_manager
    finally:
        download_manager.close()


def _wait_for(manager, job_id, statuses, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and job['status'] in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f'{job_id} never reached {statuses}: {manager.get_job(job_id)}')


def test_index_failure_marks_the_job_partial(manager, monkeypatch):
    """The bytes were already in the library, but the job looked like a plain
    failure, so the retry button re-downloaded and duplicated the file."""
    monkeypatch.setattr(type(manager), '_execute_download',
                        lambda self, job_id: (0, '', ['clip.mp4']))

    def boom(paths):
        raise RuntimeError('index is read-only')

    manager.on_outputs = boom
    job = manager.enqueue('https://example.com/v.mp4')
    final = _wait_for(manager, job['id'], {'failed'})
    assert final['partial'] is True
    assert 'media registration failed' in final['error_message']
