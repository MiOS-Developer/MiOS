#!/usr/bin/env python3
# AI-hint: MiOS-Xbox spec renderer: layered SSOT [windows.xbox] (vendor -> host -> user via mios_toml.load_merged) -> resolved xbox-spec.json, the single input of every Windows build step; a missing key or empty credential is a hard failure naming the key; a Remote Desktop Services registry_map entry must name its TerminalServer.admx policy and that policy's value name (every settings.rdp key mapped).
# AI-related: usr/lib/mios/mios_toml.py, usr/share/mios/mios.toml, tests/test-mios-xbox-spec.py, /.devloop/GOALS-mios-xbox.md, /.devloop/plan-mios-xbox.md
# AI-functions: render, render_layers, resolve_ref, build_registry, guest_projection, main
"""Render the MiOS-Xbox image specification.

Input:  the merged SSOT ([windows.xbox] plus the keys it references by *_ref).
Output: xbox-spec.json with
  spec      the merged [windows.xbox] tree, verbatim (references left as written)
  resolved  every *_ref path -> its SSOT value
  users     accounts with credentials resolved (the file is written mode 0600)
  region    Windows timezone id, UI language, input locale
  source    UUP source with the language tag derived from the SSOT locale
  registry  every registry write the spec implies, one owner per hive/key/value
  feature_overrides, debloat_services, layers

Exit status: 0 rendered; 2 a required key is missing / empty / malformed (the key is named);
1 usage or I/O error. Credentials are never printed.
"""

from __future__ import annotations

import argparse
import copy
import functools
import hashlib
import hmac
import json
import os
import re
import struct
import sys
import tempfile
import uuid
import zipfile
import zlib

_TREE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", ".."))
_LIB = os.path.join(_TREE, "usr", "lib", "mios")
if os.path.isfile(os.path.join(_LIB, "mios_toml.py")) and _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import mios_toml  # noqa: E402
import mios_credentials  # noqa: E402  -- the ONE layered-SSOT credential resolver (same tree)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import spec_items as _S  # noqa: E402  -- Microsoft's TerminalServer.admx policy table (TS_POLICIES), shared with the gate

ROOT = "windows.xbox"
SCHEMA = 1

# The contract every Windows step relies on: key path (under windows.xbox) -> expected type.
# Values live only in the SSOT; this lists names and shapes, never defaults.
REQUIRED = {
    "enable": bool,
    "spec_schema": int,
    "source.provider": str, "source.channel": str, "source.build": str, "source.arch": str,
    "source.edition": str, "source.language_ref": str, "source.apps": list, "source.app_frameworks": str,
    "preferences.taskbar_align": int, "preferences.search_box_mode": int,
    "preferences.taskbar_widgets": int, "preferences.taskbar_chat": int,
    "preferences.task_view_button": int, "preferences.touch_keyboard_button": int,
    "preferences.taskbar_end_task": int, "preferences.desktop_icons_hidden": int,
    "preferences.recycle_bin_hidden": int, "preferences.edge_desktop_shortcut": int,
    "preferences.hide_file_extensions": int,
    "start.pins": list, "start.taskbar_pins": list, "start.folders": list,
    "start.folders_universe": list, "start.folder_policy_hive": str,
    "start.folder_policy_key": str, "start.folder_policy_prefix": str,
    "start.pins_policy_hive": str, "start.pins_policy_key": str,
    "start.layout_family_source": str, "start.layout_assets": dict,
    "start.capture.user": str, "start.capture.logon_timeout_s": int, "start.capture.settle_s": int,
    "start.capture.open_start_after_s": int, "gate.negative_controls": list,
    "xbox.enable": bool, "xbox.device_form_spoof": bool, "xbox.device_form": int,
    "xbox.home_app": str, "xbox.start_on_startup": bool, "xbox.game_mode": int,
    "xbox.game_bar": int, "xbox.background_capture": int, "xbox.auto_hdr": int,
    "xbox.hags": int, "xbox.override_state": int, "xbox.override_hive": str,
    "xbox.override_key": str, "xbox.feature_overrides": dict,
    "features.enable": list, "features.enable_if_nested_virt": list,
    "features.disable": list, "features.capabilities_add": list,
    "drivers.gpu": str, "drivers.gpu_step": str, "drivers.deny_display_providers": list,
    "drivers.virtio_win": dict, "drivers.looking_glass_idd": dict,
    "guest_tools.virtio_win_guest_tools": dict, "guest_tools.qemu_ga": dict,
    "guest_tools.spice_vdagent": dict, "guest_tools.winfsp": dict,
    "guest_tools.virtiofs": dict, "guest_tools.looking_glass_host": dict,
    "components.dest_dir": str, "components.ship": list, "components.not_shipped": list,
    "settings.oobe": dict, "settings.privacy": dict, "settings.power": dict,
    "settings.display": dict, "settings.input.keyboard_ref": str,
    "settings.region.timezone_ref": str, "settings.region.language_ref": str,
    "settings.region.timezone_map": dict, "settings.region.keyboard_map": dict,
    "settings.region.utc_clock": int,
    "settings.enablements": dict,
    "settings.security.prevent_device_encryption": int,
    "settings.ssh.start": int, "settings.ssh.enable": bool,
    "settings.rdp.enable": int, "settings.rdp.allow_audio_playback": int,
    "settings.rdp.allow_microphone": int, "settings.rdp.allow_camera": int, "settings.rdp.allow_drives": int,
    "settings.rdp.allow_com_ports": int, "settings.rdp.allow_clipboard": int,
    "settings.rdp.allow_location": int, "settings.rdp.allow_time_zone": int,
    "users.computer_name_ref": str, "users.service_account_ref": str,
    "users.desktop.name_ref": str, "users.desktop.password_ref": str,
    "users.desktop.display_name_ref": str, "users.desktop.groups": list,
    "users.desktop.autologon": bool, "users.desktop.autologon_persistent": bool,
    "users.builtin_administrator.password_ref": str,
    "users.builtin_administrator.disable_at": str,
    "store_hydrate.stage": str, "store_hydrate.packages": list,
    "baked.webview2": dict,
    "debloat.enable": bool, "debloat.profile": str, "debloat.profiles": dict,
    "vm.name": str, "vm.disk_size": str, "vm.disk_format": str, "vm.pool": str,
    "vm.memory_kib_ref": str, "vm.vcpus_ref": str, "vm.worker.name": str,
    "vm.worker.overlay_of": str, "vm.worker.gpu_passthrough": bool,
    "vm.worker.memory_gib": int, "vm.worker.vcpus": int,
    "vm.worker.credential_source": str, "vm.worker.base_answer_file": str,
    "settings.setup.install_key": str, "settings.setup.disk_id": int, "settings.setup.wipe_disk": bool,
    "settings.setup.efi_mb": int, "settings.setup.msr_mb": int,
    "winrm.enable": bool, "winrm.remote_address": str, "winrm.port_ref": str,
    "probe.winrm_port": int, "probe.transport": str, "probe.operation_timeout_s": int,
    "probe.read_timeout_s": int, "probe.connect_timeout_s": int, "probe.python": str,
    "gpu.task_name": str, "gpu.script_path": str, "gpu.spec_path": str, "gpu.record_path": str,
    "build.work_root": str, "build.artifact_root": str, "build.native_dir": str,
    "build.iso_label": str, "build.iso_name": str, "build.memory_headroom_gib": int,
    "build.exchange_disk_gib": int, "build.worker_boot_timeout_s": int,
    "build.worker_build_timeout_s": int, "build.worker_poll_s": int, "build.download_tries": int,
    "build.extract_memsize_mib": int, "build.heavy_lock": str, "build.host_memory_max": str,
    "build.converter.upstream": str, "build.converter.subdir": str, "build.converter.channel": str,
    "build.converter.entry": str, "build.converter.config": dict, "build.converter.store_apps": dict,
    "registry_map": dict,
    # provisioning stages (who runs what), SSH inputs, MiOS services/tasks, wallpaper page (fix-a M1)
    "features.capability_fod": dict, "features.fod_metadata": str,
    "provision.first_logon_commands": bool, "provision.state_dir": str, "provision.log_dir": str,
    "provision.verify_task": str, "provision.hydrate_task": str, "provision.retry_task": str, "provision.serial_port": str,
    "provision.mirror_task": str, "provision.legacy_mirror_tasks": list,
    "devices.record": str, "drivers.looking_glass_idd.required_device": str,
    "guest_tools.looking_glass_host.install": bool,
    "identity.accent_ref": str, "identity.cursor_ref": str, "identity.cursor_theme_ref": str, "identity.cursor_repo_ref": str,
    "identity.font_ref": str,
    "identity.font_dir": str, "identity.system_root": str,
    "identity.theme_name_ref": str, "identity.theme_mode_ref": str, "identity.transparency_ref": str,
    "identity.background_color_ref": str, "identity.background": str,
    "identity.oem_manufacturer_ref": str, "identity.oem_model_ref": str, "identity.oem_support_url_ref": str,
    "identity.oem_support_hours_ref": str, "identity.oem_logo_ref": str, "identity.oem_logo_source_ref": str,
    "identity.oem_logo_projection": str,
    "shell.dir": str, "shell.bin_dir": str, "shell.wt_fragment": str, "shell.all_users_profiles": list,
    "shell.profile_name_ref": str, "shell.scheme_name_ref": str, "shell.look_ref": str, "shell.font_ref": str,
    "shell.prompt_config_ref": str, "shell.fastfetch_config_ref": str, "shell.fastfetch_theme_ref": str,
    "shell.verbs_ref": str, "shell.execution_policy": str,
    "shell.pwsh.upstream": str, "shell.pwsh.channel": str, "shell.pwsh.asset": str, "shell.pwsh.signer": str,
    "shell.pwsh.install_dir": str, "shell.pwsh.msi_properties": list, "shell.pwsh.install_at": str,
    "shell.oh_my_posh.upstream": str, "shell.oh_my_posh.channel": str, "shell.oh_my_posh.asset": str,
    "shell.oh_my_posh.exe": str,
    "shell.fastfetch.upstream": str, "shell.fastfetch.channel": str, "shell.fastfetch.asset": str, "shell.fastfetch.exe": str,
    "provision.serial_baud": int, "provision.network_category": str, "provision.network_wait_s": int,
    "ssh.install": str, "ssh.port_ref": str, "ssh.settings_ref": str, "ssh.authorized_keys_key": str,
    "ssh.authorized_keys_files": list, "ssh.settings_path": str, "ssh.authorized_keys_path": str,
    "services": dict, "tasks": dict,
    "wallpaper.html_ref": str, "wallpaper.target_ref": str, "wallpaper.setup_component": str,
}
SSH_INSTALL_MODES = ("fod-offline",)   # F4: the OpenSSH.Server FOD is baked into install.wim; never online-only
SSH_KEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+( .*)?$")
NETWORK_CATEGORIES = ("Private",)      # WinRM's firewall exception is refused while a profile is Public
SERVICE_HOSTS = ("self", "MiosServiceTool")
TASK_RUN_AS = ("system", "desktop")
TASK_TRIGGERS = ("startup", "logon")
# components.ship[].invocation: "per-request" = a one-shot CLI another program runs per request (mios-agent-relay
# --state PATH < request.json exits 2 without them: as a startup task it "ran" at every boot and did nothing)
COMPONENT_INVOCATIONS = ("per-request",)
# Account names Windows reserves for the built-in Administrator (RID 500): never the AutoLogon account.
RID500_NAMES = ("administrator",)
WORKER_CREDENTIAL_SOURCES = ("base-answer-file",)
# References whose target is a credential: must resolve to a non-blank string.
CREDENTIAL_REFS = ("users.desktop.password_ref", "users.builtin_administrator.password_ref", "users.admin.password_ref")
HIVES = ("DEFAULT_USER", "SOFTWARE", "SYSTEM")
REG_TYPES = ("REG_DWORD", "REG_SZ")
DEBLOAT_HIVES = {"software": "SOFTWARE", "system": "SYSTEM", "default_user": "DEFAULT_USER"}
GPU_MODES = ("detect",)   # XB-3: no vendor package mode exists


