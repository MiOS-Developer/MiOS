#!/usr/bin/env python3
# AI-hint: MiOS-Xbox in-guest conformance probe, host side (xb-18, XB-6/XB-3): derives the item list from the same xbox-spec.json as the offline gate, runs usr/share/mios/windows/xbox/mios-xbox-probe.ps1 in the guest over WinRM (read-only; credentials = the SSOT-resolved desktop account from the spec, never printed), optionally checks the host side (qemu-ga guest-ping, listening console ports, Looking Glass shm size), and evaluates the guest's JSON into named findings; `evaluate` replays a recorded probe output offline.
# AI-related: usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py, usr/share/mios/windows/xbox/mios-xbox-probe.ps1, tests/test-mios-xbox-probe.py, usr/libexec/mios/vfio/setup-looking-glass.py
# AI-functions: build_request, evaluate, host_checks, probe_settings, default_spec, fixture_spec, ssot_host_defaults, run_guest, main
"""MiOS-Xbox in-guest conformance probe.

  probe.py request  [--spec xbox-spec.json | --fixture]    # the request the guest receives (no secrets)
  probe.py evaluate [--spec xbox-spec.json | --fixture] --recorded OUT.json [--host HOST.json] [--report R.json]
  probe.py run      [--spec xbox-spec.json] [--endpoint URL | --address IP | --domain NAME] [--host-from-ssot]
                    [--auth-from-answer-file XML] [--shm PATH --shm-size BYTES --port N ...] [--save OUT.json] [--report R.json]

Without --spec the layered SSOT is rendered in memory (the REAL spec; nothing is written); the recorded test
fixture is used only with --fixture. WinRM signs in as the SSOT desktop admin. Exit 0 = conforming,
1 = findings, 2 = unreachable (fail closed) / usage / spec error."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_items as S  # noqa: E402
from spec_items import Finding  # noqa: E402

REQUEST_SCHEMA = "mios-xbox-probe-request/1"
PROBE_SCHEMA = "mios-xbox-probe/1"
REPORT_SCHEMA = "mios-xbox-probe-report/1"
_HERE = os.path.dirname(os.path.abspath(__file__))
GUEST_SCRIPT = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", "share", "mios", "windows", "xbox", "mios-xbox-probe.ps1"))
_REQ_FIELDS = ("id", "kind", "scope", "path", "name", "type", "inf", "service", "installer", "task",
               "record", "groups", "state")


def desktop_user(items: List[Dict[str, Any]]) -> str:
    for it in items:
        if it["kind"] == "user" and it.get("role") == "desktop":
            return it["name"]
    for it in items:
        if it["kind"] == "user" and it.get("autologon"):
            return it["name"]
    raise S.SpecError("spec has no desktop user")


def build_request(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The guest request: locators only -- never values, hashes or credentials."""
    out = []
    for it in items:
        out.append({k: it[k] for k in _REQ_FIELDS if k in it})
    return {"schema": REQUEST_SCHEMA, "desktop_user": desktop_user(items), "items": out}


# ---------------------------------------------------------------------------
# evaluation of a probe output (live or recorded)
# ---------------------------------------------------------------------------

