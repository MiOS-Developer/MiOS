<!-- AI-hint: Native MiOS-MCP and tmux-mcp architecture, packaging, runtime theme projection and verification contract. -->
<!-- AI-related: /usr/share/mios/mios.toml [mcp], [packages.mcp], [keybindings], [theme]; /usr/libexec/mios/mios-mcp-server -->

# Native MiOS terminal and MCP

The native MiOS-MCP server publishes the existing MiOS verbs, promoted skills,
recipes and read-only resources, alongside `mios_tmux_*` terminal tools. Existing
dispatch and resource handlers remain intact. The same stdio server is registered
with the agent-pipe consumer in terminal-only mode, preventing recursive MiOS
catalog discovery. Local coordinating clients use the full combined endpoint.

`[packages.mcp]` is mandatory in the workstation, dev environment and shared
`mios-base` service image. Every MiOS-owned final service image inherits this
base. Upstream build stages and third-party OCI dependencies keep their original
bases. The SDK installs offline from the declared wheelhouse; architecture-specific
tmux-mcp archives and patched font assets are checked against SSOT SHA-256 values
before installation. A source change does not deploy already published images:
the native image pipeline must build and publish the new revision.

## Process boundaries and completion

MadAppGang's slot-based tools run behind the SDK's protocol negotiation and
framing. The adapter publishes the SSOT allowlist with bounded slots and timeouts.
It does not invent raw `split-pane` tools that this upstream does not expose.
Each stdio connection owns a private `0700` runtime directory and worker HOME.
Unbound automation uses its own `mcp-headless` socket. The native CLI launcher
binds an interactive head to its caller-owned SSOT `mios-human` socket and exact
pane; socket ownership, session identity and pane existence are checked before
any desktop mutation. Arbitrary ambient tmux sessions are not adopted. In this
mode helpers appear as live splits of the head's desktop window. Separate slot
namespaces and a window allocation lock allow nested heads to coexist. Teardown
uses witnessed owned pane IDs and preserves human panes and other heads' workers.
Personal shell startup hooks and ambient API keys are excluded from workers.

`mios ai` opens the native CLI chooser; `mios ai NAME` starts that catalog client
directly. The chooser displays an introduction, installation/MCP support and
navigation hints. Small panes page the client list with `n` and `p`; a number or
name selects a client, and `q` returns to the SSOT-themed shell. Desktop layout
has a head on the left and four empty worker reservations on the right when
the viewport is large enough. Default launches use the SSOT `[terminal]` size
(80×20): compact landscape places one active agent on the left and the live
agent view on the right; portrait stacks the monitor above the active agent. Other
workers keep running in a managed worker window. Ctrl+B then O cycles the active
agent, and Ctrl+B then F toggles compact/automatic layout. Ctrl+B then W selects
a pane from the tree; expand with arrows, then use Ctrl+B then Z to zoom. The
MCP bridge verifies each reserved worker and its workspace before directing
upstream slot tools to its current window. Resizing preserves pane IDs and PIDs.

The native layout follows `[mcp.tmux.workspace]` on creation and resize. Existing
pane processes survive rotation. A zoomed or operator-modified layout is left
alone. Clients attached to the same tmux window share its geometry; its latest
client resize drives layout selection. A failed creation removes only the panes
created by that attempt. CLI installation supplies neither provider login nor
model readiness; an installed client can still require either before doing work.

A worker launch verifies the selected local model with
`mios-ai-config --root / --probe-inference`. The check requires a completed
Responses stream with text from that exact model, within
`[mcp.tmux].timeout_s`; an advertised catalog, empty reply, truncated result or
substituted model is insufficient. `--probe` checks only the model catalog.
Local `--help` and `--version` commands skip inference and client projection.

Day-0 finalization applies system presets and global user presets so the
operator's launcher broker is enabled on the first user session. Incomplete AI
and model provisioning services return to the inactive state for their retry
timers. Model provisioning serializes writers, validates required weights before
publishing readiness, and preserves baked immutable seeds. The launcher condition
is projected from `[identity].username`.

