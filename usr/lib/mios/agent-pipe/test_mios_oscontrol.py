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

    destination = {"id": "fixture-windows", "platform": "windows",
                   "session_id": "fixture", "backend": "win32"}
    for snapshot in (before, after_opened, after_none):
        snapshot["targets"] = {destination["id"]: destination}
        for window in snapshot["windows"]:
            window["_execution_target_id"] = destination["id"]
    fired = {"success": True, "output": json.dumps(
        {"execution_target": destination}), "exit_code": 0}
    verdict_ok = m._verify_os_action(
        "open_app", {"app": "sample app"}, fired, before, after_opened, d_open)
    check("launch verifies the matching window on its actual session",
          verdict_ok is True)

    d_none = m._window_diff(before, after_none)
    verdict_none = m._verify_os_action(
        "open_app", {"app": "sample app"}, fired, before, after_none, d_none)
    check("launch that fired but opened NO window verifies FALSE (anti-fabrication)",
          verdict_none is False,
          "exit-0 fire must NOT be claimed a success without a window")

    blind = {"ok": False, "count": 0, "windows": []}
    verdict_blind = m._verify_os_action(
        "open_app", {"app": "sample app"}, fired, blind, blind,
        m._window_diff(blind, blind))
    check("blind enumeration fails action verification (fail-closed)",
          verdict_blind is False)

    close_res = dict(fired)
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
    check("tab receipt alone does not verify the destination window", v_tab is False)

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
# Consolidated from test_mios_launch.py (task_1006)
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

_LIVE_CONNECT = None


def setUpModule():
    """Hermeticity guard: every executor call is mocked, so refuse a real
    connection -- a dropped mock fails here instead of reaching a live executor."""
    global _LIVE_CONNECT
    import socket

    def _refuse(self, address, *args, **kwargs):
        raise AssertionError(f"hermetic suite attempted a live connection to {address!r}")

    _LIVE_CONNECT = patch.multiple(socket.socket, connect=_refuse, connect_ex=_refuse)
    _LIVE_CONNECT.start()


def tearDownModule():
    if _LIVE_CONNECT is not None:
        _LIVE_CONNECT.stop()


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


class OSActionResponseContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import AsyncMock
        _install_stubs()
        self.m = importlib.import_module("mios_pipe.routing.oscontrol")
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.before = {"ok": True, "count": 1, "windows": [{"id": "shell", "title": "Terminal"}]}
        self.after = {"ok": True, "count": 2, "windows": [
            *self.before["windows"], {"id": "obs", "title": "OBS Studio"}]}
        self.destination = {"id": "fixture-linux", "platform": "linux",
                            "session_id": "fixture", "backend": "hyprland"}
        for snapshot in (self.before, self.after):
            snapshot["targets"] = {self.destination["id"]: self.destination}
            for window in snapshot["windows"]:
                window["_execution_target_id"] = self.destination["id"]
        self.result = {"success": True, "exit_code": 0,
                       "output": json.dumps({"dispatched": True, "confirmed": False,
                                             "execution_target": self.destination}),
                       "stderr": "dispatch accepted"}
        self.dispatch = AsyncMock(return_value=self.result)
        self.enumerate = AsyncMock(side_effect=[self.before, self.after])
        self.polish, self.satisfaction = AsyncMock(return_value="invented success"), AsyncMock()
        self.process = AsyncMock(return_value=True)
        self.stack.enter_context(patch.multiple(self.m,
            _OS_CONTROL_ACTION_VERBS=frozenset({"open_app", "focus_window", "close_window"}),
            _LAUNCH_VERBS=frozenset({"open_app", "open_url"}), OS_CONTROL_LAUNCH_VERIFY_S=0,
            dispatch_mios_verb=self.dispatch, _enumerate_windows=self.enumerate,
            polish_response=self.polish, _inline_satisfaction_check=self.satisfaction,
            _proc_present=self.process, _center_windows=AsyncMock(return_value=[]),
            _conv_key_var=types.SimpleNamespace(get=lambda: None), _index_window_event=lambda *a: None,
            _db_create=lambda *a, **k: "SQL", _db_post=lambda *a: None, _db_fire=lambda *a: None,
            DCI_ENABLED=False, _loads_lenient=json.loads, _load_oscontrol_endpoints=lambda: [],
            JSONResponse=lambda **k: types.SimpleNamespace(body=json.dumps(k["content"]).encode()),
            StreamingResponse=lambda iterator, **k: types.SimpleNamespace(body_iterator=iterator),
            _sse_chunk=lambda text, **k: text.encode(), _sse_done=lambda: b"",
            _sse_status=lambda **k: b"", _sse_status_phase=lambda **k: b""))

    async def respond(self, tool="open_app", streaming=False):
        response = await self.m._respond_os_control(tool, {"name": "OBS"}, None,
            streaming=streaming, chat_id="fixture", model="fixture", session_id=None,
            last_user_text="open OBS")
        if streaming:
            return b"".join([chunk async for chunk in response.body_iterator]).decode()
        return json.loads(response.body)["choices"][0]["message"]["content"]

    def envelope(self, text):
        return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])

    async def test_failed_launcher_has_clear_result_without_model_or_process_fallback(self):
        self.result.update(success=False, exit_code=1, stderr="MiOS launch: obsstudio")
        text = await self.respond()
        self.assertIn("I couldn't open OBS", text.split("<details", 1)[0])
        envelope = self.envelope(text)
        self.assertEqual(envelope["tool_result"], self.result)
        self.assertFalse(envelope["window_change"]["verified"])
        self.assertEqual(self.enumerate.await_count, 1)
        self.polish.assert_not_awaited()
        self.satisfaction.assert_not_awaited()
        self.process.assert_not_awaited()

    async def test_success_without_window_is_unverified_even_with_existing_process(self):
        self.enumerate.side_effect = [{"ok": False, "count": 0, "windows": []}] * 2
        text = await self.respond()
        self.assertIn("couldn't verify its window", text.split("<details", 1)[0])
        self.assertFalse(self.envelope(text)["window_change"]["verified"])
        self.assertTrue(self.envelope(text)["tool_result"]["success"])
        self.process.assert_not_awaited()
        self.polish.assert_not_awaited()

    async def test_verified_window_has_grounded_success_and_keeps_stderr(self):
        text = await self.respond()
        self.assertIn("OBS is open; its window was verified", text)
        self.assertTrue(self.envelope(text)["window_change"]["verified"])
        self.assertEqual(self.envelope(text)["tool_result"]["stderr"], "dispatch accepted")
        self.polish.assert_not_awaited()
        self.satisfaction.assert_not_awaited()

    async def test_denied_focus_is_not_retried_or_converted_into_launch(self):
        self.result.update(success=False, exit_code=126, stderr="human approval required")
        text = await self.respond(tool="focus_window")
        self.assertIn("failed", text.split("<details", 1)[0])
        self.assertEqual(self.dispatch.await_count, 1)
        self.assertEqual(self.dispatch.await_args.args[0], "focus_window")
        self.assertEqual(self.envelope(text)["tool_result"]["exit_code"], 126)

    async def test_streaming_failure_contains_human_failure(self):
        self.result.update(success=False, exit_code=1)
        text = await self.respond(streaming=True)
        self.assertIn("I couldn't open OBS", text)
        self.assertFalse(self.envelope(text)["window_change"]["verified"])

    async def test_streaming_exception_does_not_claim_completion(self):
        self.dispatch.side_effect = RuntimeError("fixture failure")
        text = await self.respond(streaming=True)
        self.assertIn("couldn't complete or verify", text)
        self.assertNotIn("action completed", text)

    async def test_failed_or_contradictory_command_cannot_verify_new_window(self):
        for success in (False, True):
            self.assertFalse(self.m._verify_os_action("open_app", {"name": "OBS"},
                {"success": success, "exit_code": 1}, self.before, self.after,
                self.m._window_diff(self.before, self.after)))

    async def test_unrelated_session_cannot_verify_launch(self):
        other = dict(self.destination, id="fixture-windows", platform="windows")
        self.result["output"] = json.dumps({"execution_target": other})
        text = await self.respond()
        self.assertFalse(self.envelope(text)["window_change"]["verified"])
        self.assertIn("couldn't verify", text.split("<details", 1)[0])
        self.assertEqual(self.envelope(text)["window_change"]["after_count"], 0)

    async def test_unrelated_app_on_same_session_does_not_verify_launch(self):
        self.after["windows"][-1]["title"] = "Unrelated application"
        text = await self.respond()
        self.assertFalse(self.envelope(text)["window_change"]["verified"])
        self.assertIn("couldn't verify", text.split("<details", 1)[0])

    async def test_unverified_unrelated_window_is_not_recorded_or_centered(self):
        self.after["windows"][-1]["title"] = "Unrelated application"
        with patch.object(self.m, "_record_last_opened_window") as record:
            await self.respond()
        record.assert_not_called()
        self.m._center_windows.assert_not_awaited()

    async def test_native_probe_with_conflicting_operator_identity_is_unavailable(self):
        other = dict(self.destination, session_id="different")
        snapshot, _ = await self.snapshot({"success": True, "exit_code": 0,
            "operator_context": other, "output": json.dumps({
                "local": True, "execution_target": self.destination, "windows": []})})
        self.assertFalse(snapshot["ok"])
        self.assertIn("differs from its operator context", " ".join(snapshot["errors"]))

    async def test_missing_destination_is_unverified_despite_new_local_window(self):
        self.result["output"] = '{"dispatched":true,"confirmed":false}'
        text = await self.respond()
        self.assertFalse(self.envelope(text)["window_change"]["verified"])
        self.assertIn("couldn't verify", text.split("<details", 1)[0])

    async def test_same_target_id_with_different_session_is_unverified(self):
        other = dict(self.destination, session_id="different")
        self.result["output"] = json.dumps({"execution_target": other})
        text = await self.respond()
        self.assertFalse(self.envelope(text)["window_change"]["verified"])


    async def test_requested_unavailable_session_is_rejected_before_dispatch(self):
        response = await self.m._respond_os_control("open_app", {"name":"OBS","session":"offline-guest"}, None,
            streaming=False,chat_id="fixture",model="fixture",session_id=None,last_user_text="open OBS")
        text=json.loads(response.body)["choices"][0]["message"]["content"]
        self.dispatch.assert_not_awaited()
        self.assertFalse(self.envelope(text)["tool_result"]["success"])
        self.assertIn("unavailable",self.envelope(text)["tool_result"]["stderr"])

    async def test_requested_ready_session_is_bound_to_dispatch_and_proof(self):
        response = await self.m._respond_os_control("open_app", {"name":"OBS","session":"fixture"}, None,
            streaming=False,chat_id="fixture",model="fixture",session_id=None,last_user_text="open OBS")
        text=json.loads(response.body)["choices"][0]["message"]["content"]
        self.assertEqual(self.dispatch.await_args.args[1]["session"],self.destination["id"])
        self.assertTrue(self.envelope(text)["window_change"]["verified"])

    async def test_requested_session_changed_receipt_fails_without_retry(self):
        self.result["output"]=json.dumps({"dispatched":True,"execution_target":dict(self.destination,session_id="changed")})
        response = await self.m._respond_os_control("open_app", {"name":"OBS","session":"fixture"}, None,
            streaming=False,chat_id="fixture",model="fixture",session_id=None,last_user_text="open OBS")
        text=json.loads(response.body)["choices"][0]["message"]["content"]
        self.assertFalse(self.envelope(text)["tool_result"]["success"])
        self.assertEqual(self.dispatch.await_count,1)

    async def test_native_inventory_contributes_nested_targets_without_losing_identity(self):
        nested=dict(self.destination,id="nested",session_id="nested")
        probe=self.m._probe_windows({"execution_target":self.destination,"windows":[],
            "session_inventory":{"ok":True,"sessions":[{"execution_target":nested,"available":True,
                "windows":[{"id":"obs","title":"OBS"}]}]}},"native")
        self.assertEqual(probe["targets"],{self.destination["id"]:self.destination,"nested":nested})
        self.assertEqual(probe["windows"][0]["_execution_target_id"],"nested")

    async def test_registered_nodes_are_loaded_from_both_sections_on_each_invocation(self):
        self.stack.close()  # Exercise discovery, not this class's empty-registry stub.
        import sys
        config={"os_control":{"nodes":{"console":{"endpoint":"http://console.invalid"}}},
                "computer_use":{"nodes":{"mobile":{"endpoint":"http://mobile.invalid"}}}}
        module=types.SimpleNamespace(section=lambda _,name:config[name])
        with patch.dict(sys.modules,{"mios_db_config":module}):
            first=self.m._load_oscontrol_endpoints()
            config["computer_use"]["nodes"]["mobile"]["endpoint"]="http://replacement.invalid"
            second=self.m._load_oscontrol_endpoints()
        self.assertEqual(len(first),2)
        self.assertTrue(any(row["registration_id"]=="computer_use.nodes.mobile" and row["url"]=="http://replacement.invalid" for row in second))


    async def test_explicit_platform_location_is_not_swallowed_as_an_application_name(self):
        from mios_pipe.routing import routing
        with patch.multiple(routing,_FASTPATH_VERBS=frozenset({"open_app"}),
                _LAUNCH_TRIGGERS=frozenset({"open"}),_LAUNCH_FILLERS=["on my desktop"],
                _LAUNCH_LEAD_WORDS=frozenset(),_LAUNCH_TRAIL_WORDS=frozenset(),
                _COMPOUND_ACTION_ALT="",_COMPOUND_CONNECTIVE_ALT=""):
            self.assertIsNone(routing._deterministic_action_route("open OBS on xbox"))
            self.assertEqual(routing._deterministic_action_route("open OBS")["args"],{"name":"OBS"})
            self.assertEqual(routing._deterministic_action_route("open OBS on my desktop")["args"],{"name":"OBS"})

    async def snapshot(self, result):
        self.stack.close()
        from unittest.mock import AsyncMock
        dispatch = AsyncMock(return_value=result)
        with patch.object(self.m, "dispatch_mios_verb", dispatch), \
             patch.object(self.m, "_loads_lenient", json.loads), \
             patch.object(self.m, "_load_oscontrol_endpoints", return_value=[]), \
             patch.object(self.m, "OS_CONTROL_ENUM_RETRY", 0), \
             patch.object(self.m.os.path, "exists", return_value=False):
            value = await self.m._enumerate_windows()
        return value, dispatch

    async def test_linux_probe_valid_empty_is_successful_without_retries(self):
        snapshot, dispatch = await self.snapshot(
            {"success": True, "exit_code": 0, "output": '{"windows":[]}'})
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["count"], 0)
        self.assertEqual([call.args[0] for call in dispatch.await_args_list], ["cu_window_list"])

    async def test_native_probe_can_bind_its_authoritative_operator_context(self):
        snapshot, _ = await self.snapshot({"success": True, "exit_code": 0,
            "operator_context": self.destination,
            "output": '{"local":true,"windows":[{"id":"obs","title":"OBS"}]}'})
        self.assertEqual(snapshot["targets"], {self.destination["id"]: self.destination})

    async def test_forwarded_probe_cannot_borrow_local_operator_identity(self):
        snapshot, _ = await self.snapshot({"success": True, "exit_code": 0,
            "operator_context": self.destination,
            "output": '{"local":false,"windows":[{"id":"obs","title":"OBS"}]}'})
        self.assertEqual(snapshot["targets"], {})
        self.assertFalse(self.m._target_snapshot(snapshot, self.destination)["ok"])

    async def test_probe_failure_preserves_diagnostic(self):
        snapshot, _ = await self.snapshot({"success": False, "exit_code": 1,
            "output": '{"windows":[]}', "stderr": "Wayland unavailable"})
        self.assertFalse(snapshot["ok"])
        self.assertIn("Wayland unavailable", " ".join(snapshot["errors"]))

    async def test_native_window_identity_does_not_change_with_title(self):
        before = {"windows": [{"id": "0x1", "title": "OBS", "_source": "linux"}]}
        after = {"windows": [{"id": "0x1", "title": "OBS Studio", "_source": "linux"}]}
        self.assertEqual(self.m._window_diff(before, after), {"opened": [], "closed": []})


def _run_all_folded_oscontrol_suites():
    rc = _run_extra_launch()
    if rc not in (None, 0):
        sys.exit(f"Folded test suite failed: exit code {rc}")
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(case)
                                for case in (ClientContracts, OSActionResponseContracts)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_oscontrol_suites()
    sys.exit(_rc_main)