class SpecError(Exception):
    """A required SSOT key is missing, empty or malformed. str() names the key, never a value."""

    def __init__(self, key: str, why: str):
        super().__init__(f"{key}: {why}")
        self.key = key


_MISSING = object()


def _get(tree, dotted):
    cur = tree
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _full(path: str) -> str:
    return f"{ROOT}.{path}"


def _typecheck(x: dict) -> None:
    for path, typ in REQUIRED.items():
        val = _get(x, path)
        if val is _MISSING:
            raise SpecError(_full(path), "missing")
        if typ is int and (isinstance(val, bool) or not isinstance(val, int)):
            raise SpecError(_full(path), "must be an integer")
        if typ is not int and not isinstance(val, typ):
            raise SpecError(_full(path), f"must be a {typ.__name__}")
        if typ is str and path.endswith("_ref") and not val.strip():
            raise SpecError(_full(path), "empty reference")


def _walk_refs(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            if k.endswith("_ref") and isinstance(v, str):
                yield p, v
            elif isinstance(v, dict):
                yield from _walk_refs(v, p)


def resolve_ref(merged: dict, ref_path: str, target: str, credential: bool = False):
    """Resolve one *_ref; missing or blank target -> SpecError naming both keys."""
    val = _get(merged, target)
    if credential and target in ("identity.admin_password", "identity.user_password") and (
            val is _MISSING or val is None or (isinstance(val, str) and not val.strip())):
        def_val = _get(merged, "identity.default_password")
        if def_val is not _MISSING and def_val is not None and isinstance(def_val, str) and def_val.strip():
            val = def_val
            target = "identity.default_password"
    if val is _MISSING or val is None:
        raise SpecError(_full(ref_path), f"references missing key {target}")
    if isinstance(val, str) and not val.strip():
        kind = "empty credential" if credential else "empty value"
        raise SpecError(target, f"{kind} (referenced by {_full(ref_path)})")
    if credential and not isinstance(val, str):
        raise SpecError(target, f"credential must be a string (referenced by {_full(ref_path)})")
    if credential and isinstance(val, str) and val.strip().lower() == "mios":
        raise SpecError(target, f"credential equals vendor default 'mios' (referenced by {_full(ref_path)})")
    return val


def _reg_data(path, value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, str)):
        return value
    raise SpecError(_full(path), "registry-mapped value must be an integer, boolean or string")


def build_registry(x: dict) -> list[dict]:
    """Every registry write implied by the spec; duplicate owners are an error."""
    items: list[dict] = []

    def add(owner, hive, key, name, typ, data, kv_field=None, delete=False, policy=None):
        if hive not in HIVES:
            raise SpecError(_full(owner), f"unknown hive {hive!r}")
        if not delete and typ not in REG_TYPES:
            raise SpecError(_full(owner), f"unknown registry type {typ!r}")
        if typ == "REG_DWORD" and not delete and not isinstance(data, int):
            raise SpecError(_full(owner), "REG_DWORD data must be an integer")
        item = {"owner": owner, "hive": hive, "key": key, "name": name}
        if delete:
            item["delete"] = True
        else:
            item.update(type=typ, data=data)
        if kv_field:
            item["kv_field"] = kv_field
        if policy:
            item["policy"] = policy
        items.append(item)

    for path, loc in x["registry_map"].items():
        if not isinstance(loc, dict):
            raise SpecError(_full(f"registry_map.{path}"), "must be a table")
        for f in ("hive", "key", "name", "type"):
            if not isinstance(loc.get(f), str) or not loc[f]:
                raise SpecError(_full(f"registry_map.{path}.{f}"), "missing")
        val = _get(x, path)
        if val is _MISSING:
            raise SpecError(_full(path), f"missing (mapped by {_full('registry_map')})")
        when = loc.get("when")
        if when is not None:
            gate = _get(x, when)
            if gate is _MISSING:
                raise SpecError(_full(when), f"missing (gate of registry_map.{path})")
            if not gate:
                continue
        if loc.get("policy") is not None or _S.is_ts_policy_key(loc["hive"], loc["key"]):
            # a Remote Desktop Services policy: the value name and data must be the named TerminalServer.admx
            # policy's (Microsoft's ADMX_TerminalServer table), so a misnamed value (fDisableLocation) or a policy
            # used for the wrong device (fDisableCam = playback, not the camera) cannot render (F5)
            if not _S.is_ts_policy_key(loc["hive"], loc["key"]) or loc["type"] != "REG_DWORD":
                raise SpecError(_full(f"registry_map.{path}"), f"a policy entry must be a REG_DWORD under SOFTWARE\\{_S.TS_POLICY_KEY}")
            why = _S.ts_policy_problem(loc.get("policy"), loc["name"], _reg_data(path, val))
            if why:
                raise SpecError(_full(f"registry_map.{path}"), why)
        add(path, loc["hive"], loc["key"], loc["name"], loc["type"], _reg_data(path, val), loc.get("kv_field"),
            policy=loc.get("policy"))
    # every RDP redirection switch is written by exactly one Terminal Services policy entry (closed world: a stale
    # or renamed key in a host/user layer fails here instead of being silently ignored)
    for k in sorted((x.get("settings") or {}).get("rdp") or {}):
        if k == "enable":
            continue
        loc = x["registry_map"].get(f"settings.rdp.{k}")
        if not isinstance(loc, dict) or not loc.get("policy"):
            raise SpecError(_full(f"settings.rdp.{k}"), "no registry_map entry naming its TerminalServer.admx policy")

    start = x["start"]
    universe = start["folders_universe"]
    for i, f in enumerate(start["folders"]):
        if f not in universe:
            raise SpecError(_full(f"start.folders[{i}]"), f"{f!r} is not in start.folders_universe")
    if len(set(start["folders"])) != len(start["folders"]):
        raise SpecError(_full("start.folders"), "duplicate folder")
    for f in universe:
        add("start.folders", start["folder_policy_hive"], start["folder_policy_key"],
            start["folder_policy_prefix"] + f, "REG_DWORD", 1 if f in start["folders"] else 0)
    add("start.pins", start["pins_policy_hive"], start["pins_policy_key"], "ConfigureStartPins",
        "REG_SZ", json.dumps({"pinnedList": start["pins"]}, separators=(",", ":")))

    xb = x["xbox"]
    if xb["enable"]:
        for name, ov in sorted(x["xbox"]["feature_overrides"].items()):
            if not ov.get("enabled"):
                continue
            add(f"xbox.feature_overrides.{name}", xb["override_hive"],
                f"{xb['override_key']}\\{ov['id']}", "EnabledState", "REG_DWORD", xb["override_state"])

    if x["debloat"]["enable"]:
        prof = x["debloat"]["profile"]
        body = x["debloat"]["profiles"].get(prof)
        if not isinstance(body, dict):
            raise SpecError(_full(f"debloat.profiles.{prof}"), "missing (selected by debloat.profile)")
        for sect, hive in DEBLOAT_HIVES.items():
            for i, e in enumerate(body.get(sect, [])):
                owner = f"debloat.profiles.{prof}.{sect}[{i}]"
                if not isinstance(e, dict) or not e.get("key") or not e.get("name"):
                    raise SpecError(_full(owner), "needs key and name")
                if e.get("delete"):
                    add(owner, hive, e["key"], e["name"], None, None, delete=True)
                else:
                    add(owner, hive, e["key"], e["name"], e.get("type"), e.get("data"))

    seen: dict = {}
    for it in items:
        ident = (it["hive"], it["key"].lower(), it["name"].lower(), it.get("kv_field"))
        if ident in seen:
            raise SpecError(_full(it["owner"]),
                            f"registry value {it['hive']}\\{it['key']}\\{it['name']} already owned by {_full(seen[ident])}")
        seen[ident] = it["owner"]
    return items


def _feature_ids(x: dict) -> list[dict]:
    out, ids = [], set()
    for name, ov in sorted(x["xbox"]["feature_overrides"].items()):
        p = f"xbox.feature_overrides.{name}"
        if not isinstance(ov, dict):
            raise SpecError(_full(p), "must be a table")
        fid = ov.get("id")
        if isinstance(fid, bool) or not isinstance(fid, int) or fid <= 0:
            raise SpecError(_full(f"{p}.id"), "must be a positive integer")
        if not isinstance(ov.get("enabled"), bool):
            raise SpecError(_full(f"{p}.enabled"), "must be a boolean")
        if fid in ids:
            raise SpecError(_full(f"{p}.id"), f"duplicate feature id {fid}")
        ids.add(fid)
        out.append({"name": name, "id": fid, "enabled": ov["enabled"], "group": ov.get("group", "")})
    return out


def _lang_tag(locale_value: str, key: str) -> str:
    m = re.fullmatch(r"([a-z]{2,3})[_-]([A-Za-z]{2})(?:\..*)?", locale_value)
    if not m:
        raise SpecError(key, "locale must look like xx_YY[.charset]")
    return f"{m.group(1)}-{m.group(2).upper()}"


