#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Dispatches Quadlet placeholder rendering to the native mios-render-quadlets; every list comes from [build.quadlet_render].
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

# shellcheck disable=SC1090  # log.sh resolves at runtime: build ctx or installed
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"

# shellcheck source=/dev/null
source "$_self_dir/lib/common.sh"

if id -u mios >/dev/null 2>&1; then
    # Declared then assigned: `export X="$(cmd)"` masks the command's exit status.
    MIOS_CODE_SERVER_UID="$(id -u mios)"
    MIOS_CODE_SERVER_GID="$(id -g mios)"
    export MIOS_CODE_SERVER_UID MIOS_CODE_SERVER_GID
fi

mios_log "Render Quadlet placeholders from mios.toml"

# No `command -v miosd` branch: unreachable at bake (task_962). Dispatch is by
# absolute path only, so which renderer runs is not a function of PATH.
_renderer=""
for _c in /usr/libexec/mios/mios-render-quadlets \
          "${ROOT}/tools/native/target/release/mios-render-quadlets" \
          "${ROOT}/tools/native/target/debug/mios-render-quadlets"; do
    [ -x "$_c" ] && { _renderer="$_c"; break; }
done

if [ -z "$_renderer" ]; then
    # No bash fallback on purpose. The two it replaced disagreed by fourteen
    # variable names, could not nest, and destroyed systemd's $$ escape (task_977).
    mios_err "mios-render-quadlets is not available -- refusing to render with a substitute that corrupts \$\$ and cannot nest"
    exit 1
fi

mios_log "Using $_renderer"
if ! "$_renderer" --root "$ROOT"; then
    mios_err "failed to render Quadlet placeholders"
    exit 1
fi

mios_ok "Quadlet placeholders rendered"
exit 0
