#!/usr/bin/env python3
# AI-hint: Standalone unit test for the mios-daemon refusal/fabrication detector after the NO-HARDCODE cutover.
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Unit test: mios-daemon refusal detection is model-driven + degrade-open."""

import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_fails = 0

try:  # the judge handles any-language responses; keep stdout able to print them
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _load(fname):
    _orig_cdll = ctypes.CDLL

    def _cdll(name=None, *a, **k):
        try:
            return _orig_cdll(name, *a, **k)
        except OSError:
            return types.SimpleNamespace(
                inotify_init1=lambda *_: -1,
                inotify_add_watch=lambda *_: -1)
    ctypes.CDLL = _cdll
    try:
        loader = importlib.machinery.SourceFileLoader(
            "tool_" + fname.replace("-", "_"), os.path.join(_HERE, fname))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
    finally:
        ctypes.CDLL = _orig_cdll
    return mod

def main() -> int:
    mod = _load("mios-daemon")

    check("pattern loader deleted",
          not hasattr(mod, "_load_refusal_patterns"),
          "_load_refusal_patterns still present")
    check("compiled pattern list deleted",
          not hasattr(mod, "_refusal_res"),
          "_refusal_res still present")
    check("pattern-file constant deleted",
          not hasattr(mod, "REFUSAL_PATTERNS_FILE"),
          "REFUSAL_PATTERNS_FILE still present")
    src = open(os.path.join(_HERE, "mios-daemon"), encoding="utf-8").read()
    check("daemon no longer reads the pattern file (env hook gone)",
          "MIOS_REFUSAL_PATTERNS" not in src,
          "MIOS_REFUSAL_PATTERNS read hook still present")

    mod.REFUSAL_DETECT = "model"

    calls = {"n": 0, "last": None}

    def _stub(reply):
        def _f(system, user, **kw):
            calls["n"] += 1
            calls["last"] = (system, user)
            return reply
        return _f

    mod.llm_chat = _stub("YES")
    out = mod._classify_refusal("Sure, here you go.")  # no "cannot"/"unable"/etc.
    check("model YES on keyword-free response -> refusal True",
          out is True, repr(out))
    check("judge actually consulted (no English pre-filter gate)",
          calls["n"] >= 1)

    mod.llm_chat = _stub("YES")
    out = mod._classify_refusal("抱歉，我无法为你打开它。")
    check("unicode response judged (no ascii/keyword gate)",
          out is True, repr(out))

    mod.llm_chat = _stub("NO")
    out = mod._classify_refusal("I cannot stress this enough: it worked.")
    check("model NO -> not a refusal (False)", out is False, repr(out))

    mod.llm_chat = _stub("")
    out = mod._classify_refusal("I'm unable to find that tool.")
    check("empty model output -> degrade-open None (no keyword fallback)",
          out is None, repr(out))

    mod.llm_chat = _stub("maybe?")
    out = mod._classify_refusal("The mios-find tool appears to be unavailable.")
    check("unparseable verdict -> None", out is None, repr(out))

    calls["n"] = 0
    mod.llm_chat = _stub("YES")
    out = mod._classify_refusal("   ")
    check("empty input -> None without consulting model",
          out is None and calls["n"] == 0, f"out={out!r} calls={calls['n']}")

    mod.REFUSAL_DETECT = "off"
    calls["n"] = 0
    mod.llm_chat = _stub("YES")
    out = mod._classify_refusal("I cannot do that.")
    check("mode=off -> None without consulting model",
          out is None and calls["n"] == 0, f"out={out!r} calls={calls['n']}")

    print(f"\n{'ALL PASS' if _fails == 0 else str(_fails) + ' FAIL(S)'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
