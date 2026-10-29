import datetime
import errno
import logging
import os
import re
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from tiklocal.services.library import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from tiklocal.services import media_naming
from tiklocal.services.database import AppDatabase
from tiklocal.services.media_info import probe_media_dims, probe_tool_available
from tiklocal.services.search_text import normalize as normalize_search_text

LOGGER = logging.getLogger(__name__)


class MediaIndexStore:
    """SQLite query index for the filesystem-backed media library."""

    def __init__(self, database: AppDatabase):
        self.database = database

    def replace_snapshot(
        self,
        records: list[dict],
        *,
        synced_source_ids: set[str] | None = None,
        preserved_prefixes: list[str] | None = None,
        configured_source_ids: set[str] | None = None,
    ) -> dict:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        source_ids = sorted(
            {str(record["source_id"]) for record in records}
            if synced_source_ids is None
            else synced_source_ids
        )
        with self.database.connect() as conn:
            conn.execute("CREATE TEMP TABLE media_index_seen(uri TEXT PRIMARY KEY)")
            for record in records:
                uri = str(record.get('uri') or '')
                try:
                    uri.encode('utf-8')
                except UnicodeEncodeError:
                    # sqlite3 cannot bind a lone surrogate: one weird filename used
                    # to abort the whole scan (and with it application startup).
                    LOGGER.warning('Skipping a file whose name is not valid UTF-8: %r', uri)
                    continue
                if Path(uri).name != Path(uri).name.rstrip():
                    # Only trailing whitespace: Windows silently drops it on create,
                    # so the name on disk and the name a downloader recorded disagree.
                    # Leading spaces are legal and common in exported files; using
                    # strip() here dropped those files from the index for good.
                    LOGGER.warning('Skipping a file with trailing whitespace in its name: %r', uri)
                    continue
                values = self._record_values(record, now)
                conn.execute(
                    """
                    INSERT INTO media_items(
                      uri, source_id, rel_path, filename, parent_path, media_type,
                      extension, size_bytes, mtime, captured_at, captured_local_date,
                      capture_year, capture_month, time_source, time_confidence,
                      time_metadata_version, discovered_at, indexed_at,
                      live_video_uri, album_key, album_index, album_size, search_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(uri) DO UPDATE SET
                      source_id = excluded.source_id,
                      rel_path = excluded.rel_path,
                      filename = excluded.filename,
                      parent_path = excluded.parent_path,
                      media_type = excluded.media_type,
                      extension = excluded.extension,
                      size_bytes = excluded.size_bytes,
                      mtime = excluded.mtime,
                      captured_at = excluded.captured_at,
                      captured_local_date = excluded.captured_local_date,
                      capture_year = excluded.capture_year,
                      capture_month = excluded.capture_month,
                      time_source = excluded.time_source,
                      time_confidence = excluded.time_confidence,
                      time_metadata_version = excluded.time_metadata_version,
                      indexed_at = excluded.indexed_at,
                      live_video_uri = excluded.live_video_uri,
                      album_key = excluded.album_key,
                      album_index = excluded.album_index,
                      album_size = excluded.album_size,
                      search_text = excluded.search_text
                    """,
                    values,
                )
                conn.execute("INSERT OR IGNORE INTO media_index_seen(uri) VALUES (?)", (record["uri"],))
            deleted = 0
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                # A subfolder we could not read this round must not be treated as
                # deleted: keep every row under the skipped URI prefixes.
                keep_prefix_sql = ""
                keep_params: list[str] = []
                for prefix in preserved_prefixes or ():
                    # The scanner reports folder prefixes with a trailing slash
                    # ("@default/sub/"); normalise before building the pattern.
                    key = prefix.rstrip('/') or prefix
                    escaped = key.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                    # Match the folder itself or its children only: a plain
                    # `LIKE prefix%` also protected siblings such as `subdir` when
                    # the skipped folder was `sub`, leaving ghost rows forever.
                    keep_prefix_sql += " AND uri <> ? AND uri NOT LIKE ? ESCAPE '\\'"
                    keep_params.extend([key, f'{escaped}/%'])
                deleted = conn.execute(
                    f"""
                    DELETE FROM media_items
                    WHERE source_id IN ({placeholders})
                      AND uri NOT IN (SELECT uri FROM media_index_seen)
                      {keep_prefix_sql}
                    """,
                    [*source_ids, *keep_params],
                ).rowcount
            if configured_source_ids is not None:
                # A source that was removed from the config never reaches the scan
                # loop, so the seen-based delete above can never clear its rows: the
                # grid kept cards whose files answer 404. Rows of a source that is
                # still configured but failed to scan are protected by source_ids.
                configured = sorted(str(source_id) for source_id in configured_source_ids)
                if configured:
                    placeholders = ",".join("?" for _ in configured)
                    deleted += conn.execute(
                        f"DELETE FROM media_items WHERE source_id NOT IN ({placeholders})",
                        configured,
                    ).rowcount
                else:
                    deleted += conn.execute("DELETE FROM media_items").rowcount
            if deleted:
                self._sweep_orphan_state(conn, configured_source_ids)
            conn.execute("DROP TABLE media_index_seen")
            item_count = int(conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0])
            conn.execute(
                """
                INSERT INTO media_index_state(id, last_synced_at, item_count)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  last_synced_at = excluded.last_synced_at,
                  item_count = excluded.item_count
                """,
                (now, item_count),
            )
        if records or deleted:
            # Statistics decide whether the paged orders use an index at all:
            # without them SQLite answered the three-value media_type IN with
            # (media_type, mtime DESC, uri) plus a temporary B-tree over the whole
            # grid (~90k rows sorted to return 24). Kept out of the snapshot
            # transaction so the write lock is released before this 50ms pass.
            try:
                with self.database.connect() as conn:
                    conn.execute("ANALYZE")
            except sqlite3.OperationalError as error:
                LOGGER.warning('Could not refresh the query statistics: %s', error)
        return {
            "indexed": len(records),
            "deleted": max(deleted, 0),
            "last_synced_at": now,
        }

    @staticmethod
    def _sweep_orphan_state(conn, configured_source_ids: set[str] | None) -> None:
        """Drop per-file state whose media row is gone.

        ``playback_positions``, ``media_events`` and ``media_affinity`` are keyed by
        URI, so they outlive the file: a resume point written for a deleted clip
        made the next file that reused the name open at the wrong time, and rows
        accumulated for every file deleted outside the app. Only sources that are
        still configured are swept -- a source that is unplugged or removed from the
        config keeps its history for when it comes back.
        """
        for source_id in sorted(str(value) for value in (configured_source_ids or ())):
            escaped = source_id.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            pattern = f'@{escaped}/%'
            for table in ('playback_positions', 'media_events', 'media_affinity', 'media_vectors'):
                conn.execute(
                    f"DELETE FROM {table} WHERE uri LIKE ? ESCAPE '\\'"
                    " AND uri NOT IN (SELECT uri FROM media_items)",
                    (pattern,),
                )

    def upsert(self, records: list[dict]) -> int:
        if not records:
            return 0
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.database.connect() as conn:
            for record in records:
                conn.execute(
                    """
                    INSERT INTO media_items(
                      uri, source_id, rel_path, filename, parent_path, media_type,
                      extension, size_bytes, mtime, captured_at, captured_local_date,
                      capture_year, capture_month, time_source, time_confidence,
                      time_metadata_version, discovered_at, indexed_at,
                      live_video_uri, album_key, album_index, album_size, search_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(uri) DO UPDATE SET
                      size_bytes = excluded.size_bytes,
                      mtime = excluded.mtime,
                      captured_at = excluded.captured_at,
                      captured_local_date = excluded.captured_local_date,
                      capture_year = excluded.capture_year,
                      capture_month = excluded.capture_month,
                      time_source = excluded.time_source,
                      time_confidence = excluded.time_confidence,
                      time_metadata_version = excluded.time_metadata_version,
                      indexed_at = excluded.indexed_at,
                      media_type = excluded.media_type,
                      live_video_uri = excluded.live_video_uri,
                      album_key = excluded.album_key,
                      album_index = excluded.album_index,
                      album_size = excluded.album_size,
                      search_text = excluded.search_text
                    """,
                    self._record_values(record, now),
                )
            count = conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
            conn.execute(
                """
                INSERT INTO media_index_state(id, last_synced_at, item_count)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET item_count = excluded.item_count
                """,
                (now, count),
            )
        return len(records)

    def delete(self, uri: str) -> bool:
        with self.database.connect() as conn:
            deleted = conn.execute("DELETE FROM media_items WHERE uri = ?", (uri,)).rowcount
            if deleted:
                conn.execute(
                    "UPDATE media_index_state SET item_count = MAX(0, item_count - 1) WHERE id = 1"
                )
                # Same transaction as the media row: a second connection used to be a
                # second transaction, so a lock conflict between them left the resume
                # point, the affinity weight, the events and the vector behind — and the
                # next file to reuse this name inherited all of it.
                for table in ('playback_positions', 'media_events', 'media_affinity', 'media_vectors'):
                    conn.execute(f"DELETE FROM {table} WHERE uri = ?", (uri,))
        return bool(deleted)

    def forget_uri(self, uri: str) -> int:
        """Drop every per-file row keyed by this URI and return how many went.

        Deleting through the app only removed the media row, so the resume point, the
        affinity weight, the event log and the similarity vector stayed behind and the
        next file to land on the same name inherited all of it. The full-scan sweep
        covers rows whose source was rescanned; the delete path has to forget the URI
        itself, including the vector blob, which is never cheap to keep.
        """
        removed = 0
        with self.database.connect() as conn:
            for table in ('playback_positions', 'media_events', 'media_affinity', 'media_vectors'):
                removed += conn.execute(f"DELETE FROM {table} WHERE uri = ?", (uri,)).rowcount
        return removed

    def unprobed(self, limit: int = 50, exclude: tuple[str, ...] = ()) -> list[dict]:
        """Media whose dimensions were never read, or were read before the file changed.

        ``exclude`` lets the probe pass step over rows that just failed: without it
        the head of ``ORDER BY mtime DESC`` was re-probed every five seconds and
        every row behind it stayed unmeasured forever.
        """
        skip_sql = ""
        params: list = []
        if exclude:
            placeholders = ",".join("?" for _ in exclude)
            skip_sql = f" AND uri NOT IN ({placeholders})"
            params.extend(exclude)
        params.append(int(limit))
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT uri, media_type, mtime FROM media_items
                WHERE media_type IN ('video', 'image', 'live')
                  AND (probed_mtime IS NULL OR probed_mtime != mtime)
                  {skip_sql}
                ORDER BY mtime DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def set_dimensions(self, rows: list[tuple]) -> None:
        """Store (width, height, probed_mtime, uri) rows; a failed probe stores no size."""
        with self.database.connect() as conn:
            conn.executemany(
                "UPDATE media_items SET width = ?, height = ?, probed_mtime = ? WHERE uri = ?",
                rows,
            )

    def records(
        self, *, search: str = "", media_type: str = "",
        source_id: str = "", album_covers_only: bool = False,
    ) -> list[dict]:
        where, params = self._filters(
            search=search, media_type=media_type, source_id=source_id,
            album_covers_only=album_covers_only,
        )
        with self.database.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM media_items {where} ORDER BY mtime DESC, uri",
                params,
            ).fetchall()
        return [self._to_library_record(row) for row in rows]

    def records_for_names(self, names) -> list[dict]:
        """Fetch only the given URIs.

        The feed renders ~24 cards but used to load every record just to look up
        those few names, which cost 16000 rows and ~400ms on an 8000-file library.
        """

        wanted = sorted({str(name) for name in names if name})
        if not wanted:
            return []
        placeholders = ','.join('?' for _ in wanted)
        with self.database.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM media_items WHERE uri IN ({placeholders})",
                wanted,
            ).fetchall()
        return [self._to_library_record(row) for row in rows]

    def neighbors(self, media_type: str, name: str) -> tuple[str | None, str | None]:
        """The URIs before and after ``name`` in library order (newest first).

        The detail and image pages only need the two neighbours, but looking them
        up through :meth:`records` materialised the whole media type first: 3000
        rows for a video library and 19000 for an image one, on the two most
        visited pages. Each side is a pair of key-set lookups on
        ``idx_media_items_type_mtime``: an index range scan for the nearest row
        with a different mtime, then a small sort among the rows sharing the
        target's mtime to apply the URI tie-break. The order is ``mtime DESC,
        uri``, so a row is "before" the target when its mtime is newer, or equal
        with a smaller URI. Live photos pair with images in image-shaped views, so
        the image walk covers both types and merges the two index walks.
        """

        types = ('image', 'live') if media_type == 'image' else (media_type,)
        with self.database.connect() as conn:
            target = None
            for kind in types:
                target = conn.execute(
                    "SELECT mtime FROM media_items WHERE uri = ? AND media_type = ?",
                    (name, kind),
                ).fetchone()
                if target is not None:
                    break
            if target is None:
                return None, None
            mtime = target["mtime"]
            previous = self._nearest(conn, types, mtime, name, before=True)
            following = self._nearest(conn, types, mtime, name, before=False)
        return previous, following

    @staticmethod
    def _nearest(conn, types, mtime, name: str, *, before: bool) -> str | None:
        """The closest neighbour of ``name`` among ``types``, or None.

        One index walk per type: a filter of ``media_type IN ('image', 'live')``
        would make SQLite sort the whole partition to answer the ORDER BY, which
        is exactly the cost this method exists to avoid.
        """

        best = None
        for kind in types:
            if before:
                # The rows sharing the target's mtime come first in display order, so
                # the tie-break is checked before the older mtime band.
                row = conn.execute(
                    "SELECT mtime, uri FROM media_items WHERE media_type = ? "
                    "AND mtime = ? AND uri < ? ORDER BY uri DESC LIMIT 1",
                    (kind, mtime, name),
                ).fetchone()
                if row is None:
                    row = conn.execute(
                        "SELECT mtime, uri FROM media_items WHERE media_type = ? AND mtime > ? "
                        "ORDER BY mtime ASC LIMIT 1",
                        (kind, mtime),
                    ).fetchone()
            else:
                row = conn.execute(
                    "SELECT mtime, uri FROM media_items WHERE media_type = ? "
                    "AND mtime = ? AND uri > ? ORDER BY uri ASC LIMIT 1",
                    (kind, mtime, name),
                ).fetchone()
                if row is None:
                    row = conn.execute(
                        "SELECT mtime, uri FROM media_items WHERE media_type = ? AND mtime < ? "
                        "ORDER BY mtime DESC LIMIT 1",
                        (kind, mtime),
                    ).fetchone()
            if row is None:
                continue
            if best is None:
                best = row
            elif before:
                # Newest first: "before" is the oldest of the newer rows, and the
                # URI breaks an equal mtime the same way the ORDER BY does.
                if row["mtime"] < best["mtime"] or (
                    row["mtime"] == best["mtime"] and str(row["uri"]) > str(best["uri"])
                ):
                    best = row
            elif row["mtime"] > best["mtime"] or (
                row["mtime"] == best["mtime"] and str(row["uri"]) < str(best["uri"])
            ):
                best = row
        return str(best["uri"]) if best is not None else None

    def count_uris(self, uris) -> int:
        """How many of the given URIs are still indexed.

        Collection cards store the length of their saved list, which never shrinks
        when a file is deleted outside the collection, so a card could promise three
        items and open on two. Counting against the index keeps the card honest
        without loading the rows.
        """

        wanted = sorted({str(uri) for uri in uris if uri})
        if not wanted:
            return 0
        total = 0
        chunk = 500
        with self.database.connect() as conn:
            for start in range(0, len(wanted), chunk):
                part = wanted[start:start + chunk]
                placeholders = ','.join('?' for _ in part)
                row = conn.execute(
                    f"SELECT COUNT(*) FROM media_items WHERE uri IN ({placeholders})",
                    part,
                ).fetchone()
                total += int(row[0] if row else 0)
        return total

    def on_days(
        self,
        month_days: list[str],
        before_year: str,
        limit: int = 200,
        per_year: int | None = None,
    ) -> list[dict]:
        """Photos and videos captured on the given MM-DD days in years before `before_year`, newest first.

        With ``per_year`` the quota is applied inside each year instead of to the
        whole result: a global LIMIT used to let one busy day eat the budget, so
        every older year silently vanished from "On This Day".
        """
        days = [day for day in month_days if re.fullmatch(r"\d{2}-\d{2}", day)]
        if not days:
            return []
        placeholders = ",".join("?" for _ in days)
        with self.database.connect() as conn:
            if per_year:
                rows = conn.execute(
                    f"""
                    SELECT * FROM (
                      SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY substr(captured_local_date, 1, 4)
                        ORDER BY captured_at DESC, uri
                      ) AS _rn
                      FROM media_items
                      WHERE media_type IN ('video', 'image', 'live')
                        AND captured_at > 0
                        AND capture_year < ?
                        AND substr(captured_local_date, 6, 5) IN ({placeholders})
                    ) WHERE _rn <= ?
                    ORDER BY captured_at DESC, uri
                    """,
                    (before_year, *days, int(per_year)),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT * FROM media_items
                    WHERE media_type IN ('video', 'image', 'live')
                      AND captured_at > 0
                      AND capture_year < ?
                      AND substr(captured_local_date, 6, 5) IN ({placeholders})
                    ORDER BY captured_at DESC, uri
                    LIMIT ?
                    """,
                    (before_year, *days, limit),
                ).fetchall()
        return [self._to_library_record(row) for row in rows]

    def existing_uris(self, uris) -> set[str]:
        """Which of the given URIs are still indexed, in one round trip."""
        wanted = sorted({str(uri) for uri in uris if uri})
        found: set[str] = set()
        if not wanted:
            return found
        chunk = 500
        with self.database.connect() as conn:
            for start in range(0, len(wanted), chunk):
                part = wanted[start:start + chunk]
                placeholders = ",".join("?" for _ in part)
                rows = conn.execute(
                    f"SELECT uri FROM media_items WHERE uri IN ({placeholders})",
                    part,
                ).fetchall()
                found.update(str(row[0]) for row in rows)
        return found

    def page(
        self,
        *,
        search: str = "",
        media_type: str = "",
        min_size: int = 0,
        month: str = "",
        offset: int = 0,
        limit: int = 48,
        mode: str = "all",
        seed: str = "",
        uris: list[str] | set[str] | None = None,
        preserve_order: bool = False,
        source_id: str = "",
        album_covers_only: bool = False,
    ) -> dict:
        if mode == 'image_random':
            media_type = 'image'
        elif mode in {'video_latest', 'big_files'}:
            media_type = 'video'
        where, params = self._filters(
            search=search, media_type=media_type, min_size=min_size, month=month,
            source_id=source_id, album_covers_only=album_covers_only,
        )
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 96))
        order_by = "captured_at DESC, uri" if self.is_month_key(month) else "mtime DESC, uri"
        if mode == 'big_files':
            order_by = "size_bytes DESC, mtime DESC, uri"
        with self.database.connect() as conn:
            source = 'media_items'
            if uris is not None:
                conn.execute('CREATE TEMP TABLE selected_media(selected_uri TEXT PRIMARY KEY, position INTEGER)')
                conn.executemany(
                    'INSERT INTO selected_media VALUES (?, ?)',
                    [(uri, index) for index, uri in enumerate(dict.fromkeys(uris))],
                )
                source += ' JOIN selected_media ON uri = selected_uri'
                if preserve_order:
                    # Collections retain their member order and media types.
                    where, params, order_by = '', [], 'position'
            if mode == 'image_random' and not preserve_order:
                # Shuffling every matching row cost ~90 MB and a second of CPU per
                # page on a 100k library, and the page only needs `limit` of them.
                # The permutation depends on the ordered uri list alone, so shuffle
                # the uris (one small column) and read the page's rows afterwards.
                uris_in_order = [row[0] for row in conn.execute(
                    f'SELECT uri FROM {source} {where} ORDER BY {order_by}', params,
                )]
                random.Random(seed).shuffle(uris_in_order)
                total = len(uris_in_order)
                picked = uris_in_order[safe_offset:safe_offset + safe_limit]
                rows = []
                if picked:
                    placeholders = ','.join('?' * len(picked))
                    by_uri = {
                        row['uri']: row
                        for row in conn.execute(
                            f'SELECT media_items.* FROM media_items WHERE uri IN ({placeholders})',
                            picked,
                        )
                    }
                    rows = [by_uri[uri] for uri in picked if uri in by_uri]
            else:
                total = int(conn.execute(f'SELECT COUNT(*) FROM {source} {where}', params).fetchone()[0])
                rows = conn.execute(
                    f'SELECT media_items.* FROM {source} {where} ORDER BY {order_by} LIMIT ? OFFSET ?',
                    [*params, safe_limit, safe_offset],
                ).fetchall()
        return {
            "records": [self._to_library_record(row) for row in rows],
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "next_offset": safe_offset + safe_limit,
            "has_more": safe_offset + safe_limit < total,
        }

    def timeline_months(
        self,
        *,
        before: str = "",
        limit: int = 18,
        preview_limit: int = 18,
    ) -> dict:
        """Return compact month chapters without probing media metadata."""
        safe_limit = max(1, min(int(limit), 36))
        safe_preview_limit = max(1, min(int(preview_limit), 18))
        before_key = before if self.is_month_key(before) else ""
        month_expr = "capture_month"
        before_clause = f"AND {month_expr} < ?" if before_key else ""
        params: list[object] = [before_key] if before_key else []
        params.append(safe_limit + 1)

        with self.database.connect() as conn:
            year_rows = conn.execute(
                f"""
                SELECT
                  capture_year AS year,
                  COUNT(*) AS item_count,
                  COUNT(DISTINCT {month_expr}) AS month_count
                FROM media_items
                WHERE media_type IN ('image', 'video', 'live')
                  AND captured_at > 0
                GROUP BY year
                HAVING year IS NOT NULL AND year <> ''
                ORDER BY year DESC
                """
            ).fetchall()
            month_rows = conn.execute(
                f"""
                SELECT
                  {month_expr} AS month_key,
                  COUNT(*) AS item_count,
                  SUM(CASE WHEN media_type IN ('image', 'live') THEN 1 ELSE 0 END) AS image_count,
                  SUM(CASE WHEN media_type = 'video' THEN 1 ELSE 0 END) AS video_count,
                  MAX(mtime) AS latest_mtime
                FROM media_items
                WHERE media_type IN ('image', 'video', 'live')
                  AND captured_at > 0
                  {before_clause}
                GROUP BY month_key
                HAVING month_key IS NOT NULL AND month_key <> ''
                ORDER BY month_key DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

            has_more = len(month_rows) > safe_limit
            visible_rows = month_rows[:safe_limit]
            months: list[dict] = []
            for month_row in visible_rows:
                month_key = str(month_row["month_key"])
                # A bounded candidate window keeps dense months cheap while still
                # giving the sampler enough temporal spread for stable covers.
                candidates = conn.execute(
                    f"""
                    SELECT *
                    FROM media_items
                    WHERE media_type IN ('image', 'video', 'live')
                      AND {month_expr} = ?
                    ORDER BY captured_at DESC, uri
                    LIMIT 120
                    """,
                    (month_key,),
                ).fetchall()
                months.append({
                    "key": month_key,
                    "count": int(month_row["item_count"] or 0),
                    "image_count": int(month_row["image_count"] or 0),
                    "video_count": int(month_row["video_count"] or 0),
                    "latest_mtime": float(month_row["latest_mtime"] or 0),
                    "records": [
                        self._to_library_record(row)
                        for row in self._sample_timeline_records(candidates, safe_preview_limit)
                    ],
                })

        next_before = str(visible_rows[-1]["month_key"]) if visible_rows and has_more else ""
        return {
            "months": months,
            "years": [
                {
                    "year": str(row["year"]),
                    "count": int(row["item_count"] or 0),
                    "month_count": int(row["month_count"] or 0),
                }
                for row in year_rows
            ],
            "has_more": has_more,
            "next_before": next_before,
        }

    def stats(self) -> dict:
        with self.database.connect() as conn:
            counts = {
                str(row["media_type"]): int(row["count"])
                for row in conn.execute(
                    "SELECT media_type, COUNT(*) AS count FROM media_items GROUP BY media_type"
                ).fetchall()
            }
            state = conn.execute("SELECT * FROM media_index_state WHERE id = 1").fetchone()
        return {
            "videos": counts.get("video", 0),
            "images": counts.get("image", 0) + counts.get("live", 0),
            "lives": counts.get("live", 0),
            "audios": counts.get("audio", 0),
            "total": sum(counts.values()),
            "last_synced_at": str(state["last_synced_at"]) if state else "",
        }

    def album_members(self, album_key: str) -> list[dict]:
        """Group members ordered by position; live posts mix image, live and video rows."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM media_items
                WHERE album_key = ? AND media_type IN ('image', 'live', 'video')
                ORDER BY album_index, uri
                """,
                (str(album_key or ''),),
            ).fetchall()
        return [self._to_library_record(row) for row in rows]

    def blog_rows(self) -> list[dict]:
        """Per media source counts for the blogger switcher; latest media feeds the cover."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT source_id,
                       COUNT(*) AS item_count,
                       MAX(mtime) AS latest_mtime
                FROM media_items
                WHERE media_type IN ('image', 'video', 'live')
                GROUP BY source_id
                ORDER BY latest_mtime DESC
                """
            ).fetchall()
            latest = {}
            for row in rows:
                cover = conn.execute(
                    """
                    SELECT uri FROM media_items
                    WHERE source_id = ? AND media_type IN ('image', 'video', 'live')
                    ORDER BY mtime DESC, uri LIMIT 1
                    """,
                    (row["source_id"],),
                ).fetchone()
                latest[str(row["source_id"])] = str(cover["uri"]) if cover else ""
        return [
            {
                "source_id": str(row["source_id"]),
                "count": int(row["item_count"] or 0),
                "latest_uri": latest.get(str(row["source_id"]), ""),
            }
            for row in rows
        ]

    def time_states(self) -> dict[str, dict]:
        """Return cached capture metadata so unchanged files are not reopened."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT uri, size_bytes, mtime, captured_at, captured_local_date,
                       time_source, time_confidence, time_metadata_version
                FROM media_items
                """
            ).fetchall()
        return {str(row["uri"]): dict(row) for row in rows}

    @staticmethod
    def _record_values(record: dict, now: str) -> tuple:
        return (
            record["uri"], record["source_id"], record["rel_path"], record["filename"],
            record["parent_path"], record["media_type"], record["extension"],
            int(record["size_bytes"]), float(record["mtime"]),
            float(record.get("captured_at") or record["mtime"]),
            str(record.get("captured_local_date") or ""),
            str(record.get("captured_local_date") or "")[:4],
            str(record.get("captured_local_date") or "")[:7],
            str(record.get("time_source") or "filesystem_mtime"),
            str(record.get("time_confidence") or "fallback"),
            int(record.get("time_metadata_version") or TIME_METADATA_VERSION),
            now, now,
            (str(record["live_video_uri"]) if record.get("live_video_uri") else None),
            (str(record["album_key"]) if record.get("album_key") else None),
            (int(record["album_index"]) if record.get("album_index") is not None else None),
            (int(record["album_size"]) if record.get("album_size") is not None else None),
            normalize_search_text(f"{record['filename']} {record['rel_path']}"),
        )

    @staticmethod
    def _to_library_record(row) -> dict:
        return {
            "name": str(row["uri"]),
            "media_type": str(row["media_type"]),
            "mtime_ts": float(row["mtime"]),
            "captured_at": float(row["captured_at"] or row["mtime"]),
            "captured_local_date": str(row["captured_local_date"] or ""),
            "time_source": str(row["time_source"] or "filesystem_mtime"),
            "time_confidence": str(row["time_confidence"] or "fallback"),
            "size_bytes": int(row["size_bytes"]),
            "width": row["width"],
            "height": row["height"],
            "is_favorite": False,
            "source_id": str(row["source_id"] or ""),
            "live_video_uri": (str(row["live_video_uri"]) if row["live_video_uri"] else ""),
            "album_key": (str(row["album_key"]) if row["album_key"] else ""),
            "album_index": (int(row["album_index"]) if row["album_index"] is not None else None),
            "album_size": (int(row["album_size"]) if row["album_size"] is not None else None),
        }

    @staticmethod
    def _like_term(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @classmethod
    def _filters(
        cls,
        *,
        search: str,
        media_type: str,
        min_size: int = 0,
        month: str = "",
        source_id: str = "",
        album_covers_only: bool = False,
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if media_type == 'image':
            # Live photos pair with images in image-shaped views.
            clauses.append("media_type IN ('image', 'live')")
        elif media_type:
            clauses.append("media_type = ?")
            params.append(media_type)
        else:
            clauses.append("media_type IN ('video', 'image', 'live')")
        if album_covers_only:
            clauses.append("(album_key IS NULL OR album_index = 0)")
        if source_id:
            clauses.append("source_id = ?")
            params.append(str(source_id))
        if min_size > 0:
            clauses.append("size_bytes >= ?")
            params.append(int(min_size))
        if cls.is_month_key(month):
            clauses.append("capture_month = ?")
            params.append(month)
        term = normalize_search_text(str(search or ""))
        if term:
            clauses.append("search_text LIKE ? ESCAPE '\\'")
            params.append(cls._like_term(term))
        return f"WHERE {' AND '.join(clauses)}", params

    @staticmethod
    def is_month_key(value: str) -> bool:
        return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(value or "")))

    @staticmethod
    def _sample_timeline_records(rows, limit: int):
        """Pick stable, time-spread covers and avoid a video-heavy chapter."""
        values = list(rows)
        if len(values) <= limit:
            return values

        target_indexes = {
            round(position * (len(values) - 1) / max(1, limit - 1))
            for position in range(limit)
        }
        sampled = [values[index] for index in sorted(target_indexes)]
        video_limit = max(1, limit // 4)
        selected: list = []
        deferred_videos: list = []
        video_count = 0
        for row in sampled:
            if str(row["media_type"]) == "video" and video_count >= video_limit:
                deferred_videos.append(row)
                continue
            selected.append(row)
            if str(row["media_type"]) == "video":
                video_count += 1

        if len(selected) < limit:
            selected_uris = {str(row["uri"]) for row in selected}
            for row in values:
                if str(row["uri"]) in selected_uris or str(row["media_type"]) == "video":
                    continue
                selected.append(row)
                selected_uris.add(str(row["uri"]))
                if len(selected) >= limit:
                    break
        if len(selected) < limit:
            selected.extend(deferred_videos[:limit - len(selected)])
        return selected[:limit]


_FILENAME_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<year>19\d{2}|20\d{2})[-_]?\s?(?P<month>0[1-9]|1[0-2])[-_]?\s?(?P<day>0[1-9]|[12]\d|3[01])(?:[T_ -]?(?P<hour>[01]\d|2[0-3])[:._-]?(?P<minute>[0-5]\d)[:._-]?(?P<second>[0-5]\d))?(?!\d)"),
)


