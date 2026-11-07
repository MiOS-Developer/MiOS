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
  mios-apps, mios-lan-status, the container-matrix firewall rule, the Windows
  bootstrap build-mios.ps1 and the three Windows forwarding helpers) carries
  the resolved value, and only host-context clients address it as localhost;
* direct Windows LAN forwarding and tailnet serving skip that loopback-only
  listener and name it by key, and mios-lan-status lists it as such;
* layered overrides move every derived default, a non-empty higher-layer
  endpoint survives verbatim, and an empty one does not override;
* a missing setting fails by name instead of falling back;
* plants for the retired key, port fallbacks and literals, stale container
  hostnames, wildcard binds and the false "host maps" comment are caught by
  the same scanner that passes the tree, repo-root files and the whole manual
  included, with no exemption register.

Every resolver run here is hermetic: vendor/host/user layers point into a
temp dir, MIOS_RESOLVER_NATIVE=0 keeps the Python resolver on this tree, and no
live /etc layer is read. `--render-quadlets-bin=PATH` (or
MIOS_RENDER_QUADLETS_BIN) selects the bake renderer under test.

The three Windows forwarding helpers cannot run whole off Windows (they
self-elevate through WindowsPrincipal and drive netsh, the firewall and
tailscale.exe). Their port-map statements are lifted by the PowerShell AST, in
file order, into a file beside the helper's copy in a scratch tree shaped like
the repo, and dot-sourced there, so $PSScriptRoot and the helpers' own
mios-common.ps1 lookup resolve as shipped; only the Windows side effects are
recording stubs. The default-path assertions run them as shipped: no
preload, no injected environment. mios-common.ps1's reader ends its candidates with
/usr/share/mios/mios.toml, so where that exists the harness runs in a private
user+mount namespace with the directory hidden.

Their entry points also run whole, as shipped: each helper is invoked by its
own path in such a tree, with elevation answered by stub types for the
WindowsPrincipal / WindowsIdentity / WindowsBuiltInRole literals and every
Windows side effect (netsh, the firewall and service cmdlets, Start-Process,
tailscale.exe, wsl.exe) a recording stub, so parameter binding and the
StrictMode preamble are proven here and nothing on this host is touched.
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
_LAN_STATUS = os.path.join(_LIBEXEC, "mios-lan-status")
# The Windows forwarding helpers, by the repo path each one ships at.
WIN_HELPERS = {
    "setup": "usr/libexec/mios/Setup-MiOSLanPortProxy.ps1",
    "heal": "usr/libexec/mios/Heal-MiOSLocalhostForwarding.ps1",
    "tailscale": "usr/share/mios/windows/mios-tailscale-serve.ps1",
}
# Every Windows entry point the lane touched, by repo path, with the
# parameters it declares first as (name, type, default); [] declares none,
# None too many to pin here (its AST and the binder must still agree).
WIN_ENTRIES = {
    "setup": (WIN_HELPERS["setup"], [("AiNode", "SwitchParameter", None)]),
    "setup-shim": ("usr/share/mios/windows/Setup-MiOSLanPortProxy.ps1", []),
    "heal": (WIN_HELPERS["heal"], []),
    "tailscale": (WIN_HELPERS["tailscale"], [("Method", "String", "'tcp'"), ("DryRun", "SwitchParameter", None),
                                            ("Off", "SwitchParameter", None)]),
    "build-mios": ("build-mios.ps1", None),
}
_COMMON_PS1 = "installation/mios-common.ps1"
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


