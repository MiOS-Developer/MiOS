#!/usr/bin/env bash
# AI-hint: Build-time "latest" resolution for upstream inputs: picks the newest upstream release (mios-bake-plan latest-git / git ls-remote), downloads only what an upstream-published checksum or an SSOT [build.pins] digest proves (GitHub release asset digest, a release checksums file, the advertised git object id), reads the GitHub API through scurl (the mios_github_token build secret when one is mounted), falls back to the [build.pins] release by name (MIOS-PIN-FALLBACK) when upstream answers empty, aborts the build on any mismatch, and records the resolved version + sha256 as build output (version manifest + SBOM binaries.tsv).
# AI-related: usr/share/mios/mios.toml, automation/25-gpu-cdi-toolkits.sh, automation/49-cosign-policy.sh, automation/56-fonts.sh, automation/57-gnome.sh, automation/59-tools.sh, automation/64-bake-fan-control.sh, tools/native/mios-bake-plan/src/latest.rs, tests/test-upstream-latest.sh
# AI-doc: usr/share/doc/mios/manual/lib.md
#
# Contract: no function hands back an unverified artefact. An integrity
# MISMATCH goes through mios_ul_integrity_fail, which exits (status 3). A
# checksum the forge cannot supply returns 2 and downloads nothing, so the
# caller may take another verified path or skip.
#
# A pinned asset ([build.pins.<component>]) never needs the GitHub REST API:
# when the release to bake is the pinned one, the pinned sha256 proves it. The
# API is only asked for a release the SSOT does not pin (authenticated by scurl
# when the build mounts a token), and an empty answer falls back to the pin by name.

_mios_ul_dir="$(dirname "${BASH_SOURCE[0]}")"
if ! declare -f scurl >/dev/null 2>&1; then
    # shellcheck source=automation/lib/masking.sh
    source "${_mios_ul_dir}/masking.sh"
fi
declare -f mios_log  >/dev/null 2>&1 || mios_log()  { printf '[upstream-latest] %s\n' "$*" >&2; }
declare -f mios_warn >/dev/null 2>&1 || mios_warn() { printf '[upstream-latest] WARN: %s\n' "$*" >&2; }
declare -f mios_err  >/dev/null 2>&1 || mios_err()  { printf '[upstream-latest] ERR %s\n' "$*" >&2; }
# Functions here print results on stdout for $(...) capture; notes go to stderr.
_mios_ul_note() { mios_log "$@" >&2; }

: "${MIOS_GITHUB_API:=https://api.github.com}"
: "${MIOS_SBOM_DIR:=/usr/share/mios/artifacts/sbom}"
: "${MIOS_VERSION_MANIFEST:=/tmp/mios-build-versions.tsv}"
_mios_ul_src="$(cd "${_mios_ul_dir}/../.." && pwd)"

# An integrity mismatch is never recoverable: stop the build step.
mios_ul_integrity_fail() {
    mios_err "INTEGRITY: $*"
    exit 3
}

# A command substitution runs in a subshell: re-raise its integrity exit.
# Usage: x="$(fn ...)" || mios_ul_pass $? || <unavailable handling>
mios_ul_pass() {
    [[ "$1" -eq 3 ]] && exit 3
    return "$1"
}

