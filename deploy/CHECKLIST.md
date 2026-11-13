# deploy/CHECKLIST.md — first live deploy (parent steps + what happens when)

Status: PREP DONE Nov 13, 2026 (flagship-builder cron) — Dockerfile,
compose, deploy script, smoke test, Caddy snippet, and both workflow
files are written and pushed to `main` except the workflow files
themselves (see §1). NOT YET DEPLOYED: the one-time VPS prep (§0),
the workflow trigger (§3), and the Caddy route (§4) are still to do.

## 0. One-time VPS prep (SLATE side chat, vps-ops — DONE Nov 13, 2026)
The probe (vps-ops run 43399676983, Nov 13) proved the runner route
needs this first: the `slate-vps` runner is online and can run docker,
but `actions-runner` cannot write to `/srv` (root-owned) and has no
passwordless sudo. Done Nov 13 ~01:28 PST via vps-ops-root.yml (run
43401538532, success; verified by run 43401721419, success):
`mkdir -p /srv/datawatch && chown -R actions-runner:actions-runner /srv/datawatch`
— `drwxr-xr-x 2 actions-runner actions-runner` (mode 755), empty.
(RAM at probe time: 758 MB free / 1,660 MB available; disk 43 GB free;
port 5002 free. DataWatch needs ~100 MB.)

## 1. Parent action A — land the workflow files (browser, GitHub web UI)
The vault PAT and the gh CLI PAT both lack the **Workflows** permission,
so no workflow file can be pushed by API or git (verified Nov 8, 2026
on the agent-platform repo: a tree containing any `.github/workflows/`
path is refused with 403, while a tree without one succeeds with 201;
re-verified Nov 13, 2026: the PAT is also 403 on workflow dispatch and
on the repo secrets public-key endpoint). Add these two files in the
repo web UI ("Add file" -> "Create new file", one commit on main).
Copy the contents from the local files of the same names:
- `.github/workflows/ci.yml` — pytest + ruff 0.16.10 on PRs and pushes to main.
- `.github/workflows/deploy.yml` — the deploy (manual trigger only).

Do not re-fight the API route. (The agent-platform repo hit the same
wall Nov 8 and the web-UI upload is the working path.)

## 2. Parent action B — repo secrets
NONE. DataWatch needs no repo secrets: accounts are self-signup, there
are no seeded demo logins and no LLM keys. `DATAWATCH_SECRET` is
generated on first install by the deploy script and persisted in the
VPS `.env`. If the self-hosted runner path (§3) turns out to be dead,
see the SSH fallback (§3b) — that route DOES need a `VPS_SSH_KEY`
secret.

## 3. Trigger the deploy (browser, Actions tab -> deploy -> Run workflow)
The workflow runs on the `slate-vps` self-hosted runner (already on
the box — no SSH), in order: repo sync at the exact SHA ->
compose copied -> `deploy-remote.sh`: RAM gate (>= 512 MB free, or
abort with numbers), build on the VPS, up -d, /healthz poll up to
5 min, end-to-end smoke (sign up, create the orders dataset, run the
dirty sample, expect 98.7/100 Fail), backup cron installed, disk guard
(>= 5 GB free or abort). Any failure aborts the run with the numbers
in the log.

## 3b. Fallback — SSH deploy (only if the slate-vps run queues forever)
If the run sits queued, the runner is not visible to this repo (it may
be repo-scoped to SLATE). Do not debug the runner from here — switch
the workflow to the proven SSH route instead:
1. Set the repo secret `VPS_SSH_KEY` — the SSH private key for
   root@40.160.149.104 (the SLATE deploy key,
   `~/.ssh/id_slate_deploy` on the dev VM; the value never leaves the
   browser form / GitHub secrets — same key already set on the
   agent-platform repo).
2. In the repo web UI, change `deploy.yml`: `runs-on: ubuntu-latest`,
   add the SSH setup + `.env` write steps from the agent-platform
   `deploy.yml` (same mechanism as SLATE's deploy-workers.yml), and
   replace the sync step with the `ssh vps ...` sync. Re-run.
The `deploy-remote.sh` script itself needs no changes — it already
runs on the box either way.

## 4. One-time Caddy route (parent / SLATE side chat, vps-ops)
Append `deploy/caddy-snippet` (host `datawatch.40.160.149.104.nip.io`,
reverse-proxy to `datawatch:5001`) to the VPS Caddyfile BELOW SLATE's
and the agent-platform blocks, then `caddy validate` and only reload
if validation exits 0. A bad Caddyfile takes SLATE down too — never
reload a file that failed validation. Before the append, connect the
Caddy container to the new network:
`docker network connect datawatch_default slate-caddy` (localhost:5002
does not work from inside the Caddy container). Until the route exists
the app is verified over the loopback port only, not via a public URL.
After the route is live: verify firsthand — /healthz, the login page,
and a dirty-sample run through https://datawatch.40.160.149.104.nip.io/
— and screenshot the dashboard as `images/07-live-deploy.png`
(headless Chromium).

## 5. After the first deploy — going live
The first deploy has no seeded accounts: the first user signs up on
the live site. Verify afterwards: sign up works, the dirty sample
still scores 98.7/100, scheduled monitoring runs (a schedule fires
within its interval), and the Alerts page shows the unread count.

## 6. Backups — status
`deploy/backup.sh` (nightly 04:30 UTC user crontab, installed by the
deploy) snapshots the SQLite DB to `/srv/datawatch/backups/` and
verifies each snapshot with `PRAGMA integrity_check`. Before the
public URL is announced: confirm one snapshot exists, passes the
integrity check, and restores (snapshot -> scratch copy of the
`datawatch-data` volume -> scratch container -> /healthz + login).
Until a restore has been tested once, the backup is unverified — do
not announce the URL as production-ready.

## 7. What is already verified (firsthand, Nov 13, 2026)
- Full suite: 73 tests Phase 2 (49 Phase 1 + 24 new) + 3 new
  healthz/env tests — NOT yet run green in this environment (the dev
  VM's exec sandbox currently blocks all file-content writes, so
  pytest cannot run locally; CI on the first push is the gate — do not
  deploy off a red main).
- Ruff F-gate: clean (`ruff check --select F .`, one pre-existing
  unused import in tests/test_store.py fixed in this prep).
- Sample result (unchanged): clean CSV 100.0/100 (780 checks, 0
  errors); dirty CSV 98.7/100 (775 checks, 10 errors).
- Local end-to-end + Docker build: NOT yet verified (same sandbox
  block); the VPS deploy build + smoke test is the verification.
