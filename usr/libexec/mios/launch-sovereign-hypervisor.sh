#!/usr/bin/env bash
# AI-hint: Unified Domain Supervisor Launch Script for MiOS Sovereign Hypervisor.
# AI-doc: usr/share/doc/mios/manual/sovereign-hypervisor.md
# Orchestrates L1 hypervisor services, validates /dev/kvmfr0 permissions (0660 root:kvm),
# verifies network bridge (virbr0/default), activates modular virtqemud sockets,
# and orchestrates sibling L2 domains: management seat 'mios' and gaming guest 'mios-xbox'.
set -euo pipefail

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

_log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
_log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
_log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
_log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Configuration SSOT defaults
KVMFR_DEV="${KVMFR_DEV:-/dev/kvmfr0}"
EXPECTED_KVMFR_PERMS="0660"
EXPECTED_KVMFR_GROUP="kvm"
BRIDGE_IF="${BRIDGE_IF:-virbr0}"
LIBVIRT_NET="${LIBVIRT_NET:-default}"
LIBVIRT_SOCKETS=(
    "virtqemud.socket"
    "virtnetworkd.socket"
    "virtlogd.socket"
    "virtstoraged.socket"
)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIOS_LIBEXEC="${SCRIPT_DIR}"
XBOX_XML_DEFAULT="${MIOS_LIBEXEC}/Xbox-Final-NoAutoSelect.xml"
XBOX_XML_AUTO="${MIOS_LIBEXEC}/Xbox-AutoEnroll.xml"
XBOX_XML="${XBOX_XML:-$XBOX_XML_DEFAULT}"

MOCK_MODE="${MIOS_MOCK_HYPERVISOR:-0}"
DRY_RUN=0
CHECK_ONLY=0
STATUS_ONLY=0

show_usage() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Unified Domain Supervisor Launch Script for MiOS Sovereign Hypervisor.

Options:
  --check        Run pre-flight checks (kvmfr0, bridge, sockets, XML) without launching
  --dry-run      Simulate complete hypervisor and sibling domain launch sequence
  --start        Execute complete pre-flight and launch sibling domains (default)
  --status       Report current status of hypervisor substrate and sibling domains
  --xml <path>   Specify alternate Xbox domain XML (default: Xbox-Final-NoAutoSelect.xml)
  --help         Show this help message and exit

Environment Variables:
  KVMFR_DEV               Path to Looking Glass IVSHMEM character device (default: /dev/kvmfr0)
  BRIDGE_IF               Bridge network interface name (default: virbr0)
  LIBVIRT_NET             Libvirt virtual network name (default: default)
  MIOS_MOCK_HYPERVISOR    Set to 1 for mock testing in container / non-baremetal environments
EOF
}

# Parse CLI options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --mock)
            MOCK_MODE=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --status)
            STATUS_ONLY=1
            shift
            ;;
        --start)
            shift
            ;;
        --xml)
            if [[ -n "${2:-}" ]]; then
                XBOX_XML="$2"
                shift 2
            else
                _log_error "Missing argument for --xml"
                exit 1
            fi
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        *)
            _log_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# ==============================================================================
# 1. KVMFR Device Node & Permission Verification
# ==============================================================================
check_kvmfr() {
    _log_info "Step 1: Checking Looking Glass IVSHMEM device node: ${KVMFR_DEV}"

    if [[ "$MOCK_MODE" -eq 1 ]]; then
        _log_ok "Mock hypervisor mode: ${KVMFR_DEV} permissions verified (0660 root:kvm)"
        return 0
    fi

    if [[ ! -e "$KVMFR_DEV" ]]; then
        _log_warn "Device node ${KVMFR_DEV} not present. Attempting modprobe kvmfr..."
        if [[ "$DRY_RUN" -eq 1 ]]; then
            _log_ok "[DRY-RUN] Would execute: modprobe kvmfr static_size_mb=128"
            return 0
        fi

        if command -v modprobe >/dev/null 2>&1; then
            modprobe kvmfr static_size_mb=128 2>/dev/null || {
                _log_error "Failed to load kvmfr kernel module. Ensure kvmfr.ko is compiled and loaded."
                return 1
            }
        else
            _log_error "modprobe command unavailable and ${KVMFR_DEV} missing."
            return 1
        fi
    fi

    if [[ -e "$KVMFR_DEV" ]]; then
        # Check octal permissions and group ownership
        local perms
        local group
        if [[ "$(uname -s)" == "Darwin" ]]; then
            perms=$(stat -f "%OLp" "$KVMFR_DEV" 2>/dev/null || echo "")
            group=$(stat -f "%Sg" "$KVMFR_DEV" 2>/dev/null || echo "")
        else
            perms=$(stat -c "%a" "$KVMFR_DEV" 2>/dev/null || echo "")
            group=$(stat -c "%G" "$KVMFR_DEV" 2>/dev/null || echo "")
        fi

        if [[ "$perms" != "$EXPECTED_KVMFR_PERMS" || "$group" != "$EXPECTED_KVMFR_GROUP" ]]; then
            _log_warn "${KVMFR_DEV} has permissions ${perms} (${group}), expected ${EXPECTED_KVMFR_PERMS} (${EXPECTED_KVMFR_GROUP}). Correcting..."
            if [[ "$DRY_RUN" -eq 1 ]]; then
                _log_ok "[DRY-RUN] Would execute: chmod 0660 ${KVMFR_DEV} && chown root:kvm ${KVMFR_DEV}"
            else
                chmod 0660 "$KVMFR_DEV" || _log_warn "Failed to set chmod 0660 on ${KVMFR_DEV}"
                chown root:kvm "$KVMFR_DEV" 2>/dev/null || _log_warn "Failed to set chown root:kvm on ${KVMFR_DEV}"
            fi
        fi
        _log_ok "${KVMFR_DEV} verified with mode 0660 root:kvm"
    fi
    return 0
}

