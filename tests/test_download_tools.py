import time
from pathlib import Path

import pytest

from tiklocal.app import create_app
from tiklocal.services.download_tools import YT_DLP_OUTPUT_TEMPLATE, yt_dlp_command


def test_output_template_stays_relative_so_the_temp_folder_is_used(tmp_path: Path) -> None:
    media_root = tmp_path / 'media'
    temp_dir = media_root / '.tiklocal-download-tmp' / 'job-1'
    cmd = yt_dlp_command(['yt-dlp'], media_root, 'https://example.com/v.mp4', allow_playlist=False,
                         cookie_path=None, temp_dir=temp_dir)

    output_index = cmd.index('-o')
    template = cmd[output_index + 1]
    assert template == YT_DLP_OUTPUT_TEMPLATE
    assert not Path(template).is_absolute(), 'an absolute -o makes yt-dlp ignore --paths temp:'
    assert f'home:{media_root}' in cmd
    assert f'temp:{temp_dir}' in cmd
    assert '--no-playlist' in cmd


def test_output_template_bounds_title_like_fields() -> None:
    # display_id is the page title for several extractors; unbounded it blows
    # MAX_PATH and --write-info-json fails before any media is written.
    assert '%(display_id|na).80B' in YT_DLP_OUTPUT_TEMPLATE
    assert '%(id|na).80B' in YT_DLP_OUTPUT_TEMPLATE


def test_no_temp_dir_still_pins_the_home_path(tmp_path: Path) -> None:
    media_root = tmp_path / 'media'
    cmd = yt_dlp_command(['yt-dlp'], media_root, 'https://example.com/v.mp4', allow_playlist=True,
                         cookie_path=None)
    assert f'home:{media_root}' in cmd
    assert not any(part.startswith('temp:') for part in cmd)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    instance = tmp_path / 'instance'
    instance.mkdir(parents=True)
    media_root = tmp_path / 'media'
    media_root.mkdir(parents=True)
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('MEDIA_ROOT', str(media_root))
    monkeypatch.setattr('tiklocal.services.downloader.probe_binary', lambda engine: (True, 'fake'))
    app = create_app({'TESTING': True, 'MEDIA_ROOT': media_root})
    download_manager = app.extensions['download_manager']
    download_manager.start()
    try:
        yield download_manager
    finally:
        download_manager.close()


def test_playlist_records_every_destination(manager, monkeypatch, tmp_path):
    media_root = Path(manager.media_root)
    destinations = [media_root / f'item{index}.mp4' for index in range(3)]
    seen_commands = []

    monkeypatch.setattr('tiklocal.services.downloader.resolve_tool', lambda name: ['yt-dlp'])

    def fake_run_tool(self, job_id, cmd, on_line):
        seen_commands.append(cmd)
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b'fake-media')
            on_line(f'[download] Destination: {destination}')
        return 0, ''

    def direct(self, job_id):
        return self._execute_download_yt_dlp(job_id)

    monkeypatch.setattr(type(manager), '_run_tool', fake_run_tool)
    monkeypatch.setattr(type(manager), '_execute_download', direct)
    job = manager.enqueue('https://example.com/playlist')

    deadline = time.monotonic() + 10
    final = None
    while time.monotonic() < deadline:
        final = manager.get_job(job['id'])
        if final and final['status'] in {'success', 'failed'}:
            break
        time.sleep(0.02)

    assert final is not None and final['status'] == 'success', final
    assert final['file_count'] == 3, final
    assert final['output_files_rel'] == ['@default/item0.mp4', '@default/item1.mp4', '@default/item2.mp4']
    # The engine must have received the job-private cookie copy, not the store.
    assert seen_commands and '-P' in seen_commands[0]
