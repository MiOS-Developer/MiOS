#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_preempt (WS-A12 RR-preemption state machine + snapshot contract, PLUS the task_19/...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_preempt (WS-A12 RR primitives + task_19/task_19 turn seam)."""

import asyncio
import sys

import mios_preempt as pre

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def t_quantum():
    q = pre.Quantum(t0=100.0, limit_s=5.0)
    check("quantum: not expired before limit", q.expired(104.0) is False)
    check("quantum: expired at limit", q.expired(105.0) is True)
    check("quantum: expired after limit", q.expired(110.0) is True)
    check("quantum: remaining decreases", q.remaining(102.0) == 3.0, f"{q.remaining(102.0)}")
    check("quantum: remaining floors at 0", q.remaining(200.0) == 0.0)
    qinf = pre.Quantum(0.0, 0.0)
    check("quantum: limit<=0 never expires", qinf.expired(1e9) is False)
    check("quantum: limit<=0 remaining inf", qinf.remaining(1e9) == float("inf"))

def t_slots():
    s = pre.PreemptScheduler(max_suspended=2)
    check("slots: can_admit initially", s.can_admit() is True)
    a = s.acquire_slot(); b = s.acquire_slot()
    check("slots: two distinct ids", {a, b} == {0, 1}, f"{a},{b}")
    check("slots: exhausted -> can_admit False", s.can_admit() is False)
    check("slots: acquire when empty -> None", s.acquire_slot() is None)
    s.release_slot(a)
    check("slots: release frees a slot", s.can_admit() is True)
    s.release_slot(a)  # idempotent
    check("slots: double-release idempotent", s.acquire_slot() == a and s.acquire_slot() is None)

def t_suspend_resume():
    s = pre.PreemptScheduler(max_suspended=3)
    for tid, prio in [("low", 1.0), ("high", 9.0), ("mid", 5.0)]:
        slot = s.acquire_slot()
        ok = s.suspend(pre.Snapshot(tid, prio, position=0, partial="x", slot=slot))
        check(f"suspend: {tid} recorded", ok is True)
    check("suspend: duplicate rejected",
          s.suspend(pre.Snapshot("high", 9.0, 0, "y", 0)) is False)
    r1 = s.resume()
    check("resume: highest priority first (high)", r1.task_id == "high", r1.task_id)
    r2 = s.resume()
    check("resume: next highest (mid)", r2.task_id == "mid", r2.task_id)
    r3 = s.resume()
    check("resume: lowest last (low)", r3.task_id == "low")
    check("resume: empty -> None", s.resume() is None)
    check("resume: slots all freed", s.can_admit() is True and s.stats()["free_slots"] == 3)

def t_fifo_tiebreak():
    s = pre.PreemptScheduler(max_suspended=3)
    for tid in ["a", "b", "c"]:
        s.suspend(pre.Snapshot(tid, 5.0, 0, "x", s.acquire_slot()))  # equal priority
    check("tiebreak: equal priority resumes FIFO (a first)", s.resume().task_id == "a")

def t_stats():
    s = pre.PreemptScheduler(max_suspended=4)
    s.suspend(pre.Snapshot("t1", 5.0, 0, "x", s.acquire_slot()))
    st = s.stats()
    check("stats: shape", st["max_suspended"] == 4 and st["suspended"] == 1
          and st["free_slots"] == 3 and st["queued_ids"] == ["t1"], f"{st}")

def t_discharge():
    """discharge() frees a SPECIFIC task's slot (self-resume) without disturbing
    other suspended gens -- unlike resume() which pops the global highest."""
    s = pre.PreemptScheduler(max_suspended=3)
    for tid, prio in [("a", 1.0), ("b", 9.0)]:
        s.suspend(pre.Snapshot(tid, prio, 0, "x", s.acquire_slot()))
    check("discharge: a is suspended", s.is_suspended("a") is True)
    snap = s.discharge("a")
    check("discharge: returns the specific task (a, not the higher-prio b)",
          snap is not None and snap.task_id == "a", snap and snap.task_id)
    check("discharge: a no longer suspended", s.is_suspended("a") is False)
    check("discharge: b untouched", s.is_suspended("b") is True)
    check("discharge: a's slot freed, b's held", s.stats()["free_slots"] == 2)
    check("discharge: unknown task -> None", s.discharge("zzz") is None)

