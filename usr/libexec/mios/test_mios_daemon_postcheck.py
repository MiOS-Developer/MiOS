#!/usr/bin/env python3
# AI-hint: Standalone unit test for the mios-daemon per-verb post-check after the NO-HARDCODE cutover.
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Unit test: mios-daemon per-verb post-check is SSOT-driven + degrade-open."""

import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_TOML = os.path.normpath(
    os.path.join(_HERE, "..", "..", "share", "mios", "mios.toml"))
_fails = 0

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

def _write_toml(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path

def main() -> int:
    mod = _load("mios-daemon")

    src = open(os.path.join(_HERE, "mios-daemon"), encoding="utf-8").read()
    check("no baked window-verb tuple gating the check",
          '("open_app", "open_url", "focus_window", "launch_app")' not in src,
          "baked verb tuple still present in _verb_post_check")

    if os.path.exists(_REPO_TOML):
        os.environ["MIOS_TOML"] = _REPO_TOML
        ssot = mod._read_verb_post_check_map()
        for verb, sig in (("open_app", "window_visible"),
                          ("open_url", "window_visible"),
                          ("focus_window", "window_visible"),
                          ("launch_app", "window_visible"),
                          ("text_create", "file_exists"),
                          ("text_str_replace", "file_nonempty"),
                          ("flatpak_install", "flatpak_installed")):
            check(f"SSOT maps {verb} -> {sig}",
                  ssot.get(verb) == sig, f"got {ssot.get(verb)!r}")
    else:
        check("repo mios.toml present for SSOT read", False, _REPO_TOML)

    custom = _write_toml(
        "[daemon.post_check]\n"
        'myverb = "file_exists"\n')
    os.environ["MIOS_TOML"] = custom
    mod._VERB_POST_CHECK = mod._read_verb_post_check_map()
    try:
        check("non-default verb now mapped",
              mod._VERB_POST_CHECK.get("myverb") == "file_exists",
              repr(mod._VERB_POST_CHECK))
        check("verb dropped from SSOT -> checked=False",
              mod._verb_post_check("open_app", {"name": "Firefox"}).get("checked") is False)

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"x")
            existing = tf.name
        try:
            r_ok = mod._verb_post_check("myverb", {"path": existing})
            check("SSOT-mapped verb runs file_exists (pass)",
                  r_ok.get("checked") and r_ok.get("passed")
                  and r_ok.get("signal") == "file_exists", repr(r_ok))
            missing = existing + ".gone"
            r_no = mod._verb_post_check("myverb", {"path": missing})
            check("SSOT-mapped verb runs file_exists (fail on missing)",
                  r_no.get("checked") and not r_no.get("passed"), repr(r_no))
        finally:
            os.unlink(existing)
    finally:
        os.unlink(custom)

    mod._VERB_POST_CHECK = {"text_str_replace": "file_nonempty"}
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        empty = tf.name  # zero bytes
    try:
        r_empty = mod._verb_post_check("text_str_replace", {"path": empty})
        check("file_nonempty fails an empty file",
              r_empty.get("checked") and not r_empty.get("passed")
              and r_empty.get("signal") == "file_nonempty", repr(r_empty))
    finally:
        os.unlink(empty)

    mod._VERB_POST_CHECK = {"text_create": "no_such_signal"}
    check("unknown signal -> checked=False",
          mod._verb_post_check("text_create", {"path": "/x"}).get("checked") is False)

    mod._VERB_POST_CHECK = {}
    check("empty SSOT map -> all verbs checked=False",
          mod._verb_post_check("text_create", {"path": "/x"}).get("checked") is False)

    print(f"\n{'ALL PASS' if _fails == 0 else str(_fails) + ' FAIL(S)'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
