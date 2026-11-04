#!/usr/bin/env python3
# AI-hint: Automated unit test suite for unified mios CLI engine and output formatting (task_457).
# AI-doc: usr/share/doc/mios/manual/mios.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MIOS_BIN = os.path.join(_ROOT, "usr", "bin", "mios")
_CLI_LIB = os.path.join(_ROOT, "usr", "lib", "mios")

if _CLI_LIB not in sys.path:
    sys.path.insert(0, _CLI_LIB)


class TestCliEngine(unittest.TestCase):
    """Validates unified CLI engine, POSIX exit codes, and adaptive formatting."""

    def setUp(self):
        self.env = os.environ.copy()
        self.env["MIOS_ROOT"] = _ROOT

    def test_cli_binary_exists(self):
        self.assertTrue(os.path.isfile(_MIOS_BIN), f"Missing {_MIOS_BIN}")
        self.assertTrue(os.access(_MIOS_BIN, os.X_OK), f"Not executable: {_MIOS_BIN}")

    def test_version_json_output(self):
        res = subprocess.run([_MIOS_BIN, "version", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("version"), "0.3.0")
        self.assertEqual(data.get("law_conformance"), 16)

    def test_version_yaml_output(self):
        res = subprocess.run([_MIOS_BIN, "version", "--yaml"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        self.assertIn("version: 0.3.0", res.stdout)
        self.assertIn("law_conformance: 16", res.stdout)

    def test_health_json_output(self):
        res = subprocess.run([_MIOS_BIN, "health", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "healthy")
        self.assertTrue(data.get("immutable_root"))

    def test_status_json_output(self):
        res = subprocess.run([_MIOS_BIN, "status", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIn("os", data)
        self.assertIn("cpu", data)
        self.assertIn("memory", data)
        self.assertIn("services", data)

    def test_formatter_module_direct(self):
        from cli.formatter import OutputFormatter
        formatter = OutputFormatter(force_format="json")
        sample = {"test": 123, "active": True}
        out = formatter.format(sample)
        parsed = json.loads(out)
        self.assertEqual(parsed["test"], 123)



class TestCliChatCompletion(unittest.TestCase):
    """Exercise the public CLI with the actual SDK and in-memory SSE transport."""

    def setUp(self):
        import importlib.machinery
        import importlib.util
        from unittest.mock import patch
        loader = importlib.machinery.SourceFileLoader("mios_cli_offline", _MIOS_BIN)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.cli = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"MIOS_AI_KEY": "offline-fixture",
                "MIOS_AI_ENDPOINT": "http://offline.invalid/v1",
                "MIOS_AI_GATEWAY_MODEL": "fixture"}):
            loader.exec_module(self.cli)

    @staticmethod
    def wire(*parts):
        chunks = []
        for delta, reason in parts:
            chunks.append("data: " + json.dumps({"id": "fixture", "object": "chat.completion.chunk",
                "created": 1, "model": "fixture", "choices": [{"index": 0,
                "delta": delta, "finish_reason": reason}]}) + "\n\n")
        return "".join(chunks) + "data: [DONE]\n\n"

    def run_stream(self, wires, *, tools=None):
        import contextlib
        import io
        import httpx
        from openai import OpenAI
        responses = iter(wires)
        self.requests = []
        def handler(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                text=next(responses))
        self.output = io.StringIO()
        self.answer = []
        self.messages = [{"role": "user", "content": "An offline request"}]
        with OpenAI(api_key="offline-fixture", base_url="http://offline.invalid/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
            with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(io.StringIO()):
                self.cli.chat_streaming(client, self.messages, tools, None, answer_box=self.answer)

    def test_final_answer_after_progress(self):
        self.run_stream([self.wire(({"role": "assistant", "content": "Tool evidence. "}, None),
            ({"content": "Here is the final answer."}, None), ({}, "stop"))])
        self.assertIn("Here is the final answer.", self.output.getvalue())
        self.assertIn("Here is the final answer.", "".join(self.answer))
        self.assertEqual(len(self.requests), 1)

    def test_truncated_and_empty_streams_fail(self):
        for reason, content in ((None, "partial"), ("length", "partial"),
                ("content_filter", "partial"), ("stop", "")):
            with self.subTest(reason=reason, content=content):
                with self.assertRaises(Exception):
                    self.run_stream([self.wire(({"role": "assistant", "content": content}, reason))])
                self.assertEqual(len(self.messages), 1)

    def test_tool_call_requires_completed_final_answer(self):
        from unittest.mock import Mock
        call = {"index": 0, "id": "offline-call", "type": "function",
            "function": {"name": "inspect_fixture", "arguments": "{}"}}
        tools = [{"type": "function", "function": {"name": "inspect_fixture",
            "parameters": {"type": "object", "properties": {}}}}]
        tool_wire = self.wire(({"role": "assistant", "tool_calls": [call]}, None),
            ({}, "tool_calls"))
        for final_ok in (True, False):
            self.cli.dispatch_tool = Mock(return_value={"success": True})
            final = self.wire(({"role": "assistant", "content": "A completed answer." if final_ok else ""}, None),
                ({}, "stop" if final_ok else None))
            if final_ok:
                self.run_stream([tool_wire, final], tools=tools)
                self.assertIn("A completed answer.", self.output.getvalue())
            else:
                with self.assertRaises(Exception):
                    self.run_stream([tool_wire, final], tools=tools)
            self.assertEqual(len(self.requests), 2)
            self.cli.dispatch_tool.assert_called_once()
        self.cli.dispatch_tool.reset_mock()
        with self.assertRaises(Exception):
            self.run_stream([self.wire(({"role": "assistant", "tool_calls": [call]}, None))], tools=tools)
        self.cli.dispatch_tool.assert_not_called()

class TestCliRequestPolicy(unittest.TestCase):
    """Use the actual SDK transport to bound interactive request attempts."""

    setUp = TestCliChatCompletion.setUp
    wire = staticmethod(TestCliChatCompletion.wire)

    def run_main(self, handler, *, policy_env=None, host_policy=None, argv=None):
        import contextlib
        import io
        import pathlib
        import tempfile
        import types
        import httpx
        import openai
        from unittest.mock import Mock, patch
        real_client = openai.OpenAI
        self.requests = []
        self.client_options = []
        clients = []
        self.output, self.errors = io.StringIO(), io.StringIO()
        session = {"id": "request-policy-fixture", "history": []}
        self.saved = Mock()
        self.action = Mock(return_value='{"success":true}')

        def transport(request):
            self.requests.append(request)
            return handler(request, len(self.requests))

        def client_factory(**options):
            self.client_options.append(options)
            client = real_client(**options,
                http_client=httpx.Client(transport=httpx.MockTransport(transport)))
            clients.append(client)
            return client

        plugins = types.ModuleType("cli.plugin_loader")
        plugins.get_available_plugins = lambda: {}
        plugins.run_plugin = lambda *args: None
        with tempfile.TemporaryDirectory(prefix="mios-cli-request-policy-") as directory:
            root = pathlib.Path(directory)
            host = root / "host.toml"
            host.write_text(host_policy or "")
            env = {key: value for key, value in os.environ.items()
                if not key.startswith(("MIOS_", "OPENAI_", "PYTHON"))}
            env.update(MIOS_AI_KEY="offline-fixture",
                MIOS_AI_ENDPOINT="http://offline.invalid/v1",
                MIOS_AI_GATEWAY_MODEL="fixture",
                MIOS_VENDOR_TOML=os.path.join(_ROOT, "usr/share/mios/mios.toml"),
                MIOS_VENDOR_TOML_D=os.path.join(_ROOT, "usr/lib/mios/mios.d"),
                MIOS_HOST_TOML=str(host), MIOS_HOST_TOML_D=str(root / "host.d"),
                MIOS_USER_TOML=str(root / "no-user.toml"),
                MIOS_USER_TOML_D=str(root / "no-user.d"),
                MIOS_DB_AUTHORITATIVE="0", MIOS_RESOLVER_NATIVE="0")
            env.update(policy_env or {})
            self.cli._AI_CONFIG = None
            try:
                with patch.dict(os.environ, env, clear=True), \
                        patch.dict(sys.modules, {"cli.plugin_loader": plugins}), \
                        patch.object(openai, "OpenAI", side_effect=client_factory), \
                        patch.object(self.cli, "load_session", return_value=session), \
                        patch.object(self.cli, "save_session", self.saved), \
                        patch.object(self.cli, "resolve_system_prompt", return_value=""), \
                        patch.object(self.cli, "dispatch_tool", self.action), \
                        contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.errors):
                    result = self.cli.main(argv or ["mios", "An opaque request."])
            finally:
                for client in clients:
                    client.close()
        self.session = session
        return result

    @staticmethod
    def unavailable(request, attempt):
        import httpx
        return httpx.Response(503, json={"error": {"message": "fixture unavailable",
            "type": "server_error"}}, request=request)

    def success(self, request, attempt):
        import httpx
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text=self.wire(({"role": "assistant", "content": "A completed answer."}, None),
                           ({}, "stop")), request=request)

    def test_default_gateway_failure_is_not_replayed(self):
        self.assertEqual(self.run_main(self.unavailable), 1)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.client_options[0]["max_retries"], 0)
        self.assertIn("fixture unavailable", self.errors.getvalue())
        self.saved.assert_not_called()

    def test_explicit_sdk_retry_is_opt_in(self):
        def handler(request, attempt):
            return self.unavailable(request, attempt) if attempt == 1 else self.success(request, attempt)
        result = self.run_main(handler, policy_env={"MIOS_AI_CLI_MAX_RETRIES": "1"})
        self.assertEqual(result, 0, self.errors.getvalue())
        self.assertEqual(len(self.requests), 2)
        self.assertIn("A completed answer.", self.output.getvalue())

    def test_layered_timeouts_reach_the_actual_http_request(self):
        host = "[ai.cli]\nmax_retries=0\ntimeout_s=12.5\nconnect_timeout_s=2.0\n"
        self.assertEqual(self.run_main(self.success, host_policy=host), 0, self.errors.getvalue())
        timeout = self.requests[0].extensions["timeout"]
        self.assertEqual(timeout, {"connect": 2.0, "read": 12.5, "write": 12.5, "pool": 12.5})
        self.assertEqual(self.run_main(self.success, host_policy=host,
            policy_env={"MIOS_AI_CLI_TIMEOUT_S": "3.5",
                        "MIOS_AI_CLI_CONNECT_TIMEOUT_S": "0.25"}), 0, self.errors.getvalue())
        self.assertEqual(self.requests[0].extensions["timeout"],
            {"connect": 0.25, "read": 3.5, "write": 3.5, "pool": 3.5})

    def test_sdk_read_timeout_is_not_retried_by_default(self):
        import httpx
        def handler(request, attempt):
            raise httpx.ReadTimeout("fixture read timeout", request=request)
        self.assertEqual(self.run_main(handler), 1)
        self.assertEqual(len(self.requests), 1)
        self.assertIn("APITimeoutError", self.errors.getvalue())
        self.saved.assert_not_called()

    def test_invalid_limits_fail_before_sdk_or_transport(self):
        invalid = (("MAX_RETRIES", "-1"), ("MAX_RETRIES", "0.5"),
                   ("TIMEOUT_S", "0"), ("TIMEOUT_S", "nan"), ("TIMEOUT_S", "inf"),
                   ("CONNECT_TIMEOUT_S", "-1"), ("CONNECT_TIMEOUT_S", "not-seconds"))
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                self.assertEqual(self.run_main(self.success,
                    policy_env={"MIOS_AI_CLI_" + key: value}), 2)
                self.assertEqual(self.requests, [])
                self.assertEqual(self.client_options, [])
                self.assertIn("invalid AI configuration", self.errors.getvalue())

    def test_interrupted_stream_never_replays_partial_output(self):
        import httpx
        prefix = self.wire(({"role": "assistant", "content": "Partial answer."}, None))
        prefix = prefix.split("data: [DONE]", 1)[0].encode()
        class BrokenStream(httpx.SyncByteStream):
            def __iter__(self):
                yield prefix
                raise httpx.ReadError("fixture interrupted stream")
        def handler(request, attempt):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=BrokenStream(), request=request)
        self.assertEqual(self.run_main(handler,
            policy_env={"MIOS_AI_CLI_MAX_RETRIES": "2"}), 1)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.output.getvalue().count("Partial answer."), 1)
        self.assertEqual(self.session["history"], [])
        self.saved.assert_not_called()

    def test_tool_action_is_not_replayed_when_final_request_fails(self):
        import httpx
        call = {"index": 0, "id": "fixture-action", "type": "function",
            "function": {"name": "fixture_action", "arguments": "{}"}}
        def handler(request, attempt):
            if attempt == 1:
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                    text=self.wire(({"role": "assistant", "tool_calls": [call]}, None),
                                   ({}, "tool_calls")), request=request)
            return self.unavailable(request, attempt)
        self.assertEqual(self.run_main(handler), 1)
        self.assertEqual(len(self.requests), 2)
        self.action.assert_called_once_with("fixture_action", {})
        self.saved.assert_not_called()

    def test_local_help_ignores_invalid_request_policy(self):
        self.assertEqual(self.run_main(self.unavailable,
            policy_env={"MIOS_AI_CLI_MAX_RETRIES": "-1"}, argv=["mios", "--help"]), 0)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.client_options, [])


def main() -> int:
    suite = unittest.TestSuite(unittest.TestLoader().loadTestsFromTestCase(case)
        for case in (TestCliEngine, TestCliChatCompletion, TestCliRequestPolicy))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
