#!/bin/bash
# AI-hint: Fetch the pinned [keybindings.tmux.desktop.source] tarball, build the patched MiOS desktop tmux through `mios-gen build-tmux-desktop` (which verifies the digest, the patch and an admitted build context), and install the binary with its build receipt under an install root. The image bake (59-tools.sh) and CI's drift-gate job both run it.
# AI-related: usr/share/mios/mios.toml [keybindings.tmux.desktop.source], tools/native/mios-gen/src/tmux_desktop_build.rs, automation/59-tools.sh, .github/workflows/mios-ci.yml
# AI-functions: main

set -euo pipefail

main() {
    local root="${1:?usage: tmux-desktop.sh <source-root> <install-root>}"
    local dest="${2:?usage: tmux-desktop.sh <source-root> <install-root>}"
    root="$(cd "$root" && pwd)"
    local getter="$root/usr/libexec/mios/mios-toml-get"
    local gen
    gen="$(command -v mios-gen || echo /usr/libexec/mios/mios-gen)"
    [[ -x "$getter" && -x "$gen" ]] || { echo "[tmux-desktop] mios-toml-get and mios-gen are required" >&2; return 1; }
    local url binary
    url="$(MIOS_TOML_ROOT="$root" "$getter" --vendor keybindings.tmux.desktop.source url)"
    binary="$(MIOS_TOML_ROOT="$root" "$getter" --vendor keybindings.tmux.desktop binary)"
    [[ "$url" == https://* && "$binary" == /* ]] || { echo "[tmux-desktop] source url or binary is not set" >&2; return 1; }
    local work
    work="$(mktemp -d)"
    # shellcheck disable=SC2064  # expand now: the trap must remove this run's directory
    trap "rm -rf -- '$work'" EXIT
    chmod 0700 "$work"
    curl --fail --silent --show-error --location --proto '=https' --output "$work/source.tar.gz" "$url"
    install -d -m 0700 "$work/root"
    "$gen" build-tmux-desktop --root "$root" --source-archive "$work/source.tar.gz" \
        --build-dir "$work/build" --install-root "$work/root"
    install -D -m 0755 "$work/root$binary" "${dest%/}$binary"
    install -D -m 0644 "$work/root$binary.build.json" "${dest%/}$binary.build.json"
    echo "[tmux-desktop] installed ${dest%/}$binary ($("${dest%/}$binary" -V))"
}

main "$@"
