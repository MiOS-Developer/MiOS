#!/usr/bin/env python3
# AI-hint: Offline stdlib test for mios_oscontrol (refactor R9): stubs every sibling (fastapi.responses + mios_sse/mios_jsonsalvage/m...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stub-and-import test for the OS-control window verify + anti-fabrication verdict."""

import sys
import types

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _install_stubs():
    """Minimal stand-ins for every module mios_oscontrol imports at top level so
    it loads with no 3rd-party deps, network or DB. The pure verify helpers under
    test never call into any of them."""
    fastapi = sys.modules.setdefault("fastapi", types.ModuleType("fastapi"))
    responses = types.ModuleType("fastapi.responses")
    for _c in ("JSONResponse", "StreamingResponse"):
        setattr(responses, _c, type(_c, (), {"__init__": lambda self, *a, **k: None}))
    fastapi.responses = responses
    sys.modules.setdefault("fastapi.responses", responses)

    sse = types.ModuleType("mios_sse")
    for _n in ("_sse_status_phase", "_sse_status", "_sse_chunk", "_sse_done"):
        setattr(sse, _n, lambda *a, **k: b"")
    sys.modules["mios_sse"] = sse

    js = types.ModuleType("mios_jsonsalvage")
    js.loads_lenient = lambda s: {}
    sys.modules["mios_jsonsalvage"] = js

    dci = types.ModuleType("mios_dci")
    dci.DCI_ENABLED = False
    dci.critic_then_maybe_flow = lambda *a, **k: None
    sys.modules["mios_dci"] = dci

    disp = types.ModuleType("mios_dispatch")
    async def _dispatch(*a, **k):
        return {"success": True, "output": "", "exit_code": 0}
    disp.dispatch_mios_verb = _dispatch
    sys.modules["mios_dispatch"] = disp

    verity = types.ModuleType("mios_verity")
    async def _polish(*a, **k):
        return ""
    verity.polish_response = _polish
    sys.modules["mios_verity"] = verity

    know = types.ModuleType("mios_knowledge")
    know._store_knowledge = lambda *a, **k: None
    sys.modules["mios_knowledge"] = know

def main():
    _install_stubs()
    import mios_oscontrol as m

    m.configure(launch_verbs=frozenset({"open_app", "launch_app", "open_url"}),
                os_control_action_verbs=frozenset({"open_app", "close_window"}))

    shell = {"hwnd": 1, "title": "Program Manager", "proc": "explorer"}
    before = {"ok": True, "count": 1, "windows": [shell]}
    opened_win = {"hwnd": 42, "title": "Sample App - Home", "proc": "msrdc"}
    after_opened = {"ok": True, "count": 2, "windows": [shell, opened_win]}
    after_none = {"ok": True, "count": 1, "windows": [shell]}

    d_open = m._window_diff(before, after_opened)
    check("window_diff sees the opened window",
          [w["hwnd"] for w in d_open["opened"]] == [42] and not d_open["closed"],
          str(d_open))
    d_close = m._window_diff(after_opened, before)
    check("window_diff sees the closed window (reverse)",
          [w["hwnd"] for w in d_close["closed"]] == [42] and not d_close["opened"],
          str(d_close))
    check("window_delta_text renders the opened title",
          "Sample App - Home" in m._window_delta_text(d_open)
          and m._window_delta_text(d_open).startswith("opened:"),
          m._window_delta_text(d_open))
    check("window_delta_text reports no change when snapshots match",
          m._window_delta_text(m._window_diff(before, before))
          == "no visible window change detected")

    fired = {"success": True, "output": "launching org.gnome.Epiphany", "exit_code": 0}
    verdict_ok = m._verify_os_action(
        "open_app", {"app": "epiphany"}, fired, before, after_opened, d_open)
    check("launch with a NEW window verifies TRUE (count-delta, name-agnostic)",
          verdict_ok is True)

    d_none = m._window_diff(before, after_none)
    verdict_none = m._verify_os_action(
        "open_app", {"app": "epiphany"}, fired, before, after_none, d_none)
    check("launch that fired but opened NO window verifies FALSE (anti-fabrication)",
          verdict_none is False,
          "exit-0 fire must NOT be claimed a success without a window")

    blind = {"ok": False, "count": 0, "windows": []}
    verdict_blind = m._verify_os_action(
        "open_app", {"app": "epiphany"}, fired, blind, blind,
        m._window_diff(blind, blind))
    check("blind enumeration fails action verification (fail-closed)",
          verdict_blind is False)

    close_res = {"success": True, "output": "", "exit_code": 0}
    v_close = m._verify_os_action(
        "close_window", {"title": "Sample App"}, close_res,
        after_opened, before, m._window_diff(after_opened, before))
    check("close verifies TRUE when the target window is gone", v_close is True)
    v_close_still = m._verify_os_action(
        "close_window", {"title": "Sample App"}, close_res,
        after_opened, after_opened, m._window_diff(after_opened, after_opened))
    check("close verifies FALSE when the target window is still present",
          v_close_still is False)

    tab_res = {"success": True, "output": '{"success": true, "target": "com.google.ChromeDev", "url": "https://example.com", "summary": "tab-opened-existing"}', "exit_code": 0}
    v_tab = m._verify_os_action(
        "open_url", {"url": "https://example.com"}, tab_res,
        before, after_none, m._window_diff(before, after_none))
    check("open_url verify is TRUE for already-running tab opens", v_tab is True)

    m.configure(
        fastpath_verbs=frozenset({"open_app", "schedule"}),
        verb_catalog={
            "open_app": {"sig": "app", "desc": "Launch an app"},
            "schedule": {"sig": "when, task", "desc": "Schedule a task\nlater"},
        })
    rendered = m._render_os_control_verbs()
    lines = rendered.split("\n")
    check("render emits one line per fast-path verb", len(lines) == 2, rendered)
    check("render sorts verbs (open_app before schedule)",
          lines[0] == "  open_app(app) -- Launch an app", lines[0])
    check("render collapses newlines in the desc",
          lines[1] == "  schedule(when, task) -- Schedule a task later", lines[1])
    m.configure(fastpath_verbs=frozenset())
    check("render is empty when no verbs are registered",
          m._render_os_control_verbs() == "",
          "expected '' for empty _FASTPATH_VERBS")

    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_launch.py (T-1092)
