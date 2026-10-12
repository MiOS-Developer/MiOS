#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_cua (WS-8 perceive->act->verify computer-use loop core). Pure stdlib, no server.py/VLM/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_cua (WS-8)."""
import asyncio
import json
import sys

import mios_cua as cua

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_resolve_verb():
    check("resolve: click on windows", cua.resolve_verb("click", "windows") == "windows_desktop_click")
    check("resolve: click on linux", cua.resolve_verb("click", "linux") == "linux_desktop_click")
    check("resolve: screenshot symmetric",
          cua.resolve_verb("screenshot", "windows") == "windows_desktop_screenshot"
          and cua.resolve_verb("screenshot", "linux") == "linux_desktop_screenshot")
    check("resolve: type/key/find map", all([
        cua.resolve_verb("type", "windows") == "windows_desktop_type_text",
        cua.resolve_verb("key", "linux") == "linux_desktop_press_key",
        cua.resolve_verb("find_element", "windows") == "windows_desktop_find_element_by_name",
    ]))
    check("resolve: unknown action -> None", cua.resolve_verb("teleport", "windows") is None)
    check("resolve: unknown platform -> None", cua.resolve_verb("click", "macos") is None)
    check("resolve: blank -> None", cua.resolve_verb("", "") is None)
    for a in cua._ACTION_VERB:
        check(f"resolve: {a} defined for both platforms",
              cua.resolve_verb(a, "windows") and cua.resolve_verb(a, "linux"))

def t_observation():
    check("obs: same text -> no change", cua.observation_changed("screen A", "screen A") is False)
    check("obs: different text -> change", cua.observation_changed("screen A", "screen B") is True)
    check("obs: bytes digest stable",
          cua.observation_digest(b"abc") == cua.observation_digest(b"abc"))
    check("obs: None -> empty digest", cua.observation_digest(None) == "")

def t_verify_verdict():
    check("verify: json done=true", cua.parse_verify_verdict('{"done": true, "reason": "ok"}')["done"] is True)
    check("verify: json done=false", cua.parse_verify_verdict('{"done": false}')["done"] is False)
    check("verify: prose with embedded json",
          cua.parse_verify_verdict('I checked. {"done": true, "reason": "window open"} done.')["done"] is True)
    check("verify: GOAL_REACHED sentinel", cua.parse_verify_verdict("GOAL_REACHED")["done"] is True)
    check("verify: done=yes sentinel", cua.parse_verify_verdict("status done: yes")["done"] is True)
    check("verify: garbage -> NOT done (fail-safe)", cua.parse_verify_verdict("uhhh maybe?")["done"] is False)
    check("verify: empty -> NOT done", cua.parse_verify_verdict("")["done"] is False)
    check("verify: NOT_DONE wins over stray 'done'",
          cua.parse_verify_verdict("the task is NOT_DONE yet, done soon")["done"] is False)

def t_loop_status():
    check("status: goal reached wins",
          cua.loop_status(step=9, max_steps=10, goal_done=True, stall_count=5) == cua.GOAL_REACHED)
    check("status: budget exhausted",
          cua.loop_status(step=10, max_steps=10, goal_done=False, stall_count=0) == cua.MAX_STEPS)
    check("status: stalled",
          cua.loop_status(step=3, max_steps=10, goal_done=False, stall_count=2) == cua.STALLED)
    check("status: still running",
          cua.loop_status(step=2, max_steps=10, goal_done=False, stall_count=1) == cua.RUNNING)
    check("status: goal beats budget AND stall",
          cua.loop_status(step=10, max_steps=10, goal_done=True, stall_count=9) == cua.GOAL_REACHED)

def t_trace():
    tr = cua.CuaTrace("linux", "open settings and toggle wifi")
    tr.record("screenshot", "linux_desktop_screenshot", True, True)
    tr.record("click", "linux_desktop_click", True, True)
    tr.record("click", "linux_desktop_click", True, False)  # no change -> stall-ish
    tr.finish(cua.GOAL_REACHED)
    d = tr.to_dict()
    check("trace: counts steps", d["n_steps"] == 3)
    check("trace: reached flag", d["reached"] is True and d["status"] == cua.GOAL_REACHED)
    check("trace: platform/goal carried", d["platform"] == "linux" and d["goal"].startswith("open settings"))
    tr2 = cua.CuaTrace("windows", "x").finish(cua.MAX_STEPS)
    check("trace: budget exhaust -> not reached", tr2.to_dict()["reached"] is False)

class _FakeReq:
    """Request stand-in: async json() returns the payload (or raises to test the
    bad-body degrade path)."""
    def __init__(self, obj, raise_json=False):
        self._obj = obj
        self._raise = raise_json

    async def json(self):
        if self._raise:
            raise ValueError("bad json")
        return self._obj

def _cbody(resp):
    """Decode a (real fastapi) JSONResponse rendered body into a dict."""
    return json.loads(bytes(resp.body).decode("utf-8"))

