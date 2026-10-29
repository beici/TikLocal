"""Media library folders managed from the settings page.

Covers the three things a user notices when they point TikLocal at another
folder: the folder is validated before anything is saved, the change is visible
immediately without a restart, and it is still there after a restart. It also
pins the recovery behaviour — an unreadable or corrupt folder list must fall back
to the launch configuration instead of starting with an empty library.
"""

import json
import logging

import pytest

from tiklocal.app import create_app
from tiklocal.services.library import MediaSource, build_media_sources
from tiklocal.services.media_sources_store import (
    MediaSourceError,
    MediaSourceStore,
    StoredMediaSources,
    add_source,
    apply_sources,
    remove_source,
    resolve_startup_sources,
    update_source,
    validate_folder,
)


def make_library(root):
    """A folder with one image, so a scan has something to index."""
    root.mkdir(parents=True, exist_ok=True)
    (root / 'pic.jpg').write_bytes(b'\xff\xd8\xff\xd9')
    return root


def client_for(root):
    return create_app({'TESTING': True, 'MEDIA_ROOT': root}).test_client()


# --- store round trip ---------------------------------------------------------

def test_store_round_trip_keeps_order_names_and_removals(tmp_path):
    store = MediaSourceStore(tmp_path / 'media_sources.json')
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    store.save(
        [
            MediaSource('default', 'Default', first),
            MediaSource('second', '第二目录', second),
        ],
        removed_ids=['old-one'],
    )

    stored = store.load()

    assert stored is not None
    assert [source.id for source in stored.sources] == ['default', 'second']
    assert stored.sources[1].name == '第二目录'
    assert stored.sources[1].path == second.resolve()
    assert stored.removed_ids == frozenset({'old-one'})


def test_missing_store_means_the_launch_configuration_still_owns_the_list(tmp_path):
    assert MediaSourceStore(tmp_path / 'never-written.json').load() is None


def test_a_store_with_no_usable_entries_reads_as_absent(tmp_path):
    path = tmp_path / 'media_sources.json'
    path.write_text(json.dumps({'version': 1, 'sources': [{'id': '', 'path': ''}]}), encoding='utf-8')
    # An empty list must not be mistaken for "the user deleted every folder".
    assert MediaSourceStore(path).load() is None


def test_unreadable_store_raises_instead_of_reporting_no_folders(tmp_path):
    path = tmp_path / 'media_sources.json'
    path.mkdir()  # a directory where a file belongs: readable path, unreadable store
    with pytest.raises(OSError):
        MediaSourceStore(path).load()


# --- validation ---------------------------------------------------------------

def test_validate_folder_accepts_a_real_folder_and_strips_explorer_quotes(tmp_path):
    folder = make_library(tmp_path / 'Videos')
    assert validate_folder(f'"{folder}"') == folder.resolve()


@pytest.mark.parametrize(
    ('raw', 'code'),
    [
        ('', 'required'),
        ('   ', 'required'),
        (r'C:\definitely\not\here\tiklocal-test', 'missing'),
    ],
)
def test_validate_folder_rejects_missing_folders(tmp_path, raw, code):
    with pytest.raises(MediaSourceError) as error:
        validate_folder(raw)
    assert error.value.code == code


def test_validate_folder_rejects_a_file(tmp_path):
    file_path = tmp_path / 'clip.mp4'
    file_path.write_bytes(b'x')
    with pytest.raises(MediaSourceError) as error:
        validate_folder(file_path)
    assert error.value.code == 'not_dir'


def test_add_source_names_the_folder_when_no_name_is_given(tmp_path):
    sources, source = add_source([], path=make_library(tmp_path / 'Videos'))
    assert source.id == 'videos'
    assert source.name == 'Videos'
    assert sources == [source]


def test_add_source_keeps_ids_unique_when_two_folders_share_a_name(tmp_path):
    first = make_library(tmp_path / 'a' / 'Videos')
    second = make_library(tmp_path / 'b' / 'Videos')
    sources, first_source = add_source([], path=first)
    sources, second_source = add_source(sources, path=second)
    assert first_source.id == 'videos'
    assert second_source.id == 'videos-2'
    assert [source.id for source in sources] == ['videos', 'videos-2']


def test_add_source_rejects_the_same_folder_twice_case_insensitively(tmp_path):
    folder = make_library(tmp_path / 'Videos')
    sources, _ = add_source([], path=folder)
    with pytest.raises(MediaSourceError) as error:
        add_source(sources, path=str(folder).upper())
    assert error.value.code == 'duplicate'


