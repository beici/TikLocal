# -*- coding: utf-8 -*-
"""Author profile page: identity header, works grid, avatar and profile refresh.

Fixture filenames use a placeholder blogger name (示例博主), never real exports.
"""

import gc
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from tiklocal.app import create_app
from tiklocal.services.tiktok_downloader import (
    AuthorAmbiguousError,
    TikTokDownloaderError,
    TikTokDownloadService,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# Author caches, the app database and auth files must stay inside the repo
# instead of the pytest temp tree.
INSTANCE_ROOT = REPO_ROOT / f'.tmp-inst-author-{os.getpid()}'
BLOGGER_FOLDER = 'UID100000000000001_示例博主_发布作品'
SIDECAR_URL = 'http://127.0.0.1:9/'
PROFILE_URL = 'https://www.douyin.com/user/MS4wLjABAAAAfake'


@pytest.fixture(scope='session', autouse=True)
def author_instance_root():
    """Own the instance root: start clean and leave nothing behind."""
    shutil.rmtree(INSTANCE_ROOT, ignore_errors=True)
    yield INSTANCE_ROOT
    # Windows keeps files delete-pending while sqlite handles are alive, so
    # collect first and retry a few times once every test frame is gone.
    gc.collect()
    for _ in range(5):
        shutil.rmtree(INSTANCE_ROOT, ignore_errors=True)
        if not INSTANCE_ROOT.exists():
            return
        time.sleep(0.1)


@pytest.fixture(autouse=True)
def author_instance(monkeypatch, tmp_path):
    """Give every test its own isolated instance directory under the repo."""
    instance = INSTANCE_ROOT / tmp_path.name
    shutil.rmtree(instance, ignore_errors=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    yield instance
    gc.collect()
    shutil.rmtree(instance, ignore_errors=True)


def _touch(path: Path, size: int = 2048) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {'.jpg', '.jpeg'}:
        Image.new('RGB', (48, 32), 'red').save(path)
    else:
        path.write_bytes(b'0' * size)
    stamp = time.time() - 120
    os.utime(path, (stamp, stamp))


def _blogger_app(tmp_path, files, config=None):
    """Build the app with one blogger export folder as its media source."""
    folder = tmp_path / 'media' / BLOGGER_FOLDER
    for name in files:
        _touch(folder / name)
    settings = {
        'TESTING': True,
        'MEDIA_ROOT': tmp_path / 'unused',
        'MEDIA_SOURCES': [{'id': 'blogger', 'name': BLOGGER_FOLDER, 'path': str(folder)}],
    }
    settings.update(config or {})
    app = create_app(settings)
    return app, app.test_client()


def _html(client, path='/author/blogger'):
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _routes_only_client(tmp_path, *, tiktok_service):
    """A bare app with only the author routes, so a missing sidecar is reachable."""
    from flask import Flask

    from tiklocal.paths import get_database_path
    from tiklocal.services.database import AppDatabase
    from tiklocal.services.library import LibraryService, build_media_sources
    from tiklocal.services.library_index import MediaIndexStore
    from tiklocal.web.author import register_author_routes

    media = tmp_path / 'routes-only'
    folder = media / BLOGGER_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    sources = [{'id': 'blogger', 'name': BLOGGER_FOLDER, 'path': str(folder)}]
    library_service = LibraryService(media, media_sources=build_media_sources(media, sources))
    media_index = MediaIndexStore(AppDatabase(get_database_path()))
    app = Flask(__name__)
    register_author_routes(
        app,
        library_service=library_service,
        media_index=media_index,
        tiktok_service=tiktok_service,
    )
    return app.test_client()


CANDIDATES = [
    {
        'nickname': 'puff', 'unique_id': 'demo0001', 'short_id': '', 'uid': '7001',
        'sec_uid': 'MS4wLjABAAAA', 'signature': '', 'avatar': 'https://example.test/a.jpg',
        'follower_count': 12345, 'total_favorited': 678,
    },
    {
        'nickname': 'puff', 'unique_id': 'demo0002', 'short_id': '37543', 'uid': '7002',
        'sec_uid': 'MS4wLjABAAAB', 'signature': '', 'avatar': '',
        'follower_count': 20, 'total_favorited': 3,
    },
]


def test_page_renders_folder_identity_and_works_grid(tmp_path):
    _, client = _blogger_app(tmp_path, [
        '2024-05-01 10.00.00-视频-示例博主-一.mp4',
        '2024-05-02 10.00.00-图集-示例博主-二_1.jpeg',
        '2024-05-02 10.00.00-图集-示例博主-二_2.jpeg',
        '2024-05-03 10.00.00-图文-示例博主-三.jpeg',
    ])

    html = _html(client)

    # Folder-derived identity: blogger name and the UID-shaped Douyin id.
    assert '示例博主' in html
    assert '抖音号: 100000000000001' in html
    assert '作品' in html
    # Three works: the two-member album collapses into its cover tile.
    assert html.count('class="author-tile"') == 3
    assert html.count('href="/detail/') == 1
    assert html.count('href="/image?uri=') == 2
    # Videos carry a play glyph, images do not.
    assert html.count('author-tile-play') == 1
    # No social actions on a local library.
    # The sync entry point is always on the page — hiding it until TikTokDownloader
    # was configured left the feature undiscoverable — but without an address it
    # opens a note pointing at Settings instead of the two actions and the picker.
    assert 'data-author-sync' in html
    assert 'data-author-sheet-profile' not in html
    assert 'class="author-sheet-note"' in html
    assert 'href="/settings/"' in html
    assert 'data-author-picker' not in html


def test_works_grid_lists_the_newest_post_first(tmp_path):
    """A whole download batch shares one mtime, so the grid must sort by publish time.

    The fixture stamps every file with the same modification time; without the
    publish-time sort the grid would fall back to file-name order.
    """
    _, client = _blogger_app(tmp_path, [
        '2024-03-01 10.00.00-视频-示例博主-早.mp4',
        '2024-05-09 10.00.00-视频-示例博主-晚.mp4',
        '2024-04-02 10.00.00-图集-示例博主-中_1.jpeg',
    ])

    html = _html(client)

    tiles = [part.split('"')[0] for part in html.split('data-tile-uri="')[1:]]
    assert [tile.split('/')[-1] for tile in tiles] == [
        '2024-05-09 10.00.00-视频-示例博主-晚.mp4',
        '2024-04-02 10.00.00-图集-示例博主-中_1.jpeg',
        '2024-03-01 10.00.00-视频-示例博主-早.mp4',
    ]


def test_page_404s_for_unknown_sources(tmp_path):
    _, client = _blogger_app(tmp_path, ['2024-05-01 10.00.00-视频-示例博主-一.mp4'])

    assert client.get('/author/missing').status_code == 404
    assert client.get('/author/missing/avatar').status_code == 404
    assert client.post('/api/author/missing/refresh', json={}).status_code == 404
    assert client.post('/api/author/missing/search', json={'keyword': 'x'}).status_code == 404


def test_page_ignores_unknown_query_params(tmp_path):
    _, client = _blogger_app(tmp_path, ['2024-05-01 10.00.00-视频-示例博主-一.mp4'])

    html = _html(client, '/author/blogger?tag=%E7%83%AD%E9%97%A8&from=flow')

    assert '示例博主' in html
    assert html.count('class="author-tile"') == 1


def test_page_shows_empty_state_without_works(tmp_path):
    _, client = _blogger_app(tmp_path, [])

    html = _html(client)

    assert '还没有作品' in html
    assert 'class="author-tile"' not in html


def test_cached_profile_drives_name_and_compact_counters(tmp_path, author_instance):
    _, client = _blogger_app(tmp_path, ['2024-05-01 10.00.00-视频-示例博主-一.mp4'])
    cache = author_instance / 'authors' / 'blogger.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        'name': 'puff',
        'douyin_id': 'puff01',
        'follower_count': 12345,
        'total_favorited': 678,
    }, ensure_ascii=False), encoding='utf-8')

    html = _html(client)

    assert 'puff' in html
    assert '抖音号: puff01' in html
    assert '1.2万' in html
    assert '678' in html


