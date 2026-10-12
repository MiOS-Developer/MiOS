<!-- AI-hint: Manual pages distilled from the source comments of windows, sanitized, each passage anchored to the comment it came from. -->

# windows

### Mode

Mode: 'auto' (default) omits the mode param so the page follows the host light/dark theme LIVE
via prefers-color-scheme (WebView2/Chromium tracks the OS theme; the page re-grades on change --
same media query works on Linux). Only an explicit -Mode dark|light pins it. This is the real
cross-platform theme sync: no reload needed when the user toggles Windows (or a Linux DE) theme.

<!-- mios-src:68ee939e6bbc from usr/share/mios/windows/Set-MiOSWallpaper.ps1:69-72 -->

### resolve the WSL distro generatively from the registry...

---- resolve the WSL distro generatively from the registry -------------------
(wsl.exe -l emits UTF-16 that mangles under the default console encoding ->
"p" instead of "podman-MiOS-DEV"; the Lxss registry is clean + null-free.)
Prefer a distro whose name carries the MiOS product (that's where the MiOS
MCP server lives), else the WSL default distro, else the first registered.

<!-- mios-src:961276c4a03d from usr/share/mios/windows/mios-claude-mcp-setup.ps1:31-35 -->

### The iGPU's ROLE is the ALWAYS-ON LIGHT-COMPUTE BRAIN (...

The iGPU's ROLE is the ALWAYS-ON LIGHT-COMPUTE BRAIN (
"iGPU SHOULD BE THE MICRO LLM ... AND the always-on MiOS daemon background
agent"): it hosts the micro-LLM (router/refine/judge/web-expand, hit every
turn) + the mios-daemon-agent, so it is NEVER cold and the dGPU/CPU are
freed. It is NOT a heavy reasoning agent (it is ~7 tok/s -- too slow for big
facets). So serve a SMALL fast instruct GGUF, not the old 3B. Override with
-Model / -ModelUrl for a different micro/daemon brain (e.g. a Qwen3-1.7B
GGUF to match the daemon model exactly).

<!-- mios-src:3ccf7777c6c0 from usr/share/mios/windows/mios-igpu-server.ps1:40-47 -->

### 64K ctx (iGPU is now ALSO the Hermes-desktop FRONT DOOR via...

64K ctx (iGPU is now ALSO the Hermes-desktop FRONT DOOR
via mios-model-router's mios-orchestrator lane). The front door must hold the
full ~17K-token MCP tool surface (113 tools) that Hermes sends EVERY turn --
at the old 8192 the tool defs were TRUNCATED, the model never saw open_app,
and it answered in prose (the recurring "FAILURE"). 65536 also matches the
router-advertised ctx + Hermes's 64K floor. KV for a 1.5B at 64K is ~1.9 GB
on the iGPU's shared system RAM -- cheap. (ctx-size only sizes the KV pool;
it does NOT slow prefill -- prefill cost scales with the ACTUAL prompt len.)

<!-- mios-src:21bcde9029ce from usr/share/mios/windows/mios-igpu-server.ps1:49-56 -->

### Single inference slot (KV-paging). llama-server defaults to...

Single inference slot (KV-paging). llama-server defaults
to 4 parallel slots, which (a) splits ctx-size 4 ways (16384 each) and (b)
makes the OpenAI /v1 endpoint land a request on ANY slot, so the agent-pipe's
per-slot KV save/restore (_kv_paging, slot 0) can't deterministically bracket
it. ONE slot = the full 65536 ctx + every request lands on slot 0, so demand-
paging the conversation's KV to/from disk is reliable. The iGPU front door
processes one user turn at a time anyway; delegated children that round-robin
back onto the iGPU simply queue, which is fine.

<!-- mios-src:7b3fa5014656 from usr/share/mios/windows/mios-igpu-server.ps1:58-65 -->

### Pin to a SINGLE Vulkan device so llama.cpp does NOT...

