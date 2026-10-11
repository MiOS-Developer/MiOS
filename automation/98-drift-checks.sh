#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Source-tree drift fitness-functions (WS-0A).
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

PYTHON="python3"
_real_py="$(command -v python 2>/dev/null || true)"
if ! command -v python3 >/dev/null 2>&1 && [[ -f "${_real_py:-/nonexistent}" ]]; then
    _shim_dir="${TEMP:-${TMP:-/tmp}}/mios-py-bin"
    mkdir -p "$_shim_dir" 2>/dev/null || true
    # Copied unconditionally: the cache is what went stale, so there is no
    # cache. An mtime or size test only narrows the window; this closes it.
    for _shim in python3 python3.exe; do
        cp -f "$_real_py" "$_shim_dir/$_shim" 2>/dev/null || true
    done
    export PATH="$_shim_dir:$PATH"
fi

_self="${BASH_SOURCE[0]}"
_self_dir="$(cd "$(dirname "$_self")" && pwd)"
ROOT="${MIOS_DRIFT_CHECK_ROOT:-$(cd "$_self_dir/.." && pwd)}"
cd "$ROOT"
export MIOS_TOML_ROOT="${MIOS_TOML_ROOT:-$ROOT}"
_SOFT="${MIOS_DRIFT_CHECK_SOFT:-0}"

if [ -r "$ROOT/usr/lib/mios/log.sh" ]; then
    . "$ROOT/usr/lib/mios/log.sh"
fi

SCAN_DIRS=(
    "$ROOT/usr/share/containers/systemd"
    "$ROOT/usr/lib/systemd/system"
    "$ROOT/usr/share/mios/ai"
    "$ROOT/etc/containers/systemd"
    "$ROOT/etc/mios/ai"
)

VIOLATIONS=0
_violation() {
    # 61 checks can reach a _violation BEFORE their header echo, and in
    # single-check mode errexit then aborts main() -- rc=1 and total silence.
    # Name the check here rather than editing 61 call sites.
    local __who="" __frame
    for __frame in "${FUNCNAME[@]}"; do
        case "$__frame" in check_*) __who="$__frame"; break ;; esac
    done
    local __msg="$*"
    [[ -n "$__who" && "$__msg" != *"$__who"* ]] && __msg="$__who: $__msg"
    echo "[98-drift-checks] VIOLATION: $__msg" >&2
    VIOLATIONS=$((VIOLATIONS + 1))
    return 1
}
_fail() { _violation "$@"; }

_subject_present() {
    # Bash twin of drift-checks.py _absent (bdf6aba swept Python only).
    # Present -> 0; absent though TRACKED or git mute -> _violation, then 1.
    local __p="$1" __rel __out __rc
    [[ -f "$__p" ]] && return 0
    [[ -e "$ROOT/.git" ]] || return 1
    __rel="$(realpath --relative-to="$ROOT" "$__p" 2>/dev/null || true)"
    if [[ -z "$__rel" ]]; then
        __rel="${__p#"$ROOT"/}"
    fi
    if ! command -v git >/dev/null 2>&1; then
        _violation "cannot tell whether $__rel is tracked: git is not installed" || true
        return 1
    fi
    # --literal-pathspecs: an unexpanded glob must never match index entries.
    __out="$(git -C "$ROOT" --literal-pathspecs ls-files -- "$__rel" 2>&1)" || __rc=$?
    if [[ -n "${__rc:-}" ]]; then
        _violation "cannot tell whether $__rel is tracked: git ls-files exit ${__rc}: ${__out:-no message}" || true
        return 1
    fi
    [[ -z "$__out" ]] && return 1
    _violation "$__rel is tracked but missing from the worktree -- the subject of this check is gone, which is not a pass" || true
    return 1
}

_need_python() {
    # Folded from 56 copies; fails, not skips, under MIOS_DRIFT_REQUIRE_TOOLS=1.
    command -v python3 >/dev/null 2>&1 && return 0
    if [[ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" == "1" ]]; then
        _violation "python3 is required under MIOS_DRIFT_REQUIRE_TOOLS=1 and is not installed"
    else
        echo "[98-drift-checks]   WARNING: python3 missing" >&2
    fi
    return 1
}

_gate_bin() {
    if [[ -n "${MIOS_NATIVE_BIN_DIR:-}" ]]; then
        native_bin mios-gate
        return $?
    fi
    # Folded from 13 copies.  ONE resolution order for the native gate, because
    # a copy that forgot the debug path made check_version_ssot violate
    # unconditionally in CI (which builds `cargo build -p mios-gate`, debug).
    local c
    for c in "${MIOS_GATE_BIN:-}" \
             "$ROOT/src/mios-rs/target/release/mios-gate" \
             "$ROOT/src/mios-rs/target/debug/mios-gate" \
             "$ROOT/src/mios-rs/target/release/mios-gate.exe" \
             "$ROOT/src/mios-rs/target/debug/mios-gate.exe" \
             /usr/libexec/mios/mios-gate; do
        [[ -n "$c" && -x "$c" ]] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

_unit_gen_bin() {
    if [[ -n "${MIOS_NATIVE_BIN_DIR:-}" ]]; then
        native_bin mios-unit-gen
        return $?
    fi
    local c
    for c in \
             "$ROOT/tools/native/target/release/mios-unit-gen" \
             "$ROOT/tools/native/target/debug/mios-unit-gen" \
             "$ROOT/tools/native/target/release/mios-unit-gen.exe" \
             "$ROOT/tools/native/target/debug/mios-unit-gen.exe" \
             /usr/libexec/mios/mios-unit-gen /opt/mios/bin/mios-unit-gen; do
        [[ -n "$c" && -x "$c" ]] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

# These generators' Python twins were ported and deleted ([rust.categories.gen].replaces);
# a missing native binary is a violation, never a fallback to a script that no longer exists.
_GEN_MISSING="native mios-gen is required (its Python twin was ported and deleted, ADR-0021) -- build it: cd tools/native && cargo build -p mios-gen"

native_bin() {
    local name="$1" override="${2:-}" suffix candidate
    local suffixes=("" ".exe")
    case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) suffixes=(".exe" "");; esac
    if [[ -n "${MIOS_NATIVE_BIN_DIR:-}" ]]; then
        candidate="$MIOS_NATIVE_BIN_DIR/$name${suffixes[0]}"
        [[ -x "$candidate" ]] || return 1
        printf '%s' "$candidate"
        return 0
    fi
    if [[ -n "$override" && -x "$override" ]]; then
        printf '%s' "$override"
        return 0
    fi
    for suffix in "${suffixes[@]}"; do
        for candidate in "$ROOT/tools/native/target/release/$name$suffix" \
            "$ROOT/tools/native/target/debug/$name$suffix" \
            "$ROOT/src/mios-rs/target/release/$name$suffix" \
            "$ROOT/src/mios-rs/target/debug/$name$suffix" \
            "/usr/libexec/mios/$name$suffix" "/usr/bin/$name$suffix" "/opt/mios/bin/$name$suffix"; do
            [[ -x "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
        done
    done
    return 1
}

_run_deployment_projection() {
    local mode="$1" bin output
    bin="$(_unit_gen_bin)" || {
        _violation "mios-unit-gen is not built -- build it in MiOS-DEV: cd tools/native && cargo build -p mios-unit-gen"
        return
    }
    # An older binary ignores a new subcommand and --check runs its unit gate.
    # Require the named projection before trusting its exit status.
    if ! "$bin" --list-projections | tr -d '\r' | grep -Fxq "$mode"; then
        _violation "mios-unit-gen does not advertise $mode -- rebuild the binary from this checkout"
        return
    fi
    if output="$("$bin" "$mode" --root "$ROOT" --check 2>&1)"; then
        printf '[98-drift-checks]   %s\n' "$output"
    else
        _violations_from "$mode: " "$output"
    fi
}

_violations_from() {
    # Folded from 42 copies of this loop.
    local __prefix="$1" __blob="$2" line __n=0
    while IFS= read -r line; do
        if [[ -n "$line" ]]; then
            # `|| :`: _violation ends in `return 1`, and under errexit that cut
            # this loop after the FIRST line in single-check mode.
            _violation "${__prefix}${line}" || :
            __n=$(( __n + 1 ))
        fi
    done <<<"$__blob"
    # Six callers capture stdout with 2>/dev/null, so a tool that dies with a
    # traceback on stderr hands us an EMPTY blob. Recording nothing there left
    # VIOLATIONS at 0 and main()'s summary called the run clean.
    if (( __n == 0 )); then
        _violation "${__prefix}the backing tool failed and produced no parsable output on stdout (stderr may be discarded by the caller)"
    fi
    return 1
}

_emit_projection_evidence() {
    local pfx='[98-drift-checks][diff]' generator="$1" bin target
    shift
    bin="$(native_bin mios-gen "${MIOS_GEN_BIN:-}")" || {
        echo "$pfx native mios-gen required for isolated evidence" >&2
        return 0
    }
    local -a args=(projection-evidence --root "$ROOT" --generator "$generator")
    for target in "$@"; do args+=(--target "$target"); done
    if ! "$bin" "${args[@]}"; then
        echo "$pfx native evidence unavailable; the projection gate remains failed" >&2
    fi
}

_require_python3() {
    if command -v python3 >/dev/null 2>&1; then
        return 0
    fi
    if [[ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" == "1" ]]; then
        _violation "python3 is required by critical security/law checks but absent (MIOS_DRIFT_REQUIRE_TOOLS=1; )"
    fi
    return 1
}

echo "[98-drift-checks] source-tree AI-plane drift fitness-functions"
echo "[98-drift-checks]   root: $ROOT"

check_dead_lane() {
    local pattern=':11434'
    local hits="" f active
    for d in "${SCAN_DIRS[@]}"; do
        [[ -d "$d" ]] || continue
        while IFS= read -r f; do
            [[ -f "$f" ]] || continue
            active=$(sed -E '/^[[:space:]]*(#|\/\/)/d' "$f")
            if printf '%s\n' "$active" | grep -qE "$pattern"; then
                hits+="    $f"$'\n'
            fi
        done < <(find "$d" -type f \( -name '*.container' -o -name '*.service' \
            -o -name '*.conf' -o -name '*.json' -o -name '*.toml' \
            -o -name '*.yaml' -o -name '*.yml' \) 2>/dev/null)
    done
    if [[ -n "$hits" ]]; then
        printf '%s' "$hits" >&2
        _violation "retired :11434 (ollama) lane in active source config -- MiOS is /v1-only; use the live lane (mios-llm-light on \${MIOS_PORTS_LLM_LIGHT})"
    else
        echo "[98-drift-checks]   no retired :11434 lane in active config"
    fi
}

check_retired_models() {
    local pattern='(^|[^A-Za-z0-9_./-])(gemma4|qwen3:1\.7b)([^A-Za-z0-9_-]|$)'
    local hits="" f active
    for d in "${SCAN_DIRS[@]}"; do
        [[ -d "$d" ]] || continue
        while IFS= read -r f; do
            [[ -f "$f" ]] || continue
            active=$(sed -E '/^[[:space:]]*(#|\/\/)/d' "$f")
            if printf '%s\n' "$active" | grep -qE "$pattern"; then
                hits+="    $f"$'\n'
            fi
        done < <(find "$d" -type f \( -name '*.container' -o -name '*.service' \
            -o -name '*.json' -o -name '*.conf' -o -name '*.yaml' \
            -o -name '*.yml' \) 2>/dev/null)
    done
    if [[ -n "$hits" ]]; then
        printf '%s' "$hits" >&2
        _violation "retired model-id (gemma4 / qwen3:1.7b) hardcoded in a consumer unit (point it at the live [ai].model, e.g. granite4.1:8b)"
    else
        echo "[98-drift-checks]   no retired model-id in consumer config"
    fi
}

check_structured() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py structured
    then
        echo "[98-drift-checks]   every [nodes.local-*] localhost lane is served; ai/v1 manifests parse + refs resolve"
    else
        _violation "structured drift: a [nodes.*] lane is dangling and/or an ai/v1 manifest is broken (see lines above)"
    fi
}

check_hint_coverage() {
    local tool="$ROOT/usr/libexec/mios/mios-ai-hint-coverage"
    _need_python || return 0
    if [[ ! -f "$tool" ]]; then
        _violation "usr/libexec/mios/mios-ai-hint-coverage absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    if python3 "$tool" --root "$ROOT"; then
        echo "[98-drift-checks]   AI-hint coverage within ratchet ceiling"
    else
        _violation "AI-hint coverage regressed: a new taggable file lacks an AI-hint header (run mios-ai-tag, or raise [ai_tag].max_untagged only for prompt/data files)"
    fi
}

check_module_boundary() {
    local dir="$ROOT/usr/lib/mios/agent-pipe"
    if [[ ! -d "$dir" ]]; then
        _violation "agent-pipe dir absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local hits="" f active
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        active=$(sed -E '/^[[:space:]]*#/d' "$f")
        if printf '%s\n' "$active" | grep -qE '^[[:space:]]*(import[[:space:]]+server|from[[:space:]]+server[[:space:]])'; then
            hits+="    $f"$'\n'
        fi
    done < <(find "$dir" -maxdepth 1 -type f -name 'mios_*.py' 2>/dev/null)
    if [[ -n "$hits" ]]; then
        printf '%s' "$hits" >&2
        _violation "agent-pipe sibling module imports the server monolith (breaks the modular-monolith boundary; siblings must stay server.py-free + isolation-testable)"
    else
        echo "[98-drift-checks]   agent-pipe sibling modules are server.py-free"
    fi
}

check_rbac_tiers() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py rbac-tiers
    then
        echo "[98-drift-checks]   RBAC max_permission tiers all valid"
    else
        _violation "an [agents.*]/[users.*].max_permission names an UNKNOWN permission tier -- the dispatch PDP fails CLOSED on it (restricts the caller to the safest tier); fix the typo or add the tier to [ai].permission_tiers "
    fi
}

check_agent_schema() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py agent-schema
    then
        echo "[98-drift-checks]   agent-schema contract satisfied"
    else
        _violation "an [agents.*] entry violates the unified agent schema : a local-optional agent missing health_gate, a kind=cli without timeout_s, a kind=node without api+lane, or >1 default=true -- the opencode 'dead local endpoint treated as live -> merged_chars=0' class. Fix the [agents.*] block (or [agents._defaults])."
    fi
}

check_ai_manifest() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py ai-manifest
    then
        echo "[98-drift-checks]   ai/v1 verb-catalog manifest in sync with mios.toml SSOT"
    else
        _violation "ai/v1/tools.generated.json is STALE vs mios.toml [verbs.*] -- regenerate with mios-ai-manifest-gen "
    fi
}

check_package_registry() {
    local _en_val=""
    if [[ -n "${MIOS_PACKAGE_REGISTRY:-}" ]]; then
        _en_val="$MIOS_PACKAGE_REGISTRY"
    else
        _en_val=$(MIOS_TOML="$ROOT/usr/share/mios/mios.toml" python3 -c '
import sys, os
try:
    import tomllib as t
except ImportError:
    import tomli as t
toml = os.environ.get("MIOS_TOML", "")
if os.path.isfile(toml):
    with open(toml, "rb") as f:
        d = t.load(f)
    val = (d.get("ai") or {}).get("package_registry", False)
    print("true" if val else "false")
else:
    print("false")
' 2>/dev/null || echo "false")
    fi
    local _en
    _en="$(printf '%s' "${_en_val:-false}" | tr '[:upper:]' '[:lower:]')"
    case "$_en" in
        1|true|yes|on) : ;;
        *)
            local _reg_file="$ROOT/usr/share/mios/ai/v1/packages/registry.json"
            if [[ -f "$_reg_file" ]]; then
                _violation "package_registry is dormant in mios.toml [ai].package_registry but registry.json exists -- remove stale file or turn feature on"
            else
                echo "[98-drift-checks]   package registry dormant"
            fi
            return 0 ;;
    esac
    _need_python || return 0
    if MIOS_AGENT_PIPE_DIR="$ROOT/usr/lib/mios/agent-pipe" \
       MIOS_TOML="$ROOT/usr/share/mios/mios.toml" \
       MIOS_VENDOR_TOML="$ROOT/usr/share/mios/mios.toml" \
       MIOS_PACKAGES_DIR="$ROOT/usr/share/mios/ai/v1/packages" \
       python3 "$ROOT/usr/libexec/mios/mios-registry" verify >/dev/null 2>"$ROOT/.pkgreg.err"; then
        rm -f "$ROOT/.pkgreg.err" 2>/dev/null || true
        echo "[98-drift-checks]   package registry in sync with mios.toml SSOT"
    else
        sed 's/^/    /' "$ROOT/.pkgreg.err" >&2 2>/dev/null || true
        rm -f "$ROOT/.pkgreg.err" 2>/dev/null || true
        _violation "ai/v1/packages/registry.json is STALE vs the SSOT -- regenerate with mios-registry generate "
    fi
}

check_cli_sql_safety() {
    local dir="$ROOT/usr/libexec/mios"
    if [[ ! -d "$dir" ]]; then
        _violation "libexec dir absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local allow=" "   # empty: all libexec tools cut over to parameterized pg
    local legacy_pattern='(_pgesc\(|_pgq\(|post_sql\(|def _sql\(|/sql"|surreal-ns)'
    local hits="" f base active
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        base="$(basename "$f")"
        case "$base" in test_*|*.pyc) continue ;; esac
        [[ "$allow" == *" $base "* ]] && continue
        grep -qI . "$f" || continue
        active=$(sed -E '/^[[:space:]]*#/d' "$f")
        if printf '%s\n' "$active" | grep -qE "$legacy_pattern"; then
            hits+="    $f (legacy DB transport or escaping)"$'\n'
        fi
        # Flag raw unwhitelisted f-string SQL identifier interpolation in execute()
        local unsafe_fstrings
        unsafe_fstrings=$(printf '%s\n' "$active" | grep -E 'execute\(f["'"'"'].*\{' | grep -vE '\{(safe_|ph_)' || true)
        if [[ -n "$unsafe_fstrings" ]]; then
            hits+="    $f (unwhitelisted f-string SQL interpolation: $unsafe_fstrings)"$'\n'
        fi
    done < <(find "$dir" -maxdepth 1 -type f 2>/dev/null)
    if [[ -n "$hits" ]]; then
        printf '%s' "$hits" >&2
        _violation "a libexec CLI (re)introduced the retired legacy DB transport (post_sql/_sql/:8000/sql), hand-rolled SQL escaping (_pgesc/_pgq), or raw unwhitelisted f-string SQL interpolation -- use parameterized pg or safe_ident() for SQLite identifiers"
    else
        echo "[98-drift-checks]   libexec CLIs SQL-safe"
    fi
}

check_module_test_coverage() {
    # Native (ADR-0021): a unit test must NAME each module; a test file named after it is not evidence.
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_module_test_coverage could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    local out
    if out="$("$bin" module-test-coverage --root "$ROOT" 2>&1)"; then
        echo "[98-drift-checks]   every agent-pipe module is named by a unit test; tools/ and libexec python within the sibling-test ratchet"
    else
        _violations_from "check_module_test_coverage: " "$out"
    fi
}

check_raw_toml_readers() {
    local dir="$ROOT/usr/lib/mios/agent-pipe"
    if [[ ! -d "$dir" ]]; then
        _violation "raw TOML readers -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local violations="" f base
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        base="$(basename "$f")"
        case "$base" in test_*) continue ;; esac

        if grep -q -E "os\.environ(\.get)?\([\"']MIOS_TOML[\"']\)" "$f"; then
            violations+="    $base (reads MIOS_TOML env var directly)"$'\n'
        fi
        if grep -E "open\(" "$f" | grep -q -E "mios\.toml"; then
            violations+="    $base (hardcoded open of mios.toml)"$'\n'
        fi
    done < <(find "$dir" -maxdepth 2 -type f -name '*.py' 2>/dev/null)

    if [[ -n "$violations" ]]; then
        printf '%s' "$violations" >&2
        _violation "found raw MIOS_TOML / mios.toml file readers. Use mios_toml.load_merged() / load_vendor() instead of manual file opens or raw env lookups (B11)"
    else
        echo "[98-drift-checks]   no raw MIOS_TOML readers in agent-pipe"
    fi
}

check_capability_manifest() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py capability-manifest
    then
        echo "[98-drift-checks]   ai/v1 capability manifest in sync with mios.toml SSOT"
    else
        _violation "ai/v1/capabilities.generated.json is STALE vs mios.toml [verbs.*]+[recipes.*] -- regenerate with mios-ai-capabilities-gen"
    fi
}

check_surface_parity() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py surface-parity
    then
        echo "[98-drift-checks]   server.py public surface matches the committed golden"
    else
        _violation "server.py PUBLIC SURFACE drifted from usr/share/mios/ai/v1/surface.generated.json -- a route/symbol was dropped or added during the refactor. If intended, regenerate: python3 usr/lib/mios/agent-pipe/mios_surface.py usr/lib/mios/agent-pipe/server.py --package > usr/share/mios/ai/v1/surface.generated.json (refactor WS R0)"
    fi
}

check_pod_quadlets() {
    local nb; nb="$(native_bin mios-gen || true)"
    if [[ -n "$nb" ]]; then
        if "$nb" pod-quadlets --root "$ROOT" --check; then
            echo "[98-drift-checks]   Quadlet units in sync with mios.toml SSOT"
            return 0
        else
            _violation "Quadlet unit(s) (.pod, .container, .network, .volume) STALE vs mios.toml SSOT -- regenerate with mios-gen pod-quadlets"
            return 0
        fi
    fi
    _violation "Native mios-gen is required for the Quadlet projection check; install the SSOT release catalog"
}

check_egress_firewall() {
    local native
    native="$(native_bin mios-gen "${MIOS_GEN_BIN:-}")" || {
        _violation "check_egress_firewall: native mios-gen is required; build/install the SSOT release catalog"
        return
    }
    if "$native" egress-firewall --root "$ROOT" --check; then
        echo "[98-drift-checks]   egress.nft in sync with mios.toml [security.egress] SSOT"
    else
        _violation "usr/share/mios/security/egress.nft is STALE vs mios.toml [security.egress] -- regenerate with mios-gen egress-firewall"
    fi
}

check_blade_dropins() {
    _run_deployment_projection blade-dropins
}

check_no_hardcode() {
    local bin=""
    local _c
    for _c in "${MIOS_HARDCODE_LINT_BIN:-}" \
              "$ROOT/tools/native/target/release/mios-hardcode-lint" \
              "$ROOT/tools/native/target/debug/mios-hardcode-lint" \
              "$ROOT/tools/native/target/release/mios-hardcode-lint.exe" \
              "$ROOT/tools/native/target/debug/mios-hardcode-lint.exe" \
              /usr/libexec/mios/mios-hardcode-lint \
              /usr/bin/mios-hardcode-lint; do
        if [[ -n "$_c" && -x "$_c" ]]; then bin="$_c"; break; fi
    done
    if [[ -n "$bin" ]]; then
        if "$bin" "$ROOT" >/dev/null 2>"$ROOT/.nohc.err"; then
            rm -f "$ROOT/.nohc.err" 2>/dev/null || true
            echo "[98-drift-checks]   no date-in-comment / header crash-risk"
            return 0
        else
            sed 's/^/    /' "$ROOT/.nohc.err" >&2 2>/dev/null || true
            rm -f "$ROOT/.nohc.err" 2>/dev/null || true
            _violation "NO-HARDCODE law (Law 7): a date/timestamp in a comment/docstring OR an AI-Hint header crash-risk -- strip the date (timeless comment) or move the header below the shebang/BOM (see mios-hardcode-lint)"
            return
        fi
    fi

    _need_python || return 0
    local tool="$ROOT/usr/libexec/mios/mios-hardcode-lint"
    if [[ ! -f "$tool" ]]; then
        _violation "usr/libexec/mios/mios-hardcode-lint absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    if python3 "$tool" "$ROOT" >/dev/null 2>"$ROOT/.nohc.err"; then
        rm -f "$ROOT/.nohc.err" 2>/dev/null || true
        echo "[98-drift-checks]   no date-in-comment / header crash-risk"
    else
        sed 's/^/    /' "$ROOT/.nohc.err" >&2 2>/dev/null || true
        rm -f "$ROOT/.nohc.err" 2>/dev/null || true
        _violation "NO-HARDCODE law (Law 7): a date/timestamp in a comment/docstring OR an AI-Hint header crash-risk -- strip the date (timeless comment) or move the header below the shebang/BOM (see mios-hardcode-lint)"
    fi
}

check_no_hardcode_version() {
    _need_python || return 0
    local tool="$ROOT/usr/libexec/mios/mios-version-lint"
    if [[ ! -f "$tool" ]]; then
        _violation "usr/libexec/mios/mios-version-lint absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    if MIOS_TOML_ROOT="$ROOT" python3 "$tool" "$ROOT" >/dev/null 2>"$ROOT/.nohc_ver.err"; then
        rm -f "$ROOT/.nohc_ver.err" 2>/dev/null || true
        echo "[98-drift-checks]   no hand-pinned version literal"
    else
        sed 's/^/    /' "$ROOT/.nohc_ver.err" >&2 2>/dev/null || true
        rm -f "$ROOT/.nohc_ver.err" 2>/dev/null || true
        _violation "NO-HARDCODE-VERSION law (Law 7 / ADR-0003): a hand-pinned version literal in a download URL / pip|npm pin / image @sha256 digest -- float it (:latest) and record the resolved version in the SBOM, or allowlist via mios.toml [build.float]"
    fi
}

check_unwired_modules() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py unwired-modules
    then
        echo "[98-drift-checks]   no imported-but-dead agent-pipe module"
    else
        _violation "an agent-pipe module is imported-but-dead (no real non-test caller) OR a _UNWIRED_ALLOW entry is stale -- wire the module (give it a call site) or update the _UNWIRED_ALLOW register (MIOS-GAP-REGISTER A1)"
    fi
}

check_cephfs_ssot() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py cephfs-ssot
    then
        echo "[98-drift-checks]   CephFS SSOT configuration is valid"
    else
        _violation "[storage.cephfs] SSOT validation failed (see lines above)"
    fi
}

