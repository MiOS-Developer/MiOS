<!-- AI-hint: Reality-checked landscape of multi-agent orchestration strategies (OpenAI Agents SDK / Swarm, MCP, LangGraph/CrewAI/ADK, structured deliberation, document-mutation coordination, progressive-disclosure retrieval, identity-aware delegation, contribution evaluation) mapped onto the existing MiOS agent plane, with a MiOS gap register feeding ROADMAP Part 13 / task_155..task_162. -->
<!-- AI-related: usr/lib/mios/agent-pipe/server.py, usr/share/mios/agents/, ROADMAP.md (Part 13), TASKS.md (task_155..task_162), usr/share/doc/mios/concepts/chat-quality-and-visibility-standards.md -->

# Multi-Agent Orchestration Strategies — Landscape, Reality-Check, and MiOS Mapping

**Source:** operator-provided multi-vendor research digest (SSOT rationale → developer SSOT file formats → multi-agent coordination strategies), requested folded into roadmap/tasks/research.
**Posture:** reality-checked. MiOS already implements a large fraction of this landscape; this doc separates *upstream-verified* strategies from *unverifiable, post-cutoff claims*, maps each onto the existing MiOS agent plane, and registers only the genuine gaps.

---

## 0. Provenance & reality-check (read first)

The digest mixes well-established, upstream-verified frameworks with a cluster of protocols that trace to **single-author, post-cutoff arXiv preprints with future-dated identifiers** (`2603.xxxxx` = March 2026; assistant knowledge cutoff is January 2026). Those cannot be verified from training and carry the same signature as fabrications MiOS has already caught (LAKE / ProbeLogits — see `mios_target_and_plans_2026_06_25`). **The underlying *concepts* are sound and worth building; the *specific named protocols/papers* are captured as "evaluate-first, verify provenance," never cargo-culted.**

| Claim / artifact | Status | Handling |
|---|---|---|
| OpenAI Agents SDK (handoffs, agents-as-tools, guardrails, tracing, `context_variables`) | **Verified real** (GA 2025) | Adopt patterns directly |
| OpenAI Swarm (experimental routines/handoffs, `context_variables`) | **Verified real** (educational) | Adopt patterns |
| Model Context Protocol (MCP) | **Verified real** (Anthropic; MiOS already consumes) | Already shipped |
| LangGraph / CrewAI / Google ADK (A2A) | **Verified real** frameworks | Reference for topology/HITL |
| Multi-agent debate + Creator/Verifier + self-consistency/ensembling | **Verified real** research area | Already partially shipped |
| Swarm / Mesh / Hierarchical / Pipeline topology taxonomy | **Real taxonomy** | Reference |
| **DCI — Deliberative Collective Intelligence** (typed epistemic acts, archetypes, DCI-CF, decision packets) | **Concept sound; source UNVERIFIABLE** (`arXiv 2603.11781`, single author, post-cutoff) | Build the *concept* (structured deliberation) gated; do not cite the paper as authority |
| **LDP — LLM/Lightweight Delegate Protocol** (identity cards, payload modes, attested vs claimed quality, "Provenance Paradox", trust domains) | **Concept sound; source UNVERIFIABLE** (`arXiv 2603.18043`) | Extend MiOS A2A/agent-passport with the *concepts*; do not adopt the wire protocol blind |
| **OpenClaw AgentOS / "OpenClaw Meets Hospital"** (document-mutation coordination, manifest-guided progressive-disclosure retrieval) | **Concept sound; source UNVERIFIABLE** (`arXiv 2603.11721`; name pattern suspicious) | Build doc-mutation + progressive-disclosure on the *existing* pgvector plane |
| **IntrospecLOO** (introspective leave-one-out contribution eval) | **Concept sound; source UNVERIFIABLE** | Build cheap contribution scoring for the council/swarm |

