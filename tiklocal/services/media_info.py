import json
import re
import subprocess as sp

from PIL import Image, ImageOps

from tiklocal.services.download_tools import resolve_tool
from tiklocal.services.thumbnail import banner_duration, ffmpeg_argv, ffprobe_argv


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
                # Phone cameras store the sensor's landscape frame plus an EXIF
                # orientation flag, so the raw size is the picture rotated by 90°.
                # The waterfall builds its tiles from this pair, and the thumbnail
                # is generated already transposed, so an unrotated pair stretches
                # every portrait shot.
                width, height = ImageOps.exif_transpose(img).size
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
                '-show_entries', 'stream=width,height:stream_side_data=rotation',
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
                    rotation = _rotation_degrees(proc.stdout or '')
                    return _apply_rotation(width, height, rotation)
        except Exception:
            pass
    return _banner_dims(target)


def _rotation_degrees(text: str) -> int:
    """Return the display rotation ffmpeg/ffprobe reports, normalised to 0/90/180/270."""
    match = re.search(r'rotation of\s+(-?\d+(?:\.\d+)?)\s+degrees', text)
    if not match:
        match = re.search(r'"rotation"\s*:\s*(-?\d+(?:\.\d+)?)', text)
    if not match:
        return 0
    try:
        value = int(round(float(match.group(1))))
    except ValueError:
        return 0
    return value % 360


def _apply_rotation(width: int, height: int, rotation: int) -> tuple[int, int]:
    # ffmpeg autorotates the poster frame, so a 90/270 degree display matrix means the
    # stored frame size is the transpose of what the thumbnail looks like.
    if rotation in (90, 270):
        return height, width
    return width, height


def probe_media_duration(library_service, name: str, media_type: str) -> float | None:
    """Length in seconds for a video or live post, or None when it is unknowable.

    The info sheet asks for this only when the user opens it, so a slow probe is
    acceptable; the ffprobe call is still capped so a stalled file cannot pin the
    request thread, and ffmpeg's banner is the fallback when ffprobe is absent.
    """
    if media_type not in {'video', 'live'}:
        return None
    target = library_service.resolve_path(name)
    if not target or not target.exists() or not target.is_file():
        return None
    probe = ffprobe_argv()
    if probe:
        try:
            output = sp.check_output([
                *probe, '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', str(target),
            ], stderr=sp.DEVNULL, timeout=5)
            value = float(output.decode().strip())
            if value > 0 and value != float('inf') and value == value:
                return value
        except (OSError, sp.SubprocessError, ValueError):
            pass
    return banner_duration(target)


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
    text = str(stderr)
    rotation = _rotation_degrees(text)
    for line in text.splitlines():
        if 'Video:' not in line:
            continue
        match = re.search(r'(\d{2,5})x(\d{2,5})', line)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            if width > 0 and height > 0:
                return _apply_rotation(width, height, rotation)
    return None, None
