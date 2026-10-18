#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures PAM via authselect, creates the primary system user with fixed UID 1000, and assigns group memberships (wheel, libvirt, ...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

mios_log "'MiOS' ${MIOS_VERSION:-} user & authentication"

mios_log "Configuring PAM via authselect"
if command -v authselect &>/dev/null; then
    authselect select local --force 2>/dev/null || authselect select minimal --force 2>/dev/null || {
        mios_warn "Authselect select failed"
    }
    authselect apply-changes --force 2>/dev/null || authselect opt-out 2>/dev/null || true
fi

# Operator account + password come ONLY from the layered SSOT (vendor < host < user + mios.d,
# resolved for this build tree) or an explicit build input. No literal fallback: a missing
# value fails the build, naming the key (usr/libexec/mios/mios-credential).
_cred_bin=""
for _c in "${CTX:-}/usr/libexec/mios/mios-credential" "$(dirname "${BASH_SOURCE[0]}")/../usr/libexec/mios/mios-credential" /usr/libexec/mios/mios-credential; do
    [[ -f "$_c" ]] && { _cred_bin="$_c"; break; }
done
_ssot_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_mios_cred() { # resolve against THIS tree's vendor SSOT (+ its etc/mios host tier) unless already retargeted
    [[ -n "$_cred_bin" ]] || { mios_err "mios-credential not found; cannot resolve SSOT credentials"; return 3; }
    if [[ -n "${MIOS_VENDOR_TOML:-}${MIOS_TOML:-}${MIOS_TOML_ROOT:-}" ]]; then
        python3 "$_cred_bin" "$@"
    else
        MIOS_TOML_ROOT="$_ssot_root" python3 "$_cred_bin" "$@"
    fi
}
C_USER="${MIOS_IDENTITY_USERNAME:-}"
if [[ -z "$C_USER" ]]; then
    C_USER="$(_mios_cred username)" || { mios_err "SSOT [identity].username unresolved -- refusing to create an account with a literal name"; exit 1; }
fi

mios_log "Creating user ${C_USER} via sysusers"
if [[ "${C_USER}" != "mios" ]]; then
    rm -f /usr/lib/sysusers.d/10-mios.conf /usr/lib/sysusers.d/50-mios-users.conf /etc/sysusers.d/10-mios.conf /etc/sysusers.d/50-mios-users.conf 2>/dev/null || true
    for _f in /usr/lib/sysusers.d/50-mios.conf /etc/sysusers.d/50-mios.conf; do
        [[ -f "$_f" ]] && sed -i -E '/^[gum][[:space:]]+mios[[:space:]]/d' "$_f"
    done
    if getent passwd mios >/dev/null 2>&1; then
        userdel -f mios 2>/dev/null || true
    fi
    if getent group mios >/dev/null 2>&1; then
        groupdel mios 2>/dev/null || true
    fi

    cat <<EOF > /usr/lib/sysusers.d/15-mios-custom.conf
g ${C_USER} 1000
u ${C_USER} 1000:${C_USER} "'MiOS' Custom User" /var/home/${C_USER} /bin/bash
m ${C_USER} wheel
m ${C_USER} libvirt
m ${C_USER} kvm
m ${C_USER} video
m ${C_USER} render
m ${C_USER} input
m ${C_USER} dialout
m ${C_USER} docker
m ${C_USER} mios-hermes
m ${C_USER} mios-ai
m ${C_USER} mios-sys
EOF
fi

systemd-sysusers --root=/ 2>/dev/null || true

if ! getent passwd "${C_USER}" >/dev/null 2>&1; then
    mios_log "Sysusers did not create ${C_USER}"
    groupadd -g 1000 "${C_USER}" 2>/dev/null || groupadd "${C_USER}" 2>/dev/null || true
    useradd -u 1000 -g "${C_USER}" -m -d "/var/home/${C_USER}" -s /bin/bash "${C_USER}" 2>/dev/null || useradd -m -s /bin/bash "${C_USER}" 2>/dev/null || true
    for g in wheel libvirt kvm video render input dialout docker mios-hermes mios-ai mios-sys; do
        usermod -aG "$g" "${C_USER}" 2>/dev/null || true
    done
fi

