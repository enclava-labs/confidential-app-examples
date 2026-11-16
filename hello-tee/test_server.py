#!/usr/bin/env python3
"""hello-tee HTTP contract: health, page + injected payload, 404, no-store-free."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import server


def start() -> tuple[ThreadingHTTPServer, str]:
    # No sidecar on this host → fetch() degrades to {"_error": ...} cards.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_health(self):
        with urllib.request.urlopen(f"{self.base}/health") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read(), b"ok")

    def test_page_carries_payload(self):
        with urllib.request.urlopen(f"{self.base}/") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers["Content-Type"])
            body = r.read().decode()
            self.assertIn("hello-tee", body)
            # the server injects the sidecar payload; locally it degrades to _error
            payload = json.loads(body.split("const P = ")[1].split(";\n")[0])
            self.assertIn("attestation_info", payload)
            self.assertIn("_error", payload["attestation_info"])  # no sidecar locally

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"{self.base}/nope")
        self.assertEqual(cm.exception.code, 404)

    def test_fetch_degrades_never_raises(self):
        out = server.fetch("/v1/attestation/info")
        self.assertIsInstance(out, dict)


if __name__ == "__main__":
    unittest.main()
