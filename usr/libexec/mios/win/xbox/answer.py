#!/usr/bin/env python3
# AI-hint: MiOS-Xbox payload renderer (xb-05/xb-08/xb-10/xb-12): from ONE rendered xbox-spec.json (render_spec.py) plus the verified build inputs, writes everything the image carries -- autounattend.xml (accounts/AutoLogon/locale/disk/Xbox home from the spec, credentials from the layered SSOT only), SetupComplete.cmd + the rendered SYSTEM setup script (guest tools, WinFsp/VirtIO-FS, WebView2, Looking Glass host + IDD, GPU detection task, WinRM for the desktop admin on the host-only subnet, groups, nested-virt features, capabilities), the first-logon script (persistent AutoLogon, Store hydrate task, RID-500 disabled at finalize), the offline registry .reg the worker imports, the credential-free guest spec projection and the MiOS components. No legacy literal, no credential literal outside the answer file, no GPU vendor driver.
# AI-related: usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/build.py, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1, tests/test-mios-xbox-build.py
# AI-functions: render_answer_file, render_registry, render_setup_script, render_firstlogon_script, stage_components, render_payload, main
"""Render the MiOS-Xbox image payload from xbox-spec.json.

  answer.py --spec build/xbox-spec.json --inputs build/inputs.json --out build/payload

`inputs.json` (written by fetch.py) names the verified local installers:
  {"installers": {"<role>": {"path": ..., "sha256": ..., "signer": ..., "version": ..., "url": ...}}}
with roles virtio_guest_tools, qemu_ga, winfsp, webview2, looking_glass_host, looking_glass_idd.

Exit 0 rendered; 2 spec/input error (named); 1 usage. Credentials are never printed."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
sys.path.insert(0, _HERE)
import render_spec  # noqa: E402

UNS = "urn:schemas-microsoft-com:unattend"
WCM = "http://schemas.microsoft.com/WMIConfig/2002/State"
PKT = "31bf3856ad364e35"
OFFLINE_MOUNT = {"SOFTWARE": "MIOS_SW", "SYSTEM": "MIOS_SYS", "DEFAULT_USER": "MIOS_DEFU"}
# Well-known local group SIDs (locale-independent membership; the spec names English group names).
GROUP_SIDS = {"administrators": "S-1-5-32-544", "users": "S-1-5-32-545", "remote desktop users": "S-1-5-32-555",
              "remote management users": "S-1-5-32-580", "performance log users": "S-1-5-32-559"}
INSTALLER_ROLES = ("virtio_guest_tools", "qemu_ga", "winfsp", "webview2", "looking_glass_host", "looking_glass_idd")
SETUP_SCRIPT = "mios-xbox-setup.ps1"
FIRSTLOGON_SCRIPT = "mios-xbox-firstlogon.ps1"
STATE_DIR = "ProgramData/MiOS"
PROVISION_SCRIPT = os.path.join(_TREE, "usr", "share", "mios", "windows", "build", "MiOS-Xbox.Provision.ps1")


class PayloadError(Exception):
    """A spec value or build input the payload needs is missing or malformed (named, never a secret)."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _req(d: Any, dotted: str) -> Any:
    cur = d
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            raise PayloadError(f"windows.xbox.{dotted}: missing in xbox-spec.json")
        cur = cur[p]
    return cur


def _win_rel(p: str) -> str:
    return re.sub(r"^[A-Za-z]:", "", str(p)).replace("\\", "/").lstrip("/")


