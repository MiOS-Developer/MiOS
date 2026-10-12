# AI-hint: Stdlib unit test for the extracted A2A federation publish surface (mios_a2a).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_a2a (A2A federation publish surface). Pure stdlib + stubs."""
import asyncio
import contextvars
import json
import logging
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

import mios_a2a

class _FakeApp:
    description = "MiOS test agent"
    version = "9.9.9"

class _FakePriv:
    def sign(self, b: bytes) -> bytes:
        return b"\x01\x02\x03signature"

class _FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "stub answer"}}]}

class _FakeClient:
    async def post(self, url, json=None, headers=None, timeout=None):
        return _FakeResp()

async def _fake_get_client():
    return _FakeClient()

_ENV_VAR = contextvars.ContextVar("client_env", default={})

def _configure():
    mios_a2a.configure(
        app=_FakeApp(),
        agent_registry={"hermes": {"role": "general", "default": True,
                                   "strengths": ["reasoning", "tools"]}},
        verb_catalog={"web_search": {"tier": "core", "desc": "search the web",
                                     "section": "web", "permission": "read"}},
        scratchpads={},
        agent_lane=lambda cfg: "gpu",
        agent_skill_tags=lambda cfg: ["reasoning", "tools"],
        match_user_cfg=lambda: ("mios", {"max_permission": "interactive"}),
        cap_skills=lambda: {},
        get_client=_fake_get_client,
        api_require_auth=True,
        client_env_var=_ENV_VAR,
        passport_load_priv=lambda: _FakePriv(),
        passport_canonical_json=lambda o: json.dumps(o, sort_keys=True,
                                                     separators=(",", ":")),
        passport_kid=lambda: "mios-test-kid",
        passport_sign=lambda table, fields: {"agent": "agent-pipe",
                                             "table": table, **fields},
        passport_verify=lambda *a, **k: (False, "stub", {}),
        passport_algo="ed25519",
        passport_enable=True,
        passport_agent_name="agent-pipe",
    )

class TestAgentCard(unittest.TestCase):
    def setUp(self):
        _configure()

    def test_card_shape_and_signature(self):
        card = mios_a2a._build_agent_card()
        self.assertEqual(card["description"], "MiOS test agent")
        self.assertEqual(card["version"], "9.9.9")
        self.assertEqual(len(card["skills"]), 1)
        sk = card["skills"][0]
        self.assertEqual(sk["id"], "hermes")
        self.assertEqual(sk["tags"], ["reasoning", "tools"])
        self.assertIn("text/plain", sk["inputModes"])
        self.assertIn("securitySchemes", card)
        self.assertIn("security", card)   # api_require_auth=True
        self.assertIn("signatures", card)
        sig = card["signatures"][0]
        self.assertEqual(set(sig.keys()), {"protected", "signature"})
        hdr = json.loads(mios_a2a._b64u_decode(sig["protected"]))
        self.assertEqual(hdr["alg"], "EdDSA")
        self.assertEqual(hdr["kid"], "mios-test-kid")

    def test_card_is_a2a_v1_schema(self):
        """The AgentCard emits the A2A v1.0 schema (authoritative spec a2a.proto
        v1.0.x + spec §8.5): supportedInterfaces[] (no top-level url/
        preferredTransport/additionalInterfaces/protocolVersion), per-interface
        protocolVersion = the v1.0 value, the securitySchemes discriminated-union
        shape, and the card is valid + parseable JSON."""
        card = mios_a2a._build_agent_card()
        for gone in ("protocolVersion", "url", "preferredTransport",
                     "additionalInterfaces", "supportsAuthenticatedExtendedCard"):
            self.assertNotIn(gone, card, gone)
        ifaces = card["supportedInterfaces"]
        self.assertIsInstance(ifaces, list)
        self.assertEqual(ifaces[0]["protocolBinding"], "JSONRPC")
        self.assertEqual({i["protocolBinding"] for i in ifaces},
                         {"JSONRPC", "OpenAI"})
        for i in ifaces:
            self.assertIn("url", i)
            self.assertEqual(i["protocolVersion"], mios_a2a.A2A_PROTOCOL_VERSION)
        self.assertEqual(mios_a2a.A2A_PROTOCOL_VERSION, "1.0")
        bearer = card["securitySchemes"]["bearer"]
        self.assertIn("httpAuthSecurityScheme", bearer)
        self.assertNotIn("type", bearer)
        self.assertEqual(bearer["httpAuthSecurityScheme"]["scheme"], "bearer")
        self.assertNotIn("stateTransitionHistory", card["capabilities"])
        self.assertTrue(card["x-mios"]["contextSharing"])
        self.assertEqual(json.loads(json.dumps(card))["name"], card["name"])

    def test_card_unsigned_when_no_key(self):
        mios_a2a.configure(passport_load_priv=lambda: None)
        card = mios_a2a._build_agent_card()
        self.assertNotIn("signatures", card)
        _configure()   # restore the signer for other tests

    def test_passport_shape(self):
        doc = mios_a2a._build_agent_passport()
        self.assertEqual(doc["version"], mios_a2a.AGENT_PASSPORT_VERSION)
        self.assertIn("issuer", doc)
        self.assertIn("agent", doc)
        self.assertIn("authority", doc)
        self.assertIn("signature", doc)
        self.assertEqual(doc["signature"]["value"], "")
        self.assertIn("x-mios-unsigned", doc)

    def test_agntcy_manifest_shape(self):
        man = mios_a2a._build_agntcy_manifest()
        self.assertEqual(man["oasf_version"], mios_a2a.AGNTCY_OASF_SCHEMA_VERSION)
        proto_names = {p["name"] for p in man["protocols"]}
        self.assertEqual(proto_names, {"A2A", "MCP", "OpenAI"})
        self.assertEqual(len(man["features"]), 1)   # one agent skill -> one feature
        self.assertTrue(man["capabilities"]["tool_use"])

