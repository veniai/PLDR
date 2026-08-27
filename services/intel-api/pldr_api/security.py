from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    pass


def validate_public_http_url(url: str, *, resolve: bool = True) -> str:
    parsed=urlparse(url)
    if parsed.scheme not in {"http","https"} or not parsed.hostname: raise UnsafeUrlError("Only public http/https URLs are allowed")
    host=parsed.hostname.lower()
    if host in {"localhost","localhost.localdomain"} or host.endswith(".local"): raise UnsafeUrlError("Local addresses are blocked")
    try: literal_ip=ipaddress.ip_address(host)
    except ValueError: literal_ip=None
    if literal_ip is not None and not literal_ip.is_global: raise UnsafeUrlError(f"Non-public address is blocked: {literal_ip}")
    if not resolve: return url
    try: addresses=socket.getaddrinfo(host,parsed.port or (443 if parsed.scheme=="https" else 80))
    except socket.gaierror as exc: raise UnsafeUrlError(f"Unable to resolve host: {host}") from exc
    for _,_,_,_,sockaddr in addresses:
        ip=ipaddress.ip_address(sockaddr[0])
        if not ip.is_global: raise UnsafeUrlError(f"Non-public address is blocked: {ip}")
    return url
