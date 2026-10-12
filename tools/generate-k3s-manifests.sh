#!/usr/bin/env bash
# AI-hint: Generate k3s/k8s manifests from the live MiOS pods (pods-as-SSOT, WS-7 #61).
# AI-related: usr/share/mios/k3s, usr/share/containers/systemd, usr/share/mios/mios.toml, tools/native/mios-gen/src/ai.rs
# AI-functions: _emit_header, main
set -euo pipefail

ROOT="${MIOS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_DIR="${MIOS_K3S_OUT:-$ROOT/usr/share/mios/k3s/generated}"
NAME_FILTER="${MIOS_K3S_FILTER:-^mios-}"
PODMAN="${PODMAN:-podman}"

_emit_header() {
    local name="$1"
    printf '# AI-hint: GENERATED k3s/k8s manifest for the MiOS %s pod (pods-as-SSOT, WS-7). DO NOT EDIT -- regenerate via tools/generate-k3s-manifests.sh. Inert until the [k3s] lane is enabled; host-net/GPU/bind-mount services need k3s adaptation first.\n' "$name"
    printf '# AI-related: tools/generate-k3s-manifests.sh, %s.container, usr/share/mios/k3s/README.md\n' "$name"
}

main() {
    if ! "$PODMAN" kube generate --help >/dev/null 2>&1; then
        echo "[generate-k3s] podman kube generate unavailable" >&2
        return 1
    fi
    mkdir -p "$OUT_DIR"
    local pods=() standalone=() targets=()
    mapfile -t pods < <("$PODMAN" pod ps --format '{{.Name}}' 2>/dev/null \
        | grep -E "$NAME_FILTER" | sort -u || true)
    mapfile -t standalone < <("$PODMAN" ps -a --format '{{.Names}}|{{.Pod}}' 2>/dev/null \
        | grep -E "$NAME_FILTER" | grep -E '\|$' | sed 's/|$//' | sort -u || true)
    targets=("${pods[@]}" "${standalone[@]}")
    if [[ "${#targets[@]}" -eq 0 ]]; then
        echo "[generate-k3s] no pods/containers match $NAME_FILTER" >&2
        return 0
    fi
    local n out raw generated=0
    for n in "${targets[@]}"; do
        [[ -n "$n" ]] || continue
        out="$OUT_DIR/${n}.yaml"
        raw="$("$PODMAN" kube generate "$n" 2>/dev/null || true)"
        if printf '%s\n' "$raw" | grep -qE '^apiVersion:'; then
            {
                _emit_header "$n"
                printf '%s\n' "$raw" \
                    | grep -vE '^[[:space:]]*creationTimestamp:' \
                    | grep -vE '^[[:space:]]*bind-mount-options:' \
                    | grep -vE '^# Created with podman' || true
            } > "$out"
            generated=$((generated + 1))
            echo "[generate-k3s]   $out"
        else
            echo "[generate-k3s]   SKIP $n" >&2
        fi
    done
    echo "[generate-k3s] wrote $generated manifest to $OUT_DIR"
    return 0
}

main "$@"
