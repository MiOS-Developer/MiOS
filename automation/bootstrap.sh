#!/bin/bash
# AI-hint: Initializes the MiOS build environment by prompting for/loading configuration variables, GitHub PATs, and admin credentials to prepare the local environment...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

PRIVATE_INSTALLER="https://raw.githubusercontent.com/mios-dev/mios/main/install.sh"
_ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/mios/mios-build.env"

_r=$'\033[0m'; _b=$'\033[1m'; _dim=$'\033[2m'; _c=$'\033[36m'; _g=$'\033[32m'; _red=$'\033[31m'; _y=$'\033[33m'

echo ""
echo "  ${_c}╔══════════════════════════════════════════════════════════════╗${_r}"
echo "  ${_c}║  'MiOS'"
echo "  ${_c}╚══════════════════════════════════════════════════════════════╝${_r}"
echo ""

if [[ -f "$_ENV_FILE" ]]; then
    echo "  ${_dim}Found saved config: $_ENV_FILE${_r}"
    read -rp "  Load previous build variables? [Y/n]: " _load_ok </dev/tty
    if [[ "${_load_ok,,}" != "n" ]]; then
        set +u
        # shellcheck source=/dev/null  # the operator's saved answers, written by this script's previous run
        source "$_ENV_FILE"
        set -u
        echo "  ${_g}[OK]${_r} Loaded"
        echo ""
    fi
fi

if [[ -z "${GHCR_TOKEN:-}" ]]; then
    read -rsp "  ${_b}GitHub PAT${_r} (requires 'repo' scope): " GHCR_TOKEN </dev/tty; echo ""
fi
if [[ -z "${GHCR_TOKEN:-}" ]]; then
    echo "  ${_red}[!] Token required.${_r}"; exit 1
fi
export GHCR_TOKEN

echo ""
echo "  ${_y}── Build Configuration ─────────────────────────────────────────${_r}"
echo ""

if [[ -z "${MIOS_IDENTITY_USERNAME:-}" ]]; then
    read -rp "  Admin username ${_dim}[user]${_r}: " MIOS_IDENTITY_USERNAME </dev/tty
    MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-user}"
else
    echo "  Admin username: ${MIOS_IDENTITY_USERNAME}  ${_dim}${_r}"
fi
export MIOS_IDENTITY_USERNAME

if [[ -z "${MIOS_PASSWORD:-}" ]]; then
    while true; do
        read -rsp "  Admin password: " MIOS_PASSWORD </dev/tty; echo ""
        [[ -z "${MIOS_PASSWORD:-}" ]] && { echo "  ${_red}[!] Password cannot be empty.${_r}"; continue; }
        read -rsp "  Confirm password: " _c2 </dev/tty; echo ""
        [[ "$MIOS_PASSWORD" == "$_c2" ]] && break
        echo "  ${_red}[!] Mismatch"
    done
else
    echo "  Admin password: ${_dim}${_r}"
fi
export MIOS_PASSWORD

if [[ -z "${MIOS_IDENTITY_HOSTNAME:-}" ]]; then
    _suf=$(shuf -i 10000-99999 -n1 2>/dev/null || printf '%05d' $(( RANDOM % 90000 + 10000 )))
    read -rp "  Hostname base ${_dim}[mios]${_r} (suffix -${_suf} is pre-generated -> mios-${_suf}): " _hbase </dev/tty
    _hbase="${_hbase:-mios}"
    export MIOS_IDENTITY_HOSTNAME="${_hbase}-${_suf}"
else
    echo "  Hostname: ${MIOS_IDENTITY_HOSTNAME}  ${_dim}${_r}"
fi

if [[ -z "${MIOS_GHCR_USER:-}" ]]; then
    echo ""
    read -rp "  GHCR push username ${_dim}[skip]${_r}: " MIOS_GHCR_USER </dev/tty
fi
export MIOS_GHCR_USER="${MIOS_GHCR_USER:-}"

if [[ -n "$MIOS_GHCR_USER" && -z "${MIOS_GHCR_PUSH_TOKEN:-}" ]]; then
    read -rsp "  GHCR push token ${_dim}[reuse GitHub PAT]${_r}: " MIOS_GHCR_PUSH_TOKEN </dev/tty; echo ""
    export MIOS_GHCR_PUSH_TOKEN="${MIOS_GHCR_PUSH_TOKEN:-$GHCR_TOKEN}"
fi

echo ""
echo "  ${_y}── Summary ──────────────────────────────────────────────────────${_r}"
echo ""
printf "    %-20s %s\n" "Admin user:"     "$MIOS_IDENTITY_USERNAME"
printf "    %-20s %s\n" "Admin password:" "(masked)"
printf "    %-20s %s\n" "Hostname:"       "$MIOS_IDENTITY_HOSTNAME"
printf "    %-20s %s\n" "Registry push:"  "${MIOS_GHCR_USER:-none (local build only)}"
printf "    %-20s %s\n" "Config saved to:" "$_ENV_FILE"
echo ""
read -rp "  ${_b}Proceed?${_r} [Y/n]: " _ok </dev/tty
[[ "${_ok,,}" == "n" ]] && { echo "  Aborted"; exit 0; }

mkdir -p "$(dirname "$_ENV_FILE")"
{
    printf '# 'MiOS' Build Configuration\n'
    printf '# Generated: %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf 'GHCR_TOKEN=%q\n'    "$GHCR_TOKEN"
    printf 'MIOS_IDENTITY_USERNAME=%q\n'     "$MIOS_IDENTITY_USERNAME"
    printf 'MIOS_PASSWORD=%q\n' "$MIOS_PASSWORD"
    printf 'MIOS_IDENTITY_HOSTNAME=%q\n' "$MIOS_IDENTITY_HOSTNAME"
    [[ -n "${MIOS_GHCR_USER:-}" ]]       && printf 'MIOS_GHCR_USER=%q\n'       "$MIOS_GHCR_USER"
    [[ -n "${MIOS_GHCR_PUSH_TOKEN:-}" ]] && printf 'MIOS_GHCR_PUSH_TOKEN=%q\n' "$MIOS_GHCR_PUSH_TOKEN"
} > "$_ENV_FILE"
chmod 600 "$_ENV_FILE"
echo "  ${_g}[OK]${_r} Build config saved → ${_dim}$_ENV_FILE${_r}"

export MIOS_AUTOINSTALL=1
echo ""
echo "  [+] Fetching private installer"
_tmp=$(mktemp /tmp/mios-install-XXXXXX.sh)
if curl -fsSL -H "Authorization: token $GHCR_TOKEN" "$PRIVATE_INSTALLER" -o "$_tmp"; then
    chmod +x "$_tmp"
    echo "  ${_g}[OK]${_r} Launching installer"
    echo ""
    bash "$_tmp"
    rm -f "$_tmp"
else
    rm -f "$_tmp"
    echo "  ${_red}[!] Failed to fetch installer. Check token and repo permissions.${_r}"
    exit 1
fi
