#!/usr/bin/env python3
# AI-hint: Standalone unit test for mios_portal, mios_pipe.portal_status and mios_pipe.portal_spec -- portal logic with stubs and no network/DB, plus the /portal/status aggregate against planted snapshots.
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
    names = ("_PORTAL_STYLE", "_PORTAL_ICON", "_read_portal_asset", "_PORTAL_ICON_192", "_PORTAL_ICON_512",
             "_PORTAL_MANIFEST", "_PORTAL_SW", "_PORTAL_LOGIN_HTML", "_IOSTEST_HTML")
    stale = [n for n in names if getattr(mios_portal, n, None) is not getattr(portal_assets, n)]
    check("assets: mios_portal re-exports portal_assets' own objects", not stale, str(stale))
    check("assets: the PNG icons are bytes (b'' when not baked)",
          isinstance(portal_assets._PORTAL_ICON_192, bytes)
          and isinstance(portal_assets._PORTAL_ICON_512, bytes))
    check("assets: the complete static style is composed once into the dashboard HTML",
          portal_assets._PORTAL_STYLE.startswith("<style>") and portal_assets._PORTAL_STYLE.endswith("</style>")
          and mios_portal._PORTAL_HTML.count(portal_assets._PORTAL_STYLE) == 1
          and "__MIOS_PORTAL_STYLE__" not in mios_portal._PORTAL_HTML)

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

def test_portal_unicode_login_logic():
    saved = mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal.PORTAL_PASSWORD
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        mios_portal.PORTAL_PASSWORD = "fixture-café-猫-🔑"
        for json_login in (False, True):
            headers = {"accept": "application/json"} if json_login else {}
            for password, accepted in (("fixture-café-猫-🔑", True), ("fixture-cafè-猫-🔑", False)):
                form = urlencode({"password": password}).encode("utf-8")
                response = asyncio.run(mios_portal.portal_login_logic(_ReqBody(form, headers=headers)))
                if accepted and json_login:
                    result = _body(response)
                    ok = response.status_code == 200 and mios_portal._portal_token_ok(result.get("token"))
                elif accepted:
                    ok = (response.status_code == 303 and response.headers.get("location") == "/"
                          and mios_portal.PORTAL_COOKIE in response.headers.get("set-cookie", ""))
                elif json_login:
                    ok = response.status_code == 401 and _body(response) == {"error": "incorrect password"}
                else:
                    ok = response.status_code == 303 and response.headers.get("location") == "/login?e=1"
                check(f"Unicode login ({'JSON' if json_login else 'form'}): {'matches' if accepted else 'mismatch refused'}", ok)
        mios_portal.PORTAL_PASSWORD = "fixture-ascii-login"
        for form in (b"password=fixture-\xff", b"password=fixture-%FF", b"unrelated=field"):
            response = asyncio.run(mios_portal.portal_login_logic(_ReqBody(form, headers={"accept": "application/json"})))
            check("malformed or missing form password remains an auth failure",
                  response.status_code == 401 and _body(response) == {"error": "incorrect password"})
    finally:
        mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal.PORTAL_PASSWORD = saved

def test_portal_unencodable_login_logic():
    from unittest import mock
    # JSON decoding can produce a surrogate that valid UTF-8 form decoding cannot.
    malformed = json.loads(r'{"password":"\ud800"}')["password"]
    check("constant-time comparison accepts matching UTF-8 credentials",
          mios_portal._cteq("fixture-café", "fixture-café"))
    for left, right in ((malformed, "fixture-login"), ("fixture-login", malformed), (malformed, malformed)):
        check("unencodable credential input never matches", mios_portal._cteq(left, right) is False)
    saved = mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal.PORTAL_PASSWORD
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = True
        for submitted, configured in ((malformed, "fixture-login"), ("fixture-login", malformed)):
            mios_portal.PORTAL_PASSWORD = configured
            # Exercise the real handler at its parsed-password boundary without changing its form API.
            with mock.patch("urllib.parse.parse_qs", return_value={"password": [submitted]}):
                response = asyncio.run(mios_portal.portal_login_logic(
                    _ReqBody(b"password=fixture-login", headers={"accept": "application/json"})))
            check("JSON-decoded unencodable request/config credential returns 401",
                  response.status_code == 401 and _body(response) == {"error": "incorrect password"})
    finally:
        mios_portal.PORTAL_REQUIRE_LOGIN, mios_portal.PORTAL_PASSWORD = saved

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

def test_portal_v1_chat_page():
    page = asyncio.run(mios_portal.portal_v1_chat_page(_Req(None)))
    html = bytes(page.body).decode("utf-8")
    check("v1 chat page -> HTTP 200", getattr(page, "status_code", 200) == 200)
    check("v1 chat page -> contains SSOT colors",
          "#E7DFD3" in html and "#282262" in html and "#F35C15" in html)

def test_portal_login_reads_the_applied_operator_credential():
    """POSITIVE: the credstore value mios-credential apply writes is the login. NEGATIVE:
    nothing resolvable means no literal 'mios' and an empty form never logs in."""
    import os
    import tempfile
    from unittest import mock
    with tempfile.TemporaryDirectory(prefix="mios-portal-cred-") as d:
        for name, text in (("mios.portal.password", "fixture-mios-html-pass"),
                           ("mios.portal.user", "fixop")):
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_PORTAL_")}
        env["CREDENTIALS_DIRECTORY"] = d
        with mock.patch.dict(os.environ, env, clear=True):
            got = mios_portal._portal_login()
        check("portal login = the applied operator credential", got == ("fixture-mios-html-pass", "fixop"), str(got[1]))
    empty = {k: v for k, v in os.environ.items()
             if not k.startswith("MIOS_PORTAL_") and k != "CREDENTIALS_DIRECTORY"}
    with mock.patch.dict(os.environ, empty, clear=True), \
         mock.patch.object(sys.modules["mios_pipe.routing.portal"], "_PORTAL_TOML", {}):
        got = mios_portal._portal_login()
    check("no SSOT credential -> no literal fallback", got == ("", ""))
    saved = (mios_portal.PORTAL_PASSWORD, mios_portal.PORTAL_REQUIRE_LOGIN)
    try:
        mios_portal.PORTAL_PASSWORD, mios_portal.PORTAL_REQUIRE_LOGIN = "", True
        resp = asyncio.run(mios_portal.portal_login_logic(
            _ReqBody(urlencode({"password": ""}).encode(), headers={"accept": "application/json"})))
        check("empty credential refuses an empty login", getattr(resp, "status_code", 0) == 401)
    finally:
        mios_portal.PORTAL_PASSWORD, mios_portal.PORTAL_REQUIRE_LOGIN = saved

def test_front_door_probes_meet_the_real_routes():
    """The vendor portal route's probes against the real routers and auth middleware
    (api_require_auth on) refuse it; v2's three probes published it."""
    import importlib.machinery
    import importlib.util
    import os
    import threading
    import time
    import tomllib

    import uvicorn
    from fastapi import FastAPI
    from mios_pipe import auth as _auth
    from mios_pipe.federation.http_caps import http_caps_router
    from mios_pipe.routing.portal import portal_router

    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
    loader = importlib.machinery.SourceFileLoader(
        "mios_tailscale_sync_rt", os.path.join(root, "usr", "libexec", "mios", "mios-tailscale-sync"))
    sync = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(sync)
    app = FastAPI()
    app.include_router(portal_router)
    app.include_router(http_caps_router)
    _auth.configure(api_require_auth=True, auth_open_paths=frozenset(), auth_gated_prefixes=("/v1/", "/a2a"),
                    check_inbound_principal=lambda tok: None)
    app.middleware("http")(_auth.inbound_auth_mw)
    app.middleware("http")(_auth.peercred_auth_mw)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical", lifespan="off"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    deadline = time.time() + 30
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        port = srv.servers[0].sockets[0].getsockname()[1]
        with open(os.path.join(root, "usr", "share", "mios", "mios.toml"), "rb") as fh:
            data = tomllib.load(fh)
        data["tailscale"]["serve"]["enabled"] = True
        data["tailscale"]["serve"]["routes"]["portal"]["enabled"] = True
        data["ports"]["agent_pipe"] = port
        routes, errors = sync.render_routes(data)
        portal = next((r for r in routes if r["name"] == "portal"), None)
        check("the vendor portal route renders", portal is not None and not errors, errors)
        why = sync._auth_refusal(dict(portal))
        check("the vendor probes refuse a front door while a real route answers without a login",
              why.startswith("the upstream answered /v1 without a login"), why)
        v2 = dict(portal, probes=[f"http://127.0.0.1:{port}{p}" for p in ("/", "/v1/chat/completions", "/a2a")])
        why = sync._auth_refusal(v2)
        check("negative control: v2's three probes would have published it", why == "", why)
        for path, want in (("/skills/run", "open"), ("/dci/deliberate", "open"), ("/kg/lookup", "open"),
                           ("/iostest", "open"), ("/portal/config", "login"), ("/v1/chat/completions", "login")):
            got = sync._probe(f"http://127.0.0.1:{port}{path}")
            check(f"probe {path} reads {want}", got == want, got)
        got = sync._probe(f"ws://127.0.0.1:{port}/portal/term/0")
        check("the session websocket refuses an anonymous handshake", got == "login", got)
    finally:
        srv.should_exit = True
        th.join(10)
        _auth.configure(api_require_auth=False)

def main():
    test_portal_login_reads_the_applied_operator_credential()
    test_front_door_probes_meet_the_real_routes()
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
    test_portal_unicode_login_logic()
    test_portal_unencodable_login_logic()
    test_portal_login_page_logic()
    test_portal_page_logic()
    test_portal_v1_chat_page()
    test_portal_status_positive()
    test_portal_status_ready_from_mios_task()
    test_portal_status_unauthenticated()
    test_portal_status_degrades()
    test_portal_status_no_leak()
    test_portal_status_js_safe()
    test_portal_page_status_mount()
    test_portal_status_write_spec()
    test_portal_status_git_dirty_runs_as_owner()
    test_portal_status_git_dirty_skips_unreadable()
    test_portal_status_ssot_reloads()
    test_portal_status_user_tier_pins()
    test_portal_status_federation_peers()
    test_portal_status_one_live_frame_never_masks_a_dead_one()
    test_portal_status_services_need_an_answer_per_unit()
    test_portal_status_producer_units()
    test_portal_sw_never_caches_status()
    test_portal_status_cadence_relations()
    test_portal_status_writer_stays_off_fastapi()
    test_portal_status_failed_aggregate_keeps_polling()
    test_portal_status_poll_off_the_loop()
    test_portal_status_agent_rows_cap()
    test_portal_status_request_timeout_from_ssot()
    test_portal_status_untrusted_publisher_invents_no_peers()
    test_portal_status_same_agent_id_under_two_publishers()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

