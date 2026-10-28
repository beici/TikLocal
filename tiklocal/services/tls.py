"""Self-signed certificate material for the built-in HTTPS server.

TikLocal normally serves through Waitress, which cannot terminate TLS. The
``--tls`` flag switches the production server to Werkzeug's threaded server and
this module supplies the certificate: on first use it generates a self-signed
pair into the instance directory with the system OpenSSL CLI, then reuses it.

No third-party crypto library is involved: OpenSSL performs the signing and the
standard library (``ssl``, ``hashlib``) validates and inspects the result. A
certificate somebody else issued is used as-is through ``--tls-cert``/``--tls-key``
and only validated, never regenerated.
"""

from __future__ import annotations

import calendar
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tiklocal.paths import get_data_dir


#: Certificate file generated and reused under ``<instance>/tls``.
CERT_FILENAME = 'server.crt'
#: Private key that belongs to :data:`CERT_FILENAME`. Never leave this machine.
KEY_FILENAME = 'server.key'
#: Sidecar recording the SANs and fingerprint of the generated pair, so a start
#: can tell a reusable certificate from one that was made for another network.
METADATA_FILENAME = 'material.json'

#: Lifetime of the generated certificate. 825 days is the longest lifetime Chrome
#: still accepts for a publicly-trusted certificate; self-signed ones are not
#: actually capped, but there is no reason to hand out decade-long secrets.
DEFAULT_DAYS = 825
#: Regenerate before expiry so a long-running deployment does not go dark.
RENEW_BEFORE_DAYS = 30
DEFAULT_TLS_PORT = 8443

COMMON_NAME = 'TikLocal local'
ORGANIZATION = 'TikLocal'
OPENSSL_TIMEOUT_SECONDS = 120

#: Search paths for OpenSSL when it is installed but not on PATH. Git for Windows
#: bundles it, and it is the one most Windows users already have.
OPENSSL_CANDIDATES = (
    r'C:\Program Files\Git\usr\bin\openssl.exe',
    r'C:\Program Files\Git\mingw64\bin\openssl.exe',
    r'C:\Program Files\OpenSSL-Win64\bin\openssl.exe',
    r'C:\Program Files\OpenSSL\bin\openssl.exe',
    '/usr/bin/openssl',
    '/usr/local/bin/openssl',
    '/opt/homebrew/bin/openssl',
    '/opt/local/bin/openssl',
    '/data/data/com.termux/files/usr/bin/openssl',
)

_HOSTNAME_PATTERN = re.compile(r'^[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?$')


class TlsError(RuntimeError):
    """A TLS problem with an actionable one-line message for the CLI."""


@dataclass(frozen=True)
class TlsMaterial:
    """A certificate/key pair plus what is known about it."""

    cert_path: Path
    key_path: Path
    hosts: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    days: int = DEFAULT_DAYS
    not_before: float | None = None
    not_after: float | None = None
    sha256: str = ''
    created: bool = False
    custom: bool = False
    directory: Path | None = None


@dataclass(frozen=True)
class CertificateInfo:
    """What can be read back from a certificate file."""

    subject: str = ''
    not_before: str = ''
    not_after: str = ''
    not_before_epoch: float | None = None
    not_after_epoch: float | None = None
    hosts: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    sha256: str = ''
    inspected: bool = False


def find_openssl() -> str | None:
    """Return the OpenSSL executable, looking on PATH and in known install dirs."""
    found = shutil.which('openssl')
    if found:
        return found
    for candidate in OPENSSL_CANDIDATES:
        path = Path(candidate)
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return None


def openssl_install_hint() -> str:
    """One-line installation instruction for the current platform."""
    if sys.platform.startswith('win'):
        return 'install it with `winget install ShiningLight.OpenSSL.Light` (Git for Windows also bundles it)'
    if sys.platform == 'darwin':
        return 'install it with `brew install openssl`'
    if 'com.termux' in os.environ.get('PREFIX', ''):
        return 'install it with `pkg install openssl-tool`'
    return 'install it with `sudo apt install openssl` or `sudo dnf install openssl`'


def default_tls_dir() -> Path:
    """Directory holding the generated certificate and key for this instance."""
    return get_data_dir() / 'tls'


def default_certificate_paths(directory: str | Path | None = None) -> tuple[Path, Path]:
    """Return ``(certificate, private key)`` paths for the given TLS directory."""
    base = Path(directory) if directory is not None else default_tls_dir()
    return base / CERT_FILENAME, base / KEY_FILENAME


