import errno
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiklocal.paths import DOWNLOAD_TMP_DIR
from tiklocal.services.trash import TRASH_DIR

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.aac', '.m4a', '.ogg', '.opus', '.wav'}

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_dir(path: Path) -> bool:
    """True for junctions/symlinks: os.walk follows junctions but symlinks only if asked."""
    isjunction = getattr(os.path, 'isjunction', None)
    if isjunction is not None:
        try:
            return bool(isjunction(path))
        except OSError:
            return True
    if path.is_symlink():
        return True
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return True
    return bool(getattr(info, 'st_file_attributes', 0) & _FILE_ATTRIBUTE_REPARSE_POINT) or bool(
        stat.S_ISLNK(info.st_mode)
    )


def _mtime_desc_key(path: Path) -> float:
    """Sort key that survives a file disappearing mid-scan (CLI thumbnails)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@dataclass(frozen=True)
class MediaSource:
    id: str
    name: str
    path: Path


@dataclass(frozen=True)
class MediaRef:
    source_id: str
    rel_path: str

    def to_uri(self) -> str:
        return f"@{self.source_id}/{self.rel_path}"


def normalize_source_id(value: Any) -> str:
    text = str(value or '').strip().lower()
    cleaned = ''.join(ch for ch in text if ch.isalnum() or ch in {'-', '_'})
    return cleaned or 'default'


def normalize_media_uri(value: Any) -> str:
    text = str(value or '').strip().replace('\\', '/')
    while text.startswith('./'):
        text = text[2:]
    return text


def build_media_sources(media_root: str | Path | None = None, raw_sources: Any = None) -> list[MediaSource]:
    sources: list[MediaSource] = []
    seen: set[str] = set()

    def add_source(source_id: Any, name: Any, path_value: Any) -> None:
        path_text = str(path_value or '').strip()
        if not path_text:
            return
        sid = normalize_source_id(source_id)
        if sid in seen:
            # Silently dropping the second source made its directory disappear from
            # the index while the startup banner still listed it.
            logger.warning(
                'Duplicate media source id %r in MEDIA_SOURCES; ignoring path %s '
                '(first path for this id wins).', sid, path_text,
            )
            return
        seen.add(sid)
        sources.append(MediaSource(
            id=sid,
            name=str(name or sid).strip() or sid,
            path=Path(path_text).expanduser().resolve(),
        ))

    if raw_sources:
        if isinstance(raw_sources, dict):
            iterable = [{'id': key, 'name': key, 'path': value} for key, value in raw_sources.items()]
        else:
            iterable = raw_sources if isinstance(raw_sources, list) else []
        for item in iterable:
            if isinstance(item, dict):
                add_source(item.get('id') or item.get('name'), item.get('name') or item.get('id'), item.get('path'))

    if media_root and 'default' not in seen:
        add_source('default', 'Default', media_root)

    return sources


class LibraryService:
    def __init__(self, media_root: str | Path | None = None, media_sources: list[MediaSource] | None = None):
        self.sources = media_sources or build_media_sources(media_root)
        if not self.sources:
            self.sources = build_media_sources(media_root or '.')
        self.sources_by_id = {source.id: source for source in self.sources}
        self.default_source_id = 'default' if 'default' in self.sources_by_id else self.sources[0].id
        self.media_root = self.sources_by_id[self.default_source_id].path
        # Resolving a source root costs a syscall (two on Windows) and the indexer
        # calls ref_for_path() once per file: resolve each root exactly once.
        self._resolved_roots = [(source, source.path.resolve()) for source in self.sources]
        self._warn_overlapping_sources()

    def _warn_overlapping_sources(self) -> None:
        """Name sources that another source's root swallows.

        ref_for_path() returns the first root that contains the file, so a source
        configured on a subdirectory of another source (or on the same directory)
        never owns a single record: its chip disappears from the UI, `?blog=<id>`
        is always empty, and nothing anywhere says why. Warn once at startup.
        """
        accepted: list[tuple[Any, Path]] = []
        for source, root in self._resolved_roots:
            shadowed_by = None
            for other_source, other_root in accepted:
                try:
                    root.relative_to(other_root)
                except ValueError:
                    continue
                shadowed_by = other_source
                break
            if shadowed_by is None:
                accepted.append((source, root))
                continue
            logger.warning(
                'Media source %r is inside media source %r; its files are indexed under %r. '
                'Configure non-overlapping directories if it should appear separately.',
                source.id, shadowed_by.id, shadowed_by.id,
            )

    def scan_source(
        self, source: MediaSource, extensions: set[str] | None = None, *, recursive: bool = True,
        skipped: list[str] | None = None,
    ) -> list[Path]:
        """Scan one source, skipping directories it may not read.

        Raising on the first unreadable folder (System Volume Information at a drive
        root, an ACL-locked folder) used to fail the whole source, which left the
        library empty and froze the index: a failed scan never publishes a snapshot.

        A skipped subfolder is recorded in ``skipped`` (source-relative URI prefix):
        the indexer must keep those rows, otherwise a folder we merely cannot read
        right now looks like "all its media was deleted".
        """
        extensions = extensions if extensions is not None else VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS

        def skip_unreadable(error: OSError) -> None:
            # A subfolder we may not read is normal (System Volume Information); the
            # source itself disappearing must still surface, otherwise the index would
            # treat "cannot scan" as "everything was deleted" and drop every record.
            if Path(error.filename or '') == source.path:
                raise error
            if error.errno in (errno.EACCES, errno.EPERM):
                if skipped is not None and error.filename:
                    try:
                        relative = Path(error.filename).resolve().relative_to(source.path.resolve())
                    except (OSError, ValueError):
                        relative = None
                    if relative is not None and str(relative) != '.':
                        skipped.append(f'@{source.id}/{relative.as_posix().rstrip("/")}/')
                return
            raise error

        paths = []
        for directory, folders, files in os.walk(source.path, onerror=skip_unreadable):
            if not recursive:
                folders.clear()
            else:
                # Neither the trash nor a download's scratch folder holds finished
                # media; indexing them published half-written files and broken previews.
                for hidden in (TRASH_DIR, DOWNLOAD_TMP_DIR):
                    if hidden in folders:
                        folders.remove(hidden)
                # A junction/symlink can point outside the library (or back into an
                # ancestor and loop forever); its files would also be indexed under a
                # host absolute path and never load.
                folders[:] = [
                    name for name in folders if not _is_reparse_dir(Path(directory) / name)
                ]
            paths.extend(
                Path(directory) / name for name in files
                if Path(name).suffix.lower() in extensions
            )
        return paths

    def _scan_available_sources(self, extensions: set[str], recursive: bool) -> list[Path]:
        paths = []
        for source in self.sources:
            try:
                paths.extend(self.scan_source(source, extensions, recursive=recursive))
            except OSError:
                continue
        return paths

    def scan_videos(self, recursive=True) -> list[Path]:
        return sorted(self._scan_available_sources(VIDEO_EXTENSIONS, recursive), key=_mtime_desc_key, reverse=True)

    def scan_audios(self, recursive=True) -> list[Path]:
        return sorted(self._scan_available_sources(AUDIO_EXTENSIONS, recursive), key=_mtime_desc_key, reverse=True)

    def scan_images(self, recursive=True) -> list[Path]:
        return self._scan_available_sources(IMAGE_EXTENSIONS, recursive)

    def get_relative_path(self, path: Path) -> str:
        ref = self.ref_for_path(path)
        # Never fall back to a host absolute path: it becomes a URI that 404s on
        # /media and /thumb, and it leaks local directory names to LAN clients.
        return ref.to_uri() if ref else ''

    def ref_for_path(self, path: Path) -> MediaRef | None:
        # resolve() costs ~290µs per file on Windows (2.3s for 8000 files) and the
        # scan hands us paths that already start with the source root, so try the
        # lexical match first and only resolve when that fails (symlinked roots).
        for source, root in self._resolved_roots:
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if not parts or '..' in parts:
                continue
            return MediaRef(source.id, '/'.join(parts))
        try:
            target = path.resolve()
        except OSError:
            return None
        for source, root in self._resolved_roots:
            try:
                return MediaRef(source.id, str(target.relative_to(root)).replace('\\', '/'))
            except ValueError:
                continue
        return None

    def parse_uri(self, uri: str) -> MediaRef | None:
        clean = normalize_media_uri(uri)
        if not clean:
            return None
        if clean.startswith('@'):
            source_text, sep, rel_path = clean[1:].partition('/')
            source_id = normalize_source_id(source_text)
            rel_path = normalize_media_uri(rel_path)
            if sep and source_id in self.sources_by_id and rel_path:
                return MediaRef(source_id, rel_path)
            return None
        return MediaRef(self.default_source_id, clean)

    def canonicalize_uri(self, uri: str) -> str:
        ref = self.parse_uri(uri)
        return ref.to_uri() if ref else normalize_media_uri(uri)

    def legacy_candidates(self, uri: str) -> list[str]:
        clean = normalize_media_uri(uri)
        ref = self.parse_uri(clean)
        if not ref:
            return [clean] if clean else []
        candidates = [ref.to_uri()]
        if ref.source_id == self.default_source_id:
            candidates.append(ref.rel_path)
        return list(dict.fromkeys([item for item in candidates if item]))

    def is_uri_in_set(self, uri: str, values: set[str]) -> bool:
        return any(candidate in values for candidate in self.legacy_candidates(uri))

    def canonicalize_many(self, values: set[str]) -> set[str]:
        return {self.canonicalize_uri(value) for value in values if normalize_media_uri(value)}

    def source_for_uri(self, uri: str) -> MediaSource | None:
        ref = self.parse_uri(uri)
        return self.sources_by_id.get(ref.source_id) if ref else None

    def relative_path_for_uri(self, uri: str) -> str:
        ref = self.parse_uri(uri)
        return ref.rel_path if ref else normalize_media_uri(uri)

    def canonicalize_outputs(self, rel_paths: list[str], *, source_id: str | None = None) -> list[str]:
        sid = source_id or self.default_source_id
        outputs: list[str] = []
        for rel_path in rel_paths:
            clean = normalize_media_uri(rel_path)
            if not clean:
                continue
            outputs.append(self.canonicalize_uri(clean) if clean.startswith('@') else MediaRef(sid, clean).to_uri())
        return outputs

    def resolve_source_relative_path(self, uri: str) -> tuple[MediaSource, str] | None:
        ref = self.parse_uri(uri)
        if not ref:
            return None
        source = self.sources_by_id.get(ref.source_id)
        if not source:
            return None
        return source, ref.rel_path

    def resolve_path(self, relative_path: str) -> Path | None:
        """Securely resolve a media URI to an absolute path within its media source."""
        resolved = self.resolve_source_relative_path(relative_path)
        if not resolved:
            return None
        source, rel_path = resolved
        try:
            target = (source.path / rel_path).resolve()
            target.relative_to(source.path.resolve())
            return target
        except Exception:
            return None

    def find_existing_uri(self, uri: str) -> str:
        for candidate in self.legacy_candidates(uri):
            target = self.resolve_path(candidate)
            if target and target.exists():
                return candidate
        return self.canonicalize_uri(uri)
