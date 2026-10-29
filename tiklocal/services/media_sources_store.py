"""Media library folders the user manages from the settings page.

Start-up configuration (``config.yaml``, ``--media-source``, ``MEDIA_ROOT``) stays
the default way to point TikLocal at a library. Once the settings page saves this
store, the store owns the folder list: a folder removed in the UI must not
reappear on the next start, and a folder added there must still be there.

Everything here is deliberately small and side-effect free except :func:`apply_sources`,
which is the one place that mutates a live service. Keeping the mutation here
(rather than in ``LibraryService``) means the settings feature cannot change the
behaviour of the reader/scan hot paths.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tiklocal.services.json_storage import read_json_file, write_json_atomic
from tiklocal.services.library import MediaSource, normalize_source_id

LOGGER = logging.getLogger(__name__)

#: Bumped only when the stored meaning of a field changes.
STORE_VERSION = 1
#: A person with a media collection rarely has more than a handful of roots; the
#: cap keeps one bad request from filling the settings page (and the index scan).
MAX_SOURCES = 24
MAX_NAME_LENGTH = 60
DEFAULT_SOURCE_ID = 'default'


class MediaSourceError(ValueError):
    """A folder-list edit the user has to fix.

    ``code`` is a stable identifier, not a sentence: the web layer maps it to the
    translated ``settings.library.source_error_<code>`` message, so validation
    stays translatable and testable without importing Flask.
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


@dataclass(frozen=True)
class StoredMediaSources:
    """The persisted folder list plus the ids the user explicitly removed."""

    sources: tuple[MediaSource, ...]
    removed_ids: frozenset[str] = frozenset()

    def without(self, source_id: str) -> 'StoredMediaSources':
        return StoredMediaSources(self.sources, self.removed_ids | {source_id})


class MediaSourceStore:
    """JSON list of library folders inside the instance directory."""

    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)

    def load(self) -> StoredMediaSources | None:
        """Return the saved list, or ``None`` when the user never edited it.

        A store that cannot be read raises :class:`~tiklocal.services.json_storage.StoreUnreadableError`
        (an ``OSError``) instead of returning an empty list: the caller must fall
        back to the launch configuration, never to "no folders at all".
        """
        payload = read_json_file(self.store_path, None, expected_type=dict)
        if not isinstance(payload, dict):
            return None
        raw_sources = payload.get('sources')
        if not isinstance(raw_sources, list):
            return None
        sources = _coerce_sources(raw_sources)
        if not sources:
            return None
        raw_removed = payload.get('removed')
        removed_ids = frozenset(
            normalize_source_id(item)
            for item in raw_removed
            if str(item or '').strip()
        ) if isinstance(raw_removed, list) else frozenset()
        return StoredMediaSources(tuple(sources), removed_ids)

    def save(self, sources: Iterable[MediaSource], *, removed_ids: Iterable[str] = ()) -> None:
        write_json_atomic(self.store_path, {
            'version': STORE_VERSION,
            'sources': [
                {'id': source.id, 'name': source.name, 'path': str(source.path)}
                for source in sources
            ],
            'removed': sorted({
                normalize_source_id(item) for item in removed_ids if str(item or '').strip()
            }),
        })


def _path_key(path: Path) -> str:
    """Case-fold a path so ``D:\\Videos`` and ``d:\\videos`` count as one folder."""
    return os.path.normcase(str(path))


def _coerce_path(raw_path: Any) -> Path | None:
    text = str(raw_path or '').strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve()
    except (OSError, ValueError):
        return None


def _coerce_sources(raw_sources: Iterable[Any]) -> list[MediaSource]:
    """Read a stored list, dropping unreadable/duplicate entries instead of failing."""
    sources: list[MediaSource] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        path = _coerce_path(item.get('path'))
        if path is None:
            continue
        source_id = normalize_source_id(item.get('id') or item.get('name'))
        key = _path_key(path)
        if source_id in seen_ids or key in seen_paths:
            continue
        seen_ids.add(source_id)
        seen_paths.add(key)
        name = _clean_name(item.get('name')) or source_id
        sources.append(MediaSource(id=source_id, name=name, path=path))
    return sources


def _clean_name(raw_name: Any) -> str:
    text = ' '.join(str(raw_name or '').split())
    return text[:MAX_NAME_LENGTH]


def _folder_name(path: Path) -> str:
    # A drive root ("D:\\") has no name; the path itself is the useful label.
    return path.name or str(path)


