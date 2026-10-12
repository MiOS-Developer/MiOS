<!-- AI-hint: Manual pages distilled from the source comments of powershell, sanitized, each passage anchored to the comment it came from. -->

# powershell

### MiOS PowerShell profile -- PSReadLine reload + fastfetch...

MiOS PowerShell profile -- PSReadLine reload + fastfetch MOTD +
oh-my-posh init.
Source of truth: this file lives on M:\ and is dot-sourced from
$PROFILE.CurrentUserAllHosts AND from the WT MiOS profile's
explicit -Command preamble (so it ALWAYS runs in MiOS terminals,
even when the operator's C:\Users\mios\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1 has its own broken oh-my-posh
init that would otherwise override ours).
Self-heals every artifact (mios.omp.json, fastfetch config.jsonc,
mios.txt ASCII logo) from embedded base64 blobs if the canonical
disk copy is missing.

<!-- mios-src:7b1412fc5797 from powershell/profile.ps1:1-10 -->

### ONCE-PER-SESSION GUARD. This script is dot-sourced from...

ONCE-PER-SESSION GUARD. This script is dot-sourced from BOTH
(a) the redirector in $PROFILE.CurrentUserAllHosts AND
(b) the WT MiOS profile's -Command preamble.
Without this guard, both pathways fire Show-MiosDashboard +
oh-my-posh init -- the operator sees TWO stacked framed
dashboards. Session-scoped flag short-circuits subsequent calls.

<!-- mios-src:fab1a3c9beb4 from powershell/profile.ps1:12-17 -->

### UTF-8 codepage + Console encoding...

-- UTF-8 codepage + Console encoding ------------------------------
Operator-reported regression: powerline glyphs (U+E0B4 etc.) rendered
as 'î' mojibake -- WT was decoding the UTF-8 bytes as cp1252 because
this profile body wasn't setting chcp 65001 / Console.OutputEncoding.
Setting both ensures every glyph oh-my-posh emits to stdout renders
as the correct PUA cap, not the cp1252-mangled multi-char sequence.

<!-- mios-src:de3b7e90f5cf from powershell/profile.ps1:28-33 -->

### Window resize + center (every MiOS pwsh)...

-- Window resize + center (every MiOS pwsh) --------------------
Dimensions sourced from mios.toml [terminal] (cols/rows/
scrollback_rows). Per feedback_mios_terminal_dimensions every
MiOS-spawned window opens at the configured size centered on
the active monitor. Apply BEFORE any output paints so the
operator never sees a default-sized window briefly before the
resize. Idempotent -- a second pass via the inner script
(Pass-2 elevation) is a no-op.

IMPORTANT GATE: only resize when we're actually in the MiOS APP
context (i.e. the WT MiOS profile launched us). Otherwise -- if a
child pwsh during BOOTSTRAP/INSTALL accidentally loads this profile
via $PROFILE.CurrentUserAllHosts redirector -- the resize shrinks
the operator's 80x40 install conhost down to the 80x20 MiOS-app
size mid-install. Operator-reported regression: "window changes to
the MiOS Global sizes of 80x20 somewhere in the middle of the
installations". $env:MIOS_APP_CONTEXT is set ONLY by the WT MiOS
profile commandline (see Install-MiOSTerminalProfile in Get-MiOS.ps1).

<!-- mios-src:221ebb182b8d from powershell/profile.ps1:39-56 -->

### Center on the ACTIVE display (where the cursor currently...

Center on the ACTIVE display (where the cursor currently is),
NOT PrimaryScreen. On multi-monitor hosts the operator launches
mios.bat from whichever monitor they're working on; the window
should land THERE.

<!-- mios-src:c51cf90c00bc from powershell/profile.ps1:82-85 -->

### NO TERMINAL-TYPE GATE. Always run the PSReadLine reload +...

