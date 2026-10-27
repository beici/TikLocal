import os
import datetime
import gc
import logging
import sqlite3
import socket
import time
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, url_for

# Service Imports
from tiklocal.logging_setup import configure_logging
from tiklocal.services.library import LibraryService, build_media_sources
from tiklocal.services.favorites import FavoriteService
from tiklocal.services.recommendation import RecommendService
from tiklocal.services.thumbnail import ThumbnailService
from tiklocal.services.metadata import ImageMetadataStore
from tiklocal.services.captions import CaptionSettings
from tiklocal.web.captions import register_caption_routes
from tiklocal.web.author import register_author_routes
from tiklocal.web.downloads import register_download_routes
from tiklocal.web.library import register_library_routes
from tiklocal.services.database import AppDatabase, MediaActivityStore
from tiklocal.services.library_index import LibraryIndexer, MediaIndexStore, MediaProbeWorker
from tiklocal.services.trash import TrashService
from tiklocal.services.downloader import (
    DownloadConfigStore,
    DownloadHistoryStore,
    DownloadManager,
)
from tiklocal.services.download_sources import DownloadSourceStore
from tiklocal.services.tiktok_downloader import TikTokDownloadService, seed_tiktok_downloader_config
from tiklocal.services.collections import CollectionStore
from tiklocal.services.radio import RadioProfileStore, RadioService
from tiklocal.services.radio_energy import RadioEnergyService
from tiklocal.services.auth import AuthStore
from tiklocal.services.device_auth import DeviceAuthStore
from tiklocal.services.pairing_grants import PairingGrantStore
from tiklocal.auth import configure_auth
from tiklocal.i18n import current_locale, js_catalog, t, translate
from tiklocal.radio_client import register_radio_client_routes
from tiklocal.paths import (
    get_data_dir,
    get_metadata_path,
    get_favorites_path,
    get_prompt_config_path,
    get_llm_config_path,
    get_database_path,
    get_download_config_path,
    get_download_jobs_path,
    get_download_sources_path,
    get_collections_path,
    get_radio_profile_path,
    get_auth_path,
    get_device_auth_path,
)
from tiklocal.web.flow import register_flow_routes
from tiklocal.web.radio import register_radio_routes
from tiklocal.web.media import register_media_routes
from tiklocal.web.settings import register_settings_routes
from tiklocal.config import similarity_enabled
from tiklocal import __version__


def get_app_version():
    """Return the app version, reading pyproject.toml during development."""
    # Prefer pyproject.toml when running from a development checkout.
    pyproject_path = Path(__file__).parent.parent / 'pyproject.toml'
    if pyproject_path.exists():
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                tomllib = None

        if tomllib:
            try:
                with open(pyproject_path, 'rb') as f:
                    data = tomllib.load(f)
                    return data.get('tool', {}).get('poetry', {}).get('version') or __version__
            except Exception:
                pass

    # Installed builds read their package metadata.
    try:
        return version("tiklocal")
    except PackageNotFoundError:
        # Never claim a release that does not exist on PyPI.
        return __version__


app_version = get_app_version()

_logger = logging.getLogger(__name__)

_DAMAGED_DATABASE_MARKERS = (
    'malformed',
    'not a database',
    'file is encrypted',
    'corrupt',
)


def _quick_check(database: AppDatabase) -> None:
    """Raise DatabaseError when SQLite reports a malformed image.

    ``PRAGMA quick_check`` returns a row per problem (and a single ``ok`` row when
    the file is sound), so anything else means the btree is broken even though the
    schema pages still open.
    """
    with database.connect() as connection:
        rows = connection.execute('PRAGMA quick_check').fetchall()
    problems = [str(row[0]) for row in rows if str(row[0]).lower() != 'ok']
    if problems:
        raise sqlite3.DatabaseError('database disk image is malformed: ' + '; '.join(problems[:3]))


def _is_damaged_database(error: sqlite3.DatabaseError) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _DAMAGED_DATABASE_MARKERS)


def _quarantine_database(db_path: Path) -> Path | None:
    """Move a damaged SQLite file aside so a fresh one can be created."""
    suffix = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = db_path.with_name(f'{db_path.name}.{suffix}.corrupt')
    for attempt in range(3):
        try:
            db_path.replace(backup)
            break
        except OSError as error:
            # Windows keeps the file locked until the last handle is closed; the
            # failing sqlite connection is usually released by the next GC pass.
            if attempt == 2:
                _logger.error('Unable to quarantine damaged database %s: %s', db_path, error)
                return None
            gc.collect()
            time.sleep(0.05)
    for extra in ('-wal', '-shm'):
        sidecar = db_path.with_name(db_path.name + extra)
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass
    return backup