**Binding constraint on everything below (Architectural Law 7 / NO-HARDCODE):** any "route this class of task to the heavy strategy" decision must be **model-driven** (a classifier / the orchestrator's own judgement), never a keyword/English/ASCII gate; every strategy ships **flag-gated in `mios.toml` and degrades open**; no magic weights or SSOT-restated literals.

---

## 1. What MiOS already has (map before you build)

MiOS is already ~70% of this landscape. The agent plane (`CLAUDE.md` "AI stack") covers most named strategies:

| Digest strategy | Existing MiOS mechanism |
|---|---|
| Triage & Handoff (front-desk router) | agent-pipe **router + refine** (`:8640`, `_env_grounding` + route hop) |
| Orchestrator-Worker / Agents-as-Tools | agent-pipe **council/swarm fan-out**; Hermes tool-loop (`:8642`); `delegation-prefilter` (`:8641`) injects `tool_choice=delegate_task` |
| Creator-Verifier / critic loop | agent-pipe **critic/polish** hop |
| Swarm topology + global tool surface | swarm agents get the **global verb/tool surface** (see `mios_swarm_tooluse_mcp_hybrid_fix`) |
| Shared memory / "clipboard" / context variables | **pgvector** unified datastore (`agent_memory`, `knowledge`, `session`, `skill`, `scratch`, `sys_env`) |
| MCP tool interop | **MCP consume** — 127 verbs via `:8765/mcp` + stdio `mios-mcp-server` |
| A2A agent federation + identity | **A2A card** (v0.3.0) + **`agent-passport.json` (Ed25519)** + `max_permission` risk gate (see `mios_ai_rename_opencode_a2a_2026_06_20`) |
| Model-tier cost routing | **light/heavy inference lanes** + `[ai.host_thresholds]` VRAM tiering |
| Per-turn native env grounding | `_env_grounding()` system-role `<env>` block (Law: native, not user-injection) |
| Reputation / contribution weighting | prior **reputation** workstream (WS master-plan) |

**Implication:** the roadmap items below are *enhancements and gap-fills to an existing plane*, not greenfield builds. Reuse `agent-pipe`, `pgvector`, A2A/agent-passport, and the light/heavy lanes.

---

## 2. Genuine gaps → strategies worth adopting

### 2.1 Typed handoffs + guardrails + tracing (OpenAI Agents SDK)
MiOS fans out, but handoffs are not modelled as **typed tool-calls** with **parallel input/output guardrails** and **first-class tracing spans**. Adopt: (a) handoff = a typed transfer function returning the target agent + a `Result` that updates shared context; (b) guardrails run *in parallel* to validate inputs/outputs (cheap model) and can short-circuit; (c) every hop emits a trace span (feeds the "everything streams natively" mandate). Server-side `context_variables` injection (hidden from the tool schema) prevents prompt pollution — MiOS already hides heavy state behind tools; formalize the light shared-context dict.

### 2.2 Structured deliberation for consequential tasks (DCI *concept*)
Upgrade the council from free-form debate to **structured deliberation** *only when the task warrants it*: differentiated archetype roles (Framer / Explorer / Challenger / Integrator) via **differentiated system prompts** (bias, not hardcoded capability), a **typed interaction grammar** (propose / challenge / evidence / reframe / synthesize / concede / …) so a challenge is structurally distinguishable from a proposal, **tension tracking** (disagreements preserved as first-class objects, not averaged away), and a **bounded convergence loop** that always terminates in a **Decision Packet** (chosen action + residual objections + minority report + reopen-conditions). **Cost reality from the digest: ~62× tokens vs a single agent, and it HARMS routine tasks** — so the trigger must be a **model-driven consequentiality classifier**, gated, defaulting off; routine work stays on the cheap path. Maps onto the existing council hop.

### 2.3 Document-mutation / event-bus coordination (OpenClaw *concept*, on pgvector)
Add a **decoupled, auditable async coordination lane**: agents coordinate by *mutating shared rows/documents* in pgvector rather than direct message-passing; a **`LISTEN`/`NOTIFY` (or logical-decode) event bus** wakes decoupled worker/daemon agents on the mutation. Benefits: permanent auditable trail (every trigger/decision is a DB row), reactive priority without polling, absolute decoupling (agents know only the shared schema). MiOS already has the datastore and daemon pattern — this is a coordination *mode*, not new infra. Use for long-running, high-audit workflows (the "MiOS-Daemon"-style supervisors).

### 2.4 Manifest-guided progressive-disclosure retrieval (OpenClaw *concept*)
Complement pure vector RAG for **large/longitudinal document trees** where a single cosine distance collapses temporal + scope + type relevance. Organize documents as a tree; each internal node carries a `manifest` (natural-language child descriptions); retrieval walks the tree via **LLM-select** (reason over descriptions, prune irrelevant subtrees) to a depth bound, instead of embedding-similarity alone. Manifest maintenance is **O(depth) per mutation** (local update). Adopt as an *additional retrieval strategy* selectable per query-class, not a replacement for pgvector recall.

### 2.5 Identity-aware delegation on A2A (LDP *concept*)
MiOS already ships `agent-passport.json` (Ed25519) + A2A card + `max_permission` — extend it toward the LDP *concepts*: capability/`reasoning_profile`/`context_window`/`cost_hint` fields for **metadata-aware routing** (cheap-fast model for simple subtasks, heavy model for hard reasoning), **attested vs self-claimed quality** to defeat the **Provenance Paradox** (routing on self-reported score selects the *worst* delegates), **governed sessions** (persistent context, no re-transmitting history each call), and **trust domains** (capability scopes / data-handling policy). This is an *extension of existing MiOS identity*, not a new protocol stack.

### 2.6 Progressive payload / token-efficiency modes (LDP *concept*)
Negotiate the richest mutually-supported payload mode for A2A/delegation: text (fallback, auditable) → **semantic frame (typed JSON)** → embedding hints → semantic graph. The digest claims ~37% token reduction at semantic-frame with no quality loss. Feeds the MiOS "native typed launch-args" + streaming mandates. Gated; text fallback always available for auditability.

### 2.7 Cheap contribution evaluation (IntrospecLOO *concept*)
For the council/swarm, score each agent's marginal contribution **without re-running the debate**: after a session, prompt the remaining agents to re-decide while ignoring agent *j*'s inputs; the delta approximates leave-one-out at O(N) instead of O(T·N²). Feeds the **reputation** table (down-weight consistently-negative or adversarial agents; surface high-value ones). Auditable, financially viable at scale.

### 2.8 Explicit topology + debate-protocol selection
Make the **topology** (pipeline / hierarchical / swarm / mesh) and **debate-protocol** (within-round vs cross-round vs rank-adaptive) *selectable per task-class* from SSOT + orchestrator judgement, with the trade-off documented: within-round maximizes peer-reference/interaction but converges slowly; rank-adaptive cross-round converges fastest. No hardcoded choice.

---

## 3. MiOS gap register (→ ROADMAP Part 13 / TASKS)

| Gap | Roadmap item | Task | Priority |
|---|---|---|---|
| Typed handoffs + guardrails + tracing spans | MAO-01 | task_155 | P2 |
| Structured deliberation (archetypes/typed acts/decision packet), model-gated | MAO-02 | task_156 | P2 |
| Document-mutation + `LISTEN/NOTIFY` coordination lane on pgvector | MAO-03 | task_157 | P3 |
| Manifest-guided progressive-disclosure retrieval | MAO-04 | task_158 | P3 |
| Identity-aware delegation (extend agent-passport/A2A; attested quality) | MAO-05 | task_159 | P2 |
| Progressive payload modes (semantic-frame delegation) | MAO-06 | task_160 | P3 |
| Cheap contribution eval (introspective LOO) → reputation | MAO-07 | task_161 | P3 |
| Selectable topology + debate protocol from SSOT | MAO-08 | task_162 | P2 |

All items: flag-gated in `mios.toml` (`[agents.orchestration]`), **model-driven triggers** (no keyword gates), degrade-open, reuse agent-pipe + pgvector + A2A. Heavy strategies (deliberation) default **off** and route only model-classified consequential tasks (the 62× token reality).

---

## 4. Sources (as provided; verify before citing)
Upstream-verified: OpenAI Agents SDK + Swarm docs; Anthropic MCP; LangGraph / CrewAI / Google ADK docs; multi-agent-debate literature.
**Unverifiable / post-cutoff (do NOT cite as authority):** DCI (`arXiv 2603.11781`), LDP (`arXiv 2603.18043`), OpenClaw-Hospital (`arXiv 2603.11721`), IntrospecLOO — all captured here as concepts pending independent verification.