def classify_host_value(value: str) -> tuple[str, str] | None:
    """Classify a host name or IP address, returning ``('dns', name)``/``('ip', addr)``."""
    text = str(value or '').strip().strip('[]')
    if not text:
        return None
    try:
        return 'ip', str(ipaddress.ip_address(text.split('%', 1)[0]))
    except ValueError:
        pass
    name = _normalize_dns_name(text)
    return ('dns', name) if name else None


def split_host_values(values: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split CLI-style host values (repeatable, comma separated) into names and IPs.

    Raises :class:`TlsError` for a value that is neither a host name nor an IP
    address, so a typo in ``--hosts`` cannot silently shrink the certificate.
    """
    hosts: list[str] = []
    ips: list[str] = []
    for value in values or ():
        for item in str(value).replace(';', ',').split(','):
            item = item.strip()
            if not item:
                continue
            classified = classify_host_value(item)
            if classified is None:
                raise TlsError(f'Not a host name or IP address: {item}')
            kind, normalized = classified
            if kind == 'ip':
                if normalized not in ips:
                    ips.append(normalized)
            elif normalized not in hosts:
                hosts.append(normalized)
    return tuple(hosts), tuple(ips)


def discover_names(
    *,
    extra_hosts: Iterable[str] = (),
    extra_ips: Iterable[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the DNS names and IP addresses a local certificate should cover.

    ``localhost``, ``127.0.0.1`` and ``::1`` are always included. The machine's
    own host name, its fully-qualified name and every private IPv4 address of a
    local interface are added on a best-effort basis, so a phone that opens
    ``https://<this-machine>:8443`` does not hit a hard name mismatch. Public
    addresses, IPv6 link-local addresses and the addresses of virtual adapters
    that are not reachable from the LAN are left out unless passed explicitly
    with ``extra_hosts``/``extra_ips`` (identical values are merged).
    """
    hosts: list[str] = ['localhost']
    ips: list[str] = ['127.0.0.1', '::1']

    def add(value: object) -> None:
        classified = classify_host_value(str(value or ''))
        if classified is None:
            return
        kind, normalized = classified
        if kind == 'dns':
            if normalized not in hosts:
                hosts.append(normalized)
            if normalized != 'localhost' and '.' not in normalized:
                # mDNS name some browsers and file managers use on a LAN.
                local = f'{normalized}.local'
                if local not in hosts:
                    hosts.append(local)
        elif normalized not in {'0.0.0.0', '::'} and normalized not in ips:
            ips.append(normalized)

    for value in [*extra_hosts, *extra_ips]:
        add(value)

    machine_name = ''
    try:
        machine_name = socket.gethostname() or ''
        add(machine_name)
    except OSError:
        pass
    try:
        add(socket.getfqdn())
    except OSError:
        pass
    # The machine name resolves to one address per interface; only LAN-reachable
    # IPv4 addresses are worth a SAN (virtual adapters and IPv6 link-local
    # addresses churn every boot and never help a browser match).
    try:
        for info in socket.getaddrinfo(machine_name, None):
            address = info[4][0]
            if info[0] == socket.AF_INET and _is_lan_ipv4(address):
                add(address)
    except (OSError, UnicodeError):
        pass
    # Ask the routing table which local address reaches the outside world. No
    # packet is sent (UDP connect only picks a source address); TEST-NET-1 is a
    # documentation range that never answers.
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(('192.0.2.1', 9))
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        if probe is not None:
            try:
                probe.close()
            except OSError:
                pass
    return tuple(hosts), tuple(ips)


def certificate_fingerprint(cert_path: str | Path) -> str:
    """SHA-256 (hex) of the certificate's DER encoding, from a PEM file."""
    path = Path(cert_path)
    try:
        pem = path.read_text(encoding='ascii', errors='strict')
    except OSError as exc:
        raise TlsError(f'Cannot read the TLS certificate {path}: {exc}') from exc
    except UnicodeDecodeError as exc:
        raise TlsError(f'{path} is not a PEM certificate; export the certificate as PEM') from exc
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
    except ValueError as exc:
        raise TlsError(f'{path} is not a PEM certificate; export the certificate as PEM') from exc
    return hashlib.sha256(der).hexdigest()


def format_fingerprint(digest: str) -> str:
    """``ab:cd:...`` form of a hex digest, as printed by browsers."""
    upper = str(digest or '').upper()
    return ':'.join(upper[index:index + 2] for index in range(0, len(upper), 2))


def format_timestamp(epoch: float | None) -> str:
    if not epoch:
        return ''
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(epoch)))
    except (OverflowError, OSError, ValueError):
        return ''


