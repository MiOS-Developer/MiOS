#!/usr/bin/env python3
# AI-hint: Executes production HTTP handlers with failing dependencies to verify public redaction and preserved success/error envelopes.
# AI-related: usr/lib/mios/agent-pipe/server.py, usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py, usr/lib/mios/gateway-agent/server.py, usr/lib/mios/crawl4ai/mios-crawl4ai-service.py
"""No service startup, browser, model, database server or network is invoked."""
import ast
import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
MARKER = "internal-private-path-and-credential-marker"
PIPE = "usr/lib/mios/agent-pipe/server.py"
PORTAL = "usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py"
GATEWAY = "usr/lib/mios/gateway-agent/server.py"
CRAWL = "usr/lib/mios/crawl4ai/mios-crawl4ai-service.py"
CONFIG = "usr/lib/mios/agent-pipe/mios_pipe/kernel/config.py"


def response(content=None, status_code=200, **kwargs):
    return SimpleNamespace(content=content, status_code=status_code)


def handlers(path, names, **injected):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(definitions) == len(names), (path, names)
    for node in definitions:
        node.decorator_list = []
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    scope = dict(JSONResponse=response, Response=response, log=Mock(), os=os, sys=sys, uuid=uuid, json=json, **injected)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *definitions], type_ignores=[])), str(ROOT / path), "exec"), scope)
    return scope


