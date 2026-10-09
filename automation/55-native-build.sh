#!/usr/bin/env bash
# AI-hint: Builds and installs native executables from the SSOT role catalog through miosd native-targets; preserves separate CLI, app, service and daemon categories.
# AI-related: tools/native/Cargo.toml, src/mios-rs/Cargo.toml, automation/85-bake-plan.sh, /usr/libexec/mios/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEST_DIR="${MIOS_NATIVE_DEST_DIR:-/usr/libexec/mios}"
if [[ "${EUID}" -ne 0 && -z "${MIOS_NATIVE_DEST_DIR:-}" ]]; then
    DEST_DIR="${ROOT_DIR}/usr/libexec/mios"
fi

# The image bake reuses the rust-builder artifacts; build.sh excludes this phase.
# A direct invocation from an incomplete source context requires prebuilt tools.
if [[ ! -f "${ROOT_DIR}/src/mios-rs/Cargo.toml" ]]; then
    [[ -x "${DEST_DIR}/miosd" ]] || {
        echo "[55-native-build] FATAL: incomplete source context and missing prebuilt ${DEST_DIR}/miosd" >&2
        exit 1
    }
    "${DEST_DIR}/miosd" native-runtime-check --root "$ROOT_DIR" --platform linux --bin-dir "$DEST_DIR"
    echo "[55-native-build] Image bake uses the required prebuilt native tools."
    exit 0
fi

mkdir -p "${DEST_DIR}"

if command -v cargo >/dev/null 2>&1; then
    # A caller's CARGO_TARGET_DIR must not cause installation to read stale
    # workspace artifacts. Build and install from an explicit common output.
    TARGET_DIR="${ROOT_DIR}/tools/native/target"
    # Bootstrap the existing Rust management program, then let its shared build
    # library validate Cargo's executable inventory against the role catalog.
    host="$(rustc -vV | sed -n 's/^host: //p')"
    [[ -n "$host" ]] || { echo "[55-native-build] FATAL: Rust host target unavailable" >&2; exit 1; }
    (cd "${ROOT_DIR}/src/mios-rs" && RUSTFLAGS='' cargo build --release --locked -p miosd --target "$host" --target-dir "$TARGET_DIR")
    builder="${TARGET_DIR}/${host}/release/miosd"
    [[ -x "$builder" ]] || { echo "[55-native-build] FATAL: native catalog builder missing" >&2; exit 1; }
    # A component declaration is not evidence that the builder can lint.
    # This Rust verifier consumes the SSOT and fails if Clippy/rustfmt is absent.
    "$builder" native-toolchain-check --root "$ROOT_DIR" --lint
    arch="$(uname -m)"
    settings="$("$builder" native-build-settings --root "$ROOT_DIR" --arch "$arch")"
    IFS=$'\t' read -r target linker rust_flags jobs <<< "$settings"
    [[ -n "$target" && -n "$linker" && -n "$rust_flags" && "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "[55-native-build] FATAL: incomplete native build policy" >&2; exit 1; }
    export CARGO_BUILD_JOBS="$jobs"
    libdir="$(rustc --print target-libdir --target "$target")"
    if [[ ! -d "$libdir" ]] || ! compgen -G "$libdir/libstd-*.rlib" >/dev/null; then
        if command -v rustup >/dev/null 2>&1; then rustup target add "$target"
        else echo "[55-native-build] FATAL: missing Rust target standard library ${target}; provision the SSOT toolchain" >&2; exit 1; fi
    fi
    [[ -d "$libdir" ]] && compgen -G "$libdir/libstd-*.rlib" >/dev/null || { echo "[55-native-build] FATAL: missing target standard library ${target}" >&2; exit 1; }
    linker_path="$(rustc --print sysroot)/lib/rustlib/${host}/bin/${linker}"
    [[ -x "$linker_path" ]] || { echo "[55-native-build] FATAL: selected linker ${linker} unavailable" >&2; exit 1; }
    export RUSTFLAGS="${rust_flags} -C linker=${linker_path}"
    plan="$("$builder" native-targets --root "$ROOT_DIR" --platform linux)"
    [[ -n "$plan" ]] || { echo "[55-native-build] FATAL: native catalog selected no executables" >&2; exit 1; }
    while IFS=$'\t' read -r workspace package bin category install_dir expose_bin compat_dirs; do
            echo "[55-native-build] Compiling ${category}: ${bin}..."
            (cd "${ROOT_DIR}/${workspace}" && cargo build --release --locked -p "$package" --bin "$bin" --target "$target" --target-dir "$TARGET_DIR")
            SRC_BIN="${TARGET_DIR}/${target}/release/${bin}"
            [[ -f "$SRC_BIN" && -x "$SRC_BIN" ]] || { echo "[55-native-build] FATAL: build did not produce ${SRC_BIN}" >&2; exit 1; }
            "$builder" native-artifact-check "$SRC_BIN" --arch "$arch" --root "$ROOT_DIR"
            prefix="${MIOS_NATIVE_INSTALL_ROOT:-}"
            [[ -n "$prefix" || "$EUID" -eq 0 ]] || prefix="$ROOT_DIR"
            if [[ -n "${MIOS_NATIVE_DEST_DIR:-}" ]]; then destination="$DEST_DIR"
            else destination="${prefix}${install_dir}"; fi
            mkdir -p "$destination"
            echo "[55-native-build] Installing ${category}: ${bin} to ${destination}..."
            # Replace an old symlink itself rather than following it. Otherwise
            # reversing the canonical and compatibility paths creates a cycle.
            staged="$(mktemp "${destination}/.${bin}.XXXXXX")"
            if ! install -m 0755 "$SRC_BIN" "$staged" || ! mv -fT "$staged" "${destination}/${bin}"; then
                rm -f "$staged"
                echo "[55-native-build] FATAL: cannot install ${bin}" >&2
                exit 1
            fi
            if [[ -z "${MIOS_NATIVE_DEST_DIR:-}" ]]; then
                aliases=(); [[ "$compat_dirs" == - ]] || IFS=',' read -ra aliases <<< "$compat_dirs"
                [[ "$expose_bin" != true ]] || aliases+=(/usr/bin)
                for alias in "${aliases[@]}"; do
                    [[ "${prefix}${alias}" != "$destination" ]] || continue
                    mkdir -p "${prefix}${alias}"
                    # Staging roots never appear in a deployed link target.
                    link_target="${install_dir}/${bin}"
                    [[ -n "${MIOS_NATIVE_INSTALL_ROOT:-}" || "$EUID" -eq 0 ]] || link_target="${destination}/${bin}"
                    ln -sfT "$link_target" "${prefix}${alias}/${bin}"
                done
            fi
    done <<< "$plan"
else
    echo "[55-native-build] FATAL: selected self-build dependency closure did not provide Cargo." >&2
    exit 1
fi
