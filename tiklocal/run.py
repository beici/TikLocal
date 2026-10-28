import os
import sys
import signal
import socket
import argparse
import getpass
from pathlib import Path
from waitress.server import create_server as create_waitress_server
from tiklocal.app import create_app
from tiklocal import __version__
from tiklocal.thumbs import generate_thumbnails
from tiklocal.paths import get_data_dir
from tiklocal.paths import get_auth_path
from tiklocal.paths import get_media_sources_path
from tiklocal.services.auth import AuthStore
from tiklocal.services.instance_lock import InstanceLockError, acquire_instance_lock
from tiklocal.services.library import build_media_sources, normalize_source_id
from tiklocal.services.media_sources_store import MediaSourceStore, resolve_startup_sources
from tiklocal.services.tls import (
    DEFAULT_DAYS as TLS_DEFAULT_DAYS,
    DEFAULT_TLS_PORT,
    TlsError,
    default_certificate_paths,
    describe_material,
    ensure_self_signed_certificate,
    inspect_material,
    load_custom_certificate,
    split_host_values,
)
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


def fold_stored_media_sources(media_sources):
    """Add the folders saved from Settings to the launch-time source list.

    The instance keeps the user's folder list in ``media_sources.json``; only the
    app used to read it, so the banner (and the ``sources=`` log line) named just
    the launch directory while the server already served the saved folders. Folding
    the file here makes both agree, and lets a bare ``tiklocal`` start on the folders
    the user added earlier instead of demanding a directory argument.
    """
    try:
        stored = MediaSourceStore(get_media_sources_path()).load()
    except Exception as exc:  # a corrupt file must not block the server
        print(f"Warning: ignoring the saved media folders: {exc}", file=sys.stderr)
        return media_sources
    if stored is None:
        return media_sources
    merged = resolve_startup_sources(stored, build_media_sources(None, media_sources))
    return [
        {'id': source.id, 'name': source.name, 'path': str(source.path)}
        for source in merged
    ]


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
    # Built-in HTTPS is configurable from the file too; a wrong type used to
    # surface as `https: yes` quietly starting plain HTTP because `any(...)` only
    # checks truthiness, and `tls_cert: 123` dying inside Path().
    https = config.get('https')
    if https is not None and not isinstance(https, bool):
        print(
            f"Error: config.yaml option 'https' must be true or false, got {type(https).__name__}",
            file=sys.stderr,
        )
        sys.exit(2)
    for key in ('tls_cert', 'tls_key'):
        value = config.get(key)
        if value is not None and not isinstance(value, str):
            print(
                f"Error: config.yaml option '{key}' must be a path, got {type(value).__name__}",
                file=sys.stderr,
            )
            sys.exit(2)
    hostnames = config.get('hostnames')
    if hostnames is not None and not isinstance(hostnames, (str, list)):
        print(
            "Error: config.yaml option 'hostnames' must be a list of names, "
            f"got {type(hostnames).__name__}",
            file=sys.stderr,
        )
        sys.exit(2)


def config_tls_hosts(config):
    """Extra certificate names from the ``hostnames`` config key, old versions' name."""
    value = config.get('hostnames')
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]


