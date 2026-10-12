#!/usr/bin/env python3
# AI-hint: Two-sided test suite for the Darwin Gödel Machine (DGM) self-improvement meta-agentic loop and pre-commit gate.
# AI-doc: usr/share/doc/mios/manual/scheduler.md
# AI-related: usr/libexec/mios/mios-bench, usr/lib/mios/agent-pipe/mios_pipe/scheduler/bench.py, usr/share/git-core/templates/hooks/pre-commit.d/10-dgm-bench.sh


from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

# Add repo lib to sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, ".."))
_LIB = os.path.join(_REPO, "usr", "lib", "mios", "agent-pipe")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

try:
    import mios_pipe.scheduler.bench as b
except ImportError:
    try:
        import mios_bench as b
    except ImportError as e:
        sys.stderr.write(f"Cannot import bench scoring core: {e}\n")
        sys.exit(2)

MIOS_BENCH = os.path.join(_REPO, "usr", "libexec", "mios", "mios-bench")
HOOK_SCRIPT = os.path.join(_REPO, "usr", "share", "git-core", "templates", "hooks", "pre-commit.d", "10-dgm-bench.sh")

_fails = 0

def check(name: str, cond: bool, detail: str = ""):
    global _fails
    if not cond:
        _fails += 1
    status = "PASS" if cond else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)

def _close(a: float, c: float, tol: float = 1e-6) -> bool:
    return abs(a - c) <= tol


# ------------------------------------------------------------------------------
# Test 1: Mathematical precision of pass@k and unbiased pass^k formulas
# ------------------------------------------------------------------------------
def test_math_formulas():
    print("\n--- Test 1: Mathematical Formulas (pass@k, pass^k) ---")
    # Exact values
    check("pass@1 equals c/n (10 trials, 7 correct)", _close(b.pass_at_k(10, 7, 1), 0.7))
    check("pass^1 equals c/n (10 trials, 7 correct)", _close(b.pass_hat_k(10, 7, 1), 0.7))

    # Boundary conditions
    check("pass@k all correct equals 1.0", _close(b.pass_at_k(5, 5, 3), 1.0))
    check("pass^k all correct equals 1.0", _close(b.pass_hat_k(5, 5, 3), 1.0))
    check("pass@k zero correct equals 0.0", _close(b.pass_at_k(5, 0, 3), 0.0))
    check("pass^k zero correct equals 0.0", _close(b.pass_hat_k(5, 0, 3), 0.0))
    check("pass@k k > n returns 0.0", b.pass_at_k(3, 2, 5) == 0.0)
    check("pass^k k > n returns 0.0", b.pass_hat_k(3, 3, 5) == 0.0)
    check("pass^k c < k returns 0.0", b.pass_hat_k(5, 2, 3) == 0.0)

    # Combinatorial verification: n=4, c=2, k=2
    # pass@2 = 1 - C(2,2)/C(4,2) = 1 - 1/6 = 5/6
    check("pass@2 exact (4,2,2)=5/6", _close(b.pass_at_k(4, 2, 2), 5.0 / 6.0))
    # pass^2 = C(2,2)/C(4,2) = 1/6
    check("pass^2 exact (4,2,2)=1/6", _close(b.pass_hat_k(4, 2, 2), 1.0 / 6.0))

    # Fundamental Invariant: pass^k <= pass@1 <= pass@k
    n, c, k = 10, 8, 4
    p_hat = b.pass_hat_k(n, c, k)
    p_1 = b.pass_at_k(n, c, 1)
    p_at = b.pass_at_k(n, c, k)
    check("Invariant pass^k <= pass@1 <= pass@k", p_hat <= p_1 + 1e-9 and p_1 <= p_at + 1e-9,
          f"pass^{k}={p_hat:.4f} <= pass@1={p_1:.4f} <= pass@{k}={p_at:.4f}")


# ------------------------------------------------------------------------------
# Test 2: Utility function evaluation & Security penalties
# ------------------------------------------------------------------------------
def test_utility_calculation():
    print("\n--- Test 2: Composite Utility Formulation ---")
    good_metrics = {"accuracy": 0.90, "stability": 0.85, "cost_mean": 0.01, "latency_p95": 200.0, "security": 1.0}
    u_good = b.compute_utility(good_metrics)
    check("Good metrics yield positive utility", u_good > 0.5, f"utility={u_good}")

    # Plant security defect: security drops below 1.0
    bad_sec_metrics = {"accuracy": 0.90, "stability": 0.85, "cost_mean": 0.01, "latency_p95": 200.0, "security": 0.8}
    u_bad_sec = b.compute_utility(bad_sec_metrics)
    check("Security violation severely penalizes utility", u_bad_sec < u_good - 0.5,
          f"u_good={u_good:.4f}, u_bad_sec={u_bad_sec:.4f}")


