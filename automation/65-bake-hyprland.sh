#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs Hyprland tiling compositor, XWayland, window routing helpers, and constructs the base layout configuration inside...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"

mios_log "Installing Hyprland compositor & tools"
# Select the compositor before installation using the same ABI hold as its native rebuild/plugins.
_hl_package="$(/usr/libexec/mios/mios-gen build-hyprland --root / --package-spec)"
install_packages_strict "hyprland" "hyprland" "$_hl_package"
# MiOS compositor patch series ([desktop.hyprland.compositor]): rebuild the installed release with it and replace the
# stock package (Release tagged, e.g. 2.mios1) before any plugin is built against it.
# The build's own dependencies (rpm-build, builddep) leave the layer again: only the patched package stays.
_hl_rpms="$(mktemp -d)"
_hl_before="$(mktemp)"
rpm -qa --qf '%{NAME}\n' | sort -u > "$_hl_before"
/usr/libexec/mios/mios-gen build-hyprland --root / --out "$_hl_rpms"
dnf -y install "$_hl_rpms"/hyprland-[0-9]*.rpm
mapfile -t _hl_added < <(rpm -qa --qf '%{NAME}\n' | sort -u | comm -13 "$_hl_before" - | grep -vx 'hyprland')
if ((${#_hl_added[@]})); then dnf -y remove --noautoremove "${_hl_added[@]}"; fi
rm -f -- "$_hl_before"
_hl_sources=("$_hl_rpms"/source/*.src.rpm)
[[ ${#_hl_sources[@]} -eq 1 && -f "${_hl_sources[0]}" ]] || { mios_err "Matching compositor SDK source is missing or ambiguous"; exit 1; }
install_packages_strict "console-pointer"
# ShaderGlass overlay pane ([desktop.shaderglass]): Wine + DXVK only. The GPL-3 release itself is fetched and
# verified on the host by `miosd shaderglass install`, never baked into the image.
install_packages "shaderglass"
systemctl enable gpm.service mios-console-theme.service mios-boot-display-report.service

# Render each imperative generator's installed surface from the merged build SSOT (MIOS_VENDOR_TOML), so operator edits ship.
for _gen in ux/wm_config_gen.py desktop/gpu_terminal.py win/wt_profile_inject.py; do
    python3 "/usr/libexec/mios/${_gen}" --write-fixture /
done
/usr/libexec/mios/mios-gen build-hyprglass --root / --srpm "${_hl_sources[0]}"
/usr/libexec/mios/mios-gen build-hyprland-plugin --root / --name hypr3d --srpm "${_hl_sources[0]}"
# Compiled CRT interface is an image-producer postcondition, not a lean source-CI assumption.
if [[ "$(/usr/libexec/mios/mios-toml-get desktop.hyprland.hypr3d enabled)" == true ]]; then
    _h3d_artifact="$(/usr/libexec/mios/mios-toml-get desktop.hyprland.hypr3d path)"
    MIOS_HYPR3D_SO="$_h3d_artifact" python3 "${SCRIPT_DIR}/../tests/test-hypr3d-crt.py" TestHypr3DCrtBinary
fi
/usr/libexec/mios/mios-gen build-hyprland-plugin --root / --name mios-pacing --srpm "${_hl_sources[0]}"
/usr/libexec/mios/mios-gen build-hyprland-plugin --root / --name mios-appmenu --srpm "${_hl_sources[0]}"
rm -rf -- "$_hl_rpms"

# The fallback and remote greeters share an owned GNU/NSS RPM. A stock distro
# upgrade must not quietly replace the login-name/display-name correction.
_tg_root="$(cd "${SCRIPT_DIR}/.." && pwd)"
_tg_get() { MIOS_TOML_ROOT="$_tg_root" "$_tg_root/usr/libexec/mios/mios-toml-get" --vendor desktop.greeter.tuigreet_source "$1"; }
_tg_package="$(_tg_get package_name)"
_tg_sources="$(_tg_get source_install_dir)"
_tg_out="$(mktemp -d)"
_tg_before="$(mktemp)"
rpm -qa --qf '%{NAME}\n' | sort -u > "$_tg_before"
install_packages_strict "tuigreet-build"
/usr/libexec/mios/mios-gen build-tuigreet --root "$_tg_root" --out "$_tg_out"
_tg_rpms=("$_tg_out"/*.rpm)
_tg_srpms=("$_tg_out"/source/*.src.rpm)
[[ ${#_tg_rpms[@]} -eq 1 && -f "${_tg_rpms[0]}" && ${#_tg_srpms[@]} -eq 1 && -f "${_tg_srpms[0]}" ]] \
    || { mios_err "The patched greeter must have one binary RPM and its matching source RPM"; exit 1; }
if ! _tg_provider="$(rpm -q --whatprovides /usr/bin/tuigreet --qf '%{NAME}\n')"; then
    [[ ! -e /usr/bin/tuigreet && ! -L /usr/bin/tuigreet ]] \
        || { mios_err "Refusing to replace an unowned greeter executable"; exit 1; }
    dnf -y install "${_tg_rpms[0]}"
elif [[ "$_tg_provider" == "$_tg_package" ]]; then
    dnf -y reinstall "${_tg_rpms[0]}"
else
    rpm -qp --qf '[%{CONFLICTNAME}\n]' "${_tg_rpms[0]}" | grep -Fx -- "$_tg_provider" \
        || { mios_err "Unreviewed installed greeter provider: $_tg_provider"; exit 1; }
    dnf -y swap "$_tg_provider" "${_tg_rpms[0]}"
fi
rpm -q --quiet -- "$_tg_package"
install -D -m 0644 "${_tg_srpms[0]}" "$_tg_sources/$(basename "${_tg_srpms[0]}")"
install -D -m 0644 "$_tg_out/build.json" "$_tg_sources/build.json"
install -D -m 0644 "$_tg_out/source/vendor-inventory.json" "$_tg_sources/vendor-inventory.json"
mapfile -t _tg_added < <(rpm -qa --qf '%{NAME}\n' | sort -u | comm -13 "$_tg_before" - | grep -vxF -- "$_tg_package")
if ((${#_tg_added[@]})); then dnf -y remove --noautoremove "${_tg_added[@]}"; fi
rm -f -- "$_tg_before"
rm -rf -- "$_tg_out"
unset -f _tg_get
/usr/libexec/mios/mios-gen render-hyprland --root /
# Project the user session from the same SSOT before registering the login entry.
while IFS= read -r _unit; do
    _rendered="$(mktemp)"
    /usr/libexec/mios/mios-unit-gen --root / --user --render "$_unit" > "$_rendered"
    install -D -m 0644 "$_rendered" "/usr/lib/systemd/user/$_unit"
    rm -f -- "$_rendered"
done < <(/usr/libexec/mios/mios-unit-gen --root / --user --list)
/usr/libexec/mios/mios-unit-gen --root / --user --check
/usr/libexec/mios/mios-gen render-console-theme --root /
/usr/libexec/mios/mios-sync-theme --bridge-only
/usr/libexec/mios/mios-dotfiles-render render rofi
install -D -m 0644 /usr/share/mios/rofi/config.rasi /etc/skel/.config/rofi/config.rasi
python3 /usr/libexec/mios/mios-theme-render --gtk --qt --config-root /etc/skel/.config
/usr/libexec/mios/mios-gen render-tmux-theme --write-fixture /
python3 -c 'import json,sys; sys.path.insert(0,"/usr/lib/mios"); import mios_toml; json.dump({"keybindings":mios_toml.load_merged()["keybindings"]},open("/tmp/mios-keybindings-build.json","w"))'
/usr/libexec/mios/mios-unit-gen keybindings --from-json /tmp/mios-keybindings-build.json --emit-json | \
    python3 -c 'import json,sys,pathlib; d=json.load(sys.stdin); [(pathlib.Path("/")/p).parent.mkdir(parents=True,exist_ok=True) or (pathlib.Path("/")/p).write_text(v) for p,v in d.items()]'
rm -f /tmp/mios-keybindings-build.json
command -v dconf >/dev/null && dconf update
mios_ok "Rendered Hyprland, Sway, Alacritty, WSL terminal profile and tmux theme from mios.toml"

# After the RPM, which ships its own copy at this path; the tracked overlay file is the one source.
_session="${SCRIPT_DIR}/../usr/share/wayland-sessions/hyprland.desktop"
[ -f "$_session" ] || _session="${CTX:-/ctx}/usr/share/wayland-sessions/hyprland.desktop"
install -D -m 0644 "$_session" /usr/share/wayland-sessions/hyprland.desktop
mios_ok "Registered /usr/share/wayland-sessions/hyprland.desktop"

mkdir -p /etc/hypr
if [[ ! -e /etc/hypr/hyprland.conf ]]; then
    ln -sf /usr/share/mios/hyprland/hyprland.conf /etc/hypr/hyprland.conf
fi

# Seed the native Lua entry used by current Hyprland.
install -D -m 0644 /etc/skel/.config/hypr/hyprland.lua /etc/hypr/hyprland.lua
