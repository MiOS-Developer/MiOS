#!/usr/bin/env bash
# AI-hint: Consolidated profile.d initialization for sh/bash (folded per ADR-0030 task_3659).
# AI-doc: usr/share/doc/mios/manual/root.md

# ============================================================================
# Native compositor startup uses the SSOT device once, avoiding alias duplicates.
if command -v mios-toml-get >/dev/null 2>&1; then
    _mios_drm_device="$(mios-toml-get desktop.hyprland.environment AQ_DRM_DEVICES "" 2>/dev/null)"
    if [ -n "$_mios_drm_device" ] && [ -e "$_mios_drm_device" ]; then
        export AQ_DRM_DEVICES="$_mios_drm_device"
        export WLR_DRM_DEVICES="$_mios_drm_device"
    fi
    unset _mios_drm_device
fi

# Snippet: 00-mios-pre-bootc.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Pre-bootc-switch MiOS terminal-experience bridge and locale normalization profile script.
for _var in LANG LC_ALL LC_CTYPE LC_COLLATE LC_MESSAGES LC_NUMERIC LC_TIME LC_MONETARY LC_PAPER LC_NAME LC_ADDRESS LC_TELEPHONE LC_MEASUREMENT LC_IDENTIFICATION; do
    _val="$(eval echo "\"\${${_var}:-}\"" 2>/dev/null | tr -d '"\\')"
    if [ -n "$_val" ]; then
        case "$_val" in
            "C.UTF-8"|"C.utf8"|"c.utf8"|"en_US.UTF-8"|"en_US.utf8") export "${_var}=C.utf8" ;;
            *) export "${_var}=${_val}" ;;
        esac
    fi
done
unset _var _val

if [ ! -e /etc/profile.d/mios-prompt.sh ] && [ -d /mnt/m/etc/profile.d ]; then
    for _miosf in /mnt/m/etc/profile.d/mios-*.sh /mnt/m/etc/profile.d/zz-mios-*.sh; do
        [ -r "$_miosf" ] && . "$_miosf"
    done
    unset _miosf
fi
}
_mios_profile_step

# ============================================================================
# Snippet: bash_completion.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures interactive bash shell completion by sourcing system and user-defined completion scripts to enable tab-completion for MiOS commands and standard utilities.
if [ "x${BASH_VERSION-}" != x -a "x${PS1-}" != x -a "x${BASH_COMPLETION_VERSINFO-}" = x ]; then

    if [ "${BASH_VERSINFO[0]}" -gt 4 ] ||
        [ "${BASH_VERSINFO[0]}" -eq 4 -a "${BASH_VERSINFO[1]}" -ge 2 ]; then
        [ -r "${XDG_CONFIG_HOME:-$HOME/.config}/bash_completion" ] &&
            . "${XDG_CONFIG_HOME:-$HOME/.config}/bash_completion"
        if shopt -q progcomp && [ -r /usr/share/bash-completion/bash_completion ]; then
            . /usr/share/bash-completion/bash_completion
        fi
    fi

fi
}
_mios_profile_step

# ============================================================================
# Snippet: colorgrep.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures shell environment aliases for grep, egrep, and fgrep to enable automatic color output for terminal-based text searching and filtering.

/usr/libexec/grepconf.sh -c || return

alias grep='grep --color=auto' 2>/dev/null
alias egrep='grep -E --color=auto' 2>/dev/null
alias fgrep='grep -F --color=auto' 2>/dev/null
}
_mios_profile_step

# ============================================================================
# Snippet: colorls.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures shell aliases (ll, l., ls) and initializes directory color schemes via dircolors for interactive shells to provide visual distinction in file listings.

[ ! -t 0 ] && return

if [ -z "$USER_LS_COLORS" ]; then

  alias ll='ls -l' 2>/dev/null
  alias l.='ls -d .*' 2>/dev/null

  INCLUDE=
  COLORS=

  for colors in "$HOME/.dir_colors.$TERM" "$HOME/.dircolors.$TERM" \
      "$HOME/.dir_colors" "$HOME/.dircolors"; do
    [ -e "$colors" ] && COLORS="$colors" && \
    INCLUDE="`/usr/bin/cat "$COLORS" | /usr/bin/grep '^INCLUDE' | /usr/bin/cut -d ' ' -f2-`" && \
    break
  done

  [ -z "$COLORS" ] && [ -e "/etc/DIR_COLORS.$TERM" ] && \
  COLORS="/etc/DIR_COLORS.$TERM"

  [ -z "$COLORS" ] && [ -e "/etc/DIR_COLORS" ] && \
  COLORS="/etc/DIR_COLORS"

  [ -n "$COLORS" ] || return

  if [ -e "$INCLUDE" ];
  then
    TMP="`/usr/bin/mktemp .colorlsXXX -q --tmpdir=/tmp`"
    [ -z "$TMP" ] && return

    /usr/bin/cat "$INCLUDE" >> $TMP
    /usr/bin/grep -v '^INCLUDE' "$COLORS" >> $TMP

    eval "`/usr/bin/dircolors --sh $TMP 2>/dev/null`"
    /usr/bin/rm -f $TMP
  else
    eval "`/usr/bin/dircolors --sh $COLORS 2>/dev/null`"
  fi

  [ -z "$LS_COLORS" ] && return
  /usr/bin/grep -qi "^COLOR.*none" $COLORS >/dev/null 2>/dev/null && return
fi

unset TMP COLORS INCLUDE

alias ll='ls -l --color=auto' 2>/dev/null
alias l.='ls -d .* --color=auto' 2>/dev/null
alias ls='ls --color=auto' 2>/dev/null
}
_mios_profile_step

# ============================================================================
# Snippet: colorxzgrep.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Defines shell aliases for xzgrep, xzegrep, and xzfgrep with automatic color highlighting to ensure consistent, readable output when searching compressed files.
/usr/libexec/grepconf.sh -c || return
alias xzgrep='xzgrep --color=auto' 2>/dev/null
alias xzegrep='xzegrep --color=auto' 2>/dev/null
alias xzfgrep='xzfgrep --color=auto' 2>/dev/null
}
_mios_profile_step

# ============================================================================
# Snippet: colorzgrep.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures shell aliases for zgrep, zfgrep, and zegrep to enable automatic color output, ensuring consistent visual highlighting for compressed file searches in the terminal.
[ -f /usr/libexec/grepconf.sh ] || return

/usr/libexec/grepconf.sh -c || return
alias zgrep='zgrep --color=auto' 2>/dev/null
alias zfgrep='zfgrep --color=auto' 2>/dev/null
alias zegrep='zegrep --color=auto' 2>/dev/null
}
_mios_profile_step

# ============================================================================
# Snippet: debuginfod.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures the DEBUGINFOD_URLS and DEBUGINFOD_IMA_CERT_PATH environment variables by scanning /etc/debuginfod for .urls and .certpath files to enable remote debug information retrieval.