class HttpErrorSafety(unittest.IsolatedAsyncioTestCase):
    def assert_redacted(self, result, status):
        self.assertEqual(result.status_code, status)
        self.assertNotIn(MARKER, json.dumps(result.content))

    async def test_cephfs_health(self):
        import subprocess
        scope = handlers(PIPE, ["cephfs_health"])
        with patch.dict(os.environ, {"MIOS_STORAGE_CEPHFS_ENABLE": "true"}), patch.object(subprocess, "run", side_effect=RuntimeError(MARKER)):
            result = await scope["cephfs_health"]()
        self.assertEqual(result["health"]["status"], "UNAVAILABLE")
        self.assertNotIn(MARKER, json.dumps(result))
        scope["log"].warning.assert_called_once()
        with patch.dict(os.environ, {"MIOS_STORAGE_CEPHFS_ENABLE": "true"}), patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='{"status":"OK"}')):
            self.assertEqual((await scope["cephfs_health"]())["health"]["status"], "OK")

    async def test_lora_failure_and_success(self):
        client = SimpleNamespace(post=AsyncMock(side_effect=RuntimeError(MARKER)))
        vllm_lane = lambda section: {"heavy_engine": "vllm"} if section == "ai" else {}
        scope = handlers(PIPE, ["lora_load"], _TOOL_BACKEND_HEAVY="unused", _toml_section=vllm_lane,
                         _get_client=AsyncMock(return_value=client))
        request = SimpleNamespace(json=AsyncMock(return_value={"lora_name": "adapter", "lora_path": "/adapter"}))
        self.assert_redacted(await scope["lora_load"](request), 500)
        client.post = AsyncMock(return_value=SimpleNamespace(content=b"ok", status_code=201, headers={}))
        result = await scope["lora_load"](request)
        self.assertEqual((result.status_code, result.content), (201, b"ok"))

    async def test_ast_diff_and_review(self):
        engine = SimpleNamespace(compute_ast_diff=Mock(side_effect=RuntimeError(MARKER)))
        gate = SimpleNamespace(**{name: Mock(side_effect=RuntimeError(MARKER)) for name in ("create_review", "submit_vote", "get_review")})
        module = SimpleNamespace(AstDiffEngine=lambda: engine, global_gate=gate)
        scope = handlers(PIPE, ["ast_diff_endpoint", "ast_review_endpoint"])
        request = SimpleNamespace(json=AsyncMock(return_value={}))
        with patch.dict(sys.modules, {"mios_ast_diff": module}):
            self.assert_redacted(await scope["ast_diff_endpoint"](request), 400)
            engine.compute_ast_diff = Mock(return_value={"has_semantic_diff": False})
            self.assertEqual((await scope["ast_diff_endpoint"](request)).content, {"has_semantic_diff": False})
            for action, operation in (("create", "create_review"), ("vote", "submit_vote"), ("status", "get_review")):
                request.json.return_value = {"action": action}
                self.assert_redacted(await scope["ast_review_endpoint"](request), 400)
                getattr(gate, operation).side_effect = None
                getattr(gate, operation).return_value = {"review_id": "existing"}
                self.assertEqual((await scope["ast_review_endpoint"](request)).content["review_id"], "existing")

    async def test_portal_configuration_failures_and_success(self):
        config = SimpleNamespace(to_toml=Mock(side_effect=RuntimeError(MARKER)), write_user_config=Mock(), validate_config=Mock(return_value=(True, [])))
        toml = SimpleNamespace(load_merged=Mock(return_value={}))
        scope = handlers(PORTAL, ["get_portal_config", "post_portal_config"], _portal_authed=lambda request: True, run_db_reseed_bg=lambda: None)
        request = SimpleNamespace(body=AsyncMock(return_value=b"[identity]\nusername='mios'\n"))
        background = SimpleNamespace(add_task=Mock())
        with patch.dict(sys.modules, {"mios_toml": toml, "mios_pipe.kernel.config": config}):
            self.assert_redacted(await scope["get_portal_config"](request), 500)
            config.to_toml.side_effect = None
            config.to_toml.return_value = "resolved TOML"
            self.assertEqual((await scope["get_portal_config"](request)).content, "resolved TOML")
            request.body.return_value = ("invalid = " + MARKER).encode()
            self.assert_redacted(await scope["post_portal_config"](request, background), 400)
            config.write_user_config.assert_not_called()
            request.body.return_value = b"[identity]\nusername='mios'\n"
            config.write_user_config.side_effect = RuntimeError(MARKER)
            self.assert_redacted(await scope["post_portal_config"](request, background), 500)
            background.add_task.assert_not_called()
            config.write_user_config.side_effect = None
            self.assertEqual((await scope["post_portal_config"](request, background)).content, {"status": "ok"})
            background.add_task.assert_called_once()

    async def test_validator_errors_are_safe(self):
        scope = handlers(CONFIG, ["validate_config"], _validate_max_bytes=Mock(side_effect=RuntimeError(MARKER)), _VALIDATE_CRITICAL_SECTIONS=("identity", "ports"))
        for payload in ("x = 1", "invalid = " + MARKER):
            ok, errors = scope["validate_config"](payload)
            self.assertFalse(ok)
            self.assertNotIn(MARKER, json.dumps(errors))
            scope["_validate_max_bytes"].side_effect = None
            scope["_validate_max_bytes"].return_value = 10000
        self.assertEqual(scope["validate_config"]("[ports]\ntest = 8700\n"), (True, []))

    async def test_crawl_failure_fallback_and_success(self):
        good = dict(ok=True, markdown="page", url="https://example.invalid", title="Title", internal_links=1, external_links=2)
        scope = handlers(CRAWL, ["crawl"], _crawl_cdp=AsyncMock(side_effect=RuntimeError(MARKER)), _crawl_camoufox=AsyncMock(side_effect=RuntimeError(MARKER)), _md_ok=lambda value: True, CAMOUFOX_ON=True)
        request = SimpleNamespace(url="https://example.invalid", force_camoufox=False)
        result = await scope["crawl"](request)
        self.assertFalse(result["success"])
        self.assertNotIn(MARKER, json.dumps(result))
        scope["_crawl_camoufox"].side_effect = None
        scope["_crawl_camoufox"].return_value = good
        result = await scope["crawl"](request)
        self.assertTrue(result["success"])
        self.assertEqual((result["engine"], result["links"]), ("camoufox", 3))
        self.assertNotIn(MARKER, json.dumps(result))
        scope["_crawl_cdp"].side_effect = None
        scope["_crawl_cdp"].return_value = good
        self.assertEqual((await scope["crawl"](request))["engine"], "chrome-cdp")

    async def test_gateway_openai_errors_and_native_success(self):
        smol = SimpleNamespace(OpenAIServerModel=Mock(side_effect=RuntimeError(MARKER)), ToolCallingAgent=Mock(side_effect=RuntimeError(MARKER)))
        session = SimpleNamespace(get_session=AsyncMock(return_value=[]), save_session=AsyncMock())
        settings = {}
        scope = handlers(GATEWAY, ["chat_completions", "openai_error"], session_db=session, _toml_section=lambda section: settings, tool_registry=None, skill_catalog_loader=None)
        request = SimpleNamespace(messages=[{"role": "user", "content": "task"}], metadata={}, model="test", stream=False, model_dump=lambda **kw: {})
        with patch.dict(sys.modules, {"smolagents": smol}):
            for code in ("model_init_failed", "agent_init_failed"):
                result = await scope["chat_completions"](request)
                self.assert_redacted(result, 500)
                self.assertEqual(result.content["error"]["code"], code)
                smol.OpenAIServerModel.side_effect = None
            settings["tool_loop_engine"] = "native"
            client = SimpleNamespace(post=AsyncMock(side_effect=RuntimeError(MARKER)))
            class Context:
                async def __aenter__(self): return client
                async def __aexit__(self, *args): return False
            with patch.dict(sys.modules, {"httpx": SimpleNamespace(AsyncClient=lambda: Context())}):
                result = await scope["chat_completions"](request)
                self.assert_redacted(result, 502)
                self.assertEqual(result.content["error"]["code"], "upstream_unavailable")
                client.post.side_effect = None
                client.post.return_value = SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "answer"}}]})
                self.assertEqual((await scope["chat_completions"](request)).content["choices"][0]["message"]["content"], "answer")

    async def test_gateway_agent_execution_and_stream_contracts(self):
        class ActionStep(SimpleNamespace):
            pass

        class FinalAnswerStep(SimpleNamespace):
            pass

        max_steps_error = type("AgentMaxStepsError", (Exception,), {})
        for stream in (False, True):
            for outcome in ("error", "max_steps", "success"):
                with self.subTest(stream=stream, outcome=outcome):
                    failure = max_steps_error(MARKER) if outcome == "max_steps" else RuntimeError(MARKER)
                    agent = SimpleNamespace(run=Mock(side_effect=None if outcome == "success" else failure))
                    agent.run.return_value = [FinalAnswerStep(output="answer")] if stream else "answer"
                    smol = SimpleNamespace(OpenAIServerModel=Mock(), ToolCallingAgent=Mock(return_value=agent))
                    session = SimpleNamespace(get_session=AsyncMock(return_value=[]), save_session=AsyncMock())
                    scope = handlers(GATEWAY, ["chat_completions", "openai_error"],
                                     session_db=session, _toml_section=lambda section: {},
                                     tool_registry=None, skill_catalog_loader=None,
                                     asyncio=asyncio, time=time, StreamingResponse=response)
                    request = SimpleNamespace(messages=[{"role": "user", "content": "task"}],
                                              metadata={}, model="test", stream=stream)
                    modules = {"smolagents": smol, "smolagents.memory": SimpleNamespace(ActionStep=ActionStep),
                               "smolagents.agents": SimpleNamespace(FinalAnswerStep=FinalAnswerStep)}
                    with patch.dict(sys.modules, modules):
                        result = await scope["chat_completions"](request)
                        if stream:
                            chunks = [chunk async for chunk in result.content]
                            self.assertNotIn(MARKER, "".join(chunks))
                            self.assertEqual(chunks[-1], "data: [DONE]\n\n")
                            payloads = [json.loads(chunk.removeprefix("data: ")) for chunk in chunks[:-1]]
                            self.assertEqual(payloads[-1]["choices"][0]["finish_reason"],
                                             "length" if outcome == "max_steps" else "stop")
                            if outcome == "success":
                                self.assertIn("answer", "".join(chunks))
                        elif outcome == "error":
                            self.assert_redacted(result, 500)
                            self.assertEqual(result.content["error"]["code"], "agent_loop_failed")
                        else:
                            self.assertEqual(result["choices"][0]["finish_reason"],
                                             "length" if outcome == "max_steps" else "stop")
                            self.assertEqual(result["choices"][0]["message"]["content"],
                                             "[Agent Max Steps Reached]" if outcome == "max_steps" else "answer")
                        if outcome == "error":
                            self.assertIs(scope["log"].error.call_args.args[1], failure)


