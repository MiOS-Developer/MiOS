#!/bin/bash
# AI-hint: Hot-deploys source-only MiOS binaries, configuration files (tmpfiles/sysusers), and OWUI tools to the live VM's /usr path without a full i...
# AI-doc: usr/share/doc/mios/manual/support.md
set -euo pipefail
SRC=/mnt/c/MiOS
LX=/usr/libexec/mios

echo "[live] libexec tools -> $LX"
for f in mios-launcher-daemon mios-db mios-docgen mios-coderun-codemode \
         mios-stresstest mios-owui-install-computer-use mios-hermes-firstboot \
         mios-codemode-api.py test_mios_office_convert.py; do
    s="$SRC/usr/libexec/mios/$f"
    [ -f "$s" ] || { echo "  MISSING $s"; continue; }
    tr -d '\r' < "$s" | sudo tee "$LX/$f" >/dev/null
    sudo chmod 0755 "$LX/$f"
    echo "  + $f"
done

echo "[live] tmpfiles + sysusers"
[ -f "$SRC/usr/lib/tmpfiles.d/50-mios.conf" ] && \
    tr -d '\r' < "$SRC/usr/lib/tmpfiles.d/50-mios.conf" | sudo tee /usr/lib/tmpfiles.d/50-mios.conf >/dev/null && echo "  + tmpfiles/50-mios.conf"
[ -f "$SRC/usr/lib/sysusers.d/50-mios.conf" ] && \
    tr -d '\r' < "$SRC/usr/lib/sysusers.d/50-mios.conf" | sudo tee /usr/lib/sysusers.d/50-mios.conf >/dev/null && echo "  + sysusers/50-mios.conf"
sudo systemd-sysusers 2>&1 | tail -2 || true
sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/50-mios.conf 2>&1 | tail -2 || true

echo "[live] restart broker"
sudo systemctl restart mios-launcher-daemon.service 2>&1 || true
sleep 2
echo "  broker: $"

echo "[live] shim resolution check"
for t in mios-docgen mios-coderun-codemode mios-stresstest mios-db; do
    p="$(command -v "$t" 2>/dev/null || true)"
    [ -n "$p" ] && echo "  resolves: $t -> $p" || echo "  NOT-on-PATH: $t"
done

echo "[live] OWUI computer-use tool file + installer"
sudo install -d -m 0755 /usr/share/mios/openwebui/tools
tr -d '\r' < "$SRC/usr/share/mios/openwebui/tools/mios_computer_use.py" | sudo tee /usr/share/mios/openwebui/tools/mios_computer_use.py >/dev/null
if [ -x "$LX/mios-owui-install-computer-use" ]; then
    sudo "$LX/mios-owui-install-computer-use" 2>&1 | tail -6 || echo ""
fi

echo "[live] DONE. REBUILD-GATED: docgen pandoc/libreoffice, code_mode mios-coderun-sandbox image, WS-7 fapolicyd/UKI"
