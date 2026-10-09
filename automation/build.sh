#!/bin/bash
# AI-hint: This script is the primary build runner for MiOS, managing the build lifecycle by parsing `mios.toml` configurations, enforcing environment constraints, and ren...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Captured before common.sh re-exports the resolved SSOT environment over it (ADR-0025).
_requested_profile="${MIOS_PROFILES_DEFAULT:-}"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/packages.sh"
register_common_masks

export MIOS_TOML="${MIOS_TOML:-/ctx/usr/share/mios/mios.toml}"
export MIOS_VENDOR_TOML="${MIOS_VENDOR_TOML:-$MIOS_TOML}"
BUILD_LOG="/tmp/mios-build.log"
VERSION_STR="$(cat "${SCRIPT_DIR}/../VERSION" 2>/dev/null || cat /ctx/VERSION 2>/dev/null || grep -m1 -E '^[[:space:]]*mios_version' "${MIOS_TOML:-${SCRIPT_DIR}/../usr/share/mios/mios.toml}" 2>/dev/null | sed -E 's/[^"]*"([^"]*)".*/\1/')"

exec > >(mask_filter | tee -a "$BUILD_LOG") 2>&1

_row() {
    local content="$1"
    printf '%s\n' "$content"
}

_fail_report() {
    local -a fails=("${@}")
    if [[ ${#fails[@]} -eq 0 ]]; then
        _row " FAILURE LOG: (none)"
    else
        _row " FAILURE LOG:"
        for entry in "${fails[@]}"; do
            _row "  [FAIL]  ${entry}"
        done
    fi
}

_warn_report() {
    local -a warns=("${@}")
    if [[ ${#warns[@]} -eq 0 ]]; then
        _row " WARNING LOG: (none)"
    else
        _row " WARNING LOG:"
        for entry in "${warns[@]}"; do
            _row "  [WARN]  ${entry}"
        done
    fi
}

_check_critical_packages() {
    local critical pkg
    local -a critical_packages=()
    VALIDATION_FAIL=0
    PKG_OK=0
    PKG_MISS=0
    if ! critical="$(get_packages_strict critical)"; then
        printf '[FATAL] critical package catalog is empty, missing or invalid\n' >&2
        VALIDATION_FAIL=1
        return 1
    fi
    # Package closures are a space-separated line, not one package per line.
    IFS=$' \t\n' read -r -a critical_packages <<< "$critical"
    for pkg in "${critical_packages[@]}"; do
        if rpm -q "$pkg" > /dev/null 2>&1; then
            printf '|  %-38s [ OK ] |\n' "$pkg"
            PKG_OK=$(( PKG_OK + 1 ))
        else
            printf '|  %-38s [MISS] |\n' "$pkg"
            PKG_MISS=$(( PKG_MISS + 1 ))
            VALIDATION_FAIL=$(( VALIDATION_FAIL + 1 ))
        fi
    done
    [[ "$VALIDATION_FAIL" -eq 0 ]]
}

export SYSTEMD_OFFLINE=1
export container=podman

_build_root="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ ! -e "${_build_root}/.git" ]] && command -v git >/dev/null 2>&1; then
    (
        cd "${_build_root}"
        git init -q 2>/dev/null || true
        _ssot_name="$(grep -m1 -E '^[[:space:]]*fullname[[:space:]]*=' "${MIOS_TOML:-/usr/share/mios/mios.toml}" 2>/dev/null | sed -E 's/[^"]*"([^"]*)".*/\1/' || echo "User")"
        _ssot_email="$(grep -m1 -E '^[[:space:]]*email[[:space:]]*=' "${MIOS_TOML:-/usr/share/mios/mios.toml}" 2>/dev/null | sed -E 's/[^"]*"([^"]*)".*/\1/' || echo "User@localhost")"
        git config user.name "${_ssot_name:-User}" 2>/dev/null || true
        git config user.email "${_ssot_email:-user@localhost}" 2>/dev/null || true
        git add -A 2>/dev/null || true
    ) 2>/dev/null || true
fi

