#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_blades (V4/V5 blade topology + AI-related: ./mios_blades.py, ./mios_config.py, ./...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_blades (V4 blade model + V5 per-blade admission capacity)."""

import os
import sys

import mios_blades as B

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _endpoint_key(ep):
    """host:port of an endpoint URL -- the same identity server._endpoint_key uses."""
    s = str(ep or "").split("://", 1)[-1]
    return s.split("/", 1)[0] or s

def t_local_blade_name():
    _saved_env = os.environ.get("MIOS_IDENTITY_HOSTNAME")
    _saved_toml = B._toml_section
    try:
        os.environ["MIOS_IDENTITY_HOSTNAME"] = "blade-from-env"
        check("local_blade: env MIOS_IDENTITY_HOSTNAME wins", B.local_blade_name() == "blade-from-env")
        os.environ.pop("MIOS_IDENTITY_HOSTNAME", None)
        B._toml_section = lambda s: {"hostname": "blade-from-identity"} if s == "identity" else {}
        check("local_blade: falls to [identity].hostname",
              B.local_blade_name() == "blade-from-identity")
        B._toml_section = lambda s: {}            # no env, no identity -> OS hostname
        check("local_blade: OS hostname fallback non-empty", bool(B.local_blade_name()))
    finally:
        B._toml_section = _saved_toml
        if _saved_env is None:
            os.environ.pop("MIOS_IDENTITY_HOSTNAME", None)
        else:
            os.environ["MIOS_IDENTITY_HOSTNAME"] = _saved_env

def t_load_blade_pool_default():
    _saved = B._toml_section
    B._toml_section = lambda s: {}
    try:
        pool = B.load_blade_pool("ws", 23000, 16.0)
    finally:
        B._toml_section = _saved
    check("pool: default has only the local blade", list(pool.keys()) == ["ws"], str(pool))
    check("pool: local vram defaults to the local scalar", pool["ws"]["vram_budget_mb"] == 23000)
    check("pool: local load_ceil carried", pool["ws"]["load_ceil"] == 16.0)

def t_load_blade_pool_overrides():
    _saved = B._toml_section
    blades = {
        "ws":     {"vram_budget_mb": 24000},        # OVERRIDE the local default
        "potato": {"vram_budget_mb": 8000, "load_ceil": 12},
        "bigbox": {"vram_budget_mb": 80000},
        "vague":  {},                                # declared, no capacity -> local scalar
        "bad":    "notadict",                        # ignored
    }
    B._toml_section = lambda s: blades if s == "blades" else {}
    try:
        pool = B.load_blade_pool("ws", 23000, None)
    finally:
        B._toml_section = _saved
    check("pool: local blade overridden by [blades.<local>]", pool["ws"]["vram_budget_mb"] == 24000)
    check("pool: remote blade vram carried", pool["potato"]["vram_budget_mb"] == 8000)
    check("pool: remote blade load_ceil carried", pool["potato"]["load_ceil"] == 12.0)
    check("pool: big blade carried", pool["bigbox"]["vram_budget_mb"] == 80000)
    check("pool: capacity-less declared blade degrades to local scalar",
          pool["vague"]["vram_budget_mb"] == 23000, str(pool.get("vague")))
    check("pool: non-dict blade entry ignored", "bad" not in pool)

def t_endpoint_blade_map():
    registry = {
        "node:a":   {"endpoint": "http://10.0.0.9:11434/v1", "blade": "potato"},
        "node:b":   {"endpoint": "http://10.0.0.9:11435/v1", "blade": "potato"},  # same machine
        "node:loc": {"endpoint": "http://localhost:11441/v1"},                    # no blade -> local
        "inert":    {"endpoint": ""},                                             # skipped
        "bad":      "notadict",                                                   # skipped
    }
    m = B.endpoint_blade_map(registry, _endpoint_key, "ws")
    check("ep_map: explicit blade mapped", m.get("10.0.0.9:11434") == "potato", str(m))
    check("ep_map: second node same machine -> same blade", m.get("10.0.0.9:11435") == "potato")
    check("ep_map: node without blade -> local blade", m.get("localhost:11441") == "ws")
    check("ep_map: endpoint-less node skipped", all("inert" not in k for k in m))
    check("ep_map: non-dict cfg skipped", len(m) == 3, str(m))

def t_blade_for_endpoint():
    m = {"10.0.0.9:11434": "potato"}
    check("blade_for_ep: known endpoint -> its blade",
          B.blade_for_endpoint(m, _endpoint_key, "http://10.0.0.9:11434/v1", "ws") == "potato")
    check("blade_for_ep: unknown endpoint -> local (degrade-open)",
          B.blade_for_endpoint(m, _endpoint_key, "http://1.2.3.4:9/v1", "ws") == "ws")

