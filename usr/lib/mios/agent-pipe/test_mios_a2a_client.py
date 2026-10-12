# AI-hint: Stdlib unit test for the extracted A2A peer-client consumer half (mios_a2a_client).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_a2a_client (A2A peer-client consumer half). Pure stdlib + stubs."""
import asyncio
import json
import os
import tempfile

# A fixture directory that outlives the run shows up as a stray tree in an
# editor and accumulates one per run. Registering the removal at creation works
# whether the module ends through unittest or its own main().
import atexit as _atexit
import shutil as _shutil

_mkdtemp_orig = tempfile.mkdtemp

def _mkdtemp_cleaned(*a, **kw):
    _d = _mkdtemp_orig(*a, **kw)
    _atexit.register(_shutil.rmtree, _d, True)
    return _d

tempfile.mkdtemp = _mkdtemp_cleaned

import unittest

import mios_a2a_client
_ORIG_SELF_PEER_URL = mios_a2a_client._a2a_self_peer_url
_ORIG_FETCH_CARD = mios_a2a_client._a2a_fetch_card

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)

class _A2AClientBase(unittest.TestCase):
    """Restore the module-level discovery helpers after any test stubs them."""

    def tearDown(self):
        mios_a2a_client._a2a_self_peer_url = _ORIG_SELF_PEER_URL
        mios_a2a_client._a2a_fetch_card = _ORIG_FETCH_CARD

class _FakeReputation:
    def __init__(self):
        self.calls = []

    def record(self, peer_id, ok):
        self.calls.append((peer_id, bool(ok)))

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

class _FakeClient:
    """Captures the last POST so the test can assert the JSON-RPC body shape."""

    def __init__(self, payload):
        self._payload = payload
        self.last = None

    async def post(self, url, json=None, headers=None, timeout=None):
        self.last = {"url": url, "json": json, "headers": headers}
        return _FakeResp(self._payload)

def _base_configure(*, peers, peer_skills, registry, reputation, client,
                    fetch_card=None, paths=None, self_peer_url=None):
    cache = {"invalidated": 0}

    def _invalidate():
        cache["invalidated"] += 1

    async def _get_client():
        return client

    mios_a2a_client.configure(
        a2a_peers=peers,
        a2a_peer_skills=peer_skills,
        a2a_peers_lock=asyncio.Lock(),
        a2a_reputation=reputation,
        agent_registry=registry,
        a2a_peer_registry_paths=(paths if paths is not None else []),
        a2a_council=False,
        a2a_self_id="local-mios",
        get_client=_get_client,
        invalidate_worker_cache=_invalidate,
    )
    mios_a2a_client._a2a_self_peer_url = (self_peer_url or (lambda u: False))
    if fetch_card is not None:
        mios_a2a_client._a2a_fetch_card = fetch_card
    return cache

class TestLoadPeers(_A2AClientBase):
    def test_layered_dedupe_and_self_exclude(self):
        files = []
        tmp = tempfile.mkdtemp()
        vendor = os.path.join(tmp, "vendor.json")
        user = os.path.join(tmp, "user.json")
        with open(vendor, "w") as f:
            json.dump({"peers": [
                {"id": "p1", "url": "http://a:8640", "enabled": True},
                {"id": "self", "url": "http://127.0.0.1:8640"},
            ]}, f)
        with open(user, "w") as f:
            json.dump({"peers": [
                {"id": "p1", "url": "http://a:8640", "enabled": False},
                {"id": "p2", "url": "http://b:8640"},
            ]}, f)
        files = [vendor, "/nonexistent/missing.json", user]

        def _is_self(u):
            return "127.0.0.1" in (u or "")

        _base_configure(peers={}, peer_skills={}, registry={},
                        reputation=_FakeReputation(), client=_FakeClient({}),
                        paths=files, self_peer_url=_is_self)
        out = mios_a2a_client._a2a_load_peers()
        by_id = {p["id"]: p for p in out}
        self.assertEqual(set(by_id), {"p1", "p2"})        # self excluded
        self.assertFalse(by_id["p1"]["enabled"])

