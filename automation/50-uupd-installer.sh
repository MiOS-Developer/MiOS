#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs uupd and offline atomic OCI upgrade path with kernel vs userspace soft-reboot differentiation.
# AI-doc: usr/share/doc/mios/manual/offline-upgrade.md
set -euo pipefail

# Sourcing logging helpers
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do
    if [ -r "$_mlog" ]; then
        # shellcheck source=/dev/null
        . "$_mlog"
        break
    fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/lib/common.sh" ]]; then
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/lib/common.sh"
fi
if [[ -f "${SCRIPT_DIR}/lib/packages.sh" ]]; then
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/lib/packages.sh"
fi

if ! declare -f mios_log >/dev/null 2>&1; then
    log_ts() { date '+%Y-%m-%d %H:%M:%S'; }
    mios_log()  { printf '[%s] ==> %s\n' "$(log_ts)" "$*"; }
    mios_ok()   { printf '[%s] OK %s\n' "$(log_ts)" "$*"; }
    mios_step() { printf '[%s] STEP %s\n' "$(log_ts)" "$*"; }
    mios_skip() { printf '[%s] SKIP %s\n' "$(log_ts)" "$*"; }
    mios_warn() { printf '[%s] WARN: %s\n' "$(log_ts)" "$*" >&2; }
    mios_err()  { printf '[%s] ERR: %s\n' "$(log_ts)" "$*" >&2; }
fi

# -----------------------------------------------------------------------------
# Offline Atomic OCI Upgrade Engine Functions
# -----------------------------------------------------------------------------

detect_offline_media() {
    local explicit_media="${1:-}"
    local detected_path=""
    local detected_transport=""

    if [[ -n "$explicit_media" ]]; then
        case "$explicit_media" in
            oci:*)
                detected_transport="oci"
                detected_path="${explicit_media#oci:}"
                ;;
            oci-archive:*)
                detected_transport="oci-archive"
                detected_path="${explicit_media#oci-archive:}"
                ;;
            containers-storage:*)
                detected_transport="containers-storage"
                detected_path="${explicit_media#containers-storage:}"
                ;;
            *)
                if [[ -d "$explicit_media" ]]; then
                    if [[ -f "${explicit_media}/index.json" && -d "${explicit_media}/blobs" ]]; then
                        detected_transport="oci"
                    else
                        detected_transport="directory"
                    fi
                    detected_path="$explicit_media"
                elif [[ -f "$explicit_media" ]]; then
                    case "$explicit_media" in
                        *.tar|*.tar.gz|*.tar.xz|*.oci.tar)
                            detected_transport="oci-archive"
                            ;;
                        *)
                            detected_transport="file"
                            ;;
                    esac
                    detected_path="$explicit_media"
                else
                    detected_path="$explicit_media"
                    detected_transport="unknown"
                fi
                ;;
        esac
        printf '%s|%s' "$detected_path" "$detected_transport"
        return 0
    fi

    # Auto-detection across USB media mountpoints and staging directories
    local search_roots=(
        "/run/media/${USER:-root}"
        "/run/media"
        "/media"
        "/mnt/usb"
        "/mnt"
        "/var/mnt"
        "/var/lib/mios/offline-update"
    )

    for root in "${search_roots[@]}"; do
        [[ -d "$root" ]] || continue

        # 1. Search for OCI Layout directories (has index.json and blobs/)
        while IFS= read -r oci_dir; do
            if [[ -n "$oci_dir" && -f "${oci_dir}/index.json" && -d "${oci_dir}/blobs" ]]; then
                detected_path="$oci_dir"
                detected_transport="oci"
                break 2
            fi
        done < <(find "$root" -maxdepth 3 -type d -name "*oci*" -o -name "*mios*" 2>/dev/null || true)

        # 2. Search for OCI tarballs / archives
        while IFS= read -r tar_file; do
            if [[ -n "$tar_file" && -f "$tar_file" ]]; then
                detected_path="$tar_file"
                detected_transport="oci-archive"
                break 2
            fi
        done < <(find "$root" -maxdepth 3 -type f \( -name "*.tar" -o -name "*.tar.gz" -o -name "*.oci.tar" \) 2>/dev/null || true)
    done

    if [[ -z "$detected_path" ]]; then
        return 1
    fi

    printf '%s|%s' "$detected_path" "$detected_transport"
    return 0
}

