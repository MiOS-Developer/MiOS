<!-- AI-hint: Blender Lab MCP on MiOS: the per-user installation receipt, how a caller-owned [mcp.servers.blender] reaches agents through the caller's MiOS MCP front door ([mcp.federation]), and how to verify it end to end. -->
<!-- AI-related: usr/lib/mios/mios_mcp_federation.py, usr/libexec/mios/mios-mcp-server, usr/share/mios/mios.toml [mcp.federation], tests/test-mcp-federation.py, tests/test-blender-mcp-real.py, tests/test-blender-mcp-live.py -->

# Blender MCP

Blender Lab's MCP server lets an agent inspect and drive a running Blender
scene. On this workstation it is installed per user, not in the image. MiOS
makes its tools available to that user's agents through the MiOS MCP front
door (`mios-control`).

## What is installed (per user)

| Part | Location |
|---|---|
| Blender 5.2.3 LTS (official archive, checksum verified) | `~/.local/share/mios/blender/blender-5.2.3-linux-x64/blender`, linked as `~/.local/bin/blender` |
| Desktop entry (overrides the Flathub build's ID) | `~/.local/share/applications/org.blender.Blender.desktop` |
| Blender Lab MCP server 1.0.3 (`blmcp`, stdio) | `~/.local/share/mios/blender-mcp/release` with its venv `~/.local/share/mios/blender-mcp/.venv` |
| Blender add-on (`bl_ext.user_default.mcp`), loopback bridge | `~/.config/blender/5.2/extensions/user_default/mcp` |
| Declaration | `[mcp.servers.blender]` in `~/.config/mios/mios.toml` (stdio, `namespace = "blender_"`, env `BLENDER_MCP_HOST`, `BLENDER_MCP_PORT`, `BLENDER_PATH`) |

The installation receipt is `/var/tmp/mios-blender-setup/REPORT.md`. The
same directory holds `official-install.json`, `preferences.json`,
`mios-blender-config.toml` (the exact stanza) and the step `*.exit`/`*.log`
files. `/var/tmp` is not permanent, so copy the receipt elsewhere if you need
to keep it. The Flathub build `org.blender.Blender` remains installed as a
fallback.

The add-on starts its bridge automatically when the Blender window opens. It
listens on `127.0.0.1:9876` only, and needs Blender's *Online Access*
preference, even on loopback. Scene tools need a running Blender. The
documentation and `*_for_cli` tools do not.

## How agents reach it: caller-owned federation

The shared agent-pipe service runs as another user with `ProtectHome=yes`.
It cannot read, or run, anything declared in your home directory. So a
server declared only in your user tier was invisible to the MiOS MCP stack:
`/v1/mcp/tools` listed no Blender tools, and neither did `mios-control`.

`[mcp.federation]` (vendor SSOT, configurator section *Your own MCP
servers*) fixes this in the right place. Your own MiOS MCP front door runs as
you, so it:

* starts each `[mcp.servers.<id>]` declared **only** in your tier
  (`~/.config/mios/mios.toml` and its `mios.d/`). An id that a system tier
  declares belongs to the agent-pipe and is never started here;
* runs it over stdio with the MCP SDK's minimal environment plus the
  declared `env` only. Front-door endpoints, relay pointers and tokens are
  never inherited. Its stderr goes to
  `$XDG_STATE_HOME/mios/mcp-federation/<id>.log` (0600);
* lists its tools as `<namespace><tool>` (here `blender_*`, 26 tools) and
  forwards calls. A native MiOS tool name always wins a collision;
* is never offered over the Streamable HTTP front door unless
  `serve_http = true`, and never from the agent-pipe's `--tmux-only` child.

Tunables: `enabled`, `serve_http`, `startup_timeout_s`, `call_timeout_s`,
`restart_backoff_s`, `max_restarts`, `catalog_cache`, `idle_stop_s`,
`max_tools_per_server`, `log_directory`.

## Spawn budget

Every agent session opens its own stdio front door. If each one started
`blmcp` (a Python process, about 68 MB idle) as soon as it opened, a burst of
sessions would become a burst of Python children. The front door bounds this
instead:

* **Cached catalog** (`catalog_cache = true`). The first start writes the
  tool list to `$XDG_STATE_HOME/mios/mcp-federation/<id>.tools.json` (0600,
  written atomically). The cache is keyed by a SHA-256 of the declaration
  (command and its size/mtime, args, env, cwd, namespace, allowed_tools).
  Only the digest is stored, never the env values. A new session lists the
  `blender_*` tools from the cache and starts nothing. The server starts on
  the first `blender_*` call. A changed declaration invalidates the cache.
  The cache is rewritten each time the server starts, so after an
  in-place `blmcp` upgrade the next started session lists the new tools.
* **Idle stop** (`idle_stop_s = 600`). A started server that has had no call
  for that long stops cleanly. The next call starts it again. A call still
  in flight keeps the server alive. `0` keeps it alive for the whole session.
* **Bounded restarts**. A failed start, a crash or a failed call counts as
  one failure. A successful call resets the count. The next start waits
  `restart_backoff_s`, doubling with each consecutive failure up to one hour.
  After `max_restarts` failures in a row the server stays down
  (`gave-up`) until the front door restarts.

Nothing in this path is a timer, watcher or loop of its own. Only a tool
list or a tool call can start a server, and the idle wait is one bounded
wait inside the member that already exists.

## Verify

```bash
# Receipt: which caller servers federate, their status and exported names
/usr/lib/mios/mcp/.venv/bin/python3 /usr/libexec/mios/mios-mcp-server --federation-status

# Unit + protocol tests (fixture stdio server, front door, negative controls)
python3 tests/test-mcp-federation.py
# Real binaries: headless Blender, background bridge on a private port, round trips
MIOS_TEST_LIVE=1 python3 tests/test-blender-mcp-real.py
# Live: Blender GUI inside an owned nested PiP session, MCP round trips
MIOS_LIVE_NESTED=1 python3 tests/test-blender-mcp-live.py
```

The real-binary and live tests write a private caller tier for a free
loopback port. They never touch your saved Blender preferences or port 9876.
Each has negative controls: with no bridge listening the tool returns
*Cannot connect to Blender*, and with federation disabled no `blender_*`
tool is listed.
The real-binary test also checks the spawn budget with the real `blmcp`: a
second session that only lists tools exports the same 26 cached tools, and
the server's log is not reopened, so nothing was started.

## Notes

* An MCP client that is already connected keeps its old front door. Restart
  the client, or its `mios-control` server, to see the `blender_*` tools.
* The `blmcp` server pins MCP SDK 1.30 in its own venv. The front door
  (SDK 2.x) talks to it over the negotiated protocol, so the two SDKs never
  share a process.
* Updating Blender or the MCP release means changing `BLENDER_PATH` or the
  venv in your own tier. No MiOS change is needed.
