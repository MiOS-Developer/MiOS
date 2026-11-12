# AI-hint: Root writer of the Portal status view's spec.json (bootc deployments, work-tree dirt; git runs as the throwaway account) and the [portal.status] validation portal_status imports.
# AI-related: /usr/lib/mios/agent-pipe/mios_pipe/portal_status.py, /usr/lib/systemd/system/mios-podman-ps.service, /usr/lib/mios/agent-pipe/test_mios_portal.py, mios.toml [portal.status]
# AI-functions: write_spec, main

"""`python3 -P -m mios_pipe.portal_spec --write-spec` is the ExecStopPost=- of mios-podman-ps.service (root):
spec.json (mios.status.spec.v1) lands atomically in [portal.status].snapshot_dir. It reads only the SSOT file
layers and imports no web stack: portal_status imports _cfg from here, never the reverse.
"""

import json
import logging
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

logger = logging.getLogger("mios.portal_spec")

SPEC_SCHEMA = "mios.status.spec.v1"

# The throwaway account a root writer runs git as: never root, never the repo owner.
_GIT_USER = "nobody"
# The producer units whose cadence and budget [portal.status] must respect ([units.*] in the SSOT).
_SPEC_TIMER, _SPEC_SERVICE = "mios-podman-ps.timer", "mios-podman-ps.service"
# [portal.status] keys a user tier never moves: every producer and reader must agree on them.
_PINNED = ("snapshot_dir", "repo_root")

_KEYS = ("snapshot_dir", "repo_root", "goal_state", "goal_docs", "refresh_s", "cache_s", "stale_after_s",
         "task_rows", "agent_rows", "probe_timeout_s", "request_timeout_s")
_INT_RANGES = {"refresh_s": (1, 3600), "cache_s": (0, 3600), "stale_after_s": (1, 86400), "task_rows": (1, 64),
               "agent_rows": (1, 256), "probe_timeout_s": (1, 300), "request_timeout_s": (1, 60)}
