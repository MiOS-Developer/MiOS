#!/usr/bin/env bash
# AI-hint: Compatibility entry point; the complete ordered pipeline belongs to native mios-gen sync.
# AI-doc: usr/share/doc/mios/manual/tools.md
set -euo pipefail
ROOT="${MIOS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
_gen="${MIOS_GEN_BIN:-}"
if [[ -n "${MIOS_NATIVE_BIN_DIR:-}" ]]; then
    _suffix=''
    case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) _suffix='.exe';; esac
    _gen="$MIOS_NATIVE_BIN_DIR/mios-gen$_suffix"
elif [[ -z "$_gen" ]]; then
    _gen="$(command -v mios-gen || true)"
fi
if [[ -z "$_gen" || ! -x "$_gen" ]]; then
    printf '[mios-gen sync] required native tool mios-gen is missing; build the SSOT catalog\n' >&2
    exit 1
fi
exec "$_gen" sync --root "$ROOT" "$@"
