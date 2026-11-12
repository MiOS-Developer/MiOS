#!/usr/bin/env bash
# AI-hint: Builds the mios-agents image from the repository and runs it as the development super-container with the workspace mounted.
set -euo pipefail
cd "$(dirname "$0")"                              # usr/share/mios/agents
REPO_SRC="$(cd ../../../.. && pwd)"               # repo root
IMG=localhost/mios-agents:dev
NAME=mios-agents-dev
PORT="${MIOS_AGENTS_PORT:-8800}"                  # IDE port (takes over the mios-agents endpoint)
PASS="${MIOS_AGENTS_PASSWORD:?set MIOS_AGENTS_PASSWORD (the dev IDE login -- there is no default)}"
WORK="${MIOS_FRONTIER_WORKSPACE:-$HOME/MiOS}"     # NATIVE ext4 workspace (container-writable)

echo ">> build $IMG from the repo"
sudo podman build --network=host -t "$IMG" -f Containerfile .

if [ ! -e "$WORK/.git" ]; then
  echo ">> seed native workspace $WORK from $REPO_SRC"
  mkdir -p "$WORK"
  rsync -a \
    --exclude="__pycache__" --exclude="*.pyc" --exclude="node_modules" --exclude=".venv" \
    --exclude="output/" --exclude="*.tar" --exclude="*.tar.gz" --exclude="*.qcow2" \
    --exclude="*.iso" --exclude="*.vhdx" --exclude="*.raw" --exclude="*.wsl" \
    "$REPO_SRC"/ "$WORK"/
else
  echo ">> native workspace $WORK exists"
fi
chmod -R a+rwX "$WORK"

echo ">>start $NAME on :$PORT"
sudo systemctl stop mios-agents 2>/dev/null || true
sudo podman rm -f "$NAME" 2>/dev/null || true; podman rm -f "$NAME" 2>/dev/null || true
sudo chmod -R a+rwX "$WORK"
mounts=(-v "$WORK":/mnt/mios-root:rw)
[ -d "$HOME/.gemini" ]      && mounts+=(-v "$HOME/.gemini":/home/coder/.gemini:rw)
[ -d "$HOME/.claude" ]      && mounts+=(-v "$HOME/.claude":/home/coder/.claude:rw)
[ -f "$HOME/.claude.json" ] && mounts+=(-v "$HOME/.claude.json":/home/coder/.claude.json:rw)
sudo podman run -d --name "$NAME" --network=host -e PASSWORD="$PASS" \
  "${mounts[@]}" "$IMG" --bind-addr "0.0.0.0:$PORT" /mnt/mios-root

cat <<EOF

mios-agents-dev up -- war-room on a NATIVE writable workspace synced to GitHub.
  IDE:          http://localhost:$PORT   (password: $PASS)
  Workspace:    $WORK   (origin: $(git -C "$WORK" remote get-url origin 2>/dev/null || echo '?'))
  Gemini login: sudo podman exec -it $NAME agy
  Claude login: sudo podman exec -it $NAME claude
  Doctor:       sudo podman exec -it $NAME mios-a2o doctor
  WAR ROOM:     sudo podman exec -it $NAME mios-frontier
  Sync:         sudo podman exec -it $NAME mios-frontier-sync push|pull   (<-> GitHub origin)
EOF
