#!/usr/bin/env bash
# AI-hint: bash Runs mios-chrony-ptp-dropin against fixture PTP clocks: a Hyper-V or KVM clock gets the PHC refclock drop-in; a bare-metal NIC PHC, an unnamed clock or no /dev/ptp0 gets none, and a drop-in an earlier boot wrote on bare metal is removed.
# AI-related: usr/libexec/mios/mios-chrony-ptp-dropin, usr/lib/systemd/system/mios-chrony-ptp.service
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"
GEN="$ROOT/usr/libexec/mios/mios-chrony-ptp-dropin"

log() { printf '[test-chrony-ptp-dropin] %s\n' "$*"; }
die() { printf '[test-chrony-ptp-dropin] ERROR: %s\n' "$*" >&2; exit 1; }

[ -x "$GEN" ] || die "missing or non-executable $GEN"
fix="$(mktemp -d)"
trap 'rm -rf "$fix"' EXIT
# chronyc must never touch the host's daemon from a test.
mkdir -p "$fix/bin"; printf '#!/bin/sh\nexit 0\n' > "$fix/bin/chronyc"; chmod +x "$fix/bin/chronyc"

# run CASE CLOCK_NAME|-none- [DEVICE=yes]
run() {
    local c="$fix/$1"
    mkdir -p "$c/chrony.d" "$c/sys"
    if [[ "${3:-yes}" == yes ]]; then : > "$c/ptp0"; fi
    if [[ "$2" != -none- ]]; then printf '%s\n' "$2" > "$c/sys/clock_name"; fi
    PATH="$fix/bin:$PATH" CHRONY_D="$c/chrony.d" PTP_DEV="$c/ptp0" PTP_SYSFS="$c/sys" \
        bash "$GEN" > "$c/out" 2>&1 || { cat "$c/out" >&2; die "$1: generator failed"; }
}
has() { [[ -f "$fix/$1/chrony.d/10-ptp.conf" ]]; }

log "Case 1: Hyper-V clock (WSL2) gets the PHC refclock"
run hyperv hyperv
has hyperv || die "case 1: no drop-in for a Hyper-V clock"
grep -q "^refclock PHC $fix/hyperv/ptp0 .*prefer trust" "$fix/hyperv/chrony.d/10-ptp.conf" || die "case 1: wrong refclock line"

log "Case 2: KVM virtual PTP gets the PHC refclock"
run kvm "KVM virtual PTP"
has kvm || die "case 2: no drop-in for a KVM clock"

log "Case 3: a bare-metal NIC PHC gets no drop-in"
run nic "igc"
if has nic; then die "case 3: a NIC PHC was made the preferred, trusted source"; fi
grep -q "not a hypervisor clock" "$fix/nic/out" || die "case 3: no explanation logged"

log "Case 4: an unnamed clock gets no drop-in"
run anon -none-
if has anon; then die "case 4: an unidentified clock was trusted"; fi

log "Case 5: a drop-in left on bare metal by an earlier boot is removed"
mkdir -p "$fix/stale/chrony.d"
printf 'refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0 minsamples 4 prefer trust\n' > "$fix/stale/chrony.d/10-ptp.conf"
run stale "r8169 PHC"
if has stale; then die "case 5: the stale bare-metal drop-in survived"; fi

log "Case 6: no /dev/ptp0 writes nothing"
run none hyperv no
if has none; then die "case 6: a drop-in without a PTP device"; fi

log "PASS"
