#!/usr/bin/env python3
# AI-hint: Standalone unit test for mios_portal (refactor R10) -- proves the moved portal logic works with stubs and no network/DB.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit test for mios_portal: auth round-trip + stats/asset shapes + swarm probe."""

import asyncio
import json
import sys
import types
from urllib.parse import urlencode

import mios_portal

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _body(resp):
    """Decode a fastapi JSONResponse rendered body into a dict."""
    return json.loads(bytes(resp.body).decode("utf-8"))

class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

class _FakeClient:
    """Minimal stand-in for an httpx.AsyncClient: returns a canned /models list
    on the first GET (MiOS is /v1-only; there is no legacy fallback path)."""
    def __init__(self, payload):
        self._payload = payload

    async def get(self, url, headers=None):
        return _FakeResp(200, self._payload)

class _FakeAsyncClient:
    """httpx.AsyncClient stand-in usable as ``async with AsyncClient(...) as c``
    -- the moved route logic opens its own client, so the test patches
    mios_portal.httpx.AsyncClient to return this."""
    def __init__(self, payload=None, status=200):
        self._payload = payload or {}
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, url, headers=None):
        return _FakeResp(self._status, self._payload)

class _FakeWS:
    """Minimal Starlette WebSocket stand-in: records the close code + whether the
    socket was ever accepted, so the term-bridge auth/port gates are observable
    without a live ttyd."""
    def __init__(self, cookie=None, headers=None):
        self.cookies = {} if cookie is None else {mios_portal.PORTAL_COOKIE: cookie}
        self.headers = {} if headers is None else headers
        self.closed = None
        self.accepted = "UNSET"

    async def close(self, code=1000):
        self.closed = code

    async def accept(self, subprotocol=None):
        self.accepted = subprotocol

class _ReqBody:
    """Request stand-in exposing an async .body() (login POST) + cookies."""
    def __init__(self, body=b"", cookie=None, headers=None):
        self._body = body
        self.cookies = {} if cookie is None else {mios_portal.PORTAL_COOKIE: cookie}
        self.headers = {} if headers is None else headers

    async def body(self):
        return self._body

def test_token_roundtrip():
    tok = mios_portal._portal_make_token("alice")
    check("token round-trips (valid)", mios_portal._portal_token_ok(tok) is True)
    check("tampered token rejected",
          mios_portal._portal_token_ok(tok + "x") is False)
    check("garbage token rejected",
          mios_portal._portal_token_ok("not-a-token") is False)
    check("empty token rejected", mios_portal._portal_token_ok("") is False)

class _Req:
    def __init__(self, cookie=None, headers=None):
        self.cookies = {} if cookie is None else {mios_portal.PORTAL_COOKIE: cookie}
        self.headers = {} if headers is None else headers

def test_authed_flag():
    saved = mios_portal.PORTAL_REQUIRE_LOGIN
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        good = mios_portal._portal_make_token(mios_portal.PORTAL_USER)
        check("authed True with valid cookie",
              mios_portal._portal_authed(_Req(good)) is True)
        check("authed False with no cookie",
              mios_portal._portal_authed(_Req(None)) is False)
        mios_portal.PORTAL_REQUIRE_LOGIN = False
        check("authed True when login disabled",
              mios_portal._portal_authed(_Req(None)) is True)
    finally:
        mios_portal.PORTAL_REQUIRE_LOGIN = saved

def test_manifest_shape():
    m = json.loads(mios_portal._PORTAL_MANIFEST)
    check("manifest has name/start_url/display",
          m.get("name") and m.get("start_url") == "/" and m.get("display") == "standalone")
    pngs = [i for i in m.get("icons", []) if i.get("type") == "image/png"]
    check("manifest declares PNG icons (192+512)",
          {i.get("sizes") for i in pngs} >= {"192x192", "512x512"})
    check("service worker is a JS string",
          isinstance(mios_portal._PORTAL_SW, str) and "addEventListener" in mios_portal._PORTAL_SW)
    check("svg icon string present",
          isinstance(mios_portal._PORTAL_ICON, str) and mios_portal._PORTAL_ICON.startswith("<svg"))

def test_read_asset_missing():
    check("missing asset degrades to b''",
          mios_portal._read_portal_asset("does-not-exist-xyz.bin") == b"")