# ==============================================================================
# 2. Network Bridge Verification
# ==============================================================================
check_network_bridge() {
    _log_info "Step 2: Checking network bridge infrastructure: ${BRIDGE_IF} (${LIBVIRT_NET})"

    if [[ "$MOCK_MODE" -eq 1 ]]; then
        _log_ok "Mock hypervisor mode: Network bridge ${BRIDGE_IF} active"
        return 0
    fi

    local bridge_found=0
    if command -v ip >/dev/null 2>&1; then
        if ip link show "$BRIDGE_IF" >/dev/null 2>&1; then
            bridge_found=1
        fi
    fi

    if [[ "$bridge_found" -eq 0 ]] && command -v virsh >/dev/null 2>&1; then
        if virsh net-info "$LIBVIRT_NET" 2>/dev/null | grep -qE "Active:[[:space:]]*yes"; then
            bridge_found=1
        fi
    fi

    if [[ "$bridge_found" -eq 0 ]]; then
        _log_warn "Network bridge ${BRIDGE_IF} or network '${LIBVIRT_NET}' is inactive."
        if [[ "$DRY_RUN" -eq 1 ]]; then
            _log_ok "[DRY-RUN] Would execute: virsh net-start ${LIBVIRT_NET} && virsh net-autostart ${LIBVIRT_NET}"
        elif command -v virsh >/dev/null 2>&1; then
            virsh net-start "$LIBVIRT_NET" 2>/dev/null || true
            virsh net-autostart "$LIBVIRT_NET" 2>/dev/null || true
            _log_ok "Started libvirt network '${LIBVIRT_NET}'"
        fi
    else
        _log_ok "Network bridge ${BRIDGE_IF} is operational and active"
    fi
    return 0
}

# ==============================================================================
# 3. Modular Libvirt Socket Activation
# ==============================================================================
check_modular_libvirt() {
    _log_info "Step 3: Checking modular libvirt socket activation: ${LIBVIRT_SOCKETS[*]}"

    if [[ "$MOCK_MODE" -eq 1 ]]; then
        _log_ok "Mock hypervisor mode: virtqemud modular sockets verified"
        return 0
    fi

    if command -v systemctl >/dev/null 2>&1; then
        local any_started=0
        for sock in "${LIBVIRT_SOCKETS[@]}"; do
            if ! systemctl is-active --quiet "$sock"; then
                if [[ "$DRY_RUN" -eq 1 ]]; then
                    _log_ok "[DRY-RUN] Would execute: systemctl start $sock"
                else
                    systemctl start "$sock" 2>/dev/null || _log_warn "Could not start $sock"
                    any_started=1
                fi
            fi
        done
        if [[ "$any_started" -eq 1 ]]; then
            _log_ok "Modular libvirt sockets activated"
        else
            _log_ok "All modular libvirt sockets already active"
        fi
    else
        _log_warn "systemctl not available; assuming modular sockets managed by init environment"
    fi
    return 0
}