# build-mios.ps1's own SSOT reader, its LAN firewall and portproxy steps, the
# dev-VM firewalld port list and the Start Menu service shortcuts, lifted out
# by the PowerShell AST and run against a scratch SystemDrive whose vendor
# layer is this stack's (no GitHub fallback: the vendor file always exists).
# The Windows cmdlets, netsh and wsl.exe are recording stubs.
_PS_HARNESS = r"""
param([string]$Src)
$ErrorActionPreference = 'Stop'
$errs = $null; $toks = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Src, [ref]$toks, [ref]$errs)
if ($errs.Count) { throw ("build-mios.ps1 does not parse: " + $errs[0].Message) }
$want = 'Resolve-MiosTomlLayers', 'Get-MiosTomlValue', 'Get-MiosSsotPort', 'Set-MiosLanFirewallRules', 'Set-MiosLanPortProxy'
$fns = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $want -contains $n.Name }, $false))
if ($fns.Count -ne $want.Count) { throw ("expected " + ($want -join ', ') + "; found " + (($fns | ForEach-Object Name) -join ', ')) }
function Get-Run([string]$Anchor, [scriptblock]$First, [scriptblock]$Last) {
    $a = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq $Anchor }, $true)
    if (-not $a) { throw "no $Anchor in build-mios.ps1" }
    $st = @($a.Parent.Statements); $k = [array]::IndexOf($st, $a)
    $i = $k; while ($i -ge 0 -and -not (& $First $st[$i])) { $i-- }
    $j = $k; while ($j -lt $st.Count -and -not (& $Last $st[$j])) { $j++ }
    if ($i -lt 0 -or $j -ge $st.Count) { throw "no statement run around $Anchor" }
    ($st[$i..$j] | ForEach-Object { $_.Extent.Text }) -join "`n"
}
$fwRun = Get-Run '$_fwServicePorts' { param($s) $true } { param($s) $s.Extent.Text -match '^\$_fwPortsStr\s*=' }
$linkRun = Get-Run '$_webLinks' { param($s) $s.Extent.Text -match '^\$_defaultPorts\s*=' } {
    param($s) $s -is [System.Management.Automation.Language.ForEachStatementAst] -and $s.Condition.Extent.Text -eq '$_webLinks' }
$script:_MiosTomlCache = @{}
foreach ($f in $fns) { . ([scriptblock]::Create($f.Extent.Text)) }
$script:rules = @(); $script:removed = @(); $script:warnings = @(); $script:infos = @()
$script:proxy = @(); $script:links = @()
function Write-Log { param($T, $L) }
function Log-Ok { param($T) }
function Log-Info { param($T) $script:infos += $T }
function Log-Warn { param($T) $script:warnings += $T }
function Get-NetFirewallRule { param($DisplayName, $ErrorAction) $null }
function New-NetFirewallRule { param($DisplayName, $Description, $Direction, $Action, $Protocol, $LocalPort, $Profile, $ErrorAction)
    $script:rules += [pscustomobject]@{ Name = $DisplayName; Port = [int]$LocalPort } }
function Remove-NetFirewallRule { param($DisplayName, $ErrorAction) $script:removed += [string]$DisplayName }
function netsh {
    $a = @($args | ForEach-Object { [string]$_ })
    $op = if ($a -contains 'add') { 'add' } elseif ($a -contains 'delete') { 'delete' } else { '' }
    if ($op) { $script:proxy += ,@($op, [int](($a | Where-Object { $_ -like 'listenport=*' }) -replace '^listenport=', '')) }
    $global:LASTEXITCODE = 0
}
function wsl.exe { '2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0' }
function Get-Service { param($Name, $ErrorAction) [pscustomobject]@{ Status = 'Running' } }
function Set-Content { param($Path, $Value, $Encoding, [switch]$Force)
    $script:links += ,@([IO.Path]::GetFileNameWithoutExtension([string]$Path), [string](@($Value) -match '^URL=')[0]) }
$DevDistro = 'MiOS-DEV'
$port = Get-MiosSsotPort -Key 'searxng'
Set-MiosLanFirewallRules
Set-MiosLanPortProxy
$fwPorts = & { . ([scriptblock]::Create($fwRun)); $_fwPortsStr }
& { $servicesDir = 'services'; . ([scriptblock]::Create($linkRun)) }
[pscustomobject]@{ port = $port; rules = @($script:rules); removed = @($script:removed); warnings = @($script:warnings)
    infos = @($script:infos); proxy = @($script:proxy); fw_ports = [string]$fwPorts; links = @($script:links) } |
    ConvertTo-Json -Depth 5 -Compress
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
    out = json.loads(r.stdout.strip().splitlines()[-1])
    out["fw_ports"] = [int(p) for p in out["fw_ports"].split()]
    out["proxy"] = [tuple(x) for x in out["proxy"]]
    out["links"] = [tuple(x) for x in out["links"]]
    return out


# Lifts one forwarding helper's port-map statements, and what each says about
# SearXNG (see the module docstring), and records their Windows side effects
# and console lines. -Preload loads mios-common.ps1 first, as a caller that
# already dot-sourced it does; shipped, nothing does.
_PS_HELPERS = r"""
param([string]$Src, [string]$Mode, [string]$Preload)
$ErrorActionPreference = 'Stop'
$errs = $null; $toks = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Src, [ref]$toks, [ref]$errs)
if ($errs.Count) { throw ("$Src does not parse: " + $errs[0].Message) }
$top = @($ast.EndBlock.Statements)
function Index-Of([scriptblock]$Pred) {
    for ($i = 0; $i -lt $top.Count; $i++) { if (& $Pred $top[$i]) { return $i } }
    throw "statement not found in $Src"
}
$take = New-Object System.Collections.Generic.List[int]
switch ($Mode) {
    'setup' {
        $take.Add((Index-Of { param($s) $s.Extent.Text -match '^Set-StrictMode' }))
        $take.Add((Index-Of { param($s) $s -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $s.Name -eq '_MiosPort' }))
        $b = Index-Of { param($s) $s.Extent.Text -match '^\$portMap\s*=' }
        $take.Add($b)
        for ($i = $b + 1; $i -lt $top.Count; $i++) {
            # The portproxy and firewall loops; the closing display loop only prints.
            if (($top[$i] -is [System.Management.Automation.Language.ForEachStatementAst] -and $top[$i].Condition.Extent.Text -eq '$portMap' -and
                 $top[$i].Body.Extent.Text -match 'netsh|NetFirewallRule') -or $top[$i].Extent.Text -match '(?i)searxng') { $take.Add($i) }
        }
    }
    'heal' {
        $a = Index-Of { param($s) $s.Extent.Text -match 'mios-common\.ps1' }
        for ($i = $a; $i -lt $top.Count; $i++) { if ($top[$i].Extent.Text -notmatch 'ReadKey') { $take.Add($i) } }
    }
    'tailscale' {
        $a = Index-Of { param($s) $s.Extent.Text -match 'mios-common\.ps1' }
        $b = Index-Of { param($s) $s.Extent.Text -match '^\$SERVICES\s*=' }
        for ($i = $a; $i -le $b; $i++) { $take.Add($i) }
        $c = Index-Of { param($s) $s.Extent.Text -match '^\$served\s*=\s*@\(\)' }
        $take.Add($c)
        for ($i = $c + 1; $i -lt $top.Count; $i++) { if ($top[$i].Extent.Text -match '(?i)searxng') { $take.Add($i) } }
        $take.Add((Index-Of { param($s) $s -is [System.Management.Automation.Language.ForEachStatementAst] -and $s.Condition.Extent.Text -eq '$SERVICES' }))
    }
    default { throw "unknown mode $Mode" }
}
$lifted = Join-Path (Split-Path -Parent $Src) ("lifted-" + $Mode + ".ps1")
Set-Content -LiteralPath $lifted -Encoding ascii -Value (($take | Sort-Object -Unique | ForEach-Object { $top[$_].Extent.Text }) -join "`n")
$script:warnings = @(); $script:proxies = @(); $script:deleted = @(); $script:rules = @()
$script:probed = @(); $script:servedArgs = @(); $script:hosts = @()
function Write-Host { param([Parameter(Position = 0)]$Object, $ForegroundColor, [switch]$NoNewline) $script:hosts += [string]$Object }
function Write-Warning { param([string]$Message) $script:warnings += $Message }
function netsh {
    $a = @($args | ForEach-Object { [string]$_ })
    if ($a -contains 'add') { $script:proxies += [int](($a | Where-Object { $_ -like 'listenport=*' }) -replace '^listenport=', '') }
    if ($a -contains 'delete') { $script:deleted += [int](($a | Where-Object { $_ -like 'listenport=*' }) -replace '^listenport=', '') }
    $global:LASTEXITCODE = 0
}
function Get-NetFirewallRule { param($DisplayName, $ErrorAction) $null }
function New-NetFirewallRule { param($DisplayName, $Direction, $Action, $Protocol, $LocalPort, $Profile, $RemoteAddress)
    $script:rules += ,@($DisplayName, [int]$LocalPort) }
function Invoke-WebRequest { param($Uri, [switch]$UseBasicParsing, [switch]$SkipCertificateCheck, $TimeoutSec, $ErrorAction)
    $script:probed += [string]$Uri; [pscustomobject]@{ StatusCode = 200 } }
function Invoke-TsStub { $script:servedArgs += (@($args) -join ' '); $global:LASTEXITCODE = 0 }
function Test-Port([int]$p) { $true }
$ts = 'Invoke-TsStub'; $Method = 'tcp'; $DryRun = $false; $dns = 'node.example.invalid'
if ($Preload) { . $Preload }
. $lifted
$out = [ordered]@{ warnings = @($script:warnings); hosts = @($script:hosts)
    reader = [bool](Get-Command Get-MiosSsotValue -ErrorAction SilentlyContinue) }
switch ($Mode) {
    'setup' {
        $out.map = @($portMap | ForEach-Object { ,@($_.Name, [int]$_.Port) })
        $out.proxies = @($script:proxies); $out.rules = @($script:rules)
    }
    'heal' {
        $out.map = @($resolvedPorts.Keys | ForEach-Object { ,@([string]$_, [int]$resolvedPorts[$_]) })
        $out.deleted = @($script:deleted); $out.probed = @($script:probed)
    }
    'tailscale' {
        $out.map = @($SERVICES | ForEach-Object { ,@($_.name, [int]$_.port) })
        $out.served = @($script:servedArgs)
    }
}
$out | ConvertTo-Json -Depth 6 -Compress
"""


_NS_PROBE: list[subprocess.CompletedProcess] = []


def _hermetic(cmd: list[str], test: unittest.TestCase) -> list[str]:
    """mios-common.ps1's Get-MiosSsotValue ends its candidate list with
    /usr/share/mios/mios.toml, the live vendor file on a MiOS host. Where it
    exists the harness runs in a private user+mount namespace with that one
    directory hidden behind an empty tmpfs; nothing outside the process sees
    it, and no layer but the scratch tree's is read."""
    if not os.path.exists("/usr/share/mios"):
        return cmd
    unshare = test.need(tool("unshare"), "unshare (to hide the live /usr/share/mios from the harness)")
    if not _NS_PROBE:
        _NS_PROBE.append(run([unshare, "-rm", "--", "sh", "-c", "mount -t tmpfs none /usr/share/mios"],
                             {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}))
    probe = _NS_PROBE[0]
    if probe.returncode != 0:
        if _REQUIRE_TOOLS:
            test.fail("a private mount namespace is required to keep the live /usr/share/mios"
                      " out of the Windows-helper harness: " + probe.stderr)
        raise unittest.SkipTest("no private mount namespace: " + probe.stderr.strip())
    return [unshare, "-rm", "--", "sh", "-c",
            'mount -t tmpfs none /usr/share/mios && exec "$@"', "sh", *cmd]