class TestAgentCardJWS(unittest.TestCase):
    """U3: the AgentCard `signatures[]` is a real A2A v1.0 JWS (RFC-7515 over RFC-8785
    JCS), proven with a real Ed25519 key -- the spec mandates JWS, so the proof is a
    cryptographic sign->verify round-trip, not just a shape check. A tampered card or
    tampered signature FAILS verification; a non-EdDSA alg is rejected; the protected
    header decodes to the JOSE-standard {alg: EdDSA, kid}. Skipped cleanly where
    python3-cryptography is absent (the build host), exactly like the passport
    real-key round-trip in test_mios_a2a_principal."""

    def setUp(self):
        _configure()
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey)
        except Exception:  # noqa: BLE001 -- crypto lib optional on the build host
            self.skipTest("cryptography unavailable")
        self._priv = Ed25519PrivateKey.generate()
        self._pub = self._priv.public_key()
        mios_a2a.configure(
            passport_load_priv=lambda: self._priv,
            passport_kid=lambda: "mios-card-test-v1",
            passport_load_public=lambda agent: self._pub,
        )

    def _signed_card(self):
        card = mios_a2a._build_agent_card()
        self.assertIn("signatures", card)
        return card

    def test_protected_header_is_jose_eddsa(self):
        sig = self._signed_card()["signatures"][0]
        self.assertEqual(set(sig.keys()), {"protected", "signature"})
        hdr = json.loads(mios_a2a._b64u_decode(sig["protected"]))
        self.assertEqual(hdr["alg"], "EdDSA")            # RFC-8037 JWS alg name
        self.assertEqual(hdr["kid"], "mios-card-test-v1")  # from the key store (SSOT)

    def test_sign_verify_roundtrip(self):
        card = self._signed_card()
        verdict, reason = mios_a2a._verify_agent_card_signature(card)
        self.assertTrue(verdict, reason)
        self.assertEqual(reason, "ok")
        v2, r2 = mios_a2a._verify_agent_card_signature(card, public_key=self._pub)
        self.assertTrue(v2, r2)

    def test_tampered_card_fails(self):
        card = self._signed_card()
        card["description"] = str(card["description"]) + " (tampered)"
        verdict, reason = mios_a2a._verify_agent_card_signature(card)
        self.assertFalse(verdict)
        self.assertEqual(reason, "invalid_signature")

    def test_tampered_signature_fails(self):
        card = self._signed_card()
        good = mios_a2a._b64u_decode(card["signatures"][0]["signature"])
        flipped = bytes([good[0] ^ 0xFF]) + good[1:]   # any bit flip breaks Ed25519
        card["signatures"][0]["signature"] = mios_a2a._b64u(flipped)
        verdict, reason = mios_a2a._verify_agent_card_signature(card)
        self.assertFalse(verdict)
        self.assertEqual(reason, "invalid_signature")

    def test_unsupported_alg_rejected(self):
        card = self._signed_card()
        bad_hdr = json.dumps({"alg": "RS256", "kid": "x"},
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        card["signatures"][0]["protected"] = mios_a2a._b64u(bad_hdr)
        verdict, reason = mios_a2a._verify_agent_card_signature(card)
        self.assertFalse(verdict)
        self.assertTrue(reason.startswith("unsupported_alg"), reason)

    def test_unsigned_card_verdict_none(self):
        card = self._signed_card()
        card.pop("signatures", None)
        verdict, reason = mios_a2a._verify_agent_card_signature(card)
        self.assertIsNone(verdict)
        self.assertEqual(reason, "unsigned")

    def test_signing_input_excludes_signatures_field(self):
        card = self._signed_card()
        prot = card["signatures"][0]["protected"]
        with_sigs = mios_a2a._agent_card_signing_input(prot, card)
        bare = {k: v for k, v in card.items() if k != "signatures"}
        without = mios_a2a._agent_card_signing_input(prot, bare)
        self.assertEqual(with_sigs, without)

class TestSkillDirectory(unittest.TestCase):
    def setUp(self):
        _configure()

    def test_skill_directory_projection(self):
        res = asyncio.run(mios_a2a.a2a_skill_directory_logic())
        body = json.loads(res.body)
        self.assertEqual(body["object"], "mios.a2a.skill_directory")
        self.assertEqual(body["ceiling"], "interactive")
        self.assertIsInstance(body["skills"], list)

class TestJsonRpc(unittest.TestCase):
    def setUp(self):
        _configure()
        mios_a2a._A2A_PRINCIPAL_REQUIRE = False

    def test_unknown_method(self):
        out = asyncio.run(mios_a2a._a2a_jsonrpc_dispatch(
            {"id": 1, "method": "no/such"}))
        self.assertEqual(out["error"]["code"], -32601)

    def test_tasks_get_not_found(self):
        out = asyncio.run(mios_a2a._a2a_jsonrpc_dispatch(
            {"id": 2, "method": "tasks/get", "params": {"id": "missing"}}))
        self.assertEqual(out["error"]["code"], mios_a2a._A2A_ERR_TASK_NOT_FOUND)

    def test_error_carries_google_rpc_detail(self):
        out = asyncio.run(mios_a2a._a2a_jsonrpc_dispatch(
            {"id": 9, "method": "GetTask", "params": {"id": "nope"}}))
        data = out["error"]["data"]
        self.assertIsInstance(data, list)
        info = data[0]
        self.assertEqual(info["@type"],
                         "type.googleapis.com/google.rpc.ErrorInfo")
        self.assertEqual(info["reason"], "TASK_NOT_FOUND")
        self.assertEqual(info["domain"], "a2a-protocol.org")
        self.assertEqual(info["metadata"]["taskId"], "nope")

    def test_pascalcase_methods_accepted(self):
        for method in ("GetTask", "tasks/get"):
            out = asyncio.run(mios_a2a._a2a_jsonrpc_dispatch(
                {"id": 1, "method": method, "params": {"id": "x"}}))
            self.assertEqual(out["error"]["code"],
                             mios_a2a._A2A_ERR_TASK_NOT_FOUND, method)

    def test_make_task_uses_v1_state_tokens(self):
        task = mios_a2a._a2a_make_task("", {"role": "user", "parts": [
            {"text": "hi"}]})
        self.assertEqual(task["status"]["state"], "TASK_STATE_SUBMITTED")
        self.assertNotIn("kind", task)
        self.assertEqual(task["history"][0]["role"], "ROLE_USER")
        self.assertIn("TASK_STATE_COMPLETED", mios_a2a._A2A_TERMINAL)

    def test_tasks_get_missing_id(self):
        out = asyncio.run(mios_a2a._a2a_jsonrpc_dispatch(
            {"id": 3, "method": "tasks/get", "params": {}}))
        self.assertEqual(out["error"]["code"], -32602)

    def test_message_send_roundtrip(self):
        msg = {"id": 4, "method": "message/send",
               "params": {"message": {"role": "user",
                          "parts": [{"kind": "text", "text": "hello"}]}}}
        out = asyncio.run(mios_a2a._a2a_jsonrpc_dispatch(msg))
        task = out["result"]["task"]          # v1.0 SendMessageResponse.task
        self.assertNotIn("kind", task)        # v1.0 Task has no `kind` discriminator
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        part = task["artifacts"][0]["parts"][0]
        self.assertEqual(part["text"], "stub answer")
        self.assertEqual(part["mediaType"], "text/plain")
        self.assertNotIn("kind", part)
        self.assertEqual(task["history"][-1]["role"], "ROLE_AGENT")

    def test_text_from_message_accepts_both_part_shapes(self):
        txt = mios_a2a._a2a_text_from_message(
            {"parts": [{"kind": "text", "text": "a"},
                       {"type": "text", "text": "b"},
                       {"text": "c", "mediaType": "text/plain"},
                       {"kind": "data", "data": {"x": 1}}]})
        self.assertEqual(txt, "a\nb\nc")

class TestPrincipal(unittest.TestCase):
    def setUp(self):
        _configure()

    def test_metadata_none_when_disabled(self):
        mios_a2a.configure(passport_enable=False)
        self.assertFalse(mios_a2a.PASSPORT_ENABLE)
        self.assertIsNone(
            mios_a2a._a2a_principal_metadata("text", "peer-1", "ctx-1"))
        _configure()

    def test_metadata_built_when_enabled(self):
        _ENV_VAR.set({"user_name": "corey"})
        md = mios_a2a._a2a_principal_metadata("text", "peer-1", "ctx-1")
        self.assertIsInstance(md, dict)

class _FakeReq:
    """Minimal stand-in for a Starlette Request: only .json() is exercised."""
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    async def json(self):
        if self._exc is not None:
            raise self._exc
        return self._payload

class _FakeRep:
    """Reputation double: rank() is the stable identity sort the logic relies on."""
    def rank(self, ids):
        return list(ids)

async def _fake_send_to_peer(peer_id, text, context_id=None):
    return {"kind": "task", "id": "t-1", "peer_id": peer_id,
            "contextId": context_id, "echo": text}

def _configure_peer_routes():
    """Inject the consumer-side A2A deps the /v1/a2a/* route logic reads."""
    mios_a2a.configure(
        a2a_peers={"peer-1": {"status": "ready", "skills": [
            {"id": "sk1", "name": "S1", "description": "d", "tags": ["t"]}]}},
        a2a_peer_skills={"sk1": ["peer-1"]},
        a2a_peers_lock=asyncio.Lock(),
        a2a_reputation=_FakeRep(),
        a2a_send_message_to_peer=_fake_send_to_peer,
    )

class TestJsonRpcRouteLogic(unittest.TestCase):
    def setUp(self):
        _configure()
        mios_a2a._A2A_PRINCIPAL_REQUIRE = False

    def test_jsonrpc_logic_message_send(self):
        req = _FakeReq({"id": 1, "method": "message/send",
                        "params": {"message": {"role": "user", "parts": [
                            {"kind": "text", "text": "hello"}]}}})
        res = asyncio.run(mios_a2a.a2a_jsonrpc_logic(req))
        body = json.loads(res.body)
        self.assertEqual(body["result"]["task"]["status"]["state"],
                         "TASK_STATE_COMPLETED")

    def test_jsonrpc_logic_unknown_method(self):
        req = _FakeReq({"id": 2, "method": "no/such"})
        res = asyncio.run(mios_a2a.a2a_jsonrpc_logic(req))
        self.assertEqual(json.loads(res.body)["error"]["code"], -32601)

    def test_jsonrpc_logic_batch(self):
        req = _FakeReq([{"id": 3, "method": "no/such"}])
        res = asyncio.run(mios_a2a.a2a_jsonrpc_logic(req))
        body = json.loads(res.body)
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["error"]["code"], -32601)

    def test_jsonrpc_logic_parse_error(self):
        res = asyncio.run(mios_a2a.a2a_jsonrpc_logic(_FakeReq(exc=ValueError("boom"))))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(json.loads(res.body)["error"]["code"], -32700)