async def _fake_loop_ok(goal, platform="windows", max_steps=None, session_id=None):
    """Stand-in _cua_loop: a reached trace (the shape CuaTrace.to_dict() yields)."""
    return {"platform": platform, "goal": goal, "status": cua.GOAL_REACHED,
            "n_steps": 1, "steps": [], "reached": True}

async def _fake_loop_boom(*a, **k):
    raise RuntimeError("boom")

def t_computer_use():
    _orig_loop = getattr(cua, "_cua_loop", None)
    try:
        cua.configure(cua_enable=False)
        cua._cua_loop = _fake_loop_boom
        r = asyncio.run(cua.v1_computer_use_logic(_FakeReq({"goal": "x"})))
        b = _cbody(r)
        check("cua route: disabled -> honest notice, loop not run",
              b.get("enabled") is False and getattr(r, "status_code", None) == 200
              and "error" not in b)

        cua.configure(cua_enable=True)
        cua._cua_loop = _fake_loop_ok
        r = asyncio.run(cua.v1_computer_use_logic(_FakeReq({"goal": "   "})))
        b = _cbody(r)
        check("cua route: enabled + blank goal -> 400 missing goal",
              getattr(r, "status_code", None) == 400 and "goal" in (b.get("error") or ""))

        r = asyncio.run(cua.v1_computer_use_logic(
            _FakeReq({"goal": "open settings", "platform": "linux"})))
        b = _cbody(r)
        check("cua route: enabled + goal -> loop trace merged (enabled True)",
              b.get("enabled") is True and b.get("status") == cua.GOAL_REACHED
              and b.get("reached") is True and b.get("platform") == "linux")

        cua._cua_loop = _fake_loop_boom
        r = asyncio.run(cua.v1_computer_use_logic(_FakeReq({"goal": "x"})))
        b = _cbody(r)
        check("cua route: loop error -> 200 honest error (no 500)",
              getattr(r, "status_code", None) == 200 and "boom" in (b.get("error") or ""))
    finally:
        if _orig_loop is not None:
            cua._cua_loop = _orig_loop

def t_extract_png():
    check("extract_png: linux path",
          cua._cua_extract_png({"output": "saved to /tmp/shot.png ok"}) == "/tmp/shot.png")
    check("extract_png: windows path",
          cua._cua_extract_png({"output": r"wrote C:\Users\m\a.png done"}) == r"C:\Users\m\a.png")
    check("extract_png: no png -> None", cua._cua_extract_png({"output": "nothing here"}) is None)
    check("extract_png: empty+None -> None",
          cua._cua_extract_png({}) is None and cua._cua_extract_png(None) is None)

async def _fake_dispatch_nopng(verb, args, session_id=None):
    """Injected verb-dispatch stand-in: a screenshot that wrote no path."""
    return {"output": "screenshot taken but no path here", "success": True}

def t_screenshot_uri_injected():
    cua.configure(dispatch_mios_verb_inner=_fake_dispatch_nopng)
    uri, obs = asyncio.run(cua._cua_screenshot_uri("windows", None))
    check("screenshot_uri: no png in result -> (None, output)",
          uri is None and "no path here" in obs)
    uri2, obs2 = asyncio.run(cua._cua_screenshot_uri("macos", None))
    check("screenshot_uri: unknown platform -> (None, '')", uri2 is None and obs2 == "")

def main():
    t_resolve_verb()
    t_observation()
    t_verify_verdict()
    t_loop_status()
    t_trace()
    t_computer_use()
    t_extract_png()
    t_screenshot_uri_injected()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_cua_hierarchy.py (task_1006)
# ==============================================================================
# AI-hint: Verification test suite for mios_cua hierarchy routing, verify-after-action, and coordinate scaling.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

import sys
import os
import json
import asyncio
import contextvars
import mios_cua

_fails_cua_hierarchy = 0

def _check_cua_hierarchy(name, cond, detail=""):
    global _fails_cua_hierarchy
    if not cond:
        _fails_cua_hierarchy += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def test_coordinate_scaling():
    mios_cua._W_ORIG = 1920
    mios_cua._H_ORIG = 1080
    mios_cua._W_TENSOR = 1280
    mios_cua._H_TENSOR = 720
    mios_cua._HIDPI_SCALE_FACTOR = 1.0

    mios_cua.configure(vision_model="qwen3-vl")

    captured = {}
    async def _mock_dispatch(verb, args, session_id=None):
        captured["verb"] = verb
        captured["args"] = args
        return {"success": True, "output": "clicked"}

    mios_cua._dispatch_mios_verb_inner = _mock_dispatch

    asyncio.run(mios_cua._execute_click_hierarchy("windows_desktop_click", {"x": 512, "y": 384}, "windows"))

    _check_cua_hierarchy("qwen3 scaling: x coord", captured["args"]["x"] == 983)
    _check_cua_hierarchy("qwen3 scaling: y coord", captured["args"]["y"] == 415)

    mios_cua.configure(vision_model="qwen2.5-vl")
    asyncio.run(mios_cua._execute_click_hierarchy("windows_desktop_click", {"x": 512, "y": 384}, "windows"))

    _check_cua_hierarchy("qwen2.5 scaling: x coord", captured["args"]["x"] == 768)
    _check_cua_hierarchy("qwen2.5 scaling: y coord", captured["args"]["y"] == 576)

