"""Regressions for the round-08 findings (data layer, cross-store, CLI)."""

from urllib.parse import quote

import pytest

from tiklocal.app import create_app
from tiklocal.config import normalize_media_sources
from tiklocal.paths import get_database_path
from tiklocal.run import validate_config_types
from tiklocal.services.database import AppDatabase
from tiklocal.services.library import LibraryService, MediaSource
from tiklocal.services.library_index import LibraryIndexer, MediaIndexStore


@pytest.fixture
def client(tmp_path):
    media_root = tmp_path / 'media'
    (media_root / 'album').mkdir(parents=True)
    (media_root / 'album' / 'a b.jpg').write_bytes(b'image')
    (media_root / 'clip.mp4').write_bytes(b'video')
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()


def trash_delete(client, uri):
    response = client.post(f'/delete/{quote(uri)}', headers={'Accept': 'application/json'})
    assert response.status_code == 200
    return response.get_json()['id']


def create_collection(client, name='Trip'):
    created = client.post('/api/collections', json={'name': name})
    assert created.status_code == 200
    return created.get_json()['data']['item']['id']


def collection_card(client, collection_id):
    cards = client.get('/api/collections').get_json()['data']['items']
    return next(card for card in cards if card['id'] == collection_id)


def test_a_damaged_database_is_quarantined_instead_of_blocking_startup(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'clip.mp4').write_bytes(b'video')
    database_path = get_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.write_bytes(b'not a database' * 64)

    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})

    assert list(database_path.parent.glob('*.corrupt')), 'the damaged file must be kept for inspection'
    response = app.test_client().get('/api/library/items')
    assert response.status_code == 200
    assert [item['name'] for item in response.get_json()['data']['items']] == ['@default/clip.mp4']


def test_rows_of_a_source_removed_from_the_config_are_dropped(tmp_path):
    media_a = tmp_path / 'a'
    media_b = tmp_path / 'b'
    media_a.mkdir()
    media_b.mkdir()
    (media_a / 'a0.mp4').write_bytes(b'a')
    (media_b / 'b0.mp4').write_bytes(b'b')
    database = AppDatabase(tmp_path / 'tiklocal.sqlite3')
    database.migrate()

    def sync(sources):
        library = LibraryService(media_a, media_sources=sources)
        LibraryIndexer(library, MediaIndexStore(database)).sync()
        return {record['source_id'] for record in MediaIndexStore(database).records()}

    both = [
        MediaSource(id='a', name='a', path=media_a),
        MediaSource(id='b', name='b', path=media_b),
    ]
    assert sync(both) == {'a', 'b'}
    # Dropping @b from config.yaml used to leave its rows behind, so the grid kept
    # rendering cards whose /media and /detail answered 404 forever.
    assert sync([MediaSource(id='a', name='a', path=media_a)]) == {'a'}


def test_collection_card_count_follows_the_index_after_a_delete(client):
    collection_id = create_collection(client)
    client.post(
        f'/api/collections/{collection_id}/items',
        json={'uris': ['@default/album/a b.jpg', '@default/clip.mp4']},
    )
    assert collection_card(client, collection_id)['item_count'] == 2

    entry_id = trash_delete(client, 'album/a b.jpg')
    assert client.delete(f'/api/trash/{entry_id}').status_code == 200

    assert collection_card(client, collection_id)['item_count'] == 1


def test_purge_drops_the_uri_from_favorites_and_collections(client):
    assert client.post('/api/favorite/' + quote('album/a b.jpg', safe='')).status_code == 200
    collection_id = create_collection(client)
    client.post(f'/api/collections/{collection_id}/items', json={'uris': ['@default/album/a b.jpg']})
    assert client.get('/api/library/items?scope=favorite').get_json()['data']['total'] == 1

    entry_id = trash_delete(client, 'album/a b.jpg')
    assert client.delete(f'/api/trash/{entry_id}').status_code == 200

    # A saved list keeps the URI, so a later file that reuses the name would look
    # favourited/collected without the user ever touching it.
    assert client.get('/api/library/items?scope=favorite').get_json()['data']['total'] == 0
    assert client.get(f'/api/collections/{collection_id}/items').get_json()['data']['total'] == 0


def test_restore_succeeds_even_when_the_index_write_fails(client, monkeypatch):
    media_root = client.application.config['MEDIA_ROOT']
    entry_id = trash_delete(client, 'album/a b.jpg')

    def broken_upsert(self, records):
        raise RuntimeError('index is unavailable')

    monkeypatch.setattr(MediaIndexStore, 'upsert', broken_upsert)

    response = client.post(f'/api/trash/{entry_id}/restore')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['indexed'] is False
    # The file is back on disk; the missing index row only hides it until the next sync.
    assert (media_root / 'album' / 'a b.jpg').exists()


def test_explicit_media_root_replaces_a_configured_default_source(tmp_path):
    config = {'media_sources': [{'id': 'default', 'path': 'D:/configured'}]}

    sources = normalize_media_sources(config, None, media_root=str(tmp_path))

    assert [source['id'] for source in sources] == ['default']
    assert sources[0]['path'] == str(tmp_path)


def test_config_type_mistakes_are_reported():
    with pytest.raises(SystemExit) as media_root_error:
        validate_config_types({'media_root': 123})
    assert media_root_error.value.code == 2

    with pytest.raises(SystemExit) as port_error:
        validate_config_types({'port': True})
    assert port_error.value.code == 2
