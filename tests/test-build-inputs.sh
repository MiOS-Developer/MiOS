#!/usr/bin/env bash
# AI-hint: Verifies font archive integrity and shared-base phase prerequisites, plus narrow signing-input exports, without building images or downloading assets.
# AI-related: automation/56-fonts.sh, usr/share/mios/base/Containerfile, tools/native/mios-resolver/src/emit.rs
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mios_err() { echo "$*" >&2; }
# shellcheck source=/dev/null  # one function excerpted from automation/56-fonts.sh at run time
source <(sed -n '/^_install_geist_nerd_font() {/,/^}/p' "$ROOT/automation/56-fonts.sh")
readarray -t inputs < <(python3 - "$ROOT" <<'PY'
import pathlib, sys, tomllib
root = pathlib.Path(sys.argv[1])
config = tomllib.loads((root / 'usr/share/mios/mios.toml').read_text())
font = config['theme']['font']
phases = [p['name'] for p in config['build']['phases']['list']]
assert phases.index('native-build') < phases.index('bake-coderun-sandbox')
assert phases.index('fonts') < phases.index('bake-coderun-sandbox')
sys.path.insert(0, str(root / 'usr/lib/mios'))
import mios_toml
exports = mios_toml.emit_exports(config)
for key in ('cosign_version', 'cosign_release_url'):
    assert exports['MIOS_SECURITY_SIGSTORE_' + key.upper()] == config['security']['sigstore'][key]
print(root / font['native_archive'])
print(font['native_sha256'])
PY
)
[[ ${#inputs[@]} == 2 ]]
_install_geist_nerd_font "${inputs[0]}" "${inputs[1]}" "$TMP/good"
[[ $(find "$TMP/good" \( -name '*.ttf' -o -name '*.otf' \) | wc -l) -gt 0 ]]
echo 'PASS actual SSOT font archive checksum and font extraction'
if _install_geist_nerd_font "${inputs[0]}" "$(printf '0%.0s' {1..64})" "$TMP/bad" > "$TMP/bad.log" 2>&1; then
    echo 'FAIL corrupted font checksum accepted' >&2; exit 1
fi
grep -q FAILED "$TMP/bad.log"
[[ ! -e "$TMP/bad" ]]
echo 'PASS checksum mismatch prevents font publication'
python3 - "$TMP/empty.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'w') as archive:
    archive.writestr('README', 'no font payload')
PY
empty_sha="$(sha256sum "$TMP/empty.zip" | cut -d ' ' -f 1)"
if _install_geist_nerd_font "$TMP/empty.zip" "$empty_sha" "$TMP/empty" > "$TMP/empty.log" 2>&1; then
    echo 'FAIL fontless archive accepted' >&2; exit 1
fi
grep -q 'filename not matched' "$TMP/empty.log"
echo 'PASS correctly hashed archive without fonts is rejected'
echo 'PASS shared-base ordering and Python signing-input export checks executed'
