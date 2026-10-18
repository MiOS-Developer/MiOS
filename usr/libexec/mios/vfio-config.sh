#!/bin/bash
# AI-hint: Universal VFIO PCIe Device Isolation Configurator AI-functions: log_info, log_success, log_warning, log_error, log_header, check_root, detect_bootlo...
# AI-doc: usr/share/doc/mios/manual/mios.md

set -euo pipefail

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m' # No Color

readonly VFIO_CONF="/etc/modprobe.d/vfio.conf"
readonly MKINITCPIO_CONF="/etc/mkinitcpio.conf"
readonly DRACUT_CONF="/etc/dracut.conf.d/vfio.conf"
BACKUP_SUFFIX=".backup-$(date +%Y%m%d-%H%M%S)"
readonly BACKUP_SUFFIX

declare -a SELECTED_DEVICES=()
declare -a SELECTED_IDS=()
declare -a SELECTED_DRIVERS=()

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[[OK]]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[[WARN]]${NC} $1"; }
log_error() { echo -e "${RED}[[ERR]]${NC} $1"; }
log_header() {
    echo ""
    echo -e "${CYAN}+****************************************************************+${NC}"
    echo -e "${CYAN}|${NC} ${BOLD}$1${NC}"
    echo -e "${CYAN}+****************************************************************+${NC}"
    echo ""
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        echo "Usage: sudo $0"
        exit 1
    fi
}

detect_bootloader() {
    log_info "Detecting bootloader..."

    if [[ -d /boot/loader/entries ]] && command -v bootctl &>/dev/null; then
        BOOTLOADER="systemd-boot"
        BOOT_ENTRIES_DIR="/boot/loader/entries"
    elif [[ -f /boot/grub/grub.cfg ]] || [[ -f /boot/grub2/grub.cfg ]]; then
        BOOTLOADER="grub"
        GRUB_CONF="/etc/default/grub"
    elif [[ -f /boot/refind_linux.conf ]] || [[ -f /boot/EFI/refind/refind.conf ]]; then
        BOOTLOADER="refind"
    else
        log_warning "Could not detect bootloader automatically"
        echo "Please select your bootloader:"
        echo "  1) systemd-boot"
        echo "  2) GRUB"
        echo "  3) rEFInd"
        echo "  4) Other/Manual"
        read -p "Selection [1-4]: " BOOTLOADER_CHOICE

        case $BOOTLOADER_CHOICE in
            1) BOOTLOADER="systemd-boot"; BOOT_ENTRIES_DIR="/boot/loader/entries" ;;
            2) BOOTLOADER="grub"; GRUB_CONF="/etc/default/grub" ;;
            3) BOOTLOADER="refind" ;;
            4) BOOTLOADER="manual" ;;
            *) log_error "Invalid selection"; exit 1 ;;
        esac
    fi

    log_success "Detected bootloader: $BOOTLOADER"
}

detect_cpu_and_iommu() {
    log_info "Detecting CPU and IOMMU support..."

    CPU_VENDOR=$(lscpu | grep "Vendor ID" | awk '{print $3}')

    case "$CPU_VENDOR" in
        AuthenticAMD)
            IOMMU_PARAM="amd_iommu=on"
            IOMMU_TYPE="AMD-Vi"
            ;;
        GenuineIntel)
            IOMMU_PARAM="intel_iommu=on"
            IOMMU_TYPE="Intel VT-d"
            ;;
        *)
            log_warning "Unknown CPU vendor: $CPU_VENDOR"
            IOMMU_PARAM="iommu=on"
            IOMMU_TYPE="Generic"
            ;;
    esac

    log_success "CPU Vendor: $CPU_VENDOR"

    if dmesg | grep -iq "$IOMMU_TYPE"; then
        log_success "IOMMU ($IOMMU_TYPE) detected and initialized"
    else
        log_warning "IOMMU not detected - ensure it's enabled in BIOS/UEFI"
    fi
}

