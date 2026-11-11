# MiOS-Tmux guarded client focus successor

This is source for a successor to the sealed R7 backend. It is not an installed
capability or a native verification receipt. Root owns the native MiOS-DEV build,
integration, acceptance, and publication. No live sessions were changed here.

## Provenance

- MiOS lane starts at tree `bb27170cd5e2869182eb5eab6a58283edd78501f`.
- Upstream tmux 3.7c commit: `54a9ae7907eb9c686ef13acf02c46bd3ac0bdb72`.
- Preserved R7 full patch SHA-256:
  `65de930f524a0d68d62fbc93a25b47d74ce359cf899f2faff7f09ad9688b97aa`.
- Sealed R7 backend SHA-256:
  `105e827a936a7799a4318eca3e62cbda6bd9debe5f79bdef623f490c00914cbe`.
- Complete successor `tools/patches/tmux-desktop.patch` SHA-256:
  `776b3ba7d335ccb523ca1dcf225fee38abf5245c0229030e4532aea6f3a88e3c`.
- Incremental `tools/patches/tmux-client-focus-successor-r7.patch` SHA-256:
  `8245e3adf5098ed91524f31a2db895edbe1efc645e9a91c30366a29573ce0689`.

The complete patch retains every R7 change. The incremental patch is for review
and provenance; do not apply it after the complete successor patch. Both were
applied to their respective baselines in temporary Git indexes and yielded tree
`5c8104aabfc1e268175b802feb1485202dc66026`. Whitespace checks and the Python
fixture's AST parse passed. Neither C compilation nor the native fixture has
been run by this lane. Root's R7 patch and sealed receipts remain untouched.

## Focus metadata and atomic zoom

`format_defaults()` selects the window's active pane when no pane is supplied.
Consequently, `list-clients -F '#{pane_id}'` does not describe a client's
`active-pane` override, whereas input uses `server_client_get_pane(c)`.

Dedicated formats now use that actual input lookup:

| Format | Value |
| --- | --- |
| `client_pane_id` | Actual input pane, `%N` |
| `client_pane_pid` | Its native pane PID |
| `client_window_id` | Its current window, `@N` |
| `client_window_zoomed_flag` | Standard shared window zoom flag, `0` or `1` |
| `client_window_action_version` | `1` when this protocol exists |

The four focus fields are empty for unavailable or exited focus. Version 1 with
empty focus fields must fail closed; it must not trigger legacy fallback.
Existing `pane_id` and other global format meanings are unchanged. Consumers
must update every client discovery path, including leaf-client lookup.

Invoke the exact argv directly, without `run-shell`:

```text
client-window-action -t /dev/pts/N -C CLIENT_PID -I CLIENT_INSTANCE \
  -s '$SESSION_ID' -w '@WINDOW_ID' -p '%PANE_ID' -P PANE_PID zoom
```

The command requires every guard. It accepts only a same-UID invoking peer and
an exact attached, writable, nonsuspended terminal client. It compares native
client PID and instance, session ID, current window ID, actual input pane ID and
pane PID synchronously in the server event loop. Hidden or exited panes and
unknown actions fail without layout mutation. Target-client prefix matching or
implicit default-client selection cannot pass the exact name check.

Success prints exactly `ok\n`. Errors are ordinary nonzero tmux errors. A
one-pane already-full window may succeed as an idempotent no-op, matching native
zoom behavior. The command reuses `window_zoom()` and `window_unzoom()` for
tiled and floating panes, retaining their process, PTY, and saved geometry.
Zoom is standard shared-window state. Independent client focus overrides stay
their own; a client focused on a pane hidden by another client's zoom is refused.

The Rust caller still owns socket/server process identity, process start time,
login generation, and input-user assignment checks. This adapter does not add
app-specific PTY key delivery. A foreground-process check followed by a PTY write
cannot prevent an app-exit race from delivering that key into a shell.

## Explicit menu choice with mouse input

An explicit `display-menu -C` now enables mouse input for a keyboard- or
controller-opened menu while retaining that exact initial selection, including
`-C -` for no selection. No unitgen menu alias or destructive default changed.
Menus without explicit `-C` retain the existing mouse-enablement rule.

Mouse events update their own interior row before activation. A direct click
without a preceding motion event therefore cannot choose a stale default.
Outer borders and outside clicks are excluded. Disabled items and separators
retain their existing no-action behavior, including `-O` stay-open semantics.
Enter on explicit No/Cancel and Escape remain safe. This change introduces no
layout, wheel, navigation, or app-menu redesign.

## Native acceptance queued for Root

Build through the normal locked MiOS-DEV workflow from the complete patch.
Pin the newly built backend's SHA-256 and run, in a native worker slot:

```sh
python3 tests/test-tmux-client-focus.py \
  --binary /absolute/path/to/new/mios-tmux --sha256 NEW_BACKEND_SHA256
```

The fixture uses one private short-path server, two real PTY clients with
independent active-pane overrides, a third disposable readonly client, owned
sleep processes, and private menu markers. It never opens an operator socket,
controller, login service, or application save. Cleanup is bounded and guarded
by process start identity and socket inode/UID/type; unexpected evidence remains.

Required results:

1. Client-specific focus differs from legacy global `pane_id` and resolves to
   the pane that receives real client keyboard bindings.
2. Tiled and floating zoom restore exact pane geometry, PIDs, PTYs, and process
   identities. The second client's override remains unchanged.
3. Changed focus, every mismatched or missing identity guard, unsupported action,
   readonly client, and another client's hidden pane fail without layout changes.
4. Explicit Cancel/No stays selected even when it is not row zero; Enter chooses
   it. `-C -` Enter and Escape perform no action. Legacy keyboard Enter is kept.
5. Direct Yes clicks without prior motion work with ordinary and `-O` menus.
   Separator, disabled, border, and outside clicks never execute the old default.

Also exercise actual detach/reattach, client-instance replacement, exited panes,
and session/window switches in the native integration acceptance run. Repeat
the existing R7 pointer security, lease, and layout regression suite on the
successor. Prove old sealed R7 lacks version 1 and refuses the new command, so
the adapter never reports unsupported live zoom as success. Native results,
binary hash, service changes, and live-controller trials must be recorded by
Root separately from these source checks.

## Native fixture cancellation correction

The first R8 native build succeeded with all 19 modified source hashes matching;
the resulting 1,142,072-byte artifact is SHA-256
`95b479aafe4c4d454cbe029fa22677a9e7043de28def816ea4ff2d7470af900e`.
Its initial acceptance run failed all seven cases in setup before reaching any
functional assertion. The setup's standalone Escape and following raw F9 arrived
about 1.3 ms apart and decoded as M-F9.

A separate actual PTY diagnostic proved clean F9 and Ctrl-G followed by F9 select
the split pane, while immediate Escape followed by F9 selects neither: the key
marker is M-F9 and ordinary PTY text still echoes only in the base pane. Waiting
until Escape had decoded restored correct F9 selection. The diagnostic retained
the original failed proof, source, and binary unchanged.

The fixture now uses raw Ctrl-G for setup cancellation. `tty-keys.c` maps that
C0 byte to `g|KEYC_CTRL`, and `menu_key_cb()` accepts that key as cancellation.
An additional actual menu Ctrl-G cancellation assertion accompanies the change.
Every prior semantic assertion, raw F9 selection event, and exact `ok\n` check
remains. The corrected fixture and inherited regressions must run in fresh
acceptance state against the same artifact; no C rebuild is needed. These native
acceptance results remain pending.
