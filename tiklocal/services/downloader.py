import ipaddress
import locale
import logging
import os
import queue
from collections import deque
import shutil
import signal
import subprocess as sp
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from tiklocal.i18n import DEFAULT_LOCALE, current_locale, t, translate
from tiklocal.paths import DOWNLOAD_TMP_DIR
from tiklocal.services.library import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from tiklocal.services.json_storage import read_json_file, write_json_atomic
from tiklocal.services.download_sources import (
    DownloadSourceStore,
    normalize_file_rel,
    normalize_source_meta,
    source_from_filename,
    source_from_info_json,
    source_meta_for_url,
    utc_now_iso,
)
from tiklocal.services.download_tools import (
    claim_available_path,
    collect_gallery_outputs,
    error_line,
    gallery_dl_command,
    parse_output_path,
    parse_progress,
    probe_binary,
    resolve_tool,
    yt_dlp_command,
)
from tiklocal.services.tiktok_downloader import (
    DEFAULT_TIKTOK_DOWNLOADER_CONFIG,
    build_work_filename,
    guess_download_extension,
    validate_tiktok_downloader_config,
)


DOWNLOAD_MAX_URL_LENGTH = 2048
DOWNLOAD_MAX_CONCURRENT_LIMIT = 16
DOWNLOAD_HISTORY_LIMIT = 200
COOKIE_MAX_UPLOAD_BYTES = 1024 * 1024
COOKIE_MATCH_MODE = "filename_contains_domain"
COOKIE_FILE_EXTENSIONS = {".txt", ".cookies"}
DOWNLOAD_ENGINES = {"yt-dlp", "gallery-dl", "tiktok-downloader"}
USER_DOWNLOAD_ENGINES = {"yt-dlp", "gallery-dl"}
DEFAULT_DOWNLOAD_ENGINE = "yt-dlp"
LOGGER = logging.getLogger(__name__)
DIRECT_DOWNLOAD_TIMEOUT_SEC = 30
# A tool that stalls silently (no stdout for hours) used to block the single worker
# forever; the queue then accepted jobs that never ran. This is the wall-clock cap.
DEFAULT_JOB_TIMEOUT_SEC = 3600
DIRECT_DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
#: gallery-dl also writes sidecar metadata and archives; only media belongs in the library.
GALLERY_MEDIA_SUFFIXES = frozenset(VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS)
#: Code pages a child tool may have written its stdout with, most likely first. The
#: interpreter locale is only a hint: yt-dlp.exe writes cp936 on a zh-CN console even
#: when the parent runs in UTF-8 mode, and a hard-coded choice breaks the other case.
OUTPUT_ENCODINGS = tuple(
    dict.fromkeys(
        encoding
        for encoding in (locale.getpreferredencoding(False), 'utf-8', 'cp936', 'gbk')
        if encoding
    )
)


def _decode_output(raw: bytes) -> str:
    """Decode one line of tool output by trying every plausible code page."""
    for encoding in OUTPUT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode(OUTPUT_ENCODINGS[0] or 'utf-8', errors='replace')


def gallery_media_outputs(temp_dir: Path, log_file: Path, cookie_copy: Path | None) -> list[Path]:
    """Collect downloaded media, ignoring the tool log and the private cookie copy.

    Both live inside ``temp_dir`` and used to be collected as products: the user's
    imported session cookie was moved into the media library, and a run that
    downloaded nothing still reported success.
    """
    excluded = {log_file.resolve()}
    if cookie_copy is not None:
        excluded.add(cookie_copy.resolve())
    return [
        entry
        for entry in collect_gallery_outputs(temp_dir, excluded=excluded)
        if entry.suffix.lower() in GALLERY_MEDIA_SUFFIXES
    ]

DEFAULT_DOWNLOAD_CONFIG = {
    "enabled": True,
    "max_concurrent": 2,
    "default_to_root": True,
    "allow_playlist": False,
    "cookie_enabled": True,
    "cookie_dir": "~/.tiklocal/cookies",
    "cookie_match_mode": COOKIE_MATCH_MODE,
    "gallery_archive_enabled": True,
    "gallery_archive_file": "~/.tiklocal/gallery-dl-archive.txt",
    "tiktok_downloader": dict(DEFAULT_TIKTOK_DOWNLOADER_CONFIG),
}

TERMINAL_JOB_STATUS = {"success", "failed", "canceled"}

# The settings page round-trips the whole download config.  Secrets are replaced by
# this sentinel before they leave the server; seeing it again means "unchanged".
MASKED_SECRET = "\u2022\u2022\u2022\u2022\u2022\u2022"


