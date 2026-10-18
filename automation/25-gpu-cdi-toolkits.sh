#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs AMD and Intel vendor-specific CDI (Container Device Interface) generator tools (amd-ctk and intel-cdi-specs-ge...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck disable=SC1090  # log.sh resolves at runtime: build ctx or installed
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "$0")/lib/common.sh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"

# shellcheck source=automation/lib/upstream-latest.sh
source "${SCRIPT_DIR}/lib/upstream-latest.sh"

# No fallback tags: the newest release is resolved at build time (forge
# releases/latest, else git ls-remote over the [build.float] shapes), every
# downloaded binary is proven by the sha256 GitHub publishes for that release
# asset, and the resolved tag + sha256 are recorded as build output.
AMD_REPO="https://github.com/ROCm/container-toolkit.git"
INTEL_REPO="https://github.com/intel/intel-resource-drivers-for-kubernetes.git"

_install_amd_ctk() {
    mios_log "AMD: resolving latest amd-container-toolkit release"
    AMD_TAG="$(mios_latest_git_ref "$AMD_REPO" || true)"
    installed_amd=0
    mkdir -p /tmp/amd-cdi-dl
    if [[ -n "$AMD_TAG" ]]; then
        AMD_RPM="$(mios_gh_release_asset_name "$AMD_REPO" "$AMD_TAG" '^amd-container-toolkit-.*\.x86_64\.rpm$' || true)"
        if [[ -n "$AMD_RPM" ]]; then
            if AMD_SHA="$(mios_fetch_gh_release_asset "$AMD_REPO" "$AMD_TAG" "$AMD_RPM" "/tmp/amd-cdi-dl/${AMD_RPM}" "amd-container-toolkit ${AMD_TAG}")"; then
                if dnf5 install -y "/tmp/amd-cdi-dl/${AMD_RPM}" >/dev/null 2>&1 \
                   || dnf  install -y "/tmp/amd-cdi-dl/${AMD_RPM}" >/dev/null 2>&1 \
                   || rpm  -ivh --replacepkgs "/tmp/amd-cdi-dl/${AMD_RPM}" >/dev/null 2>&1; then
                    mios_record_resolved amd-container-toolkit "$AMD_TAG" \
                        "https://github.com/ROCm/container-toolkit/releases/tag/${AMD_TAG}" "$AMD_SHA"
                    mios_ok "AMD container toolkit ${AMD_TAG} installed via RPM"
                    installed_amd=1
                else
                    warn "AMD RPM verified but install failed"
                fi
            else
                mios_ul_pass $? || true
            fi
        else
            warn "AMD container toolkit ${AMD_TAG}: no x86_64 RPM asset listed (or forge unreachable)"
        fi
    else
        warn "AMD container toolkit: newest release could not be resolved"
    fi
    if [[ $installed_amd -eq 0 ]]; then
        # Go modules are verified against sum.golang.org (GOSUMDB), never unproven.
        if command -v go >/dev/null 2>&1 && GOBIN=/usr/bin go install github.com/ROCm/container-toolkit/cmd/amd-ctk@latest >/dev/null 2>&1; then
            mios_record_resolved amd-container-toolkit "go:latest" "github.com/ROCm/container-toolkit/cmd/amd-ctk@latest" \
                "$(sha256sum /usr/bin/amd-ctk 2>/dev/null | awk '{print $1}')"
            mios_ok "AMD container toolkit installed via go build"
        else
            warn "AMD container toolkit not installed (no verified artefact)"
        fi
    fi
    rm -rf /tmp/amd-cdi-dl
}

_install_intel_sg() {
    mios_log "Intel: resolving latest intel-resource-drivers-for-kubernetes specs-generator release"
    # The repo releases several drivers; only specs-generator-* tags carry this tool.
    INTEL_TAG="$(mios_latest_git_tag_matching "$INTEL_REPO" '^specs-generator-v?[0-9]+(\.[0-9]+)*$' || true)"
    INTEL_BIN="intel-cdi-specs-generator-linux-amd64"
    installed_intel=0
    mkdir -p /tmp/intel-cdi-dl
    if [[ -n "$INTEL_TAG" ]]; then
        if INTEL_SHA="$(mios_fetch_gh_release_asset "$INTEL_REPO" "$INTEL_TAG" "$INTEL_BIN" "/tmp/intel-cdi-dl/${INTEL_BIN}" "intel-cdi-specs-generator ${INTEL_TAG}")"; then
            install -d -m 0755 /usr/libexec/mios
            install -m 0755 "/tmp/intel-cdi-dl/${INTEL_BIN}" /usr/libexec/mios/intel-cdi-specs-generator
            mios_record_resolved intel-cdi-specs-generator "$INTEL_TAG" \
                "https://github.com/intel/intel-resource-drivers-for-kubernetes/releases/tag/${INTEL_TAG}" "$INTEL_SHA"
            mios_ok "Intel CDI specs-generator ${INTEL_TAG} installed at /usr/libexec/mios/intel-cdi-specs-generator"
            installed_intel=1
        else
            mios_ul_pass $? || true
        fi
    else
        warn "Intel CDI generator: newest specs-generator release could not be resolved"
    fi

    if [[ $installed_intel -eq 0 ]]; then
        if command -v go >/dev/null 2>&1 && GOBIN=/usr/libexec/mios go install github.com/intel/intel-resource-drivers-for-kubernetes/cmd/intel-cdi-specs-generator@latest >/dev/null 2>&1; then
            mios_record_resolved intel-cdi-specs-generator "go:latest" \
                "github.com/intel/intel-resource-drivers-for-kubernetes/cmd/intel-cdi-specs-generator@latest" \
                "$(sha256sum /usr/libexec/mios/intel-cdi-specs-generator 2>/dev/null | awk '{print $1}')"
            mios_ok "Intel CDI specs-generator installed via go build"
        else
            warn "Intel CDI specs-generator not installed (no verified artefact for ${INTEL_TAG:-unresolved})"
        fi
    fi
    rm -rf /tmp/intel-cdi-dl
}

_install_amd_ctk
_install_intel_sg
mios_ok "Done"
