#!/usr/bin/env bash
# AI-hint: bash Unified builder script to build localhost/mios-sys and localhost/mios-cuda shared-base images into the additional containers-storage r...
# AI-doc: usr/share/doc/mios/manual/mios.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIOS_TOML="${MIOS_TOML:-/usr/share/mios/mios.toml}"
export MIOS_VENDOR_TOML="${MIOS_VENDOR_TOML:-$MIOS_TOML}"
STORE="${STORE:-/usr/lib/containers/storage}"
SCRATCH="${SCRATCH:-/var/tmp/mios-bakescratch}"

if [[ -f "/usr/lib/mios/paths.sh" ]]; then
    source "/usr/lib/mios/paths.sh"
else
    source "${SCRIPT_DIR}/../../lib/mios/paths.sh"
fi

log() { printf '[57-mios-sys-build] %s\n' "$*"; }

if [ "${MIOS_BAKE_BOUND_IMAGES:-1}" != "1" ]; then
    log "SKIP bound-images bake for mios-base, mios-sys, mios-cuda and mios-piper"
    exit 0
fi

BASE="${MIOS_BASE_IMAGE:-registry.fedoraproject.org/fedora-minimal:latest}"
SERVICE_BASE="${MIOS_SERVICE_BASE_IMAGE:-localhost/mios-base:latest}"

log "Base image configured: $BASE"
log "Service base image configured: $SERVICE_BASE"
log "Target storage root: $STORE"

# Resolve all service build inputs once, in Rust, before any image mutation.
_resolver="${MIOS_RESOLVER_BIN:-/usr/bin/mios-resolver}"
[[ -x "$_resolver" ]] || _resolver=/usr/libexec/mios/mios-resolver
_inputs="$("$_resolver" --emit=build-shell)" || { log "ERROR: native service build SSOT resolution failed"; exit 1; }
# TD-1: eval-safe, input=mios-resolver --emit=build-shell, not agent-controlled
eval "$_inputs"

install -d -m 0700 "$SCRATCH"
install -d -m 0700 "$SCRATCH/tmp" "$SCRATCH/run"
CONF="$SCRATCH/storage.conf"
cat > "$CONF" <<'SC'
[storage]
driver = "overlay"
[storage.options]
[storage.options.overlay]
mountopt = "nodev"
[storage.options.pull_options]
enable_partial_images = "true"
convert_images = "true"
use_hard_links = "true"
SC

REG_CONF="$SCRATCH/registries.conf"
cat > "$REG_CONF" <<'RC'
short-name-mode = "permissive"
[[registry]]
location = "docker.io"
[[registry.mirror]]
location = "mirror.gcr.io"
RC


build_image_with_retry() {
    local target_tag="$1"
    local build_dir="$2"
    shift 2
    local attempt=1
    local max_attempts=3
    local backoff=5
    local success=0

    while [[ $attempt -le $max_attempts ]]; do
        log "Attempt $attempt/$max_attempts: Building $target_tag"
        if CONTAINERS_STORAGE_CONF="$CONF" CONTAINERS_REGISTRIES_CONF="$REG_CONF" TMPDIR="$SCRATCH/tmp" \
          podman --root "$STORE" --runroot "$SCRATCH/run" build \
          --network=host \
          --cap-add all \
          --security-opt seccomp=unconfined \
          --security-opt apparmor=unconfined \
          --layers \
          -t "$target_tag" \
          "$@" \
          "$build_dir"; then
            success=1
            break
        else
            log "WARNING: Build $target_tag failed on attempt $attempt/$max_attempts"
            if [[ $attempt -lt $max_attempts ]]; then
                log "Backing off $backoff seconds before retry"
                sleep "$backoff"
                backoff=$((backoff * 2))
            fi
        fi
        attempt=$((attempt + 1))
    done

    if [[ $success -ne 1 ]]; then
        log "ERROR: Persistent build failure for $target_tag after $max_attempts attempts"
        exit 1
    fi

    if ! CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" image exists "$target_tag"; then
        log "ERROR: Image verification failed: $target_tag does not exist after build"
        exit 1
    fi
    log "Image $target_tag verified successfully"
}

