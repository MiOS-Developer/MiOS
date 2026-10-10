#!/usr/bin/env bash
# AI-hint: bash Configures the MiOS-DEV podman machine by syncing system files, creating service users, setting up tmpfiles, and configuring subuid/subgid to ...
# AI-doc: usr/share/doc/mios/manual/automation.md

set -euo pipefail

REPO="${1:-${PWD}}"
if [[ ! -d "$REPO/usr/share/mios" ]]; then
    echo "[overlay-builder] FAIL: '$REPO' does not look like a 'MiOS' repo" >&2
    exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
    echo "[overlay-builder] FAIL: must run as root" >&2
    exit 1
fi

cd "$REPO"
echo "[overlay-builder] Source repo: $REPO"

_rsync_in() {
    local src="$1" dst="$2"
    [[ -e "$src" ]] || { echo "[overlay-builder] skip $src"; return 0; }
    install -d "$dst"
    rsync -aH --ignore-existing --info=stats0 "$src" "$dst"
    echo "[overlay-builder]  $src -> $dst"
}

_rsync_in "usr/share/mios/"    "/usr/share/mios/"

_rsync_in "usr/lib/mios/"      "/usr/lib/mios/"

_rsync_in "usr/libexec/mios/"  "/usr/libexec/mios/"

if [[ -f "usr/bin/mios" ]]; then
    install -m 0755 "usr/bin/mios" "/usr/bin/mios"
    echo "[overlay-builder]  /usr/bin/mios"
fi

for src in usr/lib/profile.d/mios-*.sh etc/profile.d/mios-*.sh; do
    [[ -f "$src" ]] || continue
    install -d "/etc/profile.d"
    install -m 0644 "$src" "/etc/profile.d/$(basename "$src")"
    echo "[overlay-builder]  /etc/profile.d/$(basename "$src")"
done

_rsync_in "etc/skel/"          "/etc/skel/"

_rsync_in "etc/containers/"    "/etc/containers/"

_rsync_in "etc/binfmt.d/"      "/etc/binfmt.d/"

_rsync_in "etc/mios/"          "/etc/mios/"

find /usr/libexec/mios -type f -exec chmod +x {} + 2>/dev/null || true
chmod +x /usr/bin/mios 2>/dev/null || true

echo "[overlay-builder] Setting up MiOS sysusers"
install -d -m 0755 /etc/sysusers.d
rm -f /etc/sysusers.d/10-mios*.conf /etc/sysusers.d/30-mios*.conf /etc/sysusers.d/50-mios-*.conf 2>/dev/null || true
_sysusers_added=0
for sf in usr/lib/sysusers.d/*.conf; do
    [[ -f "$sf" ]] || continue
    install -m 0644 "$sf" "/etc/sysusers.d/$(basename "$sf")"
    echo "[overlay-builder]  /etc/sysusers.d/$(basename "$sf")"
    _sysusers_added=$((_sysusers_added + 1))
done
if (( _sysusers_added > 0 )); then
    systemd-sysusers 2>&1 | sed 's/^/[overlay-builder] sysusers: /' || true
fi

echo "[overlay-builder] Setting up MiOS tmpfiles.d"
install -d -m 0755 /etc/tmpfiles.d
rm -f /etc/tmpfiles.d/mios-*.conf /etc/tmpfiles.d/20-podman-machine.conf 2>/dev/null || true
_tmpfiles_added=0
for tf in usr/lib/tmpfiles.d/*.conf; do
    [[ -f "$tf" ]] || continue
    install -m 0644 "$tf" "/etc/tmpfiles.d/$(basename "$tf")"
    echo "[overlay-builder]  /etc/tmpfiles.d/$(basename "$tf")"
    _tmpfiles_added=$((_tmpfiles_added + 1))
done
install -d -m 0755 /usr/lib/mios/logs
install -d -m 0755 /var/lib/mios
if (( _tmpfiles_added > 0 )); then
    systemd-tmpfiles --create 2>&1 | sed 's/^/[overlay-builder] tmpfiles: /' || true
fi

install -d -m 0755 /var/home
if id mios >/dev/null 2>&1; then
    install -d -m 0755 -o mios -g mios /var/home/mios 2>/dev/null || \
        install -d -m 0755 /var/home/mios
    if [[ -d /etc/skel ]] && [[ ! -e /var/home/mios/.bashrc ]]; then
        rsync -aH --ignore-existing /etc/skel/ /var/home/mios/ 2>/dev/null || true
        chown -R mios:mios /var/home/mios 2>/dev/null || true
        echo "[overlay-builder]  /var/home/mios seeded from /etc/skel"
    fi
fi

if ! grep -q '^mios:' /etc/subuid 2>/dev/null; then
    echo 'mios:524288:65536' >> /etc/subuid
    echo "[overlay-builder]  /etc/subuid: mios:524288:65536 added"
fi
if ! grep -q '^mios:' /etc/subgid 2>/dev/null; then
    echo 'mios:524288:65536' >> /etc/subgid
    echo "[overlay-builder]  /etc/subgid: mios:524288:65536 added"
fi

if command -v loginctl >/dev/null 2>&1 && id mios >/dev/null 2>&1; then
    loginctl enable-linger mios 2>/dev/null || true
    echo "[overlay-builder]  loginctl enable-linger mios"
fi

echo "[overlay-builder] Overlay complete"
echo "[overlay-builder] Open a fresh shell to see the 'MiOS' MOTD"
echo "[overlay-builder] Verify mios user: id mios; subuid grep mios /etc/subuid"
echo "[overlay-builder] Container-host probe: sudo -u mios podman info"
