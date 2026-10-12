#!/usr/bin/env python3
# AI-hint: Standalone adversarial integration test proving the python memory CLIs (mios-kg, mios-remember) route TAINTED input (argv phr...
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Adversarial SQL-injection integration test for the WS-A3 parameterized CLIs."""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_fails = 0
EVIL = "x'); drop table alias; --"          # already lowercase (tools .lower())
EVIL2 = "global'; delete from person; --"

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _load(fname):
    loader = importlib.machinery.SourceFileLoader(
        "tool_" + fname.replace("-", "_"), os.path.join(_HERE, fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

def _all_sql(envelopes):
    out = []
    for e in envelopes:
        if "statements" in e:
            out += [s["sql"] for s in e["statements"]]
        elif "sql" in e:
            out.append(e["sql"])
    return out

def _all_params(envelopes):
    out = []
    for e in envelopes:
        if "statements" in e:
            for s in e["statements"]:
                out += (s.get("params") or [])
        else:
            out += (e.get("params") or [])
    return out

def t_kg():
    kg = _load("mios-kg")
    cap = []
    kg._pg_json = lambda env: (cap.append(env) or "[]")   # reads parse "[]" -> []
    kg.cmd_alias_rm(argparse.Namespace(phrase=EVIL))
    kg.cmd_lookup(argparse.Namespace(phrase=EVIL))
    kg.cmd_apps(argparse.Namespace(filter=EVIL))
    sqls = _all_sql(cap)
    params = _all_params(cap)
    check("kg: captured DB calls", len(cap) >= 3, str(len(cap)))
    check("kg: NO injected SQL in any statement",
          all("drop table" not in s.lower() for s in sqls))
    check("kg: payload carried as a bound param", EVIL in params)
    check("kg: statements use $-placeholders",
          any("$1" in s for s in sqls))
    check("kg: no leftover single-quote literal of the value",
          all("'" + EVIL not in s for s in sqls))

def t_remember():
    rem = _load("mios-remember")
    cap = []
    rem._pg_json = lambda env: (cap.append(env) or (True, ""))
    rem._embed_vec = lambda t: ""                         # no network
    old_argv = sys.argv
    sys.argv = ["mios-remember", "add", EVIL, "--scope", EVIL2]
    try:
        rem.main()
    finally:
        sys.argv = old_argv
    sqls = _all_sql(cap)
    params = _all_params(cap)
    check("remember: captured DB calls", len(cap) >= 1, str(len(cap)))
    check("remember: NO injected SQL in any statement",
          all("drop table" not in s.lower() and "delete from person" not in s.lower()
              for s in sqls))
    check("remember: fact payload bound as a param", EVIL in params)
    check("remember: scope payload bound as a param", EVIL2 in params)
    check("remember: INSERT/DELETE use $-placeholders",
          any("$1" in s for s in sqls))

def t_skills():
    sk = _load("mios-skills")
    cap = []
    sk._pg_exec = lambda sql, params=None: cap.append({"sql": sql, "params": params or []})
    sk._pg_rows = lambda sql, params=None: (cap.append({"sql": sql, "params": params or []}) or [])
    sk.cmd_delete(argparse.Namespace(name=EVIL))
    sk._update_status(EVIL, EVIL2)
    sqls = _all_sql(cap)
    params = _all_params(cap)
    check("skills: captured DB calls", len(cap) >= 2, str(len(cap)))
    check("skills: NO injected SQL in any statement",
          all("drop table" not in s.lower() for s in sqls))
    check("skills: name payload bound as a param", EVIL in params)
    check("skills: status payload bound as a param", EVIL2 in params)
    check("skills: statements use $-placeholders", any("$1" in s for s in sqls))

def t_sqlite_safe_ident():
    import sqlite3
    sys.path.insert(0, os.path.join(_HERE, "..", "..", "lib", "mios"))
    from mios_db_config import safe_ident

    allowlist = frozenset({"chat", "file", "knowledge", "user", "email", "role"})

    # Positive Control 1: valid table name executes cleanly
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chat (id TEXT, text TEXT);")
    valid_tbl = safe_ident("chat", allowlist)
    cur = conn.cursor()
    cur.execute(f"INSERT INTO {valid_tbl} VALUES ('1', 'hello');")
    res = cur.execute(f"SELECT COUNT(*) FROM {valid_tbl}").fetchone()[0]
    check("sqlite safe_ident: positive control valid table executes cleanly", res == 1)

    # Positive Control 2: valid column name passes validator
    valid_col = safe_ident("role", allowlist)
    check("sqlite safe_ident: valid column passes validator", valid_col == "role")

    # Negative Control 1: planted SQL injection payload raises ValueError BEFORE execute()
    evil_table = "x; DROP TABLE chat; --"
    raised_table = False
    try:
        safe_ident(evil_table, allowlist)
    except ValueError as e:
        raised_table = True
        check("sqlite safe_ident: planted table injection rejected", "Invalid SQL identifier format" in str(e))
    check("sqlite safe_ident: injection prevented before execute()", raised_table is True)

    # Negative Control 2: valid identifier format NOT in allowlist is rejected
    unauthorized_table = "unauthorized_secret_table"
    raised_unauth = False
    try:
        safe_ident(unauthorized_table, allowlist)
    except ValueError as e:
        raised_unauth = True
        check("sqlite safe_ident: unauthorized table rejected", "not in schema allowlist" in str(e))
    check("sqlite safe_ident: allowlist membership enforced", raised_unauth is True)

    # Negative Control 3: planted malformed column injection payload raises ValueError
    evil_col = "email) VALUES ('evil'); --"
    raised_col = False
    try:
        safe_ident(evil_col, allowlist)
    except ValueError as e:
        raised_col = True
        check("sqlite safe_ident: malformed column injection rejected", "Invalid SQL identifier format" in str(e))
    check("sqlite safe_ident: column injection payload rejected", raised_col is True)

def main():
    t_kg()
    t_remember()
    t_skills()
    t_sqlite_safe_ident()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
