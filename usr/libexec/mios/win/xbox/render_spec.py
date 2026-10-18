#!/usr/bin/env python3
# AI-hint: MiOS-Xbox spec renderer: layered SSOT [windows.xbox] (vendor -> host -> user via mios_toml.load_merged) -> resolved xbox-spec.json, the single input of every Windows build step; a missing key or empty credential is a hard failure naming the key.
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
import hmac
import json
import os
import re
import sys
import tempfile

_TREE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", ".."))
_LIB = os.path.join(_TREE, "usr", "lib", "mios")
if os.path.isfile(os.path.join(_LIB, "mios_toml.py")) and _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import mios_toml  # noqa: E402
import mios_credentials  # noqa: E402  -- the ONE layered-SSOT credential resolver (same tree)

ROOT = "windows.xbox"
SCHEMA = 1

# The contract every Windows step relies on: key path (under windows.xbox) -> expected type.
# Values live only in the SSOT; this lists names and shapes, never defaults.
REQUIRED = {
    "enable": bool,
    "spec_schema": int,
    "source.provider": str, "source.channel": str, "source.build": str, "source.arch": str,
    "source.edition": str, "source.language_ref": str,
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
    "settings.rdp.enable": int, "settings.rdp.allow_cam": int,
    "settings.rdp.allow_audio": int, "settings.rdp.allow_drives": int,
    "settings.rdp.allow_com_ports": int, "settings.rdp.allow_clipboard": int,
    "settings.rdp.allow_location": int,
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
    "build.converter.entry": str, "build.converter.config": dict,
    "registry_map": dict,
}
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

    def add(owner, hive, key, name, typ, data, kv_field=None, delete=False):
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
        add(path, loc["hive"], loc["key"], loc["name"], loc["type"], _reg_data(path, val), loc.get("kv_field"))

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

    out = {
        "schema": SCHEMA,
        "layers": list(layers or []),
        "spec": x,
        "resolved": resolved,
        "users": users,
        "region": {"timezone": win_tz, "ui_language": ui_lang, "input_locale": input_locale},
        "source": source,
        "feature_overrides": _feature_ids(x),
        "registry": build_registry(x),
        "debloat_services": debloat_services,
        "winrm": {"enable": x["winrm"]["enable"], "remote_address": x["winrm"]["remote_address"], "port": port},
    }
    # credential refs were resolved into users; keep `resolved` free of secrets
    for p in CREDENTIAL_REFS:
        out["resolved"][p] = "<credential>"
    return out


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
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
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
