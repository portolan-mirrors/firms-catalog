#!/usr/bin/env python3
"""The publish contract: only the published directory is ever uploaded.

This is the gate that turns the three-file-category model into an enforced
property. It builds a temp tree holding all three categories, asks the
publisher what it would upload, and asserts set equality — so a leak fails and
a missing file fails too.

No network, no AWS, no credentials.

Run: python3 tests/test_publish.py
"""
import hashlib
import importlib.util
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import (  # noqa: E402
    Upload,
    aws_session,
    collect_uploads,
    content_type_for,
    is_unchanged,
    split_s3_uri,
    unedited_sentinels,
    upload_all,
)

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- what gets uploaded, and what never does ---------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # Category 1: tracked and published.
    write(root / "catalog/catalog.json")
    write(root / "catalog/README.md")
    write(root / "catalog/AGENTS.md")
    write(root / "catalog/roads/collection.json")
    write(root / "catalog/roads/thumbnail.png")
    write(root / "catalog/roads/styles/default.json")
    write(root / "catalog/_assets/logo.svg")

    # Category 2: tracked, never published.
    write(root / "tools/publish.py")
    write(root / "tests/test_publish.py")
    write(root / "docs/conformance.md")
    write(root / "README.md")
    write(root / "AGENTS.md")
    write(root / "CLAUDE.md")
    write(root / "catalog.publish.yaml")
    write(root / ".github/workflows/ci.yml")

    # Dotfiles inside the published directory are tracked but not uploaded.
    write(root / "catalog/_assets/.gitkeep", "")
    write(root / "catalog/.portolan/state.json")

    config = {
        "write_prefix": "s3://a-bucket/a/prefix",
        "public_base": "https://data.example.org/a/prefix",
        "publish_dir": "catalog",
    }
    keys = {u.key for u in collect_uploads(config, root)}

    expected = {
        "a/prefix/catalog.json",
        "a/prefix/README.md",
        "a/prefix/AGENTS.md",
        "a/prefix/roads/collection.json",
        "a/prefix/roads/thumbnail.png",
        "a/prefix/roads/styles/default.json",
        "a/prefix/_assets/logo.svg",
    }
    check(keys == expected, f"upload set wrong.\n  extra:   {keys - expected}"
                            f"\n  missing: {expected - keys}")

    # The bare-prefix case: no prefix at all.
    flat = dict(config, write_prefix="s3://a-bucket")
    check(
        {u.key for u in collect_uploads(flat, root)}
        == {k.removeprefix("a/prefix/") for k in expected},
        "keys are wrong when write_prefix names no prefix",
    )

# --- split_s3_uri ------------------------------------------------------
check(split_s3_uri("s3://b/a/c") == ("b", "a/c"), "plain uri")
check(split_s3_uri("s3://b/a/c/") == ("b", "a/c"), "trailing slash")
check(split_s3_uri("s3://b") == ("b", ""), "bare bucket")
check(split_s3_uri("s3://b/") == ("b", ""), "bare bucket, trailing slash")

# --- content types -----------------------------------------------------
check(content_type_for(Path("a/catalog.json")) == "application/json",
      "plain json")
check(
    content_type_for(Path("a/styles/default.json"))
    == "application/vnd.mapbox.style+json",
    "json under styles/ is a MapLibre style",
)
check(
    content_type_for(Path("a/roads.style.json"))
    == "application/vnd.mapbox.style+json",
    "*.style.json is a MapLibre style",
)
check(
    content_type_for(Path("a/d.parquet")) == "application/vnd.apache.parquet",
    "parquet",
)
check(content_type_for(Path("a/x.unknown")) == "application/octet-stream",
      "unknown suffix falls back")

