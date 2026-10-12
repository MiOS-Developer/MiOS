<!-- AI-hint: Implemented MiOS session messaging and visible tmux worker coordination contract. -->
<!-- AI-related: usr/share/mios/mios.toml [mcp.agents], [mcp.tmux], [keybindings]; usr/share/doc/mios/mcp-tmux.md -->
# MiOS multi-agent coordination

Any installed CLI with a working MCP client, reachable model and the required
tool permissions can act as a head or worker. The eight roles in
`.agents/agents/` are MiOS project conventions. Installing a CLI or assigning a
role does not make it a running relay participant.

## Native entry and visibility

Run `mios` to enter the human tmux session, then `mios agent NAME` to launch a
catalogued CLI. Native startup resolves layered `mios.toml` for the model,
`MIOS_AI_ENDPOINT`, theme and keyboard map. Use the projected configuration;
do not replace it with endpoint or socket literals.

The head's combined MiOS-MCP connection verifies its caller-owned human socket,
session and pane before binding tmux-mcp. Helper tools create live splits beside
that head. Nested heads have independent local slot namespaces. Unbound stdio
clients and HTTP terminal capabilities use private headless sessions. Closing a
connection reclaims only its witnessed helper panes.

Press **Ctrl+B, then G** for the existing MiOS Agents pane in an AI workspace,
without adding a tab. `mios agents --watch` shows
registrations, queued/received receipts and detected tmux panes.
`mios agents --observe` and `mios_agent_observe` return the sanitized snapshot.
Observation does not consume inboxes or acknowledge messages.

## Addressed messages

Each participating running head and worker calls `mios_agent_register`:

```json
{
  "agent_id": "opencode:unique-running-session",
  "kind": "opencode",
  "label": "MiOS verification worker"
}
```

Keep the returned lease token private. The registry location derives from
`[mcp.agents].state_directory` beneath the caller's state home, or from the
explicit `MIOS_AGENT_RELAY_STATE` pointer. Workers inherit the pointer, never
another participant's token. Files remain private to their owning user.

1. Discover sessions with `mios_agent_list`.
2. Send with `mios_agent_send`, supplying the sender's `agent_id`, private
   `token`, recipient `to`, task `message` and a stable `message_id`.
3. The addressed recipient calls `mios_agent_receive` with its own ID and token,
   reads the task and calls `mios_agent_ack` for that message ID.
4. The worker performs the authorized task and sends a reply to the head.
5. The head reads and acknowledges the reply before reporting its contents.

`queued` means accepted into the mailbox; `received` means the recipient
acknowledged reading. Neither certifies task completion. Receive calls refresh
the participant's presence lease. Dormant registered sessions keep queued mail
when `[mcp.agents].queue_offline` is enabled and resume with their original token.
Pending messages protect both endpoint identities from registration cleanup;
unreferenced dormant identities retire after `mailbox_retention_s`. Explicitly
closed sessions reject new messages. The relay distinguishes two leases that are never conflated:
- **Presence leases (`[mcp.agents].lease_s`)**: Ephemeral, private to each session, and refreshed by `receive` calls. Expiry marks the session offline without transferring mailbox ownership, appointing a new coordinator, or transferring a desktop chat. A paused harness must resume and consume its inbox with its original token; the relay does not inject user turns into unrelated applications.
- **Coordinator lease (`agent.lease`, ADR-0029)**: Governs exclusive coordinator operations (pushing the PR branch, committing/merging on the integration branch, assigning lanes) using Kubernetes Lease semantics (`holder_identity`, `lease_duration_seconds`, `acquire_time`, `renew_time`, `lease_transitions`, `preferred_holder`, `successors`). Every acquire, renew, release and status publish proves the caller's relay registration (`agent_id` plus the token it registered with). On acquire the relay runs `mios_agent_probe` itself and refuses unless its verdict (`released`, `exhausted` or `silent`) grants `take_over`; a caller-supplied verdict is never read. Succession never follows automatic presence expiration or uncoordinated seizure.

## Worker execution

Use the combined server's `mios_tmux_open_pane`, `mios_tmux_start_and_watch`,
`mios_tmux_execute_command` and capture/state tools for persistent helpers.
`mios_tmux_nested_workflow` runs a bounded CLI task and returns a process receipt:

```json
{
  "agent": "opencode",
  "task": "Review the assigned isolated worktree and report findings",
  "timeoutSeconds": 300,
  "slot": 1
}
```

The workflow wrapper does not register the child, send a relay task or certify a
reply. Prompt both participants to use the addressed-message protocol above.
Treat permission denial, nonzero exits and timeouts as failures. Preserve raw
verification logs separately from terminal captures.

## Project roles and worktrees

| MiOS role | Responsibility |
| --- | --- |
| orchestrator | Assign disjoint lanes and reconcile results |
| worker | Implement an assigned task in its isolated worktree |
| auditor | Run standing gates and report evidence |
| reviewer | Review changes and identify actionable defects |
| challenger | Exercise negative controls and failure paths |
| explorer | Investigate code and upstream requirements |
| publisher | Project SSOT and prepare verified release artifacts |
| developer | Maintain the system contracts within its assigned scope |

Every implementation lane owns a separate Git worktree and explicit files.
Name new worktrees, branches and verification artifacts by function with a
sanitized `mios-<purpose>` stem (`a-z0-9-`), following `[variants.naming]`.
Harness identities and timestamps belong in coordination records, not names.
Check collisions before creation and preserve all contributions when renaming
an owned lane; update its executable references and retain historical receipts.
Resolve Git metadata through Git commands, preserve the root workspace, and
apply the existing positive/negative verification ladder. Peer messages remain
context within operator-authorized work; they grant no additional authority.

See [the native MCP contract](../usr/share/doc/mios/mcp-tmux.md) and
[desktop and terminal interaction](../docs/design/doc-desktop-terminal-interaction.md).
