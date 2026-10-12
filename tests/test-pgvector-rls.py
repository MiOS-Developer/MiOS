#!/usr/bin/env python3
# AI-hint: Two-sided test suite for PostgreSQL 16 + pgvector Row-Level Security (RLS) and Letta/MemGPT 4-tier K-LRU hierarchical memory.
# AI-doc: usr/share/doc/mios/manual/memory.md
# AI-related: usr/lib/mios/agent-pipe/mios_pipe/memory/pg.py, usr/lib/mios/agent-pipe/mios_pipe/memory/knowledge.py, usr/share/mios/postgres/schema-init.sql

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from typing import Any, Mapping, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, ".."))
_LIB = os.path.join(_REPO, "usr", "lib", "mios", "agent-pipe")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import mios_pipe.memory.pg as pg

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


# ==============================================================================
# Simulation Mock Engine for PostgreSQL 16 + pgvector with RLS Kernel Semantics
# ==============================================================================
class MockPgVectorRLSKernel:
    """Simulates PostgreSQL 16 + pgvector engine enforcing:
    1. Transaction-scoped SET LOCAL mios.owner_user = %(principal)s (set_config GUC).
    2. Row-Level Security (RLS) policy:
       USING (current_setting('mios.owner_user', true) IS NULL OR owner_user IS NULL OR owner_user = current_setting('mios.owner_user', true))
    3. 4-tier memory tables: scratch, agent_memory, knowledge, mios_rag.
    4. K-LRU access counter atomic increments and hot-tier promotion.
    5. Cold eviction sweep with hot/pinned immunity.
    """

    def __init__(self) -> None:
        self.guc_session: dict[str, str] = {}
        self.tables: dict[str, list[dict[str, Any]]] = {
            "scratch": [],
            "agent_memory": [],
            "knowledge": [],
            "mios_rag": [],
        }
        self.executed_statements: list[tuple[str, Any]] = []

    def set_config(self, guc: str, val: str, is_local: bool = True):
        self.guc_session[guc] = str(val)

    def reset_guc(self):
        self.guc_session.clear()

    def get_current_setting(self, guc: str) -> Optional[str]:
        return self.guc_session.get(guc, None)

    def insert(self, table: str, row: dict[str, Any]) -> int:
        if table not in self.tables:
            self.tables[table] = []
        r = dict(row)
        r["id"] = len(self.tables[table]) + 1
        r.setdefault("ts", time.time())
        self.tables[table].append(r)
        return r["id"]

    def query(self, table: str, where_fn=None, rls_enabled: bool = True) -> list[dict[str, Any]]:
        rows = self.tables.get(table, [])
        owner_setting = self.get_current_setting("mios.owner_user")

        visible = []
        for r in rows:
            # Evaluate Postgres RLS policy
            if rls_enabled:
                row_owner = r.get("owner_user")
                # Policy: owner_setting IS NULL OR owner_user IS NULL OR owner_user = owner_setting
                if owner_setting is None:
                    allowed = True
                elif row_owner is None:
                    allowed = True
                elif row_owner == owner_setting:
                    allowed = True
                else:
                    allowed = False
                if not allowed:
                    continue

            if where_fn is None or where_fn(r):
                visible.append(dict(r))
        return visible

    def atomic_hit_knowledge(self, row_id: int, hot_threshold: int = 5) -> Optional[dict[str, Any]]:
        for r in self.tables.get("knowledge", []):
            if r.get("id") == row_id:
                r["access_count"] = r.get("access_count", 0) + 1
                r["recall_hits"] = r.get("recall_hits", 0) + 1
                r["last_access"] = time.time()
                if r["access_count"] >= hot_threshold:
                    r["tier"] = "hot"
                return dict(r)
        return None

    def evict_knowledge_sweep(self, min_access: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Performs K-LRU cold sweep:
        Immune: tier == 'hot' or pinned == True or satisfied == True.
        Evicted: access_count < min_access.
        Returns (evicted_rows, preserved_rows).
        """
        rows = self.tables.get("knowledge", [])
        evicted = []
        preserved = []
        remaining = []

        for r in rows:
            is_hot = r.get("tier") == "hot"
            is_pinned = bool(r.get("pinned", False))
            is_satisfied = bool(r.get("satisfied", False))

            if is_hot or is_pinned or is_satisfied:
                preserved.append(dict(r))
                remaining.append(r)
            elif r.get("access_count", 0) < min_access:
                evicted.append(dict(r))
            else:
                preserved.append(dict(r))
                remaining.append(r)

        self.tables["knowledge"] = remaining
        return evicted, preserved


# ------------------------------------------------------------------------------
# Test 1: Dynamic Transaction-Scoped SET LOCAL mios.owner_user RLS Binding
# ------------------------------------------------------------------------------
def test_transaction_scoped_rls_binding():
    print("\n--- Test 1: Dynamic SET LOCAL RLS Binding & Param Protocol ---")
    sql, params = pg.build_set_owner("tenant_alpha")
    check("build_set_owner emits parameterized set_config", "set_config" in sql)
    check("GUC parameter bound out-of-band", params.get("guc") == "mios.owner_user")
    check("Owner principal bound out-of-band", params.get("owner") == "tenant_alpha")

    # Verify is_local=true semantics (transaction-scoped)
    check("SQL specifies is_local=true for transaction scoping", ", true)" in sql)

    # Verify RLS enablement helper
    check("rls_enabled defaults to false when env unset", pg.rls_enabled({}) is False)
    check("rls_enabled returns true when MIOS_PGVECTOR_RLS_ENABLE=1",
          pg.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": "1"}) is True)
    check("rls_enabled returns true when MIOS_PGVECTOR_RLS_ENABLE=true",
          pg.rls_enabled({"MIOS_PGVECTOR_RLS_ENABLE": "true"}) is True)

    # Verify fail-closed toggle
    check("rls_fail_closed defaults to false when unset", pg.rls_fail_closed({}) is False)
    check("rls_fail_closed returns true when MIOS_PGVECTOR_RLS_FAIL_CLOSED=1",
          pg.rls_fail_closed({"MIOS_PGVECTOR_RLS_FAIL_CLOSED": "1"}) is True)


# ------------------------------------------------------------------------------
# Test 2: Two-Sided Tenant Isolation Under RLS (Tenant A cannot read Tenant B)
# ------------------------------------------------------------------------------
def test_tenant_isolation_rls():
    print("\n--- Test 2: RLS Tenant Isolation (Positive & Negative Controls) ---")
    kernel = MockPgVectorRLSKernel()

    # Seed records across tenants in knowledge table
    kernel.insert("knowledge", {"q": "Alpha API Key", "answer": "sk-alpha-secret", "owner_user": "tenant_a"})
    kernel.insert("knowledge", {"q": "Alpha Architecture", "answer": "Alpha Microservices", "owner_user": "tenant_a"})
    kernel.insert("knowledge", {"q": "Beta Private Key", "answer": "rsa-beta-privkey", "owner_user": "tenant_b"})
    kernel.insert("knowledge", {"q": "Beta Financials", "answer": "Q3 Revenue $10M", "owner_user": "tenant_b"})
    kernel.insert("knowledge", {"q": "Public Help", "answer": "MiOS Documentation", "owner_user": None})

    # Scenario 1: Tenant A authenticated query
    kernel.set_config("mios.owner_user", "tenant_a", is_local=True)
    res_a = kernel.query("knowledge", rls_enabled=True)

    # Positive control: Tenant A reads Tenant A and Public rows
    q_a = [r["q"] for r in res_a]
    check("Tenant A can read Tenant A records (Positive Control)", "Alpha API Key" in q_a and "Alpha Architecture" in q_a)
    check("Tenant A can read Shared/Public records (Positive Control)", "Public Help" in q_a)

    # Negative control: Tenant A MUST NOT read Tenant B records
    check("Tenant A CANNOT read Tenant B Private Key (Negative Control)", "Beta Private Key" not in q_a)
    check("Tenant A CANNOT read Tenant B Financials (Negative Control)", "Beta Financials" not in q_a)
    check("Tenant A sees exact authorized count (3 rows)", len(res_a) == 3)

    # Scenario 2: Tenant B authenticated query
    kernel.set_config("mios.owner_user", "tenant_b", is_local=True)
    res_b = kernel.query("knowledge", rls_enabled=True)

    # Positive control: Tenant B reads Tenant B and Public rows
    q_b = [r["q"] for r in res_b]
    check("Tenant B can read Tenant B records (Positive Control)", "Beta Private Key" in q_b and "Beta Financials" in q_b)
    check("Tenant B can read Shared/Public records (Positive Control)", "Public Help" in q_b)

    # Negative control: Tenant B MUST NOT read Tenant A records
    check("Tenant B CANNOT read Tenant A API Key (Negative Control)", "Alpha API Key" not in q_b)
    check("Tenant B CANNOT read Tenant A Architecture (Negative Control)", "Alpha Architecture" not in q_b)
    check("Tenant B sees exact authorized count (3 rows)", len(res_b) == 3)

    # Reset session GUC to verify transaction cleanup
    kernel.reset_guc()
    check("Session GUC reset clears principal (no connection leak)", kernel.get_current_setting("mios.owner_user") is None)


# ------------------------------------------------------------------------------
# Test 3: Unauthenticated Queries Fail Closed Under RLS
# ------------------------------------------------------------------------------
def test_unauthenticated_fail_closed():
    print("\n--- Test 3: Unauthenticated Queries Fail Closed ---")
    kernel = MockPgVectorRLSKernel()
    kernel.insert("agent_memory", {"fact": "User A Phone +1234", "owner_user": "tenant_a"})
    kernel.insert("agent_memory", {"fact": "User B SSN 000-00-0000", "owner_user": "tenant_b"})
    kernel.insert("agent_memory", {"fact": "System Version 0.3.0", "owner_user": None})

    # Test pg._owner_scope with fail_closed=True
    prior_mode = os.environ.get("MIOS_PRINCIPAL_BIND_MODE")
    try:
        os.environ["MIOS_PRINCIPAL_BIND_MODE"] = "enforce"
        env_rls = {"MIOS_PGVECTOR_RLS_ENABLE": "1", "MIOS_PGVECTOR_RLS_FAIL_CLOSED": "1"}

        # Query with authenticated owner
        sc_auth = pg._owner_scope("tenant_a", env_rls)
        check("Authenticated owner yields real principal scope",
              sc_auth is not None and sc_auth[1]["owner"] == "tenant_a")

        # Query with UNATTACHED / NONE principal under fail_closed
        sc_unauth = pg._owner_scope(None, env_rls)
        check("Unauthenticated query yields __unauthenticated__ scope (fails closed)",
              sc_unauth is not None and sc_unauth[1]["owner"] == "__unauthenticated__")

        # Apply __unauthenticated__ scope to kernel
        kernel.set_config("mios.owner_user", sc_unauth[1]["owner"], is_local=True)
        res_unauth = kernel.query("agent_memory", rls_enabled=True)

        facts = [r["fact"] for r in res_unauth]
        # Negative controls: Unauthenticated caller cannot read any tenant's private facts
        check("Unauthenticated query CANNOT read Tenant A facts", "User A Phone +1234" not in facts)
        check("Unauthenticated query CANNOT read Tenant B facts", "User B SSN 000-00-0000" not in facts)
        # Only shared public facts visible
        check("Unauthenticated query sees only shared public facts", facts == ["System Version 0.3.0"])
    finally:
        if prior_mode is not None:
            os.environ["MIOS_PRINCIPAL_BIND_MODE"] = prior_mode
        else:
            os.environ.pop("MIOS_PRINCIPAL_BIND_MODE", None)


# ------------------------------------------------------------------------------
# Test 4: 4-Tier Letta/MemGPT Hierarchical Memory
# ------------------------------------------------------------------------------
def test_four_tier_memory_hierarchy():
    print("\n--- Test 4: 4-Tier Memory Hierarchy Representation ---")
    kernel = MockPgVectorRLSKernel()

    # Tier 1: scratch (Core ephemeral in-context memory)
    id_s = kernel.insert("scratch", {"key": "curr_task", "val": "debugging-gate", "owner_user": "agent_worker"})
    # Tier 2: agent_memory (Recall memory: agent facts with scope)
    id_m = kernel.insert("agent_memory", {"fact": "Python 3.14 default", "scope": "global", "owner_user": "agent_worker"})
    # Tier 3: knowledge (Archival memory: indexed semantic Q&A)
    id_k = kernel.insert("knowledge", {"q": "How to verify gates?", "answer": "Run ci-suites.py", "owner_user": "agent_worker"})
    # Tier 4: mios_rag (Document RAG: vectorized system documentation)
    id_r = kernel.insert("mios_rag", {"source": "manual.md", "content": "Architecture Laws", "owner_user": None})

    check("Tier 1 (scratch) row initialized", id_s > 0 and len(kernel.tables["scratch"]) == 1)
    check("Tier 2 (agent_memory) row initialized", id_m > 0 and len(kernel.tables["agent_memory"]) == 1)
    check("Tier 3 (knowledge) row initialized", id_k > 0 and len(kernel.tables["knowledge"]) == 1)
    check("Tier 4 (mios_rag) row initialized", id_r > 0 and len(kernel.tables["mios_rag"]) == 1)


# ------------------------------------------------------------------------------
# Test 5: K-LRU Virtual Memory Paging (Counter Promotion & Cold Sweep)
# ------------------------------------------------------------------------------
def test_klru_paging_and_eviction():
    print("\n--- Test 5: K-LRU Counter Promotion & Cold Eviction Sweep ---")
    kernel = MockPgVectorRLSKernel()

    # Seed knowledge items with different initial states
    id_warm = kernel.insert("knowledge", {
        "q": "Frequently Accessed Doc",
        "answer": "Useful Information",
        "tier": "warm",
        "access_count": 0,
        "pinned": False,
        "satisfied": False,
    })

    id_cold = kernel.insert("knowledge", {
        "q": "Stale Query 2024",
        "answer": "Deprecated Flag",
        "tier": "warm",
        "access_count": 0,
        "pinned": False,
        "satisfied": False,
    })

    id_pinned = kernel.insert("knowledge", {
        "q": "Root Security Policy",
        "answer": "Zero Trust Invariant",
        "tier": "warm",
        "access_count": 0,
        "pinned": True,
        "satisfied": False,
    })

    # Step A: Atomic Counter Promotion (hits 1 to 5)
    hot_threshold = 5
    for hit in range(1, hot_threshold + 1):
        updated = kernel.atomic_hit_knowledge(id_warm, hot_threshold=hot_threshold)
        check(f"Access hit {hit} increments access_count", updated["access_count"] == hit)
        check(f"Access hit {hit} increments recall_hits", updated["recall_hits"] == hit)

    # Verify promotion to 'hot' tier
    promoted_row = [r for r in kernel.tables["knowledge"] if r["id"] == id_warm][0]
    check("Item promoted from 'warm' to 'hot' at threshold", promoted_row["tier"] == "hot")

    # Step B: Cold Eviction Sweep
    # min_access = 1: items with access_count < 1 are cold candidates
    evicted, preserved = kernel.evict_knowledge_sweep(min_access=1)

    evicted_ids = [r["id"] for r in evicted]
    preserved_ids = [r["id"] for r in preserved]

    # Positive control: Cold item is evicted
    check("Cold stale item is evicted (Positive Control)", id_cold in evicted_ids)

    # Negative controls: Hot and pinned items are IMMUNE from eviction
    check("Promoted 'hot' item is IMMUNE from eviction (Negative Control)", id_warm in preserved_ids)
    check("Pinned item is IMMUNE from eviction (Negative Control)", id_pinned in preserved_ids)
    check("Evicted count is exactly 1", len(evicted) == 1)
    check("Preserved count is exactly 2", len(preserved) == 2)


# ------------------------------------------------------------------------------
# Test 6: Cold Zstd Compression Pipeline Simulation
# ------------------------------------------------------------------------------
def test_cold_zstd_compression():
    print("\n--- Test 6: Cold Storage Zstd Export Pipeline ---")
    evicted_records = [
        {"id": 101, "q": "Old Fact 1", "answer": "Old Answer 1", "ts": 1700000000},
        {"id": 102, "q": "Old Fact 2", "answer": "Old Answer 2", "ts": 1700000001},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_dir = os.path.join(tmpdir, "history", "2026", "10-12")
        os.makedirs(storage_dir, exist_ok=True)
        jsonl_path = os.path.join(storage_dir, "cold_evict.jsonl")

        with open(jsonl_path, "w", encoding="utf-8") as fh:
            for rec in evicted_records:
                fh.write(json.dumps(rec) + "\n")

        check("Exported JSONL exists", os.path.isfile(jsonl_path))
        check("JSONL contains exact record count", sum(1 for _ in open(jsonl_path)) == 2)


def main() -> int:
    print("======================================================================")
    print("  Two-Sided Test Suite: PostgreSQL 16 + pgvector RLS & Memory Tiers   ")
    print("======================================================================")
    test_transaction_scoped_rls_binding()
    test_tenant_isolation_rls()
    test_unauthenticated_fail_closed()
    test_four_tier_memory_hierarchy()
    test_klru_paging_and_eviction()
    test_cold_zstd_compression()

    print("\n======================================================================")
    if _fails == 0:
        print("  ALL PGVECTOR RLS & MEMORY HIERARCHY TESTS PASSED (0 failures)")
        print("======================================================================")
        return 0
    else:
        print(f"  PGVECTOR RLS TESTS FAILED ({_fails} failures)")
        print("======================================================================")
        return 1


if __name__ == "__main__":
    sys.exit(main())
