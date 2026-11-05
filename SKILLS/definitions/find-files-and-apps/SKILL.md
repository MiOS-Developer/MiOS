---
name: find-files-and-apps
description: |
  How to LOCATE files and applications on a MiOS host by navigating the
  real filesystems generatively -- never from a hardcoded list of names
  or paths, never by guessing. Load this whenever you need to find where
  something IS (a binary, a config, a document, an installed app, a game,
  an .exe/.lnk) before acting on it: "where is X", "find the X config",
  "is X installed", "is X on disk anywhere", "resolve X to a launch
  target", or any step that needs a concrete path. MiOS spans TWO
  filesystems -- the Linux side (FHS: /usr, /etc, /var, /home, flatpak
  app-ids, .desktop entries, $PATH) and the Windows side (/mnt/<drive>,
  the App Paths registry, the Start Menu, the NTFS file index). Each has
  a dedicated discovery TOOL; this skill maps intent to the right one.
  The NTFS index is the authoritative answer for "is X anywhere on a
  Windows drive" -- never fabricate a negative ("I searched Steam, not
  found") without actually querying it. The cardinal rule: discover by
  QUERYING the live system, not by assuming a path.
metadata:
  hermes:
    requires_tools:
      - find_file_fast
      - app_search
      - list_installed_apps
      - linux_file_search
      - windows_file_search
      - resolve_launch_command
      - launch_windows_app
      - verify_launch
      - knowledge_search
      - tool_search
---
<!-- AI-hint: Defines the logic for locating files and applications across Linux and Windows filesystems by mapping user intent to specific discovery tools (find_file_fast, app_search, list_installed_apps, linux_file_search, windows_file_search) to resolve concrete paths; the NTFS file index (windows_file_search / find_file_fast) is the authoritative answer for whether a file/app exists on any Windows drive, so never fabricate a negative without querying it.
     AI-related: /usr/share/mios/hermes/skills/find-files-and-apps/SKILL.md._, /usr/lib/mios/agent-pipe
     AI-functions: app_search, find_file_fast, windows_file_search, linux_file_search, list_installed_apps, resolve_launch_command, verify_launch -->

# Locating files & applications on MiOS

> _MiOS-managed: seeded from
> /usr/share/mios/hermes/skills/find-files-and-apps/SKILL.md._

## Cardinal rule

**Navigate the real filesystem; never guess a path or rely on a baked-in
name list.** Every "where is X / is X installed / find X" question has a
TOOL that queries the live environment. Call the tool, read the result,
act on the concrete path it returns. If you don't know which tool, start
with `find_file_fast` (it spans every drive) and narrow from there.

**Never fabricate a negative.** Before you say "X is not installed", "X
isn't on this system", or "I searched Steam/Epic/Program Files and found
nothing", you MUST have actually run the matching tool. The NTFS file
index (via `windows_file_search`, or `find_file_fast` for a cross-drive
sweep) covers every mounted Windows drive -- if a file genuinely isn't in
the index, it isn't on disk. "Not found" is only a valid answer AFTER a
real query came back empty.

## Pick the tool by what you're locating

| You want to find… | Tool | Covers |
| --- | --- | --- |
| **An app to launch** (any OS) | `app_search(query)` | flatpak ids, .desktop, $PATH CLIs, Windows Start-Menu/App-Paths, games, MiOS shims -- the unified resolver. Returns a runnable launch target. |
| **The full app inventory** | `list_installed_apps()` | Every installed app across Linux (flatpak + RPM .desktop) and Windows (Start Menu, games). Use to enumerate / disambiguate / inventory-check before a deeper file search. |
| **A FILE on the Linux side** | `linux_file_search(query, ext?, path?, type?)` | plocate/locate/find over the Linux FHS (/usr, /etc, /var, /home, flatpak exports). Fast, always available, fully offline. |
| **A FILE on the Windows side** | `windows_file_search(query, ext?)` | The NTFS file index across every mounted Windows drive (.exe, .lnk, save files, configs, anything). Authoritative for "is X on a Windows drive". (Index-gated: if it returns an unreachable error, fall back -- see below.) |
| **A FILE on EITHER side, fast** | `find_file_fast(query, ext?)` | Fast cross-drive find that spans the Linux FHS and the mounted Windows drives in one call. Use when you don't know which side a file is on. |
| **Prior knowledge / a skill / a memory** | `knowledge_search(query)` | The second-brain knowledge store (skills, knowledge, notes). For "have we solved this before", not for disk files. |

## NTFS / Windows file-search query syntax

`windows_file_search` (and the Windows half of `find_file_fast`) query the
live NTFS index -- they are never stale, so prefer them over any cached
inventory when an install might be new. Shape the `query` the same way you
would a fast file-index search:

| Want | How |
| --- | --- |
| Substring on the filename | plain `query` -- e.g. `windows_file_search(query="<name>")` |
| A specific extension | pass `ext` -- e.g. `windows_file_search(query="<name>", ext="exe")` |
| Executable or shortcut | `ext="exe"` then retry `ext="lnk"` (Start-Menu shortcuts often resolve when the bare .exe doesn't) |
| Narrow to a folder | include a path fragment in the `query` (e.g. a `steamapps` / `Program Files` substring) so only matches under that tree come back |
| Disambiguate many hits | enumerate with `list_installed_apps()` first, or surface the candidates and ask which |

When you don't know which drive (or which OS) the file is on, use
`find_file_fast(query, ext?)` -- one call across the Linux FHS and every
mounted Windows drive.

## Resolving a fuzzy app name to a concrete launch target

This is generative on BOTH OSes -- no hardcoded name→path table:

- **Linux**: the app is identified by its flatpak app-id or XDG .desktop
  id; the launch target comes from the .desktop `Exec` key (full path,
  else `$PATH`). `app_search` / `list_installed_apps` surface these from
  the live `flatpak list` + `/usr/share/applications` scan.
- **Windows**: a bare name resolves via the **App Paths registry**
  (`HKCU`/`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\<name>.exe`),
  then the System32 / Windows system dirs, then the Start Menu, then the
  NTFS file index. `resolve_launch_command` does this chain for you;
  `app_search` exposes it for resolve-without-launch.

So to OPEN something, you usually don't pre-resolve at all -- just call
`resolve_launch_command(name=...)` to get the concrete launch command,
then `launch_windows_app(...)` (see the `app-launch` skill) and confirm
with `verify_launch(...)`; the chain is generative. Pre-resolve with
`app_search` / `find_file_fast` only when you need the PATH itself (to
read/edit a file, pass an arg, or disambiguate).

### Canonical workflow when launching something whose path you don't know

When an app/game might be a fresh install that no cached inventory knows yet:

1. **Inventory check** -- `list_installed_apps()` / `app_search(query)` (fast; reads the resolver).
2. **If not surfaced, hit the live index** -- `windows_file_search(query, ext="exe")` then retry `ext="lnk"` (or `find_file_fast` if the OS is unknown). This is authoritative and never stale.
3. **Resolve the discovered target** -- `resolve_launch_command(name=...)` (or pass the discovered path).
4. **Verify it came up** -- `verify_launch(...)`.

## When a tool can't reach its index (graceful fallback)

- `windows_file_search` returns an unreachable error (the Windows file
  index isn't installed/running on this host, e.g. an incomplete install):
  **don't give up or fabricate a path.** Fall back to `linux_file_search`
  for anything under the Linux FHS, to `find_file_fast` for a fast
  cross-drive sweep, and to `app_search` / `list_installed_apps` (which
  also read the Start Menu + App Paths) for Windows apps. Tell the
  operator the Windows file index is unavailable if a Windows *file* (not
  app) genuinely can't be found another way.
- `find_file_fast` / `app_search` returns multiple candidates: surface
  them and ask which -- never guess one.
- A search returns nothing: **don't give up after one empty result.**
  Widen the query (drop the extension, use a shorter substring), try an
  alternate spelling (e.g. a `.drive` suffix vs the bare name), try the
  OTHER extension (`exe` ↔ `lnk`), try the OTHER OS's tool, then report
  honestly.
- You're unsure which discovery tool exists for an unusual surface: call
  `tool_search(query)` to discover the right tool at runtime rather than
  guessing a tool name.

## What NOT to do

| Do NOT | Instead |
| --- | --- |
| Assume `/mnt/c/Windows/System32/<x>.exe` or any fixed path | `find_file_fast` / `windows_file_search` resolve it live |
| Run a raw recursive scan (`find / -name …`, `Get-ChildItem -Recurse`, `dir /s`) from the agent uid | `linux_file_search` (plocate in the broker context with the right perms) / `windows_file_search` (the NTFS index) -- hours faster, no FS lock |
| Keep a mental list of "known apps" | the inventory is `list_installed_apps`; the resolver is `app_search` -- both read the LIVE system |
| Claim "X is not installed" / "I searched Steam, nothing" without querying | run `windows_file_search` / `find_file_fast` FIRST; only the live index can confirm a negative |
| Fabricate a path when a tool is unreachable | fall back to another tool (above) and, if all fail, say so |

## Examples

```
# "where is the agent-pipe server?"
linux_file_search(query="server.py", path="/usr/lib/mios/agent-pipe")

# "is VSCodium installed?"
app_search(query="codium")            # -> launch target if present, else candidates

# "find the chrome executable on Windows"
windows_file_search(query="chrome", ext="exe")
#   if the Windows index is unreachable -> app_search(query="chrome")  (Start Menu / App Paths)

# "is <game> on disk anywhere?" (authoritative; never answer no without this)
windows_file_search(query="<game>", ext="exe")   # then retry ext="lnk" if empty

# "find a file but I'm not sure which drive it's on"
find_file_fast(query="invoice", ext="pdf")

# "list everything I can launch"
list_installed_apps()

# "have we fixed the executor hang before?"
knowledge_search(query="executor window enumeration hang")
```