def test_add_source_limits_the_number_of_folders(tmp_path):
    from tiklocal.services import media_sources_store as module

    sources = [MediaSource(f's{index}', f'S{index}', make_library(tmp_path / f's{index}'))
               for index in range(module.MAX_SOURCES)]
    with pytest.raises(MediaSourceError) as error:
        add_source(sources, path=make_library(tmp_path / 'overflow'))
    assert error.value.code == 'too_many'


# --- edit and remove ----------------------------------------------------------

def test_update_source_renames_without_touching_the_path(tmp_path):
    folder = make_library(tmp_path / 'Videos')
    sources, source = add_source([], path=folder)
    sources, updated = update_source(sources, source.id, name='我的视频')
    assert updated.id == source.id
    assert updated.name == '我的视频'
    assert updated.path == folder.resolve()


def test_update_source_moves_the_folder_and_rejects_stealing_another_path(tmp_path):
    first = make_library(tmp_path / 'first')
    second = make_library(tmp_path / 'second')
    third = make_library(tmp_path / 'third')
    sources, first_source = add_source([], path=first)
    sources, second_source = add_source(sources, path=second)

    sources, moved = update_source(sources, first_source.id, path=third)
    assert moved.path == third.resolve()

    with pytest.raises(MediaSourceError) as error:
        update_source(sources, second_source.id, path=moved.path)
    assert error.value.code == 'duplicate'


def test_update_source_reports_unknown_ids(tmp_path):
    with pytest.raises(MediaSourceError) as error:
        update_source([], 'ghost', name='x')
    assert error.value.code == 'not_found'


def test_remove_source_keeps_the_default_folder_and_the_download_target(tmp_path):
    sources = [MediaSource('default', 'Default', make_library(tmp_path / 'main'))]
    sources, extra = add_source(sources, path=make_library(tmp_path / 'extra'))

    with pytest.raises(MediaSourceError) as error:
        remove_source(sources, 'default')
    assert error.value.code == 'default'

    with pytest.raises(MediaSourceError) as error:
        remove_source(sources, extra.id, protected_ids=['extra'])
    assert error.value.code == 'download_target'

    assert [source.id for source in remove_source(sources, extra.id)] == ['default']


# --- startup merge ------------------------------------------------------------

def test_saved_folders_win_over_the_launch_configuration(tmp_path):
    saved = make_library(tmp_path / 'saved')
    launch = make_library(tmp_path / 'launch')
    stored = StoredMediaSources((MediaSource('default', 'Default', saved),))

    resolved = resolve_startup_sources(stored, build_media_sources(launch))

    assert [source.path for source in resolved] == [saved.resolve()]


def test_ignoring_a_different_launch_folder_is_logged(tmp_path, caplog):
    # The settings page has to keep winning (a folder removed there must not come
    # back), but a start that names another directory cannot be dropped without a
    # word: downloads and deletions keep landing in the saved folder while the
    # banner shows the one from the command line.
    saved = make_library(tmp_path / 'saved')
    launch = make_library(tmp_path / 'launch')
    stored = StoredMediaSources((MediaSource('default', 'Default', saved),))

    with caplog.at_level(logging.WARNING, logger='tiklocal.services.media_sources_store'):
        resolved = resolve_startup_sources(stored, build_media_sources(launch))

    assert [source.path for source in resolved] == [saved.resolve()]
    assert 'wins over the launch directory' in caplog.text
    assert str(saved.resolve()) in caplog.text and str(launch.resolve()) in caplog.text


def test_the_same_folder_named_twice_does_not_warn(tmp_path, caplog):
    # Starting with the folder the settings page already saved is the normal case;
    # warning there would train people to ignore the message.
    saved = make_library(tmp_path / 'saved')
    stored = StoredMediaSources((MediaSource('default', 'Default', saved),))

    with caplog.at_level(logging.WARNING, logger='tiklocal.services.media_sources_store'):
        resolved = resolve_startup_sources(stored, build_media_sources(saved))

    assert [source.path for source in resolved] == [saved.resolve()]
    assert 'wins over the launch directory' not in caplog.text


