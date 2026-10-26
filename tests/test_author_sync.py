# -*- coding: utf-8 -*-
"""Author profile sync: home-page link → sec_uid → account → work → author block.

Identity is only trusted when the numeric ``uid`` inside the author block equals
the UID in the export folder name, so the fixture data below uses fake ids
(``demo_author`` / ``demo0001`` / ``MS4wLjABAAAAfake``) and a placeholder folder.
"""

import gc
import shutil
import time
from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.services import author_profiles
from tiklocal.services.tiktok_downloader import (
    TikTokDownloaderClient,
    TikTokDownloaderError,
    TikTokDownloadService,
    parse_aweme_id,
    parse_profile_sec_uid,
)
from tiklocal.web.author import parse_sync_text

REPO_ROOT = Path(__file__).resolve().parents[1]
# Author caches and the app database must stay inside the repo instead of the
# pytest temp tree.
INSTANCE_ROOT = REPO_ROOT / '.tmp-inst-sync'
FOLDER = 'UID200000000000002_demo_author_发布作品'
FOLDER_UID = '200000000000002'
SEC_UID = 'MS4wLjABAAAAfake'
PROFILE_URL = f'https://www.douyin.com/user/{SEC_UID}?from_tab_name=main'
SIDECAR_URL = 'http://127.0.0.1:9/'
WORK_ID = '7680590482027890354'

AUTHOR_BLOCK = {
    'uid': FOLDER_UID,
    'nickname': 'demo_author',
    'unique_id': 'demo0001',
    'short_id': '37543',
    'sec_uid': SEC_UID,
    'signature': '',
    'follower_count': 1234,
    'total_favorited': 5678,
}


@pytest.fixture(scope='session', autouse=True)
def sync_instance_root():
    """Own the instance root: start clean and leave nothing behind."""
    shutil.rmtree(INSTANCE_ROOT, ignore_errors=True)
    yield INSTANCE_ROOT
    # Windows keeps files delete-pending while sqlite handles are alive.
    gc.collect()
    for _ in range(5):
        shutil.rmtree(INSTANCE_ROOT, ignore_errors=True)
        if not INSTANCE_ROOT.exists():
            return
        time.sleep(0.1)


