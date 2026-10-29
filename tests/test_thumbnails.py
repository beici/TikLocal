import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from tiklocal.app import create_app
from tiklocal.thumbs import clean_thumbnails, generate_thumbnails, verify_thumbnails


@pytest.mark.parametrize('legacy_cache', [False, True])
def test_cli_cache_is_reused_by_web_and_isolated_between_sources(tmp_path, monkeypatch, legacy_cache):
    main, extra = tmp_path / 'main', tmp_path / 'extra'
    for root in (main, extra):
        root.mkdir()
        (root / 'same # clip.Mp4').write_bytes(b'video')
    captures = []

    def capture(command, **kwargs):
        name = Path(str(command[0])).name.lower()
        if name.startswith('ffmpeg'):
            if '-ss' in command:
                source = Path(command[command.index('-i') + 1])
                captures.append(source)
                Image.new('RGB', (32, 24), 'red' if source.parent == main else 'blue').save(command[-1])
                return subprocess.CompletedProcess(command, 0, stdout=b'', stderr=b'')
            banner = b'  Duration: 00:00:10.00, start: 0.000000, bitrate: 100 kb/s\n'
            return subprocess.CompletedProcess(command, 0, stdout=b'', stderr=banner)
        return subprocess.CompletedProcess(command, 0, stdout='{}' if kwargs.get('text') else b'10',
                                           stderr=b'')

    monkeypatch.setattr(subprocess, 'run', capture)
    assert generate_thumbnails(main, show_progress=False)['generated'] == 1
    data_dir = tmp_path / 'tiklocal-data'
    cache = next((data_dir / 'thumbnails').glob('*.jpg'))
    expected = cache.read_bytes()
    if legacy_cache:
        legacy = cache.with_name(hashlib.sha1(b'same # clip.Mp4').hexdigest() + '.jpg')
        cache.rename(legacy)
    assert generate_thumbnails(main, show_progress=False)['skipped'] == 1
    assert verify_thumbnails(main)['missing'] == 0
    app = create_app({'TESTING': True, 'MEDIA_ROOT': main, 'MEDIA_SOURCES': [
        {'id': 'extra', 'path': str(extra)},
    ]})
    client = app.test_client()
    primary = client.get('/thumb', query_string={'uri': '@default/same # clip.Mp4'})
    assert primary.status_code == 200
    assert primary.data == expected
    assert len(captures) == 1
    other = client.get('/thumb', query_string={'uri': '@extra/same # clip.Mp4'})
    assert other.mimetype == 'image/jpeg'
    assert other.data != expected
    assert len(captures) == 2
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout=b''))
    assert generate_thumbnails(main, overwrite=True, show_progress=False)['failed'] == 1
    assert client.get('/thumb', query_string={'uri': '@default/same # clip.Mp4'}).data == expected

    mapping_path = data_dir / 'thumbs.json'
    mapping = json.loads(mapping_path.read_text())
    mapping['@extra/same # clip.Mp4'] = {'ts': None}
    mapping_path.write_text(json.dumps(mapping))
    (main / 'same # clip.Mp4').unlink()
    assert clean_thumbnails(main, show_progress=False)['removed'] == 1
    assert client.get('/thumb', query_string={'uri': '@extra/same # clip.Mp4'}).data == other.data
    assert '@extra/same # clip.Mp4' in json.loads(mapping_path.read_text())

    assert client.get('/api/library/stats').get_json()['cache_count'] == 1

    cleared = client.post('/api/cache/clear').get_json()
    assert cleared['success'] and cleared['deleted_count'] == 1
    assert client.get('/api/library/stats').get_json()['cache_count'] == 0
    assert (extra / 'same # clip.Mp4').read_bytes() == b'video'
    assert '@extra/same # clip.Mp4' in json.loads(mapping_path.read_text())


