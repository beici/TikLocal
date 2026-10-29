"""Recoverable deletes: files move into a hidden folder inside their own media source."""

import datetime
import json
import secrets
import shutil
from pathlib import Path

TRASH_DIR = '.tiklocal-trash'
RETENTION_DAYS = 30


def sidecar_paths(target: Path) -> list[Path]:
    """Metadata files a downloader wrote next to a media file.

    yt-dlp leaves ``clip.info.json`` beside ``clip.mp4`` and
    ``source_from_info_json`` reads both that and ``clip.mp4.info.json``. A delete
    that moves only the media file leaves the page URL behind: it keeps describing
    a file that is gone, and a later file reusing the name inherits it as its
    provenance.
    """
    candidates = (target.with_suffix('.info.json'), Path(str(target) + '.info.json'))
    return [path for path in candidates if path.is_file()]


class TrashService:
    def __init__(self, library_service, retention_days: int = RETENTION_DAYS):
        self.library = library_service
        self.retention = datetime.timedelta(days=retention_days)

    def move(self, uri: str) -> dict:
        """Move a media file into its source's trash and return the new entry."""
        resolved = self.library.resolve_source_relative_path(uri)
        target = self.library.resolve_path(uri)
        if not resolved or not target or not target.is_file():
            raise FileNotFoundError(uri)
        source, _ = resolved
        now = datetime.datetime.now(datetime.timezone.utc)
        folder = source.path / TRASH_DIR / f"{now:%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
        folder.mkdir(parents=True)
        sidecars = sidecar_paths(target)
        entry = {
            'uri': self.library.canonicalize_uri(uri),
            'name': target.name,
            'size': target.stat().st_size,
            'deleted_at': now.isoformat(timespec='seconds'),
            'sidecars': [path.name for path in sidecars],
        }
        # Write the metadata first: a file that moved while its entry.json failed to
        # write would sit in the trash unreachable, unlisted and never expired.
        (folder / 'entry.json').write_text(json.dumps(entry, ensure_ascii=False), encoding='utf-8')
        try:
            # Same filesystem as the source, so this is a rename rather than a copy.
            shutil.move(str(target), str(folder / target.name))
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        # The media file is already safely in the trash; a sidecar that cannot move
        # is left behind rather than failing the delete, and is dropped from the
        # manifest so restore does not look for a file that is not there.
        moved = []
        for path in sidecars:
            try:
                shutil.move(str(path), str(folder / path.name))
            except OSError:
                continue
            moved.append(path.name)
        if moved != entry['sidecars']:
            entry['sidecars'] = moved
            (folder / 'entry.json').write_text(json.dumps(entry, ensure_ascii=False), encoding='utf-8')
        return {**entry, 'id': f"{source.id}.{folder.name}"}

    def restore(self, entry_id: str) -> str:
        """Put a trashed file back at its original path and return its URI."""
        folder, entry = self._entry(entry_id)
        # The file lives in the trash right now, so the target must resolve without
        # requiring an existing file at that path.
        target = self.library.resolve_target_path(entry['uri'])
        if not target:
            raise FileNotFoundError(entry['uri'])
        if target.exists():
            raise FileExistsError(entry['uri'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder / entry['name']), str(target))
        for name in entry.get('sidecars') or []:
            sidecar = folder / str(name)
            if not sidecar.is_file():
                continue
            destination = target.with_name(str(name))
            # Never overwrite a metadata file that was written while this entry sat
            # in the trash; the older copy is dropped with the folder below.
            if not destination.exists():
                try:
                    shutil.move(str(sidecar), str(destination))
                except OSError:
                    pass
        shutil.rmtree(folder, ignore_errors=True)
        return entry['uri']

    def purge(self, entry_id: str) -> str:
        """Permanently delete one entry and return the URI it came from."""
        source_id, _, name = str(entry_id).partition('.')
        source = self.library.sources_by_id.get(source_id)
        if not source or not name or '/' in name or '\\' in name or name.startswith('.'):
            raise KeyError(entry_id)
        folder = source.path / TRASH_DIR / name
        entry = self._read(folder)
        if not entry:
            if not folder.exists():
                # A retried purge (double click, a second tab, a response that never
                # arrived) finds the folder already gone. That is success, not "not in
                # the trash": answering 404 made the UI report a failure and keep the
                # row around.
                return ''
            raise KeyError(entry_id)
        self._delete_folder(folder)
        return str(entry['uri'])

    @staticmethod
    def _delete_folder(folder: Path) -> None:
        """Delete a trash folder without leaving an entry nobody can see.

        ``shutil.rmtree`` deletes ``entry.json`` before it reaches the payload; on
        Windows a read-only media file (copied from a disc or a read-only share)
        then raised PermissionError and left a folder that was no longer listed,
        could not be restored and never expired -- a silent, permanent leak.
        Payload files go first, the manifest last, and read-only bits are cleared.

        A payload file that still refuses to go (a player holding it open) is left
        behind on purpose, but the manifest is always removed: keeping it would make
        the folder invisible to ``entries()``, so neither the retention timer nor a
        retried purge could ever reach it again.
        """
        manifest = folder / 'entry.json'
        for path in folder.rglob('*'):
            if not path.is_file() or path == manifest:
                continue
            try:
                path.chmod(0o666)
            except OSError:
                pass
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                continue
        manifest.unlink(missing_ok=True)
        shutil.rmtree(folder, ignore_errors=True)

    def entries(self) -> list[dict]:
        """All trashed files across sources, newest first."""
        items = []
        for source in self.library.sources:
            root = source.path / TRASH_DIR
            if not root.is_dir():
                continue
            for folder in root.iterdir():
                entry = self._read(folder)
                if not entry:
                    continue
                deleted_at = str(entry.get('deleted_at') or '')
                if not deleted_at:
                    # A manifest written before the timestamp existed (or edited by
                    # hand) used to raise KeyError from the sort key, and that ran
                    # during startup: one bad entry kept the whole app from booting.
                    # Falling back to the folder's own mtime keeps the entry listed
                    # and purgeable instead of hiding it from /api/trash forever.
                    try:
                        deleted_at = datetime.datetime.fromtimestamp(
                            folder.stat().st_mtime, datetime.timezone.utc,
                        ).isoformat(timespec='seconds')
                    except OSError:
                        continue
                items.append({**entry, 'deleted_at': deleted_at, 'id': f"{source.id}.{folder.name}"})
        return sorted(items, key=lambda item: item['deleted_at'], reverse=True)

    def expired(self) -> list[str]:
        """Ids of entries older than the retention period."""
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - self.retention).isoformat(timespec='seconds')
        return [item['id'] for item in self.entries() if item['deleted_at'] < cutoff]

    def _entry(self, entry_id: str) -> tuple[Path, dict]:
        source_id, _, name = str(entry_id).partition('.')
        source = self.library.sources_by_id.get(source_id)
        if not source or not name or '/' in name or '\\' in name or name.startswith('.'):
            raise KeyError(entry_id)
        folder = source.path / TRASH_DIR / name
        entry = self._read(folder)
        if not entry:
            raise KeyError(entry_id)
        return folder, entry

    @staticmethod
    def _read(folder: Path) -> dict | None:
        try:
            entry = json.loads((folder / 'entry.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or not (folder / str(entry.get('name', ''))).is_file():
            return None
        return entry
