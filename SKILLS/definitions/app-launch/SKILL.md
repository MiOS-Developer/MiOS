---
name: app-launch
description: |
  The canonical chain for launching ANY app, game, URL, or system
  service on a MiOS host. Load this skill whenever the operator says
  "launch / open / start / run X", whether X is a Windows .exe, a
  Steam/Epic/Xbox game, a flatpak, an RPM with a .desktop entry, or
  an internal MiOS service URL. There is exactly ONE entry point:
  the native `launch_windows_app` tool. Do NOT shell out to wsl / cmd /
  powershell / start-process / xdg-open / flatpak run / steam.exe
  yourself -- those paths consistently fail in the agent's execution
  context (no /init interop inside the agent uid, no WSLg env, no
  Windows ACL access) and the model has been observed looping 6+
  failed shell calls before giving up. The mios_verbs.launch_windows_app
  native tool handles every dispatch shape correctly via the
  operator-side launcher broker.
metadata:
  hermes:
    requires_tools:
      - resolve_launch_command
      - launch_windows_app
      - verify_launch
---
<!-- AI-hint: Defines the canonical `app-launch` skill for the Hermes agent, mandating the use of the `launch_windows_app` tool to resolve and execute any application, game, or service via the MiOS broker instead of direct shell commands.
     AI-related: /usr/share/mios/hermes/skills/app-launch/SKILL.md._, mios-launch, mios-hermes, mios-launcher, localhost:9090 -->

# Launching apps on MiOS

> _MiOS-managed: seeded from
> /usr/share/mios/hermes/skills/app-launch/SKILL.md._

## ONE rule

For ANY launch intent, the FIRST and (usually) ONLY tool you call is:

```
launch_windows_app(name="<exact-or-substring>")
```

That's it. `launch_windows_app` resolves through the canonical mios-launch
chain (internal-service alias -> URL/URI literal -> Windows GUI
shortname -> Windows games inventory -> MiOS shim -> Linux GUI ->
plain CLI), dispatches through the operator-side launcher broker
(which has the WSL interop env + Windows ACL access the agent uid
lacks), and returns the resolved dispatch target so you can confirm
it landed.

## What NOT to try (each has failed for the model before)

| Do NOT | Why |
| --- | --- |
| `powershell_run: wsl <app>` | The agent IS already inside WSL; there is no `wsl` binary in its PATH |
| `powershell_run: cmd /c start <app>` | `cmd.exe` lives at `/mnt/c/Windows/System32/cmd.exe`; the agent uid (mios-hermes) cannot exec DrvFs binaries -- "Permission denied" |
| `powershell_run: powershell ...` | Same DrvFs exec wall as cmd.exe; "command not found" in the agent's PATH |
| `run_sandboxed_code: steam://...` | A URI is not a shell command -- bash exits 127 |
| `run_sandboxed_code: flatpak run ...` | The agent's user@<uid> session is NOT the operator's; flatpak inherits the WRONG WAYLAND_DISPLAY, the window never surfaces |
| `open_url https://...` for an APP | `open_url` is for URLs/URIs, not for surfacing a native APP window; the CDP backend (port 9222) is only up when the operator has chromedev running |

The launcher broker (`/run/mios-launcher/launcher.sock`) exists
SPECIFICALLY to bypass all of the above. `launch_windows_app` uses it.
Trust the tool.

## Examples

```
# Operator: "open BeamNG"
launch_windows_app(name="beamng")
# -> {"success": true, "target": "steam://rungameid/284160", ...}

# Operator: "launch wallpaper engine"
launch_windows_app(name="wallpaper engine")
# -> {"success": true, "target": "steam://rungameid/431960", ...}

# Operator: "open chromedev"
launch_windows_app(name="chromedev")
# -> {"success": true, "target": "/usr/bin/flatpak run --branch=stable com.google.ChromeDev", ...}

# Operator: "open the cockpit dashboard"
launch_windows_app(name="cockpit")
# -> {"success": true, "target": "https://localhost:9090", ...}

# Operator: "launch notepad"
launch_windows_app(name="notepad")
# -> {"success": true, "target": "/mnt/c/Windows/System32/notepad.exe", ...}
```