def t_decide():
    d = pre.decide
    check("decide: finished -> COMPLETE",
          d(finished=True, quantum_expired=True, higher_priority_waiting=True,
            can_suspend=True) == pre.COMPLETE)
    check("decide: finished -> COMPLETE even when idle",
          d(finished=True, quantum_expired=False, higher_priority_waiting=False,
            can_suspend=False) == pre.COMPLETE)
    check("decide: quantum+waiter+slot -> PREEMPT",
          d(finished=False, quantum_expired=True, higher_priority_waiting=True,
            can_suspend=True) == pre.PREEMPT)
    check("decide: no waiter -> CONTINUE",
          d(finished=False, quantum_expired=True, higher_priority_waiting=False,
            can_suspend=True) == pre.CONTINUE)
    check("decide: quantum not spent -> CONTINUE",
          d(finished=False, quantum_expired=False, higher_priority_waiting=True,
            can_suspend=True) == pre.CONTINUE)
    check("decide: no free slot -> CONTINUE (bounded suspension)",
          d(finished=False, quantum_expired=True, higher_priority_waiting=True,
            can_suspend=False) == pre.CONTINUE)

def t_rr_simulation():
    """End-to-end RR preemption over the pure primitives: a long LOW-priority
    generation is preempted when a HIGH-priority one appears -- snapshotting its
    partial -- HIGH completes first, then LOW resumes from EXACTLY its partial
    (no token lost or reprocessed) and finishes. Proves the snapshot contract
    preserves progress and that decide()+suspend()+resume() compose."""
    sched = pre.PreemptScheduler(max_suspended=2)
    SLICE, low_total, high_total = 4, 20, 8

    low_out = ""
    q = pre.Quantum(t0=0.0, limit_s=1.0)
    now = 0.0
    preempted = False
    while True:
        now += 1.0
        low_out += "L" * min(SLICE, low_total - len(low_out))
        high_waiting = now >= 2.0
        action = pre.decide(finished=len(low_out) >= low_total,
                            quantum_expired=q.expired(now),
                            higher_priority_waiting=high_waiting,
                            can_suspend=sched.can_admit())
        if action == pre.PREEMPT:
            slot = sched.acquire_slot()
            sched.suspend(pre.Snapshot("low", 1.0, position=len(low_out),
                                       partial=low_out, slot=slot))
            preempted = True
            break
        if action == pre.COMPLETE:
            break
    check("rr: LOW preempted before finishing", preempted and len(low_out) < low_total,
          f"out_len={len(low_out)}")
    check("rr: snapshot captured 1 suspended gen", sched.stats()["suspended"] == 1)

    high_out = ""
    qh = pre.Quantum(t0=now, limit_s=1.0)
    nowh = now
    while len(high_out) < high_total:
        nowh += 1.0
        high_out += "H" * min(SLICE, high_total - len(high_out))
        if pre.decide(finished=len(high_out) >= high_total,
                      quantum_expired=qh.expired(nowh),
                      higher_priority_waiting=False,
                      can_suspend=sched.can_admit()) == pre.COMPLETE:
            break
    check("rr: HIGH completed first", len(high_out) == high_total)

    snap = sched.resume()
    check("rr: resume returns LOW", snap.task_id == "low", snap.task_id)
    check("rr: resume starts at snapshot position", len(snap.partial) == snap.position)
    resumed = snap.partial
    while len(resumed) < low_total:
        resumed += "L" * min(SLICE, low_total - len(resumed))
    check("rr: LOW finished after resume (exact length, no dup)",
          len(resumed) == low_total, f"len={len(resumed)}")
    check("rr: LOW = partial + continuation (no reprocess)",
          resumed.startswith(snap.partial) and set(resumed) == {"L"})
    check("rr: slot freed after resume", sched.stats()["free_slots"] == 2)

