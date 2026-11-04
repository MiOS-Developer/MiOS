#!/usr/bin/env bash
# AI-hint: Writes .artifacts/image-facts.json -- every release tag of the [image].machine_os_repo podman machine OS and its CoreOS stream -- for `mios-gate image-freshness`.
# AI-related: src/mios-rs/mios-gate/src/image_freshness.rs, usr/share/mios/mios.toml
set -euo pipefail

root="${1:-.}"
repo="$(MIOS_TOML_ROOT="$root" "${MIOS_TOML_GET:-$root/usr/libexec/mios/mios-toml-get}" image machine_os_repo)"
out="$root/.artifacts/image-facts.json"
install -d "$root/.artifacts"

tags="$(skopeo list-tags "docker://$repo" \
    | python3 -c 'import json,sys; print(" ".join(t for t in json.load(sys.stdin)["Tags"] if t.replace(".", "").isdigit()))')"
{
    printf '{"%s":{' "$repo"
    sep=""
    for tag in $tags; do
        stream="$(skopeo inspect --override-os linux --override-arch amd64 --config "docker://$repo:$tag" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["config"]["Labels"].get("com.coreos.stream", ""))')"
        printf '%s"%s":"%s"' "$sep" "$tag" "$stream"
        sep=","
    done
    printf '}}\n'
} > "$out.tmp"
mv "$out.tmp" "$out"
echo "[fetch-image-facts] $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(len(next(iter(d.values()))))' "$out") tag(s) of $repo -> $out"
