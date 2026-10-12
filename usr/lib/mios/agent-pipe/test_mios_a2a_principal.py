#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_a2a_principal (#60 WS-6 signed A2A delegation principal). Pure stdlib, no ...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_a2a_principal (signed A2A delegation principal helpers)."""

import hashlib
import json
import os
import sys
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

import mios_a2a_principal as ap
_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def sign_real(table, fields):
    """Deterministic fake passport: binds to a hash of (table, sorted-fields)."""
    blob = table + "|" + repr(sorted(fields.items()))
    return {"sig": hashlib.sha256(blob.encode("utf-8")).hexdigest(), "table": table}

def sign_nokey(table, fields):
    """No key provisioned -> degrade-open (unsigned)."""
    return None

def verify_real(passport, key):
    """Re-derive what sign_real would produce and compare."""
    table, fields = key
    if not isinstance(passport, dict):
        return False, "no_envelope"
    expect = sign_real(table, fields)
    if passport.get("sig") == expect["sig"]:
        return True, "ok"
    return False, "bad_signature"

def verify_always_true(passport, key):
    return True, "ok"

def verify_should_not_run(passport, key):
    raise AssertionError("verify_fn must NOT be invoked when text digest mismatches")

def t_text_digest():
    d = ap.text_digest("hello world")
    check("digest: matches stdlib sha256 hexdigest",
          d == hashlib.sha256(b"hello world").hexdigest(), d)
    check("digest: 64 hex chars", len(d) == 64 and all(c in "0123456789abcdef" for c in d))
    check("digest: deterministic across calls",
          ap.text_digest("same input") == ap.text_digest("same input"))
    check("digest: sensitive to single-char change",
          ap.text_digest("delegate task A") != ap.text_digest("delegate task B"))
    check("digest: whitespace is significant",
          ap.text_digest("a b") != ap.text_digest("a  b"))
    empty = hashlib.sha256(b"").hexdigest()
    check("digest: None -> empty-string digest", ap.text_digest(None) == empty)
    check("digest: '' -> empty-string digest", ap.text_digest("") == empty)
    check("digest: None == '' (both falsy->'')", ap.text_digest(None) == ap.text_digest(""))
    check("digest: int coerced via str()",
          ap.text_digest(123) == hashlib.sha256(b"123").hexdigest())
    check("digest: 0 is falsy -> empty digest (not '0')", ap.text_digest(0) == empty)
    check("digest: unicode utf-8 encoded",
          ap.text_digest("éè") == hashlib.sha256("éè".encode("utf-8")).hexdigest())

def t_build_claims():
    c = ap.build_claims("agentX", "alice", "peer1", "ctx42", "do the thing")
    check("claims: exactly the 5 required keys",
          set(c.keys()) == {"agent", "principal", "peer", "context", "text_sha256"},
          sorted(c.keys()))
    check("claims: all values are str", all(isinstance(v, str) for v in c.values()))
    check("claims: agent passthrough", c["agent"] == "agentX")
    check("claims: principal passthrough", c["principal"] == "alice")
    check("claims: peer passthrough", c["peer"] == "peer1")
    check("claims: context passthrough", c["context"] == "ctx42")
    check("claims: text bound as digest, not raw text",
          c["text_sha256"] == ap.text_digest("do the thing") and "do the thing" not in c["text_sha256"])
    auto = ap.build_claims("agentX", None, "peer1", "ctx42", "t")
    check("claims: None principal -> '' (autonomous)", auto["principal"] == "")
    none_all = ap.build_claims(None, None, None, None, None)
    check("claims: all-None -> all '' except digest",
          none_all["agent"] == "" and none_all["peer"] == "" and none_all["context"] == "")
    check("claims: all-None text -> empty-string digest",
          none_all["text_sha256"] == ap.text_digest(""))
    numc = ap.build_claims(7, 8, 9, 10, "x")
    check("claims: numeric ids stringified", numc["agent"] == "7" and numc["context"] == "10")

