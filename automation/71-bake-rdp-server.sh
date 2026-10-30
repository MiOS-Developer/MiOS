#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Builds the MiOS native RDP server (src/mios-rdp-server: mios-rdp-server + mios-rdp-priv) against IronRDP's default-branch HEAD, resolved now and never pinned (the resolved commit, the generated Cargo.lock hash and the enabled features are build output in /usr/libexec/mios/rdp-native/build.json and the SBOM), and installs it to /usr/libexec/mios/rdp-native/. Documented static-policy exceptions (rdp-native-design.md 12): both binaries are x86_64-unknown-linux-gnu, dynamically linked (E1 mios-rdp-server: libEGL/libgbm/libxkbcommon + dlopen of the Cisco libopenh264; E2 mios-rdp-priv: libpam). If the build fails nothing is installed (image) or the last good binary stays (MIOS_RDP_DESTDIR host/dev builds), and [remote.rdp].server = "native" falls back to xrdp.
# AI-related: /usr/libexec/mios/mios-rdp, /usr/lib/systemd/user/mios-rdp-native.service, /usr/lib/systemd/system/mios-rdp-priv.socket, /usr/share/mios/mios.toml [remote.rdp] [remote.rdp.native] [packages.rdp-server-build], src/mios-rdp-server/
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
umask 0022
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh" 2>/dev/null || true
source "${SCRIPT_DIR}/lib/packages.sh" 2>/dev/null || true
# shellcheck source=automation/lib/upstream-latest.sh
source "${SCRIPT_DIR}/lib/upstream-latest.sh" 2>/dev/null || true
command -v mios_log >/dev/null || mios_log() { printf '[71-bake-rdp-server] %s\n' "$*" >&2; }
command -v mios_ok >/dev/null || mios_ok() { mios_log "OK: $*"; }
command -v mios_err >/dev/null || mios_err() { mios_log "ERROR: $*"; }

get() { MIOS_TOML_ROOT="$ROOT_DIR" python3 "$ROOT_DIR/usr/libexec/mios/mios-toml-get" --vendor "$@"; }

DESTDIR="${MIOS_RDP_DESTDIR:-}"
OUT_DIR="$DESTDIR/usr/libexec/mios/rdp-native"
BUILD_DIR="${MIOS_RDP_BUILD_DIR:-/tmp/mios-rdp-server-build}"
URL="${MIOS_URL_IRONRDP:-https://github.com/Devolutions/IronRDP.git}"
SRC="$ROOT_DIR/src/mios-rdp-server"
[[ -f "$SRC/Cargo.toml" ]] || { mios_err "src/mios-rdp-server missing"; exit 1; }

# "latest" floats with upstream: IronRDP's default-branch HEAD, resolved now, never pinned.
SHA="$(git ls-remote "$URL" HEAD | awk '{print $1; exit}')" || true
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { mios_err "IronRDP HEAD of $URL could not be resolved"; exit 1; }
SRC_HASH="$(cd "$SRC" && find . -type f \( -name '*.rs' -o -name '*.toml' \) -not -path './target/*' -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
mios_log "IronRDP HEAD resolves to $SHA; MiOS source ${SRC_HASH:0:12}"

if [[ "${MIOS_RDP_FORCE:-0}" != 1 && -x "$OUT_DIR/mios-rdp-server" && -r "$OUT_DIR/build.json" ]] \
   && python3 -I - "$OUT_DIR/build.json" "$SHA" "$SRC_HASH" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d.get("ironrdp", {}).get("commit") == sys.argv[2] and d.get("source_sha256") == sys.argv[3] else 1)
PY
then
    mios_ok "mios-rdp-server already built from IronRDP ${SHA:0:12} / source ${SRC_HASH:0:12}"
    exit 0
fi

if [[ "${MIOS_RDP_SKIP_DEPS:-0}" != 1 ]] && command -v install_packages >/dev/null; then
    install_packages "rdp-server-build" || mios_log "rdp-server-build packages not installable here; using what is present"
fi

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Hermetic linker stubs for dynamic dependencies when -devel packages are absent
STUB_DIR="$BUILD_DIR/linker-stubs"
mkdir -p "$STUB_DIR"
if [[ ! -e /usr/lib64/libgbm.so && -e /usr/lib64/libgbm.so.1 ]]; then
    ln -sf /usr/lib64/libgbm.so.1 "$STUB_DIR/libgbm.so"
fi
if [[ ! -e /usr/lib64/libpam.so && -e /usr/lib64/libpam.so.0 ]]; then
    ln -sf /usr/lib64/libpam.so.0 "$STUB_DIR/libpam.so"