prefix="/usr"
if [ -z "$DEBUGINFOD_URLS" ]; then
    DEBUGINFOD_URLS=$(find "/etc/debuginfod" -name "*.urls" -print0 2>/dev/null | xargs -0 cat 2>/dev/null | tr '\n' ' ' || :)
    [ -n "$DEBUGINFOD_URLS" ] && export DEBUGINFOD_URLS || unset DEBUGINFOD_URLS
fi

if [ -z "$DEBUGINFOD_IMA_CERT_PATH" ]; then
    DEBUGINFOD_IMA_CERT_PATH=$(find "/etc/debuginfod" -name "*.certpath" -print0 2>/dev/null | xargs -0 cat 2>/dev/null | tr '\n' ':' || :)
    [ -n "$DEBUGINFOD_IMA_CERT_PATH" ] && export DEBUGINFOD_IMA_CERT_PATH || unset DEBUGINFOD_IMA_CERT_PATH
fi
unset prefix
}
_mios_profile_step

# ============================================================================
# Snippet: docker-host.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures the DOCKER_HOST environment variable to point to the Podman remote socket path, enabling Docker-compatible CLI tools and agents to interact with the container engine.
# AI-related: mios-podman-ps.sh
# shellcheck shell=bash
# Export only when podman reports a path. Rootless podman cannot re-exec where
# user namespaces are unavailable (a container inside a container), and an
# unconditional substitution both printed that error on every login and
# exported "unix://" -- an empty socket path a docker client fails on instead
# of falling back to its own default.
if command -v podman >/dev/null 2>&1; then
    _mios_sock=$(podman info -f '{{.Host.RemoteSocket.Path}}' 2>/dev/null) || _mios_sock=""
    [ -n "$_mios_sock" ] && export DOCKER_HOST="unix://$_mios_sock"
    unset _mios_sock
fi
}
_mios_profile_step

# ============================================================================
# Snippet: enterns.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Shell script that detects the systemd PID to determine if running in a native environment versus WSL, executing the /enterns utility and displaying the motd if a non-standard systemd PID is detected.
SYSDPID=`ps -eo cmd,pid | grep -m 1 ^/lib/systemd/systemd | awk '{print $2}'`
if [ ! -z "$SYSDPID" ] && [ "$SYSDPID" != "1" ]; then
    cat /etc/wslmotd
	/usr/local/bin/enterns
fi
}
_mios_profile_step

# ============================================================================
# Snippet: flatpak.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Upstream Flatpak profile snippet that appends flatpak exports to XDG_DATA_DIRS so installed apps appear in the desktop menus; shipped verbatim, MiOS adds only this header.
if command -v flatpak > /dev/null; then

    new_dirs=$(
        (
            unset G_MESSAGES_DEBUG
            echo "${XDG_DATA_HOME:-"$HOME/.local/share"}/flatpak"
            GIO_USE_VFS=local flatpak --installations
        ) | (
            new_dirs=
            while read -r install_path
            do
                share_path=$install_path/exports/share
                case ":$XDG_DATA_DIRS:" in
                    (*":$share_path:"*) :;;
                    (*":$share_path/:"*) :;;
                    (*) new_dirs=${new_dirs:+${new_dirs}:}$share_path;;
                esac
            done
            echo "$new_dirs"
        )
    )

    export XDG_DATA_DIRS
    XDG_DATA_DIRS="${new_dirs:+${new_dirs}:}${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

    unset new_dirs
fi
}
_mios_profile_step

# ============================================================================
# Snippet: gawk.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Provides shell functions to initialize, prepend, or append paths to AWK environment variables (AWKPATH and AWKLIBPATH) to ensure correct script execution and library linking.
# AI-doc: usr/share/doc/mios/manual/profile.d.md
gawkpath_default () {
	unset AWKPATH
	export AWKPATH=`gawk 'BEGIN {print ENVIRON["AWKPATH"]}'`
}

gawkpath_prepend () {
	[ -z "$AWKPATH" ] && AWKPATH=`gawk 'BEGIN {print ENVIRON["AWKPATH"]}'`
	export AWKPATH="$*:$AWKPATH"
}

gawkpath_append () {
	[ -z "$AWKPATH" ] && AWKPATH=`gawk 'BEGIN {print ENVIRON["AWKPATH"]}'`
	export AWKPATH="$AWKPATH:$*"
}

gawklibpath_default () {
	unset AWKLIBPATH
	export AWKLIBPATH=`gawk 'BEGIN {print ENVIRON["AWKLIBPATH"]}'`
}

gawklibpath_prepend () {
	[ -z "$AWKLIBPATH" ] && \
		AWKLIBPATH=`gawk 'BEGIN {print ENVIRON["AWKLIBPATH"]}'`
	export AWKLIBPATH="$*:$AWKLIBPATH"
}

gawklibpath_append () {
	[ -z "$AWKLIBPATH" ] && \
		AWKLIBPATH=`gawk 'BEGIN {print ENVIRON["AWKLIBPATH"]}'`
	export AWKLIBPATH="$AWKLIBPATH:$*"
}
}
_mios_profile_step

# ============================================================================
# Snippet: gnupg2.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures the GPG_TTY environment variable for interactive shells to ensure GPG prompts and agents function correctly in non-interactive or multiplexed terminal sessions.
if [[ $- == *i* ]]; then
    export GPG_TTY=$(tty)
fi
}
_mios_profile_step

# ============================================================================
# Snippet: guestfish.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Upstream libguestfs profile snippet that sets the guestfish interactive prompt and output colours; shipped verbatim, MiOS adds only this header.
GUESTFISH_PS1='\[\e[1;32m\]><fs>\[\e[0;31m\] '
GUESTFISH_OUTPUT='\e[0m'
GUESTFISH_RESTORE="$GUESTFISH_OUTPUT"
GUESTFISH_INIT='\e[1;34m'
export GUESTFISH_PS1 GUESTFISH_OUTPUT GUESTFISH_RESTORE GUESTFISH_INIT
}
_mios_profile_step

# ============================================================================
# Snippet: lang.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures system locale environment variables and handles CJK fallback for terminal displays by parsing /etc/locale.conf and ~/.i18n to set LANG and LC_* variables.

unset LANG_backup

if [ -n "$(/usr/bin/locale 2>&1 1>/dev/null)" ]; then
    [ -z "$LANG" ] || LANG=C.utf8
    unset LC_ALL
    LC_CTYPE="C.utf8"
    LC_NUMERIC="C.utf8"
    LC_TIME="C.utf8"
    LC_COLLATE="C.utf8"
    LC_MONETARY="C.utf8"
    LC_MESSAGES="C.utf8"
    LC_PAPER="C.utf8"
    LC_NAME="C.utf8"
    LC_ADDRESS="C.utf8"
    LC_TELEPHONE="C.utf8"
    LC_MEASUREMENT="C.utf8"
    LC_IDENTIFICATION="C.utf8"
else
    LANG_backup="${LANG}"
fi

for config in /etc/locale.conf "${HOME}/.i18n"; do
    if [ -f "${config}" ]; then
        if [ -x /usr/bin/sed ]; then
            eval $(/usr/bin/sed -r -e 's/^[[:blank:]]*([[:upper:]_]+)=([[:print:][:digit:]\._-]+|"[[:print:][:digit:]\._-]+")/export \1=\2/;t;d' ${config})
        else
            [ -f "${config}" ] && . "${config}"
        fi
    fi
