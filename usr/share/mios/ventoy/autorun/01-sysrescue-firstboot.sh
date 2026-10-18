#!/bin/bash
# AI-hint: SystemRescue autorun on the Ventoy stick: generates SSH host keys and enables SSH access for MiOS field deployment; credentials come ONLY from the layered SSOT (mios-sysrescue.env / [field.sysrescue] / [identity]), fail closed.
# SystemRescue Firstboot -- enables SSH + full remote access (user/pass/sudo/services)
# and installs a LIVE connection header (IP + hardware) that is regenerated + printed on
# EVERY console boot AND EVERY login. SystemRescue is a live env that re-runs autorun on
# each boot, so a banner baked once at firstboot would show a STALE IP after DHCP changes;
# the generator below recomputes everything at the moment it prints, so it is always current.
set -euo pipefail

echo "[MiOS SystemRescue Firstboot] Enabling SSH + remote access + live connection header..."

# --- SSOT credentials (defined ONCE in the layered mios.toml [field.sysrescue] -> [identity],
#     projected here). Order: (1) mios-sysrescue.env rendered at flash time by
#     Render-Sysrescue.ps1 (build-time layered SSOT), (2) a flashed mios.toml parsed live
#     (runtime SSOT, section-scoped). There is NO built-in default: when neither yields a
#     credential, no password is set and no login account is created (fail closed, logged).
MIOS_USER=""; MIOS_PASS=""; ROOT_PASS=""; MIOS_HEADER=1
# (1) rendered env -- beside autorun0 at the ar_source partition root (dirname $0), then the
#     usual auto-mount points as fallbacks.
for _env in "$(dirname "$0")/mios-sysrescue.env" /mnt/*/mios-sysrescue.env \
            /run/archiso/bootmnt/mios-sysrescue.env /root/mios-sysrescue.env; do
    # shellcheck source=/dev/null  # flash-time render (Render-Sysrescue.ps1), present only on the stick
    if [ -r "$_env" ]; then . "$_env" 2>/dev/null || true; break; fi
done
[ -n "${MIOS_SR_USER:-}" ]   && MIOS_USER="$MIOS_SR_USER"
[ -n "${MIOS_SR_PASS:-}" ]   && { MIOS_PASS="$MIOS_SR_PASS"; ROOT_PASS="$MIOS_SR_PASS"; }
[ -n "${MIOS_SR_HEADER:-}" ] && MIOS_HEADER="$MIOS_SR_HEADER"
# (2) fall back to a flashed mios.toml if the env was not rendered: [field.sysrescue] first,
#     then [identity] (username / default_password) -- the same chain Render-Sysrescue uses.
_toml_get() { # <file> <section> <key> -> value of a "quoted" key inside exactly that [section]
    awk -v sec="$2" -v key="$3" '
        /^[[:space:]]*\[/ { h = $0; sub(/^[[:space:]]*\[+/, "", h); sub(/\].*$/, "", h); insec = (h == sec); next }
        insec { l = $0; sub(/^[[:space:]]+/, "", l)
                if (index(l, key) == 1) { r = substr(l, length(key) + 1)
                    if (r ~ /^[[:space:]]*=/ && match(r, /"[^"]*"/)) { print substr(r, RSTART + 1, RLENGTH - 2); exit } } }' "$1"
}
if [ -z "$MIOS_USER" ] || [ -z "$MIOS_PASS" ]; then
    for _t in /run/archiso/bootmnt/mios.toml /run/archiso/bootmnt/Documents/mios.toml /mnt/*/mios.toml /root/mios.toml; do
        [ -r "$_t" ] || continue
        _u=$(_toml_get "$_t" field.sysrescue username); [ -n "$_u" ] || _u=$(_toml_get "$_t" identity username)
        _p=$(_toml_get "$_t" field.sysrescue password); [ -n "$_p" ] || _p=$(_toml_get "$_t" identity default_password)
        [ -z "$MIOS_USER" ] && [ -n "$_u" ] && MIOS_USER="$_u"
        [ -z "$MIOS_PASS" ] && [ -n "$_p" ] && { MIOS_PASS="$_p"; ROOT_PASS="$_p"; }
        break
    done