#: Bumped whenever the capture-time derivation changes. ``_capture_is_current``
#: compares it with ``==``: a ``>= 1`` check turned the version into a boolean, so
#: raising it would never have invalidated a single cached row.
#:
#: 2: read DateTimeOriginal from the Exif sub-IFD instead of IFD0, and treat the
#:    file-modified tag (306) as a low-confidence hint.
TIME_METADATA_VERSION = 2


def _capture_payload(value: datetime.datetime, source: str, confidence: str) -> dict:
    # A timestamp outside the platform range (mtime 0 from an unzipped archive, or
    # a pre-1970 file) makes timestamp() raise on Windows, which used to abort the
    # whole source scan; an unknown capture time is 0 instead.
    try:
        captured_at = float(value.timestamp())
    except (OSError, OverflowError, ValueError):
        captured_at = 0.0
    return {
        "captured_at": captured_at,
        "captured_local_date": value.strftime("%Y-%m-%dT%H:%M:%S"),
        "time_source": source,
        "time_confidence": confidence,
        "time_metadata_version": TIME_METADATA_VERSION,
    }


def _parse_embedded_datetime(value: object) -> datetime.datetime | None:
    text = str(value or "").strip().replace("\x00", "")
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.datetime.strptime(text[:19], pattern)
            if 1970 <= parsed.year <= 2100:
                return parsed
        except ValueError:
            continue
    return None