done

if [ -n "${LANG_backup}" ]; then
    LANG="${LANG_backup}"
fi

unset LANG_backup config

if [ -n "${LC_ALL}" ]; then
    if [ "${LC_ALL}" != "${LANG}" -a -n "${LANG}" ]; then
        export LC_ALL
    else
        unset LC_ALL
    fi
fi

if [ -n "${LANG}" ] && [ "${TERM}" = 'linux' ] && /usr/bin/tty | /usr/bin/grep --quiet -e '/dev/tty'; then
    if /usr/bin/grep --quiet -E -i -e '^.+\.utf-?8$' <<< "${LANG}"; then
        case ${LANG} in
            ja*)    LANG=en_US.UTF-8 ;;
            ko*)    LANG=en_US.UTF-8 ;;
            si*)    LANG=en_US.UTF-8 ;;
            zh*)    LANG=en_US.UTF-8 ;;
            ar*)    LANG=en_US.UTF-8 ;;
            fa*)    LANG=en_US.UTF-8 ;;
            he*)    LANG=en_US.UTF-8 ;;
            en_IN*) true             ;;
            *_IN*)  LANG=en_US.UTF-8 ;;
        esac
    else
        case ${LANG} in
            ja*)    LANG=en_US ;;
            ko*)    LANG=en_US ;;
            si*)    LANG=en_US ;;
            zh*)    LANG=en_US ;;
            ar*)    LANG=en_US ;;
            fa*)    LANG=en_US ;;
            he*)    LANG=en_US ;;
            en_IN*) true       ;;
            *_IN*)  LANG=en_US ;;
        esac
    fi

fi
}
_mios_profile_step

# ============================================================================
# Snippet: less.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures the LESSOPEN environment variable to pipe input through lesspipe.sh, enabling interactive viewing of remote or dynamic content via the less pager.

if [ -z "$LESSOPEN" ] && [ -x /usr/bin/lesspipe.sh ]; then
    export LESSOPEN="||/usr/bin/lesspipe.sh %s"
fi
}
_mios_profile_step

# ============================================================================
# Snippet: mingw32.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Defines the mingw32-env alias and environment variables for cross-compiling for the MinGW32 architecture, used by agents to configure the build environment for Windows-targeted binaries.

alias mingw32-env='eval `rpm --eval %{mingw32_env}`'
}
_mios_profile_step

# ============================================================================
# Snippet: mingw64.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures environment variables and provides the mingw64-env alias to initialize the MinGW-w64 cross-compilation toolchain environment for Windows development.

alias mingw64-env='eval `rpm --eval %{mingw64_env}`'
}
_mios_profile_step

# ============================================================================
# Snippet: mios-agent.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures shell environment for MiOS agents by injecting @-prefix command dispatching, setting MIOS_AGENT_DEFAULT, and providing the mios_repo_use helper...
# AI-doc: usr/share/doc/mios/manual/profile.d.md

case ":${PATH}:" in
    *":/usr/bin:"*) ;;
    *) PATH="/usr/bin:${PATH}"; export PATH ;;
esac

case "$-" in *i*) ;; *) return 0 2>/dev/null || exit 0 ;; esac

if [ -n "${BASH_VERSION-}" ]; then
    __mios_at_dispatch() {
        case "${READLINE_LINE}" in
            '@'*)
                _q="${READLINE_LINE#@}"
                _q="${_q# }"
                READLINE_LINE="@ $(printf %q "$_q")"
                READLINE_POINT=${#READLINE_LINE}
                ;;
        esac
    }
    bind -x '"\C-x@": __mios_at_dispatch' 2>/dev/null || true
    bind '"\r": "\C-x@\C-j"'              2>/dev/null || true
fi

if [ -n "${ZSH_VERSION-}" ]; then
    __mios_at_widget() {
        case "$BUFFER" in
            '@'*)
                local q="${BUFFER#@}"
                q="${q# }"
                BUFFER="@ ${(q)q}"
                CURSOR=${#BUFFER}
                ;;
        esac
        zle .accept-line
    }
    zle -N __mios_at_widget 2>/dev/null && bindkey '^M' __mios_at_widget
fi

export MIOS_AGENT_DEFAULT="${MIOS_AGENT_DEFAULT:-hermes}"
if [ -r /etc/mios/agents/.local_key ]; then
    MIOS_AGENT_LOCAL_KEY="$(cat /etc/mios/agents/.local_key)"
    export MIOS_AGENT_LOCAL_KEY
fi

mios_repo_use() {
    case "$1" in
        main)
            unset GIT_DIR GIT_WORK_TREE
            echo "Mios repo: main"
            ;;
        bootstrap)
            export GIT_DIR=/.mios-bootstrap.git GIT_WORK_TREE=/
            echo "Mios repo: bootstrap"
            ;;
        *)
            echo "Usage: mios_repo_use {main|bootstrap}" >&2
            return 2
            ;;
    esac
}
alias 'mios-repo'='mios_repo_use'
}
_mios_profile_step

# ============================================================================
# Snippet: mios-btop.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Wraps the btop monitor to enforce MiOS-specific presets (palette, transparency, and process-only view) by seeding user configs from /etc/btop/ and intercepting the ...
# AI-doc: usr/share/doc/mios/manual/profile.d.md

[ -n "${PS1:-}" ] || return 0

_mios_btop_seed_user_config() {
    local _src=/etc/btop/btop.conf
    local _dst="${HOME:-/root}/.config/btop/btop.conf"
    [ -f "$_src" ] || return 0
    [ -f "$_dst" ] && return 0
    mkdir -p "$(dirname "$_dst")" 2>/dev/null || return 0
    cp "$_src" "$_dst" 2>/dev/null && chmod 0644 "$_dst" 2>/dev/null
    if [ -f /etc/btop/themes/mios.theme ] && [ ! -f "${HOME:-/root}/.config/btop/themes/mios.theme" ]; then
        mkdir -p "${HOME:-/root}/.config/btop/themes" 2>/dev/null
        cp /etc/btop/themes/mios.theme "${HOME:-/root}/.config/btop/themes/mios.theme" 2>/dev/null
    fi
}
_mios_btop_seed_user_config
unset -f _mios_btop_seed_user_config

if [ -z "${BTOP_CONFIG_DIR:-}" ]; then
    if [ -f "${HOME:-/root}/.config/btop/btop.conf" ]; then
        export BTOP_CONFIG_DIR="${HOME:-/root}/.config/btop"
    elif [ -f /etc/btop/btop.conf ]; then
        export BTOP_CONFIG_DIR=/etc/btop
    fi
fi

btop() {
    local has_preset=0
    for arg in "$@"; do
        case "$arg" in
            -p|--preset|-p[0-9]*|--preset=*) has_preset=1; break ;;
        esac
    done
    if [ $has_preset -eq 0 ]; then
        command btop -p 4 "$@"
    else
        command btop "$@"
    fi
}
}
_mios_profile_step

