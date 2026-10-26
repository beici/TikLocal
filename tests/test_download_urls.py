import os

import pytest

from tiklocal.app import create_app
from tiklocal.services.url_list import (
    MAX_LINES, UrlListStore, format_text, normalize_url, parse_text,
)


@pytest.fixture
def store(tmp_path):
    return UrlListStore(tmp_path / 'urls.txt', tmp_path / 'urls.json')


@pytest.fixture
def client(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True, exist_ok=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))

    def fake_execute_download(self, job_id):  # noqa: ARG001
        (self.media_root / 'mock-output.mp4').write_bytes(b'video')
        return 0, '', 'mock-output.mp4'

    monkeypatch.setattr('tiklocal.services.downloader.DownloadManager._execute_download', fake_execute_download)

    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    manager = app.extensions['download_manager']
    manager.start()
    try:
        yield app.test_client()
    finally:
        manager.close()


def test_normalize_url_accepts_only_http_and_https():
    assert normalize_url('https://www.douyin.com/video/1') == 'https://www.douyin.com/video/1'
    assert normalize_url('  http://example.com/a  ') == 'http://example.com/a'
    assert normalize_url('# comment') == ''
    assert normalize_url('') == ''
    assert normalize_url('ftp://example.com/a') == ''
    assert normalize_url('https://') == ''
    assert normalize_url('x' * 3000) == ''


def test_parse_text_skips_comments_blanks_and_duplicates():
    text = '\n'.join([
        '# my list',
        'https://a.example/1',
        '',
        'https://a.example/1',
        'https://b.example/2',
        'not-a-url',
    ])
    assert parse_text(text) == ['https://a.example/1', 'https://b.example/2']


def test_parse_text_caps_the_number_of_lines():
    text = '\n'.join(f'https://a.example/{index}' for index in range(MAX_LINES + 25))
    assert len(parse_text(text)) == MAX_LINES


def test_format_text_round_trips():
    lines = ['https://a.example/1', 'https://b.example/2']
    assert parse_text(format_text(lines)) == lines
    assert format_text([]) == ''


def test_store_add_replace_remove_and_clear(store):
    assert store.lines() == []
    assert store.add(['https://a.example/1']) == ['https://a.example/1']
    assert store.add(['https://a.example/1', 'https://b.example/2']) == [
        'https://a.example/1', 'https://b.example/2',
    ]
    assert store.remove('https://a.example/1') == ['https://b.example/2']
    assert store.replace(['https://c.example/3', 'https://d.example/4']) == [
        'https://c.example/3', 'https://d.example/4',
    ]
    assert store.clear() == []
    assert store.lines() == []


def test_store_rejects_relative_or_directory_source_path(store, tmp_path):
    with pytest.raises(ValueError):
        store.set_source_path('relative/user-url.txt')
    with pytest.raises(ValueError):
        store.set_source_path(str(tmp_path))

    target = tmp_path / 'user-url.txt'
    assert store.set_source_path(str(target)) == str(target)
    assert store.source_path() == str(target)


def test_store_pull_and_push_round_trip(store, tmp_path):
    target = tmp_path / 'user-url.txt'
    target.write_text('# server list\nhttps://a.example/1\nhttps://b.example/2\n', encoding='utf-8')
    store.set_source_path(str(target))

    pulled = store.pull()
    assert pulled['direction'] == 'pull'
    assert pulled['lines'] == ['https://a.example/1', 'https://b.example/2']
    assert store.lines() == pulled['lines']

    store.replace(['https://c.example/3'])
    pushed = store.push()
    assert pushed['direction'] == 'push'
    assert target.read_text(encoding='utf-8') == 'https://c.example/3\n'


def test_store_pull_reports_missing_file(store, tmp_path):
    store.set_source_path(str(tmp_path / 'missing.txt'))
    with pytest.raises(ValueError):
        store.pull()


def test_store_pull_rejects_oversized_file(store, tmp_path):
    target = tmp_path / 'big.txt'
    target.write_text('https://a.example/1\n' + ('#' * (3 * 1024 * 1024)), encoding='utf-8')
    store.set_source_path(str(target))
    with pytest.raises(ValueError):
        store.pull()


def test_store_push_requires_a_configured_path(store):
    with pytest.raises(ValueError):
        store.push()


