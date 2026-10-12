<!-- AI-hint: Guide to configuring Blink Shell on iOS (iPhone/iPad) and tmux for Shift+Tab and mobile terminal shortcut combos. -->
<!-- AI-related: /usr/share/mios/tmux/blink-mobile-keys.tmux.conf, tools/native/mios-gen/src/terminal.rs, /etc/tmux.conf -->

# Blink Shell & Tmux Mobile Keyboard Integration Guide

This guide details how to configure **Blink Shell** on iOS (iPhone & iPad) and **tmux** within MiOS to enable reliable **`Shift + Tab` (Backtab)**, modifier combinations, and touchscreen-optimized shortcut combos.

---

## 1. The Challenge on Mobile iOS Terminals

In standard desktop terminals, pressing `Shift + Tab` emits the standard ANSI/VT escape sequence `\033[Z` (or `^[[Z`, terminfo `kcbt`).
However, on iOS devices:
1. **Virtual Keyboards** do not natively emit backtab escape sequences when tapping Shift then Tab.
2. **Terminal Multiplexers (tmux)** by default require explicit extended-key capability negotiation (`extended-keys on` / `extkeys`) to distinguish modified tab keys from standard tab characters (`\t`).
3. Chording multiple modifier keys (Shift, Ctrl, Alt) on a phone screen can be cumbersome.

MiOS resolves this with two-sided configuration:
- Host-side tmux key configuration and fallback shortcuts.
- Client-side Blink Shell SmartBar and custom key cast configuration.

---

## 2. Host-Side Tmux Configuration

MiOS ships [`usr/share/mios/tmux/blink-mobile-keys.tmux.conf`](file:///usr/share/mios/tmux/blink-mobile-keys.tmux.conf). It is automatically sourced or can be appended to `~/.tmux.conf`:

```tmux
# 1. Enable xterm extended keys and 256 colors
set -s extended-keys on
set -as terminal-features 'xterm*:extkeys'
set -as terminal-overrides ',*:kbt=\E[Z'
set -gw xterm-keys on

# 2. Native Shift+Tab (Backtab / BTab) Pass-Through
# Passes standard ^[[Z straight to the running process (fzf, vim, zsh, agy, claude)
bind-key -n BTab send-keys Escape "[Z"

# 3. Mobile Touchscreen Fallback Combos:
# Prefix + Tab (tap prefix C-b, then Tab):
bind-key Tab send-keys Escape "[Z"

# Alt + Tab (tap Alt on SmartBar, then Tab):
bind-key -n M-Tab send-keys Escape "[Z"

# Prefix + Backtick (one-tap mobile key):
bind-key ` send-keys Escape "[Z"
```

---

## 3. Blink Shell (iOS) Configuration

### Option A: Custom SmartBar Button (One-Touch on iPhone Screen)
Blink Shell features a customizable toolbar (SmartBar) at the bottom or top of the touch keyboard.

1. In Blink Shell, open settings by running `config` in the prompt, or double-tap with two fingers.
2. Navigate to **Keyboard** > **SmartBar**.
3. Tap **Add Key**:
   - **Label**: `⇧⇥` (or `S-Tab` / `BackTab`)
   - **Action**: `Send Sequence`
   - **Sequence**: `\e[Z` (or `\033[Z`)
4. Save and position the key on your SmartBar. Now, tapping that button sends an instant `Shift+Tab` event into tmux and your running interactive CLI.

### Option B: Custom Key Casts (Physical Keyboards & iPad Magic Keyboard)
For iPad Magic Keyboard, Folio, or Bluetooth keyboards:

1. Open `config` > **Keyboard** > **Custom Keys**.
2. Select **Add Key**:
   - **Key**: `Tab`
   - **Modifiers**: `Shift`
   - **Action**: `Send Sequence`
   - **Sequence**: `\e[Z`
3. Save. Pressing `Shift + Tab` on the physical keyboard will now send `\e[Z`.

---

## 4. Verification

To verify that `Shift + Tab` is correctly received in tmux on MiOS:
1. Open a tmux session: `tmux new -s test`
2. Run `cat -v`
3. Tap your Blink Shell `⇧⇥` button, or type `Prefix` then `Tab`.
4. Output should display:
   ```
   ^[[Z
   ```
5. Press `Ctrl + C` to exit.

With this setup, reverse menu navigation in interactive CLIs, fzf history selection, and completions work smoothly directly from an iPhone!