class TestPeerRouteLogic(unittest.TestCase):
    def setUp(self):
        _configure()
        _configure_peer_routes()

    def test_skills_list_logic(self):
        res = asyncio.run(mios_a2a.a2a_skills_list_logic())
        body = json.loads(res.body)
        self.assertEqual(body["object"], "mios.a2a.skills")
        self.assertEqual(body["skills"][0]["id"], "sk1")
        self.assertEqual(body["skills"][0]["peers"][0]["peer_id"], "peer-1")

    def test_dispatch_logic_by_skill(self):
        res = asyncio.run(mios_a2a.a2a_dispatch_logic(
            _FakeReq({"skill": "sk1", "text": "hi"})))
        body = json.loads(res.body)
        self.assertEqual(body["peer_id"], "peer-1")
        self.assertEqual(body["echo"], "hi")

    def test_dispatch_logic_missing_text(self):
        res = asyncio.run(mios_a2a.a2a_dispatch_logic(
            _FakeReq({"peer_id": "peer-1"})))
        self.assertEqual(res.status_code, 400)
        self.assertIn("missing", json.loads(res.body)["error"])

    def test_dispatch_logic_no_peer(self):
        res = asyncio.run(mios_a2a.a2a_dispatch_logic(
            _FakeReq({"skill": "nope", "text": "hi"})))
        self.assertEqual(res.status_code, 404)