detect_initramfs() {
    if [[ -f /etc/mkinitcpio.conf ]]; then
        INITRAMFS="mkinitcpio"
    elif command -v dracut &>/dev/null; then
        INITRAMFS="dracut"
    else
        log_warning "Could not detect initramfs system"
        INITRAMFS="unknown"
    fi
    log_info "Initramfs system: $INITRAMFS"
}

get_all_pcie_devices() {
    local pcie_devices=()

    while IFS= read -r line; do
        pcie_devices+=("$line")
    done < <(lspci -nn | grep -E "VGA compatible controller|3D controller|Display controller")

    while IFS= read -r line; do
        pcie_devices+=("$line")
    done < <(lspci -nn | grep -i "Audio device.*NVIDIA\|Audio device.*AMD\|Audio.*Intel.*Display")

    while IFS= read -r line; do
        pcie_devices+=("$line")
    done < <(lspci -nn | grep -E "USB.*NVIDIA|USB.*AMD")

    printf '%s\n' "${pcie_devices[@]}"
}

display_devices() {
    local -n dev_array=$1
    local counter=1

    echo -e "${CYAN}+****************************************************************+${NC}"
    echo -e "${CYAN}|${NC} ${BOLD}Available PCIe Devices${NC}"
    echo -e "${CYAN}+****************************************************************+${NC}"

    for device in "${dev_array[@]}"; do
        local pci_addr device_id device_desc current_driver
        pci_addr=$(echo "$device" | awk '{print $1}') || true
        device_id=$(echo "$device" | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])') || true
        device_desc=$(echo "$device" | sed 's/^[^ ]* //' | sed 's/\[.*\]//') || true

        current_driver=$(lspci -nnk -s "$pci_addr" | grep "Kernel driver in use:" | awk '{print $5}') || true
        [[ -z "$current_driver" ]] && current_driver="${YELLOW}none${NC}"

        local iommu_group="N/A"
        if [[ -L "/sys/bus/pci/devices/0000:$pci_addr/iommu_group" ]]; then
            iommu_group=$(basename "$(readlink "/sys/bus/pci/devices/0000:$pci_addr/iommu_group")")
        fi

        local color=$NC
        if echo "$device_desc" | grep -qi "nvidia"; then
            color=$GREEN
        elif echo "$device_desc" | grep -qi "amd"; then
            color=$RED
        elif echo "$device_desc" | grep -qi "intel"; then
            color=$BLUE
        fi

        echo -e "${CYAN}|${NC} ${BOLD}${counter})${NC} ${color}${device_desc}${NC}"
        echo -e "${CYAN}|${NC}    PCI: ${pci_addr} | ID: ${device_id} | IOMMU: ${iommu_group}"
        echo -e "${CYAN}|${NC}    Driver: ${current_driver}"
        echo -e "${CYAN}+----------------------------------------------------------------+${NC}"

        counter=$((counter + 1))
    done

    echo -e "${CYAN}+****************************************************************+${NC}"
}

