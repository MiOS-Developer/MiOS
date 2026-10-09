#!/usr/bin/env python3
# AI-hint: Standalone assert-script unit test for mios_bench (agentic-capability benchmark scoring core). Pure stdlib, no server.py/DB/pytest.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Unit tests for mios_bench (pass@k / pass^k / CLASSic scoring)."""

import sys

import mios_bench as b

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _close(a, c, tol=1e-9):
    return abs(a - c) <= tol

def t_pass_at_k():
    check("pass@1 == c/n", _close(b.pass_at_k(10, 7, 1), 0.7))
    check("pass@k all-correct -> 1", _close(b.pass_at_k(5, 5, 3), 1.0))
    check("pass@k c==0 -> 0", _close(b.pass_at_k(5, 0, 3), 0.0))
    check("pass@k k>n -> 0", b.pass_at_k(3, 2, 5) == 0.0)
    check("pass@k exact (4,2,2)=5/6", _close(b.pass_at_k(4, 2, 2), 5.0 / 6.0))
    check("pass@k monotonic in k",
          b.pass_at_k(8, 3, 1) <= b.pass_at_k(8, 3, 2) <= b.pass_at_k(8, 3, 3))

def t_pass_hat_k():
    check("pass^1 == c/n", _close(b.pass_hat_k(10, 7, 1), 0.7))
    check("pass^k c==n -> 1", _close(b.pass_hat_k(5, 5, 3), 1.0))
    check("pass^k c<k -> 0", b.pass_hat_k(5, 2, 3) == 0.0)
    check("pass^k c==0 -> 0", b.pass_hat_k(5, 0, 2) == 0.0)
    check("pass^k k>n -> 0", b.pass_hat_k(3, 3, 5) == 0.0)
    check("pass^k exact (4,2,2)=1/6", _close(b.pass_hat_k(4, 2, 2), 1.0 / 6.0))
    n, c, k = 8, 6, 3
    check("pass^k <= pass@1 <= pass@k",
          b.pass_hat_k(n, c, k) <= b.pass_at_k(n, c, 1) + 1e-12
          and b.pass_at_k(n, c, 1) <= b.pass_at_k(n, c, k) + 1e-12)

def t_iid():
    check("iid pass^k = p**k (0.9,8)~=0.4305", _close(b.iid_pass_hat_k(0.9, 8), 0.9 ** 8, 1e-9))
    check("iid ~56% reliability at k=8 for p=0.93", 0.55 <= b.iid_pass_hat_k(0.93, 8) <= 0.57)
    check("iid p=1 -> 1", _close(b.iid_pass_hat_k(1.0, 8), 1.0))
    check("iid k=0 -> 1", _close(b.iid_pass_hat_k(0.5, 0), 1.0))
    check("iid clamps p>1", _close(b.iid_pass_hat_k(2.0, 3), 1.0))

def t_aggregate():
    tasks = [(4, 2), (4, 4), (1, 1)]   # last has n<k, skipped at k=2
    check("agg pass^k skips n<k", _close(
        b.aggregate_pass_hat_k(tasks, 2), (b.pass_hat_k(4, 2, 2) + 1.0) / 2.0))
    check("agg pass@k skips n<k", _close(
        b.aggregate_pass_at_k(tasks, 2), (b.pass_at_k(4, 2, 2) + 1.0) / 2.0))
    check("agg empty -> 0", b.aggregate_pass_hat_k([], 2) == 0.0)
    check("agg all-too-small -> 0", b.aggregate_pass_hat_k([(1, 1)], 3) == 0.0)

def t_pass_and_k_rate():
    tasks = [(4, 4), (4, 2), (1, 1)]
    check("pass^k_rate = fraction all-pass (1 of 2 qualifying)",
          _close(b.aggregate_pass_and_k_rate(tasks, 2), 0.5))
    check("pass^k_rate all-perfect -> 1",
          _close(b.aggregate_pass_and_k_rate([(3, 3), (5, 5)], 2), 1.0))
    check("pass^k_rate none-perfect -> 0",
          _close(b.aggregate_pass_and_k_rate([(4, 3), (4, 2)], 2), 0.0))
    check("pass^k_rate empty -> 0", b.aggregate_pass_and_k_rate([], 2) == 0.0)
    check("pass^k_rate all-too-small -> 0",
          b.aggregate_pass_and_k_rate([(1, 1)], 3) == 0.0)
    check("pass^k_rate differs from mean pass^k",
          b.aggregate_pass_and_k_rate([(4, 4), (4, 2)], 2)
          != b.aggregate_pass_hat_k([(4, 4), (4, 2)], 2))

def t_percentile():
    check("pctl p50 interp [100,200,300]=200", _close(b.percentile([100, 200, 300], 50), 200.0))
    check("pctl p95 [100,200,300]=290", _close(b.percentile([100, 200, 300], 95), 290.0))
    check("pctl single value", _close(b.percentile([42], 95), 42.0))
    check("pctl empty -> 0", b.percentile([], 50) == 0.0)
    check("pctl p0/p100 = min/max", _close(b.percentile([5, 1, 9], 0), 1.0)
          and _close(b.percentile([5, 1, 9], 100), 9.0))