class TestPassportRouteLogic(unittest.TestCase):
    def setUp(self):
        _configure()
        mios_a2a.configure(
            passport_verify=lambda env, payload=None: (True, "ok"),
            passport_load_public=lambda agent: (
                object() if agent == "agent-pipe" else None),
        )

    def test_passport_verify_logic_ok(self):
        res = asyncio.run(mios_a2a.passport_verify_logic(
            _FakeReq({"envelope": {"agent": "a", "kid": "k", "alg": "ed25519"}})))
        body = json.loads(res.body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["reason"], "ok")
        self.assertEqual(body["agent"], "a")

    def test_passport_verify_logic_bad_json(self):
        res = asyncio.run(mios_a2a.passport_verify_logic(_FakeReq(exc=ValueError("x"))))
        self.assertEqual(res.status_code, 400)
        self.assertIn("invalid JSON", json.loads(res.body)["error"])

    def test_passport_verify_logic_no_envelope(self):
        res = asyncio.run(mios_a2a.passport_verify_logic(_FakeReq({"foo": 1})))
        self.assertEqual(res.status_code, 400)
        self.assertIn("envelope", json.loads(res.body)["error"])

    def test_passport_public_key_logic_missing(self):
        res = asyncio.run(mios_a2a.passport_public_key_logic("nobody"))
        self.assertEqual(res.status_code, 404)
        self.assertIn("no public key", json.loads(res.body)["error"])

