import json
import os
import time
import sqlite3

import pytest
from PIL import Image

from tiklocal.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "v1.mp4").write_bytes(b"00")
    (media_root / "v2.mp4").write_bytes(b"00")
    (media_root / "nested").mkdir(parents=True, exist_ok=True)
    (media_root / "nested" / "v 3.mp4").write_bytes(b"00")

    (media_root / "i1.jpg").write_bytes(b"00")
    (media_root / "i2.png").write_bytes(b"00")

    monkeypatch.setenv("MEDIA_ROOT", str(media_root))

    app = create_app({"TESTING": True, "MEDIA_ROOT": media_root})
    return app.test_client()


def test_mix_feed_returns_typed_items(client):
    res = client.get("/api/feed/mix?page=1&size=12&seed=fixed-seed&snapshot=1")
    assert res.status_code == 200
    data = res.get_json()

    assert isinstance(data, dict)
    assert "items" in data
    assert "seed" in data
    assert "has_more" in data
    assert data["has_more"] is False

    items = data["items"]
    assert isinstance(items, list)
    assert len(items) > 0

    types = {item.get("type") for item in items}
    assert "video" in types
    assert "image" in types

    for item in items:
        assert "name" in item
        assert "media_url" in item
        assert "detail_url" in item
        assert item["recommendation_reason"]
        assert item["type"] in {"video", "image"}
        if item["type"] == "video":
            assert item["detail_url"].startswith("/detail/")
        else:
            assert item["detail_url"].startswith("/image?uri=")