select_devices() {
    local -n devices=$1

    log_header "Device Selection"

    display_devices devices

    echo ""
    echo -e "${BOLD}Selection Options:${NC}"
    echo "  * Enter device numbers separated by spaces"
    echo "  * Enter 'a' to select all devices"
    echo "  * Enter 'q' to quit"
    echo ""

    while true; do
        read -p "Select devices for VFIO isolation: " selection

        if [[ "$selection" == "q" ]]; then
            log_info "Exiting..."
            exit 0
        elif [[ "$selection" == "a" ]]; then
            for i in "${!devices[@]}"; do
                local device="${devices[$i]}"
                local pci_addr device_id current_driver
                pci_addr=$(echo "$device" | awk '{print $1}') || true
                device_id=$(echo "$device" | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])') || true
                current_driver=$(lspci -nnk -s "$pci_addr" | grep "Kernel driver in use:" | awk '{print $5}') || true

                SELECTED_DEVICES+=("$pci_addr")
                SELECTED_IDS+=("$device_id")
                SELECTED_DRIVERS+=("${current_driver:-none}")
            done
            break
        else
            local valid=true
            for num in $selection; do
                if ! [[ "$num" =~ ^[0-9]+$ ]] || [ "$num" -lt 1 ] || [ "$num" -gt "${#devices[@]}" ]; then
                    log_error "Invalid selection: $num"
                    valid=false
                    break
                fi
            done

            if $valid; then
                for num in $selection; do
                    local idx=$((num - 1))
                    local device="${devices[$idx]}"
                    local pci_addr device_id current_driver
                    pci_addr=$(echo "$device" | awk '{print $1}') || true
                    device_id=$(echo "$device" | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])') || true
                    current_driver=$(lspci -nnk -s "$pci_addr" | grep "Kernel driver in use:" | awk '{print $5}') || true

                    SELECTED_DEVICES+=("$pci_addr")
                    SELECTED_IDS+=("$device_id")
                    SELECTED_DRIVERS+=("${current_driver:-none}")
                done
                break
            fi
        fi
    done

    echo ""
    log_success "Selected ${#SELECTED_DEVICES[@]} device(s):"
    for i in "${!SELECTED_DEVICES[@]}"; do
        echo "  * ${SELECTED_DEVICES[$i]} [${SELECTED_IDS[$i]}] - Driver: ${SELECTED_DRIVERS[$i]}"
    done
}