def t_bounded_no_preempt():
    """No free snapshot slot -> decide() refuses to preempt, so the running gen
    finishes rather than being dropped (the bounded-suspension safety rule)."""
    sched = pre.PreemptScheduler(max_suspended=1)
    sched.suspend(pre.Snapshot("x", 5.0, 0, "p", sched.acquire_slot()))  # fill only slot
    action = pre.decide(finished=False, quantum_expired=True,
                        higher_priority_waiting=True, can_suspend=sched.can_admit())
    check("bounded: full slots -> CONTINUE (don't drop the gen)",
          action == pre.CONTINUE)

_PREEMPT_STATE_KEYS = ("PREEMPT_ENABLE", "QUEUE_ENABLE", "TURN_QUANTUM_S",
                       "SLICE_TOKENS", "PRIORITY_LEVELS", "MAX_PREEMPT_DEPTH",
                       "_TURN_SCHEDULER", "_TURN_QUEUE", "_HEAD_PRIORITY", "_CLOCK")

def _snapshot_state():
    return {k: getattr(pre, k) for k in _PREEMPT_STATE_KEYS}

def _restore_state(snap):
    for k, v in snap.items():
        setattr(pre, k, v)

class _SpySched:
    """Records every PreemptScheduler method the hook calls (consult evidence)."""

    def __init__(self, admit=True, slot=0, suspend_ok=True):
        self.calls = []
        self._admit, self._slot, self._suspend_ok = admit, slot, suspend_ok

    def can_admit(self):
        self.calls.append("can_admit")
        return self._admit

    def acquire_slot(self):
        self.calls.append("acquire_slot")
        return self._slot

    def suspend(self, snap):
        self.calls.append("suspend")
        return self._suspend_ok

    def release_slot(self, slot):
        self.calls.append("release_slot")

    def discharge(self, task_id):
        self.calls.append("discharge")
        return None

    def stats(self):
        return {}

class _SpyQueue:
    """Records every TokenSliceQueue method slice_boundary calls (consult evidence)."""

    def __init__(self):
        self.calls = []

    def account(self, task_id, tokens):
        self.calls.append("account")
        return False

    def requeue(self, task_id):
        self.calls.append("requeue")

    def head_priority(self, exclude=None):
        self.calls.append("head_priority")
        return None

    def stats(self):
        return {}

def t_as_bool():
    check("as_bool: real bools pass through",
          pre._as_bool(True) is True and pre._as_bool(False) is False)
    check("as_bool: off-tokens -> False",
          all(pre._as_bool(x) is False for x in ("false", "0", "no", "off", "", "FALSE")))
    check("as_bool: on-tokens -> True",
          pre._as_bool("true") is True and pre._as_bool("1") is True and pre._as_bool("on") is True)
    check("as_bool: None -> default",
          pre._as_bool(None) is False and pre._as_bool(None, True) is True)

def t_scheduler_cfg_defaults():
    cfg = pre._scheduler_cfg()
    check("scheduler_cfg: keys == fallback keys", set(cfg) == set(pre._SCHEDULER_FALLBACK),
          f"{set(cfg)}")
    check("scheduler_cfg: preempt_enable DEFAULT-OFF", cfg["preempt_enable"] is False, f"{cfg}")
    check("scheduler_cfg: quantum/max_suspended/depth defaults",
          cfg["max_suspended"] == 4 and cfg["quantum_s"] == 8.0
          and cfg["max_preempt_depth"] == 1 and cfg["priority_levels"] == 0, f"{cfg}")

def t_turn_boundary_disabled():
    """DEFAULT-OFF: the hook is a no-op -- returns False and NEVER consults the
    scheduler (proving byte-identical no-preemption behaviour)."""
    snap = _snapshot_state()
    try:
        spy = _SpySched()
        pre.configure(preempt_enable=False, turn_scheduler=spy, head_priority=lambda: 9.0)
        preempted = asyncio.run(pre.turn_boundary(task_id="t1", priority=1.0))
        check("turn_boundary disabled: returns False", preempted is False)
        check("turn_boundary disabled: scheduler NOT consulted (no-op)", spy.calls == [],
              f"{spy.calls}")
    finally:
        _restore_state(snap)

