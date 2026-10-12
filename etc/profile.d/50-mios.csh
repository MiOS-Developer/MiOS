# AI-hint: Consolidated profile.d initialization for csh/tcsh (folded per ADR-0030 task_3659).
# AI-doc: usr/share/doc/mios/manual/root.md

# ============================================================================
# Snippet: colorgrep.csh
# ============================================================================

# color-grep initialization

/usr/libexec/grepconf.sh -c
if ( $status == 1 ) then
    goto _end_snippet_0_colorgrep_csh
endif

alias grep 'grep --color=auto'
alias egrep 'grep -E --color=auto'
alias fgrep 'grep -F --color=auto'
_end_snippet_0_colorgrep_csh:

# ============================================================================
# Snippet: colorls.csh
# ============================================================================
# skip everything for non-interactive shells
if (! $?prompt) goto _end_snippet_1_colorls_csh

# color-ls initialization
if ( $?USER_LS_COLORS ) then
  if ( "$USER_LS_COLORS" != "" ) then
     #when USER_LS_COLORS defined do not override user
     #specified LS_COLORS and use them
     goto finish
  endif
endif

alias ll 'ls -l'
alias l. 'ls -d .*'
set COLORS=/etc/DIR_COLORS

if ($?TERM) then
  if ( -e "/etc/DIR_COLORS.$TERM" ) then
     set COLORS="/etc/DIR_COLORS.$TERM"
  endif
endif
if ( -f ~/.dircolors ) set COLORS=~/.dircolors
if ( -f ~/.dir_colors ) set COLORS=~/.dir_colors
if ($?TERM) then
  if ( -f ~/.dircolors."$TERM" ) set COLORS=~/.dircolors."$TERM"
  if ( -f ~/.dir_colors."$TERM" ) set COLORS=~/.dir_colors."$TERM"
endif
set INCLUDE="`/usr/bin/cat "$COLORS" | /usr/bin/grep '^INCLUDE' | /usr/bin/cut -d ' ' -f2-`"

if ( ! -e "$COLORS" ) goto _end_snippet_1_colorls_csh

set _tmp="`/usr/bin/mktemp .colorlsXXX -q --tmpdir=/tmp`"
#if mktemp fails, goto _end_snippet_1_colorls_csh when include was active, otherwise use $COLORS file
if ( "$_tmp" == '' ) then
  if ( "$INCLUDE" == '' ) then
    eval "`/usr/bin/dircolors -c $COLORS`"
  endif
  goto cleanup
endif

if ( "$INCLUDE" != '' ) /usr/bin/cat "$INCLUDE" >> $_tmp
/usr/bin/grep -v '^INCLUDE' "$COLORS" >> $_tmp

eval "`/usr/bin/dircolors -c $_tmp`"

/usr/bin/rm -f $_tmp

if ( "$LS_COLORS" == '' ) goto _end_snippet_1_colorls_csh
cleanup:
set color_none=`/usr/bin/sed -n '/^COLOR.*none/Ip' < $COLORS`
if ( "$color_none" != '' ) then
   unset color_none
   goto _end_snippet_1_colorls_csh
endif
unset color_none
unset _tmp
unset INCLUDE
unset COLORS

finish:
alias ll 'ls -l --color=auto'
alias l. 'ls -d .* --color=auto'
alias ls 'ls --color=auto'
_end_snippet_1_colorls_csh:

# ============================================================================
# Snippet: colorxzgrep.csh
# ============================================================================
/usr/libexec/grepconf.sh -c
if ( $status == 1 ) goto _end_snippet_2_colorxzgrep_csh
alias xzgrep 'xzgrep --color=auto'
alias xzfgrep 'xzfgrep --color=auto'
alias xzegrep 'xzegrep --color=auto'
_end_snippet_2_colorxzgrep_csh:

# ============================================================================
# Snippet: colorzgrep.csh
# ============================================================================
test -f /usr/libexec/grepconf.sh
if ( $status == 1 ) goto _end_snippet_3_colorzgrep_csh

/usr/libexec/grepconf.sh -c
if ( $status == 1 ) goto _end_snippet_3_colorzgrep_csh

alias zgrep 'zgrep --color=auto'
alias zfgrep 'zfgrep --color=auto'
alias zegrep 'zegrep --color=auto'
_end_snippet_3_colorzgrep_csh:

# ============================================================================
# Snippet: csh.local
# ============================================================================
#Add any required envvar overrides to this file, it is sourced from /etc/csh.login
_end_snippet_4_csh_local:

# ============================================================================
# Snippet: debuginfod.csh
# ============================================================================
# $HOME/.login* or similar files may first set $DEBUGINFOD_URLS.
# If $DEBUGINFOD_URLS is not set there, we set it from system *.url files.
# $HOME/.*rc or similar files may then amend $DEBUGINFOD_URLS.
# See also [man debuginfod-client-config] for other environment variables
# such as $DEBUGINFOD_MAXSIZE, $DEBUGINFOD_MAXTIME, $DEBUGINFOD_PROGRESS.

