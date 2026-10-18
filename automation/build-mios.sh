#!/bin/bash
# AI-hint: This script is the primary installation and ignition tool for MiOS; an agent uses it to clone the MiOS repository and merge its components into the Fedora ...
# AI-doc: usr/share/doc/mios/manual/automation.md

set -euo pipefail

MIOS_REPO_URL="${MIOS_REPO_URL:-https://github.com/mios-dev/MiOS-bootstrap.git}"
MIOS_REPO_BRANCH="${MIOS_REPO_BRANCH:-main}"
MIOS_TMP_DIR="/tmp/mios-ignition-$$"
MIOS_INSTALL_LOG="/var/log/mios-ignition.log"

: "${MIOS_USR_DIR:=/usr/lib/mios}"
: "${MIOS_LIBEXEC_DIR:=/usr/libexec/mios}"
: "${MIOS_SHARE_DIR:=/usr/share/mios}"
: "${MIOS_ETC_DIR:=/etc/mios}"
: "${MIOS_VAR_DIR:=/var/lib/mios}"
MIOS_CONFIG_DIR="${MIOS_ETC_DIR}"
MIOS_USER_CONFIG_DIR="" # Will be set after user is determined

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $*" | tee -a "$MIOS_INSTALL_LOG"
}

log_warn() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARN:${NC} $*" | tee -a "$MIOS_INSTALL_LOG"
}

log_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $*" | tee -a "$MIOS_INSTALL_LOG"
}

log_info() {
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')] INFO:${NC} $*" | tee -a "$MIOS_INSTALL_LOG"
}

show_banner() {
    cat << EOF
â*"â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*--
â*'                   'MiOS' Fedora Server Ignition                            â*'
â*'                         Version ${MIOS_VERSION:-Unknown}                                    â*'
â*šâ*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*

This script will:
  1. Fetch 'MiOS' repository from GitHub
  2. Prompt for user configuration (username, hostname, etc.)
  3. Queue environment files and dotfiles
  4. Merge 'MiOS' structure onto Fedora Server (FHS-compliant)
  5. NO deletions - only additions and updates
  6. Build 'MiOS' OCI image

EOF
}