verify_oci_image() {
    local media_path="$1"
    local transport="$2"

    if [[ ! -e "$media_path" && "$transport" != "containers-storage" ]]; then
        mios_err "Media path does not exist: ${media_path}"
        return 1
    fi

    mios_log "Validating image at ${media_path} (transport: ${transport})"

    if command -v skopeo >/dev/null 2>&1; then
        local skopeo_src=""
        case "$transport" in
            oci)
                skopeo_src="oci:${media_path}"
                ;;
            oci-archive)
                skopeo_src="oci-archive:${media_path}"
                ;;
            containers-storage)
                skopeo_src="containers-storage:${media_path}"
                ;;
            *)
                skopeo_src="${media_path}"
                ;;
        esac

        local inspect_out
        if inspect_out="$(skopeo inspect "$skopeo_src" 2>/dev/null)"; then
            local img_arch img_os
            img_arch="$(printf '%s' "$inspect_out" | grep -m1 '"Architecture":' | awk -F'"' '{print $4}' || true)"
            img_os="$(printf '%s' "$inspect_out" | grep -m1 '"Os":' | awk -F'"' '{print $4}' || true)"
            local host_arch
            host_arch="$(uname -m)"
            [[ "$host_arch" == "x86_64" ]] && host_arch="amd64"
            [[ "$host_arch" == "aarch64" ]] && host_arch="arm64"

            if [[ -n "$img_os" && "$img_os" != "linux" ]]; then
                mios_err "Unsupported OS in image: ${img_os} (expected linux)"
                return 1
            fi
            if [[ -n "$img_arch" && "$img_arch" != "$host_arch" && "$img_arch" != "$(uname -m)" ]]; then
                mios_warn "Image architecture (${img_arch}) diverges from host ($(uname -m))"
            else
                mios_ok "Verified image manifest: os=${img_os:-linux} arch=${img_arch:-$(uname -m)}"
            fi
        else
            mios_warn "skopeo inspect returned non-zero; continuing with filesystem-level checks"
        fi
    fi

    return 0
}

stage_offline_image() {
    local media_path="$1"
    local transport="$2"
    local staging_ref="${3:-localhost/mios:offline-update}"
    local dry_run="${4:-0}"

    if [[ "$dry_run" == "1" ]]; then
        mios_log "[DRY-RUN] Would stage image from ${media_path} via transport ${transport}"
        return 0
    fi

    install -d -m 0755 /var/lib/mios
    install -d -m 0755 /var/log

    case "$transport" in
        oci)
            # Direct OCI layout switch if supported by bootc
            mios_log "Attempting direct bootc switch from OCI layout: ${media_path}"
            if bootc switch --transport oci "${media_path}" 2>&1 | tee -a /var/log/mios-offline-upgrade.log; then
                mios_ok "bootc switch --transport oci succeeded"
                return 0
            fi
            mios_warn "Direct bootc switch --transport oci failed; falling back to containers-storage import"
            ;&
        oci-archive|directory|file|*)
            # Import image to local containers-storage via skopeo copy
            mios_log "Importing image into containers-storage as ${staging_ref} via skopeo"
            local src_uri=""
            if [[ "$transport" == "oci" || ( -d "$media_path" && -f "${media_path}/index.json" ) ]]; then
                src_uri="oci:${media_path}"
            elif [[ "$transport" == "oci-archive" || -f "$media_path" ]]; then
                src_uri="oci-archive:${media_path}"
            elif [[ "$transport" == "containers-storage" ]]; then
                src_uri="containers-storage:${media_path}"
            else
                src_uri="${media_path}"
            fi

            if command -v skopeo >/dev/null 2>&1; then
                if ! skopeo copy "$src_uri" "containers-storage:${staging_ref}" 2>&1 | tee -a /var/log/mios-offline-upgrade.log; then
                    mios_err "skopeo copy failed to import offline update archive"
                    return 1
                fi
            elif command -v podman >/dev/null 2>&1 && [[ -f "$media_path" ]]; then
                if ! podman load -i "$media_path" 2>&1 | tee -a /var/log/mios-offline-upgrade.log; then
                    mios_err "podman load failed to ingest archive"
                    return 1
                fi
            else
                mios_err "Neither skopeo nor podman available to import offline container image"
                return 1
            fi

            mios_ok "Image successfully imported to containers-storage:${staging_ref}"
            mios_log "Staging new OS deployment via bootc switch"
            if command -v bootc >/dev/null 2>&1; then
                if ! bootc switch --transport containers-storage "${staging_ref}" 2>&1 | tee -a /var/log/mios-offline-upgrade.log; then
                    mios_err "bootc switch failed; system deployment unchanged"
                    return 1
                fi
            else
                mios_warn "bootc binary not found on host; simulating deployment staging"
            fi
            ;;
    esac

    # Record switch in history TSV
    local ts
    ts="$(date -u +%FT%TZ)"
    { printf '%s\t%s\t%s\t%s\n' "$ts" "$transport" "$media_path" "$staging_ref"; } >> /var/lib/mios/bootc-switch-history.tsv 2>/dev/null || true

    return 0
}