def _image_capture_time(path: Path) -> dict | None:
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            try:
                sub_ifd = exif.get_ifd(0x8769) or {}
            except Exception:
                sub_ifd = {}
            # DateTimeOriginal/Digitized live in the Exif sub-IFD (0x8769) on every
            # camera and phone. ``exif.get(tag)`` only reads IFD0, so asking it for
            # 36867 always missed and the code fell through to tag 306 (the
            # file-modified time): an export or "save as" stamped the export date
            # onto the whole batch, marked "high" confidence.
            for tag, source in ((36867, "exif_original"), (36868, "exif_digitized")):
                parsed = _parse_embedded_datetime(sub_ifd.get(tag))
                if parsed:
                    return _capture_payload(parsed, source, "high")
            for tag, source in ((36867, "exif_original"), (36868, "exif_digitized")):
                parsed = _parse_embedded_datetime(exif.get(tag))
                if parsed:
                    return _capture_payload(parsed, source, "medium")
            parsed = _parse_embedded_datetime(exif.get(306))
            if parsed:
                # 306 is rewritten by every editor and exporter, so it is a weak hint.
                return _capture_payload(parsed, "exif_datetime", "low")
    except Exception:
        return None
    return None


def _filename_capture_time(path: Path) -> dict | None:
    stem = path.stem
    for pattern in _FILENAME_DATE_PATTERNS:
        match = pattern.search(stem)
        if not match:
            continue
        values = match.groupdict()
        try:
            parsed = datetime.datetime(
                int(values["year"]), int(values["month"]), int(values["day"]),
                int(values.get("hour") or 12), int(values.get("minute") or 0),
                int(values.get("second") or 0),
            )
            return _capture_payload(parsed, "filename", "medium")
        except ValueError:
            continue
    return None


