import json
from threading import RLock
from pathlib import Path

from tiklocal.services.library import LibraryService, normalize_media_uri

from tiklocal.services.json_storage import read_json_file, write_json_atomic


class FavoriteService:
    def __init__(
        self,
        db_path: Path,
        library_service: LibraryService,
    ):
        self.db_path = db_path
        self.library_service = library_service
        self._lock = RLock()
        # (mtime_ns, size) of the file the cached set was read from.
        self._cache_stamp: tuple[int, int] | None = None
        self._cache: set[str] = set()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _normalize(self, filename: str) -> str:
        return self.library_service.canonicalize_uri(filename)

    def _read_raw(self, path: Path) -> set[str]:
        # A truncated favorites.json (power loss, full disk) used to raise on every
        # read: the library reported 0 favorites and every write returned 503
        # forever, because save() loads before it writes. read_json_file quarantines
        # the broken file and falls back to an empty list, so the app self-heals.
        data = read_json_file(path, [], expected_type=list)
        return {normalize_media_uri(item) for item in data if isinstance(item, str)}

    def load(self) -> set[str]:
        return self.library_service.canonicalize_many(self._read_raw(self.db_path))

    def read_only(self) -> set[str]:
        """The current favorites, reused until ``favorites.json`` changes.

        ``load()`` parses and canonicalizes the whole file on every call, which is
        right for a read-modify-write but wasteful for callers that only ask
        "is this one a favorite?": one ``/flow`` request asked twice (the picker and
        the reason builder) and the radio page once more. At 10k favorites that was
        42ms of URI parsing per feed page.

        The returned set must not be mutated; use :meth:`toggle` or
        :meth:`set_favorite` to change anything.
        """
        return self._load_cached()

    def save(self, favorites: set[str]) -> None:
        # Callers hand in canonical URIs (load() canonicalizes and _save_state adds a
        # canonical key), so normalizing every entry again only doubled the cost of
        # each star click on a large library.
        with self._lock:
            write_json_atomic(self.db_path, sorted(item for item in favorites if normalize_media_uri(item)))
            self._cache_stamp = None

    def toggle(self, filename: str) -> bool:
        """Toggle and persist within one read-modify-write critical section."""
        with self._lock:
            favs = self.load()
            key = self._normalize(filename)
            return self._save_state(favs, key, key not in favs)

    def set_favorite(self, filename: str, is_favorite: bool) -> bool:
        """Set favorite state idempotently and return the persisted state."""
        with self._lock:
            return self._save_state(self.load(), self._normalize(filename), bool(is_favorite))

    def _save_state(self, favs: set[str], key: str, is_favorite: bool) -> bool:
        if is_favorite:
            favs.add(key)
        else:
            favs.discard(key)
        self.save(favs)
        return is_favorite

    def is_favorite(self, filename: str) -> bool:
        return self._normalize(filename) in self._load_cached()

    def _load_cached(self) -> set[str]:
        """Canonical favorites, reused until favorites.json changes.

        The stamp is the file's own mtime+size, so a write from another process
        invalidates it without any cross-process signalling. ``save()`` clears it
        explicitly for this process.

        The returned set is read-only: callers must not mutate it.
        """
        try:
            stat = self.db_path.stat()
        except OSError:
            return self.load()
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._cache_stamp == stamp:
                return self._cache
        loaded = self.load()
        with self._lock:
            self._cache_stamp, self._cache = stamp, loaded
        return loaded
