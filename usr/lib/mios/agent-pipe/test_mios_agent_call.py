#!/usr/bin/env python3
# AI-hint: Stdlib assert-script for mios_agent_call. Stubs every injected dep (no
# AI-related: ./mios_agent_call.py
# AI-functions: (test)
"""Offline regression for mios_agent_call -- body assembly + response normalisation."""

import asyncio
import contextvars
import json

import mios_agent_call as T

class _ACM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

def _acm(*_a, **_k):
    return _ACM()

async def _anoop(*_a, **_k):
    return None

class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

class _FakeClient:
    """Records the last POST + replays a scripted response."""

    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        self.calls = []

    async def post(self, url, *, content=None, headers=None, timeout=None):
        body = json.loads(content.decode("utf-8")) if content else {}
        self.calls.append({"url": url, "body": body, "headers": headers})
        return _Resp(self.status, self.payload)

class _StreamResp:
    """Async-CM SSE response: replays scripted `data:` lines via aiter_lines()."""

    def __init__(self, status, lines):
        self.status_code = status
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

class _FakeStreamClient:
    """Records the last client.stream() + replays a scripted SSE line list."""

    def __init__(self, status, lines):
        self.status = status
        self.lines = lines
        self.calls = []

    def stream(self, method, url, *, content=None, headers=None, timeout=None):
        body = json.loads(content.decode("utf-8")) if content else {}
        self.calls.append({"method": method, "url": url, "body": body,
                           "headers": headers})
        return _StreamResp(self.status, self.lines)

_conv = contextvars.ContextVar("conv", default="")
_dispatch = contextvars.ContextVar("dispatch", default="")
_kvparent = contextvars.ContextVar("kvparent", default="")

def _configure(client_unused=None):
    T.configure(
        healthgate_connect_timeout=6.0,
        healthgate_read_timeout=120.0,
        secondary_tool_loop=False,   # skip the pipe-side tool-loop in this test
        kv_fork_enable=False,
        src_turn_header="X-MiOS-Turn",
        agent_registry={},
        sloshed=type("_SloShed", (Exception,), {}),
        admit=_anoop,
        agent_binding=lambda cfg, eng: (cfg.get("endpoint"), cfg.get("model")),
        agent_offload_engine=lambda cfg: None,
        apply_outbound_auth=lambda hdrs, ep: None,
        conv_key_var=_conv,
        current_trace_id=lambda: "",
        dispatch_agent_var=_dispatch,
        dispatch_priority=lambda cfg: 0.0,
        endpoint_sem=_acm,
        harvest_sub_sources=lambda rj, content: None,
        hop_via_headers=lambda: {},
        kv_fork_parent_var=_kvparent,
        lane_sem=_acm,
        lane_sem_key=lambda cfg: "cpu",
        model_active=_anoop,
        opt_int_mb=lambda v: 0,
        priority_gate=_acm,
        is_slow_lane_ep=lambda ep: True,
        llm_num_predict_cap=2048,
        llm_num_predict_cap_cpu=512,
        node_live={},
        should_health_probe=lambda cfg: False,
        src_turn_key=lambda: "",
        strip_agent_chrome=lambda t: t.replace("> build · qwen\n", "").strip(),
        strip_think_tags=lambda t: t.replace("<think>x</think>", ""),
        v1_secondary_tool_loop=_anoop,
    )

_configure()

PASS = 0

def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1
    print("  ok:", label)

CFG = {"endpoint": "http://gw:8642/v1", "model": "mios-secondary"}
BODY = {
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [{"type": "function", "function": {"name": "web_search"}}],
    "tool_choice": "auto",
}

print("[request-body assembly + normalisation]")