set prefix="/usr"
if (! $?DEBUGINFOD_URLS) then
    set DEBUGINFOD_URLS=`sh -c 'cat /dev/null "$0"/*.urls 2>/dev/null; :' "/etc/debuginfod" | tr '\n' ' '`
    if ( "$DEBUGINFOD_URLS" != "" ) then
        setenv DEBUGINFOD_URLS "$DEBUGINFOD_URLS"
    else
        unset DEBUGINFOD_URLS
    endif
    set DEBUGINFOD_IMA_CERT_PATH=`sh -c 'cat /dev/null "$0"/*.certpath 2>/dev/null; :' "/etc/debuginfod" | tr '\n' ':'`
    if ( "$DEBUGINFOD_IMA_CERT_PATH" != "" ) then
        setenv DEBUGINFOD_IMA_CERT_PATH "$DEBUGINFOD_IMA_CERT_PATH"
    else
        unset DEBUGINFOD_IMA_CERT_PATH
    endif
endif
unset prefix
_end_snippet_5_debuginfod_csh:

# ============================================================================
# Snippet: flatpak.csh
# ============================================================================
set _flatpak=`where flatpak | head -n 1`
if ( ${%_flatpak} > 0 ) then
    if ( ! ${?XDG_DATA_HOME} ) setenv XDG_DATA_HOME "$HOME/.local/share"
    if ( ${%XDG_DATA_HOME} == 0 ) setenv XDG_DATA_HOME "$HOME/.local/share"
    if ( ! ${?XDG_DATA_DIRS} ) setenv XDG_DATA_DIRS /usr/local/share:/usr/share
    if ( ${%XDG_DATA_DIRS} == 0 ) setenv XDG_DATA_DIRS /usr/local/share:/usr/share
    set _new_dirs=""
    foreach _line (`(unset G_MESSAGES_DEBUG; echo "${XDG_DATA_HOME}"/flatpak; setenv GIO_USE_VFS local; flatpak --installations)`)
        set _line=${_line}/exports/share
	if ( ":${XDG_DATA_DIRS}:" =~ *:${_line}:* ) continue
	if ( ":${XDG_DATA_DIRS}:" =~ *:${_line}/:* ) continue
	if ( ${%_new_dirs} > 0 ) set _new_dirs="${_new_dirs}:"
	set _new_dirs="${_new_dirs}${_line}"
    end
    if ( ${%_new_dirs} > 0 ) then
	set _new_dirs="${_new_dirs}:"
	setenv XDG_DATA_DIRS "${_new_dirs}${XDG_DATA_DIRS}"
    endif
endif
unset _flatpak _line _new_dirs
_end_snippet_6_flatpak_csh:

# ============================================================================
# Snippet: gawk.csh
# ============================================================================
alias gawkpath_default 'unsetenv AWKPATH; setenv AWKPATH `gawk -v x=AWKPATH "BEGIN {print ENVIRON[x]}"`'

alias gawkpath_prepend 'if (! $?AWKPATH) setenv AWKPATH ""; if ($AWKPATH == "") then; unsetenv AWKPATH; setenv AWKPATH `gawk -v x=AWKPATH "BEGIN {print ENVIRON[x]}"`; endif; setenv AWKPATH "\!*"":$AWKPATH"'

alias gawkpath_append 'if (! $?AWKPATH) setenv AWKPATH ""; if ($AWKPATH == "") then; unsetenv AWKPATH; setenv AWKPATH `gawk -v x=AWKPATH "BEGIN {print ENVIRON[x]}"`; endif; setenv AWKPATH "$AWKPATH"":\!*"'

alias gawklibpath_default 'unsetenv AWKLIBPATH; setenv AWKLIBPATH `gawk -v x=AWKLIBPATH "BEGIN {print ENVIRON[x]}"`'

alias gawklibpath_prepend 'if (! $?AWKLIBPATH) setenv AWKLIBPATH ""; if ($AWKLIBPATH == "") then; unsetenv AWKLIBPATH; setenv AWKLIBPATH `gawk -v x=AWKLIBPATH "BEGIN {print ENVIRON[x]}"`; endif; setenv AWKLIBPATH "\!*"":$AWKLIBPATH"'

alias gawklibpath_append 'if (! $?AWKLIBPATH) setenv AWKLIBPATH ""; if ($AWKLIBPATH == "") then; unsetenv AWKLIBPATH; setenv AWKLIBPATH `gawk -v x=AWKLIBPATH "BEGIN {print ENVIRON[x]}"`; endif; setenv AWKLIBPATH "$AWKLIBPATH"":\!*"'
_end_snippet_7_gawk_csh:

# ============================================================================
# Snippet: gnupg2.csh
# ============================================================================
if ( $?prompt ) then
    setenv GPG_TTY `tty`
endif
_end_snippet_8_gnupg2_csh:

# ============================================================================
# Snippet: lang.csh
# ============================================================================
# /etc/profile.d/lang.csh - exports environment variables, and provides fallback
#                          for CJK languages that can't be displayed in console.
#                          Resets the locale if unavailable.