detect_related_devices() {
    log_header "Related Device Detection"

    log_info "Scanning for related devices in IOMMU groups..."

    local -A seen_groups
    declare -a additional_devices=()

    for pci_addr in "${SELECTED_DEVICES[@]}"; do
        if [[ -L "/sys/bus/pci/devices/0000:$pci_addr/iommu_group" ]]; then
            local group
            group=$(basename "$(readlink "/sys/bus/pci/devices/0000:$pci_addr/iommu_group")") || true

            if [[ -z "${seen_groups[$group]}" ]]; then
                seen_groups[$group]=1

                local group_devices
                group_devices=$(ls "/sys/kernel/iommu_groups/$group/devices/" 2>/dev/null) || true

                for dev in $group_devices; do
                    local dev_addr="${dev#0000:}"

                    local already_selected=false
                    for selected in "${SELECTED_DEVICES[@]}"; do
                        if [[ "$selected" == "$dev_addr" ]]; then
                            already_selected=true
                            break
                        fi
                    done

                    if ! $already_selected; then
                        local dev_info dev_id
                        dev_info=$(lspci -nns "$dev_addr") || true
                        dev_id=$(echo "$dev_info" | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])') || true

                        additional_devices+=("$dev_addr|$dev_id|$dev_info")
                    fi
                done
            fi
        fi
    done

    if [[ ${#additional_devices[@]} -gt 0 ]]; then
        log_warning "Found ${#additional_devices[@]} related device(s) in same IOMMU group(s):"
        echo ""

        for entry in "${additional_devices[@]}"; do
            IFS='|' read -r addr id info <<< "$entry"
            echo "  * $info"
        done

        echo ""
        read -p "Include these related devices? [Y/n]: " include_related

        if [[ ! "$include_related" =~ ^[Nn] ]]; then
            for entry in "${additional_devices[@]}"; do
                IFS='|' read -r addr id info <<< "$entry"
                SELECTED_DEVICES+=("$addr")
                SELECTED_IDS+=("$id")
                SELECTED_DRIVERS+=("auto-detected")
            done
            log_success "Added ${#additional_devices[@]} related device(s)"
        fi
    else
        log_success "No additional related devices found"
    fi
}

analyze_iommu_groups() {
    log_header "IOMMU Group Analysis"

    for pci_addr in "${SELECTED_DEVICES[@]}"; do
        if [[ -L "/sys/bus/pci/devices/0000:$pci_addr/iommu_group" ]]; then
            local group
            group=$(basename "$(readlink "/sys/bus/pci/devices/0000:$pci_addr/iommu_group")") || true
            local group_size
            group_size=$(ls "/sys/kernel/iommu_groups/$group/devices/" 2>/dev/null | wc -l) || true

            echo -e "${CYAN}+****************************************************************+${NC}"
            echo -e "${CYAN}|${NC} Device: $pci_addr | IOMMU Group: $group | Size: $group_size device(s)"
            echo -e "${CYAN}+****************************************************************+${NC}"

            for dev in /sys/kernel/iommu_groups/$group/devices/*; do
                local dev_id dev_info
                dev_id=$(basename "$dev") || true
                dev_info=$(lspci -nns "${dev_id}" | sed 's/^[^ ]* //') || true
                echo -e "${CYAN}|${NC} $dev_info"
            done

            echo -e "${CYAN}+****************************************************************+${NC}"

            if [[ $group_size -gt 5 ]]; then
                log_warning "Large IOMMU group detected ($group_size devices)"
                echo "  Consider enabling ACS override if passthrough fails"
            fi

            echo ""
        fi
    done
}

configure_modprobe() {
    log_header "Modprobe Configuration"

    log_info "Creating VFIO modprobe configuration..."

    if [[ -f "$VFIO_CONF" ]]; then
        cp "$VFIO_CONF" "${VFIO_CONF}${BACKUP_SUFFIX}"
        log_info "Backed up existing config to ${VFIO_CONF}${BACKUP_SUFFIX}"
    fi

    local vfio_ids
    vfio_ids=$(IFS=,; echo "${SELECTED_IDS[*]}")

    local has_nvidia=false
    local has_amd=false
    local has_intel=false

    for driver in "${SELECTED_DRIVERS[@]}"; do
        [[ "$driver" =~ nvidia ]] && has_nvidia=true
        [[ "$driver" =~ amdgpu|radeon ]] && has_amd=true
        [[ "$driver" =~ i915|xe ]] && has_intel=true
    done

    cat > "$VFIO_CONF" << EOF

options vfio-pci ids=$vfio_ids

EOF

    if $has_nvidia; then
        cat >> "$VFIO_CONF" << EOF
softdep nvidia pre: vfio-pci
softdep nvidia_drm pre: vfio-pci
softdep nvidia_modeset pre: vfio-pci
softdep nvidia_uvm pre: vfio-pci
softdep nouveau pre: vfio-pci

EOF
    fi

    if $has_amd; then
        cat >> "$VFIO_CONF" << EOF
softdep amdgpu pre: vfio-pci
softdep radeon pre: vfio-pci

EOF
    fi

    if $has_intel; then
        cat >> "$VFIO_CONF" << EOF
softdep i915 pre: vfio-pci
softdep xe pre: vfio-pci

EOF
    fi

    cat >> "$VFIO_CONF" << EOF

EOF

    log_success "Created $VFIO_CONF"

    echo ""
    echo -e "${BOLD}Configuration preview:${NC}"
    grep -v "^#" "$VFIO_CONF" | grep -v "^$" | sed 's/^/  /'
}

configure_initramfs() {
    log_header "Initramfs Configuration"

    case "$INITRAMFS" in
        mkinitcpio)
            configure_mkinitcpio
            ;;
        dracut)
            configure_dracut
            ;;
        *)
            log_warning "Unknown initramfs system - skipping automatic configuration"
            log_info "Manually add: vfio_pci vfio vfio_iommu_type1 to your initramfs"
            ;;
    esac
}

configure_mkinitcpio() {
    log_info "Configuring mkinitcpio..."

    if [[ -f "$MKINITCPIO_CONF" ]]; then
        cp "$MKINITCPIO_CONF" "${MKINITCPIO_CONF}${BACKUP_SUFFIX}"
        log_info "Backed up $MKINITCPIO_CONF"
    fi

    if grep -q "vfio_pci" "$MKINITCPIO_CONF"; then
        log_info "VFIO modules already present in mkinitcpio.conf"
    else
        log_info "Adding VFIO modules to MODULES array..."

        sed -i '/^MODULES=/ s/)/ vfio_pci vfio vfio_iommu_type1)/' "$MKINITCPIO_CONF"

        log_success "Added VFIO modules to mkinitcpio.conf"
    fi

    local hooks_line
    hooks_line=$(grep "^HOOKS=" "$MKINITCPIO_CONF") || true
    if [[ "$hooks_line" =~ kms.*modconf ]]; then
        log_warning "Hook order issue: 'modconf' should come before 'kms'"
        log_info "Current: $hooks_line"

        read -p "Fix hook order automatically? [Y/n]: " fix_hooks
        if [[ ! "$fix_hooks" =~ ^[Nn] ]]; then
            sed -i '/^HOOKS=/ s/\(.*\)kms\(.*\)modconf\(.*\)/\1modconf\2kms\3/' "$MKINITCPIO_CONF"
            log_success "Fixed hook order"
        fi
    else
        log_success "Hook order is correct (modconf before kms)"
    fi

    log_info "Regenerating initramfs..."
    if mkinitcpio -P; then
        log_success "Initramfs regenerated successfully"
    else
        log_error "Failed to regenerate initramfs"
        return 1
    fi
}

configure_dracut() {
    log_info "Configuring dracut..."

    mkdir -p "$(dirname "$DRACUT_CONF")"

    cat > "$DRACUT_CONF" << EOF

add_drivers+=" vfio vfio_iommu_type1 vfio_pci "
EOF

    log_success "Created $DRACUT_CONF"

    log_info "Regenerating dracut initramfs..."
    if dracut --force; then
        log_success "Dracut initramfs regenerated successfully"
    else
        log_error "Failed to regenerate dracut initramfs"
        return 1
    fi
}

configure_bootloader() {
    log_header "Bootloader Configuration"

    local vfio_ids
    vfio_ids=$(IFS=,; echo "${SELECTED_IDS[*]}")
    local kernel_params="$IOMMU_PARAM iommu=pt vfio-pci.ids=$vfio_ids"

    log_info "Kernel parameters to add:"
    echo -e "  ${GREEN}$kernel_params${NC}"
    echo ""

    case "$BOOTLOADER" in
        systemd-boot)
            configure_systemd_boot "$kernel_params"
            ;;
        grub)
            configure_grub "$kernel_params"
            ;;
        refind)
            configure_refind "$kernel_params"
            ;;
        manual)
            show_manual_bootloader_config "$kernel_params"
            ;;
        *)
            log_error "Unknown bootloader: $BOOTLOADER"
            return 1
            ;;
    esac
}

configure_systemd_boot() {
    local params="$1"

    log_info "Configuring systemd-boot..."

    local entries
    entries=$(find "$BOOT_ENTRIES_DIR" -name "*.conf" 2>/dev/null | grep -v "backup" | sort) || true

    if [[ -z "$entries" ]]; then
        log_error "No systemd-boot entries found in $BOOT_ENTRIES_DIR"
        return 1
    fi

    echo "Found boot entries:"
    echo "$entries" | nl -w2 -s'. '
    echo ""

    read -p "Which entry to configure? (number or 'all'): " entry_selection

    local selected_entries
    if [[ "$entry_selection" == "all" ]]; then
        selected_entries="$entries"
    else
        selected_entries=$(echo "$entries" | sed -n "${entry_selection}p")
        if [[ -z "$selected_entries" ]]; then
            log_error "Invalid selection"
            return 1
        fi
    fi

    for entry in $selected_entries; do
        log_info "Processing: $(basename $entry)"

        cp "$entry" "${entry}${BACKUP_SUFFIX}"
        log_info "Backed up to ${entry}${BACKUP_SUFFIX}"

        if grep -q "vfio-pci.ids=" "$entry"; then
            log_warning "VFIO parameters already present, updating..."
            sed -i 's/\(amd_iommu\|intel_iommu\)=[^ ]* //g' "$entry"
            sed -i 's/iommu=[^ ]* //g' "$entry"
            sed -i 's/vfio-pci\.ids=[^ ]* //g' "$entry"
        fi

        sed -i "/^options / s/$/ $params/" "$entry"
        log_success "Updated: $(basename $entry)"
    done

    log_info "Updating systemd-boot..."
    bootctl update
    log_success "systemd-boot updated"
}

configure_grub() {
    local params="$1"

    log_info "Configuring GRUB..."

    if [[ ! -f "$GRUB_CONF" ]]; then
        log_error "GRUB configuration not found at $GRUB_CONF"
        return 1
    fi

    cp "$GRUB_CONF" "${GRUB_CONF}${BACKUP_SUFFIX}"
    log_info "Backed up $GRUB_CONF"

    if grep -q "GRUB_CMDLINE_LINUX_DEFAULT" "$GRUB_CONF"; then
        log_info "Updating GRUB_CMDLINE_LINUX_DEFAULT..."

        sed -i 's/\(amd_iommu\|intel_iommu\)=[^ "]* //g' "$GRUB_CONF"
        sed -i 's/iommu=[^ "]* //g' "$GRUB_CONF"
        sed -i 's/vfio-pci\.ids=[^ "]* //g' "$GRUB_CONF"

        sed -i "/GRUB_CMDLINE_LINUX_DEFAULT/ s/\"$/ $params\"/" "$GRUB_CONF"
    else
        echo "GRUB_CMDLINE_LINUX_DEFAULT=\"$params\"" >> "$GRUB_CONF"
    fi

    log_success "Updated $GRUB_CONF"

    log_info "Regenerating GRUB configuration..."

    if command -v grub-mkconfig &>/dev/null; then
        if [[ -d /boot/grub ]]; then
            grub-mkconfig -o /boot/grub/grub.cfg
        elif [[ -d /boot/grub2 ]]; then
            grub-mkconfig -o /boot/grub2/grub.cfg
        fi
        log_success "GRUB configuration regenerated"
    elif command -v grub2-mkconfig &>/dev/null; then
        grub2-mkconfig -o /boot/grub2/grub.cfg
        log_success "GRUB2 configuration regenerated"
    else
        log_error "Could not find grub-mkconfig or grub2-mkconfig"
        return 1
    fi
}

configure_refind() {
    local params="$1"

    log_info "Configuring rEFInd..."
    log_warning "rEFInd configuration is typically manual"

    echo ""
    echo "Add these parameters to your boot stanza in refind_linux.conf:"
    echo -e "${GREEN}$params${NC}"
    echo ""
    echo "Example:"
    echo '  "Boot with VFIO" "root=PARTUUID=xxx rw '"$params"'"'
    echo ""

    read -p "Press Enter to continue..."
}

show_manual_bootloader_config() {
    local params="$1"

    log_header "Manual Bootloader Configuration"

    echo "Add these kernel parameters to your bootloader configuration:"
    echo ""
    echo -e "  ${GREEN}$params${NC}"
    echo ""
    echo "Depending on your bootloader:"
    echo ""
    echo "Systemd-boot: Edit /boot/loader/entries/*.conf"
    echo "  Add to 'options' line"
    echo ""
    echo "GRUB: Edit /etc/default/grub"
    echo "  Add to GRUB_CMDLINE_LINUX_DEFAULT"
    echo "  Run: grub-mkconfig -o /boot/grub/grub.cfg"
    echo ""
    echo "REFInd: Edit /boot/refind_linux.conf"
    echo "  Add to boot options string"
    echo ""

    read -p "Press Enter when done..."
}

create_helper_scripts() {
    log_header "Helper Scripts"

    log_info "Creating IOMMU group viewer..."

    cat > /usr/local/bin/iommu-groups << 'EOF'
shopt -s nullglob
for g in $(find /sys/kernel/iommu_groups/* -maxdepth 0 -type d | sort -V); do
    echo -e "\033[1;34mIOMMU Group ${g##*/}:\033[0m"
    for d in $g/devices/*; do
        echo "  $"
    done
done
EOF

    chmod +x /usr/local/bin/iommu-groups
    log_success "Created iommu-groups command"

    log_info "Creating VFIO verification script..."

    local vfio_ids
    vfio_ids=$(IFS=,; echo "${SELECTED_IDS[*]}")

    cat > /usr/local/bin/vfio-verify << EOF
echo "VFIO Configuration Verification"
echo "Checking VFIO modules"
for mod in vfio vfio_pci vfio_iommu_type1; do
    if lsmod | grep -q "^\$mod"; then
        echo "  [OK] \$mod loaded"
    else
        echo "  [ERR] \$mod NOT loaded"
    fi
done
echo ""

echo "Checking device binding"
for id in ${vfio_ids//,/ }; do
    echo "Device: \$id"
    lspci -nnk -d "\$id" | grep -E "driver in use|Kernel modules"
    echo ""
done

echo "VFIO device nodes:"
ls -la /dev/vfio/ 2>/dev/null || echo "  No VFIO devices found"
echo ""

echo "Kernel parameters:"
cat /proc/cmdline | grep -o "iommu=[^ ]*\|vfio-pci.ids=[^ ]*" || echo "  No VFIO parameters found"
EOF

    chmod +x /usr/local/bin/vfio-verify
    log_success "Created vfio-verify command"
}

generate_summary() {
    log_header "Configuration Summary"

    echo -e "${BOLD}System Information:${NC}"
    echo "  CPU: $CPU_VENDOR"
    echo "  IOMMU: $IOMMU_TYPE"
    echo "  Bootloader: $BOOTLOADER"
    echo "  Initramfs: $INITRAMFS"
    echo ""

    echo -e "${BOLD}Isolated Devices (${#SELECTED_DEVICES[@]}):${NC}"
    for i in "${!SELECTED_DEVICES[@]}"; do
        echo "  ${SELECTED_DEVICES[$i]} [${SELECTED_IDS[$i]}]"
    done
    echo ""

    echo -e "${BOLD}Kernel Parameters:${NC}"
    local vfio_ids
    vfio_ids=$(IFS=,; echo "${SELECTED_IDS[*]}")
    echo "  $IOMMU_PARAM iommu=pt vfio-pci.ids=$vfio_ids"
    echo ""

    echo -e "${BOLD}Configuration Files:${NC}"
    echo "  Modprobe: $VFIO_CONF"
    [[ "$INITRAMFS" == "mkinitcpio" ]] && echo "  Initramfs: $MKINITCPIO_CONF"
    [[ "$INITRAMFS" == "dracut" ]] && echo "  Initramfs: $DRACUT_CONF"
    echo ""

    echo -e "${BOLD}Helper Commands:${NC}"
    echo "  iommu-groups  - View IOMMU group assignments"
    echo "  vfio-verify   - Verify VFIO configuration after reboot"
    echo ""

    echo -e "${BOLD}Next Steps:${NC}"
    echo "  1. Review the changes above"
    echo "  2. Reboot your system"
    echo "  3. Run 'vfio-verify' to confirm configuration"
    echo "  4. Check device binding: lspci -nnk -d <device-id>"
    echo "  5. Configure VM in virt-manager with PCI passthrough"
    echo ""

    echo -e "${BOLD}Rollback Instructions:${NC}"
    echo "  Restore backed up files and reboot"
    echo ""
}

main() {
    log_header "Universal VFIO PCIe Device Configurator"

    check_root
    detect_bootloader
    detect_cpu_and_iommu
    detect_initramfs

    mapfile -t ALL_DEVICES < <(get_all_pcie_devices)

    if [[ ${#ALL_DEVICES[@]} -eq 0 ]]; then
        log_error "No PCIe devices found"
        exit 1
    fi

    select_devices ALL_DEVICES

    detect_related_devices

    analyze_iommu_groups

    configure_modprobe
    configure_initramfs
    configure_bootloader
    create_helper_scripts

    generate_summary

    echo -e "${YELLOW}****************************************************************${NC}"
    read -p "Reboot now to apply changes? [y/N]: " reboot_now

    if [[ "$reboot_now" =~ ^[Yy]$ ]]; then
        log_info "Rebooting system..."
        systemctl reboot
    else
        log_warning "Remember to reboot before testing VFIO passthrough!"
        echo ""
        echo "After reboot, run: vfio-verify"
    fi
}

main "$@"
