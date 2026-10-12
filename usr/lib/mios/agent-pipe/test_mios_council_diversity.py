# AI-hint: Stdlib offline unit tests for mios_council_diversity -- the council input-diversity gate (task_47 RouteMoA GAP-1) + confidence-aware aggre...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stdlib offline unit tests for mios_council_diversity (task_47 / task_48)."""

import asyncio
import sys

import mios_council_diversity as M

_fails = 0

def check(name, cond):
    global _fails
    if cond:
        print(f"ok   - {name}")
    else:
        _fails += 1
        print(f"FAIL - {name}")

_VEC = {
    "A":  [1.0, 0.0, 0.0],
    "B":  [0.0, 1.0, 0.0],
    "C":  [0.0, 0.0, 1.0],
    "An": [0.9999, 0.01414, 0.0],
    "Am": [0.8, 0.6, 0.0],
}

def _mk_embed(missing=None):
    async def _embed(text):
        if missing is not None and text == missing:
            return None
        return list(_VEC.get(text, [0.0, 0.0, 0.0]))
    return _embed

def _nodes(*texts):
    return [{"output": t, "tag": t} for t in texts]

def t_select_diverse():
    v = lambda k: list(_VEC[k])
    sel = M.select_diverse([v("A"), v("A")], 0.92)
    check("select_diverse: two identical -> 1 selected", len(sel) == 1)
    sel = M.select_diverse([v("A"), v("B")], 0.92)
    check("select_diverse: two orthogonal -> both kept", sorted(sel) == [0, 1])
    sel = M.select_diverse([v("A"), v("A"), v("B")], 0.92)
    check("select_diverse: {A,A,B} -> 2 selected (one A dropped)", len(sel) == 2)
    check("select_diverse: seed is the distinct response (lowest mean sim)",
          sel[0] == 2)
    check("select_diverse: exactly one of the identical A's survives",
          (0 in sel) ^ (1 in sel))
    sel = M.select_diverse([v("A"), v("B"), v("C")], 0.92)
    check("select_diverse: three diverse -> all kept", len(sel) == 3)
    sel = M.select_diverse([v("A"), v("An"), v("Am")], 0.92)
    check("select_diverse: near-dup pruned, moderate kept -> 2 selected",
          len(sel) == 2)
    check("select_diverse: single -> pass-through", M.select_diverse([v("A")], 0.92) == [0])
    check("select_diverse: empty -> []", M.select_diverse([], 0.92) == [])

def t_should_bypass():
    v = lambda k: list(_VEC[k])
    ok, mean = M.should_bypass([v("A"), v("A"), v("A")], 0.95)
    check("should_bypass: three identical > thr -> True", ok is True)
    check("should_bypass: mean_similarity ~= 1.0", abs(mean - 1.0) < 1e-9)
    ok, _ = M.should_bypass([v("A"), v("An")], 0.95)
    check("should_bypass: two near-identical (>0.95) -> True", ok is True)
    ok, _ = M.should_bypass([v("A"), v("A"), v("B")], 0.95)
    check("should_bypass: one divergent pair -> False", ok is False)
    ok, mean = M.should_bypass([v("A")], 0.95)
    check("should_bypass: <2 responses -> (False, 0.0)", ok is False and mean == 0.0)

def t_medoid():
    v = lambda k: list(_VEC[k])
    mi = M.medoid_index([v("A"), v("A"), v("B")])
    check("medoid_index: {A,A,B} -> an identical A (index 0/1)", mi in (0, 1))
    check("medoid_index: single -> 0", M.medoid_index([v("A")]) == 0)

def t_gates_off_noop():
    called = {"n": 0}

    async def _boom(_t):
        called["n"] += 1
        return [1.0, 0.0, 0.0]

    nodes = _nodes("A", "A")
    sel, byp = asyncio.get_event_loop().run_until_complete(
        M.apply_council_gates(nodes, embed_one=_boom,
                              diversity_gate=False, aggregator_bypass=False))
    check("gates off: nodes unchanged", sel is nodes and byp is None)
    check("gates off: embedder NOT called (zero model calls)", called["n"] == 0)