fi
if ! pkg-config --exists gbm 2>/dev/null && [[ -e /usr/lib64/libgbm.so.1 ]]; then
    mkdir -p "$BUILD_DIR/pkgconfig"
    cat << 'EOF_PC' > "$BUILD_DIR/pkgconfig/gbm.pc"
prefix=/usr
libdir=/usr/lib64
includedir=/usr/include

Name: gbm
Description: Generic Buffer Management
Version: 26.2.4
Libs: -L${libdir} -lgbm
Cflags: -I${includedir}
EOF_PC
    export PKG_CONFIG_PATH="${BUILD_DIR}/pkgconfig:${PKG_CONFIG_PATH:-}"
fi

MISSING=""
for tool in git pkg-config cc; do command -v "$tool" >/dev/null 2>&1 || MISSING="${MISSING}${tool} "; done
for pc in egl gbm xkbcommon; do pkg-config --exists "$pc" 2>/dev/null || MISSING="${MISSING}${pc}.pc "; done
[[ -z "$MISSING" ]] || { mios_err "missing build dependencies: $MISSING"; exit 1; }

export RUSTUP_HOME="${RUSTUP_HOME:-$(get build.toolchain rustup_home 2>/dev/null || echo /usr/lib/mios/rustup)}"
export CARGO_HOME="${CARGO_HOME:-$(get build.toolchain cargo_home 2>/dev/null || echo /usr/lib/mios/cargo)}"
if [[ ! -d "${CARGO_HOME}" && -d /root/.cargo ]]; then
    export CARGO_HOME=/root/.cargo
elif [[ ! -d "${CARGO_HOME}" && -d /home/mios/.cargo ]]; then
    export CARGO_HOME=/home/mios/.cargo
fi
if [[ ! -d "${RUSTUP_HOME}" && -d /root/.rustup ]]; then
    export RUSTUP_HOME=/root/.rustup
elif [[ ! -d "${RUSTUP_HOME}" && -d /home/mios/.rustup ]]; then
    export RUSTUP_HOME=/home/mios/.rustup
fi
export PATH="${CARGO_HOME}/bin:${PATH}"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-$(get build.toolchain channel 2>/dev/null || echo stable)}"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}"
export RUSTFLAGS="-L ${STUB_DIR} ${RUSTFLAGS:-}"
TARGET=x86_64-unknown-linux-gnu

if [[ -d /var/lib/mios-build/rdp-native/ironrdp/.git ]] \
   && [[ "$(git -C /var/lib/mios-build/rdp-native/ironrdp rev-parse HEAD 2>/dev/null || true)" == "$SHA" ]] \
   && [[ -z "$(git -C /var/lib/mios-build/rdp-native/ironrdp status --porcelain 2>/dev/null || echo dirty)" ]]; then
    cp -a /var/lib/mios-build/rdp-native/ironrdp "$BUILD_DIR/ironrdp"
else
    git init -q "$BUILD_DIR/ironrdp"
    git -C "$BUILD_DIR/ironrdp" fetch -q --depth 1 "$URL" "$SHA"
    git -C "$BUILD_DIR/ironrdp" checkout -q FETCH_HEAD
fi
[[ "$(git -C "$BUILD_DIR/ironrdp" rev-parse HEAD)" == "$SHA" ]] || { mios_err "IronRDP checkout is not $SHA"; exit 1; }
cp -a "$SRC" "$BUILD_DIR/src"
rm -rf "$BUILD_DIR/src/target" "$BUILD_DIR/src/Cargo.lock"
if [[ -z "$DESTDIR" ]]; then
    mkdir -p /var/lib/mios-build/rdp-native
    rm -rf /var/lib/mios-build/rdp-native/ironrdp
    cp -a "$BUILD_DIR/ironrdp" /var/lib/mios-build/rdp-native/ironrdp
    chown -R 1000:1000 /var/lib/mios-build/rdp-native/ironrdp 2>/dev/null || true
    chmod -R u+rwX,go+rX /var/lib/mios-build/rdp-native/ironrdp
    ln -sfn /var/lib/mios-build/rdp-native/ironrdp "$ROOT_DIR/src/ironrdp"
fi

# Compile the compatibility preparer as a native static build tool. This runs
# before Cargo sees the dependency; build.rs would run too late for that.
PREP_TARGET="$(get build.native.linux.targets "$(uname -m)")"
[[ -n "$PREP_TARGET" ]] || { mios_err "native static Rust target missing from SSOT"; exit 1; }
rustc --edition=2024 --target="$PREP_TARGET" -O -C codegen-units=1 "$SRC/prepare.rs" -o "$BUILD_DIR/mios-rdp-source-prepare"
rustc --edition=2024 --target="$PREP_TARGET" --test "$SRC/prepare.rs" -o "$BUILD_DIR/mios-rdp-source-prepare-tests"
"$BUILD_DIR/mios-rdp-source-prepare-tests"
"$BUILD_DIR/mios-rdp-source-prepare" "$BUILD_DIR/ironrdp" > "$BUILD_DIR/source-preparation.json"