async def _happy():
    client = _FakeClient(200, {"choices": [{"message": {
        "content": "> build · qwen\n<think>x</think>the real answer"}}]})
    name, text = await T._call_agent_complete(
        "secondary", CFG, dict(BODY), {}, client, prefer_cpu=True)
    ok(len(client.calls) == 1, "exactly one /v1 POST issued")
    call = client.calls[0]
    ok(call["url"] == "http://gw:8642/v1/chat/completions",
       "POST targets the agent's bound /v1 endpoint")
    b = call["body"]
    ok(b["model"] == "mios-secondary", "body carries the agent's model")
    ok(b["messages"] == BODY["messages"], "body preserves the messages verbatim")
    ok(b["tools"] == BODY["tools"], "body preserves the offered tools")
    ok(b["tool_choice"] == "auto", "body preserves tool_choice")
    ok(b["stream"] is False, "stream forced False (non-streaming dispatch)")
    ok(b["max_tokens"] == 512, "max_tokens defaulted from _num_predict_cap_for")
    ok(b.get("chat_template_kwargs") == {"enable_thinking": False},
       "thinking channel disabled so /v1 renders content not reasoning")
    ok("options" not in b and "think" not in b,
       "legacy options/think stripped from the /v1 body")
    ok(name == "secondary", "returns the dispatched agent name")
    ok(text == "the real answer",
       "response normalised: agent-chrome + think tags stripped")

asyncio.run(_happy())

print("[error path]")

async def _err():
    client = _FakeClient(500, {"error": "boom"})
    name, text = await T._call_agent_complete(
        "secondary", CFG, dict(BODY), {}, client, prefer_cpu=True)
    ok(name == "secondary" and text == "",
       "non-200 with no failover_agents -> ('', dropped from the merge)")

asyncio.run(_err())

print("[no-endpoint guard]")

async def _noep():
    client = _FakeClient(200, {"choices": []})
    name, text = await T._call_agent_complete(
        "secondary", {"endpoint": "", "model": "m"}, dict(BODY), {}, client)
    ok(text == "", "absent endpoint with no failover -> '' (skipped)")
    ok(len(client.calls) == 0, "no POST issued when the endpoint is empty")

asyncio.run(_noep())

print("[streaming: live fragment broadcast + merge text]")

async def _stream_happy():
    lines = [
        "",                                                  # blank -> skipped
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":" pondering"}}]}',
        'data: {"choices":[{"delta":{"content":" <think>x</think>world"}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":" AFTER-DONE"}}]}',  # past [DONE]
    ]
    client = _FakeStreamClient(200, lines)
    q = asyncio.Queue()
    name, text = await T._call_agent_stream_inner(
        "secondary", CFG, dict(BODY), {}, client, q, prefer_cpu=True)
    ok(len(client.calls) == 1, "exactly one client.stream() issued")
    call = client.calls[0]
    ok(call["url"] == "http://gw:8642/v1/chat/completions",
       "stream targets the agent's bound /v1 endpoint")
    b = call["body"]
    ok(b["stream"] is True, "stream forced True (streaming dispatch)")
    ok(b["model"] == "mios-secondary", "body carries the agent's model")
    ok(b["max_tokens"] == 512, "max_tokens defaulted from _num_predict_cap_for")
    ok(b.get("chat_template_kwargs") == {"enable_thinking": False},
       "thinking channel disabled so /v1 renders content not reasoning")
    ok("options" not in b and "think" not in b,
       "legacy options/think stripped from the /v1 body")
    frags = []
    while not q.empty():
        frags.append(q.get_nowait())
    ok(all(ev[0] == "SF" and ev[1] == "secondary" for ev in frags),
       "every queued event tagged ('SF', agent_name, fragment)")
    pushed = [ev[2] for ev in frags]
    ok(pushed == ["Hello", " pondering", " <think>x</think>world"],
       "both answer AND reasoning fragments broadcast live, in arrival order")
    ok(name == "secondary", "returns the dispatched agent name")
    ok(text == "Hello world",
       "merge text = answer content only (reasoning excluded, <think> stripped, "
       "stops at [DONE])")

asyncio.run(_stream_happy())

print("[streaming error path]")

async def _stream_err():
    client = _FakeStreamClient(500, ['data: {"choices":[{"delta":{"content":"x"}}]}'])
    q = asyncio.Queue()
    name, text = await T._call_agent_stream_inner(
        "secondary", CFG, dict(BODY), {}, client, q, prefer_cpu=True)
    ok(name == "secondary" and text == "",
       "non-200 stream -> ('', dropped from the merge)")
    ok(q.empty(), "no fragments broadcast on a non-200 stream")