def t_blade_vram_budget_degrade_open():
    pool = {"ws": {"vram_budget_mb": 23000}, "potato": {"vram_budget_mb": 8000},
            "zero": {"vram_budget_mb": 0}}
    check("budget: known blade -> its budget", B.blade_vram_budget(pool, "potato", 23000) == 8000)
    check("budget: local blade -> its budget", B.blade_vram_budget(pool, "ws", 23000) == 23000)
    check("budget: UNKNOWN blade -> local scalar (degrade-open)",
          B.blade_vram_budget(pool, "ghost", 23000) == 23000)
    check("budget: zero/missing budget -> local scalar (degrade-open)",
          B.blade_vram_budget(pool, "zero", 23000) == 23000)

def t_admission_decision():
    """The V5 headline: the SAME co-load (used+est+reserve) is DENIED against a small
    remote blade's budget yet ADMITTED against the LOCAL scalar (the default-off path).
    Mirrors _admit's comparison `(used + est + reserve) <= budget` with synthetic data."""
    LOCAL_SCALAR = 23000          # == server VRAM_BUDGET_MB default (the default-off budget)
    pool = {"ws": {"vram_budget_mb": LOCAL_SCALAR}, "potato": {"vram_budget_mb": 8000}}
    ep_map = {"10.0.0.9:11434": "potato"}
    used, est, reserve = 6000, 2000, 1000     # co-load footprint = 9000 MB

    def admits(budget):
        return (used + est + reserve) <= budget

    off_budget = LOCAL_SCALAR
    check("admit(default-off): remote co-load fits the local scalar -> ADMIT",
          admits(off_budget) is True)

    on_blade = B.blade_for_endpoint(ep_map, _endpoint_key, "http://10.0.0.9:11434/v1", "ws")
    on_budget = B.blade_vram_budget(pool, on_blade, LOCAL_SCALAR)
    check("admit(multiblade-on): remote node resolves to its blade", on_blade == "potato")
    check("admit(multiblade-on): 9000 over potato's 8000 -> DENY", admits(on_budget) is False)

    used2 = 4000                              # 4000+2000+1000 = 7000 <= 8000
    check("admit(multiblade-on): 7000 under potato's 8000 -> ADMIT",
          (used2 + est + reserve) <= on_budget)

    unk_budget = B.blade_vram_budget(pool, "ghost-blade", LOCAL_SCALAR)
    check("admit(degrade-open): unknown blade -> local scalar -> ADMIT",
          admits(unk_budget) is True and unk_budget == LOCAL_SCALAR)

def main():
    t_local_blade_name()
    t_load_blade_pool_default()
    t_load_blade_pool_overrides()
    t_endpoint_blade_map()
    t_blade_for_endpoint()
    t_blade_vram_budget_degrade_open()
    t_admission_decision()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_k3s.py (task_1006)
# ==============================================================================
# AI-hint: Standalone unit test for the #61 generated k3s manifests: every committed usr/share/mios/k3s/generated/*.yaml parses, declares an apiVersion, carries ...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for the #61 pods->k3s generated manifests.

Validates the COMMITTED artifacts (not the generator, which needs live pods +
podman): each manifest must parse as YAML, declare an apiVersion, carry the
deterministic AI-hint header, and contain none of the volatile fields the
generator strips -- so a malformed or un-stripped manifest can never land. Skips
cleanly if pyyaml is unavailable.

Run:  python test_mios_k3s.py
"""

import glob
import os
import sys

_RESULTS_k3s: list = []

def _check_k3s(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS_k3s.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _gen_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    return os.path.join(repo, "usr", "share", "mios", "k3s", "generated")

def _main_k3s() -> int:
    try:
        import yaml
    except ModuleNotFoundError:
        print("[SKIP] pyyaml not installed")
        print("\nskipped (0 checks)")
        return 0

    d = _gen_dir()
    files = sorted(glob.glob(os.path.join(d, "*.yaml")))
    _check_k3s("manifests present", len(files) > 0, f"{len(files)} in {d}")

    VOLATILE = ("creationTimestamp:", "bind-mount-options:", "Created with podman")
    for p in files:
        base = os.path.basename(p)
        txt = open(p, encoding="utf-8").read()
        try:
            docs = list(yaml.safe_load_all(txt))
            parsed = True
        except Exception as e:  # noqa: BLE001
            parsed = False
            _check_k3s(f"{base}: parses", False, str(e)[:80])
            continue
        _check_k3s(f"{base}: parses", parsed)
        _check_k3s(f"{base}: declares apiVersion",
               any(isinstance(x, dict) and x.get("apiVersion") for x in docs))
        _check_k3s(f"{base}: AI-hint header", txt.splitlines()[0].startswith("# AI-hint:"))
        bad = [v for v in VOLATILE if v in txt]
        _check_k3s(f"{base}: no volatile fields", not bad, str(bad))

    passed = sum(1 for _, ok in _RESULTS_k3s if ok)
    total = len(_RESULTS_k3s)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def _run_extra_k3s():
    try:
        return _main_k3s()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_blades_suites():
    rc = _run_extra_k3s()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_blades_suites()
    sys.exit(main())