def test_a_folder_removed_in_settings_is_not_reused_silently(tmp_path, caplog):
    extra = make_library(tmp_path / 'extra')
    launch = make_library(tmp_path / 'launch')
    stored = StoredMediaSources((MediaSource('default', 'Default', launch),), frozenset({'extra'}))

    with caplog.at_level(logging.WARNING, logger='tiklocal.services.media_sources_store'):
        resolved = resolve_startup_sources(stored, build_media_sources(launch, {'extra': str(extra)}))

    assert [source.id for source in resolved] == ['default']
    assert "removed in settings" in caplog.text and 'extra' in caplog.text


def test_removed_launch_folders_do_not_come_back_and_launch_extras_still_work(tmp_path):
    saved = make_library(tmp_path / 'saved')
    extra = make_library(tmp_path / 'extra')
    stored = StoredMediaSources(
        (MediaSource('default', 'Default', saved),), frozenset({'extra'}),
    )
    launch = build_media_sources(
        tmp_path / 'launch', {'default': str(tmp_path / 'launch'), 'extra': str(extra)},
    )

    resolved = resolve_startup_sources(stored, launch)

    assert [source.id for source in resolved] == ['default']


def test_unreachable_saved_default_falls_back_to_the_launch_folder(tmp_path):
    launch = make_library(tmp_path / 'launch')
    stored = StoredMediaSources((MediaSource('default', 'Default', tmp_path / 'unplugged'),))

    resolved = resolve_startup_sources(stored, build_media_sources(launch))

    assert resolved[0].path == launch.resolve()


def test_apply_sources_rebuilds_the_shared_library_service(tmp_path):
    from tiklocal.services.library import LibraryService

    first = make_library(tmp_path / 'first')
    second = make_library(tmp_path / 'second')
    library = LibraryService(first)
    applied = apply_sources(library, [MediaSource('default', 'Default', first), MediaSource('second', 'Second', second)])

    assert [source.id for source in applied] == ['default', 'second']
    assert library.sources_by_id['second'].path == second.resolve()
    assert library.media_root == first.resolve()
    # The scanner and the URI parser walk _resolved_roots; a stale copy would keep
    # answering 404 for every file of the new folder.
    assert [root for _, root in library._resolved_roots] == [first.resolve(), second.resolve()]


# --- HTTP endpoints -----------------------------------------------------------

def test_settings_page_renders_the_media_folder_card(tmp_path):
    client = client_for(make_library(tmp_path / 'media'))
    response = client.get('/settings/')
    assert response.status_code == 200
    assert b'media-source-list' in response.data
    assert '添加目录'.encode() in response.data


def test_sources_endpoint_lists_the_running_folder_with_counts(tmp_path):
    media = make_library(tmp_path / 'media')
    client = client_for(media)
    payload = client.get('/api/library/sources').get_json()

    assert payload['success'] is True
    sources = payload['data']['sources']
    assert [source['id'] for source in sources] == ['default']
    assert sources[0]['path'] == str(media.resolve())
    assert sources[0]['is_default'] is True
    assert sources[0]['removable'] is False
    assert sources[0]['item_count'] >= 1


def test_adding_a_folder_is_live_and_persisted(tmp_path):
    media = make_library(tmp_path / 'media')
    extra = make_library(tmp_path / 'extra')
    (extra / 'second.jpg').write_bytes(b'\xff\xd8\xff\xd9')
    client = client_for(media)
    assert client.get('/media/@extra/second.jpg').status_code == 404

    response = client.post('/api/library/sources', json={'path': str(extra), 'name': 'Extra'})

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert [source['id'] for source in payload['data']['sources']] == ['default', 'extra']
    assert payload['data']['scan']['indexed'] >= 2
    # Live: the same request that saved the folder can already serve its files.
    assert client.get('/media/@extra/second.jpg').status_code == 200
    counts = {row['id']: row['item_count'] for row in client.get('/api/library/sources').get_json()['data']['sources']}
    assert counts == {'default': 1, 'extra': 2}


def test_the_scan_result_uses_the_keys_the_page_reads(tmp_path):
    # The settings page reported "rescanned 0 items" for an unreachable folder
    # because this payload named the failures 'unavailable' while every reader
    # (and /api/library/sync) uses 'unavailable_sources'. It also used to hand the
    # absolute path of the folder list file to every LAN visitor.
    media = make_library(tmp_path / 'media')
    extra = make_library(tmp_path / 'extra')
    client = client_for(media)

    payload = client.post('/api/library/sources', json={'path': str(extra), 'name': 'Extra'}).get_json()

    data = payload['data']
    assert 'store_path' not in data
    scan = data['scan']
    assert scan['unavailable_sources'] == []
    assert scan['source_errors'] == {}
    assert 'unavailable' not in scan


