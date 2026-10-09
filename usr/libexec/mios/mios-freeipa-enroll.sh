#!/usr/bin/env bash
# AI-hint: bash Bash oneshot run by mios-freeipa-enroll.service that joins the host to a FreeIPA domain via ipa-client-install; gated on /etc/mios/i...
# AI-doc: usr/share/doc/mios/manual/mios.md
set -euo pipefail
source /usr/lib/mios/paths.sh

_log()  { logger -t mios-freeipa-enroll "$*" 2>/dev/null || true; echo "[freeipa-enroll] $*" >&2; }

CONF="${MIOS_ETC_DIR}/ipa-enroll.env"
if [[ ! -r "$CONF" ]]; then
    _log "no $CONF -- enrollment not requested"
    exit 0
fi
if [[ -e /etc/ipa/default.conf ]]; then
    _log "already enrolled (/etc/ipa/default.conf exists); nothing to do"
    exit 0
fi
if ! command -v ipa-client-install >/dev/null 2>&1; then
    _log "ipa-client-install not present -- freeipa-client RPM missing"
    exit 0
fi

umask 0077
# shellcheck disable=SC1090  # $CONF is a runtime path, not a tracked file
. "$CONF"

# MIOS_IPA_PRINCIPAL and MIOS_IPA_PASSWORD were required here and are emitted
# by nothing; the projection writes MIOS_IPA_ENROLL_PRINCIPAL (task_990).
for v in MIOS_IPA_REALM MIOS_IPA_DOMAIN MIOS_IPA_SERVER MIOS_IPA_ENROLL_PRINCIPAL; do
    if [[ -z "${!v:-}" ]]; then
        _log "missing required var $v in $CONF"
        exit 1
    fi
done

# $CONF is 0644 and tracked, so the password is read from a 0600 file it names
# rather than carried in it (task_987). umask 0077 is already set above.
: "${MIOS_IPA_OTP_FILE:=/etc/mios/secrets.env}"
: "${MIOS_IPA_OTP_KEY:=MIOS_IPA_OTP}"
# shellcheck disable=SC1090  # the secrets path is operator-chosen at runtime
[[ -r "$MIOS_IPA_OTP_FILE" ]] && . "$MIOS_IPA_OTP_FILE"
_otp="${!MIOS_IPA_OTP_KEY:-}"
if [[ -z "$_otp" ]]; then
    _log "no $MIOS_IPA_OTP_KEY in $MIOS_IPA_OTP_FILE -- enrollment needs the one-time password"
    exit 1
fi

: "${MIOS_IPA_HOSTNAME:=$(hostname -f 2>/dev/null || hostname)}"
: "${MIOS_IPA_NTP:=true}"
: "${MIOS_IPA_AUTOMOUNT:=false}"

_log "enrolling to realm=$MIOS_IPA_REALM domain=$MIOS_IPA_DOMAIN server=$MIOS_IPA_SERVER hostname=$MIOS_IPA_HOSTNAME"

ARGS=(
    --unattended
    --mkhomedir
    --no-ssh
    --no-sshd
    --realm="$MIOS_IPA_REALM"
    --domain="$MIOS_IPA_DOMAIN"
    --server="$MIOS_IPA_SERVER"
    --hostname="$MIOS_IPA_HOSTNAME"
    --principal="$MIOS_IPA_ENROLL_PRINCIPAL"
    --password="$_otp"
)
[[ "$MIOS_IPA_NTP" == "true" ]] || ARGS+=(--no-ntp)
[[ "$MIOS_IPA_AUTOMOUNT" == "true" ]] && ARGS+=(--enable-automount)

ipa-client-install "${ARGS[@]}"
rc=$?
if (( rc == 0 )); then
    _log "enrollment successful"
    _otp="$(printf '%*s' "${#_otp}" '' | tr ' ' x)"
    exit 0
fi
_log "enrollment FAILED (rc=$rc)"
exit "$rc"