class TestProbePeer(_A2AClientBase):
    def test_indexes_card_and_registers_agent(self):
        peers, peer_skills, registry = {}, {}, {}
        rep = _FakeReputation()

        async def _fetch(url, headers, timeout_s=10.0):
            return {"protocolVersion": "0.3.0", "name": "Peer One",
                    "skills": [{"id": "summarize", "name": "Summarize",
                                "tags": ["text"]}]}

        cache = _base_configure(peers=peers, peer_skills=peer_skills,
                                registry=registry, reputation=rep,
                                client=_FakeClient({}), fetch_card=_fetch)
        _run(mios_a2a_client._a2a_probe_peer(
            {"id": "p1", "url": "http://a:8640"}))
        self.assertEqual(peers["p1"]["status"], "ready")
        self.assertIn("p1", peer_skills.get("summarize", []))
        self.assertIn("a2a:p1", registry)
        self.assertEqual(registry["a2a:p1"]["lane"], "remote")
        self.assertGreaterEqual(cache["invalidated"], 1)

    def test_reads_v1_card_protocol_version_from_interfaces(self):
        peers, peer_skills, registry = {}, {}, {}

        async def _fetch(url, headers, timeout_s=10.0):
            return {"name": "V1 Peer",
                    "supportedInterfaces": [
                        {"url": "http://a:8640/a2a", "protocolBinding": "JSONRPC",
                         "protocolVersion": "1.0"}],
                    "skills": [{"id": "plan", "name": "Plan", "tags": []}]}

        _base_configure(peers=peers, peer_skills=peer_skills, registry=registry,
                        reputation=_FakeReputation(), client=_FakeClient({}),
                        fetch_card=_fetch)
        _run(mios_a2a_client._a2a_probe_peer({"id": "v1", "url": "http://a:8640"}))
        self.assertEqual(peers["v1"]["status"], "ready")
        self.assertEqual(peers["v1"]["protocolVersion"], "1.0")

    def test_card_fetch_failure_marks_state(self):
        peers = {}
        rep = _FakeReputation()

        async def _fetch(url, headers, timeout_s=10.0):
            return {"error": "404 at /.well-known/agent-card.json"}

        _base_configure(peers=peers, peer_skills={}, registry={},
                        reputation=rep, client=_FakeClient({}), fetch_card=_fetch)
        _run(mios_a2a_client._a2a_probe_peer(
            {"id": "bad", "url": "http://x:8640"}))
        self.assertEqual(peers["bad"]["status"], "card-fetch-failed")

class TestSendMessageToPeer(_A2AClientBase):
    def test_jsonrpc_body_shape_and_reputation(self):
        peers = {"p1": {"id": "p1", "url": "http://a:8640", "status": "ready",
                        "headers_template": {}}}
        rep = _FakeReputation()
        client = _FakeClient({"result": {"task": {"id": "t1"}}})
        _base_configure(peers=peers, peer_skills={}, registry={},
                        reputation=rep, client=client)
        out = _run(mios_a2a_client._a2a_send_message_to_peer(
            "p1", "hello peer", context_id="ctx-7"))
        self.assertEqual(out, {"id": "t1"})            # unwrapped from {"task": ...}
        body = client.last["json"]
        self.assertEqual(client.last["url"], "http://a:8640/a2a")
        self.assertEqual(body["method"], "message/send")
        msg = body["params"]["message"]
        self.assertEqual(msg["role"], "ROLE_USER")
        self.assertNotIn("kind", msg)
        self.assertEqual(msg["contextId"], "ctx-7")
        self.assertEqual(msg["parts"][0]["text"], "hello peer")
        self.assertEqual(msg["parts"][0]["mediaType"], "text/plain")
        self.assertNotIn("kind", msg["parts"][0])
        self.assertEqual(rep.calls, [("p1", True)])

    def test_send_unwraps_bare_task_result(self):
        peers = {"p1": {"id": "p1", "url": "http://a:8640", "status": "ready",
                        "headers_template": {}}}
        client = _FakeClient({"result": {"id": "bare-1", "artifacts": []}})
        _base_configure(peers=peers, peer_skills={}, registry={},
                        reputation=_FakeReputation(), client=client)
        out = _run(mios_a2a_client._a2a_send_message_to_peer("p1", "hi"))
        self.assertEqual(out["id"], "bare-1")

    def test_unknown_peer(self):
        _base_configure(peers={}, peer_skills={}, registry={},
                        reputation=_FakeReputation(), client=_FakeClient({}))
        out = _run(mios_a2a_client._a2a_send_message_to_peer("ghost", "hi"))
        self.assertIn("error", out)

class TestExtractText(_A2AClientBase):
    def test_artifacts_then_status_message(self):
        env = {"artifacts": [{"parts": [{"text": "from-artifact"}]}]}
        self.assertEqual(mios_a2a_client._a2a_extract_text(env), "from-artifact")
        env2 = {"status": {"message": {"parts": [{"text": "from-status"}]}}}
        self.assertEqual(mios_a2a_client._a2a_extract_text(env2), "from-status")
        self.assertEqual(mios_a2a_client._a2a_extract_text({"error": "x"}), "")

