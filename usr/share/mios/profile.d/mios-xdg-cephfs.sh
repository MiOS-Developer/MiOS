# AI-hint: Configures Freedesktop XDG directories for CephFS storage fabric, ensuring XDG_CACHE_HOME is isolation-bound to local tmpfs.
# AI-related: /etc/profile.d/mios-xdg-cephfs.sh, [storage.cephfs]
# shellcheck shell=sh

if [ "${MIOS_STORAGE_CEPHFS_ENABLE:-false}" = "true" ]; then
    _uid=$(id -u)
    _raw_path="${MIOS_STORAGE_CEPHFS_XDG_CACHE_HOME_OVERRIDE:-/run/user/{uid}/.cache}"
    XDG_CACHE_HOME=$(echo "$_raw_path" | sed "s/{uid}/$_uid/g")
    export XDG_CACHE_HOME

    export XDG_CONFIG_HOME="${HOME}/.config"
    export XDG_DATA_HOME="${HOME}/.local/share"
    export XDG_STATE_HOME="${HOME}/.local/state"
    export XDG_RUNTIME_DIR="/run/user/$_uid"

    mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" 2>/dev/null
    chmod 700 "$XDG_CACHE_HOME" 2>/dev/null
fi