def t_diversity_gate_prunes():
    nodes = _nodes("A", "A")
    sel, byp = asyncio.get_event_loop().run_until_complete(
        M.apply_council_gates(nodes, embed_one=_mk_embed(),
                              diversity_gate=True, diversity_threshold=0.92,
                              aggregator_bypass=False))
    check("task_47 diversity: two identical -> 1 input kept", len(sel) == 1)
    check("task_47 diversity: no bypass", byp is None)
    nodes = _nodes("A", "B")
    sel, byp = asyncio.get_event_loop().run_until_complete(
        M.apply_council_gates(nodes, embed_one=_mk_embed(),
                              diversity_gate=True, aggregator_bypass=False))
    check("task_47 diversity: diverse pair kept whole", len(sel) == 2)

def t_aggregator_bypass():
    events = []
    aggregator_calls = {"n": 0}

    def _log(**kw):
        events.append(kw)

    nodes = _nodes("A", "A", "A")
    sel, byp = asyncio.get_event_loop().run_until_complete(
        M.apply_council_gates(nodes, embed_one=_mk_embed(),
                              diversity_gate=False,
                              aggregator_bypass=True,
                              aggregator_bypass_threshold=0.95,
                              log_event=_log))
    if byp is None:
        aggregator_calls["n"] += 1
    check("task_48 bypass: bypass returned (aggregator skipped)", byp is not None)
    check("task_48 bypass: aggregator LLM NOT called", aggregator_calls["n"] == 0)
    check("task_48 bypass: highest-confidence individual response returned",
          byp is not None and (byp.get("node") or {}).get("output") == "A")
    check("task_48 bypass: council_size == 3",
          byp is not None and byp.get("council_size") == 3)
    check("task_48 bypass: event logged kind=aggregator_bypass",
          len(events) == 1 and events[0].get("kind") == "aggregator_bypass")
    check("task_48 bypass: event carries council_size + mean_similarity",
          events[0].get("council_size") == 3
          and abs(events[0].get("mean_similarity") - 1.0) < 1e-9)

    events2 = []
    nodes = _nodes("A", "B", "C")
    sel, byp = asyncio.get_event_loop().run_until_complete(
        M.apply_council_gates(nodes, embed_one=_mk_embed(),
                              diversity_gate=False, aggregator_bypass=True,
                              aggregator_bypass_threshold=0.95,
                              log_event=lambda **k: events2.append(k)))
    check("task_48 bypass: divergent council -> no bypass", byp is None)
    check("task_48 bypass: divergent council -> no event", events2 == [])

def t_degrade_open_missing_embed():
    nodes = _nodes("A", "A", "MISS")
    sel, byp = asyncio.get_event_loop().run_until_complete(
        M.apply_council_gates(nodes, embed_one=_mk_embed(missing="MISS"),
                              diversity_gate=True, aggregator_bypass=True))
    check("degrade-open: missing embedding -> nodes unchanged, no bypass",
          sel is nodes and byp is None)
    nodes = _nodes("A")
    sel, byp = asyncio.get_event_loop().run_until_complete(
        M.apply_council_gates(nodes, embed_one=_mk_embed(),
                              diversity_gate=True, aggregator_bypass=True))
    check("degrade-open: <2 nodes -> no-op", sel is nodes and byp is None)

def t_stats_counter():
    M.reset_stats()
    check("stats: pct 0.0 when no opportunities", M.bypassed_pct() == 0.0)
    M.note_aggregator(bypassed=True)
    M.note_aggregator(bypassed=False)
    M.note_aggregator(bypassed=False)
    M.note_aggregator(bypassed=True)
    check("stats: 2/4 bypassed -> 50.0 pct", M.bypassed_pct() == 50.0)
    M.reset_stats()
    check("stats: reset -> 0.0", M.bypassed_pct() == 0.0)

def main():
    asyncio.set_event_loop(asyncio.new_event_loop())
    t_select_diverse()
    t_should_bypass()
    t_medoid()
    t_gates_off_noop()
    t_diversity_gate_prunes()
    t_aggregator_bypass()
    t_degrade_open_missing_embed()
    t_stats_counter()
    print(f"\n{_fails} FAILED" if _fails else "\nok")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_consensus.py (task_1006)