class _FetchClient:
    """Fake async HTTP client for _a2a_fetch_card: returns a programmed
    (status, payload) per candidate URL in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []

    async def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        status, payload = self._responses.pop(0)
        return _FakeResp(payload, status_code=status)

class TestDiscoveryHelpers(_A2AClientBase):
    """The self-peer-loop guard / agent-card fetch / tailnet candidate helpers
    that now live in the module (moved verbatim out of server.py)."""

    def test_self_peer_url_only_excludes_loopback_on_self_port(self):
        os.environ.pop("MIOS_PORTS_AGENT_PIPE", None)   # falls back to [ports].agent_pipe
        f = mios_a2a_client._a2a_self_peer_url
        self.assertTrue(f("http://127.0.0.1:8700"))
        self.assertTrue(f("http://localhost:8700/v1"))
        self.assertTrue(f("http://[::1]:8700"))
        self.assertFalse(f("http://10.0.0.5:8700"))
        self.assertFalse(f("http://127.0.0.1:9999"))
        self.assertFalse(f(""))

    def test_self_peer_url_honours_configured_port(self):
        os.environ["MIOS_PORTS_AGENT_PIPE"] = "8650"
        try:
            f = mios_a2a_client._a2a_self_peer_url
            self.assertTrue(f("http://127.0.0.1:8650"))
            self.assertFalse(f("http://127.0.0.1:8700"))
        finally:
            os.environ.pop("MIOS_PORTS_AGENT_PIPE", None)

    def test_fetch_card_falls_through_candidates_and_tags_origin(self):
        client = _FetchClient([
            (404, {}),
            (200, {"name": "Peer", "protocolVersion": "0.3.0"}),
        ])

        async def _get_client():
            return client

        mios_a2a_client.configure(get_client=_get_client)
        card = _run(mios_a2a_client._a2a_fetch_card("http://a:8640/", {}))
        self.assertEqual(card["name"], "Peer")
        self.assertEqual(card["_fetched_from"],
                         "http://a:8640/.well-known/agent.json")
        self.assertEqual(len(client.urls), 2)

    def test_fetch_card_all_candidates_fail_returns_error(self):
        client = _FetchClient([(404, {}), (500, {}), (404, {})])

        async def _get_client():
            return client

        mios_a2a_client.configure(get_client=_get_client)
        card = _run(mios_a2a_client._a2a_fetch_card("http://a:8640", {}))
        self.assertIn("error", card)
        self.assertEqual(len(client.urls), 3)

    def test_tailnet_candidates_includes_explicit_urls_deduped(self):
        prev_urls = os.environ.get("MIOS_A2A_DISCOVER_URLS")
        prev_port = os.environ.get("MIOS_A2A_DISCOVER_PORT")
        os.environ["MIOS_A2A_DISCOVER_URLS"] = (
            "http://x:9000, http://y:9000/ , http://x:9000")
        os.environ["MIOS_A2A_DISCOVER_PORT"] = "9000"
        try:
            out = _run(mios_a2a_client._a2a_tailnet_candidates())
        finally:
            if prev_urls is None:
                os.environ.pop("MIOS_A2A_DISCOVER_URLS", None)
            else:
                os.environ["MIOS_A2A_DISCOVER_URLS"] = prev_urls
            if prev_port is None:
                os.environ.pop("MIOS_A2A_DISCOVER_PORT", None)
            else:
                os.environ["MIOS_A2A_DISCOVER_PORT"] = prev_port
        self.assertEqual(out[:2], ["http://x:9000", "http://y:9000"])
        self.assertEqual(len(out), len(set(out)))

class TestCardlessJoin(_A2AClientBase):
    def test_cardless_v1_models_probe_success(self):
        peers, peer_skills, registry = {}, {}, {}
        class _CardlessClient:
            async def get(self, url, headers=None, timeout=None):
                if url.endswith("/v1/models"):
                    return _FakeResp({
                        "object": "list",
                        "data": [
                            {"id": "llama-3-8b", "object": "model"},
                            {"id": "bge-large-en", "object": "model"}
                        ]
                    })
                return _FakeResp({"error": "not found"}, status_code=404)

        _base_configure(peers=peers, peer_skills=peer_skills,
                        registry=registry, reputation=_FakeReputation(),
                        client=_CardlessClient())

        _run(mios_a2a_client._a2a_probe_peer(
            {"id": "cardless-peer", "url": "http://cardless:8640"}))

        self.assertEqual(peers["cardless-peer"]["status"], "ready")
        self.assertEqual(peers["cardless-peer"]["agent_name"], "cardless")
        self.assertTrue(peers["cardless-peer"]["card"]["_cardless"])

        skills = peers["cardless-peer"]["skills"]
        skill_ids = {s["id"] for s in skills}
        self.assertEqual(skill_ids, {"text-generation", "embeddings"})

        self.assertIn("a2a:cardless-peer", registry)
        self.assertEqual(set(registry["a2a:cardless-peer"]["strengths"]), {"text-generation", "embeddings"})

if __name__ == "__main__":
    unittest.main()
