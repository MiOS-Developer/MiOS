#!/usr/bin/env python3
# AI-hint: Proof that SearXNG has ONE port key and ONE listener: every default endpoint consumer projects the resolved [ports].searxng through the native SSOT mechanisms, layers move them all, missing settings fail by name, and planted regressions are caught.
# AI-doc: usr/share/doc/mios/manual/tests.md
"""SearXNG endpoint projection: one key, one listener, no fallbacks.

mios-searxng is a member of the host-network mios-webtools pod and granian
binds 127.0.0.1:<[ports].searxng> directly (GRANIAN_HOST/GRANIAN_PORT), so
there is no port mapping and no internal key. These controls hold that:

* the authoritative port semantics -- [ports.categories] derivation after the
  layer merge beats a flat [ports] value, and [ports].stack_id offsets it;
* every default consumer (the Quadlet listener and labels, the bake-rendered
  units, the install.env exports, the gateway agent, mios-web-search, the
  portal port injection, the Open WebUI applier, mios-show-image, mios-launch,
  mios-apps, the container-matrix firewall rule and the Windows bootstrap
  build-mios.ps1) carries the resolved value, and only host-context clients
  address it as localhost;
* layered overrides move every derived default, a non-empty higher-layer
  endpoint survives verbatim, and an empty one does not override;
* a missing setting fails by name instead of falling back;
* plants for the retired key, port fallbacks and literals, stale container
  hostnames and the false "host maps" comment are caught by the same scanner
  that passes the tree, repo-root files included.

Every resolver run here is hermetic: vendor/host/user layers point into a
temp dir, MIOS_RESOLVER_NATIVE=0 keeps the Python resolver on this tree, and no
live /etc layer is read. `--render-quadlets-bin=PATH` (or
MIOS_RENDER_QUADLETS_BIN) selects the bake renderer under test.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("MIOS_TOML_ROOT") or os.path.dirname(_HERE)
_TOML = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")
_LIB = os.path.join(_ROOT, "usr", "lib", "mios")
_PIPE = os.path.join(_LIB, "agent-pipe")
_LIBEXEC = os.path.join(_ROOT, "usr", "libexec", "mios")
_UNITS = os.path.join(_ROOT, "usr", "lib", "systemd", "system")
_QUADLETS = os.path.join(_ROOT, "usr", "share", "containers", "systemd")
_GATEWAY = os.path.join(_LIB, "gateway-agent", "tool_registry.py")
_ISOLATE = os.path.join(_LIBEXEC, "mios-firewall-isolate")
_BUILD_MIOS = os.path.join(_ROOT, "build-mios.ps1")
_REQUIRE_TOOLS = os.environ.get("MIOS_DRIFT_REQUIRE_TOOLS") == "1"
_RENDER_BIN_ARG = ""

# This ruling keeps the live listener where it is; moving the port is a
# separate decision that updates this pin together with [ports].
DEFAULT_LISTENER = 8800
RESOLVED_NAMES = ("MIOS_PORTS_SEARXNG", "MIOS_URLS_SEARXNG", "MIOS_SEARCH_ENDPOINT",
                  "MIOS_GATEWAY_SEARXNG_URL")
BAKED_UNITS = ("hermes-worker.service", "mios-firewall-ports.service")


def _vendor_text() -> str:
    with open(_TOML, encoding="utf-8") as fh:
        return fh.read()


def _vendor() -> dict:
    return tomllib.loads(_vendor_text())


def derived_searxng(data: dict, stack_id: int = 0) -> int:
    """[ports].searxng by the category rule, computed from the SSOT shape alone:
    base + index_in_members * stride, then stack_id * 10000."""
    owners = [cfg for cfg in data["ports"]["categories"].values()
              if "searxng" in (cfg.get("members") or [])]
    if len(owners) != 1:
        raise AssertionError(f"searxng must be a member of exactly one category, got {len(owners)}")
    cfg = owners[0]
    port = int(cfg["base"]) + cfg["members"].index("searxng") * int(cfg.get("stride", 1))
    return port + stack_id * 10000


def without_searxng(text: str) -> str:
    """The vendor SSOT with the SearXNG port gone: no member, no flat value."""
    members = 'members = ["searxng", "crawl4ai", "firecrawl"]'
    if text.count(members) != 1:
        raise AssertionError("webtools members line not found")
    text = text.replace(members, 'members = ["", "crawl4ai", "firecrawl"]')
    text, n = re.subn(r"^searxng\s*=\s*\d+.*\n", "", text, count=1, flags=re.M)
    if n != 1:
        raise AssertionError("flat [ports].searxng line not found")
    return text


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    if port > 65000:
        return free_port()
    return port


class Stub:
    """A loopback HTTP listener standing in for SearXNG; records every path."""

    def __init__(self, port: int):
        hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 -- stdlib hook name
                hits.append(self.path)
                body = json.dumps({"results": [{
                    "title": "t", "url": "http://example.invalid/r", "content": "c",
                    "img_src": "http://example.invalid/i.png"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.hits = hits
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()


class Stack:
    """A hermetic vendor < host < user layer stack in a temp dir."""

    def __init__(self, tmp: str, vendor: str | None = None, host: str = "", user: str = ""):
        self.tmp = tmp
        self.vendor = os.path.join(tmp, "vendor.toml")
        self.host = os.path.join(tmp, "host.toml")
        self.user = os.path.join(tmp, "user.toml")
        for path, text in ((self.vendor, _vendor_text() if vendor is None else vendor),
                           (self.host, host), (self.user, user)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        for d in ("vendor.d", "host.d", "user.d", "xdg", "home"):
            os.makedirs(os.path.join(tmp, d), exist_ok=True)

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "HOME": os.path.join(self.tmp, "home"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "MIOS_VENDOR_TOML": self.vendor, "MIOS_TOML": self.vendor,
            "MIOS_VENDOR_TOML_D": os.path.join(self.tmp, "vendor.d"),
            "MIOS_HOST_TOML": self.host, "MIOS_HOST_TOML_D": os.path.join(self.tmp, "host.d"),
            "MIOS_USER_TOML": self.user, "MIOS_USER_TOML_D": os.path.join(self.tmp, "user.d"),
            "XDG_CONFIG_HOME": os.path.join(self.tmp, "xdg"),
            "MIOS_RESOLVER_NATIVE": "0", "MIOS_DB_AUTHORITATIVE": "0",
            # mios-web-search reads these two tunables from its own fixed
            # layer list; supplying them keeps that list off the live /etc.
            "MIOS_PORTS_LLM_LIGHT": "1", "MIOS_WEB_ANCHOR_STOPWORDS": "the",
        }
        env.update(extra)
        return env

    def root(self, name: str) -> str:
        """A minimal tree whose vendor SSOT is this stack's vendor layer."""
        root = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(root, "usr", "share", "mios"), exist_ok=True)
        shutil.copyfile(self.vendor, os.path.join(root, "usr", "share", "mios", "mios.toml"))
        return root


def run(cmd: list[str], env: dict[str, str], timeout: int = 120, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, **kw)


