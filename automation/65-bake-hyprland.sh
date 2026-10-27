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
install_packages_strict "hyprland"
install_packages_strict "console-pointer"
# ShaderGlass overlay pane ([desktop.shaderglass]): Wine + DXVK only. The GPL-3 release itself is fetched and
# verified on the host by `miosd shaderglass install`, never baked into the image.
install_packages "shaderglass"
systemctl enable gpm.service mios-console-theme.service

# Render each imperative generator's installed surface from the merged build SSOT (MIOS_VENDOR_TOML), so operator edits ship.
for _gen in ux/wm_config_gen.py desktop/gpu_terminal.py win/wt_profile_inject.py; do
    python3 "/usr/libexec/mios/${_gen}" --write-fixture /
done
/usr/libexec/mios/mios-gen build-hyprglass --root /
/usr/libexec/mios/mios-gen build-hyprland-plugin --root / --name hypr3d
/usr/libexec/mios/mios-gen build-hyprland-plugin --root / --name mios-pacing
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
