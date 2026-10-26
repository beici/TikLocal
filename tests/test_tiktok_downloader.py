"""Unit tests for the TikTokDownloader sidecar integration."""

import io
import json
import urllib.error

import pytest

from tiklocal.app import create_app
from tiklocal.services.downloader import DownloadConfigStore, DownloadManager, validate_download_config
from tiklocal.services.tiktok_downloader import (
    DEFAULT_TIKTOK_DOWNLOADER_CONFIG,
    TikTokDownloaderClient,
    TikTokDownloaderError,
    TikTokDownloadService,
    build_work_filename,
    classify_post_type,
    format_timestamp_slug,
    guess_download_extension,
    normalize_work_items,
    parse_platform_link,
    seed_tiktok_downloader_config,
    validate_tiktok_downloader_config,
)


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._stream = io.BytesIO(json.dumps(payload).encode("utf-8"))
        self.status = status

    def read(self) -> bytes:
        return self._stream.read()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code: int):
    return urllib.error.HTTPError(
        "http://sidecar:5555/token", code, "err", io.StringIO(""), io.BytesIO(b"")
    )


# --- link parsing ---------------------------------------------------------


def test_parse_platform_link_detail_variants():
    assert parse_platform_link("https://www.douyin.com/video/7345678901234567890?a=1") == {
        "kind": "detail", "id": "7345678901234567890", "platform": "douyin",
    }
    assert parse_platform_link("看这个 https://www.douyin.com/note/7100000000000000001 很棒")["kind"] == "detail"
    assert parse_platform_link("https://www.iesdouyin.com/share/video/7100000000000000002/")["kind"] == "detail"
    assert parse_platform_link("https://www.tiktok.com/@someone/video/7300000000000000001") == {
        "kind": "detail", "id": "7300000000000000001", "platform": "tiktok",
    }


def test_parse_platform_link_account_mix_share():
    account = parse_platform_link("https://www.douyin.com/user/MS4wLjABAAAAxi")
    assert account == {"kind": "account", "id": "MS4wLjABAAAAxi", "platform": "douyin"}
    mix = parse_platform_link("https://www.douyin.com/collection/7200000000000000001")
    assert mix == {"kind": "mix", "id": "7200000000000000001", "platform": "douyin"}
    share = parse_platform_link("https://v.douyin.com/AbCd123/")
    assert share == {"kind": "share", "id": "", "platform": "douyin"}
    assert parse_platform_link("hello world") is None
    assert parse_platform_link("https://example.com/watch?v=1") is None


def test_parse_platform_link_modal_id_counts_as_detail():
    assert parse_platform_link("https://www.douyin.com/user/MS4wLjABAAAAxi?modal_id=7345678901234567890") == {
        "kind": "detail", "id": "7345678901234567890", "platform": "douyin",
    }


# --- config validation ----------------------------------------------------


def test_validate_tiktok_downloader_config_accepts_valid_section():
    section, error = validate_tiktok_downloader_config(
        {"base_url": "http://192.168.x.x:5555/", "token": "t", "timeout_sec": 20, "account_max_items": 50}
    )
    assert error is None
    assert section == {
        "base_url": "http://192.168.x.x:5555", "token": "t", "timeout_sec": 20, "account_max_items": 50,
    }


@pytest.mark.parametrize("payload", [
    {"base_url": "192.168.1.5:5555"},
    {"timeout_sec": 1},
    {"timeout_sec": 61},
    {"account_max_items": 0},
    {"token": 123},
    "nope",
])
def test_validate_tiktok_downloader_config_rejects_bad_sections(payload):
    section, error = validate_tiktok_downloader_config(payload)
    assert section is None
    assert error


def test_download_config_accepts_tiktok_section():
    validated, error = validate_download_config(
        {"tiktok_downloader": {"base_url": "http://192.168.x.x:5555", "token": ""}}, partial=True
    )
    assert error is None
    assert validated["tiktok_downloader"]["base_url"] == "http://192.168.x.x:5555"
    _, error = validate_download_config({"tiktok_downloader": {"base_url": "ftp://x"}}, partial=True)
    assert error


