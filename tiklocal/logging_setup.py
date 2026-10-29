"""Process-wide logging setup.

Flask's default handler writes to stderr, and waitress calls ``logging.basicConfig()``
when it starts serving. Both end up on the root logger, so every ``app.logger``
record was printed twice -- once with a timestamp, once as ``LEVEL:logger:message``.
Nothing was written to disk either, so an exception inside a background worker was
lost as soon as the terminal closed.

Configuring the root logger before the first ``app.logger`` access makes Flask skip
its default handler (``has_level_handler`` sees ours) and makes waitress' late
``basicConfig()`` a no-op.
"""

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = '[%(asctime)s] %(levelname)s in %(name)s: %(message)s'
_CONFIGURED_FLAG = '_tiklocal_logging_configured'
# One file per process. A single fixed name meant the server and `tiklocal thumbs`
# rotated the same file: one of them won the rename and the other's records landed in
# `tiklocal.log.1`, or the rotation failed on every record and the log died quietly.
LOG_FILE_NAME = f'tiklocal.{os.getpid()}.log'
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
# Process files kept on disk (each with its .1-.3 rotations).
KEEP_LOG_FILES = 4

# Credentials reach the log through exception messages and request dumps far too
# easily (e.g. a failing provider call echoes the URL with its key in the query).
# Masking happens on the formatted text so both handlers get it for free.
_SENSITIVE_PATTERN = re.compile(
    r'(?i)\b(token|password|passwd|secret|api[_-]?key|cookie|authorization)'
    r'(\s*[=:]\s*)'
    r'([^\s,;&"\')]+)'
)
_BEARER_PATTERN = re.compile(r'(?i)\b(bearer)\s+([A-Za-z0-9._\-]{8,})')


def redact_text(text: str) -> str:
    """Mask credential-looking fragments in an already rendered string."""
    text = _BEARER_PATTERN.sub(r'\1 ***', text)
    return _SENSITIVE_PATTERN.sub(lambda match: f'{match.group(1)}{match.group(2)}***', text)


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the final line, traceback included.

    Filtering ``record.getMessage()`` alone left every ``logger.exception()``
    traceback and every dict/JSON argument untouched, which is exactly how a
    provider URL with its key ends up in the log file.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


class RedactFilter(logging.Filter):
    """Replace credential-looking values in log messages with ``***``."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact_text(message)
        if redacted != message:
            # The arguments were already interpolated, so drop them: keeping them
            # would make ``getMessage()`` raise on the next handler.
            record.msg = redacted
            record.args = ()
        return True


class _SharedRotatingFileHandler(RotatingFileHandler):
    """Rotate the shared log file without letting a lock kill logging for good.

    Every CLI subcommand configures logging, so the server and a short-lived command
    can hold the same ``tiklocal.log`` at once. On Windows the rollover renames the
    active file, which fails with ``WinError 32`` while the other process has it open.
    ``RotatingFileHandler`` closes the stream *before* trying and never reopens it, so
    every later record -- including the traceback that explains the failure -- was
    dropped silently. Reopening here keeps the log alive and retries the rotation on
    the next full file.
    """

    def doRollover(self) -> None:  # pragma: no cover - needs a held file handle
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                try:
                    self.stream = self._open()
                except OSError:
                    # Still locked: the next emit goes to stderr only, and the next
                    # rollover attempt tries the rename again.
                    self.stream = None


def _prune_old_log_files(log_dir: Path) -> None:
    """Keep only the newest per-process log files.

    Naming the log after the pid keeps two processes from rotating the same file, but
    every start then adds a file: without this the data directory grew by up to 8 MB
    per run and nothing ever removed the old ones.
    """
    try:
        candidates = sorted(
            (path for path in log_dir.glob('tiklocal.*.log*') if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for path in candidates[KEEP_LOG_FILES:]:
        try:
            path.unlink()
        except OSError:
            # Still open in another process: it will be pruned on a later start.
            pass


def configure_logging(log_dir, level: int = logging.INFO) -> logging.Logger:
    """Attach a console handler and a rotating file handler exactly once."""
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return root

    root.setLevel(level)
    formatter = RedactingFormatter(_LOG_FORMAT)
    redact = RedactFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(redact)
    root.addHandler(stream)

    try:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        _prune_old_log_files(log_dir)
        file_handler = _SharedRotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding='utf-8',
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redact)
        root.addHandler(file_handler)
    except OSError:
        # A read-only data directory must not stop the server from starting, but the
        # warning has to reach stderr *and* be obvious: the file log is gone for good.
        root.warning('Could not open the log file under %s; logging to stderr only.', log_dir)

    setattr(root, _CONFIGURED_FLAG, True)
    return root