def t_turn_boundary_enabled_roundtrip():
    """ENABLED + a higher-priority waiter: the scheduler IS consulted and the
    snapshot/resume round-trips on a REAL PreemptScheduler (suspended 0->...->0,
    the acquired slot freed again)."""
    snap = _snapshot_state()
    try:
        real = pre.PreemptScheduler(max_suspended=2)
        pre.configure(preempt_enable=True, turn_scheduler=real,
                      head_priority=lambda: 9.0, quantum_s=8.0, max_preempt_depth=1)
        before = real.stats()
        preempted = asyncio.run(pre.turn_boundary(task_id="t1", priority=1.0))
        after = real.stats()
        check("turn_boundary enabled: preempted (higher waiter)", preempted is True)
        check("turn_boundary enabled: snapshot resumed (0 suspended after)",
              after["suspended"] == 0, f"{after}")
        check("turn_boundary enabled: slot freed after resume (balanced)",
              after["free_slots"] == before["free_slots"] == 2, f"{before}/{after}")
    finally:
        _restore_state(snap)

def t_turn_boundary_consulted_spy():
    """ENABLED: the exact consult sequence (can_admit -> acquire_slot -> suspend ->
    ... -> discharge) fires, with suspend BEFORE discharge (the round-trip). The
    head-priority probe clearing mid-yield exercises the cooperative-yield exit."""
    snap = _snapshot_state()
    try:
        spy = _SpySched()
        flips = iter([True, True, False])  # decide-arg True, loop True, then clears

        def _hp():
            try:
                return 9.0 if next(flips) else 0.0
            except StopIteration:
                return 0.0

        pre.configure(preempt_enable=True, turn_scheduler=spy, head_priority=_hp,
                      max_preempt_depth=5)
        preempted = asyncio.run(pre.turn_boundary(task_id="t1", priority=1.0))
        check("turn_boundary consulted: preempted", preempted is True)
        check("turn_boundary consulted: full round-trip sequence",
              {"can_admit", "acquire_slot", "suspend", "discharge"} <= set(spy.calls)
              and spy.calls.index("suspend") < spy.calls.index("discharge"),
              f"{spy.calls}")
    finally:
        _restore_state(snap)

def t_turn_boundary_no_higher_waiter():
    """ENABLED but no higher-priority waiter -> decide() returns CONTINUE -> no
    preemption, nothing suspended."""
    snap = _snapshot_state()
    try:
        real = pre.PreemptScheduler(max_suspended=2)
        pre.configure(preempt_enable=True, turn_scheduler=real, head_priority=lambda: 1.0)
        preempted = asyncio.run(pre.turn_boundary(task_id="t1", priority=5.0))
        check("turn_boundary no-waiter: not preempted", preempted is False)
        check("turn_boundary no-waiter: nothing suspended",
              real.stats()["suspended"] == 0 and real.stats()["free_slots"] == 2)
    finally:
        _restore_state(snap)

def t_turn_boundary_unwired_probe():
    """ENABLED but the head-priority probe is unwired (None) -> no signal -> never
    preempts (degrade-open on a missing dependency)."""
    snap = _snapshot_state()
    try:
        pre.configure(preempt_enable=True, turn_scheduler=pre.PreemptScheduler(2),
                      head_priority=None)
        preempted = asyncio.run(pre.turn_boundary(task_id="t1", priority=1.0))
        check("turn_boundary unwired: not preempted (no probe signal)", preempted is False)
    finally:
        _restore_state(snap)

