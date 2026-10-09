# AI-hint: stdlib unit test for mios_template.
# AI-related: usr/lib/mios/agent-pipe/test_mios_template.py, usr/lib/mios/agent-pipe/mios_template.py
# AI-functions: TestMiosTemplate, main
"""Unit tests for mios_template command rendering."""

import unittest
import os
from unittest import mock

from mios_template import _template_to_cmd

class TestMiosTemplate(unittest.TestCase):

    def test_basic_substitution(self):
        res = _template_to_cmd("test_tool", "echo {message}", {"message": "hello world"})
        self.assertEqual(res, "echo 'hello world'")

    def test_required_or_abort(self):
        res = _template_to_cmd("test_tool", "echo {message!}", {"message": "hello"})
        self.assertEqual(res, "echo hello")

        res = _template_to_cmd("test_tool", "echo {message!}", {"message": ""})
        self.assertIsNone(res)

        res = _template_to_cmd("test_tool", "echo {message!}", {})
        self.assertIsNone(res)

    def test_default_values(self):
        res = _template_to_cmd("test_tool", "echo {message=fallback}", {})
        self.assertEqual(res, "echo fallback")

        res = _template_to_cmd("test_tool", "echo {message=fallback}", {"message": "hello"})
        self.assertEqual(res, "echo hello")

    @mock.patch.dict(os.environ, {"MIOS_TEST_ENV": "env_val"})
    def test_env_defaults(self):
        res = _template_to_cmd("test_tool", "echo {message=$MIOS_TEST_ENV:fallback}", {})
        self.assertEqual(res, "echo env_val")

        res = _template_to_cmd("test_tool", "echo {message=$MIOS_NONEXISTENT_ENV:fallback}", {})
        self.assertEqual(res, "echo fallback")

    def test_optional_flags(self):
        res = _template_to_cmd("test_tool", "cmd{arg?-f}", {"arg": "value"})
        self.assertEqual(res, "cmd -f value")

        res = _template_to_cmd("test_tool", "cmd{arg?-f}", {})
        self.assertEqual(res, "cmd")

        res = _template_to_cmd("test_tool", "cmd{arg?}", {"arg": "value"})
        self.assertEqual(res, "cmd value")

    def test_splat_varargs(self):
        res = _template_to_cmd("test_tool", "cmd{args*}", {"args": ["a", "b", "c"]})
        self.assertEqual(res, "cmd a b c")

        res = _template_to_cmd("test_tool", "cmd{args*}", {"args": "single"})
        self.assertEqual(res, "cmd single")

        res = _template_to_cmd("test_tool", "cmd{args*}", {})
        self.assertEqual(res, "cmd")

    def test_list_flattening(self):
        res = _template_to_cmd("test_tool", "cmd --files {files}", {"files": ["file1.txt", "file2.txt"]})
        self.assertEqual(res, "cmd --files file1.txt file2.txt")

    def test_compiled_template(self):
        from mios_template import compile_template, CompiledTemplate
        tpl_str = "cmd {req!} {opt?-f} {dflt=val} {splat*}"
        ct1 = compile_template(tpl_str)
        ct2 = compile_template(tpl_str)

        self.assertIsInstance(ct1, CompiledTemplate)
        self.assertIs(ct1, ct2)  # Identical cached object
        self.assertEqual(ct1.placeholder_names, {"req", "opt", "dflt", "splat"})

        args = {"req": "r_val", "opt": "o_val", "splat": ["s1", "s2"]}
        rendered_ct = ct1.render("test_tool", args)
        rendered_legacy = _template_to_cmd("test_tool", tpl_str, args)
        self.assertEqual(rendered_ct, rendered_legacy)

    def test_adversarial_quoting(self):
        import shlex
        bad_inputs = ["$(id)", ";rm -rf /", "`whoami`", "foo bar", "line1\nline2"]
        for bad in bad_inputs:
            res = _template_to_cmd("test_tool", "echo {arg}", {"arg": bad})
            self.assertIsNotNone(res)
            tokens = shlex.split(res)
            self.assertEqual(tokens[0], "echo")
            self.assertEqual(tokens[1], bad)

    def test_env_default_isolation(self):
        res = _template_to_cmd("test_tool", "echo {arg=$PATH:/bin}", {"arg": "$HOME"})
        self.assertEqual(res, "echo '$HOME'")



# ==============================================================================
# Consolidated from test_mios_run_template.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for mios_pipe.routing.run_template -- the WS-6 capture half plus the task_205 replay re...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

"""Unit tests for run-template capture + the replay read side (WS-6 / task_205)."""

import asyncio
import sys

from mios_pipe.routing import replay as R
from mios_pipe.routing import run_template as RT

_fails_run_template = 0

def _check_run_template(name, cond, detail=""):
    global _fails_run_template
    if not cond:
        _fails_run_template += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

A = "search the web for the latest linux kernel CVEs and summarise the top three"

def _wire(rows, *, enable=True, db_read=None):
    RT.configure(run_template_enable=enable, pg_primary=True,
                 pg_mirror=lambda t, r: rows.append(r),
                 db_create=lambda *a, **k: "x", db_post=lambda s: s,
                 db_fire=lambda x: None, db_read=db_read or (lambda *a, **k: None))

