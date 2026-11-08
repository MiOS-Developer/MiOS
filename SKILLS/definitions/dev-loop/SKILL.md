---
name: dev-loop
description: Multi-harness engineering loop, terminal worker orchestrator, and autonomous research engine. Coordinates concurrent git worktrees, automated two-sided verification, phantom-failure triage, upstream research, project-native planning, and operator disambiguation across Gemini Spark, OpenAI Codex, Claude Code, Gemini CLI, Antigravity, OpenCode, and local terminal harnesses.
---

# Dev Loop: Universal Multi-Harness Engineering Framework & Terminal Worker Orchestrator

A unified execution framework and orchestrator for multi-harness, multi-agent, and multi-turn engineering workflows. Combines structured git worktree isolation with rigorous verification discipline, upstream truth-finding, phantom-failure triage, cross-platform runtime drivers, native slash-command integrations, project-native contract templates, artifact manifest hashing, and operator disambiguation.

---

## 1. Role and Topology

Dev Loop enforces strict separation of concerns across three structural tiers:

```
                  +-----------------------------------+
                  |         L0 Orchestrator            |
                  | Global Architecture & Worktrees   |
                  +-----------------+-----------------+
                                    |
          +-------------------------+-------------------------+
          |                                                   |
+---------v---------+                               +---------v---------+
|   L1 Supervisor   |                               |   L1 Supervisor   |
| Backend / Storage |                               | Frontend / UI / QA|
+---------+---------+                               +---------+---------+
          |                                                   |
    +-----+-----+                                       +-----+-----+
    |           |                                       |           |
+---v---+   +---v---+                               +---v---+   +---v---+
|  L2   |   |  L2   |                               |  L2   |   |  L2   |
|Worker |   |Worker |                               |Worker |   |Worker |
+-------+   +-------+                               +-------+   +-------+
```

- **L0 Orchestrator:** Global architecture, task decomposition, git worktree lifecycle management, dependency DAG coordination, merge gate validation, and cross-lane dependency reconciliation.
- **L1 Supervisors:** Domain context tracking (Backend, Frontend, QA, Security, Infrastructure), contract enforcement, interface synchronization, and sub-tree test verification.
- **L2 Workers:** Terminal agents running in visible or headless sessions (Windows Terminal, PowerShell, tmux, background subshells, or Python daemon) executing precision edits, unit/integration test suites, diff audits, and security scans.

---

## 2. Core Iteration Lifecycle

Execute these nine stages sequentially on every iteration:

1. **Research Upstream:** Check canonical vendor documentation, release notes, and active proposals before committing to architectural decisions or choosing replacements for deprecated APIs.
2. **Sync Project Plan and Contracts:** Update repository-native tracking files (`TODO.md`, `ROADMAP.md`, `TASKS.md`, `DOD.md`, `GOALS.md`) and agent guidelines (`AGENTS.md`, `CLAUDE.md`).
3. **Provision and Isolate:** Create dedicated git worktrees per concurrent lane (under `.worktrees/<worker_id>`) to prevent file collisions, index contention, and uncommitted state pollution.
4. **Implement with Precision & Atomicity:** Apply targeted, minimal edits addressing root causes rather than symptoms. Use atomic writes (`.tmp` -> rename) to avoid mid-write read corruption.
5. **Verify Hard (Two-Sided Validation):** Execute two-sided validation (positive control passes expected inputs, negative control fails strictly for the expected reason). Verify non-zero item counts processed.
6. **Triage Phantoms:** Differentiate environment, cache, and path artifacts from true code regressions before changing application code.
7. **Reconcile and Merge Gates:** Run test suites and diff audits inside each worktree before merging into the base branch. Merge atomically with conflict abort rollbacks (`git merge --abort`).
8. **Register Artifacts & Explicit Staging:** Register verified deliverables in `.devloop_artifacts/manifest.json` with SHA-256 hashes. Stage explicit file paths (`git add <file>`), write reasoned commit messages explaining what broke and how verification proved correctness, and track tiered CI pipelines to terminal states.
9. **Clarify and Report:** Proactively disambiguate high-impact forks with the operator using concrete options, blast radius estimates, and trade-off matrices.

---

## 3. Tool Specification (`dev_loop`)

The `dev_loop` tool coordinates multi-lane terminal execution. Schema definition:

```json
{
  "name": "dev_loop",
  "description": "Spawns and coordinates concurrent terminal worker agents in isolated git worktrees across multiple harnesses (OpenAI, Claude Code, Gemini/Cloud Code, Copilot, Antigravity, OpenCode, Cursor) to execute planning, editing, testing, and validation.",
  "parameters": {
    "type": "object",
    "properties": {
      "objective": { "type": "string", "description": "High-level engineering task or goal to implement." },
      "base_branch": { "type": "string", "default": "main", "description": "Base git branch to branch worktrees from and reconcile into." },
      "worktree_dir": { "type": "string", "default": ".worktrees", "description": "Root directory under which worktrees are provisioned (must be in .gitignore)." },
      "terminal_layout": { 
        "type": "string", 
        "enum": ["wt_grid", "detached_ps", "tmux_grid", "detached_sh", "headless_py"], 
        "default": "wt_grid", 
        "description": "Terminal session layout manager to launch." 
      },
      "timeout_seconds": { "type": "integer", "default": 3600, "description": "Per-lane timeout in seconds." },
      "lanes": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "domain": { "type": "string", "description": "Subsystem domain (e.g., backend, frontend, qa, security)." },
            "worker_id": { "type": "string", "description": "Unique worker identifier (alphanumeric, hyphens, underscores)." },
            "harness": { 
              "type": "string", 
              "enum": ["spark", "gemini-spark", "openai", "claude", "cloudcode", "gemini", "codex", "copilot", "antigravity", "opencode", "cursor", "custom"], 
              "description": "Agent harness CLI or native subagent driving the lane." 
            },
            "worktree": { "type": "string", "description": "Relative worktree path (e.g., .worktrees/lane-backend)." },
            "command": { "type": "string", "description": "Primary task command or prompt passed to the harness." },
            "test_cmd": { "type": "string", "description": "Optional validation or test command executed after task completion." },
            "env": { "type": "object", "description": "Optional environment variables injected into the worker process." }
          },
          "required": ["domain", "worker_id", "harness", "worktree", "command"]
        }
      },
      "reconcile_and_clean": { "type": "boolean", "default": false, "description": "Perform pre-merge verification, atomic merge, and cleanup." }
    },
    "required": ["objective", "lanes"]
  }
}
```

*See `reference/lane-schema.json` for the complete JSON Schema, and `reference/lanes.example.json` for full configuration examples.*

---

## 4. Universal Cross-Harness Command Matrix

| Intent | Gemini Spark (Native) | OpenAI / Codex | Claude Code CLI | Gemini / Cloud Code / Antigravity | GitHub Copilot / Cursor |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Pre-flight Check** | `/doctor`, `vm_shell:execute_bash` | `/skill:doctor` | `/doctor` | `/doctor` | `gh copilot --doctor`, `doctor` |
| **Project Indexing** | `/init`, `/audit`, `context_service_agent:get_context` | `/skill:register` | `/init`, `/memory` | `/init`, `agent-pipe:index` | `.cursor/rules`, workspace index |
| **Architecture Plan**| `/plan <task>`, `reference/lifecycle.py set refinement` | `/skill:plan <goal>` | `/plan <task>` | `/workflows:plan <task>` | `plan: <task>` |
| **Execute Workflow** | `/dev-loop <objective>`, `invoke_subagent` / `vm_shell` | `/skill:call <fn> <json>`| `claude -w <wt> -p <cmd>` | `/workflows:run <name>` | `gh copilot run`, `cursor-cli` |
| **Code Refactoring** | `/refactor <target>`, atomic `.tmp` edit loop | `/skill:call refactor` | `/simplify <target>` | `/workflows:simplify` | Refactor inline prompt |
| **Diff & Staging**   | `git diff --staged`, explicit `git add <file>` | `/skill:diff` | `/diff` | `/diff` | `git diff --staged` |
| **Test & Auto-Repair**| `python3 reference/triage.py`, `reference/goal.py eval` | `/skill:verify test`| `/debug <context>` | `/workflows:test-repair` | Test runner integration |
| **Security Review**  | `python3 reference/review.py HEAD` | `/skill:verify security`| `/security-review` | `/workflows:security-harden` | Security scan prompt |
| **Context Compact**  | `python3 reference/sync.py`, context compaction | `/compact` | `/compact <focus>` | `/compact` | Session log compaction |
| **Telemetry / Cost** | Read `.devloop_reports/*.json`, export to Drive | `/usage` | `/cost` | `/cost`, `agent-pipe:metrics` | Read `.devloop_reports/*.json` |

---

## 5. Universal Slash Commands & Installation Suite

Dev Loop provides native custom slash commands and prompt configurations pre-built for every major agent environment under `commands/`:

- **Google Gemini Spark (Native):** `commands/spark/*.md` (`skills/dev-loop/commands/spark/*.md`)
- **Anthropic Claude Code:** `../../workflows/dev-loop.md` (`~/.claude/commands/dev-loop.md`)
- **OpenAI Codex:** `commands/codex/dev-loop.md` (`~/.codex/prompts/dev-loop.md`)
- **Google Gemini CLI / AGY:** `commands/gemini/dev-loop.toml` (`~/.gemini/commands/dev-loop.toml`)
- **Google Antigravity:** `commands/antigravity/dev-loop.md` (`~/.antigravity/workflows/dev-loop.md`)
- **GitHub Copilot (Prompts):** `commands/copilot/dev-loop.prompt.md` (`.github/prompts/dev-loop.prompt.md`)
- **GitHub Copilot (Agents):** `commands/copilot/dev-loop.agent.md` (`.github/agents/dev-loop.agent.md`)
- **OpenCode:** `commands/opencode/dev-loop.md` (`~/.opencode/commands/dev-loop.md`)
- **Cursor (Rules & MDC):** `commands/cursor/dev-loop.mdc` & `commands/cursor/dev-loop.md` (`.cursor/rules/dev-loop.mdc`)