def t_turn_boundary_degrade_open():
    """DEGRADE-OPEN: a scheduler that raises mid-consult must NOT propagate -- the
    hook returns False and the SYNTHETIC turn still runs to completion."""
    snap = _snapshot_state()
    try:
        class _RaisingSched:
            def can_admit(self):
                return True

            def acquire_slot(self):
                raise RuntimeError("boom")

            def discharge(self, task_id):
                return None

            def stats(self):
                return {}

        pre.configure(preempt_enable=True, turn_scheduler=_RaisingSched(),
                      head_priority=lambda: 9.0)
        ran = {"v": False}

        async def synthetic_turn():
            preempted = await pre.turn_boundary(task_id="t1", priority=1.0)
            ran["v"] = True  # the turn body runs AFTER the (degrade-open) boundary
            return preempted, "turn-output"

        preempted, out = asyncio.run(synthetic_turn())
        check("turn_boundary degrade-open: no raise, returns False", preempted is False)
        check("turn_boundary degrade-open: turn still executes",
              ran["v"] is True and out == "turn-output")
    finally:
        _restore_state(snap)

def t_turn_boundary_quantum_backstop():
    """A persistently-waiting higher-priority turn + a huge depth must STILL
    terminate when the quantum elapses (the time backstop), via an injected clock."""
    snap = _snapshot_state()
    try:
        t = [0.0]

        def clk():
            t[0] += 5.0  # each read advances 5s -> quantum (8s) elapses by the 2nd check
            return t[0]

        real = pre.PreemptScheduler(2)
        pre.configure(preempt_enable=True, turn_scheduler=real, head_priority=lambda: 9.0,
                      quantum_s=8.0, max_preempt_depth=10_000, clock=clk)
        preempted = asyncio.run(pre.turn_boundary(task_id="t1", priority=1.0))
        check("turn_boundary quantum-backstop: terminates + preempts", preempted is True)
        check("turn_boundary quantum-backstop: slot freed", real.stats()["suspended"] == 0)
    finally:
        _restore_state(snap)

def t_configure_aliases_and_stats():
    snap = _snapshot_state()
    try:
        sd = pre.PreemptScheduler(3)
        clk = lambda: 123.0  # noqa: E731 -- terse stub clock
        pre.configure(preempt_enable=True, quantum_s=2.5, priority_levels=4,
                      max_preempt_depth=7, turn_scheduler=sd, clock=clk,
                      head_priority=lambda: 1.0, bogus_key="ignored")
        check("configure: enable + numeric aliases land",
              pre.PREEMPT_ENABLE is True and pre.TURN_QUANTUM_S == 2.5
              and pre.PRIORITY_LEVELS == 4 and pre.MAX_PREEMPT_DEPTH == 7)
        check("configure: object aliases land", pre._TURN_SCHEDULER is sd and pre._CLOCK is clk)
        check("configure: unknown key ignored", not hasattr(pre, "bogus_key"))
        st = pre.turn_scheduler_stats()
        check("turn_scheduler_stats: shape",
              st["preempt_enable"] is True and st["quantum_s"] == 2.5
              and st["priority_levels"] == 4 and st["max_preempt_depth"] == 7
              and isinstance(st["scheduler"], dict), f"{st}")
    finally:
        _restore_state(snap)

def t_queue_cfg_defaults():
    cfg = pre._scheduler_cfg()
    check("queue_cfg: queue_enable DEFAULT-OFF", cfg["queue_enable"] is False, f"{cfg}")
    check("queue_cfg: slice_tokens / queue_max_turns defaults",
          cfg["slice_tokens"] == 256 and cfg["queue_max_turns"] == 64, f"{cfg}")

