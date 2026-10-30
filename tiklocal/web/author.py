"""Douyin-style author profile page, one page per media source.

A media source is a single blogger export folder, so ``/author/<source_id>`` is
that author's home: a back arrow, a ringed avatar, the nickname, the muted
Douyin-id line, a stats row, one works tab and a three-column grid of the
author's media. Profiles come from :mod:`tiklocal.services.author_profiles`,
which caches the nickname, Douyin id and avatar fetched through the
TikTokDownloader sidecar and falls back to the folder-derived name plus the
bundled default avatar when that cache is cold.

The refresh endpoint is a thin wrapper over
``TikTokDownloadService.refresh_author_profile``; it is only reachable while a
sidecar base URL is configured. Douyin nicknames are not unique, so the search
endpoint feeds a picker and the refresh endpoint reports an ambiguous keyword as
HTTP 409 with the matching accounts instead of guessing one. The sync endpoints
take the exact route instead: an author home-page link yields the ``sec_uid``,
the account timeline yields a work id, and that work's detail payload carries
the real ``unique_id`` plus the numeric ``uid`` that has to match the export
folder before anything is cached.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from flask import abort, current_app, jsonify, redirect, render_template, request, send_file

from tiklocal.i18n import current_locale, t
from tiklocal.services import author_profiles
from tiklocal.services.media_naming import (
    author_folder,
    blogger_display_name,
    is_author_folder,
    is_blogger_folder,
)
from tiklocal.services.tiktok_downloader import (
    AuthorAmbiguousError,
    TikTokDownloaderError,
    normalize_profile_url,
    parse_profile_sec_uid,
    tiktok_user_message,
)
from tiklocal.web.media_payloads import media_urls

AVATAR_MAX_AGE = 300
COMPACT_COUNT_THRESHOLD = 10000
TEN_THOUSAND = 10000
# 每次 sidecar 调用最长 150 s；没有上限时一个请求会串行跑几十分钟。
MAX_SYNC_AUTHORS_PER_REQUEST = 200


def folder_name_for(source) -> str:
    """The export folder that names an author: its last path segment."""
    path = str(getattr(source, 'path', '') or '')
    if path and path not in {'.', '/'}:
        return Path(path).name
    return str(getattr(source, 'name', '') or '')


def display_name(source_id: str, folder_name: str, profile: dict) -> str:
    """Cached nickname first, then the folder-derived blogger name, then the id."""
    return (
        str((profile or {}).get('name') or '').strip()
        or blogger_display_name(folder_name)
        or str(source_id or '')
    )


def profile_key(source_id: str, folder: str, folder_name: str) -> str:
    """The key an author's cached profile and avatar are stored under.

    ``author_profiles`` used to key everything by media source, which was right
    while a source was one blogger's export folder. A shared root such as
    ``~/Downloads/douyin`` breaks that: the source names nobody, so every author
    inside it shared one cache entry and the second sync overwrote the first.
    Scoping the key to ``<source>/<folder>`` gives each blogger its own nickname,
    Douyin id and avatar while keeping every existing single-blogger install on
    its old ``source_id`` key, so nothing has to be re-synced after upgrading.
    """
    scope = str(folder or '').strip()
    if not scope or scope == str(folder_name or ''):
        return str(source_id or '')
    return f'{source_id}/{scope}'


def author_of(record: dict) -> str:
    """The export folder that owns ``record``; '' when it belongs to nobody.

    A record names itself ``@<source>/<rel_path>``, so the folder is the first
    segment of the path after the source.
    """
    uri = str(record.get('name') or '')
    _, _, rel_path = uri.partition('/') if uri.startswith('@') else ('', '', uri)
    folder = author_folder(rel_path)
    return folder if folder and is_author_folder(folder) else ''


def author_folders(records) -> list[dict]:
    """The authors living inside one media source, biggest first.

    A shared root such as ``~/Downloads/douyin`` holds one export folder per
    blogger, and the root itself names nobody: listing the raw folder as a
    nickname is what turned the page into "down". Only folders that look like an
    export (a ``UID…_`` prefix) count, so a year or trip directory keeps the
    ordinary single-page behaviour.
    """
    counts: dict[str, int] = {}
    for record in records or ():
        folder = author_of(record)
        if not folder:
            continue
        counts[folder] = counts.get(folder, 0) + 1
    entries = [
        {'folder': folder, 'name': blogger_display_name(folder), 'count': count}
        for folder, count in counts.items()
    ]
    entries.sort(key=lambda entry: (-entry['count'], entry['folder']))
    return entries


def format_count(value) -> str:
    """Compact Douyin-style counter: ``1234`` stays, ``12345`` becomes ``1.2`` + the unit."""
    if value is None or value == '':
        return ''
    try:
        count = max(0, int(value))
    except (TypeError, ValueError):
        return ''
    if count < COMPACT_COUNT_THRESHOLD:
        return str(count)
    if current_locale() == 'zh':
        return f'{count / TEN_THOUSAND:.1f}{t("author.unit.ten_thousand")}'
    # The English unit used to be 'K' while the divisor stayed 10000, so 12345
    # rendered as "1.2K" -- ten times too small.
    for limit, suffix in ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'K')):
        if count >= limit:
            return f'{count / limit:.1f}{suffix}'
    return str(count)


def parse_sync_text(text) -> list[dict]:
    """Parse pasted sync lines into ``{source_id, profile_url}`` entries.

    One author per line, either ``<source_id>|<profile_url>`` or a bare
    home-page link (matched later through the UID inside the author block).
    Blank lines and ``#`` comments are skipped.
    """
    entries = []
    for raw_line in str(text or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        source_id, separator, rest = line.partition('|')
        if separator:
            entries.append({'source_id': source_id.strip(), 'profile_url': rest.strip()})
        else:
            entries.append({'source_id': '', 'profile_url': line})
    return entries


def local_work_names(media_index, source_id: str, *, folder: str = '') -> list[str]:
    """Every file name the library holds for a source, newest first.

    ``更新最新视频`` diffs the sidecar's account timeline against these names, so
    it has to see album members and live-post parts too, not just the covers the
    grid renders. ``folder`` narrows that to one blogger inside a shared root:
    otherwise every author's page re-queued the whole root's works.
    """
    # No blanket except here: treating an index failure as "no local works" made
    # 更新最新视频 queue up to 100 already-downloaded videos again. Let it reach the
    # route's 502 handler instead.
    records = media_index.records(source_id=source_id, album_covers_only=False, folder=folder)
    return [str(record.get('name') or '') for record in records or () if record.get('name')]


def _publish_order(record) -> tuple:
    """Sort key for the author grid: publishing time first, file name as tie-break.

    The index hands records back in filesystem-mtime order, which groups a whole
    download batch onto the same timestamp and scrambles the timeline. The capture
    time comes from the post's own name (2026-09-22 23.34.19-图集-...), so sorting
    on it puts the author page back in publishing order.
    """
    captured = record.get('captured_at') or record.get('mtime_ts') or 0
    try:
        captured = float(captured)
    except (TypeError, ValueError):
        captured = 0.0
    return (captured, str(record.get('name') or ''))


def build_tiles(records) -> list[dict]:
    """Grid tiles: one square cover per work, newest published first, album covers only."""
    tiles = []
    for record in sorted(records or (), key=_publish_order, reverse=True):
        uri = str(record.get('name') or '')
        if not uri:
            continue
        media_type = str(record.get('media_type') or 'image')
        plays = media_type in {'video', 'live'}
        urls = media_urls(uri)
        tiles.append({
            'uri': uri,
            'media_type': media_type,
            'plays': plays,
            'thumb_url': urls['thumb_url'],
            'media_url': urls['media_url'],
            'detail_url': (
                f'/detail/{quote(uri, safe="/")}' if plays else f'/image?uri={quote(uri)}'
            ),
        })
    return tiles


def build_stats(tiles: list[dict], profile: dict) -> list[dict]:
    """The header counter row; counters the cache cannot know are left out."""
    stats = [{'key': 'works', 'label': t('author.stats.works'), 'value': format_count(len(tiles))}]
    for key, label_key, field in (
        ('followers', 'author.stats.followers', 'follower_count'),
        ('likes', 'author.stats.likes', 'total_favorited'),
    ):
        value = format_count((profile or {}).get(field))
        if value:
            stats.append({'key': key, 'label': t(label_key), 'value': value})
    return stats


def register_author_routes(app, *, library_service, media_index, tiktok_service=None):
    """Wire the author page, its avatar endpoint and the profile refresh API."""

    def _source(source_id):
        return library_service.sources_by_id.get(str(source_id or '').strip())

    def _source_or_404(source_id):
        source = _source(source_id)
        if source is None:
            abort(404)
        return source

    def _sidecar_base_url() -> str:
        """Cheap configuration probe: an empty base URL means no sidecar."""
        if tiktok_service is None:
            return ''
        try:
            config = tiktok_service.get_config() or {}
        except Exception:
            return ''
        return str(config.get('base_url') or '').strip()

    def _json_body() -> dict:
        payload = request.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}

    def _render_author(
        source_id, source, *, folder: str, profile: dict, tiles: list[dict],
        authors: list[dict] | None = None,
    ):
        folder_name = folder_name_for(source)
        key = profile_key(source_id, folder, folder_name)
        sidecar_ready = bool(_sidecar_base_url())
        scoped = key != str(source_id or '')
        # A folder-scoped page is a full author page, not a read-only preview: it
        # has its own cache key, so it can bind a home-page link and sync its own
        # nickname, Douyin id, avatar and counters without touching the root's.
        scoped_url = '/author/{}/{}'.format(
            quote(str(source_id or ''), safe=''), quote(folder, safe=''),
        ) if scoped else ''
        avatar_url = (
            f'{scoped_url}/avatar' if scoped
            else f'/author/{quote(str(source_id or ""), safe="")}/avatar'
        )
        return render_template(
            'author.html',
            menu='author',
            source_id=source_id,
            author_folder=folder if scoped else '',
            author_name=display_name(key, folder, profile),
            avatar_url=avatar_url,
            search_default=blogger_display_name(folder) or str(source_id or ''),
            douyin_id=(
                str(profile.get('douyin_id') or '')
                or author_profiles.douyin_id_from_folder(folder)
            ),
            profile_url=str(profile.get('profile_url') or ''),
            tiles=tiles,
            authors=authors or [],
            stats=[] if authors else build_stats(tiles, profile),
            # The sync entry point is part of the page, not of the configuration:
            # hiding it until TikTokDownloader is configured left the reader with no
            # way to discover the feature at all. It opens a menu that says what is
            # missing and where to fix it. A source that is really a list of authors
            # has nothing to sync at the source level, so the menu stays off there.
            refresh_enabled=not authors,
            sidecar_ready=sidecar_ready,
        )

    @app.route('/author/<source_id>')
    def author_view(source_id):
        source = _source_or_404(source_id)
        folder_name = folder_name_for(source)
        profile = author_profiles.resolve_profile(source_id, folder_name=folder_name)
        records = media_index.records(source_id=source_id, album_covers_only=True)
        authors = (
            [] if is_blogger_folder(folder_name) else author_folders(records)
        )
        # A mixed root can hold loose files next to the export folders. They name
        # nobody, so listing the authors must not make them disappear from the
        # page the user came to look at.
        loose = [record for record in records if not author_of(record)] if authors else records
        return _render_author(
            source_id, source,
            folder=folder_name,
            profile=profile,
            tiles=build_tiles(loose),
            authors=authors,
        )

    @app.route('/author/<source_id>/<folder>')
    def author_folder_view(source_id, folder):
        """One blogger inside a shared root: ``/author/default/UID123_博主名_发布作品``.

        The avatar on a Flow card points here, because the source alone owns no
        author when several exports share a directory.
        """
        source = _source_or_404(source_id)
        records = media_index.records(
            source_id=source_id, album_covers_only=True, folder=folder,
        )
        if not records:
            abort(404)
        folder_name = folder_name_for(source)
        profile = author_profiles.resolve_profile(
            profile_key(source_id, folder, folder_name),
            folder_name=folder,
            # An entry synced before this page existed lives under the root.
            shared_key=source_id,
        )
        return _render_author(
            source_id, source,
            folder=folder,
            profile=profile,
            tiles=build_tiles(records),
        )

    @app.route('/author/<source_id>/avatar')
    def author_avatar(source_id):
        _source_or_404(source_id)
        return _send_avatar(source_id, source_id)

    @app.route('/author/<source_id>/<folder>/avatar')
    def author_folder_avatar(source_id, folder):
        """The avatar of one blogger inside a shared root."""
        source = _source_or_404(source_id)
        return _send_avatar(
            profile_key(source_id, folder, folder_name_for(source)), source_id, folder,
        )

    def _newest_thumb(source_id: str, folder: str) -> str:
        """The cover of the newest work, used until a profile picture is synced."""
        try:
            records = media_index.records(
                source_id=source_id, album_covers_only=True, folder=folder,
            )
        except Exception:
            return ''
        if not records:
            return ''
        newest = max(records, key=_publish_order)
        uri = str(newest.get('name') or '')
        return media_urls(uri)['thumb_url'] if uri else ''

    def _avatar_key(key, source_id: str = '', folder: str = '') -> str:
        """Where this author's picture really is: their own key, or the root's.

        A picture synced before per-author keys existed sits under the media
        root, and only the folder UID can prove it is the right person.
        """
        if author_profiles.existing_avatar_path(key).is_file():
            return key
        shared = author_profiles.inherited_profile(key, source_id, folder)
        if shared and author_profiles.existing_avatar_path(source_id).is_file():
            return source_id
        return key

    def _send_avatar(key, source_id: str = '', folder: str = ''):
        cached = author_profiles.existing_avatar_path(_avatar_key(key, source_id, folder))
        if cached.is_file():
            return send_file(cached, mimetype='image/jpeg', max_age=AVATAR_MAX_AGE)
        # Nobody has synced this author yet. The bundled grey face made every page
        # look like an empty profile, so the newest work stands in for the picture;
        # a 302 keeps the thumbnail cache and the avatar cache apart.
        fallback = _newest_thumb(source_id, folder) if source_id else ''
        if fallback:
            return redirect(fallback)
        bundled = Path(current_app.static_folder or '') / author_profiles.DEFAULT_AVATAR_FILENAME
        if not bundled.is_file():
            abort(404)
        return send_file(bundled, mimetype='image/jpeg', max_age=AVATAR_MAX_AGE)

    def _scoped_key(source_id, source) -> str:
        """The cache key for the author this request talks about.

        ``?folder=`` names one blogger inside a shared root; without it the
        request is about the source itself, exactly as before.
        """
        folder = str(request.args.get('folder') or '').strip()
        return profile_key(source_id, folder, folder_name_for(source)), folder

    def _sync_profile(source_id):
        """Refresh one author's cached profile (nickname, Douyin id, avatar, stats).

        A home-page or work link goes through the exact identity chain and is
        then bound to the source, so the next sync is a single click; a bare
        keyword feeds the nickname picker and answers 409 with the candidates
        when several accounts share the name.
        """
        source = _source_or_404(source_id)
        if not _sidecar_base_url():
            return jsonify({'success': False, 'ok': False, 'error': t('author.refresh.unconfigured')}), 400
        payload = _json_body()
        folder_name = folder_name_for(source)
        key, folder = _scoped_key(source_id, source)
        # The export folder's UID is what proves a pasted link belongs here. For a
        # blogger parked in a shared root that is the folder's own UID, not the
        # root's (which has none), so the identity check still has something to
        # match before anything is written to that blogger's cache entry.
        expected_uid = author_profiles.douyin_id_from_folder(
            folder or folder_name
        )
        profile_url = str(payload.get('profile_url') or '').strip()
        aweme_id = str(payload.get('aweme_id') or '').strip()
        if profile_url or aweme_id:
            try:
                tiktok_service.sync_author_profile(
                    key,
                    profile_url=profile_url,
                    aweme_id=aweme_id,
                    folder_uid=expected_uid,
                )
            except ValueError as exc:
                return jsonify({'success': False, 'ok': False, 'error': str(exc)}), 400
            except TikTokDownloaderError as exc:
                app.logger.warning('author sidecar request failed: %s', exc)
                return jsonify({'success': False, 'ok': False, 'error': tiktok_user_message(exc)}), 502
            except Exception:  # a sidecar hiccup must never become a 500 page
                # The sidecar client's own text reads "HTTPConnectionPool(host=…,
                # port=…): Max retries exceeded with url: /api/…", which handed any
                # LAN client the sidecar address and its private routes.
                app.logger.warning('author refresh failed for %s', source_id, exc_info=True)
                return jsonify({'success': False, 'ok': False, 'error': t('author.refresh.error')}), 502
            profile = author_profiles.resolve_profile(
                key, folder_name=folder or folder_name,
                shared_key=source_id if folder else '',
            )
            return jsonify({'success': True, 'ok': True, 'profile': profile})

        keyword = str(payload.get('keyword') or '').strip()
        keyword = keyword or blogger_display_name(folder or folder_name)
        unique_id = str(payload.get('unique_id') or '').strip()
        uid = str(payload.get('uid') or '').strip()
        try:
            profile = tiktok_service.refresh_author_profile(
                key, keyword=keyword, unique_id=unique_id, uid=uid,
            )
        except AuthorAmbiguousError as exc:
            # Several accounts share the nickname: hand the picker back to the page.
            return jsonify({'success': False, 'ok': False, 'error': str(exc), 'candidates': exc.candidates}), 409
        except ValueError as exc:
            return jsonify({'success': False, 'ok': False, 'error': str(exc)}), 400
        except TikTokDownloaderError as exc:
            app.logger.warning('author sidecar request failed: %s', exc)
            return jsonify({'success': False, 'ok': False, 'error': tiktok_user_message(exc)}), 502
        except Exception:  # a sidecar hiccup must never become a 500 page
            app.logger.warning('author refresh failed for %s', source_id, exc_info=True)
            return jsonify({'success': False, 'ok': False, 'error': t('author.refresh.error')}), 502
        return jsonify({'success': True, 'ok': True, 'profile': profile})

    @app.post('/api/author/<source_id>/refresh')
    def author_refresh(source_id):
        return _sync_profile(source_id)

    @app.post('/api/author/<source_id>/sync')
    def author_sync_one(source_id):
        """Alias of ``refresh``: the sync menu calls the action by its new name."""
        return _sync_profile(source_id)

    @app.post('/api/author/<source_id>/bind')
    def author_bind(source_id):
        """Persist the author home-page link that belongs to this media source."""
        source = _source_or_404(source_id)
        url = str(_json_body().get('profile_url') or '').strip()
        if not url:
            return jsonify({'success': False, 'ok': False, 'error': t('author.sync.error.profile_url')}), 400
        try:
            sec_uid = parse_profile_sec_uid(url)
        except ValueError as exc:
            return jsonify({'success': False, 'ok': False, 'error': str(exc)}), 400
        try:
            author_profiles.set_profile_url(_scoped_key(source_id, source)[0], url)
        except OSError:
            # authors/*.json is briefly locked by a scanner or an editor. The old
            # password-store lesson applies: an unwritable store is a 503 with a
            # readable message, not a 500 page that says "internal error".
            app.logger.exception('Could not save the author link for %s', source_id)
            return jsonify({'success': False, 'ok': False, 'error': t('common.error.storage_unavailable')}), 503
        return jsonify({'success': True, 'ok': True, 'profile_url': url, 'sec_uid': sec_uid})

    @app.post('/api/author/<source_id>/update-videos')
    def author_update_videos(source_id):
        """Queue downloads for the author's works that the library does not hold yet."""
        source = _source_or_404(source_id)
        if not _sidecar_base_url():
            return jsonify({'success': False, 'ok': False, 'error': t('author.sync.error.unconfigured')}), 400
        payload = _json_body()
        key, folder = _scoped_key(source_id, source)
        profile_url = str(payload.get('profile_url') or '').strip()
        if profile_url:
            bound = normalize_profile_url(profile_url)
            if not bound:
                return jsonify({'success': False, 'ok': False, 'error': t('author.sync.error.profile_url')}), 400
            try:
                author_profiles.set_profile_url(key, bound)
            except OSError:
                app.logger.exception('Could not save the author link for %s', source_id)
                return jsonify({'success': False, 'ok': False, 'error': t('common.error.storage_unavailable')}), 503
        try:
            result = tiktok_service.enqueue_latest_videos(
                key,
                profile_url=profile_url,
                local_names=local_work_names(media_index, source_id, folder=folder),
            )
        except ValueError as exc:
            return jsonify({'success': False, 'ok': False, 'error': str(exc)}), 400
        except TikTokDownloaderError as exc:
            app.logger.warning('author sidecar request failed: %s', exc)
            return jsonify({'success': False, 'ok': False, 'error': tiktok_user_message(exc)}), 502
        except Exception:  # a sidecar hiccup must never become a 500 page
            app.logger.warning('author video sync failed for %s', source_id, exc_info=True)
            return jsonify({'success': False, 'ok': False, 'error': t('author.sync.error.unknown')}), 502
        return jsonify({'success': True, 'ok': True, **result})

    @app.post('/api/author/<source_id>/search')
    def author_search(source_id):
        _source_or_404(source_id)
        if tiktok_service is None or not _sidecar_base_url():
            return jsonify({'success': False, 'ok': False, 'error': t('author.refresh.unconfigured')}), 503
        keyword = str(_json_body().get('keyword') or '').strip()
        try:
            candidates = tiktok_service.list_author_candidates(keyword)
        except ValueError as exc:
            return jsonify({'success': False, 'ok': False, 'error': str(exc)}), 400
        except TikTokDownloaderError as exc:
            app.logger.warning('author sidecar request failed: %s', exc)
            return jsonify({'success': False, 'ok': False, 'error': tiktok_user_message(exc)}), 502
        except Exception:
            app.logger.warning('author candidate search failed', exc_info=True)
            return jsonify({'success': False, 'ok': False, 'error': t('author.refresh.error')}), 502
        return jsonify({'success': True, 'ok': True, 'candidates': candidates})

    def _uid_index() -> dict:
        """Export-folder UID → source id, so a bare profile link can be matched."""
        index = {}
        for source_id, source in (library_service.sources_by_id or {}).items():
            folder_uid = author_profiles.douyin_id_from_folder(folder_name_for(source))
            if folder_uid:
                index.setdefault(folder_uid, str(source_id))
        return index

    @app.get('/api/author/sources')
    def author_sources():
        """One row per author, for the settings sync card.

        A shared media root holds several bloggers: a row per source made every
        one of them sync into a single cache entry that none of their pages read,
        so the card reported success and the author page stayed empty. Each export
        folder gets its own row, and the row carries the ``folder`` that the sync
        endpoints scope by.
        """
        items = []
        for source_id, source in (library_service.sources_by_id or {}).items():
            folder_name = folder_name_for(source)
            records = media_index.records(source_id=source_id, album_covers_only=True)
            folders = [] if is_blogger_folder(folder_name) else author_folders(records)
            rows = [(entry['folder'], entry['count']) for entry in folders]
            if not rows:
                # The source is one blogger's export folder, or a root with no
                # export folders in it. Either way the source itself is the author.
                rows = [('', len(records))]
            for folder, local_count in rows:
                key = profile_key(source_id, folder, folder_name)
                shared = (
                    author_profiles.inherited_profile(key, source_id, folder) if folder else {}
                )
                profile = author_profiles.resolve_profile(
                    key, folder_name=folder or folder_name,
                    shared_key=source_id if folder else '',
                )
                page_url = '/author/{}'.format(quote(str(source_id), safe=''))
                if folder:
                    page_url = '{}/{}'.format(page_url, quote(folder, safe=''))
                items.append({
                    'source_id': str(source_id),
                    'folder': folder,
                    'folder_name': folder or folder_name,
                    'display_name': display_name(key, folder or folder_name, profile),
                    'douyin_id': str(profile.get('douyin_id') or ''),
                    'nickname': str(profile.get('name') or ''),
                    'has_profile': bool(author_profiles.load_profile(key) or shared),
                    'profile_url': str(profile.get('profile_url') or ''),
                    'local_count': local_count,
                    'avatar_url': f'{page_url}/avatar',
                })
        items.sort(key=lambda item: (item['display_name'].lower(), item['source_id'], item['folder']))
        return jsonify({'success': True, 'ok': True, 'items': items})

    @app.post('/api/author/sync')
    def author_sync():
        """Sync author profiles from home-page links, one line per author.

        Accepts ``{items: [{source_id?, folder?, profile_url?, aweme_id?}]}`` or
        ``{text: "<source_id>|<profile_url>"}``. A line without a source id is
        matched to its export folder through the numeric UID inside the author
        block, which is the only identity proof a bare link carries. ``folder``
        names one blogger inside a shared root and scopes where the result is
        cached, so two authors in one root stop overwriting each other.
        """
        if not _sidecar_base_url():
            return jsonify({'success': False, 'ok': False, 'error': t('author.sync.error.unconfigured')}), 400
        payload = _json_body()
        entries = []
        raw_items = payload.get('items')
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                entries.append({
                    'source_id': str(item.get('source_id') or '').strip(),
                    'folder': str(item.get('folder') or '').strip(),
                    'profile_url': str(item.get('profile_url') or '').strip(),
                    'aweme_id': str(item.get('aweme_id') or '').strip(),
                })
        else:
            entries = parse_sync_text(payload.get('text'))
        if not entries:
            return jsonify({'success': False, 'ok': False, 'error': t('settings.sync.author_need_url')}), 400
        if len(entries) > MAX_SYNC_AUTHORS_PER_REQUEST:
            # 单次请求里串行调 N 次外部 sidecar（每次最长 150 s）会让一个 HTTP
            # 请求跑几十分钟、无进度且不可取消；超限直接拒绝，让前端分批。
            return jsonify({
                'ok': False,
                'error': t('settings.sync.author_too_many', n=MAX_SYNC_AUTHORS_PER_REQUEST),
            }), 400

        index = _uid_index()
        results = []
        try:
            for entry in entries:
                source_id = entry.get('source_id') or ''
                source = _source(source_id) if source_id else None
                if source_id and source is None:
                    results.append({
                        'ok': False,
                        'source_id': source_id,
                        'error': t('author.sync.error.unknown_source_id', id=source_id),
                    })
                    continue
                entry['folder_uid'] = author_profiles.douyin_id_from_folder(
                    entry.get('folder') or ''
                ) if entry.get('folder') else (
                    author_profiles.douyin_id_from_folder(folder_name_for(source)) if source else ''
                )
                if source:
                    entry['profile_key'] = profile_key(
                        source_id, entry.get('folder') or '', folder_name_for(source),
                    )
                results.extend(tiktok_service.sync_author_profiles([entry], source_lookup=index.get))
        except TikTokDownloaderError as exc:
            # The batch stopped early: keep the authors that already synced.
            synced = len([item for item in results if item.get('ok')])
            return jsonify({'success': False, 'ok': False, 'error': tiktok_user_message(exc), 'items': results, 'synced': synced}), 502
        except Exception:  # a sidecar hiccup must never become a 500 page
            # str(exc) leaked KeyError/TypeError text into the UI and gave the user
            # nothing actionable, so the detail goes to the log instead.
            app.logger.exception('Author sync failed')
            synced = len([item for item in results if item.get('ok')])
            return jsonify({
                'success': False,
                'ok': False,
                'error': t('author.sync.error.unknown'),
                'items': results,
                'synced': synced,
            }), 502

        synced = [item for item in results if item.get('ok')]
        if results and not synced:
            return jsonify({
                'ok': False,
                'error': str(results[0].get('error') or t('author.sync.error.unknown')),
                'items': results,
                'synced': 0,
            }), 502
        return jsonify({'success': True, 'ok': True, 'items': results, 'synced': len(synced)})

