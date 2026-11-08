---
name: agents
description: List, inspect, and load the specialist MiOS-Dev agents and subagents for the current AGY environment.
---

# MiOS-Dev Agents & Subagents Directory

This workflow inspects, verifies, and activates the loaded specialist subagents for MiOS development within the active Google Antigravity (AGY) session.

## Active Session Subagents
The following specialist agents are defined and available for immediate delegation via `invoke_subagent`:

| Agent Name | Role | Primary Domain | Core Capabilities |
| :--- | :--- | :--- | :--- |
| **`mios-dev`** | Primary MiOS Developer | Substrate & OS Core | Full stack MiOS development, FHS overlay, 5 architectural invariants, OpenAI-compatible AI routing. |
| **`pipeline-auditor`** | Gate & Ratchet Auditor | CI/CD & Security | Standing gate checks (phase-registry, ratchet-direction, credential-literals, version-literals-ssot, signature-policy). |
| **`pipeline-worker`** | Linux Core & Rust Developer | Native Binaries & Services | Hardened Rust static binaries, systemd units, `/usr/libexec/mios` utilities, two-sided unit tests. |
| **`pipeline-reviewer`** | SCOPE Reviewer & Verifier | Code Oversight & Quality | SCOPE staged code reviews, contract preservation, positive/negative control verification. |
| **`artifact-publisher`** | Release & SSOT Publisher | Packaging & Projections | `tools/sync-generated.sh`, UKI kernel cmdline drop-ins, SBOM generation, `.devloop/LEDGER.md` receipts. |
| **`pipeline-orchestrator`** | Dev-Loop Coordinator | Multi-Lane Orchestration | Isolated worktree management, `tasks.jsonl` queueing, subagent dispatching and verification. |

## Invocation Protocol
To delegate tasks to any specialized agent in this environment:
```json
{
  "TypeName": "pipeline-worker",
  "Role": "Rust Systems Engineer",
  "Prompt": "Implement the requested feature under native Linux FHS with unit tests..."
}
```
Or for multi-agent audits:
```json
{
  "TypeName": "pipeline-auditor",
  "Role": "CI Auditor",
  "Prompt": "Audit all 6 standing gates and verify that ratchet ceilings did not increase..."
}
```

## Discovery Paths Loaded
- Workspace definitions: `.agents/agents/*.md` & `.agents/subagents.json`
- Global environment configurations: `~/.gemini/config/agents/*.md`
- Active session registry: `define_subagent` registered in memory
