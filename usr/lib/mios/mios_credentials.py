# AI-hint: The ONE fail-closed credential resolution over the layered mios.toml SSOT (vendor < host < user + mios.d) -- account names, passwords, service account and persistent AutoLogon for every MiOS image pipeline (Windows answer files/DISM/UUP/ISO via the JSON projection, Linux image/installer/firstboot via the CLI).
# AI-related: usr/lib/mios/mios_toml.py, usr/libexec/mios/mios-credential, usr/libexec/mios/win/unattend_gen.py
# AI-functions: CredentialError, resolve, windows_projection, identity_field, sysrescue, password_hash, operator_load, secrets_env_path, surface_password, surface_user, portal_login, Ledger, enabled_surfaces, scrub_leftovers, inbox_submit, ingest_inbox, apply_lock, publish_operator_toggles, apply, ttyd_argv
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

Runtime admin surfaces (Portal, Forgejo, code-server, ttyd, Open WebUI, the Linux login)
resolve through operator_load() and surface_password(). For root code, operator_load takes
only the [security.operator_tier] credential values and enable toggles from the operator's
user tier (the file mios.html writes; a Portal save reaches it through ingest_inbox, run as
root); every username, path, unit, host and port is vendor/host. `mios-credential apply`
reconciles each surface whenever that value changes. No surface ever invents a value, and
an unparseable or unlocatable tier, or an operator credential it must refuse, fails closed.
ttyd is the one exception to the shared credential: it takes its login only on argv, which
every local uid can read in /proc, so it gets its OWN login ([ttyd].auth_pass or
MIOS_TTYD_AUTH_PASS, else a secret generated once), delivered by root through the credstore
-- never a credential another surface uses (follow-up: ttyd behind the Portal).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

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


IDENTITY_PUBLIC_KEYS = ("username", "fullname", "hostname", "shell", "email")


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


# ------------------------------------------------------------------ the mios.html tier + surfaces
# A system service resolving "~" reads its own home, never the tier mios.html writes; root
# code takes from that tier only [security.operator_tier] (mios_toml.operator_view).

SURFACES = ("portal", "forge", "code_server", "ttyd", "owui", "shadow")
RANDOM_OPT_IN = "__random__"          # explicit opt-in to a generated value (owui only)
_RANDOM_OK = ("owui",)
_SURFACE_ENV = {"forge": "MIOS_FORGE_ADMIN_PASSWORD", "owui": "MIOS_OWUI_ADMIN_PASSWORD",
                "portal": "MIOS_PORTAL_PASSWORD", "ttyd": "MIOS_TTYD_AUTH_PASS"}
_SURFACE_TOML = {"portal": ("portal", "password"), "ttyd": ("ttyd", "auth_pass")}
_SURFACE_USER_TOML = {"portal": ("portal", "user"), "ttyd": ("ttyd", "auth_user")}
_OWN_LOGIN = ("ttyd",)                # argv-only consumers: never the shared operator credential
OWN_GENERATED = "generated"           # tier of an _OWN_LOGIN surface with no own login set
SECRETS_ENV_NAME = "secrets.env"      # Law 11: <[paths].etc_dir>/secrets.env, 0600 root
CREDSTORE_DIR = "/run/credstore"      # systemd's ImportCredential= search dir -- not a tunable
TOKEN_PREFIX = "mios-credential-apply-"
INBOX_MAX_BYTES = 65536


def operator_load(getpw=None, euid=None) -> tuple:
    """(data, reason, taken) from mios_toml.operator_view: vendor + host in full and, for a
    system caller, only [security.operator_tier] from the operator's user tier. A broken
    layer or an operator tier that cannot be located raises CredentialError, so a caller
    writes nothing rather than falling back to the vendor default."""
    import mios_toml  # noqa: PLC0415

    try:
        return mios_toml.operator_view(getpw=getpw, euid=euid)
    except mios_toml.LayerError as exc:
        raise CredentialError(f"MiOS SSOT: operator tier unresolved: {exc}") from None


def _paths(data: dict, key: str) -> str:
    import mios_toml  # noqa: PLC0415

    val = _s(_sect(data, "paths").get(key)).strip()
    if not val:
        raise _missing(f"paths.{key}")
    try:
        return mios_toml.expand_refs(data, val)
    except mios_toml.LayerError as exc:
        raise CredentialError(f"MiOS SSOT: [paths].{key}: {exc}") from None


def secrets_env_path(data: dict) -> str:
    return os.path.join(_paths(data, "etc_dir"), SECRETS_ENV_NAME)


