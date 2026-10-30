"""Douyin-style search: the ``/search`` page and the JSON it renders from.

One search box has to answer two different questions -- "which author is this?"
and "which work says this?" -- while the library index only stores one row per
file: the caption is folded into the row's search text and the author lives on
the media source. The page therefore answers with two cheap queries
(:meth:`~tiklocal.services.library_index.MediaIndexStore.blog_rows` for the
authors and one :meth:`~tiklocal.services.library_index.MediaIndexStore.page`
for the works) instead of a fan-out per result.

The works query asks for album covers only: an album or a live post is one card,
and a caption search must not turn its five members into five separate results.
A blank query never reaches the index at all -- an empty LIKE pattern would match
the whole library, while the Douyin page opens on its hint line instead.

Results scroll like Douyin's: the page asks for the next slice with ``offset``
(and asks for the video tab when it does, so the author shortcut at the top is
never repeated mid-scroll).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from flask import Response, render_template, request

from tiklocal.i18n import t
from tiklocal.services import media_naming
from tiklocal.services.author_profiles import resolve_profile
from tiklocal.services.search_text import normalize as normalize_search_text
from tiklocal.web import read_int_arg
from tiklocal.web.author import display_name, folder_name_for, format_count
from tiklocal.web.media_payloads import media_urls, post_date_of

SEARCH_TABS = ('general', 'video', 'user')
DEFAULT_TAB = 'general'
QUERY_MAX_LENGTH = 200
DEFAULT_LIMIT = 24
MIN_LIMIT = 1
MAX_LIMIT = 48
# A scroll request beyond this window has long passed the last match; the cap only
# stops a hand-written offset from making SQLite walk a pointless slice.
MAX_OFFSET = 5000
# The general tab shows a taste of the author matches; the user tab is where
# they are browsed in full.
GENERAL_USER_LIMIT = 3
SEARCHABLE_MEDIA_TYPES = frozenset({'video', 'image', 'live'})
_ALBUM_INDEX_SUFFIX = re.compile(r'_\d+$')
# A downloaded work is often titled after its numeric id only (``…-示例博主-7680590….mp4``);
# that is not a caption and must not be printed where the caption goes. Only a long
# run of digits is treated as an id, so a caption like "2024" still shows.
_NOT_A_CAPTION = re.compile(r'^\d{12,}$')


def clean_query(raw) -> str:
    """Trim a query and bound its length; the LIKE window and the page input agree."""
    return str(raw or '').strip()[:QUERY_MAX_LENGTH]


def search_tab(raw) -> str:
    """One of ``general``, ``video`` or ``user``; anything else is the default tab."""
    tab = str(raw or '').strip().lower()
    return tab if tab in SEARCH_TABS else DEFAULT_TAB


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _source_id(record: dict) -> str:
    """The media source a row belongs to; the URI carries it when a stub omits it."""
    source_id = str(record.get('source_id') or '').strip()
    if source_id:
        return source_id
    name = str(record.get('name') or '')
    return name[1:].partition('/')[0] if name.startswith('@') else ''


def _works_page(media_index, query: str, limit: int, offset: int = 0, media_type: str = '') -> dict:
    """One page of matching works; a lightweight index stand-in degrades to none."""
    method = getattr(media_index, 'page', None)
    if not callable(method):
        return {}
    page = method(
        search=query, limit=limit, offset=offset, album_covers_only=True, media_type=media_type,
    )
    return page if isinstance(page, dict) else {}


def _blog_rows(media_index) -> list[dict]:
    """Per-source counts; an index without the method simply has no author rows."""
    method = getattr(media_index, 'blog_rows', None)
    if not callable(method):
        return []
    rows = method() or ()
    return [row for row in rows if isinstance(row, dict)]


def _profile(profiles: dict, source_id: str, folder: str) -> dict:
    """Resolve an author profile once per request; it reads a small file per source."""
    if source_id not in profiles:
        profiles[source_id] = resolve_profile(source_id, folder_name=folder) or {}
    return profiles[source_id]


def _folder_for(uri: str, source_id: str, sources_by_id: dict) -> str:
    """The folder that names the author of ``uri``; '' when nobody is named.

    Mirrors ``flow.build_author_caption``: a sub-folder names its author, else the
    source root does. Loose files in a generic default root must not leak the
    source id into the page as a nickname, but a default root that *is* a Douyin
    export folder does name one (the ``tiklocal <UID…_博主名_发布作品>`` setup).
    """
    rel_path = str(uri or '')
    if rel_path.startswith('@'):
        _, _, rel_path = rel_path[1:].partition('/')
    segments = [segment for segment in rel_path.split('/') if segment]
    if len(segments) > 1:
        return segments[0]
    source = (sources_by_id or {}).get(source_id)
    folder = folder_name_for(source) if source is not None else ''
    if source_id == 'default' and not media_naming.is_blogger_folder(folder):
        return ''
    return folder


def _work_author_fields(uri: str, source_id: str, sources_by_id: dict, profiles: dict) -> dict:
    """The author line of a work card; empty when no folder names an author."""
    folder = _folder_for(uri, source_id, sources_by_id)
    if not folder or not source_id:
        return {'author_name': '', 'author_url': '', 'author_avatar_url': ''}
    encoded = quote(source_id, safe='')
    return {
        'author_name': display_name(source_id, folder, _profile(profiles, source_id, folder)),
        'author_url': f'/flow?blog={encoded}',
        'author_avatar_url': f'/author/{encoded}/avatar',
    }


def _author_identity(source_id: str, sources_by_id: dict, profiles: dict) -> tuple[str, str]:
    """``(folder, display name)`` for a source, or ``('', '')`` when it names nobody.

    A plain ``~/Media`` root has no nickname, and inventing one from its folder
    would put a fake account on the user tab; only a Douyin export folder names
    its author. A profile file still carries a name without that folder, and
    non-default sources keep their configured folder as the author, matching
    ``flow.build_author_caption``.
    """
    source = (sources_by_id or {}).get(source_id)
    if source is None:
        return '', ''
    folder = folder_name_for(source)
    if source_id == 'default' and not media_naming.is_blogger_folder(folder):
        folder = ''
    profile = _profile(profiles, source_id, folder)
    if not folder and not str(profile.get('name') or '').strip():
        return '', ''
    return folder, display_name(source_id, folder, profile)


def _title(uri: str) -> str:
    """The post caption, or the least noisy thing the file name still offers.

    An empty result hides the title line entirely: a bare aweme id reads worse
    than nothing next to Douyin's own cards.
    """
    filename = str(uri or '').rsplit('/', 1)[-1]
    caption = media_naming.post_title(filename)
    if not caption:
        caption = _ALBUM_INDEX_SUFFIX.sub('', filename.rsplit('.', 1)[0]).strip()
    if not caption or _NOT_A_CAPTION.match(caption):
        return ''
    return caption


def _kind_label(record: dict) -> str:
    """Douyin's corner pill: gallery, live photo or video; plain photos have none."""
    filename = str(record.get('name') or '').rsplit('/', 1)[-1]
    media_type = str(record.get('media_type') or '')
    album_size = _as_int(record.get('album_size'), 0)
    if album_size > 1:
        stem = filename.rsplit('.', 1)[0]
        return t('search.kind.live') if media_naming.is_live_stem(stem) else t('search.kind.album')
    if media_type == 'live':
        return t('search.kind.live')
    if media_type == 'video':
        return t('search.kind.video')
    return ''


