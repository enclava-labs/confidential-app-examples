# hello-tee

The smallest Enclava app: one page, `/health`, and the in-pod attestation
sidecar's verified claims rendered server-side. Use it to walk the whole loop —
build → sign → `create` → `deploy` → claim → serve — in about ten minutes.

**What it teaches:** the deploy lifecycle, password-mode first claim,
`enclava status`, and the claims-vs-proof distinction (the page *claims* it runs
in a TEE; `enclava verify` is how anyone checks). **What it deliberately
omits:** persistent storage (see `attested-rng` for that), client-facing
attestation patterns.

## Run locally

```bash
python3 server.py     # http://localhost:8080 — attestation cards show "unavailable" (no sidecar)
python3 -m unittest discover -p 'test_*.py'
```

## Deploy it

Two paths. Both need the CLI (`docs.enclava.dev` quickstart) and an Enclava account.

### Path A — deploy the pre-built image (fastest)

CI in this repo builds, signs, and publishes `ghcr.io/enclava-labs/confidential-app-examples/hello-tee`.
Deploy it into **your** org — the image is public and its signature is verified
against the GitHub Actions identity below:

```bash
enclava login                                  # approve in the browser
enclava key setup --backup-out org-keys.json   # first deploy in a fresh org only
cd hello-tee
enclava create \
  --image ghcr.io/enclava-labs/confidential-app-examples/hello-tee:latest \
  --signer-subject https://github.com/enclava-labs/confidential-app-examples/.github/workflows/hello-tee.yml@refs/heads/main
enclava deploy \
  --image ghcr.io/enclava-labs/confidential-app-examples/hello-tee@sha256:<digest-from-CI> \
  --storage-password-file ~/hello-tee.password
enclava key backup --out ~/hello-tee-recovery.json   # do this now — see below
enclava status
```

The `<digest>` is printed by the [build workflow's](../.github/workflows/hello-tee.yml)
job summary on every run. `deploy` with `--storage-password-file` claims ownership
unattended; `status` turning `running` / `TEE: unlocked` is your success signal.

### Path B — build your own

Fork/clone this repo, push to your GitHub, and the same workflow runs in *your*
repo (rename it or keep the path — the signer subject is the workflow file path
inside your repo). Then `create`/`deploy` with **your** image + signer subject:

```bash
enclava create --image ghcr.io/<you>/confidential-app-examples/hello-tee:latest \
  --signer-subject https://github.com/<you>/confidential-app-examples/.github/workflows/hello-tee.yml@refs/heads/main
enclava deploy --image ghcr.io/<you>/confidential-app-examples/hello-tee@sha256:<digest> \
  --storage-password-file ~/hello-tee.password
```

Scaffolding your own app from scratch is `enclava init` — it writes this
`enclava.toml` shape and a starter workflow for you.

## The one thing you must not skip

The first deploy creates **owner recovery material**: the claim's one-time
mnemonic lands in the CLI's protected keystore, and `enclava key backup --out
<file>` is how it leaves the machine. Without password *or* backup, the app's
data can never be opened again. Keep the backup outside the repo.

## Troubleshooting

`enclava status` names the failure (`tee_error`). The classics:
`Read-only file system (os error 30)` → a `storage.paths` entry isn't a
`VOLUME` in the image (hello-tee has none — did you add one?); `drifted` after
a failed deploy → `enclava destroy --app hello-tee --force`, then `create` +
`deploy` fresh. Full list: the manual-OCI guide in the developer docs.
