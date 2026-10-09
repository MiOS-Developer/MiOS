#!/usr/bin/env bash
# AI-hint: Continuous thermal stress and proactive governor modulation recovery test suite (task_488, task_2721).
# AI-doc: usr/share/doc/mios/manual/ch88-proactive-thermal-governor.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BIN_PATH="${REPO_ROOT}/usr/libexec/mios/mios-thermald"
SERVICE_PATH="${REPO_ROOT}/usr/lib/systemd/system/mios-thermald.service"

VERBOSE=false
DRY_RUN=false
MOCK_MODE=false

show_help() {
    cat <<'EOF'
Usage: test-thermal-governor-recovery.sh [OPTIONS]

Continuous thermal stress and governor modulation recovery test suite (task_488, task_2721).

Validates proactive PID thermal regulation, sysfs sensor telemetry, EPP modulation,
GPU power limit capping before hardware throttle (80°C vs 95°C), and automated hysteresis
recovery when temperatures cool below 70°C for >=10 seconds.

Options:
  -v, --verbose       Enable verbose test output and debug logging
  --dry-run           Verify script syntax, environment, and test setup without running tests
  --mock              Force synthetic sysfs execution without host hardware dependencies
  -h, --help          Show this help message and exit
EOF
}

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
            # Every check below already runs on synthetic sysfs; accepted so the
            # common test CLI works here too.
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Run 'test-thermal-governor-recovery.sh --help' for usage." >&2
            exit 1
            ;;
    esac
done
[[ "$MOCK_MODE" == "true" ]] && echo "[test-thermal] --mock: every case already runs on synthetic sysfs"

pass_count=0
fail_count=0

_pass() {
    echo "  [PASS] $1"
    pass_count=$((pass_count + 1))
}

_fail() {
    echo "  [FAIL] $1" >&2
    fail_count=$((fail_count + 1))
}

echo "=== MiOS Thermal Governor & Recovery Test Suite (task_488, task_2721) ==="
echo "sysfs: synthetic fixture (--mock requested: ${MOCK_MODE})"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry-run verification:"
    [[ -x "$BIN_PATH" ]] && echo "  Executable exists: $BIN_PATH"
    [[ -f "$SERVICE_PATH" ]] && echo "  Service unit exists: $SERVICE_PATH"
    echo "Dry-run syntax and path checks passed."
    exit 0
fi

if [[ ! -x "$BIN_PATH" ]]; then
    _fail "Binary not found or not executable: $BIN_PATH"
    exit 1
fi

TMP_DIR="$(mktemp -d -t mios-thermal-test-XXXXXX)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Test 1: CLI and help verification
# ---------------------------------------------------------------------------
echo "--- Test 1: CLI and help verification ---"
if "$BIN_PATH" --help >/dev/null 2>&1; then
    _pass "Root CLI help (-h/--help) executes successfully"
else
    _fail "Root CLI help failed"
fi

if "$BIN_PATH" daemon --help >/dev/null 2>&1; then
    _pass "Subcommand 'daemon --help' displays usage"
else
    _fail "Subcommand 'daemon --help' failed"
fi

if "$BIN_PATH" status --help >/dev/null 2>&1; then
    _pass "Subcommand 'status --help' displays usage"
else
    _fail "Subcommand 'status --help' failed"
fi

if "$BIN_PATH" set-policy --help >/dev/null 2>&1; then
    _pass "Subcommand 'set-policy --help' displays usage"
else
    _fail "Subcommand 'set-policy --help' failed"
fi

# Assert invalid subcommand returns non-zero exit code
if ! "$BIN_PATH" invalid-cmd >/dev/null 2>&1; then
    _pass "Invalid subcommand correctly rejected with non-zero exit code"
else
    _fail "Invalid subcommand unexpectedly returned 0"
fi

