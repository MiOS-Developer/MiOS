#!/usr/bin/env bash
# AI-hint: bash Proves ADR-0016 D5 -- a seat's front door is off-box by design, so that is where [security].api_require_auth and principal_bind_mode stop being optional.
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

log() { printf '[seat-auth-posture] %s\n' "$*"; }
die() { printf '[seat-auth-posture] ERROR: %s\n' "$*" >&2; exit 1; }
ok()  { PASS=$((PASS + 1)); log "ok: $*"; }

# shellcheck source=/dev/null
source "${ROOT}/usr/lib/mios/blade.sh"
declare -F _auth_posture >/dev/null || die "_auth_posture missing -- did it get renamed?"

export MIOS_USR_DIR="${ROOT}/usr/lib/mios"
export MIOS_USER_TOML=/dev/null

# Run the REAL resolver against one fixture overlay.
posture() {
    local body="$1" f="${FIXTURE}/host.toml"
    printf '%s\n' "$body" > "$f"
    MIOS_HOST_TOML="$f" _auth_posture
}
verdict() { printf '%s' "${1%%$'\t'*}"; }
detail()  { local r="${1#*$'\t'}"; printf '%s' "${r#*$'\t'}"; }

BLADE='blade-01.mesh.mios.local'
OFFBOX="[ai]
endpoint = \"http://${BLADE}:8700/v1\"
"

# --- a hosted blade: loopback front door, flags irrelevant -------------------
r="$(posture '')"
[[ "$(verdict "$r")" == "local" ]] \
    || die "vendor default must be a LOCAL front door, got: $r"
ok "vendor default is loopback -- the auth question does not arise"

r="$(posture "[ai]
endpoint = \"http://127.0.0.1:8700/v1\"
")"
[[ "$(verdict "$r")" == "local" ]] || die "127.0.0.1 must read as local, got: $r"
ok "127.0.0.1 is local"

# A hosted blade with the controls OFF is not a finding. Demanding a caller key
# on a single-tenant loopback box would lock the agent plane out of its own door.
r="$(posture "[ai]
endpoint = \"http://localhost:8700/v1\"

[security]
api_require_auth = false
principal_bind_mode = \"off\"
")"
[[ "$(verdict "$r")" == "local" ]] \
    || die "a loopback blade with auth off must NOT be flagged, got: $r"
ok "a hosted blade with the controls off is not a finding"

# --- a seat: off-box front door ---------------------------------------------
r="$(posture "$OFFBOX")"
[[ "$(verdict "$r")" == "exposed" ]] \
    || die "an off-box endpoint with vendor defaults must read EXPOSED, got: $r"
case "$(detail "$r")" in
    *api_require_auth*principal_bind_mode*) : ;;
    *) die "the exposed detail must name BOTH gaps, got: $(detail "$r")" ;;
esac
ok "off-box + vendor defaults is exposed, naming both gaps"

r="$(posture "${OFFBOX}
[security]
api_require_auth = true
")"
[[ "$(verdict "$r")" == "exposed" ]] \
    || die "auth alone must still read exposed -- the owner is unbound, got: $r"
case "$(detail "$r")" in
    *principal_bind_mode*) : ;;
    *) die "must name the REMAINING gap, got: $(detail "$r")" ;;
esac
case "$(detail "$r")" in
    *api_require_auth*) die "must not report a gap that is closed: $(detail "$r")" ;;
esac
ok "auth on, binding off -- exposed, naming only the remaining gap"

r="$(posture "${OFFBOX}
[security]
api_require_auth = true
principal_bind_mode = \"enforce\"
")"
[[ "$(verdict "$r")" == "armed" ]] || die "off-box + both controls must be ARMED, got: $r"
ok "off-box with both controls on is armed"

# The bind-mode consumer (grounding._parse_principal_bind_mode) reads "audit" as
# verify and rejects anything else, failing closed. Neither arms a seat: verify
# only logs a mismatch, and a rejected value must be fixed, not guessed at.
bound() {
    posture "${OFFBOX}
[security]
api_require_auth = true
principal_bind_mode = \"$1\"
"
}
r="$(bound verify)"
[[ "$(verdict "$r")" == "exposed" && "$(detail "$r")" == *"principal_bind_mode is verify, not enforce"* ]] \
    || die "verify only observes -- it must read exposed, got: $r"
ok "verify is a gap: it logs a mismatch but binds nothing"

r="$(bound audit)"
[[ "$(verdict "$r")" == "exposed" ]] || die "audit (the vendor value) must read exposed, got: $r"
[[ "$(detail "$r")" == *"principal_bind_mode is audit (an alias of verify), not enforce"* ]] \
    || die "audit must be reported as the verify alias it is, got: $(detail "$r")"
[[ "$(detail "$r")" != *api_require_auth* ]] || die "must not report a closed gap: $(detail "$r")"
ok "audit reads as verify, naming only the binding gap"

r="$(bound verfy)"
[[ "$(verdict "$r")" == "exposed" ]] || die "a rejected bind mode must never read ARMED, got: $r"
[[ "$(detail "$r")" == *"'verfy' is not a bind mode"*"fails closed to enforce"* ]] \
    || die "a rejected value must be named, with what the consumer does instead, got: $(detail "$r")"
ok "an unknown bind mode is reported as rejected, not armed"

r="$(bound ENFORCE)"
[[ "$(verdict "$r")" == "armed" ]] || die "the mode is case-insensitive like its consumer, got: $r"
ok "ENFORCE arms the seat like enforce"

# The overlay is ONE mechanism: a LAN address is the same case as a hostname.
r="$(posture "[ai]
endpoint = \"http://10.42.0.7:8700/v1\"
")"
[[ "$(verdict "$r")" == "exposed" ]] || die "a LAN IP is off-box too, got: $r"
ok "a LAN address is the same case as a remote hostname"

# --- degrade open (Law 12): the verdict is a report, never an exit code ------
r="$(posture "$OFFBOX")" || die "_auth_posture must not fail on an exposed seat"
ok "an exposed seat still resolves -- the check degrades open"

log "PASS: ${PASS}/12 assertions"
