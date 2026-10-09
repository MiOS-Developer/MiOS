#!/bin/bash
# AI-hint: Claude Code cloud environment Setup script for MiOS: provisions the host (dev-loop plugin, agy), then bootstrap.sh claude-code pulls the MiOS image in the background and installs mios-dev; always exits 0.
# AI-related: .devcontainer/cloud-shell/bootstrap.sh, .devcontainer/Containerfile, .devcontainer/devcontainer.json, .devcontainer/cloud-shell/README.md
# AI-functions: main, mios_checkout
#
# The environment's Setup script field fetches this file from MiOS main and
# runs it (see README.md beside it), so a change here reaches the next new
# session without re-pasting anything. Everything MiOS-specific lives in
# bootstrap.sh beside it; this entry point only finds a MiOS checkout for it.
#
# Cloud setup-script contract: runs as root before Claude Code starts, must
# exit 0, and should finish inside about five minutes or the environment
# cache is not saved. No `set -e`: every step is guarded and a failure leaves
# a plain session with a log line, never a failed one. The ~23 GB MiOS image
# cannot arrive in that window, so it is pulled in the background and
# `mios-dev` reports its progress until it is in.

# The session's MiOS checkout, else a clone of MIOS_REPO at MIOS_REF.
mios_checkout() {
    local c cache=/opt/mios-cloud/src/MiOS
    for c in "${MIOS_ROOT:-}" "$PWD" /home/*/MiOS /root/MiOS /workspace/MiOS "$cache"; do
        [ -n "$c" ] && [ -f "$c/.devcontainer/cloud-shell/bootstrap.sh" ] && [ -f "$c/usr/share/mios/mios.toml" ] \
            && { printf '%s\n' "$c"; return 0; }
    done
    mkdir -p "$(dirname "$cache")" || return 1
    git clone -q --depth 1 --branch "${MIOS_REF:-main}" "${MIOS_REPO:-https://github.com/mios-dev/MiOS}" "$cache" \
        && printf '%s\n' "$cache"
}

main() {
    local toolkit=/opt/dev-loop root
    # The engineering-loop toolkit is the plugin (CLAUDE_CODE_PLUGIN_DIRS) and
    # provisions the VM itself (agy, keyring, grants, dev-loop skill). A cache,
    # not a workspace: it always lands on main.
    if [ -d "$toolkit/.git" ]; then
        { git -C "$toolkit" fetch -q --depth 1 origin main && git -C "$toolkit" reset -q --hard FETCH_HEAD; } \
            || echo "[mios-cloud] toolkit update failed; using the cached checkout"
    else
        git clone -q --depth 1 https://github.com/mios-dev/-dev-loop "$toolkit" \
            || echo "[mios-cloud] toolkit clone failed; no dev-loop plugin this session"
    fi
    if [ -f "$toolkit/skills/dev-loop/scripts/env/setup-antigravity.sh" ]; then
        timeout 300 bash "$toolkit/skills/dev-loop/scripts/env/setup-antigravity.sh" --quiet \
            >/var/log/mios-cloud-host-setup.log 2>&1 \
            || echo "[mios-cloud] host provisioning failed; see /var/log/mios-cloud-host-setup.log"
    fi

    root="$(mios_checkout)" || { echo "[mios-cloud] no MiOS checkout and the clone failed; no MiOS container this session"; return 0; }
    timeout "${MIOS_SETUP_BUDGET_S:-240}" bash "$root/.devcontainer/cloud-shell/bootstrap.sh" claude-code \
        || echo "[mios-cloud] MiOS provisioning did not finish; run: bash $root/.devcontainer/cloud-shell/bootstrap.sh claude-code"
}

main "$@"
exit 0