if getent passwd "${C_USER}" >/dev/null; then
    home=$(getent passwd "${C_USER}" | cut -d: -f6)
    passwd -u "${C_USER}" 2>/dev/null || true

    c_uid=$(id -u "${C_USER}" 2>/dev/null || echo 1000)
    alloc_bin="$(dirname "${BASH_SOURCE[0]}")/../usr/libexec/mios/mios-subuid-alloc"
    sub_line=""
    if [[ -x "${alloc_bin}" ]]; then
        sub_line=$("${alloc_bin}" --user "${C_USER}" --uid "${c_uid}" 2>/dev/null || true)
    elif [[ -f "${alloc_bin}" && -n "$(command -v python3 2>/dev/null || true)" ]]; then
        sub_line=$(python3 "${alloc_bin}" --user "${C_USER}" --uid "${c_uid}" 2>/dev/null || true)
    elif [[ -x /usr/libexec/mios/mios-subuid-alloc ]]; then
        sub_line=$(/usr/libexec/mios/mios-subuid-alloc --user "${C_USER}" --uid "${c_uid}" 2>/dev/null || true)
    fi

    if [[ -z "${sub_line}" ]]; then
        uid_base=$((100000 + (c_uid - 1000) * 65536))
        sub_line="${C_USER}:${uid_base}:65536"
    fi

    for subfile in /etc/subuid /etc/subgid; do
        install -d -m 0755 "$(dirname "$subfile")" 2>/dev/null || true
        if ! grep -qE "^${C_USER}:" "$subfile" 2>/dev/null; then
            echo "${sub_line}" >> "$subfile"
            mios_log "Added ${C_USER} -> ${subfile} (${sub_line})"
        fi
        chmod 0644 "$subfile" 2>/dev/null || true
    done

    # MIOS_USER_PASSWORD_HASH (alias MIOS_PASSWORD_HASH, the name build-mios.sh used to pass)
    # > [auth].password_hash > sha512-crypt of [identity].default_password. Fail closed.
    pw_hash="$(_mios_cred hash)" || { mios_err "SSOT password unresolved ([auth].password_hash / [identity].default_password) -- refusing a literal default"; exit 1; }
    if [[ "$pw_hash" =~ ^\$6\$ ]]; then
        echo "${C_USER}:${pw_hash}" | chpasswd -e
        if command -v chage >/dev/null 2>&1; then
            chage -d 0 "${C_USER}" 2>/dev/null || true
        elif [[ -f /etc/shadow ]]; then
            sed -i -E "s/^(${C_USER}:[^:]*):[0-9]*:(.*)/\1:0:\2/" /etc/shadow 2>/dev/null || true
        fi
        mios_ok "Password hash baked into /etc/shadow for ${C_USER} (expired via chage -d 0 per ruling Q5)"
    else
        mios_err "Pw_hash is not sha512crypt"
        exit 1
    fi
else
    mios_err "failed to create user ${C_USER}"
fi

chmod 440 /usr/lib/sudoers.d/10-mios-wheel 2>/dev/null || true
chmod 0644 /etc/sudoers.d/* /etc/fapolicyd/fapolicyd.rules 2>/dev/null || true

localedef -i C -f UTF-8 C.UTF-8 2>/dev/null || true
localedef -i en_US -f UTF-8 en_US.UTF-8 2>/dev/null || true
if [ -d /usr/lib/locale/C.utf8 ]; then
    rm -rf /usr/lib/locale/C.UTF-8 2>/dev/null || true
    ln -sf C.utf8 /usr/lib/locale/C.UTF-8
fi
if [ -f /usr/share/locale/locale.alias ]; then
    grep -q "C.UTF-8" /usr/share/locale/locale.alias 2>/dev/null || echo "C.UTF-8 C.utf8" >> /usr/share/locale/locale.alias
fi

mios_log "Fixing home directory ownership"
{ awk -F: '$3 >= 1000 && $3 < 65000 {print $1}' /etc/passwd; echo "mios"; } | sort -u | while read -r u; do
    if getent passwd "$u" >/dev/null 2>&1; then
        home=$(getent passwd "$u" | cut -d: -f6)
        if [ -d "$home" ]; then
            uid=$(id -u "$u"); gid=$(id -g "$u")
            mkdir -p "$home/.cache/oh-my-posh" "$home/.config" 2>/dev/null || true
            chown -R "${uid}:${gid}" "$home"
            chmod 0755 "$home" 2>/dev/null || true
            chmod -R 0755 "$home/.cache" "$home/.config" 2>/dev/null || true
        fi
    fi
done

mios_ok "User & authentication configured"