def _env_file(path: str, *, secret: bool) -> dict:
    """KEY=value pairs of an env file. A secret file must be a regular 0600 file owned by
    root (or by the caller) and is never followed through a link; an unreadable one is
    absent for this caller (a service user cannot read root's secrets.env)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {}
    except PermissionError:
        return {}
    except OSError as exc:
        if secret:
            raise CredentialError(f"refusing {path}: not a regular file ({exc.strerror})") from None
        return {}
    import stat as _stat
    with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fh:
        st = os.fstat(fh.fileno())
        if secret:
            if not _stat.S_ISREG(st.st_mode):
                raise CredentialError(f"refusing {path}: not a regular file")
            if _stat.S_IMODE(st.st_mode) != 0o600:
                raise CredentialError(f"refusing {path}: mode {_stat.S_IMODE(st.st_mode):04o}, want 0600")
            if st.st_uid not in (0, os.geteuid()):
                raise CredentialError(f"refusing {path}: owned by uid {st.st_uid}, want root")
        out = {}
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
        return out


def surface_password(surface: str, data: dict, env=None, secrets_env=None, install_env=None) -> dict:
    """{value, tier, shadowed}: MIOS_OPERATOR_PASSWORD, then the surface override (_SURFACE_ENV),
    each from env or secrets.env; then [portal].password / [ttyd].auth_pass; then
    [identity].default_password. install.env is never a tier; nothing found raises
    CredentialError; "__random__" is owui only. `tier` and `shadowed` never hold the secret.
    An _OWN_LOGIN surface (ttyd) takes only its own login (MIOS_TTYD_AUTH_PASS, [ttyd].auth_pass):
    with none, value is "" and tier OWN_GENERATED -- apply() hands it a generated secret."""
    if surface not in SURFACES:
        raise CredentialError(f"unknown credential surface '{surface}' ({', '.join(SURFACES)})")
    env = os.environ if env is None else env
    if secrets_env is None:
        secrets_env = secrets_env_path(data) if _sect(data, "paths").get("etc_dir") else ""
    if install_env is None:
        install_env = _s(_sect(data, "paths").get("install_env")).strip()
    secrets = _env_file(secrets_env, secret=True) if secrets_env else {}
    leaked = set(_env_file(install_env, secret=False)) if install_env else set()

    def from_env(var):
        v = env.get(var, "")
        if v and var not in leaked:
            return v, f"{var} (environment)"
        v = secrets.get(var, "")
        return (v, f"{var} (secrets.env)") if v else ("", "")

    layered, layered_tier = "", ""
    sk = _SURFACE_TOML.get(surface)
    if sk:
        layered = _s(_sect(data, sk[0]).get(sk[1]))
        layered_tier = f"[{sk[0]}].{sk[1]}" if layered else ""
    if not layered and surface not in _OWN_LOGIN:
        layered = _s(_sect(data, "identity").get("default_password"))
        layered_tier = "[identity].default_password" if layered else ""

    value, tier = ("", "") if surface in _OWN_LOGIN else from_env("MIOS_OPERATOR_PASSWORD")
    if not value and surface in _SURFACE_ENV:
        value, tier = from_env(_SURFACE_ENV[surface])
    shadowed = bool(value) and bool(layered) and value != layered
    if not value:
        value, tier = layered, layered_tier
    if not value and surface in _OWN_LOGIN:
        return {"value": "", "tier": OWN_GENERATED, "shadowed": False}
    if not value:
        raise _missing("identity.default_password", f"credential surface '{surface}'")
    if value == RANDOM_OPT_IN and surface not in _RANDOM_OK:
        raise CredentialError(f"'{RANDOM_OPT_IN}' at {tier} is honoured only by {', '.join(_RANDOM_OK)}, not "
                              f"'{surface}': its login must be the operator credential")
    return {"value": value, "tier": tier, "shadowed": shadowed}


def _operator_set(tier: str, taken) -> bool:
    """True when the credential came from the operator (its user tier or secrets.env),
    not from a vendor/host default."""
    if tier.endswith("(secrets.env)") or tier.endswith("(environment)"):
        return True
    return tier.strip("[").replace("].", ".") in taken


def surface_user(surface: str, data: dict) -> str:
    """The login name a surface uses: its own TOML key when set, else [identity].username."""
    sk = _SURFACE_USER_TOML.get(surface)
    name = _s(_sect(data, sk[0]).get(sk[1])).strip() if sk else ""
    name = name or _s(_sect(data, "identity").get("username")).strip()
    if not name:
        raise _missing("identity.username", f"credential surface '{surface}'")
    return name


def _imported(env, name: str) -> str:
    """A credential systemd imported for this unit ($CREDENTIALS_DIRECTORY/<name>), or ''."""
    cdir = env.get("CREDENTIALS_DIRECTORY", "")
    if not cdir:
        return ""
    try:
        with open(os.path.join(cdir, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def portal_login(data: dict, log, env=None) -> tuple:
    """(password, user) for the Portal: the credstore mios-credential apply writes
    ($CREDENTIALS_DIRECTORY, ImportCredential=), then MIOS_PORTAL_* from the environment
    unless install.env carries it. Never a vendor default: nothing applied refuses every login."""
    env = os.environ if env is None else env
    pw = _imported(env, "mios.portal.password")
    user = _imported(env, "mios.portal.user")
    if not (pw and user):
        install_env = _s(_sect(data, "paths").get("install_env")).strip()
        leaked = set(_env_file(install_env, secret=False)) if install_env else set()
        pw = pw or ("" if "MIOS_PORTAL_PASSWORD" in leaked else env.get("MIOS_PORTAL_PASSWORD", ""))
        user = user or ("" if "MIOS_PORTAL_USER" in leaked else env.get("MIOS_PORTAL_USER", ""))
    if not (pw and user):
        log.error("portal: no applied credential (mios.portal.* credstore) -- every portal login "
                  "is refused; run mios-credential apply portal")
        return "", ""
    return pw, user


# ------------------------------------------------------------------ reconciler (mios-credential apply)

def _atomic(path: str, text: str, mode: int, uid: int = -1, gid: int = -1) -> None:
    import tempfile as _tf
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = _tf.mkstemp(prefix=".mios-cred.", dir=d)
    try:
        os.fchmod(fd, mode)
        if uid >= 0 or gid >= 0:
            os.fchown(fd, uid, gid)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _as_owner(uid: int, gid: int, fn) -> str:
    """fn() in a forked child holding ONLY uid/gid (root drops to it; another caller runs
    as itself), so writing a less-privileged home can never reach a file its owner cannot.
    Returns fn's string result; the child's error message becomes a CredentialError."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover -- the child reports through the pipe
        os.close(r)
        code, msg = 1, "err:child failed"
        try:
            if os.geteuid() == 0:
                os.setgroups([])
                os.setresgid(gid, gid, gid)
                os.setresuid(uid, uid, uid)
            code, msg = 0, "ok:" + fn()
        except BaseException as exc:  # noqa: BLE001 -- reported, never re-raised in the child
            msg = "err:" + (str(exc) or type(exc).__name__)
        os.write(w, msg.encode("utf-8", "replace")[:4096])
        os._exit(code)
    os.close(w)
    chunks = []
    while True:
        b = os.read(r, 4096)
        if not b:
            break
        chunks.append(b)
    os.close(r)
    os.waitpid(pid, 0)
    msg = b"".join(chunks).decode("utf-8", "replace")
    if msg.startswith("ok:"):
        return msg[3:]
    raise CredentialError(msg[4:] or "child failed")