# ---- GET /portal/status + /portal/status.js (mios_pipe.portal_status) ----------
# The planted frames copy the shape a real `mios-mcp-server --agent-cli --observe
# --publish` run wrote (mios-agent-relay's mios.agents.observation.v1), extended
# with a subagent, a tmux slot and hostile fields.
import copy as _copy  # noqa: E402
import os as _os  # noqa: E402
import pwd as _pwd  # noqa: E402
import re as _re  # noqa: E402
import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import tempfile as _tempfile  # noqa: E402
import time as _time  # noqa: E402
import tomllib as _tomllib  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from mios_pipe import portal_spec, portal_status  # noqa: E402

_ROOT = _Path(__file__).resolve().parents[4]
_STATUS_SCHEMA = json.loads((_ROOT / "usr/lib/mios/schemas/portal_status.schema.json").read_text())
_VENDOR = _tomllib.loads((_ROOT / "usr/share/mios/mios.toml").read_text())
_SHA = "0123456789abcdef0123456789abcdef01234567"
_LEAK = "status-private-marker-7f3a"
_RELAY = "/home/operator/.local/state/mios/agent-relay"

def _frame(now, **extra):
    agents = [
        {"agent_id": "coordinator", "kind": "claude", "label": "Coordinator", "expires": now + 600, "online": True,
         "pending": 2, "parent_agent_id": "", "session_name": "work", "node": "blade-a", "runtime_pid": 4242,
         "runtime_start": 1, "source": _RELAY},
        {"agent_id": "worker-1", "kind": "codex", "label": "Worker", "expires": now - 5, "online": False,
         "pending": 0, "parent_agent_id": "coordinator", "session_name": "work", "node": "blade-a",
         "runtime_pid": None, "runtime_start": None, "source": _RELAY},
        {"agent_id": "idle", "kind": "gemini", "label": "Idle", "expires": 0, "online": False, "pending": 1,
         "parent_agent_id": "", "session_name": "", "node": "", "runtime_pid": None, "runtime_start": None,
         "source": _RELAY}]
    frame = {"schema": "mios.agents.observation.v1", "ts": now, "agent_total": 3, "online_total": 1,
             "message_total": 1, "agents": agents,
             "messages": [{"message_id": "m1", "from": "idle", "to": "coordinator", "status": "queued",
                           "created": now, "received": None}],
             "panes": [], "sessions": [], "subagents": [dict(agents[1], evidence="reported parent")],
             "sources": [{"source": _RELAY, "state": "ready", "kind": "relay"}], "errors": []}
    frame.update(extra)
    return frame

