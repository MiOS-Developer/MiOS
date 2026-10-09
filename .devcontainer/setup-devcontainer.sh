#!/usr/bin/env bash
# AI-hint: Post-create provisioning for the MiOS devcontainer: clones every [workspace] repo side by side, then applies the root overlay, agent shims and editor projection.
set -euo pipefail

# [workspace] in the SSOT names the root and every MiOS repo; the devcontainer
# opens that root, so a codespace shows all of them, not only the one it cloned.
repo_root="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.."
TOML_GET="${repo_root}/usr/libexec/mios/mios-toml-get"
WORKSPACE_DIR="$(MIOS_TOML_ROOT="${repo_root}" python3 "${TOML_GET}" workspace root)"
[ -n "$WORKSPACE_DIR" ] || { echo "[devcontainer:setup] ERROR: [workspace].root is empty" >&2; exit 1; }
mkdir -p "$WORKSPACE_DIR"

echo "=== [1/6] Syncing the MiOS workspace ([workspace].repos) ==="
while IFS=$'\t' read -r repo url; do
    target="${WORKSPACE_DIR}/${repo}"
    if [ -d "$target/.git" ] || [ -f "$target/.git" ]; then
        echo "  [EXISTS] $target"
    elif [ "$repo" = "-dev-loop" ] && [ -d "$HOME/.dev-loop/.git" ]; then
        echo "  [LINK/SYNC] Existing checkout at $HOME/.dev-loop found"
        ln -sfn "$HOME/.dev-loop" "$target"
    else
        echo "  [CLONE] $repo -> $target"
        # A failed clone leaves a usable codespace; the folder shows as missing until the next create.
        git clone "$url" "$target" || echo "  [WARN] Failed to clone $url"
    fi
done < <(MIOS_TOML_ROOT="${repo_root}" python3 "${TOML_GET}" workspace repos \
            | python3 -c 'import json, sys; print("\n".join(r["name"] + "\t" + r["url"] for r in json.load(sys.stdin)))')

# ~/.dev-loop is where the toolkit's own installer looks. No second link under
# the workspace root: it would show as a duplicate folder in the opened workspace.
if [ -d "${WORKSPACE_DIR}/-dev-loop" ] || [ -L "${WORKSPACE_DIR}/-dev-loop" ]; then
    if [ ! -d "$HOME/.dev-loop" ] || [ -L "$HOME/.dev-loop" ]; then
        ln -sfn "${WORKSPACE_DIR}/-dev-loop" "$HOME/.dev-loop"
    fi
fi

