#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Sets executable permissions for the core mios- CLI tools, bakes the SSOT-selected native code-server (its [build.pins.code_server] digest, no REST call) with its verified workbench patches and the newest digest-verified Satty release (mios-shot's annotation editor, falling back to [build.pins.satty] by name when GitHub answers empty), and installs auxiliary scripts like mios-toggle-headless.
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck disable=SC1090  # log.sh resolves at runtime: build ctx or installed
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mios_log "Configure MiOS CLI tools"

TOOLS=(
    mios
    mios-backup
    mios-build
    mios-chrome
    mios-deploy
    mios-pull
    mios-rebuild
    mios-update
    hermes
)

for tool in "${TOOLS[@]}"; do
    if [ -f "/usr/bin/$tool" ]; then
        chmod +x "/usr/bin/$tool"
    fi
done

[[ -f "/usr/bin/mios-dash" ]] || ln -sf /usr/libexec/mios/mios-dashboard /usr/bin/mios-dash 2>/dev/null || true

# code-server, native: the [image.sidecars].code_server release (":latest" floats
# to upstream's newest release), its workbench stylesheet and both workbench.js
# patches baked and verified. A container that cannot run the sidecar (the
# devcontainer, Codespaces, a cloud session) starts this one on
# [ports].code_server; an upstream release that moves a patch anchor fails the build.
# The RPM is proven by [build.pins.code_server] when that is the release named
# (no REST call), else by the sha256 GitHub publishes for it; the version baked
# is build output (version manifest + SBOM).
mios_log "Install and bake code-server"
_src="$(cd "${SCRIPT_DIR}/.." && pwd)"
_toml_get() { MIOS_TOML_ROOT="$_src" "${MIOS_TOML_GET:-$_src/usr/libexec/mios/mios-toml-get}" --vendor "$@"; }
# shellcheck source=automation/lib/upstream-latest.sh
source "${SCRIPT_DIR}/lib/upstream-latest.sh"
# _cs_fetch_rpm IMAGE DIR: fetch the RPM of the release IMAGE names (":latest" ->
# upstream's newest) into DIR. Sets _cs_version, _cs_rpm, _cs_sha and _cs_url.
_cs_fetch_rpm() {
    local image="$1" dir="$2" want
    want="${image##*:}"
    [[ "$image" == *:* ]] || want=latest
    [[ "$want" == latest || "$want" =~ ^[0-9]+(\.[0-9]+)+$ ]] \
        || { mios_err "[image.sidecars].code_server '${image}' names non-release '${want}'"; exit 1; }
    mios_pinned_asset code_server "$want" "$dir" code-server \
        || { mios_err "code-server ${want}: no verified RPM"; exit 1; }
    [[ "$mios_pin_version" =~ ^[0-9]+(\.[0-9]+)+$ ]] \
        || { mios_err "[image.sidecars].code_server '${image}' resolved to non-release '${mios_pin_version}'"; exit 1; }
    _cs_version="$mios_pin_version" _cs_rpm="$mios_pin_file" _cs_sha="$mios_pin_sha" _cs_url="$mios_pin_url"
}
_cs_dl="$(mktemp -d)"
_cs_fetch_rpm "$(_toml_get image.sidecars code_server)" "$_cs_dl"
dnf install -y "$_cs_rpm"
rm -rf -- "$_cs_dl"
mios_record_resolved code-server "$_cs_version" "$_cs_url" "$_cs_sha"
_cs_scrollbar="$(_toml_get theme.edge code_server_scrollbar_px)"
_cs_perimeter="$(_toml_get theme.edge code_server_perimeter_px)"
[[ -n "$_cs_scrollbar" && -n "$_cs_perimeter" ]] \
    || { mios_err "[theme.edge].code_server_scrollbar_px or code_server_perimeter_px is empty"; exit 1; }
_cs_bake=(--target /usr/lib/code-server/lib/vscode/out/vs/code/browser/workbench/workbench.html
          --css "$_src/usr/share/mios/themes/code-server-terminal.css"
          --scrollbar-px "$_cs_scrollbar" --perimeter-px "$_cs_perimeter")
python3 "$_src/usr/libexec/mios/mios-vscode-custom-css" patch "${_cs_bake[@]}"
python3 "$_src/usr/libexec/mios/mios-vscode-custom-css" verify "${_cs_bake[@]}"
mios_ok "code-server ${_cs_version} baked and verified"