# ==============================================================================
# AI-hint: Stdlib offline unit tests for mios_pipe.routing.consensus -- the weighted multi-judge Definition-of-Done fold (CONS-01). No network / no DB / no...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stdlib offline unit tests for the weighted multi-judge consensus fold (CONS-01)."""

import sys

from mios_pipe.routing import consensus as M_consensus

_fails_consensus = 0

def _check_consensus(name, cond):
    global _fails_consensus
    if cond:
        print(f"ok   - {name}")
    else:
        _fails_consensus += 1
        print(f"FAIL - {name}")

def t_resolve_weights():
    w = M_consensus.resolve_weights(["a", "b", "c"])
    _check_consensus("weights: no reliability -> uniform", w == {"a": 1.0, "b": 1.0, "c": 1.0})

    w = M_consensus.resolve_weights(["a", "b"], {"a": 0.8, "b": 0.3})
    _check_consensus("weights: reliability passed through", w == {"a": 0.8, "b": 0.3})

    w = M_consensus.resolve_weights(["a", "b"], {"a": 0.0}, floor=0.25)
    _check_consensus("weights: zero score clamps to the floor", w["a"] == 0.25)
    _check_consensus("weights: unscored lane keeps the default", w["b"] == 1.0)

    w = M_consensus.resolve_weights(["a", "b"], {"a": "junk", "b": float("nan")})
    _check_consensus("weights: non-numeric score -> default", w["a"] == 1.0)
    _check_consensus("weights: NaN score -> default", w["b"] == 1.0)

def t_weighted_vote_basic():
    r = M_consensus.weighted_vote({"a": True, "b": True})
    _check_consensus("vote: unanimous yes -> True", r["decision"] is True)
    _check_consensus("vote: unanimous yes -> agreement 1.0", r["agreement"] == 1.0)

    r = M_consensus.weighted_vote({"a": False, "b": False})
    _check_consensus("vote: unanimous no -> False", r["decision"] is False)
    _check_consensus("vote: unanimous no -> agreement 1.0", r["agreement"] == 1.0)

    r = M_consensus.weighted_vote({"a": True, "b": False})
    _check_consensus("vote: even split at threshold 0.5 -> True", r["decision"] is True)
    _check_consensus("vote: even split -> score 0.5", abs(r["score"] - 0.5) < 1e-9)

    r = M_consensus.weighted_vote({"a": True, "b": False}, threshold=0.51)
    _check_consensus("vote: even split under a raised threshold -> False", r["decision"] is False)

def t_weighted_vote_resolves_conflict_by_weight():
    # Done-When: conflicting judges are resolved by WEIGHT, not by head-count.
    verdicts = {"trusted": True, "flaky_a": False, "flaky_b": False}
    weights = {"trusted": 3.0, "flaky_a": 0.5, "flaky_b": 0.5}
    r = M_consensus.weighted_vote(verdicts, weights)
    _check_consensus("vote: one heavy lane outvotes two light ones", r["decision"] is True)
    _check_consensus("vote: weighted score is 3/4", abs(r["score"] - 0.75) < 1e-9)

    r_unweighted = M_consensus.weighted_vote(verdicts)
    _check_consensus("vote: same verdicts unweighted flip to the majority",
          r_unweighted["decision"] is False)

    r = M_consensus.weighted_vote(verdicts, {"trusted": 3.0, "flaky_a": 0.0, "flaky_b": 0.5})
    _check_consensus("vote: a zero-weight lane is excluded from the fold",
          abs(r["score"] - (3.0 / 3.5)) < 1e-9)

def t_abstain_is_not_a_no():
    r = M_consensus.weighted_vote({"a": True, "b": None, "c": True})
    _check_consensus("abstain: dropped from the denominator", r["score"] == 1.0)
    _check_consensus("abstain: live count excludes it", r["live"] == 2)
    _check_consensus("abstain: quorum still formed", r["quorum"] is True)

    # The whole panel erroring must not read as a rejection.
    r = M_consensus.weighted_vote({"a": None, "b": None})
    _check_consensus("abstain: all lanes out -> no decision", r["decision"] is None)
    _check_consensus("abstain: all lanes out -> no quorum", r["quorum"] is False)

