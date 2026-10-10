<!-- AI-hint: ADR-0029 adds a coordinator lease (agent.lease, Kubernetes Lease semantics) beside the relay's presence lease, so a standby harness can detect the holder's credit exhaustion from its transcript and take over; maps the relay, translation layer and tmux spawning onto A2A v1.0, MCP 2026-07-28 tasks, OpenAI handoffs/Responses, OpenTelemetry GenAI and AGENTS.md. -->
<!-- AI-related: /usr/share/doc/mios/adr/README.md, .agents/COORDINATION.md, /usr/share/doc/mios/mcp-tmux.md, tools/native/mios-agent-relay, /usr/libexec/mios/mios-mcp-server, /usr/lib/mios/mios_translate.py, /usr/share/mios/mios.toml [mcp.agents], [agent_cli] -->
---
adr: 0029
title: Agent succession and interop -- a coordinator lease over the MiOS-MCP relay, mapped to A2A, MCP tasks, OpenAI handoffs and OpenTelemetry GenAI
status: proposed
date: 2026-10-09
deciders: [operator, ai-pair]
tags: [agents, mcp, relay, a2a, openai, lease, succession, tmux, translation, interop]
laws: [7, 8, 9, 11, 14, 15]
ssot_keys: [mcp.agents, agent_cli]
related_ws: []
supersedes: []
superseded_by: []
---

# ADR-0029: Agent succession and interop -- a coordinator lease over the MiOS-MCP relay, mapped to A2A, MCP tasks, OpenAI handoffs and OpenTelemetry GenAI

## Status

proposed — 2026-10-09.

The operator's directive: Antigravity works beneath Claude Code as subagents, in parallel, probes Claude Code's status, and fully takes over once Claude Code exhausts its credits. The findings are to be folded into OpenAI and open-source AI standards and into MiOS-MCP's translation layer, with tmux-mcp spawning local and cloud subagents.

Phase 0 (below) runs today. An agent never flips this record to accepted.

## Context

MiOS-MCP already carries most of a multi-agent plane:

- **The relay.** `mios-agent-relay` provides addressed mailboxes: seven `mios_agent_*` tools, idempotent `message_id`, `queued`/`received` receipts, and a presence lease (`[mcp.agents].lease_s`).
- **The translation layer.** `translate_frames` normalizes AGY, Claude, Responses/Codex and Chat Completions frames to `loop.v1` events and Responses items.
- **Spawning.** `mios_tmux_nested_workflow` runs a bounded task in any of the seven catalogued CLIs inside a tmux slot.

What it lacks is succession. `.agents/COORDINATION.md` says so: "Expiry does not appoint a new coordinator." The live relay showed the cost on 2026-10-09:

- five registrations, all offline;
- 358 queued messages that nobody received — broadcasts addressed to sessions that had already gone;
- 94 of those waiting for an `antigravity` identity that no running session holds.

Presence is not coordination, and an expired presence lease tells no one to step in.

The trigger the operator named — credit exhaustion — is abrupt. The harness stops mid-turn and cannot hand off. It is also observable, because Claude Code writes it into the session transcript as the last assistant entry. This shape was observed in this repository's own sessions on 2026-10-08 and 2026-10-09:

```json
{"type":"assistant","isApiErrorMessage":true,"apiError":"usage_limit_reached","error":"rate_limit",
 "apiErrorStatus":429,"quotaLimits":{"status":"rejected","rateLimitType":"five_hour","resetsAt":1791511200,
 "overageDisabledReason":"out_of_credits"},"message":{"content":[{"type":"text","text":"You've hit your session limit · resets 10pm"}]}}
```

The weekly limit uses the same fields. An expired login appears as `isApiErrorMessage` with "Failed to authenticate". A harness's own subagents share its account limits, so they stop with it.

## Decision

1. **Two leases, never conflated.**
   - The relay's **presence** lease stays as it is.
   - A new **coordinator** lease names the one agent that may take the holder-only actions. Today those are pushing the PR branch, committing and merging on the integration branch, committing on the bootstrap integration branch, and assigning lanes.
   - Holding the coordinator lease grants no operator authority. Merging to `main`, publishing `:latest` (operator ruling Q20) and booting VMs on the host stay operator-gated. Peer messages remain context, never authorization.