def _ps(v: Any) -> str:
    """PowerShell single-quoted literal."""
    return "'" + str(v).replace("'", "''") + "'"


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(path: str, data: bytes, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


# ---------------------------------------------------------------------------
# answer file
# ---------------------------------------------------------------------------

def _el(parent: ET.Element, tag: str, text: Any = None, **attrs: str) -> ET.Element:
    e = ET.SubElement(parent, f"{{{UNS}}}{tag}", {k.replace("__", ":"): v for k, v in attrs.items()})
    if text is not None:
        e.text = str(text).lower() if isinstance(text, bool) else str(text)
    return e


def _component(settings: ET.Element, name: str, arch: str) -> ET.Element:
    return ET.SubElement(settings, f"{{{UNS}}}component", {
        "name": name, "processorArchitecture": arch, "publicKeyToken": PKT,
        "language": "neutral", "versionScope": "nonSxS"})


def _added(parent: ET.Element, tag: str) -> ET.Element:
    return ET.SubElement(parent, f"{{{UNS}}}{tag}", {f"{{{WCM}}}action": "add"})


def _password(parent: ET.Element, tag: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(f"users: empty credential for <{tag}> (the layered SSOT must supply it)")
    p = _el(parent, tag)
    _el(p, "Value", value)
    _el(p, "PlainText", True)


def render_answer_file(doc: Dict[str, Any]) -> bytes:
    """autounattend.xml from the rendered spec: every value comes from xbox-spec.json."""
    x = doc["spec"]
    arch = _req(x, "source.arch")
    reg = doc["region"]
    users = doc["users"]
    desk = users["desktop"]
    setup = _req(x, "settings.setup")
    oobe = _req(x, "settings.oobe")
    xb = _req(x, "xbox")
    svc = (users.get("service_account") or {}).get("name", "")
    if desk.get("autologon") and svc and str(desk["name"]).lower() == str(svc).lower():
        raise PayloadError("users.desktop: AutoLogon would sign in the service account")
    if desk.get("autologon") and str(desk["name"]).lower() in render_spec.RID500_NAMES:
        raise PayloadError("users.desktop: AutoLogon would sign in the built-in Administrator")
    groups = list(desk.get("groups") or [])
    if not groups:
        raise PayloadError("windows.xbox.users.desktop.groups: empty")
    admin_acct = users.get("admin")
    if admin_acct:
        admin_groups = list(admin_acct.get("groups") or [])
        if not admin_groups or admin_groups[0].lower() != "administrators":
            raise PayloadError("windows.xbox.users.admin.groups: the first group must be Administrators (separate elevation admin)")
        if groups[0].lower() not in ("users", "administrators"):
            raise PayloadError("windows.xbox.users.desktop.groups: the first group must be Users or Administrators")
    else:
        if groups[0].lower() != "administrators":
            raise PayloadError("windows.xbox.users.desktop.groups: the first group must be Administrators (separate desktop admin)")

    ET.register_namespace("", UNS)
    ET.register_namespace("wcm", WCM)
    root = ET.Element(f"{{{UNS}}}unattend")

    # windowsPE
    pe = _el(root, "settings", **{"pass": "windowsPE"})
    intl = _component(pe, "Microsoft-Windows-International-Core-WinPE", arch)
    _el(intl, "InputLocale", reg["input_locale"])
    _el(intl, "SetupUILanguage").append(_mk("UILanguage", reg["ui_language"]))
    _el(intl, "SystemLocale", reg["ui_language"])
    _el(intl, "UILanguage", reg["ui_language"])
    _el(intl, "UserLocale", reg["ui_language"])
    st = _component(pe, "Microsoft-Windows-Setup", arch)
    if setup["wipe_disk"]:
        dc = _el(st, "DiskConfiguration")
        disk = _added(dc, "Disk")
        cps = _el(disk, "CreatePartitions")
        for order, ptype, size in ((1, "EFI", setup["efi_mb"]), (2, "MSR", setup["msr_mb"]), (3, "Primary", None)):
            cp = _added(cps, "CreatePartition")
            if size is None:
                _el(cp, "Extend", True)
            _el(cp, "Order", order)
            if size is not None:
                _el(cp, "Size", size)
            _el(cp, "Type", ptype)
        _el(disk, "DiskID", setup["disk_id"])
        mps = _el(disk, "ModifyPartitions")
        for order, pid, fmt, label, letter in ((1, 1, "FAT32", "System", None), (2, 2, None, None, None),
                                                (3, 3, "NTFS", "Windows", "C")):
            mp = _added(mps, "ModifyPartition")
            if fmt:
                _el(mp, "Format", fmt)
            if label:
                _el(mp, "Label", label)
            if letter:
                _el(mp, "Letter", letter)
            _el(mp, "Order", order)
            _el(mp, "PartitionID", pid)
        _el(disk, "WillWipeDisk", True)
    ii = _el(st, "ImageInstall")
    osi = _el(ii, "OSImage")
    md = _added(_el(osi, "InstallFrom"), "MetaData")
    _el(md, "Key", "/IMAGE/INDEX")
    _el(md, "Value", 1)   # New-MiOSXboxImage exports exactly source.edition as index 1
    it = _el(osi, "InstallTo")
    _el(it, "DiskID", setup["disk_id"])
    _el(it, "PartitionID", 3)
    ud = _el(st, "UserData")
    _el(ud, "AcceptEula", True)
    pk = _el(ud, "ProductKey")
    _el(pk, "Key", setup["install_key"])
    _el(pk, "WillShowUI", "OnError")

    # specialize
    sp = _el(root, "settings", **{"pass": "specialize"})
    sh = _component(sp, "Microsoft-Windows-Shell-Setup", arch)
    cn = str(users["computer_name"])
    if not re.fullmatch(r"[A-Za-z0-9-]{1,15}", cn) or cn.isdigit():
        raise PayloadError("windows.xbox.users.computer_name_ref: not a valid NetBIOS computer name (1-15 of A-Z 0-9 -)")
    _el(sh, "ComputerName", cn)
    _el(sh, "TimeZone", reg["timezone"])
    if xb["enable"] and xb["home_app"]:
        gc = _component(sp, "Microsoft-Windows-Gaming-Configuration", arch)
        _el(gc, "GamingHomeApp", xb["home_app"])
        _el(gc, "StartupToGamingHome", bool(xb["start_on_startup"]))

    ssd = _component(sp, "Microsoft-Windows-SecureStartup-FilterDriver", arch)
    _el(ssd, "PreventDeviceEncryption", True)

    dep = _component(sp, "Microsoft-Windows-Deployment", arch)
    rsync = _el(dep, "RunSynchronous")
    cmd1 = _added(rsync, "RunSynchronousCommand")
    _el(cmd1, "Order", 1)
    _el(cmd1, "Path", r"reg add HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation /v RealTimeIsUniversal /t REG_DWORD /d 1 /f")
    _el(cmd1, "Description", "UTC hardware clock")
    cmd2 = _added(rsync, "RunSynchronousCommand")
    _el(cmd2, "Order", 2)
    _el(cmd2, "Path", r"reg add HKLM\SYSTEM\CurrentControlSet\Control\BitLocker /v PreventDeviceEncryption /t REG_DWORD /d 1 /f")
    _el(cmd2, "Description", "Prevent automatic BitLocker device encryption")

    # oobeSystem
    ob = _el(root, "settings", **{"pass": "oobeSystem"})
    intl2 = _component(ob, "Microsoft-Windows-International-Core", arch)
    _el(intl2, "InputLocale", reg["input_locale"])
    _el(intl2, "SystemLocale", reg["ui_language"])
    _el(intl2, "UILanguage", reg["ui_language"])
    _el(intl2, "UserLocale", reg["ui_language"])
    sh2 = _component(ob, "Microsoft-Windows-Shell-Setup", arch)
    if desk.get("autologon"):
        al = _el(sh2, "AutoLogon")
        _el(al, "Enabled", True)
        count = int(desk.get("logon_count") or 0)
        if count < 1:
            raise PayloadError("users.desktop.logon_count: missing (render_spec derives it from the credential resolver)")
        _el(al, "LogonCount", count)
        _password(al, "Password", desk["password"])
        _el(al, "Username", desk["name"])
    flc = _el(sh2, "FirstLogonCommands")
    sc = _added(flc, "SynchronousCommand")
    _el(sc, "CommandLine", f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\\Windows\\Setup\\Scripts\\{FIRSTLOGON_SCRIPT}"')
    _el(sc, "Description", "MiOS-Xbox first logon (rendered from xbox-spec.json)")
    _el(sc, "Order", 1)
    _el(sc, "RequiresUserInput", False)
    o = _el(sh2, "OOBE")
    _el(o, "HideEULAPage", bool(oobe["hide_eula"]))
    _el(o, "HideLocalAccountScreen", bool(oobe["hide_local_account_screen"]))
    _el(o, "HideOEMRegistrationScreen", bool(oobe["hide_oem_registration"]))
    _el(o, "HideOnlineAccountScreens", bool(oobe["hide_online_account_screens"]))
    _el(o, "HideWirelessSetupInOOBE", bool(oobe["hide_wireless_setup"]))
    _el(o, "ProtectYourPC", int(oobe["protect_your_pc"]))
    ua = _el(sh2, "UserAccounts")
    _password(ua, "AdministratorPassword", users["builtin_administrator"]["password"])
    las = _el(ua, "LocalAccounts")
    la = _added(las, "LocalAccount")
    _el(la, "Description", "MiOS desktop gaming user (layered SSOT)" if admin_acct else "MiOS desktop administrator (layered SSOT)")
    _el(la, "DisplayName", desk.get("display_name") or desk["name"])
    _el(la, "Group", groups[0])
    _el(la, "Name", desk["name"])
    _password(la, "Password", desk["password"])
    if admin_acct:
        la_adm = _added(las, "LocalAccount")
        _el(la_adm, "Description", "MiOS elevation administrator (layered SSOT)")
        _el(la_adm, "DisplayName", admin_acct.get("display_name") or admin_acct["name"])
        adm_groups = list(admin_acct.get("groups") or ["Administrators"])
        _el(la_adm, "Group", adm_groups[0])
        _el(la_adm, "Name", admin_acct["name"])
        _password(la_adm, "Password", admin_acct["password"])

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'   # pin-gate:allow (XML declaration, not a version pin)
            "<!-- MiOS-Xbox answer file rendered by usr/libexec/mios/win/xbox/answer.py from xbox-spec.json "
            "(layered SSOT). Do not edit; re-render. -->\n" + body + "\n").encode("utf-8")


def _mk(tag: str, text: Any) -> ET.Element:
    e = ET.Element(f"{{{UNS}}}{tag}")
    e.text = str(text)
    return e


# ---------------------------------------------------------------------------
# offline registry (.reg imported by New-MiOSXboxImage into the mounted hives)
# ---------------------------------------------------------------------------

def _reg_str(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_registry(doc: Dict[str, Any]) -> bytes:
    """REGEDIT5 text of every registry item of the spec (incl. debloat services) under the offline mount
    names New-MiOSXboxImage loads (HKLM\\MIOS_SW / MIOS_SYS / MIOS_DEFU). kv_field values sharing one
    value are composed into a single 'k=v;' string."""
    keys: Dict[Tuple[str, str], Dict[str, str]] = {}
    order: List[Tuple[str, str]] = []
    kv: Dict[Tuple[str, str, str], List[Tuple[str, Any]]] = {}

    def put(hive: str, key: str, name: str, data: str) -> None:
        k = (hive, key)
        if k not in keys:
            keys[k] = {}
            order.append(k)
        keys[k][name] = data

    for r in doc["registry"]:
        hive = r["hive"]
        if hive not in OFFLINE_MOUNT:
            raise PayloadError(f"registry item {r.get('owner')}: unknown hive {hive!r}")
        if r.get("delete"):
            put(hive, r["key"], r["name"], "-")
        elif r.get("kv_field"):
            kv.setdefault((hive, r["key"], r["name"]), []).append((r["kv_field"], r["data"]))
        elif r["type"] == "REG_DWORD":
            v = int(r["data"])
            if not 0 <= v <= 0xFFFFFFFF:
                raise PayloadError(f"registry item {r.get('owner')}: DWORD out of range")
            put(hive, r["key"], r["name"], f"dword:{v:08x}")
        elif r["type"] == "REG_SZ":
            put(hive, r["key"], r["name"], _reg_str(r["data"]))
        else:
            raise PayloadError(f"registry item {r.get('owner')}: unsupported type {r['type']!r}")
    for (hive, key, name), fields in kv.items():
        put(hive, key, name, _reg_str("".join(f"{f}={v};" for f, v in fields)))
    for svc in doc.get("debloat_services") or []:
        put("SYSTEM", f"ControlSet001\\Services\\{svc['name']}", "Start", f"dword:{int(svc['start']):08x}")
    lines = ["Windows Registry Editor Version 5.00", "",
             "; MiOS-Xbox offline registry, rendered by answer.py from xbox-spec.json (one owner per value).",
             "; Imported by New-MiOSXboxImage.ps1 with the image hives loaded at HKLM\\MIOS_SW / MIOS_SYS / MIOS_DEFU.", ""]
    for hive, key in order:
        lines.append(f"[HKEY_LOCAL_MACHINE\\{OFFLINE_MOUNT[hive]}\\{key}]")
        for name, data in keys[(hive, key)].items():
            lines.append(f"{_reg_str(name)}={data}")
        lines.append("")
    return b"\xff\xfe" + ("\r\n".join(lines) + "\r\n").encode("utf-16-le")   # REGEDIT5 = UTF-16LE with BOM


# ---------------------------------------------------------------------------
# SetupComplete (SYSTEM) and first logon (desktop admin) scripts
# ---------------------------------------------------------------------------

_PS_LIB = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$MiOSState = [ordered]@{}
function Write-MiOSLog([string]$m) {
    $line = "[{0}] {1}" -f [DateTime]::UtcNow.ToString('o'), $m
    Write-Output $line
    try { [System.IO.File]::AppendAllText('\\.\COM1', "$line`r`n") } catch {}
    try { Add-Content -LiteralPath 'C:\ProgramData\MiOS\setup.log' -Value $line -ErrorAction SilentlyContinue } catch {}
}
function Invoke-MiOSStep([string]$Name, [scriptblock]$Body) {
    try { & $Body; $MiOSState[$Name] = 'ok'; Write-MiOSLog "$Name ok" }
    catch { $MiOSState[$Name] = "failed: $($_.Exception.Message)"; Write-MiOSLog "$Name FAILED: $($_.Exception.Message)" }
}
function Assert-MiOSSha256([string]$Path, [string]$Sha256) {
    $got = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $Sha256.ToLowerInvariant()) { throw "$Path sha256 $got does not match the build manifest" }
}
function Assert-MiOSSigned([string]$Path, [string]$Signer) {
    $s = Get-AuthenticodeSignature -LiteralPath $Path
    if ($s.Status -eq 'NotSigned' -and $Signer -eq 'Red Hat') { return }
    if ($s.Status -ne 'Valid' -or -not $s.SignerCertificate -or $s.SignerCertificate.Subject -notmatch [regex]::Escape($Signer)) {
        throw "$Path Authenticode $($s.Status) ($($s.SignerCertificate.Subject)); expected a valid signature by $Signer"
    }
}
function Invoke-MiOSInstaller([string]$File, [string[]]$Arguments) {
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru -WindowStyle Hidden
    if ($p.ExitCode -notin 0, 1641, 3010) { throw "$File exited $($p.ExitCode)" }
}
function Save-MiOSState([string]$Path) {
    $doc = [ordered]@{ schema = 'mios-xbox-setup-state/1'; at = [DateTime]::UtcNow.ToString('o'); steps = $MiOSState }
    $tmp = "$Path.tmp"
    [IO.File]::WriteAllText($tmp, ($doc | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
'''


def _installer(inputs: Dict[str, Any], role: str) -> Dict[str, Any]:
    ins = (inputs.get("installers") or {}).get(role)
    if not isinstance(ins, dict) or not ins.get("path") or not ins.get("sha256"):
        raise PayloadError(f"build input {role}: not fetched/verified (inputs.json)")
    if not os.path.isfile(ins["path"]):
        raise PayloadError(f"build input {role}: {ins['path']} missing")
    if _sha256(ins["path"]) != str(ins["sha256"]).lower():
        raise PayloadError(f"build input {role}: sha256 differs from the verified download")
    return ins


def installer_names(doc: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, str]:
    """Image file name of each installer: the spec names it where the spec does, else the upstream name."""
    x = doc["spec"]
    gt = x["guest_tools"]
    names = {
        "virtio_guest_tools": gt["virtio_win_guest_tools"]["file"],
        "qemu_ga": gt["qemu_ga"]["package"],
        "looking_glass_host": "looking-glass-host.zip",
        "looking_glass_idd": "looking-glass-idd.zip",
    }
    for role in ("winfsp", "webview2"):
        names[role] = os.path.basename(_installer(inputs, role)["path"])
    import fnmatch
    if not fnmatch.fnmatchcase(names["winfsp"].lower(), str(gt["winfsp"]["asset"]).lower()):
        raise PayloadError(f"build input winfsp: {names['winfsp']} does not match guest_tools.winfsp.asset")
    return names


def render_setup_script(doc: Dict[str, Any], inputs: Dict[str, Any]) -> str:
    """SYSTEM stage run by SetupComplete.cmd: literal values rendered from the spec (gate-checkable)."""
    x = doc["spec"]
    dest = str(_req(x, "components.dest_dir"))
    inst_dir = dest + "\\installers"
    names = installer_names(doc, inputs)
    ins = {r: _installer(inputs, r) for r in INSTALLER_ROLES}
    gt = x["guest_tools"]
    gpu = _req(x, "gpu")
    lg = doc["resolved"].get("vm.display_ref") or {}
    if not isinstance(lg, dict) or "idd_exclusive" not in lg or "mirror_displays" not in lg:
        raise PayloadError("windows.xbox.vm.display_ref: must resolve to [vm.looking_glass] (idd_exclusive, mirror_displays)")
    desk = doc["users"]["desktop"]
    admin_acct = doc["users"].get("admin")
    w = doc.get("winrm") or {}
    out: List[str] = [
        "# MiOS-Xbox SetupComplete stage (SYSTEM). Rendered by usr/libexec/mios/win/xbox/answer.py from xbox-spec.json.",
        "# Every value below is the spec's; re-render instead of editing. Results: C:\\ProgramData\\MiOS\\setup-state.json",
        _PS_LIB.strip("\n"),
        f"$MiOS = {_ps(dest)}",
        f"$Inst = {_ps(inst_dir)}",
        "New-Item -ItemType Directory -Force -Path 'C:\\ProgramData\\MiOS' | Out-Null",
        "",
        "Invoke-MiOSStep 'virtio_win_guest_tools' {",
        f"    $f = Join-Path $Inst {_ps(names['virtio_guest_tools'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['virtio_guest_tools']['sha256'])}",
        f"    Assert-MiOSSigned $f {_ps(ins['virtio_guest_tools']['signer'])}",
        "    Invoke-MiOSInstaller $f @('/install', '/quiet', '/norestart')",
        "}",
        "Invoke-MiOSStep 'qemu_ga' {",
        f"    $f = Join-Path $Inst {_ps(names['qemu_ga'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['qemu_ga']['sha256'])}",
        f"    Assert-MiOSSigned $f {_ps(ins['qemu_ga']['signer'])}",
        "    Invoke-MiOSInstaller 'msiexec.exe' @('/i', ('\"{0}\"' -f $f), '/qn', '/norestart')",
        f"    Set-Service -Name {_ps(gt['qemu_ga']['service'])} -StartupType Automatic",
        f"    Start-Service -Name {_ps(gt['qemu_ga']['service'])} -ErrorAction SilentlyContinue",
        "}",
        "Invoke-MiOSStep 'spice_vdagent' {",
        f"    Set-Service -Name {_ps(gt['spice_vdagent']['service'])} -StartupType Automatic",
        "}",
        "Invoke-MiOSStep 'looking_glass_idd' {",
        f"    $z = Join-Path $Inst {_ps(names['looking_glass_idd'])}",
        f"    & (Join-Path $MiOS 'mios-looking-glass-idd.ps1') -Url {_ps(ins['looking_glass_idd']['url'])} "
        f"-Sha256 {_ps(ins['looking_glass_idd']['sha256'])} -Version {_ps(ins['looking_glass_idd']['version'])} "
        f"-Signer {_ps('CN=' + str(ins['looking_glass_idd']['signer']))} -Package $z "
        f"-ExclusiveMonitor {1 if lg['idd_exclusive'] else 0} -MirrorDisplays {1 if lg['mirror_displays'] else 0}",
        "}",
        "Invoke-MiOSStep 'looking_glass_host' {",
        f"    $z = Join-Path $Inst {_ps(names['looking_glass_host'])}",
        f"    Assert-MiOSSha256 $z {_ps(ins['looking_glass_host']['sha256'])}",
        "    $d = Join-Path $env:ProgramData 'MiOS\\looking-glass\\host'",
        "    Expand-Archive -Force -LiteralPath $z -DestinationPath $d",
        "    $exe = Get-ChildItem -Path $d -Recurse -Filter 'looking-glass-host-setup.exe' | Select-Object -First 1",
        "    if (-not $exe) { throw 'looking-glass-host-setup.exe not in the host artifact' }",
        f"    Assert-MiOSSigned $exe.FullName {_ps(ins['looking_glass_host']['signer'])}",
        "    Invoke-MiOSInstaller $exe.FullName @('/S')",
        f"    Set-Service -Name {_ps(gt['looking_glass_host']['service'])} -StartupType Automatic -ErrorAction SilentlyContinue",
        f"    Start-Service -Name {_ps(gt['looking_glass_host']['service'])} -ErrorAction SilentlyContinue",
        "}",
        "Invoke-MiOSStep 'winfsp' {",
        f"    $f = Join-Path $Inst {_ps(names['winfsp'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['winfsp']['sha256'])}",
        "    Invoke-MiOSInstaller 'msiexec.exe' @('/i', ('\"{0}\"' -f $f), '/qn', '/norestart')",
        "}",
        "Invoke-MiOSStep 'virtiofs' {",
        f"    $svc = Get-Service -Name {_ps(gt['virtiofs']['service'])} -ErrorAction SilentlyContinue",
        "    if (-not $svc) {",
        "        $exe = Get-ChildItem -Path (Join-Path $env:ProgramFiles 'Virtio-Win') -Recurse -Filter 'virtiofs.exe' -ErrorAction SilentlyContinue | Select-Object -First 1",
        "        if (-not $exe) { throw 'virtiofs.exe not installed by the virtio-win guest tools' }",
        f"        New-Service -Name {_ps(gt['virtiofs']['service'])} -BinaryPathName ('\"{{0}}\"' -f $exe.FullName) "
        f"-StartupType Automatic -DependsOn 'WinFsp.Launcher' -DisplayName 'Virtio FS Service' | Out-Null",
        "    }",
        f"    Set-Service -Name {_ps(gt['virtiofs']['service'])} -StartupType Automatic",
        "}",
        "Invoke-MiOSStep 'webview2' {",
        f"    $f = Join-Path $Inst {_ps(names['webview2'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['webview2']['sha256'])}",
        f"    Assert-MiOSSigned $f {_ps(ins['webview2']['signer'])}",
        "    Invoke-MiOSInstaller $f @('/silent', '/install')",
        "}",
        "Invoke-MiOSStep 'gpu_step' {",
        f"    & {_ps(gpu['script_path'])} -SpecPath {_ps(gpu['spec_path'])} -Register",
        "}",
        "Invoke-MiOSStep 'groups' {",
    ]
    for g in desk.get("groups") or []:
        sid = GROUP_SIDS.get(str(g).lower())
        if not sid:
            raise PayloadError(f"windows.xbox.users.desktop.groups: no well-known SID for {g!r}")
        out.append(f"    if (-not (Get-LocalGroupMember -SID {_ps(sid)} -Member {_ps(desk['name'])} -ErrorAction SilentlyContinue)) "
                   f"{{ Add-LocalGroupMember -SID {_ps(sid)} -Member {_ps(desk['name'])} }}")
    if admin_acct:
        for g in admin_acct.get("groups") or []:
            sid = GROUP_SIDS.get(str(g).lower())
            if not sid:
                raise PayloadError(f"windows.xbox.users.admin.groups: no well-known SID for {g!r}")
            out.append(f"    if (-not (Get-LocalGroupMember -SID {_ps(sid)} -Member {_ps(admin_acct['name'])} -ErrorAction SilentlyContinue)) "
                       f"{{ Add-LocalGroupMember -SID {_ps(sid)} -Member {_ps(admin_acct['name'])} }}")
    out.append("}")
    out += [
        "Invoke-MiOSStep 'utc_clock' {",
        "    Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\TimeZoneInformation' -Name 'RealTimeIsUniversal' -Type DWord -Value 1",
        "}",
        "Invoke-MiOSStep 'sshd_service' {",
        "    Set-Service -Name sshd -StartupType Automatic -ErrorAction SilentlyContinue",
        "    Start-Service -Name sshd -ErrorAction SilentlyContinue",
        "    Enable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue",
        "}",
        "Invoke-MiOSStep 'ssh_setup' {",
        f"    $sshScript = Join-Path $MiOS 'mios-ssh-setup.ps1'",
        "    if (Test-Path -LiteralPath $sshScript) {",
        "        & $sshScript -SettingsPath 'C:\\ProgramData\\MiOS\\ssh-settings.json' -AuthorizedKeysPath 'C:\\ProgramData\\MiOS\\authorized_keys'",
        "    }",
        "}",
        "Invoke-MiOSStep 'rdp_firewall' {",
        "    Enable-NetFirewallRule -Name 'RemoteDesktop-UserMode-In-TCP' -ErrorAction SilentlyContinue",
        "    Set-NetFirewallRule -Name 'RemoteDesktop-UserMode-In-TCP' -RemoteAddress @('LocalSubnet', '100.64.0.0/10') -ErrorAction SilentlyContinue",
        "}",
        "Invoke-MiOSStep 'mios_daemons' {",
        f"    $miosdExe = Join-Path $env:ProgramFiles 'MiOS\\miosd.exe'",
        "    if (Test-Path -LiteralPath $miosdExe) {",
        "        $svc = Get-Service -Name 'miosd' -ErrorAction SilentlyContinue",
        "        if (-not $svc) {",
        "            & sc.exe create miosd binPath= ('\"{0}\"' -f $miosdExe) start= auto DisplayName= 'MiOS Daemon' | Out-Null",
        "        }",
        "        Set-Service -Name 'miosd' -StartupType Automatic -ErrorAction SilentlyContinue",
        "        Start-Service -Name 'miosd' -ErrorAction SilentlyContinue",
        "    }",
        f"    $relayExe = Join-Path $env:ProgramFiles 'MiOS\\mios-agent-relay.exe'",
        "    if (Test-Path -LiteralPath $relayExe) {",
        "        $a = New-ScheduledTaskAction -Execute $relayExe",
        "        $t = New-ScheduledTaskTrigger -AtStartup",
        "        $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest",
        "        Register-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-AgentRelay' -Action $a -Trigger $t -Principal $p -Force -ErrorAction SilentlyContinue | Out-Null",
        "        Start-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-AgentRelay' -ErrorAction SilentlyContinue",
        "    }",
        f"    $svcTool = Join-Path $env:ProgramFiles 'MiOS\\MiosServiceTool.exe'",
        f"    $fhsCfg = Join-Path $env:ProgramFiles 'MiOS\\MiOS-FhsSync.cfg'",
        "    if ((Test-Path -LiteralPath $svcTool) -and (Test-Path -LiteralPath $fhsCfg)) {",
        "        & $svcTool -Install -Config $fhsCfg -ErrorAction SilentlyContinue | Out-Null",
        "        Start-Service -Name 'MiOS-FhsSync' -ErrorAction SilentlyContinue",
        "    }",
        f"    $oscExe = Join-Path $env:ProgramFiles 'MiOS\\mios-oscontrol-server.exe'",
        "    if (Test-Path -LiteralPath $oscExe) {",
        "        $a = New-ScheduledTaskAction -Execute $oscExe",
        "        $t = New-ScheduledTaskTrigger -AtStartup",
        "        $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest",
        "        Register-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-OsControl' -Action $a -Trigger $t -Principal $p -Force -ErrorAction SilentlyContinue | Out-Null",
        "        Start-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-OsControl' -ErrorAction SilentlyContinue",
        "    }",
        "}",
    ]
    if w.get("enable"):
        port = int(w["port"])
        out += [
            "Invoke-MiOSStep 'winrm' {",
            "    Enable-PSRemoting -SkipNetworkProfileCheck -Force | Out-Null",
            "    Set-Item -Path WSMan:\\localhost\\Service\\Auth\\Basic -Value $false",
            "    Set-Item -Path WSMan:\\localhost\\Service\\AllowUnencrypted -Value $false",
            "    $l = Get-ChildItem WSMan:\\localhost\\Listener | Where-Object { $_.Keys -contains 'Transport=HTTP' } | Select-Object -First 1",
            "    if (-not $l) { throw 'no WinRM HTTP listener' }",
            f"    if ((Get-Item (Join-Path $l.PSPath 'Port')).Value -ne '{port}') {{ Set-Item -Path (Join-Path $l.PSPath 'Port') -Value {port} -Force }}",
            "    Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule",
            "    Remove-NetFirewallRule -Name 'MiOS-WinRM-HTTP' -ErrorAction SilentlyContinue",
            f"    New-NetFirewallRule -Name 'MiOS-WinRM-HTTP' -DisplayName 'MiOS WinRM (host probe)' -Direction Inbound -Protocol TCP "
            f"-LocalPort {port} -RemoteAddress {_ps(w['remote_address'])} -Profile Any -Action Allow | Out-Null",
            "    Set-Service -Name WinRM -StartupType Automatic",
            "}",
        ]
    feats = _req(x, "features")
    nested = list(feats.get("enable_if_nested_virt") or [])
    if nested:
        out += ["Invoke-MiOSStep 'nested_virt_features' {",
                "    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1",
                "    if ($cpu -and [bool]$cpu.VirtualizationFirmwareEnabled) {"]
        for f in nested:
            out.append(f"        Enable-WindowsOptionalFeature -Online -FeatureName {_ps(f)} -All -NoRestart | Out-Null")
        out += ["    } else { Write-MiOSLog 'no nested virtualization exposed: conditional features stay off' }", "}"]
    for cap in feats.get("capabilities_add") or []:
        out += [f"Invoke-MiOSStep 'capability:{cap}' {{",
                f"    $c = Get-WindowsCapability -Online -Name {_ps(str(cap) + '*')} | Select-Object -First 1",
                f"    if (-not $c) {{ throw 'capability {cap} is not offered by this build' }}",
                f"    if ($c.State -ne 'Installed') {{ Add-WindowsCapability -Online -Name (Get-WindowsCapability -Online -Name {_ps(str(cap) + '*')} | Select-Object -First 1).Name | Out-Null }}",
                "}"]
    pw = _req(x, "settings.power")
    out += ["Invoke-MiOSStep 'power' {",
            f"    powercfg.exe /setactive {pw['scheme']}",
            f"    powercfg.exe /change monitor-timeout-ac {int(pw['monitor_timeout_ac_min'])}",
            f"    powercfg.exe /change standby-timeout-ac {int(pw['standby_timeout_ac_min'])}"]
    if not pw["hibernate"]:
        out.append("    powercfg.exe /hibernate off")
    out.append("}")
    out += ["Save-MiOSState 'C:\\ProgramData\\MiOS\\setup-state.json'", ""]
    return "\n".join(out)


def render_firstlogon_script(doc: Dict[str, Any]) -> str:
    """First logon of the desktop admin (elevated FirstLogonCommand): persistent AutoLogon, the Store
    hydrate task, then finalize -- the built-in Administrator (RID 500) is disabled once the desktop
    admin is proven to be an enabled member of Administrators."""
    x = doc["spec"]
    desk = doc["users"]["desktop"]
    dest = str(_req(x, "components.dest_dir"))
    prov = dest + "\\windows\\MiOS-Xbox.Provision.ps1"
    hyd = _req(x, "store_hydrate")
    out = ["# MiOS-Xbox first-logon stage (desktop admin, elevated). Rendered by answer.py from xbox-spec.json.",
           _PS_LIB.strip("\n")]
    if desk.get("autologon") and desk.get("autologon_persistent"):
        out += ["Invoke-MiOSStep 'autologon_persistent' {",
                "    Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -Name 'AutoLogonCount' -ErrorAction SilentlyContinue",
                "}"]
    if hyd["packages"]:
        if hyd["stage"] != "first_logon":
            raise PayloadError("windows.xbox.store_hydrate.stage: only first_logon is implemented")
        out += ["Invoke-MiOSStep 'store_hydrate_task' {",
                f"    $a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File \"{prov}\" -Stage Hydrate')",
                f"    $t = New-ScheduledTaskTrigger -AtLogOn -User {_ps(desk['name'])}",
                f"    $p = New-ScheduledTaskPrincipal -UserId {_ps(desk['name'])} -LogonType Interactive -RunLevel Highest",
                "    Register-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-StoreHydrate' -Action $a -Trigger $t -Principal $p -Force | Out-Null",
                "    Start-ScheduledTask -TaskPath '\\MiOS\\' -TaskName 'MiOS-StoreHydrate'",
                "}"]
    out += [
        "Invoke-MiOSStep 'wallpaper_setup' {",
        f"    $wpScript = Join-Path $env:ProgramFiles 'MiOS\\Set-MiOSWallpaper.ps1'",
        "    if (Test-Path -LiteralPath $wpScript) {",
        "        & $wpScript -Restart -ErrorAction SilentlyContinue",
        "    }",
        "}",
    ]
    b = doc["users"]["builtin_administrator"]
    if b.get("disable_at") == "finalize":
        admin_acct = doc["users"].get("admin")
        target_admin = admin_acct["name"] if admin_acct else desk["name"]
        out += ["Invoke-MiOSStep 'finalize_rid500' {",
                f"    $me = Get-LocalUser -Name {_ps(target_admin)}",
                "    if (-not $me.Enabled) { throw 'desktop admin is disabled; RID 500 stays enabled' }",
                f"    if (-not (Get-LocalGroupMember -SID 'S-1-5-32-544' -Member {_ps(target_admin)} -ErrorAction SilentlyContinue)) {{ throw 'desktop admin is not in Administrators; RID 500 stays enabled' }}",
                "    Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' } | Disable-LocalUser",
                "}"]
    elif b.get("disable_at"):
        raise PayloadError("windows.xbox.users.builtin_administrator.disable_at: only finalize is implemented")
    out += ["Save-MiOSState 'C:\\ProgramData\\MiOS\\firstlogon-state.json'",
            f"& {_ps(prov)} -Stage Verify", ""]
    return "\n".join(out)


def render_setupcomplete_cmd() -> str:
    return "\r\n".join([
        "@echo off",
        "rem MiOS-Xbox SetupComplete (rendered by usr/libexec/mios/win/xbox/answer.py; runs as SYSTEM before first logon)",
        'echo [SetupComplete] Starting provisioning >> \\\\.\\COM1 2>nul',
        'if not exist "%ProgramData%\\MiOS" mkdir "%ProgramData%\\MiOS"',
        f'"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%SystemRoot%\\Setup\\Scripts\\{SETUP_SCRIPT}" >> "%ProgramData%\\MiOS\\setupcomplete.log" 2>&1',
        'echo [SetupComplete] Provisioning complete >> \\\\.\\COM1 2>nul',
        "exit /b 0", ""])


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

def _native_manifest(native_dir: str) -> Dict[str, str]:
    p = os.path.join(native_dir, "manifest.json")
    try:
        with open(p, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError) as e:
        raise PayloadError(f"windows.xbox.build.native_dir: {p} unreadable ({e}); build build.native.windows first") from None
    if isinstance(rows, dict):
        rows = rows.get("artifacts") or rows.get("files") or []
    return {str(r["name"]).lower(): str(r["sha256"]).lower() for r in rows if isinstance(r, dict) and r.get("name") and r.get("sha256")}


def stage_components(doc: Dict[str, Any], out_dir: str, tree_root: str, native_dir: str) -> List[Dict[str, Any]]:
    x = doc["spec"]
    dest = _win_rel(_req(x, "components.dest_dir"))
    staged: List[Dict[str, Any]] = []
    nm: Optional[Dict[str, str]] = None
    for i, c in enumerate(_req(x, "components.ship")):
        name, kind, src = c["name"], c["kind"], str(c["source"])
        if kind == "native":
            m = re.fullmatch(r"build\.native\.windows:([\w.-]+)", src)
            if not m:
                raise PayloadError(f"windows.xbox.components.ship[{i}].source: native source must be build.native.windows:<name>")
            fname = f"{name}.exe"
            path = os.path.join(native_dir, f"{m.group(1)}.exe")
            nm = nm if nm is not None else _native_manifest(native_dir)
            want = nm.get(f"{m.group(1)}.exe".lower())
            if not os.path.isfile(path) or not want:
                raise PayloadError(f"component {name}: {m.group(1)}.exe is not in the native build output ({native_dir})")
            if _sha256(path) != want:
                raise PayloadError(f"component {name}: {path} does not match the native build manifest")
        elif kind in ("script", "binary", "config"):
            if os.path.isabs(src) or ".." in src.split("/"):
                raise PayloadError(f"windows.xbox.components.ship[{i}].source: must be a tree-relative path")
            path = os.path.join(tree_root, src)
            fname = os.path.basename(src)
            if not os.path.isfile(path):
                raise PayloadError(f"component {name}: {src} missing in the MiOS tree")
        else:
            raise PayloadError(f"windows.xbox.components.ship[{i}].kind: {kind!r} unknown")
        rel = f"{dest}/{fname}"
        target = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(path, target)
        staged.append({"name": name, "path": rel, "sha256": _sha256(target)})
    gpu = _req(x, "gpu")
    gpu_src = os.path.join(tree_root, str(_req(x, "drivers.gpu_step")))
    if not os.path.isfile(gpu_src):
        raise PayloadError("windows.xbox.drivers.gpu_step: the GPU detection script is missing in the MiOS tree")
    rel = _win_rel(gpu["script_path"])
    os.makedirs(os.path.dirname(os.path.join(out_dir, rel)), exist_ok=True)
    shutil.copyfile(gpu_src, os.path.join(out_dir, rel))
    staged.append({"name": "mios-gpu-driver", "path": rel, "sha256": _sha256(os.path.join(out_dir, rel)), "task": gpu["task_name"]})
    if not os.path.isfile(PROVISION_SCRIPT):
        raise PayloadError("usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1 missing")
    rel = f"{dest}/windows/MiOS-Xbox.Provision.ps1"
    shutil.copyfile(PROVISION_SCRIPT, os.path.join(out_dir, rel))
    staged.append({"name": "MiOS-Xbox.Provision", "path": rel, "sha256": _sha256(os.path.join(out_dir, rel))})
    return staged


# ---------------------------------------------------------------------------
# whole payload
# ---------------------------------------------------------------------------

def render_payload(doc: Dict[str, Any], inputs: Dict[str, Any], out_dir: str, tree_root: str = _TREE,
                   native_dir: Optional[str] = None) -> Dict[str, Any]:
    """Write the payload tree; return its summary (no secret)."""
    if doc.get("schema") != render_spec.SCHEMA or "spec" not in doc or "users" not in doc:
        raise PayloadError("not a render_spec.py xbox-spec.json")
    if doc.get("projection") == "guest":
        raise PayloadError("a guest projection carries no credentials; render the payload from the full spec")
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise PayloadError(f"{out_dir} is not empty (a payload is rendered into a fresh directory)")
    x = doc["spec"]
    native_dir = native_dir or str(_req(x, "build.native_dir"))
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    _write(os.path.join(out_dir, "autounattend.xml"), render_answer_file(doc), 0o600)
    scripts = os.path.join(out_dir, "Windows", "Setup", "Scripts")
    _write(os.path.join(scripts, "SetupComplete.cmd"), render_setupcomplete_cmd().encode("ascii"))
    _write(os.path.join(scripts, SETUP_SCRIPT), _crlf(render_setup_script(doc, inputs)))
    _write(os.path.join(scripts, FIRSTLOGON_SCRIPT), _crlf(render_firstlogon_script(doc)))
    state = os.path.join(out_dir, *STATE_DIR.split("/"))
    _write(os.path.join(state, "xbox-registry.reg"), render_registry(doc))
    _write(os.path.join(state, "xbox-spec.json"),
           (json.dumps(render_spec.guest_projection(doc), indent=2, sort_keys=True) + "\n").encode("utf-8"))
    comps = stage_components(doc, out_dir, tree_root, native_dir)
    names = installer_names(doc, inputs)
    inst_rel = _win_rel(str(_req(x, "components.dest_dir"))) + "/installers"
    inst_rows = {}
    for role in INSTALLER_ROLES:
        ins = _installer(inputs, role)
        rel = f"{inst_rel}/{names[role]}"
        os.makedirs(os.path.dirname(os.path.join(out_dir, rel)), exist_ok=True)
        shutil.copyfile(ins["path"], os.path.join(out_dir, rel))
        inst_rows[role] = {"path": rel, "sha256": ins["sha256"], "version": ins.get("version", ""), "signer": ins.get("signer", "")}
    summary = {"schema": "mios-xbox-payload/1", "components": comps, "installers": inst_rows,
               "registry_items": len(doc["registry"]) + len(doc.get("debloat_services") or []),
               "answer_file": "autounattend.xml"}
    _write(os.path.join(state, "payload.json"), (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="answer.py", description="Render the MiOS-Xbox image payload from xbox-spec.json")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--inputs", required=True, help="inputs.json written by fetch.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=_TREE, help="MiOS tree holding the component sources")
    ap.add_argument("--native-dir")
    a = ap.parse_args(argv)
    try:
        with open(a.spec, encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(a.inputs, encoding="utf-8") as fh:
            inputs = json.load(fh)
        s = render_payload(doc, inputs, a.out, a.root, a.native_dir)
    except (PayloadError, render_spec.SpecError) as e:
        print(f"answer: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"answer: {e}", file=sys.stderr)
        return 2
    print(f"answer: payload {a.out}: {len(s['components'])} components, {len(s['installers'])} installers, "
          f"{s['registry_items']} registry values", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
