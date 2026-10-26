import errno
import os
import stat
import time
from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.services.database import AppDatabase
from tiklocal.services.library import LibraryService
from tiklocal.services.library_index import MediaIndexStore
from tiklocal.services.trash import TRASH_DIR, TrashService


def test_purge_removes_a_read_only_file_and_its_manifest(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    (media_root / 'readonly.mp4').write_bytes(b'video')
    library = LibraryService(media_root)
    trash = TrashService(library)

    entry = trash.move('readonly.mp4')
    folder = media_root / TRASH_DIR / entry['id'].split('.', 1)[1]
    payload = folder / 'readonly.mp4'
    os.chmod(payload, stat.S_IREAD)

    assert trash.purge(entry['id']) == '@default/readonly.mp4'
    assert trash.entries() == []
    assert not folder.exists(), 'a read-only payload used to leave an unlisted, unexpired folder'


def test_unreadable_subfolder_keeps_its_rows_in_the_index(tmp_path, monkeypatch):
    media_root = tmp_path / 'media'
    (media_root / 'a').mkdir(parents=True)
    (media_root / 'b').mkdir()
    (media_root / 'a' / 'one.mp4').write_bytes(b'one')
    (media_root / 'b' / 'two.mp4').write_bytes(b'two')

    database = AppDatabase(tmp_path / 'tiklocal.sqlite3')
    config = {'TESTING': True, 'MEDIA_ROOT': media_root, 'APP_DATABASE': database}
    first = create_app(config)
    assert {item['name'] for item in MediaIndexStore(database).records()} == {
        '@default/a/one.mp4', '@default/b/two.mp4',
    }
    assert first.extensions['media_index_sync']['deleted'] == 0

    real_walk = os.walk
    locked = media_root / 'b'

    def walk_with_locked_folder(root, onerror=None, **kwargs):
        if Path(root) == locked:
            if onerror is not None:
                onerror(PermissionError(errno.EACCES, 'Permission denied', str(root)))
            return
        if Path(root) == media_root:
            # Mirror os.walk's top-down recursion so the walker visits our wrapper
            # for the locked child instead of scandir'ing it itself.
            yield str(root), ['a', 'b'], []
            for name in ('a', 'b'):
                yield from walk_with_locked_folder(Path(root) / name, onerror=onerror, **kwargs)
            return
        for item in real_walk(root, onerror=onerror, **kwargs):
            yield item

    monkeypatch.setattr('tiklocal.services.library.os.walk', walk_with_locked_folder)
    app = create_app(config)

    sync = app.extensions['media_index_sync']
    assert sync['deleted'] == 0, 'a folder we cannot read is not a folder the user deleted'
    assert sync['skipped_prefixes'] == ['@default/b/']
    assert {item['name'] for item in MediaIndexStore(database).records()} == {
        '@default/a/one.mp4', '@default/b/two.mp4',
    }


def test_crashed_job_reports_a_reason(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir()
    media_root = tmp_path / 'media'
    media_root.mkdir()
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    monkeypatch.setattr('tiklocal.services.downloader.probe_binary', lambda engine: (True, 'fake'))
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    manager = app.extensions['download_manager']
    manager.start()
    try:
        def boom(self, job_id):
            raise TimeoutError()  # str(TimeoutError()) == ''

        monkeypatch.setattr(type(manager), '_execute_download', boom)
        job = manager.enqueue('https://example.com/a.mp4')
        deadline = time.monotonic() + 10
        final = None
        while time.monotonic() < deadline:
            final = manager.get_job(job['id'])
            if final and final['status'] in {'failed', 'success'}:
                break
            time.sleep(0.02)
        assert final is not None and final['status'] == 'failed', final
        assert final['error_message'], 'a failed job without a message is invisible to the user'
        assert 'TimeoutError' in final['error_message']
    finally:
        manager.close()