def t_token_slice_queue_structure():
    """Pure ordering: dispatch() yields the highest-priority READY turn first (FIFO
    tie-break), marks it running, and requeue()/remove() move turns back/out."""
    q = pre.TokenSliceQueue(default_slice_tokens=4)
    check("queue: enqueue new -> True", q.enqueue("a", 1.0) is True)
    check("queue: re-enqueue known -> False (refresh, no dup)", q.enqueue("a", 2.0) is False)
    q.enqueue("b", 9.0)
    q.enqueue("c", 5.0)
    check("queue: 3 distinct tracked", q.stats()["turns"] == 3, f"{q.stats()}")
    check("queue: dispatch highest first (b)", q.dispatch() == "b")
    check("queue: dispatch next-highest (c)", q.dispatch() == "c")
    check("queue: dispatch lowest last (a)", q.dispatch() == "a")
    check("queue: nothing ready -> None", q.dispatch() is None)
    check("queue: all running", q.stats()["running"] == 3 and q.stats()["ready"] == 0,
          f"{q.stats()}")
    q.requeue("b")
    check("queue: requeue -> ready again, re-dispatched by priority", q.dispatch() == "b")
    check("queue: remove drops the turn", q.remove("a") is not None and q.is_tracked("a") is False)

def t_token_slice_queue_fifo_tiebreak():
    q = pre.TokenSliceQueue()
    for tid in ("x", "y", "z"):
        q.enqueue(tid, 5.0)  # equal priority -> FIFO by enqueue order
    check("queue tiebreak: equal priority dispatches FIFO (x first)", q.dispatch() == "x")

def t_token_slice_account():
    """account() trips a boundary only when the slice budget is crossed, carrying the
    remainder; budget 0 = slicing off; unknown task / bad count degrade to False."""
    q = pre.TokenSliceQueue(default_slice_tokens=4)
    q.enqueue("t", 5.0)
    check("account: below budget -> False", q.account("t", 2) is False)
    check("account: crossing budget -> True (slice boundary)", q.account("t", 3) is True)
    check("account: remainder (1) carried -> +3 reaches budget again", q.account("t", 3) is True)
    check("account: after carry, small step below budget -> False", q.account("t", 1) is False)
    q0 = pre.TokenSliceQueue(default_slice_tokens=0)
    q0.enqueue("z", 5.0)
    check("account: budget 0 -> slicing off, never a boundary", q0.account("z", 10_000) is False)
    check("account: unknown task -> False (degrade-open)", q.account("nope", 99) is False)
    check("account: non-numeric tokens -> False (degrade-open)", q.account("t", "oops") is False)

def t_token_slice_head_priority():
    """head_priority is the highest-priority READY (not-running) waiter, excluding the
    asker -- the 'is a higher-priority turn waiting?' signal slice_boundary feeds."""
    q = pre.TokenSliceQueue(default_slice_tokens=4)
    q.enqueue("low", 1.0)
    q.dispatch()  # low -> running
    check("head: only a running turn -> None waiting", q.head_priority() is None)
    q.enqueue("high", 9.0)  # ready waiter
    check("head: ready waiter seen", q.head_priority() == 9.0)
    check("head: exclude=running-self still sees the waiter", q.head_priority(exclude="low") == 9.0)
    check("head: exclude the waiter -> None", q.head_priority(exclude="high") is None)

def t_token_slice_queue_bounded():
    """Bounded advisory capacity: a new turn over the cap evicts the OLDEST READY row;
    a RUNNING turn is never evicted (its coroutine is unaffected -- degrade-open)."""
    q = pre.TokenSliceQueue(default_slice_tokens=4, max_turns=2)
    q.enqueue("a", 5.0)
    q.enqueue("b", 5.0)
    q.enqueue("c", 5.0)  # over cap -> evict oldest ready (a)
    check("bounded: cap respected", q.stats()["turns"] == 2, f"{q.stats()}")
    check("bounded: oldest ready evicted (a gone, c kept)",
          q.is_tracked("a") is False and q.is_tracked("c") is True)
    q2 = pre.TokenSliceQueue(default_slice_tokens=4, max_turns=2)
    q2.enqueue("r", 5.0)
    q2.dispatch()         # r -> running
    q2.enqueue("s", 5.0)  # ready
    q2.enqueue("t", 5.0)  # over cap -> evict oldest READY (s), keep running r
    check("bounded: running turn never evicted", q2.is_tracked("r") is True)
    check("bounded: oldest ready evicted not the running one",
          q2.is_tracked("s") is False and q2.is_tracked("t") is True)

