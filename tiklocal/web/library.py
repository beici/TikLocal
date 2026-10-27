"""Library, favorites and collections share one browsing/query boundary."""

import datetime
import random
import sqlite3
from pathlib import Path
from urllib.parse import quote

from flask import render_template, request

from tiklocal.i18n import t
from tiklocal.services.collections import CollectionStoreCorrupt, normalize_collection_mutation_uris
from tiklocal.services.library import IMAGE_EXTENSIONS
from tiklocal.services.media_naming import blogger_display_name
from tiklocal.web.media_payloads import build_feed_media_item, media_urls, serialize_library_item
from tiklocal.web import json_body, read_int_arg


def register_library_routes(
    app, *, library_service, media_index, library_indexer, favorite_service,
    collection_store, activity_store, media_search_available=None,
):
    @app.errorhandler(CollectionStoreCorrupt)
    def handle_collections_corrupt(exc):
        # Refuse to overwrite a collections file we cannot read; the store keeps a
        # .corrupt copy next to it so the user can recover the original data.
        app.logger.error('Collections file is unreadable: %s', exc)
        message = t('library.collection.store_corrupt')
        if request.path.startswith('/api/'):
            return {'success': False, 'error': message}, 503
        return message, 503

    def _read_page_options(scope: str = 'all') -> dict:
        mode = str(request.args.get('mode', 'all')).strip()
        if scope != 'all' or mode not in {'all', 'image_random', 'video_latest', 'big_files'}:
            mode = 'all'
        search = str(request.args.get('q', '')).strip()[:200] if scope == 'all' else ''
        month = str(request.args.get('month', '')).strip() if scope == 'all' else ''
        if not media_index.is_month_key(month):
            month = ''
        if search:
            mode = 'all'
        seed = str(request.args.get('seed', '')).strip()
        if mode == 'image_random' and not seed:
            seed = str(random.randint(1, 999999))
        blog = str(request.args.get('blog', '')).strip() if scope == 'all' else ''
        if blog and blog not in library_service.sources_by_id:
            blog = ''
        return {
            'mode': mode,
            'search': search,
            'month': month,
            'seed': seed,
            'blog': blog,
            'offset': read_int_arg('offset', 0, minimum=0),
            'limit': read_int_arg('limit', 24, minimum=12, maximum=96),
            'min_mb': read_int_arg('min_mb', 50, minimum=1, maximum=10240),
        }

    def _collection_chip_rows() -> list[dict]:
        """Lightweight collection summaries for filter chips (no cover IO)."""
        return [
            {
                'id': str(item.get('id') or ''),
                'name': str(item.get('name') or ''),
                'item_count': int(item.get('item_count') or 0),
            }
            for item in collection_store.list()
        ]

    def _blog_chips() -> list[dict]:
        chips = []
        for row in media_index.blog_rows():
            source = library_service.sources_by_id.get(row['source_id'])
            if not source:
                continue
            # Sources are usually named after their export folder; fall back to
            # the folder itself when the id was reused as the display name.
            folder = Path(source.path).name if str(source.path) not in {'', '.', '/'} else ''
            display = folder or source.name
            chips.append({
                'id': row['source_id'],
                'name': blogger_display_name(display),
                'count': row['count'],
                'thumb_url': media_urls(row['latest_uri'])['thumb_url'] if row['latest_uri'] else '',
                'flow_url': f"/flow?blog={row['source_id']}",
            })
        return chips

    def _render_library_page(
        page: dict, *, scope: str = 'all', mode: str = 'all',
        min_mb: int = 50, collection: dict | None = None, **context,
    ):
        collection = collection or {}
        return render_template(
            'library.html',
            menu='library' if scope == 'all' else 'favorite',
            scope=scope,
            collection_id=collection.get('id', ''),
            collection_name=collection.get('name', ''),
            collection_description=collection.get('description', ''),
            collection_count=collection.get('item_count', 0),
            collection_cover_uri=collection.get('cover_uri', ''),
            collection_preview_items=collection.get('preview_items', []),
            active_mode=mode,
            mode_seed=page['seed'],
            min_mb=min_mb,
            empty_message={
                'all': t('library.empty.all'),
                'favorite': t('library.empty.favorite'),
                'collection': t('library.empty.collection'),
            }[scope],
            initial_items=page['items'],
            initial_has_more=page['has_more'],
            initial_offset=page['offset'],
            initial_next_offset=page['next_offset'],
            page_size=page['limit'],
            **context,
        )

    def _serialize_collection_summary(collection: dict, indexed_uris: set[str] | None = None) -> dict:
        collection_id = str(collection.get('id') or '')
        cover_uri = str(collection.get('cover_uri') or '').strip()
        stored_items = collection.get('items')
        if isinstance(stored_items, list) and stored_items:
            item_uris = [str(entry.get('uri') or '') for entry in reversed(stored_items) if isinstance(entry, dict)]
        else:
            item_uris = collection_store.list_item_uris(collection_id, newest_first=True)
        candidates = [cover_uri, *item_uris]
        previews = []
        seen = set()
        # Four covers are enough; when files were deleted outside the app the list
        # is all misses, and resolving every stored item cost ~2 s at 20 cards.
        for candidate in candidates[:12]:
            if not candidate:
                continue
            if indexed_uris is not None:
                # The index already answered "does this file exist"; a stat per
                # candidate cost a Windows round trip and dominated the endpoint.
                uri = library_service.canonicalize_uri(candidate)
                if not uri or uri in seen or uri not in indexed_uris:
                    continue
                media_type = 'image' if uri.lower().endswith(tuple(IMAGE_EXTENSIONS)) else 'video'
            else:
                uri = library_service.find_existing_uri(candidate)
                if not uri or uri in seen:
                    continue
                target = library_service.resolve_path(uri)
                if not target or not target.exists() or not target.is_file():
                    continue
                media_type = 'image' if target.suffix.lower() in IMAGE_EXTENSIONS else 'video'
            seen.add(uri)
            previews.append({
                'uri': uri,
                'type': media_type,
                'thumb_url': media_urls(uri)['thumb_url'],
            })
            if len(previews) == 4:
                break
        cover_uri = previews[0]['uri'] if previews else ''
        # The stored list never shrinks when a file is deleted elsewhere, so the
        # card kept promising more items than the collection can open. Count through
        # the same query the detail header uses so both agree.
        item_count = int(collection.get('item_count') or 0)
        if indexed_uris is not None:
            # Counting against the pre-fetched index is O(items) per card; running a
            # full page query per card made /api/collections 535 ms at 20 cards.
            item_count = sum(1 for uri in item_uris if uri in indexed_uris)
        elif collection_id:
            try:
                item_count = int(_build_library_page(collection_id=collection_id, offset=0, limit=1).get('total') or 0)
            except Exception:  # noqa: BLE001 - a broken index must not hide the card
                item_count = int(collection.get('item_count') or 0)
        return {
            'id': collection_id,
            'name': str(collection.get('name') or ''),
            'description': str(collection.get('description') or ''),
            'item_count': item_count,
            'cover_uri': cover_uri,
            'cover_type': previews[0]['type'] if previews else '',
            'cover_media_url': media_urls(cover_uri)['media_url'] if cover_uri else '',
            'preview_items': previews,
            'detail_url': f"/collection/{quote(collection_id, safe='')}" if collection_id else '#',
            'created_at': str(collection.get('created_at') or ''),
            'updated_at': str(collection.get('updated_at') or ''),
        }

    def _build_library_page(
        *,
        favorites_only: bool = False,
        mode: str = 'all',
        offset: int = 0,
        limit: int = 24,
        min_mb: int = 50,
        seed: str = '',
        collection_id: str = '',
        search: str = '',
        month: str = '',
        blog: str = '',
    ) -> dict:
        uris = None
        if collection_id:
            uris = collection_store.list_item_uris(collection_id, newest_first=True)
        elif favorites_only:
            uris = favorite_service.load()
        page = media_index.page(
            mode=mode, seed=seed, search=search, month=month,
            min_size=min_mb * 1024 * 1024 if mode == 'big_files' else 0,
            offset=offset, limit=limit, uris=uris, preserve_order=bool(collection_id),
            source_id=blog if uris is None else '',
        )
        records = page.pop('records')
        items = [serialize_library_item(record) for record in records]
        positions = activity_store.positions_for([item['name'] for item in items if item['type'] == 'video'])
        for item in items:
            if spot := positions.get(item['name']):
                item['resume'] = spot['position']
                item['progress'] = round(spot['position'] / spot['duration'], 3)
        return {
            **page,
            'items': items,
            'seed': seed,
        }

    def _serialize_timeline_payload(payload: dict) -> dict:
        months = []
        for month in payload.get('months') or []:
            covers = []
            for record in month.get('records') or []:
                item = build_feed_media_item(
                    str(record.get('name') or ''),
                    str(record.get('media_type') or 'image'),
                )
                covers.append({
                    **item,
                    'mtime_ts': float(record.get('mtime_ts') or 0),
                })
            months.append({
                'key': str(month.get('key') or ''),
                'count': int(month.get('count') or 0),
                'image_count': int(month.get('image_count') or 0),
                'video_count': int(month.get('video_count') or 0),
                'covers': covers,
            })
        return {
            'months': months,
            'years': [
                {
                    'year': str(year.get('year') or ''),
                    'count': int(year.get('count') or 0),
                    'month_count': int(year.get('month_count') or 0),
                }
                for year in payload.get('years') or []
            ],
            'has_more': payload.get('has_more') is True,
            'next_before': str(payload.get('next_before') or ''),
        }

    @app.route('/library')
    def library_view():
        requested_view = str(request.args.get('view', '')).strip()
        if requested_view not in {'timeline', 'explore', 'month'}:
            requested_view = 'explore' if any(request.args.get(key) for key in ('mode', 'q', 'focus')) else 'timeline'
        options = _read_page_options()
        options['offset'] = 0
        if requested_view == 'month' and not options['month']:
            requested_view = 'timeline'
        timeline_payload = {'months': [], 'has_more': False, 'next_before': ''}
        if requested_view == 'timeline':
            timeline_payload = _serialize_timeline_payload(
                media_index.timeline_months(limit=8, preview_limit=18)
            )
            initial_page = {
                'items': [], 'total': 0, 'offset': 0, 'limit': options['limit'],
                'next_offset': 0, 'has_more': False, 'seed': '',
            }
        else:
            if requested_view == 'month':
                options['mode'] = 'all'
            initial_page = _build_library_page(**options)
        return _render_library_page(
            initial_page, mode=options['mode'], min_mb=options['min_mb'],
            library_view=requested_view, timeline_month=options['month'],
            timeline_payload=timeline_payload,
            media_search_available=bool(media_search_available and media_search_available()),
            blog=options['blog'],
            blogs=_blog_chips(),
            library_collections=_collection_chip_rows() if requested_view != 'timeline' else [],
        )

    @app.route('/favorite')
    def favorite_view():
        collection_id = str(request.args.get('collection', '')).strip()
        collection = collection_store.get(collection_id) if collection_id else None
        page = _build_library_page(
            collection_id=collection['id'] if collection else '',
            favorites_only=not collection,
            limit=read_int_arg('limit', 24, minimum=12, maximum=96),
        )
        return _render_library_page(
            page, scope='favorite',
            favorite_collections=_collection_chip_rows(),
            **({'collection': _serialize_collection_summary(collection)} if collection else {}),
        )

    @app.route('/collections')
    def collections_view():
        return render_template('collections.html', menu='favorite')

    @app.route('/collection/<collection_id>')
    def collection_detail_view(collection_id):
        collection = collection_store.get(collection_id)
        if not collection:
            return t('library.collection.not_found'), 404
        page = _build_library_page(
            collection_id=collection_id,
            limit=read_int_arg('limit', 24, minimum=12, maximum=96),
        )
        summary = _serialize_collection_summary(collection)
        # The stored item_count is historical: deleting a file through the app
        # removes the index row but leaves the collection entry behind, so the
        # header promised more items than the grid (which joins the index) shows.
        summary['item_count'] = int(page.get('total') or 0)
        return _render_library_page(page, scope='collection', collection=summary)

    @app.route('/api/library/items')
    def api_library_items():
        scope = str(request.args.get('scope', 'all')).strip()
        collection_id = ''
        if scope == 'collection':
            collection_id = str(request.args.get('collection_id', '')).strip()
            if not collection_id:
                return {'success': False, 'error': t('library.error.collection_id_required')}, 400
            # A deleted collection used to fall through to the whole library, so
            # "load more" in an old tab appended every item into the collection grid.
            if collection_store.get(collection_id) is None:
                return {'success': False, 'error': t('library.collection.not_found')}, 404
        page = _build_library_page(
            favorites_only=scope == 'favorite', collection_id=collection_id,
            **_read_page_options(scope),
        )
        return {'success': True, 'data': page}

    @app.route('/api/library/on-this-day')
    def api_on_this_day():
        """Past years' media from today's date, widening to the surrounding week when the day is empty."""
        try:
            today = datetime.date.fromisoformat(str(request.args.get('date', '')))
        except ValueError:
            today = datetime.date.today()
        # `date` is client supplied and may sit at 0001/9999, where widening by a
        # week raises OverflowError (HTTP 500). Keep the window inside the range.
        earliest = datetime.date.min + datetime.timedelta(days=3)
        latest = datetime.date.max - datetime.timedelta(days=3)
        today = min(max(today, earliest), latest)
        window = 'day'
        records = media_index.on_days([f"{today:%m-%d}"], str(today.year), per_year=8)
        if not records:
            window = 'week'
            week = [today + datetime.timedelta(days=offset) for offset in range(-3, 4)]
            records = media_index.on_days([f"{day:%m-%d}" for day in week], str(today.year), per_year=8)
        years = {}
        for record in records:
            year = record['captured_local_date'][:4]
            group = years.setdefault(year, {'year': int(year), 'years_ago': today.year - int(year), 'items': []})
            if len(group['items']) < 8:
                group['items'].append(serialize_library_item(record))
        return {'success': True, 'data': {'date': today.isoformat(), 'window': window, 'years': list(years.values())}}

    @app.route('/api/library/timeline')
    def api_library_timeline():
        before = str(request.args.get('before', '')).strip()
        limit = read_int_arg('limit', 18, minimum=1, maximum=36)
        preview_limit = read_int_arg('preview_limit', 18, minimum=1, maximum=18)
        payload = media_index.timeline_months(
            before=before,
            limit=limit,
            preview_limit=preview_limit,
        )
        return {
            'success': True,
            'data': _serialize_timeline_payload(payload),
        }

    @app.route('/api/library/blogs')
    def api_library_blogs():
        return {'success': True, 'data': {'items': _blog_chips()}}

    @app.route('/api/library/sync', methods=['POST'])
    def api_library_sync():
        # Another connection holding the write lock (a second tab syncing, the
        # downloader registering outputs) surfaced as a bare 500 with the raw
        # SQLite English text; it is a retryable conflict, not a server fault.
        try:
            result = library_indexer.sync()
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc) or 'busy' in str(exc):
                app.logger.warning('Library sync hit a busy database: %s', exc)
                return {'success': False, 'error': t('library.error.busy')}, 409
            raise
        return {'success': True, 'data': result}

    @app.route('/api/favorite/<path:name>', methods=['GET', 'POST'])
    def api_favorite(name):
        name = library_service.canonicalize_uri(name)
        try:
            if request.method == 'GET':
                return {'favorite': favorite_service.is_favorite(name)}
            # A tab that has been open for a while holds a stale `isFavorited`, so a
            # blind toggle can execute the opposite of what the button promised when
            # another tab (or device) changed it. Callers may therefore state the
            # desired end state and get an idempotent set instead of a toggle.
            desired = request.args.get('favorited')
            if desired is None:
                body = request.get_json(silent=True)
                if isinstance(body, dict) and isinstance(body.get('favorited'), bool):
                    return {'success': True, 'favorite': favorite_service.set_favorite(name, body['favorited'])}
            elif desired.lower() in {'1', 'true', 'yes', 'on', 'false', '0', 'no', 'off'}:
                return {'success': True, 'favorite': favorite_service.set_favorite(name, desired.lower() in {'1', 'true', 'yes', 'on'})}
            return {'success': True, 'favorite': favorite_service.toggle(name)}
        except (OSError, ValueError):
            return {'success': False, 'error': t('library.error.favorite_failed')}, 503

    @app.route('/api/collections', methods=['GET', 'POST'])
    def api_collections():
        if request.method == 'GET':
            collections = collection_store.list_with_items()
            wanted = {
                str(entry.get('uri') or '')
                for collection in collections
                for entry in (collection.get('items') or [])
                if isinstance(entry, dict)
            }
            try:
                indexed_uris = media_index.existing_uris(wanted)
            except Exception:  # noqa: BLE001 - a broken index must not hide the cards
                indexed_uris = None
            items = [_serialize_collection_summary(item, indexed_uris) for item in collections]
            return {'success': True, 'data': {'items': items}}

        payload = json_body()
        raw_name = payload.get('name', '')
        raw_description = payload.get('description', '')
        # str(None) is 'None' and str([]) is '[]': a JSON null/array used to land in
        # the store as a collection literally named "None".
        if raw_name is not None and not isinstance(raw_name, str):
            return {'success': False, 'error': t('library.error.name_required')}, 400
        if raw_description is not None and not isinstance(raw_description, str):
            return {'success': False, 'error': t('library.error.name_required')}, 400
        name = (raw_name or '').strip()
        description = (raw_description or '').strip()
        if not name:
            return {'success': False, 'error': t('library.error.name_required')}, 400
        try:
            created = collection_store.create(name=name, description=description)
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}, 400
        except OSError as exc:
            app.logger.warning('Could not save the collection', exc_info=exc)
            return {'success': False, 'error': t('common.error.storage_unavailable')}, 503
        return {'success': True, 'data': {'item': _serialize_collection_summary(created)}}

    @app.route('/api/collections/<collection_id>', methods=['GET', 'PATCH', 'DELETE'])
    def api_collection_detail(collection_id):
        if request.method == 'GET':
            found = collection_store.get(collection_id)
            if not found:
                return {'success': False, 'error': t('library.collection.not_found')}, 404
            return {'success': True, 'data': {'item': _serialize_collection_summary(found)}}

        if request.method == 'DELETE':
            deleted = collection_store.delete(collection_id)
            if not deleted:
                return {'success': False, 'error': t('library.collection.not_found')}, 404
            return {'success': True, 'data': {'deleted': True}}

        payload = json_body()
        name = payload['name'] if 'name' in payload else None
        description = payload['description'] if 'description' in payload else None
        cover_uri = library_service.canonicalize_uri(payload['cover_uri']) if 'cover_uri' in payload else None
        try:
            updated = collection_store.update(
                collection_id,
                name=name,
                description=description,
                cover_uri=cover_uri,
            )
        except ValueError as exc:
            return {'success': False, 'error': str(exc)}, 400
        if not updated:
            return {'success': False, 'error': t('library.collection.not_found')}, 404
        return {'success': True, 'data': {'item': _serialize_collection_summary(updated)}}

    @app.route('/api/collections/<collection_id>/items', methods=['GET', 'POST', 'DELETE'])
    def api_collection_items(collection_id):
        if request.method == 'GET':
            found = collection_store.get(collection_id)
            if not found:
                return {'success': False, 'error': t('library.collection.not_found')}, 404
            offset = read_int_arg('offset', 0, minimum=0)
            limit = read_int_arg('limit', 24, minimum=12, maximum=96)
            page = _build_library_page(
                offset=offset,
                limit=limit,
                collection_id=collection_id,
            )
            return {'success': True, 'data': page}

        payload = json_body()
        uris = [
            library_service.canonicalize_uri(uri)
            for uri in normalize_collection_mutation_uris(payload.get('uris'))
        ]
        if not uris:
            return {'success': False, 'error': t('library.error.uris_required')}, 400

        if request.method == 'POST':
            updated = collection_store.add_items(collection_id, uris)
        else:
            updated = collection_store.remove_items(collection_id, uris)
        if not updated:
            return {'success': False, 'error': t('library.collection.not_found')}, 404
        return {'success': True, 'data': {'item': _serialize_collection_summary(updated)}}

    @app.route('/api/collections/by-media')
    def api_collections_by_media():
        uri = str(request.args.get('uri', '')).strip()
        if not uri:
            return {'success': False, 'error': t('library.error.uri_required')}, 400
        canonical_uri = library_service.canonicalize_uri(uri)
        items = collection_store.list_for_media(canonical_uri)
        if not items:
            for legacy_uri in library_service.legacy_candidates(canonical_uri)[1:]:
                items = collection_store.list_for_media(legacy_uri)
                if items:
                    break
        payload = [_serialize_collection_summary(item) for item in items]
        return {'success': True, 'data': {'items': payload}}
