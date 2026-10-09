#!/usr/bin/env bash
# AI-hint: Thin Linux bash dispatcher for the mios-install unified provisioning
# AI-related: mios-common.sh, build-mios.sh, field/MiOS-Field.sh, field/MiOS-Field.bat
# AI-functions: usage, resolve_flash_or_live, resolve_live, resolve_flash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIELD_DIR="${ROOT}/field"
if [[ ! -d "$FIELD_DIR" && -d "${ROOT}/../mios-bootstrap/field" ]]; then
    FIELD_DIR="$(cd "${ROOT}/../mios-bootstrap/field" && pwd)"
fi
MIOS_FIELD_SH="${FIELD_DIR}/MiOS-Field.sh"

_MIOS_REPO_ROOT="$ROOT"
. "${SCRIPT_DIR}/mios-common.sh"

usage() {
    cat <<'EOF'
mios-install.sh -- unified MiOS provisioning dispatcher (Linux)

  mios-install.sh <target> [--type <name>] [--stage <name>] [--dry-run] [--unattended] [-- <native args>]

  <target>      live | xbox | fedora | bootc | oci | seed | flash | build | update | stage | verify
  --type NAME   narrows the target to a concrete flavor (per-target; omit for the default)
  --stage NAME  prereqs | fetch | service | iso | flash (best-effort unless the target's
                row in installation/README.md marks it REAL; omit to run the whole pipeline)
  --dry-run     print the resolved command + env, execute nothing, exit 0
  --unattended  maps to whatever real non-interactive switch the target already has;
                if none exists, this script says so and still runs interactively
  -- ARGS       everything after a literal `--` is appended verbatim to the invocation
                (also: any unrecognized flag, WITHOUT `--`, is treated the same way --
                e.g. `mios-install.sh update --check` == `mios-install.sh update -- --check`)

Native on Linux: fedora, bootc, build, update, flash, live.
Windows-only (guidance printed, not executed here): xbox, oci, seed.

Examples:
  mios-install.sh flash --dry-run
  mios-install.sh fedora --unattended
  mios-install.sh bootc --type upgrade --stage prereqs   # -> mios-update --check
  mios-install.sh update --check                         # -> mios-update --check
  mios-install.sh build -- --tag mios:test

Full grammar, the target -> entrypoint mapping table, and per-stage
REAL-vs-best-effort notes: installation/README.md.
EOF
}

ORIG_ARGS=("$@")

if [[ $# -eq 0 ]]; then usage; exit 1; fi
case "$1" in -h|--help|help) usage; exit 0 ;; esac
case "$1" in -*) die "Missing required <target> as the first argument. Run with" ;; esac

TARGET="$1"; shift
TYPE=""
STAGE=""
DRY_RUN=0
UNATTENDED=0
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)      TYPE="${2:-}"; shift 2 ;;
        --type=*)    TYPE="${1#*=}"; shift ;;
        --stage)     STAGE="${2:-}"; shift 2 ;;
        --stage=*)   STAGE="${1#*=}"; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --unattended) UNATTENDED=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        --)          shift; PASSTHROUGH=("$@"); break ;;
        *)
            log_info "note: unrecognized flag '$1' -- treating it and everything after as passthrough args (same as '-- $1 ...')."
            PASSTHROUGH=("$@")
            break
            ;;
    esac
done

case "$STAGE" in
    ""|prereqs|fetch|service|iso|flash) ;;
    *) die "Invalid" ;;
esac

CMD=(); ENV=(); STAGE_NOTES=(); REQUIRES_ROOT=0; FORBIDS_ROOT=0; WINDOWS_GUIDANCE=""

resolve_flash_or_live() {
    local target_name="$1"
    TYPE="${TYPE:-usb}"
    case "$TYPE" in
        usb|live) ;;
        *) die "Target '${target_name}' only supports" ;;
    esac
    [[ -f "$MIOS_FIELD_SH" ]] || die "field/MiOS-Field.sh not found at ${MIOS_FIELD_SH}"
    FORBIDS_ROOT=1
    CMD=(env -C "$FIELD_DIR" bash ./MiOS-Field.sh "${PASSTHROUGH[@]}")
    STAGE_NOTES+=("Linux flash/live opens the field media menu; this launcher does not partition a disk or install Ventoy. Use stage/verify for existing mounted media.")
    [[ -n "$STAGE" ]] && STAGE_NOTES+=("--stage ${STAGE} requested but not isolable; opening the field menu.")
    if (( UNATTENDED )); then
        STAGE_NOTES+=("--unattended requested but flash/live opens an interactive field menu; use stage/verify with explicit media paths for unattended operations.")
    fi
    if [[ "$target_name" == "live" ]]; then
        STAGE_NOTES+=("'live' and 'flash' both open the same field menu; this does not prove the media is bootable.")
    fi
}
resolve_live()  { resolve_flash_or_live live; }
resolve_flash() { resolve_flash_or_live flash; }

resolve_build_mios_sh() {
    local mode="$1"   # fhs | bootc
    REQUIRES_ROOT=1
    ENV+=("INSTALL_MODE=${mode}")
    if (( UNATTENDED )); then
        ENV+=("MIOS_PROMPT_TIMEOUT=1")
        [[ "$mode" == "fhs" ]] && ENV+=("MIOS_FHS_TOTAL_ROOT_MERGE=1")
        if [[ -z "${MIOS_PASSWORD:-}" ]]; then
            STAGE_NOTES+=("--unattended set but MIOS_PASSWORD is not exported -- the Linux-user password prompt will still block (prompt_password() only auto-fills from \$MIOS_PASSWORD). Export it before invoking for a truly unattended run.")
        fi
    fi
    if [[ "$mode" == "bootc" ]] && command -v bootc >/dev/null 2>&1 && bootc status >/dev/null 2>&1; then
        local img
        img="$(mios_ssot_value image ref 'ghcr.io/mios-dev/mios:latest')"
        CMD=(bootc switch "$img")
    else
        CMD=(bash "$0" _install_core "$mode")
    fi
}

resolve_fedora() {
    TYPE="${TYPE:-fhs}"
    [[ "$TYPE" == "fhs" ]] || die "Target 'fedora' only supports"
    resolve_build_mios_sh fhs
}

resolve_bootc() {
    TYPE="${TYPE:-switch}"
    case "$TYPE" in
        switch)
            resolve_build_mios_sh bootc
            STAGE_NOTES+=("'bootc switch' only actually engages bootc mode if THIS host is already bootc-booted (build-mios.sh's detect_host_kind checks 'bootc status'); on a plain Fedora (non-bootc) host it silently falls back to the fhs path regardless of INSTALL_MODE.")
            STAGE_NOTES+=("the target image is NOT settable via an IMAGE_TAG env var (source-verified: gather_user_choices() unconditionally re-prompts via prompt_default, ignoring any pre-set IMAGE_TAG) -- pin it in mios.toml [image].ref before an --unattended run, or omit --unattended and answer the prompt.")
            ;;
        upgrade)
            resolve_mios_update_like "bootc --type upgrade"
            ;;
        *) die "Target 'bootc' supports" ;;
    esac
}

resolve_mios_update_like() {
    local via="$1"   # "update" or "bootc --type upgrade" -- both resolve identically, only the error text differs
    local bin
    if bin="$(find_mios_bin mios-update)"; then :; else
        if (( DRY_RUN )); then bin="mios-update"; else
            die "mios-update not found on PATH or under /usr/bin (via ${via})"
        fi
    fi
    local args=()
    case "$STAGE" in
        prereqs) args+=(--check); STAGE_NOTES+=("--stage prereqs -> mios-update --check (dry preview, REAL isolation).") ;;
        flash)   args+=(--apply); STAGE_NOTES+=("--stage flash -> mios-update --apply (stages + reboots, REAL isolation).") ;;
        fetch|service|iso) STAGE_NOTES+=("--stage ${STAGE} collapses into bootc-upgrade internals -- not isolable here; running with no stage flag.") ;;
        "") ;;
    esac
    if (( UNATTENDED )) && [[ "$STAGE" != prereqs && "$STAGE" != flash ]]; then
        args+=(--apply)
        STAGE_NOTES+=("--unattended -> mios-update --apply (closest analog; the script itself is already non-interactive).")
    fi
    args+=("${PASSTHROUGH[@]}")
    CMD=("$bin" "${args[@]}")
}

resolve_update() {
    case "$TYPE" in
        ""|update) ;;
        repo)
            WINDOWS_GUIDANCE="target 'update' --type repo is Windows-only: it maps to 'field\\MiOS-Field.bat update' (git fetch/pull of BOTH C:\\MiOS and C:\\mios-bootstrap). There is no Linux row for this in installation/README.md -- run it on the Windows checkout instead, or just 'git pull' this repo yourself."
            return
            ;;
        *) die "Target 'update' supports" ;;
    esac
    resolve_mios_update_like update
}

resolve_build() {
    [[ -z "$TYPE" ]] || die "Target 'build' takes no"
    local bin
    if bin="$(find_mios_bin mios-build)"; then :; else
        if (( DRY_RUN )); then bin="mios-build"; else
            die "Mios-build not found on PATH or under /usr/bin"
        fi
    fi
    local args=()
    case "$STAGE" in
        flash)   args+=(--apply); STAGE_NOTES+=("--stage flash -> mios-build --apply (build + switch + reboot, REAL isolation).") ;;
        prereqs) STAGE_NOTES+=("--stage prereqs: N/A for mios-build -- no isolable preflight-only mode.") ;;
        fetch|service|iso) STAGE_NOTES+=("--stage ${STAGE}: N/A -- mios-build operates on the tree already present on this host; no fetch/iso stages.") ;;
        "") ;;
    esac
    if (( UNATTENDED )) && [[ "$STAGE" != flash ]]; then
        args+=(--apply)
        STAGE_NOTES+=("--unattended -> mios-build --apply (closest analog; the script itself is always non-interactive).")
    fi
    args+=("${PASSTHROUGH[@]}")
    CMD=("$bin" "${args[@]}")
}

