#!/bin/bash
# AI-hint: Diagnoses Secure Boot OVMF enrollment by CONTENT, not filenames: parses every OVMF_VARS varstore under /usr/share (raw and qcow2) and reports enrolled versus blank, plus which CODE images are Secure Boot capable. Read-only.
# AI-related: find-ovmf-firmware.sh, get-secureboot-ovmf.sh, fix-ovmf-enrollment.sh
# Enrolled = live PK/KEK/db signature lists + SecureBootEnable, per the EDK2 parser sourced from find-ovmf-firmware.sh.

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

echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${CYAN}   Secure Boot OVMF Enrollment Checker (content-verified)${NC}"
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${NC}\n"

echo -e "${YELLOW}The contract:${NC}"
echo -e "  Enrollment is proven by varstore CONTENT (live PK/KEK/db signature lists in their UEFI namespaces, plus enable intent),"
echo -e "  never by a filename. A 'secboot'-named VARS can be blank, and a blank"
echo -e "  template copied to a secboot name enrolls nothing. OVMF never"
echo -e "  self-enrolls on first boot; libvirt never enrolls keys either.\n"

SHARE=$(ovmf_share_root)

echo -e "${BLUE}[1] Secure Boot capable CODE images:${NC}\n"
code_files=$(find "$SHARE/edk2" "$SHARE/OVMF" -type f \( -name 'OVMF_CODE*' -o -name 'OVMF*.fd' -o -name 'OVMF*.qcow2' \) 2>/dev/null | sort -u)
code_count=0
while IFS= read -r f; do
    case "$(basename "$f")" in *CODE*) ;; *) continue ;; esac
    code_count=$((code_count + 1))
    cap=$(ovmf_sb_capability "$f")
    case "$cap" in
        yes*) echo -e "  ${GREEN}[ok]${NC} $f - capable: $cap" ;;
        no*)  echo -e "  ${YELLOW}[i]${NC} $f - $cap" ;;
        *)    echo -e "  ${RED}[?]${NC} $f - $cap" ;;
    esac
done <<< "$code_files"
[ $code_count -eq 0 ] && echo -e "  ${RED}[x] No OVMF CODE images found under $SHARE${NC}"
echo

echo -e "${BLUE}[2] Enrollment state of every VARS varstore (content-parsed):${NC}\n"
vars_files=$(find "$SHARE/edk2" "$SHARE/OVMF" -type f \( -name 'OVMF_VARS*' -o -name '*VARS*.fd' -o -name '*VARS*.qcow2' \) 2>/dev/null | sort -u)
enrolled_path=""
enrolled_list=""
unknown_count=0
blank_count=0
while IFS= read -r f; do
    state=$(ovmf_vars_enrollment "$f")
    size=$(ovmf_human_size "$(ovmf_file_size "$f")")
    case "$state" in
        ENROLLED*)
            echo -e "  ${GREEN}[ok]${NC} ENROLLED  $f ($size) - $state"
            enrolled_list+="$f"$'\n'
            if [ -z "$enrolled_path" ]; then enrolled_path="$f"; fi
            ;;
        BLANK*)
            blank_count=$((blank_count + 1))
            echo -e "  ${YELLOW}[!]${NC} BLANK     $f ($size) - not enrolled (usable template; keys must be enrolled or obtained)"
            ;;
        *)
            unknown_count=$((unknown_count + 1))
            echo -e "  ${RED}[?]${NC} UNKNOWN   $f ($size) - $state"
            ;;
    esac
done <<< "$vars_files"
[ -z "$vars_files" ] && echo -e "  ${RED}[x] No OVMF VARS files found under $SHARE${NC}"
echo

echo -e "${BLUE}[3] Firmware descriptor pairs (libvirt autoselection DB):${NC}\n"
desc_count=0
while IFS=$'\t' read -r json code vars feats fmt desc; do
    desc_count=$((desc_count + 1))
    enrolled=""
    case ",$feats," in *,enrolled-keys,*) enrolled=" ${GREEN}[enrolled-keys]${NC}" ;; esac
    echo -e "  ${BOLD}$(basename "$json")${NC}:$enrolled $desc"
    echo -e "    ${CYAN}CODE:${NC} $code"
    echo -e "    ${CYAN}VARS:${NC} $vars"
