"""Outbound requests that must never reach this machine.

The download queue and the avatar cache both take a URL from a response the app
does not control, so both need the same two rules: refuse a URL that resolves to
loopback / link-local / a metadata address, and refuse a redirect hop to one.
Private LAN ranges stay allowed: pointing at a NAS is a legitimate use.
"""
import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse


def is_local_host(url: str) -> bool:
    """True when the URL targets this machine or a link-local/metadata address."""
    try:
        host = (urlparse(url).hostname or "").strip().strip("[]")
    except ValueError:
        return True
    if not host:
        return True
    candidates = [host]
    try:
        candidates.extend(entry[4][0] for entry in socket.getaddrinfo(host, None))
    except OSError:
        pass
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_reserved:
            return True
    return False


class GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a redirect hop that points back at this machine.

    Checking only the final URL is not enough: the request for the hop is sent
    before anyone can look at where it landed, so a public URL that 302s to
    loopback still made the server issue a request to it.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib hook
        if is_local_host(newurl):
            raise urllib.error.URLError("The URL redirected to a local address.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_guarded_opener() -> urllib.request.OpenerDirector:
    """An opener whose redirects are checked hop by hop."""
    return urllib.request.build_opener(GuardedRedirectHandler())