asyncio.run(_stream_err())

print("[streaming no-endpoint guard]")

async def _stream_noep():
    client = _FakeStreamClient(200, [])
    q = asyncio.Queue()
    name, text = await T._call_agent_stream_inner(
        "secondary", {"endpoint": "", "model": "m"}, dict(BODY), {}, client, q)
    ok(text == "", "absent endpoint -> '' (skipped, streaming)")
    ok(len(client.calls) == 0, "no stream issued when the endpoint is empty")

asyncio.run(_stream_noep())

print("[moved cluster: pure + inert paths]")

ok(T._kv_base("http://h:8080/v1") == "http://h:8080", "_kv_base strips a trailing /v1")
ok(T._kv_base("http://h:8080/") == "http://h:8080", "_kv_base strips a trailing slash")
ok(T._kv_base("http://h:8080") == "http://h:8080", "_kv_base leaves a bare root")

_fn = T._kv_filename("conv/../x y")
ok(isinstance(_fn, str) and "/" not in _fn and "\\" not in _fn,
   "_kv_filename is filesystem-safe")
ok(T._kv_filename("conv-abc") == T._kv_filename("conv-abc"),
   "_kv_filename is deterministic for one conversation")

ok(T._rr_eligible({"messages": [{"role": "user", "content": "hi"}]},
                  "http://h/v1", {}, None) is False,
   "_rr_eligible False while RR_ENABLE is off")

async def _kv_paging_inert():
    client = _FakeClient(200, {})
    async with T._kv_paging(client, "http://h/v1", {"model": "m"}, None):
        pass
    ok(len(client.calls) == 0, "_kv_paging is a no-op yield when disabled")

asyncio.run(_kv_paging_inert())

async def _kv_fork_disabled():
    res = await T._kv_fork(_FakeClient(200, {}), "http://h/v1", {}, None,
                           "parent", "child")
    ok(res.get("forked") is False, "_kv_fork inert (disabled) -> forked False")

asyncio.run(_kv_fork_disabled())

print("[moved cluster: active KV-paging + RR decode]")

class _SlotResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http %d" % self.status_code)

    def json(self):
        return self._payload

class _SlotClient:
    """Fake llama.cpp client for /slots + /chat/completions (records calls)."""

    def __init__(self, status=200):
        self.status = status
        self.calls = []

    async def post(self, url, *, params=None, content=None, headers=None,
                   timeout=None):
        self.calls.append({
            "url": url, "params": params,
            "body": json.loads(content.decode("utf-8")) if content else {}})
        return _SlotResp(self.status, {"choices": [{
            "message": {"content": "sliced answer"},
            "finish_reason": "stop"}]})

class _FakeGate:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    async def acquire(self, prio):
        self.acquired += 1

    def release(self):
        self.released += 1

    def head_priority(self):
        return None

_gate = _FakeGate()
T.configure(kv_paging_enable=True, kv_paging_slot=0, kv_paging_timeout=12.0,
            rr_enable=True, priority_queue_enable=True,
            kv_locks={}, kv_resident={}, backend_key="",
            global_priority_gate=_gate)
T._endpoint_is_llamacpp = lambda ep, cfg, eng=None: True

ok(T._rr_eligible({"messages": [{"role": "user", "content": "hi"}]},
                  "http://h/v1", {}, None) is True,
   "_rr_eligible True for a plain completion on a /slots lane")
ok(T._rr_eligible({"messages": [{"role": "user", "content": "hi"}],
                   "tools": [{"type": "function"}]},
                  "http://h/v1", {}, None) is False,
   "_rr_eligible False when the dispatch carries tools[]")

async def _slot_action_status():
    ok(await T._kv_slot_action(_SlotClient(200), "http://h/v1", "save",
                               "conv1", "m") is True,
       "_kv_slot_action True on a 200 slot save")
    ok(await T._kv_slot_action(_SlotClient(404), "http://h/v1", "restore",
                               "conv1", "m") is False,
       "_kv_slot_action False when every slot URL 404s")