def t_class():
    a = {"nodes": [{"id": 1, "tool": "web_search"}, {"id": 2, "tool": "summarize", "deps": [1]}]}
    b = {"nodes": [{"id": 9, "tool": "summarize", "deps": [8]}, {"id": 8, "tool": "web_search"}]}
    _check_run_template("class: same shape, different ids/order -> same class",
          RT._run_template_class(a) == RT._run_template_class(b))
    c = {"nodes": [{"id": 1, "tool": "web_search"}, {"id": 2, "tool": "summarize"}]}
    _check_run_template("class: a different EDGE count -> a different class",
          RT._run_template_class(a) != RT._run_template_class(c))
    d = {"nodes": [{"id": 1, "tool": "web_search"}, {"id": 2, "tool": "open_url", "deps": [1]}]}
    _check_run_template("class: different TOOLS -> a different class",
          RT._run_template_class(a) != RT._run_template_class(d))
    _check_run_template("class: an empty DAG still classes without raising",
          isinstance(RT._run_template_class({}), str))

def t_capture():
    rows = []
    _wire(rows)
    RT._capture_run_template(
        {"summary": "s", "intent": A, "nodes": [{"id": 1, "tool": "web_search"}]}, "sess1")
    _check_run_template("capture: one row is written", len(rows) == 1, str(len(rows)))
    row = rows[0] if rows else {}
    _check_run_template("capture: the row carries a NON-EMPTY intent key",
          bool(row.get("intent_key")), str(row.get("intent_key")))
    _check_run_template("capture: the row carries the turn itself", row.get("intent") == A)
    _check_run_template("capture: the row carries the session", row.get("session_id") == "sess1")
    _check_run_template("capture: the captured row is matchable by ITS OWN turn",
          R.match_template(A, [dict(row)], 0.85)[1] == 1.0)

    rows.clear()
    RT._capture_run_template({"summary": "s", "nodes": []}, "sess1")
    _check_run_template("capture: an empty DAG writes nothing", rows == [])

    rows.clear()
    RT._capture_run_template({"summary": "s", "nodes": [{"id": 1, "tool": "x"}]}, None)
    _check_run_template("capture: a DAG with no intent still stores, with an empty key",
          len(rows) == 1 and rows[0].get("intent_key") == "", str(rows[:1]))

def t_capture_disabled():
    rows = []
    _wire(rows, enable=False)
    RT._capture_run_template(
        {"summary": "s", "intent": A, "nodes": [{"id": 1, "tool": "web_search"}]}, "s")
    _check_run_template("capture: the disabled flag writes nothing", rows == [])
    _wire([], enable=True)

def t_load():
    seen = {}

    async def _read(sql, pg_sql=None):
        seen["sql"] = pg_sql or sql
        return [{"result": [{"intent": A, "intent_key": R.intent_key(A),
                             "dag": {"nodes": [{"id": 1}]}}]}]

    _wire([], db_read=_read)
    out = asyncio.run(RT.load_run_templates(7))
    _check_run_template("load: returns the stored rows", len(out) == 1, str(out))
    _check_run_template("load: filters to rows that actually carry a key",
          "intent_key IS NOT NULL" in seen.get("sql", ""), seen.get("sql", ""))
    _check_run_template("load: newest first", "ORDER BY ts DESC" in seen.get("sql", ""))
    _check_run_template("load: the statement is a CONSTANT -- no caller value reaches the SQL",
          seen.get("sql", "").rstrip(";") == RT._SQL_LOAD, seen.get("sql", ""))
    _check_run_template("load: the read is capped in the statement itself",
          f"LIMIT {RT._MAX_ROWS}" in seen.get("sql", ""), seen.get("sql", ""))

    async def _many(sql, pg_sql=None):
        seen["sql"] = pg_sql or sql
        return [{"result": [{"intent": f"turn {i}", "intent_key": R.intent_key(f"turn {i}"),
                             "dag": {"nodes": [{"id": 1}]}} for i in range(40)]}]

    _wire([], db_read=_many)
    _check_run_template("load: the caller's limit slices the RESULT",
          len(asyncio.run(RT.load_run_templates(7))) == 7)
    _check_run_template("load: a zero/absent limit falls back to a sane one, never zero rows",
          len(asyncio.run(RT.load_run_templates(0))) == 40)
    _wire([], db_read=_read)

    async def _boom(sql, pg_sql=None):
        raise RuntimeError("db down")

    _wire([], db_read=_boom)
    _check_run_template("load: a read failure degrades OPEN (planning proceeds)",
          asyncio.run(RT.load_run_templates(5)) == [])

    _wire([], enable=False, db_read=_read)
    _check_run_template("load: the disabled flag reads nothing",
          asyncio.run(RT.load_run_templates(5)) == [])
    _wire([], enable=True)

def _main_run_template():
    t_class()
    t_capture()
    t_capture_disabled()
    t_load()
    print(f"\n{'ok' if _fails_run_template == 0 else str(_fails_run_template) + ' FAILED'}")
    return 1 if _fails_run_template else 0


def _run_extra_run_template():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_run_template()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)

class TestFolded_run_template(unittest.TestCase):
    def test_run_folded(self):
        rc = _run_extra_run_template()
        self.assertIn(rc, (None, 0))



def _run_all_folded_template_suites():
    rc = _run_extra_run_template()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    unittest.main()