class _FakeAuthReq:
    """Request stand-in carrying both .headers (for the admin gate) and .json()."""
    def __init__(self, payload=None, auth="", exc=None):
        self._payload = payload
        self._exc = exc
        self.headers = {"authorization": auth} if auth else {}

    async def json(self):
        if self._exc is not None:
            raise self._exc
        return self._payload

class TestPrincipalModeVerify(unittest.TestCase):
    """task_14 FED-G6: the 'verify' tier runs the SAME check enforce does but, on a
    failed/absent principal, emits a STRUCTURED audit record and ALLOWS the request
    through. off/enforce behaviour is unchanged."""

    def setUp(self):
        _configure()
        self._mode = mios_a2a._A2A_PRINCIPAL_MODE
        self._req = mios_a2a._A2A_PRINCIPAL_REQUIRE

    def tearDown(self):
        mios_a2a._A2A_PRINCIPAL_MODE = self._mode
        mios_a2a._A2A_PRINCIPAL_REQUIRE = self._req

    def _send(self, metadata=None):
        msg = {"role": "user", "parts": [{"kind": "text", "text": "hello"}]}
        if metadata is not None:
            msg["metadata"] = metadata
        return asyncio.run(mios_a2a._a2a_jsonrpc_dispatch(
            {"id": 1, "method": "message/send", "params": {"message": msg}}))

    def test_mode_parsing_tristate(self):
        for raw, want in (("off", "off"), ("verify", "verify"),
                          ("enforce", "enforce"), ("require", "enforce"),
                          ("true", "enforce"), ("", "off"), ("bogus", "off")):
            os.environ["MIOS_A2A_PRINCIPAL_MODE"] = raw
            try:
                self.assertEqual(mios_a2a._principal_mode(), want, raw)
            finally:
                os.environ.pop("MIOS_A2A_PRINCIPAL_MODE", None)

    def test_verify_absent_audits_and_passes(self):
        mios_a2a._A2A_PRINCIPAL_MODE = "verify"
        mios_a2a._A2A_PRINCIPAL_REQUIRE = False
        with self.assertLogs("mios-agent-pipe", level="WARNING") as cm:
            out = self._send(metadata=None)          # no principal block -> _pv is None
        self.assertEqual(out["result"]["task"]["status"]["state"],
                         "TASK_STATE_COMPLETED")                           # ALLOWED
        self.assertTrue(any("a2a-principal-audit" in m and "absent" in m
                            for m in cm.output))

    def test_verify_failed_audits_and_passes(self):
        mios_a2a._A2A_PRINCIPAL_MODE = "verify"
        mios_a2a._A2A_PRINCIPAL_REQUIRE = False
        md = {"mios_principal": {"claims": {"agent": "rogue", "principal": "p",
                                            "text_sha256": "00"}}}
        with self.assertLogs("mios-agent-pipe", level="WARNING") as cm:
            out = self._send(metadata=md)
        self.assertEqual(out["result"]["task"]["status"]["state"],
                         "TASK_STATE_COMPLETED")                           # ALLOWED
        self.assertTrue(any("a2a-principal-audit" in m and "verify_failed" in m
                            for m in cm.output))

    def test_enforce_absent_rejects(self):
        mios_a2a._A2A_PRINCIPAL_MODE = "enforce"
        mios_a2a._A2A_PRINCIPAL_REQUIRE = True
        out = self._send(metadata=None)
        self.assertEqual(out["error"]["code"], -32600)                    # REJECTED

    def test_off_absent_passes_without_audit(self):
        mios_a2a._A2A_PRINCIPAL_MODE = "off"
        mios_a2a._A2A_PRINCIPAL_REQUIRE = False
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        logger = logging.getLogger("mios-agent-pipe")
        logger.addHandler(handler)
        try:
            out = self._send(metadata=None)
        finally:
            logger.removeHandler(handler)
        self.assertEqual(out["result"]["task"]["status"]["state"],
                         "TASK_STATE_COMPLETED")                           # ALLOWED
        self.assertFalse(any("a2a-principal-audit" in m for m in records))  # no audit