NO TERMINAL-TYPE GATE. Always run the PSReadLine reload + oh-my-
posh init. The WT_SESSION gate on the previous version was
silently skipping the init when WT didn't set the env var early
enough -- producing the "theme works in normal terminal but not
MiOS Terminal" symptom. fastfetch is gated separately below
since its ASCII rendering only makes sense in a real terminal.

<!-- mios-src:f75af3f01364 from powershell/profile.ps1:94-99 -->

### Import terminal completion modules ------------------------...

-- Import terminal completion modules ------------------------
Silent best-effort: each module is imported if installed,
skipped if not. Operator gets icon-aware ls (Terminal-Icons),
git tab-completion (posh-git), AI-style prediction
(CompletionPredictor), and command-not-found suggestions
(Microsoft.WinGet.CommandNotFound).

<!-- mios-src:043085015bd9 from powershell/profile.ps1:102-107 -->

### PSReadLine reload -----------------------------------------...

-- PSReadLine reload -----------------------------------------
PowerShell 7.x ships with an in-box PSReadLine that's too old
for oh-my-posh init's Get-PSReadLineKeyHandler -Chord syntax.
Updating PSReadLine on disk (Install-Module) doesn't help the
CURRENT session because PSReadLine is autoloaded BEFORE the
profile runs. Force-import the newest installed version here
so oh-my-posh init's PSReadLine integration doesn't throw
"A positional parameter cannot be found that accepts argument
'Spacebar'/'Enter'/'Ctrl+c'".

<!-- mios-src:627f8a3882c9 from powershell/profile.ps1:114-122 -->

### Resolve / self-heal MiOS artifact paths -------------------...

-- Resolve / self-heal MiOS artifact paths -------------------
M:\-everywhere invariant (operator: "irm|iex sets up M:\
disk/partition installs EVERYTHING to M:\ EVERYTHING").
M:\ is created at install time and never removed at runtime;
if it's missing, the install never completed and the operator
needs to re-run irm|iex.  The profile body falls back to a
warn rather than silently splitting state across drives.

<!-- mios-src:0a628376c8eb from powershell/profile.ps1:131-137 -->

### Width adapts to LIVE terminal width every render so the...

Width adapts to LIVE terminal width every render so the dashboard
always renders edge-to-edge. "dashboards
should be edge to edge globally!! 80x20 window is the Global
benchmark!" + "opening MiOS app and using things like fastfetch
and btop--things that clear the screen; ends up fitting the
dashboards in the same original window and tab--eventually".

First-render timing: at session start, WT hasn't settled the
cell count yet. Solution: poll WindowWidth up to 5x with a
50ms gap until it stabilizes (two consecutive reads agree),
then use the stable value. After fastfetch/btop run, WT has
fully settled and subsequent renders read correctly.

<!-- mios-src:59fe70446c90 from powershell/profile.ps1:176-187 -->

### Cap to mios.toml [terminal].frame_width (SSOT). WT's...

