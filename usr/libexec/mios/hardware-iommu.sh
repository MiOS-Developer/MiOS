#!/bin/bash
# AI-hint: IOMMU Group Visualizer
# AI-functions: print_banner, check_iommu, get_device_color, visualize_groups, show_gpu_details, show_usb_controllers, export_to_file, interactive_menu, main

set -euo pipefail

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly MAGENTA='\033[0;35m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m'

print_banner() {
    clear
    echo -e "${BOLD}${CYAN}"
    cat << 'EOF'
╔═══════════════════════════════════════════════════════════════╗
║                  IOMMU GROUP VISUALIZER                       ║
║            PCIe Passthrough Topology Analysis                 ║
╚═══════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}\n"
}

check_iommu() {
    if [ ! -d /sys/kernel/iommu_groups ]; then
        echo -e "${RED}[x] IOMMU not available${NC}"
        echo "Please enable IOMMU in BIOS and add kernel parameters:"
        echo "  Intel: intel_iommu=on iommu=pt"
        echo "  AMD:   amd_iommu=on iommu=pt"
        exit 1
    fi

    if ! dmesg | grep -i iommu | grep -qi enabled; then
        echo -e "${YELLOW}[!] IOMMU may not be enabled in kernel${NC}"
        echo "Check dmesg | grep -i iommu"
        echo ""
    fi
}

get_device_color() {
    local device="$1"

    case "$device" in
        *VGA*|*3D*|*Display*) echo "$MAGENTA" ;;
        *Audio*|*sound*) echo "$CYAN" ;;
        *Ethernet*|*Network*) echo "$BLUE" ;;
        *USB*) echo "$YELLOW" ;;
        *SATA*|*NVMe*|*storage*) echo "$GREEN" ;;
        *) echo "$NC" ;;
    esac
}

