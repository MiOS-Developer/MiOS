#!/usr/bin/env bash
# AI-hint: Exercises headless Flatpak installation, failure propagation and browser defaults with a real private D-Bus session and isolated command fixtures. The default browser is MiOS-Portal (native Gecko, not a Flatpak): the SSOT names it in every default-browser key, no [[desktop.apps]] browser carries default=true, and the real resolvers (mios-app-type, mios-launch, mios-open-url) dispatch to it. The freedesktop layer agrees: gio and xdg-mime, in isolated XDG dirs holding exported Zen/Epiphany/Firefox Flatpaks and Fedora's vendor mimeapps lists, resolve http/https/html to mios-portal.desktop through the projected etc/xdg/mimeapps.list. The session copies of the shipped entries Exec executable stubs inside the fixture (GLib skips an entry whose program is missing) and differ from the shipped bytes in nothing else, so the result never depends on the host's installed mios-portal, mios-flatpak-run or flatpak; a separate check requires the tree itself to ship the portal's Exec program as an executable file. Twelve planted regressions fail that guard (Zen named or listed first, stray gnome-/hyprland-mimeapps.list, MIME types moved, no %u, Hidden=true, a [Removed Associations] line, the pre-portal layer) and three fail the shipped-program check.
# AI-related: automation/61-flatpak-bake.sh, etc/xdg/mimeapps.list, usr/share/applications/mios-portal.desktop, usr/bin/mios-portal, tools/native/mios-browser/src/main.rs, usr/libexec/mios/mios-hermes-browser, usr/libexec/mios/mios-app-type, usr/libexec/mios/mios-launch, usr/libexec/mios/mios-open-url, usr/share/mios/mios.toml
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/automation/lib" "$TMP/bin" "$TMP/share/vendored"
cp "$ROOT/automation/61-flatpak-bake.sh" "$TMP/automation/"
cat > "$TMP/automation/lib/common.sh" <<'EOF'
mios_log() { echo "$*"; }
mios_ok() { echo "OK $*"; }
mios_warn() { echo "WARN $*"; }
mios_err() { echo "ERROR $*"; }
mios_skip() { echo "SKIP $*"; }
export MIOS_DESKTOP_FLATPAKS=org.example.ResolverDefault
EOF
cat > "$TMP/bin/flatpak" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$MIOS_TEST_FLATPAK_LOG"
case "$1" in
    remote-list) printf 'flathub\nfedora\n'; exit 0 ;;
    remote-add) exit "${MIOS_TEST_REMOTE_RC:-0}" ;;
    install)
        # The real daemon must answer without DISPLAY; a dummy address is insufficient.
        dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames >/dev/null
        if [[ "$*" == *"${MIOS_TEST_FAILED_APP:-never-fail}"* ]]; then
            echo 'bsdtar: planted extraction diagnostic'
            echo 'Error: apply_extra script failed, exit status 256'
            exit 1
        fi
        printf 'Installing %s\n' "${@: -1}"
        ;;
    run) printf '%s\n' "$@" > "$MIOS_TEST_BROWSER_LOG" ;;
    *) exit 1 ;;
esac
EOF
chmod +x "$TMP/bin/flatpak"
export PATH="$TMP/bin:$PATH" MIOS_TEST_FLATPAK_LOG="$TMP/calls"
export MIOS_USR_DIR="$TMP/lib" MIOS_SHARE_DIR="$TMP/share"
export MIOS_DESKTOP_FLATPAKS='fedora:org.gnome.Epiphany,com.google.ChromeDev'
unset DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS
bash "$TMP/automation/61-flatpak-bake.sh" > "$TMP/success" 2>&1
grep -q 'OK 2 refs installed, 0 failed' "$TMP/success"
grep -q 'MIOS_FLATPAK_BAKE_INSTALLED=2' "$TMP/lib/state/flatpak-bake.env"
echo 'PASS headless Fedora and Chrome installs use a real session bus'
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent/planted-stale-bus
bash "$TMP/automation/61-flatpak-bake.sh" > "$TMP/stale" 2>&1
echo 'PASS inherited stale desktop bus is replaced'
MIOS_DESKTOP_FLATPAKS='' bash "$TMP/automation/61-flatpak-bake.sh" > "$TMP/empty" 2>&1
grep -q 'SKIP no Flatpaks selected' "$TMP/empty"
echo 'PASS explicit empty selection overrides resolver defaults'
if MIOS_TEST_FAILED_APP=com.google.ChromeDev bash "$TMP/automation/61-flatpak-bake.sh" > "$TMP/failure" 2>&1; then
    echo 'FAIL failed Chrome install returned success' >&2; exit 1
