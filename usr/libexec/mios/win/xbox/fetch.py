#!/usr/bin/env python3
# AI-hint: MiOS-Xbox fetch step (xb-15, XB-2): downloads every artefact resolve_latest.py recorded in build-manifest.json (fresh UUP Dump file list for the resolved update id, virtio-win RPM -> ISO -> w11 driver tree + guest-tools bundle, latest qemu-ga, WinFsp, WebView2 standalone, Looking Glass host + IDD of the client's commit, UUP Dump's Windows converter at the resolved commit) and verifies each against the manifest's integrity anchor (UUP per-file sha256 + Microsoft FileDigest, published md5/sha256, the virtio-win RPM header's sha256 per member -- ISO, gt MSI, guest-tools bundle, qemu-ga MSI, all unsigned upstream -- re-read from the CHECKSUM-verified RPM, git tree blob ids, Authenticode size now + signature on the worker and in the guest for WebView2 only). Nothing unverified is ever kept; any mismatch aborts. Writes inputs.json for answer.py and the worker.
# AI-related: usr/libexec/mios/win/xbox/resolve_latest.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/build.py, tests/test-mios-xbox-build.py
# AI-functions: Downloader, fetch_uup, fetch_fod, fetch_virtio, burn_payloads, carve_burn_msi, fetch_file, fetch_converter, render_converter_config, fetch_all, main
"""Fetch + verify the resolved MiOS-Xbox build inputs.

  fetch.py --manifest BUILD/build-manifest.json --spec BUILD/xbox-spec.json --dir WORK/inputs

Exit 0 fetched and verified; 2 an artefact could not be fetched or verified (nothing partial is left
marked as verified). Network access is the only side effect besides files under --dir."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import struct
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import resolve_latest as RL  # noqa: E402

USER_AGENT = "MiOS fetch (xb-15)"
CHUNK = 1 << 20
INPUTS_SCHEMA = "mios-xbox-inputs/1"


class FetchError(RuntimeError):
    """An artefact could not be fetched or does not verify: the build must abort."""


class Downloader:
    """Streaming HTTP(S) download to a .part file, bounded retries, optional exact size. Injectable for tests."""

    def __init__(self, tries: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.tries = max(1, int(tries))
        self.sleep = sleep

    def get(self, url: str, dest: str, size: Optional[int] = None) -> None:
        last = ""
        for i in range(1, self.tries + 1):
            part = dest + ".part"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as fh:
                    n = 0
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        n += len(b)
                        if size is not None and n > size:
                            raise FetchError(f"{RL._redact(url)}: more than the resolved {size} bytes")
                        fh.write(b)
                    fh.flush()
                    os.fsync(fh.fileno())
                if size is not None and n != size:
                    raise FetchError(f"{RL._redact(url)}: got {n} bytes, resolved size {size}")
                os.replace(part, dest)
                return
            except FetchError:
                if os.path.exists(part):
                    os.unlink(part)
                raise
            except (OSError, urllib.error.URLError) as e:
                last = f"{type(e).__name__}: {e}"
                if os.path.exists(part):
                    os.unlink(part)
            if i < self.tries:
                self.sleep(min(2 ** i, 30))
        raise FetchError(f"GET {RL._redact(url)} failed after {self.tries} tries: {last}")


def _verify(manifest: Dict[str, Any], artifact: str, path: str, member: Optional[str] = None) -> str:
    """resolve_latest.verify_file, except that an Authenticode anchor (only artefacts their publisher really signs:
    WebView2) without osslsigncode on the host is verified by size here and by signature on the worker
    (New-MiOSXboxImage requires a Valid signature by the expected signer) and again in the guest before install.
    Unsigned artefacts (virtio-win, qemu-ga) carry a sha256 anchor and never an Authenticode claim."""
    integ = ((manifest.get("artifacts") or {}).get(artifact) or {}).get("integrity") or {}
    try:
        if integ.get("kind") == "authenticode" and not (os.environ.get("MIOS_OSSLSIGNCODE") or shutil.which("osslsigncode")):
            if integ.get("size") is None or os.path.getsize(path) != int(integ["size"]):
                raise RL.ResolveError(f"{os.path.basename(path)}: size {os.path.getsize(path)} != published {integ.get('size')}")
            return "size (signature: worker + guest)"
        return RL.verify_file(manifest, artifact, path, member)
    except RL.ResolveError as e:
        if os.path.exists(path):
            os.unlink(path)
        raise FetchError(f"[{artifact}] {e}") from None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def _art(manifest: Dict[str, Any], name: str) -> Dict[str, Any]:
    a = (manifest.get("artifacts") or {}).get(name)
    if not isinstance(a, dict) or not a.get("integrity"):
        raise FetchError(f"build-manifest.json has no verified entry for {name} (run resolve_latest.py first)")
    return a


# ---------------------------------------------------------------------------
# UUP
# ---------------------------------------------------------------------------

def fetch_uup(manifest: Dict[str, Any], spec: Dict[str, Any], dest: str, dl: Downloader,
              http: Any = None) -> str:
    """Fresh signed URLs for the resolved update id; the file set must equal the manifest's exactly."""
    a = _art(manifest, "windows_uup")
    api = str(spec["spec"]["resolve"]["uupdump_api"]).rstrip("/")
    q = urllib.parse.urlencode({"id": a["uuid"], "lang": a["lang"], "edition": a["edition"]})
    want = {f["name"]: f for f in a["files"]}

    def _refresh_files() -> Dict[str, Any]:
        got = RL._fetch(http or RL.Http(), f"{api}/get.php?{q}", tries=dl.tries, sleep=dl.sleep,
                        retry=RL.UUP_API_RETRY).json().get("response") or {}
        fls = got.get("files")
        if not isinstance(fls, dict) or not fls:
            raise FetchError(f"[windows_uup] get.php returned no file list ({got.get('error')})")
        if set(fls) != set(want):
            raise FetchError(f"[windows_uup] the live file set differs from the resolved one "
                             f"({len(set(fls) ^ set(want))} names); re-resolve (the build moved on)")
        return fls

    files = _refresh_files()
    os.makedirs(dest, exist_ok=True)
    for name in sorted(want):
        if "/" in name or "\\" in name or name.startswith("."):
            raise FetchError(f"[windows_uup] unsafe file name {name!r}")
        path = os.path.join(dest, name)
        if os.path.isfile(path) and os.path.getsize(path) == int(want[name]["size"]) and _sha256(path) == want[name]["sha256"]:
            continue   # an already verified file from an interrupted fetch of the same resolution
        for refresh_attempt in range(3):
            live = files[name] or {}
            if str(live.get("sha256", "")).lower() != want[name]["sha256"]:
                raise FetchError(f"[windows_uup] {name}: live sha256 differs from the resolved list")
            url = str(live.get("url") or "")
            if not url.startswith(("https://", "http://")):
                raise FetchError(f"[windows_uup] {name}: no download URL")
            try:
                dl.get(url, path, int(want[name]["size"]))
                break
            except FetchError:
                if refresh_attempt < 2:
                    time.sleep(5)
                    files = _refresh_files()
                else:
                    raise
        _verify(manifest, "windows_uup", path, name)
    return dest