# --- [converge] values are read from mios.toml and within legal bounds ---
check_converge_ssot() {
    _need_python || return 0

    local toml="${MIOS_TOML_ROOT:-$ROOT}/usr/share/mios/mios.toml"
    local ssot
    ssot="$(python3 -c '
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    d = tomllib.load(fh)
c = d.get("converge", {})
inf, mem = c.get("inference", {}), c.get("memory", {})
def emit(name, value):
    print("%s=%s" % (name, value))
emit("SSOT_HEAVY_ENGINE", d.get("ai", {}).get("heavy_engine", ""))
emit("SSOT_RUNTIME_LORA", str(inf.get("vllm_allow_runtime_lora", False)).lower())
emit("SSOT_COLD_DIR", mem.get("cold_storage_dir", "/var/lib/mios/history/"))
emit("SSOT_COLD_DAYS", mem.get("cold_retention_days", 30))
emit("SSOT_COLD_ZSTD", mem.get("cold_zstd_level", 3))
emit("SSOT_SQLITE_VEC", str(mem.get("sqlite_vec_enable", False)).lower())
' "$toml" 2>&1)" || {
        _violation "check_converge_ssot: cannot read [converge] from ${toml}: ${ssot}"
        return
    }

    local SSOT_HEAVY_ENGINE SSOT_RUNTIME_LORA SSOT_COLD_DIR SSOT_COLD_DAYS SSOT_COLD_ZSTD SSOT_SQLITE_VEC
    eval "$ssot"

    # Runtime LoRA is a vLLM feature of the ONE heavy lane: it binds the engine.
    local runtime_lora="${MIOS_CONVERGE_INFERENCE_VLLM_ALLOW_RUNTIME_LORA:-$SSOT_RUNTIME_LORA}"
    if [[ "$runtime_lora" == "true" && "$SSOT_HEAVY_ENGINE" != "vllm" ]]; then
        _violation "[converge.inference].vllm_allow_runtime_lora=true but [ai].heavy_engine is '${SSOT_HEAVY_ENGINE}', not 'vllm'"
        return
    fi

    local cold_storage_dir="${MIOS_CONVERGE_MEMORY_COLD_STORAGE_DIR:-$SSOT_COLD_DIR}"
    if [[ "$cold_storage_dir" == *"/tenants/"* ]]; then
        _violation "[converge.memory].cold_storage_dir cannot sit inside a CephFS tenants mount: ${cold_storage_dir}"
        return
    fi

    local cold_retention_days="${MIOS_CONVERGE_MEMORY_COLD_RETENTION_DAYS:-$SSOT_COLD_DAYS}"
    if ! [[ "$cold_retention_days" =~ ^[0-9]+$ ]] || (( cold_retention_days < 1 )); then
        _violation "[converge.memory].cold_retention_days must be an integer >= 1, got: ${cold_retention_days}"
        return
    fi

    local cold_zstd_level="${MIOS_CONVERGE_MEMORY_COLD_ZSTD_LEVEL:-$SSOT_COLD_ZSTD}"
    if ! [[ "$cold_zstd_level" =~ ^[0-9]+$ ]] || (( cold_zstd_level < 1 || cold_zstd_level > 19 )); then
        _violation "[converge.memory].cold_zstd_level must be an integer 1..19, got: ${cold_zstd_level}"
        return
    fi

    local sqlite_vec_enable="${MIOS_CONVERGE_MEMORY_SQLITE_VEC_ENABLE:-$SSOT_SQLITE_VEC}"
    if [[ "$sqlite_vec_enable" == "true" ]]; then
        local py_bin="/usr/lib/mios/agents/.venv/bin/python3"
        [[ -x "$py_bin" ]] || py_bin="python3"
        if ! "$py_bin" -c "import sqlite_vec" >/dev/null 2>&1; then
            _violation "[converge.memory].sqlite_vec_enable=true but sqlite_vec is not importable"
            return
        fi
    fi

    echo "[98-drift-checks]   [converge] SSOT values validated (retention=${cold_retention_days}d zstd=${cold_zstd_level} heavy_engine=${SSOT_HEAVY_ENGINE} runtime_lora=${runtime_lora})"
}

# --- Hummingbird distroless Containerfile and Quadlet conform when the feature is enabled ---
check_hummingbird() {
    local distroless_enable="${MIOS_CONVERGE_IMAGE_DISTROLESS_ENABLE:-false}"
    local rechunk_enable="${MIOS_CONVERGE_IMAGE_RECHUNK_ENABLE:-false}"
    local containerfile="Containerfile.hummingbird"
    local quadlet="usr/share/containers/systemd/mios-agent-pipe.container"

    # With the feature off and its Containerfile never authored, every branch
    # below is skipped, so report that rather than a validated configuration.
    if [[ "$distroless_enable" != "true" && "$rechunk_enable" != "true" && ! -f "$containerfile" ]]; then
        echo "[98-drift-checks]   Hummingbird distroless off ([conv.image].distroless_enable=false) and $containerfile absent -- nothing examined"
        return 0
    fi

    if [[ "$distroless_enable" == "true" ]]; then
        if [[ ! -f "$containerfile" ]]; then
            _violation "distroless_enable=true but $containerfile is missing"
            return 1
        fi

        if [[ ! -f "$quadlet" ]]; then
            _violation "Quadlet definition $quadlet is missing"
            return 1
        fi

        if ! grep -q "Environment=MIOS_AI_ENDPOINT=" "$quadlet"; then
            _violation "Quadlet $quadlet is missing Environment=MIOS_AI_ENDPOINT"
            return 1
        fi
    fi

    if [[ -f "$containerfile" ]]; then
        local mios_toml="usr/share/mios/mios.toml"
        local expected_base
        expected_base=$(grep -E '^\s*distroless_base\s*=' "$mios_toml" | head -n 1 | cut -d'"' -f2 || echo "Gcr.io/distroless/python3-debian13")
        if [[ -z "$expected_base" ]]; then
            expected_base="gcr.io/distroless/python3-debian13"
        fi

        if ! grep -F "FROM $expected_base" "$containerfile" >/dev/null 2>&1; then
            _violation "Containerfile.hummingbird base image does not match distroless_base"
            return 1
        fi

        local final_stage
        final_stage=$(awk '/^FROM/ { stage="" } { stage=stage "\n" $0 } END { print stage }' "$containerfile")

        if echo "$final_stage" | grep -F "/bin/bash" >/dev/null; then
            _violation "Containerfile.hummingbird final stage contains /bin/bash"
            return 1
        fi

        local user_line
        user_line=$(echo "$final_stage" | grep -E '^\s*USER\s+' | tail -n 1 | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
        if [[ "$user_line" != "USER 65534" && "$user_line" != "USER 65534:65534" ]]; then
            _violation "Containerfile.hummingbird final stage USER is not 65534 or 65534:65534"
            return 1
        fi
    fi

    if [[ "$rechunk_enable" == "true" ]]; then
        if ! command -v rpm-ostree >/dev/null 2>&1; then
            _violation "rechunk_enable=true but rpm-ostree binary not found in PATH"
            return 1
        fi
    fi

    echo "[98-drift-checks]   Hummingbird distroless Containerfile and Quadlet conform"
}

check_container_ports() {
    _need_python || return 0
    local out
    if out="$(MIOS_DRIFT_ROOT="$ROOT" python3 "$ROOT/tools/drift-checks.py" container-ports 2>&1)"
    then
        echo "[98-drift-checks]   no manual port literals in container definitions"
    else
        printf '%s\n' "$out" >&2
        _violation "manual port literal found in container Quadlets"
    fi
}

# --- every SSOT budget key has a consumer or an itemised shrink-only exemption ---
check_agent_pipe_budgets() {
    local lint_bin output
    lint_bin="$(native_bin mios-aiplane-lint "${MIOS_AIPLANE_LINT_BIN:-}")" || {
        _violation "mios-aiplane-lint is required for the complete SSOT budget census"
        return
    }
    if output="$(MIOS_DRIFT_ROOT="$ROOT" "$lint_bin" 2>&1)"; then
        printf '[98-drift-checks]   %s\n' "$output"
    else
        _violations_from "budget census: " "$output"
    fi
}
check_no_bare_port_literals() {
    _need_python || return 0
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py no-bare-port-literals 2>&1)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   no bare port literals remain in execution paths"
    echo "[98-drift-checks]   ${out}"
}

check_dotfiles_projection() {
    _need_python || return 0
    local tool="$ROOT/usr/libexec/mios/mios-dotfiles-render"
    if [[ ! -f "$tool" ]]; then
        _violation "usr/libexec/mios/mios-dotfiles-render absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    if MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" MIOS_HOST_TOML=/nonexistent.toml MIOS_USER_TOML=/nonexistent.toml python3 "$tool" check >/dev/null 2>"$ROOT/.dotfiles.err"; then
        rm -f "$ROOT/.dotfiles.err" 2>/dev/null || true
        echo "[98-drift-checks]   every committed theme + settings surface projects from mios.toml [colors]/[btop]/[gitconfig]/[identity]/[dotfiles] SSOT"
    else
        sed 's/^/    /' "$ROOT/.dotfiles.err" >&2 2>/dev/null || true
        rm -f "$ROOT/.dotfiles.err" 2>/dev/null || true
        _violation "a dotfiles surface drifted from the mios.toml SSOT projection -- re-run mios dotfiles sync (Phase-1 palette drift-gate; mios-dotfiles-render)"
    fi
    # ADR-0024 second leg: diff each client-portable settings block against the
    # [dotfiles.vscode] projection, so a key dropped from the list is red too.
    local sync="$ROOT/tools/sync-dotfiles.py"
    if [[ ! -f "$sync" ]]; then
        _violation "tools/sync-dotfiles.py absent -- a tracked deliverable is missing, so the client-portable half of this check cannot run"
        return
    fi
    if MIOS_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" python3 "$sync" --check --client-surfaces >/dev/null 2>"$ROOT/.dotfiles.err"; then
        rm -f "$ROOT/.dotfiles.err" 2>/dev/null || true
        echo "[98-drift-checks]   every devcontainer.json / *.code-workspace settings block carries exactly the client-portable subset of .dotfiles/vscode/settings.json and no [dotfiles.vscode] desktop-only key (ADR-0024)"
    else
        sed 's/^/    /' "$ROOT/.dotfiles.err" >&2 2>/dev/null || true
        rm -f "$ROOT/.dotfiles.err" 2>/dev/null || true
        _violation "a client-portable VS Code surface drifted from the mios.toml [dotfiles.vscode] projection (ADR-0024) -- re-run python3 tools/sync-dotfiles.py"
    fi
}

check_edge_generators() {
    echo "[98-drift-checks] every imperative edge-to-edge generator regenerates the surface the image installs byte-identical"
    _need_python || return 0
    local spec gen arg out n=0
    local mgen; mgen="$(native_bin mios-gen || true)"
    for spec in "usr/libexec/mios/ux/wm_config_gen.py ." "usr/libexec/mios/desktop/gpu_terminal.py ." \
                "usr/libexec/mios/win/wt_profile_inject.py ." "usr/libexec/mios/ux/tmux_theme.py ." \
                "usr/lib/mios/agent-pipe/mios_pipe/routing/portal_edge.py ."; do
        read -r gen arg <<<"$spec"
        if [[ "$gen" == "usr/libexec/mios/ux/tmux_theme.py" ]]; then  # ported to mios-gen render-tmux-theme
            [[ -n "$mgen" ]] || { _violation "$_GEN_MISSING" || :; continue; }
            if out="$(cd "$ROOT" && "$mgen" render-tmux-theme --root "$ROOT" --check 2>&1)"; then
                n=$(( n + 1 ))
            else
                _violations_from "render-tmux-theme --check: " "$out" || :
            fi
            continue
        fi
        if [[ ! -f "$ROOT/$gen" ]]; then
            _violation "$gen absent -- a tracked generator is missing, so its golden cannot be regenerated" || :
            continue
        fi
        # Vendor tier pinned to this tree: an inherited MIOS_VENDOR_TOML would grade another one.
        if out="$(cd "$ROOT" && MIOS_VENDOR_TOML="$ROOT/usr/share/mios/mios.toml" MIOS_VENDOR_TOML_D="$ROOT/usr/lib/mios/mios.d" \
                python3 "$gen" --check-fixture "$arg" 2>&1)"; then
            n=$(( n + 1 ))
        else
            _violations_from "$gen --check-fixture $arg: " "$out" || :
        fi
    done
    (( n == 5 )) || return 1
    echo "[98-drift-checks]   hyprland.conf, sway/config, skel alacritty.toml, WSL terminal-profile.json, mios-theme.tmux.conf, portal-term.css and ttyd-page.json each match their generator"
}

check_edge_status() {
    echo "[98-drift-checks] every [theme.edge.reach] artifact measures at its declared reach (mios-edge-status)"
    if ! command -v cargo >/dev/null 2>&1; then
        _violation "cargo is not installed, so mios-edge-status cannot run -- an unmeasured reach table is not a pass"
        return
    fi
    if [[ ! -f "$ROOT/tools/native/mios-edge-status/Cargo.toml" ]]; then
        _violation "tools/native/mios-edge-status/Cargo.toml absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local bs="${MIOS_BOOTSTRAP_ROOT:-$ROOT/../mios-bootstrap}" out rc=0
    local -a args=(--root "$ROOT")
    [[ -d "$bs" ]] && args+=(--bootstrap "$bs")
    out="$(cd "$ROOT/tools/native" && cargo run -q -p mios-edge-status -- "${args[@]}" 2>&1)" || rc=$?
    if (( rc != 0 )); then
        printf '%s\n' "$out" | grep -vE '^edge [^ ]+ ' | sed 's/^/    /' >&2 || :
        _violations_from "mios-edge-status exit ${rc}: " "$(printf '%s\n' "$out" | grep -E 'DRIFT|unclassified|unmeasured|error|mios-edge-status' || true)"
        return
    fi
    echo "[98-drift-checks]   $(grep -c '^edge ' <<<"$out" || :) reach entries measured, none drifted"
}

check_userenv_parity() {
    local src="$ROOT/tools/lib/userenv.sh" dst="$ROOT/usr/lib/mios/userenv.sh"
    # Both twins are tracked repo files, so absence is a defect, never a skip.
    # This printed a pass-shaped line and returned 0, so deleting the shipped
    # copy read exactly like parity.
    local missing=""
    [[ -f "$src" ]] || missing+=" tools/lib/userenv.sh"
    [[ -f "$dst" ]] || missing+=" usr/lib/mios/userenv.sh"
    if [[ -n "$missing" ]]; then
        _violation "userenv.sh twin missing, so parity is unverifiable:${missing}"
        return
    fi
    if diff -q "$src" "$dst" >/dev/null 2>&1; then
        echo "[98-drift-checks]   usr/lib/mios/userenv.sh matches authoritative tools/lib/userenv.sh"
    else
        _violation "usr/lib/mios/userenv.sh drifted from the authoritative tools/lib/userenv.sh (59-tools.sh installs the latter) -- resync: cp tools/lib/userenv.sh usr/lib/mios/userenv.sh (flatten check 27)"
    fi
}

check_verb_backends() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py verb-backends
    then
        echo "[98-drift-checks]   every [verbs.*].cmd mios-* backend resolves on disk"
    else
        _violation "a [verbs.*].cmd dispatches to a mios-* backend that does not exist (dead dispatch) -- fix the cmd template or ship the backend (flatten check 26)"
    fi
}

# --- globals port declarations match mios.toml [ports] SSOT ---
check_globals_ports() {
    check_globals_generated
}

check_globals_image_parity() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py globals-image-parity
    then
        echo "[98-drift-checks]   default image references in globals.{sh,ps1} equal mios.toml [image] SSOT"
    else
        _violation "default image reference in automation/lib/globals.sh or globals.ps1 drifted from mios.toml [image] SSOT"
    fi
}

check_dag_integrity() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py dag-integrity
    then
        echo "[98-drift-checks]   DAG-integrity: consumers start after their producers' readiness artifacts exist"
    else
        _violation "DAG dependency ordering violation detected: consumer starts before producer (flatten check 29)"
    fi
}

# --- generated names registry matches source topology ---
check_names_registry() {
    local bin out
    bin="$(native_bin mios-gen)" || {
        _violation "mios-gen is required to verify names-registry projections"
        return
    }
    if out="$("$bin" names-registry --root "$ROOT" --check 2>&1)"; then
        if [[ "$out" != *"Names-registry projections verified read-only against SSOT and tracked consumers"* ]]; then
            _violation "mios-gen did not confirm read-only names-registry verification -- rebuild from this checkout"
            return
        fi
        printf '[98-drift-checks]   %s\n' "$out"
    else
        _violations_from "names-registry: " "$out"
    fi
}
check_drift_projection() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py drift-projection
    then
        echo "[98-drift-checks]   DB->TOML materialize round-trip is lossless for config_kv and verbs"
    else
        _violation "DB->TOML materialize round-trip drift detected (check 31) -- verify seed-db-config.py and materialize-config-toml.py mappings"
    fi
}

check_canonical_bools() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_canonical_bools could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if MIOS_TOML="$ROOT/usr/share/mios/mios.toml" "$bin" canonical-bools --root "$ROOT"
    then
        echo "[98-drift-checks]   no non-canonical bool literals in [verbs.*]"
    else
        _violation "Non-canonical bool literal detected in mios.toml verbs (check 33)"
    fi
}

check_etc_duplicates() {
    local etc_dir="$ROOT/etc/containers/systemd"
    local usr_dir="$ROOT/usr/share/containers/systemd"
    local hits=""
    if [[ -d "$etc_dir" ]]; then
        while IFS= read -r -d '' f; do
            local base
            base="$(basename "$f")"
            if [[ -f "$usr_dir/$base" ]]; then
                hits+="    $f (shadows $usr_dir/$base)"$'\n'
            fi
        done < <(find "$etc_dir" -maxdepth 2 -type f \( -name '*.container' -o -name '*.pod' -o -name '*.network' -o -name '*.volume' \) -print0 2>/dev/null)
    fi
    if [[ -n "$hits" ]]; then
        _violation "Full-unit duplicate(s) found in etc/ containers that shadow usr/share/ generated units (check 34):"$'\n'"$hits"
    else
        echo "[98-drift-checks]   no etc/ full-unit duplicate shadows generated usr/share/ containers"
    fi
}

check_drift_build_catalog() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py drift-build-catalog
    then
        echo "[98-drift-checks]   DB->/ctx materialize round-trip is lossless for build catalog"
    else
        _violation "DB->/ctx materialize round-trip drift detected (check 32) -- verify seed-db-config.py and materialize-build-ctx.py mappings"
    fi
}

check_no_mkdir_in_var() {
    local pat='mkdir[^;&|#]*['\''"[:space:]]/var/'
    local hits="" f active m
    for f in "$ROOT"/automation/[0-9]*.sh "$ROOT"/Containerfile*; do
        [[ -f "$f" ]] || continue
        active=$(sed -E '/^[[:space:]]*#/d' "$f")
        m=$(printf '%s\n' "$active" | grep -nE "$pat" || true)
        [[ -n "$m" ]] && hits+="    ${f#"$ROOT"/}:"$'\n'"$(printf '%s\n' "$m" | sed 's/^/      /')"$'\n'
    done
    if [[ -n "$hits" ]]; then
        printf '%s' "$hits" >&2
        _violation "an imperative 'mkdir .../var/...' in a numbered build step (Law 2 NO-MKDIR-IN-VAR) -- declare the path in usr/lib/tmpfiles.d/*.conf instead"
    else
        echo "[98-drift-checks]   no imperative /var mkdir in numbered automation/Containerfiles"
    fi
}

_privileged_quadlet_array() {
    # The sed form treated `key = []` as an array OPENING, found no line
    # starting with `]` to close the range, and ran to EOF.
    awk -v key="$2" '
        /^\[/ { insec = ($0 ~ /^\[security\.privileged_quadlets\]/); next }
        !insec { next }
        !inarr {
            if ($0 ~ "^" key "[[:space:]]*=[[:space:]]*[[]") {
                line = $0
                sub("^" key "[[:space:]]*=[[:space:]]*[[]", "", line)
                if (line ~ /\]/) { sub(/\].*$/, "", line); print line; next }
                inarr = 1; print line
            }
            next
        }
        /^[[:space:]]*\]/ { inarr = 0; next }
        { print }
    ' "$1" 2>/dev/null | grep -oE '"[^"]+\.container"' | tr -d '"'
}

check_quadlet_privilege() {
    local toml="$ROOT/usr/share/mios/mios.toml"
    if [[ ! -f "$toml" ]]; then
        _violation "mios.toml absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local root_allow ngd_allow
    root_allow="$(_privileged_quadlet_array "$toml" root)"
    ngd_allow="$(_privileged_quadlet_array "$toml" no_group_delegate)"
    # An empty allowlist is a LEGAL policy (no Quadlet may run as root) and the
    # check must then flag every root Quadlet rather than stand down. A missing
    # SECTION is SSOT damage in a tracked deliverable, which is not a skip.
    if ! grep -q '^\[security\.privileged_quadlets\]' "$toml"; then
        _violation "[security.privileged_quadlets] is absent from mios.toml, so Law 6 could not be evaluated against any allowlist"
        return
    fi
    local bad="" f base user
    for d in "$ROOT/usr/share/containers/systemd" "$ROOT/etc/containers/systemd"; do
        [[ -d "$d" ]] || continue
        while IFS= read -r f; do
            [[ -f "$f" ]] || continue
            base="$(basename "$f")"
            user=""
            if grep -qE '^[[:space:]]*User=' "$f"; then
                user="$(grep -hE '^[[:space:]]*User=' "$f" | head -1 | sed -E 's/^[[:space:]]*User=//' | tr -d '[:space:]')"
            fi
            if [[ -z "$user" || "$user" == "root" || "$user" == "0" ]]; then
                if ! printf '%s\n' "$root_allow" | grep -qxF "$base"; then
                    bad+="    $base: implicitly/explicitly root (User=$user) but NOT in [security.privileged_quadlets].root"$'\n'
                fi
            fi
            if ! printf '%s\n' "$ngd_allow" | grep -qxF "$base"; then
                if [[ -n "$user" && "$user" != "root" && "$user" != "0" ]]; then
                    grep -qE '^[[:space:]]*Group='        "$f" || bad+="    $base: missing Group= for non-root User (Law 6)"$'\n'
                fi
                grep -qE '^[[:space:]]*Delegate=yes'  "$f" || bad+="    $base: missing Delegate=yes (Law 6)"$'\n'
            fi
        done < <(find "$d" -type f -name '*.container' 2>/dev/null)
    done
    if [[ -n "$bad" ]]; then
        printf '%s' "$bad" >&2
        _violation "a Quadlet violates Law 6 (UNPRIVILEGED-QUADLETS): missing User=, an undocumented User=root/0 (add to [security.privileged_quadlets].root with a justification), or a missing Group=/Delegate=yes (exempt via [...].no_group_delegate)"
    else
        echo "[98-drift-checks]   every Quadlet declares User=; root only where allowlisted; Group=/Delegate=yes present"
    fi
}

check_var_closure() {
    local tool="$ROOT/automation/lib/mios_var_closure.py"
    local ledger="$ROOT/usr/share/mios/reference/var-closure-baseline.tsv"
    _need_python || return 0
    if [[ ! -f "$tool" ]]; then
        _violation "mios_var_closure.py absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    if [[ ! -f "$ledger" ]]; then
        _violation "var-closure-baseline.tsv absent -- a gate with no ledger must never report green (task_989)"
        return
    fi
    local _vc_rc=0
    MIOS_ROOT="$ROOT" python3 "$tool" >"$ROOT/.varclosure.out" 2>"$ROOT/.varclosure.err" || _vc_rc=$?
    # rc=2 is the emitter refusing to answer -- a partial or broken resolver.
    # Reporting that as a closure breach sends the reader to the wrong file.
    if (( _vc_rc == 2 )); then
        sed 's/^/    /' "$ROOT/.varclosure.err" >&2 2>/dev/null || true
        rm -f "$ROOT/.varclosure.out" "$ROOT/.varclosure.err" 2>/dev/null || true
        _violation "var-closure could not build a complete emitted set (the SSOT resolver is broken or partial) -- run python automation/lib/mios_var_closure.py"
        return
    fi

    local found ceiling n_found n_ledger
    found="$(sed -n 's/^  \(MIOS_[A-Z0-9_]*\)  .*/\1/p' "$ROOT/.varclosure.err" | sort -u)"
    rm -f "$ROOT/.varclosure.out" "$ROOT/.varclosure.err" 2>/dev/null || true
    ceiling="$(sed -n 's/^#!ceiling \([0-9]*\).*/\1/p' "$ledger" | head -1)"
    if [[ -z "$ceiling" ]]; then
        _violation "var-closure-baseline.tsv carries no #!ceiling -- an unbounded ledger is not a ratchet"
        return
    fi
    n_found="$(printf '%s\n' "$found" | grep -c '^MIOS_' || true)"
    n_ledger="$(grep -c '^MIOS_' "$ledger" || true)"

    # The ledger anchors the SCAN, not just the verdict. This gate's previous
    # version passed because its scan reached no consumer at all; a named ledger
    # makes that failure mode loud -- the names would simply vanish.
    local nleft nright
    nleft="$(comm -23 <(printf '%s\n' "$found" | grep '^MIOS_' | sort -u) <(grep '^MIOS_' "$ledger" | cut -f1 | sort -u) | head -20)"
    nright="$(comm -13 <(printf '%s\n' "$found" | grep '^MIOS_' | sort -u) <(grep '^MIOS_' "$ledger" | cut -f1 | sort -u) | head -20)"
    local bad=0
    if [[ -n "$nleft" ]]; then
        bad=1
        while IFS= read -r v; do
            [[ -n "$v" ]] && _violation "var-closure: '$v' is referenced but NOT emitted and is not on the ledger -- emit it from the SSOT cascade or stop referencing it"
        done <<< "$nleft"
    fi
    if [[ -n "$nright" ]]; then
        bad=1
        while IFS= read -r v; do
            [[ -n "$v" ]] && _violation "var-closure: '$v' is on the ledger but no longer found -- tighten the ledger and the ceiling in the same commit"
        done <<< "$nright"
    fi
    if [[ "$n_ledger" != "$ceiling" ]]; then
        bad=1
        _violation "var-closure: the ledger holds $n_ledger name(s) but declares a ceiling of $ceiling"
    fi
    if [[ "$n_found" != "$ceiling" ]]; then
        bad=1
        _violation "var-closure: $n_found referenced-but-unemitted name(s) against a ceiling of $ceiling -- this ratchet is EXACT in both directions"
    fi
    (( bad == 0 )) && echo "[98-drift-checks]   MIOS_* closure: $n_found referenced-but-unemitted name(s), all on the ledger (ceiling $ceiling)"
    return 0
}

