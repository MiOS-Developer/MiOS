#!/usr/bin/env bash
# AI-hint: The one MiOS cloud-environment script: runs the devcontainer ([image].ref plus wiring) under podman or docker for Claude Code cloud, Codex cloud and Cloud Shell, creates a GCE VM, preflights disk, pulls and applies the [deployment.cloud] overlay.
# AI-related: .devcontainer/Containerfile, .devcontainer/devcontainer.json, .devcontainer/artifact-builder/devcontainer.json, .devcontainer/cloud-shell/claude-code-cloud.sh, .devcontainer/cloud-shell/codex-cloud.sh, usr/share/mios/mios.toml
# AI-functions: main, preflight, pull_image, status, up, enter, overlay, gce_up, claude_code, codex
#
# Every environment runs the SAME image: .devcontainer/Containerfile is FROM
# [image].ref and adds only container wiring, so building it is a pull of the
# MiOS image plus one small layer. The container's user, workspace, env, mounts,
# privilege and lifecycle are read from the devcontainer.json (never restated):
# the main one, or with --builder the artifact-builder one (privileged, with its
# own container store, for `podman build` of MiOS and disk artifacts).
#
#   bootstrap.sh up [--builder]        preflight, pull, build, start, lifecycle
#   bootstrap.sh enter [CMD...]        run CMD (or a login shell) in it, same $PWD
#   bootstrap.sh status                image, pull progress and container readiness
#   bootstrap.sh preflight [--builder] free space in the container store vs the SSOT
#   bootstrap.sh pull [--background]   pull [image].ref
#   bootstrap.sh overlay [--apply]     the [deployment.cloud] drop-in (print or write)
#   bootstrap.sh install               Google / Oracle Cloud Shell launcher
#   bootstrap.sh claude-code           Claude Code cloud setup (claude-code-cloud.sh)
#   bootstrap.sh codex install|start|check   Codex cloud (codex-cloud.sh)
#   bootstrap.sh gce-up | gce-down     a Compute Engine VM running the image (gcloud)
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT="$(cd "$(dirname "$SELF")/../.." && pwd)"
if [[ ! -f "$ROOT/usr/share/mios/mios.toml" ]]; then
    ROOT="${MIOS_ROOT:-}"
fi
STATE=""
BUILDER=0
RT=""

log() { printf '[mios-cloud] %s\n' "$*" >&2; }
die() { printf '[mios-cloud] ERROR: %s\n' "$*" >&2; exit 1; }

require_root_checkout() {
    [[ -n "$ROOT" && -f "$ROOT/usr/share/mios/mios.toml" ]] \
        || die "no MiOS checkout: run this from .devcontainer/cloud-shell/ of a MiOS clone, or set MIOS_ROOT"
}

# Resolve the selected checkout's layered operator configuration.
ssot() {
    if [[ -n "${MIOS_NATIVE_BIN_DIR:-}" ]]; then
        local tool="$MIOS_NATIVE_BIN_DIR/mios-toml-get"
        [[ -x "$tool" ]] || die "native mios-toml-get is missing from the selected catalog"
        MIOS_TOML_ROOT="$ROOT" "$tool" "$@"
    else
        MIOS_TOML_ROOT="$ROOT" python3 "$ROOT/usr/libexec/mios/mios-toml-get" "$@"
    fi
}
need() {
    local v
    v="$(ssot "$@")"
    [[ -n "$v" ]] || die "mios.toml [$1].$2 is empty"
    printf '%s\n' "$v"
}

initialize_state() {
    STATE="${MIOS_DEPLOYMENT_CLOUD_STATE_DIRECTORY:-$(ssot deployment.cloud state_directory)}"
    if [[ -z "$STATE" ]]; then
        if [[ $EUID -eq 0 ]]; then STATE=/var/lib/mios-cloud
        else STATE="${XDG_STATE_HOME:-$HOME/.local/state}/mios-cloud"
        fi
    fi
}

# A devcontainer.json key, JSON-decoded to shell-friendly text (lists one per line).
dc_json() {
    python3 - "$CONFIG" "$1" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
v = doc.get(sys.argv[2], "")
if isinstance(v, bool):
    print("true" if v else "false")
elif isinstance(v, list):
    print("\n".join(str(x) for x in v))
elif isinstance(v, dict):
    print("\n".join(f"{k}={x}" for k, x in v.items()))
else:
    print(v)
PY
}

