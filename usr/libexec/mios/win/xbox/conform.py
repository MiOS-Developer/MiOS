#!/usr/bin/env python3
# AI-hint: Offline MiOS-Xbox spec-conformance gate (xb-03/xb-16): checks the rendered Windows pipeline inputs (answer file incl. embedded base64 scripts, SetupComplete/first-logon payloads, .reg files, driver set, payload tree) and/or a serviced install.wim (wimlib-imagex info/dir/extract + hivexregedit on SOFTWARE/SYSTEM/Default NTUSER.DAT, driver store) against every xbox-spec.json item, plus a legacy-literal deny-list; Remote Desktop Services policy writes must match the named TerminalServer.admx policy (Microsoft's table, and the image's own Windows\PolicyDefinitions\TerminalServer.admx in image mode), the AutoLogon account must not be made an Administrator when the spec keeps it standard, no script may install a GPU vendor display driver (PnPUtil/DISM/Add-WindowsDriver/DevCon), and gpu.post_setup runs the tree's Read-MiOSGpuSpec (pwsh) on the guest spec the image ships; a Windows\Setup\Scripts stage that silences a state-changing cmdlet (-ErrorAction SilentlyContinue) is a false-ok step (deny.false-ok-step); exits non-zero with named findings.
# AI-related: usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/probe.py, tests/test-mios-xbox-conform.py, /.devloop/plan-mios-xbox.md
# AI-functions: Observations, parse_reg_text, parse_script, parse_answer_file, collect_rendered, collect_image, evaluate, run_rendered, run_image, main
"""MiOS-Xbox offline conformance gate.

  conform.py rendered --spec xbox-spec.json --input DIR|FILE [...] [--report R.json]
  conform.py image    --spec xbox-spec.json --wim install.wim [--index N]
                      [--answer autounattend.xml] [--workdir DIR] [--report R.json]

Exit 0 = conforming, 1 = findings, 2 = usage/spec/tool error.  Findings carry a
stable id (``missing:<item>``, ``off-spec:<item>``, ``deny.<literal>``,
``users.<...>``, ``drivers.gpu-vendor``, ...).  No credential is ever printed:
answer-file and script credentials are compared as sha256 digests only."""

from __future__ import annotations

import argparse
import base64
import dataclasses
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_items as S  # noqa: E402
from spec_items import Finding  # noqa: E402

REPORT_SCHEMA = "mios-xbox-conform/1"
SCRIPT_EXT = (".ps1", ".psm1", ".cmd", ".bat", ".vbs", ".txt")
MAX_INPUT_FILES = 20000           # the walker is bounded; a gate never walks a whole filesystem
MAX_TEXT_BYTES = 64 * 1024 * 1024
TOOL_TIMEOUT = 600


@dataclasses.dataclass
class Obs:
    scope: str
    path: str      # canonical lower-case path below the hive
    name: str      # lower-case value name ("" = default value)
    vtype: str
    value: Any
    source: str


@dataclasses.dataclass
class Observations:
    reg: List[Obs] = dataclasses.field(default_factory=list)
    features: Dict[str, List[Tuple[str, str]]] = dataclasses.field(default_factory=dict)  # name -> [(state, source)]
    tasks: Set[str] = dataclasses.field(default_factory=set)
    services: Set[str] = dataclasses.field(default_factory=set)
    files: Dict[str, str] = dataclasses.field(default_factory=dict)       # rel lower path -> abs path
    infs: Dict[str, Optional[str]] = dataclasses.field(default_factory=dict)  # "<base>|<where>" -> inf text
    driver_bases: Set[str] = dataclasses.field(default_factory=set)
    script_text: List[Tuple[str, str]] = dataclasses.field(default_factory=list)  # (source, text)
    credentials: List[Tuple[str, str, str]] = dataclasses.field(default_factory=list)  # (sha256, context, source)
    answer_users: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    autologon: Optional[Dict[str, Any]] = None
    build: Optional[str] = None
    branch: Optional[str] = None
    capabilities: Set[str] = dataclasses.field(default_factory=set)
    computer_names: List[Tuple[str, str]] = dataclasses.field(default_factory=list)  # (name, source)
    listing: Set[str] = dataclasses.field(default_factory=set)  # image-relative lower paths seen by wimlib dir
    answer_seen: bool = False
    first_logon_commands: List[Tuple[str, str]] = dataclasses.field(default_factory=list)  # (command line, source)
    group_adds: List[Tuple[str, str, str]] = dataclasses.field(default_factory=list)  # (group, member, source) from scripts
    # the image's own Windows\PolicyDefinitions\TerminalServer.admx: policy name -> {key, value, enabled, disabled}
    admx: Optional[Dict[str, Dict[str, Any]]] = None
    # package families staged offline under Program Files\WindowsApps (image mode; None = not listed)
    provisioned_apps: Optional[Set[str]] = None
    findings: List[Finding] = dataclasses.field(default_factory=list)

    def add_reg(self, path: str, name: str, vtype: str, value: Any, source: str) -> None:
        c = S.canon_reg_path(path)
        if c is None:
            return
        self.reg.append(Obs(c[0], c[1], name.lower(), S.canon_type(vtype), value, source))


# ---------------------------------------------------------------------------
# .reg text (rendered .reg files and hivexregedit --export output)
# ---------------------------------------------------------------------------

def _decode_hex(kind: str, hexstr: str) -> Tuple[str, Any]:
    raw = bytes(int(b, 16) for b in re.findall(r"[0-9a-fA-F]{2}", hexstr))
    k = kind.lower()
    if k in ("1", "2"):
        return ("sz" if k == "1" else "expand_sz"), raw.decode("utf-16-le", "replace").rstrip("\0")
    if k == "7":
        return "multi_sz", [x for x in raw.decode("utf-16-le", "replace").split("\0") if x]
    if k == "4":
        return "dword", int.from_bytes(raw[:4].ljust(4, b"\0"), "little")
    if k == "b":
        return "qword", int.from_bytes(raw[:8].ljust(8, b"\0"), "little")
    return "binary", raw.hex()


def parse_reg_text(text: str, source: str, obs: Observations) -> None:
    key = None
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        while line.endswith("\\") and i < len(lines):   # continuation
            line = line[:-1] + lines[i].strip()
            i += 1
        if not line or line.startswith(";") or line.startswith("Windows Registry Editor") or line == "REGEDIT4":
            continue
        m = re.match(r"^\[(-?)(.+)\]$", line)
        if m:
            key = None if m.group(1) else m.group(2)
            continue
        if key is None:
            continue
        m = re.match(r'^(@|"((?:[^"\\]|\\.)*)")\s*=\s*(.*)$', line)
        if not m:
            continue
        name = "" if m.group(1) == "@" else re.sub(r"\\(.)", r"\1", m.group(2))
        data = m.group(3).strip()
        src = f"{source}:{i}"
        if data == "-":
            continue
        if data.startswith('"'):
            obs.add_reg(key, name, "sz", re.sub(r"\\(.)", r"\1", data[1:-1] if data.endswith('"') else data[1:]), src)
        elif data.lower().startswith("dword:"):
            obs.add_reg(key, name, "dword", int(data[6:], 16), src)
        else:
            mh = re.match(r"^hex(?:\(([0-9a-fA-F]+)\))?:(.*)$", data)
            if mh:
                t, v = _decode_hex(mh.group(1) or "3", mh.group(2))
                obs.add_reg(key, name, t, v, src)


