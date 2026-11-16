#!/usr/bin/env python3
"""hello-tee — the smallest Enclava app.

One page, `/health`, and the in-pod attestation sidecar's verified claims
rendered server-side. No storage, no secrets — just enough to prove the loop:
build → sign → create → deploy → claim → serve.

The sidecar (http://127.0.0.1:8081 inside the guest) is reachable only from the
workload, so the page fetches it server-side. What the page shows is a CLAIM;
the proof is `enclava verify <origin> --policy <yours>` — see the README.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PORT = int(os.environ.get("PORT", "8080"))
PROXY = os.environ.get("ATTESTATION_PROXY", "http://127.0.0.1:8081")
HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"


def fetch(path: str, timeout: float = 2.0):
    """GET <proxy><path> as JSON. Never raises — {"_error": ...} degrades the
    card gracefully (e.g. no sidecar when running locally, /status 404s in
    auto-unlock mode)."""
    try:
        with urllib.request.urlopen(f"{PROXY}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # one net for all failures; demo only.
        return {"_error": f"{type(e).__name__}: {e}"}


class H(BaseHTTPRequestHandler):
    server_version = "hello-tee"

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self._send(200, b"ok", "text/plain")
        elif path == "/":
            payload = {
                "attestation_info": fetch("/v1/attestation/info"),
                "ownership_status": fetch("/status"),
                "guest": {"hostname": socket.gethostname()},
            }
            page = INDEX.read_text().replace("__PAYLOAD__", json.dumps(payload))
            self._send(200, page.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # enclava relays logs encrypted; keep stderr quiet.
        pass


if __name__ == "__main__":
    print(f"hello-tee listening on 0.0.0.0:{PORT} (proxy={PROXY})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