HTTP clients must call `mios_tmux_session_open`, keep its opaque capability
private, and supply `session` on terminal calls. This is explicit because modern
stateless MCP HTTP requests share the application's lifespan. Capabilities expire
after the SSOT idle interval and are reclaimed on shutdown. Session count, busy
slots, cancellation, closed capabilities and command timeouts fail explicitly.
The loopback service is intended for trusted local clients. Its capability is
session separation, not a security boundary against another process with the same
UID. Arbitrary shell tools retain the caller's filesystem permissions.

Batch execution requires an upstream exit receipt. Nonzero status, missing
receipts and timeouts report MCP errors; timed-out/cancelled panes are retired.
Wait-channel inspection indicates interactive input readiness and cannot prove
that an agent's turn is complete. Generator/verifier lanes still need independent
worktrees, explicit completion receipts and the existing two-sided verification
ladder. Avoid installing blanket approval bypasses as system defaults.

Terminal screen capture is a display transcript, not a byte-exact artifact log.
Verification commands must write original output to files and compute hashes and
byte counts from those files. Keep positive and planted-negative logs separately;
revert mutations and audit the pre-mutation snapshot, including untracked files.
Resolve Git metadata with `git rev-parse --git-path` or `--git-dir`, and retain the
existing shared-ref locking/backoff adapter.

## Addressed agent sessions

`[mcp.agents]` configures the native `mios-agent-relay` transaction engine.
The combined endpoint adds seven `mios_agent_*` tools without removing the legacy
catalog. Each participating running CLI or chat registers a session ID and keeps
its returned lease private. Registered sessions share a caller-owned state
directory; nested tmux workers inherit only the directory pointer, not leases.

Use `mios_agent_list` for live session discovery, `mios_agent_send` for addressed
tasks or replies, `mios_agent_receive` to read the recipient's inbox, and
`mios_agent_ack` after reading. Reusing a message ID makes retries idempotent and
returns its current receipt. A queued message is not delivery, and a received
receipt is not completion. `mios_agent_unregister` marks a session offline.
Presence expires without transferring mailbox ownership. With
`[mcp.agents].queue_offline`, registered dormant sessions retain bounded queued
work and resume `receive` with their original private token. Explicitly closed
sessions reject new messages. Registration preserves both identities referenced
by pending messages; unrelated dormant identities retire after
`mailbox_retention_s`. Queue, message, registry and receipt limits derive from SSOT.

Presence never appoints a coordinator. The coordinator lease (`agent.lease`,
ADR-0029) is separate, and every number and name it uses comes from
`[mcp.agents.succession]`:

- **Identity is relay registration.** `mios_agent_lease` acquire, renew and
  release, and `mios_agent_status` publish, carry the session's `agent_id` and
  the token it registered with. Only the token's digest is stored, and
  registration cleanup never retires an identity the lease names.
- **The relay decides takeover.** `mios_agent_probe` returns `held_by_me`,
  `released`, `exhausted_briefly`, `exhausted`, `silent`, `waiting` or `alive`,
  with `take_over` and `next_probe_s`. On acquire the relay runs the same probe
  itself and refuses unless it says `take_over`; a caller's verdict is never read.
- **Files stay in the registry directory.** The lease, each
  `<agent_id>.status.json` and the `journal.jsonl` of `agent.event` records live
  beside `state.json`. An identity that names a file uses the relay alphabet,
  starts with a letter or digit, and never contains `..`.
- **A fresh install has an unheld lease** derived from SSOT. A listed successor
  or the preferred holder acquires it first; reading it writes nothing.

`mios_agent_observe` and `mios agents --observe` provide a sanitized,
read-only global inventory. Every registration is returned; `max_rows` controls
only the recent receipt window. The inventory includes caller-owned relay
registries witnessed on live MiOS-MCP processes, all witnessed tmux servers and
sessions (including hidden anchor, observer and headless sessions), live catalogued
CLI/MCP processes, and detected worker slots. Registered children may report
`parent_agent_id`, `session_name` and `node` with their own private registration
token; workers never inherit a parent's token. Unreported internal harness threads
cannot be enumerated by the relay. Detection alone never proves registration.

