"""Home, settings and shared library/cache statistics."""

from flask import render_template


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
        )

    @app.route('/api/cache/clear', methods=['POST'])
    def api_clear_cache():
        try:
            return {'success': True, **thumbnail_service.clear_cache()}
        except Exception as exc:
            return {'success': False, 'error': str(exc)}, 500

    def library_stats() -> dict:
        # Settings is the only page that can clear the thumbnail cache or repair the
        # author sync config, so a corrupt favorites.json (or a cache clear racing the
        # scan) must degrade to zeros instead of taking the whole page down.
        def safe(call, fallback):
            try:
                return call()
            except Exception:
                app.logger.exception('library stats unavailable')
                return fallback

        index = safe(media_index.stats, {}) or {}
        cache = safe(thumbnail_service.cache_stats, {}) or {}
        favorites = safe(favorite_service.load, []) or []
        return {
            'videos': index.get('videos', 0),
            'images': index.get('images', 0),
            'audios': index.get('audios', 0),
            'indexed_total': index.get('total', 0),
            'last_synced_at': index.get('last_synced_at'),
            'favorites': len(favorites) if isinstance(favorites, list) else 0,
            'cache_count': cache.get('count', 0),
            'cache_mb': cache.get('size_mb', 0),
        }

    @app.route('/api/library/stats')
    def api_library_stats():
        return library_stats()
