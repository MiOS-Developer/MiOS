#!/usr/bin/env bash
# AI-hint: Canonical "Git = $ROOT" root-merge -- makes a target root ($MIOS_ROOT, default /) a
# AI-functions: mios_root_merge
# AI-related: build-mios.sh, build-mios.ps1, automation/build.sh, automation/mios-apply, usr/lib/mios/userenv.sh

mios_root_merge() {
    local root="${1:-/}"
    local repo="${2:-}"
    local branch="${3:-main}"

    [[ -n "$root" ]] || { echo "[root-merge] FATAL: empty root" >&2; return 2; }
    [[ -n "$repo" ]] || { echo "[root-merge] FATAL: empty repo/source" >&2; return 2; }

    git config --global --add safe.directory "$root" 2>/dev/null || true
    git config --global --add safe.directory '*'      2>/dev/null || true

    if [[ ! -d "${root%/}/.git" ]]; then
        git init "$root" >/dev/null || { echo "[root-merge] FATAL: git init $root failed" >&2; return 1; }
    fi
    if git -C "$root" remote get-url origin >/dev/null 2>&1; then
        git -C "$root" remote set-url origin "$repo"
    else
        git -C "$root" remote add origin "$repo"
    fi
    git -C "$root" config core.autocrlf false 2>/dev/null || true

    local fetch_err
    if ! fetch_err="$(git -C "$root" fetch --depth=1 origin "$branch" 2>&1)"; then
        echo "[root-merge] FATAL: fetch $branch from $repo failed: $fetch_err" >&2
        return 1
    fi
    if ! git -C "$root" reset --hard FETCH_HEAD >/dev/null 2>&1; then
        echo "[root-merge] FATAL: reset" >&2
        return 1
    fi

    git -C "$root" checkout -B "$branch" >/dev/null 2>&1 || true
    git -C "$root" config "branch.${branch}.remote" origin
    git -C "$root" config "branch.${branch}.merge"  "refs/heads/${branch}"

    # Git checkout already restores the tracked executable bits. Recursively
    # chmodding runtime directories changes data and sourced libraries too,
    # leaving the root worktree dirty immediately after installation.

    return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
      --selftest)
        set -uo pipefail
        _src_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # automation/lib/../.. = repo root
        _tmp="${2:-/tmp/mios-rootmerge-selftest.$$}"
        rm -rf "$_tmp"; mkdir -p "$_tmp"
        _branch="$(git -C "$_src_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
        echo "[selftest] merge $_src_root/.git -> $_tmp"
        _ok=1
        mios_root_merge "$_tmp" "$_src_root/.git" "$_branch" || _ok=0
        echo ""
        [[ -d "$_tmp/.git" ]]                  && echo "  OK   .git materialized"          || { echo "  FAIL no .git"; _ok=0; }
        [[ -f "$_tmp/automation/build.sh" ]]   && echo "  OK   tracked file present"       || { echo "  FAIL tracked file missing"; _ok=0; }
        _up="$(git -C "$_tmp" config --get "branch.${_branch}.remote" 2>/dev/null || true)"
        [[ "$_up" == "origin" ]]               && echo "  OK   upstream wired (git pull works)" || { echo "  FAIL upstream=$_up"; _ok=0; }
        _hd="$(git -C "$_tmp" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
        [[ "$_hd" == "$_branch" ]]             && echo "  OK   HEAD on $_branch"           || { echo "  FAIL HEAD=$_hd"; _ok=0; }
        rm -rf "$_tmp"
        [[ $_ok -eq 1 ]] && { echo "SELFTEST: PASS"; exit 0; } || { echo "SELFTEST: FAIL"; exit 1; }
        ;;
      *)
        echo "Root-merge.sh is a sourced library. Run 'bash $0" >&2
        exit 2 ;;
    esac
fi
