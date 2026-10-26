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

from flask import abort, current_app, jsonify, render_template, request, send_file

from tiklocal.i18n import t
from tiklocal.services import author_profiles
from tiklocal.services.media_naming import blogger_display_name
from tiklocal.services.tiktok_downloader import AuthorAmbiguousError, TikTokDownloaderError
from tiklocal.web.media_payloads import media_urls

AVATAR_MAX_AGE = 300
COMPACT_COUNT_THRESHOLD = 10000
TEN_THOUSAND = 10000


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
    return f'{count / TEN_THOUSAND:.1f}{t("author.unit.ten_thousand")}'


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


def build_tiles(records) -> list[dict]:
    """Grid tiles: one square cover per work, newest first, album covers only."""
    tiles = []
    for record in records or ():
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

    @app.route('/author/<source_id>')
    def author_view(source_id):
        source = _source_or_404(source_id)
        folder_name = folder_name_for(source)
        profile = author_profiles.resolve_profile(source_id, folder_name=folder_name)
        tiles = build_tiles(media_index.records(source_id=source_id, album_covers_only=True))
        return render_template(
            'author.html',
            menu='author',
            source_id=source_id,
            author_name=display_name(source_id, folder_name, profile),
            search_default=blogger_display_name(folder_name) or str(source_id or ''),
            douyin_id=str(profile.get('douyin_id') or ''),
            tiles=tiles,
            stats=build_stats(tiles, profile),
            refresh_enabled=bool(_sidecar_base_url()),
        )

    @app.route('/author/<source_id>/avatar')
    def author_avatar(source_id):
        _source_or_404(source_id)
        cached = author_profiles.avatar_path(source_id)
        if cached.is_file():
            return send_file(cached, mimetype='image/jpeg', max_age=AVATAR_MAX_AGE)
        bundled = Path(current_app.static_folder or '') / author_profiles.DEFAULT_AVATAR_FILENAME
        if not bundled.is_file():
            abort(404)
        return send_file(bundled, mimetype='image/jpeg', max_age=AVATAR_MAX_AGE)

    @app.post('/api/author/<source_id>/refresh')
    def author_refresh(source_id):
        source = _source_or_404(source_id)
        if not _sidecar_base_url():
            return jsonify({'ok': False, 'error': t('author.refresh.unconfigured')}), 400
        payload = _json_body()
        keyword = str(payload.get('keyword') or '').strip()
        keyword = keyword or blogger_display_name(folder_name_for(source))
        unique_id = str(payload.get('unique_id') or '').strip()
        uid = str(payload.get('uid') or '').strip()
        try:
            profile = tiktok_service.refresh_author_profile(
                source_id, keyword=keyword, unique_id=unique_id, uid=uid,
            )
        except AuthorAmbiguousError as exc:
            # Several accounts share the nickname: hand the picker back to the page.
            return jsonify({'ok': False, 'error': str(exc), 'candidates': exc.candidates}), 409
        except ValueError as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 400
        except TikTokDownloaderError as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 502
        except Exception as exc:  # a sidecar hiccup must never become a 500 page
            return jsonify({'ok': False, 'error': str(exc) or t('author.refresh.error')}), 502
        return jsonify({'ok': True, 'profile': profile})

    @app.post('/api/author/<source_id>/search')
    def author_search(source_id):
        _source_or_404(source_id)
        if tiktok_service is None or not _sidecar_base_url():
            return jsonify({'ok': False, 'error': t('author.refresh.unconfigured')}), 503
        keyword = str(_json_body().get('keyword') or '').strip()
        try:
            candidates = tiktok_service.list_author_candidates(keyword)
        except ValueError as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 400
        except TikTokDownloaderError as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 502
        except Exception as exc:
            return jsonify({'ok': False, 'error': str(exc) or t('author.refresh.error')}), 502
        return jsonify({'ok': True, 'candidates': candidates})

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
        """Every media source as an author row, for the settings sync card."""
        items = []
        for source_id, source in (library_service.sources_by_id or {}).items():
            folder_name = folder_name_for(source)
            profile = author_profiles.resolve_profile(source_id, folder_name=folder_name)
            items.append({
                'source_id': str(source_id),
                'folder_name': folder_name,
                'display_name': display_name(source_id, folder_name, profile),
                'douyin_id': str(profile.get('douyin_id') or ''),
                'nickname': str(profile.get('name') or ''),
                'has_profile': bool(author_profiles.load_profile(source_id)),
                'avatar_url': f'/author/{quote(str(source_id))}/avatar',
            })
        items.sort(key=lambda item: (item['display_name'].lower(), item['source_id']))
        return jsonify({'ok': True, 'items': items})

    @app.post('/api/author/sync')
    def author_sync():
        """Sync author profiles from home-page links, one line per author.

        Accepts ``{items: [{source_id?, profile_url?, aweme_id?}]}`` or ``{text:
        "<source_id>|<profile_url>"}``. A line without a source id is matched to
        its export folder through the numeric UID inside the author block, which
        is the only identity proof a bare link carries.
        """
        if not _sidecar_base_url():
            return jsonify({'ok': False, 'error': t('author.sync.error.unconfigured')}), 400
        payload = _json_body()
        entries = []
        raw_items = payload.get('items')
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                entries.append({
                    'source_id': str(item.get('source_id') or '').strip(),
                    'profile_url': str(item.get('profile_url') or '').strip(),
                    'aweme_id': str(item.get('aweme_id') or '').strip(),
                })
        else:
            entries = parse_sync_text(payload.get('text'))
        if not entries:
            return jsonify({'ok': False, 'error': t('settings.sync.author_need_input')}), 400

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
                entry['folder_uid'] = (
                    author_profiles.douyin_id_from_folder(folder_name_for(source)) if source else ''
                )
                results.extend(tiktok_service.sync_author_profiles([entry], source_lookup=index.get))
        except TikTokDownloaderError as exc:
            # The batch stopped early: keep the authors that already synced.
            synced = len([item for item in results if item.get('ok')])
            return jsonify({'ok': False, 'error': str(exc), 'items': results, 'synced': synced}), 502
        except Exception as exc:  # a sidecar hiccup must never become a 500 page
            synced = len([item for item in results if item.get('ok')])
            return jsonify({
                'ok': False,
                'error': str(exc) or t('author.sync.error.unknown'),
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
        return jsonify({'ok': True, 'items': results, 'synced': len(synced)})

