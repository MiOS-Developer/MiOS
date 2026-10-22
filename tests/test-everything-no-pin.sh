#!/usr/bin/env bash
# AI-hint: Two-sided test that mios-everything carries no pinned es.exe version: with a stub es.exe that cannot reach Everything, the unreachable message names the latest es.exe and never an SSOT version, and no [paths].everything_cli_version read remains; a mutant that re-reads the pin is caught.
# AI-related: usr/libexec/mios/mios-everything, usr/share/mios/mios.toml [paths]
set -euo pipefail
ROOT="${MIOS_TEST_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
printf '#!/bin/sh\necho "Error 8: IPC window not found" >&2\nexit 8\n' > "$T/es.exe"
printf '#!/bin/sh\n[ "$2" = everything_cli_version ] && echo 9.9.9.99\n' > "$T/mios-toml-get"
chmod +x "$T/es.exe" "$T/mios-toml-get"

check() {  # SCRIPT -> 0 when it carries no pin
    local s="$1" out
    out="$(EVERYTHING_CLI="$T/es.exe" bash "$s" --json probe 2>&1 || true)"
    grep -q '"error":"everything_unreachable"' <<<"$out" || { echo "no unreachable verdict: $out" >&2; return 2; }
    ! grep -q 'everything_cli_version' "$s" && ! grep -q '9\.9\.9\.99' <<<"$out" && grep -q 'latest es.exe' <<<"$out"
}
cp "$ROOT/usr/libexec/mios/mios-everything" "$T/mios-everything"
cp "$T/mios-toml-get" "$T/mios-toml-get.keep"
check "$T/mios-everything" || { echo "FAIL mios-everything still recommends a pinned es.exe" >&2; exit 1; }
echo "PASS unreachable message points at the latest es.exe, no SSOT version read"
python3 - "$T/mios-everything" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
s = s.replace('        _MSG="Everything UNREACHABLE', '        _ES_VER="$("$(cd "$(dirname "$0")" && pwd)/mios-toml-get" paths everything_cli_version "" 2>/dev/null)"\n        _MSG="(recommended: es.exe $_ES_VER) Everything UNREACHABLE', 1)
open(p, "w").write(s)
PY
if check "$T/mios-everything"; then echo "FAIL a re-pinned mutant passed" >&2; exit 1; fi
echo "PASS a mutant that reads everything_cli_version is caught"