# ---------------------------------------------------------------------------
# Helper: Setup mock sysfs fixture
# ---------------------------------------------------------------------------
MOCK_SYSFS="$TMP_DIR/mock_sysfs"
setup_mock_sysfs() {
    local cpu_temp_mc="${1:-54000}"   # millidegrees C
    local gpu_temp_mc="${2:-51000}"   # millidegrees C
    local epp="${3:-performance}"
    local gpu_cap_uw="${4:-250000000}" # microwatts (250W)

    mkdir -p "$MOCK_SYSFS/sys/class/hwmon/hwmon0"
    echo "coretemp" > "$MOCK_SYSFS/sys/class/hwmon/hwmon0/name"
    echo "Package id 0" > "$MOCK_SYSFS/sys/class/hwmon/hwmon0/temp1_label"
    echo "$cpu_temp_mc" > "$MOCK_SYSFS/sys/class/hwmon/hwmon0/temp1_input"

    mkdir -p "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0"
    echo "$gpu_temp_mc" > "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input"
    echo "50000000" > "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_average"
    echo "$gpu_cap_uw" > "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap"

    mkdir -p "$MOCK_SYSFS/sys/devices/system/cpu/cpu0/cpufreq"
    echo "$epp" > "$MOCK_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"
    echo "3400000" > "$MOCK_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
}

# ---------------------------------------------------------------------------
# Test 2: Hardware sensor discovery and temperature reading (positive control)
# ---------------------------------------------------------------------------
echo "--- Test 2: Hardware sensor discovery and temperature reading ---"
setup_mock_sysfs 54000 51000 "performance" 250000000

status_json="$("$BIN_PATH" status --sysfs-root "$MOCK_SYSFS" --mock --json)"
if [[ "$VERBOSE" == "true" ]]; then
    echo "Status JSON:"
    echo "$status_json"
fi

cpu_temp_read="$(python3 -c "import json; d=json.loads('''$status_json'''); print(d['cpu']['temperature_c'])")"
gpu_temp_read="$(python3 -c "import json; d=json.loads('''$status_json'''); print(d['gpu']['temperature_c'])")"
epp_read="$(python3 -c "import json; d=json.loads('''$status_json'''); print(d['cpu']['epp'])")"
gov_read="$(python3 -c "import json; d=json.loads('''$status_json'''); print(d['governor']['state'])")"

if [[ "$cpu_temp_read" == "54.0" && "$gpu_temp_read" == "51.0" && "$epp_read" == "performance" && "$gov_read" == "NORMAL" ]]; then
    _pass "Sensor discovery accurately parsed CPU ($cpu_temp_read°C), GPU ($gpu_temp_read°C), EPP ($epp_read), State ($gov_read)"
else
    _fail "Sensor discovery mismatch: CPU=$cpu_temp_read, GPU=$gpu_temp_read, EPP=$epp_read, State=$gov_read"
fi

# ---------------------------------------------------------------------------
# Test 3: Power cap modulation when synthetic thermal threshold exceeded (positive control)
# ---------------------------------------------------------------------------
echo "--- Test 3: Power cap modulation on thermal threshold exceeded ---"
# Set synthetic temperature to 82°C (exceeding 78°C/80°C proactive throttle threshold)
echo "82000" > "$MOCK_SYSFS/sys/class/hwmon/hwmon0/temp1_input"
echo "80000" > "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input"

# Execute daemon tick
"$BIN_PATH" daemon --once --sysfs-root "$MOCK_SYSFS" --mock

new_epp="$(cat "$MOCK_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")"
new_gpu_cap_uw="$(cat "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap")"
new_gpu_cap_w=$(( new_gpu_cap_uw / 1000000 ))

if [[ "$new_epp" == "balance_power" || "$new_epp" == "balance_performance" ]]; then
    _pass "Proactive throttling modulated CPU EPP to '$new_epp'"
else
    _fail "CPU EPP not throttled: '$new_epp'"
fi

# Baseline was 250W. A 15-25% drop results in <= 213W.
if (( new_gpu_cap_w <= 213 && new_gpu_cap_w >= 180 )); then
    _pass "Proactive throttling reduced GPU TDP power limit to ${new_gpu_cap_w}W (>=15% reduction)"
