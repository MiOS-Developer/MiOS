#!/bin/bash
# AI-hint: Locates enrolled OVMF Secure Boot varstores by CONTENT (bounded EDK2 variable-store parser) across all known layouts, checks each CODE/VARS pair against qemu firmware descriptors and offers a verification-gated repair menu.
# AI-related: find-ovmf-firmware.sh, check-ovmf-enrollment.sh, fix-ovmf-enrollment.sh
# Layouts: /usr/share/edk2/ovmf (Fedora), /usr/share/edk2/x64 (kraxel), /usr/share/OVMF symlinks. Never installs a
# blank or unverified varstore, never overwrites existing files, never touches live NVRAM.

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

echo -e "${BOLD}${CYAN}════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${CYAN}   Secure Boot OVMF Locator (content-verified)${NC}"
echo -e "${BOLD}${CYAN}════════════════════════════════════════════════════════${NC}\n"

echo -e "${BLUE}Scanning for enrolled OVMF varstores...${NC}\n"

SHARE=$(ovmf_share_root)
X64_DIR="$SHARE/edk2/x64"

echo -e "${YELLOW}OVMF files under $SHARE:${NC}"
find "$SHARE/edk2" "$SHARE/OVMF" -type f \( -name 'OVMF*.fd' -o -name 'OVMF*.qcow2' \) 2>/dev/null | sort | while IFS= read -r f; do
    printf '  %-52s %10s\n' "$f" "$(ovmf_human_size "$(ovmf_file_size "$f")")"
done
echo

# The ONLY acceptable answer for "enrolled VARS": a varstore whose
# content was parsed and found to carry PK/KEK/db. Filename checks are gone.
FOUND=$(ovmf_find_enrolled_vars)

if [ -n "$FOUND" ]; then
    FOUND_PATH=$(echo "$FOUND" | head -1 | cut -f1)
    FOUND_EV=$(echo "$FOUND" | head -1 | cut -f2)
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}[ok] Content-verified enrolled varstore found!${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}\n"
    echo -e "${YELLOW}Use this file as your VM NVRAM template:${NC}"
    echo -e "  ${CYAN}$FOUND_PATH${NC}"
    echo -e "  Size: $(ovmf_human_size "$(ovmf_file_size "$FOUND_PATH")")"
    echo -e "  Evidence: $FOUND_EV"

    # Show the matching Secure Boot capable CODE for a complete pair.
    PAIR_CODE=""
    while IFS=$'\t' read -r json code vars feats fmt _desc; do
        if [ "$vars" = "$FOUND_PATH" ]; then
            ovmf_pair_status "$code" "$vars" >/dev/null || continue
            case ",$feats," in
                *,secure-boot,*)
                    PAIR_CODE=$code
                    echo -e "\n${YELLOW}Paired Secure Boot CODE (same build, per $(basename "$json")):${NC}"
                    echo -e "  ${CYAN}$code${NC} [$fmt]"
                    ;;
            esac
        fi
    done < <(ovmf_descriptor_pairs)
    if [ -z "$PAIR_CODE" ] && [ -d "$(dirname "$FOUND_PATH")" ]; then
        # No descriptor: suggest same-directory secboot CODE only if the pair
        # passes compatibility checks.
        for c in "$(dirname "$FOUND_PATH")"/*CODE*secboot*; do
            [ -f "$c" ] || continue
            status=$(ovmf_pair_status "$c" "$FOUND_PATH")
            case "$status" in
                OK*)
                    echo -e "\n${YELLOW}Compatible same-build Secure Boot CODE:${NC}"
                    echo -e "  ${CYAN}$c${NC} - $status"
                    break
                    ;;
            esac
        done
    fi
    echo
    exit 0
fi

echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
echo -e "${RED}[x] No content-verified enrolled varstore found on this system${NC}"
echo -e "${RED}════════════════════════════════════════════════════════════${NC}\n"
echo -e "${YELLOW}Firmware selection facts:${NC}"
echo -e "  * Distribution packages may ship enrolled OVMF_VARS.secboot.fd;"
echo -e "    template contents must be checked on the installed package."
echo -e "  * A secboot FILENAME never proves enrollment - only content does."
echo -e "  * Do NOT download random RPMs: hard-coded 2023 URLs in older tooling"
echo -e "    are dead (404) and mixing builds produces incompatible CODE/VARS pairs."
echo

if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}To install a verified enrolled varstore into $X64_DIR, run as root:${NC}"
    echo -e "  ${CYAN}sudo $0${NC}\n"
    echo -e "${YELLOW}Read-only alternatives:${NC}"
    echo -e "  * sudo dnf install edk2-ovmf  (ships enrolled varstore on current Fedora)"
    echo -e "  * tools/check-ovmf-enrollment.sh  (full diagnosis)"
    exit 1
fi

ovmf_repair_menu "$X64_DIR"
rc=$?
# Re-check: success means a verified enrolled varstore now exists somewhere.
if [ $rc -eq 0 ]; then
    FOUND=$(ovmf_find_enrolled_vars)
    if [ -n "$FOUND" ]; then
        echo -e "\n${GREEN}[ok] Verified enrolled varstore now available:${NC}"
        echo -e "  ${CYAN}$(echo "$FOUND" | head -1 | cut -f1)${NC}\n"
        exit 0
    fi
fi
exit 1
