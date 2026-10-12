#!/usr/bin/env bash
# AI-hint: Two-sided test that mios-swarm-pack-firstboot creates a worker's slot dir before writing its .env, and arms no worker whose slot dir cannot be created.
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail(){ echo "[swarm-pack] FAIL: $*" >&2; exit 1; }

# shellcheck source=/dev/null
. "$ROOT/usr/libexec/mios/mios-swarm-pack-firstboot"
systemctl(){ echo "$*" >> "$TMP/systemctl.log"; }
logger(){ :; }
RUNDIR="$TMP/run"; mkdir -p "$RUNDIR"
MIOS_SERVICES_LLAMACPP_UID="$(id -u)"; MIOS_SERVICES_LLAMACPP_GID="$(id -g)"
export MIOS_SERVICES_LLAMACPP_UID MIOS_SERVICES_LLAMACPP_GID

# Positive: the slot dir exists before the .env and the instance is started.
SLOTS="$TMP/slots"
arm_worker w1 m.gguf 8601 99 8192 >/dev/null || fail "arm_worker w1 returned non-zero"
[ -d "$SLOTS/w1" ] || fail "slot dir $SLOTS/w1 not created"
grep -q '^WORKER_MODEL=m.gguf$' "$RUNDIR/w1.env" || fail "w1.env missing or wrong"
grep -q 'start mios-llm-worker@w1.service' "$TMP/systemctl.log" || fail "w1 was not started"
echo "[swarm-pack] ok: armed worker has its slot dir, .env and start"

# Negative: a slot dir that cannot be created (its parent is a file) arms nothing.
: > "$TMP/notadir"; SLOTS="$TMP/notadir"
if arm_worker w2 m.gguf 8602 99 8192 >/dev/null 2>&1; then fail "arm_worker w2 succeeded without a slot dir"; fi
[ ! -e "$RUNDIR/w2.env" ] || fail "w2.env written although its slot dir failed (the unit's condition would pass)"
! grep -q 'mios-llm-worker@w2' "$TMP/systemctl.log" || fail "w2 was started without a slot dir"
echo "[swarm-pack] ok: a worker whose slot dir fails gets no .env and no start"
echo "[swarm-pack] PASS: 2/2"
