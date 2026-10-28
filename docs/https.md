# Built-in HTTPS

TikLocal can terminate TLS itself, so phones and tablets get a secure origin
without a reverse proxy. This document records how the switch works, where the
files live, and which failures are expected.

## Why it exists

Several browser features TikLocal uses (clipboard, service workers, camera,
install prompts) only work on a secure origin, and a phone reaching
`http://192.168.1.20:8000` is not one. The previous implementation shipped a local
certificate authority with the `cryptography` package and a Cheroot TLS server;
it was removed in v0.8.38 in favour of an external proxy, which is a dead end on a
LAN with no DNS. This version keeps the feature but drops both dependencies: it
reuses the system `openssl` binary and Werkzeug's threaded server, which is
already an indirect dependency of Flask.

## CLI surface

| Command | Effect |
| --- | --- |
| `tiklocal --tls` | Serve HTTPS. Generates a self-signed certificate on the first start, reuses it afterwards. Default port 8443. |
| `tiklocal --tls --tls-cert <crt> --tls-key <key>` | Serve HTTPS with a certificate pair you own. Both options are required together, and the pair alone turns HTTPS on, so `--tls` may be omitted. |
| `--tls-hosts <names>` | Extra DNS names or IP addresses to put in the generated certificate. Repeatable, comma or semicolon separated. |
| `--tls-days <n>` | Lifetime of a generated certificate (default 825, the maximum modern browsers accept). |
| `tiklocal tls status` | Show the certificate in use: paths, covered names, expiry, subject, SHA-256. |
| `tiklocal tls generate [--hosts ...] [--days N] [--force]` | Create or replace the certificate without starting a server. |
| `tiklocal tls status --cert <crt> --key <key>` | Inspect a certificate pair you own. |

The same settings can come from the configuration file: `https: true` (or a
`tls_cert`/`tls_key` pair) enables HTTPS, and `hostnames` adds names to the
generated certificate. Command-line options win. Malformed values (`https: yes`,
`tls_cert: 123`, a non-list `hostnames`) are startup errors rather than a silent
fallback to plain HTTP.

## Files

Everything lives in the instance directory, next to `auth.json` and the database:
`TIKLOCAL_INSTANCE/tls/`, i.e. `~/.tiklocal/tls/` by default.

| File | Contents |
| --- | --- |
| `server.crt` | The certificate, public. Safe to copy to devices. |
| `server.key` | The private key. Never copy it anywhere. |
| `material.json` | Which names and IPs the certificate covers, when it expires, and its SHA-256. Lets a restart decide between reuse and regeneration. |

The key is written with owner-only permissions and replaced atomically
(`os.replace`), so a crash mid-generation cannot leave a half-written pair.

### Covered names

A generated certificate always covers `localhost`, `127.0.0.1` and `::1`, plus
this machine's host name, `<host>.local`, the FQDN when there is one, every
private IPv4 address the machine currently has, and anything passed through
`--tls-hosts`/`hostnames`. The addresses are discovered on every start: when the
machine gets a new LAN address, the certificate is regenerated so that address
works too.

### Reuse and renewal

A stored pair is reused when all of these hold, and regenerated otherwise:

- `material.json` exists and matches the certificate's SHA-256 (a pair placed there
  by hand without metadata is replaced);
- the certificate and key are a valid pair (checked by loading them into an
  `ssl.SSLContext`);
- the stored names and IPs cover what this start would ask for;
- the certificate does not expire within 30 days.

`tiklocal tls generate --force` skips the checks and replaces the pair.

## Servers

Waitress cannot terminate TLS, so the serving path is explicit:

- plain `tiklocal` keeps using **Waitress** with 8 threads, unchanged;
- `tiklocal --tls` uses **Werkzeug's threaded server** with an `ssl_context`;
- `--dev --tls` uses the Flask reloader with the same certificate.

The startup banner and the `TikLocal ... on https://...; server=...` log line name
the server that is actually running. Over TLS the session cookie is always marked
`Secure`; plain HTTP keeps honouring `FLASK_AUTH_COOKIE_SECURE` so reverse-proxy
deployments behave as before.

## Failure modes

Each of these ends with one actionable line, never a traceback, and exit code 1:

| Situation | Message |
| --- | --- |
| OpenSSL not installed | `Generating a self-signed certificate needs OpenSSL, but it was not found.` + install hint + `or provide a certificate pair with --tls-cert/--tls-key` |
| OpenSSL present but cannot run | `Cannot run OpenSSL at <path>: <reason>` |
| Certificate or key missing / unreadable | `The TLS certificate ... was not found` / `... is not readable` |
| Certificate and key do not match | `The TLS certificate and private key do not match, or the key is encrypted` |
| Port already taken | `cannot listen on <host>:<port> (<reason>)` + `Another process may already use this port; retry with --port <free port>.` |
| `--tls-hosts`/`--tls-days` without `--tls` | `--tls-hosts and --tls-days only apply to --tls` |
| Only one of `--tls-cert`/`--tls-key` | `--tls-cert and --tls-key must be used together` |

`-v`-style debugging is not needed for these; the message is the whole story.

## Trusting the certificate

A self-signed certificate is not trusted by default: the browser warns once per
device. Either accept the warning, or install `server.crt` on the device (iOS:
Settings → General → VPN & Device Management; Android: Settings → Security →
Encryption & credentials → Install a certificate → CA certificate; desktop:
the OS certificate store). Installing the file is enough — the private key stays
on the server.

For a household where the warning is unacceptable, pass a real certificate pair
(`--tls-cert`/`--tls-key`), for example one issued by an internal CA or by
`mkcert`, which is also trusted on the devices that installed its root.

## Testing it

```bash
# 1. Certificate, paths and JSON metadata
TIKLOCAL_INSTANCE=/tmp/tls-demo tiklocal tls generate --force
TIKLOCAL_INSTANCE=/tmp/tls-demo tiklocal tls status

# 2. Serve and check the certificate the server actually presents
TIKLOCAL_INSTANCE=/tmp/tls-demo tiklocal ~/Videos --tls --port 8443 &
openssl s_client -connect 127.0.0.1:8443 -servername localhost </dev/null \
  | openssl x509 -noout -subject -ext subjectAltName -fingerprint -sha256

# 3. Sign in over TLS; the cookie must carry Secure
curl -k -s -c cookies.txt https://127.0.0.1:8443/login -o login.html
TOKEN=$(grep -o 'name="_csrf_token" value="[^"]*"' login.html | head -1 \
  | sed 's/.*value="//;s/"$//')
curl -k -s -b cookies.txt -c cookies.txt -D - -o /dev/null \
  --data-urlencode "_csrf_token=$TOKEN" --data-urlencode 'password=<password>' \
  --data-urlencode 'remember=1' https://127.0.0.1:8443/login | grep -i 'set-cookie'
```

The `pytest tests/test_tls.py` suite covers generation, reuse, renewal, custom
pairs, the CLI wiring (including the banner and `Secure` cookie) and a real TLS
handshake against a live Werkzeug server.