check_lint_is_final() {
    # Globbed, not named: a hardcoded list silently skips a file that was
    # renamed and reports success over the ones that remain.
    local bad="" cf last n=0 want="RUN bootc container lint"
    for cf in "$ROOT"/Containerfile*; do
        [[ -f "$cf" ]] || continue
        # Only bootc container images are subject to bootc container lint (Law 4)
        if ! grep -iqE '^[[:space:]]*LABEL[[:space:]]+.*containers\.bootc=["'\'']?1(["'\'']|[[:space:]]|$)' "$cf"; then
            # Negative control: non-bootc images must NOT include bootc container lint
            last="$(grep -vE '^[[:space:]]*(#|$)' "$cf" 2>/dev/null | tail -1 || true)"
            if grep -qF "$want" "$cf"; then
                bad+="    ${cf#"$ROOT"/}: non-bootc container image contains invalid [$want]"$'\n'
            fi
            continue
        fi
        n=$((n + 1))
        last="$(grep -vE '^[[:space:]]*(#|$)' "$cf" 2>/dev/null | tail -1 || true)"
        last="${last%$'\r'}"
        if [[ "$last" != "$want" ]]; then
            bad+="    ${cf#"$ROOT"/}: final instruction is [$last], expected [$want]"$'\n'
        fi
    done
    if [[ "$n" -eq 0 ]]; then
        _violation "(43) no bootc Containerfile* (carrying LABEL containers.bootc=1) at the repo root -- Law 4 would pass vacuously"
    elif [[ -n "$bad" ]]; then
        printf '%s' "$bad" >&2
        _violation "a Containerfile's final instruction is invalid (Law 4 BOOTC-CONTAINER-LINT) -- bootc images must end with '$want'; non-bootc images must not"
    else
        echo "[98-drift-checks]   all $n bootc root Containerfile(s) end with 'RUN bootc container lint'"
    fi
}

# --- firstboot scripts degrade open on egress failure (Law 12) ---
check_firstboot_degrade_open() {
    _run_py_check check_firstboot_degrade_open "tools/check-runtime.py firstboot-degrade-open"
}

check_vendor_urls() {
    local pattern='https?://(api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.cohere\.|api\.mistral\.|api\.cline\.bot|api\.cursor\.com|api\.githubcopilot\.com)'
    local hits="" f active
    for d in "${SCAN_DIRS[@]}"; do
        [[ -d "$d" ]] || continue
        while IFS= read -r f; do
            [[ -f "$f" ]] || continue
            active=$(sed -E '/^[[:space:]]*(#|\/\/)/d' "$f")
            if printf '%s\n' "$active" | grep -qE "$pattern"; then
                hits+="    $f"$'\n'
            fi
        done < <(find "$d" -type f \( -name '*.container' -o -name '*.service' \
            -o -name '*.json' -o -name '*.toml' -o -name '*.conf' -o -name '*.yaml' \
            -o -name '*.yml' \) 2>/dev/null)
    done
    if [[ -n "$hits" ]]; then
        printf '%s' "$hits" >&2
        _violation "a vendor cloud URL is hardcoded in active AI-plane config (Law 5 UNIFIED-AI-REDIRECTS) -- route through MIOS_AI_ENDPOINT"
    else
        echo "[98-drift-checks]   no vendor cloud URL in active config"
    fi

    # ADR-0016 D5: the VENDOR endpoint stays local; only an /etc overlay
    # may point it off-box.
    local ep_out
    ep_out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py ai-endpoint-local)"
    if [[ -n "$ep_out" ]]; then
        _violation "$ep_out"
    else
        echo "[98-drift-checks]   vendor [ai].endpoint is local"
    fi
}

check_resolver_twin_parity() {
    _need_python || return 0
    local ue="$ROOT/usr/lib/mios/userenv.sh" mt="$ROOT/usr/lib/mios/mios_toml.py"
    if [[ ! -f "$ue" || ! -f "$mt" ]]; then
        _violation "a resolver is absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local _nat="" _c
    if [[ -n "${MIOS_NATIVE_BIN_DIR:-}" ]]; then
        for _c in "$MIOS_NATIVE_BIN_DIR/mios-resolver" "$MIOS_NATIVE_BIN_DIR/mios-resolver.exe"; do
            [[ -x "$_c" ]] && { _nat="$_c"; break; }
        done
        if [[ -z "$_nat" ]]; then
            _violation "configured resolver is missing or not executable: $MIOS_NATIVE_BIN_DIR/mios-resolver"
            return
        fi
    else
        # debug BEFORE release, matching check_resolver_differential_parity and what
        # CI step 3 actually builds. Preferring release picked up a binary older
        # than the fix under test and reported its staleness as a twin mismatch.
        for _c in "$ROOT/tools/native/target/debug/mios-resolver" \
                  "$ROOT/tools/native/target/release/mios-resolver" \
                  /usr/libexec/mios/mios-resolver /usr/bin/mios-resolver; do
            [[ -x "$_c" ]] && { _nat="$_c"; break; }
        done
        if [[ -z "$_nat" ]]; then
            _violation "mios-resolver is not built, so userenv.sh would fall back to mios_toml.py and this check would compare it with itself -- build it: cd tools/native && cargo build -p mios-resolver"
            return
        fi
    fi

    # `local fix="$(...)"` returns the status of `local`, not of mktemp, so this
    # guard never fired: a failed mktemp left $fix EMPTY and the mkdir below
    # then targeted /vendor.d at the filesystem root. Declare, then assign.
    local fix
    fix="$(mktemp -d 2>/dev/null)" || fix=""
    if [[ -z "$fix" || ! -d "$fix" ]]; then
        _violation "check_resolver_twin_parity could not create a temp fixture, so twin parity is unverified"
        return
    fi
    mkdir -p "$fix/vendor.d" "$fix/.config/mios"
    # [ports].agent_pipe exists so the host layer's endpoint can reference it.
    # With three plain literals this fixture could not fail on the one input
    # shape where the twins can disagree, and it ran green through the whole of
    # 5efe9e70 -- the shell binding exporting ${MIOS_*} as literal text (task_3360).
    printf '[ports]\nagent_pipe = 8700\n[ai]\nendpoint = "http://vendor:1000"\nmodel = "vendor-model"\nembed_model = "vendor-embed"\n' > "$fix/vendor.toml"
    printf '[ai]\nendpoint = "http://vendor-frag:1050"\n'                                                 > "$fix/vendor.d/50-frag.toml"
    # The cross-reference goes in the WINNING layer. Put on vendor it was
    # overridden by host and never reached the resolved value, so the fixture
    # still could not fail -- a repaired check that is still vacuous.
    printf '[ai]\nendpoint = "http://host:${MIOS_PORTS_AGENT_PIPE}"\nmodel = "host-model"\n'                 > "$fix/host.toml"
    printf '[ai]\nendpoint = ""\nmodel = "user-model"\n'                                                  > "$fix/.config/mios/mios.toml"
    local sel='^MIOS_AI_(ENDPOINT|MODEL|EMBED_MODEL)=' bash_out py_out
    mkdir -p "$fix/bin" && ln -sf "$_nat" "$fix/bin/mios-resolver"
    bash_out="$(env -i PATH="$fix/bin:$PATH" HOME="$fix" XDG_CONFIG_HOME="$fix/.config" \
        MIOS_VENDOR_TOML="$fix/vendor.toml" MIOS_VENDOR_TOML_D="$fix/vendor.d" \
        MIOS_HOST_TOML="$fix/host.toml" MIOS_HOST_TOML_D="$fix/host.d" \
        bash -c ". '$ue' >/dev/null 2>&1; env" 2>/dev/null | grep -E "$sel" | sort)"
    py_out="$(env -i PATH="$PATH" \
        MIOS_VENDOR_TOML="$fix/vendor.toml" MIOS_VENDOR_TOML_D="$fix/vendor.d" \
        MIOS_HOST_TOML="$fix/host.toml" MIOS_HOST_TOML_D="$fix/host.d" \
        MIOS_USER_TOML="$fix/.config/mios/mios.toml" MIOS_USER_TOML_D="$fix/.config/mios/mios.d" \
        MIOS_ROOT_LIB="$ROOT/usr/lib/mios" "$PYTHON" -c '
import os, sys
sys.path.insert(0, os.environ["MIOS_ROOT_LIB"])
import mios_toml
for k, v in sorted(mios_toml.emit_exports().items()):
    print(k + "=" + str(v))
' 2>/dev/null | grep -E "$sel" | sort)"
    rm -rf "$fix" 2>/dev/null || true
    if [[ -z "$bash_out" && -z "$py_out" ]]; then
        _violation "neither resolver emitted MIOS_AI_* for the layered fixture, so twin parity is unverified"
        return
    fi
    if [[ "$bash_out" == "$py_out" ]]; then
        # Name both implementations: the old line said "userenv.sh and
        # mios_toml.py" while both legs were mios_toml.py.
        echo "[98-drift-checks]   resolver twin parity: userenv.sh via native mios-resolver agrees with mios_toml.py on the layered MIOS_AI_* set"
    else
        # Was a SOFT WARNING with no _violation, so a real disagreement
        # between the twins reported success. Law 13 requires agreement.
        echo "        userenv.sh -> $(printf '%s' "$bash_out" | tr '\n' ' ')" >&2
        echo "        mios_toml  -> $(printf '%s' "$py_out"   | tr '\n' ' ')" >&2
        _violation "resolver twin-parity mismatch: userenv.sh and mios_toml.py disagree on the layered MIOS_AI_* set"
        return
    fi
}

check_resolver_twin_equivalence() {
    _need_python || return 0
    local mismatches
    # MIOS_VERSION_MANIFEST (and other build-time MIOS_* vars) into this gate's env, and
    if ! mismatches=$(env -i PATH="$PATH" MIOS_DRIFT_ROOT="$ROOT" python3 "$ROOT/tools/check-runtime.py" resolver-twin 2>&1); then
        printf '%s\n' "$mismatches" >&2
        _violation "resolver twin equivalence check failed -- userenv.sh and mios_toml.py have drifted"
    else
        echo "[98-drift-checks]   resolver twin equivalence: userenv.sh and mios_toml.py are equivalent"
    fi
}

check_template_conformance() {
    _need_python || return 0
    local tool="$ROOT/usr/libexec/mios/check-template-conformance"
    if [[ ! -f "$tool" ]]; then
        _violation "check-template-conformance not found -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local errors
    if ! errors=$(MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" python3 "$tool" --root "$ROOT" 2>&1); then
        printf '%s\n' "$errors" >&2
        _violation "template conformance check failed -- new/modified files must follow their templates"
    else
        echo "[98-drift-checks]   template conformance: all new files conform to templates"
    fi
}

# --- kargs.d managed files match the mios.toml [kargs] projection ---
check_kargs_projection() {
    _need_python || return 0

    local managed=("01-mios-vfio.toml" "99-mios-kargs.toml")

    local src="$ROOT/usr/lib/bootc/kargs.d"
    local tmp_dir; tmp_dir="$(mktemp -d)"
    local f base

    # Seed ONLY the managed files, so the renderer sees the in-place inputs it
    # expects while every unmanaged file is absent from the comparison.
    for base in "${managed[@]}"; do
        [[ -f "$src/$base" ]] && cp "$src/$base" "$tmp_dir/$base"
    done

    local rc=0 out
    out="$(MIOS_TOML="$ROOT/usr/share/mios/mios.toml" KARGS_DIR="$tmp_dir"            bash "$ROOT/automation/75-kargs-render.sh" 2>&1)" || rc=$?
    if (( rc != 0 )); then
        rm -rf "$tmp_dir"
        _violation "kargs renderer automation/75-kargs-render.sh exited ${rc}: ${out}"
        return
    fi

    if ! python3 "$ROOT/automation/validate-kargs.py" "$tmp_dir" >/dev/null 2>&1; then
        rm -rf "$tmp_dir"
        _violation "rendered kargs.d files failed validate-kargs.py schema validation"
        return
    fi

    local diffs=""
    for base in "${managed[@]}"; do
        local rendered="$tmp_dir/$base" committed="$src/$base"
        if [[ -f "$rendered" && ! -f "$committed" ]]; then
            diffs+="    $base: the renderer produces it, but it is not committed"$'
'
        elif [[ ! -f "$rendered" && -f "$committed" ]]; then
            diffs+="    $base: committed, but the renderer removes it (is [kargs] empty?)"$'
'
        elif [[ -f "$rendered" && -f "$committed" ]]; then
            if ! diff -u --label "a/$base (committed)" --label "b/$base (rendered)"                     "$committed" "$rendered" >&2; then
                diffs+="    $base: content drift -- run automation/75-kargs-render.sh"$'
'
            fi
        fi
    done

    rm -rf "$tmp_dir"

    if [[ -n "$diffs" ]]; then
        printf '%s' "$diffs" >&2
        _violation "kargs.d projection drifted from mios.toml [kargs]"
    else
        echo "[98-drift-checks]   kargs.d projected files (${managed[*]}) match [kargs] SSOT"
    fi
}

check_greenboot_enablement() {
    if ! grep -q "greenboot-healthcheck.service" "$ROOT/automation/78-greenboot.sh" || \
       ! grep -q "greenboot-set-rollback-trigger.service" "$ROOT/automation/78-greenboot.sh"; then
        _violation "greenboot services enablement commands are missing in automation/78-greenboot.sh"
    fi

    local non_execs=""
    local f
    if [[ -d "$ROOT/etc/greenboot" ]]; then
        while read -r f; do
            [[ -f "$f" ]] || continue
            local relpath
            relpath="$(realpath --relative-to="$ROOT" "$f")"
            local mode=""
            if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
                mode="$(git -C "$ROOT" ls-files -s "$relpath" 2>/dev/null | awk '{print $1}')"
            fi
            if [[ -n "$mode" && "$mode" != "100755" ]]; then
                non_execs+="    $relpath has git mode $mode (expected 100755)"$'\n'
            fi
        done < <(find "$ROOT/etc/greenboot" -name "*.sh")
    fi

    if [[ -n "$non_execs" ]]; then
        printf '%s' "$non_execs" >&2
        _violation "greenboot check scripts must be executable (mode 100755)"
    else
        echo "[98-drift-checks]   greenboot services and scripts are correctly configured"
    fi
}

check_chrony_projection() {
    local tmp_file
    tmp_file="$(mktemp)"

    MIOS_TOML="$ROOT/usr/share/mios/mios.toml" CHRONY_CONF="$tmp_file" bash "$ROOT/automation/42-chrony-render.sh" >/dev/null 2>&1

    if [[ ! -f "$ROOT/etc/chrony.conf" ]]; then
        rm -f "$tmp_file"
        _violation "committed etc/chrony.conf is missing"
        return
    fi

    if ! diff -u "$ROOT/etc/chrony.conf" "$tmp_file" >/dev/null 2>&1; then
        diff -u "$ROOT/etc/chrony.conf" "$tmp_file" >&2
        rm -f "$tmp_file"
        _violation "etc/chrony.conf check failed. Rendered NTP config does not match committed etc/chrony.conf."
    else
        echo "[98-drift-checks]   chrony.conf matches mios.toml [network.ntp] projection"
        rm -f "$tmp_file"
    fi
}

check_nut_projection() {
    local tmp_dir
    tmp_dir="$(mktemp -d)"

    MIOS_TOML="$ROOT/usr/share/mios/mios.toml" UPS_CONF_DIR="$tmp_dir" bash "$ROOT/automation/43-nut-render.sh" >/dev/null 2>&1

    local diffs=""
    local f base
    for f in "$tmp_dir"/*.conf; do
        [[ -f "$f" ]] || continue
        base="$(basename "$f")"
        if [[ ! -f "$ROOT/etc/ups/$base" ]]; then
            diffs+="    Extra rendered NUT config: $base"$'\n'
        elif ! diff -u "$ROOT/etc/ups/$base" "$f" >/dev/null 2>&1; then
            diffs+="    Content drift in etc/ups/$base"$'\n'
        fi
    done

    for f in "$ROOT/etc/ups"/*.conf; do
        [[ -f "$f" ]] || continue
        base="$(basename "$f")"
        if [[ ! -f "$tmp_dir/$base" ]]; then
            diffs+="    Missing rendered NUT config: $base"$'\n'
        fi
    done

    rm -rf "$tmp_dir"

    local preset="$ROOT/usr/lib/systemd/system-preset/90-mios.preset"
    if [[ -f "$preset" ]]; then
        if ! grep -qE '^\s*enable\s+nut-server\.service' "$preset" || ! grep -qE '^\s*enable\s+nut-monitor\.service' "$preset"; then
            diffs+="    90-mios.preset missing enable line for nut-server.service or nut-monitor.service"$'\n'
        fi
    fi

    if [[ -n "$diffs" ]]; then
        printf '%s' "$diffs" >&2
        _violation "etc/ups/ configuration check failed. Rendered NUT configs do not match committed etc/ups/ files."
    else
        echo "[98-drift-checks]   etc/ups/ configurations match mios.toml [power.ups] projection"
    fi
}

check_fluff_tokens() {
    local bad=""
    local f

    while read -r f; do
        [[ -f "$f" ]] || continue
        local bname
        bname="$(basename "$f")"
        if [[ "$bname" == "98-drift-checks.sh" || "$bname" == "build-mios.sh" || "$bname" == "99-postcheck.sh" || "$f" =~ /firstboot/ ]]; then
            continue
        fi

        local line_num=0
        while read -r line || [[ -n "$line" ]]; do
            line_num=$((line_num + 1))
            [[ "$line" =~ ^[[:space:]]*# ]] && continue

            if [[ "$line" =~ (echo|log|warn|die)[[:space:]] ]]; then
                if [[ "$line" =~ successfully ]]; then
                    bad+="    $f:$line_num: contains 'successfully'"$'\n'
                fi
                if [[ "$line" =~ "BAKED IN" ]]; then
                    bad+="    $f:$line_num: contains 'BAKED IN'"$'\n'
                fi
                if [[ "$line" =~ \"Done\" ]] || [[ "$line" =~ \"Done.\" ]] || [[ "$line" =~ \'Done\' ]] || [[ "$line" =~ \'Done.\' ]]; then
                    bad+="    $f:$line_num: contains bare 'Done'"$'\n'
                fi
                if [[ "$line" =~ ![[:space:]]*[\"\'][[:space:]]*$ ]]; then
                    bad+="    $f:$line_num: contains trailing '!'"$'\n'
                fi
            fi
        done < "$f"
    done < <(find "$ROOT/automation" -name "*.sh")

    if [[ -n "$bad" ]]; then
        printf '%s' "$bad" >&2
        _violation "fluff tokens detected in pipeline logs (E5)"
    else
        echo "[98-drift-checks]   fluff-token drift check passed"
    fi
}

check_coordination_hygiene() {
    # The two absorbed ledgers, rebuilt from the frozen history in tasks.jsonl (ADR-0028).
    local bad="" f text bin
    bin="$(native_bin mios-task)" || { _violation "mios-task is not built, so check_coordination_hygiene could not run -- build it: cd tools/native && cargo build -p mios-task"; return; }
    for f in AGY-TASKS.md TASKS.md; do
        text="$("$bin" source "MiOS:$f" --root "$ROOT")" || { _violation "mios-task could not rebuild $f from tasks.jsonl"; return; }
        local line_num=0
        while read -r line || [[ -n "$line" ]]; do
            line_num=$((line_num + 1))
            if [[ "$line" =~ AppData ]] || [[ "$line" =~ \bTemp\b ]] || [[ "$line" =~ [0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12} ]]; then
                bad+="    $f:$line_num: contains AppData/Temp/session-id path"$'\n'
            fi
        done <<< "$text"
    done

    if [[ -n "$bad" ]]; then
        printf '%s' "$bad" >&2
        _violation "coordination-hygiene lint failed (E6)"
    else
        echo "[98-drift-checks]   coordination-hygiene lint passed"
    fi
}

check_templates_compilation() {
    local bin; bin="$(native_bin mios-template-compile)" || { _violation "native mios-template-compile is required (tools/compile-templates.py was ported and deleted, ADR-0021) -- build it: cd tools/native && cargo build -p mios-template-compile"; return; }
    if ! "$bin" --root "$ROOT" --check >/dev/null; then
        "$bin" --root "$ROOT" --check >&2
        _violation "compile-templates validation failed. One or more templates in usr/share/mios/templates are syntactically invalid."
    else
        echo "[98-drift-checks]   all templates compile and validate successfully"
    fi
}


check_impossible_eol_regressions() {
    local bad=""

    local toml="$ROOT/usr/share/mios/mios.toml"
    if grep -E '"glusterfs"' "$toml" &>/dev/null || grep -E '"glusterfs-fuse"' "$toml" &>/dev/null || grep -E '"glusterfs-server"' "$toml" &>/dev/null; then
        bad+="    Found glusterfs packages in mios.toml"$'\n'
    fi

    local f
    while read -r f; do
        [[ -f "$f" ]] || continue
        [[ "$(basename "$f")" == "mios-metal-architecture.md" ]] && continue

        if grep -F "mdevctl vGPU" "$f" &>/dev/null; then
            if ! grep -E "mdevctl vGPU.*(impossible|unsupported|out of scope|reject)" "$f" &>/dev/null; then
                bad+="    $f: contains 'mdevctl vGPU' claim without rejecting it"$'\n'
            fi
        fi
    done < <(find "$ROOT/usr/share/doc/mios/concepts" -name "*.md")

    if grep -E '"tang"' "$toml" &>/dev/null; then
        bad+="    Found tang package in mios.toml (on-host Tang is prohibited)"$'\n'
    fi

    if [[ -n "$bad" ]]; then
        printf '%s' "$bad" >&2
        _violation "impossible/EOL regression check failed (F11)"
    else
        echo "[98-drift-checks]   impossible/EOL regression checks passed"
    fi
}

check_deploy_plane() {
    local bad=""
    local ks_file=""
    for cand in "$ROOT/usr/share/mios/ventoy/mios-kickstart.cfg" \
                "${BOOTSTRAP_DIR:-}/field/resources/ventoy/mios-kickstart.cfg" \
                "/c/mios-bootstrap/field/resources/ventoy/mios-kickstart.cfg" \
                "/mios-bootstrap/field/resources/ventoy/mios-kickstart.cfg" \
                "C:/mios-bootstrap/field/resources/ventoy/mios-kickstart.cfg" \
                "$ROOT/../mios-bootstrap/field/resources/ventoy/mios-kickstart.cfg" \
                "$ROOT/field/resources/ventoy/mios-kickstart.cfg"; do
        if [[ -n "$cand" && -f "$cand" ]]; then
            ks_file="$cand"
            break
        fi
    done

    if [[ -n "$ks_file" ]]; then
        if ! grep -q "MIOS_FHS_TOTAL_ROOT_MERGE=1" "$ks_file"; then
            bad+="    mios-kickstart.cfg: missing MIOS_FHS_TOTAL_ROOT_MERGE=1 export"$'\n'
        fi
        if ! grep -q "BOOTSTRAP_REPO" "$ks_file" || ! grep -q "MIOS_REPO" "$ks_file"; then
            bad+="    mios-kickstart.cfg: missing BOOTSTRAP_REPO or MIOS_REPO offline overrides"$'\n'
        fi
    else
        echo "[98-drift-checks]   WARNING: mios-kickstart.cfg not found, skipping kickstart exports assertion"
    fi

    local ventoy_json=""
    for cand in "$ROOT/usr/share/mios/ventoy/ventoy.json" \
                "${BOOTSTRAP_DIR:-}/field/resources/ventoy/ventoy.json" \
                "/c/mios-bootstrap/field/resources/ventoy/ventoy.json" \
                "/mios-bootstrap/field/resources/ventoy/ventoy.json" \
                "C:/mios-bootstrap/field/resources/ventoy/ventoy.json" \
                "$ROOT/../mios-bootstrap/field/resources/ventoy/ventoy.json" \
                "$ROOT/field/resources/ventoy/ventoy.json"; do
        if [[ -n "$cand" && -f "$cand" ]]; then
            ventoy_json="$cand"
            break
        fi
    done

    if [[ -n "$ventoy_json" ]]; then
        if ! grep -q "Fedora-Server.iso" "$ventoy_json" || ! grep -q "mios-kickstart.cfg" "$ventoy_json"; then
            bad+="    ventoy.json: missing Fedora-Server.iso/mios-kickstart.cfg binding in kickstart section"$'\n'
        fi
    else
        echo "[98-drift-checks]   WARNING: ventoy.json not found, skipping ISO-kickstart binding check"
    fi

    local toml="$ROOT/usr/share/mios/mios.toml"
    local base_image_version
    local base_image
    base_image=$(grep -E '^[[:space:]]*base_image[[:space:]]*=' "$toml" | head -n1 | cut -d'"' -f2)
    if [[ -n "$base_image" ]]; then
        base_image_version=$(echo "$base_image" | grep -oE '[0-9]+$')
        if [[ -n "$base_image_version" ]]; then
            if [[ -f "$ks_file" ]]; then
                if grep -oE 'Fedora-Server-[0-9]+' "$ks_file" | grep -qv "Fedora-Server-${base_image_version}" &>/dev/null; then
                    local mismatched_version
                    mismatched_version=$(grep -oE 'Fedora-Server-[0-9]+' "$ks_file" | head -n1)
                    bad+="    kickstart/base_image: version mismatch (${mismatched_version} vs Fedora ${base_image_version})"$'\n'
                fi
            fi
        fi
    fi

    if [[ -n "$bad" ]]; then
        printf '%s' "$bad" >&2
        _violation "deploy-plane drift check failed (G11)"
    else
        echo "[98-drift-checks]   deploy-plane checks passed"
    fi
}

check_version_ssot() {
    local toml="$ROOT/usr/share/mios/mios.toml"
    local ssot vfile bad=""
    ssot="$(grep -m1 -E '^[[:space:]]*mios_version' "$toml" 2>/dev/null | sed -E 's/[^"]*"([^"]*)".*/\1/')"
    vfile="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null)"
    if [[ -z "$ssot" ]]; then
        _violation "version SSOT: mios.toml [meta].mios_version is empty/unparseable"
        return
    fi
    [[ "$vfile" != "$ssot" ]] && bad+="    VERSION file = [$vfile], expected [$ssot]"$'\n'

    local _cf _cv
    while read -r _cf; do
        [[ -f "$ROOT/$_cf" ]] || continue
        _cv="$(grep -m1 -E '^ARG[[:space:]]+MIOS_VERSION=' "$ROOT/$_cf" 2>/dev/null | sed -E 's/^ARG[[:space:]]+MIOS_VERSION=//; s/[[:space:]].*//' || true)"
        [[ -n "$_cv" && "$_cv" != "$ssot" ]] && bad+="    $_cf ARG MIOS_VERSION default = [$_cv], expected [$ssot]"$'\n'
    done < <(git ls-files "*Containerfile*" 2>/dev/null || find "$ROOT" -name "*Containerfile*" -type f)

    local osr="$ROOT/usr/lib/os-release" _f _v
    if [[ -f "$osr" ]]; then
        for _f in VERSION VERSION_ID BUILD_ID IMAGE_VERSION OSTREE_VERSION; do
            _v="$(grep -m1 -E "^${_f}=" "$osr" | sed -E 's/^[^=]+=//; s/^"//; s/"[[:space:]]*$//')"
            [[ -n "$_v" && "$_v" != "$ssot" ]] && bad+="    os-release ${_f} = [$_v], expected [$ssot]"$'\n'
        done
        _v="$(grep -m1 -E '^PRETTY_NAME=' "$osr" | sed -E 's/.*MiOS //; s/"[[:space:]]*$//')"
        [[ -n "$_v" && "$_v" != "$ssot" ]] && bad+="    os-release PRETTY_NAME version = [$_v], expected [$ssot]"$'\n'
        _v="$(grep -m1 -E '^CPE_NAME=' "$osr" | sed -E 's|.*:mios:||; s/"[[:space:]]*$//')"
        [[ -n "$_v" && "$_v" != "$ssot" ]] && bad+="    os-release CPE_NAME version = [$_v], expected [$ssot]"$'\n'
    fi

    local _cargo_ver
    for _toml in "$ROOT/tools/native/Cargo.toml" "$ROOT/tools/native/mios-version-check/Cargo.toml" "$ROOT/tools/native/mios-wallpaperd/Cargo.toml"; do
        _subject_present "$_toml" || continue
        _cargo_ver="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$_toml" 2>/dev/null | sed -E 's/[^"]*"([^"]*)".*/\1/' || true)"
        [[ -n "$_cargo_ver" && "$_cargo_ver" != "$ssot" ]] && bad+="    ${_toml#$ROOT/} version = [$_cargo_ver], expected [$ssot]"$'\n'
    done

    # Ported to the native gate (ADR-0021), beside doc_refs. No python fallback:
    # certifying a program other than the one that runs is itself the defect.
    local literal_bad="" vlbin=""
    vlbin="$(_gate_bin)" || vlbin=""
    if [[ -z "$vlbin" ]]; then
        bad+="    mios-gate is not built, so version literals were NOT scanned -- cd src/mios-rs && cargo build -p mios-gate"$'\n'
    elif ! literal_bad="$(MIOS_CANONICAL_VER="$ssot" "$vlbin" version-literals-ssot --root "$ROOT" 2>&1)"; then
        bad+="$literal_bad"$'\n'
    elif [[ -n "$literal_bad" ]]; then
        echo "$literal_bad" >&2
    fi

    if [[ -n "$bad" ]]; then
        printf '%s' "$bad" >&2
        _violation "version drift from SSOT mios.toml [meta].mios_version=[$ssot] (Law 7 NO-HARDCODE / Law 8 SSOT-PROJECTION) -- VERSION file + Containerfile ARG default must match the SSOT"
    else
        echo "[98-drift-checks]   VERSION + Containerfile ARG MIOS_VERSION == mios.toml [meta].mios_version"
    fi
}