def _plant(tmp, now, frame=None):
    """A status tree: snapshot dir (agents + spec), a work tree with .git, a goal, a GOALS doc, a ledger."""
    root, snap = _Path(tmp, "repo"), _Path(tmp, "status")
    (root / ".git/refs/heads").mkdir(parents=True)
    (root / ".devloop").mkdir()
    snap.mkdir()
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git/refs/heads/main").write_text(_SHA + "\n")
    goal = {"objective": "Ship the status view", "stopping_condition": "the gates pass", "status": "IN_PROGRESS",
            "created_at": "2026-01-01T00:00:00", "completed_at": None,
            "criteria": [{"id": "C-01", "description": "gate", "criterion_type": "test", "command": "false",
                          "passed": False, "details": None},
                         {"id": "C-02", "description": "clean", "criterion_type": "git_clean", "command": None,
                          "passed": True, "details": "ok"},
                         {"id": "C-03", "description": "lint", "criterion_type": "test", "command": "false",
                          "passed": False, "details": "exit code 1"}],
            "non_goals": ["remote nodes"]}
    (root / ".devloop/goal_state.json").write_text(json.dumps(goal))
    (root / ".devloop/GOALS-x.md").write_text(
        "# X goal\n\n| ID | Condition | Evidence |\n|----|-----------|----------|\n"
        "| SC-1 | **First.** a \\| b | e |\n| SC-2 | Second | e |\n")
    rows = [{"id": f"task_{n}", "number": n, "title": f"T{n}", "status": s, "priority": "P1", "owner": ""}
            for n, s in enumerate(["pending", "pending", "in_progress", "completed"], 1)]
    (root / "tasks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (snap / f"agents-{_os.getuid()}.json").write_text(json.dumps(frame or _frame(now)))
    (snap / "spec.json").write_text(json.dumps(
        {"schema": "mios.status.spec.v1", "ts": now, "git_dirty": 2,
         "bootc": {"state": "not-bootc", "booted_image": None, "booted_digest": None,
                   "staged_image": None, "rollback_image": None}}))
    for path in snap.iterdir():
        _os.chmod(path, 0o640)
    doc = _copy.deepcopy(_VENDOR)
    doc["portal"]["status"].update(snapshot_dir=str(snap), repo_root=str(root))
    doc["identity"]["username"] = _pwd.getpwuid(_os.getuid()).pw_name  # the relay owner is this test's uid
    return root, snap, doc

class _StatusEnv:
    """Point portal_status at a planted doc; PATH holds only what the case names."""
    def __init__(self, doc, path_dirs=(), spec_uid=None, login=False):
        self.doc, self.path_dirs, self.spec_uid, self.login = doc, path_dirs, spec_uid, login

    def __enter__(self):
        self.saved = (portal_status._ssot, portal_spec._writer_ssot, portal_status._SPEC_UID,
                      _os.environ.get("PATH"), mios_portal.PORTAL_REQUIRE_LOGIN)
        self.empty = _tempfile.mkdtemp(prefix="mios-status-path-")
        portal_status._ssot = portal_spec._writer_ssot = lambda: self.doc
        portal_status._SPEC_UID = _os.getuid() if self.spec_uid is None else self.spec_uid
        _os.environ["PATH"] = _os.pathsep.join([*self.path_dirs, self.empty])
        mios_portal.PORTAL_REQUIRE_LOGIN = self.login
        for memo in (portal_status._CACHE, portal_status._LEDGER_MEMO, portal_status._TOOL_MEMO):
            memo.clear()
        return self

    def __exit__(self, *_a):
        (portal_status._ssot, portal_spec._writer_ssot, portal_status._SPEC_UID, path,
         mios_portal.PORTAL_REQUIRE_LOGIN) = self.saved
        if path is None:
            _os.environ.pop("PATH", None)
        else:
            _os.environ["PATH"] = path
        _shutil.rmtree(self.empty, ignore_errors=True)
        return False

def _status(req=None):
    portal_status._CACHE.clear()
    resp = asyncio.run(portal_status.status_logic(req or _Req(None)))
    return resp, _body(resp)

def _schema_errors(body, schema=None):
    import jsonschema
    validator = jsonschema.Draft202012Validator(schema or _STATUS_SCHEMA["schema"])
    return "; ".join(sorted(e.message for e in validator.iter_errors(body))[:3])

def _src(body, name):
    return next(s for s in body["sources"] if s["name"] == name)

def test_portal_status_positive():
    now = int(_time.time())
    with _tempfile.TemporaryDirectory() as tmp:
        root, _, doc = _plant(tmp, now)
        with _StatusEnv(doc):
            resp, body = _status()
    check("status: authed request -> 200 + no-store", resp.status_code == 200
          and resp.headers.get("cache-control") == "no-store")
    errors = _schema_errors(body)
    check("status: body validates against mios_portal_status", not errors, errors)
    check("status: object + schema_version", body["object"] == "mios.portal.status" and body["schema_version"] == "1")
    a = body["agents"]
    check("status: agents total/online/pending from the frame",
          (a["state"], a["total"], a["online"], a["pending"]) == ("ok", 3, 1, 3), str(a)[:200])
    subs = [r for r in a["rows"] if r["subagent"]]
    check("status: the reported subagent appears once, parented",
          [(r["agent_id"], r["parent_agent_id"]) for r in subs] == [("worker-1", "coordinator")], str(subs))
    check("status: a local relay path reads as source 'local'", {r["source"] for r in a["rows"]} == {"local"}
          and a["sources"] == [{"source": "local", "presence": "local"}])
    g = body["goal"]
    check("status: a never-evaluated criterion (details null) reads null; evaluated ones keep their flag",
          [(c["id"], c["passed"]) for c in g["criteria"]] == [("C-01", None), ("C-02", True), ("C-03", False)],
          str(g["criteria"]))
    check("status: goal is flagged as recorded, never evaluated", g["evaluated"] == "recorded"
          and g["status"] == "IN_PROGRESS" and g["non_goals"] == ["remote nodes"])
    check("status: declared targets parsed from GOALS*.md",
          body["goals"][0]["title"] == "X goal"
          and [c["id"] for c in body["goals"][0]["conditions"]] == ["SC-1", "SC-2"]
          and body["goals"][0]["conditions"][0]["condition"] == "First. a | b", str(body["goals"])[:200])
    t = body["tasks"]
    check("status: task histogram from the ledger",
          t["by_status"] == {"pending": 2, "in_progress": 1, "completed": 1, "incomplete": 0, "cancelled": 0,
                             "other": 0} and t["total"] == 4 and [r["id"] for r in t["in_progress"]] == ["task_3"])
    n = body["nodes"][0]
    check("status: local node spec from SSOT + .git + spec.json",
          n["local"] and n["git_head"] == _SHA and n["git_branch"] == "main" and n["git_dirty"] == 2
          and n["bootc_state"] == "not-bootc" and n["mios_version"] == _VENDOR["meta"]["mios_version"]
          and n["state"] == "ok", str(n)[:300])
    check("status: services come from [greenboot].critical_services",
          [s["name"] for s in body["services"]] == _VENDOR["greenboot"]["critical_services"])
    check("status: every source detail is from the fixed vocabulary",
          all(s["detail"] in portal_status.DETAILS for s in body["sources"]))
    check("status: refresh_s/stale_after_s come from [portal.status]",
          body["refresh_s"] == _VENDOR["portal"]["status"]["refresh_s"]
          and body["stale_after_s"] == _VENDOR["portal"]["status"]["stale_after_s"])
    # packed-refs variant: the loose ref is gone, the commit resolves from packed-refs.
    with _tempfile.TemporaryDirectory() as tmp:
        root, _, doc = _plant(tmp, now)
        (root / ".git/refs/heads/main").unlink()
        (root / ".git/packed-refs").write_text(f"# pack-refs with: peeled\n{_SHA} refs/heads/main\n")
        with _StatusEnv(doc):
            _, body = _status()
    check("status: git head resolves from packed-refs", body["nodes"][0]["git_head"] == _SHA)

def test_portal_status_ready_from_mios_task():
    """ready/overrides come from the real mios-task over this checkout (integration, not a fake)."""
    exe = _shutil.which("mios-task")
    if not exe:
        print("[SKIP] status: mios-task is not on PATH; ready/overrides integration not run")
        return
    expected = json.loads(_subprocess.run([exe, "check", "--json", "--root", str(_ROOT)],
                                          capture_output=True, text=True, timeout=30).stdout)["overrides"]
    portal_status._TOOL_MEMO.clear()
    ready, overrides = portal_status._task_tool(str(_ROOT), 3, ("integration",),
                                                _VENDOR["portal"]["status"]["request_timeout_s"])
    check("status: ready rows from `mios-task next --json`", 0 < len(ready) <= 3
          and all(set(r) == {"id", "number", "title", "priority", "owner"} for r in ready), str(ready)[:200])
    check("status: overrides from `mios-task check --json`", overrides == expected, f"{overrides} != {expected}")

def test_portal_status_unauthenticated():
    now = int(_time.time())
    with _tempfile.TemporaryDirectory() as tmp:
        _, _, doc = _plant(tmp, now)
        with _StatusEnv(doc, login=True):
            resp, body = _status(_Req(None))
            js = asyncio.run(portal_status.status_js_logic(_Req(None)))
            good, good_body = _status(_Req(mios_portal._portal_make_token(mios_portal.PORTAL_USER)))
    check("status: 401 without a session and no agents in the body", resp.status_code == 401 and "agents" not in body)
    check("status.js: 401 without a session", getattr(js, "status_code", None) == 401)
    check("status: a valid session cookie passes the same gate", good.status_code == 200 and "agents" in good_body)

def test_portal_status_degrades():
    now = int(_time.time())
    uid = _os.getuid()

    def case(name, mutate, expect, spec_uid=None, path_dirs=()):
        with _tempfile.TemporaryDirectory() as tmp:
            root, snap, doc = _plant(tmp, now)
            mutate(root, snap, doc)
            with _StatusEnv(doc, path_dirs=path_dirs, spec_uid=spec_uid):
                resp, body = _status()
        errors = _schema_errors(body)
        ok = resp.status_code == 200 and not errors and expect(body)
        check(f"status degrades: {name}", ok, errors or json.dumps(body["sources"])[:300])

    def write(path, text, mode=0o640):
        path.write_text(text)
        _os.chmod(path, mode)
    agents = lambda snap: snap / f"agents-{uid}.json"
    case("missing snapshot_dir -> agents/spec absent",
         lambda r, s, d: d["portal"]["status"].update(snapshot_dir=str(s) + "-missing"),
         lambda b: _src(b, "agents")["state"] == "absent" and _src(b, "spec")["state"] == "absent"
         and b["agents"]["state"] == "absent")
    case("corrupt agents JSON -> corrupt",
         lambda r, s, d: write(agents(s), "{not json"),
         lambda b: _src(b, "agents")["state"] == "corrupt" and b["agents"]["rows"] == [])
    case("wrong frame schema -> corrupt",
         lambda r, s, d: write(agents(s), json.dumps(_frame(now, schema="other.v9"))),
         lambda b: _src(b, "agents")["state"] == "corrupt")
    case("over [mcp.agents].max_state_bytes -> refused",
         lambda r, s, d: d["mcp"]["agents"].update(max_state_bytes=64),
         lambda b: _src(b, "agents")["state"] == "refused" and _src(b, "agents")["detail"] == portal_status.D_OVERSIZE)
    case("owner uid != the uid in the name -> refused",
         lambda r, s, d: agents(s).rename(s / f"agents-{uid + 1}.json"),
         lambda b: _src(b, "agents")["state"] == "refused" and _src(b, "agents")["detail"] == portal_status.D_OWNER)
    case("world-writable snapshot -> refused",
         lambda r, s, d: _os.chmod(agents(s), 0o646),
         lambda b: _src(b, "agents")["state"] == "refused" and _src(b, "agents")["detail"] == portal_status.D_WORLD)
    case("group-writable snapshot (the directory's group holds other publishers) -> refused",
         lambda r, s, d: _os.chmod(agents(s), 0o660),
         lambda b: _src(b, "agents")["state"] == "refused" and _src(b, "agents")["detail"] == portal_status.D_GROUP)
    case("symlinked snapshot -> refused",
         lambda r, s, d: (agents(s).rename(s / "real.json"), agents(s).symlink_to(s / "real.json")),
         lambda b: _src(b, "agents")["state"] == "refused")
    case("frame older than stale_after_s -> stale",
         lambda r, s, d: write(agents(s), json.dumps(_frame(now - 10000))),
         lambda b: _src(b, "agents")["state"] == "stale" and b["agents"]["age_s"] >= 10000
         and b["agents"]["total"] == 3)
    case("spec.json not owned by the trusted uid -> refused, bootc unknown",
         lambda r, s, d: None,
         lambda b: _src(b, "spec")["state"] == "refused" and b["nodes"][0]["bootc_state"] == "unknown"
         and b["nodes"][0]["state"] == "unknown", spec_uid=uid + 1)
    case("missing goal_state -> goal null + absent",
         lambda r, s, d: (r / ".devloop/goal_state.json").unlink(),
         lambda b: b["goal"] is None and _src(b, "goal")["state"] == "absent")
    case("a bad ledger line is counted, the rest kept",
         lambda r, s, d: (r / "tasks.jsonl").write_text((r / "tasks.jsonl").read_text() + "not json\n"),
         lambda b: b["tasks"]["invalid_lines"] == 1 and b["tasks"]["total"] == 4
         and _src(b, "tasks")["detail"] == portal_status.D_INVALID)
    case("mios-task not on PATH -> ready [] and overrides null",
         lambda r, s, d: None,
         lambda b: b["tasks"]["ready"] == [] and b["tasks"]["overrides"] is None)
    case("no [portal.status] -> every source unavailable, refresh_s 0",
         lambda r, s, d: d["portal"].pop("status"),
         lambda b: b["refresh_s"] == 0 and b["nodes"] == []
         and {x["state"] for x in b["sources"]} == {"unavailable"})
    case("an out-of-range refresh_s fails closed",
         lambda r, s, d: d["portal"]["status"].update(refresh_s=0),
         lambda b: {x["state"] for x in b["sources"]} == {"unavailable"})

def _leaks(body):
    text = json.dumps(body)
    return [m for m in (_LEAK, "/home/operator", "100.64.0.9") if m in text]

def test_portal_status_no_leak():
    now = int(_time.time())
    frame = _frame(now)
    frame["agents"][0].update(token=_LEAK, token_hash=_LEAK, LoginName=_LEAK, TailscaleIPs=["100.64.0.9"],
                              label="<img src=x onerror=1>")
    frame["messages"][0]["body"] = _LEAK
    frame["panes"] = [{"socket": "/run/user/0/tmux-0/mios-human", "session": "$1", "session_name": "work",
                       "window": "0", "pane": "%7", "pid": 9, "command": _LEAK, "dead": False,
                       "agent_kind": "codex", "role": "W1", "parent_pane": "%1", "slot": "1"}]
    frame["subagents"].append(dict(frame["panes"][0], evidence="tmux slot"))
    frame["sessions"] = [{"session_id": "s", "session_name": "work", "socket": _LEAK, "kind": "tmux",
                          "state": "Detected", "evidence": _LEAK}]
    with _tempfile.TemporaryDirectory() as tmp:
        _, _, doc = _plant(tmp, now, frame)
        with _StatusEnv(doc):
            _, body = _status()
            original = portal_status._project_agents

            def widened(frames, cap, *stale):  # mutation: let raw panes through the whitelist
                rows, *rest = original(frames, cap, *stale)
                return (rows + [dict(p) for _, f in frames for p in f.get("panes", [])], *rest)
            portal_status._project_agents = widened
            try:
                _, leaky = _status()
            finally:
                portal_status._project_agents = original
    check("status no-leak: no token, digest, body, command, login, peer IP or home path", not _leaks(body),
          str(_leaks(body)))
    check("status no-leak: a hostile label survives only as JSON text",
          any(r["label"] == "<img src=x onerror=1>" for r in body["agents"]["rows"]))
    check("status no-leak: the tmux slot appears as a pane subagent without its command",
          any(r["agent_id"] == "pane:%7" and r["subagent"] and r["online"] is None for r in body["agents"]["rows"]))
    check("status no-leak (mutation): widening the whitelist to pass panes is caught", _LEAK in _leaks(leaky))

def _js_unsafe(js):
    """Reasons the view script is unsafe: innerHTML fed anything but '', a host/port literal, or a
    status fetch that is not relative + no-store."""
    bad = [m.strip() for m in _re.findall(r"innerHTML\s*=\s*([^;]+)", js) if m.strip() not in ('""', "''")]
    bad += _re.findall(r"https?://|localhost|127\.0\.0\.1|:\d{2,5}\b", js)
    if 'fetch("/portal/status",{credentials:"same-origin",cache:"no-store"})' not in js:
        bad.append("status fetch is not relative/no-store")
    return bad

_JS_NODE = r'''
const vm = require('vm'), fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const events = {fetch: [], timers: [], innerHTML: []};
const answers = input.answers || [{status: 200, body: input.body}];
let pending = null;
function node(tag) {
  const n = {tag, children: [], className: '', style: {}, hidden: tag === 'statussec', _t: '',
    appendChild(c) { this.children.push(c); return c; }};
  Object.defineProperty(n, 'textContent', {get() { return this._t + this.children.map(c => c.textContent).join(''); },
    set(v) { this._t = String(v); this.children = []; }});
  Object.defineProperty(n, 'innerHTML', {get() { return ''; }, set(v) { events.innerHTML.push(String(v)); }});
  return n;
}
const ids = {statussec: node('statussec'), 'mios-ver': node('sup')};
const ctx = {console, Date, Math, String,
  document: {hidden: false, getElementById: id => ids[id] || null, createElement: node, addEventListener() {}},
  setTimeout: (fn, ms) => { events.timers.push(ms); pending = fn; return 1; }, clearTimeout() {},
  fetch: async (url, options) => { const a = answers[Math.min(events.fetch.length, answers.length - 1)];
    events.fetch.push({url, options});
    if (a.error) throw new Error('offline');
    return {ok: a.status >= 200 && a.status < 300, status: a.status, json: async () => a.body}; }};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(input.js, ctx);
const settle = () => new Promise(r => setImmediate(() => setImmediate(() => setImmediate(r))));
const sec = ids.statussec;
const snap = () => { const head = sec.children[0], grid = sec.children[1];
  return {hidden: sec.hidden, text: sec.textContent, ver: ids['mios-ver'].textContent,
          note: head && head.children[1] ? head.children[1].textContent : '', opacity: grid ? grid.style.opacity || '' : ''}; };
(async () => {
  await settle();
  const steps = [snap()];
  for (let i = 1; i < answers.length; i++) { const fn = pending; pending = null; if (fn) fn(); await settle(); steps.push(snap()); }
  process.stdout.write(JSON.stringify(Object.assign({events, steps}, steps[0])));
})();
'''

def _served_js(refresh_s=None):
    """status.js as the Portal serves it: the first-poll retry cap set from [portal.status].refresh_s."""
    return portal_status.status_js(_VENDOR["portal"]["status"]["refresh_s"] if refresh_s is None else refresh_s)

def test_portal_status_js_safe():
    js = _served_js()
    check("status.js: the retry-cap placeholder is present exactly once in the source",
          portal_status.STATUS_JS.count(portal_status._RETRY) == 1)
    check("status.js: served with RETRY_MAX_S = [portal.status].refresh_s",
          f"var RETRY_MAX_S={_VENDOR['portal']['status']['refresh_s']};" in js and portal_status._RETRY not in js)
    check("status.js: no innerHTML data, no host/port literal, relative no-store fetch", not _js_unsafe(js),
          str(_js_unsafe(js)))
    check("status.js (negative): a planted innerHTML sink is caught",
          bool(_js_unsafe(js.replace('sec.textContent=""', "sec.innerHTML=j.node"))))
    check("status.js (negative): a planted absolute URL is caught",
          bool(_js_unsafe(js.replace('"/portal/status"', '"http://localhost:8640/portal/status"'))))
    with _StatusEnv(_copy.deepcopy(_VENDOR)):
        served = asyncio.run(portal_status.status_js_logic(_Req(None)))
    check("status.js: served as JavaScript with the SSOT retry cap", served.media_type == "application/javascript"
          and bytes(served.body).decode() == js)
    check("status.js: no agent-row literal cap in the renderer (the server caps rows at agent_rows)",
          ".slice(0,12)" not in js)
    if not _shutil.which("node"):
        print("[SKIP] status.js: node is not on PATH; DOM execution not run")
        return
    now = int(_time.time())
    with _tempfile.TemporaryDirectory() as tmp:
        frame = _frame(now)
        frame["agents"][0]["label"] = "<img src=x onerror=1>"
        _, _, doc = _plant(tmp, now, frame)
        with _StatusEnv(doc):
            _, body = _status()
    out = _subprocess.run(["node", "-e", _JS_NODE], input=json.dumps({"js": js, "body": body}),
                          capture_output=True, text=True, timeout=30)
    run = json.loads(out.stdout or "{}")
    events = run.get("events", {})
    check("status.js (DOM): one relative no-store fetch, section shown, version set",
          [e["url"] for e in events.get("fetch", [])] == ["/portal/status"]
          and events["fetch"][0]["options"] == {"credentials": "same-origin", "cache": "no-store"}
          and run.get("hidden") is False and run.get("ver") == _VENDOR["meta"]["mios_version"], out.stderr[-300:])
    check("status.js (DOM): polls at the payload's refresh_s", events.get("timers") == [body["refresh_s"] * 1000])
    check("status.js (DOM): hostile label rendered as text, innerHTML never written",
          "<img src=x onerror=1>" in run.get("text", "") and events.get("innerHTML") == [])
    check("status.js (DOM): a null criterion reads 'not evaluated', a false one 'fail'",
          "C-01not evaluated · gate" in run.get("text", "") and "C-03fail · lint" in run.get("text", ""),
          run.get("text", "")[:400])
    check("status.js (DOM): a fresh payload is not marked stale",
          "stale since" not in run.get("note", "") and run.get("opacity") == "" and body["node"] in run.get("note", ""))
    _status_js_staleness(js, body)

def _portal_lines(text):
    return len(text.splitlines())

def test_portal_page_status_mount():
    saved = mios_portal.PORTAL_REQUIRE_LOGIN
    try:
        mios_portal.PORTAL_REQUIRE_LOGIN = False
        html = bytes(asyncio.run(mios_portal.portal_page_logic(_Req(None))).body).decode("utf-8")
    finally:
        mios_portal.PORTAL_REQUIRE_LOGIN = saved
    check("portal page: mounts /portal/status.js + #statussec + #mios-ver",
          '<script src="/portal/status.js" defer></script>' in html and 'id="statussec"' in html
          and 'id="mios-ver"' in html)
    check("portal page: the build18 literal is gone", "build18" not in html)
    text = (_ROOT / "usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py").read_text()
    registered = next(row["lines"] for row in _VENDOR["refactor"]["oversize"]
                      if row["path"] == "mios_pipe/routing/portal.py")
    check("portal.py source length matches its SSOT shrink-only register",
          _portal_lines(text) == registered, str(_portal_lines(text)))
    check("portal.py length check (negative): one planted line exceeds the registered cap",
          _portal_lines(text + "# x\n") > registered)
    assets = (_ROOT / "usr/lib/mios/agent-pipe/mios_pipe/routing/portal_assets.py").read_text()
    check("portal assets remain below the SSOT submodule ceiling",
          _portal_lines(assets) <= _VENDOR["refactor"]["max_lines"], str(_portal_lines(assets)))

def _git_fixture_readable(repo):
    """Only synthetic repos: the throwaway account must read them even under an inherited umask 077."""
    for path in (repo, *repo.rglob("*")):
        if path.is_symlink():
            raise AssertionError("synthetic Git fixture contains a symlink")
        _os.chmod(path, 0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)

def test_portal_status_write_spec():
    git = _shutil.which("git")
    with _tempfile.TemporaryDirectory() as tmp:
        _os.chmod(tmp, 0o755)  # under root, git runs as the throwaway account, which must reach the repo
        repo, snap, stubs = _Path(tmp, "repo"), _Path(tmp, "status"), _Path(tmp, "bin")
        for d in (repo, snap, stubs):
            d.mkdir()
        doc = _copy.deepcopy(_VENDOR)
        doc["portal"]["status"].update(snapshot_dir=str(snap), repo_root=str(repo))
        if git:
            g = [git, "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
            _subprocess.run(g + ["init", "-q"], check=True)
            (repo / "a").write_text("1")
            _subprocess.run(g + ["add", "a"], check=True)
            _subprocess.run(g + ["commit", "-qm", "a"], check=True)
            (repo / "a").write_text("2")
            _git_fixture_readable(repo)
        tools = [str(_Path(git).parent)] if git else []
        with _StatusEnv(doc, path_dirs=tools):
            rc = portal_spec.main(["--write-spec"])
            spec_path = snap / "spec.json"
            spec = json.loads(spec_path.read_text()) if spec_path.exists() else {}
            mode = spec_path.stat().st_mode & 0o777 if spec_path.exists() else None
            # a bootc on PATH answering a booted host, then one that fails
            (stubs / "bootc").write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(
                {"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost", "status": {
                    "booted": {"image": {"image": {"image": "ghcr.io/example/mios:latest", "transport": "registry"},
                                         "imageDigest": "sha256:" + "a" * 64}},
                    "staged": None, "rollback": {"image": {"image": {"image": "ghcr.io/example/mios:prev"}}}}})
                + "\nEOF\n")
            _os.chmod(stubs / "bootc", 0o755)
            _os.environ["PATH"] = _os.pathsep.join([str(stubs), *tools])
            booted = portal_spec._bootc_probe(doc["portal"]["status"]["probe_timeout_s"])
            (stubs / "bootc").write_text("#!/bin/sh\nexit 1\n")
            failing = portal_spec._bootc_probe(doc["portal"]["status"]["probe_timeout_s"])
            doc["portal"]["status"]["snapshot_dir"] = str(snap / "missing")
            bad_rc = portal_spec.main(["--write-spec"])
            usage_rc = portal_spec.main([])
        strays = [p.name for p in snap.iterdir() if p.name != "spec.json"]
    errors = _schema_errors(spec, _STATUS_SCHEMA["schema"]["$defs"]["spec_snapshot"])
    check("write-spec: exit 0, spec.json mode 0640, mios.status.spec.v1", rc == 0 and mode == 0o640
          and spec.get("schema") == "mios.status.spec.v1" and not errors, errors or str(spec))
    check("write-spec: no bootc on PATH -> not-bootc", spec.get("bootc", {}).get("state") == "not-bootc")
    if git:
        check("write-spec: git_dirty counts the modified tracked file", spec.get("git_dirty") == 1, str(spec))
    check("write-spec: a booted host's images are parsed",
          booted["state"] == "booted" and booted["booted_image"] == "ghcr.io/example/mios:latest"
          and booted["booted_digest"] == "sha256:" + "a" * 64 and booted["staged_image"] is None
          and booted["rollback_image"] == "ghcr.io/example/mios:prev", str(booted))
    check("write-spec: a failing bootc -> unavailable", failing["state"] == "unavailable")
    check("write-spec (negative): an unwritable snapshot_dir exits non-zero, no stray tmp",
          bad_rc == 1 and strays == [], f"{bad_rc} {strays}")
    check("write-spec: unknown arguments -> usage exit 2", usage_rc == 2)

def _status_js_run(js, answers):
    out = _subprocess.run(["node", "-e", _JS_NODE], input=json.dumps({"js": js, "answers": answers}),
                          capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout or "{}"), out.stderr[-300:]

def _status_js_staleness(js, body):
    """A failed poll after a good one, and a payload older than stale_after_s, both read 'stale since'."""
    run, err = _status_js_run(js, [{"status": 200, "body": body}, {"status": 502, "body": None},
                                   {"status": 200, "body": body}])
    steps = run.get("steps") or [{}, {}, {}]
    check("status.js (DOM): a failed poll after a good one dims the cards and reads 'stale since'",
          "stale since" in steps[1].get("note", "") and steps[1].get("opacity") == "0.5"
          and run.get("events", {}).get("timers") == [body["refresh_s"] * 1000] * 3, err or json.dumps(steps[1])[:300])
    check("status.js (DOM): the next good poll clears the stale mark",
          "stale since" not in steps[2].get("note", "") and steps[2].get("opacity") == "", json.dumps(steps[2])[:300])
    refresh = _VENDOR["portal"]["status"]["refresh_s"]
    answers = [{"error": True}, {"status": 502, "body": None}, {"error": True}, {"error": True}, {"error": True},
               {"error": True}, {"status": 200, "body": body}]
    run, err = _status_js_run(js, answers)
    steps = run.get("steps") or [{}] * len(answers)
    backoff = [min(2 ** n, refresh) * 1000 for n in range(6)]
    check("status.js (DOM): a first poll that fails stays hidden and retries with a backoff capped at refresh_s",
          run.get("events", {}).get("timers") == backoff + [refresh * 1000]
          and all(s.get("hidden") is True for s in steps[:6]), err or json.dumps(run.get("events", {}).get("timers")))
    check("status.js (DOM): the first good poll after failures shows the section", steps[6].get("hidden") is False
          and "stale since" not in steps[6].get("note", ""), json.dumps(steps[6])[:300])
    run, err = _status_js_run(portal_status.STATUS_JS, [{"error": True}])
    check("status.js (negative): unserved, RETRY_MAX_S=0 retries nothing (the cap comes from the server)",
          run.get("hidden") is True and run.get("events", {}).get("timers") == [], err)
    failed = portal_status._degraded(body["ts"], body["node"], portal_status.D_FAILED, refresh)
    run, err = _status_js_run(js, [{"status": 200, "body": failed}, {"status": 200, "body": body}])
    steps = run.get("steps") or [{}, {}]
    check("status.js (DOM): a failed-aggregate body keeps polling and the next good body replaces it",
          run.get("events", {}).get("timers") == [refresh * 1000] * 2 and "collector failed" in steps[0].get("text", "")
          and "collector failed" not in steps[1].get("text", ""), err or json.dumps(steps)[:300])
    config = portal_status._degraded(body["ts"], body["node"], portal_status.D_CONFIG)
    run, err = _status_js_run(js, [{"status": 200, "body": config}])
    check("status.js (DOM): a configuration-error body (refresh_s 0) stops polling",
          run.get("events", {}).get("timers") == [], err)
    old = dict(body, ts=body["ts"] - 10 * body["stale_after_s"])
    run, err = _status_js_run(js, [{"status": 200, "body": old}])
    check("status.js (DOM): a payload older than stale_after_s reads 'stale since'",
          "stale since" in run.get("note", "") and run.get("opacity") == "0.5", err or str(run.get("note")))

_GIT_OWNER = 12345  # a repo owner that is neither root nor the throwaway account

def _git_probe(mode, identity=None):
    """Run _git_dirty as root over a repo whose config runs a clean filter; return what the filter saw
    ([uid, gid, *groups]), whether core.fsmonitor ran, and the dirty count. 'gid': repo and .git owned
    by another uid, group root. 'cfg': .git root-owned; config, attributes and filter another uid's.
    identity overrides the drop target (a mutation)."""
    git = _shutil.which("git")
    with _tempfile.TemporaryDirectory() as tmp:
        _os.chmod(tmp, 0o755)
        repo, marks = _Path(tmp, "repo"), _Path(tmp, "marks")
        repo.mkdir()
        marks.mkdir()
        _os.chmod(marks, 0o777)
        g = [git, "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
        _subprocess.run(g + ["init", "-q"], check=True)
        for name in ("a", "b"):
            (repo / name).write_text("1")
        _subprocess.run(g + ["add", "a", "b"], check=True)
        _subprocess.run(g + ["commit", "-qm", "c"], check=True)
        marker, fsmon = marks / "filter", marks / "fsmonitor-ran"
        hook, filt = repo / ".git/fsmon-hook", repo / ".git/clean-filter"
        hook.write_text(f"#!/bin/sh\ntouch {fsmon}\n")
        filt.write_text(f"#!/bin/sh\n{{ id -u; id -g; id -G; }} > {marker}\ncat\n")
        for path in (hook, filt):
            _os.chmod(path, 0o755)
        _subprocess.run(g + ["config", "filter.x.clean", str(filt)], check=True)
        _subprocess.run(g + ["config", "core.fsmonitor", str(hook)], check=True)
        (repo / ".git/info").mkdir(exist_ok=True)
        (repo / ".git/info/attributes").write_text("a filter=x\n")
        (repo / "b").write_text("22")
        _os.utime(repo / "a", (1, 1))  # stat-dirty only: git re-hashes it through the clean filter
        _git_fixture_readable(repo)
        if mode == "gid":
            for path in [repo, *repo.rglob("*")]:
                _os.lchown(path, _GIT_OWNER, 0)
        else:
            for path in (repo / ".git/config", repo / ".git/info/attributes", filt):
                _os.lchown(path, _GIT_OWNER, _GIT_OWNER)
        saved = portal_spec._git_identity
        if identity is not None:
            portal_spec._git_identity = lambda: identity
        try:
            dirty = portal_spec._git_dirty(str(repo), _VENDOR["portal"]["status"]["probe_timeout_s"])
        finally:
            portal_spec._git_identity = saved
        seen = marker.read_text().split() if marker.exists() else []
        return seen, fsmon.exists(), dirty

def test_portal_status_git_dirty_runs_as_owner():
    """git_dirty never runs repo-controlled config with root's uid or group: under root it runs git as the
    throwaway account, whoever owns <root>/.git or the config files git reads."""
    if not _shutil.which("git") or _os.geteuid() != 0:
        print("[SKIP] git_dirty: needs git and root to prove the privilege drop; not run")
        return
    try:
        nobody = _pwd.getpwnam(portal_spec._GIT_USER)
    except KeyError:
        print("[SKIP] git_dirty: the throwaway account is not on this host; not run")
        return
    want = [str(nobody.pw_uid), str(nobody.pw_gid), str(nobody.pw_gid)]
    for mode in ("gid", "cfg"):
        seen, fsmonitor_ran, dirty = _git_probe(mode)
        check(f"git_dirty ({mode}): repo config runs as the throwaway account, no root uid or group, no owner",
              seen == want, f"filter saw {seen}, want {want}")
        check(f"git_dirty ({mode}): core.fsmonitor is forced off", not fsmonitor_ran)
        check(f"git_dirty ({mode}): the modified tracked file is counted", dirty == 1, str(dirty))
    seen, _, _ = _git_probe("gid", identity=(_GIT_OWNER, 0))
    check("git_dirty (negative): a drop to the owner with the directory's group 0 is caught",
          seen[1:2] == ["0"], str(seen))
    seen, _, _ = _git_probe("cfg", identity=(0, 0))
    check("git_dirty (negative): git run as root over another uid's config is caught", seen[:1] == ["0"], str(seen))

def test_portal_status_git_dirty_skips_unreadable():
    """A 0600 root file that is only stat-dirty (what a deployment leaves) does not make a clean tree dirty:
    git as the throwaway account cannot read it to compare. A readable modified file still counts."""
    if not _shutil.which("git") or _os.geteuid() != 0 or portal_spec._git_identity() is None:
        print("[SKIP] git_dirty (unreadable): needs git, root and the throwaway account; not run")
        return
    g = [_shutil.which("git"), "-c", "user.name=t", "-c", "user.email=t@t", "-C"]
    timeout = _VENDOR["portal"]["status"]["probe_timeout_s"]
    with _tempfile.TemporaryDirectory() as tmp:
        _os.chmod(tmp, 0o755)
        repo = _Path(tmp, "repo")
        repo.mkdir()
        _subprocess.run(g + [str(repo), "init", "-q"], check=True)
        for name in ("private", "open"):
            (repo / name).write_text("1")
        _subprocess.run(g + [str(repo), "add", "private", "open"], check=True)
        _subprocess.run(g + [str(repo), "commit", "-qm", "c"], check=True)
        _git_fixture_readable(repo)
        _os.chmod(repo / "private", 0o600)
        _os.utime(repo / "private", (1, 1))
        clean = portal_spec._git_dirty(str(repo), timeout)
        (repo / "open").write_text("2")
        one = portal_spec._git_dirty(str(repo), timeout)
        saved = portal_spec._probe_reads
        portal_spec._probe_reads = lambda *_a: True
        try:
            raw = portal_spec._git_dirty(str(repo), timeout)
        finally:
            portal_spec._probe_reads = saved
    check("git_dirty: a clean tree with a stat-dirty 0600 file is not dirty", clean == 0, str(clean))
    check("git_dirty: a readable modified file beside it still counts", one == 1, str(one))
    check("git_dirty (negative): git itself reports the unreadable file, so the filter is what drops it",
          raw == 2, str(raw))

def test_portal_status_user_tier_pins():
    """Writer and reader take [units.*] from the vendor tier (what boots) and snapshot_dir/repo_root from
    vendor+host: a user layer moving them is ignored, so a 2x probe budget the unit cannot hold fails closed."""
    import mios_toml
    keys = ("MIOS_VENDOR_TOML", "MIOS_HOST_TOML", "MIOS_USER_TOML", "MIOS_VENDOR_TOML_D", "MIOS_HOST_TOML_D",
            "MIOS_USER_TOML_D", "MIOS_RESOLVER_NATIVE", "MIOS_DB_AUTHORITATIVE", "MIOS_TOML_ROOT", "MIOS_TOML")
    saved = {k: _os.environ.get(k) for k in keys}
    with _tempfile.TemporaryDirectory() as tmp:
        host, user, host_dir = _Path(tmp, "host.toml"), _Path(tmp, "user.toml"), _Path(tmp, "host-dir")
        host.write_text(f'[portal.status]\nsnapshot_dir = "{host_dir}"\n')
        user.write_text('[portal.status]\nsnapshot_dir = "/nonexistent/user-dir"\nrepo_root = "/nonexistent/user-root"\n'
                        'probe_timeout_s = 100\n[units."mios-podman-ps.service".Service]\nTimeoutStopSec = "600s"\n')
        for k in ("MIOS_TOML_ROOT", "MIOS_TOML"):
            _os.environ.pop(k, None)
        _os.environ.update(MIOS_VENDOR_TOML=str(_ROOT / "usr/share/mios/mios.toml"), MIOS_HOST_TOML=str(host),
                           MIOS_USER_TOML=str(user), MIOS_VENDOR_TOML_D=str(_Path(tmp, "v.d")),
                           MIOS_HOST_TOML_D=str(_Path(tmp, "h.d")), MIOS_USER_TOML_D=str(_Path(tmp, "u.d")),
                           MIOS_RESOLVER_NATIVE="0", MIOS_DB_AUTHORITATIVE="0")
        try:
            portal_status._SSOT_MEMO.clear()
            mios_toml.clear_cache()
            views = {"writer": portal_spec._writer_ssot(), "reader": portal_status._ssot()}
            raw = mios_toml.load_merged(layers=mios_toml.layer_paths())
        finally:
            for k, v in saved.items():
                if v is None:
                    _os.environ.pop(k, None)
                else:
                    _os.environ[k] = v
            portal_status._SSOT_MEMO.clear()
            mios_toml.clear_cache()
    want = (str(host_dir), _VENDOR["portal"]["status"]["repo_root"],
            _VENDOR["units"]["mios-podman-ps.service"]["Service"]["TimeoutStopSec"])
    for name, doc in views.items():
        got = (doc["portal"]["status"]["snapshot_dir"], doc["portal"]["status"]["repo_root"],
               doc["units"]["mios-podman-ps.service"]["Service"]["TimeoutStopSec"])
        check(f"pins ({name}): snapshot_dir/repo_root from vendor+host, [units.*] from vendor", got == want, str(got))
        check(f"pins ({name}): probe_timeout_s=100 against the vendor 60s TimeoutStopSec fails closed",
              doc["portal"]["status"]["probe_timeout_s"] == 100 and portal_status._cfg(doc) is None)
    check("pins (negative): unpinned, the user layer moves both and the 600s budget is accepted",
          raw["portal"]["status"]["snapshot_dir"] == "/nonexistent/user-dir"
          and raw["units"]["mios-podman-ps.service"]["Service"]["TimeoutStopSec"] == "600s"
          and portal_status._cfg(raw) is not None)

def test_portal_status_ssot_reloads():
    """_ssot() re-reads a changed or newly present layer instead of the process-lifetime merge."""
    import mios_toml
    keys = ("MIOS_VENDOR_TOML", "MIOS_HOST_TOML", "MIOS_USER_TOML", "MIOS_VENDOR_TOML_D", "MIOS_HOST_TOML_D",
            "MIOS_USER_TOML_D", "MIOS_RESOLVER_NATIVE", "MIOS_DB_AUTHORITATIVE", "MIOS_TOML_ROOT", "MIOS_TOML")
    saved = {k: _os.environ.get(k) for k in keys}
    with _tempfile.TemporaryDirectory() as tmp:
        vendor, user = _Path(tmp, "vendor.toml"), _Path(tmp, "user.toml")
        vendor.write_text('[meta]\nmios_version = "1.0.0"\n')
        for k in ("MIOS_TOML_ROOT", "MIOS_TOML"):
            _os.environ.pop(k, None)
        _os.environ.update(MIOS_VENDOR_TOML=str(vendor), MIOS_HOST_TOML=str(_Path(tmp, "host.toml")),
                           MIOS_USER_TOML=str(user), MIOS_VENDOR_TOML_D=str(_Path(tmp, "v.d")),
                           MIOS_HOST_TOML_D=str(_Path(tmp, "h.d")), MIOS_USER_TOML_D=str(_Path(tmp, "u.d")),
                           MIOS_RESOLVER_NATIVE="0", MIOS_DB_AUTHORITATIVE="0")
        try:
            portal_status._SSOT_MEMO.clear()
            mios_toml.clear_cache()
            first = portal_status._ssot()["meta"]["mios_version"]
            vendor.write_text('[meta]\nmios_version = "1.0.10"\n')
            second = portal_status._ssot()["meta"]["mios_version"]
            user.write_text('[meta]\nmios_version = "2.0.0"\n')
            third = portal_status._ssot()["meta"]["mios_version"]
        finally:
            for k, v in saved.items():
                if v is None:
                    _os.environ.pop(k, None)
                else:
                    _os.environ[k] = v
            portal_status._SSOT_MEMO.clear()
            mios_toml.clear_cache()
    check("ssot: a changed vendor layer is re-read, not served from the process cache",
          (first, second) == ("1.0.0", "1.0.10"), f"{first} -> {second}")
    check("ssot: a user layer that appears later is merged", third == "2.0.0", str(third))

def test_portal_status_federation_peers():
    now = int(_time.time())
    frame = _frame(now)
    frame["sources"] += [{"source": "blender_lab", "kind": "mcp", "state": "unsupported"},
                         {"source": "peer-b", "kind": "mcp", "state": "ready"},
                         {"source": "peer-c", "kind": "mcp", "state": "ready"},
                         {"source": "peer-p", "kind": "mcp", "state": "pending"},
                         {"source": "peer-u", "kind": "mcp", "state": "unavailable"},
                         {"source": "peer-x", "kind": "mcp", "state": "partial"}]
    remote = {"kind": "claude", "online": True, "pending": 0, "parent_agent_id": "", "session_name": "", "node": ""}
    frame["agents"] += [dict(remote, agent_id="remote-1", label="Remote", node="peer-b", source="peer-b/agent-relay"),
                        dict(remote, agent_id="cached-1", label="Cached", stale=True, source="peer-u/agent-relay"),
                        dict(remote, agent_id="ghost-1", label="Ghost", source="ghost/agent-relay")]
    with _tempfile.TemporaryDirectory() as tmp:
        _, _, doc = _plant(tmp, now, frame)
        with _StatusEnv(doc):
            _, body = _status()
    names = [n["name"] for n in body["nodes"] if not n["local"]]
    peers = {s["source"]: s["presence"] for s in body["agents"]["sources"]}
    rows = {r["agent_id"]: r for r in body["agents"]["rows"]}
    check("federation: a plain MCP server (state unsupported) is neither a peer nor a node",
          "blender_lab" not in names and "blender_lab" not in peers, f"nodes={names} peers={peers}")
    check("federation: only a peer that contributed agent rows becomes a node",
          names == ["peer-b", "peer-u"], f"nodes={names}")
    check("federation: presence only for ready/partial members or members with rows (never pending/unavailable alone)",
          peers == {"local": "local", "peer-b": "reachable", "peer-c": "reachable", "peer-x": "unknown",
                    "peer-u": "unknown"}, str(peers))
    check("federation: a row whose source is no declared member reads 'unknown', presence unknown, and makes no node",
          rows.get("ghost-1", {}).get("source") == "unknown" and rows["ghost-1"]["online"] is None
          and "ghost" not in names and "ghost" not in peers, str(rows.get("ghost-1")))

def test_portal_status_one_live_frame_never_masks_a_dead_one():
    if _os.geteuid() != 0:
        print("[SKIP] status: a second publisher uid needs root to plant; not run")
        return
    now, dead_uid = int(_time.time()), 65534
    with _tempfile.TemporaryDirectory() as tmp:
        _, snap, doc = _plant(tmp, now)
        dead = _frame(now - 7200)
        for row in dead["agents"]:
            row.update(agent_id="dead-" + row["agent_id"], online=True)
        dead["subagents"] = []
        path = snap / f"agents-{dead_uid}.json"
        path.write_text(json.dumps(dead))
        _os.chmod(path, 0o640)
        _os.chown(path, dead_uid, -1)
        with _StatusEnv(doc):
            _, body = _status()
    a, src = body["agents"], _src(body, "agents")
    rows = {r["agent_id"]: r["online"] for r in a["rows"]}
    check("status: rows of a dead publisher's frame lose their recorded online flag",
          all(rows.get(k) is None for k in ("dead-coordinator", "dead-worker-1", "dead-idle"))
          and rows.get("coordinator") is True and a["online"] == 1, str(rows))
    check("status: the oldest frame sets the age and the source reads partial",
          a["age_s"] >= 7200 and src["state"] == "stale" and src["detail"] == portal_status.D_PARTIAL, json.dumps(src))

def test_portal_status_services_need_an_answer_per_unit():
    now = int(_time.time())
    cases = (("no bus: nothing on stdout", "echo 'Failed to connect to bus' >&2; exit 1", "unavailable"),
             ("one answer short", "echo active", "unavailable"),
             ("one answer per unit", 'shift; for u in "$@"; do echo active; done', "ok"))
    for name, script, want in cases:
        with _tempfile.TemporaryDirectory() as tmp:
            _, _, doc = _plant(tmp, now)
            stubs = _Path(tmp, "bin")
            stubs.mkdir()
            (stubs / "systemctl").write_text("#!/bin/sh\n" + script + "\n")
            _os.chmod(stubs / "systemctl", 0o755)
            with _StatusEnv(doc, path_dirs=[str(stubs), "/usr/bin", "/bin"]):
                _, body = _status()
        src = _src(body, "services")
        active = {s["active"] for s in body["services"]}
        ok = src["state"] == want and (src["detail"] == portal_status.D_NO_SYSTEMCTL if want != "ok"
                                       else active == {"active"})
        check(f"services: {name} -> {want}", ok, json.dumps(src) + " " + str(sorted(active)))

_SPAN = {"s": 1, "sec": 1, "min": 60, "h": 3600}

def _span_s(text):
    match = _re.fullmatch(r"(\d+)\s*(s|sec|min|h)", str(text).strip())
    return int(match.group(1)) * _SPAN[match.group(2)] if match else None

def _unit(path):
    """{section: {key: [values]}} of a rendered unit file."""
    out, section = {}, None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = out.setdefault(line[1:-1], {})
        elif "=" in line and section is not None and not line.startswith("#"):
            key, _, value = line.partition("=")
            section.setdefault(key, []).append(value)
    return out

def _stop_writer(service):
    """The spec writer lines of a [Service] table: an ExecStopPost=- (it runs whatever ExecStart returned)
    and never an ExecStartPost (which is skipped when podman ps fails)."""
    stop = [v for v in service.get("ExecStopPost", []) if "mios_pipe.portal_spec --write-spec" in v]
    start = [v for v in service.get("ExecStartPost", []) if "mios_pipe.portal_spec --write-spec" in v]
    return stop if len(stop) == 1 and stop[0].startswith("-") and not start else []

def test_portal_status_producer_units():
    """The spec writer rides mios-podman-ps (15s) as ExecStopPost=-, fits its stop budget, runs -P, the unit is
    declared in [units.*] (so its budget sits in the SSOT), and snapshot_dir binds everywhere."""
    st, system = _VENDOR["portal"]["status"], _ROOT / "usr/lib/systemd/system"
    path = system / "mios-podman-ps.service"
    host = _unit(path).get("Service", {})
    timer = _unit(system / "mios-podman-ps.timer").get("Timer", {})
    writer = _stop_writer(host)
    check("producer units: the spec writer is the one ExecStopPost=- of mios-podman-ps.service", len(writer) == 1,
          str(host)[:300])
    with _tempfile.TemporaryDirectory() as tmp:
        planted = _Path(tmp, "planted.service")
        planted.write_text(path.read_text().replace("ExecStopPost=-", "ExecStartPost=-"))
        check("producer units (negative): a writer planted back as ExecStartPost is caught",
              _stop_writer(_unit(planted).get("Service", {})) == [])
    check("producer units: the root writer runs python3 -P (the / work tree stays off sys.path)",
          bool(writer) and " -P -m mios_pipe.portal_spec" in writer[0], str(writer))
    declared = _VENDOR["units"]["mios-podman-ps.service"]["Service"]
    check("producer units: [units.\"mios-podman-ps.service\"] declares the writer and its TimeoutStopSec",
          bool(writer) and declared.get("ExecStopPost") == writer[0]
          and [declared.get("TimeoutStopSec")] == host.get("TimeoutStopSec"), str(declared)[:300])
    gen = _os.path.join(_os.environ.get("MIOS_NATIVE_BIN_DIR", ""), "mios-unit-gen")
    gen = gen if _os.access(gen, _os.X_OK) else _shutil.which("mios-unit-gen")
    if gen:
        rendered = _subprocess.run([gen, "--root", str(_ROOT), "--render", "mios-podman-ps.service"],
                                   capture_output=True, text=True, timeout=60).stdout
        check("producer units: mios-podman-ps.service is the mios-unit-gen render of its [units.*] declaration",
              rendered == path.read_text(), rendered[:200])
    else:
        print("[SKIP] producer units: mios-unit-gen is not available; render equality not run")
    cadence = _span_s((timer.get("OnUnitActiveSec") or [""])[0])
    check("producer units: the spec cadence (mios-podman-ps.timer) is under [portal.status].stale_after_s",
          cadence is not None and cadence < st["stale_after_s"], f"{cadence} vs {st['stale_after_s']}")
    budget = _span_s((host.get("TimeoutStopSec") or [""])[0])
    check("producer units: both writer probes fit inside mios-podman-ps.service TimeoutStopSec",
          budget is not None and 2 * st["probe_timeout_s"] < budget, f"2 x {st['probe_timeout_s']} vs {budget}")
    check("producer units: the retired mios-status-spec units are gone",
          not any((system / n).exists() for n in ("mios-status-spec.service", "mios-status-spec.timer"))
          and not any(n.startswith("mios-status-spec") for n in _VENDOR["units"]))
    tmpfiles = [line.split() for line in (_ROOT / "usr/lib/tmpfiles.d/50-mios.conf").read_text().splitlines()
                if line.startswith("d ") and "/status" in line]
    check("snapshot_dir: [portal.status].snapshot_dir is the one tmpfiles status directory",
          [row[1] for row in tmpfiles] == [st["snapshot_dir"]], str(tmpfiles))
    for path in (system / "mios-podman-ps.service", _ROOT / "usr/lib/systemd/user/mios-status-agents.service"):
        svc = _unit(path).get("Service", {})
        grants = " ".join(svc.get("ReadWritePaths", [])).split()
        check(f"snapshot_dir: {path.name} may write {st['snapshot_dir']}",
              (svc.get("ProtectSystem") or [""])[-1] != "strict" or st["snapshot_dir"] in grants,
              f"ProtectSystem=strict without ReadWritePaths={st['snapshot_dir']} (grants {grants})")
    html = (_ROOT / "usr/share/mios/configurator/mios.html").read_text()
    offered = [k for k in ("snapshot_dir", "repo_root", "probe_timeout_s", "request_timeout_s")
               if f'data-key="portal.status.{k}"' in html]
    check("snapshot_dir/repo_root/probe_timeout_s/request_timeout_s: no mios.html input offers the host constants",
          offered == [], str(offered))

_SW_NODE = r'''
const vm = require('vm'), fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const handlers = {}, events = {respond: [], put: [], match: []};
const ctx = {URL, location: {origin: 'https://portal.invalid'},
  self: {addEventListener: (type, fn) => { handlers[type] = fn; }, skipWaiting() {}, clients: {claim() {}}},
  caches: {open: async () => ({put: req => events.put.push(new URL(req.url).pathname), addAll: async () => {}}),
           match: async req => { events.match.push(String(req.url || req)); return null; }, keys: async () => []},
  fetch: async () => ({status: 200, redirected: false, clone() { return this; }})};
vm.createContext(ctx);
vm.runInContext(input.js, ctx);
(async () => {
  for (const path of input.paths) {
    const request = {method: 'GET', mode: 'cors', url: 'https://portal.invalid' + path};
    handlers.fetch({request, respondWith: p => { events.respond.push(path); return p; }});
  }
  await new Promise(r => setImmediate(() => setImmediate(r)));
  process.stdout.write(JSON.stringify(events));
})();
'''

def test_portal_sw_never_caches_status():
    if not _shutil.which("node"):
        print("[SKIP] sw: node is not on PATH; service worker not run")
        return
    paths = ["/portal/status", "/portal/status.js", "/portal/icon.svg"]
    out = _subprocess.run(["node", "-e", _SW_NODE], input=json.dumps({"js": mios_portal._PORTAL_SW, "paths": paths}),
                          capture_output=True, text=True, timeout=30)
    events = json.loads(out.stdout or "{}")
    check("sw: /portal/status* is never answered, cached or replayed by the service worker",
          events.get("respond") == ["/portal/icon.svg"] and events.get("put") == ["/portal/icon.svg"],
          out.stderr[-300:] or json.dumps(events))

def test_portal_status_cadence_relations():
    """_cfg fails closed (reader and writer) when a cadence relation breaks, at any tier, not only vendor."""
    now = int(_time.time())
    st = lambda d: d["portal"]["status"]  # noqa: E731
    cases = (
        ("vendor values hold", lambda d: None, True),
        ("boundary values still hold",
         lambda d: st(d).update(cache_s=14, request_timeout_s=4, probe_timeout_s=29, stale_after_s=16), True),
        ("[mcp.agents.observation].refresh_s >= stale_after_s",
         lambda d: d["mcp"]["agents"]["observation"].update(refresh_s=600), False),
        ("mios-podman-ps.timer OnUnitActiveSec >= stale_after_s",
         lambda d: d["units"]["mios-podman-ps.timer"]["Timer"].update(OnUnitActiveSec="5min"), False),
        ("cache_s >= refresh_s", lambda d: st(d).update(cache_s=3600), False),
        ("3 x request_timeout_s >= refresh_s", lambda d: st(d).update(request_timeout_s=5), False),
        ("2 x probe_timeout_s >= mios-podman-ps.service TimeoutStopSec", lambda d: st(d).update(probe_timeout_s=30), False),
        ("an unparseable TimeoutStopSec", lambda d: d["units"]["mios-podman-ps.service"]["Service"].update(
            TimeoutStopSec="1min 30s"), False),
        ("mios-podman-ps.service undeclared in [units]", lambda d: d["units"].pop("mios-podman-ps.service"), False),
    )
    for name, mutate, holds in cases:
        with _tempfile.TemporaryDirectory() as tmp:
            _, snap, doc = _plant(tmp, now)
            mutate(doc)
            (snap / "spec.json").unlink()
            with _StatusEnv(doc):
                _, body = _status()
                rc = portal_spec.main(["--write-spec"])
            written = (snap / "spec.json").exists()
        closed = body["refresh_s"] == 0 and {s["state"] for s in body["sources"]} == {"unavailable"}
        if holds:
            ok = portal_status._cfg(doc) is not None and not closed and rc == 0 and written
        else:
            ok = portal_status._cfg(doc) is None and closed and rc == 1 and not written
        check(f"cadence relations: {name} -> {'accepted' if holds else 'fail closed (view and writer)'}", ok,
              f"rc={rc} written={written} refresh_s={body['refresh_s']}")

_WRITER_DRIVER = r"""
import json, runpy, sys, time
mode = sys.argv[1]
t0 = time.process_time()
code = None
if mode == "writer":
    sys.argv = ["portal_status", "--write-spec"]
    try:
        runpy.run_module("mios_pipe.portal_spec", run_name="__main__")
    except SystemExit as stop:
        code = stop.code
else:  # the server path: the router and the full SSOT loader
    from mios_pipe import portal_status
    portal_status.status_router
    portal_status._ssot()
print(json.dumps({"code": code, "fastapi": "fastapi" in sys.modules, "db": "mios_db_config" in sys.modules,
                  "cpu_s": round(time.process_time() - t0, 3)}))
"""

def test_portal_status_writer_stays_off_fastapi():
    """The root writer (python3 -P -m mios_pipe.portal_spec --write-spec) imports no fastapi and skips the
    native resolver and DB overlay; the server path still gets its router lazily."""
    with _tempfile.TemporaryDirectory() as tmp:
        snap, repo = _Path(tmp, "status"), _Path(tmp, "repo")
        snap.mkdir()
        repo.mkdir()
        text = (_ROOT / "usr/share/mios/mios.toml").read_text()
        text = _re.sub(r'(?m)^snapshot_dir  = "[^"]*"', f'snapshot_dir  = "{snap}"', text, count=1)
        text = _re.sub(r'(?m)^repo_root     = "[^"]*"', f'repo_root     = "{repo}"', text, count=1)
        vendor = _Path(tmp, "vendor.toml")
        vendor.write_text(text)
        here = _Path(__file__).resolve().parent
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{here}:{here.parent}", "MIOS_VENDOR_TOML": str(vendor),
               "MIOS_HOST_TOML": str(_Path(tmp, "none-host.toml")), "MIOS_USER_TOML": str(_Path(tmp, "none-user.toml")),
               "MIOS_VENDOR_TOML_D": str(_Path(tmp, "v.d")), "MIOS_HOST_TOML_D": str(_Path(tmp, "h.d")),
               "MIOS_USER_TOML_D": str(_Path(tmp, "u.d")), "HOME": tmp}
        runs = {}
        for mode in ("writer", "server"):
            out = _subprocess.run([sys.executable, "-P", "-c", _WRITER_DRIVER, mode], env=env, capture_output=True,
                                  text=True, timeout=120)
            lines = out.stdout.strip().splitlines()
            runs[mode] = json.loads(lines[-1]) if lines else {"error": out.stderr[-300:]}
        written = (snap / "spec.json").exists()
    writer, server = runs["writer"], runs["server"]
    check("writer: --write-spec exits 0, writes spec.json, never imports fastapi or the DB overlay",
          writer.get("code") == 0 and written and writer.get("fastapi") is False and writer.get("db") is False,
          json.dumps(runs))
    check("writer (negative): the server path (router + full SSOT) is seen importing both",
          server.get("fastapi") is True and server.get("db") is True, json.dumps(server))
    print(f"[INFO] writer CPU {writer.get('cpu_s')}s per run vs the server path {server.get('cpu_s')}s")

def test_portal_status_failed_aggregate_keeps_polling():
    """A failed aggregate answers the configured refresh_s (clients recover); only a configuration error says 0."""
    now = int(_time.time())
    with _tempfile.TemporaryDirectory() as tmp:
        _, _, doc = _plant(tmp, now)
        saved = portal_status._cached_status

        def boom():
            raise RuntimeError("aggregate exploded")
        portal_status._cached_status = boom
        try:
            with _StatusEnv(doc) as env:
                _, failed = _status()
                env.doc = _copy.deepcopy(doc)
                env.doc["portal"].pop("status")
                _, config = _status()
        finally:
            portal_status._cached_status = saved
    errors = _schema_errors(failed)
    check("status: a failed aggregate keeps [portal.status].refresh_s and reads 'collector failed' everywhere",
          not errors and failed["refresh_s"] == _VENDOR["portal"]["status"]["refresh_s"]
          and {(s["state"], s["detail"]) for s in failed["sources"]} == {("unavailable", portal_status.D_FAILED)},
          errors or json.dumps(failed["sources"])[:200])
    check("status (negative): a failed aggregate under a configuration error still says refresh_s 0",
          config["refresh_s"] == 0, str(config["refresh_s"]))

def test_portal_status_poll_off_the_loop():
    """_poll_s (an SSOT reload: native resolver subprocess, DB overlay) runs off the event-loop thread, for the
    served script and on the failed-aggregate path."""
    import threading
    seen = []

    def poll():
        seen.append(threading.current_thread() is threading.main_thread())
        return 15
    saved = (portal_status._poll_s, portal_status._cached_status)
    portal_status._poll_s = poll
    try:
        with _tempfile.TemporaryDirectory() as tmp:
            _, _, doc = _plant(tmp, int(_time.time()))
            with _StatusEnv(doc):
                asyncio.run(portal_status.status_js_logic(_Req(None)))
                portal_status._cached_status = lambda: 1 // 0
                asyncio.run(portal_status.status_logic(_Req(None)))
    finally:
        portal_status._poll_s, portal_status._cached_status = saved
    check("status: _poll_s runs off the event-loop thread (status.js, then the failed aggregate)",
          seen == [False, False], str(seen))

def test_portal_status_agent_rows_cap():
    """Rows are capped server-side at [portal.status].agent_rows (online first); totals count every agent."""
    now = int(_time.time())
    frame = _frame(now)
    frame["agents"] += [{"agent_id": f"extra-{i:02d}", "kind": "claude", "label": f"Extra {i:02d}", "online": i % 2 == 0,
                         "pending": 0, "parent_agent_id": "", "session_name": "", "node": "", "source": _RELAY}
                        for i in range(20)]
    bodies = {}
    for rows in (_VENDOR["portal"]["status"]["agent_rows"], 40):
        with _tempfile.TemporaryDirectory() as tmp:
            _, _, doc = _plant(tmp, now, frame)
            doc["portal"]["status"]["agent_rows"] = rows
            with _StatusEnv(doc):
                _, bodies[rows] = _status()
    capped, wide = bodies[_VENDOR["portal"]["status"]["agent_rows"]], bodies[40]
    a = capped["agents"]
    check("status: rows capped at agent_rows, online first, totals uncapped",
          len(a["rows"]) == _VENDOR["portal"]["status"]["agent_rows"] and a["total"] == 23 and a["online"] == 11
          and all(r["online"] is True for r in a["rows"][:11]), f"{len(a['rows'])} rows, total {a['total']}")
    check("status (negative): a wider agent_rows shows every row, so the cap is the SSOT value",
          len(wide["agents"]["rows"]) == 23, str(len(wide["agents"]["rows"])))
    if _shutil.which("node"):
        run, err = _status_js_run(_served_js(), [{"status": 200, "body": wide}])
        check("status.js (DOM): the renderer draws every row it is given (no literal cap)",
              "Extra 19" in run.get("text", "") and "Extra 00" in run.get("text", ""), err)

def test_portal_status_request_timeout_from_ssot():
    """The request-path probes use [portal.status].request_timeout_s, not a literal."""
    now = int(_time.time())
    elapsed = {}
    for timeout in (1, 3):
        with _tempfile.TemporaryDirectory() as tmp:
            _, _, doc = _plant(tmp, now)
            doc["portal"]["status"]["request_timeout_s"] = timeout
            stubs = _Path(tmp, "bin")
            stubs.mkdir()
            (stubs / "systemctl").write_text("#!/bin/sh\nexec /bin/sleep 8\n")
            _os.chmod(stubs / "systemctl", 0o755)
            with _StatusEnv(doc, path_dirs=[str(stubs)]):
                started = _time.monotonic()
                _, body = _status()
                elapsed[timeout] = _time.monotonic() - started
        src = _src(body, "services")
        check(f"status: a hung systemctl is cut at request_timeout_s={timeout}",
              src["state"] == "unavailable" and src["detail"] == portal_status.D_NO_SYSTEMCTL
              and timeout <= elapsed[timeout] < timeout + 2.5, f"{elapsed[timeout]:.2f}s {json.dumps(src)}")
    check("status (negative): the cut moves with the SSOT value", elapsed[3] - elapsed[1] > 1.5, str(elapsed))

def _hostile_frame(now):
    frame = _frame(now)
    frame["sources"] = [{"source": "/srv/other/state", "state": "ready", "kind": "relay"},
                        {"source": "100.64.0.9:8443", "kind": "mcp", "state": "ready"}]
    row = {"kind": "claude", "online": True, "pending": 0, "parent_agent_id": "", "session_name": "",
           "node": "100.64.0.9"}
    frame["agents"] = [dict(row, agent_id="forged", label="Forged", source="100.64.0.9:8443/relay"),
                       dict(row, agent_id="local-forged", label="Coordinator", source="/srv/other/state")]
    frame["subagents"] = []
    return frame

def test_portal_status_untrusted_publisher_invents_no_peers():
    """A frame from a mios-ai member that is not the relay owner shows its own agents, never peers or nodes."""
    if _os.geteuid() != 0:
        print("[SKIP] status: a second publisher uid needs root to plant; not run")
        return
    now, other = int(_time.time()), 65534
    bodies = {}
    for who in ("untrusted", "trusted"):
        with _tempfile.TemporaryDirectory() as tmp:
            _, snap, doc = _plant(tmp, now)
            uid = other if who == "untrusted" else _os.getuid()
            path = snap / f"agents-{uid}.json"
            path.write_text(json.dumps(_hostile_frame(now)))
            _os.chmod(path, 0o640)
            _os.chown(path, uid, -1)
            with _StatusEnv(doc):
                _, bodies[who] = _status()
    body = bodies["untrusted"]
    names = [n["name"] for n in body["nodes"] if not n["local"]]
    peers = {s["source"] for s in body["agents"]["sources"]}
    forged = next((r for r in body["agents"]["rows"] if r["agent_id"] == "forged"), {})
    check("status: an untrusted publisher's self-declared peer makes no node card and no peer presence",
          "100.64.0.9:8443" not in names and "100.64.0.9:8443" not in peers, f"nodes={names} peers={sorted(peers)}")
    check("status: the forged remote row reads source 'unknown' with presence unknown",
          forged.get("source") == "unknown" and forged.get("online") is None, str(forged))
    check("status: the untrusted frame's rows are still counted, the relay owner's rows untouched",
          {r["agent_id"] for r in body["agents"]["rows"]} >= {"forged", "local-forged", "coordinator"},
          str(body["agents"]["rows"])[:200])
    local = next((r for r in body["agents"]["rows"] if r["agent_id"] == "local-forged"), {})
    check("status: an untrusted publisher's own 'local' row has no vouched online flag or node",
          local.get("source") == "local" and local.get("online") is None and local.get("node") is None
          and forged.get("node") is None and body["agents"]["online"] == 1, f"{local} online={body['agents']['online']}")
    trusted = [n["name"] for n in bodies["trusted"]["nodes"] if not n["local"]]
    check("status (negative): the same frame from the relay owner does make the node, so trust is what blocks it",
          trusted == ["100.64.0.9:8443"], str(trusted))
    mine = next((r for r in bodies["trusted"]["agents"]["rows"] if r["agent_id"] == "local-forged"), {})
    check("status (negative): from the relay owner the same row keeps online and node",
          mine.get("online") is True and mine.get("node") == "100.64.0.9", str(mine))

def test_portal_status_same_agent_id_under_two_publishers():
    """Rows are keyed by (publisher uid, source, agent id): two relay owners' 'coordinator' are two rows."""
    if _os.geteuid() != 0:
        print("[SKIP] status: a second publisher uid needs root to plant; not run")
        return
    now, other = int(_time.time()), 65534
    with _tempfile.TemporaryDirectory() as tmp:
        _, snap, doc = _plant(tmp, now)
        path = snap / f"agents-{other}.json"
        stale = _frame(now - 7200)
        stale["subagents"] = []
        path.write_text(json.dumps(stale))
        _os.chmod(path, 0o640)
        _os.chown(path, other, -1)
        with _StatusEnv(doc):
            _, body = _status()
        own = snap / f"agents-{_os.getuid()}.json"
        frame = _frame(now)
        frame["agents"].append(dict(frame["agents"][0]))
        own.write_text(json.dumps(frame))
        path.unlink()
        with _StatusEnv(doc):
            _, single = _status()
    rows = [r for r in body["agents"]["rows"] if r["agent_id"] == "coordinator"]
    check("status: the same agent id under two publishers is two rows; the stale one's flag is unknown",
          sorted(str(r["online"]) for r in rows) == ["None", "True"], str(rows))
    check("status (negative): a duplicate inside one publisher's frame still collapses to one row",
          [r["agent_id"] for r in single["agents"]["rows"]].count("coordinator") == 1)

if __name__ == "__main__":
    sys.exit(main())
