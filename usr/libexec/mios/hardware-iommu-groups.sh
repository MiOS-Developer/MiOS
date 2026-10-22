#!/usr/bin/env bash
# AI-hint: iommu-groups -- list IOMMU groups and the PCIe devices in each.
# AI-functions: check_enabled, list_groups, show_device
set -euo pipefail

iommu_root="/sys/kernel/iommu_groups"

check_enabled() {
    if [[ -d "$iommu_root" ]] && [[ -n "$(ls -A "$iommu_root" 2>/dev/null)" ]]; then
        echo "IOMMU: enabled ($(ls "$iommu_root" | wc -l) groups)"
        return 0
    fi
    echo "IOMMU: NOT enabled"
    echo "  - confirm CPU+chipset support VT-d / AMD-Vi"
    echo "  - confirm kargs include 'intel_iommu=on iommu=pt' or 'amd_iommu=on iommu=pt'"
    echo "  - on bare-metal: BIOS 'VT-d' / 'IOMMU' / 'SVM' must be Enabled"
    return 1
}

list_groups() {
    local g d
    for g in $(find "$iommu_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -n); do
        echo "IOMMU Group $g:"
        for d in "$iommu_root/$g/devices/"*; do
            local bdf="${d##*/}"
            local desc
            desc="$(lspci -nns "$bdf" 2>/dev/null || echo "$bdf")"
            echo "    $desc"
        done
    done
}

show_device() {
    local bdf="$1"
    local link
    link="$(readlink -f /sys/bus/pci/devices/"$bdf"/iommu_group 2>/dev/null || true)"
    if [[ -z "$link" ]]; then
        echo "Iommu-groups: device '$bdf' not found or not in an IOMMU group" >&2
        return 1
    fi
    local g="${link##*/}"
    echo "Device $bdf is in IOMMU Group $g:"
    for d in "$link/devices/"*; do
        local sib="${d##*/}"
        lspci -nns "$sib" 2>/dev/null || echo "    $sib"
    done
}

case "${1:-}" in
    --check) check_enabled ;;
    --device) [[ -n "${2:-}" ]] || { echo "Iommu-groups" >&2; exit 2; }; show_device "$2" ;;
    --help|-h) sed -n '2,12p' "$0" | sed 's/^# \?//' ;;
    "") check_enabled >/dev/null && list_groups ;;
    *) echo "Iommu-groups: unknown flag '$1'" >&2; exit 2 ;;
esac