_MAX_PLAUSIBLE_FUTURE_SEC = 86400.0

# A cold scan holds the sync lock for minutes on a large library. A download that
# finishes meanwhile used to wait on that lock inside a request thread, which pinned
# the waitress workers one at a time until the whole site stopped answering. Both
# callers treat 0 as "not indexed yet" and the next scan picks the file up.
REGISTER_LOCK_TIMEOUT_SEC = 0.5


class IndexBusyError(RuntimeError):
    """A full scan held the index lock past ``REGISTER_LOCK_TIMEOUT_SEC``.

    Returned-as-0 used to be indistinguishable from "nothing to index" (a finished
    job routinely names stream files yt-dlp merged away), so the download reported
    success while its output stayed unsearchable until someone ran a manual scan.
    Raising lets the downloader take its ``failure_stage='index'`` path instead,
    which keeps the files, marks the job partial and offers a re-index retry.
    """


def _safe_mtime_datetime(mtime: float) -> datetime.datetime:
    """A naive local datetime for ``mtime`` that never raises.

    ``datetime.fromtimestamp`` raises ``OSError``/``OverflowError`` on Windows for
    timestamps before 1970 or past year 9999. One such file (unzipped archive with
    mtime 0, camera with a zeroed clock, COPY keeping 1601 timestamps) used to abort
    the whole source scan, so the source was reported unavailable and nothing new
    ever entered the index.
    """
    try:
        return datetime.datetime.fromtimestamp(mtime)
    except (OSError, OverflowError, ValueError):
        return datetime.datetime(1970, 1, 1, 0, 0, 0)


