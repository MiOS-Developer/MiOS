# AI-hint: Pure, DB-free scoring core for the MiOS agentic-capability benchmark harness.
# AI-doc: usr/share/doc/mios/manual/scheduler.md

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

def comb_ratio(numer_n: int, numer_k: int, denom_n: int, denom_k: int) -> float:
    """C(numer_n, numer_k) / C(denom_n, denom_k), guarding the empty denominator.
    Returns 0.0 when the denominator combination is 0 (e.g. k > n)."""
    denom = math.comb(denom_n, denom_k) if denom_k <= denom_n else 0
    if denom == 0:
        return 0.0
    numer = math.comb(numer_n, numer_k) if numer_k <= numer_n else 0
    return numer / denom

def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k ("at least one of k succeeds") for n trials, c correct.
    = 1 - C(n-c, k)/C(n, k). pass@1 = c/n; all-correct -> 1; c==0 -> 0; k>n -> 0."""
    n, c, k = int(n), int(c), int(k)
    if k <= 0 or n <= 0 or k > n:
        return 0.0
    c = max(0, min(c, n))
    return 1.0 - comb_ratio(n - c, k, n, k)

def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased pass^k ("ALL k succeed", tau-bench reliability) for n trials, c
    correct. = C(c, k)/C(n, k). pass^1 = c/n; c==n -> 1; c<k -> 0; k>n -> 0.
    Always <= pass@1 <= pass@k (consistency is strictly harder)."""
    n, c, k = int(n), int(c), int(k)
    if k <= 0 or n <= 0 or k > n:
        return 0.0
    c = max(0, min(c, n))
    return comb_ratio(c, k, n, k)

def iid_pass_hat_k(p: float, k: int) -> float:
    """The i.i.d. closed form of pass^k: p**k. The intuition pump -- a 93%-reliable
    agent succeeds on all 8 of 8 trials only ~56% of the time."""
    p = max(0.0, min(1.0, float(p)))
    return p ** max(0, int(k))

def aggregate_pass_at_k(tasks: "Sequence[Tuple[int, int]]", k: int) -> float:
    """Mean pass@k across tasks. `tasks` = [(n_trials, c_correct), ...]. Tasks
    with fewer than k trials are skipped (can't estimate). 0.0 if none qualify."""
    vals = [pass_at_k(n, c, k) for (n, c) in tasks if n >= k and n > 0]
    return sum(vals) / len(vals) if vals else 0.0

def aggregate_pass_hat_k(tasks: "Sequence[Tuple[int, int]]", k: int) -> float:
    """Mean pass^k across tasks (the headline tau-bench reliability number).
    Tasks with fewer than k trials are skipped. 0.0 if none qualify."""
    vals = [pass_hat_k(n, c, k) for (n, c) in tasks if n >= k and n > 0]
    return sum(vals) / len(vals) if vals else 0.0

def aggregate_pass_and_k_rate(tasks: "Sequence[Tuple[int, int]]", k: int) -> float:
    vals = [1.0 if pass_hat_k(n, c, k) >= 1.0 else 0.0
            for (n, c) in tasks if n >= k and n > 0]
    return sum(vals) / len(vals) if vals else 0.0

def percentile(values: "Sequence[float]", q: float) -> float:
    """Linear-interpolation percentile (q in [0,100]) over `values`. [] -> 0.0."""
    xs = sorted(float(v) for v in values)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    q = max(0.0, min(100.0, float(q)))
    pos = (q / 100.0) * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def classic_rollup(records: "List[dict]", *, k: int = 1) -> dict:
    recs = [r for r in (records or []) if isinstance(r, dict)]
    n = len(recs)
    if n == 0:
        return {"n": 0, "n_tasks": 0, "cost_total": 0.0, "cost_mean": 0.0,
                "latency_p50": 0.0, "latency_p95": 0.0, "accuracy": 0.0,
                "stability": 0.0, "security": 1.0}
    costs = [float(r.get("cost") or 0.0) for r in recs]
    lats = [float(r.get("latency_ms") or 0.0) for r in recs]
    n_ok = sum(1 for r in recs if r.get("ok"))
    n_err = sum(1 for r in recs if r.get("error"))
    n_sec = sum(1 for r in recs if r.get("security_violation"))
    by_task: dict = {}
    for r in recs:
        t = str(r.get("task") or "")
        slot = by_task.setdefault(t, [0, 0])
        slot[0] += 1
        if r.get("ok"):
            slot[1] += 1
    if k > 1:
        stability = aggregate_pass_hat_k([(v[0], v[1]) for v in by_task.values()], k)
    else:
        stability = 1.0 - (n_err / n)
    return {
        "n": n,
        "n_tasks": len(by_task),
        "cost_total": round(sum(costs), 6),
        "cost_mean": round(sum(costs) / n, 6),
        "latency_p50": round(percentile(lats, 50), 3),
        "latency_p95": round(percentile(lats, 95), 3),
        "accuracy": round(n_ok / n, 6),
        "stability": round(stability, 6),
        "security": round(1.0 - (n_sec / n), 6),
    }

