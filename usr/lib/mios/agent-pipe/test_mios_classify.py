# AI-hint: Stdlib unit tests for mios_classify (layer-1 micro-LLM classifiers).
# AI-related: mios_classify.py, mios_config.py
"""Stdlib assert-tests for mios_classify (no network/DB)."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

import mios_classify as M

class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p

class _FakeClient:
    """Async-context fake replacing httpx.AsyncClient; returns a canned response."""
    _resp = (200, {})

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        return _FakeResp(*self._resp)

def _configure(domains=None, enable=True):
    fired = []
    M.configure(
        verb_catalog={"open_app": {}, "read_file": {}},
        routing_domains=domains if domains is not None else {
            "web": {"desc": "web research", "verbs": ["web_search"]}},
        routing_enable=enable,
        db_create=lambda table, fields, **k: {"table": table, "fields": fields},
        db_post=lambda row: row,
        db_fire=lambda row: fired.append(row),
    )
    return fired

class TestClassifyIntent(unittest.TestCase):
    def setUp(self):
        _configure()
        self._real = M.httpx.AsyncClient

    def tearDown(self):
        M.httpx.AsyncClient = self._real

    def test_empty_returns_none(self):
        self.assertIsNone(asyncio.run(M.classify_intent("")))
        self.assertIsNone(asyncio.run(M.classify_intent("   ")))

    def test_parses_verdict_and_fires_event(self):
        fired = _configure()
        _FakeClient._resp = (200, {"choices": [
            {"message": {"content": '{"action":"chat","reply":"hi"}'}}]})
        M.httpx.AsyncClient = _FakeClient
        out = asyncio.run(M.classify_intent("hello there"))
        self.assertEqual(out, {"action": "chat", "reply": "hi"})
        self.assertEqual(len(fired), 1)                    # event row fired
        self.assertEqual(fired[0]["fields"]["kind"], "classify")

    def test_non_200_degrades_to_none(self):
        _FakeClient._resp = (500, {})
        M.httpx.AsyncClient = _FakeClient
        self.assertIsNone(asyncio.run(M.classify_intent("hello there")))

class TestRouteDomain(unittest.TestCase):
    def setUp(self):
        self._real = M.httpx.AsyncClient

    def tearDown(self):
        M.httpx.AsyncClient = self._real

    def test_disabled_returns_none(self):
        _configure(enable=False)
        self.assertIsNone(asyncio.run(M._route_domain("do some web research")))

    def test_in_enum_domain_returned(self):
        _configure(enable=True)
        _FakeClient._resp = (200, {"choices": [
            {"message": {"content": '{"domain":"web"}'}}]})
        M.httpx.AsyncClient = _FakeClient
        self.assertEqual(asyncio.run(M._route_domain("research X on the web")), "web")

    def test_conversation_is_representable_without_a_capability_domain(self):
        _configure(enable=True)
        captured = []
        class Client(_FakeClient):
            async def post(self, url, **kwargs):
                captured.append(kwargs["json"])
                return _FakeResp(200, {"choices": [{"message": {"content": '{"domain":null}'}}]})
        M.httpx.AsyncClient = Client
        self.assertIsNone(asyncio.run(M._route_domain("A conversational turn.")))
        domain = captured[0]["response_format"]["json_schema"]["schema"]["properties"]["domain"]
        self.assertIn("null", domain["type"])
        self.assertIn(None, domain["enum"])
        self.assertIn("web", domain["enum"])

    def test_out_of_enum_fails_open_to_none(self):
        _configure(enable=True)
        _FakeClient._resp = (200, {"choices": [
            {"message": {"content": '{"domain":"bogus"}'}}]})
        M.httpx.AsyncClient = _FakeClient
        self.assertIsNone(asyncio.run(M._route_domain("research X on the web")))


class TestCanonicalMicroModel(unittest.TestCase):
    """Exercise the shipped TOML, real config reader and both classifier payloads."""

    def test_canonical_micro_model_reaches_router_and_domain_planner(self):
        root = Path(__file__).resolve().parents[4]
        vendor = root / "usr/share/mios/mios.toml"
        source = vendor.read_text()
        data = tomllib.loads(source)
        expected = data["ai"]["micro_model"]
        self.assertTrue(expected)
        self.assertNotIn("micro_model", data["ai"]["host_thresholds"])
        self.assertNotIn("ai.micro_model", data["ssot_consumers"]["unresolved"])
        self.assertEqual(data["ssot_consumers"]["max_unresolved"],
                         len(data["ssot_consumers"]["unresolved"]))
        child = r'''import asyncio, json, sys
# The canonical file is authoritative here; the optional DB is unavailable offline.
sys.modules["psycopg"] = None
import mios_config as c
import mios_classify as classify
expected = sys.argv[1]
assert c._MICRO_MODEL == expected, "_MICRO_MODEL ignored canonical ai.micro_model"
assert c.ROUTER_MODEL == expected, "ROUTER_MODEL ignored canonical ai.micro_model"
assert c.PLANNER_MODEL == c._STACK_MODEL, "primary planner model changed"
calls = []
class Response:
    status_code = 200
    def json(self):
        return {"choices": [{"message": {"content": json.dumps({"action": "chat", "domain": "web"})}}]}
class Client:
    def __init__(self, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def post(self, url, **kwargs):
        calls.append((url, kwargs["json"]["model"]))
        return Response()
classify.httpx.AsyncClient = Client
classify.configure(verb_catalog={"web_search": {}},
    routing_domains={"web": {"desc": "web research", "verbs": ["web_search"]}},
    routing_enable=True, db_create=lambda *args, **kwargs: {},
    db_post=lambda row: row, db_fire=lambda row: None)
assert asyncio.run(classify.classify_intent("hello"))["action"] == "chat"
assert asyncio.run(classify._route_domain("research today's news")) == "web"
assert len(calls) == 2 and all(model == expected for _, model in calls), calls
print(json.dumps({"micro": c._MICRO_MODEL, "router": c.ROUTER_MODEL,
                  "primary": c._STACK_MODEL, "planner": c.PLANNER_MODEL,
                  "classifier_calls": len(calls)}))
'''
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("MIOS_", "PYTHON"))}
        with tempfile.TemporaryDirectory(prefix="mios-micro-config-") as tmp:
            env.update(HOME=tmp, XDG_CONFIG_HOME=tmp,
                       PYTHONPATH=os.pathsep.join((str(root / "usr/lib/mios/agent-pipe"),
                                                  str(root / "usr/lib/mios"))),
                       PYTHONDONTWRITEBYTECODE="1", MIOS_TOML_ROOT=str(root),
                       MIOS_VENDOR_TOML=str(vendor), MIOS_HOST_TOML="/dev/null",
                       MIOS_USER_TOML="/dev/null", MIOS_VENDOR_TOML_D=tmp + "/vendor.d",
                       MIOS_HOST_TOML_D=tmp + "/host.d", MIOS_USER_TOML_D=tmp + "/user.d",
                       MIOS_DB_AUTHORITATIVE="false")
            result = subprocess.run([sys.executable, "-c", child, expected], env=env,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["classifier_calls"], 2)
            # Recreate the original misplaced field in a private full-vendor copy.
            line, = [line for line in source.splitlines(keepends=True)
                     if line.startswith("micro_model ")]
            broken = source.replace(line, "").replace("[ai.host_thresholds]\n",
                                                       "[ai.host_thresholds]\n" + line)
            broken_vendor = Path(tmp) / "mios.toml"
            broken_vendor.write_text(broken)
            env["MIOS_VENDOR_TOML"] = str(broken_vendor)
            negative = subprocess.run([sys.executable, "-c", child, expected], env=env,
                                      capture_output=True, text=True, timeout=20)
            self.assertNotEqual(negative.returncode, 0)
            self.assertIn("_MICRO_MODEL ignored canonical ai.micro_model", negative.stderr)

if __name__ == "__main__":
    unittest.main()