if [[ ! -f "$MIOS_TOML" ]]; then
    printf '[FATAL] mios.toml SSOT not found: %s\n' "$MIOS_TOML" >&2
    printf '        Set $MIOS_TOML to the path of the package manifest\n' >&2
    printf '        (default: /ctx/usr/share/mios/mios.toml during OCI build)\n' >&2
    exit 1
fi

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so this lookup could never succeed and the
# whole Rust dispatch below it was dead on every build ever run (T-1018).
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "${_build_root}/src/mios-rs/target/release/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

# miosd resolves the registry as $MIOS_ROOT/usr/share/mios/mios.toml, so the
# root has to be derived from the manifest this script already validated --
# not left to miosd's "." default, which depends on the caller's cwd.
_mios_root="${MIOS_TOML%/usr/share/mios/mios.toml}"
[[ "$_mios_root" == "$MIOS_TOML" ]] && _mios_root="$_build_root"

# The registry supplies both ordered execution and fatality policy; no second
# phase roster or substring allowlist can disagree with it.
[[ -n "$_miosd" ]] || { printf '[MISSING] Native build registry and progress engine miosd are required\n' >&2; exit 1; }
ALL_SCRIPTS=()
declare -A PHASE_FATAL=()
_profile_args=()
[[ -n "$_requested_profile" ]] && _profile_args=(--profile "$_requested_profile")
# The profile's package sections, SPACE-separated: lib/packages.sh matches
# " $BUILD_PROFILE_SECTIONS " == *" <section> "*, so miosd's one-per-line list
# left verbatim selected no section at all under any profile but "*".
if ! BUILD_PROFILE_SECTIONS="$(MIOS_ROOT="$_mios_root" "$_miosd" build --sections "${_profile_args[@]}" 2>&1 | tr '\n' ' ')"; then
    printf '[FATAL] miosd build --sections failed: %s\n' "$BUILD_PROFILE_SECTIONS" >&2
    exit 1
fi
export BUILD_PROFILE_SECTIONS
_phase_list="$(mktemp)"
if ! MIOS_ROOT="$_mios_root" "$_miosd" build --list "${_profile_args[@]}" >"$_phase_list"; then
    printf '[FAIL] Native build registry could not resolve selected profile\n' >&2
    rm -f "$_phase_list"
    exit 1
fi
while IFS=: read -r _name _fatal _extra; do
    [[ "$_name" =~ ^[a-zA-Z0-9_-]+\.sh$ && ( "$_fatal" == true || "$_fatal" == false ) && -z "$_extra" ]] || { printf '[FAIL] Invalid native build phase: %s\n' "$_name" >&2; exit 1; }
    [[ -z "${PHASE_FATAL[$_name]+present}" ]] || { printf '[FAIL] Duplicate native build phase: %s\n' "$_name" >&2; exit 1; }
    PHASE_FATAL[$_name]=$_fatal
    case " $_name " in
        ' 01-system-files-overlay.sh '|' 55-native-build.sh '|' 97-ssot-lint.sh '|' 98-drift-checks.sh '|' 99-postcheck.sh ') continue ;;
    esac
    ALL_SCRIPTS+=("$SCRIPT_DIR/$_name")