def _video_items(records, sources_by_id: dict, profiles: dict, caption: str = '') -> list[dict]:
    """One card per work, matching the feed's media links so taps land on /flow.

    A caption search opens the work with its query attached: the feed then keeps
    scrolling the works that share that caption instead of dropping the user back
    into the recommended timeline (``caption`` is only attached when the query
    really is part of this work's caption, so a nickname search does not pretend
    to be a caption filter).
    """
    needle = str(caption or '').strip().casefold()
    items: list[dict] = []
    for record in records or ():
        media_type = str(record.get('media_type') or '')
        uri = str(record.get('name') or '')
        if not uri or media_type not in SEARCHABLE_MEDIA_TYPES:
            continue
        source_id = _source_id(record)
        urls = media_urls(uri)
        title = _title(uri)
        open_url = f'/flow?start={quote(uri)}'
        if needle and needle in title.casefold():
            # A caption search keeps scrolling the works that share the query.
            open_url = f'{open_url}&caption={quote(str(caption).strip())}'
        elif source_id:
            # Otherwise keep scrolling one author's works, which is what tapping a
            # work after searching a user should do.
            open_url = f'{open_url}&blog={quote(source_id, safe="")}'
        items.append({
            'name': uri,
            'media_type': media_type,
            'title': title,
            'thumb_url': urls['thumb_url'],
            'media_url': urls['media_url'],
            'kind_label': _kind_label(record),
            **_work_author_fields(uri, source_id, sources_by_id, profiles),
            'date_label': post_date_of(record),
            'open_url': open_url,
        })
    return items