_mios_ul_bake_plan() {
    local c
    for c in "${MIOS_BAKE_PLAN:-}" /usr/libexec/mios/mios-bake-plan "$(command -v mios-bake-plan 2>/dev/null || true)"; do
        [[ -n "$c" && -x "$c" ]] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

# mios_latest_git_ref URL -> newest upstream release tag (the forge's
# releases/latest, else the [build.float] git shapes over git ls-remote).
mios_latest_git_ref() {
    local url="$1" plan ref
    plan="$(_mios_ul_bake_plan)" || { mios_err "mios-bake-plan is required to resolve latest for ${url}"; return 1; }
    ref="$("$plan" latest-git "$url")" || return 1
    [[ -n "$ref" ]] || return 1
    printf '%s\n' "$ref"
}

# mios_latest_git_tag_matching URL ERE -> highest tag (sort -V) whose name
# matches ERE, from git ls-remote (git smart HTTP: no REST rate limit).
mios_latest_git_tag_matching() {
    local url="$1" ere="$2" tag
    tag="$(git ls-remote --tags --refs "$url" | awk -F'refs/tags/' 'NF==2{print $2}' \
            | grep -E -- "$ere" | sort -V | tail -n 1)" || true
    [[ -n "$tag" ]] || return 1
    printf '%s\n' "$tag"
}

# mios_git_ref_commit URL REF -> commit id upstream advertises for REF
# (annotated tags peeled).
mios_git_ref_commit() {
    local url="$1" ref="$2" out sha
    out="$(git ls-remote "$url" "refs/tags/${ref}^{}" "refs/tags/${ref}" "refs/heads/${ref}")" || return 1
    sha="$(awk -v p="refs/tags/${ref}^{}" '$2==p{print $1; exit}' <<<"$out")"
    [[ -n "$sha" ]] || sha="$(awk 'NR==1{print $1}' <<<"$out")"
    [[ "$sha" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || return 1
    printf '%s\n' "$sha"
}

# mios_git_clone_verified URL REF DEST -> shallow clone of REF. The checked-out
# commit must equal the id upstream advertises, and git fsck must pass (every
# object is hashed against its id, so the id is the integrity proof).
mios_git_clone_verified() {
    local url="$1" ref="$2" dest="$3" want got attempt
    want="$(mios_git_ref_commit "$url" "$ref")" || { mios_err "${url}: ref ${ref} not advertised"; return 1; }
    for attempt in 1 2 3; do
        rm -rf -- "$dest"
        git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$ref" "$url" "$dest" && break
        mios_warn "clone of ${url}@${ref} failed (attempt ${attempt})"
        [[ $attempt -lt 3 ]] && sleep $((attempt * ${MIOS_UL_RETRY_DELAY:-8}))
    done
    [[ -d "$dest/.git" ]] || { mios_err "${url}@${ref}: source unavailable"; return 1; }
    got="$(git -C "$dest" rev-parse HEAD)"
    [[ "$got" == "$want" ]] || mios_ul_integrity_fail "${url}@${ref}: cloned ${got} but upstream advertises ${want}"
    git -C "$dest" fsck --no-dangling >/dev/null 2>&1 \
        || mios_ul_integrity_fail "${url}@${ref}: object store failed git fsck"
    printf '%s\n' "$got"
}

# mios_gh_repo URL -> owner/repo of a github.com clone or web URL.
mios_gh_repo() {
    local rest="${1#*://github.com/}"
    [[ "$rest" != "$1" ]] || return 1
    rest="${rest%/}"; rest="${rest%.git}"
    [[ "$rest" =~ ^[^/]+/[^/]+$ ]] || return 1
    printf '%s\n' "$rest"
}

# mios_gh_release_json OWNER/REPO TAG|latest -> release JSON on stdout. When
# the API does not answer 200 it says why on stderr -- curl's own error, or the
# HTTP status with GitHub's message and the rate-limit headers -- and returns 1,
# so an unreachable or rate-limited API is never reported as a missing digest.
mios_gh_release_json() {
    local repo="$1" tag="$2" path url tmp code rc=0 msg remaining reset auth
    if [[ "$tag" == latest ]]; then path="releases/latest"; else path="releases/tags/${tag}"; fi
    url="${MIOS_GITHUB_API}/repos/${repo}/${path}"
    tmp="$(mktemp -d)"
    code="$(scurl -sSL -H 'Accept: application/vnd.github+json' -D "$tmp/headers" -o "$tmp/body" \
                  -w '%{http_code}' "$url" 2>"$tmp/stderr")" || rc=$?
    if [[ $rc -ne 0 ]]; then
        mios_err "GitHub API ${url}: request failed (curl exit ${rc}: $(tr '\n' ' ' < "$tmp/stderr" | sed 's/ *$//'))"
        rm -rf -- "$tmp"; return 1
    fi
    if [[ "$code" != 200 ]]; then
        msg="$(jq -r '.message // empty' < "$tmp/body" 2>/dev/null | head -n 1)" || msg=""
        remaining="$(awk -F': *' 'tolower($1)=="x-ratelimit-remaining"{v=$2} END{gsub(/\r/,"",v); print v}' "$tmp/headers")"
        reset="$(awk -F': *' 'tolower($1)=="x-ratelimit-reset"{v=$2} END{gsub(/\r/,"",v); print v}' "$tmp/headers")"
        if declare -f _scurl_github_token >/dev/null && [[ -n "$(_scurl_github_token)" ]]; then auth=authenticated; else auth=unauthenticated; fi
        mios_err "GitHub API ${url}: HTTP ${code:-none}${msg:+ (${msg})}, ${auth}${remaining:+, x-ratelimit-remaining ${remaining}}${reset:+, x-ratelimit-reset ${reset}}"
        rm -rf -- "$tmp"; return 1
    fi
    cat -- "$tmp/body"
    rm -rf -- "$tmp"
}

# mios_gh_asset_sha256 JSON NAME -> the sha256 GitHub publishes for that asset.
mios_gh_asset_sha256() {
    local json="$1" name="$2" d
    d="$(jq -er --arg n "$name" \
          '[.assets[]? | select(.name == $n) | .digest // empty | select(startswith("sha256:"))][0] | ltrimstr("sha256:")' \
          <<<"$json" 2>/dev/null)" || return 1
    [[ "$d" =~ ^[0-9a-fA-F]{64}$ ]] || return 1
    printf '%s\n' "${d,,}"
}

# mios_sha256_from_sums FILE NAME -> digest for NAME from a "<sha>  <name>" list.
mios_sha256_from_sums() {
    local d
    d="$(awk -v n="$2" '{f=$2; sub(/^\*/,"",f)} f==n{print $1; exit}' "$1")"
    [[ "$d" =~ ^[0-9a-fA-F]{64}$ ]] || return 1
    printf '%s\n' "${d,,}"
}

# mios_fetch_verified URL DEST SHA256 LABEL -> download and prove. A mismatch
# deletes the file and aborts; no checksum returns 2 without downloading.
mios_fetch_verified() {
    local url="$1" dest="$2" want="${3,,}" label="$4" got
    [[ "$want" =~ ^[0-9a-f]{64}$ ]] || { mios_err "${label}: no upstream checksum, refusing ${url}"; return 2; }
    scurl -fsSL "$url" -o "$dest" || { mios_err "${label}: download failed: ${url}"; return 1; }
    got="$(sha256sum "$dest" | awk '{print $1}')"
    if [[ "$got" != "$want" ]]; then
        rm -f -- "$dest"
        mios_ul_integrity_fail "${label}: sha256 ${got} != upstream ${want} (${url})"
    fi
    _mios_ul_note "${label}: sha256 verified (${got:0:16}...)"
}

# mios_record_resolved COMPONENT VERSION SOURCE SHA256 -> build output only:
# a version-manifest row and an SBOM binaries.tsv row.
mios_record_resolved() {
    local component="$1" version="$2" source="$3" sha="${4:-}"
    if declare -f record_version >/dev/null 2>&1; then
        record_version "$component" "$version" "$source"
    else
        [[ -f "$MIOS_VERSION_MANIFEST" ]] \
            || printf 'component\tversion\tresolved_to\trecorded_at\n' > "$MIOS_VERSION_MANIFEST"
        printf '%s\t%s\t%s\t%s\n' "$component" "$version" "$source" "$(date '+%Y-%m-%d %H:%M:%S')" \
            >> "$MIOS_VERSION_MANIFEST"
    fi
    mkdir -p "$MIOS_SBOM_DIR"
    printf '%s\t%s\t%s\n' "$component" "$version" "${sha:-unknown}" >> "${MIOS_SBOM_DIR}/binaries.tsv"
    _mios_ul_note "resolved ${component} = ${version} (${source})"
}

# mios_fetch_gh_release_asset REPO_URL TAG ASSET DEST LABEL -> one release asset
# proven by GitHub's published digest; prints the sha256. Returns 2 (nothing
# downloaded) when the release lists no digest for the asset, 1 (nothing
# downloaded) when the release metadata could not be fetched at all.
mios_fetch_gh_release_asset() {
    local url="$1" tag="$2" asset="$3" dest="$4" label="$5" repo json sha
    repo="$(mios_gh_repo "$url")" || { mios_err "${label}: not a github.com repo: ${url}"; return 1; }
    json="$(mios_gh_release_json "$repo" "$tag")" || {
        mios_err "${label}: release ${tag} metadata unavailable from the GitHub API; not downloading ${asset}"
        return 1
    }
    sha="$(mios_gh_asset_sha256 "$json" "$asset")" || {
        mios_warn "${label}: GitHub publishes no digest for ${asset}@${tag}; not downloading it"
        return 2
    }
    mios_fetch_verified "https://github.com/${repo}/releases/download/${tag}/${asset}" "$dest" "$sha" "$label" || return
    printf '%s\n' "$sha"
}

# mios_gh_release_asset_name REPO_URL TAG ERE -> first asset name matching ERE.
mios_gh_release_asset_name() {
    local repo json
    repo="$(mios_gh_repo "$1")" || return 1
    json="$(mios_gh_release_json "$repo" "$2")" || return 2
    jq -er --arg re "$3" '[.assets[]?.name | select(test($re))][0] // empty' <<<"$json" 2>/dev/null || return 2
}

# --- SSOT pins ([build.pins.<component>]) ------------------------------------

# _mios_pin_get COMPONENT KEY [ARCH] -> one [build.pins] value, empty when unset.
_mios_pin_get() {
    local table="build.pins.$1"
    [[ -n "${3:-}" ]] && table+=".assets.$3"
    MIOS_TOML_ROOT="${MIOS_TOML_ROOT:-$_mios_ul_src}" \
        "${MIOS_TOML_GET:-$_mios_ul_src/usr/libexec/mios/mios-toml-get}" --vendor "$table" "$2" 2>/dev/null || true
}

# _mios_pin_repo COMPONENT -> owner/name from `repository`, or from the SSOT key
# `repository_from` names (a URL or owner/name), so a repository is declared once.
_mios_pin_repo() {
    local r from
    r="$(_mios_pin_get "$1" repository)"
    if [[ -z "$r" ]]; then
        from="$(_mios_pin_get "$1" repository_from)"
        [[ "$from" == *.* ]] || return 1
        r="$(MIOS_TOML_ROOT="${MIOS_TOML_ROOT:-$_mios_ul_src}" \
              "${MIOS_TOML_GET:-$_mios_ul_src/usr/libexec/mios/mios-toml-get}" --vendor "${from%.*}" "${from##*.}" 2>/dev/null)" || return 1
    fi
    [[ "$r" == *://* ]] && { r="$(mios_gh_repo "$r")" || return 1; }
    [[ "$r" =~ ^[^/]+/[^/]+$ ]] || return 1
    printf '%s\n' "$r"
}

# mios_pinned_asset COMPONENT WANT DIR [LABEL] -> COMPONENT's release asset for
# this arch, proven, in DIR. WANT is a release version or "latest". Sets
# mios_pin_version, mios_pin_sha, mios_pin_file, mios_pin_url (the release page)
# and mios_pin_source (pin | vendored | upstream). The pinned release is proven by its pinned sha256
# with no REST call; any other release needs GitHub's published digest. When
# upstream answers empty (no newest tag, no digest) a "latest" request bakes the
# pin and says so (MIOS-PIN-FALLBACK); with no pin to fall back on it fails by
# name (MIOS-PIN-MISSING). An integrity mismatch exits 3.
# shellcheck disable=SC2034  # mios_pin_* are this function's outputs, read by its callers
mios_pinned_asset() {
    local comp="$1" want="$2" dir="$3" label="${4:-$1}" arch repo prefix pver psha name tmpl vendored tag ver rc
    mios_pin_version="" mios_pin_sha="" mios_pin_file="" mios_pin_url="" mios_pin_source=""
    arch="${mios_pin_arch:-$(uname -m)}"
    repo="$(_mios_pin_repo "$comp")" \
        || { mios_err "MIOS-PIN-MISSING: [build.pins.${comp}] names no github.com repository"; return 1; }
    [[ -n "$(_mios_pin_get "$comp" name "$arch")" ]] || arch=noarch
    tmpl="$(_mios_pin_get "$comp" name "$arch")"
    [[ -n "$tmpl" ]] || { mios_err "MIOS-PIN-MISSING: [build.pins.${comp}.assets] has no $(uname -m) or noarch asset"; return 1; }
    prefix="$(_mios_pin_get "$comp" tag_prefix)"
    pver="$(_mios_pin_get "$comp" version)"
    psha="$(_mios_pin_get "$comp" sha256 "$arch")"
    vendored="$(_mios_pin_get "$comp" vendored "$arch")"
    [[ -n "$pver" && "$psha" =~ ^[0-9a-f]{64}$ ]] || { pver=""; psha=""; }

    ver="$want"
    if [[ "$want" == latest ]]; then
        if tag="$(mios_latest_git_ref "https://github.com/${repo}.git")"; then
            ver="${tag#"$prefix"}"
        elif [[ -n "$pver" ]]; then
            mios_warn "MIOS-PIN-FALLBACK: ${label}: newest release could not be resolved; baking pinned ${pver}"
            ver="$pver"
        else
            mios_err "MIOS-PIN-MISSING: ${label}: newest release could not be resolved and [build.pins.${comp}] pins no release"
            return 1
        fi
    fi
    tag="${prefix}${ver}"
    name="${tmpl//\{version\}/$ver}"
    mkdir -p "$dir"
    mios_pin_file="${dir}/${name}"
    mios_pin_url="https://github.com/${repo}/releases/tag/${tag}"

    if [[ -n "$pver" && "$ver" == "$pver" ]]; then
        if [[ -n "$vendored" && -f "$vendored" ]]; then
            mios_pin_sha="$(sha256sum "$vendored" | awk '{print $1}')"
            [[ "$mios_pin_sha" == "$psha" ]] \
                || mios_ul_integrity_fail "${label}: vendored ${vendored} sha256 ${mios_pin_sha} != [build.pins.${comp}] ${psha}"
            cp -f -- "$vendored" "$mios_pin_file"
            mios_pin_source=vendored
            _mios_ul_note "${label} ${ver}: vendored copy matches the pinned sha256"
        else
            mios_fetch_verified "https://github.com/${repo}/releases/download/${tag}/${name}" \
                "$mios_pin_file" "$psha" "${label} ${ver}" || return 1
            mios_pin_sha="$psha" mios_pin_source=pin
        fi
        mios_pin_version="$ver"
        return 0
    fi

    rc=0
    mios_pin_sha="$(mios_fetch_gh_release_asset "https://github.com/${repo}" "$tag" "$name" "$mios_pin_file" "${label} ${ver}")" \
        || { rc=$?; mios_ul_pass "$rc" || true; }
    if [[ $rc -eq 0 ]]; then
        mios_pin_version="$ver" mios_pin_source=upstream
        return 0
    fi
    if [[ "$want" == latest && -n "$pver" ]]; then
        mios_warn "MIOS-PIN-FALLBACK: ${label} ${ver}: GitHub published no reachable digest; baking pinned ${pver}"
        mios_pinned_asset "$comp" "$pver" "$dir" "$label"
        return
    fi
    mios_err "MIOS-PIN-MISSING: ${label} ${ver}: GitHub published no reachable digest and [build.pins.${comp}] does not pin ${ver} (refresh: just refresh-pins)"
    return 1
}

# --- consumers ---------------------------------------------------------------

# Cosign: newest release (inside MAJOR when the SSOT declares one), binary proven
# by that release's own cosign_checksums.txt. Prints "<tag> <sha256>".
mios_cosign_fetch_latest() {
    local release_url="$1" major="$2" dest="$3" arch="${4:-amd64}" repo_url tag tmp want
    repo_url="${release_url%/releases/download*}.git"
    if [[ -n "$major" ]]; then
        tag="$(mios_latest_git_tag_matching "$repo_url" "^v${major}\.[0-9]+\.[0-9]+$")" || tag=""
    else
        tag="$(mios_latest_git_ref "$repo_url")" || tag=""
    fi
    [[ -n "$tag" ]] || { mios_err "cosign: newest release could not be resolved from ${repo_url}"; return 1; }
    tmp="$(mktemp -d)"
    if ! scurl -fsSL "${release_url}/${tag}/cosign_checksums.txt" -o "$tmp/sums"; then
        rm -rf -- "$tmp"; mios_err "cosign ${tag}: cosign_checksums.txt unavailable"; return 1
    fi
    if ! want="$(mios_sha256_from_sums "$tmp/sums" "cosign-linux-${arch}")"; then
        rm -rf -- "$tmp"; mios_err "cosign ${tag}: no checksum for cosign-linux-${arch}"; return 1
    fi
    mios_fetch_verified "${release_url}/${tag}/cosign-linux-${arch}" "$tmp/cosign" "$want" "cosign ${tag}" \
        || { rm -rf -- "$tmp"; return 1; }
    install -D -m 0755 "$tmp/cosign" "$dest"
    rm -rf -- "$tmp"
    printf '%s %s\n' "$tag" "$want"
}

# Git bakes (ventulusd, ...): REF "latest" or empty floats to the newest
# release; the clone is proven against the advertised commit. Prints "<ref> <commit>".
mios_git_bake_checkout() {
    local url="$1" ref="${2:-latest}" dest="$3" commit
    if [[ "$ref" == latest ]]; then
        ref="$(mios_latest_git_ref "$url")" || { mios_err "${url}: newest release could not be resolved"; return 1; }
    fi
    commit="$(mios_git_clone_verified "$url" "$ref" "$dest")" || { mios_ul_pass $? || return 1; }
    printf '%s %s\n' "$ref" "$commit"
}
