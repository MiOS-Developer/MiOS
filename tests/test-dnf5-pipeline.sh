#!/usr/bin/env bash
# AI-hint: Verification suite for atomic DNF5 package pipeline, local cache staging, and mirror retry (task_447).
# AI-doc: usr/share/doc/mios/manual/ch44-host-package-overrides-and-dnf5.md
set -euo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== [test-dnf5-pipeline] Validating DNF5 caching and retry logic (task_447) ==="

# 1. Verify DNF_SETOPT options in common.sh
COMMON_SH="${ROOT_DIR}/automation/lib/common.sh"
if ! grep -q "cachedir=/var/cache/dnf5" "${COMMON_SH}"; then
    echo "FAIL: cachedir=/var/cache/dnf5 not found in ${COMMON_SH}" >&2
    exit 1
fi
if ! grep -q "keepcache=1" "${COMMON_SH}"; then
    echo "FAIL: keepcache=1 not found in ${COMMON_SH}" >&2
    exit 1
fi
if ! grep -q "clean_requirements_on_remove=1" "${COMMON_SH}"; then
    echo "FAIL: clean_requirements_on_remove=1 not found in ${COMMON_SH}" >&2
    exit 1
fi
echo "PASS: DNF_SETOPT contains cachedir, keepcache, and clean_requirements_on_remove"

# 2. Verify Containerfile cache mounts
CONTAINERFILE="${ROOT_DIR}/Containerfile"
if ! grep -q "dst=/var/cache/dnf5" "${CONTAINERFILE}"; then
    echo "FAIL: dst=/var/cache/dnf5 cache mount missing in ${CONTAINERFILE}" >&2
    exit 1
fi
echo "PASS: Containerfile mounts /var/cache/dnf5 cache"

# 3. Source packages.sh in subshell and test _dnf_retry_exec
PACKAGES_SH="${ROOT_DIR}/automation/lib/packages.sh"
# shellcheck source=/dev/null
source "${COMMON_SH}"
# shellcheck source=/dev/null
source "${PACKAGES_SH}"

if ! declare -f _dnf_retry_exec >/dev/null; then
    echo "FAIL: _dnf_retry_exec not declared in ${PACKAGES_SH}" >&2
    exit 1
fi
echo "PASS: _dnf_retry_exec function declared"

# 4. Test retry execution simulates retry
test_attempt=0
mock_fail_twice() {
    test_attempt=$((test_attempt + 1))
    if [[ $test_attempt -lt 2 ]]; then
        return 1
    fi
    return 0
}

_dnf_retry_exec mock_fail_twice 2>/dev/null
if [[ $test_attempt -ne 2 ]]; then
    echo "FAIL: Expected 2 attempts, got ${test_attempt}" >&2
    exit 1
fi
echo "PASS: _dnf_retry_exec retried and succeeded on attempt 2"

echo "=== [test-dnf5-pipeline] All checks passed ==="
exit 0
