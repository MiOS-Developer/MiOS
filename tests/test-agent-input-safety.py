#!/usr/bin/env python3
# AI-hint: Regression controls for agent process inputs and bounded input normalization; never invokes a model.
# AI-related: usr/lib/mios/agents/opencode-gateway/server.py, usr/lib/mios/agent-pipe/mios_dispatch.py, usr/lib/mios/agent-pipe/mios_ast_diff.py
"""Exercise production input handling without network or model subprocesses."""
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATEWAY = load("usr/lib/mios/agents/opencode-gateway/server.py", "gateway_input_safety")
DIFF = load("usr/lib/mios/agent-pipe/mios_ast_diff.py", "diff_input_safety")
DISPATCH_SOURCE = (ROOT / "usr/lib/mios/agent-pipe/mios_dispatch.py").read_text(encoding="utf-8")
DISPATCH_AST = ast.parse(DISPATCH_SOURCE)
NORMALIZER = next(node for node in DISPATCH_AST.body if isinstance(node, ast.FunctionDef) and node.name == "_normalize_tool_name")
namespace = {}
exec(compile(ast.Module(body=[NORMALIZER], type_ignores=[]), "dispatcher-normalizer", "exec"), namespace)
normalize = namespace["_normalize_tool_name"]


class InputSafety(unittest.TestCase):
    def test_session_paths_and_readonly_database_use_same_identity(self):
        import os
        import sqlite3
        real_connect = sqlite3.connect

        class VectorStubConnection(sqlite3.Connection):
            # Only the vector extension is stubbed; filesystem/URI/SQL are real.
            def execute(self, sql, *args):
                if sql.startswith("CREATE VIRTUAL TABLE"):
                    sql = "CREATE TABLE IF NOT EXISTS vec_scratch(content TEXT, tainted INTEGER)"
                return super().execute(sql, *args)

        with patch.dict(os.environ, {"MIOS_CONVERGE_MEMORY_SQLITE_VEC_ENABLE": "true"}), patch.dict(sys.modules, {"sqlite_vec": SimpleNamespace(load=lambda conn: None)}):
            scratch = load("usr/lib/mios/agent-pipe/mios_scratchpad.py", "scratch_input_safety")
        with tempfile.TemporaryDirectory() as tmp, patch.object(sqlite3, "connect", side_effect=lambda *a, **kw: real_connect(*a, factory=VectorStubConnection, **kw)):
            directory = Path(tmp) / "scratch # space"
            directory.mkdir()
            (directory / "mios-session-..").mkdir()  # Makes the old traversal reachable.
            paths = set()
            for session in ("normal-123", "../../../escape", "a/b", "ab", "a\\b", "?mode=rw#fragment", "用户:session", "x" * 1000):
                with self.subTest(session=session):
                    conn, path = scratch.create_scratchpad(session, str(directory))
                    try:
                        self.assertEqual(path.resolve().parent, directory.resolve())
                        self.assertNotIn(path, paths)
                        paths.add(path)
                        conn.execute("INSERT INTO vec_scratch VALUES ('content', 1)")
                        conn.commit()
                        self.assertTrue(scratch.has_tainted(session, str(directory)))
                        conn.execute("UPDATE vec_scratch SET tainted = 0")
                        conn.commit()
                        self.assertFalse(scratch.has_tainted(session, str(directory)))
                    finally:
                        scratch.destroy_scratchpad(conn, path)
                    self.assertFalse(path.exists())

    def test_runtime_session_identifiers_do_not_alias(self):
        import os
        source = ast.parse((ROOT / "usr/lib/mios/agent-pipe/mios_pipe/routing/dispatch_cmd.py").read_text(encoding="utf-8"))
        function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "_sandbox_wrap_cmd")
        scope = dict(os=os, hashlib=hashlib, uuid=uuid, Optional=object, SANDBOX_ENFORCE=False, _VERB_CATALOG={})
        # Future annotations avoid importing the full sandbox/agent runtime.
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), "runtime-session-input", "exec"), scope)
        directories = []
        with patch.dict(os.environ, {"MIOS_STORAGE_CEPHFS_ENABLE": "true"}), patch.object(subprocess, "run", side_effect=FileNotFoundError), patch.object(os, "makedirs") as mkdir, patch.object(os, "chmod"):
            for session in ("a/b", "ab", "a\\b", "用户", "x" * 1000, "x" * 999 + "y"):
                command, workspace = scope["_sandbox_wrap_cmd"]("read", "true", SimpleNamespace(confined=False), session)
                directory = mkdir.call_args.args[0]
                self.assertRegex(Path(directory).name, r"^session-[0-9a-f]{32}$")
                self.assertTrue(command.startswith("XDG_RUNTIME_DIR=" + directory + " "))
                self.assertIsNone(workspace)
                directories.append(directory)
            again, _ = scope["_sandbox_wrap_cmd"]("read", "true", SimpleNamespace(confined=False), "a/b")
            self.assertTrue(again.startswith("XDG_RUNTIME_DIR=" + directories[0] + " "))
        self.assertEqual(len(directories), len(set(directories)))

    def test_prompt_is_stdin_data(self):
        response = SimpleNamespace(stdout=json.dumps({"part": {"type": "text", "text": "answer"}}), stderr="", returncode=0)
        for prompt in ("ordinary task", "--command=touch /tmp/unwanted", "--attach=http://remote", "--auto", "-m other/model", "a\nb 'quoted'", "用户: café\n🙂"):
            with self.subTest(prompt=prompt), patch.object(GATEWAY, "OPENCODE_MODEL", "model"), patch.object(GATEWAY, "OPENCODE_PROVIDER", "local"), patch.object(GATEWAY.subprocess, "run", return_value=response) as run:
                self.assertEqual(GATEWAY._run_opencode(prompt, "local/model"), "answer")
                argv = run.call_args.args[0]
                self.assertEqual(argv, [GATEWAY.OPENCODE_BIN, "run", "--format", "json", "-m", "local/model"])
                self.assertEqual(run.call_args.kwargs["input"], prompt)
                self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
                self.assertNotIn("stdin", run.call_args.kwargs)
                self.assertNotIn("shell", run.call_args.kwargs)
                self.assertEqual(run.call_args.kwargs["env"]["OPENCODE_CONFIG"], GATEWAY.OPENCODE_CONFIG)

    def test_invalid_selector_never_launches(self):
        for model in ("-flag/model", "local/line\nbreak", "local/carriage\rreturn", "local/nul\0value"):
            with self.subTest(model=model), patch.object(GATEWAY.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "invalid opencode model selector"):
                    GATEWAY._run_opencode("task", model)
                run.assert_not_called()
        self.assertEqual(GATEWAY._selector(GATEWAY.OPENCODE_MODEL),
                         GATEWAY.OPENCODE_MODEL if "/" in GATEWAY.OPENCODE_MODEL else GATEWAY.OPENCODE_PROVIDER + "/" + GATEWAY.OPENCODE_MODEL)

    def test_unconfigured_selector_never_launches(self):
        response = SimpleNamespace(stdout='{"part":{"type":"text","text":"answer"}}', stderr="", returncode=0)
        with patch.object(GATEWAY, "OPENCODE_MODEL", "model"), patch.object(GATEWAY, "OPENCODE_PROVIDER", "local"):
            for model in ("remote/model", "local/other-model", "other-provider/other-model", "other-model"):
                with self.subTest(model=model), patch.object(GATEWAY.subprocess, "run", return_value=response) as run:
                    with self.assertRaisesRegex(ValueError, "not configured"):
                        GATEWAY._run_opencode("task", model)
                    run.assert_not_called()
            self.assertEqual(GATEWAY._selector("model"), "local/model")
            self.assertEqual(GATEWAY._selector("local/model"), "local/model")

    def test_http_rejects_unconfigured_model_before_execution(self):
        for model in ("remote/model", "local/not-configured"):
            with self.subTest(model=model), patch.object(GATEWAY, "OPENCODE_MODEL", "model"), patch.object(GATEWAY, "OPENCODE_PROVIDER", "local"), patch.object(GATEWAY, "_run_opencode", return_value="answer") as run:
                payload = json.dumps({"model": model, "messages": [{"role": "user", "content": "task"}]}).encode()
                handler = object.__new__(GATEWAY.Handler)
                handler.path = "/v1/chat/completions"
                handler.headers = {"Content-Length": str(len(payload))}
                handler.rfile = io.BytesIO(payload)
                handler._send = unittest.mock.Mock()
                handler.do_POST()
                status, body = handler._send.call_args.args
                self.assertEqual(status, 400)
                self.assertEqual(body["error"]["code"], "model_not_configured")
                run.assert_not_called()

    def test_dispatcher_compatibility(self):
        for tool in (None, "", " web_search(query) ", "`web_search`", "'tool'", "tool(a(b))", "tool(a)\n", "tool(a\nb)", "tool(a)\nsecond(b)", "tool(a) trailing", "tool(()\r\n", "tool(a)\n\n"):
            with self.subTest(tool=tool):
                legacy = re.sub(r"\(.*?\)\s*$", "", str(tool or "").strip()).strip().strip("`'\"")
                self.assertEqual(normalize(tool), legacy)
        for name in ("_dispatch_bounded", "_dispatch_mios_verb_inner_raw"):
            function = next(node for node in DISPATCH_AST.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
            self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_normalize_tool_name" for node in ast.walk(function)))

    def test_real_comments_are_cosmetic(self):
        engine = DIFF.AstDiffEngine()
        for lang, original, modified in (
            ("rust", "fn f<'a>(x: &'a str) { // before\n x; }", "fn f<'a>(x: &'a str) { /* after */\n x; }"),
            ("typescript", 'let url = "https://local/a"; // before\n', 'let url = "https://local/a"; /* after */\n'),
            ("go", 'var s = `/* literal */ // literal`; // before\n', 'var s = `/* literal */ // literal`; // after\n'),
        ):
            with self.subTest(lang=lang):
                self.assertTrue(engine.compute_ast_diff(original, modified, lang)["is_purely_cosmetic"])

    def test_comment_markers_inside_strings_remain_semantic(self):
        engine = DIFF.AstDiffEngine()
        for before, after in (('"https://local/a"', '"https://local/b"'), ('"/* before */"', '"/* after */"'), ('`// before`', '`// after`'), ('"escaped \\\" // before"', '"escaped \\\" // after"')):
            with self.subTest(before=before):
                self.assertTrue(engine.compute_ast_diff("let x = " + before + ";", "let x = " + after + ";", "typescript")["has_semantic_diff"])

    def test_adversarial_inputs_finish_in_child_deadline(self):
        # Bound the complete production calls, not merely a regex microbenchmark.
        code = ast.get_source_segment(DISPATCH_SOURCE, NORMALIZER)
        script = "import importlib.util\n" + code + "\n"
        script += "value = '(' * 200000 + 'x'\nassert _normalize_tool_name(value) == value\n"
        script += "spec = importlib.util.spec_from_file_location('bounded_diff', " + repr(str(ROOT / "usr/lib/mios/agent-pipe/mios_ast_diff.py")) + ")\n"
        script += "m = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(m)\n"
        script += "value = '/* unclosed ' * 30000\nassert m._strip_comments(value) == value\nprint('bounded input controls executed')\n"
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5, check=True)
        self.assertIn("bounded input controls executed", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
