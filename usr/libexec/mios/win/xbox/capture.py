#!/usr/bin/env python3
# AI-hint: MiOS-Xbox Start layout capture (xb-11, XB-1; operator decision "start2.bin/VisiblePlaces captured per build family on the worker"). Renders job\capture.json from xbox-spec.json (family, the start.folders/start.pins registry items the image gets, [windows.xbox.start.capture] account + timeouts), drives one GPU-less worker session (exchange disk -> define -> boot -> quiesce -> MiOS-Xbox-Capture.ps1 prepare -> reboot into the capture account's autologon -> collect -> shutdown -> read-only copy-out -> cleanup), verifies the record (schema, family, hashes, empty exported grid == start.pins, policy in effect) and installs <family>/start2.bin + visibleplaces.json into the MiOS tree atomically with the tree's owner. check_assets() is the build's family gate: a family with no asset, a missing file or a start2.bin that differs from its capture record refuses the build.
# AI-related: usr/share/mios/windows/build/MiOS-Xbox-Capture.ps1, usr/libexec/mios/win/xbox/worker.py, usr/libexec/mios/win/xbox/build.py, usr/libexec/mios/win/xbox/spec_items.py, usr/share/mios/mios.toml ([windows.xbox.start.layout_assets.*], [windows.xbox.start.capture]), tests/test-mios-xbox-build.py
# AI-functions: CaptureError, capture_job, run_capture, verify_capture, install_assets, check_assets, find_asset
"""MiOS-Xbox Start layout capture (driven by build.py; no CLI of its own)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from typing import Any, Callable, Dict, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, _HERE)
import worker as W  # noqa: E402

_TREE = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
WINDOWS_BUILD_DIR = os.path.join(_TREE, "usr", "share", "mios", "windows", "build")
SCRIPT = "MiOS-Xbox-Capture.ps1"
JOB_SCHEMA = "mios-xbox-capture-job/1"
CAPTURE_SCHEMA = "mios-xbox-start-capture/1"
START_OWNERS = ("start.folders", "start.pins")
_USER_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,19}")   # a Windows local account name (max 20)
MAX_START2 = 4 << 20


class CaptureError(RuntimeError):
    """The Start layout could not be captured or verified: the build must stop."""


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def find_asset(doc: Dict[str, Any], family: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """(SSOT key, asset table) of a build family, matched case-insensitively (BuildBranch casing varies)."""
    for k, a in (doc["spec"]["start"].get("layout_assets") or {}).items():
        if k.lower() == str(family).lower() and isinstance(a, dict):
            return k, a
    return None, None


# ---------------------------------------------------------------------------
# job
# ---------------------------------------------------------------------------

def capture_job(doc: Dict[str, Any], family: Optional[str], job_dir: str) -> Dict[str, Any]:
    """job_dir/{MiOS-Xbox-Capture.ps1, capture.json}: everything the guest needs, credential-free."""
    x = doc["spec"]
    c = x["start"].get("capture")
    if not isinstance(c, dict):
        raise CaptureError("windows.xbox.start.capture missing")
    user = str(c.get("user", ""))
    if not _USER_RE.fullmatch(user):
        raise CaptureError(f"windows.xbox.start.capture.user {user!r} is not a valid local account name")
    users = doc.get("users") or {}
    taken = {str((users.get(r) or {}).get("name", "")).lower() for r in ("desktop", "service_account")} | {"administrator"}
    if user.lower() in taken:
        raise CaptureError(f"windows.xbox.start.capture.user {user!r} collides with a real account of the image")
    regs = [r for r in doc["registry"] if r["owner"] in START_OWNERS]
    if not any(r["owner"] == "start.pins" for r in regs) or not any(r["owner"] == "start.folders" for r in regs):
        raise CaptureError("xbox-spec.json has no start.pins / start.folders registry items")
    for r in regs:
        if r["hive"] != "SOFTWARE" or r.get("delete") or r["type"] not in ("REG_DWORD", "REG_SZ"):
            raise CaptureError(f"Start policy item {r['owner']}:{r['name']} is not a SOFTWARE value")
    job = {
        "schema": JOB_SCHEMA, "family": family or "", "family_source": x["start"]["layout_family_source"],
        "user": user, "logon_timeout_s": int(c["logon_timeout_s"]), "settle_s": int(c["settle_s"]),
        "open_start_after_s": int(c["open_start_after_s"]), "pins": list(x["start"]["pins"]),
        "registry": [{"key": r["key"], "name": r["name"], "type": r["type"], "data": r["data"]} for r in regs],
    }
    if os.path.exists(job_dir):
        shutil.rmtree(job_dir)
    os.makedirs(job_dir, mode=0o700)
    shutil.copyfile(os.path.join(WINDOWS_BUILD_DIR, SCRIPT), os.path.join(job_dir, SCRIPT))
    with open(os.path.join(job_dir, "capture.json"), "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=2, sort_keys=True)
    return job


# ---------------------------------------------------------------------------
# worker session
# ---------------------------------------------------------------------------

def run_capture(cfg: "W.WorkerConfig", job_dir: str, dest: str, runner: W.Runner = W._default_runner,
                session_factory: Any = None, sleep: Optional[Callable[[float], None]] = None,
                guards: Optional[Dict[str, Any]] = None, log: Callable[[str], None] = lambda m: None) -> str:
    """One worker session; the worker is always stopped, copied out read-only and removed. Returns the out dir."""
    kw = {"sleep": sleep} if sleep else {}
    guards = guards or {}
    W.preflight(cfg, runner, **guards)
    try:
        W.prepare_exchange(cfg, job_dir, runner)
        W.define(cfg, runner)
        W.start(cfg, runner, **guards)
        log(f"capture: worker {cfg.name} started")
        sess = W.wait_winrm(cfg, runner, session_factory=session_factory, **kw)
        log(f"capture: {W.quiesce(sess)}")
        log("capture: prepare " + W.run_exchange_script(sess, SCRIPT, "-Phase prepare").strip()[-600:])
        sess = W.reboot_and_wait(cfg, sess, runner, session_factory=session_factory, **kw)
        log("capture: worker rebooted into the capture account's autologon")
        out = W.run_exchange_script(sess, SCRIPT, "-Phase collect").strip()
        log(f"capture: collect {len(out)} bytes of record")
    finally:
        W.stop(cfg, runner, **kw)
        if os.path.exists(cfg.exchange):
            try:
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                W.extract(cfg, dest, runner)
            except W.WorkerError as e:
                log(f"capture: copy-out failed: {e}")
        W.cleanup(cfg, runner, **kw)   # the overlay held the capture account's autologon secret
        shutil.rmtree(job_dir, ignore_errors=True)
    return os.path.join(dest, "out")


# ---------------------------------------------------------------------------
# verification + install
# ---------------------------------------------------------------------------

def verify_capture(out_dir: str, doc: Dict[str, Any], family: Optional[str]) -> Tuple[Dict[str, Any], bytes, str]:
    """(record, start2 bytes, SSOT family key) or CaptureError naming what is wrong."""
    try:
        with open(os.path.join(out_dir, "capture.json"), encoding="utf-8-sig") as fh:
            rec = json.load(fh)
        with open(os.path.join(out_dir, "start2.bin"), "rb") as fh:
            data = fh.read(MAX_START2 + 1)
    except (OSError, ValueError) as e:
        raise CaptureError(f"capture output unreadable ({type(e).__name__}: {e})") from None
    if rec.get("schema") != CAPTURE_SCHEMA or rec.get("status") != "done":
        raise CaptureError("capture.json is not a finished mios-xbox-start-capture/1 record")
    fam = str(rec.get("family") or "")
    if not fam:
        raise CaptureError("capture record names no build family")
    if family and fam.lower() != str(family).lower():
        raise CaptureError(f"captured family {fam!r} is not the image family {family!r}")
    key, asset = find_asset(doc, fam)
    if not key:
        raise CaptureError(f"captured family {fam!r} has no [windows.xbox.start.layout_assets.{fam}] in the SSOT")
    if not data or len(data) > MAX_START2:
        raise CaptureError(f"start2.bin size {len(data)} is implausible")
    if _sha256_bytes(data) != str(rec.get("start2_sha256", "")).lower() or len(data) != int(rec.get("start2_size", -1)):
        raise CaptureError("start2.bin differs from its capture record")
    pins = doc["spec"]["start"]["pins"]
    export = rec.get("pinned_export")
    if not isinstance(export, dict) or "pinnedList" not in export:
        raise CaptureError("capture record has no exported Start layout (pinnedList)")
    if list(export.get("pinnedList") or []) != list(pins):
        raise CaptureError(f"captured Start grid has {len(export.get('pinnedList') or [])} pins; windows.xbox.start.pins has {len(pins)}")
    want = {(r["key"].lower(), r["name"].lower()): r["data"] for r in doc["registry"] if r["owner"] in START_OWNERS}
    seen = {(str(o.get("key", "")).lower(), str(o.get("name", "")).lower()): o.get("value") for o in rec.get("policy_observed") or []}
    for k, v in want.items():
        if k not in seen or seen[k] != v:
            raise CaptureError(f"Start policy {k[0]}\\{k[1]} was not in effect during the capture")
    return rec, data, key


def _owner_of(path: str) -> Tuple[int, int]:
    p = os.path.abspath(path)
    while not os.path.exists(p):
        p = os.path.dirname(p)
    st = os.stat(p)
    return st.st_uid, st.st_gid


def _atomic(path: str, data: bytes, uid: int, gid: int, mode: int = 0o644) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".capture.", dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        if os.geteuid() == 0:
            os.chown(tmp, uid, gid)
        os.replace(tmp, path)
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def install_assets(doc: Dict[str, Any], key: str, rec: Dict[str, Any], data: bytes, tree: str = _TREE,
                   worker: str = "") -> Dict[str, str]:
    """<tree>/<asset.start2> + <asset.visible_places> (the capture record), owned like the tree."""
    asset = doc["spec"]["start"]["layout_assets"][key]
    s2 = os.path.join(tree, asset["start2"])
    vp = os.path.join(tree, asset["visible_places"])
    uid, gid = _owner_of(s2)
    for d in sorted({os.path.dirname(s2), os.path.dirname(vp)}):
        missing = []
        p = d
        while not os.path.exists(p):
            missing.append(p)
            p = os.path.dirname(p)
        for m in reversed(missing):
            os.mkdir(m, 0o755)
            os.chmod(m, 0o755)
            if os.geteuid() == 0:
                os.chown(m, uid, gid)
    vph = rec.get("visible_places")
    record = {
        "schema": CAPTURE_SCHEMA, "family": key, "family_source": rec.get("family_source"),
        "build": rec.get("build"), "ubr": rec.get("ubr"), "edition": rec.get("edition"),
        "captured_at": rec.get("captured_at"), "captured_on": worker or None,
        "start2": asset["start2"], "start2_sha256": _sha256_bytes(data), "start2_size": len(data),
        "pinned_export": rec.get("pinned_export"),
        "folders": list(doc["spec"]["start"]["folders"]),
        "visible_places": vph,
        "visible_places_note": None if vph else
        "Windows wrote no VisiblePlaces value: the Start folder row is enforced by the start.folders policy "
        "(Policy CSP AllowPinnedFolder*), which was in effect during the capture (policy_observed)",
        "policy_observed": rec.get("policy_observed"),
        "neutralized_legacy": rec.get("neutralized_legacy"),
        "opened_start": rec.get("opened_start"),
    }
    _atomic(s2, data, uid, gid)
    _atomic(vp, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(), uid, gid)
    return {"start2": s2, "visible_places": vp}


def check_assets(doc: Dict[str, Any], family: str, tree: str = _TREE) -> Optional[str]:
    """None when <family> has a captured, self-consistent Start layout; else the reason (the build refuses)."""
    key, asset = find_asset(doc, family)
    if not key:
        return f"image build family {family!r} has no [windows.xbox.start.layout_assets.{family}]"
    s2 = os.path.join(tree, asset.get("start2", ""))
    vp = os.path.join(tree, asset.get("visible_places", ""))
    if not os.path.isfile(s2):
        return f"{asset.get('start2')} (family {key}) is not captured yet"
    if not os.path.isfile(vp):
        return f"{asset.get('visible_places')} (family {key}) is not captured yet"
    try:
        with open(vp, encoding="utf-8") as fh:
            rec = json.load(fh)
        with open(s2, "rb") as fh:
            sha = _sha256_bytes(fh.read())
    except (OSError, ValueError) as e:
        return f"Start layout assets of family {key} unreadable ({type(e).__name__})"
    if rec.get("schema") != CAPTURE_SCHEMA or str(rec.get("family", "")).lower() != key.lower():
        return f"{asset.get('visible_places')} is not the capture record of family {key}"
    if rec.get("start2_sha256") != sha:
        return f"{asset.get('start2')} differs from its capture record (altered or re-captured without a record)"
    if list((rec.get("pinned_export") or {}).get("pinnedList") or []) != list(doc["spec"]["start"]["pins"]):
        return f"the captured Start grid of family {key} is not windows.xbox.start.pins (re-capture)"
    return None
