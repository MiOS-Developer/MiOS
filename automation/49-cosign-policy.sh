#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs the cosign binary (v2.x), configures Sigstore trust roots, and sets up policy.json to ensure OCI 1.1 bundle compatibility during image builds.
# AI-related: mios-cosign
set -euo pipefail

# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

mios_log "Ensuring cosign + trust roots + policy.json"

if ! command -v cosign >/dev/null 2>&1; then
    COSIGN_VERSION="${MIOS_SECURITY_SIGSTORE_COSIGN_VERSION:?SSOT cosign version unresolved}"
    COSIGN_BASE_URL="${MIOS_SECURITY_SIGSTORE_COSIGN_RELEASE_URL:?SSOT cosign release URL unresolved}/${COSIGN_VERSION}"
    record_version cosign "$COSIGN_VERSION" "https://github.com/sigstore/cosign/releases/tag/${COSIGN_VERSION}"
    mios_log "Resolved SSOT cosign version: ${COSIGN_VERSION}"
    mios_log "Downloading cosign ${COSIGN_VERSION} static binary"
    mkdir -p /tmp/cosign-dl
    scurl -sfL "${COSIGN_BASE_URL}/cosign-linux-amd64" -o /tmp/cosign-dl/cosign-linux-amd64
    scurl -sfL "${COSIGN_BASE_URL}/cosign_checksums.txt" -o /tmp/cosign-dl/cosign_checksums.txt
    (cd /tmp/cosign-dl && grep "cosign-linux-amd64$" cosign_checksums.txt | sha256sum -c -) \
        || die "Cosign ${COSIGN_VERSION} SHA256 mismatch"
    install -m 0755 /tmp/cosign-dl/cosign-linux-amd64 /usr/bin/cosign

    sbom_dir="/usr/share/mios/artifacts/sbom"
    mkdir -p "$sbom_dir"
    sha=""
    if command -v sha256sum >/dev/null 2>&1; then
        sha="$(sha256sum /usr/bin/cosign | awk '{print $1}')"
    fi
    printf '%s\t%s\t%s\n' "cosign" "${COSIGN_VERSION}" "${sha:-unknown}" >> "${sbom_dir}/binaries.tsv"

    rm -rf /tmp/cosign-dl
fi

SYSFILES="${CTX:-/ctx}"
install -d -m 0755 /usr/share/pki/containers
install -d -m 0755 /usr/lib/containers/registries.d

# Native projection is required; an existing policy file does not prove freshness.
_miosd=""
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/bin/miosd \
          /usr/libexec/mios/miosd \
          "${_here}/src/mios-rs/target/release/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

[[ -n "$_miosd" ]] || die "Native miosd is required for signing policy projection"
MIOS_ROOT="${MIOS_ROOT:-$_here}" "$_miosd" cosign-policy
mios_ok "Policy.json generated via native mios-gen"

for f in fulcio_v1.crt.pem rekor.pub ublue-os.pub ublue-cosign.pub mios-cosign.pub; do
    src="${SYSFILES}/usr/share/pki/containers/${f}"
    dst="/usr/share/pki/containers/${f}"
    if [[ -f "${src}" ]]; then
        install -m 0644 "${src}" "${dst}"
        mios_ok "Installed ${dst}"
    fi
done

if command -v jq >/dev/null 2>&1 && [[ -f /usr/lib/containers/policy.json ]]; then
    jq -e . /usr/lib/containers/policy.json >/dev/null || die "Policy.json failed jq parse"
    mios_ok "Policy.json parses cleanly"
fi

mios_ok "Validation complete"