# --- client error paths ---------------------------------------------------


def _patch_urlopen(monkeypatch, fake):
    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_client_maps_connection_error(monkeypatch):
    def fake(request, timeout=None):
        raise urllib.error.URLError("refused")

    _patch_urlopen(monkeypatch, fake)
    client = TikTokDownloaderClient("http://192.168.x.x:5555")
    status = client.check_connection()
    assert status["reachable"] is False
    assert "Unable to reach" in status["message"]


def test_client_maps_token_rejection_and_other_http(monkeypatch):
    client = TikTokDownloaderClient("http://192.168.x.x:5555", token="bad")

    def forbidden(request, timeout=None):
        raise _http_error(403)

    _patch_urlopen(monkeypatch, forbidden)
    with pytest.raises(TikTokDownloaderError, match="rejected the request token"):
        client.resolve_share("https://v.douyin.com/AbCd123/")

    def server_error(request, timeout=None):
        raise _http_error(500)

    _patch_urlopen(monkeypatch, server_error)
    with pytest.raises(TikTokDownloaderError, match="HTTP 500"):
        client.fetch_detail("7345678901234567890")


def test_client_maps_invalid_json(monkeypatch):
    def fake(request, timeout=None):
        return io.BytesIO(b"not-json")

    _patch_urlopen(monkeypatch, fake)
    client = TikTokDownloaderClient("http://192.168.x.x:5555")
    with pytest.raises(TikTokDownloaderError, match="invalid response"):
        client.fetch_mix("7200000000000000001")


def test_client_rejects_unexpected_payload_shape(monkeypatch):
    def fake(request, timeout=None):
        return FakeResponse({"message": "ok", "data": {"unexpected": True}})

    _patch_urlopen(monkeypatch, fake)
    client = TikTokDownloaderClient("http://192.168.x.x:5555")
    with pytest.raises(TikTokDownloaderError, match="unexpected response shape"):
        client.fetch_account("MS4wLjABAAAAxi")


def test_client_builds_expected_requests(monkeypatch):
    seen = []

    def fake(request, timeout=None):
        seen.append((request.full_url, request.get_method(), request.data.decode("utf-8")))
        if request.full_url.endswith("/share"):
            return FakeResponse({"message": "ok", "url": "https://www.douyin.com/video/7345678901234567890"})
        return FakeResponse({"message": "ok", "data": [{"id": "1", "downloads": ["https://cdn/x.mp4"]}]})

    _patch_urlopen(monkeypatch, fake)
    client = TikTokDownloaderClient("http://192.168.x.x:5555/", token="secret", timeout_sec=9)
    assert client.resolve_share("https://v.douyin.com/AbCd123/") == "https://www.douyin.com/video/7345678901234567890"
    client.fetch_account("MS4wLjABAAAAxi", tab="favorite", count=36)
    client.fetch_mix("7200000000000000001")
    client.fetch_detail("7345678901234567890", platform="tiktok")

    assert seen[0][0] == "http://192.168.x.x:5555/douyin/share"
    assert json.loads(seen[0][2]) == {"text": "https://v.douyin.com/AbCd123/"}
    assert seen[1][0] == "http://192.168.x.x:5555/douyin/account"
    assert json.loads(seen[1][2]) == {"sec_user_id": "MS4wLjABAAAAxi", "tab": "favorite", "count": 36}
    assert json.loads(seen[2][2]) == {"mix_id": "7200000000000000001", "count": 18}
    assert seen[3][0] == "http://192.168.x.x:5555/tiktok/detail"


# --- item normalization and filenames -------------------------------------


