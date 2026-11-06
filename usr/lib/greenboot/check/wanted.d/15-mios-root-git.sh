#!/usr/bin/bash
# AI-hint: Advisory greenboot check of the deployed-root axiom: / is the MiOS Git top level and tracks the vendor SSOT, so `git -C / pull` updates the overlay. wanted.d: a miss warns and never rolls back.
# AI-related: installation/mios-common.sh, usr/libexec/mios/git-root-init.sh, tests/test-m4-root-git-substrate.py
set -euo pipefail
# Greenboot passes no argument; the optional root argument lets the unit suite run this gate on fixtures.
root="${1:-/}"
if ! command -v git >/dev/null 2>&1; then
    echo "[greenboot] WARN: git is not installed -- cannot verify that ${root} is the MiOS Git top level"
    exit 1
fi
top="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null)" || top=""
if [[ -z "$top" || "$(realpath -- "$top")" != "$(realpath -- "$root")" ]]; then
    echo "[greenboot] WARN: ${root} is not the MiOS Git top level (git resolves: ${top:-no repository})"
    exit 1
fi
if ! git -C "$root" ls-files --error-unmatch -- usr/share/mios/mios.toml >/dev/null 2>&1; then
    echo "[greenboot] WARN: ${root} is a Git top level that does not track the vendor SSOT usr/share/mios/mios.toml"
    exit 1
fi
echo "[greenboot] OK: ${root} is the MiOS Git top level and tracks usr/share/mios/mios.toml"