collect_user_config() {
    log_info "Collecting user configuration..."
    echo ""

    # Defaults are the LAYERED SSOT values (vendor < host < user + mios.d) -- never a literal.
    local _cred=""
    for _c in "$(dirname "${BASH_SOURCE[0]}")/../usr/libexec/mios/mios-credential" /usr/libexec/mios/mios-credential; do
        [[ -f "$_c" ]] && { _cred="$_c"; break; }
    done
    local _ssot_user=""
    [[ -n "$_cred" ]] && _ssot_user="$(python3 "$_cred" username 2>/dev/null || true)"

    read -p "Enter username (default: ${_ssot_user:-<none: SSOT [identity].username unset>}): " MIOS_USERNAME
    MIOS_USERNAME="${MIOS_USERNAME:-$_ssot_user}"
    if [[ -z "$MIOS_USERNAME" ]]; then
        log_error "No username given and SSOT [identity].username is unset -- refusing a literal default."
        return 1
    fi

    while true; do
        read -sp "Enter password for ${MIOS_USERNAME} (Enter = SSOT [identity].default_password): " MIOS_PASSWORD
        echo ""
        if [[ -z "$MIOS_PASSWORD" ]]; then
            # Use the SSOT credential: hash it (never echo it) and keep the plaintext only for chpasswd.
            MIOS_USER_PASSWORD_HASH="$([[ -n "$_cred" ]] && python3 "$_cred" hash 2>/dev/null || true)"
            if [[ -z "$MIOS_USER_PASSWORD_HASH" ]]; then
                log_error "SSOT [identity].default_password is unset -- enter a password."
                continue
            fi
            MIOS_PASSWORD=""
            break
        fi
        read -sp "Confirm password: " MIOS_PASSWORD_CONFIRM
        echo ""

        if [[ "$MIOS_PASSWORD" == "$MIOS_PASSWORD_CONFIRM" ]]; then
            MIOS_USER_PASSWORD_HASH=$(printf '%s\n' "$MIOS_PASSWORD" | openssl passwd -6 -stdin)
            break
        else
            log_error "Passwords do not match. Please try again."
        fi
    done

    read -p "Enter hostname (default: mios): " MIOS_IDENTITY_HOSTNAME
    MIOS_IDENTITY_HOSTNAME="${MIOS_IDENTITY_HOSTNAME:-mios}"

    echo ""
    echo "Select base image:"
    echo "  1) ghcr.io/ublue-os/ucore-hci:stable-nvidia"
    echo "  2) ghcr.io/ublue-os/ucore-hci:stable"
    echo "  3) ghcr.io/ublue-os/ucore:stable"
    echo "  4) Custom"
    read -p "Choice [1-4] (default: 1): " BASE_IMAGE_CHOICE
    BASE_IMAGE_CHOICE="${BASE_IMAGE_CHOICE:-1}"

    case "$BASE_IMAGE_CHOICE" in
        1) MIOS_BASE_IMAGE="ghcr.io/ublue-os/ucore-hci:stable-nvidia" ;;
        2) MIOS_BASE_IMAGE="ghcr.io/ublue-os/ucore-hci:stable" ;;
        3) MIOS_BASE_IMAGE="ghcr.io/ublue-os/ucore:stable" ;;
        4)
            read -p "Enter custom base image: " MIOS_BASE_IMAGE
            ;;
        *) MIOS_BASE_IMAGE="ghcr.io/ublue-os/ucore-hci:stable-nvidia" ;;
    esac

    echo ""
    read -p "Enter Flatpak app IDs (comma-separated, optional): " MIOS_FLATPAKS_INPUT
    MIOS_DESKTOP_FLATPAKS="${MIOS_FLATPAKS_INPUT}"

    echo ""
    read -p "Configure AI settings? (y/N): " CONFIGURE_AI
    if [[ "$CONFIGURE_AI" =~ ^[Yy]$ ]]; then
        read -p "AI Model (default: llama3.1:8b): " MIOS_AI_MODEL
        MIOS_AI_MODEL="${MIOS_AI_MODEL:-llama3.1:8b}"

        read -p "AI Endpoint (default: http://localhost:8642/v1): " MIOS_AI_ENDPOINT
        MIOS_AI_ENDPOINT="${MIOS_AI_ENDPOINT:-http://localhost:8642/v1}"

        read -sp "AI API Key (optional, press Enter to skip): " MIOS_AI_KEY
        echo ""
    else
        MIOS_AI_MODEL="llama3.1:8b"
        MIOS_AI_ENDPOINT="http://localhost:8642/v1"
        MIOS_AI_KEY=""
    fi

    echo ""
    log_info "Configuration Summary:"
    echo "  Username:     $MIOS_USERNAME"
    echo "  Hostname:     $MIOS_IDENTITY_HOSTNAME"
    echo "  Base Image:   $MIOS_BASE_IMAGE"
    echo "  Flatpaks:     ${MIOS_DESKTOP_FLATPAKS:-none}"
    echo "  AI Model:     $MIOS_AI_MODEL"
    echo "  AI Endpoint:  $MIOS_AI_ENDPOINT"
    echo ""

    read -p "Proceed with this configuration? (y/N): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_error "Installation cancelled by user."
        exit 1
    fi
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi

    if [[ ! -f /etc/fedora-release ]]; then
        log_warn "This script is designed for Fedora Server. Detected OS: $(cat /etc/os-release | grep PRETTY_NAME || echo 'Unknown')"
        read -p "Continue anyway? (y/N): " CONTINUE
        if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    if ! curl -fsSL --retry 3 --max-time 5 -o /dev/null https://github.com/; then
        log_error "No internet connection. Please check your network."
        exit 1
    fi

    log "Prerequisites check passed"
}

