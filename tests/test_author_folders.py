# -*- coding: utf-8 -*-
"""One media root, several authors: the avatar names the folder, not the root.

A shared directory such as ``~/Downloads/douyin`` collects one export folder per
blogger. The root itself names nobody, so ``/author/<source>`` used to print the
root's own folder name as a nickname ("down") and list every work in the source.
These tests pin the folder-scoped page, the author list, and the link the feed
hands to the avatar.
"""

import os
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from PIL import Image

from tiklocal.app import create_app
from tiklocal.services.media_naming import blogger_display_name

REPO_ROOT = Path(__file__).resolve().parents[1]
# The pytest temp tree is not always writable on this host, so instance data
# lives inside the repo the way the other author tests do it.
INSTANCE_ROOT = REPO_ROOT / f'.tmp-inst-author-folders-{os.getpid()}'

PUFF = 'UID958392617677856_示例博主_发布作品'
NO_SUFFIX = 'UID1146129073323168_示例博主二'


@pytest.fixture(scope='session', autouse=True)
def clean_instance_root():
    import gc
    import shutil

    shutil.rmtree(INSTANCE_ROOT, ignore_errors=True)
    yield INSTANCE_ROOT
    gc.collect()
    for _ in range(5):
        shutil.rmtree(INSTANCE_ROOT, ignore_errors=True)
        if not INSTANCE_ROOT.exists():
            return
        time.sleep(0.1)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(INSTANCE_ROOT / tmp_path.name))
    root = _build_media(tmp_path)
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': root,
        'MEDIA_SOURCES': [{'id': 'default', 'name': 'down', 'path': str(root)}],
    })
    return app.test_client()


@pytest.fixture
def sidecar_client(tmp_path, monkeypatch):
    """The same library with a sidecar configured, so the sync menu renders."""
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(INSTANCE_ROOT / tmp_path.name))
    root = _build_media(tmp_path)
    app = create_app({
        'TESTING': True,
        'MEDIA_ROOT': root,
        'MEDIA_SOURCES': [{'id': 'default', 'name': 'down', 'path': str(root)}],
        'TIKTOK_DOWNLOADER': {'base_url': 'http://127.0.0.1:9/'},
    })
    return app.test_client()


def _build_media(tmp_path) -> Path:
    root = tmp_path / 'media'
    # Two export folders with real Douyin names, plus one loose file that
    # belongs to neither author.
    _image(root / PUFF / '2026-06-04 22.58.14-视频-示例博主-第一个.mp4', video=True)
    _image(root / PUFF / '2026-06-05 10.00.00-图集-示例博主-第二个_0.jpg')
    _image(root / PUFF / '2026-06-05 10.00.00-图集-示例博主-第二个_1.jpg')
    _image(root / NO_SUFFIX / 'video' / '2023-09-07 13.11.40-视频-示例博主二-第三个.mp4', video=True)
    _image(root / 'loose.jpg')
    # Two ordinary folders whose names differ only where one has an underscore,
    # which is what a SQL LIKE pattern would read as a wildcard.
    _image(root / 'notes_a' / 'one.jpg')
    _image(root / 'notesXa' / 'two.jpg')
    return root