def t_build_metadata():
    m = ap.build_metadata("agentX", "alice", "peer1", "ctx42", "task text", sign_real)
    check("metadata: exactly {claims, passport}", set(m.keys()) == {"claims", "passport"}, sorted(m.keys()))
    check("metadata: claims is the build_claims dict",
          m["claims"] == ap.build_claims("agentX", "alice", "peer1", "ctx42", "task text"))
    check("metadata: passport present when key provisioned", isinstance(m["passport"], dict))
    check("metadata: passport signs the TABLE constant",
          m["passport"].get("table") == ap.TABLE == "a2a_delegation")
    check("metadata: passport sig binds to the exact claims",
          m["passport"]["sig"] == sign_real(ap.TABLE, m["claims"])["sig"])
    mu = ap.build_metadata("agentX", "alice", "peer1", "ctx42", "task text", sign_nokey)
    check("metadata: unsigned passport is None when no key", mu["passport"] is None)
    check("metadata: claims still present when unsigned", isinstance(mu["claims"], dict) and mu["claims"]["agent"] == "agentX")

def _wrap(meta):
    """Wrap a build_metadata() result under the on-wire METADATA_KEY envelope."""
    return {ap.METADATA_KEY: meta}

def t_verify_valid_roundtrip():
    text = "please summarize the report"
    meta = ap.build_metadata("agentX", "alice", "peer1", "ctx42", text, sign_real)
    verdict, reason, claims = ap.verify(_wrap(meta), text, verify_real)
    check("verify: valid self-built claim accepted", verdict is True, reason)
    check("verify: reason ok on success", reason == "ok")
    check("verify: returns the claims dict", claims == meta["claims"])

def t_verify_tampered_text():
    text = "transfer $10 to alice"
    meta = ap.build_metadata("agentX", "alice", "peer1", "ctx42", text, sign_real)
    verdict, reason, claims = ap.verify(_wrap(meta), "transfer $10000 to mallory", verify_real)
    check("verify: tampered text rejected", verdict is False, reason)
    check("verify: reason is text_digest_mismatch", reason == "text_digest_mismatch")
    check("verify: claims still returned on mismatch", claims == meta["claims"])
    v2, r2, _ = ap.verify(_wrap(meta), "totally different task", verify_should_not_run)
    check("verify: digest checked before signature (verify_fn not called)",
          v2 is False and r2 == "text_digest_mismatch")

def t_verify_unsigned_degrade():
    text = "low-trust task"
    meta = ap.build_metadata("agentX", "", "peer1", "ctx42", text, sign_nokey)
    check("setup: unsigned meta has passport None", meta["passport"] is None)
    verdict, reason, claims = ap.verify(_wrap(meta), text, verify_should_not_run)
    check("verify: unsigned -> rejected (not None)", verdict is False, reason)
    check("verify: reason is unsigned", reason == "unsigned")
    check("verify: claims returned even when unsigned", claims == meta["claims"])
    v2, r2, _ = ap.verify(_wrap(meta), "different", verify_should_not_run)
    check("verify: mismatch beats unsigned ordering", v2 is False and r2 == "text_digest_mismatch")

def t_verify_bad_signature():
    text = "signed task"
    meta = ap.build_metadata("agentX", "alice", "peer1", "ctx42", text, sign_real)
    verdict, reason, claims = ap.verify(_wrap(meta), text, verify_real)
    check("setup: valid before tamper", verdict is True)
    forged = _wrap({"claims": dict(meta["claims"]), "passport": {"sig": "deadbeef", "table": ap.TABLE}})
    v2, r2, _ = ap.verify(forged, text, verify_real)
    check("verify: bad signature rejected", v2 is False, r2)
    check("verify: reason from verify_fn on bad sig", r2 == "bad_signature")