def t_classic_rollup():
    recs = [
        {"task": "a", "ok": True, "cost": 0.1, "latency_ms": 100, "error": False, "security_violation": False},
        {"task": "a", "ok": False, "cost": 0.2, "latency_ms": 300, "error": True, "security_violation": False},
        {"task": "b", "ok": True, "cost": 0.3, "latency_ms": 200, "error": False, "security_violation": True},
    ]
    r = b.classic_rollup(recs, k=1)
    check("classic: n + n_tasks", r["n"] == 3 and r["n_tasks"] == 2)
    check("classic: cost", _close(r["cost_total"], 0.6, 1e-6) and _close(r["cost_mean"], 0.2, 1e-6))
    check("classic: accuracy 2/3", _close(r["accuracy"], 2.0 / 3.0, 1e-5))
    check("classic: stability k=1 = 1-err", _close(r["stability"], 2.0 / 3.0, 1e-5))
    check("classic: security 1-1/3", _close(r["security"], 2.0 / 3.0, 1e-5))
    check("classic: latency p50=200 p95=290", _close(r["latency_p50"], 200.0) and _close(r["latency_p95"], 290.0))
    r2 = b.classic_rollup(recs, k=2)
    check("classic: stability k=2 = pass^2 grouped", _close(r2["stability"], 0.0))
    check("classic: empty -> safe zeros", b.classic_rollup([])["n"] == 0
          and b.classic_rollup([])["security"] == 1.0)

def main():
    t_pass_at_k()
    t_pass_hat_k()
    t_iid()
    t_aggregate()
    t_pass_and_k_rate()
    t_percentile()
    t_classic_rollup()
    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0



# ==============================================================================
# Consolidated from test_mios_bench_harness.py (task_1006)
# ==============================================================================
# AI-hint: Verification test suite for mios-bench harness CLI option parsing, metrics reporting, a...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

import sys
import os
import json
import subprocess
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
MIOS_BENCH = os.path.join(_REPO, "usr", "libexec", "mios", "mios-bench")

_fails_bench_harness = 0

def _check_bench_harness(name, cond, detail=""):
    global _fails_bench_harness
    if not cond:
        _fails_bench_harness += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def test_offline_score_table():
    results = [
        {"task": "t1", "ok": True, "cost": 0.1, "latency_ms": 1500, "error": False, "security_violation": False},
        {"task": "t1", "ok": False, "cost": 0.2, "latency_ms": 2500, "error": False, "security_violation": False},
        {"task": "t2", "ok": True, "cost": 0.15, "latency_ms": 1000, "error": False, "security_violation": False}
    ]
    tmp_path = "/tmp/test_bench_results.json"
    with open(tmp_path, "w") as fh:
        json.dump({"results": results}, fh)

    try:
        p = subprocess.Popen(
            [sys.executable, MIOS_BENCH, "score", tmp_path, "--k", "2"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = p.communicate()

        if p.returncode != 0:
            print(f"DEBUG score exit code: {p.returncode}")
            print(f"DEBUG score stdout: {out.decode('utf-8')}")
            print(f"DEBUG score stderr: {err.decode('utf-8')}")
        _check_bench_harness("score command exit code is 0", p.returncode == 0)
        output_str = out.decode("utf-8")

        _check_bench_harness("table has pass@1", "pass@1:" in output_str)
        _check_bench_harness("table has pass@k", "pass@k:" in output_str)
        _check_bench_harness("table has pass^k", "pass^k:" in output_str)
        _check_bench_harness("table has throughput", "throughput:" in output_str)
        _check_bench_harness("table has avg_wait", "avg_wait:" in output_str)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def test_run_command_suite_routing():
    suite = [
        {"task": "addition", "prompt": "40+2", "expect": "42"}
    ]
    tmp_suite = "/tmp/test_bench_suite.json"
    with open(tmp_suite, "w") as fh:
        json.dump(suite, fh)

    import http.server
    import threading
    class MockHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "choices": [{"message": {"content": "The result of 40+2 is 42."}}]
            }).encode())

        def log_message(self, format, *args):
            pass  # Suppress logging noise

    server = http.server.HTTPServer(("127.0.0.1", 8699), MockHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)

    try:
        p = subprocess.Popen(
            [sys.executable, MIOS_BENCH, "run", tmp_suite, "--endpoint", "http://127.0.0.1:8699/v1", "--k", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = p.communicate()
        _check_bench_harness("run command exit code is 0", p.returncode == 0)
        output_str = out.decode("utf-8")
        _check_bench_harness("results output has pass@1 == 1.0", "pass@1:     1.0000" in output_str)
    finally:
        server.shutdown()
        if os.path.exists(tmp_suite):
            os.remove(tmp_suite)

def _main_bench_harness():
    test_offline_score_table()
    test_run_command_suite_routing()
    print(f"\n{'ok' if _fails_bench_harness == 0 else str(_fails_bench_harness) + ' FAILED'}")
    return 1 if _fails_bench_harness else 0


def _run_extra_bench_harness():
    try:
        return _main_bench_harness()
    except SystemExit as _e:
        return _e.code if _e.code is not None else 0



def _run_all_folded_bench_suites():
    rc = _run_extra_bench_harness()
    if rc not in (None, 0):
        import sys
        sys.exit(f"Folded test suite failed: exit code {rc}")

if __name__ == "__main__":
    _run_all_folded_bench_suites()
    sys.exit(main())