# ------------------------------------------------------------------------------
# Test 3: Positive Delta Verification (Positive Control)
# ------------------------------------------------------------------------------
def test_positive_delta_gate():
    print("\n--- Test 3: Positive Delta Gate (Positive Control) ---")
    baseline = [
        {"task": "t1", "ok": True, "cost": 0.02, "latency_ms": 150.0, "error": False, "security_violation": False},
        {"task": "t1", "ok": False, "cost": 0.02, "latency_ms": 200.0, "error": True, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.02, "latency_ms": 180.0, "error": False, "security_violation": False},
        {"task": "t2", "ok": False, "cost": 0.02, "latency_ms": 220.0, "error": True, "security_violation": False},
    ]

    # Candidate improves accuracy and consistency: all pass
    candidate = [
        {"task": "t1", "ok": True, "cost": 0.02, "latency_ms": 120.0, "error": False, "security_violation": False},
        {"task": "t1", "ok": True, "cost": 0.02, "latency_ms": 130.0, "error": False, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.02, "latency_ms": 140.0, "error": False, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.02, "latency_ms": 130.0, "error": False, "security_violation": False},
    ]

    passes, report = b.verify_positive_delta(baseline, candidate, k=2, min_delta=0.0)
    check("Positive delta passes gate verification", passes is True)
    check("Delta utility is positive", report["delta_utility"] > 0, f"delta={report['delta_utility']}")
    check("Delta accuracy is positive", report["delta_accuracy"] > 0, f"delta_acc={report['delta_accuracy']}")
    check("Delta stability is positive", report["delta_stability"] > 0, f"delta_stab={report['delta_stability']}")
    check("Zero regressions reported", len(report["regressions"]) == 0)


# ------------------------------------------------------------------------------
# Test 4: Negative Delta Gate Blocking (Negative Control: Regression)
# ------------------------------------------------------------------------------
def test_negative_delta_gate():
    print("\n--- Test 4: Negative Delta Gate (Negative Control: Accuracy Regression) ---")
    baseline = [
        {"task": "t1", "ok": True, "cost": 0.02, "latency_ms": 100.0, "error": False, "security_violation": False},
        {"task": "t1", "ok": True, "cost": 0.02, "latency_ms": 110.0, "error": False, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.02, "latency_ms": 120.0, "error": False, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.02, "latency_ms": 100.0, "error": False, "security_violation": False},
    ]

    # Plant defect: Candidate regresses severely on accuracy and errors
    candidate = [
        {"task": "t1", "ok": False, "cost": 0.05, "latency_ms": 500.0, "error": True, "security_violation": False},
        {"task": "t1", "ok": False, "cost": 0.05, "latency_ms": 400.0, "error": True, "security_violation": False},
        {"task": "t2", "ok": False, "cost": 0.05, "latency_ms": 450.0, "error": True, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.05, "latency_ms": 300.0, "error": False, "security_violation": False},
    ]

    passes, report = b.verify_positive_delta(baseline, candidate, k=2, min_delta=0.0)
    check("Negative delta BLOCKED by gate", passes is False)
    check("Delta utility is negative", report["delta_utility"] < 0, f"delta={report['delta_utility']}")
    check("Regressions list contains explicit diagnostics", len(report["regressions"]) > 0,
          f"regressions={report['regressions']}")


# ------------------------------------------------------------------------------
# Test 5: Negative Control: Security Regression Injection
# ------------------------------------------------------------------------------
def test_security_violation_rejection():
    print("\n--- Test 5: Security Violation Rejection (Negative Control) ---")
    baseline = [
        {"task": "t1", "ok": True, "cost": 0.02, "latency_ms": 100.0, "error": False, "security_violation": False},
    ]

    # High accuracy candidate, BUT with an injected security violation
    candidate = [
        {"task": "t1", "ok": True, "cost": 0.01, "latency_ms": 50.0, "error": False, "security_violation": True},
    ]

    passes, report = b.verify_positive_delta(baseline, candidate, k=1, require_security=True)
    check("Security violation causes gate to REJECT candidate", passes is False)
    check("Security regression captured in report", any("Security" in r for r in report["regressions"]),
          f"regressions={report['regressions']}")


