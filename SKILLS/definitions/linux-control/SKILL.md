---
name: linux-control
description: Use when the operator asks to control the LINUX / Wayland desktop on a MiOS host -- screenshot it, click/type/press keys, find a UI element, or list windows -- on bare-metal/VM GNOME or KDE, on WSLg, OR on a remote MiOS desktop node. The SEMANTIC path (linux_desktop_find_element_by_name / linux_desktop_locate_element via AT-SPI) needs NO vision model and NO pixels. The VISION path (linux_desktop_screenshot + linux_desktop_locate_element via qwen3-vl / UI-TARS) is the fallback only when the accessibility tree is empty. The executor is ENVIRONMENT-ADAPTIVE -- the same verbs drive bare-metal GNOME, WSLg, and a federated remote desktop with no change.
metadata:
  hermes:
    requires_tools:
      - linux_desktop_screenshot
      - linux_desktop_window_list
      - linux_desktop_find_element_by_name
      - linux_desktop_locate_element
      - linux_desktop_click
      - linux_desktop_type_text
      - linux_desktop_press_key
      - linux_desktop_press_key_combo
---
<!-- AI-hint: Defines the linux-control skill for interacting with Linux/Wayland desktops via the linux_desktop_* verbs, providing an environment-adaptive interface for UI automation using AT-SPI semantic trees or vision-based fallbacks.
     AI-related: /etc/mios/ai/v1/mcp.json, /etc/mios/ai/v1/a2a-peers.json, mios-computer-use, mios-pc-control, mios-env-probe, mios-environment, mios-grounding, mios-pc-vision, mios-computer-use-server -->

# linux-control -- Linux/Wayland computer-use under MiOS

The Linux peer of [windows-control](../windows-control/SKILL.md) (which
drives the Windows host via Win32). This skill drives the **Linux/Wayland
desktop** via the `linux_desktop_*` verbs. It is environment-adaptive:
on bare-metal/VM GNOME or KDE it uses the RemoteDesktop + Screenshot
portals; on WSLg it transparently delegates to the Windows-host executor; and
when a remote desktop's `executor_endpoint` is configured it routes the
op there. **You don't pick the backend -- the executor does.**

## linux_desktop_* vs windows-control -- which family do I use?

* **Linux/Wayland target** (bare-metal MiOS, a Linux VM, a remote MiOS
  desktop node) -> use the **`linux_desktop_*`** verbs.
* **Windows-host target** (you're on MiOS-on-WSL2 and the operator means
  the Windows desktop the distro lives inside) -> use the **`windows_desktop_*`**
  verbs / [windows-control](../windows-control/SKILL.md).

If you're unsure which desktop the operator means, check the environment
first (`sys_env_snapshot`, or `skill_view name=mios-environment`).
On a pure Linux deployment there is no Windows host, so `linux_desktop_*`
is the only answer. The Linux executor also self-delegates to the
Windows-host executor under WSLg, so reaching for `linux_desktop_*` is the
safe default when the target is "this machine's desktop".

## The two paths (pick by what the operator asked for)

### Path A: SEMANTIC (no vision, no screenshots) -- PREFER THIS

The accessibility tree (AT-SPI2) exposes every GTK/Qt widget's role, name,
and on-screen coordinates **without a single pixel or model call**. Always
try this first.

* "click the Save button" / "where is the OK button"
  -> `linux_desktop_locate_element query="Save button"`  (AT-SPI first; returns {x,y})
  -> `linux_desktop_click x=<x> y=<y>`
* "what UI elements match X" / "find the address bar"
  -> `linux_desktop_find_element_by_name query="address"`  (role/name -> screen coords)
* "what windows are open"
  -> `linux_desktop_window_list`

`linux_desktop_locate_element` returns `{"x":..,"y":..,"confidence":0.99,"source":"atspi"}`
when the a11y tree has the element. Deterministic, instant, offline.

### Path B: VISION (screenshot -> grounded coordinates) -- FALLBACK

Use this only when AT-SPI returns nothing -- Chromium/Electron without
accessibility, canvas apps, custom-rendered UIs. `linux_desktop_locate_element`
does this automatically: if the a11y tree has no match it screenshots the
desktop and calls the grounding VLM.

```
linux_desktop_screenshot path=/tmp/screen.png
linux_desktop_locate_element query="the blue Submit button"
  -> {"x":814,"y":562,"confidence":0.9,"source":"atspi|vision","reasoning":"..."}
linux_desktop_click x=814 y=562
linux_desktop_screenshot path=/tmp/after.png    # verify
linux_desktop_locate_element query="did the Submit button disappear?"
```

