#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_pdp (WS-A9 PDP capability gate).
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_pdp (WS-A9)."""

import sys

import mios_pdp as pdp

TIERS = ["read", "write", "interactive"]
_fails = 0

def check(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    tag = "PASS" if cond else "FAIL"
    if not cond:
        _fails += 1
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))

def t_rank():
    check("rank: read=0", pdp.permission_rank("read", TIERS) == 0)
    check("rank: write=1", pdp.permission_rank("write", TIERS) == 1)
    check("rank: interactive=2", pdp.permission_rank("interactive", TIERS) == 2)
    check("rank: unknown tier ranks ABOVE top (fail-closed)",
          pdp.permission_rank("nuke", TIERS) == 3)
    check("rank: case/space-insensitive", pdp.permission_rank("  WRITE ", TIERS) == 1)

def t_ceiling():
    check("ceiling: empty -> None (no ceiling)", pdp.resolve_ceiling("", TIERS) is None)
    check("ceiling: absent -> None", pdp.resolve_ceiling(None, TIERS) is None)
    check("ceiling: known tier -> its rank", pdp.resolve_ceiling("write", TIERS) == 1)
    check("ceiling: UNKNOWN tier -> 0 (FAIL CLOSED, not None)",
          pdp.resolve_ceiling("supervisor", TIERS) == 0)
    check("ceiling: typo'd tier -> 0 (fail closed)", pdp.resolve_ceiling("writ", TIERS) == 0)

def _d(name, *, in_catalog=True, verb_perm="read", denied=(), allowed=(), ceiling=None):
    return pdp.decide(name, in_catalog=in_catalog, verb_perm=verb_perm,
                      denied=denied, allowed=allowed,
                      ceiling_rank=ceiling, tiers=TIERS)

def t_decide():
    check("decide: empty policy allows", _d("open_app").allow)
    check("decide: denied verb -> deny", not _d("open_app", denied=["open_app"]).allow)
    check("decide: denied rule named", _d("x", denied=["x"]).rule == "denied_verbs")
    check("decide: allowed-list excludes others",
          not _d("open_app", allowed=["web_search"]).allow)
    check("decide: allowed-list includes self",
          _d("web_search", allowed=["web_search"]).allow)
    ceil_read = pdp.resolve_ceiling("read", TIERS)
    check("decide: ceiling read drops write verb",
          not _d("pc_type", verb_perm="write", ceiling=ceil_read).allow)
    check("decide: ceiling read keeps read verb",
          _d("list_windows", verb_perm="read", ceiling=ceil_read).allow)
    check("decide: ceiling rule named",
          _d("pc_type", verb_perm="write", ceiling=ceil_read).rule == "max_permission")
    bad_ceil = pdp.resolve_ceiling("typo", TIERS)
    check("decide: fail-closed ceiling drops write verb",
          not _d("pc_type", verb_perm="write", ceiling=bad_ceil).allow)
    check("decide: fail-closed ceiling keeps read verb",
          _d("list_windows", verb_perm="read", ceiling=bad_ceil).allow)

def t_non_verb():
    check("non-verb: passes allowed-list (not a verb)",
          _d("some_mcp_tool", in_catalog=False, allowed=["web_search"]).allow)
    check("non-verb: passes ceiling (not a verb)",
          _d("some_skill", in_catalog=False, verb_perm="write",
             ceiling=pdp.resolve_ceiling("read", TIERS)).allow)
    check("non-verb: denied STILL applies",
          not _d("evil_tool", in_catalog=False, denied=["evil_tool"]).allow)
    check("non-verb: rule = non_verb when allowed",
          _d("t", in_catalog=False).rule == "non_verb")

def main() -> int:
    t_rank()
    t_ceiling()
    t_decide()
    t_non_verb()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# mios_pipe.auth middlewares; task_1006 had folded only a placeholder here.
# ==============================================================================
import asyncio
import json
import types

from mios_pipe import auth as _auth


def _request(path, authorization=None):
    headers = {} if authorization is None else {"authorization": authorization}
    return types.SimpleNamespace(url=types.SimpleNamespace(path=path), headers=headers,
                                 state=types.SimpleNamespace())


class _Upstream:
    """call_next: counts how often the route ran and answers with `body`."""

    def __init__(self, body=b"{}", content_type="application/json", status=200):
        self.ran = 0
        self.body, self.content_type, self.status = body, content_type, status

    async def __call__(self, request):
        self.ran += 1

        async def chunks():
            yield self.body[:5]
            yield self.body[5:]
        return types.SimpleNamespace(
            headers={"content-type": self.content_type, "content-length": str(len(self.body))},
            body_iterator=chunks(), status_code=self.status)


def _gate(**overrides):
    policy = dict(api_require_auth=True, auth_open_paths=("/v1/health",),
                  auth_gated_prefixes=("/v1/", "/a2a"),
                  check_inbound_principal=lambda tok: {"principal": "op"} if tok == "good" else None)
    policy.update(overrides)
    _auth.configure(**policy)


def t_auth_gate():
    _gate(api_require_auth=False)
    up = _Upstream()
    asyncio.run(_auth.inbound_auth_mw(_request("/v1/chat/completions"), up))
    check("auth: gate off -> the route runs without a credential", up.ran == 1)

    _gate()
    for path in ("/v1/health", "/portal"):
        up = _Upstream()
        asyncio.run(_auth.inbound_auth_mw(_request(path), up))
        check(f"auth: {path} is outside the gate", up.ran == 1)

    up = _Upstream()
    resp = asyncio.run(_auth.inbound_auth_mw(_request("/v1/models"), up))
    check("auth: gated path without a credential -> 401", resp.status_code == 401, str(resp.status_code))
    check("auth: the 401 carries the OpenAI error envelope",
          json.loads(resp.body)["error"]["code"] == "unauthorized", resp.body.decode())
    check("auth: the route never ran", up.ran == 0)

    up = _Upstream()
    req = _request("/a2a/tasks", authorization="Bearer good")
    asyncio.run(_auth.inbound_auth_mw(req, up))
    check("auth: Bearer prefix stripped, a valid credential is admitted", up.ran == 1)
    check("auth: the principal is bound to request.state",
          getattr(req.state, "mios_principal", None) == {"principal": "op"})

    up = _Upstream()
    resp = asyncio.run(_auth.inbound_auth_mw(_request("/v1/models", "Bearer bad"), up))
    check("auth: an unknown credential -> 401", resp.status_code == 401 and up.ran == 0)

    def unreadable(_tok):
        raise RuntimeError("caller-key store unreadable")
    _gate(check_inbound_principal=unreadable)
    up = _Upstream()
    resp = asyncio.run(_auth.inbound_auth_mw(_request("/v1/models", "Bearer good"), up))
    check("auth: a failing principal check fails CLOSED", resp.status_code == 401 and up.ran == 0)


def t_usage_completeness():
    class _Mode:
        def get(self):
            return "council"
    _auth.configure(loads_lenient=json.loads, council_mode_var=_Mode(),
                    usage_estimate=lambda prompt, answer: {"prompt_tokens": 0,
                                                           "completion_tokens": len(answer.split())})
    chat = "/v1/chat/completions"

    bare = {"object": "chat.completion", "choices": [{"message": {"content": "two words"}}]}
    resp = asyncio.run(_auth.usage_completeness_mw(_request(chat), _Upstream(json.dumps(bare).encode())))
    data = json.loads(resp.body)
    usage = data.get("usage") or {}
    check("usage: a completion without usage gets an estimated one",
          usage.get("completion_tokens") == 2 and usage.get("total_tokens") == 2, str(usage))
    check("usage: the added usage is normalized",
          usage.get("prompt_tokens_details") == {"cached_tokens": 0}
          and usage.get("completion_tokens_details") == {"reasoning_tokens": 0}, str(usage))
    check("usage: mios_mode is stamped from the council context", data.get("mios_mode") == "council")
    check("usage: content-length matches the rewritten body",
          int(resp.headers["content-length"]) == len(resp.body), str(dict(resp.headers)))

    complete = {"object": "chat.completion", "mios_mode": "solo",
                "choices": [{"message": {"content": "x"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                          "prompt_tokens_details": {"cached_tokens": 0},
                          "completion_tokens_details": {"reasoning_tokens": 0}}}
    raw = json.dumps(complete).encode()
    resp = asyncio.run(_auth.usage_completeness_mw(_request(chat), _Upstream(raw)))
    check("usage: an already-complete response passes byte-identical", resp.body == raw)

    err = json.dumps({"error": {"message": "backend down"}}).encode()
    resp = asyncio.run(_auth.usage_completeness_mw(_request(chat), _Upstream(err, status=502)))
    check("usage: an error body is not rewritten and keeps its status",
          resp.body == err and resp.status_code == 502, str(resp.status_code))

    other = asyncio.run(_auth.usage_completeness_mw(_request("/v1/models"), _Upstream()))
    check("usage: other routes come back untouched", isinstance(other, types.SimpleNamespace))
    sse = asyncio.run(_auth.usage_completeness_mw(
        _request(chat), _Upstream(b"data: {}\n\n", content_type="text/event-stream")))
    check("usage: a streamed (SSE) completion comes back untouched", isinstance(sse, types.SimpleNamespace))


def _run_extra_auth():
    before = _fails
    t_auth_gate()
    t_usage_completeness()
    return 1 if _fails > before else 0



# ==============================================================================
# Consolidated from test_mios_authn.py (task_1006)
# ==============================================================================
# AI-hint: Unit tests for mios_pipe.access.authn.
"""Unit tests for authentication and caller key management."""

import json
import os
import tempfile
import unittest

from mios_pipe.access.authn import (
    _bind_host,
    _check_inbound_principal,
    _load_backend_key,
    _probe_auth_headers,
    configure as configure_authn,
)

class TestAuthn(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.caller_keys_file = os.path.join(self.tmp_dir.name, "caller-keys.json")

        keys_data = {
            "keys": {
                "secret_token_123": {"principal": "worker_agent", "scope": "read"},
            }
        }
        with open(self.caller_keys_file, "w", encoding="utf-8") as fh:
            json.dump(keys_data, fh)

        configure_authn(
            backend_key="backend_secret_xyz",
            ingress_key="ingress_secret_999",
            api_require_auth=True,
            caller_keys_path=self.caller_keys_file,
            auth_hostports={"127.0.0.1:8000"},
            agent_auth_by_hostport={"remote:8000": "Bearer remote_tok"},
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_check_inbound_principal_shared_and_caller(self):
        op = _check_inbound_principal("backend_secret_xyz")
        self.assertIsNotNone(op)
        self.assertEqual(op["principal"], "operator")

        ck = _check_inbound_principal("secret_token_123")
        self.assertIsNotNone(ck)
        self.assertEqual(ck["principal"], "worker_agent")

        un = _check_inbound_principal("invalid_token_999")
        self.assertIsNone(un)

    def test_probe_auth_headers(self):
        hdrs = _probe_auth_headers("http://127.0.0.1:8000/v1/models")
        self.assertEqual(hdrs.get("Authorization"), "Bearer backend_secret_xyz")

    def test_bind_host(self):
        self.assertEqual(_bind_host(require_auth=False), "127.0.0.1")
        self.assertEqual(_bind_host(require_auth=True), "0.0.0.0")
        self.assertEqual(_bind_host(require_auth=False, override="10.0.0.5"), "10.0.0.5")


def _run_extra_authn():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestAuthn))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_pdp_suites():
    rc = _run_extra_auth()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")
    rc = _run_extra_authn()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_pdp_suites()
    sys.exit(_rc_main)
