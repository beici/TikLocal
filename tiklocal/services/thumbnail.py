import hashlib
import io
import itertools
import logging
import math
import os
import re
import shutil
import threading
import time
import subprocess as sp
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from tiklocal.paths import get_thumbnails_dir
from tiklocal.services.download_tools import resolve_tool
from tiklocal.services.library import LibraryService, AUDIO_EXTENSIONS, IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

# Thumbnail failures used to be completely silent. These bound the new warning to
# the first few distinct files instead of one line per broken tile.
_FAILURE_LOG_SEEN: set[str] = set()
_FAILURE_LOG_LOCK = threading.Lock()
_FAILURE_LOG_LIMIT = 20

#: How long a video frame extraction may take before the request gives up.
FFMPEG_TIMEOUT_SEC = 30
# One poster may need a duration probe, a colour-transfer probe and up to four
# ffmpeg runs (three timestamps plus the tonemap fallback). Each is bounded, but in
# sequence they pinned a waitress worker for minutes on a network share or a damaged
# container, and only two generation slots exist. Past this budget the poster is not
# worth the worker: give up and let the next request try again.
VIDEO_FRAME_BUDGET_SEC = 35.0

#: How long a second request waits for the in-flight decode of the same file.
GENERATE_WAIT_SEC = 20

#: PIL refuses anything above 2x its MAX_IMAGE_PIXELS (~89 MP by default) with
#: DecompressionBombError. That made a valid 189 MP panorama render as an invisible
#: 1x1 placeholder forever, so the thumbnailer opens under a higher ceiling and bounds
#: the work itself with ``MAX_DECODE_PIXELS``.
#:
#: That ceiling was saved/raised/restored per call, which raced with the other decode
#: threads of the grid: a thread that restored the default while a second one was still
#: inside ``Image.open`` turned a legal 182 MP PNG into a "bomb" (intermittent blank
#: tiles). The save/raise/restore window is now serialised instead, and it is only the
#: open: the expensive decode/encode stays fully parallel. The global value is left at
#: Pillow's default for every other user (captions, embedded metadata, similarity), so
#: they keep refusing absurd images rather than decoding them.
MAX_DECODE_PIXELS = 200_000_000
_PIXEL_LIMIT_LOCK = threading.Lock()

#: Above this many pixels the decode is reduced by an integer factor before the
#: LANCZOS pass. The final target is 640 px, so 48 MP of detail is already far more
#: than visible, while the intermediate RGB buffer stays around 150 MB instead of
#: growing with the source (a 189 MP PNG would otherwise decode to ~567 MB).
REDUCE_ABOVE_PIXELS = 48_000_000

#: Bumped whenever the rendered size or quality changes: the cache key carries it so
#: an upgrade cannot keep serving previews produced by the previous settings.
THUMB_VERSION = 2

