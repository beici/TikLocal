"""Regressions from the round-35 audit (partial failures must not look like success)."""
import pytest

from tiklocal.app import create_app
from tiklocal.services.library_index import LibraryIndexer


def _client(tmp_path, name: str = 'media'):
    media = tmp_path / name
    media.mkdir(parents=True, exist_ok=True)
    (media / 'one.jpg').write_bytes(b'\xff\xd8\xff\xd9')
    return create_app({'TESTING': True, 'MEDIA_ROOT': media}).test_client(), media


def test_a_source_that_vanishes_mid_scan_keeps_its_rows(tmp_path, monkeypatch):
    # A USB drive unplugged between the directory walk and the stat() pass used to
    # look like "the source is empty now": every row was deleted and its playback
    # positions swept. The scan has to report the source as unavailable instead.
    client, media = _client(tmp_path)
    assert client.post('/api/library/sync').status_code == 200

    def _vanished(self, paths, time_states):
        return []

    monkeypatch.setattr(LibraryIndexer, '_records_for_paths', _vanished)
    payload = client.post('/api/library/sync').get_json()

    assert payload['success'] is True
    errors = payload['data']['source_errors']
    assert errors, payload['data']
    # Round 44: the browser gets a code and the folder's display name; the reason
    # (which names an absolute path) stays in the server log.
    failure = next(iter(errors.values()))
    assert failure['code'] == 'missing'
    assert failure['name']
    sources = client.get('/api/library/sources').get_json()['data']['sources']
    assert sources[0]['item_count'] == 1


def test_a_failed_folder_change_restores_the_previous_list(tmp_path, monkeypatch):
    # The list was written to disk before the new roots were resolved. When the
    # resolve failed the request answered 500 with the new list on disk and the old
    # one in memory, and no retry could repair the split.
    client, media = _client(tmp_path)
    extra = tmp_path / 'extra'
    extra.mkdir()
    (extra / 'two.jpg').write_bytes(b'\xff\xd8\xff\xd9')

    def _boom(library_service, sources):
        raise OSError('network path is not available')

    monkeypatch.setattr('tiklocal.web.settings.apply_sources', _boom)
    response = client.post('/api/library/sources', json={'path': str(extra)})

    assert response.status_code == 500
    sources = client.get('/api/library/sources').get_json()['data']['sources']
    assert [source['id'] for source in sources] == ['default']
    assert sources[0]['path'] == str(media.resolve())


