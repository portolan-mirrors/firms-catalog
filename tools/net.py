#!/usr/bin/env python3
"""Pin outbound resolution to IPv4.

firms.modaps.eosdis.nasa.gov is dual-stack and GitHub runners generally have no
IPv6 route, which surfaces as "[Errno 101] Network is unreachable". It is not a
rate limit and not a transient blip: one slice failed all 73 of its windows and
fetched zero rows, and the same error has taken out scheduled refreshes. Retries
cannot help, because the route does not exist -- only the address family does.

Resolving A records only is deliberate rather than defensive. If FIRMS ever
becomes IPv6-only this breaks loudly, which is the right failure: silently
falling back would restore the intermittent, hours-long stalls this replaces.

Idempotent, because tools import each other and a second wrap would lengthen
the call chain on every import without changing behaviour.
"""
from __future__ import annotations

import socket

_ORIGINAL = None


def force_ipv4() -> None:
    """Make every socket.getaddrinfo in this process ask for IPv4 only."""
    global _ORIGINAL
    if _ORIGINAL is not None:
        return
    _ORIGINAL = socket.getaddrinfo

    def ipv4_only(host, port, family=0, *args, **kwargs):
        # The caller's family is discarded on purpose: urllib passes
        # AF_UNSPEC, which is exactly the case that yields an AAAA record.
        return _ORIGINAL(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = ipv4_only
