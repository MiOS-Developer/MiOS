# shellcheck shell=bash
# AI-hint: Shared heavy-build lock: one flock serialises every memory-heavy MiOS build (Gecko, image, kernel) on a host so two never overlap.
# AI-related: usr/libexec/mios/mios-portal-build, usr/share/mios/mios.toml ([desktop.portal.builder].heavy_lock)
# AI-doc: usr/share/doc/mios/manual/automation.md
#
# Source this file, then run a command under the lock:
#
#   mios_heavy_lock_exec <lock-path> <command> [args...]
#
# MIOS_HEAVY_LOCK, when set, replaces <lock-path> for this run (the live host shares one lock
# across workflows). MIOS_HEAVY_LOCK_WAIT=<seconds> bounds the wait; 0 means do not wait.
# Unset waits for as long as it takes. A wait that times out exits 75 (EX_TEMPFAIL), so a
# second heavy build never starts beside the first one.

mios_heavy_lock_exec() {
    local lock="${MIOS_HEAVY_LOCK:-${1:-}}"
    [ -n "$lock" ] || { echo "mios_heavy_lock_exec: lock path required" >&2; return 2; }
    shift
    [ "$#" -gt 0 ] || { echo "mios_heavy_lock_exec: command required" >&2; return 2; }
    local dir
    dir="$(dirname "$lock")"
    [ -d "$dir" ] || { echo "heavy-lock: directory $dir is missing" >&2; return 2; }
    [ -e "$lock" ] || : >>"$lock" || { echo "heavy-lock: cannot create $lock" >&2; return 2; }
    local wait_args=()
    if [ -n "${MIOS_HEAVY_LOCK_WAIT:-}" ]; then
        case "$MIOS_HEAVY_LOCK_WAIT" in
            *[!0-9]*) echo "heavy-lock: MIOS_HEAVY_LOCK_WAIT must be whole seconds" >&2; return 2 ;;
        esac
        if [ "$MIOS_HEAVY_LOCK_WAIT" = 0 ]; then wait_args=(-n); else wait_args=(-w "$MIOS_HEAVY_LOCK_WAIT"); fi
    fi
    if ! flock -n "$lock" true 2>/dev/null; then
        echo "heavy-lock: $lock is held by another heavy job; waiting${MIOS_HEAVY_LOCK_WAIT:+ up to ${MIOS_HEAVY_LOCK_WAIT}s}" >&2
    fi
    local rc=0
    flock "${wait_args[@]}" -E 75 "$lock" "$@" || rc=$?
    [ "$rc" -ne 75 ] || echo "heavy-lock: gave up waiting for $lock" >&2
    return "$rc"
}
