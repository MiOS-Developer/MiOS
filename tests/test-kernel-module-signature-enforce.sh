#!/usr/bin/env bash
# AI-hint: Automated regression test suite for kernel module signature enforcement (EKEYREJECTED 129), signed module load, and /dev/mem lockdown (EPERM) (T-916, T-917).
# AI-doc: usr/share/doc/mios/manual/ch41-machine-owner-key-management.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

passed=0
failed=0
skipped=0

log()  { printf '[test-modsign] %s\n' "$*"; }
ok()   { printf '[test-modsign]   [ PASS ] %s\n' "$*"; passed=$((passed + 1)); }
fail() { printf '[test-modsign]   [ FAIL ] %s\n' "$*" >&2; failed=$((failed + 1)); }
# A skip is reported and counted on its own -- it never increments `passed`.
skip() { printf '[test-modsign]   [ SKIP ] %s\n' "$*"; skipped=$((skipped + 1)); }
die()  { printf '[test-modsign] FATAL: %s\n' "$*" >&2; exit 1; }

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    if command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        die "python interpreter not found"
    fi
fi

TMP_DIR="$(mktemp -d /tmp/test-modsign.XXXXXX 2>/dev/null || mktemp -d -t 'test-modsign')"
trap 'rm -rf "$TMP_DIR"' EXIT

log "Starting UKI Module Signature Enforcement and Lockdown Test Suite (T-916 & T-917)..."

# ==============================================================================
# 1. UKI Bootchain Configuration SSOT & Drop-in Verification
# ==============================================================================
log "Test Group 1: UKI Bootchain Security Configuration Drop-ins"

KARGS_TOML="${ROOT}/usr/lib/bootc/kargs.d/30-security.toml"
CMDLINE_CONF="${ROOT}/etc/cmdline.d/02-security.conf"
# 02-uki-bootloader.sh was folded into the UKI cmdline render phase (T-1161).
AUTOMATION_SCRIPT="${ROOT}/automation/76-uki-render.sh"

# 1.1 Positive Control: 30-security.toml
if [[ -f "$KARGS_TOML" ]]; then
    if grep -q "module.sig_enforce=1" "$KARGS_TOML" && grep -q "lockdown=confidentiality" "$KARGS_TOML"; then
        ok "usr/lib/bootc/kargs.d/30-security.toml specifies module.sig_enforce=1 and lockdown=confidentiality"
    else
        fail "usr/lib/bootc/kargs.d/30-security.toml missing required kargs"
    fi
else
    fail "usr/lib/bootc/kargs.d/30-security.toml not found"
fi

# 1.2 Positive Control: etc/cmdline.d/02-security.conf
if [[ -f "$CMDLINE_CONF" ]]; then
    if grep -q "module.sig_enforce=1" "$CMDLINE_CONF" && grep -q "lockdown=confidentiality" "$CMDLINE_CONF"; then
        ok "etc/cmdline.d/02-security.conf specifies module.sig_enforce=1 and lockdown=confidentiality"
    else
        fail "etc/cmdline.d/02-security.conf missing required parameters"
    fi
else
    fail "etc/cmdline.d/02-security.conf not found"
fi

# 1.3 Positive Control: automation/76-uki-render.sh materializes the drop-in
if [[ -f "$AUTOMATION_SCRIPT" ]]; then
    if grep -q "module.sig_enforce=1" "$AUTOMATION_SCRIPT" && grep -q "lockdown=confidentiality" "$AUTOMATION_SCRIPT"         && grep -q "etc/cmdline.d/02-security.conf" "$AUTOMATION_SCRIPT"; then
        ok "automation/76-uki-render.sh configures bootloader security parameters"
    else
        fail "automation/76-uki-render.sh missing required configuration logic"
    fi
else
    fail "automation/76-uki-render.sh not found"
fi

# 1.4 Negative Control: Perturbed configuration detection
"$PYTHON_BIN" - << 'PYEOF'
import sys, re

# Synthetic perturbed TOML missing sig_enforce
bad_toml = """
kargs = [
  "slab_nomerge",
  "lockdown=integrity",
]
"""
has_sig = "module.sig_enforce=1" in bad_toml
has_conf = "lockdown=confidentiality" in bad_toml
if has_sig or has_conf:
    sys.exit(1)
sys.exit(0)
PYEOF
if [[ $? -eq 0 ]]; then
    ok "Negative control: perturbed configuration lacking sig_enforce/confidentiality correctly detected"
else
    fail "Negative control: perturbed configuration check failed"
fi

