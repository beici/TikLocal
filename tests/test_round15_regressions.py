"""Regressions for the round-15 findings (time boundaries, overlapping sources)."""

import datetime
import logging
import os

from tiklocal.app import create_app
from tiklocal.services import collections, download_sources
from tiklocal.services.library import LibraryService, MediaSource
from tiklocal.services.library_index import discover_capture_time


def _build_library(tmp_path):
    media_root = tmp_path / 'media'
    media_root.mkdir()
    for name in ('good1.mp4', 'good2.mp4'):
        (media_root / name).write_bytes(b'\x00' * 64)
    return media_root


def test_pre_epoch_mtime_does_not_freeze_the_whole_source(tmp_path, monkeypatch):
    """One file with mtime before 1970 must not abort the source scan.

    ``datetime.fromtimestamp(-1)`` raises OSError on Windows, which used to escape
    discover_capture_time, be swallowed as "source unavailable" and leave every new
    file out of the index while /api/library/sync still answered success: true.
    """
    media_root = _build_library(tmp_path)
    ancient = media_root / 'ancient.mp4'
    ancient.write_bytes(b'\x00' * 64)
    os.utime(ancient, (-1.0, -1.0))
    instance = tmp_path / 'instance'
    instance.mkdir()
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))

    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    client = app.test_client()
    response = client.post('/api/library/sync')
    assert response.status_code == 200
    payload = response.get_json()['data']
    assert not payload.get('unavailable_sources'), payload
    assert not payload.get('source_errors'), payload
    assert payload['indexed'] >= 3

    items = client.get('/api/library/items?limit=50').get_json()['data']
    assert items['total'] >= 3
    assert any('ancient.mp4' in item['name'] for item in items['items'])


def test_future_filename_date_falls_back_to_the_filesystem(tmp_path):
    """A file name claiming 2031 must not pin a phantom year to the timeline."""
    media_root = tmp_path / 'media'
    media_root.mkdir()
    future = media_root / '2031-05-05 10.00.00-future.mp4'
    future.write_bytes(b'\x00' * 16)
    mtime = datetime.datetime(2024, 2, 2, 9, 0, 0).timestamp()
    os.utime(future, (mtime, mtime))

    payload = discover_capture_time(future, 'video', mtime)
    assert payload['time_source'] == 'filesystem_mtime'
    assert payload['captured_local_date'].startswith('2024-02-02')


def test_utc_helpers_keep_the_z_suffix_without_utcnow():
    for helper in (collections._utc_now_iso, download_sources.utc_now_iso):
        value = helper()
        assert value.endswith('Z'), value
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == datetime.timedelta(0)


def test_overlapping_sources_are_named_in_a_warning(tmp_path, caplog):
    root = tmp_path / 'media'
    (root / 'sub').mkdir(parents=True)
    sources = [
        MediaSource(id='alpha', name='alpha', path=root),
        MediaSource(id='beta', name='beta', path=root / 'sub'),
    ]
    with caplog.at_level(logging.WARNING, logger='tiklocal.services.library'):
        service = LibraryService(media_root=root, media_sources=sources)
    assert service.sources_by_id['beta'] is not None
    assert 'beta' in caplog.text and 'alpha' in caplog.text


def test_missing_nested_media_root_is_created(tmp_path, monkeypatch):
    """`tiklocal D:\\media\\2026\\hits` used to serve an empty library silently."""
    instance = tmp_path / 'instance'
    instance.mkdir()
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    media_root = tmp_path / 'library' / '2026' / 'hits'

    create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    assert media_root.is_dir()
