#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs cosign, renders the [security.sigstore] container policy, installs the trust anchors it names from the source tree into /usr/share/pki/containers, validates it with skopeo, and activates it in /etc/containers only in image builds.
# AI-related: mios-cosign, usr/share/mios/mios.toml, usr/lib/containers/policy.json, usr/lib/containers/registries.d, tools/native/mios-resolver/src/sigstore.rs, tests/test-cosign-trust-anchor.sh
set -euo pipefail

# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

mios_log "Ensuring cosign + trust roots + policy.json"

if ! command -v cosign >/dev/null 2>&1; then
    # SSOT carries intent only: the release URL and, optionally, the cosign_major
    # compatibility channel. The newest release is resolved here and proven by the
    # release's own cosign_checksums.txt; the resolved tag is build output.
    # shellcheck source=automation/lib/upstream-latest.sh
    source "$(dirname "$0")/lib/upstream-latest.sh"
    _here_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    COSIGN_RELEASE_URL="${MIOS_SECURITY_SIGSTORE_COSIGN_RELEASE_URL:?SSOT cosign release URL unresolved}"
    COSIGN_MAJOR="$(MIOS_TOML_ROOT="$_here_root" python3 "$_here_root/usr/libexec/mios/mios-toml-get" --vendor security.sigstore cosign_major "" 2>/dev/null || true)"
    case "$(uname -m)" in x86_64) _cosign_arch=amd64 ;; aarch64) _cosign_arch=arm64 ;; *) die "No cosign build for $(uname -m)" ;; esac
    mios_log "Resolving newest cosign release${COSIGN_MAJOR:+ (v${COSIGN_MAJOR} channel)}"
    _cosign_out="$(mios_cosign_fetch_latest "$COSIGN_RELEASE_URL" "$COSIGN_MAJOR" /usr/bin/cosign "$_cosign_arch")" \
        || { mios_ul_pass $? || die "cosign: newest release could not be fetched and verified"; }
    read -r COSIGN_VERSION _cosign_sha <<< "$_cosign_out"
    mios_record_resolved cosign "$COSIGN_VERSION" "https://github.com/sigstore/cosign/releases/tag/${COSIGN_VERSION}" "$_cosign_sha"
    mios_ok "cosign ${COSIGN_VERSION} installed (sha256 ${_cosign_sha:0:16}...)"
fi

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The tree the policy is rendered into and the tree trust anchors are read from:
# the build context in an image build, the checkout otherwise. Never /etc.
_policy_root="${MIOS_ROOT:-$_here}"
_src_root="${CTX:-$_here}"
_anchor_dir=/usr/share/pki/containers

# Native projection is required; an existing policy file does not prove freshness.
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/bin/miosd \
          /usr/libexec/mios/miosd \
          "${_here}/src/mios-rs/target/release/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done
[[ -n "$_miosd" ]] || die "Native miosd is required for signing policy projection"
# mios-gen refuses an invalid requirement type and any policy under which an
# image in the MiOS-owned namespace is accepted without a MiOS-anchored signature.
MIOS_ROOT="$_policy_root" "$_miosd" cosign-policy \
    || die "[security.sigstore] did not render a sound policy (see mios-gen above)"
_policy="${_policy_root}/usr/lib/containers/policy.json"
[[ -s "$_policy" ]] || die "cosign-policy produced no ${_policy}"
mios_ok "Policy rendered from [security.sigstore]: ${_policy}"

# Consumer validation: containers/image's own loader, which is strict about
# requirement types (jq only proves the bytes are JSON). The source cannot
# exist, so a valid policy makes skopeo fail on the source instead, after the
# policy has loaded. Neither outcome is a skip: anything else is a failure.
command -v skopeo >/dev/null 2>&1 || die "skopeo (containers/image) is required to validate ${_policy}"
_probe_src="/nonexistent/mios-cosign-policy-probe-$$"
_probe="$(skopeo --policy "$_policy" copy "dir:${_probe_src}" "dir:${_probe_src}-out" 2>&1 || true)"
case "$_probe" in
    *"trust policy"*|*"invalid policy"*) die "containers/image refuses ${_policy}: ${_probe}" ;;
    *"${_probe_src}"*) mios_ok "containers/image loads ${_policy}" ;;
    *) die "could not establish that containers/image loads ${_policy}: ${_probe}" ;;
esac

# Every key the policy trusts. Anchors under ${_anchor_dir} are MiOS's and come
# only from the source tree, byte for byte: not from /etc (host state) and not
# from the destination (that would trust whatever is already installed). A key
# path outside it (a base image's own key) must already exist in this root.
mapfile -t _keys < <(python3 -I - "$_policy" <<'PY'
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
reqs = list(p.get("default", []))
for scopes in p.get("transports", {}).values():
    for rs in scopes.values():
        reqs.extend(rs)
seen = set()
for r in reqs:
    for k in ([r["keyPath"]] if "keyPath" in r else []) + list(r.get("keyPaths", [])):
        if k not in seen:
            seen.add(k)
            print(k)
PY
)
for _dst in "${_keys[@]}"; do
    if [[ "$_dst" != "${_anchor_dir}/"* ]]; then
        [[ -s "$_dst" ]] || die "${_policy} trusts ${_dst}, which this root does not have"
        mios_ok "Trust anchor ${_dst} (provided by the base image)"
        continue
    fi
    _src="${_src_root}${_dst}"
    if [[ ! -f "$_src" || -L "$_src" || ! -s "$_src" ]]; then
        die "MiOS trust anchor ${_dst} is required by ${_policy} but the source tree ships no ${_dst#/}. The MiOS signing key is an operator secret (ruling Q4, task_3536): create it with 'cosign generate-key-pair', keep the private key as a publisher secret, sign every ghcr.io/mios-dev image by digest with it, then commit only the public key at ${_dst#/}."
    fi
    if [[ "$_src" -ef "$_dst" ]]; then
        mios_ok "Trust anchor ${_dst} (the source tree is this root)"
        continue
    fi
    install -D -m 0644 "$_src" "$_dst"
    cmp -s "$_src" "$_dst" || die "trust anchor ${_dst} does not match ${_src} after install"
    mios_ok "Installed trust anchor ${_dst}"
done

# Activation. containers/image reads only $HOME/.config/containers or
# /etc/containers (policy.json and registries.d), never /usr/lib/containers.
# In an image build /etc is the deployment default each host 3-way-merges, so
# local edits still win; on a running host /etc/containers is the admin's and
# this phase leaves it alone.
if [[ -f /run/.containerenv || -n "${container:-}" ]]; then
    install -d -m 0755 /usr/lib/containers /etc/containers /etc/containers/registries.d
    [[ "$_policy" -ef /usr/lib/containers/policy.json ]] || install -m 0644 "$_policy" /usr/lib/containers/policy.json
    install -m 0644 "$_policy" /etc/containers/policy.json
    for _y in "${_src_root}"/usr/lib/containers/registries.d/*.yaml; do
        [[ -f "$_y" ]] || continue
        install -m 0644 "$_y" "/etc/containers/registries.d/${_y##*/}"
        mios_ok "Sigstore attachments: /etc/containers/registries.d/${_y##*/}"
    done
    mios_ok "Image default /etc/containers/policy.json enforces [security.sigstore]"
else
    mios_log "Not an image build: /etc/containers is this host's; the policy stays the vendor projection ${_policy}"
fi

mios_ok "Validation complete"