fi
grep -q 'bsdtar: planted extraction diagnostic' "$TMP/failure"
grep -q 'ERROR 1 refs installed, 1 failed' "$TMP/failure"
! grep -q '^OK ' "$TMP/failure"
grep -q 'MIOS_FLATPAK_BAKE_FAILED=1' "$TMP/lib/state/flatpak-bake.env"
echo 'PASS Chrome extra-data failure is nonzero, retains stderr and has no success claim'
if MIOS_DESKTOP_FLATPAKS=gnome-nightly:org.gnome.Nautilus.Devel MIOS_TEST_REMOTE_RC=7 bash "$TMP/automation/61-flatpak-bake.sh" > "$TMP/remote" 2>&1; then
    echo 'FAIL remote setup error returned success' >&2; exit 1
fi
echo 'PASS remote creation failure propagates'
mkdir -p "$TMP/share/vendored directory"
export MIOS_SHARE_DIR="$TMP/share/vendored directory"
mkdir -p "$MIOS_SHARE_DIR/vendored"
touch "$MIOS_SHARE_DIR/vendored/com.google.ChromeDev.flatpak"
MIOS_DESKTOP_FLATPAKS=com.google.ChromeDev bash "$TMP/automation/61-flatpak-bake.sh" > "$TMP/local" 2>&1
echo 'PASS local bundle path with spaces installs through quoted argv'
export MIOS_TEST_BROWSER_LOG="$TMP/browser-argv"
export HERMES_BROWSER_PROFILE_DIR="$TMP/profile" HERMES_BROWSER_LOG="$TMP/browser.log"
unset HERMES_BROWSER_APP_ID MIOS_CHROME_FLATPAK_ID
bash "$ROOT/usr/libexec/mios/mios-hermes-browser" start >/dev/null
grep -Fxq com.google.ChromeDev "$TMP/browser-argv"
grep -Fxq -- '--remote-debugging-address=127.0.0.1' "$TMP/browser-argv"
HERMES_BROWSER_APP_ID=org.chromium.Chromium bash "$ROOT/usr/libexec/mios/mios-hermes-browser" start >/dev/null
grep -Fxq org.chromium.Chromium "$TMP/browser-argv"
echo 'PASS Chrome Dev CDP default and explicit browser override'
python3 - "$ROOT" <<'PY'
import os, sys, tomllib
root = sys.argv[1]
with open(os.path.join(root, 'usr/share/mios/mios.toml'), 'rb') as source:
    config = tomllib.load(source)
apps = config['desktop']['flatpaks']
assert 'com.google.ChromeDev' in apps
assert 'org.chromium.Chromium' not in apps
assert next(p for p in config['build']['phases']['list'] if p['name'] == 'flatpak-bake')['fatal']
# MiOS-Portal, the native Gecko build with the MiOS desktop app, is the ONE default browser.
portal = config['desktop']['portal']['launcher']
assert portal == config['desktop']['portal']['gecko']['binary'] == 'mios-portal', portal
assert portal in config['desktop']['launchers'], portal
assert os.path.isfile(os.path.join(root, 'usr/share/applications', portal + '.desktop'))
assert config['aliases']['browser'] == portal, config['aliases']['browser']
assert config.get('browser', {}).get('default') in (None, portal), config['browser'].get('default')
assert config['mios-find']['aliases']['web'] == portal, config['mios-find']['aliases']['web']
typed = [t for t in config['desktop']['app_types'] if t.get('type') == 'browser']
assert typed and all((t.get('linux_default') or t.get('default')) == portal for t in typed), typed
assert portal in config['browser']['family']['firefox']
# It is not a Flatpak, so the flatpak list carries no browser default; the secondary browsers stay installed.
browsers = {a['id']: a for a in config['desktop']['apps'] if a.get('role') == 'browser'}
assert portal not in browsers and not any(f.split(':')[-1] == portal for f in apps)
assert [i for i, a in browsers.items() if a.get('default') is not False] == [], browsers
for app_id in ('app.zen_browser.zen', 'org.gnome.Epiphany', 'com.google.ChromeDev'):
    assert app_id in browsers and any(f.split(':')[-1] == app_id for f in apps), app_id
