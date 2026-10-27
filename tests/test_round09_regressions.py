"""Regressions for the round-09 findings (cache headers, sync safety, download recovery)."""

from types import SimpleNamespace

import pytest
from PIL import Image

from tiklocal.app import create_app
from tiklocal.services.download_tools import parse_output_path
from tiklocal.services.downloader import DownloadManager
from tiklocal.services.url_list import MAX_LINES, UrlListStore


@pytest.fixture
def client(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    Image.new('RGB', (32, 32), 'red').save(media_root / 'photo.jpg')
    (media_root / 'notes.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding='utf-8')
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()


def test_files_that_are_not_media_are_downloaded_instead_of_rendered(client):
    response = client.get('/media/notes.svg')
    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'application/octet-stream'
    assert 'attachment' in response.headers['Content-Disposition']


def test_images_are_still_served_inline(client):
    response = client.get('/media/photo.jpg')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('image/jpeg')


def test_thumbnails_revalidate_with_an_etag_and_misses_are_not_cached(client):
    first = client.get('/thumb?uri=photo.jpg')
    assert first.status_code == 200
    assert first.headers['Cache-Control'] == 'private, no-cache'
    etag = first.headers.get('ETag')
    assert etag, 'a revalidating response is useless without a validator'
    again = client.get('/thumb?uri=photo.jpg', headers={'If-None-Match': etag})
    assert again.status_code == 304
    assert not again.get_data()

    missing = client.get('/thumb?uri=missing.jpg')
    # A missing source renders the placeholder, which must never be cached for
    # an hour: the real thumbnail may appear a second later.
    assert missing.status_code == 200
    assert 'no-store' in missing.headers['Cache-Control']


def test_bulk_url_list_writes_keep_a_backup_and_respect_the_cap(tmp_path):
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'url-list.json')
    store.add(['https://example.com/1'])
    store.add(['https://example.com/2'])
    store.replace(['https://example.com/3'])

    assert store.lines() == ['https://example.com/3']
    backup = (tmp_path / 'urls.txt.bak').read_text(encoding='utf-8')
    assert 'https://example.com/2' in backup

    with pytest.raises(ValueError):
        store.replace([f'https://example.com/{index}' for index in range(MAX_LINES + 1)])
    with pytest.raises(ValueError):
        store.add([f'https://example.org/{index}' for index in range(MAX_LINES)])


def test_pull_keeps_the_local_queue_recoverable(tmp_path):
    server_file = tmp_path / 'server.txt'
    server_file.write_text('https://example.com/server\n', encoding='utf-8')
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'url-list.json')
    store.add(['https://example.com/hand-written'])
    store.set_source_path(str(server_file))

    assert store.pull()['lines'] == ['https://example.com/server']
    backup = (tmp_path / 'urls.txt.bak').read_text(encoding='utf-8')
    assert 'https://example.com/hand-written' in backup


def test_yt_dlp_output_paths_are_read_from_the_move_and_skip_lines():
    moved = '[MoveFiles] Moving file "C:\\tmp\\a.mp4" to "D:\\media\\a.mp4"'
    assert parse_output_path(moved) == 'D:\\media\\a.mp4'
    skipped = '[download] D:\\media\\b.mp4 has already been downloaded'
    assert parse_output_path(skipped) == 'D:\\media\\b.mp4'


def test_temp_outputs_never_become_library_files(tmp_path):
    media_root = tmp_path / 'media'
    temp_dir = media_root / '.tiklocal-download-tmp' / 'job1'
    temp_dir.mkdir(parents=True)
    (media_root / 'album').mkdir()
    (media_root / 'album' / 'b.mp4').write_bytes(b'video')
    manager = SimpleNamespace(media_root=media_root, output_source_id='default')

    assert DownloadManager._to_media_relative(manager, str(temp_dir / 'a.mp4')) == ''
    assert DownloadManager._to_media_relative(manager, str(media_root / 'album' / 'b.mp4')).endswith('/album/b.mp4')


def test_author_sync_rejects_more_authors_than_it_can_serve_in_one_request(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': media_root,
        'TIKTOK_DOWNLOADER': {'base_url': 'http://127.0.0.1:9'},
    })
    items = [{'source_id': f'a{index}', 'profile_url': 'https://www.douyin.com/user/x'} for index in range(201)]

    response = app.test_client().post('/api/author/sync', json={'items': items})

    assert response.status_code == 400
    assert '200' in response.get_json()['error']
