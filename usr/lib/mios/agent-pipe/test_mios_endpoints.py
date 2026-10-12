#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_endpoints (refactor R-wave leaf extraction). Pure stdlib, no server.py/DB/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_endpoints (refactor R-wave leaf)."""

import os
import sys
import importlib
from unittest.mock import patch

os.environ["MIOS_NO_TOOL_CHOICE_HINTS"] = "11436"
os.environ["MIOS_PARALLEL_TOOLS_HINTS"] = "11441"
os.environ["MIOS_KV_PAGING_HINTS"] = "11436"

import mios_endpoints as e  # noqa: E402

_fails = 0

GENERIC = "http://core.example/v1"

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_binding_api():
    check("binding: top-level api, case-folded", e._binding_api({"api": "Llamacpp"}, None) == "llamacpp")
    check("binding: empty when absent", e._binding_api({}, None) == "")
    check("binding: engine api wins, engine key case-insensitive",
          e._binding_api({"engines": {"cpu": {"api": "OpenAI"}}}, "CPU") == "openai")
    check("binding: engine overrides top-level api",
          e._binding_api({"api": "openai", "engines": {"cpu": {"api": "vllm"}}}, "cpu") == "vllm")
    check("binding: falls back to top-level when engine has no api",
          e._binding_api({"api": "v1", "engines": {"cpu": {}}}, "cpu") == "v1")

def t_tool_choice():
    check("tool_choice: api=llamacpp -> False",
          e._endpoint_supports_tool_choice(GENERIC, {"api": "llamacpp"}) is False)
    check("tool_choice: explicit tool_choice=False -> False",
          e._endpoint_supports_tool_choice(GENERIC, {"tool_choice": False}) is False)
    check("tool_choice: per-engine tool_choice=False -> False",
          e._endpoint_supports_tool_choice(GENERIC, {"engines": {"ig": {"tool_choice": False}}}, "ig") is False)
    check("tool_choice: iGPU port hint (11436) -> False",
          e._endpoint_supports_tool_choice("http://h:11436/v1", {}) is False)
    check("tool_choice: generic OpenAI endpoint -> True",
          e._endpoint_supports_tool_choice(GENERIC, {}) is True)

def t_parallel():
    check("parallel: heavy-lane port hint (11441) -> True",
          e._endpoint_supports_parallel_tools("http://h:11441/v1") is True)
    check("parallel: generic endpoint -> False (sequential default)",
          e._endpoint_supports_parallel_tools(GENERIC) is False)
    check("parallel: light-lane port -> False",
          e._endpoint_supports_parallel_tools("http://h:11434/v1") is False)
    # A [ports] key stands for that lane's allocated port, never for itself;
    # a number or an unknown token passes through unchanged.
    got = e._port_hints("llm_heavy, 11441,")
    import mios_toml  # importable once the SSOT reader has put usr/lib/mios on sys.path
    heavy = str(mios_toml.load_merged()["ports"]["llm_heavy"])
    check("parallel: a [ports] key hint resolves to the lane's port",
          got == (heavy, "11441"), str(got))
    check("parallel: an unknown token is kept verbatim",
          e._port_hints("no_such_lane") == ("no_such_lane",))

def t_is_llamacpp():
    check("llamacpp: api=llamacpp -> True", e._endpoint_is_llamacpp(GENERIC, {"api": "llamacpp"}) is True)
    check("llamacpp: api=vulkan -> True", e._endpoint_is_llamacpp(GENERIC, {"api": "vulkan"}) is True)
    check("llamacpp: api=llama.cpp -> True", e._endpoint_is_llamacpp(GENERIC, {"api": "llama.cpp"}) is True)
    check("llamacpp: KV paging port hint (11436) -> True",
          e._endpoint_is_llamacpp("http://h:11436/v1", {}) is True)
    check("llamacpp: generic OpenAI endpoint -> False", e._endpoint_is_llamacpp(GENERIC, {}) is False)


def t_reallocated_hints():
    # Patch the implementation behind the compatibility shim, whose forwarded
    # attributes are intentionally absent from the shim's own __dict__.
    config = importlib.import_module(e._toml_section.__module__)

    ports = {"llm_igpu": 42001, "llm_heavy": 42002, "k3s_api": 8450}
    keys = ("no_tool_choice_hints", "kv_paging_hints")
    env_keys = ("MIOS_NO_TOOL_CHOICE_HINTS", "MIOS_KV_PAGING_HINTS")
    cases = (
        ("SSOT port keys", dict.fromkeys(keys, "llm_igpu"), None, "42001"),
        ("missing dispatch keys", {}, None, "42001"),
        ("environment port keys", dict.fromkeys(keys, "llm_heavy"), "llm_igpu", "42001"),
        ("environment literal", dict.fromkeys(keys, "llm_igpu"), "43001", "43001"),
        ("environment host substring", dict.fromkeys(keys, "llm_igpu"), "custom-host", "custom-host"),
    )
    try:
        for name, dispatch, override, expected in cases:
            with patch.dict(os.environ), \
                    patch.object(config, "_DISPATCH_TOML", dispatch), \
                    patch.object(config, "_toml_section", return_value=ports):
                for key in env_keys:
                    os.environ.pop(key, None)
                    if override is not None:
                        os.environ[key] = override
                importlib.reload(e)
                endpoint = (f"http://h:{expected}/v1" if expected.isdigit()
                            else f"http://{expected}/v1")
                check(f"{name}: tool choice opt-out follows selected hint",
                      e._endpoint_supports_tool_choice(endpoint, {}) is False)
                check(f"{name}: KV paging follows selected hint",
                      e._endpoint_is_llamacpp(endpoint, {}) is True)
                for stale in ("8450", "8458", "8540", "11436", "42002", "llm_igpu"):
                    endpoint = f"http://h:{stale}/v1"
                    check(f"{name}: unrelated {stale} keeps default capabilities",
                          e._endpoint_supports_tool_choice(endpoint, {}) is True
                          and e._endpoint_is_llamacpp(endpoint, {}) is False)
                check(f"{name}: explicit API still declares llama.cpp",
                      e._endpoint_is_llamacpp(GENERIC, {"api": "llamacpp"}) is True)
    finally:
        importlib.reload(e)

def main():
    t_binding_api()
    t_tool_choice()
    t_parallel()
    t_is_llamacpp()
    t_reallocated_hints()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
