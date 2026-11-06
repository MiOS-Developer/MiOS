#!/usr/bin/env bash
# AI-hint: Exercises headless Flatpak installation, failure propagation and browser defaults with a real private D-Bus session and isolated command fixtures. The default browser is MiOS-Portal (native Gecko, not a Flatpak): the SSOT names it in every default-browser key, no [[desktop.apps]] browser carries default=true, and the real resolvers (mios-app-type, mios-launch, mios-open-url) dispatch to it. The freedesktop layer agrees: gio and xdg-mime, in isolated XDG dirs holding exported Zen/Epiphany/Firefox Flatpaks and Fedora's vendor mimeapps lists, resolve http/https/html to mios-portal.desktop through the projected etc/xdg/mimeapps.list, and six planted regressions each fail that guard.
# AI-related: automation/61-flatpak-bake.sh, etc/xdg/mimeapps.list, usr/share/applications/mios-portal.desktop, usr/libexec/mios/mios-hermes-browser, usr/libexec/mios/mios-app-type, usr/libexec/mios/mios-launch, usr/libexec/mios/mios-open-url, usr/share/mios/mios.toml
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
# fd_fixture <dir> <applications dir> [etc/xdg dir]: a session's XDG dirs. Data dirs are in Fedora's
# flatpak profile order (user exports, system exports, /usr/share); the system dir also holds the
# two vendor lists Fedora's RPMs put next to MiOS's launchers.
fd_fixture() {
    local r="$1" app
    mkdir -p "$r/home" "$r/config" "$r/data" "$r/etc-xdg" "$r/user-flatpak/share/applications" \
        "$r/flatpak/share/applications" "$r/usr/share/applications"
    cp "$2"/*.desktop "$r/usr/share/applications/"
    [ -z "${3:-}" ] || cp -R "$3/." "$r/etc-xdg/"
    for app in app.zen_browser.zen org.gnome.Epiphany org.mozilla.firefox; do
        printf '[Desktop Entry]\nType=Application\nName=%s\nExec=/usr/bin/flatpak run %s @@u %%u @@\nMimeType=text/html;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;\nX-Flatpak=%s\n' \
            "$app" "$app" "$app" > "$r/flatpak/share/applications/$app.desktop"
    done
    printf '[Default Applications]\n' > "$r/usr/share/applications/mimeapps.list"
    printf '[Default Applications]\n' > "$r/usr/share/applications/gnome-mimeapps.list"
    for type in $FD_TYPES; do
        printf '%s=org.mozilla.firefox.desktop\n' "$type" >> "$r/usr/share/applications/mimeapps.list"
        printf '%s=org.gnome.Epiphany.desktop\n' "$type" >> "$r/usr/share/applications/gnome-mimeapps.list"
    done
    update-desktop-database -q "$r/flatpak/share/applications"
    update-desktop-database -q "$r/usr/share/applications"
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
    local r="$1" apps="$1/usr/share/applications" entry de tool type got
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
    [ -f "$r/etc-xdg/mimeapps.list" ] || { echo "guard: etc/xdg/mimeapps.list is not shipped"; return 1; }
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
echo 'PASS gio and xdg-mime resolve http, https and html to mios-portal.desktop over exported Flatpaks and the Fedora lists (GNOME, Hyprland, none)'
# Planted: each one alone must make the guard fail.
# fd_plant <name> <expected guard finding>: plant_<name> mutates a copy of the shipped layer.
fd_plant() {
    local name="$1" expect="$2" out
    rm -rf "$FDT/$name"
    fd_fixture "$FDT/$name" "$ROOT/usr/share/applications" "$ROOT/etc/xdg"
    "plant_$name" "$FDT/$name"
    if out="$(fd_guard "$FDT/$name")"; then
        echo "FAIL planted $name still passed the freedesktop guard" >&2; exit 1
    fi
    case "$out" in *"$expect"*) ;; *) echo "FAIL planted $name failed for another reason: $out" >&2; exit 1 ;; esac
    echo "PASS planted $name fails the freedesktop guard ($out)"
}
plant_zen_flatpak_default() { sed -i -E 's|^(x-scheme-handler/https?)=.*|\1=app.zen_browser.zen.desktop;|' "$1/etc-xdg/mimeapps.list"; }
plant_zen_launcher_default() { sed -i 's|^text/html=.*|text/html=mios-zen.desktop;|' "$1/etc-xdg/mimeapps.list"; }
plant_no_default_list() { rm -f "$1/etc-xdg/mimeapps.list"; }
plant_zen_launcher_mime() {
    printf 'MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;\n' >> "$1/usr/share/applications/mios-zen.desktop"
    update-desktop-database -q "$1/usr/share/applications"
}
plant_portal_without_url() { sed -i 's|^Exec=\(.*\) %u$|Exec=\1|' "$1/usr/share/applications/mios-portal.desktop"; }
plant_portal_without_mime() {
    sed -i '/^MimeType=/d' "$1/usr/share/applications/mios-portal.desktop"
    update-desktop-database -q "$1/usr/share/applications"
}
fd_plant zen_flatpak_default "gio picks 'app.zen_browser.zen.desktop' for x-scheme-handler/http "
fd_plant zen_launcher_default "gio picks 'mios-zen.desktop' for text/html "
fd_plant no_default_list 'etc/xdg/mimeapps.list is not shipped'
fd_plant zen_launcher_mime 'mios-zen.desktop declares x-scheme-handler/http'
fd_plant portal_without_url 'mios-portal.desktop Exec has no %u'
fd_plant portal_without_mime 'mios-portal.desktop does not declare x-scheme-handler/http'
# Without the projected list a session opens an exported Flatpak, never MiOS-Portal.
for de in '' GNOME; do
    got="$(fd_default "$FDT/no_default_list" "$de" gio x-scheme-handler/https)"
    [ -n "$got" ] && [ "$got" != mios-portal.desktop ] || { echo "FAIL without etc/xdg/mimeapps.list gio picked '${got:-nothing}'" >&2; exit 1; }
    echo "PASS without etc/xdg/mimeapps.list gio picks $got (XDG_CURRENT_DESKTOP='$de'), which is why the list ships"
done