2. **The lease is an OpenAI-style object with Kubernetes Lease semantics.**
   - `object: "agent.lease"`, with LeaseSpec's fields in snake_case: `holder_identity`, `lease_duration_seconds`, `acquire_time`, `renew_time`, `lease_transitions`, `preferred_holder`.
   - MiOS adds an ordered `successors` list, `state` (`held` | `released`), `handoff_to`, and the holder-only `exclusive` scopes.
   - On acquire, the previous holder becomes the first successor. The preferred holder takes the lease back through a graceful release, never by seizure.
   - client-go's timing rule (`leaseDuration > renewDeadline > retryPeriod × 1.2`) holds for the values:
     - lease duration: 5400 s;
     - renew interval: at most 1800 s;
     - probe interval: 600 s.
3. **Status and probe objects.**
   - Each harness publishes `object: "agent.status"`: its state, note, usage reading, transcript path and activity directories.
   - A read-only probe emits `object: "agent.probe"` with a `verdict` and a `take_over` boolean. Only a listed successor acts on `take_over: true`.

   | verdict | evidence | take over |
   |---|---|---|
   | `released` | `lease.state = released` (to the prober, or to anyone) | yes |
   | `exhausted` | the holder's latest assistant entry is an API error (`usage_limit_reached`, `rate_limit`, 429, authentication), and it resets more than 15 minutes out or never | yes |
   | `exhausted_briefly` | as above, but it resets within 15 minutes | no; wait it out |
   | `silent` | no transcript, subagent or status activity, and no renewal, for longer than `lease_duration_seconds` | yes |
   | `alive` / `waiting` | otherwise | no |

   An error followed by a normal turn is not exhaustion. Only the latest assistant entry decides.

   The relay decides, never the caller:
   - **Identity is relay registration.** Acquire, renew, release and status publish carry `agent_id` and the token that identity registered with. The relay compares the token's digest with the registration and stores nothing else. Registration cleanup never retires an identity the lease names.
   - **The relay runs the probe on acquire** and refuses unless `take_over` is true. A verdict in the request is never read, and the holder comes only from the lease.
   - **Files stay in the relay's own state directory.** The lease, the status files and the journal resolve beneath it. An identity that names a file uses the relay alphabet, starts with a letter or digit, and never contains `..`.
   - **A fresh install has an unheld lease** derived from `[mcp.agents.succession]`. A listed successor or the preferred holder acquires it first, and only that acquire writes it.
