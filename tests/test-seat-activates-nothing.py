#!/usr/bin/env python3
# AI-hint: The executable definition of MiOS-Metal. A seat is [blade].type = "endpoint", an archetype granting NO capabilities, so it must activ...
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Proof: an endpoint blade -- a MiOS-Metal -- activates no service."""

import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

SEAT = "endpoint"

def _load():
    with open(os.path.join(_ROOT, "usr/share/mios/mios.toml"), "rb") as fh:
        return tomllib.load(fh)

def _caps(v):
    return [v] if isinstance(v, str) else list(v or [])

def _long_running():
    """Shipped .service units that stay up (a oneshot needs no blade gate)."""
    out = set()
    d = os.path.join(_ROOT, "usr/lib/systemd/system")
    for name in sorted(os.listdir(d)):
        if not name.endswith(".service") or "@" in name:
            continue
        with open(os.path.join(d, name), encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        stype = next((l.split("=", 1)[1].strip() for l in body.splitlines()
                      if l.startswith("Type=")), "")
        if stype != "oneshot":
            out.add(name[:-len(".service")])
    return out

def starts(data, archetype) -> set:
    """Containers a blade of this archetype activates.

    The fanout writes one ConditionPathExists per required capability, and
    systemd ANDs them, so a unit runs only when the archetype grants ALL of them.
    """
    blade = data["blade"]
    have = set(_caps(blade["archetypes"][archetype]))
    req = {k: set(_caps(v)) for k, v in blade["requires"].items()}
    return {c for c in data["containers"] if req.get(c, set()) <= have}

class TestSeatActivatesNothing(unittest.TestCase):
    def setUp(self):
        self.d = _load()
        self.conts = set(self.d["containers"])

    def test_the_seat_grants_no_capability(self):
        self.assertEqual(_caps(self.d["blade"]["archetypes"][SEAT]), [])

    def test_the_seat_starts_no_container(self):
        self.assertEqual(starts(self.d, SEAT), set())

    def test_every_container_is_gated(self):
        # If any container required nothing it would start on a seat too, which
        # is the exact state this replaced.
        req = self.d["blade"]["requires"]
        ungated = sorted(c for c in self.conts if not _caps(req.get(c)))
        self.assertEqual(ungated, [])

    def test_native_long_running_units_are_gated_or_declared_seat_side(self):
        # The containers were only half the plane: 18 long-running .service
        # units shipped ungated, so a seat started every one of them.
        req = set(self.d["blade"]["requires"])
        seat = set(self.d["blade"].get("seat_side") or [])
        for u in sorted(_long_running()):
            self.assertTrue(u in req or u in seat, "%s is classified nowhere" % u)

    def test_the_seat_runs_its_front_door_and_nothing_that_serves(self):
        seat = set(self.d["blade"].get("seat_side") or [])
        self.assertIn("mios-agent-pipe", seat)      # reaches the blade
        for serving in ("hermes-worker", "k3s", "mios-finetune-serve",
                        "mios-opencode-gateway", "mios-policy-arbiter",
                        # The WORKER's browser, not the person's: its only
                        # client is hermes-worker, which a seat does not run.
                        "mios-hermes-browser-worker"):
            self.assertNotIn(serving, seat, serving)

    def test_the_seat_line_is_io_versus_compute(self):
        """Everything a seat runs is something the PERSON touches."""
        seat = set(self.d["blade"].get("seat_side") or [])
        self.assertEqual(seat, {
            "mios-agent-pipe",        # the front door every client dials
            "mios-ai-legacy-forward", # the same front door on the legacy port
            "hermes-dashboard",       # the UI
            "mios-hermes-browser",    # the browser the person watches
            "mios-hermes-tail",       # the journal view
            "mios-ttyd-bash",         # a pty
            "mios-ttyd-powershell",   # a pty
        })

    def test_the_ungated_register_is_drained(self):
        self.assertEqual(list(self.d["blade"].get("ungated") or []), [])

    def test_non_seat_archetypes_still_start_the_service_plane(self):
        for a in ("hybrid", "compute", "controller", "headless", "desktop"):
            self.assertGreater(len(starts(self.d, a)), 0, a)

    def test_only_gpu_archetypes_start_the_gpu_lanes(self):
        # ONE heavy lane: its engine (vLLM or SGLang) is [ai].heavy_engine, not a unit.
        gpu = {"mios-llm-heavy", "mios-llm-worker@"}
        for a in ("hybrid", "compute"):
            self.assertTrue(gpu <= starts(self.d, a), a)
        for a in ("controller", "headless", "desktop", SEAT):
            self.assertFalse(gpu & starts(self.d, a), a)

    def test_the_heavy_engine_is_a_choice_inside_one_lane(self):
        # The second heavy lane was folded in: an engine is an overlay of the one
        # spec, selected by [ai].heavy_engine, so no archetype starts two.
        lanes = {c for c, spec in self.d["containers"].items() if "engine" in spec}
        self.assertEqual(lanes, {"mios-llm-heavy"})
        engine = self.d["containers"]["mios-llm-heavy"]["engine"]
        self.assertEqual(engine["select"], "ai.heavy_engine")
        self.assertIn(self.d["ai"]["heavy_engine"], engine)

    def test_every_required_capability_is_grantable(self):
        granted = set()
        for caps in self.d["blade"]["archetypes"].values():
            granted |= set(_caps(caps))
        for svc, caps in self.d["blade"]["requires"].items():
            for cap in _caps(caps):
                self.assertIn(cap, granted, "%s requires ungrantable %s" % (svc, cap))

    def test_a_dropin_exists_for_every_required_capability(self):
        need = set()
        for caps in self.d["blade"]["requires"].values():
            need |= set(_caps(caps))
        for cap in sorted(need):
            path = os.path.join(_ROOT, "usr/share/mios/dropins", "blade-%s.conf" % cap)
            self.assertTrue(os.path.isfile(path), path)
            with open(path, encoding="utf-8") as fh:
                self.assertIn("ConditionPathExists=/etc/mios/blade.d/%s" % cap, fh.read())

if __name__ == "__main__":
    unittest.main()