def parse_admx(text: str) -> Dict[str, Dict[str, Any]]:
    """Machine policies of an ADMX file: name -> {key, value (valueName), enabled, disabled} (decimal values)."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as e:
        raise ValueError(f"ADMX is not well-formed XML ({e})") from None
    for pol in root.iter():
        if _local(pol.tag) != "policy" or not pol.get("name"):
            continue
        ent: Dict[str, Any] = {"key": pol.get("key") or "", "value": pol.get("valueName") or "", "enabled": None, "disabled": None}
        for child in pol:
            tag = _local(child.tag)
            if tag in ("enabledValue", "disabledValue"):
                dec = next((d for d in child if _local(d.tag) == "decimal"), None)
                if dec is not None and re.fullmatch(r"\d+", dec.get("value") or ""):
                    ent["enabled" if tag == "enabledValue" else "disabled"] = int(dec.get("value"))
        out[pol.get("name")] = ent
    return out


# ---------------------------------------------------------------------------
# scripts (cmd / PowerShell / decoded answer-file blobs)
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"^(\*|%[^%]*%|\$\S*|__\w+__|\{\{.*\}\}|<.*>|@\w+|/\w+)$")


def _tokens(s: str) -> List[str]:
    """cmd / PowerShell argument split. Backslashes are literal (Windows paths); "..." honours \" and ""
    as an embedded quote (reg.exe / PowerShell); '...' is a PowerShell literal ('' = one quote). A POSIX
    shell tokenizer would eat the backslashes of 'Registry::HKCU\\...' and unquoted HKCU\\... paths and
    let a legacy value through the deny-list."""
    toks: List[str] = []
    cur: List[str] = []
    have = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            if have:
                toks.append("".join(cur))
                cur, have = [], False
            i += 1
            continue
        have = True
        if c in "\"'":
            q = c
            i += 1
            while i < n:
                if q == '"' and s[i] == "\\" and i + 1 < n and s[i + 1] == '"':
                    cur.append('"')
                    i += 2
                    continue
                if s[i] == q:
                    if i + 1 < n and s[i + 1] == q:
                        cur.append(q)
                        i += 2
                        continue
                    i += 1
                    break
                cur.append(s[i])
                i += 1
            continue
        cur.append(c)
        i += 1
    if have:
        toks.append("".join(cur))
    return toks


def _cred(obs: Observations, value: str, context: str, source: str) -> None:
    v = value.strip().strip("'\"")
    if not v or _PLACEHOLDER_RE.match(v):
        return
    obs.credentials.append((S.sha256_text(v), context, source))


_REG_ADD_RE = re.compile(r"\breg(?:\.exe)?\s+add\s+(.*)$", re.I)
_PS_PROP_RE = re.compile(r"\b(?:(?:Set|New)-ItemProperty(?:Value)?|sp)\b(.*)$", re.I)
_FEATURE_RE = [
    (re.compile(r"\bEnable-WindowsOptionalFeature\b.*?-FeatureName\s+['\"]?([\w.\-]+(?:['\"]?\s*,\s*['\"]?[\w.\-]+)*)", re.I), "enabled"),
    (re.compile(r"\bDisable-WindowsOptionalFeature\b.*?-FeatureName\s+['\"]?([\w.\-]+(?:['\"]?\s*,\s*['\"]?[\w.\-]+)*)", re.I), "disabled"),
    (re.compile(r"\bdism(?:\.exe)?\b.*?/enable-feature\b.*?/featurename:([\w.\-]+)", re.I), "enabled"),
    (re.compile(r"\bdism(?:\.exe)?\b.*?/disable-feature\b.*?/featurename:([\w.\-]+)", re.I), "disabled"),
]
_TASK_RES = [re.compile(r"\bschtasks(?:\.exe)?\s+/create\b.*?/tn\s+(\"[^\"]+\"|'[^']+'|\S+)", re.I),
             re.compile(r"\bRegister-ScheduledTask\b.*?-TaskName\s+(\"[^\"]+\"|'[^']+'|\S+)", re.I)]
_SVC_RES = [re.compile(r"\bsc(?:\.exe)?\s+create\s+(\"[^\"]+\"|\S+)", re.I),
            re.compile(r"\bNew-Service\b.*?-Name\s+(\"[^\"]+\"|'[^']+'|\S+)", re.I)]
_CRED_RES = [
    (re.compile(r"\bnet(?:\.exe)?\s+user\s+(\"[^\"]+\"|\S+)\s+(\"[^\"]*\"|'[^']*'|\S+)", re.I), "net user", 2),
    (re.compile(r"\s/rp\s+(\"[^\"]*\"|'[^']*'|\S+)", re.I), "schtasks /rp", 1),
    (re.compile(r"\bConvertTo-SecureString\b.*?(?:-String[\s:=]+)?(\"[^\"]*\"|'[^']*'|[A-Za-z0-9!@#%^&*_~-]+).*?-AsPlainText\b", re.I), "SecureString literal", 1),
    (re.compile(r"\bConvertTo-SecureString\b.*?-AsPlainText\b.*?(?:-String[\s:=]+)?(\"[^\"]*\"|'[^']*'|[A-Za-z0-9!@#%^&*_~-]+)", re.I), "SecureString literal", 1),
    (re.compile(r"(?:(?:\(|\b(?:echo|write-output)\s+)?(\"[^\"]*\"|'[^']*'|[A-Za-z0-9!@#%^&*_~-]+)\s*\)?)\s*\|\s*ConvertTo-SecureString\b.*?-AsPlainText\b", re.I), "SecureString literal (piped)", 1),
    (re.compile(r"-(?:Password|PlainPassword)[\s:=]+(\"[^\"]*\"|'[^']*'|[A-Za-z0-9!@#%^&*_~-]+)", re.I), "-Password literal", 1),
]
_NET_LG_RE = re.compile(r"\bnet(?:\.exe)?\s+localgroup\s+(\"[^\"]+\"|'[^']+'|\S+)\s+(\"[^\"]+\"|'[^']+'|\S+)\s+/add\b", re.I)
_ADD_LGM_RE = re.compile(r"\bAdd-LocalGroupMember\b(.*)$", re.I)
# well-known group SIDs a script may name instead of the (localized) group name
_GROUP_SIDS = {"s-1-5-32-544": "administrators", "s-1-5-32-545": "users", "s-1-5-32-555": "remote desktop users"}


def _group_adds(line: str) -> List[Tuple[str, str]]:
    """(group, member) pairs a script line adds (net localgroup ... /add, Add-LocalGroupMember -Group|-Name|-SID
    ... -Member ...); groups given by well-known SID are mapped to their English name. Variables are skipped."""
    out: List[Tuple[str, str]] = []
    for m in _NET_LG_RE.finditer(line):
        out.append((m.group(1).strip("'\"").lower(), m.group(2).strip("'\"").lower()))
    m = _ADD_LGM_RE.search(line)
    if m:
        toks = _tokens(m.group(1))
        args: Dict[str, str] = {}
        for i, t in enumerate(toks):
            if t.startswith("-") and i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                args[t.lstrip("-").lower()] = toks[i + 1].strip("'\"")
        g = args.get("group") or args.get("name") or args.get("sid")
        mem = args.get("member")
        if g and mem and not g.startswith("$") and not mem.startswith("$"):
            for one in re.split(r"\s*,\s*", mem):
                out.append((g.lower(), one.lower()))
    return [(_GROUP_SIDS.get(g, g), mem) for g, mem in out]


# A SetupComplete-stage step records "ok" unless its body throws, so a state-changing cmdlet told to SilentlyContinue
# turns its failure into a false ok (baseline N-e / R3-8: sshd_service and mios_daemons reported ok while nothing was
# registered). Matched per pipeline segment: probes (Get-*) and cleanups (Remove-*) may stay silent.
_STATE_CMDLETS = (r"(?:Set-Service|Start-Service|Restart-Service|New-Service|Enable-NetFirewallRule|Set-NetFirewallRule"
                  r"|New-NetFirewallRule|Register-ScheduledTask|Start-ScheduledTask|Add-WindowsCapability"
                  r"|Enable-WindowsOptionalFeature|Add-LocalGroupMember|Disable-LocalUser|Set-NetConnectionProfile"
                  r"|Set-ItemProperty|New-ItemProperty)")
_FALSE_OK_RE = re.compile(r"(?:^|[|;{(]|\btry\b)\s*&?\s*" + _STATE_CMDLETS
                          + r"\b[^|;{}\r\n]*?-(?:ErrorAction|EA)\s*:?\s*(?:SilentlyContinue|Ignore|0)\b", re.I)
_CAP_RE = re.compile(r"(?:Add-WindowsCapability\b.*?-Name\s+|/add-capability\b.*?/capabilityname:)(\"[^\"]+\"|'[^']+'|[\w.~\-]+)", re.I)
_RID500_RE = re.compile(r"(net(?:\.exe)?\s+user\s+\S+\s+/active:no|Disable-LocalUser\b|-500['\"\s]?\)?\s*\|\s*Disable-LocalUser)", re.I)


def _reg_add(args: str, obs: Observations, source: str) -> None:
    toks = _tokens(args)
    if not toks:
        return
    key = toks[0]
    name, vtype, data = None, "sz", ""
    j = 1
    while j < len(toks):
        t = toks[j].lower()
        if t == "/v" and j + 1 < len(toks):
            name = toks[j + 1]; j += 2; continue
        if t == "/ve":
            name = ""; j += 1; continue
        if t == "/t" and j + 1 < len(toks):
            vtype = toks[j + 1]; j += 2; continue
        if t == "/d" and j + 1 < len(toks):
            data = toks[j + 1]; j += 2; continue
        j += 1
    if name is None:
        return
    ct = S.canon_type(vtype)
    if ct in ("dword", "qword"):
        value: Any = S.canon_value(ct, data)
    elif ct == "binary":
        value = data.lower()
    elif ct == "multi_sz":
        value = [x for x in data.split("\\0") if x]
    else:
        value = data
    if name.lower() in ("defaultpassword", "autologonpassword"):
        _cred(obs, data, "Winlogon DefaultPassword", source)
        return
    obs.add_reg(key, name, ct, value, source)


def _ps_prop(args: str, obs: Observations, source: str) -> None:
    toks = _tokens(args)
    if not toks:
        return
    path, name, val, ptype = None, None, None, ""
    positional = []
    i = 0
    while i < len(toks):
        t = toks[i]
        t_low = t.lower()
        if t_low.startswith("-"):
            eq_m = re.match(r"^-([A-Za-z]+)[:=](.*)$", t)
            if eq_m:
                k, v = eq_m.group(1).lower(), eq_m.group(2).strip("'\"")
                if k in ("path", "literalpath", "pspath"): path = v
                elif k in ("name", "property"): name = v
                elif k == "value": val = v
                elif k in ("type", "propertytype"): ptype = v
                i += 1
                continue
            k = t_low.lstrip("-")
            if k in ("force", "passthru", "whatif", "confirm"):
                i += 1
                continue
            if i + 1 < len(toks) and not toks[i + 1].startswith("-"):
                v = toks[i + 1].strip("'\"")
                if k in ("path", "literalpath", "pspath"): path = v; i += 2; continue
                elif k in ("name", "property"): name = v; i += 2; continue
                elif k == "value": val = v; i += 2; continue
                elif k in ("type", "propertytype"): ptype = v; i += 2; continue
            i += 1
        else:
            positional.append(t.strip("'\""))
            i += 1

    if path is None and len(positional) > 0:
        path = positional[0]
    if name is None and len(positional) > 1:
        name = positional[1]
    if val is None and len(positional) > 2:
        val = positional[2]

    if path is None or name is None or val is None:
        return

    # Skip pure variable references ($var, $env:var), but NOT strings containing $ like 'Hunter2$Secret'
    if val.startswith("$") and not re.match(r"^\$\s*\(", val):
        return

    vt = {"dword": "dword", "qword": "qword", "string": "sz", "expandstring": "expand_sz",
          "binary": "binary", "multistring": "multi_sz"}.get(ptype.lower(), "dword" if re.fullmatch(r"-?\d+|0x[0-9a-fA-F]+", val) else "sz")
    if name.lower() in ("defaultpassword", "autologonpassword"):
        _cred(obs, val, "Winlogon DefaultPassword", source)
        return
    obs.add_reg(path, name, vt, S.canon_value(vt, val), source)


def _is_stage_script(source: str) -> bool:
    """A PowerShell provisioning stage Windows Setup runs from Windows\\Setup\\Scripts (the SYSTEM SetupComplete stage)."""
    p = source.split("#", 1)[0].replace("\\", "/").lower()
    return "windows/setup/scripts/" in p and p.endswith(".ps1")


def parse_script(text: str, source: str, obs: Observations) -> None:
    obs.script_text.append((source, text))
    stage_script = _is_stage_script(source)
    raw_lines = text.replace("\r\n", "\n").split("\n")
    joined_lines = []
    buffer = ""
    start_n = 1
    for n, raw in enumerate(raw_lines, 1):
        line_s = raw.strip()
        if not buffer:
            start_n = n
        if line_s.endswith("`") or line_s.endswith("^"):
            buffer += raw.rstrip()[:-1] + " "
            continue
        elif line_s.endswith("|"):
            buffer += raw + " "
            continue
        else:
            if buffer:
                joined_lines.append((start_n, buffer + raw))
                buffer = ""
            else:
                joined_lines.append((n, raw))
    if buffer:
        joined_lines.append((start_n, buffer))

    for n, raw in joined_lines:
        line = raw.strip()
        if not line or line.startswith("::") or line.lower().startswith("rem ") or line.startswith("#"):
            continue
        src = f"{source}:{n}"
        m = _REG_ADD_RE.search(line)
        if m:
            _reg_add(m.group(1), obs, src)
        m = _PS_PROP_RE.search(line)
        if m:
            _ps_prop(m.group(1), obs, src)
        for rx, state in _FEATURE_RE:
            for fm in rx.finditer(line):
                for f in re.split(r"['\"]?\s*,\s*['\"]?", fm.group(1)):
                    obs.features.setdefault(f.strip("'\"").lower(), []).append((state, src))
        for cm in _CAP_RE.finditer(line):
            obs.capabilities.add(cm.group(1).strip("'\"").lower())
        for rx in _TASK_RES:
            for tm in rx.finditer(line):
                obs.tasks.add(tm.group(1).strip("'\"").replace("/", "\\").split("\\")[-1].lower())
        for rx in _SVC_RES:
            for sm in rx.finditer(line):
                obs.services.add(sm.group(1).strip("'\"").lower())
        for rx, ctx, grp in _CRED_RES:
            for cm in rx.finditer(line):
                val = cm.group(grp)
                if ctx == "net user" and val.lower().startswith("/"):
                    continue
                _cred(obs, val, ctx, src)
        # Dedicated check for ConvertTo-SecureString with arbitrary preceding switches (e.g. -ErrorAction "Stop")
        for csm in re.finditer(r"\bConvertTo-SecureString\b(.*?)(?:$|[;|&])", line, re.I):
            c_args = csm.group(1)
            if re.search(r"-AsPlainText\b", c_args, re.I):
                c_toks = _tokens(c_args)
                c_val = None
                skip = False
                for idx, ct in enumerate(c_toks):
                    if skip:
                        skip = False
                        continue
                    m_s = re.match(r"^-String[:=](.*)$", ct, re.I)
                    if m_s:
                        c_val = m_s.group(1); break
                    if ct.lower() == "-string" and idx + 1 < len(c_toks):
                        c_val = c_toks[idx + 1]; break
                    if ct.startswith("-"):
                        if ct.lower() in ("-erroraction", "-warningaction", "-informationaction", "-ea", "-wa") and idx + 1 < len(c_toks):
                            skip = True
                        continue
                    if not ct.startswith("-"):
                        c_val = ct
                        break
                if c_val and not c_val.startswith("$"):
                    _cred(obs, c_val, "SecureString literal", src)
        for fid, ln in S.text_deny_hits(line):
            msg = {"deny.pnputil-nvidia-inf": "GPU vendor display driver INF referenced in script",
                   "deny.gpu-driver-script": "a script installs a GPU vendor display driver (DISM /Add-Driver, Add-WindowsDriver, "
                                             "PnPUtil or DevCon with an NVIDIA/AMD/Intel display INF or vendor driver directory)"
                   }.get(fid, "legacy literal in a pipeline input")
            obs.findings.append(Finding(fid, msg, src))
        for g, m in _group_adds(line):
            obs.group_adds.append((g, m, src))
        if stage_script and _FALSE_OK_RE.search(line):
            obs.findings.append(Finding("deny.false-ok-step", "a SetupComplete step silences a state-changing cmdlet "
                                        "(-ErrorAction SilentlyContinue/Ignore): its failure is recorded as ok", src))


# ---------------------------------------------------------------------------
# answer file
# ---------------------------------------------------------------------------

UNS = "urn:schemas-microsoft-com:unattend"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _decode_blob(b64: str) -> Optional[str]:
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", b64), validate=True)
    except Exception:  # noqa: BLE001
        return None
    if len(raw) > 1 and raw[1:2] == b"\0":
        return raw.decode("utf-16-le", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _password(el: ET.Element, suffix: str) -> Optional[str]:
    """Plain password from a <Password>/<AdministratorPassword> element; None if absent."""
    val = el.find(f"{{{UNS}}}Value")
    if val is None:
        return None
    v = val.text or ""
    plain = (el.findtext(f"{{{UNS}}}PlainText") or "true").strip().lower() != "false"
    if plain:
        return v
    try:
        d = base64.b64decode(v).decode("utf-16-le")
    except Exception:  # noqa: BLE001
        return v
    return d[: -len(suffix)] if d.endswith(suffix) else d


def parse_answer_file(path: str, source: str, obs: Observations) -> None:
    with open(path, "rb") as fh:
        data = fh.read(MAX_TEXT_BYTES)
    text = data.decode("utf-8", "replace")
    obs.answer_seen = True
    for fid, ln in S.text_deny_hits(text):
        obs.findings.append(Finding(fid, "legacy literal in the answer file", f"{source}:{ln}"))
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        obs.findings.append(Finding("answer.parse", f"answer file is not well-formed XML ({e})", source))
        return
    for el in root.iter():
        tag = _local(el.tag)
        if tag in ("Path", "CommandLine") and el.text:
            parse_script(el.text, f"{source}#{tag}", obs)
        elif tag == "File" and el.text and el.get("path"):
            parse_script(el.text, f"{source}#File:{el.get('path')}", obs)
        elif tag != "Value" and el.text and len(el.text.strip()) > 64 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", el.text.strip()):
            dec = _decode_blob(el.text)
            if dec is not None:
                parse_script(dec, f"{source}#{tag}(base64)", obs)
    for cn in root.iter(f"{{{UNS}}}ComputerName"):
        obs.computer_names.append(((cn.text or "").strip(), f"{source}#ComputerName"))
    for flc in root.iter(f"{{{UNS}}}FirstLogonCommands"):   # run as the first account to log on (fix-a M1)
        for cl in flc.iter(f"{{{UNS}}}CommandLine"):
            obs.first_logon_commands.append(((cl.text or "").strip(), f"{source}#FirstLogonCommands"))
    for comp in root.iter(f"{{{UNS}}}component"):
        for ua in comp.iter(f"{{{UNS}}}UserAccounts"):
            ap = ua.find(f"{{{UNS}}}AdministratorPassword")
            if ap is not None:
                pw = _password(ap, "AdministratorPassword")
                obs.answer_users.append({"name": "<RID-500>", "sha256": S.sha256_text(pw or ""), "empty": not pw,
                                         "source": f"{source}#AdministratorPassword"})
            for la in ua.iter(f"{{{UNS}}}LocalAccount"):
                name = la.findtext(f"{{{UNS}}}Name") or ""
                pe = la.find(f"{{{UNS}}}Password")
                pw = _password(pe, "Password") if pe is not None else None
                obs.answer_users.append({"name": name, "sha256": S.sha256_text(pw or ""), "empty": not pw,
                                         "group": la.findtext(f"{{{UNS}}}Group") or "",
                                         "source": f"{source}#LocalAccount"})
        al = comp.find(f"{{{UNS}}}AutoLogon")
        if al is not None:
            pe = al.find(f"{{{UNS}}}Password")
            pw = _password(pe, "Password") if pe is not None else None
            obs.autologon = {"name": al.findtext(f"{{{UNS}}}Username") or "",
                             "enabled": (al.findtext(f"{{{UNS}}}Enabled") or "").strip().lower() == "true",
                             "sha256": S.sha256_text(pw or ""), "empty": not pw,
                             "persistent": int(al.findtext(f"{{{UNS}}}LogonCount") or "0") > 1000,
                             "source": f"{source}#AutoLogon"}


# ---------------------------------------------------------------------------
# rendered-input collection
# ---------------------------------------------------------------------------

def _bounded_walk(root: str) -> Iterable[str]:
    n = 0
    real = os.path.realpath(root)
    if real == "/":
        raise SystemExit("conform: refusing to walk /")
    for d, dirs, files in os.walk(real):
        dirs.sort()
        for f in sorted(files):
            n += 1
            if n > MAX_INPUT_FILES:
                raise SystemExit(f"conform: more than {MAX_INPUT_FILES} files under {root}")
            yield os.path.join(d, f)


def _read_text(p: str) -> str:
    with open(p, "rb") as fh:
        raw = fh.read(MAX_TEXT_BYTES)
    if raw.startswith(b"\xff\xfe") or (len(raw) > 1 and raw[1:2] == b"\0"):
        return raw.decode("utf-16-le", "replace").lstrip("﻿")
    return raw.decode("utf-8", "replace").lstrip("﻿")


def _is_answer_file(p: str) -> bool:
    if not p.lower().endswith(".xml"):
        return False
    with open(p, "rb") as fh:
        head = fh.read(4096)
    return b"urn:schemas-microsoft-com:unattend" in head


def ingest_file(p: str, rel: str, obs: Observations) -> None:
    low = p.lower()
    rel_l = rel.replace("\\", "/").lower()
    obs.files[rel_l] = p
    if low.endswith(".inf"):
        base = re.sub(r"\.inf$", "", os.path.basename(low))
        obs.driver_bases.add(base)
        obs.infs[f"{base}|{rel}"] = _read_text(p)
    elif _is_answer_file(p):
        parse_answer_file(p, rel, obs)
    elif low.endswith(".reg"):
        parse_reg_text(_read_text(p), rel, obs)
    elif low.endswith(SCRIPT_EXT):
        parse_script(_read_text(p), rel, obs)
    elif low.endswith(".xml") and "/tasks/" in "/" + rel_l:
        obs.tasks.add(os.path.basename(rel_l))
    elif os.path.basename(low) == "terminalserver.admx":
        obs.admx = parse_admx(_read_text(p))
    elif low.endswith(".json") and os.path.basename(low) == "build-manifest.json":
        try:
            man = json.loads(_read_text(p))
            win = man.get("windows") or {}
            b = win.get("build") or man.get("build")
            if b:
                obs.build = str(b)
            br = win.get("branch") or man.get("branch")
            if br:
                obs.branch = str(br)
        except ValueError:
            obs.findings.append(Finding("manifest.parse", "build-manifest.json is not JSON", rel))


def collect_rendered(inputs: List[str]) -> Observations:
    obs = Observations()
    for inp in inputs:
        if os.path.isdir(inp):
            for p in _bounded_walk(inp):
                ingest_file(p, os.path.relpath(p, inp), obs)
        elif os.path.isfile(inp):
            ingest_file(inp, os.path.basename(inp), obs)
        else:
            raise SystemExit(f"conform: input not found: {inp}")
    for rel in list(obs.files):
        m = re.search(r"(?:^|/)tasks/(.+)$", rel)
        if m and "/system32/tasks/" in "/" + rel:
            obs.tasks.add(m.group(1).split("/")[-1])
    return obs


# ---------------------------------------------------------------------------
# offline image collection (wimlib-imagex + hivexregedit)
# ---------------------------------------------------------------------------

class ToolError(Exception):
    pass


def _run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except FileNotFoundError:
        raise ToolError(f"{cmd[0]} not installed")
    except subprocess.TimeoutExpired:
        raise ToolError(f"{cmd[0]} timed out")
    if check and r.returncode != 0:
        raise ToolError(f"{' '.join(cmd[:2])} failed rc={r.returncode}: {r.stderr.strip()[:300]}")
    return r


def wim_info(wim: str, index: int) -> Dict[str, str]:
    r = _run(["wimlib-imagex", "info", wim, str(index)])
    info = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip().lower()] = v.strip()
    return info


WINDOWSAPPS = "/Program Files/WindowsApps"
# <Name>_<Version>_<Arch>_<ResourceId>_<PublisherId>: a package folder DISM stages for a provisioned app
_PKG_DIR_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9.\-]*)_[^_/]+_[^_/]*_[^_/]*_([a-z0-9]{13})", re.I)


def windowsapps_families(listing: Iterable[str]) -> Set[str]:
    """Package family names (lower case) of the package folders in a wimlib listing of Program Files/WindowsApps."""
    fams: Set[str] = set()
    for ent in listing:
        rel = ent.replace("\\", "/")
        if rel.lower().startswith(WINDOWSAPPS.lower()):
            rel = rel[len(WINDOWSAPPS):]
        top = rel.strip("/").split("/", 1)[0]
        m = _PKG_DIR_RE.fullmatch(top)
        if m:
            fams.add(f"{m.group(1)}_{m.group(2)}".lower())
    return fams


def wim_dir(wim: str, index: int, path: str) -> List[str]:
    r = _run(["wimlib-imagex", "dir", wim, str(index), f"--path={path}"], check=False)
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def wim_extract(wim: str, index: int, paths: List[str], dest: str) -> None:
    if not paths:
        return
    _run(["wimlib-imagex", "extract", wim, str(index), *paths, f"--dest-dir={dest}",
          "--preserve-dir-structure", "--no-acls", "--nullglob"])


HIVE_FILES = {  # scope -> (image path, export prefix)
    "software": ("/Windows/System32/config/SOFTWARE", "HKEY_LOCAL_MACHINE\\SOFTWARE"),
    "system": ("/Windows/System32/config/SYSTEM", "HKEY_LOCAL_MACHINE\\SYSTEM"),
    "user": ("/Users/Default/NTUSER.DAT", "HKEY_USERS\\DefaultUser"),
}
_CBS_FEATURES = "Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\Notifications\\OptionalFeatures"
_CURRENT_VERSION = "Microsoft\\Windows NT\\CurrentVersion"
_DENY_KEYS = {  # keys read for the deny-list beyond the spec's own (the whole Default user hive is exported)
    "software": ["Policies\\Microsoft\\Windows\\Explorer"],
    "system": ["Setup"],   # Windows Setup's first-boot CmdLine: a SetupComplete/cmd hook there is denied (fix-a 6)
}
# a Setup\CmdLine that replaces Windows Setup's own first-boot command (oobe\windeploy.exe) with a script hook
_SETUP_HOOK_RE = re.compile(r"setupcomplete|\bcmd(?:\.exe)?\s+/[ck]\b", re.I)
_SCRIPT_REF_RE = re.compile(r"[\w.\-]+\.(?:ps1|cmd|bat|vbs)\b", re.I)
# Windows-defined places the pipeline stages payloads; the spec adds components.dest_dir.
_STAGING_ROOTS = ["/Windows/Setup/Scripts", "/ProgramData/MiOS"]
# the image's Remote Desktop Services administrative template (policy name -> registry value name)
_TS_ADMX = "/Windows/PolicyDefinitions/TerminalServer.admx"

# One perl process per hive reads single keys (non-recursive) with Win::Hivex and prints .reg text
# (hex(T) values), so a 100 MB SOFTWARE hive is opened once and never exported whole.
_HIVEX_KEYS_PL = r'''
use strict; use warnings; use Win::Hivex;
my ($file, $prefix, @keys) = @ARGV;
my $h = Win::Hivex->open($file);
print "Windows Registry Editor Version 5.00\n\n";
KEY: for my $k (@keys) {
  my $n = $h->root();
  for my $part (grep { length } split /\\/, $k) {
    $n = $h->node_get_child($n, $part);
    next KEY unless $n;
  }
  print "[$prefix$k]\n";
  for my $v ($h->node_values($n)) {
    my $name = $h->value_key($v);
    my ($t, $data) = $h->value_value($v);
    $name =~ s/\\/\\\\/g; $name =~ s/"/\\"/g;
    my $nm = length($name) ? "\"$name\"" : "@";
    printf "%s=hex(%x):%s\n", $nm, $t, join(",", map { sprintf "%02x", ord } split //, $data);
  }
  print "\n";
}
'''


def _offline_key(scope: str, path: str) -> str:
    parts = [x for x in path.replace("/", "\\").split("\\") if x]
    if scope == "system" and parts and parts[0].lower() == "currentcontrolset":
        parts = ["ControlSet001"] + parts[1:]
    return "\\" + "\\".join(parts)


def hive_keys(hive: str, prefix: str, keys: List[str]) -> str:
    r = _run(["perl", "-e", _HIVEX_KEYS_PL, hive, prefix, *keys], check=False)
    if r.returncode != 0:
        raise ToolError(f"Win::Hivex read of {os.path.basename(hive)} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def hive_export(hive: str, prefix: str, key: str = "\\") -> Optional[str]:
    r = _run(["hivexregedit", "--export", "--prefix", prefix, hive, key], check=False)
    if r.returncode != 0:
        if "not found" in r.stderr:
            return None
        raise ToolError(f"hivexregedit failed on {os.path.basename(hive)} {key}: {r.stderr.strip()[:200]}")
    return r.stdout


def wim_images(wim: str) -> List[Dict[str, str]]:
    """Every image of a WIM/ESD (wimlib-imagex info), as dicts of lower-case field -> value."""
    r = _run(["wimlib-imagex", "info", wim])
    images: List[Dict[str, str]] = []
    for line in r.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        if k == "index":
            images.append({})
        if images:
            images[-1][k] = v.strip()
    return images


def pick_index(wim: str, edition: str) -> int:
    """The image whose Edition ID equals the spec's source.edition (UUP 'professional' = 'Professional')."""
    hits = [int(i["index"]) for i in wim_images(wim) if i.get("edition id", "").lower() == edition.lower()]
    if len(hits) != 1:
        raise ToolError(f"{len(hits)} images with Edition ID {edition!r} in {os.path.basename(wim)}; pass --index")
    return hits[0]


