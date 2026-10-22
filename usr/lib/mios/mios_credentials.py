# AI-hint: The ONE fail-closed credential resolution over the layered mios.toml SSOT (vendor < host < user + mios.d) -- account names, passwords, service account and persistent AutoLogon for every MiOS image pipeline (Windows answer files/DISM/UUP/ISO via the JSON projection, Linux image/installer/firstboot via the CLI).
# AI-related: usr/lib/mios/mios_toml.py, usr/libexec/mios/mios-credential, usr/libexec/mios/win/unattend_gen.py, mios-bootstrap field/autounattend/MiOS-Provision.lib.ps1 (Resolve-MiOSCredentials -- the PowerShell twin; keep the rules identical)
# AI-functions: CredentialError, resolve, windows_projection, identity_field, sysrescue, password_hash
"""Layered SSOT credential resolver. There is NO built-in credential default:
a missing or empty key raises CredentialError naming the key.

Keys (all read from the MERGED layers, so a host /etc/mios/mios.d/*.toml or a
user ~/.config/mios/mios.toml override wins over the vendor file):
  [identity]                 username (required), default_password (required), fullname
  [[autounattend.accounts]]  name (required per entry), password ("" -> default_password),
                             display_name, group
  [autounattend.service]     enable (default true), svc_user (required when enabled),
                             svc_password ("" -> default_password)
  [autounattend]             autologon_enable (default true),
                             autologon_user ("" -> identity.username; never svc_user),
                             autologon_persistent (default true),
                             autologon_count (required when not persistent)
  [auth]                     password_hash (sha512-crypt; wins for Linux /etc/shadow)
  [field.sysrescue]          username ("" -> identity.username), password ("" -> default_password)
"""

from __future__ import annotations

import os
import shutil
import subprocess

PERSISTENT_LOGON_COUNT = 9999999


class CredentialError(RuntimeError):
    """A required SSOT credential key is missing/empty or inconsistent."""


def _missing(key: str, why: str = "") -> CredentialError:
    ctx = f" ({why})" if why else ""
    return CredentialError(
        f"MiOS SSOT: required credential key '{key}' is missing or empty{ctx}. Set it in "
        "mios.toml (vendor usr/share/mios/mios.toml, host /etc/mios/mios.toml or "
        "/etc/mios/mios.d/*.toml, user ~/.config/mios/mios.toml); there is no built-in default."
    )


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _truthy(v, default: bool) -> bool:
    if v is None or _s(v) == "":
        return default
    return _s(v).strip().lower() in ("true", "1", "yes", "on")


def _sect(data: dict, dotted: str) -> dict:
    cur = data
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            return {}
    return cur


def load(layers=None) -> dict:
    """The merged SSOT (mios_toml.load_merged -- native resolver first, same tiers)."""
    import mios_toml  # noqa: PLC0415 -- resolved from /usr/lib/mios or the repo tree

    if hasattr(mios_toml, "clear_cache"):
        mios_toml.clear_cache()
    data = mios_toml.load_merged(layers) if layers is not None else mios_toml.load_merged()
    if not isinstance(data, dict) or not data:
        raise CredentialError("MiOS SSOT: no mios.toml layer could be read; credentials cannot be resolved.")
    return data


def resolve(data: dict) -> dict:
    """Resolve every credential from the merged SSOT dict; raise CredentialError if any is missing."""
    ident = _sect(data, "identity")
    au = _sect(data, "autounattend")
    svc = _sect(data, "autounattend.service")

    username = _s(ident.get("username")).strip()
    if not username:
        raise _missing("identity.username")
    default_pw = _s(ident.get("default_password"))
    if not default_pw:
        raise _missing("identity.default_password")
    fullname = _s(ident.get("fullname")) or username

    src = au.get("accounts") if isinstance(au.get("accounts"), list) else []
    src = [a for a in src if isinstance(a, dict)]
    if not src:
        src = [{"name": username, "display_name": fullname, "group": "Administrators", "password": ""}]
    accounts = []
    for i, a in enumerate(src):
        name = _s(a.get("name")).strip()
        if not name:
            raise _missing(f"autounattend.accounts[{i}].name")
        accounts.append({
            "name": name,
            "display_name": _s(a.get("display_name")) or name,
            "group": _s(a.get("group")) or "Users",
            "password": _s(a.get("password")) or default_pw,
        })

    svc_enabled = _truthy(svc.get("enable"), True)
    svc_user = svc_pw = ""
    if svc_enabled:
        svc_user = _s(svc.get("svc_user")).strip()
        if not svc_user:
            raise _missing("autounattend.service.svc_user", "[autounattend.service].enable = true")
        svc_pw = _s(svc.get("svc_password")) or default_pw

    al_enable = _truthy(au.get("autologon_enable"), True)
    al_user = _s(au.get("autologon_user")).strip() or username
    # The console AutoLogon never signs in the service account: svc_user is the renamed
    # built-in Administrator (RID 500), which mios-setup-finalize disables once setup is done
    # (operator decision: a separate desktop admin account carries the persistent AutoLogon).
    # The PowerShell twin (Resolve-MiOSCredentials) and unattend_gen refuse it identically.
    if al_enable and svc_user and al_user.lower() == svc_user.lower():
        raise CredentialError(
            f"MiOS SSOT: autounattend.autologon_user '{al_user}' is the service account "
            "([autounattend.service].svc_user, the RID-500 Administrator disabled at setup finalize); "
            "AutoLogon must sign in a desktop account ([identity].username or an [[autounattend.accounts]] entry)."
        )
    al_pw = ""
    for a in accounts:
        if a["name"].lower() == al_user.lower():
            al_pw = a["password"]
            break
    if al_enable and not al_pw:
        raise CredentialError(
            f"MiOS SSOT: autounattend.autologon_user '{al_user}' is neither an [[autounattend.accounts]] "
            "entry nor [identity].username, so it has no SSOT password."
        )
    persistent = _truthy(au.get("autologon_persistent"), True)
    if persistent:
        count = PERSISTENT_LOGON_COUNT
    else:
        raw = _s(au.get("autologon_count")).strip()
        if not raw:
            raise _missing("autounattend.autologon_count", "autologon_persistent = false")
        if not raw.isdigit() or int(raw) < 1:
            raise CredentialError(f"MiOS SSOT: autounattend.autologon_count must be a positive integer (got '{raw}').")
        count = int(raw)

    return {
        "username": username,
        "fullname": fullname,
        "default_password": default_pw,
        "accounts": accounts,
        "service": {"enable": svc_enabled, "user": svc_user, "password": svc_pw},
        "autologon": {"enable": al_enable, "user": al_user, "password": al_pw,
                      "count": count, "persistent": persistent},
    }


