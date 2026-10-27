#!/usr/bin/env python3
# AI-hint: The ONE MiOS-Xbox spec item model shared by the offline conformance gate (conform.py) and the in-guest WinRM probe (probe.py): loads xbox-spec.json (or the [windows.xbox] SSOT tree), turns it into a flat list of typed check items with evidence locators, canonicalises registry paths/values, holds Microsoft's TerminalServer.admx policy table (TS_POLICIES: RDP redirection value names) and the legacy-literal deny-list (incl. GPU display driver installs by script).
# AI-related: usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/xbox/mios-xbox-probe.ps1, tests/test-mios-xbox-conform.py, tests/test-mios-xbox-probe.py
# AI-functions: load_spec, items_from_spec, items_from_rendered, spec_policy, canon_reg_path, canon_value, value_equal, deny_hits, spec_deny_findings, sha256_text, Finding
"""MiOS-Xbox spec items (schema ``mios-xbox-spec/1``).

Input is the resolved ``xbox-spec.json`` rendered from the layered SSOT (xb-02,
render_spec.py).  Two shapes are accepted:

1. render_spec.py output (``schema`` 1: ``spec``, ``registry``, ``users``, ...).  The
   registry locators come from ``[windows.xbox.registry_map]`` via render_spec's
   ``registry`` list -- this module owns NO spec-key -> registry mapping.
2. ``{"schema": "mios-xbox-spec/1", "items": [...]}`` -- the canonical item list.

Item kinds (every item has ``id`` and ``kind``):

=============  ===============================================================
reg            scope (user|software|system), path, name, type, value
feature        name, state (enabled|disabled|conditional)
driver         inf (base name, e.g. ``viostor``) -- must be in the driver set
guest_tool     name, installer (payload path) and/or service, file
component      name, path (image-relative), sha256?, task?, service?
capability     name (Windows capability name prefix, e.g. OpenSSH.Server), proof? (image-relative file of the offline-baked FOD)
file_hash      path (image-relative), sha256
build_family   family (list of BuildBranch names with Start assets), families{}
user           name ("<RID-500>" = built-in Administrator), autologon, password_sha256
rid500         state (disabled)
computer_name  value
appx           name (package family prefix), present (bool), pfn + inbox (integrated offline: image gate + probe) -- else probe only
gpu_step       record (guest path of the post-setup JSON record)
=============  ===============================================================

Credentials only ever appear here as sha256 hex digests; nothing in this module
prints a credential."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = "mios-xbox-spec/1"
KINDS = ("reg", "feature", "capability", "driver", "guest_tool", "component", "file_hash",
         "build_family", "user", "rid500", "computer_name", "appx", "gpu_step")
USER_SCOPES = ("user",)
SCOPES = ("user", "software", "system")


class SpecError(Exception):
    """The spec is unusable (missing key, wrong type, empty credential)."""


@dataclasses.dataclass
class Finding:
    """One named non-conformance.  ``id`` is stable (tests and reports key on it)."""
    id: str
    detail: str
    source: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {"id": self.id, "detail": self.detail, "source": self.source}

    def __str__(self) -> str:  # never carries a credential (callers build detail without one)
        return f"{self.id}: {self.detail}" + (f" [{self.source}]" if self.source else "")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# registry canonicalisation
# ---------------------------------------------------------------------------

_HIVE_ALIASES = {
    "hkcu": "hkcu", "hkey_current_user": "hkcu",
    "hklm": "hklm", "hkey_local_machine": "hklm",
    "hku": "hku", "hkey_users": "hku",
    "hkcr": "hkcr", "hkey_classes_root": "hkcr",
}
_SOFTWARE_TOP = {"microsoft", "policies", "classes", "wow6432node", "clients", "registeredapplications"}
_SYSTEM_TOP_RE = re.compile(r"^(controlset\d{3}|currentcontrolset|select|setup|mounteddevices|driverdatabase|hardwareconfig|wpa|resourcemanager|software|state|keyboard layout|rng)$")


def _norm_ctlset(rest: List[str]) -> List[str]:
    if rest and re.match(r"^controlset\d{3}$", rest[0], re.I):
        return ["CurrentControlSet"] + rest[1:]
    return rest


def canon_reg_path(path: str) -> Optional[Tuple[str, str]]:
    """Map any observed registry path to (scope, canonical lower-case path below the hive).

    Handles HKCU, HKU\\<anything> (default-profile mounts, SIDs), HKLM\\SOFTWARE,
    HKLM\\SYSTEM (ControlSet00N -> CurrentControlSet), and offline mounts such as
    HKLM\\MIOS_SYS / HKLM\\MIOS_SW / HKLM\\DefUser.  PowerShell drive syntax
    (``HKCU:\\``, ``Registry::HKEY_...``) is accepted.  Returns None for an
    unrecognised path."""
    p = path.strip().strip('"').strip("'")
    p = re.sub(r"(?i)^(?:microsoft\.powershell\.core\\)?registry::", "", p)   # Registry:: is case-insensitive
    p = p.replace("/", "\\")
    p = re.sub(r"^([A-Za-z_]+):\\?", r"\1\\", p)  # HKCU:\X -> HKCU\X
    parts = [x for x in p.split("\\") if x != ""]
    if not parts:
        return None
    hive = _HIVE_ALIASES.get(parts[0].lower())
    rest = parts[1:]
    if hive is None:
        return None
    if hive == "hkcu":
        return "user", "\\".join(rest).lower()
    if hive == "hkcr":
        return "software", "\\".join(["Classes"] + rest).lower()
    if hive == "hku":
        # HKU\<mount or SID or .DEFAULT>\Software\...  -> per-user
        if len(rest) < 1:
            return None
        return "user", "\\".join(rest[1:]).lower()
    # HKLM
    if not rest:
        return None
    top = rest[0].lower()
    if top == "software":
        return "software", "\\".join(rest[1:]).lower()
    if top == "system":
        return "system", "\\".join(_norm_ctlset(rest[1:])).lower()
    # offline mount: HKLM\<MOUNT>\...
    mount, below = top, rest[1:]
    nxt = below[0].lower() if below else ""
    if re.match(r"^(controlset\d{3}|currentcontrolset|select|mounteddevices)$", nxt) or re.search(r"sys", mount) and not re.search(r"(soft|sw)", mount):
        return "system", "\\".join(_norm_ctlset(below)).lower()
    if re.search(r"(def|user|ntuser|hkcu|profile)", mount):
        return "user", "\\".join(below).lower()
    if re.search(r"(soft|sw)", mount) or nxt in _SOFTWARE_TOP:
        return "software", "\\".join(below).lower()
    if nxt in ("software", "control panel", "system", "environment", "appevents", "console", "keyboard layout"):
        return "user", "\\".join(below).lower()
    return None


def canon_item_path(scope: str, path: str) -> str:
    """Canonical lower-case path for a spec item (path is below the hive)."""
    parts = [x for x in path.replace("/", "\\").split("\\") if x]
    if scope == "system":
        parts = _norm_ctlset(parts)
    return "\\".join(parts).lower()


_TYPE_ALIASES = {
    "reg_dword": "dword", "dword": "dword", "reg_qword": "qword", "qword": "qword",
    "reg_sz": "sz", "sz": "sz", "string": "sz", "reg_expand_sz": "expand_sz", "expand_sz": "expand_sz",
    "expandstring": "expand_sz", "reg_binary": "binary", "binary": "binary",
    "reg_multi_sz": "multi_sz", "multi_sz": "multi_sz", "multistring": "multi_sz",
}


def canon_type(t: str) -> str:
    return _TYPE_ALIASES.get(str(t).strip().lower(), str(t).strip().lower())


def canon_value(vtype: str, value: Any) -> Any:
    """Normalise a registry value for comparison: dword/qword -> int, binary -> lower hex
    without separators, sz -> str, multi_sz -> list[str]."""
    t = canon_type(vtype)
    if t in ("dword", "qword"):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        s = str(value).strip()
        try:
            return int(s, 16) if s.lower().startswith("0x") else int(s)
        except ValueError:
            return s
    if t == "binary":
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).hex()
        return re.sub(r"[^0-9a-f]", "", str(value).lower())
    if t == "multi_sz":
        if isinstance(value, (list, tuple)):
            return [str(x) for x in value]
        return [x for x in str(value).replace("\\0", "\0").split("\0") if x]
    return "" if value is None else str(value)


def kv_field_value(text: Any, field: str) -> Optional[str]:
    """The ``field`` of a 'k=v;k2=v2;' string (DirectXUserGlobalSettings style)."""
    for part in str(text).split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().lower() == field.lower():
                return v.strip()
    return None


def value_equal(item: Dict[str, Any], vtype: str, value: Any) -> bool:
    if item.get("kv_field"):
        got = kv_field_value(value, item["kv_field"])
        return got is not None and got == str(canon_value("sz", item["value"]))
    want_t = canon_type(item["type"])
    got = canon_value(want_t, value)
    want = canon_value(want_t, item["value"])
    if want_t in ("sz", "expand_sz") and isinstance(got, str):
        return got.rstrip("\0") == want
    return got == want


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_spec(path: str) -> Dict[str, Any]:
    """Load xbox-spec.json (or a TOML file holding [windows.xbox])."""
    if path.endswith(".toml"):
        try:
            import tomllib
        except ImportError:  # pragma: no cover
            raise SpecError("tomllib unavailable")
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def items_from_spec(spec: Dict[str, Any], root: Optional[str] = None) -> List[Dict[str, Any]]:
    """The validated item list: canonical ``items[]`` or render_spec.py output."""
    if isinstance(spec.get("items"), list):
        items = [dict(i) for i in spec["items"]]
    elif "registry" in spec and "spec" in spec:
        items = items_from_rendered(spec, root)
    else:
        raise SpecError("not an xbox-spec.json (need items[] or render_spec.py output); render the SSOT first")
    validate_items(items)
    return items


def validate_items(items: List[Dict[str, Any]]) -> None:
    seen = set()
    for it in items:
        iid, kind = it.get("id"), it.get("kind")
        if not iid or kind not in KINDS:
            raise SpecError(f"bad item (id={iid!r}, kind={kind!r})")
        if iid in seen:
            raise SpecError(f"duplicate item id {iid}")
        seen.add(iid)
        need = {"reg": ("scope", "path", "name", "type", "value"), "feature": ("name", "state"),
                "driver": ("inf",), "guest_tool": ("name",), "component": ("name", "path"),
                "file_hash": ("path", "sha256"), "build_family": ("family",), "capability": ("name",), "computer_name": ("value",),
                "user": ("name", "password_sha256"), "rid500": ("state",), "appx": ("name",),
                "gpu_step": ("record",)}[kind]
        for k in need:
            if it.get(k) in (None, "") and not (k == "value" and it.get(k) == ""):
                raise SpecError(f"{iid}: missing {k}")
        if kind == "reg" and it["scope"] not in SCOPES:
            raise SpecError(f"{iid}: scope must be one of {SCOPES}")
        if kind == "user":
            h = str(it["password_sha256"]).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", h) or h == sha256_text(""):
                raise SpecError(f"{iid}: empty or malformed credential hash")
        if kind == "guest_tool" and not (it.get("installer") or it.get("service") or it.get("file")):
            raise SpecError(f"{iid}: guest_tool needs installer, service or file")


# ---------------------------------------------------------------------------
# items from render_spec.py output (xbox-spec.json, schema 1)
# ---------------------------------------------------------------------------

HIVE_SCOPE = {"DEFAULT_USER": "user", "SOFTWARE": "software", "SYSTEM": "system"}
# Windows-defined location of the Default profile's Start layout blob (xb-11 asset target).
START2_TARGET = "Users/Default/AppData/Local/Packages/Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy/LocalState/start2.bin"


def _win_rel(p: str) -> str:
    """'C:\\Program Files\\MiOS\\x' -> 'Program Files/MiOS/x' (image-relative)."""
    return re.sub(r"^[A-Za-z]:", "", str(p)).replace("\\", "/").lstrip("/")


def _file_sha(root: Optional[str], rel: str) -> Optional[str]:
    if not root or not rel:
        return None
    p = os.path.join(root, rel)
    if not os.path.isfile(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def items_from_rendered(doc: Dict[str, Any], root: Optional[str] = None) -> List[Dict[str, Any]]:
    """Items from render_spec.py's xbox-spec.json.  ``root`` is the MiOS tree holding the
    component sources and Start assets (their sha256 is the expected image hash).
    Plain credentials in ``doc['users']`` are hashed here and never kept."""
    x = doc.get("spec")
    if not isinstance(x, dict) or not isinstance(doc.get("registry"), list) or not isinstance(doc.get("users"), dict):
        raise SpecError("xbox-spec.json lacks spec/registry/users (render it with render_spec.py)")
    items: List[Dict[str, Any]] = []
    owners: Dict[str, int] = {}
    for r in doc["registry"]:
        owners[r["owner"]] = owners.get(r["owner"], 0) + 1
    for r in doc["registry"]:
        scope = HIVE_SCOPE.get(r.get("hive", ""))
        if scope is None:
            raise SpecError(f"registry item {r.get('owner')}: unknown hive {r.get('hive')!r}")
        iid = "reg." + r["owner"] + (f":{r['name']}" if owners[r["owner"]] > 1 else "")
        it = {"id": iid, "kind": "reg", "scope": scope, "path": r["key"], "name": r["name"], "owner": r["owner"]}
        if r.get("delete"):
            it.update(type="sz", value="", absent=True)
        else:
            it.update(type=canon_type(r["type"]), value=r["data"])
        if r.get("kv_field"):
            it["kv_field"] = r["kv_field"]
        if r.get("policy"):   # TerminalServer.admx policy name (the image gate checks it against the image's ADMX)
            it["policy"] = r["policy"]
        items.append(it)
    for svc in doc.get("debloat_services") or []:
        items.append({"id": f"debloat.services.{svc['name']}", "kind": "reg", "scope": "system",
                      "path": f"CurrentControlSet\\Services\\{svc['name']}", "name": "Start", "type": "dword",
                      "value": int(svc["start"])})
    feats = x.get("features") or {}
    for key, state in (("enable", "enabled"), ("disable", "disabled"), ("enable_if_nested_virt", "conditional")):
        for f in feats.get(key) or []:
            items.append({"id": f"features.{f}", "kind": "feature", "name": f, "state": state})
    fod = feats.get("capability_fod") or {}
    for cap in feats.get("capabilities_add") or []:
        it = {"id": f"capabilities.{cap}", "kind": "capability", "name": cap}
        proof = (fod.get(cap) or {}).get("proof") if isinstance(fod.get(cap), dict) else None
        if proof:   # baked offline (F4): the image gate requires this file in install.wim
            it["proof"] = _win_rel(str(proof))
        items.append(it)
    for dname, d in (x.get("drivers") or {}).items():
        if isinstance(d, dict):
            for inf in d.get("inf") or []:
                base = re.sub(r"\.inf$", "", str(inf), flags=re.I)
                items.append({"id": f"drivers.{dname}.{base}", "kind": "driver", "inf": base})
    for gname, g in (x.get("guest_tools") or {}).items():
        if not isinstance(g, dict):
            continue
        it = {"id": f"guest_tools.{gname}", "kind": "guest_tool", "name": gname}
        inst = g.get("file") or g.get("package") or g.get("asset")
        if inst:
            it["installer"] = inst
        if g.get("service"):
            it["service"] = g["service"]
        if inst or g.get("service"):
            items.append(it)
    comps = x.get("components") or {}
    dest = _win_rel(comps.get("dest_dir", ""))
    for c in comps.get("ship") or []:
        src = str(c.get("source", ""))
        fname = f"{c['name']}.exe" if c.get("kind") == "native" else os.path.basename(src)
        it = {"id": f"components.{c['name']}", "kind": "component", "name": c["name"],
              "path": f"{dest}/{fname}" if dest else fname}
        sha = None if c.get("kind") == "native" else _file_sha(root, src)
        if sha:
            it["sha256"] = sha
        items.append(it)
    gpu = x.get("gpu") or {}
    if gpu.get("script_path"):
        drv = x.get("drivers") or {}
        it = {"id": "gpu.script", "kind": "component", "name": "mios-gpu-driver", "path": _win_rel(gpu["script_path"])}
        sha = _file_sha(root, drv.get("gpu_step", ""))
        if sha:
            it["sha256"] = sha
        if gpu.get("task_name"):
            it["task"] = gpu["task_name"]
        items.append(it)
    if gpu.get("record_path"):
        it = {"id": "gpu.post_setup", "kind": "gpu_step", "record": gpu["record_path"],
              "skip_vendor_ids": list(gpu.get("skip_vendor_ids") or []),
              "vendor_ids": sorted({str(v.get("vendor_id", "")).upper() for v in (gpu.get("vendors") or {}).values()
                                    if isinstance(v, dict) and v.get("vendor_id")})}
        # offline gates: the tree's GPU step (drivers.gpu_step, byte-identical to the shipped copy per gpu.script) runs
        # Read-MiOSGpuSpec on the guest spec the image SHIPS (gpu.spec_path) and must get this mode/task/record/vendors
        if gpu.get("spec_path") and (x.get("drivers") or {}).get("gpu_step"):
            it.update(spec_path=_win_rel(gpu["spec_path"]), script=str(x["drivers"]["gpu_step"]),
                      mode=str((x.get("drivers") or {}).get("gpu", "")), task=str(gpu.get("task_name", "")))
        items.append(it)
    start = x.get("start") or {}
    fams = {}
    for fam, a in (start.get("layout_assets") or {}).items():
        if isinstance(a, dict):
            fams[fam] = {"start2": a.get("start2", ""), "start2_sha256": _file_sha(root, a.get("start2", "")),
                         "visible_places": a.get("visible_places", "")}
    if fams:
        items.append({"id": "start.layout_family", "kind": "build_family", "family": sorted(fams),
                      "families": fams, "family_source": start.get("layout_family_source", "BuildBranch"),
                      "start2_target": START2_TARGET})
    for pkg in (x.get("store_hydrate") or {}).get("packages") or []:
        items.append({"id": f"store_hydrate.{pkg['name']}", "kind": "appx", "name": pkg["name"], "present": True})
    # inbox apps integrated offline (source.apps): the image gate finds them under Program Files\WindowsApps, the
    # probe as installed packages (round-1: no Store, winget or Xbox app in the image)
    for pfn in (x.get("source") or {}).get("apps") or []:
        items.append({"id": f"source.apps.{pfn}", "kind": "appx", "name": pfn.rsplit("_", 1)[0], "pfn": pfn,
                      "present": True, "inbox": True})
    users = doc["users"]
    d = users.get("desktop") or {}
    pw = d.get("password")
    if not isinstance(pw, str) or not pw.strip():
        raise SpecError("users.desktop.password: empty credential")
    it = {"id": "users.desktop", "kind": "user", "name": d.get("name", ""), "role": "desktop",
          "autologon": bool(d.get("autologon")), "persistent": bool(d.get("autologon_persistent")),
          "groups": list(d.get("groups") or []), "password_sha256": sha256_text(pw)}
    svc = (users.get("service_account") or {}).get("name")
    if svc:
        it["service_account"] = svc   # never the AutoLogon account (operator decision 2026-10-16)
    items.append(it)
    if "admin" in users:
        adm = users["admin"]
        apw = adm.get("password")
        if not isinstance(apw, str) or not apw.strip():
            raise SpecError("users.admin.password: empty credential")
        items.append({"id": "users.admin", "kind": "user", "name": adm.get("name", ""), "role": "admin",
                      "autologon": False, "groups": list(adm.get("groups") or []),
                      "password_sha256": sha256_text(apw)})
    b = users.get("builtin_administrator") or {}
    bpw = b.get("password")
    if not isinstance(bpw, str) or not bpw.strip():
        raise SpecError("users.builtin_administrator.password: empty credential")
    items.append({"id": "users.builtin_administrator", "kind": "user", "name": "<RID-500>", "role": "builtin",
                  "autologon": False, "password_sha256": sha256_text(bpw)})
    if b.get("disable_at"):
        items.append({"id": "users.rid500", "kind": "rid500", "state": "disabled", "at": b["disable_at"]})
    if users.get("computer_name"):
        items.append({"id": "users.computer_name", "kind": "computer_name", "value": users["computer_name"]})
    return items


def spec_policy(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Gate policy carried by the spec (falls back to the gate's own deny-list)."""
    drv = ((doc.get("spec") or {}).get("drivers") or {}) if isinstance(doc, dict) else {}
    prov = drv.get("deny_display_providers")
    return {"gpu_provider_re": re.compile("|".join(re.escape(p) for p in prov), re.I) if prov else GPU_PROVIDER_RE}


