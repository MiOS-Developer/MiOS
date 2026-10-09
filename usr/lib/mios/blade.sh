#!/usr/bin/bash
# AI-hint: Shared blade-resolution library. ONE implementation of the archetype ladder, the capability set and the alias table, sourced by usr/libexec/mios/role-appl...
# AI-doc: usr/share/doc/mios/manual/mios.md

: "${ROLE_CONF:=${MIOS_ETC_DIR:-/etc/mios}/role.conf}"
: "${BLADE_D:=${MIOS_ETC_DIR:-/etc/mios}/blade.d}"
: "${CMDLINE_FILE:=/proc/cmdline}"

# Last `key=value` token on the kernel command line wins: bootc concatenates
# kargs.d in file order.
_cmdline_tok() {
    local key="$1" tok out=""
    [[ -r "$CMDLINE_FILE" ]] || return 0
    for tok in $(< "$CMDLINE_FILE"); do
        case "$tok" in
            "${key}="*) out="${tok#"${key}"=}" ;;
        esac
    done
    printf '%s' "$out"
}

# Read one KEY from a bare KEY=value file WITHOUT sourcing it: `.` would run
# it as root and clobber the names it sets.
_conf_get() {
    local file="$1" key="$2" val=""
    [[ -r "$file" ]] || return 0
    val="$(sed -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$file" | tail -1)"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    printf '%s' "$val"
}

# One SSOT call, emitting shell assignments for `eval`. Empty on any failure,
# so an unreadable SSOT still boots.
_ssot_query() {
    python3 - <<'PY' 2>/dev/null || true
import os
import shlex
import sys

sys.path.insert(0, os.environ.get("MIOS_USR_DIR", "/usr/lib/mios"))
import mios_toml  # noqa: E402

blade = (mios_toml.load_merged().get("blade") or {})
arche = blade.get("archetypes") or {}
alias = blade.get("role_aliases") or {}

def flat(value):
    return [value] if isinstance(value, str) else list(value or [])

legal = sorted({c for caps in arche.values() for c in flat(caps) if c})
print("SSOT_TYPE=%s" % shlex.quote(str(blade.get("type") or "")))
print("SSOT_FALLBACK=%s" % shlex.quote(str(blade.get("fallback") or "")))
print("SSOT_ARCHETYPES=%s" % shlex.quote(" ".join(sorted(arche))))
print("SSOT_CAPS_LEGAL=%s" % shlex.quote(" ".join(legal)))
print("SSOT_ALIASES=%s" % shlex.quote(
    " ".join("%s=%s" % (k, v) for k, v in sorted(alias.items()))))
for name, caps in sorted(arche.items()):
    var = "".join(ch if ch.isalnum() else "_" for ch in name).upper()
    print("SSOT_ARCH_%s=%s" % (var, shlex.quote(" ".join(flat(caps)))))
PY
}

# A safety demotion applied to tier 3 only. Emits a REASON; what it demotes TO
# is [blade].fallback, and with none it demotes to nothing.
# Callers set VIRT and IS_BLACKWELL.
_hw_demotion() {
    [[ -n "${SSOT_FALLBACK:-}" ]] || return 0
    if [[ "${VIRT:-}" == "wsl" || -e /dev/dxg ]]; then
        printf 'wsl'
    elif [[ "${IS_BLACKWELL:-false}" == "true" ]]; then
        printf 'blackwell-safety'
    elif ! compgen -G "/dev/dri/renderD*" >/dev/null && [[ ! -e /sys/class/drm/card0 ]]; then
        printf 'no-drm'
    fi
}