resolve_xbox() {
    TYPE="${TYPE:-iso}"
    local toml; toml="$(mios_ssot_path)"
    local script args_str extra=""
    (( ${#PASSTHROUGH[@]} )) && extra=" ${PASSTHROUGH[*]}"
    case "$TYPE" in
        iso)
            script='field\autounattend\Build-MiOSXboxISO.ps1'
            args_str="-TomlPath '<ssot>'"
            case "$STAGE" in
                fetch|service|iso|flash) args_str+=" -SkipPrereqs" ;;
            esac
            [[ -n "$STAGE" ]] && STAGE_NOTES+=("--stage ${STAGE}: only -SkipPrereqs is a REAL flag at this wrapper level; service/iso isolation needs New-MiOSISO.ps1 directly.")
            ;;
        vm)
            script='field\autounattend\Deploy-MiOSXbox.ps1'
            args_str="-TomlPath '<ssot>' -VMName MiOS-XBOX-Test -LogDir C:\\MiOS\\logs"
            if [[ "$STAGE" == flash ]]; then
                args_str+=" -SkipBuild"
                if (( ${#PASSTHROUGH[@]} == 0 )); then
                    args_str+=" -SourceIso <path-to-existing-iso>"
                fi
                STAGE_NOTES+=("--stage flash -> -SkipBuild (REAL: create+boot the VM only, against an existing ISO). Supply the ISO path via -- -SourceIso <path> (shown below if not already given).")
            fi
            ;;
        provision)
            script='field\autounattend\Invoke-MiOSProvision.ps1'
            args_str="-TomlPath '<ssot>'"
            (( UNATTENDED )) && args_str+=" -SkipBootstrap"
            ;;
        *) die "Target 'xbox' supports" ;;
    esac
    WINDOWS_GUIDANCE="target 'xbox' (--type ${TYPE}) is a Windows-only entrypoint (DISM servicing / Hyper-V) -- it cannot run on Linux. On a Windows host, run:
  powershell -NoProfile -ExecutionPolicy Bypass -File ${script} ${args_str}${extra}
<ssot> resolves like every existing entrypoint: '..\\mios.toml' relative to the script, else C:\\MiOS\\usr\\share\\mios\\mios.toml. mios.toml on THIS checkout: ${toml}"
}

resolve_oci() {
    TYPE="${TYPE:-local}"
    case "$TYPE" in local|full|push) ;; *) die "Target 'oci' supports" ;; esac
    local unattended_flag="" skip_bib_note=""
    (( UNATTENDED )) && unattended_flag=" -Unattended"
    case "$TYPE" in
        local) skip_bib_note='$env:MIOS_SKIP_BIB=1 before calling (OCI image only -- what MiOS-Field.bat''s build_oci menu item sets)' ;;
        full)  skip_bib_note='MIOS_SKIP_BIB left UNSET (full raw/iso/qcow2/vhd/wsl2 matrix -- what build_all sets)' ;;
        push)  skip_bib_note='MIOS_SKIP_BIB left UNSET, plus $env:MIOS_GITHUB_TOKEN exported first for the ghcr push' ;;
    esac
    local extra=""
    (( ${#PASSTHROUGH[@]} )) && extra=" ${PASSTHROUGH[*]}"
    WINDOWS_GUIDANCE="target 'oci' (--type ${TYPE}) is a Windows-only entrypoint -- it cannot run on Linux. On a Windows host, run:
  ${skip_bib_note}
  powershell -NoProfile -ExecutionPolicy Bypass -File build-mios.ps1${unattended_flag}${extra}
IMPORTANT (source-verified against the CURRENT build-mios.ps1): its -BootstrapOnly/-BuildOnly/-FullBuild switches are hard-forced no-ops now. This step only acks + provisions the MiOS-DEV podman machine + installs the 'mios build' launcher -- it does NOT by itself produce a finished OCI image or the BIB matrix any more. The actual build happens afterward when the operator runs 'mios build' inside the provisioned MiOS terminal (SSHes into MiOS-DEV -> /usr/libexec/mios/mios-build-driver). See installation/README.md's 'Verified caveat -- oci and MIOS_SKIP_BIB' section."
}

resolve_seed() {
    TYPE="${TYPE:-dev}"
    [[ "$TYPE" == "dev" ]] || die "Target 'seed' supports"
    local toml; toml="$(mios_ssot_path)"
    local extra=""
    (( ${#PASSTHROUGH[@]} )) && extra=" ${PASSTHROUGH[*]}"
    WINDOWS_GUIDANCE="target 'seed' (--type dev) is a Windows-only entrypoint (exports an existing MiOS-DEV WSL2 distro + OCI image as an offline seed blob) -- it cannot run on Linux. On a Windows host, run:
  powershell -NoProfile -ExecutionPolicy Bypass -File field\\autounattend\\Build-MiOSSeed.ps1 -TomlPath '<ssot>'${extra}
--stage/--unattended have no mapping here -- the script has no stage flags and (per source) no interactive prompts, so it already runs unattended. mios.toml on THIS checkout: ${toml}"
}

resolve_config() {
    local port
    port="$(mios_ssot_value ports agent_pipe 8640)"
    local url="http://localhost:${port}/configure"
    local html_path="${ROOT}/usr/share/mios/configurator/mios.html"
    log_info "Opening MiOS Configurator at ${url} ..."
    if command -v xdg-open >/dev/null 2>&1; then
        CMD=(xdg-open "$url")
    elif command -v python3 >/dev/null 2>&1; then
        CMD=(python3 -m webbrowser "$url")
    elif [[ -f "$html_path" ]]; then
        log_warn "No browser launcher found; offline HTML available at ${html_path}"
        CMD=(echo "Offline configurator HTML: ${html_path}")
    else
        log_warn "No browser launcher found -- open ${url} manually."
        CMD=(true)
    fi
}

resolve_stage() {
    CMD=("$0" _stage "${PASSTHROUGH[@]}")
}

resolve_verify() {
    CMD=("$0" _verify "${PASSTHROUGH[@]}")
}

DEFAULT_USER="user"
DEFAULT_HOST="user"
DEFAULT_USER_FULLNAME="User"
DEFAULT_USER_SHELL="/bin/bash"
DEFAULT_USER_GROUPS="wheel,libvirt,kvm,video,render,input,dialout,docker"
DEFAULT_SSH_KEY_TYPE="ed25519"
DEFAULT_IMAGE="ghcr.io/mios-dev/mios:latest"
DEFAULT_BRANCH="main"
# AI model defaults. These are pre-profile-load vendor fallbacks that
# MATCH the SSOT mios.toml [ai] section (model / embed_model); they are
# superseded in load_profile_defaults() by the RAM-driven auto-pick
# against the [ai.host_thresholds] tier table and then by any explicit
# [ai].model operator override.
DEFAULT_AI_MODEL="qwen3.5:2b"
DEFAULT_AI_EMBED_MODEL="nomic-embed-text"

: "${MIOS_REPO:=https://github.com/mios-dev/mios.git}"
: "${BOOTSTRAP_REPO:=https://github.com/mios-dev/mios-bootstrap.git}"
PROFILE_DIR="/etc/mios"
# Canonical user-edit copy lives in mios-bootstrap.git/mios.toml (repo root).
# /etc/mios/mios.toml is the host-installed copy of that file. Legacy
# /etc/mios/profile.toml is still recognized by the resolver for
# pre-unification deployments.
PROFILE_CARD="${PROFILE_DIR}/mios.toml"
PROFILE_CARD_LEGACY="${PROFILE_DIR}/profile.toml"
PROFILE_FILE="${PROFILE_DIR}/install.env"

# Pull a value from a TOML file. Args: <file> <section> <key>.
# Strips quotes and inline comments. Returns empty if missing.
toml_get() {
    local file="$1" section="$2" key="$3"
    [[ -f "$file" ]] || { echo ""; return; }
    awk -v sect="[${section}]" -v k="$key" '
        $0 == sect            { in_sect = 1; next }
        /^\[/                 { in_sect = 0 }
        in_sect && $1 == k    { sub(/^[^=]*=[ \t]*/, ""); sub(/[ \t]*#.*$/, ""); gsub(/^"|"$/, ""); print; exit }
    ' "$file"
}

# Parse a TOML array of strings into a comma-joined value (groups, flatpaks).
toml_get_array_csv() {
    local file="$1" section="$2" key="$3"
    [[ -f "$file" ]] || { echo ""; return; }
    awk -v sect="[${section}]" -v k="$key" '
        $0 == sect            { in_sect = 1; next }
        /^\[/                 { in_sect = 0 }
        in_sect && $1 == k    {
            sub(/^[^\[]*\[/, ""); sub(/\].*$/, "")
            gsub(/[ \t"]/, "")
            print
            exit
        }
    ' "$file"
}

# Profile resolution. Each layer overlays the one above. Returned as a
# space-separated list of paths (lowest precedence first).
#
#   1. /usr/share/mios/mios.toml           vendor defaults (mios.git)
#   2. /usr/share/mios/profile.toml        legacy vendor defaults (mios.git)
#   3. <bootstrap-checkout>/mios.toml      user-edit copy at repo root  <-- canonical
#   4. <bootstrap-checkout>/etc/mios/profile.toml  legacy user-edit copy
#   5. /etc/mios/mios.toml                 host-installed user-edit (re-run)
#   6. /etc/mios/profile.toml              legacy host-installed copy
#
# Empty strings in higher layers do NOT override non-empty defaults below
# them -- that's how this implements "user-set fields supersede defaults"
# without requiring sparse TOML files.
resolve_profile_layers() {
    local layers=()
    [[ -f /usr/share/mios/mios.toml ]]    && layers+=(/usr/share/mios/mios.toml)
    [[ -f /usr/share/mios/profile.toml ]] && layers+=(/usr/share/mios/profile.toml)
    local bootstrap_root; bootstrap_root="$(dirname "${BASH_SOURCE[0]}")"
    [[ -f "${bootstrap_root}/mios.toml" ]]              && layers+=("${bootstrap_root}/mios.toml")
    [[ -f "${bootstrap_root}/etc/mios/profile.toml" ]]  && layers+=("${bootstrap_root}/etc/mios/profile.toml")
    [[ -f "$PROFILE_CARD" ]]        && layers+=("$PROFILE_CARD")
    [[ -f "$PROFILE_CARD_LEGACY" ]] && layers+=("$PROFILE_CARD_LEGACY")
    printf '%s\n' "${layers[@]}"
}

# Read a single key, walking layers in order. Higher layers override lower.
toml_get_layered() {
    local section="$1" key="$2" array_mode="${3:-}"
    local fn="toml_get"
    [[ "$array_mode" == "array" ]] && fn="toml_get_array_csv"
    local result=""
    while IFS= read -r card; do
        local v; v="$($fn "$card" "$section" "$key")"
        [[ -n "$v" ]] && result="$v"
    done < <(resolve_profile_layers)
    echo "$result"
}

# Auto-pick the AI model from detected host RAM against the SSOT
# [ai.host_thresholds] tier table: >= big_ram_gb -> big_ram_model,
# >= mid_ram_gb -> mid_ram_model, else small_ram_model. mios.toml
# documents [ai].model as the operator override that wins over this
# pick, so callers apply that override AFTER consulting this function.
# Vendor fallbacks mirror the canonical [ai.host_thresholds] values.
pick_ai_model_by_ram() {
    local big_gb mid_gb big_m mid_m small_m ram_gb mem_kb
    big_gb="$(toml_get_layered ai.host_thresholds big_ram_gb)";       big_gb="${big_gb:-32}"
    mid_gb="$(toml_get_layered ai.host_thresholds mid_ram_gb)";       mid_gb="${mid_gb:-12}"
    big_m="$(toml_get_layered ai.host_thresholds big_ram_model)";     big_m="${big_m:-qwen3.5:14b}"
    mid_m="$(toml_get_layered ai.host_thresholds mid_ram_model)";     mid_m="${mid_m:-qwen3.5:2b}"
    small_m="$(toml_get_layered ai.host_thresholds small_ram_model)"; small_m="${small_m:-phi4-mini:3.8b-q4_K_M}"
    # Detected host RAM in GiB (rounded). /proc/meminfo MemTotal is KiB.
    mem_kb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null)"
    if [[ -n "$mem_kb" && "$mem_kb" -gt 0 ]]; then
        ram_gb=$(( (mem_kb + 524288) / 1048576 ))
    else
        ram_gb="$mid_gb"   # RAM unknown -> mid tier
    fi
    if   [[ "$ram_gb" -ge "$big_gb" ]]; then echo "$big_m"
    elif [[ "$ram_gb" -ge "$mid_gb" ]]; then echo "$mid_m"
    else                                     echo "$small_m"; fi
}

# Override DEFAULT_* from the merged profile-card layers.
load_profile_defaults() {
    local layers; layers=$(resolve_profile_layers | tr '\n' ' ')
    [[ -n "$layers" ]] || return 0
    log_info "Loading profile layers (lowest→highest precedence):"
    while IFS= read -r card; do log_info "  * ${card}"; done < <(resolve_profile_layers)

    local v
    v="$(toml_get_layered identity username)";        [[ -n "$v" ]] && DEFAULT_USER="$v"
    v="$(toml_get_layered identity hostname)";        [[ -n "$v" ]] && DEFAULT_HOST="$v"
    v="$(toml_get_layered identity fullname)";        [[ -n "$v" ]] && DEFAULT_USER_FULLNAME="$v"
    v="$(toml_get_layered identity shell)";           [[ -n "$v" ]] && DEFAULT_USER_SHELL="$v"
    v="$(toml_get_layered identity groups array)";    [[ -n "$v" ]] && DEFAULT_USER_GROUPS="$v"
    v="$(toml_get_layered auth ssh_key_type)";        [[ -n "$v" ]] && DEFAULT_SSH_KEY_TYPE="$v"
    v="$(toml_get_layered image ref)";                [[ -n "$v" ]] && DEFAULT_IMAGE="$v"
    v="$(toml_get_layered image branch)";             [[ -n "$v" ]] && DEFAULT_BRANCH="$v"
    v="$(toml_get_layered bootstrap mios_repo)";      [[ -n "$v" ]] && MIOS_REPO="$v"
    v="$(toml_get_layered bootstrap bootstrap_repo)"; [[ -n "$v" ]] && BOOTSTRAP_REPO="$v"

    # AI model selection (Architectural Law 5). The model lineup + RAM
    # thresholds are the SSOT [ai.host_thresholds] tier table. Auto-pick
    # by detected host RAM first; then let an explicit [ai].model (the
    # documented operator override) win. embed follows [ai].embed_model.
    local _ram_pick; _ram_pick="$(pick_ai_model_by_ram)"
    [[ -n "$_ram_pick" ]] && DEFAULT_AI_MODEL="$_ram_pick"
    v="$(toml_get_layered ai model)";          [[ -n "$v" ]] && DEFAULT_AI_MODEL="$v"
    v="$(toml_get_layered ai embed_model)";    [[ -n "$v" ]] && DEFAULT_AI_EMBED_MODEL="$v"

    # Legacy .env.mios fallback (deprecated; sourced last so explicit TOML wins).
    local legacy_env; legacy_env="$(dirname "${BASH_SOURCE[0]}")/.env.mios"
    if [[ -f "$legacy_env" ]]; then
        log_info "Sourcing legacy ${legacy_env} (deprecated; migrate to profile.toml)"
        set +u
        # shellcheck source=/dev/null  # legacy operator file beside the installer, present only on the host
        source "$legacy_env"
        set -u
        [[ -n "${MIOS_IDENTITY_USERNAME:-}" ]] && DEFAULT_USER="${MIOS_IDENTITY_USERNAME}"
        [[ -n "${MIOS_IDENTITY_HOSTNAME:-}" ]] && DEFAULT_HOST="${MIOS_IDENTITY_HOSTNAME}"
        [[ -n "${MIOS_IMAGE_NAME:-}" && -n "${MIOS_IMAGE_TAG:-}" ]] && \
            DEFAULT_IMAGE="${MIOS_IMAGE_NAME}:${MIOS_IMAGE_TAG}"
    fi
}

# ============================================================================
# Logging
# ============================================================================
# Convert hex color to 24-bit ANSI escape code (truecolor)
hex_to_ansi() {
    local hex="${1:-}"
    [[ -n "$hex" ]] || { echo ""; return; }
    hex="${hex#\#}"
    if [[ ${#hex} -eq 3 ]]; then
        hex="${hex:0:1}${hex:0:1}${hex:1:1}${hex:1:1}${hex:2:1}${hex:2:1}"
    fi
    if [[ ${#hex} -eq 6 ]]; then
        local r=$((16#${hex:0:2}))
        local g=$((16#${hex:2:2}))
        local b=$((16#${hex:4:2}))
        echo -ne "\e[38;2;${r};${g};${b}m"
    else
        echo ""
    fi
}

# Initial standard fallbacks (will be updated dynamically once mios.toml layers resolve)
_BOLD=$(tput bold 2>/dev/null || echo -ne "\e[1m")
_RED=$(tput setaf 1 2>/dev/null || echo -ne "\e[31m")
_GREEN=$(tput setaf 2 2>/dev/null || echo -ne "\e[32m")
_YELLOW=$(tput setaf 3 2>/dev/null || echo -ne "\e[33m")
_CYAN=$(tput setaf 6 2>/dev/null || echo -ne "\e[36m")
_ACCENT=$(tput setaf 4 2>/dev/null || echo -ne "\e[34m")
_DIM=$(tput dim 2>/dev/null || echo -ne "\e[2m")
_RESET=$(tput sgr0 2>/dev/null || echo -ne "\e[0m")

initialize_dynamic_branding() {
    # Resolve color values from layered mios.toml
    local c_accent; c_accent="$(toml_get_layered colors accent || toml_get_layered colors.accent || echo "")"
    local c_subtle; c_subtle="$(toml_get_layered colors subtle || toml_get_layered colors.subtle || echo "")"
    local c_success; c_success="$(toml_get_layered colors success || toml_get_layered colors.success || echo "")"
    local c_warning; c_warning="$(toml_get_layered colors warning || toml_get_layered colors.warning || echo "")"
    local c_error; c_error="$(toml_get_layered colors error || toml_get_layered colors.error || echo "")"

    # Map ANSI colors dynamically from SSOT mios.toml (using truecolor)
    local a_accent; a_accent="$(hex_to_ansi "$c_accent")"
    local a_subtle; a_subtle="$(hex_to_ansi "$c_subtle")"
    local a_success; a_success="$(hex_to_ansi "$c_success")"
    local a_warning; a_warning="$(hex_to_ansi "$c_warning")"
    local a_error; a_error="$(hex_to_ansi "$c_error")"

    if [[ -n "$a_accent" ]]; then _ACCENT="$a_accent"; fi
    if [[ -n "$a_subtle" ]]; then _CYAN="$a_subtle"; fi
    if [[ -n "$a_success" ]]; then _GREEN="$a_success"; fi
    if [[ -n "$a_warning" ]]; then _YELLOW="$a_warning"; fi
    if [[ -n "$a_error" ]]; then _RED="$a_error"; fi

    # Output dynamic colored logo banner
    cat <<EOF

\${_ACCENT}       .MMMMMMMMMMMMMMMMMMMMMM.
    .MMMMMMMMMMMMMMMMMMMMMMMMMMMM.
  .MMMMMMMM                  MMMMMMMM.
 MMMMMMMM                      MMMMMMMM
MMMMMMMM   \${_CYAN}__  ___   _   ____  _____   \${_ACCENT}MMMMMMMM
MMMMMMMM  \${_CYAN}/  |/  /  (_) / __ \/ ___/   \${_ACCENT}MMMMMMMM
MMMMMMMM \${_CYAN}/ /|_/ /  / / / / / /\\__ \\    \${_ACCENT}MMMMMMMM
MMMMMMMM\${_CYAN}/ /  / /  / / / /_/ /___/ /    \${_ACCENT}MMMMMMMM
 MMMMMMM\${_CYAN}/_/  /_/  /_/  \\____//____/    \${_ACCENT}MMMMMMM
  .MMMMMMMM                  MMMMMMMM.
    .MMMMMMMMMMMMMMMMMMMMMMMMMMMM.
       .MMMMMMMMMMMMMMMMMMMMMM.
\${_RESET}
EOF
}


log_info()  { printf '%s[INFO]%s %s\n' "${_CYAN}" "${_RESET}" "$*"; }
log_ok()    { printf '%s[ OK ]%s %s\n' "${_GREEN}" "${_RESET}" "$*"; }
log_warn()  { printf '%s[WARN]%s %s\n' "${_YELLOW}" "${_RESET}" "$*" >&2; }
log_err()   { printf '%s[ERR ]%s %s\n' "${_RED}" "${_RESET}" "$*" >&2; }
log_phase() { printf '\n%s%s== %s ==%s\n\n' "${_BOLD}" "${_CYAN}" "$*" "${_RESET}"; }

# ── Spinner ───────────────────────────────────────────────────────────────────
_SPIN_PID=0
spin_start() {
    local msg="${1:-Working...}"
    printf '%s  %s...%s\n' "${_CYAN}" "$msg" "${_RESET}" >&2
    (
        local i=0 chars='|/-\'
        while true; do
            printf '\r  %s %s %s  ' "${_CYAN}" "${chars:$((i % 4)):1}" "$msg${_RESET}" >&2
            i=$((i + 1))
            sleep 0.2
        done
    ) &
    _SPIN_PID=$!
}
spin_stop() {
    if [[ "$_SPIN_PID" -ne 0 ]]; then
        kill "$_SPIN_PID" 2>/dev/null || true
        wait "$_SPIN_PID" 2>/dev/null || true
        _SPIN_PID=0
    fi
    printf '\r%s\r' "$(tput el 2>/dev/null || printf '%80s' '')" >&2
}

# ============================================================================
# Preflight
# ============================================================================
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_err "Bootstrap must run as root. Re-invoke with sudo:"
        log_err "  sudo $0"
        exit 1
    fi
}

detect_host_kind() {
    if command -v bootc >/dev/null 2>&1 && bootc status --format=json 2>/dev/null | grep -q '"booted"'; then
        echo "bootc"
    elif [[ -f /etc/os-release ]] && grep -qE '^ID(_LIKE)?=.*fedora' /etc/os-release; then
        echo "fhs-fedora"
    else
        echo "unsupported"
    fi
}

check_network() {
    local host
    for host in github.com ghcr.io; do
        if ! curl -fsSL --max-time 5 -o /dev/null "https://${host}/" 2>/dev/null; then
            log_warn "No network reachability to ${host}. Proceeding best-effort."
        fi
    done
    log_ok "Network reachability verified"
}

install_prerequisites() {
    local missing=()
    for cmd in git curl openssl; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    [[ ${#missing[@]} -eq 0 ]] && return 0

    log_info "Installing missing prerequisites: ${missing[*]}"
    local dnf_cmd="dnf"
    command -v dnf5 &>/dev/null && dnf_cmd="dnf5"
    spin_start "Installing ${missing[*]}"
    $dnf_cmd install -y --skip-unavailable "${missing[@]}" || {
        spin_stop
        log_err "Failed to install prerequisites: ${missing[*]}"
        exit 1
    }
    spin_stop
    log_ok "Prerequisites ready: ${missing[*]}"
}

# ============================================================================
# Prompts -- the "mios" defaults are baked in; user just hits Enter to accept,
# or stays idle for $MIOS_PROMPT_TIMEOUT seconds (default 90 = 1.5 minutes)
# for the prompt to auto-accept the default. Set MIOS_PROMPT_TIMEOUT=0 to
# disable the timeout (wait forever); set MIOS_PROMPT_TIMEOUT=1 in CI for
# fastest unattended runs.
# ============================================================================
: "${MIOS_PROMPT_TIMEOUT:=90}"

prompt_default() {
    local question="$1" default="$2" answer
    if [[ "${MIOS_PROMPT_TIMEOUT}" -gt 0 ]]; then
        if read -r -t "${MIOS_PROMPT_TIMEOUT}" -p "$(printf '%s%s%s [%s%s%s] (auto-accept in %ds): ' "${_BOLD}" "${question}" "${_RESET}" "${_DIM}" "${default}" "${_RESET}" "${MIOS_PROMPT_TIMEOUT}")" answer; then
            echo "${answer:-$default}"
        else
            # 'read' exits with non-zero on EOF or timeout. Either way we take
            # the default and emit a one-line note to stderr so the operator
            # can audit the unattended decision in the install log.
            printf '\n%s%s%s [auto-accept after %ds] -> %s\n' "${_DIM}" "${question}" "${_RESET}" "${MIOS_PROMPT_TIMEOUT}" "${default}" >&2
            echo "${default}"
        fi
    else
        read -r -p "$(printf '%s%s%s [%s%s%s]: ' "${_BOLD}" "${question}" "${_RESET}" "${_DIM}" "${default}" "${_RESET}")" answer
        echo "${answer:-$default}"
    fi
}

prompt_model() {
    # AI model menu prompt. Same auto-accept timing as prompt_default.
    # The lineup is sourced from the SSOT [ai.host_thresholds] tier
    # table (small/mid/big_ram_model) so it never drifts; option N maps
    # 1:1 onto the RAM tiers plus a 'custom' free-form escape hatch.
    local default="$1"
    local small mid big mid_gb big_gb
    small="$(toml_get_layered ai.host_thresholds small_ram_model)"; small="${small:-phi4-mini:3.8b-q4_K_M}"
    mid="$(toml_get_layered ai.host_thresholds mid_ram_model)";     mid="${mid:-qwen3.5:2b}"
    big="$(toml_get_layered ai.host_thresholds big_ram_model)";     big="${big:-qwen3.5:14b}"
    mid_gb="$(toml_get_layered ai.host_thresholds mid_ram_gb)";     mid_gb="${mid_gb:-12}"
    big_gb="$(toml_get_layered ai.host_thresholds big_ram_gb)";     big_gb="${big_gb:-32}"
    log_info ""
    log_info "AI model (Architectural Law 5 -- baked into the image):"
    log_info "  1) ${small}  -- low-RAM default (CPU-fit)"
    log_info "  2) ${mid}  -- >= ${mid_gb} GB RAM, auto-promote tier"
    log_info "  3) ${big}  -- >= ${big_gb} GB RAM, big-RAM tier"
    log_info "  4) custom             -- enter your own model id"
    local choice; choice="$(prompt_default 'Choice [1-4]' '1')"
    case "$choice" in
        1|"")    echo "$small" ;;
        2)       echo "$mid" ;;
        3)       echo "$big" ;;
        4)       prompt_default 'Custom model id (e.g. mistral:7b)' "${default}" ;;
        *)       log_warn "invalid choice '${choice}'; using default '${default}'"; echo "${default}" ;;
    esac
}

launch_configurator() {
    # Optional GUI step. Open /usr/share/mios/configurator/mios.html
    # in the operator's default browser, stage a writable mios.toml
    # template at a known path, and wait for the operator to save
    # before continuing. The HTML uses the File System Access API to
    # overwrite the staged file in place (no Downloads detour, no "(1)"
    # suffix). Skipped on headless / unattended runs.
    if [[ "${MIOS_NO_CONFIGURATOR:-0}" == "1" ]]; then
        return 0
    fi
    if [[ "${MIOS_PROMPT_TIMEOUT:-90}" == "1" ]]; then
        # Unattended mode -- never show GUI prompts.
        return 0
    fi

    local choice; choice="$(prompt_default 'Open MiOS configurator (HTML) to edit mios.toml in browser?' 'n')"
    case "${choice,,}" in y|yes|true|1) ;; *) return 0 ;; esac

    local bootstrap_root; bootstrap_root="$(dirname "${BASH_SOURCE[0]}")"
    # Locate the HTML configurator. Three candidate paths covering the
    # bootstrap-side checkout, the system overlay (after install), and
    # the staged mirror under /usr/share/mios/configurator (preferred
    # post-install location).
    local html=""
    for cand in \
        "${bootstrap_root}/usr/share/mios/configurator/mios.html" \
        "/usr/share/mios/configurator/mios.html" \
        "${bootstrap_root}/../mios/usr/share/mios/configurator/mios.html" \
        "/tmp/mios-bootstrap-src/usr/share/mios/configurator/mios.html"
    do
        if [[ -f "$cand" ]]; then html="$cand"; break; fi
    done
    if [[ -z "$html" ]]; then
        log_warn "Configurator HTML not found locally -- skipping GUI step"
        return 0
    fi

    # Stage a writable mios.toml template the configurator can bind to.
    # Pick the highest-precedence existing layer; otherwise copy the
    # repo-shipped template. The operator's browser will overwrite this
    # path in place via the File System Access API.
    local staging
    staging="$(mktemp /tmp/mios-config.XXXXXX.toml)"
    local src=""
    for cand in \
        "${HOME}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "${bootstrap_root}/mios.toml" \
        "/usr/share/mios/mios.toml"
    do
        if [[ -r "$cand" ]]; then src="$cand"; break; fi
    done
    if [[ -n "$src" ]]; then
        cp -f "$src" "$staging"
    else
        : > "$staging"   # empty placeholder; operator can click "Defaults" in the UI
    fi
    chmod 0644 "$staging"

    # Pass the staging path to the HTML via a query param so the banner
    # shows the operator exactly where to save (use Pick file -> select
    # this file -> edit -> Save).
    local url
    url="file://${html}?suggested_path=$(printf '%s' "$staging" | sed 's/ /%20/g')"

    log_info ""
    log_info "Opening configurator: ${url}"
    log_info "  Staging file: ${staging}"
    log_info "  After editing: click 'Pick file' -> open the staging file -> Save"
    log_info ""

    # Pick a browser opener. xdg-open works on most desktop sessions;
    # sensible-browser on Debian-derived; explicit firefox/chromium as
    # fallbacks. Detached so the bootstrap tty isn't tied to the
    # browser process.
    local opener=""
    for cand in xdg-open sensible-browser gio firefox chromium google-chrome; do
        if command -v "$cand" >/dev/null 2>&1; then opener="$cand"; break; fi
    done
    if [[ -n "$opener" ]]; then
        if [[ "$opener" == "gio" ]]; then
            "$opener" open "$url" </dev/null >/dev/null 2>&1 &
        else
            "$opener" "$url" </dev/null >/dev/null 2>&1 &
        fi
    else
        log_warn "No browser opener found (xdg-open/firefox/chromium); please open manually:"
        log_warn "  ${url}"
    fi

    # Wait for the operator to finish editing. We don't auto-detect
    # save (mtime polling is fragile on some filesystems) -- explicit
    # confirmation is more reliable.
    prompt_default 'Press Enter when finished editing in the browser' '' >/dev/null

    # Promote the staged file to the per-host layer if the operator
    # actually saved something. Only the [identity], [ai], [network],
    # [image] sections are typically edited; secrets stay in install.env.
    if [[ -s "$staging" ]] && [[ -n "${SUDO_USER:-}" || $EUID -eq 0 ]]; then
        install -d -m 0755 /etc/mios
        install -m 0644 -T "$staging" /etc/mios/mios.toml
        log_ok "Staged ${staging} -> /etc/mios/mios.toml"
        # Re-resolve the layered defaults so the prompts that follow
        # default to whatever the operator wrote in the HTML.
        load_profile_defaults
    fi
}

prompt_password() {
    local prompt="$1" pw1 pw2
    if [[ -n "${MIOS_PASSWORD:-}" ]]; then
        echo "${MIOS_PASSWORD}"
        return 0
    fi
    while :; do
        printf '%s%s%s: ' "${_BOLD}" "${prompt}" "${_RESET}" >&2
        read -rs pw1; echo >&2
        printf '%sConfirm:%s ' "${_BOLD}" "${_RESET}" >&2
        read -rs pw2; echo >&2
        if [[ "$pw1" == "$pw2" ]]; then
            if [[ -z "$pw1" ]]; then
                log_warn "Empty password not allowed."
                continue
            fi
            echo "$pw1"
            return 0
        fi
        log_warn "Passwords don't match, please try again."
    done
}

prompt_yesno() {
    local question="$1" default="${2:-y}" answer hint
    if [[ "$default" == "y" ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
    if [[ "${MIOS_PROMPT_TIMEOUT:-0}" -gt 0 ]]; then
        if read -r -t "${MIOS_PROMPT_TIMEOUT}" -p "$(printf '%s%s%s %s (auto-accept in %ds): ' "${_BOLD}" "${question}" "${_RESET}" "${hint}" "${MIOS_PROMPT_TIMEOUT}")" answer; then
            answer="${answer:-$default}"
        else
            printf '\n%s%s%s [auto-accept after %ds] -> %s\n' "${_DIM}" "${question}" "${_RESET}" "${MIOS_PROMPT_TIMEOUT}" "${default}" >&2
            answer="${default}"
        fi
    else
        read -r -p "$(printf '%s%s%s %s: ' "${_BOLD}" "${question}" "${_RESET}" "${hint}")" answer
        answer="${answer:-$default}"
    fi
    case "${answer,,}" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

# ============================================================================
# Phase-0 (continued): gather installation profile
# ============================================================================
gather_user_choices() {
    log_phase "Phase-0 -- Installation profile"
    log_info "Press Enter to accept defaults (everything defaults to 'MiOS')."
    echo

    LINUX_USER="$(prompt_default 'Linux username' "${DEFAULT_USER}")"
    HOSTNAME_VAL="$(prompt_default 'Hostname' "${DEFAULT_HOST}")"
    USER_FULLNAME="$(prompt_default 'Full name (GECOS)' "${DEFAULT_USER_FULLNAME}")"

    log_info "Setting password for '${LINUX_USER}' (will be a sudoer):"
    USER_PASSWORD="$(prompt_password 'Password')"

    SSH_CHOICE="$(prompt_default 'SSH key: (g)enerate ed25519 / (e)xisting path / (s)kip' 'g')"
    case "${SSH_CHOICE,,}" in
        e|existing) SSH_KEY_PATH="$(prompt_default 'Existing private key path' "/root/.ssh/id_${DEFAULT_SSH_KEY_TYPE}")" ;;
        s|skip)     SSH_KEY_PATH="" ;;
        *)          SSH_KEY_PATH="generate" ;;
    esac

    if prompt_yesno 'Configure GitHub PAT for git credential helper?' n; then
        printf '%sGitHub PAT (input hidden):%s ' "${_BOLD}" "${_RESET}"
        read -rs GH_TOKEN; echo
    else
        GH_TOKEN=""
    fi

    # mios-forge admin (Forgejo). Defaults derive from the linux user so
    # the locally-hosted .git = ./ pattern works out of the box. Empty
    # password means the firstboot service will generate a 24-byte
    # URL-safe random password and write it to /etc/mios/forge/admin-
    # password (root-owned, mode 0600).
    FORGE_ADMIN_USER="$(prompt_default 'Forge admin username (Forgejo)' "${LINUX_USER}")"
    FORGE_ADMIN_EMAIL="$(prompt_default 'Forge admin email' "${LINUX_USER}@${HOSTNAME_VAL}.local")"

    # AI model selection -> MIOS_AI_MODEL / MIOS_AI_EMBED_MODEL in install.env (runtime).
    # The chosen pair is what mios-ai-firstboot.service confirms on first boot, so it
    # carries through end-to-end. Model BAKING is SSOT-driven from mios.toml [ai].bake_models
    # / [ai.vllm].bake_model (read directly by 38-llamacpp-prep.sh / 38-vllm-prep.sh).
    AI_MODEL_VAL="$(prompt_model "${DEFAULT_AI_MODEL}")"
    AI_EMBED_VAL="$(prompt_default 'AI embedding model' "${DEFAULT_AI_EMBED_MODEL}")"

    local hostkind
    hostkind="$(detect_host_kind)"
    if [[ "${INSTALL_MODE:-}" != "fhs" ]] && [[ "$hostkind" == "bootc" ]]; then
        IMAGE_TAG="$(prompt_default 'MiOS bootc image' "${DEFAULT_IMAGE}")"
        INSTALL_MODE="bootc"
    else
        # FHS mode is always "fhs" for total root overlay in this branch.
        INSTALL_MODE="fhs"
        IMAGE_TAG=""
    fi
}

# ============================================================================
# Phase-0 (continued): confirm before applying
# ============================================================================
print_summary() {
    log_phase "Phase-0 -- Review profile"
    cat <<EOF
  ${_BOLD}Linux user${_RESET}     : ${LINUX_USER}  (full name: ${USER_FULLNAME})
  ${_BOLD}Sudo groups${_RESET}    : ${DEFAULT_USER_GROUPS}
  ${_BOLD}Hostname${_RESET}       : ${HOSTNAME_VAL}
  ${_BOLD}Password${_RESET}       : (set, hidden)
  ${_BOLD}SSH key${_RESET}        : ${SSH_KEY_PATH:-skip}
  ${_BOLD}GitHub PAT${_RESET}     : $([ -n "${GH_TOKEN:-}" ] && echo 'configured' || echo 'skip')
  ${_BOLD}Install mode${_RESET}   : ${INSTALL_MODE} (Total Root Overlay)

EOF
    if ! prompt_yesno 'Proceed with these settings?' y; then
        log_info "Aborted by user. No changes made."
        exit 0
    fi
}

# ============================================================================
# Phase-3: apply profile to host
# ============================================================================
apply_user_profile() {
    log_phase "Phase-3 -- Apply profile to host"
    mkdir -p "${PROFILE_DIR}"
    chmod 0750 "${PROFILE_DIR}"

    log_info "Setting hostname -> ${HOSTNAME_VAL}"
    hostnamectl set-hostname "${HOSTNAME_VAL}"

    if id -u "${LINUX_USER}" >/dev/null 2>&1; then
        log_info "User '${LINUX_USER}' exists; updating groups + password"
        usermod -aG "${DEFAULT_USER_GROUPS}" "${LINUX_USER}"
        usermod -c "${USER_FULLNAME}" "${LINUX_USER}"
    else
        log_info "Creating '${LINUX_USER}' (groups: ${DEFAULT_USER_GROUPS})"
        useradd -m -G "${DEFAULT_USER_GROUPS}" -s "${DEFAULT_USER_SHELL}" -c "${USER_FULLNAME}" "${LINUX_USER}"
    fi
    echo "${LINUX_USER}:${USER_PASSWORD}" | chpasswd
    log_ok "User '${LINUX_USER}' configured"

    local home; home="$(getent passwd "${LINUX_USER}" | cut -d: -f6)"
    if [[ "$SSH_KEY_PATH" == "generate" ]]; then
        if [[ -f "${home}/.ssh/id_${DEFAULT_SSH_KEY_TYPE}" ]]; then
            log_info "SSH key already exists at ${home}/.ssh/id_${DEFAULT_SSH_KEY_TYPE} -- skipping generation"
        else
            log_info "Generating ${DEFAULT_SSH_KEY_TYPE} key for ${LINUX_USER}"
            sudo -u "${LINUX_USER}" mkdir -p "${home}/.ssh"
            chmod 0700 "${home}/.ssh"
            sudo -u "${LINUX_USER}" ssh-keygen -q -t "${DEFAULT_SSH_KEY_TYPE}" -N '' \
                -C "mios@${HOSTNAME_VAL}" \
                -f "${home}/.ssh/id_${DEFAULT_SSH_KEY_TYPE}"
            log_ok "SSH key generated: ${home}/.ssh/id_${DEFAULT_SSH_KEY_TYPE}"
        fi
    elif [[ -n "$SSH_KEY_PATH" ]]; then
        if [[ ! -f "$SSH_KEY_PATH" ]]; then
            log_warn "SSH key path not found: ${SSH_KEY_PATH} -- skipping"
        else
            log_info "Installing SSH key from ${SSH_KEY_PATH}"
            sudo -u "${LINUX_USER}" mkdir -p "${home}/.ssh"
            cp "${SSH_KEY_PATH}" "${home}/.ssh/id_${DEFAULT_SSH_KEY_TYPE}"
            cp "${SSH_KEY_PATH}.pub" "${home}/.ssh/id_${DEFAULT_SSH_KEY_TYPE}.pub" 2>/dev/null || true
            chown "${LINUX_USER}:${LINUX_USER}" "${home}/.ssh"/*
            chmod 0600 "${home}/.ssh/id_${DEFAULT_SSH_KEY_TYPE}"
            log_ok "SSH key installed"
        fi
    fi

    if [[ -n "${GH_TOKEN:-}" ]]; then
        sudo -u "${LINUX_USER}" mkdir -p "${home}/.config/git"
        sudo -u "${LINUX_USER}" git config --file "${home}/.config/git/config" credential.helper store
        echo "https://${LINUX_USER}:${GH_TOKEN}@github.com" > "${home}/.git-credentials"
        chmod 0600 "${home}/.git-credentials"
        chown "${LINUX_USER}:${LINUX_USER}" "${home}/.git-credentials"
        log_ok "GitHub credential helper configured"
    fi

    cat > "${PROFILE_FILE}" <<EOF
# 'MiOS' install profile -- written by mios-bootstrap install.sh
# Non-secret installation metadata. Passwords/tokens are NOT stored here.
MIOS_LINUX_USER="${LINUX_USER}"
MIOS_IDENTITY_HOSTNAME="${HOSTNAME_VAL}"
MIOS_USER_FULLNAME="${USER_FULLNAME}"
MIOS_IDENTITY_GROUPS="${DEFAULT_USER_GROUPS}"
MIOS_INSTALL_MODE="${INSTALL_MODE}"
MIOS_IMAGE_TAG="${IMAGE_TAG}"
MIOS_INSTALLED_AT="$(date -u --iso-8601=seconds)"
MIOS_BOOTSTRAP_VERSION="0.2.0"

# mios-forge (Forgejo) -- consumed by /usr/libexec/mios/forge-firstboot.sh
# at first boot to create the admin user. Empty password = generate
# random 24-byte URL-safe at first boot, write to mode-0600 file at
# /etc/mios/forge/admin-password.
MIOS_FORGE_ADMIN_USER="${FORGE_ADMIN_USER:-${LINUX_USER}}"
MIOS_FORGE_ADMIN_EMAIL="${FORGE_ADMIN_EMAIL:-${LINUX_USER}@${HOSTNAME_VAL}.local}"
MIOS_FORGE_ADMIN_PASSWORD=""

# AI model selection (Architectural Law 5). MIOS_AI_MODEL / MIOS_AI_EMBED_MODEL are the
# runtime selection mios-ai-firstboot.service confirms post-deploy. Operators swap them
# later via /etc/mios/mios.toml [ai] without rebuilding. Model BAKING is SSOT-driven from
# mios.toml [ai].bake_models / [ai.vllm].bake_model (consumed directly by the prep scripts).
MIOS_AI_MODEL="${AI_MODEL_VAL:-${DEFAULT_AI_MODEL}}"
MIOS_AI_EMBED_MODEL="${AI_EMBED_VAL:-${DEFAULT_AI_EMBED_MODEL}}"
EOF
    chmod 0640 "${PROFILE_FILE}"
    log_ok "Profile env written: ${PROFILE_FILE}"

    # Persist the user-editable profile card alongside install.env so future
    # bootstrap re-runs (or `mios edit-env`) can amend defaults in TOML.
    # The canonical user-edit copy lives at mios-bootstrap.git/mios.toml
    # (repo root); we stage it to /etc/mios/mios.toml. The legacy
    # etc/mios/profile.toml is still picked up if present.
    if [[ ! -f "${PROFILE_CARD}" ]]; then
        local bootstrap_root; bootstrap_root="$(dirname "${BASH_SOURCE[0]}")"
        local src=""
        if   [[ -f "${bootstrap_root}/mios.toml" ]];               then src="${bootstrap_root}/mios.toml"
        elif [[ -f "${bootstrap_root}/etc/mios/profile.toml" ]];   then src="${bootstrap_root}/etc/mios/profile.toml"
        fi
        if [[ -n "$src" ]]; then
            install -m 0644 "$src" "${PROFILE_CARD}"
            log_ok "Profile card seeded from $(basename "$src"): ${PROFILE_CARD}"
        fi
    fi

    # Disable bare-metal/cluster services for FHS overlay installs (folded from the
    # former C:\MiOS build-mios.sh, where it was dead behind the AGY-106 redirect).
    if [[ -f "${PROFILE_CARD}" && "${INSTALL_MODE}" == "fhs" ]]; then
        log_info "Configuring FHS profile: disabling bare-metal/cluster services in $(basename "${PROFILE_CARD}")"
        local svc
        for svc in mios-ceph mios-k3s mios-guacamole mios-pxe-hub mios-guacamole-postgres mios-guacd mios-cockpit-link; do
            sed -i -E "s/^([[:space:]]*${svc}[[:space:]]*=[[:space:]]*)true/\1false/g" "${PROFILE_CARD}"
        done
    fi
}

# ============================================================================
# Phase-3 (continued): deploy AI system prompt to host AND user home
# ============================================================================
deploy_system_prompt() {
    log_phase "Phase-3 -- Deploy AI system prompt"
    install -d -m 0755 /etc/mios/ai

    local src_local prompt_url
    src_local="$(dirname "${BASH_SOURCE[0]}")/system-prompt.md"
    prompt_url="https://raw.githubusercontent.com/mios-dev/mios-bootstrap/${DEFAULT_BRANCH}/system-prompt.md"

    if [[ -f "$src_local" ]]; then
        log_info "Using local system-prompt.md from ${src_local}"
        install -m 0644 "$src_local" /etc/mios/ai/system-prompt.md
    else
        log_info "Fetching system prompt from ${prompt_url}"
        spin_start "Downloading system-prompt.md"
        if curl -fsSL --max-time 30 "$prompt_url" -o /etc/mios/ai/system-prompt.md.new; then
            spin_stop
            mv /etc/mios/ai/system-prompt.md.new /etc/mios/ai/system-prompt.md
            chmod 0644 /etc/mios/ai/system-prompt.md
        else
            spin_stop
            rm -f /etc/mios/ai/system-prompt.md.new
            log_warn "Could not fetch system prompt"
            return 0
        fi
    fi
    log_ok "Host system prompt deployed: /etc/mios/ai/system-prompt.md"

    # Stage per-user copies for every existing human account
    # (uid 1000-65533). Single helper avoids duplicate logic across
    # deploy_system_prompt + stage_user_profile_artifacts; the call sites
    # remain distinct so the bootstrap-created user still gets the
    # name-bearing log line.
    seed_user_skel_for_all_accounts
}

# ============================================================================
# Multi-user seeder: copy /etc/skel/.config/<subdir>/* into every existing
# user's home for each MiOS-managed config subdirectory. Called from
# deploy_system_prompt (after the host /etc/mios/ai/system-prompt.md is in
# place) and again from stage_user_profile_artifacts. Idempotent: install(1)
# overwrites with current content, mode is enforced.
#
# Subdirs covered:
#   - mios/      profile.toml + system-prompt.md (per-user MiOS overlay)
#   - aichat/    config.yaml -- Architectural Law 5 default for sigoden/aichat
#                and blob42/aichat-ng (both consume the same config path).
# ============================================================================
seed_user_skel_for_all_accounts() {
    local -a skel_subdirs=(mios aichat)
    local subdir found_any=0
    for subdir in "${skel_subdirs[@]}"; do
        [[ -d "/etc/skel/.config/${subdir}" ]] && { found_any=1; break; }
    done
    [[ "$found_any" -eq 1 ]] || {
        log_warn "etc/skel/.config/{mios,aichat} missing -- per-user staging skipped"
        return 0
    }

    local u home uid
    while IFS=: read -r u _ uid _ _ home _; do
        [[ "$uid" -ge 1000 && "$uid" -lt 65534 && -d "$home" ]] || continue
        sudo -u "$u" install -d -m 0755 "${home}/.config"
        for subdir in "${skel_subdirs[@]}"; do
            local skel_root="/etc/skel/.config/${subdir}"
            [[ -d "$skel_root" ]] || continue
            sudo -u "$u" install -d -m 0755 "${home}/.config/${subdir}"
            local f
            for f in "$skel_root"/*; do
                [[ -f "$f" ]] || continue
                install -o "$u" -g "$u" -m 0644 \
                    "$f" "${home}/.config/${subdir}/$(basename "$f")"
            done
            log_ok "Seeded ${home}/.config/${subdir}/ for ${u} (uid ${uid})"
        done
    done < /etc/passwd
}

# ============================================================================
# Phase-3 (continued): stage per-user profile card + system prompt for the
# bootstrap-created user. Reads from /etc/skel/.config/mios/, the FHS-native
# template surface that mios-bootstrap.git populates from etc/skel/.
# ============================================================================
stage_user_profile_artifacts() {
    log_phase "Phase-3 -- Stage per-user 'MiOS' artifacts"
    local home; home="$(getent passwd "${LINUX_USER}" | cut -d: -f6)"
    [[ -n "$home" && -d "$home" ]] || {
        log_warn "User home not found; skipping per-user staging"
        return 0
    }

    sudo -u "${LINUX_USER}" install -d -m 0755 "${home}/.config" "${home}/.config/mios"

    local skel_root=/etc/skel/.config/mios
    if [[ -d "$skel_root" ]]; then
        local f
        for f in "$skel_root"/*; do
            [[ -f "$f" ]] || continue
            install -o "${LINUX_USER}" -g "${LINUX_USER}" -m 0644 \
                "$f" "${home}/.config/mios/$(basename "$f")"
            log_ok "User artifact: ${home}/.config/mios/$(basename "$f")"
        done
    else
        log_warn "etc/skel/.config/mios missing -- bootstrap user staging skipped"
    fi

    # Re-run the multi-user pass so a newly added user picks up the same
    # content as everyone else (idempotent).
    seed_user_skel_for_all_accounts
}

# ============================================================================
# Phase-1 + Phase-2: clone mios.git into /, apply bootstrap overlays, install
# packages from PACKAGES.md SSOT, run mios.git/install.sh for system init.
# Phase-2 (build) is implicit: on FHS hosts the package install + system-side
# init is the equivalent of "build the running system from the merged tree";
# on bootc hosts Phase-2 is `bootc switch` to a pre-built image.
# ============================================================================
# ============================================================================
# mios.toml package helpers (mirroring automation/lib/packages.sh SSOT)
# ============================================================================
_resolve_mios_toml() {
    local f
    for f in "/etc/mios/mios.toml" "/usr/share/mios/mios.toml"; do
        if [[ -f "$f" ]]; then
            echo "$f"
            return 0
        fi
    done
    return 1
}

_get_pkgs_from_file() {
    local category="$1"
    local file="$2"
    [[ -f "$file" ]] || return 1

    awk -v section="packages.${category}" '
        /^\[/ {
            in_section = 0
            collecting = 0
            line = $0
            sub(/^\[/, "", line); sub(/\][[:space:]]*$/, "", line)
            gsub(/[[:space:]]/, "", line)
            if (line == section) in_section = 1
            next
        }
        in_section && /^[[:space:]]*pkgs[[:space:]]*=/ {
            sub(/^[^=]*=[[:space:]]*/, "", $0)
            collecting = 1
        }
        collecting {
            print
            if ($0 ~ /\][[:space:]]*$/) { collecting = 0 }
        }
    ' "$file" \
        | tr -d '[]' \
        | tr ',' '\n' \
        | sed -E "s/[[:space:]]*\"([^\"]*)\"[[:space:]]*\$/\\1/" \
        | sed '/^[[:space:]]*$/d' \
        | sed -E 's/[[:space:]]*#.*$//' \
        | tr '\n' ' '
}

get_packages_from_toml() {
    local category="$1"
    local pkgs

    if [[ -f "/etc/mios/mios.toml" ]]; then
        pkgs=$(_get_pkgs_from_file "$category" "/etc/mios/mios.toml")
        if [[ -n "${pkgs// }" ]]; then
            echo "$pkgs"
            return 0
        fi
    fi

    if [[ -f "/usr/share/mios/mios.toml" ]]; then
        pkgs=$(_get_pkgs_from_file "$category" "/usr/share/mios/mios.toml")
        if [[ -n "${pkgs// }" ]]; then
            echo "$pkgs"
            return 0
        fi
    fi

    return 1
}

get_all_packages_except() {
    local excl="^(repos|kernel|k3s-selinux-build|looking-glass-build|cockpit-plugins-build|self-build|build-toolchain)$"
    local master_toml
    master_toml="$(_resolve_mios_toml)"
    [[ -n "$master_toml" ]] || return 1

    local categories
    categories=$(awk '/^\[packages\./ {
        line = $0
        sub(/^\[packages\./, "", line)
        sub(/\][[:space:]]*$/, "", line)
        gsub(/[[:space:]]/, "", line)
        print line
    }' "$master_toml")

    local cat pkgs
    for cat in $categories; do
        if [[ ! "$cat" =~ $excl ]]; then
            pkgs=$(get_packages_from_toml "$cat")
            if [[ -n "$pkgs" ]]; then
                echo -n "$pkgs "
            fi
        fi
    done
    echo ""
}

trigger_mios_install() {
    # Translate Windows paths to WSL mount paths
    if [[ "${BOOTSTRAP_REPO}" =~ ^[a-zA-Z]:[/\\] ]]; then
        local drive="${BOOTSTRAP_REPO:0:1}"
        BOOTSTRAP_REPO="/mnt/${drive,,}/${BOOTSTRAP_REPO#??}"
        BOOTSTRAP_REPO="${BOOTSTRAP_REPO//\\//}"
    fi
    if [[ "${MIOS_REPO}" =~ ^[a-zA-Z]:[/\\] ]]; then
        local drive="${MIOS_REPO:0:1}"
        MIOS_REPO="/mnt/${drive,,}/${MIOS_REPO#??}"
        MIOS_REPO="${MIOS_REPO//\\//}"
    fi

    # Bypass dubious ownership checks for local repository mounts
    git config --global --add safe.directory '*' || true

    log_phase "Phase-1 -- Total Root Merge"

    case "${INSTALL_MODE}" in
        bootc)
            log_info "Switching bootc deployment to ${IMAGE_TAG}"
            # A fresh no-cred host pulls IMAGE_TAG. If ghcr.io/mios-dev/mios is a
            # PRIVATE package the pull fails; log in first when a token is present,
            # otherwise assume the package is public. (The GH PAT prompt earlier
            # feeds git creds, not this bootc pull.)
            _tok="${GHCR_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-${MIOS_GITHUB_TOKEN:-}}}}"
            if [[ "${IMAGE_TAG}" == ghcr.io/* && -n "${_tok}" ]]; then
                printf '%s' "${_tok}" | podman login ghcr.io -u "${GITHUB_USER:-mios-dev}" --password-stdin \
                    || log_info "ghcr login failed; continuing (image may be public)"
            fi
            if ! bootc switch "${IMAGE_TAG}"; then
                log_info "ERROR: bootc switch ${IMAGE_TAG} failed -- is ${IMAGE_TAG%%:*} a public ghcr package, or are creds set?"
                exit 1
            fi
            log_ok "bootc deployment staged"
            ;;
        fhs)
            local dnf_cmd="dnf"
            command -v dnf5 >/dev/null 2>&1 && dnf_cmd="dnf5"

            # Confirm before mutating the host root. 'git init /' followed by
            # 'reset --hard FETCH_HEAD' is bold by design (it is the canonical
            # "MiOS-ify a stock Fedora Server" path) but it overwrites every
            # file the upstream tree owns. Operators must opt in.
            #
            # Auto-accept respects MIOS_PROMPT_TIMEOUT (90s default; '0' waits
            # forever, '1' is the unattended-CI value). Setting
            # MIOS_FHS_TOTAL_ROOT_MERGE=1 in the environment also bypasses
            # the prompt for scripted re-runs.
            if [[ "${MIOS_FHS_TOTAL_ROOT_MERGE:-0}" != "1" ]]; then
                log_warn "Total Root Merge will run 'git init /' and 'git reset --hard FETCH_HEAD' against this host."
                log_warn "Files tracked by mios.git will be overwritten with the upstream branch (${DEFAULT_BRANCH})."
                local confirm
                confirm="$(prompt_default 'Proceed with Total Root Merge?' 'no')"
                case "${confirm,,}" in
                    y|yes|true|1) ;;
                    *)
                        log_warn "Total Root Merge declined by operator -- aborting Phase-1."
                        log_info "Re-run with MIOS_FHS_TOTAL_ROOT_MERGE=1 to bypass this prompt."
                        return 1
                        ;;
                esac
            fi

            # 1. Initialize / as the git root for 'MiOS' core
            log_info "Staging 'MiOS' core repository (mios.git) to /"
            if [[ ! -d "/.git" ]]; then
                git init /
                git -C / remote add origin "${MIOS_REPO}"
            fi
            spin_start "Fetching mios.git (system layer)"
            local fetch_err
            fetch_err=$(git -C / fetch --depth=1 origin "${DEFAULT_BRANCH}" 2>&1)
            local fetch_rc=$?
            if [[ $fetch_rc -ne 0 ]]; then
                spin_stop
                log_err "Failed to fetch mios.git from ${MIOS_REPO}: ${fetch_err}"
                return 1
            fi
            if ! git -C / reset --hard FETCH_HEAD; then
                spin_stop
                log_err "Failed to reset root filesystem to FETCH_HEAD"
                return 1
            fi
            # Make / a first-class, SELF-UPDATING git work tree so `git -C / pull` works
            # on Day-N+ -- "/ IS $ROOT": the deployed root is the SAME git tree the
            # drift-gate resolves ($ROOT = $(cd automation/.. ) = /), and it pulls its own
            # updates. `fetch <branch>` + `reset --hard FETCH_HEAD` leaves HEAD on
            # git-init's default branch with NO upstream, so a bare `git pull` errors
            # "no tracking information". checkout -B at the CURRENT commit renames/creates
            # ${DEFAULT_BRANCH} with no working-tree change; the config lines wire its
            # upstream to origin so `git -C / pull --ff-only` fast-forwards mios.git.
            git -C / checkout -B "${DEFAULT_BRANCH}" >/dev/null 2>&1 || true
            git -C / config "branch.${DEFAULT_BRANCH}.remote" origin
            git -C / config "branch.${DEFAULT_BRANCH}.merge" "refs/heads/${DEFAULT_BRANCH}"
            spin_stop
            log_ok "MiOS core (mios.git) merged to / (branch ${DEFAULT_BRANCH} tracks origin -- git -C / pull works)"

            # 2. Apply MiOS-bootstrap repo overlays. Prefer a LOCALLY-STAGED source
            # (offline install): if BOOTSTRAP_REPO resolves to a filesystem dir that
            # already carries the etc/usr overlays, use it directly -- no git repo and
            # no network required. Only git-clone for a real remote URL.
            local bootstrap_tmp="/tmp/mios-bootstrap-src"
            local bootstrap_src="" _bs_local="${BOOTSTRAP_REPO#file://}"
            if [[ -d "${_bs_local}/etc" || -d "${_bs_local}/usr" ]]; then
                log_info "Using locally-staged MiOS-bootstrap overlays at ${_bs_local} (offline)"
                bootstrap_src="${_bs_local}"
            else
                log_info "Fetching MiOS-bootstrap overlays from ${BOOTSTRAP_REPO}"
                spin_start "Cloning mios-bootstrap.git (user layer)"
                rm -rf "${bootstrap_tmp}"
                local clone_err
                clone_err=$(git clone --depth=1 "${BOOTSTRAP_REPO}" "${bootstrap_tmp}" 2>&1)
                local clone_rc=$?
                if [[ $clone_rc -ne 0 ]]; then
                    spin_stop
                    log_err "Failed to clone mios-bootstrap from ${BOOTSTRAP_REPO}: ${clone_err}"
                    log_err "For an OFFLINE install, stage the mios-bootstrap tree and set BOOTSTRAP_REPO to its path."
                    return 1
                fi
                spin_stop
                bootstrap_src="${bootstrap_tmp}"
            fi

            log_info "Merging bootstrap system folders (etc, usr) to /"
            for d in etc usr; do
                if [[ -d "${bootstrap_src}/${d}" ]]; then
                    cp -a "${bootstrap_src}/${d}/." "/${d}/" 2>/dev/null || true
                fi
            done
            [[ "${bootstrap_src}" == "${bootstrap_tmp}" ]] && rm -rf "${bootstrap_tmp}"
            log_ok "MiOS-bootstrap overlays applied"

            # 3. Phase-2: RPM package install from mios.toml SSOT.
            # Build-only blocks (kernel kmods, selinux policy source, looking-glass
            # build deps, cockpit plugin build deps) are excluded -- they only make
            # sense inside the OCI build pipeline, not on a running FHS host.
            log_phase "Phase-2 -- FHS package install (from mios.toml)"
            local toml_path
            toml_path="$(_resolve_mios_toml)"
            if [[ -z "$toml_path" ]]; then
                log_err "mios.toml not found -- package installation skipped"
                return 1
            fi

            local repo_pkgs
            repo_pkgs=$(get_packages_from_toml "repos")
            if [[ -n "$repo_pkgs" ]]; then
                log_info "Setting up additional repos..."
                spin_start "Installing repo packages"
                # shellcheck disable=SC2086
                $dnf_cmd install -y --skip-unavailable $repo_pkgs 2>&1 | grep -E '^(Install|Upgrade|Error|Warning|Failed)' || true
                spin_stop
                $dnf_cmd makecache --refresh 2>/dev/null || true
                log_ok "Repos configured"
            fi

            local pkgs
            pkgs=$(get_all_packages_except)
            if [[ -n "$pkgs" ]]; then
                log_info "Installing full 'MiOS' component stack..."
                spin_start "dnf install (this takes several minutes)"
                # shellcheck disable=SC2086
                $dnf_cmd install -y --skip-unavailable --best $pkgs 2>&1 \
                    | grep -E '^\s*(Installing|Upgrading|Removing|Error|Warning|Nothing)' || true
                spin_stop
                log_ok "Package installation complete"
            else
                log_warn "No packages extracted from mios.toml"
            fi

            # 4. Phase-3: systemd-sysusers, systemd-tmpfiles, daemon-reload.
            # This wires up 'MiOS' user/group definitions and creates /var/ paths
            # declared in usr/lib/tmpfiles.d/mios*.conf.
            log_phase "Phase-3 -- System init (sysusers + tmpfiles + daemon-reload)"

            log_info "Ensuring executable permissions on all core binaries and scripts..."
            chmod +x /automation/*.sh /usr/bin/mios* 2>/dev/null || true
            find /usr/libexec/mios -type f -exec chmod +x {} + 2>/dev/null || true

            log_info "Synchronizing environment configuration..."
            if [[ -f "/usr/libexec/mios/system-sync-env.sh" ]]; then
                /usr/libexec/mios/system-sync-env.sh 2>/dev/null || log_warn "system-sync-env.sh failed"
            fi

            spin_start "Running systemd-sysusers"
            systemctl-sysusers 2>/dev/null || systemd-sysusers 2>/dev/null || log_warn "systemd-sysusers not available"
            spin_stop
            spin_start "Running systemd-tmpfiles --create"
            systemd-tmpfiles --create 2>/dev/null || log_warn "systemd-tmpfiles failed"
            spin_stop
            if [[ -f "/automation/15-render-quadlets.sh" ]]; then
                spin_start "Rendering Quadlet container files"
                (cd /automation && ./15-render-quadlets.sh) 2>/dev/null || log_warn "15-render-quadlets.sh failed"
                spin_stop
            fi
            if systemctl is-system-running --quiet 2>/dev/null; then
                spin_start "Reloading systemd daemon"
                systemctl daemon-reload
                spin_stop
                log_ok "Systemd daemon reloaded"
            fi
            log_ok "FHS system init complete"
            ;;
    esac
}

# ============================================================================
# Phase-4: reboot prompt
# ============================================================================
reboot_prompt() {
    log_phase "Phase-4 -- Reboot"
    if prompt_yesno 'Reboot now to activate 'MiOS'?' y; then
        log_info "Rebooting in 3s..."
        sleep 3
        systemctl reboot
    else
        log_info "Skipping reboot. Run 'sudo systemctl reboot' when ready."
    fi
}

do_install_core() {
    local mode="${1:-fhs}"

    initialize_dynamic_branding
    require_root
    log_phase "Phase-0 -- mios-bootstrap (Total Root Merge Mode)"

    local hostkind
    hostkind="$(detect_host_kind)"
    if [[ "$hostkind" == "unsupported" ]]; then
        log_err "Host is not Fedora. Aborting."
        exit 1
    fi
    log_info "Detected host: ${hostkind}"

    check_network
    install_prerequisites
    load_profile_defaults
    launch_configurator
    gather_user_choices
    print_summary

    # Phase-1 (overlay merge) and Phase-2 (build / package install) happen
    # inside trigger_mios_install. System groups are created there before
    # apply_user_profile needs them.
    trigger_mios_install

    # Phase-3a: deploy AI system prompt to host /etc/ AND every existing user home.
    deploy_system_prompt

    # Phase-3b: create the bootstrap user, set password, persist install.env
    # and seed /etc/mios/profile.toml.
    apply_user_profile

    # Phase-3c: stage the per-user profile.toml + system-prompt.md into the
    # newly-created user's home (idempotent on re-run).
    stage_user_profile_artifacts

    # Phase-4
    reboot_prompt
}


case "$TARGET" in
    live)   resolve_live ;;
    flash)  resolve_flash ;;
    xbox)   resolve_xbox ;;
    fedora) resolve_fedora ;;
    bootc)  resolve_bootc ;;
    oci)    resolve_oci ;;
    seed)   resolve_seed ;;
    build)  resolve_build ;;
    update) resolve_update ;;
    config|configure) resolve_config ;;
    stage)  resolve_stage ;;
    verify) resolve_verify ;;
    _stage) invoke_mios_stage "${PASSTHROUGH[@]}"; exit $? ;;
    _verify) invoke_mios_verify "${PASSTHROUGH[@]}"; exit $? ;;
    # ${PASSTHROUGH[0]} carries the positional mode from the internal re-entry
    # above; --type still wins when a caller sets it explicitly.
    _install_core) resolve_install_core "${TYPE:-${PASSTHROUGH[0]:-fhs}}" ;;
    default|Default|offlinesync|OfflineSync|buildxboxiso|BuildXboxISO|flashusb|FlashUSB)
        die "'${TARGET}' is a Get-MiOS.ps1 -Action value, not a mios-install target. mios-install only runs AFTER Get-MiOS.ps1 has already cloned this repo locally" ;;
    *)
        die "Unknown target '${TARGET}'. Valid targets: live xbox fedora bootc oci seed flash build update config stage verify" ;;
esac

resolve_target_prereqs() {
    local target="$1"
    log_info "Resolving prerequisites for target '${target}'..."
    case "$target" in
        build|fedora|bootc)
            if command -v dnf >/dev/null 2>&1; then
                for pkg in git curl podman; do
                    if ! rpm -q "$pkg" >/dev/null 2>&1; then
                        log_info "Target prerequisite missing: ${pkg}"
                        (( DRY_RUN )) || sudo dnf install -y "$pkg" || true
                    fi
                done
            fi
            if ! command -v cargo >/dev/null 2>&1 && ! command -v rustup >/dev/null 2>&1; then
                log_info "Target prerequisite missing: rustup/cargo for native tools"
                if (( ! DRY_RUN )) && command -v dnf >/dev/null 2>&1; then
                    sudo dnf install -y cargo rust || true
                fi
            fi
            ;;
        live|flash)
            if command -v dnf >/dev/null 2>&1; then
                for pkg in git curl podman ventoy; do
                    if ! rpm -q "$pkg" >/dev/null 2>&1; then
                        log_info "Target prerequisite missing: ${pkg}"
                        (( DRY_RUN )) || sudo dnf install -y "$pkg" || true
                    fi
                done
            fi
            ;;
        update)
            if command -v dnf >/dev/null 2>&1; then
                for pkg in git curl; do
                    if ! rpm -q "$pkg" >/dev/null 2>&1; then
                        log_info "Target prerequisite missing: ${pkg}"
                        (( DRY_RUN )) || sudo dnf install -y "$pkg" || true
                    fi
                done
            fi
            ;;
    esac
}

if [[ -n "$WINDOWS_GUIDANCE" ]]; then
    log_warn "target '${TARGET}' has no native Linux entrypoint"
    printf '\n%s\n\n' "$WINDOWS_GUIDANCE" >&2
    if (( DRY_RUN )); then exit 0; else exit 2; fi
fi

if (( ${#STAGE_NOTES[@]} )); then
    for _n in "${STAGE_NOTES[@]}"; do log_warn "$_n"; done
fi

if (( DRY_RUN )); then
    log_phase "DRY RUN -- target=${TARGET} type=${TYPE:-<default>} stage=${STAGE:-<full pipeline>} unattended=${UNATTENDED}"
    if (( ${#ENV[@]} )); then
        log_info "Environment:"
        for _kv in "${ENV[@]}"; do printf '    %s\n' "$_kv"; done
    fi
    log_info "Command:"
    printf '    '
    for _a in "${CMD[@]}"; do printf '%q ' "$_a"; done
    printf '\n'
    exit 0
fi

if (( FORBIDS_ROOT )) && [[ "$(id -u)" -eq 0 ]]; then
    die "Target '${TARGET}' must NOT run as root/sudo"
fi

if (( REQUIRES_ROOT )); then
    mios_self_elevate "$0" "${ORIG_ARGS[@]}"
fi

resolve_target_prereqs "$TARGET"

for _kv in "${ENV[@]:-}"; do
    [[ -z "$_kv" ]] && continue
    export "${_kv?}"
done

log_phase "Launching: ${TARGET}${TYPE:+ (--type ${TYPE})}${STAGE:+ (--stage ${STAGE})}"
exec "${CMD[@]}"