fi
if [ -z "$MIOS_USER" ] || [ -z "$MIOS_PASS" ]; then
    echo "[MiOS SystemRescue Firstboot] ERROR: no SSOT credential (mios-sysrescue.env or mios.toml [field.sysrescue]/[identity] username + password) -- NOT setting any password or creating a login account." >&2
fi

# 1. SSH host keys (idempotent)
[ -f /etc/ssh/ssh_host_rsa_key ] || ssh-keygen -A 2>/dev/null || true

# 2. sshd: permit root + password + pubkey + X11 forwarding
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-mios-ssh.conf <<'EOF'
PermitRootLogin yes
PasswordAuthentication yes
PubkeyAuthentication yes
X11Forwarding yes
EOF

# 3. Users + passwords + sudo permissions
if [ -n "$MIOS_USER" ] && [ -n "$MIOS_PASS" ]; then
    echo "root:${ROOT_PASS}" | chpasswd 2>/dev/null || true
    if ! id "${MIOS_USER}" &>/dev/null; then
        useradd -m -G wheel -s /bin/bash "${MIOS_USER}" 2>/dev/null || true
    fi
    usermod -aG wheel "${MIOS_USER}" 2>/dev/null || true
    echo "${MIOS_USER}:${MIOS_PASS}" | chpasswd 2>/dev/null || true
fi
# passwordless sudo for wheel (live diagnostic env -- convenience for remote admin)
echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/10-mios-wheel 2>/dev/null || true
chmod 440 /etc/sudoers.d/10-mios-wheel 2>/dev/null || true

# 4. Enable + start remote-access services (sshd; avahi for .local discovery if present)
systemctl enable --now sshd 2>/dev/null || systemctl restart sshd 2>/dev/null || true
systemctl enable --now avahi-daemon 2>/dev/null || true