# The ladder, emitted as "role<TAB>source". Tiers, highest first:
#   1. mios.blade= / mios.role= on the cmdline, DIFFERENT from [blade].type
#   2. ROLE= in /etc/mios/role.conf                            (host tier)
#   3. [blade].type -- equivalently the generated karg       (vendor tier)
#   4. hardware demotion to [blade].fallback, tier 3 only
_resolve_role() {
    local karg conf
    karg="$(_cmdline_tok mios.blade)"
    [[ -n "$karg" ]] || karg="$(_cmdline_tok mios.role)"
    conf="$(_conf_get "$ROLE_CONF" ROLE)"

    if [[ -n "$karg" && "$karg" != "${SSOT_TYPE:-}" ]]; then
        printf '%s\t%s' "$karg" "cmdline"
        return 0
    fi
    if [[ -n "$conf" ]]; then
        printf '%s\t%s' "$conf" "$ROLE_CONF"
        return 0
    fi

    local vendor="${SSOT_TYPE:-}"
    if [[ -z "$vendor" ]]; then
        # No vendor tier and no karg: refuse to invent an archetype.
        printf '\t%s' "unresolved"
        return 0
    fi
    local demote
    demote="$(_hw_demotion)"
    if [[ -n "$demote" ]]; then
        printf '%s\t%s' "$SSOT_FALLBACK" "hardware:${demote}"
        return 0
    fi
    printf '%s\t%s' "$vendor" "[blade].type"
}

# [blade.role_aliases] maps a legacy spelling onto an archetype. Exact match
# only -- a glob would accept `k3sx` and resolve it to zero capabilities.
_canon_role() {
    local role="$1" pair
    for pair in ${SSOT_ALIASES:-}; do
        if [[ "$role" == "${pair%%=*}" ]]; then
            printf '%s' "${pair#*=}"
            return 0
        fi
    done
    printf '%s' "$role"
}

_caps_for() {
    local var
    var="SSOT_ARCH_$(printf '%s' "$1" | tr -c '[:alnum:]' '_' | tr '[:lower:]' '[:upper:]')"
    printf '%s' "${!var:-}"
}

_is_legal_cap() {
    local cap="$1" known
    for known in ${SSOT_CAPS_LEGAL:-}; do
        [[ "$cap" == "$known" ]] && return 0
    done
    return 1
}

# FEATURES are UNIONED across tiers, never replaced: blade.d is wiped on every
# role-apply run, so a skipped tier is a capability that vanishes.
_resolve_features() {
    printf '%s,%s' "$(_cmdline_tok mios.features)" "$(_conf_get "$ROLE_CONF" FEATURES)"
}

# The addresses a seat offloads to, as "<label><TAB><url>" lines. ${MIOS_PORT_*}
# placeholders are expanded from the environment the resolver populates -- the
# same values every consumer sees, never a second resolution of the TOML.
_ssot_offload_targets() {
    python3 - <<'TARGETS' 2>/dev/null || true
import os
import re
import sys

sys.path.insert(0, os.environ.get("MIOS_USR_DIR", "/usr/lib/mios"))
import mios_toml  # noqa: E402

d = mios_toml.load_merged()
seen = set()
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

def expand(url):
    """Substitute from the environment; leave an unset name visible rather
    than silently emitting a broken URL."""
    return _VAR.sub(lambda m: os.environ.get(m.group(1), m.group(0)), url)

def emit(label, url):
    url = expand(str(url or "").strip())
    if url and url not in seen:
        seen.add(url)
        print("%s\t%s" % (label, url))

emit("ai", (d.get("ai") or {}).get("endpoint"))
emit("search", (d.get("search") or {}).get("endpoint"))
for name, cfg in sorted((d.get("nodes") or {}).items()):
    if isinstance(cfg, dict):
        emit("node:%s" % name, cfg.get("endpoint"))
TARGETS
}
# Is a URL host this machine? A seat's targets are elsewhere; a blade's are its
# own loopback.
_url_is_local() {
    case "$1" in
        *://localhost|*://localhost:*|*://localhost/*) return 0 ;;
        *://127.0.0.1|*://127.0.0.1:*|*://127.0.0.1/*) return 0 ;;
    esac
    return 1
}