def collect_image(wim: str, index: int, items: List[Dict[str, Any]], workdir: str,
                  answer: Optional[str] = None, staging_roots: Optional[List[str]] = None) -> Observations:
    obs = Observations()
    info = wim_info(wim, index)
    obs.build = info.get("build") or None
    ex = os.path.join(workdir, "extract")
    os.makedirs(ex, exist_ok=True)
    roots = list(staging_roots or _STAGING_ROOTS)
    for it in items:
        if it["kind"] == "component" and "/" in it["path"]:
            r = "/" + it["path"].rsplit("/", 1)[0]
            if r not in roots:
                roots.append(r)
    # listing of the staging roots (wimlib dir is recursive): installers are matched by name/glob
    for r in roots:
        for ent in wim_dir(wim, index, r):
            obs.listing.add(ent.lstrip("/").lower())
    want_files = [("/" + it["path"].lstrip("/")) for it in items if it["kind"] in ("component", "file_hash")]
    want_files += ["/" + it["proof"].replace("\\", "/").lstrip("/") for it in items if it["kind"] == "capability" and it.get("proof")]
    want_files += [f"/Windows/System32/Tasks/{it['task'].replace(chr(92), '/')}" for it in items
                   if it["kind"] == "component" and it.get("task")]
    for it in items:
        if it["kind"] == "build_family":
            want_files.append("/" + it.get("start2_target", S.START2_TARGET))
    want_files += ["/" + it["spec_path"].lstrip("/") for it in items if it["kind"] == "gpu_step" and it.get("spec_path")]
    if any(it["kind"] == "reg" and it.get("policy") for it in items):
        want_files.append(_TS_ADMX)   # RDS policy names are checked against the image's own ADMX (F5)
    wim_extract(wim, index, [v[0] for v in HIVE_FILES.values()] + [
        "/Windows/Setup/Scripts/*", "/Windows/System32/DriverStore/FileRepository/*/*.inf",
        "/Windows/Panther/unattend.xml", "/Windows/System32/Sysprep/unattend.xml",
    ] + want_files, ex)
    admx = os.path.join(ex, _TS_ADMX.lstrip("/"))
    if os.path.isfile(admx):
        obs.admx = parse_admx(_read_text(admx))
    want: Dict[str, Set[str]] = {"software": set(_DENY_KEYS["software"]) | {_CURRENT_VERSION}, "system": set(_DENY_KEYS["system"])}
    for it in items:
        if it["kind"] == "reg" and it["scope"] in want:
            want[it["scope"]].add(it["path"])
        elif it["kind"] == "feature":
            want["software"].add(f"{_CBS_FEATURES}\\{it['name']}")
    for scope, (ipath, prefix) in HIVE_FILES.items():
        local = os.path.join(ex, ipath.lstrip("/"))
        if not os.path.isfile(local):
            obs.findings.append(Finding("image.hive-missing", f"{ipath} not in image index {index}", os.path.basename(wim)))
            continue
        if scope == "user":   # small: export the whole Default profile hive so the deny-list sees every value
            out = hive_export(local, prefix)
            if out:
                parse_reg_text(out, "NTUSER.DAT", obs)
            continue
        out = hive_keys(local, prefix, sorted(_offline_key(scope, k) for k in want[scope]))
        parse_reg_text(out, os.path.basename(ipath), obs)
    cbs = _CBS_FEATURES.lower()
    for o in obs.reg:
        if o.scope == "software" and o.path.startswith(cbs + "\\") and o.name == "selection":
            obs.features.setdefault(o.path.rsplit("\\", 1)[-1], []).append(("enabled" if o.value == 1 else "disabled", o.source))
        if o.scope == "software" and o.path == _CURRENT_VERSION.lower():
            if o.name == "buildbranch":
                obs.branch = str(o.value)
            elif o.name == "currentbuild" and not obs.build:
                obs.build = str(o.value)
    if any(it["kind"] == "appx" and it.get("inbox") for it in items):
        obs.provisioned_apps = windowsapps_families(wim_dir(wim, index, WINDOWSAPPS))
    repo = "/Windows/System32/DriverStore/FileRepository"
    for ent in wim_dir(wim, index, repo):
        rel = ent[len(repo):].strip("/")
        if rel and "/" not in rel:
            obs.driver_bases.add(re.sub(r"\.inf_.*$", "", rel.lower()))
    rdir = os.path.join(ex, repo.lstrip("/"))
    if os.path.isdir(rdir):
        for d in sorted(os.listdir(rdir)):
            for f in sorted(os.listdir(os.path.join(rdir, d))):
                if f.lower().endswith(".inf"):
                    obs.infs[f"{f[:-4].lower()}|{repo}/{d}/{f}"] = _read_text(os.path.join(rdir, d, f))
    for p in _bounded_walk(ex):
        rel = os.path.relpath(p, ex).replace("\\", "/")
        rl = rel.lower()
        obs.files[rl] = p
        if rl.startswith("windows/setup/scripts/") and rl.endswith(SCRIPT_EXT):
            parse_script(_read_text(p), "image:/" + rel, obs)
        elif rl.startswith("windows/system32/tasks/"):
            obs.tasks.add(os.path.basename(rl))
        elif rl.endswith("unattend.xml") and _is_answer_file(p):
            parse_answer_file(p, "image:/" + rel, obs)
    if answer:
        parse_answer_file(answer, os.path.basename(answer), obs)
    return obs


