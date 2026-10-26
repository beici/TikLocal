"""TikTokDownloader (Web API mode) client and download orchestration.

TikTokDownloader runs as an external sidecar service (FastAPI, default port
5555). Its API only returns work metadata as JSON; the actual media URLs are
downloaded by TikLocal itself through the ``tiktok-downloader`` engine in
``tiklocal/services/downloader.py``.
"""

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tiklocal.i18n import t


DEFAULT_TIKTOK_DOWNLOADER_CONFIG: dict[str, Any] = {
    "base_url": "",
    "token": "",
    "timeout_sec": 15,
    "account_max_items": 100,
}

TIKTOK_DOWNLOADER_TABS = {"post", "favorite", "collection"}

# Link patterns mirror the official extractor in TikTokDownloader
# (src/link/extractor.py) so links accepted here match the sidecar.
_DOUYIN_DETAIL_LINK_PATTERN = re.compile(r"https?://www\.douyin\.com/(?:video|note|slides)/(\d{15,21})")
_DOUYIN_DETAIL_SHARE_PATTERN = re.compile(r"https?://www\.iesdouyin\.com/share/(?:video|note|slides)/(\d{15,21})")
_DOUYIN_DETAIL_MODAL_PATTERN = re.compile(r"modal_id=(\d{15,21})\b")
_DOUYIN_ACCOUNT_LINK_PATTERN = re.compile(r"https?://(?:www\.)?douyin\.com/user/([A-Za-z0-9_\-]+)")
_DOUYIN_ACCOUNT_SHARE_PATTERN = re.compile(r"https?://www\.iesdouyin\.com/share/user/([A-Za-z0-9_=\-]+)")
_DOUYIN_MIX_LINK_PATTERN = re.compile(r"https?://www\.douyin\.com/collection/(\d{15,21})")
_DOUYIN_MIX_SHARE_PATTERN = re.compile(r"https?://www\.iesdouyin\.com/share/mix/detail/(\d{15,21})")
_DOUYIN_SHORT_LINK_PATTERN = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_\-]+")
_TIKTOK_DETAIL_LINK_PATTERN = re.compile(r"https?://www\.tiktok\.com/@[^\s/]+/(?:video|photo)/(\d{15,21})")
_TIKTOK_ACCOUNT_LINK_PATTERN = re.compile(r"https?://www\.tiktok\.com/@([^\s/?#]+)")
_TIKTOK_MIX_LINK_PATTERN = re.compile(r"https?://www\.tiktok\.com/@[^\s/]+/(?:playlist|collection)/[^\s/?#]*?-(\d{15,21})")
_TIKTOK_SHORT_LINK_PATTERN = re.compile(r"https?://v[a-z]*\.tiktok\.com/[A-Za-z0-9_\-]+")

_LIVE_TYPE_TEXTS = {"实况", "LivePhoto"}
_FILENAME_UNSAFE_PATTERN = re.compile(r'[\\/:*?"<>|\r\n\t\-]+')
_WHITESPACE_PATTERN = re.compile(r"\s+")
_MEDIA_FILENAME_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}


class TikTokDownloaderError(Exception):
    """Raised when the TikTokDownloader sidecar cannot serve a request."""


