#!/usr/bin/env bash
# DataWatch — VPS-side deploy, run ON the VPS by the deploy workflow
# (.github/workflows/deploy.yml, runs-on: slate-vps). Never run from the
# dev VM: direct SSH from the dev VM to the VPS is blocked by the egress
# proxy, and this script assumes it is already on the box.
#
# Usage: deploy-remote.sh <expected-sha>
#
# Steps, each failing loudly (set -euo pipefail; a failure aborts the run
# and the workflow reports it — never silently serve a broken deploy):
#   0. Preflight: docker + compose available, /srv/datawatch writable,
#      RAM gate (>= 512 MB free — DataWatch is a small Flask app, no
#      models), host port 127.0.0.1:5002 free of FOREIGN listeners.
#   0b. The checkout must be exactly the commit being deployed.
#   1. Ensure DATAWATCH_SECRET exists in /srv/datawatch/.env (generated
#      on first install, persisted in the .env, never printed).
#   2. docker compose build (from /srv/datawatch/repo) && up -d.
#   3. Poll /healthz on 127.0.0.1:5002 for up to 5 minutes.
#   4. End-to-end smoke: deploy/smoke-test.py (sign up, create the
#      orders dataset, run the dirty sample, expect 98.7/100).
#   5. Nightly backup cron (user crontab) if absent.
#   6. Disk guard: refuse to finish if / drops below 5 GB free.
#
# Never touches SLATE's files, routes, or env, or the agent platform's.
# The Caddy route (datawatch.40.160.149.104.nip.io) is NOT touched here
# — one-time manual append, see deploy/caddy-snippet and
# deploy/CHECKLIST.md. Until the route exists the app is verified over
# the loopback port only, not via a public URL.

set -euo pipefail

APP_DIR="/srv/datawatch"
REPO_DIR="$APP_DIR/repo"
COMPOSE_FILE="$APP_DIR/docker-compose.yml"
ENV_FILE="$APP_DIR/.env"
EXPECTED_SHA="${1:-}"

log() { echo "[datawatch-deploy $(date -u +%FT%TZ)] $*"; }

[ -n "$EXPECTED_SHA" ] || { log "ERROR: expected SHA not given"; exit 1; }
[ -f "$COMPOSE_FILE" ] || { log "ERROR: $COMPOSE_FILE not found (did the workflow copy it?)"; exit 1; }
[ -d "$REPO_DIR/.git" ] || { log "ERROR: $REPO_DIR is not a git checkout"; exit 1; }
for cmd in docker git python3 openssl; do
  command -v "$cmd" >/dev/null || { log "ERROR: $cmd not found on the VPS"; exit 1; }
done

# --- 0. Preflight: docker works, /srv writable, RAM gate --------------------
log "deploying as $(whoami) on $(hostname)"
docker info >/dev/null 2>&1 || {
  log "ERROR: 'docker info' failed for $(whoami) — is this user in the docker group?"
  exit 1
}
mkdir -p "$APP_DIR" || {
  log "ERROR: cannot create $APP_DIR — the runner user cannot write /srv"
  exit 1
}
FREE_MB="$(free -m | awk '/^Mem:/ {print $7}')"
log "free RAM: ${FREE_MB} MB (need >= 512 MB for DataWatch + headroom)"
if [ "$FREE_MB" -lt 512 ]; then
  log "ERROR: not enough free RAM (free=${FREE_MB} MB, need 512 MB)."
  docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null || true
  exit 1
fi
log "disk free on /: $(df -h / --output=avail | tail -1)"

# --- 0b. The checkout must be exactly the commit being deployed -------------
ACTUAL_SHA="$(git -C "$REPO_DIR" rev-parse HEAD)"
[ "$ACTUAL_SHA" = "$EXPECTED_SHA" ] || {
  log "ERROR: checkout is $ACTUAL_SHA, expected $EXPECTED_SHA (did the pull race?)"
  exit 1
}
log "deploying $ACTUAL_SHA"