# ---------------------------------------------------------------------------
# evaluation (shared by both modes)
# ---------------------------------------------------------------------------

def _sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_file(obs: Observations, path: str) -> Optional[str]:
    want = path.replace("\\", "/").lstrip("/").lower()
    if want in obs.files:
        return obs.files[want]
    for rel, p in obs.files.items():
        if rel.endswith("/" + want):
            return p
    return None


def _staged(obs: Observations, name: str) -> bool:
    """An installer staged anywhere in the inputs (basename or glob, case-insensitive)."""
    pat = name.replace("\\", "/").split("/")[-1].lower()
    names = {r.rsplit("/", 1)[-1] for r in list(obs.files) + list(obs.listing)}
    return any(fnmatch.fnmatchcase(n, pat) for n in names)


def _mentioned(obs: Observations, name: str) -> bool:
    pat = name.replace("\\", "/").split("/")[-1].lower()
    rx = re.compile(re.escape(pat).replace("\\*", r"[\w.\-*%]*"))
    return any(rx.search(t.lower()) for _, t in obs.script_text)


def _user_context(src: str, obs: Observations) -> bool:
    """A script source that runs as the logged-on user: an answer-file CommandLine (FirstLogonCommands /
    LogonCommands) or a file a FirstLogonCommand names."""
    if src.endswith("#CommandLine"):
        return True
    refs = {m.group(0).lower() for cl, _ in obs.first_logon_commands for m in _SCRIPT_REF_RE.finditer(cl)}
    return os.path.basename(src.split("#", 1)[0].replace("\\", "/")).lower() in refs