def test_normalize_work_items_filters_and_classifies():
    raw_items = [
        {"id": "a1", "desc": "图集: 海边/旅行", "create_timestamp": 1730000000, "type": "图集",
         "downloads": ["https://cdn/1.image", "", "https://cdn/2.jpeg"]},
        {"id": "a2", "desc": "live", "create_timestamp": 1730000100, "type": "实况",
         "downloads": ["https://cdn/v", "https://cdn/i.image"]},
        {"id": "a3", "desc": "视频", "create_timestamp": 1730000200, "type": "视频",
         "downloads": ["https://cdn/v2"]},
        {"id": "a4", "desc": "no urls", "create_timestamp": 1730000300, "downloads": []},
        "garbage",
    ]
    works = normalize_work_items(raw_items)
    assert [w["id"] for w in works] == ["a1", "a2", "a3"]
    assert works[0]["post_type"] == "图集" and len(works[0]["downloads"]) == 2
    assert works[0]["desc"] == "图集 海边 旅行"
    assert works[1]["post_type"] == "实况"
    assert works[2]["post_type"] == "视频"


def test_classify_post_type_fallbacks():
    assert classify_post_type({"type": "LivePhoto"}, 2) == "实况"
    assert classify_post_type({}, 1) == "视频"
    assert classify_post_type({}, 3) == "图集"


def test_build_work_filename_matches_pairing_convention():
    naming = {"created_at": "2024-10-27 12.34.56", "post_type": "图集", "owner": "UIDMS4wLjABA", "desc": "海边 旅行"}
    assert build_work_filename(naming, 2, 3, ".jpg") == "2024-10-27 12.34.56-图集-UIDMS4wLjABA-海边 旅行_2.jpg"
    single = dict(naming, post_type="视频", desc="")
    assert build_work_filename(single, 1, 1, ".mp4") == "2024-10-27 12.34.56-视频-UIDMS4wLjABA-work.mp4"


def test_guess_download_extension():
    assert guess_download_extension("https://cdn/x/~tplv-sharp.image") == ".jpg"
    assert guess_download_extension("https://cdn/x.awebp") == ".webp"
    assert guess_download_extension("https://cdn/x/photo.jpeg") == ".jpeg"
    assert guess_download_extension("https://cdn/obj/tos-abc", "图集") == ".jpg"
    assert guess_download_extension("https://cdn/obj/tos-abc", "视频") == ".mp4"


def test_format_timestamp_slug_handles_bad_input():
    assert format_timestamp_slug(0) == "1970-01-01 00.00.00"
    assert format_timestamp_slug("oops") == "1970-01-01 00.00.00"
    assert format_timestamp_slug(1730000000) != "1970-01-01 00.00.00"


# --- seed + service orchestration -----------------------------------------


class FakeManager:
    def __init__(self):
        self.calls = []

    def enqueue(self, url, *, save_mode, engine, cookie_mode, cookie_file="", retry_of="", output_token="", payload=None):
        self.calls.append({"url": url, "engine": engine, "payload": payload})
        return {"id": f"job{len(self.calls)}"}


def _config_store(tmp_path):
    return DownloadConfigStore(tmp_path / "download-config.json")


def test_seed_only_fills_untouched_keys(tmp_path):
    store = _config_store(tmp_path)
    seed_tiktok_downloader_config(store, {"base_url": "http://192.168.x.x:5555", "token": "abc"})
    assert store.get()["tiktok_downloader"]["base_url"] == "http://192.168.x.x:5555"

    store.set({"tiktok_downloader": {"base_url": "http://10.0.0.2:9000"}})
    seed_tiktok_downloader_config(store, {"base_url": "http://192.168.x.x:5555"})
    assert store.get()["tiktok_downloader"]["base_url"] == "http://10.0.0.2:9000"


def test_service_enqueue_from_url_detail(monkeypatch, tmp_path):
    store = _config_store(tmp_path)
    seed_tiktok_downloader_config(store, {"base_url": "http://192.168.x.x:5555"})
    manager = FakeManager()
    service = TikTokDownloadService(store, manager)

    def fake_fetch_detail(self, detail_id, platform="douyin"):
        assert detail_id == "7345678901234567890"
        return [{
            "id": "7345678901234567890", "desc": "海边 旅行", "create_timestamp": 1730000000,
            "type": "视频", "downloads": ["https://cdn/x.mp4"],
        }]

    monkeypatch.setattr(TikTokDownloaderClient, "fetch_detail", fake_fetch_detail)
    result = service.enqueue_from_url("https://www.douyin.com/video/7345678901234567890")
    assert result["kind"] == "detail" and result["items"] == 1 and result["queued"] == 1
    assert manager.calls[0]["engine"] == "tiktok-downloader"
    payload = manager.calls[0]["payload"]
    assert payload["downloads"] == ["https://cdn/x.mp4"]
    assert payload["naming"]["post_type"] == "视频"
    assert payload["naming"]["owner"] == "douyin"