done < <(ovmf_descriptor_pairs)
[ $desc_count -eq 0 ] && echo -e "  ${YELLOW}(none found in $(ovmf_fwdesc_dir))${NC}"
echo

echo -e "${BOLD}${YELLOW}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${YELLOW}                   DIAGNOSIS${NC}"
echo -e "${BOLD}${YELLOW}═══════════════════════════════════════════════════════${NC}\n"

if [ -n "$enrolled_path" ]; then
    echo -e "${GREEN}[ok] GOOD NEWS: a content-verified ENROLLED varstore exists:${NC}"
    echo -e "  File: ${CYAN}$enrolled_path${NC}"
    [ $blank_count -gt 0 ] && echo -e "  ${YELLOW}($blank_count blank/unenrolled varstores also present - they are templates, not enrolled stores)${NC}"
    echo -e "\n${YELLOW}Fix: use the enrolled file as your NVRAM template (or use autoselection):${NC}"
    cat <<XMLHINT
  <os firmware='efi'>
    <firmware>
      <feature enabled='yes' name='secure-boot'/>
      <feature enabled='yes' name='enrolled-keys'/>
    </firmware>
  </os>
XMLHINT
    echo -e "  ${YELLOW}(autoselection requires a descriptor with enrolled-keys; libvirt does NOT enroll keys itself)${NC}"
else
    echo -e "${RED}[x] PROBLEM: NO content-verified enrolled varstore found.${NC}"
    if [ $unknown_count -gt 0 ]; then
        echo -e "${YELLOW}($unknown_count varstores could not be parsed - install python3 and python3-cryptography for content verification)${NC}"
    fi
    echo -e "${YELLOW}Blank templates exist but enrolling requires one of:${NC}"
    echo -e "  1. sudo dnf install edk2-ovmf   (modern Fedora ships OVMF_VARS.secboot.fd enrolled)"
    echo -e "  2. virt-fw-vars --input COPY --output COPY --enroll-redhat --secure-boot"
    echo -e "     (enrolls MS/RH vendor keys offline - MiOS ships virt-firmware)"
    echo -e "  3. tools/fix-ovmf-enrollment.sh  (guided, verification-gated repair)"
fi
echo

echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${NC}\n"

cat > /tmp/ovmf-diagnosis.txt << EOF
OVMF Secure Boot Diagnosis (content-verified)
=============================================
Date: $(date)
Share root scanned: $SHARE
Descriptors: $(ovmf_fwdesc_dir)

Enrolled varstores (content-verified):
${enrolled_list:-NONE}

Code images analyzed:
EOF
while IFS= read -r f; do
    case "$(basename "$f")" in *CODE*) echo "  $f -> $(ovmf_sb_capability "$f")" >> /tmp/ovmf-diagnosis.txt ;; esac
done <<< "$code_files"
while IFS= read -r f; do
    echo "  $f -> $(ovmf_vars_enrollment "$f")" >> /tmp/ovmf-diagnosis.txt
done <<< "$vars_files"

if [ -n "$enrolled_path" ]; then
    echo "Recommendation: use $enrolled_path as NVRAM template" >> /tmp/ovmf-diagnosis.txt
else
    echo "Recommendation: obtain/enroll a varstore (dnf install edk2-ovmf, or virt-fw-vars --enroll-redhat); see fix-ovmf-enrollment.sh" >> /tmp/ovmf-diagnosis.txt
fi

echo -e "${GREEN}[ok] Report saved to: ${CYAN}/tmp/ovmf-diagnosis.txt${NC}\n"

# Exit code: 0 = verified enrolled varstore exists, 1 = none, 2 = undeterminable coverage.
[ -n "$enrolled_path" ] && exit 0
[ $unknown_count -gt 0 ] && [ $blank_count -eq 0 ] && exit 2
exit 1
