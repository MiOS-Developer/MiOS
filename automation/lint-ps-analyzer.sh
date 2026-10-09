#!/usr/bin/env bash
# AI-hint: Thin adapter for the static mios-gate PowerShell check; missing tools fail closed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Absolute paths, never `command -v` (task_962): MIOS_NATIVE_BIN_DIR, else this checkout's
# build, else the SSOT install and compat dirs of mios-gate's [build.native.categories.cli].
. "$ROOT/automation/lib/globals.sh"; x=""; case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) x=.exe;; esac
IFS=, read -ra dirs <<< "${MIOS_NATIVE_BIN_DIR:-$ROOT/src/mios-rs/target/release,$ROOT/src/mios-rs/target/debug,$MIOS_BUILD_NATIVE_CATEGORIES_CLI_INSTALL_DIR,$MIOS_BUILD_NATIVE_CATEGORIES_CLI_COMPAT_DIRS}"
gate=""; for d in "${dirs[@]}"; do [[ -x "$d/mios-gate$x" ]] && { gate="$d/mios-gate$x"; break; }; done
[[ -n "$gate" ]] || { echo '[lint-ps-analyzer] required native mios-gate is unavailable' >&2; exit 2; }
exec "$gate" powershell-analyze --root "$ROOT"
