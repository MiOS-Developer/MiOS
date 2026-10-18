#!/bin/bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Installs Qt6 build-time tools, clones the quickshell repository, gates it on closed Qt property update groups
# (src/quickshell/check-update-groups.py; carries the HyprlandToplevel fix for older refs), compiles it, and deploys the default declarative QML pa...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"

DESTDIR="${MIOS_QUICKSHELL_DESTDIR:-${DESTDIR:-}}"

if [[ "${MIOS_QUICKSHELL_SKIP_DEPS:-0}" != 1 ]] && command -v install_packages >/dev/null; then
    mios_log "Installing Qt6 build dependencies"
    install_packages "quickshell-build" || mios_log "quickshell-build packages not installable here; using what is present"
fi

mios_log "Compiling quickshell from upstream"
source "${SCRIPT_DIR}/lib/common.sh" 2>/dev/null || true

PIN_REF="${MIOS_BUILD_BAKE_REFS_QUICKSHELL:-latest}"
[ "$PIN_REF" != latest ] || PIN_REF="$(/usr/libexec/mios/mios-bake-plan latest-git "${MIOS_URL_QUICKSHELL:-https://github.com/quickshell-mirror/quickshell.git}")" || { echo "quickshell: newest release could not be resolved" >&2; exit 1; }
mios_log "Quickshell pin ref: ${PIN_REF}"

BUILD_DIR="/tmp/quickshell-build"
QUICKSHELL_OK=""

for attempt in 1 2 3; do
    mios_log "Compilation attempt $attempt/3"
    cd /tmp
    rm -rf "$BUILD_DIR"

    if ! git clone "${MIOS_URL_QUICKSHELL:-https://github.com/quickshell-mirror/quickshell.git}" "$BUILD_DIR"; then
        mios_warn "Git clone failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    cd "$BUILD_DIR"
    if ! git checkout "$PIN_REF"; then
        mios_warn "Git checkout to $PIN_REF failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    git submodule sync --recursive || true
    if ! git submodule update --init --recursive --force; then
        mios_warn "Git submodule update failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    # Every Qt property update group must close on all paths (src/quickshell/check-update-groups.py). Quickshell refs
    # before upstream 1bd5b083 return inside one in HyprlandToplevel::updateFromObject(); the shell's computed
    # bindings then freeze and the status island / dock stop reserving their rows (tiled windows run under them).
    # Carry the fix for such refs; refuse a ref that leaks anywhere else.
    check_tool="${SCRIPT_DIR}/../src/quickshell/check-update-groups.py"
    [[ -f "$check_tool" ]] || check_tool="/src/quickshell/check-update-groups.py"
    patch_file="${SCRIPT_DIR}/../src/quickshell/patches/hyprland-toplevel-update-group.patch"
    [[ -f "$patch_file" ]] || patch_file="/src/quickshell/patches/hyprland-toplevel-update-group.patch"

    if ! python3 -I "$check_tool" "$BUILD_DIR/src"; then
        git -C "$BUILD_DIR" apply --verbose "$patch_file" \
            || { mios_err "Quickshell ${PIN_REF} leaks a Qt property update group and the carried fix no longer applies"; exit 1; }
        python3 -I "$check_tool" "$BUILD_DIR/src" \
            || { mios_err "Quickshell ${PIN_REF} still leaks a Qt property update group"; exit 1; }
        mios_log "Quickshell ${PIN_REF}: carried the HyprlandToplevel update-group fix"
    fi

    # Hermetic pkgconfig for GBM if gbm.pc is absent from host
    if ! pkg-config --exists gbm 2>/dev/null && [[ -e /usr/lib64/libgbm.so.1 ]]; then
        mkdir -p "$BUILD_DIR/pkgconfig"
        cat << 'EOF' > "$BUILD_DIR/pkgconfig/gbm.pc"
prefix=/usr
libdir=/usr/lib64
includedir=/usr/include

Name: gbm
Description: Generic Buffer Management
Version: 26.2.4
Libs: -L${libdir} -lgbm
Cflags: -I${includedir}
EOF
        export PKG_CONFIG_PATH="${BUILD_DIR}/pkgconfig:${PKG_CONFIG_PATH:-}"
    fi

    rm -rf build && mkdir -p build && cd build
    if (cmake -GNinja -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release -DCRASH_REPORTER=OFF .. 2>/dev/null || \
        cmake -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release -DCRASH_REPORTER=OFF .. 2>/dev/null || \
        cmake -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release ..) && \
       (ninja 2>/dev/null || cmake --build . --parallel "$(nproc)" 2>/dev/null || make -j1) && \
       (DESTDIR="$DESTDIR" cmake --install . 2>/dev/null || make install DESTDIR="$DESTDIR"); then
        if [[ -x "${DESTDIR}/usr/bin/quickshell" || -x /usr/bin/quickshell ]]; then
            QUICKSHELL_OK=1
            break
        fi
    fi

    mios_warn "Build failed on attempt $attempt"
    sleep $((attempt * 8))
done

if [[ -z "$QUICKSHELL_OK" ]]; then
    mios_warn "Quickshell build failed after 3 attempts"
    exit 1
fi

record_version quickshell "$PIN_REF" "https://github.com/quickshell-mirror/quickshell/tree/${PIN_REF}"

if [[ ! -s "${DESTDIR}/usr/share/mios/quickshell/Config.qml" && ! -s /usr/share/mios/quickshell/Config.qml ]]; then
    mios_log "Writing canonical ${DESTDIR}/usr/share/mios/quickshell/Config.qml"
    mkdir -p "${DESTDIR}/usr/share/mios/quickshell"
    cat << 'EOF' > "${DESTDIR}/usr/share/mios/quickshell/Config.qml"
import QtQuick
import Quickshell

ShellRoot {
    PanelWindow {}
    Sidebar {}
    Notifications { id: notifs }
}
EOF
    chmod 0644 "${DESTDIR}/usr/share/mios/quickshell/Config.qml"
fi
mios_ok "Installed ${DESTDIR}/usr/bin/quickshell and verified ${DESTDIR}/usr/share/mios/quickshell/Config.qml"

