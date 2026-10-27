"""Regressions for the round-14 findings (damaged database pages, stats honesty)."""

import sqlite3

import pytest
from PIL import Image

from tiklocal.app import create_app


def _build_library(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    for index in range(8):
        Image.new('RGB', (16, 16), 'blue').save(media_root / f'photo-{index}.jpg')
    return media_root


def _fill_database(db_path, rows=600):
    connection = sqlite3.connect(db_path)
    try:
        connection.executemany(
            'INSERT INTO media_items (uri, source_id, rel_path, filename, parent_path, '
            'media_type, extension, size_bytes, mtime, discovered_at, indexed_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (f'@default/pad-{index}.jpg', '@default', f'pad-{index}.jpg',
                 f'pad-{index}.jpg', '', 'image', '.jpg', 1024, 1_700_000_000 + index,
                 '2024-01-01', '2024-01-01')
                for index in range(rows)
            ],
        )
        connection.commit()
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        connection.close()


def _corrupt_tail(db_path):
    """Destroy the btree pages after the schema so ``migrate()`` still passes."""
    with open(db_path, 'r+b') as handle:
        handle.seek(0, 2)
        size = handle.tell()
        assert size > 4096 * 2
        handle.seek(size // 2)
        handle.write(b'\xff' * (size - size // 2))


def test_damaged_data_pages_are_quarantined_instead_of_aborting_start_up(tmp_path, monkeypatch):
    media_root = _build_library(tmp_path)
    instance = tmp_path / 'instance'
    instance.mkdir()
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    create_app({'TESTING': True, 'MEDIA_ROOT': media_root})

    db_path = instance / 'tiklocal.sqlite3'
    _fill_database(db_path)
    _corrupt_tail(db_path)

    # Before round 14 this raised "database disk image is malformed" out of the first
    # index sync, after migrate() had already succeeded on the intact schema pages.
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    assert app is not None
    quarantined = list(instance.glob('tiklocal.sqlite3.*.corrupt'))
    assert quarantined, 'the damaged file must be kept next to the rebuilt index'

    client = app.test_client()
    response = client.get('/api/library/stats')
    assert response.status_code == 200
    # The rebuilt index must pick the media folder up again, not stay empty.
    assert response.get_json()['indexed_total'] >= 8


def test_library_stats_does_not_report_zero_for_a_damaged_index(tmp_path, monkeypatch):
    media_root = _build_library(tmp_path)
    instance = tmp_path / 'instance'
    instance.mkdir()
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})

    db_path = instance / 'tiklocal.sqlite3'
    _fill_database(db_path)
    _corrupt_tail(db_path)

    client = app.test_client()
    with pytest.raises(sqlite3.DatabaseError):
        # The exception surfaces through the test client instead of being flattened
        # into {"images": 0}, which used to contradict /library on the same file.
        client.get('/api/library/stats')