# Satty (MPL-2.0, Satty-org/Satty): mios-shot's annotation editor ([desktop.screenshot].editors), run as its own
# program and never linked. The newest upstream release; its prebuilt satty-<arch>-unknown-linux-gnu.tar.gz is
# accepted only against the sha256 GitHub publishes for it or, for the [build.pins.satty] release, the pinned one
# (an empty GitHub answer bakes that pin by name). The licence must still be MPL-2.0, and the version baked is
# build output (version manifest + SBOM).
_satty_repo="$(_toml_get desktop.screenshot satty_repository 2>/dev/null || true)"
if [[ -n "$_satty_repo" ]]; then
    mios_log "Install satty (latest release)"
    _satty_dl="$(mktemp -d)"
    mios_pinned_asset satty latest "$_satty_dl" satty \
        || { mios_err "satty: no verified $(uname -m) release asset"; exit 1; }
    tar -xzf "$mios_pin_file" -C "$_satty_dl" ./satty ./LICENSE
    grep -q '^Mozilla Public License Version 2.0' "${_satty_dl}/LICENSE" \
        || { mios_err "satty LICENSE is no longer MPL-2.0 -- refusing to bake"; exit 1; }
    install -D -m 0755 "${_satty_dl}/satty" /usr/bin/satty
    install -D -m 0644 "${_satty_dl}/LICENSE" /usr/share/licenses/satty/LICENSE
    rm -rf -- "$_satty_dl"
    mios_record_resolved satty "$mios_pin_version" "$mios_pin_url" "$mios_pin_sha"
    mios_ok "satty ${mios_pin_version} installed (mios-shot annotation editor)"
fi

if [ -f "/usr/libexec/mios/mios-vscode-custom-css" ]; then
    chmod +x "/usr/libexec/mios/mios-vscode-custom-css"
    ln -sf "/usr/libexec/mios/mios-vscode-custom-css" "/usr/bin/mios-vscode-custom-css" 2>/dev/null || true
    /usr/libexec/mios/mios-vscode-custom-css install --all 2>/dev/null || true
    mios_ok "Configured VS Code and code-server custom CSS extension across environments"
fi

mios_log "Install mios-toggle-headless"
if [ -f "${SCRIPT_DIR}/mios-toggle-headless" ]; then
    install -Dm0755 "${SCRIPT_DIR}/mios-toggle-headless" "/usr/bin/mios-toggle-headless"
fi

USERENV_SRC=""
for cand in \
    "${SCRIPT_DIR}/../tools/lib/userenv.sh" \
    "/tmp/build/tools/lib/userenv.sh" \
    "/ctx/tools/lib/userenv.sh"
do
    if [[ -f "$cand" ]]; then USERENV_SRC="$cand"; break; fi
done
if [[ -n "$USERENV_SRC" ]]; then
    install -D -m 0644 "$USERENV_SRC" /usr/lib/mios/userenv.sh
    mios_ok "Installed userenv.sh resolver to /usr/lib/mios/userenv.sh"
else
    mios_warn "Tools/lib/userenv.sh not found in build context; mios-env will fall back to legacy env-style files only"
fi

# --- Multi-user Nix Subsystem Setup ---
mios_log "Configure multi-user Nix subsystem"
mkdir -p /etc/nix
if [[ -f /usr/share/mios/nix/nix.conf && ! -f /etc/nix/nix.conf ]]; then
    cp /usr/share/mios/nix/nix.conf /etc/nix/nix.conf
    chmod 0644 /etc/nix/nix.conf
    mios_ok "Deployed default /etc/nix/nix.conf from /usr/share/mios/nix/nix.conf"
fi

mkdir -p /etc/profile.d
cat > /etc/profile.d/nix.sh << 'EOF'
# Nix multi-user environment setup for MiOS
if [ -n "${BASH_VERSION:-}" ] || [ -n "${ZSH_VERSION:-}" ]; then
    export NIX_PROFILES="/nix/var/nix/profiles/default ${HOME}/.nix-profile"
    export PATH="${HOME}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:${PATH}"
    if [ -e /etc/pki/tls/certs/ca-bundle.crt ]; then
        export NIX_SSL_CERT_FILE="/etc/pki/tls/certs/ca-bundle.crt"
    elif [ -e /etc/ssl/certs/ca-certificates.crt ]; then
        export NIX_SSL_CERT_FILE="/etc/ssl/certs/ca-certificates.crt"
    fi
fi
EOF
chmod 0644 /etc/profile.d/nix.sh

for unit in nix-daemon.socket nix-daemon.service; do
    if systemctl list-unit-files "${unit}" &>/dev/null; then
        systemctl enable "${unit}" 2>/dev/null || true
        mios_ok "Enabled systemd unit: ${unit}"
    fi
done

mios_log "Bake the MiOS desktop tmux ([keybindings.tmux.desktop.source])"
# The patched tmux backend (float-pane) the TTY desktop and the relay's worker placement need.
# Its build-only inputs leave the layer again: only the binary, its receipt and runtime libraries stay.
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lib/packages.sh"
_td_before="$(mktemp)"
rpm -qa --qf '%{NAME}\n' | sort -u > "$_td_before"
install_packages_strict "tmux-desktop-build"
bash "${SCRIPT_DIR}/lib/tmux-desktop.sh" "${SCRIPT_DIR}/.." /
mapfile -t _td_added < <(rpm -qa --qf '%{NAME}\n' | sort -u | comm -13 "$_td_before" -)
if ((${#_td_added[@]})); then dnf -y remove --noautoremove "${_td_added[@]}"; fi
rm -f -- "$_td_before"
# The runtime libraries must outlive the build set's removal.
"$(/usr/libexec/mios/mios-toml-get keybindings.tmux.desktop binary)" -V

mios_ok "CLI tools and Nix subsystem configured; run 'mios'"
