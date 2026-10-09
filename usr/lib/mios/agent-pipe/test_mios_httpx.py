#!/usr/bin/env python3
# AI-hint: Unit test for mios_httpx.py
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from mios_httpx import MiOSAsyncHTTPTransport

class TestMiOSAsyncHTTPTransport(unittest.TestCase):
    def test_fetch_endpoint_mock(self):
        transport = MiOSAsyncHTTPTransport(mock_mode=True)
        res = asyncio.run(transport.fetch_endpoint("http://localhost:8000/v1/models"))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["status_code"], 200)

    def test_uds_endpoint_mock(self):
        transport = MiOSAsyncHTTPTransport(mock_mode=True)
        res = asyncio.run(transport.fetch_endpoint("http+unix://%2Frun%2Fmios%2Fpipe.sock/status"))
        self.assertTrue(res["is_unix_socket"])
        self.assertEqual(res["status_code"], 200)



# ==============================================================================
# Consolidated from test_mios_httpclient.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for mios_pipe.kernel.httpclient -- the ONE shared outbound AsyncClient and the task_206 b...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

"""Unit tests for the shared http client + the task_206 coalescing chokepoint."""

import asyncio
import os
import sys
import time

try:
    import httpx
except ModuleNotFoundError:                      # pragma: no cover
    print("[SKIP] httpx absent -- the shared client cannot be exercised")
    sys.exit(0)

from mios_pipe.kernel import httpclient as hc

_fails_httpclient = 0

def _check_httpclient(name, cond, detail=""):
    global _fails_httpclient
    if not cond:
        _fails_httpclient += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _req(url="http://remote:9999/v1/chat/completions", method="POST", model="m"):
    if method == "GET":
        return httpx.Request(method, url)
    return httpx.Request(method, url, json={"model": model})

def t_default_off():
    async def run():
        await hc.reset()
        hc.configure(batch_enable=False)
        client = await hc._get_client()
        hooks = list(client.event_hooks.get("request") or [])
        t0 = time.perf_counter()
        await hc._batch_request_hook(_req())
        ms = (time.perf_counter() - t0) * 1000
        await hc.reset()
        return hooks, ms
    hooks, ms = asyncio.run(run())
    _check_httpclient("default: NO request hook is registered at all", hooks == [], repr(hooks))
    _check_httpclient("default: no coalescer is built", hc.get_coalescer() is None)
    _check_httpclient("default: the hook is a no-op even if called directly", ms < 50, f"{ms:.1f}ms")

def t_flag_on():
    async def run():
        await hc.reset()
        hc.configure(batch_enable=True, batch_interval_s=0.12, batch_max_size=8,
                     batch_native_hints=["8500"])
        client = await hc._get_client()
        hooks = list(client.event_hooks.get("request") or [])

        t0 = time.perf_counter()
        await hc._batch_request_hook(_req("http://localhost:8500/v1/chat/completions"))
        native_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        await asyncio.gather(*[hc._batch_request_hook(_req()) for _ in range(3)])
        remote_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        await hc._batch_request_hook(_req("http://remote:9999/v1/models", method="GET"))
        get_ms = (time.perf_counter() - t0) * 1000

        groups = hc.get_coalescer().open_groups
        await hc.reset()
        hc.configure(batch_enable=False)
        return hooks, native_ms, remote_s, get_ms, groups

    hooks, native_ms, remote_s, get_ms, groups = asyncio.run(run())
    _check_httpclient("flag on: the hook is registered on the shared client",
          len(hooks) == 1 and hooks[0] is hc._batch_request_hook, repr(hooks))
    _check_httpclient("flag on: a native lane is not held", native_ms < 50, f"{native_ms:.1f}ms")
    _check_httpclient("flag on: concurrent non-native POSTs are held together",
          remote_s >= 0.10, f"{remote_s:.3f}s")
    _check_httpclient("flag on: the hold is bounded by the window", remote_s < 1.0, f"{remote_s:.3f}s")
    _check_httpclient("flag on: a GET is never held", get_ms < 50, f"{get_ms:.1f}ms")
    _check_httpclient("flag on: no window is left behind", groups == 0, str(groups))

def t_degrades_open():
    class _Boom:
        method = "POST"

        @property
        def content(self):
            raise RuntimeError("streaming body")

        @property
        def url(self):
            raise RuntimeError("no url")

    async def run():
        await hc.reset()
        hc.configure(batch_enable=True, batch_interval_s=5.0)
        await hc._get_client()
        t0 = time.perf_counter()
        await hc._batch_request_hook(_Boom())      # must not raise, must not hold
        ms = (time.perf_counter() - t0) * 1000
        await hc.reset()
        hc.configure(batch_enable=False)
        return ms
    ms = asyncio.run(run())
    _check_httpclient("degrade-open: an unreadable request is sent unheld", ms < 50, f"{ms:.1f}ms")

def t_server_reexport():
    try:
        import server
    except ModuleNotFoundError as exc:
        name = getattr(exc, "name", "") or ""
        here = os.path.dirname(os.path.abspath(__file__))
        if name.startswith("mios") or os.path.exists(os.path.join(here, f"{name}.py")):
            _check_httpclient(f"reexport: repo module {name} must import", False, repr(exc))
            return
        print(f"[SKIP] reexport: third-party dependency absent ({name})")
        return
    except (ImportError, NameError, AttributeError) as exc:
        _check_httpclient("reexport: server.py must be importable", False, repr(exc))
        return
    _check_httpclient("reexport: server._get_client IS the extracted one",
          server._get_client is hc._get_client)
    _check_httpclient("reexport: server._batch_request_hook IS the extracted one",
          server._batch_request_hook is hc._batch_request_hook)

def _main_httpclient():
    t_default_off()
    t_flag_on()
    t_degrades_open()
    t_server_reexport()
    print(f"\n{'ok' if _fails_httpclient == 0 else str(_fails_httpclient) + ' FAILED'}")
    return 1 if _fails_httpclient else 0


def _run_extra_httpclient():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_httpclient()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)

class TestFolded_httpclient(unittest.TestCase):
    def test_run_folded(self):
        rc = _run_extra_httpclient()
        self.assertIn(rc, (None, 0))



def _run_all_folded_httpx_suites():
    rc = _run_extra_httpclient()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    unittest.main()