print('PASS SSOT names MiOS-Portal the one default browser; Zen, Epiphany and Chrome Dev stay secondary flatpaks')
PY
# The real resolvers, against the vendor SSOT of this tree only (no host or user layer).
NOLAYER="$TMP/no-layer"
OPEN_BIN="$TMP/open-bin"
mkdir -p "$OPEN_BIN"
export MIOS_TOML_ROOT="$ROOT" MIOS_HOST_TOML="$NOLAYER/host.toml" MIOS_HOST_TOML_D="$NOLAYER/host.d" \
    MIOS_USER_TOML="$NOLAYER/user.toml" MIOS_USER_TOML_D="$NOLAYER/user.d" MIOS_RESOLVER_NATIVE=0 MIOS_DB_AUTHORITATIVE=0
unset MIOS_BROWSER MIOS_VENDOR_TOML MIOS_VENDOR_TOML_D MIOS_TOML
[ "$(bash "$ROOT/usr/libexec/mios/mios-app-type" browser)" = mios-portal ]
sed -n "/^_alias_resolve() {/,/^PYEOF/p" "$ROOT/usr/libexec/mios/mios-launch" | sed '1,2d;$d' > "$TMP/launch-alias.py"
grep -q 'role_app' "$TMP/launch-alias.py"
for ask in browser browsers web; do
    [ "$(python3 "$TMP/launch-alias.py" "$ROOT/usr/lib/mios" "$ask")" = mios-portal ] || { echo "FAIL mios-launch '$ask' does not resolve to mios-portal" >&2; exit 1; }
done
[ "$(python3 "$TMP/launch-alias.py" "$ROOT/usr/lib/mios" 'zen browser')" = zen ]
awk '/<<.PYEOF. 2>\/dev\/null \|\| true$/{f=1;next} f&&/^PYEOF$/{exit} f' "$ROOT/usr/libexec/mios/mios-open-url" > "$TMP/open-flags.py"
grep -q 'fam_map' "$TMP/open-flags.py"
[ "$(python3 "$TMP/open-flags.py" "$ROOT/usr/lib/mios" mios-portal tab)" = --new-tab ]
[ "$(python3 "$TMP/open-flags.py" "$ROOT/usr/lib/mios" mios-portal private)" = --private-window ]
# Only the resolver under test is real; the GUI dispatcher, flag expander and Windows bridge are fixtures.
printf '#!/bin/sh\nexec bash "%s/usr/libexec/mios/mios-app-type" "$@"\n' "$ROOT" > "$OPEN_BIN/mios-app-type"
printf '#!/bin/sh\nprintf "Flags: --new-tab\\n"\n' > "$OPEN_BIN/mios-browser"
printf '#!/bin/sh\nprintf "%%s\\n" "$@" > "$MIOS_TEST_GUI_LOG"\nexit 0\n' > "$OPEN_BIN/mios-gui"
printf '#!/bin/sh\nexit 1\n' > "$OPEN_BIN/mios-windows"
chmod +x "$OPEN_BIN/mios-app-type" "$OPEN_BIN/mios-browser" "$OPEN_BIN/mios-gui" "$OPEN_BIN/mios-windows"
export MIOS_TEST_GUI_LOG="$TMP/gui-argv"
open_rc=0
PATH="$OPEN_BIN:$PATH" bash "$ROOT/usr/libexec/mios/mios-open-url" https://example.invalid/default-browser \
    > "$TMP/open.json" 2> "$TMP/open.err" || open_rc=$?