Pin to a SINGLE Vulkan device so llama.cpp does NOT layer-split onto the
RTX 4090 (Vulkan also enumerates the 4090, and GPU-PV shares it with the
WSL VM where hermes runs -- spilling onto it would steal hermes's VRAM).
Vulkan device ENUMERATION ORDER IS NOT STABLE across processes (operator
the task-managed server got Vulkan0=RTX 4090 and ran the "iGPU"
model on the dGPU at 138 tok/s, stealing hermes's VRAM; standalone
--list-devices on the same host showed Vulkan0=AMD). So a fixed index is
unreliable. 'auto' (default) resolves the AMD/Radeon device by NAME at
launch (see below). Pass an explicit VulkanN to override.

<!-- mios-src:e546c44939e2 from usr/share/mios/windows/mios-igpu-server.ps1:68-76 -->

### resolve the AMD iGPU device by NAME (enumeration order is...

---- resolve the AMD iGPU device by NAME (enumeration order is unstable) -----
CRITICAL: Vulkan device INDICES are not stable across
processes, so a fixed --device Vulkan0 sometimes pinned the RTX 4090 and ran
the "iGPU" model on the dGPU (138 tok/s, stealing hermes's VRAM). Resolve the
index by NAME here, in the SAME process context that will launch the server
(so the enumeration it sees matches), picking the AMD/Radeon device and NEVER
an NVIDIA one. `--list-devices` prints e.g. "  Vulkan1: AMD Radeon(TM) Graphics
(..)". Only runs for -Device auto; an explicit VulkanN is honoured as-is.

<!-- mios-src:1f1a120d5fbf from usr/share/mios/windows/mios-igpu-server.ps1:262-269 -->

### llama-server logs to STDERR. Under Windows PowerShell 5.1...

llama-server logs to STDERR. Under Windows PowerShell 5.1 (which the scheduled
task now uses for a STABLE interpreter path -- the MSIX pwsh alias is
unresolvable by Task Scheduler, see -Install above), a native command writing
to stderr with $ErrorActionPreference='Stop' + 2>&1 raises a terminating
NativeCommandError and KILLS the server on its FIRST log line (operator
task exited 1, port never bound). Relax to Continue for the exec
so the server's normal logging flows into the Tee'd log instead of aborting.
(pwsh 7 does not treat native stderr this way, so this is harmless there.)

<!-- mios-src:89e4d9fb74f6 from usr/share/mios/windows/mios-igpu-server.ps1:292-299 -->

### CRITICAL ("iGPU NEVER fired -- not a single tick on Task...

CRITICAL ("iGPU NEVER fired -- not a single tick on Task
Manager"): newer llama.cpp auto-fits params to device memory ("fitting params
to device memory ...") and SILENTLY places all layers on the CPU -- it prefers
the big Ryzen 9950X3D -- EVEN WITH --device VulkanN + --n-gpu-layers 99. So the
"iGPU server" ran a 1.5B on CPU at 0% iGPU util. `-fit off` disables that
auto-placement so the explicit iGPU offload is honoured. VERIFIED 0% -> 99.6%.

<!-- mios-src:31fe7f6cb1cb from usr/share/mios/windows/mios-igpu-server.ps1:302-307 -->

### Enumerate visible top-level windows -> list of @{ title...

Enumerate visible top-level windows -> list of @{ title; pid; proc }.

HANG-HARDENING (/windows timed out while / answered):
the per-window `Get-Process -Id` call below was made INSIDE the EnumWindows
callback, so a single hung/protected process (or a slow WMI-backed lookup)
stalled the ENTIRE enumeration and the route never returned. Build a
PID -> ProcessName snapshot ONCE up front (single Get-Process) and look up
from the hashtable in the callback -- no blocking syscall per window, and
far faster across a desktop full of windows.

<!-- mios-src:4e8037e52913 from usr/share/mios/windows/mios-oscontrol-server.ps1:229-237 -->

### CACHE the PID->name snapshot with a short TTL (the executor...

CACHE the PID->name snapshot with a short TTL (the
executor WEDGED under launch load). During a launch the agent verify-poll
+ mios-autocenter hammer /windows ~30x in 30s; re-running the heavy +
occasionally-stalling Get-Process on EVERY request was the load-induced
hang. Rebuild only when the snapshot is stale (>2s); a burst reuses it, so
/windows stays cheap (just the non-blocking EnumWindows walk). script:
scope so the EnumWindows delegate callback always sees the map.

<!-- mios-src:4e1393763852 from usr/share/mios/windows/mios-oscontrol-server.ps1:240-246 -->

### Perform a window op on the matching window(s). op =...

Perform a window op on the matching window(s). op = close|focus|move|resize|
state. close is a GRACEFUL WM_CLOSE (operator binding: never force-kill /
Stop-Process a window). Returns {ok, op, count, matched:[...]}.

<!-- mios-src:111c66e4fd7d from usr/share/mios/windows/mios-oscontrol-server.ps1:315-317 -->

### move/resize/center use SetWindowPos with SWP_ASYNCWINDOWPOS...

move/resize/center use SetWindowPos with SWP_ASYNCWINDOWPOS instead
of MoveWindow (the executor stalled DURING a
launch -> centering hung the single-threaded listener). MoveWindow
(and a repaint:$true SetWindowPos) SENDS WM_WINDOWPOSCHANGING/paint
SYNCHRONOUSLY to the target window's message loop and BLOCKS until
it acks -- a freshly-launched app isn't pumping its queue yet, so
the call hangs for seconds and every queued /windows poll behind it
times out. SWP_ASYNCWINDOWPOS (0x4000) POSTS the request to the
target thread and returns immediately -> the listener never blocks.
  SWP_NOSIZE=0x1 NOMOVE=0x2 NOZORDER=0x4 NOACTIVATE=0x10 SHOWWINDOW=0x40 ASYNC=0x4000

<!-- mios-src:ffd4affdc191 from usr/share/mios/windows/mios-oscontrol-server.ps1:360-369 -->

### input (SendInput-equivalent) + capture on the interactive...

---- input (SendInput-equivalent) + capture on the interactive desktop -------
These mirror mios-pc-control.ps1 but run IN the executor's interactive session
so SetCursorPos / mouse_event / SendKeys hit WinSta0\Default (the operator's
real desktop), not a blind service window station.

<!-- mios-src:3c6bee7cdb85 from usr/share/mios/windows/mios-oscontrol-server.ps1:503-506 -->

### ── UIA semantic element targeting (the #1 Windows gap --...

── UIA semantic element targeting (the #1 Windows gap --
Linux has AT-SPI, Windows control was pixel-only). Find a control BY NAME via UI
Automation, scoped to the FOREGROUND window's subtree (fast; avoids a whole-
desktop tree walk that can hang), returning its clickable CENTER so the agent
acts on a SEMANTIC target instead of guessed pixels. NOT arbitrary code exec --
only enumerates + acts on the active window's accessibility tree.

<!-- mios-src:459730807487 from usr/share/mios/windows/mios-oscontrol-server.ps1:534-539 -->

### Build the search roots

Build the search roots: the foreground window FIRST (fast path), then
EVERY top-level window on the desktop. A just-launched app (e.g. Notepad)
is frequently not yet the foreground window by the time this call runs, so
relying on GetForegroundWindow alone returned "no foreground window" and
the type never landed. Searching all windows for the editable control --
then activating its window -- makes set-value land regardless of focus.

<!-- mios-src:c12ec6e37ca0 from usr/share/mios/windows/mios-oscontrol-server.ps1:641-646 -->

### Read-back helpers for type verification

Read-back helpers for type verification: the FOREGROUND-window title and the
focused control's text (UIA Value/Text pattern). Used to confirm typed text
ACTUALLY landed, so the executor never reports a false success.

<!-- mios-src:ec2f5f29985d from usr/share/mios/windows/mios-oscontrol-server.ps1:713-715 -->

### Resolve an app name to a launch target. Returns the thing...

Resolve an app name to a launch target. Returns the thing Start-Process can
take: a Start-Menu .lnk path if one matches, else the bare name (Start-Process
handles exe-on-PATH, registered apps, protocols/URIs like ms-settings:).
Cached index of Start-Menu .lnks: @(@{Name;Path;Wslg}). The recursive scan +
per-shortcut COM target-resolution (for the WSLg-skip) is the per-launch cost
the executor-first routing added; cache it with a TTL so repeat launches are
instant ("streamlined, fast and perfected"). Installs are
rare -> a 60s TTL is safe; a cold cache (first launch / post-install) pays the
full scan once. script: scope so it survives across requests.

<!-- mios-src:83ecd8d17d15 from usr/share/mios/windows/mios-oscontrol-server.ps1:989-997 -->

### For known browsers, return bare .exe so Windows resolves it...

For known browsers, return bare .exe so Windows resolves it via App Paths registry,
which natively reuses the running instance and opens a tab instead of a new window.

<!-- mios-src:c9f6c4c570ba from usr/share/mios/windows/mios-oscontrol-server.ps1:1031-1032 -->

### Match the cached Start-Menu index, SKIPPING WSLg-exported...

Match the cached Start-Menu index, SKIPPING WSLg-exported shortcuts
(target = WSL\wslg.exe -- those launch a LINUX flatpak through WSLg, which
on the executor is slow + bypasses the operator's trained flatpak path;
letting the executor MISS makes mios-launch fall back to Linux). A NATIVE
Windows app .lnk targets a real .exe and is kept -- "codium" picks the
native VSCodium over the WSLg one; "discord"/"notepad" resolve normally.
Prefer the SHORTEST matching BaseName.

<!-- mios-src:fa298ae72b08 from usr/share/mios/windows/mios-oscontrol-server.ps1:1040-1046 -->

### FAST MISS

FAST MISS: if the launch did NOT fire (target unresolvable on Windows /
Start-Process threw -- e.g. a Linux-only flatpak name), return immediately
instead of sleeping + polling for a window that was never started. The
executor is now tried FIRST for every app, so a
non-Windows app must fall through to the Linux chain cheaply.

<!-- mios-src:da83e9abca09 from usr/share/mios/windows/mios-oscontrol-server.ps1:1090-1094 -->

### READ-BACK VERIFICATION (the agent claimed it typed when...

READ-BACK VERIFICATION (the agent claimed it typed when
nothing reached a window -> "LIAR"). NEVER report success unless the text
actually landed: read the focused control value (UI Automation) and/or the
foreground-window title BEFORE and AFTER SendKeys. verified ONLY if the value
contains/grew by the sent text or the title changed; otherwise exit 1 with a
real reason so the orchestrator surfaces uncertainty, never a false success.

<!-- mios-src:9ccf63bbc160 from usr/share/mios/windows/mios-pc-control.ps1:125-130 -->

### STRICT verification

STRICT verification: success ONLY if the EXACT sent text
actually appears in the focused-control value OR the foreground title (Notepad
shows it as "*<text> - Notepad"). A partial / dropped-keystroke result must NOT
pass -- "value grew" / "title changed" alone was the RESIDUAL lie (it let
"RD-5566" verify for "DEHARD-5566"). If neither is readable/contains it -> NOT
verified (exit 1) so the orchestrator can surface uncertainty / retry.

<!-- mios-src:593dc2fbb58a from usr/share/mios/windows/mios-pc-control.ps1:177-182 -->

### Machine-mode envelope so the agent reads typed fields, not...

Machine-mode envelope so the agent reads typed fields,
not Format-Table prose. Operator directive
task #148 (shim JSON sweep).

<!-- mios-src:2103571517ee from usr/share/mios/windows/mios-pc-control.ps1:281-283 -->

### Center the window on the primary monitor's work area....

Center the window on the primary monitor's work area.
Usage: window-center <hwnd-or-pid>
Operator directive "MiOS apps STILL don't center
launch and don't self center" -- Windows apps launched via
Start-Process appear at default Win32 placement (often top-
left or last-position). This puts them in the screen center.

<!-- mios-src:980f2fede3a4 from usr/share/mios/windows/mios-pc-control.ps1:342-347 -->

### Graceful close of a window via WM_CLOSE. Usage...

Graceful close of a window via WM_CLOSE.

Usage: window-close <hwnd-or-pid>

Posts WM_CLOSE to the target window so the app's own message
loop handles it (same as the operator clicking the X / Alt+F4).
Most apps prompt to save unsaved work, then exit cleanly.

NOT a kill: never use Stop-Process / taskkill /f for "close
this window" -- that loses unsaved state and may not even
close the right process when the target hosts multiple
windows (Chrome, Discord, browsers in general). Operator
directive chat showed agent running
`pkill -f hermes-agent` thinking "close the crew" meant
"close the agent crew" -- self-terminated. WM_CLOSE on the
right window is the correct verb every time.

<!-- mios-src:87d75c0a1540 from usr/share/mios/windows/mios-pc-control.ps1:384-399 -->

### Liveness is checked IN THE VM, not on Windows 127.0.0.1....

Liveness is checked IN THE VM, not on Windows 127.0.0.1. The services run in the
WSL2 VM; a Windows-side TCP probe false-positives when a Windows process shadows
a port (e.g. http.sys holds :8443, so a Windows probe "sees" it but it forwards to
nothing real). We count only ports the VM binds on 0.0.0.0 / * / [::] (loopback-
only VM ports aren't WSL-forwarded to Windows, so Tailscale can't reach them).

<!-- mios-src:fbe05fbcd2d8 from usr/share/mios/windows/mios-tailscale-serve.ps1:101-105 -->
### resolve the WSL distro generatively from the registry...

---- resolve the WSL distro generatively from the registry -------------------
(wsl.exe -l emits UTF-16 that mangles under the default console encoding ->
"p" instead of "podman-MiOS-DEV"; the Lxss registry is clean + null-free.)
Prefer a distro whose name carries the MiOS product (that's where the MiOS
MCP server lives). A non-MiOS default distro cannot serve the native component.

<!-- mios-src:ac481736771e from usr/share/mios/windows/mios-claude-mcp-setup.ps1:29-33 -->

### resolve the MiOS WSL distro generatively...

---- resolve the MiOS WSL distro generatively ---------------------------------
The shared Resolve-MiosDistro when the MiOS globals are loaded; otherwise the
Lxss registry walk it was lifted from (wsl.exe -l emits UTF-16 that mangles
under the default console encoding), preferring a distro that carries the
MiOS product. Resolved here, in the operator's session, so -Install can pin it
for the service, whose own HKCU is not the operator's.

<!-- mios-src:4f8208af0d53 from usr/share/mios/windows/mios-igpu-server.ps1:120-125 -->

### Center the window on its current monitor's work area....

Center the window on its current monitor's work area.
Usage: window-center <hwnd-or-pid>
Operator directive "MiOS apps STILL don't center
launch and don't self center" -- Windows apps launched via
Start-Process appear at default Win32 placement (often top-
left or last-position). This puts them in the screen center.

<!-- mios-src:ab8c3383025b from usr/share/mios/windows/mios-pc-control.ps1:336-341 -->
