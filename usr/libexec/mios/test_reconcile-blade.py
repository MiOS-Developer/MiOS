#!/usr/bin/env python3
# AI-hint: Fixtures for reconcile-blade.py -- one per ADR-0017 D5 merge rule, including the one that must REFUSE: a config_kv divergence is an operator decision, never an automatic winner.
# AI-related: usr/libexec/mios/reconcile-blade.py, usr/share/doc/mios/adr/0017-blade-workload-mobility.md, usr/share/mios/mios.toml
# AI-functions: main
"""Each merge class, and the one that must not merge.

The dangerous rule is config_kv: config is intent, so silently picking a winner
would let a network partition change policy without anyone deciding to.
"""
from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("rb", os.path.join(HERE, "reconcile-blade.py"))
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

FAILED: list[str] = []
PASSED = 0

def check(name, got, want):
    global PASSED
    if got == want:
        PASSED += 1
    else:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")

def test_union_by_hash_dedupes():
    left = [{"hash": "a", "v": 1}, {"hash": "b", "v": 2}]
    right = [{"hash": "b", "v": 2}, {"hash": "c", "v": 3}]
    merged, conflicts = rb.reconcile_rows("knowledge", "union-by-hash", left, right)
    check("union-count", len(merged), 3)
    check("union-no-conflicts", conflicts, [])
    # Same content hash on both sides is a no-op, which is what makes the
    # availability choice affordable for derived data.
    check("union-dedupes", sorted(r["hash"] for r in merged), ["a", "b", "c"])

def test_append_ordered_sorts_by_clock_then_origin():
    left = [{"id": "1", "logical_ts": 5, "origin_node": "b"}]
    right = [{"id": "2", "logical_ts": 3, "origin_node": "a"},
             {"id": "3", "logical_ts": 5, "origin_node": "a"}]
    merged, conflicts = rb.reconcile_rows("event", "append-ordered", left, right)
    check("append-count", len(merged), 3)
    check("append-order", [r["id"] for r in merged], ["2", "3", "1"])
    check("append-no-conflicts", conflicts, [])

def test_config_kv_conflict_is_refused():
    """The rule that must NOT auto-merge."""
    left = [{"key": "policy", "value": "strict"}]
    right = [{"key": "policy", "value": "lax"}]
    merged, conflicts = rb.reconcile_rows("config_kv", "conflict-is-error", left, right)
    check("config-conflict-reported", len(conflicts) > 0, True)
    check("config-conflict-names-key", any("policy" in c for c in conflicts), True)

def test_config_kv_agreement_is_not_a_conflict():
    same = [{"key": "policy", "value": "strict"}]
    merged, conflicts = rb.reconcile_rows("config_kv", "conflict-is-error", same, list(same))
    check("config-agreement-clean", conflicts, [])

def main() -> int:
    test_union_by_hash_dedupes()
    test_append_ordered_sorts_by_clock_then_origin()
    test_config_kv_conflict_is_refused()
    test_config_kv_agreement_is_not_a_conflict()
    print(f"[test_reconcile-blade] {PASSED} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAIL {f}")
    return 1 if FAILED else 0

if __name__ == "__main__":
    sys.exit(main())
