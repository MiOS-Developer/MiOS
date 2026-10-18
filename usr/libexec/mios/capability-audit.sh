#!/usr/bin/env bash
# AI-hint: bash A diagnostic script that audits hardware topology, IOMMU groups, VFIO bindings, and PCIe layout to determine device passthrough readine...
# AI-doc: usr/share/doc/mios/manual/mios.md

set -euo pipefail

trap 'echo "Error on line $LINENO" >&2' ERR

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: $0 [OUTPUT_FILE]"
    echo ""
    echo "System assessment for hardware, topology, IOMMU, VFIO, and passthrough"
    echo ""
    echo "Arguments:"
    echo "  OUTPUT_FILE    Optional custom path for assessment report"
    echo "                 Default: ~/system-assessment_YYYYMMDD_HHMMSS.txt"
    echo ""
    echo "Examples:"
    echo "  $0                                  # Output to home directory"
    echo "  $0 ~/my-assessment.txt              # Custom output path"
    echo "  sudo $0                             # Run as root for full details"
    exit 0
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME="$HOME"
fi

if [[ -n "${1:-}" ]]; then
    OUTPUT_FILE="$1"
else
    OUTPUT_FILE="${REAL_HOME}/Documents/system-assessment_${TIMESTAMP}.txt"
fi

OUTPUT_DIR=$(dirname "$OUTPUT_FILE")
if [[ ! -d "$OUTPUT_DIR" ]]; then
    mkdir -p "$OUTPUT_DIR" 2>/dev/null || {
        echo "Error: Cannot create output directory '$OUTPUT_DIR'"
        exit 1
    }
fi
SEPARATOR="================================================================================"
SUBSEP="--------------------------------------------------------------------------------"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Entry names of a directory, one per line, sorted and without dotfiles -- what
# `ls DIR 2>/dev/null` printed into a pipe; a missing directory prints nothing.
dir_names() {
    local entry
    for entry in "$1"/*; do
        [[ -e "$entry" || -L "$entry" ]] || continue
        printf '%s\n' "${entry##*/}"
    done
    return 0
}

print_section() {
    echo -e "\n${SEPARATOR}"
    echo "  $1"
    echo "${SEPARATOR}"
}

print_subsection() {
    echo -e "\n${SUBSEP}"
    echo "  $1"
    echo "${SUBSEP}"
}

cmd_exists() {
    command -v "$1" &>/dev/null
}

run_if_exists() {
    local cmd="$1"
    shift
    if cmd_exists "$cmd"; then
        "$cmd" "$@" 2>/dev/null || echo "[Command failed or no data]"
    else
        echo "[${cmd} not installed]"
    fi
}

read_file_if_exists() {
    if [[ -f "$1" ]]; then
        cat "$1" 2>/dev/null || echo "[Cannot read file]"
    else
        echo "[File not found: $1]"
    fi
}

check_privileges() {
    if [[ $EUID -eq 0 ]]; then
        echo "Running as root - full information available"
    else
        echo "Running as user - some information may be limited"
    fi
}

