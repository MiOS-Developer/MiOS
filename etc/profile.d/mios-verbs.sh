# AI-hint: Defines the `mios()` shell function to intercept and route canonical verbs (mini, dash, build, config, etc.) to specific `/usr/libexec/mios/` helpers while allowin...
# AI-doc: usr/share/doc/mios/manual/profile.d.md
# shellcheck shell=bash
[ -n "${PS1:-}" ] || return 0

_mios_dashboard() {
    local _label="$1"; shift
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
            shift; _mios_dashboard mini --mini --once "$@"
            ;;
        dash|dashboard)
            shift; ( export MIOS_DASH_SERVICES=1 MIOS_COMPACT=0; _mios_dashboard dash --dash --once "$@" )
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
