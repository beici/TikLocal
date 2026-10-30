"""Single-instance lock for one TikLocal instance directory.

Two servers sharing an instance directory corrupt each other: the second one
marks the first one's running jobs as "interrupted by a restart" (see
``DownloadManager._load_history``) and every ``download_jobs.json`` write is a
whole-list overwrite, so the two processes delete each other's jobs. The lock is
taken by the CLI entry point before the app is built and held for the process
lifetime.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


class InstanceLockError(RuntimeError):
    """Raised when the instance directory cannot be locked.

    Either another process already owns it, or the directory is not writable by
    the account running this one (a bind mount owned by another uid, a read-only
    volume).
    """


def _lock(fd: int) -> None:
    if os.name == 'nt':
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unwritable_message(instance_path: Path, error: OSError) -> str:
    """Explain a data directory this process cannot write into.

    A Docker bind mount owned by another uid, a read-only volume and a full
    volume all arrive here, and all three used to escape as a bare traceback
    from the CLI entry point.
    """
    return (
        f'Cannot write the instance directory {instance_path} ({error}). The account '
        'that runs TikLocal must be able to create files in it; check that it owns '
        'the folder and that the volume is not full.'
    )


def _directory_is_writable(instance_path: Path) -> bool:
    """Say whether a new file can be created in ``instance_path`` right now.

    Used only on a failure path, to tell "another process holds the lock file"
    apart from "this account cannot write the data folder at all" — the first
    needs the other process stopped, the second needs the folder's owner fixed.
    """
    probe = instance_path / '.lock-probe'
    try:
        with open(probe, 'wb'):
            pass
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        pass
    return True


def _explain(instance_path: Path, error: OSError) -> InstanceLockError:
    """Name the problem behind an OSError from the lock file.

    Three situations fail here and they need different fixes: another process
    holds the lock, the data folder belongs to another account (the Docker bind
    mount owned by a foreign uid), or the volume is full. Windows reports the
    first one as a plain ``Permission denied`` on open/read/write without a
    filename, so the folder is probed rather than trusting the error code.
    """
    if not _directory_is_writable(instance_path):
        return InstanceLockError(_unwritable_message(instance_path, error))
    return InstanceLockError(
        f'The instance directory {instance_path} is already in use by another '
        f'TikLocal process, or its lock file is not writable ({error}). Stop the '
        'other process first, or start this one with a different --instance '
        'directory.'
    )


def acquire_instance_lock(instance_path: Path) -> IO[bytes]:
    """Take an exclusive lock for ``instance_path`` or raise ``InstanceLockError``.

    The returned file object must stay open for as long as the server runs; the
    OS releases the lock when the process exits, even after a crash.
    """
    instance_path = Path(instance_path)
    lock_path = instance_path / 'tiklocal.lock'
    handle = None
    try:
        instance_path.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, 'a+b')
        handle.seek(0)
        if handle.read(1) == b'':
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        _lock(handle.fileno())
    except OSError as error:
        if handle is not None:
            handle.close()
        raise _explain(instance_path, error) from error
    return handle
