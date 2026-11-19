#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Wires the admin sshd from SSOT: the [ports].ssh port, an include of the vendor drop-ins, and [ssh.ca] user-CA trust for [ssh].user.
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

mios_log "Pin host admin sshd to MIOS_PORTS_SSH=${MIOS_PORTS_SSH} via drop-in"

install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/09-mios-ssh-port.conf <<EOF
Port ${MIOS_PORTS_SSH}
EOF
chmod 0644 /etc/ssh/sshd_config.d/09-mios-ssh-port.conf

# Fedora's sshd reads only /etc/ssh/sshd_config.d, so it never saw the vendor drop-ins
# under /usr/lib/ssh/sshd_config.d (Law 1): route it there. sshd keeps the FIRST value it
# reads for a keyword, so 05 sorts ahead of the distro's drop-ins, and an admin overrides
# a MiOS value from a drop-in that sorts before 05.
mios_log "Route sshd to the vendor drop-ins in /usr/lib/ssh/sshd_config.d"
printf 'Include /usr/lib/ssh/sshd_config.d/*.conf\n' > /etc/ssh/sshd_config.d/05-mios-vendor.conf
chmod 0644 /etc/ssh/sshd_config.d/05-mios-vendor.conf

# The MiOS user CA ([ssh.ca]). sshd trusts ca_file -- written at runtime by
# `mios-credential apply` (surface ssh_ca); absent means no CA -- only for a login that
# has a principals file. Only [ssh].user gets one, naming itself, so a CA certificate
# opens nothing else.
_ca_file="${MIOS_SSH_CA_CA_FILE:?[ssh.ca].ca_file is unset}"
_principals="${MIOS_SSH_CA_PRINCIPALS_DIR:?[ssh.ca].principals_dir is unset}"
_login="${MIOS_SSH_USER:?[ssh].user is unset}"
install -d -m 0755 /usr/lib/ssh/sshd_config.d "$_principals"
printf 'TrustedUserCAKeys %s\nAuthorizedPrincipalsFile %s/%%u\n' "$_ca_file" "$_principals" \
    > /usr/lib/ssh/sshd_config.d/20-mios-user-ca.conf
printf '%s\n' "$_login" > "${_principals}/${_login}"
chmod 0644 /usr/lib/ssh/sshd_config.d/20-mios-user-ca.conf "${_principals}/${_login}"
mios_ok "sshd trusts the [ssh.ca] user CA for ${_login} only"

if command -v sshd >/dev/null 2>&1; then
    sshd -t 2>/dev/null \
        && mios_ok "Sshd config valid; admin sshd will bind ${MIOS_PORTS_SSH}" \
        || mios_skip "drop-in written; skipped sshd -t (host keys absent at build is normal)"
fi
