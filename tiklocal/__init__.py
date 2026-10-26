"""TikLocal package metadata."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _read_pyproject_version() -> str:
    """Best-effort version for source checkouts (no installed metadata)."""
    pyproject = Path(__file__).resolve().parent.parent / 'pyproject.toml'
    try:
        text = pyproject.read_text(encoding='utf-8')
    except OSError:
        return '0.0.0'
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('version ='):
            return stripped.split('=', 1)[1].strip().strip('"\'') or '0.0.0'
    return '0.0.0'


try:
    __version__ = version('tiklocal')
except PackageNotFoundError:  # running from a source checkout
    __version__ = _read_pyproject_version()