# --- tools/native/Cargo.toml equals its generator projection ---
check_cargo_manifest_generated() {
    # The generator carried its member list as a literal and had fallen two
    # crates behind the tree, so regenerating dropped them out of the
    # workspace: on disk, never compiled, never tested, never shipped.
    local bin; bin="$(native_bin mios-gen "${MIOS_GEN_BIN:-}")" || true
    if [[ -n "$bin" ]]; then
        local out rc=0
        out="$( "$bin" cargo-manifests --root "$ROOT" --check 2>&1 )" || rc=$?
        if (( rc == 0 )); then
            echo "[98-drift-checks]   tools/native/Cargo.toml matches its generator projection"
            return 0
        else
            echo "$out" >&2
            _emit_projection_evidence "cargo-manifests" "tools/native/Cargo.toml"
            _violation "check_cargo_manifest_generated: tools/native/Cargo.toml drifted from mios-gen cargo-manifests -- re-run mios-gen cargo-manifests (Law 8 SSOT-PROJECTION)"
            return
        fi
    fi
    _violation "check_cargo_manifest_generated: native mios-gen is required; build/install the SSOT release catalog"
}

check_root_toml_subset() {
    _need_python || return 0
    local rc=0
    python3 -c "
import os, sys
import tomllib

def get_keys(d, prefix=''):
    keys = set()
    for k, v in d.items():
        full = f'{prefix}.{k}' if prefix else k
        keys.add(full)
        if isinstance(v, dict):
            keys.update(get_keys(v, full))
    return keys

root = os.environ.get('MIOS_DRIFT_ROOT', '.')
root_toml = os.path.join(root, 'mios.toml')
canonical = os.path.join(root, 'usr/share/mios/mios.toml')

if not os.path.isfile(root_toml):
    # Say so. Exiting 0 here made the gate print 'root mios.toml schema is
    # subset of canonical SSOT' -- a claim about a comparison it never ran, and
    # the reader has no way to tell that from a real pass.
    sys.exit(2)

with open(root_toml, 'rb') as f:
    r_data = tomllib.load(f)
with open(canonical, 'rb') as f:
    c_data = tomllib.load(f)

r_keys = get_keys(r_data)
c_keys = get_keys(c_data)

ignored_prefixes = (
    'autounattend', 'bootstrap', 'medicat', 'containers', 'ports.lan_firewall',
    'quadlets', 'smoke_tests', 'terminal.startup', 'branding.windows', 'branding.cursor',
    'branding.oem_', 'branding.wallpaper', 'branding.lockscreen', 'branding.ui_font',
    'branding.font_substitute', 'ai.enable_', 'terminal.startup', 'branding.living_wallpaper',
    'terminal.gui_min', 'theme.terminal.dev_profile_name', 'theme.terminal.hub_target_profile',
    'theme.terminal.summon_keys', 'theme.terminal.summon_window_name', 'mios_app'
)
filtered_r_keys = {k for k in r_keys if not any(k.startswith(pfx) for pfx in ignored_prefixes)}

diff = filtered_r_keys - c_keys
if diff:
    sys.stderr.write('    Drift: root mios.toml defines keys not in canonical SSOT:\\n')
    for k in sorted(diff):
        sys.stderr.write(f'      {k}\\n')
    sys.exit(1)
sys.exit(0)
    " || rc=$?
    if [[ $rc -eq 2 ]]; then
        # 2 means there is no root mios.toml. Printing the success line here
        # claimed a comparison that never ran, and a reader could not tell that
        # from a real pass.
        echo "[98-drift-checks]   no root mios.toml -- subset check not applicable"
    elif [[ $rc -eq 0 ]]; then
        echo "[98-drift-checks]   root mios.toml schema is subset of canonical SSOT"
    else
        _violation "root mios.toml schema has keys not in canonical SSOT"
    fi
}

check_toml_projection() {
    _need_python || return 0
    local tool="$ROOT/usr/libexec/mios/mios-sync-toml"
    if [[ ! -f "$tool" ]]; then
        _violation "usr/libexec/mios/mios-sync-toml absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    if python3 "$tool" --check >/dev/null 2>"$ROOT/.synctoml.err"; then
        rm -f "$ROOT/.synctoml.err" 2>/dev/null || true
        echo "[98-drift-checks]   mios.toml derived copies project verbatim from the canonical SSOT"
    else
        sed 's/^/    /' "$ROOT/.synctoml.err" >&2 2>/dev/null || true
        rm -f "$ROOT/.synctoml.err" 2>/dev/null || true
        _violation "a mios.toml derived copy drifted from the canonical [ports]/[colors] projection -- re-run usr/libexec/mios/mios-sync-toml"
    fi
}

check_render_extension_coverage() {
    # A placeholder in a file type 34-render-quadlets.sh does not substitute
    # ships verbatim. mios-cockpit-link.socket carried
    # ListenStream=0.0.0.0:${MIOS_PORTS_COCKPIT_LINK} because `.socket` was
    # missing from the renderer's find filter (task_977).
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_render_extension_coverage could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" render-coverage --root "$ROOT"; then
        return 0
    else
        _violation "a tracked file carries a \${MIOS_*} placeholder the Quadlet renderer never substitutes -- add its extension to [build.quadlet_render].extensions"
    fi
}

check_size_ceiling() {
    local bin
    if ! bin="$(native_bin mios-size-ceiling)"; then
        _violation "mios-size-ceiling is not built, so check_size_ceiling could not run -- build it: cd tools/native && cargo build -p mios-size-ceiling"
        return
    fi
    if "$bin" --root "$ROOT" --check; then
        return 0
    else
        _violation "[legibility].max_tracked_mb is outside the band its measurement implies -- regenerate it: tools/native/target/release/mios-size-ceiling"
    fi
}

check_task_store() {
    # ADR-0028: `mios-task check` over tasks.jsonl, its frozen history, overrides and TASKS.md render.
    local bin
    if ! bin="$(native_bin mios-task)"; then
        _violation "mios-task is not built, so check_task_store could not run -- build it: cd tools/native && cargo build -p mios-task"
        return
    fi
    local out rc=0
    out="$("$bin" check --root "$ROOT" 2>&1)" || rc=$?
    if [[ $rc -ne 0 ]]; then
        printf '%s\n' "$out" | head -40 | sed 's/^/    /' >&2
        _violation "tasks.jsonl, its frozen history, its overrides or TASKS.md failed mios-task check (ADR-0028)"
        return
    fi
    echo "[98-drift-checks]   task list: $(printf '%s' "$out" | tail -1)"
}

check_task_ids() {
    # ADR-0028: no purged task id ([tasks.store].purged) in any tracked path or file; a task is task_<number>.
    local bin out rc=0
    bin="$(native_bin mios-task)" || { _violation "mios-task is not built, so check_task_ids could not run -- build it: cd tools/native && cargo build -p mios-task"; return; }
    out="$("$bin" check --tree --root "$ROOT" 2>&1)" || rc=$?
    if [[ $rc -ne 0 ]]; then
        printf '%s\n' "$out" | head -40 | sed 's/^/    /' >&2
        _violation "a purged task id is back in the tracked tree -- name the task task_<number> (mios-task check --tree, ADR-0028)"
        return
    fi
    echo "[98-drift-checks]   task ids: $(printf '%s' "$out" | tail -1)"
}

check_render_quadlets() {
    # Asserts every ${MIOS_*} in the render scope RESOLVES -- not that the tree
    # is already rendered. Stage 34 renders in place at bake; the tracked files
    # are templates (task_977).
    local bin
    if ! bin="$(native_bin mios-render-quadlets)"; then
        _violation "mios-render-quadlets is not built, so check_render_quadlets could not run -- build it: cd tools/native && cargo build -p mios-render-quadlets"
        return
    fi
    if "$bin" --root "$ROOT" --check; then
        return 0
    else
        _violation "a tracked file carries a \${MIOS_*} placeholder that resolves to nothing -- it would ship literal"
    fi
}

check_toolchain_pin() {
    # rust-toolchain.toml is generated, so the question is not "is a pin
    # present" but "is the committed pin still what the SSOT says". A hand
    # edit here silently un-pins CI, which is the exact state task_996 closed:
    # clippy::for_kv_map fired under 1.98.0 and killed four pushes that were
    # clean under the 1.94.1 a contributor happened to have.
    local bin
    if ! bin="$(native_bin mios-toolchain-pin)"; then
        _violation "mios-toolchain-pin is not built, so check_toolchain_pin could not run -- build it: cd tools/native && cargo build -p mios-toolchain-pin"
        return
    fi
    if "$bin" --root "$ROOT" --check; then
        return 0
    else
        _violation "rust-toolchain.toml is missing or differs from [build.toolchain] -- regenerate it: tools/native/target/release/mios-toolchain-pin"
    fi
}

# --- etc/mios/ai/config.json and usr/share/mios/ai/v1/config.json equal their [ai] + [ports] projection (Law 8) ---
check_ai_config_projection() {
    # Generated from [ai] + [ports]. A hand edit, an unregenerated SSOT move or
    # an unbuilt generator is a violation, never a skip.
    local bin
    if ! bin="$(native_bin mios-ai-config)"; then
        _violation "mios-ai-config is not built, so check_ai_config_projection could not run -- build it: cd tools/native && cargo build -p mios-ai-config"
        return
    fi
    if "$bin" --root "$ROOT" --check; then
        return 0
    else
        _violation "an AI client config.json (etc/mios/ai/ or usr/share/mios/ai/v1/) is missing or differs from [ai] + [ports] -- regenerate it: tools/native/target/debug/mios-ai-config --root ."
    fi
}

# --- ARTIFACT-PROMPT.md equals its [artifacts.daily] projection (Law 8) ---
check_artifact_prompt() {
    # The out-of-loop daily task fetches this file from main on every run, so
    # a hand edit or an unregenerated SSOT change reaches that agent directly.
    # An unbuilt generator is cannot-run, which is a violation, never a skip.
    local bin
    if ! bin="$(native_bin xtask)"; then
        _violation "xtask is not built, so check_artifact_prompt could not run -- build it: cd tools/native && cargo build -p xtask"
        return
    fi
    "$bin" artifact-prompt --root "$ROOT" --check && return 0
    _violation "ARTIFACT-PROMPT.md differs from [artifacts.daily] + usr/share/mios/templates/artifact-prompt, or could not be generated -- regenerate: cd tools/native && cargo run -q -p xtask -- artifact-prompt"
}

check_ai_artifacts() {
    local bin; bin="$(_gate_bin)" || { _violation "mios-gate is not built, so check_ai_artifacts could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"; return; }
    "$bin" artifact --root "$ROOT" && echo "[98-drift-checks]   AI artifacts (ADR-0027): SFT/DPO datasets, weight headers, OCI closure with digests" \
        || _violation "mios-gate artifact found AI-artifact violations (datasets, weight headers, or OCI descriptor closure) -- see its findings above"
}

check_ratchet_direction() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_ratchet_direction could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" ratchet-direction --root "$ROOT"; then
        echo "[98-drift-checks]   shrink-only ratchet ceilings in mios.toml do not exceed the baseline"
    else
        _violation "a shrink-only ratchet ceiling increased in mios.toml, or a generated-budget exemption is not itemised"
    fi
}

check_rust_categories() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_rust_categories could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" rust-categories --root "$ROOT"; then
        echo "[98-drift-checks]   [rust.categories] registry: owners, binaries, scopes and the unowned ceiling all hold"
    else
        _violation "the [rust.categories] ADR-0021 registry failed its ownership audit -- see the mios-gate rust-categories findings above"
    fi
}