# Auth posture per ADR-0016 D5. Emits: <verdict>\t<url>\t<detail>, where verdict
# is local|armed|exposed|unknown. Verdict table: TASKS.md task_271.
_ssot_security() {
    python3 - <<'SECPY' 2>/dev/null || true
import os
import sys

sys.path.insert(0, os.environ.get("MIOS_USR_DIR", "/usr/lib/mios"))
import mios_toml  # noqa: E402

sec = (mios_toml.load_merged().get("security") or {})

def flag(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

print("require=%d" % (1 if flag(sec.get("api_require_auth")) else 0))
print("bind=%s" % (str(sec.get("principal_bind_mode") or "off").strip().lower()))
SECPY
}

_auth_posture() {
    local ep
    ep="$(_ssot_offload_targets | awk -F'\t' '$1 == "ai" { print $2; exit }')"
    if [[ -z "$ep" ]]; then
        printf 'unknown\t\tno [ai].endpoint resolved'
        return 0
    fi
    if _url_is_local "$ep"; then
        printf 'local\t%s\tloopback front door -- single-tenant, no key needed' "$ep"
        return 0
    fi

    local sec require bind
    sec="$(_ssot_security)"
    require="$(printf '%s\n' "$sec" | sed -n 's/^require=//p')"
    bind="$(printf '%s\n' "$sec" | sed -n 's/^bind=//p')"
    [[ -n "$require" ]] || require=0
    [[ -n "$bind" ]] || bind=off

    local gaps=""
    [[ "$require" == "1" ]] || gaps="[security].api_require_auth is off"
    if [[ "$bind" == "off" ]]; then
        [[ -z "$gaps" ]] || gaps="${gaps}; "
        gaps="${gaps}[security].principal_bind_mode is off"
    fi
    if [[ -z "$gaps" ]]; then
        printf 'armed\t%s\tauth required, principal bound (%s)' "$ep" "$bind"
    else
        printf 'exposed\t%s\t%s' "$ep" "$gaps"
    fi
}

_target_for() {
    printf 'mios-%s.target' "$1"
}

_ssot_placement() {
    python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ.get("MIOS_USR_DIR", "/usr/lib/mios"))
import mios_toml

blade = (mios_toml.load_merged().get("blade") or {})
placement = blade.get("placement") or {}
collapse = blade.get("collapse") or {}

legal_tiers = {"local", "localhost", "cluster"}

raw_fo = placement.get("failover_order")
if raw_fo is not None and len(raw_fo) == 0:
    sys.stderr.write("ERROR: empty failover_order\n")
    sys.exit(1)

fo = raw_fo if raw_fo is not None else ["local", "localhost", "cluster"]
if isinstance(fo, str):
    fo = [fo]

if not fo:
    sys.stderr.write("ERROR: empty failover_order\n")
    sys.exit(1)

for t in fo:
    if t not in legal_tiers:
        sys.stderr.write(f"ERROR: unknown failover tier: {t}\n")
        sys.exit(1)

print("failover_order=%s" % " ".join(fo))
print("dwell_s=%s" % str(collapse.get("dwell_s") or 30))
print("recover_dwell_s=%s" % str(collapse.get("recover_dwell_s") or 120))
print("fail_checks=%s" % str(collapse.get("fail_checks") or 3))
PY
}