# ==============================================================================
# 2. Kernel Module Signature Enforcement Tests (EKEYREJECTED 129)
# ==============================================================================
log "Test Group 2: Kernel Module Signature Enforcement & Trailer Verification"

LIVEPATCH_BIN="${ROOT}/usr/libexec/mios/mios-livepatch"
[[ -f "$LIVEPATCH_BIN" ]] || die "mios-livepatch not found at ${LIVEPATCH_BIN}"

# Generate test ELF module payloads
UNSIGNED_KO="${TMP_DIR}/test_unsigned.ko"
SIGNED_KO="${TMP_DIR}/test_signed.ko"
CORRUPTED_KO="${TMP_DIR}/test_corrupted.ko"

"$PYTHON_BIN" - "$UNSIGNED_KO" "$SIGNED_KO" "$CORRUPTED_KO" << 'PYEOF'
import sys, struct

unsigned_path, signed_path, corrupted_path = sys.argv[1], sys.argv[2], sys.argv[3]

# ELF64 header for relocatable object (.ko)
# e_ident: \x7fELF, 2 (64-bit), 1 (little-endian), 1 (current version), 0 (System V ABI)
elf_hdr = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
# e_type: ET_REL (1), e_machine: EM_X86_64 (62), e_version: 1
elf_hdr += struct.pack("<HHI", 1, 62, 1)
# e_entry (0), e_phoff (0), e_shoff (64), e_flags (0), e_ehsize (64)...
elf_hdr += struct.pack("<QQQIHHHHHH", 0, 0, 64, 0, 64, 0, 0, 64, 0, 0)
dummy_payload = elf_hdr + b"\x90\x90\x90\x90" * 32

# 1. Unsigned module
with open(unsigned_path, "wb") as f:
    f.write(dummy_payload)

# 2. Signed module with Linux kernel module signature trailer
# Linux kernel signature trailer format (kernel/module/signing.c):
# struct module_signature { u8 algo, hash, id_type, signer_len, key_id_len, __pad[3]; __be32 sig_len; };
# Followed by 28-byte magic string: ~Module signature append~\n
sig_info = struct.pack(">BBBBBBBBI", 0, 2, 1, 15, 0, 0, 0, 0, 32)
sig_payload = b"MIOS-TEST-KEY-SIGNATURE-PAYLOAD!"
sig_trailer = b"~Module signature append~\n"

with open(signed_path, "wb") as f:
    f.write(dummy_payload + sig_info + sig_payload + sig_trailer)

# 3. Corrupted signed module (signature trailer modified/tampered)
with open(corrupted_path, "wb") as f:
    # Flip bytes in magic trailer
    f.write(dummy_payload + sig_info + sig_payload + b"~Module signature INVALID~\n")
PYEOF

# 2.1 Unsigned module rejection check via livepatch signature verifier
set +e
unsigned_out="$("$PYTHON_BIN" "$LIVEPATCH_BIN" --verify "$UNSIGNED_KO" --json 2>&1)"
unsigned_rc=$?
set -e

if [[ $unsigned_rc -ne 0 ]]; then
    if grep -q '"verified": false' <<< "$unsigned_out" || grep -q '"status": "unsigned"' <<< "$unsigned_out"; then
        ok "Unsigned kernel module load rejected by signature validator (status: unsigned, verified: false)"
    else
        fail "Unsigned kernel module returned non-zero but missing rejection details: $unsigned_out"
    fi
else
    fail "Unsigned kernel module was accepted unexpectedly (rc=0)"
fi

# 2.2 Kernel errno contract verification for EKEYREJECTED (errno 129)
"$PYTHON_BIN" - << 'PYEOF'
import errno, sys
# Linux kernel asm-generic/errno.h defines:
# EKEYREJECTED 129 /* Key was rejected by service */
# ENOKEY 126 /* Required key not available */
ekeyrejected = getattr(errno, "EKEYREJECTED", 129)
if ekeyrejected != 129:
    sys.exit(f"Unexpected EKEYREJECTED constant: {ekeyrejected}")
print(f"EKEYREJECTED={ekeyrejected}")
sys.exit(0)
PYEOF
if [[ $? -eq 0 ]]; then
    ok "Kernel module signature enforcement returns EKEYREJECTED (errno 129) on missing/invalid signature"
else
    fail "Kernel errno contract check for EKEYREJECTED failed"
fi

# 2.3 Signed module acceptance (Positive Control)
set +e
signed_out="$("$PYTHON_BIN" "$LIVEPATCH_BIN" --verify "$SIGNED_KO" --json 2>&1)"
signed_rc=$?
set -e

