import json
import re
import subprocess as sp

from PIL import Image

from tiklocal.services.download_tools import resolve_tool
from tiklocal.services.thumbnail import ffmpeg_argv, ffprobe_argv


def probe_tool_available() -> bool:
    """True when a bundled or system tool can measure a video.

    The imageio-ffmpeg wheel ships ffmpeg only, so a default install has no
    ffprobe; the caller uses this to retry later instead of freezing NULL sizes.
    """
    return bool(ffprobe_argv() or resolve_tool('ffmpeg'))


def probe_media_dims(library_service, name: str, media_type: str) -> tuple[int | None, int | None]:
    target = library_service.resolve_path(name)
    if not target or not target.exists():
        return None, None

    if media_type == 'image':
        try:
            with Image.open(target) as img:
                width, height = img.size
            if int(width) > 0 and int(height) > 0:
                return int(width), int(height)
        except Exception:
            return None, None
        return None, None

    probe = ffprobe_argv()
    if probe:
        try:
            cmd = [
                *probe,
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'json',
                str(target),
            ]
            proc = sp.run(cmd, capture_output=True, text=True, timeout=8)
            if proc.returncode == 0:
                payload = json.loads(proc.stdout or '{}')
                streams = payload.get('streams') or []
                stream = streams[0] if streams and isinstance(streams[0], dict) else {}
                width = int(stream.get('width') or 0)
                height = int(stream.get('height') or 0)
                if width > 0 and height > 0:
                    return width, height
        except Exception:
            pass
    return _banner_dims(target)


def _banner_dims(target) -> tuple[int | None, int | None]:
    """Read the frame size from ffmpeg's stream banner (no ffprobe required)."""
    try:
        completed = sp.run(
            [*ffmpeg_argv(), '-hide_banner', '-i', str(target)],
            stdout=sp.DEVNULL, stderr=sp.PIPE, timeout=8,
        )
    except (OSError, sp.SubprocessError):
        return None, None
    stderr = completed.stderr or b''
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', 'ignore')
    for line in str(stderr).splitlines():
        if 'Video:' not in line:
            continue
        match = re.search(r'(\d{2,5})x(\d{2,5})', line)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            if width > 0 and height > 0:
                return width, height
    return None, None
