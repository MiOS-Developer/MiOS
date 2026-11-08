#!/usr/bin/bash
# AI-hint: Advisory greenboot check of the deployed-root axiom: / is the MiOS Git top level and tracks the vendor SSOT, so `git -C / pull` updates the overlay. wanted.d: a miss warns and never rolls back.
# AI-related: installation/mios-common.sh, usr/libexec/mios/git-root-init.sh, tests/test-m4-root-git-substrate.py
set -euo pipefail
root="${1:-/}"  # greenboot passes none; the unit suite passes fixture roots
command -v git >/dev/null 2>&1 || { echo "[greenboot] WARN: git is not installed -- cannot verify that ${root} is the MiOS Git top level"; exit 1; }
# As mios_root_merge does: trust the root by name, since its owner need not be the user greenboot runs as.
real="$(cd -- "$root" 2>/dev/null && pwd -P)" || real="$root"
g() { git -c safe.directory="$real" -C "$real" "$@"; }
top="$(g rev-parse --show-toplevel 2>/dev/null)" || top=""
[[ -n "$top" && "$(realpath -- "$top")" == "$real" ]] || { echo "[greenboot] WARN: ${root} is not the MiOS Git top level (git resolves: ${top:-no repository})"; exit 1; }
g ls-files --error-unmatch -- usr/share/mios/mios.toml >/dev/null 2>&1 || { echo "[greenboot] WARN: ${root} is a Git top level that does not track the vendor SSOT usr/share/mios/mios.toml"; exit 1; }
echo "[greenboot] OK: ${root} is the MiOS Git top level and tracks usr/share/mios/mios.toml"
