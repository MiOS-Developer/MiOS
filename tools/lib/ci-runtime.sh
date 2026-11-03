#!/usr/bin/env bash
# AI-hint: Shared container-runtime, registry and image helpers for the workflows, so both publishers execute one implementation.
# AI-related: .github/workflows/mios-ci.yml, .forgejo/workflows/build-mios.yml, tools/lib/userenv.sh
#
# Every function here replaced a block that had been pasted into two or more
# workflow steps. The registry-name validation existed four times, the storage
# configuration twice, the label verification twice, and the version parse three
# times; the copies had already diverged. Source this instead.
#
# shellcheck shell=bash

mios_ci_prepare_storage() {
    mios_ci_require_host_cpus "${1:-.}" || return
    sudo mkdir -p /etc/containers /mnt/tmp
    printf '%s\n' \
        '[storage]' \
        'driver = "overlay"' \
        'graphroot = "/mnt/containers-storage"' \
        'runroot = "/run/containers/storage"' \
        '[storage.options.overlay]' \
        'mountopt = "nodev"' | sudo tee /etc/containers/storage.conf >/dev/null
    sudo rm -rf /var/lib/containers/storage /run/containers/storage /mnt/containers-storage
    rm -rf "${HOME}/.local/share/containers/storage" 2>/dev/null || true
    sudo podman system reset -f || true
}