@pytest.fixture(autouse=True)
def sync_instance(monkeypatch, tmp_path):
    """Give every test its own isolated instance directory under the repo."""
    instance = INSTANCE_ROOT / tmp_path.name
    shutil.rmtree(instance, ignore_errors=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    yield instance
    gc.collect()
    shutil.rmtree(instance, ignore_errors=True)


class _ConfigStore:
    """Minimal stand-in for the settings store the service reads its URL from."""

    def __init__(self, config=None):
        self._config = {'tiktok_downloader': {'base_url': SIDECAR_URL, **(config or {})}}

    def get(self):
        return self._config


def _service(config=None):
    return TikTokDownloadService(_ConfigStore(config), download_manager=None)


def _fake_client(monkeypatch, *, author=None, works=None, calls=None):
    """Patch the sidecar client so no HTTP request ever leaves the process."""
    block = dict(AUTHOR_BLOCK if author is None else author)
    work_items = [{'aweme_id': WORK_ID}] if works is None else works
    log = calls if calls is not None else []

    def fetch_account(self, sec_user_id, tab='post', count=18, platform='douyin', *, source=False, pages=0):
        log.append(('account', sec_user_id, count, source, pages))
        return list(work_items)

    def fetch_detail(self, detail_id, platform='douyin', *, source=False):
        log.append(('detail', detail_id, source))
        return [{'aweme_id': detail_id, 'author': dict(block)}]

    monkeypatch.setattr(TikTokDownloaderClient, 'fetch_account', fetch_account)
    monkeypatch.setattr(TikTokDownloaderClient, 'fetch_detail', fetch_detail)
    return log


def _app(tmp_path, *, sources=None, config=None):
    media = tmp_path / 'media'
    entries = []
    for source_id, folder_name in (sources or [('demo', FOLDER)]):
        (media / folder_name).mkdir(parents=True, exist_ok=True)
        entries.append({'id': source_id, 'name': folder_name, 'path': str(media / folder_name)})
    settings = {
        'TESTING': True,
        'MEDIA_ROOT': tmp_path / 'unused',
        'MEDIA_SOURCES': entries,
    }
    settings.update(config or {})
    app = create_app(settings)
    return app, app.test_client()


@pytest.mark.parametrize('text', [
    PROFILE_URL,
    f'/user/{SEC_UID}',
    f'https://m.douyin.com/user/{SEC_UID}',
    SEC_UID,
    f'  https://www.douyin.com/user/{SEC_UID}  ',
])
def test_parse_profile_sec_uid_accepts_links_paths_and_bare_ids(text):
    assert parse_profile_sec_uid(text) == SEC_UID


@pytest.mark.parametrize('text', [
    '',
    '   ',
    'https://www.douyin.com/',
    'https://v.douyin.com/short-link/',
    'not a profile link',
])
def test_parse_profile_sec_uid_rejects_junk(text):
    with pytest.raises(ValueError):
        parse_profile_sec_uid(text)


@pytest.mark.parametrize('text', [
    f'https://www.douyin.com/video/{WORK_ID}',
    f'https://www.douyin.com/note/{WORK_ID}?previous_page=app_code_link',
    f'https://www.douyin.com/slides/{WORK_ID}',
    f'https://www.iesdouyin.com/share/video/{WORK_ID}/?region=CN',
    WORK_ID,
    f'  https://www.douyin.com/video/{WORK_ID}  ',
])
def test_parse_aweme_id_accepts_work_links_and_bare_ids(text):
    assert parse_aweme_id(text) == WORK_ID


@pytest.mark.parametrize('text', [
    '',
    '   ',
    PROFILE_URL,
    'https://www.douyin.com/',
    'https://v.douyin.com/short-link/',
    '12345',
])
def test_parse_aweme_id_rejects_junk(text):
    assert parse_aweme_id(text) == ''


def test_sync_follows_link_through_account_and_detail(monkeypatch):
    calls = _fake_client(monkeypatch)

    result = _service().sync_author_profile('demo', profile_url=PROFILE_URL, folder_uid=FOLDER_UID)

    assert calls == [('account', SEC_UID, 5, True, 1), ('detail', WORK_ID, True)]
    assert result == {
        'source_id': 'demo',
        'nickname': 'demo_author',
        'unique_id': 'demo0001',
        'short_id': '37543',
        'uid': FOLDER_UID,
        'follower_count': 1234,
        'total_favorited': 5678,
        'avatar_saved': False,
    }
    cached = author_profiles.load_profile('demo')
    assert cached['name'] == 'demo_author'
    assert cached['douyin_id'] == 'demo0001'
    assert cached['uid'] == FOLDER_UID


def test_sync_skips_the_account_hop_when_the_work_id_is_known(monkeypatch):
    calls = _fake_client(monkeypatch)

    _service().sync_author_profile('demo', aweme_id=WORK_ID, folder_uid=FOLDER_UID)

    assert calls == [('detail', WORK_ID, True)]


def test_sync_takes_the_fast_path_for_a_pasted_work_link(monkeypatch):
    calls = _fake_client(monkeypatch)

    result = _service().sync_author_profile(
        'demo', profile_url=f'https://www.douyin.com/video/{WORK_ID}', folder_uid=FOLDER_UID,
    )

    assert calls == [('detail', WORK_ID, True)]
    assert result['unique_id'] == 'demo0001'


def test_sync_rejects_a_uid_mismatch_without_writing(monkeypatch):
    other = dict(AUTHOR_BLOCK, uid='999000999')
    _fake_client(monkeypatch, author=other)

    with pytest.raises(TikTokDownloaderError) as excinfo:
        _service().sync_author_profile('demo', profile_url=PROFILE_URL, folder_uid=FOLDER_UID)

    assert '999000999' in str(excinfo.value)
    assert FOLDER_UID in str(excinfo.value)
    assert author_profiles.load_profile('demo') == {}
    assert not author_profiles.avatar_path('demo').exists()


def test_sync_reports_a_link_without_works(monkeypatch):
    _fake_client(monkeypatch, works=[])

    with pytest.raises(TikTokDownloaderError):
        _service().sync_author_profile('demo', profile_url=PROFILE_URL, folder_uid=FOLDER_UID)


def test_sync_requires_a_configured_sidecar(monkeypatch):
    _fake_client(monkeypatch)
    service = TikTokDownloadService(_ConfigStore(), download_manager=None)
    service._config_store._config = {'tiktok_downloader': {'base_url': ''}}

    with pytest.raises(TikTokDownloaderError):
        service.sync_author_profile('demo', profile_url=PROFILE_URL)


def test_batch_keeps_the_entries_that_succeeded(monkeypatch):
    _fake_client(monkeypatch)
    service = _service()

    results = service.sync_author_profiles([
        {'source_id': 'demo', 'profile_url': PROFILE_URL, 'folder_uid': FOLDER_UID},
        {'source_id': 'other', 'profile_url': PROFILE_URL, 'folder_uid': '123'},
        {'source_id': 'demo', 'profile_url': 'https://v.douyin.com/short-link/'},
    ])

    assert [item['ok'] for item in results] == [True, False, False]
    assert results[0]['nickname'] == 'demo_author'
    assert results[1]['error']
    assert results[2]['error']
    assert author_profiles.load_profile('demo')['douyin_id'] == 'demo0001'


def test_batch_matches_a_bare_link_through_the_folder_uid(monkeypatch):
    _fake_client(monkeypatch)
    looked_up = []

    def source_lookup(uid):
        looked_up.append(uid)
        return 'demo' if uid == FOLDER_UID else ''

    results = _service().sync_author_profiles(
        [{'profile_url': PROFILE_URL}], source_lookup=source_lookup,
    )

    assert looked_up == [FOLDER_UID]
    assert results == [{
        'ok': True,
        'source_id': 'demo',
        'nickname': 'demo_author',
        'unique_id': 'demo0001',
        'short_id': '37543',
        'uid': FOLDER_UID,
        'follower_count': 1234,
        'total_favorited': 5678,
        'avatar_saved': False,
    }]


def test_batch_reports_a_link_without_a_matching_folder(monkeypatch):
    _fake_client(monkeypatch)

    results = _service().sync_author_profiles([{'profile_url': PROFILE_URL}], source_lookup=lambda uid: '')

    assert results[0]['ok'] is False
    assert FOLDER_UID in results[0]['error']


def test_parse_sync_text_accepts_pairs_links_and_comments():
    entries = parse_sync_text(
        f'\n# comment\ndemo|{PROFILE_URL}\n{PROFILE_URL}\n\n',
    )

    assert entries == [
        {'source_id': 'demo', 'profile_url': PROFILE_URL},
        {'source_id': '', 'profile_url': PROFILE_URL},
    ]


def test_sources_endpoint_lists_media_sources(tmp_path):
    app, client = _app(tmp_path, sources=[('demo', FOLDER), ('second', 'UID300000000000003_demo_other_发布作品')])
    author_profiles.save_profile('demo', {'name': 'demo_author', 'douyin_id': 'demo0001'})

    response = client.get('/api/author/sources')

    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    by_id = {item['source_id']: item for item in body['items']}
    assert {'demo', 'second'} <= set(by_id)
    assert by_id['demo']['display_name'] == 'demo_author'
    assert by_id['demo']['douyin_id'] == 'demo0001'
    assert by_id['demo']['has_profile'] is True
    assert by_id['demo']['avatar_url'] == '/author/demo/avatar'
    assert by_id['second']['has_profile'] is False
    assert by_id['second']['douyin_id'] == '300000000000003'


def test_sync_endpoint_parses_text_lines(tmp_path, monkeypatch):
    captured = {}

    def fake_sync(self, entries, *, source_lookup=None):
        captured['entries'] = list(entries)
        return [{'ok': True, 'source_id': entries[0]['source_id'], 'nickname': 'demo_author'}]

    monkeypatch.setattr(TikTokDownloadService, 'sync_author_profiles', fake_sync)
    _, client = _app(tmp_path, config={'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}})

    response = client.post('/api/author/sync', json={'text': f'demo|{PROFILE_URL}'})

    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['synced'] == 1
    assert captured['entries'] == [{
        'source_id': 'demo', 'profile_url': PROFILE_URL, 'folder_uid': FOLDER_UID,
    }]


def test_sync_endpoint_rejects_a_missing_sidecar(tmp_path):
    _, client = _app(tmp_path)

    response = client.post('/api/author/sync', json={'text': PROFILE_URL})

    assert response.status_code == 400
    assert response.get_json()['ok'] is False


def test_sync_endpoint_requires_input(tmp_path):
    _, client = _app(tmp_path, config={'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}})

    response = client.post('/api/author/sync', json={'text': '   '})

    assert response.status_code == 400
    assert response.get_json()['ok'] is False


def test_sync_endpoint_reports_unknown_sources_per_item(tmp_path, monkeypatch):
    def fake_sync(self, entries, *, source_lookup=None):
        raise AssertionError('an unknown source must not reach the sidecar')

    monkeypatch.setattr(TikTokDownloadService, 'sync_author_profiles', fake_sync)
    _, client = _app(tmp_path, config={'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}})

    response = client.post('/api/author/sync', json={'items': [{'source_id': 'ghost', 'profile_url': PROFILE_URL}]})

    assert response.status_code == 502
    body = response.get_json()
    assert body['ok'] is False
    assert body['synced'] == 0
    assert body['items'][0]['source_id'] == 'ghost'
    assert 'ghost' in body['items'][0]['error']


def test_sync_endpoint_keeps_partial_results(tmp_path, monkeypatch):
    def fake_sync(self, entries, *, source_lookup=None):
        return [
            {'ok': True, 'source_id': entries[0]['source_id'], 'nickname': 'demo_author'},
            {'ok': False, 'source_id': entries[0]['source_id'], 'error': 'boom'},
        ]

    monkeypatch.setattr(TikTokDownloadService, 'sync_author_profiles', fake_sync)
    _, client = _app(tmp_path, config={'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}})

    response = client.post('/api/author/sync', json={'text': f'demo|{PROFILE_URL}'})

    assert response.status_code == 200
    body = response.get_json()
    assert body['ok'] is True
    assert body['synced'] == 1
    assert [item['ok'] for item in body['items']] == [True, False]


def test_settings_page_renders_the_sync_card(tmp_path):
    _, client = _app(tmp_path)

    html = client.get('/settings/').get_data(as_text=True)

    assert 'author-sync-links' in html
    assert 'author-sync-run' in html
    assert 'author-sync-source-list' in html


def test_author_page_offers_the_link_route_inside_the_picker(tmp_path):
    _, client = _app(tmp_path, config={'TIKTOK_DOWNLOADER': {'base_url': SIDECAR_URL}})

    html = client.get('/author/demo').get_data(as_text=True)

    assert 'data-author-profile-url' in html
    assert 'data-author-url-sync' in html
    assert 'data-author-search-form' in html