install_dependencies() {
    log_info "Installing required dependencies..."

    dnf install -y \
        git \
        podman \
        buildah \
        rsync \
        python3 \
        systemd \
        coreutils \
        util-linux \
        || { log_error "Failed to install dependencies"; exit 1; }

    if ! command -v just &>/dev/null; then
        log_info "Installing 'just' command runner..."
        if command -v cargo &>/dev/null; then
            cargo install just || log_warn "'just' installation failed, continuing without it"
        else
            log_warn "'just' not installed (cargo not available). You can use podman directly."
        fi
    fi

    log "Dependencies installed successfully"
}

fetch_mios_repo() {
    log_info "Fetching 'MiOS' repository from ${MIOS_REPO_URL}..."

    mkdir -p "$MIOS_TMP_DIR"
    cd "$MIOS_TMP_DIR"

    git clone --depth 1 --branch "$MIOS_REPO_BRANCH" "$MIOS_REPO_URL" mios \
        || { log_error "Failed to clone 'MiOS' repository"; exit 1; }

    cd mios

    log "'MiOS' repository fetched successfully"
}

queue_environment_files() {
    log_info "Queuing environment files and dotfiles..."

    if [[ "$MIOS_USERNAME" == "root" ]]; then
        MIOS_USER_HOME="/root"
    else
        MIOS_USER_HOME="/home/${MIOS_USERNAME}"
    fi

    MIOS_USER_CONFIG_DIR="${MIOS_USER_HOME}/.config/mios"

    mkdir -p "$MIOS_USER_CONFIG_DIR"

    {
        echo "# ~/.config/mios/mios.toml"
        echo "# Generated: $"
        echo ""
        echo "[user]"
        echo "Name     = \"${MIOS_USERNAME}\""
        echo "Hostname = \"${MIOS_IDENTITY_HOSTNAME}\""
        echo ""
        echo "[image]"
        echo "Base = \"${MIOS_BASE_IMAGE}\""
        echo "Bib  = \"quay.io/centos-bootc/bootc-image-builder:latest\""
        echo ""
        echo "[build]"
        echo "Local_tag = \"localhost/mios:latest\""
        echo ""
        echo "[flatpaks]"
        if [[ -n "$MIOS_DESKTOP_FLATPAKS" ]]; then
            echo "Install = ["
            echo "$MIOS_DESKTOP_FLATPAKS" | tr ',' '\n' | while read -r f; do
                f="$(echo "$f" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
                [[ -z "$f" ]] && continue
                echo "    \"$f\","
            done
            echo "]"
        else
            echo "# install = []"
        fi
        echo ""
        echo "[ai]"
        echo "Model    = \"${MIOS_AI_MODEL}\""
        echo "Endpoint = \"${MIOS_AI_ENDPOINT}\""
    } > "$MIOS_USER_CONFIG_DIR/mios.toml"

    if [[ -n "$MIOS_AI_KEY" ]]; then
        cat > "$MIOS_USER_CONFIG_DIR/ai.env" <<EOF
MIOS_AI_KEY="${MIOS_AI_KEY}"
EOF
        chmod 600 "$MIOS_USER_CONFIG_DIR/ai.env"
    fi

    log "Environment files queued successfully"
}