def render(merged: dict, layers: list[str] | None = None) -> dict:
    merged = copy.deepcopy(merged)
    x = _get(merged, ROOT)
    if x is _MISSING or not isinstance(x, dict):
        raise SpecError(ROOT, "missing")
    _typecheck(x)
    if x["drivers"]["gpu"] not in GPU_MODES:
        raise SpecError(_full("drivers.gpu"), f"must be one of {GPU_MODES} (no GPU vendor package in the image)")
    if x["spec_schema"] != SCHEMA:
        raise SpecError(_full("spec_schema"), f"renderer understands schema {SCHEMA}")

    resolved = {}
    for path, target in _walk_refs(x):
        resolved[path] = resolve_ref(merged, path, target, credential=path in CREDENTIAL_REFS)

    for i, comp in enumerate(x["components"]["ship"]):
        for f in ("name", "kind", "source"):
            if not isinstance(comp, dict) or not isinstance(comp.get(f), str) or not comp[f]:
                raise SpecError(_full(f"components.ship[{i}].{f}"), "missing")
    for i, pkg in enumerate(x["store_hydrate"]["packages"]):
        if not isinstance(pkg, dict) or not pkg.get("name") or not pkg.get("store_id"):
            raise SpecError(_full(f"store_hydrate.packages[{i}]"), "needs name and store_id")

    reg = x["settings"]["region"]
    tz = resolved["settings.region.timezone_ref"]
    win_tz = reg["timezone_map"].get(tz)
    if not win_tz:
        raise SpecError(_full(f"settings.region.timezone_map.{tz}"), "no Windows timezone id for the SSOT timezone")
    kb = resolved["settings.input.keyboard_ref"]
    input_locale = reg["keyboard_map"].get(kb)
    if not input_locale:
        raise SpecError(_full(f"settings.region.keyboard_map.{kb}"), "no Windows input locale for the SSOT keyboard layout")
    ui_lang = _lang_tag(resolved["settings.region.language_ref"], reg["language_ref"])

    d, b = x["users"]["desktop"], x["users"]["builtin_administrator"]
    logon_count = _check_accounts(merged, x, resolved)
    users = {
        "computer_name": resolved["users.computer_name_ref"],
        "service_account": {"name": resolved["users.service_account_ref"]},
        "desktop": {
            "name": resolved["users.desktop.name_ref"],
            "password": resolved["users.desktop.password_ref"],
            "display_name": resolved["users.desktop.display_name_ref"],
            "groups": d["groups"],
            "autologon": d["autologon"],
            "autologon_persistent": d["autologon_persistent"],
            "logon_count": logon_count,
        },
        "builtin_administrator": {
            "password": resolved["users.builtin_administrator.password_ref"],
            "disable_at": b["disable_at"],
        },
    }
    if "admin" in x["users"]:
        adm = x["users"]["admin"]
        users["admin"] = {
            "name": resolved["users.admin.name_ref"],
            "password": resolved["users.admin.password_ref"],
            "display_name": resolved["users.admin.display_name_ref"],
            "groups": adm.get("groups", ["Administrators", "Remote Desktop Users"]),
            "autologon": bool(adm.get("autologon", False)),
        }
    if x["vm"]["worker"]["credential_source"] not in WORKER_CREDENTIAL_SOURCES:
        raise SpecError(_full("vm.worker.credential_source"), f"must be one of {WORKER_CREDENTIAL_SOURCES}")
    if x["vm"]["worker"]["gpu_passthrough"]:
        raise SpecError(_full("vm.worker.gpu_passthrough"), "the build worker never holds a GPU (XB-2/Q9)")
    port = resolved["winrm.port_ref"]
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise SpecError(_full("winrm.port_ref"), "must resolve to a TCP port")
    source = {k: v for k, v in x["source"].items() if not k.endswith("_ref")}
    source["language"] = _lang_tag(resolved["source.language_ref"], x["source"]["language_ref"]).lower()

    debloat_services = []
    if x["debloat"]["enable"]:
        debloat_services = x["debloat"]["profiles"][x["debloat"]["profile"]].get("services", [])
    ssh = _ssh_inputs(merged, x, resolved)
    _check_provision(x, resolved)
    _check_apps(x)
    _check_devices(x)
    ident, ident_registry = render_identity(x, resolved)
    shell, shell_registry = render_shell(x, resolved, merged)

    out = {
        "schema": SCHEMA,
        "layers": list(layers or []),
        "spec": x,
        "resolved": resolved,
        "users": users,
        "region": {"timezone": win_tz, "ui_language": ui_lang, "input_locale": input_locale},
        "source": source,
        "feature_overrides": _feature_ids(x),
        "registry": build_registry(x) + ident_registry + shell_registry,
        "identity": ident,
        "shell": shell,
        "debloat_services": debloat_services,
        "winrm": {"enable": x["winrm"]["enable"], "remote_address": x["winrm"]["remote_address"], "port": port},
        "ssh": ssh,
    }
    # credential refs were resolved into users; keep `resolved` free of secrets
    for p in CREDENTIAL_REFS:
        out["resolved"][p] = "<credential>"
    return out


PCI_ID_RE = re.compile(r"^[0-9A-F]{4}:([0-9A-F]{4}|\*)$")


def device_tables(x: dict) -> dict:
    """[windows.xbox.devices.<name>] tables (name -> {pci: [...]}); the scalar keys of [windows.xbox.devices] are not devices."""
    return {k: v for k, v in (x.get("devices") or {}).items() if isinstance(v, dict)}


def _check_devices(x: dict) -> None:
    """xbox-bm-01: one image for VMs and bare metal. Every guest-only piece names the PCI device it needs, so SetupComplete,
    Verify, the probe and the image gate decide per device -- never on HypervisorPresent, which MiOS's own
    nested_virt_features step makes true on bare metal."""
    devs = device_tables(x)
    if not devs:
        raise SpecError(_full("devices"), "no [windows.xbox.devices.<name>] table (pci = [\"VVVV:DDDD\", ...])")
    for name, d in devs.items():
        pci = d.get("pci")
        if not isinstance(pci, list) or not pci:
            raise SpecError(_full(f"devices.{name}.pci"), "must be a non-empty list of \"VVVV:DDDD\" PCI ids")
        for p in pci:
            if not isinstance(p, str) or not PCI_ID_RE.match(p):
                raise SpecError(_full(f"devices.{name}.pci"), f"{p!r} is not \"VVVV:DDDD\" (upper-case hex) or \"VVVV:*\"")
    users = [(f"guest_tools.{g}", t) for g, t in x["guest_tools"].items() if isinstance(t, dict)]
    users.append(("drivers.looking_glass_idd", x["drivers"]["looking_glass_idd"]))
    for path, t in users:
        dev = t.get("required_device")
        if not isinstance(dev, str) or not dev:
            raise SpecError(_full(f"{path}.required_device"), "missing: every guest-only piece names the device it needs")
        if dev not in devs:
            raise SpecError(_full(f"{path}.required_device"), f"{dev!r} is not a [windows.xbox.devices] table")
        if "install" in t and not isinstance(t["install"], bool):
            raise SpecError(_full(f"{path}.install"), "must be true or false")
    if x["guest_tools"]["looking_glass_host"]["install"]:
        # exactly one Looking Glass server per IVSHMEM device (xbox-bm-37): the IDD retires the legacy host app
        raise SpecError(_full("guest_tools.looking_glass_host.install"),
                        "must be false while the Looking Glass IDD is installed: one Looking Glass server per IVSHMEM device")
    if not str(x["provision"]["mirror_task"]).replace("/", "\\").strip("\\"):
        raise SpecError(_full("provision.mirror_task"), "empty task name")


# ---------------------------------------------------------------------------
# MiOS identity (xbox-bm-39): accent, cursor scheme, font -- from the ONE SSOT value each, rendered offline
# ---------------------------------------------------------------------------

ACCENT_RE = re.compile(r"^#([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})$")
CURSOR_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*\.(cur|ani)$")
CURSOR_ASSET_RE = re.compile(r"[A-Za-z0-9][\w.\-]*\.zip")
VERSION_RE = re.compile(r"\d+\.\d+")       # an asset name carrying a version would pin the release
GITHUB_SLUG_RE = re.compile(r"[\w.-]+/[\w.-]+")
CURSOR_KEY = r"Control Panel\Cursors"
CURSOR_SCHEMES_KEY = r"Microsoft\Windows\CurrentVersion\Control Panel\Cursors\Schemes"
FONTS_KEY = r"Microsoft\Windows NT\CurrentVersion\Fonts"
DWM_KEY = r"Software\Microsoft\Windows\DWM"
ACCENT_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"
PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
OEM_KEY = r"Microsoft\Windows\CurrentVersion\OEMInformation"
SCHEME_SOURCE_SYSTEM = 2   # Control Panel\Cursors "Scheme Source": 2 = a scheme registered system-wide (HKLM)
THEME_MODES = ("dark", "light")
# Themes/WindowColor: Windows lightens or darkens an accent outside HSL luminosity 25%..75% (Microsoft's
# unattend reference), so such an SSOT accent would never be the colour the image shows.
WINDOW_COLOR_LUMINOSITY = (0.25, 0.75)
BACKGROUND_PX = 64   # a solid colour: any size scales to the screen identically; small keeps the payload small
OEM_VALUES = (("Manufacturer", "oem_manufacturer_ref"), ("Model", "oem_model_ref"),
              ("SupportURL", "oem_support_url_ref"), ("SupportHours", "oem_support_hours_ref"))
OEM_LOGO_PX = 120          # OEMInformation/Logo: 32-bit colour; Windows scales a larger bitmap to 120x120
OEM_LOGO_MAX_PATH = 256    # the Logo path's maximum length (Microsoft's unattend reference)


def solid_bmp(rgb: tuple, px: int = BACKGROUND_PX) -> bytes:
    """A px x px 24-bit BMP of one colour: byte-identical on every host (no compressor involved), so the sha256
    render_spec records is the sha256 answer.py stages and conform/probe check."""
    row = bytes((rgb[2], rgb[1], rgb[0])) * px            # BGR pixels; px * 3 is a multiple of 4: no row padding
    pixels = row * px
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, px, px, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def _rgb(value: str, key: str) -> tuple:
    m = ACCENT_RE.match(str(value))
    if not m:
        raise SpecError(key, f"{value!r} is not #RRGGBB")
    return tuple(int(v, 16) for v in m.groups())


def _luminosity(rgb: tuple) -> float:
    return (max(rgb) + min(rgb)) / 2 / 255


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_MAX_SIDE = 4096


def png_rgba(data: bytes) -> tuple:
    """(width, height, RGBA bytes) of an 8-bit RGB/RGBA non-interlaced PNG; any other PNG, or a bad chunk, is a ValueError."""
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("not a PNG file")
    pos, ihdr, idat = 8, None, []
    while pos + 12 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        end = pos + 12 + n
        if end > len(data):
            raise ValueError("truncated PNG chunk")
        tag, body = data[pos + 4:pos + 8], data[pos + 8:end - 4]
        if zlib.crc32(tag + body) != struct.unpack(">I", data[end - 4:end])[0]:
            raise ValueError(f"PNG chunk {tag!r} fails its CRC")
        if tag == b"IHDR":
            if n != 13:
                raise ValueError("malformed PNG IHDR")
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos = end
    else:
        raise ValueError("truncated PNG chunk" if pos < len(data) else "PNG has no IEND chunk")
    if ihdr is None or not idat:
        raise ValueError("PNG has no IHDR/IDAT chunk")
    w, h, depth, ctype, comp, filt, interlace = ihdr
    if depth != 8 or ctype not in (2, 6) or comp or filt or interlace:
        raise ValueError(f"unsupported PNG (bit depth {depth}, colour type {ctype}, interlace {interlace}): "
                         "only 8-bit RGB/RGBA, non-interlaced")
    if not (0 < w <= PNG_MAX_SIDE and 0 < h <= PNG_MAX_SIDE):
        raise ValueError(f"PNG size {w}x{h} is outside 1..{PNG_MAX_SIDE}")
    bpp = 4 if ctype == 6 else 3
    stride = w * bpp
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise ValueError(f"PNG image data does not inflate: {e}") from None
    if len(raw) != h * (stride + 1):
        raise ValueError("PNG image data has the wrong size")
    out = bytearray()
    prev = bytes(stride)
    for y in range(h):
        ft, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            line = bytearray((a + b) & 0xFF for a, b in zip(line, prev))
        elif ft == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                b = prev[i]
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        elif ft != 0:
            raise ValueError(f"PNG row {y} has filter type {ft}")
        out += line
        prev = bytes(line)
    if bpp == 3:
        rgb, out = out, bytearray()
        for i in range(0, len(rgb), 3):
            out += rgb[i:i + 3] + b"\xff"
    return w, h, bytes(out)


