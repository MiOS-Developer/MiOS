#!/usr/bin/env bash
# AI-hint: bash First-boot admin-bootstrap for the mios-forge Quadlet (Forgejo).
# AI-doc: usr/share/doc/mios/manual/mios.md

set -euo pipefail

SENTINEL=/var/lib/mios/forge/.firstboot-done
ENV_FILE=/etc/mios/install.env
PASSWORD_FILE=/etc/mios/forge/admin-password

_log() {
    logger -t mios-forge-firstboot "$*" 2>/dev/null || true
    echo "[forge-firstboot] $*" >&2
}

if [[ -f "$SENTINEL" ]]; then
    _log "sentinel present, nothing to do"
    exit 0
fi

mkdir -p /srv/mios/forge 2>/dev/null || true
chown -R 1000:1000 /srv/mios/forge 2>/dev/null || true

if [[ -r "$ENV_FILE" ]]; then
    _mios_had_u=0; case "$-" in *u*) _mios_had_u=1;; esac
    set +u; set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE" 2>/dev/null || true
    set +a
    [ "$_mios_had_u" = 1 ] && set -u
fi

admin_user="${MIOS_FORGE_ADMIN_USER:-${MIOS_LINUX_USER:-${MIOS_IDENTITY_USERNAME:-mios}}}"
admin_host="${MIOS_IDENTITY_HOSTNAME:-mios}"
admin_email="${MIOS_FORGE_ADMIN_EMAIL:-${admin_user}@${admin_host}.local}"
# MIOS_FORGE_ADMIN_PASSWORD=__random__ to force a generated value.
admin_password="${MIOS_FORGE_ADMIN_PASSWORD:-${MIOS_IDENTITY_DEFAULT_PASSWORD:-mios}}"
if [[ "$admin_password" == "__random__" ]]; then
    admin_password=""
fi

_is_forge_up() {
    local p="$1"
    curl -sI "http://127.0.0.1:${p}/" 2>/dev/null | grep -q "HTTP/" && return 0
    curl -sI "http://localhost:${p}/" 2>/dev/null | grep -q "HTTP/" && return 0
    return 1
}

http_port="${MIOS_PORTS_FORGE_HTTP:-${MIOS_PORT_FORGE_HTTP:-8400}}"
deadline=$(( $(date +%s) + 300 ))
ready_port=""

while (( $(date +%s) < deadline )); do
    for check_port in "$http_port" 3000 8300; do
        if _is_forge_up "$check_port"; then
            ready_port="$check_port"
            _log "Forgejo is up on :${ready_port}"
            break 2
        fi
    done
    sleep 2
done

if [[ -z "$ready_port" ]]; then
    _log "DEGRADED: Forgejo did not become ready; exiting with error for systemd retry"
    exit 1
fi
http_port="$ready_port"

if [[ -z "$admin_password" ]]; then
    admin_password="$(openssl rand -base64 24 | tr -d '\n')"
    install -d -m 0750 -o root -g mios-forge /etc/mios/forge
    umask 077
    printf '%s\n' "$admin_password" > "$PASSWORD_FILE"
    chmod 0600 "$PASSWORD_FILE"
    chown root:root "$PASSWORD_FILE"
    _log "generated initial admin password; wrote $PASSWORD_FILE (mode 0600, root-only)"
fi

# MIOS_FORGE_ADMIN_PASSWORD) -- it IS the operator's intended
if podman exec -e HOME=/data/gitea mios-forge \
        forgejo --config /data/gitea/conf/app.ini admin user create \
        --admin --must-change-password=false \
        --username "$admin_user" \
        --email "$admin_email" \
        --password "$admin_password" 2>&1 | tee -a /var/log/mios/forge/firstboot.log; then
    _log "admin user '${admin_user}' (${admin_email}) created"
else
    rc=$?
    _log "admin create returned ${rc}; treating as 'already exists' (set MIOS_FORGE_FORCE_FIRSTBOOT=1 to override)"
    if [[ "${MIOS_FORGE_FORCE_FIRSTBOOT:-}" == "1" ]]; then
        exit "$rc"
    fi
fi

INITIAL_REPO_NAME="${MIOS_FORGE_INITIAL_REPO:-mios}"
_log "creating initial repo '${admin_user}/${INITIAL_REPO_NAME}' for the deployed root"
_repo_status=$(curl -sS -o /tmp/forge-repo-create.json -w '%{http_code}' \
    -u "${admin_user}:${admin_password}" \
    -H 'Content-Type: application/json' \
    -X POST "http://localhost:${http_port}/api/v1/user/repos" \
    -d "{\"name\":\"${INITIAL_REPO_NAME}\",\"description\":\"MiOS deployed root (live working tree of /).\",\"private\":false,\"auto_init\":false,\"default_branch\":\"main\"}" \
    || echo "000")
case "$_repo_status" in
    201) _log "initial repo created: http://localhost:${http_port}/${admin_user}/${INITIAL_REPO_NAME}" ;;
    409) _log "initial repo already exists -- skipping" ;;
    *)   _log "WARN: repo create returned HTTP ${_repo_status}; manual step needed:"
         _log "      curl -u ${admin_user}:<password> -X POST http://localhost:${http_port}/api/v1/user/repos -d '{\"name\":\"${INITIAL_REPO_NAME}\"}'" ;;