def t_verify_claims_tampered_under_valid_sig():
    """If a claim field is altered after signing, the re-derived signature no
    longer matches -> verify_fn rejects (signature binds the whole claim set)."""
    text = "audit task"
    meta = ap.build_metadata("agentX", "alice", "peer1", "ctx42", text, sign_real)
    bad = dict(meta["claims"])
    bad["principal"] = "mallory"  # privilege escalation attempt, text digest untouched
    forged = _wrap({"claims": bad, "passport": meta["passport"]})
    v, r, _ = ap.verify(forged, text, verify_real)
    check("verify: tampered principal under old sig rejected", v is False, r)
    check("verify: reason bad_signature for claim tamper", r == "bad_signature")

def t_verify_absent_and_malformed():
    text = "x"
    v, r, c = ap.verify({}, text, verify_should_not_run)
    check("verify: absent block -> verdict None", v is None and r == "absent")
    check("verify: absent -> empty claims dict", c == {})
    v2, r2, c2 = ap.verify(None, text, verify_should_not_run)
    check("verify: None metadata -> None/absent", v2 is None and r2 == "absent" and c2 == {})
    v3, r3, _ = ap.verify({ap.METADATA_KEY: "not-a-dict"}, text, verify_should_not_run)
    check("verify: non-dict principal block -> None/absent", v3 is None and r3 == "absent")
    v4, r4, _ = ap.verify("garbage", text, verify_should_not_run)
    check("verify: non-dict metadata -> None/absent", v4 is None and r4 == "absent")
    v5, r5, c5 = ap.verify({ap.METADATA_KEY: {"passport": {"sig": "z"}}}, "real text", verify_should_not_run)
    check("verify: missing claims -> {} and text mismatch (verify_fn unused)",
          v5 is False and r5 == "text_digest_mismatch" and c5 == {})
    v6, r6, c6 = ap.verify({ap.METADATA_KEY: {"claims": ["bad"], "passport": {"sig": "z"}}}, "", verify_always_true)
    check("verify: non-dict claims coerced to {} -> mismatch", v6 is False and r6 == "text_digest_mismatch" and c6 == {})

def t_verify_empty_text_consistency():
    """A claim built over empty text verifies against delivered empty/None text."""
    meta = ap.build_metadata("agentX", "", "peer1", "ctx42", "", sign_real)
    v, r, _ = ap.verify(_wrap(meta), "", verify_real)
    check("verify: empty-text claim accepts empty delivered", v is True, r)
    vn, rn, _ = ap.verify(_wrap(meta), None, verify_real)
    check("verify: empty-text claim accepts None delivered (both -> '' digest)", vn is True, rn)
    vx, rx, _ = ap.verify(_wrap(meta), "nonempty", verify_real)
    check("verify: empty-text claim rejects nonempty delivered", vx is False and rx == "text_digest_mismatch")

def t_constants():
    check("const: TABLE", ap.TABLE == "a2a_delegation")
    check("const: METADATA_KEY", ap.METADATA_KEY == "mios_principal")

def _empty_keydir():
    """A throwaway dir with no agent key material in it (degrade-open paths)."""
    return tempfile.mkdtemp(prefix="mios-pp-")

def t_passport_canonical_json():
    s = ap._passport_canonical_json({"b": 1, "a": 2})
    check("canonical_json: keys sorted + compact", s == '{"a":2,"b":1}', s)
    check("canonical_json: deterministic",
          ap._passport_canonical_json({"x": [3, 2, 1], "y": "z"})
          == ap._passport_canonical_json({"y": "z", "x": [3, 2, 1]}))
    check("canonical_json: non-native value coerced via str (no raise)",
          ap._passport_canonical_json({"a": {1, 2}}).startswith('{"a":'))

