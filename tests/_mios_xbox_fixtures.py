# AI-hint: Shared fixture builders for the MiOS-Xbox gate tests: load the fixture xbox-spec.json (or render the live SSOT + staged fragments through mios_toml.load_merged + render_spec when present), and from its items build a conforming rendered-input bundle (answer file with base64 UTF-16 embedded scripts, SetupComplete, .reg, drivers, payload), a real offline image (minimal regf hives filled by hivexregedit --merge, captured with wimlib-imagex), and recorded in-guest probe outputs. Fixture credentials only.
# AI-related: tests/test-mios-xbox-conform.py, tests/test-mios-xbox-probe.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py
"""Fixture builders for the MiOS-Xbox conformance tests (no network, no VM, no real secret)."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XBOX_DIR = os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox")
FIXTURES = os.path.join(HERE, "fixtures", "mios-xbox")
FIXTURE_SPEC = os.path.join(FIXTURES, "xbox-spec.fixture.json")
STAGED_DIR = os.environ.get("MIOS_XBOX_STAGED_DIR", "/home/mios/mios-desktop-work-1/staged-ssot")
FIXTURE_IDENTITY = os.path.join(FIXTURES, "fixture-identity.toml")

sys.path.insert(0, XBOX_DIR)
import spec_items as S  # noqa: E402


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def live_render() -> Optional[Dict[str, Any]]:
    """Render live mios.toml + staged xbox-*.toml fragments + fixture identity through
    load_merged and render_spec.py.  None when render_spec.py or the spec fragment is absent."""
    rs = os.path.join(XBOX_DIR, "render_spec.py")
    frag = os.path.join(STAGED_DIR, "xbox-spec.toml")
    vendor = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
    if not (os.path.isfile(rs) and os.path.isfile(vendor)):
        return None
    # The fragments these gates consume (spec tree, GPU step, gate/probe settings); other lanes'
    # fragments are rendered by their own tests.
    sys.path.insert(0, os.path.join(ROOT, "usr", "lib", "mios"))
    import mios_toml
    vendor_has_xbox = bool(mios_toml.section(mios_toml.load_merged(layers=[vendor]), "windows.xbox"))
    extra_frags = [] if vendor_has_xbox else [p for p in (frag, os.path.join(STAGED_DIR, "xbox-gpu.toml"),
                                                           os.path.join(STAGED_DIR, "xbox-gates.toml")) if os.path.isfile(p)]
    layers = [vendor] + extra_frags + [FIXTURE_IDENTITY]
    mod = load_module("render_spec_live", rs)
    merged = mios_toml.load_merged(layers=layers)
    if not mios_toml.section(merged, "windows.xbox"):
        return None
    return mod.render(merged, layers)


def load_fixture_spec() -> Dict[str, Any]:
    with open(FIXTURE_SPEC, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str, data: Any) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


# ---------------------------------------------------------------------------
# fixture MiOS tree (component sources, gpu step script, Start assets)
# ---------------------------------------------------------------------------

def fixture_root(base: str, doc: Dict[str, Any]) -> str:
    """A tree with every component source and Start asset the spec references (deterministic
    fixture bytes), so the expected hashes are known and stable."""
    root = os.path.join(base, "root")
    x = doc["spec"]
    rels = [c["source"] for c in x["components"]["ship"] if c.get("kind") != "native"]
    if x.get("drivers", {}).get("gpu_step"):
        rels.append(x["drivers"]["gpu_step"])
    for rel in rels:
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:   # deterministic bytes: recorded outputs carry these hashes
            fh.write(f"fixture source {rel}\n".encode())
    for fam, a in (x.get("start", {}).get("layout_assets") or {}).items():
        dst = os.path.join(root, a["start2"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(hashlib.sha256(fam.encode()).digest() * 8)
    return root


# ---------------------------------------------------------------------------
# rendered bundle
# ---------------------------------------------------------------------------

def _uapw(pw: str, suffix: str) -> str:
    return base64.b64encode((pw + suffix).encode("utf-16-le")).decode()


def _cmd_value(it: Dict[str, Any]) -> str:
    v = it["value"]
    if it.get("kv_field"):
        v = f"{it['kv_field']}={v};"
    if S.canon_type(it["type"]) in ("dword", "qword"):
        return str(int(v))
    return '"' + str(v).replace('"', '\\"') + '"'


def reg_add_line(prefix: str, it: Dict[str, Any]) -> str:
    t = {"dword": "REG_DWORD", "qword": "REG_QWORD", "sz": "REG_SZ", "expand_sz": "REG_EXPAND_SZ",
         "binary": "REG_BINARY"}[S.canon_type(it["type"])]
    return f'reg add "{prefix}\\{it["path"]}" /v "{it["name"]}" /t {t} /d {_cmd_value(it)} /f'


def answer_file(doc: Dict[str, Any], specialize: str, desktop_pw: Optional[str] = None,
                admin_pw: Optional[str] = None, autologon_user: Optional[str] = None) -> str:
    u = doc["users"]
    d = u["desktop"]
    dpw = d["password"] if desktop_pw is None else desktop_pw
    apw = u["builtin_administrator"]["password"] if admin_pw is None else admin_pw
    alu = d["name"] if autologon_user is None else autologon_user
    blob = base64.b64encode(specialize.encode("utf-16-le")).decode()
    d_group = (d.get("groups") or ["Administrators"])[0]
    admin_tag = ""
    if u.get("admin"):
        adm = u["admin"]
        admpw = adm["password"]
        adm_group = (adm.get("groups") or ["Administrators"])[0]
        admin_tag = f'\n          <LocalAccount wcm:action="add"><Name>{adm["name"]}</Name><Group>{adm_group}</Group><Password><Value>{_uapw(admpw, "Password")}</Value><PlainText>false</PlainText></Password></LocalAccount>'
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <ComputerName>{u["computer_name"]}</ComputerName>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <UserAccounts>
        <AdministratorPassword><Value>{_uapw(apw, "AdministratorPassword")}</Value><PlainText>false</PlainText></AdministratorPassword>
        <LocalAccounts>
          <LocalAccount wcm:action="add"><Name>{d["name"]}</Name><Group>{d_group}</Group><Password><Value>{_uapw(dpw, "Password")}</Value><PlainText>false</PlainText></Password></LocalAccount>{admin_tag}
        </LocalAccounts>
      </UserAccounts>
      <AutoLogon><Username>{alu}</Username><Enabled>true</Enabled><LogonCount>9999999</LogonCount><Password><Value>{_uapw(dpw, "Password")}</Value><PlainText>false</PlainText></Password></AutoLogon>
    </component>
  </settings>
  <Extensions xmlns="urn:mios:unattend"><SpecializeScript>{blob}</SpecializeScript></Extensions>
</unattend>
"""


