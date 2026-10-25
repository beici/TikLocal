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


def blogger_display_name(folder_name: str) -> str:
    """``UID123_示例博主_发布作品`` → ``示例博主``; anything else is returned unchanged."""
    match = _BLOGGER_FOLDER.match(str(folder_name or ''))
    return match.group('name') if match else str(folder_name or '')