def _gpu_findings(it: Dict[str, Any], o: Dict[str, Any], probe: Dict[str, Any]) -> List[Finding]:
    out: List[Finding] = []
    skip = {v.upper() for v in it.get("skip_vendor_ids") or []}
    supported = {v.upper() for v in it.get("vendor_ids") or []}
    gpus = []
    for d in probe.get("display") or []:
        m = re.search(r"PCI\\VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})", d.get("device_id", ""))
        if m and m.group(1).upper() not in skip and (not supported or m.group(1).upper() in supported):
            gpus.append((m.group(1).upper(), m.group(2).upper(), d))
    if not o.get("present"):
        return [Finding(f"missing:{it['id']}", "the GPU post-setup step left no record")]
    rec = o.get("record") or {}
    status = str(rec.get("status", ""))
    if status not in ("ok", "no-gpu"):
        out.append(Finding("gpu.step-failed", f"GPU step status {status or '<none>'}"))
    devices = rec.get("devices") or []
    if not gpus:
        if devices and any(str(e.get("status")) in ("installed", "current") for e in devices):
            out.append(Finding("gpu.unexpected-install", "a vendor driver was installed but no supported GPU is present"))
        if status == "ok" and devices:
            out.append(Finding("gpu.unexpected-install", "record lists devices although none is present"))
        return out
    if status == "no-gpu":
        out.append(Finding("gpu.not-installed", "a supported GPU is present but the step recorded no-gpu"))
    for ven, dev, d in gpus:
        ent = next((e for e in devices if str(e.get("vendor_id", "")).upper() == ven and str(e.get("device_id", "")).upper() == dev), None)
        if ent is None:
            out.append(Finding("gpu.not-installed", f"{ven}:{dev} has no entry in the GPU step record"))
            continue
        if str(ent.get("status")) not in ("installed", "current"):
            out.append(Finding("gpu.not-installed", f"{ven}:{dev} step status {ent.get('status')}"))
        if not ent.get("version"):
            out.append(Finding("gpu.version-unrecorded", f"{ven}:{dev} has no recorded upstream version"))
        if ent.get("installed_driver_version") and str(ent["installed_driver_version"]) != str(d.get("version")):
            out.append(Finding("gpu.driver-mismatch", f"{ven}:{dev} runs {d.get('version')}, record says {ent['installed_driver_version']}"))
    return out