DEFAULT_UTILITY_WEIGHTS = {
    "accuracy": 0.40,
    "stability": 0.40,
    "latency": 0.10,
    "cost": 0.10,
    "security": 1.00,
}

def compute_utility(rollup_or_metrics: dict, weights: Optional[dict] = None) -> float:
    """Computes composite agent utility score according to DGM CLASSic criteria:
    Utility = w_acc * Accuracy + w_stab * Stability - w_lat * (Latency_p95 / 5000) - w_cost * Cost
    Subject to: Security == 1.0 (strict security penalty if security violations occur).
    """
    w = dict(DEFAULT_UTILITY_WEIGHTS)
    if weights:
        w.update(weights)

    acc = float(rollup_or_metrics.get("accuracy", rollup_or_metrics.get("pass_at_1", 0.0)))
    stab = float(rollup_or_metrics.get("stability", rollup_or_metrics.get("pass_hat_k", 0.0)))
    cost = float(rollup_or_metrics.get("cost_mean", rollup_or_metrics.get("cost", 0.0)))
    lat_ms = float(rollup_or_metrics.get("latency_p95", rollup_or_metrics.get("avg_wait", 0.0) * 1000.0))
    sec = float(rollup_or_metrics.get("security", 1.0))

    lat_norm = min(1.0, max(0.0, lat_ms / 5000.0))
    cost_norm = min(1.0, max(0.0, cost / 1.0))

    utility = (w["accuracy"] * acc) + (w["stability"] * stab) - (w["latency"] * lat_norm) - (w["cost"] * cost_norm)

    if sec < 1.0:
        sec_penalty = (1.0 - sec) * 5.0 * w["security"]
        utility -= sec_penalty

    return round(utility, 6)

def verify_positive_delta(baseline: Sequence[dict] | dict,
                          candidate: Sequence[dict] | dict,
                          *,
                          k: int = 1,
                          min_delta: float = 0.0,
                          allow_neutral: bool = False,
                          require_security: bool = True,
                          weights: Optional[dict] = None) -> tuple[bool, dict]:
    """Verifies that candidate changes produce a positive delta utility (or >= min_delta)
    over baseline results with zero regressions.
    Returns (passes_gate, detail_report).
    """
    base_recs = baseline if isinstance(baseline, list) else (baseline.get("results", []) if isinstance(baseline, dict) else [])
    cand_recs = candidate if isinstance(candidate, list) else (candidate.get("results", []) if isinstance(candidate, dict) else [])

    base_roll = classic_rollup(base_recs, k=k) if base_recs or not isinstance(baseline, dict) or "accuracy" not in baseline else baseline
    cand_roll = classic_rollup(cand_recs, k=k) if cand_recs or not isinstance(candidate, dict) or "accuracy" not in candidate else candidate

    base_by_task: dict = {}
    for r in base_recs:
        t = str(r.get("task") or "")
        slot = base_by_task.setdefault(t, [0, 0])
        slot[0] += 1
        if r.get("ok"):
            slot[1] += 1

    cand_by_task: dict = {}
    for r in cand_recs:
        t = str(r.get("task") or "")
        slot = cand_by_task.setdefault(t, [0, 0])
        slot[0] += 1
        if r.get("ok"):
            slot[1] += 1

    base_tasks = [(v[0], v[1]) for v in base_by_task.values()]
    cand_tasks = [(v[0], v[1]) for v in cand_by_task.values()]

    base_pass_1 = aggregate_pass_at_k(base_tasks, 1) if base_tasks else float(base_roll.get("accuracy", 0.0))
    cand_pass_1 = aggregate_pass_at_k(cand_tasks, 1) if cand_tasks else float(cand_roll.get("accuracy", 0.0))

    base_pass_k = aggregate_pass_at_k(base_tasks, k) if base_tasks else float(base_roll.get("accuracy", 0.0))
    cand_pass_k = aggregate_pass_at_k(cand_tasks, k) if cand_tasks else float(cand_roll.get("accuracy", 0.0))

    base_pass_hat = aggregate_pass_hat_k(base_tasks, k) if base_tasks else float(base_roll.get("stability", 0.0))
    cand_pass_hat = aggregate_pass_hat_k(cand_tasks, k) if cand_tasks else float(cand_roll.get("stability", 0.0))

    base_util = compute_utility(base_roll, weights)
    cand_util = compute_utility(cand_roll, weights)

    delta_utility = round(cand_util - base_util, 6)
    delta_accuracy = round(cand_roll["accuracy"] - base_roll["accuracy"], 6)
    delta_stability = round(cand_roll["stability"] - base_roll["stability"], 6)
    delta_pass_hat_k = round(cand_pass_hat - base_pass_hat, 6)
    delta_pass_at_k = round(cand_pass_k - base_pass_k, 6)
    delta_security = round(cand_roll["security"] - base_roll["security"], 6)

    regressions = []
    if require_security and cand_roll.get("security", 1.0) < 1.0:
        regressions.append(f"Security violation detected: security={cand_roll.get('security')}")

    if delta_accuracy < -1e-6:
        regressions.append(f"Accuracy regression: {cand_roll['accuracy']} < {base_roll['accuracy']} (delta={delta_accuracy})")

    if delta_stability < -1e-6 and k > 1:
        regressions.append(f"Stability/consistency regression: {cand_roll['stability']} < {base_roll['stability']} (delta={delta_stability})")

    if delta_utility < min_delta:
        if not (allow_neutral and abs(delta_utility - min_delta) <= 1e-6):
            regressions.append(f"Delta utility {delta_utility} < required min_delta {min_delta}")

    passes = len(regressions) == 0

    report = {
        "passed": passes,
        "k": k,
        "delta_utility": delta_utility,
        "delta_accuracy": delta_accuracy,
        "delta_stability": delta_stability,
        "delta_pass_at_k": delta_pass_at_k,
        "delta_pass_hat_k": delta_pass_hat_k,
        "delta_security": delta_security,
        "baseline": {
            "utility": base_util,
            "accuracy": base_roll["accuracy"],
            "stability": base_roll["stability"],
            "pass_at_1": round(base_pass_1, 4),
            "pass_at_k": round(base_pass_k, 4),
            "pass_hat_k": round(base_pass_hat, 4),
            "security": base_roll["security"],
            "cost_mean": base_roll["cost_mean"],
            "latency_p95": base_roll["latency_p95"],
        },
        "candidate": {
            "utility": cand_util,
            "accuracy": cand_roll["accuracy"],
            "stability": cand_roll["stability"],
            "pass_at_1": round(cand_pass_1, 4),
            "pass_at_k": round(cand_pass_k, 4),
            "pass_hat_k": round(cand_pass_hat, 4),
            "security": cand_roll["security"],
            "cost_mean": cand_roll["cost_mean"],
            "latency_p95": cand_roll["latency_p95"],
        },
        "regressions": regressions,
    }
    return passes, report