def _user_item(source_id: str, *, sources_by_id: dict, profiles: dict, works_count: int) -> dict:
    """One author card: the same identity the /author page renders."""
    folder, name = _author_identity(source_id, sources_by_id, profiles)
    profile = _profile(profiles, source_id, folder)
    fans = format_count(profile.get('follower_count'))
    douyin_id = str(profile.get('douyin_id') or '')
    encoded = quote(source_id, safe='')
    return {
        'source_id': source_id,
        'name': name,
        'douyin_id': douyin_id,
        'avatar_url': f'/author/{encoded}/avatar',
        'works_count': works_count,
        'works_label': t('search.user.works', n=works_count),
        'fans_label': t('search.user.fans', count=fans) if fans else '',
        'douyin_id_label': t('search.user.douyin_id', id=douyin_id) if douyin_id else '',
        'open_label': t('search.user.open'),
        'url': f'/author/{encoded}',
    }


def _user_matches(
    query: str,
    *,
    media_index,
    sources_by_id: dict,
    profiles: dict,
    limit: int,
    work_source_ids: list[str],
) -> tuple[list[str], dict[str, int]]:
    """Ordered author ids for a query: nickname/id hits first, then work authors.

    Douyin ranks a name hit above everything else and then prefers authors with
    more works. When no nickname contains the term, the authors of the matching
    works are appended, so searching a caption still shows whose work it is.
    """
    term = normalize_search_text(query)
    works_by_source: dict[str, int] = {}
    named: list[str] = []
    if term:
        for row in _blog_rows(media_index):
            source_id = str(row.get('source_id') or '')
            if not source_id or source_id not in sources_by_id:
                # A stale row for a removed source has no author page to open.
                continue
            works_by_source[source_id] = _as_int(row.get('count'), 0)
            folder, name = _author_identity(source_id, sources_by_id, profiles)
            if not name:
                continue
            douyin_id = str(_profile(profiles, source_id, folder).get('douyin_id') or '')
            haystack = f'{name}\n{douyin_id}'
            if term in normalize_search_text(haystack):
                named.append(source_id)
    named.sort(key=lambda source_id: (-works_by_source.get(source_id, 0), source_id))
    ordered = list(named)
    if len(ordered) < limit:
        candidates = [
            source_id for source_id in work_source_ids
            if source_id in sources_by_id and source_id not in named
            and _author_identity(source_id, sources_by_id, profiles)[1]
        ]
        # Work authors arrive in relevance order already; only their own card
        # count orders them among themselves.
        candidates.sort(key=lambda source_id: (-works_by_source.get(source_id, 0), source_id))
        ordered.extend(candidates[:limit - len(ordered)])
    return ordered, works_by_source


