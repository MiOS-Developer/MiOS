#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_interop (WS-11 3-projection: the A2A skill shape). Pure stdlib, no server.py/DB/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_interop (WS-11)."""

import sys

import mios_interop as io

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

VERB = {"section": "Window / app launch", "desc": "launch an app", "permission": "write",
        "tier": "core", "model_name": "launch_windows_app"}

def t_verb():
    s = io.to_a2a_skill("open_app", VERB, "verb")
    check("verb: bare id (no prefix)", s["id"] == "open_app")
    check("verb: display = model_name", s["name"] == "launch_windows_app")
    check("verb: description", s["description"] == "launch an app")
    check("verb: tags include kind + perm + tier", set(["verb", "perm:write", "tier:core"]) <= set(s["tags"]), s["tags"])
    check("verb: section tag normalized", "window___app_launch" in s["tags"], s["tags"])

def t_namespacing():
    check("recipe: prefixed id", io.to_a2a_skill("disk_usage", {"description": "df"}, "recipe")["id"] == "mios_recipe__disk_usage")
    check("skill: prefixed id", io.to_a2a_skill("summarize", {"description": "x"}, "skill")["id"] == "mios_skill__summarize")
    check("recipe: description from 'description' key", io.to_a2a_skill("r", {"description": "d"}, "recipe")["description"] == "d")

def t_tags_dedup_and_fallback():
    s = io.to_a2a_skill("v", {"permission": "read"}, "verb")
    check("tags: no dup verb tag", s["tags"].count("verb") == 1)
    check("name: falls back to id when no model_name", io.to_a2a_skill("bare", {}, "verb")["name"] == "bare")
    check("desc: empty when absent", io.to_a2a_skill("x", {}, "verb")["description"] == "")

def t_project_all():
    p = io.project_all("open_app", VERB, "verb")
    check("project_all: function_name bare", p["function_name"] == "open_app")
    check("project_all: a2a_id matches verb (bare)", p["a2a_id"] == "open_app")
    pr = io.project_all("disk_usage", {"description": "df"}, "recipe")
    check("project_all: recipe function vs a2a id differ by prefix",
          pr["function_name"] == "disk_usage" and pr["a2a_id"] == "mios_recipe__disk_usage")
    check("project_all: shared description", pr["description"] == "df")

def t_desc_cap():
    long = io.to_a2a_skill("x", {"desc": "z" * 999}, "verb")["description"]
    check("desc: capped to 500", len(long) == 500)

def main():
    t_verb()
    t_namespacing()
    t_tags_dedup_and_fallback()
    t_project_all()
    t_desc_cap()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_remote_adapter.py (task_1006)
# ==============================================================================
# AI-hint: Unit test for mios_pipe.routing.remote_adapter. Validates Anthropic, Gemini, and OpenAI remote calls.
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from mios_pipe.routing.remote_adapter import call_remote

class TestRemoteAdapter(unittest.TestCase):
    def test_openai_passthrough(self):
        async def _run():
            node_cfg = {"name": "remote-oai", "api": "openai"}
            oai_req = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}

            captured = []
            async def mock_transport(cfg, payload):
                captured.append((cfg, payload))
                return {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}

            res = await call_remote(node_cfg, oai_req, mock_transport)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0][1], oai_req)
            self.assertEqual(res["choices"][0]["message"]["content"], "hello")

        asyncio.run(_run())

    def test_anthropic_adapter(self):
        async def _run():
            node_cfg = {"name": "remote-claude", "api": "anthropic"}
            oai_req = {
                "model": "claude-3-5-sonnet",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hello"}
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "get weather",
                            "parameters": {"type": "object", "properties": {"loc": {"type": "string"}}}
                        }
                    }
                ]
            }

            captured = []
            async def mock_transport(cfg, payload):
                captured.append(payload)
                return {
                    "content": [
                        {"type": "text", "text": "The weather is sunny."},
                        {"type": "tool_use", "id": "call_123", "name": "get_weather", "input": {"loc": "NYC"}}
                    ]
                }

            res = await call_remote(node_cfg, oai_req, mock_transport)
            self.assertEqual(len(captured), 1)
            p = captured[0]
            self.assertEqual(p["system"], "You are helpful.")
            self.assertEqual(p["messages"][0]["role"], "user")
            self.assertEqual(p["messages"][0]["content"][0]["text"], "Hello")
            self.assertEqual(p["tools"][0]["name"], "get_weather")

            self.assertEqual(res["role"], "assistant")
            self.assertEqual(res["content"], "The weather is sunny.")
            self.assertEqual(len(res["tool_calls"]), 1)
            self.assertEqual(res["tool_calls"][0]["function"]["name"], "get_weather")

        asyncio.run(_run())

    def test_gemini_adapter(self):
        async def _run():
            node_cfg = {"name": "remote-gemini", "api": "gemini"}
            oai_req = {
                "model": "gemini-1.5-pro",
                "messages": [
                    {"role": "system", "content": "System prompt"},
                    {"role": "user", "content": "Hi"}
                ]
            }

            captured = []
            async def mock_transport(cfg, payload):
                captured.append(payload)
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "Gemini response text"}]
                            }
                        }
                    ]
                }

            res = await call_remote(node_cfg, oai_req, mock_transport)
            self.assertEqual(len(captured), 1)
            p = captured[0]
            self.assertEqual(p["systemInstruction"]["parts"][0]["text"], "System prompt")
            self.assertEqual(p["contents"][0]["role"], "user")

            self.assertEqual(res["role"], "assistant")
            self.assertEqual(res["content"], "Gemini response text")

        asyncio.run(_run())


def _run_extra_remote_adapter():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestRemoteAdapter))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_interop_suites():
    rc = _run_extra_remote_adapter()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_interop_suites()
    sys.exit(_rc_main)
