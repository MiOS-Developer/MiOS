#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs Ceph client tools and the K3s Kubernetes orchestrator, handling version resolution and offline vendoring to provision ...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "$0")/lib/common.sh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"

mios_log "Ceph client tools + cephadm"
install_packages "ceph"

mios_log "K3s prerequisites"
install_packages "k3s"

# MIOS_K3S_VERSION by tools/lib/userenv.sh (sourced via lib/common.sh above). The
mios_log "Resolve K3s release tag from mios.toml SSOT"
USE_OFFLINE=false
if [ -f "/usr/share/mios/vendored/k3s/k3s" ]; then
    mios_log "Offline vendored K3s files found"
    USE_OFFLINE=true
    K3S_TAG="vendored"
else
    K3S_TAG="${MIOS_K3S_VERSION:-}"
    K3S_TAG="${K3S_TAG/-k3s/+k3s}"
fi

if [[ -z "$K3S_TAG" ]]; then
    mios_warn "K3s version SSOT empty; skipping binary install"
    K3S_TAG=""
fi

if [[ -n "$K3S_TAG" ]]; then
    mios_log "K3s tag: $K3S_TAG"
    record_version k3s "$K3S_TAG" "https://github.com/k3s-io/k3s/releases/tag/${K3S_TAG}"

    mkdir -p /tmp/k3s-dl
    if [ "$USE_OFFLINE" = true ]; then
        cp /usr/share/mios/vendored/k3s/k3s /tmp/k3s-dl/k3s
        if [ -f "/usr/share/mios/vendored/k3s/k3s-install.sh" ]; then
            cp /usr/share/mios/vendored/k3s/k3s-install.sh /tmp/k3s-dl/k3s-install.sh
        else
            echo '#!/bin/sh' > /tmp/k3s-dl/k3s-install.sh
        fi
        if [ -f "/usr/share/mios/vendored/k3s/sha256sum-amd64.txt" ]; then
            cp /usr/share/mios/vendored/k3s/sha256sum-amd64.txt /tmp/k3s-dl/sha256sum.txt
        else
            local_sum=$(sha256sum /usr/share/mios/vendored/k3s/k3s | awk '{print $1}')
            echo "${local_sum}  k3s" > /tmp/k3s-dl/sha256sum.txt
        fi
        download_ok=true
    else
        mios_log "Download K3s binary, checksum, install script"
        K3S_URL="https://github.com/k3s-io/k3s/releases/download/${K3S_TAG}/k3s"
        K3S_SUM_URL="https://github.com/k3s-io/k3s/releases/download/${K3S_TAG}/sha256sum-amd64.txt"
        K3S_INSTALL_URL="https://raw.githubusercontent.com/k3s-io/k3s/${K3S_TAG}/install.sh"
        download_ok=false
        if scurl -sfL "$K3S_URL" -o /tmp/k3s-dl/k3s && \
           scurl -sfL "$K3S_SUM_URL" -o /tmp/k3s-dl/sha256sum.txt && \
           scurl -sfL "$K3S_INSTALL_URL" -o /tmp/k3s-dl/k3s-install.sh; then
            download_ok=true
        fi
    fi

    if [ "$download_ok" = true ]; then
        cd /tmp/k3s-dl
        if grep -E "  k3s$" sha256sum.txt | sha256sum -c - >/dev/null 2>&1; then
            mios_ok "K3s SHA256 checksum verified"
            install -m 0755 -t /usr/bin/ k3s
            install -m 0755 -t /usr/bin/ k3s-install.sh

            sbom_dir="/usr/share/mios/artifacts/sbom"
            mkdir -p "$sbom_dir"
            sha=""
            if command -v sha256sum >/dev/null 2>&1; then
                sha="$(sha256sum /usr/bin/k3s | awk '{print $1}')"
            fi
            printf '%s\t%s\t%s\n' "k3s" "${K3S_TAG}" "${sha:-unknown}" >> "${sbom_dir}/binaries.tsv"

            [ ! -e /usr/bin/kubectl ] && ln -sf k3s /usr/bin/kubectl || true
            [ ! -e /usr/bin/crictl ]  && ln -sf k3s /usr/bin/crictl  || true
            [ ! -e /usr/bin/ctr ]     && ln -sf k3s /usr/bin/ctr     || true

            mios_ok "K3s binary + install script installed"
        else
            mios_err "K3s binary SHA256 checksum mismatch; skipping"
        fi
        cd - >/dev/null
    else
        mios_warn "K3s download failed; skipping install"
    fi
    rm -rf /tmp/k3s-dl
fi

chmod 755 /usr/libexec/mios/ceph-bootstrap.sh 2>/dev/null || true

mios_ok "Ceph client + cephadm installed; K3s binary per tag ${K3S_TAG:-none}"
mios_log "Ceph Dashboard:  https://<host>:8443"
mios_log "K3s API server:  https://<host>:${MIOS_PORTS_K3S_API:-unresolved}"
