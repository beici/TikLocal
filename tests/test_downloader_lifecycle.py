import sys
import threading
import time

import pytest

from tiklocal.app import create_app


@pytest.fixture
def manager(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    # Keep enqueue() fast: probing spawns a subprocess per job.
    monkeypatch.setattr('tiklocal.services.downloader.probe_binary', lambda engine: (True, 'fake'))
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    download_manager = app.extensions['download_manager']
    download_manager.start()
    try:
        yield download_manager
    finally:
        download_manager.close()


def wait_for_status(manager, job_id, statuses, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job and job['status'] in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f'{job_id} never reached {statuses}: {manager.get_job(job_id)}')


def test_cancel_wins_over_a_download_that_finishes_afterwards(manager, monkeypatch):
    started = threading.Event()

    def slow_execute(self, job_id):
        started.set()
        self._cancel_events[job_id].wait(3.0)
        return 1, 'Canceled.', []

    monkeypatch.setattr(type(manager), '_execute_download', slow_execute)
    job = manager.enqueue('https://example.com/video.mp4')

    assert started.wait(3.0)
    requested = manager.cancel(job['id'])
    assert requested is not None and requested['cancel_requested'] is True
    final = wait_for_status(manager, job['id'], {'canceled'})
    assert final['cancel_requested'] is True
    assert final['error_message'] == 'Canceled.'


def test_run_tool_enforces_a_wall_clock_deadline(manager, monkeypatch):
    manager._config['job_timeout_sec'] = 1
    command = [sys.executable, '-c', 'import time; time.sleep(30)']

    def slow_direct(self, job_id):
        return_code, error_message = self._run_tool(job_id, command, lambda line: None)
        return return_code, error_message, []

    monkeypatch.setattr(type(manager), '_execute_download_direct', slow_direct)
    job = manager.enqueue('https://example.com/slow.mp4', engine='tiktok-downloader')

    final = wait_for_status(manager, job['id'], {'failed'}, timeout=15.0)
    assert 'time limit' in final['error_message']