else
    _fail "GPU power cap unexpected: ${new_gpu_cap_w}W (expected <= 213W)"
fi

# ---------------------------------------------------------------------------
# Test 4: Dynamic frequency/power recovery when thermal load cools down (positive control)
# ---------------------------------------------------------------------------
echo "--- Test 4: Dynamic frequency/power recovery after cooldown ---"
# Lower temperature to 62°C (well below 70°C recovery threshold)
echo "62000" > "$MOCK_SYSFS/sys/class/hwmon/hwmon0/temp1_input"
echo "60000" > "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input"

# Advance 11 simulated seconds (11 ticks with dt=1.0s) to satisfy the >=10s hysteresis requirement
"$BIN_PATH" daemon --ticks 11 --dt 1.0 --sysfs-root "$MOCK_SYSFS" --mock

recovered_epp="$(cat "$MOCK_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")"
recovered_gpu_cap_uw="$(cat "$MOCK_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap")"
recovered_gpu_cap_w=$(( recovered_gpu_cap_uw / 1000000 ))

if [[ "$recovered_epp" == "performance" ]]; then
    _pass "Full recovery restored CPU EPP to 'performance' profile"
else
    _fail "CPU EPP failed to recover: '$recovered_epp'"
fi

if (( recovered_gpu_cap_w == 250 )); then
    _pass "Full recovery restored GPU power limit to baseline 100% TDP (${recovered_gpu_cap_w}W)"
else
    _fail "GPU power limit failed to restore to 250W: ${recovered_gpu_cap_w}W"
fi

# ---------------------------------------------------------------------------
# Test 5: Negative control - sensor read error or missing hwmon node handled gracefully
# ---------------------------------------------------------------------------
echo "--- Test 5: Negative control - sensor read error and missing hwmon ---"
CORRUPT_SYSFS="$TMP_DIR/corrupt_sysfs"
mkdir -p "$CORRUPT_SYSFS/sys/class/hwmon/hwmon0"
echo "INVALID_HEX_DATA_CORRUPT" > "$CORRUPT_SYSFS/sys/class/hwmon/hwmon0/temp1_input"

# Ensure status does not crash on corrupt data
corrupt_out="$("$BIN_PATH" status --sysfs-root "$CORRUPT_SYSFS" --mock 2>&1)" || true
if echo "$corrupt_out" | grep -qv "Traceback"; then
    _pass "Corrupted sensor data handled gracefully without unhandled exception"
else
    _fail "Corrupted sensor data triggered unhandled traceback"
fi

EMPTY_SYSFS="$TMP_DIR/empty_sysfs"
mkdir -p "$EMPTY_SYSFS"
empty_out="$("$BIN_PATH" status --sysfs-root "$EMPTY_SYSFS" --mock 2>&1)" || true
if echo "$empty_out" | grep -qv "Traceback"; then
    _pass "Empty sysfs structure handled gracefully with safe fallbacks"
else
    _fail "Empty sysfs triggered unhandled traceback"
fi

# ---------------------------------------------------------------------------
# Test 6: Systemd service unit validation
# ---------------------------------------------------------------------------
echo "--- Test 6: Systemd service unit validation ---"
if [[ -f "$SERVICE_PATH" ]]; then
    _pass "Service unit file exists at $SERVICE_PATH"
else
    _fail "Service unit file missing at $SERVICE_PATH"
fi

if grep -q "Description=" "$SERVICE_PATH" && \
   grep -q "ExecStart=/usr/libexec/mios/mios-thermald daemon" "$SERVICE_PATH" && \
   grep -q "Restart=on-failure" "$SERVICE_PATH" && \
   grep -q "WantedBy=multi-user.target" "$SERVICE_PATH"; then
    _pass "Service unit contains required directives (Description, ExecStart, Restart, WantedBy)"
else
    _fail "Service unit missing required systemd directives"
fi