# ============================================================================
# Snippet: mios-colors.sh
# ============================================================================
_mios_profile_step() {
[ -r /usr/share/mios/theme/mios-colors.sh ] && . /usr/share/mios/theme/mios-colors.sh
}
_mios_profile_step

# ============================================================================
# Snippet: mios-cursor.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Sets XCURSOR_THEME, XCURSOR_SIZE, and XCURSOR_PATH for interactive shells to ensure GUI applications launched from the terminal correctly inherit and display the Bibata cursor theme.
# AI-related: mios-cursor, mios-theme, mios-cursor-ensure
# shellcheck shell=sh

case "$-" in
    *i*) ;;
    *) return 0 ;;
esac

_mios_cursor_env=$(python3 - <<'PY'
import os, shlex, sys
sys.path.insert(0, "/usr/lib/mios")
import mios_toml
data = mios_toml.load_merged()
cursor = data["theme"]["cursor_linux"]
values = {"XCURSOR_THEME": cursor["theme"], "XCURSOR_SIZE": cursor["size"],
          "XCURSOR_PATH": ":".join(os.path.expanduser(p) for p in data["graphics"]["xcursor_path"].split(":"))}
for key, value in values.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
) || return 1
eval "$_mios_cursor_env"
unset _mios_cursor_env

if command -v mios-cursor-ensure >/dev/null 2>&1 \
   && [ ! -d "$HOME/.local/share/icons/${XCURSOR_THEME}/cursors" ]; then
    (mios-cursor-ensure >/dev/null 2>&1 &) 2>/dev/null || true
fi

if [ -n "${DISPLAY:-}" ] && [ -x /usr/libexec/mios/mios-cursor-apply ]; then
    /usr/libexec/mios/mios-cursor-apply >/dev/null 2>&1 || true
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user import-environment XCURSOR_THEME XCURSOR_SIZE 2>/dev/null || true
fi
}
_mios_profile_step

# ============================================================================
# Snippet: mios-env.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Resolves and exports MiOS environment variables (MIOS_*) by merging layered TOML configs and .env files to provide a unified configuration for CLI tools, agents, and...
# AI-doc: usr/share/doc/mios/manual/profile.d.md
# shellcheck shell=sh

# The image's own Rust toolchain ([build.toolchain].rustup_home/cargo_home, from
# 55-native-build.sh --toolchain): its rustup proxies go first on PATH. CARGO_HOME
# is left unset, so cargo's registry stays per user and /usr can stay read-only.
_mios_rust_env() {
    _mios_rh="${MIOS_BUILD_TOOLCHAIN_RUSTUP_HOME:-}"
    _mios_cb="${MIOS_BUILD_TOOLCHAIN_CARGO_HOME:-}/bin"
    if [ -n "$_mios_rh" ] && [ -d "$_mios_rh/toolchains" ] && [ -x "$_mios_cb/rustup" ]; then
        export RUSTUP_HOME="${RUSTUP_HOME:-$_mios_rh}"
        case ":${PATH}:" in *":${_mios_cb}:"*) ;; *) export PATH="${_mios_cb}:${PATH}" ;; esac
    fi
    unset _mios_rh _mios_cb
}

case "$-" in
    *i*) ;;
    *)
        _orig_am="${MIOS_AI_AGENT_MODEL:-}"
        _orig_gm="${MIOS_AI_GATEWAY_MODEL:-}"
        if [ -r /usr/lib/mios/userenv.sh ] || [ -r /usr/share/mios/tools/lib/userenv.sh ]; then
            for _ue in /usr/lib/mios/userenv.sh /usr/share/mios/tools/lib/userenv.sh; do
                if [ -r "$_ue" ]; then
                    # shellcheck source=usr/lib/mios/userenv.sh
                    . "$_ue"
                    break
                fi
            done
        fi
        [ -n "$_orig_am" ] && export MIOS_AI_AGENT_MODEL="$_orig_am"
        [ -n "$_orig_gm" ] && export MIOS_AI_GATEWAY_MODEL="$_orig_gm"
        _mios_gm="${MIOS_AI_GATEWAY_MODEL:-${MIOS_AI_AGENT_MODEL:-}}"
        if [ -n "$_mios_gm" ]; then export MIOS_AI_GATEWAY_MODEL="$_mios_gm"; else unset MIOS_AI_GATEWAY_MODEL; fi
        unset _mios_gm _orig_am _orig_gm
        export MIOS_AI_ENDPOINT MIOS_AI_MODEL MIOS_AI_KEY BROWSER="${BROWSER:-/usr/libexec/mios/mios-open-url}" MIOS_BROWSER="${MIOS_BROWSER:-/usr/libexec/mios/mios-open-url}"
        _mios_rust_env
        unset -f _mios_rust_env
        return 0 2>/dev/null || exit 0
        ;;
esac

_mios_source_if_readable() {
    [ -r "$1" ] || return 0
    # shellcheck source=/dev/null  # operator env files (~/.env.mios, /etc/mios/env.d, install.env) exist only on the host
    . "$1"
}

_mios_source_if_readable "${HOME}/.env.mios"

