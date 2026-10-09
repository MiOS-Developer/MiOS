#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures GPU drivers by installing Mesa, AMD ROCm, and Intel compute runtimes, while performing a multi-stage check and fallb...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"

KVER=$(cat /tmp/mios-kver 2>/dev/null || find /lib/modules/ -mindepth 1 -maxdepth 1 -printf "%f\n" | sort -V | tail -1)

mios_log "Install Mesa GPU stack"
install_packages_strict "gpu-mesa"

mios_log "Install ROCm"
install_packages "gpu-amd-compute"

mios_log "Install Intel compute runtime"
install_packages "gpu-intel-compute" || true

mios_log "Check NVIDIA modules from ucore base"

NVIDIA_PRESENT=0
if [[ -d "/lib/modules/$KVER/extra/nvidia" ]] || \
   [[ -d "/lib/modules/$KVER/extra/nvidia-open" ]] || \
   modinfo nvidia -k "$KVER" &>/dev/null; then
    mios_ok "NVIDIA kmod present for kernel $KVER"
    NVIDIA_PRESENT=1
fi

if [[ $NVIDIA_PRESENT -eq 0 ]]; then
    mios_log "Fallback: akmod-nvidia build against $KVER"
    if install_packages "gpu-nvidia"; then
        if command -v akmods &>/dev/null; then
            akmods --force --kernels "$KVER" 2>&1 | tail -10 || true
            if modinfo nvidia -k "$KVER" &>/dev/null; then
                mios_ok "NVIDIA kmod rebuilt via akmods for $KVER"
                NVIDIA_PRESENT=1
            fi
        fi
    fi
fi

if [[ $NVIDIA_PRESENT -eq 0 ]]; then
    mios_warn "No NVIDIA kmod for $KVER after all fallback attempts"
    mios_warn "Image will ship without NVIDIA acceleration. Users with"
    mios_warn "NVIDIA hardware can rebuild the kmod at runtime:"
    mios_warn "Sudo dnf install kernel-devel-\$ akmod-nvidia"
    mios_warn "Sudo akmods"
fi

if command -v nvidia-ctk &>/dev/null; then
    nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml 2>/dev/null || true
    mios_ok "NVIDIA CDI spec generated"
fi

HW_PROFILE="${SCRIPT_DIR}/../usr/libexec/mios/mios-hardware-profile"
if [[ -x "$HW_PROFILE" ]]; then
    mios_log "Classify hardware target tier and configure initial profile"
    "$HW_PROFILE" --apply || true
    mios_ok "Hardware target profile applied"
elif [[ -x "/usr/libexec/mios/mios-hardware-profile" ]]; then
    mios_log "Classify hardware target tier and configure initial profile"
    /usr/libexec/mios/mios-hardware-profile --apply || true
    mios_ok "Hardware target profile applied"
fi

mios_ok "GPU stack: Mesa + AMD ROCm + Intel installed; NVIDIA kmod present=$NVIDIA_PRESENT"

# Folded from 24-gpu-pv-shim.sh (T-1161): Hyper-V GPU-PV (dxgkrnl) support
mios_log "GPU-PV shim dirs"
mkdir -p /usr/lib/wsl/lib
mkdir -p /usr/lib/wsl/drivers

mios_log "Ld.so.conf paths"
install -d -m 0755 /usr/lib/ld.so.conf.d
echo "/usr/lib/wsl/lib" > /usr/lib/ld.so.conf.d/mios-gpu-pv.conf

MIOS_LIBEXEC_DIR="${SCRIPT_DIR}/../usr/libexec/mios"
mkdir -p "${MIOS_LIBEXEC_DIR}"
cat > "${MIOS_LIBEXEC_DIR}/gpu-pv-detect" <<'EOF'
set -euo pipefail
log() { echo "[gpu-pv-detect] $*"; }

if [ ! -e /dev/dxg ]; then
    exit 0
fi

log "/dev/dxg present"
if [ -z "$(ls -A /usr/lib/wsl/lib 2>/dev/null)" ]; then
    log "HINT: /usr/lib/wsl/lib is empty. GPU acceleration requires host drivers"
    log "HINT: Copy drivers from Windows: C:\Windows\System32\lxss\lib -> /usr/lib/wsl/lib"
fi
EOF

chmod +x "${MIOS_LIBEXEC_DIR}/gpu-pv-detect"

cat > /usr/lib/systemd/system/mios-gpu-pv-detect.service <<EOF
[Unit]
Description='MiOS' Hyper-V GPU-PV Detection
ConditionVirtualization=microsoft
After=local-fs.target
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=${MIOS_LIBEXEC_DIR}/gpu-pv-detect
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

mios_log "Enable gpu-pv-detect service"
WANTS=/usr/lib/systemd/system/multi-user.target.wants
install -d -m 0755 "${WANTS}"
ln -sf ../mios-gpu-pv-detect.service "${WANTS}/mios-gpu-pv-detect.service" 2>/dev/null || true

mios_ok "GPU-PV shim installed: /usr/lib/wsl/{lib,drivers}, ld.so.conf.d/mios-gpu-pv.conf, mios-gpu-pv-detect.service enabled"