# Print the registry host the SSOT publishes to, or fail with the reason.
#
# MIOS_IMAGE_NAME is resolved from mios.toml, so an empty or host-less value is
# a broken SSOT rather than a workflow typo, and saying which of the two it is
# saves the reader a round trip.
mios_ci_registry_host() {
    local root="${1:-.}"
    # shellcheck source=/dev/null
    source "${root}/tools/lib/userenv.sh"
    if [[ -z "${MIOS_IMAGE_NAME:-}" ]]; then
        echo "MIOS_IMAGE_NAME resolved empty from the SSOT" >&2
        return 1
    fi
    if [[ "$MIOS_IMAGE_NAME" != */* ]]; then
        echo "MIOS_IMAGE_NAME must carry a registry host, got '${MIOS_IMAGE_NAME}'" >&2
        return 1
    fi
    printf '%s\n' "${MIOS_IMAGE_NAME%%/*}"
}

# Print the image tag: VERSION, a UTC timestamp and the short commit.
#
# VERSION must reduce to one token. Comment lines and whitespace are stripped
# because a multi-line value writes a bare line to the step output file, which
# the runner rejects as an invalid format rather than as a bad version.
mios_ci_version() {
    local root="${1:-.}" ver
    ver="$(grep -vE '^[[:space:]]*#' "${root}/VERSION" 2>/dev/null | tr -d '[:space:]')"
    printf '%s\n' "${ver:-0.0.0}"
}

mios_ci_image_tag() {
    local root="${1:-.}" ver sha ts
    ver="$(mios_ci_version "$root")"
    sha="$(git -C "$root" rev-parse --short=12 HEAD)"
    ts="$(date -u +%Y%m%d-%H%M%S)"
    printf '%s\n' "${ver#v}-${ts}-${sha}"
}

mios_ci_verify_bootc_labels() {
    local image="${1:-localhost/mios:latest}" label value
    for label in containers.bootc ostree.bootable; do
        value="$(sudo podman image inspect "$image" \
                 --format "{{ index .Config.Labels \"${label}\" }}" 2>/dev/null)"
        if [[ "$value" != "1" ]]; then
            echo "${image} carries ${label}='${value:-<unset>}', expected 1" >&2
            return 1
        fi
    done
    echo "${image}: containers.bootc=1 ostree.bootable=1"
}

# Every CI producer shares the vendor CPU floor and the runner's effective limits.
mios_ci_require_host_cpus() {
    local root="${1:?usage: mios_ci_require_host_cpus ROOT}"
    MIOS_DRIFT_ROOT="$root" MIOS_ROOT="$root" python3 "$root/tools/ci-suites.py" --require-host-cpus
}

mios_ci_require_cpus() {
    local root="${1:?usage: mios_ci_require_cpus ROOT ALLOCATION}" requested="${2-}"
    MIOS_DRIFT_ROOT="$root" MIOS_ROOT="$root" python3 "$root/tools/ci-suites.py" --require-cpus "$requested"
}

# Verify downloaded bytes in the producer's development userspace. Ubuntu's
# Pillow/zlib emits different PNG/ICO bytes; source binding must stay byte exact.
mios_ci_verify_portal() (
    set -euo pipefail
    local root run image config
    root="$(cd "${1:-.}" && pwd)"
    run="$(cd "${2:?usage: mios_ci_verify_portal ROOT RUN_DIR}" && pwd)"
    image="$(python3 "$root/tools/ci-suites.py" --fedora-image)"
    config="$(python3 - "$root/usr/share/mios/mios.toml" <<'PY'
import shlex, sys, tomllib
with open(sys.argv[1], "rb") as source:
    data = tomllib.load(source)
builder = data["desktop"]["portal"]["builder"]
# These are the verifier's imports, selected from the declared producer set.
packages = ["python3", "python3-pillow"]
if not all(package in builder["packages"] for package in packages):
    raise SystemExit("Portal builder must declare the Python/Pillow verifier dependencies")
for key, value in (
    ("PV_CPUS", data["bootstrap"]["dev_vm"]["minimum_cpus"]),
    ("PV_MEMORY", builder["scope_memory_max"]),
    ("PV_PACKAGES", " ".join(packages)),
):
    print(f"{key}={shlex.quote(str(value))}")
PY
)"
    eval "$config"
    mios_ci_require_cpus "$root" "$PV_CPUS"
    # Both mounts are read-only; the complete original verify-run still checks
    # the package digest, source-derived branding, overlay, and smoke receipts.
    sudo podman run --rm --name podman-MiOS-DEV --user 0:0 \
        --cpus "$PV_CPUS" --memory "$PV_MEMORY" --memory-swap "$PV_MEMORY" \
        -v "$root:/ctx:ro" -v "$run:/run/mios-portal:ro" --workdir /ctx \
        -e MIOS_PORTAL_VERIFY_PACKAGES="$PV_PACKAGES" \
        -e MIOS_THEME_ROOT=/ctx -e MIOS_TOML_ROOT=/ctx \
        -e MIOS_VENDOR_TOML=/ctx/usr/share/mios/mios.toml \
        -e MIOS_HOST_TOML=/nonexistent -e MIOS_USER_TOML=/nonexistent \
        --entrypoint /bin/bash "$image" --noprofile --norc -c '
            set -euo pipefail
            set -f
            # shellcheck disable=SC2086
            dnf install -y --setopt=install_weak_deps=False $MIOS_PORTAL_VERIFY_PACKAGES
            python3 -c "import PIL, zlib; print(\"Portal verifier:\", PIL.__version__, zlib.ZLIB_RUNTIME_VERSION)"
            exec python3 ./usr/libexec/mios/mios-portal-tree verify-run --run /run/mios-portal
        '
)

# Build Portal once in the bounded development environment. The image bake consumes
# only stage-run's verified deliverable; no Gecko source/compiler enters that bake.
mios_ci_build_portal() (
    set -euo pipefail
    local root state image overlay ts config bootstrap_packages icon_source workspace
    root="$(cd "${1:-.}" && pwd)"
    # The source context uses the same internal path as the OCI build. A host
    # /home path cannot be a mount target before ostree tmpfiles creates /var/home.
    workspace=/ctx
    state="${2:?usage: mios_ci_build_portal ROOT STATE_DIR}"
    [[ ! -e "$state" ]] || { echo "Portal build state already exists: $state" >&2; exit 1; }
    image="$(python3 "$root/tools/ci-suites.py" --fedora-image)"
    # Read only vendor data: runner state must never inherit an operator's secrets.
    config="$(MIOS_TOML_ROOT="$root" MIOS_VENDOR_TOML="$root/usr/share/mios/mios.toml" MIOS_HOST_TOML=/nonexistent \
        MIOS_USER_TOML=/nonexistent "$root/usr/libexec/mios/mios-portal-build" config)"
    eval "$config"
    mios_ci_require_cpus "$root" "${PB_CPUS:-}"
    mkdir -p "$state"
    state="$(cd "$state" && pwd)"
    # Buildah context-overlay upper/work stays on the runner filesystem.
    mkdir -m 1777 "$state/tmp"
    overlay="$state/paths.toml"
    # Nested commands reuse the private development container's working network
    # instead of a second bridge/resolver boundary. Its outer isolation stays put.
    # Buildah's OCI RUN also lacks a systemd bus; both defaults are CI-local.
    printf '%s\n' '[containers]' 'netns = "host"' \
        '[engine]' 'cgroup_manager = "cgroupfs"' >"$state/containers.conf"
    [[ "$PB_ICON_SRC" = /* && "$PB_ICON_SRC" != *"/../"* ]] || {
        echo "Portal icon must be an absolute checkout path" >&2; exit 1;
    }
    icon_source="$root$PB_ICON_SRC"
    [[ -f "$icon_source" ]] || { echo "Portal icon not found: $icon_source" >&2; exit 1; }
    bootstrap_packages="$(python3 - "$root/usr/share/mios/mios.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as source:
    data = tomllib.load(source)
assert "podman" in data["packages"]["devcontainer"]["pkgs"]
print(" ".join(dict.fromkeys([*data["desktop"]["portal"]["builder"]["packages"], "podman"])))
PY
)"
    python3 - "$root" "$overlay" "$workspace" <<'PY'
import json, sys, tomllib
from pathlib import Path
root, output, workspace = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
data = tomllib.loads((root / "usr/share/mios/mios.toml").read_text())
builder = data["desktop"]["portal"]["builder"]
paths = {
    "containerfile_dir": str(workspace / builder["containerfile_dir"].lstrip("/")),
    "product_tree": str(workspace / builder["product_tree"].lstrip("/")),
    "heavy_lock": "/var/lib/mios-build/portal/heavy.lock",
}
output.write_text("[desktop.portal.builder]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in paths.items()))
PY
    # Existing containers belong to their owners: a name collision fails instead
    # of stopping an unrelated build.
    sudo podman run --detach --name podman-MiOS-DEV --privileged --user 0:0 \
        --cgroupns private --systemd always --memory "$PB_SCOPE_MEM" \
        --memory-swap "$PB_SCOPE_MEM" --cpus "$PB_CPUS" \
        -v "$root:$workspace:rw" -v "$state:/var/lib/mios-build:rw" \
        -v "$state/tmp:/var/tmp:rw" -v "$icon_source:$PB_ICON_SRC:ro" \
        --workdir "$workspace" --entrypoint /sbin/init "$image"
    trap 'sudo podman rm --force podman-MiOS-DEV >/dev/null' EXIT
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    sudo podman exec \
        -e CONTAINERS_CONF_OVERRIDE=/var/lib/mios-build/containers.conf \
        -e MIOS_PORTAL_BOOTSTRAP_PACKAGES="$bootstrap_packages" \
        -e MIOS_VENDOR_TOML="$workspace/usr/share/mios/mios.toml" \
        -e MIOS_HOST_TOML=/var/lib/mios-build/paths.toml \
        -e MIOS_USER_TOML=/nonexistent \
        -e MIOS_THEME_ROOT="$workspace" -e MIOS_TOML_ROOT="$workspace" \
        -e MIOS_PORTAL_BUILD_SCOPED=1 -e MIOS_PORTAL_BUILD_TS="$ts" \
        -e XDG_RUNTIME_DIR=/run/mios-portal-build \
        podman-MiOS-DEV bash --noprofile --norc -c '
            set -euo pipefail
            systemd-tmpfiles --create --prefix=/var/home --prefix=/var/roothome \
                --prefix=/var/opt --prefix=/var/srv --prefix=/var/mnt --prefix=/var/usrlocal
            # The canonical builder table supplies its dependencies. Podman is
            # the development environment runtime from the existing package set.
            set -f
            # No Python is assumed in the machine OS before this install.
            # shellcheck disable=SC2086
            dnf install -y --setopt=install_weak_deps=False $MIOS_PORTAL_BOOTSTRAP_PACKAGES
            mkdir -p /var/lib/mios-build/portal /var/lib/mios-build/artifacts/mios-portal "$XDG_RUNTIME_DIR"
            config="$(./usr/libexec/mios/mios-portal-build config)"
            eval "$config"
            free_gb=$(( $(df --output=avail -k /var/lib/mios-build/portal | tail -1) / 1024 / 1024 ))
            [[ "$free_gb" -ge "$PB_MIN_FREE_GB" ]] || { echo "Portal requires $PB_MIN_FREE_GB GB free" >&2; exit 1; }
            # The browser smoke opens the real Portal URL. A fresh CI machine
            # has no gateway service, so serve its actual router in this private
            # development namespace for the complete build/smoke lifetime.
            runtime=/var/lib/mios-build/portal-runtime
            python3 -m venv "$runtime"
            "$runtime/bin/python3" -m pip install -r ./usr/lib/mios/agent-pipe/requirements.txt
            PYTHONPATH="$PWD/usr/lib/mios/agent-pipe:$PWD/usr/lib/mios" \
                "$runtime/bin/python3" - "$PB_PORTAL_URL" "$PB_LOCK" systemd-run --scope --quiet --collect \
                -p MemoryMax="$PB_SCOPE_MEM" -p MemorySwapMax="$PB_SCOPE_SWAP" -p Delegate=yes \
                ./usr/libexec/mios/mios-portal-build build <<"MIOS_PORTAL_RUNTIME"
import asyncio
import sys
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI

import mios_toml
from mios_pipe.routing.portal import portal_router

url = urlsplit(sys.argv[1])
if url.scheme != "http" or url.hostname != "localhost" or not url.port:
    raise SystemExit("Portal smoke requires its configured localhost HTTP endpoint")
startup_timeout = mios_toml.load_merged()["ci"]["suite_timeout_s"]
app = FastAPI()
app.include_router(portal_router)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=url.port, access_log=False))

async def main():
    serving = asyncio.create_task(server.serve())
    process = None
    waiting = None
    try:
        async def ready():
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("Portal server exited before binding its endpoint")
                await asyncio.sleep(0.01)
        await asyncio.wait_for(ready(), timeout=startup_timeout)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
            response = await client.get(sys.argv[1])
            response.raise_for_status()
            if response.status_code != 200 or not response.headers.get("content-type", "").startswith("text/html"):
                raise RuntimeError("Portal endpoint did not serve its HTML page")
        print("Portal smoke backend ready at " + sys.argv[1], flush=True)
        # Preserve the canonical lock helper and every bounded scope argument.
        process = await asyncio.create_subprocess_exec(
            "bash", "--noprofile", "--norc", "-c",
            "source \"$1\"; shift; mios_heavy_lock_exec \"$@\"",
            "mios-portal-ci", "./usr/lib/mios/heavy-lock.sh", *sys.argv[2:])
        waiting = asyncio.create_task(process.wait())
        finished, _ = await asyncio.wait((waiting, serving), return_when=asyncio.FIRST_COMPLETED)
        if serving in finished and not waiting.done():
            await serving
            raise RuntimeError("Portal server exited during the build or smoke")
        return await waiting
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        if waiting is not None:
            await waiting
        server.should_exit = True
        await serving

raise SystemExit(asyncio.run(main()))
MIOS_PORTAL_RUNTIME
            ./usr/libexec/mios/mios-portal-tree stage-run \
                --run "/var/lib/mios-build/artifacts/mios-portal/$MIOS_PORTAL_BUILD_TS" \
                --out "$PWD/.staged-portal"
        '
    sudo chown -R "$(id -u):$(id -g)" "$root/.staged-portal"
)
