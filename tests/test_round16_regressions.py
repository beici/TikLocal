"""Regressions for the round-16 findings (storage deadlocks, silent data loss)."""

import json
import locale
import time
from pathlib import Path

import pytest

from tiklocal.services import download_tools
from tiklocal.services.json_storage import StoreUnreadableError, read_json_file, write_json_atomic
from tiklocal.services.url_list import UrlListStore


def test_write_denied_directory_fails_fast_and_keeps_the_old_file(tmp_path, monkeypatch):
    """A write-denied store directory must raise, not spin through tempfile names.

    ``tempfile`` treats PermissionError as "name taken" and retries TMP_MAX
    (2**31-1) times, which pinned a waitress thread for days with no error logged.
    """
    store = tmp_path / 'favorites.json'
    store.write_text(json.dumps(['keep-me']), encoding='utf-8')

    def deny_write(self, *args, **kwargs):
        raise PermissionError(13, 'Access is denied')

    monkeypatch.setattr(Path, 'write_text', deny_write)
    started = time.monotonic()
    with pytest.raises(PermissionError):
        write_json_atomic(store, ['replaced'])
    assert time.monotonic() - started < 5.0
    monkeypatch.undo()
    assert json.loads(store.read_text(encoding='utf-8')) == ['keep-me']
    assert list(tmp_path.glob('*.tmp')) == []


def test_unreadable_store_raises_instead_of_answering_empty(tmp_path, monkeypatch):
    """An unreadable store must not look empty: the next save would erase it."""
    store = tmp_path / 'favorites.json'
    store.write_text(json.dumps(['a', 'b']), encoding='utf-8')

    def deny_read(self, *args, **kwargs):
        raise PermissionError(13, 'Access is denied')

    monkeypatch.setattr(Path, 'read_text', deny_read)
    with pytest.raises(StoreUnreadableError):
        read_json_file(store, [])
    monkeypatch.undo()
    assert json.loads(store.read_text(encoding='utf-8')) == ['a', 'b']


def test_missing_store_still_reads_as_the_default(tmp_path):
    assert read_json_file(tmp_path / 'nope.json', []) == []


def test_url_list_unreadable_file_is_not_treated_as_empty(tmp_path, monkeypatch):
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'urls.json')
    store._write(['https://example.com/a'])

    def deny_read(self, *args, **kwargs):
        raise PermissionError(13, 'Access is denied')

    monkeypatch.setattr(Path, 'read_text', deny_read)
    with pytest.raises(StoreUnreadableError):
        store.lines()
    monkeypatch.undo()
    assert store.lines() == ['https://example.com/a']


def test_url_list_write_denied_directory_raises(tmp_path, monkeypatch):
    store = UrlListStore(tmp_path / 'urls.txt', tmp_path / 'urls.json')

    def deny_write(self, *args, **kwargs):
        raise PermissionError(13, 'Access is denied')

    monkeypatch.setattr(Path, 'write_text', deny_write)
    with pytest.raises(PermissionError):
        store._write(['https://example.com/b'])


def test_output_decoding_prefers_the_console_locale(monkeypatch):
    """yt-dlp on zh-CN Windows writes cp936; decoding as utf-8 with replace lost paths."""
    encoding = locale.getpreferredencoding(False)
    raw = '已下载 clip.mp4'.encode(encoding, errors='replace')
    monkeypatch.setattr(download_tools, '_CONSOLE_ENCODINGS', (encoding, 'utf-8'), raising=False)
    from tiklocal.services.downloader import _decode_output

    assert _decode_output(raw) == '已下载 clip.mp4'
    assert _decode_output('plain ascii'.encode()) == 'plain ascii'