def _clamp_future(payload: dict) -> dict:
    """Drop a capture time that lies in the future.

    A filename or EXIF tag claiming 2031 used to pin a phantom year to the top of
    the timeline, and the stored month never self-heals. Anything beyond tomorrow is
    treated as untrusted and falls back to the filesystem time.
    """
    captured_at = float(payload.get("captured_at") or 0.0)
    if captured_at <= time.time() + _MAX_PLAUSIBLE_FUTURE_SEC:
        return payload
    return {}


def _clamp_future_mtime(mtime: float) -> float:
    """Cap a filesystem mtime that sits in the future.

    ``_clamp_future`` drops a future EXIF or filename date, but the filesystem
    fallback went through unclamped: a file copied from a machine with a wrong
    clock (or a NAS with clock skew) pinned a phantom future year to the top of
    the timeline and stayed there until its mtime changed.
    """
    if mtime > time.time() + _MAX_PLAUSIBLE_FUTURE_SEC:
        return time.time()
    return mtime


def _capture_is_current(existing: dict) -> bool:
    """Whether a cached capture record still describes this file under this timezone.

    ``captured_local_date`` is a wall clock and ``captured_at`` the epoch of the
    same instant, both computed under the timezone the scan ran in. Moving the
    instance (or the library) to a machine with another offset left the two columns
    pointing at different calendar days, and the unchanged check below used to
    trust the row forever, so nothing ever recomputed it.
    """
    if int(existing.get("time_metadata_version") or 0) != TIME_METADATA_VERSION:
        return False
    local_date = str(existing.get("captured_local_date") or "")
    if not local_date:
        return False
    captured_at = float(existing.get("captured_at") or 0.0)
    if captured_at <= 0:
        # Unknown capture time: the wall clock came from the same fallback as now.
        return True
    try:
        local = datetime.datetime.fromtimestamp(captured_at)
    except (OSError, OverflowError, ValueError):
        return False
    # Compare the calendar day, not the second: a wall clock that does not exist in
    # this timezone (the hour skipped by a spring-forward transition) is normalised
    # by timestamp(), so the round trip lands an hour off and a strict comparison
    # would re-probe that file on every scan forever.
    return local.strftime("%Y-%m-%d") == local_date[:10]