asyncio.run(_slot_action_status())

async def _kv_paging_active():
    T._KV_RESIDENT.clear()
    T._SAVED_CONVS.clear()
    T._SAVED_CONVS.add("convA")
    _conv.set("convA")
    client = _SlotClient(200)
    async with T._kv_paging(client, "http://h/v1", {"model": "m"}, None):
        pass
    ok(any(c["params"] == {"action": "restore"} for c in client.calls),
       "_kv_paging pages the conversation IN on a cold slot when snapshot exists")
    key = T._kv_base("http://h/v1") + "#0"
    ok(T._KV_RESIDENT.get(key) == "convA",
       "_kv_paging records the resident conversation for the slot")

asyncio.run(_kv_paging_active())

async def _rr_run_single_slice():
    client = _SlotClient(200)
    text = await T._rr_run(client, "http://h/v1", "m",
                           [{"role": "user", "content": "hi"}],
                           conv="convA", priority=5.0, max_tokens=10)
    ok(text == "sliced answer", "_rr_run returns the assembled slice text")
    ok(_gate.acquired >= 1 and _gate.released == _gate.acquired,
       "_rr_run balances the priority gate (acquire == release)")

asyncio.run(_rr_run_single_slice())

print("[task_3483: Lock order Gate -> Endpoint -> Lane across mixed RR/non-RR]")