def test_poster_frame_request_actually_regenerates_the_cache(tmp_path, monkeypatch):
    # The detail page's "use this frame as the cover" posts a timestamp, but the
    # fresh-cache short-circuit returned the existing poster and dropped it while
    # the response still reported success.
    (tmp_path / 'clip.mp4').write_bytes(b'video')
    calls = []

    def fake_generate(self, video_path, output_path, timestamp, *, auto_timestamp):
        calls.append(timestamp)
        Image.new('RGB', (8, 8), 'red').save(output_path)
        return True

    monkeypatch.setattr('tiklocal.services.thumbnail.ThumbnailService._generate', fake_generate)
    client = create_app({'TESTING': True, 'MEDIA_ROOT': tmp_path}).test_client()

    # Prime the cache the way the detail page poster does.
    assert client.get('/thumb', query_string={'uri': 'clip.mp4'}).status_code == 200
    assert calls == [None]

    response = client.post('/api/thumbnail/clip.mp4', json={'time': 7.5})
    assert response.status_code == 200
    assert response.get_json()['success'] is True
    assert calls == [None, 7.5]


def test_oversized_images_are_reduced_instead_of_rendering_invisible(tmp_path, monkeypatch):
    # PIL's DecompressionBombError used to make a valid 189 MP photo render as a
    # transparent 1x1 placeholder that looked like a broken tile. The ceiling is
    # raised and the decode is reduced to a bounded pixel count instead.
    from tiklocal.services import thumbnail as thumbnail_module

    monkeypatch.setattr(thumbnail_module, 'REDUCE_ABOVE_PIXELS', 1_000_000)
    source = tmp_path / 'huge.png'
    Image.new('RGB', (3000, 3000), 'teal').save(source)
    output = tmp_path / 'out.jpg'
    previous_limit = Image.MAX_IMAGE_PIXELS

    assert thumbnail_module.ThumbnailService._generate_image(source, output) is True
    # The ceiling is widened only for the header parse: every other Pillow user keeps
    # Pillow's default, so absurd images are still refused there.
    assert Image.MAX_IMAGE_PIXELS == previous_limit
    with Image.open(output) as rendered:
        assert max(rendered.size) <= 640
        assert rendered.size[0] == rendered.size[1]


