"""Author metadata (nickname, Douyin id, avatar) cached per media source.

A Douyin export folder is one media source (``UID123_博主名_发布作品``), so the
source id is the only stable author key the library has. The DouK-Downloader
Web API returns raw Douyin payloads whose author block carries the real
nickname, Douyin id and avatar URLs; this module keeps the interesting bits in
``<data dir>/authors/<source id>.json`` plus the downloaded avatar bytes, and
degrades to the folder-derived name and the bundled default avatar when the
cache is cold or the sidecar is offline.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from tiklocal.paths import get_data_dir
from tiklocal.services.json_storage import write_json_atomic
from tiklocal.services.net_guard import build_guarded_opener, is_local_host

DEFAULT_AVATAR_FILENAME = 'default-avatar.jpg'
AVATAR_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
)
AVATAR_REFERER = 'https://www.douyin.com/'
MAX_AVATAR_BYTES = 4 * 1024 * 1024
#: Redirects are checked hop by hop; see ``tiklocal.services.net_guard``.
_AVATAR_OPENER = build_guarded_opener()

#: Binding a home-page link and syncing the profile can run on two threads at
#: once; both used one fixed ``.json.tmp`` and lost each other's fields.
_PROFILE_LOCK = threading.RLock()

#: Unique suffix for the avatar temp file, so two concurrent downloads never write
#: the same path.
_TEMP_COUNTER = itertools.count(1)

_UID_FOLDER = re.compile(r'^UID(?P<uid>\d+)_')
_AVATAR_KEYS = ('avatar_larger', 'avatar_medium', 'avatar_thumb', 'avatar_168x168', 'avatar_300x300')
_STAT_KEYS = ('follower_count', 'total_favorited', 'aweme_count', 'following_count')


def authors_dir() -> Path:
    directory = get_data_dir() / 'authors'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def profile_path(source_id: str) -> Path:
    return authors_dir() / f'{_safe_key(source_id)}.json'


def avatar_path(source_id: str) -> Path:
    return authors_dir() / f'{_safe_key(source_id)}.jpg'


def _safe_key(source_id: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(source_id or '').strip())
    return cleaned or 'default'


def douyin_id_from_folder(folder_name: str) -> str:
    """``UID123456789_demo_发布作品`` → ``123456789``; '' otherwise."""
    match = _UID_FOLDER.match(str(folder_name or ''))
    return match.group('uid') if match else ''


def load_profile(source_id: str) -> dict:
    path = profile_path(source_id)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_profile(source_id: str, values: dict) -> dict:
    """Merge ``values`` into the cached profile and persist it."""
    with _PROFILE_LOCK:
        profile = load_profile(source_id)
        for key, value in (values or {}).items():
            if value in (None, ''):
                continue
            profile[key] = value
        profile['updated_at'] = int(time.time())
        write_json_atomic(profile_path(source_id), profile)
        return profile


def set_profile_url(source_id: str, url: str) -> dict:
    """Remember the author home-page link that was proven to belong to ``source_id``.

    Binding the link once is what makes the next sync a single click: the sync
    flow can start from the stored ``sec_uid`` instead of asking for a paste.
    An empty value clears the binding.
    """
    cleaned = str(url or '').strip()
    if not cleaned:
        with _PROFILE_LOCK:
            profile = load_profile(source_id)
            profile.pop('profile_url', None)
            profile['updated_at'] = int(time.time())
            write_json_atomic(profile_path(source_id), profile)
            return profile
    return save_profile(source_id, {'profile_url': cleaned})


def clear_profile(source_id: str) -> None:
    for path in (profile_path(source_id), avatar_path(source_id)):
        try:
            path.unlink()
        except OSError:
            pass


def _avatar_url_from_block(block: Any) -> str:
    """Pick the largest avatar URL out of a Douyin avatar block."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ''
    urls = block.get('url_list') or block.get('urlList') or []
    if isinstance(urls, str):
        return urls
    for url in urls:
        if isinstance(url, str) and url.startswith('http'):
            return url
    return ''


