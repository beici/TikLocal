import datetime
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from tiklocal.services.json_storage import write_json_atomic


AUTH_FILE_VERSION = 1
MIN_PASSWORD_LENGTH = 8
#: Password a fresh instance starts with. It is public knowledge, so it only keeps
#: strangers out until you set your own: `tiklocal auth set-password`, or
#: TIKLOCAL_AUTH_PASSWORD before the first start.
DEFAULT_PASSWORD = 'admin666'


@dataclass(frozen=True)
class AuthBootstrap:
    created: bool
    #: The default password this run installed because none was supplied.
    default_password: str | None = None


class AuthStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict | None = None
        self._mtime_ns: int | None = None

    def _load(self) -> dict | None:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            self._data = None
            self._mtime_ns = None
            return None
        except OSError as exc:
            # An antivirus or search indexer holding the file for a moment must
            # not turn every request into a 500: the copy read a moment ago is
            # still the truth, and the next stat will notice a real change.
            # `Path.exists()` cannot answer the missing-file case above: it
            # swallows this same OSError and reports "no file", which would turn
            # a sharing violation into what looks like a fresh install.
            if self._data is not None:
                return self._data
            raise RuntimeError(f'Authentication configuration cannot be read: {self.path}') from exc
        if self._data is not None and self._mtime_ns == mtime_ns:
            return self._data
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f'Authentication configuration cannot be read: {self.path}') from exc
        except OSError as exc:
            if self._data is not None:
                return self._data
            raise RuntimeError(f'Authentication configuration cannot be read: {self.path}') from exc
        required = {'password_hash', 'secret_key', 'revision'}
        if not isinstance(data, dict) or not required.issubset(data):
            raise RuntimeError(f'Authentication configuration is incomplete: {self.path}')
        self._data = data
        self._mtime_ns = mtime_ns
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A fixed `.tmp` name let two writers replace each other's half-written file,
        # and a sharing violation left that file (password hash and secret_key in
        # clear JSON) behind for good. write_json_atomic retries the replace, always
        # removes its own temporary file, and applies the private mode before the
        # replace so the target is never briefly world-readable.
        write_json_atomic(self.path, data, mode=0o600)
        self._data = data
        try:
            self._mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime_ns = None

    def ensure(self, initial_password: str | None = None) -> AuthBootstrap:
        """Create the auth file on first run; returns whether this call created it.

        The check-then-create used to run unguarded, so a fresh instance started by the
        server and by `tiklocal auth status` at the same moment produced two different
        `secret_key`s: the server kept the one it read at start-up and the loser's write
        won on disk, invalidating every session at the next restart. A claim file with
        ``O_EXCL`` makes exactly one of them the creator.
        """
        if self._load() is not None:
            return AuthBootstrap(created=False)
        supplied = str(initial_password or '').strip()
        password = supplied or DEFAULT_PASSWORD
        self._validate_password(password)
        claim = self.path.with_name(self.path.name + '.claim')
        claim.parent.mkdir(parents=True, exist_ok=True)
        fd: int | None = None
        for attempt in range(3):
            try:
                fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                # Another process is writing it right now; wait for its atomic replace.
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if self._load() is not None:
                        return AuthBootstrap(created=False)
                    time.sleep(0.05)
                # A crashed run left the claim behind (the pid is not in the name, so it
                # cannot be checked): take it over, but never loop forever.
                try:
                    claim.unlink()
                except OSError:
                    pass
            except OSError:
                # Read-only data directory: the caller's own error reporting is better
                # than a claim-file traceback.
                break
        try:
            if self._load() is not None:
                return AuthBootstrap(created=False)
            now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
            self._save({
                'version': AUTH_FILE_VERSION,
                'password_hash': generate_password_hash(password),
                'secret_key': secrets.token_hex(32),
                'revision': 1,
                'created_at': now,
                'updated_at': now,
            })
            return AuthBootstrap(created=True, default_password=None if supplied else password)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                claim.unlink()
            except OSError:
                pass

    def set_password(self, password: str) -> None:
        self._validate_password(password)
        current = self._load() or {}
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
        self._save({
            'version': AUTH_FILE_VERSION,
            'password_hash': generate_password_hash(password),
            'secret_key': current.get('secret_key') or secrets.token_hex(32),
            'revision': int(current.get('revision') or 0) + 1,
            'created_at': current.get('created_at') or now,
            'updated_at': now,
        })

    def verify(self, password: str) -> bool:
        data = self._load()
        return bool(data and check_password_hash(str(data['password_hash']), str(password or '')))

    @property
    def secret_key(self) -> str:
        data = self._load()
        if not data:
            raise RuntimeError('Authentication has not been initialized')
        return str(data['secret_key'])

    @property
    def revision(self) -> int:
        data = self._load()
        if not data:
            raise RuntimeError('Authentication has not been initialized')
        return int(data['revision'])

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(str(password or '')) < MIN_PASSWORD_LENGTH:
            raise ValueError(f'Access password must contain at least {MIN_PASSWORD_LENGTH} characters')