# ---------------------------------------------------------------------------
# Remote Desktop Services policies (Microsoft TerminalServer.admx)
# ---------------------------------------------------------------------------

# The registry key every TerminalServer.admx machine policy below writes (SOFTWARE hive).
TS_POLICY_KEY = "Policies\\Microsoft\\Windows NT\\Terminal Services"
# ADMX policy name -> (registry value name, enabledValue, disabledValue). Source: Microsoft Learn "Policy CSP -
# ADMX_TerminalServer" / "Policy CSP - RemoteDesktopServices" (ADMX mapping tables), cross-checked against the
# image's own Windows\PolicyDefinitions\TerminalServer.admx (build 29683; TS_LOCATION_REDIRECTION is only in the
# ADMX). This is Microsoft's schema, not MiOS data: the SSOT registry_map names the policy, and a value name that
# is not that policy's valueName (fDisableLocation, or fDisableCam used for the camera) fails the render and the gate.
TS_POLICIES: Dict[str, Tuple[str, int, int]] = {
    "TS_CLIENT_AUDIO": ("fDisableCam", 0, 1),                      # Allow audio and video playback redirection
    "TS_CLIENT_AUDIO_CAPTURE": ("fDisableAudioCapture", 0, 1),     # Allow audio recording redirection
    "TS_CAMERA_REDIRECTION": ("fDisableCameraRedir", 1, 0),        # Do not allow video capture redirection
    "TS_CLIENT_DRIVE_M": ("fDisableCdm", 1, 0),                    # Do not allow drive redirection
    "TS_CLIENT_COM": ("fDisableCcm", 1, 0),                        # Do not allow COM port redirection
    "TS_CLIENT_CLIPBOARD": ("fDisableClip", 1, 0),                 # Do not allow Clipboard redirection
    "TS_CLIENT_LPT": ("fDisableLPT", 1, 0),                        # Do not allow LPT port redirection
    "TS_CLIENT_PNP": ("fDisablePNPRedir", 1, 0),                   # Do not allow supported Plug and Play device redirection
    "TS_CLIENT_PRINTER": ("fDisableCpm", 1, 0),                    # Do not allow client printer redirection
    "TS_LOCATION_REDIRECTION": ("fDisableLocationRedir", 1, 0),    # Do not allow location redirection
    "TS_SMART_CARD": ("fEnableSmartCard", 0, 1),                   # Do not allow smart card device redirection
    "TS_TIME_ZONE": ("fEnableTimeZoneRedirection", 1, 0),          # Allow time zone redirection
    "TS_UIA": ("EnableUiaRedirection", 1, 0),                      # Allow UI Automation redirection
    "TS_WEBAUTHN": ("fDisableWebAuthn", 1, 0),                     # Do not allow WebAuthn redirection
    "TS_DISABLE_REMOTE_DESKTOP_WALLPAPER": ("fNoRemoteDesktopWallpaper", 1, 0),   # Remove remote desktop wallpaper
}