if [ -d /etc/mios/env.d ]; then
    for _f in /etc/mios/env.d/*.env; do
        _mios_source_if_readable "$_f"
    done
    unset _f
fi

_mios_source_if_readable /etc/mios/install.env

_mios_source_if_readable "${HOME}/.config/mios/env"

_orig_am="${MIOS_AI_AGENT_MODEL:-}"
_orig_gm="${MIOS_AI_GATEWAY_MODEL:-}"
for _ue in /usr/lib/mios/userenv.sh /usr/share/mios/tools/lib/userenv.sh; do
    if [ -r "$_ue" ]; then
        # shellcheck source=usr/lib/mios/userenv.sh
        . "$_ue"
        break
    fi
done
unset _ue
[ -n "$_orig_am" ] && export MIOS_AI_AGENT_MODEL="$_orig_am"
[ -n "$_orig_gm" ] && export MIOS_AI_GATEWAY_MODEL="$_orig_gm"
unset _orig_am _orig_gm

export MIOS_AI_ENDPOINT="${MIOS_AI_ENDPOINT:-http://localhost:${MIOS_PORTS_AGENT_PIPE:-8700}/v1}"
# The gateway model is the SSOT agent model ([ai] agent_model, exported above as MIOS_AI_AGENT_MODEL);
# a hard-coded name drifts from what the agent-pipe advertises and agents refuse to launch.
_mios_gm="${MIOS_AI_GATEWAY_MODEL:-${MIOS_AI_AGENT_MODEL:-}}"
# Unset rather than empty: `mios agent` / agy then fill it from the resolved SSOT.
if [ -n "$_mios_gm" ]; then export MIOS_AI_GATEWAY_MODEL="$_mios_gm"; else unset MIOS_AI_GATEWAY_MODEL; fi
unset _mios_gm
export MIOS_AI_MODEL="${MIOS_AI_MODEL:-granite4.1:8b}"
export MIOS_AI_EMBED_MODEL="${MIOS_AI_EMBED_MODEL:-nomic-embed-text}"
if [ -z "${MIOS_AI_KEY:-}" ] && [ -r /etc/mios/hermes/api.env ]; then
    MIOS_AI_KEY="$(awk -F= '/^API_SERVER_KEY=/ { gsub(/"/, "", $2); print $2; exit }' /etc/mios/hermes/api.env 2>/dev/null)"
fi
export MIOS_AI_KEY="${MIOS_AI_KEY:-}"

export MIOS_IDENTITY_USERNAME="${MIOS_IDENTITY_USERNAME:-${MIOS_IDENTITY_USERNAME:-mios}}"
export MIOS_IDENTITY_HOSTNAME="${MIOS_IDENTITY_HOSTNAME:-${MIOS_IDENTITY_HOSTNAME:-mios}}"
export MIOS_VERSION="${MIOS_VERSION:-}"

export MIOS_SHARE_DIR="${MIOS_SHARE_DIR:-/usr/share/mios}"
export MIOS_AI_DIR="${MIOS_AI_DIR:-/usr/share/mios/ai}"
export MIOS_AI_SCRATCH_DIR="${MIOS_AI_SCRATCH_DIR:-/var/lib/mios/ai/scratch}"
export MIOS_AI_MEMORY_DIR="${MIOS_AI_MEMORY_DIR:-/var/lib/mios/ai/memory}"
export BROWSER="${BROWSER:-/usr/libexec/mios/mios-open-url}"
export MIOS_BROWSER="${MIOS_BROWSER:-/usr/libexec/mios/mios-open-url}"
_mios_rust_env

unset -f _mios_source_if_readable _mios_rust_env
}
_mios_profile_step

# ============================================================================
# Snippet: mios-keyring-init.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Stub script for keyring initialization that informs agents that interactive unlocking is handled by mios-keyring-autounlock.service and configuration is man...
# AI-doc: usr/share/doc/mios/manual/profile.d.md
return 0
}
_mios_profile_step

# ============================================================================
# Snippet: mios-opencode.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Points the interactive opencode TUI/CLI at the MiOS local inference backend by exporting OPENCODE_CONFIG, so opencode resolves the MiOS provider (mios-...
# AI-doc: usr/share/doc/mios/manual/profile.d.md

if [ -z "${OPENCODE_CONFIG-}" ] && [ -r /etc/mios/opencode/opencode.json ]; then
    OPENCODE_CONFIG=/etc/mios/opencode/opencode.json
    export OPENCODE_CONFIG
fi
}
_mios_profile_step

# ============================================================================
# Snippet: mios-podman-ps.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Provides a shell function to intercept `podman ps` for non-root users, falling back to a JSON snapshot of rootful containers if the local rootless podman view is empty.
# AI-related: mios-podman-ps, mios-podman-ps.service
# AI-functions: podman
if [ -z "${CONTAINER_HOST:-}" ] && [ -S /run/podman/podman.sock ] && [ -r /run/podman/podman.sock ]; then
    export CONTAINER_HOST="unix:///run/podman/podman.sock"
fi

case $- in
  *i*) : ;;            # interactive -- install the helper
  *)   return 2>/dev/null || true ;;
esac

podman() {
  if [ "$1" = "ps" ] && [ "$(id -u)" -ne 0 ]; then
    local _snap="${MIOS_PODMAN_PS_SNAPSHOT:-/var/lib/mios/podman-ps.json}"
    local _out; _out="$(command podman "$@" 2>/dev/null)"
    [ -n "$_out" ] && printf '%s\n' "$_out"
    if ! printf '%s\n' "$_out" | tail -n +2 | grep -q '[^[:space:]]' && [ -r "$_snap" ]; then
      echo "── rootful containers ──"
      python3 - "$_snap" <<'PY' 2>/dev/null
import sys, json
try:
    rows = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
if not isinstance(rows, list):
    sys.exit(0)
print(f"{'NAMES':30} {'STATUS':24} IMAGE")
for c in rows:
    n = c.get('Names'); n = ','.join(n) if isinstance(n, list) else (n or '')
    st = c.get('Status') or c.get('State') or ''
    print(f"{n[:29]:30} {st[:23]:24} {c.get('Image') or ''}")
PY
    fi
  else
    command podman "$@"
  fi
}
}
_mios_profile_step

# ============================================================================
# Snippet: mios-prompt.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures the Oh-My-Posh interactive shell prompt for bash and zsh by mapping the MiOS theme JSON to the shell's initialization sequence.
# AI-related: /usr/libexec/mios/oh-my-posh/oh-my-posh, /usr/share/mios/oh-my-posh/mios.omp.json, mios-prompt
# shellcheck shell=bash

[ -n "${PS1:-}" ] || [ -n "${ZSH_VERSION:-}" ] || return 0
[ -t 0 ] && [ -t 1 ] || return 0

OMP_BIN="$(command -v oh-my-posh 2>/dev/null || true)"
[ -z "$OMP_BIN" ] && [ -x /usr/libexec/mios/oh-my-posh/oh-my-posh ] && OMP_BIN=/usr/libexec/mios/oh-my-posh/oh-my-posh
[ -z "$OMP_BIN" ] && [ -x /usr/bin/oh-my-posh ] && OMP_BIN=/usr/bin/oh-my-posh

# Seed user config directories from SSOT if missing
_u_omp="${HOME:-/root}/.config/oh-my-posh/mios.omp.json"
[ -f "$_u_omp" ] || { mkdir -p "$(dirname "$_u_omp")" 2>/dev/null && cp /usr/share/mios/oh-my-posh/mios.omp.json "$_u_omp" 2>/dev/null || true; }
_u_ff="${HOME:-/root}/.config/fastfetch/config.jsonc"
[ -f "$_u_ff" ] || { mkdir -p "$(dirname "$_u_ff")" 2>/dev/null && cp /usr/share/mios/fastfetch/config.jsonc "$_u_ff" 2>/dev/null || true; }
unset _u_ff

# Render on shell startup, including SSH and existing tmux servers. Runtime
# projections are caller-owned; /usr stays immutable and user JSON cannot drift
# away from the layered TOML contract.
if [ "${MIOS_THEME_PROJECTED:-}" != 1 ] && [ -x /usr/libexec/mios/mios-unit-gen ] && [ -x /usr/libexec/mios/mios-gen ]; then
    _mios_projection="${XDG_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}}/mios-terminal"
    [ -z "${MIOS_OMP_THEME:-}" ] || _mios_projection="${MIOS_OMP_THEME%/*}"
    if /usr/libexec/mios/mios-gen render-tmux-theme --runtime "$_mios_projection"; then
        export MIOS_OMP_THEME="$_mios_projection/mios.omp.json"
        [ -z "${TMUX:-}" ] || tmux source-file "$_mios_projection/tmux.conf"
    else
        printf 'MiOS: SSOT terminal projection failed\n' >&2
        return 1
    fi
    unset _mios_projection
fi
OMP_THEME="/usr/share/mios/oh-my-posh/mios.omp.json"
[ -n "${MIOS_OMP_THEME:-}" ] && [ -r "$MIOS_OMP_THEME" ] && OMP_THEME="$MIOS_OMP_THEME"
unset _u_omp

if [ -n "$OMP_BIN" ] && [ -x "$OMP_BIN" ] && [ -r "$OMP_THEME" ]; then
    _posh_cache="${XDG_CACHE_HOME:-$HOME/.cache}/oh-my-posh"
    if [ ! -d "$_posh_cache" ] || [ ! -w "$_posh_cache" ]; then
        mkdir -p "$_posh_cache" 2>/dev/null || true
    fi
    if [ ! -w "$_posh_cache" ]; then
        export POSH_CACHE_DIR="/tmp/posh-cache-${EUID:-$(id -u)}"
        mkdir -p "$POSH_CACHE_DIR" 2>/dev/null || true
    else
        export POSH_CACHE_DIR="$_posh_cache"
    fi
    if [ -n "${BASH_VERSION:-}" ]; then
        eval "$("$OMP_BIN" init bash --config="$OMP_THEME" --print)"
    elif [ -n "${ZSH_VERSION:-}" ]; then
        eval "$("$OMP_BIN" init zsh --config="$OMP_THEME" --print)"
    fi
    unset _posh_cache
fi
}
_mios_profile_step

# ============================================================================
# Snippet: mios-verbs.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Defines the `mios()` shell function to intercept and route canonical verbs (mini, dash, build, config, etc.) to specific `/usr/libexec/mios/` helpers while allowin...
# AI-doc: usr/share/doc/mios/manual/profile.d.md
# shellcheck shell=bash
[ -n "${PS1:-}" ] || return 0

_mios_dashboard() {
    local _label="$1"; shift
    if [[ "$_label" != monitor ]]; then
        /usr/bin/mios-gen dashboard --root / "$@"
        return $?
    fi
    local _c _dash=""
    for _c in /usr/libexec/mios/mios-dashboard \
              /mnt/m/usr/libexec/mios/mios-dashboard; do
        [[ -x "$_c" ]] && { _dash="$_c"; break; }
    done
    [[ -n "$_dash" ]] || { echo "Mios $_label: mios-dashboard not found" >&2; return 127; }
    "$_dash" "$@"
}

mios() {
    case "${1:-help}" in
        mini)
            shift; _mios_dashboard mini "$@"
            ;;
        dash|dashboard)
            shift; _mios_dashboard dash "$@"
            ;;
        mon|monitor)
            shift; _mios_dashboard monitor --monitor "$@"
            ;;
        build)
            shift
            if [[ -x /usr/libexec/mios/mios-build-driver ]]; then
                /usr/libexec/mios/mios-build-driver "$@"
            elif [[ -x /mnt/m/usr/libexec/mios/mios-build-driver ]]; then
                /mnt/m/usr/libexec/mios/mios-build-driver "$@"
            else
                echo "Mios build: mios-build-driver not found" >&2
                return 127
            fi
            ;;
        config)
            shift
            if [[ -x /usr/libexec/mios/mios-configurator-launch ]]; then
                /usr/libexec/mios/mios-configurator-launch "$@"
            elif [[ -x /mnt/m/usr/libexec/mios/mios-configurator-launch ]]; then
                /mnt/m/usr/libexec/mios/mios-configurator-launch "$@"
            else
                echo "Mios config: mios-configurator-launch not found" >&2
                return 127
            fi
            ;;
        dotfiles)
            shift
            if [[ -x /usr/libexec/mios/mios-dotfiles ]]; then
                /usr/libexec/mios/mios-dotfiles "$@"
            elif [[ -x /mnt/m/usr/libexec/mios/mios-dotfiles ]]; then
                /mnt/m/usr/libexec/mios/mios-dotfiles "$@"
            else
                echo "Mios dotfiles: mios-dotfiles not found" >&2
                return 127
            fi
            ;;
        dev)
            shift
            exec bash "$@"
            ;;
        pull)
            shift
            if [[ -d /mnt/m/.git ]]; then
                ( cd /mnt/m && git fetch --depth=1 origin main && git reset --hard FETCH_HEAD )
            else
                echo "Mios pull: /mnt/m is not a git working tree" >&2
                return 1
            fi
            ;;
        update)
            shift
            if command -v mios-update >/dev/null 2>&1; then
                exec mios-update "$@"
            elif [[ -x /usr/bin/mios-update ]]; then
                exec /usr/bin/mios-update "$@"
            else
                echo "Mios update: /usr/bin/mios-update is absent" >&2
                return 1
            fi
            ;;
        help|"-h"|"--help"|"")
            cat <<'EOH'

  MiOS verbs (inside MiOS-DEV):
    mios mini    -- compact 80x20 framed banner + fastfetch (auto on shell spawn)
    mios dash    -- FULL dashboard: ASCII banner + services + extended sys specs
    mios mon     -- resource monitor + unified stack table (refreshes every 5s)
    mios build   -- run /usr/libexec/mios/mios-build-driver (OCI image build)
    mios config  -- open the unified MiOS Settings surface (:8640/configure; offline HTML fallback)
    mios dotfiles [status|diff|sync] -- project the SSOT dotfiles (theme, btop, gitconfig, VS Code / Windows Terminal) to your HOME
    mios dev     -- nested bash session (you're already in MiOS-DEV)
    mios pull    -- git fetch + reset M:\ to origin/main
    mios update  -- mios pull + hint to re-run bootstrap from Windows
    mios help    -- this list
    mios <prompt>-- pass to the AI agent (/usr/bin/mios)

EOH
            ;;
        *)
            command mios "$@"
            ;;
    esac
}
export -f mios 2>/dev/null || true

_mios_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    if [[ $COMP_CWORD -eq 1 ]]; then
        mapfile -t COMPREPLY < <(compgen -W "mini dash build config dotfiles dev pull update help mon monitor" -- "$cur")
    fi
}
# `complete` is a bash builtin and /etc/profile.d is read by every login shell,
# so an unguarded call is a hard error in zsh on every login.
if [ -n "${BASH_VERSION:-}" ]; then complete -F _mios_complete mios; fi

command_not_found_handle() {
    if [[ "${1:-}" == @* ]] && [[ "${1}" != "@" ]]; then
        local first="${1#@}"
        shift
        if [[ -x /usr/bin/mios ]]; then
            /usr/bin/mios "$first" "$@"
            return $?
        fi
    fi
    printf '%s: %s: command not found\n' "${BASH_SOURCE[0]##*/}" "${1:-}" >&2
    return 127
}
export -f command_not_found_handle 2>/dev/null || true

