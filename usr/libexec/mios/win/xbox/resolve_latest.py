#!/usr/bin/env python3
# AI-hint: MiOS-Xbox latest-resolver (xb-06, XB-2). Resolves, at build time and with no pins, the newest
#   UUP Dump build for the SSOT channel/arch/edition/lang, virtio-win (latest-virtio), the QEMU guest agent
#   (latest-qemu-ga), the Looking Glass host built from the same commit as the host's client, the newest
#   WinFsp release and the WebView2 evergreen offline installer, each with the integrity anchor its upstream
#   publishes (virtio-win: CHECKSUM md5 of the RPM plus the sha256 per member its RPM header records, read with a ranged
#   GET; the unsigned qemu-ga MSI: that header's sha256 of guest-agent/<package>, never an Authenticode claim). Writes ONLY build-manifest.json (never the SSOT); any resolver failure aborts and removes a
#   stale manifest so nothing downstream can fall back to an older/cached version.
# AI-related: usr/lib/mios/mios_toml.py, usr/libexec/mios/vfio/setup-looking-glass.py, automation/69-bake-lookingglass-client.sh, tests/test-mios-xbox-resolve-latest.py, usr/share/mios/mios.toml
# AI-functions: load_config, resolve_uup, resolve_virtio, resolve_qemu_ga, resolve_lg_host, resolve_winfsp,
#   resolve_webview2, resolve_lg_idd, resolve_converter, resolve_all, verify_file, verify_tree, git_blob_sha1,
#   write_manifest_atomic, main
"""Resolve the latest upstream inputs of a MiOS-Xbox image build into build-manifest.json.

Usage:
  resolve_latest.py resolve --out BUILD_DIR/build-manifest.json [--profile mios-xbox] [--layers A.toml B.toml ...] [--replace]
  resolve_latest.py verify  --manifest BUILD_DIR/build-manifest.json --artifact NAME --file PATH [--member NAME]

Inputs come only from the merged SSOT: the [windows.xbox] spec tree (source, drivers.virtio_win,
guest_tools.qemu_ga/winfsp, baked.webview2; locale via source.language_ref; channel/arch fall back to
[editions.<profile>].autounattend.*) plus the endpoints/trust anchors in [windows.xbox.resolve]. A missing key, an unreachable endpoint, a malformed or inconsistent
published digest, or a pinned value in the SSOT all exit non-zero with nothing written.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA = "mios.xbox.build-manifest/1"
MANIFEST_NAME = "build-manifest.json"
USER_AGENT = "MiOS resolve_latest (xb-06)"

# Channel -> UUP Dump / Windows Update ring. An unknown channel is an error, not a silent Retail.
RINGS = {"canary": "Canary", "dev": "Dev", "beta": "Beta", "release_preview": "ReleasePreview",
         "releasepreview": "ReleasePreview", "rp": "ReleasePreview", "retail": "Retail"}

# Endpoints and trust anchors only the resolver needs ([windows.xbox.resolve]); everything else comes from the
# [windows.xbox] spec tree itself (source/drivers/guest_tools/baked), so there is one spec.
REQUIRED_RESOLVE_KEYS = ("uupdump_api", "lg_downloads", "lg_client_build_record", "github_api",
                         "edge_products_api", "webview2_signer", "lg_signer")

# Values that would pin a version: the pins gate (xb-04) rejects these in the SSOT; the resolver refuses them too.
_PIN_KEY = re.compile(r"(^|_)(version|build|uuid|tag|commit|sha256|sha1|md5|release)$")
_PIN_URL = re.compile(r"stable-virtio|archive-virtio|archive-qemu-ga|/releases/(tag|download)/|"  # pin-gate:allow
                      r"virtio-win-\d|qemu-ga-win-\d|/artifact/B\d|winfsp-\d", re.I)

_HEX = {"sha256": 64, "sha1": 40, "md5": 32}


class ResolveError(RuntimeError):
    """A resolver could not produce a verified latest version: the build must abort."""


# ---------------------------------------------------------------------------
# HTTP (injectable for tests; every network call in this module goes through it)
# ---------------------------------------------------------------------------

class Response:
    def __init__(self, status: int, headers: Dict[str, str], body: bytes = b""):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ResolveError(f"invalid JSON from upstream: {e}") from None

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


ERROR_BODY_MAX = 64 * 1024


class Http:
    """Small metadata-only client: GET (bounded body), HEAD, and no-redirect GET for redirect-carried digests."""

    max_body = 8 * 1024 * 1024  # metadata only; never a payload download

    def request(self, url: str, method: str = "GET", follow: bool = True, headers: Optional[Dict[str, str]] = None,
                limit: Optional[int] = None) -> Response:
        """limit: read at most this many body bytes and stop (a ranged read of a file's leading metadata, e.g. an
        RPM header); without it a body larger than max_body is refused."""
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a: Any, **k: Any) -> None:
                return None

        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT,
                                                                   "Accept": "application/json, text/html, */*",
                                                                   **(headers or {})})
        try:
            with opener.open(req, timeout=60) as r:
                body = (r.read(limit) if limit else r.read(self.max_body + 1)) if method == "GET" else b""
                if not limit and len(body) > self.max_body:
                    raise ResolveError(f"{url}: response larger than {self.max_body} bytes (not metadata)")
                return Response(r.status, dict(r.headers), body)
        except urllib.error.HTTPError as e:
            try:   # bounded: an error body only names the upstream's reason (UUP Dump: {"response": {"error": ...}})
                body = e.read(ERROR_BODY_MAX) if method == "GET" else b""
            except OSError:
                body = b""
            return Response(e.code, dict(e.headers or {}), body)


# HTTP 429 (rate limited) has its own bounded budget: the upstream's Retry-After (capped) when it sends one, else this
# schedule. Build 20261023T224037Z attempt 2 lost its stage to api.uupdump.net answering get.php with 429 three times in
# 6 s (the 2 s/4 s transport backoff); the rate window is tens of seconds.
RATE_LIMIT_WAITS_S = (15, 30, 60, 120)
RATE_LIMIT_MAX_WAIT_S = 300

# The UUP Dump JSON API answers a failed live Windows Update request with HTTP 400 and {"response": {"error": ...}}
# (build 20261024T044408Z: fetchupd.php?ring=Dev gave 400 at 05:35:25Z and 200 a minute later). Its calls retry 400
# within their ``tries`` like a 5xx; every other upstream's 4xx still aborts at once.
UUP_API_RETRY = (400,)


def _upstream_error(r: Response) -> str:
    """The reason an upstream put in an error body (UUP Dump response.error, GitHub message), else ''."""
    try:
        d = json.loads(r.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    if not isinstance(d, dict):
        return ""
    e = (d.get("response") or {}).get("error") if isinstance(d.get("response"), dict) else None
    return re.sub(r"[^\w .:/-]", "", str(e or d.get("message") or ""))[:120]


def _retry_after(r: Response) -> Optional[float]:
    """Retry-After in delta-seconds (the HTTP-date form is not used by these upstreams: None, use the schedule)."""
    try:
        return max(0.0, float(str(r.headers.get("retry-after", "")).strip()))
    except ValueError:
        return None


def _fetch(http: Any, url: str, *, method: str = "GET", follow: bool = True, ok: Tuple[int, ...] = (200,),
           tries: int = 3, sleep: Callable[[float], None] = time.sleep, prefix: Optional[int] = None,
           retry: Tuple[int, ...] = ()) -> Response:
    """One upstream call with bounded backoff on 5xx/transport errors (``tries`` attempts) and, apart from that, on 429
    (RATE_LIMIT_WAITS_S, or the upstream's Retry-After capped at RATE_LIMIT_MAX_WAIT_S). Exhaustion raises ResolveError.
    prefix=N: a ranged GET of the first N bytes only (206, or 200 read up to N bytes). retry: 4xx statuses this
    upstream uses for transient failures (UUP_API_RETRY), retried within ``tries`` like a 5xx."""
    last = ""
    extra = {"headers": {"Range": f"bytes=0-{prefix - 1}"}, "limit": prefix} if prefix else {}
    attempt = limited = 0
    while True:
        try:
            r = http.request(url, method=method, follow=follow, **extra)
        except ResolveError:
            raise
        except Exception as e:  # noqa: BLE001 -- transport error: retry, then abort
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status in ok:
                return r
            why = _upstream_error(r)
            last = f"HTTP {r.status}" + (f" ({why})" if why else "")
            if r.status == 429:
                if limited >= len(RATE_LIMIT_WAITS_S):
                    break
                wait = _retry_after(r)
                sleep(min(RATE_LIMIT_WAITS_S[limited] if wait is None else wait, RATE_LIMIT_MAX_WAIT_S))
                limited += 1
                continue
            if r.status < 500 and r.status not in retry:
                break
        attempt += 1
        if attempt >= tries:
            break
        sleep(min(2 ** attempt, 30))
    raise ResolveError(f"{method} {_redact(url)} failed: {last}")


def _redact(url: str) -> str:
    """Drop query strings that may carry short-lived signed tokens (UUP CDN URLs) from messages."""
    p = urllib.parse.urlsplit(url)
    if "P4=" in (p.query or ""):
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "<signed>", ""))
    return url


def _hex(algo: str, value: Any, what: str) -> str:
    v = str(value or "").strip().lower()
    if not re.fullmatch(rf"[0-9a-f]{{{_HEX[algo]}}}", v):
        raise ResolveError(f"{what}: upstream {algo} is missing or malformed ({value!r}); refusing unverifiable input")
    return v


def _location(r: Response, what: str) -> str:
    loc = r.headers.get("location", "")
    if not loc:
        raise ResolveError(f"{what}: expected a redirect carrying the resolved artefact, got none")
    return loc


# ---------------------------------------------------------------------------
# SSOT
# ---------------------------------------------------------------------------

def _import_mios_toml() -> Any:
    here = os.path.dirname(os.path.abspath(__file__))
    for libdir in (os.environ.get("MIOS_LIB_DIR", ""), "/usr/lib/mios",
                   os.path.normpath(os.path.join(here, "..", "..", "..", "..", "lib", "mios"))):
        if libdir and os.path.isfile(os.path.join(libdir, "mios_toml.py")):
            sys.path.insert(0, libdir)
            try:
                import mios_toml  # type: ignore
                return mios_toml
            finally:
                sys.path.pop(0)
    raise ResolveError("usr/lib/mios/mios_toml.py not found (the layered SSOT loader is required)")


def _dig(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _lang_tag(locale: str) -> str:
    """SSOT locale (en_US.UTF-8) -> UUP Dump language id (en-us)."""
    m = re.fullmatch(r"([a-z]{2,3})[_-]([A-Za-z]{2,4})(?:\.[\w-]+)?(?:@\w+)?", str(locale or "").strip())
    if not m:
        raise ResolveError(f"SSOT locale {locale!r} does not map to a UUP language id")
    return f"{m.group(1)}-{m.group(2)}".lower()


def load_config(data: Dict[str, Any], profile: str = "mios-xbox") -> Dict[str, Any]:
    """Resolver inputs from the merged SSOT -- the [windows.xbox] spec tree (source, drivers, guest_tools,
    baked) plus the resolver endpoints in [windows.xbox.resolve]. Missing or pinned values raise ResolveError."""
    xb = _dig(data, "windows", "xbox") or {}
    src = xb.get("source") or {}
    resolve = xb.get("resolve") or {}
    ed = _dig(data, "editions", profile, "autounattend") or {}
    lang_ref = src.get("language_ref")
    lang = src.get("language") or (_dig(data, *str(lang_ref).split(".")) if lang_ref else None)
    winfsp_up = _dig(xb, "guest_tools", "winfsp", "upstream") or ""
    m = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?", str(winfsp_up))
    cfg: Dict[str, Any] = {
        "profile": profile,
        "uup_provider": src.get("provider"),
        "uup_channel": src.get("channel") or ed.get("uup_channel"),
        "uup_arch": src.get("arch") or ed.get("uup_arch"),
        "uup_edition": src.get("edition"),
        "uup_lang": _lang_tag(lang) if lang else None,
        "virtio_iso": _dig(xb, "drivers", "virtio_win", "upstream"),
        "qemu_ga_dir": _dig(xb, "guest_tools", "qemu_ga", "upstream"),
        "qemu_ga_msi": _dig(xb, "guest_tools", "qemu_ga", "package"),
        "virtio_gt_msi": _dig(xb, "guest_tools", "virtio_win_guest_tools", "file"),
        "virtio_bundle": _dig(xb, "guest_tools", "virtio_win_guest_tools", "bundle"),
        "winfsp_repo": m.group(1) if m else None,
        "winfsp_asset": _dig(xb, "guest_tools", "winfsp", "asset"),
        "webview2_fwlink": _dig(xb, "baked", "webview2", "upstream"),
        "converter_upstream": _dig(xb, "build", "converter", "upstream"),
        "converter_subdir": _dig(xb, "build", "converter", "subdir"),
        "converter_channel": _dig(xb, "build", "converter", "channel"),
        "converter_entry": _dig(xb, "build", "converter", "entry"),
    }
    # capabilities baked OFFLINE (F4): capability -> FOD package of the same update, plus its FOD metadata
    caps = list(_dig(xb, "features", "capabilities_add") or [])
    fod = _dig(xb, "features", "capability_fod") or {}
    cfg["fod_packages"] = {c: str((fod.get(c) or {}).get("package") or "") for c in caps}
    cfg["fod_metadata"] = _dig(xb, "features", "fod_metadata") if caps else ""
    if caps and (not all(cfg["fod_packages"].values()) or not cfg["fod_metadata"]):
        raise ResolveError("SSOT is missing resolver inputs: windows.xbox.features.capability_fod.<capability>.package / "
                           "features.fod_metadata for every features.capabilities_add entry (capabilities are baked offline)")
    # inbox apps of the SAME update, integrated offline by the converter: the edition file list carries none of them
    # (round-1 boot of build-20261023T224037Z: no Store, no winget, no Xbox app in the image)
    apps = src.get("apps", [])
    if not isinstance(apps, list) or not all(isinstance(a, str) and APP_PFN_RE.fullmatch(a) for a in apps) \
            or len(set(a.lower() for a in apps)) != len(apps):
        raise ResolveError("windows.xbox.source.apps must be a list of distinct package family names (Name_publisherid)")
    cfg["app_packages"] = list(apps)
    cfg["app_frameworks"] = str(src.get("app_frameworks") or "") if apps else ""
    if apps:
        try:
            re.compile(cfg["app_frameworks"])
        except re.error as e:
            raise ResolveError(f"windows.xbox.source.app_frameworks is not a regular expression ({e})") from None
        if not cfg["app_frameworks"]:
            raise ResolveError("SSOT is missing resolver inputs: windows.xbox.source.app_frameworks (the apps' framework packages)")
    for k in REQUIRED_RESOLVE_KEYS:
        cfg[k] = resolve.get(k)
    missing = [k for k, v in cfg.items() if v is None or (v == "" and k not in ("fod_metadata", "app_frameworks"))]
    if missing:
        raise ResolveError("SSOT is missing resolver inputs: " + ", ".join(sorted(missing))
                           + " ([windows.xbox.source|drivers|guest_tools|baked] / [windows.xbox.resolve])")
    if cfg["uup_provider"] != "uupdump":
        raise ResolveError(f"windows.xbox.source.provider {cfg['uup_provider']!r} is not supported (uupdump)")
    pins: List[str] = []
    if str(src.get("build", "latest")).lower() != "latest":
        pins.append(f"windows.xbox.source.build={src.get('build')!r}")
    pins += [f"windows.xbox.resolve.{k}" for k in resolve if _PIN_KEY.search(k)]
    for k in ("virtio_iso", "qemu_ga_dir", "webview2_fwlink", *REQUIRED_RESOLVE_KEYS):
        v = cfg.get(k)
        if isinstance(v, str) and _PIN_URL.search(v):
            pins.append(f"{k}={v}")
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+?(?:\.git)?/?", str(cfg["converter_upstream"])):
        raise ResolveError(f"windows.xbox.build.converter.upstream {cfg['converter_upstream']!r} is not a GitHub repository URL")
    for path in (("drivers", "virtio_win"), ("guest_tools", "qemu_ga"), ("guest_tools", "winfsp"), ("build", "converter")):
        ch = _dig(xb, *path, "channel")
        if ch not in (None, "latest"):
            pins.append(f"windows.xbox.{'.'.join(path)}.channel={ch!r} (expected 'latest')")
    if "/latest-virtio/" not in str(cfg["virtio_iso"]):
        pins.append(f"windows.xbox.drivers.virtio_win.upstream={cfg['virtio_iso']} (must be the latest-virtio channel)")
    if "/latest-qemu-ga" not in str(cfg["qemu_ga_dir"]):
        pins.append(f"windows.xbox.guest_tools.qemu_ga.upstream={cfg['qemu_ga_dir']} (must be the latest-qemu-ga channel)")
    if pins:
        raise ResolveError("SSOT pins a version (XB-2 forbids pins; resolve latest at build time): " + "; ".join(pins))
    ch = str(cfg["uup_channel"]).strip().lower().replace(" ", "_").replace("-", "_")
    if ch not in RINGS:
        raise ResolveError(f"unknown source channel {cfg['uup_channel']!r}; expected one of {sorted(set(RINGS))}")
    cfg["uup_ring"] = RINGS[ch]
    if cfg["uup_arch"] not in ("amd64", "arm64"):
        raise ResolveError(f"unsupported source arch {cfg['uup_arch']!r}")
    return cfg


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

def resolve_uup(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Newest build on the SSOT ring (live WU scan via fetchupd), its edition/lang, and the per-file SHA-256
    UUP Dump publishes. Each file's SHA-1 must equal Microsoft's FileDigest (base64) when present."""
    api = str(cfg["uupdump_api"]).rstrip("/")
    arch, ring = cfg["uup_arch"], cfg["uup_ring"]
    edition, lang = str(cfg["uup_edition"]).lower(), str(cfg["uup_lang"]).lower()
    q = urllib.parse.urlencode({"arch": arch, "ring": ring})
    upd = _fetch(http, f"{api}/fetchupd.php?{q}", tries=5, sleep=sleep, retry=UUP_API_RETRY).json().get("response") or {}
    if upd.get("error"):
        raise ResolveError(f"UUP fetchupd ({ring}/{arch}): {upd['error']}")
    cands = [u for u in (upd.get("updateArray") or [upd]) if u.get("updateId") and u.get("foundBuild")]
    cands = [u for u in cands if u.get("arch", arch) == arch and "server" not in str(u.get("updateTitle", "")).lower()]
    if not cands:
        raise ResolveError(f"UUP fetchupd returned no {ring}/{arch} client build")
    cands.sort(key=lambda u: tuple(int(x) for x in re.findall(r"\d+", str(u["foundBuild"]))[:2] or [0]), reverse=True)
    pick = cands[0]
    uuid, build = str(pick["updateId"]), str(pick["foundBuild"])
    if not re.fullmatch(r"[0-9a-f-]{36}", uuid):
        raise ResolveError(f"UUP returned a malformed update id {uuid!r}")

    langs = _fetch(http, f"{api}/listlangs.php?{urllib.parse.urlencode({'id': uuid})}", sleep=sleep, retry=UUP_API_RETRY).json()
    langs = [str(x).lower() for x in ((langs.get("response") or {}).get("langList") or [])]
    if lang not in langs:
        raise ResolveError(f"UUP build {build}: language {lang!r} not available ({len(langs)} langs)")
    eds = _fetch(http, f"{api}/listeditions.php?{urllib.parse.urlencode({'id': uuid, 'lang': lang})}", sleep=sleep,
                 retry=UUP_API_RETRY).json()
    eds = [str(x).lower() for x in ((eds.get("response") or {}).get("editionList") or [])]
    if edition not in eds:
        raise ResolveError(f"UUP build {build}: edition {edition!r} not available for {lang} ({', '.join(eds)})")

    got = _fetch(http, f"{api}/get.php?{urllib.parse.urlencode({'id': uuid, 'lang': lang, 'edition': edition})}",
                 tries=5, sleep=sleep, retry=UUP_API_RETRY).json().get("response") or {}
    if got.get("error") or not isinstance(got.get("files"), dict) or not got["files"]:
        raise ResolveError(f"UUP get.php for {build} {edition}/{lang} returned no file list ({got.get('error')})")
    files = [_uup_row(name, got["files"][name]) for name in sorted(got["files"])]
    listing = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    out = {
        "version": build, "uuid": uuid, "title": str(pick.get("updateTitle", "")),
        "ring": ring, "arch": arch, "edition": edition, "lang": lang,
        "source": f"{api}/get.php?id={uuid}&lang={lang}&edition={edition}",
        "files": files, "file_count": len(files),
        "integrity": {"kind": "sha256-per-file", "source": "uupdump get.php (sha256) cross-checked against "
                      "Microsoft FileDigest (sha1)", "files_digest": hashlib.sha256(listing).hexdigest()},
        "note": "download URLs are short-lived signed CDN links; the fetch step requests fresh ones and must "
                "match every file to this sha256 list",
    }
    full = _full_file_list(http, api, uuid, sleep) if cfg.get("fod_packages") or cfg.get("app_packages") else None
    if cfg.get("fod_packages"):
        fods = resolve_fod(cfg, http, api, uuid, arch, sleep=sleep, full=full)
        out["fod_files"] = fods
        out["fod_source"] = f"{api}/get.php?id={uuid}"
        out["integrity"]["fod_digest"] = hashlib.sha256(json.dumps(fods, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if cfg.get("app_packages"):
        apps = resolve_apps(cfg, full, uuid)
        out["app_files"] = apps
        out["app_source"] = f"{api}/get.php?id={uuid}"
        out["integrity"]["app_digest"] = hashlib.sha256(json.dumps(apps, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return out


def _full_file_list(http: Any, api: str, uuid: str, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """The update's FULL file list (every FOD, inbox app and framework, not just the edition's packages)."""
    got = _fetch(http, f"{api}/get.php?{urllib.parse.urlencode({'id': uuid, 'noLinks': 1})}", tries=5, sleep=sleep,
                 retry=UUP_API_RETRY).json().get("response") or {}
    allf = got.get("files")
    if got.get("error") or not isinstance(allf, dict) or not allf:
        raise ResolveError(f"UUP get.php (full list) for {uuid} returned no file list ({got.get('error')})")
    return allf


def _uup_row(name: str, f: Any) -> Dict[str, Any]:
    """One UUP file entry: sha256 + sha1 (+ Microsoft FileDigest cross-check when published) + size."""
    f = f or {}
    sha256 = _hex("sha256", f.get("sha256"), f"UUP file {name}")
    sha1 = _hex("sha1", f.get("sha1"), f"UUP file {name}")
    m = re.search(r"<FileDigest>([A-Za-z0-9+/=]+)</FileDigest>", str(f.get("debug") or ""))
    if m:
        try:
            ms_sha1 = base64.b64decode(m.group(1), validate=True).hex()
        except ValueError:
            raise ResolveError(f"UUP file {name}: Microsoft FileDigest is not base64") from None
        if ms_sha1 != sha1:
            raise ResolveError(f"UUP file {name}: published sha1 {sha1} != Microsoft FileDigest {ms_sha1} (tampered)")
    try:
        size = int(f.get("size"))
    except (TypeError, ValueError):
        raise ResolveError(f"UUP file {name}: missing size") from None
    return {"name": name, "sha256": sha256, "sha1": sha1, "size": size}


FOD_TOKEN = "31bf3856ad364e35"   # Microsoft's public key token in FOD package names


def fod_canonical(package: str, arch: str) -> str:
    """Microsoft's FOD repository file name (OpenSSH-Server-Package~31bf3856ad364e35~amd64~~.cab)."""
    return f"{package}~{FOD_TOKEN}~{arch}~~.cab"


def resolve_fod(cfg: Dict[str, Any], http: Any, api: str, uuid: str, arch: str,
                sleep: Callable[[float], None] = time.sleep, full: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The FOD cab of every SSOT capability (+ the FOD metadata) from the SAME update's full file list (the
    edition list carries only the preinstalled FODs, e.g. OpenSSH-Client). Each is recorded with the name UUP
    Dump serves it under and the Microsoft repository name the worker stages it as. Missing = abort."""
    allf = full if full is not None else _full_file_list(http, api, uuid, sleep)
    low = {n.lower(): n for n in allf}
    rows: List[Dict[str, Any]] = []
    for cap, pkg in sorted(cfg["fod_packages"].items()):
        canon = fod_canonical(pkg, arch)
        cands = [f"cabs_{pkg}-{arch}.cab", f"{pkg}-{arch}.cab", canon]
        name = next((low[c.lower()] for c in cands if c.lower() in low), None)
        if not name:
            raise ResolveError(f"UUP update {uuid} offers no FOD package {pkg} ({arch}) for capability {cap}")
        rows.append({**_uup_row(name, allf[name]), "capability": cap, "canonical": canon, "role": "package"})
    meta = str(cfg["fod_metadata"])
    if meta.lower() not in low:
        raise ResolveError(f"UUP update {uuid} offers no {meta}")
    rows.append({**_uup_row(low[meta.lower()], allf[low[meta.lower()]]), "canonical": meta, "role": "metadata"})
    return rows


# A package family name: <identity name>_<13-char publisher id> (Microsoft.WindowsStore_8wekyb3d8bbwe).
APP_PFN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*_[a-z0-9]{13}")
# UUP Dump serves an inbox app's files as IPA_<group>_<file> (the CompDB payload path ...\IPA\<group>\<file>); the
# converter's CompDB_App step expects exactly these names in its UUPs dir.
_IPA_RE = re.compile(r"IPA_([A-Za-z0-9.\-]+)_(.+)")


def resolve_apps(cfg: Dict[str, Any], allf: Dict[str, Any], uuid: str) -> List[Dict[str, Any]]:
    """Every file of each SSOT inbox app's IPA group (main bundle, stubs, resource packs) plus every framework package
    the update offers (cfg app_frameworks, matched on the name after IPA_<group>_), from the SAME update's full list.
    An app the update does not offer (or offers twice), or no framework at all, aborts the resolve."""
    rows: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for pfn in cfg["app_packages"]:
        main = re.compile(r"IPA_([A-Za-z0-9.\-]+)_" + re.escape(pfn) + r"\.(msixbundle|appxbundle|msix|appx)", re.I)
        groups = sorted({m.group(1) for n in allf if (m := main.fullmatch(n))})
        if len(groups) != 1:
            raise ResolveError(f"UUP update {uuid} offers {'no' if not groups else 'more than one'} inbox app {pfn} "
                               f"(IPA_<group>_{pfn}.msixbundle|appxbundle|msix|appx; windows.xbox.source.apps)")
        prefix = f"IPA_{groups[0]}_".lower()
        for name in sorted(allf):
            if name.lower().startswith(prefix) and name not in seen:
                seen[name] = {**_uup_row(name, allf[name]), "role": "app", "pfn": pfn, "group": groups[0]}
                rows.append(seen[name])
    fw = re.compile(cfg["app_frameworks"], re.I)
    frameworks = 0
    for name in sorted(allf):
        m = _IPA_RE.fullmatch(name)
        if not m or not fw.search(m.group(2)):
            continue
        frameworks += 1
        if name in seen:
            seen[name]["role"] = "framework"   # a framework file of a selected app's own group
            seen[name].pop("pfn", None)
        else:
            seen[name] = {**_uup_row(name, allf[name]), "role": "framework", "group": m.group(1)}
            rows.append(seen[name])
    if not frameworks:
        raise ResolveError(f"UUP update {uuid} offers no framework package matching windows.xbox.source.app_frameworks")
    for r in rows:
        if not re.fullmatch(r"[\w.\-~]+", r["name"]):
            raise ResolveError(f"UUP update {uuid}: unsafe inbox app file name {r['name']!r}")
    return rows


# ---------------------------------------------------------------------------
# RPM header (the virtio-win release metadata that carries a sha256 per file)
# ---------------------------------------------------------------------------

RPM_HEADER_MAX = 8 * 1024 * 1024   # lead + signature + main header of the virtio-win RPM (~1 MB); never the payload
_RPM_TAGS = {"name": 1000, "version": 1001, "release": 1002, "filesizes": 1028, "filemodes": 1030, "filedigests": 1035,
             "dirindexes": 1116, "basenames": 1117, "dirnames": 1118, "longfilesizes": 5008, "digestalgo": 5011}
_RPMSIGTAG_SHA256 = 273             # sha256 (hex) of the main header, in the signature header
_RPM_DIGEST_SHA256 = 8              # PGPHASHALGO_SHA256 (RPMTAG_FILEDIGESTALGO)


def _rpm_struct(data: bytes, off: int, what: str) -> Tuple[Dict[int, Tuple[int, int, int]], bytes, int]:
    """One RPM header structure at off: ({tag: (type, offset, count)}, data store, end offset)."""
    if data[off:off + 4] != b"\x8e\xad\xe8\x01":
        raise ResolveError(f"RPM {what} header magic missing (not an RPM, or truncated)")
    if off + 16 > len(data):
        raise ResolveError(f"RPM {what} header truncated")
    n, size = int.from_bytes(data[off + 8:off + 12], "big"), int.from_bytes(data[off + 12:off + 16], "big")
    start = off + 16 + 16 * n
    end = start + size
    if n > 200000 or end > len(data):
        raise ResolveError(f"RPM {what} header is larger than the {len(data)} bytes read")
    idx = {}
    for i in range(n):
        e = off + 16 + 16 * i
        tag, typ, o, cnt = (int.from_bytes(data[e + k:e + k + 4], "big") for k in (0, 4, 8, 12))
        idx[tag] = (typ, o, cnt)
    return idx, data[start:end], end


def _rpm_value(store: bytes, ent: Tuple[int, int, int]) -> Any:
    typ, o, cnt = ent
    if typ in (6, 9):
        return store[o:store.index(b"\0", o)].decode("utf-8", "replace")
    if typ == 8:
        out, p = [], o
        for _ in range(cnt):
            q = store.index(b"\0", p)
            out.append(store[p:q].decode("utf-8", "replace"))
            p = q + 1
        return out
    width = {3: 2, 4: 4, 5: 8}.get(typ)
    if width:
        return [int.from_bytes(store[o + width * i:o + width * (i + 1)], "big") for i in range(cnt)]
    if typ == 7:
        return store[o:o + cnt]
    raise ResolveError(f"RPM header: unsupported tag type {typ}")


def rpm_header_extent(data: bytes) -> int:
    """Bytes from the start of the file to the end of the main header (from the leading bytes alone)."""
    if data[:4] != b"\xed\xab\xee\xdb" or len(data) < 112:
        raise ResolveError("not an RPM (lead magic missing)")
    n, size = int.from_bytes(data[104:108], "big"), int.from_bytes(data[108:112], "big")
    send = 96 + 16 + 16 * n + size
    hstart = send + (-send % 8)
    if len(data) < hstart + 16:
        raise ResolveError("RPM signature header larger than the bytes read")
    n2, size2 = int.from_bytes(data[hstart + 8:hstart + 12], "big"), int.from_bytes(data[hstart + 12:hstart + 16], "big")
    end = hstart + 16 + 16 * n2 + size2
    if end > RPM_HEADER_MAX:
        raise ResolveError(f"RPM header of {end} bytes exceeds {RPM_HEADER_MAX} (not release metadata)")
    return end


def rpm_header(data: bytes) -> Dict[str, Any]:
    """Parse the lead, signature and main header of an RPM (a prefix of the file is enough). The signature header's
    sha256 of the main header must verify; the file digests must be sha256. Returns name/version/release and
    files {path: {sha256, size, regular}}. Raises ResolveError on any inconsistency."""
    if data[:4] != b"\xed\xab\xee\xdb":
        raise ResolveError("not an RPM (lead magic missing)")
    sig, sstore, send = _rpm_struct(data, 96, "signature")
    hstart = send + (-send % 8)
    hdr, store, hend = _rpm_struct(data, hstart, "main")
    if _RPMSIGTAG_SHA256 not in sig:
        raise ResolveError("RPM signature header carries no sha256 of the main header")
    want = str(_rpm_value(sstore, sig[_RPMSIGTAG_SHA256])).lower()
    if hashlib.sha256(data[hstart:hend]).hexdigest() != want:
        raise ResolveError("RPM main header does not match the sha256 in its signature header (tampered or truncated)")
    def tag(name: str, default: Any = None) -> Any:
        t = _RPM_TAGS[name]
        return _rpm_value(store, hdr[t]) if t in hdr else default
    algo = (tag("digestalgo") or [1])[0]
    if algo != _RPM_DIGEST_SHA256:
        raise ResolveError(f"RPM file digests use hash algorithm {algo}, not sha256")
    names, dirs, didx = tag("basenames", []), tag("dirnames", []), tag("dirindexes", [])
    digests, modes = tag("filedigests", []), tag("filemodes", [])
    sizes = tag("longfilesizes") or tag("filesizes", [])
    if not (len(names) == len(didx) == len(digests) == len(sizes) == len(modes)):
        raise ResolveError("RPM header file arrays disagree in length")
    files = {}
    for i, b in enumerate(names):
        path = (dirs[didx[i]] + b).lstrip("/")
        regular = (modes[i] & 0o170000) == 0o100000
        files[path] = {"sha256": str(digests[i]).lower(), "size": int(sizes[i]), "regular": regular}
    return {"name": tag("name"), "version": tag("version"), "release": tag("release"), "header_sha256": want,
            "files": files, "header_end": hend}


def rpm_member(info: Dict[str, Any], basename: str, under: str = "") -> Tuple[str, Dict[str, Any]]:
    """The single regular file named basename (optionally below the directory `under`) of a parsed RPM header."""
    hits = [(p, f) for p, f in info["files"].items() if f["regular"] and os.path.basename(p) == basename
            and (not under or p.startswith(under.strip("/") + "/"))]
    if len(hits) != 1:
        raise ResolveError(f"RPM {info.get('name')}-{info.get('version')}: {len(hits)} regular files named {basename!r}"
                           + (f" under {under}" if under else "") + " (need exactly one)")
    p, f = hits[0]
    if not re.fullmatch(r"[0-9a-f]{64}", f["sha256"]):
        raise ResolveError(f"RPM {p}: no sha256 file digest")
    return p, f


def resolve_virtio(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """virtio-win from latest-virtio/: the redirect names the release directory; CHECKSUM there is the
    digest upstream publishes (md5 of the noarch RPM that carries the ISO and virtio-win-guest-tools.exe)."""
    iso = str(cfg["virtio_iso"])
    r = _fetch(http, iso, method="HEAD", follow=False, ok=(301, 302, 303, 307, 308), sleep=sleep)
    loc = urllib.parse.urljoin(iso, _location(r, "virtio-win latest-virtio"))
    m = re.search(r"/virtio-win-(\d+\.\d+\.\d+)-(\d+)/", loc)
    if not m:
        raise ResolveError(f"virtio-win latest-virtio redirect has no version: {loc}")
    ver, rel = m.group(1), m.group(2)
    rel_dir = loc[: loc.rfind("/") + 1].replace("http://", "https://", 1)
    ck = _fetch(http, rel_dir + "CHECKSUM", sleep=sleep).text()
    rpm = f"virtio-win-{ver}-{rel}.noarch.rpm"
    sums = {name: h for h, name in re.findall(r"^([0-9A-Fa-f]+)\s+\*?(\S+)\s*$", ck, re.M)}
    if rpm not in sums:
        raise ResolveError(f"virtio-win {ver}-{rel}: CHECKSUM does not list {rpm}")
    algo = {32: "md5", 40: "sha1", 64: "sha256"}.get(len(sums[rpm]))
    if not algo:
        raise ResolveError(f"virtio-win CHECKSUM entry for {rpm} is malformed")
    # the release metadata that carries a sha256 per file: the RPM header (FILEDIGESTS, sha256), read with a ranged
    # GET of the RPM's first bytes. Its own sha256 (signature header) must verify; fetch re-reads the header of the
    # downloaded, CHECKSUM-verified RPM and requires the same header, so these pins are anchored by CHECKSUM.
    head = _fetch(http, rel_dir + rpm, ok=(200, 206), sleep=sleep, prefix=1 << 20).body
    need = rpm_header_extent(head)
    if need > len(head):
        head = _fetch(http, rel_dir + rpm, ok=(200, 206), sleep=sleep, prefix=need).body
    info = rpm_header(head[:need])
    if (info["name"], f"{info['version']}-{info['release']}") != ("virtio-win", f"{ver}-{rel}"):
        raise ResolveError(f"virtio-win {rpm}: the RPM header names {info['name']}-{info['version']}-{info['release']}")
    want = {"iso": (f"virtio-win-{ver}.iso", ""),
            "guest_tools": (str(cfg["virtio_bundle"]), "usr/share/virtio-win/installer"),
            "gt_msi": (str(cfg["virtio_gt_msi"]), "usr/share/virtio-win/installer"),
            "qemu_ga": (str(cfg["qemu_ga_msi"]), "usr/share/virtio-win/guest-agent")}
    members, digests = {}, {}
    for role, (base, under) in want.items():
        path, f = rpm_member(info, base, under)
        members[role] = path
        digests[path] = {"sha256": f["sha256"], "size": f["size"]}
    return {
        "version": f"{ver}-{rel}", "source": rel_dir, "fetch": rel_dir + rpm,
        "members": members, "member_digests": digests, "rpm_header_sha256": info["header_sha256"],
        "integrity": {"kind": algo, "value": _hex(algo, sums[rpm], f"virtio-win {rpm}"), "of": rpm,
                      "source": rel_dir + "CHECKSUM",
                      "chain": "CHECKSUM (the only digest upstream publishes) -> RPM; RPM header (its signature-header "
                               "sha256 verified) -> member_digests (sha256 per file: ISO, guest-tools bundle, gt MSI, "
                               "qemu-ga MSI); the ISO -> driver tree; the bundle -> SPICE agent MSI (Burn manifest SHA-1). "
                               "The gt and qemu-ga MSIs and the bundle are NOT Authenticode-signed: sha256 is their anchor"},
    }


def resolve_qemu_ga(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep,
                    virtio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The latest-qemu-ga MSI. It is unsigned and upstream publishes no digest for it, so its anchor is the sha256 the
    RPM header of the same resolved virtio-win release records for guest-agent/<package> (never an Authenticode
    claim). A latest-qemu-ga MSI that release does not carry (size differs) aborts: it cannot be anchored."""
    virtio = virtio or resolve_virtio(cfg, http, sleep=sleep)
    d = str(cfg["qemu_ga_dir"]).rstrip("/") + "/"
    r = _fetch(http, d + str(cfg["qemu_ga_msi"]), method="HEAD", follow=False, ok=(301, 302, 303, 307, 308), sleep=sleep)
    loc = urllib.parse.urljoin(d, _location(r, "qemu-ga latest-qemu-ga"))
    m = re.search(r"/qemu-ga-win-([0-9][\w.\-]*?)/", loc)
    if not m:
        raise ResolveError(f"qemu-ga latest redirect has no version: {loc}")
    url = loc.replace("http://", "https://", 1)
    h = _fetch(http, url, method="HEAD", sleep=sleep)
    try:
        size = int(h.headers.get("content-length", ""))
    except ValueError:
        raise ResolveError("qemu-ga: upstream did not report a size") from None
    member = (virtio.get("members") or {}).get("qemu_ga")
    pin = (virtio.get("member_digests") or {}).get(member or "")
    if not pin:
        raise ResolveError(f"virtio-win {virtio.get('version')} records no sha256 for {cfg['qemu_ga_msi']}")
    if int(pin["size"]) != size:
        raise ResolveError(f"latest-qemu-ga {m.group(1)} ({size} bytes) is not the {cfg['qemu_ga_msi']} of virtio-win "
                           f"{virtio.get('version')} ({pin['size']} bytes): upstream publishes no digest for it, so it "
                           "cannot be anchored; the build waits for a virtio-win release that carries it")
    return {
        "version": m.group(1), "source": url, "fetch": url,
        "integrity": {"kind": "sha256", "value": _hex("sha256", pin["sha256"], f"qemu-ga {member}"), "size": size,
                      "source": f"virtio-win {virtio.get('version')} RPM header FILEDIGESTS of {member} "
                                f"(the RPM is anchored by {virtio['integrity']['source']})",
                      "signed": False},
    }


def _client_ref(path: str) -> Tuple[str, str]:
    """(commit, version) of the host's Looking Glass client build record (automation/69-bake-lookingglass-client.sh)."""
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError) as e:
        raise ResolveError(f"Looking Glass client build record {path} unreadable ({e}); the guest host app must match it") from None
    commit = str(rec.get("commit") or "").lower()
    version = str(rec.get("version") or "")
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit) and not re.fullmatch(r"B\d+(-rc\d+)?", version):
        raise ResolveError(f"Looking Glass client build record {path} has neither a commit nor a release tag")
    return commit, version


def _resolve_lg(cfg: Dict[str, Any], http: Any, kind: str, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """The looking-glass.io `kind` (host | idd) build of the client's commit (the protocol must match);
    SHA-256 from the artifact redirect; the installer inside is Authenticode-checked against lg_signer."""
    commit, version = _client_ref(str(cfg["lg_client_build_record"]))
    base = str(cfg["lg_downloads"])
    page = _fetch(http, base, sleep=sleep).text()
    if commit:
        ids = sorted(set(re.findall(rf"/artifact/(B\d+[\w.]*-\d+-([0-9a-f]{{7,40}}))/{kind}", page)))
        match = [aid for aid, h in ids if commit.startswith(h) or h.startswith(commit)]
        if not match:
            raise ResolveError(f"no Looking Glass {kind} build for client commit {commit[:12]} on {base}")
        aid = match[0]
    else:
        aid = version
        if f"/artifact/{aid}/{kind}" not in page:
            raise ResolveError(f"no Looking Glass {kind} release {aid} on {base}")
    url = urllib.parse.urljoin(base, f"/artifact/{aid}/{kind}")
    loc = _location(_fetch(http, url, method="HEAD", follow=False, ok=(301, 302, 303, 307, 308), sleep=sleep), url)
    m = re.search(rf"/artifact/file/{kind}/([0-9A-Fa-f]+)/", loc)
    if not m:
        raise ResolveError(f"{url}: redirect carries no SHA-256 ({loc!r})")
    return {
        "version": aid, "client_commit": commit, "client_version": version, "source": url, "fetch": url,
        "signer": str(cfg["lg_signer"]),
        "integrity": {"kind": "sha256", "value": _hex("sha256", m.group(1), f"Looking Glass {kind} {aid}"),
                      "source": "looking-glass.io artifact redirect"},
    }


def resolve_lg_host(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    return _resolve_lg(cfg, http, "host", sleep)


def resolve_lg_idd(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    return _resolve_lg(cfg, http, "idd", sleep)


def resolve_converter(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """UUP Dump's Windows converter: the newest commit of the upstream default branch. GitHub publishes no
    tarball digest, so the anchor is the git tree of that commit: every file under `subdir` is listed with
    its git blob SHA-1 and size, and the fetch step must reproduce exactly that set."""
    api = str(cfg["github_api"]).rstrip("/")
    m = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?", str(cfg["converter_upstream"]))
    if not m:
        raise ResolveError(f"converter upstream {cfg['converter_upstream']!r} is not a GitHub repository URL")
    repo, sub = m.group(1), str(cfg["converter_subdir"]).strip("/")
    branch = str(_fetch(http, f"{api}/repos/{repo}", sleep=sleep).json().get("default_branch") or "")
    if not branch:
        raise ResolveError(f"{repo}: no default branch")
    head = _fetch(http, f"{api}/repos/{repo}/commits/{urllib.parse.quote(branch)}", sleep=sleep).json()
    sha = str(head.get("sha") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ResolveError(f"{repo}@{branch}: malformed commit id")
    tree = _fetch(http, f"{api}/repos/{repo}/git/trees/{sha}?recursive=1", sleep=sleep).json()
    if tree.get("truncated"):
        raise ResolveError(f"{repo}@{sha[:12]}: git tree listing truncated; cannot anchor every file")
    files = []
    for e in tree.get("tree") or []:
        path = str(e.get("path", ""))
        if e.get("type") == "blob" and path.startswith(sub + "/"):
            files.append({"path": path[len(sub) + 1:], "git_sha1": _hex("sha1", e.get("sha"), f"{repo}:{path}"),
                          "size": int(e.get("size") or 0)})
    if not files or not any(f["path"] == str(cfg["converter_entry"]) for f in files):
        raise ResolveError(f"{repo}@{sha[:12]}: {sub}/ has no converter entry script")
    listing = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "version": f"{branch}@{sha[:12]}", "commit": sha, "repo": repo, "subdir": sub, "branch": branch,
        "source": f"https://github.com/{repo}/tree/{sha}/{sub}",
        "fetch": f"{api}/repos/{repo}/tarball/{sha}", "files": files,
        "integrity": {"kind": "git-tree", "files_digest": hashlib.sha256(listing).hexdigest(),
                      "source": "GitHub git tree of the resolved commit (blob SHA-1 per file)"},
    }


def resolve_winfsp(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    api = str(cfg["github_api"]).rstrip("/")
    repo = str(cfg["winfsp_repo"]).strip("/")
    rel = _fetch(http, f"{api}/repos/{repo}/releases/latest", sleep=sleep).json()
    if rel.get("draft") or rel.get("prerelease"):
        raise ResolveError("WinFsp releases/latest returned a draft/prerelease")
    tag = str(rel.get("tag_name") or "")
    glob = str(cfg["winfsp_asset"])
    assets = [a for a in rel.get("assets") or [] if fnmatch.fnmatchcase(str(a.get("name", "")), glob)]
    if not tag or len(assets) != 1:
        raise ResolveError(f"WinFsp latest release {tag!r}: expected exactly one {glob} asset, got {len(assets)}")
    a = assets[0]
    dg = str(a.get("digest") or "")
    if not dg.startswith("sha256:"):
        raise ResolveError(f"WinFsp {a['name']}: GitHub published no sha256 digest")
    return {
        "version": re.sub(r"^winfsp-|\.msi$", "", a["name"]), "tag": tag, "source": rel.get("html_url", ""),
        "fetch": str(a.get("browser_download_url")), "size": a.get("size"),
        "integrity": {"kind": "sha256", "value": _hex("sha256", dg[7:], f"WinFsp {a['name']}"),
                      "source": "GitHub release asset digest", "signer_expected": "authenticode (WinFsp)"},
    }


def resolve_webview2(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """WebView2 evergreen standalone (offline) installer: fwlink -> current CDN object; the runtime ships on the
    Edge Stable train, whose version the Edge enterprise API publishes. Microsoft publishes no digest for this
    installer: the anchor is its Authenticode signature plus the CDN-reported size."""
    fw = str(cfg["webview2_fwlink"])
    loc = _location(_fetch(http, fw, method="HEAD", follow=False, ok=(301, 302, 303, 307, 308), sleep=sleep), "WebView2 fwlink")
    p = urllib.parse.urlsplit(loc)
    if p.scheme != "https" or not (p.hostname or "").endswith(".microsoft.com") or "WebView2" not in p.path:
        raise ResolveError(f"WebView2 fwlink redirected off Microsoft's CDN or to a non-WebView2 object: {_redact(loc)}")
    h = _fetch(http, loc, method="HEAD", sleep=sleep)
    try:
        size = int(h.headers.get("content-length", ""))
    except ValueError:
        raise ResolveError("WebView2: CDN did not report a size") from None
    prods = _fetch(http, str(cfg["edge_products_api"]), sleep=sleep).json()
    arch = "x64" if cfg["uup_arch"] == "amd64" else "arm64"
    ver = ""
    for prod in prods if isinstance(prods, list) else []:
        if prod.get("Product") == "Stable":
            rels = [r for r in prod.get("Releases") or [] if r.get("Platform") == "Windows" and r.get("Architecture") == arch]
            rels.sort(key=lambda r: tuple(int(x) for x in re.findall(r"\d+", str(r.get("ProductVersion", "0")))), reverse=True)
            ver = str(rels[0].get("ProductVersion", "")) if rels else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ver):
        raise ResolveError("WebView2: Edge Stable version not resolvable from the enterprise products API")
    return {
        "version": ver, "source": fw, "fetch": urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", "")),
        "last_modified": h.headers.get("last-modified", ""), "etag": h.headers.get("etag", ""),
        "integrity": {"kind": "authenticode", "signer": str(cfg["webview2_signer"]), "size": size,
                      "source": "Microsoft publishes no digest for the evergreen standalone installer"},
    }


RESOLVERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "windows_uup": resolve_uup,
    "virtio_win": resolve_virtio,
    "qemu_ga": resolve_qemu_ga,
    "looking_glass_host": resolve_lg_host,
    "looking_glass_idd": resolve_lg_idd,
    "winfsp": resolve_winfsp,
    "webview2": resolve_webview2,
    "uup_converter": resolve_converter,
}


def resolve_all(cfg: Dict[str, Any], http: Any, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Every artefact or nothing: the first failure propagates (no partial manifest, no fallback)."""
    arts: Dict[str, Any] = {}
    for name, fn in RESOLVERS.items():
        try:
            # qemu-ga is anchored by the virtio-win release resolved just before it (same metadata, one header read)
            arts[name] = fn(cfg, http, sleep=sleep, virtio=arts["virtio_win"]) if name == "qemu_ga" else fn(cfg, http, sleep=sleep)
        except ResolveError as e:
            raise ResolveError(f"[{name}] {e}") from None
    return {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": cfg["profile"],
        "inputs": {k: cfg[k] for k in ("uup_channel", "uup_ring", "uup_arch", "uup_edition", "uup_lang")},
        "policy": "latest-at-build; no pins; resolver failure aborts; versions recorded only here",
        "artifacts": arts,
    }


# ---------------------------------------------------------------------------
# Output and verification
# ---------------------------------------------------------------------------

def _check_out(out: str) -> str:
    out = os.path.abspath(out)
    if os.path.basename(out) != MANIFEST_NAME:
        raise ResolveError(f"--out must name {MANIFEST_NAME} (the resolver writes nothing else)")
    real = os.path.realpath(os.path.dirname(out))
    for forbidden in ("/usr/share/mios", "/usr/lib/mios", "/etc/mios"):
        if real == forbidden or real.startswith(forbidden + "/"):
            raise ResolveError(f"--out {out} is inside the SSOT tree ({forbidden}); resolved versions belong in build output")
    return out


def write_manifest_atomic(path: str, manifest: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    fd, tmp = tempfile.mkstemp(prefix=".build-manifest.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
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


def _digest(path: str, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(path: str) -> str:
    """git's object id of a file (sha1 over 'blob <size>\\0' + content)."""
    h = hashlib.sha1()
    h.update(f"blob {os.path.getsize(path)}\0".encode())
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_tree(manifest: Dict[str, Any], artifact: str, root: str) -> int:
    """Every file of the resolved git tree is present under `root` with the recorded blob id, and nothing else."""
    art = (manifest.get("artifacts") or {}).get(artifact) or {}
    if (art.get("integrity") or {}).get("kind") != "git-tree":
        raise ResolveError(f"artifact {artifact!r} is not git-tree anchored")
    want = {f["path"]: f for f in art.get("files") or []}
    got = set()
    for d, _dirs, names in os.walk(root):
        for n in names:
            rel = os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/")
            got.add(rel)
            f = want.get(rel)
            if f is None:
                raise ResolveError(f"{artifact}: unexpected file {rel} (not in the resolved commit)")
            if git_blob_sha1(os.path.join(d, n)) != f["git_sha1"]:
                raise ResolveError(f"{artifact}: {rel} does not match the resolved commit's blob")
    missing = sorted(set(want) - got)
    if missing:
        raise ResolveError(f"{artifact}: {len(missing)} file(s) of the resolved commit missing (e.g. {missing[0]})")
    return len(want)


def _authenticode(path: str, signer: str) -> None:
    tool = os.environ.get("MIOS_OSSLSIGNCODE") or shutil.which("osslsigncode")
    if not tool:
        raise ResolveError("osslsigncode is required to verify the Authenticode signature (no unverified install)")
    r = subprocess.run([tool, "verify", "-in", path], capture_output=True, text=True, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or not re.search(r"Signature verification: ok", out):
        raise ResolveError(f"{os.path.basename(path)}: Authenticode signature does not verify")
    subjects = re.findall(r"Subject:\s*(.+)", out)
    if not any(signer.lower() in s.lower() for s in subjects):
        raise ResolveError(f"{os.path.basename(path)}: signed, but not by {signer!r}")


def verify_file(manifest: Dict[str, Any], artifact: str, path: str, member: Optional[str] = None) -> str:
    """Check a downloaded file against the manifest's integrity entry. Any mismatch raises ResolveError."""
    art = (manifest.get("artifacts") or {}).get(artifact)
    if not art:
        raise ResolveError(f"artifact {artifact!r} not in manifest")
    integ = art.get("integrity") or {}
    kind = integ.get("kind")
    name = os.path.basename(path)
    if kind == "sha256-per-file":
        want = {f["name"]: f for f in (art.get("files") or []) + (art.get("fod_files") or []) + (art.get("app_files") or [])}
        f = want.get(member or name)
        if not f:
            raise ResolveError(f"{member or name} is not part of the resolved UUP file set")
        if os.path.getsize(path) != int(f["size"]) or _digest(path, "sha256") != f["sha256"]:
            raise ResolveError(f"{name}: sha256/size mismatch against the resolved UUP file list")
        return "sha256"
    if kind in _HEX:
        if integ.get("size") is not None and os.path.getsize(path) != int(integ["size"]):
            raise ResolveError(f"{name}: size {os.path.getsize(path)} != resolved {integ['size']}")
        got = _digest(path, kind)
        if got != integ.get("value"):
            raise ResolveError(f"{name}: {kind} {got} != published {integ.get('value')}")
        return kind
    if kind == "authenticode":
        if integ.get("size") is not None and os.path.getsize(path) != int(integ["size"]):
            raise ResolveError(f"{name}: size {os.path.getsize(path)} != published {integ['size']}")
        _authenticode(path, str(integ.get("signer", "")))
        return "authenticode"
    raise ResolveError(f"artifact {artifact!r} has no usable integrity entry ({kind!r})")


def main(argv: Optional[List[str]] = None, http: Any = None, data: Optional[Dict[str, Any]] = None,
         sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(prog="resolve_latest.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="resolve latest upstream inputs into build-manifest.json")
    r.add_argument("--out", required=True)
    r.add_argument("--profile", default="mios-xbox")
    r.add_argument("--layers", nargs="+", help="explicit SSOT layer files (lowest first) for load_merged")
    r.add_argument("--replace", action="store_true", help="replace an existing manifest (it is removed on failure)")
    v = sub.add_parser("verify", help="verify a downloaded artefact against the manifest")
    v.add_argument("--manifest", required=True)
    v.add_argument("--artifact", required=True, choices=sorted(RESOLVERS))
    v.add_argument("--file", required=True)
    v.add_argument("--member", help="UUP file name when it differs from the local basename")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "verify":
            with open(a.manifest, encoding="utf-8") as fh:
                kind = verify_file(json.load(fh), a.artifact, a.file, a.member)
            print(f"[resolve_latest] OK {a.artifact} {os.path.basename(a.file)} ({kind})")
            return 0
        out = _check_out(a.out)
        if os.path.exists(out) and not a.replace:
            raise ResolveError(f"{out} exists; use a fresh build directory or --replace")
        try:
            if data is None:
                data = _import_mios_toml().load_merged(a.layers) if a.layers else _import_mios_toml().load_merged()
            cfg = load_config(data, a.profile)
            manifest = resolve_all(cfg, http or Http(), sleep=sleep)
        except BaseException:
            if os.path.exists(out):  # never leave a previous run's manifest behind as an implicit fallback
                os.unlink(out)
            raise
        write_manifest_atomic(out, manifest)
        for n, art in manifest["artifacts"].items():
            print(f"[resolve_latest] {n}: {art['version']} ({art['integrity']['kind']})")
        print(f"[resolve_latest] wrote {out}")
        return 0
    except ResolveError as e:
        print(f"[resolve_latest] ABORT: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
