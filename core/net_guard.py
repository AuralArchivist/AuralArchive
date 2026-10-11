"""
Module Name: net_guard.py
Author: AuralArchivist
Created: September 23, 2026
Last Modified: October 9, 2026
Description:
    Checks user-entered hosts before test-connection requests.

Location:
    /core/net_guard.py
"""

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse


class OutboundURLRejected(ValueError):
    """Raised when a user-supplied URL points somewhere the server must not request."""


def _address_is_forbidden(address: ipaddress._BaseAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def validate_outbound_url(url: str, *, default_scheme: Optional[str] = 'http') -> str:
    """Return ``url`` (with a scheme added if missing) or raise OutboundURLRejected."""
    candidate = (url or '').strip()
    if not candidate:
        raise OutboundURLRejected('A host is required')
    if '://' not in candidate and default_scheme:
        candidate = f'{default_scheme}://{candidate}'

    parsed = urlparse(candidate)
    if parsed.scheme not in ('http', 'https'):
        raise OutboundURLRejected('Only http:// and https:// addresses are allowed')
    host = parsed.hostname
    if not host:
        raise OutboundURLRejected('The address has no host')

    try:
        port = parsed.port
    except ValueError as exc:
        raise OutboundURLRejected('Invalid port') from exc

    try:
        infos = socket.getaddrinfo(host, port or (443 if parsed.scheme == 'https' else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise OutboundURLRejected(f'Could not resolve host {host!r}') from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0].split('%', 1)[0])
        if _address_is_forbidden(address):
            raise OutboundURLRejected(f'Requests to {address} are not allowed')
    return candidate