def run_py(code: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return run([sys.executable, "-c", code], env)


def tool(name: str) -> str | None:
    found = shutil.which(name)
    return found if found and os.access(found, os.X_OK) else None


def render_quadlets_bin() -> str | None:
    """The bake renderer under test: an explicit choice, else a tree build,
    else the deployed one (automation/34-render-quadlets.sh's candidates)."""
    for cand in (_RENDER_BIN_ARG, os.environ.get("MIOS_RENDER_QUADLETS_BIN", ""),
                 os.path.join(_ROOT, "tools/native/target/release/mios-render-quadlets"),
                 os.path.join(_ROOT, "tools/native/target/debug/mios-render-quadlets"),
                 "/usr/libexec/mios/mios-render-quadlets"):
        if cand and os.access(cand, os.X_OK):
            return cand
    return tool("mios-render-quadlets")


# -- consumers ---------------------------------------------------------------

def resolved(stack: Stack, names=RESOLVED_NAMES, **extra) -> dict[str, str | None]:
    """mios_toml.resolved_export -- the install.env path every Python consumer uses."""
    code = (f"import json, sys\nsys.path.insert(0, {_LIB!r})\nimport mios_toml\nout = {{}}\n"
            f"for n in {list(names)!r}:\n"
            "    try:\n        out[n] = mios_toml.resolved_export(n)\n"
            "    except KeyError:\n        out[n] = None\nprint(json.dumps(out))\n")
    r = run_py(code, stack.env(**extra))
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def install_env(stack: Stack, **extra) -> str:
    """An install.env for the bash consumers, carrying what the resolver renders."""
    path = os.path.join(stack.tmp, "install.env")
    with open(path, "w", encoding="utf-8") as fh:
        for name, value in resolved(stack, **extra).items():
            if value is not None:
                fh.write(f"{name}={value}\n")
    return path


def gateway_url(stack: Stack, **extra) -> subprocess.CompletedProcess:
    code = ("import importlib.util, sys, types\n"
            "sys.modules['smolagents'] = types.SimpleNamespace(Tool=object)\n"
            f"spec = importlib.util.spec_from_file_location('tool_registry', {_GATEWAY!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "print(m.searxng_url())\n")
    return run_py(code, stack.env(**extra))


def web_search_url(stack: Stack, **extra) -> subprocess.CompletedProcess:
    path = os.path.join(_LIBEXEC, "mios-web-search")
    code = ("import importlib.machinery, importlib.util\n"
            f"loader = importlib.machinery.SourceFileLoader('mws', {path!r})\n"
            "spec = importlib.util.spec_from_loader('mws', loader)\n"
            "m = importlib.util.module_from_spec(spec); loader.exec_module(m)\n"
            "print(m.searxng_url())\n")
    return run_py(code, stack.env(**extra))


def web_search_cli(stack: Stack, **extra) -> subprocess.CompletedProcess:
    return run([sys.executable, os.path.join(_LIBEXEC, "mios-web-search"), "-n", "1",
                "--timeout", "5", "probe"], stack.env(**extra))


def portal_port(stack: Stack, **extra) -> subprocess.CompletedProcess:
    code = ("import asyncio, re, sys\n"
            f"sys.path.insert(0, {_PIPE!r}); sys.path.insert(0, {_LIB!r})\n"
            "from mios_pipe.routing import portal\n"
            "portal.PORTAL_REQUIRE_LOGIN = False\n"
            "class R:\n    cookies = {}\n    headers = {}\n"
            "resp = asyncio.run(portal.portal_page_logic(R()))\n"
            "m = re.search(r'var _MIOS_PORT_SEARXNG=([^;]*);', bytes(resp.body).decode())\n"
            "print(m.group(1) if m else 'ABSENT')\n")
    return run_py(code, stack.env(**extra))


def owui_query(stack: Stack, **extra) -> tuple[subprocess.CompletedProcess, str | None]:
    db = os.path.join(stack.tmp, "webui.db")
    if os.path.exists(db):
        os.unlink(db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE config (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO config (id, data) VALUES (1, '{}')")
    r = run([sys.executable, os.path.join(_LIBEXEC, "mios-owui-apply-websearch")],
            stack.env(MIOS_OWUI_DB=db, **extra))
    with sqlite3.connect(db) as conn:
        data = json.loads(conn.execute("SELECT data FROM config WHERE id = 1").fetchone()[0])
    url = data.get("rag", {}).get("web", {}).get("search", {}).get("searxng_query_url")
    return r, url


def show_image(stack: Stack, env_file: str) -> subprocess.CompletedProcess:
    stubs = os.path.join(stack.tmp, "stubs")
    os.makedirs(stubs, exist_ok=True)
    for name in ("mios-open-url", "mios-window"):
        path = os.path.join(stubs, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
    env = stack.env(MIOS_PATHS_INSTALL_ENV=env_file)
    env["PATH"] = stubs + os.pathsep + env["PATH"]
    return run(["bash", os.path.join(_LIBEXEC, "mios-show-image"), "probe"], env)


def isolate(stack: Stack, **extra) -> tuple[subprocess.CompletedProcess, dict | None]:
    """mios-firewall-isolate over this stack: the matrix from its vendor layer,
    every port_key through the same layered resolver."""
    r = run([sys.executable, _ISOLATE, "--toml", stack.vendor, "--check", "--json"], stack.env(**extra))
    return r, (json.loads(r.stdout) if r.returncode == 0 else None)


def matrix_dport(out: dict) -> list[int]:
    """The ports the rendered ruleset accepts for hermes -> searxng."""
    return [int(m) for m in re.findall(r'tcp dport (\d+) accept comment "hermes->searxng"', out["ruleset"])]


# build-mios.ps1's own SSOT reader and its LAN firewall step, lifted out by the
# PowerShell AST and run against a scratch SystemDrive whose vendor layer is
# this stack's (no GitHub fallback: the vendor file always exists). The
# Windows firewall cmdlets are recording stubs.
_PS_HARNESS = r"""
param([string]$Src)
$ErrorActionPreference = 'Stop'
$errs = $null; $toks = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Src, [ref]$toks, [ref]$errs)
if ($errs.Count) { throw ("build-mios.ps1 does not parse: " + $errs[0].Message) }
$want = 'Resolve-MiosTomlLayers', 'Get-MiosTomlValue', 'Get-MiosSsotPort', 'Set-MiosLanFirewallRules'
$fns = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $want -contains $n.Name }, $false))
if ($fns.Count -ne $want.Count) { throw ("expected " + ($want -join ', ') + "; found " + (($fns | ForEach-Object Name) -join ', ')) }
$script:_MiosTomlCache = @{}
foreach ($f in $fns) { . ([scriptblock]::Create($f.Extent.Text)) }
$script:rules = @(); $script:warnings = @()
function Write-Log { param($T, $L) }
function Log-Ok { param($T) }
function Log-Warn { param($T) $script:warnings += $T }
function Get-NetFirewallRule { param($DisplayName, $ErrorAction) $null }
function New-NetFirewallRule { param($DisplayName, $Description, $Direction, $Action, $Protocol, $LocalPort, $Profile, $ErrorAction)
    $script:rules += [pscustomobject]@{ Name = $DisplayName; Port = [int]$LocalPort } }
$port = Get-MiosSsotPort -Key 'searxng'
Set-MiosLanFirewallRules
[pscustomobject]@{ port = $port; rules = @($script:rules); warnings = @($script:warnings) } | ConvertTo-Json -Depth 4 -Compress
"""


def windows_bootstrap(test: unittest.TestCase, vendor: str) -> dict:
    pwsh = test.need(tool("pwsh"), "pwsh")
    tmp = tempfile.mkdtemp(prefix="mios-searxng-ps-")
    test.addCleanup(shutil.rmtree, tmp, True)
    sysdrive = os.path.join(tmp, "sys")
    os.makedirs(os.path.join(sysdrive, "usr", "share", "mios"))
    for d in ("home", "appdata"):
        os.makedirs(os.path.join(tmp, d))
    with open(os.path.join(sysdrive, "usr", "share", "mios", "mios.toml"), "w", encoding="utf-8") as fh:
        fh.write(vendor)
    script = os.path.join(tmp, "harness.ps1")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_PS_HARNESS)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "HOME": os.path.join(tmp, "home"), "USERPROFILE": os.path.join(tmp, "home"),
           "APPDATA": os.path.join(tmp, "appdata"), "SystemDrive": sysdrive,
           "XDG_CONFIG_HOME": os.path.join(tmp, "home"), "MIOS_AGREEMENT_BANNER": "quiet"}
    r = run([pwsh, "-NoProfile", "-NonInteractive", "-File", script, "-Src", _BUILD_MIOS], env, cwd=tmp)
    test.assertEqual(0, r.returncode, r.stdout + r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


def _snippet(path: str, start: str, end: str, exact_end: bool = False) -> str:
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    first = next(i for i, ln in enumerate(lines) if start in ln)
    last = next(i for i in range(first, len(lines))
                if (lines[i].strip() == end if exact_end else end in lines[i]))
    return "\n".join(lines[first:last + 1])


def launch_arm(env: dict[str, str]) -> subprocess.CompletedProcess:
    """mios-launch's searxng case arm, run as written (exec dropped for a stub)."""
    arm = _snippet(os.path.join(_LIBEXEC, "mios-launch"), "searxng|search)", ";;")
    arm = arm.replace("exec mios-open-url", "mios-open-url")
    script = ("set -euo pipefail\nmios-open-url() { printf 'OPEN %s\\n' \"$*\"; }\n"
              f"NAME=searxng\ncase \"$NAME\" in\n{arm}\nesac\n")
    return run(["bash", "-c", script], env)


def apps_row(env: dict[str, str]) -> subprocess.CompletedProcess:
    block = _snippet(os.path.join(_LIBEXEC, "mios-apps"), "# [urls].searxng from install.env", "fi",
                     exact_end=True)
    script = ("set -euo pipefail\nemit() { printf 'EMIT %s|%s|%s\\n' \"$1\" \"$2\" \"$4\"; }\n"
              f"{block}\n")
    return run(["bash", "-c", script], env)


def quadlet(stack: Stack, **extra) -> tuple[subprocess.CompletedProcess, str]:
    """The native Quadlet generator, rendering this stack into a scratch tree."""
    gen = tool("mios-gen")
    if gen is None:
        if _REQUIRE_TOOLS:
            raise AssertionError("mios-gen is required (MIOS_DRIFT_REQUIRE_TOOLS=1) but not installed")
        raise unittest.SkipTest("mios-gen is not installed")
    root = stack.root("qroot")
    out = os.path.join(root, "usr", "share", "containers", "systemd")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    r = run([gen, "pod-quadlets", "--root", root], stack.env(**extra))
    unit = os.path.join(out, "mios-searxng.container")
    text = open(unit, encoding="utf-8").read() if os.path.exists(unit) else ""
    return r, text


def baked(stack: Stack, binary: str, **extra) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    """The bake renderer over copies of the units it renders the SearXNG port into."""
    root = stack.root("broot")
    units = os.path.join(root, "usr", "lib", "systemd", "system")
    shutil.rmtree(units, ignore_errors=True)
    os.makedirs(units)
    marks = {}
    for name in BAKED_UNITS:
        shutil.copyfile(os.path.join(_UNITS, name), os.path.join(units, name))
        with open(os.path.join(_UNITS, name), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        marks[name] = [i for i, ln in enumerate(lines) if "${MIOS_PORTS_SEARXNG}" in ln]
    r = run([binary, "--root", root], stack.env(**extra))
    got = {}
    for name, idx in marks.items():
        with open(os.path.join(units, name), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        got[name] = "\n".join(lines[i] for i in idx)
    return r, got


def env_lines(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        for key in ("Environment=", "Label="):
            if line.startswith(key):
                name, _, value = line[len(key):].partition("=")
                out[key + name] = value
    return out


# -- the stale-endpoint scanner ----------------------------------------------

RETIRED_KEY = re.compile(r"searxng_internal", re.I)
_SEARX_NAMES = (r"MIOS_PORTS?_SEARXNG|MIOS_URLS_SEARXNG|MIOS_SEARXNG_(?:PORT|URL)|"
                r"MIOS_SEARCH_ENDPOINT|MIOS_GATEWAY_SEARXNG_URL")
FALLBACKS = (
    re.compile(r"\$\{(?:" + _SEARX_NAMES + r"):-[^}]"),
    re.compile(r"""\.get\(\s*["'](?:""" + _SEARX_NAMES + r""")["']\s*,"""),
    re.compile(r"""["']?(?:""" + _SEARX_NAMES + r""")["']?[^\n]*?\b\d{4,5}\b"""),
    re.compile(r"""Key\s*=\s*'searxng'\s*;\s*Default\s*="""),
)
STALE_HOST = re.compile(r"mios-searxng:\d|//mios-searxng\b")
BRIDGE_ROUTE = re.compile(r"host\.containers\.internal")
HOST_MAPS = re.compile(r"host maps\s*\d+\s*->\s*\d+", re.I)
STALE_PORT = re.compile(r"\b(?:8888|8899)\b")
# A SearXNG port written as a number: `searxng = N` in a port map, or a rule
# whose `port = N` sits on a SearXNG line. The one exemption is the flat
# [ports] table of the SSOT itself, the projection test_flat_value_is_the_
# category_projection holds to the derivation.
PORT_LITERAL = re.compile(r"""\b(?:searxng|port)["']?\s*=\s*\d{2,5}\b""")
FLAT_PROJECTION = ("usr/share/mios/mios.toml", "ports")
_TOML_HEADER = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(?:#.*)?$")
_SEARX_LINE = re.compile(r"searx", re.I)

SCAN_PREFIXES = (
    "usr/share/mios/mios.toml", "usr/lib/mios/", "usr/libexec/mios/", "usr/lib/systemd/",
    "usr/share/containers/", "usr/share/mios/hermes/", "usr/share/mios/searxng/",
    "usr/share/mios/configurator/", "usr/share/mios/owui/", "usr/share/mios/windows/",
    "usr/share/doc/mios/manual/ch25-local-search-engine-and-searxng.md",
    "etc/", "automation/", "tools/", "src/",
)
# Every tracked repo-root file is scanned too (build-mios.ps1, Get-MiOS.ps1,
# Containerfile, Justfile, ...), except these, which hold no consumer.
NOT_CONSUMERS = {
    "tasks.jsonl": "the task ledger: history that quotes retired endpoints on purpose; nothing reads one from it",
}
# Projections `mios-gen sync` rewrites from the SSOT; Law 8's regenerate-and-
# diff gates own them, and they lag a source change until the next sync.
REGENERATED = frozenset({
    "automation/lib/globals.sh", "automation/lib/globals.ps1",
    "automation/manifest.json", "tools/manifest.json",
})
_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test[_-][^/]*$|_test\.rs$")

# Shrink-only: consumers this ruling did not convert, each with the exact text
# that still reproduces, a follow-up id and the reason. A fixed entry fails as
# loudly as a new violation.
FOLLOW_UP = {
    ("usr/libexec/mios/Setup-MiOSLanPortProxy.ps1", "_MiosPort 'MIOS_PORTS_SEARXNG'"):
        ("searxng-fu-1", "Windows LAN portproxy: env -> literal helper shared by every forwarded port"),
    ("usr/libexec/mios/Heal-MiOSLocalhostForwarding.ps1", "Key = 'searxng'; Default = 8800"):
        ("searxng-fu-2", "Windows localhost forwarding: Get-MiosSsotValue default held by check_ps_port_fallback_ssot"),
    ("usr/share/mios/windows/mios-tailscale-serve.ps1",
     "Get-PortFromSsot 'MIOS_PORTS_SEARXNG' 'searxng' 8800"):
        ("searxng-fu-3", "Windows tailscale serve: env -> Get-MiosSsotValue -> literal, shared by every served port"),
}
_FOLLOW_UP_ID = re.compile(r"^searxng-fu-\d+$")


def scan_text(path: str, text: str) -> list[tuple[str, int, str, str]]:
    """(path, line, rule, text) for every stale SearXNG endpoint claim."""
    found = []
    section = ""
    toml = path.endswith(".toml")
    for n, line in enumerate(text.splitlines(), 1):
        if toml and (header := _TOML_HEADER.match(line)):
            section = header.group(1)
        rules = []
        if RETIRED_KEY.search(line):
            rules.append("retired-key")
        if any(p.search(line) for p in FALLBACKS):
            rules.append("port-fallback")
        if STALE_HOST.search(line):
            rules.append("stale-hostname")
        if BRIDGE_ROUTE.search(line) and (_SEARX_LINE.search(line) or "/search?q=" in line):
            rules.append("bridge-route")
        if _SEARX_LINE.search(line):
            if HOST_MAPS.search(line):
                rules.append("host-maps")
            if STALE_PORT.search(line):
                rules.append("retired-port")
            if PORT_LITERAL.search(line) and (path, section) != FLAT_PROJECTION:
                rules.append("port-literal")
        found.extend((path, n, rule, line.strip()) for rule in rules)
    return found


def scanned_paths(root: str) -> list[str]:
    r = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr.decode(errors="replace"))
    out = []
    for rel in r.stdout.decode().split("\0"):
        if not rel or rel in REGENERATED or _TEST_PATH.search(rel):
            continue
        if ("/" not in rel and rel not in NOT_CONSUMERS) or any(
                rel == p or (p.endswith("/") and rel.startswith(p)) for p in SCAN_PREFIXES):
            out.append(rel)
    return out


def scan_tree(root: str) -> list[tuple[str, int, str, str]]:
    found = []
    for rel in scanned_paths(root):
        path = os.path.join(root, rel)
        if not os.path.isfile(path) or os.path.getsize(path) > 4 * 1024 * 1024:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        found.extend(scan_text(rel, text))
    return found


def unregistered(found):
    return [f for f in found
            if not any(f[0] == path and needle in f[3] for (path, needle) in FOLLOW_UP)]


def retired_key_problems(data: dict) -> list[str]:
    """Every place the SSOT could re-introduce an internal SearXNG key."""
    ports = data.get("ports") or {}
    problems = [f"[ports].{k}" for k in ports if "searxng" in k and k != "searxng"]
    for cat, cfg in (ports.get("categories") or {}).items():
        for k in (cfg.get("pinned") or {}):
            if "searxng" in k:
                problems.append(f"[ports.categories.{cat}].pinned.{k}")
    for k in (data.get("urls") or {}).get("non_addressable") or []:
        if "searxng" in k:
            problems.append(f"[urls].non_addressable {k}")
    return problems


# -- tests ---------------------------------------------------------------------

class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mios-searxng-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def stack(self, name: str = "s", **kw) -> Stack:
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        return Stack(d, **kw)

    def need(self, binary: str | None, what: str) -> str:
        if binary:
            return binary
        if _REQUIRE_TOOLS:
            self.fail(f"{what} is required (MIOS_DRIFT_REQUIRE_TOOLS=1) but not installed")
        raise unittest.SkipTest(f"{what} is not installed")


class TestPortSemantics(Base):
    """The authoritative [ports] semantics every consumer must follow."""

    def test_flat_value_is_the_category_projection(self):
        data = _vendor()
        self.assertEqual(data["ports"]["searxng"], derived_searxng(data))
        self.assertEqual(DEFAULT_LISTENER, derived_searxng(data))

    def test_the_internal_key_is_retired(self):
        self.assertEqual([], retired_key_problems(_vendor()))
        exports = resolved(self.stack(), names=("MIOS_PORTS_SEARXNG_INTERNAL",
                                               "MIOS_PORT_SEARXNG_INTERNAL",
                                               "MIOS_SEARXNG_INTERNAL_PORT"))
        self.assertEqual({}, {k: v for k, v in exports.items() if v is not None})

    def test_defaults_resolve_to_the_derived_port(self):
        p = DEFAULT_LISTENER
        self.assertEqual({
            "MIOS_PORTS_SEARXNG": str(p),
            "MIOS_URLS_SEARXNG": f"http://localhost:{p}",
            "MIOS_SEARCH_ENDPOINT": f"http://localhost:{p}/",
            "MIOS_GATEWAY_SEARXNG_URL": f"http://localhost:{p}",
        }, resolved(self.stack()))

    def test_a_category_override_moves_the_port(self):
        got = resolved(self.stack(host="[ports.categories.webtools]\nbase = 18830\n"))
        self.assertEqual("18830", got["MIOS_PORTS_SEARXNG"])
        self.assertEqual("http://localhost:18830", got["MIOS_GATEWAY_SEARXNG_URL"])

    def test_a_flat_only_override_is_not_authoritative(self):
        got = resolved(self.stack(host="[ports]\nsearxng = 18831\n"))
        self.assertEqual(str(DEFAULT_LISTENER), got["MIOS_PORTS_SEARXNG"])

    def test_stack_id_offsets_every_endpoint(self):
        got = resolved(self.stack(host="[ports]\nstack_id = 1\n"))
        p = derived_searxng(_vendor(), stack_id=1)
        self.assertEqual(str(p), got["MIOS_PORTS_SEARXNG"])
        self.assertEqual(f"http://localhost:{p}/", got["MIOS_SEARCH_ENDPOINT"])

    def test_the_native_install_env_writer_agrees(self):
        resolver = self.need(tool("mios-resolver"), "mios-resolver")
        for host in ("", "[ports.categories.webtools]\nbase = 18830\n", "[ports]\nstack_id = 1\n"):
            with self.subTest(host=host):
                s = self.stack("n" + str(abs(hash(host))), host=host)
                r = run([resolver, "--root", s.root("rroot"), "--emit=install-env"], s.env())
                self.assertEqual(0, r.returncode, r.stderr)
                emitted = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
                for name, value in resolved(s).items():
                    self.assertEqual(value, emitted.get(name), name)


class TestNetworkContext(Base):
    """localhost is right only for host-context clients; each one is proven."""

    def test_the_listener_is_host_loopback(self):
        data = _vendor()
        self.assertEqual("host", data["pods"]["mios-webtools"]["network"])
        self.assertIn("mios-searxng", data["pods"]["mios-webtools"]["members"])
        ctr = data["containers"]["mios-searxng"]["Container"]
        self.assertEqual("mios-webtools.pod", ctr["Pod"])
        self.assertIn("GRANIAN_HOST=127.0.0.1", ctr["Environment"])
        self.assertNotIn("PublishPort", ctr)
        with open(os.path.join(_QUADLETS, "mios-webtools.pod"), encoding="utf-8") as fh:
            self.assertIn("\nNetwork=host\n", fh.read())

    def test_open_webui_shares_the_host_netns(self):
        data = _vendor()
        self.assertEqual("mios-ai.pod", data["containers"]["mios-open-webui"]["Container"]["Pod"])
        self.assertEqual("host", data["pods"]["mios-ai"]["network"])
        with open(os.path.join(_QUADLETS, "mios-ai.pod"), encoding="utf-8") as fh:
            self.assertIn("\nNetwork=host\n", fh.read())

    def test_host_services_have_no_private_netns(self):
        paths = [os.path.join(_UNITS, "hermes-worker.service")]
        dropins = os.path.join(_UNITS, "mios-gateway-agent.service.d")
        paths += [os.path.join(dropins, f) for f in sorted(os.listdir(dropins))]
        for path in paths:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for directive in ("PrivateNetwork=yes", "PrivateNetwork=true", "NetworkNamespacePath=",
                              "JoinsNamespaceOf=", "IPAddressDeny=any"):
                self.assertNotIn(directive, text, path)


class TestDefaultConsumers(Base):
    """Every default consumer carries the resolved port at defaults."""

    P = DEFAULT_LISTENER

    def test_committed_quadlet_listener_is_unchanged(self):
        with open(os.path.join(_QUADLETS, "mios-searxng.container"), encoding="utf-8") as fh:
            committed = fh.read()
        got = env_lines(committed)
        p = self.P
        self.assertEqual(f"{p}", got["Environment=GRANIAN_PORT"])
        self.assertEqual("127.0.0.1", got["Environment=GRANIAN_HOST"])
        self.assertEqual(f"127.0.0.1:{p}", got["Environment=SEARXNG_BIND_ADDRESS"])
        self.assertEqual(f"http://localhost:{p}/", got["Environment=SEARXNG_BASE_URL"])
        self.assertIn("Pod=mios-webtools.pod", committed)
        r, fresh = quadlet(self.stack())
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(committed, fresh, "the generated listener must not move")

    def test_quadlet_template_has_no_port_fallback(self):
        ctr = _vendor()["containers"]["mios-searxng"]["Container"]
        for entry in ctr["Environment"] + ctr["Label"]:
            self.assertNotIn("MIOS_PORTS_SEARXNG:-", entry)
        self.assertIn("GRANIAN_PORT=${MIOS_PORTS_SEARXNG}", ctr["Environment"])

    def test_bake_rendered_units(self):
        binary = self.need(render_quadlets_bin(), "mios-render-quadlets")
        r, got = baked(self.stack(), binary)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(f"Environment=SEARXNG_URL=http://127.0.0.1:{self.P}",
                         got["hermes-worker.service"])
        self.assertEqual(f'"{self.P}" \\', got["mios-firewall-ports.service"].strip())

    def test_unit_ssot_mirrors_have_no_fallback(self):
        units = _vendor()["units"]
        hermes = units["hermes-worker.service"]["Service"]["Environment"]
        self.assertIn("SEARXNG_URL=http://localhost:${MIOS_PORTS_SEARXNG}", hermes)
        fw = units["mios-firewall-ports.service"]["Service"]["ExecStart"]
        self.assertIn('"${MIOS_PORTS_SEARXNG}"', fw)
        self.assertNotIn("MIOS_PORTS_SEARXNG:-", fw)

    def test_gateway_agent(self):
        r = gateway_url(self.stack())
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(f"http://localhost:{self.P}", r.stdout.strip())

    def test_web_search(self):
        r = web_search_url(self.stack())
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(f"http://localhost:{self.P}", r.stdout.strip())

    def test_portal_injection(self):
        r = portal_port(self.stack())
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(str(self.P), r.stdout.strip().splitlines()[-1])

    def test_open_webui_applier(self):
        r, url = owui_query(self.stack())
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(f"http://localhost:{self.P}/search?q=<query>", url)

    def test_launchers_read_the_resolved_url(self):
        s = self.stack()
        url = resolved(s)["MIOS_URLS_SEARXNG"]
        r = launch_arm(s.env(MIOS_URLS_SEARXNG=url))
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual(f"OPEN http://localhost:{self.P}", r.stdout.strip())
        r = apps_row(s.env(MIOS_URLS_SEARXNG=url))
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn(f"mios-open-url http://localhost:{self.P}", r.stdout)

    def test_vendor_files_carry_no_endpoint_literal(self):
        with open(os.path.join(_ROOT, "usr/share/mios/searxng/settings.yml"), encoding="utf-8") as fh:
            settings = fh.read()
        server = settings.split("\nserver:\n", 1)[1].split("\n\n", 1)[0]
        for key in ("base_url:", "port:", "bind_address:"):
            self.assertNotRegex(server, rf"(?m)^\s+{key}", key)
        with open(os.path.join(_ROOT, "usr/share/mios/hermes/config.yaml"), encoding="utf-8") as fh:
            hermes = fh.read()
        web = hermes.split("  web_search:\n", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("base_url", web)

    def test_configurator_hints_mirror_the_ssot(self):
        data = _vendor()
        with open(os.path.join(_ROOT, "usr/share/mios/configurator/mios.html"), encoding="utf-8") as fh:
            html = fh.read()
        port = re.search(r'data-key="ports\.searxng"[^>]*placeholder="([^"]*)"', html).group(1)
        endpoint = re.search(r'data-key="search\.endpoint"[^>]*placeholder="([^"]*)"', html).group(1)
        self.assertEqual(str(data["ports"]["searxng"]), port)
        self.assertEqual(data["search"]["endpoint"], endpoint)

    def test_launcher_entry_names_the_key(self):
        entry = _vendor()["desktop"]["launchers"]["mios-svc-searxng"]
        self.assertEqual("searxng", entry["port_key"])
        self.assertNotRegex(entry["ai_hint"], r"\b\d{4,5}\b")

    def test_container_matrix_firewall(self):
        # The rule's destination is the host-netns listener above, so its port
        # is the listener's: the matrix names the key, never a number.
        rows = [r for r in _vendor()["network"]["firewall"]["container_matrix"]["rules"]
                if r.get("destination") == "searxng"]
        self.assertEqual([{"source": "hermes", "destination": "searxng", "port_key": "searxng",
                           "protocol": "tcp"}], rows)
        r, out = isolate(self.stack())
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([self.P], matrix_dport(out))
        self.assertEqual([self.P], [x["port"] for x in out["rules"] if x["destination"] == "searxng"])

    def test_windows_bootstrap(self):
        # build-mios.ps1's layered reader, and the Windows LAN rule it opens
        # for the Windows host's own localhost/LAN clients.
        got = windows_bootstrap(self, _vendor_text())
        self.assertEqual(self.P, got["port"])
        self.assertIn({"Name": f"MiOS - searxng ({self.P}/tcp)", "Port": self.P}, got["rules"])
        self.assertEqual([], [w for w in got["warnings"] if "searxng" in w])

    def test_dev_vm_overlay_adds_no_listener(self):
        with open(_BUILD_MIOS, encoding="utf-8") as fh:
            ps = fh.read()
        # One listener: no drop-in re-binds mios-searxng (a member of the
        # Network=host mios-webtools pod), and a stale one is removed.
        self.assertNotRegex(ps, r"GRANIAN_(?:HOST|PORT)=|BIND_ADDRESS=")
        loop = ps.split("for svc_pair in \\\n", 1)[1].split("; do\n", 1)[0]
        self.assertNotIn("mios-searxng", loop)
        self.assertIn("rm -f /etc/containers/systemd/mios-searxng.container.d/"
                      "10-mios-dev-host-network.conf", ps)
        # Hermes reaches SearXNG through SEARXNG_URL, not a local override.
        local = ps.split("<<'CFGLOCAL'\n", 1)[1].split("\nCFGLOCAL\n", 1)[0]
        self.assertNotRegex(local, r"(?m)^\s*(?:tools|web_search):")
        # Each port map reads the key through Get-MiosSsotPort, never a literal.
        self.assertEqual(3, ps.count("Get-MiosSsotPort -Key 'searxng'"))
        self.assertIn("$_port = Get-MiosSsotPort -Key $_w.Key", ps)


class TestLayeredOverrides(Base):
    """A layer moves every derived default; explicit endpoints survive."""

    def test_a_category_override_moves_every_consumer(self):
        p = free_port()
        s = self.stack(host=f"[ports.categories.webtools]\nbase = {p}\n")
        self.assertEqual(str(p), resolved(s)["MIOS_PORTS_SEARXNG"])
        r, unit = quadlet(s)
        self.assertEqual(0, r.returncode, r.stderr)
        got = env_lines(unit)
        self.assertEqual(str(p), got["Environment=GRANIAN_PORT"])
        self.assertEqual(f"127.0.0.1:{p}", got["Environment=SEARXNG_BIND_ADDRESS"])
        self.assertEqual(f"http://localhost:{p}/", got["Label=io.podman_desktop.openInBrowser"])
        self.assertEqual(f"http://localhost:{p}", gateway_url(s).stdout.strip())
        self.assertEqual(str(p), portal_port(s).stdout.strip().splitlines()[-1])
        self.assertEqual(f"http://localhost:{p}/search?q=<query>", owui_query(s)[1])
        url = resolved(s)["MIOS_URLS_SEARXNG"]
        self.assertEqual(f"OPEN http://localhost:{p}", launch_arm(s.env(MIOS_URLS_SEARXNG=url)).stdout.strip())
        with Stub(p) as stub:
            r = web_search_cli(s)
            self.assertEqual(0, r.returncode, r.stdout + r.stderr)
            self.assertTrue(json.loads(r.stdout)["success"], r.stdout)
            self.assertTrue(any(h.startswith("/search?") for h in stub.hits), stub.hits)
            hits = len(stub.hits)
            r = show_image(s, install_env(s))
            # The endpoint is what is under test: the verb queried the resolved
            # listener and named it. (Its image pick reads the JSON from a stdin
            # that `python3 - <<PY` has already replaced -- a separate defect.)
            self.assertIn(f"http://localhost:{p}", r.stdout + r.stderr)
            self.assertTrue(any(h.startswith("/search?q=probe") and "categories=images" in h
                                for h in stub.hits[hits:]), stub.hits)

    def test_the_bake_follows_categories_not_flat_values(self):
        binary = self.need(render_quadlets_bin(), "mios-render-quadlets")
        cases = (
            ("[ports.categories.webtools]\nbase = 18830\n", 18830),
            ("[ports]\nsearxng = 18831\n", DEFAULT_LISTENER),
            ("[ports]\nstack_id = 1\n", derived_searxng(_vendor(), stack_id=1)),
        )
        for i, (host, want) in enumerate(cases):
            with self.subTest(host=host):
                s = self.stack(f"b{i}", host=host)
                self.assertEqual(str(want), resolved(s)["MIOS_PORTS_SEARXNG"])
                r, got = baked(s, binary)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertEqual(f"Environment=SEARXNG_URL=http://127.0.0.1:{want}",
                                 got["hermes-worker.service"], binary)
                self.assertEqual(f'"{want}" \\', got["mios-firewall-ports.service"].strip(), binary)

    def test_the_firewall_matrix_follows_the_resolved_port(self):
        cases = (
            ("[ports.categories.webtools]\nbase = 18830\n", 18830),
            ("[ports]\nsearxng = 18831\n", DEFAULT_LISTENER),
            ("[ports]\nstack_id = 1\n", derived_searxng(_vendor(), stack_id=1)),
        )
        for i, (host, want) in enumerate(cases):
            with self.subTest(host=host):
                s = self.stack(f"f{i}", host=host)
                self.assertEqual(str(want), resolved(s)["MIOS_PORTS_SEARXNG"])
                r, out = isolate(s)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertEqual([want], matrix_dport(out))

    def test_explicit_endpoints_survive_verbatim(self):
        user = ('[gateway]\nsearxng_url = "http://search.example.internal:9443"\n'
                '[urls]\nsearxng = "http://search.example.internal:9444"\n'
                '[search]\nendpoint = "http://search.example.internal:9445/"\n')
        s = self.stack(user=user)
        got = resolved(s)
        self.assertEqual(str(DEFAULT_LISTENER), got["MIOS_PORTS_SEARXNG"])
        self.assertEqual("http://search.example.internal:9443", gateway_url(s).stdout.strip())
        self.assertEqual("http://search.example.internal:9444", web_search_url(s).stdout.strip())
        self.assertEqual("http://search.example.internal:9445/search?q=<query>", owui_query(s)[1])
        self.assertEqual("OPEN http://search.example.internal:9444",
                         launch_arm(s.env(MIOS_URLS_SEARXNG=got["MIOS_URLS_SEARXNG"])).stdout.strip())

    def test_an_empty_higher_layer_value_does_not_override(self):
        s = self.stack(user='[gateway]\nsearxng_url = ""\n[urls]\nsearxng = ""\n')
        self.assertEqual(f"http://localhost:{DEFAULT_LISTENER}", gateway_url(s).stdout.strip())
        self.assertEqual(f"http://localhost:{DEFAULT_LISTENER}", web_search_url(s).stdout.strip())

    def test_process_inputs_follow_the_layering_contract(self):
        s = self.stack()
        self.assertEqual("http://search.example.internal:9446",
                         web_search_url(s, MIOS_URLS_SEARXNG="http://search.example.internal:9446").stdout.strip())
        self.assertEqual(f"http://localhost:{DEFAULT_LISTENER}",
                         web_search_url(s, MIOS_URLS_SEARXNG="").stdout.strip())
        self.assertEqual("http://q.example.internal/search?q=<query>",
                         owui_query(s, MIOS_OWUI_SEARXNG_QUERY="http://q.example.internal/search?q=<query>")[1])
        self.assertEqual(f"http://localhost:{DEFAULT_LISTENER}/search?q=<query>",
                         owui_query(s, MIOS_OWUI_SEARXNG_QUERY="")[1])


class TestMissingSettings(Base):
    """With no SearXNG port in the SSOT, every consumer fails by name."""

    def setUp(self):
        super().setUp()
        self.s_vendor = without_searxng(_vendor_text())
        self.s = self.stack(vendor=self.s_vendor)

    def test_resolver(self):
        self.assertEqual({n: None for n in RESOLVED_NAMES}, resolved(self.s))

    def test_quadlet_generator(self):
        r, _ = quadlet(self.s)
        self.assertNotEqual(0, r.returncode)
        self.assertIn("MIOS_PORTS_SEARXNG", r.stdout + r.stderr)

    def test_bake_renderer(self):
        binary = self.need(render_quadlets_bin(), "mios-render-quadlets")
        r, _ = baked(self.s, binary)
        self.assertNotEqual(0, r.returncode)
        self.assertIn("MIOS_PORTS_SEARXNG", r.stderr)

    def test_python_consumers(self):
        for name, r in (("gateway", gateway_url(self.s)), ("web-search", web_search_url(self.s)),
                        ("portal", portal_port(self.s))):
            with self.subTest(consumer=name):
                self.assertNotEqual(0, r.returncode, r.stdout)
                self.assertRegex(r.stderr, r"MIOS_\w*SEARXNG\w* does not resolve")

    def test_web_search_cli_reports_instead_of_guessing(self):
        r = web_search_cli(self.s)
        self.assertEqual(1, r.returncode)
        out = json.loads(r.stdout)
        self.assertFalse(out["success"])
        self.assertIn("MIOS_URLS_SEARXNG does not resolve", out["error"])

    def test_open_webui_applier_leaves_the_db_alone(self):
        r, url = owui_query(self.s)
        self.assertEqual(1, r.returncode)
        self.assertIn("MIOS_SEARCH_ENDPOINT", r.stderr)
        self.assertIsNone(url)

    def test_bash_consumers(self):
        env = self.s.env()
        r = launch_arm(env)
        self.assertNotEqual(0, r.returncode)
        self.assertIn("MIOS_URLS_SEARXNG", r.stderr)
        self.assertEqual("", r.stdout)
        r = apps_row(env)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual("", r.stdout)
        self.assertIn("MIOS_URLS_SEARXNG is unresolved", r.stderr)
        r = show_image(self.s, install_env(self.s))
        self.assertEqual(78, r.returncode, r.stdout + r.stderr)
        self.assertIn("MIOS_URLS_SEARXNG is unresolved", r.stderr)

    def test_firewall_matrix(self):
        r, _ = isolate(self.s)
        self.assertEqual(78, r.returncode, r.stdout + r.stderr)
        self.assertEqual("", r.stdout)
        self.assertIn("rule hermes->searxng: port_key 'searxng'", r.stderr)
        self.assertIn("MIOS_PORTS_SEARXNG does not resolve", r.stderr)

    def test_windows_bootstrap(self):
        got = windows_bootstrap(self, self.s_vendor)
        self.assertIsNone(got["port"])
        self.assertEqual([], [x for x in got["rules"] if "searxng" in x["Name"]])
        self.assertTrue(any("[ports].searxng does not resolve" in w for w in got["warnings"]),
                        got["warnings"])


class TestScanner(Base):
    """The tree is clean; planted regressions are caught."""

    def test_the_tree_carries_no_stale_endpoint(self):
        found = scan_tree(_ROOT)
        self.assertEqual([], unregistered(found))
        for (path, needle), (fu, why) in FOLLOW_UP.items():
            with self.subTest(path=path):
                self.assertTrue(any(f[0] == path and needle in f[3] for f in found),
                                f"{path} no longer reproduces ({fu}: {why}) -- drop it from FOLLOW_UP")

    def test_the_scan_covers_every_consumer(self):
        paths = set(scanned_paths(_ROOT))
        for rel in ("usr/share/mios/mios.toml", "usr/lib/mios/gateway-agent/tool_registry.py",
                    "usr/libexec/mios/mios-web-search", "usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py",
                    "usr/libexec/mios/mios-owui-apply-websearch", "usr/libexec/mios/mios-show-image",
                    "usr/libexec/mios/mios-launch", "usr/libexec/mios/mios-apps",
                    "usr/lib/systemd/system/hermes-worker.service",
                    "usr/lib/systemd/system/mios-firewall-ports.service",
                    "usr/share/containers/systemd/mios-searxng.container",
                    "usr/share/mios/searxng/settings.yml", "usr/share/mios/hermes/config.yaml",
                    "automation/72-hermes-agent.sh", "src/mios-rs/miosd/src/drift/ports.rs",
                    "usr/libexec/mios/mios-firewall-isolate", "build-mios.ps1"):
            self.assertIn(rel, paths)

    def test_the_scan_covers_every_repo_root_file(self):
        r = subprocess.run(["git", "-C", _ROOT, "ls-files", "-z"], capture_output=True, timeout=60)
        self.assertEqual(0, r.returncode, r.stderr)
        top = {rel for rel in r.stdout.decode().split("\0") if rel and "/" not in rel}
        self.assertTrue(set(NOT_CONSUMERS) <= top, NOT_CONSUMERS)
        self.assertEqual(top - set(NOT_CONSUMERS), {p for p in scanned_paths(_ROOT) if "/" not in p})

    def plant(self, rel: str, old: str, new: str) -> set[str]:
        with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(1, text.count(old), (rel, old))
        before = {f[2] for f in scan_text(rel, text)}
        self.assertEqual(set(), before, rel)
        return {f[2] for f in scan_text(rel, text.replace(old, new))}

    def test_plant_the_retired_key(self):
        got = self.plant("usr/share/mios/mios.toml", "cockpit_link       = 8120",
                         "searxng_internal   = 8888\ncockpit_link       = 8120")
        self.assertIn("retired-key", got)
        data = _vendor()
        data["ports"]["searxng_internal"] = 8888
        data["ports"]["categories"]["webtools"].setdefault("pinned", {})["searxng_internal"] = 8888
        data["urls"]["non_addressable"].append("searxng_internal")
        self.assertEqual(3, len(retired_key_problems(data)))

    def test_plant_port_fallbacks(self):
        for rel, old, new in (
            ("usr/share/mios/mios.toml", '"GRANIAN_PORT=${MIOS_PORTS_SEARXNG}"',
             '"GRANIAN_PORT=${MIOS_PORTS_SEARXNG:-8800}"'),
            ("usr/lib/systemd/system/hermes-worker.service", ":${MIOS_PORTS_SEARXNG}\n",
             ":${MIOS_PORTS_SEARXNG:-8800}\n"),
            ("usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py",
             'int(mios_toml.resolved_export("MIOS_PORTS_SEARXNG"))',
             '_portal_port("MIOS_PORTS_SEARXNG", "searxng", 8800)'),
            ("usr/libexec/mios/mios-web-search", 'mios_toml.resolved_export("MIOS_URLS_SEARXNG")',
             'os.environ.get("MIOS_URLS_SEARXNG", "http://localhost:8800")'),
            ("usr/libexec/mios/mios-show-image", 'PROBE_LIST="${MIOS_URLS_SEARXNG%/}"',
             'PROBE_LIST="${MIOS_URLS_SEARXNG:-http://127.0.0.1:8800}"'),
        ):
            with self.subTest(rel=rel):
                self.assertIn("port-fallback", self.plant(rel, old, new))

    def test_plant_the_false_host_maps_comment(self):
        got = self.plant("usr/share/mios/mios.toml",
                         "searxng            = 8800     # local privacy-respecting metasearch",
                         "searxng            = 8800     # SearXNG (host maps 8800->8888)")
        self.assertIn("host-maps", got)
        self.assertEqual([], [f for f in scan_text("x", "cockpit_internal = 9090 # host maps 8110->9090")
                              if f[2] == "host-maps"])

    def test_plant_stale_hostnames_and_routes(self):
        for rel, old, new, rule in (
            ("usr/share/mios/mios.toml", 'searxng_url               = "http://localhost:${MIOS_PORTS_SEARXNG}"',
             'searxng_url               = "http://mios-searxng:8080"', "stale-hostname"),
            ("usr/share/mios/hermes/config.yaml", "    provider: searxng\n",
             "    provider: searxng\n    base_url: http://mios-searxng:8080\n", "stale-hostname"),
            ("usr/libexec/mios/mios-owui-apply-websearch", 'endpoint.rstrip("/") + "/search?q=<query>"',
             '"http://host.containers.internal:8800/search?q=<query>"', "bridge-route"),
            ("automation/72-hermes-agent.sh", "(the local self-hosted instance at SEARXNG_URL, port key searxng)",
             "(local self-hosted instance at mios-searxng:8888)", "stale-hostname"),
        ):
            with self.subTest(rel=rel):
                self.assertIn(rule, self.plant(rel, old, new))

    def test_plant_port_literals(self):
        for rel, old, new in (
            ("usr/share/mios/mios.toml", 'destination = "searxng", port_key = "searxng"',
             'destination = "searxng", port = 8801'),
            ("build-mios.ps1", "            ceph_dashboard   = 8444\n            cockpit          = 8090\n",
             "            ceph_dashboard   = 8444\n            searxng          = 8800\n"
             "            cockpit          = 8090\n"),
        ):
            with self.subTest(rel=rel):
                self.assertIn("port-literal", self.plant(rel, old, new))
        # Only the SSOT's own flat [ports] table is the projection.
        self.assertEqual([], scan_text("usr/share/mios/mios.toml", "[ports]\nsearxng = 8800\n"))
        for path, text in (("usr/share/mios/mios.toml", "[network]\nsearxng = 8800\n"),
                           ("other.toml", "[ports]\nsearxng = 8800\n")):
            with self.subTest(path=path, text=text):
                self.assertIn("port-literal", {f[2] for f in scan_text(path, text)})

    def test_plant_at_the_repo_root(self):
        planted = "    base_url: http://mios-searxng:${MIOS_PORTS_SEARXNG:-8888}\n"
        for rel, anchor in (("build-mios.ps1", "function Get-MiosSsotPort {\n"),
                            ("Get-MiOS.ps1", "function Get-MiosTomlValue {\n")):
            with self.subTest(rel=rel):
                got = self.plant(rel, anchor, planted + anchor)
                self.assertTrue({"stale-hostname", "port-fallback", "retired-port"} <= got, got)
                self.assertIn(rel, scanned_paths(_ROOT))

    def test_clean_forms_pass(self):
        for line in ('"GRANIAN_PORT=${MIOS_PORTS_SEARXNG}"', '"mios-searxng",',
                     '{ source = "hermes", destination = "searxng", port_key = "searxng", protocol = "tcp" },',
                     "$_searxngPort = Get-MiosSsotPort -Key 'searxng'",
                     'ContainerName=mios-searxng', '[ -n "${MIOS_URLS_SEARXNG:-}" ]',
                     'Environment=SEARXNG_URL=http://${MIOS_NETWORK_LOOPBACK:-127.0.0.1}:${MIOS_PORTS_SEARXNG}'):
            with self.subTest(line=line):
                self.assertEqual([], scan_text("x", line))

    def test_follow_up_entries_are_real_files(self):
        for path, _needle in FOLLOW_UP:
            self.assertTrue(os.path.isfile(os.path.join(_ROOT, path)), path)

    def test_follow_up_entries_carry_an_id(self):
        ids = [fu for fu, _why in FOLLOW_UP.values()]
        self.assertEqual(len(ids), len(set(ids)), ids)
        for fu, why in FOLLOW_UP.values():
            self.assertRegex(fu, _FOLLOW_UP_ID)
            self.assertTrue(why.strip(), fu)


def main() -> int:
    global _RENDER_BIN_ARG
    argv = []
    for arg in sys.argv[1:]:
        if arg.startswith("--render-quadlets-bin="):
            _RENDER_BIN_ARG = arg.split("=", 1)[1]
        else:
            argv.append(arg)
    prog = unittest.main(argv=[sys.argv[0], "-v", *argv], exit=False)
    return 0 if prog.result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