def validate_tiktok_downloader_config(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a ``tiktok_downloader`` config section, returning (config, error)."""
    if not isinstance(payload, dict):
        return None, t('download.error.tiktok_config_object')

    section: dict[str, Any] = {}

    base_url = str(payload.get("base_url") or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        return None, t('download.error.tiktok_base_url')
    section["base_url"] = base_url.rstrip("/")

    token = payload.get("token")
    if token is None:
        token = ""
    if not isinstance(token, str) or len(token) > 512:
        return None, t('download.error.tiktok_token')
    section["token"] = token

    timeout_sec = payload.get("timeout_sec", 15)
    if isinstance(timeout_sec, bool) or not isinstance(timeout_sec, int) or not 3 <= timeout_sec <= 60:
        return None, t('download.error.tiktok_timeout')
    section["timeout_sec"] = timeout_sec

    max_items = payload.get("account_max_items", 100)
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 500:
        return None, t('download.error.tiktok_max_items')
    section["account_max_items"] = max_items

    return section, None


def parse_platform_link(text: str) -> dict[str, Any] | None:
    """Extract a Douyin/TikTok work, profile, collection, or short link.

    Returns ``{"kind": "detail"|"account"|"mix"|"share", "id": str,
    "platform": "douyin"|"tiktok"}`` or ``None`` when no known link is found.
    Short links are reported as ``share`` and must be expanded through
    ``TikTokDownloaderClient.resolve_share`` before use.
    """
    if not isinstance(text, str):
        return None

    for pattern, platform in (
        (_DOUYIN_DETAIL_LINK_PATTERN, "douyin"),
        (_DOUYIN_DETAIL_SHARE_PATTERN, "douyin"),
        (_TIKTOK_DETAIL_LINK_PATTERN, "tiktok"),
        (_DOUYIN_DETAIL_MODAL_PATTERN, "douyin"),
    ):
        match = pattern.search(text)
        if match:
            return {"kind": "detail", "id": match.group(1), "platform": platform}

    for pattern, platform in (
        (_DOUYIN_MIX_LINK_PATTERN, "douyin"),
        (_DOUYIN_MIX_SHARE_PATTERN, "douyin"),
        (_TIKTOK_MIX_LINK_PATTERN, "tiktok"),
    ):
        match = pattern.search(text)
        if match:
            return {"kind": "mix", "id": match.group(1), "platform": platform}

    for pattern, platform in (
        (_DOUYIN_ACCOUNT_LINK_PATTERN, "douyin"),
        (_DOUYIN_ACCOUNT_SHARE_PATTERN, "douyin"),
        (_TIKTOK_ACCOUNT_LINK_PATTERN, "tiktok"),
    ):
        match = pattern.search(text)
        if match:
            return {"kind": "account", "id": match.group(1), "platform": platform}

    for pattern, platform in (
        (_DOUYIN_SHORT_LINK_PATTERN, "douyin"),
        (_TIKTOK_SHORT_LINK_PATTERN, "tiktok"),
    ):
        if pattern.search(text):
            return {"kind": "share", "id": "", "platform": platform}

    return None


class TikTokDownloaderClient:
    """Minimal HTTP client for the TikTokDownloader Web API."""

    def __init__(self, base_url: str, token: str = "", timeout_sec: int = 15):
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.token = str(token or "")
        self.timeout_sec = timeout_sec

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise TikTokDownloaderError("TikTokDownloader service is not configured.")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["token"] = self.token
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout_sec) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise TikTokDownloaderError("TikTokDownloader rejected the request token.") from exc
            raise TikTokDownloaderError(f"TikTokDownloader returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TikTokDownloaderError(f"Unable to reach TikTokDownloader at {self.base_url}.") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TikTokDownloaderError("TikTokDownloader returned an invalid response.") from exc
        if not isinstance(decoded, dict):
            raise TikTokDownloaderError("TikTokDownloader returned an invalid response.")
        return decoded

    def check_connection(self) -> dict[str, Any]:
        """Probe the sidecar with ``GET /token`` and describe the outcome."""
        if not self.base_url:
            return {
                "configured": False,
                "base_url": "",
                "reachable": False,
                "message": "TikTokDownloader service is not configured.",
            }
        try:
            self._request("GET", "/token", timeout=2.5)
        except TikTokDownloaderError as exc:
            return {
                "configured": True,
                "base_url": self.base_url,
                "reachable": False,
                "message": str(exc),
            }
        return {
            "configured": True,
            "base_url": self.base_url,
            "reachable": True,
            "message": "TikTokDownloader service is connected.",
        }

    def resolve_share(self, text: str) -> str:
        """Expand a short share link through ``POST /douyin/share``."""
        response = self._request("POST", "/douyin/share", {"text": text})
        url = str(response.get("url") or "").strip()
        if not url:
            raise TikTokDownloaderError("TikTokDownloader could not resolve the shared link.")
        return url

    def _fetch_items(self, path: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._request("POST", path, payload)
        data = response.get("data")
        if data is None:
            data = []
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise TikTokDownloaderError("TikTokDownloader returned an unexpected response shape.")
        return data

    def fetch_detail(self, detail_id: str, platform: str = "douyin") -> list[dict[str, Any]]:
        return self._fetch_items(f"/{platform}/detail", {"detail_id": str(detail_id)})

    def fetch_account(self, sec_user_id: str, tab: str = "post", count: int = 18, platform: str = "douyin") -> list[dict[str, Any]]:
        if tab not in TIKTOK_DOWNLOADER_TABS:
            raise ValueError(t('download.error.tiktok_tab'))
        return self._fetch_items(f"/{platform}/account", {"sec_user_id": str(sec_user_id), "tab": tab, "count": int(count)})

    def fetch_mix(self, mix_id: str, count: int = 18, platform: str = "douyin") -> list[dict[str, Any]]:
        return self._fetch_items(f"/{platform}/mix", {"mix_id": str(mix_id), "count": int(count)})


def classify_post_type(raw_item: dict[str, Any], download_count: int) -> str:
    """Classify a work as 视频 / 图集 / 实况 following TikTokDownloader's type field."""
    type_text = str(raw_item.get("type") or "")
    if type_text in _LIVE_TYPE_TEXTS:
        return "实况"
    if type_text == "图集":
        return "图集"
    if download_count <= 1:
        return "视频"
    return "图集"


def sanitize_filename_component(text: Any, limit: int = 60) -> str:
    """Make a string safe for Windows filenames and the douyin pairing markers."""
    cleaned = _FILENAME_UNSAFE_PATTERN.sub(" ", str(text or ""))
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip().strip(".")
    return cleaned[:limit].strip()


def format_timestamp_slug(timestamp: Any) -> str:
    """Format a unix timestamp as ``YYYY-MM-DD HH.MM.SS`` for filenames."""
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        value = 0
    return time.strftime("%Y-%m-%d %H.%M.%S", time.localtime(value)) if value else "1970-01-01 00.00.00"


def normalize_work_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten sidecar work dicts into the fields the download engine needs."""
    works: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        downloads = [
            url.strip()
            for url in (raw_item.get("downloads") or [])
            if isinstance(url, str) and url.strip()
        ]
        if not downloads:
            continue
        work_id = str(raw_item.get("id") or "").strip()
        desc = sanitize_filename_component(raw_item.get("desc")) or sanitize_filename_component(work_id) or "work"
        works.append(
            {
                "id": work_id,
                "desc": desc,
                "create_timestamp": raw_item.get("create_timestamp"),
                "downloads": downloads,
                "post_type": classify_post_type(raw_item, len(downloads)),
            }
        )
    return works


def guess_download_extension(url: str, post_type: str = "视频") -> str:
    """Guess a media file extension from a CDN URL, falling back by post type."""
    suffix = Path(urlparse(str(url)).path).suffix.lower()
    if suffix == ".image":
        return ".jpg"
    if suffix == ".awebp":
        return ".webp"
    if suffix in _MEDIA_FILENAME_SUFFIXES:
        return suffix
    return ".jpg" if post_type == "图集" else ".mp4"


def build_work_filename(naming: dict[str, Any], index: int, total: int, extension: str) -> str:
    """Build a filename matching the ``date-type-owner-title`` douyin convention."""
    parts = [
        naming.get("created_at") or "1970-01-01 00.00.00",
        naming.get("post_type") or "视频",
        sanitize_filename_component(naming.get("owner"), limit=40) or "douyin",
        sanitize_filename_component(naming.get("desc"), limit=80) or "work",
    ]
    stem = "-".join(parts)
    if total > 1:
        stem = f"{stem}_{index}"
    return f"{stem}{extension}"


def seed_tiktok_downloader_config(config_store: Any, overrides: Any) -> None:
    """Apply first-boot yaml/env overrides without clobbering UI-saved values."""
    if not isinstance(overrides, dict) or not overrides:
        return
    try:
        current = config_store.get().get("tiktok_downloader")
    except Exception:
        return
    if not isinstance(current, dict):
        current = {}
    updated = dict(current)
    changed = False
    for key, default_value in DEFAULT_TIKTOK_DOWNLOADER_CONFIG.items():
        if key not in overrides or overrides.get(key) is None:
            continue
        if current.get(key, default_value) != default_value:
            continue
        if overrides.get(key) != current.get(key, default_value):
            updated[key] = overrides.get(key)
            changed = True
    if not changed:
        return
    try:
        config_store.set({"tiktok_downloader": updated})
    except (ValueError, OSError):
        return


class TikTokDownloadService:
    """Resolves links through the sidecar and enqueues direct download jobs."""

    def __init__(self, config_store: Any, download_manager: Any):
        self._config_store = config_store
        self._download_manager = download_manager

    def get_config(self) -> dict[str, Any]:
        merged = dict(DEFAULT_TIKTOK_DOWNLOADER_CONFIG)
        try:
            section = self._config_store.get().get("tiktok_downloader")
        except Exception:
            section = None
        if isinstance(section, dict):
            for key in merged:
                if section.get(key) is not None:
                    merged[key] = section.get(key)
        return merged

    def _client(self) -> TikTokDownloaderClient:
        config = self.get_config()
        return TikTokDownloaderClient(config["base_url"], token=config["token"], timeout_sec=config["timeout_sec"])

    def status(self) -> dict[str, Any]:
        """Return the sidecar connection status for the download page."""
        return self._client().check_connection()

    def resolve_target(self, url: str) -> dict[str, Any]:
        """Parse a pasted link, expanding short links through the sidecar."""
        target = parse_platform_link(url)
        if target and target["kind"] == "share":
            expanded = self._client().resolve_share(url)
            resolved = parse_platform_link(expanded)
            if not resolved or resolved["kind"] == "share":
                raise TikTokDownloaderError("TikTokDownloader could not resolve the shared link.")
            target = resolved
        if not target:
            raise ValueError(t('download.error.tiktok_link'))
        return target

    def enqueue_from_url(self, url: str, tab: str = "post", count: int = 18) -> dict[str, Any]:
        """Resolve a link into works and enqueue one direct-download job per work."""
        if tab not in TIKTOK_DOWNLOADER_TABS:
            raise ValueError(t('download.error.tiktok_tab'))
        config = self.get_config()
        if not config["base_url"]:
            raise TikTokDownloaderError("TikTokDownloader service is not configured.")
        client = self._client()
        target = self.resolve_target(url)
        count = max(1, min(int(count or 18), 100))

        if target["kind"] == "detail":
            raw_items = client.fetch_detail(target["id"], platform=target["platform"])
            owner_label = "douyin" if target["platform"] == "douyin" else "tiktok"
        elif target["kind"] == "mix":
            raw_items = client.fetch_mix(target["id"], count=count, platform=target["platform"])
            owner_label = f"MIX{target['id'][:12]}"
        else:
            raw_items = client.fetch_account(target["id"], tab=tab, count=count, platform=target["platform"])
            owner_label = f"UID{target['id'][:12]}"

        works = normalize_work_items(raw_items)
        max_items = int(config.get("account_max_items") or 100)
        truncated = len(works) > max_items
        works = works[:max_items]

        jobs: list[dict[str, Any]] = []
        for work in works:
            payload = {
                "downloads": work["downloads"],
                "naming": {
                    "created_at": format_timestamp_slug(work["create_timestamp"]),
                    "post_type": work["post_type"],
                    "owner": owner_label,
                    "desc": work["desc"],
                },
            }
            job = self._download_manager.enqueue(
                url,
                save_mode="root",
                engine="tiktok-downloader",
                cookie_mode="none",
                payload=payload,
            )
            if isinstance(job, dict):
                jobs.append(job)

        return {
            "kind": target["kind"],
            "platform": target["platform"],
            "target_id": target["id"],
            "items": len(works),
            "queued": len(jobs),
            "truncated": truncated,
            "jobs": jobs,
        }
