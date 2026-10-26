import hashlib
import io
import math
import os
import re
import tempfile
import threading
import time
import subprocess as sp
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from tiklocal.paths import get_thumbnails_dir
from tiklocal.services.download_tools import resolve_tool
from tiklocal.services.library import LibraryService, AUDIO_EXTENSIONS, IMAGE_EXTENSIONS

#: How long a video frame extraction may take before the request gives up.
FFMPEG_TIMEOUT_SEC = 30


def ffmpeg_argv() -> list[str]:
    """Resolve ffmpeg once per call: PATH, venv console script, then the bundled build."""
    return resolve_tool('ffmpeg') or ['ffmpeg']


def ffprobe_argv() -> list[str] | None:
    return resolve_tool('ffprobe')


def banner_duration(path: Path) -> float | None:
    """Read a media duration from ffmpeg's banner; works when ffprobe is absent."""
    try:
        completed = sp.run([*ffmpeg_argv(), '-hide_banner', '-i', str(path)],
                           stdout=sp.DEVNULL, stderr=sp.PIPE, timeout=10)
    except (OSError, sp.SubprocessError):
        return None
    stderr = completed.stderr or b''
    if isinstance(stderr, str):
        stderr = stderr.encode('utf-8', 'ignore')
    match = re.search(r'Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)',
                      stderr.decode('utf-8', 'ignore'))
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return duration if duration > 0 else None


