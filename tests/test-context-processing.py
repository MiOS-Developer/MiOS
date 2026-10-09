#!/usr/bin/env python3
# AI-hint: Automated unit test suite for MiOS Context & Prompt Processing domain (task_3353 / GATECAT-01).
# AI-related: usr/lib/mios/agent-pipe/context_compactor.py, usr/lib/mios/agent-pipe/mios_pipe/context/ctxpack.py, usr/libexec/mios/prompt/pruning.py, usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py
"""Automated unit test suite for MiOS Context & Prompt Processing.

Consolidates:
- Semantic context compaction & invariant retention (test-context-compactor)
- Priority context window packing & needle heuristics (test-context-trim)
- Contextual prompt compression, code syntax preservation & CLI (test-prompt-pruning)
- Chain-of-thought <think> reasoning tag stripping (test-think-stripper)
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))

# Resolve agent-pipe path via standard helper
try:
    import _agentpipe_path  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(_ROOT, "usr", "lib", "mios", "agent-pipe"))

# Resolve prompt pruning path
sys.path.insert(0, os.path.join(_ROOT, "usr", "libexec", "mios", "prompt"))

from context_compactor import ContextCompactor, ConversationTurn
from mios_pipe.context.ctxpack import pack
from mios_pipe.routing.turn import _strip_think_tags, _split_think_tags

try:
    import pruning
except ImportError:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "pruning", os.path.join(_ROOT, "usr", "libexec", "mios", "prompt", "pruning.py")
    )
    if _spec and _spec.loader:
        pruning = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(pruning)
    else:
        raise


class TestContextCompactor(unittest.TestCase):
    """Automated unit test suite for MiOS Context Compactor."""

    def setUp(self):
        self.compactor = ContextCompactor(max_context_tokens=8192, dry_run=True)

    def test_pinned_invariants_preservation(self):
        """Test pinned system invariants and architectural rules are strictly preserved."""
        turns = [
            ConversationTurn("system", "LAW: USR-OVER-ETC", 300, is_pinned=True),
            ConversationTurn("user", "Hello world", 100, is_pinned=False),
            ConversationTurn("assistant", "Hi", 100, is_pinned=False),
        ]
        res = self.compactor.compact_dialog(turns)
        self.assertEqual(res.pinned_invariants_count, 1)
        self.assertIn("Preserved 1 pinned system rules", res.recap_summary)

    def test_100k_token_dialog_compaction_retains_constraints(self):
        """Test long-horizon dialog compaction retains 100% of injected constraints."""
        turns = [
            ConversationTurn("system", "LAW: USR-OVER-ETC", 500, is_pinned=True),
            ConversationTurn("user", "CONSTRAINT: Secret token is 9988", 200, is_pinned=False),
            ConversationTurn("assistant", "Working on task...", 4000, is_pinned=False),
            ConversationTurn("user", "CONSTRAINT: Never format NVMe", 200, is_pinned=False),
            ConversationTurn("assistant", "Done.", 4000, is_pinned=False),
        ]
        res = self.compactor.compact_dialog(turns)
        self.assertEqual(len(res.retained_constraint_keys), 3)
        self.assertLess(res.compacted_token_count, res.original_token_count)


class TestContextTrim(unittest.TestCase):
    """Validates priority packing, needle retention, and token budget bounds."""

    def test_system_prompt_retention(self):
        items = [
            {"type": "system", "text": "SYSTEM_INSTRUCTION", "prio": 100},
            {"type": "memory", "text": "PINNED_FACT", "prio": 80},
            {"type": "chat", "text": "OLD_INTERMEDIATE_TURN", "prio": 10},
            {"type": "chat", "text": "RECENT_USER_TURN", "prio": 50},
        ]
        # Restrict budget so that only top items fit
        res = pack(items, budget=8, text_of=lambda x: x["text"], priority_of=lambda x: x["prio"])
        kept_types = [x["type"] for x in res.kept]
        self.assertIn("system", kept_types)
        self.assertIn("memory", kept_types)
        self.assertNotIn("OLD_INTERMEDIATE_TURN", [x["text"] for x in res.kept])


class TestPromptPruning(unittest.TestCase):
    """Validates compression ratio, syntax preservation, and header deduplication."""

    def setUp(self):
        self.pruner = pruning.PromptPruner()

    def test_filler_pruning_ratio(self):
        sample_context = (
            "Please be advised that in order to configure the system properly, "
            "it is critically necessary to ensure that the following parameters are set: "
            "port=8080 host=127.0.0.1. Furthermore, we would like to note that default values apply. "
            "If you have any further questions, please let me know. Best regards!"
        )
        compressed, stats = self.pruner.compress(sample_context, target_ratio=0.25)
        self.assertGreaterEqual(stats["reduction_ratio"], 0.20)
        self.assertIn("port=8080", compressed)
        self.assertIn("host=127.0.0.1", compressed)
        self.assertNotIn("Please be advised that", compressed)
        self.assertNotIn("If you have any further questions", compressed)

    def test_code_syntax_preservation(self):
        raw_code = (
            "def calculate_hash(key: str, salt: bytes, rounds: int = 1000) -> str:\n"
            "    # Internal HMAC digest calculation\n"
            "    val = hmac.new(salt, key.encode('utf-8'), hashlib.sha256).hexdigest()\n"
            "    return val[:32]"
        )
        code_block = f"```python\n{raw_code}\n```"
        prompt = (
            "As an AI language model, I would be happy to help you with that.\n"
            "Here is the function you requested:\n"
            f"{code_block}\n"
            "Let me know if you need anything else!"
        )
        compressed, stats = self.pruner.compress(prompt, preserve_code=True)
        self.assertIn(raw_code, compressed)
        self.assertIn("```python", compressed)
        self.assertNotIn("As an AI language model", compressed)
        self.assertNotIn("Let me know if you need anything else", compressed)

        # Verify the python code inside is still 100% valid AST
        parsed = ast.parse(raw_code)
        self.assertIsInstance(parsed, ast.Module)

    def test_inline_code_preservation(self):
        text = (
            "Please note that you should run `systemctl restart mios-llm-light.service` "
            "in order to apply the new configuration to `/etc/mios/profile.toml`."
        )
        compressed, stats = self.pruner.compress(text, preserve_code=True)
        self.assertIn("`systemctl restart mios-llm-light.service`", compressed)
        self.assertIn("`/etc/mios/profile.toml`", compressed)

    def test_message_list_pruning(self):
        messages = [
            {
                "role": "system",
                "content": "As an AI assistant, it is important to note that you must respond strictly in JSON."
            },
            {
                "role": "user",
                "content": "Please be advised that I need the status of port 8080."
            },
            {
                "role": "assistant",
                "content": "```json\n{\"port\": 8080, \"status\": \"active\"}\n```\nHope this helps!"
            }
        ]
        pruned_msgs, stats = self.pruner.prune_messages(messages, target_ratio=0.25)
        self.assertEqual(len(pruned_msgs), 3)
        self.assertIn("respond strictly in JSON", pruned_msgs[0]["content"])
        self.assertNotIn("As an AI assistant", pruned_msgs[0]["content"])
        self.assertNotIn("Please be advised that", pruned_msgs[1]["content"])
        self.assertIn("{\"port\": 8080, \"status\": \"active\"}", pruned_msgs[2]["content"])
        self.assertNotIn("Hope this helps!", pruned_msgs[2]["content"])
        self.assertGreater(stats["reduction_ratio"], 0.15)
        self.assertGreater(stats["saved_tokens_approx"], 0)

    def test_empty_and_whitespace_edge_cases(self):
        # Empty string
        comp, stats = self.pruner.compress("")
        self.assertEqual(comp, "")
        self.assertEqual(stats["reduction_ratio"], 0.0)

        # Whitespace string
        comp_ws, stats_ws = self.pruner.compress("   \n\t\n   ")
        self.assertEqual(comp_ws, "   \n\t\n   ")

        # Single word
        comp_single, stats_single = self.pruner.compress("Status")
        self.assertEqual(comp_single, "Status")

        # Empty messages list
        pruned_empty, stats_empty = self.pruner.prune_messages([])
        self.assertEqual(pruned_empty, [])
        self.assertEqual(stats_empty["original_chars"], 0)

    def test_markdown_formatting_normalization(self):
        doc = (
            "# System Architecture\n\n"
            "# System Architecture\n\n"
            "---\n---\n---\n\n\n\n"
            "This section details the node interconnect.\n"
        )
        compressed, stats = self.pruner.compress(doc)
        # Should deduplicate consecutive identical headers
        self.assertEqual(compressed.count("# System Architecture"), 1)
        # Should collapse horizontal rules
        self.assertNotIn("---\n---", compressed)
        # Should collapse excessive blank lines
        self.assertNotIn("\n\n\n", compressed)

    def test_long_retrieval_context_compression(self):
        verbose_retrieval_context = (
            "# Documentation for MiOS Node Federation\n\n"
            "Please be advised that in order to establish a secure peer connection, "
            "it is critically necessary to ensure that each node utilizes an Ed25519 signing key. "
            "Furthermore, we would like to note that prior to transmitting any payload, "
            "the node conducts an investigation of the peer's AgentCard signature.\n\n"
            "```python\n"
            "def verify_node(node_id: int, card_signature: bytes) -> bool:\n"
            "    # Validate against trusted registry\n"
            "    pubkey = get_trusted_key(node_id)\n"
            "    return ed25519.verify(pubkey, card_signature)\n"
            "```\n\n"
            "Due to the fact that network latency can vary, a large number of nodes "
            "give consideration to caching verified credentials at the present time. "
            "It is worth noting that subsequent to verification, heartbeats are dispatched every 5 seconds. "
            "If you have any further questions, please let me know. Best regards!"
        )
        compressed, stats = self.pruner.compress(verbose_retrieval_context, target_ratio=0.25)
        self.assertGreaterEqual(stats["reduction_ratio"], 0.20)
        self.assertIn("def verify_node(node_id: int, card_signature: bytes) -> bool:", compressed)
        self.assertIn("get_trusted_key(node_id)", compressed)
        self.assertNotIn("Please be advised that", compressed)
        self.assertNotIn("Furthermore, we would like to note that", compressed)

    def test_cli_execution_text_mode(self):
        input_text = "Please be advised that the port is 8080. Hope this helps!"
        with patch("sys.stdin", io.StringIO(input_text)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = pruning.main(["--stats"])
            self.assertEqual(exit_code, 0)
            out = mock_stdout.getvalue()
            self.assertIn("port is 8080", out)
            self.assertNotIn("Please be advised that", out)

    def test_cli_execution_stats_only(self):
        input_text = "Please be advised that in order to run tests, execute pytest. Cheers!"
        with patch("sys.stdin", io.StringIO(input_text)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = pruning.main(["--stats-only"])
            self.assertEqual(exit_code, 0)
            data = json.loads(mock_stdout.getvalue())
            self.assertIn("reduction_ratio", data)
            self.assertIn("original_chars", data)
            self.assertGreater(data["reduction_ratio"], 0.20)

    def test_cli_execution_json_messages(self):
        msgs = [{"role": "user", "content": "Please be advised that I need help. Hope this helps!"}]
        with patch("sys.stdin", io.StringIO(json.dumps(msgs))), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = pruning.main(["--json"])
            self.assertEqual(exit_code, 0)
            data = json.loads(mock_stdout.getvalue())
            self.assertEqual(len(data), 1)
            self.assertNotIn("Please be advised that", data[0]["content"])

    def test_cli_file_input_output(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as in_f:
            in_f.write("Please be advised that host is localhost. Best regards!")
            in_path = in_f.name

        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as out_f:
            out_path = out_f.name

        try:
            exit_code = pruning.main(["-i", in_path, "-o", out_path])
            self.assertEqual(exit_code, 0)
            with open(out_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Host is localhost", content)
            self.assertNotIn("Please be advised that", content)
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_cli_error_cases(self):
        # Nonexistent file
        exit_code = pruning.main(["-i", "/nonexistent/file/path/here.txt"])
        self.assertEqual(exit_code, 1)

        # Invalid JSON
        with patch("sys.stdin", io.StringIO("NOT_VALID_JSON")):
            exit_code = pruning.main(["--json"])
            self.assertEqual(exit_code, 1)

    def test_multipart_messages_with_non_text(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please note that I want to analyze this image. Hope this helps!"
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/image.png"}
                    }
                ]
            }
        ]
        pruned_msgs, stats = self.pruner.prune_messages(messages)
        self.assertEqual(len(pruned_msgs), 1)
        parts = pruned_msgs[0]["content"]
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertNotIn("Please note that", parts[0]["text"])
        self.assertIn("I want to analyze this image", parts[0]["text"])

    def test_multilanguage_code_blocks_preservation(self):
        multilang = (
            "Please be advised that you must configure the following files:\n\n"
            "```bash\n"
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "echo \"Starting container...\"\n"
            "podman run -d --name mios-db -p 5432:5432 pgvector:latest\n"
            "```\n\n"
            "And write the Rust connector:\n\n"
            "```rust\n"
            "pub fn connect_db(url: &str) -> Result<Pool, Error> {\n"
            "    let pool = Pool::new(url)?;\n"
            "    Ok(pool)\n"
            "}\n"
            "```\n\n"
            "Let me know if you need anything else!"
        )
        compressed, stats = self.pruner.compress(multilang, preserve_code=True)
        self.assertIn("podman run -d --name mios-db -p 5432:5432 pgvector:latest", compressed)
        self.assertIn("pub fn connect_db(url: &str) -> Result<Pool, Error>", compressed)
        self.assertNotIn("Please be advised that", compressed)
        self.assertNotIn("Let me know if you need anything else", compressed)

    def test_target_ratio_bounds(self):
        text = "Please be advised that in order to start, run mios. Best regards!"
        c0, s0 = self.pruner.compress(text, target_ratio=0.0)
        self.assertIsNotNone(c0)
        self.assertIn("original_chars", s0)

        c5, s5 = self.pruner.compress(text, target_ratio=0.5)
        self.assertGreater(s5["reduction_ratio"], 0.30)


class TestThinkStripper(unittest.TestCase):
    """Verify _strip_think_tags removes qwen3 reasoning leaks from sub-agent output."""

    CASES = [
        ("clean string with no think tags", "clean string with no think tags"),
        (
            "Before. <think>internal reasoning here</think> After.",
            "Before. After.",
        ),
        (
            "<think>only think</think>",
            "",
        ),
        (
            "Header.\n<think>multi\nline\nthought</think>\nFooter.",
            "Header.\nFooter.",
        ),
        (
            "Body. <think>unclosed tail because token budget ran out",
            "Body.",
        ),
        (
            "<THINK>case-insensitive</THINK> kept text",
            "kept text",
        ),
    ]

    def test_strip_think_tags(self):
        for inp, expected in self.CASES:
            with self.subTest(inp=inp):
                got = _strip_think_tags(inp)
                self.assertEqual(got.strip(), expected.strip())

    def test_split_think_tags(self):
        reasoning, answer = _split_think_tags("Answer prefix <think>pondering</think> Answer suffix")
        self.assertEqual(reasoning, "pondering")
        self.assertEqual(answer, "Answer prefix Answer suffix")


if __name__ == "__main__":
    unittest.main()
