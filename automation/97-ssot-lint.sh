#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: SSOT-render conformance lint: a thin adapter that runs native mios-ssot-lint over the Quadlet ${MIOS_*} placeholders; a missing binary fails closed.
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MIOS_SSOT_LINT_ROOT="${MIOS_SSOT_LINT_ROOT:-$here}"
# Absolute paths, never `command -v` (task_962): MIOS_NATIVE_BIN_DIR, else this checkout's
# build, else the SSOT install and compat dirs of [build.native.categories.cli].
. "$here/automation/lib/globals.sh"; x=""; case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) x=.exe;; esac
IFS=, read -ra dirs <<< "${MIOS_NATIVE_BIN_DIR:-$here/tools/native/target/release,$here/tools/native/target/debug,$MIOS_BUILD_NATIVE_CATEGORIES_CLI_INSTALL_DIR,$MIOS_BUILD_NATIVE_CATEGORIES_CLI_COMPAT_DIRS}"
lint=""; for d in "${dirs[@]}"; do [[ -x "$d/mios-ssot-lint$x" ]] && { lint="$d/mios-ssot-lint$x"; break; }; done
[[ -n "$lint" ]] || { echo '[97-ssot-lint] required native mios-ssot-lint is unavailable' >&2; exit 2; }
exec "$lint"
