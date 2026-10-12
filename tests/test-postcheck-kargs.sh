#!/usr/bin/env bash
# AI-hint: bash Runs the 99-postcheck kargs.d gate against fixture roots: the shipped kargs.d passes; a missing dir, an empty dir, malformed TOML and a non-string kargs array each fail.
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"
PC="$ROOT/automation/99-postcheck.sh"
KARGS_SRC="$ROOT/usr/lib/bootc/kargs.d"

log() { printf '[test-postcheck-kargs] %s\n' "$*"; }
die() { printf '[test-postcheck-kargs] ERROR: %s\n' "$*" >&2; exit 1; }

[ -r "$PC" ] || die "missing $PC"
[ -d "$KARGS_SRC" ] || die "missing $KARGS_SRC"
command -v python3 >/dev/null 2>&1 || die "python3 required"
# Sourcing a postcheck without the guard would run the whole image gate
# (sysusers, sshd probes) on this host, so refuse before sourcing.
grep -q '^mios_postcheck_kargs()' "$PC" || die "99-postcheck.sh defines no mios_postcheck_kargs()"
grep -q '^mios_postcheck_expired_password()' "$PC" || die "99-postcheck.sh defines no mios_postcheck_expired_password()"
grep -q '^\[\[ "${BASH_SOURCE\[0\]}" == "$0" \]\] || return 0' "$PC" \
    || die "99-postcheck.sh has no source guard"

fix="$(mktemp -d)"
trap 'rm -rf "$fix"' EXIT

# 0 when the gate passes on root $1, 1 when it dies.
# shellcheck source=/dev/null
gate() { ( source "$PC" && mios_postcheck_kargs "$1" ) >"$fix/out" 2>&1; }

log "Case 1: the shipped usr/lib/bootc/kargs.d passes"
mkdir -p "$fix/good/usr/lib/bootc"
cp -r "$KARGS_SRC" "$fix/good/usr/lib/bootc/kargs.d"
gate "$fix/good" || { cat "$fix/out" >&2; die "case 1: shipped kargs.d was rejected"; }

log "Case 2: a missing kargs.d fails"
mkdir -p "$fix/missing/usr/lib/bootc"
if gate "$fix/missing"; then die "case 2: missing kargs.d passed"; fi

log "Case 3: an empty kargs.d fails"
mkdir -p "$fix/empty/usr/lib/bootc/kargs.d"
if gate "$fix/empty"; then die "case 3: empty kargs.d passed"; fi

log "Case 4: a malformed TOML file fails"
mkdir -p "$fix/bad/usr/lib/bootc"
cp -r "$KARGS_SRC" "$fix/bad/usr/lib/bootc/kargs.d"
printf 'kargs = ["iommu=pt"\n' > "$fix/bad/usr/lib/bootc/kargs.d/99-broken.toml"
if gate "$fix/bad"; then die "case 4: malformed TOML passed"; fi
grep -q '99-broken.toml' "$fix/out" || { cat "$fix/out" >&2; die "case 4: failure does not name the file"; }

log "Case 5: a kargs value that is not an array of strings fails"
mkdir -p "$fix/type/usr/lib/bootc/kargs.d"
printf 'kargs = ["iommu=pt", 3]\n' > "$fix/type/usr/lib/bootc/kargs.d/10-type.toml"
if gate "$fix/type"; then die "case 5: non-string karg passed"; fi
printf 'match-architectures = ["x86_64"]\n' > "$fix/type/usr/lib/bootc/kargs.d/10-type.toml"
if gate "$fix/type"; then die "case 5: file without kargs passed"; fi

log "Case 6: a valid expired password (lastchg = 0) passes"
mkdir -p "$fix/good_pw/etc"
printf 'mios:$6$fakehash:0:0:99999:7:::\n' > "$fix/good_pw/etc/shadow"
# shellcheck source=/dev/null
( source "$PC" && mios_postcheck_expired_password "$fix/good_pw" ) >"$fix/out_pw" 2>&1 || { cat "$fix/out_pw" >&2; die "case 6: valid expired password was rejected"; }

log "Case 7: a non-expired password (lastchg != 0) fails"
mkdir -p "$fix/bad_pw/etc"
printf 'mios:$6$fakehash:19800:0:99999:7:::\n' > "$fix/bad_pw/etc/shadow"
# shellcheck source=/dev/null
if ( source "$PC" && mios_postcheck_expired_password "$fix/bad_pw" ) >"$fix/out_pw" 2>&1; then
    die "case 7: non-expired password passed";
fi
grep -qE 'lastchg|expired|chage' "$fix/out_pw" || { cat "$fix/out_pw" >&2; die "case 7: failure does not name lastchg/expired"; }

log "PASS"