class PortalSessionTests(unittest.TestCase):
    """Execute actual session settings/functions without importing service dependencies."""

    def sessions(self, config=None, environment=None):
        tree = ast.parse((ROOT / PORTAL).read_text(encoding="utf-8"))
        assignments = {"PORTAL_PASSWORD", "PORTAL_USER", "PORTAL_SESSION_TTL",
                       "_portal_secret_cfg", "_PORTAL_SECRET"}
        functions = {"_pcfg", "_portal_session_key", "_portal_make_token", "_portal_token_ok"}
        nodes = [node for node in tree.body if
                 (isinstance(node, ast.FunctionDef) and node.name in functions) or
                 (isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id in assignments
                     for target in node.targets))]
        scope = {"os": os, "hashlib": hashlib, "hmac": hmac, "base64": base64,
                 "time": time, "Optional": __import__("typing").Optional,
                 "_PORTAL_TOML": {"portal": config or {}, "identity": {"default_password": "test-password"}}}
        with patch.dict(os.environ, environment or {}, clear=True):
            exec(compile(ast.Module(nodes, type_ignores=[]), PORTAL, "exec"), scope)
        return scope

    def test_password_key_uses_stretched_derivation(self):
        expected = hashlib.pbkdf2_hmac("sha256", b"test-password", b"mios-portal-session|v2", 600000)
        with patch.object(hashlib, "pbkdf2_hmac", wraps=hashlib.pbkdf2_hmac) as derive:
            scope = self.sessions()
        self.assertEqual(scope["_PORTAL_SECRET"], expected)
        derive.assert_called_once_with("sha256", b"test-password", b"mios-portal-session|v2", 600000)

    def test_worker_restart_rotation_and_legacy_rejection(self):
        first, second = self.sessions(), self.sessions()
        token = first["_portal_make_token"]("mios")
        self.assertTrue(second["_portal_token_ok"](token))
        changed = self.sessions({"password": "rotated-password"})
        self.assertFalse(changed["_portal_token_ok"](token))
        body = token.partition(".")[0]
        legacy_key = hashlib.sha256(b"mios-portal-session|test-password").digest()
        legacy = body + "." + hmac.new(legacy_key, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
        self.assertFalse(second["_portal_token_ok"](legacy))
        self.assertFalse(second["_portal_token_ok"](token[:-1] + ("0" if token[-1] != "0" else "1")))
        with patch.object(time, "time", return_value=time.time() + 604801):
            self.assertFalse(second["_portal_token_ok"](token))

    def test_explicit_secret_keeps_sessions_across_password_changes(self):
        with patch.object(hashlib, "pbkdf2_hmac", side_effect=AssertionError("explicit secret must bypass derivation")):
            first = self.sessions({"secret": "stable-test-key"})
            second = self.sessions({"secret": "stable-test-key", "password": "rotated-password"})
            environment = self.sessions({"secret": "ignored"}, {"MIOS_PORTAL_SECRET": "stable-test-key"})
        token = first["_portal_make_token"]("mios")
        self.assertTrue(second["_portal_token_ok"](token))
        self.assertTrue(environment["_portal_token_ok"](token))

    def test_ssot_and_environment_work_factor(self):
        for config, environment, expected in (
            ({"session_kdf_iterations": 610000}, {}, 610000),
            ({"session_kdf_iterations": 610000}, {"MIOS_PORTAL_SESSION_KDF_ITERATIONS": "620000"}, 620000),
            ({"session_kdf_iterations": 610000}, {"MIOS_PORTAL_SESSION_KDF_ITERATIONS": ""}, 610000),
        ):
            with self.subTest(config=config, environment=environment):
                with patch.object(hashlib, "pbkdf2_hmac", return_value=b"derived-test-key") as derive:
                    scope = self.sessions(config, environment)
                self.assertEqual(scope["_PORTAL_SECRET"], b"derived-test-key")
                self.assertEqual(derive.call_args.args[-1], expected)
        for value in (0, -1, 599999, "invalid", True, 600000.5):
            with self.subTest(invalid=value), self.assertRaises(ValueError):
                self.sessions({"session_kdf_iterations": value})


if __name__ == "__main__":
    unittest.main(verbosity=2)
