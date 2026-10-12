#!/usr/bin/env bash
# AI-hint: bash Verifies mios-pgvector-major-upgrade never destroys an agent datastore -- exercises the no-op, unparseable-tag, downgrade-refusal, miss...
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"
SCRIPT="$ROOT/usr/libexec/mios/mios-pgvector-major-upgrade"

log() { printf '[test-pgvector-major-upgrade] %s\n' "$*"; }
die() { printf '[test-pgvector-major-upgrade] ERROR: %s\n' "$*" >&2; exit 1; }

[ -x "$SCRIPT" ] || die "missing or non-executable: $SCRIPT"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

stub_bin="$tmp_dir/bin"
mkdir -p "$stub_bin"
export PATH="$stub_bin:$PATH"

# Rebuild a pristine PG17 cluster + empty restore slot for each case.
reset_fixture() {
    rm -rf "$tmp_dir/data" "$tmp_dir/restore.sql" "$tmp_dir"/data.pg*
    mkdir -p "$tmp_dir/data/pgdata"
    echo "17" > "$tmp_dir/data/pgdata/PG_VERSION"
    echo "REAL-CLUSTER-BYTES" > "$tmp_dir/data/pgdata/base.dat"
    : > "$tmp_dir/restore.sql"
}

# $1 = podman behaviour: "absent" | "no-image" | "dump-fails" | "dump-empty" | "dump-ok"
make_podman() {
    case "$1" in
        absent) rm -f "$stub_bin/podman"; return 0 ;;
    esac
    cat > "$stub_bin/podman" <<EOF
#!/usr/bin/env bash
case "\$1" in
  image)
      [ "$1" = "no-image" ] && exit 1
      exit 0 ;;
  run)
      case "$1" in
        dump-fails) exit 1 ;;
        dump-empty) exit 0 ;;
        dump-ok)    echo "-- pg_dump output"; echo "DROP TABLE IF EXISTS knowledge;"; exit 0 ;;
      esac ;;
esac
exit 0
EOF
    chmod +x "$stub_bin/podman"
}

run_upgrade() {
    MIOS_PGVECTOR_DATA_DIR="$tmp_dir/data" \
    MIOS_PGVECTOR_RESTORE_SQL="$tmp_dir/restore.sql" \
    MIOS_PGVECTOR_IMAGE="$1" \
    MIOS_PGVECTOR_USER=mios MIOS_PGVECTOR_DB=mios \
    bash "$SCRIPT" >"$tmp_dir/out.log" 2>&1
}

assert_cluster_intact() {
    [ -f "$tmp_dir/data/pgdata/PG_VERSION" ] || die "$1: PG_VERSION gone -- the cluster was destroyed"
    grep -q "REAL-CLUSTER-BYTES" "$tmp_dir/data/pgdata/base.dat" 2>/dev/null \
        || die "$1: cluster contents gone -- data was destroyed"
}

log "Case 1: majors already match -> no-op, cluster untouched"
reset_fixture; make_podman dump-ok
run_upgrade "docker.io/pgvector/pgvector:pg17" || die "case 1: non-zero exit"
assert_cluster_intact "case 1"
[ -s "$tmp_dir/restore.sql" ] && die "case 1: restore slot should stay empty"

log "Case 2: spent restore slot is blanked once the majors agree"
reset_fixture; make_podman dump-ok
echo "STALE DUMP" > "$tmp_dir/restore.sql"
run_upgrade "docker.io/pgvector/pgvector:pg17" || die "case 2: non-zero exit"
[ -s "$tmp_dir/restore.sql" ] && die "case 2: stale dump must be cleared or it replays into a future cluster"
assert_cluster_intact "case 2"

log "Case 3: unparseable tag -> leave the cluster alone"
reset_fixture; make_podman dump-ok
run_upgrade "docker.io/pgvector/pgvector:latest" || die "case 3: non-zero exit"
assert_cluster_intact "case 3"
grep -q "no pgNN major" "$tmp_dir/out.log" || die "case 3: expected the unparseable-tag message"

log "Case 4: downgrade (pg16 image on a PG17 cluster) -> refused, non-destructive"
reset_fixture; make_podman dump-ok
run_upgrade "docker.io/pgvector/pgvector:pg16" || die "case 4: non-zero exit"
assert_cluster_intact "case 4"
grep -q "downgrade is not supported" "$tmp_dir/out.log" || die "case 4: expected the downgrade refusal"

log "Case 5: old-major image absent -> refuse, do NOT stash"
reset_fixture; make_podman no-image
run_upgrade "docker.io/pgvector/pgvector:pg18" || die "case 5: non-zero exit"
assert_cluster_intact "case 5"
grep -q "not touching the data dir" "$tmp_dir/out.log" || die "case 5: expected the refusal message"

log "Case 6: dump command fails -> refuse, do NOT stash"
reset_fixture; make_podman dump-fails
run_upgrade "docker.io/pgvector/pgvector:pg18" || die "case 6: non-zero exit"
assert_cluster_intact "case 6"
[ -s "$tmp_dir/restore.sql" ] && die "case 6: a failed dump must not leave a restore slot"

log "Case 7: dump succeeds but is EMPTY -> refuse, do NOT stash"
reset_fixture; make_podman dump-empty
run_upgrade "docker.io/pgvector/pgvector:pg18" || die "case 7: non-zero exit"
assert_cluster_intact "case 7"
[ -s "$tmp_dir/restore.sql" ] && die "case 7: an empty dump must not be accepted as a migration"

log "Case 8: podman missing entirely -> refuse, do NOT stash"
reset_fixture; make_podman absent
run_upgrade "docker.io/pgvector/pgvector:pg18" || die "case 8: non-zero exit"
assert_cluster_intact "case 8"

log "Case 9: happy path -> dump written, old cluster STASHED not deleted"
reset_fixture; make_podman dump-ok
run_upgrade "docker.io/pgvector/pgvector:pg18" || die "case 9: non-zero exit"
[ -s "$tmp_dir/restore.sql" ] || die "case 9: expected a populated restore slot"
grep -q "DROP TABLE IF EXISTS" "$tmp_dir/restore.sql" || die "case 9: restore slot lacks the dump body"
[ -e "$tmp_dir/data/pgdata" ] && die "case 9: old pgdata should have been moved aside"
stashed="$(find "$tmp_dir" -maxdepth 1 -name 'data.pg17.*' -type d | head -1)"
[ -n "$stashed" ] || die "case 9: old cluster was not stashed -- data would be lost"
# The stash IS the old PGDATA, renamed to a sibling of the data dir (same
# filesystem, so the move is a rename: atomic, and it never doubles disk use).
grep -q "REAL-CLUSTER-BYTES" "$stashed/base.dat" \
    || die "case 9: stash does not contain the original cluster bytes"
[ -f "$stashed/PG_VERSION" ] || die "case 9: stash is not a usable cluster (no PG_VERSION)"

log "Case 10: no cluster yet (fresh install) -> no-op, no stash"
rm -rf "$tmp_dir/data" "$tmp_dir"/data.pg*; mkdir -p "$tmp_dir/data"; : > "$tmp_dir/restore.sql"
make_podman dump-ok
run_upgrade "docker.io/pgvector/pgvector:pg18" || die "case 10: non-zero exit"
grep -q "no existing cluster" "$tmp_dir/out.log" || die "case 10: expected the fresh-install message"

log "all cases passed"