def test_portal_assets_reexport():
    """The PWA assets live in mios_pipe.routing.portal_assets; the routes,
    server.py and the checks above read them as mios_portal.<name>. Pin that the
    re-export hands over the very same objects."""
    from mios_pipe.routing import portal_assets
    names = ("_PORTAL_ICON", "_read_portal_asset", "_PORTAL_ICON_192", "_PORTAL_ICON_512",
             "_PORTAL_MANIFEST", "_PORTAL_SW", "_PORTAL_LOGIN_HTML", "_IOSTEST_HTML")
    stale = [n for n in names if getattr(mios_portal, n, None) is not getattr(portal_assets, n)]
    check("assets: mios_portal re-exports portal_assets' own objects", not stale, str(stale))
    check("assets: the PNG icons are bytes (b'' when not baked)",
          isinstance(portal_assets._PORTAL_ICON_192, bytes)
          and isinstance(portal_assets._PORTAL_ICON_512, bytes))

def test_host_stats_shape():
    s = mios_portal._host_stats()
    check("host stats is a dict with cpu key",
          isinstance(s, dict) and "cpu" in s)

def test_swarm_probe():
    mios_portal.configure(
        probe_auth_headers=lambda ep: {},
        agent_lane=lambda cfg: "light")
    cfg = {"role": "council", "endpoint": "http://x:1/v1", "model": "m",
           "default": True, "fanout": True, "strengths": ["a"]}
    client = _FakeClient({"data": [{"id": "m1"}, {"id": "m2"}]})
    out = asyncio.run(mios_portal._portal_swarm_probe("node1", cfg, client))
    check("probe reports reachable", out.get("reachable") is True)
    check("probe lists live models", out.get("live_models") == ["m1", "m2"])
    check("probe carries name + lane",
          out.get("name") == "node1" and out.get("lane") == "light")
    check("probe surfaces role/model/default",
          out.get("role") == "council" and out.get("model") == "m" and out.get("default") is True)

def test_portal_stats_logic():
    saved = (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._PORTAL_SERVICES,
             mios_portal._podman_ps, mios_portal.httpx)
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        denied = asyncio.run(mios_portal.portal_stats_logic(_Req(None)))
        check("stats logic 401 without auth",
              getattr(denied, "status_code", None) == 401)

        mios_portal.PORTAL_REQUIRE_LOGIN = False
        mios_portal._PORTAL_SERVICES = [
            {"name": "OWUI", "port": 3030, "path": "/", "container_name": "owui",
             "kind": "", "local": "http://127.0.0.1:3030/"}]

        async def _fake_ps():
            return {"port": {3030: {"container": "owui", "state": "running",
                                    "image": "img"}}, "name": {}}
        mios_portal._podman_ps = _fake_ps
        mios_portal.httpx = types.SimpleNamespace(
            AsyncClient=lambda *a, **k: _FakeAsyncClient({"ok": 1}))
        body = _body(asyncio.run(mios_portal.portal_stats_logic(_Req(None))))
        check("stats logic returns host+services+ts",
              "host" in body and isinstance(body.get("services"), list)
              and "ts" in body)
        svc0 = (body.get("services") or [{}])[0]
        check("stats logic service is up + carries container state",
              svc0.get("ok") is True and svc0.get("container") == "owui"
              and svc0.get("state") == "running"
              and svc0.get("url", "").endswith(":3030/"))
    finally:
        (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._PORTAL_SERVICES,
         mios_portal._podman_ps, mios_portal.httpx) = saved

def test_portal_service_detail_logic():
    saved = (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._PORTAL_SERVICES,
             mios_portal._podman_ps, mios_portal.httpx,
             mios_portal._sanitize_tool_text)
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = False
        mios_portal._sanitize_tool_text = lambda s: s
        mios_portal._PORTAL_SERVICES = [
            {"name": "OWUI", "port": 3030, "path": "/", "container_name": "",
             "kind": "", "local": "http://127.0.0.1:3030/"}]

        async def _fake_ps():
            return {"port": {}, "name": {}}
        mios_portal._podman_ps = _fake_ps
        mios_portal.httpx = types.SimpleNamespace(
            AsyncClient=lambda *a, **k: _FakeAsyncClient({}))
        miss = asyncio.run(mios_portal.portal_service_detail_logic(9999, _Req(None)))
        check("service-detail 404 for unknown port",
              getattr(miss, "status_code", None) == 404)
        body = _body(asyncio.run(
            mios_portal.portal_service_detail_logic(3030, _Req(None))))
        check("service-detail returns shape (up, no logs)",
              body.get("name") == "OWUI" and body.get("port") == 3030
              and body.get("ok") is True and body.get("logs") == "")
    finally:
        (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._PORTAL_SERVICES,
         mios_portal._podman_ps, mios_portal.httpx,
         mios_portal._sanitize_tool_text) = saved