(cd "$BUILD_DIR/src" && cargo build --release --target "$TARGET" -p mios-rdp-server -p mios-rdp-priv)
(cd "$BUILD_DIR/src" && cargo test --release --target "$TARGET" -p mios-rdp-server -p mios-rdp-priv)
BIN="$BUILD_DIR/src/target/$TARGET/release"
LOCK_HASH="$(sha256sum "$BUILD_DIR/src/Cargo.lock" | awk '{print $1}')"
SELF="$("$BIN/mios-rdp-server" --self-test 2>/dev/null | python3 -I -c 'import json,sys
d = json.load(sys.stdin)
print(json.dumps({"openh264": d.get("openh264", {}).get("ok"), "egl": d.get("egl", {}).get("ok")}))' 2>/dev/null || echo '{}')"

install -d -m 0755 "$OUT_DIR"
install -m 0755 "$BIN/mios-rdp-server" "$OUT_DIR/mios-rdp-server.new"
install -m 0755 "$BIN/mios-rdp-priv" "$OUT_DIR/mios-rdp-priv.new"
mv -f "$OUT_DIR/mios-rdp-server.new" "$OUT_DIR/mios-rdp-server"
mv -f "$OUT_DIR/mios-rdp-priv.new" "$OUT_DIR/mios-rdp-priv"

# Install canonical tracked PAM file
install -d -m 0755 "${DESTDIR}/usr/lib/pam.d"
if [[ -f "${ROOT_DIR}/usr/lib/pam.d/mios-rdp" ]]; then
    pam_src="$(realpath -m "${ROOT_DIR}/usr/lib/pam.d/mios-rdp")"
    pam_dst="$(realpath -m "${DESTDIR}/usr/lib/pam.d/mios-rdp")"
    if [[ "$pam_dst" != "$pam_src" ]]; then
        install -m 0644 "${pam_src}" "${pam_dst}"
    else
        chmod 0644 "${pam_src}"
    fi
    install -d -m 0755 "${DESTDIR}/etc/pam.d"
    pam_etc="$(realpath -m "${DESTDIR}/etc/pam.d/mios-rdp")"
    install -m 0644 "${pam_src}" "${pam_etc}"
fi

python3 -I - "$OUT_DIR/build.json" "$URL" "$SHA" "$SRC_HASH" "$LOCK_HASH" "$SELF" "$("$BIN/mios-rdp-server" --version 2>/dev/null || echo mios-rdp-server)" "$OUT_DIR/mios-rdp-server" "$OUT_DIR/mios-rdp-priv" "$BUILD_DIR/source-preparation.json" <<'PY'
import json, os, sys, time, hashlib
out, url, sha, src, lock, selftest, ver, srv_path, priv_path, preparation = sys.argv[1:11]

def file_sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

rec = {
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "version": ver,
    "ironrdp": {"repo": url, "commit": sha},
    "source_sha256": src,
    "source_preparation": json.load(open(preparation)),
    "cargo_lock_sha256": lock,
    "target": "x86_64-unknown-linux-gnu",
    "features": ["egfx", "helper", "nscodec"],
    "static_policy_exceptions": ["E1 mios-rdp-server glibc dynamic (EGL, GBM, xkbcommon, dlopen libopenh264)",
                                 "E2 mios-rdp-priv glibc + libpam"],
    "self_test": json.loads(selftest or "{}"),
    "binaries": {
        "mios-rdp-server": file_sha256(srv_path),
        "mios-rdp-priv": file_sha256(priv_path),
    },
}
tmp = out + ".new"
with open(tmp, "w") as f:
    json.dump(rec, f, indent=2)
    f.write("\n")
    f.flush()
    os.fsync(f.fileno())
os.chmod(tmp, 0o644)
os.replace(tmp, out)
PY
if command -v mios_record_resolved >/dev/null 2>&1; then
    mios_record_resolved mios-rdp-server "ironrdp-${SHA:0:12}" "${URL%.git}/commit/${SHA}" "$(sha256sum "$OUT_DIR/mios-rdp-server" | awk '{print $1}')" || true
fi
mios_ok "mios-rdp-server -> $OUT_DIR (IronRDP $SHA, self-test $SELF)"
cd /
rm -rf "$BUILD_DIR"
