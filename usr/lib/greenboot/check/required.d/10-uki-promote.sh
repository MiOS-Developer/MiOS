#!/usr/bin/env bash
# AI-hint: Automated UKI A/B boot promotion and Greenboot validation gate (task_452).
# AI-doc: usr/share/doc/mios/manual/ch08-bootloader-and-unified-kernel-images-uki.md
set -euo pipefail

BOOT_DIR="${MIOS_BOOT_DIR:-/boot}"
EFI_DIR="${BOOT_DIR}/EFI/Linux"
LOADER_DIR="${BOOT_DIR}/loader"
ENTRIES_DIR="${LOADER_DIR}/entries"

NEXT_EFI="${EFI_DIR}/mios-next.efi"
DEFAULT_EFI="${EFI_DIR}/mios.efi"
NEXT_CONF="${ENTRIES_DIR}/mios-next.conf"
DEFAULT_CONF="${ENTRIES_DIR}/mios.conf"
LOADER_CONF="${LOADER_DIR}/loader.conf"

if [[ ! -f "$NEXT_EFI" ]]; then
    # No staged UKI pending promotion
    exit 0
fi

echo "[greenboot] Staged UKI detected at ${NEXT_EFI}; promoting to default"

# Atomically promote mios-next.efi to mios.efi
install -d -m 0755 "${EFI_DIR}"
mv -f "${NEXT_EFI}" "${DEFAULT_EFI}"

# Ensure default loader entry exists pointing to mios.efi
if [[ -d "${ENTRIES_DIR}" ]]; then
    cat > "${DEFAULT_CONF}" <<EOF
# MiOS Active Default UKI Boot Entry (task_452)
title MiOS (Active UKI)
linux /EFI/Linux/mios.efi
options (baked into UKI)
EOF
    rm -f "${NEXT_CONF}"
fi

# Update loader.conf default entry if present
if [[ -f "${LOADER_CONF}" ]]; then
    if grep -q "^default " "${LOADER_CONF}"; then
        sed -i 's/^default .*/default mios.conf/' "${LOADER_CONF}"
    else
        echo "default mios.conf" >> "${LOADER_CONF}"
    fi
fi

# Reset one-shot if bootctl is present
if command -v bootctl >/dev/null 2>&1; then
    bootctl set-default mios.conf 2>/dev/null || true
    bootctl set-oneshot "" 2>/dev/null || true
fi

echo "[greenboot] Successfully promoted UKI to ${DEFAULT_EFI} and updated boot loader"
exit 0
