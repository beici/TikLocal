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
#: The password every release up to 0.5.x installed on a fresh instance. It is
#: public knowledge (it was in the install docs), so it must never be installed
#: again: first start now generates one. Kept only so an existing install can be
#: told that it is still using it (``AuthStore.uses_published_default``).
LEGACY_DEFAULT_PASSWORD = 'admin666'
#: Ambiguous glyphs (0/O, 1/l/I) are left out: this password is typed by hand
#: from a terminal banner, and one misread character is a lockout.
_PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
#: 12 characters over 31 symbols is ~59 bits. Long enough for a LAN service on a
#: page behind a login form, short enough to copy from a terminal by eye.
GENERATED_PASSWORD_LENGTH = 12


def generate_password(length: int = GENERATED_PASSWORD_LENGTH) -> str:
    """Return a random password from the unambiguous alphabet."""
    return ''.join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


@dataclass(frozen=True)
class AuthBootstrap:
    created: bool
    #: The password this call generated because none was supplied. It is only
    #: known here: the file stores a hash, so the caller must show it once.
    generated_password: str | None = None


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

        With no password supplied this generates a random one instead of
        installing a published constant: the old default was in the install docs,
        so every fresh instance on a 0.0.0.0 bind (the default) was open to
        anyone on the network until the owner happened to change it. The
        generated password is returned for the caller to print, and is otherwise
        unrecoverable: only its hash reaches the file.

        The check-then-create used to run unguarded, so a fresh instance started by the
        server and by `tiklocal auth status` at the same moment produced two different
        `secret_key`s: the server kept the one it read at start-up and the loser's write
        won on disk, invalidating every session at the next restart. A claim file with
        ``O_EXCL`` makes exactly one of them the creator.
        """
        if self._load() is not None:
            return AuthBootstrap(created=False)
        supplied = str(initial_password or '').strip()
        password = supplied or generate_password()
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
                # Recorded so a later `tiklocal auth status` can say the password
                # exists but was never shown to this user, instead of leaving a
                # lockout with no explanation. Only the hash is stored, so the
                # value itself is gone the moment the banner scrolls away.
                'password_generated': not supplied,
                'created_at': now,
                'updated_at': now,
            })
            return AuthBootstrap(created=True, generated_password=None if supplied else password)
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
            # It is the owner's own password now, so the "generated, never shown"
            # hint no longer applies.
            'password_generated': False,
            'created_at': current.get('created_at') or now,
            'updated_at': now,
        })

    def verify(self, password: str) -> bool:
        data = self._load()
        return bool(data and check_password_hash(str(data['password_hash']), str(password or '')))

    def uses_published_default(self) -> bool:
        """True while this instance still accepts the password every release published.

        There is no migration that can change it for the user (the plaintext is
        gone), so start-up says so instead of letting an old install stay open.
        """
        return self.verify(LEGACY_DEFAULT_PASSWORD)

    def password_was_generated(self) -> bool:
        """True when the stored password is a generated one nobody may have seen.

        It cannot be shown again: only the hash is on disk. The point of asking is
        to tell the owner how to get back in (`auth set-password`) rather than
        leaving a fresh install looking broken.
        """
        data = self._load() or {}
        return bool(data.get('password_generated'))

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