def render_bundle(out: str, doc: Dict[str, Any], items: List[Dict[str, Any]], root: str,
                  branch: Optional[str] = None) -> str:
    """A conforming rendered-input bundle for these items."""
    os.makedirs(out, exist_ok=True)
    reg = [i for i in items if i["kind"] == "reg" and not i.get("absent")]
    user = [reg_add_line("HKU\\MiOSDefault", i) for i in reg if i["scope"] == "user"]
    spec_script = "\r\n".join(["& cmd.exe /d /c 'reg load HKU\\MiOSDefault C:\\Users\\Default\\NTUSER.DAT'"]
                              + [f"& cmd.exe /d /c '{ln}'" for ln in user]
                              + ["& cmd.exe /d /c 'reg unload HKU\\MiOSDefault'"])
    with open(os.path.join(out, "autounattend.xml"), "w", encoding="utf-8") as fh:
        fh.write(answer_file(doc, spec_script))
    machine = [i for i in reg if i["scope"] != "user" and not i.get("owner", "").startswith("debloat.")]
    deb = [i for i in reg if i["scope"] != "user" and i.get("owner", "").startswith("debloat.")]
    scripts = os.path.join(out, "payload", "Windows", "Setup", "Scripts")
    os.makedirs(scripts, exist_ok=True)
    lines = ["@echo off", "rem MiOS-Xbox SetupComplete (fixture)"]
    for i in machine:
        lines.append(reg_add_line("HKLM\\SOFTWARE" if i["scope"] == "software" else "HKLM\\SYSTEM", i))
    lines.append('reg import "%~dp0mios-debloat.reg"')
    for i in items:
        if i["kind"] == "feature" and i["state"] == "enabled":
            lines.append(f"dism /online /enable-feature /featurename:{i['name']} /all /norestart")
        elif i["kind"] == "feature" and i["state"] == "disabled":
            lines.append(f"dism /online /disable-feature /featurename:{i['name']} /norestart")
        elif i["kind"] == "capability":
            lines.append(f"dism /online /add-capability /capabilityname:{i['name']}~~~~0.0.1.0")
        elif i["kind"] == "guest_tool" and i.get("installer"):
            inst = i["installer"]
            if "*" in inst:
                lines.append(f'for %%f in ("%~dp0{inst}") do msiexec /i "%%f" /qn /norestart')
                staged = inst.replace("*", "fixture")
            else:
                lines.append(f'start "" /wait "%~dp0{inst}" /quiet /norestart')
                staged = inst
            with open(os.path.join(scripts, staged), "wb") as fh:
                fh.write(b"fixture installer\n")
        elif i["kind"] == "component" and i.get("task"):
            lines.append(f'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\\{i["path"]}" -Register')
            lines.append(f'schtasks /create /tn "{i["task"]}" /sc onstart /ru SYSTEM /rl highest /tr "powershell -File C:\\{i["path"]}" /f')
        elif i["kind"] == "rid500":
            lines.append("net user Administrator /active:no")
    with open(os.path.join(scripts, "SetupComplete.cmd"), "w", encoding="utf-8") as fh:
        fh.write("\r\n".join(lines) + "\r\n")
    with open(os.path.join(scripts, "mios-debloat.reg"), "w", encoding="utf-8") as fh:
        fh.write(reg_file(deb, absolute=True))
    for i in items:
        if i["kind"] == "driver":
            d = os.path.join(out, "drivers", i["inf"])
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, i["inf"] + ".inf"), "w", encoding="utf-8") as fh:
                fh.write(virtio_inf(i["inf"]))
        elif i["kind"] in ("component",):
            dst = os.path.join(out, "payload", i["path"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _write_component(dst, i, doc, root)
        elif i["kind"] == "build_family":
            fam = branch or sorted(i["families"])[0]
            dst = os.path.join(out, "payload", i["start2_target"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(root, i["families"][fam]["start2"]), dst)
            write_json(os.path.join(out, "build-manifest.json"), {"windows": {"build": "29683", "branch": fam}})
    return out


def _write_component(dst: str, it: Dict[str, Any], doc: Dict[str, Any], root: str) -> None:
    x = doc["spec"]
    if it["id"] == "gpu.script":
        src = os.path.join(root, x["drivers"]["gpu_step"])
    else:
        c = next(c for c in x["components"]["ship"] if f"components.{c['name']}" == it["id"])
        src = None if c.get("kind") == "native" else os.path.join(root, c["source"])
    if src:
        shutil.copyfile(src, dst)
    else:
        with open(dst, "wb") as fh:
            fh.write(b"MZ fixture native component\n")


def virtio_inf(base: str, cls: str = "SCSIAdapter", provider: str = "Red Hat, Inc.") -> str:
    return f"; {base}.inf (fixture)\n[Version]\nSignature=\"$WINDOWS NT$\"\nClass={cls}\nProvider=%VENDOR%\n\n[Strings]\nVENDOR=\"{provider}\"\n"


def _reg_value_line(it: Dict[str, Any]) -> str:
    t = S.canon_type(it["type"])
    v = it["value"]
    if it.get("kv_field"):
        v = f"{it['kv_field']}={v};"
    name = '"' + it["name"].replace("\\", "\\\\").replace('"', '\\"') + '"'
    if t == "dword":
        return f"{name}=dword:{int(v) & 0xFFFFFFFF:08x}"
    if t == "binary":
        h = S.canon_value("binary", v)
        return f"{name}=hex:" + ",".join(h[i:i + 2] for i in range(0, len(h), 2))
    return name + '="' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def reg_file(items: List[Dict[str, Any]], absolute: bool = False, offline_system: bool = False) -> str:
    """.reg text.  absolute: HKLM\\SOFTWARE / HKLM\\SYSTEM / HKEY_USERS\\.DEFAULT prefixes;
    else hive-relative paths with every ancestor (what hivexregedit --merge needs)."""
    keys: Dict[str, List[str]] = {}
    for it in items:
        path = it["path"]
        if it["scope"] == "system" and offline_system:
            parts = path.split("\\")
            if parts[0].lower() == "currentcontrolset":
                parts[0] = "ControlSet001"
            path = "\\".join(parts)
        if absolute:
            pre = {"software": "HKEY_LOCAL_MACHINE\\SOFTWARE", "system": "HKEY_LOCAL_MACHINE\\SYSTEM",
                   "user": "HKEY_USERS\\.DEFAULT"}[it["scope"]]
            k = f"{pre}\\{path}"
        else:
            k = "\\" + path
        keys.setdefault(k, []).append(_reg_value_line(it))
    if not absolute:
        for k in list(keys):
            parts = k.strip("\\").split("\\")
            for n in range(1, len(parts)):
                keys.setdefault("\\" + "\\".join(parts[:n]), [])
    out = ["Windows Registry Editor Version 5.00", ""]
    for k in sorted(keys, key=lambda s: s.lower()):
        out.append(f"[{k}]")
        out.extend(keys[k])
        out.append("")
    return "\r\n".join(out) + "\r\n"


# ---------------------------------------------------------------------------
# offline image (real regf hives + real WIM)
# ---------------------------------------------------------------------------

def mkhive(path: str) -> None:
    """An empty but valid regf hive (root nk + one sk) that hivexregedit --merge can fill."""
    def cell(payload: bytes) -> bytes:
        n = (4 + len(payload) + 7) & ~7
        return struct.pack("<i", -n) + payload + b"\0" * (n - 4 - len(payload))
    name = b"ROOT"
    nk_off = 0x20
    nk_size = (4 + 76 + len(name) + 7) & ~7
    sk_off = nk_off + nk_size
    sd = struct.pack("<BBHIIII", 1, 0, 0x8004, 0, 0, 0, 0)
    nk = (b"nk" + struct.pack("<H", 0x2C) + struct.pack("<Q", 0) + struct.pack("<I", 0) + struct.pack("<I", 0xFFFFFFFF)
          + struct.pack("<II", 0, 0) + struct.pack("<II", 0xFFFFFFFF, 0xFFFFFFFF) + struct.pack("<II", 0, 0xFFFFFFFF)
          + struct.pack("<II", sk_off, 0xFFFFFFFF) + struct.pack("<IIIII", 0, 0, 0, 0, 0)
          + struct.pack("<HH", len(name), 0) + name)
    sk = b"sk" + struct.pack("<H", 0) + struct.pack("<III", sk_off, sk_off, 1) + struct.pack("<I", len(sd)) + sd
    c1, c2 = cell(nk), cell(sk)
    free = 0x1000 - (0x20 + len(c1) + len(c2))
    hbin = b"hbin" + struct.pack("<II", 0, 0x1000) + b"\0" * 8 + struct.pack("<Q", 0) + struct.pack("<I", 0)
    hbin += c1 + c2 + struct.pack("<i", free) + b"\0" * (free - 4)
    hdr = bytearray(0x1000)
    struct.pack_into("<4sIIQIIIII", hdr, 0, b"regf", 1, 1, 0, 1, 5, 0, 1, nk_off)
    struct.pack_into("<I", hdr, 0x28, 0x1000)
    struct.pack_into("<I", hdr, 0x2C, 1)
    ck = 0
    for i in range(0, 0x1FC, 4):
        ck ^= struct.unpack_from("<I", hdr, i)[0]
    struct.pack_into("<I", hdr, 0x1FC, ck)
    with open(path, "wb") as fh:
        fh.write(bytes(hdr) + hbin)


def fill_hive(path: str, items: List[Dict[str, Any]], offline_system: bool = False) -> None:
    mkhive(path)
    if not items:
        return
    reg = path + ".reg"
    with open(reg, "w", encoding="utf-8") as fh:
        fh.write(reg_file(items, offline_system=offline_system))
    subprocess.run(["hivexregedit", "--merge", path, reg], check=True, capture_output=True, timeout=300)
    os.unlink(reg)


def build_image(out: str, bundle: str, items: List[Dict[str, Any]], branch: str,
                extra_user: Optional[List[Dict[str, Any]]] = None, extra_infs: Optional[Dict[str, str]] = None,
                drop: Optional[List[str]] = None, build: str = "29683") -> str:
    """install.wim from a rendered bundle: the bundle's payload becomes the image tree, its registry
    items go into real hives, its drivers into the driver store.  Returns the WIM path."""
    tree = os.path.join(out, "tree")
    shutil.copytree(os.path.join(bundle, "payload"), tree)
    cfg = os.path.join(tree, "Windows", "System32", "config")
    os.makedirs(cfg, exist_ok=True)
    os.makedirs(os.path.join(tree, "Users", "Default"), exist_ok=True)
    reg = [i for i in items if i["kind"] == "reg" and not i.get("absent")]
    sw = [i for i in reg if i["scope"] == "software"]
    sw += [{"scope": "software", "path": "Microsoft\\Windows NT\\CurrentVersion", "name": "BuildBranch", "type": "sz", "value": branch},
           {"scope": "software", "path": "Microsoft\\Windows NT\\CurrentVersion", "name": "CurrentBuild", "type": "sz", "value": build}]
    for i in items:
        if i["kind"] == "feature" and i["state"] in ("enabled", "disabled"):
            sw.append({"scope": "software", "path": "Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\Notifications\\OptionalFeatures\\" + i["name"],
                       "name": "Selection", "type": "dword", "value": 1 if i["state"] == "enabled" else 0})
    fill_hive(os.path.join(cfg, "SOFTWARE"), sw)
    fill_hive(os.path.join(cfg, "SYSTEM"), [i for i in reg if i["scope"] == "system"], offline_system=True)
    fill_hive(os.path.join(tree, "Users", "Default", "NTUSER.DAT"), [i for i in reg if i["scope"] == "user"] + list(extra_user or []))
    repo = os.path.join(tree, "Windows", "System32", "DriverStore", "FileRepository")
    infs = {i["inf"]: virtio_inf(i["inf"]) for i in items if i["kind"] == "driver"}
    infs.update(extra_infs or {})
    for base, text in infs.items():
        d = os.path.join(repo, f"{base}.inf_amd64_{hashlib.sha1(base.encode()).hexdigest()[:16]}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, base + ".inf"), "w", encoding="utf-8") as fh:
            fh.write(text)
    for i in items:
        if i["kind"] == "component" and i.get("task"):
            t = os.path.join(tree, "Windows", "System32", "Tasks", *i["task"].split("\\"))
            os.makedirs(os.path.dirname(t), exist_ok=True)
            with open(t, "w", encoding="utf-8") as fh:
                fh.write("<Task/>\n")
    for rel in drop or []:
        p = os.path.join(tree, rel)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.unlink(p)
    wim = os.path.join(out, "install.wim")
    subprocess.run(["wimlib-imagex", "capture", tree, wim, "Windows 11 Pro", "--compress=none", "--no-acls"],
                   check=True, capture_output=True, timeout=600)
    return wim


# ---------------------------------------------------------------------------
# recorded in-guest probe outputs
# ---------------------------------------------------------------------------

def conforming_probe(items: List[Dict[str, Any]], branch: str, root: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """The probe output of a guest that matches the spec in every item."""
    obs: Dict[str, Any] = {}
    gpu_ven = "10DE"
    for it in items:
        k = it["kind"]
        if k == "reg":
            if it.get("absent"):
                obs[it["id"]] = {"present": False}
            else:
                v = it["value"] if not it.get("kv_field") else f"{it['kv_field']}={it['value']};"
                t = S.canon_type(it["type"])
                obs[it["id"]] = {"present": True, "type": t, "value": S.canon_value(t, v)}
        elif k == "feature":
            obs[it["id"]] = {"state": "Disabled" if it["state"] == "disabled" else "Enabled"}
        elif k == "capability":
            obs[it["id"]] = {"state": "Installed"}
        elif k == "driver":
            obs[it["id"]] = {"present": True, "version": "100.0.0.0"}
        elif k == "guest_tool":
            obs[it["id"]] = {"service": {"present": True, "status": "Running", "start": "Automatic"}} if it.get("service") else {}
        elif k == "component":
            o: Dict[str, Any] = {"present": True}
            if it.get("sha256"):
                o["sha256"] = it["sha256"]
            if it.get("task"):
                o["task"] = {"present": True, "state": "Ready"}
            obs[it["id"]] = o
        elif k == "build_family":
            obs[it["id"]] = {"present": True, "sha256": it["families"][branch]["start2_sha256"]}
        elif k == "user":
            obs[it["id"]] = {"present": True, "enabled": True, "groups": list(it.get("groups") or [])}
        elif k == "rid500":
            obs[it["id"]] = {"present": True, "enabled": False}
        elif k == "computer_name":
            obs[it["id"]] = {"value": str(it["value"]).upper()[:15]}
        elif k == "appx":
            obs[it["id"]] = {"present": True, "version": "1.0.0.0"}
        elif k == "gpu_step":
            obs[it["id"]] = {"present": True, "record": {"schema": 1, "status": "ok", "devices": [
                {"vendor": "nvidia", "vendor_id": gpu_ven, "device_id": "2684", "status": "installed",
                 "version": "999.99", "installed_driver_version": "32.0.15.9999", "driver_provider": "NVIDIA"}], "skipped": []}}
    desktop = next(i for i in items if i["kind"] == "user" and i.get("role") == "desktop")
    return {"schema": "mios-xbox-probe/1", "probed_at": "2026-10-16T00:00:00Z", "auth_user": desktop["name"],
            "build": "29683", "branch": branch, "ubr": 1000,
            "desktop_user": {"name": desktop["name"], "sid": "S-1-5-21-1-2-3-1001", "hive": "signed-in"},
            "autologon": {"auto_admin_logon": "1", "default_user": desktop["name"], "logon_count": None, "password_sha256": None},
            "nested_virt": False, "hypervisor": True,
            "display": [{"device_id": "PCI\\VEN_10DE&DEV_2684&SUBSYS_00000000&REV_A1\\4&1", "name": "NVIDIA GeForce RTX 4090",
                         "provider": "NVIDIA", "version": "32.0.15.9999", "inf": "oem12.inf"},
                        {"device_id": "PCI\\VEN_1AF4&DEV_1050&SUBSYS_11001AF4&REV_01\\3&1", "name": "Red Hat VirtIO GPU DOD controller",
                         "provider": "Red Hat, Inc.", "version": "100.0.0.0", "inf": "oem5.inf"}],
            "third_party_drivers": [], "observations": obs, "errors": []}
