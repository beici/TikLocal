import os
import sys
import signal
import argparse
import getpass
from pathlib import Path
from waitress.server import create_server as create_waitress_server
from tiklocal.app import create_app
from tiklocal import __version__
from tiklocal.thumbs import generate_thumbnails
from tiklocal.paths import get_data_dir
from tiklocal.paths import get_auth_path
from tiklocal.services.auth import AuthStore
from tiklocal.services.instance_lock import InstanceLockError, acquire_instance_lock
from tiklocal.services.library import normalize_source_id
from tiklocal.config import normalize_media_sources, similarity_enabled

try:
    import yaml
except ImportError:
    yaml = None


def load_config():
    """Load configuration from a file."""
    config = {}

    # Try the supported configuration paths; an isolated instance reads its own first.
    config_paths = [
        Path.home() / '.config' / 'tiklocal' / 'config.yaml',
        Path.home() / '.tiklocal' / 'config.yaml',
    ]
    instance = os.environ.get('TIKLOCAL_INSTANCE')
    if instance:
        config_paths.insert(0, Path(instance).expanduser() / 'config.yaml')

    for config_path in config_paths:
        if config_path.exists():
            if yaml is None:
                print(f"Warning: Found {config_path}, but PyYAML is not installed; skipping the configuration file", file=sys.stderr)
                break
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
                break
            except Exception as e:
                print(f"Warning: Failed to read configuration file {config_path}: {e}", file=sys.stderr)

    if not isinstance(config, dict):
        # A list or a bare scalar used to surface as `AttributeError: 'list'
        # object has no attribute 'get'` pointing at the host line.
        print(f"Error: {config_path} must contain a mapping of configuration keys", file=sys.stderr)
        sys.exit(2)
    return config


def resolve_data_dir(parser):
    """Create and return the data directory, reporting bad paths as usage errors."""
    try:
        return get_data_dir()
    except OSError as exc:
        # TIKLOCAL_INSTANCE pointing at a file or an unwritable location used to
        # surface as a bare traceback from paths.py after the banner was printed.
        parser.error(f"Cannot create the data directory: {exc}")


def validate_config_types(config):
    """Reject configuration values of the wrong type instead of failing later.

    `media_root: 123` used to raise "TypeError: expected str, bytes or os.PathLike
    object" from deep inside Path(), and `port: true` silently became port 1 because
    int(True) == 1 passes the range check.
    """
    if not isinstance(config, dict):
        return
    expectations = {
        'media_root': (str, 'a path'),
        'host': (str, 'a host name'),
        'name': (str, 'a name'),
        'download_source': (str, 'a source id'),
    }
    for key, (expected, description) in expectations.items():
        value = config.get(key)
        if value is not None and not isinstance(value, expected):
            print(
                f"Error: config.yaml option '{key}' must be {description}, got {type(value).__name__}",
                file=sys.stderr,
            )
            sys.exit(2)
    port = config.get('port')
    if port is not None and (not isinstance(port, int) or isinstance(port, bool)):
        print(
            f"Error: config.yaml option 'port' must be an integer, got {type(port).__name__}",
            file=sys.stderr,
        )
        sys.exit(2)


def parse_cli_media_source(value):
    text = str(value or '').strip()
    if '=' not in text:
        raise argparse.ArgumentTypeError('format must be id=/path/to/media')
    source_id, path = text.split('=', 1)
    source_id = normalize_source_id(source_id)
    path = path.strip()
    if not path:
        raise argparse.ArgumentTypeError('media directory cannot be empty')
    return {'id': source_id, 'name': source_id, 'path': path}