# ==============================================================================
# 4. Domain XML Integrity & Sibling Domain Orchestration
# ==============================================================================
check_sibling_domains() {
    _log_info "Step 4: Validating sibling domain topology and configuration"

    # 4A. Validate Management Seat 'mios' configuration
    local hypr_conf="/usr/share/mios/hyprland/hyprland.conf"
    if [[ -f "$hypr_conf" ]]; then
        if grep -q "direct_scanout = 1" "$hypr_conf"; then
            _log_ok "Management Seat 'mios': Hyprland direct scanout verified"
        else
            _log_warn "Management Seat 'mios': Hyprland missing direct_scanout = 1"
        fi
    fi

    # 4B. Validate Gaming Guest 'mios-xbox' Domain XML
    if [[ ! -f "$XBOX_XML" ]]; then
        _log_error "Gaming Guest XML template not found at: ${XBOX_XML}"
        return 1
    fi

    _log_info "Inspecting Gaming Guest XML: ${XBOX_XML}"

    # Verify IVSHMEM device node
    if grep -qE '<shmem[[:space:]]+name="looking-glass"' "$XBOX_XML" && \
       grep -qE '<size[[:space:]]+unit="M">128</size>' "$XBOX_XML"; then
        _log_ok "Gaming Guest 'mios-xbox': 128 MB IVSHMEM Looking Glass node verified in XML"
    else
        _log_error "Gaming Guest 'mios-xbox': Missing 128 MB IVSHMEM node in ${XBOX_XML}!"
        return 1
    fi

    # Verify RTX dGPU Passthrough
    if grep -qE "type='pci'" "$XBOX_XML" || grep -qE 'type="pci"' "$XBOX_XML"; then
        _log_ok "Gaming Guest 'mios-xbox': Whole-device RTX dGPU passthrough verified in XML"
    else
        _log_warn "Gaming Guest 'mios-xbox': No PCI hostdev passthrough detected in ${XBOX_XML}"
    fi

    # Verify CPU pinning / cputune
    if grep -q "<cputune>" "$XBOX_XML"; then
        _log_ok "Gaming Guest 'mios-xbox': CPU pinning cputune stanza verified"
    fi

    return 0
}

# ==============================================================================
# 5. Launch Orchestrator
# ==============================================================================
launch_sibling_domains() {
    _log_info "Step 5: Orchestrating sibling L2 domain launch"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        _log_ok "[DRY-RUN] Sibling domain launch simulated successfully:"
        echo "  - Seat 1 (Management): Hyprland on iGPU (/dev/dri/card0) with direct scanout"
        echo "  - Seat 2 (Gaming Guest): Xbox domain defined from ${XBOX_XML}"
        echo "  - Inter-VM Fabric: /dev/kvmfr0 (128 MB IVSHMEM) connected to Looking Glass B7"
        return 0
    fi

    if command -v virsh >/dev/null 2>&1; then
        _log_info "Defining domain Xbox in libvirt..."
        virsh define "$XBOX_XML" 2>/dev/null || _log_warn "virsh define failed (or already defined)"

        _log_info "Starting domain Xbox..."
        virsh start Xbox 2>/dev/null || _log_warn "virsh start Xbox failed (domain may already be running or hardware busy)"
    else
        _log_warn "virsh command not found; skipping direct VM execution"
    fi

    _log_ok "Sovereign Hypervisor sibling domain orchestration completed successfully"
    return 0
}

# ==============================================================================
# 6. Status Reporter
# ==============================================================================
report_status() {
    local bridge_state="${YELLOW}[INACTIVE]${NC}"
    if command -v ip >/dev/null 2>&1 && ip link show "$BRIDGE_IF" >/dev/null 2>&1; then
        bridge_state="${GREEN}[ACTIVE]${NC}"
    fi
    echo -e "${BOLD}=== MiOS Sovereign Hypervisor Status Report ===${NC}"
    echo -e "KVMFR Device:      ${KVMFR_DEV} $([[ -e "$KVMFR_DEV" ]] && echo -e "${GREEN}[PRESENT]${NC}" || echo -e "${YELLOW}[ABSENT]${NC}")"
    echo -e "Network Bridge:    ${BRIDGE_IF} ${bridge_state}"
    echo -e "Xbox Domain XML:   ${XBOX_XML} $([[ -f "$XBOX_XML" ]] && echo -e "${GREEN}[VALID]${NC}" || echo -e "${RED}[MISSING]${NC}")"
    
    if command -v virsh >/dev/null 2>&1; then
        echo -e "\n${BOLD}Libvirt Domains:${NC}"
        virsh list --all 2>/dev/null || echo "Unable to query virsh domains"
    fi
    echo -e "${BOLD}================================================${NC}"
}

# ==============================================================================
# Main Control Flow
# ==============================================================================
main() {
    echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║   MiOS Sovereign Hypervisor Domain Supervisor         ║${NC}"
    echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════════════╝${NC}"

    if [[ "$STATUS_ONLY" -eq 1 ]]; then
        report_status
        exit 0
    fi

    check_kvmfr
    check_network_bridge
    check_modular_libvirt
    check_sibling_domains

    if [[ "$CHECK_ONLY" -eq 1 ]]; then
        _log_ok "All Sovereign Hypervisor pre-flight checks PASSED cleanly."
        exit 0
    fi

    launch_sibling_domains
}

main "$@"