_resolve_placement_failover() {
    local target="$1" status="${2:-failed}"
    local p_info
    if ! p_info="$(_ssot_placement 2>&1)"; then
        printf '%s\n' "$p_info" >&2
        return 1
    fi
    local order rec_dwell fail_c
    order="$(printf '%s\n' "$p_info" | sed -n 's/^failover_order=//p')"
    rec_dwell="$(printf '%s\n' "$p_info" | sed -n 's/^recover_dwell_s=//p')"
    fail_c="$(printf '%s\n' "$p_info" | sed -n 's/^fail_checks=//p')"

    local -a order_arr
    read -ra order_arr <<< "$order"

    local state_dir="${FAILOVER_STATE_DIR:-/run/mios/failover}"
    mkdir -p "$state_dir" 2>/dev/null || true
    local state_file="${state_dir}/${target}.state"
    local now="${NOW_OVERRIDE:-${CURRENT_TIME:-$(date +%s)}}"

    local current_tier="${order_arr[0]}"
    local fail_count=0
    local last_ts=0
    local last_transition_ts=0
    local flaps=0

    if [[ -r "$state_file" ]]; then
        local st_tier st_fc st_lts st_tr_ts st_flaps
        st_tier="$(sed -n 's/^current_tier=//p' "$state_file")"
        [[ -n "$st_tier" ]] || st_tier="$(sed -n 's/^last_tier=//p' "$state_file")"
        st_fc="$(sed -n 's/^fail_count=//p' "$state_file")"
        st_lts="$(sed -n 's/^last_ts=//p' "$state_file")"
        st_tr_ts="$(sed -n 's/^last_transition_ts=//p' "$state_file")"
        st_flaps="$(sed -n 's/^flaps=//p' "$state_file")"

        [[ -n "$st_tier" ]] && current_tier="$st_tier"
        [[ -n "$st_fc" ]] && fail_count="$st_fc"
        [[ -n "$st_lts" ]] && last_ts="$st_lts"
        [[ -n "$st_tr_ts" ]] && last_transition_ts="$st_tr_ts"
        [[ -n "$st_flaps" ]] && flaps="$st_flaps"
    fi

    local cur_idx=0
    local found=0
    local i
    for i in "${!order_arr[@]}"; do
        if [[ "${order_arr[$i]}" == "$current_tier" ]]; then
            cur_idx=$i
            found=1
            break
        fi
    done
    if (( ! found )); then
        cur_idx=0
        current_tier="${order_arr[0]}"
        fail_count=0
    fi

    local max_fails="${fail_c:-3}"
    local recover_window="${rec_dwell:-120}"

    if [[ "$status" == "ok" || "$status" == "healthy" ]]; then
        fail_count=0
        cat > "$state_file" <<EOF
current_tier=$current_tier
last_tier=$current_tier
fail_count=$fail_count
last_ts=$now
last_transition_ts=$last_transition_ts
flaps=$flaps
EOF
        printf '%s\tok\t0\n' "$current_tier"
        return 0
    fi

    # status == "failed"
    fail_count=$(( fail_count + 1 ))

    if (( fail_count >= max_fails )); then
        local next_idx=$(( cur_idx + 1 ))
        if (( next_idx < ${#order_arr[@]} )); then
            if (( last_transition_ts > 0 )); then
                local elapsed=$(( now - last_transition_ts ))
                if (( elapsed < recover_window )); then
                    flaps=$(( flaps + 1 ))
                    cat > "$state_file" <<EOF
current_tier=$current_tier
last_tier=$current_tier
fail_count=$fail_count
last_ts=$now
last_transition_ts=$last_transition_ts
flaps=$flaps
EOF
                    printf '%s\tflapping_suppressed\t%s\n' "$current_tier" "$flaps"
                    return 0
                fi
            fi

            current_tier="${order_arr[$next_idx]}"
            fail_count=0
            last_transition_ts=$now
            flaps=$(( flaps + 1 ))

            cat > "$state_file" <<EOF
current_tier=$current_tier
last_tier=$current_tier
fail_count=$fail_count
last_ts=$now
last_transition_ts=$last_transition_ts
flaps=$flaps
EOF
            printf '%s\ttransitioned\t%s\n' "$current_tier" "$flaps"
            return 0
        fi
    fi

    cat > "$state_file" <<EOF
current_tier=$current_tier
last_tier=$current_tier
fail_count=$fail_count
last_ts=$now
last_transition_ts=$last_transition_ts
flaps=$flaps
EOF
    if [[ ! -r "$state_file" || $last_ts -eq 0 ]]; then
        printf '%s\tassigned\t1\n' "$current_tier"
    else
        printf '%s\tfailed_checks\t%s\n' "$current_tier" "$fail_count"
    fi
}

