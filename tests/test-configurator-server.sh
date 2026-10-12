#!/usr/bin/env bash
# AI-hint: Automated CI verification test suite for miosd embedded Rust SSOT configurator engine (mios.html & /portal/config).
# AI-related: src/mios-rs/miosd/src/lib.rs, usr/share/mios/configurator/mios.html, usr/share/mios/mios.toml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MIOSD="${REPO_ROOT}/src/mios-rs/target/debug/miosd"
if [[ ! -x "$MIOSD" ]]; then
    if [[ -x "${REPO_ROOT}/src/mios-rs/target/release/miosd" ]]; then
        MIOSD="${REPO_ROOT}/src/mios-rs/target/release/miosd"
    elif [[ -x "/usr/libexec/mios/miosd" ]]; then
        MIOSD="/usr/libexec/mios/miosd"
    else
        echo "Building miosd for testing..."
        (cd "${REPO_ROOT}/src/mios-rs" && cargo build -p miosd)
    fi
fi

TMP_DIR=""
SERVER_PID=""

cleanup() {
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT
TMP_DIR="$(mktemp -d)"

TEST_PORT=8788
TEST_PROFILE="${TMP_DIR}/test_profile.toml"
HTML_PATH="${REPO_ROOT}/usr/share/mios/configurator/mios.html"

pass=0
fail=0

_pass() {
    echo "  [PASS] $1"
    pass=$((pass + 1))
}

_fail() {
    echo "  [FAIL] $1" >&2
    fail=$((fail + 1))
}

echo "[test-configurator-server] === MiOS Embedded Rust SSOT Engine & Configurator Server Test Suite ==="

# Test 1: Configurator HTML asset existence
echo "[test-configurator-server] Test 1: Configurator HTML asset validation"
if [[ -f "$HTML_PATH" ]]; then
    _pass "Configurator HTML asset found at $HTML_PATH"
    if grep -q "<!DOCTYPE html>" "$HTML_PATH" && grep -q "mios" "$HTML_PATH"; then
        _pass "Configurator HTML contains valid HTML5 doctype and MiOS bindings"
    else
        _fail "Configurator HTML structure invalid"
    fi
else
    _fail "Configurator HTML asset missing: $HTML_PATH"
fi

# Test 2: Spawn miosd config-server
echo "[test-configurator-server] Test 2: Starting embedded Rust config-server on port ${TEST_PORT}"
MIOS_ROOT="$REPO_ROOT" \
MIOS_CONFIGURATOR_HTML="$HTML_PATH" \
MIOS_USER_TOML="$TEST_PROFILE" \
"$MIOSD" config-server --port "$TEST_PORT" > "${TMP_DIR}/server.log" 2>&1 &
SERVER_PID=$!

READY=0
for _ in {1..20}; do
    if curl -s "http://127.0.0.1:${TEST_PORT}/health" | grep -q "miosd-config-server"; then
        READY=1
        break
    fi
    sleep 0.2
done

if [[ "$READY" -eq 1 ]]; then
    _pass "Embedded Rust config-server started and responding on port ${TEST_PORT}"
else
    cat "${TMP_DIR}/server.log" >&2
    _fail "Embedded Rust config-server failed to bind within 4 seconds"
    exit 1
fi

# Test 3: Health check endpoint
echo "[test-configurator-server] Test 3: Health endpoint verification (GET /health)"
HEALTH_RESP="$(curl -s "http://127.0.0.1:${TEST_PORT}/health")"
if echo "$HEALTH_RESP" | grep -q '"status":"ok"' && echo "$HEALTH_RESP" | grep -q '"engine":"miosd-config-server"'; then
    _pass "GET /health returned JSON with engine status OK"
else
    _fail "GET /health returned unexpected response: $HEALTH_RESP"
fi

# Test 4: HEAD method support
echo "[test-configurator-server] Test 4: HEAD method verification"
HEAD_CODE="$(curl -s -o /dev/null -w "%{http_code}" -I "http://127.0.0.1:${TEST_PORT}/health")"
if [[ "$HEAD_CODE" == "200" ]]; then
    _pass "HEAD /health returned HTTP 200 OK"
else
    _fail "HEAD /health returned HTTP $HEAD_CODE"
fi

# Test 5: Root path redirect
echo "[test-configurator-server] Test 5: Root path redirection (GET / -> /configure)"
REDIRECT_HEADER="$(curl -s -I "http://127.0.0.1:${TEST_PORT}/")"
if echo "$REDIRECT_HEADER" | grep -iq "Location: /configure"; then
    _pass "GET / correctly redirects to /configure (302 Found)"
else
    _fail "GET / did not issue Location: /configure redirect"
fi

# Test 6: Serving mios.html
echo "[test-configurator-server] Test 6: Serving configurator HTML (GET /configure)"
curl -s "http://127.0.0.1:${TEST_PORT}/configure" -o "${TMP_DIR}/test_cfg.html"
if [[ -s "${TMP_DIR}/test_cfg.html" ]] && grep -q "<!DOCTYPE html>" "${TMP_DIR}/test_cfg.html"; then
    _pass "GET /configure returned full mios.html contents ($(wc -c < "${TMP_DIR}/test_cfg.html") bytes)"
else
    _fail "GET /configure did not return valid HTML content"
fi

# Test 7: Layered TOML retrieval (GET /portal/config)
echo "[test-configurator-server] Test 7: Layered TOML SSOT endpoint (GET /portal/config)"
curl -s "http://127.0.0.1:${TEST_PORT}/portal/config" -o "${TMP_DIR}/test_portal.toml"
if [[ -s "${TMP_DIR}/test_portal.toml" ]] && (grep -q "\[identity\]" "${TMP_DIR}/test_portal.toml" || grep -q "\[meta\]" "${TMP_DIR}/test_portal.toml"); then
    _pass "GET /portal/config returned valid layered TOML from SSOT ($(wc -c < "${TMP_DIR}/test_portal.toml") bytes)"
else
    _fail "GET /portal/config did not return expected TOML sections"
fi

# Test 8: Positive Control - Save an edit into the user tier (POST /portal/config)
# The configurator posts the whole live config back with its edits; the save
# keeps only what differs from the lower tiers. MIOS_USER_TOML points the user
# tier at the test's temp dir so the runner's own ~/.config is never touched.
echo "[test-configurator-server] Test 8: Save an edit into the user tier (POST /portal/config)"
VALID_PAYLOAD="${TMP_DIR}/edited.toml"
curl -s "http://127.0.0.1:${TEST_PORT}/portal/config" | python3 -c '
import sys
lines = sys.stdin.read().split("\n")
start = lines.index("[identity]")
for i in range(start + 1, len(lines)):
    if lines[i].startswith("["):
        sys.exit("no username in [identity]")
    if lines[i].startswith("username = "):
        lines[i] = "username = \"test-operator\""
        break
print("\n".join(lines), end="")
' > "$VALID_PAYLOAD"

SAVE_RESP="$(curl -s -w "\n%{http_code}" -X POST \
    -H "Content-Type: application/toml" \
    --data-binary "@${VALID_PAYLOAD}" \
    "http://127.0.0.1:${TEST_PORT}/portal/config")"

HTTP_STATUS="$(echo "$SAVE_RESP" | tail -n1)"
BODY_RESP="$(echo "$SAVE_RESP" | sed '$d')"

if [[ "$HTTP_STATUS" == "200" ]] && [[ -f "$TEST_PROFILE" ]]; then
    _pass "POST /portal/config succeeded with HTTP 200 and created $TEST_PROFILE"
    if grep -q "test-operator" "$TEST_PROFILE"; then
        _pass "Persisted user tier contains the edited value"
    else
        _fail "Persisted user tier missing the edited value"
    fi
    if grep -q '^\[ports\]' "$TEST_PROFILE"; then
        _fail "Unchanged [ports] were copied into the user tier"
    else
        _pass "Only the edit was persisted; unchanged sections stay in lower tiers"
    fi
else
    _fail "POST /portal/config failed with HTTP $HTTP_STATUS: $BODY_RESP"
fi

# Test 9: Negative Control - Reject invalid TOML syntax
echo "[test-configurator-server] Test 9: Reject invalid TOML syntax (negative control)"
INVALID_PAYLOAD='[broken_section
missing_bracket = "invalid"
'

ERR_RESP="$(curl -s -w "\n%{http_code}" -X POST \
    -H "Content-Type: application/toml" \
    --data-binary "$INVALID_PAYLOAD" \
    "http://127.0.0.1:${TEST_PORT}/portal/config")"

ERR_STATUS="$(echo "$ERR_RESP" | tail -n1)"
if [[ "$ERR_STATUS" == "422" ]]; then
    _pass "POST /portal/config rejected malformed TOML with HTTP 422 Unprocessable Entity"
else
    _fail "POST /portal/config accepted malformed TOML (HTTP $ERR_STATUS, expected 422)"
fi

echo "[test-configurator-server] === Test Results: $pass passed, $fail failed ==="
if [[ $fail -gt 0 ]]; then
    exit 1
fi

echo "[test-configurator-server] SUCCESS: All $pass tests passed (100% success)."
