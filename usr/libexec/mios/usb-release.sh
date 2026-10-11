#!/usr/bin/env bash
# AI-hint: Decouples physical boot USB media post-boot in copytoram RAM hypervisor mode.
# AI-related: usb-release.service, RAM-Resident-SystemRescue-Hypervisor-Blueprint.md
set -euo pipefail

BOOTMNT="/run/archiso/bootmnt"

if mountpoint -q "${BOOTMNT}"; then
    DEV_SOURCE=$(findmnt -n -o SOURCE "${BOOTMNT}" || true)
    if [ -n "${DEV_SOURCE}" ]; then
        DEV_NODE=$(echo "${DEV_SOURCE}" | sed -E 's/p?[0-9]+$//')
        umount -l "${BOOTMNT}" || true
        sync
        DEV_BASE=$(basename "${DEV_NODE}")
        if [ -b "${DEV_NODE}" ]; then
            echo 1 > "/sys/block/${DEV_BASE}/device/delete" 2>/dev/null || true
        fi
        logger -t "MiOS-Bootstrap" "Physical installation media unmounted. USB device (${DEV_NODE}) safely decoupled."
    fi
fi
