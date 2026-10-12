<!-- AI-hint: Manual pages distilled from the source comments of root, sanitized, each passage anchored to the comment it came from. -->

# root

### The canonical Windows-entry working tree per...

The canonical Windows-entry working tree per
feedback_mios_entry_m_drive_clone.md: M:\MiOS\repo\mios-bootstrap.
M:\ is provisioned to EXACTLY 256 GB by Initialize-MiosDataDisk
below. The previous %TEMP%-with-GUID approach (commit 88a0de3)
was a stopgap; M:\ is the canonical answer because the build's
downstream artifacts (OCI layers, WSL2 .tar/.vhdx, Hyper-V vhdx,
qcow2, ISO, RAW) easily exceed 50 GB and need a dedicated
data partition.

<!-- mios-src:220c3f685253 from Get-MiOS.ps1:70-77 -->

### For non-Default (build/flash/sync) actions invoked via...

For non-Default (build/flash/sync) actions invoked via `irm|iex` on a BARE Windows, the
mios-bootstrap repo isn't cloned yet -- the Default bootstrap clones it, but these actions run
FIRST (this router precedes the bootstrap). Fetch it here (git if present, else a GitHub zip)
so a factory Windows can go straight from the web one-liner to a build/flash with no manual clone.

<!-- mios-src:e1b07f8cfd55 from Get-MiOS.ps1:111-114 -->

### 4. Launch the canonical MiOS-Cat launcher. It self-elevates...

4. Launch the canonical MiOS-Cat launcher. It self-elevates via UAC, so
it ends up running as the machine Administrator -- which on a provisioned
MiOS host is the SSOT-named MiOS AI admin account (the renamed built-in
Administrator; [autounattend.service].svc_user, default 'mios-sudo').
We no longer hardcode a 'MIOS\Administrator' scheduled-task principal:
the hostname AND the admin-account name are operator-defined via SSOT, so
a fixed 'MIOS\Administrator' was wrong on every box but this dev machine.

<!-- mios-src:209802d65f28 from Get-MiOS.ps1:204-210 -->

### Self-cache-bust on entry...

-- Self-cache-bust on entry ------------------------------------------------
raw.githubusercontent.com is fronted by Fastly with `Cache-Control: max-age=300`,
so the canonical Run-dialog paste:
  powershell -ExecutionPolicy Bypass -Command "irm https://...Get-MiOS.ps1 | iex"
returns the 5-min-old cached copy after a push. Operators who test in tight
iteration cycles end up running stale code without realizing it.

Fix: every cached copy of this script self-relaunches with a `?cb=<unix-time>`
query string on first entry. Fastly treats unique URLs as distinct cache
keys, so the busted URL always pulls origin-fresh. The `MIOS_CACHE_BUSTED`
sentinel breaks the loop on the second pass (the freshly-fetched copy
doesn't re-relaunch). Once this prefix is deployed, ALL future pushes
land fresh on the next canonical-one-liner paste -- the only run that
pays the stale-cache cost is the very first one after this prefix is
itself deployed (the cached version pre-dates the prefix).
-- Resize + center the OUTER WinR pwsh window before anything paints ------
At irm|iex entry the operator's WinR-spawned pwsh defaults to 120x30
(or whatever their conhost default is). Resize to 80x40 (the
[terminal.install] default) and center on the cursor's active monitor
so the readme/acknowledgements + cache-bust banner are centered and
fit without wrap.

RESIZE ORDER MATTERS: SetWindowSize requires buffer >= window. If
current buffer < target cols, SetWindowSize fails. If current window
> target cols, SetBufferSize fails. Branch on current width.

<!-- mios-src:d7580dd3ed93 from Get-MiOS.ps1:266-290 -->

### Cleanup of stale legacy profile body BEFORE anything else...

-- Cleanup of stale legacy profile body BEFORE anything else ----------------
Earlier failed runs may have left a corrupted, mojibake'd profile.ps1 at
the legacy fallback path %USERPROFILE%\MiOS-bootstrap\powershell\. The
OUTER WinR pwsh dot-sources $PROFILE.CurrentUserAllHosts (the redirector)
at startup, BEFORE our script runs -- if the redirector's target file
has bad UTF-8 bytes, the parse error fires every time the operator pastes
the irm|iex one-liner. We can't suppress that startup load (it happened
before we got control), but we CAN delete the bad file here so it doesn't
fire AGAIN on subsequent runs. The canonical profile location is M:\MiOS\
powershell\profile.ps1 (written by Pass-1 with UTF-8 BOM); the
%USERPROFILE%\MiOS-bootstrap\ tree is purely a stale fallback artifact.

<!-- mios-src:9ade315a9d7c from Get-MiOS.ps1:332-342 -->

### Acknowledgement gate (full scrollable form -- inlined...

Acknowledgement gate (full scrollable form -- inlined because this
script runs via 'irm | iex' where $PSScriptRoot is empty so we cannot
dot-source automation/lib/agreements-banner.ps1 from a clone.

Skip paths:
  $env:MIOS_AGREEMENT_BANNER in (quiet|silent|off|0|false)  -- silent skip
  $env:MIOS_AGREEMENT_ACK   = 'accepted'                    -- declared accept (CI)
  $env:MIOS_GETMIOS_RELAUNCHED = '1'                        -- inner call inherits the outer's accept

On 'No thanks' or any non-accept reply we exit 78 (EX_CONFIG) before
any clone, fetch, or elevation -- nothing on disk is mutated.

<!-- mios-src:f87d2cc14bc4 from Get-MiOS.ps1:388-398 -->

### mios.toml reader (Get-MiOS.ps1 = ALWAYS web-only)...

-- mios.toml reader (Get-MiOS.ps1 = ALWAYS web-only) -------------------------
mios.toml is THE global dotfile (per feedback_mios_toml_html_global_dotfile
memory). EVERY tunable -- window dims, M:\ size, font, AumID, retry
delays, theming, package lists -- sources from here. The HTML
configurator edits mios.toml; every consumer reads from it.

This file (Get-MiOS.ps1) is the BOOTSTRAP entry -- invoked via
`irm | iex` for clean installs and via `mios update` for forced
refresh. Per operator architectural rule

  "ORIGIN = web entries/repos only -- no fallback to M:\ or
   anywhere else -- unless origin has been pulled and it's a
   simple 'mios build' -- that can pull from M:\ as it'd already
   exist -- then 'mios update' would ALWAYS pull from web
   regardless of clean entry, updating, etc-etc!!!"

Get-MiOS.ps1 is BOTH the clean entry AND what mios update re-runs,
so EVERY read here is web-only.  M:\ overlays / ~/.config user
overrides are honored by build-mios.ps1's `mios build` flow (which
is downstream of mios-pull and assumes M:\ is current), NOT by the
bootstrap itself.  Mixing the two would let a stale M:\ silently
override a web fetch, defeating the "clean entry forces refresh"
guarantee.

Vendor defaults are sufficient (per feedback_mios_defaults_baseline):
the stack works with no user toml present. Get-MiosTomlValue returns
its `-Default` arg if the key is missing anywhere.
Import MiOS.Install sub-modules

<!-- mios-src:4b52f4d9b404 from Get-MiOS.ps1:400-427 -->

### Return without unary-comma wrapper -- callers collect via...

Return without unary-comma wrapper -- callers collect via
@(Get-MiosTomlValue ...) which collects the pipeline-
unrolled int sequence into a fresh array. With the
unary-comma wrapper, @() got @(@(0,5,15,30)) -- a 1-
element array containing the int array -- and
$delays[0] = @(0,5,15,30) blew up Start-Sleep -Seconds.

<!-- mios-src:065f55427a08 from Get-MiOS.ps1:526-531 -->

### Default to string -- strip the SURROUNDING TOML string...

Default to string -- strip the SURROUNDING TOML string quotes (and
unescape backslash sequences for double-quoted strings). The
previous Trim('"',"'") was too aggressive: a value like
    "'MiOS' v0.2.4"
had its leading apostrophe stripped because Trim treats the char
set as a multi-set on BOTH ends. Operator-reported regression:
the installer banner rendered as `MiOS' v0.2.4` (missing leading
`'`) instead of `'MiOS' v0.2.4`.

<!-- mios-src:0112f387b3fb from Get-MiOS.ps1:538-545 -->

### Basic string

Basic string: strip and unescape \\, \", \n, \t, \r.
Sentinel uses [char]0x01 (literal SOH byte) instead of the
PS 7-only `` `u{0001} `` syntax -- PS 5.1 treats `` `u ``
as just literal "u", which leaked the placeholder
`u{0001}BS` (visible) into rendered strings.  Operator
"Initializing mios.git as the M:u{0001}BSu{0001}
working tree".  [char]0x01 works in both PS 5.1 and PS 7+.

<!-- mios-src:597bdd1cbe76 from Get-MiOS.ps1:549-555 -->

### Canonical origin URLs (SSOT: [bootstrap] mios_repo /...

-- Canonical origin URLs (SSOT: [bootstrap] mios_repo / bootstrap_repo) ------
ONE source for every web fetch below. Resolved once from mios.toml so an
operator override of the repo owner/name/ref flows to all download sites; the
vendor defaults match the [bootstrap] keys. Raw-content bases are DERIVED from
the .git clone URLs (github.com host -> raw.githubusercontent.com, drop the
trailing .git, append the ref) so the owner/name live in exactly one place.
Script-scoped + assigned AFTER Get-MiosTomlValue is defined; the fetch
functions above resolve these at call time (which is always later). The two
root chicken-egg fetches that pull mios.toml / Get-MiOS.ps1 itself keep their
inline literal -- they run before any toml exists and ARE the documented
vendor default these vars fall back to.

<!-- mios-src:3b2ebfee02b9 from Get-MiOS.ps1:575-585 -->

### Framed branded ASCII banner -- shown at the top of EVERY...

Framed branded ASCII banner -- shown at the top of EVERY MiOS
window/dashboard per operator: "EVERY WINDOW SHOULD HAVE A FRAMED
AND BRANDED BANNER OF THE MIOS ASCII BANNER ART -- EVERY WINDOW
AND/OR DASHBOARD HAS IT AT THE TOP".
Width = 80 cells (frame char to frame char). Inner width = 78.
The ASCII art block + subtitle are CENTERED within the inner
width as a single block (same approach as Show-MiosDashboard) --
not line-by-line, so the art's internal diagonal alignment is
preserved while the whole logo sits visually centered.
Box-drawing requires UTF-8 codepage (chcp 65001) -- conhost in
CP437/CP1252 mangles ++++|- to `?`. Callers must set codepage
before invoking; the agreement gate + Pass-2 inner cmd both do.

<!-- mios-src:2b0184139248 from Get-MiOS.ps1:602-613 -->

### Width

Width: cols - right_margin - 2 frame chars. SSOT from mios.toml.
Operator reported "framing too wide STILL" at the previous hard-
coded inner=78 (total=80) -- that totaled the entire 80-col
terminal width with no slack, and WT's pseudo-console
over-reports by 1 cell during the first paint, so the right
frame char wrapped. inner = cols - right_margin - 2 always
leaves right_margin cells of slack on the right edge.

<!-- mios-src:36c2e403f349 from Get-MiOS.ps1:629-635 -->

### "dashboards should be edge to edge globally!! 80x20 window...

"dashboards should be edge to edge globally!!
80x20 window is the Global benchmark!". right_margin=0 means the
frame paints col 1..N where N = WindowWidth, edge-to-edge.
Canonical launches use mios-launch.exe with --focus so WT runs in
true 80x20 cells with no chrome reservation. Non-focus launches
(operator opens WT profile directly) have chrome that eats cells
-- in those cases the operator can override right_margin via
mios.toml [terminal].right_margin.

<!-- mios-src:2927a88cbcc0 from Get-MiOS.ps1:637-644 -->

### PS 5.1 (Windows PowerShell -- the ONLY shell on a fresh...

PS 5.1 (Windows PowerShell -- the ONLY shell on a fresh Windows) does
NOT define [char] * [int]: it throws "the operation '[System.Char] *
[System.Int32]' is not defined" and kills the whole elevated bootstrap
before the agreement gate can even render. pwsh 7 silently promotes the
char to a string and repeats it; 5.1 does not. Cast to a string FIRST so
the horizontal rule repeats identically on both shells. (char + string
concatenation IS fine in 5.1 -- only the multiply was undefined.)
install-robustness.

<!-- mios-src:fa3f1731f18d from Get-MiOS.ps1:655-662 -->

### Note

Note: gate IS rendered in the elevated relaunch (Pass-2). Pass-1
(the small black box from `irm|iex`) self-elevates and exits
BEFORE this function is ever invoked -- the agreement belongs in
the properly-sized 80x40 Pass-2 conhost. The previous behaviour
short-circuited Pass-2 via $env:MIOS_GETMIOS_RELAUNCHED, which
caused the agreement to be rendered in Pass-1's tiny inherited
conhost (~80x25) where the ~104-line summary scrolled past in a
flash and the operator only saw the bottom prompt.

<!-- mios-src:3c5769a46ff8 from Get-MiOS.ps1:778-785 -->

### Ensure the conhost is 80 cells wide BEFORE rendering. Use...

Ensure the conhost is 80 cells wide BEFORE rendering. Use the same
branching SetBufferSize/SetWindowSize pattern as the WinR-entry
resize at the top of this script (lines 105-115): the order matters
because the Win32 console rule is `buffer.cols >= window.cols`.
DON'T call MoveWindow with hardcoded pixel dimensions: at 150-200%
DPI, conhost cells are ~16-25 px so a hardcoded 820 px window only
fits 33-50 cells visible while the buffer stays 80 wide -- conhost
adds a horizontal scrollbar and the operator sees what looks like a
20x40 window. Letting SetWindowSize drive the Win32 window size
auto-pixel-sizes correctly at any DPI.

<!-- mios-src:56ffc4814e0c from Get-MiOS.ps1:787-796 -->

### Don't clamp by LargestWindowSize: at 200% DPI it can return...

Don't clamp by LargestWindowSize: at 200% DPI it can return as
low as 20 rows on a 1080p monitor, which produced the operator-
reported regression "window opens but is 1/2 the size it should
be". 80x40 is the documented [terminal.install] target -- if
conhost can't fit it visibly the worst case is silent fallback
to LargestWindowSize anyway, but most setups handle it fine.

<!-- mios-src:443d4fc9b954 from Get-MiOS.ps1:800-805 -->

### Win32 helpers for re-centering on every page refresh....

Win32 helpers for re-centering on every page refresh. Operator-
reported regression: "window respawns slightly off-center every
time it refreshes the window". Conhost doesn't move the Win32
window on Clear-Host, but tiny size renegotiations (font cache /
DPI re-resolve when the active monitor changes) drift it. We
snapshot the active monitor once and re-center on every page.

<!-- mios-src:c51fce9739ae from Get-MiOS.ps1:816-821 -->

### Capture the operator's active monitor + the FROZEN target...

Capture the operator's active monitor + the FROZEN target pixel
rect ONCE at gate entry. Reading current dims via GetWindowRect on
every page lets conhost's tiny per-render renegotiations drift the
window a few pixels each time -- the operator-reported "final
agreements window still ends up off-centered". Pinning to a frozen
target X,Y,W,H on every MoveWindow is a no-op when the window is
already there, and a snap-back when conhost has drifted.

<!-- mios-src:ef1cf89c72d2 from Get-MiOS.ps1:838-844 -->

### Resolve the topmost-ancestor HWND of the conhost: WT main...

Resolve the topmost-ancestor HWND of the conhost: WT main window
when WT is the default terminal app (Windows 11 22H2+), conhost
itself otherwise. Stored once so every per-page _Center call
targets the same window. Operator-reported regression: "all
windows aren't recentering still!" was caused by GetConsoleWindow
returning the OpenConsole pseudo-host HWND (NOT WT's) -- moving
the pseudo-host had no visible effect because WT owns the actual
window.

<!-- mios-src:3e7941ddecbc from Get-MiOS.ps1:850-857 -->

### AUTO-PAGINATE so the banner ALWAYS stays visible at the top...

AUTO-PAGINATE so the banner ALWAYS stays visible at the top of
the window. Operator-reported regression: previous two-page split
had page 1 = 53 lines but the conhost only shows 40 rows, so the
banner auto-scrolled off the top before the prompt rendered. The
operator had to scroll up to see the banner -- which violated
"EVERY WINDOW HAS THE BANNER AT THE TOP".

Strategy: render the banner first, then pack as many content lines
as fit in (window_rows - banner_rows - prompt_rows - margin) before
pausing. Repeat until the agreement body is exhausted, then enter
the Acknowledged prompt loop on the final page.

<!-- mios-src:70e295c69c04 from Get-MiOS.ps1:893-903 -->

### Re-center the conhost window on the OPERATOR'S active...

Re-center the conhost window on the OPERATOR'S active monitor
captured at gate entry. Without this, conhost drifts a few
pixels per Clear-Host (font cache / DPI renegotiation).

<!-- mios-src:2940a5f389a2 from Get-MiOS.ps1:954-956 -->

### 1. ALWAYS spawn a fresh elevated pwsh window. The original...

1. ALWAYS spawn a fresh elevated pwsh window. The original `irm | iex`
host inherits whatever terminal called us (VS Code integrated, remote
session, embedded host, etc.) which often (a) isn't admin, (b) is the
wrong size for the build, and (c) breaks console cursor positioning.
A fresh top-level pwsh window guarantees a clean, properly-sized
environment regardless of where the curl was run from.

-- Auto-elevate at script entry (single UAC) -----------------------
Per operator: "irm|iex mios.bat Win + R entry should it itself auto
elevate!!! it needs admin rights to install some components without
several UAC prompts interrupting the install".

Previously this script split work into Pass-1 (user) + Pass-2 (admin
via mid-install UAC). That meant operator saw the UAC prompt halfway
through; some Pass-2 steps (M:\ partition shrink, Podman Desktop
winget install, podman machine init) need elevation, so the prompt
was unavoidable -- but firing it at the start instead means ONE
UAC interaction up-front and the entire install runs in the same
elevated session.

Sentinel: $env:MIOS_GETMIOS_RELAUNCHED prevents the elevated relaunch
from re-elevating in an infinite loop.

<!-- mios-src:094710407b2e from Get-MiOS.ps1:985-1006 -->

### Pass-1 -> Pass-2 UAC handoff prompt strings resolve through...

Pass-1 -> Pass-2 UAC handoff prompt strings resolve through
mios.toml [messages.elevation] (SSOT).  Operator rebrands via
mios.html.  Vendor defaults below are the cold-fallback set
when no toml is reachable yet (this runs BEFORE M:\ overlay
exists on first install).

<!-- mios-src:a2fb7a0a0108 from Get-MiOS.ps1:1011-1015 -->

### Capture cursor position BEFORE the UAC prompt, while the...

Capture cursor position BEFORE the UAC prompt, while the operator's
attention is still on whichever monitor they pasted from. By the
time the inner script runs (after UAC accept), Cursor.Position is
at the UAC "Yes" button location -- typically the primary monitor,
NOT necessarily where the operator was working. Embed the captured
X,Y as constants in the inner cmd so Screen.FromPoint() resolves
to the active-display before-elevation, not after.

<!-- mios-src:10b7ce9a5d41 from Get-MiOS.ps1:1022-1028 -->

### Bootstrap window dims (the elevated conhost that runs...

Bootstrap window dims (the elevated conhost that runs Pass-1 +
Pass-2 + readme/acknowledgements). Pulled from mios.toml
[terminal.install] -- vendor default 80x40 for log/output room.
The post-install MiOS APP spawn uses [terminal] (80x20, portal
feel) because the operator-facing terminal is shorter than the
install-time log window.
Compute target pixel dims HERE so they bake as literal integers
into the rendered inner cmd -- the spawned pwsh has no access
to outer-scope variables.

<!-- mios-src:27c791a6ae2d from Get-MiOS.ps1:1033-1041 -->

### Separate dims for the post-install MiOS APP spawn (80x20 --...

Separate dims for the post-install MiOS APP spawn (80x20 -- the
canonical operator-facing terminal). These bake into the inner
cmd alongside $_elevCols/$_elevRows but drive the wt.exe -p MiOS
spawn at end-of-bootstrap, NOT the bootstrap conhost itself.

<!-- mios-src:81cb2b6b22ce from Get-MiOS.ps1:1052-1055 -->

### Pass-2 exit-message strings resolved at install time from...

Pass-2 exit-message strings resolved at install time from
mios.toml [messages.pass2_exit] (SSOT). Baked as literals into
the inner-cmd heredoc below.  Single-quote the values + escape
single-quotes so the heredoc-substituted text is a valid PS
literal regardless of operator-supplied content.

<!-- mios-src:777d75b0b18a from Get-MiOS.ps1:1061-1065 -->

### AGREEMENT_ACK is intentionally NOT pre-set. Pass-2 (this...

AGREEMENT_ACK is intentionally NOT pre-set. Pass-2 (this elevated
relaunch) is where the operator reads + acks the agreement, in the
properly-sized 80x40 conhost. Pre-accepting via env would skip the
gate -- which would defeat the point of moving the gate here.
Tell the MiOS pwsh profile body to render the framed dashboard +
oh-my-posh prompt for THIS bootstrap window. The profile gates the
dashboard call on `$env:WT_SESSION OR `$env:TERM_PROGRAM='mios';
elevated pwsh in conhost has neither, so without this the install
runs in a vanilla black box. Setting it here makes the elevated
bootstrap window itself the MiOS terminal experience.

<!-- mios-src:56365e4ef5b8 from Get-MiOS.ps1:1078-1087 -->

### Force UTF-8 codepage + output encoding BEFORE any output...

Force UTF-8 codepage + output encoding BEFORE any output paints.
Without this, conhost defaults to CP437/CP1252 and the dashboard's
Unicode box-drawing glyphs (+ + + + | - + +) render as `?`. Setting
OutputEncoding alone isn't enough -- chcp 65001 changes the active
codepage for the underlying console, which is what affects glyph
substitution.

<!-- mios-src:e05b22a8a82a from Get-MiOS.ps1:1089-1094 -->

### Pass-2 transcript -- the early elevated window was...

Pass-2 transcript -- the early elevated window was historically UNLOGGED
(operator: "the incorrectly launched powershell window just dies silently
--seemingly no logs in sight!!!"). Start a transcript NOW so ANY early
failure (IRM fetch, scriptblock parse/throw, agreement gate, a preflight
'exit', or a bare error) lands in a readable file. build-mios.ps1 opens
its own mios-install-*.log later; this closes the gap BEFORE that on the
Pass-2 critical path. install-robustness.

<!-- mios-src:2625a20c5994 from Get-MiOS.ps1:1099-1105 -->

### Pre-UAC cursor location (captured by the launching pwsh...

Pre-UAC cursor location (captured by the launching pwsh BEFORE Start-
Process -Verb RunAs); use these constants instead of querying
Cursor.Position now (which would read at the UAC Yes-button click
location, defeating the active-display intent).

<!-- mios-src:d9750db7ae91 from Get-MiOS.ps1:1113-1116 -->

### DPI per-monitor v2 so Screen.WorkingArea + SetWindowPos...

DPI per-monitor v2 so Screen.WorkingArea + SetWindowPos agree on
the coordinate space (was off-by-DPI on multi-monitor setups
where the operator-reported regression "all windows aren't
recentering still" surfaced -- MoveWindow placed the window at
logical-px coords interpreted as physical-px, missing the target
monitor entirely on high-DPI secondary displays).

<!-- mios-src:0f4c7fdfe642 from Get-MiOS.ps1:1129-1134 -->

### Pixel target size -- BAKED from outer scope as literal...

Pixel target size -- BAKED from outer scope as literal integers
via @"..."@ interpolation (no backticks on $_winWPx / $_winHPx /
$_elevCols / $_elevRows / $_elevScr). The inner pwsh process
cannot see outer-scope variables (it's a fresh pwsh.exe spawn);
everything we want it to know must be substituted at template-
build time. Earlier broken edits used backticks on $_elevCols
which produced LITERAL `\$_elevCols` in the rendered script,
which evaluated to $null inner-side, multiplied by cell dims
to zero, and gave a 20x12 (basically 1x1 visible) window.
Branch on current width: SetBufferSize fails when shrinking buffer
below current window; SetWindowSize fails when growing window
beyond current buffer. Conhost rule: buffer.cols >= window.cols.

<!-- mios-src:d9d118dd2374 from Get-MiOS.ps1:1136-1147 -->

### SetWindowSize tells conhost to display N cells; conhost...

SetWindowSize tells conhost to display N cells; conhost itself
auto-pixel-sizes the Win32 window correctly for the active DPI.
DON'T MoveWindow with hardcoded pixel dims (the previous behaviour
of `MoveWindow ... 820x812`) -- at 150% DPI conhost cells render
~16 px wide so a 820 px window only fits ~50 cells, and at 200%
DPI ~33 cells. Operator-reported regression at 200% DPI:
"window opens but is 1/2 the size it should be". Reading the
ACTUAL post-resize Win32 window dims via GetWindowRect and using
those for centering keeps the window correctly cell-sized while
still putting it on the operator's active display.
Retry loop: window may not be fully realized + sized yet at first
call; SetWindowPos before that is a silent no-op. Try up to 8x
over ~2 seconds. Log every step to M:\MiOS\logs\mios-center-debug.log
(per feedback_mios_m_drive_everything; falls back to %TEMP% only
when M:\ doesn't exist yet during very-early bootstrap) so operator
can paste back what's happening when "windows aren't centering" recurs.

<!-- mios-src:6360c745a50f from Get-MiOS.ps1:1156-1171 -->

### Don't break on success. Operator-reported regression...

Don't break on success. Operator-reported regression: "spawned
install window still isn't centered/self centering STILL".
Logs showed centering succeeded on attempt 0 but the window
subsequently moved -- conhost/WT re-layouts after every output
paint + SetWindowSize call can shift the window. Keep re-
centering through all 12 ticks (~6 seconds) so the window
stays put through the inner-cmd's banner Write-Host calls,
the IRM fetch, and the child pwsh spawn.

<!-- mios-src:54c69c26bb3d from Get-MiOS.ps1:1195-1202 -->

### Build the cache-busted URL HERE inside the inner cmd, NOT...

Build the cache-busted URL HERE inside the inner cmd, NOT via outer-
scope interpolation. Operator-reported regression: when the inner cmd
was rendered with `-Uri '$_rawUrl'` and `$_rawUrl` interpolated to empty
for any reason (encoding issue / heredoc quirk / nested-template bug),
the rendered file became `Invoke-RestMethod -Uri '' -Headers ...` which
sent PowerShell into its mandatory-parameter prompt loop:
    cmdlet Invoke-RestMethod at command pipeline position 1
    Supply values for the following parameters:
    Uri:
Computing the URL inside the inner cmd removes the outer-scope dep
entirely and makes the rendered file self-sufficient.

<!-- mios-src:0918c1ef809d from Get-MiOS.ps1:1213-1223 -->

### Write to a temp .ps1 and run as a CHILD pwsh process so any...

Write to a temp .ps1 and run as a CHILD pwsh process so any
'exit N' calls inside Get-MiOS.ps1 terminate the child, NOT our
hosting elevation window. Without this, any preflight 'exit 1'
killed the elevated host before the operator could read the
error or pause for inspection -- the window appeared to "die
silently". Per operator: "the incorrectly launched powershell
window... just dies silently--seemingly no logs in sight!!!"
Log path: M:\MiOS\logs if M:\ exists (the canonical install-on-M
location), else %TEMP%. The child pwsh runs Start-Transcript
internally so the log gets every Write-Host without the parent
having to pipe through Tee-Object (which DESTROYS the child's
`$Host.UI.RawUI` console handle and makes `$RawUI.CursorPosition
= @{X=0;Y=0}` throw "The handle is invalid" -- exactly the crash
the operator hit in commit 1e3484f).
NO PRELUDE PREPEND. Get-MiOS.ps1 has a `param()` block at the
top of the file -- PowerShell requires param() to be the FIRST
statement in a script (after comments / using statements). My
prior commits prepended chcp/Start-Transcript lines which
pushed param() to line 6+, causing PowerShell to parse the
block's arguments as standalone assignments:
    "[string]\$RepoUrl = 'https://github.com/mios-dev/...'"
    -> "The assignment expression is not valid"
The codepage + Console encoding are ALREADY set in the inner
cmd (chcp 65001 etc. above); the child pwsh inherits the
conhost codepage from this elevated parent, so Unicode glyphs
render correctly without an inline prelude.
Logging during Pass-1 is sacrificed for now -- build-mios.ps1's
own logging at M:\MiOS\logs\mios-install-*.log covers Pass-2+
which is where 90% of the install time lives. Operator sees
all Pass-1 output live in the elevated host (Read-Host pause
at the end keeps it visible).
Run the freshly-fetched Get-MiOS.ps1 IN-PROCESS via scriptblock.
The previous `& pwsh.exe -File $tmpScript` spawned a new pwsh
process. On Windows 11 with WT as the default terminal, that
spawn opened a NEW WT TAB / WINDOW (operator-reported regression:
"spawns bootstrap window (correct) >> THEN opens a new window
(incorrect) >> THEN ALSO spawns the acknowledgement window").
In-process scriptblock execution eliminates the third window AND
avoids the cross-process console handle dance that previously
broke Read-Host on PS 5.1 fallback. Any `exit N` calls inside
Get-MiOS.ps1 will terminate THIS pwsh -- but that's exactly what
the operator wants for a unified "single bulk-install window"
experience. The existing try/catch wrapping is enough to keep
the elevation host visible long enough for the Read-Host pause
at the bottom of the inner cmd to fire.

<!-- mios-src:cf2adc8ee86f from Get-MiOS.ps1:1244-1288 -->

### "irm|iex invocation and install processes spawn too many...

"irm|iex invocation and install processes
spawn too many powershell windows and should be performed
in-line in one promoted Powershell window after bootstrap".
On success, transition THIS elevated conhost into the MiOS
terminal experience instead of asking the operator to press
Enter and click a shortcut.  Dot-source M:\MiOS\powershell\
profile.ps1 -- it self-renders the framed dashboard and
exposes the `mios <verb>` dispatcher (build / dash / dev /
config / pull / update / help).  Operator types verbs
directly in this same window; no new WT spawn, no shortcut
click, no third window.

<!-- mios-src:42354e7f078f from Get-MiOS.ps1:1306-1316 -->

### The MiOS profile body sources the dashboard, oh-my-posh...

The MiOS profile body sources the dashboard, oh-my-posh,
the mios.toml resolvers, AND defines `mios <verb>` plus the
per-verb function aliases.  After this dot-source the
operator is at the MiOS prompt in this same elevated
conhost.  pwsh -NoExit (set in the spawn args) keeps the
interactive prompt alive; no Read-Host below for the
success path.

<!-- mios-src:b978023f0c73 from Get-MiOS.ps1:1322-1328 -->

### SUCCESS path returns here -- the dot-sourced profile owns...

SUCCESS path returns here -- the dot-sourced profile owns
the prompt from this point.  No press-Enter close; the
operator quits the window naturally (`exit` / Ctrl-D / `q`).

<!-- mios-src:ad43c77bee53 from Get-MiOS.ps1:1338-1340 -->

### Write the inner cmd to a temp .ps1 and pass -File. Why NOT...

Write the inner cmd to a temp .ps1 and pass -File. Why NOT
-EncodedCommand: the inner cmd is ~12.5 KB of source. UTF-16
encoding doubles that to ~25 KB; Base64 expands to ~33 KB. Start-
Process -Verb RunAs goes through ShellExecute, whose lpParameters
is capped at 32,767 chars (signed 16-bit limit). The encoded
payload + surrounding -NoLogo / -NoProfile / -ExecutionPolicy /
-NoExit / -EncodedCommand args pushes us OVER 32 KB -- ShellExecute
returns ERROR_INVALID_PARAMETER (0x80070057) which surfaces to the
operator as "Self-elevation failed: The parameter is incorrect."
UAC never even fires; Pass-2 never opens. -File <shortpath> keeps
the command line tiny regardless of inner cmd size, so ShellExecute
is happy.

<!-- mios-src:64764e21e4c7 from Get-MiOS.ps1:1355-1366 -->

### SUCCESS

SUCCESS: Pass-1 has done its job. Pass-2 is alive in a new
elevated window which will fetch the latest Get-MiOS.ps1, render
the agreement gate (in 80x40), and run the install. Pass-1 must
EXIT IMMEDIATELY so the operator's focus moves cleanly to Pass-2.
The hosting `powershell -Command "irm | iex"` has no -NoExit, so
`return` here lets Pass-1's powershell.exe close on its own.
Operator perceives: small black box flashes -> UAC prompt ->
properly-sized elevated window appears with the agreement.

<!-- mios-src:d277abbb6027 from Get-MiOS.ps1:1385-1392 -->

### FAILURE PATH

FAILURE PATH: keep Pass-1 visible so the operator can read the
error detail (UAC denied, ShellExecute failure, etc.). On
success Pass-1 has already returned above.

<!-- mios-src:15a625b2e5b2 from Get-MiOS.ps1:1395-1397 -->

### AGREEMENT GATE -- runs in Pass-2 only. Pass-1 returned out...

AGREEMENT GATE -- runs in Pass-2 only. Pass-1 returned out of the
elevation block above, so reaching this line means we're already in
the properly-sized 80x40 elevated conhost. The gate function resizes
UP to 80x60 to give the ~104-line agreement breathing room, then
blocks on Read-Host until the operator types "Acknowledged" or aborts.

<!-- mios-src:77a5471db875 from Get-MiOS.ps1:1410-1414 -->

### Windows Terminal "MiOS" profile + Geist Mono Nerd Font +...

-----------------------------------------------------------------------
Windows Terminal "MiOS" profile + Geist Mono Nerd Font + oh-my-posh
wiring. Runs ONCE on the outer (pre-elevation) pass so the elevated
relaunch can pin -p MiOS and inherit the correct font, scheme,
padding, acrylic backdrop, 50% blur, 12pt Geist, and a
borderless 80x30 focus-mode window centered on the primary display.

Canonical dimensions: 80 cols × 30 rows.
  * 80×30 is the IBM "text-mode 3+" / TTY0 standard dimension
    (alongside 80×25 / 80×50). Universal grub/console fallback.
  * 4:3 pixel aspect ratio: with a 1:2 (W:H) monospace cell, 80/30
    gives 720×600 px ≈ 1.20:1 → render with lineHeight=1.0 the cells
    squash to 9×18 px → 720×540 → exactly 4:3.
  * Wide enough for the dashboard frame (80-col strict-clamp) and
    tall enough for the menu + footer + 8 phase rows + log row.

All three helpers are idempotent: safe to call on every run.
-----------------------------------------------------------------------

<!-- mios-src:3604382583c4 from Get-MiOS.ps1:1417-1434 -->

### Hokusai + operator-neutrals palette -- ALL values source...

Hokusai + operator-neutrals palette -- ALL values source from
mios.toml [colors] (vendor < host < user three-layer overlay) via
Get-MiosTomlValue. mios.toml is THE singular SSOT for the palette;
the literals below are FALLBACKS used only when the layered TOML
can't be read (early bootstrap before M:\ exists, or a corrupted
overlay). An operator edit in mios.html flows through to this
palette without touching any PS1.

<!-- mios-src:58ffff6bee92 from Get-MiOS.ps1:1436-1442 -->

### DEFENSIVE color resolution. Every WT scheme field MUST be a...

DEFENSIVE color resolution. Every WT scheme field MUST be a valid
`#rrggbb` or `#rgb` hex string -- WT rejects the entire
settings.json with "Line N column N (foreground) Have: ""
Expected: color" if ANY field is empty or malformed, falling back
to bare WT defaults (the operator-reported "no theme / no acrylic
/ no MiOS profile" symptom -- WT silently dropped the broken
MiOS scheme). The _hex helper below accepts the TOML value, then
ALWAYS returns a valid hex color: if the resolved value is empty
/ null / malformed, it returns the hardcoded fallback instead.

<!-- mios-src:502719282064 from Get-MiOS.ps1:1444-1452 -->

### Idempotent winget install for Windows Terminal Preview...

Idempotent winget install for Windows Terminal Preview ("dev line").
WT Preview tracks the active development branch, so MiOS gets the
newest acrylic/systemBackdrop/launchMode behavior the moment Microsoft
ships it. Stable WT (Microsoft.WindowsTerminal) is fine too; we only
upgrade an operator who has neither installed.

Source: winget pulls from msstore by default; Preview lives at
  id = Microsoft.WindowsTerminal.Preview
We pass --silent so no UI surfaces and --accept-{package,source}-
agreements so Server SKUs (which display the agreement EULA on first
winget call) don't hang the bootstrap.

<!-- mios-src:6f3447f325a5 from Get-MiOS.ps1:1521-1531 -->

### Per operator

Per operator: target the BASE Windows Terminal install (Stable),
NOT Preview. Polls until WT Stable's AppX package is registered
AND its LocalState dir is materialized.

<!-- mios-src:f6ff6be699e3 from Get-MiOS.ps1:1533-1535 -->

### Bootstrap winget itself on a truly bare Windows host (Win...

Bootstrap winget itself on a truly bare Windows host (Win 10 21H2,
OOBE-fresh Win 11 N edition without Store, etc.) before any other
Install-MiOS* function tries to use it. Operator-flagged
"MiOS should automatically install EVERYTHING needed to install MiOS
via irm|iex". Without this, every winget invocation on a bare host
silently warns + skips, leaving the bootstrap half-installed.

Resolution chain:
  1. winget already on PATH -> done.
  2. Microsoft.DesktopAppInstaller AppxPackage installed but PATH
     stale -> refresh PATH, re-probe.
  3. Download App Installer MSIXBUNDLE from mios.toml
     [bootstrap.prereqs].appinstaller_url (default aka.ms/getwinget)
     -> Add-AppxPackage.

<!-- mios-src:3c2f4f35da32 from Get-MiOS.ps1:1560-1573 -->

### Operator pivot

Operator pivot: MiOS targets the BASE Windows Terminal install,
NOT Preview. We do NOT pollute the operator's globals or default
profile -- we just upsert the MiOS / MiOS-DEV profiles into the
operator's existing settings.json so they appear in the WT
profile dropdown. Borderless / centered / sized launch comes
from wt.exe COMMAND-LINE flags at launch time, not globals.

<!-- mios-src:2d880d8138ff from Get-MiOS.ps1:1614-1619 -->

### Ensure PowerShell 7 (`pwsh.exe`) is on disk BEFORE the WT...

Ensure PowerShell 7 (`pwsh.exe`) is on disk BEFORE the WT MiOS
profile is generated, so the profile's `commandline` can bind
to pwsh.exe rather than silently falling back to Windows
PowerShell 5.1. PS 5.1 has the OLD PSReadLine that breaks
oh-my-posh init's modern PSReadLine integration; the resulting
MiOS terminal renders the OPERATOR'S pre-existing PS 5.1
profile (whatever broken oh-my-posh init they had — typical
symptom: "CONFIG NOT FOUND" prompt segment). Install-MiOSTerminalExtras
at Step 6/7 also installs Microsoft.PowerShell, but that's
AFTER WT profile creation — too late.

Idempotent: probes existing install before re-installing.
Refreshes $env:PATH after install so the caller's pwsh
detection (Get-AppxPackage / Get-Command pwsh) sees the new
binary in this same session.

<!-- mios-src:2f39901f8939 from Get-MiOS.ps1:1657-1671 -->

### TOML-first per AGENTS.md §3 -- winget ID from mios.toml...

TOML-first per AGENTS.md §3 -- winget ID from mios.toml
[bootstrap.prereqs].pwsh_pkg (operator can pin to PowerShell-Preview
or an MSI variant via mios.html).

<!-- mios-src:3e24aec83cba from Get-MiOS.ps1:1695-1697 -->

### ALL MiOS install artifacts land on M:\ per the operator's...

ALL MiOS install artifacts land on M:\ per the operator's
invariant. Fonts go to M:\MiOS\fonts\ -- Windows accepts any
path in HKCU\...\Fonts as long as the registry value points
at the actual .ttf file. Falls back to %LOCALAPPDATA%\...
only if M:\ isn't mounted yet (very early bootstrap).

<!-- mios-src:8f21efc6e7ad from Get-MiOS.ps1:1745-1749 -->

### Get every font file in the extracted tree (.ttf OR .otf --...

Get every font file in the extracted tree (.ttf OR .otf -- the
current Geist Nerd Fonts release ships .otf only). Nerd Fonts
release naming has changed multiple times -- the Get-ChildItem
-Filter pattern was missing valid faces because of case-sensitivity
and substring quirks on PowerShell 7.6+. Use -match instead which
is case-insensitive by default.

<!-- mios-src:ab4656f72f13 from Get-MiOS.ps1:1761-1766 -->

### Install Bibata-Modern-Classic as the Windows-wide cursor...

Install Bibata-Modern-Classic as the Windows-wide cursor scheme.
Operator-flagged "cursor is still not bibata GLOBALLY".
Linux dev VM Bibata install runs separately inside the WSL distro
(build-mios.ps1's Set-Step "Installing Bibata-Modern-Classic
cursor"). This Windows-side complement covers the desktop chrome
so the operator sees the same cursor on hover/click outside WT.

Mechanism:
  1. Fetch ful1e5/Bibata_Cursor latest "Bibata-Modern-Classic-
     Windows.tar.gz" release asset.
  2. Extract to M:\MiOS\cursors\Bibata-Modern-Classic (per the
     everything-on-M:\ invariant).
  3. Set HKCU\Control Panel\Cursors values to the extracted
     .cur / .ani paths so Windows uses Bibata in every app.
  4. Register the scheme under HKCU\Control Panel\Cursors\Schemes
     so it appears in Settings -> Mouse -> Additional pointer
     options and survives operator scheme switches.
  5. Broadcast SystemParametersInfo(SPI_SETCURSORS) so the running
     desktop picks up the new pointers without a logoff.

Idempotent: if Bibata is already installed AND the active
`(Default)` scheme is "Bibata Modern Classic", short-circuit.

<!-- mios-src:bd016a5062b6 from Get-MiOS.ps1:1814-1835 -->

### Map Bibata filenames -> Windows cursor registry value...

Map Bibata filenames -> Windows cursor registry value names.
Sourced from Bibata's shipped install.inf (clickgen-generated
Wreg section). Notable rename from older Bibata releases:
- Pointer.cur (not Default.cur) for Arrow
- Work.ani (not Working.ani) for AppStarting
- Vert.cur / Horz.cur / Dgn1.cur / Dgn2.cur (compact names)
- Alternate.cur for UpArrow (no -Select suffix)

<!-- mios-src:47ddf1beb043 from Get-MiOS.ps1:1890-1896 -->

### CursorBaseSize controls the rendered pixel size of the...

CursorBaseSize controls the rendered pixel size of the active
cursor (Windows picks the matching variant from the multi-image
.cur file). Bibata's Windows release embeds 5 sizes per .cur
(32, 48, 64, 96, 128); even the smallest 32px variant renders
visibly larger than typical Windows cursors because the
bibata glyph fills more of the 32x32 canvas. Operator-flagged
"windows bibata is too large" -- lowering the base
size to 24 forces Windows to downscale the 32px source to
match the visual weight of the default Aero cursor.
Operator-overridable via mios.toml [theme.cursor_windows].base_size.

<!-- mios-src:b5a575f427ee from Get-MiOS.ps1:1954-1963 -->

### Resolve the WT settings.json path. Per operator: target the...

Resolve the WT settings.json path. Per operator: target the BASE
(Stable) Windows Terminal install. Returns $null if WT Stable isn't
installed (caller should run Install-MiOSWindowsTerminal first).

<!-- mios-src:72fdafe5116b from Get-MiOS.ps1:1993-1995 -->

### Stable WT profile GUID for "MiOS-Bootstrap". Re-using the...

Stable WT profile GUID for "MiOS-Bootstrap". Re-using the same GUID
across runs lets us upsert idempotently instead of polluting the
profile list with a new entry every time.

<!-- mios-src:f9911bb72589 from Get-MiOS.ps1:2023-2025 -->

### Re-resolve the palette HERE (in case $Script:MiosPalette...

Re-resolve the palette HERE (in case $Script:MiosPalette was cached
before the M:\ TOML existed -- file-load-time evaluation of
Get-MiosPalette can hit the cold-fetch path which may have failed
silently). Then guard EVERY field with the same hex-fallback the
palette resolver applies, so a stale/empty cached value can't leak
into the WT scheme and trigger WT's "Line N column N (foreground)
Have: '' Expected: color" rejection -- which falls back the entire
settings.json to defaults (no MiOS profile, no acrylic, no scheme).

<!-- mios-src:7e4c8b067a5a from Get-MiOS.ps1:2028-2035 -->

### Profile commandline

Profile commandline: pwsh -NoLogo -NoExit -Command ". 'M:\...'".
Explicitly dot-sources the canonical M:\ profile script AFTER
$PROFILE has loaded -- so even if the operator has a broken
oh-my-posh init in their $PROFILE that runs after our markers,
OUR regex-patched init runs LAST and wins. This is what makes
the MiOS terminal's prompt deterministic regardless of the
operator's existing PowerShell profile state. Without this
explicit re-init, the MiOS terminal could inherit a broken
PSReadLine binding state from the operator's pre-existing init.
Resolve pwsh 7 across all install shapes:
  1. MSI install at $env:ProgramFiles\PowerShell\7\pwsh.exe
  2. Microsoft Store install at WindowsApps\Microsoft.PowerShell_*
     (operator's actual setup -- PS 7.6.1 from MS Store).
  3. App Execution Alias via Get-Command (last-ditch).
  4. Windows PS 5.1 (only if no pwsh found at all). 5.1 has the
     OLD PSReadLine that breaks oh-my-posh init -- avoid unless
     truly desperate.

<!-- mios-src:68883d89edd4 from Get-MiOS.ps1:2068-2084 -->

### NoProfile is CRITICAL

-NoProfile is CRITICAL: skip the operator's $PROFILE chain
entirely so any pre-existing oh-my-posh init / PSReadLine
configuration / aliases the operator already has DON'T run AFTER
our M:\ profile and override it. Operator-reported symptom: their
pre-existing themed PS 7 prompt rendered in MiOS terminal because
their $PROFILE re-initialized oh-my-posh AFTER our marker block.
With -NoProfile, ONLY the M:\ profile runs (via -Command dot-
source), so the MiOS terminal is operator-isolated and 100%
deterministic.
Single-quoted PS string with `''` for embedded literal quotes.
ConvertTo-Json will JSON-encode the outer double-quotes correctly.
`$env:MIOS_APP_CONTEXT='1'` is the gate signal the M:\ profile
body checks before resizing the conhost to the MiOS-app dims
(80x20). Without this signal the profile body skips the resize,
which is what we want during BOOTSTRAP/INSTALL where any child
pwsh inheriting `$PROFILE.CurrentUserAllHosts redirector should
NOT shrink the operator's 80x40 install conhost mid-install.

<!-- mios-src:ca4e79dc5a16 from Get-MiOS.ps1:2121-2137 -->

### Per-profile shared settings -- apply to BOTH "MiOS" and...

Per-profile shared settings -- apply to BOTH "MiOS" and "MiOS-DEV"
so they look/feel identical. Belt-AND-braces acrylic settings:
WT 1.16-1.17 reads `useAcrylic` (legacy bool) and `opacity`. WT
1.18+ reads `systemBackdrop` (per-profile). Setting BOTH means
acrylic 50% transparency renders correctly across every WT
version the operator might end up on. `useMica` is NOT set --
it's not a documented WT key (mica is selected via
systemBackdrop="mica"), and shipping unknown keys can cause WT's
schema validator to reject the profile and fall back to defaults.
GLOBAL MiOS terminal defaults sourced from mios.toml [theme] +
[theme.font]. Per operator (multiple reaffirmations): acrylic ON,
50% transparency, frame-less, border-less, scroll-bar-less. The
WT profile patcher reads from mios.toml so editing those keys in
the configurator HTML re-skins every MiOS terminal on the next
bootstrap run -- single edit surface, applied to BOTH WT profiles
(MiOS + MiOS-DEV) below.
-- Defensive toml-value resolution --------------------------
If ANY of these returns an empty / invalid value, WT's schema
validator rejects the entire profile and the operator gets bare
default chrome (no acrylic, no MiOS scheme, no font). The earlier
tabColor "" failure proved this is fragile -- so we validate
EVERY toml-resolved string before stamping it into the profile.

<!-- mios-src:c24366ea9a59 from Get-MiOS.ps1:2140-2161 -->

### launch_mode -- forces WT focus mode (no titlebar, no tabs)...

launch_mode -- forces WT focus mode (no titlebar, no tabs) at
window-create time so the pseudo-console reports the actual
visible cell count from first paint. Without this, WT initially
measures the viewport WITH titlebar/tabs (cell count = cols-1)
and only re-measures after `scrollbarState=hidden` takes over,
by which time the first prompt has already been rendered to the
wrong width. With launch_mode=focus, the chrome is gone before
the first paint, so cell count = cols immediately.

<!-- mios-src:48a4ba0d0642 from Get-MiOS.ps1:2183-2190 -->

### disable_animations -- defaults to FALSE (animations ON) per...

disable_animations -- defaults to FALSE (animations ON) per
operator: "enable animations and all preview features in the MiOS
Windows Terminal profile -- full aesthetics! ALSO: can it quickly
fade on open and close??". WT's built-in window open/close fade is
gated on disableAnimations=false + useAcrylic=true. The trade-off:
acrylic-recompute on first paint MAY re-trigger the off-by-N
cell-count bug; if the powerline wraps again with animations on,
bump mios.toml [terminal].right_margin to 1 as the targeted band-
aid (NOT animations off -- operator wants the aesthetics).

<!-- mios-src:06d557dd77cf from Get-MiOS.ps1:2193-2201 -->

### enable_preview_features -- gates the bundle of WT...

enable_preview_features -- gates the bundle of WT experimental.*
toggles that are aesthetics-relevant (URL detection, AtlasEngine
GPU renderer, forced-VT input, full-repaint rendering). Defaults
to TRUE per operator. Set to false only if a specific WT version
ships a regression in one of the preview keys.

<!-- mios-src:8ca5c82f8612 from Get-MiOS.ps1:2204-2208 -->

### MINIMAL chrome only -- per operator's trace, the WT MiOS...

MINIMAL chrome only -- per operator's trace, the WT MiOS app
rendered the oh-my-posh prompt (so commandline + profile body
work) but DID NOT apply chrome (no acrylic, no MiOS scheme).
That means WT silently rejected one of the chrome keys and
fell back to defaults for the rest. Stripping back to the
bare minimum proven-working set; will re-add carefully once
this verifies rendering with full theming.
Terminal dims sourced from mios.toml [terminal].cols / .rows so
opening the WT profile DIRECTLY (without the launcher's --size
arg, e.g. from the WT dropdown) still produces an 80x20 window.
Without these, WT inherits the operator's global default
(typically 120x30) and the dashboard's framing breaks.

<!-- mios-src:4a90f1eb749c from Get-MiOS.ps1:2215-2226 -->

### Disable WT's end-of-line auto-wrap on the MiOS profile....

Disable WT's end-of-line auto-wrap on the MiOS profile.
Default WT behavior: writing to the LAST column emits a
soft-wrap newline, so content that fills exactly cols-wide
(e.g. our edge-to-edge dashboard frame at width=80 in an
80-col window) wraps every full-width row to a new visual
row -- pushing the dashboard's TOP frame above the viewport.
Operator screenshot image #12: top `+-MiOS-+`
corner clipped, fastfetch info at row 0, right `|` border
missing. Setting this to true tells WT to leave col cols-1
written without firing the soft-wrap, so width=80 content
in an 80-col window stays on one row. Combined with
mios.toml [terminal].right_margin=0 + frame_width=80 this
produces the truly edge-to-edge framed dashboard the
operator wants on BOTH bash + pwsh sides.

<!-- mios-src:513a1884c076 from Get-MiOS.ps1:2256-2269 -->

### initialCols / initialRows lock the dims when WT spawns this...

initialCols / initialRows lock the dims when WT spawns this
profile from a non-launcher entry point (dropdown, "MiOS
Terminal" Start Menu shortcut). Operator-edited via mios.toml
[terminal].cols / .rows.

<!-- mios-src:05274fe1c9b3 from Get-MiOS.ps1:2271-2274 -->

### MiOS-DEV profile

MiOS-DEV profile: drops the operator straight into the MiOS-DEV WSL2
distro as the mios user, cwd /. Same look as MiOS (acrylic, font,
Resolve the actual on-disk WSL distro name. podman machine init
registers the distro as 'podman-MiOS-DEV' (podman hardcodes the
'podman-' prefix), even though the operator-facing name is
MiOS-DEV. Operator-reported regression: clicking the MiOS-DEV
shortcut threw 'WSL_E_DISTRO_NOT_FOUND' because the profile
commandline targeted bare 'MiOS-DEV' which doesn't exist on disk.
Walk the registered distro list at install time and pick the
first match in priority order: prefer 'podman-MiOS-DEV' (post
init) -> 'MiOS-DEV' (post Restore-PodmanPrefix) -> 'podman-MiOS-
BUILDER' (legacy) -> 'MiOS-BUILDER' (legacy).

<!-- mios-src:5cdd952d723b from Get-MiOS.ps1:2290-2301 -->

### GLOBAL WRITES (edge-to-edge pivot): the prior "no globals"...

GLOBAL WRITES (edge-to-edge pivot): the prior "no
globals" stance left WT's pseudo-console reporting +1-2 cells
over the actual visible cell count during first paint, before
`profiles.defaults.scrollbarState='hidden'` could take effect.
That made oh-my-posh's right-aligned powerline block wrap the
trailing time char to col 0 of the next line ("powerline seconds
rolling over to the left under the second-line ❯"). Operator:
"MiOS app/windows terminal windows should be completely
frameless/borderless with no margin (edge-to-edge printing)."

Setting `launchMode = "focus"` at the ROOT level strips the title
bar AND the tab row from the very first paint, so WT measures the
viewport at the actual cell count cols × rows. Pairing it with
per-profile `suppressApplicationTitle = true` keeps WT from
re-measuring whenever the shell tries to set the window title
(every `cd`, every prompt repaint), and `disableAnimations = true`
skips the acrylic-recompute pass that re-measures the cell grid.
All three are required: drop any one and the off-by-N comes back.

<!-- mios-src:fc8f88065644 from Get-MiOS.ps1:2346-2363 -->

### Root-level launchMode -- forces focus mode (no titlebar, no...

Root-level launchMode -- forces focus mode (no titlebar, no tabs)
globally. This affects EVERY WT window the operator opens, not
just MiOS profiles. Operator-approved ("go fix
mios-bootstrap edge-to-edge now") because `--focus` on the wt.exe
CLI alone hides tabs but leaves the titlebar, so the off-by-N
cell-count bug persisted on launches that didn't go through the
MiOS launcher. Sourced from mios.toml [theme].launch_mode (SSOT)
so an operator who needs tabs back can flip it via mios.html
without editing this script. Use `wt.exe -w 0 nt` for a transient
tabs-and-titlebar window if needed.

<!-- mios-src:a8ac96e1d228 from Get-MiOS.ps1:2369-2378 -->

### GLOBAL no-scrollbars + zero-padding + no-titlebar-rewriting...

GLOBAL no-scrollbars + zero-padding + no-titlebar-rewriting via
profiles.defaults. Per operator: "MiOS app window/terminal
window(s) should all have NO scrollbars inhibiting any windows
globally!!". Per-profile scrollbarState only affects that profile;
profiles.defaults applies to EVERY profile including auto-
generated ones (cmd, PowerShell, WSL distros), so when an operator
switches profiles they keep the borderless+scrollbar-less feel.
suppressApplicationTitle=true and disableAnimations=true are the
second + third legs of the edge-to-edge tripod (see comment
above) -- without them, WT re-measures the viewport after the
first prompt has already been rendered using the wrong width.

<!-- mios-src:f5f35a64a1af from Get-MiOS.ps1:2381-2391 -->

### Preview / experimental features bundle. All gated on...

Preview / experimental features bundle. All gated on
mios.toml [theme].enable_preview_features. Operator: "enable
animations and all preview features in the MiOS Windows Terminal
profile -- full aesthetics!" Each key here MUST be a documented
WT experimental knob (no random invented keys -- WT silently
rejects unknown keys, and a single rejected key can cascade into
the entire profile being skipped, which manifests as "MiOS scheme
never applied" / "powerline glyphs render as boxes").

<!-- mios-src:468522cf4252 from Get-MiOS.ps1:2403-2410 -->

### ForceVT input -- routes ALL input through the VT pathway...

ForceVT input -- routes ALL input through the VT pathway, so
modifier keys (Ctrl/Alt/Shift combos) hit the shell as
documented escape sequences instead of being intercepted by
WT's native key handler.

<!-- mios-src:b522cb00d7fd from Get-MiOS.ps1:2418-2421 -->

### Filter out any prior MiOS / MiOS-DEV entries by GUID *or*...

Filter out any prior MiOS / MiOS-DEV entries by GUID *or* by the
names we've used in earlier revisions, so the upsert is exactly two.
Also strip podman/WSL auto-generated profiles for our distros
(podman-MiOS-DEV, podman-MiOS-BUILDER, etc.) -- WT auto-creates one
per `podman machine init` call and they accumulate without dedup.
Our branded MiOS-DEV profile already covers that distro.
Strip prior MiOS-related entries.  "MiOS" name kept in the strip
list so re-runs after the rename ("MiOS app itself
should be defined as MiOS-WIN") clean up the old "MiOS" profile
left behind.  Also strips current "MiOS-WIN" by name in case the
GUID changed.

<!-- mios-src:e59937c77af1 from Get-MiOS.ps1:2447-2457 -->

### NOTE

NOTE: globalSummon keybinding (Win+Space) NOT written. Adding
it appears to trip WT's settings-file validator silently --
the prompt rendered (so commandline + scheme reference were
fine) but acrylic / scheme resolution didn't apply, suggesting
WT bailed mid-load. Will re-add via a separate post-MVP commit
after minimum chrome is verified rendering. Operator can still
add it manually via mios-config.html or by editing settings.json.

<!-- mios-src:dd6ab125b9e2 from Get-MiOS.ps1:2474-2480 -->

### Verify against the ACTUAL renamed profile names from...

Verify against the ACTUAL renamed profile names from
mios.toml [theme.terminal] ("MiOS app
itself should be defined as MiOS-WIN").  Was hardcoded to
'MiOS' which always failed post-rename and dropped through
to the raw-JSON-injection fallback that wrote a degraded
settings.json (schemes/profiles arrays partly-stripped),
leaving WT without the proper MiOS chrome -> Nerd Font
PUA glyphs (U+E0B4 / U+E0B6) rendered as `?` placeholders.

<!-- mios-src:92d95ef966ad from Get-MiOS.ps1:2503-2510 -->

### Make MiOS a first-class Windows app the moment irm|iex...

Make MiOS a first-class Windows app the moment irm|iex finishes:
  * Start Menu MiOS.lnk  (so Win-search "MiOS" returns it)
  * Desktop MiOS.lnk     (one-click launch)
  * HKCU Uninstall key   (Settings > Apps > Installed apps lists it)
  * AppUserModelID stamp (taskbar grouping + Pin to Start identity)
  * Best-effort programmatic Pin to Start (Win10 only -- Win11 hint)

Target dir for the launcher script: M:\MiOS\bin\mios-launch.ps1
(operator's M:\-everywhere invariant -- "irm|iex sets up M:\
disk/partition installs EVERYTHING to M:\ EVERYTHING").  M:\
is a HARD REQUIREMENT -- the bootstrap creates it in
Initialize-DataDisk before this function runs.  No fallback
to LOCALAPPDATA; if M:\ isn't there, something has wiped it
mid-install and we should fail loudly rather than silently
split the install across C:\ and M:\.

<!-- mios-src:1842db5d85f4 from Get-MiOS.ps1:2538-2552 -->

### mios-launch.ps1 -- native MiOS app launcher. Spawns wt.exe...

mios-launch.ps1 -- native MiOS app launcher.
Spawns wt.exe with the MiOS profile in focus mode (frameless,
borderless, no titlebar/tab-row), 80 cols x 30 rows, screen-centered
on whichever monitor the cursor is currently on, always-on-top.
Runs invisibly (parent shortcut uses -WindowStyle Hidden).

-Profile <name>  WT profile to launch.  Canonical names:
                 'MiOS-DEV' (= dev VM via wsl.exe -d podman-MiOS-DEV)
                 'MiOS-WIN' (= Windows pwsh + MiOS profile body)
-Verb <name>     Optional. Runs `mios <verb>` inside the launched
                 Windows-side window after the profile body loads.
                 Ignored for MiOS-DEV (the dev VM is a bash login).

"UNIFY all MiOS app windows/themed windows
terminal windows to use the same profile and launch params GLOBALLY!!!"

<!-- mios-src:ef600b96fa8c from Get-MiOS.ps1:2577-2591 -->

### Window name

Window name: MiOS for the bare hub launch, MiOS-<verb> for verb
launches. Per-verb unique names prevent verb tabs piling into the
main MiOS hub window -- each click opens its OWN centered focus
window. The hub stays single-instance (clicking MiOS again reuses
the existing window). Win+Space summon still targets `MiOS` (the hub)
per mios.toml [theme.terminal].summon_window_name.

<!-- mios-src:6a821546868c from Get-MiOS.ps1:2612-2617 -->

### `-w <winName>` names the window so click-to-focus finds it...

`-w <winName>` names the window so click-to-focus finds it and the
post-launch SetWindowPos retry can target it. The hub uses
`-w MiOS` (single-instance, summon-targetable). Per-verb launches
use `-w MiOS-<verb>` (own window per verb -- no tab-pile).

Empty subcommand on hub launches uses the profile's bound commandline
(Windows pwsh + MiOS PS profile body via Install-MiOSTerminalProfile).
On verb launches, override commandline with a pwsh that loads the
profile body explicitly THEN runs `mios <verb>` -- otherwise wt.exe's
subcommand replaces the profile commandline and we lose the dashboard
render + the `mios` function definition.

<!-- mios-src:211242bb3393 from Get-MiOS.ps1:2640-2650 -->

### Pick the WindowsTerminal process whose StartTime is AFTER...

Pick the WindowsTerminal process whose StartTime is AFTER our
spawnedAt timestamp. Picking "newest WT" without the timestamp
filter accidentally targets the operator's pre-existing WT
window (whose StartTime is later only because StartTime sort
picks the most-recently-active one). Filter by spawn time + 1s
leeway so we always land on OUR newly-spawned WT.

<!-- mios-src:79c0635b523c from Get-MiOS.ps1:2682-2687 -->

### ENFORCE the target pixel size ($winW / $winH computed from...

ENFORCE the target pixel size ($winW / $winH computed from
mios.toml [terminal].cols/.rows + [theme.font] cell metrics).
The previous version of this code used $rw/$rh from GetWindowRect
-- which is the CURRENT window size -- and only re-centered.
When `wt.exe -w MiOS` added a tab to an existing wider window
(operator already had a MiOS-named WT window from a prior run),
the launcher kept the old wide dims and the operator saw a
~167-col terminal instead of the canonical 80x20. SetWindowPos
with the COMPUTED target dims ($winW / $winH) forces the resize
every launch so the MiOS terminal is deterministic.

<!-- mios-src:51a818bf8e15 from Get-MiOS.ps1:2697-2706 -->

### 0x40 = SWP_SHOWWINDOW | SWP_NOOWNERZORDER (apply size +...

0x40 = SWP_SHOWWINDOW | SWP_NOOWNERZORDER (apply size + topmost).
0x04 = SWP_NOZORDER                       (re-apply to release topmost
                                           after the window is the
                                           front-most; without this
                                           second pass the operator
                                           can't focus other windows).

<!-- mios-src:fe473714f205 from Get-MiOS.ps1:2711-2716 -->

### Bake mios.toml [terminal] / [theme.font] values into the...

Bake mios.toml [terminal] / [theme.font] values into the launcher
body. Single-quoted here-string above means $vars don't interpolate
at definition time; we substitute placeholders here at install time
so the launcher's geometry tracks the operator's mios.toml edits.

<!-- mios-src:343242e79d3a from Get-MiOS.ps1:2723-2726 -->

### Resolve a pwsh.exe for the .lnk target. IMPORTANT: probe...

Resolve a pwsh.exe for the .lnk target.
IMPORTANT: probe canonical install locations FIRST. Get-Command
pwsh.exe on Windows 11 returns the WindowsApps reparse-point stub
(%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe) which ShellExecute
rejects with 0x80070002 (operator 17:57 install: clicking MiOS
Help.lnk produced "[error 2147942402 (0x80070002) when launching
`mios help`] The system cannot find the file specified.")

<!-- mios-src:0e925fa1e32d from Get-MiOS.ps1:2743-2749 -->

### Hub .lnk targets the MiOS-DEV WT profile (mios.toml...

Hub .lnk targets the MiOS-DEV WT profile (mios.toml
[theme.terminal].hub_target_profile, default "MiOS-DEV") --
"MiOS app opens direct to... podman-MiOS-DEV".
The launcher receives -Profile <name>; mios-launch.ps1 spawns
`wt.exe ... -p <name>` which lands the operator straight in the
dev VM shell.  No Verb -- the dev VM commandline is a bash
login, not a `mios <verb>` dispatcher.

<!-- mios-src:a88c33b9a320 from Get-MiOS.ps1:2762-2768 -->

### Canonical 4-shortcut set ------------------ SSOT: each...

- Canonical 4-shortcut set ------------------
SSOT: each shortcut's metadata (name, profile, verb, description)
resolves through mios.toml [apps.shortcut.<key>]. PS-code defaults
below are vendor fallbacks per feedback_mios_defaults_baseline.
Operator can rename/relabel via mios.html -> mios.toml without
touching code. Per feedback_mios_toml_is_ssot_for_code: no
hardcoded user-facing strings.

Operator directive: "MiOS app opens MiOS-DEV machine to the GLOBAL
unified dash, MiOS-WIN does the windows side ... MiOS Help and
Uninstall MiOS are the ONLY installed shortcuts/links system wide!!!"

<!-- mios-src:5cbe00b61264 from Get-MiOS.ps1:2801-2811 -->

### Per-verb shortcuts (MiOS-DEV / MiOS Build / MiOS Dashboard...

-- Per-verb shortcuts (MiOS-DEV / MiOS Build / MiOS Dashboard / etc.) --
Per the canonical e2e contract: native-app surface is the MiOS hub +
per-verb shortcuts. Each verb opens a fresh MiOS WT app window (via
mios-launch.ps1) and runs `mios <verb>` inside it. Both Start Menu
AND Desktop get the shortcuts so the operator can pick whichever
surface they prefer (and pin manually -- Win11 disabled programmatic
pinning to Start, so we drop the .lnk and the operator right-clicks
→ "Pin to Start" / "Pin to Taskbar").
Operator-curated 4-app surface: MiOS (the terminal hub, created
separately above as MiOS.lnk), MiOS-DEV (dev VM dashboard),
MiOS Help (verb reference), Uninstall MiOS (Add/Remove). The
build / dash / config / update / pull verbs are operator-typed
commands INSIDE the MiOS terminal, NOT separate native apps.
Per-verb shortcuts.  Each entry maps to:
  * Profile  -- WT profile name to launch ('MiOS' = hub,
                'MiOS-DEV' = wsl.exe -d podman-MiOS-DEV --user mios)
  * Verb     -- mios verb to run inside the launched window
                (empty = just open the profile, no dispatch)
  * Icon     -- per-verb .ico under M:\MiOS\icons (fallback: mios.ico)
"launching MiOS-DEV doesn't launch in to
the podman-MiOS-DEV machine still" -- root cause was that the
MiOS-DEV.lnk was passing `-Verb dev` to mios-launch.ps1 which
only accepted -Profile, so the dev launcher silently fell back
to Profile='MiOS' (the hub) and the dev verb was never used.
The Profile field below routes to the right WT profile so
MiOS-DEV.lnk now lands in the actual dev VM.
consolidation: "TOO MANY APPS!! I SAID
UNIFY MiOS APPS in a way that makes sense and is minimal --
MiOS app opens direct to ... podman-MiOS-DEV!!!".  The hub
MiOS.lnk written above is the ONE user-facing app.  Per-verb
shortcuts (MiOS Help / MiOS Config / MiOS-DEV / MiOS-WIN)
are NOT created -- those are typed verbs in the terminal
(`mios help`, `mios config`, `mios dev`, etc.), not separate
native apps.  Only the Uninstall MiOS shortcut sibling lives
alongside the hub.  $miosVerbs is left as an empty array so
downstream loops (AumID stamping, .lnk reaping) iterate
zero entries instead of crashing on $null.

<!-- mios-src:c82d38238c60 from Get-MiOS.ps1:2902-2938 -->

### Uninstall MiOS shortcut (Start Menu + Desktop)...

-- Uninstall MiOS shortcut (Start Menu + Desktop) --------------
Per "MiOS should... Install as a Native
Windows Application with a bundled uninstaller being a
shortcut/link as well".  The hub already registers in
Add/Remove Programs (line 2294+); this gives the operator a
direct desktop / Start-Menu shortcut to the uninstaller without
opening Settings -> Apps.  The .lnk targets M:\MiOS\bin\uninstall.ps1
which build-mios.ps1's Install-WindowsBranding stages -- it does
the full reap (WSL distros, podman machines, registry keys, M:\
overlay, .lnk cleanup).  Falls back to the inline UninstallString
registered above if the full uninstall.ps1 isn't on disk yet
(e.g. on a half-bootstrapped host).

<!-- mios-src:cda6204b9040 from Get-MiOS.ps1:2941-2952 -->

### Stale-shortcut cleanup -- canonical 4-shortcut set is MiOS...

Stale-shortcut cleanup -- canonical 4-shortcut set is
MiOS / MiOS-WIN / MiOS Help / Uninstall MiOS (created above).
Every OTHER variant a prior revision shipped gets reaped so
re-running Get-MiOS.ps1 normalizes the menu. NOTE: MiOS-DEV.lnk
is reaped because the canonical "MiOS.lnk" already targets the
dev VM (no second shortcut for the same target). MiOS Config.lnk
is reaped because `mios config` is a typed verb inside the terminal.

<!-- mios-src:02d664fc05f1 from Get-MiOS.ps1:2980-2986 -->

### DisplayName resolves through mios.toml...

DisplayName resolves through mios.toml [branding].tagline_app
(per 'the Applications tag/description
when installed "MiOS - Immutable Fedora AI Workstation"
should be defined as My Personal Operating System or similar').
The technical descriptor "Immutable Fedora AI Workstation"
remains in the dashboard subtitle for in-terminal context;
the OS-wide app face (this DisplayName, .lnk descriptions,
AppX manifest) uses the operator-friendly tagline.

<!-- mios-src:66da70192690 from Get-MiOS.ps1:3070-3077 -->

### Explicit Windows Start Menu shortcuts for MiOS web...

Explicit Windows Start Menu shortcuts for MiOS web services. WSLg's
auto-publish heuristic filters out the 10 mios-svc-*.desktop files
MiOS ships in /usr/share/applications/ (Categories=System;Network;
Settings; + Exec=xdg-open URL doesn't fit WSLg's app model).
Operator-confirmed 0 of 10 mios-svc-* entries surfaced
as Windows shortcuts despite clean Type=Application + NoDisplay=false.

TOML-first per AGENTS.md §3 -- iterates mios.toml [desktop.start_menu]
`publish` list and reads <key>_label, <key>_scheme, <key>_port_key
for each entry. Resolves the port from [ports].<port_key>. Writes
one .url Internet shortcut per entry into
  %APPDATA%\Microsoft\Windows\Start Menu\Programs\podman-MiOS-DEV\
so they land in the same Start Menu folder WSLg uses for the
2 apps it does auto-publish (gnome-software, winemine).

Idempotent: rewrites the .url body each pass; safe to re-run.
Operator removes by dropping a key from `publish` (existing .url
persists until Pass-0 reap or manual delete).

<!-- mios-src:6d77937736f2 from Get-MiOS.ps1:3125-3142 -->

### Vendor content blobs (branding ASCII / fastfetch config /...

========================================================================
Vendor content blobs (branding ASCII / fastfetch config / oh-my-posh
theme) USED to be embedded as heredocs in this script.  They drifted
from upstream mios.git on every iteration and produced stale
powerline glyphs / ASCII art / fastfetch logos at install time --
"you are hardcoding mios build to build a
smaller version of itself that you've embedded in the actual codebase
and THAT's where it's sourcing from!! MiOS is completely self
developing, self building, self hosted... ALL values source from the
toml".

Get-MiosVendorContent resolves vendor content from mios.git origin
(raw.githubusercontent.com).  WEB ONLY -- no local fallback.

Per operator architectural rule

  "ORIGIN = web entries/repos only -- no fallback to M:\ or
   anywhere else -- unless origin has been pulled and it's a
   simple 'mios build' -- that can pull from M:\ as it'd already
   exist -- then 'mios update' would ALWAYS pull from web
   regardless of clean entry, updating, etc-etc!!!"

Get-MiOS.ps1 is BOTH the clean `irm | iex` entry AND what `mios
update` re-fetches.  Both must hit the web -- never M:\, never
/usr/share/mios, never %USERPROFILE%.  M:\ overlays exist for build-mios.ps1
/ `mios build` to read AFTER mios-pull has populated them; the
bootstrap itself ALWAYS forces a fresh fetch.  Mixing the two
would let a stale M:\ silently override the web pull, defeating
the "clean entry forces refresh" guarantee.

Hard-fail with a clear error rather than falling back to a stale
snapshot.  No embedded heredocs, no M:\ cache, no on-disk dev
tree -- nothing but origin.
========================================================================

<!-- mios-src:36c8751f33bd from Get-MiOS.ps1:3181-3214 -->

### Use Invoke-WebRequest, NOT Invoke-RestMethod. IRM...

Use Invoke-WebRequest, NOT Invoke-RestMethod.  IRM
auto-deserializes any JSON response into a PSCustomObject; for
vendor content like mios.omp.json we need RAW TEXT.  IRM
produced an 867-byte stringified-PSCustomObject (instead of
the 10.9 kb omp.json source) and broke the downstream
GetBytes() call with "Cannot find an overload" because the
argument was an object, not a string.  IWR returns the raw
response body as a string (or byte[] for binary), which we
then UTF-8-decode if it came back as bytes -- so PUA glyphs
(U+E0B4 / U+E0B6) in mios.omp.json survive end-to-end.

<!-- mios-src:6e1f6e541e97 from Get-MiOS.ps1:3233-3242 -->

### Open-source terminal-completion + UX enhancers. PowerShell...

Open-source terminal-completion + UX enhancers. PowerShell
modules come from PSGallery (Install-Module); CLI tools come
from winget. Net effect: every MiOS shell session gets:

  * Terminal-Icons          -- file/folder icons in `ls` output
  * posh-git                -- git tab-completion + branch info
  * CompletionPredictor     -- AI-style predictive completion
  * WinGet.CommandNotFound  -- "did you mean: winget install X?"
                               when an unknown command is typed
  * sharkdp.bat             -- syntax-highlighted `cat` replacement
  * junegunn.fzf            -- fuzzy finder (Ctrl-T, Ctrl-R)
  * GitHub.cli              -- `gh` CLI for github operations

All idempotent: probes existing install before re-installing.

MUST run under PowerShell 7+, not Windows PowerShell 5.1:
  * PS 5.1 ships PowerShellGet 1.0.0.1, which can resolve Install-Module
    as a *command* but fails to load the *module* dependency graph
    (NuGet PackageProvider) -- the operator-visible error is
    "Install-Module was found in PowerShellGet, but the module could
    not be loaded". Force-Import + bootstrapping NuGet doesn't fully
    fix this on a fresh 5.1 install.
  * CompletionPredictor + Microsoft.WinGet.CommandNotFound require
    PS 7+ at *runtime* anyway (they use the PSReadLine 2.2 predictor
    API only available in pwsh 7).
  * PS 5.1 and PS 7 have SEPARATE per-user module paths
    (~/Documents/WindowsPowerShell/Modules vs ~/Documents/PowerShell/Modules)
    -- installing from 5.1 wouldn't help pwsh 7 see them at runtime.

If launched via `powershell` (5.1), trampoline this step through
pwsh.exe so installs land in pwsh 7's user-module path.

<!-- mios-src:12cb66ed71e3 from Get-MiOS.ps1:3262-3292 -->

### NOTE

NOTE: when invoked via the trampoline below, this script's stdout is
captured by the parent (Windows PowerShell 5.1) and CLIXML-serialized
because pwsh 7 sends Write-Host through the PSHost information stream.
Use [Console]::WriteLine instead -- raw stdout bypasses the PSHost
serializer entirely, so the parent sees plain text. Cost: no color in
the trampolined branch (acceptable -- the in-process branch still
uses Write-Host with color).

<!-- mios-src:3187455241fb from Get-MiOS.ps1:3306-3312 -->

### SSOT

SSOT: package list comes from the layered mios.toml chain.
Per operator "ALL Global packages SOURCE FROM THE TOML/HTML
FILE!!!" + "now how does changing the html change the toml
thats read by multiple scripts and components".

Layered resolution order (highest → lowest precedence):
  1. M:\etc\mios\mios.toml          -- HOST overlay (where the
                                       Epiphany configurator
                                       saves; visible to BOTH
                                       Windows AND MiOS-DEV via
                                       /mnt/m/etc/mios/mios.toml)
  2. M:\usr\share\mios\mios.toml    -- VENDOR copy on M:\ if
                                       Phase 2 already cloned it
  3. raw.githubusercontent.com mios.git origin/main  -- COLD
                                       first-run path (no M:\
                                       yet)

Each layer is checked; the first that yields a non-empty
[packages.windows] pkgs = [...] wins. This makes Pass 1 see
user edits made via the HTML configurator the moment they're
saved, the same way the Linux side sees them via /etc/mios/.

<!-- mios-src:85c9055ad033 from Get-MiOS.ps1:3356-3376 -->

### winget install/upgrade oh-my-posh to latest....

winget install/upgrade oh-my-posh to latest. Operator-reported
"Get-PSReadLineKeyHandler Spacebar / Enter / Ctrl+c" positional
parameter errors come from oh-my-posh's init script emitting the
legacy positional syntax that no PSReadLine version accepts.
Latest oh-my-posh emits -Chord <key> -- the correct named-parameter
syntax. So bumping oh-my-posh fixes the init errors at the source.

<!-- mios-src:e7e1713aa48f from Get-MiOS.ps1:3457-3462 -->

### oh-my-posh's init pwsh emits Get-PSReadLineKeyHandler calls...

oh-my-posh's init pwsh emits Get-PSReadLineKeyHandler calls that
use named parameters (Get-PSReadLineKeyHandler -Chord Spacebar).
The version of PSReadLine that ships in PowerShell 7.6's box is
too old to accept those args -- it expects positional, and emits
"A positional parameter cannot be found that accepts argument
'Spacebar'/'Enter'/'Ctrl+c'". This breaks oh-my-posh init, which
then leaves the prompt in a fallback state.

Fix: install/update PSReadLine via PowerShellGet to >= 2.3.5.
Per-user (-Scope CurrentUser) so we don't need elevation.

<!-- mios-src:fa9deff73bf0 from Get-MiOS.ps1:3488-3497 -->

### winget install fastfetch + stage MiOS-themed config and...

winget install fastfetch + stage MiOS-themed config and ASCII
logo at M:\MiOS\fastfetch\ (or LOCALAPPDATA fallback). The PS
profile invokes `fastfetch -c <staged>` on every MiOS shell
session start so the operator sees a MiOS-branded MOTD.

<!-- mios-src:bb34a542ebc2 from Get-MiOS.ps1:3521-3524 -->

### Stage the config + logo on M:\ (M:\-everywhere invariant --...

Stage the config + logo on M:\ (M:\-everywhere invariant -- no
LOCALAPPDATA fallback; Initialize-DataDisk creates M:\ before
any MiOS staging runs).

<!-- mios-src:bd4b101f8f80 from Get-MiOS.ps1:3558-3560 -->

### MUST write the JSONC config without a UTF-8 BOM....

MUST write the JSONC config without a UTF-8 BOM. fastfetch's
JSON parser is strict and rejects files starting with EF BB BF
("Error: failed to parse JSON config file"). Set-Content
-Encoding UTF8 prepends a BOM on Windows PowerShell 5.1 and
pwsh's "UTF8" alias too. Use System.IO.File.WriteAllText with
an explicit no-BOM encoding to match what fastfetch expects.

<!-- mios-src:a2823b2e7635 from Get-MiOS.ps1:3570-3575 -->

### Color substitution from mios.toml [theme.fastfetch] (SSOT)...

-- Color substitution from mios.toml [theme.fastfetch] (SSOT) --
Per "oh my posh and other settings should
source from the same toml sections for all platform for theme/
branding to be truly unified in code".  fastfetch's per-module
color overrides (logo / keys / title / output) ship with vendor-
default ANSI tags that match [theme.fastfetch] vendor defaults;
operator overrides via mios.html flow into every MiOS terminal
without touching this script.  Only fires when the resolved
value differs from vendor and is one of fastfetch's accepted
ANSI color names.

<!-- mios-src:51f7e92b0d9a from Get-MiOS.ps1:3589-3598 -->

### Per the M:\-everywhere invariant: the actual oh-my-posh...

Per the M:\-everywhere invariant: the actual oh-my-posh init
script lives at M:\MiOS\powershell\profile.ps1. The C:\ user
profile ($PROFILE.CurrentUserAllHosts) gets a tiny redirector
block that dot-sources the M:\ script -- so the operator can
edit the M:\ copy and every PS shell picks up changes on next
launch, without bouncing through C:\.

<!-- mios-src:3a58f5281b1f from Get-MiOS.ps1:3650-3655 -->

### Write the FULL oh-my-posh init script to...

Write the FULL oh-my-posh init script to M:\MiOS\powershell\profile.ps1.
The C:\ user profile only gets a thin redirector that dot-sources
this file -- so future edits to the M:\ copy take effect on next
shell launch with no C:\ round-trip.
Build the M:\ profile script. Self-heals every embedded artifact
(oh-my-posh config + fastfetch config + MiOS ASCII logo) on
dot-source if the file isn't already staged on disk -- so even
an operator who irm|iex'd an older Get-MiOS.ps1 without these
stages gets a fully-themed MiOS terminal on the next pwsh launch.

<!-- mios-src:e869e467289e from Get-MiOS.ps1:3675-3683 -->

### Lift terminal dims from mios.toml [terminal] (per...

Lift terminal dims from mios.toml [terminal] (per
feedback_mios_toml_html_global_dotfile -- mios.toml is THE
global dotfile). Vendor defaults: 80x30 (operator-defined MiOS
default) with frame at cols-1 / rows-1 so the dashboard fits
inside the borderless + scrollbar-less terminal without the
right border colliding with the line-wrap boundary.

<!-- mios-src:0cbe9379632f from Get-MiOS.ps1:3687-3692 -->

### frame_width default is COLS - 1 per operator "everything...

frame_width default is COLS - 1 per operator "everything should be
-1 width" -- 1-cell gutter on the right edge prevents the frame
from line-wrapping when WT reports WindowWidth one cell over
visible. mios.toml [terminal].frame_width is the SSOT; the
configurator HTML exposes this for operator override.
frame_height stays rows-1 so one row is reserved for the prompt.

<!-- mios-src:4f2ccf770c05 from Get-MiOS.ps1:3696-3701 -->

### right_margin

right_margin: cells of slack between the rightmost paintable cell
and the rightmost cell the dashboard frame / right-aligned prompt
block writes to. Default 2 because the operator reported "framing
too wide STILL" with the previous cols-1 (1 cell) margin -- WT's
pseudo-console reports WindowWidth 1 cell over the visible/
paintable cell count during the first paint (before the
scrollbarState='hidden' setting and its scrollbar-reservation
release have taken effect). cols-2 always avoids wrap.

<!-- mios-src:0bb2493b0c57 from Get-MiOS.ps1:3704-3711 -->

### EULA pre-print lines (mios.toml [messages.eula])...

-- EULA pre-print lines (mios.toml [messages.eula]) -------------
Read the toml once at install time and bake the resolved lines
as a literal PS array into the heredoc.  Operator edits via
mios.html flow on the next `mios update` re-run.  Get-MiosTomlValue
can't parse multi-line array values (its key regex doesn't span
lines), so use an inline DOTALL match here.

<!-- mios-src:71b1918c52a6 from Get-MiOS.ps1:3722-3727 -->

### MiOS PowerShell profile -- PSReadLine reload + fastfetch...

MiOS PowerShell profile -- PSReadLine reload + fastfetch MOTD +
oh-my-posh init.
Source of truth: this file lives on M:\ and is dot-sourced from
`$PROFILE.CurrentUserAllHosts AND from the WT MiOS profile's
explicit -Command preamble (so it ALWAYS runs in MiOS terminals,
even when the operator's $PROFILE has its own broken oh-my-posh
init that would otherwise override ours).
Self-heals every artifact (mios.omp.json, fastfetch config.jsonc,
mios.txt ASCII logo) from embedded base64 blobs if the canonical
disk copy is missing.

<!-- mios-src:bb11ccac7329 from Get-MiOS.ps1:3783-3792 -->

### ONCE-PER-SESSION GUARD. This script is dot-sourced from...

ONCE-PER-SESSION GUARD. This script is dot-sourced from BOTH
(a) the redirector in `$PROFILE.CurrentUserAllHosts AND
(b) the WT MiOS profile's -Command preamble.
Without this guard, both pathways fire Show-MiosDashboard +
oh-my-posh init -- the operator sees TWO stacked framed
dashboards. Session-scoped flag short-circuits subsequent calls.

<!-- mios-src:1c6ff969906d from Get-MiOS.ps1:3794-3799 -->

### UTF-8 codepage + Console encoding...

-- UTF-8 codepage + Console encoding ------------------------------
Operator-reported regression: powerline glyphs (U+E0B4 etc.) rendered
as 'î' mojibake -- WT was decoding the UTF-8 bytes as cp1252 because
this profile body wasn't setting chcp 65001 / Console.OutputEncoding.
Setting both ensures every glyph oh-my-posh emits to stdout renders
as the correct PUA cap, not the cp1252-mangled multi-char sequence.

<!-- mios-src:de3b7e90f5cf from Get-MiOS.ps1:3803-3808 -->

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
via `$PROFILE.CurrentUserAllHosts redirector -- the resize shrinks
the operator's 80x40 install conhost down to the 80x20 MiOS-app
size mid-install. Operator-reported regression: "window changes to
the MiOS Global sizes of 80x20 somewhere in the middle of the
installations". `$env:MIOS_APP_CONTEXT is set ONLY by the WT MiOS
profile commandline (see Install-MiOSTerminalProfile in Get-MiOS.ps1).

<!-- mios-src:a58a80b19583 from Get-MiOS.ps1:3814-3831 -->

### Center on the ACTIVE display (where the cursor currently...

Center on the ACTIVE display (where the cursor currently is),
NOT PrimaryScreen. On multi-monitor hosts the operator launches
mios.bat from whichever monitor they're working on; the window
should land THERE.

<!-- mios-src:c51cf90c00bc from Get-MiOS.ps1:3857-3860 -->

### NO TERMINAL-TYPE GATE. Always run the PSReadLine reload +...

NO TERMINAL-TYPE GATE. Always run the PSReadLine reload + oh-my-
posh init. The WT_SESSION gate on the previous version was
silently skipping the init when WT didn't set the env var early
enough -- producing the "theme works in normal terminal but not
MiOS Terminal" symptom. fastfetch is gated separately below
since its ASCII rendering only makes sense in a real terminal.

<!-- mios-src:f75af3f01364 from Get-MiOS.ps1:3869-3874 -->

### Import terminal completion modules ------------------------...

-- Import terminal completion modules ------------------------
Silent best-effort: each module is imported if installed,
skipped if not. Operator gets icon-aware ls (Terminal-Icons),
git tab-completion (posh-git), AI-style prediction
(CompletionPredictor), and command-not-found suggestions
(Microsoft.WinGet.CommandNotFound).

<!-- mios-src:043085015bd9 from Get-MiOS.ps1:3877-3882 -->

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

<!-- mios-src:627f8a3882c9 from Get-MiOS.ps1:3889-3897 -->

### Resolve / self-heal MiOS artifact paths -------------------...

-- Resolve / self-heal MiOS artifact paths -------------------
M:\-everywhere invariant (operator: "irm|iex sets up M:\
disk/partition installs EVERYTHING to M:\ EVERYTHING").
M:\ is created at install time and never removed at runtime;
if it's missing, the install never completed and the operator
needs to re-run irm|iex.  The profile body falls back to a
warn rather than silently splitting state across drives.

<!-- mios-src:0a628376c8eb from Get-MiOS.ps1:3906-3912 -->

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

<!-- mios-src:59fe70446c90 from Get-MiOS.ps1:3951-3962 -->

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

<!-- mios-src:ba428f47444e from Get-MiOS.ps1:3977-3988 -->

### Uniform frame color -- per "make the entire frame 1 uniform...

Uniform frame color -- per "make the
entire frame 1 uniform colour--make it a complimenting colour
to the windows colour that's sourced from the toml fields that
are relevant to MiOS's color palette colours". MiOS canonical
accent (mios.toml [colors].accent + [branding.dashboard].frame_color)
is operator-blue (#1A407F = ANSI 34 = [ConsoleColor]::Blue).
Embed ANSI 34 around every `$V` border so the per-content rows
render their borders in the SAME color as the standalone
top/divider/bottom Write-Host calls (which use
-ForegroundColor Blue). Without this, _Frame/_Center returned
a plain string that Write-Host emitted in the inherited
foreground (often cream from the MiOS scheme), making per-row
borders visually different from top/divider/bottom borders.

<!-- mios-src:ba1dda753b39 from Get-MiOS.ps1:3996-4008 -->

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

<!-- mios-src:81af7065bbf6 from Get-MiOS.ps1:4034-4044 -->

### 1-line title band -- resolves through mios.toml...

1-line title band -- resolves through mios.toml [dashboard].title
at runtime so the configurator HTML edits flow through to the
next render.  Vendor default is the technical descriptor
("MiOS  --  Immutable Fedora AI Workstation"); operators who
want the friendly "My Personal Operating System" face on the
dashboard subtitle override [dashboard].title via mios.html.

<!-- mios-src:ef6ccf97690d from Get-MiOS.ps1:4072-4077 -->

### Centered ASCII logo (operator-blue). Center the BLOCK (not...

Centered ASCII logo (operator-blue). Center the BLOCK (not
each line individually) -- the logo's internal alignment
depends on each line's leading whitespace.

<!-- mios-src:c746e415640d from Get-MiOS.ps1:4086-4088 -->

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
cached) / Get-Volume / `$PSVersionTable.  They each return a
short labeled string ("CPU AMD Ryzen 9 9950X3D 5.75GHz (32c)").
Unknown field-keys are silently skipped so the dashboard
is forward-compatible with future mios.toml additions.

<!-- mios-src:3a5f4b913efc from Get-MiOS.ps1:4109-4123 -->

### Compact OS caption

Compact OS caption: strip Microsoft prefix, the
"for Workstations" SKU suffix, "Insider Preview"
marketing, "(64-bit)" arch (it's redundant -- the
arch line covers it), and trailing whitespace.
Operator-flagged "Windows 11 Pro for
Workstations Insider Preview" overflowed the 80x20
frame and wrapped, pushing the top frame off-screen.

<!-- mios-src:9836fb8614e9 from Get-MiOS.ps1:4133-4139 -->

### PowerShell switch with regex condition matches but does NOT...

PowerShell switch with regex condition matches but
does NOT reliably populate `$Matches in the action
block scope -- saw `disk_c : err`
in the dashboard because `$Matches[1]` was \$null and
`$_dl` came back empty.  Parse the letter from `$_
directly via Substring instead.

<!-- mios-src:49255ef7ec38 from Get-MiOS.ps1:4196-4201 -->

### Try/catch per-field so a single broken renderer (e.g....

Try/catch per-field so a single broken renderer
(e.g. Get-Volume not available, lspci missing) doesn't
kill the whole loop -- saw the
dashboard render only the first 3 rows and bail because
the disk_c renderer's Get-Volume call raised in a
context where the Storage module wasn't loaded.

<!-- mios-src:a61fe3580b59 from Get-MiOS.ps1:4277-4282 -->

### MiOS services block ----------------------------------...

-- MiOS services block ----------------------------------
refresh: parity with the Linux-side
mios-dashboard.sh services grid. Each cell is a
<dot> <name> :<port> probe row. Endpoints reachable from
the Windows host go through localhost (WSL2's
localhostForwarding mirrors the dev VM's listening sockets
to the Windows loopback automatically). When a probe
fails -- service is down OR forwarding misses (a known
WSL2 networking flake) -- we show the dot as off and
carry the row anyway so the layout stays stable.

<!-- mios-src:0b7a39fc6c7a from Get-MiOS.ps1:4304-4313 -->

### Command hints rows ----------------------------------- Verb...

-- Command hints rows -----------------------------------
Verb list resolves through mios.toml [verbs] at RUNTIME (SSOT).
The dashboard re-reads on every render so an operator edit via
mios.html flows mios.toml -> dashboard immediately. No hard-
coding here. Vendor fallback only if every TOML candidate is
missing (cold first-run before M:\ overlay is staged).

<!-- mios-src:b7ac6bb2026b from Get-MiOS.ps1:4366-4371 -->

### NO inline-render here. The profile body is a thin function-...

NO inline-render here. The profile body is a thin function-
definition layer; the "what shows up on terminal spawn" is
whatever verb mios.toml [terminal.startup].windows points at.
The dispatch fires AT THE END of this profile (after the `mios`
verb function is defined). See the [terminal.startup] block
below the function definitions.
"have the bash and pwsh/WT environment/
dotfile(s) automatically run mios dash on open/launch--NOT
PRINT ON LAUNCH!!! THE ACTUAL ENV/DOTFILE(S) SHOULD DICTATE THE
COMMANDS/VERBS AND WHATS RUN ON CONSOLE SPAWN(ALL PLATFORMS
GLOBALLY)--ALL SOURCED FROM THE MIOS.TOML"

<!-- mios-src:a979639481c5 from Get-MiOS.ps1:4419-4429 -->

### oh-my-posh init -------------------------------------------...

-- oh-my-posh init -------------------------------------------
Capture the init script output, then regex-patch the broken
positional Get-PSReadLineKeyHandler calls. Older oh-my-posh
versions emit `Get-PSReadLineKeyHandler Spacebar` etc. -- which
NO PSReadLine version accepts (the cmdlet's parameter binder
has no positional [string]). Latest oh-my-posh emits -Chord
<key>. We inject -Chord even when running latest, since it's
idempotent (latest already has it). This makes oh-my-posh's
PSReadLine integration work regardless of installed version.

<!-- mios-src:b9e9a2dab119 from Get-MiOS.ps1:4431-4439 -->

### Shell-aware

Shell-aware: oh-my-posh init pwsh emits PS 7+ syntax that
FAILS silently in Windows PowerShell 5.1, leaving the
operator's pre-existing broken init showing "CONFIG NOT
FOUND". Detect PS edition and use the matching arg
(`powershell` for 5.1 / Desktop, `pwsh` for 7+ / Core).

<!-- mios-src:50d02e92b4a1 from Get-MiOS.ps1:4441-4445 -->

### MiOS commands...

-- MiOS commands ---------------------------------------------------
Defined in EVERY pwsh session (not gated on WT_SESSION) so the
operator can run mios-build / mios-update / mios-help from any shell.
Each command fetches its target script fresh from
raw.githubusercontent.com so the operator doesn't have to manually
pull the mios-bootstrap repo. Cache-busting via ?cb=<unix-time>
defeats Fastly's 5-minute max-age.

<!-- mios-src:abd471681485 from Get-MiOS.ps1:4459-4465 -->

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

<!-- mios-src:3d9f11995dde from Get-MiOS.ps1:4472-4486 -->

### Capture mtime BEFORE opening so we can tell if the operator...

Capture mtime BEFORE opening so we can tell if the operator
actually saved a new copy (the browser saves to Downloads
because file:// URLs can't write back to source). Used by
the promote step below.

<!-- mios-src:9747fca8084a from Get-MiOS.ps1:4499-4502 -->

### Step 2

-- Step 2: promote downloaded mios.toml from Downloads ----
The browser saves to %USERPROFILE%\Downloads (file:// URLs
can't write back to source). Scan for any mios*.toml /
*mios*.html newer than the in-place overlay copies and
PROMOTE them to M:\etc\mios\ + M:\usr\share\mios\configurator\.
Also archive the imported source so we don't double-promote
on the next mios-build run.

<!-- mios-src:233519a7a866 from Get-MiOS.ps1:4518-4524 -->

### Archive the source so a re-run of mios build doesn't...

Archive the source so a re-run of mios build doesn't
re-promote the same file. Keep it (don't delete) so
the operator can recover if something went wrong.

<!-- mios-src:70c617394dd5 from Get-MiOS.ps1:4549-4551 -->

### Step 3

-- Step 3: sync overlay so the build sees the latest mios.toml -
Note: this runs AFTER the Downloads-promote step so mios-pull
sees the just-promoted files in M:\etc\mios. mios-pull's git
reset --hard would otherwise blow away the operator's changes
if they lived in the tracked tree.

<!-- mios-src:db73e315cd01 from Get-MiOS.ps1:4579-4583 -->

### MINI dashboard -- the compact 80x20 framed banner +...

MINI dashboard -- the compact 80x20 framed banner + fastfetch
info. This is what fires on every shell spawn (vendor default
of [terminal.startup].verb). "have launch
be the mini-dashboard ... NOT PRINT ON LAUNCH" -- the dotfile
dispatches THIS verb so the render comes from a verb command,
not inline-print in the profile body.

<!-- mios-src:5f7256caea1f from Get-MiOS.ps1:4664-4669 -->

### FULL MiOS dashboard -- ASCII banner + fastfetch (full...

FULL MiOS dashboard -- ASCII banner + fastfetch (full width,
no compact frame trim) + MiOS-DEV service status + extended
sys specs. "the invoked 'mios dash'
command(s) runs the FULL MiOS dashboard; showing all service's
and relevant MiOS system specs too--include the MIOS ASCII
banner in the full dash!"

<!-- mios-src:d037c96248d1 from Get-MiOS.ps1:4680-4685 -->

### Unified `mios <verb>` dispatcher. Operator types `mios...

Unified `mios <verb>` dispatcher. Operator types `mios build` or
`mios b<TAB>` (PSReadLine + the ArgumentCompleter below complete to
`mios build`). Falls through to `mios-<verb>` so the same wrappers
back both call shapes.
Known verbs dispatch to mios-<verb>.ps1 wrappers in `$Global:MiosBin.
Anything that isn't a known verb is routed to Hermes-Agent at
MIOS_AI_ENDPOINT as a chat completion, so `mios how do I bootc switch`
works from any PowerShell terminal without a separate `ask` verb.

<!-- mios-src:d17615a3692f from Get-MiOS.ps1:4773-4780 -->

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
  - `$env:MIOS_SKIP_MOTD = "1"      -> no startup verb fires.
  - non-interactive host           -> no fire (background scripts,
                                      VS Code's PowerShell extension
                                      integrated terminal, etc.).
  - `$Global:MiosStartupVerbFired   -> idempotent across re-sources
                                      (mios.ps1 dot-sources this
                                      profile to load functions, we
                                      don't want a recursive verb
                                      call inside an already-running
                                      verb).

<!-- mios-src:a6a0339ccc0a from Get-MiOS.ps1:4818-4839 -->

### Vendor fallback

Vendor fallback: mini (the compact 80x20 framed banner).
`dash` is the FULL render -- ASCII banner + service status +
extended sys specs -- explicitly invoked by the operator,
not auto-fired on every shell spawn.

<!-- mios-src:c0226c9181ad from Get-MiOS.ps1:4861-4864 -->

### Write the profile body with explicit UTF-8 BOM. The body...

Write the profile body with explicit UTF-8 BOM. The body contains
Unicode box-drawing chars (+ + + + | - + +) for the dashboard
frame; without a BOM, PowerShell falls back to system codepage
(CP1252 on US Windows) when reading no-BOM files in some
contexts, parsing each UTF-8 byte as a separate Latin-1 char
and exploding with "Unexpected token 'â”€'" at parse time.
[IO.File]::WriteAllText with UTF8Encoding($true) writes the
3-byte 0xEF 0xBB 0xBF BOM up front so EVERY PS host (5.1, 7.x,
ISE, VS Code) decodes the file as UTF-8 deterministically.

<!-- mios-src:f4cea0117e2a from Get-MiOS.ps1:4876-4884 -->

### Append a diagnostic block to M:\MiOS\powershell\profile.ps1...

Append a diagnostic block to M:\MiOS\powershell\profile.ps1 that
writes [Console]::WindowWidth + BufferWidth + LASTEXITCODE-style
context to M:\MiOS\diagnostics\window-width.txt at every profile
load. Operators (and the AI agent debugging wrap issues) can read
this file to know the EXACT cell count WT is reporting on the
operator's hardware -- no more guessing right_margin values from
screenshots. Re-runs append (with timestamp) so we get a history
across MiOS WT launches. Per operator's 5-hour iteration spiral
STOP guessing margin values, measure the actual width.

<!-- mios-src:c7a4de39bb54 from Get-MiOS.ps1:4912-4920 -->

### MiOS WindowWidth diagnostic (auto-appended by...

-- MiOS WindowWidth diagnostic (auto-appended by Install-MiOSPowerShellProfile) --
Every MiOS pwsh launch appends one line to M:\MiOS\diagnostics\window-width.txt
capturing [Console]::WindowWidth + BufferWidth + WT_SESSION + timestamp.
This is the SOURCE OF TRUTH for the actual visible cell count on the
operator's hardware -- if WindowWidth != mios.toml [terminal].cols, the
delta is the WT chrome budget that right_margin must absorb.

<!-- mios-src:b38ecb3d63ba from Get-MiOS.ps1:4923-4928 -->

### DPI-aware centered position for an 80x30 acrylic focus-mode...

DPI-aware centered position for an 80x30 acrylic focus-mode window.

Cell metrics (Geist Mono 12pt @ 100% DPI, lineHeight=1.0): ~10 × 20 px
→ grid 800 × 600 px → 4:3 exactly.

Window-level slack (DWM frame + scrollbar + acrylic edge in focus mode):
+20 px width, +12 px height. So the wt.exe outer rect is ~820 × 612 px
at 100% DPI on a typical Win11 build.

Robustness layers:
  1. SetProcessDPIAware() -- without this, on 125%/150% scaled displays
     Screen.WorkingArea returns LOGICAL pixels and our --pos math is
     off by the scale factor (window lands top-left).
  2. Cursor-monitor detection -- PrimaryScreen always sends the window
     to display #1 even when the operator is on display #2. Use
     Screen.FromPoint(Cursor.Position) so the window opens on whichever
     monitor the operator is actively using.
  3. Post-launch correction -- wt.exe sometimes ignores --pos in focus
     mode (1.18+ regression). Move-MiOSWindowToCenter (called from the
     relaunch path after Start-Process) finds the WT hwnd and moves it
     to the true center. This is the belt-AND-braces guarantee that
     'exit' is type-able because the window is on-screen.

<!-- mios-src:73c77a41c134 from Get-MiOS.ps1:4948-4969 -->

### Post-launch re-center

Post-launch re-center: WT in focus mode sometimes lands at (0,0) or at
the previous WT window's last position because it ignores --pos. We
wait up to ~3s for a WindowsTerminal.exe process to surface a top-level
hwnd, GetWindowRect to read its real outer-rect size, then SetWindowPos
to (screenCenter - rect/2). This guarantees the window is exactly
screen-center regardless of what WT did with --pos.

<!-- mios-src:f71d462c5bcf from Get-MiOS.ps1:5001-5006 -->

### IMPORTANT

IMPORTANT: do NOT strip WS_THICKFRAME / WS_CAPTION via
SetWindowLongPtr -- DWM's acrylic compositor REQUIRES those style
bits to allocate the per-window swap chain that backs the blur
surface. Earlier revisions stripped them for "completely
borderless" -- and the cost was no acrylic at all (the window
rendered as a flat black popup). The WT-side `--focus` flag +
padding=0 + suppressApplicationTitle gives the closest-to-
borderless WT can deliver while keeping acrylic alive: a 1px
DWM resize frame remains, but the titlebar / tab row / min-max
buttons are all gone.

<!-- mios-src:02ad7ce8863e from Get-MiOS.ps1:5037-5046 -->

### Re-center 3 times with 350ms gaps. WT in focus mode often...

Re-center 3 times with 350ms gaps. WT in focus mode often animates
the window to its last-known position AFTER the first SetWindowPos
registers, then settles. A single move loses the race; three
spaced-out moves stick. Each iteration re-reads the outer rect
(size can shift slightly during animation) so center math is
always against the current dimensions.

<!-- mios-src:1cabe03bd558 from Get-MiOS.ps1:5048-5053 -->

### By the time we reach this point we're GUARANTEED admin --...

By the time we reach this point we're GUARANTEED admin -- the
auto-elevation block at the top of the script (right after the
agreement-gate function definition) returned out of Pass-1 if the
operator pasted from a non-admin shell, and only Pass-2 (the elevated
relaunch) ever falls through to here. Code below runs in Pass-2 only.

<!-- mios-src:d5f31b3c7265 from Get-MiOS.ps1:5073-5077 -->

### Bootstrap winget (Microsoft.DesktopAppInstaller) on a...

Bootstrap winget (Microsoft.DesktopAppInstaller) on a Windows host
that doesn't ship it. Win11 has it preinstalled; Win10 22H2 also
ships it; but Windows Server, Sandbox, debloated images, and very
fresh OOBE machines sometimes don't. winget is the prerequisite
for every package install downstream (WSL, Podman, Windows Terminal,
PowerShell 7, oh-my-posh, fastfetch, etc.) so failing here means
NOTHING else installs.

Operator directive "Make sure the irm|iex installer
can STILL install on a fresh Windows System with NOTHING installed".

<!-- mios-src:b5ffbad1c394 from Get-MiOS.ps1:5094-5103 -->

### Detect + enable the OS-level features MiOS needs...

Detect + enable the OS-level features MiOS needs:
  Microsoft-Windows-Subsystem-Linux   -- WSL substrate
  VirtualMachinePlatform              -- WSL2 (HCS) + Hyper-V hypervisor
  Microsoft-Hyper-V                   -- Hyper-V Manager + VMs (Pro/Ent)

All require admin (DISM-level feature toggles). Caller is responsible
for admin context -- Get-MiOS.ps1 self-elevates via UAC before any
call site reaches this function, so we hard-fail with a clear message
rather than silently skipping if we somehow land here as a normal user.

"pwsh7+, podman, wsl, hyper-v, etc-etc are all
fecthed and installed during irm|iex installations -- THE FIRST
STEPS AFTER DISK CREATION". This function is Step 0.6 in Pass-2,
immediately after Initialize-DataDisk + Set-PodmanMachineStorageOnM
+ Set-WingetStorageOnM + mios.toml promotion to M:\.

Reboot policy: enables with -NoRestart and aggregates which features
required a reboot. Surfaces a clear warning at the end if any
reboot is pending; doesn't reboot automatically (operator-flagged:
NO automatic mid-install reboots).

<!-- mios-src:0ed8f61076f9 from Get-MiOS.ps1:5160-5179 -->

### TOML-first per AGENTS.md §3 -- feature DISM names resolve...

TOML-first per AGENTS.md §3 -- feature DISM names resolve from
mios.toml [bootstrap.prereqs.features].* so operators can swap
Hyper-V for Hyper-V-Core, add Containers/SMBDirect, etc., via
mios.html. Order matters (WSL substrate before VMP before Hyper-V);
use [ordered] to preserve insertion order. Build via .Add() (void
return) instead of $features[k]=v to avoid any indexer-emit leak
into the function's pipeline output (operator-confirmed
the indexer-assignment form leaked the assigned value into the
function's return stream, making `$_featResult -eq 'reboot-required'`
filter-match a multi-element array even when rebootPending stayed
$false -- spurious Pass-2 halt despite all 3 features already
Enabled).

<!-- mios-src:1dcf45df54a8 from Get-MiOS.ps1:5186-5197 -->

### Get-WindowsOptionalFeature threw. Either the feature...

Get-WindowsOptionalFeature threw. Either the feature genuinely
isn't on this edition (e.g. Hyper-V on Home), OR the legacy
optional-feature name no longer exists because WSL is now
Store-distributed (WSL 2.x MSIX needs only VirtualMachinePlatform,
not the deprecated 'Microsoft-Windows-Subsystem-Linux' feature).
If wsl.exe already works, the substrate is satisfied regardless
of optional-feature state -- don't emit a scary "not available".

<!-- mios-src:8ed7ac7e23de from Get-MiOS.ps1:5209-5215 -->

### WSL bootstrap on fresh Windows...

--- WSL bootstrap on fresh Windows ----------------------------------
Fresh Windows 11 doesn't ship wsl.exe -- it's a separate Store-distributed
MSIX app since 2022. On a clean machine, DISM enables the Windows feature
("Microsoft-Windows-Subsystem-Linux") but the actual wsl.exe binary +
the WSL kernel are downloaded from the Store. `wsl --install` (DISM-era
path) auto-pulls them on first run; we drive it explicitly so the
operator sees a known transcript instead of waiting on opaque downloads.

Operator-flagged "MiOS should be running preview builds
of WSL. Make sure the irm|iex installer can STILL install on a fresh
Windows System with NOTHING installed".

<!-- mios-src:078b1c92c75c from Get-MiOS.ps1:5239-5249 -->

### TOML-first -- WSL Store MSIX winget ID from mios.toml...

TOML-first -- WSL Store MSIX winget ID from mios.toml
[bootstrap.prereqs].wsl_pkg (operator can pin to Microsoft.WSL
preview channel via mios.html).

<!-- mios-src:233b3297318f from Get-MiOS.ps1:5251-5253 -->

### WSL kernel update + opt into PRE-RELEASE channel (preview...

WSL kernel update + opt into PRE-RELEASE channel (preview builds).
`wsl --update` pulls the latest MSIX kernel from Microsoft Store;
`--pre-release` flag (added in WSL 2.0.0, available on every modern
Windows + WSL combo) opts into the preview build channel which has
the newer compositor + gnome-shell --nested fixes operator needs
for the Enhanced Session full-desktop path.
`--set-default-version 2` ensures wsl --install / `wsl --import`
use WSL2 (HCS via VirtualMachinePlatform) by default.

<!-- mios-src:200a56ee7396 from Get-MiOS.ps1:5281-5288 -->

### TOML-first -- mios.toml...

TOML-first -- mios.toml [bootstrap.prereqs.features].require_reboot_to_continue
decides whether Pass-2 halts here (so downstream WSL-dependent
steps don't cascade-fail) or surfaces a warning and continues.
Operator default: halt (true), since on a truly fresh Windows
the dev VM, podman machine init, and OCI build all REQUIRE the
reboot; trying to run them just produces noise + half-broken
state. Operator opts to "continue anyway and watch what
survives" by setting it to false in mios.html.

<!-- mios-src:14737ddc2c17 from Get-MiOS.ps1:5314-5321 -->

### ALWAYS install RedHat.Podman (the CLI MSI) -- this is what...

ALWAYS install RedHat.Podman (the CLI MSI) -- this is what actually
lays down podman.exe with PATH integration. Podman Desktop alone
bundles the CLI internally but doesn't expose it on PATH; the
standalone CLI package does. Idempotent: winget no-ops if already
present.
TOML-first -- Podman CLI MSI ID from mios.toml [bootstrap.prereqs].podman_cli_pkg

<!-- mios-src:cdb3a5b550f6 from Get-MiOS.ps1:5431-5436 -->

### NOTE

NOTE: do NOT exit 1 here. build-mios.ps1's Phase 2 (machine init)
talks to Podman Desktop's API directly via the WSL distro -- it
doesn't need podman.exe on the Windows-side PATH to function.
Per operator: "no 'restart this shell' or 're-run' anything!!!!
automated!!!!!"

<!-- mios-src:ff619ec48c7f from Get-MiOS.ps1:5540-5544 -->

### Invoke-MiOSFullReap -- Phase 0 reap of every prior MiOS...

-----------------------------------------------------------------------------
Invoke-MiOSFullReap -- Phase 0 reap of every prior MiOS artifact
-----------------------------------------------------------------------------
Per feedback_mios_entry_full_reset memory:
  "every irm|iex must reap ALL prior MiOS state: temp clones, persistent
  clones, WSL distros (MiOS / MiOS-DEV / podman-MiOS-DEV / MiOS-BUILDER),
  podman machines, Hyper-V VMs (MiOS-*), install dirs (M:\ contents +
  %PROGRAMDATA%\MiOS / %LOCALAPPDATA%\MiOS / %APPDATA%\MiOS), Start Menu
  shortcuts, registry uninstall key. No partial state; no carry-over."
/usr/share/mios + C:\mios-bootstrap are PROTECTED -- operator dev working trees
of mios.git + mios-bootstrap.git per feedback_mios_no_c_drive_fallback.

AND per "If the uninstaller actually uninstalled
things automatically every time; I wouldn't have to Manually uninstall
anything EVERY TIME it fails!!!!"

Two callers:
  1. Phase 0 of the irm|iex main flow -- runs BEFORE Initialize-DataDisk
     so every install starts from zero state regardless of prior runs.
  2. The top-level failure trap -- runs on any unhandled exception so a
     half-broken install never leaves stale state behind.

Idempotent: every block is wrapped in EAP=SilentlyContinue + try/catch so
missing artifacts are no-ops. Logs each category's outcome to stdout in
DarkGray so the operator sees what's being reaped without noise.

Scope (matches uninstall.ps1's 12-category contract + Hyper-V + persistent
clones):
  1. Podman machines (MiOS-DEV, MiOS-BUILDER, plus any podman-MiOS-* WSL distro)
  2. WSL distros (MiOS, MiOS-DEV, podman-MiOS-DEV, MiOS-BUILDER, podman-MiOS-BUILDER)
  3. Hyper-V VMs matching MiOS-*
  4. Install dirs: M:\ contents (everything except drive root metadata),
     %PROGRAMDATA%\MiOS, %LOCALAPPDATA%\MiOS, %APPDATA%\MiOS.
     NEVER /usr/share/mios (operator dev tree of mios.git -- protected).
  5. WT settings.json -- launchMode, profiles.defaults MiOS keys, MiOS scheme,
     MiOS / MiOS-WIN / MiOS-DEV / podman-MiOS-* profiles
  6. PowerShell profile redirector blocks (10 candidate paths, marker-delimited)
  7. Fonts: Geist + symbols-only Nerd Font + matching HKCU font reg entries
  8. PATH env entries pointing into M:\MiOS\bin (HKCU + HKLM)
  9. HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\MiOS
 10. Start Menu folder + Desktop .lnk shortcuts (every legacy variant)
 11. AppUserModelID HKCU/HKLM\Software\Classes\AppUserModelId\MiOS.Workstation
 12. podman-machine state symlinks (3 candidate paths)
 13. MIOS_*/MiOS_* environment variables (HKCU + HKLM)

Non-destructive: NEVER touches C:\mios-bootstrap OR /usr/share/mios (both are
operator dev clones -- may have uncommitted work), the operator's
pwsh profile body outside the >>> MiOS ... >>> markers, or any
non-MiOS WT profiles / schemes / fonts.

<!-- mios-src:6bf7bc3bddc5 from Get-MiOS.ps1:5548-5596 -->

### SSOT

SSOT: every operator-visible reap string resolves through
mios.toml [messages.reap].* with the hardcoded fallback as Default.
Per feedback_mios_messages_section_ssot: no Write-Host literals.

<!-- mios-src:0281334da27d from Get-MiOS.ps1:5602-5604 -->

### 4. Install dirs. PROTECTED FROM REAP -- operator-owned dev...

4. Install dirs. PROTECTED FROM REAP -- operator-owned dev trees:
  * /usr/share/mios            -- dev working tree of mios.git (memory:
                           feedback_mios_no_c_drive_fallback;
                           ".git IS /" working tree). End consumers
                           never have this dir, so deleting it
                           only ever destroys operator dev work.
Operator-flagged after this
                           trap fired on a Phase-3 reap-on-failure
                           and wiped their checkout (uncommitted
                           edits unrecoverable -- no shadow copies).
  * C:\mios-bootstrap  -- dev working tree of mios-bootstrap.git
                           (same protected category).

MiOS owns M:\ exclusively (see block below) + a few %ProgramData% /
%LOCALAPPDATA% / %APPDATA% caches that ARE install-managed.

<!-- mios-src:73734c1a87ce from Get-MiOS.ps1:5650-5664 -->

### Desktop folders also collect Windows scratch artifacts like...

Desktop folders also collect Windows scratch artifacts like
`.tmp.driveu...` from disk-shrink/format operations. These aren't
MiOS-managed but they appear during the Initialize-DataDisk shrink
and confuse the operator (they look like leftover MiOS junk).
Reap any .tmp.* item from desktop dirs only (NOT Start Menu --
those are the actual install targets for MiOS shortcuts).

<!-- mios-src:aeaae63bb2eb from Get-MiOS.ps1:5826-5831 -->

### Recursively remove MiOS\Linux Apps\ subfolder (Files / Web...

Recursively remove MiOS\Linux Apps\ subfolder (Files / Web / VSCodium /
Flatseal / Extension Manager / Ptyxis / System Monitor / Settings)
created by Install-WindowsBranding's Linux Apps loop. Operator
"uninstaller STILL doesn't uninstall everything from
windows" -- the named-.lnk loop above left Linux Apps\ orphaned.

<!-- mios-src:76e62210ff3d from Get-MiOS.ps1:5848-5852 -->

### 16a. Windows Firewall inbound rules with the "MiOS -"...

16a. Windows Firewall inbound rules with the "MiOS -" prefix.
Paired with build-mios.ps1 :: Set-MiosLanFirewallRules. Sweep by
DisplayName prefix so we never touch operator-authored rules.

<!-- mios-src:f8f94ed3cf96 from Get-MiOS.ps1:5944-5946 -->

### 17. Prepare M:\ for a fresh MiOS tree. HISTORICALLY this...

17. Prepare M:\ for a fresh MiOS tree. HISTORICALLY this FULL-formatted
M:\ on the premise "MiOS owns this entire volume" -- true on a clean
provision. But a MiOS-Xbox-provisioned host (this machine was the first
MiOS-Xbox deployment) can later carry the ACTIVE Windows pagefile and
Windows UUP staging on M:\ because C:\ is too small to hold them.
Formatting there would strip the live pagefile with nowhere to recreate
it (C:\ full) and destroy unrelated data. So: full-format ONLY when M:\
is a DEDICATED MiOS volume (no pagefile, nothing but MiOS artifacts);
otherwise surgically remove just the MiOS dirs and preserve the rest.

<!-- mios-src:7927e37a8242 from Get-MiOS.ps1:5964-5972 -->

### Install-robustness do NOT hard-exit on a box that cannot...

Install-robustness do NOT hard-exit on a box that cannot
free the full 256 GB (256/512 GB laptop SSDs, or a heavily-used C:).
CLAMP the data partition to the largest fittable size, down to a floor
([bootstrap.host_storage].min_shrink_mb, default 64 GB); only abort if
even the floor won't fit -- and then `throw` (TRAPPABLE by the caller's
try/catch) instead of a bare `exit 1` (which terminated the whole
runspace, so the caller's catch + remediation never ran).

<!-- mios-src:befa23a3aeff from Get-MiOS.ps1:6041-6047 -->

### Functions-only dot-source gate...

-- Functions-only dot-source gate -------------------------------------------
Per "irm|iex is the main entry point for ALL things
MiOS... FIX all in code!". The canonical entry is:
  irm https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1 | iex
which falls through to the Pass-1 main flow below (M:\ provisioning + Step
1-8 chain + bootstrap.ps1 handoff). EVERY install path -- whether triggered
by the irm|iex one-liner, the MiOS launcher, mios-update, or build-mios.ps1
-- routes through these same Install-MiOS* functions so the deployed state
is deterministic regardless of entry path.

build-mios.ps1's Install-MiosLauncher dot-sources THIS script with
$env:MIOS_GETMIOS_FUNCTIONS_ONLY=1 set so it can reuse the function
definitions (Install-MiOSPowerShellProfile, Install-MiOSTerminalProfile,
etc.) without re-entering the main flow. Without this gate, dot-sourcing
would re-trigger Initialize-DataDisk + Step 1-8 + the bootstrap.ps1
handoff, which would recurse infinitely (build-mios.ps1 was called BY
bootstrap.ps1 in the first place).

<!-- mios-src:724f4770e7cc from Get-MiOS.ps1:6187-6203 -->

### Step 0

-- Step 0: M:\ provisioning BEFORE Pass-1 stages anything -------------------
Per operator: "EVERYTHING MIOS RELATED--EVEN WINDOWS COMPONENTS INSTALLED--
ARE ALL INSTALLED ON THE CREATED M:\ Drive/Partition!!!"

Pass-1 below stages the WT MiOS profile, MiOS PS profile body, native-app
launcher, fastfetch config, oh-my-posh theme. ALL of those have a "M:\ if
exists else %USERPROFILE%\..." fallback -- without M:\ provisioned FIRST,
files land on C:\ and Pass-2's later Initialize-DataDisk creates an empty
M:\ partition while the staged content is stuck in C:\ (split state).

This block creates M:\, junctions podman-machine + winget storage paths
onto M:\, so Pass-1's WT install + winget tools install + profile staging
all land on M:\ from the very first write. The Pass-2 calls to the same
functions are idempotent no-ops.
-- Defender exclusions BEFORE anything else --------------------------------
16:48 install: Microsoft Defender AMSI blocked
build-mios.ps1 with "This script contains malicious content and has
been blocked by your antivirus software". The C# Add-Type blocks for
IPropertyStore + PROPVARIANT + StringToCoTaskMemUni (AppUserModelID
stamping) match heuristic patterns that malware uses for shortcut-
persistence -- false positive that kills the install.

Pre-add Defender exclusions for the MiOS-owned paths so AMSI skips
scanning them. Requires admin (Pass-2 elevated context). Wrapped in
try/catch -- if the operator's Group Policy forbids Set-MpPreference,
we continue silently and let AMSI do its thing (the bait-reduction
refactor in build-mios.ps1 should keep most installs unblocked).

<!-- mios-src:946d7cb6a9f1 from Get-MiOS.ps1:6212-6238 -->

### SSOT

SSOT: exclusion paths + processes resolve through mios.toml
[security.defender_exclusions].* with vendor defaults baked here.
Operator can add their own paths via mios.html -> mios.toml.

<!-- mios-src:e83120b987e8 from Get-MiOS.ps1:6241-6243 -->

### Pre-Phase-0

-- Pre-Phase-0: write .wslconfig BEFORE the very first wsl.exe call ---------
Mirrored networking + firewall=false are read by WSL2 when the
UTILITY VM starts. The utility VM starts on the FIRST wsl.exe
invocation anywhere in this run -- and Invoke-MiOSFullReap below
calls `wsl --unregister` + `wsl --shutdown` before anything else.
If .wslconfig isn't on disk by then, the utility VM that those reap
calls implicitly boot lands in legacy NAT mode and STAYS there until
the next time someone explicitly stops it. Symptom the operator hit
every container port (port keys `cockpit`, `forge_http`,
`open_webui`, `hermes`, `searxng`, `llm_light`) timed out from
Windows even though `ss -tlnp` inside MiOS-DEV showed the binds, and
the host showed `vEthernet (WSL (Hyper-V firewall))` (NAT-only
adapter) instead of the IP-mirrored topology.
build-mios.ps1 Phase 3 still writes .wslconfig before podman-machine
init (belt-and-suspenders); this earlier write is what makes that
work even after the reap's wsl.exe calls.
Pre-Phase-0 .wslconfig writer -- TOML-first per AGENTS.md §3 / mios.toml
is THE singular SSOT for every operator-visible value. Resolve from the
layered overlay (~/.config > /etc > /usr/share); falls back to the
safe default (NAT + localhostForwarding) on a fresh host where mios.toml
isn't deployed yet. Mirrored mode opt-in: edit [wsl2].networking_mode
in mios.html, save, re-run irm|iex (read the [wsl2] comment block in
the vendor mios.toml for the prerequisites).

<!-- mios-src:5db16057c514 from Get-MiOS.ps1:6270-6292 -->

### Phase 0

-- Phase 0: Reap ALL prior MiOS state BEFORE anything else -----------------
Per feedback_mios_entry_full_reset memory: "every irm|iex must reap ALL
prior MiOS state... No partial state; no carry-over." AND operator
"If the uninstaller actually uninstalled things automatically
every time; I wouldn't have to Manually uninstall anything EVERY TIME it
fails!!!!". Runs UNCONDITIONALLY on every irm|iex invocation -- even if
nothing prior is installed (idempotent no-op).

<!-- mios-src:6ccfe0f2a5c4 from Get-MiOS.ps1:6352-6358 -->

### Failure-trap auto-reap...

-- Failure-trap auto-reap --------------------------------------------------
Operator contract "If the uninstaller actually uninstalled
things automatically every time; I wouldn't have to Manually uninstall
anything EVERY TIME it fails!!!!". Phase 0 reap above already handled the
"next irm|iex starts clean" case. This trap handles the "current install
fails mid-way" case -- terminating errors here trigger a final reap so
Windows is left in zero-state immediately on failure (operator never sees
half-broken state). Runs in addition to (not replacing) Phase 0.

SSOT: every operator-visible string resolves through mios.toml
[messages.failure_trap].* with the hardcoded fallback as Default.

<!-- mios-src:d46ddd39a0a8 from Get-MiOS.ps1:6361-6371 -->

### Factory-fresh guard

Factory-fresh guard: everything below (podman/winget storage, the mios.toml
promotion, the repo clone) targets M:\. Initialize-DataDisk already clamps the
carve down to what C: can spare (64 GB floor), so if we STILL have no M: volume
the disk genuinely cannot provide it -- STOP with an actionable reason instead
of silently cascading a broken install onto a drive that does not exist.

<!-- mios-src:42e066e33d9e from Get-MiOS.ps1:6416-6420 -->

### Step 0.5

Step 0.5: Promote the fetched vendor mios.toml to BOTH M:\usr\share\mios
and M:\etc\mios so the Windows-side dashboard / wrappers / Show-MiosDashboard
read the same [dashboard].rows / [colors] / [ports] / [packages.windows]
as the Linux side. Without this step Show-MiosDashboard falls back to its
vendor row-layout when M:\etc\mios\mios.toml is missing or stale, and
operator sees a different dashboard layout in pwsh vs in MiOS-DEV bash
(operator-flagged "MIOS.TOML ISN'T USED GLOBALLY"). Idempotent:
overwrites the M:\ overlay on every install with the live origin/main
fetch so a re-run always picks up the latest configurator edits.

<!-- mios-src:aafce3e07fd0 from Get-MiOS.ps1:6449-6457 -->

### Step 0.6

Step 0.6: Enable Windows OS-level features MiOS depends on (WSL +
VirtualMachinePlatform + Hyper-V). "pwsh7+,
podman, wsl, hyper-v, etc-etc are all fecthed and installed during
irm|iex installations -- THE FIRST STEPS AFTER DISK CREATION". This
runs as Step 0.6 -- after Initialize-DataDisk + the storage redirects
+ mios.toml M:\ promotion, before Pass-1 Windows-user-scope setup.
Requires admin; function self-checks and defers cleanly otherwise.

<!-- mios-src:6bd5ae1c50f6 from Get-MiOS.ps1:6476-6482 -->

### Hyper-V Firewall must allow inbound to the WSL VM. By...

Hyper-V Firewall must allow inbound to the WSL VM. By default the WSL
VM Creator GUID {40E0AC32-46A5-438A-A0B2-2B479E8F2E90} is
NotConfigured, which inherits a deny-all-inbound policy and silently
drops every Windows-host -> WSL service request -- even when WSL2
native localhostForwarding, the in-distro firewalld, AND the netsh
portproxy are all open. Operator-confirmed with this
setting NotConfigured, every browser hit on http://localhost:PORT/
returned 000 across the entire MiOS stack. Setting it to Allow +
Enabled is what unblocks the inbound path.

<!-- mios-src:25b081428e8d from Get-MiOS.ps1:6488-6496 -->

### WSL/VirtualMachinePlatform were just enabled and need a...

WSL/VirtualMachinePlatform were just enabled and need a reboot. Rather
than making the operator re-paste the one-liner (the factory-fresh
friction point), arm a run-once ELEVATED scheduled task that re-runs
the one-liner AUTOMATICALLY at the next logon, then halt cleanly so the
WSL/podman/build steps don't cascade-fail on a not-yet-rebooted host.

<!-- mios-src:7e991cdd7416 from Get-MiOS.ps1:6522-6526 -->

### Strict install order. Each step gates the next: 1. WT...

Strict install order. Each step gates the next:
  1. WT Preview install + AppX-ready wait. Until this completes
     LocalState\settings.json doesn't exist and the patcher
     silently no-ops -- which is exactly what the operator
     caught us doing in earlier revisions.
  2. settings.json patch IMMEDIATELY after install, while the
     LocalState dir is freshly materialized. This is what makes
     MiOS the default theme on the very first WT launch.
  3. Geist Mono NF font install. Settings.json already references
     this face name; if the font isn't on disk yet WT will
     silently fall back to Cascadia, but the ANSI scheme + acrylic
     still apply -- so font order doesn't break anything else.
  4. PowerShell profile (oh-my-posh init line). Lowest priority;
     cosmetic, only matters once the operator hits a prompt.
Apply the MiOS palette + transparency settings to the Windows OS
registry so the OPERATOR'S WHOLE DESKTOP is MiOS-themed -- not
just the WT window. EnableTransparency is the precondition for
acrylic to render at all (Server / freshly-imaged Windows ships
with it OFF, which is why "no acrylic, nothing" was happening).
Dark mode + ColorPrevalence + DWM accent paint MiOS's operator-
blue (#1A407F) onto title bars, taskbar, and Start chrome too.

MiOS canonical accent (mios.toml [colors].accent): #1A407F.
DWM stores AccentColor in 0xAABBGGRR layout (alpha + reverse-byte
BGR), so #1A407F encodes as 0xFF7F401A.

<!-- mios-src:d86c0f947151 from Get-MiOS.ps1:6555-6579 -->

### Use reg.exe directly. Both Set-ItemProperty -Type DWord AND...

Use reg.exe directly. Both Set-ItemProperty -Type DWord AND
.NET Microsoft.Win32.RegistryKey.SetValue('DWord') reject
0xFF7F401A in PS 7 / .NET 8 because their validators want
UInt32 inputs but PS represents the value as Int64
4286529562, which overflows when downcast to Int32 (->
-8437734) and then fails UInt32's range check. reg.exe
accepts hex literals natively for REG_DWORD and writes the
raw 32-bit pattern -- DWM reads back the unsigned 0xFF7F401A.

<!-- mios-src:6e954bd5820d from Get-MiOS.ps1:6594-6601 -->

### SSOT

SSOT: Step 1/7..7/7 banners resolve through mios.toml [messages.steps].
"applications and icons should be installed AFTER
everything--at the end!!!! LAST STEPS". Step 8 (Install-MiOSNativeApp)
was relocated to the very end of Get-MiOS.ps1, AFTER bootstrap.ps1 +
build-mios.ps1's full phase loop succeeds. If the dev VM build fails
part-way, the failure-trap reap fires and NO shortcuts are ever
created -- operator never sees broken icons pointing at a half-built
dev VM. Steps 1-7 below stage the Windows-side basics ONLY.

<!-- mios-src:a5fd32edefe2 from Get-MiOS.ps1:6616-6623 -->

### Bibata cursor rides alongside the font install -- both are...

Bibata cursor rides alongside the font install -- both are
operator-visible "global desktop chrome" touches that don't fit
neatly into a separate numbered step.
"cursor is still not bibata GLOBALLY".

<!-- mios-src:21bbbb34e8b9 from Get-MiOS.ps1:6646-6649 -->

### Start Menu shortcuts for every Linux .desktop entry in the...

Start Menu shortcuts for every Linux .desktop entry in the dev
VM (flatpak apps + native rpm apps + MiOS service launchers).
Uses Microsoft WSL's native shortcut pattern (wslg.exe target,
no console flash, .ico icons in %LOCALAPPDATA%\Temp\WSLDVCPlugin\
<distro>\) so apps appear in Windows search / Start with their
proper icons. Operator-flagged "opening WSL apps in
windows is NOT native WSL behaviour ... icons should be visible
for each application NATIVELY".

<!-- mios-src:db0993389aae from Get-MiOS.ps1:6651-6658 -->

### NOTE

NOTE: Install-MiOSNativeApp (canonical 4-shortcut creation) used to
run here as Step 8/8. Moved to the end-of-script "FINAL STEP"
block (post-bootstrap.ps1 success) per operator directive.

<!-- mios-src:89aef87392b3 from Get-MiOS.ps1:6674-6676 -->

### Refresh $env:PATH from registry BEFORE dot-sourcing the...

Refresh $env:PATH from registry BEFORE dot-sourcing the profile.
winget just installed oh-my-posh / fastfetch / etc. and updated the
USER + MACHINE PATH, but the current pwsh session inherited the
PATH from the launching (non-admin) pwsh -- it does NOT see those
newly installed binaries. Without this refresh the profile body's
`oh-my-posh init pwsh | iex` silently no-ops and the prompt stays
vanilla; Show-MiosDashboard's `Get-Command fastfetch` returns null
and the dashboard never renders.

<!-- mios-src:98053fa1cb89 from Get-MiOS.ps1:6678-6685 -->

### Reload the user profile in the CURRENT irm|iex pwsh session...

Reload the user profile in the CURRENT irm|iex pwsh session so
the regex-patch + PSReadLine reload + MiOS prompt take effect
immediately, without the operator having to close + re-open
pwsh. The redirector was just written -- dot-source it now.

<!-- mios-src:7d355ae35cdb from Get-MiOS.ps1:6697-6700 -->

### Steps 1-7 done -- WT, fonts, oh-my-posh, fastfetch, native...

Steps 1-7 done -- WT, fonts, oh-my-posh, fastfetch, native app
all live under the OPERATOR's user profile (HKCU, OneDrive,
%LOCALAPPDATA%, per-user Start Menu). Bootstrap below
(Initialize-DataDisk + bootstrap.ps1) needs ADMIN to shrink C:\
and machine-scope-winget-install Podman Desktop. UAC-spawn an
elevated pwsh that re-fetches Get-MiOS.ps1 with
MIOS_GETMIOS_RELAUNCHED=1, which causes the inner call to
SKIP this Pass-1 block entirely (no font reinstall) and
fall through to the Pass-2 path (lines below this if-block --
M:\ provisioning + bootstrap.ps1 hand-off).

<!-- mios-src:8ec18d2c5169 from Get-MiOS.ps1:6710-6719 -->

### Pass-2 inner script

Pass-2 inner script: first action is to size the console to 80x30
and center it on the primary monitor, BEFORE any output runs (so the
operator never sees a default 120x30 window briefly before resize).
`[Console]::SetWindowSize` covers conhost; the Win32 SetWindowPos
call covers conhost AND WT's pseudo-console (WT honors the absolute
client-area sizing on its parent HWND).

<!-- mios-src:36fa0fb0f50d from Get-MiOS.ps1:6741-6746 -->

### NB: -NoProfile is INTENTIONALLY OMITTED. Per operator...

NB: -NoProfile is INTENTIONALLY OMITTED. Per operator
("launch with the same themes and settings as Global MiOS
Dashboards with oh my posh piping--etc--everything!!"), the
Pass-2 elevated window must load the MiOS PowerShell profile
body (M:\MiOS\powershell\profile.ps1) so it gets:
  * the resize+center preamble (every MiOS pwsh dashboard sized)
  * Show-MiosDashboard (framed banner + fastfetch info)
  * oh-my-posh init with the MiOS theme
  * mios-* command shims (mios-build, mios-pull, etc.)
The once-per-session guard ($Global:MiosProfileLoaded) keeps
the profile from rendering twice when WT also fires it.

<!-- mios-src:ca3a10cec336 from Get-MiOS.ps1:6809-6819 -->

### NB: previous attempt to launch via `wt.exe new-window...

NB: previous attempt to launch via `wt.exe new-window
--profile MiOS pwsh ...` with `-Verb RunAs` returned
0x80070002 ERROR_FILE_NOT_FOUND on Windows 11 -- appx-packaged
WT + UAC + complex argv combine badly under ShellExecuteEx.
Fall back to bare pwsh elevation. The user's default terminal
host (conhost or WT) decides where the elevated process
lands. Either way, the MiOS PS profile body still loads
in-process via $PROFILE.CurrentUserAllHosts redirector, so
oh-my-posh + Show-MiosDashboard render automatically -- the
operator gets the MiOS terminal experience regardless of
which host paints the chrome.
If WT is the operator's default-terminal-host (Windows 11
22H2+ default), the elevated pwsh lands in WT with the
operator's default profile (PowerShell). To get the MiOS WT
profile inside an already-elevated pwsh, the operator can
run `wt -p MiOS` from that elevated session -- no second UAC.

<!-- mios-src:90f83e93b6f2 from Get-MiOS.ps1:6822-6837 -->

### 2. Resize host window to 80x30 -- the canonical TTY0 /...

2. Resize host window to 80x30 -- the canonical TTY0 / text-mode-3+
dimension and the MiOS dashboard's global size. 80 cols × 30 rows
yields a 4:3 pixel aspect with standard 1:2 monospace cells, fits
the dashboard frame's 80-col strict-clamp, and matches the post-
install hub menu's row budget. wt.exe --size 80,30 already requested
this for the WT window; this RawUI set is the conhost-fallback path
AND a belt-and-braces resize in case WT honored --pos but ignored
--size on an older build.

<!-- mios-src:1715886c6bf3 from Get-MiOS.ps1:6853-6860 -->

### 3. Helpers (Write-Info / Write-Good / Write-Err /...

3. Helpers (Write-Info / Write-Good / Write-Err / Require-Cmd /
Ensure-PodmanDesktop) and the M:\ provisioning functions
(Initialize-DataDisk / Set-PodmanMachineStorageOnM /
Set-WingetStorageOnM) are defined ABOVE Pass-1 now (so Step 0 can
create M:\ before Pass-1 stages files). Their original definitions
moved up; this section header retained for orientation.

<!-- mios-src:f4b2747b9f1d from Get-MiOS.ps1:6870-6875 -->

### 4. Prerequisites Podman Desktop is no longer a "Require-Cmd...

4. Prerequisites

Podman Desktop is no longer a "Require-Cmd or die" gate -- mios.bat
self-elevates so we have admin here, which means winget can install
RedHat.Podman-Desktop unattended without bouncing the operator out
to a browser. Latest stable (per memory: target latest) -- no
version pin, winget picks whatever the manifest currently advertises.

<!-- mios-src:9a85dbd5877e from Get-MiOS.ps1:6881-6887 -->

### Junction every candidate podman-machine storage path onto...

Junction every candidate podman-machine storage path onto M:\ so the
eventual `podman machine init` lands the WSL distro VHDX (multi-GB) on
the dedicated 256 GB partition rather than on C:\. Per
feedback_mios_dev_on_m_drive.md, this MUST happen before any podman
command runs -- if podman creates files at the source path first, the
junction can't be applied to a non-empty dir without a move-then-junction
dance.

Podman v4.x and v5.x use different default storage paths on Windows
depending on machine provider, user vs. system scope, and version
upgrades that didn't migrate the data. We junction ALL candidates so
whichever one the installed podman picks resolves to M:\.
Junction every winget package storage path onto M:\ so winget-installed
CLIs (oh-my-posh, fastfetch, fd, ripgrep, jq, btop4win, etc.) land on
the dedicated MIOS-DEV partition rather than scattering across
%LOCALAPPDATA% and %PROGRAMFILES%. Per operator: "winget should be
installing EVERYTHING to the M:\ partition for ease of uninstallations".

Carve-outs (NOT relocatable):
  - Windows Terminal (appx-packaged UWP, lives in WindowsApps)
  - Podman Desktop (machine-scope MSI, lives in Program Files)
These two stay where Microsoft / RedHat installed them; everything
else (per-user winget package cache + per-user manifest cache + the
winget portable-app stash) gets symlinked to M:\winget\*.

Same symlink-not-junction discipline as podman storage paths above:
mklink /D, not /J. winget's link resolver follows symlinks; some
uninstallers fail on junction targets.

Runs BEFORE any winget install so the very first install's package
directory creation lands on M:\ from the start. If we redirect
AFTER winget has already created the dirs, we'd need to move the
contents over -- doable but racy. Idempotent: re-runs are no-ops if
the symlinks already point at M:\.
NOTE: Phase 0 above (Invoke-MiOSFullReap, called BEFORE Initialize-
DataDisk on every irm|iex run) has already nuked every prior MiOS
artifact on this machine: WSL distros, podman machines, Hyper-V VMs,
install dirs (%PROGRAMDATA%\MiOS / %LOCALAPPDATA%\MiOS / %APPDATA%\MiOS),
M:\ contents, WT MiOS scheme + profiles, Start Menu folder + Desktop
.lnks, HKCU uninstall reg key, AppUserModelID regs, podman-machine
state symlinks, MIOS_* env vars, fonts, PATH entries, MiOS Firewall
rules.

/usr/share/mios + C:\mios-bootstrap are NEVER touched: both are operator-
owned dev working trees of mios.git / mios-bootstrap.git (per the
feedback_mios_no_c_drive_fallback memory). End consumers never have
these dirs; reaping them only ever destroys operator dev work.
Operator-flagged after /usr/share/mios got nuked.

Per feedback_mios_entry_full_reset memory +
"every irm|iex must reap ALL prior MiOS state... No partial state;
no carry-over." M:\ is the MiOS-owned 256 GB partition; the reap
clears that + the AppData caches but never the dev-tree C:\ paths.

<!-- mios-src:2735d8eb6029 from Get-MiOS.ps1:6898-6950 -->

### Step 0 above (before Pass-1) ALREADY provisioned M:\ +...

Step 0 above (before Pass-1) ALREADY provisioned M:\ + symlinked
podman-machine + winget package storage onto M:\. Pass-1's winget
tools install + WT install + profile staging all landed on M:\
from the very first write. The Initialize-DataDisk + storage-junction
functions are idempotent, so this comment block stands as a marker
of where the late-bound calls USED to live -- they're no longer needed.

<!-- mios-src:83d094ec251a from Get-MiOS.ps1:6952-6957 -->

### 5. Fresh-clone the mios-bootstrap repo to...

5. Fresh-clone the mios-bootstrap repo to M:\MiOS\repo\mios-bootstrap.

CONTRACT (per feedback_mios_irm_iex_always_temp_clone.md +
feedback_mios_entry_m_drive_clone.md): irm|iex ALWAYS clones a
fresh copy. There is NO update / fetch / pull branch. The clone
target is M:\MiOS\repo\mios-bootstrap (the canonical Windows-entry
working tree), NOT %TEMP% or %USERPROFILE%.

Since the full reset above already wiped M:\MiOS, $RepoDir won't
exist when we get here -- no Remove-Item dance needed. (Operator
overrides with -RepoDir <other-path> still get the safety check.)

<!-- mios-src:a866f97854fd from Get-MiOS.ps1:6965-6975 -->

### If $RepoDir already exists with a .git subdir from a prior...

If $RepoDir already exists with a .git subdir from a prior run, do an
in-place fetch + reset --hard to bring it to origin/main. NEVER delete
operator-side files (per feedback_mios_entry_full_reset.md). If it
exists but isn't a git repo, fail with an actionable message rather
than silently nuking it.

<!-- mios-src:d8385dce8937 from Get-MiOS.ps1:7018-7022 -->

### FINAL STEP

-- FINAL STEP: applications + icons (operator directive) ------------------
"applications and icons should be installed AFTER
everything--at the end!!!! LAST STEPS". Only fires on bootstrap.ps1 +
build-mios.ps1 success ($_bootstrapExit==0). On failure the trap-on-
failure auto-reap above already wiped Windows clean -- no shortcuts
pointing at a half-broken dev VM.

<!-- mios-src:c9f7c21176ca from Get-MiOS.ps1:7118-7123 -->

### Bootstrap stops at DEV-ready...

-- Bootstrap stops at DEV-ready --------------------------------------------
(feedback_mios_dev_vm_is_builder_only.md):
  "we aren't bootc switching podman-MiOS-DEV!!! WE NEED TO FIRST BOOT IN
   TO podman-MiOS-DEV and have it working!!!! 'mios build' command is
   for building OCI images from any MiOS app window"

The dev VM is the BUILDER substrate -- podman-machine-os Fedora 44 with
the MiOS overlay (Quadlets / RPM layer / flatpaks / branding) applied
during Phase 3. It is NOT bootc-switched to localhost/mios:latest; that
would conflate the builder with the deployment target. `mios build` is
the operator-triggered verb that produces OCI + bootc-image-builder
artifacts (vhdx / qcow2 / iso / raw / wsl tarball) for deploying to
OTHER substrates. Output flows outward from the dev VM, never inward.

Earlier commits (a307e4b ... 90aa799) auto-chained `mios build` here on
the assumption that post-bootstrap = bootc-switched dev VM. Operator
corrected: that's wrong. Bootstrap returns at DEV-ready; the staged
MiOS hub shortcut + verb-hint banner above tell the operator what
verbs to type next.

<!-- mios-src:970a8d119634 from Get-MiOS.ps1:7140-7158 -->

### WSLg host-side bridge reset (clears [WARN: COPY MODE])...

-- WSLg host-side bridge reset (clears [WARN: COPY MODE]) ------------------
"STILL no visible windows" -- weston / msrdc
accumulate state during the multi-minute install (mid-install
wsl.exe -- probes, daemon-reloads, container starts) that often
leaves the host-side RDP-RAIL bridge stuck in COPY MODE even after
our /mnt/wslg/runtime-dir chmod fix lands. A fresh `wsl --shutdown`
+ `Restart-Service LxssManager` on the Windows host gives WSLg a
clean slate to negotiate VAIL (shared-memory) mode on first re-entry.

Safe to run unconditionally at the END of irm|iex: bootstrap has
already completed all its work, no in-flight operations to lose.
The next time the operator launches MiOS, WSLg starts fresh.

<!-- mios-src:b8a3c342f35d from Get-MiOS.ps1:7160-7171 -->

### Restart-Service requires admin; the irm|iex caller already...

Restart-Service requires admin; the irm|iex caller already
elevated, so this works. Failure is non-fatal -- shutdown
alone is usually enough.
WSL service name differs by Windows build: 'WslService' on Win11
Store/inbox WSL, 'LxssManager' on legacy Win10. Try both; skip
gracefully if neither exists ('Cannot find any service
with service name LxssManager' on Win11).

<!-- mios-src:ce13c4686408 from Get-MiOS.ps1:7178-7184 -->

### MiOS-Cat handoff

-- MiOS-Cat handoff: offer to flash a bootable MiOS-Cat USB -----------------
The bare `irm|iex` one-liner runs the Default action and (until now) never
routed to MiOS-Cat -- the -Action FlashUSB path is unreachable from a pipe (no
params). Offer it here as a param-less prompt so a factory-fresh install can go
straight from provisioning to building a deploy USB. Skipped under -Unattended
(never surprise-format a drive) or if bootstrap did not succeed.

<!-- mios-src:aee6d4b2bbe2 from Get-MiOS.ps1:7198-7203 -->

### Dry-run (default) -- see exactly what would be removed:

Dry-run (default) -- see exactly what would be removed:

<!-- mios-src:86a2cb8a5014 from Uninstall-MiOS.ps1:33-33 -->

### File at data-drive root. DO NOT blanket-delete every...

File at data-drive root. DO NOT blanket-delete every non-KEEP
file -- that nukes genuine operator data dropped at M:\ root.
Only remove files matching a known MiOS artifact pattern;
preserve anything else (whitelist parity with $MIOS_DIRS).

<!-- mios-src:6ed6649c6d3d from Uninstall-MiOS.ps1:192-195 -->

### BootstrapOnly / -BuildOnly / -FullBuild

-BootstrapOnly / -BuildOnly / -FullBuild: LEGACY FLAGS, KEPT FOR
CALL-SITE COMPATIBILITY ONLY. Per the self-replication contract
(project memory: project_mios_self_replication_vision.md), the
Windows side runs ONLY: ack -> MiOS-DEV podman-machine setup ->
SSH handoff. Phase 6+ (Identity / OCI build / WSL2 export /
Hyper-V deploy) MUST run inside MiOS-DEV via /usr/libexec/mios/
mios-build-driver, NOT on Windows.

These flags are now no-ops -- the script always behaves as if
-BootstrapOnly was the only mode. -FullBuild and -BuildOnly emit
a deprecation note and are otherwise ignored. Operators who want
the old in-Windows pipeline can revert to a pre-352aee3 build of
this script; nothing else honors them any more.

<!-- mios-src:26faab5f1dc1 from build-mios.ps1:41-53 -->

### Disable console QuickEdit mode up-front. With QuickEdit on...

Disable console QuickEdit mode up-front. With QuickEdit on (the Windows
default), the instant anyone clicks or selects text in the window the console
enters "mark" mode and BLOCKS the process on its next write until Enter/Esc is
pressed -- on a long elevated install this looks identical to a dead hang
(process idle, only a conhost child, VM perfectly healthy). The
stall right after "MiOS Quadlet overlay applied" was exactly this. Clearing
ENABLE_QUICK_EDIT_MODE (0x40) + setting ENABLE_EXTENDED_FLAGS (0x80) makes the
installer immune to accidental click-to-freeze. Best-effort; never fatal.

<!-- mios-src:18854c8749fd from build-mios.ps1:65-72 -->

### ── mios.toml layered-overlay reader (mirrors Get-MiOS.ps1's...

── mios.toml layered-overlay reader (mirrors Get-MiOS.ps1's helper) ─────────
mios.toml is THE global dotfile (per feedback_mios_toml_html_global_dotfile).
Every tunable -- terminal dims, retry delays, dev VM image tag, distro
names -- sources from the layered overlay. We inline the helper instead
of dot-sourcing because build-mios.ps1 must work both in-tree (clone) and
under irm|iex relaunch where the path to Get-MiOS.ps1 isn't guaranteed.

<!-- mios-src:0a6f4880c60c from build-mios.ps1:96-101 -->

### Read as UTF-8. PS 5.1's Get-Content default is the system...

Read as UTF-8. PS 5.1's Get-Content default is the
system ANSI codepage (cp1252 on en-US) which decoded
the UTF-8 PUA glyphs in [theme.prompt] as 3-char
mojibake (the U+E0B4 cap's bytes EE 82 B4 became
'î‚´'). The omp.json glyph substitution then took
'î' as the cap and wrote U+00EE into the deployed
theme, producing operator-reported "powerline seconds
are shifted to the next row" + 'î' instead of ''.

<!-- mios-src:256749306a9a from build-mios.ps1:113-120 -->

### Return without unary-comma -- callers do `@(Get-Mios...)`...

Return without unary-comma -- callers do `@(Get-Mios...)`
which collects pipeline-unrolled ints into an array.
With `,$coerced` the result was @(@(0,5,15,30)) -- a
1-element array, so $delays[0] was the array itself,
crashing Start-Sleep -Seconds with "cannot convert
System.Object[] to System.Double".

<!-- mios-src:d90a9eb585e6 from build-mios.ps1:178-183 -->

### String -- strip SURROUNDING TOML quotes only (no Trim...

String -- strip SURROUNDING TOML quotes only (no Trim multi-set,
which previously ate leading ' from values like "'MiOS' v0.2.4"
because Trim('"',"'") matches both chars on both ends). Unescape
backslash sequences for double-quoted strings per TOML 1.0.0.

<!-- mios-src:a24bff2256b8 from build-mios.ps1:190-193 -->

### Resolve canonical terminal dims ONCE at script-load so...

Resolve canonical terminal dims ONCE at script-load so every later
resize / wt --size / stty call uses the same values from mios.toml.

IMPORTANT: build-mios.ps1 runs DURING the bootstrap install. Use
[terminal.install] dims (vendor default 80x40 -- enough rows for
the dashboard + install logs to fit visibly without auto-scroll
eating the banner). [terminal] dims (80x20) are reserved for the
POST-INSTALL MiOS app spawn -- using them here would shrink the
install conhost mid-flight, which the operator reports as "windows
still shrink to 80x20 and are also off-center". The post-install
wt --size spawn uses script:MiosAppCols / script:MiosAppRows.
[terminal.install] dims (80x40 install conhost, taller for log
room).  Renamed to $script:MiosInst{Cols,Rows} to avoid colliding
with Initialize-MiosGlobals which loads $script:Mios{Cols,Rows}
from [terminal] (the app dims).  Operator: "I said Unified!!! ...
extracted to ONE function used by every".

<!-- mios-src:ed47787e30fd from build-mios.ps1:215-230 -->

### Initialize-MiosGlobals (defined further down, called once...

Initialize-MiosGlobals (defined further down, called once at
script load) writes $script:MiosCols / $script:MiosRows from
the [terminal] section.  Shadow with the install dims here so
any sizing-dependent code BEFORE Initialize-MiosGlobals fires
uses the install conhost dims; after that point the app dims
from Initialize-MiosGlobals take over.  $script:MiosScroll +
$script:MiosAppCols / $script:MiosAppRows are kept inline (not
overwritten by Initialize-MiosGlobals) for any legacy site that
referenced the App-prefixed names.

<!-- mios-src:e5e4b3750aaf from build-mios.ps1:233-241 -->

### ── Console resize: mios.toml [terminal] dims BEFORE any...

── Console resize: mios.toml [terminal] dims BEFORE any sizing-dependent state ─
$script:DW (~line 543) is computed from [Console]::WindowWidth at script-
load time and never re-read. If the parent window opened wider, the
dashboard frame draws at the wrong width and log lines bleed past it.
Resize NOW, before $DW is computed. Dims source from mios.toml [terminal]
(vendor default 80x20 portal feel).
Per feedback_mios_terminal_dimensions.md.

The order matters: SetWindowSize requires buffer >= window. If the
current buffer is smaller than the target cols, SetWindowSize fails.
If the current window is larger than the target cols, SetBufferSize
fails (buffer can't be smaller than current window). So we branch.

<!-- mios-src:cb218c343935 from build-mios.ps1:248-259 -->

### NOTE

NOTE: The bootstrap-conhost window-centering helper that lived here
was REMOVED in commit 82dda7e+ because AMSI heuristics flagged the
combination of console-window-handle retrieval + window-positioning
Win32 calls as malware. Window centering was purely cosmetic; install
runs identically without it. Operator can drag the window if needed.

<!-- mios-src:27fa2991253d from build-mios.ps1:282-286 -->

### ── Self-replication enforcement: Windows ALWAYS halts at...

── Self-replication enforcement: Windows ALWAYS halts at Phase 5 ────────────
Per the self-replication architecture, the Windows side has STRICT scope:
ack + MiOS-DEV podman-machine setup + SSH handoff. The legacy -FullBuild /
-BuildOnly flags that bypassed this and ran identity / OCI / disk-image
phases ON WINDOWS are deprecated AND IGNORED here. We force $BootstrapOnly
to $true unconditionally so every code path that gates "stop after
Windows phases" via `if ($BootstrapOnly)` keeps the bootstrap halted.
Operators who need the old behavior must revert to a pre-352aee3 build.

<!-- mios-src:5764b475fcd7 from build-mios.ps1:289-296 -->

### ── Install scope detection...

── Install scope detection ───────────────────────────────────────────────────
'MiOS' installs as a native Windows app. Two scopes:

  AllUsers  -- machine-wide install at C:\Program Files\MiOS\
               Add/Remove Programs in HKLM. Distros + images in
               C:\ProgramData\MiOS. Per-user logs/config still use
               %LOCALAPPDATA%\MiOS / %APPDATA%\MiOS so each Windows
               account on the box gets its own state.

  CurrentUser -- per-user install at %LOCALAPPDATA%\Programs\MiOS\
                 Add/Remove Programs in HKCU. Used as a fallback when
                 the operator declines UAC elevation, or when the
                 installer is invoked under a standard (non-admin)
                 account.

Detection: a process is "admin" if it holds the Administrators
built-in role. The 'irm | iex' one-liner from Get-MiOS.ps1 will refuse
to elevate itself (UAC cannot prompt mid-pipeline); operators are
expected to run from an elevated PowerShell when AllUsers is desired.

<!-- mios-src:1c9488443726 from build-mios.ps1:321-339 -->

### ── Paths & constants -- ALL sourced from mios.toml SSOT...

── Paths & constants -- ALL sourced from mios.toml SSOT ─────────────────────
Per operator: "toml is the SSOT for code too!!! no hardcoding ANYWHERE!!!".
Every value below resolves through Get-MiosTomlValue with a vendor-default
fallback. The configurator HTML (mios.html) exposes each key as an editable
field; an operator edit there flows mios.toml -> these values -> the entire
install pipeline.

<!-- mios-src:333e1b58afe2 from build-mios.ps1:349-354 -->

### MiOS-DEV's base machine-OS image. Pinned to 6.0 per...

MiOS-DEV's base machine-OS image. Pinned to 6.0 per operator's
explicit instruction:

  "use 6.0 machine podman-os images!!!!!"

6.0 is the newest stable non-floating tag at quay.io/podman/machine-os
(probed tags = 5.0, 5.1,..., 5.8, 6.0, next).

IMPORTANT compatibility note: pinning a major-version-newer machine-os
than the installed podman client requires the client to know how to
consume it. On podman 5.8.2 (the operator's current client), `--image
docker://quay.io/podman/machine-os:6.1` may fail at the Win32 pull-
extraction step with:
    Error: failed to pull ... : The system cannot find the path specified.
That's a podman-5.8-on-WSL bug, NOT a wrong-URL bug -- 6.0 itself is
correctly published at quay.io. The fix on the operator's side is:
    winget upgrade Podman.Podman
which gets a 6.x client that handles the 6.0 machine-os pull cleanly.

The `docker://` prefix is required for OCI-registry refs on the
`--image` flag; bare refs hit GetFileAttributesEx-as-file-path on
Windows. The MIOS_MACHINE_IMAGE override hatch stays open if a
specific operator wants to fall back to 5.8 (their bundled default)
until they upgrade -- set MIOS_MACHINE_IMAGE='' (empty string) to
omit --image entirely.
Default: NO --image (use podman's bundled local file, which always
works because podman ships its own machine-os tarball alongside the
client). Empirical lesson from logs across this stretch:

  * podman 5.8.2 on Windows / WSL provider FAILS to pull ANY OCI
    ref via `podman machine init --image docker://...` -- both 6.0
    AND the bundled-tag fallback to :5.8 hit the same Win32 error:
        Error: failed to pull quay.io/podman/machine-os@sha256:<digest>:
               The system cannot find the path specified.
    This is a podman-on-Windows pull-extraction bug, NOT a wrong-URL
    bug -- the digests resolve correctly; the local extraction
    stage is broken on the WSL provider for this client version.

  * Without --image, podman uses its bundled local tarball and
    `wsl --import`s it directly -- no pull, no extraction-from-
    registry path, just works. Operator's earlier successful runs
    all took this path.

To pin a specific machine-os tag, the operator must:
  (a) upgrade their podman client to a version that fixes the
      WSL pull bug (`winget upgrade Podman.Podman`, retry)
  (b) THEN set $env:MIOS_MACHINE_IMAGE=docker://quay.io/podman/
      machine-os:6.1 (or whatever tag) before invoking the
      bootstrap.

Until the operator's client is upgraded, pinning is wedged shut by
podman, not by us. This default makes the bootstrap actually
progress instead of dying at Phase 3 with "path not found."

<!-- mios-src:f3eefd951ccf from build-mios.ps1:376-428 -->

### Mirror the path locals to $script: scope so functions...

Mirror the path locals to $script: scope so functions defined in
this file (which use $script:MiosInstallDir / $script:MiosRepoDir
etc. for the AFTER-data-disk-bootstrap variant) ALWAYS find a
valid value -- even when Update-MiosInstallPaths never runs (no
admin, no M:\ provisioning). Without this mirroring,
New-BuilderDistro's `Join-Path $script:MiosInstallDir 'machine-os'`
threw "Cannot bind argument to parameter 'Path' because argument
is null" the moment Phase 3 fired in CurrentUser scope.

<!-- mios-src:958c3ace40ac from build-mios.ps1:473-480 -->

### Early M:\ detection: if the MIOS-DEV partition is already...

Early M:\ detection: if the MIOS-DEV partition is already mounted
(from a previous admin run), redirect EVERY install path onto it
UNCONDITIONALLY -- regardless of whether THIS run is admin. The
operator's expectation per memory feedback_mios_repo_context_invariant
is "EVERY MiOS artifact lives on M:\ when M:\ is provisioned".
Without this early redirect, a non-admin re-run of build-mios.ps1
falls back to C:\Users\Administrator\AppData\Local\MiOS even when
M:\ is right there waiting -- which is exactly the operator's
"should ALL be installing to the created M:\ partition!!!" symptom.

<!-- mios-src:e07092dae433 from build-mios.ps1:494-502 -->

### Data/log/config roots derive from the ALREADY-RESOLVED...

Data/log/config roots derive from the ALREADY-RESOLVED install root
($script:MiosInstallDir) so logging + btop + toml work in BOTH modes:
  * admin / M:\ provisioned -> $script:MiosInstallDir is M:\MiOS (the
    early M:\ redirect / Update-MiosInstallPaths fired above), so logs
    land on M:\MiOS\logs exactly as before -- single mount point holds
    the full audit trail per feedback_mios_m_drive_everything.
  * non-admin (no M:\, no write to C:\) -> $script:MiosInstallDir is
    %LOCALAPPDATA%\MiOS, so logs/config land there instead of a
    non-existent M:\ (which previously hard-broke logging/btop/toml).
The 'M:\MiOS' literal stays only as the last-resort fallback for the
(theoretically unreachable) case where neither root resolved.

<!-- mios-src:bc4916eb6801 from build-mios.ps1:544-554 -->

### Returns the best Windows-side install root, preferring the...

Returns the best Windows-side install root, preferring the dedicated
MiOS data disk (created by Initialize-MiosDataDisk in Phase 3:
shrinks C: by 256 GB, formats NTFS, label "MIOS-DEV", default
mount letter M:). Falls back to the boot-time default
($MiosInstallDir) when the data disk hasn't been provisioned yet.

Honors $env:MIOS_DATA_DISK_LETTER for non-default mount letters
(must match Initialize-MiosDataDisk's -DriveLetter argument).

<!-- mios-src:017ed4c0b1e6 from build-mios.ps1:564-571 -->

### Full-partition overlay

Full-partition overlay: re-point EVERY install path at the new
root so the entire MiOS pipeline (Windows app, repos, dev VM
VHDX, build artifacts, machine-state, logs) lives on the same
volume. The `MIOS-DEV` partition is the operator's choice for
"everything MiOS lives here"; we honor that across the board.

Caller MUST run this BEFORE Phase 2 (repos clone) so the clones
land at the right place for the new "M:\ IS git" layout.

OPERATOR DIRECTIVE -- "MIOS REPOSITORIES BOTH OVERLAYED
AT THE M:\ ROOT". The previous "$MiosRepoDir = M:\MiOS\repo with
mios/ + mios-bootstrap/ as siblings" layout is gone. New layout:

  M:\                  mios.git working tree (M:\.git is mios.git's)
                         + mios-bootstrap.git files overlaid on top
                           (Get-MiOS.ps1, build-mios.ps1, bootstrap.ps1)
  M:\MiOS\               Windows install state (subdirs below)
  M:\MiOS\bin            entry-point .ps1 scripts
  M:\MiOS\share          materialized templates (legacy convenience)
  M:\MiOS\machine-state  podman-machine + WSL2 state
  M:\MiOS\distros        WSL2 distro tarballs
  M:\MiOS\images         BIB output artifacts
  M:\MiOS\logs           install logs
  M:\MiOS\bootstrap-shadow  mios-bootstrap.git's actual checkout (.git lives here
                             so fetch+reset on bootstrap doesn't fight mios.git's
                             .git at M:\); files are robocopied onto M:\ root.

<!-- mios-src:ffcdc3132293 from build-mios.ps1:582-607 -->

### NO-OP by default (final). Kept callable only for legacy...

NO-OP by default (final). Kept callable only for legacy
invocation sites; the function returns immediately unless the operator
explicitly opts in via MIOS_FORCE_LEGACY_MIGRATE=1.

── Why no-op ───────────────────────────────────────────────────

The "C:\\MiOS legacy install -> M:\\MiOS data disk" migration was a
design error. The two surfaces serve DIFFERENT purposes and should
never be merged:

  C:\\MiOS   = developer's git working tree on the Windows host.
              Where the operator edits source, runs git, drives
              Claude Code, etc. Active dev surface.

  M:\\MiOS\\ = bootstrap-created install root for MiOS-DEV runtime
              artifacts: vhdx, icons, themes, machine-state,
              distros, build-output images, logs, plus
              M:\\MiOS\\repo\\ as a Windows-side MIRROR of origin
              (cloned by the bootstrap from origin, NOT migrated
              from C:\\MiOS).

The "full-partition overlay is the LAW" architectural rule applies
INSIDE a running MiOS deployment (the deployed Linux host treats
`/` as a full git working tree against the local Forgejo / cloud
GitHub). It does NOT mean "migrate the developer's Windows-side
working tree onto M:\\".

The previous /MOVE behavior wiped C:\\MiOS files between bootstrap
turns (visible 14:43-14:52 session as a 13-file working-
tree wipe restored via `git checkout HEAD -- ...`) -- a destructive
failure mode for the operator's active dev surface that no
combination of "make it git-aware" or "fence it behind opt-in"
really redeems. The cleanest fix is: don't migrate.

── Bypass switches (env vars; all default off) ─────────────────

  MIOS_FORCE_LEGACY_MIGRATE=1    proceed with destructive
                                 robocopy /MOVE (rare cleanup
                                 scenarios where the operator
                                 KNOWS the legacy root is stale).
  MIOS_SKIP_LEGACY_MIGRATE=1     legacy bypass alias; now the
                                 default behavior, kept
                                 recognized so old recipes
                                 don't error.

<!-- mios-src:7d433d6dee6d from build-mios.ps1:650-694 -->

### Provisions the dedicated MIOS-DEV data disk and re-points...

Provisions the dedicated MIOS-DEV data disk and re-points all
install paths onto it. Idempotent: if M:\ is already a MIOS-DEV-
labeled volume we just redirect; otherwise we shrink C: by the
configured amount and create the partition. Honors:
  $env:MIOS_SKIP_DATA_DISK    - skip everything (legacy /usr/share/mios layout)
  $env:MIOS_DATA_DISK_LETTER  - drive letter (default M)
  $env:MIOS_DATA_DISK_MB      - shrink size in MB (default 262144)

Called BEFORE Phase 2 so the repo clones go directly to the
data disk instead of having to migrate later.

<!-- mios-src:c2c620946e4b from build-mios.ps1:753-762 -->

### Verify [Console]::SetCursorPosition actually moves the...

Verify [Console]::SetCursorPosition actually moves the cursor.
In some hosts (Start-Transcript active, redirected stdout, certain
`irm | iex` parent shells, remote PSSession, captured runspace)
the call silently no-ops or throws -- in either case the dashboard
would just stack frames downward forever. Returns $true only when
we can confidently repaint in place.

<!-- mios-src:a08637db8169 from build-mios.ps1:810-815 -->

### ── Log files...

── Log files ─────────────────────────────────────────────────────────────────
UNIFIED COUNTING SYSTEM: there is exactly one logged counter timeline --
the Write-Log entries written to $LogFile by [IO.File]::AppendAllText.
Show-Dashboard writes directly to the console (in-place repaint via
SetCursorPosition) and is NEVER captured to the log file. This keeps
the log a single chronological event stream instead of being flooded
by hundreds of repainted dashboard frames per minute.

Why no Start-Transcript: Start-Transcript wraps stdout at the host
layer, so [Console]::Write calls from Show-Dashboard get captured.
Each 150ms repaint then duplicates the entire ~20-row dashboard into
the log. Direct file append-only logging avoids this entirely.

<!-- mios-src:310a1660de3e from build-mios.ps1:851-862 -->

### Capture build-mios.ps1's own commit SHA when running from a...

Capture build-mios.ps1's own commit SHA when running from a git
working tree. This is invaluable for diagnosing "is the user
actually running the latest build-mios.ps1?" -- GitHub raw +
Fastly caching can serve a stale outer Get-MiOS.ps1 / cached
mios-bootstrap clone for ~5 minutes after a push, and without
this stamp it's impossible to tell from the log whether a
specific fix was reachable.

<!-- mios-src:db632fbb75c8 from build-mios.ps1:873-879 -->

### Promote to script scope so the dashboard's title can show...

Promote to script scope so the dashboard's title can show it on
every screenshot -- the operator can see at a glance which
commit is actually running, no log-grep required.

<!-- mios-src:f36c31036e21 from build-mios.ps1:890-892 -->

### Console mirroring policy

Console mirroring policy:
  * INFO/DEBUG -> file ONLY. Never Write-Host. The previous code
    said "interactive: mirror every line, Show-Dashboard repaints
    over them" but Show-Dashboard only writes ~25 rows; the
    quadlet-overlay seed alone emits hundreds of INFO lines (file
    update percent x 618, oh-my-posh sub-lines, etc.), drowning
    the dashboard with scrolling text and producing the
    stacked-frame screenshot artifact. The operator sees the
    current step via $script:CurStep on the dashboard's now-line;
    the log file is authoritative for everything else.
  * WARN/ERROR -> file + Write-Host. Operators MUST see these,
    so we surface them above the dashboard. Show-Dashboard's next
    tick scrolls the visible region but the log file always has
    the canonical record.

<!-- mios-src:a8c3eaf6c94a from build-mios.ps1:927-940 -->

### ── MiOS globals (ONE central loader)...

── MiOS globals (ONE central loader) ────────────────────────────────────────
"EXACTLY BUT FOR ALL VARIABLES GLOBALLY!!!!".
Every shared mios.toml value the build pipeline reads is loaded
ONCE here into the $script:Mios* namespace and read by name from
downstream code instead of each site re-calling Get-MiosTomlValue.
Single source-of-truth catalog -- one call site for each toml key.

<!-- mios-src:0a22c8e006a2 from build-mios.ps1:949-954 -->

### ── [terminal] -- framing only ───────────────────────────...

── [terminal] -- framing only ───────────────────────────
cols / rows / scrollback are loaded at top-of-script into
$script:MiosInst{Cols,Rows} (install conhost) + $script:MiosApp{
Cols,Rows} (post-install MiOS app) -- DIFFERENT toml sections
([terminal.install] vs [terminal]) -- so Initialize-MiosGlobals
doesn't touch them to avoid clobbering the install dims with
the app dims.  Frame width / height / right_margin ARE
loaded here because they're identical for both contexts.

<!-- mios-src:d388a8dadd8a from build-mios.ps1:956-963 -->

### Per the self-replication architecture, the Windows side...

Per the self-replication architecture, the Windows side (BootstrapOnly,
the default for irm | iex entry) does ONLY:
  ack -> hardware/env probe -> minimal mios-bootstrap clone ->
  MiOS-DEV podman-machine setup -> .wslconfig sanity ->
  Start Menu / shortcuts -> SSH handoff into MiOS-DEV.
Everything else (identity prompts, OCI build, WSL2/Hyper-V/QEMU
image exports, disk-image generation) belongs INSIDE MiOS-DEV via
/usr/libexec/mios/mios-build-driver -- no Windows-side rendering of
those phases. We render a 6-entry dashboard in BootstrapOnly mode
and the historical 14-entry one in -FullBuild / -BuildOnly mode.

$AppRegPhaseId is the index for the "App registration" phase in
whichever array is active; the Start-Phase / End-Phase callers near
the bottom of the script reference it so we don't hardcode 8 or 5.
Phase names resolve through mios.toml [install_phases.<mode>] (SSOT).
Operator edits via mios.html flow mios.toml -> next install run uses
the new names. Vendor fallback below is the cold first-run set when
no TOML is reachable.

<!-- mios-src:9d9891aab14f from build-mios.ps1:1013-1030 -->

### Last-rendered row count -- used by Show-Dashboard to blank...

Last-rendered row count -- used by Show-Dashboard to blank rows that
were part of a previous larger render but are no longer present in
the current one. Without this, transitioning from a 14-phase layout
to a 6-phase layout (BootstrapOnly mode truncating the tail) leaves
the bottom 8 rows of the previous dashboard as ghost content.

<!-- mios-src:5b2951f1c8ce from build-mios.ps1:1080-1084 -->

### Last-rendered row WIDTH (in columns). Tracks the high-water...

Last-rendered row WIDTH (in columns). Tracks the high-water mark
across renders so a render that ends up narrower than a prior one
(e.g. terminal got resized down by 1 col, [Console]::WindowWidth
reported a smaller value, or the box width clamp dropped from 80
to 79) still pads to the previous max -- otherwise the previous
render's RIGHTMOST column lingers as a vertical ghost stripe of
`+`/`|`/`=` characters running down the right edge of the new
narrower render.

<!-- mios-src:64259c124cc2 from build-mios.ps1:1086-1093 -->

### Build sub-step denominator. In -BootstrapOnly mode we never...

Build sub-step denominator. In -BootstrapOnly mode we never run
the OCI build, so the 48 podman-build steps don't apply -- using
the full 48 makes the dashboard's "0/62" denominator nonsensical
for a 6-phase bootstrap run. Set to 0 here when bootstrap-only;
the full path (-FullBuild / -BuildOnly) bumps it back to 48 once
Phase 8 starts.

<!-- mios-src:a5fd63aaa06d from build-mios.ps1:1096-1101 -->

### ── Render throttle...

── Render throttle ──────────────────────────────────────────────────────
Show-Dashboard is invoked once per stdout line during heavy native
commands (podman build, dnf install, etc.) -- 100+ calls/second
during a layer pull. Each render writes ~25 rows via per-row
SetCursorPosition + Write, and the conhost / WT pseudo-console
tears visibly when repaints land mid-flush. Cap at 10 fps (100 ms
between renders) -- imperceptible lag, no tearing. Force overrides
for end-of-phase / state-change calls that must show NOW.

<!-- mios-src:0238c8c05382 from build-mios.ps1:1172-1179 -->

### ── Sizing -- max 80 cols (standard tty0/console)...

── Sizing -- max 80 cols (standard tty0/console) ──────────────────────────
Pad to BufferWidth, not just WindowWidth. The buffer can be wider
than the visible window (Windows console default = 120-col buffer
in a 80-col window), and log lines written before the dashboard
rendered may have left stale content at buffer columns past the
visible right edge. PadRight(WindowWidth) only clears up to the
visible width; PadRight(BufferWidth) clears every column the log
could have written to. Per the operator's "ics / oder:14b /
GB free)" right-edge bleed in repeated screenshots.

<!-- mios-src:d5561304ef66 from build-mios.ps1:1190-1198 -->

### ── Width strict-clamp...

── Width strict-clamp ────────────────────────────────────────────
The previous code did `winW = max(winW, bufW, DashLastWidth)` to
"blank stale columns from a wider previous render" -- but that
ratchet locks the padding wider than the live buffer for the
rest of the session.

Concrete failure mode (commit 53ac9d8 stacking screenshots):
  1. Load-time resize: 80x30 / 80x9000
  2. `Try-ResizeConsole -Cols 100 -Rows 40` (~line 4501)
     enlarges to 100x40 transiently
  3. First Show-Dashboard: winW=max(100, 100, 0)=100, rows padded
     to 100, DashLastWidth=100
  4. Defensive resize (~line 4395): back to 80x30 / 80x9000
  5. Every later Show-Dashboard: winW=max(80, 80, 100)=100
  6. Writing a 100-char row on an 80-col buffer auto-wraps at
     col 79; 20 chars overflow to the next buffer row; the next
     iteration overwrites cols 0-79 of that row but the
     now-orphaned wrap content from the previous iteration stays
     visible -> the stacked-banner artifact.

Strict-clamp: never pad wider than the LIVE current console.
Capped at 80 for tty0/console portability. If a previous render
was wider than the current, the ghost-row blanking pass below
handles those extra rows; we never need to keep padding wide.

<!-- mios-src:dd0677b60fd9 from build-mios.ps1:1202-1225 -->

### ── Phase table col widths...

── Phase table col widths ────────────────────────────────────────────────
Single table layout used by header / divider / data rows:

  "{0,2} {1,-6} {2,-nameW} {3,5}"
    idx  tag   name        time
    2  +1+ 6  +1+ nameW   +1+ 5  = 16 + nameW

Setting nameW = $in - 16 makes every row land at exactly $in
characters of content, so the right "|" border sits in the same
column on all three rows -- no more zigzag right edge.

<!-- mios-src:55773e7bcdaa from build-mios.ps1:1275-1284 -->

### Stamp the commit SHA in the title so every screenshot of...

Stamp the commit SHA in the title so every screenshot of the
dashboard makes it unambiguous which build-mios.ps1 is running.
Diagnoses Fastly cache lag at a glance: if the operator sees
"(commit abc1234)" but the latest fix you just pushed is def5678,
they're on stale code.

<!-- mios-src:8ed0503289c0 from build-mios.ps1:1293-1297 -->

### ── ONE counter, ONE bar...

── ONE counter, ONE bar ──────────────────────────────────────────────────
Single global step counter (phases + build sub-steps) rendered as
one progress bar. The textual "Phase [N/Total]" and "(step X/Y)"
rows used to duplicate this same metric three different ways and
are intentionally gone -- the bar's "N/M" suffix is THE counter.
Current operation + spinner share one row above the bar so the
operator sees what's running without a second phase-counter line.
Bounds-clamp $script:CurPhase against PhStat.Count -- defensive
against any code path that sets CurPhase past the end of the array
(e.g. Start-Phase 9 in a mode where TotalPhases=6 -- the BootstrapOnly
collapsed layout). Without this clamp, [Console]::Write fires a
"Index was outside the bounds of the array" that gets caught by
MAIN's try/catch and surfaces as the dashboard's FATAL banner.

<!-- mios-src:5981c7762be0 from build-mios.ps1:1324-1336 -->

### Per-row absolute cursor placement. The previous code relied...

Per-row absolute cursor placement. The previous code relied on
NewLine to advance to col 0 of the next row; in wider hosts
(110-160+ col terminals against an 80-cap buffer, or when the
background heartbeat slipped a write between rows) the cursor
could land mid-row, painting subsequent rows offset to the
right -- the visible "side-by-side ghost dashboard" symptom.
SetCursorPosition before each Write guarantees col=0.

<!-- mios-src:8fd6b49ea223 from build-mios.ps1:1400-1406 -->

### No ANSI \e[K -- the operator's terminal sometimes does NOT...

No ANSI \e[K -- the operator's terminal sometimes does NOT
process the escape, in which case the literal "[K" leaks
into the dashboard view (seen in paste). The
strict-clamp on $winW above caps every row at 80 chars
already, so stale content past col 80 from prior renders
is not the concern it was; rely on row-overwrite alone.

<!-- mios-src:a2686596b622 from build-mios.ps1:1411-1416 -->

### ── Ghost-row blanking...

── Ghost-row blanking ────────────────────────────────────────
If a previous render placed MORE rows than this one, blank
those tail rows with a $winW-wide space line so the previous
bottom of the dashboard doesn't linger underneath the new
render. Common cause: BootstrapOnly mode collapses the phase
table from 14 -> 6 rows mid-run; without this loop, phases
6-13 stay visible as orphan text below the new bottom border.

<!-- mios-src:8cb88e41a227 from build-mios.ps1:1419-1425 -->

### DashLastWidth is no longer ratcheted -- the strict-clamp on...

DashLastWidth is no longer ratcheted -- the strict-clamp on
$winW makes the ratchet harmful (locks padding wider than the
live buffer; see comment near top of Show-Dashboard).

<!-- mios-src:94d373834b02 from build-mios.ps1:1438-1440 -->

### Inline progress bar -- prints once at each phase boundary...

Inline progress bar -- prints once at each phase boundary
(called from End-Phase). Counts COMPLETED phases (PhStat
entries >= 2 i.e. OK/FAIL/WARN). 50-cell bar, operator-blue
filled, dim unfilled. NO ANSI cursor manipulation -- earlier
attempts at scroll-region pinning fought PowerShell's normal
output flow and produced garbled banners + interleaved bars.
The bar scrolls with the log; that's the trade-off.

<!-- mios-src:227304334d92 from build-mios.ps1:1493-1499 -->

### Scrub keys from $env:USERPROFILE\.wslconfig's [wsl2]...

Scrub keys from $env:USERPROFILE\.wslconfig's [wsl2] section that
don't belong there. The most common mis-placement is `systemd=true`,
which is a /etc/wsl.conf [boot] directive (per-distro, INSIDE the
distro's filesystem) -- never a .wslconfig [wsl2] directive
(host-side, Windows). When wsl.exe parses .wslconfig and finds an
unknown key it prints:

    wsl: Unknown key 'wsl2.systemd' in C:\Users\...\.wslconfig

Older wsl versions treat that as a warning, newer ones can fail
the parse entirely. Either way the line ends up in our Phase 3
podman-init pipeline capture and surfaces as a FATAL with the
warning text (because the dashboard displays the LAST stderr line
captured before podman exits non-zero).

This helper runs once at the end of Phase 0 so every subsequent
WSL/podman invocation in the build sees a clean .wslconfig.

<!-- mios-src:9eaa354839f4 from build-mios.ps1:1581-1597 -->

### BOM-free

BOM-free: PS 5.1 `Set-Content -Encoding UTF8` writes a UTF-8 BOM, and a
leading BOM makes WSL silently IGNORE the [wsl2] section (the operator's
memory/processor limits are dropped). WriteAllLines + UTF8Encoding($false)
is BOM-free on 5.1 AND pwsh 7. install-robustness.

<!-- mios-src:385f74fd1631 from build-mios.ps1:1633-1636 -->

### Invoke a native command with stderr collected into the...

Invoke a native command with stderr collected into the success stream
but WITHOUT the "$ErrorActionPreference='Stop' + 2>&1" trap that
causes a chatty stderr (git's "Cloning into ...", "From https://...",
"Receiving objects: ...") to surface as a fatal exception. Returns
the command's $LASTEXITCODE so callers can do their own checks. Kept
minimal -- callers that want to inspect stdout/stderr can swap to
Invoke-NativeQuiet's variable-capture variant below.

<!-- mios-src:b062c1fa7463 from build-mios.ps1:1642-1648 -->

### Post-bootstrap interactive menu. Called from the...

Post-bootstrap interactive menu. Called from the BootstrapOnly path
in MAIN after Install-MiosLauncher has dropped the Start Menu /
Desktop shortcuts -- the operator now has a fully-provisioned dev
VM + Windows-side surface and chooses what to do next from here:

  1. Continue to build      -> re-invoke this script with -BuildOnly
                               so the OCI image build runs against
                               the freshly-provisioned MiOS-DEV.
  2. Change settings         -> open the configurator HTML for an
                               interactive mios.toml edit pass
                               (Open-Configurator).
  3. System checks           -> run preflight.ps1 against the
                               current state (MiOS-DEV health,
                               mios.toml validation, .wslconfig,
                               disk space, GHCR token).
  4. Logs / reports          -> print the unified log path + the
                               last 30 lines.
  5. Close                   -> exit cleanly.

Skipped automatically when -Unattended is set (CI / non-interactive).

<!-- mios-src:40645f28a8fd from build-mios.ps1:1661-1680 -->

### Resolve the actual WSL distro name once -- podman-machine...

Resolve the actual WSL distro name once -- podman-machine prefixes
its distros with `podman-` (so the on-disk distro is podman-MiOS-DEV
by default), the auto-rename to plain MiOS-DEV is opt-in via
MIOS_RENAME_DISTRO=1, and operators commonly type `wsl -d MiOS-DEV`
only to hit `WSL_E_DISTRO_NOT_FOUND`. Print the live name so the
operator can copy-paste it.

<!-- mios-src:34fb348ce308 from build-mios.ps1:1684-1689 -->

### Clear the screen before every menu render so the canvas is...

Clear the screen before every menu render so the canvas is
always clean -- whether this is the first render after
bootstrap OR a re-render after the operator picked an
option (wsl entry, configurator, etc.) and returned. Any
output from the previous option (wsl session output, build
tail, etc.) is wiped so the menu draws against blank space.

<!-- mios-src:d2fbde1b1020 from build-mios.ps1:1700-1705 -->

### ── Windows -> MiOS-DEV handoff (per self-replication...

── Windows -> MiOS-DEV handoff (per self-replication contract) ──
The Windows side has finished its STRICT scope: ack +
MiOS-DEV podman-machine setup. The actual build (OCI +
WSL2/g + Hyper-V + QEMU + Live-CD + USB + RAW) runs
INSIDE MiOS-DEV. We open a fresh Windows Terminal tab
hosting `wsl.exe -d <distro>` -- the MiOS-DEV tty
renders the dashboard there directly, no streaming
back across the WSL/Windows boundary.

<!-- mios-src:eedbe07d2ea7 from build-mios.ps1:1733-1740 -->

### The driver lives in the MiOS image at...

The driver lives in the MiOS image at /usr/libexec/mios/mios-build-driver.
Phase 3's quadlet-overlay drops it into MiOS-DEV, so by the time the
operator picks "1" the file is present. We invoke it directly with a
SINGLE-LINE bash command -- multi-line heredocs survive PowerShell -> wt
-> wsl arg-parsing only if every layer quotes correctly, and previously
the chain shredded a heredoc into pseudo-args, surfacing as
    [error 2147942402 (0x80070002): The system cannot find the file specified.]
at wt.exe spawn time. Single-line, single-quoted-on-bash-side, no escapes.

<!-- mios-src:ef8180ec3d63 from build-mios.ps1:1753-1760 -->

### Open a NEW Windows Terminal window at exactly 80x30 to...

Open a NEW Windows Terminal window at exactly 80x30 to
match the dashboard frame (per feedback_mios_terminal_
dimensions.md). `wt.exe --size W,H -- <cmdline>` sets
the initial dimensions of a NEW wt window; `new-tab`
inherits whatever the parent window already has, which
is wrong for the build-pipeline tty.

<!-- mios-src:31b664b89caf from build-mios.ps1:1783-1788 -->

### Resolve which user actually exists in the distro before...

Resolve which user actually exists in the distro
before launching. Rootful machine-os ships with
`core` (and root) but no `mios` user until the
OCI build completes -- in which case --user mios
fails with WSL_E_USER_NOT_FOUND. Probe the
distro's /etc/passwd to pick the first available
account in priority order: mios > core > root.

<!-- mios-src:26cb6b998001 from build-mios.ps1:1863-1869 -->

### NB: Windows PowerShell 5.1 (the universal elevation...

NB: Windows PowerShell 5.1 (the universal elevation fallback in
Get-MiOS.ps1's chain) doesn't support the PS7 ternary operator,
so this stays as a plain if/else.

<!-- mios-src:d113c4e4e3da from build-mios.ps1:1898-1900 -->

### AI model menu prompt -- feature parity with build-mios.sh's...

AI model menu prompt -- feature parity with build-mios.sh's
prompt_model. Drives MIOS_LLAMACPP_BAKE_MODELS at build time and
MIOS_AI_MODEL in install.env at runtime. Same auto-accept
semantics as the rest of the Phase-6 prompts. The lineup is
sourced from mios.toml [ai.host_thresholds] (the RAM-tier table)
so the menu never drifts from the SSOT -- the three options map
1:1 onto small/mid/big_ram_model plus a custom escape hatch.

<!-- mios-src:da53c345efa6 from build-mios.ps1:1905-1911 -->

### Read [ai].model / [ai].embed_model / [ai].bake_models out...

Read [ai].model / [ai].embed_model / [ai].bake_models out of the
unified mios.toml dotfile. Walks the same layered overlay
build-mios.sh's resolve_profile_layers walks, so per-host edits
to /etc/mios/mios.toml or ~/.config/mios/mios.toml seed the
interactive prompt without re-cloning. Pure regex parser; no TOML
library dependency. Returns a hashtable -- caller picks fields.
Vendor fallbacks mirror the SSOT [ai] section (model / embed_model)
so an absent/unreadable card lands on the same values the canonical
mios.toml declares; bake = model + embed.

<!-- mios-src:9ce2526ea64d from build-mios.ps1:1936-1944 -->

### Open /usr/share/mios/configurator/mios.html for the...

Open /usr/share/mios/configurator/mios.html for the operator to
edit the unified mios.toml. Canonical path: launch Epiphany IN
MiOS-DEV via WSLg so the configurator runs inside the same
environment that built it. The window appears on the Windows
desktop; the saved mios.toml lands in the dev VM's FHS-compliant
~/Downloads (which IS the bootc-style home/user/Downloads
location, since MiOS-DEV mirrors the deployed MiOS layout). The
PowerShell side then picks up that file and overlays it as the
new source for the build pipeline -- so the operator's Epiphany
save IS the build's input.

Falls back to the operator's default Windows browser if MiOS-DEV
isn't reachable or Epiphany is unavailable (covers fresh installs
before the dev distro has finished provisioning).

<!-- mios-src:d53fc99f5db8 from build-mios.ps1:2001-2014 -->

### Seed the working mios.toml in ~/Downloads. The...

Seed the working mios.toml in ~/Downloads. The configurator's "Pick file"
button binds to it; "Save" overwrites in place (File System Access API)
or, if the WebKit build lacks FSA, the operator triggers a download that
also lands here.

<!-- mios-src:9a7c38aa2165 from build-mios.ps1:2091-2094 -->

### Pick up the saved mios.toml from MiOS-DEV's ~/Downloads and...

Pick up the saved mios.toml from MiOS-DEV's ~/Downloads and
promote it as the build source. We write to BOTH:
  1. %APPDATA%\MiOS\mios.toml   -- runtime per-user overlay
  2. mios-bootstrap clone root   -- seed-merge inputs to podman build
so the very next build/install pass uses the operator's edits.

<!-- mios-src:3b20932f4fa1 from build-mios.ps1:2156-2160 -->

### Legacy / fallback path

Legacy / fallback path: run the configurator in the operator's
default Windows browser. Used when MiOS-DEV isn't reachable yet
(e.g. fresh install before Phase 3 finishes) or when WSLg is
disabled. Saves go through the Windows Downloads folder via the
standard <input type="file"> + downloads flow.

<!-- mios-src:84d1d4f0c9b0 from build-mios.ps1:2183-2187 -->

### Detect host capability

Detect host capability: full CPU / RAM / disk / GPU surface.
Then apply mios.toml [bootstrap.dev_vm.host_reserve] to compute
the dev-VM allocation. The dev VM IS the builder (memory:
feedback_mios_dev_is_the_builder), so we err maximalist — give
it every resource the host can spare while keeping Windows
responsive.

Override sources (highest precedence first):
  1. $env:MIOS_DEV_VM_{CPUS,MEMORY_MB,DISK_GB} — explicit pin
     from mios.toml [bootstrap.dev_vm].* if not set to "max"
  2. $env:MIOS_DEV_VM_*_RESERVE_* — host reserve policy from
     mios.toml [bootstrap.dev_vm.host_reserve]
  3. Hardcoded fallbacks below

<!-- mios-src:aab89ba72c18 from build-mios.ps1:2262-2274 -->

### Pre-stage a podman-machine OCI image via direct HTTPS...

Pre-stage a podman-machine OCI image via direct HTTPS, bypassing
`podman machine init`'s pull-extraction pipeline. On podman 5.8.2
for Windows + WSL provider that pipeline fails with:
    Error: failed to pull quay.io/podman/machine-os@sha256:<...>:
           The system cannot find the path specified.
for ANY ref (6.0, 5.8, bundled default). Direct GET against the
OCI Distribution API works fine -- the bug is in podman's own
cache write step on Windows. Pre-staging the layer ourselves and
passing the result to `--image <local-path>` skips the broken
path entirely.

Returns the local file path on success; throws on failure. The
output filename follows the layer's
`org.opencontainers.image.title` annotation
(e.g. "podman-machine.x86_64.wsl.tar.zst") so podman recognizes
the format from the extension alone.

<!-- mios-src:ff51a2989ded from build-mios.ps1:2564-2579 -->

### ── Step 1: image index...

── Step 1: image index ───────────────────────────────────────────
PowerShell 5.1's Invoke-WebRequest -UseBasicParsing returns
.Content as a byte[] for non-text content types (anything not
in its hard-coded text list -- application/json IS text but
application/vnd.oci.image.index.v1+json is NOT, despite the
`+json` suffix). Piping the byte[] to ConvertFrom-Json
stringifies the array to "123 34 115 ..." and produces an empty
object -- which is exactly the "got mediaType=" symptom seen in
the 16:35 log. Force UTF-8 decode so ConvertFrom-Json sees the
actual JSON text.

<!-- mios-src:95d3bc0cb870 from build-mios.ps1:2601-2610 -->

### Force the podman-managed WSL2 distro VHDX onto M:\. WSL2...

Force the podman-managed WSL2 distro VHDX onto M:\. WSL2 ignores
XDG_DATA_HOME; it stores VHDXs at the path passed to `wsl --import`
(or under %LOCALAPPDATA%\Packages\<distro-id>\LocalState if podman
didn't pass an explicit path). The registry HKCU\...\Lxss\<guid>\
BasePath records where each distro's ext4.vhdx actually lives.

Procedure (idempotent, only fires when BasePath is NOT under M:\):
  1. Read BasePath from registry
  2. If already on M:\ -> no-op + log
  3. Else: wsl --shutdown, export tar, unregister, import to
     M:\MiOS\distros\<distroname> -- VHDX bytes now live on M:\

podman picks the distro back up because podman locates it by name
via wsl.exe -- the import path doesn't matter to podman's
connection state.

<!-- mios-src:9f9d7efd768d from build-mios.ps1:2720-2734 -->

### Redirect podman-machine state (the VHDX, registry, configs)...

Redirect podman-machine state (the VHDX, registry, configs) onto
M:\ when M:\ is mounted -- no admin required. Podman honors
XDG_DATA_HOME for storage paths on Windows (machine-state lands
at <XDG_DATA_HOME>\containers\podman\machine). This is the
non-admin path equivalent of Set-PodmanMachineStorageOn's
mklink /D approach (which requires elevation).
Without this, the dev distro's VHDX (multi-GB, grows during the
OCI build) lands on C: instead of the operator's M:\ partition.

<!-- mios-src:66af3c871081 from build-mios.ps1:2814-2821 -->

### $HW.RamGB is already the maximalist-minus-host-reserve...

$HW.RamGB is already the maximalist-minus-host-reserve allocation
computed by Get-Hardware (per mios.toml [bootstrap.dev_vm.host_reserve]).
Multiply to MB and clamp once more against the OS-reported total
(what podman validates; nominal Win32_PhysicalMemory rounds up and
would otherwise cause podman to reject the request) minus a 512 MB
safety margin. Floor of 4096 MB so the dev VM is always usable.

<!-- mios-src:382561e945bf from build-mios.ps1:2830-2835 -->

### ── Pre-stage machine-os via direct HTTPS...

── Pre-stage machine-os via direct HTTPS ──────────────────────────────────
On podman 5.8.2 (Windows + WSL provider) the in-process pull pipeline
fails for ANY machine-os ref with "system cannot find the path
specified". Direct OCI-Distribution GET against quay.io works fine,
so we fetch the wsl-x86_64 layer ourselves and hand podman a local
`.tar.zst` path -- no registry pull happens inside podman at all.

Default tag: 6.0 (per operator instruction). Override with
MIOS_MACHINE_TAG=<tag> or MIOS_MACHINE_IMAGE=<docker:// url> for a
specific ref; pre-stage runs in both cases.
Default machine image sourced from mios.toml [bootstrap.dev_vm].
base_image (vendor default: quay.io/podman/machine-os:6.1). Env var
MIOS_MACHINE_TAG / MIOS_MACHINE_IMAGE still wins for ad-hoc overrides.

<!-- mios-src:45675a8e83ed from build-mios.ps1:2845-2857 -->

### Retry-with-backoff loop. quay.io has been intermittently...

Retry-with-backoff loop. quay.io has been intermittently
502/503-ing during peak hours; without retry, a 5-minute
outage kills the entire bootstrap. 3 attempts with 5s/15s/30s
backoff covers most transient registry blips. Cache-hit
short-circuit inside Get-PodmanMachineOsImage means a
successful prior fetch makes subsequent retries instant.

<!-- mios-src:81948ac249a7 from build-mios.ps1:2878-2883 -->

### Retry schedule from mios.toml...

Retry schedule from mios.toml [network.retry].delays_seconds
(vendor default: 0s, 5s, 15s, 30s). Operator can lengthen for
known-flaky upstreams via the configurator HTML.

<!-- mios-src:c4ef08ff7928 from build-mios.ps1:2886-2888 -->

### Build the arg list dynamically so --image is only passed...

Build the arg list dynamically so --image is only passed when the
operator (or env override) has supplied one. With no --image,
podman init uses its bundled default -- always compatible with
the installed client version.

<!-- mios-src:a310dfefa426 from build-mios.ps1:2926-2929 -->

### Wrap the init invocation in a fresh child scope with...

Wrap the init invocation in a fresh child scope with
$ErrorActionPreference='Continue'. Without this, podman's normal
post-start stderr line (e.g. "API forwarding for Docker API
clients is not available...") trips the script's outer EAP=Stop
via the 2>&1 stream merge and surfaces as a Phase 3 FATAL even
though `podman machine init` exited 0 and the machine is fully
up. $LASTEXITCODE survives the scope exit (it's an automatic
variable populated globally by every native command invocation),
so the if-($initRc -ne 0) check below sees the real exit code,
not a phantom from a stream-merged warning.

<!-- mios-src:efbb3fecb135 from build-mios.ps1:2942-2951 -->

### ── Recovery branch 1: pull failed on a pinned --image...

── Recovery branch 1: pull failed on a pinned --image ──────────────────
Pinning $MachineImage to a tag the operator's installed podman client
can't pull (typical: docker://quay.io/podman/machine-os:6.1 against a
podman 5.8 client) produces:
    Error: failed to pull quay.io/podman/machine-os@sha256:<digest>:
           The system cannot find the path specified.
init exits 125 BEFORE creating any registration, so there's no
cleanup needed -- just retry without --image so podman uses its
bundled default (which the client always knows how to handle).
The fallback is logged so the operator sees they're on a
fallback tag and can `winget upgrade Podman.Podman` to actually
land on their requested pin.

<!-- mios-src:5300ae160168 from build-mios.ps1:2971-2982 -->

### "VM already exists" -- recover by starting (or treating as...

"VM already exists" -- recover by starting (or treating as already
running) instead of failing. Caller's outer loop already tried to
detect a running machine; we got here because the registration
exists but `podman machine ls` didn't expose it as running, which
also matches Windows Subsystem for Linux's transient ghost state
right after a previous interrupted init. Best response is just to
try starting it and verify the API.

<!-- mios-src:02043a63124c from build-mios.ps1:3013-3019 -->

### MUST wrap in EAP=Continue +...

MUST wrap in EAP=Continue + PSNativeCommandUseErrorActionPreference=$false:
podman returns non-zero on "already running" (which IS our happy
path here), and PS 7.4+ defaults PSNativeCommandUseErrorActionPreference
to $true -- so a non-zero exit throws BEFORE the regex match below
can downgrade it to a Log-Ok. The init call uses the same wrap; this
one was missing it and threw straight to the outer FATAL handler.

<!-- mios-src:efb648afb228 from build-mios.ps1:3022-3027 -->

### Start failed too -- registration is stale or the VM is in a...

Start failed too -- registration is stale or the VM is in
a half-provisioned state from a SIGINT'd previous run.
Force-remove the registration and re-init from scratch.
Safe at this point in the pipeline: no MiOS image / no
operator data lives in the build VM yet.

<!-- mios-src:0910197e9d58 from build-mios.ps1:3045-3049 -->

### v3: WSL unregister chain + final `wsl --shutdown` to fully...

v3: WSL unregister chain + final
`wsl --shutdown` to fully reset the WSL2 service state
before retry-init.  Previous v2 (commit c434302) got
past the getpwnam crash but the retry-init then hit
`Wsl/Service/RegisterDistro/E_FAIL ... Error code: 6,
failure step: 2` (= WSL_E_VM_MODE_INVALID_STATE) --
the WSL service was in a transient bad state from
the unregister + reparse-point-removal cycle, and
`wsl --import` to the M:\ path failed.  `wsl
--shutdown` forces a clean lifebooot of the WSL2
subsystem so import lands cleanly.  Whole block in
EAP=Continue so non-zero exits don't throw to FATAL.

<!-- mios-src:55cac30c51ae from build-mios.ps1:3053-3064 -->

### Sweep ALL candidate podman-machine storage paths...

Sweep ALL candidate podman-machine storage paths
unconditionally. A previous run (admin or otherwise)
may have left:
  * a dangling symlink ([Test-Path] returns false on
    these because PS resolves the target -- so the
    prior dangling-only check missed them entirely)
  * a non-dangling symlink to a now-stale target
  * a real directory with stale machine state
ANY of these can make podman init's Mkdir() fail
with "Cannot create a file when that file already
exists". After `podman machine rm --force` the VM
registration is gone, so the on-disk state in these
paths is unambiguously safe to wipe.

DirectoryInfo lets us probe both regular dirs AND
reparse points without follow-the-link semantics --
Test-Path's "exists" check fails on dangling links.

<!-- mios-src:3e32e8cb1226 from build-mios.ps1:3088-3104 -->

### Tolerate a non-zero rmdir exit

Tolerate a non-zero rmdir exit: the junction may already be gone
(dangling target / prior run / race). Under PS7 a native non-zero
exit THROWS under EAP=Stop, which previously FATAL'd the whole
install here ("The system cannot find the file specified"). Isolate
it in an EAP=Continue scope (same guard as the retry-init below) so
an already-absent link is a no-op, not a fatal.

<!-- mios-src:507c478432b9 from build-mios.ps1:3135-3140 -->

### ── Force the podman-MiOS-DEV WSL distro onto M:\...

── Force the podman-MiOS-DEV WSL distro onto M:\ ────────────────────
Operator: "podman-MiOS-DEV MUST also be located on M:\". XDG_DATA_HOME=
(4th time): "I have told you the broken
MiOS-DEV machine is due to relocation and renaming breaking
the connections!!!".  Move-PodmanWslDistroToM does a
wsl --export + unregister + import which breaks podman's
internal machine state (podman's config files reference the
old VHDX path; after import the distro has the same name but
podman doesn't recognize it as the same machine -- subsequent
`podman machine` commands fail with "machine not found" /
`wsl ... getpwnam(root) failed 5`).

Per memory feedback_mios_distro_name_locked +
feedback_mios_dev_on_m_drive: junctions ONLY, never re-import.
The XDG_DATA_HOME=M:\podman set at the top of New-BuilderDistro
+ the reparse-point junctions on every podman-machine candidate
path (Set-PodmanMachineStorageOnM, called from Initialize-DataDisk)
already redirect new VHDX writes to M:\ at podman init time --
no migration needed.

Gated behind $env:MIOS_FORCE_VHDX_MIGRATE=1 for the rare case
where the junction approach fails on a host (e.g., admin denied
symlink creation).  Default is to SKIP the migration entirely.

<!-- mios-src:80c8955a2515 from build-mios.ps1:3183-3205 -->

### Use `podman machine inspect --format {{.State}}` -- it...

Use `podman machine inspect --format {{.State}}` -- it returns the
canonical state string ("running" / "starting" / "stopped"). The
older `podman machine ls --format {{.Running}}` boolean is broken on
podman 5.8: it returns "false" for several seconds AFTER the machine
is actually up (LastUp shows "Currently starting" while State is
already "running"). Inspect.State flips first and is what podman
itself uses for socket-readiness gating.

<!-- mios-src:c290bf1781ad from build-mios.ps1:3220-3226 -->

### DEPRECATED bare invocation is a silent no-op. Original...

DEPRECATED bare invocation is a silent no-op.

Original purpose: read mios.toml packages.* sections
from the cloned mios.git checkout and run `dnf5 install` per
block inside MiOS-DEV. Replaced by Invoke-MiosQuadletOverlay
(which makes / a git working tree of mios.git) plus
automation/lib/packages.sh (which resolves mios.toml
[packages.<section>].pkgs as the SSOT).

Per project_mios_self_replication_vision.md the package surface
is now baked into the OCI image at build time and made live on
MiOS-DEV via `bootc switch` + reboot at the end of the
mios-build-driver flow. There's no more "live overlay" install
step on the Windows side -- the dev VM gets the same packages
by becoming the OCI image, not by running dnf at the host level.

Force-enable for testing-only via MIOS_FORCE_LEGACY_PACKAGES_MD=1
(intentionally undocumented in the operator-facing flow).

<!-- mios-src:9698fff64a1a from build-mios.ps1:3249-3266 -->

### Resolve the dev-overlay section list from the user's...

Resolve the dev-overlay section list from the user's mios.toml. The
layered resolver (highest wins): per-user (~/.config/mios/mios.toml),
host (/etc/mios/mios.toml), bootstrap clone, vendor. The PowerShell side
stages the highest-precedence layer at $SRC_TOML before invoking us.
Falls back to a hardcoded minimal list if no [packages.dev_overlay].sections
array is present.

<!-- mios-src:46ee232f34d4 from build-mios.ps1:3324-3329 -->

### Hard always-skip list. This wins even if the operator typed...

Hard always-skip list. This wins even if the operator typed e.g.
"kernel" into mios.toml -- those sections are WSL-incompatible or
anti-pattern fences and refusing them is the right move.

<!-- mios-src:06fc3e1c1c18 from build-mios.ps1:3404-3406 -->

### Install a wrapper at /usr/local/bin/mios-dev-seed so the...

Install a wrapper at /usr/local/bin/mios-dev-seed so the operator can
re-run the overlay manually inside the dev distro after editing
mios.toml (e.g. `wsl -d podman-MiOS-DEV -- sudo mios-dev-seed`).

<!-- mios-src:98885466ea32 from build-mios.ps1:3439-3441 -->

### Materialize the script + a copy of mios.toml inside the...

Materialize the script + a copy of mios.toml inside the distro
via stdin; avoids cross-FS quoting headaches and works for both
/mnt/c-mounted paths and rootful machines.
CRLF -> LF: PowerShell @'...'@ here-strings produce CRLF on
Windows; without normalization the bash shebang becomes
"#!/usr/bin/env bash\r" -> "env: 'bash\r': No such file or
directory" -> the entire overlay silently no-ops on the dev VM.

<!-- mios-src:913f7bc40689 from build-mios.ps1:3460-3466 -->

### Mirror the MiOS FHS overlay (Quadlets, systemd units...

Mirror the MiOS FHS overlay (Quadlets, systemd units, sysusers,
tmpfiles, libexec, profile.d, /etc/mios config templates) onto the
dev distro so MiOS-DEV runs the same container surface as a deployed
MiOS host. After this:
  - Podman Desktop (Windows) sees mios-cockpit-link, mios-forge, etc.
    under the MiOS-DEV machine connection -- each carries
    io.podman_desktop.openInBrowser labels for one-click access.
  - Cockpit on the dev VM (https://localhost:9090, mirrored networking)
    renders the same containers + system services as a deployed host.

Idempotent via /var/lib/mios/.quadlet-overlay-seeded; re-runs are no-ops
unless the source mios.git Containerfile has been touched since the
sentinel. Set MIOS_SKIP_DEV_QUADLETS=1 to bypass entirely.

<!-- mios-src:47343ab389ac from build-mios.ps1:3482-3494 -->

### NOTE

NOTE: an earlier version of this function early-returned here based
on podman machine inspect.Rootful, on the theory that rootful
machine-os distros aren't wsl.exe-accessible. Modern WSL handles
rootful machine-os fine and the contract `MiOS-DEV ≡ MiOS` requires
the dev VM to have the same Quadlets / containers / units as a
deployed MiOS host AS EARLY AS POSSIBLE -- not deferred to the OCI
build phase. Letting the wsl.exe probe below decide gates the
overlay on actual capability rather than an a-priori assumption.
(The OCI build path still re-applies the overlay later via the
baked-in image; if the install-time overlay succeeds, it's a no-op
post-bootc-switch via the sentinel check.)

<!-- mios-src:2c1942fb557b from build-mios.ps1:3502-3512 -->

### Probe wsl.exe with a hard timeout. Rootful machine-os...

Probe wsl.exe with a hard timeout. Rootful machine-os distros
are NOT wsl.exe-accessible, and `wsl.exe --exec` on them hangs
indefinitely instead of erroring -- which made the build freeze
at "Overlaying MiOS Quadlets + systemd units" with no progress.
8-second timeout per candidate; if both time out, the overlay
is deferred (matches the rootful-machine-os documented behavior).

<!-- mios-src:bab3afc6cdbb from build-mios.ps1:3521-3526 -->

### PROJECT INVARIANT

PROJECT INVARIANT: MiOS treats the deployed root `/` AS the git
working tree of mios.git on EVERY deploy shape -- bare-metal,
Hyper-V, QEMU, WSL distro, AND the Windows-side podman-WSL2 dev VM.
`git init` at `/`, point origin at the cloned mios.git checkout
(later swappable to the self-hosted Forgejo at localhost:3000),
`fetch + reset --hard`, and now every mios.git tracked file is at
its FHS path on `/` in one operation -- no tar-list to maintain,
no missing-file bugs, full parity with the deployed system.

Safety: `git reset --hard FETCH_HEAD` only touches FILES TRACKED
IN mios.git. Untracked Fedora-base paths (/etc/passwd, /var/lib/
dnf, ~/.bash_history, /var/log, etc.) are left alone -- they are
not in mios.git and git's reset doesn't enumerate them. The repo's
root .gitignore further declares which `/etc/*`, `/var/*`, etc.
subtrees stay host-managed.

<!-- mios-src:2c50a9fcdbbc from build-mios.ps1:3593-3607 -->

### ── Universal mios.git overlay sync...

── Universal mios.git overlay sync ──────────────────────────────────────────
Works identically across every MiOS deploy shape:
  - Bare-metal bootc (mios:latest deployed)
  - Hyper-V VHDX / QEMU qcow2 / RAW disk image
  - WSL2/g distros (mios:latest imported via wsl --import)
  - Podman-WSL dev VM (the canonical podman-MiOS-DEV pre-bootc-switch)
  - Podman / Podman Desktop (Windows + Linux native)
  - Traditional FHS installs (mios.git overlaid into / via install.sh)

Architectural Law 3 ".git IS /": the deployed root is always a git
working tree of mios.git. This sync brings / up to origin/main using
the FASTEST available source given the deploy context.

Per WSL filesystem-performance guidance
(learn.microsoft.com/en-us/windows/wsl/filesystems):
  "For the fastest performance speed, store your files in the WSL
   file system if you are working in a Linux command line."
So all git operations target a NATIVE-ext4 bare-clone cache at
$CACHE_DIR; /mnt/m (DrvFs / 9P) is only ever consulted as a one-shot
offline-bootstrap source for the cache itself.

<!-- mios-src:5a164b94c9f1 from build-mios.ps1:3609-3628 -->

### Statically enable mios-ai-firstboot via a .wants symlink...

Statically enable mios-ai-firstboot via a .wants symlink rather than
`systemctl enable --now`. During the overlay the VM's system bus is
transitional ("Transport endpoint is not connected"), so enable --now for
this long-running oneshot fails; a symlink is D-Bus-independent and lets the
firstboot run on the FIRST CLEAN BOOT, when the bus + ollama are up. It
self-heals (sentinel only on full success) and builds the venv + GGUFs there.

<!-- mios-src:ffbd9ec7d2f2 from build-mios.ps1:3737-3742 -->

### Globally enable the OPERATOR-side launcher broker...

Globally enable the OPERATOR-side launcher broker (mios-launcher.service, a
USER unit) the same D-Bus-independent way: a .wants symlink in the GLOBAL
user target dir so the operator's user manager starts it (ConditionUser=mios
gates it to that user). Without this the broker ships DISABLED -> the socket
/run/mios-launcher/launcher.sock is never created -> EVERY OS-control verb
(open_app, etc.) fails "broker socket missing" and the agent cannot drive
Windows/Linux apps ("open notepad" -> "LIAR"). The broker
is what lets MiOS AI actually control the OS. install-robustness.

<!-- mios-src:980530ad6404 from build-mios.ps1:3748-3755 -->

### Top-of-root SSOT shortcuts

Top-of-root SSOT shortcuts: mios.toml + configurator HTML at /
so operators can `cat /mios.toml` and open `file:///configurator.html`
from the dev VM browser. The deployed root IS the git working tree
of mios.git, so these symlinks live in the same view as /.git --
the operator's "single source of truth" surface is one cd / away.

<!-- mios-src:cfa674036243 from build-mios.ps1:3773-3777 -->

### Render Quadlet ${MIOS_*} placeholders BEFORE systemd's...

Render Quadlet ${MIOS_*} placeholders BEFORE systemd's podman
generator runs at daemon-reload. The .container files at
/etc/containers/systemd/*.container ship raw `${VAR:-default}`
placeholders (Image=, PublishPort=, User=, Group=, Network=, ...);
systemd's Quadlet generator does NOT expand them, so podman gets
the literal string `${MIOS_PORTS_LLM_LIGHT` (split on the `:` of
`:-8450`) and dies with:
    Error: cannot parse "${MIOS_PORTS_LLM_LIGHT" as an IP address
Every Quadlet stays in `activating auto-restart` and `podman ps`
is empty. Operator-flagged (containers all dead after
install).

automation/34-render-quadlets.sh walks the four Quadlet search
dirs, resolves the placeholders against the layered mios.toml
(vendor < host < user) via tools/lib/userenv.sh, and writes the
rendered files back in place. The deployed bootc image builds run
this at image-build time; the dev-VM overlay path does NOT, so
we run it here. Idempotent: re-runs against an already-rendered
.container are a no-op (envsubst sees no remaining placeholders).
install-robustness the dev-VM overlay never ran 59-tools.sh
(which deploys tools/lib/userenv.sh -> /usr/lib/mios/userenv.sh, the env-bridge
resolver) NOR mios-sync-env -- so /etc/mios/install.env was never generated,
leaving the AI plane INERT on a fresh install: empty bake_models -> no GGUFs ->
mios-llm-light skipped, and unresolved MIOS_PORT_* templates -> agent-pipe 502.
Deploy the resolver + generate the bridge HERE so 15-render-quadlets below AND
the firstboot services (EnvironmentFile=/etc/mios/install.env) see resolved
values. Both idempotent; LIVE-verified this is the keystone that brought a
fresh dev VM's MiOS AI fully operational on the GPU.

<!-- mios-src:aa73e4728910 from build-mios.ps1:3782-3809 -->

### Realize sysusers + tmpfiles, then reload systemd so the new...

Realize sysusers + tmpfiles, then reload systemd so the new units
(and Quadlet-generated *.service files) are visible.

Critical: `wsl --exec` lands in the OUTER WSL namespace, not the
nested process namespace where systemd actually runs (per the
podman-machine welcome banner). Bare `systemctl daemon-reload`
from this context fails with "Failed to set unit properties:
Transport endpoint is not connected" / "Reload daemon failed".
nsenter into systemd's PID with -a (all namespaces) gives the same
view an interactive `wsl -d <distro>` session has, so systemctl
reaches its bus and units register correctly.

<!-- mios-src:7acdd37cbaf1 from build-mios.ps1:3828-3838 -->

### Set MiOS-DEV's default WSL2 user to mios (sysusers just...

Set MiOS-DEV's default WSL2 user to mios (sysusers just created uid
1000=mios above). Without this, `wsl -d podman-MiOS-DEV` lands on
whatever the machine-os tarball seeded as default (typically a bare
`user` UID 1000, which exists but has none of the mios HOME / shell
/ groups setup). /etc/wsl.conf is read once at distro start, so the
next `wsl --terminate podman-MiOS-DEV` + reentry picks this up.
Idempotent: only ADDS [user] block if not already present.

<!-- mios-src:0d0b048a2fd2 from build-mios.ps1:3853-3859 -->

### [boot].systemd=true is REQUIRED for `systemctl...

[boot].systemd=true is REQUIRED for `systemctl is-system-running`,
Quadlet generators, mios-flatpak-install.service, and every other
systemd-coupled feature inside the WSL distro. Without it, WSL boots
without systemd as PID 1; smoke tests then see state='offline' and
the build pipeline can't poll service state. WSL >= 0.67.6 honors
this directive on next `wsl --terminate` + reentry.

<!-- mios-src:badef5535588 from build-mios.ps1:3861-3866 -->

### Container-host prerequisites for the mios user. Manifesto...

Container-host prerequisites for the mios user. Manifesto says MiOS-DEV
"should have the mios user appended as it will be needed for this MiOS-DEV
machine to host its containers (mirroring the layered containers in MiOS
at build time; guacamole, ollama, forgejo, cockpit etc-etc)". The
systemd-sysusers run above creates the mios login user (uid 1000); the
three steps below complete the container-hosting plumbing:

  1. subuid/subgid append -- rootless podman needs an unprivileged uid
     range available for user-namespace mapping. Standard convention is
     one 64K-uid range starting at 524288 (well outside the host's
     regular uid space). Idempotent: skip if mios is already present.

  2. linger enable -- so systemd --user services (the Quadlets) start
     at boot without an active interactive login session. Required for
     `systemctl --user enable mios-forge.service` etc. to actually
     launch the daemon at boot rather than waiting for a TTY login.

  3. /var/home/mios skeleton seeded from /etc/skel -- FCOS / atomic-
     desktops home convention; the deployed MiOS image uses
     /var/home/<user> as $HOME so /etc 3-way merge doesn't have to
     manage home-dir state. Establish the same on MiOS-DEV so any
     operator-side configs (.bashrc, .config/) match across substrates.

<!-- mios-src:0165f2149e56 from build-mios.ps1:3898-3919 -->

### Idempotent

Idempotent: `cp -an` (no-clobber) copies entries that are
MISSING in /var/home/mios without overwriting operator-edited
dotfiles. Previous guard (only-on-first-boot via missing
.bashrc) prevented newly-added skel entries (XDG user-dir tree,
user-dirs.dirs) from propagating to existing users on
`mios update`. Switched to per-file no-clobber so re-runs are
safe AND new skel content reaches existing users.
`cp -a` instead of rsync -- podman-machine-os 6.0 base does
NOT ship rsync, so the prior rsync call silently no-op'd.
Operator-flagged.

<!-- mios-src:f11f494c32a0 from build-mios.ps1:3938-3947 -->

### Expose flatpak .desktop entries to WSLg's auto-publisher....

Expose flatpak .desktop entries to WSLg's auto-publisher. WSLg scans
/usr/share/applications/ + ~/.local/share/applications/ on each
distro start and creates Windows Start Menu shortcuts under
%APPDATA%\Microsoft\Windows\Start Menu\Programs\<distro>\<App>
(on <distro>).lnk -- WITH the app's real icon and no terminal popup.
Flatpak installs its entries to /var/lib/flatpak/exports/share/
applications/ which WSLg does NOT scan, so symlink each into the
WSLg-watched dir. Operator-flagged flatpak apps weren't
in Start Menu STILL after the custom Linux Apps shortcuts were
fixed -- WSLg's quality (icons + no terminal) is the canonical
user expectation, our custom .lnks are a fallback only.

<!-- mios-src:e9fa06fa2287 from build-mios.ps1:3954-3964 -->

### ALWAYS-ON LIGHTWEIGHT SET

ALWAYS-ON LIGHTWEIGHT SET: Cockpit (web console at :9090), the
Podman-Desktop discovery shim that surfaces MiOS containers in PD's
UI, and the self-hosted Forgejo forge (small SQLite-backed git host).
Plus NVIDIA CDI plumbing (mios-cdi-detect + nvidia-cdi-refresh) so
Podman containers on MiOS-DEV can claim /dev/dxg (WSL2 GPU surface)
via the same Container Device Interface spec a deployed bare-metal
MiOS host uses. mios-cdi-detect.service auto-no-ops when no GPU is
present (no /dev/nvidia0 / no /dev/dxg) and explicitly passes
--mode=wsl to `nvidia-ctk cdi generate` when systemd-detect-virt
reports wsl, so it works correctly on the dev VM out of the box.
Each enable is best-effort -- a unit that ConditionVirtualization-skips
itself just no-ops with status=inactive (success).
Quadlet-generated *.service files (from etc/containers/systemd/*.container)
live at /run/systemd/generator/ and are AUTO-WANTED via the [Install]
section Quadlet's generator already processed at daemon-reload time.
`systemctl enable` on them errors with "transient or generated" -- use
`start` instead. Native systemd units (cockpit.socket, mios-cdi-detect,
nvidia-cdi-refresh.path) take the standard `enable --now` path.

mios-ai-firstboot.service is DELIBERATELY EXCLUDED from this set: it is a
long-running oneshot (builds the AI venv + pulls GGUFs) that MUST run on the
FIRST CLEAN BOOT, not during the overlay. `enable --now` on it synchronously
starts + WAITS for that firstboot, blocking the whole install indefinitely on
the transitional system bus ("Transport endpoint is not connected"). It is
already enabled the D-Bus-independent way via the .wants symlink above (see
"Statically enable mios-ai-firstboot"). Do NOT re-add it here.

<!-- mios-src:0abf39da9927 from build-mios.ps1:3977-4002 -->

### "now to finally fix none of the containers existing or...

"now to finally fix none of the containers
existing or properly launching on boot.. in podman-MiOS-DEV".
Plus: "bake into mios.toml so operators can edit the list --
EVERYTHING is sourced from the mios.toml file and edited in the
mios.html in live environments browser".

Quadlet-generated services have [Install] WantedBy=multi-user.target
in their .container files, so they SHOULD auto-start at boot. On the
WSL podman-machine substrate the dependency chain doesn't reliably
fire for every service -- explicit `systemctl start --no-block` is
the fix. --no-block returns immediately so overlay doesn't wait on
multi-GB image pulls; each Quadlet's Restart=on-failure handles the
retry.

Both lists are TOML-sourced: mios-bootstrap/mios.toml
[containers.quadlets].autostart + .optin. Operators edit via
mios.html in the browser; build-mios.ps1's PowerShell side reads
these on every overlay pass and substitutes them here. The
PowerShell-side substitution replaces __MIOS_QUADLET_AUTOSTART__
and __MIOS_QUADLET_OPTIN__ with literal bash-array entries.

<!-- mios-src:1cdd5f5b4116 from build-mios.ps1:4005-4024 -->

### Install the operator-facing terminal flatpak so MiOS-DEV...

Install the operator-facing terminal flatpak so MiOS-DEV mirrors a
deployed MiOS host's UX: open Ptyxis on the Windows desktop via WSLg
-> default tab spawns into the host shell via flatpak-spawn --host
-> the operator types `mios "..."` and hits the local AI plane on
the `agent_pipe` port directly. Idempotent (--or-update). Also pulls the few other
substrate-class flatpaks (Nautilus, Bazaar, Flatseal) so the
emulated MiOS environment carries its file manager and app store.
Run the same canonical automation scripts the build pipeline uses,
now that `/` IS mios.git's working tree. One install path, no
parallel fetch logic to drift. Each script is best-effort
(rc != 0 doesn't kill the overlay) and self-skips when the relevant
binary already exists.

56-fonts.sh         Geist (Vercel) + Symbols-Only Nerd Font
62-oh-my-posh.sh    Oh-My-Posh static binary -> /usr/bin/oh-my-posh

<!-- mios-src:6b6cb0367c1a from build-mios.ps1:4066-4080 -->

### Flatpak here runs as ROOT (uid 0), but WSLg exports...

Flatpak here runs as ROOT (uid 0), but WSLg exports XDG_RUNTIME_DIR=/mnt/wslg/runtime-dir owned
by uid 1000 -> dbus refuses ("runtime dir owned by uid 1000, not our uid 0") and spams that on
EVERY system-wide install/remote op. Give root its OWN runtime dir + drop the inherited session
bus so all `sudo flatpak --system` calls below are quiet + correct. sudo propagates XDG_RUNTIME_DIR
(that is how the uid-1000 path leaked in), so exporting the root path here reaches the child.

<!-- mios-src:6a1e72573893 from build-mios.ps1:4094-4098 -->

### Two flatpak remotes

Two flatpak remotes:
  flathub -- community / third-party flatpaks (Flatseal, VSCodium, etc.)
  fedora  -- Fedora's own flatpak registry, ships CURRENT GNOME apps
             built against the current libadwaita runtime. Critical for
             Nautilus + Epiphany because Flathub's versions are EOL
             (pinned to GNOME 3.28 runtime, years out of date) which
             gives operators the "old GTK / CSS / decorations" look.

<!-- mios-src:0bbfd5c58fa4 from build-mios.ps1:4103-4109 -->

### Substrate-class Flatpaks

Substrate-class Flatpaks: terminal (Ptyxis), file manager (Nautilus
from fedora), Flatpak permissions UI (Flatseal), default browser
(Epiphany from fedora), GNOME shell extensions, VSCodium. Each
routes through WSLg as a Windows desktop window; the
gnome-flatpak-runtime RPM section provides the host-side
portals/audio/theming these need to render correctly.

Entries with a "fedora:" prefix install from the fedora remote
(current libadwaita / GNOME 50.x); plain entries install from
flathub. Operator directive "just enable newer fedora
repos for the flatpaks" / "you hard coded an old version of gnome
files flatpak -- THAT'S why it's old looking!!"

<!-- mios-src:d763317befde from build-mios.ps1:4134-4145 -->

### Drop a /usr/local/bin/<short> wrapper so operators can run...

Drop a /usr/local/bin/<short> wrapper so operators can run
`nautilus`, `epiphany`, `ptyxis` directly instead of the
`flatpak run org.gnome.<App>` long form. /var/lib/flatpak/exports/
bin already publishes the AppID-named symlink; this adds the
short alias on top.

The wrapper delegates to /usr/libexec/mios/flatpak-launch, which
restores the WSLg / Wayland / X11 / PulseAudio / D-Bus environment
whenever the parent shell stripped it (`su -`, `nsenter -m`, sudo
without -E, systemd-run, cron). Login shells under WSL pick those
vars up via /etc/profile.d/mios-wslg.sh, but a `bash -c 'nautilus'`
from a non-login context bypasses profile.d entirely -- which was
the failure mode the operator hit when `epiphany` errored with
"Cannot autolaunch D-Bus without X11 \$DISPLAY" after `su - mios`
under nsenter. The helper is idempotent: it only sets variables
that are unset, so a bare-metal GNOME session that already has a
working environment passes straight through.

If /usr/libexec/mios/flatpak-launch is absent (older deployment
before this fix landed), fall back to the original direct-exec
form so the wrapper still launches the flatpak -- it just won't
benefit from the env restore.
Look up short alias by the ORIGINAL key (with potential remote: prefix).

<!-- mios-src:7e6bc0d1e79a from build-mios.ps1:4191-4213 -->

### Regenerate the shim if it's missing OR if it doesn't...

Regenerate the shim if it's missing OR if it doesn't reference
the flatpak-launch helper -- a previous bootstrap run before the
WSLg-env-restore fix landed produced shims that just `exec flatpak
run`, and those leave the operator with silent-window-failures
whenever they invoke the shim from a non-login shell. The grep
below makes the regeneration idempotent: re-runs are no-ops once
the shim already points at the helper.

<!-- mios-src:8b0b57c14952 from build-mios.ps1:4215-4221 -->

### Passwordless sudo for the dev VM's regular user account...

Passwordless sudo for the dev VM's regular user account (uid 1000)
so `sudo -u mios -i` and similar account-switch commands work without
the mios user having a password set. /etc/sudoers.d/00-mios-dev is
installed mode 0440 (the only mode sudoers.d will load) and has
both the dev `user` account and the canonical `mios` account in the
wheel-equivalent set.

<!-- mios-src:7ad06727356d from build-mios.ps1:4239-4244 -->

### Default dev passwords for both `user` (uid 1000) and `mios`...

Default dev passwords for both `user` (uid 1000) and `mios` (uid >=1000
system user from sysusers.d) so Cockpit's PAM auth at https://localhost:
9090/ works without manual passwd setup. The MiOS dashboard prints these
credentials inline next to the Cockpit endpoint so the operator doesn't
have to remember them. Single-tenant dev VM trust model -- documented
on the dashboard, never used outside the dev surface.

Placeholder __MIOS_LOGIN_PASSWORD__ is substituted at heredoc-bake
time by Invoke-MiosQuadletOverlay from mios.toml [auth].password
(SSOT, operator-editable via mios.html). Vendor default is 'mios'.
DO NOT inline 'mios' here -- the substitution pass is what makes
the toml the single source of truth.

<!-- mios-src:e7fa6145e69e from build-mios.ps1:4262-4273 -->

### Verify

Verify: drive `su - mios -c id` through a pty so we can actually
type the password. If this succeeds, Cockpit's PAM stack (which
uses the same /etc/shadow lookup) will accept the same credential.
Operator-flagged dashboard said `mios / mios` but the
Cockpit login rejected those credentials because an earlier chpasswd
silently set the hash to something else (likely a CRLF leak from a
prior PowerShell heredoc, since fixed). The verify step catches a
silent failure here instead of letting the operator hit it at login.

<!-- mios-src:3dd07e48fff9 from build-mios.ps1:4282-4289 -->

### ── Layer the FULL mios.toml [packages].sections set into...

── Layer the FULL mios.toml [packages].sections set into MiOS-DEV ───────
Per feedback_mios_dev_equals_mios.md and the directive
"MIOS MUST CONTAIN EVERYTHING NEEDED TO SELF; dev, build, run, host,
hosting, etc-etc TOML/HTML SHOULD BOTH REFLECT EACHOTHER AND DICTATE
ANY AND ALL MIOS DEPLOYMENTS AND ENTRIES INCLUDING DEPLOYING MIOS DEV":
the same package set that lands in a deployed MiOS host must land in
MiOS-DEV at Phase 3 time, NOT deferred to mios-build-driver. Operator
expects `just`, `btop`, `fastfetch`, `ripgrep`, etc. to be available
the moment they enter the dev distro.

Approach: parse /usr/share/mios/mios.toml [packages].sections (master
inclusion list, configurator-controlled), filter by per-section
.enable, dedupe pkgs, layer them via `rpm-ostree install` (machine-os
is FCOS-based + ostree-managed; rpm-ostree is the canonical layered-
package mechanism). --idempotent skips already-installed, --allow-
inactive doesn't fail when a layered package's services can't start
yet (e.g. needs reboot or kernel module not in WSL kernel).

Best-effort: a non-zero rpm-ostree exit doesn't abort the seed. The
dashboard MOTD's `untracked 28` cosmetic note is unrelated and
unaffected.

<!-- mios-src:870d27a5e6ed from build-mios.ps1:4322-4342 -->

### Pure-awk TOML parser. machine-os 6.0's stripped FCOS base...

Pure-awk TOML parser. machine-os 6.0's stripped FCOS base often
ships without python3, so the previous tomllib-based approach
silently skipped (visible in the 19:24 log as "WARN: rpm-ostree
or python3 not available"). Awk is in coreutils-equivalents on
every Linux base.

Two-stage parse:
  1. Read [packages].sections array -> the master inclusion list.
  2. For each section name, read [packages.<name>].pkgs IF
     [packages.<name>].enable != false. Append to the global
     package list.
Output: deduped space-separated package names on stdout.

<!-- mios-src:f41e9237a3e8 from build-mios.ps1:4355-4366 -->

### Re-resolve the systemd PID NOW. The dnf transaction we just...

Re-resolve the systemd PID NOW. The dnf transaction we just ran
upgraded the `systemd` RPM (a transitive dep of dozens of the 297
packages in mios.toml). On WSL2's nested-systemd-in-WSL the new
binary respawns inside the user namespace and PID 1's PID number
changes -- so the $NS we captured at overlay start (line ~3658)
points at a /proc/<old-pid> entry that no longer exists. Every
subsequent `nsenter -t <old-pid> -a` then dies with:
    nsenter: stat of /proc/<old-pid>/ns/user failed: No such file
tripping the reap-on-failure trap and wiping the install.
Operator-flagged.

<!-- mios-src:c14f972423fe from build-mios.ps1:4479-4488 -->

### ── Dev-VM host networking drop-ins...

── Dev-VM host networking drop-ins ──────────────────────────────────
Operator-flagged localhost:3000 / the `searxng` port from Windows
(and from inside the dev VM) timed out even though the containers
were `Up` per `podman ps` and bound 0.0.0.0:NNNN per `ss -tlnp`.
Root cause: netavark was installed at /usr/libexec/podman/netavark
but failed to install its per-container DNAT chain in the nat table
(probably due to firewall_driver=iptables vs iptables-nft +
nftables-only ruleset on the podman-machine-os base). conmon's host
proxy listener accepted TCP but had no DNAT rule to forward to the
container netns -> HTTP request hangs.

Workaround that actually works on the dev VM: Network=host. The
container shares the VM's main netns, listens directly on
0.0.0.0:NNNN, and wslrelay (Windows-side) picks up the listener via
/proc/net/tcp scanning + forwards Windows localhost:NNNN -> VM port.
This is the standard practice for single-tenant dev VMs.

The deployed MiOS image (real Fedora bootc) doesn't have this
problem -- netavark is wired through systemd-networkd and the
firewall driver matches the host firewall backend. So the drop-ins
below ONLY land on the dev VM (their parent units are guarded by
the existing overlay flow, which only runs in podman-MiOS-DEV).

Per-container env overrides for host-network mode. In host netns,
every container shares the VM's main netns -- so bind ports collide
AND inter-container DNS (e.g. mios-hermes resolution) no longer
works (no aardvark; bridge networks aren't used). Override each
image's bind/upstream env vars to talk over localhost on the
canonical MiOS port from mios.toml [ports].*. Discovered live
while shaking out the operator's first install.

  ollama: HOME=/var/lib/ollama -- without this ollama tries to
      mkdir /.ollama in the read-only container root and dies with
      "permission denied". The Quadlet already mounts /var/lib/ollama
      (writable for UID 815), so point HOME at it.
  webui:  [redacted] (env.py:611 requires non-empty
      when WEBUI_AUTH=true), PORT=${MIOS_PORTS_OPEN_WEBUI}, OPENAI_API_BASE_URL=
      http://localhost:${MIOS_PORTS_HERMES}/v1 (mios-hermes:${MIOS_PORTS_HERMES} doesn't resolve in
      host netns; use localhost instead).
  hermes: PORT=${MIOS_PORTS_HERMES} (otherwise picks an upstream default).
  searxng: BIND_ADDRESS=0.0.0.0:${MIOS_PORTS_SEARXNG} (granian default is :8080 which
      collides with mios-ai).
Hermes-Agent on the dev VM uses host networking, so the
container-name DNS that the vendor /etc/mios/hermes/config.yaml
relies on (mios-ollama, mios-ai, mios-searxng) does NOT resolve.
Drop a config.local.yaml that overrides each base_url to talk over
the VM's loopback instead. The vendor config has a trailing
`include: /etc/hermes/config.local.yaml` so this auto-merges on
top without touching the upstream file.

<!-- mios-src:f5ec6db058c5 from build-mios.ps1:4498-4546 -->

### MiOS-DEV is a WSL2 podman machine -- bridge networking +...

MiOS-DEV is a WSL2 podman machine -- bridge networking + PublishPort
on this substrate binds container-loopback (127.0.0.1) on the WSL VM
side, which the Windows-side netsh portproxy (0.0.0.0 -> WSL-VM-eth0-IP)
can't reach. Network=host makes each container bind the WSL VM's real
eth0 + loopback directly, so wslrelay relays loopback->Windows-host
localhost AND the portproxy relays eth0->LAN.

Architecture /14 (operator-directed):
  * hermes-agent: DIRECT host install (automation/38 + hermes-agent.
    service) -- NOT a container, so it gets NO dropin here.
  * mios-hermes + mios-hermes-dashboard: container Quadlets SHELVED
    ([quadlets.enable]=false) -- dropped from this list.
  * mios-hermes-workspace: REMOVED entirely -- dropped.
  * mios-open-webui: the chat UI. Its container listens on 8080
    internally (parent Quadlet remapped host:${MIOS_PORTS_OPEN_WEBUI}->container:8080 via
    PublishPort). Under host-net PublishPort is a no-op, so it MUST
    get PORT=${MIOS_PORTS_OPEN_WEBUI} or it binds 8080 and collides with mios-code-server
("[Errno 98] address already in use" -- operator-confirmed).
  * Bind addresses: 0.0.0.0 everywhere (NOT 127.0.0.1). The old
    "127.0.0.1 forces AF_INET for localhostForwarding" theory is
    superseded -- the portproxy->WSL-VM-IP path needs eth0 binds.

<!-- mios-src:f87a5689aa4a from build-mios.ps1:4587-4607 -->

### Open the MiOS service ports in the dev VM's firewalld. The...

Open the MiOS service ports in the dev VM's firewalld. The deployed
bootc image runs automation/44-firewall-ports.sh at OCI build time
(firewall-offline-cmd), but the MiOS-DEV overlay path does NOT go
through an image build -- it's provisioned from podman-machine-os
(firewalld active, public zone: only ssh/mdns/dhcpv6) and overlaid.
Without this, every MiOS port is dropped on eth0 -- services bind but
are unreachable from the WSL-VM-IP, so the Windows-side portproxy
(0.0.0.0 -> WSL-VM-IP) hits a closed door (operator-confirmed
LAN access dead until firewalld was opened by hand).
firewall-cmd (online) here mirrors what 44-firewall-ports.sh bakes
offline. Tolerant: no-op if firewalld isn't running.

<!-- mios-src:3e71b77696d3 from build-mios.ps1:4634-4644 -->

### Use $NS (nsenter into systemd's namespace) instead of bare...

Use $NS (nsenter into systemd's namespace) instead of bare `sudo` so
the reload reaches the running PID 1's bus. Bare `sudo systemctl
daemon-reload` runs in the OUTER WSL ns and gets "Transport endpoint
is not connected" -- same root cause as the early-overlay daemon-
reload that already routes through $NS. Operator-flagged
the bare-sudo call here tripped the reap-on-failure trap and wiped
their install after a 9-minute Phase-3 build.

<!-- mios-src:5a4b838ae423 from build-mios.ps1:4656-4662 -->

### Apply the MiOS systemd-preset so cockpit.socket / pmcd /...

Apply the MiOS systemd-preset so cockpit.socket / pmcd / pmlogger /
pmproxy and other MiOS-preset-enabled units land at enabled=enabled
on the dev VM. The deployed bootc image processes presets at image-
build time; the dev-VM overlay path does NOT, so without this every
preset-`enable`d unit stays at upstream Fedora's `disabled` default.
Operator-flagged cockpit metrics page showed "pmlogger.
service is not running" because PCP units were stuck disabled. The
preset is the SSOT for "what should be on by default"; applying it
here keeps the dev VM behavior identical to the deployed image.

<!-- mios-src:333b0c42578a from build-mios.ps1:4665-4673 -->

### Mask dev-VM-hostile services. These are baked into mios.git...

Mask dev-VM-hostile services. These are baked into mios.git for the
bare-metal bootc image but cannot work in podman-machine-os WSL:
  * audit-rules / auditd       -- WSL2 kernel has no audit subsystem
  * fapolicyd                  -- needs kernel fanotify FAN_REPORT_FID
  * usbguard                   -- no USB devices in WSL
  * bootloader-update          -- no bootloader on WSL distros
  * greenboot-healthcheck      -- bootc-specific rollback machinery
  * mios-aichat-build          -- builds a Distrobox image that doesn't
                                  apply on dev VM (used on bare metal)
  * mios-wslg-permissions-fix  -- chmod /mnt/wslg fires before WSLg is
                                  mounted on this machine-os build;
                                  harmless to mask, Quadlets handle
                                  /tmp/.X11-unix via /etc/profile.d.
  * mios-wsl-init              -- the legacy first-boot init shim;
                                  superseded by mios-cdi-detect +
                                  mios-wsl-runtime-dir on the dev VM.
Each shows up as "Failed to start" in cockpit's Services panel
otherwise, which is operator-visible noise that suggests the
install is broken. Masking is idempotent and reversible
(systemctl unmask <unit>). Operator-flagged.

<!-- mios-src:c8ef50375cdc from build-mios.ps1:4686-4705 -->

### Quadlet autostart / opt-in lists -- SSOT

Quadlet autostart / opt-in lists -- SSOT: mios.toml
[containers.quadlets]. Operator-editable via mios.html. The
bash heredoc has __MIOS_QUADLET_AUTOSTART__ /
__MIOS_QUADLET_OPTIN__ placeholders; resolve them here against
the layered TOML cascade and substitute as literal bash array
entries. Vendor default is the workstation-core set (cockpit-
link + forge + searxng + webui + ai + ollama). Operator opt-in
services land in the .optin list (per mios.toml).
Operator directive 'forget open webui for now -- Ollama
>> hermes agent >> hermes-workspace app is the front-end'. Swap
mios-webui out, swap mios-hermes + mios-hermes-workspace in.

<!-- mios-src:ef7e9bf82fa3 from build-mios.ps1:4724-4734 -->

### MIOS_FIREWALL_PORTS__ -- dev-VM firewalld open-port list...

__MIOS_FIREWALL_PORTS__ -- dev-VM firewalld open-port list for the
quadlet overlay. Service ports flow from the [ports] SSOT (operator
override-aware); the infra ports (ssh, forgejo-ssh, qdrant grpc/http,
hermes-dashboard, metrics) are not operator-tunable [ports] service
keys so they carry vendor defaults here. Mirrors the offline
44-firewall-ports.sh surface baked into the OCI image.

<!-- mios-src:6533e0c3f730 from build-mios.ps1:4751-4756 -->

### MIOS_LOGIN_PASSWORD__ -- the operator-facing dev-VM login...

__MIOS_LOGIN_PASSWORD__ -- the operator-facing dev-VM login (also
the credential Cockpit web at https://localhost:9090/ accepts).
SSOT: mios.toml [auth].password (plain) or [auth].password_hash
(pre-hashed for hardened deploys). Default 'mios' if both blank.
The dashboard banner shows the literal string, so resolving it
from the same place the chpasswd line consumes guarantees the
advertised credential is the actual credential.

<!-- mios-src:b868732ed783 from build-mios.ps1:4799-4805 -->

### Stage the seed to a file on M:\ instead of base64-inlining...

Stage the seed to a file on M:\ instead of base64-inlining it
through `bash -c`. f67e5ad (rpm-ostree install + python3 toml
parse) pushed the seed past Windows' CreateProcess arg-length
cap (~32K), and `wsl.exe -d <distro> --exec bash -c $stage`
died with "FATAL: Program 'wsl.exe' failed to run: The
filename or extension is too long" before the seed could even
touch the distro. Writing to a file + invoking by path keeps
the command line tiny.

<!-- mios-src:268270b07b30 from build-mios.ps1:4818-4825 -->

### DEPRECATED

============================================================================
DEPRECATED: Invoke-WindowsPodmanBuild
----------------------------------------------------------------------------
This function (and its sibling helpers Invoke-WslBuild,
Invoke-DeployPipeline, New-MiosHyperVVm below) belongs to the
pre-self-replication architecture where Windows ran `podman build`
directly. As of v0.2.4 (memory: feedback_mios_dev_is_the_builder)
the dev VM IS the builder; Windows is provisioning + handoff ONLY.
All Phase 9 Build paths run inside MiOS-DEV via mios-build-driver,
triggered by the `mios build` verb (M:\MiOS\bin\mios-build.ps1).

These functions are now UNREACHABLE: -BuildOnly / -FullBuild are
force-deprecated at line 202 ($BootstrapOnly = $true), and every
control-flow gate (`if ($BootstrapOnly)` returns; `if (-not
$BootstrapOnly)` blocks) routes around them.

Kept in-tree for one release cycle so git-blame still resolves the
legacy callers; a follow-up commit will delete them outright.
============================================================================

<!-- mios-src:ee84530bd427 from build-mios.ps1:4860-4878 -->

### ── Universal MiOS-SEED merge...

── Universal MiOS-SEED merge ────────────────────────────────────────────
The Phase 2 overlay (lines ~4823+) already robocopies mios-bootstrap.git
onto $MiosRepoDir, so by the time we reach podman build the bootstrap
files (etc/skel/.config/mios/, etc/mios/profile.toml, mios.toml at root,
agent entry-point .md files) are already present in the build context.
seed-merge.ps1 is kept as a defensive idempotent re-run -- if the
operator added new files to mios-bootstrap.git between Phase 2 and
this phase, they get pulled in.

<!-- mios-src:7007542f1df7 from build-mios.ps1:4887-4894 -->

### Run via cmd.exe so 2>&1 merges stderr (podman build...

Run via cmd.exe so 2>&1 merges stderr (podman build progress) into stdout stream.
Build args propagate operator selections from the Phase-6 prompts
(or layered mios.toml [ai] defaults) into the Containerfile ARGs of
the same name.

<!-- mios-src:140dd00b2b85 from build-mios.ps1:4913-4916 -->

### ── Universal MiOS-SEED merge (inside WSL distro)...

── Universal MiOS-SEED merge (inside WSL distro) ─────────────────────────
Sync-RepoToDistro brought mios.git into / via `git fetch + reset --hard`.
That path strips untracked files, so we can't pre-merge on the Windows
side -- the merge has to happen INSIDE WSL after the sync, before
`just build` invokes podman build. Clone mios-bootstrap into
/tmp/mios-bootstrap, run seed-merge.sh against /, then build.

<!-- mios-src:fc404840ef6a from build-mios.ps1:4992-4997 -->

### Note

Note: NO `set -e` here -- a transient clone failure must DEGRADE
(warn + skip the overlay) rather than abort the whole build. The
clone is wrapped in a 3x exponential-backoff retry loop so a flaky
network doesn't kill an otherwise-good build on the first failure.

<!-- mios-src:cd5ce43ebb64 from build-mios.ps1:5003-5006 -->

### Stream build output line-by-line

Stream build output line-by-line: update dashboard Step, write to log.

Quoting note: the bash script body is wrapped in OUTER double
quotes (CreateProcess-recognized) so the script body stays a
single argv element through the wsl.exe / podman.exe handoff.
The inner single quotes around $BaseImage / $AiModel are then
bash-literal quoting -- preserved verbatim because CreateProcess
treats them as ordinary characters inside the "..." block.

Earlier the script wrapped the whole thing in single quotes
(`'A=''val'' B=''val'' just build'`) which CreateProcess does
NOT recognize as quoting, so it split on the spaces between the
env-var pairs and bash got an unbalanced fragment, failing with:
  MIOS_AI_MODEL='':'-c: line 1: unexpected EOF...

<!-- mios-src:c5c1d52767f6 from build-mios.ps1:5035-5048 -->

### Pre-create the output directory on the BUILDER MACHINE...

Pre-create the output directory on the BUILDER MACHINE filesystem.
podman volume bind-mounts require the host-side path to exist before
the container starts; otherwise crun fails with `statfs ENOENT`.
CRITICAL: must run on the dev distro itself -- running `mkdir`
inside a transient alpine container only creates the dir in the
container's ephemeral fs, which evaporates before BIB starts.
Routed through Invoke-DistroSh so it works in both rename states.

<!-- mios-src:f30a5d59e7b2 from build-mios.ps1:5150-5156 -->

### Smoke-test the freshly-provisioned MiOS-DEV podman machine...

Smoke-test the freshly-provisioned MiOS-DEV podman machine before
we commit to renaming it. Verifies:
  1. wsl.exe can reach the distro (basic VM bootstrap done)
  2. systemd is running inside (services can be enabled)
  3. /usr tree has the MiOS overlay (33-mios-overlay sentinel present)
  4. podman API socket is reachable from the Windows host

Returns $true on full success, $false otherwise (caller decides
whether to abort the rename or warn-and-continue). Errors bubble
up as warnings -- does NOT throw, so a partial-overlay state
doesn't kill the bootstrap.

<!-- mios-src:1e0fb5dbd400 from build-mios.ps1:5332-5342 -->

### 1. Basic responsiveness. Retried with backoff: Phase 3's...

1. Basic responsiveness. Retried with backoff: Phase 3's wsl --shutdown
restarts the distro right before this smoke check, so the FIRST echo-ready
probe races the VM cold-start (operator-flagged smoke warned
"did not respond to echo ready" on a freshly-shutdown distro). Match the
systemd/podman probes' retry pattern. SSOT: [smoke_tests].

<!-- mios-src:5d7063a30842 from build-mios.ps1:5357-5361 -->

### 4. Podman API reachable. Skipped post-rename (podman client...

4. Podman API reachable. Skipped post-rename (podman client
speaks to the SSH socket regardless of WSL distro name).
Retried with backoff: Phase 3's wsl --terminate (added in
4a8e7f6 to make /etc/wsl.conf [user] default=mios take effect)
restarts the distro right before this smoke check runs, so
the podman API is warming up. Without retry the check fires
before the API socket is ready and emits a confusing warning.

<!-- mios-src:0763e73568c4 from build-mios.ps1:5408-5414 -->

### Same reason as systemd retry above

Same reason as systemd retry above: podman machine takes 15-30s
to warm up after wsl --terminate. Operator's 16:01 install
showed 5x2s=10s wasn't enough.
SSOT: attempts + interval resolve through mios.toml [smoke_tests].

<!-- mios-src:4967a15c2469 from build-mios.ps1:5418-5421 -->

### Run a bash snippet inside the dev distro, picking the right...

Run a bash snippet inside the dev distro, picking the right
transport based on the rename state:

  * Pre-rename (distro = "podman-MiOS-DEV"): use `podman machine
    ssh` -- works because podman's WSLDistroName() = podman-<name>.
  * Post-rename (distro = "MiOS-DEV"):       use `wsl -d MiOS-DEV`
    directly -- `podman machine ssh` here fails because podman
    hardcodes the `podman-` prefix in WSLDistroName().

Both transports base64-encode the script to avoid CRLF mangling
by stdin pipelines, then `echo BASE64 | base64 -d | bash`
decodes and pipes the script to a fresh bash via stdin (bash
auto-execs when stdin is a pipe).

Returns: the inner script's stdout. After invocation,
$LASTEXITCODE holds the inner bash exit code (set by the
native wsl.exe / podman.exe process, which propagates the
last pipeline stage).

Callers MUST NOT do `return Invoke-DistroSh ...` if they want
both stdout and exit code -- assign to a variable and check
$LASTEXITCODE separately:

    $out = Invoke-DistroSh -Bash "echo hello"
    if ($LASTEXITCODE -ne 0) { ... }

All build-pipeline call sites that previously called
`podman machine ssh $BuilderDistro -- sudo bash -c "..."`
should route through this helper so the rename is transparent.

<!-- mios-src:15c295c3ec11 from build-mios.ps1:5442-5470 -->

### Write / merge $env:USERPROFILE\.wslconfig with the keys...

Write / merge $env:USERPROFILE\.wslconfig with the keys MiOS-DEV
needs from the WSL2 utility VM:
  * networkingMode=mirrored  -- containers' 0.0.0.0:NNNN binds
    show up on Windows' loopback (and physical NICs once the
    LAN firewall rules let them through).
  * firewall=false           -- bypass Hyper-V Firewall (we don't
    ship per-port New-NetFirewallHyperVRule rules).
  * dnsTunneling=true        -- VM DNS matches Windows-native.
  * autoProxy=true           -- inherit Windows proxy settings.
  * guiApplications=true     -- WSLg compositor for flatpaks.
  * memory/processors/swap   -- right-sized for the detected host.

CRITICAL: must run BEFORE Phase 3 initializes the dev VM. WSL2
reads .wslconfig at WSL2-utility-VM-START, so if we write it
AFTER podman-machine-init has spawned the VM, the VM keeps its
boot-time settings (legacy NAT mode) until the next `wsl --
shutdown`. Symptom the operator hit cockpit + every
other port timed out from Windows because the dev VM came up
in NAT mode while .wslconfig (set in Phase 4) said mirrored.
Idempotent: re-invoking from Phase 4 sees the same key set and
writes nothing new.

<!-- mios-src:4bfc7057e0ac from build-mios.ps1:5505-5525 -->

### Networking

Networking: NAT + localhostForwarding (NOT mirrored). MS labels
mirrored as "beta" and operator confirmed on Windows
build 28020 (Canary): mirrored sets up the VM IP correctly
(vm-side `ip addr` shows Windows' Wi-Fi + Tailscale IPs), but
the documented localhost-forwarding silently breaks -- every
container port times out from Windows. NAT mode + the legacy
localhostForwarding=true bridge is what reliably forwards
0.0.0.0:NNNN binds inside the VM to Windows' loopback. LAN-side
access from phone/other devices is then handled by the Windows
Firewall rules + netsh portproxy (added by Set-MiosLanFirewall
Rules + Set-MiosLanPortProxy in Phase 4).

<!-- mios-src:269a0aa11146 from build-mios.ps1:5529-5539 -->

### `firewall` is mirrored-mode-specific and useless in NAT...

`firewall` is mirrored-mode-specific and useless in NAT mode;
strip it on every merge so .wslconfig stays small. (Switch back
to ('localhostForwarding',) the day mirrored mode is the default
again -- right now NAT + localhostForwarding is the reliable
combo per operator's testing on Win 11 build 28020.)

<!-- mios-src:c6e801626313 from build-mios.ps1:5561-5565 -->

### Windows Firewall inbound rules so OTHER devices on the...

Windows Firewall inbound rules so OTHER devices on the operator's
LAN (phone, tablet, laptop) can reach the dev VM's container ports
at <Windows-host-IP>:NNNN -- not just from the same Windows box.

Why this is needed even with WSL2 mirrored networking + .wslconfig
firewall=false:
  * Mirrored mode shares Windows' IP stack with the WSL VM, so a
    container bound to 0.0.0.0:NNNN inside the dev VM appears as
    Windows-side 0.0.0.0:NNNN automatically -- LISTEN visible in
    `netstat -ano | findstr :9090`.
  * .wslconfig firewall=false bypasses Hyper-V Firewall enforcement
    for the VM's vSwitch, so Windows-side localhost reaches the
    port without an extra Hyper-V allow rule.
  * BUT incoming connections from a LAN device still traverse the
    standard Windows Defender Firewall on the host's physical NIC.
    Defender default-denies inbound TCP for unknown listeners --
    so without an explicit per-port allow rule, the phone's
    browser hangs on connect even though Windows itself reaches
    localhost fine.

SSOT: mios.toml [ports].* (port numbers) + [ports.lan_firewall].*
(profiles + expose list). Vendor defaults below; operator edits
mios.html to flip exposure on / off per service or narrow
profiles.

Operator-flagged "windows installation should also
open the containers ports / forward them on windows side so that
we can access open webui, searxng, hermes, etc -- from my phone
or another device(s) on the local network."

<!-- mios-src:622661edbc55 from build-mios.ps1:5600-5628 -->

### Windows-side `netsh interface portproxy` mappings so OTHER...

Windows-side `netsh interface portproxy` mappings so OTHER devices
on the LAN can reach the dev VM's container ports.

Why this is needed alongside Set-MiosLanFirewallRules:
In NAT networking mode (.wslconfig networkingMode=NAT, which MiOS
uses because mirrored mode silently breaks loopback forwarding on
the operator's Win11 build 28020), services bound to 0.0.0.0
inside the dev VM are reachable ONLY at Windows-side 127.0.0.1
(via the localhostForwarding=true bridge). The host's external
NIC (Wi-Fi / Ethernet) has nothing listening on those ports, so
connections from a phone on the same Wi-Fi hang on connect.
netsh portproxy makes Windows listen on 0.0.0.0:<port> and
forward to 127.0.0.1:<port>, which then bounces into the dev VM
via WSL's loopback bridge. Net effect: phone -> Win NIC ->
portproxy -> WSL distro container.

Operator-flagged "none of my services are available
on my local wifi network".

SSOT: same [ports].* + [ports.lan_firewall].expose list as the
firewall rules above, so opening / closing a service in mios.html
affects BOTH layers in lock-step. Idempotent: deletes the old
mapping before adding so re-runs converge cleanly without
accumulating duplicate listeners.

<!-- mios-src:83a136647573 from build-mios.ps1:5712-5735 -->

### CRITICAL FIX -- bind 0.0.0.0:PORT (covers Windows-host...

CRITICAL FIX -- bind 0.0.0.0:PORT (covers Windows-host
localhost AND LAN clients in one rule), connect to the WSL VM's
eth0 IP. Earlier attempts:
  v0 (broken): listen=0.0.0.0 + connect=127.0.0.1 -- hijacked
    wslhost AND landed on dead Windows-host loopback. Every
    Windows-host curl localhost:PORT timed out.
  v1 (incomplete): listen=<LAN-IP> + connect=<WSL-VM-IP> -- LAN
    clients worked, Windows-host localhost broke because nothing
    bound 0.0.0.0:PORT (and WSL2's native localhostForwarding
    turned out to silently fail under NAT mode).
  v2 (current): listen=0.0.0.0 + connect=<WSL-VM-IP>. No hijack
    because target is the WSL VM, not Windows loopback. Windows-
    host localhost:PORT and LAN client <host-lan-ip>:PORT both
    hit the portproxy and forward into the WSL VM. Operator-
confirmed 8/8 MiOS services reachable from
    Windows browser via this rule shape.
WSL VM eth0 IP resolution. wsl.exe emits UTF-16LE by default --
capturing that in PowerShell mangles it (operator-confirmed
produced "20172.21.194.158", a garbage-prefixed IP, in
the live netsh portproxy table -> every LAN connect failed).
Two-part fix:
  1. $env:WSL_UTF8=1 makes wsl.exe emit clean UTF-8.
  2. [regex] extracts ONLY a valid dotted-quad from the output --
     belt-and-suspenders against any stray byte that still slips
     through, so connectaddress is ALWAYS a clean N.N.N.N or empty.

<!-- mios-src:bbf8ef9e5353 from build-mios.ps1:5756-5780 -->

### Recovery

Recovery: if a previous run of Rename-PodmanDevDistro renamed
the WSL distro from `podman-MiOS-DEV` to `MiOS-DEV`, every
subsequent `podman machine start/init/ssh` invocation fails
with WSL_E_DISTRO_NOT_FOUND -- podman hardcodes the `podman-`
prefix in WSLDistroName() and can't see the renamed distro.

This function detects the renamed-but-broken state and reverses
the rename via export -> unregister -> import-with-prefix.
User-facing surfaces (dashboard, mios-dev launcher, icons)
already hide the prefix, so the operator still sees "MiOS-DEV"
everywhere they look.

Idempotent: bails if podman-$DevDistro already exists or if
$DevDistro isn't registered at all.
Bypass: $env:MIOS_SKIP_PODMAN_RESTORE=1.

<!-- mios-src:996966b69fcc from build-mios.ps1:5825-5839 -->

### Drops the `podman-` prefix that `podman machine init`...

Drops the `podman-` prefix that `podman machine init` auto-adds
to its WSL2 distro: renames podman-MiOS-DEV -> MiOS-DEV so the
operator-facing distro name matches the project name everywhere
(Start Menu, dashboard, `wsl -d MiOS-DEV`, mios-dev shortcut).

Procedure: export -> unregister -> import-with-new-name. Only
safe to call AFTER all `podman machine ssh` and `podman build`
operations have completed (subsequent `podman machine start/ssh`
commands will FAIL because podman hardcodes the `podman-` prefix
in WSLDistroName(); the operator's daily workflow uses `wsl -d
MiOS-DEV` or the `mios-dev` shortcut, both of which work).

The Windows-side podman client connection (a fixed SSH URI at
127.0.0.1:<port>/run/podman/podman.sock) is unaffected: the
socket lives inside the distro, the port-forward survives the
rename, and `podman cp / commit / build` continue to work as
long as the distro is started via `wsl -d MiOS-DEV`.

Idempotent: if `podman-$DevDistro` is already absent and
`$DevDistro` is registered, skip with a no-op.

Default behavior INVERTED skipping the rename is now
the default. Reason: the rename breaks Podman Desktop's machine
visibility (Podman Desktop tracks the distro by its podman- prefix
registration; the rename + M:\ relocation orphans the machine
database entry, so the dev VM appears Stopped / un-launchable in
Podman Desktop even when wsl -l -v shows it Running). Operator-
facing UX continues to read "MiOS-DEV" via the Windows Terminal
profile name, Start Menu labels, and the `mios-dev` helper.

TOML-first per AGENTS.md §3 / mios.toml is THE singular SSOT.
Resolve via [bootstrap.dev_vm].rename_distro from the layered
overlay; env var $MIOS_RENAME_DISTRO remains as a runtime override
for ad-hoc operator use (overrides TOML when set).

<!-- mios-src:bc8cffce6011 from build-mios.ps1:5878-5911 -->

### IMPORTANT

IMPORTANT: parameter was previously named $Args, which is one of
PowerShell's reserved superglobals (automatically populated with
the function's UNBOUND positional args). Inside the function body
$Args was therefore ALWAYS empty -- the test `if ($Args)` failed
and Arguments never made it onto the.lnk. Symptom
MiOS Linux Apps Start Menu shortcuts had TargetPath=wsl.exe but
Arguments="" so clicking "Files" / "Web" / etc. launched a bare
wsl.exe shell instead of `wsl -d podman-MiOS-DEV --user mios --
flatpak run <appid>`. Renamed to $ArgList so callers' --Args
passes actually land on the shortcut.

<!-- mios-src:bbfdbd2e924b from build-mios.ps1:5998-6007 -->

### Body extracted to src/install-host-tools.ps1 per operator...

Body extracted to src/install-host-tools.ps1 per operator directive
"TOLD YOU A MONOLITH INSTALL.ps1 SCRIPT WAS A BAD IDEA
AND THAT THE BOOTSTRAP SHOULD BE DOING MOST OF THE HOST_SIDE SETUP
AND INSTALLATIONS". Dot-sourced from disk at first call so the
360-line winget install logic is no longer inline in this monolith
(also reduces AMSI heuristic surface).

<!-- mios-src:047a8da3ddde from build-mios.ps1:6017-6022 -->

### Mirror MiOS's Linux branding (Geist + Symbols-Only Nerd...

Mirror MiOS's Linux branding (Geist + Symbols-Only Nerd Font +
oh-my-posh) onto the Windows host so PowerShell, Windows Terminal,
and any Windows-side terminal that opens MiOS-DEV (Ptyxis flatpak
via WSLg, or just `wsl -d podman-MiOS-DEV`) renders the same
MiOS-themed prompt with the same glyphs.

Installs:
  1. Geist + Symbols-Only Nerd Font in %LOCALAPPDATA%\Microsoft\
     Windows\Fonts (per-user, no admin needed). Registered via
     HKCU registry so all Windows apps see them.
  2. oh-my-posh.exe in %LOCALAPPDATA%\Programs\oh-my-posh\bin\
     and added to the user's PATH.
  3. PowerShell profile snippet that initializes oh-my-posh with
     the MiOS theme (mios.omp.json from the cloned mios.git repo,
     copied to %APPDATA%\MiOS\mios.omp.json so the profile can
     reach it without depending on $MiosRepoDir resolution).

Idempotent: each step probes for existing installs first.
Bypass: $env:MIOS_SKIP_WINDOWS_BRANDING=1.

<!-- mios-src:45726d79756c from build-mios.ps1:6041-6059 -->

### Re-resolve the install root

Re-resolve the install root: if the MIOS-DEV data disk is up
(M:\ by default) ALL install paths move onto it (full-partition
overlay). On a re-run that started before the data disk
existed, this is also where leftover /usr/share/mios content gets
auto-migrated onto M:\MiOS so the operator never has to clean
up split-state across drives.

<!-- mios-src:3d39c3fe114d from build-mios.ps1:6065-6070 -->

### ── 1. Fonts (TOML-first per AGENTS.md §3)...

── 1. Fonts (TOML-first per AGENTS.md §3) ───────────────────────
Sources + install scope all resolve from mios.toml [theme.font].*
so operators can pin URLs / force scope via mios.html. Geist is the
MiOS GLOBAL font ("Linux and Windows Font is
Geist font (system-wide -- terminals, apps, UI, etc-etc)") so the
default scope is "auto" => system-wide when elevated.

<!-- mios-src:2a32ca0ce9f4 from build-mios.ps1:6080-6085 -->

### SendMessageTimeout, NOT SendMessage: a synchronous...

SendMessageTimeout, NOT SendMessage: a synchronous HWND_BROADCAST of
WM_FONTCHANGE blocks the installer FOREVER if ANY top-level window is
hung/unresponsive -- the stuck-install root cause (hung after
"Symbols-Only Nerd Font installed"). SMTO_ABORTIFHUNG|SMTO_NORMAL (0x0002)
+ 1000ms/window makes the broadcast non-blocking. 0xFFFF=HWND_BROADCAST,
0x001D=WM_FONTCHANGE.

<!-- mios-src:a2e7ac4709fa from build-mios.ps1:6177-6182 -->

### Substitute powerline glyphs from mios.toml [theme.prompt]...

Substitute powerline glyphs from mios.toml [theme.prompt] (SSOT).
The on-disk omp.json ships with vendor-default rounded caps
( / ); operators who switch to sharp triangles or
flat separators via mios.html overwrite [theme.prompt].
powerline_right / .powerline_left / .leading_diamond / .trailing_diamond
which we patch into the staged copy here. Per operator: "no
hardcoding ANYWHERE -- everything from the toml/html".

<!-- mios-src:8e8bc4ddfa38 from build-mios.ps1:6228-6234 -->

### ── Color substitution from mios.toml [colors] (SSOT) ───...

── Color substitution from mios.toml [colors] (SSOT) ───
Per "oh my posh and other settings
should source from the same toml sections for all
platform for theme/branding to be truly unified in code."
The on-disk omp.json ships with vendor-default Hokusai
palette hex codes that EXACTLY match the [colors] vendor
defaults; substituting by literal hex lets operator
palette overrides via mios.html flow into every MiOS
terminal without touching this script.  Brand colors
(Python yellow, Node green, Rust orange, Go cyan) stay
hardcoded -- they're universal language identity, not
MiOS palette.

<!-- mios-src:b1bde49c8276 from build-mios.ps1:6268-6279 -->

### Inject (or refresh) a thin REDIRECTOR in the user's...

Inject (or refresh) a thin REDIRECTOR in the user's PowerShell
profile. The redirector dot-sources M:\MiOS\powershell\profile.ps1
(the SSOT). Per operator: "EVERYTHING MIOS RELATED--EVEN WINDOWS
COMPONENTS INSTALLED--ARE ALL INSTALLED ON THE CREATED M:\
Drive/Partition!!!". The previous behaviour wrote the full
oh-my-posh init body into $PROFILE.CurrentUserAllHosts (i.e.
%USERPROFILE%\Documents\PowerShell\profile.ps1, on C:\) which
duplicated logic between the redirector and the M:\ profile.
Now $PROFILE is a 4-line shim: M:\ has the actual body. Marker
comments delimit the MiOS-managed block so re-runs are
idempotent (we replace the block, not append).

<!-- mios-src:c02a75615846 from build-mios.ps1:6304-6314 -->

### Generate one multi-size .ico (16/32/48/64/256) styled to...

Generate one multi-size .ico (16/32/48/64/256) styled to match the
MiOS dashboard ASCII art: an isometric 3D cube (top + left-front +
right-front faces) with `/:\`-style hatch marks on each face,
echoing the wireframe blocks of the MIOS letters in the dashboard
banner. The cube is rendered in the MiOS palette (Hokusai bg,
cream front, accent orange top), with an optional badge in the
bottom-right corner for action-verb shortcuts.

Visual rationale: at 16-32 px the letter "M" is unrecognizable,
but the iso-cube silhouette + hatched faces stay readable and
clearly map back to the dashboard art. The badge layer
disambiguates verbs (mios-build vs mios-pull etc.).

<!-- mios-src:dec0a11909dd from build-mios.ps1:6355-6366 -->

### MiOS palette (Hokusai + operator): bg = #282262 deep...

MiOS palette (Hokusai + operator):
  bg     = #282262   deep Hokusai blue (canvas)
  fg     = #E7DFD3   warm cream (front-left face)
  accent = #F35C15   sunset orange (top face -- "lit" surface)
  shade  = #14112E   near-black blue (right face -- shadowed)
  green  = #3E7765   forest green (non-destructive verb badges)

<!-- mios-src:3a93b6293129 from build-mios.ps1:6382-6387 -->

### Builds out the Windows-side MiOS install tree and...

Builds out the Windows-side MiOS install tree and shortcuts:

  $MiosInstallDir/                 (= /usr/share/mios for admin installs,
    bin/                            %LOCALAPPDATA%\MiOS otherwise)
      oh-my-posh.exe               (already staged by Install-WindowsBranding)
      mios-dash.ps1                Windows dashboard
      mios-dev.ps1                 wsl -d <dev-distro> launcher
      mios-pull.ps1                wsl --user root sudo /usr/bin/mios-pull
      mios-update.ps1              re-runs build-mios.ps1 to refresh
    icons/                         per-verb .ico files (M + badge)
      mios.ico, mios-dev.ico, mios-pull.ico, mios-dash.ico,
      mios-build.ico, mios-update.ico, mios-config.ico
    themes/mios.omp.json           (already staged by Install-WindowsBranding)

  Start Menu\Programs\MiOS\        $StartMenuDir
    MiOS.lnk                       (main launcher; wt -p MiOS or pwsh)
    MiOS Dev VM.lnk                (wsl into MiOS-DEV)
    MiOS Update.lnk                (mios-pull)
    MiOS Dashboard.lnk             (standalone dash)
    MiOS Configurator.lnk          (HTML configurator on MiOS-DEV WSLg)

  Desktop\MiOS.lnk                 single primary shortcut
  PowerShell profile               mios-dash / mios-dev / mios-pull functions
  Windows Terminal settings.json   "MiOS" profile + color scheme

Idempotent: regenerates / replaces in place.
Bypass: $env:MIOS_SKIP_LAUNCHER=1.

<!-- mios-src:8cdac1a44da3 from build-mios.ps1:6541-6567 -->

### ── 2. Bin scripts: mios-dash + mios-dev + mios-pull +...

── 2. Bin scripts: mios-dash + mios-dev + mios-pull + mios-update ──
"the dashboards are still too big!!!... but
if I open a new tab in MiOS apps' terminal window--I get a perfectly
fitting dashboard and piping!!!".

The "too big" dashboard was THIS file's previous contents -- a
verbose Show-MiosDashboard with full ASCII logo + Self-replication
endpoint probes + dev-VM state + build-pipeline arrow. The new-tab
"perfectly fitting" dashboard is the Show-MiosDashboard inside
M:\MiOS\powershell\profile.ps1 (auto-runs on each tab open).

Unify: mios-dash.ps1 is now a thin wrapper that dot-sources the
profile body and calls the SAME Show-MiosDashboard. One canonical
dashboard rendered everywhere -- typing `mios dash` is identical
to opening a new tab. SSOT: profile body comes from Get-MiOS.ps1's
Install-MiOSPowerShellProfile (which reads mios.toml [dashboard]
rows + [terminal] dims + [theme] palette).

<!-- mios-src:a649d81ee44a from build-mios.ps1:6603-6619 -->

### <MiOSRoot>\bin\mios-dash.ps1 `mios dash` verb -- delegates...

<MiOSRoot>\bin\mios-dash.ps1
`mios dash` verb -- delegates to the canonical Show-MiosDashboard
defined in M:\MiOS\powershell\profile.ps1 so the dashboard rendered
here is byte-identical to the one that auto-renders on each MiOS
terminal tab open. Operator's directive ONE dashboard
globally, dictated by mios.toml.

<!-- mios-src:e546f5879a09 from build-mios.ps1:6622-6627 -->

### Pre-set the auto-MOTD guard BEFORE dot-sourcing the profile...

Pre-set the auto-MOTD guard BEFORE dot-sourcing the profile so the
profile body's auto-render is suppressed -- we explicitly call
Show-MiosDashboard ourselves below. Without this, fresh `pwsh`
processes (launched from a Start Menu shortcut, a new WT tab, or
any non-nested context) re-source the profile, which triggers its
auto-render, which then runs in addition to our explicit call --
producing two dashboards in a row. Operator-flagged
"DOUBLE DASHBOARD still when running 'mios dash'".

<!-- mios-src:c4f6ae050cc9 from build-mios.ps1:6630-6637 -->

### The original verbose mios-dash body (full ASCII logo +...

The original verbose mios-dash body (full ASCII logo + Self-replication
endpoint probes + WSL distro state + build pipeline arrow) was
operator-rejected too tall for the 80x20 portal. The
block below is dead code retained as a textual marker only -- the
heredoc above is what gets staged.

<!-- mios-src:552a1ca8b258 from build-mios.ps1:6655-6659 -->

### mios-dev.ps1 / mios-pull.ps1 -- self-resolving wrappers....

mios-dev.ps1 / mios-pull.ps1 -- self-resolving wrappers.
The Rename-PodmanDevDistro pass at the end of build-mios.ps1
drops the `podman-` prefix, so the canonical post-install name
is `$DevDistro` (= "MiOS-DEV"). These wrappers probe at RUNTIME
so they Just Work whether the rename has happened yet or not
(e.g. during a partial install or after a failed rename), and
they pick up future renames without needing regeneration.

<!-- mios-src:ad3196c7d677 from build-mios.ps1:6661-6667 -->

### Bare invocation -> mios user, login shell at /, with the...

Bare invocation -> mios user, login shell at /, with the MiOS Linux-side
dashboard rendering on entry (banner + ASCII logo + fastfetch + framing).
The dashboard is wired by /etc/profile.d/zz-mios-motd.sh inside the dev
VM (seeded by Phase 3 of the bootstrap) which auto-runs
/usr/libexec/mios/mios-dashboard.sh on every interactive bash login.
`bash -l` (login shell) ensures /etc/profile.d/* is sourced.

Args pass through verbatim so callers can still do `mios-dev --user user
-- some-cmd` etc.

<!-- mios-src:9f7a9bb10624 from build-mios.ps1:6681-6689 -->

### user mios matches the WT MiOS-DEV profile so dashboard /...

--user mios matches the WT MiOS-DEV profile so dashboard / theming
/ mios.toml resolution all hit the per-user MiOS layout. --cd /
because `.git IS /` (Architectural Law 3) -- the dev VM's git
working tree is the filesystem root.

<!-- mios-src:1aab04d05a8b from build-mios.ps1:6692-6695 -->

### <MiOSRoot>\bin\mios-pull.ps1 -- refreshes BOTH the...

<MiOSRoot>\bin\mios-pull.ps1 -- refreshes BOTH the Windows-side M:\
overlay AND the dev VM root (/) from origin/main. Two distinct git
working trees:
  1. M:\ (Windows-side mios.git overlay) -- backs every M:\usr/share/mios
     lookup, M:\usr/share/mios/configurator/mios.html (MiOS Config
     shortcut), and what the dev VM sees at /mnt/m/.
  2. / inside MiOS-DEV (the dev VM's mios.git working tree per
     Architectural Law 3, ".git IS /") -- /usr/bin/mios-pull does the
     git fetch + reset --hard inside the dev distro.
Operator confirmed bug previous mios-pull.ps1 only did
step 2, leaving M:\ stale -> `mios build` rendered an old MiOS.

<!-- mios-src:88ceddb36b95 from build-mios.ps1:6704-6714 -->

### Step 2

Step 2: dev VM root refresh.
Pre-bootc-switch the dev VM doesn't have /usr/bin/mios-pull yet (that
binary lands via the OCI image overlay during `mios build`). Inline
the equivalent bash so this verb works on day-0 -- before, during, and
after the OCI image is built. The work is identical to what
/usr/bin/mios-pull does post-bootc-switch: ensure / is a git working
tree of mios.git (Architectural Law 3, ".git IS /"), then
fetch + reset --hard origin/main.
NOTE: this whole heredoc is INSIDE the outer @"..."@ that builds
mios-pull.ps1. The @'...'@ below does NOT create a nested literal
section -- it's just literal chars in the outer here-string. Every
bash `$` that should reach the rendered file as a literal `$` must
be escaped with a backtick or PS evaluates it.

Earlier attempt passed `bash -c \$inlinePull` -- PowerShell's native-
command argument quoting mangled the multi-line string (operator-
observed install: ": invalid option namefail / -c: line
20: syntax error: unexpected end of file from `if' command on line
8"). The robust pattern is stdin-piping: write the script to bash's
stdin via the pipeline, with LF normalization so CRLF doesn't make
bash see `\r` as part of identifiers.

<!-- mios-src:95d4e615e5d1 from build-mios.ps1:6742-6762 -->

### Normalize CRLF -> LF (Windows authoring of this PS file may...

Normalize CRLF -> LF (Windows authoring of this PS file may leave
CRLF in `$inlinePull which would corrupt bash identifiers like `\r`
being treated as part of variable names) and pipe to bash via stdin
(bash -s reads the script from stdin; arguments after `--` reach the
script as `\$1 \$2 ...`). This avoids the native-cmd quoting bugs
`bash -c <multi-line>` exhibited.

<!-- mios-src:7b5a8bdf3ecb from build-mios.ps1:6785-6790 -->

### mios-update.ps1 -- self-updates the bootstrap from origin...

mios-update.ps1 -- self-updates the bootstrap from origin BEFORE
re-running build-mios.ps1. This is what makes `mios update` actually
pick up upstream changes: previously it ran the LOCAL stale
build-mios.ps1 directly, so any fix shipped to origin/main never
reached the operator until they manually re-paste the irm|iex
one-liner. The new flow:

  1. git -C M:\MiOS\bootstrap-shadow fetch + reset --hard origin/main
  2. robocopy mios-bootstrap shadow -> M:\ overlay (refreshes the
     build-mios.ps1 the next step will run)
  3. pwsh -File <freshly-overlaid build-mios.ps1>

Step 1 is idempotent (no-op if the shadow's HEAD already matches
origin/main); step 2 is destructive over the overlay paths but
those are managed by mios-bootstrap anyway.

<!-- mios-src:e00c6a9f63be from build-mios.ps1:6795-6809 -->

### 1. Self-update the shadow if .git is present and the...

1. Self-update the shadow if .git is present and the operator's
   network can reach origin. Falls through silently on failure --
   the next step still runs the (possibly stale) local copy.

<!-- mios-src:b8b62583fe28 from build-mios.ps1:6821-6823 -->

### mios-config.ps1 -- opens the HTML configurator in the...

mios-config.ps1 -- opens the HTML configurator in the operator's
default browser. Walks a candidate list so we hit the M:\ overlay
(canonical operator-edit copy) first, then bootstrap-shadow, then
legacy paths. Per operator: "have the MiOS config link open the
webpage directly in the local browser (opens the mios.html
directly installed on the newly created M:\ directories)".

<!-- mios-src:ed0bdd6d849d from build-mios.ps1:6861-6866 -->

### mios-config.ps1 -- the `mios config` verb / MiOS Config...

mios-config.ps1 -- the `mios config` verb / MiOS Config app.
Resolves mios.html in priority order and shell-executes it so the
operator's default browser opens the page. Edit fields, save -- the
browser writes a copy to %USERPROFILE%\Downloads; `mios build` step 2
promotes it back to M:\etc\mios + M:\usr\share\mios.

<!-- mios-src:d2b6c4368fd1 from build-mios.ps1:6871-6875 -->

### mios-build.ps1 -- THE operator-typed `mios build` verb. The...

mios-build.ps1 -- THE operator-typed `mios build` verb. The Day-0
contract: Windows host does ack + MiOS-DEV provisioning, then
STOPS. `mios build` is the operator-triggered next step that
promotes any operator edits saved to %USERPROFILE%\Downloads, syncs
the M:\ overlay to origin/main, then SSHes into MiOS-DEV and
ignites mios-build-driver. The dev VM is THE builder; Windows is
provisioning + handoff ONLY.

<!-- mios-src:6d88f1a3b51b from build-mios.ps1:7032-7038 -->

### <MiOSRoot>\bin\mios-build.ps1 -- the operator-triggered...

<MiOSRoot>\bin\mios-build.ps1 -- the operator-triggered `mios build` verb.
Self-replication contract: edit mios.toml in mios.html (browser saves
it to %USERPROFILE%\Downloads on Windows because file:// can't write
back), then run this script. It promotes the newest mios*.toml /
*mios*.html from Downloads into M:\etc\mios + M:\usr\share\mios,
archives the source as .imported-<timestamp>, syncs the M:\ overlay
to origin/main, then SSHes into MiOS-DEV to run mios-build-driver
(the actual build pipeline). Architectural Law 5 + the .git IS /
invariant flow through end-to-end.

<!-- mios-src:3b11d38d52d5 from build-mios.ps1:7043-7051 -->

### Sync M:\ overlay to origin/main BEFORE the dev VM handoff....

Sync M:\ overlay to origin/main BEFORE the dev VM handoff. Two
distinct git working trees need refreshing:

  1. M:\ (the Windows-side mios.git overlay) -- THIS is what backs
     M:\usr\share\mios\configurator\mios.html (opened by MiOS Config),
     M:\usr\share\mios\mios.toml (read by every Get-MiosTomlValue),
     and what the dev VM sees at /mnt/m/. Without a Windows-side
     `git fetch + reset --hard origin/main` here, M:\ stays frozen
     to whatever was on origin at the LAST install run, so:
       - MiOS Config opens an OLD mios.html
       - mios.toml reads return OLD values
       - the dev VM's build-driver via /mnt/m/ uses OLD overlay
Operator confirmed bug `mios build` rendered an
     "old MiOS build" because M:\ was stale.
  2. / inside MiOS-DEV (the dev VM's mios.git working tree -- Architectural
     Law 3, ".git IS /") -- mios-pull.ps1 delegates to
     /usr/bin/mios-pull inside the dev distro for this.

Step 1 (M:\ Windows-side) MUST run BEFORE step 2 because the dev
distro's mios-build-driver reads from /mnt/m/ for some inputs (e.g.
mios.toml lookups via Get-MiosTomlValue). Refreshing M:\ first
guarantees the dev VM build sees the latest overlay.

<!-- mios-src:ff0e88c28377 from build-mios.ps1:7081-7102 -->

### Start the WSL-Podman machine. `wsl.exe -d <distro>` later...

Start the WSL-Podman machine. `wsl.exe -d <distro>` later will
auto-start the WSL distro alone, but the podman MACHINE wraps the
distro with the rootful podman daemon + OCI builder services that
mios-build-driver uses to actually build MiOS. Without this explicit
start, the build can fail on first invocation after a reboot with
"Cannot connect to Podman" because the daemon isn't up yet.
Idempotent: no-op if the machine is already running. Operator-confirmed
`mios build` should actually open the WSL-Podman machine
AND build MiOS AND overlay newest MiOS repos at /ROOT.

<!-- mios-src:030df01f18d4 from build-mios.ps1:7135-7143 -->

### `podman machine` and `wsl.exe -d` use DIFFERENT names for...

`podman machine` and `wsl.exe -d` use DIFFERENT names for the same VM:
  wsl.exe -d expects the WSL distro registration name -- 'podman-MiOS-DEV'
  podman machine expects the machine name without prefix -- 'MiOS-DEV'
Resolve-MiosDevDistro returns the WSL distro name (because it iterates
`wsl -l -q`), which is correct for wsl.exe but causes `podman machine
start podman-MiOS-DEV` to fail with 'VM does not exist'. Strip the
'podman-' prefix for podman-machine calls.

<!-- mios-src:7c2635b577aa from build-mios.ps1:7145-7151 -->

### Pre-warm the WSL distro so its kernel + systemd are up...

Pre-warm the WSL distro so its kernel + systemd are up BEFORE we ask
podman to start the machine. Without this, `podman machine start`
races and frequently emits:
  "could not start api proxy since expected pipe is not available:
   podman-MiOS-DEV"
  "Error: machine did not transition into running state: ssh error"
A no-op `wsl.exe -d <distro> --user mios -- true` triggers WSL to
(re)launch the distro, which creates the AF_VSOCK / pipe endpoints
podman then attaches to.

<!-- mios-src:7827604bfd47 from build-mios.ps1:7154-7162 -->

### SSH handoff into MiOS-DEV. mios-build-driver is THE build...

SSH handoff into MiOS-DEV. mios-build-driver is THE build pipeline:
fetch + overlay newest mios.git at / (Architectural Law 3 ".git IS /")
-> account/identity -> install -> smoketest -> build -> deploy -> boot.
The build dashboard renders here in this WT tab (live, not proxied).
We pass --user mios because the WT MiOS-DEV profile and operator
expectations land on the mios login user (uid 1000) -- created by the
seed script in Phase 3, with passwordless sudo for the build pipeline's
privileged steps.
First-run staging: on a fresh MiOS-DEV the OCI image hasn't been built
yet, so /usr/libexec/mios/mios-build-driver doesn't exist inside the
distro. The canonical source lives in mios.git at
usr/libexec/mios/mios-build-driver (mios-dev/MiOS layout: FHS-shaped
tree directly at repo root, NO 'system_files/' prefix). Per the
"M:\ IS git" layout (build-mios.ps1 Update-MiosInstallPaths),
mios.git's working tree is overlaid AT M:\ root, so the file is at
M:\usr\libexec\mios\mios-build-driver, which is
/mnt/m/usr/libexec/mios/mios-build-driver from inside WSL. Copy it in
(idempotent -- overwrites any older staged copy) before invoking. Once
the OCI image is built and bootc switch deploys it, the file is also
present at the same path from the image overlay; this copy step
becomes a no-op on subsequent re-builds.

<!-- mios-src:24d3f5ab7ae5 from build-mios.ps1:7196-7216 -->

### Install-robustness surface the driver's REAL exit code....

Install-robustness surface the driver's REAL exit code. Without
this the `mios build` verb reported SUCCESS even when the OCI build failed
inside MiOS-DEV -> the operator believed the image built and MiOS AI would come
up, when it never did. Propagate the failure so it is visible + scriptable.

<!-- mios-src:de8c6b006e4d from build-mios.ps1:7226-7229 -->

### mios.ps1 -- THE MiOS app dispatcher. "U.N.I.F.I.E.D...

mios.ps1 -- THE MiOS app dispatcher.
"U.N.I.F.I.E.D EVERYTHING MiOS related!!!".  This file used to
render a SECOND, NON-UNIFIED layout (a numbered TUI menu) when
the operator typed `mios <anything>` -- diverging from the
canonical Show-MiosDashboard ([dashboard].rows) layout the
M:\MiOS\powershell\profile.ps1 renders.  The redundancy is
gone: `function mios <verb>` in the profile body now dispatches
to mios-<verb> directly, so this file just exists as a
thin pass-through (some legacy code paths Start-Process this
script).  The body re-defines the per-verb mios-<name> wrapper
functions and dispatches the requested verb.  No TUI menu, no
divergent dashboard.

<!-- mios-src:e6f655148dd1 from build-mios.ps1:7253-7264 -->

### <MiOSRoot>\bin\mios.ps1 -- thin verb-dispatch pass-through....

<MiOSRoot>\bin\mios.ps1 -- thin verb-dispatch pass-through.
Auto-installed by mios-bootstrap (Install-MiosLauncher).  Operator
"U.N.I.F.I.E.D EVERYTHING MiOS related!!!". This file
used to render its own Show-MiosApp TUI menu (a different layout
from the canonical Show-MiosDashboard that [dashboard].rows
drives) -- that has been REMOVED.  Now the file dot-sources the
canonical M:\MiOS\powershell\profile.ps1 (so the operator gets
the same Show-MiosDashboard render + `mios <verb>` dispatcher
every other entry path uses) then dispatches the verb passed as
argv if any.  No TUI menu, no second dashboard layout.

<!-- mios-src:fb7ee67cba21 from build-mios.ps1:7267-7276 -->

### If a verb was passed (e.g. `mios.ps1 build`), dispatch...

If a verb was passed (e.g. `mios.ps1 build`), dispatch through the
`mios` function the profile body just defined; else just leave the
operator at the loaded prompt.

<!-- mios-src:ff43254da3f3 from build-mios.ps1:7289-7291 -->

### Auto-generated by mios-bootstrap/build-mios.ps1. Block is...

Auto-generated by mios-bootstrap/build-mios.ps1. Block is replaced
on every re-run between the markers. ONLY the per-verb script
wrappers live here.  The `mios <verb>` dispatcher lives in
Get-MiOS.ps1's M:\MiOS\powershell\profile.ps1 -- this redirector
dot-sources that profile FIRST, then runs this block.  Previous
revisions had a `function mios { ... mios.ps1 ... }` here that
REDEFINED the canonical dispatcher to call the legacy
Show-MiosApp TUI hub -- "not unified
dashboards!!!" (TWO different layouts rendering: the legacy hub
AND the [dashboard].rows-driven Show-MiosDashboard).  Removed
`function mios` here so the canonical dispatcher (which routes
to mios-<verb> functions sharing the same Show-MiosDashboard
layout) wins.

<!-- mios-src:972afcc29966 from build-mios.ps1:7492-7504 -->

### mios-dash + mios-metal are defined as INLINE FUNCTIONS in...

mios-dash + mios-metal are defined as INLINE FUNCTIONS in the
Get-MiOS.ps1 profile body above (mios-dash = FULL render with
ASCII banner + services + sys specs; mios-metal = compact 80x20
framed banner + fastfetch). We don't override them with bin-
script wrappers here because the FULL render needs to query the
running MiOS-DEV state via wsl.exe -- inlining keeps it co-
located with the rest of the verb implementations and leaves
the bin-script staging point for legacy direct-invocation only.

<!-- mios-src:d2636ec7c4b8 from build-mios.ps1:7506-7513 -->

### Set-MiosWindow -- resize + re-center the CURRENT MiOS...

Set-MiosWindow -- resize + re-center the CURRENT MiOS terminal
window between [terminal] and [terminal.reading] modes from
mios.toml. "a centered 100x50 window called
MiOS 'reading mode' invoked with a command to resize (and re
center) the window between the sizes". Used by `mios portal` /
`mios reading` verbs and by the `btop` function which auto-flips
to reading mode.

<!-- mios-src:b02c744bc1e0 from build-mios.ps1:7521-7527 -->

### ── 4. Windows Terminal "MiOS" profile (settings.json patch)...

── 4. Windows Terminal "MiOS" profile (settings.json patch) ──────

The canonical implementation now lives in mios-bootstrap/Get-MiOS.ps1
(Install-MiOSGeistFont + Install-MiOSTerminalProfile + Get-MiOSCenteredWindowPosition).
Get-MiOS.ps1 runs FIRST on the irm|iex entry path, before this script
even starts, so the WT profile is already in place by the time
build-mios.ps1 lands here. The only thing we still rebind here is
the profile's commandline, so launching the "MiOS" tab from a
standalone WT (after install) opens the staged hub script (mios.ps1)
rather than a bare pwsh. Get-MiOS.ps1's commandline is just `pwsh
-NoLogo`; once the install dir exists we want it to launch the menu.

<!-- mios-src:126f156b1063 from build-mios.ps1:7668-7678 -->

### Per operator (clarified): "MiOS app opens to a windows...

Per operator (clarified): "MiOS app opens to a windows
terminal wherein 'mios *' invocations are done on the windows
host first and relevant MiOS-DEV 'mios *' invocations are
directly passed through to the podman-MiOS-DEV machine and then
the terminal is sshd in to the MiOS-DEV environment directly".

So MiOS profile commandline = Windows-side pwsh (loads MiOS PS
profile body with dashboard + `mios <verb>` dispatcher). The
dispatcher decides per-verb: Windows-host or pass-through to
MiOS-DEV via wsl/ssh. MiOS and MiOS-DEV WT profiles are
DIFFERENT entry points to the SAME branded experience -- MiOS
= Windows terminal, MiOS-DEV = direct dev VM shell.

Get-MiOS.ps1's Install-MiOSTerminalProfile owns commandline +
startingDirectory; we ONLY refresh the icon here (Pass-2 has
access to mios.ico after Generate-MiosIcons ran).

<!-- mios-src:13c33fae8c09 from build-mios.ps1:7684-7699 -->

### NOTE

NOTE: New-MiosShortcut + its shortcut-metadata helper code that
used to live here have been REMOVED. They were dead code -- the
only callers were the hub MiOS.lnk creator + the per-verb shortcut
loop, both of which were removed in earlier commits when shortcut
creation moved to Get-MiOS.ps1's FINAL STEP block. Removing the
dead Win32-interop code also eliminates AMSI heuristic flag bait.

<!-- mios-src:75fc9ffbe273 from build-mios.ps1:7747-7752 -->

### Install-root drive letter (SSOT...

Install-root drive letter (SSOT: [bootstrap.host_storage].drive_letter,
env override MIOS_DATA_DISK_LETTER). Substituted into the __MIOS_DRIVE__
placeholder of the staged launcher + gui-watch sources so the operator's
data-disk letter -- not a baked 'M' -- drives the install-root paths.

<!-- mios-src:65701a3f4455 from build-mios.ps1:7773-7776 -->

### ── ONE shortcut: MiOS (the hub)...

── ONE shortcut: MiOS (the hub) ─────────────────────────────────
Native-app behavior: the .lnk targets a tiny launcher script
(mios-launch.ps1) staged under $MiosBinDir. The launcher source
lives in src/mios-launch.ps1 in the repo (NOT inline here) so
AMSI heuristics don't see Win32-interop strings as part of the
.ps1 script content. build-mios.ps1 reads the source from disk
and writes it to $MiosBinDir at install time.

<!-- mios-src:c344714e6214 from build-mios.ps1:7779-7785 -->

### Requires -Version 5.1

Requires -Version 5.1

<!-- mios-src:bc35b223480a from build-mios.ps1:7871-7871 -->

### Shortcut targets WT.EXE DIRECTLY -- no pwsh launcher...

Shortcut targets WT.EXE DIRECTLY -- no pwsh launcher pre-flash.
Operator-reported regression: "opening apps shouldn't open a regular
windows terminal/powershell window before launching the MiOS app
ecosystem(s) -- MiOS app icons opens the app windows directly -- no
flashing a prompt that then launches the correct MiOS terminal
profile/application(s)".

The previous launcher pwsh.exe -NoProfile -WindowStyle Hidden -File
mios-launch.ps1 still produced a brief conhost flash before wt.exe
spawned (Windows shows the host process briefly even with
WindowStyle=Hidden). wt.exe is itself a windowed application -- the
.lnk pointing at wt.exe with the right args produces zero flash
because there's no intermediate console host.

Trade-off: lose the centering retry loop that mios-launch.ps1
provided. WT's --pos flag honors the initial position; the post-
bootstrap auto-launch path (in Get-MiOS.ps1's elevation block) still
runs the persistent re-center for the post-install spawn, but the
ongoing daily-shortcut path leans on WT's own positioning. If WT's
placement drifts the operator can edit globals.initialPosition in
mios.toml or right-click + drag.

<!-- mios-src:92b23b129d39 from build-mios.ps1:7994-8014 -->

### Fallback

Fallback: no wt.exe found -- run the bare hub script in a pwsh
console (still pre-flashes but at least gives the operator a
working shell). This branch should be unreachable on a
successful install since WT is a Phase 5 prerequisite.

<!-- mios-src:03ab2af810c1 from build-mios.ps1:8025-8028 -->

### ── Shortcut creation deferred to FINAL STEP of Get-MiOS.ps1...

── Shortcut creation deferred to FINAL STEP of Get-MiOS.ps1 ────────────
"applications and icons should be installed AFTER
everything--at the end!!!! LAST STEPS". The canonical 4-shortcut set
(MiOS, MiOS-WIN, MiOS Help, Uninstall MiOS) is created by
Get-MiOS.ps1's end-of-script block AFTER bootstrap.ps1 + build-mios.ps1
succeed. build-mios.ps1's Install-WindowsBranding does NOT create
shortcuts at all -- if it did, partial-install failures would leave
broken shortcuts pointing at a half-built dev VM.

<!-- mios-src:9a0c599c7d1f from build-mios.ps1:8033-8040 -->

### ── Per-verb native-app shortcuts...

── Per-verb native-app shortcuts ────────────────────────────────
Per operator: every MiOS verb appears as its own native Windows
app so MiOS-DEV / Dashboard / Configurator / Build are findable
in Start search and pinnable to taskbar/Start individually --
not just as items inside the hub menu. Each shortcut targets the
corresponding bin/mios-*.ps1 script with its dedicated icon.
The main MiOS.lnk above stays as the unified hub.
Per-verb shortcuts -- minimal, operator-curated set.
The hub 'MiOS.lnk' is created earlier at line ~5743 (the terminal
itself). Operator-typed verbs (build / dash / update / pull) are
NOT separate apps -- they're commands typed inside the MiOS
terminal. The native-app surface is exactly five:

  1. MiOS              The Windows-side terminal (themed WT MiOS
                       profile, dashboard on launch). Created at
                       line ~5743 as the hub.
  2. MiOS-DEV          Drops directly into podman-MiOS-DEV with
                       the Linux-side dashboard rendering at
                       login (full piping/framing/ASCII logo,
                       all theming).
  3. MiOS Config       Opens mios.html (the configurator) in the
                       operator's default browser. Browser saves
                       edited mios.toml to %USERPROFILE%\Downloads;
                       `mios build` step 2 promotes Downloads
                       edits into M:\etc\mios + M:\usr\share\mios.
  4. MiOS Help         Full verb + functionality reference.
  5. Uninstall MiOS    Created in the legacy block ~line 7126.

Both Start Menu .lnk AND Desktop .lnk for each.
The native-app catalog resolves through mios.toml [apps] (SSOT).
Operator-renames the apps via mios.html -- the configurator writes
mios.toml -- next install regenerates Start Menu / Desktop shortcuts
against the new name+bin+icon set. Vendor fallback below mirrors
what mios.toml [apps] ships with for cold first-run before any
operator edit.
canonical 4-shortcut set: MiOS / MiOS-WIN /
MiOS Help / Uninstall MiOS, all created in Get-MiOS.ps1's
Install-MiOSNativeApp. NO per-verb shortcut creator here -- the
entire [apps.shortcuts] toml-driven loader was a duplicate creator
that re-seeded MiOS-DEV.lnk / MiOS Config.lnk / MiOS Help.lnk on
every install (caught in the 15:27 install screenshot).
The previous "$verbShortcuts = @()" guard was racy: any operator
mios.toml [apps.shortcuts] section would re-populate it. Removed
entirely. Per operator: "JUST FUCKING LISTEN".

<!-- mios-src:487814dd4c55 from build-mios.ps1:8043-8086 -->

### Garbage-collect every shortcut OUTSIDE the canonical 4-set...

Garbage-collect every shortcut OUTSIDE the canonical 4-set
(MiOS / MiOS-WIN / MiOS Help / Uninstall MiOS). Per operator
MiOS-DEV.lnk and MiOS Config.lnk are NOT canonical --
the MiOS shortcut already targets the dev VM, and `mios config`
is a typed verb. Idempotent: if absent, skip.

<!-- mios-src:b19e87233537 from build-mios.ps1:8090-8094 -->

### Removed verbs (now operator-typed inside the MiOS terminal):

Removed verbs (now operator-typed inside the MiOS terminal):

<!-- mios-src:fabf9eb9d8ae from build-mios.ps1:8098-8098 -->

### ── MiOS Linux Apps (Start Menu subfolder)...

── MiOS Linux Apps (Start Menu subfolder) ─────────────────────────
"no MiOS Linux apps in windows start menus".
Two-prong fix: (a) /etc/wsl.conf adds [gui] guiApplications=true
so WSLg auto-exports .desktop entries (handled in mios.git);
(b) we ALSO create explicit Windows .lnk shortcuts here, because
WSLg auto-export depends on the distro's user-systemd being
healthy and the operator's preferred friendly names (Files / Web
/ VSCodium / etc.) don't survive the "(on podman-MiOS-DEV)"
suffix WSLg appends. Explicit shortcuts under
  Start Menu\Programs\MiOS\Linux Apps\<FriendlyName>.lnk
are bulletproof + match the operator's mental model.

Each shortcut targets wsl.exe with the dev distro:
  wsl.exe -d podman-MiOS-DEV --user mios -- flatpak run <appid>
Source of truth: mios.toml [desktop].flatpaks (operator-editable
via mios.html; new entries auto-surface on next bootstrap).

<!-- mios-src:2efd10707c95 from build-mios.ps1:8116-8131 -->

### Prefer wslg.exe (part of WSL since 2021) over wsl.exe so...

Prefer wslg.exe (part of WSL since 2021) over wsl.exe so the
shortcuts launch the GUI app DIRECTLY with no console popup
and Windows-Terminal-style chrome -- matches the exact UX
that WSLg's own auto-published `App (on podman-MiOS-DEV).lnk`
entries give the operator. wsl.exe spawns a host console;
wslg.exe is a pure GUI launcher.

<!-- mios-src:57b471c71256 from build-mios.ps1:8150-8155 -->

### AppId -> friendly-name mapping. Operator-edit-friendly...

AppId -> friendly-name mapping. Operator-edit-friendly: short
name appears in Start Menu, app id resolves the actual flatpak.
Unknown entries fall back to the last segment of the app id.

<!-- mios-src:93b306638ce5 from build-mios.ps1:8168-8170 -->

### Pull current flatpak picks from mios.toml...

Pull current flatpak picks from mios.toml [desktop].flatpaks.
Get-MiosTomlValue's regex is SINGLE-line (`(?<val>.+?)$`),
so a multi-line `flatpaks = [\n  "a",\n  "b",\n]` array
returns just `[` (the opening bracket on the same line as
the assignment). That stray `[` then propagated as a
phantom entry, producing a `[.lnk` shortcut in the
operator's Linux Apps folder (21:39).

Use the same multi-line array parser the overlay flatpak
loop uses upstream at line ~7945: regex-grab the bracket
body across newlines, strip TOML comments, split on commas,
trim quote/whitespace decoration.

<!-- mios-src:a763ea1b674a from build-mios.ps1:8182-8193 -->

### wslg.exe takes the same -d / --user / -- arg shape as...

wslg.exe takes the same -d / --user / -- arg shape as
wsl.exe BUT must be invoked with the FULL command path
(it doesn't run a login shell), so use /usr/bin/flatpak
explicitly. Matches WSLg's own auto-published shortcut
args exactly (e.g. for Ptyxis it writes:
  -d podman-MiOS-DEV --cd "~" -- /usr/bin/flatpak run
    --branch=stable --arch=x86_64 --command=ptyxis
    app.devsuite.Ptyxis).

<!-- mios-src:45ff5741f820 from build-mios.ps1:8228-8235 -->

### ── MiOS Services (web links via default browser)...

── MiOS Services (web links via default browser) ──────────────────
Start Menu\Programs\MiOS\Services\<Name>.url -- Internet Shortcut
files that open in the operator's default browser (Zen / Edge /
Firefox / Chrome). Operator-flagged "Should also
include shortcuts to all our containers and services as webapps/
weblinks using local browser(s)". .url files are Start Menu
indexable and respect the operator's BrowserChoice without us
having to detect the installed browser.
SSOT: mios.toml [ports].* + a label/url map of the same shape.

<!-- mios-src:28e17ff9e071 from build-mios.ps1:8284-8292 -->

### Internet Shortcut (.url) -- ASCII INI format that Windows...

Internet Shortcut (.url) -- ASCII INI format that
Windows Explorer + the Start Menu treat as a clickable
browser link. The [{000214A0-...}] block is the
ShellLinkPropertyBag GUID; Prop3=19,2 sets the file
as a Browse-shortcut (not Web-shortcut), which makes
Open With... behave correctly.

<!-- mios-src:ece676bc496c from build-mios.ps1:8332-8337 -->

### ── 7. Re-run Get-MiOS.ps1's Install-MiOSPowerShellProfile +...

── 7. Re-run Get-MiOS.ps1's Install-MiOSPowerShellProfile +
Install-MiOSTerminalProfile so EVERY install path (irm|iex Get-MiOS,
mios-update, build-mios.ps1 BootstrapOnly, etc.) deterministically
re-substitutes:
  - M:\MiOS\powershell\profile.ps1 (Show-MiosDashboard frame_width /
    right_margin / cell budget literals from current mios.toml
    [terminal])
  - WT settings.json globals (root launchMode, profiles.defaults
    scrollbarState/padding/useAcrylic/opacity/systemBackdrop/
    suppressApplicationTitle/disableAnimations/useAtlasEngine/
    experimental.* from current mios.toml [theme])
Before this hook, ONLY the irm|iex Get-MiOS.ps1 entry path triggered
those substitutions. Every install.ps1 / mios-update / re-run of
build-mios.ps1 left the deployed dashboard + WT settings.json STALE,
so toml/omp.json edits looked like they had no effect (operator
iteration loop on, which uninstalled + reinstalled
multiple times waiting for the dashboard to update -- it never did
because the Step 1-8 chain never ran).

Operator pivot "irm|iex is the main entry point for ALL
things MiOS... FIX all in code!" -> all entry paths now route through
the same Install-MiOS* function bodies, sourced from the canonical
Get-MiOS.ps1 via the MIOS_GETMIOS_FUNCTIONS_ONLY=1 dot-source gate.

<!-- mios-src:2381b9e1eabd from build-mios.ps1:8374-8396 -->

### CRITICAL

CRITICAL: do NOT use `. $path` -- PowerShell's parser
default encoding is cp1252 in many host configs (PS 5.1
always; pwsh 7 only when launched from a non-UTF8
console), and Get-MiOS.ps1 contains UTF-8 box-drawing
chars (│ ╭ ╮ ╰ ╯ ─). cp1252 reads `│` (UTF-8 E2 94 82)
as `â”‚` (mojibake) which crashes the parser with
"Unexpected token 'â”‚'". Read the file as explicit
UTF-8 and create a scriptblock from the string. dot-
sourcing the scriptblock runs in caller scope so all
function defs land here (build-mios.ps1's scope).

<!-- mios-src:0f67710b9694 from build-mios.ps1:8406-8415 -->

### ── Window resize (best-effort) + dashboard mode...

── Window resize (best-effort) + dashboard mode ──────────────────────────────
Default = 'log' (linear, sequential phase + step log lines). The
framed in-place dashboard has been a recurring source of
host-compat issues -- some hosts honor [Console]::SetCursorPosition
only intermittently, the probe can't catch every misbehavior, and
the failure mode (frames stacking forever) is awful. Linear log is
always correct.

Operators who specifically want the framed live dashboard can
opt in by setting $env:MIOS_DASHBOARD_MODE='interactive' before
launching. The probe is still run as a sanity-check in that case
so the opt-in falls back to log mode if the host is genuinely
broken.
80x30 EXACTLY -- per feedback_mios_terminal_dimensions.md: "every
spawned window must open at exactly 80 cols x 40 rows to match the
dashboard frame." Anything wider creates transient state that the
dashboard's strict-clamp width logic in Show-Dashboard would have
to compensate for; cleaner to never go wide in the first place.

<!-- mios-src:28e4d7a1667c from build-mios.ps1:8445-8462 -->

### Linear-log mode is the DEFAULT. Operator complaint: "the...

Linear-log mode is the DEFAULT. Operator complaint:
  "the spawned powershell window from irm|iex mios.bat entry still
   flickers/pins to shells top row and flashes everytime a new print
   occurs"
That's the symptom of interactive (in-place repaint) mode -- every
Show-Dashboard call rewrites the framed dashboard at the cursor-tracked
top row, and conhost/WT pseudo-console tears visibly on per-row
SetCursorPosition + Write. Linear log mode just streams Write-Host
lines, no repaint, no flicker. Operators who specifically want the
framed live dashboard opt in via $env:MIOS_DASHBOARD_MODE='interactive'.

<!-- mios-src:5e9e635133f9 from build-mios.ps1:8464-8473 -->

### Box-row helper -- guarantees every banner row is exactly...

Box-row helper -- guarantees every banner row is exactly $DW visible
chars wide, regardless of content length, so the right border lines
up with the top/bottom corners. Previous hand-rolled padding used
the wrong length for the inner string (counted "MiOS $version ..."
instead of "'MiOS' $version ..." -- the apostrophes added 2 chars
the pad math missed, so the title row was 2 cols wider than the
top frame -- the operator's "framing is broken" symptom).

<!-- mios-src:3a18f5cb572a from build-mios.ps1:8485-8491 -->

### Top-of-script banner. Title + tagline lines resolve through...

Top-of-script banner. Title + tagline lines resolve through mios.toml
[messages.installer_banner] (SSOT). Operator rebrands via mios.html.
Vendor fallbacks below preserve the existing wording when no TOML
is reachable. {version} placeholder substitutes $MiosVersion.

<!-- mios-src:f438de4e5314 from build-mios.ps1:8501-8504 -->

### Background spinner heartbeat. Writes a single character at...

Background spinner heartbeat. Writes a single character at
(SpinnerRow, SpinnerCol) every 120 ms so the operator sees the
script is still alive even when the main render loop is blocked
on a long sub-process.

Race protection: dashSync.Rendering is set to $true by the main
thread immediately before Show-Dashboard writes its rows, and
cleared afterwards. The heartbeat skips its write while that
flag is set.

<!-- mios-src:d56db4d47f65 from build-mios.ps1:8545-8553 -->

### NO-LOCAL-DEPS direct installer for the Phase-0 platform...

NO-LOCAL-DEPS direct installer for the Phase-0 platform prereqs (operator
"without ANY local dependencies"). Used when winget is absent OR
its install failed -- everything pulls from upstream GitHub releases or the
built-in `wsl --install`, so a clean machine bootstraps with nothing
pre-installed. Fail-soft: returns $false on any miss so the caller falls
through to the existing required-prereq failure (never worse than before).

<!-- mios-src:93d488c2bd66 from build-mios.ps1:8604-8609 -->

### Auto-install Phase 0 prerequisites. Per operator "without...

Auto-install Phase 0 prerequisites. Per operator "without ANY local
dependencies": winget is an OPTIONAL accelerator; each prereq also has a
direct path (git -> PortableGit, wsl -> built-in `wsl --install`, podman ->
containers/podman release), so a fresh machine with no winget still
bootstraps end-to-end. The prereq catalog resolves through mios.toml
[bootstrap.prereqs] (SSOT) so operators can swap implementations via mios.html.

<!-- mios-src:bd6e38ef2030 from build-mios.ps1:8662-8667 -->

### Provision .wslconfig FIRST, before any podman-machine init....

Provision .wslconfig FIRST, before any podman-machine init.
WSL2 reads .wslconfig at utility-VM start; if we write it after
podman has already spawned the VM, mirrored mode + firewall=false
never apply until the next `wsl --shutdown`. Operator-flagged
cockpit + every other container port timed out from
Windows because the VM came up in NAT mode while Phase 4's
post-hoc .wslconfig write said mirrored. Phase 4 still re-calls
this (idempotent) so any path that skips Phase 3 still lands
the config.

<!-- mios-src:377e61b450cf from build-mios.ps1:8932-8940 -->

### Check via Podman API first (covers rootful machine-os...

Check via Podman API first (covers rootful machine-os distros inaccessible via wsl.exe).
Accept BOTH the canonical "MiOS-DEV" and the legacy "MiOS-BUILDER" names so existing
installs don't get redundantly recreated. If only the legacy name is found we adopt it
in-place by re-pointing $BuilderDistro -- the operator can `podman machine rm` and
re-run for the canonical name.

<!-- mios-src:c997616d3e6a from build-mios.ps1:8945-8949 -->

### `(?i)` = case-insensitive. Different podman versions print...

`(?i)` = case-insensitive. Different podman versions print
the Running column as `true`/`false` (lowercase) or
`True`/`False` (capitalized); the previous regex was
case-sensitive on `true` and silently missed running
machines on capitalized-output builds, leading the script
to fall through into init and then hit "vm already exists".

<!-- mios-src:a2272e4c0732 from build-mios.ps1:8953-8958 -->

### Generic start failure -- registration exists but won't...

Generic start failure -- registration exists but won't start.
Force-remove so the subsequent New-BuilderDistro init has a
clean slate. This catches cases where the previous run was
SIGINT'd mid-init and left the machine in an unstartable
half-provisioned state. podman machine rm with --force is
destructive of THE BUILD VM only -- no MiOS image / no
operator data lives there yet at Phase 3, so this is
always safe at this point in the pipeline.

<!-- mios-src:3df50ee4a2ac from build-mios.ps1:8997-9004 -->

### Belt-and-braces sweep

Belt-and-braces sweep: even if NONE of the three detection
paths above (Running probe, Stopped+start probe, wsl.exe
legacy probe) flagged the machine as live, podman may still
have a registration on disk for $BuilderDistro from a prior
SIGINT'd / aborted run. Hitting `podman machine init` on an
existing registration produces:
    Error: vm "MiOS-DEV" already exists on hypervisor
which the dashboard surfaces as a Phase 3 FATAL with no
recovery path that the operator can act on.

Pre-purge: ask `podman machine ls` (any state, any case) for
the registration. If it exists we KNOW the previous detection
paths considered it not-startable, otherwise $machineRunning
would already be $true. Force-remove so init has a clean
slate. Safe at Phase 3: no MiOS image / operator data lives
in the dev VM yet, and the rebuild is what the operator
signed up for by re-running the bootstrap.

<!-- mios-src:8cd4e4ef48bc from build-mios.ps1:9022-9038 -->

### Even if podman-machine has NO registration, the underlying...

Even if podman-machine has NO registration, the underlying
WSL distro side can still hold a leftover registration --
especially after `podman machine rm` succeeded but the
WSL distro unregister step failed (or was never reached
by an interrupted run). The init then explodes with:
    Error: vm "MiOS-DEV" already exists on hypervisor
because the WSL-side hypervisor already has the distro.
Sweep both candidate names: the canonical "podman-MiOS-DEV"
that podman init creates, and the bare "MiOS-DEV" that the
rename step (Rename-PodmanDevDistro) produces.

<!-- mios-src:2d4bac423d0d from build-mios.ps1:9046-9055 -->

### Invoke-MiosOverlaySeed is deliberately NOT called anymore....

Invoke-MiosOverlaySeed is deliberately NOT called anymore.
It was the legacy PACKAGES.md fenced-block parser that ran
`dnf5 install` per ```packages-*``` block. As of the
SSOT is mios.toml `[packages.<section>].pkgs` (resolved via
automation/lib/packages.sh), and PACKAGES.md was relegated to
docs at usr/share/doc/mios/reference/PACKAGES.md. The legacy
function's path check now warns "overlay seed skipped" on every
run because it looks at the moved path -- pure noise that
confused the operator's "ignition failed" reading on.
Removed from the call chain. The function body itself is left
in place under a deprecation guard so any stale external caller
still loads cleanly; bare invocation is now a no-op.

The actual overlay work happens below in Invoke-MiosQuadletOverlay,
which `git fetch + reset --hard FETCH_HEAD`s mios.git to / inside
MiOS-DEV (the canonical "/ IS the git working tree" surface).

<!-- mios-src:371f904803dd from build-mios.ps1:9069-9084 -->

### Quadlet/systemd overlay -- mounts mios.git into MiOS-DEV's...

Quadlet/systemd overlay -- mounts mios.git into MiOS-DEV's / via
`git fetch + reset --hard`, enables sysusers/tmpfiles, runs the
canonical fetcher set (fonts, oh-my-posh, ollama). Heavy services
(mios-ai, mios-forgejo-runner) are opt-in via MIOS_DEV_ENABLE_AI=1
/ MIOS_DEV_ENABLE_RUNNER=1. Idempotent via
/var/lib/mios/.quadlet-overlay-seeded sentinel.

<!-- mios-src:2b2fb551336f from build-mios.ps1:9086-9091 -->

### Layer MiOS build essentials onto MiOS-DEV. Per...

Layer MiOS build essentials onto MiOS-DEV.

Per feedback_mios_dev_equals_mios.md: the dev VM is MiOS in full
parity. machine-os 6+ is the LOCKED base (per operator), but it
ships stripped down -- no mkpasswd, no openssl, no passlib, no
bootc -- so MiOS content has to LAYER ON TOP at provisioning time
(NOT at runtime inside the driver, which would paper over broken
provisioning). Install the minimum the build pipeline needs so the
driver can assume "everything MiOS has" is present when it starts.

Full feature parity (every package, container, flatpak, model)
still happens via `bootc switch localhost/mios:latest + reboot`
at the end of mios-build-driver -- this step is just the seed for
the build to RUN.

<!-- mios-src:729c9be88b9f from build-mios.ps1:9094-9107 -->

### NB: on Fedora 44 the `mkpasswd` binary moved out of `whois`...

NB: on Fedora 44 the `mkpasswd` binary moved out of `whois` into
its own `mkpasswd` package -- include both so the build essentials
set is correct on every Fedora vintage the dev VM might run.

iptables/nftables: machine-os 6+ ships without a firewall backend,
which makes podman's netavark networking refuse to set up the
build-container's network ("Must provide a valid firewall backend,
got iptables"). Without one, every `podman build` in the dev VM
dies at the first RUN step that needs network. Install BOTH so
netavark picks whichever is preferred on a given Fedora vintage.

MUST wrap in EAP=Continue + PSNativeCommandUseErrorActionPreference=$false:
dnf emits "Failed to set locale, defaulting to C.UTF-8" to stderr
(a harmless warning when LANG isn't set in the WSL distro), and
also "Transaction failed:" lines for non-critical post-scriptlet
errors (e.g. whois symlink-creation, which doesn't actually break
the install). Under PS 7.4+ defaults (EAP=Stop +
PSNativeCommandUseErrorActionPreference=$true), either of those
throws straight to the outer FATAL handler. The actual install
success is checked via $LASTEXITCODE below.
SSOT: dev VM essentials list comes from the layered mios.toml
chain. Per operator: Epiphany configurator HTML edits flow
through to every consumer.

Layered resolution (highest → lowest precedence):
  1. M:\etc\mios\mios.toml          -- HOST overlay (Epiphany
                                       configurator's save target;
                                       visible from Windows AND
                                       from MiOS-DEV via /mnt/m/)
  2. M:\usr\share\mios\mios.toml    -- VENDOR copy from mios.git
First layer with a non-empty [packages.dev_vm_essentials] wins.

<!-- mios-src:455c019ac7ce from build-mios.ps1:9110-9140 -->

### dnf's exit code is unreliable on rootful machine-os: %post...

dnf's exit code is unreliable on rootful machine-os: %post / %triggerin
scriptlets fail with "Transport endpoint is not connected" because there's
no systemd PID 1 to take daemon-reload, and harmless cosmetic ones (e.g.
whois-man alternatives symlink) also exit non-zero. Verify by `rpm -q`
against the actual package names instead. Note: `iptables` resolves to
`iptables-legacy` on Fedora 44; rpm -q on the source name returns
"package iptables is not installed" even when the alternatives provider
IS installed -- so query the resolved provider too.

<!-- mios-src:bdf89324501c from build-mios.ps1:9193-9200 -->

### ── Full MiOS OCI image parity at overlay time...

── Full MiOS OCI image parity at overlay time ──────────────────
"podman-MiOS-DEV machine doesn't have the
full packages list and flatpaks installed at overlay time --
ALL sourced from the toml embeds ... podman-MiOS-DEV = full
MiOS OCI image(s) parity".  This step iterates
[packages.dev_overlay].sections (22 sections by default --
base/security/utils/build-toolchain/containers/cockpit/storage/
virt/gpu-*/gnome-flatpak-runtime/ai/sbom-tools/self-build/
network-discovery/updater/cockpit-plugins-build/k3s-selinux-build/
uki) and layers every [packages.<section>].pkgs into the dev VM.
Then installs every ref in [desktop].flatpaks.

Toggle via mios.toml [bootstrap].dev_overlay_full = false for a
minimal overlay (essentials only).  Default = full parity per
operator directive.  The trade-off is bootstrap time -- full
parity adds 20-40 min of dnf + flatpak network/disk work on
first install.  The reward: every layered RPM and flatpak the
MiOS OCI image carries is already present in podman-MiOS-DEV
without a `bootc switch` reboot.

<!-- mios-src:293647e62132 from build-mios.ps1:9230-9248 -->

### Process each section. Read [packages.<section>].pkgs. NOTE...

Process each section. Read [packages.<section>].pkgs.
NOTE: build the regex via SINGLE-QUOTED concat so `$`
inside the pattern stays a literal `$` for PS-string-eval
then resolves to the regex line-end anchor.  The previous
double-quoted `"...\$..."` form had PowerShell collapse
`\$` to `$` which the regex engine then treated correctly
-- BUT the `$` mid-string was being seen as a sub-expr
opener by some PS hosts (operator's run hit zero matches
on every section), so single-quoted is the safer shape.

<!-- mios-src:56ed5044aebd from build-mios.ps1:9281-9289 -->

### Per-ref install with explicit exit-code check. "NOT AT ALL...

Per-ref install with explicit exit-code check.
"NOT AT ALL A MIOS OVERLAY...
nautilus / epiphany not found".  Previous version
silently succeeded on every flatpak install
regardless of actual outcome (the inner bash used
`command -v flatpak ... && flatpak install ... ||
echo deferred` which always exits 0 because of the
`|| echo`).  Now we run flatpak directly, capture
the exit code, and log Pass / Fail per ref so the
operator can see exactly what made it into the dev
VM.  `rpm -q flatpak` first to gate -- if flatpak
isn't even installed (machine-os 6.0 base ships
without it), skip the whole pass with one warn
instead of N "deferred" lines.

<!-- mios-src:11747fbd7ccf from build-mios.ps1:9342-9355 -->

### dbus-launch must be on PATH before any `flatpak install`...

dbus-launch must be on PATH before any
`flatpak install` runs. The podman-machine-os
6.0 base image ships dbus-broker (system bus
only) but NOT dbus-x11 (session bus launcher),
so flatpak's pre-install token-request step
fails with:
    error: Failed to execute child process
    "dbus-launch" (No such file or directory)
The retry-with-arch path then dies the same
way and the install loop reports 0/N OK.
Operator-flagged. Cheap fix: install
dbus-x11 (and its xauth dep) here once, before
any flatpak call -- under 200 KB, runs in
~2-3s. Idempotent: dnf no-ops on second run.

<!-- mios-src:6dda4497297c from build-mios.ps1:9368-9381 -->

### Pre-install GNOME runtime + SDK ONCE before the per-app...

Pre-install GNOME runtime + SDK ONCE before the
per-app loop. org.gnome.Software (and other GNOME
apps) fail with "no compatible runtime" if the
platform isn't already pulled. Running this here
avoids 6x parallel runtime resolution in the
per-ref loop. Errors are non-fatal -- if the
GNOME apps don't need it, this is a no-op.

<!-- mios-src:da5d16ca867d from build-mios.ps1:9390-9396 -->

### Refresh flathub's appstream so the per-app loop resolves...

Refresh flathub's appstream so the per-app loop resolves
cleanly. The old explicit `org.gnome.Platform//master` pre-pull
errored "Nothing matches org.gnome.Platform in remote flathub"
(//master is a gnome-nightly branch, NOT flathub -- flathub uses
versioned branches;). Runtimes are pulled as deps by
each per-app install below, so the pre-pull was redundant anyway.

<!-- mios-src:0a4c64221216 from build-mios.ps1:9402-9407 -->

### Parse "remote:appid" form; default to flathub when no...

Parse "remote:appid" form; default to flathub when no prefix.
Operator-flagged nautilus/ptyxis shims
errored "app/<id>/x86_64/master not installed" because
the install loop hardcoded `flathub` and our toml
entries used `gnome-nightly:org.gnome.Nautilus.Devel`
+ `fedora:org.gnome.Epiphany`.

<!-- mios-src:9f15e3952fde from build-mios.ps1:9437-9442 -->

### `dbus-run-session --` spawns a one-shot D-Bus session bus...

`dbus-run-session --` spawns a one-shot D-Bus
session bus, runs the command, then tears it
down. Without it, flatpak's pre-install token-
request step ("Requesting tokens for remote
fedora") tries to dbus-launch into a session
that doesn't exist and dies with:
    error: Could not connect:
    No such file or directory
which is what killed `fedora:org.gnome.Epiphany`
for the even after dbus-x11
was installed. dbus-run-session is part of dbus
(always present on Fedora-base machine-os).

<!-- mios-src:af7ca00a8730 from build-mios.ps1:9457-9468 -->

### ── NVIDIA WSL userland (gated on /dev/dxg present in dev...

── NVIDIA WSL userland (gated on /dev/dxg present in dev VM) ───
"WSLg + GPU-PV or CDI" -> "WSLg + NVIDIA
Vulkan ICD". Installs NVIDIA's userspace Vulkan ICD + GLX/EGL
libs from the official CUDA repo. Userland-only; no kernel
modules. The script self-detects /dev/dxg + /mnt/wslg presence
and exits cleanly on non-WSLg substrates (bare-metal / Hyper-V
/ OCI). Idempotent.

<!-- mios-src:887ac5d80838 from build-mios.ps1:9515-9521 -->

### Disable netavark's firewall management. WSL2's kernel...

Disable netavark's firewall management. WSL2's kernel doesn't ship
the iptables/nf_tables netfilter modules that netavark expects, so
even with the iptables BINARY present (whois package above) the
build container's network setup fails with:
  "setup network: netavark: Must provide a valid firewall backend"
The build doesn't need iptables-managed isolation -- it just needs
outbound network for package pulls. firewall_driver=none tells
netavark to skip firewall rule installation; the bridge interface
still works for outbound traffic via WSL2's normal NAT.

<!-- mios-src:087e4a4b72d7 from build-mios.ps1:9539-9547 -->

### ── MiOS terminal experience seed inside dev VM...

── MiOS terminal experience seed inside dev VM ──────────────────
Symlink /usr/libexec/mios + /usr/share/mios to the M:\ overlay
(mios.git's working tree visible at /mnt/m/ via WSL automount)
so mios.git's existing /etc/profile.d/mios-*.sh scripts can find
/usr/libexec/mios/mios-dashboard.sh + /usr/share/mios/oh-my-posh/
at the canonical paths -- without doing a heavy file-by-file
copy. After bootc switch at end-of-build, the OCI image's real
/usr/{libexec,share}/mios ride on top via composefs and the
symlinks become irrelevant.

Drop a single bridge in /etc/profile.d/ that sources mios.git's
profile.d scripts FROM /mnt/m/ on every interactive login. Auto-
disables once /usr/share/mios is real (post-bootc-switch).

<!-- mios-src:1182483c47ad from build-mios.ps1:9571-9583 -->

### ── Ensure the `mios` user exists (idempotent)...

── Ensure the `mios` user exists (idempotent) ────────────────────────
Per (`getpwnam(mios) failed 17 / User not found`):
in BootstrapOnly mode, the OCI build's quadlet-overlay step (which
runs systemd-sysusers and creates uid 1000=mios) is DEFERRED and
never executes. Without the mios user, /etc/wsl.conf default=mios
fails on the next `wsl -d podman-MiOS-DEV` invocation (the prior
behaviour log message "[Phase 3] -- next entry uses mios as default"
was a lie -- the user didn't exist yet). Create it here so every
verb that enters the dev distro (mios dev, mios-dev.lnk, the
mios-launch.ps1 -Verb dev path) lands as a real user.

<!-- mios-src:c5299e99f47a from build-mios.ps1:9597-9606 -->

### Set a known password so Cockpit PAM and operator-typed sudo...

Set a known password so Cockpit PAM and operator-typed sudo
prompts work. Operator can change it any time inside the dev
VM with `passwd`. The MiOS canonical default is `mios`.

<!-- mios-src:0ee09a452d68 from build-mios.ps1:9614-9616 -->

### ── /etc/wsl.conf [boot] systemd=true + [user] default=mios...

── /etc/wsl.conf [boot] systemd=true + [user] default=mios ─────────
[boot] systemd=true MUST be set or the distro boots without systemd
as PID 1; smoke tests then see state='offline' and Quadlets / the
flatpak first-boot service / every service-coupled bootstrap step
fails. WSL >= 0.67.6 honors this on next terminate+reentry.
[user] default=mios so `wsl -d podman-MiOS-DEV` / `wsl -d MiOS-DEV`
land in the mios shell; only written if the user exists or the
distro entry breaks.

<!-- mios-src:3c7a4e613cdd from build-mios.ps1:9629-9636 -->

### ── btop MiOS theme + 80x20 preset for the dev VM...

── btop MiOS theme + 80x20 preset for the dev VM ─────────────────────
image #15: btop reports "Width = 75 Height = 18,
Needed 80 x 24". btop runs INSIDE the dev VM (Linux) so the Windows
config at M:\MiOS\btop doesn't apply -- it reads ~/.config/btop/.
Source files are exposed via WSL automount at /mnt/m/MiOS/btop/.
Stage to BOTH the mios user (canonical) and root (in case of root
sessions). Symlink approach so operator edits to mios.toml -> rebuild
omp.json + theme flow through automatically.

<!-- mios-src:79eb876d0b02 from build-mios.ps1:9688-9695 -->

### System-wide fallback first. mios-btop.sh exports...

System-wide fallback first. mios-btop.sh exports
BTOP_CONFIG_DIR=/etc/btop when the user has no ~/.config/btop,
so this guarantees the MiOS preset/palette renders even if the
per-user copy is missing (e.g. /=git home edge case).
screenshot: btop launched with btop's
compiled-in defaults (preset 3 = cpu+net, update_ms=2000)
because no config was found at $HOME/.config/btop. With this
/etc/btop/ copy in place, the resolver hits it unconditionally.

<!-- mios-src:36063df6b42d from build-mios.ps1:9697-9704 -->

### ── Flatpak convenience symlinks (operator: epiphany /...

── Flatpak convenience symlinks (operator: epiphany / nautilus etc. should work) ─
ran `nautilus` and `epiphany` after install, got
"command not found" -- "LIAR!!!!!!". Install log said the flatpaks
installed OK; they did, but flatpak exports binaries as their full
app IDs (org.gnome.Epiphany, etc.) under /var/lib/flatpak/exports/bin/,
NOT as short names. Operator expects `epiphany`, `nautilus`, etc.
to work directly. Symlink the canonical short names into /usr/local/bin/
pointing at the flatpak wrappers.

<!-- mios-src:dbc8dbde2c2c from build-mios.ps1:9734-9741 -->

### Write the seed script to a tempfile on M:\ (visible inside...

Write the seed script to a tempfile on M:\ (visible inside the dev
VM at /mnt/m/) and invoke bash on the path. Piping the script to
`bash` via PowerShell stdin gets CRLF-mangled -- bash sees `set -\r`
and aborts with "set: -: invalid option" on line 1, killing the
whole script before any work runs (operator log: "bash: line 1:
set: -: invalid option ... syntax error: unexpected end of file
from `if' command on line 9").

<!-- mios-src:3c05b104ebd3 from build-mios.ps1:9763-9769 -->

### Compile MiOS dconf overrides into the system-db cascade....

Compile MiOS dconf overrides into the system-db cascade.  The
files at /etc/dconf/db/local.d/00-mios-theme + /etc/dconf/profile/
user ship in mios.git's overlay but only take effect after
`dconf update` builds the binary system-db.  Without this, the
adw-gtk3-dark + prefer-dark defaults stay inert and every GTK
app boots with the upstream light Adwaita fallback (operator-
flagged "not the mios.toml defined prefer-dark mode
yet").

<!-- mios-src:152d6e5d03ff from build-mios.ps1:9798-9805 -->

### bash -c (NOT -lc) -- the dconf update step must not trigger...

bash -c (NOT -lc) -- the dconf update step must not trigger
/etc/profile.d/ cascade (zz-mios-motd.sh -> mios mini -> fastfetch
render) which can hang here under WSL's pre-systemd boot state
and stall the entire install. dconf is in $PATH at /bin/dconf
without login-shell PATH-extension.
Operator-flagged install "stuck here" at this step.
NOTE: keep this bash -c free of embedded double-quotes and parens --
PowerShell's native-arg quoting mangles them passing to wsl.exe (the
'syntax error near unexpected token (' came from the old
echo message's "(...)"). Plain words only.

<!-- mios-src:0f23a11c7ad3 from build-mios.ps1:9812-9821 -->

### Bibata-Modern-Classic cursor install. mios.git's...

Bibata-Modern-Classic cursor install. mios.git's automation/57-gnome.sh
bakes Bibata into the bootc OCI image MANDATORILY, but the dev VM
(podman-MiOS-DEV = podman-machine-os Fedora 44 + MiOS overlay) doesn't
run that automation. Without this overlay step, dconf points at
'Bibata-Modern-Classic' but the theme dir doesn't exist -> libXcursor
silently falls back to default (operator-flagged "not
seeing bibata cursor that is the GLOBAL MiOS defaults"). Match the
image install path so the dev VM has the same cursor surface.

<!-- mios-src:718769453bbc from build-mios.ps1:9827-9834 -->

### Base64-wrap the bibata script. Passed inline, its embedded...

Base64-wrap the bibata script. Passed inline, its embedded
double-quotes/parens/$(...) get mangled by PowerShell's native-arg
quoting into bash syntax errors ("unexpected token ("
on the size echo). Encoding the whole script means ONLY base64 chars
reach the bash -c argument -- nothing to mangle. LF-normalize first.
Also guards the version/download/tar steps with || (a bare
`var=$(pipeline)` exits under set -e when the pipeline fails).

<!-- mios-src:ab82135e404c from build-mios.ps1:9852-9858 -->

### MiOS AI CLI install

MiOS AI CLI install: Claude Code + Gemini CLI globally via npm.
Both are Node.js CLIs distributed via npm, so they don't fit RPM
packaging. The helper script reads mios.toml [packages.ai].
npm_globals to discover what to install -- operators can extend
the list via /etc/mios/mios.toml or ~/.config/mios/mios.toml.
ON by default; MIOS_SKIP_AI_CLIS=1 to skip.

<!-- mios-src:cc6bd05d2ddf from build-mios.ps1:9905-9910 -->

### ── Unified mios-home + locale reconcile (single root pass)...

── Unified mios-home + locale reconcile (single root pass) ──────────────
Every Phase-3 seed step above ran against the LIVE distro, which still
defaults to the bundled `user` (UID 1000) -- /etc/wsl.conf [user]=mios only
takes effect after the shutdown below. So any step that wrote under
/var/home/mios as the default user left files owned by UID 1000, and the
mios user (UID 992) then hits "path ... not owned by the current user" +
oh-my-posh "permission denied" writing ~/.cache. Separately, the
podman-machine-os base ships only `C.utf8` (NOT `C.UTF-8`), so the image's
/etc/locale.conf LANG=C.UTF-8 makes every login emit
`setlocale: cannot change locale`. Reconcile BOTH here, from one root pass,
so the dev VM deploys clean every time. Dev-substrate only -- the bootc
image keeps its own Fedora C.UTF-8 locale.conf and bakes home ownership.

<!-- mios-src:9bbd976cb5e8 from build-mios.ps1:9922-9933 -->

### The overlay seed wrote /etc/wsl.conf [user] default=mios so...

The overlay seed wrote /etc/wsl.conf [user] default=mios so future
`wsl -d podman-MiOS-DEV` invocations land in the mios user (not the
bundled `user` UID 1000). But /etc/wsl.conf is read at distro
START -- the live instance running RIGHT NOW was launched with the
pre-seed config and still defaults to `user`. Terminate the distro
so the next entry (menu option 1 or 5) re-launches with the new
default user. Idempotent: if the distro isn't running, --terminate
is a no-op.
Full `wsl --shutdown` (utility VM + all distros) instead of just
`wsl --terminate <distro>`. The terminate path only restarts the
distro process, leaving the WSL2 utility VM running with whatever
networkingMode it booted in. Symptom if the utility
VM started in NAT mode earlier in the install (e.g. due to a
wsl --list -v probe in Phase 1 firing before .wslconfig was on
disk), .wslconfig's mirrored mode never takes effect and every
container port stays unreachable from Windows. shutdown forces
a clean utility-VM restart so the operator's next MiOS terminal
launch picks up mirrored + firewall=false + the /etc/wsl.conf
[user]=mios default user in one shot.

<!-- mios-src:7f2112589e35 from build-mios.ps1:9947-9965 -->

### ── Phase 4 -- WSL2 .wslconfig...

── Phase 4 -- WSL2 .wslconfig ───────────────────────────────────────────
Phase 3 already wrote .wslconfig BEFORE initializing the dev VM
(so mirrored networking + firewall=false applied at first boot).
This phase is the idempotent re-check + post-Phase-3 firewall
rules. Set-MiosWslConfig is a no-op if all required keys already
match.

<!-- mios-src:c95657189c3b from build-mios.ps1:9973-9978 -->

### Windows Firewall inbound rules for MiOS container ports....

Windows Firewall inbound rules for MiOS container ports. SSOT is
mios.toml [ports].* + [ports.lan_firewall].profiles/.expose.
Without these, mirrored networking carries the WSL port bind onto
Windows' all interfaces but Defender blocks inbound from any LAN
device (phone, tablet, second laptop). Operator-flagged.

<!-- mios-src:bcc14942f788 from build-mios.ps1:9982-9986 -->

### ── Bootstrap finalize: smoke test -> Windows install ->...

── Bootstrap finalize: smoke test -> Windows install -> launcher ───────
The auto-rename (podman-MiOS-DEV -> MiOS-DEV) is OFF by default
because podman's WSLDistroName() hardcodes the `podman-` prefix
-- a renamed distro breaks every `podman machine start/init/ssh`
with WSL_E_DISTRO_NOT_FOUND. User-facing surfaces (dashboard,
mios-dev launcher, icons, app menu) already hide the prefix, so
operators see "MiOS-DEV" everywhere they look while the actual
WSL distro stays as "podman-MiOS-DEV" for podman's sake. Set
$env:MIOS_RENAME_DISTRO=1 to opt in.

<!-- mios-src:04ea8ecbc61b from build-mios.ps1:10005-10013 -->

### ── -BootstrapOnly: exit cleanly here...

── -BootstrapOnly: exit cleanly here ─────────────────────────────────────
The curl/iex entry path stops here. The operator now has:
  * MiOS-DEV WSL2 distro (renamed, podman-managed, overlay applied)
  * Windows-side oh-my-posh / Geist / Nerd Font / theme installed
  * MiOS install root on M:\MiOS\ (or fallback) with bin/icons/themes
  * Desktop + Start Menu shortcuts including "Build MiOS"
They can now click "Build MiOS" to drive the OCI image build (which
re-runs this script with -BuildOnly).

<!-- mios-src:825122d9e0e2 from build-mios.ps1:10025-10032 -->

### Hard gate the script-level auto-chain at line ~6915. The...

Hard gate the script-level auto-chain at line ~6915. The
`return` below exits this function but the script-level
epilogue still fires the auto-chain unless we set the env
sentinel here. Per feedback_mios_bootstrap_stops_at_dev_ready:
bootstrap MUST stop at the hint banner; build is operator-
triggered via `mios build`.

<!-- mios-src:2a29af26fa7c from build-mios.ps1:10035-10040 -->

### ── Operator-facing end-of-Pass-2 summary...

── Operator-facing end-of-Pass-2 summary ────────────────────
The bootstrap STOPS here. The operator decides when to fire
the build pipeline by typing `mios build` (or clicking the
MiOS Build shortcut). Per
feedback_mios_bootstrap_stops_at_mios_dev_ready memory: the
Windows entry installs everything UP TO MiOS-DEV being a
native app, then prints hint lines and returns. No auto-chain.

<!-- mios-src:b6c3ac97d9f1 from build-mios.ps1:10042-10048 -->

### Banner title + bullet list resolve through mios.toml...

Banner title + bullet list resolve through mios.toml
[messages.install_complete] (SSOT). Operator edits via mios.html
for any custom branding text. Vendor fallback below is the cold
first-run set when no TOML is reachable.

<!-- mios-src:5e6778448428 from build-mios.ps1:10051-10054 -->

### Frame chars come from mios.toml...

Frame chars come from mios.toml [branding.dashboard].frame_chars
so the install-complete banner matches every other framed surface
(Show-MiosDashboard, mios-dashboard.sh, agreement gate, etc.).
Per "headers and dashboards and framing/
piping are all scattered and not fitting because they aren't
TRULY based off the toml code as source for everything".
Vendor default '╭─╮│╰╯' if mios.toml is unreachable.

<!-- mios-src:ea3490a36718 from build-mios.ps1:10067-10073 -->

### Verb list resolves through mios.toml [verbs] (SSOT)....

Verb list resolves through mios.toml [verbs] (SSOT). Operator
edits mios.html -> mios.toml -> this banner regenerates on the
next install. No hardcoded verb names. Per operator: "toml is
the SSOT for code too!!! no hardcoding ANYWHERE!!!"

<!-- mios-src:54f3789322cb from build-mios.ps1:10101-10104 -->

### Operator can pre-fill mios.toml fields via the HTML page...

Operator can pre-fill mios.toml fields via the HTML page; the
Phase-6 prompts that follow then default to whatever was saved.
Skipped when -Unattended or MIOS_NO_CONFIGURATOR=1.

<!-- mios-src:8c43d046ece9 from build-mios.ps1:10144-10146 -->

### Bake-set policy

Bake-set policy: the MINIMAL set from mios.toml [ai].bake_models
(small Qwen + the embedding model) is ALWAYS baked into the OCI
image so a fresh install is usable fully offline without bloating
the image layer. Larger models stay SELECTABLE -- offered here as
an opt-in. This prompt only runs in the interactive local-build
path (build-mios.ps1); the Forgejo CI build sources
MIOS_LLAMACPP_BAKE_MODELS straight from install.env, so cloud/CI
builds always get just the minimal set. If the operator's chosen
default model isn't already in the minimal set, offer to bake it
too; declining means it first-boot-pulls instead of bloating the
image.

<!-- mios-src:b2644995f4a2 from build-mios.ps1:10176-10186 -->

### SINGLE-quote every value

SINGLE-quote every value: install.env is SOURCED by services (many under
`set -u`), and the sha512crypt hash is `$6$salt$digest` -- double-quotes let
the shell expand $6/$salt as unbound vars -> "line 3: $6: unbound variable"
-> EVERY install.env-sourcing service fails to start (mios-forge-firstboot,
sys-env-refresh, podman-mnt-bindings, ...). Single quotes keep the literal.
(crypt hashes + model specs never contain a single quote, so the wrap is safe.)

<!-- mios-src:138953c843cb from build-mios.ps1:10212-10217 -->

### DisplayName / Publisher / URLInfoAbout all resolve through...

DisplayName / Publisher / URLInfoAbout all resolve through mios.toml
so operators rebrand the Add/Remove Programs entry via mios.html.
Per "the Applications tag/description when
installed 'MiOS - Immutable Fedora AI Workstation' should be
defined as My Personal Operating System or similar".
Prefer [branding].tagline_app (the explicit Application-tag value);
fall back to .tagline; final fallback to the literal default.

<!-- mios-src:4c966ee1a42e from build-mios.ps1:10291-10297 -->

### MiOS Configurator launcher script in the install dir. Calls...

MiOS Configurator launcher script in the install dir. Calls the
in-VM launcher (/usr/libexec/mios/mios-configurator-launch) via
`wsl --exec` so the same code path drives both surfaces:
  - Windows Start Menu / Desktop "MiOS Configurator.lnk"
  - GNOME Dock / Activities entry on a deployed host (mios-
    configurator.desktop -> the same launcher)
On Windows this opens Epiphany flatpak via WSLg -> the configurator
window appears on the Windows desktop.

<!-- mios-src:88f5f069165f from build-mios.ps1:10315-10322 -->

### MiOS Dev Shell points at the canonical post-rename name...

MiOS Dev Shell points at the canonical post-rename name first
($DevDistro = "MiOS-DEV"); pre-rename installs still get a usable
entry via the launcher's Resolve-MiosDevDistro fallback in
mios-dev.ps1 (under $MiosBinDir). The legacy Podman Shell entry
was removed -- `podman machine ssh MiOS-DEV` fails post-rename
because podman hardcodes the `podman-` prefix in WSLDistroName(),
and "MiOS Dev Shell" already covers the same use case.
MiOS Terminal / MiOS Dev Shell route through the centering launcher
(mios-launch.ps1) so every double-click lands a borderless 80x30
acrylic window screen-centered, regardless of last-window position
WT might have remembered. -WindowStyle Hidden keeps the wrapper
pwsh invisible -- only the WT window appears.
Final native-app shortcut set (5 apps total, per operator):
  MiOS              the terminal hub (created earlier in
                    Install-MiosLauncher line ~5743)
  MiOS-DEV          dev VM dashboard (created in verbShortcuts
                    loop line ~5904)
  MiOS Config       opens mios.html in default browser
                    (created in verbShortcuts loop line ~5904)
  MiOS Help         verb reference (created in verbShortcuts
                    loop line ~5904)
  Uninstall MiOS    Add/Remove-style uninstaller (this block)

The legacy MiOS Setup / Build MiOS / MiOS Configurator / MiOS
Terminal / MiOS Dev Shell shortcuts have been retired -- those
verbs are operator-typed inside the MiOS terminal, NOT separate
native apps. ('MiOS Configurator' is the legacy long-form name
for the new 'MiOS Config' app.)
vmconnect.exe is the Hyper-V Manager's VM-connection tool. On a
MiOS Hyper-V deployment the guest's mios-hyperv-enhanced.service
patches xrdp onto the VMBus vsock transport so vmconnect lights
up Enhanced Session by default (clipboard sync, dynamic
resolution, audio, USB). The shortcut opens vmconnect with no
VM specified -- operator picks their MiOS VM from the list.

<!-- mios-src:12a04fdf0827 from build-mios.ps1:10345-10378 -->

### Stale-shortcut cleanup -- if a legacy revision dropped any...

Stale-shortcut cleanup -- if a legacy revision dropped any of
these names, remove them so the operator's Start Menu / Desktop
match the canonical 5-app set.

<!-- mios-src:dac5f36d9287 from build-mios.ps1:10392-10394 -->

### Uninstaller script. Operator-asserted contract "EVERY...

Uninstaller script. Operator-asserted contract
"EVERY failure will result in an uninstallation!! Plus make sure
MiOS uninstaller ACTUALLY removes and cleans everything up after."

Goal: every uninstall leaves Windows in EXACTLY the state it was
in before MiOS was first installed. The next install starts from
zero, no stale state to confuse the next debug iteration.

What gets removed (12 artifact categories):
  1. Podman machine ($BuilderDistro) -- stop + rm
  2. WSL distros -- $BuilderDistro + $MiosWslDistro + every
     podman-MiOS-* + MiOS-BUILDER variant (defensive, since the
     install pipeline has gone through several distro names)
  3. M:\MiOS install dir, M:\ overlay files, M:\ProgramData,
     M:\ data dir
  4. WT settings.json -- launchMode root key (only if MiOS-set),
     profiles.defaults globals (only the keys MiOS writes), MiOS
     scheme, MiOS profile, MiOS-DEV profile, podman-MiOS-* auto
     profiles
  5. PowerShell profile redirector blocks -- both pwsh 7
     ($PROFILE.CurrentUserAllHosts) AND WindowsPowerShell 5.1
     (~\Documents\WindowsPowerShell\profile.ps1) -- marker-
     delimited block removal preserves any operator-added content
     outside the markers
  6. Fonts -- Geist*.otf/.ttf + Symbols-Only Nerd Font from
     %LOCALAPPDATA%\Microsoft\Windows\Fonts + matching HKCU font
     registry entries
  7. PATH env -- M:\MiOS\bin removed from HKCU + HKLM Path
  8. HKCU uninstall reg key
  9. Start Menu folder + Desktop .lnk shortcuts (MiOS, MiOS-DEV,
     MiOS Config, MiOS Help, Uninstall MiOS, plus stale legacy
     names from prior install revisions)
 10. AppUserModelID HKCU registrations
 11. podman-machine state symlinks (the symlinks to M:\podman from
     AppData\Local, .local\share, ProgramData\containers\podman\machine)
 12. MIOS_* environment variables (HKCU + HKLM scope)

Default preserves $MiosConfigDir (per-user identity / mios.toml
operator overrides) so a re-install picks up the operator's
last config. -Purge nukes that too for true zero-state uninstall.

Non-destructive: never touches /usr/share/mios, C:\mios-bootstrap (the
operator's source repos), the operator's own pwsh profile content
outside the >>> MiOS oh-my-posh init >>> markers, or any non-MiOS
WT profiles / schemes / fonts.

<!-- mios-src:69ae597e90dc from build-mios.ps1:10407-10451 -->

### Requires -Version 5.1

Requires -Version 5.1

<!-- mios-src:bc35b223480a from build-mios.ps1:10454-10454 -->

### Also nuke the MiOS\Linux Apps\ subfolder + every .lnk...

Also nuke the MiOS\Linux Apps\ subfolder + every .lnk inside it
(Files / Web / VSCodium / Flatseal / Extension Manager / Ptyxis /
System Monitor / Settings -- created by Install-WindowsBranding's
Linux Apps loop). "uninstaller STILL doesn't
uninstall everything from windows" -- previous build only removed
named .lnks, leaving Linux Apps\ orphaned in Start Menu.

<!-- mios-src:9c56e8f8b2c5 from build-mios.ps1:10681-10686 -->

### 16. FULL FORMAT M:\ partition ("FULLY format the M:\...

16. FULL FORMAT M:\ partition ("FULLY format
the M:\ partition only"). Only formats if M:\ exists AND is the
MiOS-DEV labeled partition we provisioned. NEVER touches any other
drive letter, never re-partitions, never creates/deletes drives.
Confirmation gated -- only fires when operator explicitly asked for
uninstall (not on -Quiet runs from a panicked irm|iex reap path).

<!-- mios-src:339bfb3ada4e from build-mios.ps1:10783-10788 -->

### ── Phase 9 -- Build (DEPRECATED)...

── Phase 9 -- Build (DEPRECATED) ─────────────────────────────────────────
Same self-replication enforcement applies: $BootstrapOnly is forced
to $true at line 202, so this Phase-9 invocation is unreachable from
the operator-facing flow. The build pipeline runs INSIDE MiOS-DEV
via /usr/libexec/mios/mios-build-driver; the `mios build` verb
(M:\MiOS\bin\mios-build.ps1) is the canonical operator trigger.
Kept here as dead code so git-blame still resolves legacy refs;
a follow-up commit will delete this branch outright.

<!-- mios-src:aa52bef65e72 from build-mios.ps1:10830-10837 -->

### Pass the operator-chosen model selection (Phase 6 prompt)...

Pass the operator-chosen model selection (Phase 6 prompt) through
to the build so 37-ollama-prep.sh bakes the right pair into
/usr/share/ollama/models. MIOS_AI_MODEL takes precedence over the
hardware-driven default in Get-Hardware.

<!-- mios-src:9ce01252db89 from build-mios.ps1:10839-10842 -->

### NOTE

NOTE: Rename-PodmanDevDistro now runs DURING bootstrap (after
Phase 5 + smoke test + Install-WindowsBranding) so the dev VM
is already named MiOS-DEV by the time the OCI build (Phase 9
above) completes. The build pipeline reaches the distro via
podman's API socket (SSH-forwarded) which is unaffected by
the WSL rename, OR via Invoke-DistroSh which probes both
names. No post-build rename is needed.

<!-- mios-src:ba5c29f5053b from build-mios.ps1:10850-10856 -->

### In BootstrapOnly mode, the hint banner at line ~6584...

In BootstrapOnly mode, the hint banner at line ~6584 already
printed the "Windows-side install complete" + verb hints.
Skip the second summary here -- printing it AGAIN duplicates
the operator-facing post-bootstrap UX. Per
feedback_mios_bootstrap_stops_at_dev_ready.

<!-- mios-src:d80490e92867 from build-mios.ps1:10880-10884 -->

### NO "Press Enter to close..." pause. The bootstrap finishes...

NO "Press Enter to close..." pause. The bootstrap finishes with
an automatic chain into the dev distro to run mios-build-driver
(the actual OCI build). Operator's terminal stays open in the
distro shell after the driver finishes; if they want the
bootstrap log they read $LogFile directly.

<!-- mios-src:1137b4ee955d from build-mios.ps1:10907-10911 -->

### The driver lives at M:\usr\libexec\mios\mios-build-driver...

The driver lives at M:\usr\libexec\mios\mios-build-driver
(Phase 2 cloned mios.git to M:\). WSL automounts every
Windows drive at /mnt/<letter>/, so the dev distro can
see it directly at /mnt/m/usr/libexec/mios/mios-build-driver --
no need to base64-stage the file via stdin (which had
its own dragons: PowerShell `|` corrupting binary stdin,
ProcessStartInfo.ArgumentList not existing in PS 5.1,
etc.). Just exec it from the mount.

Probe automount first so we surface a clear error if the
operator's WSL config has [automount].enabled=false. The
default machine-os config has automount on; this is a
belt-and-braces check.

<!-- mios-src:8bbb9304e5fd from build-mios.ps1:10937-10949 -->

### bootc-image-builder OCI to disk image converter NOTE: the...

bootc-image-builder  OCI to disk image converter
NOTE: the upstream bootc-image-builder repo merged into osbuild/image-builder
and is archived (read-only). The quay.io/centos-bootc/bootc-image-builder image
stays the canonical pull per the osbuild bootc docs; re-point depName only if
upstream relocates the published image.
renovate: datasource=docker depName=quay.io/centos-bootc/bootc-image-builder
bib_digest: sha256:<populated by Renovate>

<!-- mios-src:5f8fdaba2d57 from image-versions.yml:19-25 -->

### Requires -Version 7.1

>
Requires -Version 7.1

<!-- mios-src:eb77760c7a6e from mios-pipeline.ps1:128-129 -->

### ── Admin elevation (centralized)...

── Admin elevation (centralized) ────────────────────────────────────
Both build-mios.ps1 and install.ps1 historically self-elevated mid-
chain via Start-Process -Verb RunAs, then `return`-ed from the un-
elevated copy. That pattern silently breaks under any non-interactive
parent (CI, agent-driven runs, this orchestrator under a captured
stdout): the elevated copy spawns a UAC consent prompt the parent
can't see / accept, the un-elevated copy exits 0, and the pipeline
happily marches forward against an empty deployment.

Lift the check to here and elevate the WHOLE chain once, passing
every arg + relevant env var through. build-mios.ps1 and install.ps1
detect MIOS_PIPELINE_ELEVATED=1 and skip their own self-elevation,
so the chain runs in one elevated process from start to finish.

<!-- mios-src:b77a676aae79 from mios-pipeline.ps1:150-162 -->

### ── Unified global flattened log file...

── Unified global flattened log file ────────────────────────────────
Single log file per pipeline invocation, captured at the orchestrator
level (not per-phase) so that every line of every legacy worker
(build-mios.ps1, install.ps1, Get-MiOS.ps1, ...) and every native
command they shell out to (wsl.exe, podman, bib, ...) lands in one
flat chronologically-interleaved file at a stable, predictable path.

  M:\MiOS\logs\mios-install-YYYYMMDD-HHMMSS.log    per-invocation
  M:\MiOS\logs\latest.log                          copy of most recent

(The exact drive depends on $PSScriptRoot; on a typical Windows host
after Phase-2 migration this resolves to M:\MiOS\logs\, which the
build dashboard already advertises as the canonical log location.)

Transcript captures Write-Host / Write-Output / Write-Error / Verbose
/ Warning + native-command stdout that the orchestrator dispatches
via `&`, so this single file is everything the operator needs to
diagnose a failed run -- no scattered phase logs.

<!-- mios-src:e611f8c8f9b3 from mios-pipeline.ps1:206-223 -->

### ── Phase function bodies...

── Phase function bodies ────────────────────────────────────────────
Each phase is a thin dispatcher to existing automation.

IMPLEMENTATION NOTE -- TODAY'S COUPLING vs FUTURE STATE
build-mios.ps1 today is monolithic: a single invocation runs Phases
1-8 internally (questions -> stage -> dev-distro -> overlay -> account
-> install -> smoketest -> build). The phase functions for those IDs
all delegate to the same script; running `--phase 4` invokes
build-mios.ps1 in full because no per-phase entry exists yet. This
is acknowledged in the chain documentation above and will be split
as the legacy script is decomposed. Phases 9-11 are independently
dispatchable today -- they correspond to install.ps1 + boot helpers.

<!-- mios-src:f65ffa437a6e from mios-pipeline.ps1:277-288 -->

### Resolve image ref into (registry, repo, ref). Only ghcr.io...

Resolve image ref into (registry, repo, ref). Only ghcr.io is supported
directly; other registries fall through to a clear error so the operator
knows to use mios-cloud-build.ps1 + a podman pull instead.

<!-- mios-src:ff25edc44f14 from mios-windows-export.ps1:139-141 -->

### The scaffold needs admin (Hyper-V cmdlets gate on...

The scaffold needs admin (Hyper-V cmdlets gate on RunAsAdmin), so we
generate it for the operator to review + launch elevated themselves
rather than auto-elevating from here. Operators get to see the New-VM
parameters before committing.

<!-- mios-src:de9e7196d03d from mios-windows-export.ps1:350-353 -->

### Anonymous bearer for the public-read pull. Even private...

Anonymous bearer for the public-read pull. Even private repos that the
operator has access to via gh auth would work if you swap this for a
PAT-derived token -- left out of scope for the public-image use case.

<!-- mios-src:fd5d006cadcb from mios-windows-export.ps1:385-387 -->

### AI-hint

AI-hint: Primary entry point for MiOS installation; handles admin elevation, environment validation, and fresh-clone of the bootstrap repo to initiate the preflight, VM setup, and OCI build pipeline.
AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, /etc/mios/., /usr/share/mios/branding/mios.txt, /usr/share/mios/branding/mios, mios-dev, mios-bootstrap, mios-pull, mios-launch, mios-install
AI-functions: Disable-ConsoleQuickEdit, Resolve-MiosTomlText, Get-MiosTomlValue, Show-MiOSBanner, Show-MiOSAgreement, Invoke-MiOSAgreementGate, _Center-MiOSGateConsole, Get-MiosPalette, _hex, Test-MiOSFontInstalled, Wait-MiOSWindowsTerminalReady, Ensure-MiOSWinget

<!-- mios-src:6cb747722e65 from Get-MiOS.ps1:1-3 -->

### AI-hint

AI-hint: PowerShell entry point for MiOS installation that configures the MiOS-DEV podman-machine, handles initial licensing, and manages the SSH handoff to the Linux-side build driver for generating OCI images and disk formats.
AI-related: 37-ollama-prep.sh, mios-btop.sh, /usr/libexec/mios/mios-build-driver, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-build-driver., /etc/mios/mios.toml, /usr/share/mios/configurator/mios.html, /usr/libexec/mios/flatpak-launch, /etc/mios/hermes/config.yaml, /etc/mios/hermes/config.local.yaml
AI-functions: parse_sections_from_toml, get_pkgs, install_section, parse_pkgs, Disable-ConsoleQuickEdit, Resolve-MiosTomlText, Get-MiosTomlValue, Resolve-MiosInstallRoot, Update-MiosInstallPaths, Invoke-MigrateLegacyInstallRoot, Invoke-DataDiskBootstrap, Test-DashboardCanRedraw
Requires -Version 5.1
'MiOS' Unified Installer & Builder -- Windows 11 / PowerShell

  irm https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/install.ps1 | iex

Flags:
  -BuildOnly    Pull latest + build only (skip first-time setup)
  -Unattended   Accept all defaults, no prompts

── ARCHITECTURE: Day-0 self-replication contract ────────────────────────────
Per the MiOS self-replication architecture (project memory:
project_mios_self_replication_vision.md), the Windows side of the bootstrap
is STRICTLY an entry point with a narrow scope:

  1. Acknowledgements (AGREEMENTS.md / LICENSES.md)
  2. MiOS-DEV podman-machine setup (Phases 0-5 + 8 of this script)
  3. SSH handoff into MiOS-DEV

After step 3, EVERYTHING else runs INSIDE MiOS-DEV: local fetch + overlay,
identity prompts, and the FULL build pipeline producing every output
format MiOS targets (OCI bootc image, WSL2/g .tar/.vhdx, Hyper-V .vhdx,
QEMU qcow2, Live-CD/USB ISO, USB installer, RAW dd image). The build
dashboard renders on the MiOS-DEV tty inside the SSH-hosted Windows
Terminal -- it is NOT streamed back across the WSL/Windows boundary.

Show-PostBootstrapMenu's "Continue to build" choice IS the SSH handoff:
it spawns a new Windows Terminal tab running `wsl.exe -d MiOS-DEV` which
in turn invokes /usr/libexec/mios/mios-build-driver inside the dev distro.

Migration status : Phase 6+ legacy code (identity, OCI build,
disk image generation, Hyper-V VM deploy) still lives in this script as
the -FullBuild / -BuildOnly path. The new SSH-handoff flow runs alongside
it via the menu. Subsequent migration chunks move identity prompts and
the full output-format matrix into the Linux-side driver, then trim this
Windows-side tail entirely.

<!-- mios-src:0acbcca01ab2 from build-mios.ps1:1-38 -->

### Invoke-Expression runs this body inside its caller's...

Invoke-Expression runs this body inside its caller's PowerShell process.
This entrypoint uses `exit` for explicit pipeline exit codes, so executing it
directly with `irm ... | iex` would close the operator's terminal. Re-run the
fetched entrypoint as a script file in a child process; exit then returns to
the caller's prompt. The environment guard prevents the child's cache-busted
in-process refresh from spawning another child.

<!-- mios-src:c0df16f5dc7a from Get-MiOS.ps1:66-71 -->

### The monitor must use the profile named by the operator...

The monitor must use the profile named by the operator SSOT. Delay launch
until the TOML resolver exists so the WT profile is not inferred from a
stale global default.

<!-- mios-src:0a430550eb7e from Get-MiOS.ps1:583-585 -->

### Read existing settings.json -- preserve operator globals...

Read existing settings.json -- preserve operator globals except the
system default profile, which MiOS intentionally owns per bootstrap
contract. Other global settings and keybindings remain untouched.
WT writes JSONC; ConvertFrom-Json on PS5.1 chokes on it, so strip
comments + trailing commas before parsing.

<!-- mios-src:720d88370c85 from Get-MiOS.ps1:1758-1762 -->
