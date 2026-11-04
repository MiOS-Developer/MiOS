# AI-hint: Stdlib unit test for mios_http_caps -- the advertised-surface / capability route LOGIC extracted from server.py (refactor R-CAPS).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stdlib unit tests for mios_http_caps (refactor R-CAPS) -- stubbed, no I/O."""

import asyncio
import json
import sys

import mios_http_caps as M

_fails = 0

def check(name, ok, detail=""):
    global _fails
    if ok:
        print(f"[PASS] {name}")
    else:
        _fails += 1
        print(f"[FAIL] {name} :: {detail}")

def _body(resp):
    """Decode a fastapi JSONResponse rendered body into a dict."""
    return json.loads(bytes(resp.body).decode("utf-8"))

class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p

class _FakeClient:
    def __init__(self, payload):
        self._p = payload
        self.posted = None

    async def post(self, url, content=None, headers=None):
        self.posted = {"url": url, "content": content, "headers": headers}
        return _FakeResp(self._p, status=201)

class _FakeReq:
    def __init__(self, body=b"", jsonobj=None, headers=None):
        self._body = body
        self._json = jsonobj
        self.headers = headers or {}

    async def body(self):
        return self._body

    async def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

def _fake_verb_to_openai_tool(vname, vcfg):
    return {"type": "function",
            "function": {"name": vname, "description": vcfg.get("desc", "")},
            "x-mios-verb": vname}

def _configure_stubs(client_payload=None):
    catalog = {
        "open_app": {"desc": "open an app", "section": "os", "tier": "common",
                     "permission": "read",
                     "params": {"name": {"type": "string", "desc": "app"}}},
        "rare_verb": {"desc": "rare", "section": "x", "tier": "rare",
                      "permission": "admin", "params": {}},
    }

    class _Ledger:
        def over_budget(self, b):
            return False

        def snapshot(self):
            return {"wh": 1.0, "usd": 0.0, "tokens": 5}

    class _Model:
        gpu_watts = 300
        usd_per_kwh = 0.15
        remote_usd_per_mtok = 0.0

    class _Tracer:
        enabled = True

        def get_trace(self, tid):
            return [{"span": tid}]

        def stats(self):
            return {"buffered": 1}

        def recent(self, n):
            return [{"trace_id": "t1"}]

    M.configure(
        verb_catalog=catalog,
        a2a_peers={"peerA": {"url": "http://host:8642", "heartbeat": 3}},
        a2a_peers_lock=asyncio.Lock(),
        cost_ledger=_Ledger(), cost_model=_Model(),
        cost_accounting_enable=True, cost_budget_usd=10.0,
        tracer=_Tracer(), backend="http://backend:11450/v1",
        verb_to_openai_tool=_fake_verb_to_openai_tool,
        recipe_to_openai_tool=lambda n, c: {"function": {"name": "mios_recipe__" + n}},
        skill_to_openai_tool=lambda r: {"function": {"name": "mios_skill__" + r["name"]}},
        load_recipe_catalog=lambda: {"toast": {"desc": "t"}},
        skill_list=_fake_skill_list,
        skill_fetch=_fake_skill_fetch,
        user_rbac_filter=lambda tools: tools,
        match_user_cfg=lambda: (None, {"max_permission": "interactive"}),
        toml_section=lambda s: {"agent_model": "MiOS AI"} if s == "ai" else {},
        cap_skills=lambda: {},
        get_client=_make_get_client(client_payload or {"data": [[0.1, 0.2]]}),
        kg_lookup=_fake_kg_lookup,
        execute_skill=_fake_execute_skill,
        run_dci_flow=_fake_run_dci_flow,
        offline_posture=lambda: {"offline": True, "external_endpoints": [],
                                 "checks": [], "enforced": True})

async def _fake_skill_list(status="all", source=None, limit=200):
    return [{"name": "deploy", "description": "d", "status": "promoted"}]

async def _fake_skill_fetch(name):
    return {"name": name, "body": "x"}

async def _fake_kg_lookup(phrase):
    return {"app": "firefox"} if phrase == "browser" else None

async def _fake_execute_skill(name, params, session_id=None):
    return {"success": True, "name": name}

async def _fake_run_dci_flow(user_text, envelope, session_id=None, r_max=None):
    return {"verdict": "ok", "user_text": user_text}

def _make_get_client(payload):
    async def _get():
        return _FakeClient(payload)
    return _get