Cap to mios.toml [terminal].frame_width (SSOT). WT's
WindowWidth poll is unreliable during the first ~200ms after
spawn -- it can return a value 4-8 cells wider than the
final viewport (focus-mode + acrylic backdrop allocation
haven't settled). Without this cap, host_os/CPU/font lines
render at the inflated WIDTH, then WT re-sizes the buffer
narrower, and every overflowing line wraps -- pushing the
top frame off-viewport. Capping to the toml value (the
operator-declared "this is what 80x20 means") guarantees
the dashboard never renders wider than the declared frame.
Operator-flagged "ie..." / "on..." wraps in
MiOS-WIN dashboard with top frame clipped off-screen.

<!-- mios-src:ba428f47444e from powershell/profile.ps1:202-213 -->

### Uniform frame color -- per "make the entire frame 1 uniform...

Uniform frame color -- per "make the
entire frame 1 uniform colour--make it a complimenting colour
to the windows colour that's sourced from the toml fields that
are relevant to MiOS's color palette colours". MiOS canonical
accent (mios.toml [colors].accent + [branding.dashboard].frame_color)
is operator-blue (#1A407F = ANSI 34 = [ConsoleColor]::Blue).
Embed ANSI 34 around every $V border so the per-content rows
render their borders in the SAME color as the standalone
top/divider/bottom Write-Host calls (which use
-ForegroundColor Blue). Without this, _Frame/_Center returned
a plain string that Write-Host emitted in the inherited
foreground (often cream from the MiOS scheme), making per-row
borders visually different from top/divider/bottom borders.

<!-- mios-src:29d949f5a32b from powershell/profile.ps1:226-238 -->

### Total budget

Total budget: frame_height rows total. Layout:
  1 top frame
  logo block       (compact: 0-1 row -- title only;
                    full:    N-row ASCII when budget allows)
  1 divider
  fastfetch block  (paired -- two modules per row)
  1 divider
  hints block      (compact: 1 line; full: 1-line-per-verb)
  1 bottom frame
Per operator: dashboard MUST fit in 80x20 (= frame_height 19).
Compact mode kicks in when frame_height < 25.
-Full (the `mios dash` view) forces the banner/logo full layout to
match the Linux `mios dash`. Otherwise compact when the window is too
short to fit the logo + every section (the 80x20 `mios mini`). The
previous `19 -lt 25` was a hardcoded literal that was ALWAYS true, so
the full framed view was unreachable -- read the live window height.

<!-- mios-src:a433c16253ef from powershell/profile.ps1:264-279 -->

### 1-line title band -- resolves through mios.toml...

1-line title band -- resolves through mios.toml [dashboard].title
at runtime so the configurator HTML edits flow through to the
next render.  Vendor default is the technical descriptor
("MiOS  --  Immutable Fedora AI Workstation"); operators who
want the friendly "My Personal Operating System" face on the
dashboard subtitle override [dashboard].title via mios.html.

<!-- mios-src:ef6ccf97690d from powershell/profile.ps1:309-314 -->

### Centered ASCII logo (operator-blue). Center the BLOCK (not...

Centered ASCII logo (operator-blue). Center the BLOCK (not
each line individually) -- the logo's internal alignment
depends on each line's leading whitespace.
Skip the AI-tagging `#` header lines the logo file carries (the
Linux dashboard skips ^# too) so the banner -- not a comment --
renders.

<!-- mios-src:ccd66eab1f09 from powershell/profile.ps1:323-328 -->

### Compact metric rows ---------------------------------...

-- Compact metric rows ---------------------------------
Driven by mios.toml [dashboard].rows -- side-by-side fields
per row keep the dashboard at ~5 metric rows so 80x20 leaves
ample room for the prompt and command output.  Per operator
"the dash is set GLOBALLY to Windows and Linux
dashboards!! same settings!!! ... smaller metric can be
side-by-side in the dash; freeing up more room for the
prompt field."  The Linux-side mios-dashboard.sh reads the
same [dashboard] section.

Field renderers fetch values via Get-CimInstance (single-
cached) / Get-Volume / $PSVersionTable.  They each return a
short labeled string ("CPU AMD Ryzen 9 9950X3D 5.75GHz (32c)").
Unknown field-keys are silently skipped so the dashboard
is forward-compatible with future mios.toml additions.

<!-- mios-src:8f7a3eee484f from powershell/profile.ps1:350-364 -->

### Compact OS caption

Compact OS caption: strip Microsoft prefix, the
"for Workstations" SKU suffix, "Insider Preview"
marketing, "(64-bit)" arch (it's redundant -- the
arch line covers it), and trailing whitespace.
Operator-flagged "Windows 11 Pro for
Workstations Insider Preview" overflowed the 80x20
frame and wrapped, pushing the top frame off-screen.

<!-- mios-src:9836fb8614e9 from powershell/profile.ps1:374-380 -->

### PowerShell switch with regex condition matches but does NOT...

PowerShell switch with regex condition matches but
does NOT reliably populate $Matches in the action
block scope -- saw disk_c : err
in the dashboard because $Matches[1] was \ and
$_dl came back empty.  Parse the letter from $_
directly via Substring instead.

<!-- mios-src:1e8e536a6c03 from powershell/profile.ps1:437-442 -->

### Try/catch per-field so a single broken renderer (e.g....

Try/catch per-field so a single broken renderer
(e.g. Get-Volume not available, lspci missing) doesn't
kill the whole loop -- saw the
dashboard render only the first 3 rows and bail because
the disk_c renderer's Get-Volume call raised in a
context where the Storage module wasn't loaded.

<!-- mios-src:a61fe3580b59 from powershell/profile.ps1:518-523 -->

### MiOS services block ----------------------------------...

-- MiOS services block ----------------------------------
Resolve the dev distro ONCE, and only if it is ALREADY RUNNING, via a
fast `wsl --list --running` check (WSL_UTF8 so the names aren't UTF-16
with embedded NULs). This never cold-boots a stopped distro -- the
service + dev-shell bridges below reuse $_devDistro, so on every
terminal spawn a stopped/absent MiOS-DEV degrades open INSTANTLY
instead of blocking the prompt on a login-shell that triggers a boot.

<!-- mios-src:932815e2e5de from powershell/profile.ps1:545-551 -->

### Live UNIFIED service table -- bridged from the ONE Linux...

Live UNIFIED service table -- bridged from the ONE Linux renderer
(mios-dashboard.sh --table-only/--endpoints-only) via wsl, so BOTH
dashboards show the SAME live services (pods/containers/host units +
SSOT ports) from a single source: no hardcoded service/port list, no
drift. Full `mios dash` -> fuller UNIFIED table; compact `mios mini`
(80x20) -> compact endpoint table, exactly as the Linux dash vs mini.

<!-- mios-src:32f94afe7ae3 from powershell/profile.ps1:565-570 -->

### Command hints rows ----------------------------------- Verb...

-- Command hints rows -----------------------------------
Verb list resolves through mios.toml [verbs] at RUNTIME (SSOT).
The dashboard re-reads on every render so an operator edit via
mios.html flows mios.toml -> dashboard immediately. No hard-
coding here. Vendor fallback only if every TOML candidate is
missing (cold first-run before M:\ overlay is staged).

<!-- mios-src:b7ac6bb2026b from powershell/profile.ps1:586-591 -->

### LIVE, copy-pasteable "SSH from this Windows host into the...

LIVE, copy-pasteable "SSH from this Windows host into the code-server
dev container at the MiOS root tree". Sourced from the SAME SSOT
helper the Linux dashboard uses (mios-ssh-dev-cmd), run inside the dev
distro via a LOGIN shell so it sees the rootful podman -- so the two
dashboards never drift. Printed UNFRAMED below the box so the long
command is never truncated and stays copyable in full. The distro is
probed from the same candidate list the rest of this profile uses.

<!-- mios-src:8a5aefeb3a43 from powershell/profile.ps1:638-644 -->

### NO inline-render here. The profile body is a thin function-...

NO inline-render here. The profile body is a thin function-
definition layer; the "what shows up on terminal spawn" is
whatever verb mios.toml [terminal.startup].windows points at.
The dispatch fires AT THE END of this profile (after the mios
verb function is defined). See the [terminal.startup] block
below the function definitions.
"have the bash and pwsh/WT environment/
dotfile(s) automatically run mios dash on open/launch--NOT
PRINT ON LAUNCH!!! THE ACTUAL ENV/DOTFILE(S) SHOULD DICTATE THE
COMMANDS/VERBS AND WHATS RUN ON CONSOLE SPAWN(ALL PLATFORMS
GLOBALLY)--ALL SOURCED FROM THE MIOS.TOML"

<!-- mios-src:d404eb8419ef from powershell/profile.ps1:657-667 -->

### oh-my-posh init -------------------------------------------...

-- oh-my-posh init -------------------------------------------
Capture the init script output, then regex-patch the broken
positional Get-PSReadLineKeyHandler calls. Older oh-my-posh
versions emit Get-PSReadLineKeyHandler Spacebar etc. -- which
NO PSReadLine version accepts (the cmdlet's parameter binder
has no positional [string]). Latest oh-my-posh emits -Chord
<key>. We inject -Chord even when running latest, since it's
idempotent (latest already has it). This makes oh-my-posh's
PSReadLine integration work regardless of installed version.

<!-- mios-src:44ae3bbe79f8 from powershell/profile.ps1:669-677 -->

### Shell-aware

Shell-aware: oh-my-posh init pwsh emits PS 7+ syntax that
FAILS silently in Windows PowerShell 5.1, leaving the
operator's pre-existing broken init showing "CONFIG NOT
FOUND". Detect PS edition and use the matching arg
(powershell for 5.1 / Desktop, pwsh for 7+ / Core).

<!-- mios-src:ae7a4192342e from powershell/profile.ps1:679-683 -->

### MiOS commands...

-- MiOS commands ---------------------------------------------------
Defined in EVERY pwsh session (not gated on WT_SESSION) so the
operator can run mios-build / mios-update / mios-help from any shell.
Each command fetches its target script fresh from
raw.githubusercontent.com so the operator doesn't have to manually
pull the mios-bootstrap repo. Cache-busting via ?cb=<unix-time>
defeats Fastly's 5-minute max-age.

<!-- mios-src:abd471681485 from powershell/profile.ps1:697-703 -->

### New flow (per operator: "mios build should queue the build...

New flow (per operator: "mios build should queue the build, launch
the html file in the local windows browser window, fetch the newly
minted html/toml files to the overlay >> start the build with new
key steps implemented"):

  1. Open mios-config.html in the default Windows browser so the
     operator can edit theming / functionality / package lists.
  2. Wait for the operator to save + close the configurator (or
     hit Enter to skip the edit pass).
  3. mios-pull to sync M:\ overlay to origin/main + apply user edits.
  4. Run build-mios.ps1 -BuildOnly so it skips the bootstrap phase
     and goes straight into the OCI build inside MiOS-DEV.

Bypass the configurator pass with: mios build -SkipConfig
Bypass the pull pass        with: mios build -SkipPull

<!-- mios-src:3d9f11995dde from powershell/profile.ps1:710-724 -->

### Capture mtime BEFORE opening so we can tell if the operator...

Capture mtime BEFORE opening so we can tell if the operator
actually saved a new copy (the browser saves to Downloads
because file:// URLs can't write back to source). Used by
the promote step below.

<!-- mios-src:9747fca8084a from powershell/profile.ps1:737-740 -->

### Step 2

-- Step 2: promote downloaded mios.toml from Downloads ----
The browser saves to %USERPROFILE%\Downloads (file:// URLs
can't write back to source). Scan for any mios*.toml /
*mios*.html newer than the in-place overlay copies and
PROMOTE them to M:\etc\mios\ + M:\usr\share\mios\configurator\.
Also archive the imported source so we don't double-promote
on the next mios-build run.

<!-- mios-src:233519a7a866 from powershell/profile.ps1:756-762 -->

### Archive the source so a re-run of mios build doesn't...

Archive the source so a re-run of mios build doesn't
re-promote the same file. Keep it (don't delete) so
the operator can recover if something went wrong.

<!-- mios-src:70c617394dd5 from powershell/profile.ps1:787-789 -->

### Step 3

-- Step 3: sync overlay so the build sees the latest mios.toml -
Note: this runs AFTER the Downloads-promote step so mios-pull
sees the just-promoted files in M:\etc\mios. mios-pull's git
reset --hard would otherwise blow away the operator's changes
if they lived in the tracked tree.

<!-- mios-src:db73e315cd01 from powershell/profile.ps1:817-821 -->

### Unified mios <verb> dispatcher. Operator types mios build...

Unified mios <verb> dispatcher. Operator types mios build or
mios b<TAB> (PSReadLine + the ArgumentCompleter below complete to
mios build). Falls through to mios-<verb> so the same wrappers
back both call shapes.
Known verbs dispatch to mios-<verb>.ps1 wrappers in $Global:MiosBin.
Anything that isn't a known verb is routed to Hermes-Agent at
MIOS_AI_ENDPOINT as a chat completion, so mios how do I bootc switch
works from any PowerShell terminal without a separate sk verb.

<!-- mios-src:bc24de58f418 from powershell/profile.ps1:953-960 -->

### $Global:MiosBin may be unset (this profile is dot-sourced...

$Global:MiosBin may be unset (this profile is dot-sourced standalone from
the $PROFILE redirector). Guard it -- Join-Path throws on a null Path,
which would surface a raw binder error instead of the friendly hint below.

<!-- mios-src:40082a92105e from powershell/profile.ps1:983-985 -->

### Interactive-shell startup verb (SSOT: mios.toml...

-- Interactive-shell startup verb (SSOT: mios.toml [terminal.startup]) --
The profile body above is JUST function definitions. What runs on
terminal spawn is the verb declared in mios.toml -- read fresh
every shell launch so HTML configurator edits flow through with
zero re-bake. Vendor default is "dash" but the operator can flip
to any other verb (or "" for a silent shell).

Per-platform key precedence: [terminal.startup].windows wins over
[terminal.startup].verb (the cross-platform default). The Linux
bash side reads the same TOML keys (.linux > .verb).

Guards:
  - $env:MIOS_SKIP_MOTD = "1"      -> no startup verb fires.
  - non-interactive host           -> no fire (background scripts,
                                      VS Code's PowerShell extension
                                      integrated terminal, etc.).
  - $Global:MiosStartupVerbFired   -> idempotent across re-sources
                                      (mios.ps1 dot-sources this
                                      profile to load functions, we
                                      don't want a recursive verb
                                      call inside an already-running
                                      verb).

<!-- mios-src:f5433fb6041d from powershell/profile.ps1:1005-1026 -->

### Vendor fallback

Vendor fallback: mini (the compact 80x20 framed banner).
dash is the FULL render -- ASCII banner + service status +
extended sys specs -- explicitly invoked by the operator,
not auto-fired on every shell spawn.

<!-- mios-src:2204be398c65 from powershell/profile.ps1:1048-1051 -->

### MiOS WindowWidth diagnostic (auto-appended by...

-- MiOS WindowWidth diagnostic (auto-appended by Install-MiOSPowerShellProfile) --
Every MiOS pwsh launch appends one line to M:\MiOS\diagnostics\window-width.txt
capturing [Console]::WindowWidth + BufferWidth + WT_SESSION + timestamp.
This is the SOURCE OF TRUTH for the actual visible cell count on the
operator's hardware -- if WindowWidth != mios.toml [terminal].cols, the
delta is the WT chrome budget that right_margin must absorb.

<!-- mios-src:b38ecb3d63ba from powershell/profile.ps1:1062-1067 -->
### Pester can only mock a command that resolves. wsl.exe...

Pester can only mock a command that resolves. wsl.exe exists only on
Windows and podman only where it is installed, while this suite also
runs under pwsh on the Linux CI runner. Stand-ins make both resolvable
everywhere, and throw so a path a test forgot to mock fails instead of
reaching a real builder.

<!-- mios-src:7a6694e0e981 from tests/powershell/NativeBuildLifecycle.Tests.ps1:12-16 -->

### The projection takes its shell as a parameter and uses only...

The projection takes its shell as a parameter and uses only
CreateShortcut (load an existing link, or a blank one with
WindowStyle 1) and Save. Windows exercises the real WScript.Shell;
pwsh on the Linux CI runner has no COM, so it gets a stand-in with the
same surface that persists those properties to the link file. Both
write only on Save, so the hash and timestamp assertions below still
prove an identical projection is left untouched.

<!-- mios-src:ffa80b284b18 from tests/powershell/VerifiedInstaller.Tests.ps1:101-107 -->