## ACT this turn — never narrate the command, never ask permission

A launch intent is a COMMAND to fire a tool NOW, not a request for advice.
Two defects (operator-flagged 2026-06-14) are forbidden:

- **Do NOT print the command for the operator to run.** "To launch it, use
  `mios-gui epiphany`" or "you can run `launch_windows_app`" is wrong — you were
  asked to launch it, so CALL `launch_windows_app` / `verify_launch` yourself and
  report the actual tool result. Handing the operator a command is a non-answer.
- **Do NOT ask "would you like me to launch it now?"** and do NOT enumerate
  "1) check deps 2) verify 3) check logs" then wait. The operator already said
  launch / open / try / verify. Fire the verb first; act on its result.

## Launch + verify — fire then confirm with `verify_launch`

For a plain launch where you want confirmation it actually surfaced, fire the
launch and then confirm it landed:

```
launch_windows_app(name="<name>")
# -> {"success": true, "target": "...", ...}
verify_launch(app="<name>")
# -> {"launched": true, "verdict": "presented"}
```

`verify_launch` delegates the window success-check to the always-on
mios-daemon-agent (polls the window verifier) and returns `{launched, verdict}`.
Read `launched`:
- `launched: true` -> report success and STOP. Do NOT re-fire.
- `launched: false` -> read the error, try the documented recovery
  (`windows_file_search` / `find_file_fast` to locate the app, resolve its path
  with `resolve_launch_command`, retry the launch with the discovered path, or
  `focus_window` for a hidden window), then re-verify ONCE.
  Report honestly if it still fails — but do this WITHOUT asking permission.

If you are unsure which dispatch shape the name resolves to, call
`resolve_launch_command(name="<name>")` FIRST — it returns the resolved target
without running it — then `launch_windows_app` to fire and `verify_launch` to
confirm.

## Retry / "it didn't launch" follow-ups — re-fire, don't re-ask

When the operator follows a launch turn with a follow-up or retry phrase (as defined in SSOT `[routing].launch_followup_phrases` / `launch_retry_phrases`):

1. The app is whatever the IMMEDIATELY-PRIOR launch turn was about. Pull the
   name from the carried session context / scratchpad — these follow-ups almost
   never re-name the app. Do NOT respond "I need the name of the application";
   the prior turn already established it (e.g. just tried Epiphany ->
   `launch_windows_app(name="epiphany")` then `verify_launch(app="epiphany")`).
2. Immediately call `launch_windows_app(name="<that app>")` again and re-confirm
   with `verify_launch(app="<that app>")`. Read the verdict; report the REAL
   result.
3. Only if you have NO prior target in this session at all, read
   `/var/lib/mios/scratch/agent-nudges.md` and
   `/var/lib/mios/daemon/launch_failures.json` (they record the recent launch +
   verdict), and ask the operator in your reply ONLY if those are also empty.

Never reply with launch ADVICE and a permission question to a retry follow-up —
that is the exact defect this skill exists to kill.

## When launch_windows_app needs help

The substring resolver is fuzzy. If you call `launch_windows_app(name="steam")`
and get multiple games back as candidates (exit_code=2, stderr lists
matches), call it again with a MORE specific name from the list --
do not assume / guess. Resolution failures return their candidate
list in stderr; surface those candidates to the operator and ask
which one.

If `launch_windows_app` returns `success=false` with `stderr` containing
"no resolution", THEN (and only then) reach for `windows_file_search` (or
`find_file_fast` for a fast cross-drive find) to locate the app on disk, resolve
the dispatch target with `resolve_launch_command`, and retry `launch_windows_app`
with the discovered path.

## When to deviate

Only when the operator says "I want to run this raw shell command"
(verbatim instruction to use `powershell_run`). Otherwise: `launch_windows_app`.