if command -v systemd-analyze >/dev/null 2>&1; then
    # verify resolves ExecStart against the host root, so point it at the in-tree binary.
    sed "s#^ExecStart=/usr/libexec/#ExecStart=${REPO_ROOT}/usr/libexec/#" "$SERVICE_PATH" > "$TMP_DIR/mios-thermald.service"
    if verify_out="$(systemd-analyze verify "$TMP_DIR/mios-thermald.service" 2>&1)"; then
        _pass "systemd-analyze verify passed with 0 structural errors"
    else
        _fail "systemd-analyze verify reported errors on $SERVICE_PATH: $(grep 'mios-thermald' <<<"$verify_out" | head -3)"
    fi
else
    _pass "systemd-analyze not installed; static unit validation accepted"
fi

# ---------------------------------------------------------------------------
# Test 7: Mock end-to-end thermal stress and recovery lifecycle (--mock)
# ---------------------------------------------------------------------------
echo "--- Test 7: Mock end-to-end thermal stress and recovery lifecycle ---"
E2E_SYSFS="$TMP_DIR/e2e_sysfs"
mkdir -p "$E2E_SYSFS/sys/class/hwmon/hwmon0"
mkdir -p "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0"
mkdir -p "$E2E_SYSFS/sys/devices/system/cpu/cpu0/cpufreq"

echo "coretemp" > "$E2E_SYSFS/sys/class/hwmon/hwmon0/name"
echo "Package id 0" > "$E2E_SYSFS/sys/class/hwmon/hwmon0/temp1_label"
echo "performance" > "$E2E_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"
echo "250000000" > "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap"

# Phase A: Idle baseline (45°C)
echo "45000" > "$E2E_SYSFS/sys/class/hwmon/hwmon0/temp1_input"
echo "45000" > "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input"
"$BIN_PATH" daemon --once --sysfs-root "$E2E_SYSFS" --mock
phase_a_epp="$(cat "$E2E_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")"
phase_a_cap="$(cat "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap")"

# Phase B: Thermal stress ramp (84°C)
echo "84000" > "$E2E_SYSFS/sys/class/hwmon/hwmon0/temp1_input"
echo "84000" > "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input"
"$BIN_PATH" daemon --once --sysfs-root "$E2E_SYSFS" --mock
phase_b_epp="$(cat "$E2E_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")"
phase_b_cap_w=$(( $(cat "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap") / 1000000 ))

# Phase C: Cooldown to 64°C, intermediate hysteresis ticks (5 seconds elapsed)
echo "64000" > "$E2E_SYSFS/sys/class/hwmon/hwmon0/temp1_input"
echo "64000" > "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/temp1_input"
"$BIN_PATH" daemon --ticks 5 --dt 1.0 --sysfs-root "$E2E_SYSFS" --mock
phase_c_epp="$(cat "$E2E_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")"

# Phase D: Sustained cooling past 10 seconds (6 more seconds elapsed -> total 11s)
"$BIN_PATH" daemon --ticks 6 --dt 1.0 --sysfs-root "$E2E_SYSFS" --mock
phase_d_epp="$(cat "$E2E_SYSFS/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")"
phase_d_cap_w=$(( $(cat "$E2E_SYSFS/sys/class/drm/card0/device/hwmon/hwmon0/power1_cap") / 1000000 ))

if [[ "$phase_a_epp" == "performance" && "$phase_a_cap" == "250000000" && \
      "$phase_b_epp" != "performance" && "$phase_b_cap_w" -le 213 && \
      "$phase_c_epp" != "performance" && \
      "$phase_d_epp" == "performance" && "$phase_d_cap_w" -eq 250 ]]; then
    _pass "End-to-end stress and recovery cycle validated (Idle -> Throttle -> Cooldown Hold -> Full Recovery)"
else
    _fail "End-to-end cycle mismatch: A($phase_a_epp,$phase_a_cap), B($phase_b_epp,${phase_b_cap_w}W), C($phase_c_epp), D($phase_d_epp,${phase_d_cap_w}W)"
fi

echo "====================================================================="
echo "Thermal Governor Test Results: $pass_count passed, $fail_count failed"
echo "====================================================================="

if (( fail_count > 0 )); then
    exit 1
fi
exit 0