def test_the_pixel_ceiling_is_widened_only_while_opening(tmp_path, monkeypatch):
    # Save/raise/restore used to run unlocked, so one decode thread restored the default
    # while another was still inside Image.open and a legal 182 MP PNG raised
    # DecompressionBombError. The window is serialised now and scoped to the open.
    from tiklocal.services import thumbnail as thumbnail_module

    real_open = thumbnail_module.Image.open
    observed: list[int | None] = []
    concurrency = {'now': 0, 'max': 0}
    gate = threading.Lock()

    def recording_open(*args, **kwargs):
        with gate:
            concurrency['now'] += 1
            concurrency['max'] = max(concurrency['max'], concurrency['now'])
        try:
            observed.append(thumbnail_module.Image.MAX_IMAGE_PIXELS)
            time.sleep(0.05)  # keep the window open like a slow header parse
            return real_open(*args, **kwargs)
        finally:
            with gate:
                concurrency['now'] -= 1

    default_limit = thumbnail_module.Image.MAX_IMAGE_PIXELS
    monkeypatch.setattr(thumbnail_module.Image, 'open', recording_open)
    sources = []
    for index in range(4):
        path = tmp_path / f'photo-{index}.png'
        Image.new('RGB', (3000, 3000), 'teal').save(path)
        sources.append(path)

    outputs = [tmp_path / f'out-{index}.jpg' for index in range(4)]
    threads = [
        threading.Thread(target=thumbnail_module.ThumbnailService._generate_image, args=(path, output))
        for path, output in zip(sources, outputs)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert [output.exists() for output in outputs] == [True] * 4
    assert observed == [thumbnail_module.MAX_DECODE_PIXELS] * 4
    assert concurrency['max'] == 1
    assert thumbnail_module.Image.MAX_IMAGE_PIXELS == default_limit


def test_images_above_the_decode_ceiling_are_refused(tmp_path, monkeypatch):
    # Past MAX_DECODE_PIXELS the thumbnailer must refuse before decoding, with a log
    # line, instead of attempting a multi-gigabyte buffer.
    from tiklocal.services import thumbnail as thumbnail_module

    monkeypatch.setattr(thumbnail_module, 'MAX_DECODE_PIXELS', 100)
    source = tmp_path / 'scan.png'
    Image.new('RGB', (64, 64), 'navy').save(source)
    output = tmp_path / 'out.jpg'

    assert thumbnail_module.ThumbnailService._generate_image(source, output) is False
    assert not output.exists()


def test_successful_generation_clears_an_earlier_failure_marker(tmp_path, monkeypatch):
    # A file that failed once (still copying) kept the placeholder for the whole
    # TTL even after a retry produced a valid thumbnail.
    from tiklocal.services.thumbnail import ThumbnailService

    (tmp_path / 'photo.jpg').write_bytes(b'not-an-image-yet')
    service = ThumbnailService(tmp_path)
    service.thumb_dir = tmp_path / 'cache'
    service.thumb_dir.mkdir()
    assert service.generate_thumbnail('photo.jpg') is None
    service._mark_failure('photo.jpg')
    assert service._failure_path('photo.jpg').exists()

    Image.new('RGB', (64, 64), 'red').save(tmp_path / 'photo.jpg')
    assert service.generate_thumbnail('photo.jpg') is not None
    assert not service._failure_path('photo.jpg').exists()

def test_a_full_decode_queue_serves_the_placeholder_without_blacklisting_the_file(tmp_path, monkeypatch):
    # /thumb used to park a waitress worker on the generate semaphore for as long as
    # the in-flight decodes took: eight cold tiles were enough to fill all eight
    # threads and every other request (JSON included) queued behind them. It now gives
    # up after a bounded wait and answers with the placeholder, and the file must NOT
    # be marked failed: one unlucky queue would hide a good picture for the whole TTL.
    from tiklocal.services import thumbnail as thumbnail_module
    from tiklocal.services.thumbnail import ThumbnailBusy, ThumbnailService

    (tmp_path / 'clip.mp4').write_bytes(b'video')
    service = ThumbnailService(tmp_path)
    service.thumb_dir = tmp_path / 'cache'
    service.thumb_dir.mkdir()

    slots = thumbnail_module._GENERATE_SLOTS
    taken = 0
    while slots.acquire(timeout=0):
        taken += 1
    monkeypatch.setattr(thumbnail_module, 'GENERATE_SLOT_WAIT_SEC', 0.01)
    try:
        assert taken >= 1
        with pytest.raises(ThumbnailBusy):
            service.generate_thumbnail('clip.mp4')
        path, mimetype = service.get_thumbnail('clip.mp4')
    finally:
        for _ in range(taken):
            slots.release()

    assert path == service.placeholder
    assert mimetype == 'image/png'
    assert not service._failure_path('clip.mp4').exists()


def test_a_full_queue_on_the_image_path_is_not_swallowed_as_a_decode_failure(tmp_path, monkeypatch):
    # _generate_image guards the decode with a broad `except Exception` so one broken
    # file cannot fail the grid; Busy must pass through it, otherwise a full queue
    # would be reported as "this image is corrupt" and blacklisted.
    from tiklocal.services import thumbnail as thumbnail_module
    from tiklocal.services.thumbnail import ThumbnailBusy, ThumbnailService

    Image.new('RGB', (64, 64), 'navy').save(tmp_path / 'photo.png')
    service = ThumbnailService(tmp_path)
    service.thumb_dir = tmp_path / 'cache'
    service.thumb_dir.mkdir()

    slots = thumbnail_module._GENERATE_SLOTS
    taken = 0
    while slots.acquire(timeout=0):
        taken += 1
    monkeypatch.setattr(thumbnail_module, 'GENERATE_SLOT_WAIT_SEC', 0.01)
    try:
        with pytest.raises(ThumbnailBusy):
            service.generate_thumbnail('photo.png')
    finally:
        for _ in range(taken):
            slots.release()

    assert not service._failure_path('photo.png').exists()