esac
rm -f /tmp/forge-repo-create.json

_forge_remote_url="http://${admin_user}:${admin_password}@localhost:${http_port}/${admin_user}/${INITIAL_REPO_NAME}.git"
if command -v git >/dev/null 2>&1; then
    git config --global --add safe.directory / 2>/dev/null || true
    if [[ ! -d /.git ]]; then
        if git -C / init -q -b main 2>/dev/null; then
            _log "initialised git working tree at / (whitelist /.gitignore -> only MiOS paths tracked)"
        else
            _log "WARN: 'git -C / init' failed -- self-host push loop not wired"
        fi
    fi
    if [[ -d /.git ]]; then
        _cur_origin=$(git -C / remote get-url origin 2>/dev/null || true)
        if [[ -n "$_cur_origin" && "$_cur_origin" != *"localhost:${http_port}"* ]]; then
            git -C / remote rename origin local-bare 2>/dev/null || true
        fi
        if git -C / remote get-url origin >/dev/null 2>&1; then
            git -C / remote set-url origin "$_forge_remote_url" 2>/dev/null || _log "WARN: 'git -C / remote set-url' failed (read-only / -- self-host push loop unavailable on this deployment; non-fatal)"
        else
            git -C / remote add origin "$_forge_remote_url" 2>/dev/null || true
        fi
        _log "root '/' origin -> Forgejo (${admin_user}/${INITIAL_REPO_NAME}); a push triggers the Runner"
        _log "self-host loop: edit in code-server (/mnt/mios-root) -> 'git -C / add -A && git -C / commit -m ... && git -C / push -u origin main' -> Runner builds /Containerfile -> bootc-switch stages next boot"
    fi
else
    _log "WARN: git not found in image -- cannot wire root '/' to forge"
fi

RUNNER_TOKEN_FILE=/etc/mios/forge/runner-token
install -d -m 0750 -o root -g mios-forge /etc/mios/forge

runner_token=""
if runner_token=$(podman exec -e HOME=/data/gitea mios-forge \
        forgejo --config /data/gitea/conf/app.ini actions generate-runner-token 2>/dev/null \
        | grep -oE '[A-Za-z0-9_-]{20,}' | head -n 1); then
    [[ -n "$runner_token" ]] && _log "minted runner token via forgejo CLI"
fi

if [[ -z "$runner_token" ]]; then
    runner_token=$(curl -sS -u "${admin_user}:${admin_password}" \
        "http://localhost:${http_port}/api/v1/admin/runners/registration-token" 2>/dev/null \
        | sed -nE 's/.*"token"\s*:\s*"([^"]+)".*/\1/p' \
        | tr -d '[:space:]') || true
    [[ -n "$runner_token" ]] && _log "minted runner token via admin API"
fi

forge_net=$(podman inspect mios-forge \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null \
    | awk '{print $1}')
if [[ "$forge_net" == "host" ]]; then
    runner_instance_url="http://localhost:${http_port}/"
else
    runner_instance_url="http://mios-forge:${http_port}/"
fi
_log "runner instance URL: ${runner_instance_url} (forge network: ${forge_net:-unknown})"

if [[ -n "$runner_token" ]]; then
    umask 077
    {
        echo "# /etc/mios/forge/runner-token"
        echo "# EnvironmentFile= consumed by mios-forgejo-runner.container Quadlet"
        echo "FORGEJO_RUNNER_REGISTRATION_TOKEN=${runner_token}"
        echo "FORGEJO_INSTANCE_URL=${runner_instance_url}"
    } > "$RUNNER_TOKEN_FILE"
    chmod 0600 "$RUNNER_TOKEN_FILE"
    chown root:root "$RUNNER_TOKEN_FILE"
    _log "wrote runner token to ${RUNNER_TOKEN_FILE} (mode 0600, root-only)"
    systemctl start --no-block mios-forgejo-runner.service 2>/dev/null || \
        _log "(mios-forgejo-runner.service not yet available; will start on next boot)"
    install -d -m 0755 -o root -g root "$(dirname "$SENTINEL")"
    date -u +%FT%TZ > "$SENTINEL"
    chmod 0644 "$SENTINEL"
    _log "firstboot complete; sentinel at $SENTINEL"
else
    _log "DEGRADED: could not mint runner token from forgejo (runner registration skipped; retrying)"
    exit 1
fi

_log "Forgejo URL:  http://localhost:${http_port}/"
_log "Admin user:   ${admin_user}"
_log "Admin email:  ${admin_email}"
_log "Initial repo: http://localhost:${http_port}/${admin_user}/${INITIAL_REPO_NAME}"
_log "Push pattern: git -C / remote add origin http://${admin_user}@localhost:${http_port}/${admin_user}/${INITIAL_REPO_NAME}.git && git -C / push -u origin main"
if [[ -f "$PASSWORD_FILE" ]]; then
    _log "Initial password: 'sudo cat ${PASSWORD_FILE}' (must change on first login)"
fi

exit 0
