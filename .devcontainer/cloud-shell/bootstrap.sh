#!/usr/bin/env bash
# AI-hint: The one MiOS cloud-environment script: runs the devcontainer ([image].ref plus wiring) under podman or docker for Claude Code cloud, Codex cloud and Cloud Shell, creates a GCE VM that boots the image, preflights disk, pulls and applies the [deployment.cloud] overlay.
# AI-related: .devcontainer/Containerfile, .devcontainer/devcontainer.json, .devcontainer/artifact-builder/devcontainer.json, .devcontainer/cloud-shell/claude-code-cloud.sh, .devcontainer/cloud-shell/codex-cloud.sh, usr/share/mios/mios.toml
# AI-functions: main, preflight, pull_image, status, up, enter, overlay, install_tailscale, tailnet, install_ssh_client, ssh_ca_bin, ssh_cert, render_dropin, gce_ignition, gce_up, claude_code, codex
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
#   bootstrap.sh tailnet               this host's own tailnet node (userspace, mesh only)
#   bootstrap.sh ssh-cert              this host's [ssh.ca] certificate login to the Blades (after tailnet)
#   bootstrap.sh claude-code           Claude Code cloud setup (claude-code-cloud.sh)
#   bootstrap.sh codex install|start|check   Codex cloud (codex-cloud.sh)
#   bootstrap.sh gce-up | gce-down     a Compute Engine VM that boots the image (Fedora CoreOS + bootc switch)
#   bootstrap.sh gce-ignition          the Ignition config gce-up passes as user-data
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
        local tool="${MIOS_TOML_GET:-$ROOT/usr/libexec/mios/mios-toml-get}"
        [[ -x "$tool" ]] || die "MiOS TOML getter must be staged before cloud setup"
        MIOS_TOML_ROOT="$ROOT" "$tool" "$@"
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

