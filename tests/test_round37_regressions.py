"""Version and dependency boundaries.

Round 37 asked what each layer assumes about the runtime it lands in: the SQLite
library under Python, the ffmpeg/yt-dlp builds on PATH, the browser version running
the page. The regressions here pin the places where an unmet assumption used to
degrade into silence — a database that starts but stays empty, a store that raises
past its own error handler, a download tool whose "unknown option" reads like a
network failure.
"""

import sqlite3

import pytest

from tiklocal.services.collections import (
    COLLECTIONS_VERSION,
    CollectionStore,
    CollectionStoreCorrupt,
)
from tiklocal.services.database import (
    MIGRATIONS,
    AppDatabase,
    DatabaseVersionError,
)
from tiklocal.services.download_tools import version_is_supported, version_tuple


def test_migrations_are_numbered_without_gaps_or_duplicates():
    """Two branches adding Migration(18, …) used to make the second one a no-op.

    `_is_applied` only looks at the version, so the duplicate was skipped on fresh
    and existing databases alike and nobody noticed the missing schema change.
    """
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))


def test_an_old_sqlite_is_refused_before_the_first_sync(tmp_path, monkeypatch):
    """UPSERT/ROW_NUMBER need 3.24/3.25; without the guard the app looked healthy.

    The index writer swallowed the syntax error as "serving the previous index", so
    an old libsqlite3 produced an empty library and one log line.
    """
    monkeypatch.setattr(sqlite3, 'sqlite_version_info', (3, 24, 0))
    database = AppDatabase(tmp_path / 'index.sqlite3')
    with pytest.raises(DatabaseVersionError):
        database.migrate()


def test_migrations_survive_a_lost_bookmark(tmp_path):
    """A restored backup can keep the schema but lose schema_migrations.

    Migrations 011/012/013/015 issued bare DDL, so "duplicate column name" (not one
    of the damaged-database markers) made the app refuse to start forever.
    """
    database = AppDatabase(tmp_path / 'index.sqlite3')
    database.migrate()
    with database.connect() as conn:
        conn.execute('DELETE FROM schema_migrations')
        conn.commit()
    database.migrate()
    with database.connect() as conn:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(media_items)')}
    assert {'search_text', 'live_video_uri', 'album_key'} <= columns


def test_a_collections_version_that_is_not_an_integer_does_not_break_the_page(tmp_path):
    """"version": "2.0" raised ValueError outside _read's handler.

    /collections and every write then answered 500 forever and the file was never
    quarantined or repaired.
    """
    path = tmp_path / 'collections.json'
    path.write_text('{"version": "2.0", "collections": []}', encoding='utf-8')
    store = CollectionStore(path)
    assert store.list() == []
    store.create('Trip')
    assert [item['name'] for item in store.list()] == ['Trip']


def test_a_newer_collections_version_is_refused_instead_of_downgraded(tmp_path):
    path = tmp_path / 'collections.json'
    path.write_text(
        '{"version": %d, "collections": [{"id": "a", "name": "New", "future_field": 1}]}'
        % (COLLECTIONS_VERSION + 1),
        encoding='utf-8',
    )
    store = CollectionStore(path)
    with pytest.raises(CollectionStoreCorrupt):
        store.list()
    # The file must survive untouched: rewriting it would drop future_field.
    assert 'future_field' in path.read_text(encoding='utf-8')


@pytest.mark.parametrize('version,expected', [
    ('2024.08.06', (2024, 8, 6)),
    ('yt-dlp version 2023.1.1', (2023, 1, 1)),
    ('1.22', (1, 22, 0)),
    ('', None),
])
def test_version_tuple_reads_the_leading_numbers(version, expected):
    assert version_tuple(version) == expected


@pytest.mark.parametrize('command,version,expected', [
    ('yt-dlp', '2024.08.06', True),
    ('yt-dlp', '2021.12.01', False),
    ('yt-dlp', '', True),           # unknown version must not block the tool
    ('gallery-dl', '1.21.0', False),
    ('gallery-dl', '1.26.9', True),
    ('ffmpeg', '4.0', True),        # no floor recorded
])
def test_version_is_supported(command, version, expected):
    assert version_is_supported(command, version) is expected
