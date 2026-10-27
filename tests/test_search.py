# -*- coding: utf-8 -*-
"""Search page and API: author matches, work matches and their tab split.

Fixture filenames use placeholder blogger names (示例博主/甲博主), never real
exports. Douyin export names encode type and author, so the fixtures also prove
the search results are built from the same naming rules as the feed.
"""

import time

from PIL import Image

from tiklocal.app import create_app
from tiklocal.i18n import t


def _touch(path, size=1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {'.jpg', '.jpeg'}:
        Image.new('RGB', (40, 30), 'red').save(path)
    else:
        path.write_bytes(b'0' * size)
    stamp = time.time() - 100
    import os
    os.utime(path, (stamp, stamp))


def _client(tmp_path, files, *, media_sources=None):
    media = tmp_path / 'media'
    for name in files:
        _touch(media / name)
    config = {'TESTING': True, 'MEDIA_ROOT': media if not media_sources else media / 'unused'}
    if media_sources:
        config['MEDIA_SOURCES'] = [
            {'id': source_id, 'name': name, 'path': str(media / folder)}
            for source_id, name, folder in media_sources
        ]
    app = create_app(config)
    return app, app.test_client()


def _client_with_root(tmp_path, files, root_name):
    """Serve ``media/<root_name>`` as MEDIA_ROOT -- a Douyin export folder as root."""
    root = tmp_path / 'media' / root_name
    for name in files:
        _touch(root / name)
    app = create_app({'TESTING': True, 'MEDIA_ROOT': root})
    return app, app.test_client()


def _search(client, query, **params):
    params.setdefault('q', query)
    return client.get('/api/search', query_string=params).get_json()


def test_blank_query_returns_an_empty_page(tmp_path):
    app, client = _client(tmp_path, ['2024-05-04 12.00.00-视频-示例博主-海边晚风.mp4'])

    payload = _search(client, '   ')
    assert payload['success'] is True
    assert payload['query'] == ''
    assert payload['users'] == [] and payload['videos'] == []
    assert payload['counts'] == {'users': 0, 'videos': 0}
    assert payload['has_more'] is False


def test_caption_search_finds_one_work_per_album(tmp_path):
    app, client = _client(tmp_path, [
        '2024-06-01 09.00.00-图集-示例博主-风景_1.jpeg',
        '2024-06-01 09.00.00-图集-示例博主-风景_2.jpeg',
        '2024-06-01 09.00.00-图集-示例博主-风景_3.jpeg',
        '2024-06-01 09.30.00-视频-示例博主-晚风.mp4',
    ])

    payload = _search(client, '风景')
    videos = payload['videos']
    # One card for the album: the members must not each match the caption.
    assert [video['name'] for video in videos] == [
        '@default/2024-06-01 09.00.00-图集-示例博主-风景_1.jpeg'
    ]
    assert videos[0]['title'] == '风景'
    assert videos[0]['kind_label'] == t('search.kind.album')
    assert videos[0]['open_url'].startswith('/flow?start=')
    assert videos[0]['thumb_url'].startswith('/thumb?uri=')
    assert payload['counts']['videos'] == 1


def test_nickname_search_finds_the_author(tmp_path):
    app, client = _client(
        tmp_path,
        ['UID100000000000001_甲博主_发布作品/2024-05-01 10.00.00-视频-甲博主-一.mp4',
         'UID100000000000002_乙博主_发布作品/2024-05-02 10.00.00-图集-乙博主-二_1.jpeg',
         'UID100000000000002_乙博主_发布作品/2024-05-02 10.00.00-图集-乙博主-二_2.jpeg'],
        media_sources=[
            ('a', 'UID100000000000001_甲博主_发布作品', 'UID100000000000001_甲博主_发布作品'),
            ('b', 'UID100000000000002_乙博主_发布作品', 'UID100000000000002_乙博主_发布作品'),
        ],
    )

    payload = _search(client, '甲博主')
    users = payload['users']
    assert [user['source_id'] for user in users] == ['a']
    assert users[0]['name'] == '甲博主'
    assert users[0]['url'] == '/author/a'
    assert users[0]['avatar_url'] == '/author/a/avatar'
    assert users[0]['works_label'] == t('search.user.works', n=1)
    # The export folder name is part of the indexed text, so the author's own
    # works match too -- but nothing from the other blogger leaks in.
    assert [video['name'] for video in payload['videos']] == [
        '@a/2024-05-01 10.00.00-视频-甲博主-一.mp4'
    ]


def test_tabs_split_users_from_videos(tmp_path):
    blogger = 'UID100000000000003_示例博主_发布作品'
    # The export folder is the root: its files belong to the folder's author.
    app, client = _client_with_root(tmp_path, [
        '2024-05-04 12.00.00-视频-示例博主-海边晚风.mp4',
        '2024-06-01 09.30.00-视频-示例博主-晚风.mp4',
    ], blogger)

    general = _search(client, '晚风')
    assert len(general['videos']) == 2
    # The work author is offered on the general tab as a shortcut to their feed.
    assert [user['name'] for user in general['users']] == ['示例博主']
    assert general['videos'][0]['author_name'] == '示例博主'
    assert general['videos'][0]['author_url'] == '/flow?blog=default'

    videos_only = _search(client, '晚风', tab='video')
    assert len(videos_only['videos']) == 2
    assert videos_only['users'] == []

    users_only = _search(client, '晚风', tab='user')
    assert users_only['videos'] == []
    assert [user['name'] for user in users_only['users']] == ['示例博主']


def test_general_tab_caps_the_author_shortcut(tmp_path):
    files, sources = [], []
    for index in range(4):
        nickname = f'博主{index}'
        folder = f'UID10000000000000{index}_{nickname}_发布作品'
        files.append(f'{folder}/2024-05-0{index + 1} 10.00.00-视频-{nickname}-晚风.mp4')
        sources.append((f's{index}', folder, folder))
    app, client = _client(tmp_path, files, media_sources=sources)

    general = _search(client, '晚风', limit=24)
    assert len(general['users']) == 3
    assert general['counts']['users'] == 4

    user_tab = _search(client, '晚风', tab='user', limit=24)
    assert len(user_tab['users']) == 4


def test_search_page_prefills_query_and_tab(tmp_path):
    app, client = _client(tmp_path, ['2024-05-04 12.00.00-视频-示例博主-海边晚风.mp4'])

    page = client.get('/search', query_string={'q': '海边', 'tab': 'video'})
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'data-query="海边"' in body
    assert 'data-tab="video"' in body

    # An unknown tab falls back to the default instead of rendering a dead tab.
    fallback = client.get('/search', query_string={'q': '海边', 'tab': 'bogus'})
    assert fallback.status_code == 200
    assert 'data-tab="general"' in fallback.get_data(as_text=True)


def test_unknown_tab_and_overlong_query_are_bounded(tmp_path):
    app, client = _client(tmp_path, ['2024-05-04 12.00.00-视频-示例博主-海边晚风.mp4'])

    payload = _search(client, '海边', tab='nope', limit=999)
    assert payload['tab'] == 'general'

    long_query = '海' * 400
    payload = _search(client, long_query)
    assert payload['query'] == '海' * 200
    # No LIKE metacharacter reached the index as a wildcard.
    assert payload['videos'] == []


def test_offset_walks_the_work_results(tmp_path):
    files = [
        f'2024-05-{index + 1:02d} 10.00.00-视频-示例博主-晚风{index}.mp4'
        for index in range(5)
    ]
    app, client = _client(tmp_path, files)

    first = _search(client, '晚风', tab='video', limit=2)
    assert len(first['videos']) == 2
    assert first['counts']['videos'] == 5
    assert first['has_more'] is True
    assert first['offset'] == 0

    second = _search(client, '晚风', tab='video', limit=2, offset=first['next_offset'])
    assert len(second['videos']) == 2
    assert second['offset'] == 2
    # A slice never repeats or overlaps the one before it.
    assert {video['name'] for video in second['videos']}.isdisjoint(
        {video['name'] for video in first['videos']}
    )

    last = _search(client, '晚风', tab='video', limit=2, offset=4)
    assert len(last['videos']) == 1
    assert last['has_more'] is False


def test_head_request_does_not_crash(tmp_path):
    app, client = _client(tmp_path, ['2024-05-04 12.00.00-视频-示例博主-海边晚风.mp4'])

    response = client.head('/api/search', query_string={'q': '海边'})
    assert response.status_code == 200
    assert response.get_data() == b''


def test_a_work_named_after_its_id_has_no_title(tmp_path):
    app, client = _client(tmp_path, [
        '2024-05-04 12.00.00-视频-示例博主-7680590482027890354.mp4',
        '7680590482027890354.mp4',
        '2024-05-05 12.00.00-视频-示例博主-2024.mp4',
    ])

    payload = _search(client, '7680590482027890354', tab='video')
    assert len(payload['videos']) == 2
    # The card hides the title line rather than printing the id as a caption --
    # both when the export kept the id after the author and when the file is
    # named after the id alone.
    assert [video['title'] for video in payload['videos']] == ['', '']
    assert all(video['kind_label'] == t('search.kind.video') for video in payload['videos'])

    # A short caption that happens to be a number is still a caption.
    assert _search(client, '2024', tab='video')['videos'][0]['title'] == '2024'