# --- change detection --------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    local = write(Path(tmp) / "f.json", "hello")
    digest = hashlib.md5(b"hello").hexdigest()  # noqa: S324
    upload = Upload(local, "k", "application/json")

    check(is_unchanged(upload, {"k": (5, digest)}), "identical bytes")
    check(is_unchanged(upload, {"k": (5, f'"{digest}"')}), "quoted etag")
    check(not is_unchanged(upload, {}), "absent key")
    check(not is_unchanged(upload, {"other": (5, digest)}), "key mismatch")
    check(not is_unchanged(upload, {"k": (5, "0" * 32)}), "etag differs")
    check(not is_unchanged(upload, {"k": (9, digest)}), "size differs")
    check(is_unchanged(upload, {"k": (5, "abc-2")}), "multipart: size only")

# --- the sentinel guard ------------------------------------------------
check(
    unedited_sentinels({
        "write_prefix": "s3://EXAMPLE-BUCKET/EXAMPLE-PREFIX",
        "public_base": "https://example.invalid/EXAMPLE-PREFIX",
    }) != [],
    "an unedited config is refused",
)
check(
    unedited_sentinels({
        "write_prefix": "s3://real/prefix",
        "public_base": "https://data.example.org/prefix",
    }) == [],
    "an edited config is accepted",
)

# --- the parallel upload pool ------------------------------------------
# A fake session stands in for boto3, so this stays offline and has no
# credentials. It records every call and fails one chosen key.
class FakeClient:
    def __init__(self, calls: list, fail_key: str | None) -> None:
        self.calls = calls
        self.fail_key = fail_key

    # Mirrors boto3's signature: the real client takes Config, and s3_client
    # passes botocore settings, so a fake that refuses them hides real breakage.
    def upload_file(self, local, bucket, key, ExtraArgs, Config=None):
        if key == self.fail_key:
            raise RuntimeError("boom")
        self.calls.append((local, bucket, key, ExtraArgs["ContentType"]))


class FakeSession:
    def __init__(self, fail_key: str | None = None) -> None:
        self.calls: list = []
        self.clients = 0
        self.fail_key = fail_key

    def client(self, name, **kwargs):
        self.clients += 1
        return FakeClient(self.calls, self.fail_key)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    batch = [
        Upload(write(root / f"f{i}.json"), f"p/f{i}.json", "application/json")
        for i in range(50)
    ]

    session = FakeSession()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        failed = upload_all(session, "a-bucket", batch)
    check(failed == [], "no failures")
    check(
        {c[2] for c in session.calls} == {u.key for u in batch},
        "every object is uploaded exactly once",
    )
    # Progress is batched, not one line per object.
    check(
        len(out.getvalue().splitlines()) < len(batch),
        "progress does not print one line per object",
    )
    check(
        {c[3] for c in session.calls} == {"application/json"},
        "the content type reaches upload_file",
    )
    check(
        {c[1] for c in session.calls} == {"a-bucket"},
        "the bucket reaches upload_file",
    )

    # One bad object names itself and does not stop the other uploads.
    session = FakeSession(fail_key="p/f7.json")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        failed = upload_all(session, "a-bucket", batch)
    check(failed == ["p/f7.json"], f"the failed key is named, got {failed}")
    check(len(session.calls) == len(batch) - 1, "one failure stops nothing")
    check("p/f7.json" in err.getvalue(), "the failed key goes to stderr")

# --- the AWS session ---------------------------------------------------
# boto3 is not a dependency of this template, so this part is skipped when
# boto3 is absent. CI runs without it. A fixture AWS config keeps the check
# off the developer's own profiles.
if importlib.util.find_spec("boto3") is None:
    print("note: boto3 is not installed; skipping the aws_session checks")
else:
    with tempfile.TemporaryDirectory() as tmp:
        conf = write(
            Path(tmp) / "aws-config",
            "[default]\nregion = us-east-1\n\n"
            "[profile a-profile]\nregion = eu-west-1\n",
        )
        os.environ["AWS_CONFIG_FILE"] = str(conf)
        os.environ.pop("AWS_PROFILE", None)

        named = aws_session({"profile": "a-profile", "region": "us-west-2"})
        check(named.profile_name == "a-profile", "profile reaches the session")
        check(named.region_name == "us-west-2", "region reaches the session")

        inherited = aws_session({"profile": "a-profile"})
        check(
            inherited.region_name == "eu-west-1",
            "no region means the region of the profile",
        )

        bare = aws_session({})
        check(bare.profile_name == "default", "no profile means the default")

        empty = aws_session({"profile": "", "region": ""})
        check(empty.profile_name == "default", "an empty profile is no profile")

