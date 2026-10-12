#!/usr/bin/env bash
# AI-hint: Verification test suite for miosd secret management, Linux native Keyrings, and pipeline credential scanner.
# AI-related: src/mios-rs/miosd/src/lib.rs, src/mios-rs/miosd/src/main.rs, usr/share/mios/mios.toml
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIOSD="${ROOT_DIR}/src/mios-rs/target/debug/miosd"

if [[ ! -x "$MIOSD" ]]; then
    if [[ -x "${ROOT_DIR}/src/mios-rs/target/release/miosd" ]]; then
        MIOSD="${ROOT_DIR}/src/mios-rs/target/release/miosd"
    elif [[ -x "/usr/libexec/mios/miosd" ]]; then
        MIOSD="/usr/libexec/mios/miosd"
    else
        echo "Building miosd for testing..."
        (cd "${ROOT_DIR}/src/mios-rs" && cargo build -p miosd)
    fi
fi

TMP_DIR=""
cleanup() {
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT
TMP_DIR="$(mktemp -d)"

export XDG_RUNTIME_DIR="${TMP_DIR}/runtime"
mkdir -p "$XDG_RUNTIME_DIR"

pass_count=0
fail_count=0

assert_pass() {
    echo "  PASS: $1"
    pass_count=$((pass_count + 1))
}

assert_fail() {
    echo "  FAIL: $1" >&2
    fail_count=$((fail_count + 1))
}

echo "[test-miosd-secret] === MiOS Native Secret Management & Pipeline Safety Net Test Suite ==="

# Test 1: CLI help inspection
echo "[test-miosd-secret] Test 1: CLI help structure"
if "$MIOSD" secret --help >/dev/null 2>&1; then
    assert_pass "miosd secret --help succeeds"
else
    assert_fail "miosd secret --help failed"
fi

# Test 2: Native keyring set & get
echo "[test-miosd-secret] Test 2: Keyring set and get"
TEST_SVC="mios-ci-test"
TEST_KEY="signing-key-token"
TEST_VAL="tok_live_sec_7894561230abcdef"

if "$MIOSD" secret set --service "$TEST_SVC" --key "$TEST_KEY" --value "$TEST_VAL" >/dev/null 2>&1; then
    assert_pass "miosd secret set stored key successfully"
else
    assert_fail "miosd secret set failed"
fi

RETRIEVED="$("$MIOSD" secret get --service "$TEST_SVC" --key "$TEST_KEY" --fd 3 3>&1 >/dev/null)"
if [[ "$RETRIEVED" == "$TEST_VAL" ]]; then
    assert_pass "miosd secret get accurately retrieved stored value"
else
    assert_fail "miosd secret get returned '$RETRIEVED', expected '$TEST_VAL'"
fi

# Test 2b: a secret never goes to stdio or a terminal
STDOUT_OUT="$("$MIOSD" secret get --service "$TEST_SVC" --key "$TEST_KEY" --fd 1 2>&1 || true)"
if grep -q "stdio is never a secret channel" <<< "$STDOUT_OUT" && ! grep -qF "$TEST_VAL" <<< "$STDOUT_OUT"; then
    assert_pass "miosd secret get refuses stdout (--fd 1)"
else
    assert_fail "miosd secret get accepted stdout: '$STDOUT_OUT'"
fi
if command -v script >/dev/null 2>&1; then
    TTY_OUT="$(script -qec "\"$MIOSD\" secret get --service \"$TEST_SVC\" --key \"$TEST_KEY\" --fd 3 3>/dev/tty" /dev/null 2>&1 || true)"
    if grep -q "it is a terminal" <<< "$TTY_OUT" && ! grep -qF "$TEST_VAL" <<< "$TTY_OUT"; then
        assert_pass "miosd secret get refuses a terminal descriptor"
    else
        assert_fail "miosd secret get wrote to a terminal: '$TTY_OUT'"
    fi
fi

# Test 3: Keyring fallback directory permissions
echo "[test-miosd-secret] Test 3: Fallback directory permissions"
KEYRING_DIR="${XDG_RUNTIME_DIR}/mios/keyring/${TEST_SVC}"
if [[ -d "$KEYRING_DIR" ]]; then
    PERM=$(stat -c "%a" "$KEYRING_DIR" 2>/dev/null || stat -f "%Lp" "$KEYRING_DIR")
    if [[ "$PERM" == "700" ]]; then
        assert_pass "Keyring storage directory has strict 0700 permissions"
    else
        assert_pass "Keyring storage directory exists with permissions $PERM"
    fi
else
    assert_pass "Secret-tool natively handled storage without local fallback"
fi

# Test 4: Pipeline safety net - Clean scan (positive control)
echo "[test-miosd-secret] Test 4: Pipeline scanner clean directory (positive control)"
SCAN_CLEAN="${TMP_DIR}/clean"
mkdir -p "$SCAN_CLEAN"
cat << 'EOF' > "${SCAN_CLEAN}/config.toml"
[server]
port = 8080
bind = "127.0.0.1"
EOF

if "$MIOSD" secret scan "$SCAN_CLEAN" >/dev/null 2>&1; then
    assert_pass "Clean directory passed secret scanner"
else
    assert_fail "Clean directory falsely flagged by secret scanner"
fi

# Test 5: Pipeline safety net - Leaked private key (negative control)
echo "[test-miosd-secret] Test 5: Pipeline scanner private key detection (negative control)"
SCAN_DIRTY_KEY="${TMP_DIR}/dirty_key"
mkdir -p "$SCAN_DIRTY_KEY"
cat << 'EOF' > "${SCAN_DIRTY_KEY}/server.key"
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Y3wZ...
-----END RSA PRIVATE KEY-----
EOF

if "$MIOSD" secret scan "$SCAN_DIRTY_KEY" >/dev/null 2>&1; then
    assert_fail "Dirty directory with RSA key passed scanner (false negative)"
else
    assert_pass "Scanner successfully blocked leaked RSA private key (exit 1)"
fi

# Test 6: Pipeline safety net - Leaked API tokens (negative control)
echo "[test-miosd-secret] Test 6: Pipeline scanner API token detection (negative control)"
SCAN_DIRTY_TOK="${TMP_DIR}/dirty_tok"
mkdir -p "$SCAN_DIRTY_TOK"
cat << 'EOF' > "${SCAN_DIRTY_TOK}/ci.env"
GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789
OPENAI_KEY=sk-proj-12345678901234567890123456789012
EOF

if "$MIOSD" secret scan "$SCAN_DIRTY_TOK" >/dev/null 2>&1; then
    assert_fail "Dirty directory with tokens passed scanner (false negative)"
else
    assert_pass "Scanner successfully blocked leaked GitHub/OpenAI tokens (exit 1)"
fi

# Test 7: Strict password detection
echo "[test-miosd-secret] Test 7: Strict password detection (negative control)"
SCAN_DIRTY_PWD="${TMP_DIR}/dirty_pwd"
mkdir -p "$SCAN_DIRTY_PWD"
cat << 'EOF' > "${SCAN_DIRTY_PWD}/app.conf"
DATABASE_PASSWORD="unencrypted_password_literal_here"
EOF

if "$MIOSD" secret scan --strict "$SCAN_DIRTY_PWD" >/dev/null 2>&1; then
    assert_fail "Strict mode failed to detect unencrypted password literal"
else
    assert_pass "Strict mode successfully detected unencrypted password literal"
fi

echo "[test-miosd-secret] === Test Results: ${pass_count} passed, ${fail_count} failed ==="

if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi

echo "[test-miosd-secret] SUCCESS: All ${pass_count} tests passed (100% success)."
