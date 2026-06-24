"""SSRF guard for user-supplied URLs.

We accept URLs into source ingestion (blog, product, youtube, competitor) and
later fetch them via outbound HTTP. Without validation, a malicious tenant could
point us at AWS IMDS (169.254.169.254), the loopback, internal infra, or a
file:// path that a misconfigured fetcher might read.

This module centralizes the allowlist policy:
  - scheme: only http and https
  - hostname must resolve to a global-unicast IPv4/IPv6 (no private, link-local,
    loopback, multicast, reserved, or 0.0.0.0)
  - max URL length: 2048 chars

Call `validate_external_url(url)` from any service that accepts a tenant URL.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, status

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_URL_LEN = 2048


def _bad(reason: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, f"unsafe url: {reason}")


def _all_resolved_ips(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise _bad(f"hostname does not resolve ({exc})")
    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for _fam, _stype, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        # Strip IPv6 scope id (e.g. fe80::1%eth0)
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        addrs.append(ipaddress.ip_address(ip_str))
    return addrs


def _ip_is_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[bool, str]:
    """Return (ok, reason_if_unsafe)."""
    if ip.is_loopback:    return (False, f"loopback IP ({ip})")
    if ip.is_link_local:  return (False, f"link-local IP ({ip})")
    if ip.is_multicast:   return (False, f"multicast IP ({ip})")
    if ip.is_reserved:    return (False, f"reserved IP ({ip})")
    if ip.is_unspecified: return (False, f"unspecified IP ({ip})")
    if ip.is_private:     return (False, f"private IP ({ip})")
    # Block the AWS / GCP metadata endpoints explicitly even though they're link-local.
    if str(ip) in {"169.254.169.254", "fd00:ec2::254"}:
        return (False, f"metadata endpoint ({ip})")
    return (True, "")


def validate_external_url(url: str | None) -> None:
    """Raises HTTPException(400) when `url` is unsafe to fetch from a server.

    Skipped silently if url is None / empty so callers can pass through optional fields.
    """
    if not url:
        return
    if len(url) > _MAX_URL_LEN:
        raise _bad(f"url too long ({len(url)} > {_MAX_URL_LEN})")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise _bad(f"scheme '{parsed.scheme}' not allowed")
    host = (parsed.hostname or "").strip()
    if not host:
        raise _bad("missing hostname")

    # Direct IP literals (no DNS) — check immediately.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        ok, reason = _ip_is_safe(ip)
        if not ok:
            raise _bad(reason)
        return

    # DNS hostname — block sneaky literals.
    bad_hosts = {"localhost", "ip6-localhost", "ip6-loopback"}
    if host.lower() in bad_hosts:
        raise _bad(f"hostname '{host}' not allowed")

    # Resolve and inspect every address (defense against DNS rebinding for the synchronous check).
    for resolved in _all_resolved_ips(host):
        ok, reason = _ip_is_safe(resolved)
        if not ok:
            raise _bad(reason)
