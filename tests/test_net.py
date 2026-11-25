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

# head() retries what the network and the server can do differently next time.
#
# A dropped connection on one of twenty-seven objects failed an hourly publish
# that was otherwise ready to upload, and the object was fine on every probe a
# minute later. A 404 is the opposite: asking again cannot change it, and a
# gate that keeps asking turns a clear answer into a slow one. A 429 or a 5xx
# sits with the dropped connection, not with the 404 -- the CDN in front of
# this catalog starts returning them once a gate probes its objects in bulk,
# and a real run failed on a 520 that a hand-written list of 5xx codes missed.
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from net import USER_AGENT, head, retryable  # noqa: E402

check(not any(retryable(c) for c in (200, 301, 400, 403, 404, 410)),
      "an answer about the object is final")
check(all(retryable(c) for c in (429, 500, 502, 503, 504, 520, 522, 524)),
      "429 and everything from 500 up is worth another ask, Cloudflare's 52x included")

attempts: list[str] = []


def fake_urlopen(request, timeout=None):
    attempts.append(request.get_header("User-agent"))
    raise next_error[0]


class FakeResponse:
    status = 200
    headers = {"content-length": "7"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen

# Record the backoff instead of serving it. The policy is what this gate is
# for, and sleeping it out would spend twenty seconds proving the clock works.
import net as _net  # noqa: E402

slept: list[float] = []
_real_sleep = _net.time.sleep
_net.time.sleep = slept.append

next_error = [urllib.error.URLError("unreachable")]
status, _ = head("https://example.invalid/x", timeout=1, tries=3)
check(len(attempts) == 3, f"a transport failure is retried, saw {len(attempts)} try(s)")
check(not isinstance(status, int), f"and reports the failure, not a status: {status!r}")
check(all(ua == USER_AGENT for ua in attempts),
      f"every try names itself, saw {set(attempts)}")
check(slept == [1, 2], f"backing off further each time, waited {slept}")
check(len(slept) == len(attempts) - 1, "and not after the last try, which nothing follows")

attempts.clear()
next_error[0] = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
status, _ = head("https://example.invalid/x", timeout=1, tries=3)
check(len(attempts) == 1, f"a 404 is not retried, saw {len(attempts)} try(s)")
check(status == 404, f"and is returned as its code, got {status!r}")

for code in (429, 500, 503, 520, 524):
    attempts.clear()
    next_error[0] = urllib.error.HTTPError("u", code, "busy", {}, None)
    status, _ = head("https://example.invalid/x", timeout=1, tries=3)
    check(len(attempts) == 3, f"{code} is retried, saw {len(attempts)} try(s)")
    check(status == code, f"{code} is reported as its code once spent, got {status!r}")

# The point of retrying a 429 is the try that succeeds, so the answer has to be
# the later one and not the status that provoked the retry.
attempts.clear()
next_error[0] = urllib.error.HTTPError("u", 503, "busy", {}, None)


def flaky_then_served(request, timeout=None):
    attempts.append(request.get_header("User-agent"))
    if len(attempts) < 2:
        raise next_error[0]
    return FakeResponse()


urllib.request.urlopen = flaky_then_served
status, length = head("https://example.invalid/x", timeout=1, tries=3)
check((status, length) == (200, 7),
      f"a 503 that clears returns the served answer, got {(status, length)}")
check(len(attempts) == 2, f"and stops asking once served, saw {len(attempts)}")

attempts.clear()
urllib.request.urlopen = lambda request, timeout=None: (
    attempts.append(request.get_header("User-agent")) or FakeResponse())
status, length = head("https://example.invalid/x", timeout=1)
check((status, length) == (200, 7), f"a served object returns (200, 7), got {(status, length)}")

# get() is head() with a body, and nothing else about it may differ: the tool
# that reads a published item decides whether to stop a publish on exactly the
# distinction head() draws, between an answer about the object and no answer.
import http.client  # noqa: E402

from net import get  # noqa: E402


class FakeBody(FakeResponse):
    def read(self):
        return b'{"id": "2026"}'


methods: list[str] = []
urllib.request.urlopen = lambda request, timeout=None: (
    methods.append(request.get_method()) or FakeBody())
status, body = get("https://example.invalid/x", timeout=1)
check((status, body) == (200, b'{"id": "2026"}'),
      f"a served object returns its body, got {(status, body)}")
check(methods == ["GET"], f"with one GET, saw {methods}")

attempts.clear()
slept.clear()
urllib.request.urlopen = fake_urlopen
next_error[0] = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
status, body = get("https://example.invalid/x", timeout=1, tries=3)
check((status, body, len(attempts)) == (404, None, 1),
      f"a 404 is final for a GET too, got {(status, body, len(attempts))}")

attempts.clear()
next_error[0] = urllib.error.HTTPError("u", 503, "busy", {}, None)
status, body = get("https://example.invalid/x", timeout=1, tries=3)
check((status, body, len(attempts)) == (503, None, 3),
      f"a 503 is retried and then reported, got {(status, body, len(attempts))}")

# A body that stops part-way is not an OSError, and unhandled it would take
# the run down with a traceback instead of a finding.
attempts.clear()
next_error[0] = http.client.IncompleteRead(b'{"id"')
status, body = get("https://example.invalid/x", timeout=1, tries=3)
check(not isinstance(status, int) and body is None and len(attempts) == 3,
      f"a truncated body is retried as a transport failure, got "
      f"{(status, body, len(attempts))}")

urllib.request.urlopen = real_urlopen
_net.time.sleep = _real_sleep

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: outbound resolution is IPv4-only")