def test_avatar_falls_back_to_the_newest_work_until_one_is_synced(tmp_path):
    """No synced picture used to mean the grey face on every author page.

    The page is somebody's profile, not an empty slot: their newest work is a
    recognisable stand-in, and the thumbnail endpoint already caches it.
    """
    _, client = _blogger_app(tmp_path, [
        '2024-05-01 10.00.00-视频-示例博主-一.mp4',
        '2024-05-02 10.00.00-图集-示例博主-二_1.jpeg',
    ])

    response = client.get('/author/blogger/avatar')

    assert response.status_code == 302
    # The newest work is the 2024-05-02 image, not the older video.
    assert '/thumb?' in response.headers['Location']
    assert '%E4%BA%8C' in response.headers['Location'] or '二' in response.headers['Location']


def test_avatar_serves_the_cached_photo(tmp_path, author_instance):
    _, client = _blogger_app(tmp_path, ['2024-05-01 10.00.00-视频-示例博主-一.mp4'])
    cached = author_instance / 'authors' / 'blogger.jpg'
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b'\xff\xd8\xff\xd9')

    response = client.get('/author/blogger/avatar')

    assert response.get_data() == b'\xff\xd8\xff\xd9'


def test_refresh_without_a_sidecar_is_rejected(tmp_path):
    _, client = _blogger_app(tmp_path, ['2024-05-01 10.00.00-视频-示例博主-一.mp4'])

    response = client.post('/api/author/blogger/refresh', json={})

    assert response.status_code == 400
    assert response.get_json() == {'success': False, 'ok': False, 'error': 'TikTokDownloader 未配置'}