select_config() {
    [[ "$BUILDER" == 0 || "$BUILDER" == 1 ]] || die "deployment.cloud.builder must be 0 or 1"
    if [[ "$BUILDER" == 1 ]]; then
        CONFIG="$ROOT/.devcontainer/artifact-builder/devcontainer.json"
    else
        CONFIG="$ROOT/.devcontainer/devcontainer.json"
    fi
    [[ -f "$CONFIG" ]] || die "$CONFIG is missing"
}

select_runtime() {
    RT="${MIOS_DEPLOYMENT_CLOUD_RUNTIME:-$(ssot deployment.cloud runtime)}"
    if [[ -z "$RT" ]]; then
        if command -v podman >/dev/null 2>&1; then RT=podman
        elif command -v docker >/dev/null 2>&1; then RT=docker
        else die "neither podman nor docker is installed; MiOS is podman-native: install podman"
        fi
    fi
    command -v "$RT" >/dev/null 2>&1 || die "MIOS_DEPLOYMENT_CLOUD_RUNTIME=$RT is not installed"
}

image_present() { "$RT" image inspect "$1" >/dev/null 2>&1; }

store_path() {
    if [[ "$RT" == podman ]]; then podman info --format '{{.Store.GraphRoot}}'
    else docker info --format '{{.DockerRootDir}}'
    fi
}

free_kb() {
    local p="$1"
    while [[ ! -d "$p" && "$p" != / ]]; do p="$(dirname "$p")"; done
    df -Pk "$p" | awk 'NR == 2 { print $4 }'
}

platform_hint() {
    if [[ "${CLOUD_SHELL:-}" == true ]]; then
        echo "Google Cloud Shell keeps a 5 GB persistent \$HOME on a small ephemeral VM disk and cannot hold the MiOS image; run 'bootstrap.sh gce-up' for a Compute Engine VM sized from [deployment.cloud.gce]."
    elif [[ -f /etc/oracle-release || -n "${OCI_CLI_CONFIG_FILE:-}" ]]; then
        echo "Oracle Cloud Shell has a 5 GB home and cannot hold the MiOS image; run it on a VM with that much disk."
    else
        echo "Free space there, point the store at a larger disk (podman: graphroot in storage.conf), or use 'bootstrap.sh gce-up'."
    fi
}

# Free space in the container store against [deployment.cloud] (pull, plus a
# nested build with --builder). An image already present needs no pull space.
preflight() {
    local ref want store free
    ref="$(need image ref)"
    want=0
    image_present "$ref" || want="$(need deployment.cloud min_free_gb)"
    [[ "$BUILDER" == 1 ]] && want=$((want + $(need deployment.cloud build_free_gb)))
    store="$(store_path)" || die "$RT cannot report its storage path"
    free=$(( $(free_kb "$store") / 1048576 ))
    if (( free < want )); then
        die "${free} GB free in ${store} (the ${RT} store), ${want} GB needed for ${ref}$([[ "$BUILDER" == 1 ]] && echo ' and a nested build') ([deployment.cloud].min_free_gb$([[ "$BUILDER" == 1 ]] && echo ' + build_free_gb')). $(platform_hint)"
    fi
    log "preflight: ${free} GB free in ${store}, ${want} GB needed"
}

pull_running() { [[ -f "$STATE/pull.pid" ]] && kill -0 "$(cat "$STATE/pull.pid")" 2>/dev/null; }

# Foreground, or in the background with its log, exit code and start-of-pull free
# space recorded so `status` can report progress after this script has exited.
pull_image() {
    local ref store
    ref="$(need image ref)"
    image_present "$ref" && { log "$ref is already in the $RT store"; return 0; }
    install -d -m 0755 "$STATE"
    if [[ "${1:-}" != --background ]]; then
        log "pulling $ref (about 23 GB compressed)"
        "$RT" pull "$ref"
        return
    fi
    pull_running && { log "a pull of $ref is already running (pid $(cat "$STATE/pull.pid"))"; return 0; }
    store="$(store_path)"
    printf '%s %s %s\n' "$(date +%s)" "$(free_kb "$store")" "$store" > "$STATE/pull.start"
    rm -f "$STATE/pull.rc"
    # Its own session: a caller's timeout (the setup budget) must not take it down.
    setsid nohup bash -c '"$1" pull "$2" > "$3/pull.log" 2>&1; echo $? > "$3/pull.rc"' _ "$RT" "$ref" "$STATE" \
        >/dev/null 2>&1 < /dev/null &
    echo $! > "$STATE/pull.pid"
    log "pulling $ref in the background (pid $!); progress: bootstrap.sh status"
}

