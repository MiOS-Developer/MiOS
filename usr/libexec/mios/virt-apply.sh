#!/bin/bash
# AI-hint: Apply Xbox VM Config + Manual Secure Boot Key Enrollment
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}    Xbox VM: Final Configuration + Secure Boot Fix${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}\n"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Run as root: sudo $0${NC}\n"
    exit 1
fi

XML_FILE="Xbox-Final-NoAutoSelect.xml"

if [ ! -f "$XML_FILE" ]; then
    echo -e "${RED}XML file not found: $XML_FILE${NC}"
    exit 1
fi

echo -e "${YELLOW}This script will:${NC}"
echo -e "  1. Apply optimized CPU pinning (12 cores)"
echo -e "  2. Use explicit OVMF paths (no autoselection)"
echo -e "  3. ${BOLD}${GREEN}Manually enroll Vendor Secure Boot keys${NC}"
echo -e "  4. Optimize network, audio, and other devices"
echo

read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 0
fi

echo -e "\n${BLUE}[1/6] Checking/installing virt-firmware...${NC}"
if ! command -v virt-fw-vars &>/dev/null; then
    echo -e "${RED}[x] virt-firmware is missing!${NC}"
    echo -e "${YELLOW}This tool is required. Please ensure 'virt-firmware' and 'python3-cryptography' are listed in specs/engineering/2026-04-26-Artifact-ENG-001-Packages.md and rebuild the image.${NC}"
    exit 1
else
    echo -e "${GREEN}[ok] Already installed${NC}"
fi

echo -e "\n${BLUE}[2/6] Stopping VM...${NC}"
VM_STATE=$(virsh domstate Xbox 2>/dev/null || echo "Not found")
if [ "$VM_STATE" == "running" ]; then
    virsh shutdown Xbox
    sleep 5
fi
echo -e "${GREEN}[ok] Stopped${NC}"

echo -e "\n${BLUE}[3/6] Backing up...${NC}"
BACKUP_DIR="$HOME/xbox-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
virsh dumpxml Xbox > "$BACKUP_DIR/Xbox.xml" 2>/dev/null || true

NVRAM="/var/lib/libvirt/qemu/nvram/Xbox_VARS.fd"
if [ -f "$NVRAM" ]; then
    cp "$NVRAM" "$BACKUP_DIR/Xbox_VARS.fd"
fi
echo -e "${GREEN}[ok] Backup: $BACKUP_DIR${NC}"

echo -e "\n${BLUE}[4/6] Applying new configuration...${NC}"

_UUID="${MIOS_VM_UUID:-$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "67c67c35-3da2-4595-a578-e0ef1998ae43")}"
_MAC="${MIOS_VM_MAC:-52:54:00:e4:0a:3f}" # Keep stable default if not provided

TEMP_XML="/tmp/Xbox-final-rendered.xml"
sed -e "s|{{MIOS_VM_UUID}}|$_UUID|g" \
    -e "s|{{MIOS_VM_MAC}}|$_MAC|g" \
    "$XML_FILE" > "$TEMP_XML"
# Looking Glass profile from mios.toml [vm.looking_glass] + [vm.win11].vcpus: SPICE on the fixed
# port the client dials, one spicevmc channel, SPICE audio, SMT topology + topoext, localtime RTC.
/usr/libexec/mios/vfio/setup-looking-glass.py --render "$TEMP_XML" "$TEMP_XML" --vcpus template

if virsh define "$TEMP_XML"; then
    echo -e "${GREEN}[ok] Configuration applied${NC}"
else
    echo -e "${RED}[x] Failed${NC}"
    exit 1
fi

echo -e "\n${BLUE}[5/6] Starting VM to create fresh NVRAM...${NC}"
virsh start Xbox
sleep 3
virsh shutdown Xbox
echo "Waiting for clean shutdown"
sleep 5

if [ ! -f "$NVRAM" ]; then
    echo -e "${RED}[x] NVRAM not created${NC}"
    exit 1
fi
echo -e "${GREEN}[ok] Fresh NVRAM created${NC}"

echo -e "\n${BLUE}[6/6] Enrolling Vendor Secure Boot keys...${NC}"
echo -e "${CYAN}Using virt-fw-vars to inject Vendor keys...${NC}"

TEMP_NVRAM="/tmp/xbox-nvram-temp.fd"
cp "$NVRAM" "$TEMP_NVRAM"

if virt-fw-vars --input "$TEMP_NVRAM" \
                --output "$TEMP_NVRAM" \
                --enroll-redhat \
                --secure-boot 2>&1 | tee /tmp/enrollment.log; then
    cp "$TEMP_NVRAM" "$NVRAM"
    rm "$TEMP_NVRAM"
    echo -e "${GREEN}[ok] Vendor keys enrolled!${NC}"
else
    echo -e "${RED}[x] Enrollment failed${NC}"
    echo -e "${YELLOW}Log: /tmp/enrollment.log${NC}"
    rm "$TEMP_NVRAM"
    exit 1
fi

echo -e "\n${BLUE}Starting VM with enrolled keys...${NC}"
virsh start Xbox

echo -e "\n${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}                  Success!${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════════${NC}\n"

echo -e "${YELLOW}Configuration Summary:${NC}"
echo -e "  ${CYAN}[ok]${NC} CPU: 12 vCPUs pinned to C2-7, C18-23"
echo -e "  ${CYAN}[ok]${NC} Emulator: C0-1, C16-17"
echo -e "  ${CYAN}[ok]${NC} Topology: 6 cores × 2 threads"
echo -e "  ${CYAN}[ok]${NC} ${BOLD}${GREEN}Secure Boot: Vendor keys enrolled${NC}"
echo -e "  ${CYAN}[ok]${NC} Network: virtio (high performance)"
echo -e "  ${CYAN}[ok]${NC} Display/audio: SPICE for Looking Glass ([vm.looking_glass])"
echo -e "  ${CYAN}[ok]${NC} Memory balloon: none"
echo

echo -e "${YELLOW}Verify in Windows:${NC}"
echo -e "  ${CYAN}msinfo32${NC} → Secure Boot State = ${GREEN}On${NC}"
echo -e "  ${CYAN}PowerShell: Confirm-SecureBootUEFI${NC} → ${GREEN}True${NC}"
echo

echo -e "${YELLOW}Backup:${NC} ${CYAN}$BACKUP_DIR${NC}\n"
