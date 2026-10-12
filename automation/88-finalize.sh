#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Finalizes the build by applying systemd presets, setting the default boot target, scrubbing credential leaks, purging D...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

systemctl preset-all 2>/dev/null || true

systemctl set-default multi-user.target 2>/dev/null || true

install -d -m 0755 ${MIOS_SHARE_DIR}

mios_log "Scrubbing build-time credentials and override scripts"
rm -f /etc/containers/auth.json \
      /root/.docker/config.json \
      /root/.containers/auth.json \
      /ctx/automation/99-overrides.sh \
      /usr/local/bin/99-overrides.sh \
      /usr/bin/99-overrides.sh 2>/dev/null || true

$DNF_BIN "${DNF_SETOPT[@]}" clean all 2>/dev/null || true
rm -rf /var/cache/libdnf5 /var/cache/dnf /var/log/dnf5.log* 2>/dev/null || true

MIOS_VERSION=$(cat /ctx/VERSION 2>/dev/null || echo "Unknown")
install -d -m 0755 ${MIOS_USR_DIR}
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
    MIOS_BUILT=$(date -u -d "@${SOURCE_DATE_EPOCH}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)
else
    MIOS_BUILT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
fi
cat > ${MIOS_USR_DIR}/version <<EOF
MIOS_VERSION=${MIOS_VERSION}
MIOS_BASE=ucore-hci-stable-nvidia
MIOS_BUILT=${MIOS_BUILT}
EOF
ln -sf ${MIOS_USR_DIR}/version ${MIOS_USR_DIR}/mios-version

OSR=/usr/lib/os-release

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so the lookup this replaced could never
# succeed and the branch below it was dead on every build (task_962).
_miosd=""
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "${_here}/src/mios-rs/target/release/miosd" \
          "${_here}/src/mios-rs/target/debug/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

if [[ -z "$_miosd" ]]; then
    mios_err "Native miosd is required to finalize os-release (task_962)"
    exit 1
fi

# No blanket success log here: miosd prints what it actually did, including the
# two cases where it projects nothing. Asserting the projection on the strength
# of the exit code is what made the old miosd leg unfalsifiable.
"$_miosd" finalize-osrelease --path "$OSR" --version "$MIOS_VERSION"

mios_ok "Finalized"