def build_search_payload(
    query: str, tab: str, limit: int, *, media_index, library_service, offset: int = 0,
) -> dict:
    """The JSON body of ``/api/search``: authors and works for one query.

    ``counts`` reports how many matches exist in total (the payload itself is
    capped by ``limit`` and, on the general tab, by ``GENERAL_USER_LIMIT``) so the
    page can offer "view all" without a second request, and the works scroll can
    ask for the next slice by ``offset``.
    """
    sources_by_id = getattr(library_service, 'sources_by_id', None) or {}
    profiles: dict[str, dict] = {}
    # The video tab must only show videos: the index defaults to every media type,
    # which made the tab list images and albums with their own badges.
    page = _works_page(media_index, query, limit, offset, media_type='video' if tab == 'video' else '')
    records = [record for record in page.get('records') or () if isinstance(record, dict)]
    work_source_ids = list(dict.fromkeys(_source_id(record) for record in records if _source_id(record)))

    videos: list[dict] = []
    users: list[dict] = []
    counts = {'users': 0, 'videos': 0}
    if tab in {'general', 'video'}:
        videos = _video_items(records, sources_by_id, profiles, caption=query)
        counts['videos'] = _as_int(page.get('total'), len(records))
    if tab in {'general', 'user'}:
        ordered, works_by_source = _user_matches(
            query,
            media_index=media_index,
            sources_by_id=sources_by_id,
            profiles=profiles,
            limit=limit,
            work_source_ids=work_source_ids,
        )
        counts['users'] = len(ordered)
        cap = limit if tab == 'user' else min(GENERAL_USER_LIMIT, limit)
        users = [
            _user_item(
                source_id,
                sources_by_id=sources_by_id,
                profiles=profiles,
                works_count=works_by_source.get(source_id, 0),
            )
            for source_id in ordered[:cap]
        ]
    return {
        'success': True,
        'query': query,
        'tab': tab,
        'users': users,
        'videos': videos,
        'counts': counts,
        'offset': _as_int(page.get('offset'), offset),
        'next_offset': _as_int(page.get('next_offset'), offset + len(records)),
        'has_more': bool(page.get('has_more')) if tab in {'general', 'video'} else False,
    }


def register_search_routes(app, *, media_index, library_service):
    """Wire the Douyin-style search page and the JSON endpoint it renders from."""

    @app.get('/search')
    def search_view():
        return render_template(
            'search.html',
            menu='search',
            query=clean_query(request.args.get('q')),
            tab=search_tab(request.args.get('tab')),
        )

    @app.get('/api/search')
    def api_search():
        # Flask maps HEAD onto the GET view and only drops the body afterwards, so
        # a preflight or a probe would still run both queries and every author
        # profile read just to learn nothing.
        if request.method == 'HEAD':
            return Response(status=200, mimetype='application/json')
        query = clean_query(request.args.get('q'))
        tab = search_tab(request.args.get('tab'))
        limit = read_int_arg('limit', DEFAULT_LIMIT, minimum=MIN_LIMIT, maximum=MAX_LIMIT)
        # Keep the requested offset so a scroll past the window can be told apart
        # from one that was merely clamped; clamping alone repeated the last slice
        # forever while ``has_more`` kept saying yes.
        requested_offset = read_int_arg('offset', 0, minimum=0)
        offset = min(requested_offset, MAX_OFFSET)
        if not query:
            return {
                'success': True,
                'query': '',
                'tab': tab,
                'users': [],
                'videos': [],
                'counts': {'users': 0, 'videos': 0},
                'offset': 0,
                'next_offset': 0,
                'has_more': False,
            }
        payload = build_search_payload(
            query, tab, limit,
            media_index=media_index,
            library_service=library_service,
            offset=offset,
        )
        if requested_offset > MAX_OFFSET or _as_int(payload.get('next_offset'), 0) > MAX_OFFSET:
            # The scroll window ends here: stop offering another page instead of
            # serving the same rows again, and say why in the payload.
            payload['videos'] = []
            payload['has_more'] = False
            payload['truncated'] = True
            payload['offset'] = requested_offset
        return payload
