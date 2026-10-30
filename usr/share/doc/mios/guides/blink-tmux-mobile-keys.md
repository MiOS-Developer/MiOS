<!-- AI-hint: Guide to configuring Blink Shell on iOS (iPhone/iPad) and tmux for Shift+Tab and mobile terminal shortcut combos. -->
<!-- AI-related: /usr/share/mios/mios.toml [keybindings.tmux] [keybindings.agent], /usr/share/mios/tmux/mios-keys.tmux.conf, /usr/share/mios/tmux/blink-mobile-keys.tmux.conf, tools/native/mios-unit-gen/src/tmux_os.rs, /etc/tmux.conf -->

# Blink Shell & Tmux Mobile Keyboard Integration Guide

This guide covers **Blink Shell** on iOS (iPhone and iPad) with **MiOS-Tmux**. It explains how to send `Shift + Tab` (Backtab), how to accept an agent prompt, and how to reach every tmux action from a phone keyboard.

---

## 1. The challenge on iOS terminals

- In desktop terminals, `Shift + Tab` emits `\033[Z` (terminfo `kcbt`). iOS soft keyboards do not.
- `Shift + Enter` reaches tmux only as an extended key (`extended-keys`, CSI u). Many mobile clients never send it.
- Chords of several modifiers are slow to type on a phone screen.

MiOS fixes this on the host, so it works the same over SSH, Blink, ttyd and the Linux console:

- **Plain keys after the prefix.** Every action is the prefix (`Ctrl+B`) followed by one key.
- **One menu.** Every action can also be reached from a menu with the arrow keys and Enter.

---

## 2. Host side: one generated profile

`/usr/share/mios/tmux/blink-mobile-keys.tmux.conf` is **generated** by `mios-unit-gen keybindings` from `mios.toml`. Never hand-edit it. It is a shim that loads the one global profile, so older `source-file` references keep working:

```tmux
source-file /etc/tmux.conf
```

The profile (`mios-keys.tmux.conf`) projects `[keybindings.tmux]` and `[keybindings.agent]`:

| Need | Key | SSOT |
|---|---|---|
| MiOS menu (apps, pane, window, session; arrows + Enter) | `Ctrl+B`, then `Space` | `[keybindings.tmux].menu_key` |
| Apps and TUIs launcher | `Ctrl+B`, then `e` | `[keybindings.tmux].launcher_key`, `[[terminal.tuis]]` |
| Shift+Tab for the focused program | `Ctrl+B`, then `Tab` | `[keybindings.tmux].bindings` |
| Accept the agent prompt (no extended keys needed) | `Ctrl+B`, then `Enter` | `[keybindings.agent].accept_key` |
| Accept the agent prompt (clients that send extended keys) | `Shift+Enter` | `[keybindings.agent].accept_chord` |
| Scroll back / forward | `Ctrl+B`, then `PgUp` / `PgDn` | `[keybindings.tmux].bindings` |
| List every key | `Ctrl+B`, then `?` | `[keybindings.tmux].bindings` |

Extended keys come from `[keybindings.tmux].extended_keys`, `extended_keys_format` and `extended_key_terms`. They are set in MiOS-owned `terminal-features` slots, so a reload never stacks duplicates.

The old hand-written layer is retired:

- **Prefix + backtick.** It sent Backtab, but the file was never loaded. Backtick is now the float shell toggle.
- **Root `BTab` re-send.** Dropped: tmux already passes BTab through.
- **`M-Tab` and `xterm-keys`.** Dropped as obsolete.
- **`kbt` override.** Dropped: it grew on every reload, and terminfo already has it.

**The recommended phone path is `Ctrl+B`, then `Space`, then an item.** Backtick and `*` sit on secondary symbol pages of iOS soft keyboards, so they are accelerators only. Every menu item also has a letter or digit shortcut.

---

## 3. Blink Shell (iOS) configuration

### Option A: Custom SmartBar button (one touch on the iPhone screen)
Blink Shell has a customizable toolbar (SmartBar) at the bottom or top of the touch keyboard.

1. In Blink Shell, open settings by running `config` in the prompt, or double-tap with two fingers.
2. Navigate to **Keyboard** > **SmartBar**.
3. Tap **Add Key**:
   - **Label**: `⇧⇥` (or `S-Tab` / `BackTab`)
   - **Action**: `Send Sequence`
   - **Sequence**: `\e[Z` (or `\033[Z`)
4. Save and position the key on your SmartBar. Tapping it sends `Shift+Tab` into tmux and your running CLI.

A second SmartBar key that sends Ctrl+B, then Space, opens the MiOS menu in one tap.

### Option B: Custom key casts (physical keyboards and iPad Magic Keyboard)
For iPad Magic Keyboard, Folio or Bluetooth keyboards:

1. Open `config` > **Keyboard** > **Custom Keys**.
2. Select **Add Key**:
   - **Key**: `Tab`
   - **Modifiers**: `Shift`
   - **Action**: `Send Sequence`
   - **Sequence**: `\e[Z`
3. Save. Pressing `Shift + Tab` on the physical keyboard now sends `\e[Z`.

**Unverified:** whether a Blink long-press reaches tmux as a right-click (the MiOS pane menu) depends on Blink's mouse reporting. Use `Ctrl+B`, then `Space`, which always works.

---

## 4. Verification

To check that `Shift + Tab` reaches programs in MiOS-Tmux:

1. Open a Tmux-OS session: `mios terminal --new`.
2. Run `cat -v`.
3. Tap your Blink Shell `⇧⇥` button, or type `Ctrl+B` then `Tab`.
4. The output shows:
   ```
   ^[[Z
   ```
5. Press `Ctrl + C` to exit.

Reverse menu navigation in interactive CLIs, fzf history selection and completions then work directly from an iPhone.
