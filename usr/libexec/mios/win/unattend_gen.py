#!/usr/bin/env python3
# AI-hint: Windows 11 autounattend.xml generator with debloat, developer mode & bypasses
# AI-related: tests/test-unattend.py, usr/share/mios/mios.toml, usr/libexec/mios/win/ps_policy_config.py
# AI-functions: UnattendGenerator, UnattendPreset, generate_unattend_xml
"""
MiOS Windows 11 Unattended Answer File (autounattend.xml) Generator.

Synthesizes complete, schema-compliant autounattend.xml answer files for Windows 11:
- windowsPE: TPM 2.0, SecureBoot, RAM & CPU hardware check bypasses, display settings.
- offlineServicing: Driver search path injection for Wi-Fi and storage controllers.
- specialize: Telemetry disabling, OEM branding, Developer Mode, and Long Paths enablement.
- oobeSystem: local accounts + persistent AutoLogon resolved from the LAYERED SSOT
  (usr/lib/mios/mios_credentials.py -- fails closed, no literal account/password),
  OOBE screen bypass, and FirstLogonCommands for WSL2/Hyper-V platform initialization.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from xml.dom import minidom

# Defensive registration in sys.modules for Python 3.14 dataclass processing
# when loaded via dynamic importlib without caller registration
if __name__ not in sys.modules:
    sys.modules[__name__] = types.ModuleType(__name__)


UNATTEND_NS = "urn:schemas-microsoft-com:unattend"
WCM_NS = "http://schemas.microsoft.com/WMIConfig/2002/State"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

def _import_credentials():
    """usr/lib/mios/mios_credentials (installed or repo-relative) -- the ONE fail-closed resolver."""
    for libdir in ("/usr/lib/mios", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios")):
        libdir = os.path.normpath(libdir)
        if os.path.isfile(os.path.join(libdir, "mios_credentials.py")):
            if libdir not in sys.path:
                sys.path.insert(0, libdir)
            import mios_credentials  # noqa: PLC0415
            return mios_credentials
    raise ImportError("mios_credentials.py not found in /usr/lib/mios or the repo tree")


def load_identity_defaults() -> "dict":
    """Resolved credentials from the LAYERED SSOT (vendor < host < user, + mios.d).
    Raises mios_credentials.CredentialError (naming the key) when one is missing --
    there is no literal fallback any more."""
    mc = _import_credentials()
    return mc.resolve(mc.load())


class Preset(str, Enum):
    DEVELOPER = "developer"
    MINIMAL = "minimal"
    GAMING = "gaming"

@dataclass
class UnattendConfig:
    """Configuration parameters for Windows 11 autounattend generation."""
    preset: Preset = Preset.DEVELOPER
    # No credential defaults: from_ssot() fills these from the layered SSOT, or the caller
    # passes them explicitly. build_xml_tree() refuses to render with either empty.
    username: str = ""
    password: Optional[str] = None
    accounts: List[Dict[str, str]] = field(default_factory=list)
    autologon_enable: bool = True
    autologon_user: str = ""
    autologon_persistent: bool = True
    autologon_count: int = 9999999
    # [autounattend.service].svc_user (the RID-500 Administrator). AutoLogon never signs it in
    # (mios_credentials.resolve and the PowerShell Resolve-MiOSCredentials refuse it the same way).
    svc_user: str = ""
    admin_password: Optional[str] = None
    disable_rid500: bool = True
    computer_name: str = "MiOS-Host"
    driver_path: str = "M:\\drivers"
    bypass_tpm: bool = True
    bypass_secure_boot: bool = True
    bypass_ram: bool = True
    bypass_storage: bool = True
    bypass_cpu: bool = True
    disable_telemetry: bool = True
    enable_dev_mode: bool = True
    enable_long_paths: bool = True
    enable_wsl2: bool = True
    resolution_x: int = 1920
    resolution_y: int = 1080

def config_from_ssot(**overrides: Any) -> "UnattendConfig":
    """UnattendConfig with identity/accounts/AutoLogon from the resolved layered SSOT."""
    mc = _import_credentials()
    data = mc.load()
    cred = mc.resolve(data)
    al = cred["autologon"]

    # Resolve computer_name from layered SSOT:
    # 1. [autounattend].computer_name (explicit configurator/overlay key)
    # 2. [identity].hostname (if <= 15 chars and valid NetBIOS)
    # 3. Default fallback: "MiOS-Xbox" if gaming preset else "MiOS-Host"
    preset_val = overrides.get("preset", Preset.DEVELOPER)
    fallback_name = "MiOS-Xbox" if preset_val == Preset.GAMING else "MiOS-Host"

    comp_name = data.get("autounattend", {}).get("computer_name")
    if not comp_name:
        h = data.get("identity", {}).get("hostname")
        if h and len(str(h).strip()) <= 15 and not re.search(r'[\s/\\:*?"<>|]', str(h).strip()):
            comp_name = str(h).strip()
    if not comp_name:
        comp_name = fallback_name

    kw: Dict[str, Any] = dict(
        username=cred["username"], password=cred["default_password"],
        accounts=[dict(a) for a in cred["accounts"]],
        autologon_enable=bool(al["enable"]), autologon_user=al["user"],
        autologon_persistent=bool(al["persistent"]), autologon_count=int(al["count"]),
        svc_user=cred["service"]["user"],
        admin_password=cred.get("service", {}).get("password") or cred.get("default_password"),
        disable_rid500=True,
        computer_name=str(comp_name),
    )
    kw.update({k: v for k, v in overrides.items() if v is not None})
    return UnattendConfig(**kw)


class UnattendGenerator:
    """Generates schema-compliant autounattend.xml document."""

    def __init__(self, config: UnattendConfig, mock: bool = False):
        self.config = config
        self.mock = mock

    def build_xml_tree(self) -> ET.Element:
        """Construct the complete XML tree for autounattend.xml."""
        root = ET.Element("unattend", attrib={
            "xmlns": UNATTEND_NS,
            "xmlns:wcm": WCM_NS,
            "xmlns:xsi": XSI_NS,
        })

        # -------------------------------------------------------------
        # PASS 1: windowsPE
        # -------------------------------------------------------------
        pass_winpe = ET.SubElement(root, "settings", attrib={"pass": "windowsPE"})
        comp_setup = ET.SubElement(pass_winpe, "component", attrib={
            "name": "Microsoft-Windows-Setup",
            "processorArchitecture": "amd64",
            "publicKeyToken": "31bf3856ad364e35",
            "language": "neutral",
            "versionScope": "nonSxS",
        })

        # Display
        display = ET.SubElement(comp_setup, "Display")
        ET.SubElement(display, "HorizontalResolution").text = str(self.config.resolution_x)
        ET.SubElement(display, "VerticalResolution").text = str(self.config.resolution_y)
        ET.SubElement(display, "ColorDepth").text = "32"

        # UserData & EULA
        userdata = ET.SubElement(comp_setup, "UserData")
        ET.SubElement(userdata, "AcceptEula").text = "true"
        prodkey = ET.SubElement(userdata, "ProductKey")
        ET.SubElement(prodkey, "Key").text = "VK7JG-NPHTM-C97JM-9MPGT-3V66T"  # Generic Windows Pro Key
        ET.SubElement(prodkey, "WillShowUI").text = "OnError"

        # RunSynchronous (Hardware Bypasses)
        if self.config.bypass_tpm or self.config.bypass_secure_boot:
            run_sync = ET.SubElement(comp_setup, "RunSynchronous")
            bypasses = [
                ("BypassTPMCheck", self.config.bypass_tpm),
                ("BypassSecureBootCheck", self.config.bypass_secure_boot),
                ("BypassRAMCheck", self.config.bypass_ram),
                ("BypassStorageCheck", self.config.bypass_storage),
                ("BypassCPUCheck", self.config.bypass_cpu),
            ]
            order = 1
            for name, enabled in bypasses:
                if enabled:
                    cmd_elem = ET.SubElement(run_sync, "RunSynchronousCommand", attrib={"wcm:action": "add"})
                    ET.SubElement(cmd_elem, "Order").text = str(order)
                    ET.SubElement(cmd_elem, "Path").text = f"reg add HKLM\\SYSTEM\\Setup\\LabConfig /v {name} /t REG_DWORD /d 1 /f"
                    ET.SubElement(cmd_elem, "Description").text = f"Bypass {name}"
                    order += 1

        # -------------------------------------------------------------
        # PASS 2: offlineServicing (Driver search path)
        # -------------------------------------------------------------
        if self.config.driver_path:
            pass_offline = ET.SubElement(root, "settings", attrib={"pass": "offlineServicing"})
            comp_pnp = ET.SubElement(pass_offline, "component", attrib={
                "name": "Microsoft-Windows-PnpCustomizationsNonWinPE",
                "processorArchitecture": "amd64",
                "publicKeyToken": "31bf3856ad364e35",
                "language": "neutral",
                "versionScope": "nonSxS",
            })
            driver_paths = ET.SubElement(comp_pnp, "DriverPaths")
            path_cred = ET.SubElement(driver_paths, "PathAndCredentials", attrib={"wcm:action": "add", "wcm:keyValue": "1"})
            ET.SubElement(path_cred, "Path").text = self.config.driver_path

        # -------------------------------------------------------------
        # PASS 3: specialize
        # -------------------------------------------------------------
        pass_spec = ET.SubElement(root, "settings", attrib={"pass": "specialize"})
        comp_shell = ET.SubElement(pass_spec, "component", attrib={
            "name": "Microsoft-Windows-Shell-Setup",
            "processorArchitecture": "amd64",
            "publicKeyToken": "31bf3856ad364e35",
            "language": "neutral",
            "versionScope": "nonSxS",
        })
        c_name = (self.config.computer_name or "").strip()
        if not c_name:
            raise ValueError("ComputerName cannot be empty.")
        if c_name != "*":
            if len(c_name) > 15:
                raise ValueError(
                    f"ComputerName '{c_name}' exceeds 15-character NetBIOS limit (len={len(c_name)}). "
                    "Shorten computer name to <= 15 characters."
                )
            if re.search(r'[\s/\\:*?"<>|]', c_name):
                raise ValueError(
                    f"ComputerName '{c_name}' contains illegal NetBIOS characters. "
                    "Use alphanumeric characters and hyphens only."
                )
        ET.SubElement(comp_shell, "ComputerName").text = c_name
        ET.SubElement(comp_shell, "TimeZone").text = "UTC"

        oem_info = ET.SubElement(comp_shell, "OEMInformation")
        ET.SubElement(oem_info, "Manufacturer").text = "MiOS AI Workstation"
        ET.SubElement(oem_info, "Model").text = "MiOS Hybrid Development Node"

        # Debloat & Developer Mode registry tweaks
        comp_deployment = ET.SubElement(pass_spec, "component", attrib={
            "name": "Microsoft-Windows-Deployment",
            "processorArchitecture": "amd64",
            "publicKeyToken": "31bf3856ad364e35",
            "language": "neutral",
            "versionScope": "nonSxS",
        })
        run_spec_sync = ET.SubElement(comp_deployment, "RunSynchronous")
        spec_cmds = []
        if self.config.disable_telemetry:
            spec_cmds.append(("Disable Telemetry", "reg add HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection /v AllowTelemetry /t REG_DWORD /d 0 /f"))
            spec_cmds.append(("Disable Consumer Features", "reg add HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\CloudContent /v DisableWindowsConsumerFeatures /t REG_DWORD /d 1 /f"))
        if self.config.enable_dev_mode:
            spec_cmds.append(("Enable Developer Mode", "reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f"))
        if self.config.enable_long_paths:
            spec_cmds.append(("Enable Long Paths", "reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f"))

        for idx, (desc, cmd) in enumerate(spec_cmds, 1):
            cmd_elem = ET.SubElement(run_spec_sync, "RunSynchronousCommand", attrib={"wcm:action": "add"})
            ET.SubElement(cmd_elem, "Order").text = str(idx)
            ET.SubElement(cmd_elem, "Path").text = cmd
            ET.SubElement(cmd_elem, "Description").text = desc

        # -------------------------------------------------------------
        # PASS 4: oobeSystem
        # -------------------------------------------------------------
        pass_oobe = ET.SubElement(root, "settings", attrib={"pass": "oobeSystem"})
        comp_oobe_shell = ET.SubElement(pass_oobe, "component", attrib={
            "name": "Microsoft-Windows-Shell-Setup",
            "processorArchitecture": "amd64",
            "publicKeyToken": "31bf3856ad364e35",
            "language": "neutral",
            "versionScope": "nonSxS",
        })

        # Credentials: fail closed -- never a literal account or password.
        if not self.config.username:
            raise ValueError("MiOS SSOT: required credential key 'identity.username' is missing or empty (no built-in default).")
        if not self.config.password:
            raise ValueError("MiOS SSOT: required credential key 'identity.default_password' is missing or empty (no built-in default).")
        accounts = [dict(a) for a in self.config.accounts] or [
            {"name": self.config.username, "display_name": f"MiOS Operator ({self.config.username})",
             "group": "Administrators", "password": self.config.password}]
        for a in accounts:
            if not a.get("password"):
                a["password"] = self.config.password
        al_user = self.config.autologon_user or self.config.username
        if self.config.autologon_enable and self.config.svc_user and al_user.lower() == self.config.svc_user.lower():
            raise ValueError(f"MiOS SSOT: autounattend.autologon_user '{al_user}' is the service account "
                             "([autounattend.service].svc_user, the RID-500 Administrator disabled at setup finalize); "
                             "AutoLogon must sign in a desktop account.")
        al_pwd = next((a["password"] for a in accounts if a["name"].lower() == al_user.lower()), "")
        if self.config.autologon_enable and not al_pwd:
            raise ValueError(f"MiOS SSOT: autounattend.autologon_user '{al_user}' is not a rendered account, so it has no SSOT password.")

        # AutoLogon (console-style; persistent per SSOT autounattend.autologon_persistent)
        auto_logon = ET.SubElement(comp_oobe_shell, "AutoLogon")
        ET.SubElement(auto_logon, "Enabled").text = "true" if self.config.autologon_enable else "false"
        ET.SubElement(auto_logon, "LogonCount").text = str(self.config.autologon_count)
        ET.SubElement(auto_logon, "Username").text = al_user
        pwd = ET.SubElement(auto_logon, "Password")
        ET.SubElement(pwd, "Value").text = al_pwd
        ET.SubElement(pwd, "PlainText").text = "true"

        # OOBE Bypass flags
        oobe = ET.SubElement(comp_oobe_shell, "OOBE")
        ET.SubElement(oobe, "HideEULAPage").text = "true"
        ET.SubElement(oobe, "HideLocalAccountScreen").text = "true"
        ET.SubElement(oobe, "HideOnlineAccountScreens").text = "true"
        ET.SubElement(oobe, "HideWirelessSetupInOOBE").text = "true"
        ET.SubElement(oobe, "ProtectYourPC").text = "3"
        ET.SubElement(oobe, "NetworkLocation").text = "Work"

        # UserAccounts (every SSOT account)
        user_accs = ET.SubElement(comp_oobe_shell, "UserAccounts")
        if self.config.admin_password:
            admin_pwd = ET.SubElement(user_accs, "AdministratorPassword")
            ET.SubElement(admin_pwd, "Value").text = self.config.admin_password
            ET.SubElement(admin_pwd, "PlainText").text = "true"
        local_accs = ET.SubElement(user_accs, "LocalAccounts")
        for a in accounts:
            local_acc = ET.SubElement(local_accs, "LocalAccount", attrib={"wcm:action": "add"})
            ET.SubElement(local_acc, "Name").text = a["name"]
            ET.SubElement(local_acc, "Group").text = a.get("group") or "Administrators"
            ET.SubElement(local_acc, "DisplayName").text = a.get("display_name") or a["name"]
            l_pwd = ET.SubElement(local_acc, "Password")
            ET.SubElement(l_pwd, "Value").text = a["password"]
            ET.SubElement(l_pwd, "PlainText").text = "true"

        # FirstLogonCommands
        first_cmds = ET.SubElement(comp_oobe_shell, "FirstLogonCommands")
        fl_list = [
            ("PowerShell Execution Policy", "powershell.exe -NoProfile -Command \"Set-ExecutionPolicy -Scope LocalMachine -ExecutionPolicy RemoteSigned -Force\""),
            ("MiOS AI Endpoint Variable", "powershell.exe -NoProfile -Command \"[System.Environment]::SetEnvironmentVariable('MIOS_AI_ENDPOINT', 'http://127.0.0.1:8700/v1', 'Machine')\""),
        ]
        if self.config.autologon_enable and self.config.autologon_persistent:
            fl_list.append(("Persistent AutoLogon (SSOT autounattend.autologon_persistent)",
                            "cmd /c reg delete \"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\" /v AutoLogonCount /f || exit 0"))
        if self.config.disable_rid500:
            fl_list.append(("Disable Built-in Administrator (RID-500 at finalize)",
                            "powershell.exe -NoProfile -Command \"Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' } | Disable-LocalUser\""))
        if self.config.enable_wsl2:
            fl_list.append(("Enable WSL2 & VM Platform", "powershell.exe -NoProfile -Command \"Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform,Microsoft-Windows-Subsystem-Linux -NoRestart\""))

        for idx, (desc, cmd) in enumerate(fl_list, 1):
            cmd_elem = ET.SubElement(first_cmds, "SynchronousCommand", attrib={"wcm:action": "add"})
            ET.SubElement(cmd_elem, "Order").text = str(idx)
            ET.SubElement(cmd_elem, "CommandLine").text = cmd
            ET.SubElement(cmd_elem, "Description").text = desc

        return root

    def generate_xml_string(self) -> str:
        """Produce pretty-printed XML string with XML declaration."""
        tree = self.build_xml_tree()
        raw_str = ET.tostring(tree, encoding="utf-8")
        parsed = minidom.parseString(raw_str)
        return parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")

    def run(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Generate autounattend.xml content and write to file if requested."""
        xml_content = self.generate_xml_string()

        if output_path and not self.mock:
            parent = os.path.dirname(os.path.abspath(output_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".unattend-", suffix=".tmp", dir=parent or ".")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(xml_content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, output_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

        return {
            "status": "success",
            "preset": self.config.preset.value,
            "username": self.config.username,
            "computer_name": self.config.computer_name,
            "bypasses_enabled": {
                "tpm": self.config.bypass_tpm,
                "secure_boot": self.config.bypass_secure_boot,
                "ram": self.config.bypass_ram,
                "storage": self.config.bypass_storage,
                "cpu": self.config.bypass_cpu,
            },
            "features_enabled": {
                "telemetry_disabled": self.config.disable_telemetry,
                "developer_mode": self.config.enable_dev_mode,
                "long_paths": self.config.enable_long_paths,
                "wsl2": self.config.enable_wsl2,
            },
            "output_path": output_path,
            "xml_length": len(xml_content),
            "xml_preview": xml_content[:500] + "...",
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Windows 11 autounattend.xml Generator"
    )
    parser.add_argument("--preset", choices=["developer", "minimal", "gaming"], default="developer", help="Configuration preset")
    parser.add_argument("--username", default=None, help="Override [identity].username (default: the layered SSOT value)")
    parser.add_argument("--password", default=None, help="Override [identity].default_password (default: the layered SSOT value)")
    parser.add_argument("--admin-password", default=None, help="Override RID-500 Administrator password")
    parser.add_argument("--no-disable-rid500", dest="disable_rid500", action="store_false", default=True, help="Do not disable RID-500 Administrator")
    parser.add_argument("--computer-name", default=None, help="NetBIOS / DNS computer name (default: SSOT or 'MiOS-Host')")
    parser.add_argument("--driver-path", default="M:\\drivers", help="Driver staging search path")
    parser.add_argument("--bypass-tpm", action="store_true", default=True, help="Enable TPM 2.0 bypass")
    parser.add_argument("--disable-telemetry", action="store_true", default=True, help="Disable Windows telemetry")
    parser.add_argument("--output", help="Target autounattend.xml file path")
    parser.add_argument("--emit-xml", action="store_true", help="Print raw XML to stdout")
    parser.add_argument("--dry-run", action="store_true", help="Simulate generation without writing file")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock execution for CI testing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()

    try:
        cfg = config_from_ssot(
            preset=Preset(args.preset),
            computer_name=args.computer_name,
            driver_path=args.driver_path,
            bypass_tpm=args.bypass_tpm,
            disable_telemetry=args.disable_telemetry,
            admin_password=args.admin_password,
            disable_rid500=args.disable_rid500,
        )
    except Exception as e:  # CredentialError / unreadable SSOT -> fail closed, naming the key
        if args.username and args.password:
            cfg = UnattendConfig(preset=Preset(args.preset), computer_name=args.computer_name or "MiOS-Host",
                                 driver_path=args.driver_path, bypass_tpm=args.bypass_tpm,
                                 disable_telemetry=args.disable_telemetry,
                                 admin_password=args.admin_password,
                                 disable_rid500=args.disable_rid500)
        else:
            print(f"[unattend_gen] ERROR: {e}", file=sys.stderr)
            return 3
    if args.username is not None or args.password is not None:
        # An explicit override names ONE identity account (and AutoLogon to it).
        cfg.username = args.username if args.username is not None else cfg.username
        cfg.password = args.password if args.password is not None else cfg.password
        cfg.accounts = []
        cfg.autologon_user = cfg.username
    if args.admin_password is not None:
        cfg.admin_password = args.admin_password
    cfg.disable_rid500 = args.disable_rid500

    gen = UnattendGenerator(cfg, mock=args.mock)

    try:
        if args.emit_xml:
            print(gen.generate_xml_string())
            return 0

        res = gen.run(output_path=args.output)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[unattend_gen] SUCCESS: Generated Windows 11 autounattend.xml ({cfg.preset.value} preset)")
            print(f"  Account: {cfg.username}, Computer: {cfg.computer_name}, Driver Path: {cfg.driver_path}")
            if args.output:
                print(f"  Saved to: {args.output}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[unattend_gen] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