def evaluate(items: List[Dict[str, Any]], probe: Dict[str, Any], host: Optional[Dict[str, Any]] = None
             ) -> Tuple[List[Dict[str, Any]], List[Finding]]:
    if probe.get("schema") != PROBE_SCHEMA:
        raise S.SpecError(f"not a {PROBE_SCHEMA} document")
    obs = probe.get("observations") or {}
    results: List[Dict[str, Any]] = []
    findings: List[Finding] = list(S.spec_deny_findings(items))
    for e in probe.get("errors") or []:
        findings.append(Finding("probe.error", str(e)[:200]))

    def res(it, status, detail=""):
        results.append({"id": it["id"], "kind": it["kind"], "status": status, "detail": detail})

    for it in items:
        k, iid = it["kind"], it["id"]
        o = obs.get(iid)
        before = len(findings)
        if o is None:
            findings.append(Finding(f"probe.no-observation:{iid}", "the guest returned nothing for this item"))
        elif o.get("error"):
            findings.append(Finding(f"probe.error:{iid}", str(o["error"])[:200]))
        elif k == "reg":
            if it.get("absent"):
                if o.get("present"):
                    findings.append(Finding(f"off-spec:{iid}", f"{it['name']} must be absent"))
            elif not o.get("present"):
                findings.append(Finding(f"missing:{iid}", f"{it['name']} under {it['path']} is not set in the guest"))
            elif not S.value_equal(it, o.get("type", it["type"]), o.get("value")):
                want = it["value"] if not it.get("kv_field") else f"{it['kv_field']}={it['value']}"
                findings.append(Finding(f"off-spec:{iid}", f"{it['name']}={o.get('value')!r}, spec {want!r}"))
            if o.get("present"):
                for fid in S.reg_deny_hits(it["scope"], it["name"], o.get("type", it["type"]), o.get("value")):
                    findings.append(Finding(fid, f"legacy value {it['name']}={o.get('value')!r} live in the guest"))
        elif k == "feature":
            st = str(o.get("state", "Absent"))
            want = it["state"]
            if want == "conditional":
                if probe.get("nested_virt") and st != "Enabled":
                    findings.append(Finding(f"missing:{iid}", f"{it['name']} {st} although the VM exposes virtualization"))
            elif want == "enabled" and st != "Enabled":
                findings.append(Finding(f"missing:{iid}", f"optional feature {it['name']} is {st}"))
            elif want == "disabled" and st == "Enabled":
                findings.append(Finding(f"off-spec:{iid}", f"optional feature {it['name']} is Enabled"))
        elif k == "capability":
            if str(o.get("state")) != "Installed":
                findings.append(Finding(f"missing:{iid}", f"capability {it['name']} is {o.get('state')}"))
        elif k == "driver":
            if not o.get("present"):
                findings.append(Finding(f"missing:{iid}", f"driver {it['inf']}.inf is not installed"))
        elif k == "guest_tool":
            svc = o.get("service")
            if not it.get("service"):
                res(it, "skipped", "installer-only guest tool; covered by its services"); continue
            if not svc or not svc.get("present"):
                findings.append(Finding(f"missing:{iid}", f"service {it['service']} is not installed"))
            elif str(svc.get("status")) != "Running":
                findings.append(Finding(f"guest_tools.not-running:{iid}", f"service {it['service']} is {svc.get('status')}"))
        elif k in ("component", "file_hash"):
            if not o.get("present"):
                findings.append(Finding(f"missing:{iid}", f"{it['path']} is not in the guest"))
            elif it.get("sha256") and str(o.get("sha256", "")).lower() != str(it["sha256"]).lower():
                findings.append(Finding(f"off-spec:{iid}", f"{it['path']} sha256 differs from the spec source"))
            if it.get("task") and not (o.get("task") or {}).get("present"):
                findings.append(Finding(f"missing:{iid}.task", f"scheduled task {it['task']} is not registered"))
            if it.get("service") and not (o.get("service") or {}).get("present"):
                findings.append(Finding(f"missing:{iid}.service", f"service {it['service']} is not installed"))
        elif k == "build_family":
            fams = it.get("families") or {}
            br = str(probe.get("branch") or "")
            fam = next((f for f in fams if f.lower() == br.lower()), None)
            if fam is None:
                findings.append(Finding("start.family-mismatch", f"guest BuildBranch {br or '<none>'} has no Start layout assets"))
            else:
                want = (fams.get(fam) or {}).get("start2_sha256")
                if want and str(o.get("sha256", "")) != want:
                    findings.append(Finding("off-spec:start.start2_bin", f"desktop user's start2.bin is not the {fam} asset"))
                elif not want:
                    findings.append(Finding(f"missing:start.layout_assets.{fam}", "start2.bin of this family is not captured (xb-11)"))
        elif k == "user":
            if not o.get("present"):
                findings.append(Finding(f"users.missing:{iid}", f"account {it['name']} does not exist"))
            elif it.get("role") == "desktop":
                if not o.get("enabled"):
                    findings.append(Finding(f"users.disabled:{iid}", "desktop account is disabled"))
                missing_groups = [g for g in it.get("groups") or [] if g not in (o.get("groups") or [])]
                if missing_groups:
                    findings.append(Finding(f"users.groups:{iid}", f"not a member of {', '.join(missing_groups)}"))
                al = probe.get("autologon") or {}
                if it.get("autologon"):
                    dn = str(al.get("default_user", "")).split("\\")[-1]
                    if str(al.get("auto_admin_logon")) != "1" or dn.lower() != it["name"].lower():
                        findings.append(Finding("users.autologon", "Winlogon does not sign the desktop account in automatically"))
                    if it.get("persistent") and al.get("logon_count") not in (None, ""):
                        findings.append(Finding("users.autologon-persistent", "AutoLogonCount is set (autologon expires)"))
                    if al.get("password_sha256") and al["password_sha256"] != it["password_sha256"]:
                        findings.append(Finding(f"users.password:{iid}", "Winlogon DefaultPassword does not equal the SSOT value"))
                if probe.get("auth_user") and str(probe["auth_user"]).lower() != it["name"].lower() and not al.get("password_sha256"):
                    res(it, "pass" if len(findings) == before else "fail",
                        "password not proven (probe authenticated as another account)")
                    continue
        elif k == "rid500":
            if o.get("present") and o.get("enabled"):
                findings.append(Finding("users.rid500-enabled", "the built-in Administrator (RID 500) is still enabled"))
        elif k == "computer_name":
            want = str(it["value"]).upper()[:15]
            if str(o.get("value", "")).upper() != want:
                findings.append(Finding(f"off-spec:{iid}", f"computer name {o.get('value')!r}, spec {want!r}"))
        elif k == "appx":
            if bool(o.get("present")) != bool(it.get("present", True)):
                findings.append(Finding(f"missing:{iid}", f"Appx package {it['name']} is not provisioned"))
        elif k == "gpu_step":
            findings.extend(_gpu_findings(it, o, probe))
        new = findings[before:]
        res(it, "fail" if new else "pass", "; ".join(f.id for f in new))

    for name, chk in sorted((host or {}).items()):
        if not chk.get("ok"):
            findings.append(Finding(f"host.{name}", str(chk.get("detail", "failed"))[:200]))
    return results, findings


