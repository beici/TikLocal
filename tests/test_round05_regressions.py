# -*- coding: utf-8 -*-
"""Behaviours hardened by the fifth audit round.

Each test pins a failure mode that used to be silent: HTML error bodies for JSON
clients, a deleted collection falling back to the whole library, the download
queue accepting localhost URLs, thumbnails re-running ffmpeg for a broken file on
every request, and a local media index storing host absolute paths.
"""

import json

import pytest

from tiklocal.app import create_app
from tiklocal.services.downloader import MASKED_SECRET, validate_download_url
from tiklocal.services.library import LibraryService
from tiklocal.services.thumbnail import ThumbnailService


@pytest.fixture()
def client(tmp_path):
    return create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()


def test_unknown_api_route_answers_with_json(client):
    response = client.get('/api/does-not-exist')
    assert response.status_code == 404
    assert response.mimetype == 'application/json'
    payload = response.get_json()
    assert payload['success'] is False
    assert payload['error']


def test_api_method_mismatch_answers_with_json(client):
    # The queue endpoint is POST-only; a GET used to return Werkzeug's HTML page.
    response = client.get('/api/download/urls/queue')
    assert response.status_code == 405
    assert response.mimetype == 'application/json'
    assert response.get_json()['success'] is False


def test_missing_collection_does_not_return_the_whole_library(client, tmp_path):
    (tmp_path / 'clip.mp4').write_bytes(b'x')
    response = client.get('/api/library/items?scope=collection&collection_id=missing')
    assert response.status_code == 404
    assert response.get_json()['success'] is False


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:8000/api/library/stats',
    'http://localhost/video',
    'http://[::1]/video',
    'http://169.254.169.254/latest/meta-data',
])
def test_download_queue_rejects_local_targets(url):
    # yt-dlp runs on the server, so a localhost URL reaches services that never
    # expected a request from the downloader.
    validated, error = validate_download_url({'url': url})
    assert validated is None
    assert error


def test_download_queue_still_accepts_public_urls():
    validated, error = validate_download_url({'url': 'https://example.com/clip.mp4'})
    assert error is None
    assert validated['url'] == 'https://example.com/clip.mp4'


def test_thumbnail_failure_is_remembered(tmp_path, monkeypatch):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'broken.mp4').write_bytes(b'not a video')
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'data'))
    service = ThumbnailService(media_root)
    calls = []

    def fake_generate(uri, **kwargs):
        calls.append(uri)
        return None

    monkeypatch.setattr(service, 'generate_thumbnail', fake_generate)
    assert isinstance(service.get_thumbnail('broken.mp4')[0], bytes)
    assert isinstance(service.get_thumbnail('broken.mp4')[0], bytes)
    # The second request must not spawn ffmpeg again for the same unchanged file.
    assert calls == ['broken.mp4']


def test_media_paths_outside_every_source_are_not_published(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    library = LibraryService(media_root=media_root)
    outside = tmp_path / 'outside' / 'clip.mp4'
    outside.parent.mkdir()
    outside.write_bytes(b'x')
    # A host absolute path used to become the URI: /media 404s and the path leaks.
    assert library.get_relative_path(outside) == ''
    assert library.get_relative_path(media_root / 'clip.mp4') == '@default/clip.mp4'


def test_one_unreadable_folder_does_not_empty_the_scan(tmp_path, monkeypatch):
    import os

    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'ok.mp4').write_bytes(b'x')
    library = LibraryService(media_root=media_root)
    real_walk = os.walk

    def walk(top, onerror=None, **kwargs):
        # A drive root always contains System Volume Information, which is unreadable.
        if onerror is not None:
            onerror(PermissionError(13, 'Access is denied', str(media_root / 'locked')))
        return real_walk(top, onerror=onerror, **kwargs)

    monkeypatch.setattr(os, 'walk', walk)
    names = [path.name for path in library.scan_source(library.sources[0])]
    assert 'ok.mp4' in names


def test_scan_does_not_follow_junctions(tmp_path):
    import subprocess

    media_root = tmp_path / 'media'
    media_root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'hidden.mp4').write_bytes(b'x')
    link = media_root / 'linked'
    created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                             capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip('junctions are not available for this user')
    try:
        library = LibraryService(media_root=media_root)
        names = [path.name for path in library.scan_source(library.sources[0])]
        # Following the junction indexed the file under a host absolute path, which
        # then 404'd on /media and leaked local directory names.
        assert 'hidden.mp4' not in names
    finally:
        subprocess.run(['cmd', '/c', 'rmdir', str(link)], capture_output=True, text=True)


def test_download_config_masks_the_sidecar_token(client):
    manager = client.application.extensions['download_manager']
    manager.update_config({'tiktok_downloader': {'base_url': 'https://tiktok.example.com',
                                                 'token': 'secret-token'}})

    payload = client.get('/api/download/config').get_json()['data']
    section = payload['config']['tiktok_downloader']
    # The settings page used to receive the raw token and echo it back on save.
    assert section['token'] == MASKED_SECRET
    assert section['has_token'] is True
    assert MASKED_SECRET not in json.dumps(payload['effective'])

    client.post('/api/download/config', json=payload['config'])
    assert manager.get_config()['tiktok_downloader']['token'] == 'secret-token'

    tiktok = client.get('/api/download/tiktok/config').get_json()['data']['tiktok_downloader']
    assert tiktok['token'] == MASKED_SECRET