def test_refresh_uses_the_folder_derived_keyword(tmp_path, monkeypatch):
    calls = {}

    def fake_refresh(self, source_id, *, keyword='', sec_user_id='', unique_id='', uid=''):
        calls.update({'source_id': source_id, 'keyword': keyword,
                      'unique_id': unique_id, 'uid': uid})
        return {
            'source_id': source_id, 'name': 'puff', 'douyin_id': 'puff01', 'signature': '',
            'follower_count': 12345, 'total_favorited': 678, 'aweme_count': 3,
            'avatar_url': '', 'has_avatar': False,
        }

    monkeypatch.setattr(TikTokDownloadService, 'refresh_author_profile', fake_refresh)
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )

    assert 'data-author-sync' in _html(client)
    assert 'data-author-picker' in _html(client)
    response = client.post('/api/author/blogger/refresh', json={})

    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['profile']['name'] == 'puff'
    assert calls == {'source_id': 'blogger', 'keyword': '示例博主',
                     'unique_id': '', 'uid': ''}


def test_refresh_maps_sidecar_failures_to_4xx_5xx(tmp_path, monkeypatch):
    def offline(self, source_id, *, keyword='', sec_user_id='', unique_id='', uid=''):
        raise TikTokDownloaderError('sidecar unreachable')

    monkeypatch.setattr(TikTokDownloadService, 'refresh_author_profile', offline)
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )

    response = client.post('/api/author/blogger/refresh', json={})

    assert response.status_code == 502
    assert response.get_json() == {'success': False, 'ok': False, 'error': 'sidecar unreachable'}

    def bad_keyword(self, source_id, *, keyword='', sec_user_id='', unique_id='', uid=''):
        raise ValueError('keyword required')

    monkeypatch.setattr(TikTokDownloadService, 'refresh_author_profile', bad_keyword)
    response = client.post('/api/author/blogger/refresh', json={'keyword': '  '})

    assert response.status_code == 400
    body = response.get_json()
    assert body['ok'] is False
    assert body['error'] == 'keyword required'


def test_page_follows_the_english_locale(tmp_path):
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )
    client.set_cookie('lang', 'en')

    html = _html(client)

    assert 'Douyin ID: 100000000000001' in html
    assert 'Works' in html
    assert '示例博主' in html
    assert '抖音号' not in html
    assert 'Choose the blogger account' in html
    assert 'Blogger nickname or Douyin id' in html


def test_search_lists_candidates_for_the_picker(tmp_path, monkeypatch):
    seen = {}

    def fake_search(self, keyword, count=10):
        seen['keyword'] = keyword
        return CANDIDATES

    monkeypatch.setattr(TikTokDownloadService, 'list_author_candidates', fake_search)
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )

    response = client.post('/api/author/blogger/search', json={'keyword': ' puff '})

    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert [item['unique_id'] for item in body['candidates']] == ['demo0001', 'demo0002']
    assert seen['keyword'] == 'puff'