done < "$_phase_list"
rm -f "$_phase_list"
[[ ${#ALL_SCRIPTS[@]} -gt 0 ]] || { printf '[MISSING] Native build plan selected no phases\n' >&2; exit 1; }

rm -f "$MIOS_VERSION_MANIFEST"
record_version mios       "$VERSION_STR"                               "git:$(cat /ctx/VERSION 2>/dev/null || echo unknown)"
_base_image="${BASE_IMAGE:-}"
if [[ -z "$_base_image" ]]; then
    _base_image="$(MIOS_ROOT="$_mios_root" /usr/bin/mios-toml-get image base)"
fi
[[ -n "$_base_image" ]] || { printf '[MISSING] Resolved SSOT build base image is required\n' >&2; exit 1; }
record_version base-image "$_base_image" "resolved build base image"
record_version kernel     "$(find /usr/lib/modules/ -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort -V | tail -1)" "from base image"

SCRIPT_COUNT=0
FAIL_LOG=()
WARN_LOG=()
WARNED_JSON=()

_post_bloat() {
BLOAT_PACKAGES=$(get_packages "bloat" 2>/dev/null || true)
if [[ -n "${BLOAT_PACKAGES:-}" ]]; then
    echo "  Removing bloat packages"
    $DNF_BIN "${DNF_SETOPT[@]}" remove -y --no-autoremove $BLOAT_PACKAGES 2>/dev/null || true
fi
systemctl mask packagekit.service 2>/dev/null || true
for app in gnome-tour gnome-initial-setup; do
    desktop="/usr/share/applications/${app}.desktop"
    if [[ -f "$desktop" ]]; then
        mkdir -p /usr/local/share/applications
        grep -v '^NoDisplay=' "$desktop" > "/usr/local/share/applications/${app}.desktop"
        echo "NoDisplay=true" >> "/usr/local/share/applications/${app}.desktop"
        _row "  Hidden: ${app} (NoDisplay=true)"
    fi
done
    return 0
}

_post_package_health() {
if ! _check_critical_packages; then
    _finding missing "Critical package health: ${PKG_MISS} missing package(s), ${VALIDATION_FAIL} validation failure(s)"
    return 1
fi
if rpm -qa 'kmod-nvidia*' 2>/dev/null | grep -q . ; then
    printf '|  %-38s [ OK ] |\n' "NVIDIA kmod(s)"
else
    _finding warn "NVIDIA kmod(s) absent -- using base driver policy"
fi
if compgen -G "/etc/pki/akmods/certs/*.der" > /dev/null 2>/dev/null; then
    printf '|  %-38s [ OK ] |\n' "MOK certs"
fi
if rpm -q malcontent-libs > /dev/null 2>&1; then
    printf '|  %-38s [ OK ] |\n' "malcontent-libs (flatpak dep)"
else
    printf '|  %-38s [WARN] |\n' "malcontent-libs MISSING -- flatpak may break"
    _finding warn "malcontent-libs missing -- flatpak may break"
fi
    return 0
}

_post_invariants() {
if [[ -f "${SCRIPT_DIR}/99-postcheck.sh" ]]; then
    bash "${SCRIPT_DIR}/99-postcheck.sh"
else
    _finding missing "Required build gate 99-postcheck.sh is missing"
    return 125
fi
    return 0
}

_post_ssot() {
if [[ -f "${SCRIPT_DIR}/97-ssot-lint.sh" ]]; then
    bash "${SCRIPT_DIR}/97-ssot-lint.sh"
else
    _finding missing "Required build gate 97-ssot-lint.sh is missing"
    return 125
fi
    return 0
}

_post_drift() {
if [[ -f "${SCRIPT_DIR}/98-drift-checks.sh" ]]; then
    _drift_root="$(cd "${SCRIPT_DIR}/.." && pwd)"
    git config --global --add safe.directory "${_drift_root}" 2>/dev/null || true
    git config --global --add safe.directory '*' 2>/dev/null || true
    if git -C "${_drift_root}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "${_drift_root}" config --local --unset-all http.https://github.com/.extraheader 2>/dev/null || true
    fi
    for _projection in uki-cmdline ipa-enroll cockpit; do
        mios_project_config "$_drift_root" "$_projection"
    done
    if command -v python3 >/dev/null 2>&1; then
        # The edge goldens follow the build SSOT like the image surfaces 65-bake-hyprland.sh rendered.
        for _gen in ux/wm_config_gen.py desktop/gpu_terminal.py win/wt_profile_inject.py; do
            python3 "${_drift_root}/usr/libexec/mios/${_gen}" --write-fixture "${_drift_root}"
        done
        /usr/libexec/mios/mios-gen render-tmux-theme --write-fixture "${_drift_root}"
    else
        echo "[reproject] WARN: python3 unavailable"
    fi
    bash "${SCRIPT_DIR}/98-drift-checks.sh"
else
    _finding missing "Required build gate 98-drift-checks.sh is missing"
    return 125
fi
    return 0
}

_post_agent_tests() {
_agent_pipe_dir="$(cd "${SCRIPT_DIR}/.." && pwd)/usr/lib/mios/agent-pipe"
_test_py="/usr/lib/mios/agents/.venv/bin/python3"
if [[ -d "$_agent_pipe_dir" ]] && [[ -x "$_test_py" ]]; then
    _test_fails=0
    _test_count=0
    _DB_INTEGRATION_TESTS=" test_mios_db_config.py test_mios_build_catalog.py test_mios_config_audit.py test_mios_redact.py test_mios_vector.py "
    shopt -s nullglob
    for _t in "$_agent_pipe_dir"/test_mios_*.py; do
        _tb="$(basename "$_t")"
        _test_count=$((_test_count + 1))
        if [[ "$_DB_INTEGRATION_TESTS" == *" $_tb "* ]]; then
            _finding skip "$_tb: DB integration requires live pgvector; not certified by image build"
            continue
        fi
        # shellcheck disable=SC2046  # compgen emits one name per line; unset needs them split
        if _tout="$( cd "$_agent_pipe_dir" && { unset $(compgen -v MIOS_ 2>/dev/null); "$_test_py" "$_tb"; } 2>&1 | tr -d '\0' )"; then
            _row "  [ OK ] $_tb"
        else
            _row "  [FAIL] $_tb"
            printf '%s\n' "$_tout" | sed 's/^/      /' >&2
            _test_fails=$((_test_fails + 1))
        fi
    done
    shopt -u nullglob
    if [[ "$_test_fails" -gt 0 ]]; then
        die "Agent-pipe unit tests: ${_test_fails} test script failed"
    fi
    [[ "$_test_count" -gt 0 ]] || { _finding missing "No agent-pipe test subjects discovered"; return 125; }
    _row "  Agent-pipe test scripts examined: $_test_count; DB exclusions recorded separately"
else
    _finding missing "Required agent test environment or agent-pipe source missing"
    return 125
fi
    return 0
}

_post_libexec_tests() {
_libexec_dir="$(cd "${SCRIPT_DIR}/.." && pwd)/usr/libexec/mios"
if [[ -d "$_libexec_dir" ]] && command -v python3 >/dev/null 2>&1; then
    _lx_fails=0
    _lx_count=0
    shopt -s nullglob
    for _t in "$_libexec_dir"/test_mios_*.py; do
        _lx_count=$((_lx_count + 1))
        # MIOS_* resolver exports so hermetic libexec tests match the drift-gate.
        # shellcheck disable=SC2046  # compgen emits one name per line; unset needs them split
        if _lxout="$( cd "$_libexec_dir" && { unset $(compgen -v MIOS_ 2>/dev/null); PYTHONIOENCODING=utf-8 python3 "$(basename "$_t")"; } 2>&1 )"; then
            _row "  [ OK ] $(basename "$_t")"
        else
            _row "  [FAIL] $(basename "$_t")"
            printf '%s\n' "$_lxout" | sed 's/^/      /' >&2
            _lx_fails=$((_lx_fails + 1))
        fi
    done
    shopt -u nullglob
    [[ "$_lx_count" -gt 0 ]] || { _finding missing "No libexec test subjects discovered"; return 125; }
    if [[ "$_lx_fails" -gt 0 ]]; then
        die "Libexec unit tests: ${_lx_fails} test script failed"
    fi
else
    _finding missing "Required libexec test environment missing"
    return 125
fi
    return 0
}

_post_image_digests() {
if command -v skopeo >/dev/null 2>&1; then
    shopt -s nullglob
    for q in /usr/share/containers/systemd/*.container /etc/containers/systemd/*.container; do
        img=$(awk -F= '/^Image=/{print $2; exit}' "$q" 2>/dev/null)
        [[ -n "$img" ]] || continue
        digest=$(skopeo inspect "docker://${img}" 2>/dev/null \
            | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("Digest",""))' 2>/dev/null \
            || true)
        record_version "quadlet:$(basename "$q" .container)" "$img" "${digest:-<unresolved>}"
    done
    shopt -u nullglob
else
    _finding missing "Required image digest inspector skopeo unavailable"
    return 125
fi
    return 0
}

_post_log_chain() {
mkdir -p "$MIOS_LOG_DIR"
cp -v /var/log/dnf5.log* /var/log/hawkey.log "$MIOS_LOG_DIR/" 2>/dev/null || true

if [[ -f "$MIOS_VERSION_MANIFEST" ]]; then
    install -m 0644 "$MIOS_VERSION_MANIFEST" "$MIOS_VERSION_MANIFEST_FINAL"
    _row "  Version manifest: ${MIOS_VERSION_MANIFEST_FINAL} ($(wc -l < "$MIOS_VERSION_MANIFEST") rows)"
fi

{
    echo "# 'MiOS' ${VERSION_STR} Unified Build Log Chain"
    echo ""
    echo "# ====== build-time :latest -> observed-version manifest ======"
    if [[ -f "$MIOS_VERSION_MANIFEST" ]]; then
        cat "$MIOS_VERSION_MANIFEST"
    else
        echo ""
    fi
    for step_log in "$PROGRESS_DIR"/stage-*.log; do
        [[ -f "$step_log" ]] || continue
        echo ""
        echo "# ====== $(basename "$step_log") ======"
        cat "$step_log"
    done
    echo ""
    echo "# ====== mios-build.log ======"
    [[ -f "$BUILD_LOG" ]] && cat "$BUILD_LOG" || true
} > "$MIOS_BUILD_CHAIN_LOG"
cp "$MIOS_BUILD_CHAIN_LOG" "$MIOS_BUILD_LOG" 2>/dev/null || true

gzip -9f "$MIOS_BUILD_CHAIN_LOG" "$MIOS_BUILD_LOG" 2>/dev/null || true
gzip -9f "$MIOS_LOG_DIR"/dnf5.log* "$MIOS_LOG_DIR/hawkey.log" 2>/dev/null || true
_row "  Unified chain log: ${MIOS_BUILD_CHAIN_LOG}.gz"
_row "  Step count in chain: ${SCRIPT_COUNT}"

SBOM_ARTIFACTS_DIR="${MIOS_USR_DIR:-/usr/share/mios}/artifacts/sbom"
mkdir -p "$SBOM_ARTIFACTS_DIR" 2>/dev/null || true
MARKER_FILE="${SBOM_ARTIFACTS_DIR}/non-fatal-failures.json"
if [[ -d "$SBOM_ARTIFACTS_DIR" ]]; then
    if [[ ${#WARNED_JSON[@]} -gt 0 ]]; then
        (
            echo "["
            i=0
            len=${#WARNED_JSON[@]}
            for item in "${WARNED_JSON[@]}"; do
                i=$((i + 1))
                if [[ $i -eq $len ]]; then
                    echo "  $item"
                else
                    echo "  $item,"
                fi
            done
            echo "]"
        ) > "$MARKER_FILE"
    else
        echo "[]" > "$MARKER_FILE"
    fi
    _row "  Non-fatal failures marker: ${MARKER_FILE}"
fi
    return 0
}

_post_finalize() {
$DNF_BIN "${DNF_SETOPT[@]}" clean all 2>/dev/null || true
rm -rf /var/cache/dnf /var/cache/libdnf5 /tmp/geist-font /tmp/*.tar* /tmp/*.rpm 2>/dev/null || true
# /usr/share/man is NOT wiped wholesale: MiOS renders its own pages there
# and the manual reader is meant to answer on the installed system.
# Upstream pages go; the MiOS ones stay. Only the numbered section
# directories are swept, so man-db's own data files are left alone --
# deleting those would break the reader while leaving the pages in place.
rm -rf /usr/share/doc/* /usr/share/info/* 2>/dev/null || true
find /usr/share/man/man[0-9]* -type f ! -name 'mios*' -delete 2>/dev/null || true
find /usr/share/man/man[0-9]* -type d -empty -delete 2>/dev/null || true
if command -v python3 >/dev/null 2>&1 && [[ -f "${_build_root}/tools/render-manpages.py" ]]; then
    _row " MANPAGES: Validating man page formatting and readability with man(1)"
    python3 "${_build_root}/tools/render-manpages.py" --validate || {
        echo "[FATAL] man page validation failed during image bake" >&2
        exit 1
    }
fi
rm -rf /usr/share/gnome/help/* /usr/share/help/* 2>/dev/null || true
rm -f /var/log/dnf5.log* /var/log/hawkey.log 2>/dev/null || true
rm -rf /run/ceph /run/cockpit /run/k3s /tmp/mios-step-*.log 2>/dev/null || true
rm -f /var/lib/systemd/random-seed /tmp/mios-build.log "$MIOS_VERSION_MANIFEST" 2>/dev/null || true
    return 0
}

_finding() {
    "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event note --status "$1" --name "$2"
}

_run_stage() {
    local name=$1
    shift
    "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event start --name "$name"
    SCRIPT_COUNT=$((SCRIPT_COUNT + 1))
    local step_log="${PROGRESS_DIR}/stage-${SCRIPT_COUNT}.log" rc tee_rc status
    local -a pipeline_status=()
    set +e
    _stage_child "$@" 2>&1 | tee "$step_log"
    pipeline_status=("${PIPESTATUS[@]}")
    set -e
    rc=${pipeline_status[0]}
    tee_rc=${pipeline_status[1]}
    status=pass
    if [[ "$tee_rc" -ne 0 ]]; then
        status=fail
        FAIL_LOG+=("${name}: log capture failed exit=${tee_rc}")
    elif [[ "$rc" -eq 125 ]]; then
        status=missing
    elif [[ "$rc" -ne 0 ]]; then
        if [[ "${PHASE_FATAL[$name]:-true}" == false ]]; then
            status=warn
            WARN_LOG+=("${name}: exit=${rc}")
            WARNED_JSON+=("{\"script\":\"${name}\",\"exit_code\":${rc}}")
        else
            status=fail
            FAIL_LOG+=("${name}: exit=${rc}")
        fi
    fi
    "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event result --name "$name" --status "$status"
}

_stage_child() {
    # Functions execute in the pipeline's child shell. Enable errexit there so
    # a failing post-build gate cannot be overwritten by its final return.
    set -e
    "$@"
}

_run_script() {
    [[ -f "$1" ]] || { _finding missing "Selected phase is missing: $(basename "$1")"; return 125; }
    bash "$1"
}

[[ -n "$_miosd" ]] || { printf '[MISSING] Native build progress engine miosd is required\n' >&2; exit 1; }
PROGRESS_DIR="$(mktemp -d /tmp/mios-build-progress.XXXXXX)"
PROGRESS_STATE="${PROGRESS_DIR}/state.json"
POST_NAMES=()
POST_FUNCTIONS=()
declare -A POST_SEEN=()
_post_plan="$(MIOS_ROOT="$_mios_root" "$_miosd" build --post-list)"
while IFS=: read -r _name _action _extra; do
    [[ -n "$_name" && -z "$_extra" && -z "${POST_SEEN[$_name]+present}" ]] || { printf '[FAIL] Invalid or duplicate post-build identity: %s\n' "$_name" >&2; exit 1; }
    case "$_action" in
        bloat|package_health|invariants|ssot|drift|agent_tests|libexec_tests|image_digests|log_chain|finalize) ;;
        *) printf '[FAIL] Unknown post-build action: %s\n' "$_action" >&2; exit 1 ;;
    esac
    POST_NAMES+=("$_name")
    POST_FUNCTIONS+=("_post_${_action}")
    POST_SEEN[$_name]=true
    PHASE_FATAL[$_name]=true
done <<< "$_post_plan"
{
    for script in "${ALL_SCRIPTS[@]}"; do basename "$script"; done
    printf '%s\n' "${POST_NAMES[@]}"
} | "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event init
_progress_owner=$BASHPID
_progress_exit() {
    local result=$?
    if [[ "$BASHPID" == "$_progress_owner" && "$result" -ne 0 ]]; then
        "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event finish || true
    fi
}
trap _progress_exit EXIT
printf '[BUILD] MiOS %s | base %s | started %s | log %s | receipt %s\n' "$VERSION_STR" "$_base_image" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$BUILD_LOG" "$PROGRESS_STATE"
for script in "${ALL_SCRIPTS[@]}"; do _run_stage "$(basename "$script")" _run_script "$script"; done
for i in "${!POST_NAMES[@]}"; do _run_stage "${POST_NAMES[$i]}" "${POST_FUNCTIONS[$i]}"; done

_fail_report "${FAIL_LOG[@]+"${FAIL_LOG[@]}"}"
_warn_report "${WARN_LOG[@]+"${WARN_LOG[@]}"}"
"$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event finish
