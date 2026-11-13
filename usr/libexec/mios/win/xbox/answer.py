#!/usr/bin/env python3
# AI-hint: MiOS-Xbox payload renderer (xb-05/xb-08/xb-10/xb-12, fix-a M1): from ONE rendered xbox-spec.json (render_spec.py) plus the verified build inputs, writes everything the image carries -- autounattend.xml (accounts/AutoLogon/locale/disk/Xbox home from the spec, credentials from the layered SSOT only, NO FirstLogonCommands: the desktop account is a standard user), SetupComplete.cmd + the rendered SYSTEM stage (individual guest-tool MSIs, never the composite bundle; WinFsp/VirtIO-FS, WebView2, Looking Glass host + IDD, GPU detection task, network profile Private before WinRM, groups, OpenSSH.Server proven/fallback-installed from the staged FOD and configured from the SSOT ssh inputs, MiOS services + tasks + wallpaper, nested-virt features, persistent AutoLogon, the Store hydrate logon task, the SYSTEM Verify task, RDP/WinRM proven by a TCP listener (no false-ok step), the cached Panther answer file removed, RID-500 disabled last), a host-readable log on the guest serial port (System.IO.Ports.SerialPort), the offline registry .reg the worker imports, the credential-free guest spec projection and the MiOS components. No legacy literal, no credential literal outside the answer file, no GPU vendor driver.
# AI-related: usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/build.py, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1, tests/test-mios-xbox-build.py
# AI-functions: render_answer_file, render_registry, render_setup_script, render_setupcomplete_cmd, stage_components, stage_extras, render_payload, main
"""Render the MiOS-Xbox image payload from xbox-spec.json.

  answer.py --spec build/xbox-spec.json --inputs build/inputs.json --out build/payload

`inputs.json` (written by fetch.py) names the verified local installers:
  {"installers": {"<role>": {"path": ..., "sha256": ..., "signer": ..., "version": ..., "url": ...}}}
with roles virtio_guest_tools (the virtio-win-gt MSI), spice_vdagent (carved from the verified bundle), qemu_ga,
winfsp, webview2, looking_glass_host, looking_glass_idd; plus fod_dir (the OpenSSH.Server FOD cab + metadata).

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
import zipfile
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
INSTALLER_ROLES = ("virtio_guest_tools", "spice_vdagent", "qemu_ga", "winfsp", "webview2", "looking_glass_host", "looking_glass_idd",
                   "pwsh")   # pwsh: PowerShell 7 of the MiOS Windows shell (xbox-bm-39)
SETUP_SCRIPT = "mios-xbox-setup.ps1"
PS_EXE = r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
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
    # No FirstLogonCommands (fix-a M1): they run as the first account to log on -- the standard desktop user --
    # and Microsoft documents that for a user without administrative privileges under UAC they need an
    # administrator's consent and do not run on Cancel. Every privileged step is the SYSTEM SetupComplete stage.
    if _req(x, "provision.first_logon_commands"):
        raise PayloadError("windows.xbox.provision.first_logon_commands: must be false (nothing privileged runs as the desktop user)")
    o = _el(sh2, "OOBE")
    _el(o, "HideEULAPage", bool(oobe["hide_eula"]))
    _el(o, "HideLocalAccountScreen", bool(oobe["hide_local_account_screen"]))
    _el(o, "HideOEMRegistrationScreen", bool(oobe["hide_oem_registration"]))
    _el(o, "HideOnlineAccountScreens", bool(oobe["hide_online_account_screens"]))
    _el(o, "HideWirelessSetupInOOBE", bool(oobe["hide_wireless_setup"]))
    _el(o, "ProtectYourPC", int(oobe["protect_your_pc"]))
    # the persistent MiOS theme (xbox-bm-39): unattend Themes, the supported way to set the default theme
    th = ((doc.get("identity") or {}).get("theme") or {})
    if not th.get("name") or not (th.get("background") or {}).get("unattend") or not th.get("window_color"):
        raise PayloadError("xbox-spec.json has no identity.theme (render it with render_spec.py)")
    tm = _el(sh2, "Themes")
    _el(tm, "DesktopBackground", th["background"]["unattend"])
    _el(tm, "ThemeName", th["name"])
    _el(tm, "UWPAppsUseLightTheme", bool(th["uwp_apps_use_light_theme"]))
    _el(tm, "WindowColor", th["window_color"])
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
            lines.append(f"{'@' if name == '' else _reg_str(name)}={data}")   # @ = the key's default value
        lines.append("")
    return b"\xff\xfe" + ("\r\n".join(lines) + "\r\n").encode("utf-16-le")   # REGEDIT5 = UTF-16LE with BOM


# ---------------------------------------------------------------------------
# SetupComplete (SYSTEM) stage -- the only provisioning stage that runs privileged work
# ---------------------------------------------------------------------------

_PS_LIB = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$MiOSState = [ordered]@{}
$script:MiOSSerial = $null
function Open-MiOSSerial {
    # Host-readable log: the domain logs this guest port to a host file ([vm.looking_glass].serial_log). A
    # System.IO.Ports.SerialPort, never [IO.File] on \\.\COM1 (.NET Framework FileStream refuses device paths).
    if ($script:MiOSSerial -and $script:MiOSSerial.IsOpen) { return }
    try {
        $p = New-Object System.IO.Ports.SerialPort -ArgumentList $MiOSSerialPort, $MiOSSerialBaud
        $p.WriteTimeout = 2000
        $p.NewLine = "`r`n"
        $p.Open()
        $script:MiOSSerial = $p
    } catch { $script:MiOSSerial = $null }
}
function Write-MiOSLog([string]$m) {
    $line = "[{0}] [MiOS {1}] {2}" -f [DateTime]::UtcNow.ToString('o'), $MiOSStage, $m
    Write-Output $line
    Open-MiOSSerial
    if ($script:MiOSSerial) {
        try { $script:MiOSSerial.WriteLine($line) } catch { try { $script:MiOSSerial.Close() } catch {}; $script:MiOSSerial = $null }
    }
    try { Add-Content -LiteralPath $MiOSLogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
}
function Invoke-MiOSStep([string]$Name, [scriptblock]$Body) {
    Write-MiOSLog "$Name start"
    try { & $Body; $MiOSState[$Name] = 'ok'; Write-MiOSLog "$Name ok" }
    catch { $MiOSState[$Name] = "failed: $($_.Exception.Message)"; Write-MiOSLog "$Name FAILED: $($_.Exception.Message)" }
}
# -- guest devices (xbox-bm-01): one image for VMs and bare metal, decided per PCI device ---------------------------
$MiOSDevices = [ordered]@{}
function Get-MiOSPciIds {
    # present PCI functions as 'VVVV:DDDD' (PCI\VEN_VVVV&DEV_DDDD...)
    @(Get-PnpDevice -PresentOnly -ErrorAction Stop | ForEach-Object {
        if ([string]$_.InstanceId -match '^PCI\\VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})') { '{0}:{1}' -f $Matches[1].ToUpper(), $Matches[2].ToUpper() }
    } | Sort-Object -Unique)
}
function Find-MiOSPciMatch([string[]]$Patterns, [string[]]$Present) {
    # the first present id a 'VVVV:DDDD' / 'VVVV:*' pattern matches, else $null
    foreach ($p in $Patterns) {
        $pv, $pd = ([string]$p).ToUpper() -split ':', 2
        foreach ($id in $Present) {
            $iv, $id2 = ([string]$id) -split ':', 2
            if ($iv -eq $pv -and ($pd -eq '*' -or $pd -eq $id2)) { return $id }
        }
    }
    return $null
}
function Invoke-MiOSGuestStep([string]$Name, [string]$Device, [scriptblock]$Body) {
    # a guest-only step runs only when its SSOT-declared device is present; otherwise it is "not applicable" with the
    # probe's evidence -- never failed, never queued for retry, its service never started
    $d = $MiOSDevices[$Device]
    if (-not $d) { $MiOSState[$Name] = "failed: device $Device was not probed"; Write-MiOSLog "$Name FAILED: device $Device was not probed"; return }
    if (-not $d.present) {
        $why = "not applicable: no $Device device (PCI $(@($d.pci) -join '|') absent)"
        $MiOSState[$Name] = $why
        Write-MiOSLog "$Name $why"
        return
    }
    Invoke-MiOSStep $Name $Body
}
function Assert-MiOSSha256([string]$Path, [string]$Sha256) {
    $got = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $Sha256.ToLowerInvariant()) { throw "$Path sha256 $got does not match the build manifest" }
}
function Assert-MiOSSigned([string]$Path, [string]$Signer) {
    # rendered only for installers their publisher signs; an unsigned artefact is anchored by Assert-MiOSSha256 alone
    if (-not $Signer) { throw "$Path`: no expected Authenticode signer" }
    $s = Get-AuthenticodeSignature -LiteralPath $Path
    if ($s.Status -ne 'Valid' -or -not $s.SignerCertificate -or $s.SignerCertificate.Subject -notmatch [regex]::Escape($Signer)) {
        throw "$Path Authenticode $($s.Status) ($($s.SignerCertificate.Subject)); expected a valid signature by $Signer"
    }
}
function Invoke-MiOSInstaller([string]$File, [string[]]$Arguments) {
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru -WindowStyle Hidden
    if ($p.ExitCode -notin 0, 1641, 3010) { throw "$File exited $($p.ExitCode)" }
}
function Invoke-MiOSMsi([string]$Msi, [string]$Name, [string[]]$Properties = @()) {
    # One MSI per call (never a composite bundle), with a verbose log the failure message points at; $Properties are
    # NAME=value public properties from the SSOT.
    New-Item -ItemType Directory -Force -Path $MiOSLogDir | Out-Null
    $log = Join-Path $MiOSLogDir "$Name.msi.log"
    $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\msiexec.exe') -Wait -PassThru -WindowStyle Hidden `
        -ArgumentList (@('/i', ('"{0}"' -f $Msi), '/qn', '/norestart', '/l*v', ('"{0}"' -f $log)) + @($Properties))
    if ($p.ExitCode -notin 0, 1641, 3010) {
        $why = ''
        try { $why = (@(Get-Content -LiteralPath $log -Tail 600 | Select-String -Pattern 'Return value 3|Error \d{4}\.' | Select-Object -Last 2 | ForEach-Object { $_.Line.Trim() }) -join ' | ') } catch {}
        throw "msiexec $Name exited $($p.ExitCode) (log $log) $why"
    }
}
$script:MiOSRetry = @()
function Add-MiOSRetry([string]$Name, [string]$Msi, [string]$Sha256, [string]$Service) {
    # a guest-tool MSI that failed here is re-run by the SYSTEM retry task at the next startup (until its service exists)
    $script:MiOSRetry += [ordered]@{ name = $Name; msi = $Msi; sha256 = $Sha256; service = $Service }
}
function Start-MiOSDtc {
    # COM+ registrations inside an MSI (qemu-ga's qga-vss RegisterCom) need the Distributed Transaction Coordinator;
    # without it they fail with 0x8004E00F (CONTEXT_E_TMNOTAVAILABLE) and the MSI rolls back
    $d = Get-Service -Name 'MSDTC' -ErrorAction SilentlyContinue
    if (-not $d) { Write-MiOSLog 'MSDTC service absent'; return }
    if ([string]$d.StartType -eq 'Disabled') { Set-Service -Name 'MSDTC' -StartupType Manual }
    if ((Get-Service -Name 'MSDTC').Status -ne 'Running') {
        try { Start-Service -Name 'MSDTC' } catch { Write-MiOSLog "MSDTC did not start: $($_.Exception.Message)" }
    }
    Write-MiOSLog "MSDTC $((Get-Service -Name 'MSDTC').Status)"
}
function Start-MiOSService([string]$Name) {
    Set-Service -Name $Name -StartupType Automatic
    if ((Get-Service -Name $Name).Status -ne 'Running') { Start-Service -Name $Name }
    $st = [string](Get-Service -Name $Name).Status
    if ($st -ne 'Running') { throw "service $Name is $st after start" }
}
function Wait-MiOSListen([int]$Port, [string]$What, [int]$Seconds = 60) {
    # a service reported Running is not a reachable one: require a TCP listener on its port
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count) { Write-MiOSLog "$What listening on TCP $Port"; return }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$What is not listening on TCP $Port after $Seconds s"
}
function Get-MiOSAccount {
    try { return [Security.Principal.WindowsIdentity]::GetCurrent().Name } catch { return [Environment]::UserName }
}
function Save-MiOSState([string]$Path) {
    $doc = [ordered]@{ schema = 'mios-xbox-setup-state/2'; stage = $MiOSStage; account = (Get-MiOSAccount)
                       at = [DateTime]::UtcNow.ToString('o'); steps = $MiOSState }
    $tmp = "$Path.tmp"
    [IO.File]::WriteAllText($tmp, ($doc | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
    $na = @($MiOSState.Values | Where-Object { [string]$_ -like 'not applicable*' }).Count
    Write-MiOSLog "state written: $Path ($(@($MiOSState.Values | Where-Object { $_ -ne 'ok' -and [string]$_ -notlike 'not applicable*' }).Count) failed, $na not applicable, of $($MiOSState.Count))"
}
'''


def guest_devices(x: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """[windows.xbox.devices.<name>] tables (xbox-bm-01): name -> {pci: ['VVVV:DDDD' | 'VVVV:*', ...]}."""
    devs = {k: v for k, v in (x.get("devices") or {}).items() if isinstance(v, dict)}
    if not devs or not str((x.get("devices") or {}).get("record") or ""):
        raise PayloadError("windows.xbox.devices: no device tables or no record path (render the spec with render_spec.py)")
    for n, d in devs.items():
        if not isinstance(d.get("pci"), list) or not d["pci"]:
            raise PayloadError(f"windows.xbox.devices.{n}.pci: empty")
    return devs


def guest_device(x: Dict[str, Any], path: str) -> str:
    """The SSOT required_device of a guest-only piece ('guest_tools.qemu_ga', 'drivers.looking_glass_idd')."""
    dev = _req(x, f"{path}.required_device")
    if dev not in guest_devices(x):
        raise PayloadError(f"windows.xbox.{path}.required_device: {dev!r} is not a [windows.xbox.devices] table")
    return str(dev)


def _installer(inputs: Dict[str, Any], role: str) -> Dict[str, Any]:
    ins = (inputs.get("installers") or {}).get(role)
    if not isinstance(ins, dict) or not ins.get("path") or not ins.get("sha256"):
        raise PayloadError(f"build input {role}: not fetched/verified (inputs.json)")
    if not os.path.isfile(ins["path"]):
        raise PayloadError(f"build input {role}: {ins['path']} missing")
    if _sha256(ins["path"]) != str(ins["sha256"]).lower():
        raise PayloadError(f"build input {role}: sha256 differs from the verified download")
    return ins


def _signed(var: str, ins: Dict[str, Any], required: bool = False) -> List[str]:
    """The Authenticode check of an installer whose publisher signs it (Valid status AND the expected subject).
    An installer its publisher does not sign (virtio-win gt / qemu-ga MSIs: signer "") is anchored by its sha256
    alone -- never by accepting NotSigned (item 7)."""
    signer = str(ins.get("signer") or "")
    if not signer:
        if required:
            raise PayloadError(f"build input {ins.get('path')}: no Authenticode signer recorded for a signed installer")
        return []
    return [f"    Assert-MiOSSigned {var} {_ps(signer)}"]


def installer_names(doc: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, str]:
    """Image file name of each installer: the spec names it where the spec does, else the upstream name."""
    x = doc["spec"]
    gt = x["guest_tools"]
    names = {
        "virtio_guest_tools": gt["virtio_win_guest_tools"]["file"],
        "spice_vdagent": _req(x, "guest_tools.spice_vdagent.package"),
        "qemu_ga": gt["qemu_ga"]["package"],
        "looking_glass_host": "looking-glass-host.zip",
        "looking_glass_idd": "looking-glass-idd.zip",
    }
    for role in ("winfsp", "webview2", "pwsh"):
        names[role] = os.path.basename(_installer(inputs, role)["path"])
    import fnmatch
    if not fnmatch.fnmatchcase(names["winfsp"].lower(), str(gt["winfsp"]["asset"]).lower()):
        raise PayloadError(f"build input winfsp: {names['winfsp']} does not match guest_tools.winfsp.asset")
    if not fnmatch.fnmatchcase(names["pwsh"].lower(), str(_req(x, "shell.pwsh.asset")).lower()):
        raise PayloadError(f"build input pwsh: {names['pwsh']} does not match shell.pwsh.asset")
    if str(names["virtio_guest_tools"]).lower().endswith(".exe"):
        raise PayloadError("windows.xbox.guest_tools.virtio_win_guest_tools.file: the composite bundle .exe is never run "
                           "(silent session-0 install exits 1603); name the virtio-win-gt MSI")
    return names


def _task_path(name: str) -> Tuple[str, str]:
    """'MiOS\\X' -> ('\\MiOS\\', 'X')."""
    parts = [p for p in str(name).replace("/", "\\").split("\\") if p]
    if not parts:
        raise PayloadError(f"task name {name!r} is empty")
    return "\\" + "\\".join(parts[:-1]) + ("\\" if parts[:-1] else ""), parts[-1]


def _stage_header(doc: Dict[str, Any], stage: str, log_file: str) -> List[str]:
    x = doc["spec"]
    pv = _req(x, "provision")
    return [
        f"$MiOSStage = {_ps(stage)}",
        f"$MiOSLogFile = {_ps(log_file)}",
        f"$MiOSLogDir = {_ps(pv['log_dir'])}",
        f"$MiOSSerialPort = {_ps(pv['serial_port'])}",
        f"$MiOSSerialBaud = {int(pv['serial_baud'])}",
        _PS_LIB.strip("\n"),
    ]


def shared_directory_acl(doc: Dict[str, Any], tree_root: str = _TREE) -> str:
    """Reuse the canonical directory ACL function in the trusted SetupComplete script, independently of IVSHMEM."""
    source = render_spec.checked_tree_file(tree_root, _req(doc["spec"], "drivers.looking_glass_idd.installer"),
                                          "drivers.looking_glass_idd.installer")
    with open(source, encoding="utf-8-sig") as fh:
        text = fh.read()
    begin, end = "# BEGIN Protect-MiOSDir", "# END Protect-MiOSDir"
    if text.count(begin) != 1 or text.count(end) != 1:
        raise PayloadError("canonical Protect-MiOSDir function markers are missing or duplicated")
    body = text.split(begin, 1)[1].split(end, 1)[0]
    return "\n".join(body.splitlines()[1:]).strip()


def service_binpath(doc: Dict[str, Any], name: str, tree_root: str = _TREE) -> Tuple[str, str]:
    """(binary path name for CreateService, the exe that must exist) of [windows.xbox.services.<name>]."""
    x = doc["spec"]
    dest = str(_req(x, "components.dest_dir"))
    comps = {c["name"]: c for c in _req(x, "components.ship")}
    sv = _req(x, f"services.{name}")
    comp = comps.get(sv.get("component"))
    if not comp:
        raise PayloadError(f"windows.xbox.services.{name}.component: not a components.ship entry")
    target = dest + "\\" + (f"{comp['name']}.exe" if comp["kind"] == "native" else os.path.basename(comp["source"]))
    args = str(sv.get("args") or "")
    if sv.get("args_from"):
        cfg = comps.get(sv["args_from"])
        if not cfg or cfg.get("kind") != "config":
            raise PayloadError(f"windows.xbox.services.{name}.args_from: not a shipped config")
        try:
            with open(os.path.join(tree_root, cfg["source"]), encoding="utf-8-sig") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
        except OSError as e:
            raise PayloadError(f"windows.xbox.services.{name}.args_from: {cfg['source']} unreadable ({e})") from None
        if len(lines) < 2:
            raise PayloadError(f"{cfg['source']}: needs an exe line and an argument line")
        args = lines[1]
    if sv["host"] == "self":
        return (f'"{target}"' + (f" {args}" if args else "")), target
    if sv["host"] == "MiosServiceTool":
        host = dest + "\\" + str(sv["host_name"]) + ".exe"
        return f'"{host}"', host
    raise PayloadError(f"windows.xbox.services.{name}.host: {sv.get('host')!r} unsupported")


def render_setup_script(doc: Dict[str, Any], inputs: Dict[str, Any], tree_root: str = _TREE) -> str:
    """The SYSTEM stage run by SetupComplete.cmd (Microsoft: SetupComplete.cmd runs with local system permissions,
    after Windows is installed and before the logon screen). EVERY privileged step lives here -- guest tools,
    services, tasks, persistent AutoLogon, RID-500 -- because the desktop account is a standard user (no
    FirstLogonCommands). Literal values rendered from the spec (gate-checkable)."""
    x = doc["spec"]
    dest = str(_req(x, "components.dest_dir"))
    inst_dir = dest + "\\installers"
    names = installer_names(doc, inputs)
    ins = {r: _installer(inputs, r) for r in INSTALLER_ROLES}
    gt = x["guest_tools"]
    gpu = _req(x, "gpu")
    pv = _req(x, "provision")
    ssh = doc.get("ssh") or {}
    lg = doc["resolved"].get("vm.display_ref") or {}
    if not isinstance(lg, dict) or "idd_exclusive" not in lg or "mirror_displays" not in lg:
        raise PayloadError("windows.xbox.vm.display_ref: must resolve to [vm.looking_glass] (idd_exclusive, mirror_displays)")
    devs = guest_devices(x)
    need = {g: guest_device(x, f"guest_tools.{g}") for g in gt}
    idd_dev = guest_device(x, "drivers.looking_glass_idd")
    if not ssh.get("authorized_keys"):
        raise PayloadError("xbox-spec.json ssh: no operator public key (render it with render_spec.py)")
    desk = doc["users"]["desktop"]
    admin_acct = doc["users"].get("admin")
    w = doc.get("winrm") or {}
    state_dir = str(pv["state_dir"])
    prov = dest + "\\windows\\MiOS-Xbox.Provision.ps1"
    out: List[str] = [
        "# MiOS-Xbox SetupComplete stage (SYSTEM). Rendered by usr/libexec/mios/win/xbox/answer.py from xbox-spec.json.",
        "# Every value below is the spec's; re-render instead of editing. Results: setup-state.json + provision-state.json;",
        "# every log line also goes to the guest serial port the host logs ([vm.looking_glass].serial_log).",
        *_stage_header(doc, "setupcomplete", state_dir + "\\setup.log"),
        shared_directory_acl(doc, tree_root),
        f"$MiOS = {_ps(dest)}",
        f"$Inst = {_ps(inst_dir)}",
        f"$Fod = {_ps(dest + chr(92) + 'fod')}",
        f"$Prov = {_ps(prov)}",
        f"$Drv = {_ps(dest + chr(92) + 'drivers')}",
        f"$PsExe = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'",
        # This ACL is shared by every device shape. The IDD step may be inapplicable on bare metal.
        f"foreach ($d in @($MiOS, {_ps(state_dir)}) | Select-Object -Unique) {{ Protect-MiOSDir $d $true $false }}",
        f"New-Item -ItemType Directory -Force -Path {_ps(state_dir)}, $MiOSLogDir | Out-Null",
        "Write-MiOSLog ('SYSTEM stage start as {0}' -f (Get-MiOSAccount))",
        "",
        # -- guest devices (xbox-bm-01): probed once, before any guest-only step ----------------------------------
        "Invoke-MiOSStep 'environment_probe' {",
        "    # SSOT [windows.xbox.devices]. HypervisorPresent is recorded as a FACT only: MiOS's own nested_virt_features",
        "    # step makes it true on bare metal, so it never decides a step. A failed PCI probe runs every guest step.",
        "    $hv = $null; try { $hv = [bool](Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).HypervisorPresent } catch {}",
        "    $pci = @(); $probeOk = $true",
        "    try { $pci = @(Get-MiOSPciIds) } catch { $probeOk = $false; Write-MiOSLog \"PCI probe failed ($($_.Exception.Message)): every guest step runs\" }",
        "    $want = [ordered]@{ " + "; ".join(f"{_ps(n)} = @({', '.join(_ps(p) for p in d['pci'])})" for n, d in devs.items()) + " }",
        "    foreach ($k in $want.Keys) {",
        "        $hit = if ($probeOk) { Find-MiOSPciMatch $want[$k] $pci } else { $null }",
        "        $MiOSDevices[$k] = [ordered]@{ present = ((-not $probeOk) -or [bool]$hit); pci = @($want[$k]); matched = $hit; probed = $probeOk }",
        "    }",
        "    $rec = [ordered]@{ schema = 'mios-xbox-environment/1'; at = [DateTime]::UtcNow.ToString('o'); hypervisor_present = $hv",
        "                       probe_ok = $probeOk; pci_present = @($pci); devices = $MiOSDevices }",
        f"    [IO.File]::WriteAllText({_ps(x['devices']['record'])}, ($rec | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))",
        "    Write-MiOSLog ('devices: ' + (@($MiOSDevices.Keys | ForEach-Object { '{0}={1}' -f $_, $(if ($MiOSDevices[$_].present) { 'present' } else { 'absent' }) }) -join ', ') + \"; HypervisorPresent=$hv (fact only)\")",
        "}",
        # -- guest tools: one verified MSI each (fix-a 9), each gated on its device (xbox-bm-01) --------------------
        f"Invoke-MiOSGuestStep 'virtio_win_guest_tools' {_ps(need['virtio_win_guest_tools'])} {{",
        f"    $f = Join-Path $Inst {_ps(names['virtio_guest_tools'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['virtio_guest_tools']['sha256'])}",
        *_signed("$f", ins["virtio_guest_tools"]),
        "    Invoke-MiOSMsi $f 'virtio-win-gt'",
        "}",
        f"Invoke-MiOSGuestStep 'qemu_ga' {_ps(need['qemu_ga'])} {{",
        f"    $f = Join-Path $Inst {_ps(names['qemu_ga'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['qemu_ga']['sha256'])}",
        *_signed("$f", ins["qemu_ga"]),
        "    Start-MiOSDtc",
        f"    try {{ Invoke-MiOSMsi $f 'qemu-ga' }} catch {{ Add-MiOSRetry 'qemu-ga' $f {_ps(ins['qemu_ga']['sha256'])} {_ps(gt['qemu_ga']['service'])}; throw }}",
        f"    Start-MiOSService {_ps(gt['qemu_ga']['service'])}",
        "}",
        f"Invoke-MiOSGuestStep 'spice_vdagent' {_ps(need['spice_vdagent'])} {{",
        f"    $f = Join-Path $Inst {_ps(names['spice_vdagent'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['spice_vdagent']['sha256'])}",
        *_signed("$f", ins["spice_vdagent"]),
        f"    try {{ Invoke-MiOSMsi $f 'spice-vdagent' }} catch {{ Add-MiOSRetry 'spice-vdagent' $f {_ps(ins['spice_vdagent']['sha256'])} {_ps(gt['spice_vdagent']['service'])}; throw }}",
        f"    Start-MiOSService {_ps(gt['spice_vdagent']['service'])}",
        "}",
        "Invoke-MiOSStep 'virtio_drivers' {",
        "    # offline-injected into install.wim; proven AFTER every MSI step (an MSI rollback can uninstall driver",
        "    # packages) and repaired from the verified tree the image carries (PnPUtil /add-driver /subdirs /install)",
        "    $want = @(" + ", ".join(_ps(str(i).lower()) for i in _req(x, "drivers.virtio_win.inf")) + ")",
        "    $have = @(Get-WindowsDriver -Online | ForEach-Object { [IO.Path]::GetFileNameWithoutExtension([string]$_.OriginalFileName).ToLowerInvariant() })",
        "    $missing = @($want | Where-Object { $have -notcontains $_ })",
        "    if ($missing.Count) {",
        "        Write-MiOSLog \"virtio driver packages missing ($($missing -join ', ')): re-adding from $Drv\"",
        "        $o = & (Join-Path $env:SystemRoot 'System32\\pnputil.exe') /add-driver (Join-Path $Drv '*.inf') /subdirs /install",
        "        Write-MiOSLog ('pnputil exit {0}: {1}' -f $LASTEXITCODE, ((@($o) | Select-Object -Last 3) -join ' '))",
        "        $have = @(Get-WindowsDriver -Online | ForEach-Object { [IO.Path]::GetFileNameWithoutExtension([string]$_.OriginalFileName).ToLowerInvariant() })",
        "        $missing = @($want | Where-Object { $have -notcontains $_ })",
        "    }",
        "    if ($missing.Count) { throw \"virtio driver packages not in the driver store: $($missing -join ', ')\" }",
        "}",
        # Looking Glass only with IVSHMEM (xbox-bm-01): no IDD, no Duplicate topology on a machine without it. The mirror
        # task lives under \MiOS\ ([windows.xbox.provision].mirror_task); an earlier root-path one is removed.
        f"Invoke-MiOSGuestStep 'looking_glass_idd' {_ps(idd_dev)} {{",
        f"    $z = Join-Path $Inst {_ps(names['looking_glass_idd'])}",
        f"    & (Join-Path $MiOS 'mios-looking-glass-idd.ps1') -Url {_ps(ins['looking_glass_idd']['url'])} "
        f"-Sha256 {_ps(ins['looking_glass_idd']['sha256'])} -Version {_ps(ins['looking_glass_idd']['version'])} "
        f"-Signer {_ps('CN=' + str(ins['looking_glass_idd']['signer']))} -Package $z "
        f"-ExclusiveMonitor {1 if lg['idd_exclusive'] else 0} -MirrorDisplays {1 if lg['mirror_displays'] else 0} "
        f"-MirrorTask {_ps(pv['mirror_task'])} -LegacyMirrorTasks @({', '.join(_ps(t) for t in pv['legacy_mirror_tasks'])})",
        "}",
    ]
    if gt["looking_glass_host"].get("install", True):
        # exactly one Looking Glass server per IVSHMEM device (xbox-bm-37): rendered only when the SSOT selects the
        # legacy host app (render_spec refuses that while the IDD is installed)
        out += [
            f"Invoke-MiOSGuestStep 'looking_glass_host' {_ps(need['looking_glass_host'])} {{",
            f"    $z = Join-Path $Inst {_ps(names['looking_glass_host'])}",
            f"    Assert-MiOSSha256 $z {_ps(ins['looking_glass_host']['sha256'])}",
            "    $d = Join-Path $env:ProgramData 'MiOS\\looking-glass\\host'",
            "    Expand-Archive -Force -LiteralPath $z -DestinationPath $d",
            "    $exe = Get-ChildItem -Path $d -Recurse -Filter 'looking-glass-host-setup.exe' | Select-Object -First 1",
            "    if (-not $exe) { throw 'looking-glass-host-setup.exe not in the host artifact' }",
            *_signed("$exe.FullName", ins["looking_glass_host"], required=True),
            "    Invoke-MiOSInstaller $exe.FullName @('/S')",
            f"    Start-MiOSService {_ps(gt['looking_glass_host']['service'])}",
            "}",
        ]
    out += [
        f"Invoke-MiOSGuestStep 'winfsp' {_ps(need['winfsp'])} {{",
        f"    $f = Join-Path $Inst {_ps(names['winfsp'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['winfsp']['sha256'])}",
        "    Invoke-MiOSMsi $f 'winfsp'",
        "}",
        f"Invoke-MiOSGuestStep 'virtiofs' {_ps(need['virtiofs'])} {{",
        f"    $svc = Get-Service -Name {_ps(gt['virtiofs']['service'])} -ErrorAction SilentlyContinue",
        "    if (-not $svc) {",
        "        $exe = Get-ChildItem -Path (Join-Path $env:ProgramFiles 'Virtio-Win') -Recurse -Filter 'virtiofs.exe' -ErrorAction SilentlyContinue | Select-Object -First 1",
        "        if (-not $exe) { throw 'virtiofs.exe not installed by the virtio-win-gt MSI' }",
        f"        New-Service -Name {_ps(gt['virtiofs']['service'])} -BinaryPathName ('\"{{0}}\"' -f $exe.FullName) "
        f"-StartupType Automatic -DependsOn 'WinFsp.Launcher' -DisplayName 'Virtio FS Service' | Out-Null",
        "    }",
        f"    Set-Service -Name {_ps(gt['virtiofs']['service'])} -StartupType Automatic",
        "}",
        "Invoke-MiOSStep 'webview2' {",
        f"    $f = Join-Path $Inst {_ps(names['webview2'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['webview2']['sha256'])}",
        *_signed("$f", ins["webview2"], required=True),
        "    Invoke-MiOSInstaller $f @('/silent', '/install')",
        "}",
        # the MiOS Windows shell (xbox-bm-39): PowerShell 7 for every profile; its files are staged by the payload
        "Invoke-MiOSStep 'shell_pwsh' {",
        f"    $f = Join-Path $Inst {_ps(names['pwsh'])}",
        f"    Assert-MiOSSha256 $f {_ps(ins['pwsh']['sha256'])}",
        *_signed("$f", ins["pwsh"], required=True),
        f"    Invoke-MiOSMsi $f 'pwsh' @({', '.join(_ps(p) for p in _req(x, 'shell.pwsh.msi_properties'))})",
        f"    if (-not (Test-Path -LiteralPath {_ps('C:' + chr(92) + _shell_tools(doc)['pwsh']['exe'].replace('/', chr(92)))})) "
        "{ throw 'pwsh.exe is absent after the PowerShell 7 MSI' }",
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
        # -- network profile before WinRM (fix-a 9): a Public profile blocks the WinRM firewall exception -----------
        "Invoke-MiOSStep 'network_private' {",
        f"    $deadline = [DateTime]::UtcNow.AddSeconds({int(pv['network_wait_s'])})",
        "    do { $np = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue); if ($np.Count) { break }; Start-Sleep -Seconds 5 } while ([DateTime]::UtcNow -lt $deadline)",
        f"    if (-not $np.Count) {{ throw 'no network profile identified within {int(pv['network_wait_s'])} s' }}",
        f"    foreach ($c in $np) {{ if ([string]$c.NetworkCategory -eq 'Public') {{ Set-NetConnectionProfile -InterfaceIndex $c.InterfaceIndex -NetworkCategory {_ps(pv['network_category'])} }} }}",
        "    $pub = @(Get-NetConnectionProfile | Where-Object { [string]$_.NetworkCategory -eq 'Public' })",
        "    if ($pub.Count) { throw \"network profile still Public: $(@($pub | ForEach-Object { $_.InterfaceAlias }) -join ', ')\" }",
        "}",
    ]
    if w.get("enable"):
        port = int(w["port"])
        out += [
            "Invoke-MiOSStep 'winrm' {",
            "    try { Enable-PSRemoting -SkipNetworkProfileCheck -Force | Out-Null }",
            "    catch {",
            "        # e.g. a profile still Public after network_private, or WinRM quickconfig refusing in this context: configure",
            "        # the service + HTTP listener directly; the MiOS rule below is the firewall exception (any profile, scoped)",
            "        Write-MiOSLog \"Enable-PSRemoting failed ($($_.Exception.Message)): configuring the WinRM service and listener directly\"",
            "        Set-Service -Name 'WinRM' -StartupType Automatic",
            "        if ((Get-Service -Name 'WinRM').Status -ne 'Running') { Start-Service -Name 'WinRM' }",
            "        if (-not (Get-ChildItem WSMan:\\localhost\\Listener | Where-Object { $_.Keys -contains 'Transport=HTTP' })) {",
            "            New-Item -Path WSMan:\\localhost\\Listener -Transport HTTP -Address * -Force | Out-Null",
            "        }",
            "    }",
            "    Set-Item -Path WSMan:\\localhost\\Service\\Auth\\Basic -Value $false",
            "    Set-Item -Path WSMan:\\localhost\\Service\\AllowUnencrypted -Value $false",
            "    $l = Get-ChildItem WSMan:\\localhost\\Listener | Where-Object { $_.Keys -contains 'Transport=HTTP' } | Select-Object -First 1",
            "    if (-not $l) { throw 'no WinRM HTTP listener' }",
            f"    if ((Get-Item (Join-Path $l.PSPath 'Port')).Value -ne '{port}') {{ Set-Item -Path (Join-Path $l.PSPath 'Port') -Value {port} -Force }}",
            "    Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule",
            "    Remove-NetFirewallRule -Name 'MiOS-WinRM-HTTP' -ErrorAction SilentlyContinue",
            f"    New-NetFirewallRule -Name 'MiOS-WinRM-HTTP' -DisplayName 'MiOS WinRM (host probe)' -Direction Inbound -Protocol TCP "
            f"-LocalPort {port} -RemoteAddress {_ps(w['remote_address'])} -Profile Any -Action Allow | Out-Null",
            "    Start-MiOSService 'WinRM'",
            f"    Wait-MiOSListen {port} 'WinRM'",
            "}",
        ]
    feats = _req(x, "features")
    fod = _req(x, "features.capability_fod")
    for cap in feats.get("capabilities_add") or []:
        proof = str(fod[cap]["proof"]).lstrip("\\/")
        # Baked offline by New-MiOSXboxImage (DISM /Add-Capability /Source /LimitAccess); here only proven, or
        # installed from the FOD staged in the image -- never from Windows Update (0x8024402c offline).
        out += [f"Invoke-MiOSStep 'capability:{cap}' {{",
                f"    $c = Get-WindowsCapability -Online -Name {_ps(str(cap) + '*')} | Select-Object -First 1",
                f"    if (-not $c) {{ throw 'capability {cap} is not offered by this build' }}",
                "    if ($c.State -ne 'Installed') {",
                f"        Write-MiOSLog 'capability {cap} was not baked offline: installing it from the staged FOD (LimitAccess)'",
                f"        Add-WindowsCapability -Online -Name (Get-WindowsCapability -Online -Name {_ps(str(cap) + '*')} | Select-Object -First 1).Name -Source $Fod -LimitAccess | Out-Null",
                "    }",
                f"    $proof = Join-Path $env:SystemDrive {_ps(proof)}",
                f"    if (-not (Test-Path -LiteralPath $proof)) {{ throw \"capability {cap}: $proof is absent\" }}",
                "}"]
    out += [
        "Invoke-MiOSStep 'ssh_setup' {",
        "    # the inbox (FOD) sshd configured from the SSOT inputs the image carries: no download, no GitHub",
        f"    & (Join-Path $MiOS 'mios-ssh-setup.ps1') -SettingsPath {_ps(ssh['settings_path'])} -AuthorizedKeysPath {_ps(ssh['authorized_keys_path'])} -InboxOpenSsh",
        "}",
        "Invoke-MiOSStep 'sshd_service' {",
        "    Start-MiOSService 'sshd'",
        "}",
        # RDP (F5, reconcile): fDenyTSConnections=0 is baked offline; this step proves it, scopes + enables the inbox
        # rules on every profile (a Public profile must not hide 3389) and requires the listener -- never a false ok
        "Invoke-MiOSStep 'rdp' {",
        "    $ts = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server'",
        "    if ([int](Get-ItemProperty -Path $ts).fDenyTSConnections -ne 0) { throw 'fDenyTSConnections is not 0: Remote Desktop is off' }",
        "    $rdpPort = [int](Get-ItemProperty -Path ($ts + '\\WinStations\\RDP-Tcp')).PortNumber",
        "    $rules = @(Get-NetFirewallRule -Name 'RemoteDesktop-UserMode-In-TCP', 'RemoteDesktop-UserMode-In-UDP' -ErrorAction SilentlyContinue)",
        "    if (-not @($rules | Where-Object { $_.Name -eq 'RemoteDesktop-UserMode-In-TCP' }).Count) { throw 'inbox firewall rule RemoteDesktop-UserMode-In-TCP is absent' }",
        "    foreach ($r in $rules) { Set-NetFirewallRule -Name $r.Name -Enabled True -Profile Any -RemoteAddress @('LocalSubnet', '100.64.0.0/10') }",
        "    if ((Get-Service -Name 'TermService').Status -ne 'Running') { Start-Service -Name 'TermService' }",
        "    Wait-MiOSListen $rdpPort 'Remote Desktop'",
        "}",
    ]
    # -- MiOS componentry (requirement 13, fix-a 4): wallpaper page + URL, services, tasks ---------------------
    wp = _req(x, "wallpaper")
    comps = {c["name"]: c for c in _req(x, "components.ship")}
    wp_script = os.path.basename(comps[wp["setup_component"]]["source"])
    wp_page = str(doc["resolved"]["wallpaper.target_ref"]).replace("/", "\\")
    out += [
        "Invoke-MiOSStep 'wallpaper_setup' {",
        f"    if (-not (Test-Path -LiteralPath {_ps(wp_page)})) {{ throw {_ps(wp_page + ' is not in the image')} }}",
        f"    & (Join-Path $MiOS {_ps(wp_script)})",
        "}",
    ]
    for name, sv in _req(x, "services").items():
        bin_path, exe = service_binpath(doc, name, tree_root)
        out += [
            f"Invoke-MiOSStep {_ps('service:' + name)} {{",
            f"    if (-not (Test-Path -LiteralPath {_ps(exe)})) {{ throw {_ps(exe + ' is not in the image')} }}",
            f"    if (-not (Get-Service -Name {_ps(name)} -ErrorAction SilentlyContinue)) {{",
            f"        New-Service -Name {_ps(name)} -BinaryPathName {_ps(bin_path)} -DisplayName {_ps(sv.get('display') or name)} -StartupType Automatic | Out-Null",
            "    }",
            f"    Start-MiOSService {_ps(name)}",
            "}",
        ]
    for name, tk in _req(x, "tasks").items():
        comp = comps[tk["component"]]
        target = dest + "\\" + (f"{comp['name']}.exe" if comp["kind"] == "native" else os.path.basename(comp["source"]))
        port_arg = ""
        if tk.get("port_ref"):
            pr = doc["resolved"].get(f"tasks.{name}.port_ref")
            if isinstance(pr, bool) or not isinstance(pr, int) or not 0 < pr < 65536:
                raise PayloadError(f"windows.xbox.tasks.{name}.port_ref: must resolve to a TCP port")
            port_arg = f" -Port {pr}"
        if target.lower().endswith(".ps1"):
            action = (f"    $a = New-ScheduledTaskAction -Execute $PsExe -Argument "
                      f"('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{{0}}\"{port_arg}' -f {_ps(target)})")
        else:
            action = f"    $a = New-ScheduledTaskAction -Execute {_ps(target)}" + (f" -Argument {_ps(port_arg.strip())}" if port_arg else "")
        if tk["run_as"] == "system":
            principal = "    $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest"
        else:
            principal = f"    $p = New-ScheduledTaskPrincipal -UserId {_ps(desk['name'])} -LogonType Interactive -RunLevel Limited"
        trigger = ("    $t = New-ScheduledTaskTrigger -AtStartup" if tk["trigger"] == "startup"
                   else f"    $t = New-ScheduledTaskTrigger -AtLogOn -User {_ps(desk['name'])}")
        out += [
            f"Invoke-MiOSStep {_ps('task:' + name)} {{",
            f"    if (-not (Test-Path -LiteralPath {_ps(target)})) {{ throw {_ps(target + ' is not in the image')} }}",
            action, trigger, principal,
            "    $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)",
            f"    Register-ScheduledTask -TaskPath '\\MiOS\\' -TaskName {_ps(name)} -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null",
        ]
        if port_arg and tk["run_as"] == "desktop":
            url = f"http://127.0.0.1:{port_arg.split()[-1]}/"
            # the loopback listener needs a URL ACL for a non-admin user (the server says so when it cannot bind)
            out += [f"    try {{ & netsh.exe http delete urlacl url={url} | Out-Null }} catch {{}}",
                    f"    $o = & netsh.exe http add urlacl url={url} user={_ps(desk['name'])}",
                    f"    if ($LASTEXITCODE) {{ throw ('netsh http add urlacl {url} exited ' + $LASTEXITCODE + ': ' + ($o -join ' ')) }}"]
        if tk["run_as"] == "system" and tk["trigger"] == "startup":
            out.append(f"    Start-ScheduledTask -TaskPath '\\MiOS\\' -TaskName {_ps(name)}")
        out.append("}")
    nested = list(feats.get("enable_if_nested_virt") or [])
    if nested:
        out += ["Invoke-MiOSStep 'nested_virt_features' {",
                "    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1",
                "    if ($cpu -and [bool]$cpu.VirtualizationFirmwareEnabled) {"]
        for f in nested:
            out.append(f"        Enable-WindowsOptionalFeature -Online -FeatureName {_ps(f)} -All -NoRestart | Out-Null")
        out += ["    } else { Write-MiOSLog 'no nested virtualization exposed: conditional features stay off' }", "}"]
    pw = _req(x, "settings.power")
    out += ["Invoke-MiOSStep 'power' {",
            f"    powercfg.exe /setactive {pw['scheme']}",
            f"    powercfg.exe /change monitor-timeout-ac {int(pw['monitor_timeout_ac_min'])}",
            f"    powercfg.exe /change standby-timeout-ac {int(pw['standby_timeout_ac_min'])}"]
    if not pw["hibernate"]:
        out.append("    powercfg.exe /hibernate off")
    out.append("}")
    rp, rn = _task_path(pv["retry_task"])
    out += ["Invoke-MiOSStep 'guest_tools_retry' {",
            f"    $q = {_ps(state_dir + chr(92) + 'retry.json')}",
            "    if (-not $script:MiOSRetry.Count) { Remove-Item -LiteralPath $q -ErrorAction SilentlyContinue; return }",
            "    [IO.File]::WriteAllText($q, (ConvertTo-Json -InputObject @($script:MiOSRetry) -Depth 3), [Text.UTF8Encoding]::new($false))",
            "    $a = New-ScheduledTaskAction -Execute $PsExe -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{0}\" -Stage Retry' -f $Prov)",
            "    $t = New-ScheduledTaskTrigger -AtStartup; $t.Delay = 'PT1M'",
            # and once a few minutes after this stage ends: SetupComplete still runs inside Windows Setup, the
            # task runs after it (no reboot needed for qemu-ga / the SPICE agent on the first boot)
            "    $t2 = New-ScheduledTaskTrigger -Once -At ([DateTime]::Now.AddMinutes(3))",
            "    $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest",
            "    $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)",
            f"    Register-ScheduledTask -TaskPath {_ps(rp)} -TaskName {_ps(rn)} -Action $a -Trigger @($t, $t2) -Principal $p -Settings $s -Force | Out-Null",
            "    Write-MiOSLog \"guest tools queued for the startup retry: $(@($script:MiOSRetry | ForEach-Object { $_.name }) -join ', ')\"",
            "}"]
    # -- formerly FirstLogonCommands of the (standard) desktop user: now SYSTEM (fix-a 1) ---------------------
    hyd = _req(x, "store_hydrate")
    if hyd["packages"]:
        if hyd["stage"] != "first_logon":
            raise PayloadError("windows.xbox.store_hydrate.stage: only first_logon is implemented")
        hp, hn = _task_path(pv["hydrate_task"])
        out += ["Invoke-MiOSStep 'store_hydrate_task' {",
                "    # Store installs are per user: the task runs AS the desktop user at its logon; SYSTEM only registers it",
                "    $a = New-ScheduledTaskAction -Execute $PsExe -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{0}\" -Stage Hydrate' -f $Prov)",
                f"    $t = New-ScheduledTaskTrigger -AtLogOn -User {_ps(desk['name'])}",
                f"    $p = New-ScheduledTaskPrincipal -UserId {_ps(desk['name'])} -LogonType Interactive -RunLevel Limited",
                "    $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)",
                f"    Register-ScheduledTask -TaskPath {_ps(hp)} -TaskName {_ps(hn)} -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null",
                "}"]
    vp, vn = _task_path(pv["verify_task"])
    out += ["Invoke-MiOSStep 'verify_task' {",
            "    $a = New-ScheduledTaskAction -Execute $PsExe -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{0}\" -Stage Verify' -f $Prov)",
            "    $t1 = New-ScheduledTaskTrigger -AtStartup; $t1.Delay = 'PT2M'",
            f"    $t2 = New-ScheduledTaskTrigger -AtLogOn -User {_ps(desk['name'])}; $t2.Delay = 'PT5M'",
            "    $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest",
            "    $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)",
            f"    Register-ScheduledTask -TaskPath {_ps(vp)} -TaskName {_ps(vn)} -Action $a -Trigger @($t1, $t2) -Principal $p -Settings $s -Force | Out-Null",
            "}"]
    # Persistent AutoLogon (round-1 boot of build-20261023T224037Z): at SetupComplete Winlogon still holds OOBE's own
    # autologon (DefaultUserName=defaultuser0, no AutoLogonCount); CloudExperienceHostBroker transfers the answer-file
    # AutoLogon (DefaultUserName + LogonCount) at the FIRST desktop sign-in, after this stage. The SYSTEM Verify task
    # registered above (startup + desktop logon) deletes AutoLogonCount then; this step does it now only if Windows
    # already transferred it, and otherwise requires that task (never a deferral nobody performs).
    if desk.get("autologon") and desk.get("autologon_persistent"):
        out += ["Invoke-MiOSStep 'autologon_persistent' {",
                "    $wl = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon'",
                "    $v = Get-ItemProperty -Path $wl",
                "    if ([string]$v.AutoAdminLogon -ne '1') { throw 'Winlogon AutoAdminLogon is not 1 (no AutoLogon is configured)' }",
                f"    if ([string]$v.DefaultUserName -eq {_ps(desk['name'])}) {{",
                "        Remove-ItemProperty -Path $wl -Name 'AutoLogonCount' -ErrorAction SilentlyContinue",
                "        if (@((Get-ItemProperty -Path $wl).PSObject.Properties.Name) -contains 'AutoLogonCount') { throw 'Winlogon AutoLogonCount is still set' }",
                "    } else {",
                f"        if (-not (Get-ScheduledTask -TaskPath {_ps(vp)} -TaskName {_ps(vn)} -ErrorAction SilentlyContinue)) {{ throw 'the SYSTEM Verify task that makes AutoLogon persistent after the first sign-in is not registered' }}",
                f"        Write-MiOSLog (\"Winlogon still holds the OOBE autologon ({{0}}): Windows transfers the AutoLogon of {desk['name']} at the first sign-in; the SYSTEM Verify task {vn} then deletes AutoLogonCount\" -f $v.DefaultUserName)",
                "    }",
                "}"]
    # Microsoft ("Windows Setup Automation Overview", Sensitive Data in Answer Files): the answer file is cached in
    # %WINDIR%\Panther and must be deleted before the PC is handed over; SetupComplete.cmd may delete it. Every pass
    # (oobeSystem included) has run when SetupComplete runs, and ours holds the SSOT account passwords in plain text.
    out += ["Invoke-MiOSStep 'answer_file_cache' {",
            "    $left = @()",
            "    foreach ($rel in @('Panther\\unattend.xml', 'Panther\\unattend-original.xml', 'Panther\\autounattend.xml', 'System32\\Sysprep\\unattend.xml')) {",
            "        $p = Join-Path $env:SystemRoot $rel",
            "        if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force; if (Test-Path -LiteralPath $p) { $left += $p } else { Write-MiOSLog \"removed the cached answer file $p\" } }",
            "    }",
            "    if ($left.Count) { throw \"cached answer file(s) still present: $($left -join ', ')\" }",
            "}"]
    b = doc["users"]["builtin_administrator"]
    if b.get("disable_at") == "finalize":
        target_admin = admin_acct["name"] if admin_acct else desk["name"]
        out += ["Invoke-MiOSStep 'finalize_rid500' {",
                "    # last privileged change of the SYSTEM stage: the elevation admin is proven first",
                f"    $me = Get-LocalUser -Name {_ps(target_admin)}",
                "    if (-not $me.Enabled) { throw 'the elevation admin is disabled; RID 500 stays enabled' }",
                f"    if (-not (Get-LocalGroupMember -SID 'S-1-5-32-544' -Member {_ps(target_admin)} -ErrorAction SilentlyContinue)) {{ throw 'the elevation admin is not in Administrators; RID 500 stays enabled' }}",
                "    Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' } | Disable-LocalUser",
                "    if (@(Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' -and $_.Enabled }).Count) { throw 'RID 500 is still enabled' }",
                "}"]
    elif b.get("disable_at"):
        raise PayloadError("windows.xbox.users.builtin_administrator.disable_at: only finalize is implemented")
    out += [f"Save-MiOSState {_ps(state_dir + chr(92) + 'setup-state.json')}",
            "try { & $Prov -Stage Verify | ForEach-Object { Write-MiOSLog $_ } } catch { Write-MiOSLog \"Verify FAILED: $($_.Exception.Message)\" }",
            "Write-MiOSLog 'SYSTEM stage done'",
            ""]
    return "\n".join(out)


def render_setupcomplete_cmd(doc: Optional[Dict[str, Any]] = None) -> str:
    """SetupComplete.cmd (Microsoft: runs with local system permissions after setup; Setup does not check its exit
    code). The two echo lines reach the serial port through cmd's device redirection; the PowerShell stage mirrors
    every line to the same port with System.IO.Ports.SerialPort."""
    port = "COM1"
    if doc is not None:
        port = str(_req(doc["spec"], "provision.serial_port"))
    return "\r\n".join([
        "@echo off",
        "rem MiOS-Xbox SetupComplete (rendered by usr/libexec/mios/win/xbox/answer.py; runs as SYSTEM before first logon)",
        f'echo [SetupComplete] Starting provisioning > {port} 2>nul',
        'if not exist "%ProgramData%\\MiOS" mkdir "%ProgramData%\\MiOS"',
        f'"{PS_EXE}" -NoProfile -ExecutionPolicy Bypass -File "%SystemRoot%\\Setup\\Scripts\\{SETUP_SCRIPT}" >> "%ProgramData%\\MiOS\\setupcomplete.log" 2>&1',
        f'echo [SetupComplete] Provisioning complete (exit %ERRORLEVEL%) > {port} 2>nul',
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


def fod_cab_name(package: str, arch: str) -> str:
    """Microsoft's FOD repository name of a capability package (OpenSSH-Server-Package~31bf3856ad364e35~amd64~~.cab)."""
    return f"{package}~{PKT}~{arch}~~.cab"