def _unique_id(sources: Iterable[MediaSource], candidate: str) -> str:
    taken = {source.id for source in sources}
    if candidate not in taken:
        return candidate
    index = 2
    while f'{candidate}-{index}' in taken:
        index += 1
    return f'{candidate}-{index}'


def validate_folder(raw_path: Any) -> Path:
    """Resolve a folder the user typed, or raise the reason it cannot be used."""
    text = str(raw_path or '').strip()
    # Explorer's "Copy as path" wraps the path in quotes.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    path = _coerce_path(text)
    if path is None:
        raise MediaSourceError('required' if not text else 'missing', path=text)
    if not path.exists():
        raise MediaSourceError('missing', path=str(path))
    if not path.is_dir():
        raise MediaSourceError('not_dir', path=str(path))
    try:
        with os.scandir(path) as entries:
            next(entries, None)
    except OSError:
        raise MediaSourceError('unreadable', path=str(path)) from None
    return path


def add_source(
    sources: list[MediaSource], *, path: Any, name: Any = '',
) -> tuple[list[MediaSource], MediaSource]:
    folder = validate_folder(path)
    if len(sources) >= MAX_SOURCES:
        raise MediaSourceError('too_many', max=MAX_SOURCES)
    if any(_path_key(source.path) == _path_key(folder) for source in sources):
        raise MediaSourceError('duplicate', path=str(folder))
    display = _clean_name(name) or _folder_name(folder)
    source = MediaSource(
        id=_unique_id(sources, normalize_source_id(display)),
        name=display,
        path=folder,
    )
    return [*sources, source], source


def update_source(
    sources: list[MediaSource], source_id: Any, *, path: Any = None, name: Any = None,
) -> tuple[list[MediaSource], MediaSource]:
    wanted = normalize_source_id(source_id)
    index = next((position for position, source in enumerate(sources) if source.id == wanted), -1)
    if index < 0:
        raise MediaSourceError('not_found', id=wanted)
    current = sources[index]
    folder = current.path
    if str(path or '').strip():
        folder = validate_folder(path)
    if _path_key(folder) != _path_key(current.path) and any(
        _path_key(source.path) == _path_key(folder)
        for position, source in enumerate(sources)
        if position != index
    ):
        raise MediaSourceError('duplicate', path=str(folder))
    updated = MediaSource(
        id=current.id,
        name=_clean_name(name) or current.name,
        path=folder,
    )
    result = list(sources)
    result[index] = updated
    return result, updated


def remove_source(
    sources: list[MediaSource], source_id: Any, *, protected_ids: Iterable[str] = (),
) -> list[MediaSource]:
    wanted = normalize_source_id(source_id)
    if wanted == DEFAULT_SOURCE_ID:
        # The default folder anchors downloads and the index; it can be pointed
        # somewhere else but never dropped.
        raise MediaSourceError('default')
    if wanted in {normalize_source_id(item) for item in protected_ids}:
        # The running downloader writes into this folder; removing it would leave
        # finished downloads outside every source.
        raise MediaSourceError('download_target')
    if not any(source.id == wanted for source in sources):
        raise MediaSourceError('not_found', id=wanted)
    remaining = [source for source in sources if source.id != wanted]
    if not remaining:
        raise MediaSourceError('last')
    return remaining