def main():
    # When stdout is redirected, Python encodes with the locale codec (PEP 528 only
    # covers a real console). Never let an unencodable character abort the start.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(errors='replace')
            except (ValueError, OSError):
                pass
    # Read the configuration file.
    config = load_config()
    validate_config_types(config)

    # Normalize argv to support the following forms.
    # 1) tiklocal                      -> serve
    # 2) tiklocal /path                -> serve /path
    # 3) tiklocal --port 9000          -> serve --port 9000
    # 4) tiklocal thumbs /path         -> thumbs /path
    # 5) tiklocal /path thumbs         -> thumbs /path
    argv = sys.argv[1:]
    if '-h' not in argv and '--help' not in argv:
        if 'thumbs' in argv:
            idx = argv.index('thumbs')
            if idx != 0:
                argv.pop(idx)
                argv.insert(0, 'thumbs')
        elif len(argv) == 0 or argv[0] not in ('serve', 'thumbs', 'dedupe', 'vectorize', 'auth'):
            # Default to serve when no known subcommand is present.
            argv.insert(0, 'serve')

    # Parse commands and options.
    parser = argparse.ArgumentParser(
        description='TikLocal - local media server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  tiklocal                                 # Start the server (default)
  tiklocal /path/to/media                  # Use a media directory
  tiklocal --port 9000                     # Use a custom port
  tiklocal serve /path --port 9000         # Explicitly use the serve command
  tiklocal thumbs /path --overwrite        # Generate thumbnails in bulk
  tiklocal dedupe /path --dry-run          # Preview duplicate files
  tiklocal dedupe /path --execute          # Delete duplicates and keep the oldest
  tiklocal vectorize /path --limit 200     # Prepare 200 photos/videos for similarity
  tiklocal auth set-password               # Set a new access password
        '''
    )

    subparsers = parser.add_subparsers(dest='command')

    # serve command
    serve_parser = subparsers.add_parser('serve', help='Start the server')
    serve_parser.add_argument('media_root', nargs='?', help='Media root directory')
    serve_parser.add_argument('--host', default=None, help='Server bind address (default: 0.0.0.0)')
    serve_parser.add_argument('--port', type=int, default=None, help='Server port (default: 8000)')
    serve_parser.add_argument('--dev', action='store_true', help='Enable development reload and debugging')
    serve_parser.add_argument('--name', default=None, help='Display name for this instance')
    serve_parser.add_argument('--media-source', action='append', type=parse_cli_media_source,
                              help='Add a repeatable media source as id=/path/to/media')
    serve_parser.add_argument('--download-source', default=None, help='Media source ID for downloads')

    auth_parser = subparsers.add_parser('auth', help='Manage access authentication')
    auth_parser.add_argument('action', choices=['set-password', 'status'], help='Authentication action')

    # thumbs command
    thumbs_parser = subparsers.add_parser('thumbs', help='Generate video thumbnails in bulk')
    thumbs_parser.add_argument('media_root', nargs='?', help='Media root (optional when configured elsewhere)')
    thumbs_parser.add_argument('--overwrite', action='store_true', help='Rebuild existing thumbnails')
    thumbs_parser.add_argument('--limit', type=int, default=0, help='Maximum items to process (0 means all)')

    # dedupe command
    dedupe_parser = subparsers.add_parser('dedupe', help='Find and remove duplicate files')
    dedupe_parser.add_argument('media_root', nargs='?', help='Media root directory')
    dedupe_parser.add_argument('--type', choices=['video', 'image', 'all'], default='all',
                              help='File type (default: all)')
    dedupe_parser.add_argument('--algorithm', choices=['md5', 'sha256'], default='sha256',
                              help='Hash algorithm (default: sha256)')
    dedupe_parser.add_argument('--keep', choices=['oldest', 'newest', 'shortest_path'], default='oldest',
                              help='Keep strategy: oldest, newest, or shortest_path (default: oldest)')
    dedupe_parser.add_argument('--dry-run', action='store_true', default=True,
                              help='Preview files that would be deleted (default)')
    dedupe_parser.add_argument('--execute', action='store_true',
                              help='Delete files instead of running a preview')
    dedupe_parser.add_argument('--auto-confirm', action='store_true',
                              help='Skip the deletion confirmation prompt')

    # vectorize command
    vectorize_parser = subparsers.add_parser('vectorize', help='Prepare photo and video vectors for similarity and search')
    vectorize_parser.add_argument('media_root', nargs='?', help='Media root (optional when configured elsewhere)')
    vectorize_parser.add_argument('--media-source', action='append', type=parse_cli_media_source,
                                  help='Add a repeatable media source as id=/path/to/media')
    vectorize_parser.add_argument('--limit', type=int, default=0, help='Maximum files to send (0 means all)')
    vectorize_parser.add_argument('--dry-run', action='store_true', help='Show the plan without calling the model')
    vectorize_parser.add_argument('--yes', action='store_true', help='Skip the confirmation prompt')

    args = parser.parse_args(argv)

    # Treat a missing subcommand as serve.
    cmd = args.command or 'serve'

    # Validate the data directory for every subcommand: `auth` calls get_auth_path()
    # directly, so TIKLOCAL_INSTANCE pointing at a file used to raise a raw
    # FileExistsError from paths.py while serve/thumbs printed a readable error.
    resolve_data_dir(parser)

    if cmd == 'thumbs':
        media_root = args.media_root or os.environ.get('MEDIA_ROOT') or config.get('media_root')
        if not media_root:
            parser.error('Specify a media directory:\n  - tiklocal thumbs /path/to/media\n  - or set MEDIA_ROOT=/path/to/media')
        media_path = Path(media_root).expanduser()
        if not media_path.exists() or not media_path.is_dir():
            print(f"Error: Media directory is unavailable: {media_root}", file=sys.stderr)
            sys.exit(1)
        print(f"Data directory: {resolve_data_dir(parser)}")
        stats = generate_thumbnails(media_path, overwrite=getattr(args, 'overwrite', False), limit=getattr(args, 'limit', 0), show_progress=True)
        # Exit after completion.
        return

    if cmd == 'dedupe':
        from tiklocal.dedupe import run_dedupe

        media_root = args.media_root or os.environ.get('MEDIA_ROOT') or config.get('media_root')
        if not media_root:
            parser.error('Specify a media directory:\n  - tiklocal dedupe /path/to/media\n  - or set MEDIA_ROOT=/path/to/media')

        media_path = Path(media_root).expanduser()
        if not media_path.exists() or not media_path.is_dir():
            print(f"Error: Media directory is unavailable: {media_root}", file=sys.stderr)
            sys.exit(1)

        # --execute disables dry-run.
        dry_run = not getattr(args, 'execute', False)

        stats = run_dedupe(
            media_root=media_path,
            file_type=getattr(args, 'type', 'all'),
            algorithm=getattr(args, 'algorithm', 'sha256'),
            keep_strategy=getattr(args, 'keep', 'oldest'),
            dry_run=dry_run,
            auto_confirm=getattr(args, 'auto_confirm', False)
        )
        return

    if cmd == 'vectorize':
        try:
            enabled = similarity_enabled(config.get('experiments'), config.get('embedding') or config.get('embedding_config'))
        except ValueError as exc:
            parser.error(str(exc))
        if not enabled:
            parser.error('The similar-image experiment is disabled; set experiments.similarity.enabled: true')
        from tiklocal.experiments.similarity.cli import run_vectorize
        run_vectorize(config, args, parser)
        return

    if cmd == 'auth':
        auth_store = AuthStore(get_auth_path())
        if args.action == 'status':
            try:
                bootstrap = auth_store.ensure(os.environ.get('TIKLOCAL_AUTH_PASSWORD'))
            except ValueError as exc:
                parser.error(str(exc))
            print("Authentication: enabled")
            print(f"Authentication file: {auth_store.path}")
            if bootstrap.generated_password:
                print(f"Initial access password: {bootstrap.generated_password}")
            return

        env_password = str(os.environ.get('TIKLOCAL_AUTH_PASSWORD') or '')
        if env_password:
            password = env_password
        else:
            password = getpass.getpass('New access password: ')
            confirmation = getpass.getpass('Enter the password again: ')
            if password != confirmation:
                parser.error('The passwords do not match')
        try:
            auth_store.set_password(password)
        except ValueError as exc:
            parser.error(str(exc))
        print('Access password updated. All signed-in devices must sign in again.')
        return

    # Serve path.
    media_root = args.media_root or os.environ.get('MEDIA_ROOT') or config.get('media_root')
    host = args.host or os.environ.get('TIKLOCAL_HOST') or config.get('host') or '0.0.0.0'
    if any(config.get(key) for key in ('https', 'tls_cert', 'tls_key', 'hostnames')):
        parser.error('Built-in HTTPS has been removed. Delete the old TLS settings and provide HTTPS through an external reverse proxy.')
    env_port_text = os.environ.get('TIKLOCAL_PORT')
    try:
        env_port = int(env_port_text) if env_port_text not in (None, '') else None
    except ValueError:
        parser.error('TIKLOCAL_PORT must be an integer')
    configured_port = env_port if env_port is not None else config.get('port')
    raw_port = args.port if args.port is not None else configured_port
    if raw_port is None:
        port = 8000
    else:
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            parser.error('The server port must be an integer')
        if not 1 <= port <= 65535:
            # Waitress performs no range check: 70000 printed the URL first and
            # then died inside bind() with a bare traceback.
            parser.error('The server port must be between 1 and 65535')
    media_sources = normalize_media_sources(config, getattr(args, 'media_source', None), media_root=media_root)
    download_source = args.download_source or config.get('download_source') or 'default'
    vision_config = config.get('vision') or config.get('vision_config') or None
    embedding_config = config.get('embedding') or config.get('embedding_config') or None

    # Validate media directories.
    if not media_root and not media_sources:
        parser.error('Specify a media directory:\n  - command line: tiklocal /path/to/media\n  - environment: MEDIA_ROOT=/path/to/media\n  - configuration: ~/.config/tiklocal/config.yaml')

    if media_root:
        media_path = Path(media_root).expanduser()
        if not media_path.exists():
            print(f"Error: Media directory does not exist: {media_root}", file=sys.stderr)
            sys.exit(1)

        if not media_path.is_dir():
            print(f"Error: Path is not a directory: {media_root}", file=sys.stderr)
            sys.exit(1)
    else:
        media_path = Path(media_sources[0]['path'])

    unavailable_sources = []
    for source in media_sources:
        source_path = Path(str(source.get('path') or '')).expanduser()
        if not source_path.exists() or not source_path.is_dir():
            unavailable_sources.append((source.get('id'), source_path))

    if media_sources and len(unavailable_sources) == len(media_sources):
        print("Error: No media sources are available", file=sys.stderr)
        for source_id, source_path in unavailable_sources:
            print(f"  @{source_id}: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not media_root and unavailable_sources:
        unavailable_ids = {source_id for source_id, _ in unavailable_sources}
        first_available = next(
            source for source in media_sources if source.get('id') not in unavailable_ids
        )
        media_path = Path(str(first_available['path'])).expanduser()
    for source_id, source_path in unavailable_sources:
        print(f"Warning: Media source unavailable; preserving its existing index @{source_id}: {source_path}", file=sys.stderr)

    # Set environment variables for Flask.
    os.environ['MEDIA_ROOT'] = str(media_path.absolute())

    # Start the server.
    print("Starting TikLocal server...")
    if media_sources:
        print("Media sources:")
        seen_source_ids = set()
        for source in media_sources:
            source_id = normalize_source_id(source.get('id'))
            if source_id in seen_source_ids:
                # build_media_sources keeps only the first entry per id; listing the
                # dropped one here made the banner disagree with what is indexed.
                print(f"  @{source_id}: ignored (duplicate id)")
                continue
            seen_source_ids.add(source_id)
            marker = " (downloads)" if normalize_source_id(download_source) == source_id else ""
            print(f"  @{source_id}: {Path(str(source.get('path'))).expanduser().absolute()}{marker}")
    else:
        print(f"Media directory: {media_path.absolute()}")
    print(f"Data directory: {resolve_data_dir(parser)}")
    display_host = f'[{host}]' if ':' in host and not host.startswith('[') else host

    def print_banner():
        print(f"Open: http://{display_host}:{port}")
        print("Access authentication: enabled")
        if bootstrap and bootstrap.generated_password:
            # Plain ASCII: a redirected stdout uses the locale encoding (cp1252 has no
            # box-drawing characters), and an UnicodeEncodeError here would abort the
            # first start right after the password was generated but before it printed.
            print("+--------------------------------------------+")
            print("| Initial access password                    |")
            print(f"| {bootstrap.generated_password:<42} |")
            print("| Save it; change it with tiklocal auth set-password |")
            print("+--------------------------------------------+")

    # Two servers on one instance directory corrupt each other's download history
    # (the second marks the first one's running jobs as interrupted, and every
    # jobs.json write is a whole-list overwrite). Take the lock before building
    # the app; the OS releases it when this process exits, crash included.
    try:
        instance_lock = acquire_instance_lock(Path(resolve_data_dir(parser)))
    except InstanceLockError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        app = create_app({
            "MEDIA_ROOT": media_path,
            "MEDIA_SOURCES": media_sources or None,
            "DOWNLOAD_SOURCE": normalize_source_id(download_source),
            "VISION_CONFIG": vision_config,
            "EMBEDDING_CONFIG": embedding_config,
            "EXPERIMENTS": config.get('experiments'),
            "INSTANCE_NAME": args.name or os.environ.get('TIKLOCAL_NAME') or config.get('name'),
            "TIKTOK_DOWNLOADER": config.get('tiktok_downloader'),
        })
    except ValueError as exc:
        parser.error(str(exc))
    # Keep the lock handle alive for the whole process lifetime.
    app.extensions['instance_lock'] = instance_lock
    bootstrap = app.extensions.get('auth_bootstrap')
    download_manager = app.extensions['download_manager']
    # The banner only reaches the terminal, and a service manager restarts the
    # process: without this line the log never says which media root, port or
    # version a given run actually used.
    app.logger.info(
        'TikLocal %s on %s:%s; media_root=%s sources=%s data_dir=%s auth=%s',
        __version__,
        host,
        port,
        media_path.absolute(),
        ','.join(sorted(str(source.get('id')) for source in media_sources)) or '-',
        resolve_data_dir(parser),
        'enabled' if app.extensions.get('auth_enabled') else 'disabled',
    )
    previous_sigterm = signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        if not args.dev or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            download_manager.start()
        if getattr(args, 'dev', False):
            # Development mode uses Flask's built-in server.
            print_banner()
            print("⚠️  Development mode is enabled; do not use it in production")
            app.run(host=host, port=port, debug=True, use_reloader=True)
        else:
            # Production mode uses Waitress. Bind before printing the banner: a port
            # clash used to print the URL and the freshly generated password first and
            # then die inside waitress with a bare "PermissionError: WinError 10013",
            # which reads like a firewall problem instead of "the port is taken".
            try:
                # Cold thumbnails run ffmpeg, so allow a few more workers than the default 4.
                server = create_waitress_server(app, host=host, port=port, threads=8)
            except OSError as exc:
                print(f"Error: cannot listen on {display_host}:{port} ({exc})", file=sys.stderr)
                print("Another process may already use this port; retry with --port <free port>.", file=sys.stderr)
                sys.exit(1)
            print_banner()
            server.run()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        try:
            download_manager.close()
        except RuntimeError as exc:
            # Ctrl+C while a download worker is still winding down must not
            # replace the clean shutdown with a traceback.
            print(f"Warning: {exc}", file=sys.stderr)


if __name__ == '__main__':
    main()
