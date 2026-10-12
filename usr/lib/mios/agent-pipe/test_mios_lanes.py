# AI-hint: Standalone unit test for mios_lanes (WS-1 unified lane resolver) -- verifies build_chain ordering, health-cached pick, per-lane cooldown failover, t...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for mios_lanes (WS-1).

Pure stdlib + the sibling module only -- no server.py import, so it runs on any
Python 3.10+ without the agent-pipe runtime deps (httpx/fastapi/...). Mirrors the
mios_sched test pattern: a mock-free asyncio harness with explicit asserts and a
PASS/FAIL summary; exit code != 0 on any failure.

Run:  python test_mios_lanes.py
"""

import asyncio
import sys

from mios_lanes import Lane, LaneResolver, build_chain

_RESULTS: list = []

def _check(name: str, cond: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(cond), detail))
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, (" -- " + detail) if detail and not cond else ""))

class _Clock:
    """Mutable monotonic clock."""
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt

def _lanes():
    # The ONE heavy lane (whichever engine serves it) + the always-on light floor.
    return {
        "light": Lane("light", "http://localhost:8500/v1", "granite4.1:8b"),
        "heavy": Lane("heavy", "http://localhost:8520/v1", "mios-heavy"),
    }

def _resolver(up, clock, **kw):
    """up: dict {lane_url_substring: bool}; probe counts calls in `calls`."""
    calls = {"n": 0}

    async def probe(url):
        calls["n"] += 1
        for key, ok in up.items():
            if key in url:
                return ok
        return False

    lanes = _lanes()
    chain = build_chain(kw.pop("heavy_engine", "vllm"), lanes.keys())
    r = LaneResolver(lanes, {"heavy": chain, "tool": chain}, probe,
                     ttl=kw.pop("ttl", 30.0), cooldown=kw.pop("cooldown", 60.0),
                     clock=clock)
    return r, calls

async def t_build_chain():
    ids = ["light", "heavy"]
    for engine in ("vllm", "sglang", ""):
        _check("chain %r -> heavy first" % engine, build_chain(engine, ids) == ["heavy", "light"],
               str(build_chain(engine, ids)))
    _check("chain explicit comma", build_chain("heavy,light", ids) == ["heavy", "light"],
           str(build_chain("heavy,light", ids)))
    _check("chain engine name in a comma list = heavy", build_chain("vllm,light", ids) == ["heavy", "light"],
           str(build_chain("vllm,light", ids)))
    _check("chain light-only", build_chain("light", ids) == ["light"],
           str(build_chain("light", ids)))
    _check("chain drops unavailable heavy", build_chain("sglang", ["light"]) == ["light"],
           str(build_chain("sglang", ["light"])))
    _check("chain light always terminal", build_chain("light,heavy", ids) == ["heavy", "light"],
           str(build_chain("light,heavy", ids)))
    _check("chain has ONE heavy lane", build_chain("sglang,vllm,light", ids) == ["heavy", "light"],
           str(build_chain("sglang,vllm,light", ids)))

async def t_pick_prefers_heavy():
    clk = _Clock()
    r, _ = _resolver({"8520": True, "8500": True}, clk)
    lane = await r.pick("tool")
    _check("prefers heavy when up", lane.id == "heavy", lane.id)
    r2, _ = _resolver({"8520": True, "8500": True}, clk, heavy_engine="sglang")
    _check("engine choice does not change the lane", (await r2.pick("tool")).id == "heavy")

async def t_failover_to_light():
    clk = _Clock()
    r, _ = _resolver({"8520": False, "8500": True}, clk)
    _check("heavy down -> light", (await r.pick("tool")).id == "light")

async def t_cooldown_skips_reprobe():
    clk = _Clock()
    r, calls = _resolver({"8520": False, "8500": True}, clk, cooldown=60.0)
    await r.pick("tool")               # probes heavy(fail)+light(ok) = 2
    n1 = calls["n"]
    await r.pick("tool")               # heavy in cooldown -> skipped; light cached(ttl) -> 0 new
    _check("cooldown+ttl avoid reprobe", calls["n"] == n1, "calls went %d->%d" % (n1, calls["n"]))

async def t_ttl_caches():
    clk = _Clock()
    r, calls = _resolver({"8520": True, "8500": True}, clk, ttl=30.0)
    await r.pick("tool")
    n1 = calls["n"]
    clk.advance(10)                    # within ttl
    await r.pick("tool")
    _check("ttl caches health", calls["n"] == n1, "calls %d->%d" % (n1, calls["n"]))
    clk.advance(40)                    # past ttl -> reprobe
    await r.pick("tool")
    _check("reprobe after ttl", calls["n"] > n1)

async def t_recovery_after_cooldown():
    clk = _Clock()
    up = {"8520": False, "8500": True}
    r, _ = _resolver(up, clk, cooldown=60.0, ttl=30.0)
    _check("initially light (heavy down)", (await r.pick("tool")).id == "light")
    up["8520"] = True                  # heavy comes back
    clk.advance(70)                    # past cooldown -> re-probe heavy
    _check("recovers to heavy after cooldown", (await r.pick("tool")).id == "heavy")

async def t_terminal_floor():
    clk = _Clock()
    r, _ = _resolver({"8520": False, "8500": False}, clk)
    lane = await r.pick("tool")
    _check("all down -> terminal floor light (not None)", lane is not None and lane.id == "light",
           repr(lane))

async def t_mark_down():
    clk = _Clock()
    r, _ = _resolver({"8520": True, "8500": True}, clk)
    _check("up before mark_down", (await r.pick("tool")).id == "heavy")
    r.mark_down("heavy")
    _check("mark_down forces failover", (await r.pick("tool")).id == "light")

async def main():
    print("test_mios_lanes (WS-1 lane resolver)")
    for fn in (t_build_chain, t_pick_prefers_heavy, t_failover_to_light,
               t_cooldown_skips_reprobe, t_ttl_caches, t_recovery_after_cooldown,
               t_terminal_floor, t_mark_down):
        await fn()
    fails = [n for (n, ok, _d) in _RESULTS if not ok]
    print("\n%d checks, %d passed, %d failed" % (len(_RESULTS), len(_RESULTS) - len(fails), len(fails)))
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