# ------------------------------------------------------------------------------
# Test 6: SWE-bench-lite and tau-bench consistency verification
# ------------------------------------------------------------------------------
def test_swe_and_tau_bench_evaluators():
    print("\n--- Test 6: SWE-bench-lite and tau-bench Evaluators ---")
    swe_recs = [
        {"task": "django__django-1", "ok": True, "patch_applied": True, "regression": False},
        {"task": "pytest-dev__pytest-2", "ok": True, "patch_applied": True, "regression": False},
        {"task": "sympy__sympy-3", "ok": False, "patch_applied": True, "regression": False},
    ]
    swe_res = b.evaluate_swe_bench_lite(swe_recs)
    check("SWE-bench-lite tasks count", swe_res["tasks"] == 3)
    check("SWE-bench-lite resolved 2 of 3", swe_res["resolved"] == 2)
    check("SWE-bench-lite resolution rate ~0.6667", _close(swe_res["resolution_rate"], 2.0 / 3.0, 1e-4))

    tau_recs = [
        {"task": "airline_refund", "ok": True},
        {"task": "airline_refund", "ok": True},
        {"task": "airline_refund", "ok": False},
        {"task": "retail_exchange", "ok": True},
        {"task": "retail_exchange", "ok": True},
        {"task": "retail_exchange", "ok": True},
    ]
    tau_res = b.evaluate_tau_bench(tau_recs, k=2)
    check("tau-bench evaluated 2 tasks", tau_res["tasks"] == 2)
    check("tau-bench pass@1 calculated", tau_res["pass_at_1"] > 0.0)
    check("tau-bench pass^k (consistency) calculated", tau_res["pass_hat_k"] > 0.0)


# ------------------------------------------------------------------------------
# Test 7: CLI End-to-End Verification (`mios-bench verify` and `mios-bench gate`)
# ------------------------------------------------------------------------------
def test_cli_subcommands():
    print("\n--- Test 7: CLI End-to-End (`mios-bench verify / gate`) ---")
    base_data = [
        {"task": "t1", "ok": False, "cost": 0.01, "latency_ms": 200.0, "error": False, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.01, "latency_ms": 150.0, "error": False, "security_violation": False},
    ]
    cand_pos = [
        {"task": "t1", "ok": True, "cost": 0.01, "latency_ms": 100.0, "error": False, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.01, "latency_ms": 100.0, "error": False, "security_violation": False},
    ]
    cand_neg = [
        {"task": "t1", "ok": False, "cost": 0.01, "latency_ms": 500.0, "error": True, "security_violation": False},
        {"task": "t2", "ok": False, "cost": 0.01, "latency_ms": 600.0, "error": True, "security_violation": False},
    ]

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_base, \
         tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_pos, \
         tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_neg:
        json.dump(base_data, f_base)
        json.dump(cand_pos, f_pos)
        json.dump(cand_neg, f_neg)
        base_path = f_base.name
        pos_path = f_pos.name
        neg_path = f_neg.name

    try:
        # CLI Positive Control: verify command returns exit 0
        cmd_pos = [sys.executable, MIOS_BENCH, "verify", pos_path, "--baseline", base_path, "--k", "1"]
        res_pos = subprocess.run(cmd_pos, capture_output=True, text=True)
        check("CLI verify (positive delta) exits 0", res_pos.returncode == 0,
              f"code={res_pos.returncode}, out={res_pos.stdout}")
        check("CLI verify output confirms PASS", "[PASS]" in res_pos.stdout)

        # CLI Negative Control: verify command returns non-zero (exit 1)
        cmd_neg = [sys.executable, MIOS_BENCH, "verify", neg_path, "--baseline", base_path, "--k", "1"]
        res_neg = subprocess.run(cmd_neg, capture_output=True, text=True)
        check("CLI verify (negative delta) exits non-zero (1)", res_neg.returncode == 1,
              f"code={res_neg.returncode}")
        check("CLI verify output reports BLOCKED / FAIL", "[FAIL]" in res_neg.stdout)

        # CLI Gate alias: gate command behaves identically
        cmd_gate = [sys.executable, MIOS_BENCH, "gate", pos_path, "--baseline", base_path, "--k", "1"]
        res_gate = subprocess.run(cmd_gate, capture_output=True, text=True)
        check("CLI gate alias exits 0 on positive delta", res_gate.returncode == 0)

        # CLI Score with --baseline comparison
        cmd_score_base = [sys.executable, MIOS_BENCH, "score", pos_path, "--baseline", base_path, "--k", "1"]
        res_score = subprocess.run(cmd_score_base, capture_output=True, text=True)
        check("CLI score --baseline exits 0 on positive delta", res_score.returncode == 0)
    finally:
        for p in (base_path, pos_path, neg_path):
            if os.path.exists(p):
                os.remove(p)