def _dir_chain(root: str, parts, create: bool) -> int:
    """A dir fd for root/parts[0]/... opened component by component with O_NOFOLLOW: a
    symlinked or non-directory component is refused, a missing one created 0700 if `create`."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(root, flags)
    except OSError as exc:
        raise CredentialError(f"refusing {root}: not a real directory ({exc.strerror})") from None
    path = root
    for part in parts:
        path = os.path.join(path, part)
        try:
            nxt = os.open(part, flags, dir_fd=fd)
        except FileNotFoundError:
            if not create:
                os.close(fd)
                return -1
            os.mkdir(part, 0o700, dir_fd=fd)
            nxt = os.open(part, flags, dir_fd=fd)
        except OSError as exc:
            os.close(fd)
            raise CredentialError(f"refusing {path}: a symlink or not a directory ({exc.strerror})") from None
        os.close(fd)
        fd = nxt
    return fd


def _read_at(dfd: int, name: str, path: str) -> str | None:
    """The text of a regular, singly linked file in dfd (never through a link), or None."""
    import stat as _stat
    if dfd < 0:
        return None
    try:   # O_NONBLOCK: a FIFO planted in a less-privileged home never blocks the open
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CredentialError(f"refusing {path}: a symlink or unreadable ({exc.strerror})") from None
    st = os.fstat(fd)
    if not _stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        raise CredentialError(f"refusing {path}: not a regular, singly linked file")
    with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _write_at(dfd: int, name: str, text: str, mode: int) -> None:
    tmp = f".{name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except FileNotFoundError:
            pass
        raise


class Ledger:
    """HMAC fingerprints of what each surface last received, and of the value that last
    FAILED (with its backoff): a changed SSOT value is detected without the secret ever
    being stored a second time. Root-only (0700 dir, 0600 files)."""

    def __init__(self, directory: str):
        self.dir = directory
        self.state_path = os.path.join(directory, "state.json")
        self._key = None
        self._state = None

    def _load(self):
        import json as _json
        if self._state is None:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            kp = os.path.join(self.dir, "key")
            try:
                with open(kp, "rb") as fh:
                    self._key = fh.read()
            except FileNotFoundError:
                self._key = b""
            if len(self._key) < 32:
                self._key = os.urandom(32)
                fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(self._key)
            try:
                with open(self.state_path, encoding="utf-8") as fh:
                    self._state = _json.load(fh)
            except (OSError, ValueError):
                self._state = {}
        return self._state

    def fingerprint(self, surface: str, user: str, secret: str) -> str:
        import hashlib
        import hmac
        self._load()
        msg = "\0".join((surface, user, secret)).encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def same(self, surface: str, fp: str) -> bool:
        return self._load().get(surface) == fp

    def _save(self):
        import json as _json
        _atomic(self.state_path, _json.dumps(self._load(), sort_keys=True), 0o600)

    def record(self, surface: str, fp: str, operator_set: bool | None = None) -> None:
        st = self._load()
        st[surface] = fp
        if operator_set is not None:
            st.setdefault("_operator_set", {})[surface] = bool(operator_set)
        (st.get("_failures") or {}).pop(surface, None)
        self._save()

    def has(self, surface: str) -> bool:
        return surface in self._load()

    def operator_set(self, surface: str) -> bool:
        """True when the value this surface last received was operator-set (its user tier,
        secrets.env or the environment), not a vendor/host default."""
        return bool((self._load().get("_operator_set") or {}).get(surface))

    def own_secret(self, surface: str, create: bool) -> str:
        """The generated login of an _OWN_LOGIN surface with none set (<dir>/<surface>.generated,
        0600 root): made once, then kept, so a reboot does not change it."""
        import secrets as _secrets
        self._load()
        path = os.path.join(self.dir, f"{surface}.generated")
        try:
            with open(path, encoding="utf-8") as fh:
                have = fh.read().strip()
        except FileNotFoundError:
            have = ""
        if have or not create:
            return have
        _atomic(path, _secrets.token_urlsafe(24), 0o600)
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()

    def backoff_until(self, surface: str, fp: str) -> float:
        """When the next attempt at THIS value is due after it failed (0: now)."""
        f = (self._load().get("_failures") or {}).get(surface) or {}
        return float(f.get("next", 0)) if f.get("fp") == fp else 0.0

    def record_failure(self, surface: str, fp: str, now: float, base_s: int, max_s: int) -> float:
        st = self._load()
        fails = st.setdefault("_failures", {})
        prev = fails.get(surface) or {}
        n = int(prev.get("n", 0)) + 1 if prev.get("fp") == fp else 1
        nxt = now + min(base_s * (2 ** (n - 1)), max_s)
        fails[surface] = {"fp": fp, "n": n, "next": nxt}
        self._save()
        return nxt


def _run(cmd, *, stdin=None, timeout=60):
    """A tool by name on PATH; stdout/stderr captured and never logged by callers."""
    kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, **kw)


def _try_restart(data: dict, units_key: str, ctx) -> None:
    """Queue (--no-block) a restart: the dependents are ordered After= this oneshot, so a
    synchronous restart from inside it would wait on itself."""
    unit = _s(_sect(data, "units").get(units_key)).strip()
    if unit and ctx.get("restart", True):
        res = _run(["systemctl", "--no-block", "try-restart", unit])
        ctx["restarted"].append(unit if res.returncode == 0 else f"{unit} (failed {res.returncode})")


def _http(method: str, url: str, *, basic=None, token=None, body=None, timeout=10):
    """(status, json-or-None) over loopback; credentials ride headers and the body only."""
    import base64
    import json as _json
    import urllib.error
    import urllib.request
    data = _json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if basic:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode())
    if token:
        req.add_header("Authorization", f"token {token}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, _json.loads(raw or b"null")
            except ValueError:
                return resp.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _forge_base(data: dict) -> str:
    """http://<[network].loopback>:<[ports].forge_http> -- refused unless a loopback address,
    so the admin credential never leaves the host."""
    import ipaddress
    host = _s(_sect(data, "network").get("loopback")).strip()
    port = _s(_sect(data, "ports").get("forge_http")).strip()
    try:
        loop = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loop = False
    if not loop or not port.isdigit() or not 0 < int(port) < 65536:
        raise CredentialError("MiOS SSOT: [network].loopback must be a loopback address and "
                              "[ports].forge_http a port (forge surface)")
    return f"http://{host}:{port}" if ":" not in host else f"http://[{host}]:{port}"


def _forge_exec(data: dict) -> list:
    unit = _s(_sect(data, "units").get("forge")).strip()
    forge = _sect(data, "services.forge")
    home, ini = _s(forge.get("home")).strip(), _s(forge.get("app_ini")).strip()
    if not unit or not home or not ini:
        raise _missing("units.forge / services.forge.home / services.forge.app_ini", "forge surface")
    ctr = unit[:-len(".service")] if unit.endswith(".service") else unit
    return ["podman", "exec", "-e", f"HOME={home}", ctr, "forgejo", "--config", ini]


def _forge_sweep(base: str, user: str, secret: str) -> int:
    """Revoke every TOKEN_PREFIX token of the admin (a PATCH that failed leaves its own),
    authenticated with the now-working password. Returns how many were revoked."""
    import urllib.parse
    status, toks = _http("GET", f"{base}/api/v1/users/{urllib.parse.quote(user)}/tokens", basic=(user, secret))
    n = 0
    for t in toks if status == 200 and isinstance(toks, list) else []:
        name = str((t or {}).get("name") or "")
        if name.startswith(TOKEN_PREFIX):
            st, _ = _http("DELETE", f"{base}/api/v1/users/{urllib.parse.quote(user)}/tokens/{t.get('id')}",
                          basic=(user, secret))
            n += st in (200, 204)
    return n


def _apply_forge(data, user, secret, ctx):
    """Only an operator-set credential replaces forge-firstboot's generated password; the
    first one taken also creates [services.forge].initial_repo."""
    import time as _time
    import urllib.parse
    if not ctx["operator_set"]:
        ctx["no_record"] = True
        return "skipped (no operator-set credential: the Forgejo admin keeps its own password)"
    if ctx["fp_same"] and not ctx["verify"] and not ctx["bootstrap"]:
        return "unchanged"
    base = _forge_base(data)
    forge = _sect(data, "services.forge")
    deadline = _time.monotonic() + (int(_s(forge.get("ready_timeout_s")) or 0) if ctx["wait"] else 0)
    while True:
        status, _ = _http("GET", f"{base}/api/v1/version", timeout=5)
        if status == 200 or _time.monotonic() >= deadline:
            break
        _time.sleep(2)
    if status != 200:
        ctx["no_record"] = True
        return "skipped (forge not reachable)"
    status, _ = _http("GET", f"{base}/api/v1/user", basic=(user, secret))
    changed = False
    if status != 200:
        name = f"{TOKEN_PREFIX}{int(_time.time())}"
        res = _run(_forge_exec(data) + ["admin", "user", "generate-access-token", "--username", user,
                                        "--token-name", name, "--scopes", "write:admin", "--raw"])
        tok = (res.stdout or "").strip().splitlines()[-1].strip() if res.returncode == 0 and res.stdout.strip() else ""
        if not tok:
            return "failed (could not mint an admin token through the forgejo CLI)"
        status, _ = _http("PATCH", f"{base}/api/v1/admin/users/{urllib.parse.quote(user)}", token=tok,
                          body={"login_name": user, "source_id": 0, "password": secret,
                                "must_change_password": False})
        if status != 200:
            return (f"failed (admin PATCH returned HTTP {status}; token {name} is revoked by the next "
                    "run that logs in, and this value is retried only after its backoff)")
        changed = True
    swept = _forge_sweep(base, user, secret)
    if swept:
        ctx["notes"].append(f"forge: revoked {swept} {TOKEN_PREFIX}* token(s)")
    if ctx["bootstrap"]:
        repo = _s(forge.get("initial_repo")).strip()
        if repo:
            st, _ = _http("POST", f"{base}/api/v1/user/repos", basic=(user, secret),
                          body={"name": repo, "description": "MiOS deployed root (live working tree of /).",
                                "private": False, "auto_init": False, "default_branch": "main"})
            ctx["notes"].append(f"forge: initial repo {repo}: " + {201: "created", 409: "exists"}.get(st, f"HTTP {st}"))
    return "changed" if changed else "unchanged"


_ARGON2 = {"t": 3, "m": 65536, "p": 4, "salt": 16, "len": 32}   # argon2-cffi's RFC 9106 low-memory profile


def _code_server_hash(secret: str) -> str:
    """code-server's salted argon2id hashed-password (a PHC string): argon2-cffi when it is
    installed, else python3-cryptography's Argon2id (the image ships it). With neither the
    surface FAILS: an unsalted SHA-256 of the one operator credential is never written."""
    try:
        import argon2  # type: ignore  # noqa: PLC0415
        return argon2.PasswordHasher().hash(secret)
    except ImportError:
        pass
    try:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id  # noqa: PLC0415
    except ImportError:
        raise CredentialError("no argon2 implementation (python3-cryptography >= 44 or python3-argon2-cffi): "
                              "refusing to write an unsalted code-server hash of the operator credential") from None
    import base64
    a = _ARGON2
    salt = os.urandom(a["salt"])
    raw = Argon2id(salt=salt, length=a["len"], iterations=a["t"], lanes=a["p"],
                   memory_cost=a["m"]).derive(secret.encode("utf-8"))
    b64 = lambda b: base64.b64encode(b).decode("ascii").rstrip("=")  # noqa: E731 -- PHC: unpadded
    return f"$argon2id$v=19$m={a['m']},t={a['t']},p={a['p']}${b64(salt)}${b64(raw)}"


def _apply_code_server(data, user, secret, ctx):
    import stat as _stat
    home = _paths(data, "var_agents_dir")
    try:
        hst = os.lstat(home)
    except FileNotFoundError:
        return "skipped (no IDE home)"
    if not _stat.S_ISDIR(hst.st_mode):
        raise CredentialError(f"refusing {home}: a symlink or not a directory")
    hashed = "" if ctx["check"] else _code_server_hash(secret)
    fp_same, check = ctx["fp_same"], ctx["check"]

    def work():  # runs as the IDE home's owner, never following a link
        dfd = _dir_chain(home, (".config", "code-server"), create=not check)
        try:
            text = _read_at(dfd, "config.yaml", os.path.join(home, ".config/code-server/config.yaml"))
            lines = (text or "").splitlines()
            has_hash = any(ln.split(":", 1)[0].strip() == "hashed-password" for ln in lines)
            if fp_same and has_hash:
                return "unchanged"
            if check:
                return "drift"
            keep = [ln for ln in lines if ln.split(":", 1)[0].strip() not in ("auth", "password", "hashed-password")]
            keep += ["auth: password", f'hashed-password: "{hashed}"']
            _write_at(dfd, "config.yaml", "\n".join(keep) + "\n", 0o600)
            return "changed"
        finally:
            if dfd >= 0:
                os.close(dfd)

    state = _as_owner(hst.st_uid, hst.st_gid, work)
    if state == "changed":
        _try_restart(data, "code_server", ctx)
    return state


def credstore_dir() -> str:
    return CREDSTORE_DIR


def _credstore_sync(want: dict, ctx) -> str:
    """Bring each <credstore>/<name> (0400 root; a unit's ImportCredential= reads it) to its
    text: unchanged | drift (check) | changed. Compared to the files, so a fresh /run is refilled."""
    store = credstore_dir()
    have = {}
    for name in want:
        try:
            with open(os.path.join(store, name), encoding="utf-8") as fh:
                have[name] = fh.read()
        except OSError:
            have[name] = None
    if have == want:
        return "unchanged"
    if ctx["check"]:
        return "drift"
    os.makedirs(store, mode=0o700, exist_ok=True)
    for name, text in want.items():
        _atomic(os.path.join(store, name), text, 0o400, *((0, 0) if os.geteuid() == 0 else (-1, -1)))
    return "changed"


def _apply_portal(data, user, secret, ctx):
    state = _credstore_sync({"mios.portal.password": secret, "mios.portal.user": user}, ctx)
    if state == "changed":
        _try_restart(data, "agent_pipe", ctx)
    return state


def _apply_ttyd(data, user, secret, ctx):
    """ttyd's OWN login into the credstore its units import; one equal to a shared
    credential (ttyd's argv is readable in /proc) is refused and withdrawn, and a ttyd
    still running on the withdrawn login is restarted (exec ttyd then exits 3)."""
    if ctx.get("collides"):
        ctx["no_record"] = True
        if not ctx["check"]:
            withdrawn = False
            for name in ("mios.ttyd.password", "mios.ttyd.user"):
                try:
                    os.unlink(os.path.join(credstore_dir(), name))
                    withdrawn = True
                except FileNotFoundError:
                    pass
            if withdrawn:
                for key in ("ttyd_bash", "ttyd_powershell"):
                    _try_restart(data, key, ctx)
        return ("failed (ttyd's own login equals a credential another surface uses -- refused and "
                "withdrawn; set a distinct [ttyd].auth_pass in mios.html)")
    state = _credstore_sync({"mios.ttyd.password": secret, "mios.ttyd.user": user}, ctx)
    if state == "changed":
        for key in ("ttyd_bash", "ttyd_powershell"):
            _try_restart(data, key, ctx)
    return state


# mios-owui-bootstrap-admin exit codes (its docstring): 0 reconciled, 2 no credential,
# 4 no admin row matches [identity].email (left as it is), 5 database not ready.
_OWUI_EXIT = {2: "failed (no SSOT credential for the Open WebUI admin)",
              4: "failed (no Open WebUI admin matches [identity].email; the admin was left as it is)"}


def _apply_owui(data, user, secret, ctx):
    """A changed credential re-runs the bootstrap; --verify re-checks an applied one with
    `mios-owui-bootstrap-admin --verify`, which writes nothing while the admin row exists and
    re-creates it when the database was recreated (its first-signup seat would be open)."""
    verify_only = ctx["fp_same"]
    if verify_only and (ctx["check"] or not ctx["verify"]):
        return "unchanged"
    if ctx["check"]:
        return "drift"
    tool = os.path.join(_paths(data, "libexec_dir"), "mios-owui-bootstrap-admin")
    if not os.access(tool, os.X_OK):
        return "skipped (mios-owui-bootstrap-admin absent)"
    res = _run([tool] + (["--verify"] if verify_only else []), timeout=180)
    if res.returncode == 5:
        ctx["no_record"] = True
        return "skipped (Open WebUI database not ready)"
    if res.returncode != 0:
        return _OWUI_EXIT.get(res.returncode, f"failed (mios-owui-bootstrap-admin exit {res.returncode})")
    if verify_only:
        if "admin user created" in (res.stderr or ""):
            ctx["notes"].append("owui: --verify re-created the admin (its database was recreated)")
        return "unchanged"
    return "changed"


SHADOW_FILE = "/etc/shadow"


def _shadow_accepts(user: str, secret: str) -> bool:
    """True when the account's current shadow hash already verifies `secret` (libcrypt's
    crypt(3) over the stored setting -- yescrypt or sha512-crypt alike). Unreadable shadow,
    a locked or empty field, or no libcrypt: False, so the caller sets the login."""
    import ctypes
    import ctypes.util
    try:
        with open(SHADOW_FILE, encoding="utf-8", errors="replace") as fh:
            have = next((ln.split(":")[1] for ln in fh if ln.split(":")[0] == user and ln.count(":") >= 2), "")
    except OSError:
        return False
    if not have.startswith("$"):
        return False
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("crypt") or "libcrypt.so.1")
        lib.crypt.restype = ctypes.c_char_p
        lib.crypt.argtypes = (ctypes.c_char_p, ctypes.c_char_p)
        got = lib.crypt(secret.encode("utf-8"), have.encode("ascii"))
    except (OSError, AttributeError, UnicodeError):
        return False
    import hmac
    return bool(got) and hmac.compare_digest(got, have.encode("ascii"))


def _apply_shadow(data, user, secret, ctx):
    """The Linux login (Cockpit, sudo, xrdp) of [identity].username follows the operator's
    credential -- only one the operator set: a vendor/host default never rotates it. A
    login that already accepts the credential is left as it is ('unchanged')."""
    if not ctx["operator_set"]:
        ctx["no_record"] = True
        return "skipped (no operator-set credential: the image's login is kept)"
    if ctx["fp_same"] or _shadow_accepts(user, secret):
        return "unchanged"
    if ctx["check"]:
        return "drift"
    res = _run(["openssl", "passwd", "-6", "-stdin"], stdin=secret + "\n", timeout=30)
    h = (res.stdout or "").strip()
    if res.returncode != 0 or not h.startswith("$6$"):
        return "failed (openssl passwd -6)"
    res = _run(["chpasswd", "-e"], stdin=f"{user}:{h}\n")
    return "changed" if res.returncode == 0 else f"failed (chpasswd exit {res.returncode})"


_APPLY = {"portal": _apply_portal, "forge": _apply_forge, "code_server": _apply_code_server,
          "ttyd": _apply_ttyd, "owui": _apply_owui, "shadow": _apply_shadow}


def enabled_surfaces(data: dict) -> list:
    """[security.credential_apply].surfaces (vendor/host) -- what `apply --all` reconciles."""
    raw = _sect(data, "security.credential_apply").get("surfaces")
    if not isinstance(raw, list) or not raw:
        raise _missing("security.credential_apply.surfaces")
    bad = [s for s in raw if s not in SURFACES]
    if bad:
        raise CredentialError(f"[security.credential_apply].surfaces names unknown surface(s): {', '.join(map(str, bad))}")
    return [str(s) for s in raw]


ROOT_REPO = "/"          # the deployed root IS the repository (.git IS /)
_STALE_SECRET = "MIOS_IDENTITY_DEFAULT_PASSWORD"


def _strip_userinfo(url: str, forge_user: str = "", forge_hosts=()) -> tuple:
    """(url, state) for the deployed root's origin: state none | stripped | foreign (userinfo
    on an origin that is not this Forgejo's admin -- an operator's token: left as it is) |
    unparsed (an '@' this cannot place: left as it is). Only the shape the old
    forge-firstboot wrote, http://<forge admin>:<pw>@<forge host:port>/..., is stripped, and
    it is matched first from its LAST '@<forge host:port>/': a raw password may hold '/',
    '@' or ':', which end urlsplit's authority early."""
    import urllib.parse as _up
    scheme, sep, rest = url.partition("://")
    if not sep or scheme not in ("http", "https") or "@" not in rest:
        return url, "none"
    if forge_user and rest.startswith(f"{forge_user}:"):
        for hp in forge_hosts:
            at = rest.rfind(f"@{hp}/")
            if at >= 0:
                return f"{scheme}://{rest[at + 1:]}", "stripped"
    try:
        u = _up.urlsplit(url)
        hostport = f"{u.hostname}:{u.port}" if u.port else str(u.hostname or "")
    except ValueError:
        return url, "unparsed"
    if "@" not in u.netloc:
        return url, "unparsed"
    if hostport not in forge_hosts or not forge_user or u.username != forge_user:
        return url, "foreign"
    clean = _up.urlunsplit((u.scheme, u.netloc.rpartition("@")[2], u.path, u.query, u.fragment))
    return (url, "unparsed") if "@" in clean.partition("://")[2].partition("/")[0] else (clean, "stripped")


