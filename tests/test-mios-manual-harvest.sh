#!/usr/bin/env bash
# AI-hint: bash Round-trips the documentation harvest loop in a throwaway git fixture -- ledger, harvest, the landed() predicate flipping false to true, p...
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"
CLI="$ROOT/usr/libexec/mios/mios-manual"

log() { printf '[test-mios-manual-harvest] %s\n' "$*"; }
die() { printf '[test-mios-manual-harvest] ERROR: %s\n' "$*" >&2; exit 1; }

[ -r "$CLI" ] || die "missing $CLI"
command -v git >/dev/null 2>&1 || die "git required"

fix="$(mktemp -d)"
trap 'rm -rf "$fix"' EXIT

mkdir -p "$fix/usr/share/mios/reference" "$fix/automation" "$fix/usr/share/doc/mios"
cp "$ROOT/usr/share/mios/mios.toml" "$fix/usr/share/mios/mios.toml"

# A comment fat enough to classify MIGRATE (over both the line and word floors).
cat > "$fix/automation/50-example.sh" <<'EOF'
#!/usr/bin/env bash
# AI-hint: Example build phase used only by the harvest round-trip test fixture.
# AI-related: none
set -euo pipefail

# The reason this phase exists at all is a long and genuinely narrative account
# of a historical incident, written as prose rather than as a terse note, so it
# comfortably exceeds both the line floor and the word floor that the comment
# classifier applies when deciding that a block belongs in documentation rather
# than in source. It rambles deliberately, because the fixture needs a block the
# classifier will mark for migration every single time it is lexed, regardless
# of tuning changes to the surrounding thresholds in the shipped policy tables.
echo "phase"
EOF

( cd "$fix" && git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -qm f )

run() { MIOS_ROOT="$fix" python3 "$CLI" --root "$fix" "$@"; }

log "Case 1: ledger records the block as MIGRATE"
run ledger --write >/dev/null
sha="$(awk -F'\t' '$1=="automation/50-example.sh" && $7=="MIGRATE" {print $6; exit}' \
    "$fix/usr/share/mios/reference/manual-corpus.tsv")"
[ -n "$sha" ] || die "case 1: fixture comment did not classify MIGRATE"
log "  block $sha"

log "Case 2: prune REFUSES a block that has not landed anywhere"
if run prune --path automation/50-example.sh >/dev/null 2>&1; then
    die "case 2: prune accepted an unharvested block -- that is data loss"
fi
grep -q "long and genuinely narrative" "$fix/automation/50-example.sh" \
    || die "case 2: refused prune still modified the source"

narrative() {
    run coverage --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["unmigrated_narrative"])'
}

log "Case 3: harvest writes the passage, the anchor and the ledger columns"
before="$(narrative)"
run harvest --path automation/50-example.sh --to usr/share/doc/mios/harvested.md >/dev/null
doc="$fix/usr/share/doc/mios/harvested.md"
[ -f "$doc" ] || die "case 3: destination doc not created"
grep -q "mios-src:$sha" "$doc" || die "case 3: anchor missing from the doc"
grep -q "long and genuinely narrative" "$doc" || die "case 3: prose missing from the doc"
landed_doc="$(awk -F'\t' -v s="$sha" '$6==s {print $11}' \
    "$fix/usr/share/mios/reference/manual-corpus.tsv")"
[ "$landed_doc" = "usr/share/doc/mios/harvested.md" ] \
    || die "case 3: ledger landed_doc not recorded (got '$landed_doc')"

log "Case 4: harvest left the source untouched"
grep -q "long and genuinely narrative" "$fix/automation/50-example.sh" \
    || die "case 4: harvest modified source -- only prune may do that"

log "Case 5: harvest is idempotent (no duplicate passage)"
run harvest --path automation/50-example.sh --to usr/share/doc/mios/harvested.md >/dev/null
n="$(grep -c "mios-src:$sha" "$doc")"
[ "$n" -eq 1 ] || die "case 5: anchor written $n times, expected 1"

narrative() {
    run coverage --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["unmigrated_narrative"])'
}

log "Case 6: prune now accepts, and removes the comment from source"
# The fixture copies the real mios.toml, whose own comments dominate the census,
# so the meaningful assertion is the DELTA, not an absolute count.
run prune --path automation/50-example.sh >/dev/null
grep -q "long and genuinely narrative" "$fix/automation/50-example.sh" \
    && die "case 6: comment still present after prune"