# ==============================================================================
# AI-hint: Standalone unit test for the deterministic_action_route logic to ensure "open/launch" commands correctly strip filler phrases and map to open_app(n...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

import os
import re
import sys

_RESULTS_launch: list = []

def _check_launch(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS_launch.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _load_fillers() -> list:
    """Load the REAL SSOT list the same way server.py `_load_launch_fillers` does."""
    path = os.environ.get("MIOS_TOML", "/usr/share/mios/mios.toml")
    if not os.path.exists(path):
        here = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(here, "..", "..", "..", "share", "mios", "mios.toml")
        if os.path.exists(cand):
            path = cand
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with open(path, "rb") as f:
        rt = (tomllib.load(f).get("routing") or {})
    return sorted(
        (str(p).lower().strip() for p in (rt.get("launch_filler_phrases") or []) if str(p).strip()),
        key=len, reverse=True)

_TRIGGERS = {"open", "launch"}

def _extract(user_text: str, fillers: list):
    t = (user_text or "").strip()
    if not t or len(t) > 80 or "?" in t:
        return None
    words = t.split()
    if len(words) < 2:
        return None
    head = words[0].lower().strip(".,:;!\"'")
    if head not in _TRIGGERS:
        return None
    rest = " ".join(words[1:]).strip()
    low = rest.lower()
    changed = True
    while changed and rest:
        changed = False
        for f in fillers:
            if f and low.endswith(f):
                rest = rest[:len(rest) - len(f)].rstrip(" ,.")
                low = rest.lower()
                changed = True
                break
    if not rest or len(rest.split()) > 3:
        return None
    if "://" in rest or re.search(r"\b(in|and|then|with|on|to)\b", low):
        return None
    return rest

def t_ssot() -> None:
    fillers = _load_fillers()
    _check_launch("fillers: SSOT list non-empty", len(fillers) > 0, f"n={len(fillers)}")
    _check_launch("fillers: longest-match-first ordering",
           fillers == sorted(fillers, key=len, reverse=True))
    _check_launch("fillers: 'for me' present (the e2e case)", "for me" in fillers)
    _check_launch("fillers: 'on my desktop' present (the e2e case)", "on my desktop" in fillers)

def t_extraction() -> None:
    fillers = _load_fillers()
    cases = {
        "open notepad": "notepad",                        # bare
        "open notepad for me": "notepad",                 # trailing courtesy stripped (was the bug)
        "open spotify on my desktop for me": "spotify",   # location+courtesy stripped (was the bug)
        "launch discord please": "discord",
        "open file explorer": "file explorer",            # 2-word app preserved
        "open file in editor": None,                      # true compound -> LLM router
        "open the calculator and minimize it": None,      # conjunction -> LLM router
        "open https://example.com": None,                 # url -> LLM router
        "what is open today": None,                       # not a launch -> None
        "open": None,                                     # bare trigger only -> None
    }
    for text, expected in cases.items():
        got = _extract(text, fillers)
        _check_launch(f"extract {text!r} -> {expected!r}", got == expected, f"got={got!r}")

def _main_launch() -> int:
    for t in (t_ssot, t_extraction):
        t()
    passed = sum(1 for _, ok, _ in _RESULTS_launch if ok)
    total = len(_RESULTS_launch)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def _run_extra_launch():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_launch()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



# ==============================================================================
# Linux clients: real entrypoints with hermetic HTTP responses and layered SSOT.
# ==============================================================================
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

_CLIENT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_CLIENT_ROOT / "usr/lib/mios"))
import mios_oscontrol_client as client


class ClientContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = importlib.machinery.SourceFileLoader(
            "mios_pc_control_contract", str(_CLIENT_ROOT / "usr/libexec/mios/mios-pc-control"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.pc = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.pc)

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"MIOS_OSCONTROL_EXECUTOR": ""}))
        self.config_patch = patch.object(client.mios_toml, "load_merged",
            return_value={"ports": {"oscontrol": 9659}, "os_control": {}})
        self.config = self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def test_endpoint_uses_derived_port_without_router_discovery(self):
        with patch.object(subprocess, "check_output", side_effect=AssertionError("router discovery")):
            self.assertEqual(client.executor_endpoint(), "http://127.0.0.1:9659")
            self.config.return_value["os_control"]["executor_endpoint"] = "http://localhost:9660/"
            self.assertEqual(client.executor_endpoint(), "http://localhost:9660")
        with patch.dict(os.environ, {"MIOS_OSCONTROL_EXECUTOR": "http://192.0.2.10:9661"}):
            self.assertEqual(client.executor_endpoint(), "http://192.0.2.10:9661")

    def test_invalid_endpoint_and_port_fail(self):
        for value in (False, 0, 65536, "8950", None):
            self.config.return_value["ports"]["oscontrol"] = value
            with self.subTest(port=value), self.assertRaises(ValueError):
                client.executor_endpoint()
        for value in ("file:///tmp/executor", "http://localhost:0", "http://localhost:70000",
                      "http://user:password@localhost:9659", 'http://localhost:9659/"',
                      "http://localhost:9659/?secret=x"):
            with patch.dict(os.environ, {"MIOS_OSCONTROL_EXECUTOR": value}), self.subTest(url=value), self.assertRaises(ValueError):
                client.executor_endpoint()

    def test_actual_layered_fragments_and_port_allocation(self):
        self.config_patch.stop()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            vendor, host, user = [directory / name for name in ("vendor.toml", "host.toml", "user.toml")]
            vendor.write_text('[ports.categories.control]\nbase=9650\nstride=1\nmembers=["oscontrol"]\n')
            host.write_text('[ports.categories.control]\nbase=9651\n')
            fragments = directory / "host.d"
            fragments.mkdir()
            (fragments / "99-local.toml").write_text('[ports.categories.control]\nbase=9652\n')
            user.write_text('[os_control]\nexecutor_endpoint="http://localhost:9662"\n')
            empty_fragments = str(directory / "absent")
            with patch.dict(os.environ, {"MIOS_RESOLVER_NATIVE": "0", "MIOS_VENDOR_TOML": str(vendor),
                    "MIOS_HOST_TOML": str(host), "MIOS_USER_TOML": str(user),
                    "MIOS_VENDOR_TOML_D": empty_fragments, "MIOS_HOST_TOML_D": str(fragments),
                    "MIOS_USER_TOML_D": empty_fragments}):
                client.mios_toml.clear_cache()
                self.assertEqual(client.executor_endpoint(), "http://localhost:9662")
                user.unlink()
                client.mios_toml.clear_cache()
                self.assertEqual(client.executor_endpoint(), "http://127.0.0.1:9652")
            client.mios_toml.clear_cache()

    def test_success_requires_literal_consistent_verdicts(self):
        positive = {"ok": True, "verified": True}
        self.assertEqual(client.require_verdict(positive), positive)
        self.assertEqual(client.require_verdict({"fired": True, "launched": True}, "launched")["launched"], True)
        negative = [{"received": True}, {"status": "ok", "windows": []},
                    {"fired": True, "launched": False}, {"ok": "false"}, {"ok": 1},
                    {"ok": True, "verified": False}, {"ok": True, "success": False},
                    {"ok": True, "fired": False}, {"ok": True, "error": "failed"},
                    {"launched": True, "verdict": {"launched": False}}, [], None]
        for body in negative:
            with self.subTest(body=body), self.assertRaises(ValueError):
                client.require_verdict(body)

    def response(self, body, status=200):
        response = MagicMock(status=status)
        response.__enter__.return_value = response
        response.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
        return response

    def test_http_errors_and_malformed_bodies_never_become_success(self):
        failure = urllib.error.HTTPError("http://localhost/test", 500, "failure", {}, io.BytesIO(b'{"ok":true}'))
        with patch.object(client.urllib.request, "urlopen", side_effect=failure), self.assertRaises(urllib.error.HTTPError):
            client.request_json("http://localhost/test")
        for reply in (self.response({"ok": True}, status=500), self.response(b"not-json"),
                      self.response({"received": True}), self.response({"ok": "false"})):
            with patch.object(client.urllib.request, "urlopen", return_value=reply), self.assertRaises(ValueError):
                client.request_json("http://localhost/test")

    def test_health_requires_executor_verdict_instead_of_status_code(self):
        for body, valid in (({"ok": True, "implementation": "powershell-win32"}, True),
                            ({"ok": True, "backend": "portal"}, True),
                            ({"status": "ok"}, False), ({"ok": "false"}, False),
                            ({"ok": True, "verified": False}, False)):
            with self.subTest(body=body), patch.object(sys, "argv", ["client", "health"]), \
                    patch.object(client.urllib.request, "urlopen", return_value=self.response(body)):
                if valid:
                    client.main()
                else:
                    with self.assertRaises(ValueError):
                        client.main()

    def run_pc(self, command, body):
        def response(request, **kwargs):
            return self.response({"ok": True} if request.full_url.endswith("/health") else body)
        with patch.object(client.urllib.request, "urlopen", side_effect=response), \
                patch.object(sys, "argv", ["mios-pc-control"] + command), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                self.pc.main()
        return result.exception.code

    def test_pc_reads_and_mouse_routes_require_real_verdict(self):
        for command, body in ((["window-list", "--json"], {"ok": True, "windows": []}),
                              (["ui-list"], {"ok": True, "elements": []}),
                              (["ui-tree"], {"ok": True, "tree": {}}),
                              (["screen-layout"], {"ok": True, "screens": []}),
                              (["mouse-move", "1", "2"], {"ok": True})):
            with self.subTest(command=command):
                self.assertEqual(self.run_pc(command, body), 0)
                self.assertEqual(self.run_pc(command, {"ok": False}), 1)
                self.assertEqual(self.run_pc(command, {"status": "ok"}), 1)

    def test_pc_launch_rejects_receipt_and_fired_without_launch(self):
        self.assertEqual(self.run_pc(["launch", "test-app"], {"launched": True, "fired": True}), 0)
        for body in ({"received": True}, {"fired": True, "launched": False},
                     {"ok": True}, {"launched": "false"}):
            with self.subTest(body=body):
                self.assertEqual(self.run_pc(["launch", "test-app"], body), 1)

    def test_shell_launch_rejects_invalid_verdict_without_interop_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            fixture = directory / "mios.toml"
            fixture.write_text('[ports]\noscontrol=9659\n')
            # Patch Python's HTTP transport in the subprocess; no listener or desktop action.
            (directory / "sitecustomize.py").write_text('''import json, os, urllib.request
class Response:
    status = int(os.environ.get("MIOS_TEST_HTTP_STATUS", "200"))
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return b'{"ok":true}' if self.health else os.environ["MIOS_TEST_RESPONSE"].encode()
def open_response(request, **kwargs):
    response = Response()
    response.health = request.full_url.endswith("/health")
    return response
urllib.request.urlopen = open_response
''')
            environment = dict(os.environ, PYTHONPATH=str(directory), MIOS_RESOLVER_NATIVE="0",
                MIOS_VENDOR_TOML=str(fixture), MIOS_HOST_TOML=str(directory / "none-host"),
                MIOS_USER_TOML=str(directory / "none-user"), MIOS_VENDOR_TOML_D=str(directory / "none"),
                MIOS_HOST_TOML_D=str(directory / "none"), MIOS_USER_TOML_D=str(directory / "none"))
            script = str(_CLIENT_ROOT / "usr/libexec/mios/mios-windows")
            for body, expected in (({"launched": True, "fired": True}, 0), ({"received": True}, 1),
                                   ({"fired": True, "launched": False}, 1), ({"launched": "false"}, 1)):
                environment["MIOS_TEST_RESPONSE"] = json.dumps(body)
                result = subprocess.run(["bash", script, "launch", "https://example.invalid"],
                    env=environment, capture_output=True, text=True, timeout=15)
                with self.subTest(body=body):
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual("launched via" in result.stdout, expected == 0)
            environment["MIOS_TEST_RESPONSE"] = '{"launched":true}'
            environment["MIOS_TEST_HTTP_STATUS"] = "500"
            result = subprocess.run(["bash", script, "launch", "https://example.invalid"],
                env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stderr)


def _run_all_folded_oscontrol_suites():
    rc = _run_extra_launch()
    if rc not in (None, 0):
        sys.exit(f"Folded test suite failed: exit code {rc}")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ClientContracts))
    if not result.wasSuccessful():
        sys.exit(1)

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_oscontrol_suites()
    sys.exit(_rc_main)
