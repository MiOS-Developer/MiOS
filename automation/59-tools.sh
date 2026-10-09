#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Sets executable permissions for the core mios- suite of CLI tools in /usr/bin/ and installs auxiliary scripts like mios-toggle-headless.
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck disable=SC1090  # log.sh resolves at runtime: build ctx or installed
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mios_log "Configure MiOS CLI tools"

TOOLS=(
    mios
    mios-backup
    mios-build
    mios-chrome
    mios-deploy
    mios-pull
    mios-rebuild
    mios-update
    hermes
)

for tool in "${TOOLS[@]}"; do
    if [ -f "/usr/bin/$tool" ]; then
        chmod +x "/usr/bin/$tool"
    fi
done

[[ -f "/usr/bin/mios-dash" ]] || ln -sf /usr/libexec/mios/mios-dashboard /usr/bin/mios-dash 2>/dev/null || true
if [ -f "/usr/libexec/mios/mios-vscode-custom-css" ]; then
    chmod +x "/usr/libexec/mios/mios-vscode-custom-css"
    ln -sf "/usr/libexec/mios/mios-vscode-custom-css" "/usr/bin/mios-vscode-custom-css" 2>/dev/null || true
    /usr/libexec/mios/mios-vscode-custom-css install --all 2>/dev/null || true
    mios_ok "Configured VS Code and code-server custom CSS extension across environments"
fi

mios_log "Install mios-toggle-headless"
if [ -f "${SCRIPT_DIR}/mios-toggle-headless" ]; then
    install -Dm0755 "${SCRIPT_DIR}/mios-toggle-headless" "/usr/bin/mios-toggle-headless"
fi

USERENV_SRC=""
for cand in \
    "${SCRIPT_DIR}/../tools/lib/userenv.sh" \
    "/tmp/build/tools/lib/userenv.sh" \
    "/ctx/tools/lib/userenv.sh"
do
    if [[ -f "$cand" ]]; then USERENV_SRC="$cand"; break; fi
done
if [[ -n "$USERENV_SRC" ]]; then
    install -D -m 0644 "$USERENV_SRC" /usr/lib/mios/userenv.sh
    mios_ok "Installed userenv.sh resolver to /usr/lib/mios/userenv.sh"
else
    mios_warn "Tools/lib/userenv.sh not found in build context; mios-env will fall back to legacy env-style files only"
fi

# --- Multi-user Nix Subsystem Setup ---
mios_log "Configure multi-user Nix subsystem"
mkdir -p /etc/nix
if [[ -f /usr/share/mios/nix/nix.conf && ! -f /etc/nix/nix.conf ]]; then
    cp /usr/share/mios/nix/nix.conf /etc/nix/nix.conf
    chmod 0644 /etc/nix/nix.conf
    mios_ok "Deployed default /etc/nix/nix.conf from /usr/share/mios/nix/nix.conf"
fi

mkdir -p /etc/profile.d
cat > /etc/profile.d/nix.sh << 'EOF'
# Nix multi-user environment setup for MiOS
if [ -n "${BASH_VERSION:-}" ] || [ -n "${ZSH_VERSION:-}" ]; then
    export NIX_PROFILES="/nix/var/nix/profiles/default ${HOME}/.nix-profile"
    export PATH="${HOME}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:${PATH}"
    if [ -e /etc/pki/tls/certs/ca-bundle.crt ]; then
        export NIX_SSL_CERT_FILE="/etc/pki/tls/certs/ca-bundle.crt"
    elif [ -e /etc/ssl/certs/ca-certificates.crt ]; then
        export NIX_SSL_CERT_FILE="/etc/ssl/certs/ca-certificates.crt"
    fi
fi
EOF
chmod 0644 /etc/profile.d/nix.sh

for unit in nix-daemon.socket nix-daemon.service; do
    if systemctl list-unit-files "${unit}" &>/dev/null; then
        systemctl enable "${unit}" 2>/dev/null || true
        mios_ok "Enabled systemd unit: ${unit}"
    fi
done

mios_ok "CLI tools and Nix subsystem configured; run 'mios'"
