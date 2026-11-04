#!/bin/bash
# AI-hint: Mirrors only the OCI container images from the SSOT machine OS index, preserving child digests and excluding disk-image artifacts.
# AI-related: /usr/share/mios/templates/bash, usr/share/mios/mios.toml, .github/workflows/machine-os-mirror.yml
# AI-functions: main

set -euo pipefail

main() (
    local root="${1:-.}"
    root="$(cd "$root" && pwd -P)"
    local get="${MIOS_TOML_GET:-$root/usr/libexec/mios/mios-toml-get}"
    [[ -x "$get" ]] || { echo "[mirror-machine-os] native mios-toml-get is required" >&2; exit 1; }
    # A publisher reads the selected checkout, including when an installed
    # native getter or an unrelated host/user overlay is present.
    export MIOS_TOML_ROOT="$root"
    export MIOS_VENDOR_TOML="$root/usr/share/mios/mios.toml"
    [[ -f "$MIOS_VENDOR_TOML" ]] || { echo "[mirror-machine-os] missing vendor SSOT: $MIOS_VENDOR_TOML" >&2; exit 1; }

    local repo tag mirror
    repo="$("$get" --vendor image machine_os_repo)"
    tag="$("$get" --vendor image machine_os_tag)"
    mirror="${2:-$("$get" --vendor image machine_os_mirror)}"
    local src="$repo:$tag" dst="$mirror:$tag"
    if [[ -z "$repo" || -z "$tag" || -z "$mirror" || "$src$dst" == *[[:space:]]* ]]; then
        echo "[mirror-machine-os] image repository, mirror and tag must be nonempty references without whitespace" >&2
        exit 1
    fi
    if [[ "${GITHUB_ACTIONS:-}" == true ]]; then
        local owner="${GITHUB_REPOSITORY_OWNER:?missing GitHub repository owner}"
        owner="${owner,,}"
        if [[ "$mirror" != "ghcr.io/$owner/"* || "$mirror" == "ghcr.io/$owner/" ]]; then
            echo "[mirror-machine-os] configured mirror $mirror is outside repository owner $owner; update the SSOT after a repository transfer" >&2
            exit 1
        fi
    fi
    if [[ $# -lt 2 ]]; then
        local ci_image
        ci_image="$("$get" --vendor ci.fedora image)"
        if [[ "$ci_image" != "$dst" ]]; then
            echo "[mirror-machine-os] ci.fedora.image must equal machine_os_mirror:machine_os_tag ($dst)" >&2
            exit 1
        fi
    fi

    # Capture inspection failures before mapfile: failures in process
    # substitutions otherwise do not propagate to the publishing shell.
    local raw selected
    raw="$(skopeo inspect --raw "docker://$src")"
    if ! selected="$(jq -er '.manifests | arrays | .[] | select(.annotations.disktype == null) |
        if (.digest | type) == "string" and (.digest | length) > 0
        then .digest else error("container manifest has no digest") end' <<< "$raw")"; then
        echo "[mirror-machine-os] $src lists no usable container image digests" >&2
        exit 1
    fi
    local -a digests
    mapfile -t digests <<< "$selected"

    # Own a distinct local manifest; never remove an existing participant's
    # manifest to make space for this invocation.
    local list="localhost/mios-machine-os-mirror:${BASHPID}" status
    if podman manifest exists "$list"; then
        echo "[mirror-machine-os] refusing existing local manifest $list" >&2
        exit 1
    else
        status=$?
        [[ "$status" -eq 1 ]] || exit "$status"
    fi
    podman manifest create "$list" >/dev/null
    trap 'status=$?; podman manifest rm "$list" >/dev/null || { (( status != 0 )) || status=1; }; exit "$status"' EXIT
    local digest
    for digest in "${digests[@]}"; do
        podman manifest add "$list" "docker://$repo@$digest" >/dev/null
    done
    # GITHUB_TOKEN publication links the new package to this repository.
    # Do not relabel child images: changing their configs changes their digests.
    podman manifest push --all "$list" "docker://$dst"

    local want got mirrored
    want="$(printf '%s\n' "${digests[@]}" | sort)"
    mirrored="$(skopeo inspect --raw "docker://$dst")"
    got="$(jq -er '.manifests | arrays | .[] |
        if (.digest | type) == "string" and (.digest | length) > 0
        then .digest else error("mirror manifest has no digest") end' <<< "$mirrored" | sort)"
    if [[ "$got" != "$want" ]]; then
        printf '[mirror-machine-os] %s differs from %s\nwant:\n%s\ngot:\n%s\n' "$dst" "$src" "$want" "$got" >&2
        exit 1
    fi
    # A dependent container job may start only after publication and this
    # exact child-digest verification have both succeeded.
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        printf 'image=%s\n' "$dst" >> "$GITHUB_OUTPUT"
    fi
    echo "[mirror-machine-os] $dst: ${#digests[@]} container image(s), digest-identical to $src"
)

main "$@"