def discover_capture_time(path: Path, media_type: str, mtime: float) -> dict:
    if media_type == "image":
        embedded = _clamp_future(_image_capture_time(path) or {})
        if embedded:
            return embedded
    filename_value = _clamp_future(_filename_capture_time(path) or {})
    if filename_value:
        return filename_value
    return _capture_payload(
        _safe_mtime_datetime(_clamp_future_mtime(mtime)),
        "filesystem_mtime",
        "fallback",
    )


class LibraryIndexer:
    """Translate the filesystem library into stable index records."""

    def __init__(self, library_service, store: MediaIndexStore):
        self.library = library_service
        self.store = store
        self.on_change = lambda: None
        self._sync_lock = threading.Lock()
        #: Set when a scan was skipped because one was already running. Without the
        #: follow-up pass a folder change made during a long scan stayed unindexed
        #: until the next restart: sync() answered "busy", the route reported success
        #: and nothing rescanned the new folder.
        self._rescan_requested = False

    def sync(self) -> dict:
        # A double click or two devices can start two full scans at once; the second
        # one would duplicate the I/O and can hit a busy database.
        if not self._sync_lock.acquire(blocking=False):
            self._rescan_requested = True
            return {
                'indexed': 0,
                'deleted': 0,
                'unavailable_sources': [],
                'source_errors': {},
                'busy': True,
            }
        try:
            result = self._sync_locked()
            while self._rescan_requested:
                self._rescan_requested = False
                result = self._sync_locked()
            return result
        finally:
            self._sync_lock.release()

    def _sync_locked(self) -> dict:
        time_states = self.store.time_states()
        completed_sources = set()
        failures = {}
        records_by_uri = {}
        preserved: list[str] = []
        for source in self.library.sources:
            skipped: list[str] = []
            try:
                paths = self.library.scan_source(source, skipped=skipped)
                records = self._records_for_paths(paths, time_states)
                if paths and not records:
                    # The tree was walked but not one path could be stat'ed: the
                    # source went away mid-scan (USB unplugged, SMB dropped). Treating
                    # that as "the source is now empty" deleted every row and swept
                    # its playback positions and watch history for good.
                    raise OSError(errno.ENOENT, 'source vanished during scan', str(source.path))
                records = self._apply_douyin_pairing(records)
            except OSError as error:
                failures[source.id] = str(error)
                continue
            completed_sources.add(source.id)
            preserved.extend(skipped)
            records_by_uri.update((record['uri'], record) for record in records)
        result = self.store.replace_snapshot(
            list(records_by_uri.values()),
            synced_source_ids=completed_sources,
            preserved_prefixes=preserved,
            configured_source_ids={source.id for source in self.library.sources},
        )
        if preserved:
            result['skipped_prefixes'] = list(preserved)
        result['unavailable_sources'] = list(failures)
        result['source_errors'] = failures
        self.on_change()
        return result

    def register_uris(self, uris: list[str]) -> int:
        # A download finishing mid-scan must not be treated as "not seen by the
        # scan" by replace_snapshot(), which deletes every row outside its seen set.
        if not self._sync_lock.acquire(timeout=REGISTER_LOCK_TIMEOUT_SEC):
            LOGGER.warning('index scan still running; %d output(s) left for the next scan', len(uris))
            raise IndexBusyError(f'index scan still running; {len(uris)} output(s) not registered')
        try:
            return self._register_uris_locked(uris)
        finally:
            self._sync_lock.release()

    def _register_uris_locked(self, uris: list[str]) -> int:
        time_states = self.store.time_states()
        records = []
        for uri in uris:
            if Path(uri).suffix.lower() not in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS:
                continue
            path = self.library.resolve_path(uri)
            if path is None or not path.is_file():
                # One missing output must not cost the rest of the batch. yt-dlp
                # reports every `Destination:` line, including the separate video
                # and audio streams it deletes again after merging, so the list a
                # finished job hands over routinely names files that are gone.
                LOGGER.warning('Skipping a media output that no longer exists: %s', uri)
                continue
            try:
                record = self._record_for_path(path, time_states)
            except OSError as error:
                # is_file() passed but stat() can still fail on a share (SMB lock,
                # antivirus, ACL). Dropping the whole batch for one such file lost
                # every sibling output that had already parsed fine.
                LOGGER.warning('Unable to register media output %s: %s', uri, error)
                continue
            if record is None:
                LOGGER.warning('Unable to register media output: %s', uri)
                continue
            records.append(record)
        records = self._apply_douyin_pairing(records)
        count = self.store.upsert(records)
        self.on_change()
        return count

    def _apply_douyin_pairing(self, records: list[dict]) -> list[dict]:
        """Fold Douyin-style export posts into swipeable groups.

        Both live (``-实况-``) and album (``-图集-``) posts share a timestamp
        prefix; every member carries album_key/index/size so a post renders as
        one entry. Inside a live post the ``_N`` counter runs across mp4 and
        jpeg alike: an mp4 pairs with the adjacent jpeg (next, else previous) —
        the pair becomes one 'live' row on the jpeg cover, and the mp4 keeps no
        row of its own.
        """
        grouped: dict[tuple[str, str], list[dict]] = {}
        for record in records:
            grouped.setdefault((record['source_id'], record['parent_path']), []).append(record)

        output: list[dict] = []
        for members in grouped.values():
            live_posts: dict[str, list[tuple[int, dict]]] = {}
            albums: dict[str, list[tuple[int, dict]]] = {}
            plain: list[dict] = []
            for record in members:
                filename = record['filename']
                if record['media_type'] not in ('image', 'video'):
                    plain.append(record)
                    continue
                post = media_naming.live_post(filename)
                if post:
                    live_posts.setdefault(post[0], []).append((post[1], record))
                    continue
                album = media_naming.album_member(filename)
                if album and record['media_type'] == 'image':
                    albums.setdefault(album[0], []).append((album[1], record))
                    continue
                plain.append(record)

            def emit(key: str, ordered: list[dict]) -> None:
                size = len(ordered)
                # Stored 0-based so "album_index = 0" is the group's cover.
                for position, record in enumerate(ordered):
                    record['album_key'] = f"{record['source_id']}|{record['parent_path']}|{key}"
                    record['album_index'] = position
                    record['album_size'] = size
                    plain.append(record)

            for key, indexed in albums.items():
                indexed.sort(key=lambda pair: pair[0])
                emit(key, [record for _index, record in indexed])

            for key, indexed in live_posts.items():
                # Same-N ties (same-stem exports) keep the video before its cover.
                indexed.sort(key=lambda pair: (pair[0], pair[1]['media_type'] != 'video'))
                paired: set[int] = set()
                pairs: dict[int, int] = {}  # video position -> jpeg position

                def free_jpeg(pos: int) -> bool:
                    return (0 <= pos < len(indexed) and pos not in paired
                            and indexed[pos][1]['media_type'] == 'image')

                for position, (_index, record) in enumerate(indexed):
                    if record['media_type'] != 'video' or position in paired:
                        continue
                    # An mp4 pairs with the strictly adjacent jpeg: next, else previous.
                    if free_jpeg(position + 1):
                        pairs[position] = position + 1
                        paired.update({position, position + 1})
                    elif free_jpeg(position - 1):
                        pairs[position] = position - 1
                        paired.update({position, position - 1})
                ordered: list[dict] = []
                for position, (_index, record) in enumerate(indexed):
                    if position in pairs:
                        video = record
                        cover = indexed[pairs[position]][1]
                        cover['media_type'] = 'live'
                        cover['live_video_uri'] = video['uri']
                        continue  # merged into its cover's live row below via the cover
                    if position in paired and record['media_type'] == 'video':
                        continue
                    ordered.append(record)
                emit(key, ordered)

            output.extend(plain)
        return output

    #: EXIF reads dominate a cold index (6.25 s of 8.19 s for 19k images) and each
    #: file is independent, so they run on a small pool instead of the scan thread.
    #: Bounded at 8 so a network share is not flooded with parallel opens.
    CAPTURE_WORKERS = min(8, os.cpu_count() or 4)

    def _records_for_paths(self, paths, time_states: dict[str, dict] | None) -> list[dict]:
        records: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.CAPTURE_WORKERS) as pool:
            for record in pool.map(lambda path: self._record_for_path(path, time_states), paths):
                if record:
                    records.append(record)
        return records

    def _record_for_path(self, path: Path, time_states: dict[str, dict] | None = None) -> dict | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            # The file vanished between the directory walk and this stat (a finished
            # download cleaning up, a delete): skip it and let the snapshot drop it.
            # Any other OSError (a locked or unreadable file) still fails the source
            # so the previous index survives instead of losing every row.
            return None
        uri = self.library.get_relative_path(path)
        ref = self.library.parse_uri(uri)
        if not ref:
            return None
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            media_type = "video"
        elif suffix in IMAGE_EXTENSIONS:
            media_type = "image"
        elif suffix in AUDIO_EXTENSIONS:
            media_type = "audio"
        else:
            return None
        existing = (time_states or {}).get(uri) or {}
        # `or -1` treated a real 0 as "missing": a 0-byte file and an mtime of 0.0
        # (files unpacked from an archive) never matched, so every scan reopened them.
        stored_size = existing.get("size_bytes")
        stored_mtime = existing.get("mtime")
        unchanged = (
            stored_size is not None
            and stored_mtime is not None
            and int(stored_size) == int(stat.st_size)
            and float(stored_mtime) == float(stat.st_mtime)
            and _capture_is_current(existing)
        )
        capture = {
            "captured_at": float(existing.get("captured_at") or stat.st_mtime),
            "captured_local_date": str(existing.get("captured_local_date") or ""),
            "time_source": str(existing.get("time_source") or "filesystem_mtime"),
            "time_confidence": str(existing.get("time_confidence") or "fallback"),
            "time_metadata_version": int(existing.get("time_metadata_version") or 0),
        } if unchanged else discover_capture_time(path, media_type, float(stat.st_mtime))
        return {
            "uri": uri,
            "source_id": ref.source_id,
            "rel_path": ref.rel_path,
            "filename": path.name,
            "parent_path": str(Path(ref.rel_path).parent).replace("\\", "/"),
            "media_type": media_type,
            "extension": suffix,
            "size_bytes": int(stat.st_size),
            "mtime": float(stat.st_mtime),
            **capture,
        }


