#!/usr/bin/env bash
# AI-hint: Positive and negative controls for the off-image MiOS-Portal builder (SSOT inputs, heavy.lock, artifact-lane policy, copy-patch check, resolver and signature checks) without building Gecko.
# AI-related: usr/libexec/mios/mios-portal-build, usr/share/mios/portal-build/mios-portal-build-inner, usr/lib/mios/heavy-lock.sh, usr/share/mios/mios.toml ([desktop.portal.builder])
# Network checks (live product-details / npm / archive.mozilla.org) run only with MIOS_TEST_NETWORK=1.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAP="$ROOT/usr/libexec/mios/mios-portal-build"
INNER="$ROOT/usr/share/mios/portal-build/mios-portal-build-inner"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fails=0
ok()   { echo "PASS $*"; }
bad()  { echo "FAIL $*"; fails=$((fails + 1)); }
want() { local exp=$1 name=$2; shift 2; local rc=0; "$@" >"$TMP/out" 2>&1 || rc=$?
         if [ "$exp" = 0 ] && [ "$rc" = 0 ]; then ok "$name"
         elif [ "$exp" != 0 ] && [ "$rc" != 0 ]; then ok "$name (rc=$rc)"
         else bad "$name (rc=$rc)"; sed 's/^/    /' "$TMP/out"; fi; }
export MIOS_HOST_TOML=/nonexistent MIOS_USER_TOML=/nonexistent

# --- SSOT table -----------------------------------------------------------------------------
want 0 "ssot: [desktop.portal.builder] invariants" python3 -I - "$ROOT/usr/share/mios/mios.toml" <<'PY'
import re, sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
b = d["desktop"]["portal"]["builder"]
assert b["lane"] in ("artifact", "full"), b["lane"]
assert 1 <= b["jobs"] <= 6, "jobs must stay <= 6"
assert b["scope_swap_max"] == "0", "the scope must not swap"
assert b["scope_memory_max"].endswith("G") and int(b["scope_memory_max"][:-1]) <= 13
assert b["container_memory"] == b["container_memory_swap"], "container swap must equal memory (no swap)"
assert b["min_free_gb"] >= 40
assert re.fullmatch(r"[0-9A-F]{40}", b["release_key_fingerprint"])
assert b["base_image"].endswith(":latest"), "builder base floats (no pins)"
flat = repr({k: v for k, v in b.items() if k != "release_key_fingerprint"})
assert not re.search(r"\b1[0-9]{2}\.[0-9]+(esr)?\b", flat), "a Firefox version literal is in the builder table"
assert b["packages"] and len(set(b["packages"])) == len(b["packages"]), "package list empty or duplicated"
assert b["artifact_app_name"], "the artifact lane needs the app name Mozilla's artifact package ships"
PY
want 0 "wrapper: config resolves every input" "$WRAP" config
grep -v '^release_key_fingerprint' "$ROOT/usr/share/mios/mios.toml" >"$TMP/nokey.toml"
want 1 "wrapper: a missing SSOT key stops it" env MIOS_VENDOR_TOML="$TMP/nokey.toml" "$WRAP" config
sed '/^\[desktop.portal.builder\]/,/^\[/ s/^jobs = [0-9]*/jobs = 12/' "$ROOT/usr/share/mios/mios.toml" >"$TMP/jobs.toml"
want 1 "wrapper: jobs above 6 is refused" env MIOS_VENDOR_TOML="$TMP/jobs.toml" "$WRAP" config
sed '/^\[desktop.portal.builder\]/,/^\[/ s/^lane = .*/lane = "turbo"/' "$ROOT/usr/share/mios/mios.toml" >"$TMP/lane.toml"
want 1 "wrapper: an unknown lane is refused" env MIOS_VENDOR_TOML="$TMP/lane.toml" "$WRAP" config

# --- heavy.lock -----------------------------------------------------------------------------
# shellcheck source=/usr/lib/mios/heavy-lock.sh
. "$ROOT/usr/lib/mios/heavy-lock.sh"
L="$TMP/heavy.lock"
want 0 "heavy-lock: runs the command under the lock" mios_heavy_lock_exec "$L" true
( mios_heavy_lock_exec "$L" sleep 4 ) & holder=$!
sleep 0.5
rc=0; MIOS_HEAVY_LOCK_WAIT=1 mios_heavy_lock_exec "$L" true 2>/dev/null || rc=$?
[ "$rc" = 75 ] && ok "heavy-lock: a second heavy job does not start beside the first (75)" || bad "heavy-lock: concurrent job rc=$rc, want 75"
s=$SECONDS; mios_heavy_lock_exec "$L" true 2>/dev/null; waited=$((SECONDS - s))
[ "$waited" -ge 2 ] && ok "heavy-lock: an unbounded wait blocks until release (${waited}s)" || bad "heavy-lock: did not block (${waited}s)"
wait "$holder" 2>/dev/null
want 1 "heavy-lock: no lock path is an error" env -u MIOS_HEAVY_LOCK bash -c ". '$ROOT/usr/lib/mios/heavy-lock.sh'; mios_heavy_lock_exec ''"