def test_a11y_hierarchy_routing():
    mios_cua._W_ORIG = 1920
    mios_cua._H_ORIG = 1080
    mios_cua._HIDPI_SCALE_FACTOR = 1.0
    mios_cua.configure(vision_model="qwen3-vl")

    captured_dispatches = []

    elements_json = json.dumps({
        "elements": [
            {"name": "SaveButton", "bounds": [900, 400, 1000, 450]}
        ]
    })

    async def _mock_dispatch(verb, args, session_id=None):
        captured_dispatches.append((verb, args))
        if verb == "windows_desktop_list_elements":
            return {"success": True, "output": elements_json}
        if verb == "windows_desktop_click_element":
            return {"success": True, "output": "clicked element"}
        return {"success": True, "output": "clicked fallback"}

    mios_cua._dispatch_mios_verb_inner = _mock_dispatch

    res = asyncio.run(mios_cua._execute_click_hierarchy("windows_desktop_click", {"x": 512, "y": 384}, "windows"))

    verbs = [d[0] for d in captured_dispatches]
    _check_cua_hierarchy("hierarchy: tried elements list", "windows_desktop_list_elements" in verbs)
    _check_cua_hierarchy("hierarchy: clicked element directly", "windows_desktop_click_element" in verbs)
    _check_cua_hierarchy("hierarchy: did NOT fall back to vision coordinate click", "windows_desktop_click" not in verbs)

    captured_dispatches.clear()
    res = asyncio.run(mios_cua._execute_click_hierarchy("windows_desktop_click", {"x": 100, "y": 100}, "windows"))
    verbs = [d[0] for d in captured_dispatches]
    _check_cua_hierarchy("hierarchy fallback: tried elements list", "windows_desktop_list_elements" in verbs)
    _check_cua_hierarchy("hierarchy fallback: fell back to vision coordinate click", "windows_desktop_click" in verbs)
    _check_cua_hierarchy("hierarchy fallback: did NOT click element directly", "windows_desktop_click_element" not in verbs)

def test_verify_retry_and_escalation():
    mios_cua.configure(vision_model="qwen3-vl", cua_enable=True)

    obs_counter = 0
    async def _mock_screenshot(platform, session_id=None):
        nonlocal obs_counter
        obs_counter += 1
        return "data:image/png;base64,stub", f"obs-{obs_counter}"

    mios_cua._cua_screenshot_uri = _mock_screenshot

    vlm_calls = 0
    async def _mock_vlm(system, user_text, image_uri):
        nonlocal vlm_calls
        vlm_calls += 1
        if "verify" in system:
            return {"done": False, "reason": "not done"}
        return {"action": "click", "args": {"x": 500, "y": 500}}

    mios_cua._cua_vlm_json = _mock_vlm

    async def _mock_screenshot_static(platform, session_id=None):
        return "data:image/png;base64,stub", "obs-static"

    mios_cua._cua_screenshot_uri = _mock_screenshot_static

    async def _mock_dispatch(verb, args, session_id=None):
        return {"success": True, "output": "done"}

    mios_cua._dispatch_mios_verb_inner = _mock_dispatch

    async def _mock_void(*a, **k): pass
    mios_cua.wait_for_stable_element = _mock_void

    try:
        asyncio.run(mios_cua._cua_loop("test goal", platform="windows", max_steps=5))
        escalated = False
    except RuntimeError as e:
        escalated = "HITL escalation" in str(e)

    _check_cua_hierarchy("retry loop: escalated to HITL after 3 failed retries", escalated)

def _main_cua_hierarchy():
    test_coordinate_scaling()
    test_a11y_hierarchy_routing()
    test_verify_retry_and_escalation()
    print(f"\n{'ok' if _fails_cua_hierarchy == 0 else str(_fails_cua_hierarchy) + ' FAILED'}")
    return 1 if _fails_cua_hierarchy else 0


def _run_extra_cua_hierarchy():
    import os
    _saved_env = dict(os.environ)
    import mios_cua
    _cua_attrs = ('_dispatch_mios_verb_inner', '_cua_screenshot_uri', '_cua_vlm_json', '_cua_loop', 'wait_for_stable_element', '_W_ORIG', '_H_ORIG', '_W_TENSOR', '_H_TENSOR', '_HIDPI_SCALE_FACTOR')
    _saved_cua = {a: getattr(mios_cua, a, None) for a in _cua_attrs if hasattr(mios_cua, a)}
    try:
        return _main_cua_hierarchy()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0
    finally:
        os.environ.clear()
        os.environ.update(_saved_env)
        for a in _cua_attrs:
            if a in _saved_cua:
                setattr(mios_cua, a, _saved_cua[a])
            elif hasattr(mios_cua, a):
                delattr(mios_cua, a)



def _run_all_folded_cua_suites():
    rc = _run_extra_cua_hierarchy()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _rc_main = main()
    _run_all_folded_cua_suites()
    sys.exit(_rc_main)