check_target_languages() {
    # `-d "$ROOT/.git"` is FALSE inside a git worktree, where .git is a FILE,
    # so this skipped wherever gates run from a worktree. rev-parse is true for
    # a work tree, a worktree and a bare repo, and false when git is absent.
    if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        if [[ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" == "1" ]]; then
            _violation "check_target_languages cannot run: not a git repository, or git is absent"
            return
        fi
        echo "[98-drift-checks]   WARNING: not a git repo or git absent -- check_target_languages NOT verified" >&2
        return 0
    fi
    local toml="$ROOT/usr/share/mios/mios.toml"
    local allow bad="" nativebad f
    allow=$(awk '/^\[laws\.target_languages\]/{f=1} f&&/grandfathered_cs[[:space:]]*=[[:space:]]*\[/{g=1} g{print} g&&/\]/{exit}' "$toml" | grep -oE '"[^"]+\.cs"' | tr -d '"\r')
    nativebad=$(cd "$ROOT" && (git ls-files '*.bat' '*.cmd' '*.go' '*.cpp' '*.cxx' '*.cc' 2>/dev/null; git ls-files --others --exclude-standard '*.bat' '*.cmd' '*.go' '*.cpp' '*.cxx' '*.cc' 2>/dev/null) | grep -v '^tools/mios-portal-app/' || true)
    [[ -n "$nativebad" ]] && bad+="$nativebad"$'\n'
    while IFS= read -r f; do
        f_clean=$(echo "$f" | tr -d '\r')
        [[ -z "$f_clean" ]] && continue
        grep -qxF "$f_clean" <<<"$allow" || bad+="$f_clean"$'\n'
    done < <(cd "$ROOT" && (git ls-files '*.cs' 2>/dev/null; git ls-files --others --exclude-standard '*.cs' 2>/dev/null))
    if [[ -n "$(printf '%s' "$bad" | tr -d '[:space:]')" ]]; then
        {
          echo "    Law 14 TARGET-LANGUAGES: new code must use the roadmap targets (Rust native tier; Python AI;"
          echo "    Bun/TS Portal; bash thin-glue). These non-target-language files are not grandfathered:"
          printf '%s\n' "$bad" | sed '/^[[:space:]]*$/d;s/^/      - /'
          echo "    -> port to Rust, or add a legitimate pre-existing port target to"
          echo "       mios.toml [laws.target_languages].grandfathered_cs"
        } >&2
        _violation "Law 14 TARGET-LANGUAGES violated: new non-target-language source added"
    else
        echo "[98-drift-checks]   Law 14 TARGET-LANGUAGES: no new non-target-language code"
    fi
}

# Law 14 (TARGET-LANGUAGES) / task_3441: enforce static Linux linkage for native binaries
# --- static linkage gate: asserts absence of PT_INTERP and DT_NEEDED on Linux release binaries ---
check_static_linkage() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_static_linkage could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    local out
    if out="$("$bin" static-linkage --root "$ROOT" 2>&1)"; then
        echo "[98-drift-checks]   static linkage verified for Linux native roles (no PT_INTERP, no DT_NEEDED)"
        return 0
    else
        local rc=$?
        if [[ $rc -eq 2 ]]; then
            # Empty scan (unbuilt tree without Linux binaries)
            echo "[98-drift-checks]   advisory: static-linkage: $out" >&2
            return 0
        fi
        _violations_from "check_static_linkage: " "$out"
    fi
}

_run_bake_plan_check() {
    local mode="$1" out
    # Stage 85's candidates, in stage 85's order. CI builds debug only, so the
    # certified binary was one the bake can never run; debug is dropped because
    # that tree is where the two sides diverge (task_994).
    local bin="" c
    if [[ -n "${MIOS_NATIVE_BIN_DIR:-}" ]]; then
        bin="$(native_bin mios-bake-plan)" || {
            _violation "mios-bake-plan is missing from MIOS_NATIVE_BIN_DIR=$MIOS_NATIVE_BIN_DIR"
            return
        }
    else
        for c in /usr/libexec/mios/mios-bake-plan \
                 "$ROOT/tools/native/target/release/mios-bake-plan"; do
            [[ -n "$c" && -x "$c" ]] && { bin="$c"; break; }
        done
    fi
    if [[ -z "$bin" ]] && command -v cargo >/dev/null 2>&1; then
        cargo build --release --manifest-path "$ROOT/tools/native/Cargo.toml" -p mios-bake-plan >/dev/null 2>&1 || true
        [[ -x "$ROOT/tools/native/target/release/mios-bake-plan" ]] && bin="$ROOT/tools/native/target/release/mios-bake-plan"
    fi
    if [[ -z "$bin" ]]; then
        # No Python fallback: certifying a different program is the defect.
        _violation "mios-bake-plan is not built for release, so check_bake_plan could not certify what stage 85 runs -- build it: cd tools/native && cargo build --release -p mios-bake-plan"
        return
    fi
    # Pin MIOS_ROOT and every SSOT tier: unpinned, the binary takes its root from
    # its own path, so an installed one graded / and not this tree.
    # shellcheck disable=SC2046
    if out="$(cd "$ROOT" && env $(_render_env) \
            MIOS_PLAN_OUT="$ROOT/usr/lib/mios/bake/plan.d" "$bin" --check "$mode" 2>&1)"; then
        if [[ "$mode" == --check-integrity && "$out" != *"Bake-plan integrity verified against active Quadlets and SSOT"* ]]; then
            _violation "mios-bake-plan does not support the required native integrity check"
            return
        fi
        [[ -z "$out" ]] || printf '%s\n' "$out"
        echo "[98-drift-checks]   bake-plan lists in sync with mios.toml [build.bake] SSOT"
    else
        printf '%s\n' "$out" >&2
        _violation "bake-plan lists are STALE vs mios.toml -- regenerate with MIOS_ROOT=$ROOT $bin"
    fi
}

# --- bake-plan lists match mios.toml build.bake SSOT ---
check_bake_plan() {
    _run_bake_plan_check --check
}

# --- bake-plan groups match active Quadlets and required SSOT image assignments ---
check_bake_plan_integrity() {
    _run_bake_plan_check --check-integrity
}

check_bake_ref_defaults() {
    # `-d "$ROOT/.git"` is FALSE inside a git worktree, where .git is a FILE,
    # so this skipped wherever gates run from a worktree. rev-parse is true for
    # a work tree, a worktree and a bare repo, and false when git is absent.
    if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        if [[ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" == "1" ]]; then
            _violation "check_bake_ref_defaults cannot run: not a git repository, or git is absent"
            return
        fi
        echo "[98-drift-checks]   WARNING: not a git repo or git absent -- check_bake_ref_defaults NOT verified" >&2
        return 0
    fi
    local empty_refs
    empty_refs="$(git grep -E 'MIOS_BUILD_BAKE_REFS_[A-Z0-9_]+:-\}' automation/ 2>/dev/null || true)"
    if [[ -n "$empty_refs" ]]; then
        _violation "found empty defaults for bake-refs in automation scripts:"$'\n'"${empty_refs}"
        return 1
    fi
    if command -v python3 >/dev/null 2>&1; then
        if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py bake-refs-parity
        then
            echo "[98-drift-checks]   all baker scripts have non-empty defaults matching SSOT bake_refs"
        else
            _violation "baker script MIOS_BUILD_BAKE_REFS default value parity check failed"
            return 1
        fi
    else
        echo "[98-drift-checks]   all baker scripts have non-empty defaults for their bake-refs"
    fi
}

check_roadmap_index() {
    if [[ ! -f "$ROOT/ROADMAP.md" ]]; then
        _violation "ROADMAP.md not found -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local bin; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if "$bin" roadmap-index --root "$ROOT" --check; then
        echo "[98-drift-checks]   roadmap index in sync with frontmatter metadata"
    else
        _violation "roadmap index is STALE or cites invalid laws/ADRs/ssot_keys -- regenerate with mios-gen roadmap-index --root $ROOT"
    fi
}

check_cli_eval_safety() {
    _need_python || return 0
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py cli-eval-safety 2>&1)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   CLI verbs in usr/libexec/mios/ are eval-safe and os.system-free"
    echo "[98-drift-checks]   ${out}"
}

check_shellcheck() {
    local rc=0
    bash "$ROOT/automation/lint-shell.sh" || rc=$?
    if [[ $rc -eq 0 ]]; then
        echo "[98-drift-checks]   shellcheck: shell scripts conform to error-level linting"
    elif [[ $rc -eq 2 && "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" != "1" ]]; then
        echo "[98-drift-checks]   WARNING: shell-lint could not run (mios-gate or shellcheck absent)" >&2
    else
        _violation "shell-lint failed (exit $rc) -- run automation/lint-shell.sh for the findings"
    fi
}

check_sbom_metadata() {
    local sbom_dir="$ROOT/usr/share/mios/artifacts/sbom"
    local bad=()

    if [[ -d "$sbom_dir" ]]; then
        if [[ -f "$sbom_dir/models.tsv" ]]; then
            while IFS=$'\t' read -r name type repo file sha256 || [[ -n "$name" ]]; do
                [[ "$name" == "name" ]] && continue
                [[ -z "$name" ]] && continue
                if [[ -z "$type" || -z "$repo" || -z "$file" || -z "$sha256" ]]; then
                    bad+=("models.tsv has empty fields in row for '$name'")
                fi
                if [[ "$sha256" != "unknown" && ! "$sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
                    bad+=("models.tsv row for '$name' has invalid sha256: '$sha256'")
                fi
            done < "$sbom_dir/models.tsv"
        fi

        if [[ -f "$sbom_dir/binaries.tsv" ]]; then
            while IFS=$'\t' read -r name version sha256 || [[ -n "$name" ]]; do
                [[ "$name" == "name" ]] && continue
                [[ -z "$name" ]] && continue
                if [[ -z "$version" || -z "$sha256" ]]; then
                    bad+=("binaries.tsv has empty fields in row for '$name'")
                fi
                if [[ "$sha256" != "unknown" && ! "$sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
                    bad+=("binaries.tsv row for '$name' has invalid sha256: '$sha256'")
                fi
            done < "$sbom_dir/binaries.tsv"
        fi

        if [[ -f "$sbom_dir/bound-images.tsv" ]]; then
            while IFS=$'\t' read -r image digest group || [[ -n "$image" ]]; do
                [[ "$image" == "image" ]] && continue
                [[ -z "$image" ]] && continue
                if [[ -z "$digest" || -z "$group" ]]; then
                    bad+=("bound-images.tsv has empty fields in row for '$image'")
                fi
            done < "$sbom_dir/bound-images.tsv"
        fi
    fi

    if [[ "${#bad[@]}" -eq 0 ]]; then
        echo "[98-drift-checks]   SBOM metadata manifests are structurally valid"
    else
        for err in "${bad[@]}"; do
            echo "  [sbom-drift] $err" >&2
        done
        _violation "SBOM metadata manifests in usr/share/mios/artifacts/sbom/ contain invalid/empty fields"
    fi
}

check_curl_retry() {
    local bad=()
    local py_script="
import glob, re, os

root = '$ROOT'
files = glob.glob(os.path.join(root, '**/Containerfile*'), recursive=True) + \
        glob.glob(os.path.join(root, 'automation/**/*.sh'), recursive=True) + \
        glob.glob(os.path.join(root, 'usr/libexec/mios/**/*.sh'), recursive=True)

unretried = []
for path in files:
    if '.git' in path or 'node_modules' in path or 'test-drift-gates.sh' in path: continue
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f, 1):
                sline = line.strip()
                if sline.startswith('#'): continue
                if re.search(r'\b(curl|wget)\b', sline) and re.search(r'https?://', sline):
                    if 'localhost' in sline or '127.0.0.1' in sline: continue
                    if not re.search(r'--retry|--tries|scurl\b', sline):
                        rel = os.path.relpath(path, root)
                        unretried.append(f'{rel}:{i}')
    except Exception: pass

for u in unretried:
    print(u)
"
    # `2>/dev/null || true` made an empty $res mean EITHER no findings OR the
    # scanner died, and the no-findings branch is the one that ran.
    local res rc=0
    res="$(python3 -c "$py_script" 2>&1)" || rc=$?
    if (( rc != 0 )); then
        printf '%s\n' "$res" >&2
        _violation "the curl/wget retry scanner failed to run (exit $rc), so no build script was inspected"
        return
    fi
    if [[ -z "$res" ]]; then
        echo "[98-drift-checks]   curl/wget build network fetches carry"
    else
        while IFS= read -r line; do
            [[ -n "$line" ]] && bad+=("$line")
        done <<< "$res"
        for err in "${bad[@]}"; do
            echo "  [curl-retry-drift] unretried network fetch: $err" >&2
        done
        _violation "curl/wget build network fetch lacking --retry / --tries flag found"
    fi
}

# The SBOM's provenance is only as good as the ref list that feeds it. When
# mios-resolve-latest mirrored [image.sidecars] by hand the mirror drifted --
# four refs named images MiOS does not ship -- so the resolver must DERIVE its
# set from the SSOT and carry no registry ref literal of its own.
# --- mios-resolve-latest derives its image refs from [image.sidecars] ---
check_resolver_ssot_refs() {
    local rel="usr/libexec/mios/mios-resolve-latest"
    if [[ ! -f "$ROOT/$rel" ]]; then
        _violation "$rel is missing -- a tracked deliverable, so this check cannot run"
        return
    fi
    _require_python3 || return 0
    # `2>/dev/null || true` made an empty $res mean EITHER clean OR crashed, and
    # the clean branch was the one that ran. Keep the status and the stderr.
    local res rc=0
    res="$(MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_REL="$rel" python3 tools/drift-checks.py resolver-ssot-refs 2>&1)" || rc=$?
    if (( rc != 0 )); then
        printf '%s\n' "$res" >&2
        _violation "resolver-ssot-refs failed to run (exit $rc), so $rel was never inspected"
        return
    fi
    if [[ -z "$res" ]]; then
        echo "[98-drift-checks]   mios-resolve-latest derives its refs from the SSOT"
    else
        while IFS= read -r line; do
            [[ -n "$line" ]] && echo "  [resolver-ssot-drift] hardcoded image ref at $rel:$line" >&2
        done <<<"$res"
        _violation "mios-resolve-latest carries a hardcoded registry image ref; derive the set from mios.toml [image.sidecars] through mios_toml instead (a hand-mirrored list drifts and feeds wrong provenance into the SBOM)"
    fi
}

check_nested_podman_caps() {
    local bad=()
    local gha_file="$ROOT/.github/workflows/mios-ci.yml"
    local sys_script="$ROOT/usr/libexec/mios/57-mios-sys-build.sh"
    local doc_file="$ROOT/usr/share/doc/mios/reference/nested-podman-caps.md"

    if [[ ! -f "$doc_file" ]]; then
        bad+=("missing reference doc: usr/share/doc/mios/reference/nested-podman-caps.md")
    fi

    if [[ -f "$gha_file" ]]; then
        if ! grep -q -- "--device /dev/fuse" "$gha_file" || ! grep -q -- "--cap-add" "$gha_file" || ! grep -q "seccomp=unconfined" "$gha_file"; then
            bad+=(".github/workflows/mios-ci.yml is missing required nested podman flags (--device /dev/fuse, --cap-add, --security-opt seccomp=unconfined)")
        fi
    fi

    if [[ -f "$sys_script" ]]; then
        if ! grep -q -- "--cap-add" "$sys_script" || ! grep -q "seccomp=unconfined" "$sys_script"; then
            bad+=("usr/libexec/mios/57-mios-sys-build.sh is missing required nested podman flags (--cap-add, --security-opt seccomp=unconfined)")
        fi
        if ! grep -q "build_image_with_retry" "$sys_script" || ! grep -q "image exists" "$sys_script"; then
            bad+=("usr/libexec/mios/57-mios-sys-build.sh is missing build_image_with_retry loop or image exists verification")
        fi
    fi

    if [[ ${#bad[@]} -eq 0 ]]; then
        echo "[98-drift-checks]   nested-podman capability flags & reference doc verified"
    else
        for err in "${bad[@]}"; do
            echo "  [nested-podman-drift] $err" >&2
        done
        _violation "nested podman build missing capability/security flags or reference doc"
    fi
}

check_bake_budget() {
    _need_python || return 0
    local py_res
    py_res="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py bake-budget 2>&1)"
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "  [bake-budget-drift] $py_res" >&2
        _violation "bake-budget gate failed: $py_res"
    else
        # The gate-index generator captures this line verbatim as the check's
        # description, so interpolating a variable put the literal text
        # "$py_res" into the generated reference. The detail goes to stderr.
        echo "[98-drift-checks]   bake-budget gate: projected baked image size within the SSOT disk budget"
        [[ -n "$py_res" ]] && printf '%s
' "$py_res" >&2
    fi
}

check_greenboot() {
    echo "[98-drift-checks]   greenboot health-coverage check"
    local gb_dir="$ROOT/usr/lib/greenboot/check/required.d"
    if [[ ! -d "$gb_dir" ]]; then
        _violation "(54) greenboot required checks directory ($gb_dir) is missing"
        return
    fi
    # The critical set is READ FROM THE SSOT, never restated here: a hardcoded copy
    # agrees with the scripts it checks while both drift away from mios.toml.
    # Captured stdout only and branched on the text, not the status: a failure
    # on stderr left an empty blob and no violation recorded at all.
    local gb_out gb_rc=0
    gb_out="$(MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_GB_DIR="$gb_dir" python3 tools/drift-checks.py greenboot 2>&1)" || gb_rc=$?
    if [[ -n "$gb_out" || $gb_rc -ne 0 ]]; then
        _violations_from "" "$gb_out"
        return
    fi
}

# Regenerate-and-diff against the committed projection, the shape
# check_ipa_enroll_projection uses: the generator's exit status is an assertion,
# the match is anchored to line start, and the render is diffed against
# etc/mios/clevis-luks.env, so an unprojected SSOT edit is drift. Run through
# bash so a lost exec bit cannot decide whether the projection is checked.
check_clevis_luks() {
    echo "[98-drift-checks]   clevis LUKS SSOT projection check"
    local gen="$ROOT/usr/libexec/mios/mios-clevis-luks-gen" rc=0 out
    local tgt="$ROOT/etc/mios/clevis-luks.env"
    local repro="usr/libexec/mios/mios-clevis-luks-gen usr/share/mios/mios.toml > etc/mios/clevis-luks.env"
    if [[ ! -f "$gen" ]]; then
        _violation "(67) clevis LUKS generator missing: usr/libexec/mios/mios-clevis-luks-gen"
        return 0
    fi
    out="$(bash "$gen" "${MIOS_TOML_ROOT:-$ROOT}/usr/share/mios/mios.toml" 2>&1)" || rc=$?
    if [[ "$rc" -ne 0 ]] || ! grep -Eq '^CLEVIS_LUKS_ENABLED="' <<<"$out"; then
        printf '[98-drift-checks][diff] generator rc=%s: %s\n' "$rc" "$out" >&2
        _violation "(67) clevis LUKS generator did not project [security.luks] from usr/share/mios/mios.toml"
    elif [[ ! -f "$tgt" ]]; then
        _violation "(67) etc/mios/clevis-luks.env MISSING -- the projection is not committed; run ${repro}"
    elif ! diff -u --label "a/etc/mios/clevis-luks.env" --label "b/mios.toml[security.luks]" \
            "$tgt" <(printf '%s\n' "$out") >&2; then
        _violation "(67) etc/mios/clevis-luks.env is out of sync with [security.luks] SSOT -- run ${repro}"
    else
        echo "[98-drift-checks]   etc/mios/clevis-luks.env matches [security.luks] SSOT"
    fi
}

check_metal_vfio() {
    echo "[98-drift-checks]   MiOS-Metal vfio-pci SSOT projection check"
    _need_python || return 0
    local gen="$ROOT/usr/libexec/mios/mios-metal-vfio-gen"
    local toml="${MIOS_TOML_ROOT:-$ROOT}/usr/share/mios/mios.toml"
    if [[ ! -x "$gen" && -f "$gen" ]]; then
        chmod +x "$gen" 2>/dev/null || true
    fi
    if [[ ! -f "$gen" ]]; then
        _violation "(68) MiOS-Metal vfio generator script missing"
        return
    fi

    # Asserted only that MIOS_METAL_ENABLED= appeared in the output -- a
    # literal the heredoc emits unconditionally -- with `|| true` discarding
    # the status. Compare each emitted value against the SSOT instead.
    local out rc=0
    out="$("$gen" "$toml" 2>&1)" || rc=$?
    if (( rc != 0 )); then
        _violation "(68) MiOS-Metal vfio generator exited ${rc}: ${out}"
        return
    fi

    local mismatches
    mismatches="$(printf '%s
' "$out" | python3 -c '
import sys, tomllib

emitted = {}
for line in sys.stdin.read().splitlines():
    if "=" not in line:
        continue
    k, _, v = line.partition("=")
    emitted[k.strip()] = v.strip().strip(chr(34))

with open(sys.argv[1], "rb") as fh:
    metal = tomllib.load(fh).get("metal", {})

# Each projected key, and the SSOT value it must equal. The generator falls back
# to a hardcoded literal when the key is missing or tomllib fails -- notably
# "Vfio-pci" with a capital V, which no SSOT value would ever produce -- so
# comparing against the SSOT is what makes those fallbacks visible.
expected = {
    "MIOS_METAL_ENABLED": str(metal.get("enabled", False)).lower(),
    "MIOS_METAL_GUEST_CPU_PERCENT": str(metal.get("guest_cpu_percent", 80)),
    "MIOS_METAL_GUEST_RAM_PERCENT": str(metal.get("guest_ram_percent", 80)),
    "MIOS_METAL_DGPU_MODE": str(metal.get("dgpumode", "vfio-pci")),
}

for key, want in expected.items():
    if key not in emitted:
        print("%s: not emitted at all (expected %r)" % (key, want))
    elif emitted[key] != want:
        print("%s: projected %r but [metal] says %r" % (key, emitted[key], want))
' "$toml" 2>&1)"

    if [[ -n "$mismatches" ]]; then
        _violations_from "(68) MiOS-Metal vfio projection disagrees with [metal] SSOT: " "$mismatches"
        return
    fi
    echo "[98-drift-checks]   mios-metal-vfio-gen projects [metal] SSOT faithfully"
}

check_router_parity() {
    local test_script="$ROOT/usr/lib/mios/agent-pipe/test_mios_router_parity.py"
    local corpus_file="$ROOT/usr/lib/mios/agent-pipe/tests/router_corpus.json"
    if [[ ! -f "$test_script" || ! -f "$corpus_file" ]]; then
        _violation "Router parity test or corpus missing ($test_script or $corpus_file)"
        return 1
    fi
    if ! python3 "$test_script" >/dev/null 2>&1; then
        _violation "test_mios_router_parity.py failed on router_corpus.json"
        return 1
    fi

    # `local x=$(cmd)` returns the status of `local`, not of cmd, so the
    # scan below was unconditionally treated as passing. Declared separately.
    local py_res
    py_res=$(python3 tools/drift-checks.py router-intent-coverage "$corpus_file" "$ROOT")
    local py_rc=$?

    if [[ $py_rc -ne 0 ]]; then
        printf '%s\n' "$py_res" >&2
        _violation "server.py or agent-pipe routing code contains an intent == branch not represented in router_corpus.json"
        return 1
    fi

    echo "[98-drift-checks]   Router Stage-2 parity gate satisfied"
}

check_council_gate_ssot() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py council-gate-ssot
    then
        echo "[98-drift-checks]   council-gate SSOT parameters defined in mios.toml and consumed by code"
    else
        _violation "[agent_pipe.council] keys missing or have no code consumer"
    fi
}

check_containerfile_pinned_clones() {
    _need_python || return 0
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py containerfile-pinned-clones 2>&1)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   all git clone invocations in Containerfiles carry explicit"
    echo "[98-drift-checks]   ${out}"
}

check_firstboot_tier() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py firstboot-tier
    then
        echo "[98-drift-checks]   firstboot tier invariant verified"
    else
        _violation "firstboot tier invariant check failed"
    fi
}

check_bound_image_store() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py bound-image-store
    then
        echo "[98-drift-checks]   bound image store scoped to bound Quadlets"
    else
        _violation "bound image store contract failed"
    fi
}

check_rechunk_budget() {
    local script="$ROOT/automation/build/rechunk.sh"
    if [[ ! -f "$script" ]]; then
        _violation "rechunk.sh missing ($script)"
        return 1
    fi
    local bad=()
    if ! grep -q "rechunk_max_layers" "$script"; then
        bad+=("rechunk.sh does not reference SSOT key rechunk_max_layers")
    fi
    if grep -q "mios-bootc:" "$script"; then
        bad+=("rechunk.sh contains legacy mios-bootc: image literal")
    fi
    if grep -E -- "--max-layers=[0-9]+" "$script" >/dev/null 2>&1; then
        bad+=("rechunk.sh contains hardcoded integer --max-layers literal")
    fi
    if ((${#bad[@]} > 0)); then
        _violation "check_rechunk_budget: ${bad[*]}"
        return 1
    fi
    echo "[98-drift-checks]   rechunk budget & SSOT image reference verified"
}

# --- gate definitions are registered exactly once inside main() ---
check_gate_registry() {
    local bin; bin="$(_gate_bin)" || {
        _violation "check_gate_registry requires native mios-gate"; return; }
    local out; out="$("$bin" gate-registry --root "$ROOT" 2>&1)" || {
        _violations_from "check_gate_registry: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

check_python_lint() {
    echo "[98-drift-checks]   Python static compilation gate"
    local lint_script="$ROOT/automation/lint-python.sh"
    if [[ ! -f "$lint_script" ]]; then
        _violation "automation/lint-python.sh is missing"
        return
    fi
    local res=0
    bash "$lint_script" >/dev/null 2>&1 || res=$?
    if [[ "$res" -eq 1 ]]; then
        _violation "python linting reported compilation/syntax error(s)"
    elif [[ "$res" -eq 2 ]]; then
        if [[ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" == "1" ]]; then
            _violation "python linting could not run (python3 missing under MIOS_DRIFT_REQUIRE_TOOLS=1)"
        else
            echo "[98-drift-checks]   WARNING: python linting could not run (python3 missing)" >&2
        fi
    elif [[ "$res" -ne 0 ]]; then
        _violation "python linting failed with exit code $res"
    fi
}

check_test_hermeticity() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py test-hermeticity
    then
        echo "[98-drift-checks]   test hermeticity verified"
    else
        _violation "unguarded live-resource call in test suite"
    fi
}

check_negative_test_coverage() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py negative-test-coverage
    then
        echo "[98-drift-checks]   negative test coverage ratchet verified"
    else
        _violation "negative test coverage drift detected (/)"
    fi
}

check_soft_mode_not_committed() {
    local hits="" f
    for f in "$ROOT"/.github/workflows/*.yml "$ROOT"/.forgejo/workflows/*.yml "$ROOT"/automation/build.sh "$ROOT"/Justfile; do
        _subject_present "$f" || continue
        if grep -qE "MIOS_DRIFT_CHECK_SOFT=1|MIOS_SSOT_LINT_SOFT=1" "$f"; then
            hits+="    ${f#"$ROOT"/}: contains committed soft-mode override (MIOS_DRIFT_CHECK_SOFT=1 or MIOS_SSOT_LINT_SOFT=1)"$'\n'
        fi
    done
    if [[ -n "$hits" ]]; then
        printf '%s' "$hits" >&2
        _violation "soft-mode override is committed in CI/build scripts"
    else
        echo "[98-drift-checks]   no soft-mode override committed in CI/build pipeline"
    fi
}

# --- 97-ssot-lint (native mios-ssot-lint) passes on the tree and names a planted orphan placeholder ---
check_ssot_lint_equivalence() {
    # The bash twin was retired for the native port it was proven byte-identical
    # to; what is left to prove is that the linter still bites. The plant goes
    # into a private copy of its three inputs, never into the tree under test.
    local out tmp probe rc=0
    out="$(bash "$ROOT/automation/97-ssot-lint.sh" 2>&1)" || { _violations_from "97-ssot-lint: " "$out"; return; }
    tmp="$(mktemp -d)"; probe="$(printf 'MIOS_%s_%s' ORPHAN_PROBE "$$")"
    mkdir -p "$tmp/tools/lib" "$tmp/usr/share/mios/reference" "$tmp/usr/share/containers"
    cp "$ROOT/tools/lib/userenv.sh" "$tmp/tools/lib/"; cp "$ROOT/usr/share/mios/reference/env-baseline.txt" "$tmp/usr/share/mios/reference/"
    cp -r "$ROOT/usr/share/containers/systemd" "$tmp/usr/share/containers/"
    printf '[Container]\nEnvironment=PROBE=${%s}\n' "$probe" > "$tmp/usr/share/containers/systemd/zz-probe.container"
    out="$(MIOS_SSOT_LINT_ROOT="$tmp" bash "$ROOT/automation/97-ssot-lint.sh" 2>&1)" || rc=$?
    rm -rf "$tmp"
    if (( rc == 0 )) || ! grep -q "$probe" <<<"$out"; then
        _violation "97-ssot-lint passed a Quadlet placeholder no SSOT tier emits -- the linter no longer detects orphans"
        return
    fi
    echo "[98-drift-checks]   97-ssot-lint passes the tree and names a planted orphan placeholder"
}

check_gate_index() {
    local gen
    gen="$(native_bin mios-gen "${MIOS_GEN_BIN:-}")" || {
        _violation "check_gate_index: native mios-gen is required; build/install the SSOT release catalog"
        return
    }
    if "$gen" gate-index --root "$ROOT" --check; then
        echo "[98-drift-checks]   gate index in sync with main registration"
        return 0
    fi
    _emit_projection_evidence "gate-index" "usr/share/mios/reference/drift-gate-index.tsv"
    _violation "usr/share/mios/reference/drift-gate-index.tsv is out of sync with main() -- run mios-gen gate-index"
}

check_oci_archive_path() {
    local consumer="$ROOT/tools/install.sh"
    local producer="$ROOT/usr/libexec/mios/mios-stage-oci-archive"

    if [[ ! -f "$consumer" || ! -f "$producer" ]]; then
        _violation "oci-archive producer/consumer absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    local c_path p_path
    c_path="$(grep -oE '/mnt/mios-repo/[a-zA-Z0-9_.-]+\.tar' "$consumer" | head -1 || true)"
    p_path="$(grep -oE '/mnt/mios-repo/[a-zA-Z0-9_.-]+\.tar' "$producer" | head -1 || true)"

    if [[ -z "$c_path" || -z "$p_path" || "$c_path" != "$p_path" ]]; then
        _violation "oci-archive default path mismatch between producer ($p_path) and consumer ($c_path)"
    else
        echo "[98-drift-checks]   oci-archive producer and consumer paths match"
    fi
}

check_replaceme_mount_substitution() {
    local justfile="$ROOT/Justfile"
    if [[ ! -f "$justfile" ]]; then
        _violation "Justfile absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    _need_python || return 0

    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py replaceme-mount-substitution
    then
        echo "[98-drift-checks]   no Justfile BIB recipe mounts a format recipe or a placeholder raw; miosd artifact-build renders credentials"
    else
        _violation "a Justfile BIB recipe raw-mounts a format recipe or a REPLACE placeholder -- build disks with miosd artifact-build"
    fi
}

check_kickstart_shell_syntax() {
    local cfg="$ROOT/usr/share/mios/ventoy/mios-kickstart.cfg"
    local iso_toml="$ROOT/config/artifacts/iso.toml"

    local bad_ks="" f

    for f in "$cfg" "$iso_toml"; do
        _subject_present "$f" || continue
        local post_sh
        post_sh="$(sed -n '/%post/,/%end/p' "$f" | sed 's/.*%post.*//; s/.*%end.*//')"
        if [[ -n "$post_sh" ]]; then
            if ! printf '%s\n' "$post_sh" | bash -n 2>/dev/null; then
                bad_ks+="    ${f#"$ROOT"/}: embedded %post shell failed bash -n syntax check"$'\n'
            fi
        fi
    done

    if [[ -n "$bad_ks" ]]; then
        printf '%s' "$bad_ks" >&2
        _violation "embedded kickstart shell contains bash syntax errors"
    else
        echo "[98-drift-checks]   embedded kickstart %post shell syntax verified clean with bash -n"
    fi
}

check_bib_rootfs_label_policy() {
    local justfile="$ROOT/Justfile"
    if [[ ! -f "$justfile" ]]; then
        _violation "Justfile absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    _need_python || return 0

    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py bib-rootfs-label-policy
    then
        echo "[98-drift-checks]   BIB recipes enforce valid"
    else
        _violation "BIB recipe violates rootfs filesystem label policy"
    fi
}

check_offline_install_invariant() {
    local install_script="$ROOT/tools/install.sh"
    local oci_ks="$ROOT/usr/share/mios/ventoy/mios-oci-install.ks"

    if [[ ! -f "$install_script" ]]; then
        _violation "tools/install.sh is absent -- offline-install invariant cannot be verified"
        return 0
    fi

    if ! bash -n "$install_script" 2>/dev/null; then
        _violation "tools/install.sh failed bash -n syntax check"
        return 0
    fi

    # Strip comments so AI-hints and docstrings do not satisfy the executable invariant
    local code
    code="$(sed 's/#.*//' "$install_script")"

    # bootc has no --transport flag; the archive is named as an oci-archive: source.
    if ! grep -q -E 'oci-archive:' <<<"$code"; then
        _violation "tools/install.sh executable code does not invoke bootc install with oci-archive transport/source"
        return 0
    fi

    # Assert executable code in tools/install.sh and mios-oci-install.ks contains no forbidden network calls
    local net_regex='(http://|https://|git clone|podman pull|skopeo copy docker://)'
    local net_token
    net_token="$(sed 's/#.*//' "$install_script" | grep -nE "$net_regex" || true)"
    if [[ -n "$net_token" ]]; then
        echo "$net_token" >&2
        _violation "tools/install.sh executable code contains forbidden network pull token violating zero-network offline-install contract"
        return 0
    fi

    if [[ -f "$oci_ks" ]]; then
        local ks_net_token
        ks_net_token="$(sed 's/#.*//' "$oci_ks" | grep -nE "$net_regex" || true)"
        if [[ -n "$ks_net_token" ]]; then
            echo "$ks_net_token" >&2
            _violation "usr/share/mios/ventoy/mios-oci-install.ks executable code contains forbidden network pull token"
            return 0
        fi
    fi

    echo "[98-drift-checks]   tools/install.sh zero-network offline-install invariant verified clean against executable code"
}

# --- installer role markers are unique across every script that declares one ---
check_installer_family_roles() {
    echo "[98-drift-checks] installer role markers are unique across every script that declares one"
    # Subjects are the declared family UNION every tracked file already carrying
    # the marker, so a newly added installer is covered the day it lands.
    local family=("tools/install.sh" "automation/install.sh" "automation/install-fhs.sh")
    local bad_installers=""
    local roles=()
    local subjects=()

    local rel
    for rel in "${family[@]}"; do
        if [[ ! -f "$ROOT/$rel" ]]; then
            # Absent though TRACKED is a deleted deliverable, not a fixture root.
            if git -C "$ROOT" ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
                bad_installers+="    ${rel}: tracked installer is absent from the worktree"$'\n'
            fi
            continue
        fi
        subjects+=("$rel")
    done

    local discovered
    discovered="$(git -C "$ROOT" grep -lE '^# MIOS_INSTALLER_ROLE=' -- '*.sh' 2>/dev/null || true)"
    while IFS= read -r rel; do
        [[ -n "$rel" ]] || continue
        [[ -f "$ROOT/$rel" ]] || continue
        [[ " ${subjects[*]:-} " == *" ${rel} "* ]] && continue
        subjects+=("$rel")
    done <<<"$discovered"

    if (( ${#subjects[@]} == 0 )); then
        _violation "no installer script was readable, so an empty result is not a pass"
        return 1
    fi

    for rel in "${subjects[@]}"; do
        local role
        role="$(grep -oE '^# MIOS_INSTALLER_ROLE=[a-zA-Z0-9_-]+' "$ROOT/$rel" | cut -d= -f2 || true)"
        if [[ -z "$role" ]]; then
            bad_installers+="    ${rel}: missing # MIOS_INSTALLER_ROLE header marker"$'\n'
        else
            if [[ " ${roles[*]:-} " == *" ${role} "* ]]; then
                bad_installers+="    ${rel}: duplicate # MIOS_INSTALLER_ROLE='$role'"$'\n'
            else
                roles+=("$role")
            fi
        fi
    done

    if [[ -n "$bad_installers" ]]; then
        printf '%s' "$bad_installers" >&2
        _violation "installer script role marker violation or collision"
    else
        echo "[98-drift-checks]   ${#subjects[@]} installer role marker(s) unique across the declared family and every tracked file carrying one"
    fi
}

check_bib_configs_projection() {
    local bin; bin="$(native_bin mios-gen "${MIOS_GEN_BIN:-}")" || true
    if [[ -n "$bin" ]]; then
        local out rc=0
        out="$( "$bin" bib-configs --root "$ROOT" --check 2>&1 )" || rc=$?
        if (( rc == 0 )); then
            echo "[98-drift-checks]   BIB artifact configs in sync with mios.toml [deploy.artifacts] SSOT"
            return 0
        else
            echo "$out" >&2
            _emit_projection_evidence "bib-configs" "config/artifacts/bib.toml" "config/artifacts/iso.toml"
            _violation "BIB artifact configs (bib.toml, iso.toml) out of sync with mios.toml [deploy.artifacts] -- run mios-gen bib-configs"
            return
        fi
    fi
    _violation "check_bib_configs_projection: native mios-gen is required; build/install the SSOT release catalog"
}

check_repo_partition_label_ssot() {
    # The label is READ from the SSOT, never defaulted.
    _need_python || return 0
    local ssot="$ROOT/usr/share/mios/mios.toml" ssot_label f
    local install_sh="$ROOT/tools/install.sh"
    local cfg="$ROOT/usr/share/mios/ventoy/mios-kickstart.cfg"
    local oci_ks="$ROOT/usr/share/mios/ventoy/mios-oci-install.ks"
    local loopback="$ROOT/field/loopback.cfg"
    # A consumer that has gone missing is a violation: the label it tracked is
    # now enforced nowhere, which is exactly when this gate should speak.
    for f in "$ssot" "$install_sh" "$cfg" "$oci_ks" "$loopback"; do
        [[ -f "$f" ]] || { _violation "repo partition label consumer is absent: ${f#"$ROOT"/}"; return; }
    done
    ssot_label="$(python3 "$ROOT/tools/read-ssot-key.py" field.repo_partition.label)" \
        || { _violation "[field.repo_partition].label is absent from the SSOT -- the label its consumers track is undefined"; return; }
    local mismatch=""
    grep -q "blkid -L \"$ssot_label\"" "$install_sh" \
        || mismatch+="    tools/install.sh does not reference [field.repo_partition].label '$ssot_label'"$'\n'
    grep -q "blkid -L \"$ssot_label\"" "$cfg" \
        || mismatch+="    usr/share/mios/ventoy/mios-kickstart.cfg does not reference [field.repo_partition].label '$ssot_label'"$'\n'
    grep -q "$ssot_label" "$oci_ks" \
        || mismatch+="    usr/share/mios/ventoy/mios-oci-install.ks does not reference [field.repo_partition].label '$ssot_label'"$'\n'
    grep -q -E "($ssot_label|@@REPO_LABEL@@)" "$loopback" \
        || mismatch+="    field/loopback.cfg does not reference [field.repo_partition].label '$ssot_label' or @@REPO_LABEL@@"$'\n'
    if [[ -n "$mismatch" ]]; then
        printf '%s' "$mismatch" >&2
        _violation "repo partition label mismatch against [field.repo_partition].label SSOT"
    else
        echo "[98-drift-checks]   repo partition label consumers match [field.repo_partition].label SSOT"
    fi
}

check_artifact_recipes() {
    echo "[98-drift-checks] no artifact recipe carries a placeholder or a committed credential; disks get the operator's at build time (Law 11)"
    local bin; bin="$(_gate_bin)" || { _violation "mios-gate is not built, so check_artifact_recipes could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"; return; }
    "$bin" artifact-recipes --root "$ROOT" || \
        _violation "an artifact recipe carries a placeholder or a committed credential -- disks get theirs from miosd artifact-build (Law 11)"
}

check_bib_single_config_invariant() {
    local justfile="$ROOT/Justfile"
    if [[ ! -f "$justfile" ]]; then
        _violation "Justfile absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    _need_python || return 0

    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py bib-config-mount
    then
        echo "[98-drift-checks]   BIB recipes enforce single /config.toml mount and valid TOML syntax"
    else
        _violation "BIB recipe config mount or TOML syntax violation"
    fi
}

check_build_artifacts_output_dir() {
    local ssot="$ROOT/usr/share/mios/mios.toml"
    local justfile="$ROOT/Justfile"

    if [[ ! -f "$ssot" || ! -f "$justfile" ]]; then
        _violation "build output dir consumers absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    local output_dir
    output_dir="$(grep -A 3 '\[build\.artifacts\]' "$ssot" | grep 'output_dir' | head -1 | cut -d'"' -f2 || echo "Build")"

    local bad_out=""
    if grep -qE "(mkdir -p output|output/|-v \./output|>\s*output/)" "$justfile"; then
        bad_out+="    Justfile contains non-SSOT output path references (must use '$output_dir/')"
    fi

    if [[ -n "$bad_out" ]]; then
        printf '%s\n' "$bad_out" >&2
        _violation "Justfile recipe outputs violate [build.artifacts].output_dir SSOT"
    else
        echo "[98-drift-checks]   Justfile artifact recipes enforce SSOT output directory"
    fi
}

check_win11_vm_template_xml() {
    local xml_file="$ROOT/tools/win11-secureboot-template.xml"
    local ssot="$ROOT/usr/share/mios/mios.toml"

    if [[ ! -f "$xml_file" || ! -f "$ssot" ]]; then
        _violation "win11 VM template or SSOT absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    _need_python || return 0

    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py win11-vm-template-xml
    then
        echo "[98-drift-checks]   Win11 VM template is well-formed XML and projects SSOT [vm.win11]"
    else
        _violation "Win11 VM template XML well-formedness or SSOT projection violation"
    fi
}

check_ipa_enroll_projection() {
    _run_deployment_projection ipa-enroll
}

check_bootc_install_projection() {
    _run_deployment_projection bootc-install
}

check_uki_cmdline_projection() {
    _run_deployment_projection uki-cmdline
}

check_composefs_projection() {
    local conf="$ROOT/usr/lib/ostree/prepare-root.conf"
    local script="$ROOT/automation/77-composefs-verity.sh"

    if [[ ! -f "$conf" || ! -f "$script" ]]; then
        _violation "composefs prepare-root.conf absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    local tmp_dir tmp_conf
    tmp_dir="$(mktemp -d)"
    tmp_conf="${tmp_dir}/prepare-root.conf"

    MIOS_TOML_ROOT="$ROOT" COMPOSEFS_CONF="$tmp_conf" bash "$script" >/dev/null 2>&1 || true

    if [[ ! -f "$tmp_conf" ]]; then
        rm -rf "$tmp_dir"
        _violation "automation/77-composefs-verity.sh failed to render prepare-root.conf"
        return
    fi

    if ! diff -u "$conf" "$tmp_conf" >/dev/null 2>&1; then
        rm -rf "$tmp_dir"
        _violation "usr/lib/ostree/prepare-root.conf is out of sync with [security].composefs_mode SSOT"
    else
        rm -rf "$tmp_dir"
        echo "[98-drift-checks]   usr/lib/ostree/prepare-root.conf matches [security].composefs_mode SSOT"
    fi
}

check_cockpit_projection() {
    _run_deployment_projection cockpit
}

check_chrony_ptp_dropin() {
    local dropin_script="$ROOT/usr/libexec/mios/mios-chrony-ptp-dropin"
    local service_unit="$ROOT/usr/lib/systemd/system/mios-chrony-ptp.service"
    local canon_conf="$ROOT/etc/chrony.conf"

    if [[ ! -f "$dropin_script" || ! -f "$service_unit" ]]; then
        _violation "Chrony PTP drop-in generator script or service unit missing"
        return
    fi

    if ! bash -n "$dropin_script" >/dev/null 2>&1; then
        _violation "usr/libexec/mios/mios-chrony-ptp-dropin syntax error"
        return
    fi

    local tmp_dir tmp_ptp tmp_chrony_d
    tmp_dir="$(mktemp -d)"
    tmp_ptp="${tmp_dir}/dev_ptp0"
    tmp_chrony_d="${tmp_dir}/chrony.d"
    touch "$tmp_ptp"

    local old_conf_hash="" new_conf_hash=""
    if [[ -f "$canon_conf" ]]; then
        old_conf_hash="$(sha256sum "$canon_conf" | awk '{print $1}')"
    fi

    PTP_DEV="$tmp_ptp" CHRONY_D="$tmp_chrony_d" bash "$dropin_script" >/dev/null 2>&1 || true

    if [[ ! -f "${tmp_chrony_d}/10-ptp.conf" ]]; then
        rm -rf "$tmp_dir"
        _violation "mios-chrony-ptp-dropin failed to generate 10-ptp.conf when /dev/ptp0 exists"
        return
    fi

    PTP_DEV="$tmp_ptp" CHRONY_D="$tmp_chrony_d" bash "$dropin_script" >/dev/null 2>&1 || true

    if [[ -f "$canon_conf" ]]; then
        new_conf_hash="$(sha256sum "$canon_conf" | awk '{print $1}')"
        if [[ "$old_conf_hash" != "$new_conf_hash" ]]; then
            rm -rf "$tmp_dir"
            _violation "mios-chrony-ptp-dropin mutated canonical etc/chrony.conf"
            return
        fi
    fi

    rm -rf "$tmp_dir"
    echo "[98-drift-checks]   Chrony PTP drop-in generator is idempotent and leaves canonical chrony.conf unchanged"
}

check_renderer_gate_coverage() {
    local auto_dir="$ROOT/automation"
    local drift_file="$ROOT/automation/98-drift-checks.sh"

    # Both are tracked deliverables: their absence is the worst state to be in,
    # not a reason to report every renderer mapped.
    if [[ ! -d "$auto_dir" || ! -f "$drift_file" ]]; then
        _violation "check_renderer_gate_coverage: automation/ or automation/98-drift-checks.sh is absent -- no renderer could be mapped to a projection check"
        return
    fi

    local allowlist=("34-render-quadlets.sh" "35-render-ports.sh")

    local found_total=0
    local render_scripts=()
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        found_total=$((found_total + 1))
        local base
        base="$(basename "$f")"
        local allowed=0
        for item in "${allowlist[@]}"; do
            if [[ "$base" == "$item" ]]; then
                allowed=1
                break
            fi
        done
        if [[ "$allowed" -eq 0 ]]; then
            render_scripts+=("$base")
        fi
    done < <(find "$auto_dir" -maxdepth 1 -name "*-render*.sh" -o -name "*render*.sh" 2>/dev/null)

    # Zero renderers means the find matched nothing, and an empty loop below
    # would print the PASS line having examined no script at all.
    if [[ "$found_total" -eq 0 ]]; then
        _violation "check_renderer_gate_coverage: no renderer script found under automation/ -- the corpus is empty, so 'clean' would mean nothing was examined"
        return
    fi

    local unmapped=()
    local script
    for script in "${render_scripts[@]}"; do
        local stem
        stem="$(echo "$script" | sed -E 's/^[0-9]+-//; s/-render.*//; s/\.sh$//')"
        if ! grep -qE "check_.*${stem}.*projection|check_.*${stem}" "$drift_file"; then
            unmapped+=("$script")
        fi
    done

    if [[ ${#unmapped[@]} -gt 0 ]]; then
        _violation "The following renderer scripts have no corresponding projection check in 98-drift-checks.sh: ${unmapped[*]}"
    else
        echo "[98-drift-checks]   renderer gate coverage verified clean"
    fi
}

check_smoke_manifest() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py smoke-manifest
    then
        echo "[98-drift-checks]   smoke manifest components in mios.toml exist in source tree; every overlay names a real section, phase or profile"
    else
        _violation "smoke manifest is not closed over the SSOT (missing component, unknown kind, or an overlay naming nothing) -- see above"
    fi
}

check_negative_coverage() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_negative_coverage could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" negative-coverage --root "$ROOT"; then
        echo "[98-drift-checks]   negative test coverage gate: all dispatched checks are covered or exempt"
    else
        _violation "drift checks lacking negative test coverage"
    fi
}

check_verb_templates() {
    _need_python || return 0
    if python3 "${ROOT}/tools/verb-template-check.py"; then
        echo "[98-drift-checks]   verb templates compile and validate against SSOT args"
    else
        _violation "verb templates compilation or placeholder validation failed"
    fi
}

# --- pipe-boundaries.manifest.json matches the agent-pipe tree ---
check_pipe_boundaries() {
    local manifest="${ROOT}/usr/share/mios/pipe-boundaries.manifest.json"
    if [ ! -f "$manifest" ]; then
        _violation "pipe-boundaries.manifest.json is missing"
        return 0
    fi
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if ! out="$("$bin" pipe-boundaries --root "$ROOT" --check 2>&1)"; then
        printf '%s\n' "$out" >&2
        _violation "pipe-boundaries.manifest.json is stale -- run mios-gen pipe-boundaries"
        return
    fi
    echo "[98-drift-checks]   pipe-boundaries.manifest.json matches the agent-pipe tree"
}

# --- specs/ documentation matches standardized format ---
check_standardize_docs() {
    local bin out rc=0
    bin="$(native_bin mios-gen)" || true
    if [[ -n "$bin" && -x "$bin" ]]; then
        out="$("$bin" standardize-docs --root "$ROOT" --check 2>&1)" || rc=$?
    else
        _violation "mios-gen is not available"
        return
    fi

    if (( rc != 0 )); then
        printf '%s\n' "$out" >&2
        _violation "specs/ markdown documentation is unstandardized -- run mios-gen standardize-docs"
        return
    fi
    echo "[98-drift-checks]   specs/ markdown documentation matches standardized format"
}

# --- wiki and spec documentation embeds match SSOT ---
check_sync_wiki() {
    local bin out rc=0
    bin="$(native_bin mios-gen)" || true
    if [[ -n "$bin" && -x "$bin" ]]; then
        out="$("$bin" sync-wiki --root "$ROOT" --check 2>&1)" || rc=$?
    else
        _violation "mios-gen is not available"
        return
    fi

    if (( rc != 0 )); then
        printf '%s\n' "$out" >&2
        _violation "Wiki documentation embeds are STALE -- run mios-gen sync-wiki"
        return
    fi
    echo "[98-drift-checks]   wiki documentation embeds match SSOT"
}

check_vllm_name_canonical() {
    if grep -rn --exclude="98-drift-checks.sh" "MIOS_AI_VLL[M]_\|MIOS_AI_SGLAN[G]_" "${ROOT}/automation/" "${ROOT}/usr/lib/mios/" >/dev/null 2>&1; then
        _violation "found legacy M""IOS_AI_VLLM_ or M""IOS_AI_SGLANG_ long names in active code or automation"
    else
        echo "[98-drift-checks]   vLLM / SGLang canonical names reconciled to short consumer form"
    fi
}

check_pipe_extraction_parity() {
    _need_python || return 0

    if MIOS_DRIFT_ROOT="$ROOT" python3 "$ROOT/tools/pipe-parity-check.py" >/dev/null 2>&1; then
        echo "[98-drift-checks]   extraction surface parity + one-way imports clean"
    else
        _violation "mios_pipe extraction surface parity or one-way import rule violated"
    fi
}

# --- every .desktop launcher matches what render-desktop.py projects from SSOT ---
check_guacamole_consistency() {
    # Named for Guacamole and for "unit definitions"; render-desktop has no
    # Guacamole-specific logic and checks all .desktop launchers, of which
    # mios-svc-guacamole.desktop is one.
    echo "[98-drift-checks] every .desktop launcher matches what render-desktop projects from SSOT"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if ! out=$(cd "$ROOT" && "$bin" render-desktop --root "$ROOT" --check 2>&1); then
        _violations_from "check_guacamole_consistency: " "$out"
        return
    fi
    echo "[98-drift-checks]   every .desktop launcher in sync"
}

check_law_enforcers() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_law_enforcers could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" law-enforcers --root "$ROOT"; then
        echo "[98-drift-checks]   all [laws].enforced_by targets resolve to live enforcement"
    else
        _violation "a [laws].enforced_by target does not resolve to live enforcement -- a comment or dead code is not an enforcer"
    fi
}

check_usr_over_etc() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py usr-over-etc
    then
        echo "[98-drift-checks]   Law 1 USR-OVER-ETC verified clean"
    else
        _violation "Law 1 USR-OVER-ETC violated: tracked /etc file duplicates a /usr SSOT surface"
    fi
}

check_projection_registry() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py projection-registry
    then
        echo "[98-drift-checks]   Law 8 SSOT-PROJECTION registry verified clean"
    else
        _violation "Law 8 SSOT-PROJECTION registry check failed"
    fi
}

check_db_seed_coverage() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py db-seed-coverage
    then
        echo "[98-drift-checks]   DB seed coverage gate verified clean"
    else
        _violation "DB seed coverage gate check failed: unseeded SSOT section found"
    fi
}

check_verb_stub_backends() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py verb-stub-backends
    then
        echo "[98-drift-checks]   verb stub backends gate verified clean"
    else
        _violation "verb stub backends check failed: stub backend script found"
    fi
}

check_account_column_parity() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py account-column-parity
    then
        echo "[98-drift-checks]   account column parity gate verified clean"
    else
        _violation "account column parity check failed"
    fi
}

check_v2v_import_ssot() {
    local wrapper="$ROOT/usr/libexec/mios/mios-v2v-import"
    if [[ ! -f "$wrapper" ]]; then
        _violation "usr/libexec/mios/mios-v2v-import missing"
        return 1
    fi
    if ! bash -n "$wrapper" >/dev/null 2>&1; then
        _violation "usr/libexec/mios/mios-v2v-import failed bash -n syntax check"
        return 1
    fi
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py v2v-import-ssot
    then
        echo "[98-drift-checks]   virt-v2v import wrapper & SSOT parity verified"
    else
        _violation "virt-v2v import wrapper SSOT parity check failed"
    fi
}

# --- shell and script module line counts remain within maintainability limits ---
check_module_length() {
    echo "[98-drift-checks] shell and script module line counts remain within maintainability limits"
    # Walks the package RECURSIVELY against the [refactor] shrink-only register.
    # The former body used find -maxdepth 1 and saw 9 of 112 modules.
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-testhygiene.py module-length 2>&1)" || { _violations_from "check_module_length: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

check_vendored_assets_non_stub() {
    local vdir="$ROOT/usr/share/mios/vendored"
    if [[ ! -d "$vdir" ]]; then
        _violation "vendored assets dir absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    local stubs="" f sz
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        sz=$(wc -c < "$f" 2>/dev/null || stat -c %s "$f" 2>/dev/null || echo 0)
        if [[ "$sz" -lt 100 ]]; then
            stubs+="    $f (${sz} bytes - stub file)"$'\n'
        fi
    done < <(find "$vdir" -type f ! -name '.keep' ! -name 'VERSIONS.txt' 2>/dev/null)

    if [[ -n "$stubs" ]]; then
        printf '%s' "$stubs" >&2
        _violation "(87, WS-OFFL) vendored asset directory contains stub files (<100 bytes); replace with real assets or mios-vendor-refresh"
    else
        echo "[98-drift-checks]   vendored assets are non-stub"
    fi
}

check_resolved_env_lossless() {
    local base_file="$ROOT/usr/share/mios/reference/env-baseline.txt"
    if [[ ! -f "$base_file" ]]; then
        _violation "usr/share/mios/reference/env-baseline.txt is missing"
        return 0
    fi

    local snapshot_tool="$ROOT/usr/libexec/mios/mios-env-snapshot"
    if [[ ! -f "$snapshot_tool" ]]; then
        _violation "usr/libexec/mios/mios-env-snapshot is missing"
        return 0
    fi

    local tmp; tmp="$(mktemp)"
    if ! MIOS_VENDOR_TOML="${ROOT}/usr/share/mios/mios.toml" MIOS_TOML_ROOT="${ROOT}" bash "$snapshot_tool" > "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        _violation "mios-env-snapshot execution failed"
        return 0
    fi

    local diff_out; diff_out="$(diff -u "$base_file" "$tmp" 2>/dev/null || true)"
    rm -f "$tmp"

    if [[ -z "$diff_out" ]]; then
        echo "[98-drift-checks]   resolved environment is lossless"
    else
        if [[ "${MIOS_ENV_BASELINE_BUMP:-0}" == "1" ]]; then
            echo "[98-drift-checks]   resolved environment drifted but MIOS_ENV_BASELINE_BUMP=1 override set"
        else
            echo "  [lossless-env-drift] resolved MIOS_* environment drifted from env-baseline.txt:" >&2
            echo "$diff_out" | head -n 30 >&2
            _violation "resolved environment drifted from usr/share/mios/reference/env-baseline.txt without MIOS_ENV_BASELINE_BUMP=1"
        fi
    fi
}

check_no_duplicate_value_key() {
    # One value, one name, ratcheted against reference/value-dup-baseline.tsv.
    # A missing resolver or ledger is the WORST state, not a reason to be green.
    _need_python || return 0
    local snap_tool="${ROOT}/usr/libexec/mios/mios-env-snapshot"
    local baseline="${ROOT}/usr/share/mios/reference/value-dup-baseline.tsv"
    local bump="${MIOS_VALUE_DUP_BASELINE_BUMP:-0}"
    [[ -f "$snap_tool" ]] || { _violation "check_no_duplicate_value_key: resolver usr/libexec/mios/mios-env-snapshot is absent -- the gate has no environment to inspect"; return; }
    [[ -f "$baseline" || "$bump" == "1" ]] || { _violation "check_no_duplicate_value_key: ratchet ledger usr/share/mios/reference/value-dup-baseline.tsv is absent -- regenerate with MIOS_VALUE_DUP_BASELINE_BUMP=1"; return; }
    if MIOS_VENDOR_TOML="${ROOT}/usr/share/mios/mios.toml" MIOS_TOML_ROOT="${ROOT}" \
       MIOS_RESOLVER_BIN="$(native_bin mios-resolver)" MIOS_VALUE_DUP_BASELINE_BUMP="$bump" \
       python3 tools/drift-checks.py no-duplicate-value-key "$snap_tool" "$baseline"; then
        echo "[98-drift-checks]   value-duplication within the recorded ratchet ceiling"
    else
        _violation "check_no_duplicate_value_key: resolved MIOS_* environment drifted from the value-duplication ratchet (usr/share/mios/reference/value-dup-baseline.tsv)"
    fi
}

check_pipeline_numbering() {
    local is_bad=0
    nn=$(grep -cE '\[98-drift-checks\][[:space:]]+\([0-9]+\)' "$ROOT/automation/98-drift-checks.sh" 2>/dev/null || true)
    nn="${nn:-0}"
    if [[ "$nn" -ne 0 ]]; then
        echo "  [pipeline-numbering-drift] $nn hand-written check label reintroduced in 98-drift-checks.sh; the SSOT drift-gate-index.tsv ordinal is the single check number" >&2
        is_bad=1
    fi
    if grep -qE 'Step count in chain: \$\(ls .*mios-step.*wc -l\)' "$ROOT/automation/build.sh" 2>/dev/null; then
        echo "  [pipeline-numbering-drift] build.sh re-counts the chain via ls|wc -l instead of \$SCRIPT_COUNT" >&2
        is_bad=1
    fi
    local idx="$ROOT/usr/share/mios/reference/drift-gate-index.tsv"
    if [[ -f "$idx" ]]; then
        local dense
        dense=$(awk -F'\t' 'NR>1 && $1 ~ /^[0-9]+$/ {n++; if($1!=n){print "gap-at-"$1; exit}} END{if(n==0)print "empty"}' "$idx")
        if [[ -n "$dense" ]]; then
            echo "  [pipeline-numbering-drift] drift-gate-index.tsv ordinals not dense 1..N" >&2
            is_bad=1
        fi
    fi
    local _gen
    _gen="$(native_bin mios-gen || true)"
    if [ -n "$_gen" ]; then
        _pi_skip=""
        if [[ -n "${CTX:-}" || "$ROOT" == "/tmp/build" ]]; then
            _pi_skip="in-image OCI build (drift-gate job enforces on the pristine tree)"
        elif ! command -v git >/dev/null 2>&1 || ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            _pi_skip="no git work tree"
        elif git -C "$ROOT" ls-files --deleted 2>/dev/null | grep -q .; then
            _pi_skip="incomplete git work tree (tracked files not materialized)"
        fi
        if [[ -n "$_pi_skip" ]]; then
            echo "  [pipeline-index] SKIPPED: $_pi_skip at \$ROOT" >&2
        elif ! "$_gen" pipeline-index --root "$ROOT" --check >/dev/null 2>&1; then
            echo "  [pipeline-numbering-drift] pipeline-index.tsv is out of sync with automation/NN-*.sh scripts" >&2
            is_bad=1
        fi
    else
        echo "  [pipeline-index] $_GEN_MISSING" >&2
        is_bad=1
    fi
    if [[ "$is_bad" -ne 0 ]]; then
        _violation "pipeline numbering drift (WS-NUMBER task_1577; see reference/audit-numbering-unification.md)"
    else
        echo "[98-drift-checks]   pipeline numbering: labels deleted, single progress count, dense SSOT check ordinals, stage index in sync"
    fi
}

check_value_aliases() {
    if ! command -v python3 >/dev/null 2>&1; then
        [[ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" == "1" ]] && { _violation "check_value_aliases: python3 required (MIOS_DRIFT_REQUIRE_TOOLS=1)"; return 1; }
        return 0
    fi
    local tsv="${ROOT}/usr/share/mios/reference/value-aliases.tsv"
    local snap="${ROOT}/usr/libexec/mios/mios-env-snapshot"
    # A bare `|| return 0` dropped the check from the run with no message at all.
    if [[ ! -f "$tsv" || ! -f "$snap" ]]; then
        _violation "value-alias snapshot or reference TSV absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi
    if MIOS_VENDOR_TOML="${ROOT}/usr/share/mios/mios.toml" MIOS_TOML_ROOT="${ROOT}" \
       MIOS_RESOLVER_BIN="$(native_bin mios-resolver)" python3 tools/drift-checks.py value-aliases "$snap" "$tsv"
    then
        echo "[98-drift-checks]   value-alias consistency verified"
    else
        _violation "value-alias consistency drift (reference/value-aliases.tsv; WS-DEDUP task_1591)"
    fi
}

check_no_hardcoded_ssot_literal() {
    echo "[98-drift-checks]   checking for hardcoded fedora-XX / version literals"
    local hardcodes
    # Keep usr/share/containers in scope: the Quadlets are exactly where a
    # baked fedora-NN would land (they carry ${FEDORA_VERSION} placeholders,
    # which the fedora-\$ filter below exempts).
    hardcodes=$(grep -rE "(fedora-[0-9]{2}|stable:/v[0-9]+\.[0-9]+)" "$ROOT/automation" "$ROOT/usr/share/mios" "$ROOT/usr/share/containers" 2>/dev/null | grep -v "98-drift-checks.sh" | grep -v "\.repo" | grep -v "version-literals-audit.tsv" | grep -v "/reference/" | grep -v "/artifacts/" | grep -v "/configurator/" | grep -v "/\.claude/" || true)

    if [[ -n "$hardcodes" ]]; then
        local violations
        violations=$(echo "$hardcodes" | grep -vE "(fedora-\\\$|fedora-%|\\\$MIOS_|\\\$FEDORA_|mios\.toml)")
        if [[ -n "$violations" ]]; then
            _violation "Hardcoded version literals found in SSOT (use \${FEDORA_VERSION} / \${MIOS_K3S_VERSION} instead):"
            echo "$violations" | head -n 10 >&2
        fi
    fi
}

check_bash_phase_ratchet() {
    echo "[98-drift-checks]   bash phase script count ratchet check"
    local bin; bin="$(_gate_bin)" || { _violation "mios-gate is not built, so check_bash_phase_ratchet could not run"; return; }
    "$bin" phase-ratchet --root "$ROOT" || \
        _violation "the automation/NN-*.sh count is not exactly [build.ratchet].max_phase_scripts -- fold a phase, or lower the ceiling to the count"
}

check_signature_policy() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_signature_policy could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" signature-policy --root "$ROOT"; then
        echo "[98-drift-checks]   usr/lib/containers/policy.json regenerates byte-identically from [security.sigstore]"
        return 0
    else
        _violation "usr/lib/containers/policy.json does not match the [security.sigstore] projection -- regenerate it: mios-gen cosign-policy"
    fi
}

check_projection_coverage() {
    # The reverse of check_projection_registry. That one walks the register and
    # asks whether each row's generator and check exist -- the forward direction
    # only, which is silent on a generator that is on NO row. This one
    # enumerates the generators from the SSOT globs and asks whether each is on
    # the register, so an unregistered projector cannot ship unnoticed.
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_projection_coverage could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" projection-coverage --root "$ROOT"; then
        echo "[98-drift-checks]   every SSOT projector in scope is on the Law 8 registry"
    else
        _violation "Law 8 projection registry is incomplete -- register the generator in [laws.projection_registry].surfaces, or itemise it under .exempt with a reason"
    fi
}

check_build_tool_dispatch() {
    # Dispatched by ABSOLUTE path, never `command -v`: this check exists
    # because that lookup cannot resolve at bake time (task_962), so using it
    # here would make the check the first thing it detects.
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_build_tool_dispatch could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    if "$bin" build-tool-dispatch --root "$ROOT"; then
        echo "[98-drift-checks]   every bake-time tool-dispatch gate is on the shrink-only register"
    else
        _violation "an automation stage gates its Rust path on \`command -v <binary>\` that cannot resolve at bake time, so the branch is dead -- give the binary a PATH location or dispatch by absolute path (task_962)"
    fi
}

# --- every automation/NN-*.sh on disk is a phase build.sh actually runs ---
check_phase_registry() {
    echo "[98-drift-checks]   every automation/NN-*.sh is registered as a build phase"
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_phase_registry could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    "$bin" phase-registry --root "$ROOT" || \
        _violation "a phase script on disk is not in [build.phases].list, or the register describes it wrongly, so build.sh silently runs a pipeline short of a stage (task_975)"
}

# --- no miosd drift Check claims a verdict about a tree it never reads ---
check_drift_stubs() {
    echo "[98-drift-checks]   no miosd drift Check claims a verdict it did not compute"
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_drift_stubs could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    "$bin" drift-stubs --root "$ROOT" || \
        _violation "a miosd drift Check returns Verdict::Pass without reading the tree, or the unimplemented register is stale -- check_native_twinless runs this suite's Checks in the gate (task_974)"
}

# --- every native miosd drift Check with no bash twin in main() runs in this gate ---
check_native_twinless() {
    # Ruling Q8 keeps source-tree gates out of the bake. A native Check named like
    # a check main() dispatches runs as that twin; the rest run here, natively.
    echo "[98-drift-checks] every native miosd drift Check with no bash twin in main() runs in this gate"
    local bin; bin="$(native_bin miosd "${MIOS_MIOSD_BIN:-}")" || {
        _violation "miosd is not built, so the native drift Checks with no bash twin could not run -- build it: cd src/mios-rs && cargo build -p miosd"
        return; }
    local ids; ids="$("$bin" drift-check --list 2>&1 | sed -nE 's/^[[:space:]]*[0-9]+\.[[:space:]]+(check_[a-z0-9_]+)[[:space:]].*/\1/p')"
    [[ -n "$ids" ]] || { _violation "miosd drift-check --list named no Check -- an empty registry is not a pass"; return; }
    local twins; twins="$(sed -n '/^main() {/,/^}/p' "$_self_dir/$(basename "$_self")" | sed -nE 's/^[[:space:]]+(check_[a-z0-9_]+)[[:space:]]*$/\1/p')"
    local id out ran=0 twinned=0
    for id in $ids; do
        if grep -qxF -- "$id" <<<"$twins"; then twinned=$((twinned + 1)); continue; fi
        ran=$((ran + 1))
        # --only matches by substring; exactly one Check must run, and pass.
        if ! out="$("$bin" drift-check --root "$ROOT" --only "$id" 2>&1)" || [[ "$out" != *"[1/1 PASS "* ]]; then
            _violations_from "check_native_twinless: $id: " "$(grep -E 'FAIL|MISSING|SKIP|Summary' <<<"$out" || printf '%s' "$out")" || :
        fi
    done
    echo "[98-drift-checks]   ${ran} twinless native Check(s) ran here; ${twinned} run as their bash twin"
}

check_no_silent_tool_skips() {
    local require_tools="${MIOS_DRIFT_REQUIRE_TOOLS:-0}"
    local bad_skips=()
    local f

    for f in "$ROOT/automation/98-drift-checks.sh" "$ROOT"/automation/lint-*.sh; do
        [[ -f "$f" ]] || continue
        local hits
        hits=$(grep -nE 'command -v.*\|\|[[:space:]]*(return 0|exit 0)' "$f" | grep -v 'MIOS_DRIFT_REQUIRE_TOOLS' || true)
        if [[ -n "$hits" ]]; then
            bad_skips+=("$f:" "$hits")
        fi
    done

    if [[ "${#bad_skips[@]}" -gt 0 ]]; then
        if [[ "$require_tools" == "1" ]]; then
            _violation "Silent tool skips found under MIOS_DRIFT_REQUIRE_TOOLS=1:"
            printf '%s\n' "${bad_skips[@]}" >&2
        else
            echo "[98-drift-checks]   WARNING: silent tool skips present (enable MIOS_DRIFT_REQUIRE_TOOLS=1 to enforce)"
        fi
    else
        echo "[98-drift-checks]   no silent tool skips found (MIOS_DRIFT_REQUIRE_TOOLS compliance clean)"
    fi
}

check_negatives_are_effective() {
    echo "[98-drift-checks]   negative-test effectiveness check"
    local neg_file="${ROOT}/tests/drift-gate-negatives.sh"
    # The header above already printed, so a bare `return 0` here read as a
    # check that ran and found nothing.
    if [[ ! -f "$neg_file" ]]; then
        _violation "tests/drift-gate-negatives.sh absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    if python3 tools/drift-checks.py negatives-are-effective
    then
        echo "[98-drift-checks]   all negative tests pass structural effectiveness contract"
    else
        _violation "ineffective negative tests found in tests/drift-gate-negatives.sh"
    fi
}

check_pipefail_grep_lint() {
    echo "[98-drift-checks]   pipefail grep lint check"
    local neg_file="${ROOT}/tests/drift-gate-negatives.sh"
    # Same silent-skip shape as check_negatives_are_effective above.
    if [[ ! -f "$neg_file" ]]; then
        _violation "tests/drift-gate-negatives.sh absent -- a tracked deliverable is missing, so this check cannot run"
        return
    fi

    if python3 tools/drift-checks.py pipefail-grep-lint
    then
        echo "[98-drift-checks]   no piped greps reading from non-echo/printf commands in negatives harness"
    else
        _violation "piped grep from non-echo/printf found in tests/drift-gate-negatives.sh"
    fi
}

check_skip_list_covered() {
    echo "[98-drift-checks]   checking the agent-pipe skip list lives in the SSOT"
    _need_python || return 0
    # Declared separately: `local out=$(cmd)` returns the status of `local`, not of
    # cmd, which would make this check unable to fail.
    local out
    out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py skip-list-covered 2>&1)" \
        || { _violations_from "check_skip_list_covered: " "$out"; return; }
    echo "[98-drift-checks]   the skip list is SSOT-owned and no workflow shadows it"
}

# Both renderers resolve mios.toml through the shared layered resolver, which
# honours MIOS_ROOT / MIOS_TOML* from the environment. The gate runs with
# userenv.sh sourced, so those point at the INSTALLED system, not the tree under
# test -- pin every tier to $ROOT or the check silently grades the wrong file.
_render_env() {
    printf '%s\n' \
        "MIOS_ROOT=$ROOT" \
        "MIOS_TOML_ROOT=$ROOT" \
        "MIOS_TOML=$ROOT/usr/share/mios/mios.toml" \
        "MIOS_VENDOR_TOML=$ROOT/usr/share/mios/mios.toml" \
        "MIOS_VENDOR_TOML_D=$ROOT/usr/lib/mios/mios.d" \
        "MIOS_HOST_TOML=$ROOT/etc/mios/mios.toml" \
        "MIOS_HOST_TOML_D=$ROOT/etc/mios/mios.d" \
        "MIOS_USER_TOML=$ROOT/.mios-absent.toml" \
        "MIOS_USER_TOML_D=$ROOT/.mios-absent.d"
}

check_ports_category_schema() {
    echo "[98-drift-checks]   checking port category schema (allocation + collisions)"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    # shellcheck disable=SC2046
    if ! out=$(cd "$ROOT" && env $(_render_env) "$bin" render-ports --root "$ROOT" --check 2>&1); then
        printf '%s\n' "$out" | head -n 20 >&2
        _violation "port schema drift: every port must derive from [ports.categories] (base + index*stride), belong to exactly one category, and not collide -- run mios-gen render-ports"
    fi
}

check_globals_generated() {
    echo "[98-drift-checks]   checking generated globals resolvers match SSOT"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if ! out=$( "$bin" render-globals --root "$ROOT" --check 2>&1 ); then
        printf '%s\n' "$out" | head -n 10 >&2
        _violation "automation/lib/globals.{sh,ps1} are stale -- they are GENERATED IN FULL from mios.toml; run mios-gen render-globals (never hand-edit them)"
    fi
}

check_ai_metadata_fresh() {
    _need_python || return 0
    local out
    if out="$(cd "$ROOT" && python3 usr/libexec/mios/mios-ai-metadata.py --root "$ROOT" --check-fresh 2>&1)"; then
        echo "[98-drift-checks]   usr/share/mios/ai/v1/metadata.json regenerates byte-identically from the tracked AI headers"
    else
        printf '%s\n' "$out" | head -n 48 >&2
        _violation "check_ai_metadata_fresh: usr/share/mios/ai/v1/metadata.json is stale vs the tracked AI-* headers -- re-run bash tools/sync-generated.sh (Law 8 SSOT-PROJECTION)"
    fi
}

check_ai_manifests_fresh() {
    echo "[98-drift-checks]   checking AI manifest freshness"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if ! out=$( cd "$ROOT" && "$bin" ai-manifest --root "$ROOT" --check 2>&1 ); then
        printf '%s\n' "$out" | grep -v '^Generated ' | head -n 14 >&2
        _violation "AI manifests are stale or out of date (run 'just sync', or bash tools/sync-generated.sh, which regenerates every projection in dependency order)"
    fi
}

# A drift gate grades the tree and must never write it: check_names_registry
# rewrote names.generated.txt in place (6488 -> 720 lines) and nothing noticed.
# The tracked tree is fingerprinted through a private index (the real index is
# never touched) before and after the checks; no git work tree, no fingerprint.
_tracked_tree() {
    local idx; idx="$(mktemp)" || return 1
    cp "$(git -C "$ROOT" rev-parse --path-format=absolute --git-path index 2>/dev/null)" "$idx" 2>/dev/null \
        && GIT_INDEX_FILE="$idx" git -C "$ROOT" add -u >/dev/null 2>&1 \
        && GIT_INDEX_FILE="$idx" git -C "$ROOT" write-tree 2>/dev/null
    local rc=$?; rm -f "$idx"; return "$rc"
}
_assert_read_only() {  # $1 = what ran; _RO_BEFORE = the fingerprint taken before it
    local after; [[ -n "${_RO_BEFORE:-}" ]] && after="$(_tracked_tree)" || return 0
    [[ "$after" == "$_RO_BEFORE" ]] && return 0
    _violation "$1 wrote into the tree it grades -- a drift gate must be read-only: $(git -C "$ROOT" diff-tree -r --name-only "$_RO_BEFORE" "$after" | head -5 | tr '\n' ' ')" || :
    return 1
}

check_memory_poisoning_scan() {
    echo "[98-drift-checks] memory-poisoning scan enabled in SSOT and active in runtime"
    _need_python || return 0
    local out
    out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py memory-poisoning-scan 2>&1)" || {
        _violations_from "check_memory_poisoning_scan: " "$out"; return; }
    echo "[98-drift-checks]   memory-poisoning scan verified"
}

main() {
    if [[ $# -eq 1 && -n "$1" ]]; then
        if declare -f "$1" >/dev/null; then
            _RO_BEFORE="$(_tracked_tree)" || _RO_BEFORE=""
            _RO_WHO="$1"
            # A trap, so a check that dies under errexit is still held to it.
            trap '_assert_read_only "$_RO_WHO" || exit 1' EXIT
            "$1"
            if [[ "$VIOLATIONS" -eq 0 ]]; then
                exit 0
            fi
            exit 1
        else
            echo "Unknown check function: $1" >&2
            exit 2
        fi
    fi

    set +e
    _RO_BEFORE="$(_tracked_tree)" || _RO_BEFORE=""

    check_gate_registry
    check_dead_lane
    check_retired_models
    check_structured
    check_hint_coverage

    check_module_boundary
    check_rbac_tiers
    check_agent_schema
    check_ai_manifest
    check_package_registry
    check_cli_sql_safety
    check_module_test_coverage
    check_raw_toml_readers
    check_capability_manifest
    check_surface_parity
    check_no_hardcode
    check_no_hardcode_version
    check_pod_quadlets
    check_egress_firewall
    check_blade_dropins
    check_unwired_modules
    check_cephfs_ssot
    check_converge_ssot
    check_hummingbird
    check_container_ports
    check_bootstrap_sync
    check_agent_pipe_budgets
    check_no_bare_port_literals
    check_dotfiles_projection
    check_edge_generators
    check_edge_status
    check_verb_backends
    check_userenv_parity
    check_globals_ports
    check_globals_image_parity
    check_dag_integrity
    check_names_registry
    check_drift_projection
    check_drift_build_catalog
    check_canonical_bools
    check_etc_duplicates
    check_no_mkdir_in_var
    check_quadlet_privilege
    check_unit_security
    check_preset_roster_complete
    check_var_closure
    check_lint_is_final
    check_firstboot_degrade_open
    check_vendor_urls
    check_resolver_twin_parity
    check_resolver_twin_equivalence
    check_template_conformance
    check_kargs_projection
    check_greenboot_enablement
    check_greenboot
    check_chrony_projection
    check_nut_projection
    check_fluff_tokens
    check_coordination_hygiene
    check_templates_compilation
    check_impossible_eol_regressions
    check_deploy_plane
    check_version_ssot
    check_cargo_manifest_generated
    check_root_toml_subset
    check_toml_projection
    check_ratchet_direction
    check_size_ceiling
    check_task_store
    check_task_ids
    check_toolchain_pin
    check_ai_config_projection
    check_artifact_prompt
    check_ai_artifacts
    check_render_quadlets
    check_render_extension_coverage
    check_bake_plan
    check_bake_plan_integrity
    check_bake_ref_defaults
    check_roadmap_index
    check_cli_eval_safety
    check_sbom_metadata
    check_shellcheck
    check_target_languages
    check_static_linkage
    check_curl_retry
    check_resolver_ssot_refs
    check_nested_podman_caps
    check_bake_budget
    check_clevis_luks
    check_metal_vfio
    check_router_parity
    check_council_gate_ssot
    check_containerfile_pinned_clones
    check_firstboot_tier
    check_bound_image_store
    check_rechunk_budget
    check_python_lint
    check_test_hermeticity
    check_negative_test_coverage
    check_soft_mode_not_committed
    check_ssot_lint_equivalence
    check_gate_index
    check_oci_archive_path
    check_replaceme_mount_substitution
    check_kickstart_shell_syntax
    check_bib_rootfs_label_policy
    check_offline_install_invariant
    check_installer_family_roles
    check_bib_configs_projection
    check_repo_partition_label_ssot
    check_bib_single_config_invariant
    check_artifact_recipes
    check_build_artifacts_output_dir
    check_win11_vm_template_xml
    check_ipa_enroll_projection
    check_bootc_install_projection
    check_uki_cmdline_projection
    check_composefs_projection
    check_cockpit_projection
    check_template_self_conformance
    check_native_lint
    check_resolver_shell_equivalence
    check_resolver_ps_equivalence
    check_cargo_deny
    check_ps_redirectors
    check_powershell_parse
    check_ps_signatures
    check_windows_exe_provenance
    check_unpinned_runtime_fetches
    check_secret_handling
    check_os_update_timer_enabled
    check_wsl_distro_resolution
    check_adhoc_toml_parsers
    check_install_uninstall_symmetry
    check_ps_port_fallback_ssot
    check_github_slug_casing
    check_ps_encoding_and_bom
    check_unit_dependency_closure
    check_docs_ratchet
    check_header_integrity
    check_legibility_ratchet
    check_legacy_archive
    check_docs_ratchet_monotone
    check_comment_lex_equivalence
    check_no_generated_prose_in_resolvers
    check_manual_generated
    check_manual_ledger
    check_comment_landing
    check_credential_literals
    check_protected_refs
    check_names_registry_equivalence
    check_redact_coverage
    check_task_schema
    check_daemon_governor
    check_manual_links
    check_doc_port_scheme

    check_chrony_ptp_dropin
    check_renderer_gate_coverage
    check_smoke_manifest
    check_negative_coverage
    check_verb_templates
    check_pipe_boundaries
    check_standardize_docs
    check_sync_wiki
    check_vllm_name_canonical
    check_pipe_extraction_parity
    check_desktop_launchers
    check_tmux_theme
    check_btop_theme
    check_fastfetch
    check_guacamole_consistency
    check_no_inert_ssot_tables
    check_profile_integrity
    check_doc_refs_resolve
    check_resolver_differential_parity
    check_generator_host_parity
    check_v2v_import_ssot
    check_law_enforcers
    check_usr_over_etc
    check_projection_registry
    check_projection_coverage
    check_db_seed_coverage
    check_verb_stub_backends
    check_account_column_parity
    check_module_length
    check_firstboot_provisioners
    check_schema_consumers
    check_tasks_status_parity
    check_agy_tasks
    check_mios_toml_integrity
    check_privileged_quadlets_minimal
    check_container_names
    check_service_urls
    check_ports_bound
    check_blade_coverage
    check_blade_karg
    check_blade_reconcile_schema
    check_role_ssot
    check_port_fallbacks
    check_node_pool
    check_metal_vs_hosted
    check_unit_projection
    check_ssot_consumer_keys
    check_fleet_safety
    check_adr_index
    check_vendored_assets_non_stub
    check_resolved_env_lossless
    check_no_duplicate_value_key
    check_pipeline_numbering
    check_value_aliases

    check_no_hardcoded_ssot_literal
    check_bash_phase_ratchet
    check_no_silent_tool_skips
    check_memory_poisoning_scan
    check_build_tool_dispatch
    check_signature_policy
    check_phase_registry
    check_drift_stubs
    check_negatives_are_effective
    check_pipefail_grep_lint
    check_skip_list_covered
    check_ai_manifests_fresh
    check_ai_metadata_fresh
    check_ports_category_schema
    check_globals_generated
    check_ci_suite_coverage
    check_manpages
    check_rust_test_coverage
    check_rust_categories
    check_header_comment_syntax
    check_variant_registry
    check_deploy_formats
    check_verify_images
    check_temp_fixture_cleanup
    check_negatives_registered
    check_tracked_readable
    check_leaked_fixtures
    check_native_twinless
    _assert_read_only "the gate run (re-run each check alone to name the writer)"

    set -e

    echo "[98-drift-checks]"
    if [[ "$VIOLATIONS" -eq 0 ]]; then
        echo "[98-drift-checks] PASS: no AI-plane source drift"
        exit 0
    fi
    echo "[98-drift-checks] FAIL: $VIOLATIONS drift violation above" >&2
    if [[ "$_SOFT" == "1" ]]; then
        echo "[98-drift-checks]"
        exit 0
    fi
    exit 1
}

check_template_self_conformance() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py template-self-conformance
    then
        echo "[98-drift-checks]   every template scaffolds to a self-conforming output"
    else
        _violation "Template self-conformance failure"
    fi
}

check_native_lint() {
    if ! command -v cargo >/dev/null 2>&1; then
        return 0
    fi
    # mios-wallpaperd is Windows-only (windows-sys) and cannot build on Linux,
    # which is why mios-ci.yml excludes it from both fmt and clippy. Without the
    # same exclusion this check failed on every Linux runner -- and swallowed
    # the compiler output, so it just said "cargo check failed".

    # Both roots: src/mios-rs is 12k lines the gate never compiled.
    local ws out excl
    for ws in tools/native src/mios-rs; do
        if [[ ! -f "$ROOT/$ws/Cargo.toml" ]]; then
            _violation "$ws/Cargo.toml is missing -- a tracked workspace root is gone, so this check cannot run"
            continue
        fi
        excl=()
        # Only this workspace declares the Windows-only crate; passing --exclude
        # for a non-member is an error, so the flag follows the manifest.
        if grep -q 'mios-wallpaperd' "$ROOT/$ws/Cargo.toml"; then
            excl=(--exclude mios-wallpaperd)
        fi
        if out=$(cd "$ROOT/$ws" && cargo check --workspace "${excl[@]}" 2>&1); then
            echo "[98-drift-checks]   $ws workspace cargo check passed"
        else
            printf '%s\n' "$out" | grep -E '^(error|warning)' | head -n 10 >&2
            _violation "$ws cargo check failed"
        fi
    done
}

# --- shell resolver logic is identical to python/PS SSOT resolvers ---
check_resolver_shell_equivalence() {
    echo "[98-drift-checks] shell resolver logic is identical to python/PS SSOT resolvers"
    # Pin the SSOT tier explicitly (an inherited MIOS_TOML would grade the
    # installed system) and surface the mismatch instead of swallowing it.
    local out
    if ! out=$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" \
                 MIOS_VENDOR_TOML="$ROOT/usr/share/mios/mios.toml" \
                 "$PYTHON" tools/check-runtime.py resolver-twin 2>&1); then
        printf '%s\n' "$out" | tail -n 12 >&2
        _violation "resolver shell equivalence check failed"
    fi
}

# --- comment lexing preserves semantic intent across documentation generators ---
check_comment_lex_equivalence() {
    echo "[98-drift-checks] comment lexing preserves semantic intent across documentation generators"
    local out bin; bin="$(native_bin mios-comment-lex "${MIOS_COMMENT_LEX_BIN:-}")" || bin=""
    if ! out=$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" MIOS_COMMENT_LEX_BIN="$bin" python3 tools/check-docs.py comment-lex 2>&1); then
        printf '%s\n' "$out" | tail -n 12 >&2
        _violation "comment lexer equivalence check failed"
    fi
}

# --- the PowerShell half of the resolver twin exists; its CONTENT is check_globals_generated's job ---
check_resolver_ps_equivalence() {
    echo "[98-drift-checks] the PowerShell half of the resolver twin exists; its content is check_globals_generated's job"
    if [[ -f "$ROOT/automation/lib/globals.ps1" ]]; then
        echo "[98-drift-checks]   globals.ps1 present (content equivalence is check_globals_generated)"
    else
        _violation "automation/lib/globals.ps1 is missing"
    fi
}

# --- tools/native/deny.toml supply-chain policy is present ---
check_cargo_deny() {
    # Presence of the policy file is the whole assertion: no step in this
    # repo runs it, so advisories, licenses and bans stay unenforced.
    echo "[98-drift-checks] tools/native/deny.toml supply-chain policy is present"
    if [[ -f "$ROOT/tools/native/deny.toml" ]]; then
        echo "[98-drift-checks]   tools/native/deny.toml present -- the policy is NOT executed, so advisories/licenses/bans stay unenforced"
    else
        _violation "tools/native/deny.toml missing"
    fi
}

# --- all PowerShell scripts (.ps1, .psm1) parse without syntax errors ---
check_powershell_parse() {
    echo "[98-drift-checks] all PowerShell scripts (.ps1, .psm1) parse without syntax errors"
    if [[ -f "$ROOT/automation/lint-powershell.sh" ]]; then
        if ! bash "$ROOT/automation/lint-powershell.sh"; then
            _violation "PowerShell AST parse check failed (lint-powershell.sh)"
        fi
    else
        _violation "automation/lint-powershell.sh missing"
    fi
}

check_ps_redirectors() {
    echo "[98-drift-checks] PowerShell script entrypoint redirectors point to canonical implementation"
    # Only files the repo actually SHIPS. run-pipeline.ps1 was listed here but
    # is an untracked 3-line local wrapper around mios-pipeline.ps1, so the
    # "Redirector file missing" branch fired unconditionally on every clean
    # checkout. (mios-pipeline.ps1 itself is 415 lines -- it is the real
    # pipeline script, not a thin redirector, so it does not belong here.)
    local redirectors=("mios-build-local.ps1")
    local f line_count max_lines=50
    for f in "${redirectors[@]}"; do
        if [[ -f "$ROOT/$f" ]]; then
            line_count=$(wc -l < "$ROOT/$f")
            if (( line_count > max_lines )); then
                _violation "Redirector $f exceeds max line budget ($line_count > $max_lines)"
            fi
        else
            _violation "Redirector file $f missing"
        fi
    done
    echo "[98-drift-checks]   PS redirectors within line budget"
}

# --- PowerShell script signature headers and execution policies are clean ---
check_ps_signatures() {
    echo "[98-drift-checks] PowerShell script signature headers and execution policies are clean"
    if [[ -f "$ROOT/automation/verify-ps-signatures.ps1" ]]; then
        local ps_bin=""
        for candidate in pwsh powershell powershell.exe \
            /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
            /c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
            "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"; do
            if command -v "$candidate" >/dev/null 2>&1 || [ -f "$candidate" ]; then
                if "$candidate" -NoProfile -Command "exit 0" >/dev/null 2>&1; then
                    ps_bin="$candidate"
                    break
                fi
            fi
        done

        if [ -n "$ps_bin" ]; then
            local script_path="$ROOT/automation/verify-ps-signatures.ps1"
            local repo_root="$ROOT"
            if [[ "$ps_bin" == *".exe"* ]] && command -v wslpath >/dev/null 2>&1; then
                script_path="$(wslpath -w "$script_path")"
                repo_root="$(wslpath -w "$repo_root")"
            fi
            if ! "$ps_bin" -NoProfile -NonInteractive -File "$script_path" -RepoRoot "$repo_root"; then
                _violation "PowerShell Authenticode signature verification failed"
            fi
        else
            echo "[98-drift-checks]   WARNING: powershell absent, skipping signature check" >&2
        fi
    else
        _violation "automation/verify-ps-signatures.ps1 missing"
    fi
}

# --- vendored Windows executables carry valid origin provenance metadata ---
check_windows_exe_provenance() {
    echo "[98-drift-checks] vendored Windows executables carry valid origin provenance metadata"
    local win_dir="$ROOT/usr/share/mios/windows"
    local _WIN_EXE_SOURCE_EXEMPT
    _WIN_EXE_SOURCE_EXEMPT="$(MIOS_DRIFT_ROOT="$ROOT" python3 -c "
import os, sys
import tomllib
p = os.path.join(os.environ['MIOS_DRIFT_ROOT'], 'usr/share/mios/mios.toml')
try:
    with open(p, 'rb') as fh:
        d = tomllib.load(fh)
except Exception:
    sys.exit(0)
for name in ((d.get('security') or {}).get('windows_binaries') or {}).get('source_exempt', []):
    print('\"%s\"' % name)
" 2>/dev/null || true)"
    if [[ -d "$win_dir" ]]; then
        for exe in "$win_dir"/*.exe; do
            if [[ -f "$exe" ]]; then
                local base cs_src
                base="$(basename "$exe" .exe)"
                cs_src="$win_dir/$base.cs"
                # The old fallback tested a FIXED path (MiosServiceTool.cs)
                # which always exists, so `! -f cs_src && ! -f cs_src2` was
                # never true and this gate could not fire for ANY binary.
                # Each .exe must now have its own .cs, with known source-less
                # binaries declared in SSOT so the gap is recorded, not hidden.
                if [[ ! -f "$cs_src" ]]; then
                    if grep -q "\"$(basename "$exe")\"" <<<"$_WIN_EXE_SOURCE_EXEMPT"; then
                        echo "[98-drift-checks]   NOTE: $(basename "$exe") has no in-repo source (declared in [security.windows_binaries].source_exempt)" >&2
                    else
                        _violation "Tracked Windows binary $(basename "$exe") lacks reproducible source build (ADR-0003 violation)"
                    fi
                fi
            fi
        done
        echo "[98-drift-checks]   All tracked Windows .exe binaries have verifiable source provenance"
    fi
}

# --- no unpinned network fetches exist in runtime execution paths ---
check_unpinned_runtime_fetches() {
    echo "[98-drift-checks] no unpinned network fetches exist in runtime execution paths"
    local win_dir="$ROOT/usr/share/mios/windows"
    if [[ -d "$win_dir" ]]; then
        local f
        for f in "$win_dir"/*.ps1; do
            if [[ -f "$f" ]]; then
                if grep -q -E 'Invoke-WebRequest|curl' "$f"; then
                    if ! grep -q -E 'Test-SHA256Integrity|sha256sum' "$f"; then
                        _violation "Runtime download in $(basename "$f") lacks SHA-256 integrity verification (ADR-0003 violation)"
                    fi
                fi
            fi
        done
        echo "[98-drift-checks]   All Windows runtime downloads carry SHA-256 integrity checks"
    fi
}

# --- no hardcoded secret literals or insecure credential stores found ---
check_secret_handling() {
    _need_python || return 0
    if MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py secret-handling
    then
        echo "[98-drift-checks]   secret handling gate verified clean end-to-end"
    else
        _violation "secret handling gate failed: un-allowlisted secret shape or temp secret leak found"
    fi
}

# --- OS update timer systemd units are enabled and properly configured ---
check_os_update_timer_enabled() {
    echo "[98-drift-checks] OS update timer systemd units are enabled and properly configured"
    # uupd.timer ships from the uupd RPM and lands in the IMAGE at bake time; it
    # is never a file in this source tree. Looking for it here could only ever
    # fail, so this asserts what the repo actually owns: the SSOT declares an
    # updater package, and a bake phase enables its timer.
    local unit_dir="$ROOT/usr/share/mios/systemd"
    local sys_dir="$ROOT/usr/lib/systemd/system"
    local installer="$ROOT/automation/50-uupd-installer.sh"
    local declared=0 wired=0

    if [[ -f "$unit_dir/uupd.timer" || -f "$sys_dir/uupd.timer" \
       || -f "$unit_dir/bootc-fetch-apply-updates.timer" \
       || -f "$sys_dir/bootc-fetch-apply-updates.timer" ]]; then
        declared=1 wired=1          # shipped in-tree: nothing further to prove
    else
        if _need_python; then
            MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py os-update-timer-enabled && declared=1
        else
            declared=1              # cannot read the SSOT here; do not invent a verdict
        fi
        if [[ -f "$installer" ]] && grep -q 'uupd\.timer\|bootc-fetch-apply-updates\.timer' "$installer"; then
            wired=1
        fi
    fi

    if [[ $declared -eq 1 && $wired -eq 1 ]]; then
        echo "[98-drift-checks]   OS update mechanism declared in the SSOT and wired by a bake phase"
    else
        [[ $declared -eq 1 ]] || _violation "no OS updater package (uupd or bootc) declared in mios.toml [packages]"
        [[ $wired -eq 1 ]] || _violation "automation/50-uupd-installer.sh does not enable uupd.timer or bootc-fetch-apply-updates.timer"
    fi
}

# --- WSL distro launcher resolves target distribution without fallback ambiguity ---
check_wsl_distro_resolution() {
    echo "[98-drift-checks] WSL distro launcher resolves target distribution without fallback ambiguity"
    local win_dir="$ROOT/usr/share/mios/windows"
    # A tracked directory: absent, it is the subject going missing, which is
    # the worst state available rather than every script resolving.
    if [[ ! -d "$win_dir" ]]; then
        _violation "usr/share/mios/windows is absent -- no Windows script could be read, so distro resolution was never tested"
        return
    fi
    local f n=0
    for f in "$win_dir"/*.ps1; do
        [[ -f "$f" ]] || continue
        n=$(( n + 1 ))
        # Compliant means the distro is DERIVED, not asserted: either via
        # the shared Resolve-MiosDistro, or by the Lxss registry walk that
        # resolver was lifted from (mios-claude-mcp-setup.ps1 owns it).
        # The literal is allowed only as the last-resort default.
        if grep -q "podman-MiOS-DEV" "$f"; then
            if ! grep -q "Resolve-MiosDistro" "$f" && ! grep -q "CurrentVersion\\\\Lxss" "$f"; then
                _violation "Script $(basename "$f") carries a hardcoded 'podman-MiOS-DEV' distro literal instead of resolving it (Resolve-MiosDistro or the Lxss registry walk)"
            fi
        fi
    done
    if (( n == 0 )); then
        _violation "usr/share/mios/windows holds no .ps1 file -- a corpus of zero cannot show that every Windows script resolves its distro"
        return
    fi
    echo "[98-drift-checks]   all $n Windows script(s) perform generative Resolve-MiosDistro resolution"
}

# --- no ad-hoc regex/string TOML parsing used where canonical resolver exists ---
check_adhoc_toml_parsers() {
    echo "[98-drift-checks] no ad-hoc regex/string TOML parsing used where canonical resolver exists"
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py adhoc-toml-parsers)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   No ad-hoc regex TOML parsers outside the shared resolver"
}

# Every Windows artifact MiOS creates is registered in mios.toml
# [windows.owned_artifacts]; the uninstaller must remove each one. Driving the
# check off the SSOT means adding an artifact there fails the gate until
# Uninstall-MiOS.ps1 learns to clean it up.
# --- every [windows.owned_artifacts] entry has an uninstall step, and installers create no artifact the SSOT omits ---
check_install_uninstall_symmetry() {
    # Said "installer script side effects" while reading only the uninstaller
    # and the SSOT list. It now also reads the installers, so both directions
    # are real -- but only LITERAL artifact names are detectable.
    echo "[98-drift-checks] every [windows.owned_artifacts] entry has an uninstall step, and installers create no artifact the SSOT omits"
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py install-uninstall-symmetry)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   Uninstall-MiOS.ps1 removes every declared artifact, and no installer creates an undeclared one"
}

# The Windows scripts carry last-resort port literals for the case where
# mios.toml cannot be found at all. They are still SSOT-derived values, so they
# must equal [ports] exactly -- otherwise two MiOS scripts on one host resolve
# different ports for the same lane (the bug this gate was written for).
# --- PowerShell port fallback defaults equal mios.toml [ports] SSOT ---
check_ps_port_fallback_ssot() {
    echo "[98-drift-checks] PowerShell port fallback defaults equal mios.toml [ports] SSOT"
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py ps-port-fallback-ssot 2>&1)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   PowerShell port fallbacks all match mios.toml [ports]"
}

# --- the MiOS org slug is lowercase in every ghcr.io / github.com / raw-content reference ---
check_github_slug_casing() {
    echo "[98-drift-checks] the MiOS org slug is lowercase in every ghcr.io / github.com / raw-content reference"
    _need_python || return 0
    # Only the MiOS org is subject to this rule. Upstream orgs are legitimately
    # mixed-case (StevenBlack, NousResearch, NVIDIA), so "any uppercase org" is
    # the wrong predicate. The org is read from SSOT, never spelled here.
    local org scanned bad
    org="$(cd "$ROOT" && python3 -c '
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    name = tomllib.load(fh).get("image", {}).get("name", "")
print(name.split("/")[1] if name.count("/") >= 1 else "")
' "$ROOT/usr/share/mios/mios.toml" 2>/dev/null)"
    if [[ -z "$org" ]]; then
        _violation "check_github_slug_casing: cannot read the org from [image].name"
        return
    fi
    local ci_pat=""
    local i ch
    for (( i=0; i<${#org}; i++ )); do
        ch="${org:i:1}"
        if [[ "$ch" == [a-z] ]]; then
            ci_pat+="[${ch}$(echo "$ch" | tr 'a-z' 'A-Z')]"
        else
            ci_pat+="$ch"
        fi
    done

    scanned="$(cd "$ROOT" && git ls-files -c -o --exclude-standard | grep -cvE '\.md$')"
    if [[ -z "$scanned" || "$scanned" -lt 100 ]]; then
        _violation "check_github_slug_casing scanned only ${scanned:-0} files -- the subject list is wrong"
        return
    fi
    bad="$(cd "$ROOT" && git ls-files -z -c -o --exclude-standard         | xargs -0 grep -HnIE "(ghcr[.]io|raw[.]githubusercontent[.]com|github[.]com)/${ci_pat}" 2>/dev/null         | grep -v "/${org}"         | grep -vE '\.md:'         | grep -v 'usr/share/doc/mios/knowledge'         | grep -v '^automation/manifest[.]json:' || true)"
    if [[ -n "$bad" ]]; then
        local line
        while IFS= read -r line; do
            [[ -n "$line" ]] && { _violation "MiOS org slug is not the canonical '${org}': $line" || true; }
        done <<<"$bad"
        return
    fi
    echo "[98-drift-checks]   ${scanned} file(s) scanned; the ${org} org slug is canonical everywhere"
}

check_ps_encoding_and_bom() {
    echo "[98-drift-checks] PowerShell script encoding supports the declared interpreter contract"
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py ps-encoding-and-bom)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   PowerShell BOMs match content: non-ASCII scripts carry one, ASCII scripts do not"
}

# --- systemd unit security hardening options meet baseline policy ---
check_unit_security() {
    echo "[98-drift-checks] systemd unit security hardening options meet baseline policy"
    if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
        echo "[98-drift-checks]   WARNING: python missing" >&2
        return 0
    fi
    local out
    if ! out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py unit-security 2>&1)"; then
        _violations_from "check_unit_security: " "$out"
        return
    fi
    if [[ -n "$out" ]]; then
        echo "[98-drift-checks]   NOTE: legacy unconfined units pending migration (roster active)"
    else
        echo "[98-drift-checks]   All systemd service security baselines pass"
    fi
}

# --- systemd preset roster declares explicit enable/disable policy for every MiOS unit ---
check_preset_roster_complete() {
    echo "[98-drift-checks] systemd preset roster declares explicit enable/disable policy for every MiOS unit"
    if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
        echo "[98-drift-checks]   WARNING: python missing" >&2
        return 0
    fi
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py preset-roster-complete 2>&1)" || {
        _violations_from "check_preset_roster_complete: " "$out"; return; }
    echo "[98-drift-checks]   systemd preset roster is complete (no orphans, all units declared)"
}

# --- systemd units form complete dependency closure without missing targets ---
check_unit_dependency_closure() {
    echo "[98-drift-checks] systemd units form complete dependency closure without missing targets"
    if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
        echo "[98-drift-checks]   WARNING: python missing" >&2
        return 0
    fi
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py unit-dependency-closure)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   All systemd unit and Quadlet dependency references resolved cleanly"
}

# Documentation ratchet: see docs/design/doc-generative-documentation.md
# --- documentation coverage count meets or exceeds established ratchet floor ---
check_docs_ratchet() {
    echo "[98-drift-checks] documentation coverage count meets or exceeds established ratchet floor"
    local out bin; bin="$(_gate_bin)" || bin=""
    out="$(MIOS_DRIFT_ROOT="$ROOT" MIOS_GATE_BIN="$bin" python3 tools/drift-checks.py docs-ratchet)" || {
        _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   documentation ratchet holding (narrative + hint + stale-ref ceilings)"
}

# Ceilings must fall, never rise. Compared against HEAD.
# --- documentation ratchet ceilings never exceed their recorded floor (shrink-only) ---
check_docs_ratchet_monotone() {
    echo "[98-drift-checks] documentation ratchet ceilings never exceed their recorded floor (shrink-only)"
    local out; out="$(MIOS_DRIFT_ROOT="$ROOT" python3 "$ROOT/tools/check-docs.py" ratchet-monotone 2>&1)" || { _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   documentation ratchet ceilings did not rise"
}

# --- resolver output contains pure configuration without raw generated prose ---
check_no_generated_prose_in_resolvers() {
    echo "[98-drift-checks] resolver output contains pure configuration without raw generated prose"
    local out; out="$(cd "$ROOT" && MIOS_ROOT="$ROOT" python3 tools/check-docs.py no-generated-prose 2>&1)" || { _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# Derived doc sections must match SSOT: see docs/design/doc-generative-documentation.md
# --- generated manual chapters in docs match SSOT output verbatim ---
check_manual_generated() {
    echo "[98-drift-checks] generated manual chapters in docs match SSOT output verbatim"
    local out; out="$(cd "$ROOT" && MIOS_ROOT="$ROOT" python3 usr/libexec/mios/mios-manual             --root "$ROOT" render --check 2>&1)" || { _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- every pruned comment still lands in a doc ---
check_comment_landing() {
    echo "[98-drift-checks] every pruned comment still lands in a doc"
    local out; out="$(cd "$ROOT" && MIOS_ROOT="$ROOT" python3 usr/libexec/mios/mios-manual --root "$ROOT" landing --check 2>&1)" || { _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- the corpus ledger regenerates verbatim from the tracked tree ---
check_manual_ledger() {
    echo "[98-drift-checks] the corpus ledger regenerates verbatim from the tracked tree"
    local out; out="$(cd "$ROOT" && MIOS_ROOT="$ROOT" python3 usr/libexec/mios/mios-manual --root "$ROOT" ledger --check 2>&1)" || { _violations_from "" "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- no plain-text credential literals exist in tracked source tree ---
# --- no credential literal is baked into a systemd unit or Quadlet Environment= line (Law 11) ---
check_credential_literals() {
    # The tool scans Environment= lines under usr/lib/systemd/system and
    # usr/share/containers/systemd. It said "tracked source tree", which is
    # check_secret_handling's job, not this one.
    echo "[98-drift-checks] no credential literal is baked into a systemd unit or Quadlet Environment= line"
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_credential_literals could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    "$bin" credential-literals --root "$ROOT" || \
        _violation "a credential literal is baked into a world-readable systemd unit or Quadlet whose exact path:KEY=VALUE is not on the shrink-only register (Law 11)"
}

# Retain the registered gate name while the retired Python twin has no executable.
# Equivalence now means both committed artifacts match the canonical projection.
# --- both name-registry artifacts match the canonical read-only native projection ---
check_names_registry_equivalence() {
    check_names_registry
}
# --- every ref the Quadlet renderer leaves unbaked is actually supplied at runtime ---
check_protected_refs() {
    echo "[98-drift-checks] every bare \${MIOS_*} the Quadlet renderer leaves for systemd is supplied by the unit's own Environment= or by install.env"
    # The renderer decides WHETHER to protect a ref; nothing checked whether the
    # name can arrive. systemd expands an unset name to empty, so hollow
    # protection reads exactly like working indirection (task_3362). Scope comes
    # from [build.quadlet_render], the renderer's own table.
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_protected_refs could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    "$bin" protected-refs --root "$ROOT" || \
        _violation "a unit leaves a bare \${MIOS_*} for systemd to expand that no Environment= line and no install.env line supplies -- it resolves to the empty string at runtime"
}

# --- AGY-TASKS task descriptions conform strictly to task schema contract ---
check_task_schema() {
    echo "[98-drift-checks] AGY-TASKS task descriptions conform strictly to task schema contract"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-tasks.py schema 2>&1)" || { _violations_from "check_task_schema: " "$out"; return; }
    echo "[98-drift-checks]   every AGY task carries Verify/Do-NOT and resolvable deps"
}

# --- every drift check has a corresponding negative test registered ---
check_negatives_registered() {
    echo "[98-drift-checks] every drift check has a corresponding negative test registered"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-testhygiene.py negatives-registered 2>&1)" || { _violations_from "check_negatives_registered: " "$out"; return; }
    echo "[98-drift-checks]   no orphaned negative tests, and the untested-check count is within its ratchet"
}

# --- test suite cleans up all temporary fixtures and directories ---
check_temp_fixture_cleanup() {
    echo "[98-drift-checks] test suite cleans up all temporary fixtures and directories"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-testhygiene.py temp-fixture-cleanup 2>&1)" || { _violations_from "check_temp_fixture_cleanup: " "$out"; return; }
    echo "[98-drift-checks]   every temp-dir fixture is removed by the test that made it"
}

# --- every [variants] entry declares its required fields and names a table, edition, archetype, artifact and doc that exist ---
check_variant_registry() {
    echo "[98-drift-checks] every [variants] entry declares its required fields and names a table, edition, archetype, artifact and doc that exist"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-ssot.py variant-registry 2>&1)" || { _violations_from "check_variant_registry: " "$out"; return; }
    echo "[98-drift-checks]   every variant names a real table, edition, archetype, artifact and doc"
}

# --- deployment artifact target formats comply with bootc/BIB spec ---
check_deploy_formats() {
    echo "[98-drift-checks] deployment artifact target formats comply with bootc/BIB spec"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-ssot.py deploy-formats 2>&1)" || { _violations_from "check_deploy_formats: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- container image verification signatures and digests are valid ---
check_verify_images() {
    echo "[98-drift-checks] container image verification signatures and digests are valid"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-runtime.py verify-images 2>&1)" || { _violations_from "check_verify_images: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- file header comments strictly conform to comment parser syntax ---
check_header_comment_syntax() {
    echo "[98-drift-checks] file header comments strictly conform to comment parser syntax"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-docs.py header-syntax 2>&1)" || { _violations_from "check_header_comment_syntax: " "$out"; return; }
    echo "[98-drift-checks]   every AI header uses the comment character its format understands"
}

# --- Rust crate test coverage meets or exceeds minimum threshold ---
check_rust_test_coverage() {
    echo "[98-drift-checks] Rust crate test coverage meets or exceeds minimum threshold"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-testhygiene.py rust-test-coverage 2>&1)" || { _violations_from "check_rust_test_coverage: " "$out"; return; }
    echo "[98-drift-checks]   every Rust crate has a test or is a registered exception"
}

# --- generated manual pages compile cleanly and match CLI help surfaces ---
check_manpages() {
    echo "[98-drift-checks] generated manual pages compile cleanly and match CLI help surfaces"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" "$bin" render-manpages --root "$ROOT" --check --validate 2>&1)" || { _violations_from "check_manpages: " "$out"; return; }
    echo "[98-drift-checks]   usr/share/man matches the SSOT; man(1) reads it directly"
}

# --- all workflow CI jobs cover the required test matrix without gaps ---
check_ci_suite_coverage() {
    echo "[98-drift-checks] all workflow CI jobs cover the required test matrix without gaps"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/ci-suites.py --check 2>&1)" || { _violations_from "check_ci_suite_coverage: " "$out"; return; }
    echo "[98-drift-checks]   every tracked CI suite is registered or exempt"
}

# --- every tracked file is present and readable, so no corpus-scanning gate drops one in silence ---
check_tracked_readable() {
    echo "[98-drift-checks] every tracked file is present and readable, so no corpus-scanning gate drops one in silence"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-testhygiene.py tracked-readable 2>&1)" || { _violations_from "check_tracked_readable: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- no transient test fixtures or dump files are committed in git ---
check_leaked_fixtures() {
    echo "[98-drift-checks] no transient test fixtures or dump files are committed in git"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-testhygiene.py leaked-fixtures 2>&1)" || { _violations_from "check_leaked_fixtures: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- log sanitizer redacts all sensitive fields listed in security schema ---
check_redact_coverage() {
    echo "[98-drift-checks] log sanitizer redacts all sensitive fields listed in security schema"
    local out; out="$(cd "$ROOT" && MIOS_ROOT="$ROOT" python3 tools/check-docs.py redact-coverage 2>&1)" || { _violations_from "check_redact_coverage: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- daemon governor runtime limits and cgroup constraints are valid ---
check_daemon_governor() {
    echo "[98-drift-checks] daemon governor runtime limits and cgroup constraints are valid"
    local out; out="$(cd "$ROOT" && MIOS_ROOT="$ROOT" python3 tools/check-runtime.py daemon-governor 2>&1)" || { _violations_from "check_daemon_governor: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- all cross-references and internal links in manual docs resolve ---
check_manual_links() {
    echo "[98-drift-checks] all cross-references and internal links in manual docs resolve"
    local out; out="$(cd "$ROOT" && MIOS_ROOT="$ROOT" python3 tools/check-docs.py manual-links 2>&1)" || { _violations_from "check_manual_links: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- ADR architecture decision record index matches committed ADR files ---
check_adr_index() {
    echo "[98-drift-checks] ADR architecture decision record index matches committed ADR files"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" "$bin" adr-index --root "$ROOT" --check 2>&1)" || { _violations_from "check_adr_index: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- every SQL table declared in schema-init.sql has a reader or a writer, or a registered reason ---
check_schema_consumers() {
    echo "[98-drift-checks] every SQL table declared in schema-init.sql has a reader or a writer, or a registered reason"
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/check-testhygiene.py schema-consumers 2>&1)" || { _violations_from "check_schema_consumers: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

_run_py_check() {
    local name="$1" script="$2" pfx="${3:-$1: }" out
    echo "[98-drift-checks]   $name"
    out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 $script 2>&1)" || { _violations_from "$pfx" "$out"; return; }
    echo "[98-drift-checks]   $out"
}

check_tasks_status_parity() { _run_py_check check_tasks_status_parity "tools/check-tasks.py status-parity"; }
check_agy_tasks() { _run_py_check check_agy_tasks "tools/check-tasks.py agy"; }
check_mios_toml_integrity() { _run_py_check check_mios_toml_integrity "tools/check-ssot.py toml-integrity"; }
check_privileged_quadlets_minimal() { _run_py_check check_privileged_quadlets_minimal "tools/check-runtime.py privileged-quadlets"; }
check_container_names() { _run_py_check check_container_names "tools/check-runtime.py container-names"; }
check_service_urls() { _run_py_check check_service_urls "tools/check-runtime.py service-urls" ""; }
check_ports_bound() { _run_py_check check_ports_bound "tools/check-ssot.py ports-bound" ""; }
check_blade_coverage() { _run_py_check check_blade_coverage "tools/check-ssot.py blade-coverage" ""; }
check_fleet_safety() { _run_py_check check_fleet_safety "tools/check-ssot.py fleet-safety" ""; }
check_ssot_consumer_keys() { _run_py_check check_ssot_consumer_keys "tools/check-ssot.py consumer-keys" ""; }
check_unit_projection() { _run_py_check check_unit_projection "tools/check-ssot.py unit-projection" ""; }
check_metal_vs_hosted() {
    echo "[98-drift-checks]   check_metal_vs_hosted"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" "$bin" metal-vs-hosted --root "$ROOT" --check 2>&1)" || { _violations_from "check_metal_vs_hosted: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}
check_node_pool() { _run_py_check check_node_pool "tools/check-ssot.py node-pool" ""; }
check_port_fallbacks() { _run_py_check check_port_fallbacks "tools/check-ssot.py port-fallbacks" ""; }
check_role_ssot() { _run_py_check check_role_ssot "tools/check-ssot.py role-ssot" ""; }
check_blade_karg() { _run_deployment_projection blade-karg; }
check_firstboot_provisioners() { _run_py_check check_firstboot_provisioners "tools/check-runtime.py firstboot-provisioners"; }
check_desktop_launchers() {
    echo "[98-drift-checks]   checking desktop launchers match SSOT"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if ! out=$(cd "$ROOT" && "$bin" render-desktop --root "$ROOT" --check 2>&1); then
        printf '%s\n' "$out" | head -n 20 >&2
        _violation "desktop launchers drifted from SSOT (run 'just sync' or 'mios-gen render-desktop --root $ROOT')"
    fi
}
check_tmux_theme() {
    echo "[98-drift-checks]   checking tmux theme matches SSOT"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if ! out=$(cd "$ROOT" && "$bin" render-tmux-theme --root "$ROOT" --check 2>&1); then
        printf '%s\n' "$out" | head -n 20 >&2
        _violation "tmux theme drifted from SSOT (run 'just sync' or 'mios-gen render-tmux-theme --root $ROOT')"
    fi
}
check_btop_theme() {
    echo "[98-drift-checks]   checking btop theme matches SSOT"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    if ! out=$(cd "$ROOT" && "$bin" render-btop-theme --root "$ROOT" --check 2>&1); then
        printf '%s\n' "$out" | head -n 20 >&2
        _violation "btop theme drifted from SSOT (run 'just sync' or 'mios-gen render-btop-theme --root $ROOT')"
    fi
}
check_fastfetch() {
    echo "[98-drift-checks]   checking fastfetch config matches SSOT"
    local bin out; bin="$(native_bin mios-gen)" || { _violation "$_GEN_MISSING"; return; }
    # Real comparison: deterministic (--mock) render vs the persisted golden fixture.
    # A missing/corrupt fixture or a drifted generator both fail; nothing passes vacuously.
    if ! out=$(cd "$ROOT" && "$bin" render-fastfetch --root "$ROOT" --check --mock --out "$ROOT/tests/golden/fastfetch/mock.jsonc" 2>&1); then
        printf '%s\n' "$out" | head -n 20 >&2
        _violation "fastfetch generator output differs from tests/golden/fastfetch/mock.jsonc (regenerate: mios-gen render-fastfetch --mock --out tests/golden/fastfetch/mock.jsonc)"
    fi
}

check_no_inert_ssot_tables() {
    # Ported to mios-gate (ADR-0021, Law 14); python twin deleted (task_945).
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_no_inert_ssot_tables could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    local out
    if out="$("$bin" no-inert-ssot-tables --root "$ROOT" 2>&1)"; then
        echo "[98-drift-checks]   every mios.toml SSOT table has an access-shaped consumer or sits in the shrink-only [ssot_tables] register"
    else
        _violations_from "" "$out"
    fi
}

# --- mios.toml [profiles] is closed over phases, sections, the floor and targets (ADR-0025) ---
check_profile_integrity() {
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_profile_integrity could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    local out
    if out="$("$bin" profile-integrity --root "$ROOT" 2>&1)"; then
        echo "[98-drift-checks]   every [profiles] entry resolves over registered phases and real sections, contains the floor, and every target is declared"
    else
        _violations_from "" "$out"
    fi
}

# --- file paths referenced in documentation exist in the repository ---
check_doc_refs_resolve() {
    # Ported to mios-gate (ADR-0021, Law 14); python twin deleted.
    echo "[98-drift-checks] file paths referenced in documentation exist in the repository"
    local bin; bin="$(_gate_bin)" || bin=""
    if [[ -z "$bin" ]]; then
        _violation "mios-gate is not built, so check_doc_refs_resolve could not run -- build it: cd src/mios-rs && cargo build -p mios-gate"
        return
    fi
    local out
    if out="$("$bin" doc-refs-resolve --root "$ROOT" 2>&1)"; then
        # The binary's own line carries the measured count; a fixed sentence
        # here dropped it, so a clean pass could not be compared with anything.
        echo "[98-drift-checks]   ${out}"
    else
        _violations_from "check_doc_refs_resolve: " "$out"
    fi
}

# --- differential output between resolvers across platforms is zero ---
check_resolver_differential_parity() {
    echo "[98-drift-checks] differential output between resolvers across platforms is zero"
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py resolver-differential-parity 2>&1
)" || {
        _violations_from "check_resolver_differential_parity: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# --- generators avoid the non-portable fnmatch.fnmatch idiom (source check, nothing is rendered) ---
check_generator_host_parity() {
    # Nothing is rendered or compared here: it reads generator sources for one
    # portability idiom. The old wording promised byte-identical output.
    echo "[98-drift-checks] generators avoid the non-portable fnmatch.fnmatch idiom"
    local bin; bin="$(_gate_bin)" || {
        _violation "check_generator_host_parity requires native mios-gate"; return; }
    local out; out="$("$bin" generator-host-parity --root "$ROOT" 2>&1)" || {
        _violations_from "check_generator_host_parity: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

check_doc_port_scheme() {
    _run_py_check check_doc_port_scheme "tools/drift-checks.py doc-port-scheme"
}

# ADR-0017 D5 prerequisite: divergence needs per-row provenance to be mergeable.
# --- blade reconciliation schema conforms to hardware capability specs ---
check_blade_reconcile_schema() {
    echo "[98-drift-checks] blade reconciliation schema conforms to hardware capability specs"
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py blade-reconcile-schema 2>&1)" || {
        _violations_from "check_blade_reconcile_schema: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# Law 15 mirror: mios.toml [bootstrap.sync]. Authority is mios.git.
# --- bootstrap repository sync: shared files in MiOS-bootstrap match main repository SSOT ---
check_bootstrap_sync() {
    echo "[98-drift-checks] bootstrap repository sync: shared files in MiOS-bootstrap match main repository SSOT"
    local bin; bin="$(native_bin mios-gen "${MIOS_GEN_BIN:-}")" || {
        _violation "check_bootstrap_sync: native mios-gen is required; install the SSOT release catalog"; return; }
    local out; out="$("$bin" bootstrap-sync --root "$ROOT" --check 2>&1)" || {
        _violations_from "check_bootstrap_sync: " "$out"; return; }
    echo "[98-drift-checks]   $out"
}

# The repo is the deliverable; these floors only come down. ROADMAP.md explains why.
# --- code legibility and complexity metrics remain within ratchet thresholds ---
check_legibility_ratchet() {
    echo "[98-drift-checks] code legibility and complexity metrics remain within ratchet thresholds"
    # Every line this tool prints goes to stderr, so a breach reached
    # _violations_from empty. Capturing it means re-emitting the table.
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py legibility-ratchet 2>&1
    )" || {
        _violations_from "check_legibility_ratchet: " "$out"; return; }
    if [[ -n "$out" ]]; then
        echo "$out" >&2
    fi
    echo "[98-drift-checks]   legibility floors holding"
}

# --- the legacy archive holds every dropped file at its last content and mode ---
check_legacy_archive() { _run_py_check check_legacy_archive "tools/drift-checks.py legacy-archive"; }

check_header_integrity() {
    echo "[98-drift-checks] no AI-hint tagger has absorbed a shebang or a MIOS_* build directive from line 1"
    _need_python || return 0
    local out; out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py header-integrity 2>&1)" || {
        _violations_from "check_header_integrity: " "$out"; return; }
    echo "[98-drift-checks]   no absorbed shebangs or build directives in file headers"
}

main "$@"
