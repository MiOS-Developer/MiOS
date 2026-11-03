#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs systemd drop-in files for NVIDIA services to implement ExecCondition guards, ensuring units skip execution...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

mios_log "Installing ExecCondition drop-ins"

SERVICES=(
    nvidia-persistenced
    nvidia-powerd
    nvidia-suspend
    nvidia-resume
    nvidia-hibernate
    nvidia-suspend-then-hibernate
    nvidia-cdi-refresh
)

DROPIN_NAME="10-mios-akmod-guard.conf"
count=0

for svc in "${SERVICES[@]}"; do
    dir="/usr/lib/systemd/system/${svc}.service.d"
    path="${dir}/${DROPIN_NAME}"
    install -d -m 0755 "${dir}"
    cat > "${path}" <<'EOF'
[Service]
ExecCondition=/bin/bash -c 'grep -Eq "(^|/)nvidia\\.ko(\\.[xz]z|\\.zst)?:" /lib/modules/$(uname -r)/modules.dep'
EOF
    # Modules and /dev/nvidiactl can remain while the GPU belongs to vfio-pci.
    # The persistence daemon requires a PCI device actually bound to nvidia.
    if [[ "$svc" == nvidia-persistenced ]]; then
        cat >> "$path" <<'EOF'
ExecCondition=/bin/bash -c 'compgen -G "/sys/bus/pci/drivers/nvidia/????:??:??.?" >/dev/null'
EOF
    fi
    chmod 0644 "${path}"
    count=$((count + 1))
    mios_log "Installed ${path}"
done

mios_ok "${count} drop-ins installed"