def stage_extras(doc: Dict[str, Any], inputs: Dict[str, Any], out_dir: str, tree_root: str) -> List[Dict[str, Any]]:
    """Payload the SYSTEM stage needs beside the components (fix-a M1), each hashed into payload.json so the
    worker verifies it: the SSH inputs from the SSOT (F4: no GitHub at provisioning), the FOD the capability
    fallback installs from (LimitAccess), the living-wallpaper page at the URL Set-MiOSWallpaper writes, and the
    MiosServiceTool host copy + .cfg for every service the SSOT hosts that way."""
    x = doc["spec"]
    dest = _win_rel(_req(x, "components.dest_dir"))
    rows: List[Dict[str, Any]] = []

    def put(name: str, rel: str, data: Optional[bytes] = None, src: Optional[str] = None) -> None:
        target = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if src is not None:
            shutil.copyfile(src, target)
            os.chmod(target, 0o644)
        else:
            _write(target, data or b"")
        rows.append({"name": name, "path": rel, "sha256": _sha256(target)})

    ssh = doc.get("ssh") or {}
    if not ssh.get("authorized_keys") or not ssh.get("windows"):
        raise PayloadError("xbox-spec.json ssh: missing (render it with render_spec.py; F4 inputs come from the SSOT)")
    settings = {"port": int(ssh["port"]), "windows": ssh["windows"]}
    put("ssh-settings", _win_rel(ssh["settings_path"]), (json.dumps(settings, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    put("ssh-authorized-keys", _win_rel(ssh["authorized_keys_path"]), ("\n".join(ssh["authorized_keys"]) + "\n").encode("ascii"))

    fod_dir = inputs.get("fod_dir")
    caps = list(_req(x, "features.capabilities_add") or [])
    if caps:
        if not fod_dir or not os.path.isdir(fod_dir):
            raise PayloadError("build input fod_dir: the capability FOD set is not fetched/verified (inputs.json)")
        arch = str(_req(x, "source.arch"))
        fod_map = _req(x, "features.capability_fod")   # keys hold dots (OpenSSH.Server): no dotted lookup
        missing = [c for c in caps if not isinstance(fod_map.get(c), dict) or not fod_map[c].get("package")]
        if missing:
            raise PayloadError(f"windows.xbox.features.capability_fod: no FOD package for {missing}")
        want = [fod_cab_name(fod_map[c]["package"], arch) for c in caps]
        want.append(str(_req(x, "features.fod_metadata")))
        have = {n.lower(): n for n in os.listdir(fod_dir)}
        for w in want:
            if w.lower() not in have:
                raise PayloadError(f"build input fod_dir: {w} missing (resolve + fetch stage it from the UUP update)")
            put(f"fod:{w}", f"{dest}/fod/{have[w.lower()]}", src=os.path.join(fod_dir, have[w.lower()]))

    drv = inputs.get("drivers_dir")
    if not drv or not os.path.isdir(drv):
        raise PayloadError("build input drivers_dir: the verified virtio driver tree is not staged (inputs.json)")
    for d, _dirs, files in sorted(os.walk(drv)):
        for fn in sorted(files):
            rel = os.path.relpath(os.path.join(d, fn), drv).replace(os.sep, "/")
            put(f"driver:{rel}", f"{dest}/drivers/{rel}", src=os.path.join(d, fn))

    wp = _req(x, "wallpaper")
    html = str(doc["resolved"]["wallpaper.html_ref"])
    src = os.path.join(tree_root, html.lstrip("/"))
    if not os.path.isfile(src):
        raise PayloadError(f"windows.xbox.wallpaper.html_ref: {html} missing in the MiOS tree")
    put("wallpaper-page", _win_rel(str(doc["resolved"]["wallpaper.target_ref"]).replace("/", "\\")), src=src)
    if wp["setup_component"] not in {c["name"] for c in _req(x, "components.ship")}:
        raise PayloadError("windows.xbox.wallpaper.setup_component: not a components.ship entry")

    comps = {c["name"]: c for c in _req(x, "components.ship")}
    for name, sv in _req(x, "services").items():
        if sv.get("host") != "MiosServiceTool":
            continue
        tool = comps.get("MiosServiceTool")
        if not tool:
            raise PayloadError(f"windows.xbox.services.{name}: MiosServiceTool is not shipped")
        tool_rel = f"{dest}/{os.path.basename(tool['source'])}"
        if not os.path.isfile(os.path.join(out_dir, tool_rel)):
            raise PayloadError(f"windows.xbox.services.{name}: {tool_rel} not staged")
        comp = comps[sv["component"]]
        exe = str(_req(x, "components.dest_dir")) + "\\" + (f"{comp['name']}.exe" if comp["kind"] == "native" else os.path.basename(comp["source"]))
        host = str(sv["host_name"])
        put(f"service-host:{name}", f"{dest}/{host}.exe", src=os.path.join(out_dir, tool_rel))
        # MiosServiceTool reads <its exe>.cfg: line 1 the program, line 2 its arguments
        put(f"service-host-cfg:{name}", f"{dest}/{host}.cfg", (f"{exe}\r\n{sv.get('args') or ''}\r\n").encode("utf-8"))
    return rows


# ---------------------------------------------------------------------------
# whole payload
# ---------------------------------------------------------------------------

def parse_cursor_inf(text: str) -> Dict[str, Any]:
    """A cursor build's install.inf: SCHEME_NAME, CUR_DIR, the [Wreg] Control Panel\\Cursors role -> file map and the
    [Scheme.Reg] file order (the contract [theme.cursor_windows] declares; xbox-bm-39)."""
    sections: Dict[str, List[str]] = {}
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            cur = m.group(1)
            sections.setdefault(cur, [])
        elif cur:
            sections[cur].append(line)
    strings: Dict[str, str] = {}
    for line in sections.get("Strings", []):
        m = re.match(r'^([^=]+?)\s*=\s*"(.*)"$', line)
        if m:
            strings[m.group(1).strip().lower()] = m.group(2)

    def sub(v: str) -> str:
        return re.sub(r"%([^%]+)%", lambda mm: strings.get(mm.group(1).lower(), mm.group(0)), v)

    roles: Dict[str, str] = {}
    for line in sections.get("Wreg", []):
        parts = [p.strip() for p in line.split(",", 4)]
        if len(parts) == 5 and parts[0] == "HKCU" and parts[1].strip('"') == "Control Panel\\Cursors" and parts[2]:
            roles[parts[2]] = sub(parts[4].strip('"')).split("\\")[-1]
    order: List[str] = []
    for line in sections.get("Scheme.Reg", []):
        parts = line.split(",", 4)
        if len(parts) == 5:
            order = [sub(p).split("\\")[-1] for p in parts[4].strip().strip('"').split(",")]
    return {"scheme_name": strings.get("scheme_name", ""), "cur_dir": strings.get("cur_dir", ""), "roles": roles, "order": order}


def stage_identity(doc: Dict[str, Any], inputs: Dict[str, Any], out_dir: str, tree_root: str = _TREE) -> List[Dict[str, Any]]:
    """MiOS identity files into the image (xbox-bm-39): the fetched cursor build checked against its own install.inf, and the
    SSOT font family from the vendored archive. Returns payload rows {name, path, sha256}; any drift refuses the payload."""
    idn = doc.get("identity")
    if not isinstance(idn, dict) or "cursor" not in idn or "font" not in idn:
        raise PayloadError("xbox-spec.json has no identity record (render it with render_spec.py)")
    rows: List[Dict[str, Any]] = []
    cur = idn["cursor"]
    arc = ((inputs.get("identity") or {}).get("cursor_archive") or {})
    path = arc.get("path")
    if not path or not os.path.isfile(path):
        raise PayloadError("build input identity.cursor_archive: not fetched/verified (inputs.json)")
    want = str(arc.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", want) or _sha256(path) != want:
        raise PayloadError("cursor archive sha256 differs from the one fetch.py recorded (inputs.json)")
    if arc.get("asset") != cur.get("asset"):
        raise PayloadError(f"cursor archive {arc.get('asset')!r} is not the spec's {cur.get('asset')!r}")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        inf_name = f"{cur['variant_dir']}/install.inf"
        if inf_name not in names:
            raise PayloadError(f"cursor archive has no {inf_name}")
        inf = parse_cursor_inf(z.read(inf_name).decode("utf-8-sig", "replace"))
        problems = []
        if inf["scheme_name"] != cur["scheme_name"]:
            problems.append(f"SCHEME_NAME {inf['scheme_name']!r}")
        if inf["cur_dir"].replace("/", "\\").strip("\\") != cur["install_dir"]:
            problems.append(f"CUR_DIR {inf['cur_dir']!r}")
        if inf["order"] != list(cur["scheme_files"]):
            problems.append("the scheme file order")
        if {k.lower(): v for k, v in inf["roles"].items()} != {k.lower(): v for k, v in cur["roles"].items()}:
            problems.append("the role map")
        if problems:
            raise PayloadError("cursor archive install.inf disagrees with [theme.cursor_windows]: " + "; ".join(problems))
        for f in cur["scheme_files"]:
            member = f"{cur['variant_dir']}/{f}"
            if member not in names:
                raise PayloadError(f"cursor file {f} is missing from {cur['variant_dir']}")
            data = z.read(member)
            rel = f"{cur['dest_dir']}/{f}"
            _write(render_spec.checked_path(out_dir, rel, "identity.cursor destination"), data)
            rows.append({"name": f"cursor:{f}", "path": rel, "sha256": hashlib.sha256(data).hexdigest()})
    font = idn["font"]
    arch = render_spec.checked_path(tree_root, font["archive"], "identity.font archive")
    if not os.path.isfile(arch) or _sha256(arch) != str(font["archive_sha256"]).lower():
        raise PayloadError(f"{font['archive']} is absent or changed since the spec was rendered")
    with zipfile.ZipFile(arch) as z:
        for f in font["files"]:
            data = z.read(f["member"])
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise PayloadError(f"font {f['file']}: sha256 differs from the rendered spec")
            rel = f"{font['dest_dir']}/{f['file']}"
            _write(render_spec.checked_path(out_dir, rel, "identity.font destination"), data)
            rows.append({"name": f"font:{f['file']}", "path": rel, "sha256": f["sha256"]})
    # the theme's DesktopBackground: the solid SSOT background colour, rendered byte-identically to the spec's record
    bg = ((idn.get("theme") or {}).get("background") or {})
    if not bg.get("path") or not bg.get("sha256"):
        raise PayloadError("xbox-spec.json has no identity.theme.background (render it with render_spec.py)")
    data = render_spec.solid_bmp(render_spec._rgb(bg["color"], "identity.theme.background.color"), int(bg["px"]))
    if hashlib.sha256(data).hexdigest() != bg["sha256"]:
        raise PayloadError("theme background: the rendered image differs from the spec's sha256")
    _write(render_spec.checked_path(out_dir, bg["path"], "identity.theme destination"), data)
    rows.append({"name": "theme:background", "path": bg["path"], "sha256": bg["sha256"]})
    # the OEM logo: the MiOS mark from the tree, rendered byte-identically to the spec's record
    lg = idn.get("oem_logo") or {}
    if not lg.get("path") or not lg.get("sha256") or not lg.get("source"):
        raise PayloadError("xbox-spec.json has no identity.oem_logo (render it with render_spec.py)")
    mark = render_spec.checked_path(tree_root, lg["source"], "identity.oem_logo source")
    if not os.path.isfile(mark) or _sha256(mark) != str(lg["source_sha256"]).lower():
        raise PayloadError(f"{lg['source']} is absent or changed since the spec was rendered")
    with open(mark, "rb") as fh:
        data = render_spec.oem_logo_bmp(fh.read(), int(lg["px"]), render_spec._rgb(lg["background"], "identity.oem_logo.background"))
    if hashlib.sha256(data).hexdigest() != lg["sha256"]:
        raise PayloadError("OEM logo: the rendered image differs from the spec's sha256")
    _write(render_spec.checked_path(out_dir, lg["path"], "identity.oem_logo destination"), data)
    rows.append({"name": "oem:logo", "path": lg["path"], "sha256": lg["sha256"]})
    return rows


def _shell_tools(doc: Dict[str, Any]) -> Dict[str, Any]:
    sh = doc.get("shell")
    if not isinstance(sh, dict) or not isinstance(sh.get("tools"), dict) or not isinstance(sh.get("files"), list):
        raise PayloadError("xbox-spec.json has no shell record (render it with render_spec.py)")
    return sh["tools"]


def stage_shell(doc: Dict[str, Any], inputs: Dict[str, Any], out_dir: str, tree_root: str = _TREE) -> List[Dict[str, Any]]:
    """The MiOS Windows shell into the image (xbox-bm-39): every rendered/tree file byte-identical to the spec's sha256,
    oh-my-posh and the fastfetch archive's files from the build inputs (each re-checked against its recorded sha256).
    PowerShell 7 is an installer (INSTALLER_ROLES), run by the SYSTEM stage. Returns payload rows {name, path, sha256}."""
    tools = _shell_tools(doc)
    rows: List[Dict[str, Any]] = []
    for f in doc["shell"]["files"]:
        try:
            data = render_spec.shell_file_bytes(f, tree_root)
        except (OSError, render_spec.SpecError) as e:
            raise PayloadError(f"shell file {f.get('name')}: {e}") from None
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise PayloadError(f"shell file {f['name']}: bytes differ from the rendered spec's sha256")
        _write(render_spec.checked_path(out_dir, f["path"], "shell file destination"), data)
        rows.append({"name": f"shell:{f['name']}", "path": f["path"], "sha256": f["sha256"]})
    shin = inputs.get("shell") or {}
    for tool in ("oh_my_posh", "fastfetch"):
        r = shin.get(tool) or {}
        if not r.get("path") or not os.path.isfile(r["path"]) or not r.get("sha256"):
            raise PayloadError(f"build input shell.{tool}: not fetched/verified (inputs.json)")
        if _sha256(r["path"]) != str(r["sha256"]).lower():
            raise PayloadError(f"build input shell.{tool}: sha256 differs from inputs.json (changed since the fetch)")
    with open(shin["oh_my_posh"]["path"], "rb") as fh:
        data = fh.read()
    _write(render_spec.checked_path(out_dir, tools["oh_my_posh"]["path"], "shell.oh_my_posh destination"), data)
    rows.append({"name": "shell:oh_my_posh", "path": tools["oh_my_posh"]["path"], "sha256": hashlib.sha256(data).hexdigest()})
    dest = tools["fastfetch"]["dest_dir"]
    with zipfile.ZipFile(shin["fastfetch"]["path"]) as z:
        members = [i for i in z.infolist() if not i.is_dir()]
        for i in members:   # judged on the RAW member name, before any folder is stripped
            fn = i.filename
            if (i.external_attr >> 16) & 0o170000 == 0o120000 or fn.startswith("/") or ":" in fn or "\\" in fn or \
                    any(p in ("", ".", "..") for p in fn.split("/")):
                raise PayloadError(f"build input shell.fastfetch: unsafe archive member {fn!r}")
        tops = {i.filename.split("/", 1)[0] for i in members}
        strip = len(tops) == 1 and all("/" in i.filename for i in members)   # one top-level folder: drop it
        for i in members:
            rel = i.filename.split("/", 1)[1] if strip else i.filename
            data = z.read(i)
            path = f"{dest}/{rel}"
            _write(render_spec.checked_path(out_dir, path, "shell.fastfetch destination"), data)
            rows.append({"name": f"shell:fastfetch/{rel}", "path": path, "sha256": hashlib.sha256(data).hexdigest()})
    if not any(r["path"] == tools["fastfetch"]["path"] for r in rows):
        raise PayloadError(f"build input shell.fastfetch: the archive has no {os.path.basename(tools['fastfetch']['path'])}")
    return rows


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
    _write(os.path.join(scripts, "SetupComplete.cmd"), render_setupcomplete_cmd(doc).encode("ascii"))
    _write(os.path.join(scripts, SETUP_SCRIPT), _crlf(render_setup_script(doc, inputs, tree_root)))
    state = os.path.join(out_dir, *STATE_DIR.split("/"))
    _write(os.path.join(state, "xbox-registry.reg"), render_registry(doc))
    _write(os.path.join(state, "xbox-spec.json"),
           (json.dumps(render_spec.guest_projection(doc), indent=2, sort_keys=True) + "\n").encode("utf-8"))
    comps = stage_components(doc, out_dir, tree_root, native_dir)
    comps += stage_extras(doc, inputs, out_dir, tree_root)
    names = installer_names(doc, inputs)
    inst_rel = _win_rel(str(_req(x, "components.dest_dir"))) + "/installers"
    inst_rows = {}
    for role in INSTALLER_ROLES:
        ins = _installer(inputs, role)
        rel = f"{inst_rel}/{names[role]}"
        os.makedirs(os.path.dirname(os.path.join(out_dir, rel)), exist_ok=True)
        shutil.copyfile(ins["path"], os.path.join(out_dir, rel))
        inst_rows[role] = {"path": rel, "sha256": ins["sha256"], "version": ins.get("version", ""), "signer": ins.get("signer", "")}
    ident_rows = stage_identity(doc, inputs, out_dir, tree_root)
    shell_rows = stage_shell(doc, inputs, out_dir, tree_root)
    # the floating builds' resolved identity and actual sha256, recorded in the build output (ADR-0003 a)
    arc = (inputs.get("identity") or {}).get("cursor_archive") or {}
    sources = {"cursor": {k: arc.get(k, "") for k in ("asset", "version", "tag", "url", "sha256", "integrity", "anchor")}}
    summary = {"schema": "mios-xbox-payload/1", "components": comps, "installers": inst_rows, "identity": ident_rows,
               "shell": shell_rows, "sources": sources,
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