def t_quorum_gate():
    _check_consensus("quorum: two live votes reach the default min",
          M_consensus.quorum_reached({"a": True, "b": False}) is True)
    _check_consensus("quorum: one live vote does not",
          M_consensus.quorum_reached({"a": True, "b": None}) is False)

    r = M_consensus.weighted_vote({"a": True, "b": None})
    _check_consensus("quorum: sub-quorum panel returns decision=None", r["decision"] is None)
    _check_consensus("quorum: sub-quorum still reports the raw score", r["score"] == 1.0)

    r = M_consensus.weighted_vote({"a": True, "b": None}, min_lanes=1)
    _check_consensus("quorum: min_lanes=1 admits the single survivor", r["decision"] is True)

    r = M_consensus.weighted_vote({"a": True, "b": True}, min_lanes=3)
    _check_consensus("quorum: min_lanes above the panel size withholds a decision",
          r["decision"] is None)

def t_rrf():
    # 'y' is 2nd for one lane and 1st for the other; 'x' is the mirror image --
    # a tie. 'z' is ranked by one lane only and must land below both.
    fused = M_consensus.reciprocal_rank_fusion({"a": ["x", "y", "z"], "b": ["y", "x"]})
    names = [c for c, _ in fused]
    _check_consensus("rrf: both agreed candidates outrank the single-lane one",
          names.index("z") == 2)
    _check_consensus("rrf: scores descend", all(
        fused[i][1] >= fused[i + 1][1] for i in range(len(fused) - 1)))

    # A candidate two lanes both rank first beats one lane's first place.
    fused = M_consensus.reciprocal_rank_fusion({"a": ["p", "q"], "b": ["p", "r"], "c": ["q"]})
    _check_consensus("rrf: consensus first-place wins", fused[0][0] == "p")

    fused = M_consensus.reciprocal_rank_fusion({"a": ["x", "y"]}, {"a": 0.0})
    _check_consensus("rrf: a zero-weight lane contributes nothing", fused == [])

    heavy = M_consensus.reciprocal_rank_fusion(
        {"a": ["x", "y"], "b": ["y", "x"]}, {"a": 5.0, "b": 1.0})
    _check_consensus("rrf: weights break the tie toward the heavy lane",
          heavy[0][0] == "x")

    _check_consensus("rrf: empty input -> empty output",
          M_consensus.reciprocal_rank_fusion({}) == [])

    # k damps the head of each list: a larger k narrows the gap between ranks.
    tight = M_consensus.reciprocal_rank_fusion({"a": ["x", "y"]}, k=1000)
    loose = M_consensus.reciprocal_rank_fusion({"a": ["x", "y"]}, k=1)
    _check_consensus("rrf: larger k compresses the rank-1/rank-2 gap",
          (tight[0][1] - tight[1][1]) < (loose[0][1] - loose[1][1]))

def t_determinism():
    a = M_consensus.reciprocal_rank_fusion({"a": ["x", "y"], "b": ["y", "x"]})
    b = M_consensus.reciprocal_rank_fusion({"a": ["x", "y"], "b": ["y", "x"]})
    _check_consensus("rrf: tied candidates keep first-appearance order", a == b)
    _check_consensus("rrf: tie broken toward first appearance", a[0][0] == "x")

def _main_consensus():
    t_resolve_weights()
    t_weighted_vote_basic()
    t_weighted_vote_resolves_conflict_by_weight()
    t_abstain_is_not_a_no()
    t_quorum_gate()
    t_rrf()
    t_determinism()
    print(f"\n{_fails_consensus} FAILED" if _fails_consensus else "\nok")
    return 1 if _fails_consensus else 0


def _run_extra_consensus():
    try:
        return _main_consensus()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_council_diversity_suites():
    rc = _run_extra_consensus()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_council_diversity_suites()
    sys.exit(main())