[ "$open_rc" = 3 ] || { echo "FAIL mios-open-url exited $open_rc" >&2; cat "$TMP/open.err" >&2; exit 1; }
[ "$(grep -m1 'trying browser:' "$TMP/open.err")" = '[mios-open-url] trying browser: mios-portal' ]
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["target"] == "mios-portal" and d["navigation_verified"] is False, d' "$TMP/open.json"
[ "$(cat "$TMP/gui-argv")" = "$(printf 'mios-portal\n--new-tab\nhttps://example.invalid/default-browser')" ]
echo 'PASS mios-app-type, mios-launch and mios-open-url resolve the default browser to MiOS-Portal'

# The freedesktop layer: what xdg-open, gio open and the OpenURI portal pick. Every MiOS key above
# can name MiOS-Portal and a session still opens Zen, because exported Flatpaks declare their own
# http/https/html associations and Fedora ships mimeapps.list and gnome-mimeapps.list defaults.
python3 - "$ROOT" <<'PY'
import copy, os, sys, tomllib
root = sys.argv[1]
with open(os.path.join(root, 'usr/share/mios/mios.toml'), 'rb') as source:
    shipped = tomllib.load(source)['desktop']
BROWSER = {'text/html', 'application/xhtml+xml', 'x-scheme-handler/http', 'x-scheme-handler/https'}
def check(desktop):
    portal = desktop['portal']['launcher']
    launchers = desktop['launchers']
    types = lambda name: {t.strip() for t in launchers[name].get('mime_type', '').split(';') if t.strip()}
    assert BROWSER <= types(portal), types(portal)
    assert '%u' in launchers[portal]['exec_cmd'].split(), launchers[portal]['exec_cmd']
    assert desktop['default_applications']['launchers'][0] == portal, desktop['default_applications']
    assert 'mios-zen' in launchers and 'mime_type' not in launchers['mios-zen'], launchers['mios-zen']
    rivals = {n: sorted(types(n) & types(portal)) for n in launchers if n != portal and types(n) & types(portal)}
    assert not rivals, rivals
check(shipped)
print('PASS SSOT gives the browser MIME types and %u to MiOS-Portal alone and names it in [desktop.default_applications]')
def zen_first(d): d['default_applications']['launchers'] = ['mios-zen'] + d['default_applications']['launchers']
def zen_mime(d): d['launchers']['mios-zen']['mime_type'] = 'x-scheme-handler/http;x-scheme-handler/https;'
def portal_no_url(d): d['launchers'][d['portal']['launcher']]['exec_cmd'] = '/usr/bin/mios-portal'
def portal_no_https(d):
    entry = d['launchers'][d['portal']['launcher']]
    entry['mime_type'] = entry['mime_type'].replace('x-scheme-handler/https;', '')
def rival_html(d): d['launchers']['mios-terminal']['mime_type'] = 'text/html;'
for plant in (zen_first, zen_mime, portal_no_url, portal_no_https, rival_html):
    planted = copy.deepcopy(shipped)
    plant(planted)
    try:
        check(planted)
    except AssertionError:
        print(f'PASS planted SSOT {plant.__name__} fails the check')
    else:
        raise SystemExit(f'FAIL planted SSOT {plant.__name__} passed the check')
PY
for tool in gio xdg-mime update-desktop-database; do
    command -v "$tool" >/dev/null 2>&1 || { echo "FAIL $tool is not installed, so the freedesktop default cannot be resolved" >&2; exit 77; }
