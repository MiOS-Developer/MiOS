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

# Render each imperative generator's installed surface from the merged build SSOT (MIOS_VENDOR_TOML), so operator edits ship.
for _gen in ux/wm_config_gen.py desktop/gpu_terminal.py win/wt_profile_inject.py; do
    python3 "/usr/libexec/mios/${_gen}" --write-fixture /
done
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
