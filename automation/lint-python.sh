#!/usr/bin/env bash
# AI-hint: bash Python py_compile + undefined-name gate over EVERY tracked Python file in the repo (git ls-files, plus extensionless python-shebang entry points; ...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    if [ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" = "1" ]; then
        echo "[lint-python] ERROR: python3 is required under MIOS_DRIFT_REQUIRE_TOOLS=1" >&2
        exit 1
    fi
    echo "[lint-python] WARNING: python3 is missing" >&2
    exit 2
fi

files=()

# Ask GIT for the file set: a new Python payload is covered the moment it is
# tracked. Why not directory-by-directory: manual ch54.
if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    while IFS= read -r rel; do
        [ -f "${ROOT}/${rel}" ] && files+=("${ROOT}/${rel}")
    done < <(git -C "$ROOT" ls-files '*.py')

    # Extensionless entry points. Excludes templates ({{placeholders}}),
    # non-shebang "python" matches, and zipapps -- see manual ch54.
    while IFS= read -r rel; do
        case "$rel" in
            *.py|usr/share/mios/templates/*|tests/templates/*) continue ;;
        esac
        f="${ROOT}/${rel}"
        [ -f "$f" ] || continue
        # tr -d strips NULs so a binary file does not warn on substitution.
        head_line="$(head -c 200 "$f" 2>/dev/null | tr -d '\0' | head -n 1 || true)"
        case "$head_line" in
            '#!'*python*) ;;
            *) continue ;;
        esac
        if head -c 4096 "$f" 2>/dev/null | tail -c +2 | grep -qa 'PK\x03\x04' 2>/dev/null \
           || python3 -c 'import sys,zipfile; sys.exit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)' "$f" 2>/dev/null; then
            continue   # zipapp
        fi
        files+=("$f")
    done < <(git -C "$ROOT" ls-files)
else
    # No git (a bare unpacked tree): fall back to walking the payload dirs.
    for d in usr/lib/mios usr/share/mios usr/libexec/mios tools automation tests; do
        [ -d "${ROOT}/${d}" ] || continue
        while IFS= read -r f; do
            [ -f "$f" ] && files+=("$f")
        done < <(find "${ROOT}/${d}" -name "*.py")
    done
fi

# Coverage probe: tests/test-lint-coverage.sh re-derives the set with the
# gate's OWN logic, so the two can never disagree about what is covered.
if [ "${MIOS_LINT_PYTHON_LIST:-0}" = "1" ]; then
    printf '%s\n' "${files[@]}"
    exit 0
fi

if [ "${#files[@]}" -eq 0 ]; then
    echo "[lint-python] PASS: no Python files found to lint"
    exit 0
fi

echo "[lint-python] Compiling ${#files[@]} Python files with py_compile"
failed=0

for f in "${files[@]}"; do
    if ! python3 -c "import sys, ast; ast.parse(open(sys.argv[1], 'rb').read(), sys.argv[1])" "$f" >/dev/null 2>&1; then
        echo "[lint-python] ERROR: Python compilation failed for $f" >&2
        failed=$((failed + 1))
    fi
done

if [ "$failed" -gt 0 ]; then
    echo "[lint-python] FAIL: $failed Python file failed py_compile" >&2
    exit 1
fi

# Undefined-name pass. ast.parse above proves a file PARSES; it says nothing
# about whether the names it uses exist. Three undefined module-scope names sat
# in agent-pipe's server.py behind a clean parse, so the service could not
# import at all. pyflakes is the smallest tool that answers that question.
if command -v pyflakes >/dev/null 2>&1; then
    PYFLAKES=(pyflakes)
elif python3 -c 'import pyflakes' >/dev/null 2>&1; then
    PYFLAKES=(python3 -m pyflakes)
else
    PYFLAKES=()
fi

if [ "${#PYFLAKES[@]}" -eq 0 ]; then
    if [ "${MIOS_DRIFT_REQUIRE_TOOLS:-0}" = "1" ]; then
        echo "[lint-python] FAIL: pyflakes absent but MIOS_DRIFT_REQUIRE_TOOLS=1" >&2
        exit 1
    fi
    echo "[lint-python] SKIP: pyflakes absent -- undefined-name pass not run"
else
    echo "[lint-python] Undefined-name pass over ${#files[@]} Python files"
    undef=0
    for f in "${files[@]}"; do
        hits="$("${PYFLAKES[@]}" "$f" 2>/dev/null | grep -E 'undefined name' || true)"
        if [ -n "$hits" ]; then
            echo "$hits" >&2
            undef=$((undef + 1))
        fi
    done
    if [ "$undef" -gt 0 ]; then
        echo "[lint-python] FAIL: $undef Python file(s) reference undefined names" >&2
        exit 1
    fi
    echo "[lint-python] PASS: no undefined names"
fi

echo "[lint-python] PASS: all ${#files[@]} Python files compiled clean"
exit 0