grep -q "^echo \"phase\"" "$fix/automation/50-example.sh" \
    || die "case 6: prune removed the wrong lines -- code was destroyed"
grep -q "AI-hint:" "$fix/automation/50-example.sh" \
    || die "case 6: prune ate the AI-hint header"

log "Case 7: the narrative count fell -- the ratchet can now go down"
after="$(narrative)"
[ "$after" -eq "$((before - 1))" ] \
    || die "case 7: narrative went $before -> $after, expected $((before - 1))"
log "  narrative $before -> $after"

log "Case 8: tombstone survives ledger regeneration"
( cd "$fix" && git add -A )
run ledger --write >/dev/null
grep -q "$sha" "$fix/usr/share/mios/reference/manual-corpus.tsv" \
    || die "case 8: tombstone lost -- the deletion proof is gone"
pruned="$(awk -F'\t' -v s="$sha" '$6==s {print $14}' \
    "$fix/usr/share/mios/reference/manual-corpus.tsv")"
[ "$pruned" = "1" ] || die "case 8: pruned flag not retained (got '$pruned')"

log "Case 9: landing gate is green while the passage exists"
run landing --check >/dev/null || die "case 9: landing check failed unexpectedly"

log "Case 10: landing gate goes RED if the harvested passage is deleted"
: > "$doc"
if run landing --check >/dev/null 2>&1; then
    die "case 10: landing check passed after the doc passage was destroyed"
fi

log "Case 11: the CLI serves the sibling repo's layout (SSOT at the root)"
alt="$(mktemp -d)"
mkdir -p "$alt/src"
cp "$ROOT/usr/share/mios/mios.toml" "$alt/mios.toml"
printf '#!/usr/bin/env bash\n# AI-hint: fixture.\n# AI-related: none\necho hi\n' > "$alt/src/x.sh"
( cd "$alt" && git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -qm f )
MIOS_ROOT="$alt" python3 "$CLI" --root "$alt" coverage --json >/dev/null 2>&1 \
    || { rm -rf "$alt"; die "case 11: CLI failed against a root-level mios.toml (bootstrap layout)"; }
rm -rf "$alt"

log "Case 12: distill lands every block the ratchet counts (SSOT, unledgered file, lost landing)"
# Heredoc bodies, so the lexer reads the planted prose as fixture data, not as this file's comments.
cat >> "$fix/usr/share/mios/mios.toml" <<'EOF'

# The operator rejected the alternative after an incident, so the rationale is
# kept where the next reader looks. This fixture block sits in mios.toml itself,
# which distill once skipped while the ratchet still counted it.
EOF
cp "$fix/automation/50-example.sh" "$fix/automation/60-unledgered.sh"
cat >> "$fix/automation/60-unledgered.sh" <<'EOF'

# Previously this was reverted after a regression; the invariant is that the
# ledger has no row for this block yet, and distill must still land it rather
# than skip it while the ratchet counts it as unmigrated narrative here.
EOF
( cd "$fix" && git add -A )
[ "$(narrative)" -gt 0 ] || die "case 12: the planted blocks were not counted"
run distill >/dev/null
left="$(narrative)"
[ "$left" -eq 0 ] || die "case 12: distill left $left block(s) the ratchet still counts"
toml_sha="$(awk -F'\t' '$1=="usr/share/mios/mios.toml" && $7=="MIGRATE" && $8=="midsize-narrative" {s=$6} END {print s}' \
    "$fix/usr/share/mios/reference/manual-corpus.tsv")"
[ -n "$toml_sha" ] || die "case 12: the mios.toml block has no ledger row"
python3 -c 'import sys; p, s = sys.argv[1:]; r = [l.split("\t") for l in open(p, encoding="utf-8").read().splitlines()]
open(p, "w", encoding="utf-8", newline="\n").write("\n".join("\t".join(x[:10] + [""] + x[11:]) if len(x) == 14 and x[5] == s else "\t".join(x) for x in r) + "\n")' \
    "$fix/usr/share/mios/reference/manual-corpus.tsv" "$toml_sha"
[ "$(narrative)" -ge 1 ] || die "case 12: blanking a landing did not reopen the block"
run distill >/dev/null
[ "$(narrative)" -eq 0 ] || die "case 12: distill did not re-record a landing whose passage exists"
n="$(grep -rc "mios-src:$toml_sha" "$fix/usr/share/doc/mios/manual" | awk -F: '{s+=$2} END {print s}')"
[ "$n" -eq 1 ] || die "case 12: passage for $toml_sha written $n times, expected 1"

log "all cases passed"