def _box_taps(n_src: int, n_dst: int) -> list:
    """Per destination pixel, [(source index, integer weight)] of the exact box filter; each row's weights sum to n_src."""
    taps = []
    for d in range(n_dst):
        lo, hi = d * n_src, (d + 1) * n_src
        taps.append([(s, min(hi, (s + 1) * n_dst) - max(lo, s * n_dst))
                     for s in range(lo // n_dst, min(n_src, -(-hi // n_dst)))
                     if min(hi, (s + 1) * n_dst) > max(lo, s * n_dst)])
    return taps


def rgba_to_bmp32(w: int, h: int, rgba: bytes, px: int, bg: tuple) -> bytes:
    """A px x px 32-bit BMP: an exact box-filter downscale of premultiplied RGBA composited over bg (integer maths only)."""
    tx, ty = _box_taps(w, px), _box_taps(h, px)
    rows = []
    for y in range(h):
        line, acc = rgba[y * w * 4:(y + 1) * w * 4], []
        for taps in tx:
            r = g = b = a = 0
            for s, wt in taps:
                aw = line[s * 4 + 3] * wt
                r += line[s * 4] * aw
                g += line[s * 4 + 1] * aw
                b += line[s * 4 + 2] * aw
                a += aw
            acc.append((r, g, b, a))
        rows.append(acc)
    full = 255 * w * h   # every output pixel's weights sum to w * h; colour sums carry one more factor of alpha (255)
    pixels = bytearray()
    for taps in reversed(ty):   # BMP rows run bottom-up
        for x in range(px):
            r = g = b = a = 0
            for s, wt in taps:
                pr, pg, pb, pa = rows[s][x]
                r, g, b, a = r + pr * wt, g + pg * wt, b + pb * wt, a + pa * wt
            rest = full - a
            pixels += bytes(((c + bg[i] * rest + full // 2) // full for i, c in ((2, b), (1, g), (0, r)))) + b"\xff"
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, px, px, 1, 32, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + bytes(pixels)


@functools.lru_cache(maxsize=4)
def oem_logo_bmp(png: bytes, px: int, bg: tuple) -> bytes:
    """The OEM logo: a square PNG mark at least px wide, rendered to a px x px 32-bit BMP over bg (byte-identical)."""
    w, h, rgba = png_rgba(png)
    if w != h or w < px:
        raise ValueError(f"the mark is {w}x{h}: it must be square and at least {px}x{px} (no stretching, no upscaling)")
    return rgba_to_bmp32(w, h, rgba, px, bg)


def font_names(data: bytes) -> dict:
    """{name id: string} from an OpenType/TrueType 'name' table (Windows platform, Unicode BMP, en-US first)."""
    if len(data) < 12:
        raise ValueError("not an OpenType file")
    num_tables = struct.unpack(">H", data[4:6])[0]
    name_off = None
    for i in range(num_tables):
        tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        if tag == b"name":
            name_off = off
    if name_off is None:
        raise ValueError("no name table")
    _, count, str_off = struct.unpack(">HHH", data[name_off:name_off + 6])
    out: dict = {}
    for i in range(count):
        pid, eid, lid, nid, ln, off = struct.unpack(">HHHHHH", data[name_off + 6 + 12 * i:name_off + 18 + 12 * i])
        if pid != 3 or eid not in (0, 1):
            continue
        raw = data[name_off + str_off + off:name_off + str_off + off + ln]
        if nid not in out or lid == 0x409:
            out[nid] = raw.decode("utf-16-be", "replace")
    return out


def _font_members(family: str, archive: str) -> list:
    """Members of the vendored font archive whose typographic family (name id 16, else 1) is the SSOT family."""
    rows = []
    with zipfile.ZipFile(archive) as z:
        for m in sorted(z.namelist()):
            base = os.path.basename(m)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*\.(otf|ttf)", base, re.I):
                continue
            data = z.read(m)
            names = font_names(data)
            fam = names.get(16) or names.get(1) or ""
            if fam != family:
                continue
            full = names.get(4) or base.rsplit(".", 1)[0]
            kind = "OpenType" if base.lower().endswith(".otf") else "TrueType"
            rows.append({"member": m, "file": base, "full_name": full, "registry_name": f"{full} ({kind})",
                         "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return rows


def _oem_logo_bitmap(src_rel: str, bg: tuple, tree_root: str, key: str) -> tuple:
    """(mark PNG bytes, 32-bit logo BMP) for a tree-relative mark; a SpecError names `key`."""
    src = os.path.join(tree_root, *src_rel.split("/"))
    if not src_rel or src_rel.startswith("/") or ".." in src_rel.split("/") or not os.path.isfile(src):
        raise SpecError(key, f"{src_rel!r} is not a file in the tree")
    with open(src, "rb") as fh:
        mark = fh.read()
    try:
        return mark, oem_logo_bmp(mark, OEM_LOGO_PX, bg)
    except ValueError as e:
        raise SpecError(key, f"{src_rel}: {e}") from None


OEM_LOGO_SIDECAR = "mios-oem-logo/1"   # schema of the projection's .json sidecar


def oem_logo_projection(merged: dict, tree_root: str = _TREE) -> dict:
    """{tree path: bytes}: the logo every image renders, from the vendor SSOT, plus its .json sidecar."""
    idn = _get(merged, f"{ROOT}.identity")
    if not isinstance(idn, dict):
        raise SpecError(_full("identity"), "missing")
    rel = str(idn.get("oem_logo_projection") or "").strip()
    parts = rel.split("/")
    if not rel.endswith(".bmp") or rel.startswith("/") or "\\" in rel or ".." in parts or "" in parts:
        raise SpecError(_full("identity.oem_logo_projection"), f"{rel!r} must be a tree-relative .bmp path")
    bg_key, src_key = idn["background_color_ref"], idn["oem_logo_source_ref"]
    bg = _rgb(resolve_ref(merged, "identity.background_color_ref", bg_key), bg_key)
    src_rel = str(resolve_ref(merged, "identity.oem_logo_source_ref", src_key)).strip().replace("\\", "/")
    mark, logo = _oem_logo_bitmap(src_rel, bg, tree_root, src_key)
    sidecar = {"schema": OEM_LOGO_SIDECAR, "file": parts[-1], "sha256": hashlib.sha256(logo).hexdigest(), "size": len(logo),
               "px": OEM_LOGO_PX, "background": "#%02X%02X%02X" % bg, "background_key": bg_key, "source": src_rel,
               "source_sha256": hashlib.sha256(mark).hexdigest()}
    return {rel: logo, rel + ".json": (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode()}


def _vendor_merged(tree_root: str) -> dict:
    vendor = os.environ.get("MIOS_VENDOR_TOML") or os.path.join(tree_root, "usr", "share", "mios", "mios.toml")
    return mios_toml.load_merged([vendor])


def check_oem_logo_projection(tree_root: str = _TREE, merged: dict | None = None) -> list:
    """Problems with the tracked projection: each file must equal its render from the vendor SSOT."""
    bad = []
    for rel, want in oem_logo_projection(merged if merged is not None else _vendor_merged(tree_root), tree_root).items():
        try:
            with open(os.path.join(tree_root, *rel.split("/")), "rb") as fh:
                have = fh.read()
        except OSError:
            bad.append(f"{rel}: missing; regenerate with render_spec.py --write-oem-logo")
            continue
        if have != want:
            bad.append(f"{rel}: sha256 {hashlib.sha256(have).hexdigest()} differs from its render "
                       f"{hashlib.sha256(want).hexdigest()}; regenerate with render_spec.py --write-oem-logo")
    return bad


def write_oem_logo_projection(tree_root: str = _TREE) -> list:
    """Regenerate the tracked projection in place; returns the paths written."""
    written = []
    for rel, data in oem_logo_projection(_vendor_merged(tree_root), tree_root).items():
        path = os.path.join(tree_root, *rel.split("/"))
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        written.append(rel)
    return written


def render_identity(x: dict, resolved: dict, tree_root: str = _TREE) -> tuple:
    """(identity record, registry items) for the MiOS identity (xbox-bm-39). Every value comes from the shared SSOT key
    the [windows.xbox.identity] refs name; the cursor build floats to its newest release (resolved and recorded at build
    time) and is checked against its own install.inf at payload time; the font archive is the vendored one, verified
    against its SSOT sha256 here."""
    idn = x["identity"]
    accent = resolved["identity.accent_ref"]
    r, g, b = _rgb(accent, idn["accent_ref"])
    abgr = (0xFF << 24) | (b << 16) | (g << 8) | r   # DWM AccentColor / Explorer AccentColorMenu: 0xAABBGGRR
    argb = (0xFF << 24) | (r << 16) | (g << 8) | b   # unattend Themes/WindowColor: 0xAARRGGBB
    lo, hi = WINDOW_COLOR_LUMINOSITY
    if not lo <= _luminosity((r, g, b)) <= hi:
        raise SpecError(idn["accent_ref"], f"{accent!r} has HSL luminosity {_luminosity((r, g, b)):.0%}: Windows replaces a "
                        f"WindowColor outside {lo:.0%}..{hi:.0%}, so the image would not show the SSOT accent")

    cur = resolved["identity.cursor_ref"]
    if not isinstance(cur, dict):
        raise SpecError(_full("identity.cursor_ref"), "must reference a table ([theme.cursor_windows])")
    for k in ("channel", "asset", "variant_dir", "scheme_name", "install_dir", "scheme_files", "roles", "base_size"):
        if cur.get(k) in (None, "", [], {}):
            raise SpecError(f"{idn['cursor_ref']}.{k}", "missing")
    # floating (ADR-0003 a): the newest release's asset, resolved and recorded at build time; the SSOT never pins it
    if cur["channel"] != "latest":
        raise SpecError(f"{idn['cursor_ref']}.channel", f"{cur['channel']!r} pins a release; the Windows build floats ('latest')")
    asset = str(cur["asset"])
    if not CURSOR_ASSET_RE.fullmatch(asset) or VERSION_RE.search(asset):
        raise SpecError(f"{idn['cursor_ref']}.asset", f"{asset!r} must be a plain .zip release-asset name with no version")
    repo = str(resolved["identity.cursor_repo_ref"]).strip()
    if not GITHUB_SLUG_RE.fullmatch(repo):
        raise SpecError(idn["cursor_repo_ref"], f"{repo!r} is not a GitHub owner/name repository")
    files = [str(f) for f in cur["scheme_files"]]
    roles = {str(k): str(v) for k, v in cur["roles"].items()}
    for f in set(files) | set(roles.values()):
        if not CURSOR_FILE_RE.match(f):
            raise SpecError(f"{idn['cursor_ref']}", f"cursor file {f!r} is not a plain .cur/.ani name")
    if not set(roles.values()) <= set(files):
        raise SpecError(f"{idn['cursor_ref']}.roles", f"names files outside scheme_files: {sorted(set(roles.values()) - set(files))}")
    size = cur["base_size"]
    if isinstance(size, bool) or not isinstance(size, int) or not 16 <= size <= 256:
        raise SpecError(f"{idn['cursor_ref']}.base_size", "must be an integer 16..256")
    theme = str(resolved["identity.cursor_theme_ref"])
    if not str(cur["scheme_name"]).startswith(theme) or not str(cur["variant_dir"]).startswith(theme):
        raise SpecError(f"{idn['cursor_ref']}", f"the Windows build is not of the SSOT cursor theme {theme!r} (parity)")
    root_rel = str(idn["system_root"]).strip("\\/").replace("/", "\\")
    sysroot = "C:\\" + root_rel
    install_dir = str(cur["install_dir"]).strip("\\/").replace("/", "\\")
    if ".." in install_dir.split("\\"):
        raise SpecError(f"{idn['cursor_ref']}.install_dir", "must stay under the system root")
    live = {f: f"{sysroot}\\{install_dir}\\{f}" for f in files}

    font = resolved["identity.font_ref"]
    if not isinstance(font, dict) or not font.get("family") or not font.get("native_archive") or not font.get("native_sha256"):
        raise SpecError(_full("identity.font_ref"), "must reference [theme.font] with family, native_archive and native_sha256")
    archive = os.path.join(tree_root, str(font["native_archive"]).replace("/", os.sep))
    if not os.path.isfile(archive):
        raise SpecError(f"{idn['font_ref']}.native_archive", f"{font['native_archive']} is not in the tree")
    with open(archive, "rb") as fh:
        got = hashlib.sha256(fh.read()).hexdigest()
    if got != str(font["native_sha256"]).lower():
        raise SpecError(f"{idn['font_ref']}.native_sha256", "the vendored font archive does not match its pinned sha256")
    fonts = _font_members(str(font["family"]), archive)
    if not fonts:
        raise SpecError(f"{idn['font_ref']}.family", f"no font of family {font['family']!r} in {font['native_archive']}")
    font_dir = str(idn["font_dir"]).strip("\\/").replace("\\", "/")

    # the persistent MiOS theme (unattend Themes) and the Default profile's colour mode
    theme_name = str(resolved["identity.theme_name_ref"]).strip()
    if not theme_name or len(theme_name) > 259:
        raise SpecError(idn["theme_name_ref"], "must be a theme name of 1..259 characters")
    mode = str(resolved["identity.theme_mode_ref"]).strip().lower()
    if mode not in THEME_MODES:
        raise SpecError(idn["theme_mode_ref"], f"{mode!r} is not one of {THEME_MODES}")
    transparency = resolved["identity.transparency_ref"]
    if not isinstance(transparency, bool):
        raise SpecError(idn["transparency_ref"], "must be a boolean")
    bg = _rgb(resolved["identity.background_color_ref"], idn["background_color_ref"])
    bg_rel = str(idn["background"]).strip("\\/").replace("/", "\\")
    parts = bg_rel.split("\\")
    if parts[0].lower() != root_rel.lower() or ".." in parts or len(parts) < 3 or not bg_rel.lower().endswith(".bmp"):
        raise SpecError(_full("identity.background"), "must be a .bmp in a folder under system_root (Themes/DesktopBackground "
                        "must live in a subfolder of %WINDIR%)")
    bmp = solid_bmp(bg)
    background = {"path": bg_rel.replace("\\", "/"), "unattend": "%WINDIR%\\" + "\\".join(parts[1:]),
                  "color": "#%02X%02X%02X" % bg, "px": BACKGROUND_PX, "sha256": hashlib.sha256(bmp).hexdigest(), "size": len(bmp)}

    # OEM branding: every OEMInformation value from its one [branding] key
    oem = {}
    for value_name, ref in OEM_VALUES:
        v = resolved[f"identity.{ref}"]
        if not isinstance(v, str) or not v.strip():
            raise SpecError(idn[ref], "must be a non-empty string")
        oem[value_name] = v.strip()
    # OEMInformation/Logo: the MiOS mark as a 32-bit bitmap (Microsoft's 120x120 limit), at the [branding] path
    logo_live = str(resolved["identity.oem_logo_ref"]).strip().replace("/", "\\")
    logo_rel = logo_live[3:] if re.match(r"(?i)^C:\\", logo_live) else ""
    lparts = logo_rel.split("\\")
    if (not logo_rel or len(logo_live) > OEM_LOGO_MAX_PATH or not logo_rel.lower().endswith(".bmp") or len(lparts) < 3
            or lparts[0].lower() != root_rel.lower() or ".." in lparts or "" in lparts):
        raise SpecError(idn["oem_logo_ref"], f"{logo_live!r} must be a C:\\{root_rel}\\<folder>\\...\\<name>.bmp path of at "
                        f"most {OEM_LOGO_MAX_PATH} characters")
    src_rel = str(resolved["identity.oem_logo_source_ref"]).strip().replace("\\", "/")
    mark, logo = _oem_logo_bitmap(src_rel, bg, tree_root, idn["oem_logo_source_ref"])
    oem_logo = {"path": logo_rel.replace("\\", "/"), "live": logo_live, "source": src_rel,
                "source_sha256": hashlib.sha256(mark).hexdigest(), "px": OEM_LOGO_PX, "background": "#%02X%02X%02X" % bg,
                "sha256": hashlib.sha256(logo).hexdigest(), "size": len(logo)}

    reg: list = []

    def add(owner: str, hive: str, key: str, name: str, typ: str, data) -> None:
        reg.append({"owner": owner, "hive": hive, "key": key, "name": name, "type": typ, "data": data})

    # accent: WindowColor drives Windows' palette; the Default profile pins only the values Windows keeps as given
    add("identity.accent.dwm_accent", "DEFAULT_USER", DWM_KEY, "AccentColor", "REG_DWORD", abgr)
    add("identity.accent.dwm_prevalence", "DEFAULT_USER", DWM_KEY, "ColorPrevalence", "REG_DWORD", 1)
    add("identity.accent.menu", "DEFAULT_USER", ACCENT_KEY, "AccentColorMenu", "REG_DWORD", abgr)
    add("identity.accent.prevalence", "DEFAULT_USER", PERSONALIZE_KEY, "ColorPrevalence", "REG_DWORD", 1)
    # colour mode + transparency of every new profile (Themes/UWPAppsUseLightTheme covers apps only, not the system)
    light = 1 if mode == "light" else 0
    add("identity.theme.apps_light", "DEFAULT_USER", PERSONALIZE_KEY, "AppsUseLightTheme", "REG_DWORD", light)
    add("identity.theme.system_light", "DEFAULT_USER", PERSONALIZE_KEY, "SystemUsesLightTheme", "REG_DWORD", light)
    add("identity.theme.transparency", "DEFAULT_USER", PERSONALIZE_KEY, "EnableTransparency", "REG_DWORD", int(transparency))
    # cursor: the scheme as the Default profile's active cursors and a system-wide scheme
    add("identity.cursor.scheme_name", "DEFAULT_USER", CURSOR_KEY, "", "REG_SZ", str(cur["scheme_name"]))
    add("identity.cursor.scheme_source", "DEFAULT_USER", CURSOR_KEY, "Scheme Source", "REG_DWORD", SCHEME_SOURCE_SYSTEM)
    add("identity.cursor.base_size", "DEFAULT_USER", CURSOR_KEY, "CursorBaseSize", "REG_DWORD", size)
    for role in sorted(roles):
        add(f"identity.cursor.role.{role}", "DEFAULT_USER", CURSOR_KEY, role, "REG_SZ", live[roles[role]])
    add("identity.cursor.system_scheme", "SOFTWARE", CURSOR_SCHEMES_KEY, str(cur["scheme_name"]), "REG_SZ",
        ",".join(live[f] for f in files))
    # font: every member of the SSOT family, registered system-wide
    for f in fonts:
        add(f"identity.font.{f['file']}", "SOFTWARE", FONTS_KEY, f["registry_name"], "REG_SZ", f["file"])
    # OEM branding (Settings > System > About)
    for value_name, ref in OEM_VALUES:
        add(f"identity.oem.{ref[4:-4]}", "SOFTWARE", OEM_KEY, value_name, "REG_SZ", oem[value_name])
    add("identity.oem.logo", "SOFTWARE", OEM_KEY, "Logo", "REG_SZ", logo_live)

    record = {
        "accent": str(accent), "accent_abgr": abgr, "accent_argb": argb,
        "theme": {"name": theme_name, "mode": mode, "window_color": "0x%08x" % argb, "uwp_apps_use_light_theme": mode == "light",
                  "transparency": transparency, "background": background},
        "oem": oem, "oem_logo": oem_logo,
        "cursor": {"theme": theme, "repo": repo, "channel": "latest", "asset": asset,
                   "variant_dir": str(cur["variant_dir"]), "scheme_name": str(cur["scheme_name"]),
                   "install_dir": install_dir, "scheme_files": files, "roles": roles, "base_size": size,
                   "dest_dir": (root_rel + "/" + install_dir).replace("\\", "/")},
        "font": {"family": str(font["family"]), "archive": str(font["native_archive"]), "archive_sha256": got,
                 "dest_dir": font_dir, "files": fonts},
    }
    return record, reg


# ---------------------------------------------------------------------------
# MiOS Windows shell (xbox-bm-39): PowerShell 7, oh-my-posh, fastfetch, the WT profile + scheme, the all-users profile
# ---------------------------------------------------------------------------

SHELL_TOOLS = ("pwsh", "oh_my_posh", "fastfetch")
SHELL_LOCAL_VERBS = ("dash", "help")   # the `mios` verbs the image itself implements; the rest need the host layer
PS51_PROFILE = r"Windows\System32\WindowsPowerShell\v1.0\profile.ps1"
WT_FRAGMENTS = r"ProgramData\Microsoft\Windows Terminal\Fragments"
WT_CURSOR_SHAPES = ("bar", "vintage", "underscore", "filledBox", "emptyBox", "doubleUnderscore")
EXECUTION_POLICY_KEY = r"Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell"
EXECUTION_POLICIES = ("RemoteSigned",)   # PowerShell 7's MSI default; MiOS never sets a machine-wide Bypass
GITHUB_REPO_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+?(?:\.git)?/?")
MSI_PROPERTY_RE = re.compile(r"[A-Z][A-Z0-9_]*=[\w.\-]*")
VERB_RE = re.compile(r"[a-z][a-z0-9_-]*")


def _rel(path: str, what: str) -> str:
    """An image-relative Windows path: no drive, no '..', backslashes."""
    p = re.sub(r"^[A-Za-z]:", "", str(path)).replace("/", "\\").strip("\\")
    if not p or ".." in p.split("\\"):
        raise SpecError(what, f"{path!r} is not an image-relative path")
    return p


def _psq(v: object) -> str:
    """PowerShell single-quoted literal."""
    return "'" + str(v).replace("'", "''") + "'"


def shell_file_bytes(entry: dict, tree_root: str = _TREE) -> bytes:
    """The exact bytes of one shell file of the rendered spec: rendered text in its encoding, or the tree source."""
    if "text" in entry:
        return str(entry["text"]).encode(entry["encoding"])
    with open(os.path.join(tree_root, *str(entry["source"]).split("/")), "rb") as fh:
        return fh.read()


def _wt():
    """usr/libexec/mios/win/wt_profile_inject.py: the ONE Windows Terminal colour-scheme/edge projection of the SSOT."""
    import importlib.util
    name = "mios_wt_profile_inject"
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(_TREE, "usr", "libexec", "mios", "win", "wt_profile_inject.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod   # its dataclasses resolve their module through sys.modules
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        del sys.modules[name]
        raise
    return mod


def _fastfetch_config(text: str, theme: dict, logo_path: str, what: str) -> str:
    """The MiOS fastfetch config for Windows: the logo source points at the staged logo and the [theme.fastfetch]
    colours/padding are substituted, as Get-MiOS's Install-MiOSFastfetch does. Each value must occur exactly once."""
    for k in ("logo_color", "keys_color", "title_color", "output_color"):
        if not re.fullmatch(r"[a-z_]+", str(theme.get(k, ""))):
            raise SpecError(f"theme.fastfetch.{k}", "must be an ANSI colour name")
    for k in ("logo_padding_top", "logo_padding_right", "logo_padding_left"):
        if isinstance(theme.get(k), bool) or not isinstance(theme.get(k), int) or theme[k] < 0:
            raise SpecError(f"theme.fastfetch.{k}", "must be a non-negative integer")
    subs = [
        (r'("logo"\s*:\s*\{[^{}]*?"source"\s*:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(logo_path)),
        (r'("color"\s*:\s*\{\s*"1"\s*:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(theme["logo_color"])),
        (r'"padding"\s*:\s*\{[^{}]*\}', lambda m: '"padding": {{ "top": {}, "right": {}, "left": {} }}'.format(
            theme["logo_padding_top"], theme["logo_padding_right"], theme["logo_padding_left"])),
        (r'("keys"\s*:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(theme["keys_color"])),
        (r'("title"\s*:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(theme["title_color"])),
        (r'("output"\s*:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(theme["output_color"])),
    ]
    for pat, fn in subs:
        text, n = re.subn(pat, fn, text, flags=re.S)
        if n != 1:
            raise SpecError(what, f"the MiOS fastfetch config has {n} matches for {pat!r} (expected exactly one)")
    return text


def render_shell(x: dict, resolved: dict, merged: dict, tree_root: str = _TREE) -> tuple:
    """(shell record, registry items) for the MiOS Windows shell (xbox-bm-39). Every file is rendered here from the
    SSOT with its exact bytes and sha256 (answer.py stages them, conform/probe check them); the binaries are resolved
    at build time (resolve_latest.py) and only described here."""
    sh = x["shell"]
    dest_rel = _rel(x["components"]["dest_dir"], _full("components.dest_dir"))
    shell_rel = dest_rel + "\\" + _rel(sh["dir"], _full("shell.dir"))
    bin_rel = dest_rel + "\\" + _rel(sh["bin_dir"], _full("shell.bin_dir"))
    tools: dict = {}
    for t in SHELL_TOOLS:
        tt = sh[t]
        if not GITHUB_REPO_RE.fullmatch(str(tt["upstream"])):
            raise SpecError(_full(f"shell.{t}.upstream"), "must be a GitHub repository URL (releases + asset digest)")
        if tt["channel"] != "latest" or re.search(r"\d+\.\d+", str(tt["asset"])) or not str(tt["asset"]).strip():
            raise SpecError(_full(f"shell.{t}"), "channel must be 'latest' and asset a glob without a version (no pins)")
        tools[t] = {"upstream": str(tt["upstream"]), "asset": str(tt["asset"])}
    for t in ("oh_my_posh", "fastfetch"):
        if not re.fullmatch(r"[A-Za-z0-9][\w.\-]*\.exe", str(sh[t]["exe"])):
            raise SpecError(_full(f"shell.{t}.exe"), "must be a plain .exe file name")
    pw = sh["pwsh"]
    pwsh_rel = _rel(pw["install_dir"], _full("shell.pwsh.install_dir"))
    if not all(isinstance(p, str) and MSI_PROPERTY_RE.fullmatch(p) for p in pw["msi_properties"]):
        raise SpecError(_full("shell.pwsh.msi_properties"), "must be NAME=value MSI public properties")
    if pw["install_at"] != "SetupComplete" or not str(pw["signer"]).strip():
        raise SpecError(_full("shell.pwsh"), "install_at must be SetupComplete and signer a non-empty subject")
    tools["pwsh"].update(signer=str(pw["signer"]), install_dir=pwsh_rel.replace("\\", "/"),
                         msi_properties=list(pw["msi_properties"]), exe=pwsh_rel.replace("\\", "/") + "/pwsh.exe")
    tools["oh_my_posh"]["path"] = (bin_rel + "\\" + sh["oh_my_posh"]["exe"]).replace("\\", "/")
    tools["fastfetch"].update(dest_dir=(bin_rel + "\\fastfetch").replace("\\", "/"),
                              path=(bin_rel + "\\fastfetch\\" + sh["fastfetch"]["exe"]).replace("\\", "/"))
    policy = str(sh["execution_policy"])
    if policy not in EXECUTION_POLICIES:
        raise SpecError(_full("shell.execution_policy"), f"{policy!r}: MiOS sets {EXECUTION_POLICIES} (never a machine-wide "
                        "Bypass/Unrestricted)")

    # Windows Terminal: a machine-wide fragment with the MiOS profile and colour scheme
    frag_rel = _rel(sh["wt_fragment"], _full("shell.wt_fragment"))
    if not frag_rel.lower().startswith(WT_FRAGMENTS.lower() + "\\") or len(frag_rel.split("\\")) != len(WT_FRAGMENTS.split("\\")) + 2 \
            or not frag_rel.lower().endswith(".json"):
        raise SpecError(_full("shell.wt_fragment"), f"must be {WT_FRAGMENTS}\\<app>\\<name>.json (a machine-wide WT fragment)")
    wt = _wt()
    scheme = wt.color_scheme(merged)
    padding, scrollbar = wt.wt_edge(merged)
    profile_name = str(resolved["shell.profile_name_ref"]).strip()
    scheme_name = str(resolved["shell.scheme_name_ref"]).strip()
    if not profile_name or scheme["name"] != scheme_name:
        raise SpecError(_full("shell.scheme_name_ref"), "the terminal profile/scheme names must be the [theme.terminal] ones")
    look, font = resolved["shell.look_ref"], resolved["shell.font_ref"]
    if not isinstance(look, dict) or not isinstance(font, dict):
        raise SpecError(_full("shell.look_ref"), "look_ref/font_ref must reference tables ([theme], [theme.font])")
    opacity = look.get("opacity")
    if isinstance(opacity, bool) or not isinstance(opacity, int) or not 0 <= opacity <= 100:
        raise SpecError("theme.opacity", "must be an integer 0..100")
    if not isinstance(look.get("acrylic"), bool) or not isinstance(look.get("suppress_app_title"), bool):
        raise SpecError("theme", "acrylic and suppress_app_title must be booleans")
    if look.get("cursor_shape") not in WT_CURSOR_SHAPES:
        raise SpecError("theme.cursor_shape", f"must be one of {WT_CURSOR_SHAPES}")
    size = font.get("size")
    if not str(font.get("family") or "").strip() or isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        raise SpecError(_full("shell.font_ref"), "must reference [theme.font] with a family and a positive size")
    guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, "mios:windows-terminal:" + profile_name)) + "}"   # stable per name
    fragment = {
        "profiles": [{
            "guid": guid, "name": profile_name, "hidden": False,
            "commandline": f'"C:\\{pwsh_rel}\\pwsh.exe" -NoLogo', "startingDirectory": "%USERPROFILE%",
            "colorScheme": scheme_name, "font": {"face": str(font["family"]), "size": size},
            "opacity": opacity, "useAcrylic": look["acrylic"], "cursorShape": look["cursor_shape"],
            "padding": padding, "scrollbarState": scrollbar, "suppressApplicationTitle": look["suppress_app_title"],
        }],
        "schemes": [scheme],
    }
    files = [{"name": "wt_fragment", "path": frag_rel.replace("\\", "/"), "encoding": "utf-8",
              "text": json.dumps(fragment, indent=2, sort_keys=True) + "\n"}]

    # the tool configs: the vendored oh-my-posh config as is; the fastfetch config with the Windows logo + theme
    omp_src = str(resolved["shell.prompt_config_ref"]).lstrip("/")
    ff_src = "usr/share/mios/" + str(resolved["shell.fastfetch_config_ref"]).lstrip("/")
    for src, key in ((omp_src, "shell.prompt_config_ref"), (ff_src, "shell.fastfetch_config_ref")):
        if not os.path.isfile(os.path.join(tree_root, *src.split("/"))):
            raise SpecError(_full(key), f"{src} is not in the tree")
    with open(os.path.join(tree_root, *ff_src.split("/")), encoding="utf-8") as fh:
        ff_text = fh.read()
    m = re.search(r'"logo"\s*:\s*\{[^{}]*?"source"\s*:\s*"(/usr/share/mios/[^"]+)"', ff_text, re.S)
    if not m or not os.path.isfile(os.path.join(tree_root, *m.group(1).lstrip("/").split("/"))):
        raise SpecError(_full("shell.fastfetch_config_ref"), "the fastfetch config's logo source is not a /usr/share/mios file in the tree")
    logo_src = m.group(1).lstrip("/")
    logo_name = os.path.basename(logo_src)
    theme_ff = resolved["shell.fastfetch_theme_ref"]
    if not isinstance(theme_ff, dict):
        raise SpecError(_full("shell.fastfetch_theme_ref"), "must reference [theme.fastfetch]")
    ff_rendered = _fastfetch_config(ff_text, theme_ff, ("C:\\" + shell_rel + "\\" + logo_name).replace("\\", "/"),
                                    _full("shell.fastfetch_config_ref"))
    files += [
        {"name": "omp_config", "path": (shell_rel + "\\mios.omp.json").replace("\\", "/"), "source": omp_src},
        {"name": "fastfetch_logo", "path": (shell_rel + "\\" + logo_name).replace("\\", "/"), "source": logo_src},
        {"name": "fastfetch_config", "path": (shell_rel + "\\fastfetch.jsonc").replace("\\", "/"), "encoding": "utf-8",
         "text": ff_rendered},
    ]

    # the MiOS profile (UTF-8 with BOM: Windows PowerShell 5.1 reads a BOM-less script as ANSI) and its two hooks
    table = resolved["shell.verbs_ref"]
    if not isinstance(table, dict):
        raise SpecError(_full("shell.verbs_ref"), "must reference the [verbs] table")
    # the `mios <verb>` CLI catalogue: the [verbs] entries with a surface (the agent tool verbs share the table)
    verbs = {n: v for n, v in table.items() if isinstance(v, dict) and "surface" in v}
    if not verbs:
        raise SpecError(_full("shell.verbs_ref"), "[verbs] has no `mios <verb>` entries (name = { description, surface })")
    for name, v in verbs.items():
        if not VERB_RE.fullmatch(str(name)) or not isinstance(v, dict) or not str(v.get("description") or "").strip():
            raise SpecError(f"verbs.{name}", "every verb needs a lower-case name and a description")
    missing = [v for v in SHELL_LOCAL_VERBS if v not in verbs]
    if missing:
        raise SpecError(_full("shell.verbs_ref"), f"[verbs] lacks the verbs the image implements: {missing}")
    shell_abs, bin_abs = "C:\\" + shell_rel, "C:\\" + bin_rel
    lines = [
        "# MiOS all-users PowerShell profile (MiOS-Xbox image, xbox-bm-39). Rendered by render_spec.py from the SSOT",
        "# ([windows.xbox.shell], [verbs], [theme.prompt]); re-render, never edit. Dot-sourced by the all-users profile of",
        "# Windows PowerShell 5.1 and of PowerShell 7; a per-user profile (Get-MiOS) runs after it and may redefine both.",
        f"$MiOSShellDir = {_psq(shell_abs)}",
        f"$MiOSBinDir = {_psq(bin_abs)}",
        "try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}",
        "$OutputEncoding = [Text.UTF8Encoding]::new($false)",
        "foreach ($d in @($MiOSBinDir, (Join-Path $MiOSBinDir 'fastfetch'))) {",
        "    if ((Test-Path -LiteralPath $d) -and (@($env:PATH -split ';') -notcontains $d)) { $env:PATH = $d + ';' + $env:PATH }",
        "}",
        f"$MiOSPrompt = Join-Path $MiOSBinDir {_psq(sh['oh_my_posh']['exe'])}",
        "if ([Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost' -and (Test-Path -LiteralPath $MiOSPrompt)) {",
        "    $MiOSPromptShell = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }",
        "    try { & $MiOSPrompt init $MiOSPromptShell --config (Join-Path $MiOSShellDir 'mios.omp.json') | Invoke-Expression } catch {}",
        "}",
        "$MiOSVerbs = [ordered]@{",
        *[f"    {_psq(n)} = {_psq(str(v['description']).strip())}" for n, v in verbs.items()],
        "}",
        "function global:mios {",
        "    param([Parameter(Position = 0)][string]$Verb = 'help', [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)",
        "    switch ($Verb) {",
        f"        'dash' {{ & (Join-Path $MiOSBinDir {_psq('fastfetch' + chr(92) + sh['fastfetch']['exe'])}) -c (Join-Path $MiOSShellDir 'fastfetch.jsonc') @Rest; return }}",
        "        'help' { $MiOSVerbs.GetEnumerator() | ForEach-Object { '{0,-8} {1}' -f $_.Key, $_.Value }; return }",
        "        default {",
        "            if ($MiOSVerbs.Contains($Verb)) { Write-Host ('mios {0}: needs the MiOS host layer on this machine (Get-MiOS). {1}' -f $Verb, $MiOSVerbs[$Verb]); return }",
        "            Write-Error (\"mios: unknown verb '{0}' (mios help lists every verb)\" -f $Verb)",
        "        }",
        "    }",
        "}",
    ]
    profile_rel = shell_rel + "\\profile.ps1"
    files.append({"name": "profile", "path": profile_rel.replace("\\", "/"), "encoding": "utf-8-sig", "text": "\r\n".join(lines) + "\r\n"})
    hooks = [_rel(p, _full("shell.all_users_profiles")) for p in sh["all_users_profiles"]]
    want_hooks = {PS51_PROFILE.lower(), (pwsh_rel + "\\profile.ps1").lower()}
    if {h.lower() for h in hooks} != want_hooks or len(hooks) != 2:
        raise SpecError(_full("shell.all_users_profiles"), f"must be the all-users profiles of Windows PowerShell 5.1 and "
                        f"PowerShell 7: {sorted(want_hooks)}")
    for h in hooks:
        files.append({"name": "profile_hook:" + ("pwsh" if h.lower().startswith(pwsh_rel.lower()) else "powershell"),
                      "path": h.replace("\\", "/"), "encoding": "utf-8-sig",
                      "text": "\r\n".join(["# MiOS all-users profile hook (xbox-bm-39). Rendered by render_spec.py; re-render, never edit.",
                                           f"$MiOSProfile = {_psq('C:' + chr(92) + profile_rel)}",
                                           "if (Test-Path -LiteralPath $MiOSProfile) { . $MiOSProfile }"]) + "\r\n"})
    for f in files:
        data = shell_file_bytes(f, tree_root)
        f["sha256"], f["size"] = hashlib.sha256(data).hexdigest(), len(data)

    registry = [{"owner": "shell.execution_policy", "hive": "SOFTWARE", "key": EXECUTION_POLICY_KEY,
                 "name": "ExecutionPolicy", "type": "REG_SZ", "data": policy}]
    record = {"files": files, "tools": tools, "verbs": list(verbs), "local_verbs": list(SHELL_LOCAL_VERBS),
              "profile_name": profile_name, "scheme_name": scheme_name, "execution_policy": policy,
              "shell_dir": shell_rel.replace("\\", "/"), "bin_dir": bin_rel.replace("\\", "/")}
    return record, registry


def _check_accounts(merged: dict, x: dict, resolved: dict) -> int:
    """Users per operator decision, cross-checked with mios_credentials.resolve (the ONE credential
    resolver every MiOS image pipeline uses): a separate desktop admin carries the persistent
    AutoLogon; it is never the service account nor the RID-500 Administrator; its password is the
    resolver's. Returns the AutoLogon LogonCount. Errors name keys, never values."""
    try:
        cred = mios_credentials.resolve(merged)
    except mios_credentials.CredentialError as exc:
        raise SpecError("identity/autounattend", str(exc)) from None
    d = x["users"]["desktop"]
    name = str(resolved["users.desktop.name_ref"]).strip()
    svc = str(resolved["users.service_account_ref"]).strip()
    if not name:
        raise SpecError(x["users"]["desktop"]["name_ref"], "empty desktop account name")
    if name.lower() == svc.lower():
        raise SpecError(_full("users.desktop.name_ref"),
                        f"resolves to the service account ({x['users']['service_account_ref']}): AutoLogon never signs in the service account")
    if name.lower() in RID500_NAMES:
        raise SpecError(_full("users.desktop.name_ref"), "resolves to the built-in Administrator (RID 500), which is disabled at finalize")
    if len(name) > 20 or any(c in name for c in '"/\\[]:;|=,+*?<>@'):
        raise SpecError(_full("users.desktop.name_ref"), "not a valid Windows local account name")
    al = cred["autologon"]
    if d["autologon"]:
        if not al["enable"]:
            raise SpecError("autounattend.autologon_enable", f"false, but {_full('users.desktop.autologon')} = true")
        if str(al["user"]).lower() != name.lower():
            raise SpecError("autounattend.autologon_user",
                            f"the credential resolver signs in {al['user']!r}, the spec desktop account is {name!r}")
        if not hmac.compare_digest(str(al["password"]).encode(), str(resolved["users.desktop.password_ref"]).encode()):
            raise SpecError(_full("users.desktop.password_ref"), "differs from the credential resolver's AutoLogon password")
        if bool(al["persistent"]) != bool(d["autologon_persistent"]):
            raise SpecError(_full("users.desktop.autologon_persistent"), "disagrees with autounattend.autologon_persistent")
    if "admin" in x["users"]:
        adm_x = x["users"]["admin"]
        adm_name = str(resolved.get("users.admin.name_ref", "")).strip()
        if not adm_name:
            raise SpecError(adm_x.get("name_ref", "users.admin.name_ref"), "empty admin account name")
        if adm_name.lower() == svc.lower():
            raise SpecError(_full("users.admin.name_ref"),
                            f"resolves to the service account ({x['users']['service_account_ref']})")
        if adm_name.lower() in RID500_NAMES:
            raise SpecError(_full("users.admin.name_ref"),
                            "resolves to the built-in Administrator (RID 500), which is disabled at finalize")
        if adm_name.lower() == name.lower():
            raise SpecError(_full("users.admin.name_ref"),
                            "elevation admin cannot be the same account as the standard gaming user")
        if len(adm_name) > 20 or any(c in adm_name for c in '"/\\[]:;|=,+*?<>@'):
            raise SpecError(_full("users.admin.name_ref"), "not a valid Windows local account name")
    return int(al["count"]) if d["autologon"] else 0


def _operator_home() -> str:
    """Home of the operator the build runs for: under sudo the invoking user's (build.build_layers does the
    same for the user SSOT tier), else the caller's."""
    if hasattr(os, "geteuid") and os.geteuid() == 0 and os.environ.get("SUDO_USER"):
        import pwd
        try:
            return pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir
        except KeyError:
            pass
    return os.path.expanduser("~")


def _ssh_inputs(merged: dict, x: dict, resolved: dict) -> dict:
    """F4 inputs the image carries for mios-ssh-setup.ps1 (no GitHub, no Windows Update at provisioning):
    the SSOT SSH port and [ssh.windows] settings, and the operator public keys -- [windows.xbox.ssh].
    authorized_keys_key (an SSOT value, "" allowed) plus authorized_keys_files (globs in the operator's home).
    Public keys only (a private key or junk line is refused); none at all fails the render, naming the keys."""
    import glob
    s = x["ssh"]
    if s["install"] not in SSH_INSTALL_MODES:
        raise SpecError(_full("ssh.install"), f"must be one of {SSH_INSTALL_MODES} (the OpenSSH server is baked offline)")
    port = resolved["ssh.port_ref"]
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise SpecError(_full("ssh.port_ref"), "must resolve to a TCP port")
    settings = resolved["ssh.settings_ref"]
    if not isinstance(settings, dict):
        raise SpecError(_full("ssh.settings_ref"), "must resolve to a table ([ssh.windows])")
    for k in ("root", "config_relative", "listen_address", "remote_addresses"):
        if k not in settings or settings[k] in ("", None, []):
            raise SpecError(f"{x['ssh']['settings_ref']}.{k}", f"missing (read by {_full('ssh.settings_ref')})")
    keys: list[str] = []
    src = _get(merged, s["authorized_keys_key"])
    if src is _MISSING:
        raise SpecError(_full("ssh.authorized_keys_key"), f"references missing key {s['authorized_keys_key']}")
    lines = [ln.strip() for ln in (src if isinstance(src, list) else str(src or "").splitlines())]
    home = _operator_home()
    for pat in s["authorized_keys_files"]:
        p = str(pat)
        p = os.path.join(home, p[2:]) if p.startswith("~/") else p
        for f in sorted(glob.glob(p)):
            try:
                with open(f, encoding="utf-8") as fh:
                    lines += [ln.strip() for ln in fh.read(65536).splitlines()]
            except (OSError, UnicodeDecodeError):
                continue
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        if "PRIVATE KEY" in ln or not SSH_KEY_RE.match(ln):
            raise SpecError(_full("ssh.authorized_keys_files"), "an operator key source holds a line that is not an SSH public key")
        if ln not in keys:
            keys.append(ln)
    if not keys:
        raise SpecError(_full("ssh.authorized_keys_key"),
                        f"no operator SSH public key ({s['authorized_keys_key']} is empty and "
                        f"{_full('ssh.authorized_keys_files')} matches nothing): sshd would accept nobody")
    return {"port": port, "windows": {k: v for k, v in settings.items()}, "authorized_keys": keys,
            "settings_path": s["settings_path"], "authorized_keys_path": s["authorized_keys_path"]}


APP_PFN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*_[a-z0-9]{13}")
# The Hydrate stage installs Store-only packages through winget's msstore source: both must be in the image.
HYDRATE_REQUIRES = ("Microsoft.DesktopAppInstaller_8wekyb3d8bbwe", "Microsoft.WindowsStore_8wekyb3d8bbwe")
CONVERTER_DERIVED = ("skipapps", "customlist")   # [Store_Apps] keys fetch.py derives from source.apps


def _check_apps(x: dict) -> None:
    """source.apps: the inbox apps of the update integrated offline (round-1 boot of build-20261023T224037Z: none were,
    so the image had no Store, no winget and no Xbox app). Family-name syntax, a usable framework pattern, the
    converter keys derived from it left alone, and every app another SSOT value relies on present."""
    apps = x["source"]["apps"]
    for i, a in enumerate(apps):
        if not isinstance(a, str) or not APP_PFN_RE.fullmatch(a):
            raise SpecError(_full(f"source.apps[{i}]"), "must be a package family name (Name_publisherid)")
    low = [a.lower() for a in apps]
    if len(set(low)) != len(low):
        raise SpecError(_full("source.apps"), "lists a package family twice")
    if apps:
        try:
            re.compile(x["source"]["app_frameworks"])
        except re.error as e:
            raise SpecError(_full("source.app_frameworks"), f"is not a regular expression ({e})") from None
        if not x["source"]["app_frameworks"]:
            raise SpecError(_full("source.app_frameworks"), "is empty (the apps' framework packages would be missing)")
    for k, v in x["build"]["converter"]["store_apps"].items():
        if k.lower() in CONVERTER_DERIVED:
            raise SpecError(_full(f"build.converter.store_apps.{k}"), "is derived from source.apps (CustomAppsList.txt), never set")
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            raise SpecError(_full(f"build.converter.store_apps.{k}"), "must be an integer or string")
    home = str(x["xbox"]["home_app"] or "")
    if x["xbox"]["enable"] and home and home.split("!", 1)[0].lower() not in low:
        raise SpecError(_full("xbox.home_app"), f"names {home.split('!', 1)[0]}, which source.apps does not integrate "
                        "(the gaming home would point at an absent app)")
    names = {a.rsplit("_", 1)[0].lower() for a in apps}
    hydrated = [p["name"] for p in x["store_hydrate"]["packages"] if str(p["name"]).lower() not in names]
    if hydrated and not all(r.lower() in low for r in HYDRATE_REQUIRES):
        raise SpecError(_full("store_hydrate.packages"), f"{', '.join(hydrated)} hydrate through winget/the Store: source.apps "
                        f"must integrate {' and '.join(HYDRATE_REQUIRES)}")


def _check_provision(x: dict, resolved: dict) -> None:
    """Who runs what (fix-a M1): nothing privileged as the standard desktop user; services/tasks name a
    shipped component and a supported host/run-as/trigger; the FOD map covers every added capability."""
    p = x["provision"]
    if p["first_logon_commands"]:
        raise SpecError(_full("provision.first_logon_commands"),
                        "must be false: FirstLogonCommands run as the (standard) desktop user and need administrator consent under UAC")
    if p["network_category"] not in NETWORK_CATEGORIES:
        raise SpecError(_full("provision.network_category"), f"must be one of {NETWORK_CATEGORIES}")
    if not re.fullmatch(r"COM[1-9]", p["serial_port"]):
        raise SpecError(_full("provision.serial_port"), "must name a guest serial port COM1..COM9")
    comps = {c["name"]: c for c in x["components"]["ship"]}
    for i, c in enumerate(x["components"]["ship"]):
        if "invocation" in c and c["invocation"] not in COMPONENT_INVOCATIONS:
            raise SpecError(_full(f"components.ship[{i}].invocation"), f"must be one of {COMPONENT_INVOCATIONS}")
    for kind in ("services", "tasks"):
        for name, ent in x[kind].items():
            comp = comps.get(ent.get("component")) if isinstance(ent, dict) else None
            if comp and comp.get("invocation") == "per-request":
                raise SpecError(_full(f"{kind}.{name}.component"),
                                f"{comp['name']} is a per-request CLI (components.ship invocation): it has no daemon mode to run as a service or task")
    for name, sv in x["services"].items():
        key = f"services.{name}"
        if not isinstance(sv, dict) or sv.get("component") not in comps:
            raise SpecError(_full(f"{key}.component"), "must name a components.ship entry")
        if sv.get("host") not in SERVICE_HOSTS:
            raise SpecError(_full(f"{key}.host"), f"must be one of {SERVICE_HOSTS}")
        if sv["host"] == "MiosServiceTool" and (not sv.get("host_name") or "MiosServiceTool" not in comps):
            raise SpecError(_full(f"{key}.host_name"), "a MiosServiceTool-hosted service needs host_name and the shipped MiosServiceTool")
        if sv.get("args_from") and comps.get(sv["args_from"], {}).get("kind") != "config":
            raise SpecError(_full(f"{key}.args_from"), "must name a components.ship config")
    for name, tk in x["tasks"].items():
        key = f"tasks.{name}"
        if not isinstance(tk, dict) or tk.get("component") not in comps:
            raise SpecError(_full(f"{key}.component"), "must name a components.ship entry")
        if tk.get("run_as") not in TASK_RUN_AS:
            raise SpecError(_full(f"{key}.run_as"), f"must be one of {TASK_RUN_AS}")
        if tk.get("trigger") not in TASK_TRIGGERS:
            raise SpecError(_full(f"{key}.trigger"), f"must be one of {TASK_TRIGGERS}")
    if x["wallpaper"]["setup_component"] not in comps:
        raise SpecError(_full("wallpaper.setup_component"), "must name a components.ship entry")
    fod = x["features"]["capability_fod"]
    for cap in x["features"]["capabilities_add"]:
        ent = fod.get(cap)
        if not isinstance(ent, dict) or not ent.get("package") or not ent.get("proof"):
            raise SpecError(_full(f"features.capability_fod.{cap}"),
                            "every added capability is baked offline from its FOD package (package + proof file)")


_CREDENTIAL_FIELDS = ("password",)


def guest_projection(spec: dict) -> dict:
    """The spec the image carries (C:\\ProgramData\\MiOS\\xbox-spec.json, read by the GPU step and the
    in-guest provisioning): the rendered spec minus every credential. Account names stay."""
    out = copy.deepcopy(spec)
    for acct in (out.get("users") or {}).values():
        if isinstance(acct, dict):
            for f in _CREDENTIAL_FIELDS:
                acct.pop(f, None)
    out["projection"] = "guest"
    return out


def _layers_with_fragment(fragment: str) -> list[str]:
    """Default layer order with `fragment` as the last vendor-tier layer (staged SSOT preview)."""
    vendor, vendor_d, host, host_d, user, user_d = mios_toml._tier_dirs()
    vend = [vendor] + mios_toml._frags(vendor_d)
    return vend + [fragment] + [host] + mios_toml._frags(host_d) + [user] + mios_toml._frags(user_d)


def render_layers(layers: list[str] | None) -> dict:
    merged = mios_toml.load_merged(layers) if layers is not None else mios_toml.load_merged()
    used = layers if layers is not None else mios_toml.layer_paths()
    return render(merged, [p for p in used if os.path.isfile(p)])


def _write_atomic(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".xbox-spec.", dir=d)
    try:
        os.fchmod(fd, 0o600)   # holds resolved credentials
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
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


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Render [windows.xbox] from the layered SSOT into xbox-spec.json.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--layers", nargs="+", metavar="TOML", help="explicit layer files, lowest precedence first")
    src.add_argument("--with-fragment", metavar="TOML", help="default layers plus this staged fragment as the top vendor layer")
    out = ap.add_mutually_exclusive_group(required=True)
    out.add_argument("--out", metavar="JSON", help="write xbox-spec.json here (mode 0600)")
    out.add_argument("--check", action="store_true", help="validate only; write nothing")
    out.add_argument("--write-oem-logo", action="store_true", help="regenerate the tracked OEM logo projection")
    out.add_argument("--check-fixture", metavar="ROOT", help="verify ROOT's tracked OEM logo projection against its render")
    return ap.parse_args(argv)


def _oem_logo_projection_cli(args) -> int:
    try:
        if args.write_oem_logo:
            for rel in write_oem_logo_projection(_TREE):
                print(f"render_spec: wrote {rel}", file=sys.stderr)
            return 0
        bad = check_oem_logo_projection(os.path.abspath(args.check_fixture))
    except SpecError as exc:
        print(f"render_spec: {exc}", file=sys.stderr)
        return 2
    for problem in bad:
        print(f"render_spec: {problem}", file=sys.stderr)
    if not bad:
        print("render_spec: the OEM logo projection matches its render", file=sys.stderr)
    return 1 if bad else 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.write_oem_logo or args.check_fixture:
        return _oem_logo_projection_cli(args)
    layers = args.layers
    if args.with_fragment:
        if not os.path.isfile(args.with_fragment):
            print(f"render_spec: no such fragment: {args.with_fragment}", file=sys.stderr)
            return 1
        layers = _layers_with_fragment(args.with_fragment)
    try:
        spec = render_layers(layers)
    except SpecError as exc:
        print(f"render_spec: {exc}", file=sys.stderr)
        return 2
    if args.out:
        _write_atomic(args.out, json.dumps(spec, indent=2, sort_keys=True) + "\n")
        print(f"render_spec: wrote {args.out} ({len(spec['registry'])} registry items, "
              f"{len(spec['feature_overrides'])} feature toggles)", file=sys.stderr)
    else:
        print(f"render_spec: ok ({len(spec['registry'])} registry items)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
