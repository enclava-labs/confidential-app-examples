#!/usr/bin/env python3
"""attested-rng — randomness you can pin to a confidential VM.

A tiny "provably fresh draw" service. The client supplies a 32-byte nonce;
the draw folds it into the derivation transcript, so every response is
bound to a value only the caller knows (replays and prefabrication fail),
and the transcript lets anyone recompute the numbers locally.

Two layers of checking, deliberately mirroring each other:
  1. Derivation — sha256-chain from (draw_id | nonce | entropy) → numbers.
     Verifiable by anyone, anywhere, with no trust in us.
  2. Environment — the numbers were produced inside an AMD SEV-SNP guest
     running the pinned image. Verifiable with `enclava verify` against a
     policy the verifier holds. See README.

Honest scope: this attests WHERE and UNDER WHAT CODE the draw happened —
fresh (your nonce), code-pinned, host-unreadable. It is not a proof of
information-theoretic randomness; the CSPRNG is the guest kernel's.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PORT = int(os.environ.get("PORT", "8080"))
PROXY = os.environ.get("ATTESTATION_PROXY", "http://127.0.0.1:8081")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"

STATE = DATA_DIR / "state.json"   # {"next_draw_id": int, "chain_head": hex|null}
LOG = DATA_DIR / "log.ndjson"     # one JSON line per draw (the public transcript)

MAX_COUNT = 64
DEFAULT_COUNT = 6
RANGE = 100                       # numbers are in 1..100

NONCE_BYTES = 32
NONCE_MAX_CHARS = 64

_ephemeral = {"next_draw_id": 1, "chain_head": None}  # fallback when no mount
LOCK = threading.Lock()


# --- nonce: same discipline as the platform's proof-bundle nonce ------------
# Exactly one `nonce` param, padding-tolerant base64url of exactly 32 bytes.

def parse_nonce(query: str) -> str | None:
    values = []
    for field in query.split("&"):
        if "=" in field:
            key, value = field.split("=", 1)
            if key == "nonce":
                values.append(unquote(value))
    if len(values) != 1:
        return None
    raw = values[0]
    if not raw or len(raw) > NONCE_MAX_CHARS:
        return None
    stripped = raw.rstrip("=")
    if not stripped or "=" in stripped:  # mid-string padding is invalid
        return None
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except Exception:
        return None
    return raw if len(decoded) == NONCE_BYTES else None


def parse_count(query: str) -> int | None:
    values = [unquote(v) for k, v in (f.split("=", 1) for f in query.split("&") if "=" in f) if k == "count"]
    if len(values) > 1:
        return None
    if not values or values[0] == "":
        return DEFAULT_COUNT
    try:
        n = int(values[0])
    except ValueError:
        return None
    return n if 1 <= n <= MAX_COUNT else None


# --- derivation: public, deterministic, recomputable from the transcript ---

def derive_numbers(transcript: str, count: int) -> list[int]:
    """numbers[i] = 1 + int64_be(sha256(sha256(transcript) || str(i))[:8]) % 100.

    The [:8]-then-modulo keeps bias below 2^-60 — documented, not hidden.
    Same formula ships in the page JS so the browser recomputes it locally.
    """
    h0 = hashlib.sha256(transcript.encode()).digest()
    return [
        1 + int.from_bytes(hashlib.sha256(h0 + str(i).encode()).digest()[:8], "big") % RANGE
        for i in range(count)
    ]


# --- draw log: persistent hash chain when /app/data is a mount -------------

def _load_state(data_dir: Path) -> dict:
    try:
        s = json.loads((data_dir / "state.json").read_text())
        return {"next_draw_id": int(s["next_draw_id"]), "chain_head": s.get("chain_head")}
    except Exception:
        return {"next_draw_id": 1, "chain_head": None}


def _persistent(data_dir: Path) -> bool:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".writable"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def record_draw(nonce: str, count: int, data_dir: Path = DATA_DIR) -> dict:
    """One draw: entropy → transcript → numbers → chained log entry.

    Persistent mode survives restarts (draw_id monotonic, chain continuous);
    ephemeral mode (no writable mount — e.g. `python3 server.py` locally)
    is honest about it in the response and on the page.
    """
    with LOCK:
        persistent = _persistent(data_dir)
        state = _load_state(data_dir) if persistent else dict(_ephemeral)
        draw_id = state["next_draw_id"]
        entropy = os.urandom(32).hex()
        transcript = f"{draw_id}|{nonce}|{entropy}"
        numbers = derive_numbers(transcript, count)
        entry_hash = hashlib.sha256(
            f"{state['chain_head'] or ''}|{transcript}".encode()
        ).hexdigest()
        entry = {
            "draw_id": draw_id, "nonce": nonce, "entropy": entropy,
            "numbers": numbers, "prev": state["chain_head"], "hash": entry_hash,
            "ts": int(time.time()),
        }
        state = {"next_draw_id": draw_id + 1, "chain_head": entry_hash}
        if persistent:
            with (data_dir / "log.ndjson").open("a") as f:
                f.write(json.dumps(entry) + "\n")
            (data_dir / "state.json").write_text(json.dumps(state))
        else:
            _ephemeral.clear()
            _ephemeral.update(state)
        return {
            "server": "attested-rng", "draw_id": draw_id, "nonce": nonce,
            "count": count, "numbers": numbers, "transcript": transcript,
            "derivation": "n[i] = 1 + int64_be(sha256(sha256(transcript) || str(i))[:8]) % 100",
            "chain_head": entry_hash, "prev": entry["prev"],
            "mode": "persistent" if persistent else "ephemeral",
            "generated_at": entry["ts"],
        }


def chain_stats(data_dir: Path = DATA_DIR) -> dict:
    """Head stats for the page: draw count + chain head, honest when ephemeral."""
    persistent = _persistent(data_dir)
    if not persistent:
        # head still links the in-memory chain (verifiable from responses);
        # `mode` carries the "won't survive a restart" caveat.
        return {"mode": "ephemeral", "draws": _ephemeral["next_draw_id"] - 1,
                "chain_head": _ephemeral["chain_head"]}
    state = _load_state(data_dir)
    draws = state["next_draw_id"] - 1
    return {"mode": "persistent", "draws": draws, "chain_head": state["chain_head"]}


def tail_log(limit: int, data_dir: Path = DATA_DIR) -> list[dict]:
    try:
        lines = (data_dir / "log.ndjson").read_text().splitlines()
        return [json.loads(l) for l in lines[-limit:]]
    except Exception:
        return []


# --- sidecar: claims from the in-guest attestation proxy -------------------

def fetch(path: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(f"{PROXY}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # one net for all failures; demo only.
        return {"_error": f"{type(e).__name__}: {e}"}


class H(BaseHTTPRequestHandler):
    server_version = "attested-rng"

    def do_GET(self):
        parts = urlsplit(self.path)
        path, query = parts.path, parts.query
        if path == "/health":
            self._send(200, b"ok", "text/plain")
        elif path == "/draw":
            nonce = parse_nonce(query)
            count = parse_count(query)
            if nonce is None:
                self._send_json(400, {"error": "nonce_invalid"})
            elif count is None:
                self._send_json(400, {"error": "count_invalid"})
            else:
                self._send_json(200, record_draw(nonce, count))
        elif path == "/log":
            self._send_json(200, {"draws": tail_log(20)})
        elif path == "/":
            payload = {
                "attestation_info": fetch("/v1/attestation/info"),
                "ownership_status": fetch("/status"),
                "chain": chain_stats(),
                "range": RANGE, "max_count": MAX_COUNT, "default_count": DEFAULT_COUNT,
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

    def _send_json(self, code: int, payload: dict) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        body = json.dumps(payload).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # enclava relays logs encrypted; keep stderr quiet.
        pass


if __name__ == "__main__":
    mode = "persistent" if _persistent(DATA_DIR) else "ephemeral"
    print(f"attested-rng listening on 0.0.0.0:{PORT} (proxy={PROXY}, data={DATA_DIR}, {mode})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
