import contextlib
import datetime
import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from tiklocal.services.search_text import normalize as normalize_search_text

LOGGER = logging.getLogger(__name__)

# UPSERT needs 3.24 and ROW_NUMBER() needs 3.25; both are used by the index writer.
MINIMUM_SQLITE = (3, 25, 0)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: Callable[[sqlite3.Connection], None]


class DatabaseVersionError(RuntimeError):
    """The database cannot be opened by this build.

    Raised for a schema written by a newer TikLocal, and for a SQLite library
    too old to run the queries this build issues.
    """


def _migrate_001_create_image_vectors(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS image_vectors (
          uri TEXT PRIMARY KEY,
          source_id TEXT NOT NULL,
          rel_path TEXT NOT NULL,
          model TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          image_max_size INTEGER NOT NULL,
          image_quality INTEGER NOT NULL,
          mtime REAL NOT NULL,
          size_bytes INTEGER NOT NULL,
          embedding BLOB NOT NULL,
          embedding_norm REAL NOT NULL,
          indexed_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_image_vectors_source ON image_vectors(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_image_vectors_model ON image_vectors(model, dimensions)")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_image_vectors_state
        ON image_vectors(model, dimensions, image_max_size, image_quality, mtime, size_bytes)
        """
    )


def _migrate_002_create_media_similarity_groups(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_similarity_groups (
          group_key TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          seed_uri TEXT NOT NULL,
          score REAL NOT NULL,
          item_count INTEGER NOT NULL,
          threshold REAL NOT NULL,
          min_group_size INTEGER NOT NULL,
          max_group_size INTEGER NOT NULL,
          exclusive INTEGER NOT NULL,
          model TEXT NOT NULL,
          dimensions INTEGER NOT NULL,
          image_max_size INTEGER NOT NULL,
          image_quality INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_similarity_group_items (
          group_key TEXT NOT NULL,
          uri TEXT NOT NULL,
          rank INTEGER NOT NULL,
          score REAL NOT NULL,
          PRIMARY KEY (group_key, uri),
          FOREIGN KEY (group_key) REFERENCES media_similarity_groups(group_key) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_similarity_groups_rank
        ON media_similarity_groups(kind, item_count DESC, score DESC, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_similarity_group_items_group_rank
        ON media_similarity_group_items(group_key, rank)
        """
    )


def _migrate_003_create_media_activity(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          uri TEXT NOT NULL,
          media_type TEXT NOT NULL,
          surface TEXT NOT NULL,
          event_type TEXT NOT NULL,
          consumed_ratio REAL,
          visible_ms INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_events_uri_created
        ON media_events(uri, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_affinity (
          uri TEXT PRIMARY KEY,
          media_type TEXT NOT NULL,
          impressions INTEGER NOT NULL DEFAULT 0,
          completes INTEGER NOT NULL DEFAULT 0,
          skips INTEGER NOT NULL DEFAULT 0,
          favorites INTEGER NOT NULL DEFAULT 0,
          replays INTEGER NOT NULL DEFAULT 0,
          total_visible_ms INTEGER NOT NULL DEFAULT 0,
          affinity_score REAL NOT NULL DEFAULT 0,
          last_shown_at TEXT,
          last_consumed_at TEXT,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_affinity_recent
        ON media_affinity(last_shown_at DESC)
        """
    )


def _migrate_004_create_preference_dimensions(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS preference_dimensions (
          dimension_type TEXT NOT NULL,
          dimension_value TEXT NOT NULL,
          positive_score REAL NOT NULL DEFAULT 0,
          negative_score REAL NOT NULL DEFAULT 0,
          sample_count INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (dimension_type, dimension_value)
        )
        """
    )


def _migrate_005_create_media_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_items (
          uri TEXT PRIMARY KEY,
          source_id TEXT NOT NULL,
          rel_path TEXT NOT NULL,
          filename TEXT NOT NULL,
          parent_path TEXT NOT NULL,
          media_type TEXT NOT NULL,
          extension TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          mtime REAL NOT NULL,
          discovered_at TEXT NOT NULL,
          indexed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_items_type_mtime
        ON media_items(media_type, mtime DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_items_source_parent
        ON media_items(source_id, parent_path)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_index_state (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          last_synced_at TEXT NOT NULL,
          item_count INTEGER NOT NULL
        )
        """
    )


def _migrate_006_add_media_capture_time(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(media_items)").fetchall()
    }
    additions = {
        "captured_at": "REAL",
        "captured_local_date": "TEXT",
        "capture_year": "TEXT",
        "capture_month": "TEXT",
        "time_source": "TEXT NOT NULL DEFAULT 'filesystem_mtime'",
        "time_confidence": "TEXT NOT NULL DEFAULT 'fallback'",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE media_items ADD COLUMN {name} {definition}")
    conn.execute(
        """
        UPDATE media_items
        SET captured_at = COALESCE(captured_at, mtime),
            captured_local_date = COALESCE(
              captured_local_date,
              strftime('%Y-%m-%dT%H:%M:%S', mtime, 'unixepoch', 'localtime')
            ),
            capture_year = COALESCE(capture_year, substr(captured_local_date, 1, 4)),
            capture_month = COALESCE(capture_month, substr(captured_local_date, 1, 7))
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_items_capture_date
        ON media_items(capture_month DESC, media_type, captured_at DESC, uri)
        """
    )


def _migrate_007_add_capture_calendar_buckets(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(media_items)").fetchall()
    }
    for name in ("capture_year", "capture_month"):
        if name not in columns:
            conn.execute(f"ALTER TABLE media_items ADD COLUMN {name} TEXT")
    conn.execute(
        """
        UPDATE media_items
        SET capture_year = COALESCE(capture_year, substr(captured_local_date, 1, 4)),
            capture_month = COALESCE(capture_month, substr(captured_local_date, 1, 7))
        """
    )
    conn.execute("DROP INDEX IF EXISTS idx_media_items_capture_date")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_items_capture_month
        ON media_items(capture_month DESC, media_type, captured_at DESC, uri)
        """
    )


def _migrate_008_add_time_metadata_version(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(media_items)").fetchall()
    }
    if "time_metadata_version" not in columns:
        conn.execute(
            "ALTER TABLE media_items ADD COLUMN time_metadata_version INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_009_create_playback_positions(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playback_positions (
          uri TEXT PRIMARY KEY,
          position REAL NOT NULL,
          duration REAL NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )


def _migrate_010_add_media_dimensions(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(media_items)").fetchall()}
    for name, kind in (("width", "INTEGER"), ("height", "INTEGER"), ("probed_mtime", "REAL")):
        if name not in columns:
            conn.execute(f"ALTER TABLE media_items ADD COLUMN {name} {kind}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def _migrate_011_simplify_media_vectors(conn: sqlite3.Connection) -> None:
    """Keep one vector per file; the model column decides which rows are current."""
    if _table_exists(conn, 'media_vectors'):
        # The schema_migrations bookmark can be lost while the schema survives (a
        # partially restored backup, a manual cleanup). Without this guard the
        # CREATE below raised "table media_vectors already exists", which is not one
        # of the damaged-database markers, so the app refused to start forever.
        #
        # ``photo_groups`` was created by this same step, so a bookmark lost between
        # the two left the table missing while the version said "applied": the /flow
        # related-groups query then failed with "no such table" on every request.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photo_groups (
              position INTEGER NOT NULL,
              rank INTEGER NOT NULL,
              uri TEXT NOT NULL,
              PRIMARY KEY (position, rank)
            )
        """)
        return
    conn.execute("""
        CREATE TABLE media_vectors (
          uri TEXT PRIMARY KEY,
          media_type TEXT NOT NULL,
          model TEXT NOT NULL,
          mtime REAL NOT NULL,
          size_bytes INTEGER NOT NULL,
          embedding BLOB NOT NULL,
          embedding_norm REAL NOT NULL,
          indexed_at TEXT NOT NULL
        )
    """)
    # Only the fixed 768-dimension space remains comparable with new vectors.
    conn.execute("""
        INSERT INTO media_vectors
        SELECT uri, 'image', model, mtime, size_bytes, embedding, embedding_norm, indexed_at
        FROM image_vectors WHERE dimensions = 768
    """)
    conn.execute('DROP TABLE image_vectors')
    conn.execute('CREATE INDEX idx_media_vectors_model ON media_vectors(model, media_type, mtime)')
    # Saved groups are regenerated by the next preparation.
    conn.execute('DROP TABLE media_similarity_group_items')
    conn.execute('DROP TABLE media_similarity_groups')
    conn.execute("""
        CREATE TABLE photo_groups (
          position INTEGER NOT NULL,
          rank INTEGER NOT NULL,
          uri TEXT NOT NULL,
          PRIMARY KEY (position, rank)
        )
    """)


def _migrate_012_add_video_frames(conn: sqlite3.Connection) -> None:
    """Keep sampled video vectors with their existing media record."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(media_vectors)").fetchall()}
    if 'video_frames' not in columns:
        conn.execute('ALTER TABLE media_vectors ADD COLUMN video_frames BLOB')


def _migrate_013_douyin_media_groups(conn: sqlite3.Connection) -> None:
    """Live-photo pairs and album membership for Douyin-style exports."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(media_items)").fetchall()}
    for name, kind in (
        ("live_video_uri", "TEXT"),
        ("album_key", "TEXT"),
        ("album_index", "INTEGER"),
        ("album_size", "INTEGER"),
    ):
        if name not in columns:
            conn.execute(f'ALTER TABLE media_items ADD COLUMN {name} {kind}')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_media_items_album ON media_items(album_key, album_index)')


def _migrate_014_index_media_items_mtime(conn: sqlite3.Connection) -> None:
    """Covering index for the timeline page order (mtime DESC, uri).

    Without it a deep offset built a temporary B-tree over the whole table: 20k rows
    meant ~20 MB of scratch space and 220 ms for one 40-row page.
    """
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_media_items_mtime_uri ON media_items(mtime DESC, uri)'
    )


def _migrate_016_index_page_order(conn: sqlite3.Connection) -> None:
    """Covering indexes for the paged orders that had to sort a whole partition.

    idx_media_items_type_mtime covers (media_type, mtime DESC) but not the uri
    tie-breaker, so every video/image page built a temporary B-tree over the whole
    media type; size_bytes had no index at all, so "big files" sorted the library.
    """
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_media_items_type_mtime_uri '
        'ON media_items(media_type, mtime DESC, uri)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_media_items_size '
        'ON media_items(size_bytes DESC, mtime DESC, uri)'
    )


def _migrate_015_add_search_text(conn: sqlite3.Connection) -> None:
    """Store a case/Unicode folded copy of the name for search.

    SQLite's LIKE only folds ASCII case, so "ärger" never matched "Ärger.jpg" and
    a decomposed (NFD) name never matched its NFC query. Folding in Python and
    comparing the folded column keeps the filter inside SQL, which pagination
    needs; existing rows are backfilled here.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(media_items)").fetchall()}
    if 'search_text' not in columns:
        conn.execute('ALTER TABLE media_items ADD COLUMN search_text TEXT')
    rows = conn.execute(
        'SELECT uri, filename, rel_path FROM media_items WHERE search_text IS NULL'
    ).fetchall()
    updates = [
        (normalize_search_text(f"{row['filename'] or ''} {row['rel_path'] or ''}"), row['uri'])
        for row in rows
    ]
    if updates:
        conn.executemany('UPDATE media_items SET search_text = ? WHERE uri = ?', updates)


def _migrate_017_index_capture_and_source(conn: sqlite3.Connection) -> None:
    """Indexes for the paged orders and filters that still sorted whole partitions.

    The default grid filters ``media_type IN ('video','image','live')``, and SQLite
    cannot use ``(media_type, mtime DESC, uri)`` for ORDER BY when the leading column
    is an IN list, so every page still built a temporary B-tree over ~90k rows. A
    month page has a single capture order, which hit the same problem for
    ``(capture_month, captured_at DESC, uri)``; the blog filter (``source_id = ?``)
    had no index at all, so each per-source cover scan read its whole partition.

    Two trailing columns carry their weight: ``media_type`` on the source and size
    indexes turns their COUNT and filter into a covering scan (100k rows: the blog
    chips 112 -> 23ms, "big files" 455 -> 15ms), and the month-day expression index
    serves "on this day" (46 -> 7ms) instead of scanning the whole table.

    ANALYZE belongs in this same step: without statistics SQLite keeps choosing the
    media_type index, and ANALYZE alone would flip the month view to a full scan.
    """
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_media_items_capture_order '
        'ON media_items(capture_month, captured_at DESC, uri)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_media_items_source_mtime_uri '
        'ON media_items(source_id, mtime DESC, uri, media_type)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_media_items_size_order '
        'ON media_items(size_bytes DESC, mtime DESC, uri, media_type)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_media_items_monthday '
        'ON media_items(substr(captured_local_date, 6, 5))'
    )
    try:
        conn.execute('ANALYZE')
    except sqlite3.DatabaseError:
        # Statistics are an optimisation, never a correctness requirement: a full
        # disk or a busy write lock must not roll this migration back and leave the
        # instance unable to start (every retry would fail the same way). The index
        # sync runs ANALYZE again after its next snapshot.
        LOGGER.warning('migration 017: ANALYZE failed, keeping the previous statistics', exc_info=True)


MIGRATIONS = [
    Migration(1, "create_image_vectors", _migrate_001_create_image_vectors),
    Migration(2, "create_media_similarity_groups", _migrate_002_create_media_similarity_groups),
    Migration(3, "create_media_activity", _migrate_003_create_media_activity),
    Migration(4, "create_preference_dimensions", _migrate_004_create_preference_dimensions),
    Migration(5, "create_media_index", _migrate_005_create_media_index),
    Migration(6, "add_media_capture_time", _migrate_006_add_media_capture_time),
    Migration(7, "add_capture_calendar_buckets", _migrate_007_add_capture_calendar_buckets),
    Migration(8, "add_time_metadata_version", _migrate_008_add_time_metadata_version),
    Migration(9, "create_playback_positions", _migrate_009_create_playback_positions),
    Migration(10, "add_media_dimensions", _migrate_010_add_media_dimensions),
    Migration(11, "simplify_media_vectors", _migrate_011_simplify_media_vectors),
    Migration(12, "add_video_frames", _migrate_012_add_video_frames),
    Migration(13, "douyin_media_groups", _migrate_013_douyin_media_groups),
    Migration(14, "index_media_items_mtime", _migrate_014_index_media_items_mtime),
    Migration(15, "add_search_text", _migrate_015_add_search_text),
    Migration(16, "index_page_order", _migrate_016_index_page_order),
    Migration(17, "index_capture_and_source", _migrate_017_index_capture_and_source),
]

# Version numbers are the only identity a migration has: two branches that each add
# Migration(18, …) would silently skip the second one (its version is already in
# schema_migrations), and a fresh install would then be missing a schema change with
# no error anywhere. Fail at import time instead.
if [migration.version for migration in MIGRATIONS] != list(range(1, len(MIGRATIONS) + 1)):
    raise RuntimeError(
        'MIGRATIONS must be numbered 1..N without gaps or duplicates: '
        f'{ [migration.version for migration in MIGRATIONS] }'
    )


class AppDatabase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def connect(self):
        """Yield a connection that is closed when the block ends.

        sqlite3.Connection's own context manager only commits or rolls back; it never
        closes. Every call therefore leaked a handle until garbage collection, which
        kept the WAL files locked and made it impossible to move a damaged database
        file aside while the app was starting.
        """
        conn = self._open_connection()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        # SQLite silently falls back to the rollback journal on filesystems without
        # shared memory (SMB, some FUSE mounts) and only reports it in the return
        # value. Losing WAL also loses the "-wal goes with the database" isolation the
        # damaged-database path relies on, so say so instead of assuming.
        journal = conn.execute("PRAGMA journal_mode = WAL").fetchone()
        mode = str(journal[0]).lower() if journal and journal[0] is not None else ''
        if mode != 'wal':
            LOGGER.warning(
                'SQLite could not enable WAL on %s (mode=%s): concurrent reads and '
                'writes will be slower and the database should not live on a network '
                'share.', self.db_path, mode or 'unknown',
            )
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def migrate(self) -> None:
        # The index writes use UPSERT (SQLite 3.24) and ROW_NUMBER() (3.25), but the
        # migrations themselves only create tables: without this check an old
        # libsqlite3 starts fine, then every index sync fails with a syntax error
        # that the app swallows into "serving the previous index", so the library
        # looks empty and nothing says why.
        if sqlite3.sqlite_version_info < MINIMUM_SQLITE:
            required = '.'.join(str(part) for part in MINIMUM_SQLITE)
            raise DatabaseVersionError(
                f'This build needs SQLite {required} or newer (found '
                f'{sqlite3.sqlite_version}). Use a newer Python or a build that '
                'links a newer libsqlite3.'
            )
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version INTEGER PRIMARY KEY,
                  name TEXT NOT NULL,
                  applied_at TEXT NOT NULL
                )
                """
            )
            # A database written by a newer release must not be migrated by an older
            # one: the old code knows nothing about columns added later, and silently
            # running on that schema writes rows that violate the new constraints (or
            # crashes with "no such table" after a rename). Refuse loudly instead.
            latest_known = MIGRATIONS[-1].version if MIGRATIONS else 0
            row = conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()
            applied = int(row[0]) if row and row[0] is not None else 0
            if applied > latest_known:
                raise DatabaseVersionError(
                    f'This database was created by a newer TikLocal (schema version '
                    f'{applied}, this build knows {latest_known}). Upgrade TikLocal or '
                    'start with a different instance directory.'
                )
            stored_names = {
                int(row[0]): str(row[1])
                for row in conn.execute('SELECT version, name FROM schema_migrations').fetchall()
            }
            for migration in MIGRATIONS:
                stored_name = stored_names.get(migration.version)
                if stored_name is not None and stored_name != migration.name:
                    raise DatabaseVersionError(
                        f'Schema version {migration.version} is recorded as '
                        f'{stored_name!r} but this build calls it {migration.name!r}: '
                        'the migration history was edited after it shipped. Restore the '
                        'previous release or start with a fresh instance directory.'
                    )

        for migration in MIGRATIONS:
            if self._is_applied(migration.version):
                continue
            self._apply(migration)

    def _is_applied(self, version: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            return row is not None

    def _apply(self, migration: Migration) -> None:
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM schema_migrations WHERE version = ?', (migration.version,)).fetchone():
                return
            migration.up(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, datetime.datetime.now(datetime.timezone.utc).isoformat()),
            )


class MediaActivityStore:
    """Store sparse consumption events and a compact recommendation profile."""

    EVENT_DELTAS = {
        "complete": 0.22,
        "favorite": 0.30,
        "unfavorite": -0.20,
        "replay": 0.18,
        "open_detail": 0.10,
        "consumed": 0.0,
        "skip": -0.10,
        "error": 0.0,
    }
    DIMENSION_DELTAS = {
        "consumed": 0.015,
        "complete": 0.06,
        "favorite": 0.12,
        "unfavorite": -0.06,
        "replay": 0.08,
        "open_detail": 0.05,
        "skip": -0.035,
    }
    MAX_EVENTS = 20_000
    # A library uri is a relative path, and the caller's metadata blob is opaque: both
    # used to be stored verbatim, so one crafted /api/activity request could write a
    # multi-megabyte row into the events table.
    MAX_URI_CHARS = 512
    MAX_METADATA_CHARS = 2_048

    @classmethod
    def _bounded_uri(cls, uri: str) -> str:
        """Trim a path that is longer than any real library path, keeping it unique.

        A plain slice made two long paths share a key, so their affinity weights
        merged while ``profiles_for()`` (which gets the real uri) found neither.
        """
        if len(uri) <= cls.MAX_URI_CHARS:
            return uri
        digest = hashlib.sha1(uri.encode('utf-8')).hexdigest()[:8]
        return f"{uri[:cls.MAX_URI_CHARS - len(digest) - 1]}:{digest}"

    def __init__(self, database: AppDatabase):
        self.database = database

    def record_many(self, events: list[dict]) -> int:
        # A full index sync plus several pages reporting playback at once could make
        # SQLite answer "database is locked"; the batch then surfaced as a 500 and its
        # complete/skip signals (which move recommendation weights) were lost.
        for attempt in range(3):
            try:
                return self._record_many_once(events)
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower() or attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
        return 0

    def _record_many_once(self, events: list[dict]) -> int:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        accepted = 0
        with self.database.connect() as conn:
            for raw in events[:50]:
                uri = self._bounded_uri(str(raw.get("uri") or raw.get("name") or "").strip())
                event_type = str(raw.get("event") or raw.get("event_type") or "").strip()
                media_type = str(raw.get("media_type") or "").strip()
                surface = str(raw.get("surface") or "flow").strip()[:32]
                session_id = str(raw.get("session_id") or "").strip()[:80]
                if not uri or event_type not in {"impression", *self.EVENT_DELTAS}:
                    continue
                if media_type not in {"video", "image", "audio"}:
                    continue
                ratio = self._bounded_float(raw.get("ratio"), 0.0, 1.0)
                visible_ms = self._bounded_int(raw.get("visible_ms"), 0, 86_400_000)
                metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
                metadata_json = json.dumps(metadata, ensure_ascii=False)
                if len(metadata_json) > self.MAX_METADATA_CHARS:
                    metadata_json = json.dumps({"truncated": True}, ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO media_events(
                      session_id, uri, media_type, surface, event_type,
                      consumed_ratio, visible_ms, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, uri, media_type, surface, event_type, ratio,
                     visible_ms, metadata_json, now),
                )
                self._update_affinity(conn, uri, media_type, event_type, ratio, visible_ms, now)
                self._update_dimensions(conn, uri, media_type, event_type, now)
                accepted += 1
            if accepted:
                conn.execute(
                    """
                    DELETE FROM media_events
                    WHERE id NOT IN (
                      SELECT id FROM media_events ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (self.MAX_EVENTS,),
                )
        return accepted

    def profiles_for(self, uris: list[str]) -> dict[str, dict]:
        unique = list(dict.fromkeys(str(uri) for uri in uris if uri))
        if not unique:
            return {}
        results: dict[str, dict] = {}
        with self.database.connect() as conn:
            for start in range(0, len(unique), 500):
                chunk = unique[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT * FROM media_affinity WHERE uri IN ({placeholders})",
                    chunk,
                ).fetchall()
                results.update({str(row["uri"]): dict(row) for row in rows})
        return results

    def dimension_scores(self) -> dict[tuple[str, str], float]:
        with self.database.connect() as conn:
            rows = conn.execute("SELECT * FROM preference_dimensions").fetchall()
        return {
            (str(row["dimension_type"]), str(row["dimension_value"])):
                float(row["positive_score"]) - float(row["negative_score"])
            for row in rows
        }

    # Only long videos resume; short clips, the first seconds and the ending start over.
    RESUME_MIN_DURATION = 60.0
    RESUME_MIN_POSITION = 10.0
    RESUME_END_MARGIN = 15.0

    def save_position(self, uri: str, position: float, duration: float) -> bool:
        """Remember where playback stopped; returns False when the position was cleared instead.

        "Not worth remembering" and "finished" are different answers. A two second
        glance in another tab (the library quick viewer reports the position when it
        switches clips) used to fall into the clearing branch and delete the 40 minute
        position another device had saved — silently, for the whole account.
        """
        if duration < self.RESUME_MIN_DURATION:
            # A clip too short to resume never gets a row.
            with self.database.connect() as conn:
                conn.execute("DELETE FROM playback_positions WHERE uri = ?", (uri,))
            return False
        if position < self.RESUME_MIN_POSITION:
            # Opened and swiped away: leave whatever is stored alone.
            return True
        if position > duration - self.RESUME_END_MARGIN:
            # Watched to the end: start over next time.
            with self.database.connect() as conn:
                conn.execute("DELETE FROM playback_positions WHERE uri = ?", (uri,))
            return False
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO playback_positions(uri, position, duration, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(uri) DO UPDATE SET
                  position = excluded.position, duration = excluded.duration, updated_at = excluded.updated_at
                """,
                (uri, position, duration, datetime.datetime.now(datetime.timezone.utc).isoformat()),
            )
        return True

    def positions_for(self, uris: list[str]) -> dict[str, dict]:
        unique = list(dict.fromkeys(str(uri) for uri in uris if uri))
        results: dict[str, dict] = {}
        with self.database.connect() as conn:
            for start in range(0, len(unique), 500):
                chunk = unique[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT uri, position, duration FROM playback_positions WHERE uri IN ({placeholders})",
                    chunk,
                ).fetchall()
                results.update({str(row["uri"]): {"position": row["position"], "duration": row["duration"]} for row in rows})
        return results

    def clear(self) -> None:
        with self.database.connect() as conn:
            conn.execute("DELETE FROM playback_positions")
            conn.execute("DELETE FROM media_events")
            conn.execute("DELETE FROM media_affinity")
            conn.execute("DELETE FROM preference_dimensions")

    def _update_affinity(
        self,
        conn: sqlite3.Connection,
        uri: str,
        media_type: str,
        event_type: str,
        ratio: float | None,
        visible_ms: int,
        now: str,
    ) -> None:
        delta = self.EVENT_DELTAS.get(event_type, 0.0)
        if event_type == "complete" and ratio is not None:
            delta += ratio * 0.08
        if event_type == "skip" and ratio is not None and ratio < 0.12:
            delta -= 0.04
        conn.execute(
            """
            INSERT INTO media_affinity(
              uri, media_type, impressions, completes, skips, favorites, replays,
              total_visible_ms, affinity_score, last_shown_at, last_consumed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uri) DO UPDATE SET
              media_type = excluded.media_type,
              impressions = impressions + excluded.impressions,
              completes = completes + excluded.completes,
              skips = skips + excluded.skips,
              favorites = CASE
                WHEN ? = 'favorite' THEN favorites + 1
                WHEN ? = 'unfavorite' THEN MAX(0, favorites - 1)
                ELSE favorites
              END,
              replays = replays + excluded.replays,
              total_visible_ms = total_visible_ms + excluded.total_visible_ms,
              affinity_score = MAX(-1.0, MIN(1.8, affinity_score + excluded.affinity_score)),
              last_shown_at = COALESCE(excluded.last_shown_at, last_shown_at),
              last_consumed_at = COALESCE(excluded.last_consumed_at, last_consumed_at),
              updated_at = excluded.updated_at
            """,
            (
                uri, media_type,
                1 if event_type == "impression" else 0,
                1 if event_type == "complete" else 0,
                1 if event_type == "skip" else 0,
                1 if event_type == "favorite" else 0,
                1 if event_type == "replay" else 0,
                visible_ms,
                delta,
                now if event_type == "impression" else None,
                now if event_type != "impression" else None,
                now,
                event_type,
                event_type,
            ),
        )

    def _update_dimensions(
        self,
        conn: sqlite3.Connection,
        uri: str,
        media_type: str,
        event_type: str,
        now: str,
    ) -> None:
        delta = self.DIMENSION_DELTAS.get(event_type, 0.0)
        if not delta:
            return
        for dimension_type, dimension_value in self.dimensions_for(uri, media_type):
            conn.execute(
                """
                INSERT INTO preference_dimensions(
                  dimension_type, dimension_value, positive_score,
                  negative_score, sample_count, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(dimension_type, dimension_value) DO UPDATE SET
                  positive_score = MIN(3.0, positive_score + excluded.positive_score),
                  negative_score = MIN(3.0, negative_score + excluded.negative_score),
                  sample_count = sample_count + 1,
                  updated_at = excluded.updated_at
                """,
                (
                    dimension_type,
                    dimension_value,
                    max(delta, 0.0),
                    abs(min(delta, 0.0)),
                    now,
                ),
            )

    @staticmethod
    def dimensions_for(uri: str, media_type: str) -> list[tuple[str, str]]:
        clean = str(uri or "").strip().replace("\\", "/")
        source = "default"
        rel_path = clean
        if clean.startswith("@"):
            source, separator, rel_path = clean[1:].partition("/")
            if not separator:
                rel_path = ""
        parent = str(PurePosixPath(rel_path).parent)
        dimensions = [("media_type", media_type), ("source", source or "default")]
        if parent and parent != ".":
            dimensions.append(("directory", f"{source or 'default'}/{parent}"))
        return dimensions

    @staticmethod
    def _bounded_float(value, minimum: float, maximum: float) -> float | None:
        try:
            return max(minimum, min(float(value), maximum))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bounded_int(value, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(int(value or 0), maximum))
        except (TypeError, ValueError):
            return 0