log "Building localhost/mios-base"
build_image_with_retry "localhost/mios-base:latest" "/usr/share/mios/base" \
  --build-arg MIOS_BASE_IMAGE="$BASE" \
  --build-arg "MIOS_MCP_PACKAGES=${MIOS_MCP_PACKAGES:?SSOT packages unresolved}" --build-context mios=/

if [[ "${1:-}" == "--base-only" ]]; then
    exit 0
fi

log "Building localhost/mios-sys"
build_image_with_retry "localhost/mios-sys" "/usr/share/mios/sys" \
  --build-arg BASE_IMAGE="$SERVICE_BASE" \
  --build-arg SEARXNG_REF="$SEARXNG_REF"

log "Building localhost/mios-cuda"
build_image_with_retry "localhost/mios-cuda" "/usr/share/mios/cuda" \
  --build-arg BASE_IMAGE="$SERVICE_BASE"

# localhost/mios-piper bakes the [services.piper] voice (Law 12); every build
# arg comes from the resolver, and an empty one fails the bake.
log "Building localhost/mios-piper"
_piper_args=()
for _k in MIOS_SERVICES_PIPER_BASE MIOS_SERVICES_PIPER_VERSION MIOS_SERVICES_PIPER_VOICE MIOS_SERVICES_PIPER_UID MIOS_SERVICES_PIPER_GID; do
    _v="${!_k}"
    if [[ -z "$_v" ]]; then
        log "ERROR: ${_k} resolved empty; set [services.piper] in mios.toml"
        exit 1
    fi
    _piper_args+=(--build-arg "${_k}=${_v}")
done
build_image_with_retry "localhost/mios-piper:latest" "/usr/share/mios/piper" "${_piper_args[@]}"

SBOM_DIR="${SBOM_DIR:-/usr/share/mios/artifacts/sbom}"
_base_digest="$(CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" image inspect localhost/mios-base:latest --format '{{.Digest}}' 2>/dev/null || echo "Local")"
_sys_digest="$(CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" image inspect localhost/mios-sys --format '{{.Digest}}' 2>/dev/null || echo "Local")"
_cuda_digest="$(CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" image inspect localhost/mios-cuda --format '{{.Digest}}' 2>/dev/null || echo "Local")"
_piper_digest="$(CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" image inspect localhost/mios-piper:latest --format '{{.Digest}}' 2>/dev/null || echo "Local")"
install -d -m 0755 "$SBOM_DIR"
printf '%s\t%s\t%s\n' "localhost/mios-base:latest" "${_base_digest:-local}" "base" >> "$SBOM_DIR/bound-images.tsv"
printf '%s\t%s\t%s\n' "localhost/mios-sys:latest" "${_sys_digest:-local}" "sys" >> "$SBOM_DIR/bound-images.tsv"
printf '%s\t%s\t%s\n' "localhost/mios-cuda:latest" "${_cuda_digest:-local}" "cuda" >> "$SBOM_DIR/bound-images.tsv"
printf '%s\t%s\t%s\n' "localhost/mios-piper:latest" "${_piper_digest:-local}" "sys" >> "$SBOM_DIR/bound-images.tsv"

log "Pruning build-stage images from ${STORE}"
while read -r _img; do
    case "$_img" in
        localhost/mios-base:latest|localhost/mios-base|localhost/mios-sys:latest|localhost/mios-sys|localhost/mios-cuda:latest|localhost/mios-cuda|localhost/mios-piper:latest|localhost/mios-piper|"<none>:<none>") continue ;;
    esac
    CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" --runroot "$SCRATCH/run" rmi -f "$_img" >/dev/null 2>&1 || true
done < <(CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | sort -u)
CONTAINERS_STORAGE_CONF="$CONF" podman --root "$STORE" --runroot "$SCRATCH/run" image prune -f >/dev/null 2>&1 || true

log "Consolidated shared base images built successfully"