def resolve_startup_sources(
    stored: StoredMediaSources, startup_sources: list[MediaSource], *, logger: logging.Logger = LOGGER,
) -> list[MediaSource]:
    """Fold the saved folder list onto what the launch command asked for.

    * Saved folders win, including the default one: the launch directory is only
      used when the saved default is unreachable right now (unplugged drive,
      renamed folder), so a start with a valid directory never serves an empty
      library.
    * A folder the user removed stays removed. Launch-time sources that were never
      part of the saved list are still honoured (``--media-source`` keeps working),
      which is why removals are recorded by id.
    """
    saved = list(stored.sources)
    startup_default = next((source for source in startup_sources if source.id == DEFAULT_SOURCE_ID), None)
    saved_default = next((source for source in saved if source.id == DEFAULT_SOURCE_ID), None)
    if saved_default is None and startup_default is not None:
        saved.insert(0, startup_default)
    elif (
        saved_default is not None
        and startup_default is not None
        and not saved_default.path.is_dir()
        and startup_default.path.is_dir()
    ):
        logger.warning(
            'Media folder %s saved in settings is unavailable; using %s for this start',
            saved_default.path, startup_default.path,
        )
        saved = [
            MediaSource(source.id, source.name, startup_default.path)
            if source.id == DEFAULT_SOURCE_ID else source
            for source in saved
        ]
    elif (
        saved_default is not None
        and startup_default is not None
        and saved_default.path.is_dir()
        and startup_default.path.is_dir()
        and _path_key(saved_default.path) != _path_key(startup_default.path)
    ):
        # The settings page owns the folder list and has to keep winning, otherwise a
        # folder the user removed there comes back on the next start. Silently ignoring
        # the launch directory is the part that hurts: downloads and deletions keep
        # landing in the saved folder while the banner and the log show the new one.
        logger.warning(
            'Media folder %s from settings wins over the launch directory %s; '
            'change it under Settings -> Media folders',
            saved_default.path, startup_default.path,
        )
    known_ids = {source.id for source in saved}
    extras = []
    for source in startup_sources:
        if source.id in known_ids:
            continue
        if source.id in stored.removed_ids:
            logger.warning(
                'Media folder %r was removed in settings; ignoring the same id from this start',
                source.id,
            )
            continue
        extras.append(source)
    return [*saved, *extras]


def apply_sources(library_service: Any, sources: Iterable[MediaSource]) -> list[MediaSource]:
    """Point the running library at ``sources`` without restarting.

    Every consumer (indexer, thumbnails, radio, downloads, routes) holds this same
    :class:`~tiklocal.services.library.LibraryService` instance, so rebuilding the
    derived attributes in place is what makes the change live. ``_resolved_roots``
    is the scanner's/URI parser's boundary and has to be rebuilt with them.
    """
    installed = list(sources)
    if not installed:
        raise MediaSourceError('last')
    # Resolve first: resolve() can fail (offline share, revoked permission) and the
    # caller has already written the new list to disk. Assigning the sources before
    # that left the library on the new roots while URIs and thumbnails still used
    # the old ones -- a half-applied state no retry could repair.
    resolved_roots = [(source, source.path.resolve()) for source in installed]
    library_service.sources = installed
    library_service.sources_by_id = {source.id: source for source in installed}
    library_service.default_source_id = (
        DEFAULT_SOURCE_ID if DEFAULT_SOURCE_ID in library_service.sources_by_id else installed[0].id
    )
    library_service.media_root = library_service.sources_by_id[library_service.default_source_id].path
    library_service._resolved_roots = resolved_roots
    library_service._warn_overlapping_sources()
    return installed


def retarget_thumbnail_service(thumbnail_service: Any, media_root: Path) -> None:
    """Refresh the thumbnail cache identity after the default folder moved.

    ``ThumbnailService`` mixes the library root into every cache key so two
    libraries cannot serve each other's pictures; keeping the old key after a
    folder change would do exactly that. Mirrors ``ThumbnailService.__init__``
    because that module is shared with other in-flight work.
    """
    thumbnail_service.media_root = media_root
    thumbnail_service._root_key = hashlib.sha1(
        str(Path(media_root).resolve()).lower().encode('utf-8')
    ).hexdigest()[:16]


def item_counts(media_index: Any, *, logger: logging.Logger = LOGGER) -> dict[str, int]:
    """Indexed item count per source id; a broken index must not hide the folders."""
    try:
        with media_index.database.connect() as conn:
            rows = conn.execute(
                'SELECT source_id, COUNT(*) AS item_count FROM media_items GROUP BY source_id'
            ).fetchall()
    except sqlite3.Error as error:
        logger.warning('Could not count indexed items per media source: %s', error)
        return {}
    return {str(row['source_id']): int(row['item_count'] or 0) for row in rows}


def describe_sources(
    sources: Iterable[MediaSource], *, counts: dict[str, int] | None = None,
    download_source_id: str = '',
) -> list[dict]:
    """Settings payload: one row per folder, in saved order."""
    counts = counts or {}
    protected = normalize_source_id(download_source_id)
    rows = []
    for source in sources:
        try:
            available = source.path.is_dir()
        except OSError:
            available = False
        rows.append({
            'id': source.id,
            'name': source.name,
            'path': str(source.path),
            'item_count': int(counts.get(source.id, 0)),
            'available': available,
            'is_default': source.id == DEFAULT_SOURCE_ID,
            'removable': source.id != DEFAULT_SOURCE_ID and source.id != protected,
        })
    return rows
