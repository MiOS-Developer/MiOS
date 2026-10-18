#!/usr/bin/env bash
# AI-hint: bash Install + configure GNOME Remote Desktop in system/headless mode so AI-related: /usr/libexec/mios/automation/35-xrdp...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

_log() { printf '[grd-enhanced] %s\n' "$*" >&2; }

_MIOS_TOML_GET="$(cd "$(dirname "$0")" && pwd)/../mios-toml-get"
_mios_toml_value() { "$_MIOS_TOML_GET" "$1" "$2" "${3:-}"; }

PORT="$(_mios_toml_value 'enhanced_session' 'port' '13389')"
# Credentials: layered SSOT only (vendor < host < user). No literal fallback -- fail closed.
USER_NAME="$(_mios_toml_value 'enhanced_session' 'user' "$(_mios_toml_value 'identity' 'username' '')")"
PASSWORD="$(_mios_toml_value 'identity' 'default_password' '')"
if [ -z "$USER_NAME" ] || [ -z "$PASSWORD" ]; then
    _log "ERROR: required SSOT credential key [enhanced_session].user/[identity].username or [identity].default_password is missing or empty (no built-in default)"
    exit 3
fi
ENABLED="$(_mios_toml_value 'enhanced_session' 'enabled' 'true')"

if [ "$ENABLED" != "true" ]; then
    _log "enhanced_session.enabled=false -- skipping grd install"
    exit 0
fi

_log "installing gnome-remote-desktop + gdm + winpr-utils + freerdp"
sudo env SYSTEMD_OFFLINE=1 dnf install -y --skip-unavailable \
    gnome-remote-desktop \
    gdm \
    winpr-utils \
    freerdp \
    gnome-session \
    gnome-shell \
    mutter \
    pipewire \
    pipewire-pulseaudio \
    >/dev/null

CERT_DIR="/var/lib/gnome-remote-desktop"
if [ ! -f "$CERT_DIR/rdp-tls.crt" ] || [ ! -f "$CERT_DIR/rdp-tls.key" ]; then
    _log "generating TLS cert at $CERT_DIR/rdp-tls.{crt,key}"
    sudo -u gnome-remote-desktop winpr-makecert -silent -rdp \
        -n localhost \
        -path "$CERT_DIR" \
        rdp-tls 2>/dev/null
fi

_log "configuring grd --system: port=$PORT user=$USER_NAME tls=$CERT_DIR/rdp-tls.*"
sudo grdctl --system rdp set-tls-cert "$CERT_DIR/rdp-tls.crt"
sudo grdctl --system rdp set-tls-key  "$CERT_DIR/rdp-tls.key"
sudo grdctl --system rdp set-credentials "$USER_NAME" "$PASSWORD"
sudo grdctl --system rdp set-port "$PORT"
sudo grdctl --system rdp enable

_log "enabling gdm.service + gnome-remote-desktop.service"
sudo systemctl daemon-reload
sudo systemctl enable --now gdm.service gnome-remote-desktop.service

sleep 4

if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
    _log "gnome-remote-desktop listening on *:$PORT  (connect via mstsc /v:localhost:$PORT)"
else
    _log "WARN: grd NOT listening on :$PORT yet -- check 'journalctl -u gnome-remote-desktop.service'"
    sudo journalctl -u gnome-remote-desktop.service --no-pager 2>&1 | tail -5 | sed 's/^/[grd-enhanced]   /'
fi

_log "done. Enhanced Session ready. User=$USER_NAME  Password=[identity].default_password from mios.toml."
