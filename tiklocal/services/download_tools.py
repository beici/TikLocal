"""Command lines and output parsing for the external download tools."""
import importlib.util
import os
import re
import shutil
import subprocess as sp
import sys
from pathlib import Path
from typing import Any

# ``--version`` probes the tool itself; ffmpeg has no module fallback.
TOOL_MODULES = {
    'yt-dlp': 'yt_dlp',
    'gallery-dl': 'gallery_dl',
}
TOOL_ENV_VARS = {
    'yt-dlp': 'TIKLOCAL_YT_DLP',
    'gallery-dl': 'TIKLOCAL_GALLERY_DL',
    'ffmpeg': 'TIKLOCAL_FFMPEG',
    'ffprobe': 'TIKLOCAL_FFPROBE',
}

_PROGRESS_RE = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%")
_ETA_RE = re.compile(r"ETA\s+(?P<eta>[0-9:]+)")
_DESTINATION_PATTERNS = [
    re.compile(r"^\[download\] Destination: (?P<path>.+)$"),
    re.compile(r'^\[Merger\] Merging formats into "(?P<path>.+)"$'),
    re.compile(r'^\[ExtractAudio\] Destination: (?P<path>.+)$'),
]
# Keeps the fields source_from_filename reads back when no source was recorded.
# display_id/id are bounded because some extractors put the whole page title in
# them: an unbounded name makes --write-info-json fail before any media is
# written (Windows MAX_PATH), so the job downloads nothing at all.
YT_DLP_OUTPUT_TEMPLATE = (
    "%(extractor_key|na)s__%(uploader_id|na).24B__%(display_id|na).80B__%(id|na).80B__%(upload_date|na)s__%(autonumber)02d.%(ext)s"
)


def _script_dirs() -> list[Path]:
    """Directories that hold console scripts next to the running interpreter."""
    directories = []
    executable = Path(sys.executable)
    for candidate in (executable.parent, executable.parent / 'Scripts', executable.parent / 'bin'):
        if candidate.is_dir() and candidate not in directories:
            directories.append(candidate)
    return directories


def _bundled_ffmpeg() -> str:
    """``imageio-ffmpeg`` ships a static ffmpeg binary; use it when PATH has none."""
    try:
        import imageio_ffmpeg
    except Exception:
        return ''
    try:
        return imageio_ffmpeg.get_ffmpeg_exe() or ''
    except Exception:
        return ''


def resolve_tool(command: str) -> list[str] | None:
    """Return the argv prefix that runs ``command``.

    Resolution order: explicit ``TIKLOCAL_*`` path, ``PATH``, the console
    scripts of the running interpreter (a Poetry/venv install is not on PATH
    when the server is started from an IDE or a shortcut), the bundled
    ``imageio-ffmpeg`` binary, then ``python -m <module>`` for the two download
    engines. Returning ``None`` means the tool really is missing.
    """
    override = os.environ.get(TOOL_ENV_VARS.get(command, ''), '').strip()
    if override:
        return [override]
    found = shutil.which(command)
    if found:
        return [found]
    names = [command] if os.name == 'nt' else []
    names.extend([command, f'{command}.exe', f'{command}.cmd'] if os.name == 'nt' else [command])
    for directory in _script_dirs():
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return [str(candidate)]
    if command == 'ffmpeg':
        bundled = _bundled_ffmpeg()
        if bundled:
            return [bundled]
    module = TOOL_MODULES.get(command)
    if module and importlib.util.find_spec(module) is not None:
        return [sys.executable, '-m', module]
    return None


def tool_argv(binary: str | list[str] | tuple[str, ...]) -> list[str]:
    """Normalise a binary argument into an argv prefix."""
    if isinstance(binary, (list, tuple)):
        return [str(part) for part in binary]
    return [str(binary)]


