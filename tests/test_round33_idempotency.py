"""Repeating a request must not repeat its effect."""
import threading
import time

import pytest

from tiklocal.app import create_app


def _wait_for_job(client, job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        payload = client.get(f"/api/download/jobs/{job_id}").get_json()
        job = (payload or {}).get("data", {}).get("job")
        if job and job.get("status") != "queued":
            return job
        time.sleep(0.02)
    return job


@pytest.fixture
def client(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))

    def fake_execute_download(self, job_id):  # noqa: ARG001
        (self.media_root / "mock-output.mp4").write_bytes(b"video")
        return 0, "", "mock-output.mp4"

    monkeypatch.setattr("tiklocal.services.downloader.DownloadManager._execute_download", fake_execute_download)
    app = create_app({"TESTING": True, "MEDIA_ROOT": media_root})
    manager = app.extensions['download_manager']
    manager.start()
    try:
        yield app.test_client()
    finally:
        manager.close()


def test_the_same_url_is_queued_once(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    release = threading.Event()

    def blocking_execute(self, job_id):  # noqa: ARG001
        release.wait(5)
        return 0, "", ""

    monkeypatch.setattr("tiklocal.services.downloader.DownloadManager._execute_download", blocking_execute)
    app = create_app({"TESTING": True, "MEDIA_ROOT": media_root})
    manager = app.extensions['download_manager']
    manager.start()
    try:
        first = manager.enqueue("https://example.com/same", engine="gallery-dl")
        second = manager.enqueue("https://example.com/same", engine="gallery-dl")
        assert second["id"] == first["id"]
        assert second.get("deduplicated") is True
        assert len([job for job in manager.list_jobs(limit=50) if job["url"].endswith("/same")]) == 1
    finally:
        release.set()
        manager.close()


def test_retry_cannot_spawn_a_second_job(client, monkeypatch):
    def fail_execute(self, job_id):  # noqa: ARG001
        return 1, "network", ""

    monkeypatch.setattr("tiklocal.services.downloader.DownloadManager._execute_download", fail_execute)
    created = client.post("/api/download/jobs", json={"url": "https://example.com/retry-me", "engine": "gallery-dl"})
    job_id = created.get_json()["data"]["job"]["id"]
    failed = _wait_for_job(client, job_id)
    assert failed["status"] == "failed"

    first = client.post(f"/api/download/jobs/{job_id}/retry")
    assert first.status_code == 200
    follow_up = first.get_json()["data"]["job"]
    assert follow_up["retry_of"] == job_id

    # The original card is spent: a second click (or a second tab) must not create
    # a third job for the same failure.
    second = client.post(f"/api/download/jobs/{job_id}/retry")
    assert second.status_code == 400
    assert second.get_json()["success"] is False
    assert client.get(f"/api/download/jobs/{job_id}").get_json()["data"]["job"]["retried_by"] == follow_up["id"]
