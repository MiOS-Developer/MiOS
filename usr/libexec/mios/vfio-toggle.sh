#!/usr/bin/env bash
# AI-hint: mios-vfio-toggle -- enable or disable PCIe passthrough via vfio-pci.
# AI-related: /usr/share/mios/tools/universal-vfio-configurator.sh, /usr/libexec/mios/vfio-config.sh, mios-vfio-toggle, mios-vfio-check
set -euo pipefail

VFIO_CONF="/etc/modprobe.d/vfio.conf"
TOOL_SH="/usr/libexec/mios/vfio-config.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    sed -n '2,16p' "$0" | sed 's/^# \?//'
    exit 0
fi

if [[ "${1:-}" == "--status" ]]; then
    exec /usr/bin/mios-vfio-check
fi

if [[ $EUID -ne 0 ]]; then exec sudo -E "$0" "$@"; fi

case "${1:-}" in
    --remove)
        if [[ -f "$VFIO_CONF" ]]; then
            install -m 0644 /dev/null "$VFIO_CONF"
            echo "[mios-vfio-toggle] cleared $VFIO_CONF"
        fi
        echo "[mios-vfio-toggle] reboot to release vfio-pci bindings"
        ;;
    --add)
        id="${2:-}"
        [[ "$id" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$ ]] || {
            echo "Mios-vfio-toggle" >&2
            exit 2
        }
        mkdir -p "$(dirname "$VFIO_CONF")"
        if [[ -f "$VFIO_CONF" ]] && grep -qE '^options\s+vfio-pci\s+ids=' "$VFIO_CONF"; then
            existing=$(grep -E '^options\s+vfio-pci\s+ids=' "$VFIO_CONF" | sed -E 's/.*ids=([^ ]+).*/\1/' | head -1)
            grep -q "$id" <<< "$existing" || existing="${existing},${id}"
            sed -i -E "s|^options\s+vfio-pci\s+ids=.*|options vfio-pci ids=${existing}|" "$VFIO_CONF"
        else
            cat >> "$VFIO_CONF" <<EOF
options vfio-pci ids=${id}
softdep nvidia pre: vfio-pci
softdep amdgpu pre: vfio-pci
softdep i915   pre: vfio-pci
EOF
        fi
        echo "[mios-vfio-toggle] added $id to $VFIO_CONF"
        echo "[mios-vfio-toggle] reboot to bind"
        ;;
    "")
        if [[ -x "$TOOL_SH" ]]; then
            exec "$TOOL_SH"
        else
            echo "Mios-vfio-toggle: $TOOL_SH missing" >&2
            exit 1
        fi
        ;;
    *)
        echo "Mios-vfio-toggle: unknown flag '$1'" >&2
        exit 2
        ;;
esac