#: Bounds concurrent decode/encode work so a cold /library grid cannot balloon RSS,
#: while still letting a multi-core box fill the grid faster than two at a time.
_GENERATE_SLOTS = threading.BoundedSemaphore(max(2, min(4, (os.cpu_count() or 4) // 2)))

#: A single image above ``REDUCE_ABOVE_PIXELS`` costs several hundred MB while it
#: decodes, and ``draft()`` cannot shrink a PNG or WebP before that. Four of them at
#: once (one per slot) is how a cold grid over a folder of large scans reached the
#: process memory ceiling, so the big ones decode one at a time instead.
_BIG_IMAGE_SLOT = threading.BoundedSemaphore(1)

#: How long a big image waits for that slot before giving up. Long enough for a
#: slow 100 MP decode plus the save, short enough that a wedged decoder cannot
#: park every request for a tile that will never appear.
_BIG_IMAGE_WAIT_SEC = 180.0

#: `+=` is not atomic: two threads could build the same temporary name, and the
#: loser's replace would publish the other frame as its own thumbnail.
_TEMP_COUNTER = itertools.count(1)


#: A frame whose mean luminance sits below this is treated as a black title card:
#: the next candidate timestamp is tried instead of publishing a black tile.
BLACK_FRAME_LUMA = 6.0
#: HDR transfer functions whose frames need a tonemap before being stored as sRGB.
HDR_TRANSFERS = frozenset({'smpte2084', 'arib-std-b67'})
#: Filter support is probed once per process: a build without zscale/tonemap must
#: not pay for a failed ffmpeg run on every HDR video.
_FILTER_SUPPORT = {'tonemap': True}


def _open_image_with_wide_ceiling(image_path: Path) -> Image.Image:
    """Open ``image_path`` under ``MAX_DECODE_PIXELS`` and restore PIL's ceiling.

    ``Image.open`` reads the header and runs Pillow's bomb check while the module-level
    ceiling is raised; ``_PIXEL_LIMIT_LOCK`` keeps that window from overlapping another
    thread's open (see the ``MAX_DECODE_PIXELS`` comment). Decoding happens later, after
    the ceiling is back to normal and with the pixel count already validated.
    """
    with _PIXEL_LIMIT_LOCK:
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS
        try:
            return Image.open(image_path)
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit


def _frame_luma(path: Path) -> float | None:
    """Mean luminance (0-255) of a rendered frame, or None when it cannot be read."""
    try:
        with Image.open(path) as frame:
            grey = frame.convert('L')
            grey.thumbnail((32, 32))
            pixels = list(grey.tobytes())
    except Exception:
        return None
    if not pixels:
        return None
    return sum(pixels) / len(pixels)


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
    #: How long a failed generation is remembered before the file is retried. A
    #: copy that finished mid-generation is retried within the hour, and a corrupt
    #: file costs one failed decode per hour instead of one per grid refresh.
    FAILURE_TTL_SEC = 3600

    def __init__(self, media_root: Path, library_service=None):
        self.media_root = media_root
        self.library = library_service or LibraryService(media_root)
        self.thumb_dir = get_thumbnails_dir()
        self._locks = [threading.Lock() for _ in range(self._LOCK_POOL_SIZE)]
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()
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
        digest = hashlib.sha1(f'{self._root_key}|v{THUMB_VERSION}|{key}'.encode('utf-8')).hexdigest()
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
            if not self._claim_legacy_adoption():
                # A different library already claimed the pre-root-key layout: this
                # file is its picture, not ours (same relative path, same old mtime).
                # Serve a freshly generated preview instead of adopting it.
                return None
            # Adopt a pre-root-key cache file instead of leaving a duplicate that
            # would show up in the settings cache count and never be cleaned.
            try:
                legacy.replace(canonical)
                return canonical
            except OSError:
                return legacy
        return canonical

    def _claim_legacy_adoption(self) -> bool:
        """Adopt legacy (root-key-less) caches only for the first library that does.

        ``_legacy_path_for_key`` hashes the bare relative path, so two libraries that
        share ``~/.tiklocal`` (the user points MEDIA_ROOT at another folder) produce
        the same file name; adopting it showed library A's photo for library B's file.
        The first claim is recorded next to the cache, and any other root regenerates.
        """
        marker = self.thumb_dir / '.legacy-root'
        try:
            owner = marker.read_text(encoding='utf-8').strip()
        except OSError:
            owner = ''
        if owner and owner != self._root_key:
            return False
        if not owner:
            try:
                marker.write_text(self._root_key, encoding='utf-8')
            except OSError:
                pass
        return True

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
        key = self.library.canonicalize_uri(uri)
        with self._inflight_lock:
            event = self._inflight.get(key)
            leader = event is None
            if leader:
                event = threading.Event()
                self._inflight[key] = event
        if not leader:
            # The grid asks for the same picture from several tiles at once; waiting
            # for the first decode beats running ffmpeg twice for one frame.
            event.wait(timeout=GENERATE_WAIT_SEC)
            cached = self.cached_thumbnail(uri)
            if cached:
                return cached, 'image/jpeg'
            # The leader is still working (a >48 MP decode can hold the big-image slot
            # for minutes). Queueing behind it used to make this request wait for the
            # per-URI lock *and* another big-image slot, so one picture could pin two
            # waitress workers for minutes; answer with the last usable frame instead.
            stale = self._stale_thumbnail(uri)
            return (stale, 'image/jpeg') if stale else (self.placeholder, 'image/png')
        try:
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
        finally:
            if leader:
                with self._inflight_lock:
                    self._inflight.pop(key, None)
                event.set()

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
        self._log_generation_failure(uri)

    def _log_generation_failure(self, uri: str) -> None:
        """Say *once* per file why a thumbnail never appeared.

        The grid used to answer 200 + a grey placeholder with no log line at all,
        so "every video is a grey box" looked like a media problem rather than a
        missing ffmpeg. Bounded to the first few distinct files: a library of
        broken files should produce a handful of warnings, not one per tile.
        """
        key = self.library.canonicalize_uri(uri)
        with _FAILURE_LOG_LOCK:
            if key in _FAILURE_LOG_SEEN or len(_FAILURE_LOG_SEEN) >= _FAILURE_LOG_LIMIT:
                return
            _FAILURE_LOG_SEEN.add(key)
        logger.warning(
            'thumbnail generation failed for %s (ffmpeg=%s, ffprobe=%s)',
            key, resolve_tool('ffmpeg') or 'MISSING', resolve_tool('ffprobe') or 'MISSING',
        )
    def _clear_failure(self, uri: str) -> None:
        """A successful generation invalidates an earlier failure marker.

        Without this a file that failed once (still copying, network hiccup) kept
        the placeholder for the full TTL even after the retry succeeded, because
        the marker is only consulted, never removed, on the success path.
        """
        try:
            self._failure_path(uri).unlink()
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
            # cache older than its source is stale and must be rebuilt. Both stats
            # belong inside the guard: clearing the cache can unlink the file
            # between is_file() and stat(), and that FileNotFoundError used to
            # escape /thumb as a 500 instead of a regenerated thumbnail.
            if not overwrite and output.is_file():
                try:
                    if (output.stat().st_size > 0
                            and output.stat().st_mtime_ns >= source.stat().st_mtime_ns):
                        return output
                except OSError:
                    pass
            # `tempfile` is avoided on purpose: a write-denied cache directory makes
            # mkdtemp retry TMP_MAX (2**31-1) names, pinning the request thread.
            temporary = self.thumb_dir / f'.thumb-{os.getpid()}-{next(_TEMP_COUNTER)}.jpg'
            try:
                if self._generate(source, temporary, timestamp, auto_timestamp=auto_timestamp):
                    if _replace_with_retry(temporary, output):
                        self._clear_failure(uri)
                        return output
            except OSError:
                return None
            finally:
                for leftover in (temporary, temporary.with_name(temporary.name + '.dark')):
                    try:
                        leftover.unlink(missing_ok=True)
                    except OSError:
                        pass
        return None

    def delete_thumbnail(self, uri: str) -> bool:
        removed = False
        for key in self.library.legacy_candidates(self.library.canonicalize_uri(uri)):
            for path in (self._path_for_key(key), self._legacy_path_for_key(key)):
                try:
                    path.unlink()
                    removed = True
                except OSError:
                    pass
                # The failure marker outlived the preview: deleting a broken file and
                # dropping a good one with the same name (copy2 keeps the old mtime)
                # kept showing the placeholder for the whole TTL without one try.
                try:
                    path.with_name(path.name + '.fail').unlink(missing_ok=True)
                except OSError:
                    pass
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

    @staticmethod
    def _probe_color_transfer(path: Path) -> str:
        """Return the video stream's transfer function, or '' when unknown."""
        probe = ffprobe_argv()
        if not probe:
            return ''
        try:
            output = sp.check_output([
                *probe, '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=color_transfer',
                '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
            ], stderr=sp.DEVNULL, timeout=10)
        except (OSError, sp.SubprocessError):
            return ''
        return output.decode('utf-8', errors='replace').strip().lower()

    def _generate(self, video_path: Path, output_path: Path, timestamp: float | None, *, auto_timestamp: bool) -> bool:
        # Decoding one 24MP JPEG peaks at ~192MB; six concurrent cold thumbnails
        # took RSS to ~983MB. Two slots keeps the peak bounded and still parallel.
        # Images take their slots inside _generate_image() so a big one waits for the
        # big-image slot *before* holding a generate slot; the old order let one huge
        # PNG park every generate slot on the big-image semaphore and stall the grid.
        if video_path.suffix.lower() in IMAGE_EXTENSIONS:
            return self._generate_image(video_path, output_path)
        with _GENERATE_SLOTS:
            return self._render(video_path, output_path, timestamp, auto_timestamp=auto_timestamp)

    def _render(self, video_path: Path, output_path: Path, timestamp: float | None, *, auto_timestamp: bool) -> bool:
        suffix = video_path.suffix.lower()

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

        hdr_filter = None
        if _FILTER_SUPPORT['tonemap'] and self._probe_color_transfer(video_path) in HDR_TRANSFERS:
            hdr_filter = (
                "zscale=t=linear:npl=100,format=gbrpf32le,"
                "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,"
                "scale=-2:'min(ih,640)'"
            )

        # A clip that fades in from black, a screen recording that starts with a
        # black slate or an animation with a black title card all produced a pure
        # black tile at 5.0 s. Keep the brightest frame we saw so a genuinely dark
        # video still gets a poster, but prefer a candidate that is not black.
        fallback_path = output_path.with_name(output_path.name + '.dark')
        best_luma = -1.0
        deadline = time.monotonic() + VIDEO_FRAME_BUDGET_SEC
        for t in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 1.0:
                break
            timeout = min(float(FFMPEG_TIMEOUT_SEC), remaining)
            cmd = [
                *ffmpeg_argv(), '-y',
                '-ss', str(max(0.0, float(t))),
                '-i', str(video_path),
                '-frames:v', '1',
                # Cap the height at 640 instead of pinning it: a fixed 360 left
                # portrait clips at 203x360, and that poster is stretched across a
                # full-screen card, so it looked blurred. min() keeps small videos
                # from being upscaled, and -2 keeps the width even for yuv420p.
                '-vf', hdr_filter or "scale=-2:'min(ih,640)'",
                '-q:v', '3',
                str(output_path)
            ]
            try:
                result = sp.run(cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=timeout)
                if hdr_filter and result.returncode != 0:
                    # The bundled ffmpeg may be built without zscale/tonemap (the
                    # measured build fails every tonemap chain), so fall back to a
                    # plain frame instead of losing the poster entirely.
                    _FILTER_SUPPORT['tonemap'] = False
                    hdr_filter = None
                    # The filter sits at -4 (the tail is '-vf', filter, '-q:v',
                    # '3', output). Writing -3 overwrote '-q:v', so the retry put
                    # two stray positionals in front of the output path and ffmpeg
                    # failed with "Unable to find a suitable output format".
                    cmd[-4] = "scale=-2:'min(ih,640)'"
                    remaining = deadline - time.monotonic()
                    if remaining <= 1.0:
                        break
                    result = sp.run(cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=min(float(FFMPEG_TIMEOUT_SEC), remaining))
                if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    luma = _frame_luma(output_path)
                    if luma is None or luma >= BLACK_FRAME_LUMA:
                        fallback_path.unlink(missing_ok=True)
                        return True
                    if luma > best_luma:
                        best_luma = luma
                        shutil.copyfile(output_path, fallback_path)
            except (OSError, sp.SubprocessError):
                continue
        if best_luma >= 0 and fallback_path.exists():
            os.replace(fallback_path, output_path)
            return True
        fallback_path.unlink(missing_ok=True)
        return False

    @staticmethod
    def _generate_image(image_path: Path, output_path: Path) -> bool:
        try:
            with _open_image_with_wide_ceiling(image_path) as source:
                pixels = source.size[0] * source.size[1]
                if pixels > MAX_DECODE_PIXELS:
                    # Our own ceiling on top of PIL's: refusing here keeps the decision
                    # per-image and thread-safe, and a 500 MP scan costs nothing but one
                    # log line instead of a 1.5 GB decode.
                    logger.warning(
                        'thumbnail skipped for %s: %d MP exceeds the %d MP decode limit',
                        image_path.name, pixels // 1_000_000, MAX_DECODE_PIXELS // 1_000_000,
                    )
                    return False
                # Palette images (PNG stickers, 2-frame GIFs) keep transparency in
                # info['transparency'] instead of an alpha band, so getbands() alone
                # missed them and convert('RGB') painted the transparent area black.
                has_alpha = 'A' in source.getbands() or 'transparency' in source.info
                # A Display P3 / Adobe RGB photo without its ICC profile looks more
                # saturated in the grid than in the viewer, where the browser honours
                # the embedded profile.
                icc_profile = source.info.get('icc_profile')
                # draft() lets libjpeg decode at 1/2, 1/4 or 1/8 scale: asking for
                # 2x the 640px target keeps the final LANCZOS pass at full quality
                # (a 24MP photo drops from ~101MB extra RSS to ~6MB). It runs before
                # the slot decision on purpose: a 60MP JPEG that draft() shrinks to
                # 900px decodes in milliseconds and must not queue behind a huge PNG.
                source.draft('RGB', (1280, 1280))
                pixels = source.size[0] * source.size[1]
                # draft() only rescales JPEG; a PNG or WebP of this size decodes at full
                # size first, so one of these can hold several hundred MB while four
                # ordinary photos would fit in the same memory. Big ones therefore take
                # a single slot of their own instead of scaling with the grid's slots.
                big = pixels > REDUCE_ABOVE_PIXELS
                if big and not _BIG_IMAGE_SLOT.acquire(timeout=_BIG_IMAGE_WAIT_SEC):
                    # A decode that never finishes must not park the whole grid: fail
                    # this tile and let the next request try again.
                    logger.warning(
                        'thumbnail for %s waited %ss for the big-image slot; skipping it',
                        image_path.name, int(_BIG_IMAGE_WAIT_SEC),
                    )
                    return False
                try:
                    with _GENERATE_SLOTS:
                        if pixels > REDUCE_ABOVE_PIXELS:
                            # Reducing by an integer factor keeps the LANCZOS input bounded
                            # and leaves the visible result identical.
                            factor = math.isqrt(pixels // REDUCE_ABOVE_PIXELS) + 1
                            source = source.reduce(factor)
                        # in_place rotates the decoded buffer instead of building a second
                        # full-size copy (72 MB each at 24 MP); the copy form is the fallback
                        # for Pillow builds that return a new image anyway.
                        rotated = ImageOps.exif_transpose(source, in_place=True)
                        image = source if rotated is None else rotated
                        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
                        if has_alpha:
                            rgba = image.convert('RGBA')
                            background = Image.new('RGB', rgba.size, (247, 246, 242))
                            background.paste(rgba, mask=rgba.getchannel('A'))
                            image = background
                        else:
                            image = image.convert('RGB')
                        options: dict[str, Any] = {'quality': 84, 'optimize': True, 'progressive': True}
                        if icc_profile:
                            options['icc_profile'] = icc_profile
                        image.save(output_path, 'JPEG', **options)
                finally:
                    if big:
                        _BIG_IMAGE_SLOT.release()
            return output_path.exists() and output_path.stat().st_size > 0
        except Exception as exc:  # noqa: BLE001 - one broken file must not fail the grid
            # Silent before: a 189 MP photo and a truncated download looked identical
            # in the logs (nothing at all), which made the invisible tile impossible
            # to diagnose from a bug report.
            logger.warning(
                'thumbnail decode failed for %s: %s: %s', image_path.name, type(exc).__name__, exc,
            )
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