def t_passport_op_hash():
    h = ap._passport_op_hash("tbl", {"x": 1})
    check("op_hash: sha256: prefix + 64 hex",
          h.startswith("sha256:") and len(h) == len("sha256:") + 64, h)
    check("op_hash: equals sha256 of 'table:canonical(fields)'",
          h == "sha256:" + hashlib.sha256(b'tbl:{"x":1}').hexdigest())
    check("op_hash: strips the 'passport' field before hashing",
          ap._passport_op_hash("tbl", {"x": 1, "passport": {"sig": "z"}}) == h)
    check("op_hash: table-bound (different table -> different hash)",
          ap._passport_op_hash("other", {"x": 1}) != h)

def t_passport_sign_gated():
    ap.configure(passport_enable=False)
    check("sign: PASSPORT_ENABLE=False -> None", ap._passport_sign("t", {"x": 1}) is None)
    kd = _empty_keydir()
    ap.configure(passport_enable=True, passport_algo="ed25519",
                 passport_key_dir=kd, passport_agent_name="agent-pipe")
    ap._passport_priv = None
    ap._passport_load_attempted = False
    check("sign: enabled but no key -> None", ap._passport_sign("t", {"x": 1}) is None)

def t_passport_kid_default():
    kd = _empty_keydir()
    ap.configure(passport_key_dir=kd, passport_agent_name="agent-pipe")
    check("kid: defaults to '<agent>-v1' when no kid file",
          ap._passport_kid() == "agent-pipe-v1", ap._passport_kid())

def t_passport_load_public_missing():
    kd = _empty_keydir()
    ap.configure(passport_key_dir=kd, passport_agent_name="agent-pipe")
    ap._passport_pub_cache.clear()
    check("load_public: unknown agent -> None", ap._passport_load_public("nobody") is None)

def t_passport_verify_branches():
    check("verify: non-dict envelope", ap._passport_verify("nope") == (False, "envelope_not_dict"))
    check("verify: empty dict -> missing field",
          ap._passport_verify({}) == (False, "envelope_missing_field"))
    full = {"agent": "bob", "ts": "t", "nonce": "n",
            "op_hash": "sha256:dead", "sig": "c2ln", "alg": "rsa"}
    check("verify: unsupported alg", ap._passport_verify(full) == (False, "unsupported_alg:rsa"))
    ed = dict(full, alg="ed25519")
    v, r = ap._passport_verify(ed, payload_for_hash=("tbl", {"x": 1}))
    check("verify: op_hash mismatch (declared != recomputed)",
          v is False and r == "op_hash_mismatch", r)
    kd = _empty_keydir()
    ap.configure(passport_key_dir=kd, passport_agent_name="agent-pipe")
    ap._passport_pub_cache.clear()
    good_hash = ap._passport_op_hash("tbl", {"x": 1})
    ed2 = dict(ed, op_hash=good_hash)
    v2, r2 = ap._passport_verify(ed2, payload_for_hash=("tbl", {"x": 1}))
    check("verify: matching hash but no pubkey -> no_public_key",
          v2 is False and r2 == "no_public_key:bob", r2)

