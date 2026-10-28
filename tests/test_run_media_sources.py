"""The launch banner and the running app must agree on the media folder list.

Folders added from Settings live in ``<instance>/media_sources.json``. The app
reads that file; the CLI used to only look at the launch arguments, so the banner
listed one folder while the server served several, and a bare ``tiklocal`` start
demanded a directory argument even though the saved list had one.
"""

import json

import pytest

from tiklocal import run as run_module
from tiklocal.paths import get_media_sources_path


@pytest.fixture
def instance(tmp_path, monkeypatch):
    directory = tmp_path / "instance"
    directory.mkdir()
    monkeypatch.setenv("TIKLOCAL_INSTANCE", str(directory))
    return directory


def _write_store(sources, *, removed=()):
    path = get_media_sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "sources": list(sources), "removed": list(removed)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _folder(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    return str(folder)


def test_saved_folders_are_added_to_the_launch_list(instance, tmp_path):
    saved = _folder(tmp_path, "saved")
    launch = _folder(tmp_path, "launch")
    _write_store([{"id": "default", "name": "Default", "path": saved}])

    merged = run_module.fold_stored_media_sources([{"id": "default", "name": "Default", "path": launch}])

    # The folder the user kept in Settings wins over the argument, which is what
    # the app itself does at startup.
    assert merged == [{"id": "default", "name": "Default", "path": saved}]


def test_a_bare_start_uses_the_saved_folders(instance, tmp_path):
    saved = _folder(tmp_path, "saved")
    _write_store([{"id": "default", "name": "Default", "path": saved}])

    merged = run_module.fold_stored_media_sources([])

    assert merged == [{"id": "default", "name": "Default", "path": saved}]


def test_removed_folders_stay_removed(instance, tmp_path):
    kept = _folder(tmp_path, "kept")
    dropped = _folder(tmp_path, "dropped")
    _write_store(
        [{"id": "default", "name": "Default", "path": kept}],
        removed=["--media-source"],
    )

    merged = run_module.fold_stored_media_sources([
        {"id": "--media-source", "name": "--media-source", "path": dropped},
    ])

    assert [source["id"] for source in merged] == ["default"]


def test_a_corrupt_store_falls_back_to_the_launch_list(instance, tmp_path):
    launch = _folder(tmp_path, "launch")
    path = get_media_sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    merged = run_module.fold_stored_media_sources([{"id": "default", "name": "Default", "path": launch}])

    # An unreadable file must never turn into "no folders at all".
    assert merged == [{"id": "default", "name": "Default", "path": launch}]
