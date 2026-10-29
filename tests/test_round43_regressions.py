"""Regressions for audit round 43: upgrades must not silently drop old data."""
import json

import pytest

from tiklocal.services import author_profiles
from tiklocal.services.downloader import _salvage_download_config
from tiklocal.services.json_storage import StoreUnreadableError, read_json_file
from tiklocal.services.url_list import normalize_url, parse_text


def test_a_utf8_bom_on_the_first_line_does_not_drop_the_url():
    # Notepad and PowerShell's ``Out-File`` write a BOM; ``str.strip()`` does not
    # treat U+FEFF as whitespace, so the first line parsed as scheme "\ufeffhttps"
    # and the URL vanished for good on the next save.
    assert normalize_url('\ufeffhttps://example.com/a') == 'https://example.com/a'
    assert parse_text('\ufeffhttps://example.com/a\nhttps://example.com/b') == [
        'https://example.com/a',
        'https://example.com/b',
    ]


def test_a_store_from_a_newer_build_is_refused_instead_of_rewritten(tmp_path):
    # Two versions sharing one data directory: reading the newer file as far as this
    # build understands it and then saving the old shape back dropped every field the
    # newer version had added.
    path = tmp_path / 'media_sources.json'
    original = json.dumps({'version': 99, 'sources': [{'root': 'D:/media'}]}, indent=2)
    path.write_text(original, encoding='utf-8')

    with pytest.raises(StoreUnreadableError):
        read_json_file(path, None, expected_type=dict, max_version=1)

    assert path.read_text(encoding='utf-8') == original
    assert not list(tmp_path.glob('*.corrupt*'))


def test_one_bad_download_field_does_not_reset_the_rest():
    # ``validate_download_config`` answers for the whole payload, so a hand-edited
    # ``max_concurrent`` used to make the settings page show - and the next save
    # persist - every default, losing the cookie dir and the archive path with it.
    salvaged = _salvage_download_config({
        'max_concurrent': 'abc',
        'cookie_dir': 'D:/cookies',
        'gallery_archive_file': 'D:/archive.db',
    })

    assert 'max_concurrent' not in salvaged
    assert salvaged['cookie_dir'] == 'D:/cookies'
    assert salvaged['gallery_archive_file'] == 'D:/archive.db'


def test_two_non_ascii_source_ids_keep_separate_author_profiles(monkeypatch, tmp_path):
    # Both ids sanitised to "_", so the second sync overwrote the first author's
    # nickname and avatar and the page showed the wrong name.
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'instance'))
    assert author_profiles._safe_key('旅行') != author_profiles._safe_key('家人')


def test_an_author_profile_saved_under_the_old_key_is_still_found(monkeypatch, tmp_path):
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'instance'))
    legacy = author_profiles.authors_dir() / '_.json'
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({'nickname': '旧名字'}), encoding='utf-8')

    assert author_profiles.existing_profile_path('旅行') == legacy
    assert author_profiles.load_profile('旅行')['nickname'] == '旧名字'


def test_a_source_that_scans_empty_but_still_has_rows_keeps_them(tmp_path):
    from tiklocal.app import create_app
    from tiklocal.services.database import AppDatabase
    from tiklocal.services.library_index import MediaIndexStore

    root = tmp_path / 'photos'
    root.mkdir()
    (root / 'first.jpg').write_bytes(b'image')
    database = AppDatabase(tmp_path / 'tiklocal.sqlite3')
    config = {
        'TESTING': True,
        'MEDIA_ROOT': root,
        'APP_DATABASE': database,
        'MEDIA_SOURCES': [{'id': 'default', 'name': 'Default', 'path': str(root)}],
    }
    create_app(config)
    assert MediaIndexStore(database).source_row_count('default') == 1

    # Every media file is gone but the folder is not empty: a filter (cloud
    # placeholders, junctions, an ACL) swallowed the tree. Deleting the rows here
    # would also sweep the resume points and watch history for good.
    (root / 'first.jpg').unlink()
    (root / 'notes.txt').write_text('not media', encoding='utf-8')
    create_app(config)

    assert MediaIndexStore(database).source_row_count('default') == 1