def test_service_expands_share_links_and_truncates(monkeypatch, tmp_path):
    store = _config_store(tmp_path)
    seed_tiktok_downloader_config(store, {"base_url": "http://192.168.x.x:5555", "account_max_items": 2})
    manager = FakeManager()
    service = TikTokDownloadService(store, manager)

    monkeypatch.setattr(
        TikTokDownloaderClient, "resolve_share",
        lambda self, text: "https://www.douyin.com/user/MS4wLjABAAAAxi",
    )
    monkeypatch.setattr(
        TikTokDownloaderClient, "fetch_account",
        lambda self, sec_user_id, tab="post", count=18, platform="douyin": [
            {"id": str(i), "desc": f"作品{i}", "create_timestamp": 1730000000 + i,
             "type": "视频", "downloads": [f"https://cdn/{i}.mp4"]}
            for i in range(5)
        ],
    )
    result = service.enqueue_from_url("https://v.douyin.com/AbCd123/", tab="post", count=18)
    assert result["kind"] == "account" and result["items"] == 2 and result["truncated"] is True
    assert manager.calls[0]["payload"]["naming"]["owner"] == "UIDMS4wLjABAAAA"


def test_service_requires_configuration(tmp_path):
    service = TikTokDownloadService(_config_store(tmp_path), FakeManager())
    with pytest.raises(TikTokDownloaderError, match="not configured"):
        service.enqueue_from_url("https://www.douyin.com/user/MS4wLjABAAAAxi")


def test_service_rejects_unsupported_links(monkeypatch, tmp_path):
    store = _config_store(tmp_path)
    seed_tiktok_downloader_config(store, {"base_url": "http://192.168.x.x:5555"})
    service = TikTokDownloadService(store, FakeManager())
    with pytest.raises(ValueError):
        service.enqueue_from_url("https://example.com/watch?v=1")
    with pytest.raises(ValueError):
        service.enqueue_from_url("https://www.douyin.com/user/MS4wLjABAAAAxi", tab="stories")


# --- route integration (real app + real direct-download engine) ------------


