# attested-rng

Randomness you can pin to a confidential VM. A tiny draw service where the
**client supplies a 32-byte nonce** and every draw is bound to it — the same
freshness discipline the platform's own proof endpoint uses, applied at app
level.

**What it teaches:** the full custom-image journey *plus* the two patterns that
make a TEE worth having in user-facing code —

1. **Verifiable derivation.** Numbers come from a public formula over a
   transcript (`draw_id | your-nonce | server-entropy`); anyone can recompute
   them (the page does it in your browser with `crypto.subtle` — no trust in
   the server needed for the arithmetic).
2. **Attested environment.** *Where* the draw happened is checkable out-of-band
   with `enclava verify` against a trust policy **you** hold — see
   [Verify the service](#verify-the-service-where-those-numbers-came-from).

It also demonstrates persistent encrypted storage: the draw log lives on a
LUKS volume inside the guest, chained by hash, unreadable to the platform.

## The draw contract

```
GET /draw?nonce=<b64url-32B>&count=<1..64>

n[i] = 1 + int64_be( sha256( sha256(transcript) ‖ str(i) )[:8] ) mod 100
transcript = "<draw_id>|<nonce>|<entropy-hex>"
```

- **Your nonce** (browser-generated or your own) is folded into the transcript,
  so a response is bound to a value only you know: replays, prefabrication and
  cross-user reuse all fail to recompute.
- **`draw_id` + hash chain**: each draw's `hash = sha256(prev ‖ transcript)`
  chains it to the previous one; the head is on the page and in every response.
- Responses are `Cache-Control: no-store`; the nonce rules are exactly the
  platform's (single param, base64url, exactly 32 bytes, padding-tolerant).

## Run locally

```bash
python3 server.py    # http://localhost:8080 — draws work, TEE cards show
                     # "unreachable", draw log runs in ephemeral mode (honest about it)
python3 -m unittest discover -p 'test_*.py'
```

## Deploy it

Same two paths as `hello-tee` (see its README for the account/key-setup walk-through); from this directory, with this app's names:

```bash
# Path A — our pre-built, signed, public image into YOUR org:
enclava create \
  --image ghcr.io/enclava-labs/confidential-app-examples/attested-rng:latest \
  --signer-subject https://github.com/enclava-labs/confidential-app-examples/.github/workflows/attested-rng.yml@refs/heads/main
enclava deploy \
  --image ghcr.io/enclava-labs/confidential-app-examples/attested-rng@sha256:<digest-from-CI> \
  --storage-password-file ~/attested-rng.password
enclava key backup --out ~/attested-rng-recovery.json   # owner recovery material — keep it out of the repo
```

Path B — fork, push, let the same workflow build+sign in your repo, and use
*your* image + signer subject. The deploy claims ownership and boots the app
with `/app/data` on an encrypted volume.

## Verify the service (where those numbers came from)

The page and its JSON are **claims** — anything can serve them. Proof is
independent, and takes about ten minutes, once:

```bash
enclava describe https://<your-app-origin> --policy-skeleton draft.json
```

`describe` observes the live origin and emits a TOFU skeleton. **Do not trust
it wholesale** — the target supplied every value. Edit `draft.json`, and for
each pin ask "did I learn this independently of the target?":

| Skeleton value | How to check it independently |
| --- | --- |
| `amd.trusted_ark_sha256` | Compare with AMD's published SEV-SNP root certificate. |
| `amd.allowed_measurements`, `minimum_tcb` | Expected for the platform profile you're told the app runs on — a platform operator or release notes answer this, not the target. |
| image digest + signer subject | The digest CI printed for this app's build; the subject is the workflow path in **this** repo (`…/.github/workflows/attested-rng.yml@refs/heads/main`). |
| `origins` | The URL you typed, from your own records. |
| `trusted_policy_signing_pubkeys`, org keyring | Platform authority material — inherit only from a channel you already trust. |
| Sigstore values (`REPLACE_WITH_…`) | Fulcio roots / Rekor key from sigstore.dev; not inferable by `describe` at all. |

Then verify, and keep the policy in **your** repo (this repo keeping its own
copy is demo convenience — a third party's copy is the real thing):

```bash
enclava verify https://<your-app-origin> --policy trust-policy.json --json
enclava verify https://<your-app-origin> --policy trust-policy.json --save-bundle evidence.bin
```

Fresh nonce per run (replays fail), every check must pass, fail-closed. The
saved bundle is historical evidence of *this* verification. This repo ships a
[verify workflow](../.github/workflows/verify-attested-rng.yml) you can
dispatch against any origin + policy — after you deploy your own instance,
commit your policy and turn the schedule on.

**CLI draw check without a browser:**

```bash
python3 - <<'EOF'
import base64, hashlib, json, os, urllib.request, urllib.parse
nonce = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
u = "https://<your-app-origin>/draw?" + urllib.parse.urlencode({"nonce": nonce, "count": 6})
d = json.load(urllib.request.urlopen(u))
h0 = hashlib.sha256(d["transcript"].encode()).digest()
recomputed = [1 + int.from_bytes(hashlib.sha256(h0 + str(i).encode()).digest()[:8], "big") % 100
              for i in range(d["count"])]
print("numbers:", d["numbers"], "recompute:", "MATCH" if recomputed == d["numbers"] else "MISMATCH")
EOF
```

## Honest limits (read this bit)

Attested: the draw ran inside a hardware-isolated SEV-SNP guest, under the
image and signed policy the verifier pinned, with memory the host cannot read;
your nonce makes each response fresh. **Not** attested: information-theoretic
randomness — the entropy source is the guest kernel's CSPRNG, and a verifier is
trusting the pinned code (this repo — read it, it's small). The draw-log chain
gives continuity *within* the log, but the platform does not by itself prevent
volume rollback; a high-stakes deployment would add external anchoring (publish
chain heads elsewhere). For the same reason this is a teaching example, not a
lottery infrastructure.

## API

| Route | What |
| --- | --- |
| `GET /health` | liveness, bare 200 |
| `GET /draw?nonce=…&count=…` | one draw (JSON, `no-store`); `400 nonce_invalid` / `400 count_invalid` |
| `GET /log` | last 20 draws from the chained log |
| `GET /` | the page (claims: sidecar info, chain stats) |

Troubleshooting is shared with `hello-tee` — `enclava status` names the
failure; the `VOLUME` rule applies to `/app/data` here.
