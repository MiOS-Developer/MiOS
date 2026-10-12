# AI-hint: Standalone unit test for mios_reputation (#54 peer reputation): neutral-with-no-history, success-rate scoring, recent-failure penalty, and STAB...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Standalone unit test for mios_reputation (WS / #54 zero-trust federation).

Pure stdlib + the sibling module only -- no server.py. Proves the deterministic
properties the peer selector relies on, especially that an all-neutral list is
returned unchanged (so reputation never alters behaviour until peers have a
track record).

Run:  python test_mios_reputation.py
"""

import sys

import mios_reputation as R
import mios_pipe.identity.reputation as identity_reputation

_RESULTS: list = []

def _check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_neutral() -> None:
    r = R.PeerReputation()
    _check("neutral: unknown peer -> 0.5", r.score("nobody") == R.NEUTRAL)
    _check("neutral: empty id ignored", (r.record("", True) or True) and r.score("") == R.NEUTRAL)

def t_scoring() -> None:
    r = R.PeerReputation()
    r.record("good", False)
    for _ in range(8):
        r.record("good", True)
    g = r.score("good")
    _check("scoring: mostly-ok scores high", g > 0.7, f"{g:.3f}")
    r2 = R.PeerReputation()
    for _ in range(8):
        r2.record("bad", False)
    b = r2.score("bad")
    _check("scoring: mostly-bad scores low", b < 0.2, f"{b:.3f}")
    _check("scoring: good > bad", g > b)

def t_recent_penalty() -> None:
    r = R.PeerReputation(recent_penalty=0.15)
    for _ in range(20):
        r.record("p", True)
    high = r.score("p")
    for _ in range(3):
        r.record("p", False)
    after = r.score("p")
    _check("recent: streak of failures drops score fast", after < high - 0.3,
           f"{high:.3f} -> {after:.3f}")
    r.record("p", True)   # a success resets the streak penalty
    _check("recent: a success clears the streak penalty", r.score("p") > after,
           f"{after:.3f} -> {r.score('p'):.3f}")

def t_rank_stable() -> None:
    r = R.PeerReputation()
    order = ["a", "b", "c", "d"]
    _check("rank: all-neutral preserves input order", r.rank(order) == order,
           str(r.rank(order)))
    _check("rank: empty list ok", r.rank([]) == [])

def t_rank_prefers_reliable() -> None:
    r = R.PeerReputation()
    for _ in range(10):
        r.record("reliable", True)
    for _ in range(10):
        r.record("flaky", False)
    ranked = r.rank(["flaky", "reliable", "fresh"])
    _check("rank: reliable first", ranked[0] == "reliable", str(ranked))
    _check("rank: flaky last", ranked[-1] == "flaky", str(ranked))
    _check("rank: fresh (neutral) in the middle", ranked[1] == "fresh", str(ranked))
    snap = r.snapshot()
    _check("snapshot: carries score", "score" in snap.get("reliable", {}))

def t_persistence() -> None:
    r = R.PeerReputation()
    for _ in range(5):
        r.record("p1", True)
    r.record("p1", False)            # p1: ok=5 bad=1 streak_bad=1
    for _ in range(3):
        r.record("p2", False)        # p2: ok=0 bad=3 streak_bad=3
    rows = r.rows()
    _check("rows: one per peer, raw counters", len(rows) == 2 and "score" not in rows[0],
           str(rows))
    _check("rows: deterministic sort", [x["peer_id"] for x in rows] == ["p1", "p2"])
    r2 = R.PeerReputation()
    r2.restore(rows)
    _check("restore: round-trips score", abs(r2.score("p1") - r.score("p1")) < 1e-9
           and abs(r2.score("p2") - r.score("p2")) < 1e-9,
           f"{r2.score('p1'):.3f}/{r2.score('p2'):.3f}")
    _check("restore: streak_bad preserved", r2.rows()[1]["streak_bad"] == 3)
    r3 = R.PeerReputation()
    r3.restore([{"peer_id": "ok", "ok": 2, "bad": 0, "streak_bad": 0},
                {"no_id": 1}, None, {"peer_id": "", "ok": 9}])
    _check("restore: skips malformed, keeps valid", r3.rows() == [
        {"peer_id": "ok", "ok": 2, "bad": 0, "streak_bad": 0}], str(r3.rows()))
    _check("restore: replaces prior state", (r2.restore([]) or True) and r2.rows() == [])

def t_federation_reexport() -> None:
    """Every check above runs on mios_pipe.identity.reputation's PeerReputation:
    mios_reputation re-exports it for the A2A daemons, which call .score(),
    .restore() and .rows() on it. Aliasing it to the task_288 session
    ReputationEngine once removed that API from the federation path."""
    _check("reexport: PeerReputation is identity.reputation's class",
           R.PeerReputation is identity_reputation.PeerReputation)
    _check("reexport: NEUTRAL is identity.reputation's", R.NEUTRAL == identity_reputation.NEUTRAL)
    api = ("record", "score", "rank", "snapshot", "rows", "restore")
    missing = [a for a in api if not callable(getattr(R.PeerReputation, a, None))]
    _check("reexport: the federation API is all there", not missing, str(missing))
    _check("reexport: not aliased to the session ReputationEngine",
           R.PeerReputation is not getattr(R, "ReputationEngine", None))

def main() -> int:
    for t in (t_neutral, t_scoring, t_recent_penalty, t_rank_stable,
              t_rank_prefers_reliable, t_persistence, t_federation_reexport):
        t()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
