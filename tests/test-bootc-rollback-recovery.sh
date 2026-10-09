#!/usr/bin/env bash
# AI-hint: Verification suite for atomic bootc rollback, ostree deployment state, /var persistence guards, and greenboot health gates (task_3356).
# AI-doc: usr/share/doc/mios/manual/ch36-greenboot-health-check-and-recovery.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MIOSD_BIN="${ROOT_DIR}/src/mios-rs/target/debug/miosd"
if [[ ! -x "$MIOSD_BIN" ]]; then
    if command -v miosd >/dev/null 2>&1; then
        MIOSD_BIN="$(command -v miosd)"
    elif [[ -x "${ROOT_DIR}/src/mios-rs/target/release/miosd" ]]; then
        MIOSD_BIN="${ROOT_DIR}/src/mios-rs/target/release/miosd"
    elif [[ -x "/usr/libexec/mios/miosd" ]]; then
        MIOSD_BIN="/usr/libexec/mios/miosd"
    elif command -v cargo >/dev/null 2>&1; then
        echo "[test-bootc-rollback] building miosd for testing..."
        (cd "${ROOT_DIR}/src/mios-rs" && cargo build -q -p miosd) || { echo "[test-bootc-rollback] ERROR: cargo build -p miosd failed" >&2; exit 1; }
    fi
fi
if [[ ! -x "$MIOSD_BIN" ]]; then
    echo "[test-bootc-rollback] ERROR: miosd binary not found at $MIOSD_BIN" >&2
    exit 1
fi

# miosd's /var/lib/mios probes land in a throwaway root, never the host's /var.
STATE_ROOT="$(mktemp -d /tmp/test-bootc-rollback.XXXXXX)"
export MIOS_ROOT="$STATE_ROOT"

pass_count=0
fail_count=0

# miosd keeps its state under systemd's STATE_DIRECTORY; point it at a scratch dir so the suite needs no root.
STATE_DIRECTORY="$(mktemp -d)"
export STATE_DIRECTORY
trap 'rm -rf "$STATE_ROOT" "$STATE_DIRECTORY"' EXIT

assert_eq() {
    local label="$1"
    local expected="$2"
    local actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  [PASS] $label"
        pass_count=$((pass_count + 1))
    else
        echo "  [FAIL] $label: expected '$expected', got '$actual'" >&2
        fail_count=$((fail_count + 1))
    fi
}

assert_contains() {
    local label="$1"
    local needle="$2"
    local haystack="$3"
    if grep -qF -e "$needle" <<< "$haystack"; then
        echo "  [PASS] $label"
        pass_count=$((pass_count + 1))
    else
        echo "  [FAIL] $label: output missing '$needle'" >&2
        echo "  Actual output: $haystack" >&2
        fail_count=$((fail_count + 1))
    fi
}

echo "=== Running MiOS Bootc Rollback & Recovery Test Suite (task_3356) ==="

# Test 1: Positive Control -- bootc-rollback --help
echo "Test 1: bootc-rollback --help"
help_out="$("$MIOSD_BIN" bootc-rollback --help 2>&1 || true)"
assert_contains "help contains subcommand summary" "Inspect, dry-run, or execute atomic bootc rollback" "$help_out"
assert_contains "help includes --check flag" "--check" "$help_out"
assert_contains "help includes --dry-run flag" "--dry-run" "$help_out"

# Test 2: Positive Control -- bootc-rollback --dry-run
echo "Test 2: bootc-rollback --dry-run"
dry_out="$("$MIOSD_BIN" bootc-rollback --dry-run 2>&1 || true)"
assert_contains "dry-run verifies Invariant 1 /var persistence" "/var persistence verified (Invariant 1: persistent /var)" "$dry_out"
assert_contains "dry-run executes simulation" "Simulating atomic bootc rollback" "$dry_out"
assert_contains "dry-run reports 0 state alterations" "Dry-run completed successfully with 0 state alterations" "$dry_out"

# Test 3: Positive Control -- simulated rollback target check
echo "Test 3: bootc-rollback --check with simulated deployment"
check_out="$(MIOS_TEST_ROLLBACK_AVAILABLE=1 "$MIOSD_BIN" bootc-rollback --check 2>&1 || true)"
assert_contains "check detects armed rollback target" "Rollback deployment verified and armed for recovery" "$check_out"

# Test 4: Positive Control -- greenboot health check
echo "Test 4: greenboot health check validation"
greenboot_out="$("$MIOSD_BIN" greenboot 2>&1 || true)"
assert_contains "greenboot verifies Invariant 1" "/var persistence & writability verified (Invariant 1)" "$greenboot_out"
assert_contains "greenboot verifies SSOT" "SSOT mios.toml accessibility verified" "$greenboot_out"
assert_contains "greenboot outputs success" "SUCCESS: Core OS and SSOT health verified" "$greenboot_out"

# Test 5: Negative Control -- planted failure when rollback target is missing
echo "Test 5: Negative Control: planted failure for unready rollback deployment"
neg_rc=0
neg_out="$(MIOS_TEST_FAIL_NO_ROLLBACK=1 "$MIOSD_BIN" bootc-rollback --check 2>&1)" || neg_rc=$?
if [[ "$neg_rc" -ne 0 ]]; then
    echo "  [PASS] Negative control correctly rejected unready rollback target (exit code: $neg_rc)"
    pass_count=$((pass_count + 1))
else
    echo "  [FAIL] Negative control failed: expected non-zero exit code, got 0" >&2
    fail_count=$((fail_count + 1))
fi
assert_contains "negative control error mentions missing deployment" "No rollback deployment available" "$neg_out"

# Test 6: Negative Control -- an unwritable state directory fails greenboot, naming the path
echo "Test 6: Negative Control: unwritable state directory"
bad_rc=0
bad_out="$(STATE_DIRECTORY=/dev/null/mios "$MIOSD_BIN" greenboot 2>&1)" || bad_rc=$?
assert_eq "greenboot rejects an unwritable state dir" "nonzero" "$([[ $bad_rc -ne 0 ]] && echo nonzero || echo zero)"
assert_contains "the failure names the state dir" "/dev/null/mios is not writable" "$bad_out"

echo "=== Test Summary: $pass_count passed, $fail_count failed ==="
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
exit 0
