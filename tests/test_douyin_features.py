# -*- coding: utf-8 -*-
"""Douyin-style export features: live-photo pairing, albums and blogger browsing.

Fixture filenames use placeholder blogger names (示例博主), never real exports.
"""

import time
from urllib.parse import unquote

from PIL import Image

from tiklocal.app import create_app


def _touch(path, size=1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {'.jpg', '.jpeg'}:
        Image.new('RGB', (40, 30), 'red').save(path)
    else:
        path.write_bytes(b'0' * size)
    stamp = time.time() - 100
    import os
    os.utime(path, (stamp, stamp))


def _client(tmp_path, files):
    media = tmp_path / 'media'
    for name in files:
        _touch(media / name)
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media})
    return app, app.test_client()


def test_live_pairs_merge_into_one_entry(tmp_path):
    app, client = _client(tmp_path, [
        '2024-05-04 12.00.00-实况-示例博主-标题_1.jpeg',
        '2024-05-04 12.00.00-实况-示例博主-标题_1.mp4',
        '2024-05-04 12.01.00-视频-示例博主-普通.mp4',
    ])
    page = client.get('/api/library/items?limit=24').get_json()['data']
    types = sorted(item['type'] for item in page['items'])
    assert types == ['live', 'video']

    live = next(item for item in page['items'] if item['type'] == 'live')
    assert live['name'] == '@default/2024-05-04 12.00.00-实况-示例博主-标题_1.jpeg'
    assert live['live_video_url'].endswith('.mp4')
    assert live['detail_url'].startswith('/image?uri=')
    # The paired mp4 has no row of its own.
    assert all(item['type'] != 'video' or not item['name'].endswith('实况-示例博主-标题_1.mp4')
               for item in page['items'])


def test_live_cover_without_video_stays_an_image(tmp_path):
    app, client = _client(tmp_path, [
        '2024-05-04 12.00.00-实况-示例博主-孤封面.jpeg',
    ])
    page = client.get('/api/library/items?limit=24').get_json()['data']
    assert [item['type'] for item in page['items']] == ['image']


def test_live_post_pairs_across_running_index(tmp_path):
    # Real exports number files across the whole post: the pair is _1.mp4 + _2.jpeg.
    app, client = _client(tmp_path, [
        '2024-05-04 12.00.00-实况-示例博主-单对_1.mp4',
        '2024-05-04 12.00.00-实况-示例博主-单对_2.jpeg',
        # Mixed post: photo, photo, LIVE(3,4), lone video(5).
        '2024-05-05 12.00.00-实况-示例博主-混合_1.jpeg',
        '2024-05-05 12.00.00-实况-示例博主-混合_2.jpeg',
        '2024-05-05 12.00.00-实况-示例博主-混合_3.mp4',
        '2024-05-05 12.00.00-实况-示例博主-混合_4.jpeg',
        '2024-05-05 12.00.00-实况-示例博主-混合_5.mp4',
    ])
    page = client.get('/api/library/items?limit=48').get_json()['data']
    by_name = {item['name']: item for item in page['items']}
    single = by_name['@default/2024-05-04 12.00.00-实况-示例博主-单对_2.jpeg']
    assert single['type'] == 'live'
    assert unquote(single['live_video_url']).endswith('单对_1.mp4')
    # The mixed post is one swipeable group of four members.
    assert by_name['@default/2024-05-05 12.00.00-实况-示例博主-混合_1.jpeg']['album_size'] == 4
    live_member = by_name['@default/2024-05-05 12.00.00-实况-示例博主-混合_4.jpeg']
    assert live_member['type'] == 'live' and unquote(live_member['live_video_url']).endswith('混合_3.mp4')
    assert by_name['@default/2024-05-05 12.00.00-实况-示例博主-混合_5.mp4']['type'] == 'video'

    feed = client.get('/api/feed/mix?size=12&seed=s').get_json()
    groups = [item for item in feed['items'] if item['type'] == 'image_group']
    assert len(groups) == 1 and groups[0]['title'] == '实况'
    assert [child['type'] for child in groups[0]['items']] == ['image', 'image', 'live', 'video']
    # The lone live post renders as a single live entry, not a one-child group.
    lives = [item for item in feed['items'] if item['type'] == 'live']
    assert len(lives) == 1 and lives[0]['name'].endswith('单对_2.jpeg')


def test_album_members_are_tagged_and_covers_only_feed(tmp_path):
    app, client = _client(tmp_path, [
        '2024-06-01 09.00.00-图集-示例博主-风景_1.jpeg',
        '2024-06-01 09.00.00-图集-示例博主-风景_2.jpeg',
        '2024-06-01 09.00.00-图集-示例博主-风景_10.jpeg',
        '2024-06-01 09.30.00-图集-示例博主-另一组_1.jpeg',
    ])
    page = client.get('/api/library/items?limit=48').get_json()['data']
    by_name = {item['name']: item for item in page['items']}
    cover = by_name['@default/2024-06-01 09.00.00-图集-示例博主-风景_1.jpeg']
    assert cover['album_size'] == 3 and cover['album_index'] == 0
    # _10 sorts after _2; stored positions are 0-based.
    member = by_name['@default/2024-06-01 09.00.00-图集-示例博主-风景_10.jpeg']
    assert member['album_index'] == 2 and member['album_key'] == cover['album_key']
    assert by_name['@default/2024-06-01 09.30.00-图集-示例博主-另一组_1.jpeg']['album_size'] == 1