def tls_failure(message):
    """Print one actionable line for a TLS problem and exit without a traceback."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def probe_listen(host, port, display_host):
    """Fail with one actionable line when nothing can bind ``host:port``.

    Mirrors Werkzeug's own address resolution (AF_INET6 only for a literal IPv6
    host, first getaddrinfo result otherwise) so the probe binds exactly what the
    real server will bind. The socket is closed immediately; Werkzeug binds for
    real a moment later.
    """
    family = socket.AF_INET6 if ':' in host and hasattr(socket, 'AF_INET6') else socket.AF_INET
    try:
        address = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM, socket.IPPROTO_TCP)[0][4]
    except (socket.gaierror, UnicodeError):
        address = (host, port)
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind(address)
    except OSError as exc:
        print(f"Error: cannot listen on {display_host}:{port} ({exc})", file=sys.stderr)
        print("Another process may already use this port; retry with --port <free port>.", file=sys.stderr)
        sys.exit(1)
    finally:
        probe.close()


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
        elif len(argv) == 0 or argv[0] not in ('serve', 'thumbs', 'dedupe', 'vectorize', 'auth', 'tls'):
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
  tiklocal --tls                           # Serve HTTPS with a local certificate
  tiklocal serve /path --port 9000         # Explicitly use the serve command
  tiklocal thumbs /path --overwrite        # Generate thumbnails in bulk
  tiklocal dedupe /path --dry-run          # Preview duplicate files
  tiklocal dedupe /path --execute          # Delete duplicates and keep the oldest
  tiklocal vectorize /path --limit 200     # Prepare 200 photos/videos for similarity
  tiklocal auth set-password               # Set a new access password
  tiklocal tls status                      # Show the HTTPS certificate in use
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
    serve_parser.add_argument('--tls', action='store_true',
                              help='Serve HTTPS with a local certificate (default port: 8443)')
    serve_parser.add_argument('--tls-cert', default=None, metavar='PATH',
                              help='PEM certificate for HTTPS (used together with --tls-key)')
    serve_parser.add_argument('--tls-key', default=None, metavar='PATH',
                              help='PEM private key for HTTPS (used together with --tls-cert)')
    serve_parser.add_argument('--tls-hosts', action='append', default=None, metavar='NAMES',
                              help='Extra DNS names or IPs for a generated certificate '
                                   '(repeatable, comma separated)')
    serve_parser.add_argument('--tls-days', type=int, default=None, metavar='DAYS',
                              help=f'Lifetime of a generated certificate (default: {TLS_DEFAULT_DAYS})')

    tls_parser = subparsers.add_parser('tls', help='Manage the built-in HTTPS certificate')
    tls_parser.add_argument('action', nargs='?', default='status', choices=['status', 'generate'],
                            help='status (default) shows the certificate; generate creates one')
    tls_parser.add_argument('--hosts', action='append', default=None, metavar='NAMES',
                            help='Extra DNS names or IPs for a generated certificate '
                                 '(repeatable, comma separated)')
    tls_parser.add_argument('--days', type=int, default=None, metavar='DAYS',
                            help=f'Lifetime of a generated certificate (default: {TLS_DEFAULT_DAYS})')
    tls_parser.add_argument('--force', action='store_true',
                            help='Regenerate even when the current certificate is still usable')
    tls_parser.add_argument('--cert', default=None, metavar='PATH',
                            help='Inspect this certificate instead of the generated one')
    tls_parser.add_argument('--key', default=None, metavar='PATH',
                            help='Private key that belongs to --cert')

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
            if bootstrap.default_password:
                print(f"Access password: {bootstrap.default_password} (default; change it with tiklocal auth set-password)")
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

    if cmd == 'tls':
        try:
            if args.cert or args.key:
                if not (args.cert and args.key):
                    tls_failure('--cert and --key must be used together')
                print('\n'.join(describe_material(load_custom_certificate(args.cert, args.key))))
                return
            if args.action == 'generate':
                extra_hosts, extra_ips = split_host_values(args.hosts or [])
                material = ensure_self_signed_certificate(
                    extra_hosts=extra_hosts,
                    extra_ips=extra_ips,
                    days=args.days or TLS_DEFAULT_DAYS,
                    force=args.force,
                )
                print('\n'.join(describe_material(material)))
                print()
                print('Serve it with: tiklocal --tls')
                return
            material = inspect_material()
        except TlsError as exc:
            tls_failure(str(exc))
        if material is None:
            cert_path, key_path = default_certificate_paths()
            print('HTTPS certificate: none yet')
            print(f'Will be generated on the first `tiklocal --tls` start: {cert_path}')
            print(f'Private key: {key_path}')
            return
        print('\n'.join(describe_material(material)))
        return

    # Serve path.
    media_root = args.media_root or os.environ.get('MEDIA_ROOT') or config.get('media_root')
    host = args.host or os.environ.get('TIKLOCAL_HOST') or config.get('host') or '0.0.0.0'
    # HTTPS: explicit flag first, then the config keys old versions wrote
    # (`https`, `tls_cert`/`tls_key`). New versions removed these keys, and the
    # guard that rejected them is replaced by actually honoring them again.
    custom_cert = args.tls_cert or config.get('tls_cert') or None
    custom_key = args.tls_key or config.get('tls_key') or None
    if bool(custom_cert) != bool(custom_key):
        tls_failure('--tls-cert and --tls-key must be used together')
    tls_enabled = bool(args.tls or config.get('https') or custom_cert)
    if (args.tls_hosts or args.tls_days is not None) and not tls_enabled:
        tls_failure('--tls-hosts and --tls-days only apply to --tls')
    legacy_hosts = config_tls_hosts(config)
    env_port_text = os.environ.get('TIKLOCAL_PORT')
    try:
        env_port = int(env_port_text) if env_port_text not in (None, '') else None
    except ValueError:
        parser.error('TIKLOCAL_PORT must be an integer')
    configured_port = env_port if env_port is not None else config.get('port')
    raw_port = args.port if args.port is not None else configured_port
    if raw_port is None:
        # HTTPS keeps its own default so a plain-HTTP deployment on 8000 and an
        # HTTPS one can run side by side on one machine.
        port = DEFAULT_TLS_PORT if tls_enabled else 8000
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
    media_sources = fold_stored_media_sources(media_sources)
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

    # The banner names the actual server: HTTPS is served by Werkzeug because
    # Waitress cannot terminate TLS, and a banner that always said the same thing
    # would hide which one is running.
    scheme = 'https' if tls_enabled else 'http'
    if getattr(args, 'dev', False):
        server_label = 'Werkzeug development server'
    elif tls_enabled:
        server_label = 'Werkzeug threaded server (TLS)'
    else:
        server_label = 'Waitress'
    tls_material = None

    def print_banner():
        print(f"Open: {scheme}://{display_host}:{port}")
        print(f"Server: {server_label} ({scheme.upper()})")
        if tls_material is not None:
            kind = 'user-provided' if tls_material.custom else 'self-signed'
            print(f"HTTPS certificate: {tls_material.cert_path} ({kind})")
            if not tls_material.custom:
                print("Trust: your browser warns once about the self-signed certificate; "
                      "install it on the device to stop that (see `tiklocal tls status`).")
        print("Access authentication: enabled")
        if bootstrap and bootstrap.default_password:
            # Plain ASCII: a redirected stdout uses the locale encoding (cp1252 has no
            # box-drawing characters), and an UnicodeEncodeError here would abort the
            # first start right after the password was chosen but before it printed.
            print("+--------------------------------------------+")
            print("| Access password (default)                  |")
            print(f"| {bootstrap.default_password:<42} |")
            print("| Change it: tiklocal auth set-password      |")
            print("+--------------------------------------------+")
        # A log file behind a pipe is block-buffered, and the banner is the only
        # place that says which URL this run serves; flush it before blocking in
        # the server loop instead of showing it on exit.
        sys.stdout.flush()

    # Two servers on one instance directory corrupt each other's download history
    # (the second marks the first one's running jobs as interrupted, and every
    # jobs.json write is a whole-list overwrite). Take the lock before building
    # the app; the OS releases it when this process exits, crash included.
    try:
        instance_lock = acquire_instance_lock(Path(resolve_data_dir(parser)))
    except InstanceLockError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Resolve the certificate before the app is built: the session cookie must be
    # marked Secure from the very first response, and generating material after the
    # banner used to show an https:// URL for a server that then never started.
    if tls_enabled:
        # Werkzeug prints its own sys.exit(1) on a bind clash and would do it only
        # after a certificate had been generated; probe first so a busy port is one
        # actionable line and leaves no new files behind.
        probe_listen(host, port, display_host)
        try:
            if custom_cert:
                if args.tls_hosts or args.tls_days is not None:
                    print("Warning: --tls-hosts/--tls-days only apply to a generated "
                          "certificate; the provided pair is used as-is.", file=sys.stderr)
                tls_material = load_custom_certificate(custom_cert, custom_key)
            else:
                try:
                    extra_hosts, extra_ips = split_host_values([*(args.tls_hosts or []), *legacy_hosts])
                except TlsError as exc:
                    parser.error(str(exc))
                tls_material = ensure_self_signed_certificate(
                    extra_hosts=extra_hosts,
                    extra_ips=extra_ips,
                    days=args.tls_days or TLS_DEFAULT_DAYS,
                )
        except TlsError as exc:
            tls_failure(str(exc))
        if tls_material.created:
            print(f"Generated an HTTPS certificate for this instance: {tls_material.cert_path}")

    app_config = {
        "MEDIA_ROOT": media_path,
        "MEDIA_SOURCES": media_sources or None,
        "DOWNLOAD_SOURCE": normalize_source_id(download_source),
        "VISION_CONFIG": vision_config,
        "EMBEDDING_CONFIG": embedding_config,
        "EXPERIMENTS": config.get('experiments'),
        "INSTANCE_NAME": args.name or os.environ.get('TIKLOCAL_NAME') or config.get('name'),
        "TIKTOK_DOWNLOADER": config.get('tiktok_downloader'),
    }
    if tls_material is not None:
        # Over HTTPS the session cookie is always Secure. Passing the key only in
        # this branch keeps FLASK_AUTH_COOKIE_SECURE working for plain HTTP.
        app_config['AUTH_COOKIE_SECURE'] = True

    try:
        app = create_app(app_config)
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
        'TikLocal %s on %s://%s:%s; server=%s media_root=%s sources=%s data_dir=%s auth=%s',
        __version__,
        scheme,
        host,
        port,
        server_label,
        media_path.absolute(),
        ','.join(sorted(str(source.get('id')) for source in media_sources)) or '-',
        resolve_data_dir(parser),
        'enabled' if app.extensions.get('auth_enabled') else 'disabled',
    )
    previous_sigterm = signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        if not args.dev or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            download_manager.start()
        ssl_context = None
        if tls_material is not None:
            ssl_context = (str(tls_material.cert_path), str(tls_material.key_path))
        if getattr(args, 'dev', False):
            # Development mode uses Flask's built-in server.
            print_banner()
            print("⚠️  Development mode is enabled; do not use it in production")
            app.run(host=host, port=port, debug=True, use_reloader=True, ssl_context=ssl_context)
        elif tls_material is not None:
            # Waitress cannot terminate TLS, so HTTPS runs on Werkzeug's threaded
            # server. The port was probed before the certificate was resolved; the
            # try/except is the belt for the race between probe and bind.
            from werkzeug.serving import make_server

            try:
                server = make_server(
                    host,
                    port,
                    app,
                    threaded=True,
                    ssl_context=ssl_context,
                )
            except OSError as exc:
                print(f"Error: cannot listen on {display_host}:{port} ({exc})", file=sys.stderr)
                print("Another process may already use this port; retry with --port <free port>.", file=sys.stderr)
                sys.exit(1)
            print_banner()
            server.serve_forever()
        else:
            # Production mode uses Waitress. Bind before printing the banner: a port
            # clash used to print the URL and the freshly chosen password first and
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