def main():
    _configure_stubs()

    r = asyncio.run(M.list_verbs_logic(include_rare=True))
    b = _body(r)
    t0 = next((t for t in b["tools"] if t["name"] == "open_app"), None)
    check("verbs: open_app present", t0 is not None)
    check("verbs: inputSchema additionalProperties False",
          t0 and t0["inputSchema"]["additionalProperties"] is False)
    check("verbs: required derived (no default => required)",
          t0 and "name" in t0["inputSchema"]["required"])
    check("verbs: annotations.permission",
          t0 and t0["annotations"]["permission"] == "read")
    r2 = _body(asyncio.run(M.list_verbs_logic(include_rare=False)))
    check("verbs: rare excluded when include_rare False",
          all(t["name"] != "rare_verb" for t in r2["tools"]))

    ot = _body(asyncio.run(M.list_verbs_openai_tools_logic(include_rare=True)))
    check("openai-tools: count == 2", ot["count"] == 2)
    check("openai-tools: function name projected",
          any(t["function"]["name"] == "open_app" for t in ot["tools"]))

    tl = _body(asyncio.run(M.list_tools_logic(include_rare=True)))
    check("tools: has counts block", "counts" in tl)
    check("tools: recipe projected", tl["counts"]["recipes"] >= 1)
    check("tools: skill projected", tl["counts"]["skills"] >= 1)

    cap = _body(asyncio.run(M.v1_capabilities_logic(_FakeReq())))
    check("capabilities: manifest object",
          cap["object"] == "mios.capability.manifest")

    pr = _body(asyncio.run(M.v1_peers_logic()))
    check("peers: digest object", pr["object"] == "mios.peer.digest")
    check("peers: endpoint + heartbeat projected",
          pr["peers"] and pr["peers"][0]["endpoint"] == "http://host:8642"
          and pr["peers"][0]["heartbeat"] == 3)

    res = _body(asyncio.run(M.list_resources_logic()))
    uris = [x["uri"] for x in res["resources"]]
    check("resources: verb resource", "mios://verb/open_app" in uris)
    check("resources: recipe resource", "mios://recipe/toast" in uris)
    check("resources: skill resource", "mios://skill/deploy" in uris)
    check("projector: _verb_to_mcp_resource shape",
          M._verb_to_mcp_resource("v", {"desc": "d"})["uri"] == "mios://verb/v")

    rr = _body(asyncio.run(M.read_resource_logic("mios://verb/open_app")))
    check("read_resource: contents text is verb json",
          rr["contents"][0]["uri"] == "mios://verb/open_app")
    rr404 = asyncio.run(M.read_resource_logic("mios://verb/nope"))
    check("read_resource: 404 unknown verb", rr404.status_code == 404)

    cost = _body(asyncio.run(M.cost_ledger_logic()))
    check("cost: object + model", cost["object"] == "mios.cost"
          and cost["model"]["gpu_watts"] == 300)

    tr = _body(asyncio.run(M.trace_read_logic("t1")))
    check("trace: span_count", tr["span_count"] == 1 and tr["trace_id"] == "t1")
    trl = _body(asyncio.run(M.trace_recent_logic()))
    check("trace.list: recent", trl["object"] == "mios.trace.list")

    off = _body(asyncio.run(M.offline_status_logic()))
    check("offline: status object + offline true",
          off["object"] == "mios.offline_status" and off["offline"] is True)

    mo = _body(asyncio.run(M.list_models_logic(_FakeReq())))
    check("models: exactly one MiOS AI", len(mo["data"]) == 1
          and mo["data"][0]["id"] == "MiOS AI")
    M.configure(toml_section=lambda section: {"agent_model": "Fixture Local"} if section == "ai" else {})
    configured = _body(asyncio.run(M.list_models_logic(_FakeReq())))
    check("models: only the configured public alias is advertised",
          [row["id"] for row in configured["data"]] == ["Fixture Local"])
    check("models: public aliases share the same standard model list",
          configured["models"] == configured["data"] and configured["object"] == "list")


    emb_resp = asyncio.run(M.embeddings_logic(
        _FakeReq(body=b'{"input":"hi"}',
                 headers={"authorization": "Bearer k", "content-type": "application/json",
                          "x-drop": "y"})))
    check("embeddings: passthrough status preserved", emb_resp.status_code == 201)
    eb = _body(emb_resp)
    check("embeddings: backend payload returned", "data" in eb)

    kg = _body(asyncio.run(M.kg_lookup_endpoint_logic("browser")))
    check("kg: match returned", kg["match"] == {"app": "firefox"})
    kg404 = asyncio.run(M.kg_lookup_endpoint_logic("nope"))
    check("kg: 404 on no match", kg404.status_code == 404)
    kg400 = asyncio.run(M.kg_lookup_endpoint_logic(""))
    check("kg: 400 on empty phrase", kg400.status_code == 400)

    sl = _body(asyncio.run(M.skills_list_logic()))
    check("skills/list: rows", sl["count"] == 1)
    ss = _body(asyncio.run(M.skills_show_logic("deploy")))
    check("skills/show: row", ss["skill"]["name"] == "deploy")
    srun = asyncio.run(M.skills_run_logic(_FakeReq(jsonobj={"name": "deploy"})))
    check("skills/run: 200 on success", srun.status_code == 200)
    srun400 = asyncio.run(M.skills_run_logic(_FakeReq(jsonobj={})))
    check("skills/run: 400 on missing name", srun400.status_code == 400)
    sot = _body(asyncio.run(M.skills_openai_tools_logic()))
    check("skills/openai-tools: projected", sot["count"] == 1)

    dd = asyncio.run(M.dci_deliberate_logic(_FakeReq(jsonobj={"user_text": "hi"})))
    check("dci/deliberate: result", _body(dd)["verdict"] == "ok")
    dd400 = asyncio.run(M.dci_deliberate_logic(_FakeReq(jsonobj={"user_text": ""})))
    check("dci/deliberate: 400 empty user_text", dd400.status_code == 400)
    dsch = _body(asyncio.run(M.dci_schema_logic()))
    check("dci/schema: enabled key present", "enabled" in dsch and "acts" in dsch)

    print(f"\n{_fails} FAILED" if _fails else "\nok")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