The default registry admits up to 256 retained identities. Its operational capacity
is declared in `[drift.bounded_capacities."mcp.agents.max_agents"]`; the gate permits
scaling within that explicit integer bound, rejects values above it and rejects
silent bound increases against the immutable baseline. Other ceilings retain their
existing ratchet policy, and the saved-state byte budget still applies.

Declared caller-owned MiOS-MCP federation members contribute their local snapshots.
The observer calls `scope="local"` on capable peers to prevent federation cycles;
older local-only peers accept the original empty argument object. Global observation
is the default. No extra endpoints are guessed and HTTP federation retains its
existing opt-in policy. The TUI polls remote sources in the background, identifies
pending/unavailable sources and marks cached presence unknown after a failed poll.
Source qualification keeps equal IDs on different relays distinct. Sources without
an agent observation tool are explicitly marked unsupported.

`mios agents --watch` and Monitor's Agents & AI view share a scrollable inventory
with All / Agents / Sessions / Subagents filters (F6), search by name, full ID,
source or parent (Ctrl+F), and full selected details (Enter; Esc closes details).
Arrow keys, Page Up/Down and Ctrl+Home/End reach every row without resetting the
cursor on refresh. The small native terminal fallback rotates through every group
and page. In an AI workspace, the SSOT global G action focuses the existing pane
without adding a tab. Compact labels do not limit the JSON inventory.

Registrations, sessions, subagents and queued/received receipts remain distinct;
message bodies, leases, command arguments and terminal contents are omitted.
Registry files remain private (`0600` in a `0700` caller-owned directory).

A local live CLI test used an Antigravity head in an MCP tmux pane to launch
another Antigravity CLI, send it a task, and receive its reply through these
mailboxes. The worker queried the seven installed SSOT CLIs and observed
`codex-cli 0.160.0` in a nested pane. Both task and reply acquired recipient
acknowledgments. This tests the local Linux CLI path; it does not certify every
harness, a published image generation, or an existing desktop conversation.
Headless clients need an explicit permission policy for the tools their task
requires. An exit-zero CLI result with `denied_actions` is a failed task, even
when its wrapper labels the result `SUCCESS`.

On 4 October 2026, a separate live test connected with Windows OpenSSH and a
PTY to the localhost Windows CMD shell, confirmed all seven globally installed
agent commands on the host PATH, and typed `mios`. It attached to native MiOS
tmux with the SSOT truecolor status bar and Oh My Posh prompt. An Antigravity
head launched a worker through the combined MCP tmux tools; that worker ran
`mios agents` and `mios agent codex --version` in another pane. The head then
sent the result to this running Codex chat, and Codex replied through MiOS-MCP.
The task, worker reply, head-to-Codex result and Codex-to-head reply all had
recipient acknowledgment receipts of `received`. The observed worker result
was `MIOS_NESTED_WORKER_OK; installed=7; codex=codex-cli 0.160.0`.

The initial worker permission denial required a scoped tool policy correction;
the head also needed a conversation resume after its reply wait expired. The
completed runs had no denied actions. The temporary SSH authorization and
permission policy were removed afterward. This proves the tested Windows SSH,
Linux CLI and current Codex chat path. It does not establish automatic delivery
to an unregistered desktop conversation or authenticated inference for every
installed provider CLI.

Both participants must actually consume the MCP tools. This mailbox does not
inject a user turn into an unrelated desktop chat, register an installed binary
as a running session, or convert an A2A model-service card into a CLI address.
Peer messages remain context inside the human-authorized task. They do not
grant new permission or override the receiving agent's instructions.

## Translation layer and nested workflows

The combined endpoint exposes the native translation layer alongside terminal
and agent relay surfaces:

1. `translate_frames` normalizes frames across dialects (AGY stream JSON, Claude
   print-mode JSON, OpenAI Responses/Codex items, and OpenAI-compatible Chat
   Completions) to ordered `loop.v1` events and Responses items. Source `auto`
   sniffs the frame shape. Credential fields are refused by name before processing;
   terminal `delivered` events require gate evidence and demote to `unverified`
   or `vacuous` otherwise.