async def _test_t1190_mixed_rr_non_rr_deadlock_free():
    import mios_preempt
    from mios_pipe.scheduler.sched import PriorityGate
    gate = PriorityGate(permits=1)
    ep_sem = asyncio.Semaphore(1)
    lane_sem = asyncio.Semaphore(1)

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _priority_gate_cm(prio):
        await gate.acquire(prio)
        try:
            yield
        finally:
            gate.release()

    @asynccontextmanager
    async def _ep_sem_cm(ep):
        async with ep_sem:
            yield

    @asynccontextmanager
    async def _l_sem_cm(key):
        async with lane_sem:
            yield

    async def _anoop_admit(*a, **k):
        return None

    _conv_var = contextvars.ContextVar("conv_t1190", default="")
    _disp_var = contextvars.ContextVar("disp_t1190", default="")

    T.configure(
        healthgate_connect_timeout=6.0,
        healthgate_read_timeout=120.0,
        secondary_tool_loop=False,
        kv_fork_enable=False,
        src_turn_header="X-MiOS-Turn",
        agent_registry={},
        sloshed=type("_SloShed", (Exception,), {}),
        admit=_anoop_admit,
        agent_binding=lambda cfg, eng: ("http://fake-ep/v1", "m"),
        agent_offload_engine=lambda cfg: None,
        apply_outbound_auth=lambda hdrs, ep: None,
        conv_key_var=_conv_var,
        current_trace_id=lambda: "",
        dispatch_agent_var=_disp_var,
        dispatch_priority=lambda cfg: 5.0,
        endpoint_sem=lambda ep: _ep_sem_cm(ep),
        harvest_sub_sources=lambda rj, content: None,
        hop_via_headers=lambda: {},
        kv_fork_parent_var=contextvars.ContextVar("kvp_t1190", default=""),
        lane_sem=lambda key: _l_sem_cm(key),
        lane_sem_key=lambda cfg: "cpu",
        model_active=_anoop_admit,
        opt_int_mb=lambda v: 0,
        priority_gate=_priority_gate_cm,
        is_slow_lane_ep=lambda ep: False,
        llm_num_predict_cap=512,
        llm_num_predict_cap_cpu=512,
        node_live={},
        should_health_probe=lambda cfg: False,
        src_turn_key=lambda: "",
        strip_agent_chrome=lambda t: t,
        strip_think_tags=lambda t: t,
        v1_secondary_tool_loop=lambda *a, **k: None,
        rr_enable=True,
        priority_queue_enable=True,
        global_priority_gate=gate,
        preempt=mios_preempt.PreemptScheduler(max_suspended=10),
    )
    T._endpoint_is_llamacpp = lambda ep, cfg, eng=None: True

    old_slice_tokens = T.RR_SLICE_TOKENS
    old_quantum = T.RR_QUANTUM_S
    T.RR_SLICE_TOKENS = 5
    T.RR_QUANTUM_S = 0.001

    class _T1190Client:
        async def post(self, url, *, content=None, headers=None, timeout=None):
            await asyncio.sleep(0.02)
            b = json.loads(content.decode("utf-8")) if content else {}
            is_last = b.get("max_tokens", 5) > 10
            return type("_Resp", (), {
                "status_code": 200,
                "text": '{"choices": [{"message": {"content": "ok"}}]}',
                "json": lambda self: {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop" if is_last else "length"}]},
                "raise_for_status": lambda self: None,
            })()

    client = _T1190Client()
    cfg_low = {"endpoint": "http://fake-ep/v1", "model": "m"}
    cfg_high = {"endpoint": "http://fake-ep/v1", "model": "m"}

    rr_body = {"messages": [{"role": "user", "content": "rr request"}], "max_tokens": 20}
    non_rr_body = {"messages": [{"role": "user", "content": "non-rr request"}], "tools": [{"type": "function"}]}

    async def _dispatch_low():
        return await T._call_agent_complete("agent_low", cfg_low, rr_body.copy(), {}, client, prefer_cpu=True, priority=1.0)

    async def _dispatch_high():
        await asyncio.sleep(0.005)
        return await T._call_agent_complete("agent_high", cfg_high, non_rr_body.copy(), {}, client, prefer_cpu=True, priority=9.0)

    try:
        t1 = asyncio.create_task(_dispatch_low())
        t2 = asyncio.create_task(_dispatch_high())
        done, pending = await asyncio.wait([t1, t2], timeout=2.0)
        ok(len(pending) == 0, "task_3483: mixed RR and non-RR dispatches against 1-permit endpoint complete without deadlock")
        ok(len(done) == 2, "task_3483: both dispatches completed successfully")
    finally:
        T.RR_SLICE_TOKENS = old_slice_tokens
        T.RR_QUANTUM_S = old_quantum
        _configure()

asyncio.run(_test_t1190_mixed_rr_non_rr_deadlock_free())

print("[_record_cost cost accounting]")
import time as _time
import mios_cost
import mios_tokenize

_ledger = mios_cost.CostLedger()
_model = mios_cost.CostModel(gpu_watts=300.0, usd_per_kwh=0.0)
T.configure(
    cost_accounting_enable=True,
    cost_ledger=_ledger,
    cost_model=_model,
    is_remote_endpoint=lambda ep: False,
    lane_sem_key=lambda cfg: "synthlane",
)
_cbody = {"messages": [{"role": "user", "content": "x" * 40}]}   # 40 synthetic chars
_ctext = "y" * 20                                                # 20 synthetic chars
_expect_tokens = (mios_tokenize.count_messages(_cbody["messages"])
                  + mios_tokenize.count_text(_ctext))
T._record_cost({"endpoint": "ep"}, "http://ep/v1", _time.time() - 1.0, _cbody, _ctext)
_snap = _ledger.snapshot()
ok(_snap["dispatches"] == 1, "_record_cost records exactly one dispatch")
ok(_snap["tokens"] == _expect_tokens,
   "_record_cost token total comes from the mios_tokenize seam (not inline //4)")
ok(_snap["by_lane"].get("synthlane", {}).get("n") == 1,
   "_record_cost attributes the dispatch to the injected lane key")

T.configure(cost_accounting_enable=False)
T._record_cost({"endpoint": "ep"}, "http://ep/v1", _time.time(), _cbody, _ctext)
ok(_ledger.snapshot()["dispatches"] == 1,
   "_record_cost is a no-op when cost_accounting_enable is off")

print("[task_21: KV Slot-Save/Restore + --swa-full Guard]")
ok(T._stable_hash("conv1") == T._stable_hash("conv1"), "_stable_hash is deterministic")
ok(T._stable_hash("conv1") != T._stable_hash("conv2"), "_stable_hash produces different values for different strings")

ok(T._is_gemma_or_qwen("gemma:7b") is True, "gemma model matches directly")
ok(T._is_gemma_or_qwen("qwen2.5:7b") is True, "qwen model matches directly")
ok(T._is_gemma_or_qwen("granite4.1:8b") is False, "granite model does not match directly")

class _SwaClient:
    def __init__(self):
        self.calls = []
    async def post(self, url, params=None, content=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "content": json.loads(content.decode("utf-8") if content else "{}")})
        class R:
            status_code = 200
        return R()

