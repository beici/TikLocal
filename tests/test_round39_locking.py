"""Regressions for the round-39 review: lock scopes and the delete/scan race."""
import threading

from tiklocal.services.database import AppDatabase
from tiklocal.services.library_index import MediaIndexStore


def _record(uri: str) -> dict:
    return {
        "uri": uri,
        "source_id": "@default",
        "rel_path": uri.split("/", 1)[1],
        "filename": uri.rsplit("/", 1)[-1],
        "parent_path": "",
        "media_type": "image",
        "extension": ".jpg",
        "size_bytes": 10,
        "mtime": 1_700_000_000.0,
    }


def _store(tmp_path) -> MediaIndexStore:
    database = AppDatabase(tmp_path / "index.sqlite3")
    database.migrate()
    return MediaIndexStore(database)


def test_a_snapshot_collected_before_a_delete_does_not_resurrect_the_row(tmp_path):
    """The scan walks the tree, then writes; a delete landing in between used to lose.

    `replace_snapshot` is handed the records the walker collected. If the file went to
    the trash while the walker was still running, the row was written back and the grid
    showed a card whose file answers 404 until the next scan.
    """
    store = _store(tmp_path)
    uri = "@default/photo.jpg"

    store.replace_snapshot([_record(uri)], synced_source_ids={"@default"})
    assert [item["name"] for item in store.records()] == [uri]

    # The walker collected the record, the user deleted the file, then the walker wrote.
    stale_snapshot = [_record(uri)]
    assert store.delete(uri) is True
    store.replace_snapshot(stale_snapshot, synced_source_ids={"@default"})

    assert store.records() == []
    # The intent is drained: a later scan that legitimately finds the file indexes it.
    store.replace_snapshot([_record(uri)], synced_source_ids={"@default"})
    assert [item["name"] for item in store.records()] == [uri]


def test_replace_snapshot_writes_every_row_in_one_transaction(tmp_path):
    """Batching must not drop or reorder rows (the per-row loop was 2 statements each)."""
    store = _store(tmp_path)
    records = [_record(f"@default/photo-{index:03d}.jpg") for index in range(250)]

    result = store.replace_snapshot(records, synced_source_ids={"@default"})

    assert result["indexed"] == 250
    assert result["deleted"] == 0
    assert len(store.records()) == 250


def test_a_delete_racing_a_scan_from_another_thread_stays_deleted(tmp_path):
    """The guard has to survive the real interleaving, not just the sequential one."""
    store = _store(tmp_path)
    uri = "@default/racing.jpg"
    store.replace_snapshot([_record(uri)], synced_source_ids={"@default"})

    started = threading.Event()
    finished = threading.Event()

    def scan() -> None:
        snapshot = [_record(uri)]
        started.set()
        finished.wait(2.0)
        store.replace_snapshot(snapshot, synced_source_ids={"@default"})

    worker = threading.Thread(target=scan)
    worker.start()
    assert started.wait(2.0)
    assert store.delete(uri) is True
    finished.set()
    worker.join(5.0)

    assert not worker.is_alive()
    assert store.records() == []