def t_slice_boundary_disabled():
    """DEFAULT-OFF ([scheduler].queue_enable=false): slice_boundary is a no-op --
    returns False and NEVER consults the queue (proving no interposition / byte-
    identical admission)."""
    snap = _snapshot_state()
    try:
        spyq = _SpyQueue()
        pre.configure(queue_enable=False, turn_queue=spyq, preempt_enable=True,
                      head_priority=lambda: 9.0)
        preempted = asyncio.run(pre.slice_boundary(task_id="t1", priority=1.0, tokens=10_000))
        check("slice_boundary disabled: returns False", preempted is False)
        check("slice_boundary disabled: queue NOT consulted (no interposition)",
              spyq.calls == [], f"{spyq.calls}")
    finally:
        _restore_state(snap)

def t_slice_boundary_triggers_reeval():
    """ENABLED: slice_boundary accounts tokens; only when the slice budget is CROSSED
    does it consult turn_boundary (the re-eval). A higher-priority turn waiting in the
    queue is then dispatched-before via preemption + the preempted turn is requeued."""
    snap = _snapshot_state()
    try:
        realq = pre.TokenSliceQueue(default_slice_tokens=4, max_turns=8)
        realq.enqueue("low", 1.0)
        realq.dispatch()          # low -> running
        realq.enqueue("high", 9.0)  # higher-priority waiter (ready)
        pre.configure(queue_enable=True, preempt_enable=True, turn_queue=realq,
                      turn_scheduler=pre.PreemptScheduler(max_suspended=2),
                      head_priority=lambda: None, quantum_s=8.0, max_preempt_depth=1)
        p1 = asyncio.run(pre.slice_boundary(task_id="low", priority=1.0, tokens=2))
        check("slice_boundary below budget: no boundary -> not preempted", p1 is False)
        check("slice_boundary below budget: low still running",
              realq.stats()["running"] == 1, f"{realq.stats()}")
        p2 = asyncio.run(pre.slice_boundary(task_id="low", priority=1.0, tokens=2))
        check("slice_boundary crossing budget: re-eval preempts (higher queue waiter)",
              p2 is True)
        check("slice_boundary crossing budget: preempted turn requeued (MLFQ decayed priority)",
              realq.head_priority(exclude="high") == 0.0, f"{realq.stats()}")
    finally:
        _restore_state(snap)

def t_slice_boundary_no_higher_waiter():
    """ENABLED but no higher-priority queue waiter -> a crossed boundary re-evaluates
    yet does not preempt (the running turn keeps the lane)."""
    snap = _snapshot_state()
    try:
        realq = pre.TokenSliceQueue(default_slice_tokens=4, max_turns=8)
        realq.enqueue("hi", 9.0)
        realq.dispatch()
        realq.enqueue("lo", 1.0)  # only a LOWER-priority waiter
        pre.configure(queue_enable=True, preempt_enable=True, turn_queue=realq,
                      turn_scheduler=pre.PreemptScheduler(2), head_priority=lambda: None)
        preempted = asyncio.run(pre.slice_boundary(task_id="hi", priority=9.0, tokens=99))
        check("slice_boundary no-higher-waiter: boundary crossed but not preempted",
              preempted is False)
    finally:
        _restore_state(snap)

