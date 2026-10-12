#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Enables external DNF repositories (Terra, Kubernetes, ublue-os COPR) for MiOS by fetching .repo files into...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

enable_copr() {
    local repo="$1"
    local fallback_chroot="${2:-}"

    mios_log "COPR enable: $repo"
    if $DNF_BIN "${DNF_SETOPT[@]}" copr enable -y "$repo" 2>/dev/null; then
        return 0
    fi

    local fedora_ver=""
    if [ -f /etc/os-release ]; then
        fedora_ver=$(grep -oP 'platform:f\K[0-9]+' /etc/os-release || true)
    fi
    if [ -z "$fedora_ver" ] && command -v rpm &>/dev/null; then
        fedora_ver=$(rpm -q --qf '%{VERSION}' fedora-release 2>/dev/null | grep -oE '[0-9]+' | head -1 || true)
    fi

    if [ -n "$fedora_ver" ]; then
        mios_log "Detected Fedora $fedora_ver, retrying COPR with explicit chroot"
        if $DNF_BIN "${DNF_SETOPT[@]}" copr enable -y "$repo" "fedora-${fedora_ver}-x86_64" 2>/dev/null; then
            return 0
        fi
    fi

    if [ -n "$fallback_chroot" ]; then
        mios_log "Retrying COPR with fallback chroot: $fallback_chroot"
        if $DNF_BIN "${DNF_SETOPT[@]}" copr enable -y "$repo" "$fallback_chroot" 2>/dev/null; then
            return 0
        fi
    fi

    return 1
}

REPO_DIR=/etc/yum.repos.d
_fver="${FEDORA_VERSION:-44}"

try_fetch() {
    local url="$1" out="$2" label="$3"
    if scurl -fsSL --connect-timeout 20 --max-time 60 "$url" -o "$out" 2>/dev/null; then
        return 0
    fi
    mios_warn "${label}: fetch failed"
    rm -f "$out"
    return 1
}

if [[ ! -f "${REPO_DIR}/terra.repo" ]]; then
    mios_log "Enabling Terra repo"
    if [[ "${MIOS_ONLINE_BUILD:-0}" == "1" ]] || [[ ! -f "/usr/share/mios/repos/terra.repo" ]]; then
        try_fetch "${MIOS_URL_TERRA_REPO:-https://github.com/terrapkg/subatomic-repos/raw/main/terra.repo}" \
                  "${REPO_DIR}/terra.repo" "Terra repo" || true
    else
        mios_log "Using vendored Terra repo"
        cp "/usr/share/mios/repos/terra.repo" "${REPO_DIR}/terra.repo"
    fi
else
    mios_skip "Terra repo already present"
fi

# MIOS_DESKTOP_FLATPAKS / the Flatpak install path, never from an RPM repo. The

if [[ ! -f "${REPO_DIR}/kubernetes.repo" ]]; then
    # Kubernetes repo minor FLOATS from the k3s image-tag SSOT ([image.sidecars].k3s ->
    # MIOS_K3S_VERSION / MIOS_K3S_IMAGE): rancher/k3s v1.36.2-k3s1 -> k8s stable v1.36, kept
    # coordinated so a k3s bump cascades here automatically (no stale hardcoded minor).
    _k8s_src="${MIOS_K3S_VERSION:-${MIOS_K3S_IMAGE:-v1.36.2}}"
    _k8s_minor="$(printf '%s' "$_k8s_src" | grep -oE 'v?[0-9]+\.[0-9]+' | head -1 | tr -d 'v')"
    _k8s_minor="${_k8s_minor:-1.36}"
    mios_log "Enabling Kubernetes stable v${_k8s_minor} repo (floated from k3s SSOT)"
    cat > "${REPO_DIR}/kubernetes.repo" <<EOF
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v${_k8s_minor}/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v${_k8s_minor}/rpm/repodata/repomd.xml.key
repo_gpgcheck=1
exclude=kubelet kubeadm cri-tools kubernetes-cni
EOF
else
    mios_skip "Kubernetes repo already present"
fi

if [[ ! -f "${REPO_DIR}/ublue-os-packages.repo" ]]; then
    mios_log "Enabling ublue-os/packages COPR"
    _fver="${FEDORA_VERSION:-44}"
    if try_fetch "${MIOS_URL_UBLUE_REPO:-https://copr.fedorainfracloud.org/coprs/ublue-os/packages/repo/fedora-${_fver}/ublue-os-packages-fedora-${_fver}.repo}" \
                 "${REPO_DIR}/ublue-os-packages.repo" "ublue-os/packages COPR"; then
        if ! grep -q '^priority=' "${REPO_DIR}/ublue-os-packages.repo"; then
            sed -i '/^\[/a priority=75' "${REPO_DIR}/ublue-os-packages.repo"
        fi
    fi
else
    mios_skip "ublue-os/packages COPR already present"
fi

if ! [ -f /etc/yum.repos.d/_copr:copr.fedorainfracloud.org:aleasto:waydroid.repo ]; then
    enable_copr "aleasto/waydroid" "fedora-${_fver}-x86_64" || mios_warn "Aleasto/waydroid COPR enable failed"
else
    mios_skip "aleasto/waydroid COPR already present"
fi

# Project required dependency repositories natively, without a COPR plugin.
_resolver="${MIOS_RESOLVER_BIN:-/usr/bin/mios-resolver}"
[[ -x "$_resolver" ]] || _resolver=/usr/libexec/mios/mios-resolver
_repo_tmp="$(mktemp "${REPO_DIR}/.mios-external.XXXXXX")"
if ! "$_resolver" --emit=repos > "$_repo_tmp"; then
    rm -f "$_repo_tmp"
    die "Native external repository projection failed"
fi
chmod 0644 "$_repo_tmp"
mv -f "$_repo_tmp" "${REPO_DIR}/mios-external.repo"

if [[ ! -f "${REPO_DIR}/tailscale.repo" ]]; then
    mios_log "Enabling Tailscale official repo"
    try_fetch "${MIOS_URL_TAILSCALE_REPO:-https://pkgs.tailscale.com/stable/fedora/tailscale.repo}" \
              "${REPO_DIR}/tailscale.repo" "Tailscale repo" || true
else
    mios_skip "Tailscale repo already present"
fi

if [[ ! -f "${REPO_DIR}/crowdsec.repo" ]]; then
    mios_log "Enabling CrowdSec repo"
    _fver="${FEDORA_VERSION:-44}"
    try_fetch "${MIOS_URL_CROWDSEC_REPO:-https://packagecloud.io/crowdsec/crowdsec/config_file.repo?os=fedora&dist=${_fver}&source=script}" \
              "${REPO_DIR}/crowdsec.repo" "CrowdSec repo" || true
else
    mios_skip "CrowdSec repo already present"
fi

mios_log "External repos enabled; refreshing metadata"
if ! $DNF_BIN "${DNF_SETOPT[@]}" makecache -y 2>&1 | tail -20; then
    die "Dnf metadata refresh failed for the selected repositories"
fi

mios_log "Installing CrowdSec packages"
$DNF_BIN "${DNF_SETOPT[@]}" install -y --skip-unavailable crowdsec crowdsec-firewall-bouncer-nftables 2>&1 || mios_warn "CrowdSec packages install deferred"

mios_ok "External repos enabled"
