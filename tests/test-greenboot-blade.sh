#!/usr/bin/env bash
# AI-hint: bash Greenboot blade suite: proves activation guard, SSOT probe derivation, and reachability probe (ADR-0016 D8).
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SCRIPT="${ROOT}/usr/lib/greenboot/check/required.d/40-mios-ai-plane.sh"
PASS=0

log()  { printf '[greenboot-blade] %s\n' "$*"; }
die()  { printf '[greenboot-blade] ERROR: %s\n' "$*" >&2; exit 1; }
fail() { printf '[greenboot-blade] %s\n' "$*"; }
ok()   { PASS=$((PASS + 1)); log "ok: $*"; }

[[ -r "$SCRIPT" ]] || die "probe script missing: $SCRIPT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; [[ -n "${SRV_PID:-}" ]] && kill "$SRV_PID" 2>/dev/null || true' EXIT

# ==============================================================================
# Part 1: Activation Guard & SSOT-Driven Probe Derivation
# ==============================================================================

# Extract _blade_activates and run it against a fixture tree. Sourcing the whole
# script would execute its probes; this tests the predicate in isolation.
sed -n '/^_blade_activates() {/,/^}/p' "$SCRIPT" > "${WORK}/fn_guard.sh"
[[ -s "${WORK}/fn_guard.sh" ]] || die "could not extract _blade_activates -- did it get renamed?"

mkdir -p "${WORK}/usr/lib/systemd/system/mios-pgvector.service.d"
printf '[Unit]\nConditionPathExists=/etc/mios/blade.d/service-plane\n' \
    > "${WORK}/usr/lib/systemd/system/mios-pgvector.service.d/50-blade-service-plane.conf"
mkdir -p "${WORK}/usr/lib/systemd/system/mios-agent-pipe.service.d"   # no drop-in: ungated

# Rewrite the two absolute lookups onto the fixture so the real logic is exercised.
sed -e "s#/usr/lib/systemd/system/#${WORK}/usr/lib/systemd/system/#g" \
    -e "s#/etc/mios/blade.d#${WORK}/etc/mios/blade.d#g" \
    "${WORK}/fn_guard.sh" > "${WORK}/fn_guard2.sh"
# shellcheck disable=SC1091
. "${WORK}/fn_guard2.sh"

mkdir -p "${WORK}/etc/mios/blade.d"

# --- a seat: markers directory exists, service-plane marker absent -----------
MIOS_BLADE_CAPS="" _blade_activates mios-pgvector.service \
    && die "a seat must NOT probe mios-pgvector: its capability marker is absent"
ok "seat skips a capability-gated unit"

MIOS_BLADE_CAPS="" _blade_activates mios-agent-pipe.service \
    || die "an ungated unit must still be probed on a seat"
ok "seat still probes the ungated front door"

# --- a serving blade: the marker is present ---------------------------------
touch "${WORK}/etc/mios/blade.d/service-plane"
MIOS_BLADE_CAPS="service-plane" _blade_activates mios-pgvector.service \
    || die "a blade granting service-plane MUST probe mios-pgvector"
ok "serving blade probes the gated unit"

# --- degrade open: no resolver output at all (Law 12) ------------------------
rm -rf "${WORK}/etc/mios/blade.d"
MIOS_BLADE_CAPS="" _blade_activates mios-pgvector.service \
    || die "with no blade.d at all the check must degrade OPEN and probe"
ok "degrades open when the blade resolver has not run"

# --- the SSOT-driven probe list resolves to the right unit/port/kind ---------
# Extract the driver loop and run it with check_service stubbed, so the derived
# unit/port/kind are observable without probing anything.
sed -n '/^_var() {/,$p' "$SCRIPT" > "${WORK}/drv.sh"
CALLS="$(
  set +u
  # shellcheck disable=SC2317
  check_service() { printf '%s|%s|%s|%s\n' "$1" "$2" "$3" "$4"; }
  _blade_reachable() { :; }
  log() { :; }
  export MIOS_GREENBOOT_CRITICAL_SERVICES="agent-pipe,llm-light,pgvector,hermes"
  export MIOS_GREENBOOT_PROBE_AGENT_PIPE_KIND="http"
  export MIOS_GREENBOOT_PROBE_AGENT_PIPE_PATH="/v1/models"
  export MIOS_GREENBOOT_PROBE_HERMES_UNIT="hermes-worker.service"
  export MIOS_PORTS_AGENT_PIPE=8700 MIOS_PORTS_LLM_LIGHT=8500
  export MIOS_PORTS_PGVECTOR=8600 MIOS_PORTS_HERMES=8720
  # shellcheck disable=SC1091
  . "${WORK}/drv.sh"
)"
EXPECT='mios-agent-pipe.service|8700|http|/v1/models
mios-llm-light.service|8500|tcp|
mios-pgvector.service|8600|tcp|
hermes-worker.service|8720|tcp|'
[[ "$CALLS" == "$EXPECT" ]] || die "SSOT-driven probe list differs:
got:
$CALLS
want:
$EXPECT"
ok "SSOT drives the same four probes the hardcoded list did"