def test_search_reports_an_unconfigured_sidecar_as_503(tmp_path, monkeypatch):
    monkeypatch.setattr(TikTokDownloadService, 'get_config', lambda self: {'base_url': ''})
    _, client = _blogger_app(tmp_path, ['2024-05-01 10.00.00-视频-示例博主-一.mp4'])

    response = client.post('/api/author/blogger/search', json={'keyword': 'puff'})

    assert response.status_code == 503
    assert response.get_json()['ok'] is False


def test_search_without_a_service_is_unavailable(tmp_path):
    client = _routes_only_client(tmp_path, tiktok_service=None)

    response = client.post('/api/author/blogger/search', json={'keyword': 'puff'})

    assert response.status_code == 503
    assert response.get_json()['ok'] is False


def test_refresh_reports_ambiguous_authors_as_409(tmp_path, monkeypatch):
    def ambiguous(self, source_id, *, keyword='', sec_user_id='', unique_id='', uid=''):
        raise AuthorAmbiguousError('找到多个同名账号，请选择正确的博主。', CANDIDATES)

    monkeypatch.setattr(TikTokDownloadService, 'refresh_author_profile', ambiguous)
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )

    response = client.post('/api/author/blogger/refresh', json={'keyword': 'puff'})

    assert response.status_code == 409
    body = response.get_json()
    assert body['ok'] is False
    assert body['candidates'] == CANDIDATES
    assert body['error'] == '找到多个同名账号，请选择正确的博主。'


def test_refresh_accepts_a_picked_candidate(tmp_path, monkeypatch):
    seen = {}

    def fake_refresh(self, source_id, *, keyword='', sec_user_id='', unique_id='', uid=''):
        seen.update(keyword=keyword, unique_id=unique_id, uid=uid)
        return {
            'source_id': source_id, 'name': 'puff', 'douyin_id': 'demo0001', 'signature': '',
            'follower_count': 12345, 'total_favorited': 678, 'aweme_count': 3,
            'avatar_url': '', 'has_avatar': False,
        }

    monkeypatch.setattr(TikTokDownloadService, 'refresh_author_profile', fake_refresh)
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )

    response = client.post(
        '/api/author/blogger/refresh',
        json={'keyword': 'puff', 'unique_id': 'demo0001', 'uid': '7001'},
    )

    assert response.status_code == 200
    assert response.get_json()['profile']['douyin_id'] == 'demo0001'
    assert seen == {'keyword': 'puff', 'unique_id': 'demo0001', 'uid': '7001'}


def test_sync_menu_offers_the_bound_link_instead_of_asking_for_it(tmp_path):
    from tiklocal.services import author_profiles

    author_profiles.set_profile_url('blogger', PROFILE_URL)
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )

    html = _html(client)

    assert f'data-author-bound-url="{PROFILE_URL}"' in html
    assert 'data-author-sheet-profile' in html
    assert 'data-author-sheet-videos' in html


def test_sync_route_behaves_like_refresh(tmp_path, monkeypatch):
    seen = []

    def fake_sync(self, source_id, *, profile_url='', aweme_id='', folder_uid=''):
        seen.append((source_id, profile_url))
        return {'source_id': source_id}

    monkeypatch.setattr(TikTokDownloadService, 'sync_author_profile', fake_sync)
    _, client = _blogger_app(
        tmp_path,
        ['2024-05-01 10.00.00-视频-示例博主-一.mp4'],
        {'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}},
    )

    synced = client.post('/api/author/blogger/sync', json={'profile_url': PROFILE_URL})
    refreshed = client.post('/api/author/blogger/refresh', json={'profile_url': PROFILE_URL})

    assert synced.status_code == 200
    assert refreshed.status_code == 200
    # Both names reach the same link-based sync and answer with the cached profile.
    assert seen == [('blogger', PROFILE_URL), ('blogger', PROFILE_URL)]
    assert synced.get_json()['profile']['douyin_id'] == '100000000000001'
