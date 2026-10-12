#!/usr/bin/bash
# AI-hint: greenboot required check that verifies the core MiOS AI plane (agent-pipe, llm-light, pgvector) answered after boot; a no...
# AI-doc: usr/share/doc/mios/manual/system.md
set -euo pipefail

TIMEOUT=60          # max seconds to wait per service before declaring it down
POLL=3              # seconds between probe attempts
PROBE_TIMEOUT=3     # per-attempt connection timeout
HOST=127.0.0.1      # core AI services bind loopback (host-net / localhost)
ENV_FILE=/etc/mios/install.env

log()  { echo "[mios-greenboot] $*"; }
fail() { echo "[mios-greenboot] $*" >&2; }

# /run/mios/blade.env carries MIOS_BLADE_CAPS, written by role-apply.
if [[ -r /run/mios/blade.env ]]; then
    # shellcheck disable=SC1091
    . /run/mios/blade.env 2>/dev/null || true
fi

if [[ -r "$ENV_FILE" ]]; then
    _mios_had_u=0; case "$-" in *u*) _mios_had_u=1;; esac
    set +u; set -a
    # shellcheck disable=SC1090  # runtime SSOT bridge; absent on a fresh boot
    source "$ENV_FILE" 2>/dev/null || true
    set +a
    [ "$_mios_had_u" = 1 ] && set -u
fi

_tcp_up() {
    local host="$1" port="$2"
    timeout "$PROBE_TIMEOUT" bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null
}

_http_up() {
    local host="$1" port="$2" path="$3" code
    if ! command -v curl >/dev/null 2>&1; then
        if _tcp_up "$host" "$port"; then return 0; else return 1; fi
    fi
    code="$(curl -sS -o /dev/null -w '%{http_code}' \
        --max-time "$PROBE_TIMEOUT" "http://${host}:${port}${path}" 2>/dev/null || true)"
    case "$code" in
        [1-4][0-9][0-9]) return 0 ;;   # 100-499: listener served a response -> up
        *)               return 1 ;;   # 000 (refused/timeout) or 5xx -> down
    esac
}

# ADR-0016 D8: recorded every boot, critical only when
# [greenboot].blade_reachability_critical says so.
_endpoint_host_port() {
    local url="$1" rest hostport host port scheme
    scheme="${url%%://*}"; [[ "$scheme" != "$url" ]] || scheme=http
    rest="${url#*://}"
    hostport="${rest%%/*}"
    if [[ "$hostport" == \[*\]* ]]; then          # [::1]:8700 or [::1]
        host="${hostport%%]*}]"
        port="${hostport##*]}"; port="${port#:}"
    else
        host="${hostport%%:*}"
        port="${hostport#*:}"; [[ "$port" != "$hostport" ]] || port=""
    fi
    if [[ -z "$port" ]]; then
        case "$scheme" in https) port=443 ;; *) port=80 ;; esac
    fi
    printf '%s\t%s' "$host" "$port"
}

_blade_reachable() {
    local ep="${MIOS_BLADE_AI_ENDPOINT:-}" hp host port
    if [[ -z "$ep" || "${MIOS_BLADE_AUTH_POSTURE:-}" == "local" ]]; then
        log "blade reachability: front door is local -- nothing off-box to reach"
        return 0
    fi
    hp="$(_endpoint_host_port "$ep")"
    host="${hp%%$'\t'*}"; port="${hp##*$'\t'}"
    if [[ -z "$host" || -z "$port" ]]; then
        log "blade reachability: could not parse ${ep}"
        return 0
    fi
    if _tcp_up "$host" "$port"; then
        log "blade ${host}:${port} reachable"
        return 0
    fi
    fail "blade ${host}:${port} UNREACHABLE -- this seat offloads to it"
    return 1
}

# is-enabled reports INSTALLATION, not whether a unit will start. Ask the same
# question the unit's own ConditionPathExists asks. See ADR-0016 D4.
_blade_activates() {
    local unit="$1" caps cap
    caps="$(printf '%s' "${MIOS_BLADE_CAPS:-}" | tr ',' ' ')"
    # No resolver output at all: degrade OPEN and probe, as before (Law 12).
    [[ -z "$caps" && ! -d /etc/mios/blade.d ]] && return 0
    for f in "/usr/lib/systemd/system/${unit}.d"/50-blade-*.conf; do
        [[ -e "$f" ]] || continue
        cap="${f##*/50-blade-}"; cap="${cap%.conf}"
        [[ -e "/etc/mios/blade.d/${cap}" ]] || return 1
    done
    return 0
}

check_service() {
    local unit="$1" port="$2" kind="$3" path="${4:-}" deadline probe_desc

    if ! _blade_activates "$unit"; then
        log "${unit} not activated by this blade type"
        return 0
    fi
    if ! systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        log "${unit} not enabled on this role"
        return 0
    fi
    if [[ -z "$port" ]]; then
        log "${unit}: port unresolved from ${ENV_FILE}"
        return 0
    fi

    probe_desc="${kind} ${HOST}:${port}${path}"
    log "${unit}: probing ${probe_desc}"
    deadline=$(( $(date +%s) + TIMEOUT ))
    while true; do
        if [[ "$kind" == "http" ]]; then
            if _http_up "$HOST" "$port" "$path"; then
                log "${unit}: healthy"
                return 0
            fi
        else
            if _tcp_up "$HOST" "$port"; then
                log "${unit}: healthy"
                return 0
            fi
        fi
        if [[ $(date +%s) -ge $deadline ]]; then
            fail "FAIL: ${unit} did not answer ${probe_desc} within ${TIMEOUT}s."
            return 1
        fi
        sleep "$POLL"
    done
}

# Driven by [greenboot].critical_services; [greenboot.probe] holds the exceptions.
_var() { eval "printf '%s' \"\${$1:-}\""; }

rc=0
_svcs="$(printf '%s' "${MIOS_GREENBOOT_CRITICAL_SERVICES:-}" | tr ',' ' ')"
if [[ -z "$_svcs" ]]; then
    log "[greenboot].critical_services unresolved -- nothing to probe"
fi
for _svc in $_svcs; do
    _U="$(printf '%s' "$_svc" | tr '[:lower:]-' '[:upper:]_')"
    _unit="$(_var "MIOS_GREENBOOT_PROBE_${_U}_UNIT")"
    _kind="$(_var "MIOS_GREENBOOT_PROBE_${_U}_KIND")"
    _path="$(_var "MIOS_GREENBOOT_PROBE_${_U}_PATH")"
    _port="$(_var "MIOS_GREENBOOT_PROBE_${_U}_PORT")"
    [[ -n "$_unit" ]] || _unit="mios-${_svc}.service"
    [[ -n "$_kind" ]] || _kind="tcp"
    [[ -n "$_port" ]] || _port="$(_var "MIOS_PORTS_${_U}")"
    [[ -n "$_port" ]] || _port="$(_var "MIOS_PORT_${_U}")"
    check_service "$_unit" "$_port" "$_kind" "$_path" || rc=1
done

# Recorded on every boot, critical only when the SSOT says so.
if ! _blade_reachable; then
    case "$(printf '%s' "${MIOS_GREENBOOT_BLADE_REACHABILITY_CRITICAL:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) rc=1 ;;
        *) log "blade unreachable is RECORDED, not critical -- [greenboot].blade_reachability_critical is off" ;;
    esac
fi

if [[ "$rc" -eq 0 ]]; then
    log "AI plane healthy"
fi
exit "$rc"