def validate_certificate_pair(cert_path: str | Path, key_path: str | Path) -> None:
    """Raise :class:`TlsError` unless the pair is readable and actually matches."""
    cert = Path(cert_path)
    key = Path(key_path)
    for path, label in ((cert, 'certificate'), (key, 'private key')):
        if not path.exists():
            raise TlsError(f'TLS {label} not found: {path}')
        if not path.is_file():
            raise TlsError(f'TLS {label} is not a file: {path}')
        try:
            with path.open('rb') as handle:
                handle.read(1)
        except OSError as exc:
            raise TlsError(f'Cannot read the TLS {label} {path}: {exc}') from exc
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    except ssl.SSLError as exc:
        reason = getattr(exc, 'reason', None) or str(exc)
        raise TlsError(
            f'The TLS certificate and private key do not match, or the key is '
            f'encrypted ({reason}); TikLocal needs an unencrypted PEM key'
        ) from exc
    except OSError as exc:
        raise TlsError(f'Cannot load the TLS certificate or key: {exc}') from exc


def load_custom_certificate(cert_path: str | Path, key_path: str | Path) -> TlsMaterial:
    """Validate a user-provided pair and describe it. Nothing is written."""
    cert = Path(cert_path).expanduser()
    key = Path(key_path).expanduser()
    validate_certificate_pair(cert, key)
    info = read_certificate_info(cert)
    return TlsMaterial(
        cert_path=cert,
        key_path=key,
        hosts=info.hosts,
        ips=info.ips,
        not_before=info.not_before_epoch,
        not_after=info.not_after_epoch,
        sha256=info.sha256,
        custom=True,
    )


def inspect_material(*, directory: str | Path | None = None) -> TlsMaterial | None:
    """Describe the pair that already exists in ``directory``; writes nothing.

    Returns ``None`` when no pair (or an incomplete one) is on disk. Raises
    :class:`TlsError` when the files exist but cannot be used, so that
    ``tiklocal tls status`` can explain why the server would refuse to start.
    """
    base = Path(directory) if directory is not None else default_tls_dir()
    cert_path, key_path = default_certificate_paths(base)
    if not cert_path.is_file() or not key_path.is_file():
        return None
    validate_certificate_pair(cert_path, key_path)
    metadata = _read_metadata(base) or {}
    info = read_certificate_info(cert_path)
    return TlsMaterial(
        cert_path=cert_path,
        key_path=key_path,
        hosts=info.hosts or tuple(str(item) for item in metadata.get('hosts') or ()),
        ips=info.ips or tuple(str(item) for item in metadata.get('ips') or ()),
        days=int(metadata.get('days') or DEFAULT_DAYS),
        not_before=info.not_before_epoch or _as_epoch(metadata.get('not_before')),
        not_after=info.not_after_epoch or _as_epoch(metadata.get('not_after')),
        sha256=info.sha256,
        created=False,
        directory=base,
    )


def ensure_self_signed_certificate(
    *,
    extra_hosts: Iterable[str] = (),
    extra_ips: Iterable[str] = (),
    directory: str | Path | None = None,
    days: int = DEFAULT_DAYS,
    force: bool = False,
) -> TlsMaterial:
    """Return the instance's self-signed pair, generating it when needed.

    An existing pair is reused when it is still valid, still matches its key, is
    not close to expiry and covers every name this machine currently has. It is
    regenerated when any of that changed (for example after switching networks),
    or unconditionally with ``force=True``.
    """
    if days < 1 or days > 3650:
        raise TlsError('The certificate lifetime must be between 1 and 3650 days')
    base = Path(directory) if directory is not None else default_tls_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TlsError(f'Cannot create the TLS directory {base}: {exc}') from exc
    cert_path, key_path = default_certificate_paths(base)
    hosts, ips = discover_names(extra_hosts=extra_hosts, extra_ips=extra_ips)
    if not force:
        reusable = _reusable_material(base, cert_path, key_path, hosts, ips)
        if reusable is not None:
            return reusable
    return _generate_material(base, cert_path, key_path, hosts, ips, days)


