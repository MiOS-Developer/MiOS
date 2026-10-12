#!/bin/bash
# AI-hint: Root-gated repair that ensures a content-verified ENROLLED OVMF varstore exists: copies the distro's enrolled VARS, enrolls a same-build blank template, or fetches edk2-ovmf; never overwrites firmware or touches live NVRAM.
# AI-related: find-ovmf-firmware.sh, check-ovmf-enrollment.sh, get-secureboot-ovmf.sh
# State is read first by the bounded EDK2 parser; enrollment is virt-fw-vars --enroll-redhat, a fetch is dnf download,
# and every artifact is content- and pair-checked before install (never /var/lib/libvirt/qemu/nvram or a live VM).

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SELF_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=tools/find-ovmf-firmware.sh
source "$SELF_DIR/find-ovmf-firmware.sh"

echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}     OVMF Secure Boot Enrollment Fixer (verified)${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}\n"

OVMF_DIR="${OVMF_TARGET_DIR:-$(ovmf_share_root)/edk2/x64}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[x] This script must be run as root${NC}"
    echo -e "  Run: ${CYAN}sudo $0${NC}"
    exit 1
fi

echo -e "${BLUE}[1/3] Checking current enrollment state (content, not names)...${NC}\n"

FOUND=$(ovmf_find_enrolled_vars)
if [ -n "$FOUND" ]; then
    FOUND_PATH=$(echo "$FOUND" | head -1 | cut -f1)
    echo -e "${GREEN}[ok] Content-verified enrolled varstore already exists:${NC}"
    echo -e "  Location: $FOUND_PATH ($(ovmf_human_size "$(ovmf_file_size "$FOUND_PATH")"))"
    echo -e "  Evidence: $(echo "$FOUND" | head -1 | cut -f2)"
    echo
    echo -e "${YELLOW}Nothing to fix. Use it as your NVRAM template - for example:${NC}"
    echo -e "  ${CYAN}<nvram template=\"$FOUND_PATH\">/var/lib/libvirt/qemu/nvram/VM_VARS.fd</nvram>${NC}"
    echo
    echo -e "${YELLOW}Safety: live NVRAM under /var/lib/libvirt/qemu/nvram and running VMs are never touched.${NC}"
    exit 0
fi

echo -e "${YELLOW}[!] No content-verified enrolled varstore found on this system.${NC}"
echo -e "  (Blank templates and 'secboot'-named files do NOT count - keys must be"
echo -e "   present in the varstore content: PK/KEK/db/dbx.)"
echo

echo -e "${BLUE}[2/3] Tooling check...${NC}\n"
if command -v virt-fw-vars &>/dev/null; then
    echo -e "  ${GREEN}[ok]${NC} virt-fw-vars present (offline enrollment available)"
else
    echo -e "  ${YELLOW}[!]${NC} virt-fw-vars missing - offline enrollment unavailable"
    echo -e "    MiOS ships it via the virt package group: ${CYAN}sudo dnf install virt-firmware${NC}"
fi
command -v python3 &>/dev/null \
    && echo -e "  ${GREEN}[ok]${NC} python3 present (embedded varstore parser available)" \
    || echo -e "  ${YELLOW}[!]${NC} python3 missing - verification coverage reduced"
echo

echo -e "${BLUE}[3/3] Repair...${NC}\n"
echo -e "${YELLOW}Target directory: $OVMF_DIR${NC}"
echo -e "${YELLOW}Guarantees: only content-verified ENROLLED varstores are written;"
echo -e "existing files are never overwritten; live NVRAM is never touched.${NC}\n"

ovmf_repair_menu "$OVMF_DIR"
rc=$?

if [ $rc -eq 0 ]; then
    echo
    echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${GREEN}                 Repair Complete${NC}"
    echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════${NC}"
    echo
    echo -e "${YELLOW}Next: point your VM XML at the verified template, or rely on autoselection:${NC}"
    cat <<'XMLEOF'
  <os firmware="efi">
    <firmware>
      <feature enabled="yes" name="enrolled-keys"/>
      <feature enabled="yes" name="secure-boot"/>
    </firmware>
  </os>
XMLEOF
    echo -e "  ${YELLOW}(libvirt only SELECTS pre-enrolled firmware - it never enrolls keys itself)${NC}"
fi
exit $rc
