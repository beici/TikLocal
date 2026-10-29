import pytest

from tiklocal.app import create_app


@pytest.fixture
def client(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'movie.mp4').write_bytes(b'video')
    return create_app({'TESTING': True, 'MEDIA_ROOT': media_root}).test_client()


def save(client, position, duration=600, uri='movie.mp4'):
    return client.post('/api/playback', json={'uri': uri, 'position': position, 'duration': duration})


def library_item(client):
    return client.get('/api/library/items').get_json()['data']['items'][0]


def test_position_is_kept_only_where_resuming_makes_sense(client):
    assert save(client, 120).get_json()['saved'] is True
    item = library_item(client)
    assert (item['resume'], item['progress']) == (120, 0.2)
    assert b'resumePosition: 120.0,' in client.get('/detail/movie.mp4').data

    # Near the end counts as finished, and clears the resume point.
    assert save(client, 590).get_json()['saved'] is False
    assert 'resume' not in library_item(client)
    assert b'resumePosition: 0,' in client.get('/detail/movie.mp4').data

    # A glance (the first seconds) is not a reason to forget: the library quick
    # viewer reports the position when it switches clips, and that used to delete the
    # 40 minute position another device had saved. Nothing is stored, nothing is lost.
    assert save(client, 5).get_json()['saved'] is True
    assert 'resume' not in library_item(client)
    # A clip shorter than the resume threshold never gets a row either.
    assert save(client, 30, duration=45).get_json()['saved'] is False


def test_a_glance_does_not_erase_another_devices_position(client):
    # Device A stops five minutes into a ten minute clip; device B opens the same clip
    # in the quick viewer and swipes away three seconds later. The row used to be
    # deleted on that second report.
    assert save(client, 300).get_json()['saved'] is True
    assert save(client, 3).get_json()['saved'] is True
    assert library_item(client)['resume'] == 300


def test_rejects_bad_input_and_reset_clears_positions(client):
    assert save(client, 'soon').status_code == 400
    assert save(client, float('inf')).status_code == 400
    assert save(client, 120, uri='../outside.mp4').status_code == 404

    save(client, 120)
    client.delete('/api/activity')
    assert 'resume' not in library_item(client)
