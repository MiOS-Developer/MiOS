#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Bakes the ventulusd fan-control daemon (MIT, Plan-B-Development) from the newest upstream release (or an explicit [build.bake_refs].ventulusd ref) as a static musl binary with the SSOT Rust toolchain, applies MiOS patches from usr/share/mios/fan-control/patches/, and installs /usr/bin/ventulusd, /usr/bin/ventulus-restore-auto and the system-sleep hook.
# AI-related: /usr/lib/systemd/system/ventulusd.service, /usr/lib/systemd/system/ventulusd.service.d/10-mios.conf, /usr/share/mios/fan-control/, /usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck disable=SC1090
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib/common.sh" 2>/dev/null || true
# shellcheck source=automation/lib/upstream-latest.sh
source "${SCRIPT_DIR}/lib/upstream-latest.sh"

get() { MIOS_TOML_ROOT="$ROOT_DIR" python3 "$ROOT_DIR/usr/libexec/mios/mios-toml-get" --vendor "$@"; }

if [[ "$(get hardware.fan_control enabled 2>/dev/null || echo true)" == false ]]; then
    mios_log "fan control disabled in [hardware.fan_control]; skipping ventulusd"
    exit 0
fi

URL="${MIOS_URL_VENTULUSD:-https://github.com/Plan-B-Development/ventulusd.git}"
# "latest" (or unset) floats to the newest upstream release, resolved here;
# the resolved tag + commit are build output, never written back to the SSOT.
PIN_REF="${MIOS_BUILD_BAKE_REFS_VENTULUSD:-$(get build.bake_refs ventulusd 2>/dev/null || true)}"
PIN_REF="${PIN_REF:-latest}"
CHANNEL="$(get build.toolchain channel)"
TARGET="$(get build.native.linux.targets "$(uname -m)")"
RUSTUP_HOME="$(get build.toolchain rustup_home)"
CARGO_HOME="$(get build.toolchain cargo_home)"
export RUSTUP_HOME CARGO_HOME
export PATH="${CARGO_HOME}/bin:${PATH}"
# Upstream pins its own rust-toolchain.toml channel; build with the image's.
export RUSTUP_TOOLCHAIN="$CHANNEL"
export RUSTFLAGS="-C target-feature=+crt-static"

WORK="$(mktemp -d /tmp/ventulusd-build.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
mios_log "ventulusd ref: ${PIN_REF}"
_checkout="$(mios_git_bake_checkout "$URL" "$PIN_REF" "$WORK/src")" \
    || { mios_ul_pass $? || { mios_err "ventulusd source unavailable"; exit 1; }; }
read -r PIN_REF PIN_COMMIT <<< "$_checkout"
mios_log "ventulusd resolved ${PIN_REF} = ${PIN_COMMIT} (commit verified against upstream)"
[[ -f "$WORK/src/Cargo.toml" ]] || { mios_err "ventulusd source unavailable"; exit 1; }
grep -q '^MIT License' "$WORK/src/LICENSE" || { mios_err "ventulusd LICENSE is no longer MIT -- refusing to bake"; exit 1; }

for p in "$ROOT_DIR"/usr/share/mios/fan-control/patches/*.patch; do
    [[ -f "$p" ]] || continue
    mios_log "applying $(basename "$p")"
    git -C "$WORK/src" apply --verbose "$p" \
        || { mios_err "MiOS patch $(basename "$p") no longer applies to ventulusd ${PIN_REF} (${PIN_COMMIT}); refresh it"; exit 1; }
done

(cd "$WORK/src" && cargo build --release --locked -p ventulusd --target "$TARGET")
BIN="$WORK/src/target/${TARGET}/release/ventulusd"
"$BIN" --version
install -D -m 0755 "$BIN" /usr/bin/ventulusd
install -D -m 0755 "$WORK/src/packaging/ventulus-restore-auto.sh" /usr/bin/ventulus-restore-auto
# Widens the daemon watchdog across suspend/resume (upstream DEC-396).
install -D -m 0755 "$WORK/src/packaging/ventulus-sleep-hook.sh" /usr/lib/systemd/system-sleep/ventulusd
install -D -m 0644 "$WORK/src/LICENSE" /usr/share/mios/fan-control/licenses/ventulusd.LICENSE

mios_record_resolved ventulusd "$PIN_REF" "${URL}@${PIN_COMMIT}" "$(sha256sum /usr/bin/ventulusd | awk '{print $1}')"
mios_ok "Installed /usr/bin/ventulusd + /usr/bin/ventulus-restore-auto (${PIN_REF})"