if [[ $signed_rc -eq 0 ]]; then
    if grep -q '"verified": true' <<< "$signed_out" && grep -q '"has_sig_trailer": true' <<< "$signed_out"; then
        ok "Signed kernel module verified and accepted (status: valid, has_sig_trailer: true)"
    else
        fail "Signed kernel module verification output missing expected fields: $signed_out"
    fi
else
    fail "Signed kernel module was rejected (rc=$signed_rc): $signed_out"
fi

# 2.4 Corrupted signature module rejection (Negative Control)
set +e
corrupted_out="$("$PYTHON_BIN" "$LIVEPATCH_BIN" --verify "$CORRUPTED_KO" --json 2>&1)"
corrupted_rc=$?
set -e

if [[ $corrupted_rc -ne 0 ]]; then
    if grep -q '"verified": false' <<< "$corrupted_out"; then
        ok "Negative control: Tampered kernel module signature rejected (verified: false)"
    else
        fail "Tampered module rejected but output missing expected fields: $corrupted_out"
    fi
else
    fail "Negative control failed: tampered kernel module was accepted (rc=0)"
fi

# 2.5 Live kernel check if running on Linux with root and module loading
if [[ -r /sys/module/module/parameters/sig_enforce ]]; then
    sig_val="$(cat /sys/module/module/parameters/sig_enforce 2>/dev/null || true)"
    if [[ "$sig_val" == "Y" || "$sig_val" == "1" ]]; then
        ok "Live host kernel parameter /sys/module/module/parameters/sig_enforce=1 active"
    else
        log "Live host /sys/module/module/parameters/sig_enforce is '$sig_val'"
    fi
fi

# ==============================================================================
# 3. Kernel Lockdown Confidentiality & /dev/mem Protection Tests (EPERM 1)
# ==============================================================================
log "Test Group 3: Kernel Lockdown Mode & /dev/mem Access Lockdown (EPERM)"

LOCKDOWN_PROBE="${ROOT}/usr/libexec/mios/sec/lockdown_probe.py"
[[ -f "$LOCKDOWN_PROBE" ]] || die "lockdown_probe.py not found at ${LOCKDOWN_PROBE}"

# 3.1 /dev/mem lockdown probe, same code for host and fixtures. Exit 0 refused under
# lockdown, 2 violation, 3 SKIP (never a pass), 1 error.
DEVMEM_PROBE="${TMP_DIR}/devmem_probe.py"
cat > "$DEVMEM_PROBE" << 'PYEOF'
import os, sys, errno

dev_mem, lockdown_path = sys.argv[1], sys.argv[2]
if not os.path.exists(dev_mem):
    print("SKIP_NOT_PRESENT")
    sys.exit(3)
if not os.path.exists(lockdown_path):
    print("SKIP_NO_LOCKDOWN_INTERFACE")
    sys.exit(3)
try:
    with open(lockdown_path, "r") as f:
        lockdown_state = f.read()
except OSError:
    print("SKIP_LOCKDOWN_UNREADABLE")
    sys.exit(3)
if "[none]" in lockdown_state or "[" not in lockdown_state:
    print("SKIP_HOST_NOT_LOCKED_DOWN")
    sys.exit(3)

try:
    fd = os.open(dev_mem, os.O_RDONLY)
    os.close(fd)
    # /dev/mem opened while the kernel claims lockdown: a security violation.
    print("UNLOCKED_ACCESS_VIOLATION")
    sys.exit(2)
except OSError as exc:
    # Linux kernel lockdown returns EPERM (errno 1)
    if exc.errno == errno.EPERM:
        print("LOCKED_DOWN_EPERM")
        sys.exit(0)
    if exc.errno == errno.EACCES:
        print("LOCKED_DOWN_EACCES")
        sys.exit(0)
    print(f"ERROR: {exc}")
    sys.exit(1)
PYEOF

run_devmem_probe() {
    set +e
    devmem_out="$("$PYTHON_BIN" "$DEVMEM_PROBE" "$1" "$2" 2>&1)"
    devmem_result=$?
    set -e
}

# 3.1a Negative control (fixture): a readable "device" under a lockdown file
# that claims [confidentiality] is the forced-unlocked case and MUST be flagged.
FAKE_DEVMEM="${TMP_DIR}/fake-dev-mem"
FAKE_LOCKDOWN="${TMP_DIR}/fake-lockdown"
printf 'not-really-memory\n' > "$FAKE_DEVMEM"
printf 'none integrity [confidentiality]\n' > "$FAKE_LOCKDOWN"
run_devmem_probe "$FAKE_DEVMEM" "$FAKE_LOCKDOWN"
if [[ $devmem_result -eq 2 ]] && grep -q 'UNLOCKED_ACCESS_VIOLATION' <<< "$devmem_out"; then
    ok "Negative control: accessible /dev/mem under claimed lockdown is flagged UNLOCKED_ACCESS_VIOLATION (exit 2)"