def test_flow_renders_album_group_and_live_entry(tmp_path):
    app, client = _client(tmp_path, [
        '2024-06-01 09.00.00-图集-示例博主-风景_1.jpeg',
        '2024-06-01 09.00.00-图集-示例博主-风景_2.jpeg',
        '2024-06-01 09.00.00-图集-示例博主-风景_3.jpeg',
        '2024-05-04 12.00.00-实况-示例博主-标题_1.jpeg',
        '2024-05-04 12.00.00-实况-示例博主-标题_1.mp4',
    ])
    feed = client.get('/api/feed/mix?size=8&seed=s').get_json()
    groups = [item for item in feed['items'] if item['type'] == 'image_group']
    lives = [item for item in feed['items'] if item['type'] == 'live']
    assert len(groups) == 1
    assert groups[0]['title'] == '图集'
    assert len(groups[0]['items']) == 3
    # Members are ordered by their _N index, 10 after 2.
    names = [child['name'] for child in groups[0]['items']]
    assert names == sorted(names, key=lambda n: int(n.rsplit('_', 1)[1].split('.')[0]))
    assert len(lives) == 1 and lives[0]['live_video_url'].endswith('.mp4')
    # No stray plain member entries beside the album group.
    plain = [item for item in feed['items'] if item['type'] == 'image']
    assert plain == []


def test_blog_filter_and_chips(tmp_path):
    media = tmp_path / 'media'
    for name in [
        'UID100000000000001_甲博主_发布作品/2024-05-01 10.00.00-视频-甲博主-一.mp4',
        'UID100000000000002_乙博主_发布作品/2024-05-02 10.00.00-图集-乙博主-二_1.jpeg',
        'UID100000000000002_乙博主_发布作品/2024-05-02 10.00.00-图集-乙博主-二_2.jpeg',
    ]:
        _touch(media / name)
    app = create_app({
        'TESTING': True, 'MEDIA_ROOT': media / 'unused',
        'MEDIA_SOURCES': [
            {'id': 'a', 'name': 'UID100000000000001_甲博主_发布作品',
             'path': str(media / 'UID100000000000001_甲博主_发布作品')},
            {'id': 'b', 'name': 'UID100000000000002_乙博主_发布作品',
             'path': str(media / 'UID100000000000002_乙博主_发布作品')},
        ],
    })
    client = app.test_client()

    chips = client.get('/api/library/blogs').get_json()['data']['items']
    assert {chip['name'] for chip in chips} == {'甲博主', '乙博主'}
    assert {chip['id'] for chip in chips} == {'a', 'b'}
    assert all(chip['flow_url'].startswith('/flow?blog=') for chip in chips)

    filtered = client.get('/api/library/items?blog=a&limit=12').get_json()['data']
    assert [item['name'] for item in filtered['items']] == [
        '@a/2024-05-01 10.00.00-视频-甲博主-一.mp4'
    ]
    feed = client.get('/api/feed/mix?blog=b&size=8&seed=s').get_json()
    types = [item['type'] for item in feed['items']]
    assert 'image_group' in types  # 乙博主's album reaches the filtered feed


def test_default_root_that_is_a_blogger_folder_keeps_its_author(tmp_path):
    """A Douyin export folder served as the default source still names its author.

    ``tiklocal <UID…_博主名_发布作品>`` is the most common setup, and its files sit
    directly in the root. Dropping the author there also dropped the avatar and the
    @handle from Douyin mode.
    """
    media = tmp_path / 'UID100000000000003_示例博主_发布作品'
    _touch(media / '2024-05-04 12.00.00-视频-示例博主-海边晚风.mp4')
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media})
    client = app.test_client()

    feed = client.get('/api/feed/mix?size=8&seed=s').get_json()
    videos = [item for item in feed['items'] if item['type'] == 'video']
    assert len(videos) == 1
    assert videos[0]['author_name'] == '示例博主'
    assert videos[0]['author_url'] == '/flow?blog=default'
    assert videos[0]['caption_text'] == '海边晚风'
    # Nothing is synced for this author, so the avatar route points at the newest
    # work's cover rather than serving the grey placeholder.
    avatar = client.get('/author/default/avatar')
    assert avatar.status_code == 302
    assert 'thumb' in avatar.headers['Location']

    # A plain media root still has no author: its loose files belong to nobody.
    plain = tmp_path / 'plain'
    _touch(plain / 'holiday.jpg')
    app = create_app({'TESTING': True, 'MEDIA_ROOT': plain})
    entry = app.test_client().get('/api/feed/mix?size=8&seed=s').get_json()['items'][0]
    assert 'author_name' not in entry


def test_blogger_display_name():
    from tiklocal.services.media_naming import blogger_display_name, is_blogger_folder, album_member, is_live_stem, post_title
    assert blogger_display_name('UID123456789_示例博主_发布作品') == '示例博主'
    assert blogger_display_name('普通文件夹') == '普通文件夹'
    assert is_blogger_folder('UID123456789_示例博主_发布作品')
    assert not is_blogger_folder('普通文件夹')
    assert not is_blogger_folder('UID123456789_示例博主')
    assert is_live_stem('2024-05-04 12.00.00-实况-示例博主-标题_1')
    assert not is_live_stem('2024-05-04 12.00.00-图集-示例博主-标题_1')
    assert album_member('2024-06-01 09.00.00-图集-示例博主-风景_10.jpeg') == (
        '2024-06-01 09.00.00-图集-示例博主-风景', 10)
    assert album_member('2024-06-01 09.00.00-图集-示例博主-风景.jpeg') is None
    assert album_member('2024-06-01 09.00.00-视频-示例博主-风景_1.mp4') is None
    assert post_title('2024-05-04 12.00.00-实况-示例博主-幸福瞬间#海边 晚风_3.jpeg') == '幸福瞬间#海边 晚风'
    assert post_title('2024-05-04 12.00.00-视频-示例博主-带-连-字符-的标题.mp4') == '带-连-字符-的标题'
    assert post_title('IMG_2048.jpeg') == ''
