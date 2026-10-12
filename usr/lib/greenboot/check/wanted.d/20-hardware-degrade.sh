#!/usr/bin/env bash
# AI-hint: Peripheral hardware health evaluator and non-fatal Greenboot degradation reporter (task_475, task_2708).
# AI-doc: usr/share/doc/mios/manual/ch75-greenboot-hardware-degrade.md
set -euo pipefail


SCRIPT_NAME="20-hardware-degrade.sh"
TAG="greenboot-hardware"
DEFAULT_LOG_FILE="/var/log/mios-hardware-degrade.log"
LOG_FILE="${MIOS_HARDWARE_DEGRADE_LOG:-$DEFAULT_LOG_FILE}"

VERBOSE=false
DRY_RUN=false
MOCK_MODE=false
MOCK_DEGRADE="${MOCK_DEGRADE:-}"
MOCK_HEALTHY=false

# Sysfs / Proc probe paths (overridable for testing)
SYSFS_NET_DIR="${MIOS_SYSFS_NET_DIR:-/sys/class/net}"
PROC_ASOUND_FILE="${MIOS_ASOUND_CARDS_FILE:-/proc/asound/cards}"
DEV_DRI_DIR="${MIOS_DEV_DRI_DIR:-/dev/dri}"
SYSFS_DRM_DIR="${MIOS_SYSFS_DRM_DIR:-/sys/class/drm}"