def is_ts_policy_key(scope_or_hive: str, path: str) -> bool:
    """True for HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\Terminal Services (machine RDS policies)."""
    return str(scope_or_hive).lower() == "software" and canon_item_path("software", path) == TS_POLICY_KEY.lower()


def ts_policy_problem(policy: Optional[str], name: str, value: Any) -> Optional[str]:
    """Why a Terminal Services policy write does not match Microsoft's policy table (None = it matches)."""
    if not policy:
        return f"{name} is written under {TS_POLICY_KEY} without naming its TerminalServer.admx policy"
    want = TS_POLICIES.get(str(policy))
    if want is None:
        return f"{policy} is not a TerminalServer.admx policy MiOS knows (Microsoft's ADMX_TerminalServer table)"
    if want[0].lower() != str(name).lower():
        return f"{policy} writes {want[0]}, not {name}"
    v = canon_value("dword", value)
    if v not in (want[1], want[2]):
        return f"{policy} {want[0]}={v!r}: the policy's values are {want[1]} (Enabled) and {want[2]} (Disabled)"
    return None


# ---------------------------------------------------------------------------
# legacy-literal deny-list (gate policy, independent of the spec)
# ---------------------------------------------------------------------------

# (finding id, scope|None, value name (lower), predicate on the canonical value)
DENY_REG: List[Tuple[str, Optional[str], str, Any]] = [
    ("deny.TaskbarAl=0", "user", "taskbaral", lambda v: v == 0),
    ("deny.SearchboxTaskbarMode=1", "user", "searchboxtaskbarmode", lambda v: v == 1),
    ("deny.Start_ShowDownloads=1", "user", "start_showdownloads", lambda v: v == 1),
    ("deny.Start_ShowDocuments=1", "user", "start_showdocuments", lambda v: v == 1),
    ("deny.LockedStartLayout", None, "lockedstartlayout", lambda v: True),
    ("deny.LockedStartLayout", None, "startlayoutfile", lambda v: True),
]
# text literals in any pipeline input (scripts, answer file, payload text)
DENY_TEXT: List[Tuple[str, "re.Pattern[str]"]] = [
    ("deny.LockedStartLayout", re.compile(r"LockedStartLayout|<LayoutModificationTemplate\b|StartLayoutFile|LayoutModification\.xml", re.I)),
    ("deny.stable-virtio", re.compile(r"stable-virtio", re.I)),  # pin-gate:allow (deny-list pattern)
    ("deny.ForceUnsigned", re.compile(r"-ForceUnsigned\b|/ForceUnsigned\b", re.I)),
    ("deny.static-wallpaper", re.compile(r"mios-wallpaper\.jpg", re.I)),
    ("deny.pnputil-nvidia-inf", re.compile(r"\bpnputil(?:\.exe)?\b[^\r\n;|&]*(?:/add-driver|-a\b)[^\r\n;|&]*\bnv[a-z0-9_]*\.inf\b", re.I)),
]
# A GPU vendor display driver installed by a pipeline script (XB-3: only the post-setup detection step installs one,
# from the vendor's own installer). Any driver-install command -- PnPUtil /add-driver|-a|-i, DISM /Add-Driver,
# Add-WindowsDriver, DevCon install|update|dp_add -- that names an NVIDIA/AMD/Intel display INF (the names
# GPU_INF_NAME_RE knows) or a GPU-vendor driver directory is denied. A pnputil + nv*.inf line keeps its own id above.
_DRIVER_INSTALL_CMD = (r"(?:\bpnputil(?:\.exe)?\b[^\r\n;|&]*?(?:/add-driver\b|/install\b|(?<![\w-])-[ai]\b)"
                       r"|\bdism(?:\.exe)?\b[^\r\n;|&]*?/add-driver\b"
                       r"|\bAdd-WindowsDriver\b"
                       r"|\bdevcon(?:\.exe)?\b[^\r\n;|&]*?\b(?:install|update|dp_add)\b)")
