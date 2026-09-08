#!/usr/bin/env python3
"""Static server with HTTP Range support.

python -m http.server ignores Range and answers 200 with the whole file, which
silently breaks PMTiles: the client asks for a header slice and gets megabytes
of file, so nothing parses and no tiles ever render.
"""
import os, re, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

class H(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def send_head(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
        if not m:
            return super().send_head()
        s, e = m.group(1), m.group(2)
        if s == "":                      # suffix range: last N bytes
            length = int(e); start = max(0, size - length); end = size - 1
        else:
            start = int(s); end = int(e) if e else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416); return None
        f = open(path, "rb"); f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return _Slice(f, end - start + 1)

class _Slice:
    def __init__(self, f, n): self.f, self.n = f, n
    def read(self, k=-1):
        if self.n <= 0: return b""
        k = self.n if k < 0 else min(k, self.n)
        b = self.f.read(k); self.n -= len(b); return b
    def close(self): self.f.close()

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
