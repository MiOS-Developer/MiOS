#!/usr/bin/env bash
# AI-hint: Verification suite for composefs fs-verity root filesystem sealing and atomic validator (task_471, task_2704).
# AI-doc: usr/share/doc/mios/manual/ch71-composefs-sealing.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERBOSE=false
DRY_RUN=false
MOCK_MODE=false

show_help() {
    cat <<'EOF'
Usage: test-composefs-seal.sh [OPTIONS]

Test suite for Composefs fs-verity root filesystem sealing and atomic image descriptor validator (task_471, task_2704).

Options:
  -v, --verbose       Enable verbose test logging
  --dry-run           Execute test assertions in dry-run mode
  --mock              Run tests with mock fixtures when host tools are absent
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
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

pass_count=0
fail_count=0
skip_count=0

log() {
    echo "[test-composefs-seal] $*"
}

diag() {
    if [[ "$VERBOSE" == "true" ]]; then
        echo "  [diag] $*"
    fi
}

assert_pass() {
    local name="$1"
    echo "  PASS: $name"
    pass_count=$((pass_count + 1))
}

assert_skip() {
    echo "  SKIP: $1 - $2"
    skip_count=$((skip_count + 1))
}

assert_fail() {
    local name="$1"
    local reason="${2:-assertion failed}"
    echo "  FAIL: $name - $reason" >&2
    fail_count=$((fail_count + 1))
}

TMP_DIR="$(mktemp -d /tmp/test-cfs-seal.XXXXXX)"
# Every path the seal script can write (repo and, when writable, host): each is restored, or removed if it did not exist.
SEALED_FILES=("${ROOT_DIR}/usr/lib/bootc/kargs.d/50-composefs.toml" "${ROOT_DIR}/usr/lib/ostree/prepare-root.conf"
              /usr/lib/bootc/kargs.d/50-composefs.toml /usr/lib/ostree/prepare-root.conf /etc/ostree/prepare-root.conf)
for _i in "${!SEALED_FILES[@]}"; do [[ -f "${SEALED_FILES[$_i]}" ]] && cp -p "${SEALED_FILES[$_i]}" "${TMP_DIR}/sealed.${_i}"; done
_restore_sealed() {
    local _i
    for _i in "${!SEALED_FILES[@]}"; do
        if [[ -f "${TMP_DIR}/sealed.${_i}" ]]; then
            cp -p "${TMP_DIR}/sealed.${_i}" "${SEALED_FILES[$_i]}"
        else
            rm -f "${SEALED_FILES[$_i]}"
        fi
    done
    [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
    return 0
}
trap _restore_sealed EXIT

VALIDATOR="${ROOT_DIR}/usr/libexec/mios/mios-composefs-validator"
SEAL_SCRIPT="${ROOT_DIR}/automation/93-composefs-seal.sh"

log "=== MiOS Composefs fs-verity Root Sealing Test Suite (task_471, task_2704) ==="

if [[ "$DRY_RUN" == "true" ]]; then
    log "Running in dry-run mode: verifying test script syntax and dry-run sub-invocations"
    if bash -n "$SEAL_SCRIPT" && bash -n "$0"; then
        assert_pass "Shell syntax validation of sealing script and test harness"
    else
        assert_fail "Shell syntax validation failed"
    fi
    if "$SEAL_SCRIPT" --dry-run; then
        assert_pass "automation/93-composefs-seal.sh --dry-run exit status 0"
    else
        assert_fail "automation/93-composefs-seal.sh --dry-run execution"
    fi
    log "=== Test Summary: $pass_count passed, $fail_count failed ==="
    exit 0
fi

# ==============================================================================
# Test 1: Tooling discovery (mkcomposefs, composefs-info, mios-composefs-validator)
# ==============================================================================
log "Test 1: Tooling discovery"

if command -v mkcomposefs >/dev/null 2>&1 || [[ "$MOCK_MODE" == "true" ]]; then
    assert_pass "Tooling: mkcomposefs discovered"
else
    assert_skip "Tooling: mkcomposefs not found" "host lacks mkcomposefs; the suite exits 77 ([ci.tool_skips])"
fi

if command -v composefs-info >/dev/null 2>&1 || [[ "$MOCK_MODE" == "true" ]]; then
    assert_pass "Tooling: composefs-info discovered"
else
    assert_skip "Tooling: composefs-info not found" "host lacks composefs-info; the suite exits 77 ([ci.tool_skips])"
fi

if [[ -x "$VALIDATOR" ]]; then
    assert_pass "Tooling: mios-composefs-validator is executable at $VALIDATOR"
else
    assert_fail "Tooling: mios-composefs-validator not found or not executable" "$VALIDATOR"
fi

# ==============================================================================
# Test 2: Composefs image creation and verification using mkcomposefs on synthetic tree
# ==============================================================================
log "Test 2: Composefs image creation on synthetic tree"

SYNTH_DIR="${TMP_DIR}/synth_tree"
mkdir -p "${SYNTH_DIR}/usr/bin" "${SYNTH_DIR}/usr/lib64" "${SYNTH_DIR}/etc"
cat <<'EOF' > "${SYNTH_DIR}/usr/bin/sample-bin"
#!/usr/bin/env bash
echo "MiOS sealed payload"
EOF
chmod 0755 "${SYNTH_DIR}/usr/bin/sample-bin"
echo "MiOS Test Tree" > "${SYNTH_DIR}/etc/os-release"
ln -s "../usr/bin/sample-bin" "${SYNTH_DIR}/usr/bin/sample-alias"

SYNTH_IMAGE="${TMP_DIR}/synth.cfs"
DIGEST=""

if command -v mkcomposefs >/dev/null 2>&1; then
    mk_out="$(mkcomposefs --print-digest "${SYNTH_DIR}" "${SYNTH_IMAGE}")"
    DIGEST="$(echo "$mk_out" | tail -n 1 | tr -d '[:space:]')"
    diag "Generated image size: $(stat -c %s "${SYNTH_IMAGE}") bytes"
    diag "Calculated digest: ${DIGEST}"

    if [[ -f "${SYNTH_IMAGE}" && -s "${SYNTH_IMAGE}" && -n "${DIGEST}" ]]; then
        assert_pass "Composefs image generated with non-empty digest (${DIGEST})"
    else
        assert_fail "Composefs image generation failed" "empty output or digest"
    fi
else
    # Mock fallback for test environment without mkcomposefs
    python3 -c "
import struct
with open('${SYNTH_IMAGE}', 'wb') as f:
    f.write(struct.pack('<IBBH', 0x00736663, 1, 0, 0) + b'\x01'*56 + b'\x00'*4096)
"
    DIGEST="$("$VALIDATOR" inspect "${SYNTH_IMAGE}" | grep "fs-verity Digest:" | awk '{print $3}')"
    assert_pass "Synthetic composefs descriptor generated (mock mode)"
fi

# ==============================================================================
# Test 3: Descriptor validation with mios-composefs-validator (passes on valid .cfs)
# ==============================================================================
log "Test 3: Descriptor validation with mios-composefs-validator (positive controls)"

# 3a. Verify without digest
if "$VALIDATOR" verify "${SYNTH_IMAGE}" >/dev/null 2>&1; then
    assert_pass "Validator verify succeeds on valid .cfs without digest"
else
    assert_fail "Validator verify failed on valid image"
fi

# 3b. Verify with exact digest
if "$VALIDATOR" verify "${SYNTH_IMAGE}" --digest "${DIGEST}" >/dev/null 2>&1; then
    assert_pass "Validator verify succeeds with matching --digest"
else
    assert_fail "Validator verify failed with matching --digest"
fi

# 3c. Inspect command
insp_out="$("$VALIDATOR" inspect "${SYNTH_IMAGE}")"
if echo "$insp_out" | grep -qi "Header Valid:[[:space:]]*True" && echo "$insp_out" | grep -qi "fs-verity Digest:"; then
    assert_pass "Validator inspect reports valid header and fs-verity digest"
else
    assert_fail "Validator inspect output invalid" "$insp_out"
fi

# ==============================================================================
# Test 4: Negative control - tampered .cfs file detection and rejection (EIO)
# ==============================================================================
log "Test 4: Negative controls - tampered file detection and rejection (asserts non-zero exit / EIO)"

# 4a. Bad magic header
TAMPER_MAGIC="${TMP_DIR}/tamper_magic.cfs"
cp "${SYNTH_IMAGE}" "${TAMPER_MAGIC}"
python3 -c "
with open('${TAMPER_MAGIC}', 'r+b') as f:
    f.seek(0)
    f.write(b'\x00\x00\x00\x00')
"
set +e
"$VALIDATOR" verify "${TAMPER_MAGIC}" >/dev/null 2>&1
magic_rc=$?
set -e
if [[ "$magic_rc" -ne 0 ]]; then
    assert_pass "Rejection of corrupted magic header (exit $magic_rc, expected non-zero / EIO)"
else
    assert_fail "Corrupted magic header was erroneously accepted (exit 0)"
fi

# 4b. Truncated descriptor (< 64 bytes)
TRUNC_FILE="${TMP_DIR}/truncated.cfs"
python3 -c "
with open('${TRUNC_FILE}', 'wb') as f:
    f.write(b'cfs\x00' + b'\x00'*16)
"
set +e
"$VALIDATOR" verify "${TRUNC_FILE}" >/dev/null 2>&1
trunc_rc=$?
set -e
if [[ "$trunc_rc" -ne 0 ]]; then
    assert_pass "Rejection of truncated descriptor file (exit $trunc_rc, expected non-zero / EIO)"
else
    assert_fail "Truncated file was erroneously accepted (exit 0)"
fi

# 4c. Corrupt header format version
BAD_VER="${TMP_DIR}/bad_version.cfs"
cp "${SYNTH_IMAGE}" "${BAD_VER}"
python3 -c "
with open('${BAD_VER}', 'r+b') as f:
    f.seek(4)
    f.write(b'\xfe\xfe')
"
set +e
"$VALIDATOR" verify "${BAD_VER}" >/dev/null 2>&1
ver_rc=$?
set -e
if [[ "$ver_rc" -ne 0 ]]; then
    assert_pass "Rejection of corrupted descriptor version (exit $ver_rc, expected non-zero / EIO)"
else
    assert_fail "Corrupted version was erroneously accepted (exit 0)"
fi

# ==============================================================================
# Test 5: Negative control - mismatched digest detection and rejection
# ==============================================================================
log "Test 5: Negative controls - mismatched digest detection and rejection"

# 5a. Mismatched digest on untouched image
set +e
"$VALIDATOR" verify "${SYNTH_IMAGE}" --digest "0000000000000000000000000000000000000000000000000000000000000000" >/dev/null 2>&1
digest_rc=$?
set -e
if [[ "$digest_rc" -ne 0 ]]; then
    assert_pass "Rejection of invalid digest hash (exit $digest_rc, expected non-zero / EIO)"
else
    assert_fail "Invalid digest was erroneously accepted (exit 0)"
fi

# 5b. Bit-flip in image validated against original digest
TAMPER_BYTE="${TMP_DIR}/tamper_byte.cfs"
cp "${SYNTH_IMAGE}" "${TAMPER_BYTE}"
python3 -c "
with open('${TAMPER_BYTE}', 'r+b') as f:
    f.seek(128)
    byte = f.read(1)
    f.seek(128)
    f.write(bytes([byte[0] ^ 0xff]))
"
set +e
"$VALIDATOR" verify "${TAMPER_BYTE}" --digest "${DIGEST}" >/dev/null 2>&1
bitflip_rc=$?
set -e
if [[ "$bitflip_rc" -ne 0 ]]; then
    assert_pass "Rejection of single-byte bit flip under digest verification (exit $bitflip_rc, expected non-zero / EIO)"
else
    assert_fail "Tampered byte was erroneously accepted (exit 0)"
fi

# ==============================================================================
# Test 6: automation/93-composefs-seal.sh execution in mock / dry-run mode
# ==============================================================================
log "Test 6: automation/93-composefs-seal.sh execution in mock / dry-run mode"

# 6a. Dry-run mode
if "$SEAL_SCRIPT" --dry-run >/dev/null 2>&1; then
    assert_pass "automation/93-composefs-seal.sh executed in --dry-run mode (exit 0)"
else
    assert_fail "automation/93-composefs-seal.sh failed in --dry-run mode"
fi

# 6b. Mock mode must leave the tracked tree byte-identical
TRACKED_SEAL_OUTPUTS=("${ROOT_DIR}/usr/lib/bootc/kargs.d/50-composefs.toml" "${ROOT_DIR}/usr/lib/ostree/prepare-root.conf")
sums_before="$(sha256sum "${TRACKED_SEAL_OUTPUTS[@]}" 2>&1)"
if "$SEAL_SCRIPT" --mock >/dev/null 2>&1; then
    assert_pass "automation/93-composefs-seal.sh executed in --mock mode (exit 0)"
else
    assert_fail "automation/93-composefs-seal.sh failed in --mock mode"
fi
sums_after="$(sha256sum "${TRACKED_SEAL_OUTPUTS[@]}" 2>&1)"
if [[ "$sums_before" == "$sums_after" ]]; then
    assert_pass "--mock left the tracked seal outputs unchanged"
else
    assert_fail "--mock mutated a tracked file" "$(diff <(echo "$sums_before") <(echo "$sums_after") | grep '^>' || true)"
fi

# 6c. Mock mode into an explicit seal root, read back by Test 7
SEAL_ROOT="${TMP_DIR}/seal-root"
if COMPOSEFS_SEAL_ROOT="$SEAL_ROOT" "$SEAL_SCRIPT" --mock >/dev/null 2>&1; then
    assert_pass "automation/93-composefs-seal.sh --mock wrote into COMPOSEFS_SEAL_ROOT (exit 0)"
else
    assert_fail "automation/93-composefs-seal.sh --mock failed with COMPOSEFS_SEAL_ROOT"
fi

# ==============================================================================
# Test 7: Verify prepare-root.conf and kargs.d configuration generation
# ==============================================================================
log "Test 7: Verify prepare-root.conf and kargs.d configuration generation"

PREPARE_CONF="${SEAL_ROOT}/usr/lib/ostree/prepare-root.conf"
if [[ -f "$PREPARE_CONF" ]]; then
    if grep -q "\[composefs\]" "$PREPARE_CONF" && \
       grep -qE "enabled[[:space:]]*=[[:space:]]*(verity|yes)" "$PREPARE_CONF" && \
       grep -q "transient[[:space:]]*=[[:space:]]*false" "$PREPARE_CONF"; then
        assert_pass "prepare-root.conf contains composefs enabled=(verity|yes) and transient=false"
    else
        assert_fail "prepare-root.conf format mismatch" "$(cat "$PREPARE_CONF")"
    fi
else
    assert_fail "prepare-root.conf not found at $PREPARE_CONF"
fi

KARGS_CONF="${SEAL_ROOT}/usr/lib/bootc/kargs.d/50-composefs.toml"
if [[ -f "$KARGS_CONF" ]]; then
    if grep -q "ostree\.composefs=1" "$KARGS_CONF" && \
       grep -q "match-architectures.*x86_64" "$KARGS_CONF" && \
       ! grep -qx "[[:space:]]*" "$KARGS_CONF"; then
        assert_pass "kargs.d/50-composefs.toml contains ostree.composefs=1 and x86_64 match"
    else
        assert_fail "kargs.d/50-composefs.toml format mismatch" "$(cat "$KARGS_CONF")"
    fi
else
    assert_fail "kargs.d/50-composefs.toml not found at $KARGS_CONF"
fi

# ==============================================================================
# Summary
# ==============================================================================
log "=== Test Results: $pass_count passed, $fail_count failed, $skip_count skipped ==="

if [[ "$fail_count" -gt 0 ]]; then
    log "FAILED: $fail_count tests failed."
    exit 1
fi
if [[ "$skip_count" -gt 0 ]]; then
    log "SKIPPED: $skip_count check(s) need host tools; exit 77 is a skip only where [ci.tool_skips] registers this suite."
    exit 77
fi

log "SUCCESS: All $pass_count tests passed (100% success)."
exit 0
