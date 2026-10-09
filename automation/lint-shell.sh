#!/usr/bin/env bash
# AI-hint: Runner for shellcheck across automation, tools, and libexec shell scripts. Degrades open if shellcheck is absent.
# AI-related: /usr/libexec/mios/mios-
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v shellcheck >/dev/null 2>&1; then
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y ShellCheck >/dev/null 2>&1 || true
    elif command -v apt-get >/dev/null 2>&1; then
        { sudo -n apt-get update -qq && sudo -n apt-get install -y shellcheck; } >/dev/null 2>&1 \
            || { apt-get update -qq && apt-get install -y shellcheck; } >/dev/null 2>&1 \
            || true
    fi
fi

if ! command -v shellcheck >/dev/null 2>&1; then
    echo "[lint-shell] WARNING: shellcheck is missing and could not be provisioned" >&2
    exit 2
fi

files=()

for f in "${ROOT}"/automation/*.sh "${ROOT}"/automation/tests/*.sh "${ROOT}"/automation/support/*.sh "${ROOT}"/automation/lib/*.sh; do
    if [ -f "$f" ]; then
        # Exclude generated globals.sh as its thousands of variables cause shellcheck to OOM
        if [[ "$(basename "$f")" == "globals.sh" ]]; then
            continue
        fi
        files+=("$f")
    fi
done

for f in "${ROOT}"/tools/*.sh "${ROOT}"/tools/lib/*.sh; do
    [ -f "$f" ] && files+=("$f")
done

for f in "${ROOT}"/installation/*.sh; do
    [ -f "$f" ] && files+=("$f")
done

for f in "${ROOT}"/tests/*.sh; do
    [ -f "$f" ] && files+=("$f")
done

for f in "${ROOT}"/usr/lib/mios/*.sh; do
    [ -f "$f" ] && files+=("$f")
done

for f in "${ROOT}"/usr/libexec/mios/mios-*; do
    if [ -f "$f" ]; then
        read -r first_line < "$f" || true
        if [[ "$first_line" =~ ^#\!.*(bash|sh) ]]; then
            files+=("$f")
        fi
    fi
done

if [ ${#files[@]} -eq 0 ]; then
    echo "[lint-shell] No shell scripts found to lint"
    exit 0
fi

echo "[lint-shell] Linting ${#files[@]} shell scripts at error level"
if ! shellcheck --severity=error "${files[@]}"; then
    echo "[lint-shell] FAIL: shellcheck found error-level issues in the repository" >&2
    exit 1
fi

# The warning-level pass is a ratchet: it lints what changed since a base.
# CI exports MIOS_RATCHET_BASE (the PR base, fetched beside a depth-1 checkout
# that has neither origin/main nor HEAD~1), so it wins; origin/main, then
# HEAD~1, serve a full clone. An explicit base that does not resolve is a
# misconfiguration, not a reason to lint nothing. Paths are repo-relative, so
# the pass runs from ROOT; a worktree's .git is a file, hence -e.
cd "$ROOT"
modified_files=()
git_ref=""
if [ -e ".git" ] && command -v git >/dev/null 2>&1; then
    if [ -n "${MIOS_RATCHET_BASE:-}" ]; then
        if ! git rev-parse --verify --quiet "${MIOS_RATCHET_BASE}^{commit}" >/dev/null; then
            echo "[lint-shell] FAIL: MIOS_RATCHET_BASE=${MIOS_RATCHET_BASE} does not name a commit in this checkout" >&2
            exit 1
        fi
        git_ref="$MIOS_RATCHET_BASE"
    elif git rev-parse --verify --quiet origin/main >/dev/null; then
        git_ref="origin/main"
    elif git rev-parse --verify --quiet HEAD~1 >/dev/null; then
        git_ref="HEAD~1"
    fi

    if [ -n "$git_ref" ]; then
        while IFS= read -r f; do
            if [ -f "$f" ]; then
                if [[ "$f" =~ \.sh$ ]]; then
                    if [[ "$(basename "$f")" != "globals.sh" ]]; then
                        modified_files+=("$f")
                    fi
                elif [[ "$f" =~ ^usr/libexec/mios/mios- ]]; then
                    read -r first_line < "$f" || true
                    if [[ "$first_line" =~ ^#\!.*(bash|sh) ]]; then
                        modified_files+=("$f")
                    fi
                fi
            fi
        done < <(git diff --name-only --diff-filter=ACMRT "$git_ref" 2>/dev/null || true)
    fi
fi

if [ -z "$git_ref" ]; then
    echo "[lint-shell] WARNING: no ratchet base (MIOS_RATCHET_BASE, origin/main or HEAD~1) in this tree; warning-level pass NOT run" >&2
elif [ ${#modified_files[@]} -gt 0 ]; then
    echo "[lint-shell] Linting ${#modified_files[@]} modified/new shell scripts at warning level (base ${git_ref})"
    if ! shellcheck --severity=warning "${modified_files[@]}"; then
        echo "[lint-shell] FAIL: shellcheck found warning-level or higher issues in modified files" >&2
        exit 1
    fi
else
    echo "[lint-shell] No modified shell scripts to lint at warning level (base ${git_ref})"
fi

if [ -z "$git_ref" ]; then
    echo "[lint-shell] PASS: shellcheck reports no error-level issues repo-wide (warning-level ratchet not run: no base)"
else
    echo "[lint-shell] PASS: shellcheck reports no error-level issues repo-wide and no warning-level issues in modified/new scripts"
fi
exit 0
