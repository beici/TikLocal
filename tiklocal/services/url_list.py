"""Download URL list: a plain-text queue that can be imported, exported and synced.

The list lives in a text file so it stays readable, diffable and copyable to a
server-side path (for example a TikTokDownloader ``user-url.txt`` file).  Every
line is one URL; blank lines and ``#`` comments are ignored.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from tiklocal.paths import get_download_urls_config_path, get_download_urls_path

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
    parsed = urlparse(text)
    if parsed.scheme.lower() not in ('http', 'https'):
        return ''
    if not parsed.netloc:
        return ''
    return text


def parse_text(text: str) -> list[str]:
    """Parse a block of text into de-duplicated URLs, keeping the given order."""
    lines: list[str] = []
    seen: set[str] = set()
    for raw in str(text or '').splitlines():
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        lines.append(url)
        if len(lines) >= MAX_LINES:
            break
    return lines


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
            text = self.store_path.read_text(encoding='utf-8')
        except (FileNotFoundError, OSError):
            return []
        return parse_text(text)

    def _write(self, lines: list[str]) -> list[str]:
        cleaned = parse_text('\n'.join(lines))
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.store_path.with_suffix(self.store_path.suffix + '.tmp')
        tmp_path.write_text(format_text(cleaned), encoding='utf-8')
        os.replace(tmp_path, self.store_path)
        return cleaned

    def replace(self, lines: list[str]) -> list[str]:
        return self._write(lines)

    def add(self, lines: list[str]) -> list[str]:
        current = self.lines()
        merged = list(current)
        for url in parse_text('\n'.join(lines)):
            if url not in merged:
                merged.append(url)
        return self._write(merged)

    def remove(self, url: str) -> list[str]:
        target = normalize_url(url)
        return self._write([line for line in self.lines() if line != target])

    def clear(self) -> list[str]:
        return self._write([])

    # -- server path sync ------------------------------------------------
    def _read_config(self) -> dict:
        try:
            data = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (FileNotFoundError, OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_config(self, data: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

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
        lines = self._write(parse_text(text))
        return {'direction': 'pull', 'lines': lines, 'path': str(target)}

    def push(self) -> dict:
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