client_swa = _SwaClient()
async def test_swa_slot_action():
    await T._kv_slot_action(client_swa, "http://h/v1", "restore", "convA", "granite4.1:8b")
    await T._kv_slot_action(client_swa, "http://h/v1", "restore", "convB", "gemma:2b")

    ok("swa_full" not in (client_swa.calls[0]["params"] or {}), "non-SWA model restore has no swa_full query param")
    ok(client_swa.calls[1]["params"].get("swa_full") == "true", "SWA model restore has swa_full query param")
    ok(client_swa.calls[1]["content"].get("swa_full") is True, "SWA model restore has swa_full body param")

asyncio.run(test_swa_slot_action())

print("\nALL %d ASSERTIONS PASSED" % PASS)


# ==============================================================================
# Consolidated from test_mios_agentcard_sign.py (task_1006)
# ==============================================================================
# AI-hint: Unit test suite for mios_pipe.federation.agentcard_sign module.
# AI-related: mios_pipe/federation/agentcard_sign.py
"""Unit tests for mios_pipe.federation.agentcard_sign."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mios_pipe.federation.agentcard_sign import (
    _b64u,
    _b64u_decode,
    _jcs_canonicalize,
    _agent_card_signing_input,
    _agent_card_signature,
    _verify_agent_card_signature,
)

class TestAgentCardSign(unittest.TestCase):
    """Test JCS canonicalization and JWS agentcard signature generation/verification."""

    def test_b64u_roundtrip(self):
        data = b"hello world 123 !@#"
        encoded = _b64u(data)
        self.assertNotIn("=", encoded)
        decoded = _b64u_decode(encoded)
        self.assertEqual(decoded, data)

    def test_jcs_canonicalize(self):
        card = {"name": "MiOS Agent", "version": "1.0", "active": True, "count": 5}
        canonical = _jcs_canonicalize(card)
        self.assertIsInstance(canonical, bytes)
        self.assertIn(b'"active":true', canonical)
        self.assertIn(b'"name":"MiOS Agent"', canonical)

    def test_card_sign_and_verify_roundtrip(self):
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            priv = ed25519.Ed25519PrivateKey.generate()
            pub = priv.public_key()
        except ImportError:
            self.skipTest("cryptography library not installed")

        card = {
            "name": "TestAgent",
            "url": "http://localhost:8640",
            "skills": [{"id": "code", "description": "coding assistant"}],
        }

        mock_load_priv = lambda: priv
        mock_kid = lambda: "test_kid_01"

        sig_dict = _agent_card_signature(card, load_priv_fn=mock_load_priv, kid_fn=mock_kid)
        self.assertIsInstance(sig_dict, dict)
        self.assertIn("protected", sig_dict)
        self.assertIn("signature", sig_dict)

        signed_card = dict(card)
        signed_card["signatures"] = [sig_dict]

        verdict, reason = _verify_agent_card_signature(signed_card, public_key=pub)
        self.assertTrue(verdict)
        self.assertEqual(reason, "ok")

        tampered_card = dict(signed_card)
        tampered_card["name"] = "TamperedAgent"
        tampered_verdict, tampered_reason = _verify_agent_card_signature(tampered_card, public_key=pub)
        self.assertFalse(tampered_verdict)
        self.assertEqual(tampered_reason, "invalid_signature")

    def test_verify_unsigned_card(self):
        card = {"name": "UnsignedAgent"}
        verdict, reason = _verify_agent_card_signature(card)
        self.assertIsNone(verdict)
        self.assertEqual(reason, "unsigned")


def _run_extra_agentcard_sign():
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentCardSign))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_agent_call_suites():
    rc = _run_extra_agentcard_sign()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

_run_all_folded_agent_call_suites()
