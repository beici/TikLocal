"""TikLocal package metadata."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _read_pyproject_version() -> str:
    """Best-effort version for source checkouts (no installed metadata)."""
    pyproject = Path(__file__).resolve().parent.parent / 'pyproject.toml'
    try:
        text = pyproject.read_text(encoding='utf-8')
    except OSError:
        return ''
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('version ='):
            return stripped.split('=', 1)[1].strip().strip('"\'')
    return ''


def _installed_version() -> str:
    try:
        return version('tiklocal')
    except PackageNotFoundError:  # running from a source checkout
        return ''


# One source of truth: a checkout reports the version it is about to run, an
# installed build reports its metadata. Reading pyproject.toml through tomllib
# made this depend on the interpreter (3.10 has no tomllib and tomli is a
# dev-only dependency), so the same checkout reported two different versions.
__version__ = _read_pyproject_version() or _installed_version() or '0.0.0'
