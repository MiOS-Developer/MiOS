#!/usr/bin/env python3
# AI-hint: Looking Glass IVSHMEM setup, the SSOT [vm.looking_glass] domain profile (SPICE on a fixed port, one spicevmc channel, SPICE audio, SMT topology + topoext, localtime RTC, ivshmem), the client config/args (guest resolution follows the client window via the IDD), IVSHMEM sizing for the largest host mode, the /dev/shm SELinux label, the one-action VM + client launcher, and guest provisioning of the upstream Looking Glass IDD built from the same commit as the client.
# AI-related: tests/test-looking-glass.py, usr/share/mios/mios.toml [vm.looking_glass] [vm.win11], usr/libexec/mios/Xbox-Final-NoAutoSelect.xml, usr/libexec/mios/Xbox-AutoEnroll.xml, usr/libexec/mios/virt-apply.sh, usr/libexec/mios/xbox-repair.sh, usr/lib/tmpfiles.d/50-mios.conf, automation/38-selinux.sh, automation/69-bake-lookingglass-client.sh, usr/share/mios/windows/mios-looking-glass-idd.ps1, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""
MiOS Looking Glass shared memory, domain profile and launcher.

Every libvirt domain that Looking Glass displays gets the same profile, projected from
the SSOT [vm.looking_glass] table (usr/share/mios/mios.toml):

* SPICE graphics on a fixed listen address/port (the client connects to the same
  spice_listen/spice_port, so the two cannot drift), plus a localhost VNC console on the
  emulated display for virt-manager / Cockpit when vnc_console is set;
* exactly one spicevmc virtio channel (com.redhat.spice.0) for clipboard;
* audio over SPICE (an <audio type="spice"> backend, an ich9 sound card);
* host-passthrough CPU with 1 socket x (vcpus / threads_per_core) cores x
  threads_per_core threads and the topoext feature (AMD SMT is otherwise invisible);
* <clock offset="localtime"> (Windows keeps the RTC in local time), timers untouched;
* the ivshmem-plain <shmem> framebuffer of shm_size_mb.

The same code renders the shipped templates (virt-apply.sh, xbox-repair.sh) and patches an
installed domain (--apply-domain dumps the inactive XML, patches it, validates it with
virt-xml-validate and redefines it; a running domain is left untouched).

Guest side: the client is built from the upstream default-branch HEAD at bake time
(automation/69-bake-lookingglass-client.sh records the commit in /usr/share/mios/looking-glass/build.json).
--provision-guest finds the signed upstream IDD build of that same commit on looking-glass.io and runs
usr/share/mios/windows/mios-looking-glass-idd.ps1 in the Windows guest over WinRM (SHA-256 and
Authenticode checked there; the legacy host service is removed). With win:setGuestRes the client then
asks the IDD for a guest mode equal to its window in physical pixels, so the guest renders 1:1.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

# Defaults mirror usr/share/mios/mios.toml [vm.looking_glass]; the SSOT wins whenever it loads.
LG_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "domains": ["mios-xbox-current", "Xbox"],
    "connect": "qemu:///system",
    "threads_per_core": 2,
    "topoext": True,
    "clock_offset": "utc",
    "spice_listen": "127.0.0.1",
    # Fixed (autoport=no) SPICE port, kept clear of the low end of libvirt's autoport range (5900+, first free
    # wins): an autoport domain or host VNC started first would otherwise take 5900 and this domain fails to start.
    "spice_port": 5930,
    "vnc_console": True,            # native VNC console on the emulated display (virt-manager / Cockpit)
    "vnc_listen": "127.0.0.1",
    "vnc_passwd_file": "/etc/mios/vm/vnc.passwd",  # root-only VNC console password (generated 0600 on first use); "" = none
    "idd_exclusive": False,         # IDD as the only guest display would hide that console
    "mirror_displays": True,        # Duplicate topology at every sign-in (IDD, VNC/SPICE console, dGPU output)
    "spice_audio": True,
    "spice_channel": "com.redhat.spice.0",
    "sound_model": "ich9",
    "shm_name": "looking-glass",
    "shm_path": "/dev/shm/looking-glass",
    "shm_size_mb": 128,
    "shm_selinux_type": "svirt_tmpfs_t",
    "client": "/usr/bin/looking-glass-client",
    "client_ini": "/etc/looking-glass-client.ini",
    "client_auto_resize": False,
    "client_set_guest_res": True,   # win:setGuestRes -- the guest mode follows the client window (IDD)
    "client_keep_aspect": True,     # win:keepAspect
    "client_egl_scale": 0,          # egl:scale -- 0 auto: 1:1 when the guest matches the window, linear otherwise
    "start_timeout_s": 120,
    "build_record": "/usr/share/mios/looking-glass/build.json",
    "idd_downloads": "https://looking-glass.io/downloads",
    "idd_signer": "CN=HostFission",
    "guest_script": "/usr/share/mios/windows/mios-looking-glass-idd.ps1",
    "winrm_port": 5985,
    "winrm_answerfile": "/var/lib/mios-build/artifacts/mios-xbox/media/autounattend.xml",
    "winrm_python": "/var/lib/mios-build/venv/bin/python",
}


