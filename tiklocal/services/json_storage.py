import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: Path, value: object) -> None:
    """Publish complete JSON or leave the previous file intact."""
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)


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
    return target


def read_json_file(path: Path, default: object, *, expected_type: type | tuple[type, ...] = dict) -> object:
    """Read a JSON store, quarantining (never silently discarding) corrupt bytes."""

    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return default
    except OSError:
        quarantine_corrupt_file(path)
        return default
    try:
        value = json.loads(raw)
    except ValueError:
        quarantine_corrupt_file(path)
        return default
    if not isinstance(value, expected_type):
        quarantine_corrupt_file(path)
        return default
    return value