def evaluate_swe_bench_lite(records: Sequence[dict]) -> dict:
    """Evaluates task records against SWE-bench-lite criteria."""
    recs = [r for r in (records or []) if isinstance(r, dict)]
    total = len(recs)
    resolved = sum(1 for r in recs if r.get("ok") and not r.get("error"))
    applied = sum(1 for r in recs if r.get("patch_applied", True) and not r.get("error"))
    regressions = sum(1 for r in recs if r.get("regression"))

    res_rate = resolved / total if total > 0 else 0.0
    apply_rate = applied / total if total > 0 else 0.0

    return {
        "tasks": total,
        "resolved": resolved,
        "resolution_rate": round(res_rate, 4),
        "patch_applied_rate": round(apply_rate, 4),
        "regressions": regressions,
        "pass_at_1": round(res_rate, 4),
    }

def evaluate_tau_bench(records: Sequence[dict], *, k: int = 8) -> dict:
    """Evaluates multi-turn tool interaction consistency against tau-bench criteria."""
    by_task: dict = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        t = str(r.get("task") or "")
        slot = by_task.setdefault(t, [0, 0])
        slot[0] += 1
        if r.get("ok"):
            slot[1] += 1
    tasks = [(v[0], v[1]) for v in by_task.values()]

    return {
        "k": k,
        "tasks": len(tasks),
        "trials": len(records),
        "pass_at_1": round(aggregate_pass_at_k(tasks, 1), 4),
        "pass_at_k": round(aggregate_pass_at_k(tasks, k), 4),
        "pass_hat_k": round(aggregate_pass_hat_k(tasks, k), 4),
        "pass_and_k_rate": round(aggregate_pass_and_k_rate(tasks, k), 4),
    }

def dgm_gate(baseline_records: Sequence[dict] | dict,
             candidate_records: Sequence[dict] | dict,
             *, k: int = 8, min_delta: float = 0.0) -> tuple[bool, dict]:
    """Full Darwin Gödel Machine (DGM) self-improvement gate."""
    return verify_positive_delta(baseline_records, candidate_records, k=k, min_delta=min_delta)

