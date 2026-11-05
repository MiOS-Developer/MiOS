---
name: windows-control
description: |
  The canonical surface for driving the Windows host under MiOS: WINDOW
  MANAGEMENT (list / focus / move / position / resize / minimize /
  maximize / restore / close / query geometry / read the monitor layout)
  and DESKTOP CONTROL (clicking UI elements, typing text, and pressing
  keys / key-combos into the focused window). Load this skill whenever
  the operator references arranging or interacting with windows --
  "move X to the right", "make X fullscreen", "minimize all the chat
  windows", "put X at 100,100", "resize X to 1920x1080", "click the OK
  button", "type 'hello' here", "press Ctrl+S". One verb per intent; the
  dispatcher resolves title patterns to windows internally and runs the
  Win32 helper through the broker. LAUNCHING apps is a separate concern
  -- see the app-launch skill.
metadata:
  hermes:
    requires_tools:
      - list_windows
      - window_state
      - screen_layout
      - focus_window
      - move_window_to_region
      - place_window_at_pixel
      - resize_window
      - minimize_window
      - maximize_window
      - restore_window
      - close_window
      - windows_desktop_click
      - windows_desktop_click_element
      - windows_desktop_find_element_by_name
      - windows_desktop_list_elements
      - windows_desktop_type_text
      - windows_desktop_press_key
      - tool_search
---
<!-- AI-hint: Defines the windows-control skill for the Hermes agent to manage Windows host window state (focus/move/resize/minimize/maximize/restore/close/query) and drive the Windows desktop (click UI elements, type text, press keys) via native Win32-backed broker dispatch verbs, resolving windows by title and elements by name rather than by raw hwnd or coordinates.
     AI-related: list_windows, focus_window, move_window_to_region, place_window_at_pixel, resize_window, minimize_window, maximize_window, restore_window, close_window, window_state, screen_layout, windows_desktop_click, windows_desktop_click_element, windows_desktop_find_element_by_name, windows_desktop_list_elements, windows_desktop_type_text, windows_desktop_press_key -->

# windows-control -- Windows host window management + desktop control

> _MiOS-managed: the canonical Windows host-control surface map. Hermes's
> NATIVE skill_manage can append learned-by-experience specifics to a
> separate skill (e.g. `learned-windows-control`) without editing this
> file. Delete this marker to take ownership._

Two families of intent live here, both fully deterministic and both
routed through the launcher broker against the Win32 API -- NO vision
LLM and NO screenshots are involved. Pick the right verb per intent.

* **Window management** -- arrange / query top-level windows by title.
* **Desktop control** -- click named UI elements, type text, press keys
  into the focused window.

LAUNCHING / opening / starting apps, games, or URLs is OWNED by the
separate **app-launch** skill -- do not duplicate that here; load
app-launch when the operator asks to open something.

## Part 1 -- Window management

Every window-state intent maps to ONE native dispatch verb. The verb
resolves a title-pattern substring to a window via Win32 enumeration;
you only need to supply enough of the title to disambiguate. Ambiguous
matches pick the first and log alternatives to stderr.

| Intent | Verb | Notes |
| --- | --- | --- |
| "what windows are open" / "list windows" | `list_windows()` | Returns title / pid / hwnd / x,y,w,h for every visible top-level. |
| "where is X" / "X's geometry / state" | `window_state(title=X)` | Read-only position + size + min/max/normal state for one window. |
| "what does my screen / monitor layout look like" | `screen_layout()` | Monitor bounds + work areas. Call this FIRST when you need to compute pixel targets for `place_window_at_pixel`. |
| "focus X" / "bring X to front" / "switch to X" | `focus_window(title=X)` | Optional `position=as-is` to skip the default golden-ratio re-layout. |
| "move X to left/right/center/top-left/..." | `move_window_to_region(title=X, region=<pos>)` | SEMANTIC region names. Use for snap-style positioning. |
| "move X to (a,b)" / "X to coords (a,b)" | `place_window_at_pixel(title=X, x=a, y=b)` | LITERAL pixel coords. Pair with `screen_layout()` first if you must compute the target. |
| "resize X to WxH" / "make X WxH" | `resize_window(title=X, width=W, height=H)` | Pixel WxH. Doesn't move. |
| "minimize X" / "hide X" | `minimize_window(title=X)` | Hides to taskbar. |
| "maximize X" / "fullscreen X" | `maximize_window(title=X)` | Maximize to containing monitor. |
| "restore X" / "un-minimize X" | `restore_window(title=X)` | Returns to last normal-state geometry. |
| "close X" / "quit X" | `close_window(title=X, mode="graceful")` | Sends WM_CLOSE (apps with unsaved state get to prompt). Use `mode="force"` for kill. |

