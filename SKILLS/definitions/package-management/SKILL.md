---
name: package-management
description: |
  How to discover, install, upgrade, and remove apps on MiOS across
  BOTH platforms in one consistent dispatch surface: winget for the
  Windows side and flatpak for the Linux side. Load this skill
  whenever the operator says "install X", "find X", "uninstall X",
  "update everything", or any phrase that maps to package
  management. Pick the right platform by the app's nature (see
  decision table below) rather than asking the operator.
metadata:
  hermes:
    requires_tools:
      - winget_search
      - winget_list
      - winget_show
      - winget_install
      - winget_upgrade
      - winget_uninstall
      - flatpak_search
      - flatpak_list
      - flatpak_show
      - flatpak_install
      - flatpak_upgrade
      - flatpak_uninstall
      - launch_windows_app
---
<!-- AI-hint: Defines the unified package-management skill for Hermes, mapping "install", "find", and "update" commands to platform-specific winget (Windows) or flatpak (Linux) tools based on application type.
     AI-related: /usr/share/mios/hermes/skills/package-management/SKILL.md._, mios-launch, mios-winget -->

# Package management on MiOS

> _MiOS-managed: seeded from
> /usr/share/mios/hermes/skills/package-management/SKILL.md._

MiOS exposes two parallel package-management surfaces with the
same verb shape:

| Verb shape | Windows side | Linux side |
| --- | --- | --- |
| Search | `winget_search(query)` | `flatpak_search(query)` |
| List installed | `winget_list()` | `flatpak_list()` |
| Show details | `winget_show(id)` | `flatpak_show(id)` |
| Install | `winget_install(id)` | `flatpak_install(id, scope?)` |
| Upgrade one | `winget_upgrade(id)` | `flatpak_upgrade(id)` |
| Upgrade all | `winget_upgrade()` | `flatpak_upgrade()` |
| Uninstall | `winget_uninstall(id)` | `flatpak_uninstall(id)` |

All verbs return structured JSON envelopes (search: typed result
rows; everything else: stdout/stderr/exit_code). No free-text
parsing required.

## Picking the right platform

Most operator requests don't specify "winget" vs "flatpak". Use
this decision table to route by the app's nature:

| App kind | Pick | Reason |
| --- | --- | --- |
| Windows-only (Office, Notepad++, PowerToys, Visual Studio) | winget | Native .exe / .msi; flatpak can't ship it. |
| Linux-native GUI (GIMP, Inkscape, OBS, Discord, Steam) | flatpak | Sandboxed runtime; works the same across host distros. |
| Cross-platform GUI (Firefox, Chrome, VS Code) | flatpak preferred | Flathub builds are well-maintained; falls back to winget if the operator explicitly says "Windows version". |
| CLI / dev tool (gh, git, nodejs, python) | flatpak (org.flatpak.Builder etc) OR system package | Operator-flagged when system-package would be a better fit; don't auto-install language runtimes via flatpak. |
| Game (any platform) | flatpak | Steam / Epic flatpaks bridge to the actual installers. Don't winget-install games -- the operator launches via Steam/Epic directly. |

When in doubt: search BOTH platforms (`winget_search(X)` +
`flatpak_search(X)`), present the operator the top hits with the
platform tag, and let them pick.

## Multi-step "install and launch"

For "install X and open it", the Phase A.1 planner emits a DAG:

```
flatpak_install(id="com.discordapp.Discord")
launch_windows_app(name="Discord")
```

The install verb blocks until the package is on disk; the
`launch_windows_app` verb then resolves through the canonical
mios-launch chain (which covers the Linux GUI / flatpak target too)
to find the freshly-installed app. Always chain in that order --
launching before install completes hits a "not found" from mios-launch.

## Hardening

All six WRITE verbs (winget_install / winget_upgrade /
winget_uninstall / flatpak_install / flatpak_upgrade /
flatpak_uninstall) are in `[security].firewall_high_privilege_
verbs`. A tainted session -- one where any upstream tool_call
loaded untrusted content -- gets these refused at dispatch. The
operator clears the chain by starting a fresh session.

Both shims force `--silent` / `--noninteractive --assumeyes` flags
on writes so installation prompts can't hang the broker. Output
is truncated to 256 KiB per stream (override via
`MIOS_WINGET_MAX_OUTPUT_BYTES` / `MIOS_FLATPAK_MAX_OUTPUT_BYTES`).

## What NOT to do

- Do NOT shell out to `dnf` / `apt` / `pacman` for app installs.
  The MiOS bootc image is immutable -- only flatpak (for apps)
  and `rpm-ostree install` (for the rare host-tool addition,
  operator-driven only) are valid Linux paths. The agent stays
  in the flatpak lane.
- Do NOT call `winget` via `powershell_run` or `cmd /c winget`.
  Use `mios-winget` (or the dispatch verb) -- those go through
  the broker + Phase C.3 passport-signed audit chain.
- Do NOT assume an install succeeded without parsing the JSON
  envelope. `{"ok": false, "exit_code": 0x80070005}` means
  Windows refused (elevation required, etc.) -- surface the
  actual error to the operator.