@pytest.mark.parametrize('extension,grouped,count', [
    ('mp4', False, 24), ('jpg', False, 24), ('jpg', True, 24),
    ('mixed', False, 24), ('jpg', False, 0),
])
def test_feed_cards_and_groups_preserve_all_media_across_pages(tmp_path, monkeypatch, extension, grouped, count):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    names = [
        f'item-{index:02d}.{("mp4" if index < 6 else "jpg") if extension == "mixed" else extension}'
        for index in range(count)
    ]
    for name in names:
        (media_root / name).write_bytes(b'media')
    data_root = tmp_path / 'tiklocal-data'
    data_root.mkdir()
    if grouped:
        (data_root / 'download_sources.json').write_text(json.dumps({
            'version': 1,
            'items': {f'@default/{name}': {
                'source_url_raw': 'https://example.com/post/1',
                'source_url_display': 'https://example.com/post/1',
                'source_domain': 'example.com',
                'job_id': 'one-post',
            } for name in names},
        }))
    client = create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()

    seen, group_count = [], 0
    pages = max(1, (count + 7) // 8)
    for page in range(1, pages + 1):
        response = client.get('/api/feed/mix', query_string={'page': page, 'size': 8, 'seed': 'fixed'})
        assert response.status_code == 200
        payload = response.get_json()
        page_names = []
        for item in payload['items']:
            if item['type'] == 'image_group':
                group_count += 1
                page_names.extend(child['name'] for child in item['items'])
                assert all(client.get(child['media_url']).data == b'media' for child in item['items'])
            else:
                page_names.append(item['name'])
        assert len(page_names) == min(8, count)
        assert not set(page_names).intersection(seen)
        assert payload['has_more'] is (page < pages)
        seen.extend(page_names)
    assert set(seen) == {f'@default/{name}' for name in names}
    assert group_count == (3 if grouped else 0)


def _age(path, days):
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_memory_card_gathers_one_old_week_of_unseen_images(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    for index in range(4):
        (media_root / f'old-{index}.jpg').write_bytes(b'img')
        _age(media_root / f'old-{index}.jpg', 400)
    for index in range(10):
        (media_root / f'new-{index}.mp4').write_bytes(b'vid')
    client = create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()

    items = client.get('/api/feed/mix', query_string={'page': 1, 'size': 24, 'seed': 'm'}).get_json()['items']
    memories = [item for item in items if item['name'].startswith('memory:')]
    assert len(memories) == 1
    assert memories[0]['type'] == 'image_group'
    assert memories[0]['caption']
    assert {child['name'] for child in memories[0]['items']} == {f'@default/old-{i}.jpg' for i in range(4)}
    assert not [item for item in items if item['name'].startswith('@default/old-')]

    page_two = client.get('/api/feed/mix', query_string={'page': 2, 'size': 8, 'seed': 'm'}).get_json()['items']
    assert not [item for item in page_two if item['name'].startswith('memory:')]


def test_forgotten_feed_skips_new_and_recently_shown_media(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    for name in ('old-seen.mp4', 'old-unseen.mp4', 'old.jpg'):
        (media_root / name).write_bytes(b'x')
        _age(media_root / name, 300)
    (media_root / 'new.mp4').write_bytes(b'x')
    client = create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()
    client.post('/api/activity', json={'uri': '@default/old-seen.mp4', 'media_type': 'video', 'event': 'impression'})

    payload = client.get('/api/feed/mix', query_string={'mode': 'forgotten', 'seed': 'f', 'snapshot': 1}).get_json()
    assert {item['name'] for item in payload['items']} == {'@default/old-unseen.mp4', '@default/old.jpg'}
    assert all(item['type'] in {'video', 'image'} for item in payload['items'])


def test_flow_activity_builds_and_clears_local_profile(client, tmp_path):
    events = [
        {
            "session_id": "session-1",
            "uri": "@default/nested/v 3.mp4",
            "media_type": "video",
            "surface": "flow",
            "event": "impression",
        },
        {
            "session_id": "session-1",
            "uri": "@default/nested/v 3.mp4",
            "media_type": "video",
            "surface": "flow",
            "event": "complete",
            "ratio": 0.94,
            "visible_ms": 12000,
        },
    ]
    res = client.post("/api/activity", json={"events": events})

    assert res.status_code == 200
    assert res.get_json()["data"]["accepted"] == 2
    db_path = tmp_path / "tiklocal-data" / "tiklocal.sqlite3"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT impressions, completes, affinity_score FROM media_affinity WHERE uri = ?",
            ("@default/nested/v 3.mp4",),
        ).fetchone()
        dimensions = dict(conn.execute(
            """
            SELECT dimension_type, positive_score
            FROM preference_dimensions
            WHERE dimension_value IN ('video', 'default', 'default/nested')
            """
        ).fetchall())
    assert row[0:2] == (1, 1)
    assert row[2] > 0
    assert set(dimensions) == {"media_type", "source", "directory"}
    assert all(score > 0 for score in dimensions.values())

    assert client.delete("/api/activity").get_json()["success"] is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_affinity").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM preference_dimensions").fetchone()[0] == 0


def test_author_caption_from_blogger_source():
    from types import SimpleNamespace

    from tiklocal.web.flow import build_author_caption

    sources = {
        "blogger": SimpleNamespace(
            path=r"E:\media\UID958392617677856_泡芙_发布作品", name="blogger"
        )
    }
    enriched = build_author_caption(
        "@blogger/2026-01-01 12.00.00-实况-UID958392617677856_泡芙_发布作品-标题文字.jpg",
        sources,
    )
    assert enriched["author_name"] == "泡芙"
    assert enriched["author_url"] == "/flow?blog=blogger"
    assert enriched["caption_text"] == "标题文字"


def test_author_caption_skips_loose_root_files():
    from tiklocal.web.flow import build_author_caption

    assert build_author_caption("@default/v1.mp4") == {}


def test_feed_mix_items_carry_author_and_caption(client):
    res = client.get("/api/feed/mix?page=1&size=12&seed=fixed-seed&snapshot=1")
    assert res.status_code == 200
    items = res.get_json()["items"]

    by_name = {item["name"]: item for item in items}
    nested = by_name["@default/nested/v 3.mp4"]
    assert nested["author_name"] == "nested"
    assert nested["author_url"] == "/flow?blog=default"

    root_item = by_name["@default/v1.mp4"]
    assert "author_name" not in root_item


BLOGGER = 'UID100000000000001_示例博主_发布作品'


def _douyin_media(path, days_ago):
    """Write one exported media file; the mtime decides the feed order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {'.jpg', '.jpeg'}:
        Image.new('RGB', (40, 30), 'red').save(path)
    else:
        path.write_bytes(b'media')
    stamp = time.time() - days_ago * 86400
    os.utime(path, (stamp, stamp))


def _tag_client(tmp_path, files):
    media_root = tmp_path / 'media'
    for name, days_ago in files.items():
        _douyin_media(media_root / name, days_ago)
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()


def test_tag_feed_keeps_only_matching_items_with_caption_and_author(tmp_path):
    video = f'{BLOGGER}/2024-05-01 10.00.00-视频-示例博主-海边日落#海边.mp4'
    album_cover = f'{BLOGGER}/2024-05-03 10.00.00-图集-示例博主-海边人像_1.jpeg'
    album_member = f'{BLOGGER}/2024-05-03 10.00.00-图集-示例博主-海边人像_2.jpeg'
    client = _tag_client(tmp_path, {
        video: 1,
        f'{BLOGGER}/2024-05-02 10.00.00-视频-示例博主-城市夜景.mp4': 2,
        album_cover: 3,
        album_member: 3,
    })

    payload = client.get(
        '/api/feed/mix', query_string={'tag': '海边', 'size': 8, 'seed': 's'},
    ).get_json()

    assert payload['tag'] == '海边'
    assert payload['has_more'] is False
    assert [item['name'] for item in payload['items']] == [
        f'@default/{video}', f"album:@default/{album_cover}",
    ]

    post, group = payload['items']
    assert post['type'] == 'video'
    assert post['caption_text'] == '海边日落#海边'
    assert post['author_name'] == '示例博主'
    assert post['author_url'] == '/flow?blog=default'
    # The album reaches the feed as one swipeable card, tagged by its cover.
    assert group['type'] == 'image_group'
    assert group['caption_text'] == '海边人像'
    assert group['author_name'] == '示例博主'
    assert [child['name'] for child in group['items']] == [
        f'@default/{album_cover}', f'@default/{album_member}',
    ]
    assert client.get('/flow', query_string={'tag': '#海边'}).status_code == 200


def test_tag_matching_ignores_case_and_a_leading_hash(tmp_path):
    post = f'{BLOGGER}/2024-05-01 10.00.00-视频-示例博主-Summer#Beach 晚风.mp4'
    client = _tag_client(tmp_path, {
        post: 1,
        f'{BLOGGER}/2024-05-02 10.00.00-视频-示例博主-冬日暖阳.mp4': 2,
    })

    for raw_tag in ('#BEACH', 'beach', ' #Beach '):
        payload = client.get(
            '/api/feed/mix', query_string={'tag': raw_tag, 'size': 8, 'seed': 's'},
        ).get_json()
        assert [item['name'] for item in payload['items']] == [f'@default/{post}']
        assert payload['tag'].casefold() == 'beach'
        assert not payload['tag'].startswith('#')


def test_unknown_tag_returns_an_empty_feed(tmp_path):
    client = _tag_client(tmp_path, {
        f'{BLOGGER}/2024-05-01 10.00.00-视频-示例博主-标题.mp4': 1,
    })

    unknown = client.get(
        '/api/feed/mix', query_string={'tag': '不存在的标签', 'size': 8, 'seed': 's'},
    ).get_json()
    assert unknown['items'] == []
    assert unknown['has_more'] is False
    assert unknown['tag'] == '不存在的标签'

    # A lone '#' names no tag, so the filtered feed is simply empty.
    lone_hash = client.get(
        '/api/feed/mix', query_string={'tag': '#', 'size': 8, 'seed': 's'},
    ).get_json()
    assert lone_hash['items'] == []
    assert lone_hash['has_more'] is False


def test_tag_feed_paginates_newest_first(tmp_path):
    files = {
        f'{BLOGGER}/2024-06-01 10.00.00-视频-示例博主-夏日第{index:02d}集#夏日.mp4': index + 1
        for index in range(12)
    }
    client = _tag_client(tmp_path, files)
    expected = [f'@default/{name}' for name in files]

    first = client.get(
        '/api/feed/mix', query_string={'tag': '夏日', 'page': 1, 'size': 8, 'seed': 's'},
    ).get_json()
    second = client.get(
        '/api/feed/mix', query_string={'tag': '夏日', 'page': 2, 'size': 8, 'seed': 's'},
    ).get_json()

    assert [item['name'] for item in first['items']] == expected[:8]
    assert first['has_more'] is True
    assert [item['name'] for item in second['items']] == expected[8:]
    assert second['has_more'] is False
    assert all(item['caption_text'].endswith('#夏日')
               for item in first['items'] + second['items'])