# The devcontainer account: build.args.MIOS_IDENTITY_USERNAME, projected from
# [identity].username. devcontainer.json names no remoteUser; the image's USER is
# the account its build created from this argument.
dc_user() {
    python3 - "$CONFIG" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
build = doc.get("build") if isinstance(doc.get("build"), dict) else {}
args = build.get("args") if isinstance(build.get("args"), dict) else {}
print(args.get("MIOS_IDENTITY_USERNAME", ""))
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
# The verdict is recorded in $STATE/preflight.failed (removed when it passes), so
# `status` -- and the `mios-dev` wrapper -- can say why the image never arrived
# long after the setup script that ran this has exited.
preflight() {
    local ref want store free why
    ref="$(need image ref)"
    want=0
    image_present "$ref" || want="$(need deployment.cloud min_free_gb)"
    [[ "$BUILDER" == 1 ]] && want=$((want + $(need deployment.cloud build_free_gb)))
    store="$(store_path)" || die "$RT cannot report its storage path"
    free=$(( $(free_kb "$store") / 1048576 ))
    install -d -m 0755 "$STATE"
    if (( free < want )); then
        why="${free} GB free in ${store} (the ${RT} store), ${want} GB needed for ${ref}$([[ "$BUILDER" == 1 ]] && echo ' and a nested build') ([deployment.cloud].min_free_gb$([[ "$BUILDER" == 1 ]] && echo ' + build_free_gb')). $(platform_hint)"
        printf '%s\n' "$why" > "$STATE/preflight.failed"
        die "$why"
    fi
    rm -f "$STATE/preflight.failed"
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

# 0 ready, 3 not yet (pulling or not started), 1 failed (the pull, or the preflight
# that stopped it: this host cannot hold the image until the recorded cause is gone).
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
        if [[ -f "$STATE/preflight.failed" ]]; then
            printf 'MiOS image %s is not pulled: the preflight failed: %s\nWhen that is fixed, run: bash %s up\n' \
                "$ref" "$(cat "$STATE/preflight.failed")" "$SELF"
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

# An SSOT section (JSON on stdin) rendered as a mios.toml drop-in: the scalar keys of
# each table, nested tables as dotted headers. $1 names the section, $2 is a note.
render_dropin() {
    python3 -c '
import json, sys
data = json.load(sys.stdin)
if not data or not all(isinstance(v, dict) for v in data.values()):
    sys.exit("mios.toml [" + sys.argv[1] + "] must hold tables of SSOT keys")
print("# GENERATED from mios.toml [" + sys.argv[1] + "] by .devcontainer/cloud-shell/bootstrap.sh;")
print("# " + sys.argv[2])
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
' "$1" "$2"
}

# [deployment.cloud].overlay as TOML: the host drop-in that turns runtime model
# pulls, runtime desktop installs and the desktop session off without touching
# the image. --apply writes it to [deployment.cloud].dropin (root).
overlay() {
    local text dropin
    text="$(ssot --section deployment.cloud.overlay | render_dropin deployment.cloud.overlay \
        "a cloud/container deployment of the MiOS image. Delete it to restore the vendor behaviour.")" \
        || die "could not render [deployment.cloud.overlay]"
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
    local ref dev name user wsf wsroot primary uid gid repo src ca line target canonical selected_image container_image
    require_root_checkout; select_runtime; select_config
    ref="$(need image ref)"; dev="$(need image dev_local_tag)"; name="$(container_name)"
    user="$(dc_user)"; wsf="$(dc_json workspaceFolder)"
    canonical="$(ssot --vendor identity username)"
    [[ -n "$user" && "$user" != root && -n "$wsf" ]] || die "$CONFIG needs a non-root build.args.MIOS_IDENTITY_USERNAME and workspaceFolder"
    [[ "$user" == "$canonical" ]] || die "$CONFIG build.args.MIOS_IDENTITY_USERNAME differs from [identity].username; re-project the devcontainer before rebuilding"
    preflight
    pull_image
    "$RT" build --build-arg "MIOS_IMAGE_REF=$ref" --build-arg "MIOS_IDENTITY_USERNAME=$user" \
        -f "$ROOT/.devcontainer/Containerfile" -t "$dev" "$ROOT/.devcontainer" >&2
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
    selected_image="$("$RT" image inspect --format '{{.Id}}' "$dev")"
    container_image="$("$RT" container inspect --format '{{.Image}}' "$name")"
    [[ "${selected_image#sha256:}" == "${container_image#sha256:}" ]] \
        || die "existing container $name uses an older image; preserve its state and recreate it from $dev (workspace bind mounts remain intact)"
    "$RT" start "$name" >/dev/null
    container_account "$name" "$user"
    install -d -m 0755 "$STATE"
    rm -f "$STATE/lifecycle.failed"
    if [[ ! -f "$STATE/lifecycle.created" ]]; then
        lifecycle postCreateCommand && touch "$STATE/lifecycle.created"
    fi
    lifecycle postStartCommand
    status
}

# Validate through the same non-root runtime selection used by attachment. No
# root execution or post-create repair can substitute for this account existing.
container_account() {
    local name="$1" user="$2" uid
    [[ -n "$user" && "$user" != root && "$user" == "$(ssot --vendor identity username)" ]] \
        || die "configured devcontainer account is not the canonical non-root identity; re-project before attachment"
    uid="$("$RT" exec -u "$user" "$name" id -u)" \
        || die "container $name cannot select canonical user $user; rebuild the devcontainer, not the recovery image"
    [[ "$uid" == 1000 ]] || die "container $name canonical developer account is not UID 1000; rebuild its account wiring"
}

lifecycle() {
    local cmd user wsf
    cmd="$(dc_json "$1")"; [[ -n "$cmd" ]] || return 0
    user="$(dc_user)"; wsf="$(dc_json workspaceFolder)"
    container_account "$(container_name)" "$user"
    log "$1 as $user in $wsf: $cmd"
    # A Tailscale pre-auth key reaches the lifecycle (mios-tailscale-sync enrolls
    # with it) by NAME only: `exec -e NAME` copies the value from this process's
    # environment, so it is in no argv and not in the stored container config.
    local key_env=() name
    if [[ -z "${MIOS_TAILSCALE_AUTHKEY:-}" && -n "${TS_AUTHKEY_FILE:-}" && -r "${TS_AUTHKEY_FILE}" ]]; then
        MIOS_TAILSCALE_AUTHKEY="$(<"$TS_AUTHKEY_FILE")"; export MIOS_TAILSCALE_AUTHKEY
    fi
    for name in MIOS_TAILSCALE_AUTHKEY TS_AUTHKEY; do
        [[ -n "${!name:-}" ]] && key_env+=(-e "$name")
    done
    if ! "$RT" exec "${key_env[@]}" -u "$user" -w "$wsf" "$(container_name)" bash -lc "$cmd"; then
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
    container_account "$name" "$(dc_user)"
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
    exec "$RT" exec "${tty[@]}" -u "$(dc_user)" -w "$wd" "$name" "$@"
}

wrapper_path() { printf '%s\n' "${MIOS_DEPLOYMENT_CLOUD_WRAPPER:-$(need deployment.cloud wrapper)}"; }

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

# tailscale/tailscaled from the upstream repository for this host's distro
# ([deployment.cloud.tailnet]; apt fills {id}/{codename} from /etc/os-release).
install_tailscale() {
    command -v tailscale >/dev/null 2>&1 && command -v tailscaled >/dev/null 2>&1 && return 0
    [[ $EUID -eq 0 ]] || die "tailscale is missing and installing it needs root"
    log "installing tailscale"
    if command -v apt-get >/dev/null 2>&1; then
        local id codename keyring list
        id="$(. /etc/os-release && printf '%s' "${ID:-}")"
        codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
        [[ "$id" =~ ^[a-z0-9.-]+$ && "$codename" =~ ^[a-z0-9.-]+$ ]] \
            || die "/etc/os-release names no ID/VERSION_CODENAME for the tailscale apt repository"
        keyring="$(need deployment.cloud.tailnet apt_keyring)"; list="$(need deployment.cloud.tailnet apt_list)"
        keyring="${keyring//\{id\}/$id}"; keyring="${keyring//\{codename\}/$codename}"
        list="${list//\{id\}/$id}"; list="${list//\{codename\}/$codename}"
        curl -fsSL "$keyring" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
        curl -fsSL "$list" -o /etc/apt/sources.list.d/tailscale.list
        apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q tailscale
    elif command -v dnf >/dev/null 2>&1; then
        local repo
        repo="$(need deployment.cloud.tailnet dnf_repo)"
        dnf config-manager addrepo --from-repofile="$repo" 2>/dev/null || dnf config-manager --add-repo "$repo"
        dnf install -y tailscale
    else
        die "no apt-get or dnf to install tailscale"
    fi
}

# The tailnet node of a host that cannot hold the image (a cloud session): a
# userspace tailscaled on its own state directory and socket, enrolled and kept in
# line by the image's own mios-tailscale-sync --mesh-only, which reads the vendor
# SSOT plus the [deployment.cloud.overlay] drop-in as its host tier. Logged out,
# it mints a one-use key ([tailscale.auth_key_mint]) through the egress proxy.
# Idempotent: a running tailscaled is kept and its node only re-synced.
tailnet() {
    require_root_checkout
    [[ $EUID -eq 0 ]] || die "tailnet runs tailscaled and needs root"
    install_tailscale
    local dir sock socks
    dir="$STATE/tailscale"; sock="$dir/tailscaled.sock"
    # A unix socket path holds at most 107 bytes; past that tailscaled cannot bind it.
    (( ${#sock} <= 107 )) || die "$sock is longer than a unix socket path allows; shorten [deployment.cloud].state_directory"
    install -d -m 0700 "$dir" "$dir/mios.d"
    overlay > "$dir/mios.d/50-cloud.toml.new"
    mv -f "$dir/mios.d/50-cloud.toml.new" "$dir/mios.d/50-cloud.toml"
    socks="$(ssot deployment.cloud.overlay.tailscale socks5_server)"
    if ! { [[ -f "$dir/tailscaled.pid" ]] && kill -0 "$(<"$dir/tailscaled.pid")" 2>/dev/null; }; then
        rm -f "$sock"
        setsid nohup tailscaled --tun=userspace-networking ${socks:+"--socks5-server=$socks"} \
            --statedir="$dir" --socket="$sock" --port=0 >> "$dir/tailscaled.log" 2>&1 < /dev/null &
        echo $! > "$dir/tailscaled.pid"
        local _
        for _ in $(seq 50); do [[ -S "$sock" ]] && break; sleep 0.2; done
        [[ -S "$sock" ]] || die "tailscaled did not open $sock (log: $dir/tailscaled.log)"
        log "tailscaled started (state $dir)"
    fi
    MIOS_VENDOR_TOML="$ROOT/usr/share/mios/mios.toml" MIOS_HOST_TOML="$dir/no-host.toml" \
    MIOS_HOST_TOML_D="$dir/mios.d" \
        python3 "$ROOT/usr/libexec/mios/mios-tailscale-sync" --mesh-only "--socket=$sock"
}

# ssh and ssh-keygen from the distro ([deployment.cloud.ssh] names the package).
install_ssh_client() {
    command -v ssh >/dev/null 2>&1 && command -v ssh-keygen >/dev/null 2>&1 && return 0
    [[ $EUID -eq 0 ]] || die "the OpenSSH client is missing and installing it needs root"
    log "installing the OpenSSH client"
    if command -v apt-get >/dev/null 2>&1; then
        local pkg; pkg="$(need deployment.cloud.ssh apt_package)"
        apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$pkg"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y "$(need deployment.cloud.ssh dnf_package)"
    else
        die "no apt-get or dnf to install the OpenSSH client"
    fi
}

# The mios-ssh-ca client: the selected native catalog, else the installed image
# path, else built from this checkout for the host's own target (a cloud session
# has cargo but no MiOS image yet). Prints its path.
ssh_ca_bin() {
    local c
    for c in "${MIOS_NATIVE_BIN_DIR:+$MIOS_NATIVE_BIN_DIR/mios-ssh-ca}" /usr/libexec/mios/mios-ssh-ca; do
        [[ -n "$c" && -x "$c" ]] && { printf '%s\n' "$c"; return 0; }
    done
    command -v cargo >/dev/null 2>&1 || die "no mios-ssh-ca binary and no cargo to build one"
    local host target="$STATE/native-target"
    host="$(rustc -vV | sed -n 's/^host: //p')"
    [[ -n "$host" ]] || die "rustc names no host target"
    log "building mios-ssh-ca for $host"
    cargo build -q --release --locked --manifest-path "$ROOT/tools/native/Cargo.toml" \
        -p mios-ssh-ca --target "$host" --target-dir "$target" >&2 \
        || die "building mios-ssh-ca failed"
    printf '%s\n' "$target/$host/release/mios-ssh-ca"
}

# This host's certificate login to every Blade ([ssh.ca]): mios-ssh-ca keeps the
# node's own key under $STATE/ssh, asks [ssh.ca].signer over the tailnet node's
# socket, installs the certificate and writes an ssh_config that renews it
# before each login. The [deployment.cloud.ssh].config_dropin Includes that
# ssh_config, so `ssh <signer>` just works. Resolves the SSOT like `tailnet`.
ssh_cert() {
    require_root_checkout
    install_ssh_client
    local ts="$STATE/tailscale" dir="$STATE/ssh" bin dropin
    [[ -S "$ts/tailscaled.sock" ]] || die "no tailnet node at $ts (run bootstrap.sh tailnet first)"
    bin="$(ssh_ca_bin)"
    install -d -m 0700 "$dir"
    MIOS_VENDOR_TOML="$ROOT/usr/share/mios/mios.toml" MIOS_HOST_TOML="$ts/no-host.toml" \
    MIOS_HOST_TOML_D="$ts/mios.d" \
        "$bin" request --dir "$dir" --socket "$ts/tailscaled.sock"
    dropin="$(need deployment.cloud.ssh config_dropin)"
    if [[ $EUID -eq 0 && -d "$(dirname "$dropin")" ]]; then
        printf '# GENERATED by %s ssh-cert: the MiOS user CA login to the Blades.\nInclude %s/ssh_config\n' \
            "$SELF" "$dir" > "$dropin.new"
        mv -f "$dropin.new" "$dropin"
        log "ssh_config: $dropin includes $dir/ssh_config"
    else
        log "ssh_config: use ssh -F $dir/ssh_config"
    fi
}

# Claude Code cloud (claude-code-cloud.sh): the setup script must finish in about
# five minutes, so it only provisions and starts the pull; the first `mios-dev`
# reports progress until the image is in, then brings the container up.
# Degrades open: the wrapper is installed BEFORE anything that can fail, so a
# session whose disk cannot hold the image (the preflight) still has a `mios-dev`
# whose `status` names the cause. Measured: with the wrapper written last, a
# failed preflight left no `mios-dev` at all and the session read as having no
# MiOS and no container runtime.
claude_code() {
    require_root_checkout
    install_podman
    select_runtime
    BUILDER="${MIOS_DEPLOYMENT_CLOUD_BUILDER:-$(need deployment.cloud builder)}"; select_config
    install -d -m 0755 "$STATE"
    printf '%s\n' "$ROOT" > "$STATE/source-root"
    write_wrapper "$(wrapper_path)"
    # A subshell: a node that cannot join (no API token saved yet) never stops the setup.
    ( tailnet ) || log "tailnet not joined; every session start retries it (bootstrap.sh tailnet)"
    # The certificate may need a build of mios-ssh-ca: in the background, off the setup budget.
    if [[ -S "$STATE/tailscale/tailscaled.sock" ]]; then
        setsid nohup bash "$SELF" ssh-cert >> "$STATE/ssh-cert.log" 2>&1 < /dev/null &
        log "ssh-cert started in the background (log: $STATE/ssh-cert.log)"
    fi
    # A subshell: a failed preflight is recorded for `status`, and the setup goes on.
    if ( preflight ); then
        pull_image --background
    else
        log "the MiOS image is not pulled on this host; the cause is kept for: mios-dev status"
    fi
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
            write_wrapper "$(wrapper_path)"
            codex check ;;
        start)
            select_runtime; select_config; up
            "$RT" exec -u "$(dc_user)" "$(container_name)" bash -lc \
                "bash $(need workspace root)/$(need workspace primary)/.devcontainer/mios-agent-pipe-dev start && /usr/lib/mios/mcp/.venv/bin/python3 /usr/libexec/mios/mios-mcp-server --project-agent-clients"
            codex check ;;
        check)
            select_runtime; select_config
            "$RT" exec -u "$(dc_user)" -e "WORKSPACE_ROOT=$(need workspace root)/$(need workspace primary)" \
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

# The Ignition config of a gce-up node, on stdout. Fedora CoreOS applies it on its
# first boot: the core login's keys, the two host drop-ins, the one-use tailnet key
# (0600), Zincati masked (it would update the CoreOS this node is leaving), a
# first-boot unit that switches the node to [image].ref and reboots into it, and a
# unit that prints os-release and `bootc status` to the serial console on every boot.
gce_ignition() {
    require_root_checkout
    local ref spec user keys tskey nodekey secret authfile stamp cloud_dropin gce_dropin
    ref="$(need image ref)"
    spec="$(need deployment.cloud.gce ignition_version)"
    user="$(need deployment.cloud.gce ssh_user)"
    keys="${MIOS_DEPLOYMENT_CLOUD_GCE_SSH_AUTHORIZED_KEYS_FILE:-$(ssot deployment.cloud.gce ssh_authorized_keys_file)}"
    tskey="${MIOS_DEPLOYMENT_CLOUD_GCE_TAILSCALE_AUTH_KEY_FILE:-$(ssot deployment.cloud.gce tailscale_auth_key_file)}"
    nodekey="$(need deployment.cloud.gce node_auth_key_file)"
    secret="${MIOS_DEPLOYMENT_CLOUD_GCE_PULL_SECRET:-$(ssot deployment.cloud.gce pull_secret)}"
    authfile="$(need deployment.cloud.gce auth_file)"
    stamp="$(need deployment.cloud.gce rebase_stamp)"
    cloud_dropin="$(need deployment.cloud dropin)"; gce_dropin="$(need deployment.cloud.gce dropin)"
    [[ -z "$keys" || -r "$keys" ]] || die "ssh_authorized_keys_file $keys is not readable"
    if [[ -n "$tskey" ]]; then
        [[ -f "$tskey" && -r "$tskey" ]] || die "tailscale_auth_key_file $tskey is not a readable file"
        [[ "$(stat -c %a "$tskey")" == 600 ]] || die "tailscale_auth_key_file $tskey must be mode 0600"
    fi
    [[ -z "$secret" || "$secret" =~ ^[A-Za-z0-9_-]+$ ]] || die "pull_secret '$secret' is not a Secret Manager secret id"
    local cloud gce
    cloud="$(overlay)"
    gce="$(ssot --section deployment.cloud.gce.overlay | render_dropin deployment.cloud.gce.overlay \
        "the Compute Engine node of the MiOS image, layered after the cloud overlay.")" \
        || die "could not render [deployment.cloud.gce.overlay]"
    if [[ -n "$tskey" ]]; then
        gce+=$'\n\n# The copied one-use key; removed once this node runs on the tailnet.\n[tailscale]\nauth_key_file = '"$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$nodekey")"
    fi
    local rebase
    rebase="$(
        printf '#!/bin/bash\n# GENERATED by MiOS bootstrap.sh gce-up from mios.toml [image] and [deployment.cloud.gce].\nset -euo pipefail\n'
        printf 'REF=%q\nAUTH_FILE=%q\nPULL_SECRET=%q\nSTAMP=%q\n' "$ref" "$authfile" "$secret" "$stamp"
        cat <<'SH'
log() { echo "[mios-gce] $*"; }
if [[ -n "$PULL_SECRET" && ! -s "$AUTH_FILE" ]]; then
    # The registry credential comes from Secret Manager with this VM's service-account
    # token; it is never in instance metadata, a log or a command line.
    md=http://metadata.google.internal/computeMetadata/v1
    token="$(curl -fsS -H 'Metadata-Flavor: Google' "$md/instance/service-accounts/default/token" | jq -r .access_token)"
    project="$(curl -fsS -H 'Metadata-Flavor: Google' "$md/project/project-id")"
    install -d -m 0700 "$(dirname "$AUTH_FILE")"
    umask 077
    curl -fsS -H @- "https://secretmanager.googleapis.com/v1/projects/$project/secrets/$PULL_SECRET/versions/latest:access" \
        <<<"Authorization: Bearer $token" | jq -er .payload.data | base64 -d > "$AUTH_FILE.new"
    unset token
    chmod 0600 "$AUTH_FILE.new"
    mv -f "$AUTH_FILE.new" "$AUTH_FILE"
    log "registry credential for $REF fetched from Secret Manager secret $PULL_SECRET"
fi
if command -v bootc >/dev/null 2>&1 && bootc switch "$REF"; then
    log "bootc switched to $REF"
else
    log "bootc is absent or refused; rebasing with rpm-ostree"
    rpm-ostree rebase "ostree-unverified-registry:$REF"
fi
install -d -m 0755 "$(dirname "$STAMP")"
printf '%s\n' "$REF" > "$STAMP"
log "rebooting into MiOS ($REF)"
systemctl reboot
SH
    )"
    bash -n <<<"$rebase" || die "generated rebase script does not parse"
    MIOS_IGN_SPEC="$spec" MIOS_IGN_USER="$user" MIOS_IGN_KEYS="$keys" MIOS_IGN_TSKEY="$tskey" \
    MIOS_IGN_NODEKEY="$nodekey" MIOS_IGN_STAMP="$stamp" MIOS_IGN_REBASE="$rebase" \
    MIOS_IGN_CLOUD_DROPIN="$cloud_dropin" MIOS_IGN_CLOUD="$cloud" \
    MIOS_IGN_GCE_DROPIN="$gce_dropin" MIOS_IGN_GCE="$gce" python3 - <<'PY'
import base64, json, os, sys
E = os.environ
def data(text, mode, path):
    return {"path": path, "mode": mode, "overwrite": True,
            "contents": {"source": "data:;base64," + base64.b64encode(text.encode()).decode()}}
keys = []
if E["MIOS_IGN_KEYS"]:
    with open(E["MIOS_IGN_KEYS"], encoding="utf-8") as f:
        keys = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    for k in keys:
        if not k.startswith(("ssh-", "ecdsa-", "sk-")) or len(k.split()) < 2:
            sys.exit("ssh_authorized_keys_file holds a line that is not a public key")
files = [data(E["MIOS_IGN_CLOUD"] + "\n", 0o644, E["MIOS_IGN_CLOUD_DROPIN"]),
         data(E["MIOS_IGN_GCE"] + "\n", 0o644, E["MIOS_IGN_GCE_DROPIN"]),
         data(E["MIOS_IGN_REBASE"] + "\n", 0o755, "/etc/mios-cloud/rebase.sh")]
dirs = [{"path": "/etc/mios-cloud", "mode": 0o755}]
node_key = E["MIOS_IGN_NODEKEY"]
units = [
    {"name": "zincati.service", "mask": True},
    {"name": "mios-cloud-rebase.service", "enabled": True, "contents": "\n".join([
        "[Unit]",
        "Description=Switch this node to the MiOS image and reboot into it",
        "Wants=network-online.target",
        "After=network-online.target",
        "ConditionPathExists=!" + E["MIOS_IGN_STAMP"],
        "StartLimitIntervalSec=0",
        "[Service]",
        "Type=oneshot",
        "ExecStart=/bin/bash /etc/mios-cloud/rebase.sh",
        "Restart=on-failure",
        "RestartSec=60",
        "StandardOutput=journal+console",
        "StandardError=journal+console",
        "[Install]",
        "WantedBy=multi-user.target", ""])},
    {"name": "mios-cloud-report.service", "enabled": True, "contents": "\n".join([
        "[Unit]",
        "Description=Print the booted OS and its bootc deployment to the serial console",
        "After=network-online.target",
        "[Service]",
        "Type=oneshot",
        "ExecStart=/bin/sh -c 'cat /etc/os-release; bootc status 2>&1 || rpm-ostree status'",
        "StandardOutput=journal+console",
        "StandardError=journal+console",
        "[Install]",
        "WantedBy=multi-user.target", ""])},
]
if E["MIOS_IGN_TSKEY"]:
    with open(E["MIOS_IGN_TSKEY"], encoding="utf-8") as f:
        key = f.read().strip()
    if not key.startswith("tskey-") or any(c.isspace() for c in key):
        sys.exit("tailscale_auth_key_file does not hold one tskey- pre-auth key")
    dirs.append({"path": os.path.dirname(node_key), "mode": 0o700})
    files.append(data(key + "\n", 0o600, node_key))
    units.append({"name": "mios-cloud-authkey-cleanup.service", "enabled": True, "contents": "\n".join([
        "[Unit]",
        "Description=Remove the spent one-use tailnet key once this node runs on the tailnet",
        "After=mios-tailscale-sync.service",
        "ConditionPathExists=" + node_key,
        "[Service]",
        "Type=oneshot",
        "ExecStart=/bin/sh -c 'tailscale status --json --peers=false 2>/dev/null"
        " | grep -Eq \"\\\"BackendState\\\": *\\\"Running\\\"\" && rm -f -- " + node_key + " || :'",
        "[Install]",
        "WantedBy=mios-tailscale-sync.service", ""])})
user = {"name": E["MIOS_IGN_USER"]}
if keys:
    user["sshAuthorizedKeys"] = keys
doc = {"ignition": {"version": E["MIOS_IGN_SPEC"]},
       "passwd": {"users": [user]},
       "storage": {"directories": dirs, "files": files},
       "systemd": {"units": units}}
json.dump(doc, sys.stdout, indent=1, sort_keys=True)
print()
PY
}

gce_up() {
    require_root_checkout
    command -v gcloud >/dev/null 2>&1 || die "gcloud is not installed (https://cloud.google.com/sdk/docs/install)"
    local instance zone mtype disk dtype family iproject ref want ign project secret
    local project_args=() scope_args=()
    project="${MIOS_DEPLOYMENT_CLOUD_GCE_PROJECT:-$(ssot deployment.cloud.gce project)}"
    [[ -z "$project" ]] || project_args=(--project "$project")
    instance="${MIOS_DEPLOYMENT_CLOUD_GCE_INSTANCE:-$(need deployment.cloud.gce instance)}"
    zone="${MIOS_DEPLOYMENT_CLOUD_GCE_ZONE:-$(need deployment.cloud.gce zone)}"
    mtype="${MIOS_DEPLOYMENT_CLOUD_GCE_MACHINE_TYPE:-$(need deployment.cloud.gce machine_type)}"
    disk="${MIOS_DEPLOYMENT_CLOUD_GCE_BOOT_DISK_GB:-$(need deployment.cloud.gce boot_disk_gb)}"
    dtype="$(need deployment.cloud.gce boot_disk_type)"
    family="$(need deployment.cloud.gce image_family)"; iproject="$(need deployment.cloud.gce image_project)"
    secret="${MIOS_DEPLOYMENT_CLOUD_GCE_PULL_SECRET:-$(ssot deployment.cloud.gce pull_secret)}"
    ref="$(need image ref)"
    # The booted deployment and a staged update each hold the unpacked image.
    want=$(( 2 * $(need deployment.cloud min_free_gb) ))
    (( disk >= want )) || die "boot disk ${disk} GB is below the ${want} GB two deployments of ${ref} need"
    # Secret Manager needs the cloud-platform scope on the VM's service account.
    [[ -z "$secret" ]] || scope_args=(--scopes cloud-platform)
    ign="$(mktemp)"
    # Cleared by the trap itself: a RETURN trap outlives the local it names.
    trap 'rm -f -- "${ign:-}"; trap - RETURN' RETURN
    gce_ignition > "$ign" || die "could not render the Ignition config"
    log "creating $instance ($mtype, ${disk} GB $dtype, $iproject/$family) in $zone; first boot switches it to $ref"
    gcloud compute instances create "$instance" --zone "$zone" --machine-type "$mtype" \
        --boot-disk-size "${disk}GB" --boot-disk-type "$dtype" \
        --image-family "$family" --image-project "$iproject" \
        --metadata-from-file user-data="$ign" "${scope_args[@]}" "${project_args[@]}"
    log "first boot pulls ${ref} (~23 GB), then reboots into MiOS; follow the serial console:"
    log "  gcloud compute instances get-serial-port-output $instance --zone $zone ${project_args[*]}"
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
        tailnet)     tailnet ;;
        ssh-cert)    ssh_cert ;;
        claude-code) claude_code ;;
        codex)       codex "$@" ;;
        gce-ignition) gce_ignition ;;
        gce-up)      gce_up ;;
        gce-down)    gce_down ;;
        -h|--help)   sed -n '/^#   bootstrap.sh/s/^#   //p' "$SELF" ;;
        *)           die "unknown mode '$mode' (bootstrap.sh --help)" ;;
    esac
}

main "$@"
