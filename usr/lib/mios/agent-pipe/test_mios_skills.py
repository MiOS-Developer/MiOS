#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_skills (refactor R7 SKILLS-cluster extraction). Pure stdlib, no server.py/DB/network/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_skills (refactor R7)."""

import asyncio
import sys

import mios_skills as s

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_make_schema_strict():
    strict = s._make_schema_strict({
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
        },
        "required": ["a"],
    })
    check("strict additionalProperties False",
          strict.get("additionalProperties") is False)
    check("strict all props required",
          set(strict.get("required") or []) == {"a", "b"},
          str(strict.get("required")))
    check("strict optional prop nullable",
          strict["properties"]["b"]["type"] == ["integer", "null"],
          str(strict["properties"]["b"]))
    deg = s._make_schema_strict("nope")
    check("strict non-dict degrade",
          deg == {"type": "object", "properties": {},
                  "required": [], "additionalProperties": False})

def t_skill_to_openai_tool():
    tool = s._skill_to_openai_tool({
        "name": "my skill!",
        "description": "does a thing",
        "body": {"params": ["url", "title"]},
    })
    check("tool type function", tool.get("type") == "function")
    fn = tool.get("function") or {}
    check("tool name sanitized + prefixed",
          fn.get("name") == "mios_skill__my_skill_", fn.get("name"))
    check("tool strict True", fn.get("strict") is True)
    params = fn.get("parameters") or {}
    check("tool required == params",
          params.get("required") == ["url", "title"])
    check("tool additionalProperties False",
          params.get("additionalProperties") is False)
    tool_rich = s._skill_to_openai_tool({
        "name": "rich skill",
        "description": "does a rich thing",
        "body": {
            "params": {
                "url": {
                    "type": "string",
                    "description": "URL to open"
                },
                "disposition": {
                    "type": "string",
                    "enum": ["tab", "window"],
                    "description": "where to open"
                }
            }
        }
    })
    check("rich: tool type function", tool_rich.get("type") == "function")
    fn_rich = tool_rich.get("function") or {}
    check("rich: tool name", fn_rich.get("name") == "mios_skill__rich_skill")
    check("rich: tool strict True", fn_rich.get("strict") is True)
    params_rich = fn_rich.get("parameters") or {}
    check("rich: tool required matches keys", params_rich.get("required") == ["url", "disposition"])
    check("rich: tool additionalProperties False", params_rich.get("additionalProperties") is False)
    props_rich = params_rich.get("properties") or {}
    check("rich: url type", props_rich.get("url", {}).get("type") == "string")
    check("rich: url desc", props_rich.get("url", {}).get("description") == "URL to open")
    check("rich: disposition type", props_rich.get("disposition", {}).get("type") == "string")
    check("rich: disposition enum", props_rich.get("disposition", {}).get("enum") == ["tab", "window"])

def t_execute_skill():
    ROW = {"id": "skill:abc123", "name": "demo", "status": "promoted",
           "body": {"steps": [{"verb": "noop", "args": {}}]}}
    calls = {"dispatched": []}

    async def stub_db_read(*a, **k):
        return [{"result": [ROW]}]

    async def stub_dispatch(verb, args, *, session_id=None):
        calls["dispatched"].append((verb, args))
        return {"success": True, "exit_code": 0, "output": "ok", "stderr": ""}

    async def stub_db_post(*a, **k):
        return []

    async def stub_db_update(*a, **k):
        return None

    def stub_db_write(*a, **k):
        return None

    def stub_pg_mirror(*a, **k):
        return None

    s.configure(
        db_read=stub_db_read,
        db_post=stub_db_post,
        db_update=stub_db_update,
        db_write=stub_db_write,
        pg_mirror=stub_pg_mirror,
        dispatch_verb=stub_dispatch,
        skills_enabled=True,
    )

    out = asyncio.run(s.execute_skill("demo", {}, session_id=None))
    check("execute_skill success", out.get("success") is True, str(out))
    check("execute_skill ran the verb",
          calls["dispatched"] == [("noop", {})], str(calls["dispatched"]))
    check("execute_skill one step recorded",
          len(out.get("steps") or []) == 1, str(out.get("steps")))

def t_skill_render_args():
    out = s._skill_render_args(
        {"url": "$site/page", "n": 5, "who": "$user"},
        {"site": "http://x", "user": "alice"})
    check("render substitutes tokens", out["url"] == "http://x/page", str(out))
    check("render leaves non-str untouched", out["n"] == 5, str(out))
    check("render second token", out["who"] == "alice", str(out))
    miss = s._skill_render_args({"a": "$gone"}, {})
    check("render missing param literal", miss["a"] == "$gone", str(miss))

