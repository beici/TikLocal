import json
import os
import random
import time
from pathlib import Path

# A reader holding the target open (antivirus, Windows Search, another request
# mid-read) makes `os.replace` fail with a sharing violation on Windows. Retry
# with a growing, jittered delay instead of failing the save, and never treat
# that as corruption. Six threads saving the same store in a loop exhaust a flat
# 5x30ms budget, so the delay doubles up to a cap.
_REPLACE_ATTEMPTS = 8
_REPLACE_DELAY_SEC = 0.02
_REPLACE_MAX_DELAY_SEC = 0.4
_WRITE_ATTEMPTS = 3
_READ_ATTEMPTS = 5
_READ_DELAY_SEC = 0.05
_QUARANTINE_KEEP = 3
_TEMP_COUNTER = 0


class StoreUnreadableError(OSError):
    """The store exists but could not be read.

    Callers must not fall back to an empty default: the next save would publish
    that empty value over the real data. Subclassing OSError keeps the existing
    `except OSError` -> 503 route handlers working.
    """


def write_json_atomic(path: Path, value: object) -> None:
    """Publish complete JSON or leave the previous file intact."""
    global _TEMP_COUNTER
    _TEMP_COUNTER += 1
    # `tempfile` is deliberately avoided: when the directory itself denies writes,
    # CPython's mkdtemp/mkstemp treat PermissionError as "name taken" and retry
    # TMP_MAX (2**31-1) times, pinning a request thread for days. A sibling name
    # built from pid+counter lets PermissionError surface immediately.
    temporary = path.with_name(f'{path.name}.{os.getpid()}.{_TEMP_COUNTER}.tmp')
    try:
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                temporary.write_text(payload, encoding='utf-8')
                break
            except PermissionError:
                if attempt == _WRITE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY_SEC)
        delay = _REPLACE_DELAY_SEC
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(delay * (1 + random.random() * 0.5))
                delay = min(delay * 2, _REPLACE_MAX_DELAY_SEC)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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
    except UnicodeDecodeError:
        # Not UTF-8 at all: that is corruption, not a sharing violation.
        quarantine_corrupt_file(path)
        return default
    except OSError as error:
        # A transient sharing violation is not corruption: quarantining here
        # would rename a perfectly good store and hand back the defaults, which
        # the next save would then write over. Retry, then refuse to answer:
        # returning the default would let the caller erase real data on save.
        last_error: OSError = error
        for attempt in range(_READ_ATTEMPTS):
            time.sleep(_READ_DELAY_SEC)
            try:
                raw = path.read_text(encoding='utf-8')
                break
            except FileNotFoundError:
                return default
            except UnicodeDecodeError:
                quarantine_corrupt_file(path)
                return default
            except OSError as retry_error:
                last_error = retry_error
                if attempt == _READ_ATTEMPTS - 1:
                    raise StoreUnreadableError(
                        f'{path.name} exists but cannot be read; refusing to treat it as empty.'
                    ) from last_error
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