# ---------------------------------------------------------------------------
# host-side checks (read-only)
# ---------------------------------------------------------------------------

Runner = Callable[[List[str]], Tuple[int, str]]


def _default_runner(cmd: List[str]) -> Tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def host_checks(domain: Optional[str], ports: List[int], shm: Optional[str], shm_size: Optional[int],
                runner: Runner = _default_runner, port_open: Callable[[str, int], bool] = _port_open,
                connect: str = "qemu:///system") -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if domain:
        rc, txt = runner(["virsh", "-c", connect, "qemu-agent-command", domain, '{"execute":"guest-ping"}'])
        out["guest-ping"] = {"ok": rc == 0 and '"return"' in txt, "detail": txt.strip()[:200] or f"rc={rc}"}
    for p in ports:
        out[f"port-{p}"] = {"ok": port_open("127.0.0.1", p), "detail": f"nothing listens on 127.0.0.1:{p}"}
    if shm:
        try:
            size = os.stat(shm).st_size
            out["shm"] = {"ok": shm_size is None or size == shm_size, "detail": f"{shm} is {size} bytes, want {shm_size}"}
        except OSError as e:
            out["shm"] = {"ok": False, "detail": f"{shm}: {e.strerror}"}
    return out


# ---------------------------------------------------------------------------
# WinRM transport
# ---------------------------------------------------------------------------

def _credentials(doc: Dict[str, Any], answer_file: Optional[str] = None) -> Tuple[str, str]:
    """WinRM credentials from the rendered spec (render_spec.py): the separate elevation admin (users.admin) when the
    spec has one -- the desktop account is a STANDARD user (F2) and WinRM refuses it (round-1 boot:
    InvalidCredentialsError as the desktop user) -- else the desktop account of a single-account spec. `answer_file`
    is an explicit opt-in for a guest that was installed from another answer file (the legacy negative control): its
    first Administrators LocalAccount, read like the build worker reads its base. Never printed."""
    if answer_file:
        cfg = type("C", (), {"credential_source": "base-answer-file", "base_answer_file": answer_file})()
        import worker as W  # noqa: PLC0415
        try:
            return W.worker_credentials(cfg)  # type: ignore[arg-type]
        except W.WorkerError as e:
            raise S.SpecError(str(e)) from None
    if doc.get("projection") == "guest":
        raise S.SpecError("the guest projection carries no credentials: probe with the full rendered spec")
    users = doc.get("users") or {}
    for role in ("admin", "desktop"):
        d = users.get(role) or {}
        if d.get("name") and d.get("password"):
            return d["name"], d["password"]
    raise S.SpecError("users.admin / users.desktop: no SSOT credentials in the rendered spec (render it with render_spec.py)")


def guest_commands(script: str, request: Dict[str, Any], chunk: int = 2000) -> Tuple[List[str], str]:
    """PowerShell commands that stage the probe in the WinRM user's TEMP (WinRM runs each command
    through an 8191-char line), run it, and remove it.  Returns (staging commands, run command)."""
    blob = base64.b64encode(script.encode("utf-8")).decode("ascii")
    req = base64.b64encode(json.dumps(request).encode("utf-8")).decode("ascii")
    tag = secrets.token_hex(6)
    stage = f"(Join-Path $env:TEMP 'mios-xbox-probe-{tag}.b64')"
    cmds = [f"$ErrorActionPreference='Stop'; Set-Content -NoNewline -Encoding ascii -Path {stage} -Value ''"]
    for i in range(0, len(blob), chunk):
        cmds.append(f"Add-Content -NoNewline -Encoding ascii -Path {stage} -Value '{blob[i:i + chunk]}'")
    for i in range(0, len(req), chunk):
        cmds.append(f"Add-Content -NoNewline -Encoding ascii -Path ({stage} + '.req') -Value '{req[i:i + chunk]}'")
    run = (f"$f = {stage}; try {{ $s = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([IO.File]::ReadAllText($f))); "
           f"& ([scriptblock]::Create($s)) -RequestB64 ([IO.File]::ReadAllText($f + '.req')) }} "
           f"finally {{ Remove-Item -LiteralPath $f, ($f + '.req') -Force -ErrorAction SilentlyContinue }}")
    return cmds, run