GPU_STEP_TIMEOUT = 120
# Read-MiOSGpuSpec of the tree's GPU step on the shipped guest spec; prints one JSON line. Paths are passed as
# PowerShell single-quoted literals (no expansion); the shipped spec is data only (ConvertFrom-Json).
_GPU_READ_PS = r"""$ErrorActionPreference = 'Stop'
try {{
  . {script} -LibraryOnly
  $r = Read-MiOSGpuSpec {spec}
  $ids = @($r.Gpu.vendors.PSObject.Properties | ForEach-Object {{ [string]$_.Value.vendor_id }})
  [pscustomobject]@{{ mode = [string]$r.Mode; task = [string]$r.Gpu.task_name; record = [string]$r.Gpu.record_path;
    vendor_ids = $ids }} | ConvertTo-Json -Compress
}} catch {{ [Console]::Error.WriteLine('ERROR: ' + $_.Exception.Message); exit 3 }}
"""


def _ps_literal(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _gpu_spec_findings(it: Dict[str, Any], obs: Observations, tree: Optional[str] = None) -> List[Finding]:
    """gpu.post_setup offline (item 8): the GPU detection step must be able to read the guest spec the image SHIPS
    (the R3/M1 defect: 'SSOT table windows.xbox.gpu missing'). Runs the TREE's mios-gpu-driver.ps1 (never code taken
    from the image; gpu.script proves the shipped copy is byte-identical) with -LibraryOnly under pwsh and compares
    Read-MiOSGpuSpec's mode / task / record / vendor ids with the spec."""
    spec = _find_file(obs, it["spec_path"])
    if not spec:
        return [Finding("missing:gpu.post_setup", f"the guest spec {it['spec_path']} the GPU step reads is not shipped")]
    script = os.path.join(tree or _default_root(), it["script"])
    if not os.path.isfile(script):
        return [Finding("missing:gpu.post_setup", f"the tree has no GPU step {it['script']} (windows.xbox.drivers.gpu_step)")]
    pwsh = os.environ.get("MIOS_PWSH") or shutil.which("pwsh")
    if not pwsh:
        raise ToolError("pwsh is required to run Read-MiOSGpuSpec on the shipped guest spec (gpu.post_setup)")
    cmd = _GPU_READ_PS.format(script=_ps_literal(script), spec=_ps_literal(spec))
    try:
        r = subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                           text=True, timeout=GPU_STEP_TIMEOUT, env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
    except subprocess.TimeoutExpired:
        raise ToolError("pwsh Read-MiOSGpuSpec timed out")
    if r.returncode != 0:
        why = ((r.stderr or r.stdout).strip().splitlines() or ["no output"])[-1][:300]
        return [Finding("off-spec:gpu.post_setup", f"Read-MiOSGpuSpec fails on the shipped guest spec: {why}", it["spec_path"])]
    try:
        got = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        got = None
    if not isinstance(got, dict):
        return [Finding("off-spec:gpu.post_setup", "Read-MiOSGpuSpec returned no JSON object", it["spec_path"])]
    out: List[Finding] = []
    for k, want in (("mode", it.get("mode")), ("task", it.get("task")), ("record", it.get("record"))):
        if want and str(got.get(k, "")).lower() != str(want).lower():
            out.append(Finding("off-spec:gpu.post_setup", f"the shipped guest spec gives the GPU step {k} {got.get(k)!r}, spec {want!r}",
                               it["spec_path"]))
    raw = got.get("vendor_ids")   # ConvertTo-Json renders a one-element array as a scalar
    ids = sorted({str(v).upper() for v in (raw if isinstance(raw, list) else [raw]) if v})
    if it.get("vendor_ids") and ids != sorted(it["vendor_ids"]):
        out.append(Finding("off-spec:gpu.post_setup", f"the shipped guest spec gives vendor ids {ids}, spec {sorted(it['vendor_ids'])}",
                           it["spec_path"]))
    return out


def _ts_policy_findings(it: Dict[str, Any], obs: Observations, mode: str) -> List[Finding]:
    """A Remote Desktop Services policy item: Microsoft's policy table (all modes) and, in image mode, the image's own
    TerminalServer.admx must define the named policy with this value name and value (F5: camera = fDisableCameraRedir,
    location = fDisableLocationRedir)."""
    out: List[Finding] = []
    why = S.ts_policy_problem(it.get("policy"), it["name"], it["value"])
    if why:
        out.append(Finding(f"off-spec:{it['id']}.policy", why, "xbox-spec"))
    if mode != "image" or not it.get("policy"):
        return out
    if obs.admx is None:
        out.append(Finding("missing:policy-definitions", f"{_TS_ADMX} is not in the image: the RDS policy names cannot be verified"))
        return out
    pol = obs.admx.get(str(it["policy"]))
    if pol is None:
        out.append(Finding(f"off-spec:{it['id']}.policy", f"the image's TerminalServer.admx defines no policy {it['policy']}", _TS_ADMX))
    elif pol["value"].lower() != it["name"].lower() or S.canon_item_path("software", re.sub(r"(?i)^software\\", "", pol["key"])) != \
            S.canon_item_path("software", it["path"]):
        out.append(Finding(f"off-spec:{it['id']}.policy", f"the image's TerminalServer.admx policy {it['policy']} writes "
                           f"{pol['key']}\\{pol['value']}, not {it['path']}\\{it['name']}", _TS_ADMX))
    elif S.canon_value("dword", it["value"]) not in (pol["enabled"], pol["disabled"]):
        out.append(Finding(f"off-spec:{it['id']}.policy", f"{it['name']}={it['value']!r} is neither the Enabled ({pol['enabled']}) nor "
                           f"the Disabled ({pol['disabled']}) value of {it['policy']}", _TS_ADMX))
    return out


def evaluate(items: List[Dict[str, Any]], obs: Observations, mode: str,
             policy: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[Finding]]:
    gpu_re = (policy or {}).get("gpu_provider_re", S.GPU_PROVIDER_RE)
    findings: List[Finding] = list(S.spec_deny_findings(items)) + list(obs.findings)
    results: List[Dict[str, Any]] = []
    by_key: Dict[Tuple[str, str, str], List[Obs]] = {}
    for o in obs.reg:
        by_key.setdefault((o.scope, o.path, o.name), []).append(o)
    user_hashes = {str(it["password_sha256"]).lower() for it in items if it["kind"] == "user"}

    def res(it: Dict[str, Any], status: str, detail: str = "") -> None:
        results.append({"id": it["id"], "kind": it["kind"], "status": status, "detail": detail})

    for it in items:
        k = it["kind"]
        before = len(findings)
        if k == "reg":
            key = (it["scope"], S.canon_item_path(it["scope"], it["path"]), it["name"].lower())
            seen = by_key.get(key, [])
            if it.get("absent"):
                for o in seen:
                    findings.append(Finding(f"off-spec:{it['id']}", f"{it['name']} must be absent", o.source))
            else:
                if not seen:
                    findings.append(Finding(f"missing:{it['id']}", f"{it['name']} under {it['path']} is never set"))
                svc = re.fullmatch(r"currentcontrolset\\services\\([^\\]+)", key[1])
                if mode == "image" and seen and svc and not it["id"].startswith("debloat.") and not by_key.get(("system", key[1], "imagepath")):
                    findings.append(Finding(f"off-spec:{it['id']}.orphan-service", f"Services\\{svc.group(1)} carries {it['name']} but "
                                            "no ImagePath: the service is not registered in the image (orphan key)", seen[0].source))
                for o in seen:
                    if not S.value_equal(it, o.vtype, o.value):
                        want = it["value"] if not it.get("kv_field") else f"{it['kv_field']}={it['value']}"
                        findings.append(Finding(f"off-spec:{it['id']}", f"{it['name']}={S.canon_value(o.vtype, o.value)!r}, spec {want!r}", o.source))
            if it.get("policy") or S.is_ts_policy_key(it["scope"], it["path"]):
                findings.extend(_ts_policy_findings(it, obs, mode))
        elif k == "feature":
            if it["state"] == "conditional":
                res(it, "skipped", "conditional on nested virtualization; verified in-guest"); continue
            states = obs.features.get(it["name"].lower(), [])
            if it["state"] == "enabled" and not any(s == "enabled" for s, _ in states):
                findings.append(Finding(f"missing:{it['id']}", f"optional feature {it['name']} is not enabled"))
            for s, src in states:
                if s != it["state"]:
                    findings.append(Finding(f"off-spec:{it['id']}", f"optional feature {it['name']} {s}, spec {it['state']}", src))
        elif k == "capability":
            if mode == "image" and it.get("proof"):
                # baked offline (F4): the capability's own file must be IN the image, not merely named by a script
                if not _find_file(obs, it["proof"].replace("\\", "/")):
                    findings.append(Finding(f"missing:{it['id']}.offline", f"capability {it['name']} is not baked: {it['proof']} not in the image"))
            elif mode == "image" and not obs.script_text:
                res(it, "skipped", "capabilities are verified in-guest"); continue
            if not any(c.startswith(it["name"].lower()) for c in obs.capabilities) and not (mode == "image" and it.get("proof")):
                findings.append(Finding(f"missing:{it['id']}", f"capability {it['name']} is never added"))
        elif k == "driver":
            if it["inf"].lower() not in obs.driver_bases:
                findings.append(Finding(f"missing:{it['id']}", f"driver {it['inf']}.inf not in the driver set"))
        elif k == "guest_tool":
            if not it.get("installer"):
                res(it, "skipped", "service-only guest tool; verified in-guest"); continue
            if not _staged(obs, it["installer"]):
                findings.append(Finding(f"missing:{it['id']}", f"installer {it['installer']} not staged"))
            elif mode == "rendered" and not _mentioned(obs, it["installer"]):
                findings.append(Finding(f"missing:{it['id']}", f"installer {it['installer']} is staged but no setup script runs it"))
        elif k in ("component", "file_hash"):
            p = _find_file(obs, it["path"])
            if not p:
                findings.append(Finding(f"missing:{it['id']}", f"{it['path']} not present"))
            elif it.get("sha256") and _sha256_file(p) != str(it["sha256"]).lower():
                findings.append(Finding(f"off-spec:{it['id']}", f"{it['path']} sha256 differs from the spec source"))
            if k == "component" and it.get("task"):
                tname = it["task"].replace("\\", "/").split("/")[-1].lower()
                if tname not in obs.tasks and not _mentioned(obs, it["path"]):
                    findings.append(Finding(f"missing:{it['id']}.task", f"scheduled task {it['task']} is never registered"))
            if k == "component" and it.get("service") and it["service"].lower() not in obs.services and not \
                    any(o.scope == "system" and o.path == f"currentcontrolset\\services\\{it['service'].lower()}" for o in obs.reg):
                findings.append(Finding(f"missing:{it['id']}.service", f"service {it['service']} is never created"))
        elif k == "build_family":
            fams = it.get("families") or {f: {} for f in (it["family"] if isinstance(it["family"], list) else [it["family"]])}
            if obs.branch is None and obs.build is None:
                res(it, "skipped", "no image build/branch in these inputs"); continue
            if obs.branch is not None:
                fam = next((f for f in fams if f.lower() == obs.branch.lower()), None)
            else:
                fam = next((f for f in fams if str(obs.build).startswith(str(f))), None)
            if fam is None:
                findings.append(Finding("start.family-mismatch", f"image {it.get('family_source', 'build')} {obs.branch or obs.build} has no Start layout assets (families: {', '.join(sorted(fams))})"))
            else:
                want = (fams.get(fam) or {}).get("start2_sha256")
                if not want:
                    findings.append(Finding(f"missing:start.layout_assets.{fam}", "start2.bin of this family is not captured (xb-11)"))
                else:
                    p = _find_file(obs, it.get("start2_target", S.START2_TARGET))
                    if not p:
                        findings.append(Finding("missing:start.start2_bin", "Default profile start2.bin not staged"))
                    elif _sha256_file(p) != want:
                        findings.append(Finding("off-spec:start.start2_bin", f"staged start2.bin is not the {fam} asset"))
        elif k == "user":
            if not obs.answer_seen:
                res(it, "skipped", "no answer file in these inputs"); continue
            want = str(it["password_sha256"]).lower()
            la = [u for u in obs.answer_users if u["name"].lower() == it["name"].lower()]
            al = obs.autologon if obs.autologon and obs.autologon["name"].lower() == it["name"].lower() else None
            if not la and not al:
                findings.append(Finding(f"users.missing:{it['id']}", "spec account is not created by the answer file"))
            for u in la:
                if u["empty"]:
                    findings.append(Finding("users.empty-credential", f"{it['id']} has an empty password", u["source"]))
                elif u["sha256"] != want:
                    findings.append(Finding(f"users.password:{it['id']}", "answer-file password does not equal the SSOT value", u["source"]))
            spec_groups = {str(g).lower() for g in it.get("groups") or []}
            for u in la:   # the answer file's LocalAccount <Group> must be one of the spec's groups for that account
                ga = {g.strip().lower() for g in re.split(r"[;,]", str(u.get("group") or "")) if g.strip()}
                if spec_groups and ga - spec_groups:
                    findings.append(Finding(f"off-spec:users.group:{it['id']}", f"answer-file group(s) {', '.join(sorted(ga - spec_groups))} "
                                            f"not in the spec groups of {it['id']}", u["source"]))
            if it.get("autologon") and spec_groups and "administrators" not in spec_groups:
                # F2: the AutoLogon account is a STANDARD user; an Administrators membership from the answer file or
                # any setup script (net localgroup / Add-LocalGroupMember, by name or S-1-5-32-544) is a finding
                srcs = [u["source"] for u in la if "administrators" in
                        {g.strip().lower() for g in re.split(r"[;,]", str(u.get("group") or ""))}]
                srcs += [src for g, mem, src in obs.group_adds if g == "administrators" and mem.split("\\")[-1] == it["name"].lower()]
                for src in srcs:
                    findings.append(Finding("users.autologon-admin", f"the AutoLogon account {it['id']} is made an Administrator; "
                                            "the spec keeps it a standard user (operator decision F2)", src))
            if it.get("autologon"):
                if not obs.autologon or not obs.autologon["enabled"]:
                    findings.append(Finding("users.autologon", f"no enabled AutoLogon for {it['id']}"))
                elif al is None:
                    findings.append(Finding("users.autologon", f"AutoLogon signs in a different account than {it['id']}", obs.autologon["source"]))
                else:
                    if al["empty"] or al["sha256"] != want:
                        findings.append(Finding(f"users.password:{it['id']}", "AutoLogon password does not equal the SSOT value", al["source"]))
                    if it.get("persistent") and not al["persistent"]:
                        findings.append(Finding("users.autologon-persistent", "AutoLogon has a finite LogonCount", al["source"]))
        elif k == "rid500":
            hits = [src for src, t in obs.script_text if _RID500_RE.search(t)]
            if not hits:
                findings.append(Finding(f"missing:{it['id']}", "no setup step disables the built-in Administrator (RID 500)"))
            elif all(_user_context(src, obs) for src in hits):
                findings.append(Finding(f"users.rid500-not-system", "RID 500 is disabled only by a FirstLogonCommand (runs as the "
                                        "logged-on user; a standard user under UAC needs admin consent): it must be a SYSTEM step", hits[0]))
        elif k == "computer_name":
            if not obs.answer_seen:
                res(it, "skipped", "no answer file in these inputs"); continue
            if not obs.computer_names:
                findings.append(Finding(f"missing:{it['id']}", "answer file sets no ComputerName"))
            for name, src in obs.computer_names:
                if name.lower() != str(it["value"]).lower():
                    findings.append(Finding(f"off-spec:{it['id']}", f"ComputerName {name!r}, spec {it['value']!r}", src))
        elif k == "gpu_step":
            if not it.get("spec_path"):
                res(it, "skipped", "verified in-guest by probe.py"); continue
            findings.extend(_gpu_spec_findings(it, obs))
        elif k == "appx":
            if not (it.get("inbox") and mode == "image"):
                res(it, "skipped", "verified in-guest by probe.py" if not it.get("inbox") else
                    "integrated offline: verified by the image gate and probe.py"); continue
            if obs.provisioned_apps is None or not obs.provisioned_apps:
                findings.append(Finding(f"missing:{it['id']}", f"no provisioned app under {WINDOWSAPPS} in the image"))
            elif str(it["pfn"]).lower() not in obs.provisioned_apps:
                findings.append(Finding(f"missing:{it['id']}", f"inbox app {it['pfn']} is not provisioned in the image"))
        new = findings[before:]
        res(it, "fail" if new else "pass", "; ".join(f.id for f in new))

    if mode == "image" and obs.admx is not None:   # the image's own ADMX is the complete list of policy values
        ts_known = {str(p["value"]).lower() for p in obs.admx.values() if p.get("value")}
    else:                                          # Microsoft's table plus what the spec itself writes there
        ts_known = {v[0].lower() for v in S.TS_POLICIES.values()} | {
            it["name"].lower() for it in items if it["kind"] == "reg" and S.is_ts_policy_key(it["scope"], it["path"])}
    for o in obs.reg:
        for fid in S.reg_deny_hits(o.scope, o.name, o.vtype, o.value):
            findings.append(Finding(fid, f"legacy value {o.name}={o.value!r}", o.source))
        if o.scope == "software" and o.path == S.TS_POLICY_KEY.lower() and o.name not in ts_known:
            findings.append(Finding("deny.unknown-ts-policy-value", f"{o.name} under {S.TS_POLICY_KEY} is no TerminalServer.admx "
                                    "policy value (Windows ignores it; the intended redirection policy is not set)", o.source))
        if o.scope == "system" and o.path == "setup" and o.name == "cmdline" and _SETUP_HOOK_RE.search(str(o.value or "")):
            findings.append(Finding("deny.setup-cmdline-hook", "Setup\\CmdLine replaces Windows Setup's first-boot command "
                                    "(oobe\\windeploy.exe) with a script hook", o.source))
    if obs.first_logon_commands and obs.autologon:
        first = obs.autologon["name"].lower()
        acct = next((u for u in obs.answer_users if u["name"].lower() == first), None)
        if acct is not None and str(acct.get("group", "")).lower() != "administrators":
            findings.append(Finding("users.firstlogon-standard-user", "FirstLogonCommands run as the AutoLogon account, a standard "
                                    "user: under UAC they need administrator consent and do not run on Cancel (privileged work "
                                    "belongs in SetupComplete / a SYSTEM task)", obs.first_logon_commands[0][1]))
    for key, text in sorted(obs.infs.items()):
        base, where = key.split("|", 1)
        if S.gpu_inf_is_vendor_display(base, text, gpu_re):
            findings.append(Finding("drivers.gpu-vendor", f"GPU vendor display driver {base}.inf in the driver set", where))
    for base in sorted(obs.driver_bases):
        if not any(k.startswith(base + "|") for k in obs.infs) and S.gpu_inf_is_vendor_display(base, None, gpu_re):
            findings.append(Finding("drivers.gpu-vendor", f"GPU vendor display driver {base}.inf in the driver store"))
    # Answer-file credentials (XB-4): known-default password is a hard failure (deny.known-default-password).
    for u in obs.answer_users + ([obs.autologon] if obs.autologon else []):
        if u["empty"]:
            findings.append(Finding("users.empty-credential", "answer file carries an empty password", u["source"]))
        elif u["sha256"] in S.WEAK_CREDENTIALS:
            findings.append(Finding("deny.known-default-password", "answer file carries a known default credential", u["source"]))
            findings.append(Finding("deny.literal-credential", "answer file carries a known default credential", u["source"]))
        elif user_hashes and u["sha256"] in user_hashes:
            continue
        elif user_hashes:
            findings.append(Finding("users.credential-not-from-ssot", "answer-file credential is not an SSOT value", u["source"]))
    svc_names = {str(it.get("service_account", "")).lower() for it in items if it["kind"] == "user" and it.get("service_account")}
    if obs.autologon and obs.autologon["name"].lower() in svc_names | {"administrator"}:
        findings.append(Finding("users.autologon-service-account",
                                "AutoLogon signs in the service account / built-in Administrator (operator decision: never)",
                                obs.autologon["source"]))
    for h, ctx, src in obs.credentials:
        if h in S.WEAK_CREDENTIALS:
            findings.append(Finding("deny.known-default-password", f"known default credential literal ({ctx})", src))
            findings.append(Finding("deny.literal-credential", f"credential literal not resolved from the SSOT ({ctx})", src))
        elif h not in user_hashes:
            findings.append(Finding("deny.literal-credential", f"credential literal not resolved from the SSOT ({ctx})", src))
    seen: Set[Tuple[str, str]] = set()
    uniq = []
    for f in findings:
        if (f.id, f.source) not in seen:
            seen.add((f.id, f.source))
            uniq.append(f)
    return results, uniq


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _report(mode: str, results: List[Dict[str, Any]], findings: List[Finding], path: Optional[str],
            warnings: Optional[List[Finding]] = None) -> int:
    rep = {"schema": REPORT_SCHEMA, "mode": mode, "ok": not findings,
           "items": results, "findings": [f.as_dict() for f in findings],
           "warnings": [w.as_dict() for w in warnings or []]}
    if path:
        d = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".conform.", dir=d)
        with os.fdopen(fd, "w") as fh:
            json.dump(rep, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dfd = os.open(d, os.O_RDONLY)
        os.fsync(dfd)
        os.close(dfd)
    passed = sum(1 for r in results if r["status"] == "pass")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    for f in findings:
        print(f"FINDING {f}")
    for w in warnings or []:
        print(f"WARNING {w}")
    print(f"conform[{mode}]: {passed} pass, {len(results) - passed - skipped} fail, {skipped} skipped, "
          f"{len(findings)} findings -> {'CONFORMING' if not findings else 'NON-CONFORMING'}")
    return 0 if not findings else 1


def _load(spec_path: str, root: Optional[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    doc = S.load_spec(spec_path)
    return S.items_from_spec(doc, root), S.spec_policy(doc)


def run_rendered(spec_path: str, inputs: List[str], report: Optional[str] = None, root: Optional[str] = None) -> int:
    items, policy = _load(spec_path, root)
    obs = collect_rendered(inputs)
    if not obs.answer_seen:
        obs.findings.append(Finding("answer.missing", "no unattend answer file among the rendered inputs"))
    results, findings = evaluate(items, obs, "rendered", policy)
    return _report("rendered", results, findings, report, S.spec_credential_warnings(items))


def run_image(spec_path: str, wim: str, index: int, answer: Optional[str], workdir: Optional[str],
              report: Optional[str] = None, root: Optional[str] = None) -> int:
    items, policy = _load(spec_path, root)
    if not index:
        doc = S.load_spec(spec_path)
        edition = (((doc.get("spec") or {}).get("source") or {}).get("edition")) if isinstance(doc, dict) else None
        if not edition:
            raise S.SpecError("no --index and no windows.xbox.source.edition in the spec")
        index = pick_index(wim, edition)
    roots = (((S.load_spec(spec_path).get("spec") or {}).get("gate") or {}).get("staging_roots"))
    own = workdir is None
    wd = workdir or tempfile.mkdtemp(prefix="mios-xbox-conform.")
    try:
        obs = collect_image(wim, index, items, wd, answer, ["/" + r.strip("/") for r in roots] if roots else None)
        results, findings = evaluate(items, obs, "image", policy)
    finally:
        if own:
            shutil.rmtree(wd, ignore_errors=True)
    return _report("image", results, findings, report, S.spec_credential_warnings(items))


def _default_root() -> str:
    """The MiOS tree holding component sources: this file's tree (usr/libexec/mios/win/xbox -> /)."""
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", ".."))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="conform.py", description="MiOS-Xbox offline conformance gate")
    ap.add_argument("--root", default=_default_root(), help="MiOS tree with component sources / Start assets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rendered", help="check rendered pipeline inputs")
    r.add_argument("--spec", required=True)
    r.add_argument("--input", action="append", required=True, help="dir or file (repeatable)")
    r.add_argument("--report")
    i = sub.add_parser("image", help="offline inspection of install.wim/.esd")
    i.add_argument("--spec", required=True)
    i.add_argument("--wim", required=True)
    i.add_argument("--index", type=int, default=0, help="image index (default: the one whose Edition ID is spec source.edition)")
    i.add_argument("--answer", help="the answer file shipped beside the image (ISO root)")
    i.add_argument("--workdir", help="extraction dir on disk (default: a private temp dir, removed after)")
    i.add_argument("--report")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "rendered":
            return run_rendered(a.spec, a.input, a.report, a.root)
        return run_image(a.spec, a.wim, a.index, a.answer, a.workdir, a.report, a.root)
    except S.SpecError as e:
        print(f"conform: spec error: {e}", file=sys.stderr)
        return 2
    except ToolError as e:
        print(f"conform: tool error: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"conform: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
