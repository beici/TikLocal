"""Home, settings and shared library/cache statistics."""

import sqlite3

from flask import render_template, request, session

from tiklocal.i18n import t
from tiklocal.services.auth import MIN_PASSWORD_LENGTH


def register_settings_routes(app, media_index, favorite_service, thumbnail_service, app_version):
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
        payload = request.get_json(silent=True) or {}
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
        if not auth_store.verify(current_password):
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
        if limiter:
            limiter.clear(client_key)
        session['auth_revision'] = auth_store.revision
        return {'success': True, 'revision': auth_store.revision}

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
