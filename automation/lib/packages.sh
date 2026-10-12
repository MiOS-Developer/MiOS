#!/bin/bash
# AI-hint: Provides shell functions to parse and extract package lists from mios.toml configuration files, supporting layered overrides and specific installation mo...
# AI-doc: usr/share/doc/mios/manual/lib.md

_resolve_mios_toml() {
    local cand
    if [[ -n "${MIOS_TOML:-}" && -f "$MIOS_TOML" ]]; then
        echo "$MIOS_TOML"
        return 0
    fi
    for cand in \
        "${HOME:-/root}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "/ctx/mios-bootstrap/mios.toml" \
        "/usr/share/mios/mios.toml" \
        "/ctx/usr/share/mios/mios.toml"; do
        [[ -f "$cand" ]] || continue
        echo "$cand"
        return 0
    done
    return 1
}

_get_package_list_from_toml() {
    local category="$1"
    local file="$2"
    local field="${3:-pkgs}"
    [[ -f "$file" ]] || return 1

    local auth
    auth=$(awk '/^[[:space:]]*build_catalog_authoritative[[:space:]]*=/ {
        if ($0 ~ /=[[:space:]]*true/) print "true"
    }' "$file" 2>/dev/null)

    if [[ "$auth" == "true" && "$field" == "pkgs" ]]; then
        local mat_json
        mat_json="$(dirname "$file")/package_sets.json"
        if [[ ! -f "$mat_json" ]]; then
            echo "[packages.sh] ERROR: authoritative package catalog is missing: $mat_json" >&2
            return 2
        fi
        if [[ -f "$mat_json" ]]; then
            local pkgs
            # Read the catalog over stdin: native Windows python3 cannot open a
            # POSIX /tmp path, and the old silent fallback let an authoritative
            # catalog be bypassed by the TOML layer without any signal.
            if ! pkgs=$(python3 -c '
import json, sys
name = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(3)
for entry in data:
    if entry.get("name") == name:
        print(" ".join(entry.get("pkgs", [])))
        sys.exit(0)
sys.exit(4)
' "$category" < "$mat_json" 2>/dev/null); then
                echo "[packages.sh] ERROR: authoritative package_sets.json is unreadable or lacks [packages.$category]" >&2
                return 2
            fi
            echo "$pkgs"
            return 0
        fi
    fi

    awk -v section="packages.${category}" -v field="$field" '
        /^\[/ {
            in_section = 0
            collecting = 0
            line = $0
            sub(/^\[/, "", line); sub(/\][[:space:]]*$/, "", line)
            gsub(/[[:space:]]/, "", line)
            if (line == section) in_section = 1
            next
        }
        in_section && $0 ~ "^[[:space:]]*" field "[[:space:]]*=" {
            sub(/^[^=]*=[[:space:]]*/, "", $0)
            collecting = 1
        }
        collecting {
            line = $0
            sub(/#.*$/, "", line)
            print line
            if (line ~ /\]/) { collecting = 0 }
        }
    ' "$file" \
        | tr -d '[]' \
        | tr ',' '\n' \
        | sed -E 's/[[:space:]]*"([^"]*)"[[:space:]]*$/\1/' \
        | sed '/^[[:space:]]*$/d' \
        | sed -E 's/[[:space:]]*#.*$//' \
        | tr '\n' ' '
}

_get_pkgs_from_single_toml() {
    _get_package_list_from_toml "$1" "$2" pkgs
}