_GPU_INF_TOKEN = (r"(?<![\w.-])(?:nv[a-z0-9_]*|iigd[a-z0-9_]*|igdlh[a-z0-9_]*|igdumd[a-z0-9_]*|cui_dch|amdkmdag"
                  r"|atiilhag|atiumd[a-z0-9_]*|u0\d{6}|c0\d{6})\.inf\b")
_GPU_VENDOR_DIR = r"[\\/](?:nvidia|geforce|amd|radeon|ati|intel)(?=[\\/'\"\s_-]|$)"
DENY_TEXT.append(("deny.gpu-driver-script", re.compile(
    _DRIVER_INSTALL_CMD + r"[^\r\n;|&]*?(?:" + _GPU_INF_TOKEN + "|" + _GPU_VENDOR_DIR + ")", re.I)))
# Display drivers of these providers must never be in the image (XB-3: detect + install post-setup).
GPU_PROVIDER_RE = re.compile(r"nvidia|advanced micro devices|\bamd\b|\bati\b|ati technologies|intel", re.I)
GPU_INF_NAME_RE = re.compile(r"^(nv[a-z0-9_]*|iigd[a-z0-9_]*|igdlh[a-z0-9_]*|igdumd[a-z0-9_]*|cui_dch|amdkmdag|atiilhag|atiumd[a-z0-9_]*|u0\d{6}|c0\d{6})$", re.I)
# Credentials that are known legacy literals / factory defaults, stored as sha256 digests so no
# credential literal lives in code (the legacy MiOS defaults and common factory defaults).
# Policy (XB-4): the SSOT is authoritative. A value the resolved layered SSOT SELECTED is never
# a finding, even when it is one of these (that is only a weak-password WARNING); the same value
# HARDCODED in pipeline code/templates, or rendered without being the SSOT value, is a finding.
WEAK_CREDENTIALS = frozenset((
    "3d955d52d88ac9d439602476901fee36190493f1800917a10ce246f20a0b7dd0",
    "04f8996da763b7a969b1028ee3007569eaf3a635486ddab211d512c85b9df8fb",
    "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
    "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918",
    "4194d1706ed1f408d5e02d672777019f4d5385c766a8c6ca8acba3167d36a7b9",
    "057ba03d6c44104863dc7361fe4578965d1887360f90a0895882e58a6248fc86",
    "b03ddf3ca2e714a6548e7495e2a03f5e824eaac9837cd7f159c67b90fb4b7342",
))


