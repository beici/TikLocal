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
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = '[%(asctime)s] %(levelname)s in %(name)s: %(message)s'
_CONFIGURED_FLAG = '_tiklocal_logging_configured'
LOG_FILE_NAME = 'tiklocal.log'
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3


def configure_logging(instance_path, level: int = logging.INFO) -> logging.Logger:
    """Attach a console handler and a rotating file handler exactly once."""
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return root

    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    try:
        log_dir = Path(instance_path)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding='utf-8',
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # A read-only instance directory must not stop the server from starting.
        root.warning('Could not open the log file under %s; logging to stderr only.', instance_path)

    setattr(root, _CONFIGURED_FLAG, True)
    return root