_SPAN = re.compile(r"(\d{1,6})\s*(s|sec|min|h)?")
_SPAN_S = {None: 1, "s": 1, "sec": 1, "min": 60, "h": 3600}
_CTRL = re.compile("[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_HOME = re.compile(r"/(?:var/)?home/[^/\s]+")

def _text(value, limit=160):
    """Display text: control/bidi characters stripped, home paths folded to ~."""
    if not isinstance(value, str):
        return ""
    return _HOME.sub("~", _CTRL.sub("", value)).strip()[:limit]

def _opt(value, limit=160):
    text = _text(value, limit)
    return text or None

def _span(value):
    """Seconds of a plain systemd time span ("15s", "2min", or an integer), else None."""
    if type(value) is int:
        return value
    match = _SPAN.fullmatch(value.strip()) if isinstance(value, str) else None
    return int(match.group(1)) * _SPAN_S[match.group(2)] if match else None

def _mios_toml():
    lib = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import mios_toml
    return mios_toml

def _safe_rel(rel) -> bool:
    return (isinstance(rel, str) and bool(rel) and not os.path.isabs(rel)
            and ".." not in rel.replace("\\", "/").split("/"))

def _cfg(doc):
    """[portal.status] plus the relay budgets it reuses, or None (fail closed). Also None when a cadence
    relation breaks: stale_after_s must exceed both producer cadences, cache_s and the three request-path
    probes must fit inside one poll, and the writer's two probes inside its unit's TimeoutStopSec."""
    try:
        section = doc["portal"]["status"]
        cfg = {key: section[key] for key in _KEYS}
        relay = doc["mcp"]["agents"]
        cfg["max_state_bytes"] = relay["max_state_bytes"]
        cfg["max_agents"] = relay["max_agents"]
        observe = relay["observation"]["refresh_s"]
        cadence = _span(doc["units"][_SPEC_TIMER]["Timer"]["OnUnitActiveSec"])
        budget = _span(doc["units"][_SPEC_SERVICE]["Service"]["TimeoutStopSec"])
    except (KeyError, TypeError, AttributeError):
        return None
    for key in ("snapshot_dir", "repo_root"):
        if not isinstance(cfg[key], str) or not os.path.isabs(cfg[key]):
            return None
    if not (_safe_rel(cfg["goal_state"]) and _safe_rel(cfg["goal_docs"])):
        return None
    ranges = {**_INT_RANGES, "max_state_bytes": (1, 1 << 30), "max_agents": (1, 1 << 16)}
    for key, (low, high) in ranges.items():
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            return None
    if type(observe) is not int or cadence is None or budget is None:
        return None
    if not (cfg["stale_after_s"] > max(cadence, observe) and cfg["cache_s"] < cfg["refresh_s"]
            and 3 * cfg["request_timeout_s"] < cfg["refresh_s"] and 2 * cfg["probe_timeout_s"] < budget):
        return None
    return cfg

def _merge(paths, doc):
    mios_toml = _mios_toml()
    for path in paths:
        mios_toml.deep_merge(doc, mios_toml._load_one(path))
    return doc

def _file_layers():
    """(merged file layers, pins) in one pass over vendor < host < user, each tier's mios.d included. The
    pins are [units] as the vendor tier declares it (mios-unit-gen renders the booted units from it) and
    the _PINNED keys as vendor+host set them."""
    mios_toml = _mios_toml()
    vendor, vendor_d, host, host_d, user, user_d = mios_toml._tier_dirs()
    doc = _merge([vendor, *mios_toml._frags(vendor_d)], {})
    units = doc.pop("units", None)
    status = (_merge([host, *mios_toml._frags(host_d)], doc).get("portal") or {}).get("status") or {}
    pins = (units, {key: status.get(key) for key in _PINNED})
    return _merge([user, *mios_toml._frags(user_d)], doc), pins

def _pin(doc, pins):
    """doc with the pinned values in place; doc itself (possibly a shared cache) is never mutated."""
    units, keys = pins
    portal = doc.get("portal") if isinstance(doc.get("portal"), dict) else {}
    status = portal.get("status") if isinstance(portal.get("status"), dict) else {}
    return {**doc, "units": units, "portal": {**portal, "status": {**status, **keys}}}

def _writer_ssot() -> dict:
    """The root writer's view: the file layers only, pinned (no native resolver, no DB overlay)."""
    return _pin(*_file_layers())

def _bootc_probe(timeout):
    """mios.status.spec.v1 bootc block from `bootc status --format json`."""
    out = {"state": "unavailable", "booted_image": None, "booted_digest": None,
           "staged_image": None, "rollback_image": None}
    exe = shutil.which("bootc")
    if not exe:
        out["state"] = "not-bootc"
        return out
    try:
        proc = subprocess.run([exe, "status", "--format", "json"], capture_output=True, text=True, timeout=timeout)
        host = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        host = None
    if not isinstance(host, dict):
        return out
    status = host.get("status") if isinstance(host.get("status"), dict) else {}

    def slot(name):
        entry = status.get(name)
        image = entry.get("image") if isinstance(entry, dict) else None
        if not isinstance(image, dict):
            return None, None
        ref = image["image"].get("image") if isinstance(image.get("image"), dict) else None
        return _opt(ref, 300), _opt(image.get("imageDigest"), 100)

    booted, digest = slot("booted")
    out.update(state="booted" if isinstance(status.get("booted"), dict) else "not-bootc",
               booted_image=booted, booted_digest=digest,
               staged_image=slot("staged")[0], rollback_image=slot("rollback")[0])
    return out

def _git_identity():
    """(uid, gid) of the throwaway account a root writer runs git as, or None. Never root, never the
    repo owner: repo-controlled config (filters, hooks, fsmonitor, attributes) can then execute only
    as an account that gains nothing, whoever owns <root>/.git or its config files."""
    try:
        account = pwd.getpwnam(_GIT_USER)
    except KeyError:
        return None
    return None if 0 in (account.pw_uid, account.pw_gid) else (account.pw_uid, account.pw_gid)

def _probe_reads(root, rel, drop):
    """Whether the account git ran as can read <root>/<rel>: root lstats it and applies the owner, group
    or other read bit (no supplementary groups); a symlink needs no read permission."""
    path = os.path.join(os.fsencode(root), rel)
    try:
        info = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    if not drop:
        return os.access(path, os.R_OK)
    if info.st_uid == drop["user"]:
        return bool(info.st_mode & stat.S_IRUSR)
    return bool(info.st_mode & (stat.S_IRGRP if info.st_gid == drop["group"] else stat.S_IROTH))

def _git_dirty(root, timeout):
    """Modified tracked files. Under root, git runs as the throwaway account with no supplementary
    groups; an unprivileged caller runs it only over its own repo. No system, global or home config
    is read, core.fsmonitor is off, only <root> is a safe.directory. A file reported modified that
    the account cannot read (stat-dirty after a deployment) is not counted."""
    exe = shutil.which("git")
    try:
        info = os.lstat(os.path.join(root, ".git"))
    except OSError:
        return None
    if not exe or stat.S_ISLNK(info.st_mode):
        return None
    if os.geteuid() == 0:
        identity = _git_identity()
        if identity is None:
            return None
        drop = {"user": identity[0], "group": identity[1], "extra_groups": []}
    elif info.st_uid == os.geteuid():
        drop = {}
    else:
        return None
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0",
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    try:
        proc = subprocess.run([exe, "-c", "core.fsmonitor=false", "-c", f"safe.directory={root}", "-C", root,
                               "status", "--porcelain", "-z", "-uno"], capture_output=True,
                              timeout=timeout, env=env, cwd="/", **drop)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    fields, count, index = proc.stdout.split(b"\0"), 0, 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        if entry[:1] in (b"R", b"C"):
            index += 1  # -z puts a rename or copy source after its target
        if entry[:2] == b" M" and not _probe_reads(root, entry[3:], drop):
            continue
        count += 1
    return count

def _atomic_write(path, doc, mode=0o640):
    """tmp in the same directory, fsync, rename; no partial file survives a failure."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def write_spec(cfg, now=None):
    """Write <snapshot_dir>/spec.json (mios.status.spec.v1) and return its path."""
    doc = {"schema": SPEC_SCHEMA, "ts": int(time.time()) if now is None else now,
           "bootc": _bootc_probe(cfg["probe_timeout_s"]),
           "git_dirty": _git_dirty(cfg["repo_root"], cfg["probe_timeout_s"])}
    path = os.path.join(cfg["snapshot_dir"], "spec.json")
    _atomic_write(path, doc)
    return path

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["--write-spec"]:
        sys.stderr.write("usage: python3 -P -m mios_pipe.portal_spec --write-spec\n")
        return 2
    try:
        cfg = _cfg(_writer_ssot())
    except Exception:
        cfg = None
    if cfg is None:
        sys.stderr.write("portal-status: [portal.status] is missing or invalid\n")
        return 1
    try:
        path = write_spec(cfg)
    except OSError as error:
        sys.stderr.write(f"portal-status: cannot write the spec snapshot: {error.strerror or 'write failed'}\n")
        return 1
    print(json.dumps({"written": path}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
