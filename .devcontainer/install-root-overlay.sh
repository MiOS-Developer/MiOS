#!/bin/bash
# AI-hint: Initializes the MiOS Day-0 root overlay by symlinking the repository tree into the filesystem root (/), keeping sudoers.d root-owned so sudo still works, and seeding the dev user's mios.toml.
# AI-related: .devcontainer/Containerfile, etc/sudoers.d
set -e

if git -C /mios rev-parse --git-dir >/dev/null 2>&1; then
    REPO_ROOT="/mios"
elif git -C /workspaces/MiOS rev-parse --git-dir >/dev/null 2>&1; then
    REPO_ROOT="/workspaces/MiOS"
else
    if git rev-parse --git-dir >/dev/null 2>&1 && [ -f "Justfile" ]; then
        REPO_ROOT=$(pwd)
    else
        echo "Error: \MiOS Repository not found in /mios, /workspaces/MiOS, or current dir"
        exit 1
    fi
fi

echo " Initializing \MiOS COMPLETE System Root Overlay from $REPO_ROOT"

if [ "$(id -u)" -eq 0 ]; then
    ln -sf "${REPO_ROOT}/.git" "/.git"
fi

if [ "$(id -u)" -eq 0 ]; then
    shopt -s dotglob
    for item in "${REPO_ROOT}"/*; do
        [ -e "$item" ] || continue
        name=$(basename "$item")

        case "$name" in
            .devcontainer|proc|sys|dev|run|tmp|boot|mnt|root|vscode|workspaces)
                continue
                ;;
        esac

        target="/$name"

        if [ -d "$item" ]; then
            echo "  [MERGE] /$name"
            mkdir -p "$target"
            cp -as "${item}/"* "$target/" 2>/dev/null || true
        else
            echo "  [FILE] /$name"
            ln -sf "$item" "$target" 2>/dev/null || true
        fi
    done

    chown root:root /etc/sudoers /etc/sudoers.d 2>/dev/null || true
    chmod 0440 /etc/sudoers 2>/dev/null || true
    chmod 0755 /etc/sudoers.d 2>/dev/null || true
    # sudo refuses to run at all if any file in sudoers.d is not owned by root, and
    # the merge above links each one to a file the checkout user owns: one link took
    # sudo down for the whole container. Replace each link with a root-owned 0440
    # copy of the same content, and drop any that visudo rejects.
    for f in /etc/sudoers.d/*; do
        [ -L "$f" ] || continue
        src=$(readlink -f "$f") || continue
        rm -f "$f"
        install -o root -g root -m 0440 "$src" "$f" 2>/dev/null || continue
        visudo -cf "$f" >/dev/null 2>&1 || { echo "  [DROP] $f: visudo rejected it"; rm -f "$f"; }
    done
else
    echo "  [SKIP] Root overlay merge requires root privileges; using user-level provisioning only"
fi

TARGET_USER="${SUDO_USER:-${MIOS_TARGET_USER:-${USER:-mios-dev}}}"
if [ "$(id -u)" -eq 0 ]; then
    if ! id -u "$TARGET_USER" >/dev/null 2>&1; then
        useradd -m -s /bin/bash "$TARGET_USER"
        echo "$TARGET_USER ALL= NOPASSWD:ALL" > "/etc/sudoers.d/${TARGET_USER}"
        chmod 0440 "/etc/sudoers.d/${TARGET_USER}"
    fi
fi

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_CONFIG_DIR="${TARGET_HOME}/.config/mios"
TARGET_DATA_DIR="${TARGET_HOME}/.local/share/mios"
TARGET_CACHE_DIR="${TARGET_HOME}/.cache/mios"
TARGET_STATE_DIR="${TARGET_HOME}/.local/state/mios"

install -d -m 0755 "$TARGET_HOME/.ssh" "$TARGET_HOME/.config" "$TARGET_HOME/.local/share" "$TARGET_HOME/.cache" "$TARGET_HOME/.local/state"
install -d -m 0755 "$TARGET_CONFIG_DIR/credentials/ssh-keys" "$TARGET_DATA_DIR/artifacts" "$TARGET_DATA_DIR/images" "$TARGET_DATA_DIR/templates" "$TARGET_DATA_DIR/plugins" "$TARGET_CACHE_DIR/podman" "$TARGET_CACHE_DIR/downloads" "$TARGET_CACHE_DIR/build-cache" "$TARGET_STATE_DIR/logs"

HOSTNAME_VALUE="$(hostname 2>/dev/null || uname -n 2>/dev/null || echo "$TARGET_USER")"
if [ ! -e "${TARGET_CONFIG_DIR}/mios.toml" ]; then
cat > "${TARGET_CONFIG_DIR}/mios.toml" <<EOF
[user]
name = "${TARGET_USER}"
hostname = "${HOSTNAME_VALUE}"
EOF
fi

if [ "$(id -u)" -eq 0 ]; then
    chown -R "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/.config" "$TARGET_HOME/.local" "$TARGET_HOME/.cache" "$TARGET_HOME/.local/state" 2>/dev/null || true
fi
