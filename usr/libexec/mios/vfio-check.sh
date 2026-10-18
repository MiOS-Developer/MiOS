#!/usr/bin/env bash
# AI-hint: mios-vfio-check -- read-only VFIO state report. Shows IOMMU support
# AI-related: mios-vfio-check, mios-vfio-toggle
set -euo pipefail

short=0
[[ "${1:-}" == "--short" ]] && short=1
[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { sed -n '2,9p' "$0" | sed 's/^# \?//'; exit 0; }

iommu_ok=0
[[ -d /sys/kernel/iommu_groups ]] && [[ -n "$(ls -A /sys/kernel/iommu_groups 2>/dev/null)" ]] && iommu_ok=1

modules=()
for m in vfio vfio_iommu_type1 vfio_pci vfio_virqfd; do
    if lsmod 2>/dev/null | awk '{print $1}' | grep -qx "$m"; then modules+=("$m"); fi
done

vfio_conf="/etc/modprobe.d/vfio.conf"
configured_ids=""
if [[ -r "$vfio_conf" ]]; then
    configured_ids="$(grep -hE '^options\s+vfio-pci\s+ids=' "$vfio_conf" 2>/dev/null \
        | sed -E 's/.*ids=([^ ]+).*/\1/' | tr ',' ' ')"
fi

bound_devices=""
if [[ -d /sys/bus/pci/drivers/vfio-pci ]]; then
    bound_devices="$(find /sys/bus/pci/drivers/vfio-pci -maxdepth 1 -mindepth 1 -type l -printf '%f\n' \
        | grep -E '^[0-9a-f]{4}:' || true)"
fi

if (( short )); then
    if (( iommu_ok )) && (( ${#modules[@]} > 0 )); then
        echo "VFIO: ready (IOMMU on, ${#modules[@]} modules loaded, $(echo "$bound_devices" | grep -c . || echo 0) device(s) bound)"
    else
        echo "VFIO: NOT ready"
    fi
    exit 0
fi

echo "==> IOMMU"
if (( iommu_ok )); then
    echo "    enabled groups)"
else
    echo "    NOT enabled"
fi

echo
echo "==> VFIO kernel modules"
if (( ${#modules[@]} == 0 )); then
    echo "    none loaded"
else
    printf '    %s\n' "${modules[@]}"
fi

echo
echo "==> Configured passthrough IDs"
if [[ -z "$configured_ids" ]]; then
    echo ""
else
    for id in $configured_ids; do
        echo "    $id"
    done
fi

echo
echo "==> Currently bound to vfio-pci"
if [[ -z "$bound_devices" ]]; then
    echo ""
else
    while read -r bdf; do
        [[ -z "$bdf" ]] && continue
        lspci -nns "$bdf" 2>/dev/null || echo "    $bdf"
    done <<< "$bound_devices"
fi
