"""Download URL list: a plain-text queue that can be imported, exported and synced.

The list lives in a text file so it stays readable, diffable and copyable to a
server-side path (for example a TikTokDownloader ``user-url.txt`` file).  Every
line is one URL; blank lines and ``#`` comments are ignored.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import shutil
import threading
from pathlib import Path
from urllib.parse import urlparse

from tiklocal.paths import get_download_urls_config_path, get_download_urls_path
from tiklocal.services.json_storage import StoreUnreadableError, write_json_atomic

logger = logging.getLogger(__name__)

#: Two tabs (or a double submit) hit add/clear/pull on separate waitress threads.
#: Without this the read-modify-write below silently drops rows, and both writers
#: fight over one fixed ``.tmp`` name (WinError 32 -> HTTP 500).
_LIST_LOCK = threading.RLock()
# `+=` is not atomic: two threads could read the same value, build the same
# temporary name, and the loser's os.replace() would fail with FileNotFoundError.
_TEMP_COUNTER = itertools.count(1)

STORE_FILENAME = 'download-urls.txt'
CONFIG_FILENAME = 'download-urls.json'
MAX_URL_LENGTH = 2048
MAX_LINES = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
# The sync target is a plain URL list shared with an external downloader. Writing
# app state or another program's config there would corrupt it, and a mistyped
# path is easy, so the obviously dangerous targets are refused up front.
FORBIDDEN_SUFFIXES = frozenset({'.json', '.db', '.sqlite', '.sqlite3', '.yaml', '.yml', '.py', '.ini', '.toml'})


def normalize_url(raw: str) -> str:
    """Return a cleaned URL, or '' when the line is not a usable http(s) URL."""
    text = str(raw or '').strip()
    if not text or text.startswith('#'):
        return ''
    if len(text) > MAX_URL_LENGTH:
        return ''
    try:
        parsed = urlparse(text)
    except ValueError:
        # urlparse() raises on a malformed IPv6 literal. One such line in a
        # hand-edited download-urls.txt used to make the whole queue unreadable:
        # the error escaped through lines() and the download page answered 500.
        return ''
    if parsed.scheme.lower() not in ('http', 'https'):
        return ''
    if not parsed.netloc:
        return ''
    return text


def parse_text(text: str) -> list[str]:
    """Parse a block of text into de-duplicated URLs, keeping the given order."""
    return parse_text_limited(text)[0]


def parse_text_limited(text: str) -> tuple[list[str], bool]:
    """Parse text and report whether the line cap dropped anything.

    Silently truncating a paste of 6000 URLs made the import look successful
    while the tail vanished, so callers that write the list surface the flag.
    """
    lines: list[str] = []
    seen: set[str] = set()
    truncated = False
    for raw in str(text or '').splitlines():
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        if len(lines) >= MAX_LINES:
            truncated = True
            break
        seen.add(url)
        lines.append(url)
    return lines, truncated


def format_text(lines: list[str]) -> str:
    """Render URLs as a text file body (trailing newline keeps tools happy)."""
    body = '\n'.join(lines)
    return f'{body}\n' if body else ''


def default_store_path() -> Path:
    return get_download_urls_path()


def default_config_path() -> Path:
    return get_download_urls_config_path()


class UrlListStore:
    """Text-backed URL list plus the server path it syncs with."""

    def __init__(self, store_path: Path | str | None = None, config_path: Path | str | None = None):
        self.store_path = Path(store_path) if store_path else default_store_path()
        self.config_path = Path(config_path) if config_path else default_config_path()

    # -- storage ---------------------------------------------------------
    def lines(self) -> list[str]:
        try:
            # errors='replace': Notepad on a Chinese Windows saves this file as GBK,
            # and a strict decode raised UnicodeDecodeError (a ValueError the callers
            # below do not catch) so the whole download page answered 500. pull()
            # already tolerated bad bytes; the local read did not.
            text = self.store_path.read_text(encoding='utf-8', errors='replace')
        except FileNotFoundError:
            return []
        except OSError as error:
            # Returning [] here would let the next save publish an empty list over
            # a queue that is merely unreadable (ACL deny, EFS, AV lock).
            raise StoreUnreadableError(f'{self.store_path.name} exists but cannot be read.') from error
        return parse_text(text)

    def _write(self, lines: list[str], *, backup: bool = False) -> list[str]:
        cleaned, truncated = parse_text_limited('\n'.join(lines))
        if truncated:
            raise ValueError(f'Too many URLs: the list holds at most {MAX_LINES} entries.')
        if backup and self.store_path.exists():
            # Bulk overwrites (import, pull) used to be unrecoverable: a stale or
            # emptied server file silently wiped a hand-written queue. Copy rather
            # than rename: renaming left a window in which every reader saw "no
            # file" and the download page rendered an empty queue.
            try:
                shutil.copyfile(
                    self.store_path,
                    self.store_path.with_suffix(self.store_path.suffix + '.bak'),
                )
            except OSError:
                pass
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        # A fixed ``.tmp`` name lets concurrent writers replace each other's
        # half-written file: use a unique name per write. `tempfile` is avoided
        # because a write-denied directory makes it spin through TMP_MAX names.
        tmp_name = self.store_path.with_name(
            f'{self.store_path.name}.{os.getpid()}.{next(_TEMP_COUNTER)}.tmp')
        try:
            tmp_name.write_text(format_text(cleaned), encoding='utf-8', newline='\n')
            os.replace(tmp_name, self.store_path)
        except BaseException:
            try:
                tmp_name.unlink()
            except OSError:
                pass
            raise
        return cleaned

    def replace(self, lines: list[str]) -> list[str]:
        with _LIST_LOCK:
            return self._write(lines, backup=True)

    def remove_many(self, lines: list[str]) -> list[str]:
        """Remove the given URLs, leaving everything else alone.

        The download page used ``clear`` after queueing and ``replace(failed)`` after a
        partial failure. Both are whole-table operations, so a second tab (or a phone)
        that had just imported twenty links lost them to the first tab's cleanup.
        """
        with _LIST_LOCK:
            doomed = {normalize_url(raw) for raw in lines if isinstance(raw, str)}
            doomed.discard('')
            if not doomed:
                return list(self.lines())
            kept = [url for url in self.lines() if url not in doomed]
            return self._write(kept)

    def add(self, lines: list[str]) -> list[str]:
        with _LIST_LOCK:
            merged = list(self.lines())
            for url in parse_text('\n'.join(lines)):
                if url not in merged:
                    merged.append(url)
            if len(merged) > MAX_LINES:
                raise ValueError(f'Too many URLs: the list holds at most {MAX_LINES} entries.')
            return self._write(merged)

    def add_reporting(self, lines: list[str]) -> tuple[list[str], int, int, int]:
        """Add URLs and report what actually happened.

        The API used to answer with the whole list only, so the page announced
        "imported 8" for five new URLs: ``ftp://`` lines and junk were dropped in
        silence and everything past MAX_LINES was cut without a word.
        """
        with _LIST_LOCK:
            merged = list(self.lines())
            seen = set(merged)
            added = 0
            rejected = 0
            truncated = 0
            for raw in lines:
                url = normalize_url(raw) if isinstance(raw, str) else ''
                if not url:
                    rejected += 1
                    continue
                if url in seen:
                    continue
                if len(merged) >= MAX_LINES:
                    truncated += 1
                    continue
                seen.add(url)
                merged.append(url)
                added += 1
            written = self._write(merged)
        return written, added, rejected, truncated

    def remove(self, url: str) -> list[str]:
        # Read and write under one lock: the previous version released the lock
        # between lines() and _write(), so a concurrent add() could be overwritten
        # and the freshly added URL silently lost.
        with _LIST_LOCK:
            target = normalize_url(url)
            return self._write([line for line in self.lines() if line != target])

    def clear(self) -> list[str]:
        with _LIST_LOCK:
            return self._write([])

    # -- server path sync ------------------------------------------------
    def _read_config(self) -> dict:
        try:
            data = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (FileNotFoundError, OSError):
            return {}
        except ValueError:
            logger.warning('Ignoring unreadable URL-list config at %s', self.config_path)
            return {}
        return data if isinstance(data, dict) else {}

    def _write_config(self, data: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.config_path, data)

    def source_path(self) -> str:
        return str(self._read_config().get('source_path') or '')

    def set_source_path(self, value: str) -> str:
        text = str(value or '').strip()
        if text:
            path = Path(text).expanduser()
            if not path.is_absolute():
                raise ValueError('The server path must be absolute.')
            if path.exists() and path.is_dir():
                raise ValueError('The server path must point at a file, not a folder.')
            if path.suffix.lower() in FORBIDDEN_SUFFIXES:
                raise ValueError('The server path must be a plain text list, not a config or database file.')
            if path.name.lower().startswith('auth.'):
                raise ValueError('The server path cannot point at an application state file.')
            text = str(path)
        self._write_config({'source_path': text})
        return text

    def _sync_target(self) -> Path:
        text = self.source_path()
        if not text:
            raise ValueError('No server path configured.')
        return Path(text).expanduser()

    def pull(self) -> dict:
        """Read the server file into the local list."""
        target = self._sync_target()
        # The read and the whole-list replace must be one critical section. Reading
        # outside the lock (a network share can take hundreds of milliseconds) let an
        # add() land in between and then be overwritten by the imported list.
        with _LIST_LOCK:
            try:
                size = target.stat().st_size
            except OSError as exc:
                raise ValueError(f'Unable to read {target}: {exc.strerror or exc}') from exc
            if size > MAX_FILE_BYTES:
                raise ValueError('The server file is too large to import.')
            try:
                text = target.read_text(encoding='utf-8', errors='replace')
            except OSError as exc:
                raise ValueError(f'Unable to read {target}: {exc.strerror or exc}') from exc
            imported = parse_text(text)
            lines = self._write(imported, backup=True)
        return {'direction': 'pull', 'lines': lines, 'path': str(target)}

    def push(self) -> dict:
        with _LIST_LOCK:
            return self._push_locked()

    def _push_locked(self) -> dict:
        """Write the local list to the server file."""
        target = self._sync_target()
        lines = self.lines()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target.with_suffix(target.suffix + '.tmp')
            tmp_path.write_text(format_text(lines), encoding='utf-8')
            os.replace(tmp_path, target)
        except OSError as exc:
            raise ValueError(f'Unable to write {target}: {exc.strerror or exc}') from exc
        return {'direction': 'push', 'lines': lines, 'path': str(target)}

    def snapshot(self) -> dict:
        lines = self.lines()
        source = self.source_path()
        exists = False
        if source:
            try:
                exists = Path(source).expanduser().is_file()
            except OSError:
                exists = False
        return {
            'lines': lines,
            'count': len(lines),
            'store_path': str(self.store_path),
            'source_path': source,
            'source_exists': exists,
        }