def windows_helper(test: unittest.TestCase, mode: str, vendor: str | None = None,
                   user: str = "", common: bool = True, preload: bool = False,
                   common_text: str | None = None, **env_extra: str) -> dict:
    """A helper's lifted port map in a scratch repo tree: mios-common.ps1 at
    installation/ unless `common` is false, `vendor` and `user` as its vendor
    and Windows user layers, `env_extra` as the resolver's exports,
    `common_text` a stand-in for mios-common.ps1. The defaults are the
    shipped layout."""
    pwsh = test.need(tool("pwsh"), "pwsh")
    tmp = tempfile.mkdtemp(prefix="mios-searxng-win-")
    test.addCleanup(shutil.rmtree, tmp, True)
    tree = os.path.join(tmp, "tree")
    rel = WIN_HELPERS[mode]
    os.makedirs(os.path.join(tree, os.path.dirname(rel)))
    shutil.copyfile(os.path.join(_ROOT, rel), os.path.join(tree, rel))
    os.makedirs(os.path.join(tree, "usr", "share", "mios"), exist_ok=True)
    with open(os.path.join(tree, "usr", "share", "mios", "mios.toml"), "w", encoding="utf-8") as fh:
        fh.write(_vendor_text() if vendor is None else vendor)
    common_path = os.path.join(tree, _COMMON_PS1)
    if common:
        os.makedirs(os.path.dirname(common_path))
        shutil.copyfile(os.path.join(_ROOT, _COMMON_PS1), common_path)
        if common_text is not None:
            with open(common_path, "w", encoding="utf-8") as fh:
                fh.write(common_text)
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".config", "mios"))
    if user:
        with open(os.path.join(home, ".config", "mios", "mios.toml"), "w", encoding="utf-8") as fh:
            fh.write(user)
    script = os.path.join(tmp, "harness.ps1")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_PS_HELPERS)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "HOME": home,
           "USERPROFILE": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
           "MIOS_NO_COLOR": "1"}
    env.update(env_extra)
    cmd = [pwsh, "-NoProfile", "-NonInteractive", "-File", script, "-Src", os.path.join(tree, rel),
           "-Mode", mode, "-Preload", common_path if (common and preload) else ""]
    r = run(_hermetic(cmd, test), env, cwd=tmp)
    test.assertEqual(0, r.returncode, r.stdout + r.stderr)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    out["map"] = [tuple(x) for x in out["map"]]
    return out


# Each entry point's parse: its param block (offset and declared parameters),
# every call to a command named `param` (what a param() after any statement
# parses as), the top-level Set-StrictMode offsets, and the binder's view
# (Get-Command .Parameters, which reads the file and runs none of it).
_PS_ENTRY_AST = r"""
param([string]$Srcs)
$ErrorActionPreference = 'Stop'
$common = [System.Management.Automation.PSCmdlet]::CommonParameters + [System.Management.Automation.PSCmdlet]::OptionalCommonParameters
$out = [ordered]@{}
foreach ($src in @($Srcs | ConvertFrom-Json)) {
    $errs = $null; $toks = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$toks, [ref]$errs)
    $calls = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'param' }, $true))
    $strict = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Set-StrictMode' }, $false))
    $pb = $null
    if ($ast.ParamBlock) {
        $pb = [ordered]@{ offset = $ast.ParamBlock.Extent.StartOffset; params = @($ast.ParamBlock.Parameters | ForEach-Object {
            [ordered]@{ name = $_.Name.VariablePath.UserPath; type = $_.StaticType.Name
                        default = $(if ($_.DefaultValue) { $_.DefaultValue.Extent.Text } else { $null }) } }) }
    }
    $binder = @((Get-Command -Name $src).Parameters.Values | Where-Object { $common -notcontains $_.Name } |
        ForEach-Object { ,@($_.Name, $_.ParameterType.Name) })
    $out[$src] = [ordered]@{ errors = @($errs | ForEach-Object Message); param_block = $pb
        param_calls = @($calls | ForEach-Object { $_.Extent.StartLineNumber })
        strict = @($strict | ForEach-Object { $_.Extent.StartOffset }); binder = $binder }
}
$out | ConvertTo-Json -Depth 8 -Compress
"""


def windows_entry_ast(test: unittest.TestCase) -> dict[str, dict]:
    """The parse of every WIN_ENTRIES file, by entry name, in one pwsh run."""
    pwsh = test.need(tool("pwsh"), "pwsh")
    tmp = tempfile.mkdtemp(prefix="mios-searxng-ast-")
    test.addCleanup(shutil.rmtree, tmp, True)
    script = os.path.join(tmp, "ast.ps1")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_PS_ENTRY_AST)
    paths = {name: os.path.join(_ROOT, rel) for name, (rel, _want) in WIN_ENTRIES.items()}
    r = run([pwsh, "-NoProfile", "-NonInteractive", "-File", script, "-Srcs", json.dumps(list(paths.values()))],
            {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "HOME": tmp}, cwd=tmp)
    test.assertEqual(0, r.returncode, r.stdout + r.stderr)
    got = json.loads(r.stdout.strip().splitlines()[-1])
    return {name: got[path] for name, path in paths.items()}


# One helper's whole entry point, invoked by its own path with -Params bound
# by name. Elevation is answered by stub types: the helpers' WindowsPrincipal /
# WindowsIdentity / WindowsBuiltInRole literals resolve through these type
# accelerators, so IsInRole says what -Admin says, on any host. Start-Process,
# netsh, the firewall and service cmdlets, Get-NetIPAddress (a LAN address, as
# on a host with one) and Invoke-WebRequest are recording functions; the
# external tailscale.exe and wsl.exe are the PATH stubs below.
_PS_ENTRY = r"""
param([string]$Helper, [string]$Params, [string]$Admin)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
namespace MiosEntryStub {
    public enum Role { Administrator }
    public sealed class Identity { public static Identity GetCurrent() { return new Identity(); } }
    public sealed class Principal {
        public static bool Admin;
        public Principal(Identity identity) {}
        public bool IsInRole(Role role) { return Admin; }
    }
}
'@
[MiosEntryStub.Principal]::Admin = ($Admin -eq '1')
$acc = [psobject].Assembly.GetType('System.Management.Automation.TypeAccelerators')
$acc::Add('Security.Principal.WindowsPrincipal', [MiosEntryStub.Principal])
$acc::Add('Security.Principal.WindowsIdentity', [MiosEntryStub.Identity])
$acc::Add('Security.Principal.WindowsBuiltInRole', [MiosEntryStub.Role])
$global:calls = [System.Collections.Generic.List[string]]::new()
$global:hosts = [System.Collections.Generic.List[string]]::new()
$global:warnings = [System.Collections.Generic.List[string]]::new()
function global:Rec([string]$Name, [object[]]$A) {
    $global:calls.Add((@($Name) + @($A | ForEach-Object { if ($_ -is [array]) { $_ -join ',' } else { [string]$_ } })) -join ' ')
}
foreach ($n in 'Start-Process', 'Get-NetFirewallRule', 'New-NetFirewallRule', 'Set-NetFirewallRule', 'Remove-NetFirewallRule',
               'Get-NetFirewallPortFilter', 'Set-NetFirewallPortFilter', 'Set-Service', 'Start-Service') {
    Set-Item "function:global:$n" ([scriptblock]::Create("Rec '$n' `$args"))
}
function global:netsh { Rec 'netsh' $args; $global:LASTEXITCODE = 0 }
function global:Get-Service { Rec 'Get-Service' $args; [pscustomobject]@{ Status = 'Running' } }
function global:Get-NetIPAddress { Rec 'Get-NetIPAddress' $args; [pscustomobject]@{ IPAddress = '192.168.0.10'; InterfaceAlias = 'Ethernet' } }
function global:Invoke-WebRequest { Rec 'Invoke-WebRequest' $args; [pscustomobject]@{ StatusCode = 200 } }
function global:Write-Host { param([Parameter(Position = 0)]$Object, $ForegroundColor, [switch]$NoNewline) $global:hosts.Add([string]$Object) }
function global:Write-Warning { param([Parameter(Position = 0)][string]$Message) $global:warnings.Add($Message) }
$bind = @{}
foreach ($p in @(($Params | ConvertFrom-Json).PSObject.Properties)) { $bind[$p.Name] = $p.Value }
$err = ''; $line = 0; $file = ''
try { & $Helper @bind } catch { $err = [string]$_; $line = $_.InvocationInfo.ScriptLineNumber; $file = [string]$_.InvocationInfo.ScriptName }
[ordered]@{ calls = @($global:calls); hosts = @($global:hosts); warnings = @($global:warnings)
    err = $err; line = $line; file = $file } | ConvertTo-Json -Depth 4 -Compress
"""

