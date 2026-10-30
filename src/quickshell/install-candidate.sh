#!/bin/bash
# AI-hint: Lead-only, transactional install and exact rollback of the staged Quickshell candidate
# (usr/bin/quickshell, usr/bin/qs, usr/lib64/qt6/qml/Quickshell/**, its desktop entry and icon). Every file is
# installed one by one with an explicit mode into a temp name next to its destination and renamed into place; no
# archive is ever extracted over the root, no existing directory is created, chmod'ed or chown'ed, the binary is
# stripped, every replaced file is backed up and verified first, a failure undoes the partial install, and rollback
# restores the replaced files exactly and removes the files and directories only the candidate created. It starts,
# stops and signals nothing: the running shell keeps the binary it has mapped.
# AI-related: /usr/share/doc/mios/quickshell-candidate.md, /tests/test-quickshell-candidate-install.py,
# /src/quickshell/patches/hyprland-event-socket-reconnect.patch, /automation/66-bake-quickshell.sh
# AI-functions: main, parse_args, validate_stage, do_plan, do_install, do_rollback, do_status, undo_entries

set -euo pipefail

PROG=install-candidate
ROOT=""
STAGE=""
STATE=""
EXPECT_VERSION='^Quickshell 0\.3\.2 '
VERB=""

log_info() { printf '[%s] %s\n' "$PROG" "$*" >&2; }
log_error() { printf '[%s] ERROR: %s\n' "$PROG" "$*" >&2; }
die() { log_error "$*"; exit 1; }

show_help() {
    cat <<EOF
Usage: $PROG plan|install|rollback|status --root DIR [--stage DIR] [--state DIR] [--expect-version REGEX]

  plan      validate the stage and print every file the install would write (changes nothing)
  install   back up and replace the candidate's files under --root (root only when --root is /)
  rollback  restore the files the last install replaced, remove the ones it created
  status    print the active install transaction, if any

  --root DIR            install root; required, no default (the live system is --root /)
  --stage DIR           the candidate's DESTDIR stage (plan, install)
  --state DIR           transaction/backup directory (default <root>/var/lib/mios-build/rollback/quickshell-candidate)
  --expect-version RE   regex the staged 'quickshell --version' must match (default '$EXPECT_VERSION')

Nothing is started, stopped or signalled. The running shell keeps its mapped binary until the operator restarts it.
EOF
}

parse_args() {
    [[ $# -gt 0 ]] || { show_help >&2; exit 2; }
    case "$1" in
        plan|install|rollback|status) VERB=$1; shift ;;
        -h|--help) show_help; exit 0 ;;
        *) log_error "unknown verb: $1"; show_help >&2; exit 2 ;;
    esac
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --root) ROOT=${2:?--root needs a directory}; shift 2 ;;
            --stage) STAGE=${2:?--stage needs a directory}; shift 2 ;;
            --state) STATE=${2:?--state needs a directory}; shift 2 ;;
            --expect-version) EXPECT_VERSION=${2:?--expect-version needs a regex}; shift 2 ;;
            -h|--help) show_help; exit 0 ;;
            *) log_error "unknown option: $1"; exit 2 ;;
        esac
    done
    [[ -n $ROOT ]] || die "--root is required (the live system is --root /)"
    [[ -d $ROOT && ! -L $ROOT ]] || die "--root $ROOT is not a directory"
    ROOT=$(realpath -e -- "$ROOT")
    if [[ $ROOT == / && $VERB != plan && $VERB != status && $EUID -ne 0 ]]; then
        die "--root / needs root (run it with sudo)"
    fi
    R=${ROOT%/}    # "" for /, so "$R/usr/bin" never doubles the slash
    STATE=${STATE:-$R/var/lib/mios-build/rollback/quickshell-candidate}
    if [[ -n $STAGE ]]; then
        [[ -d $STAGE ]] || die "--stage $STAGE is not a directory"
        STAGE=$(realpath -e -- "$STAGE")
        [[ $STAGE != "$ROOT" && $STAGE != / ]] || die "--stage must not be the install root"
    fi
}