class FakeMediaResponse:
    def __init__(self, body: bytes):
        self._stream = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def tiktok_client(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    cookie_root = tmp_path / "cookies"
    cookie_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("TIKLOCAL_COOKIE_DIR", str(cookie_root))

    app = create_app({
        "TESTING": True,
        "MEDIA_ROOT": media_root,
        "TIKTOK_DOWNLOADER": {"base_url": "http://192.168.x.x:5555", "token": ""},
    })
    manager = app.extensions["download_manager"]
    manager.start()
    try:
        yield app.test_client()
    finally:
        manager.close()


def _wait_job(client, job_id, timeout=3.0):
    import time as _time

    end = _time.time() + timeout
    while _time.time() < end:
        job = client.get(f"/api/download/jobs/{job_id}").get_json()["data"]["job"]
        if job["status"] in {"success", "failed", "canceled"}:
            return job
        _time.sleep(0.05)
    pytest.fail(f"Job did not finish: {job_id}")


def _mock_account_items(monkeypatch, count=2):
    items = [
        {
            "id": f"73{i:018d}",
            "desc": f"图集作品 {i}",
            "create_timestamp": 1730000000 + i,
            "type": "图集",
            "downloads": [f"https://cdn.douyinpic.com/a{i}.image", f"https://cdn.douyinpic.com/b{i}.image"],
        }
        for i in range(1, count + 1)
    ]
    monkeypatch.setattr(
        TikTokDownloaderClient, "fetch_account",
        lambda self, sec_user_id, tab="post", count=18, platform="douyin": items,
    )
    return items


def test_tiktok_status_and_probe_report_sidecar_state(tiktok_client, monkeypatch):
    def unreachable(request, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    status = tiktok_client.get("/api/download/tiktok/status").get_json()["data"]
    assert status == {
        "configured": True, "base_url": "http://192.168.x.x:5555", "reachable": False,
        "message": "Unable to reach TikTokDownloader at http://192.168.x.x:5555.",
    }
    probe = tiktok_client.post("/api/download/probe").get_json()["data"]
    assert probe["tiktok_downloader"]["configured"] is True
    assert probe["tiktok_downloader"]["reachable"] is False


def test_tiktok_enqueue_downloads_album_into_library(tiktok_client, monkeypatch):
    _mock_account_items(monkeypatch, count=2)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: FakeMediaResponse(b"image-bytes")
    )

    res = tiktok_client.post(
        "/api/download/tiktok",
        json={"url": "https://www.douyin.com/user/MS4wLjABAAAAxi", "tab": "post", "count": 18},
    )
    data = res.get_json()
    assert res.status_code == 200 and data["success"] is True
    assert data["data"]["kind"] == "account" and data["data"]["items"] == 2

    jobs = data["data"]["jobs"]
    assert [job["engine"] for job in jobs] == ["tiktok-downloader", "tiktok-downloader"]
    final = _wait_job(tiktok_client, jobs[0]["id"])
    assert final["status"] == "success", final["error_message"]
    assert final["file_count"] == 2
    names = final["output_files_rel"]
    assert all("-图集-" in name for name in names)
    assert names[0].endswith("_1.jpg") and names[1].endswith("_2.jpg")

    second = _wait_job(tiktok_client, jobs[1]["id"])
    assert second["status"] == "success", second["error_message"]
    indexed = tiktok_client.get("/api/library/items?scope=all&q=图集作品&offset=0&limit=50")
    indexed_names = [item["name"] for item in indexed.get_json()["data"]["items"]]
    assert len(indexed_names) == 4
    assert all("-图集-" in name for name in indexed_names)


def test_tiktok_route_error_paths(tiktok_client, monkeypatch):
    res = tiktok_client.post("/api/download/tiktok", json={"url": "https://example.com/watch?v=1"})
    assert res.status_code == 400
    assert res.get_json()["error"]

    res = tiktok_client.post("/api/download/tiktok", json={})
    assert res.status_code == 400

    def unreachable(request, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    res = tiktok_client.post(
        "/api/download/tiktok", json={"url": "https://www.douyin.com/user/MS4wLjABAAAAxi"}
    )
    assert res.status_code == 502
    assert "Unable to reach" in res.get_json()["error"]


def test_tiktok_config_roundtrip(tiktok_client):
    current = tiktok_client.get("/api/download/tiktok/config").get_json()["data"]["tiktok_downloader"]
    assert current["base_url"] == "http://192.168.x.x:5555"

    res = tiktok_client.post(
        "/api/download/tiktok/config", json={"tiktok_downloader": {"base_url": "http://10.0.0.9:5555", "token": "abc"}}
    )
    assert res.status_code == 200
    updated = res.get_json()["data"]["tiktok_downloader"]
    assert updated["base_url"] == "http://10.0.0.9:5555" and updated["token"] == "abc"

    res = tiktok_client.post("/api/download/tiktok/config", json={"tiktok_downloader": {"base_url": "ftp://bad"}})
    assert res.status_code == 400


def test_tiktok_service_absent_when_unconfigured(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("TIKLOCAL_COOKIE_DIR", str(tmp_path))
    app = create_app({"TESTING": True, "MEDIA_ROOT": media_root})
    manager = app.extensions["download_manager"]
    manager.start()
    try:
        client = app.test_client()
        res = client.post("/api/download/tiktok", json={"url": "https://www.douyin.com/user/MS4wLjABAAAAxi"})
        assert res.status_code == 502
        assert "not configured" in res.get_json()["error"]
        status = client.get("/api/download/tiktok/status").get_json()["data"]
        assert status["configured"] is False
    finally:
        manager.close()
