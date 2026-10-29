"""Who is allowed to do what, and does a refusal actually refuse.

Round 38 looked at the instance's identity boundary. TikLocal has exactly one
identity (the access password) and one privilege level, so the interesting failures
are the ones where a boundary is implied but never enforced: a library folder that
covers the data directory (auth.json holds the password hash and Flask's secret key),
a URI parser that keeps resolving against a root the user just changed, and a delete
that is reversible for the file but not for the history attached to it.
"""

from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.paths import get_data_dir, get_database_path
from tiklocal.services.database import AppDatabase
from tiklocal.services.library import LibraryService
from tiklocal.services.library_index import LibraryIndexer, MediaIndexStore
from tiklocal.services.media_sources_store import MediaSourceError, validate_folder
from tiklocal.services.trash import TrashService


def _database() -> AppDatabase:
    database = AppDatabase(get_database_path())
    database.migrate()
    return database


def _index(database: AppDatabase) -> MediaIndexStore:
    return MediaIndexStore(database)


def test_the_data_directory_cannot_become_a_media_source(tmp_path, monkeypatch):
    """A source covering the data directory served auth.json through /media.

    That file holds the password hash and the Flask secret_key, so reading it is
    enough to sign a session cookie and stay signed in after a password change.
    """
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'data'))
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(MediaSourceError) as raised:
        validate_folder(data_dir)

    assert raised.value.code == 'data_dir'


def test_a_media_root_never_serves_the_data_directory(tmp_path, monkeypatch):
    """`tiklocal ~` is legal and makes ~/.tiklocal a child of the media root.

    The settings page refuses such a folder, but a hand-edited config or MEDIA_ROOT
    can still produce one, so the resolver refuses the file even when it exists.
    """
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'data'))
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / 'auth.json').write_text('{"secret_key": "leak"}', encoding='utf-8')
    (tmp_path / 'clip.mp4').write_bytes(b'video')

    library = LibraryService(tmp_path)

    assert library.resolve_path('clip.mp4') is not None
    assert library.resolve_target_path('@default/data/auth.json') is None


def test_a_restored_file_keeps_its_resume_point(tmp_path, monkeypatch):
    """Deleting dropped the resume point, the affinity and the events in one
    transaction, so "restore" brought back the pixels and nothing else: the clip
    reopened at zero and the feed treated it as never watched.
    """
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'data'))
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'clip.mp4').write_bytes(b'video')
    uri = '@default/clip.mp4'

    library = LibraryService(media_root)
    database = _database()
    index = _index(database)
    LibraryIndexer(library, index).register_uris([uri])
    with database.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO playback_positions(uri, position, duration, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (uri, 12.5, 60.0, '2026-01-01T00:00:00+00:00'),
        )

    trash = TrashService(library)
    entry = trash.move(uri, state=index.snapshot_state(uri))
    index.delete(uri)
    with database.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM playback_positions WHERE uri = ?", (uri,)
        ).fetchone()[0] == 0

    restored_uri, state = trash.restore(entry['id'])
    LibraryIndexer(library, index).register_uris([restored_uri])
    index.restore_state(restored_uri, state)

    with database.connect() as conn:
        row = conn.execute(
            "SELECT position, duration FROM playback_positions WHERE uri = ?", (uri,)
        ).fetchone()
    assert row is not None, 'the resume point must survive delete + restore'
    assert row[0] == pytest.approx(12.5)


def test_restore_ignores_a_malformed_snapshot(tmp_path, monkeypatch):
    """The snapshot comes from a JSON file on disk, so a hand-edited entry must not
    turn a restore into a 500 after the file is already back in place."""
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'data'))
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'clip.mp4').write_bytes(b'video')

    index = _index(_database())
    index.restore_state('@default/clip.mp4', {'position': ['not', 'a', 'position']})
    index.restore_state('@default/clip.mp4', {'position': [1.0, 2.0, 3.0]})

    with _database().connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM playback_positions WHERE uri = '@default/clip.mp4'"
        ).fetchone()[0] == 1


def test_flask_testing_in_the_environment_does_not_open_the_instance(tmp_path, monkeypatch):
    """`FLASK_TESTING=1` used to be enough to turn TESTING on, which disables the
    access password and the media probe: a stray dev-shell variable silently opened
    a real instance to the network."""
    monkeypatch.setenv('FLASK_TESTING', '1')
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'data'))

    app = create_app({'MEDIA_ROOT': str(tmp_path / 'media')})

    assert app.config['TESTING'] is False
    assert app.extensions['auth_enabled'] is True