### Multi-step arrangements the planner emits

For multi-window layouts the Phase A.1 planner emits a DAG of these
verbs. Operator-side examples:

- "tile chrome on the left and discord on the right"
  ```
  place_window_at_pixel(title="chrome",  x=0,    y=0)
  resize_window(        title="chrome",  width=960,  height=1080)
  place_window_at_pixel(title="Discord", x=960,  y=0)
  resize_window(        title="Discord", width=960,  height=1080)
  ```
- "fullscreen vscode and minimize everything else"
  ```
  list_windows()   # enumerate first, then act on each title
  maximize_window(title="Visual Studio Code")
  minimize_window(title="Discord")
  minimize_window(title="Telegram")
  ```

## Part 2 -- Desktop control (click / type / keys)

Use these to interact with the CONTENT of a window once it's open. They
drive the Win32 input surface through the broker -- deterministic, no
screenshots.

| Intent | Verb | Notes |
| --- | --- | --- |
| "what can I click in X" / "list the controls" | `windows_desktop_list_elements(title=X)` | Enumerates named UI-Automation elements (buttons, fields, menu items) for a window. Use to discover names BEFORE clicking. |
| "find the <name> button/field" | `windows_desktop_find_element_by_name(title=X, name=...)` | Resolves a named element; returns whether it exists + where. |
| "click the <name> button" (named element) | `windows_desktop_click_element(title=X, name=...)` | PREFERRED click path -- targets by accessibility NAME, not pixels; robust to layout shifts. |
| "click at (a,b)" / "click that spot" | `windows_desktop_click(x=a, y=b)` | LITERAL pixel click. Last resort when an element has no programmatic name. |
| "type 'hello' here" / "enter this text" | `windows_desktop_type_text(text="hello")` | Types into the FOCUSED window. `focus_window` first if the target isn't already foreground. |
| "press Enter" / "hit Escape" | `windows_desktop_press_key(key="Enter")` | Single key or a combo string (e.g. `"Ctrl+S"`, `"Alt+F4"`) into the focused window. |

### The reliable click loop

1. `focus_window(title=X)` so the target is foreground.
2. `windows_desktop_list_elements(title=X)` (or
   `windows_desktop_find_element_by_name`) to learn the element's NAME.
3. `windows_desktop_click_element(title=X, name=...)` -- name-based, not
   pixel-based.
4. Only fall back to `windows_desktop_click(x, y)` when the target has
   no programmatic name (a canvas hit-spot, a borderless dialog).

### Typing example

```
focus_window(title="Notepad")
windows_desktop_type_text(text="hello")
windows_desktop_press_key(key="Ctrl+S")
```

## What NOT to do

- Do NOT use the keyboard/key verbs to drive WINDOW-MANAGEMENT shortcuts
  (Win+Up, Alt+Space, etc). Key presses go to the FOCUSED window, which
  may not be the target -- always use the native window verbs in Part 1
  which target by title/hwnd.
- Do NOT shell out to `wmctrl` / `xdotool` / `nircmd` / PowerShell
  `Set-ForegroundWindow` or raw `SendInput`. The MiOS dispatch path runs
  the Win32 helper through the launcher broker; calling these directly
  bypasses the broker + the Phase B.3 firewall + the C.3 passport-signed
  audit trail.
- Do NOT guess hwnds or pixel coordinates. Pass a `title=` substring for
  windows and an element `name=` for controls; the dispatch layer
  resolves them. Reach for literal pixels only as the documented last
  resort.
- `Ctrl+Alt+Del` is NOT available: Windows reserves the secure attention
  sequence, so the Win32 input surface can't generate it.
- For BROWSER content (a button on a webpage, a web form field), prefer
  Hermes's `browser_*` toolset -- DOM/aria grounding is faster and more
  reliable for web UIs than desktop clicking. The desktop verbs here are
  for native Win32 GUIs and canvas apps where DOM grounding doesn't
  apply.
- This skill does NOT escalate to admin / UAC. The interop uses the
  operator's filtered token; elevated commands require the operator to
  elevate the parent process themselves.
- If you genuinely need a capability the verbs above don't cover,
  discover the real tool via `tool_search(...)` -- never invent or guess
  a tool name.

## Hardening

The WRITE-class window verbs (`minimize_window`, `maximize_window`,
`restore_window`, `resize_window`, `place_window_at_pixel`,
`move_window_to_region`, `close_window`) and the desktop input verbs are
governed by `[security].firewall_high_privilege_verbs`. A tainted
session -- one where any upstream tool_call loaded untrusted content
(web fetch, external `open_url`, system-file `read_file`) -- gets these
verbs REFUSED at dispatch with a `firewall_block` event. The operator
clears the chain by starting a fresh session.