def reg_deny_hits(scope: str, name: str, vtype: str, value: Any) -> List[str]:
    out = []
    n = name.lower()
    for fid, dscope, dname, pred in DENY_REG:
        if n == dname and (dscope is None or dscope == scope):
            v = canon_value(vtype, value)
            try:
                if pred(v):
                    out.append(fid)
            except Exception:  # noqa: BLE001
                pass
    return out


def text_deny_hits(text: str) -> List[Tuple[str, int]]:
    hits = []
    for fid, rx in DENY_TEXT:
        for m in rx.finditer(text):
            hits.append((fid, text.count("\n", 0, m.start()) + 1))
    nv_lines = {ln for fid, ln in hits if fid == "deny.pnputil-nvidia-inf"}   # one finding per line for the old pattern
    return [(fid, ln) for fid, ln in hits if not (fid == "deny.gpu-driver-script" and ln in nv_lines)]


def gpu_inf_is_vendor_display(name: str, inf_text: Optional[str], provider_re: "re.Pattern[str]" = GPU_PROVIDER_RE) -> bool:
    """True for an NVIDIA/AMD/Intel display driver .inf (by Class=Display + provider, or by name)."""
    base = re.sub(r"\.inf$", "", os.path.basename(name), flags=re.I)
    base = re.sub(r"\.inf_.*$", "", base, flags=re.I)
    if inf_text is not None:
        version = _inf_section(inf_text, "version")
        strings = _inf_strings(inf_text)
        cls = _resolve(version.get("class", ""), strings)
        prov = _resolve(version.get("provider", ""), strings)
        if cls.lower() == "display" and provider_re.search(prov):
            return True
        if cls.lower() == "display" and GPU_INF_NAME_RE.match(base):
            return True
        if cls and cls.lower() != "display":
            return False
    return bool(GPU_INF_NAME_RE.match(base))


