#!/usr/bin/env python3
"""attested-rng contract: nonce + count validation, derivation determinism,
hash-chain integrity across draws, ephemeral fallback, and the HTTP surface."""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server


def valid_nonce(seed: bytes = b"x" * 32) -> str:
    return base64.urlsafe_b64encode(seed).decode().rstrip("=")


class NonceValidation(unittest.TestCase):
    def test_accepts_urlsafe_unpadded_and_padded(self):
        n = valid_nonce()
        self.assertEqual(server.parse_nonce(f"nonce={n}"), n)
        padded = n + "=" * ((4 - len(n) % 4) % 4)
        self.assertEqual(server.parse_nonce(f"nonce={padded}"), padded)

    def test_rejects_missing_or_duplicate(self):
        self.assertIsNone(server.parse_nonce(""))
        self.assertIsNone(server.parse_nonce("a=1"))
        self.assertIsNone(server.parse_nonce(f"nonce={valid_nonce()}&nonce={valid_nonce()}"))

    def test_rejects_wrong_length_and_bad_encoding(self):
        short = base64.urlsafe_b64encode(b"too short").decode().rstrip("=")
        self.assertIsNone(server.parse_nonce(f"nonce={short}"))
        self.assertIsNone(server.parse_nonce("nonce=!!!not-base64!!!"))
        self.assertIsNone(server.parse_nonce("nonce=" + "A" * (server.NONCE_MAX_CHARS + 1)))
        self.assertIsNone(server.parse_nonce(f"nonce={valid_nonce()}x"))  # 33 bytes


class CountValidation(unittest.TestCase):
    def test_default_and_bounds(self):
        self.assertEqual(server.parse_count(""), server.DEFAULT_COUNT)
        self.assertEqual(server.parse_count("a=1"), server.DEFAULT_COUNT)
        self.assertEqual(server.parse_count("count=1"), 1)
        self.assertEqual(server.parse_count(f"count={server.MAX_COUNT}"), server.MAX_COUNT)

    def test_rejects_out_of_range_garbage_or_duplicate(self):
        for q in ("count=0", f"count={server.MAX_COUNT + 1}", "count=abc", "count=-1",
                  "count=1&count=2", "count=1.5"):
            self.assertIsNone(server.parse_count(q), q)


class Derivation(unittest.TestCase):
    def test_deterministic_and_in_range(self):
        a = server.derive_numbers("1|abc|deadbeef", 6)
        b = server.derive_numbers("1|abc|deadbeef", 6)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 6)
        for n in a:
            self.assertTrue(1 <= n <= server.RANGE, n)

    def test_transcript_changes_change_numbers(self):
        self.assertNotEqual(server.derive_numbers("1|abc|d0", 6),
                            server.derive_numbers("2|abc|d0", 6))
        self.assertNotEqual(server.derive_numbers("1|abc|d0", 6),
                            server.derive_numbers("1|abd|d0", 6))

    def test_matches_documented_formula(self):
        t = "7|NONCE|e"
        h0 = hashlib.sha256(t.encode()).digest()
        want = [1 + int.from_bytes(hashlib.sha256(h0 + str(i).encode()).digest()[:8], "big") % 100
                for i in range(3)]
        self.assertEqual(server.derive_numbers(t, 3), want)


class DrawChain(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_draws_chain_and_persist(self):
        d1 = server.record_draw(valid_nonce(b"a" * 32), 2, self.dir)
        d2 = server.record_draw(valid_nonce(b"b" * 32), 2, self.dir)
        self.assertEqual((d1["mode"], d1["draw_id"]), ("persistent", 1))
        self.assertEqual((d2["mode"], d2["draw_id"]), ("persistent", 2))
        self.assertIsNone(d1["prev"])
        self.assertEqual(d2["prev"], d1["chain_head"])   # the chain links
        # chain head is the hash of (prev or '')|transcript — recomputable from the response
        want = hashlib.sha256(f'{d2["prev"] or ""}|{d2["transcript"]}'.encode()).hexdigest()
        self.assertEqual(d2["chain_head"], want)
        # numbers follow the transcript
        self.assertEqual(d2["numbers"], server.derive_numbers(d2["transcript"], 2))
        # the log and state landed on "disk"
        lines = (self.dir / "log.ndjson").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["draw_id"], 2)
        self.assertEqual(server.chain_stats(self.dir)["draws"], 2)
        self.assertEqual(len(server.tail_log(20, self.dir)), 2)

    def test_survives_restart(self):
        server.record_draw(valid_nonce(), 1, self.dir)
        d2 = server.record_draw(valid_nonce(), 1, self.dir)  # same process, but state is file-backed
        self.assertEqual(d2["draw_id"], 2)  # monotonic via state.json, not memory


class EphemeralFallback(unittest.TestCase):
    def test_unwritable_dir_degrades_honestly(self):
        d = Path("/proc/nonexistent-attested-rng")
        d1 = server.record_draw(valid_nonce(), 1, d)
        self.assertEqual(d1["mode"], "ephemeral")
        self.assertIsNotNone(d1["numbers"])
        # head is still the verifiable entry hash — mode alone carries the caveat
        want = hashlib.sha256(f'{d1["prev"] or ""}|{d1["transcript"]}'.encode()).hexdigest()
        self.assertEqual(d1["chain_head"], want)
        stats = server.chain_stats(d)
        self.assertEqual((stats["mode"], stats["chain_head"]), ("ephemeral", d1["chain_head"]))


class HttpContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.prev_data = server.DATA_DIR
        server.DATA_DIR = cls.dir            # handler draws go to a real temp dir
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        server.DATA_DIR = cls.prev_data
        shutil.rmtree(cls.dir, ignore_errors=True)

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.headers, json.loads(r.read())

    def test_health(self):
        with urllib.request.urlopen(f"{self.base}/health") as r:
            self.assertEqual((r.status, r.read()), (200, b"ok"))

    def test_draw_contract(self):
        n = valid_nonce(b"z" * 32)
        status, headers, j = self.get(f"/draw?nonce={n}&count=3")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(set(j), {"server", "draw_id", "nonce", "count", "numbers",
                                  "transcript", "derivation", "chain_head", "prev",
                                  "mode", "generated_at"})
        self.assertEqual(j["nonce"], n)
        self.assertEqual(j["count"], 3)
        self.assertEqual(j["numbers"], server.derive_numbers(j["transcript"], 3))

    def test_bad_nonce_and_count_400(self):
        for q in ("", "nonce=short", "nonce=x&nonce=y", "count=0"):
            if q == "count=0":
                q = f"nonce={valid_nonce()}&count=0"
            try:
                urllib.request.urlopen(f"{self.base}/draw?{q}")
                self.fail(f"expected 400 for {q!r}")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 400)

    def test_page_and_log(self):
        with urllib.request.urlopen(f"{self.base}/") as r:
            body = r.read().decode()
            self.assertIn("attested-rng", body)
            self.assertIn("const P = ", body)
        status, _, j = self.get("/log")
        self.assertEqual(status, 200)
        self.assertIn("draws", j)


if __name__ == "__main__":
    unittest.main()
