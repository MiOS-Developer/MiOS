#!/usr/bin/env bash
# AI-hint: Canonical "Git = $ROOT" root-merge -- makes a target root ($MIOS_ROOT, default /) a
# AI-functions: mios_root_merge
# AI-related: installation/mios-common.sh, build-mios.sh, build-mios.ps1, automation/build.sh, automation/mios-apply, usr/lib/mios/userenv.sh

# The implementation lives in installation/mios-common.sh, the shared Linux
# library mirrored into mios-bootstrap (Law 15): the FHS installer runs the
# merge from a bootstrap checkout before /automation exists. One definition.
# shellcheck source=../../installation/mios-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../installation" && pwd)/mios-common.sh"

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
