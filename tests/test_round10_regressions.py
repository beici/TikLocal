import threading
from pathlib import Path

from tiklocal.services.database import AppDatabase
from tiklocal.services.download_tools import claim_available_path
from tiklocal.services.downloader import gallery_media_outputs
from tiklocal.services.favorites import FavoriteService
from tiklocal.services.library_index import MediaIndexStore


class _StubLibrary:
    def canonicalize_uri(self, uri: str) -> str:
        return uri

    def canonicalize_many(self, uris) -> set[str]:
        return set(uris)


def _record(uri: str, year: int, day: str = '06-15', *, captured_at: float = 0.0) -> dict:
    name = uri.rsplit('/', 1)[-1]
    return {
        'uri': uri,
        'source_id': '@default',
        'rel_path': uri.split('/', 1)[-1],
        'filename': name,
        'parent_path': '',
        'media_type': 'image',
        'extension': '.jpg',
        'size_bytes': 1,
        'mtime': captured_at,
        'captured_at': captured_at,
        'captured_local_date': f'{year}-{day}',
    }


def _store(tmp_path):
    database = AppDatabase(tmp_path / 'index.sqlite3')
    database.migrate()
    return MediaIndexStore(database)


def test_on_this_day_keeps_every_year_when_one_year_is_busy(tmp_path):
    store = _store(tmp_path)
    records = [
        _record(f'@default/{year}/{index:02d}.jpg', year, captured_at=float(year * 1000 + index))
        for year in (2021, 2022, 2023)
        for index in range(5)
    ]
    store.replace_snapshot(records)

    # A global quota lets the newest year eat the whole budget.
    assert {row['captured_local_date'][:4] for row in store.on_days(['06-15'], '2026', limit=2)} == {'2023'}
    # A per-year quota keeps one row for every year.
    years = {row['captured_local_date'][:4] for row in store.on_days(['06-15'], '2026', per_year=2)}
    assert years == {'2021', '2022', '2023'}


def test_snapshot_skips_names_sqlite_cannot_store(tmp_path):
    store = _store(tmp_path)
    store.replace_snapshot([
        _record('@default/bad\ud800.jpg', 2024),
        _record('@default/padded.jpg ', 2024),
        _record('@default/ok.jpg', 2024),
    ])

    assert [row['name'] for row in store.records()] == ['@default/ok.jpg']


def test_claim_available_path_never_hands_out_the_same_name(tmp_path):
    target = tmp_path / 'clip.mp4'
    claimed: list[Path] = []
    lock = threading.Lock()

    def claim() -> None:
        path = claim_available_path(target)
        with lock:
            claimed.append(path)

    threads = [threading.Thread(target=claim) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == 20
    assert len(set(claimed)) == 20
    assert all(path.exists() for path in claimed)


def test_gallery_outputs_ignore_cookie_copy_and_log(tmp_path):
    temp_dir = tmp_path / 'job'
    temp_dir.mkdir()
    (temp_dir / 'image.jpg').write_bytes(b'image')
    (temp_dir / 'archive.zip').write_bytes(b'archive')
    log_file = temp_dir / 'gallery-dl.log'
    log_file.write_text('log', encoding='utf-8')
    cookie_copy = temp_dir / 'cookies.txt'
    cookie_copy.write_text('session', encoding='utf-8')

    outputs = gallery_media_outputs(temp_dir, log_file, cookie_copy)

    assert [path.name for path in outputs] == ['image.jpg']


def test_a_corrupt_favorites_file_self_heals(tmp_path):
    path = tmp_path / 'favorites.json'
    path.write_text('{ this is not json', encoding='utf-8')
    service = FavoriteService(path, _StubLibrary())

    assert service.load() == set()
    assert service.toggle('@default/a.jpg') is True
    assert service.load() == {'@default/a.jpg'}
    assert any(item.name.endswith('.corrupt') for item in tmp_path.iterdir())
