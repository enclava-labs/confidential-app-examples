# confidential-app-examples

Small, complete, deployable applications for
[Enclava](https://enclava.dev) — confidential containers on AMD SEV-SNP.
Each one is a full loop: **build → sign → `create` → `deploy` → claim → serve →
verify** — small enough to read in one sitting, honest enough to learn from.

## The examples

| App | Teaches | Storage |
| --- | --- | --- |
| [`hello-tee/`](hello-tee/) | the whole deploy lifecycle in ~10 minutes; claims vs proof | none (`paths = []`) |
| [`attested-rng/`](attested-rng/) | client-nonce freshness, verifiable derivation, `enclava verify` + trust-policy authoring | encrypted draw log |

Start with `hello-tee`, then `attested-rng`. Each app's README carries two
deploy paths: **A** — deploy *our* pre-built, signed, public image into your
org (no GitHub account needed); **B** — fork and build your own under your
signer identity. Both end with a running app you can verify.

## House rules (what makes an example good here)

1. **Claims vs proof, always.** A page inside the TEE can say anything; every
   example points at `enclava verify` + a policy *the verifier holds* as the
   actual evidence path, and says so on the page itself.
2. **Honest limits in the open.** What a TEE proves (environment, code
   identity, host-unreadable memory, freshness) and what it never will
   (app correctness, information-theoretic randomness, rollback resistance
   without extra anchoring) — stated in each README, not footnoted away.
3. **Stdlib-only, one file per app.** No framework, no dependencies to audit:
   if you can read the app, you can decide to trust the app — which is the
   entire point of pinning it in a trust policy.
4. **Same nonce discipline as the platform.** Where an example takes a
   freshness input, it follows the platform's rules (single param, base64url,
   exactly 32 bytes) so the habit transfers.
5. **CI mirrors `enclava init`.** Build/sign workflows keep the scaffolded
   shape (cosign 3.x keyless + provenance + SBOM) so what you see here is what
   the CLI generates for you.

## Repository map

```
hello-tee/            server.py, index.html, Dockerfile, enclava.toml, tests, README
attested-rng/         server.py, index.html, Dockerfile, enclava.toml, tests, README
.github/workflows/    per-app build & sign (init-shaped), unit tests, rng verify
```

Docs: <https://docs.enclava.dev> (quickstart, manual OCI deploy, threat model).
Agent skills for operating Enclava: [`enclava-dev-skills`](https://github.com/enclava-labs/enclava-dev-skills).

## Adding an example

Keep the rules above; the shortest checklist that has worked: copy
`hello-tee/`, change what the app teaches (one thing per example), keep the
`test_server.py` contract-test habit, mirror the build/sign workflow, and write
the honest-limits section *last*, when you can't fool yourself anymore. MIT
licensed — copy freely.
