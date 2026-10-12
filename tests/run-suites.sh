#!/usr/bin/env bash
# AI-hint: Runs one registered suite tier to completion and reports every failure, so a single run says everything that is wrong.
# AI-related: tools/ci-suites.py, usr/share/mios/mios.toml
#
# Sequential steps stop at the first failure, so each red run taught exactly one
# thing and the next failure cost another round trip. This runs the whole tier
# and reports all of it. Suites come from [ci] in the SSOT, which is also what
# check_ci_suite_coverage enforces, so a publisher cannot quietly run a
# different set from its sibling.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TIER="${1:-}"

mios_resolve_python() {
    if [[ -n "${MIOS_PYTHON_BIN:-}" && -x "${MIOS_PYTHON_BIN}" ]]; then
        echo "${MIOS_PYTHON_BIN}"
        return 0
    fi
    local venv_py="${MIOS_AI_AGENT_VENV:-/usr/lib/mios/agents/.venv}/bin/python3"
    if [[ -x "$venv_py" ]]; then
        echo "$venv_py"
        return 0
    fi
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        if [[ -x "${VIRTUAL_ENV}/bin/python3" ]]; then
            echo "${VIRTUAL_ENV}/bin/python3"
            return 0
        elif [[ -x "${VIRTUAL_ENV}/bin/python" ]]; then
            echo "${VIRTUAL_ENV}/bin/python"
            return 0
        elif [[ -x "${VIRTUAL_ENV}/Scripts/python.exe" ]]; then
            echo "${VIRTUAL_ENV}/Scripts/python.exe"
            return 0
        fi
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return 0
    fi
    echo "python3"
}

# Scrub ambient MIOS_* environment variables to prevent host leakage
mios_scrub_env() {
    local preserve_regex='^(MIOS_DRIFT_ROOT|MIOS_DRIFT_CHECK_ROOT|MIOS_DRIFT_REQUIRE_TOOLS|MIOS_RATCHET_BASE|MIOS_NATIVE_BIN_DIR|MIOS_THEME_ROOT|MIOS_TOML_ROOT|MIOS_VENDOR_TOML|MIOS_AI_AGENT_VENV|MIOS_PYTHON_BIN|MIOS_BOOTSTRAP_ROOT|MIOS_TEST_PODMAN_BIN)$'
    for var in $(compgen -v MIOS_); do
        if [[ ! "$var" =~ $preserve_regex ]]; then
            unset "$var"
        fi
    done
}

PYTHON_BIN="$(mios_resolve_python)"
mios_scrub_env

if [[ -z "$TIER" ]]; then
    echo "usage: run-suites.sh <tier>   (tiers: $("$PYTHON_BIN" "${ROOT}/tools/ci-suites.py" --tier '' 2>&1 | sed -n 's/^unknown tier.*//p'))" >&2
    echo "       registered tiers are the keys of [ci.tiers] in usr/share/mios/mios.toml" >&2
    exit 2
fi

mapfile -t SUITES < <(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" "$PYTHON_BIN" tools/ci-suites.py --tier "$TIER" | tr -d '\r')
if [[ ${#SUITES[@]} -eq 0 ]]; then
    # An empty tier is a runner that reports success having done nothing, which
    # is the failure mode this whole registry exists to prevent.
    echo "::error::tier '${TIER}' resolved to zero suites" >&2
    exit 1
fi

# The registry, the skip reasons and the [ci].max_tool_skips ceiling are checked on every tier.
# Its findings go to stderr: discarding them left CI with "--check failed" and no reason.
(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" "$PYTHON_BIN" tools/ci-suites.py --check >&2) || { echo "[run-suites] tools/ci-suites.py --check failed" >&2; exit 1; }
mapfile -t TOOL_SKIPS < <(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" "$PYTHON_BIN" tools/ci-suites.py --tool-skips | tr -d '\r')
SUITE_TIMEOUT="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" "$PYTHON_BIN" tools/ci-suites.py --suite-timeout | tr -d '\r')" \
    || { echo "[run-suites] [ci].suite_timeout_s unresolved" >&2; exit 1; }
echo "[run-suites] tier=${TIER} suites=${#SUITES[@]} root=${ROOT}"
FAILED=()
SKIPPED=()
PASSED=0
for entry in "${SUITES[@]}"; do
    entry="${entry%$'\r'}"
    runner="${entry%%	*}"
    runner="${runner%$'\r'}"
    path="${entry#*	}"
    path="${path%$'\r'}"
    [[ -n "$path" ]] || continue
    if [[ "$runner" == "python3" ]]; then
        runner="$PYTHON_BIN"
    fi
    start=$SECONDS
    if out="$(cd "$ROOT" && MIOS_DRIFT_ROOT="$ROOT" MIOS_DRIFT_CHECK_ROOT="$ROOT" \
              MIOS_THEME_ROOT="$ROOT" MIOS_TOML_ROOT="$ROOT" \
              timeout --kill-after=10s "${SUITE_TIMEOUT}s" "$runner" "$path" 2>&1)"; then
        PASSED=$((PASSED + 1))
        echo "::group::[ OK ] ${path} ($((SECONDS - start))s)"
        printf '%s\n' "$out"
        echo "::endgroup::"
    else
        rc=$?
        # Exit 77 is a skip only for a suite registered in [ci.tool_skips]; anywhere else it is a failure.
        if [[ $rc -eq 77 ]] && printf '%s\n' "${TOOL_SKIPS[@]}" | grep -qxF -- "$path"; then
            SKIPPED+=("$path")
            echo "[SKIP] ${path} ($((SECONDS - start))s)"
            printf '%s\n' "$out"
            echo "::warning file=${path}::${path} skipped: a host tool it needs is absent ([ci.tool_skips])"
            continue
        fi
        FAILED+=("$path")
        # timeout(1) signals the suite's whole process group, so an orphan that
        # held the output pipe dies with it instead of wedging the tier.
        if [[ $rc -eq 124 || $rc -eq 137 ]]; then
            echo "[FAIL] ${path} (timed out after ${SUITE_TIMEOUT}s, [ci].suite_timeout_s)"
            printf '%s\n' "$out"
            echo "::error file=${path}::${path} timed out after ${SUITE_TIMEOUT}s"
            continue
        fi
        echo "[FAIL] ${path} (exit ${rc}, $((SECONDS - start))s)"
        printf '%s\n' "$out"
        echo "::error file=${path}::${path} failed with exit ${rc}"
    fi
done

echo "[run-suites] tier=${TIER}: ${PASSED} passed, ${#FAILED[@]} failed, ${#SKIPPED[@]} skipped"
[[ ${#SKIPPED[@]} -eq 0 ]] || printf '[run-suites]   skipped: %s\n' "${SKIPPED[@]}"
if [[ ${#FAILED[@]} -gt 0 ]]; then
    printf '[run-suites]   failed: %s\n' "${FAILED[@]}" >&2
    exit 1
fi