def t_skill_invocation_lifecycle():
    posts = []
    mirrors = []

    async def stub_db_post(sql, *a, **k):
        posts.append(sql)
        return []  # pg-primary short-circuit shape -> open synthesizes an id

    def stub_pg_mirror(table, fields):
        mirrors.append((table, fields))

    s.configure(db_post=stub_db_post, pg_mirror=stub_pg_mirror)
    inv = asyncio.run(s._skill_invocation_open("skill:abc", {"x": 1}, None))
    check("open synthesizes inv id",
          isinstance(inv, str) and inv.startswith("skill_invocation:pg-"), str(inv))
    check("open records carry meta",
          s._SKILL_INV_META.get(inv) == {"skill": "skill:abc", "session": None},
          str(s._SKILL_INV_META.get(inv)))
    check("open issued a CREATE",
          any("CREATE skill_invocation" in p for p in posts), str(posts))
    asyncio.run(s._skill_invocation_close(inv, True))
    check("close mirrors outcome to pg",
          bool(mirrors) and mirrors[-1][0] == "skill_invocation"
          and mirrors[-1][1].get("success") is True, str(mirrors))
    check("close pops carry meta", inv not in s._SKILL_INV_META,
          str(s._SKILL_INV_META))
    check("close issued an UPDATE",
          any(p.startswith(f"UPDATE {inv}") for p in posts), str(posts))

class _PGStub:
    """Records every (sql, params, fetch) the module sends to postgres and
    replays a canned RETURNING row for the invocation INSERT."""

    def __init__(self, insert_id=None):
        self.calls = []
        self.insert_id = insert_id

    def rid_to_pg_id(self, rid):
        import mios_pg
        return mios_pg.rid_to_pg_id(rid)

    async def execute(self, sql, params=None, *, fetch=False, **kw):
        self.calls.append((" ".join(sql.split()), params or {}, fetch))
        if fetch and "RETURNING id" in sql:
            return [{"id": self.insert_id}] if self.insert_id is not None else []
        return None

def _with_pg(stub):
    """Swap the module's mios_pg seam for the stub; returns the original."""
    prev = s._mios_pg
    s._mios_pg = stub
    return prev

def t_pg_invocation_lifecycle():
    # Under pg-primary the legacy CREATE returns nothing, so open must fall
    # through to a REAL postgres row rather than the synthetic uuid handle.
    async def dead_post(sql, *a, **k):
        return None

    stub = _PGStub(insert_id=77)
    prev = _with_pg(stub)
    try:
        s.configure(db_post=dead_post)
        inv = asyncio.run(s._skill_invocation_open("skill:abc", {"a": 1}, "sess:1"))
        check("open: handle names the pg row", inv == "skill_invocation:pg#77", str(inv))
        ins = [c for c in stub.calls if "INSERT INTO skill_invocation" in c[0]]
        check("open: one INSERT with RETURNING id", len(ins) == 1)
        check("open: started_at and params are persisted",
              ins and "started_at, params" in ins[0][0]
              and ins[0][1].get("params") == '{"a": 1}', str(ins[:1]))

        stub.calls.clear()
        asyncio.run(s._skill_invocation_close(inv, True))
        upd = [c for c in stub.calls if c[0].startswith("UPDATE skill_invocation")]
        check("close: updates the SAME row", len(upd) == 1
              and upd[0][1] == {"ok": True, "id": 77}, str(upd))
        check("close: does not INSERT a duplicate",
              not any("INSERT INTO skill_invocation" in c[0] for c in stub.calls))

        stub.calls.clear()
        asyncio.run(s._skill_attribute_tool_call(inv, "tool_call:9", 3))
        edge = [c for c in stub.calls if "skill_tool_call" in c[0]]
        check("attribute: the edge is persisted", len(edge) == 1)
        check("attribute: edge carries invocation, tool_call and step",
              edge and edge[0][1] == {"inv": 77, "tc": "tool_call:9", "step": 3},
              str(edge))
        check("attribute: a retried step upserts rather than erroring",
              edge and "ON CONFLICT (invocation_id, tool_call_id)" in edge[0][0])
    finally:
        _with_pg(prev)

def t_pg_absent_falls_back():
    async def dead_post(sql, *a, **k):
        return None

    stub = _PGStub(insert_id=None)   # postgres reachable but returns nothing
    prev = _with_pg(stub)
    try:
        s.configure(db_post=dead_post)
        inv = asyncio.run(s._skill_invocation_open("skill:abc", {}, None))
        check("open: no pg row -> synthetic handle still returned",
              isinstance(inv, str) and inv.startswith("skill_invocation:pg-"), str(inv))
        check("open: synthetic handle has no pg row id",
              s._pg_row_id(inv) is None)

        mirrored = []
        s.configure(pg_mirror=lambda t, r: mirrored.append((t, r)))
        asyncio.run(s._skill_invocation_close(inv, False))
        check("close: synthetic handle still mirrors the outcome",
              len(mirrored) == 1 and mirrored[0][0] == "skill_invocation", str(mirrored))
    finally:
        _with_pg(prev)

