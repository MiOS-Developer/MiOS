#!/usr/bin/env bash
# AI-hint: bash Executes the full MiOS system bootstrap to transform a bare Fedora host into a complete MiOS workstation by installing all core components, ...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

DEFAULT_USER="user"
DEFAULT_HOST="user"
DEFAULT_USER_FULLNAME="User"
DEFAULT_USER_SHELL="/bin/bash"
DEFAULT_USER_GROUPS="wheel,libvirt,kvm,video,render,input,dialout"
# shellcheck disable=SC2034
DEFAULT_SSH_KEY_TYPE="ed25519"
DEFAULT_BRANCH="main"

MIOS_REPO="https://github.com/mios-dev/MiOS.git"

: "${MIOS_USR_DIR:=/usr/lib/mios}"
: "${MIOS_LIBEXEC_DIR:=/usr/libexec/mios}"
: "${MIOS_SHARE_DIR:=/usr/share/mios}"
: "${MIOS_ETC_DIR:=/etc/mios}"
: "${MIOS_VAR_DIR:=/var/lib/mios}"
PROFILE_DIR="${MIOS_ETC_DIR}"
# shellcheck disable=SC2034
PROFILE_FILE="${PROFILE_DIR}/install.env"

_BOLD=$(tput bold 2>/dev/null || echo "")
_RED=$(tput setaf 1 2>/dev/null || echo "")
_GREEN=$(tput setaf 2 2>/dev/null || echo "")
_YELLOW=$(tput setaf 3 2>/dev/null || echo "")
_CYAN=$(tput setaf 6 2>/dev/null || echo "")
_DIM=$(tput dim 2>/dev/null || echo "")
_RESET=$(tput sgr0 2>/dev/null || echo "")

log_info()  { printf '%s[INFO]%s %s\n' "${_CYAN}" "${_RESET}" "$*"; }
log_ok()    { printf '%s[ OK ]%s %s\n' "${_GREEN}" "${_RESET}" "$*"; }
log_warn()  { printf '%s[WARN]%s %s\n' "${_YELLOW}" "${_RESET}" "$*" >&2; }
log_err()   { printf '%s[ERR ]%s %s\n' "${_RED}" "${_RESET}" "$*" >&2; }
log_phase() { printf '\n%s%s== %s ==%s\n\n' "${_BOLD}" "${_CYAN}" "$*" "${_RESET}"; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_err "Bootstrap must run as root: sudo $0"
        exit 1
    fi
}

detect_host_kind() {
    if command -v bootc >/dev/null 2>&1 && bootc status --format=json 2>/dev/null | grep -q '"booted"'; then
        echo "Bootc"
    elif [[ -f /etc/os-release ]] && grep -qE '^ID(_LIKE)?=.*fedora' /etc/os-release; then
        echo "Fhs-fedora"
    else
        echo "Unsupported"
    fi
}

check_network() {
    local host="github.com"
    if ! curl -fsSL --retry 3 --max-time 5 -o /dev/null "https://${host}/" 2>/dev/null; then
        log_err "No network reachability to ${host}."
        exit 1
    fi
    log_ok "Network reachability verified"
}

prompt_default() {
    local question="$1" default="$2" answer
    read -r -p "$(printf '%s%s%s [%s%s%s]: ' "${_BOLD}" "${question}" "${_RESET}" "${_DIM}" "${default}" "${_RESET}")" answer
    echo "${answer:-$default}"
}

prompt_password() {
    local prompt="$1" pw1 pw2
    while :; do
        printf '%s%s%s: ' "${_BOLD}" "${prompt}" "${_RESET}" >&2
        read -rs pw1; echo >&2
        printf '%sConfirm:%s ' "${_BOLD}" "${_RESET}" >&2
        read -rs pw2; echo >&2
        if [[ "$pw1" == "$pw2" && -n "$pw1" ]]; then
            echo "$pw1"
            return 0
        fi
        log_warn "Passwords don't match or are empty."
    done
}