# tailscale.exe and wsl.exe (and netsh / pwsh.exe, should a shadow ever be
# bypassed) log every call. wsl.exe reports only port 1 bound in the VM, so
# tailscale-serve's Test-Port turns every service down without one TCP probe
# of this host.
_ENTRY_STUB = r"""#!/bin/sh
n=${0##*/}
printf '%s %s\n' "$n" "$*" >> "$MIOS_ENTRY_LOG"
case "$n $*" in
  "tailscale.exe status --json") echo '{"Self":{"DNSName":"node.example.invalid."}}' ;;
  "tailscale.exe status") echo '100.64.0.1 node user linux -' ;;
  wsl.exe*) echo 'LISTEN 0 4096 0.0.0.0:1 0.0.0.0:*' ;;
esac
exit 0
"""
_ENTRY_TREE = (*WIN_HELPERS.values(), WIN_ENTRIES["setup-shim"][0], _COMMON_PS1)


def windows_entry(test: unittest.TestCase, rel: str, params: dict | None = None, admin: bool = False) -> dict:
    """`rel` run whole as shipped: invoked by its own path in a scratch tree
    shaped like the repo (every Windows helper, the Setup shim,
    installation/mios-common.ps1 and the vendor SSOT at their repo paths),
    `params` bound by name, elevation answering `admin`. Returns the recorded
    calls, console and warning lines, what the PATH stubs saw (`external`),
    and the error that ended the run, if any, with its file and line."""
    pwsh = test.need(tool("pwsh"), "pwsh")
    tmp = tempfile.mkdtemp(prefix="mios-searxng-entry-")
    test.addCleanup(shutil.rmtree, tmp, True)
    tree = os.path.join(tmp, "tree")
    for path in _ENTRY_TREE:
        os.makedirs(os.path.join(tree, os.path.dirname(path)), exist_ok=True)
        shutil.copyfile(os.path.join(_ROOT, path), os.path.join(tree, path))
    with open(os.path.join(tree, "usr", "share", "mios", "mios.toml"), "w", encoding="utf-8") as fh:
        fh.write(_vendor_text())
    stubs = os.path.join(tmp, "bin")
    os.makedirs(stubs)
    for name in ("tailscale.exe", "wsl.exe", "netsh", "netsh.exe", "pwsh.exe"):
        with open(os.path.join(stubs, name), "w", encoding="utf-8") as fh:
            fh.write(_ENTRY_STUB)
        os.chmod(os.path.join(stubs, name), 0o755)
    log = os.path.join(tmp, "external.log")
    open(log, "w", encoding="utf-8").close()
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, ".config", "mios"))
    script = os.path.join(tmp, "entry.ps1")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_PS_ENTRY)
    env = {"PATH": stubs + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "HOME": home, "USERPROFILE": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
           "MIOS_NO_COLOR": "1", "MIOS_ENTRY_LOG": log}
    helper = os.path.join(tree, rel)
    cmd = [pwsh, "-NoProfile", "-NonInteractive", "-File", script, "-Helper", helper,
           "-Params", json.dumps(params or {}), "-Admin", "1" if admin else "0"]
    r = run(_hermetic(cmd, test), env, cwd=tmp, stdin=subprocess.DEVNULL)
    test.assertEqual(0, r.returncode, r.stdout + r.stderr)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    with open(log, encoding="utf-8") as fh:
        out["external"] = fh.read().splitlines()
    out["helper"], out["tree"] = helper, tree
    return out


