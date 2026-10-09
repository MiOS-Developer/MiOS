#!/usr/bin/env bash
# AI-hint: Bootstrap the Rust management binary; the SSOT native compile/install engine is miosd native-build. --toolchain provisions the image's own SSOT Rust toolchain instead.
# AI-related: src/mios-rs/mios-build/src/native_build.rs, usr/share/mios/mios.toml, Containerfile
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --toolchain: provision the SSOT Rust toolchain system-wide and stop, so every
# MiOS image can rebuild its native catalog. The Containerfile runs it after
# [packages.self-build] (Fedora's rustup RPM ships rustup-init only). Installs the
# [build.toolchain] channel and components with this machine's
# [build.native.linux] target into [build.toolchain].rustup_home/cargo_home, then
# proves the target's std and the linker are there; any gap fails the build.
if [[ "${1:-}" == --toolchain ]]; then
    fail() { echo "[55-native-build] ERROR: $*" >&2; exit 1; }
    get() { MIOS_TOML_ROOT="$ROOT_DIR" python3 "$ROOT_DIR/usr/libexec/mios/mios-toml-get" --vendor "$@"; }
    channel="$(get build.toolchain channel)"
    target="$(get build.native.linux.targets "$(uname -m)")"
    linker="$(get build.native.linux linker)"
    RUSTUP_HOME="$(get build.toolchain rustup_home)"
    CARGO_HOME="$(get build.toolchain cargo_home)"
    [[ -n "$channel" && -n "$target" && -n "$linker" ]] \
        || fail "[build.toolchain].channel, [build.native.linux].targets.$(uname -m) or .linker is empty"
    [[ "$RUSTUP_HOME" == /* && "$CARGO_HOME" == /* ]] \
        || fail "[build.toolchain].rustup_home and .cargo_home must be absolute paths"
    export RUSTUP_HOME CARGO_HOME
    set --
    for c in $(get build.toolchain components | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)))'); do
        set -- "$@" -c "$c"
    done
    [[ $# -gt 0 ]] || fail "[build.toolchain].components is empty"
    command -v rustup-init >/dev/null 2>&1 || fail "rustup-init is missing; [packages.build-toolchain] provides it"
    rustup-init -y --no-modify-path --profile minimal --default-toolchain "$channel" --target "$target" "$@"
    rustc="$CARGO_HOME/bin/rustc"
    libdir="$("$rustc" --print target-libdir --target "$target")"
    compgen -G "$libdir/libstd-*.rlib" >/dev/null || fail "no ${target} standard library under ${libdir}"
    host="$("$rustc" -vV | sed -n 's/^host: //p')"
    [[ -x "$("$rustc" --print sysroot)/lib/rustlib/${host}/bin/${linker}" ]] \
        || fail "linker ${linker} missing from the ${channel} toolchain"
    # Readable by every user; only root updates the toolchain.
    chmod -R a+rX "$RUSTUP_HOME" "$CARGO_HOME"
    echo "[55-native-build] ${channel} toolchain with ${target} and ${linker} in ${RUSTUP_HOME}"
    exit 0
fi

if [[ ! -f "$ROOT_DIR/src/mios-rs/Cargo.toml" ]]; then
    exec /usr/libexec/mios/miosd native-runtime-check --root "$ROOT_DIR" \
        --bin-dir "${MIOS_NATIVE_DEST_DIR:-/usr/libexec/mios}"
fi
if [[ -n "${MIOS_NATIVE_DEST_DIR:-}" ]]; then
    echo '[55-native-build] Use MIOS_NATIVE_INSTALL_ROOT for an SSOT FHS installation root.' >&2
    exit 1
fi
target_dir="${CARGO_TARGET_DIR:-$ROOT_DIR/tools/native/target}"
mkdir -p "$target_dir"
target_dir="$(cd "$target_dir" && pwd)"
host="$(rustc -vV | sed -n 's/^host: //p')"
[[ -n "$host" ]] || { echo '[55-native-build] Rust host target unavailable' >&2; exit 1; }
# This temporary compiler-host management binary is not a shipped artifact.
# The native engine builds, lints and verifies every shipped static binary.
(cd "$ROOT_DIR/src/mios-rs" && RUSTFLAGS='' CARGO_ENCODED_RUSTFLAGS='' \
    cargo build --release --locked -p miosd --target "$host" --target-dir "$target_dir")
# Fedora's rustup RPM installs rustup-init, not the rustup proxy. Keep the
# bootstrap compiler on its existing PATH; initialize proxies only after it
# has produced the management binary. That binary selects the SSOT channel.
if ! command -v rustup >/dev/null 2>&1; then
    command -v rustup-init >/dev/null 2>&1 || { echo '[55-native-build] Install the SSOT rustup package (rustup-init is missing).' >&2; exit 1; }
    rustup-init -y --no-modify-path --profile minimal --default-toolchain none
    export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
    command -v rustup >/dev/null 2>&1 || { echo '[55-native-build] Rustup initialization did not provide its proxy.' >&2; exit 1; }
fi
prefix="${MIOS_NATIVE_INSTALL_ROOT:-}"
[[ -n "$prefix" ]] || { if [[ "$EUID" -eq 0 ]]; then prefix=/; else prefix="$ROOT_DIR"; fi; }
exec "$target_dir/$host/release/miosd" native-build --root "$ROOT_DIR" \
    --target-dir "$target_dir" --install-root "$prefix"
