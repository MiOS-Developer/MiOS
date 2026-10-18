#!/usr/bin/env python3
# AI-hint: THE MiOS-Xbox image build entry (xb-15, XB-1..XB-4): render_spec (layered SSOT -> xbox-spec.json) -> resolve_latest (newest upstream, no pins -> build-manifest.json) -> fetch (download + verify every artefact) -> family (offline: the image's BuildBranch has a captured Start layout) -> answer (autounattend + SetupComplete chain + offline .reg + components from the SAME spec) -> conform rendered gate -> GPU-less worker over WinRM (UUP Dump converter + New-MiOSXboxImage.ps1 DISM servicing + ISO) -> read-only copy-out -> conform image gate -> build-<ts>/ manifest. Every stage fails closed; heavy stages run under the SSOT heavy lock in a MemoryMax scope; the worker never runs next to the target/base VM and never holds a GPU.
# AI-related: usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/resolve_latest.py, usr/libexec/mios/win/xbox/fetch.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/worker.py, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, usr/share/mios/windows/build/MiOS-Xbox-Worker.ps1, tests/test-mios-xbox-build.py
# AI-functions: build_layers, plan, Build, stage_render, stage_resolve, stage_fetch, stage_family, stage_payload, stage_gate_rendered, stage_job, stage_worker, stage_gate_image, stage_manifest, run, main
"""MiOS-Xbox build.

  build.py plan                      # validate SSOT, tree, tools, VM guards; writes nothing
  sudo build.py capture [--family F] # worker: capture start2.bin + VisiblePlaces of the worker's (= image's) build family (xb-11)
  sudo build.py stage                # render -> resolve -> fetch -> family (capture when missing) -> payload -> rendered gate -> job
  sudo build.py service --build-dir D  # worker: convert + DISM + ISO, copy-out
  sudo build.py gate --build-dir D     # offline image gate on the ISO + negative controls (legacy media) + final manifest
  sudo build.py run                  # all of the above

Exit 0 ok; 1 a gate found non-conformance; 2 a stage failed (named). Credentials never printed."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
sys.path.insert(0, _HERE)
import render_spec  # noqa: E402
import resolve_latest as RL  # noqa: E402
import spec_items as S  # noqa: E402
import conform as C  # noqa: E402
import answer as A  # noqa: E402
import fetch as F  # noqa: E402
import worker as W  # noqa: E402
import capture as CAP  # noqa: E402

mios_toml = render_spec.mios_toml
SCOPED_ENV = "MIOS_XBOX_BUILD_SCOPED"
WINDOWS_BUILD_DIR = os.path.join(_TREE, "usr", "share", "mios", "windows", "build")
HEAVY = ("capture", "stage", "service", "gate", "run")


class BuildError(RuntimeError):
    """A build stage failed (the message names the stage and the key/artefact; never a secret)."""


# ---------------------------------------------------------------------------
# layers + small helpers
# ---------------------------------------------------------------------------

def build_layers(env: Optional[Dict[str, str]] = None) -> List[str]:
    """vendor -> host -> user layers. Under sudo the user tier is the invoking operator's, not root's."""
    env = os.environ if env is None else env
    if os.geteuid() == 0 and env.get("SUDO_USER") and not env.get("MIOS_USER_TOML"):
        import pwd
        home = pwd.getpwnam(env["SUDO_USER"]).pw_dir
        os.environ["MIOS_USER_TOML"] = os.path.join(home, ".config", "mios", "mios.toml")
        os.environ.setdefault("MIOS_USER_TOML_D", os.path.join(home, ".config", "mios", "mios.d"))
    return [p for p in mios_toml.layer_paths() if os.path.isfile(p)]


def _json_write(path: str, data: Any, mode: int = 0o644) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
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


def _json_read(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _link_tree(src: str, dst: str) -> None:
    """Copy a tree, hard-linking files where possible (same filesystem), never following symlinks."""
    for d, dirs, files in os.walk(src):
        rel = os.path.relpath(d, src)
        os.makedirs(os.path.join(dst, rel), mode=0o700, exist_ok=True)
        for f in files:
            s, t = os.path.join(d, f), os.path.join(dst, rel, f)
            if os.path.islink(s):
                raise BuildError(f"job staging: {s} is a symlink (refused)")
            try:
                os.link(s, t)
            except OSError:
                shutil.copy2(s, t)


# ---------------------------------------------------------------------------
# plan (read-only)
# ---------------------------------------------------------------------------

def _presence(path: str) -> str:
    """'present' | 'missing' | 'unreadable (root-only; run plan with sudo)' -- plan never guesses."""
    import stat as _stat
    try:
        return "present" if _stat.S_ISREG(os.stat(path).st_mode) else "missing"
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "unreadable (root-only; run plan with sudo)"



def plan(layers: Optional[List[str]] = None, runner: W.Runner = W._default_runner) -> Dict[str, Any]:
    """Everything that can be checked without writing or downloading. Raises BuildError on a hard fault."""
    layers = layers if layers is not None else build_layers()
    try:
        merged = mios_toml.load_merged(layers)
        doc = render_spec.render(merged, layers)
        cfg = RL.load_config(merged)
    except (render_spec.SpecError, RL.ResolveError) as e:
        raise BuildError(f"[plan] {e}") from None
    for u_key, u_data in (doc.get("users") or {}).items():
        if isinstance(u_data, dict) and "password" in u_data:
            pw = str(u_data["password"]).strip()
            if not pw:
                raise BuildError(f"[plan] users.{u_key}.password: empty credential")
            if pw.lower() == "mios":
                raise BuildError(f"[plan] users.{u_key}.password: equals vendor default 'mios' (refused)")
    x = doc["spec"]
    problems: List[str] = []
    for c in x["components"]["ship"]:
        if c["kind"] != "native" and not os.path.isfile(os.path.join(_TREE, c["source"])):
            problems.append(f"component {c['name']}: {c['source']} missing")
    for p in (x["drivers"]["gpu_step"], "usr/share/mios/windows/build/New-MiOSXboxImage.ps1",
              "usr/share/mios/windows/build/MiOS-Xbox-Worker.ps1", "usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1",
              "usr/share/mios/windows/mios-looking-glass-idd.ps1"):
        if not os.path.isfile(os.path.join(_TREE, p)):
            problems.append(f"{p} missing")
    fam_why = {f: CAP.check_assets(doc, f, _TREE) for f in x["start"]["layout_assets"]}
    fams = {f: why is None for f, why in fam_why.items()}
    tools = {t: bool(shutil.which(t)) for t in ("wimlib-imagex", "perl", "hivexregedit", "7z", "rpm2cpio", "cpio",
                                                "qemu-img", "virsh", "guestfish")}
    problems += [f"host tool {t} missing" for t, ok in tools.items() if not ok]
    states = {d: W.domstate(d, runner) for d in (x["vm"]["name"], x["vm"]["worker"]["overlay_of"], x["vm"]["worker"]["name"])}
    try:
        mem = round(W.mem_available_gib(), 1)
    except W.WorkerError:
        mem = None
    need = x["vm"]["worker"]["memory_gib"] + x["build"]["memory_headroom_gib"]
    blockers: List[str] = []
    if not any(fams.values()):
        blockers.append("no Start layout family is captured yet (xb-11): the build captures it on the worker first")
    for d in (x["vm"]["name"], x["vm"]["worker"]["overlay_of"]):
        if states.get(d) and "shut off" not in str(states[d]):
            blockers.append(f"{d} is {states[d]}: cold-stop it before the worker starts")
    if mem is not None and mem < need:
        blockers.append(f"MemAvailable {mem} GiB < {need} GiB (worker + headroom)")
    if not W.watchdog_inactive():
        blockers.append("hardware watchdog armed")
    return {
        "schema": "mios-xbox-plan/1",
        "layers": layers,
        "source": {k: cfg[k] for k in ("uup_ring", "uup_arch", "uup_edition", "uup_lang")},
        "desktop_account": doc["users"]["desktop"]["name"],
        "autologon_logon_count": doc["users"]["desktop"]["logon_count"],
        "service_account_never_autologon": doc["users"]["service_account"]["name"],
        "registry_values": len(doc["registry"]),
        "feature_toggles": sum(1 for f in doc["feature_overrides"] if f["enabled"]),
        "components": [c["name"] for c in x["components"]["ship"]],
        "start_layout_assets_captured": fams,
        "start_layout_assets_status": {f: why or "captured" for f, why in fam_why.items()},
        "negative_controls": {p: _presence(p) for p in x["gate"].get("negative_controls") or []},
        "tools": tools,
        "domains": states,
        "mem_available_gib": mem,
        "worker_needs_gib": need,
        "watchdog_inactive": W.watchdog_inactive(),
        "problems": problems,
        "blockers": blockers,
    }


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------

class Build:
    def __init__(self, root: str, layers: List[str], log: Optional[Callable[[str], None]] = None):
        self.root = root
        self.layers = layers
        self._log = log
        self.spec_path = os.path.join(root, "xbox-spec.json")
        self.guest_spec_path = os.path.join(root, "xbox-spec.guest.json")
        self.manifest_path = os.path.join(root, RL.MANIFEST_NAME)
        self.payload = os.path.join(root, "payload")
        self.reports = os.path.join(root, "reports")
        self.out = os.path.join(root, "worker-out")
        self.doc: Optional[Dict[str, Any]] = None

    @classmethod
    def new(cls, artifact_root: str, layers: List[str], now: Optional[_dt.datetime] = None) -> "Build":
        ts = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        os.makedirs(artifact_root, exist_ok=True)
        root = os.path.join(artifact_root, f"build-{ts}")
        os.mkdir(root, 0o700)   # refuses an existing build directory
        os.makedirs(os.path.join(root, "reports"), mode=0o700)
        return cls(root, layers)

    def log(self, msg: str) -> None:
        line = f"[{_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
        print(line, flush=True)
        p = os.path.join(self.root, "build.log")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self._log:
            self._log(msg)

    def spec(self) -> Dict[str, Any]:
        if self.doc is None:
            self.doc = _json_read(self.spec_path)
        return self.doc

    @property
    def work(self) -> str:
        return os.path.join(self.spec()["spec"]["build"]["work_root"], os.path.basename(self.root))

    def step(self, name: str, fn: Callable[[], Any]) -> Any:
        self.log(f"stage {name}: start")
        try:
            r = fn()
        except (render_spec.SpecError, RL.ResolveError, F.FetchError, A.PayloadError, W.WorkerError, S.SpecError,
                C.ToolError, CAP.CaptureError, OSError, ValueError, KeyError) as e:
            self.log(f"stage {name}: FAILED: {type(e).__name__}: {e}")
            raise BuildError(f"[{name}] {e}") from None
        self.log(f"stage {name}: ok")
        return r


def stage_render(b: Build) -> Dict[str, Any]:
    merged = mios_toml.load_merged(b.layers)
    doc = render_spec.render(merged, b.layers)
    render_spec._write_atomic(b.spec_path, json.dumps(doc, indent=2, sort_keys=True) + "\n")   # 0600: credentials
    _json_write(b.guest_spec_path, render_spec.guest_projection(doc))
    b.doc = doc
    return {"registry": len(doc["registry"]), "layers": len(b.layers)}


def stage_resolve(b: Build, http: Any = None, sleep: Callable[[float], None] = None) -> Dict[str, Any]:
    cfg = RL.load_config(mios_toml.load_merged(b.layers))
    manifest = RL.resolve_all(cfg, http or RL.Http(), **({"sleep": sleep} if sleep else {}))
    RL.write_manifest_atomic(b.manifest_path, manifest)
    return {n: a["version"] for n, a in manifest["artifacts"].items()}


def stage_fetch(b: Build, dl: Optional[F.Downloader] = None, http: Any = None) -> Dict[str, Any]:
    inputs = F.fetch_all(_json_read(b.manifest_path), b.spec(), os.path.join(b.work, "inputs"), dl, http)
    _json_write(os.path.join(b.work, "inputs.json"), inputs, 0o600)
    return inputs


def _edition_esd(uup_dir: str, edition: str, lang: str) -> Optional[str]:
    wants = (f"{edition}_{lang}.esd".lower(), f"metadataesd_{edition}_{lang}.esd".lower())
    for n in sorted(os.listdir(uup_dir)):
        if n.lower() in wants:
            return os.path.join(uup_dir, n)
    return None


def image_family(b: Build) -> str:
    """Offline inspection (wimlib + hivex, read-only): the build family (start.layout_family_source, i.e. the
    BuildBranch) of the edition ESD -- known before any VM starts."""
    x = b.spec()["spec"]
    inputs = _json_read(os.path.join(b.work, "inputs.json"))
    man = _json_read(b.manifest_path)["artifacts"]["windows_uup"]
    esd = _edition_esd(inputs["uup_dir"], man["edition"], man["lang"])
    if not esd:
        raise BuildError(f"[family] the UUP set has no {man['edition']}_{man['lang']}.esd")
    idx = C.pick_index(esd, x["source"]["edition"])
    tmp = tempfile.mkdtemp(prefix="family.", dir=b.work)
    try:
        C.wim_extract(esd, idx, ["/Windows/System32/config/SOFTWARE"], tmp)
        hive = os.path.join(tmp, "Windows", "System32", "config", "SOFTWARE")
        text = C.hive_keys(hive, "HKEY_LOCAL_MACHINE\\SOFTWARE", ["\\Microsoft\\Windows NT\\CurrentVersion"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    obs = C.Observations()
    C.parse_reg_text(text, "SOFTWARE", obs)
    src = x["start"]["layout_family_source"].lower()
    fam = next((str(o.value) for o in obs.reg if o.name == src), "")
    if not fam:
        raise BuildError(f"[family] the image has no {x['start']['layout_family_source']} value")
    return fam


def stage_family(b: Build, fam: Optional[str] = None, tree: str = _TREE) -> str:
    """The image's build family must have a captured, self-consistent Start layout ([windows.xbox.start.
    layout_assets.<family>], xb-11). Returns the SSOT family key; no fallback blob."""
    fam = fam or image_family(b)
    why = CAP.check_assets(b.spec(), fam, tree)
    if why:
        raise BuildError(f"[family] {why} (capture it on the worker: build.py capture, xb-11); no fallback blob")
    return CAP.find_asset(b.spec(), fam)[0]


def stage_capture(b: Build, family: Optional[str], runner: W.Runner = W._default_runner, session_factory: Any = None,
                  sleep: Optional[Callable[[float], None]] = None, guards: Optional[Dict[str, Any]] = None,
                  tree: str = _TREE) -> str:
    """xb-11 on the GPU-less worker: capture start2.bin + VisiblePlaces of ``family`` (None = the worker's own,
    which must have an SSOT layout_assets entry), verify the record, install it into the MiOS tree."""
    doc = b.spec()
    cfg = W.WorkerConfig.from_spec(doc, os.path.join(b.work, "capture-worker"))
    job = os.path.join(b.work, "capture", "job")
    CAP.capture_job(doc, family, job)
    out = CAP.run_capture(cfg, job, os.path.join(b.work, "capture", "copy-out"), runner, session_factory, sleep, guards, b.log)
    rec, data, key = CAP.verify_capture(out, doc, family)
    paths = CAP.install_assets(doc, key, rec, data, tree, cfg.name)
    shutil.copyfile(paths["visible_places"], os.path.join(b.reports, "start-capture.json"))
    why = CAP.check_assets(doc, key, tree)
    if why:
        raise BuildError(f"[capture] the installed assets do not verify: {why}")
    b.log(f"captured Start layout of family {key} (build {rec.get('build')}.{rec.get('ubr')}): start2.bin "
          f"{CAP._sha256_bytes(data)}, {len((rec.get('pinned_export') or {}).get('pinnedList') or [])} pins, "
          f"VisiblePlaces {'captured' if rec.get('visible_places') else 'not written by Windows (policy-enforced folders)'}")
    return key


def stage_payload(b: Build) -> Dict[str, Any]:
    inputs = _json_read(os.path.join(b.work, "inputs.json"))
    if os.path.exists(b.payload):
        shutil.rmtree(b.payload)
    s = A.render_payload(b.spec(), inputs, b.payload, _TREE)
    os.chmod(b.payload, 0o700)
    return {"components": len(s["components"]), "installers": len(s["installers"])}


def stage_gate_rendered(b: Build) -> None:
    inputs = _json_read(os.path.join(b.work, "inputs.json"))
    rep = os.path.join(b.reports, "conform-rendered.json")
    rc = C.run_rendered(b.spec_path, [b.payload, inputs["drivers_dir"]], rep, _TREE)
    if rc != 0:
        raise BuildError(f"[gate-rendered] the rendered inputs are NON-CONFORMING (see {rep})")


def stage_job(b: Build, family: str) -> str:
    x = b.spec()["spec"]
    inputs = _json_read(os.path.join(b.work, "inputs.json"))
    job = os.path.join(b.work, "job")
    if os.path.exists(job):
        shutil.rmtree(job)
    os.makedirs(job, mode=0o700)
    for f in ("MiOS-Xbox-Worker.ps1", "New-MiOSXboxImage.ps1"):
        shutil.copyfile(os.path.join(WINDOWS_BUILD_DIR, f), os.path.join(job, f))
    shutil.copyfile(b.guest_spec_path, os.path.join(job, "xbox-spec.json"))
    _link_tree(b.payload, os.path.join(job, "payload"))
    _link_tree(inputs["drivers_dir"], os.path.join(job, "drivers"))
    _link_tree(inputs["uup_dir"], os.path.join(job, "uup"))
    _link_tree(inputs["converter_dir"], os.path.join(job, "converter"))
    sa = os.path.join(job, "start-assets")
    index: Dict[str, Any] = {}
    for fam, a in x["start"]["layout_assets"].items():
        p = os.path.join(_TREE, a["start2"])
        if os.path.isfile(p):
            os.makedirs(os.path.join(sa, fam), exist_ok=True)
            shutil.copyfile(p, os.path.join(sa, fam, "start2.bin"))
            index[fam] = {"start2_sha256": A._sha256(p)}
    if family.lower() not in {k.lower() for k in index}:
        raise BuildError(f"[job] Start layout of family {family} is not staged")
    os.makedirs(sa, exist_ok=True)
    _json_write(os.path.join(sa, "start-assets.json"), index)
    _json_write(os.path.join(job, "job.json"), {
        "schema": "mios-xbox-job/1", "build": os.path.basename(b.root), "family": family,
        "converter_entry": x["build"]["converter"]["entry"], "iso_name": x["build"]["iso_name"],
        "iso_label": x["build"]["iso_label"]})
    return job


def stage_worker(b: Build, job: str, runner: W.Runner = W._default_runner, session_factory: Any = None,
                 sleep: Optional[Callable[[float], None]] = None, guards: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = W.WorkerConfig.from_spec(b.spec(), os.path.join(b.work, "worker"))
    kw = {"sleep": sleep} if sleep else {}
    W.preflight(cfg, runner, **(guards or {}))
    status: Dict[str, Any] = {}
    try:
        W.prepare_exchange(cfg, job, runner)
        W.define(cfg, runner)
        W.start(cfg, runner, **(guards or {}))
        b.log(f"worker {cfg.name} started")
        sess = W.wait_winrm(cfg, runner, session_factory=session_factory, **kw)
        b.log(f"worker {W.quiesce(sess)}")
        W.submit_job(sess)
        status = W.poll_job(cfg, sess, log=b.log, **kw)
    finally:
        W.stop(cfg, runner, **kw)
        if os.path.exists(cfg.exchange):
            try:
                if os.path.exists(b.out):
                    shutil.rmtree(b.out)
                W.extract(cfg, b.out, runner)
            except W.WorkerError as e:
                b.log(f"copy-out failed: {e}")
        W.cleanup(cfg, runner, **kw)   # removes overlay + exchange (it carried the answer file)
        shutil.rmtree(job, ignore_errors=True)
    if status.get("status") != "done":
        raise W.WorkerError(f"worker job {status.get('stage', '?')} failed: {status.get('error', 'no status')}")
    return status


def _out(b: Build) -> str:
    d = os.path.join(b.out, "out")
    return d if os.path.isdir(d) else b.out


def _iso_gate(b: Build, iso: str, report: str, index: int = 0) -> int:
    """conform.py image mode on an ISO: its install.wim/.esd + root answer file (read-only 7z extraction)."""
    tmp = tempfile.mkdtemp(prefix="gate.", dir=b.work)
    try:
        r = subprocess.run(["7z", "x", "-y", "-bd", "-ssc-", f"-o{tmp}", iso, "sources/install.wim", "sources/install.esd",
                            "autounattend.xml"], capture_output=True, timeout=3600)
        src = next((os.path.join(tmp, "sources", n) for n in ("install.wim", "install.esd")
                    if os.path.isfile(os.path.join(tmp, "sources", n))), None)
        if r.returncode not in (0, 1) or not src:
            raise BuildError(f"cannot read sources/install.wim|esd from {iso}")
        ans = os.path.join(tmp, "autounattend.xml")
        return C.run_image(b.spec_path, src, index, ans if os.path.isfile(ans) else None,
                           os.path.join(tmp, "x"), report, _TREE)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stage_gate_image(b: Build) -> Dict[str, Any]:
    x = b.spec()["spec"]
    res = _json_read(os.path.join(_out(b), "result.json"))
    iso_src = os.path.join(_out(b), x["build"]["iso_name"])
    if res.get("status") != "done" or not os.path.isfile(iso_src):
        raise BuildError("[gate-image] the worker produced no ISO")
    if A._sha256(iso_src) != str((res.get("iso") or {}).get("sha256", "")).lower():
        raise BuildError("[gate-image] the copied-out ISO differs from the worker's record")
    iso = os.path.join(b.root, x["build"]["iso_name"])
    os.replace(iso_src, iso)
    os.chmod(iso, 0o600)   # it carries the rendered answer file (credentials)
    rep = os.path.join(b.reports, "conform-image.json")
    rc = _iso_gate(b, iso, rep, 1)
    if rc != 0:
        os.replace(iso, iso + ".rejected")
        raise BuildError(f"[gate-image] the image is NON-CONFORMING (see {rep}); ISO renamed .rejected")
    return {"iso": iso, "result": res}


def stage_gate_negative(b: Build) -> Dict[str, str]:
    """The same image gate on the legacy media ([windows.xbox.gate].negative_controls) must report NON-CONFORMING:
    a gate that passes legacy media does not bite, and the build fails."""
    ctl = b.spec()["spec"]["gate"].get("negative_controls") or []
    if not ctl:
        raise BuildError("[gate-negative] windows.xbox.gate.negative_controls is empty (the image gate needs a negative control)")
    reports: Dict[str, str] = {}
    for iso in ctl:
        if not os.path.isfile(iso):
            raise BuildError(f"[gate-negative] negative control {iso} missing")
        name = re.sub(r"[^\w.-]", "_", os.path.basename(iso))
        rep = os.path.join(b.reports, f"conform-negative-{name}.json")
        rc = _iso_gate(b, iso, rep)
        if rc != 1:
            raise BuildError(f"[gate-negative] {iso} was not reported NON-CONFORMING (rc {rc}): the gate does not bite")
        reports[iso] = os.path.relpath(rep, b.root)
        b.log(f"negative control {os.path.basename(iso)}: NON-CONFORMING as required ({reports[iso]})")
    return reports


def stage_manifest(b: Build, image: Dict[str, Any], negative: Optional[Dict[str, str]] = None) -> None:
    m = _json_read(b.manifest_path)
    res = image["result"]
    key, asset = CAP.find_asset(b.spec(), str(res.get("family") or ""))
    m["build"] = {
        "id": os.path.basename(b.root),
        "layers": b.layers,
        "windows": {k: res.get(k) for k in ("edition", "build", "ubr", "branch", "family")},
        "iso": {"path": image["iso"], "sha256": res["iso"]["sha256"], "size": res["iso"]["size"]},
        "install_wim_sha256": res.get("install_wim_sha256"),
        "drivers": res.get("drivers"), "features": res.get("features"), "capabilities": res.get("capabilities"),
        "installers_verified_on_worker": res.get("installers"),
        "gates": {"rendered": "reports/conform-rendered.json", "image": "reports/conform-image.json",
                  "negative_controls": negative or {}},
        "start_layout": {"family": key, "start2": (asset or {}).get("start2"),
                         "start2_sha256": A._sha256(os.path.join(_TREE, asset["start2"])) if asset else None,
                         "record": (asset or {}).get("visible_places")},
        "spec_guest_sha256": A._sha256(b.guest_spec_path),
    }
    RL.write_manifest_atomic(b.manifest_path, m)


def run_stage(b: Build, http: Any = None) -> str:
    b.step("render", lambda: stage_render(b))
    b.step("resolve", lambda: stage_resolve(b, http))
    b.step("fetch", lambda: stage_fetch(b, http=http))
    detected = b.step("family-detect", lambda: image_family(b))
    if CAP.check_assets(b.spec(), detected, _TREE):
        b.step("capture", lambda: stage_capture(b, detected))
    fam = b.step("family", lambda: stage_family(b, detected))
    b.step("payload", lambda: stage_payload(b))
    b.step("gate-rendered", lambda: stage_gate_rendered(b))
    return b.step("job", lambda: stage_job(b, fam))


def run_service(b: Build, job: str) -> None:
    b.step("worker", lambda: stage_worker(b, job))


def run_gate(b: Build) -> None:
    image = b.step("gate-image", lambda: stage_gate_image(b))
    negative = b.step("gate-negative", lambda: stage_gate_negative(b))
    b.step("manifest", lambda: stage_manifest(b, image, negative))
    shutil.rmtree(b.work, ignore_errors=True)   # downloads + inputs.json (paths) are build scratch
    b.log(f"BUILD OK {image['iso']} (watchdog inactive: {W.watchdog_inactive()})")


# ---------------------------------------------------------------------------
# CLI (+ heavy-lock / memory-scope wrapper)
# ---------------------------------------------------------------------------

def _scoped_argv(argv: List[str], x: Dict[str, Any]) -> List[str]:
    py = x["probe"]["python"] if os.path.isfile(x["probe"]["python"]) else sys.executable
    scope = ["systemd-run", "--scope", "-q", "-p", f"MemoryMax={x['build']['host_memory_max']}", "-p", "MemorySwapMax=0",
             f"--setenv={SCOPED_ENV}=1"]
    if os.geteuid() != 0:
        scope.insert(1, "--user")
    return ["flock", "-w", "600", x["build"]["heavy_lock"], *scope, py, "-I", os.path.abspath(__file__), *argv]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="build.py", description="MiOS-Xbox image build (one entry, every stage fail-closed)")
    ap.add_argument("command", choices=["plan", "capture", "stage", "service", "gate", "run"])
    ap.add_argument("--build-dir", help="an existing build-<ts>/ (service, gate)")
    ap.add_argument("--family", help="capture: the build family to require of the worker (default: the worker's own)")
    a = ap.parse_args(argv)
    try:
        layers = build_layers()
        if a.command == "plan":
            p = plan(layers)
            print(json.dumps(p, indent=2, sort_keys=True))
            return 2 if p["problems"] else 0
        if os.geteuid() != 0:
            raise BuildError("stage/service/gate/run write root-only build artefacts and drive libvirt: run with sudo")
        merged = mios_toml.load_merged(layers)
        x = render_spec.render(merged, layers)["spec"]
        py = x["probe"]["python"] if os.path.isfile(x["probe"]["python"]) else sys.executable
        if os.path.realpath(sys.executable) != os.path.realpath(py):
            os.execv(py, [py, "-I", os.path.abspath(__file__), *argv])
        if os.environ.get(SCOPED_ENV) != "1":
            os.execvp("flock", _scoped_argv(argv, x))
        if a.command in ("service", "gate") and not a.build_dir:
            raise BuildError(f"{a.command} needs --build-dir build-<ts>")
        b = Build(a.build_dir, layers) if a.build_dir else Build.new(x["build"]["artifact_root"], layers)
        if a.command == "capture":
            b.step("render", lambda: stage_render(b))
            b.step("capture", lambda: stage_capture(b, a.family or None))
            shutil.rmtree(b.work, ignore_errors=True)
            return 0
        if a.command in ("stage", "run"):
            job = run_stage(b)
        else:
            job = os.path.join(b.work, "job")
        if a.command in ("service", "run"):
            run_service(b, job)
        if a.command in ("gate", "run"):
            run_gate(b)
        return 0
    except BuildError as e:
        print(f"build: {e}", file=sys.stderr)
        return 1 if "NON-CONFORMING" in str(e) else 2
    except (render_spec.SpecError, RL.ResolveError) as e:
        print(f"build: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