def test_portal_swarm_logic():
    saved = (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._AGENT_REGISTRY,
             mios_portal.httpx)
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = False
        mios_portal.configure(probe_auth_headers=lambda ep: {},
                              agent_lane=lambda cfg: "light")
        mios_portal._AGENT_REGISTRY = {
            "n1": {"role": "council", "endpoint": "http://x:1/v1", "model": "m",
                   "default": True}}
        mios_portal.httpx = types.SimpleNamespace(
            AsyncClient=lambda *a, **k: _FakeAsyncClient({"data": [{"id": "m1"}]}))
        body = _body(asyncio.run(mios_portal.portal_swarm_logic(_Req(None))))
        check("swarm logic returns probed roster",
              body.get("total") == 1 and body.get("up") == 1
              and body["agents"][0]["name"] == "n1"
              and body["agents"][0]["reachable"] is True)
    finally:
        (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._AGENT_REGISTRY,
         mios_portal.httpx) = saved

def test_portal_term_ws_logic():
    saved = (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._PORTAL_SERVICES)
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        mios_portal._PORTAL_SERVICES = [
            {"name": "Bash", "port": 7681, "kind": "terminal"}]
        ws = _FakeWS(None)
        asyncio.run(mios_portal.portal_term_ws_logic(ws, 7681))
        check("term ws closes 1008 without auth",
              ws.closed == 1008 and ws.accepted == "UNSET")
        mios_portal.PORTAL_REQUIRE_LOGIN = False
        ws2 = _FakeWS(None)
        asyncio.run(mios_portal.portal_term_ws_logic(ws2, 9999))
        check("term ws closes 1008 for unknown/non-terminal port",
              ws2.closed == 1008 and ws2.accepted == "UNSET")
    finally:
        (mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal._PORTAL_SERVICES) = saved

def test_portal_login_logic():
    saved = mios_portal.PORTAL_REQUIRE_LOGIN
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        bad = asyncio.run(mios_portal.portal_login_logic(
            _ReqBody(b"password=definitely-wrong")))
        check("login wrong pw -> /login?e=1",
              getattr(bad, "status_code", None) == 303
              and bad.headers.get("location") == "/login?e=1")
        good_body = urlencode({"password": mios_portal.PORTAL_PASSWORD}).encode()
        good = asyncio.run(mios_portal.portal_login_logic(_ReqBody(good_body)))
        check("login correct pw -> / + sets session cookie",
              getattr(good, "status_code", None) == 303
              and good.headers.get("location") == "/"
              and mios_portal.PORTAL_COOKIE in good.headers.get("set-cookie", ""))
    finally:
        mios_portal.PORTAL_REQUIRE_LOGIN = saved

def test_portal_login_page_logic():
    saved = mios_portal.PORTAL_REQUIRE_LOGIN
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        page = asyncio.run(mios_portal.portal_login_page_logic(_Req(None), 1))
        html = bytes(page.body).decode("utf-8")
        check("login page renders form + error when e=1",
              'action="/portal/login"' in html and "Incorrect password" in html)
        mios_portal.PORTAL_REQUIRE_LOGIN = False
        red = asyncio.run(mios_portal.portal_login_page_logic(_Req("x"), 0))
        check("login page when already authed -> /",
              getattr(red, "status_code", None) == 303
              and red.headers.get("location") == "/")
    finally:
        mios_portal.PORTAL_REQUIRE_LOGIN = saved

def test_portal_page_logic():
    saved = mios_portal.PORTAL_REQUIRE_LOGIN
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        red = asyncio.run(mios_portal.portal_page_logic(_Req(None)))
        check("portal page unauthed -> /login",
              getattr(red, "status_code", None) == 303
              and red.headers.get("location") == "/login")
        mios_portal.PORTAL_REQUIRE_LOGIN = False
        page = asyncio.run(mios_portal.portal_page_logic(_Req(None)))
        html = bytes(page.body).decode("utf-8")
        check("portal page authed -> dashboard HTML",
              getattr(page, "status_code", 200) == 200
              and "<!DOCTYPE html>" in html)
    finally:
        mios_portal.PORTAL_REQUIRE_LOGIN = saved

def main():
    test_token_roundtrip()
    test_authed_flag()
    test_manifest_shape()
    test_read_asset_missing()
    test_portal_assets_reexport()
    test_host_stats_shape()
    test_swarm_probe()
    test_portal_stats_logic()
    test_portal_service_detail_logic()
    test_portal_swarm_logic()
    test_portal_term_ws_logic()
    test_portal_login_logic()
    test_portal_login_page_logic()
    test_portal_page_logic()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
