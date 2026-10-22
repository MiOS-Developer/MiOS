# AI-hint: Standalone unit test for mios-docgen (WS-4 P0 doc-gen). Pure stdlib; imports the CLI module by path (it has no .py extension, matching the libexec...
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Standalone unit test for mios-docgen (WS-4 P0 doc-gen).

Pure stdlib; imports the CLI module by path (it has no .py extension, matching
the libexec convention) and exercises the DB/binary-free logic: format
resolution, the master gate, degrade-open emission, and the routing decision
table. The two backend converters (Pandoc / LibreOffice) are NOT invoked --
that needs the binaries + a graphical-free office runtime and is covered by the
operator's live check; here we prove the pure decision layer.

Mirrors the test_mios_sched.py / test_mios_evict.py pattern: explicit asserts,
PASS/FAIL summary, non-zero exit on any failure.

Run:  python test_mios_office_convert.py
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE / "mios-docgen"

_spec = importlib.util.spec_from_loader(
    "mios_docgen", importlib.machinery.SourceFileLoader("mios_docgen", str(_SRC))
)
mdg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mdg)  # type: ignore[union-attr]

_RESULTS: list = []

def _check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _capture(fn, *a, **k) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(*a, **k)
    line = buf.getvalue().strip().splitlines()[-1] if buf.getvalue().strip() else "{}"
    try:
        return rc, json.loads(line)
    except json.JSONDecodeError:
        return rc, {}

def t_fmt_of() -> None:
    cases = {
        "/tmp/x.docx": "docx", "/tmp/x.PPTX": "pptx", "/a/b.xlsx": "xlsx",
        "/x.md": "markdown", "/x.markdown": "markdown", "/x.csv": "csv",
        "/x.pdf": "pdf", "/x.html": "html", "/x.htm": "html", "/x.txt": "plain",
        "/x.unknown": "", "/x": "",
    }
    all_ok = all(mdg._fmt_of(p) == exp for p, exp in cases.items())
    _check("fmt_of: extension -> format map", all_ok,
           detail="" if all_ok else str({p: mdg._fmt_of(p) for p in cases}))

def t_bool_int_coerce() -> None:
    _check("as_bool: truthy strings", all(mdg._as_bool(v) for v in
           ("1", "true", "TRUE", "yes", "on", True)))
    _check("as_bool: falsy strings", not any(mdg._as_bool(v) for v in
           ("0", "false", "no", "off", "", None, False)))
    _check("as_int: parse + fallback",
           mdg._as_int("42", 0) == 42 and mdg._as_int("nope", 7) == 7
           and mdg._as_int(None, 9) == 9)

def t_gate_disabled() -> None:
    """With the master gate off, convert/build degrade-open: ok=false, exit 0."""
    os.environ["MIOS_DOCGEN_ENABLE"] = "0"
    rc, obj = _capture(mdg.cmd_convert, "/tmp/in.md", "/tmp/out.docx", "")
    _check("gate off: convert exit 0", rc == 0, f"rc={rc}")
    _check("gate off: convert ok=false", obj.get("ok") is False and "disabled" in obj.get("error", ""))
    rc, obj = _capture(mdg.cmd_build, "/tmp/out.docx", "markdown", "", False)
    _check("gate off: build degrades", rc == 0 and obj.get("ok") is False)
    _check("gate off: _enabled() False", mdg._enabled() is False)

def t_gate_enabled_reads_env() -> None:
    os.environ["MIOS_DOCGEN_ENABLE"] = "1"
    _check("gate on: _enabled() True", mdg._enabled() is True)
    rc, obj = _capture(mdg.cmd_convert, "/no/such/input.md", "/tmp/o.docx", "")
    _check("gate on: missing input -> ok=false", rc == 0 and obj.get("ok") is False
           and "not found" in obj.get("error", ""))
    os.environ["MIOS_DOCGEN_ENABLE"] = "0"

def t_build_needs_source() -> None:
    os.environ["MIOS_DOCGEN_ENABLE"] = "1"
    rc, obj = _capture(mdg.cmd_build, "/tmp/o.docx", "markdown", "", False)
    _check("build: no content -> error", obj.get("ok") is False
           and "content-file" in obj.get("error", ""))
    os.environ["MIOS_DOCGEN_ENABLE"] = "0"

def t_target_tables_sane() -> None:
    _check("targets: pptx/docx/pdf in pandoc set",
           {"pptx", "docx", "pdf"} <= mdg._PANDOC_TARGETS)
    _check("targets: xlsx only via soffice (not pandoc)",
           "xlsx" in mdg._SOFFICE_TARGETS and "xlsx" not in mdg._PANDOC_TARGETS)
    _check("targets: every soffice target has a filter or is its own ext",
           all(t in mdg._SOFFICE_FILTER or t == "pdf" for t in
               ("xlsx", "docx", "pptx", "ods", "csv", "html")))

def t_formats_probe() -> None:
    rc, obj = _capture(mdg.cmd_formats)
    _check("formats: emits backend availability booleans",
           rc == 0 and "pandoc" in obj and "soffice" in obj
           and isinstance(obj.get("pandoc_targets"), list))

def t_emit_helpers() -> None:
    rc, obj = _capture(mdg._ok, output="/tmp/x.docx", target="docx")
    _check("_ok: ok=true envelope", rc == 0 and obj.get("ok") is True
           and obj.get("prog") == "mios-docgen")
    rc, obj = _capture(mdg._err, "boom", 0, target="pdf")
    _check("_err: ok=false envelope + fields", rc == 0 and obj.get("ok") is False
           and obj.get("error") == "boom" and obj.get("target") == "pdf")

def main() -> int:
    for t in (t_fmt_of, t_bool_int_coerce, t_gate_disabled, t_gate_enabled_reads_env,
              t_build_needs_source, t_target_tables_sane, t_formats_probe, t_emit_helpers):
        try:
            t()
        except Exception as e:  # a thrown test is a failure, not a crash
            _check(t.__name__, False, f"raised {type(e).__name__}: {e}")
    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} checks pass")
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
