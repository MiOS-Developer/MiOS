#!/usr/bin/env bash
# AI-hint: Exercises headless Flatpak installation, failure propagation and browser defaults with a real private D-Bus session and isolated command fixtures. The default browser is MiOS-Portal (native Gecko, not a Flatpak): the SSOT names it in every default-browser key, no [[desktop.apps]] browser carries default=true, and the real resolvers (mios-app-type, mios-launch, mios-open-url) dispatch to it.
# AI-related: automation/61-flatpak-bake.sh, usr/libexec/mios/mios-hermes-browser, usr/libexec/mios/mios-app-type, usr/libexec/mios/mios-launch, usr/libexec/mios/mios-open-url, usr/share/mios/mios.toml
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