class MediaProbeWorker:
    """Reads media dimensions in the background so pages never wait on ffprobe."""

    def __init__(self, library_service, store: MediaIndexStore):
        self.library = library_service
        self.store = store
        self._wake = threading.Event()
        self._warned: set[str] = set()
        # uri -> (attempts, retry_at_monotonic). A failed probe backs off instead of
        # being retried first on every wake, which used to starve the whole library.
        self._backoff: dict[str, tuple[int, float]] = {}
        self.failures = 0

    def start(self) -> None:
        threading.Thread(target=self._loop, name="media-probe", daemon=True).start()

    def wake(self) -> None:
        self._wake.set()

    def _defer_probe(self, uri: str, reason: str, base_delay: float = 5.0) -> None:
        attempts = self._backoff.get(uri, (0, 0.0))[0] + 1
        delay = min(300.0, base_delay * (2 ** (attempts - 1)))
        self._backoff[uri] = (attempts, time.monotonic() + delay)
        if uri not in self._warned:
            if len(self._warned) > 500:
                self._warned.clear()
            self._warned.add(uri)
            LOGGER.warning('Could not read media dimensions for %s (%s); will retry later', uri, reason)

    def run_once(self, batch: int = 50, budget_sec: float | None = None) -> int:
        """Probe until nothing is left (or the time budget is spent); return how many files were read."""
        done = 0
        deadline = time.monotonic() + budget_sec if budget_sec else None
        # Without ffprobe (the bundled imageio-ffmpeg build) or ffmpeg, a video's
        # size is unknown rather than unmeasurable: keep probed_mtime unset so a
        # later pass retries once a tool is installed.
        can_probe_video = probe_tool_available()
        while True:
            if deadline is not None and time.monotonic() > deadline:
                break
            now = time.monotonic()
            if len(self._backoff) > 400:
                for stale in [u for u, (_, at) in self._backoff.items() if at <= now][:200]:
                    self._backoff.pop(stale, None)
            skip = tuple(uri for uri, (_, at) in self._backoff.items() if at > now)[:400]
            rows = self.store.unprobed(batch, exclude=skip)
            if not rows:
                break
            results = []
            for row in rows:
                if deadline is not None and time.monotonic() > deadline:
                    break
                # A live entry is probed through its jpeg cover.
                media_type = 'image' if row["media_type"] == 'live' else row["media_type"]
                if media_type == 'audio':
                    # Audio has no dimensions to read; record the pass so the row
                    # stops showing up in every batch.
                    results.append((None, None, row["mtime"], row["uri"]))
                    self._backoff.pop(row["uri"], None)
                    continue
                if media_type != 'image' and not can_probe_video:
                    self._defer_probe(row["uri"], 'no probe tool', base_delay=60.0)
                    continue
                width, height = probe_media_dims(self.library, row["uri"], media_type)
                if width is None or height is None:
                    # Writing probed_mtime for a failed read made the failure
                    # permanent: a corrupt JPEG or a timed-out ffprobe never got a
                    # second chance and nothing explained the missing tile size.
                    self._defer_probe(row["uri"], 'read failed')
                    continue
                self._backoff.pop(row["uri"], None)
                results.append((width, height, row["mtime"], row["uri"]))
            if not results:
                # The whole batch failed or was deferred. The exclusion list makes the
                # next query start deeper, so a bad head no longer starves the rest.
                continue
            self.store.set_dimensions(results)
            done += len(results)
        return done

    def _loop(self) -> None:
        while True:
            self._wake.clear()
            done = 0
            try:
                # A first scan of 8000 videos means 8000 ffprobe calls (~15 min).
                # Without a per-round budget that ran flat out and starved the
                # request threads; five seconds per round keeps the box usable.
                done = self.run_once(budget_sec=5.0)
            except Exception:
                # Dimensions are optional and the next wake retries, but swallowing
                # this silently meant a locked database left every new file without
                # width/height with nothing in the log to explain it.
                self.failures += 1
                LOGGER.warning('Media probe pass failed; dimensions stay unset for now', exc_info=True)
            if done:
                self._wake.wait(0.5)
            else:
                # Nothing was probed: either every row is done, or the probe tool is
                # missing (rows were deferred with a backoff). Waking on index changes
                # only meant installing ffmpeg never triggered a retry, so poll slowly.
                self._wake.wait(30.0)