def to_bash_path(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return f"/{p[0].lower()}{p[2:]}"
    return p

# ------------------------------------------------------------------------------
# Test 8: Pre-Commit Hook Integration (`10-dgm-bench.sh`)
# ------------------------------------------------------------------------------
def test_pre_commit_hook():
    print("\n--- Test 8: Pre-Commit Hook Integration (`10-dgm-bench.sh`) ---")
    check("Pre-commit hook script exists", os.path.isfile(HOOK_SCRIPT))

    base_data = [{"task": "t1", "ok": False, "cost": 0.01, "latency_ms": 200.0}]
    cand_pos = [{"task": "t1", "ok": True, "cost": 0.01, "latency_ms": 100.0}]
    cand_neg = [{"task": "t1", "ok": False, "cost": 0.05, "latency_ms": 500.0, "error": True}]

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_base, \
         tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_pos, \
         tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f_neg:
        json.dump(base_data, f_base)
        json.dump(cand_pos, f_pos)
        json.dump(cand_neg, f_neg)
        base_path = f_base.name
        pos_path = f_pos.name
        neg_path = f_neg.name

    try:
        hook_env = os.environ.copy()
        hook_env["MIOS_BENCH_BIN"] = f"{to_bash_path(sys.executable)} {to_bash_path(MIOS_BENCH)}"
        hook_env["PYTHONPATH"] = to_bash_path(_LIB) + (os.pathsep + hook_env["PYTHONPATH"] if "PYTHONPATH" in hook_env else "")

        hook_posix = to_bash_path(HOOK_SCRIPT)
        base_posix = to_bash_path(base_path)
        pos_posix = to_bash_path(pos_path)
        neg_posix = to_bash_path(neg_path)

        # Positive control: hook allows commit
        cmd_pos = ["bash", hook_posix, "--baseline", base_posix, "--candidate", pos_posix, "--k", "1"]
        res_pos = subprocess.run(cmd_pos, capture_output=True, text=True, env=hook_env)
        check("Hook: positive delta allows commit (exit 0)", res_pos.returncode == 0,
              f"code={res_pos.returncode}, stdout={res_pos.stdout}, stderr={res_pos.stderr}")
        check("Hook: stdout confirms clearing gate", "Positive delta verified" in res_pos.stdout)

        # Negative control: hook blocks commit with non-zero exit code
        cmd_neg = ["bash", hook_posix, "--baseline", base_posix, "--candidate", neg_posix, "--k", "1"]
        res_neg = subprocess.run(cmd_neg, capture_output=True, text=True, env=hook_env)
        check("Hook: negative delta BLOCKS commit (exit 1)", res_neg.returncode == 1,
              f"code={res_neg.returncode}, stdout={res_neg.stdout}, stderr={res_neg.stderr}")
        check("Hook: output reports BLOCKED / rejected", "BLOCKED" in res_neg.stderr or "BLOCKED" in res_neg.stdout)
    finally:
        for p in (base_path, pos_path, neg_path):
            if os.path.exists(p):
                os.remove(p)


def main() -> int:
    print("======================================================================")
    print("  Two-Sided Test Suite: DGM Self-Improvement Loop & Pre-Commit Gate   ")
    print("======================================================================")
    test_math_formulas()
    test_utility_calculation()
    test_positive_delta_gate()
    test_negative_delta_gate()
    test_security_violation_rejection()
    test_swe_and_tau_bench_evaluators()
    test_cli_subcommands()
    test_pre_commit_hook()

    print("\n======================================================================")
    if _fails == 0:
        print("  ALL DGM LOOP VERIFICATION TESTS PASSED (0 failures)")
        print("======================================================================")
        return 0
    else:
        print(f"  DGM LOOP VERIFICATION FAILED ({_fails} failures)")
        print("======================================================================")
        return 1


if __name__ == "__main__":
    sys.exit(main())
