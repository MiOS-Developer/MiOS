#!/usr/bin/env python3
# AI-hint: Blade database reconcile engine implementing ADR-0017 D5 merge rules.
# AI-related: usr/share/mios/postgres/schema-init.sql, usr/share/mios/mios.toml [blade.reconcile]
# AI-functions: reconcile_table, reconcile_all, main
"""reconcile-blade.py -- Per-class database reconciliation for multi-blade partition rejoin.

Merge rules (ADR-0017 D5):
  1. union-by-hash   (knowledge, embeddings): merge rows by unique hash/id key;
  2. append-ordered  (agent_memory, event): merge rows ordered by logical_ts;
  3. last-writer-wins(session, scratch): pick row with highest logical_ts per primary key;
  4. conflict-is-error(config_kv): raise explicit conflict if key values diverge.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

MERGE_RULES = {
    "knowledge": "union-by-hash",
    "embeddings": "union-by-hash",
    "agent_memory": "append-ordered",
    "event": "append-ordered",
    "session": "last-writer-wins",
    "scratch": "last-writer-wins",
    "config_kv": "conflict-is-error",
}

def reconcile_rows(table: str, rule: str, left_rows: list[dict], right_rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Reconcile two lists of dict-like rows according to the specified rule.

    Returns (merged_rows, conflicts).
    """
    conflicts: list[str] = []

    if rule == "union-by-hash":
        seen = set()
        merged = []
        for row in left_rows + right_rows:
            key = row.get("hash") or row.get("id") or json.dumps(row, sort_keys=True)
            if key not in seen:
                seen.add(key)
                merged.append(row)
        return merged, conflicts

    elif rule == "append-ordered":
        # Merge all rows sorted by (logical_ts, origin_node)
        all_rows = left_rows + right_rows
        seen = set()
        merged = []
        for r in sorted(all_rows, key=lambda x: (x.get("logical_ts", 0), str(x.get("origin_node", "")))):
            key = r.get("id") or json.dumps(r, sort_keys=True)
            if key not in seen:
                seen.add(key)
                merged.append(r)
        return merged, conflicts

    elif rule == "last-writer-wins":
        by_key: dict[str, dict] = {}
        for r in left_rows + right_rows:
            pk = str(r.get("id") or r.get("key") or json.dumps(r, sort_keys=True))
            if pk not in by_key:
                by_key[pk] = r
            else:
                existing_ts = by_key[pk].get("logical_ts", 0)
                new_ts = r.get("logical_ts", 0)
                if new_ts >= existing_ts:
                    by_key[pk] = r
        return list(by_key.values()), conflicts

    elif rule == "conflict-is-error":
        left_by_k = {str(r.get("key") or r.get("id")): r for r in left_rows if "key" in r or "id" in r}
        right_by_k = {str(r.get("key") or r.get("id")): r for r in right_rows if "key" in r or "id" in r}
        merged_map = dict(left_by_k)
        for k, r_val in right_by_k.items():
            if k in merged_map:
                l_val = merged_map[k]
                if l_val.get("val") != r_val.get("val") or l_val.get("value") != r_val.get("value"):
                    conflicts.append(f"config_kv conflict on key '{k}': left={l_val} vs right={r_val}")
            else:
                merged_map[k] = r_val
        return list(merged_map.values()), conflicts

    else:
        raise ValueError(f"Unknown merge rule '{rule}' for table '{table}'")

def run_selftest() -> int:
    """Run verification test suite for all 4 merge rules."""
    # 1. union-by-hash test
    l_k = [{"id": 1, "hash": "abc", "val": "k1"}]
    r_k = [{"id": 1, "hash": "abc", "val": "k1"}, {"id": 2, "hash": "def", "val": "k2"}]
    res, cfl = reconcile_rows("knowledge", "union-by-hash", l_k, r_k)
    assert len(res) == 2, f"union-by-hash failed: {len(res)}"
    assert len(cfl) == 0

    # 2. append-ordered test
    l_m = [{"id": 10, "logical_ts": 100, "origin_node": "node-A"}]
    r_m = [{"id": 11, "logical_ts": 90, "origin_node": "node-B"}]
    res, cfl = reconcile_rows("agent_memory", "append-ordered", l_m, r_m)
    assert len(res) == 2 and res[0]["id"] == 11, "append-ordered sorting failed"
    assert len(cfl) == 0

    # 3. last-writer-wins test
    l_s = [{"key": "sess1", "logical_ts": 50, "data": "old"}]
    r_s = [{"key": "sess1", "logical_ts": 60, "data": "new"}]
    res, cfl = reconcile_rows("session", "last-writer-wins", l_s, r_s)
    assert len(res) == 1 and res[0]["data"] == "new", "last-writer-wins failed"
    assert len(cfl) == 0

    # 4. conflict-is-error test (non-conflicting)
    l_cfg = [{"key": "theme", "val": "dark"}]
    r_cfg = [{"key": "lang", "val": "en"}]
    res, cfl = reconcile_rows("config_kv", "conflict-is-error", l_cfg, r_cfg)
    assert len(res) == 2 and len(cfl) == 0

    # 5. conflict-is-error test (conflicting)
    l_cfg2 = [{"key": "auth", "val": "enabled"}]
    r_cfg2 = [{"key": "auth", "val": "disabled"}]
    res, cfl = reconcile_rows("config_kv", "conflict-is-error", l_cfg2, r_cfg2)
    assert len(cfl) == 1 and "auth" in cfl[0], f"conflict-is-error failed: {cfl}"

    print("reconcile-blade selftest: ALL 5 ASSERTIONS PASSED")
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(prog="reconcile-blade.py")
    ap.add_argument("--selftest", action="store_true", help="run verification test suite")
    args = ap.parse_args()
    if args.selftest:
        return run_selftest()
    print("reconcile-blade engine ready")
    return 0

if __name__ == "__main__":
    sys.exit(main())
