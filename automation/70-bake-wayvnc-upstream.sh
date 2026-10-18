#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Builds wayvnc + neatvnc + aml from the latest upstream default-branch HEADs (no pins; the resolved commits are recorded in build output /usr/libexec/mios/wayvnc/build.json) and installs a self-contained wayvnc (neatvnc/aml linked statically) to /usr/libexec/mios/wayvnc/wayvnc. The RDP bridge prefers it over Fedora's /usr/bin/wayvnc: the Fedora-packaged release exits on any cursor-capture failure ("Fatal error while capturing", main.c:1591), which Hyprland 0.56 produces when the pointer is on a scaled output; upstream keeps running without the cursor image.
# AI-related: /usr/libexec/mios/mios-rdp-rfb-bridge, /usr/libexec/mios/mios-rdp, /usr/share/mios/mios.toml [remote.rdp].wayvnc_binary [packages.wayvnc-build]
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/packages.sh"

# MIOS_WAYVNC_DESTDIR stages the install under another root (a dev-container build copied onto a running
# host); empty for the image build. MIOS_WAYVNC_JOBS caps ninja's parallelism.
DESTDIR="${MIOS_WAYVNC_DESTDIR:-}"
OUT_DIR="$DESTDIR/usr/libexec/mios/wayvnc"
BUILD_DIR="${MIOS_WAYVNC_BUILD_DIR:-/tmp/wayvnc-upstream-build}"
declare -A REPO=(
    [wayvnc]="https://github.com/any1/wayvnc.git"
    [neatvnc]="https://github.com/any1/neatvnc.git"
    [aml]="https://github.com/any1/aml.git"
)

# "latest" floats with upstream: each repo's default-branch HEAD, resolved now, never pinned.
declare -A SHA=()
for p in wayvnc neatvnc aml; do
    SHA[$p]="$(git ls-remote "${REPO[$p]}" HEAD | awk '{print $1; exit}')" || true
    [[ "${SHA[$p]}" =~ ^[0-9a-f]{40}$ ]] || die "wayvnc-upstream: HEAD of ${REPO[$p]} could not be resolved"
    mios_log "wayvnc-upstream: $p HEAD resolves to ${SHA[$p]}"
done

if [[ -x "$OUT_DIR/wayvnc" && -r "$OUT_DIR/build.json" ]] \
   && python3 -I - "$OUT_DIR/build.json" "${SHA[wayvnc]}" "${SHA[neatvnc]}" "${SHA[aml]}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if [d[p]["commit"] for p in ("wayvnc", "neatvnc", "aml")] == sys.argv[2:5] else 1)
PY
then
    mios_ok "wayvnc-upstream already built from ${SHA[wayvnc]:0:12} (neatvnc ${SHA[neatvnc]:0:12}, aml ${SHA[aml]:0:12})"
    exit 0
fi

[[ "${MIOS_WAYVNC_SKIP_DEPS:-0}" == 1 ]] || install_packages "wayvnc-build"
MISSING=""
for tool in meson ninja gcc git pkg-config; do
    command -v "$tool" >/dev/null 2>&1 || MISSING="${MISSING}${tool} "
done
[[ -z "$MISSING" ]] || { mios_skip "missing toolchain: $MISSING"; exit 0; }

cd /
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
fetch() {  # fetch <name> <dir>: exactly the resolved commit
    local p="$1" d="$2"
    git init -q "$d"
    git -C "$d" fetch -q --depth 1 "${REPO[$p]}" "${SHA[$p]}"
    git -C "$d" checkout -q FETCH_HEAD
    [[ "$(git -C "$d" rev-parse HEAD)" == "${SHA[$p]}" ]] || die "wayvnc-upstream: $p checkout is not ${SHA[$p]}"
}
fetch wayvnc "$BUILD_DIR/wayvnc"
mkdir -p "$BUILD_DIR/wayvnc/subprojects"
fetch neatvnc "$BUILD_DIR/wayvnc/subprojects/neatvnc"
fetch aml "$BUILD_DIR/wayvnc/subprojects/aml"

cd "$BUILD_DIR/wayvnc"
# neatvnc/aml as static subprojects: the binary depends only on Fedora's system libraries.
meson setup build --buildtype=release --prefix=/usr/libexec/mios/wayvnc \
    -Ddefault_library=static -Dman-pages=disabled -Dtests=false \
    -Dneatvnc:examples=false -Dneatvnc:benchmarks=false -Dneatvnc:tests=false
ninja -C build -j"${MIOS_WAYVNC_JOBS:-$(nproc)}" wayvnc
VERSION="$(./build/wayvnc --version 2>&1 | head -1)"
[[ -n "$VERSION" ]] || die "wayvnc-upstream: built binary does not run"
if ldd ./build/wayvnc | grep -Eq 'libneatvnc|libaml|not found'; then
    die "wayvnc-upstream: binary still links neatvnc/aml dynamically or misses a library: $(ldd ./build/wayvnc | grep -E 'libneatvnc|libaml|not found')"
fi

install -d -m 0755 "$OUT_DIR"
install -m 0755 ./build/wayvnc "$OUT_DIR/wayvnc.new"
mv -f "$OUT_DIR/wayvnc.new" "$OUT_DIR/wayvnc"
# Build output, not SSOT: the commits this binary was built from.
python3 -I - "$OUT_DIR/build.json" "$VERSION" <<PY
import json, os, sys, time
rec = {
    "version": sys.argv[2],
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "wayvnc": {"repo": "${REPO[wayvnc]}", "commit": "${SHA[wayvnc]}"},
    "neatvnc": {"repo": "${REPO[neatvnc]}", "commit": "${SHA[neatvnc]}"},
    "aml": {"repo": "${REPO[aml]}", "commit": "${SHA[aml]}"},
}
tmp = sys.argv[1] + ".new"
with open(tmp, "w") as f:
    json.dump(rec, f, indent=2)
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())
os.chmod(tmp, 0o644)
os.replace(tmp, sys.argv[1])
PY
if command -v record_version >/dev/null 2>&1; then
    record_version wayvnc-upstream "$VERSION" "${REPO[wayvnc]%.git}/commit/${SHA[wayvnc]}" || true
fi
mios_ok "wayvnc-upstream $VERSION -> $OUT_DIR/wayvnc (wayvnc ${SHA[wayvnc]}, neatvnc ${SHA[neatvnc]}, aml ${SHA[aml]})"
cd /
rm -rf "$BUILD_DIR"