container_name() { basename "$(need image dev_local_tag)" | cut -d: -f1; }

# 0 ready, 3 not yet (pulling or not started), 1 failed.
status() {
    local ref dev name started kb store
    ref="$(need image ref)"; dev="$(need image dev_local_tag)"; name="$(container_name)"
    if ! image_present "$ref"; then
        if pull_running; then
            read -r started kb store < "$STATE/pull.start"
            printf 'MiOS image %s: pulling for %s min, %s GB written to %s so far (log %s)\n' "$ref" \
                $(( ($(date +%s) - started) / 60 )) $(( (kb - $(free_kb "$store")) / 1048576 )) "$store" "$STATE/pull.log"
            return 3
        fi
        if [[ -f "$STATE/pull.rc" && "$(cat "$STATE/pull.rc")" != 0 ]]; then
            printf 'MiOS image %s: the pull failed (exit %s):\n' "$ref" "$(cat "$STATE/pull.rc")"
            tail -n 5 "$STATE/pull.log" 2>/dev/null
            return 1
        fi
        printf 'MiOS image %s is not pulled; run: bash %s up\n' "$ref" "$SELF"
        return 3
    fi
    if [[ "$("$RT" container inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == true ]]; then
        printf 'ready: container %s runs %s (from %s)\n' "$name" "$dev" "$ref"
        [[ -f "$STATE/lifecycle.failed" ]] && printf 'lifecycle: %s\n' "$(cat "$STATE/lifecycle.failed")"
        return 0
    fi
    printf 'MiOS image %s is present; container %s is not running: bash %s up\n' "$ref" "$name" "$SELF"
    return 3
}

# [deployment.cloud].overlay as TOML: the host drop-in that turns runtime model
# pulls, runtime desktop installs and the desktop session off without touching
# the image. --apply writes it to [deployment.cloud].dropin (root).
overlay() {
    local text dropin
    text="$(ssot --section deployment.cloud.overlay | python3 -c '
import json, sys
data = json.load(sys.stdin)
if not data or not all(isinstance(v, dict) for v in data.values()):
    sys.exit("mios.toml [deployment.cloud.overlay] must hold tables of SSOT keys")
print("# GENERATED from mios.toml [deployment.cloud.overlay] by .devcontainer/cloud-shell/bootstrap.sh;")
print("# a cloud/container deployment of the MiOS image. Delete it to restore the vendor behaviour.")
def emit(path, table):
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
    if scalars:
        print("\n[" + ".".join(path) + "]")
        for k, v in scalars:
            print(k + " = " + json.dumps(v))
    for k, v in table.items():
        if isinstance(v, dict):
            emit(path + [k], v)
for k, v in data.items():
    emit([k], v)
')" || die "could not render [deployment.cloud.overlay]"
    if [[ "${1:-}" != --apply ]]; then
        printf '%s\n' "$text"
        return
    fi
    dropin="$(need deployment.cloud dropin)"
    [[ $EUID -eq 0 ]] || die "overlay --apply writes $dropin and needs root"
    install -d -m 0755 "$(dirname "$dropin")"
    printf '%s\n' "$text" > "$dropin.new"
    chmod 0644 "$dropin.new"
    mv -f "$dropin.new" "$dropin"
    log "cloud overlay written to $dropin"
}

# The host CA bundle the container must trust (an egress proxy's, e.g. Claude Code cloud).
ca_bundle() {
    local c
    for c in "${SSL_CERT_FILE:-}" /root/.ccr/ca-bundle.crt; do
        [[ -n "$c" && -f "$c" ]] && { printf '%s\n' "$c"; return 0; }
    done
    return 1
}

# Run the devcontainer: build the thin image (a pull of [image].ref), create the
# container from the devcontainer.json, run postCreate once and postStart on every start.
up() {
    local ref dev name user wsf wsroot primary uid gid repo src ca line target
    require_root_checkout; select_runtime; select_config
    ref="$(need image ref)"; dev="$(need image dev_local_tag)"; name="$(container_name)"
    preflight
    pull_image
    "$RT" build -f "$ROOT/.devcontainer/Containerfile" -t "$dev" "$ROOT/.devcontainer" >&2
    user="$(dc_json remoteUser)"; wsf="$(dc_json workspaceFolder)"
    [[ -n "$user" && -n "$wsf" ]] || die "$CONFIG names no remoteUser or workspaceFolder"
    wsroot="$(need workspace root)"; primary="$(need workspace primary)"
    if ! "$RT" container inspect "$name" >/dev/null 2>&1; then
        uid="$("$RT" run --rm --entrypoint id "$dev" -u "$user")"; gid="$("$RT" run --rm --entrypoint id "$dev" -g "$user")"
        local args=(create --name "$name" --hostname "$name" --network host --init
                    --mount "type=bind,source=$ROOT,target=$wsroot/$primary")
        # Sibling [workspace].repos checkouts beside this one, at their SSOT paths.
        while IFS=$'\t' read -r repo _; do
            src="$(dirname "$ROOT")/$repo"
            [[ "$repo" != "$primary" && -d "$src/.git" ]] && args+=(--mount "type=bind,source=$src,target=$wsroot/$repo")
        done < <(ssot workspace repos | python3 -c 'import json, sys; [print(r["name"] + "\t" + r["url"]) for r in json.load(sys.stdin)]')
        # The container's user owns the workspace: rootless podman maps the caller to
        # it; as root (a disposable cloud VM) the mounted checkouts are handed to it.
        if [[ "$RT" == podman && $EUID -ne 0 ]]; then
            args+=(--userns "keep-id:uid=$uid,gid=$gid")
        elif [[ $EUID -eq 0 ]]; then
            chown -R "$uid:$gid" "$ROOT"
            for line in "${args[@]}"; do
                [[ "$line" == type=bind,source=* ]] || continue
                src="${line#type=bind,source=}"; src="${src%%,*}"
                [[ "$src" == "$ROOT" ]] || chown -R "$uid:$gid" "$src"
            done
            git config --global --add safe.directory '*'
        fi
        [[ "$(dc_json privileged)" == true ]] && args+=(--privileged)
        while IFS= read -r line; do [[ -n "$line" ]] && args+=(--mount "$line"); done < <(dc_json mounts)
        while IFS= read -r line; do [[ -n "$line" ]] && args+=(-e "$line"); done < <(dc_json containerEnv)
        for line in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy NO_PROXY no_proxy; do
            [[ -n "${!line:-}" ]] && args+=(-e "$line=${!line}")
        done
        if ca="$(ca_bundle)"; then
            target=/etc/mios-cloud/ca-bundle.pem
            args+=(--mount "type=bind,source=$ca,target=$target,readonly")
            for line in SSL_CERT_FILE CURL_CA_BUNDLE REQUESTS_CA_BUNDLE NODE_EXTRA_CA_CERTS PIP_CERT GIT_SSL_CAINFO; do
                args+=(-e "$line=$target")
            done
        fi
        "$RT" "${args[@]}" "$dev" sleep infinity >/dev/null
        rm -f "$STATE/lifecycle.created"
        log "created container $name from $dev ($CONFIG)"
    fi
    "$RT" start "$name" >/dev/null
    install -d -m 0755 "$STATE"
    rm -f "$STATE/lifecycle.failed"
    if [[ ! -f "$STATE/lifecycle.created" ]]; then
        lifecycle postCreateCommand && touch "$STATE/lifecycle.created"
    fi
    lifecycle postStartCommand
    status
}

lifecycle() {
    local cmd user wsf
    cmd="$(dc_json "$1")"; [[ -n "$cmd" ]] || return 0
    user="$(dc_json remoteUser)"; wsf="$(dc_json workspaceFolder)"
    log "$1 as $user in $wsf: $cmd"
    if ! "$RT" exec -u "$user" -w "$wsf" "$(container_name)" bash -lc "$cmd"; then
        echo "$1 failed; rerun: bash $SELF up" > "$STATE/lifecycle.failed"
        log "$1 failed (the container stays usable; rerun: bash $SELF up)"
        return 1
    fi
}

# Run a command (or a login shell) in the container as its user, translating $PWD.
# Before the image is in, report the pull instead; once it is, bring the container up.
enter() {
    local wsroot primary repo wd="" name rc=0
    require_root_checkout; select_runtime; select_config
    status >/dev/null || rc=$?
    if [[ $rc -ne 0 ]]; then
        image_present "$(need image ref)" || { status; exit 3; }
        log "the MiOS image is in; bringing the devcontainer up (the first time runs its lifecycle)"
        ( up ) >&2 || true
        status >/dev/null || { status; exit 3; }
    fi
    name="$(container_name)"
    wsroot="$(need workspace root)"; primary="$(need workspace primary)"
    if [[ "$PWD" == "$ROOT" || "$PWD" == "$ROOT/"* ]]; then
        wd="$wsroot/$primary${PWD#"$ROOT"}"
    else
        while IFS= read -r repo; do
            local src; src="$(dirname "$ROOT")/$repo"
            if [[ "$PWD" == "$src" || "$PWD" == "$src/"* ]]; then wd="$wsroot/$repo${PWD#"$src"}"; break; fi
        done < <(ssot workspace repos | python3 -c 'import json, sys; [print(r["name"]) for r in json.load(sys.stdin)]')
    fi
    [[ -n "$wd" ]] || wd="$(dc_json workspaceFolder)"
    [[ $# -gt 0 ]] || set -- bash -l
    local tty=(-i); [[ -t 0 && -t 1 ]] && tty=(-it)
    exec "$RT" exec "${tty[@]}" -u "$(dc_json remoteUser)" -w "$wd" "$name" "$@"
}

write_wrapper() {  # $1 = install path: a `mios-dev` that enters this checkout's container
    local tmp="$1.new.$$"
    install -d -m 0755 "$(dirname "$1")"
    printf '#!/usr/bin/env bash\n# GENERATED by %s: run a command in the MiOS devcontainer, same $PWD.\nexec bash %q enter%s "$@"\n' \
        "$SELF" "$SELF" "$([[ "$BUILDER" == 1 ]] && echo ' --builder')" > "$tmp"
    chmod 0755 "$tmp"
    mv -f "$tmp" "$1"
    log "installed $1"
}

install_podman() {
    command -v podman >/dev/null 2>&1 && return 0
    [[ $EUID -eq 0 ]] || die "podman is missing and installing it needs root"
    log "installing podman"
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q podman
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y podman
    else
        die "no apt-get or dnf to install podman"
    fi
}

# Claude Code cloud (claude-code-cloud.sh): the setup script must finish in about
# five minutes, so it only provisions and starts the pull; the first `mios-dev`
# reports progress until the image is in, then brings the container up.
claude_code() {
    require_root_checkout
    install_podman
    select_runtime
    BUILDER="${MIOS_DEPLOYMENT_CLOUD_BUILDER:-$(need deployment.cloud builder)}"; select_config
    install -d -m 0755 "$STATE"
    printf '%s\n' "$ROOT" > "$STATE/source-root"
    preflight
    pull_image --background
    write_wrapper /usr/local/bin/mios-dev
    status || true
}

# Codex cloud (codex-cloud.sh): install and start fail closed.
codex() {
    require_root_checkout
    case "${1:-install}" in
        install)
            [[ $EUID -eq 0 ]] || die "run the Codex install as root"
            install_podman
            command -v git >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 || die "git and python3 are required"
            select_runtime; select_config
            install -d -m 0755 "$STATE"; printf '%s\n' "$ROOT" > "$STATE/source-root"
            up
            write_wrapper /usr/local/bin/mios-dev
            codex check ;;
        start)
            select_runtime; select_config; up
            "$RT" exec -u "$(dc_json remoteUser)" "$(container_name)" bash -lc \
                "bash $(need workspace root)/$(need workspace primary)/.devcontainer/mios-agent-pipe-dev start && /usr/lib/mios/mcp/.venv/bin/python3 /usr/libexec/mios/mios-mcp-server --project-agent-clients"
            codex check ;;
        check)
            select_runtime; select_config
            "$RT" exec -u "$(dc_json remoteUser)" -e "WORKSPACE_ROOT=$(need workspace root)/$(need workspace primary)" \
                "$(container_name)" bash -lc '
set -euo pipefail
. /etc/os-release
[[ "$ID" == fedora ]] || { echo "MiOS requires its Fedora userspace, got $ID" >&2; exit 1; }
[[ "$(id -u)" != 0 ]] || { echo "MiOS tasks must run unprivileged" >&2; exit 1; }
test -r /usr/share/mios/mios.toml
test -x /usr/libexec/mios/mios-agent-relay
tmux -V
oh-my-posh --version
/usr/libexec/mios/mios-gen render-tmux-theme --runtime "${XDG_CACHE_HOME:-$HOME/.cache}/mios-cloud-theme"
python3 - <<"PY"
import json, subprocess
catalog = json.loads(subprocess.check_output(["mios", "agents"], text=True))
for row in catalog["agents"]:
    subprocess.run([row["executable"], "--version"], check=True, timeout=30)
PY
bash "$WORKSPACE_ROOT/.devcontainer/mios-agent-pipe-dev" check
echo "MiOS image userspace, SSOT theme, agent catalog and gateway checks passed."
' ;;
        *) die "usage: bootstrap.sh codex install|start|check" ;;
    esac
}

