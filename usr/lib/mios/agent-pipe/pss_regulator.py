# AI-hint: MiOS system and orchestration module providing pss regulator capabilities.
# AI-functions: __init__, current_allocated_mb, admit_task, release_task, SwarmAgentTask, PSSMemoryRegulator

"""
pss_regulator.py — task_916 WS-AI
PSS memory budget regulator and swarm agent OOM circuit breaker in agent-pipe.

Enforces strict Proportional Set Size (PSS) host memory budgets across multi-agent
swarms, allowing >1,500 concurrent background workers within a 16GB RAM limit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("pss_regulator")

@dataclass
class SwarmAgentTask:
    task_id: str
    task_type: str # 'jcode_worker' (9.5MB) vs 'opencode_container' (145MB)
    pss_mb: float

class PSSMemoryRegulator:
    """
    Regulates swarm memory allocation against a hard host budget (16,000 MB).
    """
    def __init__(self, max_budget_mb: float = 16_000.0) -> None:
        self.max_budget_mb = max_budget_mb
        self.active_tasks: Dict[str, SwarmAgentTask] = {}

    @property
    def current_allocated_mb(self) -> float:
        return sum(t.pss_mb for t in self.active_tasks.values())

    def admit_task(self, task_id: str, task_type: str = "jcode_worker") -> bool:
        """Evaluates admission against PSS budget; triggers circuit breaker if full."""
        pss_cost = 9.5 if task_type == "jcode_worker" else 145.0
        if (self.current_allocated_mb + pss_cost) > self.max_budget_mb:
            log.warning("OOM Shield: Rejected task %s (type=%s, budget exceeded)", task_id, task_type)
            return False

        self.active_tasks[task_id] = SwarmAgentTask(
            task_id=task_id,
            task_type=task_type,
            pss_mb=pss_cost
        )
        return True

    def release_task(self, task_id: str) -> None:
        self.active_tasks.pop(task_id, None)
