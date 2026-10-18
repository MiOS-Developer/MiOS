#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for the v3 extended-query wire builders in mios-pg-query (WS-A3 parameterized --exec-json path). ...
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Unit tests for the mios-pg-query extended-protocol wire builders (WS-A3)."""

import importlib.machinery
import importlib.util
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader(
    "mios_pgquery_under_test", os.path.join(_HERE, "mios-pg-query"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
pg = importlib.util.module_from_spec(_spec)
_loader.exec_module(pg)   # runs defs/imports only; main() is __main__-guarded

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _declared_len_ok(msg: bytes) -> bool:
    """The Int32 after the type byte must equal len(msg) - 1 (length includes
    itself + body, excludes the 1 type byte)."""
    return len(msg) >= 5 and struct.unpack("!I", msg[1:5])[0] == len(msg) - 1

def t_sync_execute():
    check("sync: exact bytes", pg.build_sync() == b"S\x00\x00\x00\x04",
          repr(pg.build_sync()))
    ex = pg.build_execute()
    check("execute: exact bytes", ex == b"E\x00\x00\x00\x09\x00\x00\x00\x00\x00",
          repr(ex))
    check("execute: declared length", _declared_len_ok(ex))

def t_parse():
    m = pg.build_parse("SELECT 1")
    check("parse: type byte P", m[:1] == b"P")
    check("parse: declared length", _declared_len_ok(m))
    check("parse: query NUL-terminated present", b"SELECT 1\x00" in m)
    check("parse: 0 param-type OIDs (trailing 00 00)", m.endswith(b"\x00\x00"))
    check("parse: total size", len(m) == 17, str(len(m)))

def t_bind():
    m = pg.build_bind(["ab", None])
    check("bind: type byte B", m[:1] == b"B")
    check("bind: declared length", _declared_len_ok(m))
    check("bind: one text format code (00 01 00 00)", b"\x00\x01\x00\x00" in m)
    check("bind: 2 param values count", b"\x00\x02" in m)
    check("bind: text value 'ab' with length prefix",
          b"\x00\x00\x00\x02ab" in m)
    check("bind: NULL encoded as -1 length", b"\xff\xff\xff\xff" in m)
    check("bind: 0 result-format codes (trailing 00 00)", m.endswith(b"\x00\x00"))
    e = pg.build_bind([])
    check("bind: empty params declared length", _declared_len_ok(e))

def t_encode_param():
    check("encode: None -> None", pg.encode_param(None) is None)
    check("encode: True -> 'true'", pg.encode_param(True) == "true")
    check("encode: False -> 'false'", pg.encode_param(False) == "false")
    check("encode: int -> str", pg.encode_param(5) == "5")
    check("encode: float -> str", pg.encode_param(1.5) == "1.5")
    check("encode: str passthrough", pg.encode_param("x'; DROP") == "x'; DROP")
    check("encode: list -> json", pg.encode_param([1, 2]) == "[1, 2]")
    check("encode: dict -> json", pg.encode_param({"a": 1}) == '{"a": 1}')

def t_parse_envelope():
    stmts, txn = pg.parse_envelope('{"sql": "SELECT $1", "params": ["x", null, 3]}')
    check("envelope: single not txn", txn is False)
    check("envelope: single sql+encoded params",
          stmts == [("SELECT $1", ["x", None, "3"])], str(stmts))
    bstmts, btxn = pg.parse_envelope(
        '{"statements": [{"sql": "DELETE FROM t WHERE k=$1", "params": ["a"]}, '
        '{"sql": "INSERT INTO t (k) VALUES ($1)", "params": ["a"]}]}')
    check("envelope: batch is txn", btxn is True)
    check("envelope: batch two statements", len(bstmts) == 2)
    check("envelope: batch params encoded", bstmts[0] == ("DELETE FROM t WHERE k=$1", ["a"]))
    for bad in ('[]', '{}', '{"params": []}', 'not json'):
        try:
            pg.parse_envelope(bad)
            check(f"envelope: rejects {bad!r}", False)
        except (ValueError, Exception):
            check(f"envelope: rejects {bad!r}", True)

def t_no_interpolation_property():
    """The whole point: a malicious param value never changes the SQL bytes."""
    evil = "x'); DROP TABLE agent_memory; --"
    m = pg.build_bind([evil])
    sql = pg.build_parse("DELETE FROM agent_memory WHERE mem_key = $1")
    check("safety: SQL text has no param value", b"DROP TABLE" not in sql)
    check("safety: param carried only in Bind as a value",
          evil.encode() in m)
    check("safety: Bind has no SQL keywords of its own", b"DELETE" not in m)

def main():
    t_sync_execute()
    t_parse()
    t_bind()
    t_encode_param()
    t_parse_envelope()
    t_no_interpolation_property()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