### Automated Installation Drivers

- **Linux / macOS:**
  ```bash
  ./reference/install.sh --global    # Install globally into ~/.claude, ~/.gemini, etc.
  ./reference/install.sh --project   # Install project-locally into .github, .cursor, etc.
  ```
- **Windows (PowerShell):**
  ```powershell
  .
eference\install.ps1 -Scope Global    # Install globally
  .
eference\install.ps1 -Scope Project   # Install project-locally
  ```

---

## 6. Worktree Concurrency and Tree Hygiene Laws

Multi-agent concurrent execution requires uncompromising git hygiene:

1. **Dedicated Worktree Hierarchy:**
   - Store all worktrees inside `.worktrees/<worker_id>` (never flat in the parent project tree).
   - Ensure `.worktrees/` is present in `.gitignore` before creating any worktree to prevent repo index pollution.
2. **Branch Collision Defense:**
   - Always verify if a branch exists (`git show-ref --verify refs/heads/<branch>`) before creating.
   - If the branch exists and is detached or unlinked, attach to it: `git worktree add <path> <branch>`.
   - Never run unconditional `git worktree add -b <branch>` without verifying existence.
3. **The `.git` Pointer File Invariant:**
   - In linked git worktrees, `.git` is a regular ASCII pointer file (`gitdir: ...`), NOT a directory.
   - Never run `[ -d .git ]` or `Test-Path .git -PathType Container`.
   - Always resolve via `git rev-parse --git-dir` or `git rev-parse --show-toplevel`.
4. **Lockfile Contention & Exponential Backoff:**
   - Multiple parallel git commands can trigger `.git/index.lock` collisions.
   - Implement exponential backoff retry (up to 5 attempts, 500ms intervals) before throwing git lock exceptions.
5. **Strict Disjoint Partitioning:**
   - Partition concurrent agents into strictly disjoint directories and file sets.
   - If two agents must touch the same subsystem, serialize them through a supervisor or run them in sequential phases.
6. **Zero Tolerance for Blanket Staging:**
   - **NEVER** run `git add -A`, `git add .`, or `git add <directory>`.
   - Always stage explicit, individual file paths: `git add src/telemetry/queue.py tests/test_queue.py`.
   - Blanket adds sweep uncommitted collaborator files, temporary test fixtures, and secret-bearing scratchpads.
7. **Collaborator Non-Interference:**
   - Never delete or revert untracked or uncommitted files created by another agent or human operator.
   - Run `git status` and `git log -1 --stat` before making assumptions about unfamiliar files.

---

## 7. Hardened Verification Discipline ("Verify, Do Not Believe")

An exit code of `0` is not proof of success. Silent pipelines, empty globs, and swallowed exceptions can return `0` while leaving the task broken.

1. **Non-Zero / Non-Truncation Assertion:**
   - After every file creation or modification, verify the file is non-empty and syntactically valid:
     - Shell: `wc -c <file>` (> 0) and `bash -n <file>`
     - Python: `python3 -m py_compile <file>`
     - Rust: `cargo check --message-format=json`
     - TypeScript: `tsc --noEmit`
   - Never commit a 0-byte or truncated file.
2. **Atomic File Writes:**
   - Never stream directly to a live production file that concurrent processes might read mid-write.
   - Write to a sibling temporary file (`<file>.tmp`), validate size and syntax, then atomically rename (`mv <file>.tmp <file>`).
3. **Two-Sided Validation:**
   - **Positive Control:** The valid input/configuration passes acceptance criteria and produces expected artifacts.
   - **Negative Control:** A planted defect (e.g., missing parameter, invalid schema, mutated assertion) fails strictly for the expected reason and names the defect.
4. **Eliminate Checks That Cannot Fail (Skip-as-Pass Anti-Pattern):**
   - A missing optional development tool may skip with a warning.
   - A missing tracked deliverable, SSOT file (`mios.toml`, `schema.sql`), or test fixture **MUST FAIL WITH EXIT CODE 1**.
   - Absence of a required artifact is an anomaly, never a reason to pass silently.
5. **Shrink-Only Ratchets:**
   - A ratchet that can be raised is not a ratchet.
   - Never increase an error threshold, warning budget, or exemption list to make a test suite pass.
   - Store itemized cryptographic hashes (SHA-256) of accepted exceptions rather than loose scalar counts.
6. **Fixture Leak Prevention:**
   - Negative tests that mutate state or plant temporary fixtures must trap signals (`trap 'cleanup' EXIT INT TERM` in bash, `try ... finally` in Python) to guarantee cleanup.
   - Run fixture leak audits (`git status --porcelain`) to ensure tests leave no residue in the working tree.