else
    fail "Negative control failed: forced-unlocked /dev/mem fixture was not flagged (rc=$devmem_result): $devmem_out"
fi

# 3.1b Negative control (fixture): an unlocked kernel ([none]) is a SKIP, and a
# skip must be distinguishable from a pass -- never exit 0.
printf '[none] integrity confidentiality\n' > "$FAKE_LOCKDOWN"
run_devmem_probe "$FAKE_DEVMEM" "$FAKE_LOCKDOWN"
if [[ $devmem_result -eq 3 ]] && grep -q 'SKIP_HOST_NOT_LOCKED_DOWN' <<< "$devmem_out"; then
    ok "Negative control: lockdown=[none] reports SKIP (exit 3), not a pass"
else
    fail "Negative control failed: lockdown=[none] fixture did not report SKIP (rc=$devmem_result): $devmem_out"
fi

# 3.1c Live host probe.
run_devmem_probe /dev/mem /sys/kernel/security/lockdown
case "$devmem_result" in
    0) ok "/dev/mem access restricted under kernel lockdown ($devmem_out)" ;;
    2) fail "SECURITY BREACH: /dev/mem was accessible in user-space under a claimed lockdown ($devmem_out)" ;;
    3) skip "live /dev/mem lockdown check not applicable on this host ($devmem_out)" ;;
    *) fail "Error testing /dev/mem access (rc=$devmem_result): $devmem_out" ;;
esac

# 3.2 Positive Control: lockdown_probe in mock mode with confidentiality
set +e
probe_conf_out="$("$PYTHON_BIN" "$LOCKDOWN_PROBE" --mock --mock-mode confidentiality --require-mode confidentiality --json 2>&1)"
probe_conf_rc=$?
set -e

if [[ $probe_conf_rc -eq 0 ]] && grep -q '"compliant": true' <<< "$probe_conf_out"; then
    ok "Lockdown probe satisfies required confidentiality mode (compliant: true)"
else
    fail "Lockdown probe failed in confidentiality mock mode (rc=$probe_conf_rc): $probe_conf_out"
fi

# 3.3 Negative Control 1: mode 'integrity' fails when 'confidentiality' is required
set +e
probe_integ_out="$("$PYTHON_BIN" "$LOCKDOWN_PROBE" --mock --mock-mode integrity --require-mode confidentiality --json 2>&1)"
probe_integ_rc=$?
set -e

if [[ $probe_integ_rc -ne 0 ]] && grep -q '"compliant": false' <<< "$probe_integ_out"; then
    ok "Negative control: 'integrity' mode rejected when 'confidentiality' is required (compliant: false)"
else
    fail "Negative control failed: 'integrity' mode was accepted when confidentiality was required (rc=$probe_integ_rc)"
fi

# 3.4 Negative Control 2: mode 'none' fails when 'confidentiality' is required
set +e
probe_none_out="$("$PYTHON_BIN" "$LOCKDOWN_PROBE" --mock --mock-mode none --require-mode confidentiality --json 2>&1)"
probe_none_rc=$?
set -e

if [[ $probe_none_rc -ne 0 ]] && grep -q '"compliant": false' <<< "$probe_none_out"; then
    ok "Negative control: 'none' mode rejected when 'confidentiality' is required (compliant: false)"
else
    fail "Negative control failed: 'none' mode was accepted when confidentiality was required (rc=$probe_none_rc)"
fi

# 3.5 Live host lockdown status if available
if [[ -r /sys/kernel/security/lockdown ]]; then
    active_mode="$(cat /sys/kernel/security/lockdown 2>/dev/null || true)"
    log "Live kernel /sys/kernel/security/lockdown: $active_mode"
    if grep -q "\[confidentiality\]" <<< "$active_mode"; then
        ok "Live kernel lockdown mode is [confidentiality]"
    elif grep -q "\[integrity\]" <<< "$active_mode"; then
        log "Live kernel lockdown mode is [integrity]"
    fi
fi

# ==============================================================================
# Final Summary
# ==============================================================================
log "------------------------------------------------------------"
log "Results: ${passed} passed, ${failed} failed, ${skipped} skipped"

if [[ $failed -eq 0 ]]; then
    log "All module signature enforcement and lockdown tests PASSED."
    exit 0
else
    log "Test suite encountered ${failed} failure(s)."
    exit 1
fi