PROBE_KEYS = ("winrm_port", "transport", "operation_timeout_s", "read_timeout_s", "connect_timeout_s")


def probe_settings(doc: Dict[str, Any]) -> Dict[str, Any]:
    """[windows.xbox.probe] from the rendered spec; every key is required (no built-in fallback)."""
    pr = ((doc.get("spec") or {}).get("probe") or {}) if isinstance(doc, dict) else {}
    out = {}
    for k in PROBE_KEYS:
        v = pr.get(k)
        if v in (None, "", 0):
            raise S.SpecError(f"windows.xbox.probe.{k} missing")
        out[k] = v
    if int(out["read_timeout_s"]) <= int(out["operation_timeout_s"]):
        raise S.SpecError("windows.xbox.probe.read_timeout_s must exceed operation_timeout_s")
    out["python"] = pr.get("python", "")
    return out


def default_spec(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The REAL spec: the layered SSOT rendered in memory by render_spec.py (nothing is written)."""
    import render_spec  # noqa: PLC0415
    import build as B  # noqa: PLC0415
    try:
        return render_spec.render_layers(B.build_layers(env))
    except render_spec.SpecError as e:
        raise S.SpecError(f"rendering the SSOT failed: {e}") from None


def fixture_spec(root: str) -> Tuple[Dict[str, Any], str]:
    """--fixture: the recorded test fixture spec and a fixture tree (tests only)."""
    if root not in sys.path:
        sys.path.insert(0, root)
    import tests._mios_xbox_fixtures as FX  # noqa: PLC0415
    doc = FX.load_fixture_spec()
    return doc, FX.fixture_root(tempfile.mkdtemp(prefix="mios-xbox-probe-fixture."), doc)


def ssot_host_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Host-check targets from the layered SSOT: [vm.looking_glass] (SPICE port, shm, connect URI)
    and the domain name from [windows.xbox.vm].name."""
    for lib in (os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", "lib", "mios")), "/usr/lib/mios"):
        if os.path.isfile(os.path.join(lib, "mios_toml.py")):
            if lib not in sys.path:
                sys.path.insert(0, lib)
            break
    import mios_toml  # type: ignore
    lg = mios_toml.section(mios_toml.load_merged(), "vm.looking_glass")
    vm = ((doc.get("spec") or {}).get("vm") or {})
    out = {"domain": vm.get("name"), "connect": lg.get("connect", "qemu:///system"), "ports": [], "shm": lg.get("shm_path"),
           "shm_size": int(lg["shm_size_mb"]) * 1024 * 1024 if lg.get("shm_size_mb") else None}
    if lg.get("spice_port"):
        out["ports"].append(int(lg["spice_port"]))
    return out


def run_guest(endpoint: str, user: str, password: str, request: Dict[str, Any], script_path: str = GUEST_SCRIPT,
              session_factory: Optional[Callable[..., Any]] = None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with open(script_path, encoding="utf-8") as fh:
        script = fh.read()
    if session_factory is None:
        st = settings or {}
        for k in ("transport", "operation_timeout_s", "read_timeout_s"):
            if not st.get(k):
                raise S.SpecError(f"windows.xbox.probe.{k} missing")
        st = {k: (int(v) if k.endswith("_s") else v) for k, v in st.items()}
        import winrm  # type: ignore
        session_factory = lambda: winrm.Session(endpoint, auth=(user, password), transport=st["transport"],  # noqa: E731
                                                operation_timeout_sec=st["operation_timeout_s"],
                                                read_timeout_sec=st["read_timeout_s"])
    sess = session_factory()
    cmds, run = guest_commands(script, request)
    for c in cmds:
        r = sess.run_ps(c)
        if r.status_code:
            raise RuntimeError(f"staging failed rc={r.status_code}: {r.std_err.decode(errors='replace')[:300]}")
    r = sess.run_ps(run)
    if r.status_code:
        raise RuntimeError(f"probe failed rc={r.status_code}: {r.std_err.decode(errors='replace')[:300]}")
    text = r.std_out.decode("utf-8", "replace").strip()
    line = next((ln for ln in reversed(text.splitlines()) if ln.startswith("{")), "")
    return json.loads(line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write_json(path: str, data: Any) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".probe.", dir=d)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dfd = os.open(d, os.O_RDONLY)
    os.fsync(dfd)
    os.close(dfd)


def _finish(results, findings, report: Optional[str]) -> int:
    if report:
        _write_json(report, {"schema": REPORT_SCHEMA, "ok": not findings, "items": results,
                             "findings": [f.as_dict() for f in findings]})
    passed = sum(1 for r in results if r["status"] == "pass")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    for f in findings:
        print(f"FINDING {f}")
    print(f"probe: {passed} pass, {len(results) - passed - skipped} fail, {skipped} skipped, {len(findings)} findings -> "
          f"{'CONFORMING' if not findings else 'NON-CONFORMING'}")
    return 0 if not findings else 1


def _reexec_with_winrm(doc: Dict[str, Any], argv: List[str]) -> None:
    """pywinrm lives in [windows.xbox.probe].python: re-execute there once."""
    py = str(((doc.get("spec") or {}).get("probe") or {}).get("python") or "")
    # compare the interpreter PATHS, not their realpaths: a venv's bin/python is a symlink to the system python, so
    # realpath() made the venv look like "already running there" and the probe never reached pywinrm (round-1 boot)
    if py and os.path.isfile(py) and os.path.abspath(py) != os.path.abspath(sys.executable) \
            and os.environ.get("MIOS_XBOX_PROBE_REEXEC") != "1":
        os.environ["MIOS_XBOX_PROBE_REEXEC"] = "1"
        os.execv(py, [py, "-I", os.path.abspath(__file__), *argv])


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _unreachable(items: List[Dict[str, Any]], why: str, host: Optional[Dict[str, Any]], report: Optional[str]) -> int:
    """Fail closed: nothing was observed, so nothing passes; exit 2 (never CONFORMING)."""
    results = [{"id": it["id"], "kind": it["kind"], "status": "fail", "detail": "guest unreachable"} for it in items]
    findings = list(S.spec_deny_findings(items)) + [Finding("probe.unreachable", why[:300])]
    for name, chk in sorted((host or {}).items()):
        if not chk.get("ok"):
            findings.append(Finding(f"host.{name}", str(chk.get("detail", "failed"))[:200]))
    if report:
        _write_json(report, {"schema": REPORT_SCHEMA, "ok": False, "reachable": False, "items": results,
                             "findings": [f.as_dict() for f in findings]})
    for f in findings:
        print(f"FINDING {f}")
    print(f"probe: guest UNREACHABLE -> NON-CONFORMING (fail closed): {why[:200]}")
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not any(arg in ("request", "evaluate", "run", "-h", "--help") for arg in raw_args):
        raw_args.insert(0, "evaluate" if any(arg.startswith("--recorded") for arg in raw_args) else "run")

    ap = argparse.ArgumentParser(prog="probe.py", description="MiOS-Xbox in-guest conformance probe")
    ap.add_argument("--root", default=os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", "..")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("request", "evaluate", "run"):
        sp = sub.add_parser(name)
        src = sp.add_mutually_exclusive_group()
        src.add_argument("--spec", help="a rendered xbox-spec.json (default: render the layered SSOT in memory)")
        src.add_argument("--fixture", action="store_true", help="the recorded TEST fixture spec (offline tests only)")
        if name == "evaluate":
            sp.add_argument("--recorded", required=True)
            sp.add_argument("--host")
            sp.add_argument("--report")
        if name == "run":
            sp.add_argument("--endpoint", help="WinRM URL (default: http://<address>:<[windows.xbox.probe].winrm_port>/wsman)")
            sp.add_argument("--address", help="guest IPv4 address")
            sp.add_argument("--domain", help="libvirt domain (default: [windows.xbox.vm].name)")
            sp.add_argument("--auth-from-answer-file", metavar="XML",
                            help="explicit opt-in: authenticate with the first Administrators account of this answer file "
                                 "(a guest installed from another answer file, e.g. the legacy negative control)")
            sp.add_argument("--host-from-ssot", action="store_true", help="host checks from [vm.looking_glass] + [windows.xbox.vm].name")
            sp.add_argument("--port", type=int, action="append", default=[])
            sp.add_argument("--shm")
            sp.add_argument("--shm-size", type=int)
            sp.add_argument("--save")
            sp.add_argument("--report")
    a = ap.parse_args(raw_args)
    try:
        if a.fixture:
            doc, root_dir = fixture_spec(a.root)
        elif a.spec:
            doc, root_dir = S.load_spec(a.spec), a.root
        else:
            doc, root_dir = default_spec(), a.root
        items = S.items_from_spec(doc, root_dir)
        if a.cmd == "request":
            print(json.dumps(build_request(items), indent=2))
            return 0
        if a.cmd == "evaluate":
            with open(a.recorded, encoding="utf-8") as fh:
                probe = json.load(fh)
            host = None
            if a.host:
                with open(a.host, encoding="utf-8") as fh:
                    host = json.load(fh)
            results, findings = evaluate(items, probe, host)
            return _finish(results, findings, a.report)

        settings = probe_settings(doc)
        domain = a.domain or (None if (a.endpoint or a.address) else ((doc.get("spec") or {}).get("vm") or {}).get("name"))
        host = None
        if a.host_from_ssot:
            hd = ssot_host_defaults(doc)
            host = host_checks(domain or hd["domain"], a.port or hd["ports"], a.shm or hd["shm"],
                               a.shm_size or hd["shm_size"], connect=hd["connect"])
        elif a.port or a.shm:
            host = host_checks(domain, a.port, a.shm, a.shm_size)
        endpoint = a.endpoint
        if not endpoint:
            addr = a.address
            if not addr and domain:
                try:
                    out = subprocess.run(["virsh", "-c", "qemu:///system", "domifaddr", domain, "--source", "lease"],
                                         capture_output=True, text=True, timeout=10).stdout
                    m = re.search(r"ipv4\s+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", out)
                    addr = m.group(1) if m else None
                except (OSError, subprocess.TimeoutExpired):
                    addr = None
            if not addr:
                return _unreachable(items, f"no IPv4 address for domain {domain}", host, a.report)
            endpoint = f"http://{addr}:{int(settings['winrm_port'])}/wsman"
        m = re.match(r"^https?://([^/:]+)(?::(\d+))?/", endpoint)
        if not m:
            raise S.SpecError(f"bad WinRM endpoint {endpoint!r}")
        port = int(m.group(2) or (443 if endpoint.startswith("https") else 80))
        if not _tcp_open(m.group(1), port, float(settings["connect_timeout_s"])):
            return _unreachable(items, f"nothing answers on {m.group(1)}:{port} (WinRM)", host, a.report)
        try:
            import winrm  # type: ignore  # noqa: F401
        except ImportError:
            _reexec_with_winrm(doc, raw_args)
            print("probe: pywinrm is not available ([windows.xbox.probe].python)", file=sys.stderr)
            return 2
        user, password = _credentials(doc, a.auth_from_answer_file)
        try:
            probe = run_guest(endpoint, user, password, build_request(items), settings=settings)
        except Exception as ex:  # noqa: BLE001 -- transport/auth failure: fail closed, never print the secret
            msg = str(ex).replace(password, "<redacted>") if password else str(ex)
            return _unreachable(items, f"WinRM {endpoint} as {user}: {type(ex).__name__}: {msg[:200]}", host, a.report)
        finally:
            password = ""
        if a.save:
            _write_json(a.save, probe)
        results, findings = evaluate(items, probe, host)
        return _finish(results, findings, a.report)
    except S.SpecError as ex:
        print(f"probe: spec error: {ex}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as ex:
        print(f"probe: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
