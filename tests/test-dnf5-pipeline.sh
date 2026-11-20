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

# 4. The repo phases take the Fedora release from the base image's os-release, never a literal:
# a default of 44 pointed fedora-44 repos at a Fedora 45 base and broke 05-repos's distro-sync.
_osr="$(mktemp -d)"
printf 'NAME="MiOS"\nID=mios\nVERSION_ID="0.3.0"\nPLATFORM_ID="platform:f45"\n' > "${_osr}/mios"
printf 'ID=fedora\nVERSION_ID=46\n' > "${_osr}/fedora"
printf 'ID=rhel\nVERSION_ID=9.4\nPLATFORM_ID="platform:el9"\n' > "${_osr}/rhel"
_got="$(mios_fedora_release "${_osr}/mios")" && [[ "$_got" == 45 ]] \
    || { echo "FAIL: rebranded os-release resolved to '${_got:-}' not 45 (PLATFORM_ID)" >&2; exit 1; }
_got="$(mios_fedora_release "${_osr}/fedora")" && [[ "$_got" == 46 ]] \
    || { echo "FAIL: Fedora os-release resolved to '${_got:-}' not 46 (VERSION_ID)" >&2; exit 1; }
if mios_fedora_release "${_osr}/rhel" >/dev/null 2>&1 || mios_fedora_release "${_osr}/absent" >/dev/null 2>&1; then
    echo "FAIL: mios_fedora_release guessed a release for a non-Fedora or missing os-release" >&2; exit 1
fi
rm -rf -- "${_osr}"
for _phase in 05-repos.sh 06-enable-external-repos.sh; do
    if grep -nE 'FEDORA_VERSION:-[0-9]' "${ROOT_DIR}/automation/${_phase}"; then
        echo "FAIL: ${_phase} defaults FEDORA_VERSION to a release literal" >&2; exit 1
    fi
    grep -q 'mios_fedora_release' "${ROOT_DIR}/automation/${_phase}" \
        || { echo "FAIL: ${_phase} does not resolve the release from os-release" >&2; exit 1; }
done
echo "PASS: repo phases resolve the Fedora release from os-release (MiOS rebrand, Fedora, refusal)"

echo "=== [test-dnf5-pipeline] All checks passed ==="
exit 0
