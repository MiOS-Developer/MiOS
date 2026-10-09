#!/usr/bin/env bash
# AI-hint: Runs mios-gate image-equivalence on a fixture staged from usr/ (--allow-tree-only): core floor clean, the devcontainer overlay only where selected, each refusal with its own exit code.
# AI-related: src/mios-rs/mios-gate/src/image_equivalence.rs, usr/share/mios/mios.toml, tools/drift-checks.py, tests/bake-smoke.sh
# AI-functions: main
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSOT="$ROOT/usr/share/mios/mios.toml"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/test-image-equivalence.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
fails=0
pass() { echo "  [PASS] $*"; }
fail() { echo "  [FAIL] $*"; fails=$((fails + 1)); }

gate_bin() {
    local c
    for c in "${MIOS_GATE_BIN:-}" \
             "$ROOT/src/mios-rs/target/release/mios-gate" \
             "$ROOT/src/mios-rs/target/debug/mios-gate" \
             /usr/libexec/mios/mios-gate; do
        [[ -n "$c" && -x "$c" ]] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

GATE="$(gate_bin)" || {
    (cd "$ROOT/src/mios-rs" && cargo build -q -p mios-gate) \
        || { echo "[test-image-equivalence] ERROR: mios-gate is not built and cannot be built" >&2; exit 1; }
    GATE="$ROOT/src/mios-rs/target/debug/mios-gate"
}

# The fixture is this checkout's usr/ tree, hard-linked where the filesystem
# allows (it is read, never written through), plus the build products a source
# tree cannot hold: the commands and paths the manifest names, as stubs.
FX="$TMP/root"
mkdir -p "$FX"
# Hard links when TMP shares the checkout's filesystem; otherwise a plain copy
# into a clean target (a failed cp -al leaves a partial usr/ behind).
cp -al "$ROOT/usr" "$FX/usr" 2>/dev/null || { rm -rf "${FX:?}/usr"; cp -a "$ROOT/usr" "$FX/usr"; }
python3 - "$SSOT" > "$TMP/stubs.txt" <<'PY'
import sys, tomllib
sc = tomllib.load(open(sys.argv[1], "rb"))["testing"]["smoke_components"]
tables = [sc] + [t for g in ("sections", "phases", "profiles") for t in sc.get(g, {}).values()]
for t in tables:
    for c in t.get("commands", []):
        print(f"usr/bin/{c}")
    for p in t.get("paths", []):
        print(p)
PY
while IFS= read -r stub; do
    [[ -n "$stub" ]] || continue
    mkdir -p "$FX/$(dirname "$stub")"
    rm -f "$FX/$stub"   # never write through a hard link into the checkout
    printf '#!/bin/sh\nexit 0\n' > "$FX/$stub"
    chmod 0755 "$FX/$stub"
done < "$TMP/stubs.txt"

# $1 = expected exit code, rest = gate arguments; output lands in $TMP/out.
run() {
    local want="$1" rc=0
    shift
    "$GATE" image-equivalence --root "$FX" --allow-tree-only "$@" > "$TMP/out" 2>&1 || rc=$?
    if [[ "$rc" -ne "$want" ]]; then
        echo "    exit $rc, wanted $want:"; sed 's/^/      /' "$TMP/out"
        return 1
    fi
}

main() {
    local n
    if run 0 --profile core && grep -q '^\[image-equivalence\] clean' "$TMP/out"; then
        n="$(grep -o 'floor [0-9]* probe' "$TMP/out" | grep -o '[0-9]*' || echo 0)"
        if (( n >= 24 )); then pass "core is clean with a floor of $n probe(s)"; else fail "core floor is $n, below 24"; fi
        if grep -q 'section:devcontainer' "$TMP/out"; then fail "core listed the devcontainer overlay"; else pass "core lists no devcontainer overlay"; fi
    else
        fail "core is not clean"
    fi

    # Every image kind, the devcontainer included, is the full profile.
    if run 0 --profile full && grep -q 'section:devcontainer ' "$TMP/out"; then
        pass "full lists the devcontainer overlay: $(grep -o 'overlays: [^;]*' "$TMP/out" | cut -c1-120)"
    else
        fail "full did not list the devcontainer overlay"
    fi

    if run 0 --profile core --format json && python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["status"] != "clean")' "$TMP/out"; then
        pass "--format json reports status clean"
    else
        fail "--format json did not report clean"
    fi

    # Negatives: each refusal must fail, with its own message.
    if "$GATE" image-equivalence --root "$FX" --profile core > "$TMP/out" 2>&1; then
        fail "a staged tree passed without --allow-tree-only"
    elif grep -q 'allow-tree-only' "$TMP/out"; then pass "a non-/ root without --allow-tree-only is refused"
    else fail "non-/ root refusal lacked its message"; fi

    if run 2 --profile zz-planted && grep -q 'profile "zz-planted" is not declared in \[profiles\]' "$TMP/out"; then
        pass "an undeclared profile exits 2, naming it"
    else fail "undeclared profile was not refused"; fi

    python3 - "$SSOT" "$TMP/empty.toml" "$TMP/low.toml" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
head, sep, tail = text.partition("[testing.smoke_components]\n")
nxt = re.search(r"^\[(?!testing\.smoke_components)", tail, re.M)
open(sys.argv[2], "w").write(head + sep + (tail[nxt.start():] if nxt else ""))
open(sys.argv[3], "w").write(re.sub(r"^min_smoke_components = \d+", "min_smoke_components = 23", text, flags=re.M))
PY
    if run 2 --profile core --ssot "$TMP/empty.toml" && grep -q 'no assertions in \[testing.smoke_components\]' "$TMP/out"; then
        pass "an emptied manifest exits 2"
    else fail "an emptied manifest was not refused"; fi

    if run 2 --profile core --ssot "$TMP/low.toml" && grep -q 'min_smoke_components 23 is below the floor 24 (grow-only)' "$TMP/out"; then
        pass "a lowered min_smoke_components exits 2"
    else fail "a lowered min_smoke_components was not refused"; fi

    rm -f "$FX/usr/libexec/mios/mios-doctor"
    if run 1 --profile core && grep -q 'floor: usr/libexec/mios/mios-doctor missing' "$TMP/out"; then
        pass "a missing floor shim exits 1, naming its origin"
    else fail "a missing floor shim was not reported"; fi

    echo "[test-image-equivalence] failures: $fails"
    (( fails == 0 ))
}

main "$@"
