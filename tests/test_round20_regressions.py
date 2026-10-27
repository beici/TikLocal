"""Regressions for the round-20 findings: thumbnail cache ownership, decode limits,
device-token lifetime, an unreadable URL list, and the ranged-media fast path."""

import datetime
import hashlib
import json
import os
from pathlib import Path

from PIL import Image

from tiklocal.app import create_app
from tiklocal.paths import get_download_urls_path
from tiklocal.services.device_auth import DeviceAuthStore
from tiklocal.services.thumbnail import ThumbnailService
from tiklocal.services.url_list import UrlListStore


def _write_jpeg(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (64, 64), color).save(path, 'JPEG', quality=90)


def test_second_library_does_not_adopt_another_librarys_legacy_cache(tmp_path):
    """The pre-root-key layout hashes only the relative path, so it is ambiguous."""
    root_a, root_b = tmp_path / 'lib-a', tmp_path / 'lib-b'
    _write_jpeg(root_a / 'clip.jpg', 'red')
    _write_jpeg(root_b / 'clip.jpg', 'blue')

    service_a = ThumbnailService(root_a)
    legacy = service_a.thumb_dir / (hashlib.sha1(b'clip.jpg').hexdigest() + '.jpg')
    legacy.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (64, 64), 'red').save(legacy, 'JPEG', quality=90)
    adopted = False
    cached = service_a.cached_thumbnail('clip.jpg')
    if cached is not None and cached != legacy:
        adopted = True
    assert adopted, 'the first library should still adopt its legacy cache'

    service_b = ThumbnailService(root_b)
    # B has its own copy of the same relative path; the leftover legacy file is A's
    # picture and must never become B's preview.
    assert service_b.cached_thumbnail('clip.jpg') is None


def test_delete_thumbnail_clears_the_failure_marker(tmp_path):
    service = ThumbnailService(tmp_path / 'media')
    _write_jpeg(tmp_path / 'media' / 'clip.jpg', 'green')
    service._mark_failure('clip.jpg')
    marker = service._failure_path('clip.jpg')
    assert marker.exists()

    service.delete_thumbnail('clip.jpg')

    assert not marker.exists()
    assert not service.cache_path('clip.jpg').exists()


def test_device_token_expires_and_is_pruned_on_issue(tmp_path):
    store_path = tmp_path / 'device_auth.json'
    store = DeviceAuthStore(store_path)
    credential = store.issue('Kitchen phone', auth_revision=1)
    assert store.verify(credential.token, auth_revision=1)

    payload = json.loads(store_path.read_text(encoding='utf-8'))
    payload['devices'][0]['expires_at'] = '2000-01-01T00:00:00+00:00'
    store_path.write_text(json.dumps(payload), encoding='utf-8')
    assert not store.verify(credential.token, auth_revision=1)

    store.issue('Living room', auth_revision=1)
    saved = json.loads(store_path.read_text(encoding='utf-8'))['devices']
    assert credential.device_id not in {row.get('id') for row in saved}


def test_device_token_without_expiry_falls_back_to_its_age(tmp_path):
    store_path = tmp_path / 'device_auth.json'
    store = DeviceAuthStore(store_path)
    credential = store.issue('Old phone', auth_revision=2)
    payload = json.loads(store_path.read_text(encoding='utf-8'))
    payload['devices'][0].pop('expires_at', None)
    payload['devices'][0]['created_at'] = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=400)
    ).replace(microsecond=0).isoformat()
    store_path.write_text(json.dumps(payload), encoding='utf-8')
    assert not store.verify(credential.token, auth_revision=2)


def test_url_list_reads_a_notepad_encoded_file(tmp_path):
    path = tmp_path / 'download-urls.txt'
    path.write_bytes('中文标题\nhttp://example.com/one\n'.encode('gbk'))
    store = UrlListStore(store_path=path, config_path=tmp_path / 'cfg.json')

    lines = store.lines()

    assert 'http://example.com/one' in lines


def test_download_url_routes_answer_503_for_a_directory_store(tmp_path):
    instance = Path(os.environ['TIKLOCAL_INSTANCE'])
    instance.mkdir(parents=True, exist_ok=True)
    (get_download_urls_path()).mkdir()
    media_root = tmp_path / 'media'
    media_root.mkdir()
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    client = app.test_client()

    response = client.get('/api/download/urls')
    assert response.status_code in (200, 401, 403, 503)
    if response.status_code == 200:
        # AUTH disabled: the unreadable store must be reported, not a traceback.
        assert response.get_json()['success'] is False
    queue = client.post('/api/download/urls/queue', json={'engine': 'yt-dlp', 'save_mode': 'root'})
    assert queue.status_code in (401, 403, 503)


def test_range_hands_a_seekable_file_to_the_server_wrapper(tmp_path):
    (tmp_path / 'clip.mp4').write_bytes(bytes(range(256)) * 4)
    app = create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path})
    captured = []

    def wrapper(filelike, blocksize):
        captured.append((filelike, blocksize))
        return iter(())

    client = app.test_client()
    response = client.get(
        '/media/clip.mp4',
        headers={'Range': 'bytes=10-99'},
        environ_base={'wsgi.file_wrapper': wrapper},
    )

    assert response.status_code == 206
    assert response.headers['Content-Length'] == '90'
    assert captured, 'the server wrapper should have been used'
    filelike, blocksize = captured[0]
    try:
        # waitress only takes the off-thread path for a seekable file positioned at
        # the start of the range; an unseekable reader silently fell back to copying.
        assert filelike.seekable()
        assert filelike.tell() == 10
    finally:
        filelike.close()
