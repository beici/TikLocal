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
    """Raised when another process already owns the instance directory."""


def _lock(fd: int) -> None:
    if os.name == 'nt':
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire_instance_lock(instance_path: Path) -> IO[bytes]:
    """Take an exclusive lock for ``instance_path`` or raise ``InstanceLockError``.

    The returned file object must stay open for as long as the server runs; the
    OS releases the lock when the process exits, even after a crash.
    """
    instance_path = Path(instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)
    lock_path = instance_path / 'tiklocal.lock'
    handle = open(lock_path, 'a+b')
    try:
        handle.seek(0)
        if handle.read(1) == b'':
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        _lock(handle.fileno())
    except OSError as error:
        handle.close()
        raise InstanceLockError(
            f'The instance directory {instance_path} is already in use by another '
            f'TikLocal process ({error}). Stop it first, or start this one with a '
            'different --instance directory.'
        ) from error
    return handle
