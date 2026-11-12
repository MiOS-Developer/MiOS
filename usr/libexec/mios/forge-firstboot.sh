#!/usr/bin/env bash
# AI-hint: bash First-boot admin-bootstrap for the mios-forge Quadlet (Forgejo): creates the admin, has mios-credential apply set its SSOT password (never generated or literal), wires / to the repo, mints the runner token.
# AI-doc: usr/share/doc/mios/manual/mios.md

set -euo pipefail

SENTINEL=/var/lib/mios/forge/.firstboot-done
CRED=/usr/libexec/mios/mios-credential

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

# Every SSOT value comes from the SAME operator view `mios-credential apply` uses (vendor +
# host; only the credential and enable toggles follow the operator's tier), so this script
# and the reconciler it calls agree; install.env is never read for any of it.
_get() { "$CRED" get "$1"; }
admin_user="$("$CRED" identity username --operator)"
admin_email="$("$CRED" identity email --operator)"
http_port="$(_get ports.forge_http)"
loopback="$(_get network.loopback)"
initial_repo="$(_get services.forge.initial_repo)"
ready_timeout="$(_get services.forge.ready_timeout_s)"
forge_unit="$(_get units.forge)"
forge_home="$(_get services.forge.home)"
forge_ini="$(_get services.forge.app_ini)"
forge_ctr="${forge_unit%.service}"
if [[ -z "$admin_user" || -z "$admin_email" || -z "$http_port" || -z "$loopback" || -z "$initial_repo" \
      || -z "$forge_ctr" || -z "$forge_home" || -z "$forge_ini" || ! "$ready_timeout" =~ ^[0-9]+$ ]]; then
    _log "DEGRADED: [identity] username/email, [ports].forge_http, [network].loopback, [units].forge or [services.forge] initial_repo/ready_timeout_s/home/app_ini missing from SSOT; exiting for systemd retry"
    exit 1
fi
_forgejo() { podman exec -e "HOME=${forge_home}" "$forge_ctr" forgejo --config "$forge_ini" "$@"; }

_is_forge_up() {  # Forgejo opens its port once migrations are done; a loopback connect, no fetch
    (exec 3<>"/dev/tcp/${loopback}/${http_port}") 2>/dev/null
}

deadline=$(( $(date +%s) + ready_timeout ))
until _is_forge_up; do
    if (( $(date +%s) >= deadline )); then
        _log "DEGRADED: Forgejo did not become ready on :${http_port}; exiting with error for systemd retry"
        exit 1
    fi
    sleep 2
done
_log "Forgejo is up on :${http_port}"

# Create the account with a throwaway random password whose output is discarded (the CLI
# prints it); mios-credential apply then sets the SSOT value through the admin API, with
# the password in an HTTP body -- never on a command line, in a log or in a file.
if _forgejo admin user create \
        --admin --must-change-password=false \
        --username "$admin_user" --email "$admin_email" \
        --random-password >/dev/null 2>&1; then
    _log "admin user '${admin_user}' (${admin_email}) created"
else
    _log "admin create returned non-zero; treating as 'already exists'"
fi
if ! "$CRED" apply --wait --bootstrap forge; then
    _log "DEGRADED: could not apply the SSOT credential to the Forgejo admin; exiting for systemd retry"
    exit 1
fi

# The origin carries no userinfo: a push authenticates through the git credential flow.
_forge_remote_url="http://${loopback}:${http_port}/${admin_user}/${initial_repo}.git"
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
        if [[ -n "$_cur_origin" && "$_cur_origin" != *":${http_port}/"* ]]; then
            git -C / remote rename origin local-bare 2>/dev/null || true
        fi
        if git -C / remote get-url origin >/dev/null 2>&1; then
            git -C / remote set-url origin "$_forge_remote_url" 2>/dev/null || _log "WARN: 'git -C / remote set-url' failed (read-only / -- self-host push loop unavailable on this deployment; non-fatal)"
        else
            git -C / remote add origin "$_forge_remote_url" 2>/dev/null || true
        fi
        _log "root '/' origin -> Forgejo (${admin_user}/${initial_repo}); a push triggers the Runner"
    fi
else
    _log "WARN: git not found in image -- cannot wire root '/' to forge"
fi

RUNNER_TOKEN_FILE=/etc/mios/forge/runner-token
install -d -m 0750 -o root -g mios-forge /etc/mios/forge

runner_token=""
if runner_token=$(_forgejo actions generate-runner-token 2>/dev/null \
        | grep -oE '[A-Za-z0-9_-]{20,}' | head -n 1); then
    [[ -n "$runner_token" ]] && _log "minted runner token via forgejo CLI"
fi

forge_net=$(podman inspect "$forge_ctr" \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null \
    | awk '{print $1}')
if [[ "$forge_net" == "host" ]]; then
    runner_instance_url="http://${loopback}:${http_port}/"
else
    runner_instance_url="http://${forge_ctr}:${http_port}/"
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
    _log "DEGRADED: could not mint runner token from the forgejo CLI (runner registration skipped; retrying)"
    exit 1
fi

_log "Forgejo URL:  http://${loopback}:${http_port}/  (admin '${admin_user}', password = the operator credential set in mios.html)"
_log "Initial repo: http://${loopback}:${http_port}/${admin_user}/${initial_repo}"

exit 0