# Enable nested git repository access and recursive submodule hydration
git config --global --add safe.directory "*"
for repo_dir in "${WORKSPACE_DIR}"/*; do
    if [ -d "$repo_dir/.git" ] || [ -f "$repo_dir/.git" ]; then
        (cd "$repo_dir" && git submodule update --init --recursive 2>/dev/null || true)
    fi
done

echo "=== [2/6] Initializing Root Overlay (MiOS System Repo) ==="
# The devcontainer helpers stay in the checkout (the image carries only MiOS);
# mios-agent-pipe-dev is also on the user's PATH as a command.
sudo bash "${WORKSPACE_DIR}/MiOS/.devcontainer/install-root-overlay.sh" || echo "  [WARN] Overlay init completed with warnings"
install -d -m 0755 "$HOME/.local/bin"
printf '#!/bin/sh\nexec bash %s/MiOS/.devcontainer/mios-agent-pipe-dev "$@"\n' "$WORKSPACE_DIR" > "$HOME/.local/bin/mios-agent-pipe-dev"
chmod 0755 "$HOME/.local/bin/mios-agent-pipe-dev"

echo "=== [3/6] Configuring Antigravity Keyring & Shims ==="
# The image bakes agy, so this must NOT be gated on agy being absent: the
# keyring, headless grants and dev-loop skill install are setup-antigravity's
# job too, and that gate skipped all of them on every baked image. Idempotent.
DEVLOOP_ENV="${WORKSPACE_DIR}/-dev-loop/skills/dev-loop/scripts/env"
if [ -r "${DEVLOOP_ENV}/setup-antigravity.sh" ]; then
    bash "${DEVLOOP_ENV}/setup-antigravity.sh" --quiet || echo "  [WARN] Dev-loop setup-antigravity encountered warnings"
fi
if ! command -v agy >/dev/null 2>&1; then
    echo "  [INSTALL] AGY CLI"
    agy_tmp="$(mktemp -d)"
    if bash "${WORKSPACE_DIR}/MiOS/.devcontainer/fetch-installer.sh" \
         https://antigravity.google/cli/install.sh "${agy_tmp}/install.sh" \
       && bash "${agy_tmp}/install.sh"; then
        :
    else
        echo "  [WARN] AGY install failed; install manually when network is available"
    fi
    rm -rf "${agy_tmp}"
fi

echo "=== [4/6] Installing Multi-Harness Shims & Skills ==="
if [ -x "${WORKSPACE_DIR}/-dev-loop/skills/dev-loop/scripts/install.sh" ]; then
    sh "${WORKSPACE_DIR}/-dev-loop/skills/dev-loop/scripts/install.sh" --all --user >/dev/null 2>&1 || true
fi

echo "=== [5/6] Checking Toolchain Readiness ==="
echo "  Rust:    $(rustc --version 2>/dev/null || echo 'missing')"
echo "  Cargo:   $(cargo --version 2>/dev/null || echo 'missing')"
echo "  Python:  $(python3 --version 2>/dev/null || echo 'missing') (compat: $(python3.11 --version 2>/dev/null || echo 'missing'))"
echo "  Podman:  $(podman --version 2>/dev/null || echo 'missing')"
echo "  Docker:  $(docker --version 2>/dev/null || echo 'missing (podman-docker shim)')"
echo "  Claude:  $(claude --version 2>/dev/null || echo 'missing')"
echo "  Agy:     $(agy --version 2>/dev/null || echo 'missing')"
echo "  Gemini:  $(gemini -v 2>/dev/null || gemini --version 2>/dev/null || echo 'missing')"
echo "  Posh:    $(oh-my-posh --version 2>/dev/null || echo 'missing')"
echo "  Fastf:   $(fastfetch --version 2>/dev/null | head -n1 || echo 'missing')"
echo "  Btop:    $(btop --version 2>/dev/null || echo 'missing')"

if ! command -v oh-my-posh >/dev/null 2>&1 && [ -x "${WORKSPACE_DIR}/MiOS/automation/62-oh-my-posh.sh" ]; then
    sudo bash "${WORKSPACE_DIR}/MiOS/automation/62-oh-my-posh.sh" || true
fi
for pkg in btop fastfetch; do
    command -v "$pkg" >/dev/null 2>&1 || sudo dnf install -y "$pkg" 2>/dev/null || sudo apt-get install -y "$pkg" 2>/dev/null || true
done

echo "=== [6/6] Ensuring Frameless Edge-to-Edge VSCode Environment & Dotfiles ==="
VSCODE_CSS_TOOL="${WORKSPACE_DIR}/MiOS/usr/libexec/mios/mios-vscode-custom-css"
[ -f "$VSCODE_CSS_TOOL" ] || VSCODE_CSS_TOOL="/usr/libexec/mios/mios-vscode-custom-css"
CS_CSS="${WORKSPACE_DIR}/MiOS/usr/share/mios/themes/code-server-terminal.css"
[ -f "$CS_CSS" ] || CS_CSS="/usr/share/mios/themes/code-server-terminal.css"
TOML_GET="$(dirname "$VSCODE_CSS_TOOL")/mios-toml-get"
CS_SCROLLBAR_PX="$(python3 "$TOML_GET" --vendor theme.edge code_server_scrollbar_px)"
CS_PERIMETER_PX="$(python3 "$TOML_GET" --vendor theme.edge code_server_perimeter_px)"
# Fatal: a baked code-server that differs from the checked-out stylesheet or [theme.edge] is a stale image.
python3 "$VSCODE_CSS_TOOL" verify --target /usr/lib/code-server/lib/vscode/out/vs/code/browser/workbench/workbench.html --css "$CS_CSS" --scrollbar-px "$CS_SCROLLBAR_PX" --perimeter-px "$CS_PERIMETER_PX"
css_rc=0
python3 "$VSCODE_CSS_TOOL" install --all || css_rc=$?
if [ "$css_rc" -ne 0 ]; then
    echo "[devcontainer:setup] custom-css extension install failed (exit ${css_rc})"
fi

# Ensure SSOT .dotfiles/vscode/settings.json is projected to all IDE targets
DOTFILES_DIR="${WORKSPACE_DIR}/MiOS/.dotfiles"
if [ -d "$DOTFILES_DIR/vscode" ]; then
    for target_dir in "${WORKSPACE_DIR}/MiOS/.vscode" "$HOME/.vscode-server/data/Machine" "$HOME/.vscode-server-insiders/data/Machine" "$HOME/.vscode-remote/data/Machine" "$HOME/.vscode-remote-insiders/data/Machine" "$HOME/.config/Code/User" "$HOME/.config/Code - Insiders/User" "$HOME/.local/share/code-server/User"; do
        mkdir -p "$target_dir" && cp -f "$DOTFILES_DIR/vscode/settings.json" "$target_dir/settings.json" 2>/dev/null || true
    done
fi

for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$rc" ] && ! grep -q "bashrc.d/mios" "$rc"; then
        printf '\n[ -r "${HOME}/.bashrc.d/mios" ] && . "${HOME}/.bashrc.d/mios"\n' >> "$rc"
    fi
done

if [ -f "${WORKSPACE_DIR}/MiOS/usr/libexec/mios/mios-dotfiles" ]; then
    python3 "${WORKSPACE_DIR}/MiOS/usr/libexec/mios/mios-dotfiles" apply || true
elif [ -f "/usr/libexec/mios/mios-dotfiles" ]; then
    python3 "/usr/libexec/mios/mios-dotfiles" apply || true
fi

echo "Multi-repo devcontainer setup complete."