def t_slice_boundary_text_counts_via_tokenize():
    """The token-time accounting routes `text` through the mios_tokenize seam (NOT a
    re-derived //N): a short string stays within the slice (no preempt), a long one
    crosses it (preempt). Proves the seam -- not an inline char count -- is used."""
    snap = _snapshot_state()
    try:
        import mios_tokenize
        realq = pre.TokenSliceQueue(default_slice_tokens=8, max_turns=8)
        realq.enqueue("low", 1.0)
        realq.dispatch()
        realq.enqueue("high", 9.0)
        pre.configure(queue_enable=True, preempt_enable=True, turn_queue=realq,
                      turn_scheduler=pre.PreemptScheduler(2), head_priority=lambda: None,
                      quantum_s=8.0, max_preempt_depth=1)
        short = "ab"
        check("tokenize: short text is below the slice budget",
              mios_tokenize.count_text(short) < 8, f"{mios_tokenize.count_text(short)}")
        p_short = asyncio.run(pre.slice_boundary(task_id="low", priority=1.0, text=short))
        check("slice_boundary text(short): no boundary -> not preempted", p_short is False)
        long = "x" * 64
        check("tokenize: long text exceeds the slice budget",
              mios_tokenize.count_text(long) >= 8, f"{mios_tokenize.count_text(long)}")
        p_long = asyncio.run(pre.slice_boundary(task_id="low", priority=1.0, text=long))
        check("slice_boundary text(long, counted via mios_tokenize): boundary -> preempted",
              p_long is True)
    finally:
        _restore_state(snap)

def t_slice_boundary_degrade_open():
    """DEGRADE-OPEN: a queue that raises mid-consult must NOT propagate -- slice_boundary
    returns False and the SYNTHETIC turn still runs to completion."""
    snap = _snapshot_state()
    try:
        class _RaisingQueue:
            def account(self, task_id, tokens):
                raise RuntimeError("boom")

            def stats(self):
                return {}

        pre.configure(queue_enable=True, preempt_enable=True,
                      turn_queue=_RaisingQueue(), head_priority=lambda: 9.0)
        ran = {"v": False}

        async def synthetic_turn():
            preempted = await pre.slice_boundary(task_id="t1", priority=1.0, tokens=10)
            ran["v"] = True  # the turn body runs AFTER the (degrade-open) boundary
            return preempted, "turn-output"

        preempted, out = asyncio.run(synthetic_turn())
        check("slice_boundary degrade-open: no raise, returns False", preempted is False)
        check("slice_boundary degrade-open: turn still executes",
              ran["v"] is True and out == "turn-output")
    finally:
        _restore_state(snap)

def t_turn_boundary_default_off_ignores_queue():
    """Regression guard: with queue_enable OFF, _higher_priority_waiting is byte-
    identical to the task_19 probe-only path even if the queue holds a higher-priority
    turn -- so the queue can never silently change default-off preemption."""
    snap = _snapshot_state()
    try:
        realq = pre.TokenSliceQueue(default_slice_tokens=4)
        realq.enqueue("waiter", 9.0)  # a high-priority turn sits in the queue
        pre.configure(preempt_enable=True, queue_enable=False, turn_queue=realq,
                      turn_scheduler=pre.PreemptScheduler(2), head_priority=lambda: None)
        preempted = asyncio.run(pre.turn_boundary(task_id="t1", priority=1.0))
        check("turn_boundary queue-off: queue waiter ignored -> not preempted",
              preempted is False)
    finally:
        _restore_state(snap)

def main():
    t_quantum()
    t_slots()
    t_suspend_resume()
    t_fifo_tiebreak()
    t_stats()
    t_discharge()
    t_decide()
    t_rr_simulation()
    t_bounded_no_preempt()
    t_as_bool()
    t_scheduler_cfg_defaults()
    t_turn_boundary_disabled()
    t_turn_boundary_enabled_roundtrip()
    t_turn_boundary_consulted_spy()
    t_turn_boundary_no_higher_waiter()
    t_turn_boundary_unwired_probe()
    t_turn_boundary_degrade_open()
    t_turn_boundary_quantum_backstop()
    t_configure_aliases_and_stats()
    t_queue_cfg_defaults()
    t_token_slice_queue_structure()
    t_token_slice_queue_fifo_tiebreak()
    t_token_slice_account()
    t_token_slice_head_priority()
    t_token_slice_queue_bounded()
    t_slice_boundary_disabled()
    t_slice_boundary_triggers_reeval()
    t_slice_boundary_no_higher_waiter()
    t_slice_boundary_text_counts_via_tokenize()
    t_slice_boundary_degrade_open()
    t_turn_boundary_default_off_ignores_queue()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