# Reporting happens once, at the very end of the file, so every check below
# is covered too.


# --- gateway retry -----------------------------------------------------------
# Source Cooperative's CDN answers 524 on a slow UploadPart, which botocore's
# standard retry mode does not treat as retryable. 2022 and 2023 both failed
# publishing that way after their data had already been rebuilt.
import time  # noqa: E402

from publish import (  # noqa: E402
    RETRY_STATUS, UPLOAD_ATTEMPTS, is_transient, status_of,
)

# The backoff is real seconds in production and pointless here.
time.sleep = lambda *_a, **_k: None


class Boom(Exception):
    def __init__(self, code):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": code}}


class FlakyClient:
    """Fails for the first `n` attempts, then succeeds.

    `code` is an HTTP status, or an exception instance to raise as-is.
    """

    def __init__(self, code, n, log):
        self.code, self.left, self.log = code, n, log

    def upload_file(self, local, bucket, key, ExtraArgs, Config=None):
        if self.left > 0:
            self.left -= 1
            raise self.code if isinstance(self.code, Exception) else Boom(self.code)
        self.log.append(key)


class FlakySession:
    def __init__(self, code, n):
        self.log: list = []
        self.client_obj = FlakyClient(code, n, self.log)

    def client(self, name, **kwargs):
        return self.client_obj


check(status_of(Boom(524)) == 524, "status_of reads the HTTP status")


# A dropped connection has no HTTP status. Retrying only on status codes let a
# single lost part fail a 1.3 GB upload outright.
class EndpointConnectionError(Exception):
    pass


class ReadTimeoutError(Exception):
    pass


class Refused(Exception):
    pass


check(is_transient(Boom(524)), "a gateway status is transient")
check(is_transient(EndpointConnectionError("no route")),
      "a lost connection is transient even with no HTTP status")
check(is_transient(ReadTimeoutError("slow")), "a read timeout is transient")
check(not is_transient(Boom(403)), "403 is not transient")
check(not is_transient(Refused("nope")), "an unknown error is not transient")
check(status_of(EndpointConnectionError("x")) is None,
      "a connection error carries no status")
check(status_of(RuntimeError("x")) is None, "status_of tolerates a plain error")
check(524 in RETRY_STATUS, "524 is retryable")
check(403 not in RETRY_STATUS, "403 is not retryable")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    one = [Upload(write(root / "r.json"), "p/r.json", "application/json")]

    # Two 524s then success: the file must land, not be reported failed.
    sess = FlakySession(524, 2)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        failed = upload_all(sess, "a-bucket", one)
    check(failed == [], "a file that 524s twice still uploads")
    check(sess.log == ["p/r.json"], "the retried file is uploaded exactly once")

    # 403 is a real refusal and must not be retried into a long backoff.
    sess = FlakySession(403, 1)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        failed = upload_all(sess, "a-bucket", one)
    check(failed == ["p/r.json"], "403 fails immediately instead of retrying")

    # The real 2020 failure: the connection dropped on part 7 of a multipart
    # upload, with no HTTP status anywhere.
    sess = FlakySession(EndpointConnectionError("could not connect"), 3)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        failed = upload_all(sess, "a-bucket", one)
    check(failed == [], "a dropped connection is retried, not fatal")
    check(sess.log == ["p/r.json"], "the file lands after reconnecting")

    # A permanently broken gateway must give up rather than hang forever.
    sess = FlakySession(524, UPLOAD_ATTEMPTS + 5)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        failed = upload_all(sess, "a-bucket", one)
    check(failed == ["p/r.json"], "a persistent 524 eventually gives up")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: publish contract holds")
