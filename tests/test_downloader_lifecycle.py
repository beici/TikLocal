import sys
import threading
import time

import pytest

from tiklocal.app import create_app
from tiklocal.i18n import t


@pytest.fixture
def app(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root})


@pytest.fixture
def manager(app, monkeypatch):
    # Keep enqueue() fast: probing spawns a subprocess per job.
    monkeypatch.setattr('tiklocal.services.downloader.probe_binary', lambda engine: (True, 'fake'))
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
    assert final['error_message'] == t('download.error.canceled')


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


def test_cancel_message_follows_the_locale_the_job_was_queued_in(app, manager, monkeypatch):
    # The terminal messages are built on a worker thread, where ``t()`` has no request
    # context and always answers in the default locale. The job records the enqueuing
    # request's locale so an English UI keeps its English "Canceled." text.
    from tiklocal.services import downloader as downloader_module

    started = threading.Event()

    def slow_execute(self, job_id):
        started.set()
        self._cancel_events[job_id].wait(3.0)
        return 1, '', []

    monkeypatch.setattr(type(manager), '_execute_download', slow_execute)
    with app.test_request_context('/', headers={'Accept-Language': 'en-US,en;q=0.9'}):
        job = manager.enqueue('https://example.com/video.mp4')
    assert job['locale'] == 'en'
    assert started.wait(3.0)

    manager.cancel(job['id'])
    final = wait_for_status(manager, job['id'], {'canceled'})
    assert final['error_message'] == 'Canceled.'
    assert final['error_message'] == downloader_module.translate('en', 'download.error.canceled')