# Resolve a declared section dependency through the same overlay order as pkgs.
# A missing field inherits; an explicit [] clears that section's dependencies.
get_package_list_setting() {
    local category="$1" field="$2" cand
    for cand in \
        "${MIOS_TOML:-}" \
        "${HOME:-/root}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "/ctx/mios-bootstrap/mios.toml" \
        "/usr/share/mios/mios.toml" \
        "/ctx/usr/share/mios/mios.toml"; do
        [[ -n "$cand" && -f "$cand" ]] || continue
        if awk -v sect="[packages.$category]" -v field="$field" '
            $0 == sect { active = 1; next }
            /^\[/ { active = 0 }
            active && $0 ~ "^[[:space:]]*" field "[[:space:]]*=" { found = 1 }
            END { exit !found }
        ' "$cand"; then
            _get_package_list_from_toml "$category" "$cand" "$field"
            return
        fi
    done
    return 0
}

_get_package_closure() {
    local category="$1" trail="${2:- }" pkgs deps dep
    if [[ "$trail" == *" $category "* ]]; then
        echo "[packages.sh] ERROR: cyclic section dependency: ${trail}$category" >&2
        return 1
    fi
    pkgs="$(_get_raw_packages "$category")" || {
        if (( $? == 2 )); then
            return 2
        fi
        echo "[packages.sh] ERROR: [packages.$category].pkgs is empty or undefined" >&2
        return 1
    }
    deps="$(get_package_list_setting "$category" requires_sections)" || return 1
    for dep in $deps; do
        _is_section_enabled "$dep" || {
            echo "[packages.sh] ERROR: [packages.$category] requires disabled [packages.$dep]" >&2
            return 1
        }
        _get_package_closure "$dep" "${trail}${category} " || return 1
    done
    printf '%s\n' "$pkgs"
}

_get_raw_packages() {
    local category="$1"

    local cand
    for cand in \
        "${MIOS_TOML:-}" \
        "${HOME:-/root}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "/ctx/mios-bootstrap/mios.toml" \
        "/usr/share/mios/mios.toml" \
        "/ctx/usr/share/mios/mios.toml"; do
        [[ -n "$cand" && -f "$cand" ]] || continue
        if grep -q "^\[packages\.${category}\]" "$cand" 2>/dev/null; then
            local pkgs
            if pkgs=$(_get_pkgs_from_single_toml "$category" "$cand"); then
                :
            else
                local inner_rc=$?
                (( inner_rc != 2 )) || return 2
            fi
            if [[ -n "${pkgs// }" ]]; then
                echo "$pkgs"
                return 0
            fi
        fi
    done
    return 1
}

get_packages_from_toml() {
    local category="$1" file="${2:-}" toml_pkgs
    [[ -z "$file" || -f "$file" ]] || return 1
    # The explicit file is the highest priority layer for the whole closure,
    # including required children, rather than only the root's raw pkgs.
    local MIOS_TOML="${file:-${MIOS_TOML:-}}"
    toml_pkgs="$(_get_package_closure "$category")" || return 1
    printf '%s\n' "$toml_pkgs" | awk '{ for (i = 1; i <= NF; i++) if (!seen[$i]++) printf "%s ", $i } END { print "" }'
}

get_packages() {
    local category="$1"
    local toml_pkgs
    # Preserve the optional reader's empty result for an absent root section.
    # A declared root with a missing/disabled/cyclic dependency still fails.
    if _get_raw_packages "$category" >/dev/null; then
        :
    else
        local inner_rc=$?
        (( inner_rc != 1 )) || return 0
        return "$inner_rc"
    fi
    # Render the entire closure before printing so a broken dependency never
    # hands dnf a partial request. Preserve first occurrence order, deduplicated.
    toml_pkgs=$(get_packages_from_toml "$category") || return 1
    if [[ -n "${toml_pkgs// }" ]]; then
        echo "$toml_pkgs"
        return 0
    fi
    return 0
}

get_packages_strict() {
    local category="$1"
    local result
    result=$(get_packages "$category") || return 1
    if [[ -z "${result// }" ]]; then
        echo "[packages.sh] ERROR: [packages.${category}] is empty or undefined in mios.toml" >&2
        return 1
    fi
    echo "$result"
}

_PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_PKG_DIR}/common.sh"