7. **Forward-Slash Path Normalization:**
   - Gate checks comparing paths against SSOT or registries must normalize all paths with forward slashes (`/`). Windows backslashes (`\`) cause false drift failures across platforms.
8. **Fix Root Cause, Not Symptom:**
   - Never add fresh regressions to suppression lists, linters ignores, or baseline drift files. Fix the source code.

---

## 8. Phantom-Failure Triage Runbook

When a test or build fails, execute this 5-step triage before modifying code:

```
+--------------------------------------------------------------+
| 1. Local vs CI Path Mismatch Check                           |
|    Does the failure cite absolute host paths instead of repo |
|    relative paths? Fix paths to repo-relative first.         |
+------------------------------+-------------------------------+
                               |
+------------------------------v-------------------------------+
| 2. Stale Build Artifact / Cache Invalidation                 |
|    Do error line numbers disagree with current source code?  |
|    Clear __pycache__, .pytest_cache, target/, .cache.        |
+------------------------------+-------------------------------+
                               |
+------------------------------v-------------------------------+
| 3. Linked Worktree .git Pointer Verification                 |
|    Is a tool failing because .git is a file, not directory?  |
|    Use git rev-parse --git-dir.                              |
+------------------------------+-------------------------------+
                               |
+------------------------------v-------------------------------+
| 4. Root Failure Isolation                                    |
|    Find the EARLIEST error in the log. Do not debug cascade  |
|    errors (missing imports caused by failed earlier build).  |
+------------------------------+-------------------------------+
                               |
+------------------------------v-------------------------------+
| 5. Manifest & Projection Synchronization                     |
|    Were files added or removed without updating registries,  |
|    generated names, or build catalogs? Regenerate projections|
+--------------------------------------------------------------+
```

---

## 9. Safe Reconciliation & Merge Gate Protocol

Reconciling concurrent lanes into the base branch must be atomic and verified:

```
[Lane Worktree] ----> Pre-merge Status Check (git status --porcelain == clean)
                             |
                             v
                      Pre-merge Test Gate (run test_cmd in worktree)
                             | (PASS)
                             v
[Base Branch]  <----- Atomic Merge (git merge --no-ff <branch>)
                             |
                   +---------+---------+
                   | (SUCCESS)         | (CONFLICT)
                   v                   v
            Remove Worktree     git merge --abort
            Delete Branch       Keep Worktree & Branch
            Post-Merge Gate     Report to Operator
```

1. **Pre-Merge Dirty Check:** Verify the lane worktree has zero uncommitted changes (`git status --porcelain`). If dirty, halt reconciliation.
2. **Pre-Merge Test Execution:** Execute `$lane.test_cmd` *inside* the worktree. If tests fail, halt and flag the lane.
3. **Base Branch Readiness:** Verify the base branch itself has a clean working tree.
4. **Atomic Merge with Conflict Abort:**
   - Execute `git merge --no-ff <branch> -m "Merge lane <worker_id> for: <objective>"`.
   - If merge fails or conflicts occur, **IMMEDIATELY EXECUTE `git merge --abort`**.
   - Do NOT force-remove the worktree or branch upon conflict. Keep them intact for operator review.
5. **Post-Merge Clean Teardown:**
   - Remove worktree: `git worktree remove <worktree_path>`.
   - Delete branch: `git branch -d <branch>`.
   - Run overall integration test suite on the base branch.

---

## 10. Operator Disambiguation Protocol

When encountering ambiguous requirements, breaking schema migrations, or irreversible operational blast radii:

1. **Trigger Conditions:**
   - Conflicting specifications between task description and repository code/documentation.
   - Breaking API signatures or database schema alterations affecting other systems.
   - Ambiguity that could result in more than 20% wasted execution time.
2. **Disambiguation Format:**
   - Present exactly **2 to 4 concrete, actionable options**.
   - For each option, specify:
     - Proposed implementation details.
     - Affected file paths.
     - Blast radius and performance/security trade-offs.
     - Upstream documentation or precedent in codebase.
3. **Maintain Async Non-Blocking Progress:**
   - While awaiting operator clarification on the blocked lane, continue executing non-dependent lanes and subtasks.
4. **Structured Turn Status Report:**
   - **Executive Summary:** Progress since previous turn.
   - **Verification Evidence:** Exact outputs of positive and negative controls.
   - **Identified Phantoms:** Environment/cache/path discrepancies resolved.
   - **Blockers & Decisions:** Active options requiring operator input.
   - **Next Iteration Plan:** Immediate steps upon resolution.

---

## 11. Artifacts Lifecycle & Cryptographic Manifest Management

All verified outputs produced during an iteration must be registered, hashed, and tracked to eliminate silent regressions and truncated files:

- **Manifest Storage:** Stored in `.devloop_artifacts/manifest.json`.
- **Automated Verification:** The helper module `reference/artifacts.py` validates SHA-256 hashes and asserts that no file is truncated or 0 bytes:
  ```bash
  python3 reference/artifacts.py register src/api/queue.py --type code
  python3 reference/artifacts.py verify
  ```
- **Taxonomy Categories:** `code`, `test`, `doc`, `config`, `schema`, `telemetry`.
- **Integrity Guarantee:** Prohibits committing any deliverable that does not match its registered manifest fingerprint. See `reference/artifacts.md` for full specification.

---

## 12. Project-Native House Schema & Repository Contract Templates

Dev Loop provides standard templates under `reference/templates/` to synchronize contracts between operators and agents:

- **`templates/GOALS.md`**: Sprint & milestone functional invariants and non-goals.
- **`templates/DOD.md`**: Strict Definition of Done criteria (root cause, two-sided verification, zero suppressions).
- **`templates/ROADMAP.md`**: Tiered milestones, cross-lane dependency horizon, and risk register.
- **`templates/adr.md`**: Standard Architecture Decision Record format.
- **`templates/tasks.jsonl`**: Machine-readable JSONL task DAG tracking dependencies, status, and verification commands.
- **`templates/LEDGER.md`**: Persistent decision, triage, and regression ledger.
- **`templates/AGENTS.md`**: Multi-agent operational contract defining disjoint file partitions and staging rules.
- **`templates/CHECKLISTS.md`**: Pre-flight, implementation, merge gate, and release checklists.
- **`templates/CHANGELOG.md`**: Semantic versioning changelog following Keep-a-Changelog standard.

---

## 13. Goal-Driven Development Engine (`/goal` & `/goal dev`)

The Dev Loop Goal Engine couples autonomous execution loops with explicit, testable **stopping conditions**:

```
+--------------------------------------------------------------+
|   The Mental Model: Heartbeat vs. Stopping Condition         |
|   - /dev-loop (or /loop): The Heartbeat (spawns turns/tasks) |
|   - /goal (or /goal dev): The Stopping Condition (when done) |
+--------------------------------------------------------------+
```

### The `/goal dev` Execution Lifecycle

When an operator or supervisor invokes `/goal dev <objective>`:

1. **Goal Anchoring & Acceptance Invariants:**
   - Automatically initializes `GOALS.md` with:
     - Clear, testable objective statement.
     - Formal stopping conditions (e.g., all 45 integration tests pass, zero lint warnings, manifest SHA-256 validated).
     - Explicit non-goals defining blast-radius boundaries.
2. **DAG Task Decomposition:**
   - Decomposes acceptance criteria into machine-readable task dependencies in `tasks.jsonl`.
3. **Execution Heartbeat:**
   - Launches `devloop_worker.py` or harness worker sessions inside dedicated `.worktrees/<worker_id>`.
4. **Autonomous Stopping Condition Evaluation:**
   - After each turn, the agent evaluates `reference/goal.py eval`.
   - If any stopping criterion fails, the loop continues to the next turn, applying phantom triage.
   - The loop will NOT stop prematurely or report false success based on exit code 0.
5. **Cryptographic Proof & Verified Teardown:**
   - Once all criteria pass, verified artifacts are hashed in `.devloop_artifacts/manifest.json`.
   - Worktrees are merged atomically with `--no-ff`, branches pruned, and proof reported to the operator.

```bash
# Goal CLI Commands
python3 reference/goal.py init "Migrate to OAuth2 OIDC" --stop "pytest tests/auth/ passes && git status clean"
python3 reference/goal.py eval      # Evaluates all acceptance invariants
python3 reference/goal.py status    # Displays progress and active blockers
```

---

## 14. The 7-Phase Deterministic Dev-Loop State Machine

Operational reliability in autonomous software engineering is governed by the outer harness rather than stochastic prompt instructions. When an agent decides its own state through natural language prompts, it frequently skips verification gates, hallucinates completion, or fails to recover from tool errors. 

Production engineering requires the **separation of state from context**: the routing logic and phase progression reside in a deterministic, harness-agnostic state machine (`reference/lifecycle.py`) with **ZERO LLM calls in the router's decision path**.

```
+---------------------------------------------------------------------------------------------------------+
|                               The 7-Phase Deterministic Dev-Loop State Machine                          |
|                                                                                                         |
|  [1. issue_intake]        --> Normalize requirements, map authoritative issue state, init state spine   |
|  [2. refinement]          --> Socratic issue grilling (/loop-grill), harden acceptance criteria, DOD    |
|  [3. implementation]      --> Provision isolated Git worktree, run edit-test loop, pass local assertions|
|  [4. draft_gate]          --> Push branch, open Draft PR, verify CI pre-checks (requireCi assertions)   |
|  [5. feedback_resolution] --> Parse review threads, reproduce regressions, apply minimal targeted fixes  |
|  [6. pre_approval_gate]   --> Multi-perspective critic panel (/loop-review: DRY, KISS, SRP, Security)  |
|  [7. merge]               --> Safe atomic merge via API, prune worktrees, update CHANGELOG.md & state   |
+---------------------------------------------------------------------------------------------------------+
```

### 1. The 7 Canonical Phases & Deterministic Gates
1. **`issue_intake`**: Normalizes raw bug reports, extracts reproducible constraints, verifies against linked PRs, and maps state into `.devloop_state.json`.
2. **`refinement` (`/loop-grill`)**: Conducts Socratic grilling of requirements, hardens acceptance criteria, establishes two-sided test strategies, and verifies value provenance (`reference/provenance.py`).
3. **`implementation`**: Provisions an isolated Git worktree (`worktrees/lane-[name]`), executes the CodeAct/ACI edit-and-test loop, and enforces that the worktree diff is strictly bounded.
4. **`draft_gate`**: Pushes feature branch, opens GitHub Draft PR, and gates on automated CI status. The router blocks advancement until CI is green.
5. **`feedback_resolution`**: Ingests human and automated review comments, reproduces flagged regressions, applies minimal surgical fixes, and verifies all threads are resolved.
6. **`pre_approval_gate` (`/loop-review`)**: Enforces the **Maker-Checker pattern** via a multi-perspective critic panel (DRY, KISS/YAGNI, SRP/SoC, and Security/Invariants) plus running-app UI review.
7. **`merge` (`/ship`)**: Executes `--no-ff` merge into `main` with automatic rollback on conflict, prunes ephemeral worktrees, deletes lane branches, and records release entries in `CHANGELOG.md`.

---

## 15. The Two-Clock Execution Pipeline & The Ratchet Routine

```
+---------------------------------------------------------------------------------------------------------+
|                                    Two-Clock Execution Architecture                                     |
|                                                                                                         |
|  FAST CLOCK (In-Loop Micro-Recovery)                                                                    |
|    - PostToolUse Hook: Runs linters and compilers immediately on modified files.                        |
|    - Diagnostic Injection: Stderr feedback injected directly into next turn for self-repair.           |
|    - State Rollback: git reset --hard HEAD on loop thrashing or corrupted syntax trees.                 |
|                                                                                                         |
|  SLOW CLOCK (Cross-Session Ratchet Routine)                                                             |
|    - Failure Codification: Escaped agent mistakes logged in HARNESS.md.                                 |
|    - Programmatic Fencing: Permanent outer hooks codified in .devloop_rules.json / settings.json.       |
|    - Irreversibility: Once fixed by the harness, an error class CANNOT recur in future runs.            |
+---------------------------------------------------------------------------------------------------------+
```

### Fast-Clock Feedback & Micro-Recovery
During execution, every action passes through interceptor hooks (`PreToolUse`, `PostToolUse`). If an edit introduces syntax or type errors, the compiler diagnostic is immediately piped into the agent's observation window, enabling fast self-correction. If an agent thrashes or enters a poisoned state, `ratchet.py rollback` triggers an atomic `git reset --hard HEAD` to return the worktree to a clean checkpoint.

### Slow-Clock Codification (The Ratchet Habit)
Agent mistakes are not treated as stochastic anomalies to be addressed by prompt tweaks. Every systemic failure is diagnosed, categorized (M-CPE, X-CPE, AST Invalidation, Path Escape, Timeout), and permanently codified into `HARNESS.md` and `.devloop_rules.json`. This turns organizational learning into permanent, programmatic outer-harness guardrails.

---

## 16. Structural Repository Intelligence & Role-Based MCP Profiling

General-purpose MCP tool injection floods the context window with massive JSON schemas. Dev Loop implements **Role-Based Capability Profiling** (`reference/mcp_profiles.json`), dynamically activating only the MCP servers required for the active phase:

| Operational Role | Active Lifecycle Phase | Curated MCP Servers & Primitives | Token Efficiency Impact |
| :--- | :--- | :--- | :--- |
| **`Explorer`** | `issue_intake`, `refinement` | `ast-grep-mcp`, `mcp-server-filesystem`, `mcp-server-git` | High recall with structural AST filtering. |
| **`Architect`** | `refinement` | `mcp-lsp`, `ast-grep-mcp`, `context7-mcp` (docs) | Exact compiler references; eliminates doc hallucinations. |
| **`Builder`** | `implementation` | `ast-grep-mcp`, `mcp-server-filesystem`, `mcp-server-git` | Minimal schema footprint during active coding. |
| **`Critic`** | `pre_approval_gate` | `mcp-lsp`, `mcp-server-git`, `ast-grep-mcp` | Compiler-verified cross-reference and AST diffing. |
| **`UI Verifier`** | `pre_approval_gate` | `playwright-mcp`, `chrome-devtools-mcp` | Live browser driving, DOM inspection, screenshots. |

---

## 17. Complete Reference Drivers and Contract Templates

All automation engines, adapters, schemas, and commands are indexed below:

- **`reference/lifecycle.py`**: Deterministic 7-Phase Dev-Loop State Machine Engine.
- **`reference/lifecycle.md`**: Specification of the 7-Phase Dev-Loop State Machine.
- **`reference/ratchet.py`**: Two-Clock Execution Pipeline and Ratchet Routine Engine.
- **`reference/ratchet.md`**: Specification of Fast-Clock and Slow-Clock Ratchet protocols.
- **`reference/critic.py`**: Multi-Perspective Maker-Checker Critic Panel Engine (DRY, KISS, SRP, Security).
- **`reference/critic.md`**: Specification of the Multi-Perspective Critic Panel.
- **`reference/provenance.py`**: Value Provenance Engine enforcing the "Decision-Owed" gate before building.
- **`reference/provenance.md`**: Specification of the Value Provenance Protocol.
- **`reference/audit.py`**: Brownfield repository inspector and hierarchical `AGENTS.md` context generator.
- **`reference/audit.md`**: Specification of the Brownfield Repository Audit Protocol.
- **`reference/sync.py`**: State and documentation reconciler keeping context files aligned with git commits.
- **`reference/sync.md`**: Specification of the State Synchronization Protocol.
- **`reference/mcp.py`**: Role-based MCP capability profiler.
- **`reference/mcp_profiles.json`**: Role-to-server capability mapping definitions.
- **`reference/research.py`**: Upstream research engine, repository tag/SHA differ, doc search, and template scaffolder.
- **`reference/goal.py`**: Automated Goal Engine and stopping-condition evaluator.
- **`reference/triage.py`**: Automated Phantom-Failure Triage Engine, flaky test detector, and repro generator.
- **`reference/review.py`**: SCOPE Staged Code Review Engine with multi-model independent evaluation support.
- **`reference/ship.py`**: Safe Trunk-Based Shipping Engine, worktree reconciler, and changelog updater.
- **`reference/DevLoop.ps1`**: Hardened PowerShell runtime for Windows environments.
- **`reference/devloop.sh`**: Hardened POSIX Bash runtime for Linux / macOS.
- **`reference/devloop_worker.py`**: Cross-platform concurrent Python worker orchestrator.
- **`reference/templates/`**: 17 repository-native contract templates:
  `HARNESS.md`, `progress.md`, `adr.md`, `GOALS.md`, `DOD.md`, `AGENTS.md`, `SPIKE.md`, `UPSTREAM_AUDIT.md`, `TECH_EVAL.md`, `TRIAGE_INCIDENT.md`, `REVIEW_RUBRIC.md`, `RELEASE_CHECKLIST.md`, `ROADMAP.md`, `tasks.jsonl`, `LEDGER.md`, `CHECKLISTS.md`, `CHANGELOG.md`.
- **`commands/`**: Slash commands and prompt templates across Claude Code, Gemini CLI, OpenAI Codex, GitHub Copilot, Google Antigravity, OpenCode, and Cursor:
  `/dev-loop`, `/goal`, `/loop-grill`, `/loop-review`, `/research` (`/rs`), `/websearch` (`/s`), `/audit`, `/sync`, `/triage` (`/tr`), `/review` (`/rv`), `/ship` (`/sh`).

---

## 18. MiOS & Upstream Systems Conformance Invariants

Engineering workflows targeting the MiOS operating system (`mios-dev/MiOS`) and host ignition layer (`mios-dev/mios-bootstrap`) must strictly enforce upstream kernel, container, and immutability invariants:

### 1. The 5-Phase Host Ignition Lifecycle (`mios-bootstrap`)
- **Phase 0: Probing:** Interrogates hardware topology (CPU flags `avx512f`/`amx`, memory bounds via `/proc/meminfo`, GPU PCI vendor IDs `10de`/`1002`/`8086`) into `/etc/mios/identity.env`. Must use strict regex tokenization and clean temporary file renames (`cat <<EOF > identity.env.tmp && mv identity.env.tmp identity.env`) to eliminate arbitrary command injection vectors.
- **Phase 1: Root Overlay:** Merges system FHS overlays (`etc/`, `usr/share/`, `var/lib/`) into host root using streaming tar with explicit metadata preservation (`--numeric-owner --preserve-permissions --xattrs`).
- **Phase 2: SSOT Deploy:** Deploys `system-prompt.md` to `/etc/mios/ai/system-prompt.md` as the authoritative cognitive configuration.
- **Phase 3: User Staging:** Configures target user identity, groups (`wheel`, `libvirt`, `kvm`), and Ed25519 SSH keys.
- **Phase 4: Transition:** Invokes atomic transition via `bootc switch ghcr.io/MiOS-DEV/mios:latest` or FHS overlay.

### 2. Hardware Resource Tier Classification & UMA APU Detection
Hardware probing automatically resolves deployment tiers:
- **Ultra Tier:** $\ge 24	ext{ GB}$ discrete VRAM or $\ge 64	ext{ GB}$ system RAM (or unified APU memory like AMD Ryzen AI Max 395). Deploys heavy model `qwen2.5-coder:32b` (Q4_K_M) + `nomic-embed-text`.
- **High Tier:** $12	ext{--}16	ext{ GB}$ VRAM or $32	ext{--}48	ext{ GB}$ system RAM. Deploys `qwen2.5-coder:14b` (Q4_K_M).
- **Standard / CPU Tier:** $< 8	ext{ GB}$ VRAM or $16	ext{ GB}$ system RAM. Disables heavy lanes; all queries route to `qwen3.5:2b` via `mios-llm-light` (:11450).

### 3. The 5-Gate Conformance Suite (`98-drift-checks.sh`)
Every pull request or release branch in `MiOS` must pass all five conformance gates:
1. **BOUND-IMAGES Symlink Integrity:** All container images in `/usr/lib/bootc/bound-images.d/` must resolve to pinned digests.
2. **NO-MKDIR-IN-VAR Build Boundary Purity:** Zero hardcoded directory creation inside `/var` during container build. All `/var` state directories must be provisioned dynamically via `systemd-tmpfiles.d`.
3. **USR-OVER-ETC Fallback Verification:** Vendor defaults live in `/usr/share/mios/` or `/usr/lib/`; `/etc/mios/` serves strictly for administrative overrides.
4. **Bootc Container Lint:** Image passes `bootc container lint` with zero errors.
5. **Runtime Permissions & ACL Security Audit:** Binary capabilities (`setcap`), SELinux contexts, and unprivileged user namespace boundaries verified.

### 4. Upstream 24-Hour Delta Vectors & CVE Mitigations
- **Linux Kernel 7.2.7:** Incorporates commit `a73d902e` (Blackwell CXL 1:1 HDM decode collision fix with AMD-Vi page tables under `iommu=pt`) and mitigates CVE-2026-88129 (`nf_tables` use-after-free LPE) via unprivileged namespace hardening (`user.max_user_namespaces=28633`).
- **Podman v5.8.2 & crun v1.20:** Fixes CVE-2026-34821 (mount path traversal in subordinate namespaces `--userns=keep-id`) and resolves pasta socket detach races during systemd teardown.
- **bootc v1.16.13 & ostree v2026.3:** PR #2468 (composefs-sealed `/usr` hardlink collision auditing) and PR #2475 (`/etc/subuid` and `/etc/subgid` allocation persistence across 3-way atomic upgrades).
- **Inference Runtimes:** SGLang v0.5.20 and llama.cpp b4210 fix CVE-2026-3188 (GGUF v4 heap overflow) and CUDA stream synchronization for dual Blackwell GPUs.
- **MCP SDKs (v2.0.1 Python / v2.0.2 TS):** RFC-8832 stream cancellation protocol enforcement across all Model Context Protocol tool servers.
- **Vector Memory (pgvector 0.8.2):** In-place HNSW parallel index build overflow mitigation (CVE-2026-3172).

---

## 19. Gemini Spark Native Agent & Subagent Orchestration Workflow

Gemini Spark functions as a native, tool-calling autonomous research and engineering orchestrator. It bridges terminal execution in sandbox containers with persistent Workspace delivery:

```
+---------------------------------------------------------------------------------------------------------+
|                                  Gemini Spark Dev-Loop Execution Architecture                           |
|                                                                                                         |
|  [Orchestration Tier]     --> Gemini Spark evaluates contracts (GOALS.md, AGENTS.md, progress.md)        |
|  [Subagent Concurrency]   --> Spawns parallel worker instances via default_api:invoke_subagent          |
|  [Terminal Execution]     --> Runs precision edits, compilers, & tests via vm_shell:execute_bash        |
|  [Upstream Truth-Finding] --> Ingests context via context_service_agent:get_context, google:search      |
|  [Deliverable Delivery]   --> Exports briefs, proofs, & manifests to Google Drive (e.g. MiOS-RnD)       |
+---------------------------------------------------------------------------------------------------------+
```

### 1. Native Execution Rules for Gemini Spark
1. **Direct Terminal Loop (`vm_shell:execute_bash`):**
   - Execute shell scripts, compilers, and test suites directly in the workspace environment.
   - Assert non-zero outputs and validate syntax (`python3 -m py_compile`, `bash -n`) before committing.
   - Use temporary sibling files (`<file>.tmp`) and atomic renames (`mv <file>.tmp <file>`).
2. **Subagent Parallelization (`default_api:invoke_subagent`):**
   - When handling multi-domain tasks (e.g. backend refactoring + frontend updates + test harness repairs), decompose the objective into disjoint worktrees (`.worktrees/lane-<domain>`) and invoke subagents concurrently.
   - Subagents execute bounded, isolated tasks and return verified diffs and test logs to the L0 Orchestrator.
3. **Upstream Research & Grounding:**
   - Execute `/research` and `/websearch` using `google:search`, `google:browse`, and `context_service_agent:get_context`.
   - Never guess API signatures or migration paths. Cross-reference repository commit SHAs and upstream release notes.
4. **Persistent Workspace Delivery (`drive:*`):**
   - Files generated inside the ephemeral VM sandbox are not permanently accessible to the operator once the session terminates.
   - All critical deliverables (research briefs, architecture evaluation reports, test verification telemetry, and artifact manifests) must be exported to Google Drive (such as the `MiOS-RnD` project folder) using `drive:create_file` or `drive:update_file`. Include clickable Drive URLs in the final response.