def _reusable_material(
    directory: Path,
    cert_path: Path,
    key_path: Path,
    hosts: tuple[str, ...],
    ips: tuple[str, ...],
) -> TlsMaterial | None:
    if not (cert_path.is_file() and key_path.is_file()):
        return None
    metadata = _read_metadata(directory)
    if not metadata:
        # A pair without metadata was not created by TikLocal (or was made by an
        # older version); regenerate so the SANs are known again.
        return None
    try:
        fingerprint = certificate_fingerprint(cert_path)
    except TlsError:
        # A truncated or overwritten certificate is exactly the case the next
        # generation fixes; failing here would need a manual cleanup first.
        return None
    if fingerprint != str(metadata.get('sha256') or ''):
        return None
    if not _metadata_covers(metadata, hosts, ips):
        return None
    try:
        validate_certificate_pair(cert_path, key_path)
    except TlsError:
        return None
    not_after = metadata.get('not_after')
    if not isinstance(not_after, (int, float)) or not_after < time.time() + RENEW_BEFORE_DAYS * 86400:
        return None
    return TlsMaterial(
        cert_path=cert_path,
        key_path=key_path,
        hosts=tuple(str(item) for item in metadata.get('hosts') or ()),
        ips=tuple(str(item) for item in metadata.get('ips') or ()),
        days=int(metadata.get('days') or DEFAULT_DAYS),
        not_before=_as_epoch(metadata.get('not_before')),
        not_after=float(not_after),
        sha256=str(metadata.get('sha256') or ''),
        created=False,
        directory=directory,
    )