def lan_status(stack: Stack, env_file: str | None, listening: tuple[int, ...],
               setup: str | None = None, **extra: str) -> tuple[subprocess.CompletedProcess, dict[str, int]]:
    """mios-lan-status run as written, with ss/ip stubbed: `listening` are the
    loopback ports ss reports, `setup` the helper it names. Returns the LAN
    rows it prints, name -> port."""
    stubs = os.path.join(stack.tmp, "lan-stubs")
    os.makedirs(stubs, exist_ok=True)
    rows = "".join(f"LISTEN 0 4096 127.0.0.1:{p} 0.0.0.0:*\\n" for p in listening)
    for name, body in (("ss", f"printf 'State Recv-Q Send-Q Local Peer\\n{rows}'"),
                       ("ip", "echo '1.1.1.1 via 192.0.2.1 dev eth0 src 192.0.2.10 uid 1000'")):
        path = os.path.join(stubs, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\n{body}\n")
        os.chmod(path, 0o755)
    env = stack.env(MIOS_PATHS_INSTALL_ENV=env_file or os.path.join(stack.tmp, "absent-install.env"),
                    MIOS_LAN_SETUP_PS1=setup or os.path.join(stack.tmp, "absent.ps1"), **extra)
    env["PATH"] = stubs + os.pathsep + env["PATH"]
    r = run(["bash", _LAN_STATUS], env)
    got = {name: int(port) for port, name in
           re.findall(r"^\s+https?://<your-windows-LAN-ip>:(\d+)/\s+\((\S+)\)$", r.stdout, re.M)}
    return r, got


def lan_loopback(r: subprocess.CompletedProcess) -> list[int]:
    """The ports mios-lan-status lists SearXNG at, as loopback-only."""
    return [int(p) for p in re.findall(
        r"^\s+--\s+:(\d+)\s+searxng: loopback-only listener \(mios\.toml \[ports\]\.searxng\); "
        r"direct LAN forwarding is unsupported$", r.stdout, re.M)]


def unsupported_notes(got: dict) -> list[int | None]:
    """The SearXNG notices a helper printed: tailscale-serve's skip line (its
    port), or Setup's not-forwarded line (no port: Setup never needs it)."""
    out = []
    for line in got["hosts"]:
        m = re.match(r"^\s+skip\s+searxng\s+:(\d+)\s+\(loopback-only listener, mios\.toml "
                     r"\[ports\]\.searxng; tailnet serve unsupported\)$", line)
        if m:
            out.append(int(m.group(1)))
        elif re.match(r"^\s+\[-\] searxng: not forwarded -- direct LAN forwarding is unsupported "
                      r"\(loopback-only listener, mios\.toml \[ports\]\.searxng\)$", line):
            out.append(None)
    return out


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
# A SearXNG port as a number: `searxng = N`, `port = N` / `Port = N` on a
# SearXNG line, or a "N searxng" pair. Only the SSOT's own flat [ports] table
# is exempt (test_flat_value_is_the_category_projection holds it).
PORT_LITERAL = re.compile(r"""\b(?i:searxng|port)["']?\s*=\s*\d{2,5}\b|\b\d{2,5}\s+searxng\b""")
# The one listener is loopback; a SearXNG line claiming a wildcard (IPv4 or
# IPv6) or any non-loopback bind is a second listener, as the retired dev-VM
# drop-in (10-mios-dev-host-network.conf, BIND_ADDRESS=0.0.0.0:...) was. A
# line that only removes a stale listener or drop-in is cleanup, not a claim.
WILDCARD_BIND = re.compile(r"\b0\.0\.0\.0\b|\[::\]|(?:BIND_ADDRESS|GRANIAN_HOST)\s*=\s*"
                           r"(?![\"']?(?:127\.0\.0\.1\b|localhost\b|\$\{MIOS_NETWORK_LOOPBACK\b))")
RETIRED_DROPIN = re.compile(r"10-mios-dev-host-network\.conf|mios-searxng\.container\.d\b")
CLEANUP = re.compile(r"^(?:sudo\s+)?rm\s+-f\s+\S+$|^&\s*netsh\s+interface\s+portproxy\s+delete\s+v4tov4\s+"
                     r"listenaddress=0\.0\.0\.0\s+listenport=\$\w+(?:\s+2>&1\s*\|\s*Out-Null)?$")
FLAT_PROJECTION = ("usr/share/mios/mios.toml", "ports")
_TOML_HEADER = re.compile(r"^\s*\[\[?\s*([^\[\]]+?)\s*\]\]?\s*(?:#.*)?$")
_SEARX_LINE = re.compile(r"searx", re.I)

SCAN_PREFIXES = (
    "usr/share/mios/mios.toml", "usr/lib/mios/", "usr/libexec/mios/", "usr/lib/systemd/",
    "usr/share/containers/", "usr/share/mios/hermes/", "usr/share/mios/searxng/",
    "usr/share/mios/configurator/", "usr/share/mios/owui/", "usr/share/mios/windows/",
    "usr/share/doc/mios/manual/",
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

# Consumers a ruling did not convert, each as (path, exact text) -> (id,
# reason). Root's ruling D: an exemption cannot make D green, so this register
# stays EMPTY (test_the_follow_up_register_is_empty); searxng-fu-1..3, the
# three Windows forwarding helpers, are converted and held by behaviour. A new
# finding is fixed, never listed here.
FOLLOW_UP: dict[tuple[str, str], tuple[str, str]] = {}
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
            cleanup = bool(CLEANUP.match(line.strip()))
            if WILDCARD_BIND.search(line) and not cleanup:
                rules.append("wildcard-bind")
            if RETIRED_DROPIN.search(line) and not cleanup:
                rules.append("retired-drop-in")
        found.extend((path, n, rule, line.strip()) for rule in rules)
    if path.endswith(".md"):
        # Prose wraps: a paragraph naming SearXNG and the retired drop-in.
        lines, start = text.splitlines(), 0
        for n in range(len(lines) + 1):
            if n < len(lines) and lines[n].strip():
                continue
            para = lines[start:n]
            if _SEARX_LINE.search(" ".join(para)):
                for k, line in enumerate(para, start + 1):
                    hit = (path, k, "retired-drop-in", line.strip())
                    if RETIRED_DROPIN.search(line) and not CLEANUP.match(line.strip()) and hit not in found:
                        found.append(hit)
            start = n + 1
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
        # build-mios.ps1's layered reader. Direct LAN forwarding skips the
        # loopback-only listener, says so by key and removes what an earlier
        # run added; the dev-VM firewalld list and the Windows-local shortcut
        # carry the resolved port.
        p = self.P
        got = windows_bootstrap(self, _vendor_text())
        self.assertEqual(p, got["port"])
        self.assertEqual([], [x for x in got["rules"] if "searxng" in x["Name"] or x["Port"] == p])
        self.assertEqual([f"MiOS - searxng ({p}/tcp)"], got["removed"])
        self.assertNotIn(("add", p), got["proxy"])
        self.assertEqual(("delete", p), got["proxy"][0])
        self.assertTrue(any(op == "add" for op, _port in got["proxy"]), got["proxy"])
        for step in ("Firewall:", "portproxy:"):
            with self.subTest(step=step):
                notes = [t for t in got["infos"] if t.startswith(step) and "SearXNG" in t]
                self.assertEqual(1, len(notes), got["infos"])
                for part in ("direct LAN forwarding is unsupported", "loopback-only",
                             "[ports].searxng", f"port {p})"):
                    self.assertIn(part, notes[0])
        self.assertEqual([], [w for w in got["warnings"] if "searxng" in w])
        self.assertIn(p, got["fw_ports"])
        self.assertIn(("MiOS Search (SearXNG)", f"URL=http://localhost:{p}/"), got["links"])
        # No SearXNG line in build-mios.ps1 carries a port number.
        with open(_BUILD_MIOS, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if re.search(r"searx", ln, re.I)]
        self.assertEqual([], [ln for ln in lines if re.search(r"\b\d{4,5}\b", ln)])

    def test_windows_forwarding_helpers(self):
        # As shipped: installation/mios-common.ps1 at the root, no preload, no
        # injected environment. Heal and tailscale-serve load the reader for
        # SearXNG alone, in a child scope, so it does not leak.
        p = self.P
        setup, heal, serve = (windows_helper(self, m) for m in ("setup", "heal", "tailscale"))
        self.assertEqual((False, False), (heal["reader"], serve["reader"]))
        for mode, got in (("setup", setup), ("heal", heal), ("tailscale", serve)):
            with self.subTest(helper=mode):
                self.assertEqual([], got["warnings"])
        # Heal removes a stale 0.0.0.0:<searxng> portproxy and probes it.
        self.assertEqual(("searxng", p), heal["map"][-1])
        self.assertIn(p, heal["deleted"])
        self.assertIn(f"http://localhost:{p}/", heal["probed"])
        # Setup and tailscale-serve forward or serve none of it, and say so.
        for mode, got, note in (("setup", setup, None), ("tailscale", serve, p)):
            with self.subTest(helper=mode):
                self.assertEqual([], [x for x in got["map"] if x[0] == "searxng"])
                self.assertEqual([note], unsupported_notes(got))
        self.assertNotIn(p, setup["proxies"])
        self.assertEqual([], [x for x in setup["rules"] if "searxng" in x[0] or x[1] == p])
        self.assertEqual([], [x for x in serve["served"] if f"={p} " in x or x.endswith(f":{p}")])
        # A caller that preloaded the reader, or exported the port, changes nothing.
        for mode, kw in (("heal", {"preload": True}), ("tailscale", {"preload": True}),
                         ("setup", {"MIOS_PORTS_SEARXNG": str(p)})):
            with self.subTest(helper=mode, extra=sorted(kw)):
                again = windows_helper(self, mode, **kw)
                base = {"setup": setup, "heal": heal, "tailscale": serve}[mode]
                for key in ("map", "warnings", "hosts", "proxies", "deleted", "probed", "served"):
                    self.assertEqual(base.get(key), again.get(key), key)

    def test_a_reader_that_fails_to_load_changes_nothing_else(self):
        # Loading the reader for SearXNG cannot stop the rest of the helper.
        for mode in ("heal", "tailscale"):
            with self.subTest(helper=mode):
                base = windows_helper(self, mode)
                got = windows_helper(self, mode, common_text="throw 'mios-common.ps1 failed to load'\n")
                self.assertEqual([x for x in base["map"] if x[0] != "searxng"], got["map"])
                self.assertEqual([], unsupported_notes(got))
                self.assertTrue(any("[ports].searxng does not resolve" in w for w in got["warnings"]),
                                got["warnings"])
                self.assertFalse(got["reader"])

    def test_lan_status(self):
        s = self.stack()
        r, got = lan_status(s, install_env(s), (self.P, 8400))
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertNotIn("searxng", got, r.stdout)
        self.assertNotIn(self.P, got.values(), r.stdout)
        self.assertEqual([self.P], lan_loopback(r), r.stdout)
        self.assertEqual(8400, got.get("forge"), r.stdout)
        self.assertNotIn("searxng", r.stderr)

    def test_lan_status_matches_setup(self):
        # What mios-lan-status says the helper forwards is what Setup forwards.
        setup = windows_helper(self, "setup")
        ports = [port for _name, port in setup["map"]]
        s = self.stack()
        r, got = lan_status(s, install_env(s), (*ports, self.P), setup=os.path.join(_ROOT, WIN_HELPERS["setup"]))
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual(sorted(ports), sorted(got.values()), r.stdout)
        m = re.search(r"the helper forwards the (\d+) MiOS ports checked above, not searxng;", r.stdout)
        self.assertIsNotNone(m, r.stdout)
        self.assertEqual(len(ports), int(m.group(1)))
        self.assertEqual([self.P], lan_loopback(r), r.stdout)

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


class TestWindowsEntryPoints(Base):
    """Every Windows entry point the lane touched enters parameter binding as
    shipped. A param() that follows any statement is no param block but a
    call to a command named `param`: under Set-StrictMode its unset variable
    throws before a line of the helper runs (Setup's did, at line 8), and
    without StrictMode the call itself fails."""

    P = DEFAULT_LISTENER
    RELAUNCH = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]

    def relaunch(self, got: dict) -> list[str]:
        """The argv a helper that is not elevated hands its UAC re-launch;
        that re-launch is all it does."""
        self.assertEqual("", got["err"], got)
        self.assertEqual(1, len(got["calls"]), got["calls"])
        m = re.fullmatch(r"Start-Process -FilePath pwsh\.exe -ArgumentList (.+) -Verb RunAs", got["calls"][0])
        self.assertIsNotNone(m, got["calls"][0])
        self.assertEqual([], got["external"])
        return m.group(1).split(",")

    def ran_to_the_key_wait(self, got: dict) -> None:
        """Elevated, the body runs to its closing key wait, which a redirected
        console cannot serve; no earlier statement may fail."""
        with open(got["helper"], encoding="utf-8") as fh:
            waits = [i + 1 for i, ln in enumerate(fh.read().splitlines()) if "ReadKey" in ln]
        self.assertEqual(1, len(waits), got["helper"])
        self.assertEqual((got["helper"], waits[0]), (got["file"], got["line"]), got["err"])
        self.assertIn("ReadKey", got["err"])
        self.assertEqual([], [c for c in got["calls"] if c.startswith("Start-Process")])
        self.assertEqual([], got["external"])

    def test_param_blocks_come_first(self):
        parsed = windows_entry_ast(self)
        for name, (rel, want) in WIN_ENTRIES.items():
            with self.subTest(entry=rel):
                got = parsed[name]
                self.assertEqual([], got["errors"])
                self.assertEqual([], got["param_calls"], "a param() after a statement is a call, not a param block")
                pb = got["param_block"]
                if want == []:
                    self.assertIsNone(pb)
                    self.assertEqual([], got["binder"])
                    continue
                self.assertIsNotNone(pb)
                declared = [(p["name"], p["type"], p["default"]) for p in pb["params"]]
                self.assertTrue(declared)
                if want is not None:
                    self.assertEqual(want, declared)
                self.assertEqual(sorted((n, t) for n, t, _d in declared), sorted(tuple(x) for x in got["binder"]))
                self.assertEqual([], [s for s in got["strict"] if s < pb["offset"]])
        # Setup in particular declares -AiNode before it turns StrictMode on.
        setup = parsed["setup"]
        self.assertIsNotNone(setup["param_block"], "Setup has no param block: param() follows a statement")
        self.assertTrue(setup["strict"])
        self.assertLess(setup["param_block"]["offset"], min(setup["strict"]))

    def test_setup_binds_and_runs_as_shipped(self):
        rel = WIN_ENTRIES["setup"][0]
        # Not elevated, it re-launches itself, carrying -AiNode exactly when bound.
        for params in ({"AiNode": True}, {}):
            with self.subTest(elevated=False, **params):
                got = windows_entry(self, rel, params)
                self.assertEqual([*self.RELAUNCH, got["helper"], *(["-AiNode"] if params else [])],
                                 self.relaunch(got))
        # The shim hands -AiNode to the canonical helper it ships beside.
        got = windows_entry(self, WIN_ENTRIES["setup-shim"][0])
        self.assertEqual([*self.RELAUNCH, os.path.join(got["tree"], rel), "-AiNode"], self.relaunch(got))
        # Elevated, the whole body runs under StrictMode: every mapped port is
        # forwarded and opened, -AiNode adds the tailnet bridge, and SearXNG
        # is named, never forwarded.
        ports = [port for _name, port in windows_helper(self, "setup")["map"]]
        self.assertTrue(ports)
        self.assertNotIn(self.P, ports)
        for params in ({"AiNode": True}, {}):
            with self.subTest(elevated=True, **params):
                got = windows_entry(self, rel, params, admin=True)
                self.ran_to_the_key_wait(got)
                calls = "\n".join(got["calls"])
                self.assertEqual(ports, [int(p) for p in re.findall(
                    r"^netsh interface portproxy add v4tov4 listenaddress=0\.0\.0\.0 listenport=(\d+) ", calls, re.M)])
                self.assertEqual(ports, [int(p) for p in re.findall(
                    r"^New-NetFirewallRule -DisplayName MiOS - \S+ \((\d+)/tcp\) ", calls, re.M)])
                self.assertEqual(bool(params), "netsh interface portproxy add v4tov6 " in calls)
                self.assertEqual([None], unsupported_notes(got))
                self.assertEqual([], [c for c in got["calls"] if re.search(rf"searx|\b{self.P}\b", c, re.I)])
                self.assertEqual([], got["warnings"])

    def test_heal_and_tailscale_enter_as_shipped(self):
        rel = WIN_ENTRIES["heal"][0]
        got = windows_entry(self, rel)
        self.assertEqual([*self.RELAUNCH, got["helper"]], self.relaunch(got))
        # Elevated, Heal removes and probes the stale 0.0.0.0:<searxng> entry.
        got = windows_entry(self, rel, admin=True)
        self.ran_to_the_key_wait(got)
        calls = "\n".join(got["calls"])
        self.assertIn(self.P, [int(p) for p in re.findall(
            r"^netsh interface portproxy delete v4tov4 listenaddress=0\.0\.0\.0 listenport=(\d+)$", calls, re.M)])
        self.assertIn(f"Invoke-WebRequest -Uri http://localhost:{self.P}/ ", calls)
        self.assertEqual([], got["warnings"])
        # tailscale-serve: -DryRun binds and holds every change back; unbound,
        # the reset reaches tailscale.exe; a -Method outside its set never
        # gets past binding.
        rel = WIN_ENTRIES["tailscale"][0]
        dry = windows_entry(self, rel, {"DryRun": True})
        self.assertEqual("", dry["err"], dry)
        self.assertEqual([self.P], unsupported_notes(dry))
        self.assertEqual(["tailscale.exe serve status"],
                         [x for x in dry["external"] if x.startswith("tailscale.exe serve")])
        live = windows_entry(self, rel)
        self.assertEqual("", live["err"], live)
        self.assertIn("tailscale.exe serve reset", live["external"])
        bad = windows_entry(self, rel, {"Method": "bogus"})
        self.assertRegex(bad["err"], r"parameter 'Method'.*ValidateSet")
        self.assertEqual([], bad["external"])
        for got in (dry, live, bad):
            self.assertEqual([], got["calls"])
            self.assertEqual([], [x for x in got["external"] if "--bg" in x
                                  or not x.startswith(("tailscale.exe ", "wsl.exe "))])


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

    def test_the_windows_helpers_follow_the_layered_port(self):
        p = free_port()
        s = self.stack(host=f"[ports.categories.webtools]\nbase = {p}\n")
        want = int(resolved(s)["MIOS_PORTS_SEARXNG"])
        self.assertEqual(p, want)
        user = f"[ports]\nsearxng = {want}\n"
        # As shipped, a higher Windows layer carrying that projection moves
        # Heal and the port tailscale-serve names; the resolver's export, which
        # outranks the layers the reader sees, moves tailscale-serve too.
        for mode, kw in (("heal", {"user": user}), ("tailscale", {"user": user}),
                         ("tailscale", {"MIOS_PORTS_SEARXNG": str(want)}),
                         ("heal", {"user": user, "preload": True})):
            with self.subTest(helper=mode, source=sorted(kw)):
                got = windows_helper(self, mode, **kw)
                self.assertEqual(bool(kw.get("preload")), got["reader"])
                if mode == "heal":
                    self.assertEqual([want], [port for name, port in got["map"] if name == "searxng"])
                    self.assertIn(want, got["deleted"])
                    self.assertNotIn(DEFAULT_LISTENER, got["deleted"])
                else:
                    self.assertEqual([want], unsupported_notes(got))
                    self.assertEqual([], [x for x in got["served"] if str(want) in x])
        # No layer makes Setup forward it.
        got = windows_helper(self, "setup", user=user, MIOS_PORTS_SEARXNG=str(want))
        self.assertEqual([None], unsupported_notes(got))
        self.assertEqual([], [x for x in got["map"] if x[0] == "searxng"])
        self.assertNotIn(want, got["proxies"])

    def test_lan_status_follows_the_resolved_port(self):
        p = free_port()
        s = self.stack(host=f"[ports.categories.webtools]\nbase = {p}\n")
        r, got = lan_status(s, install_env(s), (p, DEFAULT_LISTENER, 8400))
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual([p], lan_loopback(r), r.stdout)
        self.assertNotIn("searxng", got, r.stdout)
        self.assertNotIn(DEFAULT_LISTENER, got.values(), r.stdout)
        # A process input outranks install.env, as for mios-show-image.
        q = free_port()
        r, got = lan_status(s, install_env(s), (q, p, 8400), MIOS_PORTS_SEARXNG=str(q))
        self.assertEqual([q], lan_loopback(r), r.stdout)
        self.assertNotIn("searxng", got, r.stdout)

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
        # Nothing stands in for the key: every list is the resolved one
        # without SearXNG, and each step names the key.
        got = windows_bootstrap(self, self.s_vendor)
        base = windows_bootstrap(self, _vendor_text())
        p = DEFAULT_LISTENER
        self.assertIsNone(got["port"])
        self.assertEqual(base["rules"], got["rules"])
        self.assertEqual([], got["removed"])
        self.assertEqual([x for x in base["proxy"] if x != ("delete", p)], got["proxy"])
        self.assertEqual([x for x in base["fw_ports"] if x != p], got["fw_ports"])
        self.assertEqual([x for x in base["links"] if "SearXNG" not in x[0]], got["links"])
        self.assertEqual([], [t for t in got["infos"] if "SearXNG" in t])
        for step in ("Firewall:", "portproxy:", "dev VM firewalld:", "MiOS Services:"):
            with self.subTest(step=step):
                self.assertTrue(any(w.startswith(step) and "[ports].searxng does not resolve" in w
                                    for w in got["warnings"]), got["warnings"])

    def test_windows_forwarding_helpers(self):
        # No layer carries the port (or no reader is found): Heal and
        # tailscale-serve leave SearXNG out by name, with nothing standing in.
        for mode, kw in (("heal", {}), ("heal", {"preload": True}), ("heal", {"common": False}),
                         ("tailscale", {}), ("tailscale", {"preload": True}), ("tailscale", {"common": False})):
            with self.subTest(helper=mode, **kw):
                got = windows_helper(self, mode, vendor=self.s_vendor, **kw)
                self.assertEqual(kw.get("preload", False), got["reader"])
                self.assertEqual([], [x for x in got["map"] if x[0] == "searxng"])
                self.assertEqual([], unsupported_notes(got))
                self.assertTrue(any("[ports].searxng does not resolve" in w for w in got["warnings"]),
                                got["warnings"])
                self.assertNotIn(DEFAULT_LISTENER, got.get("deleted", []))
                self.assertEqual([], [x for x in got.get("probed", []) + got.get("served", [])
                                      if f":{DEFAULT_LISTENER}" in x])
        # Setup never reads the port, so its output does not change.
        got = windows_helper(self, "setup", vendor=self.s_vendor)
        self.assertEqual([], got["warnings"])
        self.assertEqual([None], unsupported_notes(got))
        self.assertNotIn(DEFAULT_LISTENER, got["proxies"])
        self.assertEqual([], [x for x in got["rules"] if "searxng" in x[0]])

    def test_windows_helpers_keep_every_other_port(self):
        # SearXNG resolving or not leaves every other entry, and its order, as is.
        for mode in ("setup", "heal", "tailscale"):
            with self.subTest(helper=mode):
                with_it = windows_helper(self, mode)
                without = windows_helper(self, mode, vendor=self.s_vendor)
                self.assertEqual([x for x in with_it["map"] if x[0] != "searxng"], without["map"])
                self.assertEqual(len(without["map"]) + (mode == "heal"), len(with_it["map"]))
                for key in ("proxies", "rules"):
                    self.assertEqual(with_it.get(key), without.get(key), key)

    def test_lan_status(self):
        for env_file in (install_env(self.s), None):
            with self.subTest(install_env=bool(env_file)):
                r, got = lan_status(self.s, env_file, (DEFAULT_LISTENER, 8400))
                self.assertEqual(0, r.returncode, r.stdout + r.stderr)
                self.assertNotIn("searxng", got, r.stdout)
                self.assertEqual([], lan_loopback(r), r.stdout)
                self.assertEqual(8400, got.get("forge"), r.stdout)
                self.assertIn("[ports].searxng does not resolve", r.stderr)


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
                    "usr/libexec/mios/mios-firewall-isolate", "build-mios.ps1",
                    "usr/libexec/mios/mios-lan-status", *WIN_HELPERS.values(),
                    "usr/share/doc/mios/manual/root.md"):
            self.assertIn(rel, paths)

    def test_the_scan_covers_the_whole_manual(self):
        r = subprocess.run(["git", "-C", _ROOT, "ls-files", "-z", "usr/share/doc/mios/manual/"],
                           capture_output=True, timeout=60)
        self.assertEqual(0, r.returncode, r.stderr)
        manual = {rel for rel in r.stdout.decode().split("\0") if rel}
        self.assertGreater(len(manual), 1)
        self.assertEqual(set(), manual - set(scanned_paths(_ROOT)))

    def test_the_follow_up_register_is_empty(self):
        # Root's ruling D: no exemption can make D green.
        self.assertEqual({}, FOLLOW_UP)

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
            (WIN_HELPERS["setup"], "    @{ Port = (_MiosPort 'MIOS_PORTS_LLM_LIGHT'        8500); Name = 'llm-light'   }\n",
             "    @{ Port = (_MiosPort 'MIOS_PORTS_LLM_LIGHT'        8500); Name = 'llm-light'   }\n"
             "    @{ Port = (_MiosPort 'MIOS_PORTS_SEARXNG'          8800); Name = 'searxng'     }\n"),
            (WIN_HELPERS["heal"], "    @{ Key = 'llm_light'; Default = 8500 },\n",
             "    @{ Key = 'searxng'; Default = 8800 },\n    @{ Key = 'llm_light'; Default = 8500 },\n"),
            (WIN_HELPERS["tailscale"], "Get-PortFromSsot 'MIOS_PORTS_SEARXNG' 'searxng'\n",
             "Get-PortFromSsot 'MIOS_PORTS_SEARXNG' 'searxng' 8800\n"),
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
            ("usr/libexec/mios/mios-lan-status", '    "8500 llm-light"\n',
             '    "8500 llm-light"\n    "8801 searxng"\n'),
            (WIN_HELPERS["setup"], "    @{ Port = (_MiosPort 'MIOS_PORTS_LLM_LIGHT'        8500); Name = 'llm-light'   }\n",
             "    @{ Port = 8801; Name = 'searxng' }\n"),
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

    def test_plant_in_the_manual(self):
        rel = "usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md"
        with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
            anchor = fh.read().splitlines(keepends=True)[0]
        for planted, rule in (
            ("  searxng: BIND_ADDRESS=0.0.0.0:${MIOS_PORTS_SEARXNG} (granian default is :8080)\n",
             "port-fallback"),
            ("  searxng: BIND_ADDRESS=0.0.0.0:${MIOS_PORTS_SEARXNG}\n", "wildcard-bind"),
            ("SearXNG listens on 0.0.0.0 for the LAN.\n", "wildcard-bind"),
            ("Point Hermes at http://mios-searxng:8080 instead.\n", "stale-hostname"),
            ("  searxng: BIND_ADDRESS=[::]:${MIOS_PORTS_SEARXNG} on the dev VM drop-in.\n", "wildcard-bind"),
            ("  searxng: GRANIAN_HOST=:: binds every address.\n", "wildcard-bind"),
            ("  searxng: the dev VM writes mios-searxng.container.d/10-mios-dev-host-network.conf"
             " with Network=host.\n", "retired-drop-in"),
            ("The dev VM overlay writes the 10-mios-dev-host-network.conf drop-in\nfor mios-searxng"
             " too.\n", "retired-drop-in"),
        ):
            with self.subTest(planted=planted):
                self.assertIn(rule, self.plant(rel, anchor, anchor + planted))
                self.assertIn(rel, scanned_paths(_ROOT))

    def test_plant_a_retired_drop_in_or_wildcard_in_code(self):
        rm = "sudo rm -f /etc/containers/systemd/mios-searxng.container.d/10-mios-dev-host-network.conf\n"
        for planted, rule in (
            ("echo GRANIAN_HOST=0.0.0.0 > /etc/containers/systemd/mios-searxng.container.d/x.conf\n",
             "wildcard-bind"),
            ("rm -f a; echo Network=host > /etc/containers/systemd/mios-searxng.container.d/"
             "10-mios-dev-host-network.conf\n", "retired-drop-in"),
            ("& netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$_searxngPort\n",
             "wildcard-bind"),
        ):
            with self.subTest(planted=planted):
                self.assertIn(rule, self.plant("build-mios.ps1", rm, rm + planted))

    def test_the_manual_names_searxng_loopback_only(self):
        # Each passage that documents the Windows LAN rule, the portproxy,
        # tailnet serving or the SearXNG setup says it is loopback-only and
        # that forwarding it is unsupported.
        manual = os.path.join(_ROOT, "usr", "share", "doc", "mios", "manual")
        for name, passage in (("root.md", "Windows Firewall inbound rules so OTHER devices"),
                              ("root.md", "Windows-side `netsh interface portproxy` mappings so OTHER"),
                              ("windows.md", "Liveness is checked IN THE VM"),
                              ("ch25-local-search-engine-and-searxng.md", "## Setup")):
            with self.subTest(page=name, passage=passage):
                with open(os.path.join(manual, name), encoding="utf-8") as fh:
                    found = [p for p in re.split(r"<!-- mios-src:|\n---\n", fh.read()) if passage in p]
                self.assertEqual(1, len(found), passage)
                text = found[0].replace("`", "")
                self.assertIn("[ports].searxng", text)
                self.assertRegex(text, r"one listener is\s+loopback-only|loopback-only,\s+one listener")
                self.assertRegex(text, r"(?i)(?:direct\s+(?:Windows\s+)?LAN\s+forwarding|tailnet\s+serving)"
                                       r"[^.]*?\s(?:is|are)\s+unsupported")
                self.assertNotRegex(text, r"SearXNG\s+(?:is|gets)\s+(?:forwarded|served)\b")

    def test_clean_forms_pass(self):
        for line in ('"GRANIAN_PORT=${MIOS_PORTS_SEARXNG}"', '"mios-searxng",',
                     '{ source = "hermes", destination = "searxng", port_key = "searxng", protocol = "tcp" },',
                     "$_searxngPort = Get-MiosSsotPort -Key 'searxng'",
                     'ContainerName=mios-searxng', '[ -n "${MIOS_URLS_SEARXNG:-}" ]',
                     'Environment=SEARXNG_URL=http://${MIOS_NETWORK_LOOPBACK:-127.0.0.1}:${MIOS_PORTS_SEARXNG}',
                     "    Get-PortFromSsot 'MIOS_PORTS_SEARXNG' 'searxng'",
                     "if ($searxngPort -lt 1 -or $searxngPort -gt 65535) {",
                     "sudo rm -f /etc/containers/systemd/mios-searxng.container.d/10-mios-dev-host-network.conf",
                     "        & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0"
                     " listenport=$_searxngPort 2>&1 | Out-Null",
                     '    echo "  --   :${_searxng_port}  searxng: loopback-only listener"',
                     '  "SEARXNG_BIND_ADDRESS=127.0.0.1:${MIOS_PORTS_SEARXNG}",',
                     '  "BIND_ADDRESS=127.0.0.1:${MIOS_PORTS_SEARXNG}",',
                     'Environment=GRANIAN_HOST=127.0.0.1'):
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