# zsh spells the hook with a trailing "r"; without this the @verb shorthand is
# silently dead in every zsh login shell.
if [ -n "${ZSH_VERSION:-}" ]; then command_not_found_handler() { command_not_found_handle "$@"; }; fi
}
_mios_profile_step

# ============================================================================
# Snippet: mios-wslg-gpu.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures Mesa and GTK4 rendering paths for WSLg environments, forcing software fallback or d3d12 Gallium acceleration to ensure stable window rendering when the default Vulkan-via-Zink path fails.
# AI-related: mios-wslg-gpu
# shellcheck shell=sh

[ -d /mnt/wslg ] || return 0

if [ "${MIOS_GPU_HARDWARE:-0}" = "1" ]; then
    export LIBGL_KOPPER_DISABLE="${LIBGL_KOPPER_DISABLE:-1}"
else
    export GSK_RENDERER="${GSK_RENDERER:-cairo}"
    export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
    export GALLIUM_DRIVER="${GALLIUM_DRIVER:-llvmpipe}"
    export LIBGL_KOPPER_DISABLE="${LIBGL_KOPPER_DISABLE:-1}"
fi

export WEBKIT_DISABLE_COMPOSITING_MODE="${WEBKIT_DISABLE_COMPOSITING_MODE:-1}"

