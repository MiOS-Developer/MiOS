---
name: opencode-delegation
description: |
  How heavy code work gets handled in the MiOS swarm: opencode is a
  first-class OpenAI /v1 COUNCIL PEER (not a sub-process you spawn).
  The agent-pipe orchestrator routes multi-file code work, file-system
  navigation, and code-aware PC-control loops to the opencode peer
  automatically. This skill is the routing intuition for recognizing
  opencode-appropriate work.
metadata:
  hermes:
    requires_tools: []
---
<!-- AI-hint: Defines the routing logic for the agent-pipe orchestrator to delegate multi-file refactors, filesystem-heavy loops, and code-aware PC-control tasks to the opencode peer via the gateway on the `opencode_gateway` port.
     AI-related: /usr/lib/mios/agents/opencode/bin/opencode, mios-opencode-gateway, mios-opencode, mios-find, mios-opencode-gateway.service -->

# opencode — the coding specialist /v1 council peer

> _MiOS-managed: describes opencode as a first-class /v1 council peer.
> Delete the marker to take ownership._

opencode (the SST/charm coding agent binary at
`/usr/lib/mios/agents/opencode/bin/opencode`) runs behind an OpenAI
`/v1` gateway (`mios-opencode-gateway.service`, loopback,
SSOT slot `[ports].opencode_gateway`). It is registered in the swarm
as `[agents.opencode]` — a **peer agent on the same OpenAI contract
as you (Hermes, port key `hermes`)**, dispatched by the agent-pipe orchestrator
(port key `agent_pipe`), NOT a sub-process spawned over ACP/stdio.

This is the current model. The retired model spawned opencode via
Hermes's native `delegate_subtask_to_peer_agent(acp_command="opencode")` —
that path is gone. You no longer spawn opencode; the orchestrator dispatches
code-heavy facets to the opencode peer in parallel with you, and
folds its answer into the council synthesis. Same Ollama backend
(`mios-opencode:latest`), so no extra VRAM contention.

## What routes to the opencode peer

The orchestrator's swarm decomposition sends these facets to opencode
because its native discipline beats a generic sibling:

* **Multi-file refactors / renames / moves** — opencode's `edit` +
  `patch` tools do string-replace + unified-diff with snapshot
  rollback.
* **"Read N files, edit M files" loops** — its `read` + `glob` +
  `grep` + `edit` chain is purpose-built; it caches reads and
  presents diffs before applying.
* **Code-aware PC-control workflows** — "look at this script + edit
  the config + retry": opencode's session model + snapshot history
  outperform ad-hoc terminal calls.
* **Long-running coding work** with rollback guarantees — its
  `Snapshot.Service` captures filesystem state per session.

## Your part as a council peer

You and opencode run as siblings in the same council. When a turn is
code-heavy, the orchestrator engages the opencode peer alongside you —
you don't call it, you collaborate. Concretely:

* **Don't grind heavy multi-file code work yourself** with qwen
  children when the opencode peer is engaged — it has the better
  tools for it. Focus your effort on orchestration, system actions,
  comms, and synthesis.
* **You still own** single-shot file reads/edits via your own file
  verbs (`read_file` / `replace_file_text` / `insert_file_lines`),
  one-line shell via `powershell_run` / `run_sandboxed_code`, and the
  MiOS surface (`find_file_fast`, window launch, verbs/skills/recipes) —
  opencode doesn't know about those; they are YOUR direct tools.
* **Parallel non-code fan-out** still uses your native
  `delegate_subtask_to_peer_agent(tasks=[...])` to lfm2:700m children —
  that mechanism is unchanged and separate from the opencode peer.

## Operator's mental model

Operator sees one MiOS answer. Under the hood, for code work the
opencode /v1 peer did the read/edit/patch + snapshot discipline in
parallel and the council synthesised the result. Per the operator's
integration direction: "Hermes is the lifestyle / orchestration
agent, opencode is the specialist coding agent" — now wired as
co-equal /v1 council peers rather than a parent/child ACP spawn.