def _replace_with_retry(source: Path, target: Path, attempts: int = 3) -> bool:
    """Publish *source* over *target*, retrying a briefly locked target.

    On Windows a cache file is locked while a response streams it, so replacing it
    fails with WinError 5/32 for a moment; the old cache stays valid meanwhile.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(0.05)
    return False


class ThumbnailService:
    #: Requests for one URI arrive in bursts (grid, feed, detail). A small lock pool
    #: keeps a single ffmpeg run per URI without holding a lock per cache file.
    _LOCK_POOL_SIZE = 8
    #: How long a failed generation is remembered before the file is retried.
    FAILURE_TTL_SEC = 6 * 3600

    def __init__(self, media_root: Path, library_service=None):
        self.media_root = media_root
        self.library = library_service or LibraryService(media_root)
        self.thumb_dir = get_thumbnails_dir()
        self._locks = [threading.Lock() for _ in range(self._LOCK_POOL_SIZE)]
        # The same relative path in a different library is a different picture, and
        # the mtime freshness check cannot tell them apart (a copied file keeps its
        # old mtime). Keying the cache by root prevents cross-library thumbnails.
        self._root_key = hashlib.sha1(str(Path(media_root).resolve()).lower().encode('utf-8')).hexdigest()[:16]
        self.placeholder = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDAT\x08\x99c\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\x93\x8a\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    def cache_path(self, uri: str) -> Path:
        canonical = self.library.canonicalize_uri(uri)
        return self._path_for_key(canonical)

    def _path_for_key(self, key: str) -> Path:
        digest = hashlib.sha1(f'{self._root_key}|{key}'.encode('utf-8')).hexdigest()
        return self.thumb_dir / (digest + '.jpg')

    def _legacy_path_for_key(self, key: str) -> Path:
        """Pre-root-key cache layout: plain sha1(canonical uri)."""
        return self.thumb_dir / (hashlib.sha1(key.encode('utf-8')).hexdigest() + '.jpg')

    def _cached_path_for_key(self, key: str) -> Path | None:
        canonical = self._path_for_key(key)
        legacy = self._legacy_path_for_key(key)
        for candidate in (canonical, legacy):
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    break
            except OSError:
                continue
        else:
            return None
        if candidate == legacy:
            # Adopt a pre-root-key cache file instead of leaving a duplicate that
            # would show up in the settings cache count and never be cleaned.
            try:
                legacy.replace(canonical)
                return canonical
            except OSError:
                return legacy
        return canonical

    def cached_thumbnail(self, uri: str) -> Path | None:
        """Reuse valid canonical caches and legacy caches belonging to the default source."""
        source_path = self.library.resolve_path(uri)
        if source_path is None:
            return None
        for key in self.library.legacy_candidates(self.library.canonicalize_uri(uri)):
            cached = self._cached_path_for_key(key)
            if cached is None:
                continue
            try:
                if not source_path.exists():
                    # Offline sources may display an existing canonical preview.
                    return cached if key.startswith('@') else None
                if cached.stat().st_mtime_ns >= source_path.stat().st_mtime_ns:
                    return cached
            except OSError:
                continue
        return None

    def get_thumbnail(self, uri: str) -> tuple[Path | bytes, str]:
        """Return a cached or generated thumbnail, falling back to the last usable cache."""
        thumbnail = self.cached_thumbnail(uri)
        if thumbnail:
            return thumbnail, 'image/jpeg'
        if self._failure_is_fresh(uri):
            # A corrupt or still-copying file used to spawn three ffmpeg runs per
            # grid refresh; remember the failure and serve the placeholder instead.
            stale = self._stale_thumbnail(uri)
            return (stale, 'image/jpeg') if stale else (self.placeholder, 'image/png')
        generated = self.generate_thumbnail(uri)
        if generated:
            return generated, 'image/jpeg'
        self._mark_failure(uri)
        stale = self._stale_thumbnail(uri)
        if stale:
            # A locked cache file means the previous preview is still valid enough;
            # degrading the whole grid to a 1x1 placeholder is worse.
            return stale, 'image/jpeg'
        return self.placeholder, 'image/png'

    def _failure_path(self, uri: str) -> Path:
        cache_path = self.cache_path(uri)
        return cache_path.with_name(cache_path.name + '.fail')

    def _failure_is_fresh(self, uri: str) -> bool:
        marker = self._failure_path(uri)
        try:
            marker_stat = marker.stat()
        except OSError:
            return False
        if time.time() - marker_stat.st_mtime > self.FAILURE_TTL_SEC:
            return False
        source_path = self.library.resolve_path(uri)
        if source_path is None:
            return True
        try:
            # The file was replaced (e.g. a copy finished): give it another chance.
            return source_path.stat().st_mtime <= marker_stat.st_mtime
        except OSError:
            return False

    def _mark_failure(self, uri: str) -> None:
        marker = self._failure_path(uri)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(int(time.time())), encoding='utf-8')
        except OSError:
            pass

    def _stale_thumbnail(self, uri: str) -> Path | None:
        for key in self.library.legacy_candidates(self.library.canonicalize_uri(uri)):
            cached = self._cached_path_for_key(key)
            if cached is not None:
                return cached
        return None

    def get_radio_artwork(self, uri: str) -> tuple[Path | bytes, str]:
        if uri:
            path, mimetype = self.get_thumbnail(uri)
            if not isinstance(path, bytes):
                return path, mimetype
        return radio_artwork_bytes(uri or 'radio'), 'image/png'

    def cache_stats(self) -> dict:
        count = 0
        size = 0
        for path in self.thumb_dir.glob('*.jpg'):
            try:
                size += path.stat().st_size
            except OSError:
                # Unlinked by a concurrent clear_cache() between glob and stat.
                continue
            count += 1
        return {'count': count, 'size_mb': round(size / (1024 * 1024), 2)}

    def clear_cache(self) -> dict:
        deleted_count = 0
        freed_bytes = 0
        failed_count = 0
        for path in self.thumb_dir.glob('*.jpg'):
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError:
                # Still open in a running response (Windows): report it instead of
                # claiming the space back while the file is still on disk.
                failed_count += 1
                continue
            deleted_count += 1
            freed_bytes += size
        for marker in self.thumb_dir.glob('*.jpg.fail'):
            # Clearing the cache is the user's way of saying "try those files again".
            try:
                marker.unlink()
            except OSError:
                pass
        return {
            'deleted_count': deleted_count,
            'freed_mb': round(freed_bytes / (1024 * 1024), 2),
            'failed_count': failed_count,
        }

    def generate_thumbnail(
        self, uri: str, *, timestamp: float | None = None, auto_timestamp: bool = False,
        overwrite: bool = False,
    ) -> Path | None:
        source = self.library.resolve_path(uri)
        if source is None or not source.is_file():
            return None
        output = self.cache_path(uri)
        # A failed or concurrent generation must not publish a partial cache file.
        with self._locks[hash(str(output)) % self._LOCK_POOL_SIZE]:
            # Re-check under the lock so a burst of requests extracts one frame; a
            # cache older than its source is stale and must be rebuilt.
            if not overwrite and output.is_file() and output.stat().st_size > 0:
                try:
                    if output.stat().st_mtime_ns >= source.stat().st_mtime_ns:
                        return output
                except OSError:
                    pass
            try:
                with tempfile.TemporaryDirectory(dir=self.thumb_dir) as directory:
                    temporary = Path(directory) / 'thumbnail.jpg'
                    if self._generate(source, temporary, timestamp, auto_timestamp=auto_timestamp):
                        if _replace_with_retry(temporary, output):
                            return output
            except OSError:
                return None
        return None

    def delete_thumbnail(self, uri: str) -> bool:
        removed = False
        for key in self.library.legacy_candidates(self.library.canonicalize_uri(uri)):
            for path in (self._path_for_key(key), self._legacy_path_for_key(key)):
                try:
                    path.unlink()
                    removed = True
                except OSError:
                    continue
        return removed

    def sample_video_frames(self, uri: str, directory: Path) -> list[Path]:
        """Extract up to three temporary frames without changing the library cover; unreadable ones are skipped."""
        source = self.library.resolve_path(uri)
        if not source or not source.is_file():
            raise OSError('Video not found.')
        duration = self._probe_duration(source)
        if not duration:
            raise OSError('Could not read the video duration.')
        timestamps = [0.0] if duration < 1.0 else [duration * fraction for fraction in (0.2, 0.5, 0.8)]
        frames = []
        for index, timestamp in enumerate(timestamps):
            frame = directory / f'frame-{index}.jpg'
            if self._generate(source, frame, timestamp, auto_timestamp=False):
                frames.append(frame)
        if not frames:
            raise OSError('Could not extract a video frame.')
        return frames

    @staticmethod
    def _probe_duration(path: Path) -> float | None:
        probe = ffprobe_argv()
        if probe:
            try:
                output = sp.check_output([
                    *probe, '-v', 'error', '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
                ], stderr=sp.DEVNULL, timeout=10)
                duration = float(output.decode().strip())
                return duration if math.isfinite(duration) and duration > 0 else None
            except (OSError, sp.SubprocessError, ValueError):
                pass
        # imageio-ffmpeg ships ffmpeg without ffprobe: read the duration from its banner.
        return banner_duration(path)

    def _generate(self, video_path: Path, output_path: Path, timestamp: float | None, *, auto_timestamp: bool) -> bool:
        suffix = video_path.suffix.lower()

        if suffix in IMAGE_EXTENSIONS:
            return self._generate_image(video_path, output_path)

        # Audio: extract embedded cover art
        if suffix in AUDIO_EXTENSIONS:
            cmd = [*ffmpeg_argv(), '-i', str(video_path), '-an', '-vframes', '1', str(output_path), '-y']
            try:
                result = sp.run(cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=FFMPEG_TIMEOUT_SEC)
                if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    return True
            except (OSError, sp.SubprocessError):
                pass
            return False

        candidates = [5.0, 1.0, 0.1]
        if timestamp is not None:
            candidates = [timestamp]
        elif auto_timestamp:
            duration = self._probe_duration(video_path)
            if duration and duration > 1:
                candidates.insert(0, max(1.0, min(duration - 1.0, duration * 0.2)))

        for t in candidates:
            cmd = [
                *ffmpeg_argv(), '-y',
                '-ss', str(max(0.0, float(t))),
                '-i', str(video_path),
                '-frames:v', '1',
                '-vf', 'scale=-1:360:force_original_aspect_ratio=decrease',
                '-q:v', '3',
                str(output_path)
            ]
            try:
                result = sp.run(cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=FFMPEG_TIMEOUT_SEC)
                if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    return True
            except (OSError, sp.SubprocessError):
                continue
        return False

    @staticmethod
    def _generate_image(image_path: Path, output_path: Path) -> bool:
        try:
            with Image.open(image_path) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((640, 640), Image.Resampling.LANCZOS)
                if 'A' in image.getbands():
                    rgba = image.convert('RGBA')
                    background = Image.new('RGB', rgba.size, (247, 246, 242))
                    background.paste(rgba, mask=rgba.getchannel('A'))
                    image = background
                else:
                    image = image.convert('RGB')
                image.save(output_path, 'JPEG', quality=84, optimize=True, progressive=True)
            return output_path.exists() and output_path.stat().st_size > 0
        except Exception:
            return False


def radio_artwork_bytes(uri: str) -> bytes:
    palettes = [
        ("#466b61", "#a88756", "#d7d2c4"),
        ("#5c6750", "#b18462", "#d8d3c8"),
        ("#57707a", "#9b8257", "#d2d5ce"),
        ("#675f82", "#9b8b5b", "#d7d1c0"),
        ("#72634e", "#5f8174", "#d8d4c7"),
        ("#4f6f7e", "#a36f5d", "#d5d0c4"),
    ]
    palette = palettes[sum(uri.encode("utf-8", errors="ignore")) % len(palettes)]
    size = 512
    image = Image.new("RGB", (size, size), palette[2])
    draw = ImageDraw.Draw(image, "RGBA")

    for radius in range(size // 2, 24, -8):
        index = (radius // 8) % 2
        color = palette[index]
        alpha = 18 if index else 26
        inset = size // 2 - radius
        draw.ellipse(
            (inset, inset, size - inset, size - inset),
            fill=color + f"{alpha:02x}",
        )

    draw.ellipse((42, 42, size - 42, size - 42), outline=(36, 36, 31, 38), width=2)
    draw.ellipse((112, 112, size - 112, size - 112), outline=(70, 107, 97, 34), width=2)
    draw.ellipse(
        (182, 182, size - 182, size - 182),
        fill=palette[0],
        outline=(255, 255, 255, 56),
        width=2,
    )
    draw.ellipse(
        (220, 220, size - 220, size - 220),
        fill=palette[2],
        outline=(36, 36, 31, 30),
        width=1,
    )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