def _open_database(db_path: Path) -> AppDatabase:
    """Open and migrate the SQLite store, rebuilding it when the file is damaged.

    A half-written file after a power cut (or a cloud client / antivirus rewriting
    it) used to abort start-up with a raw sqlite traceback, leaving the whole app
    unusable with no hint about what to delete. The media index is rebuildable, so
    the damaged file is kept next to the new one instead of blocking boot.
    """
    database = AppDatabase(db_path)
    try:
        database.migrate()
        # migrate() only reads schema_migrations (pages 1-2), so a file whose data
        # pages are destroyed still migrates cleanly and then blows up in the first
        # index sync -- after the app has started serving. quick_check walks the
        # btree pages and costs milliseconds on a local index.
        _quick_check(database)
        return database
    except sqlite3.DatabaseError as error:
        if not _is_damaged_database(error):
            raise
        message = str(error)
        # The traceback keeps the failing frame (and its sqlite handle) alive, which
        # blocks the rename on Windows; drop it before moving the file.
        error.__traceback__ = None
        del error
        gc.collect()
        backup = _quarantine_database(db_path)
        if backup is None:
            raise sqlite3.DatabaseError(message)
        _logger.warning(
            'Database file was damaged (%s); moved it to %s and started with an empty index.',
            message,
            backup,
        )
        fresh = AppDatabase(db_path)
        fresh.migrate()
        return fresh


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    # Configure logging before anything can touch app.logger: Flask then skips its
    # default stderr handler and waitress' late basicConfig() becomes a no-op, so a
    # record is written once -- to stderr and to the rotating file. The file lives
    # with the data (TIKLOCAL_INSTANCE / ~/.tiklocal): Flask's instance_path sits
    # next to the package, which is read-only for a wheel install, and the log then
    # silently vanished.
    configure_logging(get_data_dir())
    app.config.from_mapping(
        SECRET_KEY = None,
        # Unauthenticated endpoints such as /api/v1/pair must not be able to make
        # the server buffer arbitrary request bodies (the default host is 0.0.0.0).
        MAX_CONTENT_LENGTH = 8 * 1024 * 1024,
        MEDIA_ROOT = Path(os.environ.get('MEDIA_ROOT', '.')),
        MEDIA_SOURCES = None,
        DOWNLOAD_SOURCE = 'default',
        VISION_CONFIG = None,
        EMBEDDING_CONFIG = None,
        EXPERIMENTS = None,
        AUTH_ENABLED = None,
        AUTH_COOKIE_SECURE = False,
        DEVICE_AUTH_PATH = None,
        INSTANCE_NAME = None,
    )
    app.config.from_pyfile('config.py', silent=True)
    app.config.from_prefixed_env()
    if test_config is not None:
        app.config.update(test_config)

    similarity_active = similarity_enabled(app.config.get('EXPERIMENTS'), app.config.get('EMBEDDING_CONFIG'))

    instance_name = str(
        app.config.get('INSTANCE_NAME')
        or os.environ.get('TIKLOCAL_NAME')
        or socket.gethostname()
        or 'Local'
    ).strip()[:48] or 'Local'
    app.config['INSTANCE_NAME'] = instance_name
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    configured_auth = app.config.get('AUTH_ENABLED')
    auth_enabled = not bool(app.config.get('TESTING')) if configured_auth is None else bool(configured_auth)
    auth_store = AuthStore(app.config.get('AUTH_PATH') or get_auth_path())
    device_auth_store = DeviceAuthStore(
        app.config.get('DEVICE_AUTH_PATH') or get_device_auth_path()
    )
    pairing_grant_store = app.config.get('PAIRING_GRANT_STORE') or PairingGrantStore(
        ttl_seconds=int(app.config.get('RADIO_PAIRING_GRANT_TTL') or 120),
    )
    bootstrap = None
    if auth_enabled:
        bootstrap = auth_store.ensure(os.environ.get('TIKLOCAL_AUTH_PASSWORD'))
    configure_auth(
        app,
        auth_store,
        enabled=auth_enabled,
        device_auth_store=device_auth_store,
    )
    app.extensions['auth_bootstrap'] = bootstrap
    app.extensions['radio_pairing_grants'] = pairing_grant_store

    @app.template_global()
    def static_asset(filename: str) -> str:
        # Cache-bust by the file's own mtime so edited assets reach browsers on
        # a plain refresh; the version only tags files that cannot be stat'ed.
        try:
            stamp = hex(int(os.stat(os.path.join(app.static_folder, filename)).st_mtime))
        except OSError:
            stamp = app_version
        return url_for('static', filename=filename, v=stamp)

    @app.context_processor
    def app_identity_context():
        locale = current_locale()

        def t(key: str, **params: object) -> str:
            return translate(locale, key, **params)

        return {
            'app_version': app_version,
            'instance_name': instance_name,
            'similarity_enabled': similarity_active,
            'locale': locale,
            't': t,
            'i18n_js': js_catalog(locale),
        }

    @app.template_filter('timestamp_to_date')
    def timestamp_to_date(timestamp):
        locale = current_locale()
        try:
            moment = datetime.datetime.fromtimestamp(float(timestamp))
        except (TypeError, ValueError, OSError, OverflowError):
            return translate(locale, 'common.unknown_time')
        if locale == 'en':
            day = moment.strftime('%d').lstrip('0')
            return f"{moment.strftime('%b')} {day}, {moment.strftime('%Y %H:%M:%S')}"
        return moment.strftime('%Y-%m-%d %H:%M:%S')

    @app.after_request
    def apply_cache_policy(response):
        endpoint = request.endpoint or ''
        if endpoint == 'static':
            if request.args.get('v'):
                # The value is per-file (mtime) or the app version; either way
                # the URL changes whenever the content does.
                response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            else:
                response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
        elif endpoint == 'retired_service_worker':
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Service-Worker-Allowed'] = '/'
        elif request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'private, no-store'
        elif request.path.startswith('/media'):
            response.headers['Cache-Control'] = 'private, no-cache'
        elif request.path == '/thumb':
            # The route marks fallback/placeholder responses no-store and real
            # thumbnails no-cache; only untouched 2xx/3xx responses get an hour.
            cache_control = response.headers.get('Cache-Control', '')
            if response.status_code < 400 and 'no-store' not in cache_control and 'no-cache' not in cache_control:
                response.headers['Cache-Control'] = 'private, max-age=3600, must-revalidate'
        elif response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'private, no-cache'
        return response

    # Initialize Services
    media_sources = build_media_sources(app.config['MEDIA_ROOT'], app.config.get('MEDIA_SOURCES'))
    library_service = LibraryService(app.config['MEDIA_ROOT'], media_sources=media_sources)
    default_media_root = library_service.media_root
    # `tiklocal D:\media\2026\hits` used to start happily on a missing nested
    # directory and serve an empty library with no explanation. The path came from
    # the user, so create it (parents included) and only warn when that fails.
    if not default_media_root.is_dir():
        try:
            default_media_root.mkdir(parents=True, exist_ok=True)
            app.logger.info('Created media directory: %s', default_media_root)
        except OSError as error:
            app.logger.warning('Media directory is not usable: %s (%s)', default_media_root, error)
    media_root_str = str(default_media_root)
    app.config['MEDIA_ROOT'] = default_media_root
    favorite_service = FavoriteService(get_favorites_path(), library_service)
    thumbnail_service = ThumbnailService(Path(media_root_str), library_service=library_service)
    metadata_store = ImageMetadataStore(get_metadata_path())
    caption_settings = CaptionSettings(app.config.get('VISION_CONFIG'), get_prompt_config_path(), get_llm_config_path())
    register_caption_routes(app, library_service, metadata_store, caption_settings)
    app_database = app.config.get('APP_DATABASE')
    if app_database is None:
        app_database = _open_database(get_database_path())
    else:
        app_database.migrate()
    media_index = MediaIndexStore(app_database)
    library_indexer = LibraryIndexer(library_service, media_index)
    index_sync_result = library_indexer.sync()
    app.extensions["media_index_sync"] = index_sync_result
    media_probe = MediaProbeWorker(library_service, media_index)
    library_indexer.on_change = media_probe.wake
    app.extensions["media_probe"] = media_probe
    if not app.config.get('TESTING'):
        media_probe.start()
    if index_sync_result["unavailable_sources"]:
        app.logger.warning(
            "Media source unavailable; preserving its existing index: %s",
            ", ".join(index_sync_result["unavailable_sources"]),
        )
    activity_store = MediaActivityStore(app_database)
    recommend_service = RecommendService(
        library_service,
        favorite_service,
        activity_store,
        media_index=media_index,
    )
    radio_profile_store = RadioProfileStore(get_radio_profile_path())
    radio_service = RadioService(
        library_service,
        favorite_service,
        media_index,
        radio_profile_store,
        activity_store=activity_store,
    )
    register_radio_client_routes(
        app,
        app_version=app_version,
        instance_name=instance_name,
        auth_store=auth_store,
        device_auth_store=device_auth_store,
        pairing_grant_store=pairing_grant_store,
        library_service=library_service,
        favorite_service=favorite_service,
        radio_service=radio_service,
        thumbnail_service=thumbnail_service,
    )
    similarity = None
    if similarity_active:
        from tiklocal.experiments.similarity.web import register_similarity_routes
        similarity = register_similarity_routes(app, library_service, thumbnail_service, app_database)
    download_config_store = DownloadConfigStore(get_download_config_path())
    download_history_store = DownloadHistoryStore(get_download_jobs_path())
    download_source_store = DownloadSourceStore(get_download_sources_path())
    collection_store = CollectionStore(get_collections_path())
    download_source_id = str(app.config.get('DOWNLOAD_SOURCE') or library_service.default_source_id).strip() or library_service.default_source_id
    download_source = library_service.sources_by_id.get(download_source_id) or library_service.sources_by_id[library_service.default_source_id]
    tiktok_overrides = app.config.get('TIKTOK_DOWNLOADER')
    if isinstance(tiktok_overrides, dict):
        # Seed before the manager construction so its cached config includes the section.
        seed_tiktok_downloader_config(download_config_store, tiktok_overrides)
    download_manager = DownloadManager(
        download_source.path,
        download_config_store,
        download_history_store,
        source_store=download_source_store,
        output_source_id=download_source.id,
        on_outputs=library_indexer.register_uris,
    )

    app.extensions['download_manager'] = download_manager
    tiktok_service = TikTokDownloadService(download_config_store, download_manager)
    trash_service = TrashService(library_service)
    register_download_routes(
        app, download_manager, library_service, tiktok_service=tiktok_service,
        trash_service=trash_service, media_index=media_index,
    )
    register_library_routes(
        app, library_service=library_service, media_index=media_index,
        library_indexer=library_indexer, favorite_service=favorite_service,
        collection_store=collection_store, activity_store=activity_store,
        media_search_available=similarity.has_vectors if similarity else None,
    )

    register_flow_routes(
        app, media_index=media_index, recommend_service=recommend_service,
        download_source_store=download_source_store,
        activity_store=activity_store,
        related_photo_groups=similarity.photo_groups if similarity else None,
        library_service=library_service,
    )
    register_author_routes(
        app, library_service=library_service, media_index=media_index,
        tiktok_service=tiktok_service,
    )

    register_radio_routes(app, library_service, radio_service, thumbnail_service, RadioEnergyService(library_service))
    register_media_routes(
        app, library_service, media_index, thumbnail_service, download_manager,
        trash_service, library_indexer, activity_store,
        favorite_service=favorite_service, collection_store=collection_store,
    )
    register_settings_routes(app, media_index, favorite_service, thumbnail_service, app_version)

    # --- Template Filters ---
    @app.template_filter('filesizeformat')
    def filesizeformat(num_bytes):
        if num_bytes is None: return '0 B'
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if num_bytes < 1024.0:
                return f"{int(num_bytes) if unit == 'B' else f'{num_bytes:.1f}'} {unit}"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} PB"

    @app.get('/favicon.ico')
    def favicon():
        return send_file(
            Path(app.static_folder) / 'brand' / 'favicon.ico',
            mimetype='image/vnd.microsoft.icon',
            max_age=86400,
        )

    @app.get('/service-worker.js')
    def retired_service_worker():
        return send_file(
            Path(app.static_folder) / 'service_worker.js',
            mimetype='application/javascript',
        )

    # --- Framework errors for JSON clients ---
    # Werkzeug's default 404/405/500 pages are HTML; every /api/ consumer calls
    # response.json() and crashed with "Unexpected token '<'" instead of showing the
    # real problem (e.g. a sync over an unreadable folder).
    def _api_error(status, message, error):
        if request.path.startswith('/api/'):
            return {'success': False, 'error': message}, status
        # Werkzeug's page is English-only and unstyled; render the app shell instead so
        # a stale bookmark or a typo does not drop the user on <html lang="en">.
        return render_template('error.html', status=status, message=message), status

    @app.errorhandler(404)
    def handle_not_found(error):
        return _api_error(404, t('common.error.not_found'), error)

    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        # _api_error builds a fresh response, which drops Werkzeug's Allow header; a
        # 405 without Allow leaves clients guessing which verb the route accepts.
        message = t('common.error.method_not_allowed')
        if request.path.startswith('/api/'):
            response = jsonify({'success': False, 'error': message})
        else:
            response = app.make_response(render_template('error.html', status=405, message=message))
        response.status_code = 405
        methods = getattr(error, 'valid_methods', None)
        if methods:
            response.headers['Allow'] = ', '.join(methods)
        return response

    @app.errorhandler(413)
    def handle_request_too_large(error):
        # Werkzeug answers an oversized body with an English HTML page; the upload
        # fetch then failed on response.json() and the UI only said "upload failed".
        limit = app.config.get('MAX_CONTENT_LENGTH') or 0
        message = t('common.error.too_large', limit=f'{limit / (1024 * 1024):.0f} MB')
        return _api_error(413, message, error)

    @app.errorhandler(500)
    def handle_server_error(error):
        # A bare path could not tell GET from POST or a retry from the original
        # request, so the entry was impossible to reproduce from the log alone.
        app.logger.exception(
            'Unhandled error on %s %s from %s',
            request.method,
            request.full_path if request.query_string else request.path,
            request.remote_addr,
        )
        return _api_error(500, t('common.error.internal'), error)

    return app