def yt_dlp_command(binary: str | list[str], media_root: Path, url: str, *, allow_playlist: bool,
                   cookie_path: Path | None, temp_dir: Path | None = None) -> list[str]:
    cmd = [
        *tool_argv(binary),
        "--newline",
        "--restrict-filenames",
        "--merge-output-format", "mp4",
        # H.264/AAC plays in every browser; AV1, VP9 and HEVC do not.
        "-S", "vcodec:h264,acodec:aac",
        "--write-info-json",
        "--continue",
        "--retries", "10",
        "--fragment-retries", "10",
        "--file-access-retries", "5",
        "--socket-timeout", "30",
        # The output template must stay relative: yt-dlp ignores `--paths temp:`
        # whenever `-o` carries an absolute path (it warns and keeps .part files
        # next to the media), so the job-private temp folder would never be used.
        "-o", YT_DLP_OUTPUT_TEMPLATE,
        "-P", f"home:{media_root}",
    ]
    if temp_dir is not None:
        # Partial files (.part/.ytdl) belong in the job's private folder: a canceled
        # or timed-out download used to leave them in the library forever.
        cmd.extend(["-P", f"temp:{temp_dir}"])
    if not allow_playlist:
        cmd.append("--no-playlist")
    if cookie_path:
        cmd.extend(["--cookies", str(cookie_path)])
    cmd.append(url)
    return cmd


def gallery_dl_command(binary: str | list[str], temp_dir: Path, log_file: Path, url: str, *,
                       archive_path: Path | None, cookie_path: Path | None) -> list[str]:
    cmd = [
        *tool_argv(binary),
        "--no-colors",
        "--directory", str(temp_dir),
        "--retries", "10",
        "--http-timeout", "30",
        "--sleep-429", "8",
    ]
    if archive_path:
        cmd.extend(["--download-archive", str(archive_path)])
    if cookie_path:
        cmd.extend(["--cookies", str(cookie_path)])
    cmd.extend(["--write-log", str(log_file), url])
    return cmd


def parse_eta_seconds(value: str) -> int | None:
    try:
        nums = [int(part) for part in value.split(":")]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def parse_progress(line: str) -> dict[str, Any] | None:
    percent_match = _PROGRESS_RE.search(line)
    percent = float(percent_match.group("percent")) if percent_match else None
    eta_match = _ETA_RE.search(line)
    eta_sec = parse_eta_seconds(eta_match.group("eta")) if eta_match else None
    if percent is None and eta_sec is None:
        return None
    return {"percent": percent, "eta_sec": eta_sec}


def parse_output_path(line: str) -> str:
    for pattern in _DESTINATION_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group("path").strip().strip('"')
    return ""


def error_line(line: str) -> str:
    return line if "error:" in line.lower() else ""


def probe_binary(command: str) -> tuple[str | None, str]:
    """Return ``(path, version)`` for a tool, honouring venv and bundled installs."""
    prefix = resolve_tool(command)
    if not prefix:
        return None, ""
    display = ' '.join(prefix)
    # The first launch of a freshly installed tool can be slow on Windows.
    for timeout in (15, 20):
        try:
            out = sp.check_output([*prefix, "--version"], text=True, timeout=timeout,
                                  stderr=sp.STDOUT)
            lines = [line for line in (out or "").strip().splitlines() if line.strip()]
            return display, (lines[0] if lines else "")
        except Exception:
            continue
    return display, ""


def collect_gallery_outputs(temp_dir: Path, *, excluded: set[Path]) -> list[Path]:
    outputs = []
    for entry in temp_dir.rglob("*"):
        if not entry.is_file():
            continue
        resolved = entry.resolve()
        if resolved not in excluded and not resolved.name.endswith(".part"):
            outputs.append(resolved)
    outputs.sort(key=lambda p: str(p))
    return outputs


def next_available_path(path: Path) -> Path:
    idx = 1
    candidate = path
    while candidate.exists():
        candidate = path.parent / f"{path.stem} ({idx}){path.suffix}"
        idx += 1
    return candidate