def t_passport_real_roundtrip():
    """Full sign->verify with a real provisioned Ed25519 keypair (skipped cleanly
    if python3-cryptography is unavailable on the build host)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except Exception:  # noqa: BLE001 -- crypto lib optional on the build host
        check("roundtrip: cryptography unavailable -> skipped", True)
        return
    kd = _empty_keydir()
    agent = "testagent"
    os.makedirs(os.path.join(kd, agent), exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    with open(os.path.join(kd, agent, "private.key"), "wb") as fh:
        fh.write(priv.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    with open(os.path.join(kd, agent, "public.key"), "wb") as fh:
        fh.write(priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
    ap.configure(passport_enable=True, passport_algo="ed25519",
                 passport_key_dir=kd, passport_agent_name=agent)
    ap._passport_priv = None
    ap._passport_load_attempted = False
    ap._passport_pub_cache.clear()
    env = ap._passport_sign("tbl", {"x": 1})
    check("roundtrip: sign returns an envelope", isinstance(env, dict) and env.get("sig"))
    check("roundtrip: envelope agent + alg + op_hash bound",
          env.get("agent") == agent and env.get("alg") == "ed25519"
          and env.get("op_hash") == ap._passport_op_hash("tbl", {"x": 1}))
    ok, reason = ap._passport_verify(env, payload_for_hash=("tbl", {"x": 1}))
    check("roundtrip: verify valid signature + matching payload", ok is True and reason == "ok", reason)
    okt, rt = ap._passport_verify(env, payload_for_hash=("tbl", {"x": 2}))
    check("roundtrip: tampered payload -> op_hash_mismatch", okt is False and rt == "op_hash_mismatch", rt)

def main():
    t_text_digest()
    t_build_claims()
    t_build_metadata()
    t_verify_valid_roundtrip()
    t_verify_tampered_text()
    t_verify_unsigned_degrade()
    t_verify_bad_signature()
    t_verify_claims_tampered_under_valid_sig()
    t_verify_absent_and_malformed()
    t_verify_empty_text_consistency()
    t_constants()
    t_passport_canonical_json()
    t_passport_op_hash()
    t_passport_sign_gated()
    t_passport_kid_default()
    t_passport_load_public_missing()
    t_passport_verify_branches()
    t_passport_real_roundtrip()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_a2a_passport.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test for mios_a2a_principal (#60 WS-6 signed A2A delegation principal): claim shape, text-binding digest, and the send->verif...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for mios_a2a_principal (WS-6 signed delegation principal).

Pure stdlib + the sibling module only -- no server.py / Ed25519 keys. The real
crypto is the agent passport's _passport_sign/_passport_verify (covered by the
passport tests + operator on MiOS-DEV); here we inject fakes to prove the
deterministic glue: claim shape, text-binding, and the absent/unsigned/tamper/ok
branches the receive path relies on.

Run:  python test_mios_a2a_passport.py
"""

import sys

import mios_a2a_principal as P

_RESULTS_a2a_passport: list = []

