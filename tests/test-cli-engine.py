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

def main() -> int:
    suite = unittest.TestSuite(unittest.TestLoader().loadTestsFromTestCase(case)
        for case in (TestCliEngine, TestCliChatCompletion))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
