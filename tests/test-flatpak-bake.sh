#!/usr/bin/env bash
# AI-hint: Exercises headless Flatpak installation, failure propagation and browser defaults with a real private D-Bus session and isolated command fixtures.
# AI-related: automation/61-flatpak-bake.sh, usr/libexec/mios/mios-hermes-browser, usr/share/mios/mios.toml
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
python3 - "$ROOT/usr/share/mios/mios.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], 'rb') as source:
    config = tomllib.load(source)
apps = config['desktop']['flatpaks']
assert 'com.google.ChromeDev' in apps
assert 'org.chromium.Chromium' not in apps
assert next(p for p in config['build']['phases']['list'] if p['name'] == 'flatpak-bake')['fatal']
assert next(a for a in config['desktop']['apps'] if a['id'] == 'org.gnome.Epiphany')['default']
print('PASS SSOT keeps Epiphany primary, Chrome secondary, optional Chromium and fatal bake failures')
PY