def test_a_saved_folder_survives_a_restart(tmp_path):
    media = make_library(tmp_path / 'media')
    extra = make_library(tmp_path / 'extra')
    client = client_for(media)
    assert client.post('/api/library/sources', json={'path': str(extra)}).status_code == 200

    # A second create_app over the same instance directory is what a restart does.
    restarted = create_app({'TESTING': True, 'MEDIA_ROOT': media}).test_client()
    sources = restarted.get('/api/library/sources').get_json()['data']['sources']

    assert [source['id'] for source in sources] == ['default', 'extra']
    assert sources[1]['path'] == str(extra.resolve())
    assert restarted.get('/media/@extra/pic.jpg').status_code == 200


def test_removing_a_folder_drops_it_and_its_index_rows(tmp_path):
    media = make_library(tmp_path / 'media')
    extra = make_library(tmp_path / 'extra')
    client = client_for(media)
    assert client.post('/api/library/sources', json={'path': str(extra)}).status_code == 200
    assert client.get('/media/@extra/pic.jpg').status_code == 200

    response = client.delete('/api/library/sources/extra')

    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert [source['id'] for source in payload['data']['sources']] == ['default']
    assert client.get('/media/@extra/pic.jpg').status_code == 404
    remaining = client.get('/api/library/sources').get_json()['data']['sources']
    assert [(row['id'], row['item_count']) for row in remaining] == [('default', 1)]


def test_editing_a_folder_path_is_live(tmp_path):
    media = make_library(tmp_path / 'media')
    extra = make_library(tmp_path / 'extra')
    moved = make_library(tmp_path / 'moved')
    (extra / 'extra-only.jpg').write_bytes(b'\xff\xd8\xff\xd9')
    (moved / 'moved-only.jpg').write_bytes(b'\xff\xd8\xff\xd9')
    client = client_for(media)
    assert client.post('/api/library/sources', json={'path': str(extra)}).status_code == 200

    response = client.patch('/api/library/sources/extra', json={'path': str(moved), 'name': 'Moved'})

    assert response.status_code == 200, response.get_json()
    sources = response.get_json()['data']['sources']
    assert sources[1]['name'] == 'Moved'
    assert sources[1]['path'] == str(moved.resolve())
    assert client.get('/media/@extra/moved-only.jpg').status_code == 200
    assert client.get('/media/@extra/extra-only.jpg').status_code == 404


@pytest.mark.parametrize(
    ('body', 'status', 'code'),
    [
        ({}, 400, 'required'),
        ({'path': ''}, 400, 'required'),
        ({'path': r'C:\definitely\not\here\tiklocal-test'}, 400, 'missing'),
    ],
)
def test_bad_folder_requests_are_rejected_with_a_message(tmp_path, body, status, code):
    client = client_for(make_library(tmp_path / 'media'))
    response = client.post('/api/library/sources', json=body)
    payload = response.get_json()

    assert response.status_code == status
    assert payload['success'] is False
    assert payload['error']  # translated sentence, never a bare message key
    assert not payload['error'].startswith('settings.')


def test_removing_the_default_folder_is_refused(tmp_path):
    client = client_for(make_library(tmp_path / 'media'))
    response = client.delete('/api/library/sources/default')
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_duplicate_folder_is_refused_over_http(tmp_path):
    media = make_library(tmp_path / 'media')
    client = client_for(media)
    response = client.post('/api/library/sources', json={'path': str(media)})
    assert response.status_code == 400
    assert 'already' in response.get_json()['error'].lower() or '已经' in response.get_json()['error']


def test_a_corrupt_folder_list_falls_back_to_the_launch_folder(tmp_path):
    media = make_library(tmp_path / 'media')
    # instance dir is created by create_app; write the broken store first.
    data_dir = tmp_path / 'tiklocal-data'
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / 'media_sources.json').write_text('{not json', encoding='utf-8')

    app = create_app({'TESTING': True, 'MEDIA_ROOT': media})
    sources = app.test_client().get('/api/library/sources').get_json()['data']['sources']

    assert [source['path'] for source in sources] == [str(media.resolve())]