def test_snapshot_reports_count_and_source_state(store, tmp_path):
    store.add(['https://a.example/1', 'https://b.example/2'])
    snapshot = store.snapshot()
    assert snapshot['count'] == 2
    assert snapshot['source_path'] == ''
    assert snapshot['source_exists'] is False

    target = tmp_path / 'user-url.txt'
    target.write_text('https://a.example/1\n', encoding='utf-8')
    store.set_source_path(str(target))
    assert store.snapshot()['source_exists'] is True


def test_api_returns_and_mutates_the_list(client):
    res = client.get('/api/download/urls')
    assert res.status_code == 200
    assert res.get_json()['data']['lines'] == []

    res = client.post('/api/download/urls', json={'action': 'add', 'text': 'https://a.example/1\nhttps://b.example/2'})
    assert res.status_code == 200
    assert res.get_json()['data']['lines'] == ['https://a.example/1', 'https://b.example/2']

    res = client.post('/api/download/urls', json={'action': 'remove', 'url': 'https://a.example/1'})
    assert res.get_json()['data']['lines'] == ['https://b.example/2']

    res = client.post('/api/download/urls', json={'action': 'replace', 'lines': ['https://c.example/3']})
    assert res.get_json()['data']['lines'] == ['https://c.example/3']

    res = client.post('/api/download/urls', json={'action': 'clear'})
    assert res.get_json()['data']['lines'] == []


def test_api_rejects_unknown_action(client):
    res = client.post('/api/download/urls', json={'action': 'explode'})
    assert res.status_code == 400
    assert res.get_json()['success'] is False


def test_api_export_returns_a_text_attachment(client):
    client.post('/api/download/urls', json={'action': 'add', 'lines': ['https://a.example/1']})
    res = client.get('/api/download/urls/export')
    assert res.status_code == 200
    assert res.headers['Content-Disposition'].startswith('attachment;')
    assert res.get_data(as_text=True) == 'https://a.example/1\n'


def test_api_sync_pull_and_push(client, tmp_path):
    target = tmp_path / 'user-url.txt'
    target.write_text('https://a.example/1\nhttps://b.example/2\n', encoding='utf-8')

    res = client.post('/api/download/urls/sync', json={'direction': 'pull', 'source_path': str(target)})
    assert res.status_code == 200
    data = res.get_json()['data']
    assert data['lines'] == ['https://a.example/1', 'https://b.example/2']
    assert data['source_path'] == str(target)
    assert data['source_exists'] is True

    client.post('/api/download/urls', json={'action': 'replace', 'lines': ['https://c.example/3']})
    res = client.post('/api/download/urls/sync', json={'direction': 'push'})
    assert res.status_code == 200
    assert target.read_text(encoding='utf-8') == 'https://c.example/3\n'


def test_api_sync_surfaces_a_bad_path(client):
    res = client.post('/api/download/urls/sync', json={'direction': 'pull', 'source_path': 'relative.txt'})
    assert res.status_code == 400
    assert res.get_json()['success'] is False


def test_api_queue_enqueues_every_url(client):
    client.post('/api/download/urls', json={
        'action': 'replace',
        'lines': ['https://a.example/1', 'https://b.example/2'],
    })
    res = client.post('/api/download/urls/queue', json={'engine': 'yt-dlp'})
    assert res.status_code == 200
    data = res.get_json()['data']
    assert data['queued'] == 2
    assert data['failed'] == []
    assert {item['url'] for item in data['items']} == {'https://a.example/1', 'https://b.example/2'}
    assert all(item['job_id'] for item in data['items'])


def test_api_queue_reports_failures(client, monkeypatch):
    client.post('/api/download/urls', json={'action': 'replace', 'lines': ['https://a.example/1']})

    def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError('downloads are disabled')

    monkeypatch.setattr('tiklocal.services.downloader.DownloadManager.enqueue', boom)
    res = client.post('/api/download/urls/queue', json={})
    data = res.get_json()['data']
    assert data['queued'] == 0
    assert data['failed'][0]['error'] == 'downloads are disabled'


def test_store_path_lives_under_the_instance_dir(client, tmp_path):
    res = client.get('/api/download/urls')
    store_path = res.get_json()['data']['store_path']
    assert os.path.dirname(store_path) == str(tmp_path / 'instance')