# 5. Install the LIVE connection-header generator (recomputes IP + hardware every call)
install -m 0755 /dev/stdin /usr/local/bin/mios-connect-header <<HDR
#!/bin/bash
# MiOS SystemRescue -- LIVE connection + hardware header. Recomputed on every invocation.
r=\$'\e[0m'; b=\$'\e[1;36m'; g=\$'\e[1;32m'; y=\$'\e[1;33m'; d=\$'\e[1;30m'
HOSTN=\$(hostname 2>/dev/null || echo sysrescue)
IPS=\$(ip -4 -o addr show scope global 2>/dev/null | awk '{print \$2"="\$4}' | paste -sd '  ' -); [ -n "\$IPS" ] || IPS="(no IPv4 yet -- check the network / plug in ethernet)"
IP6=\$(ip -6 -o addr show scope global 2>/dev/null | awk '{print \$4}' | paste -sd ' ' -)
MACS=\$(ip -o link show 2>/dev/null | awk '/link\/ether/{print \$2 substr(\$0,index(\$0,"link/ether"))}' | awk '{gsub(":\$","",\$1); print \$1"="\$3}' | paste -sd '  ' -)
GW=\$(ip route 2>/dev/null | awk '/^default/{print \$3; exit}')
SSHST=\$(systemctl is-active sshd 2>/dev/null || echo unknown)
SSHPORT=\$( (grep -h -m1 -E '^\s*Port\s+[0-9]+' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | awk '{print \$2}' | head -1) ); [ -n "\$SSHPORT" ] || SSHPORT=22
CPU=\$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//'); [ -n "\$CPU" ] || CPU=\$(uname -m)
CORES=\$(nproc 2>/dev/null || echo '?')
RAM=\$(free -h 2>/dev/null | awk '/Mem:/{print \$2}')
DISKS=\$(lsblk -dno NAME,SIZE,MODEL 2>/dev/null | awk '{n=\$1; s=\$2; \$1=""; \$2=""; sub(/^  */,""); printf "%s(%s%s) ", n, s, (\$0? " "\$0 : "")}')
GPU=\$(lspci 2>/dev/null | grep -iE 'vga|3d|display' | sed -E 's/.*: //' | paste -sd '; ' -)
FW=\$( [ -d /sys/firmware/efi ] && echo UEFI || echo BIOS )
printf '%s\n' "\${b}=======================================================================\${r}"
printf '%s\n' "\${g}  MiOS  |  SystemRescue Live Diagnostic + Remote Environment\${r}"
printf '%s\n' "\${b}=======================================================================\${r}"
printf '  %sSSH%s      : %s (port %s)   login: %sroot%s / %s%s%s  |  %s%s%s / %s\n' "\$y" "\$r" "\$SSHST" "\$SSHPORT" "\$g" "\$r" "\$g" "${MIOS_USER}" "\$r" "\$g" "${MIOS_USER}" "\$r" "${MIOS_PASS}"
printf '  %sConnect%s  : %sssh ${MIOS_USER}@<ip>%s   (any IP below; sudo is passwordless)\n' "\$y" "\$r" "\$b" "\$r"
printf '  %sHost%s     : %s   firmware: %s\n' "\$y" "\$r" "\$HOSTN" "\$FW"
printf '  %sIPv4%s     : %s\n' "\$y" "\$r" "\$IPS"
[ -n "\$IP6" ]  && printf '  %sIPv6%s     : %s\n' "\$y" "\$r" "\$IP6"
[ -n "\$GW" ]   && printf '  %sGateway%s  : %s\n' "\$y" "\$r" "\$GW"
[ -n "\$MACS" ] && printf '  %sMAC%s      : %s\n' "\$y" "\$r" "\$MACS"
printf '%s\n' "\${d}-----------------------------------------------------------------------\${r}"
printf '  %sCPU%s      : %s  (%s cores)\n' "\$y" "\$r" "\$CPU" "\$CORES"
printf '  %sRAM%s      : %s\n' "\$y" "\$r" "\$RAM"
[ -n "\$GPU" ]   && printf '  %sGPU%s      : %s\n' "\$y" "\$r" "\$GPU"
[ -n "\$DISKS" ] && printf '  %sDisks%s    : %s\n' "\$y" "\$r" "\$DISKS"
printf '%s\n' "\${b}=======================================================================\${r}"
HDR

# 6. Print on EVERY login (interactive shells) + every SSH session
cat > /etc/profile.d/00-mios-connect-header.sh <<'EOF'
# MiOS: show the live connection + hardware header on every login.
if [ -x /usr/local/bin/mios-connect-header ] && [ -t 1 ]; then /usr/local/bin/mios-connect-header; fi
EOF
chmod 644 /etc/profile.d/00-mios-connect-header.sh

# 7. Print on the CONSOLE at every boot (after the network is up) so a headless box shows
#    its own IP without anyone logging in. Re-runs each boot = always the current IP.
cat > /etc/systemd/system/mios-connect-header.service <<'EOF'
[Unit]
Description=MiOS live connection + hardware header on the console
After=network-online.target sshd.service
Wants=network-online.target
[Service]
Type=oneshot
ExecStartPre=/bin/sleep 2
ExecStart=/bin/bash -c '/usr/local/bin/mios-connect-header > /dev/tty1 2>&1 || true'
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload 2>/dev/null || true
systemctl enable mios-connect-header.service 2>/dev/null || true

# 8. Print it NOW (this boot), and refresh the static /etc/issue + /etc/motd as a fallback.
/usr/local/bin/mios-connect-header > /dev/tty1 2>/dev/null || true
/usr/local/bin/mios-connect-header > /etc/motd 2>/dev/null || true

echo "[MiOS SystemRescue Firstboot] SSH + remote access enabled; live connection header installed (prints on every boot + every login)."
