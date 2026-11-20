#!/usr/bin/env bash
# AI-hint: bash Runs the 99-postcheck unit-exec gate (miosd unit-exec-check) on fixture roots built from the shipped units: an enabled timer whose service has its program passes; the same root without the program (the PS5 Blade defect) fails naming it; the shipped opt-in mios-finetune-serve passes enabled only while its venv guard stands.
# AI-related: automation/99-postcheck.sh, src/mios-rs/mios-build/src/unit_exec.rs, usr/lib/systemd/system/mios-tailscale-sync.service, usr/lib/systemd/system/mios-finetune-serve.service
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"
PC="$ROOT/automation/99-postcheck.sh"
UNITS="$ROOT/usr/lib/systemd/system"

log() { printf '[test-postcheck-unit-execs] %s\n' "$*"; }
die() { printf '[test-postcheck-unit-execs] ERROR: %s\n' "$*" >&2; exit 1; }

[ -r "$PC" ] || die "missing $PC"
grep -q '^mios_postcheck_unit_execs()' "$PC" || die "99-postcheck.sh defines no mios_postcheck_unit_execs()"
grep -q '^mios_postcheck_unit_execs /$' "$PC" || die "99-postcheck.sh never runs the unit exec gate on the image root"
grep -q '^\[\[ "${BASH_SOURCE\[0\]}" == "$0" \]\] || return 0' "$PC" \
    || die "99-postcheck.sh has no source guard"

MIOSD="${MIOS_MIOSD_BIN:-$ROOT/src/mios-rs/target/debug/miosd}"
if [[ -z "${MIOS_MIOSD_BIN:-}" ]]; then
    (cd "$ROOT/src/mios-rs" && cargo build -q -p miosd) || die "cannot build miosd"
fi
[ -x "$MIOSD" ] || die "miosd is not executable: $MIOSD"
"$MIOSD" unit-exec-check --help >/dev/null 2>&1 || die "$MIOSD has no unit-exec-check (rebuild it from this checkout)"

fix="$(mktemp -d)"
trap 'rm -rf "$fix"' EXIT

# 0 when the gate passes on root $1, 1 when it dies.
# shellcheck source=/dev/null
gate() { ( source "$PC" && MIOS_MIOSD_BIN="$MIOSD" mios_postcheck_unit_execs "$1" ) >"$fix/out" 2>&1; }

# A root holding the shipped units $2..., with presets-style enable links.
mkroot() {
    local r="$1"; shift
    mkdir -p "$r/usr/lib/systemd/system" "$r/etc/systemd/system/timers.target.wants" \
             "$r/etc/systemd/system/multi-user.target.wants" "$r/usr/libexec/mios" "$r/usr/bin"
    local u
    for u in "$@"; do
        cp "$UNITS/$u" "$r/usr/lib/systemd/system/$u"
    done
}
enable() { ln -s "/usr/lib/systemd/system/$3" "$1/etc/systemd/system/$2/$3"; }

log "Case 1: enabled mios-tailscale-sync.timer with its program present passes"
mkroot "$fix/good" mios-tailscale-sync.service mios-tailscale-sync.timer
enable "$fix/good" timers.target.wants mios-tailscale-sync.timer
install -m 0755 "$ROOT/usr/libexec/mios/mios-tailscale-sync" "$fix/good/usr/libexec/mios/mios-tailscale-sync"
gate "$fix/good" || { cat "$fix/out" >&2; die "case 1: a complete root was rejected"; }

log "Case 2: the same root without /usr/libexec/mios/mios-tailscale-sync fails (PS5 Blade defect)"
cp -a "$fix/good" "$fix/ps5"
rm "$fix/ps5/usr/libexec/mios/mios-tailscale-sync"
if gate "$fix/ps5"; then die "case 2: a unit without its program passed"; fi
grep -q 'mios-tailscale-sync.service ExecStart=.*/usr/libexec/mios/mios-tailscale-sync' "$fix/out" \
    || { cat "$fix/out" >&2; die "case 2: failure does not name the unit and program"; }

log "Case 3: a program present but not executable fails"
cp -a "$fix/good" "$fix/noexec"
chmod 0644 "$fix/noexec/usr/libexec/mios/mios-tailscale-sync"
if gate "$fix/noexec"; then die "case 3: a non-executable program passed"; fi

log "Case 4: the shipped mios-finetune-serve passes enabled without its operator-built venv"
mkroot "$fix/ft" mios-finetune-serve.service
enable "$fix/ft" multi-user.target.wants mios-finetune-serve.service
gate "$fix/ft" || { cat "$fix/out" >&2; die "case 4: the guarded opt-in unit was rejected"; }

log "Case 5: the same unit without its ConditionPathExists guard fails"
cp -a "$fix/ft" "$fix/ft-bad"
sed -i '/^ConditionPathExists=/d' "$fix/ft-bad/usr/lib/systemd/system/mios-finetune-serve.service"
if gate "$fix/ft-bad"; then die "case 5: an unguarded unit with a missing program passed"; fi
grep -q '/var/lib/mios/finetune/venv/bin/python' "$fix/out" \
    || { cat "$fix/out" >&2; die "case 5: failure does not name the venv interpreter"; }

log "Case 6: a root with no enabled unit fails (presets never applied)"
mkdir -p "$fix/empty/usr/lib/systemd/system"
if gate "$fix/empty"; then die "case 6: an unpreset root passed"; fi

log "PASS"