def _restore_masked_secrets(patch: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return patch
    section = patch.get("tiktok_downloader")
    if not isinstance(section, dict) or section.get("token") != MASKED_SECRET:
        return patch
    restored = dict(patch)
    restored_section = dict(section)
    restored_section["token"] = (current.get("tiktok_downloader") or {}).get("token", "")
    restored["tiktok_downloader"] = restored_section
    return restored


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _merge_download_config(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if not override:
        return merged
    for key in (
        "enabled",
        "max_concurrent",
        "default_to_root",
        "allow_playlist",
        "cookie_enabled",
        "cookie_dir",
        "cookie_match_mode",
        "gallery_archive_enabled",
        "gallery_archive_file",
        "tiktok_downloader",
    ):
        if key in override:
            merged[key] = override[key]
    return merged


def validate_download_config(payload: Any, *, partial: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, t('download.error.config_object')

    cleaned: dict[str, Any] = {}

    defaults = dict(DEFAULT_DOWNLOAD_CONFIG)
    env_cookie_dir = str(os.environ.get("TIKLOCAL_COOKIE_DIR", "")).strip()
    if env_cookie_dir:
        defaults["cookie_dir"] = env_cookie_dir

    def _read_bool(field: str) -> str | None:
        if field not in payload:
            if partial:
                return None
            cleaned[field] = bool(defaults[field])
            return None
        value = payload[field]
        if not isinstance(value, bool):
            return t('download.error.field_boolean', field=field)
        cleaned[field] = value
        return None

    for field in ("enabled", "default_to_root", "allow_playlist", "cookie_enabled", "gallery_archive_enabled"):
        error = _read_bool(field)
        if error:
            return None, error

    if "max_concurrent" in payload or not partial:
        value = payload.get("max_concurrent", defaults["max_concurrent"])
        max_concurrent = _to_int(value)
        if max_concurrent is None:
            return None, t('download.error.max_concurrent_integer')
        if max_concurrent < 0 or max_concurrent > DOWNLOAD_MAX_CONCURRENT_LIMIT:
            return None, t('download.error.max_concurrent_range', limit=DOWNLOAD_MAX_CONCURRENT_LIMIT)
        cleaned["max_concurrent"] = max_concurrent

    if "cookie_dir" in payload or not partial:
        cookie_dir = str(payload.get("cookie_dir", defaults["cookie_dir"])).strip()
        if not cookie_dir:
            return None, t('download.error.cookie_dir_empty')
        cleaned["cookie_dir"] = cookie_dir

    if "cookie_match_mode" in payload or not partial:
        mode = str(payload.get("cookie_match_mode", defaults["cookie_match_mode"])).strip()
        if mode != COOKIE_MATCH_MODE:
            return None, t('download.error.cookie_match_mode', mode=COOKIE_MATCH_MODE)
        cleaned["cookie_match_mode"] = mode

    if "gallery_archive_file" in payload or not partial:
        archive_file = str(payload.get("gallery_archive_file", defaults["gallery_archive_file"])).strip()
        if not archive_file:
            return None, t('download.error.gallery_archive_empty')
        cleaned["gallery_archive_file"] = archive_file

    if "tiktok_downloader" in payload or not partial:
        section, section_error = validate_tiktok_downloader_config(
            payload.get("tiktok_downloader", defaults["tiktok_downloader"])
        )
        if section_error:
            return None, section_error
        cleaned["tiktok_downloader"] = section

    return cleaned, None


def _is_local_host(url: str) -> bool:
    """True when the URL targets this machine or a link-local/metadata address.

    yt-dlp runs on the server, so accepting such a URL turns the download queue
    into a way to reach services that only listen on localhost (including the
    DSH GUI). Private LAN ranges stay allowed: pointing at a NAS is legitimate.
    """

    try:
        host = (urlparse(url).hostname or "").strip().strip("[]")
    except ValueError:
        return True
    if not host:
        return True
    candidates = [host]
    try:
        candidates.extend(entry[4][0] for entry in socket.getaddrinfo(host, None))
    except OSError:
        pass
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_reserved:
            return True
    return False


def validate_download_url(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, t('download.error.body_object')

    url = str(payload.get("url", "")).strip()
    if not url:
        return None, t('download.error.url_empty')
    if len(url) > DOWNLOAD_MAX_URL_LENGTH:
        return None, t('download.error.url_too_long', limit=DOWNLOAD_MAX_URL_LENGTH)

    try:
        parsed = urlparse(url)
    except ValueError:
        # urlparse() raises on a malformed IPv6 literal such as "http://[::1", and the
        # route treated the ValueError as an unhandled error: a 500 with an HTML body
        # instead of the 400 every other malformed URL gets.
        return None, t('download.error.scheme_invalid')
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, t('download.error.scheme_invalid')
    if _is_local_host(url):
        return None, t('download.error.url_host_blocked')

    save_mode = str(payload.get("save_mode", "root")).strip() or "root"
    if save_mode != "root":
        return None, t('download.error.save_mode_root')

    engine = str(payload.get("engine", DEFAULT_DOWNLOAD_ENGINE)).strip().lower() or DEFAULT_DOWNLOAD_ENGINE
    if engine not in USER_DOWNLOAD_ENGINES:
        return None, t('download.error.engine_invalid')

    cookie_mode = str(payload.get("cookie_mode", "")).strip().lower()
    cookie_file_raw = payload.get("cookie_file")
    cookie_file = str(cookie_file_raw).strip() if cookie_file_raw is not None else ""

    if cookie_file and not cookie_mode:
        cookie_mode = "manual"
    if not cookie_mode:
        cookie_mode = "auto"

    if cookie_mode not in {"auto", "none", "manual"}:
        return None, t('download.error.cookie_mode_invalid')

    if cookie_mode == "manual":
        if not cookie_file:
            return None, t('download.error.cookie_file_required')
        if not is_safe_cookie_filename(cookie_file):
            return None, t('download.error.cookie_file_path')
    else:
        cookie_file = ""

    return {
        "url": url,
        "save_mode": save_mode,
        "engine": engine,
        "cookie_mode": cookie_mode,
        "cookie_file": cookie_file,
    }, None


def is_safe_cookie_filename(name: str) -> bool:
    if not name:
        return False
    if name in {".", ".."}:
        return False
    if "/" in name or "\\" in name:
        return False
    if Path(name).name != name:
        return False
    if Path(name).suffix.lower() not in COOKIE_FILE_EXTENSIONS:
        return False
    return True


def _domain_candidates(host: str) -> list[str]:
    host = host.strip().lower().strip(".")
    if not host:
        return []
    parts = host.split(".")
    if len(parts) <= 1:
        return [host]
    candidates = []
    for i in range(0, len(parts) - 1):
        candidates.append(".".join(parts[i:]))
    return candidates


class DownloadConfigStore:
    def __init__(self, store_path: Path):
        self.store_path = store_path
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        # A truncated file used to read as {} and the next save overwrote the user's
        # cookie_dir / archive path with defaults; quarantine it instead.
        data = read_json_file(self.store_path, {})
        return data if isinstance(data, dict) else {}

    def get(self) -> dict[str, Any]:
        data = self._load()
        defaults = dict(DEFAULT_DOWNLOAD_CONFIG)
        env_cookie_dir = str(os.environ.get("TIKLOCAL_COOKIE_DIR", "")).strip()
        if env_cookie_dir:
            defaults["cookie_dir"] = env_cookie_dir
        validated, _ = validate_download_config(data, partial=True)
        merged = _merge_download_config(defaults, validated)
        if isinstance(data.get("updated_at"), str):
            merged["updated_at"] = data["updated_at"]
        return merged

    def set(self, value: dict[str, Any]) -> dict[str, Any]:
        defaults = self.get()
        defaults.pop("updated_at", None)
        merged = _merge_download_config(defaults, value)
        validated, error = validate_download_config(merged, partial=False)
        if error:
            raise ValueError(error)

        payload = dict(validated)
        payload["updated_at"] = utc_now_iso()
        self._write(payload)
        return payload

    def _write(self, data: dict[str, Any]) -> None:
        # write_json_atomic uses a unique temp directory: a fixed "<name>.tmp" let two
        # concurrent saves collide (PermissionError on Windows) and lose an update.
        write_json_atomic(self.store_path, data)


class DownloadHistoryStore:
    def __init__(self, store_path: Path):
        self.store_path = store_path
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def get(self) -> list[dict[str, Any]]:
        data = read_json_file(self.store_path, [], expected_type=list)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def save(self, jobs: list[dict[str, Any]]) -> None:
        write_json_atomic(self.store_path, jobs[:DOWNLOAD_HISTORY_LIMIT])


class DownloadManager:
    def __init__(
        self,
        media_root: Path,
        config_store: DownloadConfigStore,
        history_store: DownloadHistoryStore,
        source_store: DownloadSourceStore | None = None,
        output_source_id: str = "default",
        on_outputs: Callable[[list[str]], int] | None = None,
    ):
        self.media_root = media_root.resolve()
        self.output_source_id = str(output_source_id or "default").strip() or "default"
        self.config_store = config_store
        self.history_store = history_store
        self.source_store = source_store
        self.on_outputs = on_outputs

        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._pending: deque[str] = deque()
        # job_id -> monotonic time of the last progress persist (see _update_progress).
        self._progress_persist_at: dict[str, float] = {}

        self._jobs: dict[str, dict[str, Any]] = {}
        self._job_order: list[str] = []
        self._cancel_events: dict[str, threading.Event] = {}
        self._processes: dict[str, sp.Popen[bytes]] = {}
        self._workers: dict[str, threading.Thread] = {}

        self._config = self.config_store.get()
        self._load_history()
        self._cleanup_stale_temp_dirs()

    def _cleanup_stale_temp_dirs(self) -> None:
        """Drop partial downloads a crash or hard kill left in the temp folder.

        The library scanner ignores .tiklocal-download-tmp, so leftovers were
        invisible in the UI and never freed; a restart is the only safe moment to
        clear them (no worker owns a folder yet).
        """
        root = self.media_root / DOWNLOAD_TMP_DIR
        try:
            entries = list(root.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError('The download manager is closed.')
            self._started = True

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._closed = True
            changed = False
            for job_id, job in self._jobs.items():
                if job['status'] not in TERMINAL_JOB_STATUS:
                    changed = True
                    self._cancel_events[job_id].set()
                    job['cancel_requested'] = True
                    if job['status'] == 'queued':
                        job.update(status='canceled', finished_at=utc_now_iso(),
                                   error_message=translate(
                                       str(job.get('locale') or DEFAULT_LOCALE),
                                       'download.error.canceled'),
                                   eta_sec=None)
            self._pending.clear()
            processes = list(self._processes.values())
            workers = list(self._workers.values())
        try:
            if changed:
                with self._lock:
                    self._persist_locked()
        finally:
            for process in processes:
                self._terminate_process(process)
            deadline = time.monotonic() + timeout
            for worker in workers:
                worker.join(max(0, deadline - time.monotonic()))
            if any(worker.is_alive() for worker in workers):
                raise RuntimeError(t('download.error.stop_timeout'))

    def probe_dependencies(self) -> dict[str, Any]:
        yt_dlp_path, yt_dlp_version = probe_binary("yt-dlp")
        gallery_dl_path, gallery_dl_version = probe_binary("gallery-dl")
        ffmpeg_path, _ = probe_binary("ffmpeg")

        return {
            "yt_dlp_available": bool(yt_dlp_path),
            "yt_dlp_path": yt_dlp_path or "",
            "yt_dlp_version": yt_dlp_version,
            "gallery_dl_available": bool(gallery_dl_path),
            "gallery_dl_path": gallery_dl_path or "",
            "gallery_dl_version": gallery_dl_version,
            "ffmpeg_available": bool(ffmpeg_path),
            "ffmpeg_path": ffmpeg_path or "",
        }

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        # The whole read-modify-write stays under the lock: two tabs saving at once
        # used to overwrite each other's fields and race on the temp file.
        with self._lock:
            patch = _restore_masked_secrets(patch, self._config)
            merged = _merge_download_config(self._config, patch)
            validated, error = validate_download_config(merged, partial=False)
            if error:
                raise ValueError(error)
            saved = self.config_store.set(validated)
            self._config = _merge_download_config(DEFAULT_DOWNLOAD_CONFIG, saved)
            self._config["updated_at"] = saved.get("updated_at")
            self._dispatch_locked()
            return dict(self._config)

    def list_cookie_files(self) -> dict[str, Any]:
        with self._lock:
            cookie_enabled = bool(self._config.get("cookie_enabled", True))
            cookie_dir_text = str(self._config.get("cookie_dir", DEFAULT_DOWNLOAD_CONFIG["cookie_dir"])).strip()
        cookie_dir = self._expand_cookie_dir(cookie_dir_text)

        files: list[str] = []
        if cookie_enabled and cookie_dir.exists() and cookie_dir.is_dir():
            for entry in cookie_dir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in COOKIE_FILE_EXTENSIONS:
                    continue
                files.append(entry.name)
        files.sort(key=lambda name: name.lower())
        return {
            "cookie_enabled": cookie_enabled,
            "cookie_dir": str(cookie_dir),
            "files": files,
        }

    def enqueue(
        self,
        url: str,
        *,
        save_mode: str = "root",
        engine: str = DEFAULT_DOWNLOAD_ENGINE,
        cookie_mode: str = "auto",
        cookie_file: str = "",
        retry_of: str = "",
        output_token: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        engine = (engine or DEFAULT_DOWNLOAD_ENGINE).strip().lower()
        if engine not in DOWNLOAD_ENGINES:
            raise RuntimeError(t('download.error.engine_unsupported'))
        if payload is not None and not isinstance(payload, dict):
            raise RuntimeError(t('download.error.payload_object'))

        chosen_file, chosen_mode, cookie_error = self._resolve_cookie_choice(
            url=url,
            cookie_mode=cookie_mode,
            cookie_file=cookie_file,
        )
        if cookie_error:
            raise RuntimeError(cookie_error)

        # Probing the engine spawns a subprocess (and can hit the 5s timeout when the
        # tool is missing); doing it under the queue lock stalled every other queue
        # operation for that long, including the status polling endpoint.
        engine_version = probe_binary(engine)[1]

        with self._lock:
            if not self._started or self._closed:
                raise RuntimeError('The download manager has not started or is closed.')
            if not self._config.get("enabled", True):
                raise RuntimeError(t('download.error.disabled'))

            job_id = uuid.uuid4().hex[:12]
            now = utc_now_iso()
            job = {
                "id": job_id,
                "url": url,
                "save_mode": save_mode,
                "engine": engine,
                "engine_version": engine_version,
                "status": "queued",
                "progress_percent": None,
                "eta_sec": None,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "output_path_rel": "",
                "output_files_rel": [],
                "file_count": 0,
                "error_message": "",
                "failure_stage": "",
                "requested_format": "mp4_preferred",
                "cancel_requested": False,
                "cookie_file": chosen_file,
                "cookie_match_mode": chosen_mode,
                "retry_of": retry_of.strip(),
                "output_token": output_token.strip() or now.replace(":", "").replace("-", "").replace("Z", ""),
                "download_payload": dict(payload) if isinstance(payload, dict) else {},
                # Worker threads have no request context, so ``t()`` would fall back to
                # the default locale and an English UI would show Chinese errors. Record
                # the enqueuing request's locale and translate the terminal messages
                # with it instead.
                "locale": current_locale(),
            }
            self._jobs[job_id] = job
            self._job_order.insert(0, job_id)
            self._cancel_events[job_id] = threading.Event()
            try:
                self._persist_locked()
            except Exception:
                # A failed jobs.json write used to leave a queued job that is
                # never dispatched and can only be cancelled: roll it back so the
                # caller sees one clean error instead of a ghost row.
                self._jobs.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
                if job_id in self._job_order:
                    self._job_order.remove(job_id)
                raise
            self._pending.append(job_id)
            self._dispatch_locked()
        return self.get_job(job_id) or job

    def upload_cookie_file(self, filename: str, content: bytes, *, replace: bool = False) -> dict[str, Any]:
        safe_name = str(filename or "").strip()
        if not is_safe_cookie_filename(safe_name):
            raise ValueError(t('download.error.cookie_name_invalid'))
        if not isinstance(content, (bytes, bytearray)):
            raise ValueError(t('download.error.cookie_content_invalid'))
        if len(content) == 0:
            raise ValueError(t('download.error.cookie_empty'))
        if len(content) > COOKIE_MAX_UPLOAD_BYTES:
            raise ValueError(t('download.error.file_too_large', kb=COOKIE_MAX_UPLOAD_BYTES // 1024))

        cookie_dir = self._cookie_dir_path()
        target = (cookie_dir / safe_name).resolve()
        try:
            target.relative_to(cookie_dir.resolve())
        except ValueError as exc:
            raise ValueError(t('download.error.path_invalid')) from exc

        if target.exists() and not replace:
            raise ValueError(t('download.error.cookie_exists', name=safe_name))

        tmp_path = target.with_suffix(target.suffix + ".tmp")
        with tmp_path.open("wb") as f:
            f.write(bytes(content))
        os.replace(tmp_path, target)
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass

        return {
            "filename": safe_name,
            "size": target.stat().st_size if target.exists() else len(content),
            "cookie_dir": str(cookie_dir),
        }

    def delete_job(self, job_id: str) -> tuple[bool, str | None]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, t('download.error.job_not_found')
            if job.get("status") not in TERMINAL_JOB_STATUS:
                return False, t('download.error.delete_running')

            self._jobs.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            self._processes.pop(job_id, None)
            self._progress_persist_at.pop(job_id, None)
            self._job_order = [jid for jid in self._job_order if jid != job_id]
            self._persist_locked()
        return True, None

    def clear_history(self) -> int:
        deleted = 0
        with self._lock:
            keep_ids: list[str] = []
            for job_id in self._job_order:
                job = self._jobs.get(job_id)
                if not job:
                    continue
                if job.get("status") in TERMINAL_JOB_STATUS:
                    deleted += 1
                    self._jobs.pop(job_id, None)
                    self._cancel_events.pop(job_id, None)
                    self._processes.pop(job_id, None)
                    self._progress_persist_at.pop(job_id, None)
                else:
                    keep_ids.append(job_id)
            self._job_order = keep_ids
            self._persist_locked()
        return deleted

    def delete_jobs(self, job_ids: list[str]) -> dict[str, Any]:
        """Drop several finished jobs from the history in one step.

        Returns the number of removed records, the ids that were kept together
        with the reason, and the media files each removed job produced so the
        caller can move those files to the trash.
        """
        deleted = 0
        skipped: list[dict[str, str]] = []
        outputs: dict[str, list[str]] = {}
        with self._lock:
            for job_id in job_ids:
                job = self._jobs.get(job_id)
                if not job:
                    skipped.append({"id": job_id, "error": t('download.error.job_not_found')})
                    continue
                if job.get("status") not in TERMINAL_JOB_STATUS:
                    skipped.append({"id": job_id, "error": t('download.error.delete_running')})
                    continue
                outputs[job_id] = [
                    str(value) for value in job.get("output_files_rel") or [] if str(value).strip()
                ]
                self._jobs.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
                self._processes.pop(job_id, None)
                self._progress_persist_at.pop(job_id, None)
                deleted += 1
            if deleted:
                removed = set(outputs)
                self._job_order = [jid for jid in self._job_order if jid not in removed]
                self._persist_locked()
        return {"deleted": deleted, "skipped": skipped, "outputs": outputs}

    def retry_job(self, job_id: str) -> tuple[dict[str, Any] | None, str | None]:
        with self._lock:
            if not self._started or self._closed:
                return None, 'The download manager has not started or is closed.'
            job = self._jobs.get(job_id)
            if not job:
                return None, t('download.error.job_not_found')
            if job.get("status") not in {"failed", "canceled"}:
                return None, t('download.error.retry_state')
            index_only = job.get('failure_stage') == 'index'
            if index_only:
                for uri in job['output_files_rel']:
                    path = self._resolve_media_file_path(uri)
                    if path is None or not path.is_file():
                        return None, t('download.error.register_missing')
                previous = {key: job.get(key) for key in ('status', 'error_message', 'finished_at', 'cancel_requested')}
                job.update(status='queued', error_message='', finished_at=None, cancel_requested=False)
                self._cancel_events[job_id] = threading.Event()
                try:
                    self._persist_locked()
                except OSError:
                    # jobs.json can be locked for a moment by a scanner or an editor.
                    # Letting the error escape answered 500 while the job stayed
                    # "queued" in memory: the card showed a 0 % progress bar forever
                    # and every further retry was refused because the status was no
                    # longer failed. Roll back so the retry button keeps working.
                    job.update(previous)
                    return None, t('common.error.storage_unavailable')
                self._pending.append(job_id)
                self._dispatch_locked()
            url = str(job.get("url") or "")
            save_mode = str(job.get("save_mode") or "root")
            engine = str(job.get("engine") or DEFAULT_DOWNLOAD_ENGINE)
            cookie_file = str(job.get("cookie_file") or "")
            cookie_match_mode = str(job.get("cookie_match_mode") or "none")
            output_token = str(job.get("output_token") or "")
            download_payload = job.get("download_payload")
            download_payload = dict(download_payload) if isinstance(download_payload, dict) else {}

        if index_only:
            return self.get_job(job_id), None

        cookie_mode = "none"
        if cookie_match_mode in {"auto", "manual"}:
            cookie_mode = cookie_match_mode

        try:
            new_job = self.enqueue(
                url,
                save_mode=save_mode,
                engine=engine,
                cookie_mode=cookie_mode,
                cookie_file=cookie_file,
                retry_of=job_id,
                output_token=output_token,
                payload=download_payload,
            )
            return new_job, None
        except RuntimeError as exc:
            return None, str(exc)

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), DOWNLOAD_HISTORY_LIMIT))
        with self._lock:
            job_ids = self._job_order[:limit]
            return [dict(self._jobs[job_id]) for job_id in job_ids if job_id in self._jobs]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def resolve_source_for_file(self, file_rel: str) -> dict[str, Any] | None:
        key = normalize_file_rel(file_rel)
        if not key:
            return None

        mapped = self._resolve_source_from_map(key)
        if mapped:
            return mapped

        info_meta = self._resolve_source_from_info_json(key)
        if info_meta:
            return info_meta

        return source_from_filename(key)

    def resolve_sources_for_files(self, file_rels: list[str]) -> dict[str, dict[str, Any] | None]:
        normalized = [normalize_file_rel(item) for item in file_rels]
        normalized = [item for item in normalized if item]
        normalized = list(dict.fromkeys(normalized))
        result: dict[str, dict[str, Any] | None] = {}
        for file_rel in normalized:
            result[file_rel] = self.resolve_source_for_file(file_rel)
        return result

    def delete_source_for_file(self, file_rel: str) -> bool:
        if not self.source_store:
            return False
        return self.source_store.delete(file_rel)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        process: sp.Popen[bytes] | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None

            if job["status"] in TERMINAL_JOB_STATUS:
                return dict(job)

            cancel_event = self._cancel_events.get(job_id)
            if cancel_event:
                cancel_event.set()

            job["cancel_requested"] = True
            if job["status"] == "queued":
                self._mark_canceled_locked(job)
                return dict(job)

            process = self._processes.get(job_id)

        if process:
            self._terminate_process(process)

        return self.get_job(job_id)

    def _load_history(self) -> None:
        history = self.history_store.get()
        now = utc_now_iso()
        with self._lock:
            for item in history[:DOWNLOAD_HISTORY_LIMIT]:
                job_id = str(item.get("id") or "").strip()
                if not job_id:
                    continue

                status = str(item.get("status") or "failed")
                if status in {"queued", "running"}:
                    status = "failed"
                    # Persisted jobs from before the locale was recorded translate to
                    # the default locale, which is what they saw before as well.
                    item["error_message"] = translate(
                        str(item.get("locale") or DEFAULT_LOCALE),
                        'download.error.interrupted_restart',
                    )
                    item["finished_at"] = now
                raw_output_files = item.get("output_files_rel")
                output_files_rel = raw_output_files if isinstance(raw_output_files, list) else []

                job = {
                    "id": job_id,
                    "url": str(item.get("url") or ""),
                    "save_mode": str(item.get("save_mode") or "root"),
                    "engine": str(item.get("engine") or DEFAULT_DOWNLOAD_ENGINE),
                    "engine_version": str(item.get("engine_version") or ""),
                    "status": status,
                    "progress_percent": item.get("progress_percent"),
                    "eta_sec": item.get("eta_sec"),
                    "created_at": str(item.get("created_at") or now),
                    "started_at": item.get("started_at"),
                    "finished_at": item.get("finished_at"),
                    "output_path_rel": str(item.get("output_path_rel") or ""),
                    "output_files_rel": [str(v) for v in output_files_rel if str(v).strip()],
                    "file_count": _to_int(item.get("file_count")) or 0,
                    "error_message": str(item.get("error_message") or ""),
                    "failure_stage": str(item.get("failure_stage") or ""),
                    "requested_format": str(item.get("requested_format") or "mp4_preferred"),
                    "cancel_requested": False,
                    "cookie_file": str(item.get("cookie_file") or ""),
                    "cookie_match_mode": str(item.get("cookie_match_mode") or "none"),
                    "retry_of": str(item.get("retry_of") or ""),
                    "output_token": str(
                        item.get("output_token")
                        or str(item.get("created_at") or now).replace(":", "").replace("-", "").replace("Z", "")
                    ),
                    "download_payload": dict(item.get("download_payload"))
                    if isinstance(item.get("download_payload"), dict)
                    else {},
                }
                if not job["file_count"] and job["output_files_rel"]:
                    job["file_count"] = len(job["output_files_rel"])
                if not job["output_files_rel"] and job["output_path_rel"]:
                    job["output_files_rel"] = [job["output_path_rel"]]
                    job["file_count"] = max(1, job["file_count"])
                self._jobs[job_id] = job
                self._job_order.append(job_id)
                self._cancel_events[job_id] = threading.Event()

    def _dispatch_locked(self) -> None:
        if not self._started or self._closed:
            return
        limit = int(self._config['max_concurrent'])
        while self._pending and (limit == 0 or len(self._workers) < limit):
            job_id = self._pending[0]
            if job_id in self._workers:
                break  # An immediate retry must wait for the previous execution to finish.
            self._pending.popleft()
            job = self._jobs.get(job_id)
            if not job or job['status'] != 'queued':
                continue
            worker = threading.Thread(target=self._run_job, args=(job_id,),
                                      name=f'tiklocal-download-{job_id}', daemon=True)
            self._workers[job_id] = worker
            worker.start()

    def _run_job(self, job_id: str) -> None:
        try:
            with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    return

                if job["status"] != "queued":
                    return

                cancel_event = self._cancel_events.get(job_id)
                if cancel_event and cancel_event.is_set():
                    self._mark_canceled_locked(job)
                    return

                job["status"] = "running"
                job["started_at"] = utc_now_iso()
                job["progress_percent"] = 0.0
                index_only = job.get('failure_stage') == 'index'
                existing_outputs = list(job['output_files_rel'])
                self._persist_locked()

            if index_only:
                return_code, error_message, output_rel_list = 0, '', existing_outputs
            else:
                result = self._execute_download(job_id)
                return_code, error_message, output_rel_list = self._normalize_execute_result(result)
            source_context: dict[str, Any] | None = None

            with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    return

                cancel_event = self._cancel_events.get(job_id)
                canceled = bool(cancel_event and cancel_event.is_set())
                if canceled:
                    # A cancel can land after a file has already been moved into the
                    # library. Dropping those outputs made the UI report "canceled, 0
                    # files" while the bytes sat in the media root, outside both the
                    # index and the job history, until the next manual scan.
                    if output_rel_list:
                        job["output_files_rel"] = list(output_rel_list)
                        job["file_count"] = len(output_rel_list)
                        job["output_path_rel"] = output_rel_list[0]
                        job["partial"] = True
                    self._mark_canceled_locked(job)
                elif return_code == 0:
                    job["progress_percent"] = 100.0
                    job["eta_sec"] = 0
                    job["output_files_rel"] = output_rel_list
                    job["file_count"] = len(output_rel_list)
                    job["output_path_rel"] = output_rel_list[0] if output_rel_list else ""
                    job["error_message"] = ""
                    job['failure_stage'] = 'index'
                    source_context = {
                        "url": str(job.get("url") or ""),
                        "engine": str(job.get("engine") or ""),
                        "job_id": str(job.get("id") or ""),
                        "created_at": str(job.get("created_at") or utc_now_iso()),
                        "files": list(output_rel_list),
                    }
                    self._persist_locked()
                else:
                    job["status"] = "failed"
                    job["error_message"] = error_message or "Download failed. Check the URL and network connection."
                    if output_rel_list:
                        # A playlist item can finish before the next one fails: the
                        # file is already in the library, so dropping it from the job
                        # made the UI report "failed, 0 files" while the media sat on
                        # disk with no history entry.
                        job["output_files_rel"] = list(output_rel_list)
                        job["file_count"] = len(output_rel_list)
                        job["output_path_rel"] = output_rel_list[0]
                        job["partial"] = True
                    job["finished_at"] = utc_now_iso()
                    self._persist_locked()

            if canceled:
                if output_rel_list and self.on_outputs:
                    self.on_outputs(output_rel_list)
                return

            if source_context:
                self._record_job_sources_on_success(source_context)
            if output_rel_list and self.on_outputs:
                # Partial results (a failed playlist) must reach the index too, or the
                # files that did download stay invisible until the next manual sync.
                self.on_outputs(output_rel_list)
            if return_code == 0:
                # Do not expose a successful job until its outputs are visible in
                # the media index. Otherwise clients can observe success and then
                # briefly fail to find the downloaded file.
                with self._lock:
                    job = self._jobs.get(job_id)
                    if not job:
                        return
                    if self._cancel_events[job_id].is_set():
                        self._mark_canceled_locked(job)
                        return
                    job["status"] = "success"
                    job['failure_stage'] = ''
                    job["finished_at"] = utc_now_iso()
                    self._persist_locked()
        except Exception as exc:
            # Without this the only trace of an engine/library crash was an empty
            # error_message: the UI showed "failed" with no reason and the server
            # kept no traceback at all.
            LOGGER.exception('Download job %s crashed', job_id)
            with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    return
                if self._cancel_events[job_id].is_set():
                    self._mark_canceled_locked(job)
                    return
                job["status"] = "failed"
                job["finished_at"] = utc_now_iso()
                if job.get('failure_stage') == 'index':
                    job['error_message'] = f'The file was downloaded, but media registration failed: {exc}'
                    if job.get('output_files_rel'):
                        # The bytes are already in the library and only the index
                        # write failed; without the flag the UI offers a plain
                        # retry, which re-downloads and duplicates the file.
                        job['partial'] = True
                elif isinstance(exc, FileNotFoundError):
                    engine = job.get('engine') or DEFAULT_DOWNLOAD_ENGINE
                    job['error_message'] = f'{engine} was not found. Install it before using this engine.'
                else:
                    # Exceptions such as TimeoutError() stringify to ''; fall back to
                    # the type name so the failure is never silent.
                    job['error_message'] = str(exc) or f'{type(exc).__name__} while downloading.'
                self._persist_locked()
        finally:
            with self._lock:
                process = self._processes.pop(job_id, None)
            try:
                if process:
                    self._terminate_process(process)
                    if process.stdout:
                        process.stdout.close()
            finally:
                with self._lock:
                    self._workers.pop(job_id, None)
                    self._dispatch_locked()

    def _execute_download(self, job_id: str) -> tuple[int, str, list[str]] | tuple[int, str, str]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return 1, "Job not found.", ""
            engine = str(job.get("engine") or DEFAULT_DOWNLOAD_ENGINE)

        if engine == "gallery-dl":
            result = self._execute_download_gallery_dl(job_id)
        elif engine == "tiktok-downloader":
            result = self._execute_download_direct(job_id)
        else:
            result = self._execute_download_yt_dlp(job_id)
        code, error, outputs = self._normalize_execute_result(result)
        if code == 0 and not outputs:
            # yt-dlp exits 0 for "has already been downloaded" and for runs that only
            # executed post-processors. The job used to be stored as a success with
            # zero files, so the bytes never reached the index and the UI offered
            # nothing to open.
            return 1, t('download.error.no_output'), []
        return result

    def _execute_download_yt_dlp(self, job_id: str) -> tuple[int, str, list[str]]:
        yt_dlp_bin = resolve_tool("yt-dlp")
        if not yt_dlp_bin:
            raise FileNotFoundError("yt-dlp not found")

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return 1, "Job not found.", []
            url = job["url"]
            allow_playlist = bool(self._config.get("allow_playlist", False))
        cookie_path, error = self._job_cookie_path(job)
        if error:
            return 1, error, []

        output_paths: list[str] = []

        def on_line(line: str) -> None:
            progress = parse_progress(line)
            if progress:
                self._update_progress(job_id, progress)
            destination = parse_output_path(line)
            if destination and destination not in output_paths:
                # A playlist download reports one Destination per item; keeping only
                # the last one hid N-1 files from the index, the history and delete.
                output_paths.append(destination)

        # yt-dlp writes .part/.ytdl files while downloading; keep them out of the
        # library so a cancel or timeout cannot leave hidden junk behind.
        temp_dir = (self.media_root / DOWNLOAD_TMP_DIR / job_id).resolve()
        temp_dir.mkdir(parents=True, exist_ok=True)
        # yt-dlp opens --cookies read-write and dumps the jar back on exit, so two
        # concurrent jobs sharing the imported file would truncate each other.
        cookie_for_job = self._private_cookie_copy(cookie_path, temp_dir)
        try:
            cmd = yt_dlp_command(yt_dlp_bin, self.media_root, url, allow_playlist=allow_playlist,
                                 cookie_path=cookie_for_job, temp_dir=temp_dir)
            return_code, error_message = self._run_tool(job_id, cmd, on_line)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if not error_message and return_code != 0:
            error_message = f"yt-dlp exited with code {return_code}."
        output_rel = [self._to_media_relative(path) for path in output_paths]
        return return_code, error_message, [path for path in output_rel if path]

    def _execute_download_gallery_dl(self, job_id: str) -> tuple[int, str, list[str]]:
        gallery_dl_bin = resolve_tool("gallery-dl")
        if not gallery_dl_bin:
            raise FileNotFoundError("gallery-dl not found")

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return 1, "Job not found.", []
            url = str(job.get("url") or "")
            archive_enabled = bool(self._config.get("gallery_archive_enabled", True))
            archive_file_text = str(self._config.get("gallery_archive_file", DEFAULT_DOWNLOAD_CONFIG["gallery_archive_file"])).strip()
        cookie_path, error = self._job_cookie_path(job)
        if error:
            return 1, error, []

        archive_path = None
        if archive_enabled:
            archive_path = Path(archive_file_text).expanduser()
            archive_path.parent.mkdir(parents=True, exist_ok=True)

        # gallery-dl writes into a private folder; only finished files are moved into the library.
        temp_dir = (self.media_root / DOWNLOAD_TMP_DIR / job_id).resolve()
        temp_dir.mkdir(parents=True, exist_ok=True)
        log_file = temp_dir / "gallery-dl.log"
        cookie_for_job = self._private_cookie_copy(cookie_path, temp_dir)
        try:
            cmd = gallery_dl_command(gallery_dl_bin, temp_dir, log_file, url,
                                     archive_path=archive_path, cookie_path=cookie_for_job)
            return_code, error_message = self._run_tool(job_id, cmd, lambda line: None)
            if return_code != 0:
                return return_code, error_message or f"gallery-dl exited with code {return_code}.", []

            outputs = gallery_media_outputs(temp_dir, log_file, cookie_for_job)
            moved_outputs = [self._move_file_to_media_root(path) for path in outputs]
            moved_outputs = [path for path in moved_outputs if path]
            if not moved_outputs:
                return 1, "gallery-dl did not download any usable files.", []
            return 0, "", moved_outputs
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _execute_download_direct(self, job_id: str) -> tuple[int, str, list[str]]:
        """Download the media URLs attached by TikTokDownloadService into the library."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return 1, "Job not found.", []
            payload = job.get("download_payload") if isinstance(job.get("download_payload"), dict) else {}
            timeout = float(self._config.get("job_timeout_sec") or DEFAULT_JOB_TIMEOUT_SEC)
        deadline = time.monotonic() + max(1.0, timeout)

        downloads = [
            str(url).strip()
            for url in (payload.get("downloads") or [])
            if isinstance(url, str) and url.strip()
        ]
        if not downloads:
            return 1, "No media URLs were attached to this job.", []
        naming = payload.get("naming") if isinstance(payload.get("naming"), dict) else {}

        cancel_event = self._cancel_events.get(job_id)
        temp_dir = (self.media_root / DOWNLOAD_TMP_DIR / job_id).resolve()
        temp_dir.mkdir(parents=True, exist_ok=True)
        total = len(downloads)
        try:
            downloaded: list[Path] = []
            for index, url in enumerate(downloads, start=1):
                if cancel_event and cancel_event.is_set():
                    return 1, translate(self._job_locale(job_id), 'download.error.canceled'), []
                extension = guess_download_extension(url, str(naming.get("post_type") or "视频"))
                filename = build_work_filename(naming, index, total, extension)
                target = temp_dir / filename

                def on_progress(fraction: float, index: int = index) -> None:
                    percent = ((index - 1) + max(0.0, min(fraction, 1.0))) * 100.0 / total
                    self._update_progress(job_id, {"percent": round(percent, 1)})

                error = self._direct_fetch(
                    url,
                    target,
                    on_progress,
                    cancelled=(lambda: bool(cancel_event and cancel_event.is_set())),
                    deadline=deadline,
                    locale=self._job_locale(job_id),
                )
                if error:
                    # A gallery item can fail after its siblings landed. Returning an
                    # empty list here made the finally below delete files that were
                    # already downloaded, and the job reported "failed, 0 files"
                    # while the user had nothing to show for the wait.
                    moved = [self._move_file_to_media_root(path) for path in downloaded]
                    return 1, error, [path for path in moved if path]
                downloaded.append(target)

            moved_outputs = [self._move_file_to_media_root(path) for path in downloaded]
            moved_outputs = [path for path in moved_outputs if path]
            if not moved_outputs:
                return 1, "No usable files were downloaded.", []
            return 0, "", moved_outputs
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _direct_fetch(
        self,
        url: str,
        target: Path,
        on_progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        deadline: float | None = None,
        locale: str = DEFAULT_LOCALE,
    ) -> str | None:
        """Stream one media file to ``target``; return an error message or None."""
        # The URL comes from a sidecar response, so a rewritten payload must not turn
        # the downloader into a local-file reader (file://) or an SSRF probe.
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            return "Only http and https media URLs can be downloaded."
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": DIRECT_DOWNLOAD_USER_AGENT,
                "Referer": "https://www.douyin.com/",
            },
        )
        tmp_path = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(request, timeout=DIRECT_DOWNLOAD_TIMEOUT_SEC) as response, tmp_path.open("wb") as fh:
                # A public URL may redirect to loopback or a cloud metadata endpoint.
                # The redirect has already happened by now, but refusing to read the
                # body keeps the response from ever reaching the library.
                resolved = getattr(response, "geturl", None)
                final_url = resolved() if callable(resolved) else url
                if _is_local_host(final_url or url):
                    tmp_path.unlink(missing_ok=True)
                    return "The media URL redirected to a local address."
                total_bytes = int(response.headers.get("Content-Length") or 0)
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if content_type.startswith("text/html"):
                    # A CDN that answers with a challenge page or an error document
                    # returns 200. Without this check the page was renamed to .mp4
                    # and recorded as a successful download.
                    tmp_path.unlink(missing_ok=True)
                    return "The media URL returned a web page instead of a media file."
                received = 0
                while True:
                    # Cancellation used to be checked only between files, so a slow
                    # multi-file post kept streaming for minutes after the user hit
                    # cancel. Check per chunk: it is a flag read, far cheaper than a read.
                    if cancelled and cancelled():
                        tmp_path.unlink(missing_ok=True)
                        return translate(locale, 'download.error.canceled')
                    if deadline is not None and time.monotonic() > deadline:
                        tmp_path.unlink(missing_ok=True)
                        return "Download exceeded the time limit."
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    if on_progress and total_bytes > 0:
                        on_progress(received / total_bytes)
            if total_bytes and received != total_bytes:
                # A dropped connection used to leave a truncated file that was
                # renamed into the library and reported as a success: the grid then
                # showed a video that stops halfway and cannot be repaired by retry.
                tmp_path.unlink(missing_ok=True)
                return "The media host closed the connection before the file was complete."
            os.replace(tmp_path, target)
        except urllib.error.HTTPError as exc:
            tmp_path.unlink(missing_ok=True)
            return f"Media download failed with HTTP {exc.code}."
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            tmp_path.unlink(missing_ok=True)
            return f"Unable to reach the media host: {exc}"
        except OSError as exc:
            # Disk-full / permission errors are local, not upstream: reporting them as
            # an unreachable host sent users looking at the network.
            tmp_path.unlink(missing_ok=True)
            return f"Unable to write the downloaded file: {exc.strerror or exc}"
        if on_progress:
            on_progress(1.0)
        return None

    def _job_cookie_path(self, job: dict[str, Any]) -> tuple[Path | None, str | None]:
        cookie_file = str(job.get("cookie_file") or "")
        if cookie_file and str(job.get("cookie_match_mode") or "none") in {"auto", "manual"}:
            return self._resolve_cookie_path(cookie_file)
        return None, None

    def _private_cookie_copy(self, cookie_path: Path | None, temp_dir: Path) -> Path | None:
        """Hand the engine a throwaway copy: yt-dlp dumps the jar back into its file.

        The imported cookie file lives in the persistent cookie directory and is
        the user's original; two concurrent jobs pointing at it would interleave
        their writes and truncate the jar.
        """
        if cookie_path is None:
            return None
        copy_path = temp_dir / "cookies.txt"
        try:
            shutil.copyfile(cookie_path, copy_path)
        except OSError:
            return cookie_path
        return copy_path

    def _run_tool(self, job_id: str, cmd: list[str], on_line: Callable[[str], None]) -> tuple[int, str]:
        """Run a download tool to completion, keeping its last error line.

        Stdout is pumped by a helper thread so the main loop can enforce a wall-clock
        deadline and react to cancellation even while the tool prints nothing.
        """
        process = self._start_process(job_id, cmd)
        cancel_event = self._cancel_events.get(job_id)
        timeout = float(self._config.get("job_timeout_sec") or DEFAULT_JOB_TIMEOUT_SEC)
        deadline = time.monotonic() + max(1.0, timeout)
        error_message = ""
        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:
            if process.stdout is None:
                lines.put(None)
                return
            try:
                for raw_line in process.stdout:
                    lines.put(_decode_output(raw_line).strip())
            except Exception:  # noqa: BLE001 - the pump thread must never die silently
                # A decode error here used to kill the thread while the tool kept
                # running, so a "successful" job reported zero outputs.
                LOGGER.exception("Download output pump failed for job %s", job_id)
            finally:
                lines.put(None)

        threading.Thread(target=pump, name=f"download-pump-{job_id}", daemon=True).start()

        timed_out = False
        canceled = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if cancel_event and cancel_event.is_set():
                    canceled = True
                    break
                continue
            if line is None:
                break
            if line:
                on_line(line)
                error_message = error_line(line) or error_message
            if cancel_event and cancel_event.is_set():
                canceled = True
                break

        if timed_out:
            self._terminate_process(process)
            error_message = f"Download exceeded the {int(timeout)}s time limit."
        elif canceled:
            # Return as soon as the tool is gone: a grandchild (ffmpeg) can keep the
            # stdout pipe open, and waiting for EOF here held a worker slot for up to
            # the full job timeout, stalling the whole download queue.
            self._terminate_process(process)
            error_message = error_message or translate(
                self._job_locale(job_id), 'download.error.canceled')
        return process.wait(), error_message

    def _job_locale(self, job_id: str | None) -> str:
        """Locale the job was queued in (worker threads have no request context).

        Deliberately lock-free: it only reads an immutable string out of a dict the
        caller may already hold ``self._lock`` for, and a stale read is harmless.
        """
        job = self._jobs.get(job_id or "")
        return str((job or {}).get("locale") or DEFAULT_LOCALE)

    def _update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["status"] != "running":
                return

            if progress.get("percent") is not None:
                job["progress_percent"] = progress["percent"]
            if progress.get("eta_sec") is not None:
                job["eta_sec"] = progress["eta_sec"]

            # yt-dlp emits progress dozens of times per second; rewriting the whole job
            # list each tick kept the disk busy for the entire download. Throttle to one
            # write per second per job (in-memory fields still update on every tick).
            if now - self._progress_persist_at.get(job_id, 0.0) < 1.0:
                return
            self._progress_persist_at[job_id] = now
            self._persist_locked()

    def _persist_locked(self) -> None:
        jobs = [dict(self._jobs[job_id]) for job_id in self._job_order if job_id in self._jobs]
        self.history_store.save(jobs)

    def _mark_canceled_locked(self, job: dict[str, Any]) -> None:
        job["status"] = "canceled"
        job["finished_at"] = utc_now_iso()
        job["error_message"] = translate(
            str(job.get("locale") or DEFAULT_LOCALE), 'download.error.canceled')
        job["eta_sec"] = None
        self._persist_locked()

    def _start_process(self, job_id: str, cmd: list[str]) -> sp.Popen[bytes]:
        # Cancellation and process registration share the lock: no late child can
        # escape close() while it is taking its process snapshot.
        with self._lock:
            if self._closed or self._cancel_events[job_id].is_set():
                raise RuntimeError('Download canceled.')
            # Keep the pipe in bytes and decode in _decode_output: the child's
            # encoding is not knowable from here (cp936 on a zh-CN console, utf-8
            # under PYTHONUTF8=1), and either hard-coded choice turned the other
            # case into mojibake paths whose outputs were then dropped.
            process = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.STDOUT,
                               bufsize=0, start_new_session=os.name == 'posix')
            self._processes[job_id] = process
            return process

    def _terminate_process(self, process: sp.Popen[bytes]) -> None:
        try:
            # Every job ends here, including the ones that finished on their own. On
            # Windows taskkill /T /F still ran for a reaped pid, so a recycled pid could
            # take an unrelated process tree (an editor, a build, another job's ffmpeg)
            # down with it.
            if process.poll() is None:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except sp.TimeoutExpired:
                    pass
                # A downloader may leave a child (such as ffmpeg) holding stdout open.
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    # TerminateProcess only reaches the direct child; yt-dlp's ffmpeg
                    # merger keeps writing into the job temp dir after a cancel.
                    self._kill_process_tree_windows(process.pid)
                    if process.poll() is None:
                        process.kill()
        except ProcessLookupError:
            pass
        process.wait()

    @staticmethod
    def _kill_process_tree_windows(pid: int) -> None:
        if os.name != 'nt':
            return
        try:
            sp.run(['taskkill', '/T', '/F', '/PID', str(pid)],
                   stdout=sp.DEVNULL, stderr=sp.DEVNULL, timeout=15)
        except (OSError, sp.SubprocessError):
            pass

    def _to_media_relative(self, path_text: str) -> str:
        if not path_text:
            return ""
        try:
            absolute = Path(path_text).resolve()
            media_root = self.media_root.resolve()
            if str(absolute).startswith(str(media_root)):
                rel = str(absolute.relative_to(media_root)).replace("\\", "/")
                # --paths temp: moves files into place at the end; a path still
                # inside the temp dir is deleted right after the run, so it can
                # never be a library file (it used to be recorded as one).
                if rel.split("/", 1)[0] == DOWNLOAD_TMP_DIR:
                    return ""
                return f"@{self.output_source_id}/{rel}"
        except Exception:
            return ""
        return ""

    def _normalize_execute_result(self, result: Any) -> tuple[int, str, list[str]]:
        if not isinstance(result, tuple):
            return 1, "The downloader returned an invalid result.", []

        return_code = _to_int(result[0]) if len(result) >= 1 else 1
        if return_code is None:
            return_code = 1
        error_message = str(result[1] or "") if len(result) >= 2 else ""
        raw_output = result[2] if len(result) >= 3 else ""

        if isinstance(raw_output, str):
            outputs = [raw_output] if raw_output else []
        elif isinstance(raw_output, list):
            outputs = [str(item).strip() for item in raw_output if str(item).strip()]
        else:
            outputs = []
        outputs = [self._canonical_output_uri(item) for item in outputs]
        outputs = [item for item in outputs if item]
        return return_code, error_message, outputs

    def _canonical_output_uri(self, value: str) -> str:
        key = normalize_file_rel(value)
        if not key:
            return ""
        if key.startswith("@"):
            return key
        return f"@{self.output_source_id}/{key}"

    def _move_file_to_media_root(self, source_path: Path) -> str:
        if not source_path.exists() or not source_path.is_file():
            return ""

        target_name = source_path.name
        try:
            target = claim_available_path((self.media_root / target_name).resolve())
        except OSError as error:
            LOGGER.warning('Could not reserve a target name for %s: %s', target_name, error)
            return ''
        try:
            source_path.replace(target)
        except OSError:
            try:
                shutil.move(str(source_path), str(target))
            except OSError as error:
                target.unlink(missing_ok=True)
                LOGGER.warning('Could not move %s into the library: %s', target_name, error)
                return ''
        return self._to_media_relative(str(target))

    def _record_job_sources_on_success(self, context: dict[str, Any]) -> None:
        if not self.source_store:
            return

        files = context.get("files")
        if not isinstance(files, list):
            return
        raw_url = str(context.get("url") or "").strip()
        if not raw_url:
            return

        base_meta = source_meta_for_url(
            raw_url,
            resolved_by="map",
            engine=str(context.get("engine") or "").strip(),
            job_id=str(context.get("job_id") or "").strip(),
            created_at=str(context.get("created_at") or utc_now_iso()),
        )
        if not base_meta:
            return

        records: dict[str, dict[str, Any]] = {}
        for rel in files:
            key = normalize_file_rel(str(rel or ""))
            if not key:
                continue
            records[key] = dict(base_meta)
        if records:
            self.source_store.set_many(records)

    def _resolve_source_from_map(self, file_rel: str) -> dict[str, Any] | None:
        if not self.source_store:
            return None
        mapped = self.source_store.get(file_rel)
        if not mapped:
            legacy = self._strip_output_source_prefix(file_rel)
            mapped = self.source_store.get(legacy) if legacy != file_rel else None
        if not mapped:
            return None
        return normalize_source_meta(mapped, resolved_by="map")

    def _resolve_source_from_info_json(self, file_rel: str) -> dict[str, Any] | None:
        media_file = self._resolve_media_file_path(file_rel)
        if not media_file or not media_file.exists():
            return None
        meta = source_from_info_json(media_file)
        if meta and self.source_store:
            self.source_store.set_many({file_rel: dict(meta)})
        return meta

    def _resolve_media_file_path(self, file_rel: str) -> Path | None:
        key = self._strip_output_source_prefix(file_rel)
        key = normalize_file_rel(key)
        if not key:
            return None
        path = (self.media_root / key).resolve()
        try:
            path.relative_to(self.media_root)
        except ValueError:
            return None
        return path

    def _strip_output_source_prefix(self, file_rel: str) -> str:
        key = normalize_file_rel(file_rel)
        prefix = f"@{self.output_source_id}/"
        if key.startswith(prefix):
            return key[len(prefix):]
        return key

    def _resolve_cookie_choice(self, *, url: str, cookie_mode: str, cookie_file: str) -> tuple[str, str, str | None]:
        with self._lock:
            cookie_enabled = bool(self._config.get("cookie_enabled", True))
            cookie_dir_text = str(self._config.get("cookie_dir", DEFAULT_DOWNLOAD_CONFIG["cookie_dir"])).strip()

        if not cookie_enabled:
            return "", "none", None
        if cookie_mode == "none":
            return "", "none", None

        info = self.list_cookie_files()
        files = info.get("files") or []
        cookie_dir = self._expand_cookie_dir(cookie_dir_text)
        if not files:
            if cookie_mode == "manual":
                return "", "none", t('download.error.cookie_dir_missing', path=cookie_dir)
            return "", "none", None

        if cookie_mode == "manual":
            if not cookie_file:
                return "", "none", t('download.error.cookie_file_required_manual')
            if cookie_file not in files:
                return "", "none", t('download.error.cookie_file_missing', name=cookie_file)
            return cookie_file, "manual", None

        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return "", "none", None

        lowered_files = {name.lower(): name for name in files}
        for candidate in _domain_candidates(host):
            hits = [lowered_files[key] for key in lowered_files if candidate in key]
            if hits:
                hits.sort(key=lambda item: (len(item), item.lower()))
                return hits[0], "auto", None
        return "", "none", None

    def _expand_cookie_dir(self, cookie_dir_text: str) -> Path:
        # A relative cookie_dir used to resolve against the process CWD, so the
        # same setting pointed at a different folder after a restart from another
        # directory and the settings page reported files that no longer loaded.
        path = Path(cookie_dir_text).expanduser()
        return path if path.is_absolute() else (Path.cwd() / path).resolve()

    def _cookie_dir_path(self) -> Path:
        with self._lock:
            cookie_dir_text = str(self._config.get("cookie_dir", DEFAULT_DOWNLOAD_CONFIG["cookie_dir"])).strip()
        cookie_dir = self._expand_cookie_dir(cookie_dir_text)
        cookie_dir.mkdir(parents=True, exist_ok=True)
        return cookie_dir

    def _resolve_cookie_path(self, cookie_file: str) -> tuple[Path | None, str | None]:
        info = self.list_cookie_files()
        cookie_dir = Path(info["cookie_dir"]).expanduser().resolve()
        if cookie_file not in info.get("files", []):
            return None, t('download.error.cookie_file_missing', name=cookie_file)
        path = (cookie_dir / cookie_file).resolve()
        try:
            path.relative_to(cookie_dir)
        except ValueError:
            return None, t('download.error.cookie_path_invalid')
        if not path.exists() or not path.is_file():
            return None, t('download.error.cookie_file_unavailable', name=cookie_file)
        return path, None