def _inf_section(text: str, sect: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    cur = None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = m.group(1).strip().lower()
            continue
        if cur == sect and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().lower()] = v.strip().strip('"')
    return out


def _inf_strings(text: str) -> Dict[str, str]:
    return _inf_section(text, "strings")


def _resolve(v: str, strings: Dict[str, str]) -> str:
    m = re.fullmatch(r"%(.+)%", v.strip())
    return strings.get(m.group(1).lower(), v) if m else v


def spec_deny_findings(items: List[Dict[str, Any]]) -> List[Finding]:
    """The spec itself must not carry a legacy literal, a GPU vendor driver, or known default password."""
    out: List[Finding] = []
    for it in items:
        if it["kind"] == "reg":
            for fid in reg_deny_hits(it["scope"], it["name"], it["type"], it["value"]):
                out.append(Finding("spec-" + fid, f"spec item {it['id']} sets the legacy value", "xbox-spec"))
        if it["kind"] == "driver" and gpu_inf_is_vendor_display(it["inf"], None):
            out.append(Finding("spec.gpu-driver", f"spec item {it['id']} bakes a GPU vendor display driver", "xbox-spec"))
        if it["kind"] == "user" and str(it.get("password_sha256", "")).lower() in WEAK_CREDENTIALS:
            out.append(Finding("deny.known-default-password", f"spec item {it['id']}: password equals known default", "xbox-spec"))
    return out


def spec_credential_warnings(items: List[Dict[str, Any]]) -> List[Finding]:
    """Weak-password WARNINGS (never findings): an SSOT-selected credential that is a known
    default. The gate stays green -- the operator owns the SSOT value -- but says so."""
    return [Finding("warn.weak-credential", f"spec item {it['id']}: the SSOT-selected password is a known default; set a stronger one in the SSOT", "xbox-spec")
            for it in items if it["kind"] == "user" and str(it["password_sha256"]).lower() in WEAK_CREDENTIALS]


def main(argv: Optional[List[str]] = None) -> int:
    """`spec_items.py SPEC` prints the item list (ids/kinds only) -- a debugging aid."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: spec_items.py xbox-spec.json", file=sys.stderr)
        return 2
    for it in items_from_spec(load_spec(argv[0])):
        print(f"{it['kind']:12} {it['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
