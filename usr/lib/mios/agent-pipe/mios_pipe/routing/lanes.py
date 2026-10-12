# AI-hint: Unified inference-lane resolver (WS-1) -- the ONE place the agent-pipe chooses a model lane.
# AI-doc: usr/share/doc/mios/manual/routing.md
import time

class Lane:
    """A single inference endpoint. ``url`` is the OpenAI /v1 base (no trailing
    slash); ``model`` is the served-model-name to request on it."""
    __slots__ = ("id", "url", "model")

    def __init__(self, id: str, url: str, model: str):
        self.id = id
        self.url = url
        self.model = model

    def as_tuple(self) -> tuple:
        """(url, model) -- the shape the legacy _pick_tool_backend callers expect."""
        return (self.url, self.model)

    def __repr__(self) -> str:  # pragma: no cover -- debug only
        return "Lane(%r, %r, %r)" % (self.id, self.url, self.model)

HEAVY = "heavy"   # the ONE heavy lane (mios-llm-heavy), whichever engine serves it

def build_chain(heavy_engine, available) -> list:
    """Ordered lane ids for a route. The heavy lane is one lane whatever engine
    [ai].heavy_engine runs on it, so the value only steers the ROUTE: "light"
    skips the heavy lane, a comma list is an explicit order, and anything else
    (an engine name such as "vllm"/"sglang", or empty) puts the heavy lane first.
    Inside a comma list an engine name -- any id that is not an available lane --
    means the heavy lane. "light" is always the terminal floor."""
    avail_set = set(available)
    he = (heavy_engine or "").strip().lower()
    if "," in he:
        order = [x.strip() for x in he.split(",") if x.strip()]
        order = [x if x in avail_set or x == "light" else HEAVY for x in order]
    elif he == "light":
        order = ["light"]
    else:
        order = [HEAVY, "light"]
    seen: set = set()
    chain: list = []
    for x in order:
        if x in avail_set and x not in seen:
            seen.add(x)
            chain.append(x)
    if "light" in chain:                       # force the floor lane terminal
        chain = [x for x in chain if x != "light"] + ["light"]
    return chain

class LaneResolver:

    def __init__(self, lanes, chains, probe, *, ttl: float = 30.0,
                 cooldown: float = 60.0, clock=time.monotonic):
        self._lanes = dict(lanes)
        self._chains = {k: list(v) for k, v in chains.items()}
        self._probe = probe
        self._ttl = float(ttl)
        self._cooldown = float(cooldown)
        self._clock = clock
        self._health: dict = {}          # id -> (ts, ok)
        self._cooldown_until: dict = {}   # id -> ts

    async def _is_up(self, lane: Lane) -> bool:
        now = self._clock()
        if now < self._cooldown_until.get(lane.id, 0.0):
            return False                  # parked -> skip the probe, treat as down
        cached = self._health.get(lane.id)
        if cached is not None and (now - cached[0]) < self._ttl:
            return cached[1]
        try:
            ok = bool(await self._probe(lane.url))
        except Exception:  # noqa: BLE001 -- any probe failure => lane is down
            ok = False
        self._health[lane.id] = (now, ok)
        if not ok:
            self._cooldown_until[lane.id] = now + self._cooldown
        else:
            self._cooldown_until.pop(lane.id, None)
        return ok

    async def pick(self, role: str, fallback_role: str = "heavy"):
        """First reachable Lane in ``role``'s chain. Returns the chain's terminal
        lane (the always-on floor) if none probe up, or None if the role has no
        configured lanes at all. Never raises."""
        chain = self._chains.get(role) or self._chains.get(fallback_role) or []
        last = None
        for lid in chain:
            lane = self._lanes.get(lid)
            if lane is None:
                continue
            last = lane
            if await self._is_up(lane):
                return lane
        return last                       # terminal floor (e.g. light) or None

    def mark_down(self, lane_id: str) -> None:
        """Force a lane onto cooldown (e.g. after a dispatch 404/connect error at a
        call site) so the next pick fails it over immediately."""
        now = self._clock()
        self._health[lane_id] = (now, False)
        self._cooldown_until[lane_id] = now + self._cooldown

    def snapshot(self) -> dict:
        """Health/cooldown view for /v1/cluster/health + diagnostics."""
        return {"lanes": {k: {"url": v.url, "model": v.model} for k, v in self._lanes.items()},
                "chains": {k: list(v) for k, v in self._chains.items()},
                "health": dict(self._health),
                "cooldown_until": dict(self._cooldown_until)}
