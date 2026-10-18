#!/usr/bin/env python3
# AI-hint: Standalone unit test for the mios-daemon launch-claim detector after the NO-HARDCODE cutover.
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Unit test: mios-daemon launch-claim detection is model-driven + degrade-open."""

import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_fails = 0

try:  # the detector handles any-language targets; keep stdout able to print them
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

    check("keyword regex list deleted",
          not hasattr(mod, "_LAUNCH_CLAIM_RES"),
          "_LAUNCH_CLAIM_RES still present")
    src = open(os.path.join(_HERE, "mios-daemon"), encoding="utf-8").read()
    check("no baked app-name allowlist gating detection",
          "Steam|Epic|Ubisoft|Uplay" not in src,
          "Steam|Epic|Ubisoft|Uplay literal still present")

    mod.LAUNCH_CLAIM_DETECT = "model"

    calls = {"n": 0, "last": None}

    def _stub(reply):
        def _f(system, user, **kw):
            calls["n"] += 1
            calls["last"] = (system, user)
            return reply
        return _f

    mod.llm_chat = _stub('{"claim": true, "target": "Cyberpunk 2077"}')
    out = mod._classify_launch_claim("Cyberpunk 2077 is up and running for you.")
    check("model claim -> generic target",
          isinstance(out, dict) and out.get("app") == "Cyberpunk 2077", repr(out))
    check("model was actually consulted", calls["n"] >= 1)

    mod.llm_chat = _stub('{"claim": true, "target": "电子表格"}')
    out = mod._classify_launch_claim("已为你打开 电子表格 。")
    check("unicode target resolves (no ascii/keyword gate)",
          isinstance(out, dict) and out.get("app") == "电子表格", repr(out))

    mod.llm_chat = _stub('{"claim": false, "target": ""}')
    out = mod._classify_launch_claim("I can open Steam for you if you want.")
    check("model not-a-claim -> {}", out == {}, repr(out))

    mod.llm_chat = _stub("")
    out = mod._classify_launch_claim("Steam has been successfully launched.")
    check("empty model output -> degrade-open None (no keyword fabrication)",
          out is None, repr(out))

    mod.llm_chat = _stub("not json at all")
    out = mod._classify_launch_claim("Steam is now open with the install.")
    check("unparseable model output -> None", out is None, repr(out))

    mod.LAUNCH_CLAIM_DETECT = "off"
    calls["n"] = 0
    mod.llm_chat = _stub('{"claim": true, "target": "Steam"}')
    out = mod._classify_launch_claim("Steam is now open.")
    check("mode=off -> None without consulting model",
          out is None and calls["n"] == 0, f"out={out!r} calls={calls['n']}")

    mod.LAUNCH_CLAIM_DETECT = "model"
    mod.llm_chat = _stub('{"claim": true, "target": ""}')
    out = mod._classify_launch_claim("Launched it.")
    check("empty model target -> {} (no list back-fill)", out == {}, repr(out))

    print(f"\n{'ALL PASS' if _fails == 0 else str(_fails) + ' FAIL(S)'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
