#!/bin/bash
# AI-hint: Codex Cloud entry point for MiOS: finds or clones the MiOS checkout and runs bootstrap.sh codex (install, start, check), which runs the MiOS image as the devcontainer with an unprivileged mios-dev wrapper. Fails closed.
# AI-related: .devcontainer/cloud-shell/bootstrap.sh, README.md, .devcontainer/devcontainer.json, .devcontainer/Containerfile, usr/share/mios/mios.toml
# AI-functions: mios_checkout
#
# README.md pastes this file's raw URL into the Codex environment's Install
# script, so it must work fetched on its own: it locates (or clones) MiOS and
# hands every step to .devcontainer/cloud-shell/bootstrap.sh in that checkout.
# `install` copies this entry point to /usr/local/libexec/mios-codex-cloud for
# the Start skill's `mios-codex-cloud start`.
set -euo pipefail

readonly STATE=/var/lib/mios-cloud

mios_checkout() {
    local c self cache=/opt/mios-cloud/src/MiOS
    self="$(readlink -f "${BASH_SOURCE[0]}")"
    for c in "${MIOS_ROOT:-}" "$(dirname "$self")/../.." "$(git rev-parse --show-toplevel 2>/dev/null || true)" \
             "$PWD/MiOS" "$(dirname "$PWD")/MiOS" "$(cat "$STATE/source-root" 2>/dev/null || true)" "$cache"; do
        [[ -n "$c" && -f "$c/.devcontainer/cloud-shell/bootstrap.sh" && -f "$c/usr/share/mios/mios.toml" ]] \
            && { (cd "$c" && pwd -P); return 0; }
    done
    install -d -m 0755 "$(dirname "$cache")"
    git clone --depth 1 --branch "${MIOS_DEPLOYMENT_CLOUD_SOURCE_REF:-main}" "${MIOS_REPO:-https://github.com/mios-dev/MiOS.git}" "$cache"
    chown -R "${MIOS_UID:-1000}:${MIOS_GID:-1000}" "$cache"
    printf '%s\n' "$cache"
}

# bootstrap.sh reads the SSOT with python3; a clone needs git. Podman comes later.
if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    [[ $EUID -eq 0 ]] || { echo 'Run the install script as root (or with sudo).' >&2; exit 1; }
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q git python3
    else
        dnf install -y git python3
    fi
fi
root="$(mios_checkout)"
if [[ "${1:-install}" == install ]]; then
    install -d -m 0755 /usr/local/libexec
    install -m 0755 "$(readlink -f "${BASH_SOURCE[0]}")" /usr/local/libexec/mios-codex-cloud.new
    mv -f /usr/local/libexec/mios-codex-cloud.new /usr/local/libexec/mios-codex-cloud
fi
exec bash "$root/.devcontainer/cloud-shell/bootstrap.sh" codex "${1:-install}"
