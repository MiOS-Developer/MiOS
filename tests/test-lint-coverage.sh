#!/usr/bin/env bash
# AI-hint: bash Coverage tests for automation/lint-python.sh and automation/lint-shell.sh.
# AI-doc: usr/share/doc/mios/manual/tests.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

# 1. Shell lint directory coverage: lint-shell.sh runs mios-gate shell-lint, which
#    reads its directories from [drift.lint].shell_directories.
LINT_SHELL="${ROOT}/automation/lint-shell.sh"
[[ -f "$LINT_SHELL" ]] || { echo "ERROR: $LINT_SHELL not found" >&2; exit 1; }
grep -q "shell-lint" "$LINT_SHELL" || { echo "ERROR: lint-shell.sh does not run mios-gate shell-lint" >&2; exit 1; }
mapfile -t shell_dirs < <(python3 -c 'import sys, tomllib; print("\n".join(tomllib.load(open(sys.argv[1], "rb"))["drift"]["lint"]["shell_directories"]))' "${ROOT}/usr/share/mios/mios.toml")

REQUIRED_DIRS=("automation" "tools" "installation" "tests" "usr/lib/mios" "usr/libexec/mios")
missing_dirs=()
for dir in "${REQUIRED_DIRS[@]}"; do
    printf '%s\n' "${shell_dirs[@]}" | grep -qx "$dir" || missing_dirs+=("$dir")
done
[[ ${#missing_dirs[@]} -eq 0 ]] || {
    echo "ERROR: lint-shell.sh missing coverage globs for: ${missing_dirs[*]}" >&2; exit 1;
}

# 2. Python lint payload area coverage
LINT_PY="${ROOT}/automation/lint-python.sh"
[[ -f "$LINT_PY" ]] || { echo "ERROR: $LINT_PY not found" >&2; exit 1; }

mapfile -t seen < <(MIOS_LINT_PYTHON_LIST=1 bash "$LINT_PY")
[[ ${#seen[@]} -gt 0 ]] || {
    echo "ERROR: lint-python.sh reported an EMPTY file set" >&2; exit 1;
}

contains() {
    local needle="$1" f
    for f in "${seen[@]}"; do
        [ "$f" = "${ROOT}/${needle}" ] && return 0
    done
    return 1
}

REQUIRED_FILES=(
    "usr/share/mios/owui/pipes/mios_agent_pipe.py"
    "usr/lib/mios/agent-pipe/server.py"
    "tools/check-runtime.py"
    "tools/check-module-length.py"
    "automation/validate-kargs.py"
    "tests/test-theme-merge.py"
    "usr/bin/mios"
)

missing_files=()
for rel in "${REQUIRED_FILES[@]}"; do
    [ -f "${ROOT}/${rel}" ] || continue
    contains "$rel" || missing_files+=("$rel")
done

[[ ${#missing_files[@]} -eq 0 ]] || {
    echo "ERROR: lint-python.sh missing coverage for: ${missing_files[*]}" >&2; exit 1;
}

EXCLUDED=(
    "usr/libexec/mios/mios-dashboard"
    "usr/share/mios/templates/python-tool"
    "tests/templates/golden.snap"
)
for rel in "${EXCLUDED[@]}"; do
    [ -f "${ROOT}/${rel}" ] || continue
    contains "$rel" && { echo "ERROR: lint-python.sh wrongly includes: $rel" >&2; exit 1; }
done

echo "[test-lint-coverage] PASS: All lint coverage assertions satisfied"
exit 0
