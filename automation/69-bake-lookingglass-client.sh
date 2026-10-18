#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Builds the Looking Glass client from upstream gnif/LookingGlass and installs it to /usr/bin/looking-glass-client. mios.toml [build.bake_refs].lookingglass = "latest" means the tip of the upstream default branch (the IDD era: the Windows guest runs the Looking Glass IDD of the same commit); the resolved commit is only logged and recorded as build output in /usr/share/mios/looking-glass/build.json, never written back to the SSOT.
# AI-related: usr/libexec/mios/vfio/setup-looking-glass.py (--write-client-ini; --provision-guest installs the IDD of the recorded commit in the MiOS-Xbox guest), usr/share/mios/windows/mios-looking-glass-idd.ps1, usr/share/mios/mios.toml [build.bake_refs] [vm.looking_glass] [packages.looking-glass-build]
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/packages.sh"

# MIOS_LG_DESTDIR stages the install under another root (a dev-container build copied onto a running
# host); empty for the image build. MIOS_LG_JOBS caps make's parallelism (default: all CPUs).
DESTDIR="${MIOS_LG_DESTDIR:-}"
LG_REPO="https://github.com/gnif/LookingGlass.git"
LG_BIN="$DESTDIR/usr/bin/looking-glass-client"
LG_RECORD="$DESTDIR/usr/share/mios/looking-glass/build.json"

# Client config from mios.toml [vm.looking_glass] (SPICE host/port/audio, shmFile, win:/egl: options) ->
# /etc/looking-glass-client.ini, the same values the VM domain profile carries; mios-vfio-setup.service
# re-projects it on every boot.
_LG_SETUP=/usr/libexec/mios/vfio/setup-looking-glass.py
[[ -x "$_LG_SETUP" ]] || _LG_SETUP="$(dirname "${BASH_SOURCE[0]}")/../usr/libexec/mios/vfio/setup-looking-glass.py"
python3 "$_LG_SETUP" --write-client-ini "$DESTDIR/etc/looking-glass-client.ini" || mios_warn "Looking Glass client config not written"

# "latest" floats with upstream: the default-branch HEAD (never a pinned commit). Any other value is a
# branch or tag name.
LG_REF="${MIOS_BUILD_BAKE_REFS_LOOKINGGLASS:-latest}"
if [[ "$LG_REF" == latest ]]; then
    LG_SHA="$(git ls-remote "$LG_REPO" HEAD | awk '{print $1; exit}')" || true
else
    LG_SHA="$(git ls-remote "$LG_REPO" "refs/tags/$LG_REF^{}" "refs/heads/$LG_REF" "refs/tags/$LG_REF" | awk '{print $1; exit}')" || true
fi
[[ "$LG_SHA" =~ ^[0-9a-f]{40}$ ]] || die "Looking Glass: ref '$LG_REF' could not be resolved on $LG_REPO"
mios_log "Looking Glass: ref '$LG_REF' resolves to $LG_SHA"

if [[ -x "$LG_BIN" && -r "$LG_RECORD" ]] && grep -q "\"commit\": \"$LG_SHA\"" "$LG_RECORD"; then
    mios_ok "Looking-glass-client already built from $LG_SHA"
    exit 0
fi

MISSING=""
for tool in cmake make gcc git; do
    command -v "$tool" >/dev/null 2>&1 || MISSING="${MISSING}${tool} "
done
if [[ -n "$MISSING" ]]; then
    mios_skip "missing toolchain: $MISSING"
    exit 0
fi

mios_log "Ensure Looking Glass client build deps from SSOT"
install_packages "looking-glass-build"

BUILD_DIR="/tmp/LookingGlass-build"
LG_OK=""
LG_VERSION=""
for attempt in 1 2 3; do
    mios_log "Compile attempt $attempt/3"
    cd /                 # leave any prior attempt's build dir BEFORE removing it -- otherwise the
    rm -rf "$BUILD_DIR"  # next git clone runs from a deleted CWD ("Unable to read current working directory").

    _branch=() _fref=HEAD
    [[ "$LG_REF" == latest ]] || { _branch=(--branch "$LG_REF"); _fref="$LG_REF"; }
    if ! git clone --depth 1 "${_branch[@]}" "$LG_REPO" "$BUILD_DIR"; then
        mios_warn "Git clone failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi
    cd "$BUILD_DIR"
    LG_SHA="$(git rev-parse HEAD)"
    # The version upstream builds report (git describe --tags --abbrev=10). A shallow clone has no tags:
    # deepen only back to the newest release tag and count the commits since it.
    LG_TAG="$(git ls-remote --tags --refs "$LG_REPO" | awk -F/ '{print $3}' | grep -E '^B[0-9]+(\.[0-9]+)*$' | sort -V | tail -1 || true)"
    LG_VERSION="$(git rev-parse --short=10 HEAD)"
    if [[ -n "$LG_TAG" ]] && git fetch -q --shallow-exclude="$LG_TAG" origin "$_fref" 2>/dev/null; then
        _n="$(git rev-list --count HEAD)"
        LG_VERSION="${LG_TAG}-${_n}-g$(git rev-parse --short=10 HEAD)"
    fi
    printf '%s\n' "$LG_VERSION" > VERSION
    if ! git submodule update --init --recursive --depth 1; then
        mios_warn "Submodule fetch failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    mios_log "Configure client build ($LG_VERSION, $LG_SHA)"
    mkdir -p "$BUILD_DIR/client/build"
    cd "$BUILD_DIR/client/build"
    if ! cmake -DCMAKE_INSTALL_PREFIX=/usr \
               -DCMAKE_INSTALL_LIBDIR=/usr/lib \
               -DCMAKE_BUILD_TYPE=Release \
               -DCMAKE_C_FLAGS='-Wno-error -Wno-error=maybe-uninitialized -Wno-maybe-uninitialized' \
               -DENABLE_LIBDECOR=ON \
               -DENABLE_PIPEWIRE=ON \
               -DENABLE_PULSEAUDIO=OFF \
               -DENABLE_BACKTRACE=OFF \
               ..; then
        mios_warn "Cmake configure failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    mios_log "Build looking-glass-client"
    if ! make -j"${MIOS_LG_JOBS:-$(nproc)}"; then
        mios_warn "Make failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    mios_log "Install binary to $LG_BIN"
    install -Dm0755 looking-glass-client "$LG_BIN"
    if [[ -x "$LG_BIN" ]]; then
        LG_OK=1
        break
    fi
done

if [[ -z "$LG_OK" ]]; then
    mios_warn "Looking-glass-client build failed after 3 attempts"
    exit 1
fi

record_version looking-glass "$LG_VERSION" "https://github.com/gnif/LookingGlass/commit/${LG_SHA}"
# Build output, not SSOT: the commit this client speaks, so the guest IDD is fetched for the same one.
install -Dm0644 /dev/stdin "$LG_RECORD" <<JSON
{
  "ref": "$LG_REF",
  "commit": "$LG_SHA",
  "version": "$LG_VERSION",
  "repo": "$LG_REPO",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
mios_log "Looking Glass $LG_VERSION ($LG_SHA) recorded in $LG_RECORD"

install -Dm0644 /dev/stdin "$DESTDIR/usr/share/applications/looking-glass.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=Looking Glass
Comment=Low-latency KVM display from a VM via shared memory
Icon=video-display
Exec=looking-glass-client
Terminal=false
Categories=System;Utility;
Keywords=KVM;VFIO;Passthrough;
DESK

mios_log "Cleanup source tree"
cd /
rm -rf "$BUILD_DIR"

mios_ok "Looking-glass-client $LG_VERSION baked in at $LG_BIN"
