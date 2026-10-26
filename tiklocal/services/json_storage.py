import json
import os
import tempfile
import time
from pathlib import Path

# A reader holding the target open (antivirus, Windows Search, another request
# mid-read) makes `os.replace` fail with a sharing violation on Windows. Retry
# briefly instead of failing the save, and never treat that as corruption.
_REPLACE_ATTEMPTS = 5
_REPLACE_DELAY_SEC = 0.03
_READ_ATTEMPTS = 3
_READ_DELAY_SEC = 0.02
_QUARANTINE_KEEP = 3


def write_json_atomic(path: Path, value: object) -> None:
    """Publish complete JSON or leave the previous file intact."""
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY_SEC)


def quarantine_corrupt_file(path: Path) -> Path | None:
    """Move an unreadable store aside so the next write cannot erase it.

    A truncated or zero-byte file usually means the process died mid-write, the
    disk filled up, or an antivirus held the handle. Treating it as "empty" and
    saving over it destroys whatever the user had, so keep the bytes under a
    `.corrupt` suffix (never overwriting an earlier quarantine) and start fresh.
    """

    if not path.exists():
        return None
    target = path.with_name(f'{path.name}.corrupt')
    index = 1
    while target.exists():
        target = path.with_name(f'{path.name}.corrupt.{index}')
        index += 1
    try:
        path.replace(target)
    except OSError:
        return None
    _prune_quarantine(path)
    return target


def _prune_quarantine(path: Path) -> None:
    """Keep only the newest quarantined copies so they cannot grow without bound."""

    copies = sorted(
        (candidate for candidate in path.parent.glob(f'{path.name}.corrupt*') if candidate.is_file()),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for stale in copies[_QUARANTINE_KEEP:]:
        try:
            stale.unlink()
        except OSError:
            continue


def read_json_file(path: Path, default: object, *, expected_type: type | tuple[type, ...] = dict) -> object:
    """Read a JSON store, quarantining (never silently discarding) corrupt bytes."""

    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return default
    except OSError:
        # A transient sharing violation is not corruption: quarantining here
        # would rename a perfectly good store and hand back the defaults, which
        # the next save would then write over. Retry, then leave the file alone.
        for attempt in range(_READ_ATTEMPTS):
            if attempt == _READ_ATTEMPTS - 1:
                return default
            time.sleep(_READ_DELAY_SEC)
            try:
                raw = path.read_text(encoding='utf-8')
                break
            except FileNotFoundError:
                return default
            except OSError:
                continue
    try:
        value = json.loads(raw)
    except ValueError:
        quarantine_corrupt_file(path)
        return default
    if not isinstance(value, expected_type):
        quarantine_corrupt_file(path)
        return default
    return value