2. `mios_tmux_nested_workflow` automates launching nested tasks across any of the
   seven installed global agent CLIs (`claude`, `codex`, `gemini`, `copilot`,
   `opencode`, `agy`, `aider`) inside bounded tmux slots (1..32). Native desktop
   heads create visible helpers; unbound and HTTP callers use private servers. It sets up
   private environments with the shared relay pointer, executes the task, strips
   terminal ANSI escapes, extracts receipts, and translates output frames.
3. Concurrent slots remain fully isolated with independent per-slot locks,
   enabling parallel multi-agent trees and swarms without dirty buffer contention.

## Theme projection at build and runtime

The vendor TOML, `/etc/mios` overrides and caller's `~/.config/mios` overrides
form the theme contract. Build-time generators emit shipped terminal, tmux,
compositor, prompt and keybinding files. Native `mios-terminal`, MCP pane startup
and interactive login startup re-render the prompt and tmux configuration into a
caller-owned runtime directory. `/usr` remains immutable during runtime.

Windows startup invokes the native WSL resolver through the installed dispatcher.
It atomically projects the palette, font and settings for Windows Terminal and
the Oh My Posh prompt from the same layered TOML. The launcher reads current
dimensions and profile names from that projection. The native Windows launcher
uses per-monitor DPI awareness, measures visible window bounds, fits oversized
windows to the current work area and centers the window containing its new MiOS
tab. The launch verification uses a separate window to preserve existing tabs.
Synthetic tests cover negative monitor origins, portrait layouts, large scaling
factors and oversized windows; physical display verification remains specific
to hardware tested.

Use `mios terminal`, `mios terminal --action ai`, `mios mcp` or, on Windows CMD,
`mios ssh [OpenSSH options] user@host`. Windows installation preserves existing
verb dispatch, unrelated MCP client configuration and editor settings, with
backups when owned files change. See [mobile shortcuts](guides/mobile-keybindings.md).

## MiOS-Tmux floats, hidden sessions and the apps catalogue

**Floating panes.** tmux 3.7 floating panes (`pane_floating_flag = 1`) are not
part of a tiled layout. Both the relay (`mios-agent-relay` workspace) and the
MCP server never run `select-layout` on a window that holds one: they log the
skip and keep the floats. Workspace setup counts tiled panes only.

In a managed workspace window, MiOS-Tmux never creates a native float. Ctrl+B,
then `*` opens the float shell instead, and `F`, `!`, `{` and `}` refuse. The
float shell is a hidden per-session tmux session
(`[keybindings.tmux.float].session_prefix` + the parent session id digits). It
is shown in a popup, carries `@mios-bare 1`, and is killed when its parent
session closes (a spawn-free `session-closed` hook).

**Hidden sessions.** The effective hidden set is
`[terminal.sessions].hidden_session_prefixes` plus the fixed worker-parking
prefix `mios-workspace-`. That prefix is part of the relay's storage contract,
not a tunable. These sessions are never offered by:

- `miosd sessions`;
- `choose-tree` (filtered by `@mios-visible-session`);
- session cycling (Ctrl+B, then `(` or `)`).

Workspace hooks call the native relay (`--workspace-hook ensure`), never an
interpreter. tmux itself skips bare and hidden sessions, and windows already laid
out for their size, without starting any process. The float-shell reaper
(`session-closed`) is pure tmux commands as well.

**Apps catalogue.** `miosd sessions --list` prints
`{"nodes": [...], "apps": [{id, label, section, mode}]}`. `apps` holds the
installed `[[terminal.tuis]]` rows for this terminal. It is read-only data for
the Portal, the controller app and agents. MCP has no tool that launches a TUI
into a human session; such a tool would need an owner lease first.

## Native TTY desktop

Open the desktop directly from a local terminal or SSH:

```sh
mios desktop
# Equivalent native entry:
miosd desktop
# Terminal launcher entry:
mios-terminal --desktop
```

`[keybindings.tmux.desktop]` selects the backend, dedicated socket/session,
dock, window titles, minimum sizes and resize debounce. The quiet background
draws the global top bar; the dock stays at the bottom. Its background redraws
after a settled size change and blocks while idle. Applications run in native
tmux panes without requiring a graphical compositor.

