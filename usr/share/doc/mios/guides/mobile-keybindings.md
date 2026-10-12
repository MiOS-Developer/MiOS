<!-- AI-hint: Shared SSOT shortcut contract for MiOS desktop, editor, native tmux and mobile SSH. -->
<!-- AI-related: /usr/share/mios/mios.toml [keybindings], mios-unit-gen, mios-terminal -->

# MiOS shortcuts and mobile SSH

The SSOT action table defines four entrypoints. `mios-unit-gen keybindings`
projects their names and keys into tmux, Hyprland, Sway, GNOME, VS Code,
code-server and mobile shortcut metadata. Windows installs matching Start Menu
hotkeys and checks existing shortcut registrations before assigning them.

| Action | Desktop | Editor, outside terminal | tmux and SSH |
| --- | --- | --- | --- |
| Terminal | Ctrl+Alt+Shift+T | Ctrl+B, then T | Ctrl+B, then T |
| AI | Ctrl+Alt+Shift+A | Ctrl+B, then A | Ctrl+B, then A |
| Active agents | Ctrl+Alt+Shift+G | Ctrl+B, then G | Ctrl+B, then G |
| System monitor | Ctrl+Alt+Shift+M | Ctrl+B, then M | Ctrl+B, then M |

Desktop chords and terminal prefix sequences occupy different input contexts.
The editor passes the prefix to the shell while the terminal has focus; its
outside-terminal actions have `!terminalFocus`. The SSOT projection removes
the sidebar command from `commandsToSkipShell`. MiOS's prefix table is cleared
before installing its complete declared map, and duplicate action/utility keys
fail generation. The Windows summon chord is Ctrl+Alt+Shift+Space; it avoids
the Windows input-language switch on Win+Space and the tablet emoji picker on
Ctrl+Space. Operator-installed third-party global hotkeys require their own audit.

After Ctrl+B: H/J/K/L select panes, S/V split, N/P switch windows, W selects a
window, Z zooms, Y enters copy mode and D detaches. O cycles the head/workers in
an AI workspace; F toggles compact/automatic layout. Tab sends Shift+Tab to the
agent. B (or Ctrl+B again) sends the prefix to the application. This uses plain
letters, Ctrl and Tab without relying on function keys or Super on an SSH client.

Ctrl+B, then G selects the existing **MiOS Agents** monitor pane in an AI
workspace, without creating another tab. Its compact view uses short agent
labels and pane roles; full identities remain available in JSON. Run
`mios agents --observe` for JSON or `mios agents --watch` for the live view.

On mobile, connect with a PTY and run `mios terminal`, or configure that as the
client's startup command. Reconnect to the same native session after detaching.
Use `mios ai --compact` for a monitor above one active pane in portrait, or
on the right of the active pane in landscape. Workers keep running in a separate
managed session outside the human tab list; Ctrl+B, then
O brings the next agent into the active pane. Narrow or short viewports select
this layout automatically, and a
larger viewport restores the desktop grid. Pane identities and processes survive
the transition. SSH client window placement and fullscreen belong to the client.
Termius supports startup commands, snippets and a custom keyboard bar. Add Ctrl,
Esc, Tab and arrow controls there. Blink's Smart Keys expose Ctrl/Alt/Esc; a
hardware Caps Lock remap can supply Ctrl and tap-Esc. Keep client keyboard
remaps separate from server key configuration.

Fonts are rendered by the client. Install the SSOT font on desktop clients;
SSH cannot remotely install an iPhone font. A user TOML override can set
`[theme.tmux].glyph_mode = "ascii"` for clients without Nerd Font glyphs. This
keeps tmux status text readable without changing action keys or the palette.
SSH sessions use `[theme.tmux].remote_glyph_mode` and
`[theme.prompt].remote_glyph_mode` (the full SSOT theme by default). The same SSOT palette
remains active. CMD startup applies `[theme.terminal].windows_codepage` (UTF-8)
before loading Oh My Posh; SSH and consoles without `WT_SESSION` select the
remote projection. ASCII is an explicit override, not an automatic downgrade.
Windows-to-WSL entry forwards the remote-terminal marker.

Windows CMD has a machine-PATH `mios.cmd` dispatcher, so it does not depend on
a PowerShell alias. `mios ssh user@host` starts the native remote interface.
Image startup resolves the actual registered WSL name rather than assuming
`MiOS-DEV` was renamed from `podman-MiOS-DEV`.

Sources: [Blink customization](https://docs.blink.sh/basics/customize),
[Blink keyboard tips](https://docs.blink.sh/basics/tips-and-tricks),
[Termius mobile agent workflow](https://termius.com/blog/8-tips-for-using-ai-agents-on-mobile-in-termius),
[VS Code terminal keyboard routing](https://code.visualstudio.com/docs/terminal/advanced),
[Oh My Posh CMD integration](https://ohmyposh.dev/docs/installation/prompt).