{
    echo "${SEPARATOR}"
    echo "  SYSTEM ASSESSMENT REPORT"
    echo "  Generated: $"
    echo "  Hostname: $"
    echo "  User: ${USER}"
    echo "${SEPARATOR}"

    check_privileges

    print_section "SYSTEM OVERVIEW"

    print_subsection "Operating System"
    if [[ -f /etc/os-release ]]; then
        cat /etc/os-release
    fi
    echo ""
    echo "Kernel: $"
    echo "Architecture: $"
    echo "Kernel Version: $"

    print_subsection "System Information (DMI)"
    if cmd_exists dmidecode && [[ $EUID -eq 0 ]]; then
        echo ""
        dmidecode -t system 2>/dev/null | grep -E "Manufacturer|Product Name|Version|Serial|UUID" | sed 's/^[\t ]*//'
        echo ""
        echo ""
        dmidecode -t baseboard 2>/dev/null | grep -E "Manufacturer|Product Name|Version|Serial" | sed 's/^[\t ]*//'
        echo ""
        echo ""
        dmidecode -t bios 2>/dev/null | grep -E "Vendor|Version|Release Date|BIOS Revision" | sed 's/^[\t ]*//'
    else
        echo "[dmidecode requires root privileges]"
        echo ""
        echo "Basic system info from /sys:"
        read_file_if_exists /sys/class/dmi/id/sys_vendor
        read_file_if_exists /sys/class/dmi/id/product_name
        read_file_if_exists /sys/class/dmi/id/board_vendor
        read_file_if_exists /sys/class/dmi/id/board_name
    fi

    print_section "HARDWARE PARTITIONING MAP"

    print_subsection "=== CPU PARTITIONING ==="

    echo "CPU: $(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')"
    echo "Total Cores: $(grep -c "^processor" /proc/cpuinfo)"
    echo ""

    isolated_cpus=$(cat /sys/devices/system/cpu/isolated 2>/dev/null || echo "")
    online_cpus=$(cat /sys/devices/system/cpu/online 2>/dev/null || echo "0-$-1))")

    echo "+"
    echo "|                           CPU CORE ALLOCATION                              |"
    echo "+"

    if [[ -n "$isolated_cpus" && "$isolated_cpus" != "" ]]; then
        echo "| HOST CORES (housekeeping):     $(comm -23 <(echo "$online_cpus" | tr ',' '\n' | sort -n) <(echo "$isolated_cpus" | tr ',' '\n' | sort -n) 2>/dev/null | tr '\n' ',' | sed 's/,$//' || echo "$online_cpus")"
        echo "| ISOLATED CORES:  $isolated_cpus"
    else
        echo "| HOST CORES:     $online_cpus"
        echo "| ISOLATED CORES: [none]"
    fi
    echo "+"

    echo ""
    echo "CCD/CHIPLET TOPOLOGY:"
    declare -A ccd_cpus
    for cpu_path in /sys/devices/system/cpu/cpu[0-9]*; do
        if [[ -d "${cpu_path}/cache" ]]; then
            cpu_num=$(basename "$cpu_path" | sed 's/cpu//')
            for cache in "${cpu_path}"/cache/index*; do
                if [[ -d "$cache" ]]; then
                    level=$(cat "${cache}/level" 2>/dev/null)
                    if [[ "$level" == "3" ]]; then
                        l3_id=$(cat "${cache}/id" 2>/dev/null || echo "0")
                        l3_size=$(cat "${cache}/size" 2>/dev/null || echo "")
                        ccd_cpus["$l3_id"]="${ccd_cpus[$l3_id]:-}${cpu_num},"
                        ccd_sizes["$l3_id"]="$l3_size"
                    fi
                fi
            done
        fi
    done

    for ccd_id in $(echo "${!ccd_cpus[@]}" | tr ' ' '\n' | sort -n); do
        cpus="${ccd_cpus[$ccd_id]%,}"
        size="${ccd_sizes[$ccd_id]:-unknown}"
        vcache_marker=""
        size_num=$(echo "$size" | grep -oE "[0-9]+" | head -1)
        if [[ -n "$size_num" && "$size_num" -gt 32000 ]]; then
            vcache_marker=" [V-Cache]"
        fi
        echo "  CCD $ccd_id$vcache_marker: CPUs $cpus"
    done

    echo ""
    echo "VM CPU PINNING:"
    if cmd_exists virsh; then
        vm_list=$(virsh list --all --name 2>/dev/null | grep -v "^$" || true)
        if [[ -n "$vm_list" ]]; then
            while IFS= read -r vm_name; do
                if [[ -n "$vm_name" ]]; then
                    echo "  ├─ VM: $vm_name"
                    vcpus=$(virsh vcpupin "$vm_name" 2>/dev/null | grep -E "^[[:space:]]*[0-9]" || echo "    [not running or no pinning]")
                    if [[ -n "$vcpus" ]]; then
                        echo "$vcpus" | while read -r line; do
                            echo "  │   $line"
                        done
                    else
                        echo "  │   [no CPU pinning configured]"
                    fi
                    emulator_pin=$(virsh emulatorpin "$vm_name" 2>/dev/null | grep -E "^[[:space:]]*\*" || true)
                    if [[ -n "$emulator_pin" ]]; then
                        echo "  │   Emulator: $emulator_pin"
                    fi
                    iothread_pin=$(virsh iothreadinfo "$vm_name" 2>/dev/null | grep -E "^[[:space:]]*[0-9]" || true)
                    if [[ -n "$iothread_pin" ]]; then
                        echo "  │   IOThreads:"
                        echo "$iothread_pin" | while read -r line; do
                            echo "  │     $line"
                        done
                    fi
                    echo "  └─────────────────────"
                fi
            done <<< "$vm_list"
        else
            echo "  [No VMs defined]"
        fi
    else
        echo "  [libvirt not installed]"
    fi

    print_subsection "=== MEMORY PARTITIONING ==="

    total_mem=$(free -b | awk '/^Mem:/ {print $2}')
    total_mem_gb=$(echo "Scale=1; $total_mem / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "")
    available_mem=$(free -b | awk '/^Mem:/ {print $7}')
    available_mem_gb=$(echo "Scale=1; $available_mem / 1024 / 1024 / 1024" | bc 2>/dev/null || echo "")

    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│                         MEMORY ALLOCATION                                  │"
    echo "├─────────────────────────────────────────────────────────────────────────────┤"
    echo "│ TOTAL SYSTEM MEMORY:    ${total_mem_gb} GB"
    echo "│ HOST AVAILABLE:         ${available_mem_gb} GB"
    echo "└─────────────────────────────────────────────────────────────────────────────┘"

    echo ""
    echo "HUGEPAGE RESERVATIONS:"
    if [[ -d /sys/kernel/mm/hugepages ]]; then
        for hp in /sys/kernel/mm/hugepages/hugepages-*; do
            if [[ -d "$hp" ]]; then
                size=$(basename "$hp" | sed 's/hugepages-//')
                nr=$(cat "${hp}/nr_hugepages" 2>/dev/null || echo "0")
                free_hp=$(cat "${hp}/free_hugepages" 2>/dev/null || echo "0")
                if [[ "$nr" != "0" ]]; then
                    echo "  ${size}: ${nr} pages reserved, ${free_hp} free"
                fi
            fi
        done
        total_hp=$(grep HugePages_Total /proc/meminfo | awk '{print $2}')
        [[ "$total_hp" == "0" || -z "$total_hp" ]] && echo "  [No hugepages reserved]"
    fi

    echo ""
    echo "NUMA MEMORY DISTRIBUTION:"
    if [[ -d /sys/devices/system/node ]]; then
        for node in /sys/devices/system/node/node*; do
            if [[ -d "$node" ]]; then
                node_id=$(basename "$node")
                mem_total=$(grep "MemTotal" "${node}/meminfo" 2>/dev/null | awk '{print $4}')
                cpus=$(cat "${node}/cpulist" 2>/dev/null || echo "")
                mem_total_gb=$(echo "Scale=1; ${mem_total:-0} / 1024 / 1024" | bc 2>/dev/null || echo "")
                echo "  ${node_id}: ${mem_total_gb} GB total, CPUs: $cpus"
            fi
        done
    fi

    echo ""
    echo "VM MEMORY ALLOCATIONS:"
    if cmd_exists virsh; then
        virsh list --all --name 2>/dev/null | grep -v "^$" | while read -r vm_name; do
            if [[ -n "$vm_name" ]]; then
                mem_max=$(virsh dominfo "$vm_name" 2>/dev/null | grep "Max memory" | awk '{print $3, $4}')
                mem_used=$(virsh dominfo "$vm_name" 2>/dev/null | grep "Used memory" | awk '{print $3, $4}')
                state=$(virsh domstate "$vm_name" 2>/dev/null | head -1)
                echo "  $vm_name: Max=$mem_max, Used=$mem_used [$state]"
            fi
        done
    fi

    print_subsection "=== GPU PARTITIONING ==="

    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│                          GPU ALLOCATION                                    │"
    echo "├─────────────────────────────────────────────────────────────────────────────┤"

    gpu_count=0
    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            class=$(cat "${dev}/class" 2>/dev/null || echo "")
            if [[ "$class" == 0x030000 || "$class" == 0x030200 || "$class" == 0x0300* || "$class" == 0x0302* ]]; then
                gpu_count=$((gpu_count + 1))
                pci_addr=$(basename "$dev")
                driver=$(basename "$(readlink "${dev}/driver" 2>/dev/null)" 2>/dev/null || echo "None")
                vendor=$(cat "${dev}/vendor" 2>/dev/null | sed 's/0x//')
                device_id=$(cat "${dev}/device" 2>/dev/null | sed 's/0x//')
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //')
                iommu_group=$(basename "$(readlink "${dev}/iommu_group" 2>/dev/null)" 2>/dev/null || echo "")

                allocation="HOST"
                if [[ "$driver" == "vfio-pci" ]]; then
                    allocation="PASSTHROUGH (vfio-pci)"
                elif [[ "$driver" == "nvidia" ]]; then
                    allocation="HOST (nvidia)"
                elif [[ "$driver" == "amdgpu" ]]; then
                    allocation="HOST (amdgpu)"
                elif [[ "$driver" == "nouveau" ]]; then
                    allocation="HOST (nouveau)"
                elif [[ "$driver" == "i915" ]]; then
                    allocation="HOST (i915)"
                elif [[ "$driver" == "none" ]]; then
                    allocation="UNBOUND"
                fi

                echo "│ GPU $gpu_count: $desc"
                echo "│   PCI: $pci_addr | IOMMU Group: $iommu_group"
                echo "│   Driver: $driver"
                echo "│   ALLOCATION: >>> $allocation <<<"
                echo "│"
            fi
        fi
    done
    [[ $gpu_count -eq 0 ]] && echo "│ [No GPUs detected]"
    echo "└─────────────────────────────────────────────────────────────────────────────┘"

    echo ""
    echo "GPU PASSTHROUGH TO VMs:"
    if cmd_exists virsh; then
        virsh list --all --name 2>/dev/null | grep -v "^$" | while read -r vm_name; do
            if [[ -n "$vm_name" ]]; then
                hostdevs=$(virsh dumpxml "$vm_name" 2>/dev/null | grep -A10 "<hostdev.*pci" | grep -E "domain|bus|slot|function" | tr '\n' ' ' || true)
                if [[ -n "$hostdevs" ]]; then
                    echo "  $vm_name:"
                    virsh dumpxml "$vm_name" 2>/dev/null | grep -B2 -A10 "<hostdev.*pci" | grep -E "domain=|bus=|slot=|function=" | while read -r line; do
                        domain=$(echo "$line" | grep -oP "domain='0x\K[^']+")
                        bus=$(echo "$line" | grep -oP "bus='0x\K[^']+")
                        slot=$(echo "$line" | grep -oP "slot='0x\K[^']+")
                        func=$(echo "$line" | grep -oP "function='0x\K[^']+")
                        if [[ -n "$bus" && -n "$slot" ]]; then
                            pci_addr="${domain:-0000}:${bus}:${slot}.${func:-0}"
                            desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //' || echo "Unknown")
                            echo "    PCI $pci_addr: $desc"
                        fi
                    done
                fi
            fi
        done
    fi

    print_subsection "=== STORAGE PARTITIONING ==="

    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│                        STORAGE ALLOCATION                                  │"
    echo "├─────────────────────────────────────────────────────────────────────────────┤"

    echo "│ BLOCK DEVICES:"
    lsblk -d -o NAME,SIZE,TYPE,MODEL,TRAN 2>/dev/null | while read -r line; do
        echo "│   $line"
    done

    echo "│"
    echo "│ HOST FILESYSTEMS:"
    df -h --output=source,size,used,avail,pcent,target 2>/dev/null | grep -vE "tmpfs|devtmpfs|squashfs|loop" | while read -r line; do
        echo "│   $line"
    done
    echo "└─────────────────────────────────────────────────────────────────────────────┘"

    echo ""
    echo "VM STORAGE ALLOCATIONS:"
    if cmd_exists virsh; then
        virsh list --all --name 2>/dev/null | grep -v "^$" | while read -r vm_name; do
            if [[ -n "$vm_name" ]]; then
                echo "  ├─ VM: $vm_name"
                virsh domblklist "$vm_name" 2>/dev/null | grep -vE "^Target|^-" | while read -r target source; do
                    if [[ -n "$source" && "$source" != "-" ]]; then
                        if [[ -f "$source" ]]; then
                            size=$(du -h "$source" 2>/dev/null | awk '{print $1}')
                            echo "  │   $target: $source"
                        else
                            echo "  │   $target: $source"
                        fi
                    fi
                done
                echo "  └─────────────────────"
            fi
        done
    fi

    echo ""
    echo "STORAGE PASSTHROUGH:"
    if [[ -d /sys/bus/pci/drivers/vfio-pci ]]; then
        for pci_addr in $(dir_names /sys/bus/pci/drivers/vfio-pci | grep -E "^[0-9a-f]{4}:"); do
            class=$(cat "/sys/bus/pci/devices/${pci_addr}/class" 2>/dev/null || echo "")
            if [[ "$class" == "0x010802" || "$class" == "0x010601" ]]; then
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //')
                echo "  $pci_addr: $desc [PASSTHROUGH via vfio-pci]"
            fi
        done
    fi

    print_subsection "=== NETWORK PARTITIONING ==="

    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│                        NETWORK ALLOCATION                                  │"
    echo "├─────────────────────────────────────────────────────────────────────────────┤"

    echo "│ HOST INTERFACES:"
    ip -br link show 2>/dev/null | while read -r iface state mac; do
        driver=$(basename "$(readlink "/sys/class/net/${iface}/device/driver" 2>/dev/null)" 2>/dev/null || echo "Virtual")
        echo "│   $iface: $state"
    done

    echo "│"
    echo "│ BRIDGES:"
    if cmd_exists brctl; then
        brctl show 2>/dev/null | grep -v "^bridge" | while read -r line; do
            echo "│   $line"
        done
    fi

    echo "│"
    echo "│ LIBVIRT NETWORKS:"
    if cmd_exists virsh; then
        virsh net-list --all 2>/dev/null | grep -vE "^Name|^-" | while read -r name state _; do
            if [[ -n "$name" ]]; then
                echo "│   $name: $state"
            fi
        done
    fi
    echo "└─────────────────────────────────────────────────────────────────────────────┘"

    echo ""
    echo "NIC PASSTHROUGH:"
    if [[ -d /sys/bus/pci/drivers/vfio-pci ]]; then
        found_nic=0
        for pci_addr in $(dir_names /sys/bus/pci/drivers/vfio-pci | grep -E "^[0-9a-f]{4}:"); do
            class=$(cat "/sys/bus/pci/devices/${pci_addr}/class" 2>/dev/null || echo "")
            if [[ "$class" == 0x0200* ]]; then
                found_nic=1
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //')
                echo "  $pci_addr: $desc [PASSTHROUGH via vfio-pci]"
            fi
        done
        [[ $found_nic -eq 0 ]] && echo "  [No NICs passed through]"
    fi

    echo ""
    echo "VM NETWORK ATTACHMENTS:"
    if cmd_exists virsh; then
        virsh list --all --name 2>/dev/null | grep -v "^$" | while read -r vm_name; do
            if [[ -n "$vm_name" ]]; then
                echo "  ├─ VM: $vm_name"
                virsh domiflist "$vm_name" 2>/dev/null | grep -vE "^Interface|^-" | while read -r iface type source model mac; do
                    if [[ -n "$iface" ]]; then
                        echo "  │   $iface: type=$type, source=$source, model=$model"
                    fi
                done
                echo "  └─────────────────────"
            fi
        done
    fi

    print_subsection "=== USB CONTROLLER PARTITIONING ==="

    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│                      USB CONTROLLER ALLOCATION                             │"
    echo "├─────────────────────────────────────────────────────────────────────────────┤"

    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            class=$(cat "${dev}/class" 2>/dev/null || echo "")
            if [[ "$class" == 0x0c03* ]]; then
                pci_addr=$(basename "$dev")
                driver=$(basename "$(readlink "${dev}/driver" 2>/dev/null)" 2>/dev/null || echo "None")
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //' | cut -c1-50)
                iommu_group=$(basename "$(readlink "${dev}/iommu_group" 2>/dev/null)" 2>/dev/null || echo "")

                allocation="HOST"
                [[ "$driver" == "vfio-pci" ]] && allocation="PASSTHROUGH"

                echo "│ $pci_addr: $desc"
                echo "│   Driver: $driver | ALLOCATION: $allocation"
            fi
        fi
    done
    echo "└─────────────────────────────────────────────────────────────────────────────┘"

    print_subsection "=== AUDIO PARTITIONING ==="

    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│                        AUDIO ALLOCATION                                    │"
    echo "├─────────────────────────────────────────────────────────────────────────────┤"

    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            class=$(cat "${dev}/class" 2>/dev/null || echo "")
            if [[ "$class" == 0x0403* || "$class" == 0x0401* ]]; then
                pci_addr=$(basename "$dev")
                driver=$(basename "$(readlink "${dev}/driver" 2>/dev/null)" 2>/dev/null || echo "None")
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //')
                iommu_group=$(basename "$(readlink "${dev}/iommu_group" 2>/dev/null)" 2>/dev/null || echo "")

                allocation="HOST"
                [[ "$driver" == "vfio-pci" ]] && allocation="PASSTHROUGH"

                echo "│ $pci_addr:"
                echo "│   $desc"
                echo "│   Driver: $driver | ALLOCATION: $allocation"
            fi
        fi
    done

    echo "│"
    echo "│ HOST AUDIO SERVER:"
    if pgrep -x pipewire &>/dev/null; then
        echo "│   PipeWire: running"
    elif pgrep -x pulseaudio &>/dev/null; then
        echo "│   PulseAudio: running"
    else
        echo "│   [No audio server detected]"
    fi
    echo "└─────────────────────────────────────────────────────────────────────────────┘"

    echo ""
    echo "VM AUDIO CONFIGURATION:"
    if cmd_exists virsh; then
        virsh list --all --name 2>/dev/null | grep -v "^$" | while read -r vm_name; do
            if [[ -n "$vm_name" ]]; then
                sound=$(virsh dumpxml "$vm_name" 2>/dev/null | grep -E "<sound|<audio" | head -3)
                if [[ -n "$sound" ]]; then
                    echo "  $vm_name:"
                    echo "$sound" | sed 's/^/    /'
                fi
            fi
        done
    fi

    print_subsection "=== COMPLETE IOMMU GROUP DEVICE MAP ==="

    echo "All PCI devices organized by IOMMU group:"
    echo ""

    if [[ -d /sys/kernel/iommu_groups ]]; then
        for g in $(find /sys/kernel/iommu_groups/ -maxdepth 1 -mindepth 1 -type d | sort -V); do
            group_id=$(basename "$g")

            group_status="HOST"
            all_vfio=1
            any_vfio=0
            for d in "${g}"/devices/*; do
                if [[ -L "$d" ]]; then
                    pci_addr=$(basename "$d")
                    driver=$(basename "$(readlink "/sys/bus/pci/devices/${pci_addr}/driver" 2>/dev/null)" 2>/dev/null || echo "None")
                    if [[ "$driver" == "vfio-pci" ]]; then
                        any_vfio=1
                    else
                        all_vfio=0
                    fi
                fi
            done

            if [[ $any_vfio -eq 1 && $all_vfio -eq 1 ]]; then
                group_status="PASSTHROUGH"
            elif [[ $any_vfio -eq 1 ]]; then
                group_status="MIXED (warning!)"
            fi

            echo "├─ IOMMU Group $group_id [$group_status])"
            for d in "${g}"/devices/*; do
                if [[ -L "$d" ]]; then
                    pci_addr=$(basename "$d")
                    desc=$(lspci -nns "${pci_addr}" 2>/dev/null || echo "Unknown")
                    driver=$(basename "$(readlink "/sys/bus/pci/devices/${pci_addr}/driver" 2>/dev/null)" 2>/dev/null || echo "None")
                    echo "│   $desc"
                    echo "│      └─ Driver: $driver"
                fi
            done
            echo "└───────────────────────────────────────────────────────────────────────────"
            echo ""
        done
    fi

    print_subsection "=== PARTITION SUMMARY FOR DIAGRAMS ==="

    echo ""
    echo "┌───────────────────────────────────────────────────────────────────────────────┐"
    echo "│                     CLOUD WS HARDWARE PARTITION SUMMARY                       │"
    echo "├───────────────────────────────────────────────────────────────────────────────┤"
    echo "│                                                                               │"
    echo "│  ├─────────────────────────────────────────────────────────────────────────┤  │"
    echo "│  │                              HOST SYSTEM                                │  │"
    echo "│  ├─────────────────────────────────────────────────────────────────────────┤  │"

    if [[ -n "$isolated_cpus" && "$isolated_cpus" != "" ]]; then
        host_cpus=$(comm -23 <(seq 0 $(($(nproc)-1)) | sort -n) <(echo "$isolated_cpus" | tr ',' '\n' | while read range; do
            if [[ "$range" == *-* ]]; then
                start=$(echo "$range" | cut -d- -f1)
                end=$(echo "$range" | cut -d- -f2)
                seq "$start" "$end"
            else
                echo "$range"
            fi
        done | sort -n) 2>/dev/null | tr '\n' ',' | sed 's/,$//')
        echo "│  │  CPUs: ${host_cpus:-0-$-1))}"
    else
        echo "│  │  CPUs: 0-$-1))"
    fi

    host_gpu="none"
    for dev in /sys/bus/pci/devices/*; do
        class=$(cat "${dev}/class" 2>/dev/null || echo "")
        if [[ "$class" == 0x030* ]]; then
            driver=$(basename "$(readlink "${dev}/driver" 2>/dev/null)" 2>/dev/null || echo "")
            if [[ "$driver" != "vfio-pci" && -n "$driver" ]]; then
                host_gpu=$(lspci -s "$(basename "$dev")" 2>/dev/null | cut -d: -f3- | sed 's/^ //' | cut -c1-45)
                break
            fi
        fi
    done
    echo "│  │  GPU: $host_gpu"
    echo "│  │  Memory: ${available_mem_gb:-?} GB available"
    echo "│  └─────────────────────────────────────────────────────────────────────────┘  │"
    echo "│                                                                               │"

    if cmd_exists virsh; then
        vm_list=$(virsh list --all --name 2>/dev/null | grep -v "^$")
        if [[ -n "$vm_list" ]]; then
            echo "$vm_list" | while read -r vm_name; do
                if [[ -n "$vm_name" ]]; then
                    state=$(virsh domstate "$vm_name" 2>/dev/null | head -1)
                    vcpus=$(virsh vcpucount "$vm_name" --current 2>/dev/null || echo "")
                    mem=$(virsh dominfo "$vm_name" 2>/dev/null | grep "Max memory" | awk '{print $3/1024/1024 " GB"}')

                    echo "  │  ├─────────────────────────────────────────────────────────────────────────┤  │"
                    echo "  │  │  VM: $vm_name [$state]"
                    echo "  │  ├─────────────────────────────────────────────────────────────────────────┤  │"
                    echo "  │  │  vCPUs: $vcpus | Memory: ${mem:-?}"

                    gpu_pt=$(virsh dumpxml "$vm_name" 2>/dev/null | grep -c "hostdev.*pci" || echo "0")
                    [[ "$gpu_pt" -gt 0 ]] && echo "│  │  GPU Passthrough: Yes)"

                    echo "  │  └─────────────────────────────────────────────────────────────────────────┘  │"
                fi
            done
        fi
    fi

    echo "│                                                                               │"
    echo "└───────────────────────────────────────────────────────────────────────────────┘"

    print_section "CPU INFORMATION & TOPOLOGY"

    print_subsection "CPU Model & Specifications"
    grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | sed 's/^ //'
    echo ""
    echo "Physical CPUs: $(grep "physical id" /proc/cpuinfo | sort -u | wc -l)"
    echo "Total Cores: $(grep "cpu cores" /proc/cpuinfo | head -1 | cut -d: -f2 | tr -d ' ')"
    echo "Total Threads: $"
    echo "Threads per Core: $(lscpu | grep "Thread(s) per core" | awk '{print $NF}')"

    print_subsection "CPU Flags & Capabilities"
    grep -m1 "flags" /proc/cpuinfo | cut -d: -f2 | tr ' ' '\n' | sort -u | tr '\n' ' ' | fold -s -w 80
    echo ""

    print_subsection "Virtualization Capabilities"
    echo "Hardware Virtualization:"
    if grep -qE "vmx|svm" /proc/cpuinfo; then
        if grep -q "vmx" /proc/cpuinfo; then
            echo "  Intel VT-x: SUPPORTED"
        fi
        if grep -q "svm" /proc/cpuinfo; then
            echo "  AMD-V: SUPPORTED"
        fi
    else
        echo "  Hardware virtualization: NOT DETECTED"
    fi

    echo ""
    echo "Nested Virtualization:"
    if [[ -f /sys/module/kvm_intel/parameters/nested ]]; then
        echo "  Intel nested: $"
    fi
    if [[ -f /sys/module/kvm_amd/parameters/nested ]]; then
        echo "  AMD nested: $"
    fi

    print_subsection "CPU Topology (lscpu)"
    lscpu

    print_subsection "NUMA Topology"
    if cmd_exists numactl; then
        numactl --hardware 2>/dev/null || echo "[numactl failed]"
        echo ""
        numactl --show 2>/dev/null || true
    else
        echo "[numactl not installed]"
    fi

    if [[ -d /sys/devices/system/node ]]; then
        echo ""
        echo "NUMA Nodes from sysfs:"
        for node in /sys/devices/system/node/node*; do
            if [[ -d "$node" ]]; then
                node_id=$(basename "$node")
                cpus=$(cat "${node}/cpulist" 2>/dev/null || echo "N/A")
                mem=$(cat "${node}/meminfo" 2>/dev/null | grep "MemTotal" | awk '{print $4, $5}')
                echo "  ${node_id}: CPUs ${cpus}, Memory: ${mem}"
            fi
        done
    fi

    print_subsection "CPU Cache Topology"
    lscpu -C 2>/dev/null || echo "[Cache info not available]"

    print_subsection "Per-CPU Information"
    if cmd_exists lstopo-no-graphics; then
        lstopo-no-graphics --of ascii 2>/dev/null || true
    elif cmd_exists lstopo; then
        lstopo --of ascii 2>/dev/null || true
    else
        echo "[hwloc/lstopo not installed - install for detailed topology]"
    fi

    print_subsection "CPU Frequency & Governors"
    if [[ -d /sys/devices/system/cpu/cpu0/cpufreq ]]; then
        echo "Current Governor: $"
        echo "Available Governors: $"
        echo "Min Frequency: $ kHz"
        echo "Max Frequency: $ kHz"
        echo ""
        echo "Per-CPU Frequencies:"
        for cpu in /sys/devices/system/cpu/cpu[0-9]*; do
            if [[ -f "${cpu}/cpufreq/scaling_cur_freq" ]]; then
                freq=$(cat "${cpu}/cpufreq/scaling_cur_freq")
                echo "  $(basename "$cpu"): $((freq/1000)) MHz"
            fi
        done | column
    else
        echo "[cpufreq not available]"
    fi

    print_section "MEMORY INFORMATION"

    print_subsection "Memory Summary"
    free -h

    print_subsection "Memory Details"
    if cmd_exists dmidecode && [[ $EUID -eq 0 ]]; then
        dmidecode -t memory 2>/dev/null | grep -E "Size|Type|Speed|Manufacturer|Part Number|Locator" | grep -v "No Module" | sed 's/^[\t ]*//'
    else
        echo "[dmidecode requires root for detailed memory info]"
    fi

    print_subsection "Memory Map"
    cat /proc/meminfo

    print_subsection "Huge Pages"
    grep -i huge /proc/meminfo 2>/dev/null || echo "[No hugepage info]"

    if [[ -d /sys/kernel/mm/hugepages ]]; then
        echo ""
        echo "Hugepage Pools:"
        for hp in /sys/kernel/mm/hugepages/hugepages-*; do
            if [[ -d "$hp" ]]; then
                size=$(basename "$hp" | sed 's/hugepages-//')
                nr=$(cat "${hp}/nr_hugepages" 2>/dev/null || echo "0")
                free=$(cat "${hp}/free_hugepages" 2>/dev/null || echo "0")
                echo "  ${size}: ${nr} total, ${free} free"
            fi
        done
    fi

    print_section "PCI DEVICES & TOPOLOGY"

    print_subsection "PCI Device Tree"
    run_if_exists lspci -tv

    print_subsection "PCI Devices (Detailed)"
    run_if_exists lspci -nnk

    print_subsection "PCIe Link Speed & Width"
    echo "PCIe devices with link status:"
    echo ""
    echo "PCI Address      Speed      Width   Max Speed  Max Width  Device"
    echo ""
    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            pci_addr=$(basename "$dev")

            cur_speed=$(cat "${dev}/current_link_speed" 2>/dev/null || echo "N/A")
            cur_width=$(cat "${dev}/current_link_width" 2>/dev/null || echo "N/A")
            max_speed=$(cat "${dev}/max_link_speed" 2>/dev/null || echo "N/A")
            max_width=$(cat "${dev}/max_link_width" 2>/dev/null || echo "N/A")

            if [[ "$cur_speed" != "N/A" || "$max_speed" != "N/A" ]]; then
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //' | cut -c1-35)
                printf "%-16s %-10s %-7s %-10s %-10s %s\n" "$pci_addr" "$cur_speed" "x${cur_width}" "$max_speed" "x${max_width}" "$desc"
            fi
        fi
    done

    print_subsection "PCIe Topology (lspci -tv)"
    echo "Shows slot hierarchy - useful for understanding IOMMU group relationships:"
    run_if_exists lspci -tv

    print_subsection "GPU Information"
    echo ""
    lspci -nnk | grep -A3 -E "VGA|3D|Display" || echo "[No GPU found]"

    if cmd_exists nvidia-smi; then
        echo ""
        echo ""
        nvidia-smi --query-gpu=name,driver_version,pci.bus_id,memory.total,memory.free,utilization.gpu,temperature.gpu --format=csv
    fi

    if [[ -d /sys/class/drm ]]; then
        echo ""
        echo ""
        for card in /sys/class/drm/card[0-9]*; do
            if [[ -d "$card" && ! -L "${card}/device/driver" ]]; then
                continue
            fi
            card_name=$(basename "$card")
            driver=$(basename "$(readlink "${card}/device/driver" 2>/dev/null)" 2>/dev/null || echo "Unknown")
            vendor=$(cat "${card}/device/vendor" 2>/dev/null || echo "Unknown")
            device=$(cat "${card}/device/device" 2>/dev/null || echo "Unknown")
            echo "  ${card_name}: driver=${driver}, vendor=${vendor}, device=${device}"
        done
    fi

    print_section "CPU ISOLATION & PINNING"

    print_subsection "Kernel Isolation Parameters (from cmdline)"
    CMDLINE=$(cat /proc/cmdline)

    echo "Full kernel cmdline:"
    echo "$CMDLINE" | fold -s -w 100
    echo ""

    echo "Parsed Isolation Parameters:"
    if echo "$CMDLINE" | grep -qoE "isolcpus=[^ ]+"; then
        isolcpus_val=$(echo "$CMDLINE" | grep -oE "isolcpus=[^ ]+" | cut -d= -f2)
        echo "  isolcpus: ${isolcpus_val}"
    else
        echo "  isolcpus: [not set]"
    fi

    if echo "$CMDLINE" | grep -qoE "nohz_full=[^ ]+"; then
        nohz_val=$(echo "$CMDLINE" | grep -oE "nohz_full=[^ ]+" | cut -d= -f2)
        echo "  nohz_full: ${nohz_val}"
    else
        echo "  nohz_full: [not set]"
    fi

    if echo "$CMDLINE" | grep -qoE "rcu_nocbs=[^ ]+"; then
        rcu_val=$(echo "$CMDLINE" | grep -oE "rcu_nocbs=[^ ]+" | cut -d= -f2)
        echo "  rcu_nocbs: ${rcu_val}"
    else
        echo "  rcu_nocbs: [not set]"
    fi

    if echo "$CMDLINE" | grep -q "rcu_nocb_poll"; then
        echo "  rcu_nocb_poll: enabled"
    else
        echo "  rcu_nocb_poll: [not set]"
    fi

    if echo "$CMDLINE" | grep -qoE "irqaffinity=[^ ]+"; then
        irqaff_val=$(echo "$CMDLINE" | grep -oE "irqaffinity=[^ ]+" | cut -d= -f2)
        echo "  irqaffinity: ${irqaff_val}"
    else
        echo "  irqaffinity: [not set]"
    fi

    if echo "$CMDLINE" | grep -qoE "kthread_cpus=[^ ]+"; then
        kthread_val=$(echo "$CMDLINE" | grep -oE "kthread_cpus=[^ ]+" | cut -d= -f2)
        echo "  kthread_cpus: ${kthread_val}"
    else
        echo "  kthread_cpus: [not set]"
    fi

    print_subsection "CPU Isolation Status (sysfs)"
    echo "Isolated CPUs: $"
    echo "Online CPUs: $"
    echo "Offline CPUs: $"
    echo "Present CPUs: $"
    echo "Possible CPUs: $"
    echo "Kernel Max CPUs: $"

    print_subsection "Per-CPU Status"
    echo "CPU  Online  Governor        Cur_Freq    Isolated  NUMA"
    echo ""
    isolated_cpus=$(cat /sys/devices/system/cpu/isolated 2>/dev/null || echo "")
    for cpu_path in /sys/devices/system/cpu/cpu[0-9]*; do
        if [[ -d "$cpu_path" ]]; then
            cpu_num=$(basename "$cpu_path" | sed 's/cpu//')
            online="yes"
            if [[ -f "${cpu_path}/online" ]]; then
                online_val=$(cat "${cpu_path}/online" 2>/dev/null)
                [[ "$online_val" == "0" ]] && online="no"
            fi
            governor=$(cat "${cpu_path}/cpufreq/scaling_governor" 2>/dev/null || echo "N/A")
            cur_freq=$(cat "${cpu_path}/cpufreq/scaling_cur_freq" 2>/dev/null || echo "0")
            cur_freq_mhz="$((cur_freq/1000))MHz"
            [[ "$cur_freq" == "0" ]] && cur_freq_mhz="N/A"

            is_isolated="no"
            if [[ -n "$isolated_cpus" ]]; then
                for range in $(echo "$isolated_cpus" | tr ',' ' '); do
                    if [[ "$range" == *-* ]]; then
                        start=$(echo "$range" | cut -d- -f1)
                        end=$(echo "$range" | cut -d- -f2)
                        if [[ "$cpu_num" -ge "$start" && "$cpu_num" -le "$end" ]]; then
                            is_isolated="YES"
                            break
                        fi
                    elif [[ "$range" == "$cpu_num" ]]; then
                        is_isolated="YES"
                        break
                    fi
                done
            fi

            numa_node=$(cat "${cpu_path}/topology/physical_package_id" 2>/dev/null || echo "")

            printf "%-4s %-7s %-15s %-11s %-9s %s\n" "$cpu_num" "$online" "$governor" "$cur_freq_mhz" "$is_isolated" "$numa_node"
        fi
    done

    print_subsection "CPU Topology - CCD/CCX Layout (AMD)"
    if [[ -d /sys/devices/system/cpu/cpu0/topology ]]; then
        echo "Core ID / Physical Package / Die / Cluster mapping:"
        echo "CPU   Core_ID  Pkg_ID  Die_ID  Cluster  Thread_Siblings  Core_Siblings"
        echo ""
        for cpu_path in /sys/devices/system/cpu/cpu[0-9]*; do
            if [[ -d "${cpu_path}/topology" ]]; then
                cpu_num=$(basename "$cpu_path" | sed 's/cpu//')
                core_id=$(cat "${cpu_path}/topology/core_id" 2>/dev/null || echo "")
                pkg_id=$(cat "${cpu_path}/topology/physical_package_id" 2>/dev/null || echo "")
                die_id=$(cat "${cpu_path}/topology/die_id" 2>/dev/null || echo "")
                cluster_id=$(cat "${cpu_path}/topology/cluster_id" 2>/dev/null || echo "")
                thread_sibs=$(cat "${cpu_path}/topology/thread_siblings_list" 2>/dev/null || echo "")
                core_sibs=$(cat "${cpu_path}/topology/core_siblings_list" 2>/dev/null || echo "")
                printf "%-5s %-8s %-7s %-7s %-8s %-16s %s\n" "$cpu_num" "$core_id" "$pkg_id" "$die_id" "$cluster_id" "$thread_sibs" "$core_sibs"
            fi
        done
    fi

    print_subsection "L3 Cache Domains (CCD Detection)"
    if [[ -d /sys/devices/system/cpu/cpu0/cache ]]; then
        echo "L3 Cache to CPU mapping:"
        declare -A l3_map
        for cpu_path in /sys/devices/system/cpu/cpu[0-9]*; do
            cpu_num=$(basename "$cpu_path" | sed 's/cpu//')
            for cache in "${cpu_path}"/cache/index*; do
                if [[ -d "$cache" ]]; then
                    level=$(cat "${cache}/level" 2>/dev/null)
                    if [[ "$level" == "3" ]]; then
                        l3_id=$(cat "${cache}/id" 2>/dev/null || echo "")
                        l3_size=$(cat "${cache}/size" 2>/dev/null || echo "")
                        shared_cpus=$(cat "${cache}/shared_cpu_list" 2>/dev/null || echo "")
                        l3_map["$l3_id"]="${l3_size}|${shared_cpus}"
                    fi
                fi
            done
        done
        for l3_id in $(echo "${!l3_map[@]}" | tr ' ' '\n' | sort -n); do
            IFS='|' read -r size cpus <<< "${l3_map[$l3_id]}"
            echo "  L3 Cache #${l3_id}: ${size}, CPUs: ${cpus}"
        done
    fi

    print_section "IRQ AFFINITY & INTERRUPTS"

    print_subsection "IRQ Balance Service"
    if cmd_exists systemctl; then
        irqbalance_status=$(systemctl is-active irqbalance 2>/dev/null || echo "Unknown")
        echo "Irqbalance service: ${irqbalance_status}"
    fi

    if [[ -f /etc/default/irqbalance ]]; then
        echo ""
        echo "Irqbalance config:"
        grep -v "^#" /etc/default/irqbalance | grep -v "^$" || echo "[empty/default]"
    fi

    if [[ -f /etc/sysconfig/irqbalance ]]; then
        echo ""
        echo "Irqbalance config:"
        grep -v "^#" /etc/sysconfig/irqbalance | grep -v "^$" || echo "[empty/default]"
    fi

    print_subsection "IRQ to CPU Affinity (Non-default)"
    echo "IRQs with specific CPU affinity:"
    echo "IRQ      Affinity_Mask    CPUs              Device"
    echo ""
    for irq_path in /proc/irq/[0-9]*; do
        if [[ -d "$irq_path" ]]; then
            irq_num=$(basename "$irq_path")
            affinity=$(cat "${irq_path}/smp_affinity" 2>/dev/null | sed 's/^0*//' || echo "")
            affinity_list=$(cat "${irq_path}/smp_affinity_list" 2>/dev/null || echo "")
            devices=""
            for dev in "${irq_path}"/*; do
                if [[ -d "$dev" && "$(basename "$dev")" != "." ]]; then
                    dev_name=$(basename "$dev")
                    [[ "$dev_name" != "node" ]] && devices="${devices}${dev_name},"
                fi
            done
            devices=${devices%,}
            [[ -z "$devices" ]] && devices="[kernel]"

            if [[ "$affinity_list" != *"-"* ]] || [[ $(echo "$affinity_list" | tr ',' '\n' | wc -l) -gt 1 ]]; then
                printf "%-8s %-16s %-17s %s\n" "$irq_num" "$affinity" "$affinity_list" "$devices"
            fi
        fi
    done | head -50
    echo "[showing first 50 non-default IRQ affinities]"

    print_subsection "Interrupt Counts by CPU"
    head -5 /proc/interrupts
    echo ""
    echo "[Full interrupt table truncated - see /proc/interrupts for complete data]"

    print_subsection "MSI/MSI-X Status for PCI Devices"
    echo "Devices with MSI/MSI-X enabled:"
    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            pci_addr=$(basename "$dev")
            irq=$(cat "${dev}/irq" 2>/dev/null || echo "")
            if [[ -d "${dev}/msi_irqs" ]]; then
                msi_irqs=$(ls "${dev}/msi_irqs" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //' | cut -c1-50)
                echo "  ${pci_addr}: IRQ=${irq}, MSI_IRQs=[${msi_irqs}] - ${desc}"
            fi
        fi
    done | head -30

    print_section "IOMMU CONFIGURATION"

    print_subsection "IOMMU Kernel Parameters"
    echo "IOMMU-related cmdline parameters:"
    for param in intel_iommu amd_iommu iommu iommu_group pcie_acs_override; do
        val=$(echo "$CMDLINE" | grep -oE "${param}=[^ ]+" || echo "")
        if [[ -n "$val" ]]; then
            echo "  $val"
        fi
    done

    echo ""
    echo "IOMMU Flags Detected:"
    echo "$CMDLINE" | grep -qoE "intel_iommu=on" && echo "  Intel IOMMU: ENABLED" || true
    echo "$CMDLINE" | grep -qoE "amd_iommu=on" && echo "  AMD IOMMU: ENABLED" || true
    echo "$CMDLINE" | grep -qoE "iommu=pt" && echo "  IOMMU Passthrough Mode: ENABLED" || true
    echo "$CMDLINE" | grep -qoE "pcie_acs_override" && echo "  PCIe ACS Override: ENABLED" || true

    print_subsection "IOMMU Status"
    if [[ -d /sys/kernel/iommu_groups ]]; then
        echo "IOMMU: ENABLED"
        num_groups=$(find /sys/kernel/iommu_groups/ -maxdepth 1 -mindepth 1 -type d | wc -l)
        echo "Total IOMMU Groups: ${num_groups}"
    else
        echo "IOMMU: NOT ENABLED"
    fi

    print_subsection "Interrupt Remapping"
    if dmesg 2>/dev/null | grep -qi "interrupt remapping"; then
        echo "Interrupt Remapping: ENABLED"
        dmesg 2>/dev/null | grep -i "interrupt remapping" | tail -5
    else
        echo "Interrupt Remapping: Status unknown"
    fi

    print_subsection "IOMMU Kernel Messages"
    if [[ $EUID -eq 0 ]]; then
        dmesg 2>/dev/null | grep -iE "IOMMU|DMAR|AMD-Vi" | tail -20 || echo "[No IOMMU messages found]"
    else
        echo "[Requires root to read dmesg]"
        journalctl -k 2>/dev/null | grep -iE "IOMMU|DMAR|AMD-Vi" | tail -20 || echo "[No access to kernel log]"
    fi

    print_subsection "DMA Configuration"
    echo "IOMMU DMA Mode:"
    if echo "$CMDLINE" | grep -q "iommu=pt"; then
        echo "  Passthrough mode: ENABLED - devices bypass IOMMU for DMA"
    else
        echo "  Passthrough mode: DISABLED - all DMA goes through IOMMU translation"
    fi

    echo ""
    echo "Per-device IOMMU/DMA groups:"
    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            pci_addr=$(basename "$dev")
            iommu_group=$(basename "$(readlink "${dev}/iommu_group" 2>/dev/null)" 2>/dev/null || echo "None")
            dma_alias=$(cat "${dev}/dma_alias_devid" 2>/dev/null || echo "")

            class=$(cat "${dev}/class" 2>/dev/null || echo "")
            case "$class" in
                0x030*|0x020*|0x010802|0x0c03*)
                    desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //' | cut -c1-45)
                    echo "  ${pci_addr}: Group ${iommu_group}${dma_alias:+, DMA alias: $dma_alias} - ${desc}"
                    ;;
            esac
        fi
    done

    print_subsection "IOMMU Group Mappings (Detailed)"
    if [[ -d /sys/kernel/iommu_groups ]]; then
        for g in $(find /sys/kernel/iommu_groups/ -maxdepth 1 -mindepth 1 -type d | sort -V); do
            group_id=$(basename "$g")
            echo ""
            echo "┌── IOMMU Group ${group_id} ──"
            for d in "${g}"/devices/*; do
                if [[ -L "$d" ]]; then
                    pci_addr=$(basename "$d")
                    desc=$(lspci -nns "${pci_addr}" 2>/dev/null || echo "Unknown device")
                    driver=$(basename "$(readlink "/sys/bus/pci/devices/${pci_addr}/driver" 2>/dev/null)" 2>/dev/null || echo "None")

                    reset_methods=""
                    [[ -f "/sys/bus/pci/devices/${pci_addr}/reset" ]] && reset_methods="${reset_methods}sysfs,"
                    [[ -f "/sys/bus/pci/devices/${pci_addr}/reset_method" ]] && reset_methods="${reset_methods}$(cat /sys/bus/pci/devices/${pci_addr}/reset_method 2>/dev/null),"
                    reset_methods=${reset_methods%,}
                    [[ -z "$reset_methods" ]] && reset_methods="none"

                    echo "  │ ${pci_addr} ${desc}"
                    echo "  │   └─ Driver: ${driver}, Reset: [${reset_methods}]"
                fi
            done
            echo "└──────────────────────────────"
        done
    else
        echo "[IOMMU not enabled - no groups available]"
    fi

    print_section "VFIO & PASSTHROUGH CONFIGURATION"

    print_subsection "VFIO Kernel Parameters"
    echo "VFIO-related cmdline parameters:"
    for param in vfio vfio-pci vfio_iommu_type1; do
        val=$(echo "$CMDLINE" | grep -oE "${param}[^[:space:]]*" || echo "")
        if [[ -n "$val" ]]; then
            echo "  $val"
        fi
    done

    if echo "$CMDLINE" | grep -qoE "vfio-pci.ids=[^ ]+"; then
        ids=$(echo "$CMDLINE" | grep -oE "vfio-pci.ids=[^ ]+" | cut -d= -f2)
        echo ""
        echo "  vfio-pci.ids configured for:"
        for id in $(echo "$ids" | tr ',' '\n'); do
            desc=$(lspci -d "$id" 2>/dev/null | head -1 || echo "Unknown device")
            echo "    ${id}: ${desc}"
        done
    fi

    print_subsection "VFIO Modules Status"
    echo "VFIO Module              Loaded    Parameters"
    echo ""
    for mod in vfio vfio_pci vfio_iommu_type1 vfio_virqfd vfio_mdev; do
        if lsmod | grep -q "^${mod}[[:space:]]"; then
            loaded="YES"
            params=""
            if [[ -d "/sys/module/${mod}/parameters" ]]; then
                for p in /sys/module/${mod}/parameters/*; do
                    if [[ -f "$p" ]]; then
                        pname=$(basename "$p")
                        pval=$(cat "$p" 2>/dev/null || echo "")
                        params="${params}${pname}=${pval}, "
                    fi
                done
                params=${params%, }
            fi
            [[ -z "$params" ]] && params="[default]"
        else
            loaded="no"
            params="-"
        fi
        printf "%-24s %-9s %s\n" "$mod" "$loaded" "$params"
    done

    print_subsection "Devices Bound to vfio-pci"
    if [[ -d /sys/bus/pci/drivers/vfio-pci ]]; then
        echo ""
        vfio_devices=$(dir_names /sys/bus/pci/drivers/vfio-pci | grep -E "^[0-9a-f]{4}:" || true)
        if [[ -n "$vfio_devices" ]]; then
            echo "PCI Address      Vendor:Device    Description"
            echo ""
            for pci_addr in $vfio_devices; do
                vendor=$(cat "/sys/bus/pci/devices/${pci_addr}/vendor" 2>/dev/null | sed 's/0x//')
                device=$(cat "/sys/bus/pci/devices/${pci_addr}/device" 2>/dev/null | sed 's/0x//')
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //')
                iommu_group=$(basename "$(readlink "/sys/bus/pci/devices/${pci_addr}/iommu_group" 2>/dev/null)" 2>/dev/null || echo "")
                printf "%-16s %s:%s        %s (IOMMU Group %s)\n" "$pci_addr" "$vendor" "$device" "$desc" "$iommu_group"
            done
        else
            echo "[No devices currently bound to vfio-pci]"
        fi
    else
        echo "[vfio-pci driver not loaded]"
    fi

    print_subsection "PCI Device Reset Capabilities"
    echo "Devices with reset support:"
    echo ""
    echo "PCI Address      Reset Methods                    Device"
    echo ""
    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            pci_addr=$(basename "$dev")
            reset_methods=""

            [[ -f "${dev}/reset" ]] && reset_methods="${reset_methods}sysfs "
            if [[ -f "${dev}/reset_method" ]]; then
                methods=$(cat "${dev}/reset_method" 2>/dev/null)
                reset_methods="${reset_methods}[${methods}] "
            fi

            if [[ $EUID -eq 0 ]] && cmd_exists setpci; then
                pcie_cap=$(setpci -s "$pci_addr" CAP_EXP+8.w 2>/dev/null || echo "")
                if [[ -n "$pcie_cap" ]]; then
                    flr_bit=$(( 16#${pcie_cap} & 0x8000 ))
                    [[ $flr_bit -ne 0 ]] && reset_methods="${reset_methods}FLR "
                fi
            fi

            if [[ -n "$reset_methods" ]]; then
                desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //' | cut -c1-40)
                printf "%-16s %-32s %s\n" "$pci_addr" "$reset_methods" "$desc"
            fi
        fi
    done

    print_subsection "ACS (Access Control Services) Status"
    echo "ACS is important for proper IOMMU group isolation"
    echo ""
    if cmd_exists setpci && [[ $EUID -eq 0 ]]; then
        echo "PCI Address      ACS Cap    ACS Ctrl   Device"
        echo ""
        for dev in /sys/bus/pci/devices/*; do
            if [[ -d "$dev" ]]; then
                pci_addr=$(basename "$dev")
                acs_cap=$(setpci -s "$pci_addr" ECAP_ACS+4.w 2>/dev/null || echo "")
                acs_ctrl=$(setpci -s "$pci_addr" ECAP_ACS+6.w 2>/dev/null || echo "")
                if [[ -n "$acs_cap" && "$acs_cap" != "0000" ]]; then
                    desc=$(lspci -s "$pci_addr" 2>/dev/null | cut -d: -f3- | sed 's/^ //' | cut -c1-35)
                    printf "%-16s 0x%-7s  0x%-7s  %s\n" "$pci_addr" "$acs_cap" "$acs_ctrl" "$desc"
                fi
            fi
        done
    else
        echo "[ACS detection requires root and setpci]"
    fi

    if echo "$CMDLINE" | grep -q "pcie_acs_override"; then
        echo ""
        echo "⚠  WARNING: pcie_acs_override is enabled"
        echo "  This bypasses hardware ACS and may have security implications"
        echo "  Only use if you understand the risks and need to separate devices"
    fi

    print_subsection "GPU Passthrough Readiness"
    echo "Checking GPUs for passthrough suitability:"
    echo ""
    lspci -nnk | grep -A3 -E "VGA|3D|Display" | while read -r line; do
        echo "$line"
    done

    echo ""
    echo "GPU IOMMU Group Analysis:"
    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            pci_addr=$(basename "$dev")
            class=$(cat "${dev}/class" 2>/dev/null || echo "")
            if [[ "$class" == "0x030000" || "$class" == "0x030200" || "$class" == 0x0300* || "$class" == 0x0302* ]]; then
                iommu_group_path=$(readlink "${dev}/iommu_group" 2>/dev/null)
                if [[ -n "$iommu_group_path" ]]; then
                    iommu_group=$(basename "$iommu_group_path")
                    group_members=$(ls "/sys/kernel/iommu_groups/${iommu_group}/devices/" 2>/dev/null | wc -l)
                    desc=$(lspci -s "$pci_addr" 2>/dev/null)
                    driver=$(basename "$(readlink "${dev}/driver" 2>/dev/null)" 2>/dev/null || echo "None")

                    echo "  ${desc}"
                    echo "    IOMMU Group: ${iommu_group} in group)"
                    echo "    Current Driver: ${driver}"
                    if [[ $group_members -eq 1 ]]; then
                        echo "    Passthrough: ✔ Clean isolation"
                    elif [[ $group_members -le 3 ]]; then
                        echo "    Passthrough: ~ Acceptable"
                    else
                        echo "    Passthrough: ⚠  May need ACS override or all devices passed together"
                    fi
                    echo ""
                fi
            fi
        fi
    done

    print_subsection "VFIO Event Log (recent)"
    if [[ $EUID -eq 0 ]]; then
        dmesg 2>/dev/null | grep -iE "vfio|iommu.*attach|iommu.*detach" | tail -15 || echo "[No recent VFIO events]"
    else
        journalctl -k 2>/dev/null | grep -iE "vfio" | tail -15 || echo "[Requires root for kernel log access]"
    fi

    print_section "STORAGE DEVICES"

    print_subsection "Block Devices"
    lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,ROTA,DISC-MAX,TRAN

    print_subsection "Disk Details"
    if cmd_exists fdisk && [[ $EUID -eq 0 ]]; then
        fdisk -l 2>/dev/null | grep -E "^Disk /|Disk model"
    else
        echo "[fdisk -l requires root]"
    fi

    print_subsection "NVMe Devices"
    if cmd_exists nvme; then
        nvme list 2>/dev/null || echo "[No NVMe devices or nvme-cli error]"
    else
        echo "[nvme-cli not installed]"
        if [[ -d /sys/class/nvme ]]; then
            echo "NVMe devices from sysfs:"
            for nvme in /sys/class/nvme/nvme*; do
                if [[ -d "$nvme" ]]; then
                    model=$(cat "${nvme}/model" 2>/dev/null | tr -d ' ' || echo "Unknown")
                    serial=$(cat "${nvme}/serial" 2>/dev/null | tr -d ' ' || echo "Unknown")
                    echo "  $(basename "$nvme"): ${model} (${serial})"
                fi
            done
        fi
    fi

    print_subsection "RAID/MD Arrays"
    if [[ -f /proc/mdstat ]]; then
        cat /proc/mdstat
    else
        echo "[No software RAID configured]"
    fi

    print_subsection "LVM Configuration"
    if cmd_exists pvs && [[ $EUID -eq 0 ]]; then
        echo "Physical Volumes:"
        pvs 2>/dev/null || echo "[None]"
        echo ""
        echo "Volume Groups:"
        vgs 2>/dev/null || echo "[None]"
        echo ""
        echo "Logical Volumes:"
        lvs 2>/dev/null || echo "[None]"
    else
        echo "[LVM commands require root or lvm2 not installed]"
    fi

    print_subsection "Filesystem Mounts"
    df -hT | grep -v "tmpfs\|devtmpfs\|squashfs"

    print_subsection "Filesystem Details (/etc/fstab)"
    grep -v "^#" /etc/fstab | grep -v "^$" || echo "[Empty or unreadable]"

    print_section "ZFS CONFIGURATION"

    print_subsection "ZFS Module Status"
    if lsmod | grep -q "^zfs"; then
        echo "ZFS module: LOADED"
        echo "ZFS version: $(cat /sys/module/zfs/version 2>/dev/null || modinfo zfs 2>/dev/null | grep "^version" | awk '{print $2}')"
    else
        echo "ZFS module: [not loaded]"
    fi

    print_subsection "ZFS Pools"
    if cmd_exists zpool; then
        zpool list 2>/dev/null || echo "[No pools or zpool not available]"
        echo ""
        echo "Pool status:"
        zpool status 2>/dev/null || echo "[Cannot get pool status]"
    else
        echo "[zpool command not found]"
    fi

    print_subsection "ZFS Datasets"
    if cmd_exists zfs; then
        zfs list 2>/dev/null | head -30 || echo "[No datasets or zfs not available]"
        echo ""
        echo "Dataset properties:"
        zfs list -o name,used,avail,refer,mountpoint,compression,recordsize 2>/dev/null | head -20 || true
    fi

    print_subsection "ZFS ARC Statistics"
    if [[ -f /proc/spl/kstat/zfs/arcstats ]]; then
        echo "ARC size: $(grep "^size " /proc/spl/kstat/zfs/arcstats | awk '{print $3/1024/1024/1024 " GB"}')"
        echo "ARC target: $(grep "^c " /proc/spl/kstat/zfs/arcstats | awk '{print $3/1024/1024/1024 " GB"}')"
        echo "ARC max: $(grep "^c_max " /proc/spl/kstat/zfs/arcstats | awk '{print $3/1024/1024/1024 " GB"}')"
        echo "ARC hits: $(grep "^hits " /proc/spl/kstat/zfs/arcstats | awk '{print $3}')"
        echo "ARC misses: $(grep "^misses " /proc/spl/kstat/zfs/arcstats | awk '{print $3}')"
    else
        echo "[ZFS ARC stats not available]"
    fi

    print_section "COCKPIT WEB MANAGEMENT"

    print_subsection "Cockpit Service Status"
    if cmd_exists systemctl; then
        echo "Cockpit.socket: $"
        echo "Cockpit.service: $"

        if systemctl is-active cockpit.socket &>/dev/null; then
            echo ""
            echo "Cockpit is accessible at: https://$:9090"
        fi
    fi

    print_subsection "Cockpit Packages Installed"
    if cmd_exists rpm; then
        rpm -qa 2>/dev/null | grep cockpit | sort || echo "[No cockpit packages]"
    elif cmd_exists dpkg; then
        dpkg -l 2>/dev/null | grep cockpit | awk '{print $2}' | sort || echo "[No cockpit packages]"
    elif cmd_exists pacman; then
        pacman -Q 2>/dev/null | grep cockpit | sort || echo "[No cockpit packages]"
    fi

    print_subsection "Cockpit Machines (VM Management)"
    if [[ -d /usr/share/cockpit/machines ]]; then
        echo "Cockpit-machines: installed"
    else
        echo "Cockpit-machines: [not installed - needed for VM management]"
    fi

    print_section "NETWORK INTERFACES"

    print_subsection "Network Devices"
    ip -br link show

    print_subsection "Network Details"
    ip -d link show

    print_subsection "IP Addresses"
    ip -br addr show

    print_subsection "Network Hardware"
    lspci -nnk | grep -A3 -E "Network|Ethernet|Wi-Fi|Wireless" || echo "[None found]"

    print_subsection "Bridge/Bond Configuration"
    if cmd_exists brctl; then
        brctl show 2>/dev/null || echo "[No bridges]"
    fi

    if [[ -d /sys/class/net ]]; then
        echo ""
        echo "Interface Details:"
        for iface in /sys/class/net/*; do
            if [[ -d "$iface" ]]; then
                name=$(basename "$iface")
                [[ "$name" == "lo" ]] && continue
                driver=$(basename "$(readlink "${iface}/device/driver" 2>/dev/null)" 2>/dev/null || echo "N/A")
                mac=$(cat "${iface}/address" 2>/dev/null || echo "N/A")
                mtu=$(cat "${iface}/mtu" 2>/dev/null || echo "N/A")
                speed=$(cat "${iface}/speed" 2>/dev/null || echo "N/A")
                echo "  ${name}: driver=${driver}, MAC=${mac}, MTU=${mtu}, Speed=${speed}Mbps"
            fi
        done
    fi

    print_section "USB DEVICES"

    print_subsection "USB Device Tree"
    run_if_exists lsusb -t

    print_subsection "USB Devices (Detailed)"
    run_if_exists lsusb -v 2>/dev/null | grep -E "^Bus|idVendor|idProduct|bcdUSB|iManufacturer|iProduct|iSerial" | head -100

    print_subsection "USB Controllers"
    lspci -nnk | grep -A2 -i usb || echo "[None found]"

    print_section "INPUT DEVICES"

    print_subsection "Input Devices"
    if [[ -f /proc/bus/input/devices ]]; then
        cat /proc/bus/input/devices
    else
        echo "[Input device info not available]"
    fi

    print_section "KERNEL & MODULES"

    print_subsection "Kernel Information"
    echo "Kernel: $"
    echo "Version: $"
    echo "Compiled: $(uname -v | grep -oE "#[0-9]+" || echo 'N/A')"

    print_subsection "Kernel Command Line"
    cat /proc/cmdline

    print_subsection "Loaded Modules (Virtualization Related)"
    lsmod | grep -E "kvm|vfio|iommu|nvidia|amdgpu|virtio|vhost" | sort || echo "[None found]"

    print_subsection "All Loaded Modules"
    lsmod | sort

    print_section "VIRTUALIZATION STATUS"

    print_subsection "Virtualization Type Detection"
    if cmd_exists systemd-detect-virt; then
        virt_type=$(systemd-detect-virt 2>/dev/null || echo "None")
        echo "Detected virtualization: ${virt_type}"
    fi

    if [[ -f /sys/hypervisor/type ]]; then
        echo "Hypervisor type: $"
    fi

    if [[ -d /sys/hypervisor ]] || grep -qE "hypervisor|VMware|VirtualBox|KVM|Xen|QEMU" /proc/cpuinfo 2>/dev/null; then
        echo "System appears to be: VIRTUAL MACHINE"
    else
        echo "System appears to be: BARE METAL"
    fi

    print_subsection "Hardware Virtualization Support"
    echo "CPU Virtualization Extensions:"
    if grep -q "vmx" /proc/cpuinfo; then
        echo "  Intel VT-x: SUPPORTED"
        if [[ -c /dev/kvm ]]; then
            echo "  VT-x Status: ENABLED"
        else
            echo "  VT-x Status: May be disabled in BIOS"
        fi
    fi
    if grep -q "svm" /proc/cpuinfo; then
        echo "  AMD-V: SUPPORTED"
        if [[ -c /dev/kvm ]]; then
            echo "  AMD-V Status: ENABLED"
        else
            echo "  AMD-V Status: May be disabled in BIOS"
        fi
    fi

    if grep -q "ept" /proc/cpuinfo; then
        echo "  Intel EPT: SUPPORTED"
    fi
    if grep -q "npt" /proc/cpuinfo; then
        echo "  AMD NPT: SUPPORTED"
    fi

    if grep -q "avic" /proc/cpuinfo; then
        echo "  AMD AVIC: SUPPORTED"
    fi

    print_subsection "KVM Configuration"
    if [[ -c /dev/kvm ]]; then
        echo "/dev/kvm: EXISTS"
        ls -la /dev/kvm
        echo ""

        echo "KVM Module Parameters:"
        if [[ -d /sys/module/kvm/parameters ]]; then
            for p in /sys/module/kvm/parameters/*; do
                if [[ -f "$p" ]]; then
                    pname=$(basename "$p")
                    pval=$(cat "$p" 2>/dev/null || echo "")
                    echo "  kvm.${pname} = ${pval}"
                fi
            done
        fi

        if [[ -d /sys/module/kvm_intel/parameters ]]; then
            echo ""
            echo "KVM Intel Parameters:"
            for p in /sys/module/kvm_intel/parameters/*; do
                if [[ -f "$p" ]]; then
                    pname=$(basename "$p")
                    pval=$(cat "$p" 2>/dev/null || echo "")
                    echo "  kvm_intel.${pname} = ${pval}"
                fi
            done
        fi

        if [[ -d /sys/module/kvm_amd/parameters ]]; then
            echo ""
            echo "KVM AMD Parameters:"
            for p in /sys/module/kvm_amd/parameters/*; do
                if [[ -f "$p" ]]; then
                    pname=$(basename "$p")
                    pval=$(cat "$p" 2>/dev/null || echo "")
                    echo "  kvm_amd.${pname} = ${pval}"
                fi
            done
        fi
    else
        echo "/dev/kvm: NOT FOUND"
        echo "KVM not available - check BIOS settings or load kvm modules"
    fi

    print_subsection "Nested Virtualization"
    nested_intel=""
    nested_amd=""
    if [[ -f /sys/module/kvm_intel/parameters/nested ]]; then
        nested_intel=$(cat /sys/module/kvm_intel/parameters/nested)
        echo "Intel Nested Virtualization: ${nested_intel}"
    fi
    if [[ -f /sys/module/kvm_amd/parameters/nested ]]; then
        nested_amd=$(cat /sys/module/kvm_amd/parameters/nested)
        echo "AMD Nested Virtualization: ${nested_amd}"
    fi
    if [[ -z "$nested_intel" && -z "$nested_amd" ]]; then
        echo "Nested virtualization: [KVM modules not loaded]"
    fi

    print_subsection "VFIO Configuration Summary"
    echo "VFIO Modules:"
    lsmod | grep vfio || echo "[VFIO modules not loaded]"

    if [[ -d /sys/bus/pci/drivers/vfio-pci ]]; then
        echo ""
        echo "Devices bound to vfio-pci:"
        dir_names /sys/bus/pci/drivers/vfio-pci | grep -E "^[0-9a-f]{4}:" | while read -r addr; do
            desc=$(lspci -s "$addr" 2>/dev/null | cut -d: -f3-)
            echo "  ${addr}:${desc}"
        done
        [[ $(dir_names /sys/bus/pci/drivers/vfio-pci | grep -cE "^[0-9a-f]{4}:") -eq 0 ]] && echo "  [None]"
    fi

    print_subsection "libvirt/QEMU Status"
    if cmd_exists virsh; then
        echo "Libvirt version: $"
        echo "Libvirtd status: $"
        if [[ $EUID -eq 0 ]] || groups | grep -qE "libvirt|kvm"; then
            echo ""
            echo "Virtual Networks:"
            virsh net-list --all 2>/dev/null || echo "[Cannot list networks]"
            echo ""
            echo "Storage Pools:"
            virsh pool-list --all 2>/dev/null || echo "[Cannot list pools]"
        fi
    else
        echo "Libvirt: [not installed]"
    fi

    if cmd_exists qemu-system-x86_64; then
        echo ""
        echo "QEMU version: $"
    fi

    print_subsection "Container Runtimes"
    echo "Docker: $"
    echo "Podman: $"
    echo "LXC: $"
    echo "Containerd: $"

    print_section "CGROUPS & RESOURCE CONTROL"

    print_subsection "Cgroup Version"
    if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then
        echo "Cgroup Version: v2"
        echo "Available Controllers: $"
    elif [[ -d /sys/fs/cgroup/cpu ]]; then
        echo "Cgroup Version: v1"
        echo "Mounted Controllers:"
        dir_names /sys/fs/cgroup | grep -v "unified" | tr '\n' ' '
        echo ""
    else
        echo "Cgroup: [Not detected]"
    fi

    print_subsection "CPU Cgroup Configuration"
    if [[ -d /sys/fs/cgroup/cpu ]]; then
        echo "CPU Cgroup:"
        echo "  cpu.cfs_period_us: $"
        echo "  cpu.cfs_quota_us: $"
    fi

    if [[ -f /sys/fs/cgroup/cpuset.cpus.effective ]]; then
        echo "Cpuset:"
        echo "  Effective CPUs: $"
        echo "  Effective Mems: $"
    elif [[ -d /sys/fs/cgroup/cpuset ]]; then
        echo "Cpuset:"
        echo "  cpuset.cpus: $"
        echo "  cpuset.mems: $"
    fi

    print_subsection "Memory Cgroup"
    if [[ -f /sys/fs/cgroup/memory.max ]]; then
        echo "Memory limit: $"
        echo "Memory current: $"
    elif [[ -d /sys/fs/cgroup/memory ]]; then
        echo "Memory limit: $"
    fi

    print_section "SECURITY & KERNEL FEATURES"

    print_subsection "Security Modules"
    echo "LSM:"
    if [[ -f /sys/kernel/security/lsm ]]; then
        echo "  Active LSMs: $"
    fi

    if cmd_exists getenforce; then
        echo "  SELinux: $"
    elif [[ -d /sys/fs/selinux ]]; then
        echo "  SELinux: present but getenforce not available"
    fi

    if [[ -d /sys/kernel/security/apparmor ]]; then
        echo "  AppArmor: enabled"
        if cmd_exists aa-status && [[ $EUID -eq 0 ]]; then
            aa-status 2>/dev/null | head -5
        fi
    fi

    print_subsection "Kernel Security Features"
    echo "Kernel Lockdown: $"
    echo "Secure Boot: \$ ? Enabled : Disabled}' || echo '[Cannot detect]')"
    echo "KASLR: $(grep -q "nokaslr" /proc/cmdline && echo 'Disabled' || echo 'Enabled (default)')"

    echo ""
    echo "Kernel Hardening Settings:"
    echo "  kernel.randomize_va_space: $"
    echo "  kernel.dmesg_restrict: $"
    echo "  kernel.kptr_restrict: $"
    echo "  kernel.yama.ptrace_scope: $"

    print_subsection "Spectre/Meltdown Mitigations"
    if [[ -d /sys/devices/system/cpu/vulnerabilities ]]; then
        echo "CPU Vulnerability Mitigations:"
        for vuln in /sys/devices/system/cpu/vulnerabilities/*; do
            if [[ -f "$vuln" ]]; then
                vuln_name=$(basename "$vuln")
                vuln_status=$(cat "$vuln" 2>/dev/null | cut -c1-60)
                printf "  %-25s %s\n" "${vuln_name}:" "$vuln_status"
            fi
        done
    fi

    print_section "POWER MANAGEMENT"

    print_subsection "Power State"
    if [[ -f /sys/power/state ]]; then
        echo "Supported sleep states: $"
    fi

    if cmd_exists acpi; then
        acpi -V 2>/dev/null || true
    fi

    print_subsection "CPU Power States"
    if [[ -d /sys/devices/system/cpu/cpu0/cpuidle ]]; then
        echo "C-states available:"
        for state in /sys/devices/system/cpu/cpu0/cpuidle/state*; do
            if [[ -d "$state" ]]; then
                name=$(cat "${state}/name" 2>/dev/null || echo "Unknown")
                desc=$(cat "${state}/desc" 2>/dev/null || echo "N/A")
                echo "  ${name}: ${desc}"
            fi
        done
    fi

    print_section "SENSORS & THERMAL"

    print_subsection "Temperature Sensors"
    if cmd_exists sensors; then
        sensors 2>/dev/null || echo "[sensors command failed]"
    else
        echo "[lm-sensors not installed]"
        if [[ -d /sys/class/thermal ]]; then
            echo "Thermal zones from sysfs:"
            for tz in /sys/class/thermal/thermal_zone*; do
                if [[ -d "$tz" ]]; then
                    type=$(cat "${tz}/type" 2>/dev/null || echo "Unknown")
                    temp=$(cat "${tz}/temp" 2>/dev/null || echo "0")
                    echo "  $(basename "$tz"): ${type} = $((temp/1000))Â°C"
                fi
            done
        fi
    fi

    print_section "FIRMWARE & BOOT"

    print_subsection "Boot Mode"
    if [[ -d /sys/firmware/efi ]]; then
        echo "Boot Mode: UEFI"
        echo "Secure Boot: $"
    else
        echo "Boot Mode: Legacy BIOS"
    fi

    print_subsection "UEFI Variables (if available)"
    if cmd_exists efibootmgr && [[ $EUID -eq 0 ]]; then
        efibootmgr -v 2>/dev/null || echo "[efibootmgr failed]"
    else
        echo "[efibootmgr requires root]"
    fi

    print_section "SYSTEM SERVICES & PROCESSES"

    print_subsection "System Uptime & Load"
    uptime

    print_subsection "Running Services (systemd)"
    if cmd_exists systemctl; then
        systemctl list-units --type=service --state=running --no-pager | head -30
        echo "... [truncated - $ total running services]"
    fi

    print_section "LIBVIRT & VM CONFIGURATION"

    print_subsection "libvirt Daemon Status"
    if cmd_exists systemctl; then
        echo "Libvirtd: $"
        echo "Virtlogd: $"
        echo "Virtlockd: $"
    fi

    print_subsection "libvirt Version & Capabilities"
    if cmd_exists virsh; then
        echo "Libvirt version: $"
        echo ""
        echo "Hypervisor capabilities:"
        virsh capabilities 2>/dev/null | grep -E "<arch>|<domain|<machine" | head -20 || echo "[Cannot get capabilities]"
    fi

    print_subsection "QEMU Version & Emulators"
    if cmd_exists qemu-system-x86_64; then
        qemu-system-x86_64 --version 2>/dev/null | head -3
    fi
    echo ""
    echo "Available QEMU emulators:"
    ls -la /usr/bin/qemu-system-* 2>/dev/null | awk '{print $NF}' | xargs -I{} basename {} || echo "[None found]"

    print_subsection "Defined VMs (virsh list)"
    if cmd_exists virsh; then
        virsh list --all 2>/dev/null || echo "[Cannot list VMs]"
    fi

    print_subsection "VM Configurations (XML Summary)"
    if cmd_exists virsh; then
        for vm_name in $(virsh list --all --name 2>/dev/null | grep -v "^$"); do
            echo ""
            echo "├─── VM: $vm_name ───────────────────────────────────────────────────────"
            virsh dominfo "$vm_name" 2>/dev/null | grep -E "Name|UUID|OS Type|State|CPU|Max memory|Used memory"
            echo ""
            echo "  vCPU Configuration:"
            virsh vcpuinfo "$vm_name" 2>/dev/null | head -20 || echo "    [Not running]"
            echo ""
            echo "  CPU Pinning:"
            virsh vcpupin "$vm_name" 2>/dev/null || echo "    [No pinning or not running]"
            echo ""
            echo "  Emulator Pinning:"
            virsh emulatorpin "$vm_name" 2>/dev/null || echo "    [No emulator pinning]"
            echo ""
            echo "  Memory:"
            virsh numatune "$vm_name" 2>/dev/null || echo "    [No NUMA tuning]"
            echo ""
            echo "  Disks:"
            virsh domblklist "$vm_name" 2>/dev/null || echo "    [Cannot list disks]"
            echo ""
            echo "  Network Interfaces:"
            virsh domiflist "$vm_name" 2>/dev/null || echo "    [Cannot list interfaces]"
            echo ""
            echo "  PCI Passthrough Devices:"
            virsh dumpxml "$vm_name" 2>/dev/null | grep -A5 "<hostdev.*pci" | grep -E "domain|bus|slot|function" || echo "    [None]"
            echo "└──────────────────────────────────────────────────────────────────────────"
        done
    fi

    print_subsection "libvirt Hooks"
    echo "Hooks directory: /etc/libvirt/hooks/"
    if [[ -d /etc/libvirt/hooks ]]; then
        echo ""
        echo "Installed hooks:"
        ls -la /etc/libvirt/hooks/ 2>/dev/null
        echo ""
        for hook in /etc/libvirt/hooks/*; do
            if [[ -f "$hook" && -x "$hook" ]]; then
                echo "$hook) hook (first 30 lines) ---"
                head -30 "$hook" 2>/dev/null
                echo ""
                echo ""
            fi
        done
    else
        echo "[Hooks directory not found]"
    fi

    print_subsection "libvirt Storage Pools"
    if cmd_exists virsh; then
        virsh pool-list --all 2>/dev/null || echo "[Cannot list pools]"
        echo ""
        for pool in $(virsh pool-list --all --name 2>/dev/null | grep -v "^$"); do
            echo "Pool: $pool"
            virsh pool-info "$pool" 2>/dev/null | grep -E "Name|UUID|State|Capacity|Allocation|Available"
            echo ""
        done
    fi

    print_subsection "libvirt Networks (Detailed)"
    if cmd_exists virsh; then
        virsh net-list --all 2>/dev/null || echo "[Cannot list networks]"
        echo ""
        for net in $(virsh net-list --all --name 2>/dev/null | grep -v "^$"); do
            echo "Network: $net"
            virsh net-info "$net" 2>/dev/null
            echo "  Bridge: $(virsh net-dumpxml "$net" 2>/dev/null | grep -oP "(?<=bridge name=')[^']+")"
            echo "  IP Range: $(virsh net-dumpxml "$net" 2>/dev/null | grep -oP "(?<=range start=')[^']+") - $(virsh net-dumpxml "$net" 2>/dev/null | grep -oP "(?<=end=')[^']+")"
            echo ""
        done
    fi

    print_section "LOOKING GLASS CONFIGURATION"

    print_subsection "Looking Glass Installation"
    if cmd_exists looking-glass-client; then
        echo "Looking-glass-client: $"
    else
        echo "Looking-glass-client: [not installed]"
    fi

    print_subsection "KVMFR (Shared Memory) Module"
    if lsmod | grep -q kvmfr; then
        echo "Kvmfr module: LOADED"
        echo ""
        echo "Module parameters:"
        for p in /sys/module/kvmfr/parameters/*; do
            if [[ -f "$p" ]]; then
                echo "  $(basename "$p"): $(cat "$p" 2>/dev/null)"
            fi
        done
    else
        echo "Kvmfr module: [not loaded]"
    fi

    print_subsection "KVMFR Devices"
    if [[ -d /dev ]]; then
        ls -la /dev/kvmfr* 2>/dev/null || echo "[No /dev/kvmfr* devices]"
    fi

    print_subsection "Looking Glass Shared Memory (tmpfs/shmem)"
    echo "Shared memory files:"
    ls -la /dev/shm/looking-glass* 2>/dev/null || echo "[No /dev/shm/looking-glass* files]"

    echo ""
    echo "Tmpfs mounts:"
    mount | grep tmpfs | grep -E "shm|hugepages" || echo "[No relevant tmpfs mounts]"

    print_subsection "Looking Glass Config Files"
    for cfg in ~/.looking-glass-client.ini /etc/looking-glass-client.ini; do
        if [[ -f "$cfg" ]]; then
            echo "Config: $cfg"
            cat "$cfg" 2>/dev/null
            echo ""
        fi
    done
    [[ ! -f ~/.looking-glass-client.ini && ! -f /etc/looking-glass-client.ini ]] && echo "[No Looking Glass config files found]"

    print_subsection "VM ivshmem/KVMFR Configuration"
    if cmd_exists virsh; then
        for vm_name in $(virsh list --all --name 2>/dev/null | grep -v "^$"); do
            shmem=$(virsh dumpxml "$vm_name" 2>/dev/null | grep -A10 "<shmem" || true)
            if [[ -n "$shmem" ]]; then
                echo "VM: $vm_name"
                echo "$shmem" | sed 's/^/  /'
                echo ""
            fi
        done
    fi

    print_section "DISPLAY & GRAPHICS CONFIGURATION"

    print_subsection "Display Server"
    echo "XDG_SESSION_TYPE: ${XDG_SESSION_TYPE:-[not set]}"
    echo "WAYLAND_DISPLAY: ${WAYLAND_DISPLAY:-[not set]}"
    echo "DISPLAY: ${DISPLAY:-[not set]}"

    if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        echo "Session: Wayland"
    elif [[ -n "${DISPLAY:-}" ]]; then
        echo "Session: X11"
    else
        echo "Session: [Unknown/TTY]"
    fi

    print_subsection "Connected Monitors"
    if cmd_exists xrandr && [[ -n "${DISPLAY:-}" ]]; then
        xrandr --query 2>/dev/null | grep -E "connected|^\s+[0-9]+x[0-9]+" | head -20
    elif cmd_exists wlr-randr; then
        wlr-randr 2>/dev/null | head -30
    elif [[ -d /sys/class/drm ]]; then
        echo "DRM connectors:"
        for conn in /sys/class/drm/card*/card*-*; do
            if [[ -d "$conn" ]]; then
                name=$(basename "$conn")
                status=$(cat "${conn}/status" 2>/dev/null || echo "Unknown")
                if [[ "$status" == "connected" ]]; then
                    modes=$(cat "${conn}/modes" 2>/dev/null | head -3 | tr '\n' ', ')
                    echo "  ${name}: ${status} [${modes}]"
                fi
            fi
        done
    fi

    print_subsection "GPU Driver Details"
    for card in /sys/class/drm/card[0-9]*; do
        if [[ -d "$card" ]]; then
            card_name=$(basename "$card")
            driver=$(basename "$(readlink "${card}/device/driver" 2>/dev/null)" 2>/dev/null || echo "Unknown")
            pci_addr=$(basename "$(readlink "${card}/device" 2>/dev/null)" 2>/dev/null || echo "Unknown")

            echo "Card: ${card_name}"
            echo "  PCI: ${pci_addr}"
            echo "  Driver: ${driver}"

            if [[ "$driver" == "nvidia" ]] && cmd_exists nvidia-smi; then
                nvidia-smi --query-gpu=name,memory.total,memory.used,temperature.gpu,power.draw --format=csv,noheader -i "${pci_addr}" 2>/dev/null | sed 's/^/  /'
            fi

            if [[ "$driver" == "amdgpu" ]]; then
                [[ -f "${card}/device/gpu_busy_percent" ]] && echo "  GPU Busy: $(cat "${card}/device/gpu_busy_percent" 2>/dev/null)%"
                [[ -f "${card}/device/mem_info_vram_total" ]] && echo "  VRAM Total: $(cat "${card}/device/mem_info_vram_total" 2>/dev/null)"
                [[ -f "${card}/device/mem_info_vram_used" ]] && echo "  VRAM Used: $(cat "${card}/device/mem_info_vram_used" 2>/dev/null)"
            fi
            echo ""
        fi
    done

    print_section "AUDIO SUBSYSTEM (Detailed)"

    print_subsection "Audio Server Status"
    echo "PipeWire: $"
    echo "WirePlumber: $"
    echo "PulseAudio: $"
    echo "JACK: $"

    print_subsection "PipeWire Configuration"
    if cmd_exists pw-cli; then
        echo "PipeWire version: $"
        echo ""
        echo "PipeWire info:"
        pw-cli info 0 2>/dev/null | head -20 || echo "[Cannot get PipeWire info]"
    fi

    print_subsection "ALSA Devices"
    if [[ -f /proc/asound/cards ]]; then
        echo "Sound cards:"
        cat /proc/asound/cards
    fi

    if cmd_exists aplay; then
        echo ""
        echo "Playback devices:"
        aplay -l 2>/dev/null || echo "[Cannot list playback devices]"
    fi

    print_subsection "Audio PCI Devices"
    lspci -nnk | grep -A3 -iE "audio|sound|hda" || echo "[No audio PCI devices]"

    print_section "SYSTEMD UNITS (VM Related)"

    print_subsection "VM/Virtualization Services"
    if cmd_exists systemctl; then
        echo "Virtualization-related services:"
        systemctl list-units --type=service --all 2>/dev/null | grep -iE "libvirt|qemu|kvm|vfio|looking|spice|virtio" || echo "[None found]"
        echo ""
        echo "VM autostart timers/services:"
        systemctl list-units --type=service --all 2>/dev/null | grep -iE "vm-|virtual" || echo "[None found]"
    fi

    print_subsection "Custom VM Hook Services"
    if [[ -d /etc/systemd/system ]]; then
        echo "Custom systemd units related to VMs:"
        ls -la /etc/systemd/system/*vm* /etc/systemd/system/*vfio* /etc/systemd/system/*passthrough* 2>/dev/null || echo "[None found]"
    fi

    print_section "KERNEL BOOT & MODULE CONFIGURATION"

    print_subsection "All Kernel Command Line Parameters (Parsed)"
    echo "Boot parameters categorized:"
    echo ""
    echo ""
    echo "$CMDLINE" | tr ' ' '\n' | grep -iE "iommu|vfio|pcie|acs" | sed 's/^/  /' || echo "  [none]"
    echo ""
    echo ""
    echo "$CMDLINE" | tr ' ' '\n' | grep -iE "isolcpus|nohz|rcu_|irqaffinity|kthread|nosmt|mitigations" | sed 's/^/  /' || echo "  [none]"
    echo ""
    echo ""
    echo "$CMDLINE" | tr ' ' '\n' | grep -iE "hugepage|mem|numa|transparent" | sed 's/^/  /' || echo "  [none]"
    echo ""
    echo ""
    echo "$CMDLINE" | tr ' ' '\n' | grep -iE "selinux|apparmor|security|lockdown|kaslr|module.sig" | sed 's/^/  /' || echo "  [none]"
    echo ""
    echo ""
    echo "$CMDLINE" | tr ' ' '\n' | grep -iE "video|drm|nvidia|amdgpu|nouveau|i915|nomodeset" | sed 's/^/  /' || echo "  [none]"

    print_subsection "Module Blacklist"
    echo "Blacklisted modules:"
    if [[ -d /etc/modprobe.d ]]; then
        grep -rh "^blacklist" /etc/modprobe.d/ 2>/dev/null | sort -u | sed 's/^/  /' || echo "  [none found]"
    fi

    echo ""
    echo "Softdeps and install overrides:"
    grep -rhE "^(softdep|install|options)" /etc/modprobe.d/ 2>/dev/null | head -20 | sed 's/^/  /' || echo "  [none found]"

    print_subsection "Dracut/Initramfs VFIO Configuration"
    if [[ -d /etc/dracut.conf.d ]]; then
        echo "Dracut VFIO config:"
        grep -rh "vfio" /etc/dracut.conf.d/ 2>/dev/null | sed 's/^/  /' || echo "  [none found]"
    fi

    if [[ -f /etc/mkinitcpio.conf ]]; then
        echo ""
        echo "Mkinitcpio MODULES/HOOKS:"
        grep -E "^MODULES=|^HOOKS=" /etc/mkinitcpio.conf | sed 's/^/  /'
    fi

    print_section "COMPLETE HARDWARE INVENTORY (For Block Diagrams)"

    echo ""
    echo "This section provides a complete structured inventory of all hardware"
    echo "For creating block/flow diagrams of your Cloud WS configuration"
    echo ""

    print_subsection "=== CPU CORES INVENTORY ==="
    echo ""
    echo "CORE#  SMT_PAIR  CCD  L3_CACHE      ISOLATED  ASSIGNMENT"
    echo ""

    for cpu_path in /sys/devices/system/cpu/cpu[0-9]*; do
        if [[ -d "$cpu_path" ]]; then
            cpu_num=$(basename "$cpu_path" | sed 's/cpu//')

            smt_pair=$(cat "${cpu_path}/topology/thread_siblings_list" 2>/dev/null || echo "")

            ccd="?"
            l3_size="?"
            for cache in "${cpu_path}"/cache/index*; do
                level=$(cat "${cache}/level" 2>/dev/null || echo "")
                if [[ "$level" == "3" ]]; then
                    ccd=$(cat "${cache}/id" 2>/dev/null || echo "")
                    l3_size=$(cat "${cache}/size" 2>/dev/null || echo "")
                    break
                fi
            done

            isolated_list=$(cat /sys/devices/system/cpu/isolated 2>/dev/null || echo "")
            is_isolated="HOST"
            if [[ -n "$isolated_list" ]]; then
                for range in $(echo "$isolated_list" | tr ',' ' '); do
                    if [[ "$range" == *-* ]]; then
                        start=${range%-*}
                        end=${range#*-}
                        if [[ "$cpu_num" -ge "$start" && "$cpu_num" -le "$end" ]]; then
                            is_isolated="ISOLATED"
                            break
                        fi
                    elif [[ "$range" == "$cpu_num" ]]; then
                        is_isolated="ISOLATED"
                        break
                    fi
                done
            fi

            assignment="Host OS"
            if [[ "$is_isolated" == "ISOLATED" ]]; then
                assignment="VM Pool"
            fi

            printf "%-6s %-9s %-4s %-13s %-9s %s\n" "$cpu_num" "$smt_pair" "$ccd" "${l3_size:-?}" "$is_isolated" "$assignment"
        fi
    done

    print_subsection "=== PCI DEVICES INVENTORY ==="
    echo ""
    echo "PCI_ADDR         CLASS           VENDOR:DEV  IOMMU  DRIVER       ASSIGNMENT"
    echo ""

    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            pci_addr=$(basename "$dev")
            class_code=$(cat "${dev}/class" 2>/dev/null || echo "0x000000")
            vendor=$(cat "${dev}/vendor" 2>/dev/null | sed 's/0x//')
            device_id=$(cat "${dev}/device" 2>/dev/null | sed 's/0x//')
            driver=$(basename "$(readlink "${dev}/driver" 2>/dev/null)" 2>/dev/null || echo "None")
            iommu_group=$(basename "$(readlink "${dev}/iommu_group" 2>/dev/null)" 2>/dev/null || echo "")

            class_name="Other"
            case "$class_code" in
                0x030000|0x030200) class_name="GPU" ;;
                0x0300*) class_name="VGA" ;;
                0x0302*) class_name="3D" ;;
                0x020000) class_name="Ethernet" ;;
                0x0200*) class_name="Network" ;;
                0x010802) class_name="NVMe" ;;
                0x0106*) class_name="SATA" ;;
                0x0c03*) class_name="USB" ;;
                0x0403*) class_name="HD-Audio" ;;
                0x0401*) class_name="Audio" ;;
                0x0604*) class_name="PCI-Bridge" ;;
                0x0600*) class_name="Host-Bridge" ;;
                0x0580*) class_name="Memory" ;;
                0x1180*) class_name="Signal-Proc" ;;
                0x0c05*) class_name="SMBus" ;;
                0x0500*) class_name="RAM" ;;
                0x1101*) class_name="Crypto" ;;
            esac

            assignment="Host"
            if [[ "$driver" == "vfio-pci" ]]; then
                assignment="PASSTHROUGH"
            elif [[ "$driver" == "none" ]]; then
                assignment="Unbound"
            fi

            printf "%-16s %-15s %s:%s   %-6s %-12s %s\n" "$pci_addr" "$class_name" "$vendor" "$device_id" "$iommu_group" "$driver" "$assignment"
        fi
    done

    print_subsection "=== MEMORY INVENTORY ==="
    echo ""
    total_mem_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    total_mem_gb=$((total_mem_kb / 1024 / 1024))
    echo "Total System Memory: ${total_mem_gb} GB"
    echo ""

    if cmd_exists dmidecode && [[ $EUID -eq 0 ]]; then
        echo "DIMM Inventory:"
        dmidecode -t memory 2>/dev/null | grep -A20 "Memory Device" | grep -E "Size:|Locator:|Type:|Speed:|Manufacturer:|Part Number:" | paste - - - - - - 2>/dev/null | head -20 || true
    fi

    echo ""
    echo "Memory Allocation:"
    echo "  Host Reserved: ~4-8 GB"
    hugepages_total=$(grep HugePages_Total /proc/meminfo | awk '{print $2}')
    hugepage_size_kb=$(grep Hugepagesize /proc/meminfo | awk '{print $2}')
    if [[ -n "$hugepages_total" && "$hugepages_total" != "0" ]]; then
        hugepages_gb=$(echo "Scale=1; $hugepages_total * $hugepage_size_kb / 1024 / 1024" | bc 2>/dev/null || echo "")
        echo "  Hugepages Reserved: ${hugepages_gb} GB"
    else
        echo "  Hugepages Reserved: 0"
    fi

    print_subsection "=== STORAGE INVENTORY ==="
    echo ""
    echo "DEVICE     SIZE      TYPE   MODEL                          ASSIGNMENT"
    echo ""

    lsblk -d -o NAME,SIZE,TYPE,MODEL 2>/dev/null | tail -n +2 | while read -r name size type model; do
        assignment="Host"
        printf "%-10s %-9s %-6s %-30s %s\n" "$name" "$size" "$type" "${model:0:30}" "$assignment"
    done

    print_subsection "=== NETWORK INVENTORY ==="
    echo ""
    echo "INTERFACE      MAC                DRIVER      SPEED      ASSIGNMENT"
    echo ""

    for iface in /sys/class/net/*; do
        if [[ -d "$iface" ]]; then
            name=$(basename "$iface")
            [[ "$name" == "lo" ]] && continue

            mac=$(cat "${iface}/address" 2>/dev/null || echo "N/A")
            driver=$(basename "$(readlink "${iface}/device/driver" 2>/dev/null)" 2>/dev/null || echo "Virtual")
            speed=$(cat "${iface}/speed" 2>/dev/null || echo "N/A")
            [[ "$speed" != "N/A" ]] && speed="${speed}Mbps"

            assignment="Host"
            if [[ -d "${iface}/bridge" ]]; then
                assignment="Bridge (VMs)"
            fi

            printf "%-14s %-18s %-11s %-10s %s\n" "$name" "$mac" "$driver" "$speed" "$assignment"
        fi
    done

    print_subsection "=== USB CONTROLLERS INVENTORY ==="
    echo ""
    echo "PCI_ADDR         TYPE       IOMMU  DRIVER    ASSIGNMENT"
    echo ""

    for dev in /sys/bus/pci/devices/*; do
        if [[ -d "$dev" ]]; then
            class=$(cat "${dev}/class" 2>/dev/null || echo "")
            if [[ "$class" == 0x0c03* ]]; then
                pci_addr=$(basename "$dev")
                driver=$(basename "$(readlink "${dev}/driver" 2>/dev/null)" 2>/dev/null || echo "None")
                iommu_group=$(basename "$(readlink "${dev}/iommu_group" 2>/dev/null)" 2>/dev/null || echo "")

                usb_type="USB"
                case "$class" in
                    0x0c0300) usb_type="UHCI" ;;
                    0x0c0310) usb_type="OHCI" ;;
                    0x0c0320) usb_type="EHCI" ;;
                    0x0c0330) usb_type="xHCI" ;;
                esac

                assignment="Host"
                [[ "$driver" == "vfio-pci" ]] && assignment="PASSTHROUGH"

                printf "%-16s %-10s %-6s %-9s %s\n" "$pci_addr" "$usb_type" "$iommu_group" "$driver" "$assignment"
            fi
        fi
    done

    print_subsection "=== VM RESOURCE ALLOCATION SUMMARY ==="
    echo ""
    if cmd_exists virsh; then
        echo "VM_NAME                  STATE     vCPUs  MEMORY     GPU_PT  USB_PT"
        echo ""

        for vm_name in $(virsh list --all --name 2>/dev/null | grep -v "^$"); do
            state=$(virsh domstate "$vm_name" 2>/dev/null | head -1)
            vcpus=$(virsh vcpucount "$vm_name" --current 2>/dev/null || echo "")
            mem=$(virsh dominfo "$vm_name" 2>/dev/null | grep "Max memory" | awk '{printf "%.1f GB", $3/1024/1024}')

            xml=$(virsh dumpxml "$vm_name" 2>/dev/null || echo "")
            gpu_pt=$(echo "$xml" | grep -c "hostdev.*subsys='pci'" 2>/dev/null || echo "0")
            usb_pt=$(echo "$xml" | grep -c "hostdev.*subsys='usb'" 2>/dev/null || echo "0")

            printf "%-24s %-9s %-6s %-10s %-7s %s\n" "$vm_name" "$state" "$vcpus" "$mem" "$gpu_pt" "$usb_pt"
        done
    else
        echo "[virsh not available - cannot enumerate VMs]"
    fi

    print_section "SUMMARY"

    echo ""
    echo "───────────────────────────────────────────────────────────────────────────────"
    echo "                              SYSTEM SUMMARY"
    echo "───────────────────────────────────────────────────────────────────────────────"
    echo ""
    echo "├─ HARDWARE ─────────────────────────────────────────────────────────────────┤"
    echo "│ CPU: $(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | sed 's/^ //' | cut -c1-60)"
    echo "│ Cores/Threads: $(grep "cpu cores" /proc/cpuinfo | head -1 | cut -d: -f2 | tr -d ' ')/$(nproc)"
    echo "│ Memory: $"
    echo "│ GPUs: $ detected"
    echo "└─────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    echo "├─ SYSTEM ───────────────────────────────────────────────────────────────────┤"
    echo "│ OS: $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' | cut -c1-55)"
    echo "│ Kernel: $"
    echo "│ Boot Mode: $"
    echo "└─────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    echo "├─ VIRTUALIZATION READINESS ─────────────────────────────────────────────────┤"

    virt_hw="NOT DETECTED"
    grep -qE 'vmx|svm' /proc/cpuinfo && virt_hw="SUPPORTED"
    echo "│ Hardware Virtualization: ${virt_hw}"

    kvm_status="NOT AVAILABLE"
    [[ -c /dev/kvm ]] && kvm_status="AVAILABLE"
    echo "│ KVM: ${kvm_status}"

    iommu_status="DISABLED"
    if [[ -d /sys/kernel/iommu_groups ]]; then
        iommu_status="ENABLED"
    fi
    echo "│ IOMMU: ${iommu_status}"

    vfio_status="NOT LOADED"
    lsmod | grep -q "^vfio " && vfio_status="LOADED"
    echo "│ VFIO: ${vfio_status} bound)"

    nested="DISABLED"
    [[ "$(cat /sys/module/kvm_intel/parameters/nested 2>/dev/null)" == "Y" ]] && nested="ENABLED"
    [[ "$(cat /sys/module/kvm_amd/parameters/nested 2>/dev/null)" == "1" ]] && nested="ENABLED"
    echo "│ Nested Virtualization: ${nested}"

    echo "└─────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    echo "├─ CPU ISOLATION STATUS ─────────────────────────────────────────────────────┤"
    isolated=$(cat /sys/devices/system/cpu/isolated 2>/dev/null)
    if [[ -n "$isolated" && "$isolated" != "" ]]; then
        echo "│ Isolated CPUs: ${isolated}"
    else
        echo "│ Isolated CPUs: [none]"
    fi

    isolcpus_set="no"
    echo "$CMDLINE" | grep -q "isolcpus=" && isolcpus_set="yes"
    nohz_set="no"
    echo "$CMDLINE" | grep -q "nohz_full=" && nohz_set="yes"
    rcu_set="no"
    echo "$CMDLINE" | grep -q "rcu_nocbs=" && rcu_set="yes"

    echo "│ isolcpus: ${isolcpus_set}, nohz_full: ${nohz_set}, rcu_nocbs: ${rcu_set}"

    irqbalance_active="unknown"
    systemctl is-active irqbalance &>/dev/null && irqbalance_active="running" || irqbalance_active="stopped"
    echo "│ irqbalance: ${irqbalance_active}"
    echo "└─────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    echo "├─ PASSTHROUGH CHECKLIST ────────────────────────────────────────────────────┤"

    check_pass() {
        [[ $1 == "yes" ]] && echo "✔" || echo "✔"
    }

    echo "│ $ CPU Virtualization Extensions"
    echo "│ $ KVM Module Loaded"
    echo "│ $ IOMMU Enabled"
    echo "│ $ VFIO Core Module Loaded"
    echo "│ $ VFIO IOMMU Type1 Driver Loaded"

    echo "│ $ GPU Bound to VFIO"

    echo "└─────────────────────────────────────────────────────────────────────────────┘"

    echo ""
    echo "${SEPARATOR}"
    echo "  Assessment complete: $"
    echo "  Output saved to: ${OUTPUT_FILE}"
    echo "${SEPARATOR}"

} > "${OUTPUT_FILE}" 2>&1

echo -e "${GREEN}✔ System assessment complete${NC}"
echo -e "${CYAN}Output saved to:${NC} ${OUTPUT_FILE}"
echo ""
echo -e "${YELLOW}Quick Summary:${NC}"
tail -20 "${OUTPUT_FILE}" | head -15