def load_ssot() -> Dict[str, Any]:
    """The layered SSOT (vendor < host < user) via the shared resolver, else the vendor file."""
    for libdir in ("/usr/lib/mios", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios")):
        if os.path.isfile(os.path.join(libdir, "mios_toml.py")):
            sys.path.insert(0, os.path.normpath(libdir))
            try:
                import mios_toml  # type: ignore

                data = mios_toml.load_merged()
                if isinstance(data, dict) and data:
                    return data
            except Exception:  # noqa: BLE001 -- fall back to the plain vendor file
                pass
            finally:
                sys.path.pop(0)
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        return {}
    path = os.environ.get("MIOS_VENDOR_TOML") or os.environ.get("MIOS_TOML") or "/usr/share/mios/mios.toml"
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except OSError:
        return {}


def lg_config(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """[vm.looking_glass] over LG_DEFAULTS."""
    if data is None:
        data = load_ssot()
    cfg = dict(LG_DEFAULTS)
    sect = (data.get("vm") or {}).get("looking_glass") or {}
    cfg.update({k: v for k, v in sect.items()})
    return cfg


def template_vcpus(data: Optional[Dict[str, Any]] = None) -> int:
    """[vm.win11].vcpus: the vCPU count of the shipped Xbox templates."""
    if data is None:
        data = load_ssot()
    return int(((data.get("vm") or {}).get("win11") or {}).get("vcpus", 12))


def topology(vcpus: int, threads: int) -> Dict[str, int]:
    if threads < 1 or vcpus < threads or vcpus % threads:
        raise ValueError(f"{vcpus} vCPUs cannot be split into cores of {threads} threads")
    return {"sockets": 1, "dies": 1, "cores": vcpus // threads, "threads": threads}


# ---------------------------------------------------------------------------
# Domain XML profile
# ---------------------------------------------------------------------------

def _register_namespaces(xml_text: str) -> None:
    for _event, (prefix, uri) in ET.iterparse(io.StringIO(xml_text), events=("start-ns",)):
        if prefix:
            ET.register_namespace(prefix, uri)


def _parse(xml_text: str) -> ET.Element:
    _register_namespaces(xml_text)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.fromstring(xml_text, parser=parser)


def _child(parent: ET.Element, tag: str, index: Optional[int] = None) -> ET.Element:
    el = parent.find(tag)
    if el is None:
        el = ET.Element(tag)
        if index is None:
            parent.append(el)
        else:
            parent.insert(index, el)
    return el


def _index_of(parent: ET.Element, child: ET.Element) -> int:
    return list(parent).index(child)


def _after(root: ET.Element, *tags: str) -> int:
    """Index just after the last present element of tags (domain element order)."""
    idx = 0
    for i, el in enumerate(list(root)):
        if el.tag in tags:
            idx = i + 1
    return idx


def _graphics_from(old: Optional[ET.Element], gtype: str, fixed: Dict[str, str], listen: str) -> ET.Element:
    """A <graphics type=gtype> that keeps old's attributes/children except the SSOT-owned ones in `fixed`
    and the <listen> children (rebuilt as one address listen)."""
    attrs: Dict[str, str] = {"type": gtype}
    if old is not None:
        # socket= is a listen of its own and would contradict the SSOT address
        attrs.update({k: v for k, v in old.attrib.items() if k not in ("type", "socket")})
    attrs.update(fixed)
    gfx = ET.Element("graphics", attrs)
    ET.SubElement(gfx, "listen", {"type": "address", "address": listen})
    if old is not None:
        for c in old:
            if c.tag != "listen":
                gfx.append(copy.deepcopy(c))
    return gfx


def apply_profile(xml_text: str, cfg: Dict[str, Any], vcpus: Optional[int] = None,
                  vnc_passwd: Optional[str] = None) -> str:
    """Return xml_text with the Looking Glass profile applied (idempotent).

    vnc_passwd: None keeps whatever passwd the VNC console already has; "" removes it; a string sets it
    (resolved by vnc_password() from the root-only [vm.looking_glass].vnc_passwd_file)."""
    root = _parse(xml_text)
    if root.tag != "domain":
        raise ValueError("not a libvirt <domain> document")
    threads = int(cfg["threads_per_core"])

    # -- vCPUs and topology -------------------------------------------------
    vcpu_el = root.find("vcpu")
    if vcpus is not None:
        if vcpu_el is None:
            vcpu_el = _child(root, "vcpu", _after(root, "name", "uuid", "title", "description", "metadata", "memory", "currentMemory"))
            vcpu_el.set("placement", "static")
        vcpu_el.text = str(int(vcpus))
    if vcpu_el is None or not (vcpu_el.text or "").strip().isdigit():
        raise ValueError("domain has no <vcpu> count and none was given")
    topo = topology(int(vcpu_el.text.strip()), threads)

    cpu = root.find("cpu")
    if cpu is None:
        cpu = ET.Element("cpu")
        root.insert(_after(root, "name", "uuid", "title", "description", "metadata", "memory", "currentMemory",
                           "vcpu", "vcpus", "iothreads", "cputune", "numatune", "resource", "sysinfo", "os",
                           "features"), cpu)
    cpu.set("mode", "host-passthrough")
    cpu.attrib.pop("match", None)
    for el in cpu.findall("model"):
        cpu.remove(el)
    topo_el = cpu.find("topology")
    if topo_el is None:
        topo_el = ET.Element("topology")
        cpu.insert(0, topo_el)
    clusters = topo_el.get("clusters")
    topo_el.attrib.clear()
    topo_el.set("sockets", str(topo["sockets"]))
    topo_el.set("dies", str(topo["dies"]))
    if clusters is not None:
        topo_el.set("clusters", "1")
    topo_el.set("cores", str(topo["cores"]))
    topo_el.set("threads", str(topo["threads"]))
    if cfg.get("topoext", True):
        feats = [f for f in cpu.findall("feature") if f.get("name") == "topoext"]
        for extra in feats[1:]:
            cpu.remove(extra)
        feat = feats[0] if feats else None
        if feat is None:
            feat = ET.Element("feature")
            cpu.insert(_index_of(cpu, topo_el) + 1 + len(cpu.findall("cache")), feat)
        feat.attrib.clear()
        feat.set("policy", "require")
        feat.set("name", "topoext")

    # -- clock (timers are kept as they are) ---------------------------------
    clock = root.find("clock")
    if clock is None:
        clock = ET.Element("clock")
        root.insert(_index_of(root, cpu) + 1, clock)
    offset = str(cfg["clock_offset"])
    for attr in ("adjustment", "basis", "timezone"):
        clock.attrib.pop(attr, None)
    clock.set("offset", offset)

    devices = root.find("devices")
    if devices is None:
        devices = ET.SubElement(root, "devices")

    # -- graphics: one SPICE server on the fixed port --------------------------
    # Only the SSOT-owned fields are rewritten (type/port/autoport/listen and the <listen> child); every other
    # attribute (passwd, keymap, defaultMode, ...) and child (<image>, <streaming>, <gl>, ...) of the existing
    # SPICE / VNC server is carried over, so a re-apply never silently drops operator settings.
    listen = str(cfg["spice_listen"])
    port = str(int(cfg["spice_port"]))
    graphics = devices.findall("graphics")
    old_spice = next((g for g in graphics if g.get("type") == "spice"), None)
    old_vnc = next((g for g in graphics if g.get("type") == "vnc"), None)
    pos = len(list(devices))
    if graphics:
        pos = _index_of(devices, graphics[0])
        for g in graphics:
            devices.remove(g)
    gfx = _graphics_from(old_spice, "spice", {"port": port, "autoport": "no", "listen": listen}, listen)
    devices.insert(pos, gfx)

    # -- exactly one spicevmc channel (clipboard / agent) -----------------------
    name = str(cfg["spice_channel"])
    channels = [c for c in devices.findall("channel")
                if c.get("type") == "spicevmc" or (c.find("target") is not None and c.find("target").get("name") == name)]
    kept = next((c for c in channels if c.get("type") == "spicevmc"), None)
    for c in channels:
        if c is not kept:
            devices.remove(c)
    if kept is None:
        kept = ET.Element("channel", {"type": "spicevmc"})
        devices.insert(_index_of(devices, gfx), kept)
    target = _child(kept, "target", 0)
    target.attrib.clear()
    target.set("type", "virtio")
    target.set("name", name)

    # -- audio over SPICE ---------------------------------------------------------
    sounds = devices.findall("sound")
    if not sounds:
        snd = ET.Element("sound", {"model": str(cfg["sound_model"])})
        devices.insert(_index_of(devices, gfx) + 1, snd)
        sounds = [snd]
    audios = devices.findall("audio")
    audio_id = audios[0].get("id", "1") if audios else "1"
    for a in audios[1:]:
        devices.remove(a)
    if audios:
        audio = audios[0]
        audio.attrib.clear()
        for c in list(audio):
            audio.remove(c)
    else:
        audio = ET.Element("audio")
        devices.insert(_index_of(devices, sounds[-1]) + 1, audio)
    audio.set("id", audio_id)
    audio.set("type", "spice" if cfg.get("spice_audio", True) else "none")
    for snd in sounds:
        ref = snd.find("audio")
        if ref is not None:
            ref.attrib.clear()
            ref.set("id", audio_id)

    # -- IVSHMEM framebuffer --------------------------------------------------------
    shm_name = str(cfg["shm_name"])
    shmem = next((s for s in devices.findall("shmem") if s.get("name") == shm_name), None)
    if shmem is None:
        shmem = ET.SubElement(devices, "shmem", {"name": shm_name})
    model = _child(shmem, "model", 0)
    model.attrib.clear()
    model.set("type", "ivshmem-plain")
    size = _child(shmem, "size", 1)
    size.attrib.clear()
    size.set("unit", "M")
    size.text = str(int(cfg["shm_size_mb"]))

    # Native VNC console on the emulated display (virt-manager, Cockpit): the guest always has a reachable
    # graphical console for sign-in/recovery even when the Looking Glass source is down. Placed last,
    # directly after the SPICE server, so repeated application is stable.
    if cfg.get("vnc_console", True):
        vlisten = str(cfg.get("vnc_listen", "127.0.0.1"))
        fixed: Dict[str, str] = {"listen": vlisten}
        vport = old_vnc.get("port") if old_vnc is not None else None
        if old_vnc is None or old_vnc.get("autoport") == "yes" or not vport or vport in ("-1", port):
            # autoport unless the operator pinned a VNC port that does not collide with the SPICE port
            fixed.update({"port": "-1", "autoport": "yes"})
        vnc = _graphics_from(old_vnc, "vnc", fixed, vlisten)
        if vnc_passwd is not None:
            if vnc_passwd:
                vnc.set("passwd", vnc_passwd)
            else:
                vnc.attrib.pop("passwd", None)
        spice_el = next(g for g in devices.findall("graphics") if g.get("type") == "spice")
        devices.insert(_index_of(devices, spice_el) + 1, vnc)

    _indent(root)
    out = ET.tostring(root, encoding="unicode")
    if xml_text.lstrip().startswith("<?xml"):
        out = '<?xml version="1.0" encoding="UTF-8"?>\n' + out
    return out + "\n"


def _indent(root: ET.Element) -> None:
    if hasattr(ET, "indent"):
        for el in root.iter():
            if el.text is not None and not el.text.strip():
                el.text = None
            if el.tail is not None and not el.tail.strip():
                el.tail = None
        ET.indent(root, space="  ")


def profile_problems(xml_text: str, cfg: Dict[str, Any], vcpus: Optional[int] = None,
                     vnc_passwd: Optional[str] = None) -> List[str]:
    """What keeps xml_text from matching the profile (empty when it conforms).
    vnc_passwd (when not None) is the console password the VNC server must carry ("" = none)."""
    root = _parse(xml_text)
    bad: List[str] = []
    vcpu_el = root.find("vcpu")
    have = int(vcpu_el.text.strip()) if vcpu_el is not None and (vcpu_el.text or "").strip().isdigit() else None
    if vcpus is not None and have != vcpus:
        bad.append(f"<vcpu> is {have}, SSOT says {vcpus}")
    try:
        topo = topology(have or 0, int(cfg["threads_per_core"]))
    except ValueError as e:
        bad.append(str(e))
        topo = None
    cpu = root.find("cpu")
    if cpu is None or cpu.get("mode") != "host-passthrough":
        bad.append("cpu mode is not host-passthrough")
    t = cpu.find("topology") if cpu is not None else None
    if topo and (t is None or any(t.get(k) != str(v) for k, v in topo.items())):
        bad.append(f"cpu topology is not {topo}")
    if cfg.get("topoext", True) and (cpu is None or not any(
            f.get("name") == "topoext" and f.get("policy") == "require" for f in cpu.findall("feature"))):
        bad.append("cpu lacks <feature policy='require' name='topoext'/>")
    clock = root.find("clock")
    if clock is None or clock.get("offset") != str(cfg["clock_offset"]):
        bad.append(f"clock offset is not {cfg['clock_offset']}")
    dev = root.find("devices")
    gfx = dev.findall("graphics") if dev is not None else []
    spices = [g for g in gfx if g.get("type") == "spice"]
    vncs = [g for g in gfx if g.get("type") == "vnc"]
    if len(spices) != 1:
        bad.append("graphics does not have exactly one SPICE server")
    elif (spices[0].get("port") != str(int(cfg["spice_port"])) or spices[0].get("autoport") != "no"
          or spices[0].get("listen") != str(cfg["spice_listen"])):
        bad.append(f"SPICE is not on {cfg['spice_listen']}:{cfg['spice_port']} with autoport=no")
    want_vnc = bool(cfg.get("vnc_console", True))
    if want_vnc and (len(vncs) != 1 or vncs[0].get("listen") != str(cfg.get("vnc_listen", "127.0.0.1"))):
        bad.append(f"expected one VNC console on {cfg.get('vnc_listen', '127.0.0.1')}")
    if not want_vnc and vncs:
        bad.append("VNC console present but [vm.looking_glass] vnc_console is false")
    if want_vnc and len(vncs) == 1:
        if vncs[0].get("autoport") != "yes" and vncs[0].get("port") == str(int(cfg["spice_port"])):
            bad.append("VNC console is pinned to the SPICE port")
        if vnc_passwd is not None and (vncs[0].get("passwd") or "") != vnc_passwd:
            bad.append("VNC console password does not match [vm.looking_glass].vnc_passwd_file")
    if len(gfx) != len(spices) + len(vncs):
        bad.append("unexpected graphics types besides SPICE/VNC")
    chans = [c for c in (dev.findall("channel") if dev is not None else []) if c.get("type") == "spicevmc"]
    if len(chans) != 1 or chans[0].find("target") is None or chans[0].find("target").get("name") != cfg["spice_channel"]:
        bad.append(f"expected exactly one spicevmc channel {cfg['spice_channel']}, found {len(chans)}")
    want_audio = "spice" if cfg.get("spice_audio", True) else "none"
    audios = dev.findall("audio") if dev is not None else []
    if len(audios) != 1 or audios[0].get("type") != want_audio:
        bad.append(f"audio backend is not a single type={want_audio}")
    if dev is None or not dev.findall("sound"):
        bad.append("no sound device")
    shm = [s for s in (dev.findall("shmem") if dev is not None else []) if s.get("name") == cfg["shm_name"]]
    if len(shm) != 1:
        bad.append(f"no <shmem name='{cfg['shm_name']}'>")
    else:
        m, s = shm[0].find("model"), shm[0].find("size")
        if m is None or m.get("type") != "ivshmem-plain" or s is None or (s.text or "").strip() != str(int(cfg["shm_size_mb"])) or s.get("unit") != "M":
            bad.append(f"shmem is not ivshmem-plain {cfg['shm_size_mb']}M")
    return bad


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _yn(v: Any) -> str:
    return "yes" if v else "no"


def client_options(cfg: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    set_guest_res = bool(cfg.get("client_set_guest_res", True))
    # The client refuses to start with both; a guest that follows the window wins.
    auto_resize = bool(cfg.get("client_auto_resize", False)) and not set_guest_res
    return {
        "app": {"shmFile": str(cfg["shm_path"])},
        "win": {
            "autoResize": _yn(auto_resize),
            "setGuestRes": _yn(set_guest_res),
            "keepAspect": _yn(cfg.get("client_keep_aspect", True)),
        },
        "egl": {"scale": str(int(cfg.get("client_egl_scale", 0)))},
        "spice": {
            "enable": "yes",
            "host": str(cfg["spice_listen"]),
            "port": str(int(cfg["spice_port"])),
            "audio": _yn(cfg.get("spice_audio", True)),
        },
    }


def client_args(cfg: Dict[str, Any]) -> List[str]:
    return [f"{sect}:{k}={v}" for sect, opts in client_options(cfg).items() for k, v in opts.items()]


def client_ini(cfg: Dict[str, Any]) -> str:
    lines = ["; Generated from mios.toml [vm.looking_glass] by /usr/libexec/mios/vfio/setup-looking-glass.py --write-client-ini.",
             "; Edit the SSOT, not this file."]
    for sect, opts in client_options(cfg).items():
        lines.append("")
        lines.append(f"[{sect}]")
        lines.extend(f"{k}={v}" for k, v in opts.items())
    return "\n".join(lines) + "\n"


def _fsync_dir(d: str) -> None:
    try:
        dfd = os.open(d, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def write_atomic(path: str, text: str, mode: int = 0o644) -> None:
    """Temp file in the same directory, flush + fsync, chmod, rename over path, fsync the directory: a reset
    mid-write leaves either the old file or the new one, never a truncated one."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".lg-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fchmod(fh.fileno(), mode)
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _fsync_dir(d)


VNC_PASSWD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
VNC_PASSWD_MAX = 8  # RFB (VNC) authentication is DES-keyed: QEMU uses only the first 8 characters


def _trusted_owner(st: os.stat_result) -> bool:
    return st.st_uid in (0, os.geteuid())


def vnc_password(cfg: Dict[str, Any], create: bool = True) -> Optional[str]:
    """The VNC console password from the root-only [vm.looking_glass].vnc_passwd_file.

    Returns "" when the console or the password is switched off in the SSOT (the passwd is removed), None
    when the file cannot be read here (e.g. an unprivileged render: the domain's current passwd is kept),
    else the password. A missing file is generated (0600, 8 random characters) when `create` is set; a file
    readable by group/others is tightened to 0600; a symlink, a non-regular file or a foreign owner is refused."""
    import secrets
    import stat

    if not cfg.get("vnc_console", True):
        return ""
    path = str(cfg.get("vnc_passwd_file") or "")
    if not path:
        return ""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if not create:
            return None
        d = os.path.dirname(path) or "."
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
            dst = os.stat(d)
            if not _trusted_owner(dst) or dst.st_mode & 0o022:
                raise SystemExit(f"[looking-glass] {d} is not owned by root/us or is group/other-writable; "
                                 "refusing to keep the VNC password there")
            pw = "".join(secrets.choice(VNC_PASSWD_ALPHABET) for _ in range(VNC_PASSWD_MAX))
            # Written whole to a 0600 temp file and fsynced, then hard-linked into place (fails if another run won the
            # race): a reset mid-write never leaves an empty password file that would refuse every later render.
            wfd, tmp = tempfile.mkstemp(dir=d, prefix=".vnc-")
        except PermissionError:
            return None
        try:
            with os.fdopen(wfd, "w", encoding="ascii") as fh:
                fh.write(pw + "\n")
                fh.flush()
                os.fchmod(fh.fileno(), 0o600)
                os.fsync(fh.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                return vnc_password(cfg, create=False)
        finally:
            os.unlink(tmp)
        _fsync_dir(d)
        print(f"[looking-glass] generated the VNC console password in {path} (0600)", file=sys.stderr)
        return pw
    except PermissionError:
        return None
    except OSError as e:  # ELOOP: a symlink
        raise SystemExit(f"[looking-glass] refusing VNC password file {path}: {e}")
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or not _trusted_owner(st):
            raise SystemExit(f"[looking-glass] {path} is not a regular file owned by root; refusing it")
        if st.st_mode & 0o077:
            os.fchmod(fh.fileno(), 0o600)
        pw = fh.read().strip()
    if not pw or len(pw) > VNC_PASSWD_MAX or not all(33 <= ord(ch) < 127 for ch in pw):
        raise SystemExit(f"[looking-glass] {path} must hold 1-{VNC_PASSWD_MAX} printable ASCII characters "
                         "(VNC authentication ignores anything longer)")
    return pw


# ---------------------------------------------------------------------------
# IVSHMEM size for the largest mode the guest may be asked for
# ---------------------------------------------------------------------------

def idd_required_mb(width: int, height: int) -> int:
    """IVSHMEM MiB the IDD needs for width x height (upstream doc/install_libvirt.rst 'Determining memory'):
    ceil(width*4/256)*256 * height * 3 + 4 MiB, rounded up to a power of two."""
    pitch = -(-int(width) * 4 // 256) * 256
    need = pitch * int(height) * 3 + 4 * 1024 * 1024
    mb = -(-need // (1024 * 1024))
    p = 1
    while p < mb:
        p *= 2
    return p


def host_modes() -> List[Dict[str, Any]]:
    """Physical-pixel size of every compositor output (Hyprland reports the mode; logical = mode / scale).
    The largest client window, fullscreen, is one output's mode, which is what setGuestRes requests."""
    if not shutil.which("hyprctl"):
        return []
    try:
        r = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=5)
        return [{"name": m.get("name"), "width": int(m["width"]), "height": int(m["height"]), "scale": m.get("scale", 1)}
                for m in json.loads(r.stdout or "[]")]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return []


def shm_check(cfg: Dict[str, Any], modes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    modes = host_modes() if modes is None else modes
    need = max((idd_required_mb(m["width"], m["height"]) for m in modes), default=0)
    have = int(cfg["shm_size_mb"])
    return {"shm_size_mb": have, "required_mb": need, "modes": modes, "ok": have >= need}


# ---------------------------------------------------------------------------
# Guest: the upstream Looking Glass IDD of the client's commit
# ---------------------------------------------------------------------------

def client_commit(cfg: Dict[str, Any]) -> str:
    """The upstream commit the installed client was built from (build output of 69-bake-lookingglass-client.sh),
    else the hash in `looking-glass-client --version` (e.g. B7-829-gcdba29f2f6)."""
    try:
        with open(str(cfg["build_record"]), encoding="utf-8") as fh:
            sha = json.load(fh).get("commit", "")
        if re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            return sha
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run([str(cfg["client"]), "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    m = re.search(r"Looking Glass \(B\d+[^)]*-g([0-9a-f]{7,})\+?\)", out)
    if not m:
        raise SystemExit("[looking-glass] the client's upstream commit is unknown (no build record, release build); "
                         "rebuild it with automation/69-bake-lookingglass-client.sh")
    return m.group(1)


def _http(url: str, follow: bool = True) -> Any:
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a: Any, **k: Any) -> None:  # noqa: D401
            return None

    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "MiOS setup-looking-glass"})
    try:
        return opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:  # a 3xx without follow lands here
        if not follow and 300 <= e.code < 400:
            return e
        raise


def idd_plan(cfg: Dict[str, Any], commit: Optional[str] = None) -> Dict[str, str]:
    """The looking-glass.io IDD build of `commit`: artifact id, download URL and the SHA-256 the site publishes
    (the artifact redirect carries it). Upstream's CI publishes one per default-branch commit."""
    commit = commit or client_commit(cfg)
    base = str(cfg["idd_downloads"])
    page = _http(base).read().decode("utf-8", "replace")
    ids = sorted(set(re.findall(r"/artifact/(B\d+[\w.]*-\d+-([0-9a-f]{7,40}))/idd", page)))
    match = [aid for aid, h in ids if commit.startswith(h) or h.startswith(commit)]
    if not match:
        raise SystemExit(f"[looking-glass] no signed upstream IDD build for commit {commit} on {base} (yet); "
                         "the client and guest must come from the same commit")
    aid = match[0]
    from urllib.parse import urljoin
    url = urljoin(base, f"/artifact/{aid}/idd")
    loc = _http(url, follow=False).headers.get("Location", "")
    m = re.search(r"/artifact/file/idd/([0-9a-f]{64})/", loc)
    if not m:
        raise SystemExit(f"[looking-glass] {url} did not publish a SHA-256 (redirect: {loc!r})")
    return {"commit": commit, "version": aid, "url": url, "sha256": m.group(1), "signer": str(cfg["idd_signer"])}


def _ps_quote(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def guest_script(cfg: Dict[str, Any]) -> str:
    path = str(cfg["guest_script"])
    if not os.path.isfile(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "share", "mios", "windows",
                            "mios-looking-glass-idd.ps1")
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def protect_dir_function(script: str) -> str:
    """The Protect-MiOSDir function of mios-looking-glass-idd.ps1 (between its BEGIN/END markers), comments dropped."""
    m = re.search(r"^# BEGIN Protect-MiOSDir.*?$(.*?)^# END Protect-MiOSDir", script, re.S | re.M)
    if not m:
        raise SystemExit("[looking-glass] guest script lacks the Protect-MiOSDir block")
    lines = [ln.split("   # ")[0].rstrip() for ln in m.group(1).splitlines()]
    return "\n".join(ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#"))


def guest_commands(cfg: Dict[str, Any], plan: Dict[str, str], chunk: int = 2000) -> List[str]:
    """PowerShell commands that stage mios-looking-glass-idd.ps1 in the guest and run it with the plan bound.
    WinRM runs each through a cmd.exe line (8191 chars, UTF-16 base64), so the script travels in chunks."""
    import base64
    script = guest_script(cfg)
    blob = base64.b64encode(script.encode("utf-8")).decode("ascii")
    stage = "$env:ProgramData + '\\MiOS\\looking-glass\\mios-looking-glass-idd.b64'"
    # The staging directory is created (or taken back) admin-only BEFORE anything is written: %ProgramData% lets a
    # standard user pre-create MiOS\looking-glass and swap the .b64 between the chunks and the elevated run.
    cmds = [f"{protect_dir_function(script)}\n"
            "$ErrorActionPreference = 'Stop'; "
            "Protect-MiOSDir (Join-Path $env:ProgramData 'MiOS') $true $false; "
            f"$f = {stage}; Protect-MiOSDir (Split-Path $f); "
            "Set-Content -NoNewline -Encoding ascii -Path $f -Value ''"]
    for i in range(0, len(blob), chunk):
        cmds.append(f"Add-Content -NoNewline -Encoding ascii -Path ({stage}) -Value '{blob[i:i + chunk]}'")
    cmds.append(
        f"$s = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([IO.File]::ReadAllText({stage}))); "
        f"& ([scriptblock]::Create($s)) -Url {_ps_quote(plan['url'])} -Sha256 {_ps_quote(plan['sha256'])} "
        f"-Version {_ps_quote(plan['version'])} -Signer {_ps_quote(plan['signer'])} "
        f"-ExclusiveMonitor {1 if cfg.get('idd_exclusive') else 0} "
        f"-MirrorDisplays {1 if cfg.get('mirror_displays', True) else 0}")
    return cmds


def _winrm_credentials(cfg: Dict[str, Any]) -> tuple:
    user, password = os.environ.get("MIOS_WINRM_USER"), os.environ.get("MIOS_WINRM_PASSWORD")
    if user and password:
        return user, password
    path = os.environ.get("MIOS_XBOX_ANSWERFILE") or str(cfg["winrm_answerfile"])
    d = os.path.dirname(path)
    if os.path.isdir(d) and not os.path.isfile(path):
        path = next((os.path.join(d, n) for n in os.listdir(d) if n.lower() == os.path.basename(path).lower()), path)
    import base64
    ns = {"u": "urn:schemas-microsoft-com:unattend"}
    acct = ET.parse(path).find(".//u:LocalAccount", ns)
    if acct is None:
        raise SystemExit(f"[looking-glass] no LocalAccount in {path}; set MIOS_WINRM_USER / MIOS_WINRM_PASSWORD")
    user = acct.findtext("u:Name", namespaces=ns)
    password = acct.findtext("u:Password/u:Value", namespaces=ns) or ""
    if (acct.findtext("u:Password/u:PlainText", namespaces=ns) or "").lower() == "false":
        password = base64.b64decode(password).decode("utf-16le")
        if not password.endswith("Password"):
            raise SystemExit("[looking-glass] unsupported answer-file password encoding")
        password = password[: -len("Password")]
    return user, password


def _winrm_endpoint(cfg: Dict[str, Any], dom: str) -> str:
    if os.environ.get("MIOS_WINRM_ENDPOINT"):
        return os.environ["MIOS_WINRM_ENDPOINT"]
    for source in ("lease", "arp", "agent"):
        r = _virsh(cfg, "domifaddr", dom, "--source", source, check=False)
        m = re.search(r"ipv4\s+(\d+\.\d+\.\d+\.\d+)", r.stdout)
        if m:
            return f"http://{m.group(1)}:{int(cfg['winrm_port'])}/wsman"
    raise SystemExit(f"[looking-glass] no IPv4 address for {dom}; set MIOS_WINRM_ENDPOINT")


def provision_guest(cfg: Dict[str, Any], dom: str, dry_run: bool = False) -> int:
    """Install the IDD of the client's commit in the guest (idempotent: skipped when that version is installed)."""
    plan = idd_plan(cfg)
    print(f"[looking-glass] client commit {plan['commit']} -> IDD {plan['version']} {plan['url']} sha256 {plan['sha256']}")
    commands = guest_commands(cfg, plan)
    if dry_run:
        print(json.dumps(plan))
        return 0
    try:
        import winrm  # type: ignore
    except ImportError:
        py = os.environ.get("MIOS_WINRM_PYTHON") or str(cfg["winrm_python"])
        if os.access(py, os.X_OK) and not os.environ.get("MIOS_LG_WINRM_REEXEC"):
            os.environ["MIOS_LG_WINRM_REEXEC"] = "1"  # a venv shares the real interpreter; this stops a loop
            os.execv(py, [py, "-I", os.path.abspath(__file__), *sys.argv[1:]])
        raise SystemExit("[looking-glass] pywinrm is not available (set MIOS_WINRM_PYTHON to an interpreter that has it)")
    if domain_state(cfg, dom) != "running":
        raise SystemExit(f"[looking-glass] {dom} is not running")
    endpoint = _winrm_endpoint(cfg, dom)
    user, password = _winrm_credentials(cfg)
    session = winrm.Session(endpoint, auth=(user, password), transport="ntlm",
                            operation_timeout_sec=60, read_timeout_sec=90)
    for command in commands:
        result = session.run_ps(command)
        sys.stdout.write(result.std_out.decode(errors="replace"))
        if result.status_code:
            sys.stderr.write(result.std_err.decode(errors="replace"))
            return int(result.status_code)
    return 0


# ---------------------------------------------------------------------------
# SELinux label of the /dev/shm framebuffer
# ---------------------------------------------------------------------------

def selinux_fcontext_line(cfg: Dict[str, Any]) -> str:
    """The semanage import line that 38-selinux.sh and --ensure-selinux both apply."""
    return f"fcontext -a -t {cfg['shm_selinux_type']} '{cfg['shm_path']}'"


def ensure_selinux(cfg: Dict[str, Any], dry_run: bool = False) -> List[str]:
    """Add the svirt_tmpfs_t file context for shm_path when the policy lacks it, then restorecon."""
    path, want = str(cfg["shm_path"]), str(cfg["shm_selinux_type"])
    done: List[str] = []
    if not shutil.which("matchpathcon") or not os.path.isdir("/sys/fs/selinux"):
        return ["selinux: not enabled, nothing to do"]
    cur = subprocess.run(["matchpathcon", "-n", path], capture_output=True, text=True).stdout.strip()
    if f":{want}:" not in cur:
        done.append(f"semanage fcontext -a -t {want} {path} (policy default was {cur or 'none'})")
        if not dry_run:
            subprocess.run(["semanage", "fcontext", "-a", "-t", want, path], check=True)
    if os.path.exists(path):
        done.append(f"restorecon {path}")
        if not dry_run:
            subprocess.run(["restorecon", path], check=True)
    return done


# ---------------------------------------------------------------------------
# libvirt
# ---------------------------------------------------------------------------

def _virsh(cfg: Dict[str, Any], *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["virsh", "-c", str(cfg["connect"]), *args], capture_output=True, text=True, check=check)


def pick_domain(cfg: Dict[str, Any], explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    have = set(_virsh(cfg, "list", "--all", "--name").stdout.split())
    for d in cfg["domains"]:
        if d in have:
            return d
    raise SystemExit(f"[looking-glass] none of [vm.looking_glass].domains {cfg['domains']} is defined in {cfg['connect']}")


def domain_state(cfg: Dict[str, Any], dom: str) -> str:
    return _virsh(cfg, "domstate", dom).stdout.strip()


def validate_xml(path: str) -> None:
    if shutil.which("virt-xml-validate"):
        r = subprocess.run(["virt-xml-validate", path, "domain"], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"[looking-glass] {path} fails virt-xml-validate:\n{r.stdout}{r.stderr}")


def apply_domain(cfg: Dict[str, Any], dom: str, out_dir: Optional[str], vcpus: Optional[int], dry_run: bool) -> int:
    state = domain_state(cfg, dom)
    current = _virsh(cfg, "dumpxml", "--inactive", "--security-info", dom).stdout
    pw = vnc_password(cfg, create=not dry_run)
    new = apply_profile(current, cfg, vcpus, pw)
    problems = profile_problems(current, cfg, vcpus, pw)
    d = out_dir or tempfile.mkdtemp(prefix="mios-lg-")
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    before, after = os.path.join(d, f"{dom}.{stamp}.before.xml"), os.path.join(d, f"{dom}.{stamp}.xml")
    write_atomic(before, current, 0o600)
    write_atomic(after, new, 0o600)
    validate_xml(after)
    if not problems:
        print(f"[looking-glass] {dom} already matches [vm.looking_glass]; rendered {after}")
        return 0
    for p in problems:
        print(f"[looking-glass] {dom}: {p}")
    if dry_run:
        print(f"[looking-glass] dry run: rendered {after} (before: {before}), not defined")
        return 0
    if state != "shut off":
        print(f"[looking-glass] {dom} is '{state}': shut it down cleanly first; the live definition was left untouched "
              f"(rendered {after})", file=sys.stderr)
        return 3
    _virsh(cfg, "define", after)
    left = profile_problems(_virsh(cfg, "dumpxml", "--inactive", "--security-info", dom).stdout, cfg, vcpus, pw)
    if left:
        print(f"[looking-glass] {dom} redefined but still differs: {left}", file=sys.stderr)
        return 1
    print(f"[looking-glass] {dom} redefined from {after} (previous definition: {before})")
    return 0


def launch(cfg: Dict[str, Any], dom: str, dry_run: bool = False) -> int:
    """Start the VM when it is not running, wait for its framebuffer, then exec the client."""
    argv = [str(cfg["client"]), *client_args(cfg)]
    if not dry_run and cfg.get("client_set_guest_res", True):
        chk = shm_check(cfg)
        if not chk["ok"]:
            _notify("MiOS-Xbox: IVSHMEM too small", f"[vm.looking_glass].shm_size_mb={chk['shm_size_mb']} but a "
                    f"fullscreen guest mode needs {chk['required_mb']} MiB; larger modes will be refused")
    if dry_run:
        print(json.dumps({"domain": dom, "client": argv}))
        return 0
    if domain_state(cfg, dom) != "running":
        r = _virsh(cfg, "start", dom, check=False)
        if r.returncode:
            sys.stderr.write(r.stderr)
            _notify(f"MiOS-Xbox: could not start {dom}", r.stderr.strip())
            return r.returncode
    deadline = time.monotonic() + float(cfg.get("start_timeout_s", 120))
    while not os.path.exists(str(cfg["shm_path"])) and time.monotonic() < deadline:
        time.sleep(0.5)
    os.execv(argv[0], argv)
    return 0  # pragma: no cover


def _notify(summary: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "MiOS-Xbox", summary, body], check=False)


# ---------------------------------------------------------------------------
# Shared memory / kvmfr checks (mios-vfio-setup.service)
# ---------------------------------------------------------------------------

class LookingGlassManager:
    """Manages Looking Glass shared memory framebuffer allocation and XML generation."""

    def __init__(self, shm_path: str = "/dev/shm/looking-glass", size_mb: int = 64, device_node: str = "/dev/kvmfr0") -> None:
        self.shm_path = shm_path
        self.size_mb = size_mb
        self.device_node = device_node

    def generate_ivshmem_xml(self) -> str:
        """Generates libvirt IVSHMEM domain snippet for Looking Glass."""
        return f"""<shmem name="looking-glass">
  <model type="ivshmem-plain"/>
  <size unit="M">{self.size_mb}</size>
</shmem>"""

    def validate_shm_allocation(self, mock: bool = False) -> bool:
        if mock:
            return True
        if not os.path.exists(self.shm_path):
            return False
        st = os.stat(self.shm_path)
        return (st.st_mode & 0o777) == 0o660

    def validate_kvmfr_device(self, mock: bool = False) -> bool:
        if mock:
            return True
        if not os.path.exists(self.device_node):
            return False
        st = os.stat(self.device_node)
        return (st.st_mode & 0o777) == 0o660

    def verify_all(self, mock: bool = False) -> Dict[str, Any]:
        shm_ok = self.validate_shm_allocation(mock=mock)
        kvmfr_ok = self.validate_kvmfr_device(mock=mock)
        status = "pass" if (shm_ok or kvmfr_ok) else "fail"
        return {
            "status": status,
            "mock": mock,
            "size_mb": self.size_mb,
            "shm_path": self.shm_path,
            "device_node": self.device_node,
            "checks": {
                "shm_allocation": "pass" if shm_ok else "fail",
                "kvmfr_device": "pass" if kvmfr_ok else "fail",
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Looking Glass shared memory, domain profile and launcher.")
    parser.add_argument("--verify", action="store_true", help="Verify IVSHMEM device node and permissions.")
    parser.add_argument("--generate", action="store_true", help="Generate libvirt domain IVSHMEM XML snippet.")
    parser.add_argument("--mock", action="store_true", help="Run verification in mock/synthetic mode.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    parser.add_argument("--size-mb", type=int, default=None, help="Framebuffer size in MB (default: [vm.looking_glass].shm_size_mb).")
    parser.add_argument("--shm-path", type=str, default=None, help="IVSHMEM memory file (default: [vm.looking_glass].shm_path).")
    parser.add_argument("--device-node", type=str, default="/dev/kvmfr0", help="Path to kvmfr character device.")
    parser.add_argument("--render", nargs=2, metavar=("IN", "OUT"), help="Apply the profile to a domain XML file (OUT may equal IN; '-' is stdout).")
    parser.add_argument("--check", metavar="XML", help="Exit 1 and list what keeps XML from matching the profile.")
    parser.add_argument("--vcpus", type=str, default=None, help="vCPU count to set: a number, or 'template' for [vm.win11].vcpus.")
    parser.add_argument("--apply-domain", nargs="?", const="", metavar="DOMAIN", help="Patch and redefine a shut-off libvirt domain (default: first of [vm.looking_glass].domains).")
    parser.add_argument("--out-dir", default=None, help="Where --apply-domain keeps the before/after XML.")
    parser.add_argument("--dry-run", action="store_true", help="Render/report only; change nothing.")
    parser.add_argument("--write-client-ini", nargs="?", const="", metavar="PATH", help="Write the client config (default: [vm.looking_glass].client_ini).")
    parser.add_argument("--client-args", action="store_true", help="Print the looking-glass-client arguments.")
    parser.add_argument("--ensure-selinux", action="store_true", help="Label the /dev/shm framebuffer svirt_tmpfs_t (fcontext + restorecon).")
    parser.add_argument("--launch", nargs="?", const="", metavar="DOMAIN", help="Start the VM if needed and run the Looking Glass client.")
    parser.add_argument("--shm-check", action="store_true", help="Exit 1 when shm_size_mb cannot hold the largest host output mode (IDD formula).")
    parser.add_argument("--idd-plan", action="store_true", help="Print the upstream IDD build (URL, SHA-256) matching the client's commit.")
    parser.add_argument("--provision-guest", nargs="?", const="", metavar="DOMAIN", help="Install that IDD in the running Windows guest over WinRM.")
    args = parser.parse_args()

    data = load_ssot()
    cfg = lg_config(data)
    if args.shm_path:
        cfg["shm_path"] = args.shm_path
    if args.size_mb:
        cfg["shm_size_mb"] = args.size_mb
    vcpus: Optional[int] = None
    if args.vcpus == "template":
        vcpus = template_vcpus(data)
    elif args.vcpus:
        vcpus = int(args.vcpus)

    if args.render:
        src, dst = args.render
        pw = vnc_password(cfg, create=not args.dry_run)
        with open(src, encoding="utf-8") as fh:
            out = apply_profile(fh.read(), cfg, vcpus, pw)
        if dst == "-":
            sys.stdout.write(out)
        else:
            mode = os.stat(src).st_mode & 0o777 if os.path.exists(src) else 0o644
            # a rendered domain that carries the console password is as secret as the password file
            write_atomic(dst, out, 0o600 if 'passwd="' in out else mode)
        return 0
    if args.check:
        with open(args.check, encoding="utf-8") as fh:
            problems = profile_problems(fh.read(), cfg, vcpus)
        for p in problems:
            print(f"[looking-glass] {args.check}: {p}")
        return 1 if problems else 0
    if args.apply_domain is not None:
        return apply_domain(cfg, pick_domain(cfg, args.apply_domain), args.out_dir, vcpus, args.dry_run)
    if args.write_client_ini is not None:
        path = args.write_client_ini or str(cfg["client_ini"])
        if args.dry_run:
            sys.stdout.write(client_ini(cfg))
        else:
            write_atomic(path, client_ini(cfg), 0o644)
            print(f"[looking-glass] wrote {path}")
        if not args.ensure_selinux:
            return 0
    if args.ensure_selinux:
        for line in ensure_selinux(cfg, args.dry_run):
            print(f"[looking-glass] {line}")
        if not args.verify:
            return 0
    if args.client_args:
        print(" ".join(client_args(cfg)))
        return 0
    if args.launch is not None:
        return launch(cfg, pick_domain(cfg, args.launch), args.dry_run)
    if args.shm_check:
        chk = shm_check(cfg)
        print(json.dumps(chk))
        return 0 if chk["ok"] else 1
    if args.idd_plan:
        print(json.dumps(idd_plan(cfg)))
        return 0
    if args.provision_guest is not None:
        return provision_guest(cfg, pick_domain(cfg, args.provision_guest), args.dry_run)

    lg = LookingGlassManager(shm_path=str(cfg["shm_path"]), size_mb=int(cfg["shm_size_mb"]), device_node=args.device_node)

    if args.generate:
        xml = lg.generate_ivshmem_xml()
        if args.json:
            sys.stdout.write(json.dumps({"xml": xml, "size_mb": lg.size_mb}, indent=2) + "\n")
        else:
            sys.stdout.write(xml + "\n")
        return 0

    if args.verify or not sys.argv[1:]:
        results = lg.verify_all(mock=args.mock or os.name == "nt")
        if args.json:
            sys.stdout.write(json.dumps(results, indent=2) + "\n")
        else:
            sys.stdout.write(f"[looking-glass-setup] Status: {results['status'].upper()} (mock={results['mock']})\n")
            sys.stdout.write(f"  - Size: {results['size_mb']} MB\n")
            sys.stdout.write(f"  - SHM ({results['shm_path']}): {results['checks']['shm_allocation']}\n")
            sys.stdout.write(f"  - KVMFR ({results['device_node']}): {results['checks']['kvmfr_device']}\n")
        return 0 if results["status"] == "pass" else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