# ---------------------------------------------------------------------------
# virtio-win: RPM -> ISO -> drivers + guest tools
# ---------------------------------------------------------------------------

def _run(cmd: List[str], **kw: Any) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, timeout=1800, **kw)
    if r.returncode != 0:
        raise FetchError(f"{os.path.basename(cmd[0])} failed rc={r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
    return r


VIRTIO_ROLES = ("iso", "guest_tools", "gt_msi")   # extracted from the RPM; qemu_ga is fetched from latest-qemu-ga


def extract_rpm_members(manifest: Dict[str, Any], rpm: str, ex: str, roles: Tuple[str, ...] = VIRTIO_ROLES) -> Dict[str, str]:
    """The CHECKSUM-verified virtio-win RPM -> its members, each verified by the sha256 + size the resolved RPM header
    records (build-manifest member_digests). The downloaded RPM's own header must be the resolved one (same signature-
    header sha256, same member digests): the sha256 pins are thereby anchored by upstream's CHECKSUM. Any mismatch
    removes what was extracted and aborts."""
    a = _art(manifest, "virtio_win")
    members, pins = a.get("members") or {}, a.get("member_digests") or {}
    if any(not members.get(r) or not pins.get(members[r]) for r in roles) or not a.get("rpm_header_sha256"):
        raise FetchError("[virtio_win] build-manifest.json records no sha256 per virtio-win member (re-resolve)")
    try:
        with open(rpm, "rb") as fh:
            head = fh.read(1 << 20)
            need = RL.rpm_header_extent(head)
            if need > len(head):
                fh.seek(0)
                head = fh.read(need)
        info = RL.rpm_header(head[:need])
    except RL.ResolveError as e:
        raise FetchError(f"[virtio_win] {os.path.basename(rpm)}: {e}") from None
    if info["header_sha256"] != a["rpm_header_sha256"]:
        raise FetchError("[virtio_win] the downloaded RPM's header is not the resolved one (re-resolve)")
    for r in roles:
        f = info["files"].get(members[r]) or {}
        if f.get("sha256") != pins[members[r]]["sha256"] or f.get("size") != pins[members[r]]["size"]:
            raise FetchError(f"[virtio_win] {members[r]}: the RPM header digest differs from the resolved one")
    shutil.rmtree(ex, ignore_errors=True)
    os.makedirs(ex)
    p1 = subprocess.Popen(["rpm2cpio", rpm], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        p2 = subprocess.Popen(["cpio", "-idm", "--no-absolute-filenames", "--quiet", *("./" + members[r] for r in roles)],
                              stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ex)
        assert p1.stdout is not None
        p1.stdout.close()
        _, err2 = p2.communicate(timeout=1800)
        _, err1 = p1.communicate(timeout=1800)
    finally:
        for p in (p1, p2 if "p2" in locals() else None):
            if p and p.poll() is None:
                p.kill()
                p.wait()
    if p1.returncode != 0:
        shutil.rmtree(ex, ignore_errors=True)
        raise FetchError(f"[virtio_win] rpm2cpio failed: {(err1 or b'').decode(errors='replace')[-200:]}")
    if p2.returncode != 0:
        shutil.rmtree(ex, ignore_errors=True)
        raise FetchError(f"[virtio_win] cpio failed rc={p2.returncode}: {(err2 or b'').decode(errors='replace')[-300:]}")
    out: Dict[str, str] = {}
    for r in roles:
        path, pin = os.path.join(ex, members[r]), pins[members[r]]
        if not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) != int(pin["size"]) or _sha256(path) != pin["sha256"]:
            shutil.rmtree(ex, ignore_errors=True)
            raise FetchError(f"[virtio_win] {members[r]}: missing, or its sha256/size differs from the RPM header pin")
        out[r] = path
    return out


def fetch_virtio(manifest: Dict[str, Any], spec: Dict[str, Any], work: str, dl: Downloader) -> Dict[str, str]:
    a = _art(manifest, "virtio_win")
    vw = spec["spec"]["drivers"]["virtio_win"]
    rpm = os.path.join(work, os.path.basename(urllib.parse.urlsplit(a["fetch"]).path))
    if not os.path.isfile(rpm):
        dl.get(a["fetch"], rpm)
    _verify(manifest, "virtio_win", rpm)          # CHECKSUM (md5): the only digest upstream publishes
    got = extract_rpm_members(manifest, rpm, os.path.join(work, "virtio-rpm"))   # sha256 per member (RPM header)
    iso, gt = got["iso"], got["guest_tools"]
    gts = spec["spec"]["guest_tools"]
    msi_dir = os.path.join(work, "virtio-msi")
    shutil.rmtree(msi_dir, ignore_errors=True)
    os.makedirs(msi_dir)
    # the driver/service MSI of the release (sha256-pinned; never the composite bundle: it exits 1603 silently)
    msi = os.path.join(msi_dir, str(gts["virtio_win_guest_tools"]["file"]))
    shutil.copyfile(got["gt_msi"], msi)
    sp = gts["spice_vdagent"]
    spice = carve_burn_msi(gt, str(sp["product"]), os.path.join(work, "burn"), os.path.join(msi_dir, str(sp["package"])))
    return {"iso": iso, "guest_tools": gt, "gt_msi": msi, "spice_msi": spice,
            "drivers": _stage_virtio_drivers(iso, vw, work)}


# ---------------------------------------------------------------------------
# WiX Burn bundle (virtio-win-guest-tools.exe): carve one MSI out, verified by its manifest SHA-1
# ---------------------------------------------------------------------------

def _cabinets(data: bytes) -> List[Tuple[int, int]]:
    """(offset, size) of every well-formed MS-CAB header in data (MSCF, zero reserved fields, version 1.3)."""
    out: List[Tuple[int, int]] = []
    i = data.find(b"MSCF")
    while i >= 0:
        if i + 36 <= len(data):
            r1, size, r2, _coff, r3 = struct.unpack_from("<IIIII", data, i + 4)
            minor, major = data[i + 24], data[i + 25]
            if r1 == 0 and r2 == 0 and r3 == 0 and (major, minor) == (1, 3) and 36 <= size <= len(data) - i:
                out.append((i, size))
                i = data.find(b"MSCF", i + size)
                continue
        i = data.find(b"MSCF", i + 4)
    return out


def burn_payloads(bundle: str, work: str) -> Tuple[ET.Element, Dict[str, str]]:
    """The Burn manifest (UX container file '0') and {SourcePath: extracted file} of the attached container."""
    with open(bundle, "rb") as fh:
        data = fh.read()
    cabs = _cabinets(data)
    if len(cabs) < 2:
        raise FetchError(f"[virtio_win] {os.path.basename(bundle)} is not a Burn bundle with an attached container")
    shutil.rmtree(work, ignore_errors=True)
    files: Dict[str, str] = {}
    manifest = None
    for n, (off, size) in enumerate(cabs[:2]):
        cab = os.path.join(work, f"c{n}.cab")
        d = os.path.join(work, f"c{n}")
        os.makedirs(d)
        with open(cab, "wb") as fh:
            fh.write(data[off:off + size])
        _run(["7z", "e", "-y", "-bd", "-ssc-", f"-o{d}", cab])
        for fn in os.listdir(d):
            if n == 0 and fn == "0":
                manifest = os.path.join(d, fn)
            elif n == 1:
                files[fn] = os.path.join(d, fn)
    if manifest is None:
        raise FetchError("[virtio_win] the Burn UX container carries no manifest")
    try:
        root = ET.parse(manifest).getroot()
    except ET.ParseError as e:
        raise FetchError(f"[virtio_win] Burn manifest is not XML ({e})") from None
    return root, files


def carve_burn_msi(bundle: str, product: str, work: str, dest: str) -> str:
    """The single 64-bit MsiPackage of the bundle whose MSI carries ``product`` (its ProductName), copied to
    dest after its SHA-1 equals the manifest's Payload Hash. Ambiguity or a mismatch aborts."""
    root, files = burn_payloads(bundle, work)
    loc = lambda e: e.tag.rsplit("}", 1)[-1]  # noqa: E731
    payloads = {p.get("Id"): p for p in root.iter() if loc(p) == "Payload"}
    hits: List[Tuple[str, str]] = []
    for pkg in (e for e in root.iter() if loc(e) == "MsiPackage"):
        if (pkg.get("InstallCondition") or "").strip() != "VersionNT64":
            continue
        for ref in (c for c in pkg if loc(c) == "PayloadRef"):
            pl = payloads.get(ref.get("Id"))
            src = files.get((pl.get("SourcePath") if pl is not None else "") or "")
            if pl is None or not src:
                continue
            with open(src, "rb") as fh:
                body = fh.read()
            if product.encode("utf-8") not in body:
                continue
            want = str(pl.get("Hash") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{40}", want) or hashlib.sha1(body).hexdigest() != want:
                raise FetchError(f"[virtio_win] Burn payload {pl.get('FilePath')} does not match its manifest SHA-1")
            hits.append((src, str(pkg.get("Version") or "")))
    if len(hits) != 1:
        raise FetchError(f"[virtio_win] {len(hits)} 64-bit MSI packages carry {product!r} in {os.path.basename(bundle)} (need exactly one)")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(hits[0][0], dest)
    shutil.rmtree(work, ignore_errors=True)
    return dest


def fetch_fod(manifest: Dict[str, Any], spec: Dict[str, Any], dest: str, dl: "Downloader", http: Any = None) -> str:
    """The OpenSSH.Server FOD cab(s) + FOD metadata of the resolved update (resolve_latest fod_files), verified
    by sha256/size and staged under Microsoft's FOD repository names for DISM /Add-Capability /Source /LimitAccess.
    Kept apart from the UUP set so the converter never sees them."""
    a = _art(manifest, "windows_uup")
    fods = a.get("fod_files") or []
    if spec["spec"]["features"].get("capabilities_add") and not fods:
        raise FetchError("[windows_uup] the manifest resolves no FOD file for windows.xbox.features.capabilities_add (re-resolve)")
    os.makedirs(dest, exist_ok=True)
    if not fods:
        return dest
    api = str(spec["spec"]["resolve"]["uupdump_api"]).rstrip("/")
    got = RL._fetch(http or RL.Http(), f"{api}/get.php?{urllib.parse.urlencode({'id': a['uuid']})}",
                    tries=dl.tries, sleep=dl.sleep, retry=RL.UUP_API_RETRY).json().get("response") or {}
    live = got.get("files") or {}
    for f in fods:
        canon = str(f["canonical"])
        if not re.fullmatch(r"[\w.~\-]+", canon) or not re.fullmatch(r"[\w.\-]+", str(f["name"])):
            raise FetchError(f"[windows_uup] unsafe FOD file name {f['name']!r} / {canon!r}")
        target = os.path.join(dest, canon)
        if os.path.isfile(target) and os.path.getsize(target) == int(f["size"]) and _sha256(target) == f["sha256"]:
            continue
        lf = live.get(f["name"]) or {}
        if str(lf.get("sha256", "")).lower() != f["sha256"]:
            raise FetchError(f"[windows_uup] FOD {f['name']}: live sha256 differs from the resolved list")
        url = str(lf.get("url") or "")
        if not url.startswith(("https://", "http://")):
            raise FetchError(f"[windows_uup] FOD {f['name']}: no download URL")
        path = os.path.join(dest, f["name"])
        dl.get(url, path, int(f["size"]))
        _verify(manifest, "windows_uup", path, f["name"])
        os.replace(path, target)
    return dest


def fetch_apps(manifest: Dict[str, Any], spec: Dict[str, Any], dest: str, dl: "Downloader", http: Any = None) -> List[str]:
    """The SSOT inbox apps + their frameworks of the resolved update (resolve_latest app_files), verified by sha256/size
    and placed in the UUP set under the names UUP Dump serves them (IPA_<group>_<file>): the converter's CompDB_App step
    sorts them into Apps\\<family> / Apps\\MSIXFramework and provisions the CustomAppsList.txt families offline."""
    a = _art(manifest, "windows_uup")
    rows = a.get("app_files") or []
    want = list((spec["spec"].get("source") or {}).get("apps") or [])
    got = sorted({str(f.get("pfn")) for f in rows if f.get("role") == "app"}, key=str.lower)
    if sorted(want, key=str.lower) != got:
        raise FetchError("[windows_uup] the manifest's inbox apps differ from windows.xbox.source.apps (re-resolve): "
                         f"{len(want)} wanted, {len(got)} resolved")
    os.makedirs(dest, exist_ok=True)
    if not rows:
        return []
    api = str(spec["spec"]["resolve"]["uupdump_api"]).rstrip("/")
    live = RL._fetch(http or RL.Http(), f"{api}/get.php?{urllib.parse.urlencode({'id': a['uuid']})}",
                     tries=dl.tries, sleep=dl.sleep, retry=RL.UUP_API_RETRY).json().get("response") or {}
    live = live.get("files") or {}
    names = []
    for f in rows:
        name = str(f["name"])
        if not re.fullmatch(r"IPA_[\w.\-~]+", name):
            raise FetchError(f"[windows_uup] unsafe inbox app file name {name!r}")
        path = os.path.join(dest, name)
        names.append(name)
        if os.path.isfile(path) and os.path.getsize(path) == int(f["size"]) and _sha256(path) == f["sha256"]:
            continue
        lf = live.get(name) or {}
        if str(lf.get("sha256", "")).lower() != f["sha256"]:
            raise FetchError(f"[windows_uup] inbox app {name}: live sha256 differs from the resolved list")
        url = str(lf.get("url") or "")
        if not url.startswith(("https://", "http://")):
            raise FetchError(f"[windows_uup] inbox app {name}: no download URL")
        dl.get(url, path, int(f["size"]))
        _verify(manifest, "windows_uup", path, name)
    return names


def converter_store_apps(spec: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """ConvertConfig.ini [Store_Apps] overrides + the CustomAppsList.txt text (None = no inbox apps). SkipApps and
    CustomList follow windows.xbox.source.apps; the SSOT store_apps table may not set them (one owner per value)."""
    conv = spec["spec"]["build"]["converter"]
    store = dict(conv.get("store_apps") or {})
    for k in store:
        if k.lower() in ("skipapps", "customlist"):
            raise FetchError(f"windows.xbox.build.converter.store_apps.{k}: derived from windows.xbox.source.apps, never set")
    apps = list((spec["spec"].get("source") or {}).get("apps") or [])
    store.update({"SkipApps": 0 if apps else 1, "CustomList": 1 if apps else 0})
    if not apps:
        return store, None
    head = ["### MiOS-Xbox: windows.xbox.source.apps (rendered by usr/libexec/mios/win/xbox/fetch.py; never edit by hand)"]
    return store, "\r\n".join(head + apps) + "\r\n"


def _stage_virtio_drivers(iso: str, vw: Dict[str, Any], work: str) -> str:
    raw = os.path.join(work, "drivers-raw")
    shutil.rmtree(raw, ignore_errors=True)
    os.makedirs(raw)
    _run(["7z", "x", "-y", "-bd", "-ssc-", f"-o{raw}", iso, f"*/{vw['os_dir']}/{vw['arch']}/*"])
    by_inf: Dict[str, str] = {}
    for root, _, files in os.walk(raw):
        for fn in files:
            if fn.lower().endswith(".inf"):
                by_inf[fn[:-4].lower()] = root
    drivers = os.path.join(work, "drivers")
    shutil.rmtree(drivers, ignore_errors=True)
    os.makedirs(drivers)
    try:
        for inf in vw["inf"]:
            src_dir = by_inf.get(inf.lower())
            if not src_dir or not os.path.isdir(src_dir):
                raise FetchError(f"[virtio_win] driver {inf} has no {vw['os_dir']}/{vw['arch']} .inf in the resolved ISO")
            dst_dir = os.path.join(drivers, inf, vw["os_dir"], vw["arch"])
            os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
            shutil.copytree(src_dir, dst_dir)
    finally:
        shutil.rmtree(raw, ignore_errors=True)
    return drivers


# ---------------------------------------------------------------------------
# single-file artefacts
# ---------------------------------------------------------------------------

def fetch_file(manifest: Dict[str, Any], artifact: str, work: str, dl: Downloader, name: Optional[str] = None) -> str:
    a = _art(manifest, artifact)
    fname = name or os.path.basename(urllib.parse.urlsplit(a["fetch"]).path)
    if not re.fullmatch(r"[\w.\-]+", fname or ""):
        raise FetchError(f"[{artifact}] unsafe file name {fname!r}")
    path = os.path.join(work, fname)
    size = (a.get("integrity") or {}).get("size")
    if not os.path.isfile(path):
        dl.get(a["fetch"], path, int(size) if size is not None else None)
    _verify(manifest, artifact, path)
    return path


# ---------------------------------------------------------------------------
# UUP Dump's Windows converter (git-tree anchored)
# ---------------------------------------------------------------------------

def _safe_extract(tar_path: str, dest: str, prefix_sub: str) -> str:
    """Extract only regular files under <top>/<sub>/ of a GitHub tarball; refuse links and path escapes."""
    out = os.path.join(dest, "converter")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    with tarfile.open(tar_path, "r:gz") as tf:
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1].startswith(prefix_sub + "/"):
                continue
            rel = parts[1][len(prefix_sub) + 1:]
            if not rel or m.isdir():
                continue
            if not m.isreg():
                raise FetchError(f"[uup_converter] {m.name}: links/devices are refused")
            norm = os.path.normpath(rel)
            if norm.startswith("..") or os.path.isabs(norm):
                raise FetchError(f"[uup_converter] {m.name}: path escapes the converter directory")
            target = os.path.join(out, norm)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(m)
            with open(target, "wb") as fh:
                shutil.copyfileobj(src, fh, CHUNK)
    return out


_CONVERTER_SECTIONS = {"convert-UUP": "config", "Store_Apps": "store_apps"}   # ConvertConfig.ini section -> SSOT key


def render_converter_config(upstream_ini: str, overrides: Dict[str, Any],
                            sections: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """Upstream ConvertConfig.ini with the SSOT [convert-UUP] overrides (plus `sections`, e.g. [Store_Apps]); an
    unknown key aborts."""
    lines = upstream_ini.replace("\r\n", "\n").split("\n")
    want_by = {"convert-UUP": overrides, **(sections or {})}
    want = {sec: {str(k).lower(): (k, v) for k, v in ov.items()} for sec, ov in want_by.items()}
    seen: Dict[str, set] = {sec: set() for sec in want}
    sect, out = "", []
    for ln in lines:
        m = re.match(r"^\[(.+)\]\s*$", ln)
        if m:
            sect = m.group(1)
        mk = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*=).*$", ln)
        if sect in want and mk and mk.group(2).lower() in want[sect]:
            k, v = want[sect][mk.group(2).lower()]
            if isinstance(v, bool) or not isinstance(v, (int, str)):
                raise FetchError(f"windows.xbox.build.converter.{_CONVERTER_SECTIONS.get(sect, sect)}.{k}: must be an integer or string")
            ln = f"{mk.group(1)}{mk.group(2)}{mk.group(3)}{v}"
            seen[sect].add(mk.group(2).lower())
        out.append(ln)
    for sec in want:
        missing = sorted(set(want[sec]) - seen[sec])
        if missing:
            raise FetchError(f"windows.xbox.build.converter.{_CONVERTER_SECTIONS.get(sec, sec)}: upstream ConvertConfig.ini "
                             f"has no [{sec}] key(s) " + ", ".join(want[sec][k][0] for k in missing))
    return "\r\n".join(out)


def fetch_converter(manifest: Dict[str, Any], spec: Dict[str, Any], work: str, dl: Downloader) -> str:
    a = _art(manifest, "uup_converter")
    tgz = os.path.join(work, "converter.tar.gz")
    dl.get(a["fetch"], tgz)
    out = _safe_extract(tgz, work, a["subdir"])
    try:
        RL.verify_tree(manifest, "uup_converter", out)
    except RL.ResolveError as e:
        shutil.rmtree(out, ignore_errors=True)
        raise FetchError(f"[uup_converter] {e}") from None
    conv = spec["spec"]["build"]["converter"]
    ini = os.path.join(out, "ConvertConfig.ini")
    if not os.path.isfile(ini) or not os.path.isfile(os.path.join(out, conv["entry"])):
        raise FetchError("[uup_converter] resolved commit lacks ConvertConfig.ini or the entry script")
    store, app_list = converter_store_apps(spec)
    with open(ini, encoding="utf-8-sig", errors="replace") as fh:
        text = render_converter_config(fh.read(), conv["config"], {"Store_Apps": store})
    with open(ini, "w", encoding="ascii", newline="") as fh:
        fh.write(text)
    # the converter prefers CustomAppsList2.txt when present: only our rendered list may exist
    for n in ("CustomAppsList.txt", "CustomAppsList2.txt"):
        if os.path.exists(os.path.join(out, n)):
            os.remove(os.path.join(out, n))
    if app_list is not None:
        with open(os.path.join(out, "CustomAppsList.txt"), "w", encoding="ascii", newline="") as fh:
            fh.write(app_list)
    return out


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------

def fetch_all(manifest: Dict[str, Any], spec: Dict[str, Any], work: str, dl: Optional[Downloader] = None,
              http: Any = None) -> Dict[str, Any]:
    x = spec["spec"]
    dl = dl or Downloader(int(x["build"]["download_tries"]))
    os.makedirs(work, mode=0o700, exist_ok=True)
    res = x["resolve"]
    vio = fetch_virtio(manifest, spec, work, dl)
    qga = fetch_file(manifest, "qemu_ga", work, dl, x["guest_tools"]["qemu_ga"]["package"])
    wfs = fetch_file(manifest, "winfsp", work, dl)
    wv2 = fetch_file(manifest, "webview2", work, dl)
    lgh = fetch_file(manifest, "looking_glass_host", work, dl, "looking-glass-host.zip")
    lgi = fetch_file(manifest, "looking_glass_idd", work, dl, "looking-glass-idd.zip")
    conv = fetch_converter(manifest, spec, work, dl)
    uup = fetch_uup(manifest, spec, os.path.join(work, "uup"), dl, http)
    fetch_apps(manifest, spec, uup, dl, http)   # the converter integrates them from its UUPs dir
    fod = fetch_fod(manifest, spec, os.path.join(work, "fod"), dl, http)
    arts = manifest["artifacts"]

    def row(path: str, art: str, signer: str, anchor: str) -> Dict[str, Any]:
        return {"path": path, "sha256": _sha256(path), "signer": signer, "anchor": anchor, "version": arts[art]["version"],
                "url": arts[art].get("source", "")}

    vio_anchor = "sha256 (virtio-win RPM header <- CHECKSUM); unsigned: no Authenticode check"
    return {
        "schema": INPUTS_SCHEMA,
        "installers": {
            # virtio-win-gt and qemu-ga MSIs are not Authenticode-signed: the anchor is the sha256 the virtio-win RPM
            # header records (signer ""). The SPICE agent MSI is carved from the sha256-pinned bundle by its Burn
            # manifest SHA-1 (same anchor). The composite bundle itself is never staged.
            "virtio_guest_tools": row(vio["gt_msi"], "virtio_win", "", vio_anchor),
            "spice_vdagent": row(vio["spice_msi"], "virtio_win", "", "Burn manifest SHA-1 of the " + vio_anchor),
            "qemu_ga": row(qga, "qemu_ga", "", vio_anchor),
            "winfsp": row(wfs, "winfsp", "", "sha256 (GitHub release asset digest)"),
            "webview2": row(wv2, "webview2", res["webview2_signer"], "Authenticode (Microsoft) + size"),
            "looking_glass_host": row(lgh, "looking_glass_host", res["lg_signer"], "sha256 (looking-glass.io) + Authenticode of the installer"),
            "looking_glass_idd": row(lgi, "looking_glass_idd", res["lg_signer"], "sha256 (looking-glass.io) + Authenticode of the installer"),
        },
        "drivers_dir": vio["drivers"],
        "uup_dir": uup,
        "fod_dir": fod,
        "converter_dir": conv,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch.py", description="Fetch + verify the resolved MiOS-Xbox build inputs")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", help="inputs.json path (default: <dir>/inputs.json)")
    a = ap.parse_args(argv)
    try:
        with open(a.manifest, encoding="utf-8") as fh:
            manifest = json.load(fh)
        with open(a.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
        inputs = fetch_all(manifest, spec, a.dir)
    except (FetchError, RL.ResolveError, OSError, ValueError, KeyError) as e:
        print(f"[fetch] ABORT: {e}", file=sys.stderr)
        return 2
    out = a.out or os.path.join(a.dir, "inputs.json")
    _write_json(out, inputs)
    print(f"[fetch] verified {len(inputs['installers'])} installers, drivers, converter and the UUP set -> {out}")
    return 0


def _write_json(path: str, data: Any) -> None:
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".inputs.", dir=d)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


if __name__ == "__main__":
    sys.exit(main())
