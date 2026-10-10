#!/usr/bin/env bash
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