export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    _uid="$(id -u 2>/dev/null || echo 1000)"
    export XDG_RUNTIME_DIR="/run/user/$_uid"
    unset _uid
fi

export DISPLAY="${DISPLAY:-:0}"

# Leave DPI to WSLg's per-display scaling. Cursor policy is projected by
# mios-cursor.sh from the layered SSOT; do not shrink it here.

:
}
_mios_profile_step

# ============================================================================
# Snippet: mios-wslg.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures Wayland/X11 environment variables (DISPLAY, WAYLAND_DISPLAY, XDG_RUNTIME_DIR) and PulseAudio paths specifically for WSLg integration to ensure GUI applic...
# AI-doc: usr/share/doc/mios/manual/profile.d.md
# shellcheck shell=sh

[ -d /mnt/wslg ] || return 0

if [ -d "/run/user/$(id -u)" ]; then
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    case "$XDG_RUNTIME_DIR" in
        /mnt/wslg/*)
            XDG_RUNTIME_DIR="/run/user/$(id -u)"
            export XDG_RUNTIME_DIR
            ;;
    esac
else
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/mnt/wslg/runtime-dir}"
fi
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DISPLAY="${DISPLAY:-:0}"
export PULSE_SERVER="${PULSE_SERVER:-/mnt/wslg/PulseServer}"
export XDG_SESSION_TYPE=wayland

export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-GNOME}"

if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "/run/user/$(id -u)/bus" ]; then
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
    export DBUS_SESSION_BUS_ADDRESS
fi

if [ -z "${WSL_INTEROP:-}" ]; then
    _wsl_interop="$(find /run/WSL/ -maxdepth 1 -name '*_interop' 2>/dev/null | head -n1)"
    [ -n "$_wsl_interop" ] && export WSL_INTEROP="$_wsl_interop"
    unset _wsl_interop
fi

if [ -z "${GDK_BACKEND:-}" ]; then
    export GDK_BACKEND="${MIOS_WSL2_DESKTOP_COMPAT_GDK_BACKEND:-x11}"
fi
if [ -z "${MOZ_ENABLE_WAYLAND:-}" ]; then
    export MOZ_ENABLE_WAYLAND="${MIOS_WSL2_DESKTOP_COMPAT_MOZ_WAYLAND:-0}"
fi
if [ -z "${QT_QPA_PLATFORM:-}" ]; then
    export QT_QPA_PLATFORM="${MIOS_WSL2_DESKTOP_COMPAT_QT_PLATFORM:-xcb}"
fi

if command -v mios-cursor-apply >/dev/null 2>&1; then
    (mios-cursor-apply >/dev/null 2>&1 &) 2>/dev/null || true
fi
}
_mios_profile_step

# ============================================================================
# Snippet: sh.local
# ============================================================================
_mios_profile_step() {
#Add any required envvar overrides to this file, it is sourced from /etc/profile
:
}
_mios_profile_step

# ============================================================================
# Snippet: toolbox.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Login-shell hook from toolbox: shows the toolbox welcome hints once and tags the prompt when running inside a toolbox container.
# shellcheck shell=bash

[ "${BASH_VERSION:-}" != "" ] || [ "${ZSH_VERSION:-}" != "" ] || return 0
[ "$PS1" != "" ] || return 0

toolbox_config="$HOME/.config/toolbox"
host_welcome_stub="$toolbox_config/host-welcome-shown"
toolbox_welcome_stub="$toolbox_config/toolbox-welcome-shown"

eval "$(
          if [ -f /etc/os-release ]; then
              . /etc/os-release
          else
              . /usr/lib/os-release
          fi

          echo ID="$ID"
          echo PRETTY_NAME="\"$PRETTY_NAME\""
          echo VARIANT_ID="$VARIANT_ID"
      )"

if [ -f /run/ostree-booted ] \
   && ! [ -f "$host_welcome_stub" ] \
   && [ "${ID}" = "fedora" ] \
   && { [ "${VARIANT_ID}" = "workstation" ] \
        || [ "${VARIANT_ID}" = "silverblue" ] \
        || [ "${VARIANT_ID}" = "kinoite" ] \
        || [ "${VARIANT_ID}" = "sericea" ]; }; then
    echo ""
    echo "Welcome to ${PRETTY_NAME:-Linux}"
    echo ""
    echo "This terminal is running on the host system. You may want to try"
    echo "Out the Toolbx for a directly mutable environment that allows "
    echo "Package installation with DNF"
    echo ""
    printf "For more information, see the "
    printf '\033]8;;https://docs.fedoraproject.org/en-US/fedora-silverblue/toolbox/\033\\documentation\033]8;;\033\\'
    printf ".\n"
    echo ""

    mkdir -p "$toolbox_config"
    touch "$host_welcome_stub"
fi

if [ -f /run/.containerenv ] \
   && [ -f /run/.toolboxenv ]; then
    [ "${BASH_VERSION:-}" != "" ] && PS1=$(printf "\[\033[35m\]⬢ \[\033[0m\]%s" "[\u@\h \W]\\$ ")
    [ "${ZSH_VERSION:-}" != "" ] && PS1=$(printf "\033[35m⬢ \033[0m%s" "[%n@%m]%~%# ")

    if ! [ -f "$toolbox_welcome_stub" ]; then
        echo ""
        echo "Welcome to the Toolbx; a container where you can install and run"
        echo "All your tools"
        echo ""

        if [ "${ID}" = "fedora" ]; then
            echo " - Use DNF in the usual manner to install command line tools"
            echo " - To create a new tools container, run 'toolbox create'"
            echo ""
            printf "For more information, see the "
            printf '\033]8;;https://docs.fedoraproject.org/en-US/fedora-silverblue/toolbox/\033\\documentation\033]8;;\033\\'
            printf ".\n"
        else
            echo " - To create a new tools container, run 'toolbox create'"
        fi

        echo ""

        mkdir -p "$toolbox_config"
        touch "$toolbox_welcome_stub"
    fi

    if ! [ -f /etc/profile.d/vte.sh ] && [ -z "$PROMPT_COMMAND" ] && [ "${VTE_VERSION:-0}" -ge 3405 ]; then
        case "$TERM" in
            xterm*|vte*)
                [ -n "${BASH_VERSION:-}" ] && PROMPT_COMMAND=" "
                ;;
        esac
    fi

    if [ "$TERM" != "" ]; then
        error_message="Error: terminfo entry not found for $TERM"
        term_without_first_character="${TERM#?}"
        term_just_first_character="${TERM%"$term_without_first_character"}"
        terminfo_sub_directory="$term_just_first_character/$TERM"

        if [ "$TERMINFO" = "" ]; then
          ! [ -e "/usr/share/terminfo/$terminfo_sub_directory" ] \
            && ! [ -e "/lib/terminfo/$terminfo_sub_directory" ] \
            && ! [ -e "$HOME/.terminfo/$terminfo_sub_directory" ] \
            && echo "$error_message" >&2
        else
          ! [ -e "$TERMINFO/$terminfo_sub_directory" ] \
            && echo "$error_message" >&2
        fi
    fi
fi

unset ID
unset PRETTY_NAME
unset VARIANT_ID
unset toolbox_config
unset host_welcome_stub
unset toolbox_welcome_stub
}
_mios_profile_step

# ============================================================================
# Snippet: vim.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures the shell environment by aliasing vi to vim and ensuring consistent editor behavior across bash, ksh, and zsh shells when the binaries are located in standard system paths.
if [ -n "${BASH_VERSION-}" -o -n "${KSH_VERSION-}" -o -n "${ZSH_VERSION-}" ]; then
  case "$(command -v vim)-$(command -v vi)" in
    /usr/bin/vim-/usr/bin/vi)
        alias vi=vim
        ;;
  esac
fi
}
_mios_profile_step

# ============================================================================
# Snippet: vte.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Login-shell hook from VTE: reports the working directory to VTE-based terminals so new tabs open in the same directory.
# shellcheck shell=bash

[ -n "${BASH_VERSION:-}" ] || [ -n "${ZSH_VERSION:-}" ] || return 0

[[ $- == *i* ]] || return 0

[ "${VTE_VERSION:-0}" -ge 3405 ] || return 0

case "$TERM" in
    xterm*|vte*|gnome*) :;;
    *) return 0 ;;
esac

__vte_termprop_signal() {
    local errsv="$?"
    printf '\033]666;%s!\033\\' "$1"
    return $errsv
}

__vte_termprop_set() {
    local errsv="$?"
    printf '\033]666;%s=%s\033\\' "$1" "$2"
    return $errsv
}

__vte_termprop_reset() {
    local errsv="$?"
    printf '\033]666;%s\033\\' "$1"
    return $errsv
}

__vte_osc7 () {
    local errsv="$?"
    printf "\033]7;file://%s%s\033\\" "${HOSTNAME}" "$(/usr/libexec/vte-urlencode-cwd)"
    return $errsv
}

__vte_precmd() {
    local errsv="$?"
    __vte_termprop_set "vte.shell.postexec" "$?"
    __vte_termprop_signal "vte.shell.precmd"
    return $errsv;
}

__vte_prompt_command() {
    local errsv="$?"
    __vte_termprop_set "vte.shell.postexec" "$errsv"
    __vte_osc7
    local pwd='~'
    [ "$PWD" != "$HOME" ] && pwd=${PWD/#$HOME\//\~\/}
    pwd="${pwd//[[:cntrl:]]}"
    printf "\033]0;%s@%s:%s\033\\" "${USER}" "${HOSTNAME%%.*}" "${pwd}"
    __vte_termprop_signal "vte.shell.precmd"
    return $errsv
}

if [[ -n "${BASH_VERSION:-}" ]]; then

    if [[ "$(declare -p PROMPT_COMMAND 2>&1)" =~ "declare -a" ]]; then
        PROMPT_COMMAND+=(__vte_precmd)
        PROMPT_COMMAND+=(__vte_osc7)
    else
        # shellcheck disable=SC2178  # the array case is the branch above; this one is a scalar
        PROMPT_COMMAND="__vte_prompt_command"
    fi
    PS0=$(__vte_termprop_signal "vte.shell.preexec")

    if [[ "$PS1" != *\]133\;* ]]; then

        PS1='\[\e]133;D;$?\e\\\e]133;A\e\\\]'"$PS1"'\[\e]133;B\e\\\]'

        PS0='\e]133;C\e\\\r'"${PS0:-}"
    fi

elif [[ -n "${ZSH_VERSION:-}" ]]; then
    precmd_functions+=(__vte_osc7)
    precmd_functions+=(__vte_precmd)

    if [[ "$PS1" != *\]133\;* ]]; then

        PS1=$'%{\e]133;D;%?\e\\\e]133;A\e\\%}'"$PS1"$'%{\e]133;B\e\\%}'

        __vte_preexec() {
            local errsv="$?"
            printf '\e]133;C\e\\\r'
            return $errsv
        }
        # shellcheck disable=SC2206,SC2154  # zsh branch: zsh does not word-split, quoting would add an empty hook, and preexec is zsh-set
        preexec_functions=(__vte_preexec $preexec $preexec_functions)
        unset preexec
    fi

fi

return 0
}
_mios_profile_step

# ============================================================================
# Snippet: which2.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Configures the 'which' command alias across different shells (bash, ksh, zsh) to ensure it correctly resolves functions and aliases in the current environment.

if [ -r /proc/$$/exe ]; then
    SHELLNAME=$(basename $(readlink /proc/$$/exe))
else
    SHELLNAME="unknown"
fi
case "$SHELLNAME" in
*ksh*|zsh)
    alias which='alias | /usr/bin/which --tty-only --read-alias --show-tilde --show-dot'
    ;;
bash|sh)
    alias which='(alias; declare -f) | /usr/bin/which --tty-only --read-alias --read-functions --show-tilde --show-dot'
    ;;
*)
    ;;
esac
}
_mios_profile_step

# ============================================================================
# Snippet: zz-mios-motd.sh
# ============================================================================
_mios_profile_step() {
# AI-hint: Enter the shared native terminal workspace for every interactive local or SSH shell.
# AI-related: usr/share/mios/mios.toml [terminal.startup], usr/libexec/mios/mios-terminal
case $- in *i*) ;; *) return 0 ;; esac
[ -t 0 ] && [ -t 1 ] || return 0
[ "${MIOS_TMUX_HELPER:-}" != 1 ] || return 0
[ -z "${STY:-}" ] || return 0
[ -z "${MIOS_SKIP_MOTD:-}" ] || return 0
[ "${MIOS_SESSION_BARE:-}" != 1 ] || return 0  # bare Tmux-OS session (mios-terminal --new): no workspace panes
if [ -n "${TMUX:-}" ]; then
    local _mios_terminal_python
    _mios_terminal_python="$(/usr/bin/mios-toml-get mcp python)" || return 1
    "$_mios_terminal_python" /usr/libexec/mios/mios-mcp-server --workspace-ensure
else
    # Re-entry guard: auto-exec once per login. The exported flag reaches every
    # nested interactive shell, which must stay a plain shell. It is checked
    # here, not above, so tmux panes (which inherit it from the server) still
    # run --workspace-ensure.
    [ -z "${MIOS_MOTD_SHOWN:-}" ] || return 0
    MIOS_MOTD_SHOWN=1
    export MIOS_MOTD_SHOWN
    if [ "$(/usr/bin/mios-toml-get terminal.startup auto_attach)" = true ]; then
        exec /usr/libexec/mios/mios-terminal
    fi
fi
}
_mios_profile_step

unset -f _mios_profile_step 2>/dev/null || true