# Google / Oracle Cloud Shell: a launcher in the persistent $HOME.
install_launcher() {
    require_root_checkout
    local bin="$HOME/.local/bin"
    install -d -m 0755 "$bin" "$STATE"
    printf '%s\n' "$ROOT" > "$STATE/source-root"
    printf '#!/usr/bin/env bash\nexec bash %q "$@"\n' "$SELF" > "$bin/mios-cloud-shell"
    chmod 0755 "$bin/mios-cloud-shell"
    write_wrapper "$bin/mios-dev"
    log "installed mios-cloud-shell and mios-dev in $bin; run: mios-cloud-shell up"
    select_runtime
    preflight || true
}

oracle_podman_socket() {
    [[ -f /etc/oracle-release || -n "${OCI_CLI_CONFIG_FILE:-}" ]] || return 0
    local socket="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/podman/podman.sock"
    if [[ ! -S "$socket" ]]; then
        install -d -m 0700 "$(dirname "$socket")"
        podman system service --time=0 "unix://$socket" >"$STATE/podman-api.log" 2>&1 &
        for _ in $(seq 1 20); do [[ -S "$socket" ]] && break; sleep 1; done
    fi
    [[ -S "$socket" ]] || die "the podman API socket did not start: $socket"
}

gce_up() {
    require_root_checkout
    command -v gcloud >/dev/null 2>&1 || die "gcloud is not installed (https://cloud.google.com/sdk/docs/install)"
    local instance zone mtype disk dtype family iproject cname ref dropin want startup project
    local project_args=()
    project="${MIOS_DEPLOYMENT_CLOUD_GCE_PROJECT:-$(ssot deployment.cloud.gce project)}"
    [[ -z "$project" ]] || project_args=(--project "$project")
    instance="${MIOS_DEPLOYMENT_CLOUD_GCE_INSTANCE:-$(need deployment.cloud.gce instance)}"
    zone="${MIOS_DEPLOYMENT_CLOUD_GCE_ZONE:-$(need deployment.cloud.gce zone)}"
    mtype="${MIOS_DEPLOYMENT_CLOUD_GCE_MACHINE_TYPE:-$(need deployment.cloud.gce machine_type)}"
    disk="${MIOS_DEPLOYMENT_CLOUD_GCE_BOOT_DISK_GB:-$(need deployment.cloud.gce boot_disk_gb)}"
    dtype="$(need deployment.cloud.gce boot_disk_type)"
    family="$(need deployment.cloud.gce image_family)"; iproject="$(need deployment.cloud.gce image_project)"
    cname="$(need deployment.cloud.gce container)"
    ref="$(need image ref)"; dropin="$(need deployment.cloud dropin)"
    want=$(( $(need deployment.cloud min_free_gb) + $(need deployment.cloud build_free_gb) ))
    (( disk >= want + 20 )) || die "boot disk ${disk} GB is below the ${want} GB the image and a nested build need, plus the OS"
    startup="$(mktemp)"
    trap 'rm -f "$startup"' RETURN
    {
        printf '#!/bin/bash\n# GENERATED by MiOS bootstrap.sh gce-up from mios.toml [deployment.cloud]; runs on every boot.\nset -euo pipefail\n'
        printf 'REF=%q\nNAME=%q\nDROPIN=%q\nWANT_GB=%q\n' "$ref" "$cname" "$dropin" "$want"
        cat <<'SH'
log() { echo "[mios-gce] $*"; }
command -v podman >/dev/null || { apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q podman; }
install -d -m 0755 /etc/mios-cloud
cat > /etc/mios-cloud/50-cloud.toml <<'TOML'
SH
        overlay
        cat <<'SH'
TOML
if ! podman image exists "$REF"; then
    store=/var/lib/containers; [ -d "$store" ] || store=/var/lib
    free=$(( $(df -Pk "$store" | awk 'NR==2{print $4}') / 1048576 ))
    (( free >= WANT_GB )) || { log "ERROR: ${free} GB free, ${WANT_GB} GB needed for $REF"; exit 1; }
    log "pulling $REF"
    podman pull "$REF"
fi
if ! podman container exists "$NAME"; then
    podman create --name "$NAME" --hostname "$NAME" --privileged --systemd=always --network host \
        --volume mios-var:/var --volume mios-containers:/var/lib/containers \
        --volume /etc/mios-cloud/50-cloud.toml:"$DROPIN":ro "$REF" /sbin/init
fi
podman start "$NAME"
log "MiOS is running in container $NAME: sudo podman exec -it $NAME bash -l"
SH
    } > "$startup"
    bash -n "$startup" || die "generated startup script does not parse"
    log "creating $instance ($mtype, ${disk} GB $dtype, $family) in $zone"
    gcloud compute instances create "$instance" --zone "$zone" --machine-type "$mtype" \
        --boot-disk-size "${disk}GB" --boot-disk-type "$dtype" \
        --image-family "$family" --image-project "$iproject" \
        --metadata-from-file startup-script="$startup" "${project_args[@]}"
    log "first boot pulls ${ref} (~23 GB); follow: gcloud compute ssh $instance --zone $zone -- sudo journalctl -u google-startup-scripts -f"
    log "enter: gcloud compute ssh $instance --zone $zone -- sudo podman exec -it $cname bash -l"
    log "native alternative: on that VM, 'bootc install to-existing-root' (or 'bootc switch' from a bootc host) makes ${ref} the booted OS"
}

