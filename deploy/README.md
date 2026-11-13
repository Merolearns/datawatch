# deploy/ — deploy files for DataWatch

Owner: flagship-builder cron. Nothing outside `deploy/`, `.github/`,
and the app files is touched from here.

## Files

| File | What it does |
|---|---|
| `.github/workflows/ci.yml` | CI on pull requests and pushes to `main`: Python 3.12, `pip install -r requirements.txt`, `ruff check --select F .` (pinned 0.16.10), `pytest -q`. No secrets. Red fails the build and blocks deploy. |
| `.github/workflows/deploy.yml` | Manual deploy (workflow_dispatch only). Runs ON the VPS via the `slate-vps` self-hosted runner — no SSH key, no repo secrets. Syncs the repo to `/srv/datawatch/repo` at the exact SHA, copies the compose file, then runs `deploy-remote.sh`. |
| `deploy/deploy-remote.sh` | The VPS-side deploy script: preflight (docker works, `/srv/datawatch` writable, RAM gate >= 512 MB, host port 5002 free of foreign listeners), SHA check, `DATAWATCH_SECRET` generation on first install, `docker compose build` + `up -d`, /healthz poll up to 5 min, smoke test, backup cron install, disk guard. Never touches SLATE's or the agent platform's files. |
| `deploy/smoke-test.py` | End-to-end smoke run on the VPS: /healthz, sign up, create the orders dataset from the prefilled builder form, run the dirty sample (expects 98.7/100, Fail), runs API check. Stdlib only; never prints a secret. Also runs locally: `python3 deploy/smoke-test.py http://127.0.0.1:5001`. |
| `deploy/docker-compose.yml` | The VPS Compose project (`/srv/datawatch/docker-compose.yml`). One service, built ON THE VPS from the repo checkout, gunicorn on `:5001` inside the container, host port 5002 bound to loopback only, named volume `datawatch-data:/data`, `mem_limit: 512m`, `cpus: 0.5`, env file `/srv/datawatch/.env`, `/healthz` healthcheck. |
| `deploy/caddy-snippet` | Caddy route for `datawatch.40.160.149.104.nip.io`, reverse-proxying to the container (`datawatch:5001`), kept separate from SLATE's routes. One-time manual append to the VPS Caddyfile + `caddy validate` before reload — a bad Caddyfile takes SLATE down too. |
| `deploy/backup.sh` | Nightly off-container backup (SQLite snapshot -> `/srv/datawatch/backups/`, integrity-checked, pruned to 7 days). Runs on the VPS via user crontab (04:30 UTC). Fails loudly in its log, never a silent fake backup. |
| `deploy/CHECKLIST.md` | The first-deploy checklist: the browser steps (workflow files + trigger), what the deploy does, the Caddy step, going live, backup status. |

## Deploy flow (manual trigger)

1. Parent adds the two workflow files in the repo web UI (the PATs lack
   the Workflows permission — see CHECKLIST.md) — one commit on main.
2. Parent runs the `deploy` workflow (Actions tab -> Run workflow).
3. The workflow runs on the `slate-vps` runner (already on the box):
   repo sync at the exact SHA -> compose copied ->
   `deploy-remote.sh`: RAM gate, build on the VPS, up -d, /healthz poll,
   end-to-end smoke, backup cron, disk guard.
4. Caddy route is a one-time manual append (see `caddy-snippet`); until
   it exists the app is verified over the loopback port only, not via a
   public URL.

## Secrets

None. DataWatch needs no repo secrets: accounts are self-signup, there
are no seeded demo logins and no LLM keys. `DATAWATCH_SECRET` (Flask
sessions) is generated on first install by the deploy script and
persisted in the VPS `.env`. The `.env` file is not committed anywhere.

## Backups — MANDATORY before real users

Nightly snapshots are mandatory before any real user touches the app.
The deploy installs the cron (04:30 UTC, user crontab); verify one
snapshot exists and passes `PRAGMA integrity_check` before announcing
the public URL. The restore procedure (snapshot -> scratch copy of the
`datawatch-data` volume -> scratch container -> /healthz + login) is in
`deploy/backup.sh`. Do not assume a backup works until it is verified
AND a restore has been tested once.