show_help() {
    cat <<'EOF'
Usage: 20-hardware-degrade.sh [OPTIONS]

Peripheral hardware health evaluator and non-fatal Greenboot degradation reporter.

Probes network, audio, and display subsystems during bootc boot cycle. Under the
"degrade-not-refuse" philosophy, records degraded peripheral states to the systemd
journal, /var/log/mios-hardware-degrade.log, and PostgreSQL (if reachable), but
ALWAYS exits 0 to ensure non-blocking deployment promotion.

Options:
  -v, --verbose                 Enable detailed diagnostic logging to stdout
  --dry-run                     Evaluate hardware and format events without writing to log or DB
  --mock                        Run simulated evaluation of nominal and degraded states (asserts exit 0)
  --mock-degrade <subsystem>    Simulate degradation for specific subsystem (network|audio|display|all)
  --mock-healthy                Simulate nominal state for all subsystems
  --log-file <path>             Specify custom degradation log destination
  -h, --help                    Display this help message and exit

Environment Variables:
  MIOS_HARDWARE_DEGRADE_LOG     Path to output log file (default: /var/log/mios-hardware-degrade.log)
  MOCK_DEGRADE_NETWORK          Set to 1 to simulate network subsystem degradation
  MOCK_DEGRADE_AUDIO            Set to 1 to simulate audio subsystem degradation
  MOCK_DEGRADE_DISPLAY          Set to 1 to simulate display subsystem degradation
  MOCK_HEALTHY_ALL              Set to 1 to simulate healthy state for all subsystems
EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --mock)
            MOCK_MODE=true
            shift
            ;;
        --mock-degrade)
            if [[ $# -lt 2 ]]; then
                echo "Error: --mock-degrade requires an argument (network|audio|display|all)" >&2
                exit 1
            fi
            MOCK_DEGRADE="$2"
            shift 2
            ;;
        --mock-degrade=*)
            MOCK_DEGRADE="${1#*=}"
            shift
            ;;
        --mock-healthy)
            MOCK_HEALTHY=true
            shift
            ;;
        --log-file)
            if [[ $# -lt 2 ]]; then
                echo "Error: --log-file requires a path argument" >&2
                exit 1
            fi
            LOG_FILE="$2"
            shift 2
            ;;
        --log-file=*)
            LOG_FILE="${1#*=}"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            show_help >&2
            exit 1
            ;;
    esac
done

# Apply environment variable overrides for mocking
if [[ "${MOCK_HEALTHY_ALL:-0}" == "1" || "${MOCK_HEALTHY_ALL:-false}" == "true" ]]; then
    MOCK_HEALTHY=true
fi

log_diag() {
    if [[ "$VERBOSE" == "true" ]]; then
        echo "[$SCRIPT_NAME] DIAG: $*"
    fi
}

log_warn() {
    echo "[$SCRIPT_NAME] WARNING: $*" >&2
}

# ==============================================================================
# Subsystem Probes
# ==============================================================================

# Probe 1: Network interfaces (Ethernet / Wi-Fi)
probe_network() {
    local status="healthy"
    local details=""

    # Check for targeted mock degrade argument
    if [[ -n "$MOCK_DEGRADE" ]]; then
        if [[ "$MOCK_DEGRADE" == "network" || "$MOCK_DEGRADE" == "all" ]]; then
            status="degraded"
            details="Synthetic degradation: network controller link down / driver fault simulated"
        else
            status="healthy"
            details="Synthetic nominal: mock physical interface enp0s3 (operstate: UP, link: yes)"
        fi
        echo "$status|$details"
        return 0
    fi

    # Environment variable mock overrides
    if [[ "${MOCK_DEGRADE_NETWORK:-0}" == "1" || "${MOCK_DEGRADE_NETWORK:-false}" == "true" ]]; then
        status="degraded"
        details="Synthetic degradation: network controller link down / driver fault simulated"
        echo "$status|$details"
        return 0
    fi

    if [[ "$MOCK_HEALTHY" == "true" || "${MOCK_HEALTHY_ALL:-0}" == "1" || "${MOCK_HEALTHY_ALL:-false}" == "true" ]]; then
        status="healthy"
        details="Mock physical interface enp0s3 (operstate: UP, link: yes)"
        echo "$status|$details"
        return 0
    fi

    # Probe live sysfs / ip link
    local found_ifaces=()
    local up_ifaces=()

    if [[ -d "$SYSFS_NET_DIR" ]]; then
        for iface_path in "$SYSFS_NET_DIR"/*; do
            if [[ ! -e "$iface_path" ]]; then
                continue
            fi
            local iface
            iface="$(basename "$iface_path")"

            # Ignore loopback interface
            if [[ "$iface" == "lo" ]]; then
                continue
            fi

            found_ifaces+=("$iface")
            local operstate="unknown"
            if [[ -f "$iface_path/operstate" ]]; then
                operstate="$(cat "$iface_path/operstate" 2>/dev/null || echo "unknown")"
            fi

            if [[ "$operstate" == "up" || "$operstate" == "unknown" ]]; then
                up_ifaces+=("$iface ($operstate)")
            fi
        done
    fi

    # Secondary check with ip link if sysfs yielded no interfaces
    if [[ ${#found_ifaces[@]} -eq 0 ]] && command -v ip >/dev/null 2>&1; then
        while IFS= read -r line; do
            if [[ -n "$line" && "$line" != "lo" ]]; then
                found_ifaces+=("$line")
                up_ifaces+=("$line")
            fi
        done < <(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1 | grep -v '^lo$' || true)
    fi

    if [[ ${#found_ifaces[@]} -eq 0 ]]; then
        status="degraded"
        details="No physical or virtual network interfaces detected"
    elif [[ ${#up_ifaces[@]} -eq 0 ]]; then
        status="degraded"
        local iface_list
        iface_list="$(IFS=,; echo "${found_ifaces[*]}")"
        details="Interfaces present [${iface_list}] but none in UP state"
    else
        status="healthy"
        local up_list
        up_list="$(IFS=,; echo "${up_ifaces[*]}")"
        details="Active network interfaces: ${up_list}"
    fi

    echo "$status|$details"
}

# Probe 2: Audio devices (ALSA / PipeWire soundcards)
probe_audio() {
    local status="healthy"
    local details=""

    # Check for targeted mock degrade argument
    if [[ -n "$MOCK_DEGRADE" ]]; then
        if [[ "$MOCK_DEGRADE" == "audio" || "$MOCK_DEGRADE" == "all" ]]; then
            status="degraded"
            details="Synthetic degradation: ALSA soundcard uninitialized / missing audio device"
        else
            status="healthy"
            details="Synthetic nominal: mock soundcard 0: HDA-Intel [HDA Intel PCH]"
        fi
        echo "$status|$details"
        return 0
    fi

    # Environment variable mock overrides
    if [[ "${MOCK_DEGRADE_AUDIO:-0}" == "1" || "${MOCK_DEGRADE_AUDIO:-false}" == "true" ]]; then
        status="degraded"
        details="Synthetic degradation: ALSA soundcard uninitialized / missing audio device"
        echo "$status|$details"
        return 0
    fi

    if [[ "$MOCK_HEALTHY" == "true" || "${MOCK_HEALTHY_ALL:-0}" == "1" || "${MOCK_HEALTHY_ALL:-false}" == "true" ]]; then
        status="healthy"
        details="Mock soundcard 0: HDA-Intel [HDA Intel PCH]"
        echo "$status|$details"
        return 0
    fi

    # Probe live ALSA proc / aplay
    local cards_found=()

    if [[ -f "$PROC_ASOUND_FILE" ]]; then
        while IFS= read -r line; do
            # Format: ' 0 [PCH            ]: HDA-Intel - HDA Intel PCH'
            if [[ "$line" =~ ^[[:space:]]*([0-9]+)[[:space:]]+\[([^]]+)\]:[[:space:]]*(.*) ]]; then
                local card_num="${BASH_REMATCH[1]}"
                local card_id="${BASH_REMATCH[2]}"
                cards_found+=("card${card_num}:${card_id}")
            fi
        done < "$PROC_ASOUND_FILE"
    fi

    # Secondary check with aplay -l if proc/asound was empty or absent
    if [[ ${#cards_found[@]} -eq 0 ]] && command -v aplay >/dev/null 2>&1; then
        while IFS= read -r line; do
            if [[ "$line" =~ card[[:space:]]+([0-9]+):[[:space:]]+(.*) ]]; then
                cards_found+=("card${BASH_REMATCH[1]}")
            fi
        done < <(aplay -l 2>/dev/null || true)
    fi

    if [[ ${#cards_found[@]} -gt 0 ]]; then
        status="healthy"
        local card_list
        card_list="$(IFS=,; echo "${cards_found[*]}")"
        details="ALSA audio cards detected: ${card_list}"
    else
        status="degraded"
        details="No ALSA or PipeWire soundcards detected (/proc/asound/cards absent or empty)"
    fi

    echo "$status|$details"
}

# Probe 3: Display controllers (GPU / DRM nodes)
probe_display() {
    local status="healthy"
    local details=""

    # Check for targeted mock degrade argument
    if [[ -n "$MOCK_DEGRADE" ]]; then
        if [[ "$MOCK_DEGRADE" == "display" || "$MOCK_DEGRADE" == "all" ]]; then
            status="degraded"
            details="Synthetic degradation: DRM card node missing / GPU driver uninitialized"
        else
            status="healthy"
            details="Synthetic nominal: mock DRM device /dev/dri/card0 (driver: virtio-gpu / bochs-drm)"
        fi
        echo "$status|$details"
        return 0
    fi

    # Environment variable mock overrides
    if [[ "${MOCK_DEGRADE_DISPLAY:-0}" == "1" || "${MOCK_DEGRADE_DISPLAY:-false}" == "true" ]]; then
        status="degraded"
        details="Synthetic degradation: DRM card node missing / GPU driver uninitialized"
        echo "$status|$details"
        return 0
    fi

    if [[ "$MOCK_HEALTHY" == "true" || "${MOCK_HEALTHY_ALL:-0}" == "1" || "${MOCK_HEALTHY_ALL:-false}" == "true" ]]; then
        status="healthy"
        details="Mock DRM device /dev/dri/card0 (driver: virtio-gpu / bochs-drm)"
        echo "$status|$details"
        return 0
    fi

    # Probe live /dev/dri and /sys/class/drm
    local drm_nodes=()

    if [[ -d "$DEV_DRI_DIR" ]]; then
        for node in "$DEV_DRI_DIR"/card*; do
            if [[ -e "$node" ]]; then
                drm_nodes+=("$(basename "$node")")
            fi
        done
    fi

    if [[ ${#drm_nodes[@]} -eq 0 && -d "$SYSFS_DRM_DIR" ]]; then
        for node in "$SYSFS_DRM_DIR"/card*; do
            if [[ -e "$node" ]]; then
                drm_nodes+=("$(basename "$node")")
            fi
        done
    fi

    # Secondary check for PCI display / VGA controller if nodes missing
    local pci_display=false
    if compgen -G "/sys/bus/pci/devices/*" >/dev/null 2>&1; then
        for dev in /sys/bus/pci/devices/*; do
            if [[ -f "$dev/class" ]]; then
                local pci_class
                pci_class="$(cat "$dev/class" 2>/dev/null || echo "")"
                # PCI Class 0x03xxxx = Display controller (0300 = VGA, 0302 = 3D controller)
                if [[ "$pci_class" =~ ^0x03 ]]; then
                    pci_display=true
                    break
                fi
            fi
        done
    fi

    if [[ ${#drm_nodes[@]} -gt 0 ]]; then
        status="healthy"
        local node_list
        node_list="$(IFS=,; echo "${drm_nodes[*]}")"
        details="DRM display nodes detected: ${node_list}"
    elif [[ "$pci_display" == "true" ]]; then
        # PCI display controller exists but DRM node not yet instantiated
        status="degraded"
        details="PCI display controller present but DRM card nodes not materialized"
    else
        status="degraded"
        details="No DRM display nodes (/dev/dri/card*) or PCI display controllers found"
    fi

    echo "$status|$details"
}

# ==============================================================================
# Evaluation Engine
# ==============================================================================

execute_hardware_evaluation() {
    log_diag "Initiating peripheral hardware evaluation..."

    local net_res audio_res disp_res
    net_res="$(probe_network)"
    audio_res="$(probe_audio)"
    disp_res="$(probe_display)"

    local net_status="${net_res%%|*}"
    local net_details="${net_res#*|}"
    local audio_status="${audio_res%%|*}"
    local audio_details="${audio_res#*|}"
    local disp_status="${disp_res%%|*}"
    local disp_details="${disp_res#*|}"

    local degraded_subsystems=()
    local degraded_count=0

    if [[ "$net_status" != "healthy" ]]; then
        degraded_subsystems+=("network")
        degraded_count=$((degraded_count + 1))
    fi
    if [[ "$audio_status" != "healthy" ]]; then
        degraded_subsystems+=("audio")
        degraded_count=$((degraded_count + 1))
    fi
    if [[ "$disp_status" != "healthy" ]]; then
        degraded_subsystems+=("display")
        degraded_count=$((degraded_count + 1))
    fi

    local overall_status="nominal"
    local event_type="hardware_nominal"
    if [[ $degraded_count -gt 0 ]]; then
        overall_status="degraded"
        event_type="hardware_degraded"
    fi

    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ")"

    # Build structured JSON payload
    local json_payload
    if command -v python3 >/dev/null 2>&1; then
        json_payload="$(python3 -c '
import json, sys
ts, ev_type, ov_status, deg_count, net_st, net_dt, aud_st, aud_dt, dsp_st, dsp_dt = sys.argv[1:11]
data = {
    "timestamp": ts,
    "event_type": ev_type,
    "overall_status": ov_status,
    "degraded_count": int(deg_count),
    "policy": "degrade-not-refuse",
    "action": "log_and_promote",
    "subsystems": {
        "network": {"status": net_st, "details": net_dt},
        "audio": {"status": aud_st, "details": aud_dt},
        "display": {"status": dsp_st, "details": dsp_dt}
    }
}
print(json.dumps(data))
' "$timestamp" "$event_type" "$overall_status" "$degraded_count" \
  "$net_status" "$net_details" \
  "$audio_status" "$audio_details" \
  "$disp_status" "$disp_details")"
    else
        # Fallback pure-bash JSON formatting
        json_payload="{\"timestamp\":\"$timestamp\",\"event_type\":\"$event_type\",\"overall_status\":\"$overall_status\",\"degraded_count\":$degraded_count,\"policy\":\"degrade-not-refuse\",\"action\":\"log_and_promote\",\"subsystems\":{\"network\":{\"status\":\"$net_status\",\"details\":\"$net_details\"},\"audio\":{\"status\":\"$audio_status\",\"details\":\"$audio_details\"},\"display\":{\"status\":\"$disp_status\",\"details\":\"$disp_details\"}}}"
    fi

    # Formatted log summary
    local log_summary
    if [[ "$overall_status" == "nominal" ]]; then
        log_summary="Hardware state NOMINAL: all probed peripheral subsystems healthy (network, audio, display)."
    else
        local deg_list
        deg_list="$(IFS=,; echo "${degraded_subsystems[*]}")"
        log_summary="Hardware state DEGRADED [subsystems: ${deg_list}]: non-fatal per degrade-not-refuse policy; Greenboot promotion authorized."
    fi

    log_diag "Evaluation completed: overall_status=${overall_status}, degraded_count=${degraded_count}"
    log_diag "Summary: ${log_summary}"

    # Output to stdout for Greenboot / CLI caller
    echo "[$TAG] $log_summary"

    # Dispatch to Journal
    log_to_journal "$overall_status" "$log_summary" "$json_payload"

    # Persist to log file and PostgreSQL if not dry-run
    if [[ "$DRY_RUN" == "false" ]]; then
        persist_log_file "$timestamp" "$overall_status" "$log_summary" "$json_payload"
        persist_to_postgres "$json_payload" "$overall_status"
    else
        log_diag "Dry-run enabled: skipped file and database persistence"
    fi
}

# ==============================================================================
# Sinks & Dispatchers
# ==============================================================================

log_to_journal() {
    local status="$1"
    local summary="$2"
    local payload="$3"
    local priority="info"

    if [[ "$status" == "degraded" ]]; then
        priority="warning"
    fi

    # Send summary to journal via systemd-cat or logger
    if command -v systemd-cat >/dev/null 2>&1; then
        echo "$summary" | systemd-cat -t "$TAG" -p "$priority" 2>/dev/null || true
        echo "$payload" | systemd-cat -t "$TAG" -p debug 2>/dev/null || true
    elif command -v logger >/dev/null 2>&1; then
        logger -t "$TAG" -p "user.$priority" "$summary" 2>/dev/null || true
        logger -t "$TAG" -p "user.debug" "$payload" 2>/dev/null || true
    fi
}

persist_log_file() {
    local timestamp="$1"
    local status="$2"
    local summary="$3"
    local payload="$4"

    local target_dir
    target_dir="$(dirname "$LOG_FILE")"

    # Ensure parent directory exists or attempt fallback
    if [[ ! -d "$target_dir" ]]; then
        mkdir -p "$target_dir" 2>/dev/null || {
            if [[ $EUID -eq 0 ]] || sudo -n mkdir -p "$target_dir" 2>/dev/null; then
                :
            else
                log_warn "Cannot create directory $target_dir; falling back to /tmp/mios-hardware-degrade.log"
                LOG_FILE="/tmp/mios-hardware-degrade.log"
                target_dir="/tmp"
            fi
        }
    fi

    local log_line="[${timestamp}] [${status^^}] ${summary} | EVENT_JSON: ${payload}"

    # Write log entry with permission fallback
    if touch "$LOG_FILE" 2>/dev/null && [[ -w "$LOG_FILE" ]]; then
        echo "$log_line" >> "$LOG_FILE"
    elif [[ $EUID -eq 0 ]]; then
        echo "$log_line" >> "$LOG_FILE"
    elif sudo -n test -w "$LOG_FILE" 2>/dev/null || sudo -n touch "$LOG_FILE" 2>/dev/null; then
        echo "$log_line" | sudo -n tee -a "$LOG_FILE" >/dev/null
    else
        log_warn "Target $LOG_FILE is not writable; falling back to /tmp/mios-hardware-degrade.log"
        LOG_FILE="/tmp/mios-hardware-degrade.log"
        echo "$log_line" >> "$LOG_FILE"
    fi

    log_diag "Appended status record to $LOG_FILE"
}

persist_to_postgres() {
    local payload="$1"
    local status="$2"

    # Verify if mios-pg-query or psql exists
    local pg_query_bin=""
    if [[ -x "/usr/libexec/mios/mios-pg-query" ]]; then
        pg_query_bin="/usr/libexec/mios/mios-pg-query"
    elif [[ -f "/usr/libexec/mios/mios-pg-query" ]] && command -v python3 >/dev/null 2>&1; then
        pg_query_bin="python3 /usr/libexec/mios/mios-pg-query"
    elif [[ -x "/workspaces/MiOS/usr/libexec/mios/mios-pg-query" ]]; then
        pg_query_bin="/workspaces/MiOS/usr/libexec/mios/mios-pg-query"
    elif [[ -f "/workspaces/MiOS/usr/libexec/mios/mios-pg-query" ]] && command -v python3 >/dev/null 2>&1; then
        pg_query_bin="python3 /workspaces/MiOS/usr/libexec/mios/mios-pg-query"
    elif command -v psql >/dev/null 2>&1; then
        pg_query_bin="psql -U postgres -d postgres -c"
    fi

    if [[ -z "$pg_query_bin" ]]; then
        log_diag "PostgreSQL wire tool not available; skipping DB inventory record (fail-soft)"
        return 0
    fi

    # Prepare SQL statement
    local escaped_payload
    escaped_payload="${payload//\'/\'\'}"

    local sql="
        CREATE TABLE IF NOT EXISTS hardware_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            action text NOT NULL,
            subsystem text NOT NULL,
            sys_path text NOT NULL,
            event_payload jsonb NOT NULL,
            ts timestamptz DEFAULT now()
        );
        INSERT INTO hardware_events (action, subsystem, sys_path, event_payload)
        VALUES ('greenboot_hardware_health', 'peripherals', 'usr/lib/greenboot/check/wanted.d/20-hardware-degrade.sh', '${escaped_payload}'::jsonb);
    "

    # Execute query with strict non-blocking timeout and fail-soft behavior
    log_diag "Attempting PostgreSQL event insertion..."
    if command -v timeout >/dev/null 2>&1; then
        if timeout 2 bash -c "$pg_query_bin \"$sql\"" >/dev/null 2>&1; then
            log_diag "Successfully persisted hardware event to PostgreSQL hardware_events"
        else
            log_diag "PostgreSQL unavailable or connection timed out; skipping persistence (fail-soft)"
        fi
    else
        if $pg_query_bin "$sql" >/dev/null 2>&1; then
            log_diag "Successfully persisted hardware event to PostgreSQL hardware_events"
        else
            log_diag "PostgreSQL unavailable; skipping persistence (fail-soft)"
        fi
    fi
}

# ==============================================================================
# Mock Simulation Runner
# ==============================================================================

run_mock_simulation() {
    log_diag "Initiating comprehensive mock evaluation suite..."
    local sim_passed=0
    local sim_failed=0
    local target_script="${BASH_SOURCE[0]}"

    # Scenario 1: All hardware healthy
    log_diag "Simulating Scenario 1: Nominal hardware state..."
    local out1
    out1="$("$target_script" --mock-healthy --dry-run)"
    if echo "$out1" | grep -q "NOMINAL"; then
        sim_passed=$((sim_passed + 1))
    else
        sim_failed=$((sim_failed + 1))
    fi

    # Scenario 2: Degraded network
    log_diag "Simulating Scenario 2: Degraded network controller..."
    local out2
    out2="$("$target_script" --mock-degrade network --dry-run)"
    if echo "$out2" | grep -q "DEGRADED" && echo "$out2" | grep -q "network"; then
        sim_passed=$((sim_passed + 1))
    else
        sim_failed=$((sim_failed + 1))
    fi

    # Scenario 3: Degraded audio
    log_diag "Simulating Scenario 3: Degraded audio controller..."
    local out3
    out3="$("$target_script" --mock-degrade audio --dry-run)"
    if echo "$out3" | grep -q "DEGRADED" && echo "$out3" | grep -q "audio"; then
        sim_passed=$((sim_passed + 1))
    else
        sim_failed=$((sim_failed + 1))
    fi

    # Scenario 4: Degraded display
    log_diag "Simulating Scenario 4: Degraded display controller..."
    local out4
    out4="$("$target_script" --mock-degrade display --dry-run)"
    if echo "$out4" | grep -q "DEGRADED" && echo "$out4" | grep -q "display"; then
        sim_passed=$((sim_passed + 1))
    else
        sim_failed=$((sim_failed + 1))
    fi

    # Scenario 5: All degraded
    log_diag "Simulating Scenario 5: All peripheral controllers degraded..."
    local out5
    out5="$("$target_script" --mock-degrade all --dry-run)"
    if echo "$out5" | grep -q "DEGRADED"; then
        sim_passed=$((sim_passed + 1))
    else
        sim_failed=$((sim_failed + 1))
    fi

    echo "[$TAG] Mock simulation completed: $sim_passed passed, $sim_failed failed."
    echo "[$TAG] Non-fatal guarantee verified: all mock degradation scenarios promoted with exit code 0."
    return 0
}

# ==============================================================================
# Main Entry Point
# ==============================================================================

main() {
    if [[ "$MOCK_MODE" == "true" && -z "$MOCK_DEGRADE" && "$MOCK_HEALTHY" == "false" ]]; then
        run_mock_simulation
    else
        execute_hardware_evaluation
    fi

    # INVARIANT: Non-fatal Greenboot degradation reporter ALWAYS exits 0
    exit 0
}

main "$@"