_is_section_enabled() {
    local section="$1"
    local cand result
    for cand in \
        "${MIOS_TOML:-}" \
        "${HOME:-/root}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "/ctx/mios-bootstrap/mios.toml" \
        "/usr/share/mios/mios.toml" \
        "/ctx/usr/share/mios/mios.toml"; do
        [[ -n "$cand" && -f "$cand" ]] || continue
        if grep -q "^\[packages\.${section}\]" "$cand" 2>/dev/null; then
            result=$(awk -v sect="[packages.$section]" '
                $0 == sect { in_section = 1; next }
                /^\[/ && in_section { in_section = 0 }
                in_section && /^[[:space:]]*enable[[:space:]]*=/ {
                    if ($0 ~ /=[[:space:]]*false[[:space:]]*($|#)/) print "false"
                    else print "true"
                    exit
                }
            ' "$cand" 2>/dev/null)
            [[ "$result" == "false" ]] && return 1
            return 0
        fi
    done
    return 0
}

# Scalar package policy, using the same precedence as package arrays. Missing
# values return no policy so destructive callers can refuse rather than guess.
get_package_setting() {
    local category="$1" key="$2" cand result
    local -a files
    if [[ $# -ge 3 ]]; then
        files=("$3")
    else
        files=( \
        "${MIOS_TOML:-}" \
        "${HOME:-/root}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "/ctx/mios-bootstrap/mios.toml" \
        "/usr/share/mios/mios.toml" \
        "/ctx/usr/share/mios/mios.toml" )
    fi
    for cand in "${files[@]}"; do
        [[ -n "$cand" && -f "$cand" ]] || continue
        result=$(awk -v sect="[packages.$category]" -v key="$key" '
            $0 == sect { active = 1; next }
            /^\[/ { active = 0 }
            active && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
                sub(/^[^=]*=[[:space:]]*/, ""); sub(/[[:space:]]*#.*/, "")
                sub(/[[:space:]]*$/, ""); print; exit
            }
        ' "$cand")
        if [[ -n "$result" ]]; then printf '%s\n' "$result"; return 0; fi
    done
    return 1
}

# ADR-0025: a section outside the build profile is skipped, not failed. build.sh exports
# BUILD_PROFILE_SECTIONS; unset or "*" selects every section.
_in_build_profile() {
    local sel="${BUILD_PROFILE_SECTIONS:-}"
    [[ -z "${sel// }" || "${sel// }" == "*" ]] && return 0
    [[ " ${sel} " == *" $1 "* ]]
}

_dnf_retry_exec() {
    local max_attempts=3
    local delay=2
    local attempt=1
    local ret=0
    while [[ $attempt -le $max_attempts ]]; do
        if "$@"; then
            return 0
        else
            ret=$?
        fi
        if [[ $attempt -lt $max_attempts ]]; then
            echo "[packages.sh] WARN: DNF execution failed (rc=$ret); retrying in ${delay}s (attempt $attempt/$max_attempts)..." >&2
            sleep "$delay"
            delay=$((delay * 2))
        fi
        attempt=$((attempt + 1))
    done
    return $ret
}

install_packages() {
    local category="$1"
    if ! _in_build_profile "$category"; then
        echo "[packages.sh] '$category' is outside the build profile; skipped"
        return 0
    fi
    if ! _is_section_enabled "$category"; then
        echo "[packages.sh] [packages.${category}].enable=false"
        return 0
    fi
    local packages
    packages=$(get_packages "$category") || return 1
    if [[ -n "${packages// }" ]]; then
        echo "[packages.sh] Installing '$category' packages"
        _dnf_retry_exec "$DNF_BIN" "${DNF_SETOPT[@]}" install -y "${DNF_OPTS[@]}" --setopt=strict=0 --skip-unavailable --exclude=PackageKit $packages || {
            echo "[packages.sh] WARNING: Some '$category' packages failed to install after retries" >&2
            echo "[packages.sh] Packages requested: $packages" >&2
        }
    else
        echo "[packages.sh] WARN: [packages.${category}] is empty or undefined in mios.toml"
    fi
}

install_packages_strict() {
    local category="$1"
    if ! _in_build_profile "$category"; then
        echo "[packages.sh] '$category' is outside the build profile; skipped"
        return 0
    fi
    if ! _is_section_enabled "$category"; then
        echo "[packages.sh] [packages.${category}].enable=false"
        return 0
    fi
    local packages
    packages=$(get_packages_strict "$category") || return 1
    echo "[packages.sh] Installing '$category' packages"
    _dnf_retry_exec "$DNF_BIN" "${DNF_SETOPT[@]}" install -y --allowerasing --exclude=PackageKit $packages || {
        echo "[packages.sh] FATAL: Mandatory '$category' packages failed to install after retries" >&2
        echo "[packages.sh] Packages requested: $packages" >&2
        return 1
    }
}

install_packages_optional() {
    local category="$1"
    if ! _in_build_profile "$category"; then
        echo "[packages.sh] '$category' is outside the build profile; skipped"
        return 0
    fi
    if ! _is_section_enabled "$category"; then
        echo "[packages.sh] INFO: [packages.${category}].enable=false"
        return 0
    fi
    local packages
    packages=$(get_packages "$category") || return 1
    if [[ -z "${packages// }" ]]; then
        echo "[packages.sh] INFO: [packages.${category}] is empty or undefined"
        return 0
    fi
    echo "[packages.sh] Installing optional '$category' packages"
    _dnf_retry_exec "$DNF_BIN" "${DNF_SETOPT[@]}" install -y "${DNF_OPTS[@]}" --skip-unavailable --exclude=PackageKit $packages || {
        echo "[packages.sh] WARNING: Some optional '$category' packages failed after retries" >&2
    }
}