# The only paths the candidate may write (relative to the root). Anything else in the stage aborts the install.
allowed() {
    case "$1" in
        usr/bin/quickshell|usr/bin/qs) return 0 ;;
        usr/lib64/qt6/qml/Quickshell/*) return 0 ;;
        usr/share/applications/org.quickshell.desktop) return 0 ;;
        usr/share/icons/hicolor/*/apps/org.quickshell.*) return 0 ;;
    esac
    return 1
}

mode_for() { case "$1" in usr/bin/*) echo 0755 ;; *) echo 0644 ;; esac; }

# Run a staged binary unprivileged: as the invoking sudo user when root, else as ourselves.
run_unprivileged() {
    if [[ $EUID -eq 0 ]]; then
        local uid=${SUDO_UID:-65534} gid=${SUDO_GID:-65534}
        setpriv --reuid="$uid" --regid="$gid" --clear-groups --no-new-privs -- "$@"
    else
        "$@"
    fi
}

ENTRIES=()   # "f<TAB>rel" or "l<TAB>rel<TAB>target", in install order
validate_stage() {
    [[ -n $STAGE ]] || die "--stage is required"
    local odd
    odd=$(find "$STAGE" -mindepth 1 ! -type d ! -type f ! -type l -printf '%P\n' | head -n 5)
    [[ -z $odd ]] || die "stage holds special files: $odd"
    odd=$(find "$STAGE" -mindepth 1 -name $'*[\t\n]*' -printf '%P\n' | head -n 5)
    [[ -z $odd ]] || die "stage holds names with tabs or newlines: $odd"
    local rel type target seg
    while IFS=$'\t' read -r -d '' type rel target; do
        IFS=/ read -ra segs <<<"$rel"
        for seg in "${segs[@]}"; do
            [[ $seg != . && $seg != .. && -n $seg ]] || die "unsafe stage path: $rel"
        done
        allowed "$rel" || die "stage path outside the candidate's allowlist: $rel"
        if [[ $type == l ]]; then
            local resolved
            case "$target" in
                /*) resolved=${target#/} ;;
                *) resolved=$(realpath -m --relative-to=/ -- "/$(dirname -- "$rel")/$target") ;;
            esac
            allowed "$resolved" || die "symlink $rel -> $target points outside the candidate"
            # installed relative, so the link resolves inside whichever root it is installed under
            ENTRIES+=("l"$'\t'"$rel"$'\t'"$(realpath -m --relative-to="/$(dirname -- "$rel")" -- "/$resolved")")
        else
            ENTRIES+=("f"$'\t'"$rel")
        fi
    done < <(find "$STAGE" -mindepth 1 \( -type f -o -type l \) -printf '%y\t%P\t%l\0' | sort -z)
    [[ ${#ENTRIES[@]} -gt 0 ]] || die "stage $STAGE is empty"
    [[ -f $STAGE/usr/bin/quickshell && ! -L $STAGE/usr/bin/quickshell ]] || die "stage has no usr/bin/quickshell"
    local version
    version=$(run_unprivileged "$STAGE/usr/bin/quickshell" --version 2>/dev/null | head -n 1) \
        || die "staged quickshell --version failed"
    [[ $version =~ $EXPECT_VERSION ]] || die "staged binary reports '$version', expected /$EXPECT_VERSION/"
    log_info "stage: $version (${#ENTRIES[@]} files)"
}

# Every ancestor directory of a destination must be a real directory (never a symlink out of the root) or absent.
check_ancestors() {
    local rel=$1 path=$R part
    IFS=/ read -ra parts <<<"$(dirname -- "$rel")"
    for part in "${parts[@]}"; do
        path=$path/$part
        if [[ -L $path ]]; then die "ancestor $path is a symlink; refusing"; fi
        if [[ -e $path && ! -d $path ]]; then die "ancestor $path is not a directory"; fi
    done
}

# mode uid gid of every existing ancestor (and /usr, /usr/bin, /usr/lib64, /usr/share), for the post-install assert
dir_snapshot() {
    local e rel d
    {
        printf '%s\n' "$R/usr" "$R/usr/bin" "$R/usr/lib64" "$R/usr/share"
        for e in "${ENTRIES[@]}"; do
            rel=$(cut -f2 <<<"$e")
            d=$(dirname -- "$R/$rel")
            while [[ $d == "$R"/* ]]; do printf '%s\n' "$d"; d=$(dirname -- "$d"); done
        done
    } | sort -u | while read -r d; do [[ -d $d ]] && stat -c '%a %u %g %n' -- "$d"; done
    return 0
}

do_plan() {
    validate_stage
    local e rel
    for e in "${ENTRIES[@]}"; do
        rel=$(cut -f2 <<<"$e")
        check_ancestors "$rel"
        if [[ -e $R/$rel || -L $R/$rel ]]; then echo "replace $R/$rel"; else echo "create  $R/$rel"; fi
    done
}

TXN=""
JOURNAL=""
DONE=()      # journal lines already applied (for undo on failure)
TEMPS=()     # prepared temp files not yet renamed
NEWDIRS=()   # directories this run created

undo_entries() {
    # Reverse the applied journal lines: restore replaced files, remove created ones, then the created directories.
    local i line kind rel tmp
    for ((i = ${#DONE[@]} - 1; i >= 0; i--)); do
        line=${DONE[i]}
        kind=$(cut -f1 <<<"$line"); rel=$(cut -f2 <<<"$line")
        case "$kind" in
            replace)
                tmp=$(dirname -- "$R/$rel")/.$(basename -- "$rel").mios-rollback.$$
                cp -a --no-dereference -- "$TXN/backup/$rel" "$tmp" && mv -fT -- "$tmp" "$R/$rel" \
                    || log_error "could not restore $R/$rel from $TXN/backup/$rel"
                ;;
            create) rm -f -- "$R/$rel" ;;
            dir) rmdir -- "$R/$rel" 2>/dev/null || log_error "left non-empty directory $R/$rel" ;;
        esac
    done
}

INSTALLING=0
on_install_exit() {
    local rc=$?
    [[ $INSTALLING -eq 1 && $rc -ne 0 ]] || return 0
    set +e
    log_error "install failed (rc=$rc); undoing the partial install"
    local t; for t in "${TEMPS[@]}"; do rm -f -- "$t"; done
    undo_entries
    if [[ -n $TXN ]]; then mv -f -- "$TXN" "$TXN.failed"; fi
    log_error "partial install undone; journal kept in $TXN.failed"
}

journal() { printf '%s\n' "$1" >>"$JOURNAL"; }

file_digest() { if [[ -L $1 ]]; then printf 'link:%s\n' "$(readlink -- "$1")"; else sha256sum -- "$1" | cut -d' ' -f1; fi; }

do_install() {
    validate_stage
    local e rel
    for e in "${ENTRIES[@]}"; do check_ancestors "$(cut -f2 <<<"$e")"; done
    (umask 077 && mkdir -p -- "$STATE")
    exec 9>"$STATE/.lock"
    flock -n 9 || die "another install/rollback holds $STATE/.lock"
    [[ ! -e $STATE/current ]] || die "an install is already active ($(cat -- "$STATE/current")); roll it back first"
    TXN=$STATE/txn-$(date -u +%Y%m%dT%H%M%SZ)-$$
    mkdir -m 0700 -- "$TXN" "$TXN/backup"
    JOURNAL=$TXN/journal.tsv
    : >"$JOURNAL"
    dir_snapshot >"$TXN/dirs.before"
    trap on_install_exit EXIT
    trap 'exit 130' INT TERM HUP
    INSTALLING=1

    local owner=()
    if [[ $EUID -eq 0 ]]; then owner=(-o root -g root); fi
    local kind target dest dir tmp mode d sub
    # 1. back up every destination that exists, verified, before anything is written
    for e in "${ENTRIES[@]}"; do
        rel=$(cut -f2 <<<"$e"); dest=$R/$rel
        if [[ -e $dest || -L $dest ]]; then
            [[ ! -d $dest || -L $dest ]] || die "$dest is a directory; refusing to replace it"
            (umask 077 && mkdir -p -- "$(dirname -- "$TXN/backup/$rel")")
            cp -a --no-dereference -- "$dest" "$TXN/backup/$rel"
            [[ $(file_digest "$dest") == "$(file_digest "$TXN/backup/$rel")" ]] || die "backup of $dest does not verify"
            printf 'replace\t%s\t%s\n' "$rel" "$(file_digest "$dest")" >>"$TXN/backup.tsv"
        fi
    done
    # 2. prepare every file under a temp name in its destination directory (same filesystem: rename is atomic)
    local prepared=()
    for e in "${ENTRIES[@]}"; do
        kind=$(cut -f1 <<<"$e"); rel=$(cut -f2 <<<"$e"); dest=$R/$rel; dir=$(dirname -- "$dest")
        if [[ ! -d $dir ]]; then
            # create the missing directories one level at a time, 0755, recorded for rollback
            sub=$R
            IFS=/ read -ra parts <<<"$(dirname -- "$rel")"
            for d in "${parts[@]}"; do
                sub=$sub/$d
                if [[ ! -d $sub ]]; then
                    install -d -m 0755 "${owner[@]}" -- "$sub"
                    NEWDIRS+=("${sub#"$R"/}")
                    DONE+=("dir"$'\t'"${sub#"$R"/}")
                    journal "dir"$'\t'"${sub#"$R"/}"
                fi
            done
        fi
        tmp=$dir/.$(basename -- "$rel").mios-new.$$
        TEMPS+=("$tmp")
        if [[ $kind == l ]]; then
            target=$(cut -f3 <<<"$e")
            ln -sfn -- "$target" "$tmp"
            if [[ $EUID -eq 0 ]]; then chown -h root:root -- "$tmp"; fi
        elif [[ $rel == usr/bin/quickshell ]]; then
            # the stage holds a RelWithDebInfo build; ship it stripped like the package
            strip --strip-unneeded -o "$tmp" -- "$STAGE/$rel"
            chmod 0755 -- "$tmp"
            if [[ $EUID -eq 0 ]]; then chown root:root -- "$tmp"; fi
            if readelf -S -W -- "$tmp" | grep -q '\.debug_'; then die "stripped binary still carries debug sections"; fi
            [[ $(run_unprivileged "$tmp" --version 2>/dev/null | head -n 1) =~ $EXPECT_VERSION ]] \
                || die "stripped binary does not run"
        else
            mode=$(mode_for "$rel")
            install -m "$mode" "${owner[@]}" -T -- "$STAGE/$rel" "$tmp"
        fi
        prepared+=("$tmp"$'\t'"$rel"$'\t'"$(file_digest "$tmp")")
    done
    # 3. commit: rename each prepared file over its destination, journalled for undo
    local p digest
    for p in "${prepared[@]}"; do
        tmp=$(cut -f1 <<<"$p"); rel=$(cut -f2 <<<"$p"); digest=$(cut -f3 <<<"$p"); dest=$R/$rel
        if [[ -e $TXN/backup/$rel || -L $TXN/backup/$rel ]]; then kind=replace; else kind=create; fi
        mv -fT -- "$tmp" "$dest"
        DONE+=("$kind"$'\t'"$rel")
        journal "$kind"$'\t'"$rel"$'\t'"$digest"
        # test hook (scratch roots only): fail after N renames to prove the undo of a half-committed install
        if [[ -n $R && -n ${MIOS_QS_INSTALL_FAIL_AFTER:-} && ${#DONE[@]} -ge $MIOS_QS_INSTALL_FAIL_AFTER ]]; then
            die "MIOS_QS_INSTALL_FAIL_AFTER=$MIOS_QS_INSTALL_FAIL_AFTER reached"
        fi
    done
    TEMPS=()
    # 4. labels on exactly the installed paths (the live root only)
    if [[ -z $R ]] && command -v selinuxenabled >/dev/null && selinuxenabled; then
        local paths=() p2
        for p2 in "${prepared[@]}"; do paths+=("/$(cut -f2 <<<"$p2")"); done
        for d in "${NEWDIRS[@]}"; do paths+=("/$d"); done
        restorecon -F -- "${paths[@]}"
    fi
    # 5. asserts: no pre-existing directory changed mode or owner; every file is what was prepared
    local before_n after_n
    before_n=$(wc -l <"$TXN/dirs.before")
    after_n=$(dir_snapshot | grep -Fxc -f "$TXN/dirs.before" || true)
    [[ $before_n == "$after_n" ]] || { diff <(cat "$TXN/dirs.before") <(dir_snapshot) >&2 || true; die "directory modes or owners changed"; }
    for p in "${prepared[@]}"; do
        rel=$(cut -f2 <<<"$p"); digest=$(cut -f3 <<<"$p")
        [[ $(file_digest "$R/$rel") == "$digest" ]] || die "$R/$rel does not match the prepared file"
    done
    INSTALLING=0
    trap - EXIT INT TERM HUP
    printf '%s\n' "$(basename -- "$TXN")" >"$STATE/.current.tmp"
    mv -fT -- "$STATE/.current.tmp" "$STATE/current"
    log_info "installed ${#prepared[@]} files under ${R:-/}; backup and journal in $TXN"
    stat -c '%a %U %n' -- "$R/usr" "$R/usr/bin" "$R/usr/lib64" >&2
    log_info "nothing was restarted: the running shell keeps its mapped binary until the operator restarts it"
}

do_rollback() {
    [[ -d $STATE ]] || die "no install state in $STATE"
    exec 9>"$STATE/.lock"
    flock -n 9 || die "another install/rollback holds $STATE/.lock"
    [[ -s $STATE/current ]] || die "no active install in $STATE"
    TXN=$STATE/$(cat -- "$STATE/current")
    JOURNAL=$TXN/journal.tsv
    [[ -f $JOURNAL ]] || die "journal $JOURNAL missing"
    local line kind rel n=0
    DONE=()
    while IFS= read -r line; do [[ -n $line ]] && DONE+=("$line"); done <"$JOURNAL"
    # every replaced file's backup must verify before anything is touched
    local digest
    if [[ -f $TXN/backup.tsv ]]; then
        while IFS=$'\t' read -r kind rel digest; do
            [[ $kind == replace ]] || continue
            [[ $(file_digest "$TXN/backup/$rel") == "$digest" ]] || die "backup $TXN/backup/$rel does not verify"
        done <"$TXN/backup.tsv"
    fi
    undo_entries
    # verify: replaced files are byte-identical to the backup, created ones are gone
    local bad=0 restored=()
    while IFS=$'\t' read -r kind rel _; do
        case "$kind" in
            replace)
                restored+=("/$rel"); n=$((n + 1))
                [[ $(file_digest "$R/$rel") == "$(file_digest "$TXN/backup/$rel")" ]] || { log_error "$R/$rel not restored"; bad=1; } ;;
            create) [[ ! -e $R/$rel && ! -L $R/$rel ]] || { log_error "$R/$rel still present"; bad=1; } ;;
            dir) [[ ! -d $R/$rel ]] || log_info "kept non-empty directory $R/$rel" ;;
        esac
    done <"$JOURNAL"
    [[ $bad -eq 0 ]] || die "rollback incomplete; backup kept in $TXN"
    if [[ -z $R ]] && [[ ${#restored[@]} -gt 0 ]] && command -v selinuxenabled >/dev/null && selinuxenabled; then
        restorecon -F -- "${restored[@]}"
    fi
    mv -fT -- "$TXN" "$TXN.rolled-back"
    rm -f -- "$STATE/current"
    log_info "rolled back: $n files restored exactly, the candidate's own files removed; backup kept in $TXN.rolled-back"
    log_info "nothing was restarted: restart the shell only when the operator agrees"
}

do_status() {
    if [[ -s $STATE/current ]]; then
        local t; t=$STATE/$(cat -- "$STATE/current")
        echo "active: $t"
        cut -f1 "$t/journal.tsv" | sort | uniq -c
    else
        echo "no active candidate install in $STATE"
    fi
}

main() {
    case "$VERB" in
        plan) do_plan ;;
        install) do_install ;;
        rollback) do_rollback ;;
        status) do_status ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    parse_args "$@"
    main
fi