def t_pg_row_id_parsing():
    check("row id: legacy handle -> None", s._pg_row_id("skill_invocation:abc") is None)
    check("row id: synthetic handle -> None", s._pg_row_id("skill_invocation:pg-deadbeef") is None)
    check("row id: pg handle -> int", s._pg_row_id("skill_invocation:pg#42") == 42)
    check("row id: non-string -> None", s._pg_row_id(None) is None)
    check("row id: unparseable suffix -> None", s._pg_row_id("skill_invocation:pg#x") is None)

def t_skill_attribute_tool_call():
    posts = []

    async def stub_db_post(sql, *a, **k):
        posts.append(sql)
        return []

    s.configure(db_post=stub_db_post)
    asyncio.run(s._skill_attribute_tool_call(None, "tc:1", 0))
    asyncio.run(s._skill_attribute_tool_call("inv:1", None, 0))
    check("attribute no-op on missing ids", posts == [], str(posts))
    asyncio.run(s._skill_attribute_tool_call("inv:1", "tc:1", 3))
    check("attribute emits RELATE",
          any("RELATE inv:1->emitted->tc:1" in p and "step_index = 3" in p
              for p in posts), str(posts))

def t_slug_for_skill():
    check("slug lowercases + hyphenates",
          s._slug_for_skill("  Zxq!! Vwk__Mtp  ") == "zxq-vwk-mtp",
          s._slug_for_skill("  Zxq!! Vwk__Mtp  "))
    long = "q" * 200
    check("slug length-capped to 60", len(s._slug_for_skill(long)) == 60)
    check("slug empty -> fallback", s._slug_for_skill("") == "skill")
    check("slug all-symbols -> fallback", s._slug_for_skill("@#$%") == "skill")

def t_render_skill_md():
    md = s._render_skill_md(
        "qwzx vptm", "mtpq result body",
        [{"tool": "verb_zz", "args": {"k": "v"}}], "sess:9")
    check("render frontmatter fence", md.startswith("---\n"))
    check("render carries goal", "qwzx vptm" in md)
    check("render carries outcome", "mtpq result body" in md)
    check("render lists the verb in frontmatter", "verb_zz" in md)
    check("render stamps the session", "session: sess:9" in md)
    md2 = s._render_skill_md("qwzx", "ans", None, None)
    check("render no-tools note",
          "answer produced without explicit tool calls" in md2)

def t_write_skill_md_fire(tmp_subdir):
    import os
    s.configure(skills_episodic_dir=os.path.join(tmp_subdir, "off"),
                skills_episodic_enabled=False)
    s._write_skill_md_fire(query="qwzx", answer="mtpq")
    check("write disabled -> no dir created",
          not os.path.isdir(os.path.join(tmp_subdir, "off")))
    on_dir = os.path.join(tmp_subdir, "on")
    s.configure(skills_episodic_dir=on_dir, skills_episodic_enabled=True)
    s._write_skill_md_fire(query="", answer="mtpq")
    check("write empty-query -> skip",
          not os.path.isdir(on_dir) or not os.listdir(on_dir))
    s._write_skill_md_fire(query="qwzx vptm", answer="mtpq result",
                           tool_history=[{"tool": "verb_zz", "args": {}}],
                           session_id="sess:1")
    files = [f for f in os.listdir(on_dir) if f.endswith(".md")]
    check("write produced one .md", len(files) == 1, str(files))
    body = open(os.path.join(on_dir, files[0]), encoding="utf-8").read()
    check("written md carries goal + verb",
          "qwzx vptm" in body and "verb_zz" in body)

def main():
    import tempfile
    # The unit fixture must not acquire an invocation row in an ambient DB.
    # PG lifecycle cases below swap in their own explicit returning-row stub.
    _with_pg(_PGStub())
    t_make_schema_strict()
    t_skill_to_openai_tool()
    t_execute_skill()
    t_skill_render_args()
    t_skill_invocation_lifecycle()
    t_skill_attribute_tool_call()
    t_pg_invocation_lifecycle()
    t_pg_absent_falls_back()
    t_pg_row_id_parsing()
    t_slug_for_skill()
    t_render_skill_md()
    with tempfile.TemporaryDirectory() as td:
        t_write_skill_md_fire(td)
    print(f"\n{'OK' if _fails == 0 else 'FAIL'}: {_fails} failure(s)")
    sys.exit(1 if _fails else 0)

if __name__ == "__main__":
    main()
