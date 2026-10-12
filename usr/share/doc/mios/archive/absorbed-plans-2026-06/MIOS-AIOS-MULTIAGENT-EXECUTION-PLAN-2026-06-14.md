<!-- AI-hint: Historical archived planning document from 2026-06. Absorbed into ROADMAP.md Part 17 / task_168-task_178. -->
# MiOS → Full AIOS: Multi-Agent Execution Plan (2026-06-14)

> **Archived:** 2026-06-15 &nbsp;|&nbsp; **Status:** Absorbed & Superceded &nbsp;|&nbsp; **Target:** ROADMAP.md Part 17 (task_168..task_178)

## Summary & Historical Context
This document is a historical planning record from the June 2026 AIOS capability alignment cycle. The requirements, architectural specifications, and implementation items outlined herein have been fully integrated into the canonical MiOS architecture, Architectural Decision Records (ADRs 0001..0018), and tracked tasks in ROADMAP.md and TASKS.md.

## Key Absorbed Workstreams
- **Unified OpenAI Interface Standard**: Law 5 (UNIFIED-AI-REDIRECTS) enforced via MIOS_AI_ENDPOINT.
- **Inference Lane Hierarchy**: Primary mios-llm-light (llama-swap), gated GPU heavy lanes (mios-llm-heavy), and
omic-embed-text embeddings.
- **Agent Orchestration & Federation**: Agent-Pipe router + Hermes gateway + A2A federation bus.
- **Unified Datastore**: PostgreSQL + pgvector (mios-pgvector) for episodic memory, vector recall, and session persistence.
- **FHS Compliance**: Clean native hierarchy (/usr static vendor layer, /etc host overrides, /var persistent state via tmpfiles.d).
