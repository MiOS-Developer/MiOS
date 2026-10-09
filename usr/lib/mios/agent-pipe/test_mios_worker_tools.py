#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_worker_tools (refactor R4 worker-tools reranker extraction). Pure stdlib, n...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_worker_tools (refactor R4)."""

import asyncio
import math
import sys

import mios_worker_tools as w

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _cos(a, b):
    """Real cosine over two equal-length vectors (injected as the server seam)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

_CATALOG = {
    "web_search": {"tier": "core", "permission": "read"},
    "open_app": {"tier": "common", "permission": "interactive"},
    "read_file": {"tier": "common", "permission": "read"},
    "delete_thing": {"tier": "common", "permission": "write"},
    "old_thing": {"tier": "rare", "permission": "read"},
}
_TEXT = {
    "web_search": "search the web internet query online lookup find",
    "open_app": "open launch application window program",
    "read_file": "read open file contents disk text",
    "delete_thing": "delete remove erase destroy purge",
    "old_thing": "legacy deprecated unused obsolete",
}

def _configure():
    w.configure(
        verb_catalog=_CATALOG,
        resolve_verb_key=lambda x: x,           # identity (no aliasing in the test)
        cosine=_cos,
        verb_embed_fingerprint=lambda: "fp-test-1",
        verb_embed_text=lambda k, cfg: _TEXT.get(k, ""),
        tool_rerank=True,
        rerank_fanout=3,
        rerank_min_k=24,
        rerank_rrf_k=60,
        rerank_mmr_lambda=0.8,
        rerank_skip_margin=0.08,
    )

def _tool(name):
    return {"type": "function", "function": {"name": name}}

def t_tok():
    check("tok splits snake_case + prose",
          w._tok("web_search the Internet!") == ["web", "search", "the", "internet"],
          str(w._tok("web_search the Internet!")))
    check("tok empty -> []", w._tok("") == [])

def t_bm25_lexicon():
    _configure()
    asyncio.run(w._ensure_verb_lexicon())
    lx = w._VERB_LEXICON
    check("lexicon built", isinstance(lx, dict) and lx.get("fp") == "fp-test-1")
    check("rare verb excluded from lexicon", "old_thing" not in (lx or {}).get("key2tf", {}))
    q = w._tok("search the web online")
    s_web = w._bm25(q, "web_search")
    s_del = w._bm25(q, "delete_thing")
    check("bm25 matching verb scores > 0", s_web > 0.0, str(s_web))
    check("bm25 matching > non-matching", s_web > s_del, f"web={s_web} del={s_del}")
    check("bm25 unindexed/rare verb -> 0.0", w._bm25(q, "old_thing") == 0.0)

def t_rank_positions():
    ts = [_tool("a"), _tool("b"), _tool("c")]
    rk = w._rank_positions([0.1, 0.9, 0.1], ts)
    check("rank_positions: top score rank 1", rk[1] == 1, str(rk))
    check("rank_positions: stable-name tie-break", rk[0] == 2 and rk[2] == 3, str(rk))

def t_fuse_degrade():
    _configure()
    A, B, C = _tool("a"), _tool("b"), _tool("c")
    scored = [(0.9, A, [1, 0]), (0.8, B, [0, 1]), (0.1, C, [0, 0, 1])]
    keyfn = lambda t: t["function"]["name"]
    check("fuse n>=len -> cosine slice",
          w._fuse_then_diversify(scored, [], 3, keyfn) == [A, B, C])
    w.configure(tool_rerank=False)
    check("fuse rerank-off -> cosine top-2",
          w._fuse_then_diversify(scored, w._tok("x"), 2, keyfn) == [A, B])
    w.configure(tool_rerank=True)
    conf = [(0.90, A, [1, 0]), (0.85, B, [0, 1]), (0.10, C, [0, 0, 1])]
    check("fuse confident-skip -> cosine top-2",
          w._fuse_then_diversify(conf, w._tok("search"), 2, keyfn) == [A, B])
    check("fuse n<=0 -> []", w._fuse_then_diversify(scored, [], 0, keyfn) == [])

def t_fuse_mmr():
    _configure()
    hi1 = _tool("hi1")
    hi2 = _tool("hi2")
    div = _tool("div")
    scored = [(0.90, hi1, [1.0, 0.0, 0.0]),
              (0.88, hi2, [1.0, 0.0, 0.0]),   # cosine-identical to hi1
              (0.82, div, [0.0, 1.0, 0.0])]   # gap 0.88-0.82=0.06 < 0.08 margin
    keyfn = lambda t: t["function"]["name"]
    out = w._fuse_then_diversify(scored, w._tok(""), 2, keyfn)
    check("mmr keeps highest-cosine tool", hi1 in out, str([o["function"]["name"] for o in out]))
    check("mmr promotes diverse tool over near-duplicate",
          div in out and hi2 not in out,
          str([o["function"]["name"] for o in out]))
    check("mmr returns exactly n", len(out) == 2, str(len(out)))

def t_priority():
    _configure()
    check("priority read+core -> 0", w._tool_priority(_tool("web_search")) == 0)
    check("priority read non-core -> 1", w._tool_priority(_tool("read_file")) == 1)
    check("priority read rare -> 1", w._tool_priority(_tool("old_thing")) == 1)
    check("priority action verb -> 2", w._tool_priority(_tool("open_app")) == 2)
    check("priority unknown verb -> 2", w._tool_priority(_tool("status_thing")) == 2)
    check("priority recipe -> 3", w._tool_priority(_tool("mios_recipe__reboot")) == 3)
    check("priority external -> 4", w._tool_priority(_tool("a2a_send")) == 4)
    w.configure(tool_priority_core_first=False)
    check("priority degrade: read+core -> 1 when core-first off",
          w._tool_priority(_tool("web_search")) == 1)
    check("priority degrade: action still 2",
          w._tool_priority(_tool("open_app")) == 2)
    w.configure(tool_priority_core_first=True)
    fs0 = w._priority_fallback_score(_tool("web_search"))
    fs2 = w._priority_fallback_score(_tool("open_app"))
    check("fallback score rank0 > rank2", fs0 > fs2, f"{fs0} vs {fs2}")
    check("is_core_tool core verb -> True", w._is_core_tool(_tool("web_search")) is True)
    check("is_core_tool common verb -> False", w._is_core_tool(_tool("open_app")) is False)
    check("is_core_tool recipe -> False", w._is_core_tool(_tool("mios_recipe__reboot")) is False)

def t_priority_ssot():
    """The BM25 k1/b + the priority->score map are SSOT-injected, not baked-in."""
    _configure()
    w.configure(priority_fallback_scores=[0.9, 0.8, 0.7, 0.6, 0.5])
    check("fallback score uses injected map (rank0)",
          abs(w._priority_fallback_score(_tool("web_search")) - 0.9) < 1e-9,
          str(w._priority_fallback_score(_tool("web_search"))))
    check("fallback score uses injected map (rank2)",
          abs(w._priority_fallback_score(_tool("open_app")) - 0.7) < 1e-9)
    w.configure(priority_fallback_scores=[0.42])
    check("fallback score clamps past list end",
          abs(w._priority_fallback_score(_tool("open_app")) - 0.42) < 1e-9)
    w.configure(priority_fallback_scores=[0.55, 0.45, 0.30, 0.25, 0.15])
    asyncio.run(w._ensure_verb_lexicon())
    q = w._tok("search the web online")
    w.configure(bm25_k1=1.2, bm25_b=0.75)
    s_default = w._bm25(q, "web_search")
    w.configure(bm25_b=0.0)                 # disable length normalisation
    s_nob = w._bm25(q, "web_search")
    check("bm25 uses injected b knob (score changes)", s_default != s_nob,
          f"b=0.75 -> {s_default}, b=0.0 -> {s_nob}")
    check("bm25 still positive under injected knobs", s_nob > 0.0, str(s_nob))
    w.configure(bm25_b=0.75)               # restore

def main():
    t_tok()
    t_bm25_lexicon()
    t_rank_positions()
    t_fuse_degrade()
    t_fuse_mmr()
    t_priority()
    t_priority_ssot()
    print("\n" + ("ok" if _fails == 0 else f"{_fails} FAIL"))
    sys.exit(1 if _fails else 0)



# ==============================================================================
# Consolidated from test_mios_list_dir.py (task_1006)
# ==============================================================================
# AI-hint: Sibling unit test for mios-text-edit view directory depth logic (task_113).

import os
import sys
import json
import subprocess
import tempfile
import unittest

class TestMiosListDir(unittest.TestCase):
    def setUp(self):
        self.script_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "../../../libexec/mios/mios-text-edit"
        ))
        if not os.path.exists(self.script_path):
            self.script_path = "/usr/libexec/mios/mios-text-edit"

    def test_depth_1_listing(self):
        """Verify that mios-text-edit view with --depth 1 lists only immediate children."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "file1.txt")
            with open(file1, "w") as f:
                f.write("hello")

            subdir = os.path.join(tmpdir, "subdir")
            os.makedirs(subdir)

            file2 = os.path.join(subdir, "file2.txt")
            with open(file2, "w") as f:
                f.write("world")

            cmd = [sys.executable, self.script_path, "view", tmpdir, "--depth", "1"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)

            data = json.loads(res.stdout.strip())
            self.assertTrue(data.get("ok"))
            self.assertEqual(data.get("kind"), "dir")

            entries = data.get("entries", [])
            self.assertIn("file1.txt", entries)
            self.assertIn("subdir/", entries)
            self.assertNotIn(os.path.join("subdir", "file2.txt"), entries)


def _run_extra_list_dir():
    import os
    _saved_env = dict(os.environ)
    try:
        import unittest
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestMiosListDir))
        res = unittest.TextTestRunner().run(suite)
        return 0 if res.wasSuccessful() else 1
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_worker_tools_suites():
    rc = _run_extra_list_dir()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    main()
    _run_all_folded_worker_tools_suites()