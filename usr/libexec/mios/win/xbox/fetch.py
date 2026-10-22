#!/usr/bin/env python3
# AI-hint: MiOS-Xbox fetch step (xb-15, XB-2): downloads every artefact resolve_latest.py recorded in build-manifest.json (fresh UUP Dump file list for the resolved update id, virtio-win RPM -> ISO -> w11 driver tree + guest-tools bundle, latest qemu-ga, WinFsp, WebView2 standalone, Looking Glass host + IDD of the client's commit, UUP Dump's Windows converter at the resolved commit) and verifies each against the manifest's integrity anchor (UUP per-file sha256 + Microsoft FileDigest, published md5/sha256, git tree blob ids, Authenticode size now + signature on the worker and in the guest). Nothing unverified is ever kept; any mismatch aborts. Writes inputs.json for answer.py and the worker.
# AI-related: usr/libexec/mios/win/xbox/resolve_latest.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/build.py, tests/test-mios-xbox-build.py
# AI-functions: Downloader, fetch_uup, fetch_virtio, fetch_file, fetch_converter, render_converter_config, fetch_all, main
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
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

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
    """resolve_latest.verify_file, except that an Authenticode anchor without osslsigncode on the host is
    verified by size here and by signature on the worker (New-MiOSXboxImage refuses to bake an unsigned
    installer) and again in the guest before install."""
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
        got = RL._fetch(http or RL.Http(), f"{api}/get.php?{q}", tries=dl.tries, sleep=dl.sleep).json().get("response") or {}
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


def fetch_virtio(manifest: Dict[str, Any], spec: Dict[str, Any], work: str, dl: Downloader) -> Dict[str, str]:
    a = _art(manifest, "virtio_win")
    vw = spec["spec"]["drivers"]["virtio_win"]
    rpm = os.path.join(work, os.path.basename(urllib.parse.urlsplit(a["fetch"]).path))
    if not os.path.isfile(rpm):
        dl.get(a["fetch"], rpm)
    _verify(manifest, "virtio_win", rpm)
    ex = os.path.join(work, "virtio-rpm")
    shutil.rmtree(ex, ignore_errors=True)
    os.makedirs(ex)
    members = a.get("members") or {}
    if not members.get("iso") or not members.get("guest_tools"):
        raise FetchError("[virtio_win] manifest names no ISO/guest-tools member")
    p1 = subprocess.Popen(["rpm2cpio", rpm], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        p2 = subprocess.Popen(["cpio", "-idm", "--no-absolute-filenames", "--quiet",
                               "./" + members["iso"], "./" + members["guest_tools"]],
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
        raise FetchError(f"[virtio_win] rpm2cpio failed: {(err1 or b'').decode(errors='replace')[-200:]}")
    if p2.returncode != 0:
        raise FetchError(f"[virtio_win] cpio failed rc={p2.returncode}: {(err2 or b'').decode(errors='replace')[-300:]}")
    iso = os.path.join(ex, members["iso"])
    gt = os.path.join(ex, members["guest_tools"])
    if not os.path.isfile(iso) or not os.path.isfile(gt):
        raise FetchError("[virtio_win] the verified RPM lacks the ISO or the guest-tools bundle")
    return {"iso": iso, "guest_tools": gt, "drivers": _stage_virtio_drivers(iso, vw, work)}


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


def render_converter_config(upstream_ini: str, overrides: Dict[str, Any]) -> str:
    """Upstream ConvertConfig.ini with the SSOT [convert-UUP] overrides; an unknown key aborts."""
    lines = upstream_ini.replace("\r\n", "\n").split("\n")
    sect, seen, out = "", set(), []
    want = {str(k).lower(): (k, v) for k, v in overrides.items()}
    for ln in lines:
        m = re.match(r"^\[(.+)\]\s*$", ln)
        if m:
            sect = m.group(1)
        mk = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*=).*$", ln)
        if sect == "convert-UUP" and mk and mk.group(2).lower() in want:
            k, v = want[mk.group(2).lower()]
            if isinstance(v, bool) or not isinstance(v, (int, str)):
                raise FetchError(f"windows.xbox.build.converter.config.{k}: must be an integer or string")
            ln = f"{mk.group(1)}{mk.group(2)}{mk.group(3)}{v}"
            seen.add(mk.group(2).lower())
        out.append(ln)
    missing = sorted(set(want) - seen)
    if missing:
        raise FetchError("windows.xbox.build.converter.config: upstream ConvertConfig.ini has no [convert-UUP] key(s) "
                         + ", ".join(want[k][0] for k in missing))
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
    with open(ini, encoding="utf-8-sig", errors="replace") as fh:
        text = render_converter_config(fh.read(), conv["config"])
    with open(ini, "w", encoding="ascii", newline="") as fh:
        fh.write(text)
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
    arts = manifest["artifacts"]

    def row(path: str, art: str, signer: str) -> Dict[str, Any]:
        return {"path": path, "sha256": _sha256(path), "signer": signer, "version": arts[art]["version"],
                "url": arts[art].get("source", "")}

    return {
        "schema": INPUTS_SCHEMA,
        "installers": {
            # the virtio-win guest-tools bundle is published by the same signer as qemu-ga (Red Hat)
            "virtio_guest_tools": row(vio["guest_tools"], "virtio_win", res["qemu_ga_signer"]),
            "qemu_ga": row(qga, "qemu_ga", res["qemu_ga_signer"]),
            "winfsp": row(wfs, "winfsp", ""),
            "webview2": row(wv2, "webview2", res["webview2_signer"]),
            "looking_glass_host": row(lgh, "looking_glass_host", res["lg_signer"]),
            "looking_glass_idd": row(lgi, "looking_glass_idd", res["lg_signer"]),
        },
        "drivers_dir": vio["drivers"],
        "uup_dir": uup,
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
