#!/usr/bin/env bash
# AI-hint: Build-if-missing bootstrap for the mios-webtools container images
# AI-related: /usr/share/mios/crawl4ai/Containerfile, /usr/share/mios/webtools/firecrawl.Containerfile, mios-webtools-firstboot.service
set -uo pipefail

log() { logger -t mios-webtools-firstboot "$*" 2>/dev/null || true; echo "[mios-webtools-firstboot] $*" >&2; }

_FIRSTBOOT_SENTINEL="/var/lib/mios/.webtools-firstboot.done"
if command -v miosd >/dev/null 2>&1; then
    miosd build-if-missing webtools
    exit 0
fi

if [ -f "$_FIRSTBOOT_SENTINEL" ]; then
    log "Sentinel $_FIRSTBOOT_SENTINEL present"
    exit 0
fi

need_build() {
    local img="$1" cf="$2"
    if ! podman image exists "$img"; then echo 1; return; fi
    local _img_epoch _cf_epoch
    _img_epoch="$(date -d "$(podman image inspect -f '{{.Created}}' "$img" 2>/dev/null)" +%s 2>/dev/null || echo 0)"
    _cf_epoch="$(stat -c %Y "$cf" 2>/dev/null || echo 0)"
    if [ "$_img_epoch" -gt 0 ] && [ "$_cf_epoch" -gt "$_img_epoch" ]; then echo 1; else echo 0; fi
}

build_image_retry() {
    local img="$1" cf="$2" ctx="$3" attempts=3 a
    for a in $(seq 1 "$attempts"); do
        log "Building $img from $cf "
        if podman build --network=host -t "$img" -f "$cf" "$ctx" && podman image exists "$img"; then
            log "Built + verified $img"
            return 0
        fi
        log "WARN: $img build attempt $a/$attempts failed"
        podman image prune --force >/dev/null 2>&1 || true
        if [ "$a" -lt "$attempts" ]; then
            log "Retrying in $)s"
            sleep $((a*10))
        fi
    done
    log "ERROR: $img build failed after $attempts attempts"
    return 1
}

maybe_build() {
    local img="$1" cf="$2" ctx="$3" label="$4"
    if [ ! -f "$cf" ]; then
        log "WARN: $cf missing"
        return 0
    fi
    if [ "$(need_build "$img" "$cf")" = 1 ]; then
        build_image_retry "$img" "$cf" "$ctx"
        return $?
    fi
    log "Image $img current; skipping $label build"
    return 0
}

_rc=0

maybe_build "localhost/mios-crawl4ai-slim:latest" \
            "/usr/share/mios/crawl4ai/Containerfile" \
            "/usr/share/mios/crawl4ai" "crawl4ai" || _rc=1

maybe_build "localhost/mios-firecrawl:v1.0.0" \
            "/usr/share/mios/webtools/firecrawl.Containerfile" \
            "/usr/share/mios/webtools" "firecrawl" || _rc=1

if [ "$_rc" -eq 0 ]; then
    install -d -m 0755 /var/lib/mios 2>/dev/null || true
    touch "$_FIRSTBOOT_SENTINEL" 2>/dev/null || true
    log "All webtools images present"
else
    log "One or more webtools images missing"
fi
exit "$_rc"