def scrub_leftovers(data: dict, log, check=False) -> int:
    """Remove the copies of the credential the pre-reconciler code left behind, once:
    the random MIOS_IDENTITY_DEFAULT_PASSWORD rotate-quadlet-secrets wrote into
    secrets.env, and userinfo (user:password@) in the deployed root's origin URL. The
    old URL is never put on a command line; logs name what was removed, never a value."""
    import stat as _stat
    n = 0
    path = secrets_env_path(data)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        fd = -1
    if fd >= 0:
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fh:
            st = os.fstat(fh.fileno())
            lines = fh.read().splitlines(keepends=True) if _stat.S_ISREG(st.st_mode) else []
        keep = [ln for ln in lines if not ln.lstrip().startswith(_STALE_SECRET + "=")]
        if len(keep) != len(lines) and st.st_uid == 0 and _stat.S_IMODE(st.st_mode) == 0o600:
            try:
                if not check:
                    _atomic(path, "".join(keep), 0o600, 0, 0)
                n += 1
                log(f"scrub: {'would remove' if check else 'removed'} the stale {_STALE_SECRET} from {path}")
            except OSError as exc:   # e.g. a caller whose sandbox keeps /etc/mios read-only
                log(f"scrub: could not remove the stale {_STALE_SECRET} from {path} "
                    f"({exc.strerror or type(exc).__name__}); retried on the next run")
    if os.path.isdir(os.path.join(ROOT_REPO, ".git")) and shutil.which("git"):
        res = _run(["git", "-C", ROOT_REPO, "remote", "get-url", "origin"])
        url = (res.stdout or "").strip() if res.returncode == 0 else ""
        port = _s(_sect(data, "ports").get("forge_http")).strip()
        hosts = [f"{h}:{port}" for h in (_s(_sect(data, "network").get("loopback")).strip(), "localhost") if h and port]
        clean, state = _strip_userinfo(url, _s(_sect(data, "identity").get("username")).strip(), hosts)
        if state == "unparsed":
            log(f"scrub: the origin URL of {ROOT_REPO} carries an '@' this scrub cannot place -- it may "
                "still hold a password: check it with `git -C / remote get-url origin`")
        elif state == "foreign":
            log(f"scrub: the origin URL of {ROOT_REPO} carries userinfo for a host or user that is not "
                "this Forgejo's admin -- left as it is (not ours to remove)")
        elif state == "stripped":
            ok = check or _run(["git", "-C", ROOT_REPO, "remote", "set-url", "origin", clean]).returncode == 0
            n += ok
            log(f"scrub: {'would remove' if check else 'removed' if ok else 'could not remove'} userinfo "
                f"from the origin URL of {ROOT_REPO}" + ("" if ok else "; retried on the next run"))
    return n


