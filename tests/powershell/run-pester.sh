#!/usr/bin/env bash
# AI-hint: Runner for Pester tests with a native export fixture and explicit prerequisite failures.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PS_BIN=""
for candidate in pwsh powershell powershell.exe \
    /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
    /c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
    "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"; do
    if command -v "$candidate" >/dev/null 2>&1 || [ -f "$candidate" ]; then
        PS_BIN="$candidate"
        break
    fi
done

if [ -z "$PS_BIN" ]; then
    echo "[run-pester] ERROR: pwsh/powershell is required for the Pester test suite" >&2
    exit 1
fi

# Check if Pester module is available
HAS_PESTER=$("$PS_BIN" -NoProfile -NonInteractive -Command "
    if (Get-Module -ListAvailable -Name Pester | Where-Object { \$_.Version.Major -ge 5 }) {
        Write-Output 'YES'
    } else {
        try {
            Install-Module -Name Pester -MinimumVersion 5.0.0 -Scope CurrentUser -Force -SkipPublisherCheck -EA Stop
            Write-Output 'YES'
        } catch {
            Write-Output 'NO'
        }
    }
" 2>&1 || true)

if ! echo "$HAS_PESTER" | grep -q "YES"; then
    echo "[run-pester] ERROR: Pester 5+ could not be provisioned" >&2
    exit 1
fi

echo "[run-pester] Running Pester tests in tests/powershell..."

# The negative export controls launch a real native fixture, never Podman.
if [[ -z "${MIOS_TEST_PODMAN_BIN:-}" ]]; then
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            echo "[run-pester] ERROR: provide MIOS_TEST_PODMAN_BIN built inside MiOS-DEV" >&2
            exit 1 ;;
    esac
    command -v rustc >/dev/null || { echo "[run-pester] ERROR: rustc is required for the native export fixture" >&2; exit 1; }
    fixture_dir="$(mktemp -d -t mios-pester.XXXXXX)"
    trap 'rm -rf -- "$fixture_dir"' EXIT
    rustc "$SCRIPT_DIR/mock_podman.rs" -o "$fixture_dir/podman"
    export MIOS_TEST_PODMAN_BIN="$fixture_dir/podman"
fi
[[ -x "$MIOS_TEST_PODMAN_BIN" ]] || { echo "[run-pester] ERROR: native export fixture is not executable" >&2; exit 1; }
export MIOS_BOOTSTRAP_ROOT="${MIOS_BOOTSTRAP_ROOT:-$ROOT/../mios-bootstrap}"
[[ -f "$MIOS_BOOTSTRAP_ROOT/build-mios.ps1" ]] || { echo "[run-pester] ERROR: set MIOS_BOOTSTRAP_ROOT to the bootstrap checkout" >&2; exit 1; }

win_test_dir="$ROOT/tests/powershell"
if [[ "$PS_BIN" == *.exe ]]; then
    if [[ "$win_test_dir" =~ ^/mnt/c/ ]]; then
        win_test_dir="C:/${win_test_dir#/mnt/c/}"
    elif [[ "$win_test_dir" =~ ^/c/ ]]; then
        win_test_dir="C:/${win_test_dir#/c/}"
    fi
fi

OUT=$("$PS_BIN" -NoProfile -NonInteractive -Command "
    # Pester 5 is the target: CI ships it, and its assertion syntax
    # (Should -Be) is incompatible with the Pester 3.4 that Windows PowerShell
    # bundles. Binding 'whatever is installed' silently ran the suite under v3
    # locally and v5 in CI, so the same file could not pass both.
    Import-Module Pester -MinimumVersion 5.0.0 -ErrorAction SilentlyContinue
    \$pesterVer = (Get-Module Pester | Sort-Object Version -Descending | Select-Object -First 1).Version
    if (-not \$pesterVer -or \$pesterVer.Major -lt 5) {
        Write-Output (\"PESTER_FAIL: Pester 5+ required, found '\" + \$pesterVer + \"'\")
        exit 0
    }
    \$testFiles = Get-ChildItem -Path '${win_test_dir}' -Filter '*.Tests.ps1' -Recurse
    if (-not \$testFiles) {
        Write-Output 'PESTER_FAIL: no test files discovered'
        exit 0
    }
    try {
        \$config = [PesterConfiguration]::Default
        \$config.Run.Path = '${win_test_dir}'
        # PassThru is REQUIRED. Without it Invoke-Pester returns nothing, so
        # \$result is \$null, \$null.FailedCount is \$null, and (\$null -gt 0) is
        # \$false -- the runner printed PESTER_PASS on a run with 13 failures.
        \$config.Run.PassThru = \$true
        \$config.Output.Verbosity = 'Normal'
        \$result = Invoke-Pester -Configuration \$config
        if (\$null -eq \$result) {
            Write-Output 'PESTER_FAIL: Invoke-Pester returned no result object'
        } elseif (\$result.FailedCount -gt 0 -or \$result.FailedContainersCount -gt 0) {
            Write-Output (\"PESTER_FAIL: \" + \$result.FailedCount + \" test(s), \" + \$result.FailedContainersCount + \" container(s) failed\")
        } elseif (\$result.PassedCount -eq 0) {
            Write-Output 'PESTER_FAIL: no tests ran (discovery produced nothing)'
        } else {
            Write-Output ('PESTER_PASS: ' + \$result.PassedCount + ' test(s) passed')
        }
    } catch {
        \$res = Invoke-Pester -Path '${win_test_dir}' -PassThru -ErrorAction SilentlyContinue
        if (\$null -eq \$res -or \$res.FailedCount -gt 0 -or \$res.PassedCount -eq 0) {
            Write-Output ('PESTER_FAIL: fallback run -- ' + \$res.FailedCount + ' failed, ' + \$res.PassedCount + ' passed')
        } else {
            Write-Output ('PESTER_PASS: ' + \$res.PassedCount + ' test(s) passed')
        }
    }
" 2>&1 || true)

if echo "$OUT" | grep -q "PESTER_FAIL"; then
    echo "$OUT" >&2
    echo "[run-pester] FAIL: Pester test suite failed" >&2
    exit 1
fi

# Require the PASS marker rather than inferring it from the absence of FAIL.
# `pwsh ... || true` swallows the exit code, so a crash, a missing Pester
# module or a discovery abort produced neither marker and fell through to the
# success path.
if ! echo "$OUT" | grep -q "PESTER_PASS"; then
    echo "$OUT" >&2
    echo "[run-pester] FAIL: Pester produced no PASS marker (crash / discovery abort?)" >&2
    exit 1
fi

echo "$OUT"
echo "[run-pester] PASS: Pester test suite passed"
exit 0