gce_down() {
    require_root_checkout
    command -v gcloud >/dev/null 2>&1 || die "gcloud is not installed"
    local project
    local project_args=()
    project="${MIOS_DEPLOYMENT_CLOUD_GCE_PROJECT:-$(ssot deployment.cloud.gce project)}"
    [[ -z "$project" ]] || project_args=(--project "$project")
    gcloud compute instances delete "${MIOS_DEPLOYMENT_CLOUD_GCE_INSTANCE:-$(need deployment.cloud.gce instance)}" \
        --zone "${MIOS_DEPLOYMENT_CLOUD_GCE_ZONE:-$(need deployment.cloud.gce zone)}" "${project_args[@]}"
}

main() {
    require_root_checkout
    initialize_state
    local mode="${1:-up}"; shift || true
    local args=()
    for a in "$@"; do
        if [[ "$a" == --builder ]]; then BUILDER=1; else args+=("$a"); fi
    done
    set -- "${args[@]+"${args[@]}"}"
    case "$mode" in
        up)          oracle_podman_socket; up ;;
        enter)       enter "$@" ;;
        status)      require_root_checkout; select_runtime; status ;;
        preflight)   require_root_checkout; select_runtime; preflight ;;
        pull)        require_root_checkout; select_runtime; pull_image "$@" ;;
        overlay)     require_root_checkout; overlay "$@" ;;
        install)     install_launcher ;;
        claude-code) claude_code ;;
        codex)       codex "$@" ;;
        gce-up)      gce_up ;;
        gce-down)    gce_down ;;
        -h|--help)   sed -n '/^#   bootstrap.sh/s/^#   //p' "$SELF" ;;
        *)           die "unknown mode '$mode' (bootstrap.sh --help)" ;;
    esac
}

main "$@"