The grounding model defaults to `qwen3-vl:4b` on the local inference lane (JSON
coordinate output). When the gated `mios-grounding` lane is enabled
(UI-TARS-1.5-7B), `mios-pc-vision` auto-switches to the UI-TARS Action-DSL
parser -- same `linux_desktop_locate_element` interface, better grounding on
dense UIs. The model + endpoint are SSOT (`mios.toml [ai].vision_grounding_model` /
`[computer_use].grounding_model`) -- never hardcode them.

## Acting -- write-class verbs

`linux_desktop_click`, `linux_desktop_type_text`, `linux_desktop_press_key`,
`linux_desktop_press_key_combo` send real input via the RemoteDesktop portal
(GNOME/KDE) or a self-written uinput device (wlroots / headless seat). They are
**write-class** -- they go through the DoD / approval gate. Read-class verbs
(`linux_desktop_screenshot`, `linux_desktop_window_list`,
`linux_desktop_locate_element`, `linux_desktop_find_element_by_name`) run freely.

* `linux_desktop_type_text text="hello world"`      type literal text
* `linux_desktop_press_key key=Enter`                single key (Enter/Tab/F5/Up/...)
* `linux_desktop_press_key_combo combo="Ctrl+S"`     modifier combo
* `linux_desktop_click x=200 y=140 button=right`     click (left|right|middle)

## Decision tree

```
Operator wants to control the Linux desktop
    |
    +-- "what's on screen / what windows"  -> linux_desktop_window_list
    +-- "find / where is <element>"        -> linux_desktop_locate_element (AT-SPI, vision fallback)
    +-- "click <named element>"            -> linux_desktop_locate_element -> linux_desktop_click
    +-- "type / press <keys>"              -> linux_desktop_type_text / linux_desktop_press_key / linux_desktop_press_key_combo
    +-- "click <thing only visible as pixels>" -> linux_desktop_screenshot + linux_desktop_locate_element (vision)
    +-- target is a WEBPAGE                 -> open_url (DOM/aria, no vision)
    +-- target is the WINDOWS host          -> windows-control (windows_desktop_* verbs)
```

For BROWSER tasks prefer the page/DOM path (`open_url` + the page-reading
tools) -- DOM/aria grounding is faster and more reliable than vision for web
UIs. The `linux_desktop_*` vision loop is for native GTK/Qt/canvas apps and the
desktop shell itself.

## Worked example -- click a button by name, no vision model

```bash
# AT-SPI knows the button's role + coordinates -- no screenshot, no VLM:
linux_desktop_locate_element query="the Save button"
  -> {"x":640,"y":480,"confidence":0.99,"source":"atspi"}
linux_desktop_click x=640 y=480
```

## Worked example -- canvas app the a11y tree can't see

```bash
linux_desktop_screenshot path=/tmp/canvas.png
linux_desktop_locate_element query="the red record button"   # AT-SPI empty -> vision VLM
  -> {"x":512,"y":700,"confidence":0.88,"source":"vision"}
linux_desktop_click x=512 y=700
```

## Driving a REMOTE desktop (federation)

A second MiOS/Linux machine runs `mios-computer-use-server` (dual MCP + A2A).
Register it once in the operator overlay and the central agent consumes it:

* As an **MCP server** -- add to `/etc/mios/ai/v1/mcp.json` -> its
  desktop-control tools appear in the agent loop as `mcp.<node>.*`.
* As an **A2A peer** -- add to `/etc/mios/ai/v1/a2a-peers.json` -> its
  `desktop-control` skill appears at `/v1/a2a/skills`; delegate a whole
  desktop task to it via `delegate_subtask_to_peer_agent` with a shared
  `contextId`.

See `usr/share/doc/mios/concepts/computer-use-federation.md` for the
overlay shapes. You don't do anything special in-chat -- the registered
node's tools are just there alongside the local `linux_desktop_*` verbs.

## What this skill is NOT

* NOT for the Windows host -- that's `windows_desktop_*` / windows-control.
* NOT for webpages -- prefer `open_url` + DOM grounding.
* NOT a way to bypass the approval gate -- write-class verbs are gated.
* NOT dependent on ydotool -- input is portal (libei) or our own uinput
  device, never the AGPL ydotool daemon.