# ------------------------------------------------------------------ the Portal's inbox (operator decision Q7)

def _toml_render(v) -> str:
    import json as _json
    return ("true" if v else "false") if isinstance(v, bool) else _json.dumps(v, ensure_ascii=False)


def _upsert_toml(text: str, updates: dict) -> str:
    """text with each dotted key set (its [table] edited in place or appended); the result
    must re-parse to exactly text's tree plus the updates, else CredentialError."""
    import re as _re
    import tomllib
    import mios_toml  # noqa: PLC0415
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise CredentialError("the operator tier does not parse; the Portal save was not merged") from None
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for dotted, v in updates.items():
        table, key = dotted.rsplit(".", 1)
        row = f"{key} = {_toml_render(v)}\n"
        head = _re.compile(r"^\s*\[\s*" + _re.escape(table) + r"\s*\]\s*(#.*)?$")
        at = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
        if at is None:
            lines += ["\n", f"[{table}]\n", row]
            continue
        end = next((i for i in range(at + 1, len(lines)) if _re.match(r"^\s*\[", lines[i])), len(lines))
        hit = next((i for i in range(at + 1, end) if _re.match(r"^\s*" + _re.escape(key) + r"\s*=", lines[i])), None)
        if hit is None:
            lines.insert(at + 1, row)
        else:
            lines[hit] = row
    out = "".join(lines)
    after = tomllib.loads(out)
    want = before
    for dotted, v in updates.items():
        mios_toml._dotted_set(want, dotted, v)
    if after != want:
        raise CredentialError("the operator tier's layout (dotted keys or inline tables) cannot be edited "
                              "safely; the Portal save was not merged")
    return out


