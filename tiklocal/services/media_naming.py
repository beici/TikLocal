"""Filename conventions for Douyin-style exports: live photos, albums and blogger folders.

Exported archives name files ``YYYY-MM-DD HH.MM.SS-类型-博主名-标题_N.ext``:
``-实况-`` marks a live photo (same-stem mp4 + jpeg pair), ``-图集-`` marks a
photo album whose members share a timestamp prefix and are ordered by ``_N``.
Blogger folders are named ``UID…_博主名_发布作品``.
"""

import re

LIVE_MARKER = '-实况-'
ALBUM_MARKER = '-图集-'

_ALBUM_INDEX = re.compile(r'_(\d+)$')
_BLOGGER_FOLDER = re.compile(r'^UID\d+_(?P<name>.+)_发布作品$')
_UID_PREFIX = re.compile(r'^UID\d+_')
RELEASE_SUFFIX = '_发布作品'
_POST_TITLE = re.compile(
    r'^\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}-(?:实况|图集|视频)-[^-]+-(?P<title>.+)$'
)


def is_live_stem(stem: str) -> bool:
    return LIVE_MARKER in str(stem or '')


def post_title(filename: str) -> str:
    """``YYYY-MM-DD HH.MM.SS-类型-博主名-标题_N.ext`` → ``标题``; '' when it does not match."""
    stem = str(filename or '').rsplit('/', 1)[-1].rsplit('.', 1)[0]
    stem = _ALBUM_INDEX.sub('', stem)
    match = _POST_TITLE.match(stem)
    return match.group('title').strip() if match else ''


def live_post(filename: str) -> tuple[str, int] | None:
    """Return (post_key, index) for a live-post member; ``_N`` runs across the whole post."""
    stem = str(filename or '').rsplit('.', 1)[0]
    if LIVE_MARKER not in stem:
        return None
    match = _ALBUM_INDEX.search(stem)
    if not match:
        return stem, 0
    return stem[:match.start()], int(match.group(1))


def album_member(filename: str) -> tuple[str, int] | None:
    """Return (group_key, index) for an album member filename, else None."""
    stem = str(filename or '').rsplit('.', 1)[0]
    if ALBUM_MARKER not in stem:
        return None
    match = _ALBUM_INDEX.search(stem)
    if not match:
        return None
    return stem[:match.start()], int(match.group(1))


def is_blogger_folder(folder_name: str) -> bool:
    """True when ``folder_name`` is a Douyin export folder and so names its author."""
    return bool(_BLOGGER_FOLDER.match(str(folder_name or '')))


def is_author_folder(folder_name: str) -> bool:
    """True when a folder inside a media root is one author, not just a directory.

    Exports seen in the wild drop the ``_发布作品`` suffix (``UID123_博主名``) or
    park the works under an extra ``video/`` level, so the strict
    :func:`is_blogger_folder` pattern is too narrow to decide whether a
    sub-folder names somebody. The ``UID`` prefix is what every export keeps.
    """
    return bool(_UID_PREFIX.match(str(folder_name or '')))


def blogger_display_name(folder_name: str) -> str:
    """``UID123_示例博主_发布作品`` → ``示例博主``; anything else is returned unchanged.

    The strict pattern needs the trailing ``_发布作品``; folders that drop it are
    still exports, so the ``UID123_`` prefix and the suffix are peeled off
    separately. That matters because the raw folder used to reach the author
    page as its nickname.
    """
    text = str(folder_name or '')
    match = _BLOGGER_FOLDER.match(text)
    if match:
        return match.group('name')
    if not _UID_PREFIX.match(text):
        return text
    name = _UID_PREFIX.sub('', text)
    if name.endswith(RELEASE_SUFFIX):
        name = name[: -len(RELEASE_SUFFIX)]
    if not name or name == RELEASE_SUFFIX.strip('_'):
        return text
    return name


def author_folder(rel_path: str) -> str:
    """The top-level folder that names the author of ``rel_path``; '' for a loose file.

    A shared media root keeps one folder per blogger
    (``UID123_博主名_发布作品/2026-06-04 …-视频-博主名-标题.mp4``); the file inside
    it belongs to that blogger, and the root itself belongs to nobody.
    """
    segments = [segment for segment in str(rel_path or '').split('/') if segment]
    return segments[0] if len(segments) > 1 else ''