def _image(path: Path, *, video: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if video:
        path.write_bytes(b'0' * 2048)
    else:
        Image.new('RGB', (32, 24), 'red').save(path)
    stamp = time.time() - 120
    os.utime(path, (stamp, stamp))


def _html(client, path):
    response = client.get(path)
    assert response.status_code == 200, path
    return response.get_data(as_text=True)


def test_the_shared_root_lists_its_authors_instead_of_naming_itself(client):
    html = _html(client, '/author/default')

    # The bug: the root's own folder name reached the page as a nickname.
    assert '<h1 class="author-name">down</h1>' not in html
    assert '示例博主' in html
    assert '示例博主二' in html
    # Only the export folders count; the loose file and the two ordinary folders
    # (notes_a / notesXa) name nobody.
    assert html.count('class="author-list-item"') == 2


def test_a_loose_file_next_to_the_export_folders_stays_on_the_page(client):
    """Listing the authors must not quietly drop the files that name nobody."""
    html = _html(client, '/author/default')

    assert html.count('class="author-list-item"') == 2
    assert 'loose.jpg' in html, 'the file outside every export folder vanished'


def test_a_folder_scoped_page_shows_only_that_authors_works(client):
    html = _html(client, f'/author/default/{PUFF}')

    assert '<h1 class="author-name">示例博主</h1>' in html
    # Two album members collapse to one cover; the video is its own tile.
    assert html.count('class="author-tile"') == 2
    assert '示例博主二' not in html


def test_a_folder_without_the_release_suffix_still_names_its_author(client):
    html = _html(client, f'/author/default/{NO_SUFFIX}')

    # The strict pattern needs _发布作品; dropping it used to leave the raw
    # UID-prefixed folder on screen as the nickname.
    assert '<h1 class="author-name">示例博主二</h1>' in html
    assert f'<h1 class="author-name">{NO_SUFFIX}</h1>' not in html


def test_an_unknown_folder_is_a_404_not_an_empty_page(client):
    assert client.get('/author/default/UID404_nobody_发布作品').status_code == 404


def test_the_avatar_route_is_not_shadowed_by_the_folder_route(client):
    """``/author/default/avatar`` must not be read as folder="avatar"."""
    response = client.get('/author/default/avatar')

    # No synced picture for the root, so it points at a work's cover instead of
    # 404-ing on a folder that does not exist.
    assert response.status_code == 302
    assert '/thumb?' in response.headers['Location']


def test_the_feed_points_the_avatar_at_the_folder_page(client):
    data = client.get('/api/feed/mix?page=1&size=12&seed=fixed&snapshot=1').get_json()

    for item in data['items']:
        author = item.get('author_name') or ''
        page = item.get('author_page_url') or ''
        if not author:
            assert page == ''
            continue
        # A work inside an export folder opens that folder's page, never the root.
        assert page.startswith('/author/default/'), (author, page)
        assert item['author_url'].startswith('/flow?blog=default')


def test_a_source_that_is_itself_the_export_folder_keeps_one_page():
    """The single-blogger setup must keep working: no author list, works intact."""
    from tiklocal.web.author import author_folders

    records = [{'name': f'@blogger/{PUFF}/2026-06-04 22.58.14-视频-示例博主-第一个.mp4'}]
    # The helper still sees one folder; the route only renders the list when the
    # source root is not itself an export folder, which the tests above cover.
    assert author_folders(records) == [
        {'folder': PUFF, 'name': '示例博主', 'count': 1}
    ]


def test_the_folder_filter_does_not_treat_underscores_as_wildcards(client):
    """SQL LIKE would read ``_`` as "any character"; the filter must not."""
    response = client.get('/author/default/notes_a')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count('class="author-tile"') == 1
    # `notesXa` also matches `notes_a` under LIKE, so its file must be absent.
    assert 'two.jpg' not in html


def test_blogger_names_survive_a_missing_suffix():
    assert blogger_display_name(PUFF) == '示例博主'
    assert blogger_display_name(NO_SUFFIX) == '示例博主二'
    # A folder that merely starts with letters is left alone.
    assert blogger_display_name('down') == 'down'


def test_a_folder_scoped_page_keeps_its_own_avatar_slot(client):
    """The avatar came back as the bundled grey face and the sync menu disappeared.

    Both were collateral damage from keying profiles by media source: a shared
    root has one source key, so a folder page could not own a picture or a
    bound link. Each blogger now has a cache key of its own, and until one is
    synced the page borrows that blogger's newest cover.
    """
    scoped = client.get(f'/author/default/{PUFF}/avatar')
    assert scoped.status_code == 302
    assert '/thumb?' in scoped.headers['Location']

    html = _html(client, f'/author/default/{PUFF}')
    assert f'src="/author/default/{quote(PUFF, safe="")}/avatar"' in html
    # The root's avatar must not be reused for the blogger inside it.
    assert 'src="/author/default/avatar"' not in html


def test_a_folder_scoped_page_offers_the_profile_sync_menu(sidecar_client):
    html = _html(sidecar_client, f'/author/default/{PUFF}')

    assert 'data-author-sync' in html
    # The folder travels with the page so the API calls scope to this blogger.
    assert f'data-author-folder="{PUFF}"' in html


def test_the_sync_menu_says_what_is_missing_when_no_sidecar_is_configured(client):
    """Hiding the button until TikTokDownloader was configured hid the feature.

    The menu still opens; it explains the missing address and links to Settings,
    and the two actions that would only 400 are left out.
    """
    html = _html(client, f'/author/default/{PUFF}')

    assert 'data-author-sync' in html
    assert 'class="author-sheet-note"' in html
    assert 'href="/settings/"' in html
    assert 'data-author-sheet-profile' not in html
    assert 'data-author-sheet-videos' not in html
    assert 'data-author-picker' not in html


def test_an_author_timeline_says_whose_works_they_are(client):
    """The feed reuses /flow, so an author timeline wore the 推荐 tab.

    Nothing in the chrome said the reader was inside one blogger's works, which
    made "scrolled into someone else" impossible to notice.
    """
    html = _html(client, f'/flow?blog=default&folder={quote(PUFF, safe="")}')

    assert 'id="douyin-author-feed"' in html
    assert 'class="douyin-author-feed-name"' in html
    assert '示例博主' in html
    assert '的作品' in html
    # Whose feed it is comes before the works count, and the count only counts
    # that one blogger's covers.
    assert '个作品' in html

    # The recommendation feed keeps its tabs and carries no author header.
    plain = _html(client, '/flow')
    assert 'id="douyin-author-feed"' not in plain
    assert 'id="douyin-tabs"' in plain


def test_the_author_list_shows_each_avatars(client):
    html = _html(client, '/author/default')

    assert html.count('class="author-list-avatar"') == 2
    assert f'/author/default/{quote(PUFF, safe="")}/avatar' in html


def _work_names(item) -> list[str]:
    """Feed names without the ``album:`` grouping prefix the collapser adds."""
    raw = [item.get('name')] + [child.get('name') for child in item.get('items') or []]
    return [str(name).split(':', 1)[-1] for name in raw if name]


def test_opening_one_work_scrolls_that_author_and_nobody_else(client):
    """Tapping a work on ``/author/default/UID…`` used to resume the whole root.

    The feed rebuilt the timeline from the media source alone, so the next swipe
    handed the reader a different blogger's post. The folder travels with the
    deep link now.
    """
    start = f'@default/{PUFF}/2026-06-05 10.00.00-图集-示例博主-第二个_0.jpg'
    data = client.get(
        '/api/feed/mix',
        query_string={
            'page': 1, 'size': 12, 'seed': 'fixed', 'snapshot': 1,
            'blog': 'default', 'folder': PUFF, 'start': start,
        },
    ).get_json()

    names = [name for item in data['items'] for name in _work_names(item)]
    assert names, 'the deep link must land on the requested work'
    assert start in names
    for name in names:
        assert name.startswith(f'@default/{PUFF}/'), name


def test_an_unknown_folder_opens_nothing_instead_of_the_whole_root(client):
    """A stale folder link must not silently scroll the entire media root."""
    start = f'@default/{PUFF}/2026-06-05 10.00.00-图集-示例博主-第二个_0.jpg'
    data = client.get(
        '/api/feed/mix',
        query_string={
            'page': 1, 'size': 12, 'seed': 'fixed', 'snapshot': 1,
            'blog': 'default', 'folder': 'UID404_nobody_发布作品', 'start': start,
        },
    ).get_json()

    assert data['items'] == []


def test_a_deep_link_without_a_folder_still_opens_the_requested_work(client):
    """The single-blogger layout has no folder to name; nothing changes there."""
    start = f'@default/{PUFF}/2026-06-04 22.58.14-视频-示例博主-第一个.mp4'
    data = client.get(
        '/api/feed/mix',
        query_string={
            'page': 1, 'size': 12, 'seed': 'fixed', 'snapshot': 1,
            'blog': 'default', 'start': start,
        },
    ).get_json()

    assert data['folder'] == ''
    names = [name for item in data['items'] for name in _work_names(item)]
    assert start in names


def test_a_folder_feed_keeps_its_scope_across_pages(client):
    """Page two carried no ?start= and used to fall back to the whole root.

    The recommendation pool that serves the ordinary flow knows media sources
    only, so a shared root's second page mixed every blogger back in. Running
    out of one author's works is fine; showing somebody else's is not.
    """
    seen = []
    for page in (1, 2, 3):
        data = client.get(
            '/api/feed/mix',
            query_string={'page': page, 'size': 8, 'seed': 'fixed', 'blog': 'default', 'folder': PUFF},
        ).get_json()
        names = [name for item in data['items'] for name in _work_names(item)]
        seen.extend(names)
        for name in names:
            assert name.startswith(f'@default/{PUFF}/'), f'page {page} leaked {name}'

    assert seen, 'the scoped feed must still show that author at all'


def test_the_sync_api_stores_under_the_folder_not_the_shared_root(
    sidecar_client, monkeypatch,
):
    """Two bloggers in one root would otherwise overwrite each other's profile."""
    from tiklocal.services import author_profiles
    from tiklocal.services.tiktok_downloader import TikTokDownloadService

    seen = []

    def fake_sync(self, source_id, *, profile_url='', aweme_id='', folder_uid=''):
        seen.append((source_id, folder_uid))
        # Stand in for the real sidecar trip: whatever key it is handed is the
        # slot the profile lands in.
        author_profiles.store_author_from_payload(
            source_id,
            {'uid': folder_uid, 'unique_id': 'demo0001', 'nickname': '示例博主'},
            fetch_avatar=False,
            profile_url=profile_url,
        )
        return {'ok': True, 'source_id': source_id, 'nickname': '示例博主', 'uid': folder_uid}

    monkeypatch.setattr(TikTokDownloadService, 'sync_author_profile', fake_sync)

    response = sidecar_client.post(
        '/api/author/sync',
        json={'items': [{'source_id': 'default', 'folder': PUFF, 'profile_url': 'https://www.douyin.com/user/x'}]},
    )

    assert response.status_code == 200
    # The cache key carries the folder; the folder's own UID is what gets verified.
    assert seen == [(f'default/{PUFF}', '958392617677856')]
    assert author_profiles.resolve_profile(f'default/{PUFF}')['name'] == '示例博主'
    # The shared root keeps no profile of its own, so the second blogger's sync
    # cannot land on the first one.
    assert author_profiles.resolve_profile('default')['name'] == ''


def test_two_authors_in_one_root_keep_separate_profile_slots():
    from tiklocal.web.author import profile_key

    # A single-blogger source keeps its old key, so an upgrade re-syncs nothing.
    assert profile_key('videos', 'UID1_x_发布作品', 'UID1_x_发布作品') == 'videos'
    assert profile_key('default', PUFF, 'down') == f'default/{PUFF}'
    assert profile_key('default', '', 'down') == 'default'


def test_the_settings_card_has_a_row_for_every_author(client):
    """One row per source would sync every blogger into one slot, so list authors.

    The card is where a home-page link is pasted, so each row has to carry the
    folder it belongs to: that is the only thing that tells the sync endpoint
    whose cache entry to write.
    """
    rows = client.get('/api/author/sources').get_json()['items']

    assert [row['folder'] for row in rows] == [PUFF, NO_SUFFIX]
    assert [row['local_count'] for row in rows] == [2, 1]
    assert all(row['source_id'] == 'default' for row in rows)
    assert f'/author/default/{quote(PUFF, safe="")}/avatar' == rows[0]['avatar_url']


def test_the_sync_endpoint_scopes_a_row_by_its_folder_query(sidecar_client, monkeypatch):
    """The settings row posts ?folder=; it must land in that blogger's entry."""
    from tiklocal.services.tiktok_downloader import TikTokDownloadService

    seen = []

    def fake_sync(self, source_id, *, profile_url='', aweme_id='', folder_uid=''):
        seen.append((source_id, folder_uid))
        return {'ok': True, 'source_id': source_id, 'nickname': '示例博主'}

    monkeypatch.setattr(TikTokDownloadService, 'sync_author_profile', fake_sync)

    response = sidecar_client.post(
        f'/api/author/default/sync?folder={quote(PUFF, safe="")}',
        json={'profile_url': 'https://www.douyin.com/user/x'},
    )

    assert response.status_code == 200
    assert seen == [(f'default/{PUFF}', '958392617677856')]


def test_a_profile_synced_before_the_per_author_keys_still_shows(client):
    """Every author of a shared root used to share one entry, under the root.

    A user who synced back then must not lose the nickname, the counters or the
    bound home-page link: the folder UID proves the old entry is this author, and
    it is read on their page. The proof is also what keeps it off everyone else's.
    """
    from tiklocal.services import author_profiles

    author_profiles.save_profile('default', {
        'name': '示例博主',
        'uid': '958392617677856',
        'douyin_id': 'demo0001',
        'follower_count': 123456,
        'total_favorited': 4567890,
        'profile_url': 'https://www.douyin.com/user/legacy',
    })

    page = _html(client, f'/author/default/{quote(PUFF, safe="")}')

    assert 'data-stat="followers"' in page
    assert 'data-stat="likes"' in page
    assert 'https://www.douyin.com/user/legacy' in page
    # The other blogger in the same root kept nothing and must stay empty.
    other = _html(client, f'/author/default/{quote(NO_SUFFIX, safe="")}')
    assert 'data-stat="followers"' not in other
    assert 'https://www.douyin.com/user/legacy' not in other


def test_an_inherited_profile_does_not_answer_for_a_different_uid(client):
    """UID is the whole proof: a mismatched entry must be ignored, not guessed."""
    from tiklocal.services import author_profiles

    author_profiles.save_profile('default', {
        'name': '示例博主', 'uid': '999', 'profile_url': 'https://www.douyin.com/user/legacy',
    })

    assert author_profiles.inherited_profile(f'default/{PUFF}', 'default', PUFF) == {}
    assert author_profiles.inherited_profile('default', 'default', PUFF) == {}


def test_a_synced_work_link_binds_the_home_page_under_that_author(client, monkeypatch):
    """Paste a work link, get the home page bound — and bound as *this* author.

    The sidecar payload carries the account's ``sec_uid``, which rebuilds the home
    page, so the binding needs no second paste. It has to land on the folder's
    cache entry: bound to the shared root it would show up on nobody's page.
    """
    from tiklocal.services import author_profiles
    from tiklocal.services.tiktok_downloader import TikTokDownloaderClient, TikTokDownloadService

    class ConfigStore:
        def get(self):
            return {'tiktok_downloader': {'base_url': 'http://127.0.0.1:9/'}}

    def fetch_detail(self, detail_id, platform='douyin', *, source=False):
        return [{'aweme_id': detail_id, 'author': {
            'uid': '958392617677856', 'nickname': '示例博主',
            'unique_id': 'demo0001', 'sec_uid': 'MS4wLjABAAAAsample',
            'follower_count': 1234, 'total_favorited': 5678,
        }}]

    monkeypatch.setattr(TikTokDownloaderClient, 'fetch_detail', fetch_detail)
    service = TikTokDownloadService(ConfigStore(), download_manager=None)

    service.sync_author_profile(
        f'default/{PUFF}',
        aweme_id='7000000000000000001',
        folder_uid='958392617677856',
    )

    bound = 'https://www.douyin.com/user/MS4wLjABAAAAsample'
    assert author_profiles.load_profile(f'default/{PUFF}')['profile_url'] == bound
    # Nothing may land under the shared root, or it would describe every author.
    assert 'profile_url' not in author_profiles.load_profile('default')

    page = _html(client, f'/author/default/{quote(PUFF, safe="")}')
    assert bound in page
    assert 'data-stat="followers"' in page
