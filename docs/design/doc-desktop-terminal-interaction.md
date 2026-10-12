<!-- AI-hint: Implemented MiOS desktop, terminal and agent interaction boundaries, derived from SSOT. -->
<!-- AI-related: usr/share/mios/mios.toml [keybindings], [theme]; usr/libexec/mios/mios-terminal; .agents/COORDINATION.md -->
# MiOS desktop and terminal interaction

The layered `mios.toml` defines the installed keyboard map and theme. Build-time
projection ships native defaults; terminal startup projects current vendor,
host and user settings into caller-owned runtime files. The repository README
publishes the current [global keyboard map](../../README.md#global-mios-keybindings).

## Implemented global actions

| Action | Desktop | tmux and mobile SSH | Editor outside its terminal |
| --- | --- | --- | --- |
| Terminal | Ctrl+Alt+Shift+T | Ctrl+B, then T | Ctrl+B, then T |
| MiOS AI | Ctrl+Alt+Shift+A | Ctrl+B, then A | Ctrl+B, then A |
| Agent activity | Ctrl+Alt+Shift+G | Ctrl+B, then G | Ctrl+B, then G |
| System monitor | Ctrl+Alt+Shift+M | Ctrl+B, then M | Ctrl+B, then M |

Ctrl+B is a tmux prefix. Press and release it before the next key. H/J/K/L
select panes, S/V split vertically/horizontally, N/P switch windows, W opens
the window tree, Z zooms, Y enters copy mode and D detaches. Ctrl+B then Tab
sends Shift+Tab to the application. Ctrl+B then B sends the prefix through.
These actions derive from `[keybindings]`; do not add a second competing map.

Editor chords apply outside the integrated terminal. In the terminal, Ctrl+B
passes to tmux. Desktop actions use a separate modifier set. Generation rejects
duplicate MiOS action keys; third-party and operator-installed shortcuts need
their own conflict checks.

## Human-observable agent work

Run `mios`, then `mios agent NAME`. The combined MiOS-MCP connection of a native
head verifies its desktop session and creates tmux-mcp helpers beside that head.
Unbound automation retains private headless servers. The **MiOS Agents** window
shows relay participation, queued/received receipts and pane identities without
exposing leases, message bodies or terminal contents.

The CLI must consume its MCP tools. The workflow wrapper's exit receipt does not
prove messaging, provider authentication or task completion. Follow the
[session messaging protocol](../../.agents/COORDINATION.md) to register each
participant, send addressed tasks, read/acknowledge them and exchange replies.

## Application input boundaries

The active shell or CLI owns editing, cancellation and slash commands. MiOS
does not rewrite every harness into a common composer. In a normal POSIX shell,
Ctrl+C interrupts the foreground process group, Ctrl+Z suspends it, and Ctrl+D
on empty input signals EOF. Ctrl+S may pause output through XOFF; it is not
a MiOS stash action. Ctrl+Q resumes that output when flow control is enabled.

Use the selected harness's help for planning, model selection, permissions,
conversation resume and rewind. Double Ctrl+C, double Escape, global worker
termination chords, universal sigils and conversation transfer are not global
MiOS guarantees. They require separate implementation and verification before
being added to the installed SSOT map.

## Future interaction design

A shared composer, automatic coordinator takeover and unified conversation
transfer are possible future work. They are not part of the current relay or
the installed keybinding projection. New actions must first define scope,
permissions, cancellation and positive/negative controls, then update SSOT and
its generators together.