4. **Harness adapters read; they never write.**
   - The Claude Code adapter reads the transcript fields above.
   - Every harness gets the release and silence signals.
   - A harness whose quota surfaces elsewhere (Antigravity's `agy --output-format json` returns `status` and `usage`) adds an adapter rather than a special case in the probe.
5. **Takeover and handback.** On takeover, the successor:
   1. acquires the lease;
   2. messages the previous holder through the relay;
   3. reads the holder-maintained brief;
   4. resumes the stopped lanes from their worktrees' git state, not from summaries.

   On handback, the successor parks at a safe point, updates the brief, and releases to the preferred holder.
6. **Spawning stays on tmux-mcp.**
   - **Local** subagents run through `mios_tmux_nested_workflow` or the slot tools, one worktree each, replying through addressed messages.
   - **Cloud** subagents launch from a tmux slot with the verified commands below. They work only on their own `cloud/<purpose>` branch, never on the PR branch or `main`.
   - No catalogue entry or default enables a blanket bypass: not `agy --dangerously-skip-permissions`, not `gemini --approval-mode yolo`. Scoped policies only, as `mcp-tmux.md` already requires.
7. **Phasing.**
   - **Phase 0 (landed 2026-10-09).** A reference implementation outside the repository, in the host's coordination directory: `probe.ps1`, `coord.py`, `lease.json`, the status files, an append-only `journal.jsonl` of `agent.event` records, and `HANDOFF.md`. It covers 7 probe fixtures on PowerShell 7 and 5.1, using the real usage-limit entry, plus 9 lease transitions and refusals. It sits outside the tree, so Law 14 is not waived; the native port replaces it.
   - **Phase 1.** `lease`, `status` and `probe` actions in `mios-agent-relay` (Rust); `mios_agent_lease`/`_status`/`_probe` MCP tools; every number in `[mcp.agents.succession]`; tests with a planted negative per verdict; parity with Phase 0 on the same fixtures.
   - **Phase 2.** The interop mapping below, in `translate_frames`, plus an AgentCard projection of each registration.
   - **Phase 3.** The cloud catalogue in `[agent_cli]`.

## Standards mapping (verified 2026-10-09)

| MiOS | A2A v1.0.1 | MCP 2026-07-28 (`io.modelcontextprotocol/tasks`) | OpenAI | Other |
|---|---|---|---|---|
| relay registration | AgentCard at `/.well-known/agent-card.json`: `name`, `description`, `version`, `supportedInterfaces[]`, `capabilities`, `defaultInputModes`/`defaultOutputModes`, `skills` | — | — | OTel `gen_ai.agent.id`, `gen_ai.agent.name` |
| addressed message (`message_id`) | Message: `messageId`, `role: ROLE_AGENT`, `parts[]` (`text` / `data`), `contextId`, `taskId` | — | Responses input items; Conversations `conv` | OTel `gen_ai.conversation.id` |
| assignment to a lane | Task: `id`, `contextId`, `status{state, timestamp}`, `artifacts`; the backlog item travels as `metadata` (`task_<n>`) | task: `taskId`, `status`, `createdAt`, `lastUpdatedAt`, `ttlMs`, `pollIntervalMs` | Responses `background: true`, `status` | — |
| receipt `queued` | `TASK_STATE_SUBMITTED` | (none; a task exists once the server creates it) | `queued` | — |
| receipt `received` | still `TASK_STATE_SUBMITTED`: a read is not work started | — | — | — |
| `agent.report` `state: working` | `TASK_STATE_WORKING` | `working` | `in_progress` | — |
| `agent.report` `blocked` (needs input) | `TASK_STATE_INPUT_REQUIRED` | `input_required` | `incomplete` | — |
| `agent.report` with gate evidence | `TASK_STATE_COMPLETED` + Artifact (`artifactId`, `parts[data]`) | `completed` | `completed` | — |
| failure or refusal | `TASK_STATE_FAILED` / `TASK_STATE_REJECTED` | `failed` | `failed` | — |
| cancel | `CancelTask` → `TASK_STATE_CANCELED` | `tasks/cancel` → `cancelled` | `POST /v1/responses/{id}/cancel` → `cancelled` | — |
| coordinator takeover | (A2A has no leader concept) | — | Agents SDK handoff: tool `transfer_to_<agent>`; `on_handoff` = lease acquire + journal; `input_filter` = the brief replaces raw history | Kubernetes `coordination.k8s.io/v1` Lease |
| `journal.jsonl` events | `statusUpdate` / `artifactUpdate` stream events | `notifications/tasks` | Agents SDK `handoff_span`, `agent_span` | OTel span `invoke_agent {gen_ai.agent.name}` (CLIENT for a remote agent), `gen_ai.operation.name` = `invoke_agent` / `invoke_workflow` |
| `translate_frames` dialects | stream events | — | Responses items; Open Responses (2026-04-24) | ACP `session/update` (`agent_message_chunk`, `tool_call`, `usage_update`) |
| repo instructions | — | — | — | `AGENTS.md` (AAIF) |

**Placement of each protocol:**
- A2A is agent ↔ agent over a network.
- MCP is agent ↔ tools.
- ACP (Agent Client Protocol: JSON-RPC 2.0 over stdio) is editor ↔ agent.
- The relay is the local, caller-owned mailbox, and the translation layer converts between them.

MiOS does not run an A2A server yet. The relay stays loopback and caller-owned (Law 11: tokens never in env or files others read).

The rule that matters most in this table: completion comes only from a report that carries gate evidence. A `received` receipt never maps to `completed` or `working`.

### Cloud subagent commands (verified 2026-10-09)

| Harness | Launch | Status | Result |
|---|---|---|---|
| Codex (cloud is experimental) | `codex cloud exec --env ENV_ID "query"` | `codex cloud list --json` | `codex apply TASK_ID` |
| Claude Code | `claude --cloud "task"` (`--remote` is a deprecated alias) | `claude -p "msg" --cloud <session> --output-format json` | `claude --teleport <id>` |
| Jules | `jules remote new --repo <owner/repo> --session "prompt"` | `jules remote list --session` | `jules remote pull --session <id>` |
| GitHub Copilot (preview) | `gh agent-task create "desc" -b <base> -R <repo>` | `gh agent-task view <id> --json state,pullRequestUrl` | the task's PR |
| Antigravity (local headless) | `agy -p "…" --output-format json` | JSON `status` field | `response` |
| Gemini CLI (local headless) | `gemini -p "…" -o json --approval-mode default` | exit code (0, 1, 42, 53) | `response` |

`agy` drops or hangs its output when stdout is not a TTY (antigravity-cli issues 76 and 318). A tmux slot provides the TTY, which is one more reason spawning stays on tmux-mcp.

## Rationale

- **A lease is the smallest correct primitive** for "one coordinator, a successor ready". It is the shape Kubernetes and client-go use for leader election, so the fields, timing rule and failure modes come from widely deployed practice instead of being invented.
- **Reading the transcript turns an abrupt failure into an explicit signal.** The holder cannot announce its own exhaustion, but the harness records it with a reset time. That lets the standby both wait out a short limit and take over a long one.
- **The OpenAI object shapes match ADR-0028's task store and the operator's ruling** that schemas are OpenAI formats. The mapping table lets the same records travel as A2A, MCP tasks or Responses without a second vocabulary.

## Consequences

- **Phase 0 is outside the repository and Windows-hosted.** The holder's transcript lives on the Windows side and the relay state in WSL. The native probe must read `/mnt/c` paths (or take the path from the status object) until both run in one place.
- **Only one holder at a time, by design.** Two capable agents both wanting the lease is resolved by `preferred_holder` and graceful release, not by races.
- **The stale `antigravity` mailbox stays.** It can only be drained by the token that owns it. A fresh session registers a new identity, and the old one retires through `mailbox_retention_s` once nothing references it.
- **Phase 1 changes the relay's public tool surface.** That regenerates `tools.generated.json` and `metadata.json`, and must keep cached-schema clients working, as the relay's existing compatibility test does.

## Sources

- **A2A:**
  - specification v1.0: https://a2a-protocol.org/latest/specification/
  - releases: https://github.com/a2aproject/A2A/releases
  - AAIF hosting: https://aaif.io/blog/a2a-joins-aaif
- **MCP 2026-07-28:** https://modelcontextprotocol.io/specification/2026-07-28/changelog; tasks extension: https://modelcontextprotocol.io/extensions/tasks/overview
- **OpenAI:**
  - Agents SDK handoffs: https://openai.github.io/openai-agents-python/handoffs/
  - tracing: https://openai.github.io/openai-agents-python/tracing/
  - Responses background mode: https://developers.openai.com/api/docs/guides/background
  - Open Responses: https://www.openresponses.org/specification
- **Kubernetes:**
  - Lease v1: https://kubernetes.io/docs/reference/kubernetes-api/cluster-resources/lease-v1/
  - client-go leader election: https://pkg.go.dev/k8s.io/client-go/tools/leaderelection
- **OpenTelemetry GenAI attributes:** https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ (Development stability; moved to `semantic-conventions-genai`)
- **AGENTS.md:** https://agents.md/
- **Agent Client Protocol:** https://agentclientprotocol.com/protocol/schema
- **Cloud CLIs:**
  - https://code.claude.com/docs/en/claude-code-on-the-web
  - https://jules.google/docs/cli/reference/
  - https://cli.github.com/manual/gh_agent-task_create
  - https://antigravity.google/docs/cli/headless
