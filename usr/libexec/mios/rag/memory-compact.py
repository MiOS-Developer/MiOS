#!/usr/bin/env python3
# AI-hint: Cross-turn episodic memory compaction into hierarchical semantic trees.
# AI-related: tests/test-memory-compaction.py, usr/share/doc/mios/manual/ai.md
"""
MiOS Episodic Memory Compactor.
Summarizes multi-turn conversations into concise factual nodes linked in the PostgreSQL fact ledger.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Tuple

class MemoryCompactor:
    """Extracts architectural constraints and key facts from conversation turns."""

    def extract_facts_from_turns(self, turns: List[Dict[str, str]]) -> List[Dict[str, str]]:
        facts = []
        for turn in turns:
            text = turn.get("content", "")
            role = turn.get("role", "user")
            if "preference:" in text.lower() or "fact:" in text.lower() or "constraint:" in text.lower():
                facts.append({
                    "role": role,
                    "fact": text.strip(),
                    "category": "user_preference" if "preference:" in text.lower() else "system_constraint",
                })
        return facts

    def build_hierarchical_tree(self, facts: List[Dict[str, str]]) -> Dict[str, List[str]]:
        tree = {"user_preference": [], "system_constraint": []}
        for f in facts:
            cat = f.get("category", "system_constraint")
            tree.setdefault(cat, []).append(f.get("fact", ""))
        return tree
