#!/usr/bin/env bash
# AI-hint: Unit tests for automation/lib/masking.sh and scurl wrapper.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/masking.sh"

echo "[test_masking] Starting masking and scurl unit tests"

test_binary_stream() {
    local tmp_src; tmp_src="$(mktemp)"
    local tmp_dst; tmp_dst="$(mktemp)"

    head -c 1048576 /dev/urandom > "$tmp_src"

    cat "$tmp_src" | mask_filter > "$tmp_dst"

    if ! cmp -s "$tmp_src" "$tmp_dst"; then
        echo "[FAIL] Binary stream was corrupted by mask_filter" >&2
        rm -f "$tmp_src" "$tmp_dst"
        exit 1
    fi
    rm -f "$tmp_src" "$tmp_dst"
    echo "[PASS] Binary stream byte-identity verified"
}

test_secret_masking() {
    local secret="super-secret-token-12345"
    add_mask "$secret"

    local out; out="$(echo "Log output with ${secret} included" | mask_filter)"
    if [[ "$out" != *"Log output with [MASKED] included"* ]]; then
        echo "[FAIL] Secret was not masked in text output: '$out'" >&2
        exit 1
    fi
    echo "[PASS] Secret masking verified"
}

test_scurl_parser() {
    curl() {
        echo "CURL_ARGS: $*"
    }

    local res; res="$(scurl -sSL --output=/tmp/test.tar.gz https://github.com/test)"
    if [[ "$res" != *"https://github.com/test"* ]]; then
        echo "[FAIL] scurl failed to parse URL with" >&2
        exit 1
    fi
    echo "[PASS] scurl argument parser verified"
}

# scurl attaches a GitHub token only to GitHub hosts, from the environment or
# else the image build's secret file, and never under xtrace.
test_scurl_github_token() {
    curl() { printf '%s\n' "$@"; }
    local sd; sd="$(mktemp -d)"
    local out
    (
        unset GH_TOKEN GITHUB_TOKEN GHCR_TOKEN
        # shellcheck disable=SC2034  # read by scurl in this subshell
        SCURL_SECRETS_DIR="$sd"
        out="$(scurl -sS https://api.github.com/repos/o/r)"
        [[ "$out" != *Authorization* ]] || { echo "[FAIL] token header sent with no token present" >&2; exit 1; }
        printf 'file-token-123\n' > "$sd/mios_github_token"
        out="$(scurl -sS https://api.github.com/repos/o/r)"
        [[ "$out" == *"Authorization: token file-token-123"* ]] || { echo "[FAIL] secret file token not sent: $out" >&2; exit 1; }
        out="$(scurl -sS https://evil.example/?u=https://github.com/x)"
        [[ "$out" != *Authorization* ]] || { echo "[FAIL] token sent to a non-GitHub host" >&2; exit 1; }
        out="$(scurl -sS https://github.com.evil.example/x)"
        [[ "$out" != *Authorization* ]] || { echo "[FAIL] token sent to a look-alike host" >&2; exit 1; }
        # shellcheck disable=SC2034  # read by scurl in this subshell
        GITHUB_TOKEN=env-token-456
        out="$(scurl -sS https://github.com/o/r/releases/download/v1/a)"
        [[ "$out" == *"Authorization: token env-token-456"* ]] || { echo "[FAIL] environment token not preferred: $out" >&2; exit 1; }
        out="$( { set -x; scurl -sS https://api.github.com/x >/dev/null; } 2>&1 )"
        [[ "$out" != *env-token-456* ]] || { echo "[FAIL] token traced under set -x" >&2; exit 1; }
    ) || { rm -rf -- "$sd"; exit 1; }
    rm -rf -- "$sd"
    unset -f curl
    echo "[PASS] scurl GitHub token scoping (secret file, env, host match, no trace) verified"
}

test_binary_stream
test_secret_masking
test_scurl_parser
test_scurl_github_token

echo "[test_masking] PASS: All masking and scurl tests passed"
