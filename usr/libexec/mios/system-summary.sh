#!/bin/bash
# AI-hint: Standalone diagnostic that prints a colorized terminal report of the build/dev host - distro, kernel, uptime, CPU+virt flags, memory, GPU/NVIDIA,...
# AI-doc: usr/share/doc/mios/manual/mios.md

set -euo pipefail

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m'

print_header() {
    echo -e "\n${BOLD}${CYAN}▶ $1${NC}"
    echo -e "${CYAN}$(printf '─%.0s' {1..60})${NC}"
}

check_status() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}[ok]${NC}"
    else
        echo -e "${RED}[x]${NC}"
    fi
}

main() {
    clear
    echo -e "${BOLD}${CYAN}"
    cat << 'EOF'
╔══════════════════════════════════════════════════════════╗
║           QUICK SYSTEM SUMMARY - MiOS-Build                ║
╚══════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}\n"

    print_header "SYSTEM"
    echo -e "${BOLD}Host:${NC}     $(hostname)"
    echo -e "${BOLD}Distro:${NC}   $(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2)"
    echo -e "${BOLD}Kernel:${NC}   $(uname -r)"
    echo -e "${BOLD}Uptime:${NC}   $(uptime -p)"

    print_header "CPU"
    local cpu_model cpu_cores cpu_threads
    cpu_model=$(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | xargs) || true
    cpu_cores=$(nproc) || true
    cpu_threads=$(grep -c processor /proc/cpuinfo) || true
    echo -e "${BOLD}Model:${NC}    $cpu_model"
    echo -e "${BOLD}Cores:${NC}    $cpu_cores physical, $cpu_threads threads"

    if grep -q -E '(vmx|svm)' /proc/cpuinfo; then
        local virt_type
        virt_type=$(grep -o -E '(vmx|svm)' /proc/cpuinfo | head -1) || true
        echo -e "${BOLD}Virt:${NC}     ${GREEN}[ok]${NC} Enabled ($virt_type)"
    else
        echo -e "${BOLD}Virt:${NC}     ${RED}[x]${NC} Not detected"
    fi

    print_header "MEMORY"
    local mem_total mem_used mem_avail
    mem_total=$(free -h | awk '/^Mem:/ {print $2}') || true
    mem_used=$(free -h | awk '/^Mem:/ {print $3}') || true
    mem_avail=$(free -h | awk '/^Mem:/ {print $7}') || true
    echo -e "${BOLD}Total:${NC}    $mem_total"
    echo -e "${BOLD}Used:${NC}     $mem_used"
    echo -e "${BOLD}Avail:${NC}    $mem_avail"

    print_header "GRAPHICS"
    local gpu_count
    gpu_count=$(lspci | grep -c -E "VGA|3D" || echo "0")
    if [ "$gpu_count" -gt 0 ]; then
        lspci | grep -E "VGA|3D" | while read -r line; do
            echo -e "${BOLD}GPU:${NC}      ${line#*: }"
        done

        if command -v nvidia-smi >/dev/null 2>&1; then
            echo -e "${BOLD}NVIDIA:${NC}   ${GREEN}[ok]${NC} Driver loaded"
        fi
    else
        echo -e "${BOLD}GPU:${NC}      No discrete GPU detected"
    fi

    print_header "STORAGE"
    df -h / | tail -1 | awk '{printf "'"${BOLD}"'Root:'"${NC}"'     %s total, %s used, %s free (%s)\n", $2, $3, $4, $5}'

    local disk_count
    disk_count=$(lsblk -d -o TYPE | grep -c disk || echo "0")
    echo -e "${BOLD}Disks:${NC}    $disk_count detected"

    print_header "VIRTUALIZATION"
    if [ -d /sys/kernel/iommu_groups ]; then
        local iommu_groups
        iommu_groups=$(ls -1 /sys/kernel/iommu_groups/ | wc -l) || true
        echo -e "${BOLD}IOMMU:${NC}    ${GREEN}[ok]${NC} Enabled ($iommu_groups groups)"

        if [ "$gpu_count" -gt 0 ]; then
            echo -e "${BOLD}GPU Grp:${NC}  Checking isolation..."
            local isolated=0
            for gpu_addr in $(lspci | grep -E "VGA|3D" | awk '{print $1}'); do
                for d in /sys/kernel/iommu_groups/*/devices/*; do
                    if [[ "$d" == *"$gpu_addr"* ]]; then
                        n=${d#*/iommu_groups/*}
                        n=${n%%/*}
                        device_count=$(find /sys/kernel/iommu_groups/$n/devices/ -type l | wc -l)
                        if [ "$device_count" -le 2 ]; then
                            isolated=$((isolated + 1))
                        fi
                        break
                    fi
                done
            done

            if [ "$isolated" -gt 0 ]; then
                echo -e "          ${GREEN}[ok]${NC} $isolated GPU(s) in isolated group(s)"
            else
                echo -e "          ${YELLOW}[!]${NC} GPUs share IOMMU groups"
            fi
        fi
    else
        echo -e "${BOLD}IOMMU:${NC}    ${RED}[x]${NC} Not available"
    fi

    if [ -e /dev/kvm ]; then
        echo -e "${BOLD}KVM:${NC}      ${GREEN}[ok]${NC} Available"
    else
        echo -e "${BOLD}KVM:${NC}      ${RED}[x]${NC} Not available"
    fi

    print_header "SECURITY"

    if [ -d /sys/firmware/efi ]; then
        echo -e "${BOLD}Boot:${NC}     ${GREEN}[ok]${NC} UEFI"
    else
        echo -e "${BOLD}Boot:${NC}     ${YELLOW}[!]${NC} Legacy BIOS"
    fi

    if [ -e /dev/tpm0 ]; then
        echo -e "${BOLD}TPM:${NC}      ${GREEN}[ok]${NC} Present (/dev/tpm0)"
    else
        echo -e "${BOLD}TPM:${NC}      ${RED}[x]${NC} Not detected"
    fi

    if command -v mokutil >/dev/null 2>&1; then
        if mokutil --sb-state 2>/dev/null | grep -q "SecureBoot enabled"; then
            echo -e "${BOLD}SecBoot:${NC}  ${GREEN}[ok]${NC} Enabled"
        else
            echo -e "${BOLD}SecBoot:${NC}  ${YELLOW}[!]${NC} Disabled"
        fi
    fi

    print_header "NETWORK"
    # Count /sys/class/net entries whose name does not contain "lo" (same filter as the old ls | grep -v lo).
    local iface_count=0 net_if
    for net_if in /sys/class/net/*; do
        [[ -e "$net_if" || -L "$net_if" ]] || continue
        [[ "${net_if##*/}" == *lo* ]] || iface_count=$((iface_count + 1))
    done
    echo -e "${BOLD}Ifaces:${NC}   $iface_count detected"
    ip -br addr show | grep -v "lo " | while read -r line; do
        local iface ip state
        iface=$(echo $line | awk '{print $1}') || true
        ip=$(echo $line | awk '{print $3}' | cut -d/ -f1) || true
        state=$(echo $line | awk '{print $2}') || true
        if [ "$state" = "UP" ]; then
            echo -e "          ${GREEN}[ok]${NC} $iface: $ip"
        else
            echo -e "          ${YELLOW}○${NC} $iface: $state"
        fi
    done

    print_header "MiOS-Build READINESS"

    local ready=true
    local warnings=0

    echo -e "\n${BOLD}Critical Components:${NC}"

    if grep -q -E '(vmx|svm)' /proc/cpuinfo; then
        echo -e "  ${GREEN}[ok]${NC} CPU Virtualization"
    else
        echo -e "  ${RED}[x]${NC} CPU Virtualization"
        ready=false
    fi

    if [ -d /sys/kernel/iommu_groups ]; then
        echo -e "  ${GREEN}[ok]${NC} IOMMU Support"
    else
        echo -e "  ${RED}[x]${NC} IOMMU Support"
        ready=false
    fi

    if [ -e /dev/kvm ]; then
        echo -e "  ${GREEN}[ok]${NC} KVM Available"
    else
        echo -e "  ${RED}[x]${NC} KVM Available"
        ready=false
    fi

    if [ "$gpu_count" -gt 0 ]; then
        echo -e "  ${GREEN}[ok]${NC} GPU Detected"
    else
        echo -e "  ${YELLOW}[!]${NC} No discrete GPU"
        warnings=$((warnings + 1))
    fi

    echo -e "\n${BOLD}Recommended Components:${NC}"

    if [ -d /sys/firmware/efi ]; then
        echo -e "  ${GREEN}[ok]${NC} UEFI Boot"
    else
        echo -e "  ${YELLOW}[!]${NC} Legacy BIOS (UEFI recommended)"
        warnings=$((warnings + 1))
    fi

    if [ -e /dev/tpm0 ]; then
        echo -e "  ${GREEN}[ok]${NC} TPM 2.0"
    else
        echo -e "  ${YELLOW}[!]${NC} No TPM (needed for Win11)"
        warnings=$((warnings + 1))
    fi

    echo ""
    if [ "$ready" = true ]; then
        echo -e "${GREEN}${BOLD}[ok] System ready for MiOS-Build setup!${NC}"
        if [ "$warnings" -gt 0 ]; then
            echo -e "${YELLOW}  Note: $warnings optional component(s) missing${NC}"
        fi
    else
        echo -e "${RED}${BOLD}[x] System not ready - check failed components${NC}"
    fi

    echo ""
    echo -e "${CYAN}Run './system-profiler.sh' for detailed analysis${NC}"
    echo -e "${CYAN}Run './iommu-visualizer.sh' for IOMMU group details${NC}"
    echo ""
}

main "$@"