prompt_yesno() {
    local question="$1" default="${2:-y}" answer hint
    if [[ "$default" == "y" ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
    read -r -p "$(printf '%s%s%s %s: ' "${_BOLD}" "${question}" "${_RESET}" "${hint}")" answer
    answer="${answer:-$default}"
    case "${answer,,}" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

main() {
    require_root
    log_phase "'MiOS' Bootstrap Installer (clone repo + install full [packages.*] manifest + FHS overlay)"

    local hostkind
    hostkind="$(detect_host_kind)"
    if [[ "${hostkind,,}" == "unsupported" ]]; then
        log_err "Host is not Fedora. 'MiOS' requires a Fedora-based host."
        exit 1
    fi
    log_info "Detected host: ${hostkind}"

    check_network

    LINUX_USER="$(prompt_default 'Linux username' "${DEFAULT_USER}")"
    HOSTNAME_VAL="$(prompt_default 'Hostname' "${DEFAULT_HOST}")"
    USER_FULLNAME="$(prompt_default 'Full name (GECOS)' "${DEFAULT_USER_FULLNAME}")"
    USER_PASSWORD="$(prompt_password 'Password')"

    log_phase "Review profile"
    printf "  User: %s\n  Host: %s\n  Mode: Total Root Overlay\n\n" "$LINUX_USER" "$HOSTNAME_VAL"
    if ! prompt_yesno 'Proceed with these settings?' y; then exit 0; fi

    log_phase "Applying system profile"
    hostnamectl set-hostname "$HOSTNAME_VAL"

    local existing_groups=""
    IFS=',' read -ra ADDR <<< "${DEFAULT_USER_GROUPS}"
    for group in "${ADDR[@]}"; do
        if getent group "$group" >/dev/null; then
            [[ -n "$existing_groups" ]] && existing_groups+=","
            existing_groups+="$group"
        else
            log_warn "Group '$group' missing on host, skipping."
        fi
    done

    if id -u "$LINUX_USER" >/dev/null 2>&1; then
        log_info "User '$LINUX_USER' exists; updating groups + password"
        usermod -aG "$existing_groups" "$LINUX_USER"
        usermod -c "$USER_FULLNAME" "$LINUX_USER"
    else
        log_info "Creating '$LINUX_USER' (groups: $existing_groups)"
        useradd -u 1000 -m -G "$existing_groups" -s "$DEFAULT_USER_SHELL" -c "$USER_FULLNAME" "$LINUX_USER" 2>/dev/null || useradd -m -G "$existing_groups" -s "$DEFAULT_USER_SHELL" -c "$USER_FULLNAME" "$LINUX_USER"
    fi
    echo "$LINUX_USER:$USER_PASSWORD" | chpasswd
    chage -d 0 "$LINUX_USER" 2>/dev/null || true
    log_ok "User profile applied."

    log_phase "'MiOS' Core Installation (Root Merge)"
    log_info "Cloning 'MiOS' repository to staging area..."
    MIOS_STAGE="$(mktemp -d /tmp/mios-stage-XXXXXX)"
    trap 'rm -rf "${MIOS_STAGE}"' EXIT
    git clone --depth=1 --branch "$DEFAULT_BRANCH" "$MIOS_REPO" "${MIOS_STAGE}"
    log_ok "Repository cloned to ${MIOS_STAGE}"

    log_info "Rsyncing usr/etc/var/srv from ${MIOS_STAGE} into / (rsync -aH, overwrites on content diff)"
    for d in usr etc var srv; do
        if [[ -d "${MIOS_STAGE}/${d}" ]]; then
            log_info "  Merging ${d}/ ..."
            rsync -aH --info=stats1 "${MIOS_STAGE}/${d}/" "/${d}/"
        fi
    done
    if [[ -d "${MIOS_STAGE}/v1" ]]; then
        log_info "  Materializing /v1 discovery surface..."
        install -d /v1
        rsync -aH "${MIOS_STAGE}/v1/" "/v1/"
    fi
    log_ok "'MiOS' source tree merged to root."

    log_phase "Installing 'MiOS' System Stack"
    local toml_path="${MIOS_SHARE_DIR}/mios.toml"
    [[ -f "$toml_path" ]] || toml_path="${MIOS_STAGE}/usr/share/mios/mios.toml"
    if [[ ! -f "$toml_path" ]]; then
        log_err "CRITICAL: mios.toml SSOT not found at ${MIOS_SHARE_DIR}/ or staging."
        exit 1
    fi

    log_info "Sourcing package resolver from ${MIOS_STAGE}/automation/lib/packages.sh"
    export MIOS_TOML="$toml_path"
    export MIOS_VENDOR_TOML="${MIOS_VENDOR_TOML:-$MIOS_TOML}"
    source "${MIOS_STAGE}/automation/lib/packages.sh"

    local section pkgs all_pkgs=""
    for section in $(awk -F'[][.]' '/^\[packages\./ { print $3 }' "$toml_path" | sort -u); do
        pkgs=$(get_packages "$section" 2>/dev/null || true)
        [[ -n "${pkgs// }" ]] && all_pkgs+=" $pkgs"
    done

    if [[ -n "${all_pkgs// }" ]]; then
        local dnf_cmd="dnf"
        command -v dnf5 >/dev/null 2>&1 && dnf_cmd="dnf5"
        log_info "Executing: $dnf_cmd install -y --best [PACKAGES]"
        if ! $dnf_cmd install -y --best $all_pkgs; then
            log_err "Package stack installation failed."
            return 1
        fi
        log_ok "Package stack installation complete."
    else
        log_err "No [packages.*] sections found in ${toml_path}!"
        exit 1
    fi

    log_phase "System Initialization"
    if [[ -x "/install.sh" ]]; then
        log_info "Running /install.sh to finalize FHS overlay..."
        /install.sh
        log_ok "Initialization complete."
    else
        log_err "/install.sh not found or not executable!"
        exit 1
    fi

    log_phase "'MiOS' Installation Complete"
    if prompt_yesno 'Reboot now to enter 'MiOS'?' y; then
        systemctl reboot
    fi
}

main "$@"