done
FD_PATH="$(dirname "$(command -v gio)"):$(dirname "$(command -v xdg-mime)"):/usr/bin:/bin"
FD_TYPES='x-scheme-handler/http x-scheme-handler/https text/html application/xhtml+xml'
# fd_ships_portal <tree>: the session tier below runs MiOS-Portal's entry against a stub, so the
# program it Execs is checked here, in the tree: shipped, as an executable file, at the absolute path
# the [Desktop Entry] Exec names. Prints that path, or the finding.
fd_ships_portal() {
    local entry="$1/usr/share/applications/mios-portal.desktop" prog
    [ -f "$entry" ] || { echo "ships: mios-portal.desktop is not shipped"; return 1; }
    prog="$(awk '/^\[/ { main = ($0 == "[Desktop Entry]"); next } main && /^Exec=/ { sub(/^Exec=/, ""); sub(/[ \t].*/, ""); print; exit }' "$entry")"
    case "$prog" in /*) ;; *) echo "ships: mios-portal.desktop Execs '$prog', not an absolute path"; return 1 ;; esac
    if [ ! -f "$1$prog" ] || [ ! -x "$1$prog" ]; then
        echo "ships: mios-portal.desktop Execs $prog, which the tree does not ship as an executable file"; return 1
    fi
    printf '%s\n' "$prog"
}
PORTAL_PROG="$(fd_ships_portal "$ROOT")" || { echo "FAIL $PORTAL_PROG" >&2; exit 1; }
echo "PASS the tree ships $PORTAL_PROG, the program mios-portal.desktop Execs, as an executable file"
# ships_plant <name> <expected finding>: ships_<name> mutates a minimal copy of the tree that passes first.
ships_plant() {
    local name="$1" expect="$2" t="$TMP/ships/$1" out
    mkdir -p "$t/usr/share/applications" "$t${PORTAL_PROG%/*}"
    cp "$ROOT/usr/share/applications/mios-portal.desktop" "$t/usr/share/applications/"
    cp -p "$ROOT$PORTAL_PROG" "$t$PORTAL_PROG"
    out="$(fd_ships_portal "$t")" || { echo "FAIL the unplanted copy for $name fails the shipped-program check: $out" >&2; exit 1; }
    "ships_$name" "$t"
    if out="$(fd_ships_portal "$t")"; then
        echo "FAIL planted $name still passed the shipped-program check ($out)" >&2; exit 1
    fi
    case "$out" in *"$expect"*) ;; *) echo "FAIL planted $name failed for another reason: $out" >&2; exit 1 ;; esac
    echo "PASS planted $name fails the shipped-program check ($out)"
}
ships_program_missing() { rm -f "${1:?}$PORTAL_PROG"; }
ships_program_not_executable() { chmod a-x "$1$PORTAL_PROG"; }
ships_program_bare_name() { sed -i -E 's|^Exec=/[^ ]*/|Exec=|' "$1/usr/share/applications/mios-portal.desktop"; }
ships_plant program_missing "Execs $PORTAL_PROG, which the tree does not ship as an executable file"
ships_plant program_not_executable "Execs $PORTAL_PROG, which the tree does not ship as an executable file"
ships_plant program_bare_name "Execs '${PORTAL_PROG##*/}', not an absolute path"
# GLib (gio, xdg-open, the OpenURI portal) skips an entry whose Exec or TryExec program is not an
# executable file, so a session that copied the entries verbatim would test the host's installed
# mios-portal, mios-flatpak-run and flatpak rather than the tree. fd_stub <entries dir> <session dir>
# <fixture> copies each entry with its program replaced by an executable stub: an absolute program P
# becomes <fixture>/stub/P, a PATH name N <fixture>/stub-path/N. No other byte changes.
fd_stub() {
    local src="$1" dst="$2" stub="$3/stub" bare="$3/stub-path" f prog
    mkdir -p "$dst"
    rm -f "${dst:?}"/*.desktop "${dst:?}/mimeinfo.cache"
    for f in "$src"/*.desktop; do
        sed -E -e "s|^((Try)?Exec=)/|\1$stub/|" -e t -e "s|^((Try)?Exec=)([^/\" ])|\1$bare/\3|" "$f" > "$dst/${f##*/}"
    done
    # Every Exec and TryExec GLib would read now names a stub...
    if grep -HE '^[[:space:]]*(Try)?Exec[[:space:]]*=' "$dst"/*.desktop | grep -vE "^[^:]*:(Try)?Exec=($stub|$bare)/"; then
        echo "guard: the session entries above would still run a host program"; return 1
    fi
    # ...which is an executable file here...
    while IFS= read -r prog; do
        if ! { mkdir -p "${prog%/*}" && printf '#!/bin/sh\nexit 0\n' > "$prog" && chmod 0755 "$prog"; }; then
            echo "guard: cannot create the stub $prog"; return 1
        fi
    done < <(sed -n -E 's/^(Try)?Exec=([^ ]*).*/\2/p' "$dst"/*.desktop | sort -u)
    # ...and each copy is the source entry byte for byte once the stub prefix is taken off again.
    for f in "$src"/*.desktop; do
        sed -E -e "s|^((Try)?Exec=)$bare/|\1|" -e "s|^((Try)?Exec=)$stub/|\1/|" "$dst/${f##*/}" | cmp -s - "$f" \
            || { echo "guard: the session copy of ${f##*/} differs from it beyond the program path"; return 1; }
    done
    update-desktop-database -q "$dst"
}
# fd_session <fixture>: rebuild the session's applications dirs from <fixture>/shipped and <fixture>/exports.
fd_session() {
    rm -rf "${1:?}/stub" "${1:?}/stub-path"
    fd_stub "$1/shipped/applications" "$1/usr/share/applications" "$1" \
        && fd_stub "$1/exports" "$1/flatpak/share/applications" "$1"
}
# fd_fixture <dir> <applications dir> [etc/xdg dir]: a session's XDG dirs. <dir>/shipped holds the
# tree's entries byte for byte; plants edit them there, and every Exec, %u, MimeType and Hidden check
# reads them. <dir>/exports holds what `flatpak install` exports for Zen, Epiphany and Firefox. Data
# dirs are in Fedora's flatpak profile order (user exports, system exports, /usr/share); the system dir
# also holds the two vendor lists Fedora's RPMs put next to MiOS's launchers.
fd_fixture() {
    local r="$1" app type
    case "$r" in *[!A-Za-z0-9/._-]*) echo "FAIL fixture path $r would need Exec quoting; use a plain TMPDIR" >&2; exit 1 ;; esac
    mkdir -p "$r/home" "$r/config" "$r/data" "$r/etc-xdg" "$r/shipped/applications" "$r/exports" \
        "$r/user-flatpak/share/applications" "$r/flatpak/share/applications" "$r/usr/share/applications"
    cp "$2"/*.desktop "$r/shipped/applications/"
    [ -z "${3:-}" ] || cp -R "$3/." "$r/etc-xdg/"
    for app in app.zen_browser.zen org.gnome.Epiphany org.mozilla.firefox; do
        printf '[Desktop Entry]\nType=Application\nName=%s\nExec=/usr/bin/flatpak run %s @@u %%u @@\nMimeType=text/html;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;\nX-Flatpak=%s\n' \
            "$app" "$app" "$app" > "$r/exports/$app.desktop"
    done
    printf '[Default Applications]\n' > "$r/usr/share/applications/mimeapps.list"
    printf '[Default Applications]\n' > "$r/usr/share/applications/gnome-mimeapps.list"
    for type in $FD_TYPES; do
        printf '%s=org.mozilla.firefox.desktop\n' "$type" >> "$r/usr/share/applications/mimeapps.list"
        printf '%s=org.gnome.Epiphany.desktop\n' "$type" >> "$r/usr/share/applications/gnome-mimeapps.list"
    done
}
# fd_default <dir> <XDG_CURRENT_DESKTOP or ""> <gio|xdg-mime> <type>: the desktop ID that tool picks.
fd_default() {
    local r="$1" line
    local -a session=(HOME="$r/home" PATH="$FD_PATH" LANG=C.UTF-8 XDG_CONFIG_HOME="$r/config"
        XDG_DATA_HOME="$r/data" XDG_CONFIG_DIRS="$r/etc-xdg"
        XDG_DATA_DIRS="$r/user-flatpak/share:$r/flatpak/share:$r/usr/share")
    [ -z "$2" ] || session+=(XDG_CURRENT_DESKTOP="$2")
    if [ "$3" = gio ]; then
        line="$(env -i "${session[@]}" gio mime "$4" 2>/dev/null | head -n 1)"
        case "$line" in "Default application for "*": "*) printf '%s\n' "${line##*: }" ;; esac
    else
        env -i "${session[@]}" xdg-mime query default "$4" 2>/dev/null || true
    fi
}
# fd_guard <dir>: 0 only when the shipped entries and lists make MiOS-Portal the default everywhere.
fd_guard() {
    local r="$1" apps="$1/shipped/applications" entry list de tool type got
    fd_session "$r" || return 1
    entry="$apps/mios-portal.desktop"
    [ -f "$entry" ] || { echo "guard: mios-portal.desktop is not shipped"; return 1; }
    grep -Eq '^Exec=[^ ]+( .*)? %u( |$)' "$entry" || { echo "guard: mios-portal.desktop Exec has no %u"; return 1; }
    for type in $FD_TYPES; do
        grep -E '^MimeType=' "$entry" | tr ';=' '\n\n' | grep -Fxq "$type" \
            || { echo "guard: mios-portal.desktop does not declare $type"; return 1; }
        if grep -E '^MimeType=' "$apps/mios-zen.desktop" | tr ';=' '\n\n' | grep -Fxq "$type"; then
            echo "guard: mios-zen.desktop declares $type"; return 1
        fi
    done
    # GLib still resolves a hidden entry as the default, but mios-gui hands it to mios-browser, which refuses it.
    if awk '/^\[/ { main = ($0 == "[Desktop Entry]"); next } main && /^Hidden[ \t]*=[ \t]*true[ \t]*$/ { hidden = 1 } END { exit !hidden }' "$entry"; then
        echo "guard: mios-portal.desktop is Hidden=true, which mios-browser refuses to launch"; return 1
    fi
    [ -f "$r/etc-xdg/mimeapps.list" ] || { echo "guard: etc/xdg/mimeapps.list is not shipped"; return 1; }
    # GLib still returns a [Default Applications] entry its list disowns under [Removed Associations] (the
    # removal only drops it from the registered and recommended apps), so gio alone cannot catch this.
    for list in "$r"/etc-xdg/*mimeapps.list; do
        if awk '/^\[/ { removed = ($0 ~ /^\[Removed Associations\][ \t]*$/); next }
                removed && /=/ { sub(/^[^=]*=/, ""); n = split($0, ids, ";")
                    for (i = 1; i <= n; i++) { gsub(/^[ \t]+|[ \t]+$/, "", ids[i]); if (ids[i] == "mios-portal.desktop") found = 1 } }
                END { exit !found }' "$list"; then
            echo "guard: ${list##*/} lists mios-portal.desktop under [Removed Associations]"; return 1
        fi
    done
    for de in '' GNOME Hyprland; do
        for tool in gio xdg-mime; do
            for type in $FD_TYPES; do
                got="$(fd_default "$r" "$de" "$tool" "$type")"
                [ "$got" = mios-portal.desktop ] \
                    || { echo "guard: $tool picks '${got:-nothing}' for $type (XDG_CURRENT_DESKTOP='$de')"; return 1; }
            done
        done
    done
}
FDT="$TMP/freedesktop"
fd_fixture "$FDT/shipped" "$ROOT/usr/share/applications" "$ROOT/etc/xdg"
fd_guard "$FDT/shipped" || { echo 'FAIL the shipped freedesktop layer does not make MiOS-Portal the default browser' >&2; exit 1; }
echo 'PASS gio and xdg-mime resolve http, https and html to mios-portal.desktop over exported Flatpaks and the Fedora lists (GNOME, Hyprland, none), with every Exec program a fixture stub'
# Planted: each one alone must make the guard fail.
# fd_plant <name> <expected guard finding>: plant_<name> mutates a copy of the shipped layer.
fd_plant() {
    local name="$1" expect="$2" out
    rm -rf "${FDT:?}/${name:?}"
    fd_fixture "$FDT/$name" "$ROOT/usr/share/applications" "$ROOT/etc/xdg"
    "plant_$name" "$FDT/$name"
    if out="$(fd_guard "$FDT/$name")"; then
        echo "FAIL planted $name still passed the freedesktop guard" >&2; exit 1
    fi
    case "$out" in *"$expect"*) ;; *) echo "FAIL planted $name failed for another reason: $out" >&2; exit 1 ;; esac
    echo "PASS planted $name fails the freedesktop guard ($out)"
}
FD_ZEN_MIME='MimeType=text/html;text/xml;application/xhtml+xml;application/xml;application/rss+xml;application/rdf+xml;application/pdf;x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/mailto;'
fd_stray_list() {
    local type
    printf '[Default Applications]\n' > "$1"
    for type in $FD_TYPES; do printf '%s=app.zen_browser.zen.desktop;\n' "$type" >> "$1"; done
}
plant_zen_flatpak_default() { sed -i -E 's|^(x-scheme-handler/https?)=.*|\1=app.zen_browser.zen.desktop;|' "$1/etc-xdg/mimeapps.list"; }
plant_zen_listed_first() { sed -i -E 's#^(x-scheme-handler/https?|text/html|application/xhtml\+xml)=#\1=app.zen_browser.zen.desktop;#' "$1/etc-xdg/mimeapps.list"; }
plant_zen_launcher_default() { sed -i 's|^text/html=.*|text/html=mios-zen.desktop;|' "$1/etc-xdg/mimeapps.list"; }
plant_no_default_list() { rm -f "$1/etc-xdg/mimeapps.list"; }
plant_stray_gnome_list() { fd_stray_list "$1/etc-xdg/gnome-mimeapps.list"; }
plant_stray_hyprland_list() { fd_stray_list "$1/etc-xdg/hyprland-mimeapps.list"; }
plant_zen_launcher_mime() { sed -i 's|^\[Desktop Entry\]$|&\nMimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;|' "$1/shipped/applications/mios-zen.desktop"; }
plant_portal_without_url() { sed -i 's|^Exec=\(.*\) %u$|Exec=\1|' "$1/shipped/applications/mios-portal.desktop"; }
plant_portal_without_mime() { sed -i '/^MimeType=/d' "$1/shipped/applications/mios-portal.desktop"; }
plant_portal_hidden() { sed -i 's|^\[Desktop Entry\]$|&\nHidden=true|' "$1/shipped/applications/mios-portal.desktop"; }
plant_portal_removed_association() { printf '\n[Removed Associations]\nx-scheme-handler/https=mios-portal.desktop;\n' >> "$1/etc-xdg/mimeapps.list"; }
# The layer before MiOS-Portal took the browser types: mios-zen.desktop declared them, the portal's
# Exec took no URL and it declared none, and no list shipped.
plant_pre_portal_layer() {
    sed -i "s|^\[Desktop Entry\]\$|&\n$FD_ZEN_MIME|" "$1/shipped/applications/mios-zen.desktop"
    plant_portal_without_url "$1"; plant_portal_without_mime "$1"; plant_no_default_list "$1"
}
fd_plant zen_flatpak_default "gio picks 'app.zen_browser.zen.desktop' for x-scheme-handler/http (XDG_CURRENT_DESKTOP='')"
fd_plant zen_listed_first "gio picks 'app.zen_browser.zen.desktop' for x-scheme-handler/http (XDG_CURRENT_DESKTOP='')"
fd_plant zen_launcher_default "gio picks 'mios-zen.desktop' for text/html (XDG_CURRENT_DESKTOP='')"
fd_plant no_default_list 'etc/xdg/mimeapps.list is not shipped'
fd_plant stray_gnome_list "gio picks 'app.zen_browser.zen.desktop' for x-scheme-handler/http (XDG_CURRENT_DESKTOP='GNOME')"
fd_plant stray_hyprland_list "gio picks 'app.zen_browser.zen.desktop' for x-scheme-handler/http (XDG_CURRENT_DESKTOP='Hyprland')"
fd_plant zen_launcher_mime 'mios-zen.desktop declares x-scheme-handler/http'
fd_plant portal_without_url 'mios-portal.desktop Exec has no %u'
fd_plant portal_without_mime 'mios-portal.desktop does not declare x-scheme-handler/http'
fd_plant portal_hidden 'mios-portal.desktop is Hidden=true'
fd_plant portal_removed_association 'mimeapps.list lists mios-portal.desktop under [Removed Associations]'
fd_plant pre_portal_layer 'mios-portal.desktop Exec has no %u'
# Without the projected list a session opens an exported Flatpak, never MiOS-Portal; so did the layer before it.
for layer in no_default_list pre_portal_layer; do
    for de in '' GNOME Hyprland; do
        got="$(fd_default "$FDT/$layer" "$de" gio x-scheme-handler/https)"
        [ -n "$got" ] && [ "$got" != mios-portal.desktop ] || { echo "FAIL planted $layer: gio picked '${got:-nothing}' (XDG_CURRENT_DESKTOP='$de')" >&2; exit 1; }
        echo "PASS planted $layer: gio picks $got for https (XDG_CURRENT_DESKTOP='$de'), which is why the list ships"
    done
done
