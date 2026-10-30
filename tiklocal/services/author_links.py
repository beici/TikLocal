"""Bind author home pages from a TikTokDownloader user dump.

A Douyin export folder is named ``UID123456789_昵称_发布作品``, so TikLocal knows
the numeric ``uid`` of every author from the folder name alone. The home page is
``https://www.douyin.com/user/<sec_uid>``, and ``sec_uid`` is the one thing the
folder does not carry.

The sidecar can supply it, but only by fetching the account, and the accounts that
matter most here are the ones that no longer answer: a blocked or deleted account
returns no works, so the sync that was supposed to bind the link reports "this
author has no public works" and the link is never stored. TikTokDownloader
already writes the uid → sec_uid mapping to disk while it resolves a batch of
users, which is exactly the pair TikLocal needs, offline and in one read.

The dump is not authoritative about anything else. A deleted account keeps a
placeholder nickname (``用户1234567890``) or shows its numeric id as the name, and
copying that into the profile would replace a good nickname with a worse one, so
:func:`plan_bindings` reports those rows as ``abandoned`` instead of trusting the
name.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from tiklocal.services import author_profiles
from tiklocal.services.media_naming import blogger_display_name, is_author_folder
from tiklocal.services.tiktok_downloader import parse_profile_sec_uid

#: A dump of every resolved account is a few megabytes of JSON; more than this is
#: a database or a video file picked by mistake, and reading it would park the
#: request thread.
MAX_DUMP_BYTES = 64 * 1024 * 1024


def _clean(value: Any) -> str:
    return str(value if value is not None else '').strip()


def _row_from_user(user: Any) -> dict[str, str]:
    """Normalise one dump record into the fields TikLocal can use.

    Field extraction is delegated to
    :func:`tiklocal.services.author_profiles.extract_author_fields`: the dump
    holds raw Douyin user objects, and that function is already the one place that
    knows where a nickname, a Douyin id and an avatar URL live inside one.
    """
    if not isinstance(user, dict):
        return {}
    fields = author_profiles.extract_author_fields(user)
    uid = _clean(fields.get('uid'))
    sec_uid = _clean(fields.get('sec_uid') or fields.get('sec_user_id'))
    link = _clean(user.get('profile_url') or user.get('url'))
    if not sec_uid and link:
        # ``user-url.txt`` style record: a bare home-page link.
        sec_uid = link
    if sec_uid:
        # One validator decides what a usable profile id looks like, so a junk
        # field in a hand-edited dump lands in ``unusable`` instead of becoming a
        # link that sends the next sync to nowhere.
        try:
            sec_uid = parse_profile_sec_uid(sec_uid)
        except ValueError:
            sec_uid = ''
    if not uid and not sec_uid:
        return {}
    row = {'uid': uid, 'sec_uid': sec_uid}
    for key in ('name', 'douyin_id', 'signature', 'avatar_url'):
        value = _clean(fields.get(key))
        if value:
            row[key] = value
    return row


def parse_dump(text: str) -> list[dict[str, str]]:
    """Read every record a TikTokDownloader dump can hold.

    Three shapes appear in practice and all three are accepted: a JSON array, one
    JSON object per line (``resolved_users.jsonl``), and a plain list of home-page
    links (``user-url.txt``). A line that is neither is skipped rather than
    aborting the import: a dump that a user edited by hand should still bind the
    rows that survived.
    """
    body = str(text or '').lstrip('\ufeff').strip()
    if not body:
        return []
    if body[:1] in '[{':
        try:
            loaded = json.loads(body)
        except ValueError:
            loaded = None
        if isinstance(loaded, list):
            return [row for row in (_row_from_user(item) for item in loaded) if row]
        if isinstance(loaded, dict):
            for key in ('users', 'data', 'items', 'list'):
                items = loaded.get(key)
                if isinstance(items, list):
                    return [row for row in (_row_from_user(item) for item in items) if row]
            single = _row_from_user(loaded)
            return [single] if single else []
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        entry = line.strip()
        if not entry or entry.startswith('#'):
            continue
        if entry[:1] == '{':
            try:
                loaded = json.loads(entry)
            except ValueError:
                continue
            row = _row_from_user(loaded)
        elif entry.lower().startswith(('http://', 'https://')):
            row = _row_from_user({'profile_url': entry})
        else:
            parts = [part.strip() for part in entry.replace('\t', ',').split(',')]
            # ``uid,sec_uid`` (optionally with a name) is the shape a hand-made
            # export takes; a single column is a sec_uid or a Douyin id.
            if len(parts) >= 2:
                row = _row_from_user({'uid': parts[0], 'sec_uid': parts[1],
                                      'nickname': parts[2] if len(parts) > 2 else ''})
            else:
                row = _row_from_user({'sec_uid': parts[0]})
        if row:
            rows.append(row)
    return rows


def looks_abandoned(row: dict[str, str]) -> bool:
    """True when the dump's own name suggests the account is gone.

    Douyin replaces the nickname of a blocked or cancelled account with
    ``用户<uid>``, and an account that was renamed to nothing shows its numeric
    Douyin id as the name. Neither is proof — a user can legally be called
    ``用户12345678`` — so this only decides whether TikLocal keeps the existing
    nickname and mentions the possibility, never whether it drops the link.
    """
    return author_profiles.looks_placeholder(
        (row or {}).get('name'), (row or {}).get('douyin_id'),
    )


def _binding_fields(row: dict[str, str], profile: dict) -> dict[str, Any]:
    """The fields to store for one author, keeping every value that already works."""
    fields: dict[str, Any] = {}
    sec_uid = _clean(row.get('sec_uid'))
    if sec_uid:
        fields['sec_uid'] = sec_uid
        fields['profile_url'] = f'https://www.douyin.com/user/{sec_uid}'
    uid = _clean(row.get('uid'))
    if uid:
        fields['uid'] = uid
    # A placeholder name must never replace the folder-derived one, and a real
    # nickname already cached by a sync is at least as fresh as the dump.
    abandoned = looks_abandoned(row)
    if _clean(row.get('name')) and not abandoned and not _clean(profile.get('name')):
        fields['name'] = _clean(row['name'])
    for key in ('douyin_id', 'signature'):
        if _clean(row.get(key)) and not _clean(profile.get(key)):
            fields[key] = _clean(row[key])
    # A gone account's picture in the dump is Douyin's grey stand-in, not a face.
    # Taking it would replace the newest-work cover the page falls back to with a
    # blank square, so the import leaves the avatar alone in that case.
    if not abandoned and _clean(row.get('avatar_url')) and not profile.get('avatar_cached'):
        fields['avatar_url'] = _clean(row['avatar_url'])
    # Written either way so a later import can clear the note once the dump shows
    # a real nickname again.
    fields['account_missing'] = abandoned
    return fields


def plan_bindings(
    rows: Iterable[dict[str, str]], authors: Iterable[dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    """Match dump rows to local author folders by numeric UID.

    ``authors`` carries one entry per export folder: ``{'key', 'folder', 'uid'}``.
    Matching on the UID beats matching on the nickname (Douyin nicknames are not
    unique, and the dump's name is exactly what a deleted account loses), and it
    is also the only field both sides are guaranteed to hold.
    """
    by_uid: dict[str, dict[str, str]] = {}
    for row in rows or ():
        uid = _clean((row or {}).get('uid'))
        if uid and uid not in by_uid:
            by_uid[uid] = row

    plan: dict[str, list[dict[str, Any]]] = {
        'bound': [], 'abandoned': [], 'unmatched': [], 'unusable': [],
    }
    for author in authors or ():
        uid = _clean((author or {}).get('uid'))
        row = by_uid.get(uid) if uid else None
        if row is None:
            plan['unmatched'].append(dict(author or {}, reason='uid'))
            continue
        if not _clean(row.get('sec_uid')):
            plan['unusable'].append(dict(author or {}, reason='sec_uid'))
            continue
        entry = dict(author or {})
        entry['row'] = row
        entry['abandoned'] = looks_abandoned(row)
        plan['abandoned' if entry['abandoned'] else 'bound'].append(entry)
    return plan


def local_authors(sources: Iterable[Any]) -> list[dict[str, str]]:
    """Every export folder the configured media sources hold, with its page key.

    Only the first level below each source is inspected: an export folder is a
    direct child of the folder the user pointed TikLocal at, which is also what
    :func:`tiklocal.web.author.author_of` reads out of an indexed record. Reading
    the filesystem instead of the index keeps the command useful before the first
    scan has finished, and makes it independent of the database being open.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources or ():
        source_id = _clean(getattr(source, 'id', '') or (source or {}).get('id'))
        root = _clean(getattr(source, 'path', '') or (source or {}).get('path'))
        if not root or not os.path.isdir(root):
            continue
        folder_name = Path(root).name
        try:
            children = sorted(os.scandir(root), key=lambda entry: entry.name)
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            folder = child.name
            if not is_author_folder(folder):
                continue
            uid = author_profiles.douyin_id_from_folder(folder)
            if not uid:
                continue
            key = author_profiles.profile_key(source_id, folder, folder_name)
            if key in seen:
                continue
            seen.add(key)
            found.append({
                'key': key,
                'source_id': source_id,
                'folder': folder,
                'folder_name': folder_name,
                'name': blogger_display_name(folder),
                'uid': uid,
            })
    return found


#: Files TikTokDownloader leaves in ``url`` and ``Volume``, best first. The first
#: three carry the numeric UID, which is the only field that can be matched
#: against an export folder; ``user-url.txt`` holds links alone and is kept last
#: so a directory always resolves to a file that can actually bind something.
DUMP_FILENAMES = (
    'resolved_users.jsonl',
    'resolved_users.json',
    'user-info.jsonl',
)
#: Links alone: usable as a fallback, but nothing in it can be matched to a folder.
LINK_ONLY_FILENAMES = ('user-url.txt',)


def resolve_dump_path(value: Any) -> Path:
    """Turn what the user typed into the file to read.

    A directory is normal: TikTokDownloader keeps the link list in its own
    ``url`` folder and the UID-carrying dump in a sibling ``Volume`` folder, so
    looking in the folder *and* its neighbours means the user can paste the
    folder they know instead of hunting for the file.
    """
    path = Path(str(value or '').strip()).expanduser()
    if not path.exists():
        raise ValueError('missing')
    if path.is_file():
        return path
    candidates: list[Path] = []
    for name in DUMP_FILENAMES:
        # ``Volume`` sits next to ``url`` in a TikTokDownloader checkout, and that
        # is where the resolved-user dump with the UID in it is written. The
        # sibling folder is checked before falling back to a links-only list, so
        # pasting the familiar ``url`` folder still finds the file that binds.
        candidates += [path / name, path / 'Volume' / name, path.parent / 'Volume' / name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Nothing recognisable: fall back to the one plain list this project knows,
    # so the reader gets the "these are links, not uids" message instead of a
    # bare "file not found".
    for name in LINK_ONLY_FILENAMES:
        for candidate in (path / name, path / 'Volume' / name, path.parent / 'Volume' / name):
            if candidate.is_file():
                return candidate
    raise ValueError('empty')


def read_dump(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Return the file's text and the records in it, refusing an oversized read."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError('unreadable') from exc
    if size > MAX_DUMP_BYTES:
        raise ValueError('too_large')
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        raise ValueError('unreadable') from exc
    return text, parse_dump(text)


def import_links(
    value: Any, sources: Iterable[Any], *, dry_run: bool = False,
    download_avatars: bool = True, avatar_timeout: float = 10.0, avatar_budget: float = 0.0,
) -> dict[str, Any]:
    """Read a dump, bind what it can prove, and describe the result.

    The report is what the settings card prints, so every branch that ends with
    "nothing was bound" carries a reason the reader can act on: a dump made of
    links has no UID to match on, and saying so beats a silent zero.
    """
    path = resolve_dump_path(value)
    text, rows = read_dump(path)
    if not rows:
        raise ValueError('no_records')
    authors = local_authors(sources)
    plan = plan_bindings(rows, authors)
    bound = plan['bound'] + plan['abandoned']
    if dry_run:
        counts = {
            'written': 0,
            'bound': len(plan['bound']),
            'abandoned': len(plan['abandoned']),
            'unmatched': len(plan['unmatched']),
            'unusable': len(plan['unusable']),
        }
    else:
        counts = apply_plan(
            plan, download_avatars=download_avatars,
            avatar_timeout=avatar_timeout, avatar_budget=avatar_budget,
        )
    reason = ''
    if not bound:
        if not any(row.get('uid') for row in rows):
            reason = 'links_only'
        elif not authors:
            reason = 'no_authors'
        else:
            reason = 'no_match'
    return {
        'path': str(path),
        'records': len(rows),
        'authors': len(authors),
        'reason': reason,
        'missing': [
            {'key': entry.get('key'), 'name': entry.get('name') or entry.get('folder') or ''}
            for entry in plan['unmatched']
        ],
        **counts,
    }


def apply_plan(
    plan: dict[str, list[dict[str, Any]]], *, download_avatars: bool = False,
    avatar_timeout: float = 10.0, avatar_budget: float = 0.0,
) -> dict[str, int]:
    """Write the planned bindings and report how many of each happened.

    ``avatar_budget`` caps the total time spent fetching avatars: the bindings are
    a handful of small local writes, but each avatar is a network fetch, and a
    settings request that waits on a slow CDN is a request thread parked. Past the
    budget the remaining avatars are left for the next import, which costs nothing
    because the profiles themselves are already stored.
    """
    written = 0
    started = time.monotonic()
    for entry in list(plan.get('bound') or []) + list(plan.get('abandoned') or []):
        key = _clean(entry.get('key'))
        if not key:
            continue
        profile = author_profiles.load_profile(key)
        fields = _binding_fields(entry.get('row') or {}, profile)
        if not fields:
            continue
        saved = author_profiles.save_profile(key, fields)
        written += 1
        avatar = _clean((entry.get('row') or {}).get('avatar_url'))
        if download_avatars and avatar and not saved.get('avatar_cached'):
            if avatar_budget and time.monotonic() - started > avatar_budget:
                continue
            if author_profiles.download_avatar(key, avatar, timeout=avatar_timeout):
                author_profiles.save_profile(key, {'avatar_cached': True})
    return {
        'written': written,
        'bound': len(plan.get('bound') or []),
        'abandoned': len(plan.get('abandoned') or []),
        'unmatched': len(plan.get('unmatched') or []),
        'unusable': len(plan.get('unusable') or []),
    }
