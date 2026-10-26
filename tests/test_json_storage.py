import json
import threading
from pathlib import Path

from tiklocal.services.json_storage import read_json_file, write_json_atomic


def test_transient_sharing_violation_does_not_quarantine_the_store(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / 'store.json'
    store.write_text(json.dumps({'marker': 'USER-DATA'}), encoding='utf-8')

    real_read = Path.read_text
    calls = {'n': 0}

    def flaky_read(self, *args, **kwargs):  # noqa: ANN001 - patched method
        if self == store and calls['n'] < 2:
            calls['n'] += 1
            raise PermissionError(13, 'The process cannot access the file', str(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', flaky_read)
    value = read_json_file(store, {'fallback': True})
    monkeypatch.undo()

    assert value == {'marker': 'USER-DATA'}
    assert store.exists(), 'a transient OSError must never rename the live store'
    assert not list(tmp_path.glob('store.json.corrupt*'))


def test_persistent_sharing_violation_returns_default_without_quarantine(tmp_path: Path, monkeypatch) -> None:
    store = tmp_path / 'store.json'
    store.write_text(json.dumps({'marker': 'USER-DATA'}), encoding='utf-8')

    def always_fail(self, *args, **kwargs):  # noqa: ANN001 - patched method
        raise PermissionError(13, 'The process cannot access the file', str(self))

    monkeypatch.setattr(Path, 'read_text', always_fail)
    value = read_json_file(store, {'fallback': True})
    monkeypatch.undo()

    assert value == {'fallback': True}
    assert store.exists()
    assert not list(tmp_path.glob('store.json.corrupt*'))


def test_invalid_utf8_is_quarantined_instead_of_raising(tmp_path: Path) -> None:
    store = tmp_path / 'store.json'
    store.write_bytes(b'\xff\xfe{"not": "utf-8"}')

    value = read_json_file(store, {'fallback': True})

    assert value == {'fallback': True}
    assert not store.exists()
    quarantined = list(tmp_path.glob('store.json.corrupt*'))
    assert quarantined, 'corrupt bytes must be preserved, not overwritten'
    assert quarantined[0].read_bytes().startswith(b'\xff\xfe')


def test_concurrent_writers_keep_the_store_intact(tmp_path: Path) -> None:
    store = tmp_path / 'store.json'
    store.write_text(json.dumps({'marker': 'USER-DATA'}), encoding='utf-8')
    failures: list[Exception] = []

    def writer(index: int) -> None:
        for round_index in range(40):
            try:
                write_json_atomic(store, {'writer': index, 'round': round_index})
            except Exception as error:  # noqa: BLE001 - recorded and asserted below
                failures.append(error)

    def reader() -> None:
        for _ in range(400):
            read_json_file(store, {'fallback': True})

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(6)]
    threads.append(threading.Thread(target=reader))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures, failures[:3]
    assert store.exists()
    assert 'writer' in read_json_file(store, {})
    assert not list(tmp_path.glob('store.json.corrupt*')), 'live store must never be quarantined'