# --- 1. DATAWATCH_SECRET (Flask sessions) ------------------------------------
if [ -f "$ENV_FILE" ] && grep -q '^DATAWATCH_SECRET=' "$ENV_FILE"; then
  log "DATAWATCH_SECRET already present (kept, never printed)"
else
  log "generating DATAWATCH_SECRET (first install; persisted in the .env)"
  umask 077
  { echo "DATAWATCH_SECRET=$(openssl rand -hex 32)"; } >> "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

# --- 2. Build + start --------------------------------------------------------
log "building image on the VPS (pip install; no models to download)"
docker compose -f "$COMPOSE_FILE" build
log "starting container"
# --- 2b. Host port must be free of FOREIGN listeners -------------------------
# On a redeploy the running datawatch container legitimately holds 5002
# (its docker-proxy listener); compose recreates it and re-binds.
# Only a FOREIGN listener is an error.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'datawatch'; then
  log "datawatch container is running (redeploy) — it holds 127.0.0.1:5002, compose will re-bind it"
elif command -v ss >/dev/null; then
  if ss -ltn 2>/dev/null | grep -q '127.0.0.1:5002'; then
    log "ERROR: 127.0.0.1:5002 is already in use:"
    ss -ltnp 2>/dev/null | grep '5002' || true
    exit 1
  fi
  log "host port 127.0.0.1:5002 is free"
else
  log "WARNING: ss not found, skipping host-port check"
fi
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

# --- 3. Health poll (up to 5 min; no model downloads, should be seconds) ----
log "polling /healthz (up to 5 min)"
HEALTH_OK=0
for i in $(seq 1 60); do
  if curl -fsS -m 5 http://127.0.0.1:5002/healthz >/dev/null 2>&1; then
    HEALTH_OK=1
    log "healthz ok after ~$((i * 5))s"
    break
  fi
  sleep 5
done
if [ "$HEALTH_OK" -ne 1 ]; then
  log "ERROR: /healthz never came up in 5 min — container logs:"
  docker compose -f "$COMPOSE_FILE" logs --tail 50 datawatch || true
  exit 1
fi

# --- 4. End-to-end smoke ------------------------------------------------------
log "running smoke test (sign up + dataset + dirty run 98.7/100)"
python3 "$REPO_DIR/deploy/smoke-test.py"

# --- 5. Nightly backup cron (user crontab, idempotent) -----------------------
# A missing crontab is a WARNING, not a deploy failure: the app has no
# real users at first deploy, and the CHECKLIST makes a verified backup
# run a condition of announcing the public URL. Never fake it.
if command -v crontab >/dev/null; then
  CRON_LINE="30 4 * * * /bin/bash $REPO_DIR/deploy/backup.sh >> $APP_DIR/backup.log 2>&1"
  if crontab -l 2>/dev/null | grep -q "datawatch-backup\|deploy/backup.sh"; then
    log "backup cron already installed"
  else
    log "installing nightly backup cron (04:30 UTC)"
    # NOTE (Nov 13, 2026): the || true is load-bearing. Under set -euo pipefail, a missing crontab makes crontab -l exit 1, which aborts this subshell before echo runs; crontab - then gets empty stdin and the deploy fails. The first deploy failed exactly this way (empty crontab left behind).
    (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
  fi
else
  log "WARNING: crontab not found — nightly backup cron NOT installed (see deploy/CHECKLIST.md §6)"
  
fi

# --- 6. Disk guard -------------------------------------------------------------
FREE_GB="$(df -BG / --output=avail | tail -1 | tr -dc '0-9')"
if [ "$FREE_GB" -lt 5 ]; then
  log "ERROR: disk free on / is ${FREE_GB} GB (< 5 GB) after deploy — investigate before going live"
  exit 1
fi

log "deploy ok: $ACTUAL_SHA live on localhost:5002 (public URL needs the Caddy route — see deploy/CHECKLIST.md)"