def _check_a2a_passport(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS_a2a_passport.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _fake_sign(table, claims):
    return {"t": table, "h": P.text_digest(repr(sorted(claims.items()))), "sig": "FAKE"}

def _fake_verify(envelope, payload):
    table, claims = payload
    want = P.text_digest(repr(sorted(claims.items())))
    if envelope.get("t") == table and envelope.get("h") == want:
        return True, "ok"
    return False, "invalid_signature"

def _nokey_sign(table, claims):
    return None   # no key provisioned

def t_claims() -> None:
    c = P.build_claims("agent-pipe", "alice", "peerX", "ctx1", "open notepad")
    _check_a2a_passport("claims: agent", c["agent"] == "agent-pipe")
    _check_a2a_passport("claims: principal", c["principal"] == "alice")
    _check_a2a_passport("claims: peer", c["peer"] == "peerX")
    _check_a2a_passport("claims: context", c["context"] == "ctx1")
    _check_a2a_passport("claims: text digest bound",
           c["text_sha256"] == P.text_digest("open notepad"))
    _check_a2a_passport("claims: empty principal -> '' (autonomous)",
           P.build_claims("a", "", "p", "", "x")["principal"] == "")

def _msg(meta):
    return {P.METADATA_KEY: meta}

def t_roundtrip() -> None:
    md = P.build_metadata("agent-pipe", "alice", "peerX", "ctx1", "open notepad", _fake_sign)
    _check_a2a_passport("send: signed (passport present)", isinstance(md.get("passport"), dict))
    v, reason, claims = P.verify(_msg(md), "open notepad", _fake_verify)
    _check_a2a_passport("roundtrip: verdict True", v is True, reason)
    _check_a2a_passport("roundtrip: claims carried", claims.get("principal") == "alice")

def t_tamper() -> None:
    md = P.build_metadata("agent-pipe", "alice", "peerX", "ctx1", "open notepad", _fake_sign)
    v, reason, _ = P.verify(_msg(md), "rm -rf /", _fake_verify)
    _check_a2a_passport("tamper: rejected", v is False)
    _check_a2a_passport("tamper: caught by digest (before sig)", reason == "text_digest_mismatch", reason)
    md["passport"]["sig"] = "FORGED"; md["passport"]["h"] = "wronghash"
    v2, r2, _ = P.verify(_msg(md), "open notepad", _fake_verify)
    _check_a2a_passport("tamper: bad signature rejected", v2 is False, r2)

def t_unsigned() -> None:
    md = P.build_metadata("agent-pipe", "alice", "peerX", "ctx1", "hi", _nokey_sign)
    _check_a2a_passport("unsigned: passport None when no key", md.get("passport") is None)
    v, reason, claims = P.verify(_msg(md), "hi", _fake_verify)
    _check_a2a_passport("unsigned: verdict False", v is False)
    _check_a2a_passport("unsigned: reason 'unsigned'", reason == "unsigned", reason)
    _check_a2a_passport("unsigned: claims still readable", claims.get("agent") == "agent-pipe")

def t_absent() -> None:
    v, reason, claims = P.verify({}, "hi", _fake_verify)
    _check_a2a_passport("absent: verdict None (legacy/non-MiOS peer)", v is None)
    _check_a2a_passport("absent: reason 'absent'", reason == "absent")
    _check_a2a_passport("absent: empty claims", claims == {})
    v2, r2, _ = P.verify(None, "hi", _fake_verify)
    _check_a2a_passport("absent: None metadata tolerated", v2 is None and r2 == "absent")

def _main_a2a_passport() -> int:
    for t in (t_claims, t_roundtrip, t_tamper, t_unsigned, t_absent):
        t()
    passed = sum(1 for _, ok in _RESULTS_a2a_passport if ok)
    total = len(_RESULTS_a2a_passport)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def _run_extra_a2a_passport():
    try:
        return _main_a2a_passport()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



# ==============================================================================
# Consolidated from test_mios_principal.py (task_1006)
# ==============================================================================
# AI-hint: Unit test suite for mios_pipe.identity.principal module (signed A2A delegation principal).
# AI-related: mios_pipe/identity/principal.py
"""Unit tests for mios_pipe.identity.principal."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mios_pipe.identity.principal import text_digest, build_claims, build_metadata, verify, TABLE, METADATA_KEY

class TestPrincipal(unittest.TestCase):
    """Test signed A2A delegation principal claims and digests."""

    def test_text_digest_sha256(self):
        digest = text_digest("run diagnostic task")
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        empty_digest = text_digest("")
        self.assertEqual(len(empty_digest), 64)

    def test_build_claims(self):
        claims = build_claims(
            agent="opencode",
            principal="operator",
            peer_id="node_42",
            context_id="ctx_101",
            text="execute command"
        )
        self.assertEqual(claims["agent"], "opencode")
        self.assertEqual(claims["principal"], "operator")
        self.assertEqual(claims["peer"], "node_42")
        self.assertEqual(claims["context"], "ctx_101")
        self.assertEqual(claims["text_sha256"], text_digest("execute command"))

    def test_build_metadata_unsigned(self):
        mock_sign = lambda table, claims: None
        meta = build_metadata(
            agent="opencode",
            principal="operator",
            peer_id="node_42",
            context_id="ctx_101",
            text="execute command",
            sign_fn=mock_sign,
        )
        self.assertEqual(meta["claims"]["agent"], "opencode")
        self.assertIsNone(meta["passport"])

    def test_verify_absent_metadata(self):
        verdict, reason, claims = verify(None, "text", verify_fn=lambda p, c: (True, "ok"))
        self.assertIsNone(verdict)
        self.assertEqual(reason, "absent")


def _run_extra_principal():
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestPrincipal))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_a2a_principal_suites():
    rc = _run_extra_a2a_passport()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_principal()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_a2a_principal_suites()
    sys.exit(main())
