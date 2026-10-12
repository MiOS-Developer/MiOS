#!/usr/bin/env bash
# AI-hint: bash Initialize the deployed root `/` as a git working tree of the local AI-related: /usr/lib/mios/paths.sh, /usr/libexec/mios/git-root-init.sh...
# AI-doc: usr/share/doc/mios/manual/mios.md

set -euo pipefail

source /usr/lib/mios/paths.sh 2>/dev/null || true
: "${MIOS_VAR_DIR:=/var/lib/mios}"

_log() {
    logger -t mios-git-root-init "$*" 2>/dev/null || true
    echo "[git-root-init] $*" >&2
}

if [[ -d /.git ]]; then
    _log "/.git already present; nothing to do"
    exit 0
fi

MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-mios}"
if [[ -r /etc/mios/install.env ]]; then
    _mios_had_u=0; case "$-" in *u*) _mios_had_u=1;; esac
    set +u; set -a; source /etc/mios/install.env 2>/dev/null || true; set +a
    [ "$_mios_had_u" = 1 ] && set -u
fi
MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-mios}"

FORGE_URL="${MIOS_URLS_FORGE:-http://localhost:3000}"
REPO_URL="${FORGE_URL}/${MIOS_IDENTITY_USERNAME}/mios.git"

_log "waiting for Forgejo at ${FORGE_URL} ..."
for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 -o /dev/null "${FORGE_URL}/api/v1/version" 2>/dev/null; then
        _log "Forgejo reachable"
        break
    fi
    sleep 2
done
if ! curl -fsS --max-time 2 -o /dev/null "${FORGE_URL}/api/v1/version" 2>/dev/null; then
    _log "Forgejo never came up; skipping git-root-init"
    exit 0
fi

if ! curl -fsS --max-time 2 -o /dev/null "${REPO_URL}/info/refs?service=git-upload-pack" 2>/dev/null; then
    _log "${REPO_URL} not yet present; create it on Forgejo first, then re-run"
    exit 0
fi

_log "git init / + remote add origin ${REPO_URL}"
git -C / init -b main
git -C / config core.fileMode false   # prevent perm-noise on read-only composefs
git -C / config core.autocrlf false
git -C / config user.email "${MIOS_IDENTITY_USERNAME}@$(hostname).local"
git -C / config user.name "${MIOS_IDENTITY_USERNAME}"
git -C / remote add origin "${REPO_URL}"

_log "fetching origin main ..."
if git -C / fetch --depth=1 origin main 2>&1 | logger -t mios-git-root-init; then
    git -C / reset --soft FETCH_HEAD
    _log "/.git initialized; HEAD = $(git -C / rev-parse HEAD)"
else
    _log "fetch failed; /.git left unset"
    exit 1
fi