INBOX_PROOF = "request.proof"


def _inbox_mac(rows, credential: str) -> str:
    """HMAC-SHA256 over a request's sorted `dotted = value` rows, keyed by the credential
    the Portal accepts: it proves the login without the credential ever being written."""
    import hashlib
    import hmac
    return hmac.new(credential.encode("utf-8"), "\n".join(rows).encode("utf-8"), hashlib.sha256).hexdigest()


def inbox_submit(data: dict, updates: dict, proof: str) -> str:
    """Queue a Portal save of [security.operator_tier] keys for the root reconciler: one
    0600 file of dotted keys in <[paths].run_credential_inbox_dir> under an unguessable
    name (its writers cannot list the inbox), renamed into place so a half-written request
    is never read. `proof` is the credential the Portal session logged in with: it keys the
    request's MAC and is never written; without it nothing is queued. Returns the name."""
    import secrets as _secrets
    import time as _time
    if not proof:
        raise CredentialError("a credential save needs a Portal login (no proof of the current credential)")
    inbox = _paths(data, "run_credential_inbox_dir")
    rows = [f"{dotted} = {_toml_render(v)}" for dotted, v in sorted(updates.items())]
    rows.append(f'{INBOX_PROOF} = "{_inbox_mac(rows, proof)}"')
    name = f"{_time.time_ns()}-{_secrets.token_hex(16)}.toml"
    tmp = os.path.join(inbox, f".{name}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
        os.rename(tmp, os.path.join(inbox, name))
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise CredentialError(f"the credential inbox is not writable ({exc.strerror})") from None
    return name


def _inbox_proved(req: dict) -> bool:
    """True when the request's MAC verifies under the credential the Portal accepts NOW
    (the applied credstore) over EVERY row it carries: a process that can write the inbox
    cannot change a login it does not hold, nor add a key to a request it saw."""
    import hmac
    import mios_toml  # noqa: PLC0415
    proof = mios_toml._dotted_get(req, INBOX_PROOF)
    try:
        with open(os.path.join(credstore_dir(), "mios.portal.password"), encoding="utf-8") as fh:
            want = fh.read()
    except OSError:
        return False
    if not (isinstance(proof, str) and want):
        return False
    rows = [f"{d} = {_toml_render(mios_toml._dotted_get(req, d))}"
            for d in sorted(mios_toml._leaf_keys(req)) if d != INBOX_PROOF]
    return hmac.compare_digest(proof.encode("utf-8"), _inbox_mac(rows, want).encode("ascii"))


def _inbox_read(path: str) -> tuple:
    """(bytes, mtime_ns) of an inbox entry, or (None, 0) for anything but a regular, singly
    linked file of at most INBOX_MAX_BYTES (fstat BEFORE any read: a directory or FIFO never
    reaches read). The mtime is when the Portal queued it."""
    import stat as _stat
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None, 0
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > INBOX_MAX_BYTES:
            return None, 0
        raw = os.read(fd, INBOX_MAX_BYTES + 1)
        return (raw, st.st_mtime_ns) if len(raw) <= INBOX_MAX_BYTES else (None, 0)
    finally:
        os.close(fd)


def ingest_inbox(data: dict, log, check=False, getpw=None) -> int:
    """Merge each queued Portal save (<[paths].run_credential_inbox_dir>/*.toml, oldest
    first) into the operator's user tier, as the operator uid: a request must prove the
    Portal's current credential, only [security.operator_tier] keys of the right type are
    taken, and a request is removed once merged or refused -- one that could not be
    written stays queued with every later one, so saves always land in order. Returns the
    count merged; logs carry file names and key names only."""
    import tomllib
    import mios_toml  # noqa: PLC0415
    inbox = _paths(data, "run_credential_inbox_dir")
    try:
        names = sorted(n for n in os.listdir(inbox) if n.endswith(".toml"))
    except FileNotFoundError:
        return 0
    except OSError as exc:
        log(f"inbox: {inbox} cannot be listed ({exc.strerror}); no Portal save merged this run")
        return 0
    if not names or check:
        if names:
            log(f"inbox: {len(names)} queued Portal save(s) not merged (--check)")
        return 0
    policy = _sect(data, "security.operator_tier")
    values, toggles = list(policy.get("values") or []), list(policy.get("toggles") or [])
    try:
        op_file, _op_d, _reason, system = mios_toml.operator_inputs(getpw=getpw, base=data)
    except mios_toml.LayerError as exc:
        raise CredentialError(f"MiOS SSOT: operator tier unresolved: {exc}") from None
    if system and getpw is None and not os.environ.get("MIOS_USER_TOML"):
        import pwd
        pw = pwd.getpwnam(_s(_sect(data, "identity").get("username")).strip())
        uid, gid = pw.pw_uid, pw.pw_gid
    else:
        anchor = os.path.dirname(os.path.dirname(op_file))
        st = os.stat(anchor if os.path.isdir(anchor) else os.path.dirname(anchor))
        uid, gid = st.st_uid, st.st_gid
    # A save queued before a hand edit of the tier loses to it; inbox.mtime marks the
    # mtime our own merge left, which is not a hand edit.
    mark = os.path.join(_paths(data, "var_credential_apply_dir"), "inbox.mtime")
    try:
        edited = os.lstat(op_file).st_mtime_ns
    except OSError:
        edited = 0
    try:
        with open(mark, encoding="utf-8") as fh:
            edited = 0 if fh.read().strip() == str(edited) else edited
    except OSError:
        pass
    merged = 0
    for name in names:
        path = os.path.join(inbox, name)

        def drop(path=path, name=name):  # the entry itself; a link's target is never touched
            try:
                os.unlink(path)
            except IsADirectoryError:
                try:
                    os.rmdir(path)
                except OSError:
                    log(f"inbox: {name}: a non-empty directory -- left for root to remove")
            except OSError:
                pass
        try:   # a directory, FIFO or socket planted under a *.toml name is refused, never read
            raw, queued = _inbox_read(path)
            req = tomllib.loads(raw.decode("utf-8")) if raw is not None else None
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            req, raw = None, b""
        except Exception as exc:  # noqa: BLE001 -- one entry never stops the run
            log(f"inbox: {name}: refused ({type(exc).__name__})")
            drop()
            continue
        if raw is None:
            log(f"inbox: {name}: refused (a symlink, or not a regular file of at most {INBOX_MAX_BYTES} bytes)")
            drop()
            continue
        if req is None:
            log(f"inbox: {name}: refused (does not parse as TOML)")
            drop()
            continue
        if not _inbox_proved(req):
            log(f"inbox: {name}: refused (no proof of the Portal's current credential)")
            drop()
            continue
        if edited and queued < edited:
            log(f"inbox: {name}: dropped (queued before the operator tier was last edited by hand; "
                "that newer edit wins)")
            drop()
            continue
        updates = {}
        for dotted in values:
            v = mios_toml._dotted_get(req, dotted)
            if isinstance(v, str) and v and not mios_toml._CTRL.search(v):
                updates[dotted] = v
        for pattern in toggles:
            for dotted in mios_toml._expand_toggle(data, pattern):
                v = mios_toml._dotted_get(req, dotted)
                if isinstance(v, bool):
                    updates[dotted] = v
        if not updates:
            log(f"inbox: {name}: nothing an operator-tier save may carry")
            drop()
            continue

        def work(updates=updates):  # as the operator: its own file, never through a link
            cfg_dir = os.path.dirname(op_file)
            dfd = _dir_chain(os.path.dirname(os.path.dirname(cfg_dir)),
                             (os.path.basename(os.path.dirname(cfg_dir)), os.path.basename(cfg_dir)), create=True)
            try:
                text = _read_at(dfd, os.path.basename(op_file), op_file) or ""
                _write_at(dfd, os.path.basename(op_file), _upsert_toml(text, updates), 0o600)
                return str(os.stat(os.path.basename(op_file), dir_fd=dfd, follow_symlinks=False).st_mtime_ns)
            finally:
                os.close(dfd)

        try:
            left = _as_owner(uid, gid, work)
        except (CredentialError, OSError) as exc:
            log(f"inbox: {name}: not merged ({exc if isinstance(exc, CredentialError) else type(exc).__name__}); "
                "it and every later request stay queued for the next run")
            break
        try:
            _atomic(mark, left, 0o600)
        except OSError as exc:
            log(f"inbox: the merge's mtime mark was not written ({exc.strerror}); a later save may be "
                "taken for one older than a hand edit")
        drop()
        merged += 1
        log(f"inbox: {name}: merged {', '.join(sorted(updates))} into the operator tier")
    return merged


def apply_lock(data: dict) -> int:
    """An exclusive flock on <[paths].var_credential_apply_dir>/lock, held (by the returned fd,
    until the process exits) across the inbox merge and every surface: the timer's run, a
    unit's ExecStartPre and forge-firstboot never interleave (no lost ledger update, no older
    Portal save merged after a newer one). A second caller waits for the first."""
    import fcntl
    d = _paths(data, "var_credential_apply_dir")
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(d, "lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def publish_operator_toggles(data: dict, log) -> None:
    """<[paths].run_operator_toggles>: {dotted: bool} for every [security.operator_tier]
    toggle as ROOT's view resolves it (0644, booleans only, never a credential). The Portal
    renders and diffs a save against it, since its own uid never reads the operator tier."""
    import json as _json
    import mios_toml  # noqa: PLC0415
    view = {}
    for pattern in _sect(data, "security.operator_tier").get("toggles") or []:
        for dotted in mios_toml._expand_toggle(data, str(pattern)):
            value = mios_toml._dotted_get(data, dotted)
            if isinstance(value, bool):
                view[dotted] = value
    try:
        path = _paths(data, "run_operator_toggles")
        text = _json.dumps(view, sort_keys=True) + "\n"
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    return
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        _atomic(path, text, 0o644)
    except (CredentialError, OSError) as exc:
        log(f"toggles: the Portal's view of the operator toggles was not published ({exc})")


def _vendor_tier() -> dict:
    """The vendor tier alone (the image's published defaults), or {} when it does not load."""
    import mios_toml  # noqa: PLC0415
    vendor, vendor_d, *_rest = mios_toml._tier_dirs()
    try:
        return mios_toml.load_strict([vendor] + mios_toml._frags(vendor_d))
    except mios_toml.LayerError:
        return {}


def apply(surfaces, data: dict, *, check=False, verify=False, wait=False, bootstrap=False,
          restart=True, log=None, env=None, taken=frozenset(), now=None, base=None) -> int:
    """Reconcile each surface to its SSOT credential. Logs carry only the surface name and
    changed / unchanged / skipped / drift / shadowed / failed -- never a value. A failed
    value is retried only after [security.credential_apply].retry_backoff_s (doubling to
    retry_backoff_max_s); a ledger that cannot be written is reported for its surface.
    `base` is the vendor/host view (read on demand when the operator tier supplied
    [identity].default_password): an _OWN_LOGIN surface may equal none of its credentials.
    A surface whose last value was operator-set is never moved to a vendor/host default:
    the whole run is refused (CredentialError, exit 3) and nothing is written. Returns 0
    when every surface is in sync (or was brought in sync), 1 on a failure or, with
    check=True, on drift."""
    import time as _time
    log = log or (lambda m: print(f"[mios-credential] {m}", file=sys.stderr))
    now = _time.time() if now is None else now

    def vendor_host():
        nonlocal base
        if base is None and "identity.default_password" not in taken:
            return data
        if base is None:
            import mios_toml  # noqa: PLC0415
            try:
                base = mios_toml.base_view()
            except mios_toml.LayerError as exc:
                raise CredentialError(f"MiOS SSOT: vendor/host tiers unresolved: {exc}") from None
        return base
    enabled = enabled_surfaces(data)
    policy = _sect(data, "security.credential_apply")
    base_s = int(_s(policy.get("retry_backoff_s")) or 0)
    max_s = int(_s(policy.get("retry_backoff_max_s")) or 0)
    ledger = Ledger(_paths(data, "var_credential_apply_dir"))
    downgraded = []
    for surface in (s for s in surfaces if s in enabled and s not in _OWN_LOGIN):
        try:
            got = surface_password(surface, data, env=env)
            fp = ledger.fingerprint(surface, surface_user(surface, data), got["value"])
        except CredentialError:
            continue                    # reported by the surface's own pass below
        if ledger.operator_set(surface) and not _operator_set(got["tier"], taken) and not ledger.same(surface, fp):
            downgraded.append(surface)
    if downgraded:
        raise CredentialError(f"refusing to move {', '.join(downgraded)} from the operator's credential back to "
                              "the vendor/host default (the operator tier no longer sets one): set a credential "
                              "in mios.html; nothing was changed")
    rc = 0
    for surface in surfaces:
        if surface not in enabled:
            log(f"{surface}: skipped (not in [security.credential_apply].surfaces)")
            continue
        try:
            got = surface_password(surface, data, env=env)
            user = surface_user(surface, data)
            collides = False
            if surface in _OWN_LOGIN:
                if not got["value"]:
                    got["value"] = ledger.own_secret(surface, create=not check)
                shared = {_s(_sect(view, "identity").get("default_password"))
                          for view in (data, vendor_host(), _vendor_tier())}
                for other in SURFACES:
                    if other not in _OWN_LOGIN:
                        try:
                            shared.add(surface_password(other, data, env=env)["value"])
                        except CredentialError:
                            pass
                collides = bool(got["value"]) and got["value"] in shared
            fp = ledger.fingerprint(surface, user, got["value"])
            due = ledger.backoff_until(surface, fp)
            if due > now and not check:
                log(f"{surface}: failed (this value failed before; retried after {int(due - now)} s)")
                rc = 1
                continue
            ctx = {"check": check, "verify": verify, "wait": wait,
                   "bootstrap": surface == "forge" and (bootstrap or not ledger.has(surface)),
                   "fp_same": ledger.same(surface, fp), "restarted": [], "notes": [], "no_record": False,
                   "restart": restart, "collides": collides,
                   "operator_set": _operator_set(got["tier"], taken)}
            if surface in _OWN_LOGIN and not got["value"]:
                state = "drift"                 # --check before the first apply generated it
            elif check and surface == "forge":
                state = "unchanged" if ctx["fp_same"] or not ctx["operator_set"] else "drift"
            else:
                state = _APPLY[surface](data, user, got["value"], ctx)
        except CredentialError as exc:
            log(f"{surface}: failed ({exc})")
            rc = 1
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"{surface}: failed ({type(exc).__name__})")
            rc = 1
            continue
        try:
            if state in ("changed", "unchanged") and not check and not ctx["no_record"]:
                ledger.record(surface, fp, ctx["operator_set"])
            if state.startswith("failed") and not check and not ctx["no_record"]:
                ledger.record_failure(surface, fp, now, base_s, max_s)
        except OSError as exc:   # e.g. a caller's sandbox keeps the ledger read-only
            ctx["notes"].append(f"{surface}: the ledger was not written ({exc.strerror or type(exc).__name__}); "
                                "the next run applies this surface again")
            rc = 1
        if state.startswith("failed") or (check and state == "drift"):
            rc = 1
        extra = f"; shadowed: {got['tier']} masks the operator tier" if got["shadowed"] else ""
        restarted = f"; restart queued: {' '.join(ctx['restarted'])}" if ctx["restarted"] else ""
        log(f"{surface}: {state}{extra}{restarted}")
        for note in ctx["notes"]:
            log(note)
    return rc


def ttyd_argv(args: list, env=None) -> list:
    """ttyd argv with -c user:password before ARGS, from the credstore `mios-credential apply
    ttyd` writes (the ttyd units ImportCredential= it): the service uid never re-resolves tiers
    it cannot fully read (root's secrets.env). ttyd takes the login only on argv, which every
    local uid can read in /proc, so it is ttyd's OWN login, never the operator credential
    (surface_password); ttyd behind the Portal session is the tracked follow-up."""
    env = os.environ if env is None else env
    pw, user = _imported(env, "mios.ttyd.password"), _imported(env, "mios.ttyd.user")
    if not (pw and user):
        raise CredentialError("no applied ttyd login ($CREDENTIALS_DIRECTORY/mios.ttyd.*): "
                              "run `mios-credential apply ttyd`, then restart the ttyd unit")
    return ["ttyd", "-c", f"{user}:{pw}"] + list(args)