The Apps dock control and the top bar open the same searchable launcher as
**Alt+Space** or **Ctrl+B, Space**. Press **/** or **Ctrl+F** to find apps and
actions; typed spaces, `q` and number keys stay inside the query. **Enter** opens
the selected match, **Tab** opens its options, and **Esc** clears search or backs
out. The launcher preserves the original application target through filtering.
The `terminal-shell` catalogue entry opens a persistent terminal window.

Window titles show the application command with left-side **[x] [_] [+]**
controls for confirmed close, minimize and maximize. Drag a top border to move
a floating window; drag its edge or corner to resize it. Right-click a border
for centered window actions: center, maximize/restore, snap halves or quarters,
tile, float, minimize or close. Snap actions reject unusably small geometry.
Minimize parks the existing app in an owned hidden session; restore keeps its
pane identity, process and PTY. Managed agent panes and the background anchor
retain their ownership guards.

Windows, menus, dialogs and tooltips use the shared `[terminal.popups]` policy
and existing per-type size sources. Geometry is centered and clamped to the
current client. A systems chooser is intentionally full viewport. `miosd sessions` uses compact
Apps/Systems/All tabs and at most two footer rows so descriptions and status text leave room for choices on small terminals.

The desktop reuses `render-tmux-theme` and the SSOT `terminal-shell` argv for
its shell setup. `MIOS_OMP_THEME` points at the generated prompt, and the
human desktop clears the worker-only helper-prompt flag in its own scope.
The palette follows the terminal's capabilities and layered SSOT. Native
agent worker prompts keep their separate helper policy.

### Resize and scroll behavior

The game TUI drains redundant resize/mouse notifications within a bounded
batch and paints at its existing configured frame interval. Wheel actions
remain ordered; a key or click following a wheel burst first paints the current
menu page. Current-size click hit tests remain enforced. Scene quality, game
ticks and saves keep their existing rules.

Monitor collectors run outside the input path with one pending job per
collector. Keyed tables preserve the selected identity, top visible row and
horizontal position across refresh/reorder/removal. A refresh does not queue
a cursor-scroll action over a later wheel input. Logs follow new lines while
the reader is at the end and retain a manually scrolled position otherwise.

The native backend also bounds off-screen floating-pane occlusion ranges and
yields when identification IPC has no write progress. Border callbacks use
window-coordinate mouse formats, which account for top status rows and panned
viewports; ordinary pane-relative mouse formats retain their existing meaning.

### Backend build and verification

`mios-gen build-tmux-desktop` verifies the supplied source archive against
`[keybindings.tmux.desktop.source]`, applies the declared patch, builds in a
fresh private directory and stages the configured binary with a digest receipt.
Run it inside an admitted MiOS-DEV container under the shared native build lock;
the host wrapper supplies the inspected container identity. Source, patch,
toolchain and runtime-library provenance accompany the artifact. Replacing a
backend file leaves an existing server's loaded executable unchanged; deployment
therefore verifies the running server after a checkpointed, scoped reload.

The checkpoint in
`.devloop/handoff/mios-tui-desktop.json` distinguishes completed private/native
checks from final combined validation, publication and live recovery. Its
verification archive is assembled by the integration publisher. Raw IPC traces,
environment contents, authentication data and private relay tokens are excluded.

## Licensing and validation

MiOS's repository license is Apache-2.0. Upstream tmux-mcp's README declares MIT;
the release lacks a separate root license file. The vendored README and verbatim
Go dependency notices are retained under `/usr/share/licenses/tmux-mcp`.
Font licenses remain with the installed archive. Do not infer blanket copyleft
obligations merely from IPC or claim legal approval from an architecture diagram.

`test_mios_mcp_aio.py` exercises real upstream tmux processes, stdio/HTTP protocol
eras, persistent private HOME state, concurrent slots, exit receipts, cancellation,
timeouts, policy rejection, resource/dispatch parity and corrupt-archive rejection.
The `--negative-receipt` mode must exit 23 with `DEVLOOP-PLANTED-EXIT`.
Rust keybinding tests plant duplicates and committed-file drift; theme gates
reject invalid projections and compare shipped files with SSOT output.

Sources: [tmux-mcp upstream](https://github.com/MadAppGang/tmux-mcp),
[tmux manual](https://man7.org/linux/man-pages/man1/tmux.1.html),
[Windows Terminal CLI](https://learn.microsoft.com/en-us/windows/terminal/command-line-arguments),
[per-monitor DPI](https://learn.microsoft.com/en-us/windows/win32/hidpi/dpi-awareness-context).


## Controller session assignments

The user service `mios-inputd.service` is preset for `default.target`, so
controller navigation starts on graphical and text logins. Local VT detection
uses the kernel controlling-tty identity; `/dev/console` and `/dev/tty`
aliases work only when they resolve to that same physical VT. SSH PTYs are not
treated as local consoles.

Fullscreen keeps navigation by default
(`keybindings.global.controller.capture_fullscreen = false`). North/Triangle/Y
captures the focused window on press. Holding it upgrades that capture to
fullscreen; releasing the button does not submit a second capture. A held
L3+R3 releases capture. The capture transaction reads current focus, and its
receipt pins the hold upgrade to the selected window even when a cursor click
precedes the context poll.

For multiple controllers and local or SSH tmux sessions, edit
`keybindings.global.controller.sessions` in the configurator or the layered
TOML. Each assignment names explicit controller paths, a socket, the exact
session name and an initial stable pane ID:

```toml
[[keybindings.global.controller.sessions]]
name = "player-two"
controllers = ["/dev/input/by-id/pad-event-joystick"]
socket = "/tmp/tmux-1000/default"
session = "work"
pane = "%4"
# ssh_host = "workstation" # omit for a local target
```

Replace the example paths and identities with the target's actual values.
Use stable by-id paths for reconnects. `mios-inputd map` shows the validated
assignments. SSH uses the operator's existing host configuration and
noninteractive authentication; inputd does not enroll hosts or acquire
credentials.

Assigned controllers emit navigation only to their target. They never drive
the desktop's keyboard, pointer, capture actions or raw gamepad clone. Every
controller has an independent focus cursor and bounded command queue. Target
session identity is checked before dispatch; server or window changes require
rebinding rather than falling back to another target. Expired input is dropped,
and unplugging, unassigning, shutdown or losing the active seat cancels queued
work. Ambiguous aliases reserve only the affected controller without sending
its input elsewhere.

Detached panes support terminal keys, pane focus and copy-mode paging.
Client-specific operations such as menus, prefix-table keys, popups and client
session switching require an attached-client transport and currently return an
explicit error for these pane assignments. SSH routing transports tmux
navigation; raw gamepad forwarding to remote graphical applications uses the
existing device-forwarding or nested-session path.


Captured terminals retain navigation through
`keybindings.global.controller.capture_navigation_contexts = ["terminal"]`.
The capture receipt identifies the selected window process and its actual tmux
client and pane. Pending input is cancelled on release, focus loss, target loss
or seat changes. Existing tagged captures recover through a fresh read-only
target query after a daemon restart. Menus and session switching use the
verified attached client; detached assignments continue to use explicit panes.

## Global dock input and controller pointer

A clean double Guide opens the local dock; the interval and action are
`controller.guide_double_press_ms` and `guide_double_press_action`. Single
Guide actions wait for that interval, while holds and Guide chords cancel the
pending tap. The dock action remains available on Super+Space and
Ctrl+Super+Space through `keybindings.global.always_active_actions`.

The shell publishes a short, atomic input lease in its private runtime
directory. While the dock or search surface has focus, controller arrows,
Enter, Escape, Tab and context-menu keys go to that surface. Raw gamepad input,
window capture and pending terminal delivery behind the dock are suppressed.
Closing the shell, an expired lease, or a failed compositor query detaches
shell input. Text consoles and explicitly assigned headless controllers retain
their own navigation.

The expanded dock takes keyboard focus even when its on-screen keyboard is
enabled. App, window, portability and PiP controls share that same input
surface. A click-through border animation on the captured local output shows
when it owns input; tune `desktop.dock.capture.border_glow` in the configurator.

In 2D cursor mode, the native compositor hook uses
`controller.cursor_shape` for motion from `controller.virtual_pointer`.
Physical mouse motion restores its cursor immediately. Mode changes, shutdown
and the bounded lease release the override. The existing 3D aim renderer is
unchanged.