def _generate_material(
    directory: Path,
    cert_path: Path,
    key_path: Path,
    hosts: tuple[str, ...],
    ips: tuple[str, ...],
    days: int,
) -> TlsMaterial:
    openssl = find_openssl()
    if openssl is None:
        raise TlsError(
            'Generating a self-signed certificate needs OpenSSL, but it was not found. '
            f'{openssl_install_hint()}, or provide a certificate pair with --tls-cert/--tls-key.'
        )
    temp_cert = directory / f'.{CERT_FILENAME}.{os.getpid()}.tmp'
    temp_key = directory / f'.{KEY_FILENAME}.{os.getpid()}.tmp'
    result = None
    try:
        with tempfile.TemporaryDirectory(prefix='.tiklocal-tls-', dir=directory) as scratch:
            config_path = Path(scratch) / 'openssl.cnf'
            try:
                config_path.write_text(render_openssl_config(hosts, ips), encoding='ascii')
            except OSError as exc:
                raise TlsError(f'Cannot write a temporary OpenSSL configuration: {exc}') from exc
            command = [
                openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-sha256',
                '-days', str(days), '-nodes',
                '-keyout', str(temp_key), '-out', str(temp_cert),
                '-config', str(config_path),
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=OPENSSL_TIMEOUT_SECONDS,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TlsError(
                    f'OpenSSL did not finish within {OPENSSL_TIMEOUT_SECONDS} s while '
                    'generating the certificate; try again, or provide a pair with '
                    '--tls-cert/--tls-key.'
                ) from exc
            except OSError as exc:
                raise TlsError(f'Cannot run OpenSSL ({openssl}): {exc}') from exc
        if result.returncode != 0 or not temp_cert.is_file() or not temp_key.is_file():
            raise TlsError(
                'OpenSSL could not create the self-signed certificate: '
                + _openssl_error_detail(result)
            )
        validate_certificate_pair(temp_cert, temp_key)
        _restrict_key_permissions(temp_key)
        os.replace(temp_cert, cert_path)
        os.replace(temp_key, key_path)
    except FileNotFoundError as exc:
        raise TlsError(f'Cannot write the TLS material into {directory}: {exc}') from exc
    except OSError as exc:
        raise TlsError(f'Cannot write the TLS material into {directory}: {exc}') from exc
    finally:
        for leftover in (temp_cert, temp_key):
            try:
                leftover.unlink()
            except OSError:
                pass
    info = read_certificate_info(cert_path)
    not_before = info.not_before_epoch or (time.time() - 60)
    not_after = info.not_after_epoch or (not_before + days * 86400)
    material = TlsMaterial(
        cert_path=cert_path,
        key_path=key_path,
        hosts=hosts,
        ips=ips,
        days=days,
        not_before=not_before,
        not_after=not_after,
        sha256=info.sha256 or certificate_fingerprint(cert_path),
        created=True,
        directory=directory,
    )
    _write_metadata(directory, material)
    return material


def render_openssl_config(hosts: Iterable[str], ips: Iterable[str]) -> str:
    """OpenSSL configuration with the SANs for a local self-signed certificate.

    Written to a file instead of passing ``-addext`` because ``-addext`` only
    exists in OpenSSL 1.1.1 and newer; the config form also works for the
    OpenSSL 3 build bundled with Git for Windows.
    """
    lines = [
        '[req]',
        'prompt = no',
        'distinguished_name = dn',
        'x509_extensions = v3_req',
        '',
        '[dn]',
        f'CN = {COMMON_NAME}',
        f'O = {ORGANIZATION}',
        '',
        '[v3_req]',
        'basicConstraints = critical,CA:FALSE',
        'keyUsage = critical,digitalSignature,keyEncipherment',
        'extendedKeyUsage = serverAuth',
        'subjectKeyIdentifier = hash',
        'subjectAltName = @alt_names',
        '',
        '[alt_names]',
    ]
    for index, host in enumerate(hosts, 1):
        lines.append(f'DNS.{index} = {host}')
    for index, ip in enumerate(ips, 1):
        lines.append(f'IP.{index} = {ip}')
    return '\n'.join(lines) + '\n'


def read_certificate_info(cert_path: str | Path) -> CertificateInfo:
    """Read subject, SANs and validity back from a certificate file.

    Uses the OpenSSL CLI when available (the file needs no private key and this
    never touches the network); without OpenSSL only the fingerprint is known.
    """
    path = Path(cert_path)
    digest = certificate_fingerprint(path)
    openssl = find_openssl()
    if openssl is None:
        return CertificateInfo(sha256=digest)
    text = _run_openssl(openssl, ['x509', '-in', str(path), '-noout', '-text'])
    if text is None:
        return CertificateInfo(sha256=digest)
    parsed = _parse_certificate_text(text)
    return CertificateInfo(
        subject=parsed['subject'],
        not_before=parsed['not_before'],
        not_after=parsed['not_after'],
        not_before_epoch=parsed['not_before_epoch'],
        not_after_epoch=parsed['not_after_epoch'],
        hosts=parsed['hosts'],
        ips=parsed['ips'],
        sha256=digest,
        inspected=True,
    )


def describe_material(material: TlsMaterial) -> list[str]:
    """Human-readable lines for ``tiklocal tls status`` and the start banner."""
    if material.custom:
        state = 'user-provided'
    elif material.created:
        state = 'generated'
    else:
        state = 'reused'
    lines = [
        f'HTTPS certificate: {state}',
        f'Certificate: {material.cert_path}',
        f'Private key: {material.key_path}',
    ]
    try:
        info = read_certificate_info(material.cert_path)
    except TlsError:
        # Callers validate first; a partially written file must still describe
        # itself instead of replacing the status output with a traceback.
        info = CertificateInfo(sha256=material.sha256)
    hosts = info.hosts or material.hosts
    ips = info.ips or material.ips
    if hosts or ips:
        if hosts:
            lines.append('Host names: ' + ', '.join(hosts))
        if ips:
            lines.append('IP addresses: ' + ', '.join(ips))
    elif not info.inspected:
        lines.append('Host names: (install OpenSSL to inspect the certificate)')
    expires = info.not_after or format_timestamp(material.not_after)
    if expires:
        lines.append(f'Expires: {expires}')
    if info.subject:
        lines.append(f'Subject: {info.subject}')
    digest = info.sha256 or material.sha256
    if digest:
        lines.append('SHA-256: ' + format_fingerprint(digest))
    if material.custom:
        lines.append('Trust: install this certificate on the devices that connect.')
    else:
        lines.append(
            'Trust: install the certificate on each device (or accept the browser '
            'warning once); never copy the private key.'
        )
    return lines


def _normalize_dns_name(value: str) -> str:
    text = str(value or '').strip().rstrip('.')
    if not text or len(text) > 253 or '..' in text:
        return ''
    if not _HOSTNAME_PATTERN.match(text):
        return ''
    return text.lower()


def _is_lan_ipv4(value: str) -> bool:
    """True for a private, non-loopback, non-link-local IPv4 address."""
    try:
        address = ipaddress.IPv4Address(str(value))
    except ValueError:
        return False
    return bool(
        address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_unspecified
    )


def _restrict_key_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _openssl_error_detail(result: subprocess.CompletedProcess) -> str:
    text = (result.stderr or result.stdout or '').strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f'exit code {result.returncode}'
    return ' / '.join(lines[-2:])[:300]


def _run_openssl(openssl: str, arguments: list[str]) -> str | None:
    try:
        result = subprocess.run(
            [openssl, *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _parse_openssl_date(value: str) -> float | None:
    text = ' '.join(str(value or '').split())
    if text.upper().endswith(' GMT'):
        text = text[:-4]
    try:
        return calendar.timegm(time.strptime(text, '%b %d %H:%M:%S %Y'))
    except ValueError:
        return None


def _parse_alt_names(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    hosts: list[str] = []
    ips: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if 'Subject Alternative Name' not in line:
            continue
        marker_indent = len(line) - len(line.lstrip())
        chunk: list[str] = []
        for follow in lines[index + 1:]:
            if not follow.strip():
                continue
            # The SAN values sit one level deeper than their "X509v3 ..." header;
            # anything at or left of that indent belongs to the next extension.
            if len(follow) - len(follow.lstrip()) <= marker_indent:
                break
            chunk.append(follow.strip())
        for token in ','.join(chunk).split(','):
            token = token.strip()
            if token.startswith('DNS:'):
                name = token[4:].strip().lower()
                if name and name not in hosts:
                    hosts.append(name)
            elif token.startswith('IP Address:'):
                address = token[len('IP Address:'):].strip()
                if not address:
                    continue
                try:
                    # OpenSSL prints IPv6 addresses in their long form; show the
                    # same compact notation the user types into a browser.
                    address = str(ipaddress.ip_address(address.split('%', 1)[0]))
                except ValueError:
                    pass
                if address not in ips:
                    ips.append(address)
        break
    return tuple(hosts), tuple(ips)


def _parse_certificate_text(text: str) -> dict:
    subject = ''
    match = re.search(r'^\s*Subject:\s*(.+)$', text, re.MULTILINE)
    if match:
        raw = match.group(1).strip()
        common_name = re.search(r'CN\s*=\s*([^,]+)', raw)
        subject = f'CN={common_name.group(1).strip()}' if common_name else raw
    not_before = ''
    not_before_epoch = None
    match = re.search(r'^\s*Not Before\s*:\s*(.+)$', text, re.MULTILINE)
    if match:
        raw = match.group(1)
        not_before_epoch = _parse_openssl_date(raw)
        not_before = format_timestamp(not_before_epoch) or ' '.join(raw.split())
    not_after = ''
    not_after_epoch = None
    match = re.search(r'^\s*Not After\s*:\s*(.+)$', text, re.MULTILINE)
    if match:
        not_after_epoch = _parse_openssl_date(match.group(1))
        not_after = format_timestamp(not_after_epoch) or ' '.join(match.group(1).split())
    hosts, ips = _parse_alt_names(text)
    return {
        'subject': subject,
        'not_before': not_before,
        'not_after': not_after,
        'not_before_epoch': not_before_epoch,
        'not_after_epoch': not_after_epoch,
        'hosts': hosts,
        'ips': ips,
    }


def _read_metadata(directory: Path) -> dict | None:
    try:
        payload = json.loads((directory / METADATA_FILENAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_metadata(directory: Path, material: TlsMaterial) -> None:
    payload = {
        'version': 1,
        'certificate': CERT_FILENAME,
        'hosts': list(material.hosts),
        'ips': list(material.ips),
        'days': material.days,
        'not_before': material.not_before,
        'not_after': material.not_after,
        'sha256': material.sha256,
    }
    path = directory / METADATA_FILENAME
    temp = directory / (METADATA_FILENAME + '.tmp')
    try:
        temp.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        os.replace(temp, path)
    except OSError:
        # The certificate itself is already in place; without the sidecar the next
        # start simply regenerates, so a failure here is not worth aborting.
        try:
            temp.unlink()
        except OSError:
            pass


def _as_epoch(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _metadata_covers(metadata: dict, hosts: tuple[str, ...], ips: tuple[str, ...]) -> bool:
    recorded_hosts = {str(item).lower() for item in metadata.get('hosts') or ()}
    recorded_ips = {str(item) for item in metadata.get('ips') or ()}
    return all(host.lower() in recorded_hosts for host in hosts) and all(
        address in recorded_ips for address in ips
    )
