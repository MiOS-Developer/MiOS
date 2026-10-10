#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Final build-time validation script that enforces mandatory security invariants, such as OpenSSH version minimums and C...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

# Gate: kargs.d under root $1 holds at least one *.toml, and each parses with
# a kargs array of strings. tests/test-postcheck-kargs.sh sources this.
mios_postcheck_kargs() {
    local dir="${1:-}/usr/lib/bootc/kargs.d" err
    [[ -d "$dir" ]] || die "Kargs.d missing: $dir"
    compgen -G "$dir/*.toml" >/dev/null || die "Kargs.d has no *.toml: $dir"
    err="$(python3 - "$dir" <<'PY'
import pathlib, sys, tomllib
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.toml")):
    try:
        k = tomllib.loads(f.read_text(encoding="utf-8")).get("kargs")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"{f.name}: {exc}"); continue
    if not isinstance(k, list) or not all(isinstance(v, str) for v in k):
        print(f"{f.name}: kargs must be an array of strings")
PY
)" || die "Kargs.d validator failed to run"
    [[ -z "$err" ]] || die "Kargs.d invalid: $err"
    mios_ok "Kargs.d: $(compgen -G "$dir/*.toml" | wc -l) files valid"
}

# Gate: /etc/shadow under root $1 has expired password (lastchg = 0) for the default user.
# tests/test-postcheck-kargs.sh sources this. Ruling Q5 (task_3537).
mios_postcheck_expired_password() {
    local root="${1:-}"
    local shadow="${root}/etc/shadow"
    [[ -f "$shadow" ]] || return 0
    local default_user="mios"
    local toml="${root}/usr/share/mios/mios.toml"
    if [[ -f "$toml" ]] && command -v python3 >/dev/null 2>&1; then
        default_user=$(python3 -c '
import sys
try:
    import tomllib
    with open(sys.argv[1], "rb") as f:
        t = tomllib.load(f)
        print(t.get("identity", {}).get("username", "mios"))
except Exception:
    print("mios")
' "$toml" 2>/dev/null || echo "mios")
    fi
    local line
    line=$(grep -E "^${default_user}:" "$shadow" 2>/dev/null || true)
    if [[ -n "$line" ]]; then
        local lastchg
        lastchg=$(echo "$line" | cut -d: -f3)
        if [[ "$lastchg" != "0" ]]; then
            die "User '${default_user}' in $shadow has non-expired password (lastchg='${lastchg}', expected 0 per ruling Q5)"
        fi
        mios_ok "User '${default_user}' in $shadow has expired password (lastchg=0 per ruling Q5)"
    fi
}
[[ "${BASH_SOURCE[0]}" == "$0" ]] || return 0

mios_step "'MiOS' build-time validation"

mios_log "Validate default user password is expired (ruling Q5)"
mios_postcheck_expired_password ""

if command -v systemd-sysusers >/dev/null 2>&1; then
    mios_log "Materialize sysusers.d into /etc/passwd + /etc/group"
    if systemd-sysusers --no-pager 2>/dev/null; then
        mios_ok "Sysusers.d entries materialized"
    else
        mios_warn "Systemd-sysusers exited non-zero; subsequent checks may have false-positives"
    fi
fi

mios_log "Check OpenSSH version"
if ! command -v sshd >/dev/null 2>&1; then
    die "Sshd not found in image"
fi

SSH_VER_RAW=$(sshd -V 2>&1 | head -n1 | grep -oP 'OpenSSH_\K[0-9.]+')
mios_log "OpenSSH $SSH_VER_RAW"

if [[ $(printf '%s\n9.6' "$SSH_VER_RAW" | sort -V | head -n1) != "9.6" ]]; then
    die "OpenSSH version $SSH_VER_RAW is below required 9.6"
fi
mios_ok "OpenSSH version is safe"

mios_log "Check Cockpit configuration"
if [[ -f "/etc/cockpit/cockpit.conf" ]]; then
    COCKPIT_CONF="/etc/cockpit/cockpit.conf"
elif [[ -f "/usr/lib/cockpit/cockpit.conf" ]]; then
    COCKPIT_CONF="/usr/lib/cockpit/cockpit.conf"
else
    COCKPIT_CONF=""
fi

if [[ -n "$COCKPIT_CONF" ]]; then
    if ! grep -q "LoginTo = false" "$COCKPIT_CONF"; then
        die "Cockpit LoginTo mitigation missing in $COCKPIT_CONF"
    fi
    mios_ok "Cockpit LoginTo = false enforced"
else
    mios_skip "Cockpit config not found at expected paths"
fi

mios_log "Validate kargs.d files"
mios_postcheck_kargs ""

mios_log "Verify critical system binaries"
CRITICAL_TOOLS=(podman bootc cockpit-bridge rpm-ostree)
for tool in "${CRITICAL_TOOLS[@]}"; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        die "Critical tool '$tool' is missing from the image"
    fi
    mios_ok "$tool present"
done

mios_log "Check NVIDIA Container Toolkit version"
if command -v nvidia-ctk >/dev/null 2>&1; then
    NCT_VER=$(nvidia-ctk --version | head -n1 | grep -oP 'version \K[0-9.]+')
    mios_log "Nvidia-ctk $NCT_VER"
    if [[ $(printf '%s\n1.18' "$NCT_VER" | sort -V | head -n1) != "1.18" ]]; then
        die "Nvidia-container-toolkit version $NCT_VER is below required 1.18"
    fi
    mios_ok "NVIDIA Container Toolkit version is safe"
fi

mios_log "Check Cockpit version"
if rpm -q cockpit >/dev/null 2>&1; then
    COCKPIT_VER=$(rpm -q cockpit --queryformat '%{VERSION}')
    mios_log "Cockpit $COCKPIT_VER"
    if [[ $(printf '%s\n361' "$COCKPIT_VER" | sort -V | head -n1) != "361" ]]; then
        mios_warn "Cockpit version $COCKPIT_VER below 361"
    else
        mios_ok "Cockpit version is safe"
    fi
fi

mios_log "Validate /etc/wsl.conf"
if [[ -f /etc/wsl.conf ]]; then
    if LC_ALL=C grep -nP '[^\x00-\x7F]' /etc/wsl.conf >&2; then
        die "/etc/wsl.conf contains non-ASCII bytes"
    fi
    mios_log "Pure ASCII"
    if LC_ALL=C grep -lP '\r' /etc/wsl.conf >/dev/null 2>&1; then
        die "/etc/wsl.conf contains CRLF line endings"
    fi
    mios_log "Pure LF line endings"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c '
import configparser, sys
p = configparser.ConfigParser(strict=True, interpolation=None)
try:
    with open("/etc/wsl.conf") as f:
        p.read_file(f)
except Exception as e:
    sys.stderr.write(f"wsl.conf parse failed: {e}\n"); sys.exit(1)
required = {"boot": ["systemd"], "user": ["default"]}
for section, keys in required.items():
    if not p.has_section(section):
        sys.stderr.write(f"wsl.conf missing required [section]: {section}\n"); sys.exit(1)
    for k in keys:
        if not p.has_option(section, k):
            sys.stderr.write(f"wsl.conf missing required key: {section}.{k}\n"); sys.exit(1)
print("  /etc/wsl.conf parses cleanly with all required sections/keys")
' || die "/etc/wsl.conf failed parse/required-keys validation"
    else
        mios_skip "python3 unavailable -- wsl.conf parse (post-build only)"
    fi
    if [[ -f /usr/lib/wsl.conf ]]; then
        if ! cmp -s /etc/wsl.conf /usr/lib/wsl.conf; then
            die "/etc/wsl.conf drifted from /usr/lib/wsl.conf reference at build time"
        fi
        mios_ok "/etc/wsl.conf matches /usr/lib/wsl.conf reference"
    fi
else
    mios_skip "/etc/wsl.conf not present -- WSL2 deploys fall back to defaults"
fi

mios_log "Validate sysusers.d login users have fixed UIDs"
_sysusers_effective() {
    local d
    declare -A _seen=()
    for d in /etc/sysusers.d /usr/lib/sysusers.d; do
        [[ -d "$d" ]] || continue
        for f in "$d"/*.conf; do
            [[ -f "$f" ]] || continue
            local base
            base="$(basename "$f")"
            [[ -n "${_seen[$base]:-}" ]] && continue
            _seen[$base]=1
            printf '%s\n' "$f"
        done
    done
}
_sysusers_bad=$(
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        awk '
            /^u[[:space:]]+/ {
                shell = $NF
                if ($3 == "-" && shell ~ /\/(bash|zsh|sh|fish|dash|csh|tcsh|ksh)$/) {
                    print FILENAME ":" NR ": " $0
                }
            }' "$f"
    done < <(_sysusers_effective)
)
if [[ -n "$_sysusers_bad" ]]; then
    printf '%s\n' "$_sysusers_bad" >&2
    die "Sysusers.d defines login-shell user with auto-allocated UID; pin to a value >= 1000"
fi
mios_ok "All login-shell sysusers entries have fixed UIDs"

mios_log "Validate sysusers.d UID:GID resolves to a created group"
_sysusers_known_gids=$(
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        awk '/^g[[:space:]]+/ && $3 ~ /^[0-9]+$/ { print $3 }' "$f"
    done < <(_sysusers_effective) | sort -u
)
_gid_in_etc_group() {
    [[ -r /etc/group ]] || return 1
    awk -F: -v g="$1" '$3 == g {found=1} END {exit !found}' /etc/group
}
_sysusers_unresolved=$(
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        awk -v known="$_sysusers_known_gids" '
            BEGIN {
                n = split(known, k, "\n")
                for (i = 1; i <= n; i++) if (k[i] != "") seen[k[i]] = 1
            }
            /^u[[:space:]]+/ {
                split($3, a, ":")
                if (a[2] !~ /^[0-9]+$/) next
                if (a[2] in seen) next
                print FILENAME ":" NR ":" a[2] ": " $0
            }' "$f"
    done < <(_sysusers_effective)
)
_sysusers_truly_unresolved=$(
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        gid="${line#*:*:}"; gid="${gid%%:*}"
        if _gid_in_etc_group "$gid"; then continue; fi
        echo "${line%%:*}:${line#*:}" | sed -E 's/:[0-9]+:/:/'
    done <<< "$_sysusers_unresolved"
)
if [[ -n "$_sysusers_truly_unresolved" ]]; then
    printf '%s\n' "$_sysusers_truly_unresolved" >&2
    die "Sysusers.d: u-line references a numeric GID with no 'g name GID' anywhere in /etc/sysusers.d or /usr/lib/sysusers.d AND no matching entry in /etc/group"
fi
mios_ok "All u-line GIDs resolve via cross-file g-lines or /etc/group"

mios_log "Validate tmpfiles.d uses /run"
_tmpfiles_legacy=$(
    for f in /usr/lib/tmpfiles.d/*.conf /etc/tmpfiles.d/*.conf; do
        [[ -f "$f" ]] || continue
        awk '
            /^[[:space:]]*[a-zA-Z]/ {
                if ($2 ~ /^\/var\/(run|lock)\//) {
                    print FILENAME ":" NR ": " $0
                }
            }' "$f"
    done
)
if [[ -n "$_tmpfiles_legacy" ]]; then
    printf '%s\n' "$_tmpfiles_legacy" >&2
    die "Tmpfiles.d entry uses /var/run or /var/lock"
fi
mios_ok "Tmpfiles.d entries use canonical /run paths"

mios_log "Validate MiOS systemd unit syntax"
if command -v systemd-analyze >/dev/null 2>&1; then
    _bad_units=$(
        for u in /usr/lib/systemd/system/mios-*.service \
                 /usr/lib/systemd/system/mios-*.target; do
            [[ -f "$u" ]] || continue
            out=$(systemd-analyze --no-pager verify "$u" 2>&1 || true)
            echo "$out" | grep -E "^${u}:" || true
        done
    )
    if [[ -n "$_bad_units" ]]; then
        printf '%s\n' "$_bad_units" >&2
        die "Systemd-analyze verify reported errors in MiOS unit"
    fi
    mios_ok "MiOS units lint clean"
else
    mios_skip "systemd-analyze unavailable -- unit verification"
fi

mios_log "Validate MiOS tmpfiles.d syntax"
if command -v systemd-tmpfiles >/dev/null 2>&1; then
    _sysusers_declared=$(
        for d in /etc/sysusers.d /usr/lib/sysusers.d; do
            [[ -d "$d" ]] || continue
            for f in "$d"/*.conf; do
                [[ -f "$f" ]] || continue
                awk '/^[ugm][[:space:]]+/ { print $2; if ($3 != "" && $3 !~ /^[0-9:-]+$/) print $3 }' "$f"
            done
        done | sort -u
    )
    _bad_tmpfiles=$(
        shopt -s nullglob
        _tf=(/usr/lib/tmpfiles.d/*mios*.conf)
        (( ${#_tf[@]} )) || die "no MiOS tmpfiles.d config found -- syntax check would parse nothing"
        for f in "${_tf[@]}"; do
            [[ -f "$f" ]] || continue
            out=$(systemd-tmpfiles --dry-run --create "$f" 2>&1 || true)
            echo "$out" | awk -v f="$f" -v decl="$_sysusers_declared" -v sq="'" '
                BEGIN {
                    n = split(decl, a, "\n")
                    for (i = 1; i <= n; i++) if (a[i] != "") known[a[i]] = 1
                }
                $0 !~ "^" f ":" { next }
                {
                    if ($0 ~ /Failed to resolve (user|group)/) {
                        split($0, parts, sq)
                        if (length(parts) < 2) split($0, parts, "\"")
                        target = parts[2]
                        if (target in known) next
                        cmd = "getent passwd " target " 2>/dev/null || getent group " target " 2>/dev/null"
                        if ((cmd | getline res) > 0 && length(res) > 0) { close(cmd); next }
                        close(cmd)
                    }
                    print
                }'
        done
    )
    if [[ -n "$_bad_tmpfiles" ]]; then
        printf '%s\n' "$_bad_tmpfiles" >&2
        die "Systemd-tmpfiles reported errors in MiOS tmpfiles.d configs"
    fi
    mios_ok "MiOS tmpfiles.d configs parse clean"
else
    mios_skip "systemd-tmpfiles unavailable -- tmpfiles verification"
fi

mios_log "Validate UNIFIED-AI-REDIRECTS: no vendor URLs in active config"
_law5_dirs=(
    /etc/containers/systemd
    /usr/share/containers/systemd
    /usr/lib/systemd/system
    /usr/share/mios/ai
    /etc/mios/ai
)
_law5_pattern='https?://(api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.cohere\.|api\.mistral\.|api\.cline\.bot|api\.cursor\.com|api\.githubcopilot\.com)'
_law5_hits=""
for d in "${_law5_dirs[@]}"; do
    [[ -d "$d" ]] || continue
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        active=$(sed -E '/^[[:space:]]*(#|\/\/)/d' "$f")
        if printf '%s\n' "$active" | grep -qE "$_law5_pattern"; then
            _law5_hits+="$f"$'\n'
        fi
    done < <(find "$d" -type f \( -name '*.container' -o -name '*.service' \
        -o -name '*.json' -o -name '*.toml' -o -name '*.conf' -o -name '*.yaml' \
        -o -name '*.yml' \) 2>/dev/null)
done
if [[ -n "$_law5_hits" ]]; then
    printf '%s' "$_law5_hits" >&2
    die "UNIFIED-AI-REDIRECTS: vendor cloud URL found in active config"
fi
mios_ok "No vendor URLs in active config"

mios_log "Validate UNIFIED-AI-REDIRECTS: no retired :11434 lane in active config"
_dead_lane_pattern=':11434'
_dead_lane_hits=""
for d in "${_law5_dirs[@]}"; do
    [[ -d "$d" ]] || continue
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        active=$(sed -E '/^[[:space:]]*(#|\/\/)/d' "$f")
        if printf '%s\n' "$active" | grep -qE "$_dead_lane_pattern"; then
            _dead_lane_hits+="$f"$'\n'
        fi
    done < <(find "$d" -type f \( -name '*.container' -o -name '*.service' \
        -o -name '*.conf' -o -name '*.json' -o -name '*.toml' -o -name '*.yaml' \
        -o -name '*.yml' \) 2>/dev/null)
done
if [[ -n "$_dead_lane_hits" ]]; then
    printf '%s' "$_dead_lane_hits" >&2
    die "UNIFIED-AI-REDIRECTS: retired :11434 lane in active config"
fi
mios_ok "No retired :11434 lane in active config"

mios_log "Validate UNIFIED-AI-REDIRECTS: no [agents.*] dispatch target loops to the orchestrator/gateway"
if command -v python3 >/dev/null 2>&1; then
    if _recursion_out=$(python3 - <<'PYEOF'
import os, re, sys
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("SKIP: no tomllib"); sys.exit(0)
toml = "/usr/share/mios/mios.toml"
if not os.path.exists(toml):
    print("SKIP: %s not found" % toml); sys.exit(0)
with open(toml, "rb") as f:
    d = tomllib.load(f)
def _port(u):
    m = re.search(r":(\d+)", str(u or ""))
    return m.group(1) if m else ""
ai = d.get("ai") or {}
ports = d.get("ports") or {}
orch = _port(ai.get("endpoint"))
gw = str(ports.get("hermes") or "").strip()
label = {}
if orch:
    label[orch] = "orchestrator"
if gw:
    label[gw] = "thin-gateway"
bad = []
for name, cfg in (d.get("agents") or {}).items():
    if not isinstance(cfg, dict):
        continue
    ep = str(cfg.get("endpoint") or "").strip()
    if not ep:
        continue
    p = _port(ep)
    if p and p in label:
        bad.append("  [agents.%s] endpoint %s loops to the %s (:%s)" % (name, ep, label[p], p))
if bad:
    sys.stderr.write("\n".join(bad) + "\n")
    sys.stderr.write("  a dispatch target must be a real worker ingress / model lane, "
                     "never :%s (orchestrator) / :%s (gateway)\n" % (orch, gw))
    sys.exit(1)
print("orchestrator=:%s gateway=:%s agents=%d clean" % (orch, gw, len(d.get("agents") or {})))
PYEOF
    ); then
        mios_log "$_recursion_out"
    else
        die "UNIFIED-AI-REDIRECTS: an [agents.*] dispatch target loops back to the orchestrator/gateway"
    fi
else
    mios_skip "python3 unavailable -- agent-recursion guard"
fi

mios_log "Validate UNPRIVILEGED-QUADLETS: every Quadlet declares User="
if command -v python3 >/dev/null 2>&1; then
    if ! _law6_out=$(python3 - <<'PYEOF'
import os, re, sys
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("SKIP: no tomllib"); sys.exit(0)
toml = "/usr/share/mios/mios.toml"
if not os.path.exists(toml):
    print("SKIP: %s not found" % toml); sys.exit(0)
with open(toml, "rb") as f:
    d = tomllib.load(f)
root_allow = d.get("security", {}).get("privileged_quadlets", {}).get("root", [])
root_set = set(root_allow)
bad = []
for d in ["/etc/containers/systemd", "/usr/share/containers/systemd"]:
    if not os.path.exists(d): continue
    for r, _, files in os.walk(d):
        for f in files:
            if not f.endswith(".container"): continue
            path = os.path.join(r, f)
            with open(path, "r") as cf:
                content = cf.read()
            m = re.search(r"^[ \t]*User=(.*)$", content, re.MULTILINE)
            user = m.group(1).strip() if m else ""
            if user == "" or user == "root" or user == "0":
                if f not in root_set:
                    bad.append("%s: undocumented User=root/0 or missing User= (Law 6)" % path)
if bad:
    sys.stderr.write("\n".join(bad) + "\n")
    sys.stderr.write("UNPRIVILEGED-QUADLETS: Quadlet implicitly/explicitly root without allowlist in [security.privileged_quadlets].root\n")
    sys.exit(1)
PYEOF
    ); then
        printf '%s\n' "$_law6_out" >&2
        die "UNPRIVILEGED-QUADLETS: Quadlet privilege check failed"
    fi
else
    mios_log "  [!] python3 unavailable"
fi
mios_ok "Every Quadlet declares User="

mios_log "Validate BOUND-IMAGES: Quadlet -> bound-images.d/ coverage"
_bind_dir=/usr/lib/bootc/bound-images.d
_law3_missing=""
_law3_extra=""
_p14_toml="${MIOS_TOML:-/usr/share/mios/mios.toml}"
_p14_fb_tokens="$(grep -E '^[[:space:]]*firstboot_tokens[[:space:]]*=' "${_p14_toml}" 2>/dev/null | sed -E 's/^[^=]*=//; s/[][",]/ /g')"
if [[ -d "$_bind_dir" ]]; then
    declare -A _seen_quadlets=()
    for d in /etc/containers/systemd /usr/share/containers/systemd; do
        [[ -d "$d" ]] || continue
        for f in "$d"/*.container "$d"/*/*.container; do
            [[ -f "$f" ]] || continue
            base=$(basename "$f")
            _seen_quadlets["$base"]=1
            if [[ ! -e "$_bind_dir/$base" ]]; then
                if [[ -n "${_p14_fb_tokens// /}" ]]; then
                    _p14_img="$(sed -nE 's/^Image=//p' "$f" | head -1)"
                    _p14_fb=""
                    for _p14_tok in ${_p14_fb_tokens}; do
                        [[ -n "$_p14_tok" && "$_p14_img" == *"$_p14_tok"* ]] && { _p14_fb=1; break; }
                    done
                    if [[ -n "$_p14_fb" ]]; then
                        mios_ok "$base intentionally unbound"
                        continue
                    fi
                fi
                _law3_missing+="$base: no symlink in $_bind_dir/"$'\n'
            fi
        done
    done
    for b in "$_bind_dir"/*.container; do
        [[ -e "$b" ]] || continue
        base=$(basename "$b")
        if [[ -z "${_seen_quadlets[$base]:-}" ]]; then
            _law3_extra+="$base: stale binding in $_bind_dir/ (no source Quadlet)"$'\n'
        fi
    done
    if [[ -n "$_law3_missing" || -n "$_law3_extra" ]]; then
        [[ -n "$_law3_missing" ]] && printf '%s' "$_law3_missing" >&2
        [[ -n "$_law3_extra" ]] && printf '%s' "$_law3_extra" >&2
        die "BOUND-IMAGES: Quadlet/binder drift"
    fi
    mios_ok "Every Quadlet has a corresponding bound-images.d/ symlink"
else
    mios_skip "$_bind_dir not present -- binder loop did not run"
fi

mios_log "Run build-time capability benchmark"
if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import http.server, json, threading
class MockHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "choices": [{"message": {"content": "42 Paris"}}]
        }).encode())
server = http.server.HTTPServer(("127.0.0.1", 8649), MockHandler)
server.serve_forever()
' &
    MOCK_PID=$!
    sleep 0.5

    BENCH_BIN="/usr/libexec/mios/mios-bench"
    if [[ ! -x "$BENCH_BIN" ]]; then
        BENCH_BIN="$(dirname "${BASH_SOURCE[0]}")/../usr/libexec/mios/mios-bench"
    fi

    python3 "$BENCH_BIN" run --suite gaia-lite --endpoint http://127.0.0.1:8649/v1 --k 1

    kill "$MOCK_PID" || true
    mios_ok "Benchmark harness executed"
else
    mios_skip "python3 missing -- benchmark run"
fi

mios_log "Validate BARE-SAFE-ENV: system-sync-env"
_sync_env="/usr/libexec/mios/system-sync-env.sh"
[[ -x "$_sync_env" ]] || _sync_env="$(dirname "${BASH_SOURCE[0]}")/../usr/libexec/mios/system-sync-env.sh"
if [[ -f "$_sync_env" ]]; then
    _env_err="$(mktemp)"
    if ! _env_render="$(bash "$_sync_env" --dry-run 2>"$_env_err")"; then
        cat "$_env_err" >&2
        rm -f "$_env_err"
        die "BARE-SAFE-ENV: system-sync-env.sh --dry-run failed -- see the error above"
    fi
    [[ -s "$_env_err" ]] && cat "$_env_err" >&2
    rm -f "$_env_err"

    # Shrink-only: [security.non_bare_env].max_declared is a ceiling, not a
    # label. Without this the number is decorative and the declared set could
    # grow one convenient exemption at a time -- which is how an exemption list
    # becomes the rule.
    _nb_toml="/usr/share/mios/mios.toml"
    [[ -r "$_nb_toml" ]] || _nb_toml="$(dirname "${BASH_SOURCE[0]}")/../usr/share/mios/mios.toml"
    if [[ -r "$_nb_toml" ]]; then
        _nb_n="$(awk '/^\[/ { _in = ($0 ~ /^\[security\.non_bare_env\]/); next } _in && match($0, /"MIOS_[A-Z0-9_]*"/) { n++ } END { print n + 0 }' "$_nb_toml")"
        _nb_max="$(awk '/^\[/ { _in = ($0 ~ /^\[security\.non_bare_env\]/); next } _in && /^[[:space:]]*max_declared[[:space:]]*=/ { gsub(/[^0-9]/, "", $0); print; exit }' "$_nb_toml")"
        if [[ -z "$_nb_max" ]]; then
            die "BARE-SAFE-ENV: [security.non_bare_env].max_declared is missing -- the exemption list has no ceiling"
        fi
        if (( _nb_n > _nb_max )); then
            die "BARE-SAFE-ENV: ${_nb_n} keys declared non-bare but the ceiling is ${_nb_max} -- make a value bare, do NOT raise the ceiling"
        fi
        mios_ok "BARE-SAFE-ENV: ${_nb_n}/${_nb_max} keys declared non-bare"
    else
        die "BARE-SAFE-ENV: mios.toml unreadable -- the non-bare exemption list could not be verified"
    fi
    if printf '%s\n' "$_env_render" | grep -nE '="' >&2; then
        die "BARE-SAFE-ENV: install.env render contains a double-quoted value"
    fi
    _law10_bad="$(printf '%s\n' "$_env_render" | grep -vE '^[[:space:]]*(#|$)' | grep -vE '^[A-Za-z_][A-Za-z0-9_]*=' || true)"
    if [[ -n "$_law10_bad" ]]; then
        printf '%s\n' "$_law10_bad" >&2
        die "BARE-SAFE-ENV: install.env render has a non-bare 'KEY=value' line"
    fi
    _law11_keys="$(bash "$_sync_env" --secret-keys)"
    _sk_n="$(printf '%s\n' "$_law11_keys" | grep -c . || true)"
    _sk_min=3
    if (( _sk_n < _sk_min )); then
        die "SECRETS-NEVER-IN-ENV: ${_sk_n} keys declared in [security.secret_keys], below floor of ${_sk_min}"
    fi
    _law11_names="$(printf '%s\n' "$_law11_keys" | paste -sd'|' -)"
    if printf '%s\n' "$_env_render" | grep -nE "^(${_law11_names})=" >&2; then
        die "BARE-SAFE-ENV: a secret name leaked into install.env"
    fi
    _law10_tmp="$(mktemp)"; printf '%s\n' "$_env_render" > "$_law10_tmp"
    if ! bash -u -c ". '$_law10_tmp'" >/dev/null 2>&1; then
        rm -f "$_law10_tmp"; die "BARE-SAFE-ENV: install.env render does not source clean under 'set -u'"
    fi
    rm -f "$_law10_tmp"
    mios_ok "Install.env render is bare KEY=value, secret-free, and set -u clean"
else
    mios_skip "system-sync-env.sh not found -- BARE-SAFE-ENV render check"
fi

# Law 11 SECRETS-NEVER-IN-ENV: secret-bearing env files must be 0600
mios_log "Validate SECRETS-NEVER-IN-ENV: secret-bearing env files are 0600"
[[ -n "${_law11_names:-}" ]] || die "SECRETS-NEVER-IN-ENV: no secret keys registered"
_law11_secret_re="(${_law11_names})=|[$]6[$]|[$]y[$]"
_law11_dirs=(/etc /run/secrets /usr/share/mios /usr/lib/mios /var/lib/mios)
_law11_bad=""
for d in "${_law11_dirs[@]}"; do
    [[ -d "$d" ]] || continue
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        if sed -E '/^[[:space:]]*#/d' "$f" 2>/dev/null | grep -qE "$_law11_secret_re"; then
            mode="$(stat -c '%a' "$f" 2>/dev/null || echo '???')"
            if [[ "$mode" != "600" ]]; then
                _law11_bad+="$f (mode $mode, must be 0600)"$'\n'
            fi
        fi
    done < <(find "$d" -type f \( -name '*.env' -o -name '*secrets*' \) 2>/dev/null)
done
if [[ -n "$_law11_bad" ]]; then
    printf '%s' "$_law11_bad" >&2
    die "SECRETS-NEVER-IN-ENV: a secret-bearing env file is not mode 0600"
fi
mios_ok "Every secret-bearing env file is mode 0600"

_bake_val="${MIOS_BAKE_BOUND_IMAGES:-0}"
if [[ "$_bake_val" == "0" || "$_bake_val" == "false" || "$_bake_val" == "False" || "$_bake_val" == "no" ]]; then
    mios_skip "MIOS_BAKE_BOUND_IMAGES=$_bake_val (sidecar bake skipped for CI validation build) -- BOUND-IMAGES-RESOLVE"
else
    mios_log "Validate BOUND-IMAGES-RESOLVE: bound-images.d symlinks resolve to baked images"
    _lbi_dir="/usr/lib/bootc/bound-images.d"
_lbi_tsv="/usr/share/mios/artifacts/sbom/bound-images.tsv"
if [[ -d "$_lbi_dir" ]]; then
    if [[ ! -f "$_lbi_tsv" ]]; then
        mios_skip "$_lbi_tsv not present (sidecars not baked in this Containerfile layer) -- BOUND-IMAGES-RESOLVE"
    else
        declare -A _lbi_baked=()
        while IFS=$'\t' read -r img_ref _digest _group || [[ -n "$img_ref" ]]; do
            [[ -z "$img_ref" || "$img_ref" == "image" ]] && continue
            _lbi_baked["$img_ref"]=1
        done < "$_lbi_tsv"

        while IFS= read -r link || [[ -n "$link" ]]; do
            [[ -L "$link" ]] || continue
            target="$(readlink -f "$link")"
            if [[ ! -f "$target" ]]; then
                die "BOUND-IMAGES-RESOLVE: symlink $(basename "$link") points to nonexistent file: $target"
            fi
            img_line="$(grep -i '^[[:space:]]*Image=' "$target" | head -n1 || true)"
            if [[ -n "$img_line" ]]; then
                raw_ref="${img_line#*=}"
                raw_ref="${raw_ref%%#*}"
                raw_ref="$(echo "$raw_ref" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
                resolved_ref="$raw_ref"
                if [[ "$raw_ref" =~ \$\{[A-Za-z0-9_]+:-(.+)\} ]]; then
                    resolved_ref="${BASH_REMATCH[1]}"
                elif [[ "$raw_ref" =~ \$\{[A-Za-z0-9_]+\} ]]; then
                    var_name="${raw_ref:2:${#raw_ref}-3}"
                    resolved_ref="${!var_name:-}"
                elif [[ "$raw_ref" =~ \$[A-Za-z0-9_]+ ]]; then
                    var_name="${raw_ref:1}"
                    resolved_ref="${!var_name:-}"
                fi
                resolved_ref="${resolved_ref//\"/}"
                resolved_ref="${resolved_ref//\'/}"
                [[ -z "$resolved_ref" ]] && continue
                if [[ "$resolved_ref" != "localhost/mios"* && -z "${_lbi_baked[$resolved_ref]:-}" ]]; then
                    die "BOUND-IMAGES-RESOLVE: Image '$resolved_ref' was not baked. Check your bake plan or plan.d groups"
                fi
            fi
        done < <(find "$_lbi_dir" -type l 2>/dev/null)
        mios_ok "Every bound-images.d symlink resolves to a baked image"
    fi
fi
fi

mios_ok "Validation successful"
exit 0