unset LANG_backup

# If unavailable, reset to the default. Do this before reading in any
# explicit user configuration. We simply check if locale emits any
# warnings, and assume that the settings are invalid if it does.
set locale_error=`(/usr/bin/locale >/dev/null) |& cat`
if ("${locale_error}" != "") then
    if (${?LANG}) then
        setenv LANG C.utf8
    endif
    unsetenv LC_ALL
    setenv LC_CTYPE C.utf8
    setenv LC_NUMERIC C.utf8
    setenv LC_TIME C.utf8
    setenv LC_COLLATE C.utf8
    setenv LC_MONETARY C.utf8
    setenv LC_MESSAGES C.utf8
    setenv LC_PAPER C.utf8
    setenv LC_NAME C.utf8
    setenv LC_ADDRESS C.utf8
    setenv LC_TELEPHONE C.utf8
    setenv LC_MEASUREMENT C.utf8
    setenv LC_IDENTIFICATION C.utf8
else
    if (${?LANG}) then
        set LANG_backup=${LANG}
    endif
endif

foreach config (/etc/locale.conf "${HOME}/.i18n")
    if (-f "${config}") then
        # NOTE: We are using eval & sed here to avoid invoking of any commands & functions from those files.
        eval `/usr/bin/sed -r -e 's/^[[:blank:]]*([[:upper:]_]+)=([[:print:][:digit:]\._-]+|"[[:print:][:digit:]\._-]+")/setenv \1 \2;/;t;d' ${config}`
    endif
end

if (${?LANG_backup}) then
    setenv LANG "${LANG_backup}"
endif

unset LANG_backup config locale_error

# ----------------------------------------------

# The LC_ALL is not supposed to be set in /etc/locale.conf according to 'man 5 locale.conf'.
# If it is set, then we expect it is user's explicit override (most likely from ~/.i18n file).
# See 'man 7 locale' for more info about LC_ALL.
if (${?LC_ALL}) then
    if (${?LANG}) then
        if (${LC_ALL} != ${LANG}) then
            setenv LC_ALL
        else
            unsetenv LC_ALL
        endif
    else
        unsetenv LC_ALL
    endif
endif

# The ${LANG} manipulation is necessary only in virtual terminal (a.k.a. console - /dev/tty*):
set in_console=`/usr/bin/tty | /usr/bin/grep -vc -e '/dev/tty'`

if (${?LANG} && ${?TERM}) then
    if (${TERM} == 'linux' && $in_console == 0) then
        set utf8_used=`echo ${LANG} | /usr/bin/grep -vc -E -i -e '^.+\.utf-?8$'`

        if (${utf8_used} == 0) then
            switch (${LANG})
                case en_IN*:
                    breaksw
                case ja*:
                case ko*:
                case si*:
                case zh*:
                case ar*:
                case fa*:
                case he*:
                case *_IN*:
                    setenv LANG en_US.UTF-8
                    breaksw
            endsw
        else
            switch (${LANG})
                case en_IN*:
                    breaksw
                case ja*:
                case ko*:
                case si*:
                case zh*:
                case ar*:
                case fa*:
                case he*:
                case *_IN*:
                    setenv LANG en_US
                    breaksw
            endsw
        endif

        # NOTE: We are not exporting the ${LANG} here again on purpose.
        #       If user starts GUI session from console manually, then
        #       the previously set LANG should be okay to use.
    endif
endif

unset in_console utf8_used
_end_snippet_9_lang_csh:

# ============================================================================
# Snippet: less.csh
# ============================================================================
# less initialization script (csh)

# All less.*sh files should have the same semantics!

# In case you are curious, the test for non-emptiness is not as easy as in
# Bourne shell.  This "eval" construct is probably inspired by Stack
# Overflow question 13343392.
if ( $?LESSOPEN && { eval 'test ! -z "$LESSOPEN"' } ) then
    :
else
    if ( -x /usr/bin/lesspipe.sh ) then
        # The '||' here is intentional, see rhbz#1254837.
        setenv LESSOPEN "||/usr/bin/lesspipe.sh %s"
    endif
endif
_end_snippet_10_less_csh:

# ============================================================================
# Snippet: vte.csh
# ============================================================================
# Copyright © 2019 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Red Hat Author(s): Carlos Santos

# goto _end_snippet_11_vte_csh if non-interactive, csh, no terminal or old VTE versions
if ( ! $?prompt | ! $?tcsh | ! $?TERM | ! $?VTE_VERSION ) goto _end_snippet_11_vte_csh

switch($TERM)
  case xterm*:
    alias precmd 'echo -n "\e]7;file://$HOST"; /usr/libexec/vte-urlencode-cwd; echo -n "\e\\"'
endsw
_end_snippet_11_vte_csh:

# ============================================================================
# Snippet: which2.csh
# ============================================================================
# Initialization script for csh

# alias which 'alias | /usr/bin/which --tty-only --read-alias --show-dot --show-tilde'
_end_snippet_12_which2_csh:
