#!/usr/bin/env python3
"""Outbound HTTP resolves over IPv4 only.

Both the backfill and the hourly refresh have failed with
"[Errno 101] Network is unreachable" against a dual-stack NASA host, at times
for every window in a slice -- 73 windows, 0 rows. GitHub runners generally
have no IPv6 route, so widening retries cannot help: resolution must not hand
back an AAAA address in the first place.

No network: the gate inspects what getaddrinfo is asked for, not what connects.

Run: python3 tests/test_net.py
"""
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from net import force_ipv4  # noqa: E402

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


real = socket.getaddrinfo
fake_calls: list = []


def fake_getaddrinfo(host, port, family=0, *args, **kwargs):
    fake_calls.append(family)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port))]


socket.getaddrinfo = fake_getaddrinfo
force_ipv4()
socket.getaddrinfo("example.invalid", 443)
check(bool(fake_calls) and fake_calls[-1] == socket.AF_INET,
      f"family is pinned to AF_INET, saw {fake_calls}")

# An explicit family from the caller must not defeat the pin, or a library
# asking for AF_UNSPEC would quietly get IPv6 back.
socket.getaddrinfo("example.invalid", 443, socket.AF_UNSPEC)
check(fake_calls[-1] == socket.AF_INET, "an explicit AF_UNSPEC is overridden")

# Applying it twice must not stack wrappers: a second wrap would still work
# but would grow the call chain every time a tool imported another tool.
before = len(fake_calls)
force_ipv4()
socket.getaddrinfo("example.invalid", 443)
check(fake_calls[-1] == socket.AF_INET, "still AF_INET after a second call")
check(len(fake_calls) == before + 1,
      f"one resolution per call, saw {len(fake_calls) - before}")

socket.getaddrinfo = real

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: outbound resolution is IPv4-only")