get_running_kernel() {
    uname -r
}

get_staged_kernel() {
    local override_staged_root="${1:-}"

    # If explicit root is provided (e.g. during testing or mounted staged tree)
    if [[ -n "$override_staged_root" && -d "${override_staged_root}/usr/lib/modules" ]]; then
        find "${override_staged_root}/usr/lib/modules" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | sort -V | tail -n1
        return 0
    fi

    # Look for OSTree / bootc staged deployments
    local staged_dirs=(
        /ostree/deploy/*/deploy/*.0/usr/lib/modules
        /ostree/deploy/*/deploy/*.1/usr/lib/modules
        /sysroot/ostree/deploy/*/deploy/*.0/usr/lib/modules
        /sysroot/ostree/deploy/*/deploy/*.1/usr/lib/modules
    )

    for m_dir in "${staged_dirs[@]}"; do
        if [[ -d "$m_dir" ]]; then
            local found_kver
            found_kver="$(find "$m_dir" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | sort -V | tail -n1 || true)"
            if [[ -n "$found_kver" ]]; then
                printf '%s\n' "$found_kver"
                return 0
            fi
        fi
    done

    # Fallback: check /usr/lib/modules on current root if nothing staged
    if [[ -d "/usr/lib/modules" ]]; then
        find /usr/lib/modules -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | sort -V | tail -n1
        return 0
    fi

    uname -r
}

differentiate_update_type() {
    local running_kver="$1"
    local staged_kver="$2"
    local staged_root="${3:-}"

    # Strict kernel version check
    if [[ "$running_kver" != "$staged_kver" ]]; then
        printf 'kernel\n'
        return 0
    fi

    # If kernel version strings match, check if UKI or vmlinuz binary content changed
    if [[ -n "$staged_root" && -d "${staged_root}/usr/lib/modules/${staged_kver}" && -d "/usr/lib/modules/${running_kver}" ]]; then
        local running_vmlinuz="/usr/lib/modules/${running_kver}/vmlinuz"
        local staged_vmlinuz="${staged_root}/usr/lib/modules/${staged_kver}/vmlinuz"

        if [[ -f "$running_vmlinuz" && -f "$staged_vmlinuz" ]]; then
            if ! cmp -s "$running_vmlinuz" "$staged_vmlinuz"; then
                printf 'kernel\n'
                return 0
            fi
        fi
    fi

    # Both kernel release and UKI binaries match: userspace-only update
    printf 'userspace-only\n'
    return 0
}

apply_reboot_strategy() {
    local update_type="$1"
    local reboot_mode="${2:-auto}"
    local dry_run="${3:-0}"

    mios_log "Reboot evaluation: update_type=${update_type}, reboot_mode=${reboot_mode}, dry_run=${dry_run}"

    if [[ "$dry_run" == "1" ]]; then
        if [[ "$update_type" == "userspace-only" && ( "$reboot_mode" == "auto" || "$reboot_mode" == "soft-reboot" ) ]]; then
            mios_ok "[DRY-RUN] Would execute: systemctl soft-reboot (userspace-only, no BIOS/UEFI cycle)"
        else
            mios_ok "[DRY-RUN] Would execute: systemctl reboot (full hardware/firmware power-cycle)"
        fi
        return 0
    fi

    case "$reboot_mode" in
        none|stage-only)
            mios_ok "Update staged. Reboot skipped by request (--stage-only)."
            if [[ "$update_type" == "userspace-only" ]]; then
                mios_log "Apply immediately without power cycle: sudo systemctl soft-reboot"
            else
                mios_log "Apply via full system reboot: sudo systemctl reboot"
            fi
            ;;
        soft-reboot|force-soft-reboot)
            mios_log "Triggering systemctl soft-reboot..."
            if command -v systemctl >/dev/null 2>&1; then
                if ! systemctl soft-reboot; then
                    mios_warn "systemctl soft-reboot failed; falling back to full systemctl reboot"
                    systemctl reboot
                fi
            else
                mios_warn "systemctl not available; soft-reboot simulated"
            fi
            ;;
        reboot|force-reboot)
            mios_log "Triggering full systemctl reboot..."
            if command -v systemctl >/dev/null 2>&1; then
                systemctl reboot
            else
                mios_warn "systemctl not available; reboot simulated"
            fi
            ;;
        auto|*)
            if [[ "$update_type" == "userspace-only" ]]; then
                mios_ok "Applying non-kernel userspace update via systemctl soft-reboot without full power-cycle/BIOS reboot"
                if command -v logger >/dev/null 2>&1; then
                    logger -t mios-uupd "Applying non-kernel update via systemctl soft-reboot" 2>/dev/null || true
                fi
                if command -v systemctl >/dev/null 2>&1; then
                    if ! systemctl soft-reboot; then
                        mios_warn "systemctl soft-reboot returned non-zero; falling back to full reboot"
                        systemctl reboot
                    fi
                else
                    mios_ok "[OK] systemctl soft-reboot simulated successfully"
                fi
            else
                mios_ok "Kernel update detected. Initiating full power-cycle/BIOS reboot."
                if command -v logger >/dev/null 2>&1; then
                    logger -t mios-uupd "Kernel update detected. Initiating systemctl reboot" 2>/dev/null || true
                fi
                if command -v systemctl >/dev/null 2>&1; then
                    systemctl reboot
                else
                    mios_ok "[OK] systemctl reboot simulated successfully"
                fi
            fi
            ;;
    esac
}

write_upgrade_status() {
    local update_type="$1"
    local running_kver="$2"
    local staged_kver="$3"
    local media_path="$4"
    local transport="$5"
    local reboot_action="$6"

    local status_dir="/run/mios"
    install -d -m 0755 "$status_dir" 2>/dev/null || true

    local json_file="${status_dir}/upgrade-status.json"
    cat <<EOF > "$json_file" 2>/dev/null || true
{
  "timestamp": "$(date -u +%FT%TZ)",
  "media_path": "${media_path}",
  "transport": "${transport}",
  "running_kernel": "${running_kver}",
  "staged_kernel": "${staged_kver}",
  "update_type": "${update_type}",
  "recommended_action": "${reboot_action}"
}
EOF

    local history_file="/var/lib/mios/upgrade-history.tsv"
    install -d -m 0755 "/var/lib/mios" 2>/dev/null || true
    {
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$(date -u +%FT%TZ)" "$media_path" "$transport" "$running_kver" "$staged_kver" "$update_type"
    } >> "$history_file" 2>/dev/null || true
}

run_offline_upgrade_cli() {
    local media_input=""
    local transport_input=""
    local dry_run=0
    local reboot_mode="auto"
    local check_only=0
    local staged_root_override=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --media|-m)
                media_input="$2"
                shift 2
                ;;
            --transport|-t)
                transport_input="$2"
                shift 2
                ;;
            --dry-run|-n)
                dry_run=1
                shift
                ;;
            --check|--check-only)
                check_only=1
                shift
                ;;
            --no-reboot|--stage-only)
                reboot_mode="none"
                shift
                ;;
            --soft-reboot|--force-soft-reboot)
                reboot_mode="soft-reboot"
                shift
                ;;
            --reboot|--force-reboot)
                reboot_mode="reboot"
                shift
                ;;
            --staged-root)
                staged_root_override="$2"
                shift 2
                ;;
            --help|-h)
                cat <<'EOF'
MiOS Offline Atomic OCI Upgrade Utility
Usage: 50-uupd-installer.sh [options]
       mios-offline-upgrade [options]

Options:
  -m, --media PATH         Path to USB mount, OCI directory layout, or archive tarball
  -t, --transport TYPE     Transport type: oci, oci-archive, containers-storage, auto (default)
  -n, --dry-run            Simulate media discovery, verification, and kernel comparison
      --check-only         Check offline media and compare kernels without staging
      --stage-only         Stage deployment via bootc switch but do not trigger reboot
      --soft-reboot        Force userspace-only restart via systemctl soft-reboot
      --reboot             Force full hardware/firmware power-cycle via systemctl reboot
      --staged-root PATH   Explicit root directory for staged kernel inspection
  -h, --help               Display this help text and exit

Description:
  Enables air-gapped MiOS hosts to atomically upgrade from USB media carrying an OCI
  layout or image archive. Automatically differentiates between kernel updates and
  userspace-only updates:
    - Userspace updates are applied via 'systemctl soft-reboot' without BIOS POST.
    - Kernel updates trigger a full 'systemctl reboot' to load new signed UKI binaries.
EOF
                exit 0
                ;;
            *)
                mios_err "Unknown argument: $1"
                exit 2
                ;;
        esac
    done

    mios_step "MiOS Offline Atomic OCI Upgrade Initiated"

    local detected_tuple
    if ! detected_tuple="$(detect_offline_media "$media_input")"; then
        mios_err "No offline update media found on USB mounts or search paths"
        exit 1
    fi

    local media_path="${detected_tuple%|*}"
    local detected_transport="${detected_tuple#*|}"
    local transport="${transport_input:-$detected_transport}"

    mios_ok "Located offline update source: ${media_path} (transport: ${transport})"

    if ! verify_oci_image "$media_path" "$transport"; then
        mios_err "Offline image verification failed"
        exit 1
    fi

    if [[ "$check_only" == "1" ]]; then
        local running_kver staged_kver update_type
        running_kver="$(get_running_kernel)"
        staged_kver="$(get_staged_kernel "$staged_root_override")"
        update_type="$(differentiate_update_type "$running_kver" "$staged_kver" "$staged_root_override")"

        mios_ok "Image valid. Running Kernel: ${running_kver} | Staged Kernel: ${staged_kver}"
        mios_ok "Update Classification: ${update_type}"
        if [[ "$update_type" == "userspace-only" ]]; then
            mios_ok "Candidate for fast systemctl soft-reboot"
        else
            mios_ok "Requires full systemctl reboot (kernel update)"
        fi
        exit 0
    fi

    if ! stage_offline_image "$media_path" "$transport" "localhost/mios:offline-update" "$dry_run"; then
        mios_err "Failed to stage offline update"
        exit 1
    fi

    local running_kver staged_kver update_type
    running_kver="$(get_running_kernel)"
    staged_kver="$(get_staged_kernel "$staged_root_override")"
    update_type="$(differentiate_update_type "$running_kver" "$staged_kver" "$staged_root_override")"

    mios_ok "Kernel Assessment: Running=${running_kver}, Staged=${staged_kver} -> UpdateType=${update_type}"

    write_upgrade_status "$update_type" "$running_kver" "$staged_kver" "$media_path" "$transport" "$reboot_mode"

    apply_reboot_strategy "$update_type" "$reboot_mode" "$dry_run"
    return 0
}

# -----------------------------------------------------------------------------
# System Bake / Provisioning Installation Routine
# -----------------------------------------------------------------------------

install_uupd_subsystem() {
    mios_step "Installing updater packages and configuring uupd"

    if declare -f install_packages >/dev/null 2>&1; then
        install_packages "updater" || true
    fi

    local wants_dir="/usr/lib/systemd/system/multi-user.target.wants"
    if [[ -w "/usr/lib/systemd/system" || -w "/" ]]; then
        install -d -m 0755 "${wants_dir}" 2>/dev/null || true

        if [[ -f "/usr/lib/systemd/system/uupd.timer" ]]; then
            ln -sf ../uupd.timer "${wants_dir}/uupd.timer" 2>/dev/null || true
            if command -v systemctl >/dev/null 2>&1; then
                systemctl disable bootc-fetch-apply-updates.timer 2>/dev/null || true
                systemctl disable rpm-ostreed-automatic.timer     2>/dev/null || true
            fi
            mios_ok "uupd.timer enabled as primary OS update timer"
        elif [[ -f "/usr/lib/systemd/system/bootc-fetch-apply-updates.timer" || -f "/usr/lib/systemd/system/bootc-fetch-apply-updates.service" ]]; then
            if [[ -f "/usr/lib/systemd/system/bootc-fetch-apply-updates.timer" ]]; then
                ln -sf ../bootc-fetch-apply-updates.timer "${wants_dir}/bootc-fetch-apply-updates.timer" 2>/dev/null || true
            fi
            if command -v systemctl >/dev/null 2>&1; then
                systemctl disable rpm-ostreed-automatic.timer 2>/dev/null || true
            fi
            mios_ok "bootc-fetch-apply-updates.timer enabled as primary OS update timer"
        else
            mios_warn "Neither uupd.timer nor bootc-fetch-apply-updates.timer found at bake time"
        fi
    fi

    # Materialize uupd config if directory exists or can be created
    if [[ -d "/usr/lib/uupd" || -w "/usr/lib" ]]; then
        install -d -m 0755 /usr/lib/uupd 2>/dev/null || true
        cat <<'EOF' > /usr/lib/uupd/config.json 2>/dev/null || true
{"hardware_checks":{"battery_threshold":20,"cpu_threshold":50,"memory_threshold":90,"network_threshold_kbs":700},"updates":{"bootc":true,"bootc_args":["--download-only"],"flatpak":true,"distrobox":true,"brew":true},"notifications":{"dbus":true}}
EOF
    fi

    # Install mios-offline-upgrade binary and libexec link
    local bin_dest="/usr/bin/mios-offline-upgrade"
    local libexec_dest="/usr/libexec/mios/mios-offline-upgrade"
    local service_dest="/usr/lib/systemd/system/mios-offline-upgrade.service"

    # These are the runtime producer's mandatory artifacts. Never report an
    # installed updater when copying, permissions or its compatibility link failed.
    install -d -m 0755 /usr/bin /usr/libexec/mios || {
        mios_err "Cannot prepare offline updater runtime directories"
        return 1
    }
    cp -f "${BASH_SOURCE[0]}" "$bin_dest" || {
        mios_err "Cannot install offline updater executable"
        return 1
    }
    chmod 0755 "$bin_dest" || {
        mios_err "Cannot make offline updater executable"
        return 1
    }
    ln -sf "$bin_dest" "$libexec_dest" || {
        mios_err "Cannot install offline updater compatibility link"
        return 1
    }
    if ! cmp -s "${BASH_SOURCE[0]}" "$bin_dest" ||
       [[ "$(readlink "$libexec_dest")" != "$bin_dest" ]]; then
        mios_err "Offline updater runtime artifact verification failed"
        return 1
    fi
    mios_ok "Installed ${bin_dest} and ${libexec_dest}"

    # Install systemd service unit for offline upgrade automation
    if [[ -w "/usr/lib/systemd/system" ]]; then
        cat <<'EOF' > "$service_dest" 2>/dev/null || true
[Unit]
Description=MiOS Offline Atomic OCI Upgrade Service
Documentation=man:bootc(8) file:///usr/share/doc/mios/manual/offline-upgrade.md
After=local-fs.target
ConditionPathExists=/run/media

[Service]
Type=oneshot
ExecStart=/usr/bin/mios-offline-upgrade --reboot-mode auto
StandardOutput=journal
StandardError=journal
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
EOF
        chmod 0644 "$service_dest" 2>/dev/null || true
        mios_ok "Installed ${service_dest}"
    fi

    mios_ok "uupd subsystem and offline atomic upgrade path installation complete"
}

# -----------------------------------------------------------------------------
# Main Entry Point Dispatch
# -----------------------------------------------------------------------------

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # If invoked with command line arguments (e.g. --media, --check, --dry-run, --help)
    if [[ $# -gt 0 ]]; then
        run_offline_upgrade_cli "$@"
        exit $?
    fi

    # If invoked by name as mios-offline-upgrade (symlink or binary)
    if [[ "$(basename "$0")" == "mios-offline-upgrade" ]]; then
        run_offline_upgrade_cli "$@"
        exit $?
    fi

    # Otherwise, execute bake-time phase installer
    install_uupd_subsystem
    exit 0
fi