class TestCallerKeyRevoke(unittest.TestCase):
    """task_52 FED-G8: POST /v1/admin/keys/revoke appends a caller key to the CRL and
    HOT-RELOADS it so the credential is refused on the very next check, no restart."""

    def setUp(self):
        _configure()
        self._dir = tempfile.mkdtemp()
        self._crl = os.path.join(self._dir, "crl.json")
        self._orig_path = mios_a2a._CRL_PATH
        self._orig_cip = mios_a2a._check_inbound_principal
        mios_a2a._CRL_PATH = self._crl
        mios_a2a._CRL_CACHE.clear()
        mios_a2a._CRL_CACHE.update({"mtime": -1.0, "crl": None})
        mios_a2a.configure(check_inbound_principal=lambda tok: (
            {"principal": "operator", "scope": "full"} if tok == "admin-secret"
            else None))

    def tearDown(self):
        mios_a2a._CRL_PATH = self._orig_path
        mios_a2a._check_inbound_principal = self._orig_cip
        mios_a2a._CRL_CACHE.clear()
        mios_a2a._CRL_CACHE.update({"mtime": -1.0, "crl": None})
        for p in (self._crl, self._crl + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(self._dir)
        except OSError:
            pass

    def _seed(self, revoked):
        with open(self._crl, "w", encoding="utf-8") as fh:
            json.dump({"revoked": revoked}, fh)
        mios_a2a._crl_reload()        # load + cache (a CRL already present at startup)

    def test_revoke_appends_and_hot_reloads(self):
        self._seed(["seed-id"])
        crl0 = mios_a2a._load_crl()
        self.assertTrue(crl0.is_revoked("seed-id"))
        fp = mios_a2a._crl_fingerprint("caller-tok-1")
        self.assertFalse(crl0.is_revoked(fp))                  # not revoked yet
        self.assertFalse(mios_a2a._caller_key_revoked("caller-tok-1", {}))
        res = asyncio.run(mios_a2a.caller_key_revoke_logic(
            _FakeAuthReq({"token": "caller-tok-1"}, auth="Bearer admin-secret")))
        self.assertEqual(res.status_code, 200)
        body = json.loads(res.body)
        self.assertEqual(body["object"], "mios.crl.revoke")
        self.assertEqual(body["added"], 1)
        self.assertTrue(mios_a2a._load_crl().is_revoked(fp))
        self.assertTrue(mios_a2a._load_crl().is_revoked("seed-id"))    # union, kept
        self.assertTrue(mios_a2a._caller_key_revoked("caller-tok-1", {}))
        self.assertFalse(mios_a2a._caller_key_revoked("untouched-tok", {}))

    def test_revoke_by_principal_rejected_by_a2a_check(self):
        res = asyncio.run(mios_a2a.caller_key_revoke_logic(
            _FakeAuthReq({"principal": "peer-agent-9"}, auth="Bearer admin-secret")))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(mios_a2a._load_crl().is_revoked("peer-agent-9"))
        self.assertTrue(mios_a2a._caller_key_revoked("", {"principal": "peer-agent-9"}))

    def test_revoke_unauthorized(self):
        res = asyncio.run(mios_a2a.caller_key_revoke_logic(
            _FakeAuthReq({"token": "x"}, auth="Bearer wrong")))
        self.assertEqual(res.status_code, 401)
        self.assertEqual(len(mios_a2a._load_crl()), 0)         # CRL untouched

    def test_revoke_missing_identifier(self):
        res = asyncio.run(mios_a2a.caller_key_revoke_logic(
            _FakeAuthReq({}, auth="Bearer admin-secret")))
        self.assertEqual(res.status_code, 400)



# ==============================================================================
# Consolidated from test_mios_a2a_loopback.py (task_1006)
# ==============================================================================
# AI-hint: Offline unit test for the mios-a2a-test loopback smoke-test helper -- exercises the pure message-builder, artifact extr...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Offline tests for task_68 (A2A federation loopback smoke test).

The network/CLI half of mios-a2a-test needs a live agent-pipe; the pure
protocol helpers (build_message / extract_artifact_text / classify_task) are
exercised here with stub Task payloads so the round-trip's shape logic is
guarded without any live service.
"""
import importlib.machinery
import importlib.util
import os
import sys

_fails_a2a_loopback = 0

def _check_a2a_loopback(name, cond, detail=""):
    global _fails_a2a_loopback
    if not cond:
        _fails_a2a_loopback += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _load_tester():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    path = os.path.join(repo, "usr", "libexec", "mios", "mios-a2a-test")
    loader = importlib.machinery.SourceFileLoader("mios_a2a_test", path)
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("mios_a2a_test", loader))
    loader.exec_module(mod)
    return mod

def test_build_message(T):
    m = T.build_message("hello", "ctx-1", "mid-1")
    _check_a2a_loopback("message has user role", m["role"] == "user")
    _check_a2a_loopback("message carries one text part",
          m["parts"] == [{"kind": "text", "text": "hello"}])
    _check_a2a_loopback("message threads contextId", m.get("contextId") == "ctx-1")
    _check_a2a_loopback("message carries messageId", m.get("messageId") == "mid-1")
    m2 = T.build_message("x", "", "mid-2")
    _check_a2a_loopback("absent contextId omitted (not empty-string)", "contextId" not in m2)

def test_extract_artifact_text(T):
    task_art = {"artifacts": [{"parts": [{"kind": "text", "text": "the answer"}]}]}
    _check_a2a_loopback("artifact text extracted", T.extract_artifact_text(task_art) == "the answer")
    task_hist = {"artifacts": [], "history": [
        {"role": "user", "parts": [{"kind": "text", "text": "q"}]},
        {"role": "agent", "parts": [{"kind": "text", "text": "fallback reply"}]},
    ]}
    _check_a2a_loopback("falls back to last agent history text",
          T.extract_artifact_text(task_hist) == "fallback reply")
    _check_a2a_loopback("empty task -> empty string", T.extract_artifact_text({}) == "")

def test_classify_task(T):
    good = {"id": "t1", "contextId": "c1",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": "done"}]}]}
    v = T.classify_task(good)
    _check_a2a_loopback("completed+artifact => completed", v["completed"] is True)
    _check_a2a_loopback("completed+artifact => has_artifact", v["has_artifact"] is True)
    _check_a2a_loopback("task_id surfaced", v["task_id"] == "t1")
    _check_a2a_loopback("context_id surfaced", v["context_id"] == "c1")

    working = {"id": "t2", "status": {"state": "working"}, "artifacts": []}
    v2 = T.classify_task(working)
    _check_a2a_loopback("non-completed state not marked completed", v2["completed"] is False)
    _check_a2a_loopback("no artifact => has_artifact False", v2["has_artifact"] is False)

    no_art = {"id": "t3", "status": {"state": "completed"}, "artifacts": []}
    v3 = T.classify_task(no_art)
    _check_a2a_loopback("completed but artifact-less flagged", v3["has_artifact"] is False)

def _main_a2a_loopback():
    T = _load_tester()
    test_build_message(T)
    test_extract_artifact_text(T)
    test_classify_task(T)
    print(f"\n{'ok' if _fails_a2a_loopback == 0 else str(_fails_a2a_loopback) + ' FAILED'}")
    return 1 if _fails_a2a_loopback else 0


def _run_extra_a2a_loopback():
    try:
        return _main_a2a_loopback()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0

class TestFolded_a2a_loopback(unittest.TestCase):
    def test_run_folded(self):
        rc = _run_extra_a2a_loopback()
        self.assertIn(rc, (None, 0))



def _run_all_folded_a2a_suites():
    rc = _run_extra_a2a_loopback()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    unittest.main(verbosity=2)
