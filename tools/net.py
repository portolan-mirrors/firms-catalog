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
import time
import urllib.error
import urllib.request

_ORIGINAL = None

# Anything but urllib's own. data.source.coop answers Python-urllib/3.x with a
# 403 on every key, present or missing, so the default reads as the whole
# catalog having gone missing at once rather than as a rejected client.
USER_AGENT = "firms-catalog/1.0"


# The statuses that describe the server's moment rather than the object: too
# many requests, and everything from 500 up. Naming them individually looked
# tidier and was wrong -- the CDN in front of this catalog answers 520 through
# 527 for its own origin trouble, and a set written from memory omits them.
def retryable(status: int) -> bool:
    """True when asking again could reasonably give a different answer."""
    return status == 429 or status >= 500


def head(url: str, *, user_agent: str = USER_AGENT, timeout: int = 30,
         tries: int = 3) -> tuple[int | str, int | None]:
    """(status, content length) for url, retrying transport failures.

    The status is an int when the server answered -- 200, 404 -- and a string
    naming the failure when the request never got an answer at all. The two are
    different findings: a 404 is a missing object and says something about the
    catalog, where a dropped connection says something about the network and is
    worth another go. A single blip on one of twenty-seven objects had been
    enough to fail an hourly publish that was otherwise ready to upload.

    Most HTTP statuses are returned on the first answer. A 404 does not become
    a 200 by asking again, and a gate that keeps asking turns a clear answer
    into a slow one. retryable() marks the exceptions: 429 and the 5xx range
    say the server could not answer just now rather than that the object is
    wrong, and the CDN in front of this catalog starts returning them when a
    gate probes a hundred and fifty objects back to back.
    """
    last = "no attempt"
    for attempt in range(tries):
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("content-length")
                return response.status, int(length) if length else None
        except urllib.error.HTTPError as exc:
            if not retryable(exc.code):
                return exc.code, None
            last = exc.code
        except (urllib.error.URLError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < tries:
            time.sleep(2 ** attempt)
    return last, None


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
