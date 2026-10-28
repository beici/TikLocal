"""Home, settings and shared library/cache statistics."""

import sqlite3
from pathlib import Path

from flask import render_template, request, session

from tiklocal.i18n import t
from tiklocal.services.auth import MIN_PASSWORD_LENGTH
from tiklocal.services.media_sources_store import (
    MediaSourceError,
    add_source,
    apply_sources,
    describe_sources,
    item_counts,
    remove_source,
    retarget_thumbnail_service,
    update_source,
)
from tiklocal.web import json_body


def register_settings_routes(
    app, media_index, favorite_service, thumbnail_service, app_version, *,
    media_source_store=None, library_service=None, library_indexer=None,
):
    @app.route('/')
    def home_view():
        """Quiet launchpad for the local media library."""
        return render_template('home.html', menu='home')

    @app.route('/settings/')
    def settings_view():
        stats = library_stats()
        return render_template(
            'settings.html', menu='settings', version=app_version,
            videos=stats['videos'], images=stats['images'], favorites=stats['favorites'],
            cache_count=stats['cache_count'], cache_size_mb=stats['cache_mb'],
            min_password_length=MIN_PASSWORD_LENGTH,
        )

    @app.route('/api/cache/clear', methods=['POST'])
    def api_clear_cache():
        try:
            return {'success': True, **thumbnail_service.clear_cache()}
        except Exception:
            # The raw OSError carries the absolute cache path; keep it in the log.
            app.logger.exception('Could not clear the thumbnail cache')
            return {'success': False, 'error': t('settings.cache.failed')}, 500

    @app.post('/api/auth/password')
    def api_change_password():
        """Rotate the access password from the settings page.

        The session that asks for the change survives it: :meth:`AuthStore.set_password`
        bumps the revision that every other cookie and paired device is checked
        against, so the owner stays signed in here while the phone they no longer
        want to share with is signed out. The failure path shares the login
        limiter, so guessing the current password costs the same five attempts.
        """
        auth_store = app.extensions.get('auth_store')
        if not app.extensions.get('auth_enabled') or auth_store is None:
            return {'success': False, 'error': t('settings.password.disabled')}, 400
        payload = json_body()
        current_password = str(payload.get('current_password') or '')
        new_password = str(payload.get('new_password') or '')
        if not current_password or not new_password:
            return {'success': False, 'error': t('settings.password.required')}, 400
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return {
                'success': False,
                'error': t('settings.password.too_short', n=MIN_PASSWORD_LENGTH),
            }, 400
        limiter = app.extensions.get('login_limiter')
        client_key = request.remote_addr or 'unknown'
        if limiter:
            retry_after = limiter.retry_after(client_key)
            if retry_after:
                minutes = max(1, (retry_after + 59) // 60)
                # Same Retry-After contract as /login, so a client can back off.
                return {
                    'success': False,
                    'error': t('login.error_too_many', minutes=minutes),
                }, 429, {'Retry-After': str(int(retry_after))}
        try:
            verified = auth_store.verify(current_password)
        except RuntimeError:
            # AuthStore raises this when auth.json exists but cannot be read, so the
            # password cannot be checked at all: answer "try again" like the write
            # path below instead of letting it become a 500.
            app.logger.exception('Could not read the access password')
            return {
                'success': False,
                'error': t('common.error.storage_unavailable'),
            }, 503
        if not verified:
            if limiter:
                limiter.record_failure(client_key)
            return {'success': False, 'error': t('settings.password.wrong_current')}, 400
        try:
            auth_store.set_password(new_password)
        except ValueError:
            return {
                'success': False,
                'error': t('settings.password.too_short', n=MIN_PASSWORD_LENGTH),
            }, 400
        except (OSError, RuntimeError):
            # The password is validated but auth.json could not be read or written (a
            # reader holding it, a full disk). That is "try again", not "internal
            # error", and the old password still works, so say so instead of a 500.
            app.logger.exception('Could not save the new access password')
            return {
                'success': False,
                'error': t('common.error.storage_unavailable'),
            }, 503
        if limiter:
            limiter.clear(client_key)
        session['auth_revision'] = auth_store.revision
        return {'success': True, 'revision': auth_store.revision}

    # --- Media library folders ------------------------------------------------
    # The settings page owns the folder list from here on: the launch
    # configuration (config.yaml / --media-source / MEDIA_ROOT) only seeds it the
    # first time. Edits are persisted first, then folded into the running
    # LibraryService, so the change is live without a restart.
    def _media_sources():
        sources = getattr(library_service, 'sources', None) or []
        return list(sources)

    def _download_target_id() -> str:
        manager = app.extensions.get('download_manager')
        return str(getattr(manager, 'output_source_id', '') or '')

    def _source_payload() -> dict:
        counts = item_counts(media_index, logger=app.logger)
        return {
            'sources': describe_sources(
                _media_sources(), counts=counts, download_source_id=_download_target_id(),
            ),
            'store_path': str(media_source_store.store_path) if media_source_store else '',
        }

    def _reason(error: MediaSourceError) -> str:
        # t() echoes the key when a translation is missing; showing the raw key to
        # the user is worse than the generic sentence.
        key = f'settings.library.source_error_{error.code}'
        message = t(key, **error.params)
        return t('settings.library.source_error_generic') if message == key else message

    def _persist_sources(sources, previous_ids: set[str]) -> None:
        """Write the list before touching the running app.

        Removal is recorded by id: launch-time sources (config.yaml,
        ``--media-source``) are merged back on the next start, so without the record
        a folder the user removed here would reappear after a restart.
        """
        if media_source_store is None:
            raise MediaSourceError('store')
        try:
            stored = media_source_store.load()
        except OSError:
            stored = None
        keep_ids = {source.id for source in sources}
        removed_ids = set(stored.removed_ids if stored else ()) | (previous_ids - keep_ids)
        media_source_store.save(sources, removed_ids=[item for item in removed_ids if item not in keep_ids])

    def _apply_change(sources, message: str) -> dict:
        previous_default = Path(library_service.media_root)
        previous_ids = {source.id for source in _media_sources()}
        try:
            _persist_sources(sources, previous_ids)
        except MediaSourceError as error:
            return {'success': False, 'error': _reason(error)}, 400
        except OSError:
            app.logger.exception('Could not save the media folder list')
            return {'success': False, 'error': t('settings.library.source_error_save')}, 500
        try:
            installed = apply_sources(library_service, sources)
        except MediaSourceError as error:
            return {'success': False, 'error': _reason(error)}, 400
        new_default = Path(library_service.media_root)
        app.config['MEDIA_ROOT'] = library_service.media_root
        if new_default != previous_default:
            # Cache keys embed the library root so two libraries cannot show each
            # other's pictures; a moved default has to move that identity with it.
            retarget_thumbnail_service(thumbnail_service, new_default)
        target = library_service.sources_by_id.get(_download_target_id())
        download_manager = app.extensions.get('download_manager')
        if download_manager is not None and target is not None:
            download_manager.media_root = Path(target.path).resolve()
        scan = {'indexed': 0, 'deleted': 0, 'busy': False, 'unavailable': []}
        if library_indexer is not None:
            try:
                result = library_indexer.sync()
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error) and 'busy' not in str(error):
                    raise
                # The folder list is already saved; only the rescan has to wait for
                # the next successful scan, so this is a warning, not a failure.
                app.logger.warning('Rescan after a folder change hit a busy database: %s', error)
                result = {'busy': True}
            scan = {
                'indexed': int(result.get('indexed') or 0),
                'deleted': int(result.get('deleted') or 0),
                'busy': bool(result.get('busy')),
                'unavailable': list(result.get('unavailable_sources') or []),
            }
        app.logger.info(
            'Media folders updated: %s', ', '.join(f"@{source.id}={source.path}" for source in installed),
        )
        return {'success': True, 'message': message, 'data': {**_source_payload(), 'scan': scan}}

    @app.get('/api/library/sources')
    def api_media_sources():
        try:
            return {'success': True, 'data': _source_payload()}
        except sqlite3.Error:
            # A damaged index must not hide the folder list: the user has to be
            # able to point the app somewhere else to recover.
            app.logger.exception('Could not read media folders')
            return {'success': False, 'error': t('settings.library.source_error_generic')}, 500

    @app.post('/api/library/sources')
    def api_add_media_source():
        payload = json_body()
        try:
            sources, source = add_source(
                _media_sources(), path=payload.get('path'), name=payload.get('name'),
            )
        except MediaSourceError as error:
            return {'success': False, 'error': _reason(error)}, 400
        return _apply_change(sources, t('settings.library.source_added', name=source.name))

    @app.patch('/api/library/sources/<source_id>')
    def api_update_media_source(source_id):
        payload = json_body()
        try:
            sources, source = update_source(
                _media_sources(), source_id, path=payload.get('path'), name=payload.get('name'),
            )
        except MediaSourceError as error:
            return {'success': False, 'error': _reason(error)}, 400
        return _apply_change(sources, t('settings.library.source_updated', name=source.name))

    @app.delete('/api/library/sources/<source_id>')
    def api_remove_media_source(source_id):
        try:
            sources = remove_source(
                _media_sources(), source_id, protected_ids=(_download_target_id(),),
            )
        except MediaSourceError as error:
            return {'success': False, 'error': _reason(error)}, 400
        return _apply_change(sources, t('settings.library.source_removed'))

    def library_stats() -> dict:
        # Settings is the only page that can clear the thumbnail cache or repair the
        # author sync config, so a corrupt favorites.json (or a cache clear racing the
        # scan) must degrade to zeros instead of taking the whole page down.
        def safe(call, fallback):
            try:
                return call()
            except sqlite3.DatabaseError:
                # Reporting "0 media" for a damaged index hides the real failure and
                # contradicts /library, which surfaces the same error as a 500.
                raise
            except Exception:
                app.logger.exception('library stats unavailable')
                return fallback

        index = safe(media_index.stats, {}) or {}
        cache = safe(thumbnail_service.cache_stats, {}) or {}
        # favorite_service.load() returns a set, not a list.
        favorites = safe(favorite_service.load, set()) or set()
        return {
            'videos': index.get('videos', 0),
            'images': index.get('images', 0),
            'audios': index.get('audios', 0),
            'indexed_total': index.get('total', 0),
            'last_synced_at': index.get('last_synced_at'),
            'favorites': len(favorites) if isinstance(favorites, (list, set, tuple)) else 0,
            'cache_count': cache.get('count', 0),
            'cache_mb': cache.get('size_mb', 0),
        }

    @app.route('/api/library/stats')
    def api_library_stats():
        return library_stats()
