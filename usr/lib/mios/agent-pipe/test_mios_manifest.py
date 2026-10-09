#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_manifest (WS-A1 verb-catalog -> ai/v1 manifest projection; drift-check 8 depend...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_manifest (WS-A1 verb-catalog manifest projection)."""

import copy
import os
import sys
import tempfile

import mios_manifest as mm

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _write_toml(text):
    fd, path = tempfile.mkstemp(suffix=".toml", prefix="mios_manifest_test_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path

SAMPLE_TOML = """\
[verbs.zeta_verb]
section = "Misc"
model_name = "Zeta"
sig = "zeta(x)"
desc = "the last verb alphabetically"
tier = "rare"
permission = "write"

[verbs.alpha_verb]
section = "Apps"
model_name = "Alpha"
sig = "alpha()"
desc = "first verb alphabetically"

[verbs.hidden_verb]
section = "System"
model_name = "Hidden"
hidden = true

[verbs.serialized_verb]
section = "OS"
model_name = "Serial"
conflict_group = "os-control"
parallel_limit = 3

[verbs.configurator_button]
model_name = "NotAVerb"
desc = "a UI button with no section -- must be skipped"
"""

def t_load_section_gating():
    path = _write_toml(SAMPLE_TOML)
    try:
        verbs = mm.load_verbs_from_toml(path)
    finally:
        os.unlink(path)
    check("load: returns a dict", isinstance(verbs, dict))
    check("load: includes sectioned verbs",
          set(verbs) == {"zeta_verb", "alpha_verb", "hidden_verb", "serialized_verb"},
          sorted(verbs))
    check("load: SKIPS sectionless configurator button",
          "configurator_button" not in verbs)
    check("load: preserves spec fields", verbs.get("serialized_verb", {}).get("parallel_limit") == 3)

def t_load_empty_and_no_verbs():
    p1 = _write_toml("[other]\nfoo = 1\n")
    p2 = _write_toml("[verbs.btn1]\nlabel = \"x\"\n\n[verbs.btn2]\nlabel = \"y\"\n")
    try:
        check("load: missing [verbs] -> {}", mm.load_verbs_from_toml(p1) == {})
        check("load: all-sectionless -> {}", mm.load_verbs_from_toml(p2) == {})
    finally:
        os.unlink(p1)
        os.unlink(p2)

def t_project_shape():
    path = _write_toml(SAMPLE_TOML)
    try:
        verbs = mm.load_verbs_from_toml(path)
    finally:
        os.unlink(path)
    man = mm.project_verb_catalog(verbs)
    check("project: object header", man.get("object") == "mios.verb.catalog")
    check("project: default version v1", man.get("version") == "v1")
    check("project: registry_kind verb-catalog (NOT hermes-build-tools)",
          man.get("registry_kind") == "verb-catalog")
    check("project: generated flag true", man.get("generated") is True)
    check("project: source points at mios.toml [verbs.*]",
          man.get("source") == "/usr/share/mios/mios.toml#[verbs.*]")
    check("project: count == len(data)", man.get("count") == len(man.get("data", [])))
    check("project: count == 4 sectioned verbs", man.get("count") == 4)
    man_v2 = mm.project_verb_catalog(verbs, version="v2")
    check("project: version override", man_v2.get("version") == "v2")

def t_project_ordering_determinism():
    path = _write_toml(SAMPLE_TOML)
    try:
        verbs = mm.load_verbs_from_toml(path)
    finally:
        os.unlink(path)
    man = mm.project_verb_catalog(verbs)
    names = [e["name"] for e in man["data"]]
    check("project: data sorted by name",
          names == ["alpha_verb", "hidden_verb", "serialized_verb", "zeta_verb"], names)
    import json
    a = json.dumps(mm.project_verb_catalog(verbs), sort_keys=False)
    b = json.dumps(mm.project_verb_catalog(verbs), sort_keys=False)
    check("project: re-run byte-identical", a == b)
    rev = {k: verbs[k] for k in reversed(list(verbs))}
    check("project: order-independent of input dict order",
          [e["name"] for e in mm.project_verb_catalog(rev)["data"]] == names)

def t_project_field_defaults_and_flags():
    path = _write_toml(SAMPLE_TOML)
    try:
        verbs = mm.load_verbs_from_toml(path)
    finally:
        os.unlink(path)
    man = mm.project_verb_catalog(verbs)
    by = {e["name"]: e for e in man["data"]}

    a = by["alpha_verb"]
    check("project: default tier=common", a["tier"] == "common")
    check("project: default permission=read", a["permission"] == "read")
    check("project: hidden defaults False", a["hidden"] is False)
    check("project: description from desc key", a["description"] == "first verb alphabetically")
    check("project: minimal verb omits conflict_group", "conflict_group" not in a)
    check("project: minimal verb omits parallel_limit", "parallel_limit" not in a)

    z = by["zeta_verb"]
    check("project: tier override", z["tier"] == "rare")
    check("project: permission override", z["permission"] == "write")

    h = by["hidden_verb"]
    check("project: hidden verb still projected", h is not None)
    check("project: hidden flag True", h["hidden"] is True)

    s = by["serialized_verb"]
    check("project: conflict_group projected", s.get("conflict_group") == "os-control")
    check("project: parallel_limit projected as int", s.get("parallel_limit") == 3)

    fixed = {"name", "model_name", "section", "sig", "description", "tier", "permission", "hidden"}
    check("project: every entry has fixed field subset",
          all(fixed.issubset(set(e)) for e in man["data"]))

def t_project_edge_cases():
    empty = mm.project_verb_catalog({})
    check("project: empty catalog count 0", empty["count"] == 0 and empty["data"] == [])
    none = mm.project_verb_catalog(None)
    check("project: None catalog -> count 0", none["count"] == 0 and none["data"] == [])

    bad = mm.project_verb_catalog({"good": {"section": "Misc"}, "bad": "not-a-dict"})
    check("project: non-dict spec skipped", bad["count"] == 1 and bad["data"][0]["name"] == "good")

    pl0 = mm.project_verb_catalog({"v": {"section": "Misc", "parallel_limit": 0}})
    check("project: parallel_limit 0 omitted", "parallel_limit" not in pl0["data"][0])
    pl1 = mm.project_verb_catalog({"v": {"section": "Misc", "parallel_limit": 1}})
    check("project: parallel_limit 1 projected", pl1["data"][0].get("parallel_limit") == 1)

    plbad = mm.project_verb_catalog({"v": {"section": "Misc", "parallel_limit": "lots"}})
    check("project: non-int parallel_limit omitted (no crash)", "parallel_limit" not in plbad["data"][0])

    cgws = mm.project_verb_catalog({"v": {"section": "Misc", "conflict_group": "   "}})
    check("project: whitespace conflict_group omitted", "conflict_group" not in cgws["data"][0])

    ms = mm.project_verb_catalog({"v": {"section": "Misc", "model_name": "  Padded  "}})
    check("project: model_name stripped", ms["data"][0]["model_name"] == "Padded")

def t_diff_identical():
    path = _write_toml(SAMPLE_TOML)
    try:
        verbs = mm.load_verbs_from_toml(path)
    finally:
        os.unlink(path)
    man = mm.project_verb_catalog(verbs)
    check("diff: identical -> []", mm.diff_manifest(man, man) == [])
    check("diff: re-projected -> []",
          mm.diff_manifest(mm.project_verb_catalog(verbs), copy.deepcopy(man)) == [])

def t_diff_add_remove():
    base = mm.project_verb_catalog({
        "alpha": {"section": "Misc"},
        "beta": {"section": "Misc"},
    })
    committed_missing = mm.project_verb_catalog({"alpha": {"section": "Misc"}})
    diffs = mm.diff_manifest(base, committed_missing)
    check("diff: reports added verb",
          any("+ verb 'beta'" in d and "in SSOT but not in committed" in d for d in diffs), diffs)

    committed_extra = mm.project_verb_catalog({
        "alpha": {"section": "Misc"},
        "beta": {"section": "Misc"},
        "gamma": {"section": "Misc"},
    })
    diffs2 = mm.diff_manifest(base, committed_extra)
    check("diff: reports removed verb",
          any("- verb 'gamma'" in d and "in committed manifest but not in SSOT" in d for d in diffs2), diffs2)

def t_diff_changed_fields():
    base = mm.project_verb_catalog({"v": {"section": "Misc", "permission": "read"}})

    perm = mm.project_verb_catalog({"v": {"section": "Misc", "permission": "admin"}})
    check("diff: permission change detected",
          any("~ verb 'v' changed" in d for d in mm.diff_manifest(base, perm)), )

    cg_base = mm.project_verb_catalog({"v": {"section": "Misc", "conflict_group": "g1"}})
    cg_drift = mm.project_verb_catalog({"v": {"section": "Misc", "conflict_group": "g2"}})
    check("diff: conflict_group drift detected",
          mm.diff_manifest(cg_base, cg_drift) == ["~ verb 'v' changed (regenerate the manifest)"],
          mm.diff_manifest(cg_base, cg_drift))

    pl_base = mm.project_verb_catalog({"v": {"section": "Misc", "parallel_limit": 2}})
    pl_drift = mm.project_verb_catalog({"v": {"section": "Misc", "parallel_limit": 5}})
    check("diff: parallel_limit drift detected",
          any("~ verb 'v' changed" in d for d in mm.diff_manifest(pl_base, pl_drift)))

    none_cg = mm.project_verb_catalog({"v": {"section": "Misc"}})
    add_cg = mm.project_verb_catalog({"v": {"section": "Misc", "conflict_group": "new"}})
    check("diff: newly-added conflict_group detected",
          any("~ verb 'v' changed" in d for d in mm.diff_manifest(none_cg, add_cg)))

def t_diff_registry_kind_guard():
    good = mm.project_verb_catalog({"v": {"section": "Misc"}})

    check("diff: None committed -> error",
          mm.diff_manifest(good, None) == ["committed manifest missing or unparseable"])
    check("diff: non-dict committed -> error",
          mm.diff_manifest(good, "garbage") == ["committed manifest missing or unparseable"])

    wrong_kind = copy.deepcopy(good)
    wrong_kind["registry_kind"] = "hermes-build-tools"
    diffs = mm.diff_manifest(good, wrong_kind)
    check("diff: wrong registry_kind flagged",
          "committed manifest registry_kind != 'verb-catalog'" in diffs, diffs)

    diffs_empty = mm.diff_manifest(good, {})
    check("diff: empty committed flags registry_kind",
          "committed manifest registry_kind != 'verb-catalog'" in diffs_empty)
    check("diff: empty committed reports verbs as added",
          any("+ verb 'v'" in d for d in diffs_empty), diffs_empty)

def t_diff_ignores_volatile_toplevel():
    a = mm.project_verb_catalog({"v": {"section": "Misc"}})
    b = copy.deepcopy(a)
    b["count"] = 999
    b["source"] = "somewhere/else"
    b["version"] = "v9"
    check("diff: ignores volatile top-level fields (count/source/version)",
          mm.diff_manifest(a, b) == [], mm.diff_manifest(a, b))

def main():
    t_load_section_gating()
    t_load_empty_and_no_verbs()
    t_project_shape()
    t_project_ordering_determinism()
    t_project_field_defaults_and_flags()
    t_project_edge_cases()
    t_diff_identical()
    t_diff_add_remove()
    t_diff_changed_fields()
    t_diff_registry_kind_guard()
    t_diff_ignores_volatile_toplevel()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_ai_manifest.py (task_1006)
# ==============================================================================
# AI-hint: Standalone assert-script unit test for mios_manifest (WS-A1 verb-catalog manifest projection). Pure stdlib, no server.py/DB/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_manifest (WS-A1)."""

import json
import sys

import mios_manifest as man

_fails_ai_manifest = 0

def _check_ai_manifest(name, cond, detail=""):
    global _fails_ai_manifest
    if not cond:
        _fails_ai_manifest += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

CAT = {
    "open_app": {"section": "Win", "desc": "launch", "tier": "core",
                 "permission": "write", "model_name": "launch_windows_app",
                 "conflict_group": "desktop_ui"},
    "list_windows": {"section": "Win", "desc": "list", "permission": "read"},
    "web_search": {"section": "Web", "desc": "search", "permission": "read",
                   "parallel_limit": 3},
}

def t_project():
    mani = man.project_verb_catalog(CAT)
    _check_ai_manifest("project: registry_kind = verb-catalog", mani["registry_kind"] == "verb-catalog")
    _check_ai_manifest("project: NOT hermes-build-tools", mani["registry_kind"] != "hermes-build-tools")
    _check_ai_manifest("project: generated flag", mani["generated"] is True)
    _check_ai_manifest("project: count matches", mani["count"] == 3, f"{mani['count']}")
    names = [e["name"] for e in mani["data"]]
    _check_ai_manifest("project: sorted by name (deterministic order)", names == sorted(names), f"{names}")
    om = next(e for e in mani["data"] if e["name"] == "open_app")
    _check_ai_manifest("project: carries model_name", om["model_name"] == "launch_windows_app")
    _check_ai_manifest("project: carries WS-A7 conflict_group", om.get("conflict_group") == "desktop_ui")
    ws = next(e for e in mani["data"] if e["name"] == "web_search")
    _check_ai_manifest("project: carries WS-A7 parallel_limit", ws.get("parallel_limit") == 3)
    _check_ai_manifest("project: read-default permission", next(
        e for e in mani["data"] if e["name"] == "list_windows")["permission"] == "read")

def t_deterministic():
    a = json.dumps(man.project_verb_catalog(CAT), sort_keys=True)
    b = json.dumps(man.project_verb_catalog(dict(reversed(list(CAT.items())))), sort_keys=True)
    _check_ai_manifest("deterministic: insertion-order-independent", a == b)

def t_diff():
    base = man.project_verb_catalog(CAT)
    _check_ai_manifest("diff: identical -> no diffs", man.diff_manifest(base, base) == [])
    cat2 = dict(CAT); cat2["new_verb"] = {"section": "X", "desc": "n", "permission": "read"}
    d = man.diff_manifest(man.project_verb_catalog(cat2), base)
    _check_ai_manifest("diff: detects ADDED verb", any("new_verb" in x and x.startswith("+") for x in d), f"{d}")
    d2 = man.diff_manifest(base, man.project_verb_catalog(cat2))
    _check_ai_manifest("diff: detects REMOVED verb", any("new_verb" in x and x.startswith("-") for x in d2))
    cat3 = dict(CAT); cat3["open_app"] = {**CAT["open_app"], "permission": "interactive"}
    d3 = man.diff_manifest(man.project_verb_catalog(cat3), base)
    _check_ai_manifest("diff: detects CHANGED verb", any("open_app" in x and x.startswith("~") for x in d3), f"{d3}")
    bad = {**base, "registry_kind": "hermes-build-tools"}
    _check_ai_manifest("diff: flags wrong registry_kind",
          any("registry_kind" in x for x in man.diff_manifest(base, bad)))
    _check_ai_manifest("diff: missing committed -> flagged", man.diff_manifest(base, None) != [])

def _main_ai_manifest():
    t_project()
    t_deterministic()
    t_diff()
    print(f"\n{'ok' if _fails_ai_manifest == 0 else str(_fails_ai_manifest) + ' FAILED'}")
    return 1 if _fails_ai_manifest else 0


def _run_extra_ai_manifest():
    import os
    _saved_env = dict(os.environ)
    try:
        return _main_ai_manifest()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)



def _run_all_folded_manifest_suites():
    rc = _run_extra_ai_manifest()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_manifest_suites()
    sys.exit(_rc_main)