# --- artifact-lane policy -------------------------------------------------------------------
mkproj() { rm -rf "$TMP/p"; mkdir -p "$TMP/p/src/browser/themes" "$TMP/p/configs/common"; : >"$TMP/p/mozconfig"; : >"$TMP/p/configs/common/mozconfig"; }
patch_for() { printf -- '--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-a\n+b\n' "$1" "$1" >"$TMP/p/src/$2"; }
mkproj; patch_for browser/themes/shared/browser-shared.css browser/themes/css.patch
want 0 "policy: a CSS patch passes the artifact lane" bash "$INNER" policy-check "$TMP/p" artifact
patch_for dom/base/Element.cpp browser/cpp.patch
want 1 "policy: a C++ patch is refused in the artifact lane" bash "$INNER" policy-check "$TMP/p" artifact
want 0 "policy: the same C++ patch is allowed in the full lane" bash "$INNER" policy-check "$TMP/p" full
mkproj; echo 'fn main() {}' >"$TMP/p/src/browser/lib.rs"
want 1 "policy: a Rust copy-patch is refused in the artifact lane" bash "$INNER" policy-check "$TMP/p" artifact
mkproj; echo 'ac_add_options --enable-lto=cross,full' >"$TMP/p/configs/common/mozconfig"
want 1 "policy: LTO in a mozconfig is refused (artifact)" bash "$INNER" policy-check "$TMP/p" artifact
want 1 "policy: LTO in a mozconfig is refused (full)" bash "$INNER" policy-check "$TMP/p" full
mkproj; echo 'export MOZ_PGO=1' >"$TMP/p/mozconfig"
want 1 "policy: PGO in a mozconfig is refused" bash "$INNER" policy-check "$TMP/p" full

# --- copy-patch check -----------------------------------------------------------------------
mkproj; mkdir -p "$TMP/p/src/browser/branding/x/pref" "$TMP/p/engine/browser/branding/x/pref" "$TMP/p/.surfer"
echo 'pref("a", 1);' >"$TMP/p/src/browser/branding/x/pref/firefox-branding.js"
echo 0 >"$TMP/p/.surfer/patchCount"
ln -s "$TMP/p/src/browser/branding/x/pref/firefox-branding.js" "$TMP/p/engine/browser/branding/x/pref/firefox-branding.js"
want 0 "copy-patch: a linked copy-patch passes" bash "$INNER" copy-patch-check "$TMP/p"
rm "$TMP/p/engine/browser/branding/x/pref/firefox-branding.js"
cp "$TMP/p/src/browser/branding/x/pref/firefox-branding.js" "$TMP/p/engine/browser/branding/x/pref/"
want 1 "copy-patch: a plain file where the link should be fails" bash "$INNER" copy-patch-check "$TMP/p"
rm "$TMP/p/engine/browser/branding/x/pref/firefox-branding.js"
want 1 "copy-patch: a missing copy-patch fails" bash "$INNER" copy-patch-check "$TMP/p"
ln -s "$TMP/p/src/browser/branding/x/pref/firefox-branding.js" "$TMP/p/engine/browser/branding/x/pref/firefox-branding.js"
patch_for browser/themes/shared/a.css browser/themes/a.patch
want 1 "copy-patch: Surfer's patch count disagreeing with src/ fails" bash "$INNER" copy-patch-check "$TMP/p"

# --- resolver (offline negatives) -----------------------------------------------------------
eval "$("$WRAP" config)"
export PB_ARCHIVE PB_NPM PB_SURFER_PKG PB_KEY_FPR
want 1 "resolve: an unreachable product-details fails (no fallback)" env PB_PRODUCT_DETAILS=https://127.0.0.1:9 bash "$INNER" resolve
want 1 "resolve: an unset input fails" env -u PB_NPM PB_PRODUCT_DETAILS="$PB_PRODUCT_DETAILS" bash "$INNER" resolve
want 1 "inner: an unknown mode is refused" bash "$INNER" bogus

# --- live upstream (opt-in) -----------------------------------------------------------------
if [ "${MIOS_TEST_NETWORK:-}" = 1 ]; then
    export PB_PRODUCT_DETAILS PB_CACHE="$TMP/cache"
    want 0 "resolve: latest Firefox/Surfer/revision resolve" bash "$INNER" resolve
    pd=$(curl -fsS "$PB_PRODUCT_DETAILS/firefox_versions.json" | python3 -I -c 'import json,sys;print(json.load(sys.stdin)["LATEST_FIREFOX_VERSION"])')
    got=$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["firefox"])' "$TMP/out")
    [ "$pd" = "$got" ] && ok "resolve: Firefox equals product-details ($got)" || bad "resolve: $got != product-details $pd"
    want 0 "resolve-env: emits the mios-portal-tree stage vocabulary" bash "$INNER" resolve-env
    grep -qE '^firefox_version=[0-9]+\.[0-9]+' "$TMP/out" && grep -qE '^firefox_revision=[0-9a-f]{40}$' "$TMP/out" \
        && grep -qE '^surfer_version=[0-9]+\.[0-9]+\.[0-9]+$' "$TMP/out" \
        && ok "resolve-env: firefox_version, firefox_revision and surfer_version present" || bad "resolve-env: keys missing"
    want 0 "verify-sums: SHA512SUMS is signed by the SSOT trust anchor" bash "$INNER" verify-sums
    want 1 "verify-sums: a different trust anchor is refused" env PB_KEY_FPR=0000000000000000000000000000000000000000 bash "$INNER" verify-sums
    want 1 "resolve: an unknown npm package fails" env PB_SURFER_PKG=@zen-browser/surfer-does-not-exist bash "$INNER" resolve
else
    echo "SKIP live upstream checks (set MIOS_TEST_NETWORK=1)"
fi

[ "$fails" = 0 ] && echo "ALL PASS" || { echo "$fails FAILED"; exit 1; }