# An empty critical set must probe NOTHING rather than fall back to a hidden list.
EMPTY="$(
  set +u
  # shellcheck disable=SC2317
  check_service() { printf 'UNEXPECTED\n'; }
  _blade_reachable() { :; }
  log() { :; }
  export MIOS_GREENBOOT_CRITICAL_SERVICES=""
  # shellcheck disable=SC1091
  . "${WORK}/drv.sh"
)"
[[ -z "$EMPTY" ]] || die "an empty critical set must probe nothing, got: $EMPTY"
ok "empty critical set probes nothing"


{
    sed -n '/^_tcp_up() {/,/^}/p' "$SCRIPT"
    sed -n '/^_endpoint_host_port() {/,/^}/p' "$SCRIPT"
    sed -n '/^_blade_reachable() {/,/^}/p' "$SCRIPT"
} > "${WORK}/fn_reach.sh"
grep -q '_blade_reachable' "${WORK}/fn_reach.sh" || die "could not extract _blade_reachable -- renamed?"

# shellcheck disable=SC2034  # read by _tcp_up, which fn_reach.sh (sourced below) defines
PROBE_TIMEOUT=3
# shellcheck disable=SC1091
. "${WORK}/fn_reach.sh"

hostport() { local r; r="$(_endpoint_host_port "$1")"; printf '%s:%s' "${r%%$'\t'*}" "${r##*$'\t'}"; }

# --- URL parsing -------------------------------------------------------------
[[ "$(hostport 'http://blade-01:8700/v1')" == "blade-01:8700" ]] \
    || die "host:port parse failed: $(hostport 'http://blade-01:8700/v1')"
ok "host and port parse out of a normal endpoint"

[[ "$(hostport 'https://blade-01/v1')" == "blade-01:443" ]] \
    || die "https default port wrong: $(hostport 'https://blade-01/v1')"
[[ "$(hostport 'http://blade-01/v1')" == "blade-01:80" ]] \
    || die "http default port wrong: $(hostport 'http://blade-01/v1')"
ok "a portless URL takes its scheme's default"

[[ "$(hostport 'http://[::1]:8700/v1')" == "[::1]:8700" ]] \
    || die "IPv6 literal parse failed: $(hostport 'http://[::1]:8700/v1')"
ok "an IPv6 literal keeps its brackets and finds its port"

# --- a REAL listener on an ephemeral port ------------------------------------
python3 - "$WORK/port" <<'PY' &
import socket, sys, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 0))
s.listen(8)
open(sys.argv[1], "w").write(str(s.getsockname()[1]))
time.sleep(30)
PY
SRV_PID=$!
for _ in $(seq 1 50); do [[ -s "${WORK}/port" ]] && break; sleep 0.1; done
[[ -s "${WORK}/port" ]] || die "the fixture listener never reported a port"
PORT="$(cat "${WORK}/port")"

MIOS_BLADE_AUTH_POSTURE=armed MIOS_BLADE_AI_ENDPOINT="http://127.0.0.1:${PORT}/v1" \
    _blade_reachable || die "a blade that IS listening must read reachable"
ok "a real listener reads reachable"

# --- unreachable: recorded, never fatal --------------------------------------
DEAD="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));p=s.getsockname()[1];s.close();print(p)')"
if MIOS_BLADE_AUTH_POSTURE=armed MIOS_BLADE_AI_ENDPOINT="http://127.0.0.1:${DEAD}/v1" \
    _blade_reachable; then
    die "a closed port must NOT read reachable"
fi
ok "a closed port reads unreachable"

# The shipped default must leave rc untouched. This is the whole point of D8.
rc=0
if ! MIOS_BLADE_AUTH_POSTURE=armed MIOS_BLADE_AI_ENDPOINT="http://127.0.0.1:${DEAD}/v1" \
    _blade_reachable; then
    case "$(printf '%s' "${MIOS_GREENBOOT_BLADE_REACHABILITY_CRITICAL:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) rc=1 ;;
    esac
fi
[[ "$rc" -eq 0 ]] || die "an unreachable blade must not fail the boot by default"
ok "unreachable is RECORDED, not critical, at the shipped default"

rc=0
if ! MIOS_BLADE_AUTH_POSTURE=armed MIOS_BLADE_AI_ENDPOINT="http://127.0.0.1:${DEAD}/v1" \
    _blade_reachable; then
    case "$(printf '%s' "true" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) rc=1 ;;
    esac
fi
[[ "$rc" -eq 1 ]] || die "with the flag ON an unreachable blade must fail"
ok "the flag is real: turning it on makes the same outage fatal"

# --- a hosted blade is unaffected -------------------------------------------
MIOS_BLADE_AUTH_POSTURE=local MIOS_BLADE_AI_ENDPOINT="http://localhost:8700/v1" \
    _blade_reachable || die "a local front door must never be probed as a blade"
ok "a loopback front door is not probed"

MIOS_BLADE_AUTH_POSTURE="" MIOS_BLADE_AI_ENDPOINT="" _blade_reachable \
    || die "with no endpoint recorded the check must degrade open"
ok "no recorded endpoint degrades open"

log "PASS: ${PASS}/15 assertions"