def _flatten(prefix: str, node: dict, out: dict) -> None:
    for k, v in node.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            _flatten(key, v, out)
        elif isinstance(v, list):
            continue  # lists are projected explicitly (accounts) or not at all
        else:
            out[key] = _s(v)


def windows_projection(data: dict) -> dict:
    """The MIOS_RESOLVED_JSON document the PowerShell pipeline overlays (Read-MiosToml).
    Validates first, so a missing credential fails HERE on the build host."""
    resolve(data)
    scalars: dict = {}
    for sect in ("identity", "autounattend", "field.sysrescue"):
        node = _sect(data, sect)
        if node:
            _flatten(sect, node, scalars)
    au = _sect(data, "autounattend")
    raw_accts = au.get("accounts") if isinstance(au.get("accounts"), list) else []
    accounts = [{k: _s(v) for k, v in a.items() if not isinstance(v, (dict, list))}
                for a in raw_accts if isinstance(a, dict)]
    h = _s(_sect(data, "auth").get("password_hash"))
    if h:
        scalars["auth.password_hash"] = h
    return {"schema": "mios-resolved-credentials/1", "scalars": scalars,
            "accounts": accounts, "has_accounts": bool(accounts)}


IDENTITY_PUBLIC_KEYS = ("username", "fullname", "hostname", "shell")


def identity_field(data: dict, key: str) -> str:
    """A NON-secret [identity] key from the merged layers, without validating the credential
    plane (installers prompt for what is missing). Secret keys are refused."""
    if key not in IDENTITY_PUBLIC_KEYS:
        raise CredentialError(f"identity.{key} is not a readable public identity key ({', '.join(IDENTITY_PUBLIC_KEYS)}).")
    return _s(_sect(data, "identity").get(key)).strip()


def sysrescue(data: dict) -> dict:
    cred = resolve(data)
    sr = _sect(data, "field.sysrescue")
    return {"username": _s(sr.get("username")).strip() or cred["username"],
            "password": _s(sr.get("password")) or cred["default_password"]}


def password_hash(data: dict, env: dict | None = None) -> str:
    """sha512-crypt for /etc/shadow: env MIOS_USER_PASSWORD_HASH (alias MIOS_PASSWORD_HASH),
    else [auth].password_hash, else openssl passwd -6 over [identity].default_password."""
    env = os.environ if env is None else env
    for var in ("MIOS_USER_PASSWORD_HASH", "MIOS_PASSWORD_HASH"):
        h = env.get(var, "")
        if h:
            if not h.startswith("$6$"):
                raise CredentialError(f"{var} is set but is not a sha512-crypt ($6$) hash.")
            return h
    h = _s(_sect(data, "auth").get("password_hash"))
    if h:
        if not h.startswith("$6$"):
            raise CredentialError("MiOS SSOT: [auth].password_hash is not a sha512-crypt ($6$) hash.")
        return h
    pw = resolve(data)["default_password"]
    openssl = shutil.which("openssl")
    if not openssl:
        raise CredentialError("openssl is required to hash [identity].default_password (sha512-crypt).")
    res = subprocess.run([openssl, "passwd", "-6", "-stdin"], input=pw + "\n",
                         capture_output=True, text=True, timeout=30, check=False)
    out = res.stdout.strip()
    if res.returncode != 0 or not out.startswith("$6$"):
        raise CredentialError("openssl passwd -6 failed to hash [identity].default_password.")
    return out
