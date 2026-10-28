import logging
import socket
import ssl
import sys
import time

import pytest

from tiklocal.services import tls
from tiklocal.services.tls import (
    TlsError,
    TlsMaterial,
    describe_material,
    discover_names,
    split_host_values,
    validate_certificate_pair,
)


requires_openssl = pytest.mark.skipif(
    tls.find_openssl() is None, reason='OpenSSL is not installed on this machine'
)


@pytest.fixture(autouse=True)
def reset_cli_logging(capsys):
    """Re-bind the process-wide log handlers for every CLI test.

    ``configure_logging`` attaches its console handler once per process and it
    captures ``sys.stderr`` at that moment. pytest swaps stderr per test, so the
    second CLI test in this module would otherwise write every log record to a
    closed stream ("--- Logging error ---" tracebacks in the captured output).
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    if hasattr(root, '_tiklocal_logging_configured'):
        delattr(root, '_tiklocal_logging_configured')
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    if hasattr(root, '_tiklocal_logging_configured'):
        delattr(root, '_tiklocal_logging_configured')


def _material(directory, *, created=True, custom=False):
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / 'server.crt'
    key = directory / 'server.key'
    cert.write_text('placeholder certificate\n', encoding='ascii')
    key.write_text('placeholder key\n', encoding='ascii')
    return TlsMaterial(
        cert_path=cert,
        key_path=key,
        hosts=('localhost', 'studio.local'),
        ips=('127.0.0.1',),
        not_after=time.time() + 86400,
        sha256='00' * 32,
        created=created,
        custom=custom,
        directory=directory,
    )


# --- module: certificate generation -----------------------------------------


@requires_openssl
def test_generated_certificate_is_reused_and_covers_this_machine(tmp_path):
    first = tls.ensure_self_signed_certificate(directory=tmp_path)
    assert first.created is True
    assert first.cert_path == tmp_path / tls.CERT_FILENAME
    assert first.key_path == tmp_path / tls.KEY_FILENAME
    assert first.cert_path.is_file() and first.key_path.is_file()

    second = tls.ensure_self_signed_certificate(directory=tmp_path)
    assert second.created is False, 'a valid pair must be reused, not regenerated'
    assert second.sha256 == first.sha256

    info = tls.read_certificate_info(first.cert_path)
    assert info.inspected is True
    assert info.subject == 'CN=TikLocal local'
    assert 'localhost' in info.hosts
    assert '127.0.0.1' in info.ips
    assert info.not_after_epoch and info.not_after_epoch > time.time()


@requires_openssl
def test_generated_certificate_honours_extra_names(tmp_path):
    material = tls.ensure_self_signed_certificate(
        directory=tmp_path,
        extra_hosts=['media.lan', 'studio'],
        extra_ips=['10.1.2.3'],
    )
    info = tls.read_certificate_info(material.cert_path)
    assert {'media.lan', 'studio', 'studio.local'} <= set(info.hosts)
    assert '10.1.2.3' in info.ips


@requires_openssl
def test_certificate_is_regenerated_when_a_name_is_missing(tmp_path):
    tls.ensure_self_signed_certificate(directory=tmp_path)
    regenerated = tls.ensure_self_signed_certificate(
        directory=tmp_path, extra_hosts=['new-name.lan']
    )
    assert regenerated.created is True
    assert 'new-name.lan' in tls.read_certificate_info(regenerated.cert_path).hosts


@requires_openssl
def test_expiring_certificate_is_regenerated(tmp_path, monkeypatch):
    tls.ensure_self_signed_certificate(directory=tmp_path)
    monkeypatch.setattr(tls, 'RENEW_BEFORE_DAYS', 4000)
    renewed = tls.ensure_self_signed_certificate(directory=tmp_path)
    assert renewed.created is True


def test_missing_openssl_is_a_one_line_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, 'find_openssl', lambda: None)
    with pytest.raises(TlsError) as caught:
        tls.ensure_self_signed_certificate(directory=tmp_path)
    message = str(caught.value)
    assert 'needs OpenSSL' in message
    assert '--tls-cert/--tls-key' in message
    assert not list(tmp_path.glob('*.crt'))


def test_bundle_owned_directory_permissions_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, 'find_openssl', lambda: '/nonexistent/openssl')
    with pytest.raises(TlsError) as caught:
        tls.ensure_self_signed_certificate(directory=tmp_path)
    assert 'Cannot run OpenSSL' in str(caught.value)


# --- module: validation and reporting ---------------------------------------


@requires_openssl
def test_mismatched_pair_is_rejected_with_one_line(tmp_path):
    left = tmp_path / 'left'
    right = tmp_path / 'right'
    tls.ensure_self_signed_certificate(directory=left)
    tls.ensure_self_signed_certificate(directory=right)
    with pytest.raises(TlsError) as caught:
        validate_certificate_pair(left / tls.CERT_FILENAME, right / tls.KEY_FILENAME)
    assert 'do not match' in str(caught.value)


def test_missing_pair_files_are_reported(tmp_path):
    with pytest.raises(TlsError) as caught:
        validate_certificate_pair(tmp_path / 'absent.crt', tmp_path / 'absent.key')
    assert 'certificate not found' in str(caught.value)


def test_split_host_values_accepts_names_and_addresses():
    hosts, ips = split_host_values(['media.lan, 10.0.0.5', 'studio.local'])
    assert hosts == ('media.lan', 'studio.local')
    assert ips == ('10.0.0.5',)
    assert split_host_values(['  ']) == ((), ())


def test_split_host_values_rejects_junk():
    with pytest.raises(TlsError) as caught:
        split_host_values(['not a host!!'])
    assert 'Not a host name or IP address' in str(caught.value)


def test_discover_names_always_has_loopback_and_extras():
    hosts, ips = discover_names(extra_hosts=['media.lan'], extra_ips=['10.0.0.7'])
    assert 'localhost' in hosts
    assert {'127.0.0.1', '::1'} <= set(ips)
    assert 'media.lan' in hosts
    assert '10.0.0.7' in ips


def test_describe_material_reports_state_and_safety_note(tmp_path):
    lines = describe_material(_material(tmp_path))
    text = '\n'.join(lines)
    assert 'HTTPS certificate: generated' in text
    assert str(tmp_path / 'server.crt') in text
    assert 'never copy the private key' in text

    custom = '\n'.join(describe_material(_material(tmp_path, custom=True)))
    assert 'HTTPS certificate: user-provided' in custom
    assert 'install this certificate on the devices' in custom


def test_inspect_material_is_empty_before_generation(tmp_path):
    assert tls.inspect_material(directory=tmp_path) is None


def test_render_openssl_config_lists_every_san():
    config = tls.render_openssl_config(['localhost', 'studio.local'], ['127.0.0.1', '::1'])
    assert 'CN = TikLocal local' in config
    assert 'DNS.1 = localhost' in config
    assert 'DNS.2 = studio.local' in config
    assert 'IP.1 = 127.0.0.1' in config
    assert 'IP.2 = ::1' in config
    assert 'subjectAltName = @alt_names' in config


@requires_openssl
def test_key_permissions_are_private_after_generation(tmp_path):
    if sys.platform.startswith('win'):
        # chmod on Windows only toggles the read-only attribute; access is governed
        # by the inherited ACL, which already excludes other users' profiles.
        pytest.skip('POSIX permission bits are not meaningful on Windows')
    material = tls.ensure_self_signed_certificate(directory=tmp_path)
    mode = material.key_path.stat().st_mode & 0o777
    assert mode & 0o077 == 0, f'private key is readable by others: {oct(mode)}'


# --- CLI --------------------------------------------------------------------


def _serve_stub(monkeypatch, *, raises):
    """Replace the TLS server factory; return the list of recorded calls."""
    calls = []

    class FakeServer:
        def __init__(self, host, port, app, **kwargs):
            calls.append({'host': host, 'port': port, 'app': app, 'kwargs': kwargs})

        def serve_forever(self):
            raise raises

    monkeypatch.setattr('werkzeug.serving.make_server', FakeServer)
    return calls


def _run_cli(monkeypatch, argv, config=None, material=None):
    from tiklocal.run import main

    monkeypatch.setattr('tiklocal.run.load_config', lambda: dict(config or {}))
    if material is not None:
        monkeypatch.setattr('tiklocal.run.ensure_self_signed_certificate', lambda **kwargs: material)
        monkeypatch.setattr('tiklocal.run.load_custom_certificate', lambda *args: material)
    monkeypatch.setattr(sys, 'argv', ['tiklocal', *argv])
    return main


@pytest.fixture(name='fake_material')
def fixture_fake_material(tmp_path):
    return _material(tmp_path / 'tls')


@pytest.fixture(name='fake_material_dir')
def fixture_fake_material_dir(fake_material):
    fake_material.directory.mkdir(parents=True, exist_ok=True)
    return fake_material


def test_tls_serve_uses_werkzeug_server_with_secure_cookie(
    tmp_path, monkeypatch, capsys, fake_material
):
    calls = _serve_stub(monkeypatch, raises=RuntimeError('test server stopped'))
    main = _run_cli(
        monkeypatch,
        [str(tmp_path), '--host', '127.0.0.1', '--port', '8825', '--tls'],
        material=fake_material,
    )
    with pytest.raises(RuntimeError, match='test server stopped'):
        main()
    out = capsys.readouterr().out
    assert 'Open: https://127.0.0.1:8825' in out
    assert 'Server: Werkzeug threaded server (TLS)' in out
    assert 'Trust: your browser warns once about the self-signed certificate' in out
    assert len(calls) == 1
    assert calls[0]['kwargs']['ssl_context'] == (
        str(fake_material.cert_path), str(fake_material.key_path)
    )
    assert calls[0]['kwargs']['threaded'] is True
    # Auth is on by default, so the session cookie must be marked Secure.
    assert calls[0]['app'].config['SESSION_COOKIE_SECURE'] is True


def test_plain_serve_still_uses_waitress_and_http(tmp_path, monkeypatch, capsys):
    calls = []

    class FakeWaitress:
        def __init__(self, app, **kwargs):
            calls.append(kwargs)

        def run(self):
            raise RuntimeError('test server stopped')

    monkeypatch.setattr('tiklocal.run.create_waitress_server', lambda app, **kwargs: FakeWaitress(app, **kwargs))
    main = _run_cli(monkeypatch, [str(tmp_path), '--host', '127.0.0.1', '--port', '8826'])
    with pytest.raises(RuntimeError, match='test server stopped'):
        main()
    out = capsys.readouterr().out
    assert 'Open: http://127.0.0.1:8826' in out
    assert 'Server: Waitress (HTTP)' in out
    assert calls == [{'host': '127.0.0.1', 'port': 8826, 'threads': 8}]


def test_tls_defaults_to_https_port(tmp_path, monkeypatch, capsys, fake_material):
    calls = _serve_stub(monkeypatch, raises=RuntimeError('test server stopped'))
    main = _run_cli(
        monkeypatch,
        [str(tmp_path), '--host', '127.0.0.1', '--tls'],
        material=fake_material,
    )
    with pytest.raises(RuntimeError, match='test server stopped'):
        main()
    assert calls[0]['port'] == 8443
    assert 'Open: https://127.0.0.1:8443' in capsys.readouterr().out


def test_legacy_https_config_starts_https(tmp_path, monkeypatch, capsys, fake_material):
    calls = _serve_stub(monkeypatch, raises=RuntimeError('test server stopped'))
    main = _run_cli(
        monkeypatch,
        [str(tmp_path), '--host', '127.0.0.1', '--port', '8827'],
        config={'https': True, 'hostnames': ['studio.local']},
        material=fake_material,
    )
    with pytest.raises(RuntimeError, match='test server stopped'):
        main()
    assert 'Open: https://127.0.0.1:8827' in capsys.readouterr().out
    assert len(calls) == 1


def test_custom_pair_is_used_and_never_generated(tmp_path, monkeypatch, capsys, fake_material):
    calls = _serve_stub(monkeypatch, raises=RuntimeError('test server stopped'))
    main = _run_cli(
        monkeypatch,
        [
            str(tmp_path), '--host', '127.0.0.1', '--port', '8828', '--tls',
            '--tls-cert', str(fake_material.cert_path),
            '--tls-key', str(fake_material.key_path),
        ],
        material=fake_material,
    )
    with pytest.raises(RuntimeError, match='test server stopped'):
        main()
    assert calls[0]['kwargs']['ssl_context'] == (
        str(fake_material.cert_path), str(fake_material.key_path)
    )


def test_custom_pair_alone_enables_https(tmp_path, monkeypatch, capsys, fake_material):
    """Passing a pair without --tls is intent enough; the config file behaves the same."""
    calls = _serve_stub(monkeypatch, raises=RuntimeError('test server stopped'))
    main = _run_cli(
        monkeypatch,
        [
            str(tmp_path), '--host', '127.0.0.1', '--port', '8830',
            '--tls-cert', str(fake_material.cert_path),
            '--tls-key', str(fake_material.key_path),
        ],
        material=fake_material,
    )
    with pytest.raises(RuntimeError, match='test server stopped'):
        main()
    out = capsys.readouterr().out
    assert 'Open: https://127.0.0.1:8830' in out
    assert len(calls) == 1


@pytest.mark.parametrize('argv,fragment', [
    (['--tls', '--tls-cert', 'only-cert.pem'], '--tls-cert and --tls-key must be used together'),
    (['--tls-hosts', 'example.test'], '--tls-hosts and --tls-days only apply to --tls'),
])
def test_bad_tls_combinations_exit_with_one_line(tmp_path, monkeypatch, capsys, argv, fragment):
    main = _run_cli(monkeypatch, [str(tmp_path), *argv])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert fragment in err
    assert 'Traceback' not in err


def test_tls_error_is_reported_without_traceback(tmp_path, monkeypatch, capsys):
    from tiklocal.run import main

    def broken(**kwargs):
        raise TlsError('Generating a self-signed certificate needs OpenSSL, but it was not found.')

    monkeypatch.setattr('tiklocal.run.load_config', lambda: {})
    monkeypatch.setattr('tiklocal.run.ensure_self_signed_certificate', broken)
    monkeypatch.setattr(sys, 'argv', ['tiklocal', str(tmp_path), '--tls', '--port', '8829'])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert 'Error: Generating a self-signed certificate needs OpenSSL' in captured.err
    assert 'Traceback' not in captured.err


def test_port_taken_reports_one_actionable_line(tmp_path, monkeypatch, capsys, fake_material):
    occupant = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupant.bind(('127.0.0.1', 0))
    occupant.listen(1)
    port = occupant.getsockname()[1]
    try:
        main = _run_cli(
            monkeypatch,
            [str(tmp_path), '--host', '127.0.0.1', '--port', str(port), '--tls'],
            material=fake_material,
        )
        with pytest.raises(SystemExit) as stopped:
            main()
        assert stopped.value.code == 1
        err = capsys.readouterr().err
        assert f'cannot listen on 127.0.0.1:{port}' in err
        assert 'retry with --port' in err
        # No traceback of our own: everything after the error line stays short.
        tail = err[err.index('Error: cannot listen on'):]
        assert 'Traceback' not in tail
    finally:
        occupant.close()


def test_listener_present_sees_a_wildcard_listener():
    from tiklocal.run import listener_present

    occupant = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupant.bind(('0.0.0.0', 0))
    occupant.listen(1)
    port = occupant.getsockname()[1]
    try:
        # 0.0.0.0 itself is not connectable everywhere; the loopback address is.
        assert listener_present(socket.AF_INET, ('0.0.0.0', port)) is True
    finally:
        occupant.close()
    assert listener_present(socket.AF_INET, ('0.0.0.0', port)) is False


def test_probe_listen_is_never_stricter_than_the_real_server():
    """Werkzeug sets ``allow_reuse_address``; the probe must not refuse what it can bind.

    A port parked in TIME_WAIT by the previous run is the everyday case: the
    server takes it, so the probe has to as well instead of sending the user off
    to find a "free" port.
    """
    from tiklocal.run import probe_listen

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(('127.0.0.1', port))
    accepted, _ = server.accept()
    accepted.close()  # closing first is what parks the port in TIME_WAIT
    client.close()
    server.close()

    # Exactly what Werkzeug's own server does before binding.
    real = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        real.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        real.bind(('127.0.0.1', port))
    except OSError:
        pytest.skip('this platform refuses the reuse-address bind the probe mirrors')
    finally:
        real.close()

    probe_listen('127.0.0.1', port, '127.0.0.1')  # must not exit 1


def test_dev_reloader_child_does_not_take_the_instance_lock(tmp_path, monkeypatch):
    """--dev re-runs main() in a child while the parent holds the lock."""
    from tiklocal.services.instance_lock import acquire_instance_lock

    instance = tmp_path / 'instance'
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    served = []
    monkeypatch.setattr('flask.Flask.run', lambda self, **kwargs: served.append(kwargs))
    main = _run_cli(
        monkeypatch,
        [str(tmp_path), '--host', '127.0.0.1', '--port', '8831', '--dev'],
    )
    holder = acquire_instance_lock(instance)  # what the parent already owns
    try:
        main()
    finally:
        holder.close()
    assert served and served[0]['debug'] is True


def test_a_second_serve_on_the_same_instance_is_refused(tmp_path, monkeypatch, capsys):
    """The --dev child may skip the lock; a genuinely second server may not."""
    from tiklocal.services.instance_lock import acquire_instance_lock

    instance = tmp_path / 'instance'
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(instance))
    main = _run_cli(monkeypatch, [str(tmp_path), '--host', '127.0.0.1', '--port', '8832'])

    holder = acquire_instance_lock(instance)  # a server that is already running
    try:
        with pytest.raises(SystemExit) as stopped:
            main()
    finally:
        holder.close()

    assert stopped.value.code == 1
    assert 'already in use' in capsys.readouterr().err


def test_an_explicit_zero_lifetime_is_an_error_not_the_default(tmp_path, monkeypatch, capsys):
    """``--days 0`` must reach the range check instead of meaning "unset"."""
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'instance'))
    main = _run_cli(monkeypatch, ['tls', 'generate', '--days', '0'])

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == 1
    assert 'between 1 and 3650 days' in capsys.readouterr().err


def test_tls_status_without_material_explains_the_next_step(tmp_path, monkeypatch, capsys):
    from tiklocal.run import main

    monkeypatch.setattr('tiklocal.run.load_config', lambda: {})
    monkeypatch.setattr(sys, 'argv', ['tiklocal', 'tls', 'status'])
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'instance'))
    main()
    out = capsys.readouterr().out
    assert 'HTTPS certificate: none yet' in out
    assert 'first `tiklocal --tls` start' in out


def test_tls_status_reports_a_broken_pair(tmp_path, monkeypatch, capsys):
    from tiklocal.run import main

    directory = tmp_path / 'instance' / 'tls'
    directory.mkdir(parents=True)
    (directory / tls.CERT_FILENAME).write_text('not a certificate\n', encoding='ascii')
    (directory / tls.KEY_FILENAME).write_text('not a key\n', encoding='ascii')
    monkeypatch.setattr('tiklocal.run.load_config', lambda: {})
    monkeypatch.setattr(sys, 'argv', ['tiklocal', 'tls', 'status'])
    monkeypatch.setenv('TIKLOCAL_INSTANCE', str(tmp_path / 'instance'))
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert 'Error: ' in err
    assert 'Traceback' not in err


@requires_openssl
def test_https_server_actually_terminates_tls(tmp_path, monkeypatch):
    """Serve the real app over TLS with the generated pair and fetch the login page."""
    import threading

    from werkzeug.serving import make_server

    from tiklocal.app import create_app

    material = tls.ensure_self_signed_certificate(directory=tmp_path / 'tls')
    app = create_app({'TESTING': False, 'MEDIA_ROOT': tmp_path, 'AUTH_ENABLED': True})
    server = make_server(
        '127.0.0.1',
        0,
        app,
        threaded=True,
        ssl_context=(str(material.cert_path), str(material.key_path)),
    )
    port = server.port
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
            with context.wrap_socket(raw) as conn:
                conn.sendall(
                    b'GET /login HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
                )
                response = b''
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    response += chunk
        assert response.startswith(b'HTTP/1.1 200')
        assert b'login' in response.lower()
    finally:
        server.shutdown()
        worker.join(timeout=10)
        server.server_close()