merge_mios_structure() {
    log_info "Merging 'MiOS' structure onto Fedora Server root (FHS-compliant)..."

    cd "$MIOS_TMP_DIR/mios"

    log_info "Merging /usr..."
    rsync -av --ignore-existing usr/ /usr/ \
        || log_warn "Some files in /usr were skipped (already exist)"

    log_info "Merging /etc..."
    rsync -av --ignore-existing etc/ /etc/ \
        || log_warn "Some files in /etc were skipped (already exist)"

    log_info "Declaring /var directories via tmpfiles.d..."
    if [[ -f usr/lib/tmpfiles.d/50-mios.conf ]]; then
        cp -n usr/lib/tmpfiles.d/50-mios.conf /usr/lib/tmpfiles.d/ || true
        systemd-tmpfiles --create /usr/lib/tmpfiles.d/50-mios.conf || log_warn "tmpfiles creation had warnings"
    fi

    log_info "Merging /home skeleton..."
    if [[ -d home/mios ]]; then
        mkdir -p /etc/skel/.config/mios
        rsync -av --ignore-existing home/mios/ /etc/skel/ || true
    fi

    log_info "Installing tools and automation..."
    rsync -av tools/ ${MIOS_SHARE_DIR}/tools/ || true
    rsync -av automation/ ${MIOS_SHARE_DIR}/automation/ || true

    log_info "Setting executable permissions..."
    chmod +x /usr/bin/mios* /usr/bin/iommu-groups 2>/dev/null || true
    chmod +x /usr/libexec/mios* 2>/dev/null || true
    chmod +x ${MIOS_LIBEXEC_DIR}/* 2>/dev/null || true
    chmod +x ${MIOS_SHARE_DIR}/tools/*.sh 2>/dev/null || true
    chmod +x ${MIOS_SHARE_DIR}/automation/*.sh 2>/dev/null || true

    log_info "Installing build files..."
    cp -n Containerfile ${MIOS_SHARE_DIR}/ || true
    cp -n Justfile ${MIOS_SHARE_DIR}/ || true
    cp -n VERSION ${MIOS_SHARE_DIR}/ || true

    log_info "Creating source symlink..."
    ln -sf ${MIOS_SHARE_DIR} /usr/src/mios || true

    log "'MiOS' structure merged successfully"
}

create_user_account() {
    log_info "Creating user account: ${MIOS_USERNAME}..."

    if id "$MIOS_USERNAME" &>/dev/null; then
        log_warn "User ${MIOS_USERNAME} already exists, updating password..."
        echo "${MIOS_USERNAME}:${MIOS_USER_PASSWORD_HASH}" | chpasswd -e
        chage -d 0 "$MIOS_USERNAME" 2>/dev/null || true
    else
        EXTRA_GROUPS="wheel,libvirt,kvm,video,render,input,dialout"
        if getent group docker >/dev/null 2>&1; then EXTRA_GROUPS="$EXTRA_GROUPS,docker"; fi
        useradd -u 1000 -m -G "$EXTRA_GROUPS" -s /bin/bash "$MIOS_USERNAME" 2>/dev/null || useradd -m -G "$EXTRA_GROUPS" -s /bin/bash "$MIOS_USERNAME"
        echo "${MIOS_USERNAME}:${MIOS_USER_PASSWORD_HASH}" | chpasswd -e
        chage -d 0 "$MIOS_USERNAME" 2>/dev/null || true

        install -d -m 0750 /etc/sudoers.d
        echo "${MIOS_USERNAME} ALL= NOPASSWD:ALL" > "/etc/sudoers.d/${MIOS_USERNAME}"
        chmod 0440 "/etc/sudoers.d/${MIOS_USERNAME}"
    fi

    install -d -o "$MIOS_USERNAME" -g "$MIOS_USERNAME" -m 0755 "${MIOS_USER_HOME}"
    install -d -o "$MIOS_USERNAME" -g "$MIOS_USERNAME" -m 0755 "${MIOS_USER_HOME}/.ssh"
    install -d -o "$MIOS_USERNAME" -g "$MIOS_USERNAME" -m 0755 "${MIOS_USER_HOME}/.config"
    install -d -o "$MIOS_USERNAME" -g "$MIOS_USERNAME" -m 0755 "${MIOS_USER_HOME}/.local/share"
    install -d -o "$MIOS_USERNAME" -g "$MIOS_USERNAME" -m 0755 "${MIOS_USER_HOME}/.cache"
    install -d -o "$MIOS_USERNAME" -g "$MIOS_USERNAME" -m 0755 "${MIOS_USER_HOME}/.local/state"

    log_info "Initializing user-space directories and configuration..."

    MIOS_USER_DATA_DIR="${MIOS_USER_HOME}/.local/share/mios"
    MIOS_USER_CACHE_DIR="${MIOS_USER_HOME}/.cache/mios"
    MIOS_USER_STATE_DIR="${MIOS_USER_HOME}/.local/state/mios"

    mkdir -p "${MIOS_USER_CONFIG_DIR}/credentials/ssh-keys"
    mkdir -p "${MIOS_USER_DATA_DIR}/artifacts"
    mkdir -p "${MIOS_USER_DATA_DIR}/images"
    mkdir -p "${MIOS_USER_DATA_DIR}/templates"
    mkdir -p "${MIOS_USER_DATA_DIR}/plugins"
    mkdir -p "${MIOS_USER_CACHE_DIR}/podman"
    mkdir -p "${MIOS_USER_CACHE_DIR}/downloads"
    mkdir -p "${MIOS_USER_CACHE_DIR}/build-cache"
    mkdir -p "${MIOS_USER_STATE_DIR}/logs"

    mkdir -p "${MIOS_USER_CONFIG_DIR}/dotfiles"
    if [[ ! -f "${MIOS_USER_CONFIG_DIR}/dotfiles/.bashrc.user" ]]; then
        cat > "${MIOS_USER_CONFIG_DIR}/dotfiles/.bashrc.user" <<'DOTFILE_EOF'
alias ll='ls -alF'
export EDITOR=vim
DOTFILE_EOF
    fi

    cat > "${MIOS_USER_CONFIG_DIR}/credentials/.gitignore" <<'GITIGNORE_EOF'

*
!.gitignore
!README.md
GITIGNORE_EOF

    if command -v python3 &>/dev/null; then
        if [[ ! -d "${MIOS_USER_DATA_DIR}/venv" ]]; then
            python3 -m venv "${MIOS_USER_DATA_DIR}/venv" 2>/dev/null || log_warn "Failed to create Python venv"
        fi
    fi

    chown -R "${MIOS_USERNAME}:${MIOS_USERNAME}" "${MIOS_USER_HOME}/.config" 2>/dev/null || true
    chown -R "${MIOS_USERNAME}:${MIOS_USERNAME}" "${MIOS_USER_HOME}/.local" 2>/dev/null || true
    chown -R "${MIOS_USERNAME}:${MIOS_USERNAME}" "${MIOS_USER_HOME}/.cache" 2>/dev/null || true

    log "User account and user-space configured successfully"
}

set_hostname() {
    log_info "Setting hostname to: ${MIOS_IDENTITY_HOSTNAME}..."

    hostnamectl set-hostname "$MIOS_IDENTITY_HOSTNAME"

    log "Hostname set successfully"
}

build_mios_image() {
    log_info "Would you like to build the 'MiOS' OCI image now?"
    echo "  This will take 15-25 minutes on first build"
    echo "  You can also build later with: cd ${MIOS_SHARE_DIR} && just build"
    echo ""
    read -p "Build now? (y/N): " BUILD_NOW

    if [[ "$BUILD_NOW" =~ ^[Yy]$ ]]; then
        log_info "Building 'MiOS' OCI image..."

        cd ${MIOS_SHARE_DIR}

        export MIOS_BASE_IMAGE
        export MIOS_DESKTOP_FLATPAKS
        export MIOS_IDENTITY_USERNAME="${MIOS_USERNAME}"
        # 11-user.sh reads MIOS_USER_PASSWORD_HASH (was exported as MIOS_PASSWORD_HASH, which the
        # image never read, so it silently fell back to a literal).
        export MIOS_USER_PASSWORD_HASH
        export MIOS_IDENTITY_HOSTNAME

        SOURCE_DATE_EPOCH=$(git -C "${MIOS_SHARE_DIR:-.}" log -1 --format=%ct 2>/dev/null || date +%s)
        export SOURCE_DATE_EPOCH

        if command -v just &>/dev/null; then
            just build || { log_error "Build failed"; return 1; }
        else
            podman build --no-cache \
                --timestamp "$SOURCE_DATE_EPOCH" \
                --build-arg SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" \
                --build-arg BASE_IMAGE="$MIOS_BASE_IMAGE" \
                --build-arg MIOS_IDENTITY_USERNAME="$MIOS_USERNAME" \
                --build-arg MIOS_USER_PASSWORD_HASH="$MIOS_USER_PASSWORD_HASH" \
                --build-arg MIOS_IDENTITY_HOSTNAME="$MIOS_IDENTITY_HOSTNAME" \
                --build-arg MIOS_DESKTOP_FLATPAKS="$MIOS_DESKTOP_FLATPAKS" \
                -t localhost/mios:latest . \
                || { log_error "Build failed"; return 1; }
        fi

        log "'MiOS' OCI image built successfully: localhost/mios:latest"

        echo ""
        read -p "Deploy to this system now? (y/N): " DEPLOY_NOW
        if [[ "$DEPLOY_NOW" =~ ^[Yy]$ ]]; then
            log_info "Deploying 'MiOS' to this system..."
            bootc install to-existing-root --source-imgref localhost/mios:latest \
                || log_warn "Deployment failed or not supported on this system"
        fi
    else
        log_info "Skipping build. To build later, run:"
        echo "  cd ${MIOS_SHARE_DIR} && just build"
    fi
}

cleanup() {
    log_info "Cleaning up temporary files..."

    if [[ -d "$MIOS_TMP_DIR" ]]; then
        rm -rf "$MIOS_TMP_DIR"
    fi

    log "Cleanup complete"
}

show_summary() {
    cat << EOF

â*"â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*--
â*'                   'MiOS' Installation Complete!                            â*'
â*šâ*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*â*

Configuration:
  Username:     ${MIOS_USERNAME}
  Hostname:     ${MIOS_IDENTITY_HOSTNAME}
  Config Dir:   ${MIOS_USER_CONFIG_DIR}

Installation Details:
  âœ" 'MiOS' structure merged to system root (FHS-compliant)
  âœ" User account created in groups wheel,libvirt,kvm,video,render,input,dialout with NOPASSWD sudoers drop-in
  âœ" User-space initialized (XDG directories, configs, dotfiles)
  âœ" Python virtual environment created at .local/share/mios/venv (skipped if python3 absent)
  âœ" /etc templates merged via rsync --ignore-existing
  âœ" Build files installed to ${MIOS_SHARE_DIR}

Next Steps:

  1. Switch to your user:
     su - ${MIOS_USERNAME}

  2. Build 'MiOS' image (if not done):
     cd ${MIOS_SHARE_DIR} && just build

  3. Check system status:
     mios status

  4. View available commands:
     mios --help

  5. Customize your configuration:
     \$EDITOR ~/.config/mios/mios.toml

Documentation:
  - Installation log: ${MIOS_INSTALL_LOG}
  - Configuration: ${MIOS_USER_CONFIG_DIR}
  - System config: ${MIOS_CONFIG_DIR}

For more information:
  https://github.com/mios-dev/MiOS-bootstrap

EOF
}

main() {
    mkdir -p "$(dirname "$MIOS_INSTALL_LOG")"
    touch "$MIOS_INSTALL_LOG"

    show_banner

    check_prerequisites
    collect_user_config
    install_dependencies
    fetch_mios_repo
    queue_environment_files
    merge_mios_structure
    create_user_account
    set_hostname
    build_mios_image
    cleanup
    show_summary

    log "'MiOS' Fedora Server ignition completed successfully"
}

trap 'log_error "Installation failed at line $LINENO. Check $MIOS_INSTALL_LOG for details."' ERR

main "$@"