def extract_author_fields(payload: Any) -> dict:
    """Pull nickname, Douyin id, stats and avatar URL out of a Douyin payload.

    Accepts either a work item (``{'author': {...}}``) or an author block itself.
    """
    if not isinstance(payload, dict):
        return {}
    author = payload.get('author') if isinstance(payload.get('author'), dict) else payload
    fields: dict[str, Any] = {}

    nickname = author.get('nickname') or author.get('nick_name') or ''
    if nickname:
        fields['name'] = str(nickname).strip()

    douyin_id = author.get('unique_id') or author.get('uniqueId') or ''
    if not douyin_id:
        short_id = author.get('short_id') or author.get('shortId') or ''
        douyin_id = '' if str(short_id) in ('', '0') else short_id
    if douyin_id:
        fields['douyin_id'] = str(douyin_id).strip()

    for key in ('uid', 'sec_uid', 'sec_user_id', 'signature'):
        value = author.get(key)
        if value:
            fields[key] = str(value)

    for key in _STAT_KEYS:
        value = author.get(key)
        if isinstance(value, (int, float)):
            fields[key] = int(value)

    for key in _AVATAR_KEYS:
        url = _avatar_url_from_block(author.get(key))
        if url:
            fields['avatar_url'] = url
            break
    if not fields.get('avatar_url'):
        # The search-user endpoint flattens the avatar into a bare URL string.
        for key in ('avatar', 'avatar_url'):
            value = author.get(key)
            if isinstance(value, str) and value.lower().startswith(('http://', 'https://')):
                fields['avatar_url'] = value
                break
    return fields


def download_avatar(source_id: str, url: str, timeout: float = 10.0) -> bool:
    """Fetch ``url`` into the avatar cache; returns True when bytes were stored."""
    url = str(url or '').strip()
    if not url.lower().startswith(('http://', 'https://')):
        return False
    # The URL comes from a sidecar response, so a rewritten payload could point it
    # at loopback or a metadata endpoint; the bytes would then be served back by
    # /author/<id>/avatar and the local response would be readable.
    if is_local_host(url):
        return False
    request = urllib.request.Request(url, headers={
        'User-Agent': AVATAR_USER_AGENT,
        'Referer': AVATAR_REFERER,
    })
    try:
        with _AVATAR_OPENER.open(request, timeout=timeout) as response:
            resolved = getattr(response, 'geturl', None)
            final_url = resolved() if callable(resolved) else url
            if is_local_host(final_url or url):
                return False
            content_type = str(response.headers.get('Content-Type') or '').lower()
            if content_type and not content_type.startswith('image/'):
                return False
            data = response.read(MAX_AVATAR_BYTES + 1)
    except Exception:
        return False
    if not data or len(data) > MAX_AVATAR_BYTES:
        return False
    path = avatar_path(source_id)
    # Binding a profile link and syncing the author can run at the same time (see the
    # note on the two routes above). A fixed ".jpg.tmp" made them write over each
    # other's bytes, publishing a torn JPEG, and a crash left the file behind forever.
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{next(_TEMP_COUNTER)}.tmp')
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def store_author_from_payload(
    source_id: str, payload: Any, *, fetch_avatar: bool = True, profile_url: str = '',
) -> dict:
    """Persist whatever the payload says about the author of ``source_id``."""
    fields = extract_author_fields(payload)
    bound = str(profile_url or '').strip()
    if bound:
        fields['profile_url'] = bound
    if not fields:
        return load_profile(source_id)
    profile = save_profile(source_id, fields)
    if fetch_avatar and fields.get('avatar_url'):
        if download_avatar(source_id, fields['avatar_url']):
            profile['avatar_cached'] = True
            profile = save_profile(source_id, {'avatar_cached': True})
    return profile


def resolve_profile(source_id: str, *, folder_name: str = '') -> dict:
    """Merge the cached profile with what the source folder alone can tell us."""
    profile = load_profile(source_id)
    douyin_id = str(profile.get('douyin_id') or '') or douyin_id_from_folder(folder_name or source_id)
    return {
        'source_id': str(source_id or ''),
        'name': str(profile.get('name') or ''),
        'douyin_id': douyin_id,
        'signature': str(profile.get('signature') or ''),
        'follower_count': profile.get('follower_count'),
        'total_favorited': profile.get('total_favorited'),
        'aweme_count': profile.get('aweme_count'),
        'avatar_url': str(profile.get('avatar_url') or ''),
        'profile_url': str(profile.get('profile_url') or ''),
        'has_avatar': avatar_path(source_id).is_file(),
    }