visualize_groups() {
    echo -e "${BOLD}${GREEN}IOMMU Group Topology${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

    local group_count=0
    local gpu_groups=()
    local isolated_gpus=()

    for d in /sys/kernel/iommu_groups/*/devices/*; do
        if [ -e "$d" ]; then
            n=${d#*/iommu_groups/*}
            n=${n%%/*}
            device_info=$(lspci -nns "${d##*/}")

            color=$(get_device_color "$device_info")

            printf "${BOLD}Group %2s:${NC} ${color}%s${NC}\n" "$n" "$device_info"

            group_count=$((group_count + 1))

            if echo "$device_info" | grep -qi "VGA\|3D"; then
                gpu_groups+=("$n")
                device_count=$(find /sys/kernel/iommu_groups/$n/devices/ -type l | wc -l)
                if [ "$device_count" -eq 2 ]; then  # GPU + its audio typically
                    isolated_gpus+=("Group $n")
                fi
            fi
        fi
    done | sort -V

    echo -e "\n${BOLD}${GREEN}Summary${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "Total IOMMU Groups: ${BOLD}$group_count${NC}"
    echo -e "GPU Groups: ${BOLD}${#gpu_groups[@]}${NC}"

    if [ ${#isolated_gpus[@]} -gt 0 ]; then
        echo -e "${GREEN}[ok] Isolated GPUs (good for passthrough):${NC}"
        for gpu in "${isolated_gpus[@]}"; do
            echo -e "  * $gpu"
        done
    else
        echo -e "${YELLOW}[!] No isolated GPUs found${NC}"
        echo "GPUs share IOMMU groups with other devices"
    fi

    echo ""
}

show_gpu_details() {
    echo -e "\n${BOLD}${MAGENTA}GPU Passthrough Analysis${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

    local gpus
    gpus=$(lspci | grep -E "VGA|3D") || true

    if [ -z "$gpus" ]; then
        echo "No GPUs detected"
        return
    fi

    while IFS= read -r gpu; do
        local pci_id gpu_name
        pci_id=$(echo "$gpu" | awk '{print $1}')
        gpu_name=$(echo "$gpu" | cut -d: -f3-)

        echo -e "${BOLD}GPU:${NC} $gpu_name"
        echo -e "${BOLD}PCI Address:${NC} $pci_id"

        for d in /sys/kernel/iommu_groups/*/devices/*; do
            if [ -e "$d" ] && [[ "$d" == *"$pci_id"* ]]; then
                n=${d#*/iommu_groups/*}
                n=${n%%/*}
                echo -e "${BOLD}IOMMU Group:${NC} $n"

                echo -e "${BOLD}Group Members:${NC}"
                for member in /sys/kernel/iommu_groups/$n/devices/*; do
                    if [ -e "$member" ]; then
                        member_id=$(basename "$member")
                        if [ "$member_id" != "$pci_id" ]; then
                            lspci -s "$member_id" | sed 's/^/  ├─ /'
                        fi
                    fi
                done

                device_count=$(find /sys/kernel/iommu_groups/$n/devices/ -type l | wc -l)
                if [ "$device_count" -le 2 ]; then
                    echo -e "  ${GREEN}[ok] Good for passthrough (isolated or with audio only)${NC}"
                else
                    echo -e "  ${YELLOW}[!] Shares group with $((device_count-1)) other device(s)${NC}"
                    echo -e "  ${YELLOW}  Consider ACS override patch if needed${NC}"
                fi

                break
            fi
        done

        echo -e "${BOLD}PCIe Link:${NC}"
        lspci -vv -s "$pci_id" 2>/dev/null | grep -E "LnkCap|LnkSta" | sed 's/^/  /'

        echo ""
    done <<< "$gpus"
}

show_usb_controllers() {
    echo -e "\n${BOLD}${YELLOW}USB Controller Groups${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

    local usb_controllers
    usb_controllers=$(lspci | grep -i usb) || true

    if [ -z "$usb_controllers" ]; then
        echo "No USB controllers detected"
        return
    fi

    while IFS= read -r controller; do
        local pci_id ctrl_name
        pci_id=$(echo "$controller" | awk '{print $1}')
        ctrl_name=$(echo "$controller" | cut -d: -f3-)

        for d in /sys/kernel/iommu_groups/*/devices/*; do
            if [ -e "$d" ] && [[ "$d" == *"$pci_id"* ]]; then
                n=${d#*/iommu_groups/*}
                n=${n%%/*}
                echo -e "${BOLD}Group $n:${NC} $ctrl_name"
                break
            fi
        done
    done <<< "$usb_controllers"

    echo ""
}

export_to_file() {
    local output
    output="$HOME/iommu-topology-$(date +%Y%m%d_%H%M%S).txt"

    {
        echo "IOMMU Topology Report"
        echo "Generated: $"
        echo "Hostname: $"
        echo "Kernel: $"
        echo ""
        echo ""

        for d in /sys/kernel/iommu_groups/*/devices/*; do
            if [ -e "$d" ]; then
                n=${d#*/iommu_groups/*}
                n=${n%%/*}
                printf 'IOMMU Group %s: ' "$n"
                lspci -nns "${d##*/}"
            fi
        done | sort -V
    } > "$output"

    echo -e "${GREEN}[ok] Topology exported to: $output${NC}"
}

interactive_menu() {
    while true; do
        echo -e "\n${BOLD}${CYAN}Options:${NC}"
        echo "  1) Show full topology"
        echo "  2) GPU analysis only"
        echo "  3) USB controllers"
        echo "  4) Export to file"
        echo "  5) Refresh"
        echo "  q) Quit"
        echo -n "Select: "

        read -r choice

        case "$choice" in
            1) print_banner; check_iommu; visualize_groups ;;
            2) show_gpu_details ;;
            3) show_usb_controllers ;;
            4) export_to_file ;;
            5) print_banner; check_iommu; visualize_groups ;;
            q|Q) echo "Goodbye"; exit 0 ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
    done
}

main() {
    print_banner
    check_iommu
    visualize_groups
    show_gpu_details
    show_usb_controllers

    echo -e "\n${BOLD}${GREEN}Passthrough Recommendations:${NC}"
    echo -e "* ${GREEN}[ok]${NC} Isolated GPU groups are ideal for passthrough"
    echo -e "* ${YELLOW}[!]${NC} Shared groups may need ACS override patch"
    echo -e "* ${BLUE}[i]${NC} GPU audio devices in same group is normal and safe"
    echo -e "* ${BLUE}[i]${NC} Check that your GPU supports reset (important!)"

    if [ "${1:-}" != "--no-menu" ]; then
        interactive_menu
    fi
}

main "$@"
