<!-- AI-hint: Manual pages distilled from the source comments of mios, sanitized, each passage anchored to the comment it came from. -->

# mios

### Examples for other local OpenAI-API-compatible runtimes...

Examples for other local OpenAI-API-compatible runtimes:
  LLM-Light:          base_url = "http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1"
  vLLM:               base_url = "http://localhost:8000/v1"
  LM Studio:          base_url = "http://localhost:1234/v1"
  mios-gateway-agent: base_url = "http://localhost:${MIOS_PORTS_HERMES}/v1"
  LiteLLM proxy:      base_url = "http://localhost:4000/v1"

<!-- mios-src:7036a2325287 from etc/mios/kb.conf.toml:11-16 -->

### Comment lexer + classifier for the generative documentation...

Comment lexer + classifier for the generative documentation system.

Spec: docs/design/doc-generative-documentation.md sections 1.2 and 2.

Two jobs, kept apart on purpose:

  lex(path)      -> the comment blocks in a file, with enough context
                    (attachment, anchor code, hashes) to place and track them.
  classify(b, ..) -> exactly one verdict per block, from an ordered first-match
                    rule set, so every decision is explainable by one rule id.

The classifier holds NO thresholds of its own. Every number arrives in a
`Policy` built from mios.toml `[docs]`, because a rule change must be an
operator edit to SSOT rather than a code edit (Law 7 NO-HARDCODE, Law 8
SSOT-PROJECTION).

Taggability and comment syntax are NOT redefined here. They are loaded from
usr/libexec/mios/mios-ai-tag through the same SourceFileLoader shim
mios-ai-hint-coverage uses, so "which files carry documentation" has exactly one
definition across all consumers.

<!-- mios-src:f6c3310f2a36 from usr/lib/mios/mios_comments.py:5-25 -->

### The files the census covers

The files the census covers: GIT-TRACKED only, sorted.

    Walking the filesystem instead made the count depend on whatever untracked
    or ignored files a particular machine happened to have -- vendored trees,
    scratch dirs, staging dumps. The number then differed between a contributor
    box and CI, which silently loosened the ratchet ceiling in CI to the point
    that its negative test could not breach it. Tracked files are the same set
    everywhere.

<!-- mios-src:4f4415e5d651 from usr/lib/mios/mios_comments.py:49-57 -->

### Import usr/libexec/mios/mios-ai-tag (no .py suffix) as a...

Import usr/libexec/mios/mios-ai-tag (no .py suffix) as a module.

    Same SourceFileLoader approach mios-ai-hint-coverage already uses. Returns
    None when it cannot be found, so callers can degrade rather than crash.

<!-- mios-src:28a9e30776f6 from usr/lib/mios/mios_comments.py:85-89 -->

### Every name a comment could legitimately reference. A...

Every name a comment could legitimately reference.

    A reference counts as dangling only when it is absent from here AND absent
    from the tree AND not allowlisted. Without this filter the staleness rule
    drowns in false positives -- the survey needed it to get from 353 raw hits
    down to ~70 real ones.

<!-- mios-src:6fc7fcbe217c from usr/lib/mios/mios_comments.py:212-218 -->

### Python uses tokenize + ast, never regex. Regex miscounts...

Python uses tokenize + ast, never regex.

    Regex miscounts multi-line data strings as prose -- the survey proved it on
    the AI-plane files, where a system-prompt literal reads exactly like a
    narrative comment block.

<!-- mios-src:1fe018524038 from usr/lib/mios/mios_comments.py:331-336 -->

### R4 BANNER DELIBERATE NARROWING of the spec's second clause....

R4 BANNER

DELIBERATE NARROWING of the spec's second clause. As written it is
"<= 8 words AND no sentence-final punctuation AND not WHY -> DROP", which
also swallows ordinary short comments ("bump the retry count",
"guard against zero") -- and DROP is a class that `prune` may delete.
Information safety is absolute here, so a short block must additionally
LOOK like a label -- ALL-CAPS, Title Case, or a trailing colon -- before it
can be treated as a divider. Pure divider runs are unaffected.

<!-- mios-src:f3e774fcaabb from usr/lib/mios/mios_comments.py:532-540 -->

### (vendor, vendor_d, host, host_d, user, user_d) resolved...

(vendor, vendor_d, host, host_d, user, user_d) resolved from the env at
    CALL time. Vendor FRAGMENTS live in /usr/lib/mios/mios.d (Law 1 USR-OVER-ETC
    + systemd's /usr/lib vendor convention), NOT beside the /usr/share monolith;
    admin/user fragments sit in a mios.d/ beside their monolith.

<!-- mios-src:69fb3387c633 from usr/lib/mios/mios_toml.py:52-55 -->

### The overlay layer paths, lowest precedence first, EXPANDED...

The overlay layer paths, lowest precedence first, EXPANDED to include
    drop-in fragments. Resolved from the environment at CALL time (not import
    time) so a caller / test / CI on a non-FHS host can retarget a layer via
    MIOS_VENDOR_TOML / MIOS_HOST_TOML / MIOS_USER_TOML / MIOS_TOML_ROOT (and the
    *_TOML_D fragment-dir overrides) AFTER this module is imported.

    Ordering is TIER-MAJOR (vendor < host < user); within each tier the monolith
    seeds LOWEST, then that tier's mios.d/*.toml fragments (lexical basename)
    deep-merge over it. Tier is the primary precedence key -- a vendor fragment
    can NEVER outrank a higher tier (the XDG/git-config scope model, not
    systemd's global flat sort). NO-OP when no mios.d/ exists: every _frags()
    glob is empty and this returns exactly [vendor, host, user] as before.

<!-- mios-src:982133a0fda5 from usr/lib/mios/mios_toml.py:69-80 -->

### Allocate every [ports] value from the [ports.categories]...

Allocate every [ports] value from the [ports.categories] schema, IN PLACE.

    This runs AFTER layer merging, so it is the live runtime allocator: a
    factory/OEM default in the vendor mios.toml, an operator override in
    /etc/mios/mios.toml, or a user override in ~/.config/mios/mios.toml all feed
    the same derivation and the result is what every consumer sees -- userenv.sh
    exports, /etc/mios/install.env, the Quadlet render, the firewall phases and
    the Containerfile build args.

    A member's port is  base + index_in_members * stride.  Because `members` is
    ordered, adding or removing a service reallocates the category with no hand
    edit and no chance of a collision. `pinned` entries are protocol contracts
    (DNS/53) and are emitted verbatim.

    The flat [ports] table in the vendor file is a rendered projection kept for
    readability and drift-gating; the derivation OVERRIDES it, so an operator who
    retargets a category base is never silently beaten by a stale vendor literal.

<!-- mios-src:d55cbc3df935 from usr/lib/mios/mios_toml.py:136-153 -->

### Allocate ports from [ports.categories] AFTER every layer...

Allocate ports from [ports.categories] AFTER every layer (and the DB
overlay) has merged, so operator/user overrides of a category base or
member list re-derive live instead of losing to the vendor flat table.

<!-- mios-src:4179b26ee527 from usr/lib/mios/mios_toml.py:209-211 -->

### Fixtures for mios_comments. Every classifier rule gets at...

Fixtures for mios_comments.

Every classifier rule gets at least one fixture that asserts the exact
(cls, reason) pair. A rule with no fixture is a rule nobody has proven fires --
this repo has a documented history of checks that could not fail, so the bar
here is that each rule is demonstrated, not merely written.

Runs standalone (python3 test_mios_comments.py) so it needs no pytest in the
bake image.

<!-- mios-src:54f42dee754a from usr/lib/mios/test_mios_comments.py:5-14 -->

### Folder default = "MiOS" -- NOT the distro name. Earlier...

Folder default = "MiOS" -- NOT the distro name. Earlier versions
wrote to %APPDATA%\...\Programs\<distro>\ to match Microsoft's
native WSL2 Start Menu sync, but Microsoft's wslservice TREATS
that folder AS ITS OWN: every WSL distro restart it re-enumerates
apps from the distro side using a much more restrictive filter
(NoDisplay + Terminal filtering plus its own ad-hoc rules), then
DELETES every .lnk in the folder that doesn't match. Result:
operator goes from 46 properly-iconed apps to 3 after `wsl
-shutdown`. Operator-flagged "no apps on windows
again!!!" -- the second time the WSL stomp wiped the shortcuts.

Writing to a distinct folder lets MS manage its 3-app distro
folder and lets MiOS own its 46-app folder side-by-side. They
appear next to each other in Start Menu.

<!-- mios-src:614c5fa292af from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:37-50 -->

### Unknown -- still copy; .lnk IconLocation tolerates PNG even...

Unknown -- still copy; .lnk IconLocation tolerates PNG even
at non-canonical extension. .NET's PNG->ICO converter
below will reject if truly garbage; we degrade gracefully.

<!-- mios-src:fd095c7a4419 from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:187-189 -->

### PNG -> ICO converter that EMBEDS the PNG bytes inside an...

PNG -> ICO converter that EMBEDS the PNG bytes inside an ICO
container. This is the Vista+ "PNG-encoded ICO" format Windows
Start Menu renders cleanly at every size from 16x16 to 256x256.

Why NOT .NET's Icon.Save() / Bitmap.GetHicon: those produce
single-image .ico files in 32-bit BMP format with a 32x32 source,
which Windows upscales badly at 256x256. The resulting Start Menu
tile shows either a generic icon or a blurry mess. Operator-flagged
twice: "no icons match", "NEVER saw native icons -- NOT
even ONCE". A PNG-embedded ICO matches what flatpak / Microsoft
Store / WSL's own sync produce.

Format (Vista+ PNG ICO):
  ICONDIR     (6 bytes): 00 00 | 01 00 | 01 00
  ICONDIRENTRY (16 bytes per image):
    bWidth(1)   bHeight(1)  bColorCount(1)  bReserved(1)
    wPlanes(2)  wBitCount(2)  dwBytesInRes(4)  dwImageOffset(4)
  Image data: raw PNG bytes (NOT XOR/AND masks like classic ICO)

<!-- mios-src:ddea11fea6f3 from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:224-241 -->

### Force the Shell to re-read .lnk IconLocation values WITHOUT...

Force the Shell to re-read .lnk IconLocation values WITHOUT
nuking the icon cache database -- previous version called
`ie4uinit.exe -ClearIconCache` which dropped every Start Menu
icon to blank until Explorer was manually restarted. Operator-
flagged "the icons disappeared now!!!".

Lighter approach: touch every .lnk mtime + the SHChangeNotify
broadcast. The Shell watches .lnk mtimes for change; touching
invalidates the per-shortcut icon cache entry without affecting
other Start Menu items.

<!-- mios-src:957c6391295e from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:288-297 -->

### Sweep ALL .lnk files in our managed folder so renamed /...

Sweep ALL .lnk files in our managed folder so renamed / removed apps
don't leave orphan shortcuts. Also clear the legacy `<distro>` folder
(where shortcuts USED to land before Microsoft's wslservice started
stomping it on every distro restart) -- if it still has our
leftover .lnks, they'll appear in Start Menu as duplicates next to
the new MiOS Apps folder.

<!-- mios-src:3de75bb63b2a from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:314-319 -->

### NATIVE WSL filename pattern

NATIVE WSL filename pattern: "<App Display Name> (<distro>).lnk".
Matches the exact convention WSL's built-in sync uses, so the
operator sees one consistent set of shortcuts (ours + WSL's
native sync share the same filenames -> de-dup at write).

<!-- mios-src:bde7cfdb0d72 from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:368-371 -->

### NATIVE Microsoft WSL pattern (reverse-engineered from a...

NATIVE Microsoft WSL pattern (reverse-engineered from a WSL-
generated shortcut on Win11 26H1):
  TargetPath       = C:\Program Files\WSL\wslg.exe  (GUI, no console)
  Arguments        = -d <distro> --cd "~" -- <exec line>
  WorkingDirectory = C:\WINDOWS\system32
  WindowStyle      = 7   (Minimized -- no flash; wslg handles UI)
  IconLocation     = <path-to-ico>,0

<!-- mios-src:82739bd24264 from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:376-382 -->

### ─── "MiOS Full Desktop" Enhanced Session shortcut...

─── "MiOS Full Desktop" Enhanced Session shortcut ───────────────────
Alternate launch path that opens the full GNOME desktop via mstsc.exe
connecting to the xrdp service in the dev VM. Set up by automation/
35-xrdp-enhanced-session.sh at install time. Lives alongside the
per-window app shortcuts in the same MiOS Apps folder so the operator
can pick per session (per-window for native-Windows-window feel,
Full Desktop for libadwaita-uniform rendering + Bibata cursor).
Operator directive "Full Enhanced Session is an alternate
launch option installed at irm|iex invoke and installation".

<!-- mios-src:ef9ce2ef6f33 from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:395-403 -->

### Nested GNOME approach

Nested GNOME approach: WSLg launches /usr/bin/mios-full-desktop in
the distro, which exec's gnome-session inside `gnome-shell --nested`.
Mutter runs as a Wayland CLIENT of WSLg's Weston, hosting the entire
GNOME desktop in one window. All cursor + theme + decoration rendering
happens INSIDE that nested compositor -- bypasses every WSLg-per-window
rendering limit at once (Bibata + rounded corners + libadwaita-uniform
everything just work because Mutter draws final pixels itself).

<!-- mios-src:9a6c3ef60a2c from usr/libexec/mios/Update-MiOSStartMenuShortcuts.ps1:419-425 -->

### mios_tools -- the in-sandbox Code Mode tool API (WS-2)....

mios_tools -- the in-sandbox Code Mode tool API (WS-2).

This module is the LOCAL Python API the model's generated code imports INSIDE the
coderun-sandbox. It is the whole point of Code Mode: instead of loading ~71
OpenAI function schemas into the model's context every turn, the model writes
ordinary Python that calls e.g.

    import mios_tools
    hits = mios_tools.web_search("local FOSS LLM serving 2026")
    print(mios_tools.json({"top": hits[:3]}))      # final line = filtered result

and only the FILTERED result returns to the model -- the big token win.

How a tool call leaves the jail
-------------------------------
The sandbox is Network=none + DropCapability=ALL, so the ONLY egress is the unix
socket the Quadlet already bind-mounts at /run/coderun.sock (see
mios-coderun-sandbox@.container). This shim sends a single newline-delimited JSON
request -- {"verb": "<name>", "args": {...}} -- over that socket and reads one
JSON response line back. The HOST side (the agent-pipe's Code Mode broker proxy)
listens on that socket, runs the verb through dispatch_mios_verb (so the broker's
permission / taint-firewall / dedup / HITL gates STILL apply per verb), and
writes the result back. There is NO direct verb execution inside the jail --
every call is mediated + policy-checked on the host.

Deploy: this file is mounted into the sandbox (read-only) as
/usr/local/lib/mios/mios_tools.py and put on PYTHONPATH so `import mios_tools`
resolves. Pure stdlib (socket + json) so it has no in-sandbox deps.

<!-- mios-src:c6e5226f446e from usr/libexec/mios/mios-codemode-api.py:4-32 -->

### Named window-snap region geometry (pure, no side effects)....

Named window-snap region geometry (pure, no side effects).

The rectangle math is intentionally free of hardcoded pixel constants: half /
quarter fills are computed from the LIVE work-area width and height, and the
right/bottom halves take the exact remainder so a pair of halves tiles the work
area with no gap or overlap on odd dimensions.

<!-- mios-src:b4e108c80621 from usr/libexec/mios/mios_window_region.py:5-11 -->

### Compute the ABSOLUTE (x, y, w, h) for ``region`` from a...

Compute the ABSOLUTE (x, y, w, h) for ``region`` from a screen-layout dict.

    ``layout`` matches the OS-control executor's /screen-layout contract:
    ``{"screens": [{"work": {"x", "y", "width", "height"}}, ...]}``. The chosen
    monitor's work-area origin is added to the relative rectangle. Returns None
    on an unknown region or an out-of-range / malformed monitor entry.

<!-- mios-src:476acf14a8a6 from usr/libexec/mios/mios_window_region.py:64-70 -->

### Standalone unit test for mios-docgen (WS-4 P0 doc-gen)....

Standalone unit test for mios-docgen (WS-4 P0 doc-gen).

Pure stdlib; imports the CLI module by path (it has no .py extension, matching
the libexec convention) and exercises the DB/binary-free logic: format
resolution, the master gate, degrade-open emission, and the routing decision
table. The two backend converters (Pandoc / LibreOffice) are NOT invoked --
that needs the binaries + a graphical-free office runtime and is covered by the
operator's live check; here we prove the pure decision layer.

Mirrors the test_mios_sched.py / test_mios_evict.py pattern: explicit asserts,
PASS/FAIL summary, non-zero exit on any failure.

Run:  python test_mios_docgen.py

<!-- mios-src:891a56504503 from usr/libexec/mios/test_mios_docgen.py:4-17 -->

### Tests for the mios-find ranker SSOT (mios.toml...

Tests for the mios-find ranker SSOT (mios.toml [mios-find.ranker] +
[mios-find.category_priority]).

The ranker lives in an embedded python heredoc inside the bash script
``mios-find``. We extract that block, stub the ``mios-apps --json`` inventory
call, point ``MIOS_TOML`` at a temp config, exec it in-process, and assert the
chosen launch command. Defaults must reproduce the historical in-code ranking;
a non-default config must change it -- proving the weights are read from SSOT,
not baked.

<!-- mios-src:9a3a61c4416a from usr/libexec/mios/test_mios_find_ranker.py:4-13 -->

### Quadlet sidecar enablement. Defaults policy (project-wide...

Quadlet sidecar enablement.

Defaults policy (project-wide invariant): every flag here defaults to
true. The system never disables a service via static config -- when a
service is incompatible with the host (wrong virtualization layer,
missing required path, missing hardware), systemd `Condition*`
directives in the Quadlet itself short-circuit it at boot/pre-boot
and the service silently no-ops. Operators can still override any
flag in /etc/mios/profile.toml or ~/.config/mios/profile.toml to
force-disable a service even when it would otherwise run.

<!-- mios-src:81a26ecb9a9f from usr/share/mios/profile.toml:81-90 -->

### FORCE-DISABLED (explicit exception to the defaults-true...

FORCE-DISABLED (explicit exception to the defaults-true policy):
this sidecar's docker_start.sh corrupts the SHARED host /var/lib/crowdsec
(dangling /staging symlinks + `localhost` machine re-register) and needs the
online hub at boot (incompatible with offline MiOS) -- it crash-looped the
host crowdsec agent for ~6 days. Retired; the host agent provides the IPS.
Full rationale in mios.toml [quadlets.enable] + memory mios_crowdsec_recovery.

<!-- mios-src:31fd4e869038 from usr/share/mios/profile.toml:98-103 -->
### Names the SSOT defines

Names the SSOT defines: every MIOS_* key, every unit it declares.

        A comment naming MIOS_AI_ENDPOINT is referencing a key that exists --
        in mios.toml, not as a file. Without this the staleness rule reported
        every env var in the tree as a dangling reference, which is why its
        count was noise rather than a signal.

<!-- mios-src:4ed8af1db3c0 from usr/lib/mios/mios_comments.py:271-277 -->

### The allowlist holds literal reference tokens and globs --...

The allowlist holds literal reference tokens and globs -- paths like
'C:\mios-bootstrap\Get-MiOS.ps1', bare tokens like 'ollama' or
'8080', and globs like 'blade-*.conf'. It was matched with re.search,
under which the Windows paths are invalid patterns (bad escape \m)
and 'blade-*.conf' silently matches nothing it was meant to cover.
Match them as what they are written as.

<!-- mios-src:be7e0f6ee742 from usr/lib/mios/mios_comments.py:352-357 -->

### Characters of AI-hint prose, continuation lines included....

Characters of AI-hint prose, continuation lines included.

    Counting only the line that starts with `AI-hint:` would mean a hint could
    clear the cap by being wrapped across several `#` lines -- the gate would
    then be measuring line length, which nobody cares about, instead of how much
    prose sits in the header, which is the thing being ratcheted down.

<!-- mios-src:2c1e4d0345bf from usr/lib/mios/mios_comments.py:380-386 -->

### hint_max_chars caps the AI-hint PROSE, not the whole header...

hint_max_chars caps the AI-hint PROSE, not the whole header block.
AI-related/AI-functions/AI-doc are machine-maintained: their length
tracks how many files a module touches and how many functions it
defines, neither of which is a writing-quality signal. Counting them
made the ceiling unreachable -- check-fleet-safety.py carries 357
characters of generated metadata, so it breached a 260 cap even with
an empty hint, and no amount of editing could clear the gate.

<!-- mios-src:91b1f37a187d from usr/lib/mios/mios_comments.py:681-687 -->

### reconcile-blade.py -- Per-class database reconciliation for...

reconcile-blade.py -- Per-class database reconciliation for multi-blade partition rejoin.

Merge rules (ADR-0017 D5):
  1. union-by-hash   (knowledge, embeddings): merge rows by unique hash/id key;
  2. append-ordered  (agent_memory, event): merge rows ordered by logical_ts;
  3. last-writer-wins(session, scratch): pick row with highest logical_ts per primary key;
  4. conflict-is-error(config_kv): raise explicit conflict if key values diverge.

<!-- mios-src:1f760e9abf57 from usr/libexec/mios/reconcile-blade.py:5-12 -->

### Standalone unit test for mios-docgen (WS-4 P0 doc-gen)....

Standalone unit test for mios-docgen (WS-4 P0 doc-gen).

Pure stdlib; imports the CLI module by path (it has no .py extension, matching
the libexec convention) and exercises the DB/binary-free logic: format
resolution, the master gate, degrade-open emission, and the routing decision
table. The two backend converters (Pandoc / LibreOffice) are NOT invoked --
that needs the binaries + a graphical-free office runtime and is covered by the
operator's live check; here we prove the pure decision layer.

Mirrors the test_mios_sched.py / test_mios_evict.py pattern: explicit asserts,
PASS/FAIL summary, non-zero exit on any failure.

Run:  python test_mios_office_convert.py

<!-- mios-src:714187361e14 from usr/libexec/mios/test_mios_office_convert.py:3-16 -->
### Reference resolution -- owned by doc_refs.rs in Rust (Law...

--------------------------------------------------------------------------
Reference resolution -- owned by doc_refs.rs in Rust (Law 14 / task_3395)
--------------------------------------------------------------------------

<!-- mios-src:34013fc6aaec from usr/lib/mios/mios_comments.py:188-190 -->

### Stale-reference resolution is owned by doc_refs.rs in Rust...

Stale-reference resolution is owned by doc_refs.rs in Rust (Law 14 / task_3395).

    Python mios_comments remains responsible only for comment lexing and classification.

<!-- mios-src:360895826a3d from usr/lib/mios/mios_comments.py:192-195 -->

### A list of scalars stays comma-joined and unquoted, which is...

A list of scalars stays comma-joined and unquoted, which is what every
consumer of a MIOS_*_LIST expects. A list of TABLES or of lists is
rendered as TOML inline syntax, matching mios-resolver.

str(dict) gives a PYTHON REPR -- {'ordinal': '01', 'fatal': True} --
which only Python can parse and which disagreed with the Rust
resolver's TOML inline tables on 12 keys. That was the whole residual
of check_resolver_differential_parity, sitting exactly at its ceiling
of 12 with no headroom (task_3361). TOML wins because it is a real
format and because Rust is the destination tier.

<!-- mios-src:ba4386c9db23 from usr/lib/mios/mios_toml.py:697-706 -->

### Render a value as TOML inline syntax, byte-compatible with...

Render a value as TOML inline syntax, byte-compatible with the toml crate.

    Key order inside a table is alphabetical among scalars and arrays, with
    nested TABLES emitted last -- that is what the Rust serializer does, and the
    comparison is byte-for-byte, so the order is part of the contract.

<!-- mios-src:d504ff19350f from usr/lib/mios/mios_toml.py:714-719 -->

### image.sidecars.* emits BOTH MIOS_<X>_IMAGE and...

image.sidecars.* emits BOTH MIOS_<X>_IMAGE and MIOS_<X>_VERSION
from one key (get_aliases / aliases.rs), so without this split the
two names carry an identical value and the tree gains 23 new
duplicate-value groups -- exactly what value-dup-baseline.tsv says
to collapse rather than record. Dropping the split here was tried
and reverted for that reason.

It is still the ONLY one of three emitters that splits, which is
task_3363's real finding: the fix is to stop emitting the redundant
_VERSION alias in both twins, not to change how it is rendered.
That removes 23 keys and needs its own change.

<!-- mios-src:59009b8b75b9 from usr/lib/mios/mios_toml.py:787-797 -->

### Resolve ${MIOS_*} that one emitted value makes to another....

Resolve ${MIOS_*} that one emitted value makes to another.

    Twin of resolve_cross_references in tools/native/mios-resolver/src/emit.rs
    (Law 13), and public because both the resolver and the drift gate call it.

    Apply it where the consumer CANNOT expand: systemd EnvironmentFile= and
    podman --env-file read a value literally, so an emitted
    MIOS_AI_ENDPOINT=http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1 means one thing
    to bash and another to them. It is also why system-sync-env.sh DROPPED that
    variable rather than emitting it: its filter rejects any value containing
    `$` (task_997).

    Do NOT apply it to the export map that renders automation/lib/globals.{sh,ps1}.
    Those are sourced by bash and PowerShell, which expand at load time, and the
    live reference is the feature: exporting MIOS_PORTS_AGENT_PIPE before sourcing
    propagates into MIOS_AI_ENDPOINT. render-globals.build_exports() therefore
    returns the unexpanded map.

    Reads a snapshot so the result does not depend on dict order, leaves `$$`
    alone because systemd owns it, and leaves an unresolvable name verbatim
    rather than blanking it so a caller can report it.

<!-- mios-src:6d3281e8d9cf from usr/lib/mios/mios_toml.py:819-840 -->

### Law 12

Law 12: degrade open. set -euo pipefail is active, so without the guard
a Forgejo admin API that is not up yet fails the pipeline, fails the
assignment, and aborts firstboot on an egress failure. The empty-token
path below already handles it, and the repo-create call above uses the
same idiom (|| echo "000").

<!-- mios-src:32a8890197a3 from usr/libexec/mios/forge-firstboot.sh:147-151 -->

### mios-a2a-delegate -- mid-run agent-to-agent delegation over...

mios-a2a-delegate -- mid-run agent-to-agent delegation over A2A.

P2.2 (operator 2026-05-27 "collaborate not fan-out"): any in-flight
agent (Hermes, opencode, daemon-agent, ...) can hand a sub-task to a
registered A2A peer mid-tool-loop and inject the peer's answer back
into its own reasoning -- the substrate the audit identified as
missing ("scratchpad becomes a live bus, not read-only-after").

Internally this is a thin HTTP shim over the agent-pipe's
/v1/a2a/dispatch endpoint (which itself POSTs JSON-RPC
`message/send` to the chosen peer's /a2a surface).  Keeping the verb
as a normal broker-routed shim (rather than an in-process Python
import) preserves: (a) the dedup / single-flight guard in
dispatch_mios_verb, (b) the same audit + permission posture every
other verb gets, (c) compatibility with the [verbs.*] catalog the
planner already sees.

USAGE
  mios-a2a-delegate --peer <peer_id> --text <prompt>
  mios-a2a-delegate --skill <skill_name> --text <prompt>
  mios-a2a-delegate --peer <peer_id> --text <prompt> --context-id <ctxid>
  mios-a2a-delegate --list-peers
  mios-a2a-delegate --list-skills

OUTPUT (always JSON):
  {ok, peer_id, status, response, task_id, context_id}
  on failure: {ok:false, error}

<!-- mios-src:04ddc6e9212b from usr/libexec/mios/mios-a2a-delegate:3-30 -->

### mios-a2a-discover -- SSOT-driven A2A peer discovery (no...

mios-a2a-discover -- SSOT-driven A2A peer discovery (no hardcoded endpoints).

Keeps the runtime peer list (/etc/mios/ai/v1/a2a-peers.json -- the file the
agent-pipe reads to know who it can delegate to) in sync with the fleet that is
actually LIVE. Candidates come from the SSOT, never from code literals:

  * always the local self (so single-node delegation still round-trips), and
  * every URL declared in mios.toml `[a2a].nodes` (the operator lists their
    fleet once), and
  * optionally a CIDR sweep when `[a2a].discover_cidr` is set (auto-find nodes
    on a LAN segment without naming each one).

Each candidate is probed for a valid MiOS A2A AgentCard; only responders are
written. Run on a timer / firstboot so node-spanning tracks the real fleet.
Every MiOS node ships the A2A server, so a live card == a delegable peer.

<!-- mios-src:1cbf80d8ecfc from usr/libexec/mios/mios-a2a-discover:4-19 -->

### Agent-pipe port from SSOT (env MIOS_A2A_PORT /...

Agent-pipe port from SSOT (env MIOS_A2A_PORT / MIOS_PORTS_AGENT_PIPE override,
    else [ports].agent_pipe). No literal default -- empty means we cannot honestly
    build the loopback self-URL, so the self-peer is simply skipped (degrade-open).

<!-- mios-src:f6ce806afd0e from usr/libexec/mios/mios-a2a-discover:45-47 -->

### Load the sibling mios-a2a-mdns module by path (hyphenated...

Load the sibling mios-a2a-mdns module by path (hyphenated, extensionless
    filename) and return its mDNS browse candidates. Fully degrade-open: missing
    module / avahi absent / [a2a].mdns_discovery off -> []. Each candidate is
    card-probed below exactly like an explicit node, so a non-MiOS responder on the
    wire is never trusted blindly.

<!-- mios-src:4ee11a9de4f4 from usr/libexec/mios/mios-a2a-discover:56-60 -->

### mios-a2a-mdns -- avahi/mDNS advertise + browse for A2A peer...

mios-a2a-mdns -- avahi/mDNS advertise + browse for A2A peer discovery (FED-G5).

Two SSOT-gated, degrade-open halves that mios-a2a-discover calls (or an operator
runs standalone):

  * advertise -- when [a2a].mdns_advertise is true, render the static template at
    /usr/lib/mios/avahi/mios-a2a.service.in into /etc/avahi/services/mios-a2a.service,
    substituting the agent-pipe port ([ports].agent_pipe) and the service type
    ([a2a].mdns_service_type) from the layered mios.toml -- never a code literal.
    avahi-daemon watches that directory and (de)registers the service on file
    create/remove, so toggling the flag off removes the file = withdraws the
    announce, no daemon restart needed. Conservative default OFF (operator opts
    into a LAN announce).

  * browse -- when [a2a].mdns_discovery is true, run a one-shot
    `avahi-browse -p -r -t <type>` and parse the resolved (`=`-prefixed) records
    into http://IP:port candidate URLs. These are appended to the candidate set
    that mios-a2a-discover's EXISTING AgentCard probe validates before writing
    a2a-peers.json, so a non-MiOS responder on the wire is never trusted blindly.

Every external dependency (avahi-tools present, daemon up, template readable) is
optional: any miss logs and yields nothing, so discovery degrades to the explicit
[a2a].nodes / discover_cidr paths.

<!-- mios-src:2f7b67cecde7 from usr/libexec/mios/mios-a2a-mdns:5-28 -->

### Render or withdraw /etc/avahi/services/mios-a2a.service per...

Render or withdraw /etc/avahi/services/mios-a2a.service per
    [a2a].mdns_advertise. Returns True when the desired state was reached.
    Degrade-open: any error logs + returns False, never raises.

<!-- mios-src:8ef56e59b097 from usr/libexec/mios/mios-a2a-mdns:81-83 -->

### Parse one avahi-browse -p resolved (`=`-prefixed) line into...

Parse one avahi-browse -p resolved (`=`-prefixed) line into a candidate
    URL, or None.

    avahi-browse -p emits `;`-separated fields; resolved records lead with `=`.
    The documented field order (1-indexed) is:
        1 `=`  2 iface  3 proto  4 name  5 type  6 domain
        7 hostname  8 ADDRESS(IP)  9 PORT  10 txt...
    so positionally that is fields[7]=IP, fields[8]=port (0-indexed). The EXACT
    resolved field order can shift between avahi versions and cannot be verified
    offline, so we read those positions but VALIDATE structurally (IP must parse,
    port must be 1..65535); if the documented positions don't validate we scan
    the record for the first parseable IP followed by a valid port. The
    discriminators are structural (address-parse + integer range), not lexical.
    Names carry backslash escapes (\032 = space); we need only IP + port, so no
    unescaping is required to build the URL.

<!-- mios-src:1fcd7523d316 from usr/libexec/mios/mios-a2a-mdns:115-129 -->

### Cached candidate URLs iff the cache is younger than `floor`...

Cached candidate URLs iff the cache is younger than `floor` seconds AND was
    written for the SAME service type, else None. Degrade-open: any error -> None
    (forces a fresh live browse). Liveness is NOT trusted here -- mios-a2a-discover
    re-card-probes every candidate, so a stale cache entry is simply re-validated.

<!-- mios-src:8af91e9348dd from usr/libexec/mios/mios-a2a-mdns:182-185 -->

### One-shot mDNS browse for the A2A service type when...

One-shot mDNS browse for the A2A service type when [a2a].mdns_discovery is
    set. Returns a list of candidate http://IP:port URLs (deduped, order-stable)
    for mios-a2a-discover to card-probe. Honours the [a2a].mdns_refresh_sec FLOOR:
    a browse inside that window reuses the last live result (so discovery never
    loses peers between scans). Degrade-open: avahi-browse missing / daemon down /
    timeout -> [].

<!-- mios-src:0280cd8e80a3 from usr/libexec/mios/mios-a2a-mdns:213-218 -->

### mios-a2a-test -- A2A federation loopback smoke test...

mios-a2a-test -- A2A federation loopback smoke test (roadmap B5 / task_68).

Registers the local MiOS instance as its own A2A peer (loopback) and runs a
full JSON-RPC 2.0 ``message/send`` -> Task -> Artifact round-trip against the
``/a2a`` surface, then confirms the ``event`` table recorded the delegation
chain for the task's contextId. This is the first end-to-end federation smoke
increment: it proves the same /a2a path a remote peer would use works when a
MiOS speaks to itself.

USAGE
  mios-a2a-test --loopback            # run the round-trip; exit 0 on success
  mios-a2a-test --loopback --text "…" # custom probe prompt
  mios-a2a-test --loopback --json     # machine-readable result on stdout

Endpoint resolves from MIOS_AGENT_PIPE_URL (set from the mios.toml [ports] SSOT
via /etc/mios/install.env at runtime) -- identical to mios-a2a-delegate; no
literal port is baked into a decision. Unreachable endpoint => clear message +
nonzero exit, never a traceback (degrade-open: a dev checkout has no live lane).

<!-- mios-src:fcef9d7cad23 from usr/libexec/mios/mios-a2a-test:5-23 -->

### Build an A2A Message with a single text Part (pure)....

Build an A2A Message with a single text Part (pure).

    Mirrors the shape mios_pipe.federation.a2a expects: role + parts[] where
    each part is {kind:text, text:...}; contextId threads the loopback turn so
    the delegation chain is queryable afterwards.

<!-- mios-src:71925c15ee75 from usr/libexec/mios/mios-a2a-test:46-51 -->

### Best-effort

Best-effort: count event rows recording this contextId's delegation
    chain via mios-pg-query. Returns -1 when pg is unreachable (degrade-open --
    the smoke verdict never hinges on the audit DB being live).

<!-- mios-src:7199e8ecf958 from usr/libexec/mios/mios-a2a-test:110-112 -->

### 'MiOS' AdGuard Home first-boot config generator. Writes...

'MiOS' AdGuard Home first-boot config generator.

Writes /etc/mios/adguard/AdGuardHome.yaml from the [adguard] + [ports] SSOT in
mios.toml (layered: vendor /usr/share/mios/mios.toml < host /etc/mios/mios.toml)
plus two HOST-SPECIFIC values that can't be baked at image build time and are
discovered live here:

  * the Tailscale IPv4 (`tailscale ip -4`)  -> goes in dns.bind_hosts so tailnet
    peers can resolve at <vm-tailnet-ip>:53;
  * the MagicDNS suffix (`tailscale status --json`.MagicDNSSuffix) -> a split-DNS
    upstream rule  [/<suffix>/]<magicdns_resolver>  so *.ts.net pretty URLs keep
    resolving through Tailscale's MagicDNS while everything else goes upstream
    with ad-blocking.

Idempotent + non-destructive: if the config file already exists it does NOTHING
(AdGuard rewrites that file when settings change via the UI/API, so regenerating
would clobber the operator's changes). Delete the file to force a regen.

No hardcoded literals: every tunable comes from mios.toml; only the Tailscale
MagicDNS stub IP default lives in the TOML (it is a fixed, universal address).

<!-- mios-src:0d95a37fe87a from usr/libexec/mios/mios-adguard-firstboot:4-24 -->

### Build the AdGuard `users:` block so a fresh install is NOT...

Build the AdGuard `users:` block so a fresh install is NOT left with a
    no-auth admin UI (it controls DNS for the whole tailnet once it's the global
    nameserver). If [adguard].admin_password_bcrypt is set, use it verbatim.
    Otherwise auto-generate a strong random password, bcrypt it, and drop the
    plaintext in a root/825-only file the operator can read once. If bcrypt is
    unavailable, fall back to an OPEN (tailnet-bound) UI + a loud warning.

<!-- mios-src:4d79778ec0f6 from usr/libexec/mios/mios-adguard-firstboot:100-105 -->

### mios-ai-capabilities-gen -- project mios.toml...

mios-ai-capabilities-gen -- project mios.toml [verbs.*]+[recipes.*] -> the
unified, RBAC-filterable capability manifest ai/v1/capabilities.generated.json.

  mios-ai-capabilities-gen          # regenerate the manifest (write)
  mios-ai-capabilities-gen --check  # verify committed == projection (exit 1 on drift)

The committed artifact is projected at ceiling=interactive (every known-tier
capability); server.py RE-filters per caller via mios_capreg.build_capability_manifest
+ mios_pdp at request time. Paths override via MIOS_TOML / MIOS_CAPS_OUT.

<!-- mios-src:6c3b777f817c from usr/libexec/mios/mios-ai-capabilities-gen:5-14 -->

### mios-ai-hint-coverage -- ratchet gate for AI-hint header...

mios-ai-hint-coverage -- ratchet gate for AI-hint header coverage.

Taggability is NOT redefined here: this tool imports mios-ai-tag (the single
source of truth for which files should carry a header) and reuses its
walk()/existing_hint(). It only ADDS a pass/fail policy: untagged_count <= ceiling.

The ceiling (max permitted untagged files) is resolved, highest priority first:
  1. --max-untagged N            (CLI)
  2. $MIOS_AITAG_MAX_UNTAGGED     (env)
  3. [ai_tag].max_untagged        (mios.toml SSOT, layered: vendor < /etc < ~)
  4. permissive fallback          (report-only; never false-fails a bare checkout)

Why a ratchet, not a hard 100%: a few taggable files MUST stay header-free --
agent SOUL/prompt markdown (a `<!-- AI-hint -->` line would leak into the prompt)
and single-value data files (a comment would corrupt the value). The ceiling lets
those remain while failing the build the moment a NEW untagged file lands. Drive
the ceiling toward 0 as real code/config gets tagged (`mios-ai-tag`).

Usage:
  mios-ai-hint-coverage --root .                   # gate cwd (repo == system root)
  mios-ai-hint-coverage --root . --max-untagged 0  # require 100%
  mios-ai-hint-coverage --root . --json            # machine-readable summary

<!-- mios-src:70cff22ab361 from usr/libexec/mios/mios-ai-hint-coverage:5-27 -->

### Set of walked paths that git IGNORES. Local gitignored...

Set of walked paths that git IGNORES. Local gitignored scratch (e.g. a
    dev's tmp-*.py / audit_*.ps1 at repo root) is NOT shipped or tracked, so it
    must not count against AI-hint coverage -- otherwise any dev's local scratch
    reds `just drift-gate` / the CI PR gate / a repo-root build.sh run. The OCI
    bake already excludes it (only the copied usr/etc/tools/automation subset is
    in /ctx); this aligns the repo-root invocation with the in-image semantics.
    Degrade-open: git missing / not-a-repo / error -> empty set (count
    everything, the prior behaviour) so this never falsely passes a real gap.

<!-- mios-src:512bb4858c3d from usr/libexec/mios/mios-ai-hint-coverage:96-103 -->

### mios-ai-manifest-gen -- project mios.toml [verbs.*] ->...

mios-ai-manifest-gen -- project mios.toml [verbs.*] -> ai/v1/tools.generated.json.

Usage:
  mios-ai-manifest-gen           # regenerate the manifest (write)
  mios-ai-manifest-gen --check   # verify the committed manifest is in sync (exit 1 on drift)

The generated manifest is the COMMITTED, diffable projection of the live verb
catalog (registry_kind="verb-catalog") -- distinct from the file-backed Hermes
build-tools registry (ai/v1/tools.json, registry_kind="hermes-build-tools").

<!-- mios-src:f9bfb271b285 from usr/libexec/mios/mios-ai-manifest-gen:5-13 -->

### 'MiOS' AI-tagger -- a rich, agent-useful AI header on every...

'MiOS' AI-tagger -- a rich, agent-useful AI header on every file.

Goal (operator 2026-06-13): every file in both MiOS repos carries a header that
is descriptive of its USE + PURPOSE, the RELATED files/services it touches, and
the FUNCTIONS/entrypoints it defines -- so an agent knows what a file is for, what
it depends on, and what it exposes, without reading the whole thing.

Header block (comment-syntax + shebang aware, line-ending preserving):

Reuse keeps it cheap: an existing AI-hint is kept verbatim; only files lacking one
hit the teacher. JSON (no comments) + binaries are skipped.

Usage:
  mios-ai-tag --root /usr/share/mios --root /mnt/c/mios-bootstrap               --manifest /var/lib/mios/finetune/ai-tag-rich.manifest
  mios-ai-tag --root DIR --no-llm     # never call the teacher (reuse/extract only)
  mios-ai-tag --root DIR --dry-run --limit 5
  mios-ai-tag --selftest              # execute self-test suite

<!-- mios-src:5ddfd868e924 from usr/libexec/mios/mios-ai-tag:4-22 -->

### Hint length cap, from [ai_tag].hint_max_chars. Was a...

Hint length cap, from [ai_tag].hint_max_chars.

    Was a hardcoded 260 (Law 7). That number also silently TRUNCATED existing
    hand-written hints on every re-tag, because _clean slices to it.

<!-- mios-src:5ddddf9bc90e from usr/libexec/mios/mios-ai-tag:118-122 -->

### mios-app-search <query> [--limit N] [--json] Semantic...

mios-app-search <query> [--limit N] [--json]

Semantic search over the installed-app inventory. Calls agent-pipe's
/v1/app-search endpoint, which embeds the query + cosine-ranks against
the cached mios-apps inventory.

Use for ambiguous asks like "my note-taking app", "phone settings",
"the screenshot tool" -- substring against mios-apps misses anything
the operator phrases differently.

Returns top-k {category, name, description, launch, score}.

SSOT: mios-apps inventory is the source; this is a thin client.

<!-- mios-src:06f34276aeac from usr/libexec/mios/mios-app-search:4-17 -->

### localhost/ refs have no registry behind them -- podman pull...

localhost/ refs have no registry behind them -- podman pull can NEVER succeed
(it dials https://localhost/v2/ and dies on connection refused after the full
retry ladder). Locally-built images are baked by their own builder step
(57-mios-sys-build.sh) or deferred to the firstboot tier
([build.bake].firstboot_tokens -> plan.d/firstboot.list ->
mios-webtools-firstboot.service builds them on first boot). One landing in a
pull group is a bake-plan bug: fail fast with the fix instead of retry-spinning.

<!-- mios-src:7c41a2feb2f7 from usr/libexec/mios/mios-bake-group:72-78 -->

### mios-bench -- agentic-capability benchmark harness for the...

mios-bench -- agentic-capability benchmark harness for the MiOS agent plane.

The AIOS engineering blueprint flagged the absence of a standard capability
benchmark runner (SWE-bench / OSWorld / tau-bench). This is that harness.

  mios-bench score <results.json> [--k 8]
      OFFLINE. Reads a JSON list of trial records
        [{"task","ok","cost","latency_ms","error","security_violation"}, ...]
      (or {"results":[...]}) and prints the CLASSic rollup + mean pass@k / pass^k
      grouped by task. Pure -- no endpoint needed.

  mios-bench run <suite.json> [--k 8] [--endpoint URL] [--out results.json]
      Drives the agent-pipe endpoint. suite = JSON list of
        [{"task","prompt","expect": "<substring>" | ["any","of"]}, ...].
      Runs each task k times, marks ok when an `expect` substring is present,
      records latency, writes the results JSON. Needs the live :8700 lane.

Endpoint resolves from MIOS_AI_ENDPOINT (Law 5) -> default http://localhost:8700/v1.
Degrade-open: a dead lane records a failed/errored trial, never crashes the run.

<!-- mios-src:b2aa0752f246 from usr/libexec/mios/mios-bench:5-24 -->

### Where this blade's services actually live, and whether they...

Where this blade's services actually live, and whether they answer.
On a seat every target is REMOTE, so this is the one question a seat
operator has: is my blade there? Without it an unreachable blade looks
like a broken model -- the lane resolver hands back its terminal lane
even when the probe fails, by design, so the turn degrades rather than
dead-ends and the transport error is all you see.

<!-- mios-src:a4df947e4a32 from usr/libexec/mios/mios-blade:165-170 -->

### mios-cdp-fetch <url> [max_chars] Deterministically fetch a...

mios-cdp-fetch <url> [max_chars]

Deterministically fetch a page's rendered text via the ChromeDev CDP browser
(:9222) -- so the MiOS agent gets REAL page content instead of hallucinating
it (operator 2026-06-06: "cdp web browse in hermes"; gemma4 fabricated the
Wikipedia first sentence instead of reading the DOM). Ensures the CDP chrome is
up (mios-hermes-browser ensure), opens a fresh tab AT the url (PUT /json/new?url
navigates it), waits for document.readyState == complete, reads document.title +
document.body.innerText via Runtime.evaluate over the tab's CDP websocket, prints
one JSON line {url,title,text}, then closes the tab.

Headless + observational (no visible window): the same CDP surface the agent's
browser_navigate uses, exposed as a deterministic verb the pipeline can call so
the answer is grounded in the actual page, not the model's guess.

<!-- mios-src:4a8b5030a285 from usr/libexec/mios/mios-cdp-fetch:5-19 -->

### mios-chain-verify -- walk the MiOS event hash chain and...

mios-chain-verify -- walk the MiOS event hash chain and report the first tamper.

The `event` table is an append-only observability stream; SEC-03 links every row to
its predecessor with a SHA-256 chain (chain_seq / prev_hash / chain_hash) at the
agent-pipe persist chokepoint. This tool independently RE-DERIVES the chain from the
stored rows and reports whether it is intact:

  * reads the chained rows (WHERE chain_hash IS NOT NULL) in chain_seq order via
    `mios-pg-query --exec-json` -- the pure-stdlib pg wire transport every confined
    agent-plane reader uses (no psql / psycopg / podman required);
  * recomputes each link with the SAME canonical_core + sha256 used on write,
    imported from the agent-pipe `mios_audit` module so the algorithm has ONE source
    of truth (no second copy of the crypto to drift);
  * prints `{ok, checked, first_broken_seq}` and EXITS NONZERO on a broken chain.

Exit codes:  0 = chain intact (or empty)   1 = tamper detected (first_broken_seq)
             2 = read / setup failure (cannot reach pg or import the verifier)

Env:  MIOS_PG_QUERY        (default /usr/libexec/mios/mios-pg-query)
      MIOS_AGENT_PIPE_DIR  (default: resolved relative to this CLI)
      plus mios-pg-query's own MIOS_PGVECTOR_HOST / MIOS_PG_PORT / MIOS_PGVECTOR_USER / MIOS_PGVECTOR_DB

<!-- mios-src:a198b84a4b58 from usr/libexec/mios/mios-chain-verify:5-26 -->

### Every read below is a HARD error. The previous revision...

Every read below is a HARD error. The previous revision ended each value with
`2>/dev/null || echo "False"`, which turned a missing [security.luks] table, an
unparseable mios.toml, an absent mios.toml and even a missing python3 into a
successful run that printed plausible-looking defaults -- so the projection
could not be distinguished from a total SSOT read failure, and check_clevis_luks
stayed green through all four. A generator that cannot read the SSOT must fail.

<!-- mios-src:5a5bf3c5aaf9 from usr/libexec/mios/mios-clevis-luks-gen:7-12 -->

### 'MiOS' codebase index -- one descriptive line per file, for...

'MiOS' codebase index -- one descriptive line per file, for AI-agent discovery.

The operator directive (2026-06-13): "MiOS AI should be able to use the WHOLE
MiOS codebase ... all docs, scripts, functions ... every file tagged with a
descriptive header/hint for AI agents". This tool is the DISCOVERY half: it walks
the MiOS roots, pulls a one-line description from each file (in priority order:
an explicit `AI-hint:` marker -> YAML frontmatter `description:` -> a module/script
docstring or header comment -> first H1 -> first meaningful line), and emits a
single unified index. Agents read the index, see what exists, then read or run
the exact file -- the same filesystem-discoverable pattern as mios-docs-index
(no English-prose RAG auto-injection -> no locale contamination).

`mios-ai-tag` is the COMPLEMENT: it ensures every file actually HAS an `AI-hint:`
so this index is rich. Files lacking any description are reported via --gaps.

Usage:
  mios-codebase-index                 full index to stdout (default live roots)
  mios-codebase-index --grep <pat>    filter lines by regex
  mios-codebase-index --root <dir>    index DIR instead of the default roots
                                      (repeatable; e.g. a source checkout)
  mios-codebase-index --gaps          list ONLY files missing any description
  mios-codebase-index --write [PATH]  also persist (default
                                      /var/lib/mios/scratch/codebase-index.md)
  mios-codebase-index --json          machine-readable [{path,desc,via}]

<!-- mios-src:f4192bdc7610 from usr/libexec/mios/mios-codebase-index:4-28 -->

### mios-coderun -- execute agent code in the bubblewrap...

mios-coderun -- execute agent code in the bubblewrap sandbox (the `coderun`
verb backend). Completes the P4.4 sandbox loop: the sandbox (mios-sandbox-exec)
is now reachable AS A VERB, so an agent can run code SAFELY -- read-only system,
writable scratch workspace only, NO network egress by default, resource caps.

Distinct from `powershell_run` (Windows host shell) and `mios-coderun-session`
(the container dry-run/test boundary orchestrator): this runs a SHORT code
snippet / command in a per-call bwrap jail on the Linux side + returns its
stdout/stderr/exit, then discards the scratch dir.

USAGE
  mios-coderun --code '<source>' [--lang bash|python] [--net] [--timeout 60]
  mios-coderun --code 'print(2**10)' --lang python

OUTPUT (JSON): {ok, lang, exit_code, stdout, stderr, sandboxed, net}

<!-- mios-src:057f958f2de1 from usr/libexec/mios/mios-coderun:4-19 -->

### mios-coderun-codemode -- WS-2 Code Mode runner (the...

mios-coderun-codemode -- WS-2 Code Mode runner (the `code_mode` verb backend).

Runs an AGENT-SUPPLIED code snippet inside the EXISTING rootless PODMAN
coderun-sandbox (concepts/coderun-sandbox.md) -- the defense-in-depth boundary
(Network=none, ReadOnly, DropCapability=ALL, seccomp allowlist, Landlock PID-1,
cgroups v2). This is the "Code Mode" half of the AIOS Tool Manager: the model
writes CODE that calls a small local tool API (mios_tools, the in-sandbox shim
copied alongside) instead of having ~71 OpenAI function schemas loaded into its
context; only the FILTERED result returns -- the big token win.

Distinct from its two siblings:
  * mios-coderun         -- bubblewrap (bwrap) per-call jail; the lighter
                            `coderun` verb backend. THIS uses the heavier podman
                            container instead (full Quadlet hardening + a warm,
                            conversation-scoped session).
  * mios-coderun-session -- the start/stop/snap/revert orchestrator for the same
                            podman sandbox; THIS calls it to ensure the session
                            is up, then `podman exec`s the snippet in.

DEFAULT-OFF + degrade-CLOSED: code execution only runs when [code_mode].enable is
set in mios.toml AND the podman sandbox image/unit are present. Any precondition
miss -> a clean refusal JSON, never an unsandboxed fallback (the one feature where
we degrade CLOSED, not open -- running model code outside the jail is a non-
starter).

USAGE
  mios-coderun-codemode --session <id> [--lang python|bash|sh] [--net]                         [--timeout 60]   < snippet-on-stdin
  echo 'print(2**10)' | mios-coderun-codemode --session cm-abc --lang python

OUTPUT (JSON): {ok, lang, exit_code, stdout, stderr, sandboxed, net, [result]}

<!-- mios-src:a19b7fe4bbc2 from usr/libexec/mios/mios-coderun-codemode:4-35 -->

### mios-compact -- compact recent agent + system state into a...

mios-compact -- compact recent agent + system state into a single
markdown digest that can be ingested as an OWUI knowledge artifact.

Operator directive 2026-05-17: "make sure there's tools to compact
all this and artifact it natively for OWUI knowledge/database". The
agent stack generates a lot of latent state (recent chats, hermes
session decisions, daemon classifications, launch verifier failures,
git commits) that's useful for the agent to RAG against on later
turns. This helper pulls the lot, summarizes via the local CPU
model, and writes a versioned markdown file under
/var/lib/mios/compacted/. Pair with mios-knowledge-add to register
the file as a Knowledge collection in OWUI.

Sections in the rendered digest:
  1. Recent operator chats (last N user turns + agent responses)
  2. Launch verifier failures (from daemon)
  3. Recent hermes tool-call patterns
  4. Daemon classify summaries (system log roll-up)
  5. Git commits this session

Output: /var/lib/mios/compacted/<utc-iso>.md  (timestamped, never
overwrites previous digests; mios-knowledge-add picks the newest).

Usage:
  mios-compact                          # default: last 24h of activity
  mios-compact --since "12 hours ago"   # parseable by `date`
  mios-compact --chats 10               # cap chat count
  mios-compact --out <path>             # override output path
  mios-compact --stdout                 # print to stdout instead of file
  mios-compact --no-llm                 # skip the CPU summarization step

Exit codes:
  0 = digest written (or printed)
  1 = required state unreachable (OWUI db missing, llm-light down)
  64 = bad args

<!-- mios-src:b6126d134922 from usr/libexec/mios/mios-compact:5-40 -->

### mios-computer-use -- Linux/Wayland desktop computer-use...

mios-computer-use -- Linux/Wayland desktop computer-use executor for MiOS.

The Linux/Wayland peer of `mios-pc-control` (which drives the Windows host via
Win32 SendInput through the WSL broker). This tool drives the LOCAL Linux
graphical session -- bare-metal/VM GNOME or KDE on Wayland, or a wlroots
compositor -- with NO ydotool dependency (ydotool is AGPLv3 + seat-wide):

  * INPUT   -- the freedesktop RemoteDesktop portal (libei era; GNOME >= 45 /
               KWin >= 6.1 production) over D-Bus, with a persisted restore
               token for unattended re-use. Self-written `evdev`/uinput backend
               as the compositor-agnostic fallback (our own code -> MIT-clean,
               works on wlroots + headless seats too).
  * CAPTURE -- one-shot Screenshot portal, with grim / gnome-screenshot /
               spectacle CLI fallbacks (all FOSS, offline).
  * GROUND  -- AT-SPI2 a11y tree FIRST (role/name/text, no pixels, deterministic),
               vision grounding (mios-pc-vision -> qwen3-vl:4b baseline or the
               UI-TARS-1.5-7B vLLM `mios-grounding` lane) only when the tree is
               empty (Chromium/Electron/canvas).

ENVIRONMENT-ADAPTIVE (MiOS is a bootc image deployable on any hardware):
  1. [computer_use].executor_endpoint set + reachable -> route every op to that
     HTTP executor (federation: drive ANOTHER machine's desktop, same contract
     mios-pc-control uses for the Windows executor + mios-computer-use-server
     uses for Linux). Configured-but-down => honest error, never a blind local
     fall-back (mirrors the os_control executor rule).
  2. else local Wayland session (WAYLAND_DISPLAY present) -> portal/uinput.
  3. else WSL2 with a reachable Windows host -> delegate to mios-pc-control so
     the SAME verb works everywhere.
  4. else honest "no desktop backend available" error.

SUBCOMMANDS (contract-compatible with mios-pc-control so the cu_* verbs and the
dual MCP+A2A server are backend-agnostic):
    screenshot <out-path>          one-shot capture to PNG
    click <x> <y> [button]         left|right|middle (default left)
    double-click <x> <y>
    mouse-move <x> <y>
    type "<text>"                  literal text into the focused surface
    key <name>                     Enter|Tab|Escape|Up|Down|...|F1..F12|<char>
    key-combo "Ctrl+S"             modifier combo
    window-list [--json]           AT-SPI / wmctrl / hyprctl / kdotool top-levels
    window-focus <id>
    window-move <id> <x> <y>
    window-resize <id> <w> <h>
    atspi-query "<query>" [--json] semantic element lookup (no pixels)
    ground "<query>" [--json]      screenshot -> AT-SPI/vision -> {x,y,confidence}
    help

CONFIG (layered mios.toml, per-user > /etc > vendor):
    [computer_use]
    executor_endpoint = ""           # federation: drive a remote desktop
    input_backend     = "auto"       # auto | portal | uinput
    capture_backend   = "auto"       # auto | portal | grim | gnome | spectacle
    restore_token_path = "$XDG_STATE_HOME/mios/cu-restore-token"

<!-- mios-src:a87046949b0f from usr/libexec/mios/mios-computer-use:4-57 -->

### Self-written /dev/uinput backend via python-evdev (BSD). No...

Self-written /dev/uinput backend via python-evdev (BSD). No ydotool.

    Compositor-agnostic (works on wlroots + headless seats). Absolute pointer
    axes are sized to the current screen so (x,y) are real pixel coordinates.
    Needs the agent user in `input` + the uinput uaccess udev rule.

<!-- mios-src:b55e901cc2aa from usr/libexec/mios/mios-computer-use:443-448 -->

### org.freedesktop.portal.RemoteDesktop (libei era) over D-Bus...

org.freedesktop.portal.RemoteDesktop (libei era) over D-Bus via Gio.

    Production on GNOME (>=45) and KWin (>=6.1). A linked ScreenCast stream
    defines the absolute-coordinate space; the restore token (interface v2)
    skips the consent dialog on subsequent unattended runs.

    NOTE: this path requires a live graphical-session portal and is validated
    on a real GNOME/KDE Wayland desktop (cannot be exercised headless). On any
    failure the caller falls back to the uinput backend.

<!-- mios-src:4b3eebd1af05 from usr/libexec/mios/mios-computer-use:539-548 -->

### mios-computer-use-server -- dual MCP + A2A + REST-executor...

mios-computer-use-server -- dual MCP + A2A + REST-executor server for a
MiOS/Linux desktop node, so the central agent-pipe CONSUMES this desktop as a
first-class federated capability (full MCP AND A2A).

WEBSERVER: FastAPI + uvicorn (the SAME stack the agent-pipe runs on, from the
SAME shared venv /usr/lib/mios/agents/.venv). Hand-rolled, spec-faithful routes
-- NOT the official `mcp` / `a2a-sdk` packages -- because (1) the agent-pipe (the
CONSUMER) already serves MCP + A2A hand-rolled in exactly this style, (2) those
SDKs are pip-only with churny APIs (a2a-sdk >=1.0 dropped A2AStarletteApplication;
the mcp SDK has had mount/lifespan bugs) which an immutable air-gapped bootc
image must not depend on, and (3) fastapi/uvicorn/httpx are already present +
dnf-available offline. The surface is tiny (9 tools, 1 skill); interop is
guaranteed because the pipe's own MCP/A2A client consumes these exact shapes.

ONE capability, THREE HTTP surfaces, all backed by the local `mios-computer-use`:

  1. MCP -- Streamable HTTP transport (spec 2025-06-18) at /mcp:
       * POST /mcp  -- JSON-RPC: initialize / tools/list / tools/call. Returns
                       application/json (spec-legal for a stateless server; SSE
                       is OPTIONAL for request/response tool calls).
       * GET  /mcp  -- opens a text/event-stream keepalive channel (server->client
                       notifications); spec allows 405 but we serve a minimal SSE.
       * Origin validation (DNS-rebinding guard) + MCP-Protocol-Version tolerance.
     The pipe's MCP client (add this URL to /etc/mios/ai/v1/mcp.json) surfaces the
     tools as `mcp.<server>.cu.*` IN THE AGENT LOOP (_mcp_tool_to_openai_tool).

  2. A2A (Agent2Agent 0.3.0): GET /.well-known/agent-card.json advertises a
     `desktop-control` skill; POST /a2a is JSON-RPC (message/send + message/stream
     via SSE); GET /a2a/contexts/{id} shares inter-agent context. The pipe (add
     this URL to /etc/mios/ai/v1/a2a-peers.json) DISCOVERS the skill + DELEGATES
     whole desktop tasks via _a2a_send_message_to_peer.

  3. REST executor contract: GET /health|/screenshot|/windows|/screen-layout,
     POST /input/*, /window/*, /atspi/query, /ground -- the SAME contract the
     Windows mios-oscontrol-server.ps1 speaks, so a central node's
     mios-computer-use with executor_endpoint=<this> routes fine-grained ops here.

SECURITY: bind LOOPBACK by default; reach it over the tailnet via
[computer_use].bind_address + firewall to the tailnet (mios_tailscale pattern).
Optional bearer token ([computer_use].auth_token) gates write surfaces. Require
A2A passport signing at the pipe for cross-host delegation. Every op runs through
the local mios-computer-use, which honours the DoD/approval gate.

CONFIG (layered mios.toml [computer_use]): server_port, bind_address,
allowed_origins, auth_token.

<!-- mios-src:7a1c1d193c41 from usr/libexec/mios/mios-computer-use-server:4-49 -->

### mios-crawl -- native CRAWL verb backend (fetch a URL ->...

mios-crawl -- native CRAWL verb backend (fetch a URL -> clean markdown).

Reads ONE web page and returns LLM-ready markdown so agents GROUND on the
ACTUAL page content instead of a search snippet or a fabrication. Thin HTTP
client for the local mios-crawl4ai service (loopback FastAPI) -- the slow
crawl4ai/camoufox import + browser-attach is kept WARM in that service, so
each `crawl` verb call is just a fast loopback POST (same shape as
mios-web-search -> SearXNG).

  --- engine flow (operator directive 2026-05-24) ---
  PRIMARY  : crawl4ai drives the EXISTING local Chrome over the DevTools
             Protocol (ws://127.0.0.1:9222, the ChromeDev flatpak that
             mios-hermes-browser.service keeps up). crawl4ai ATTACHES to
             that browser via BrowserConfig(browser_mode="custom",
             cdp_url=...) -- NO bundled/downloaded Chromium.
  FAIL-RETRY: if the CDP crawl errors / is blocked / returns near-empty
             markdown, the SAME url is retried with Camoufox (stealth
             anti-detect Firefox). Camoufox ships its own patched Firefox.
  HONEST-FAIL: if both engines fail, this prints success:false with an
             error -- it NEVER invents page content.

Companion to mios-web-search: web_search (SearXNG) finds candidate URLs;
crawl reads the chosen one. Agent flow: search -> pick URL -> crawl ->
answer from the fetched markdown.

SSOT (env rendered from mios.toml [crawl] via globals/userenv):
  MIOS_CRAWL_SERVICE_URL  base URL of the local crawl service
                          (default http://127.0.0.1:${MIOS_PORTS_CRAWL4AI:-8810})

Usage:
  mios-crawl <http(s)-url> [--max-chars N] [--camoufox] [--timeout S] [--json]
    --camoufox   force the Camoufox stealth path (skip Chrome CDP). Used by
                 the smoke test to confirm the fail-retry engine works.
    --max-chars  truncate returned markdown (0 = no limit; default 20000)
Output: JSON {success, engine, url, title, markdown, links}

<!-- mios-src:0fb309da56c2 from usr/libexec/mios/mios-crawl:4-39 -->

### crawl4ai service down (e.g. image not built) -> fall back...

crawl4ai service down (e.g. image not built) -> fall back to the lighter
    mios-web-extract single-page reader (no heavy crawl4ai/Chrome stack needed)
    so `crawl` still GROUNDS on real page content instead of hard-failing.
    Returns {} if the fallback also fails (then the honest-fail stands).

<!-- mios-src:a1a39056a33f from usr/libexec/mios/mios-crawl:68-71 -->

### 'MiOS' cron-director -- minimal LLM-gated recurring-task...

'MiOS' cron-director -- minimal LLM-gated recurring-task scheduler.

Reads /etc/mios/cron-rules.toml ([[rule]] name/cron/do/gate per the vendor
schema), and once per minute fires each rule whose 5-field cron expression
matches the current local minute:

  * no `gate`  -> fire `do` immediately;
  * `gate`     -> ask the micro-LLM a YES/NO question (with a little live system
                  state); fire on YES, skip+log on NO. Default on gate ERROR is
                  SKIP -- a gated rule asked for a condition we couldn't verify,
                  so we do NOT fire it blindly.

`do` runs via `bash -lc <do>`, DETACHED (fire-and-forget) so a long task never
blocks the minute loop; stdout/stderr go to the journal. A per-minute dedup
state file (/var/lib/mios/cron-director/state.json) prevents double-firing.
SIGHUP reloads the rules without a restart. Stdlib only -- no croniter.

Operator 2026-05-26: built to make "do X every N minutes" actually recur (the
service was referenced in the preset + mios.toml but the daemon never existed).

<!-- mios-src:a9808313eead from usr/libexec/mios/mios-cron-director:4-23 -->

### 'MiOS' schedule manager -- add/list/remove cron-director...

'MiOS' schedule manager -- add/list/remove cron-director rules.

The `schedule` verb dispatches here. `add` translates a human interval
("30 minutes", "hourly", "daily", or a raw 5-field cron) into a cron
expression, stores the PROMPT text in /var/lib/mios/cron-director/prompts/
<name>.txt (kept OUT of the shell `do` line so there is no injection), writes a
[[rule]] to /etc/mios/cron-rules.toml whose `do` is
`mios-scheduled-research --rule <name>`, and SIGHUPs the daemon to reload.

Usage:
  mios-cron-schedule add --prompt "<text>" --every "<interval>"
  mios-cron-schedule list
  mios-cron-schedule remove --name <name>

<!-- mios-src:b9e1d49bd61f from usr/libexec/mios/mios-cron-schedule:5-18 -->

### mios-cu-verify -- visual verification of desktop state....

mios-cu-verify -- visual verification of desktop state.

Takes a natural-language description of an expected state (e.g. "terminal is open")
and asks the local vision LLM (default qwen3-vl:4b) if the state is currently
true on the screen. Returns JSON:

    {"ok": <bool>, "reasoning": "..."}

USAGE
    mios-cu-verify "<expectation>" [--json]

CONFIG (resolved per layered mios.toml [ai] priority):
    [ai]
    vision_grounding_model = "qwen3-vl:4b"
    vision_grounding_endpoint = "http://localhost:8450/v1"

<!-- mios-src:a1d4f32f2e20 from usr/libexec/mios/mios-cu-verify:4-19 -->

### mios-cursor-apply -- set the X11 root-window default cursor...

mios-cursor-apply -- set the X11 root-window default cursor to the MiOS
cursor theme.

Operator trace 2026-05-19: under WSLg, WebKit content + GTK *widgets*
(links, text) show Bibata because they set NAMED cursors explicitly, but
the window CHROME (Epiphany frame, Nautilus, gnome-software) shows the
wrong cursor. Cause: GTK4 leaves a toplevel's ambient/idle cursor unset,
so it falls back to the X SERVER's default cursor -- and nothing themes
that under sessionless Xwayland (no gsd-xsettings / XSETTINGS daemon, no
session to set the root cursor; xsetroot/xrdb/xsettingsd aren't installed).

Fix: XDefineCursor the Bibata `left_ptr` onto the root window. Toplevels
with no explicit cursor inherit the root cursor (X11 cursor inheritance),
so the chrome themes correctly. Uses libX11 + libXcursor directly (both
present -- Xwayland/GTK link them); no package install.

Idempotent + safe: no-op without a DISPLAY. Per-X-session (the root cursor
resets if Xwayland restarts), so it's invoked on interactive login from
/etc/profile.d/mios-cursor.sh. Theme + size come from mios.toml
[theme.cursor_linux] (SSOT).

<!-- mios-src:06276cbf8b0c from usr/libexec/mios/mios-cursor-apply:4-23 -->

### mios-daemon -- consolidated MiOS micro-LLM daemon. Operator...

mios-daemon -- consolidated MiOS micro-LLM daemon.

Operator directive 2026-05-17: "ALL to be consolidated to one mios
daemon/agent". Replaces three separate journal-subscribing /
periodic-evaluating services (mios-log-watcher + mios-agent-nudger
+ mios-cron-director) with ONE process that:

  * Subscribes to journald ONCE (not three times)
  * Holds a SINGLE /v1 client (keep_alive=-1 forever, num_gpu=0
    CPU-only per Law 7 + the operator's "always-on agentic OS"
    directive)
  * Dispatches three handlers off a single event stream:
      - classify: every Nseconds-batch of journal lines summarized
                  via qwen3:0.6b-cpu (was: mios-log-watcher)
      - refusal:  every hermes-agent.service response judged by the
                  micro-LLM -- refusal / hedge / fabrication instead
                  of doing the work? (was: mios-agent-nudger; the
                  English refusal-pattern pre-filter is deleted -- the
                  judge is authoritative, degrade-open when its lane
                  is unreachable)
      - cron:     /etc/mios/daemon/cron.toml gates evaluated on a
                  cadence; YES gates fire their action (was:
                  mios-cron-director)
  * Writes a UNIFIED state file at /var/lib/mios/daemon/state.json
    with all three handler outputs. mios_sidecar_filter.py polls
    this single file (replaces the three SIBLING_AGENTS entries).

Architecture per Law 7 OFFLINE-FIRST: all LLM calls go to the local
mios-llm-light OpenAI /v1 lane at /v1/chat/completions (MiOS is
/v1-only; the server owns thread/offload placement). Thinking-mode
models' output is read from message.content first, then
message.reasoning_content / message.reasoning fallback (qwen3.5-family
puts CoT in those fields).

For the OWUI sidecar filter: state.json keys map 1:1 to the prior
SIBLING_AGENTS labels so the filter just reads ONE file instead
of three. Per-section sub-objects let the filter format each
category exactly as before. Schema:

    {
      "ts": <epoch>,
      "classify":    {"summary":..., "tags":[...], "severity":...,
                      "event_count":..., "events_sample":[...]},
      "refusal":     {"phrase":..., "model":..., "ts":..., "service":...},
      "cron":        {"last_fire": {"rule":..., "ts":...},
                      "decisions":[...]}
    }

<!-- mios-src:06f88a81dfcd from usr/libexec/mios/mios-daemon:5-52 -->

### Read top-level [daemon] scalar keys from the layered...

Read top-level [daemon] scalar keys from the layered mios.toml
    (vendor < /etc < per-user; later layer wins). Mirrors
    _read_daemon_index_config's layering. Returns {} when unreadable so
    every consumer falls back to its compiled-in default (degrade-open).

<!-- mios-src:667115e805e9 from usr/libexec/mios/mios-daemon:90-93 -->

### WS-A3

WS-A3: run a PARAMETERIZED statement/batch via `mios-db --pg-json`, which
    binds values OUT-OF-BAND through mios-pg-query's extended protocol (no value
    is spliced into SQL -> injection-safe). Returns stdout; degrade-open -> ''.

<!-- mios-src:46f48ba2bccf from usr/libexec/mios/mios-daemon:200-202 -->

### Mirror a daemon write to pgvector (chokepoint, called from...

Mirror a daemon write to pgvector (chokepoint, called from _db_create).
    WS-A3: values are now BOUND via $1..$n params (mios-db --pg-json), never
    f-string-spliced (the old _pgesc single-quote doubling was not a binding).
    dict/list -> jsonb via a ::jsonb cast on the bound param; time columns use
    the pgvector column DEFAULT (now()). `table`/column names are trusted
    identifiers (never request data). Degrade-open.

<!-- mios-src:003533209249 from usr/libexec/mios/mios-daemon:215-220 -->

### WS-A3

WS-A3: the legacy DB (:8000) is RETIRED -> hard no-op. Returns None (callers
    do `or []` / _ok_result(None) -> []), exactly as the dead backend resolved to
    but WITHOUT the per-call 3s timeout + 30s backoff. Writes are already mirrored
    to pgvector at the _db_create chokepoint (_pg_insert); translated reads go
    through _db_read's pg path. Kept as a stub (signature incl. `timeout`) so the
    many _db_post(_db_create(...)) write sites + read fallbacks need no change.

<!-- mios-src:3865af38fd2f from usr/libexec/mios/mios-daemon:249-254 -->

### R15/G10

R15/G10: read rows from pgvector. Wraps the SELECT in json_agg(row_to_json)
    so the output is a single JSON array. Returns a list of dicts ([] on error/
    pg-disabled). WS-A3: when `params` is given, the values are BOUND out-of-band
    ($1..$n) via `mios-db --pg-json` -- the select_sql must use placeholders, not
    f-string-spliced values. Without params, the legacy `mios-db --pg` path runs
    a constant SQL string.

<!-- mios-src:18b261b5f223 from usr/libexec/mios/mios-daemon:259-264 -->

### Agent-plane READ seam (mirrors agent-pipe's). When pg is...

Agent-plane READ seam (mirrors agent-pipe's). When pg is ENABLED AND a
    pg_sql translation is supplied, run it and wrap the rows in the legacy
    [{"result": [...]}] envelope so _ok_result parses both shapes UNCHANGED. Else
    the legacy DB.

    WS-A3: gated on _PG_ENABLED (not _PG_PRIMARY) -- in the DEFAULT 'dual' mode
    the legacy DB (:8000) is retired and dead, so a _PG_PRIMARY gate left every
    translated read (satisfaction monitor, refine scan, ...) hitting the dead
    backend and returning [] (the documented training/telemetry-starvation bug).
    Reading the live pgvector mirror in dual is correct (daemon writes already
    mirror to pg) and strictly non-regressive: a missing/bad translation still
    falls back to the legacy DB -> [] (degrade-open), same as before, and pg responds
    instead of timing out + arming the 30s backoff. Reads WITHOUT a pg_sql still
    go to the legacy DB (-> [] until translated). pg_params (when the pg_sql uses
    $1..$n placeholders) are bound out-of-band -> injection-safe.

<!-- mios-src:00e4d6e1996e from usr/libexec/mios/mios-daemon:284-298 -->

### WS-A3

WS-A3: write a row to Postgres+pgvector via _pg_insert (parameterized) and
    return "" -- the agent-plane DB is pg now. Callers wrap this in
    `_db_post(_db_create(...))`; _db_post is a no-op stub (legacy DB retired), so
    returning "" makes the legacy write vanish cleanly with NO call-site change.
    `now_fields` (datetime cols) are omitted from the insert so the pgvector
    column DEFAULT now() applies; `now_fields`/`extra` are vestigial (were the
    legacy time::now()/clause shaping) and kept only for signature compat.

<!-- mios-src:3f4b22d20dc4 from usr/libexec/mios/mios-daemon:307-313 -->

### Best-effort highest GPU-utilization-% across NVIDIA GPUs...

Best-effort highest GPU-utilization-% across NVIDIA GPUs via
    nvidia-smi. Returns -1.0 when nvidia-smi is missing / errors / has no
    GPUs (degrade-open: caller treats <0 as 'no GPU signal').

<!-- mios-src:23c16fb456c0 from usr/libexec/mios/mios-daemon:429-431 -->

### Return {over_ceiling, load_per_core, gpu_util, reason}...

Return {over_ceiling, load_per_core, gpu_util, reason} using a
    ~PRESSURE_TTL_S cached probe (so 4 loops sharing one tick don't run
    nvidia-smi N times). over_ceiling is True only when a configured
    ceiling (>0) is exceeded. NEVER raises -- on any probe error it
    reports over_ceiling=False so the caller proceeds as today.

    Note: over_ceiling reflects the raw measurement; whether a tick is
    actually SKIPPED is the caller's decision gated on PRESSURE_SKIP.

<!-- mios-src:a175163db65b from usr/libexec/mios/mios-daemon:455-462 -->

### True when a tick of `loop_name` should be skipped for host...

True when a tick of `loop_name` should be skipped for host pressure.
    Writes a 'deferred (host pressure)' marker into state.pressure for
    operator visibility on EVERY over-ceiling observation (even when
    PRESSURE_SKIP is off, so the operator can see what WOULD be deferred
    before enabling). Returns True only when PRESSURE_SKIP is also set.

<!-- mios-src:c886ff1cb6d7 from usr/libexec/mios/mios-daemon:499-503 -->

### Call qwen3:0.6b-cpu (or whatever MODEL is) via the OpenAI...

Call qwen3:0.6b-cpu (or whatever MODEL is) via the OpenAI /v1 lane.
    Returns the model's text output (content with thinking fallback).
    Returns empty string on any error. Always uses CPU + keep_alive=-1.

    num_thread tunes the CPU inference width: the always-on BACKGROUND loop
    keeps the default 4 (~2c, light), while the FOREGROUND daemon-agent
    reasoner (the consolidated mios-cpu/reasoner lane) passes a wider count
    (8c/16t) so a pipeline turn bursts -- operator 2026-05-24 '2c background /
    8c foreground'. On the OpenAI /v1 surface (MiOS is /v1-only) the server owns
    thread/offload config, so these CPU knobs no longer shape the request.

<!-- mios-src:4fb25059c5ab from usr/libexec/mios/mios-daemon:552-562 -->

### First N normalized tokens of a summary -- a coarse...

First N normalized tokens of a summary -- a coarse fingerprint that
    treats near-identical classifications (same root cause, trivially
    different tail) as the same event.

<!-- mios-src:5533f84d88c9 from usr/libexec/mios/mios-daemon:644-646 -->

### Record a fingerprint hit; return True when an identical...

Record a fingerprint hit; return True when an identical fingerprint
    was seen within CLASSIFY_DEDUP_S (so the caller suppresses the heavy
    write and emits a count-only update instead). Degrade-open: if dedup is
    disabled (CLASSIFY_DEDUP_S<=0) it never suppresses.

<!-- mios-src:52a205ed4c16 from usr/libexec/mios/mios-daemon:657-660 -->

### Gate a repeat escalation of the SAME concern. Returns False...

Gate a repeat escalation of the SAME concern.

    Returns False while the concern is inside ESCALATION_COOLDOWN_S of its last
    escalation, and permanently once it has been escalated
    ESCALATION_MAX_ATTEMPTS times (the concern is parked and only logged after
    that). Degrade-open: a non-positive cooldown disables the gate entirely.

<!-- mios-src:e859dc613fb0 from usr/libexec/mios/mios-daemon:685-691 -->

### MODEL-DRIVEN

MODEL-DRIVEN: ask the micro-LLM whether `message_text` is the assistant
    refusing / hedging / fabricating instead of doing the work. Returns True or
    False when the judge classifies, or None to signal DEGRADE-OPEN (detector
    off, empty input, or judge lane unreachable/unparseable) so the caller skips
    this turn's check instead of fabricating a refusal verdict. No English-regex
    pre-filter gates the call -- every candidate response is judged.

<!-- mios-src:aed695c7769a from usr/libexec/mios/mios-daemon:853-858 -->

### MODEL-DRIVEN

MODEL-DRIVEN: ask the micro-LLM whether `assistant_text` claims a launch
    and, if so, what target was claimed. Returns {"app": <str>} when it is a
    verifiable claim with a usable target, {} when the model says "not a claim",
    or None to signal DEGRADE-OPEN (detector off or lane unreachable) so the
    caller skips this turn's claim check instead of fabricating one.

<!-- mios-src:1542a3000e0a from usr/libexec/mios/mios-daemon:1103-1107 -->

### Every LAUNCH_VERIFY_TICK_S seconds, scan recent OWUI chats...

Every LAUNCH_VERIFY_TICK_S seconds, scan recent OWUI chats for
    assistant launch-success claims and verify each via
    mios-window-active. Persist mismatches so the agent (and operator)
    can see which launches were false-success.

    Already-verified claims are deduped by (chat_id, app, claim_ts)
    via the persisted failures file (kept compact at <= 50 entries).

<!-- mios-src:b517a071fd91 from usr/libexec/mios/mios-daemon:1217-1223 -->

### 4B: resolve an OS-control NODE name -> its executor base...

4B: resolve an OS-control NODE name -> its executor base URL from the
    layered mios.toml [os_control.nodes.<node>].endpoint (vendor +/etc; /etc
    wins -- that's where the operator's real tailnet endpoint lives, NEVER the
    public repo). Returns '' when unknown/unset.

<!-- mios-src:347967eff82d from usr/libexec/mios/mios-daemon:1509-1512 -->

### True when fire_result shows the IN-SESSION executor...

True when fire_result shows the IN-SESSION executor (:11437) presented a
    window. The executor runs ON the operator's interactive desktop, so its
    "presented"/launched verdict is AUTHORITATIVE -- the Linux-side
    _verify_one_launch (pgrep + WSL window probe) cannot reliably see a
    Windows-host window and false-reports no-window for a Store game the executor
    already presented (operator 2026-05-30 Wreckfest). Parses the
    '[mios-windows] launched via in-session executor: {json}' line. Best-effort.

<!-- mios-src:701ffa62c36c from usr/libexec/mios/mios-daemon:1561-1567 -->

### FIRE a launch + run the success-check as ONE operation...

FIRE a launch + run the success-check as ONE operation, returning the
    consolidated verdict the agent reads: {app, fired, launched, verdict,
    ...}. NEVER raises. With `node` set -> delegates to that REMOTE node's
    executor (4B). Otherwise the LOCAL path: fire `mios-launch <app>` (the
    same command open_app's dispatcher renders) THROUGH THE BROKER (the only
    context with the WSLg display env), then POLL _verify_one_launch until the
    window is presented or attempts run out. The daemon is the SOLE firer (if
    it's unreachable the shim returns an honest error and the agent falls back
    to plain open_app).

<!-- mios-src:91b836feaf30 from usr/libexec/mios/mios-daemon:1585-1593 -->

### Call the local /v1 lane via its OpenAI-compatible...

Call the local /v1 lane via its OpenAI-compatible /v1/chat/completions
    endpoint with response_format=json_object (operator directive:
    'native for OpenAI API standards'). Returns parsed dict; {} on
    any failure (fail-open -- the loop just skips a tick).

<!-- mios-src:918195111427 from usr/libexec/mios/mios-daemon:1646-1649 -->

### Phase A.2

Phase A.2: kernel-event-driven mutation bus.

    Watches /var/lib/mios/* scratchpads + state dirs via inotify.
    Each mutation -> one DB event row with source=fs-watcher,
    kind=fs_change, payload={path, op, size, preview}. Other agents
    subscribe via `SELECT FROM event WHERE source = 'fs-watcher'
    AND ts > <last_seen>` instead of polling individual files.

    Debounces per (path, op) at 1Hz so a noisy write loop doesn't
    flood the event table. Tolerates missing/added dirs: creates the
    watched dirs at startup; warnings on add-watch failure don't kill
    the thread.

<!-- mios-src:8b97a21a4a64 from usr/libexec/mios/mios-daemon:1718-1730 -->

### Pull tool_call rows that landed AFTER a refine event in the...

Pull tool_call rows that landed AFTER a refine event in the
    same session. The refine event's payload doesn't directly link
    to a session_id (refine runs on the prompt, session is opened
    separately), so we fall back to a ts-window match: tool_calls
    within `lookback_after_min` minutes after the refine.
    Best-effort -- if the linkage misses, the satisfaction verdict
    is "no_tools_seen" rather than wrong.

<!-- mios-src:50272472478c from usr/libexec/mios/mios-daemon:2055-2061 -->

### SSOT

SSOT: map a dispatched tool name -> its post-check SIGNAL from the
    layered mios.toml [daemon.post_check] (vendor < /etc < per-user; later
    layer wins). The check IMPLEMENTATIONS live in this module keyed by
    signal name -- this table only decides WHICH check guards WHICH verb,
    so coverage is operator-tunable instead of baked into the dispatch.
    Returns {} when unreadable -> every verb degrades-open to bare
    tool_call.success.

<!-- mios-src:a1cfc02a4f17 from usr/libexec/mios/mios-daemon:2079-2085 -->

### Cross-check that a launch/focus verb actually put a window...

Cross-check that a launch/focus verb actually put a window on the
    operator's screen. A broker can return exit 0 after its sandbox died
    at credential bootstrap with the window never having appeared.

<!-- mios-src:e95dfe0ece2d from usr/libexec/mios/mios-daemon:2102-2104 -->

### Per-verb visible-outcome verification beyond...

Per-verb visible-outcome verification beyond tool_call.success.
    Dispatches the check implementation named by the verb's SSOT-declared
    signal (mios.toml [daemon.post_check]); the raw success column alone
    misses launches whose sandbox died after the broker collected exit 0.

    Returns dict {checked: <bool>, passed: <bool>, signal: <name>,
    detail?: <str>}. checked=False means no post-check applies to this
    verb (it is unlisted, or its signal has no implementation) -- the
    caller falls back to tool_call.success alone.

<!-- mios-src:67e2421e905e from usr/libexec/mios/mios-daemon:2188-2196 -->

### Batch-upsert directory_entry rows via the legacy DB....

Batch-upsert directory_entry rows via the legacy DB.

    Datetime handling: the legacy backend rejects bare ISO strings for
    TYPE datetime fields -- the canonical literal is
    `<datetime>"2026-05-19T..."`. We bypass _db_create for this
    batch path and format CREATE statements directly with the
    right literal shape. _db_create's now_fields mechanism works
    for indexed_at (= "now") but not for mtime (which needs to
    preserve the file's actual mtime).

    Counts: per-statement ERRs come back inside the 200-OK
    response. We walk the response list to count actual successes
    vs the previous bug where wrote == sent regardless.

<!-- mios-src:2590494ac9b9 from usr/libexec/mios/mios-daemon:2402-2414 -->

### Delete all directory_entry rows for this root_label, then...

Delete all directory_entry rows for this root_label, then
    insert the fresh entries. Safer than UPDATE-by-path because
    deleted files actually disappear from the index instead of
    persisting as stale rows.

<!-- mios-src:029b2a9ffa9c from usr/libexec/mios/mios-daemon:2506-2509 -->

### Operator 2026-05-21

Operator 2026-05-21: consolidate the stored log_digests into a concise
    ROLLING REPORT. Every MIOS_DAEMON_REPORT_S, read the recent med/high
    digests from the DB, ask the micro for ONE short report, and store it
    (state.json `report` + a log_digest report row). Cheap + idle-aware:
    skips the LLM entirely when nothing notable accumulated.

<!-- mios-src:a260a1d779cc from usr/libexec/mios/mios-daemon:2630-2634 -->

### The consolidated CPU lane (operator 2026-05-24...

The consolidated CPU lane (operator 2026-05-24 "mios-daemon-agent =
    mios-cpu/reasoner! CONSOLIDATE"): the always-on daemon-agent IS the CPU
    reasoning lane now. It REASONS on the CPU model (the same model/endpoint the
    retired mios-reasoner-cpu used -- qwen3:1.7b @ the CPU lane :11435, num_gpu 0)
    via the proven think=False llm_chat() path, GROUNDED in the live global
    journal/log telemetry the daemon already maintains. So it contributes a real
    second-opinion answer that is system-state-aware -- NOT the raw telemetry
    dump that forced the old fanout=false (the model folds telemetry in ONLY
    when the request concerns the system, and ignores it otherwise). Bursts to
    8c/16t (AGENT_THREADS) for the foreground turn. Empty -> the fan-out cleanly
    drops this secondary.

<!-- mios-src:aecd8d0e7b9c from usr/libexec/mios/mios-daemon:2724-2734 -->

### mios-directory-lookup -- query the cached directory map...

mios-directory-lookup -- query the cached directory map that
mios-daemon's index_loop maintains in PostgreSQL/pgvector.

Operator directive: "mios-daemon should be indexing directories for
directory maps for llms cache/lut/DB/etc for faster navigation/
recall!!". This shim is the read-side of that index. Sub-100ms
lookups (DB query) vs the 60ms+ live mios-find / fs_search.

The query is parameterized end-to-end: every external value (the
search substring + the --root / --ext / --kind filters) is bound
out-of-band via `mios-db --pg-json` ($1..$n through the pg v3 extended
protocol), never spliced into the SQL text. Only the integer LIMIT is
inlined (int()-coerced).

USAGE
  mios-directory-lookup <query> [--root <label>] [--ext <.md>]
                                [--kind <file|dir>]
                                [--limit N] [--json]

OUTPUT
  Default: human-readable list of ranked hits.
  --json:  {ok, query, hits: [{path, kind, size, mtime, summary,
                              root_label, score}, ...]}

<!-- mios-src:33c644c49e01 from usr/libexec/mios/mios-directory-lookup:4-27 -->

### Directory lookup against pgvector via the parameterized...

Directory lookup against pgvector via the parameterized
    `mios-db --pg-json` envelope (pg v3 extended protocol -- values bound
    out-of-band as $1..$n, never spliced into the SQL text). Returns the
    rows list, or None when pg is unreachable so the caller degrades open
    (empty result, no crash). strpos() substring match; ranking happens in
    the shared pass below. Only the integer LIMIT is inlined (int-coerced --
    text->int inference is unreliable on the bound path).

<!-- mios-src:607cb5481bdf from usr/libexec/mios/mios-directory-lookup:58-64 -->

### mios-discord-send -- DETERMINISTIC Discord post/DM helper....

mios-discord-send -- DETERMINISTIC Discord post/DM helper.

Posts a message to a Discord CHANNEL (--channel <id>) OR direct-messages a USER
(--user <@name|id>, or a NON-NUMERIC --channel value treated as a username): it
resolves the username via guild member-search, opens a DM channel, and posts.
So "send @someone a message on discord" is an ACTION the MiOS orchestrator performs
directly -- NOT something the executor narrates and lies about (operator
2026-05-22), and NOT a fabricated channel URL (operator 2026-06-10). Honest JSON
result so the caller reports the REAL outcome (sent / why-not), never a fake.

[redacted] (from /etc/mios/hermes/discord.env or env).
Target:  --user <@name|id> for a DM; --channel <id> (or non-numeric @name) ;
         else MIOS_DISCORD_DEFAULT_CHANNEL (mios.toml [identity].discord_channel_default).

Discord is the operator's own external service (a chat sink), not a cloud-AI
dependency -- consistent with Architectural Law 5 (no vendor-cloud AI URL).

<!-- mios-src:cb967b272be3 from usr/libexec/mios/mios-discord-send:4-20 -->

### mios-docgen -- FOSS, fully-offline document generation for...

mios-docgen -- FOSS, fully-offline document generation for MiOS (WS-4 P0).

The computer-use "Worker" half that PRODUCES artifacts (pptx / docx / xlsx /
pdf / html / md) instead of clicking through an office GUI. Built from MiOS
parts -- Pandoc (markdown <-> docx/pptx/html/pdf) + LibreOffice headless
(`soffice --convert-to`, the universal office-format converter) -- so it stays
FOSS + offline (NO Wide-Moat / BSL vendor stack, per aios-implementation-plan
section 0-B).

Two jobs, one contract:
  * `convert`  -- any input file -> any output format (LibreOffice for office
                  binaries; Pandoc for markup; auto-routed by extension).
  * `build`    -- author a NEW document from markdown/text/CSV source content
                  (Pandoc for docx/pptx/html/pdf; native CSV->xlsx via
                  LibreOffice). The agent writes markdown, we emit the binary.

DEFAULT-OFF / GATED + DEGRADE-OPEN (binding rule): the whole tool is gated by
[computer_use].docgen_enable (env MIOS_DOCGEN_ENABLE), default false. When off,
every subcommand returns a structured {"ok": false, "error": "...disabled..."}
JSON and exit 0 -- callers degrade gracefully (the verb is visible but inert)
rather than crashing. A missing backend binary likewise degrades to an honest
JSON error, never a stack trace.

SSOT: every tunable resolves from layered mios.toml (vendor < /etc < ~/.config)
[computer_use], overridable by MIOS_DOCGEN_* env. NO literals baked in code --
the defaults here are the documented fallbacks, mirrored in mios.toml.

USAGE
    mios-docgen convert <in-path> <out-path> [--to FMT]
    mios-docgen build  <out-path> [--from FMT] [--content TEXT | --content-file PATH | --stdin]
    mios-docgen formats
    mios-docgen help

EXAMPLES
    mios-docgen build /tmp/q3.docx --from markdown --content-file /tmp/q3.md
    mios-docgen build /tmp/deck.pptx --from markdown --stdin < deck.md
    mios-docgen build /tmp/sales.xlsx --from csv --content-file /tmp/sales.csv
    mios-docgen convert /tmp/q3.docx /tmp/q3.pdf

CONFIG (layered mios.toml [computer_use], or MIOS_DOCGEN_* env):
    [computer_use]
    docgen_enable      = false     # MASTER GATE (default off)
    docgen_pandoc      = "pandoc"   # pandoc binary / path
    docgen_soffice     = "soffice"  # LibreOffice headless binary / path
    docgen_pdf_engine  = ""         # pandoc --pdf-engine (empty -> route PDF via LibreOffice)
    docgen_timeout_s   = 120        # per-conversion timeout
    docgen_max_bytes   = 20000000   # refuse inputs larger than this (DoS guard)

<!-- mios-src:7bb49f11043e from usr/libexec/mios/mios-docgen:4-51 -->

### soffice --headless --convert-to <filter> --outdir <dir>...

soffice --headless --convert-to <filter> --outdir <dir> <src>.

    LibreOffice writes <src-stem>.<ext> into --outdir; we then move it to the
    requested out path. A private per-call profile dir avoids the singleton
    lock that makes concurrent/headless soffice silently no-op.

<!-- mios-src:0eca553fe8c5 from usr/libexec/mios/mios-docgen:167-172 -->

### mios dotfiles

mios dotfiles: project the mios.toml SSOT dotfiles to the operator's live HOME (ADR-0010).

Thin, read-only-by-default orchestrator over mios-theme-render:
  status        list registered surfaces, which apply to THIS platform, and
                whether each is up-to-date or would-change (engine dry-run).
  diff [names]  show what `sync` WOULD write to live HOME (writes nothing).
  sync [names]  apply every apply-eligible surface to live HOME (backup-safe,
  apply         HOME-scoped -- both guaranteed by the engine, not us).
An optional trailing surface-name list narrows diff/sync to those surfaces
(passed straight through to the engine); default is every registered surface.

<!-- mios-src:4cbe639cb630 from usr/libexec/mios/mios-dotfiles:6-16 -->

### Verb exit code

Verb exit code: a fatal engine error (rc 3 -- template/registry bug) is
    propagated; a HOME-scope refusal is a soft failure (rc 1) so scripts notice
    without aborting a run of many surfaces; otherwise success. Platform skips
    NEVER fail (degrade-open).

<!-- mios-src:7651059a182c from usr/libexec/mios/mios-dotfiles:130-133 -->

### Merge two lists of objects BY a stable `key` field (neither...

Merge two lists of objects BY a stable `key` field (neither input mutated):
    a foreign (base) element whose key is NOT in `owned_list` is PRESERVED in base
    order; an owned element whose key matches a foreign one deep-merges OVER it in
    place (owned wins, foreign sub-keys of that element preserved); an owned-only
    element is APPENDED after the foreign ones. The caller has already verified
    every element is a dict carrying `key` (see _keyed_object_list).

<!-- mios-src:2eb63af1001f from usr/libexec/mios/mios-dotfiles-render:212-217 -->

### Deep-merge the MiOS-`owned` subtree onto `base`...

Deep-merge the MiOS-`owned` subtree onto `base` (insertion-ordered):
    recurse where BOTH sides are objects; a MiOS-owned leaf/array/object-over-
    non-object wins; arrays are REPLACED wholesale at the owned pointer (index-
    merge is unsafe) EXCEPT at a pointer listed in _ARRAY_MERGE_KEYS whose two
    lists are keyed-object lists -- there they merge BY KEY (foreign elements
    preserved, owned wins per key); any key `owned` does not carry is preserved in
    base's order. New owned keys append after the foreign keys. `pointer` is the
    JSON pointer of the CURRENT node ("" at the root), extended per recursion so
    the array-merge lookup is position-exact.

<!-- mios-src:b81b99aba4d0 from usr/libexec/mios/mios-dotfiles-render:240-248 -->

### Splice the MiOS-owned JSON subtree (owned_text) onto...

Splice the MiOS-owned JSON subtree (owned_text) onto base_text (a foreign
    JSONC document), returning pretty JSON text (base indent, ensure_ascii=False,
    trailing newline). Foreign keys byte-preserved; owned wins; arrays replaced
    wholesale EXCEPT at an _ARRAY_MERGE_KEYS pointer (merged by key -- foreign
    elements preserved). A base that will not parse => exit 2, write NOTHING (never corrupt
    an unreadable foreign file). An owned subtree that will not parse is a MiOS
    template bug => exit 3.

<!-- mios-src:24639dc0dac1 from usr/libexec/mios/mios-dotfiles-render:266-272 -->

### Parse a git-config/INI document into an ORDERED line-model...

Parse a git-config/INI document into an ORDERED line-model + an index.
    model: list of per-physical-line dicts. Every line keeps `raw` verbatim (so
    a foreign line round-trips byte-for-byte); a kv line additionally carries
    lead/key/sep/val/eol so an OWNED value can be replaced in place without
    disturbing its neighbours. index: {(section_lower, subsection_or_None,
    key_lower) -> model idx} for each kv line, so a PRESENT owned key is found
    and a foreign key is left untouched. Section headers and #/; comments are
    preserved as opaque `other` lines. Uses splitlines(keepends=True) so each
    line's own terminator (CRLF/LF/none) survives the round-trip.

<!-- mios-src:9083e7fbd8d5 from usr/libexec/mios/mios-dotfiles-render:301-309 -->

### Reverse a @MIOS:<sec>_<key>@ token to the mios.toml...

Reverse a @MIOS:<sec>_<key>@ token to the mios.toml (section, key) that
    feeds it. The gitconfig template's value tokens are identity_* (always
    injected by _resolved_for) or <section>_* (the surface's settings section);
    returns None for anything else (e.g. a bare palette token), so a literal
    owned value is never treated as operator-set.

<!-- mios-src:226a07429ce8 from usr/libexec/mios/mios-dotfiles-render:419-423 -->

### True iff the operator EXPLICITLY set [section].key in a...

True iff the operator EXPLICITLY set [section].key in a host/user overlay
    -- the merged value differs from the vendor default AND is non-empty. The
    seed-or-enforce policy ENFORCES such an operator choice over an existing
    foreign value, but SKIPS a mere vendor default (never stomps a foreign value
    with a shipped default).

<!-- mios-src:7eeade9531b6 from usr/libexec/mios/mios-dotfiles-render:452-456 -->

### Write policy for a PRESENT owned key. seed-or-enforce...

Write policy for a PRESENT owned key. seed-or-enforce (default): enforce
    ONLY an operator-set SSOT value, else SKIP (safety-first). `enforce`: always
    overwrite. `seed-only`: never overwrite an existing value.

<!-- mios-src:e7ddbf11e3ad from usr/libexec/mios/mios-dotfiles-render:465-467 -->

### ini-merge apply

ini-merge apply: the owned git keys upserted into the LIVE gitconfig (or
    an empty doc when the file does not exist yet), so the operator's foreign
    credential/signing/remote lines survive a theme/identity refresh.

<!-- mios-src:3c2aab26ce9f from usr/libexec/mios/mios-dotfiles-render:670-672 -->

### name ->...

name -> _Surface(name,template,target,section,kind,fixture,policy) from
    mios.toml [dotfiles.registry.*], in authoring order. Template surfaces are
    element-for-element equal to the former hardcoded SURFACES literal. Fails
    LOUD (exit 3) on: an empty/absent registry (the backstop for a botched
    migration -- a vacuously-'green' gate is impossible: no registry -> no run);
    a surface name containing a '.'; a missing template/target; an UNKNOWN (or
    not-yet-implemented) kind; a merge kind missing its fixture.base/expected
    (no ungated merge surface -- preserves the no-vacuous-green stance).

<!-- mios-src:a828e75bf44b from usr/libexec/mios/mios-dotfiles-render:804-811 -->

### Render each named surface (default: all) and write it to...

Render each named surface (default: all) and write it to its live-HOME
    target, backing up any existing file first. A template surface writes the
    whole rendered file; a json-merge surface MERGES its owned subtree onto the
    live file (foreign keys preserved). `diff` / --dry-run writes nothing (prints
    WOULD-write / up-to-date). A surface with no HOME target for this platform is
    a no-op skip.

<!-- mios-src:18ebda9f06ba from usr/libexec/mios/mios-dotfiles-render:1056-1061 -->

### This file says "lossless" at the top, and it was not. `env`...

This file says "lossless" at the top, and it was not. `env` prints a
multi-line value across several lines, and the old `grep '^MIOS_'` kept only
the FIRST of them -- continuation lines do not start with MIOS_, so the rest
was dropped without a word. Measured: MIOS_OWUI_SYSTEM_PROMPT_TEMPLATE is 2633
characters over 42 lines and reached the baseline as the nine characters
"# MiOS AI"; MIOS_DOCS_BOILERPLATE_WHAT_MIOS_IS lost 214 of its 288 (task_3364).
check_value_aliases trusts this output, so a gate was comparing mutilated
values confidently.

Refusing to emit a multi-line value was the other option and is worse here:
two of them exist, so the baseline could never be generated at all. The value
is escaped onto one line instead, which keeps every consumer's KEY=value parse
working and puts the whole value in the committed baseline's diff. Backslash
is escaped first, so the transform is reversible.

`read -d ''` over `env -0` is the only way to read a value whose own content
contains newlines. An awk with RS="\0" is not portable across awk
implementations, and this script must not grow a python dependency.

<!-- mios-src:514143ea70d7 from usr/libexec/mios/mios-env-snapshot:30-47 -->

### ONE resolver, always the shell twin. This snapshot is a...

ONE resolver, always the shell twin. This snapshot is a committed BASELINE that
CI re-derives and diffs, so it must depend on the tree alone. Preferring the
native mios-resolver when tools/native/target/debug happened to be built made
the output depend on BUILD STATE: the Rust path emits shell-QUOTED values
(MIOS_X='a b') while the shell path emits raw ones (MIOS_X=a b), so the same
commit produced two different baselines and whichever machine regenerated last
broke the gate for the other. The twins are proven equivalent by
check_resolver_twin_parity / check_resolver_twin_equivalence; this file just
needs the reproducible one.

<!-- mios-src:7528b53cd113 from usr/libexec/mios/mios-env-snapshot:61-69 -->

### 'MiOS' fine-tuner -- HARDWARE-AGNOSTIC LoRA/SFT of a small...

'MiOS' fine-tuner -- HARDWARE-AGNOSTIC LoRA/SFT of a small role model.

Trains a LoRA adapter on the distilled corpus from `mios-finetune-dataset`, then
exports it as a GGUF LoRA adapter for the llama.cpp / mios-llm-light lane. MiOS ships the components
to train on ANY hardware combination, so this auto-detects the compute device:

    NVIDIA CUDA  ->  4-bit QLoRA (bitsandbytes) when available, else bf16 LoRA
    AMD ROCm     ->  bf16/fp16 LoRA (torch+ROCm; bitsandbytes 4bit only if present)
    Apple MPS    ->  fp16 LoRA on the unified-memory GPU
    CPU-only     ->  fp32 LoRA (works everywhere; slow -- a fallback, not a goal)

Portable core = PyTorch + transformers + PEFT + TRL SFTTrainer (run anywhere);
Unsloth is used only as an OPTIONAL CUDA fast-path when present + prefer_unsloth.

OPERATOR-GATED + OFFLINE: the framework (see finetune/requirements.txt) and the HF
base ([finetune].hf_base) are one-time fetches you do once; thereafter training is
fully local. This is NOT an agent verb -- nothing in a chat turn can start it.

Honest: every stage guards its prerequisites and reports what actually happened
(trained / why it could not). Run with --dry-run to validate config + device +
deps + dataset WITHOUT training (no GPU work).

<!-- mios-src:41f63e103b01 from usr/libexec/mios/mios-finetune:4-25 -->

### 'MiOS' fine-tune dataset builder -- SELF-DISTILLATION, no...

'MiOS' fine-tune dataset builder -- SELF-DISTILLATION, no hardcoded English.

Builds a supervised fine-tuning (SFT) corpus for a small MiOS role model by
distilling a STRONG local teacher into it. The pipeline is deliberately grounded
in the LIVE system surface, so the dataset tracks the real install and carries no
hand-written topic list (operator rule: no hardcoded English / topics / keywords):

  1. Pull the LIVE verb catalog (name + description) from the running agent-pipe
     (/v1/verbs/openai-tools) -- the real routing surface, 61 verbs today.
  2. For each capability, the TEACHER generates N short, diverse, realistic user
     requests that would route to it (the model writes the English, never this file).
  3. The TEACHER also generates queries for the non-dispatch intent classes
     (pure chat, web/world lookup, broad multi_task) seeded by the schema's OWN
     definitions -- again, the model writes them.
  4. (optional) Mine REAL operator queries already stored in the knowledge table.
  5. Each query is LABELLED by the teacher against the live refine schema + catalog
     -> a refined-intent JSON matching exactly what the pipe parses.
  6. Emit chat-format JSONL ({"messages":[system,user,assistant]}) ready for TRL
     SFTTrainer / Unsloth.

Honest by construction: every example is the teacher's real output; nothing is
fabricated. Run with --limit N for a fast smoke check (a couple of capabilities).

Config: [finetune] in mios.toml (layered: vendor < /etc/mios < ~/.config/mios),
overridable by MIOS_FINETUNE_* env. Needs only stdlib + the running pipe.

<!-- mios-src:9eab59fc7fba from usr/libexec/mios/mios-finetune-dataset:4-29 -->

### Teacher chat over the OpenAI /v1 path (mios-llm-light). A...

Teacher chat over the OpenAI /v1 path (mios-llm-light). A legacy non-/v1
    chat shape silently 404'd here, starving the dataset generator.
    enable_thinking:False keeps qwen out of a think pass; for JSON we
    set response_format=json_object and read content OR reasoning_content (gemma4
    + response_format emits the JSON into reasoning_content with empty content --
    same drift class as the 2026-06-09 swarm/refine fixes). operator 2026-06-10.

<!-- mios-src:05f272ced1d0 from usr/libexec/mios/mios-finetune-dataset:89-94 -->

### First list value anywhere in a parsed JSON value (recurses...

First list value anywhere in a parsed JSON value (recurses dicts). Handles
    the {"requests": [...]} object the json_object response grammar forces (a bare
    array literal is rejected by that grammar), plus any single-key wrapper.

<!-- mios-src:6b1ed994b68f from usr/libexec/mios/mios-finetune-dataset:211-213 -->

### 'MiOS' fine-tune server -- serve a trained role adapter on...

'MiOS' fine-tune server -- serve a trained role adapter on ANY hardware.

Loads the base + the LoRA adapter from [finetune] (base+PEFT, the clean artifact that
works everywhere transformers runs) and exposes the OpenAI /v1 surface MiOS speaks
(MiOS is /v1-only):

  POST /v1/chat/completions    (OpenAI-compatible)
  GET  /v1/models , GET /healthz

So adopting the fine-tuned refiner is a one-line config change -- point the pipe's
refine endpoint at this server (e.g. MIOS_REFINE_ENDPOINT=http://127.0.0.1:11438) and
its model at [finetune].output_tag. Deliberately a STANDALONE helper, NOT auto-enabled:
the trained 2B served via transformers is correct but slower than the baked llama.cpp
lane on the hot path, so use it to A/B-test now; switch production to it once the
mainline llama.cpp GGUF path serves the adapter (then the same adapter runs fast).

Hardware-agnostic (auto cuda/rocm/mps/cpu). Stdlib HTTP only -- no extra deps beyond the
fine-tune venv (torch/transformers/peft). Run with the venv python:
  /var/lib/mios/finetune/venv/bin/python /usr/libexec/mios/mios-finetune-serve

<!-- mios-src:9087d5d282a3 from usr/libexec/mios/mios-finetune-serve:5-24 -->

### mios-firecrawl -- SCRAPE a web page to clean markdown via...

mios-firecrawl -- SCRAPE a web page to clean markdown via the LOCAL,
self-hosted Firecrawl API (the firecrawl-api member of the mios-webtools pod,
127.0.0.1:3002; offline-first, no cloud dependency -- Architectural Law 5).

WHY a SECOND fetch verb beside `crawl` (crawl4ai): they are DIFFERENT engines
with different strengths, and the operator wants ALL web tools globally
available to every agent. crawl4ai drives the local Chrome over CDP (best for
JS-heavy / login-walled pages that the standing browser already has state for);
Firecrawl is a dedicated scraper that returns very clean, LLM-ready markdown and
reliably renders news/article indexes (verified 2026-05-25: a scrape of a live
news index returned that day's real, dated headlines where SearXNG general
search returned only dictionary/brand junk). Give the agent both; let it pick.

SSOT: MIOS_FIRECRAWL_URL (rendered from mios.toml [ports].firecrawl). The
localhost:3002 fallback is the only literal, matching the pod publish.

Returns the SAME shape as mios-crawl so callers/ground-loops are uniform:
{success, engine, url, title, markdown, links}.

<!-- mios-src:1efd8454f24e from usr/libexec/mios/mios-firecrawl:4-22 -->

### Firecrawl down (pod/image not up) -> fall back to...

Firecrawl down (pod/image not up) -> fall back to mios-web-extract so
    web_scrape still GROUNDS on real page content (degrade-open, never
    fabricated). Returns {} if the fallback also fails.

<!-- mios-src:bc896cbc1344 from usr/libexec/mios/mios-firecrawl:132-134 -->

### mios-gen-role-system -- SSOT-driven AIOS-native role SYSTEM...

mios-gen-role-system -- SSOT-driven AIOS-native role SYSTEM generator.

Renders each MiOS agent role's SYSTEM prompt from the SSOT -- the layered
mios.toml `[agents.<name>]` config + the LIVE verb/skill/recipe catalog +
the A2A peer surface -- so the role models are intrinsically AIOS-native:
they NATIVELY decompose multi-faceted requests and delegate across the A2A
fleet, with NO hardcoded orchestration. The output is the SINGLE source
consumed by BOTH the mios-llm-light/Modelfile SYSTEM (the compiled-in fallback)
AND the agent-pipe per-role injection -- one SSOT, no drift, regenerated on
every image build.

NO HARDCODES: the tool surface + fleet awareness are read from the live SSOT
(mios.toml + /v1/verbs + a2a-peers.json), never written as literals. The only
static text is the AIOS-node IDENTITY (a role principle, not live env -- so it
is compatible with the no-context-injection rule, which is scoped to ENV
discovery, exactly like _recall_knowledge's allowed prior-answer injection).

Usage:  mios-gen-role-system [--out DIR] [--print ROLE] [--dry-run]

<!-- mios-src:ee2bfe7efb26 from usr/libexec/mios/mios-gen-role-system:4-22 -->

### The LIVE verb/skill/recipe catalog (names + one-liners)....

The LIVE verb/skill/recipe catalog (names + one-liners). Read from the
    running agent-pipe so it's never hardcoded and always current. Degrade-open
    -> [] (the model still gets its identity + role; tools are discovered at
    runtime via tool_search anyway).

<!-- mios-src:daf66e54e0b2 from usr/libexec/mios/mios-gen-role-system:55-58 -->

### The vendor matrix is SSOT, never a literal here (Law 7)....

The vendor matrix is SSOT, never a literal here (Law 7). [gpu.cdi.<vendor>]
gives each vendor its AddDevice= value and the CDI spec filenames whose
presence in CDI_DIR proves it is live; [gpu.vendors] switches one off without
deleting its wiring. Emits one TAB-separated `vendor<TAB>device<TAB>specs`
row per enabled vendor, in SSOT-name order.

<!-- mios-src:b8aa3600e24f from usr/libexec/mios/mios-gpu-passthrough:32-36 -->

### Degrade safe, not open: an empty matrix means the SSOT read...

Degrade safe, not open: an empty matrix means the SSOT read failed, and
rewriting every drop-in from it would silently strip GPU access from lanes
that had it. Leave the existing wiring alone and say why.

<!-- mios-src:96e4d40c3e7e from usr/libexec/mios/mios-gpu-passthrough:98-100 -->

### mios-handoff -- live state migration between model...

mios-handoff -- live state migration between model endpoints.

P5.5 (operator 2026-05-28 Hermes v2026.5.28 brief): "transition from a
massive model ... to a lightweight local model to complete routine
processing and save costs ... transfers the active execution session
... every conversation state, current tool output, and active variable
is serialized and migrated live to the target model without resetting
the workflow context."

In MiOS terms: pass the session's CURRENT A2A-context blackboard (the
shared scratchpad rendered as A2A Message history grouped by contextId,
served at /a2a/contexts/<ctxid>) + an optional handoff note to the
target peer/skill via the existing /v1/a2a/dispatch. The target receives
a clearly-framed "handoff take-over" message + can also fetch
/a2a/contexts/<ctxid> directly for the full history.

USAGE
  mios-handoff --to <peer_or_skill> --context-id <ctxid> [--goal <text>]
  mios-handoff --skill <skill> --context-id <ctxid> [--goal <text>]

DIFFERENCE FROM mios-a2a-delegate:
  delegate = "do this side task" (the calling agent stays in control)
  handoff  = "take over from here" (target inherits the session state
             and continues the workflow). Practically: the message is
             prefixed with a TAKE-OVER frame + carries a context summary
             pulled from /a2a/contexts/<ctxid>, so the target replies as
             the new owner of the workflow rather than producing a
             standalone answer to a delegated subtask.

<!-- mios-src:7b9b70d857c0 from usr/libexec/mios/mios-handoff:4-32 -->

### Yield (lineno, snippet) for a dated ATTRIBUTION baked into...

Yield (lineno, snippet) for a dated ATTRIBUTION baked into a .py STRING
    literal -- the hole that a date inside served CSS/JS-comment text or inside a
    system-prompt string slips through (the #-comment scan never sees string
    content). A date used as a VALUE is legitimate and exempt: a standalone or
    leading literal (a protocol-version id or a date config value is quote-led, so
    the char before it is the opening quote) or one glued into a URL/slug/identifier
    (preceded by a non-space joiner). The forbidden pattern is a date sitting in
    running prose -- structurally, one preceded by WHITESPACE and never the head of
    its own literal -- which is exactly how an `<attribution> <date>` credit reads.
    The discriminator is positional, not lexical (no keyword/English gate).

<!-- mios-src:280d6189a09b from usr/libexec/mios/mios-hardcode-lint:54-63 -->

### A GENERATED file is exempt from the timeless-comment rule...

A GENERATED file is exempt from the timeless-comment rule: its
comments are PROJECTED from mios.toml `comment = "..."` values,
where a date is a value and legitimately exempt (the lint is
value-aware). It only becomes a #-comment once rendered, and a
"DO NOT EDIT" file cannot be hand-corrected -- the fix belongs
in mios.toml. Keyed off the file's own generated marker rather
than an allowlist, so it can never drift out of date.
Both tokens, and only in the file's own top banner -- a
GENERATOR mentions "DO NOT EDIT" in the header it EMITS, and
generators are authored files that must stay linted.

<!-- mios-src:ffc9278c7b3d from usr/libexec/mios/mios-hardcode-lint:322-331 -->

### In-place patch of gateway/platforms/discord.py to add...

In-place patch of gateway/platforms/discord.py to add progressive
"thinking" reactions on the operator's Discord message during agent
processing.

Operator directive 2026-05-18: "also add more reactions to the
MiOS-Hermes Discord bot--Should be using more discord reactions to
show it's thinking!"

Upstream hermes-agent's Discord gateway emits exactly two reactions:
  on_processing_start    -> 👀 (single "looking" emoji)
  on_processing_complete -> ✅ / ❌

That gives the operator no visibility into what stage the agent is in
mid-run. This patch enriches the reaction surface with a progressive
sequence:
    📡 (received)          immediate
    🧠 (thinking)          after 2s if still processing
    🛠️ (using tools)       after 8s if still processing
    ⏳ (still working)      after 20s if still processing
    ✅ / ❌ (final)         on completion (and all phase reactions
                            are cleared first so the final outcome
                            stands alone)

A background asyncio.create_task() drives the progression so the
gateway's normal flow isn't blocked. The task is stashed on the
gateway instance keyed by Discord message id so concurrent
in-flight messages each get their own task that the matching
on_processing_complete can cancel.

Idempotent: rerunning is a no-op once the marker comment is present.
Safe: if Discord's add_reaction / remove_reaction fail (rate limit,
missing perm), each call already swallows the exception in the
existing _add_reaction / _remove_reaction helpers, so the progression
degrades silently.

Usage:
    hermes-discord-reactions-patch.py /path/to/discord.py

<!-- mios-src:367c2094d010 from usr/libexec/mios/mios-hermes-discord-reactions-patch:3-40 -->

### mios-hermes-init-hook -- Hermes pre_llm_call hook that...

mios-hermes-init-hook -- Hermes pre_llm_call hook that injects a
fresh environment-probe snapshot on the first turn of every session.

Operator directive 2026-05-15: "MiOS-Agents should be aware of all
systems; and be able to launch apps regardless of environment ...
the Agents dispatched Globally Should be informed of the environment
via an agent init/probe pass on first query".

WIRE PROTOCOL (from agent/shell_hooks.py)
-----------------------------------------
stdin: JSON. For pre_llm_call:
    {"hook_event_name": "pre_llm_call",
     "session_id": "...", "user_message": "...",
     "conversation_history": [...], "is_first_turn": true|false,
     "model": "...", "platform": "..."}

stdout: JSON. To inject context for pre_llm_call:
    {"context": "<text>"}
Anything else (incl. empty / non-JSON) is treated as no-op.

BEHAVIOUR
---------
- is_first_turn != true -> silent no-op (hook fires every turn but
  only injects on the first one).
- is_first_turn == true -> shell out to `mios-env-probe --brief`,
  wrap the output as the injected context.
- mios-env-probe missing or errors -> silent no-op (NEVER block the
  conversation; the hook is best-effort awareness).

<!-- mios-src:0906fcde5232 from usr/libexec/mios/mios-hermes-init-hook:4-32 -->

### mios-hermes-tail -- background tail of hermes-agent.service...

mios-hermes-tail -- background tail of hermes-agent.service journal,
extracts in-flight delegate_task + tool-call activity, writes state
file the OWUI mios_sidecar Filter polls for real-time chat emission.

Operator directive 2026-05-16: "MiOS-Agent (the OWUI CPU
Agent/refinement Agent) waits for answers from anywhere, prints using
OWUIs emitters functions the current global statuses from
Hermes-Agent(s)/Sub-Agents" -- this is the bridge.

Flow:
  hermes-agent.service journal
    -> mios-hermes-tail (this script, runs as root w/ journal access)
       -> writes /var/lib/mios/hermes-tail/latest.json
          -> mios_sidecar Filter (runs as mios-open-webui inside OWUI)
             polls the JSON, emits status events via __event_emitter__
             -> operator sees real-time "what hermes is doing" in chat

State file shape (atomic write):
  {
    "ts": <unix-epoch-of-last-update>,
    "events": [
      {"ts": ..., "kind": "delegate_spawn", "detail": "3 subagents (find, probe, windep)"},
      {"ts": ..., "kind": "tool_call",      "detail": "terminal: mios-find beamng"},
      {"ts": ..., "kind": "subagent_done",  "detail": "find (qwen3:1.7b) returned"},
      {"ts": ..., "kind": "synthesis",      "detail": "aggregating 3 results"},
      ...
    ],
    "active_session_count": <int>,
    "inflight_subagents": <int>
  }

Rolling buffer: last 20 events OR 5 minutes, whichever is smaller.
Cheap: pure journal tail + regex, no LLM call.

<!-- mios-src:f82ddbde587b from usr/libexec/mios/mios-hermes-tail:4-37 -->

### mios-ingest -- OFFLINE local-source ingestion into the...

mios-ingest -- OFFLINE local-source ingestion into the viking:// vault.

P5.1 (OpenHuman ingestion, offline core): OpenHuman polls external SaaS
(Gmail/Slack/GitHub/...) -- that needs cloud creds + contradicts MiOS's
offline-first principle, so the EXTERNAL pollers are deliberately NOT built.
This is the OFFLINE half that IS aligned: ingest LOCAL sources (a directory of
notes/docs/markdown the operator already has on disk) into the second-brain
vault, tiered (L0/L1 via mios-summarize) so viking:// can navigate them and the
knowledge recall can surface them. Same "compact + structure + make navigable"
value, pulling from local files instead of cloud APIs.

Each ingested doc becomes a `knowledge` row (q = derived title/L0, answer =
the L1 overview + a pointer to the source path; the raw stays on disk as L2).
Idempotent per (source_path): re-ingesting updates rather than duplicates.

USAGE
  mios-ingest --path /var/home/mios/notes            # a directory (recursive)
  mios-ingest --file /var/home/mios/notes/plan.md    # one file
  mios-ingest --path DIR --ext md,txt --max 50 --dry-run

Output JSON: {ok, ingested, skipped, sources:[...]}.
Offline: only mios-summarize (local CPU lane) + local pgvector. No network.

Storage: Postgres+pgvector via mios-pg-query. File-derived values (source path,
title/L0, L1 overview, embedding vector) are bound OUT-OF-BAND through the
parameterized extended protocol (mios-pg-query --exec-json $1..$n), never spliced
into SQL text; only trusted config identifiers (table/column names) stay inline.

<!-- mios-src:9ca39b49b2fd from usr/libexec/mios/mios-ingest:4-31 -->

### Run one SQL statement on pgvector via mios-pg-query...

Run one SQL statement on pgvector via mios-pg-query (pure-python pg wire
    client -- no psql/psycopg/podman). Rows -> lists of string columns (psql -At).
    Raises RuntimeError on a backend error so the caller's try/except can record
    the per-source failure (matches the old _sql contract). For statements that
    carry external (file-derived) values use _pg_param() instead -- it binds them
    out-of-band so nothing is ever spliced into SQL text.

<!-- mios-src:af29b1bf2fce from usr/libexec/mios/mios-ingest:53-58 -->

### Run one or more parameterized statements atomically on...

Run one or more parameterized statements atomically on pgvector via
    `mios-pg-query --exec-json`, which reads a JSON envelope on STDIN and binds
    each param OUT-OF-BAND through the pg v3 extended protocol -- no string
    splicing, no escaping. `statements` is a list of {"sql": "... $1 ...",
    "params": [...]}; a multi-element list runs as an atomic BEGIN/COMMIT batch
    (used for the delete-then-insert upsert). Strings/vectors/text bind as params;
    INTEGER values (LIMIT, interval counts) must stay inline in the SQL. A vector
    is bound as a TEXT param with a ::vector cast in the SQL. Raises RuntimeError
    on a backend error so the caller's try/except records the per-source failure
    (matches _pg's contract).

<!-- mios-src:8144ea75d9d2 from usr/libexec/mios/mios-ingest:67-76 -->

### Single-vector embed via the light-lane OpenAI...

Single-vector embed via the light-lane OpenAI /v1/embeddings
    (mios-llm-light :8500, nomic-embed-text, 768-dim) -- the SAME model+endpoint
    agent-pipe uses so cosine recall is comparable. MiOS is /v1-only: the OpenAI
    shape ({input} -> {data:[{embedding}]}). Returns a list[float] or None on ANY
    failure (network down, light lane swapping, bad shape) -- the caller then stores
    the row with emb NULL (still persisted + auditable, just not recallable). NEVER
    raises: embedding is best-effort and must not fail an ingest. Stdlib-only
    (urllib) to keep this offline tool dependency-free.

<!-- mios-src:0dccefa51bf0 from usr/libexec/mios/mios-ingest:96-103 -->

### mios-kg -- Personal Knowledge Graph CLI for the MiOS agent...

mios-kg -- Personal Knowledge Graph CLI for the MiOS agent stack.

Phase C.1 of the AgentOS roadmap. Manages the operator-preference data in
PostgreSQL/pgvector (person / app_install / alias + resolves_to natural-key
edge). The agent-pipe's kg_lookup() helper queries the same tables (JOIN on
app_id) to resolve ambiguous noun phrases to concrete launch targets. Mappings
are PER-OPERATOR data -- this CLI is the interface, never the source. NO
hardcoded English aliases in this codebase. (The legacy BSL 1.1 store is retired.)

Subcommands:
  bootstrap                Create the person row (from $USER /
                           $MIOS_IDENTITY_USERNAME) + ingest mios-apps inventory
                           into app_install rows. Idempotent.
  alias add <phrase> <target>
                           Add an alias. <target> can be:
                             app:<short_name>   (resolves to app_install)
                             "<short_name>"     (bare = app:<short_name>)
  alias rm <phrase>        Remove an alias.
  alias list               List all aliases + their resolves_to.
  lookup <phrase>          Print the lookup result for <phrase>.
  apps                     Print known app_install rows.
  who                      Print the person row.

Fully local: routes all reads + writes through the shared
/usr/libexec/mios/mios-db --pg-json CLI, which binds every value OUT-OF-BAND via
mios-pg-query's extended protocol (no value is spliced into SQL -> injection-
safe). resolves_to / app_install / alias / person tables are defined in
/usr/share/mios/postgres/schema-init.sql.

<!-- mios-src:d00cf08f5e5b from usr/libexec/mios/mios-kg:4-32 -->

### Run a parameterized statement / atomic batch via `mios-db...

Run a parameterized statement / atomic batch via `mios-db --pg-json` and
    return stdout. Degrade-open: any error -> '' (a DB hiccup never breaks the
    CLI).

<!-- mios-src:676791315568 from usr/libexec/mios/mios-kg:47-49 -->

### mios-knowledge-add -- register a markdown file (or...

mios-knowledge-add -- register a markdown file (or directory of
markdown files) as files in OWUI's `file` table and link them into a
named Knowledge collection. The collection becomes RAG-able by the
MiOS-Agent model row's meta.knowledge binding.

Operator directive 2026-05-17: "make sure there's tools to compact
all this and artifact it natively for OWUI knowledge/database".
Pairs with `mios-compact` (which produces digests at
/var/lib/mios/compacted/digest-*.md) -- this helper drops them
into a "MiOS Session Memory" collection so the agent can RAG over
prior sessions.

Distinct from `mios-owui-apply-knowledge` which is specifically for
the FHS-sourced canonical docs (refreshes on hash drift). This one
takes ARBITRARY operator-supplied or daemon-produced files.

Usage:
  mios-knowledge-add <file-or-dir> [options]

Options:
  --collection <name>     Target collection name. Default:
                          "MiOS Session Memory".
  --description <text>    Used when creating the collection (first
                          add). Ignored if collection already exists.
  --replace               If the same filename already exists in the
                          collection, replace its content (default:
                          add a new entry with timestamped suffix).
  --tag <tag>             Add a tag to the file's meta. Repeatable.
  --dry-run               Print what would be done, exit 0.
  --db <path>             Override the OWUI db path (default:
                          /var/lib/mios/open-webui/webui.db).
  --attach-to <model_id>  Also bind the collection to the named
                          OWUI model row's meta.knowledge so the
                          model RAGs over it automatically. Default
                          target: 'mios-agent' (also tries
                          'mios_agent.mios-agent'). Pass empty
                          ('--attach-to ""') to skip binding.

Examples:
  mios-knowledge-add /var/lib/mios/compacted/digest-2026-05-17T21-40Z.md
  mios-knowledge-add /var/lib/mios/compacted/ --collection "MiOS Session Memory"
  mios-knowledge-add /tmp/incident-notes.md --tag incident --tag 2026-05-17

Files are stored with meta.managed_by = "mios-knowledge-add" so
mios-cache-clear preserves them (it spares non-MiOS-managed entries
based on this marker).

Exit codes:
  0 = files registered + linked (or no-op if --dry-run)
  1 = db unreachable / write failure
  2 = no files matched the given path
  64 = bad usage

<!-- mios-src:84c4d2c1b251 from usr/libexec/mios/mios-knowledge-add:4-56 -->

### mios-knowledge-search -- query OWUI's RAG knowledge...

mios-knowledge-search -- query OWUI's RAG knowledge collections
from outside OWUI.

Operator directive: "OWUI's RAG databases are in-the-loop for all
agents globally and indexable for references". OWUI's pre-LLM
call hits its knowledge collections automatically when a model
has knowledge attached, but sub-agents (Hermes, opencode,
mios-daemon-agent, ...) running INSIDE the agent-pipe -> sub-agent
dispatch chain had no way to issue their own RAG queries -- so
they couldn't fetch context mid-tool-loop.

This shim wraps OWUI's /api/v1/retrieval/process/query endpoint
(the same engine OWUI's own pre-LLM call uses) so any agent can:

  - Search across all known collections (default).
  - Scope to a specific collection by name or id.
  - Receive ranked chunks WITH source metadata for citation in
    the polished reply.

USAGE
  mios-knowledge-search <query> [--collection NAME_OR_ID]
                                [--top-k N] [--threshold F]
                                [--json]

OUTPUT
  Default: human-readable bullet list of top hits.
  --json:  {ok, query, collection, hits: [{score, source, snippet}, ...]}

EXIT CODES
  0  query ran (any hits >= 0)
  1  no collections matched / OWUI unreachable / auth missing
  2  bad arguments

<!-- mios-src:6e429aa5df57 from usr/libexec/mios/mios-knowledge-search:5-37 -->

### Resolve the OWUI admin api_key. Prefer a broker-readable...

Resolve the OWUI admin api_key. Prefer a broker-readable secret
    (MIOS_OWUI_API_KEY env, or /etc/mios/owui-admin.key written 0640
    root:mios-ai by mios-owui-bootstrap-admin) so the agent-pipe / mios-ai user
    does NOT need read access to webui.db (0640 mios-open-webui) -- that
    permission gap is exactly why this returned '' and silently degraded to the
    pgvector fallback. Falls back to a read-only webui.db lookup for an admin/
    root caller. Never mutates webui.db.

<!-- mios-src:76e8fa744c57 from usr/libexec/mios/mios-knowledge-search:147-153 -->

### POST /api/v1/retrieval/query/collection. Returns the parsed...

POST /api/v1/retrieval/query/collection. Returns the parsed
    response dict or {"error": "..."}.

    `collection_ids` is the list of collections to search (ALL of them when the
    caller didn't scope to one -- an empty list searches NOTHING, which is why a
    bare query used to return 0 hits). Endpoint shape verified against the
    running OWUI; earlier `/api/v1/retrieval/process/query` returned 405.

<!-- mios-src:91e1f2593a9c from usr/libexec/mios/mios-knowledge-search:241-247 -->

### mios-launcher-daemon -- operator-side launcher broker. Runs...

mios-launcher-daemon -- operator-side launcher broker.

Runs as the operator's user-level systemd service
(mios-launcher.service under user@992.service), so it inherits the
operator's full WSLg env: WAYLAND_DISPLAY, WSL2_GUI_APPS_ENABLED=1,
WSL_INTEROP=/run/WSL/<pid>_interop, DBUS_SESSION_BUS_ADDRESS, etc.
That env is what flatpak GUI apps + Windows .exe interop both need
to actually surface windows on the operator's desktop.

The agent (mios-hermes uid 820) cannot call wsl.exe or any other
Windows .exe directly because /mnt/c/Windows/System32/*.exe is mode
0544 owned by mios:mios with WSL metadata xattrs that even root
can't strip (Windows ACL blocks the xattr write for system files).
Workaround: agent connects to this broker's unix socket, writes a
single line of shell, broker dispatches it from operator context.

PROTOCOL
--------
Client connects to UNIX socket at /run/user/<uid>/mios-launcher.sock
(socket mode 0666 so any local user can connect).

Send: <one line of shell>

Recv: OK
   (broker has dispatched the command; not "command finished")
       ERROR: <reason>
  (request rejected before dispatch)

Connection closed after one line. Broker spawns the dispatched
command via subprocess.Popen with start_new_session=True so it
detaches from the broker -- broker can serve other requests
immediately, dispatched command runs forever (or until it exits).

Authentication is filesystem permissions on the socket: mode 0660,
group mios-ai. That is exactly the two parties involved -- this broker
runs as the operator, the agent plane runs as User=mios-ai, and `mios`
is a member of the mios-ai group (sysusers.d/10-mios.conf).

The socket was previously 0666, justified on the grounds that the agent
stack already holds NOPASSWD: ALL sudo so the broker granted nothing
new. That reasoning does not hold for the party it actually exposed.
0666 admitted EVERY local uid, not just the agent stack -- and the agent
executes model-generated code in a sandbox, so a world-writable broker
socket was an unauthenticated path out of that sandbox to arbitrary
execution as the operator. Confinement that a socket bypasses is not
confinement.

If the group cannot be applied the broker refuses to listen rather than
falling back to a wider mode; a broker that does not start is safer than
one every local user can reach.

Still open, deliberately not done here: /run/mios-launcher is 1777
(tmpfiles.d/mios-launcher.conf), and per-caller token auth under
/etc/mios/launcher-token would authenticate the CALLER rather than its
group. Both are follow-ups, not substitutes for narrowing the mode.

<!-- mios-src:10389a44c96e from usr/libexec/mios/mios-launcher-daemon:4-56 -->

### The socket was 0666

The socket was 0666: ANY local user could dispatch arbitrary commands as the
operator. The only legitimate caller is the agent plane, which runs as
User=mios-ai, while this broker runs as the operator -- so the access this
needs is exactly "these two", not "everyone". `mios` is a member of the
mios-ai group (sysusers.d/10-mios.conf: `m mios mios-ai`), so group
ownership plus 0660 grants both and no one else.

This matters beyond tidiness: the agent executes model-generated code in a
sandbox, and a world-writable broker socket is an unauthenticated path out
of that sandbox to arbitrary execution as the operator.

<!-- mios-src:1535316f2f0c from usr/libexec/mios/mios-launcher-daemon:193-202 -->

### mios-manual -- corpus ledger and census for the MiOS...

mios-manual -- corpus ledger and census for the MiOS documentation system.

Spec: docs/design/doc-generative-documentation.md sections 3.4 and 5.

Subcommands:
  ledger   rebuild usr/share/mios/reference/manual-corpus.tsv from the tree
  audit    census as JSON (counts by class and reason)
  coverage the two ratchet numbers, for the gate and for humans
  harvest  move a comment's prose into a doc and record where it landed
  prune    delete a comment that provably landed (the only destructive path)
  landing  verify every pruned comment still lands in a doc
  render   splice derived content into MIOS-GEN marker interiors

`--root` selects the repo, and either layout resolves, so the same tools serve
mios.git and mios-bootstrap.git without being copied across (Law 15).

The ledger is the safety mechanism for the whole programme. Deletion of a
comment is only ever permitted once a ledger row proves its knowledge reached a
doc, so the landing columns must survive ordinary code churn: they are carried
forward BY CONTENT HASH, never recomputed. Moving a block within a file, or
reformatting the code around it, keeps its landing record.

Rows for blocks that no longer exist but were pruned are kept as tombstones --
that retention is what lets a gate prove, after the fact, that a comment which
is gone did land somewhere first.

<!-- mios-src:356c8d1dca2d from usr/libexec/mios/mios-manual:5-30 -->

### The SSOT for a repo root, in either repo's layout. mios.git...

The SSOT for a repo root, in either repo's layout.

    mios.git keeps it at usr/share/mios/mios.toml (it IS the deployed /usr);
    mios-bootstrap.git keeps its overlay at the root. Resolving both here is
    what lets one set of doc tools serve both repos, instead of the tools being
    copied across -- which Law 15 forbids.

<!-- mios-src:ded59315b6cc from usr/libexec/mios/mios-manual:61-67 -->

### sha12 -> row for lookups, every row preserved for writing....

sha12 -> row for lookups, every row preserved for writing.

    A plain dict keyed by sha12 loses duplicates, and identical comment text is
    common -- the same header boilerplate sits in many files. Reading the ledger
    into one and writing `list(rows.values())` back silently dropped 1105 of
    9669 rows, so a harvest followed by a commit would have deleted a ninth of
    the census with nothing to show for it.

<!-- mios-src:9e7f9f13eac9 from usr/libexec/mios/mios-manual:99-106 -->

### The census must be the same artifact everywhere, or it is...

The census must be the same artifact everywhere, or it is not a projection.

    A file the Python lexer could not tokenize or parse falls back to the regex
    lexer, which the module itself documents as miscounting -- and whether that
    happens depends on the INTERPRETER, not the file, so the same tree yields
    different ledgers on different machines. Refusing is the honest outcome.

<!-- mios-src:fd8a7202397c from usr/libexec/mios/mios-manual:149-155 -->

### True when this row's knowledge is provably in a doc. The...

True when this row's knowledge is provably in a doc.

    The predicate `prune` and the landing gate both use. Deliberately strict:
    the doc must exist, must carry the block's own content hash as an anchor,
    and the passage must retain most of the words. Anything weaker would let a
    one-line stub authorise deleting a 90-line design note.

<!-- mios-src:c790d6c39e60 from usr/libexec/mios/mios-manual:227-233 -->

### A short human title for a harvested passage. Source...

A short human title for a harvested passage.

    Source comments often open with banner decoration (`--- section ---`) or a
    bullet; carrying that into a Markdown heading reads as noise, so strip the
    ornament and title the passage by its first real words.

<!-- mios-src:8ca8382690ea from usr/libexec/mios/mios-manual:266-271 -->

### Clean a scraped comment before it reaches a doc. A comment...

Clean a scraped comment before it reaches a doc.

    A comment is written for whoever is editing that file; a manual is read by
    an operator on a booted host. Dev-box paths mean nothing to them, and a
    secret that leaked into a comment must not be republished into a doc that
    ships in the image.

<!-- mios-src:aa08f4e93c97 from usr/libexec/mios/mios-manual:298-304 -->

### Which manual page a file's prose belongs on. Grouped by the...

Which manual page a file's prose belongs on.

    Grouped by the directory the file lives in, so the manual mirrors the tree
    an operator is already navigating rather than inventing a second taxonomy.

<!-- mios-src:57b8bc7c00a0 from usr/libexec/mios/mios-manual:320-324 -->

### The Day-N+1 pass: scrape comments, sanitize, distil into...

The Day-N+1 pass: scrape comments, sanitize, distil into the manual.

    Deliberately NON-destructive. It never edits source and never touches an
    AI-hint: hints stay where they are and reach the docs through the index
    deriver, while narrative comments are copied forward. Removing a scraped
    comment stays a separate, opt-in `prune`.

<!-- mios-src:173fb50dcfe6 from usr/libexec/mios/mios-manual:332-338 -->

### Move a source comment's PROSE into a doc, and record where...

Move a source comment's PROSE into a doc, and record where it landed.

    Harvest never edits source. It writes the passage, stamps the anchor and
    fills the ledger's landing columns; `prune` is the only thing that removes
    the original, and only once landed() proves this step happened.

<!-- mios-src:dac59c09ea79 from usr/libexec/mios/mios-manual:459-464 -->

### Every pruned comment must still be provably present in a...

Every pruned comment must still be provably present in a doc.

    A tombstone whose doc passage was later deleted would mean a comment was
    removed and its knowledge then silently lost -- exactly what the ledger
    exists to make impossible. This is the gate for that.

<!-- mios-src:0b6107e4c14d from usr/libexec/mios/mios-manual:775-780 -->

### A deriver was asked for something the SSOT does not define....

A deriver was asked for something the SSOT does not define.

    Unknown deriver KINDS already failed the run; unknown ARGS silently fell
    back to rendering everything, so `ports:ai` quietly emitted the full table
    instead of saying the category does not exist.

<!-- mios-src:039d95e980bc from usr/libexec/mios/mios-manual:919-924 -->

### Windows checkouts set core.ignorecase=true, so `git...

Windows checkouts set core.ignorecase=true, so `git ls-files
usr/libexec/mios/mios-*` matched the then-CamelCase monitor there and
not on Linux (since renamed to mios-mon.py).
That made the generated index depend on which machine ran it, and CI
and the contributor took turns reverting each other. Re-filter in
Python, where the comparison is unambiguously byte-exact.

<!-- mios-src:beb4b41a4119 from usr/libexec/mios/mios-manual:947-952 -->

### (path, AI-hint) for every tracked file matching a glob. The...

(path, AI-hint) for every tracked file matching a glob.

    The hint header is already the one-line description of what a file is for,
    written next to the file and kept honest by check_hint_coverage. Reading it
    back out is how a documentation index stops being a hand-kept list.

<!-- mios-src:b5875afe1a14 from usr/libexec/mios/mios-manual:1073-1078 -->

### fnmatchcase, not fnmatch: fnmatch normalises case on...

fnmatchcase, not fnmatch: fnmatch normalises case on Windows, so
`usr/libexec/mios/mios-*` matched the then-CamelCase monitor there and
not on Linux (since renamed to mios-mon.py).
The index is a committed artifact -- when its content depends on which
machine regenerated it, CI and the contributor take turns reverting each
other and the gate reports a file rather than a cause.

<!-- mios-src:4f36f3b780f7 from usr/libexec/mios/mios-manual:1079-1084 -->

### Splice derived content into marker pairs. Writes NOWHERE...

Splice derived content into marker pairs. Writes NOWHERE else.

    The whole safety story of the doc model is that regeneration cannot destroy
    hand-written prose. That holds only because this function's sole write is
    replacing the interior of a matched MIOS-GEN pair -- there is deliberately no
    code path that emits a whole file, so an authored paragraph outside a marker
    is untouchable even if a deriver misbehaves.

<!-- mios-src:c5e4f52ab0a4 from usr/libexec/mios/mios-manual:1244-1251 -->

### Scope

Scope: every TRACKED Markdown file, not just usr/share/doc/mios -- a
marker in README.md or docs/ was previously never rendered or checked, so
a stale derived table could ship from there. Note this cannot reuse
mios_comments.iter_source_files: that filters by SCAN_EXT, which
deliberately excludes .md, and using it here silently emptied the scope.
Only files that actually contain a marker are opened for writing.

<!-- mios-src:eb6b97a87a51 from usr/libexec/mios/mios-manual:1260-1265 -->

### mios-mcp-server -- Model Context Protocol stdio server....

mios-mcp-server -- Model Context Protocol stdio server.

Exposes MiOS's [verbs.*] catalog (SSOT in mios.toml) as MCP tools so
LOCAL MCP-aware agents can drive MiOS verbs natively.

LOCAL-ONLY by design. Intended consumers:
  * MiOS-Hermes        (local; primary tool catalog)
  * MiOS-OpenCode      (local; coding agent)
  * mios-daemon-agent  (local; background sub-agent)
  * Any LOCAL MCP client the operator stands up against on-host models

NOT for cloud-LLM clients. The operator's binding rule (2026-05-19)
is "no cloud dependencies": MCP clients that route through hosted LLM
providers (Claude Desktop, Cursor, GitHub Copilot, Codex Cloud) MUST
NOT be in the chain. They CAN consume this server, but the MiOS
operator does not run them.

Protocol: JSON-RPC 2.0 over Streamable HTTP (current transport) + stdio. The
declared revision is the [mcp].protocol_version SSOT (env
MIOS_MCP_PROTOCOL_VERSION), shared with the agent-pipe consumer.
  https://modelcontextprotocol.io/specification

Implements:
  initialize          -- handshake; returns serverInfo + capabilities
  notifications/initialized  -- accepted, no response
  tools/list          -- renders [verbs.*] as MCP tool specs (calls
                         agent-pipe /v1/verbs)
  tools/call          -- dispatches via agent-pipe /v1/dispatch which
                         routes through the launcher broker
  resources/list      -- the COMPLETE read-only surface (every verb/script
                         + recipe + skill, promoted AND not) via agent-pipe
                         /v1/resources -- progressive-disclosure discovery
  resources/read      -- fetch one mios:// resource via /v1/resources/read
  ping                -- liveness
  shutdown            -- (informational; client closes stdio)

Defers to v2:
  notifications/{tools,resources}/list_changed (inotify on mios.toml + the
  skill store)
  prompts/list (no MCP prompts yet)

<!-- mios-src:8a49a3ac4dbb from usr/libexec/mios/mios-mcp-server:4-44 -->

### MCP revision this server DECLARES -- the...

MCP revision this server DECLARES -- the [mcp].protocol_version SSOT shared
    with the agent-pipe consumer (mios_mcp.MCP_PROTOCOL_VERSION), so publish + consume
    stay in lockstep. env MIOS_MCP_PROTOCOL_VERSION wins; else the layered mios.toml
    (vendor < /etc < ~/.config), each layer best-effort so a sandbox-blocked path just
    falls through; else the current revision as a degrade-open default. Declared once
    here -- never a scattered code literal.

<!-- mios-src:89cf4b1a6583 from usr/libexec/mios/mios-mcp-server:69-74 -->

### Build the verb+recipe catalog DIRECTLY from mios.toml as...

Build the verb+recipe catalog DIRECTLY from mios.toml as MCP tool specs --
    the guaranteed non-empty FLOOR, served when BOTH the live feed AND the cache
    are unavailable (cold start + agent-pipe down) so a client is never toolless
    for its whole life. Skills are NOT built here (they live in pgvector,
    unreachable from this sandbox); the verb+recipe surface -- every launch /
    OS-control / web tool -- is the floor that matters. Mirrors agent-pipe's
    /v1/tools projections (server.py:15461 verbs, :5345 recipes, :4760 filter).

<!-- mios-src:dce6b2b9160c from usr/libexec/mios/mios-mcp-server:288-294 -->

### True when `live` looks like a degrade-open partial that...

True when `live` looks like a degrade-open partial that would POISON
    last-good if saved. Two cases: (a) empty or >50%-shrunk vs cache (warmup);
    (b) it LOST promoted skills without gaining any -- agent-pipe's /v1/tools is
    degrade-open (server.py:15583-15590): a pgvector outage silently drops the
    skills tail (HTTP 200, no error), a drop too small to trip the size guard, so
    a skill-less feed would otherwise be accepted + persisted as the new last-good.

<!-- mios-src:1e66db3b54e9 from usr/libexec/mios/mios-mcp-server:325-330 -->

### One background catalog refresh

One background catalog refresh: fetch live, and persist as last-good
    UNLESS it would poison the cache (empty/shrunk, or a degrade-open skills-tail
    drop -- see _is_poisoned).

<!-- mios-src:4446e06d2712 from usr/libexec/mios/mios-mcp-server:350-352 -->

### Keep the last-good catalog warm independent of client...

Keep the last-good catalog warm independent of client connect timing.
    Re-tries faster while the upstream looks unhealthy so the cache re-warms
    promptly once agent-pipe recovers. When the served catalog actually CHANGES
    (e.g. recovers from the SSOT floor / a degraded set back to the full surface),
    push notifications/tools/list_changed so already-connected clients that honor
    it (Hermes) re-list mid-session instead of being stuck on the degraded set.

<!-- mios-src:d34e26cfa689 from usr/libexec/mios/mios-mcp-server:365-370 -->

### mios-micro-llm -- thin client + persistent-loaded helper...

mios-micro-llm -- thin client + persistent-loaded helper for the
always-on tiny model.

Used by:
  * mios-log-watcher.service -- classifies journal events
  * mios-cron-director.service -- gates scheduled tasks on system state
  * any agent / shim that wants <500 ms classification

The "micro" model is configured via mios.toml [ai].micro_model
(default qwen3:1.7b -- the 4-model-set micro/CPU base, ~1.4 GB
resident; was qwen3:0.6b before the 2026-06-01 consolidation).
Kept warm on the mios-llm-light /v1 lane (llama-swap owns residency/TTL).

USAGE
    mios-micro-llm classify <prompt>            -> single-line answer
    mios-micro-llm classify --json <prompt>     -> JSON wrapper
    mios-micro-llm warm                         -> issue a 1-token gen to load
    mios-micro-llm status                       -> is it loaded? latency probe

CONFIG (per layered mios.toml [ai] priority):
    [ai]
    micro_model    = "qwen3:1.7b"
    micro_endpoint = "http://localhost:8450/v1"
    micro_keep_alive_seconds = -1   # -1 = never unload

<!-- mios-src:dbd5e501e213 from usr/libexec/mios/mios-micro-llm:4-28 -->

### mios-model-router — always-on front door + worker fan-out...

mios-model-router — always-on front door + worker fan-out (OpenAI-compatible).

Operator 2026-06-08: "EVERYTHING starts at the front-door model and delegates to
nodes from there — multiple smaller modern models across all nodes, full tools."
The front door must prefill the ~17K-token MCP tool surface (113 tools) that Hermes
sends EVERY turn AND emit tool calls — fast. MEASURED head-to-head on that exact
17K prompt (2026-06-08):
  CPU  (qwen3-4b)        169s, answered in PROSE (no tool call)   -> non-viable
  iGPU (qwen2.5-1.5b)    >180s TIMEOUT                            -> non-viable
  dGPU (qwen3:1.7b,4090) 2.2s cold / 0.2s warm, EMITS open_app    -> the only one
The small/integrated lanes are ~100 tok/s prefill; the 4090 is ~85x faster AND
llama.cpp prefix-caches the stable tool surface (cached_tokens 15257 -> 0.2s turns).
So the dGPU(:8500) with a SMALL model (qwen3:1.7b ~2GB VRAM — does NOT fight gaming
like the 16GB heavy model did) is the front door; iGPU(:11436) + CPU(:11451) are
slow fallbacks + delegated workers.

Logical models:
  mios-orchestrator -> dGPU light node (:8500 qwen3:1.7b) PRIMARY; iGPU/CPU FALLBACK
                       (failover, preference order). The entry point / front door.
  mios-fanout       -> ROUND-ROBIN across ALL worker nodes (dGPU :8500 qwen3:1.7b,
                       iGPU :11436, CPU :11451) so concurrent delegated children land
                       on DIFFERENT hardware and run truly in parallel.
  mios-heavy        -> legacy compat (failover across workers).

Background health probe every 8s; per-lane 30s cooldown on error; SSE passthrough.
Response carries X-MiOS-Lane / X-MiOS-Model so you can SEE which node served.
Override lanes via MIOS_ROUTER_DEPLOYMENTS (JSON). Port via MIOS_ROUTER_PORT.

<!-- mios-src:a553963b0e17 from usr/libexec/mios/mios-model-router:4-31 -->

### An unverified weight file must never be installed under a...

An unverified weight file must never be installed under a
name the lanes will load. Drop the part file so the next run
re-downloads instead of resuming a poisoned partial.

<!-- mios-src:9bfa3c7a4fea from usr/libexec/mios/mios-models-firstboot:96-98 -->

### The sentinel is the unit's ConditionPathExists gate, so...

The sentinel is the unit's ConditionPathExists gate, so writing it
unconditionally retired the provisioner after ONE pass -- a failed download or
a rejected digest meant that model was never fetched again. Write it only when
every declared model is actually on disk; otherwise leave it and let
mios-models-firstboot.timer come back. Still exit 0 either way: degrade-open,
a pull must never block boot (Architectural Law 12).

<!-- mios-src:772e76713047 from usr/libexec/mios/mios-models-firstboot:108-113 -->

### The lowest unused ordinal in dest_dir, for a type numbered...

The lowest unused ordinal in dest_dir, for a type numbered sequentially.

    Scanning beats a stored counter: the directory listing IS the allocation
    record, so it cannot drift out of step with the files the way a hand-bumped
    name_prefix in SSOT did. An unreadable or empty directory yields the first
    ordinal rather than raising -- scaffolding into a fresh tree is legitimate.

<!-- mios-src:63ca6741a474 from usr/libexec/mios/mios-new:72-78 -->

### usr/libexec/mios/mios-os-control [subcommand] [flags] The...

/usr/libexec/mios/mios-os-control [subcommand] [flags]

The unifying entrypoint for MiOS's OS-control surface: VERBS (atomic
OpenAI-callable operations), RECIPES (OS-shell templates) and SKILLS
(composed verb sequences). Everything is sourced from SSOT -- mios.toml
[verbs.*] / [recipes.*] and /usr/share/mios/skills/*.json -- so there
are ZERO hardcoded operation lists anywhere: add a verb in TOML and it
appears here (and in every consumer) automatically. Self-iterating.

Operator binding 2026-05-20: "mios-os-control is an entire OS-related
recipes, tools and skills that are ALL Day-0 ready and self iterating --
Absolutely NO hardcodes for anything variable ... OpenAI compliant for
all llms/models use GLOBALLY ... has defaults for window launching
positions". This tool is that single source: any LLM/model -- not just
OWUI -- consumes `mios-os-control schema` to learn the full tool surface.

Subcommands:
  schema [--openai|--owui|--names] [--tier core|common|rare|all]
                       Emit the tool schema for every verb, generated
                       from mios.toml [verbs.*].
                         --openai (default) -> OpenAI tools array:
                              [{type:function, function:{name,
                               description, parameters}}]
                         --owui   -> Open WebUI specs list:
                              [{name, description, parameters}]
                         --names  -> bare verb names, one per line
                       --tier filters by verb tier (default: all).
  verbs [--tier ...]   Human-readable verb catalog (name / sig / desc).
  recipes              Delegate to `mios-os-recipe list`.
  skills               List composed skills in /usr/share/mios/skills.
  window-defaults      Emit the [os_control] window-placement defaults
                       (JSON) -- the SSOT for where launches land.
  doctor               Self-check: verify every verb has a description +
                       valid params block + every enum default is in its
                       enum; report drift. Exit non-zero on any problem.

SSOT: mios.toml is the only source of verbs/recipes; skills/*.json the
only source of skills. No code change needed to add capabilities.

<!-- mios-src:7908d8f2663b from usr/libexec/mios/mios-os-control:4-42 -->

### DELEGATE a launch to the always-on mios-daemon-agent (the...

DELEGATE a launch to the always-on mios-daemon-agent (the iGPU
    daemon-tier brain): it FIRES the launch THROUGH THE BROKER (the only
    context with the WSLg display env to surface a window) AND runs the
    success-check, returning ONE verdict. The agent reads `launched` instead
    of firing + checking itself (operator 2026-05-25, 4A #2). The daemon is
    the SOLE firer; if it's unreachable this fires NOTHING and returns an
    honest error so the agent falls back to plain open_app + verify_launch.
    Offline-first; NEVER fabricates launched=true.

    Usage: mios-os-control launch "<app name>" [--timeout SECONDS]
    Output JSON: {success, app, fired, launched, verdict, attempts, checked_by}.

<!-- mios-src:db7c7a59709a from usr/libexec/mios/mios-os-control:269-280 -->

### usr/libexec/mios/mios-os-recipe <name> [key=value ...]...

/usr/libexec/mios/mios-os-recipe <name> [key=value ...]

Executes a NAMED, ALLOWLISTED os-shell recipe declared in mios.toml
under [recipes.<name>]. Picks the OS-appropriate template (linux /
windows), shell-escapes every parameter, optionally converts Linux
paths to Windows paths via wslpath, and dispatches.

Hardening:
  * Only recipes present in mios.toml [recipes.*] are executable --
    arbitrary command pass-through is impossible.
  * Only kwargs whose key appears in the recipe's `args` list survive
    to the template; everything else is dropped.
  * `permission = "write"` recipes require MIOS_OS_RECIPE_WRITE=1
    in env (set by the operator via mios.toml -> userenv.sh slot).
  * Every {placeholder} substitution is shell-quoted before splice
    -- shlex.quote on Linux, double-quote-escape on Windows.
  * `wsl_paths` arg names get wslpath -w conversion when the windows
    template is chosen so File Explorer / Notepad receive a native
    Windows path.

Output: --json prints {success, exit_code, stdout, stderr, template,
target_os}; default mode prints stdout/stderr directly + exits with
the recipe's exit code.

SSOT: mios.toml is the only source of recipes. Add new recipes in
TOML; no code change required.

Operator binding 2026-05-18: "RECIPES" + "NO HARDCODES ANYWHERE" +
"mios-os-control should have more OS Specific Shell controls".

<!-- mios-src:173643a1819f from usr/libexec/mios/mios-os-recipe:4-33 -->

### mios-oscap-gate -- severity-gated verdict over an OpenSCAP...

mios-oscap-gate -- severity-gated verdict over an OpenSCAP results (ARF/XCCDF) file.

Usage: mios-oscap-gate <results.(arf|xml)> [severity_gate]
  severity_gate: high | medium | low | any   (default: high)

oscap's own exit codes only tell you "some rule failed" (rc=2), not WHICH severity.
The scan-only gate (86-oscap-compliance.sh) calls this to turn the results into a
build verdict: it counts FAILED rules at/above the configured severity and fails the
build iff that count is > 0. Severity ordering is structural (high>medium>low); a
failed rule with no/unknown severity counts only under the "any" gate.

<!-- mios-src:6ee941f57c09 from usr/libexec/mios/mios-oscap-gate:4-14 -->

### mios-owui-apply-knowledge -- attach a MiOS-Documentation...

mios-owui-apply-knowledge -- attach a MiOS-Documentation Knowledge
collection to the MiOS-Agent model row in Open WebUI, sourced
DIRECTLY from the FHS.

Operator directive 2026-05-15: "use the files directly from the FHS--
and refine the files themselves". The files in /usr/share/mios/ai/,
/usr/share/mios/docs/, /usr/share/mios/hermes/skills/,
/usr/share/mios/cookbooks/, /usr/share/mios/prompts/,
/etc/mios/system-prompts/, /CLAUDE.md, and /AGENTS.md ARE the
authoritative MiOS knowledge corpus. This helper registers each one
as an OWUI `file` row with `path = <absolute FHS path>` and links
them into a Knowledge row that the MiOS-Agent model row's
`meta.knowledge` references.

Why both `path` AND `data.content`?

  * `file.path` -- so OWUI's `/files/` re-process pipeline reads from
    the canonical FHS source when re-vectoring (requires the OWUI
    container to have /usr/share/mios + /etc/mios bind-mounted; see
    /etc/containers/systemd/mios-open-webui.container).
  * `file.data['content']` -- the snapshot OWUI uses inside the
    `/knowledge/{id}/file/add` flow when re-using cached chunks isn't
    possible (e.g. first index, hash drift). Stays accurate because
    we re-read at apply time and refresh on hash drift.
  * `file.hash` -- sha256 of the source content. If the on-disk file
    changes, the hash drifts, we clear OWUI's per-file vector
    collection (`file-{id}`), and OWUI re-indexes from the now-fresh
    `file.data.content`/`file.path` at next attach.

Idempotent. Runs on host (not inside container) -- writes directly to
the sqlite db at /var/lib/mios/open-webui/webui.db.

Designed to be safe to run from a Quadlet ExecStartPost, a oneshot
service, or the operator's shell. Returns 0 on success/no-op.

<!-- mios-src:1bea96de2326 from usr/libexec/mios/mios-owui-apply-knowledge:4-38 -->

### Best-effort

Best-effort: vector_db is on a bind-mount the host can see at
    /var/lib/mios/open-webui/vector_db.  ChromaDB stores collections
    under that directory; clearing them is enough to force a re-index
    on next read.  This is a soft cleanup -- if the directory layout
    changes upstream, OWUI will just re-vector when it can't find the
    collection and we'll have wasted no operator-visible work.

<!-- mios-src:7ae6bc1f4d3d from usr/libexec/mios/mios-owui-apply-knowledge:135-141 -->

### mios-owui-apply-suggestions -- HANDS-OFF mode. Operator...

mios-owui-apply-suggestions -- HANDS-OFF mode.

Operator directive 2026-05-17: "no hardcoded answers or anything
hardcoded for english at all--Completely generic and platform
agnostic--GLOBALLY FOR ALL FILES; AUDIT".

Previous incarnations of this script seeded a 40-entry English-only
pool of MiOS-flavored prompt cards. That violated the rule above.

The correct architecture: OWUI's built-in
`ENABLE_SUGGESTION_GENERATION=True` (set in the Quadlet) asks the
task model (TASK_MODEL_EXTERNAL=hermes-agent) to PRODUCE suggestions
per session, in the operator's language, aware of recent context.
The model picks the locale + the content from the running chat;
nothing is hardcoded here.

This script now ONLY WIPES any leftover hardcoded suggestions from
prior runs so OWUI falls back to its LLM-generated path. Re-running
is idempotent.

Operators who want a fixed set (no LLM call per session) can populate
ui.prompt_suggestions in OWUI's admin UI by hand -- that's their
choice, not vendor default.

<!-- mios-src:819ae9269661 from usr/libexec/mios/mios-owui-apply-suggestions:3-26 -->

### mios-owui-apply-system-prompt -- seed the MiOS-Agent model...

mios-owui-apply-system-prompt -- seed the MiOS-Agent model registration in
Open WebUI with the MiOS-managed system prompt.

OWUI stores per-model overrides in its sqlite db at
/var/lib/mios/open-webui/webui.db, table `model`, column `params` (JSON).
The `params.system` field is what OWUI prepends to the user's first
message in every chat that selects this model -- it rides on top of
Hermes's SOUL.md (the agent's own persona) as belt-and-suspenders
reinforcement. See /usr/share/mios/open-webui/system-prompts/mios-agent.md
for the rationale.

Idempotent. Re-seed conditions (mirror SOUL.md re-seed logic):
  * model row absent: skip with a clear log line
  * params.system absent OR contains "MiOS-managed" marker AND content
    differs from source: write source content
  * params.system has been edited away from the marker: leave alone
    (operator has taken ownership)

Designed to be safe to run from a Quadlet ExecStartPost, a oneshot
service, or the operator's shell. Returns 0 on success/no-op.

<!-- mios-src:46147b6aab3a from usr/libexec/mios/mios-owui-apply-system-prompt:4-24 -->

### mios-owui-apply-websearch -- enable Open WebUI's built-in...

mios-owui-apply-websearch -- enable Open WebUI's built-in web-search
augmentation + point it at the local SearXNG (mios-searxng container
on :8800).

OWUI's web-search feature is OFF by default in upstream. When enabled,
every chat turn can be augmented by retrieving + injecting web results
into the model's context (gated by an in-chat toggle the user clicks
on each message). Pointing it at the local SearXNG keeps everything
on-box; no external API keys, no rate limits, no telemetry.

Idempotent: only updates keys that aren't already correct. Mirrors the
mios-owui-apply-system-prompt pattern (operator-managed marker via the
default values themselves -- if the operator changes a key in OWUI's
admin UI, this script LEAVES it alone unless that key is one of the
must-be-true bools we flip).

<!-- mios-src:7ac68c254022 from usr/libexec/mios/mios-owui-apply-websearch:4-19 -->

### mios-owui-bootstrap-admin -- create the first OWUI admin...

mios-owui-bootstrap-admin -- create the first OWUI admin user when
the `user` table is empty, so a fresh install / reinstall doesn't
lock the operator out of Open WebUI.

Resolves identity from layered mios.toml ([identity].username,
[identity].email) and the password from the MiOS password SSOT (see
_read_password): MIOS_OPERATOR_PASSWORD / MIOS_OWUI_ADMIN_PASSWORD ->
mios.toml [identity].default_password -> MIOS_IDENTITY_DEFAULT_PASSWORD -> "mios"
-- the SAME source Forge / Portal / Cockpit / RDP use, so one operator
password works everywhere. A "__random__" override generates + writes a
24-char password to /etc/mios/owui-admin-password (mode 0600 root-only).

Idempotent: skips if any user row exists. Logs to stderr so
mios-hermes-firstboot can capture the output via _log.

Why direct sqlite + bcrypt instead of POST /api/v1/auths/signup:
  * doesn't depend on OWUI being up + responsive at firstboot time
  * doesn't need ENABLE_SIGNUP=True dance (toggle env, restart, post,
    toggle back, restart) which is fragile and slow
  * uses the same bcrypt scheme OWUI does (passlib bcrypt $2b$)

Returns exit 0 on success, no-op skip, or any expected non-fatal path.
Returns exit 2 only on hard infrastructure failures (db missing, no
admin name resolvable). Designed to never block firstboot.

<!-- mios-src:51c5ab83db6a from usr/libexec/mios/mios-owui-bootstrap-admin:4-28 -->

### [identity].default_password from layered mios.toml -- the...

[identity].default_password from layered mios.toml -- the canonical
    MiOS SSOT for the operator's shared admin password (same field Forge /
    Portal / Cockpit / RDP resolve).

<!-- mios-src:12f9421ee426 from usr/libexec/mios/mios-owui-bootstrap-admin:90-92 -->

### Return (password, source) resolved from the MiOS password...

Return (password, source) resolved from the MiOS password SSOT.

    ONE operator password backs every MiOS admin surface (Forge / Portal /
    Cockpit / RDP / OWUI). Resolution order, highest precedence first:
      1. MIOS_OPERATOR_PASSWORD     -- explicit override (secrets.env / env)
      2. MIOS_OWUI_ADMIN_PASSWORD   -- per-service override (parallels
                                       MIOS_FORGE_ADMIN_PASSWORD)
      3. mios.toml [identity].default_password  -- the canonical SSOT field
      4. MIOS_IDENTITY_DEFAULT_PASSWORD (install.env)    -- shell/systemd bridge of #3
      5. literal "mios"             -- vendor default; the same final fallback
                                       as usr/libexec/mios/forge-firstboot.sh

    A value of "__random__" at any tier opts INTO a generated 24-char password
    (written to PASSWORD_OUT, mode 0600), mirroring forge's __random__ token.
    We deliberately do NOT silently generate a random password: an operator
    who never sees it can't log in, and it diverges from the rest of the OS.

<!-- mios-src:4fdea076eb06 from usr/libexec/mios/mios-owui-bootstrap-admin:100-116 -->

### Export the admin's OWUI api_key to /etc/mios/owui-admin.key...

Export the admin's OWUI api_key to /etc/mios/owui-admin.key (0640
    root:mios-ai) so the agent-pipe / mios-ai broker -- which CANNOT read
    webui.db (0640 mios-open-webui) -- can authenticate to OWUI's retrieval
    API for knowledge_search instead of silently degrading to the pgvector
    fallback. The key is minted by mios-owui-install-pipe (or OWUI); this only
    EXPORTS it. Best-effort, idempotent, never raises (we already run as root
    here so the chown succeeds on a real host).

<!-- mios-src:7a902da63839 from usr/libexec/mios/mios-owui-bootstrap-admin:190-196 -->

### mios-owui-install-computer-use -- idempotent registration...

mios-owui-install-computer-use -- idempotent registration of the MiOS
Computer-Use OWUI Native tool (WS-4 P0) into webui.db's `tool` table, so the
chat model gets the desktop control + vision grounding + doc-gen verbs as typed
tool_calls instead of indirecting through the generic `terminal:` shell tool.

Mirrors mios-owui-install-tools exactly (same DB shape, change-detection over
content+specs, admin attribution, model auto-attach). Source module:
  /usr/share/mios/openwebui/tools/mios_computer_use.py

The `specs` list MUST stay in lockstep with the Tools class methods in that
module AND every spec MUST carry a JSONSchema `parameters` block -- OWUI's
process_chat indexes spec["parameters"] and a partial spec crashes the whole
chat with KeyError: 'parameters' (operator-flagged regression on the verbs
tool, 2026-05-17).

Intended to run from mios-hermes-firstboot.service alongside the existing
install-pipe / install-tools helpers. Default-OFF posture: the tool's own
Valves ship WRITE_ACTIONS_ENABLED=False and mios-docgen is gated server-side,
so registering it is inert until the operator opts into desktop writes.

<!-- mios-src:830619772ca9 from usr/libexec/mios/mios-owui-install-computer-use:4-23 -->

### mios-owui-install-tools -- idempotent registration of the...

mios-owui-install-tools -- idempotent registration of the MiOS
verbs OWUI Tools class (launch_app / everything_search / mios_apps /
mios_find) so the chat model gets them as native typed tool_calls
instead of having to indirect through `terminal: mios-<verb>`.

Matches the install pattern of mios-owui-install-pipe: writes
directly into webui.db's `tool` table. Idempotent -- re-runs check
the existing row and only UPDATE on content change.

Invoked by mios-hermes-firstboot.service alongside the existing
install-pipe / apply-knowledge / apply-suggestions helpers.

Operator directive 2026-05-17: "AI-stack native (Hermes
tools???!!)" -- this is the OWUI-native answer; the same verbs
could also be wrapped as a stdio MCP server in a follow-up for
clients that consume MCP rather than OWUI's tool surface.

<!-- mios-src:0066a525311c from usr/libexec/mios/mios-owui-install-tools:4-20 -->

### mios-passport -- Phase C.3 Agent Passport (Ed25519 signing)...

mios-passport -- Phase C.3 Agent Passport (Ed25519 signing) CLI.

Per-agent identity tokens. Every agent in the MiOS stack
(agent-pipe, MiOS-Hermes, MiOS-OpenCode, mios-daemon, cron-
director, future MCP clients) gets an Ed25519 keypair under
/var/lib/mios/agent-passports/<agent>/{private.key,public.key}.

Security-relevant agent-DB writes (tool_call, skill_invocation,
firewall_block events) carry a passport envelope of the shape:

  {agent, ts, nonce, op_hash, alg, kid, sig}

where op_hash binds the signature to the canonical-JSON of the
record's other fields. Any verifier with read access to the
agent's public key (which is world-readable) can validate the
envelope without contacting an external KMS, online CA, or other
agent.

Subcommands:

  provision [--agent NAME] [--force]
                       Generate an Ed25519 keypair for an agent.
                       Defaults to every agent in
                       [passport].agents. Writes
                       <key_dir>/<agent>/private.key (0600 owner
                       sysuser) + public.key (0644 world-readable)
                       + registers the public key in the agent DB
                       (agent_keypair table). Idempotent unless
                       --force (which rotates).

  list                 Print the registered keypairs (agent / kid
                       / provisioned_at / retired).

  show <agent>         Print the agent's current public key + kid.

  sign --agent NAME --payload @file
                       Read a JSON payload from --payload (or '-'
                       for stdin), sign it as <agent>, print the
                       envelope as JSON. Operator-callable
                       primitive for non-Python integrations.

  verify --envelope @file [--payload @file]
                       Parse a passport envelope. Resolve the
                       public key for envelope.agent (filesystem
                       first; falls back to the agent-DB agent_keypair
                       row). Verify the Ed25519 signature. Exits
                       0 on valid + 2 on invalid + 3 on missing
                       key.

  rotate <agent>       Mark the current key retired, generate a
                       new one (kid increments). Old kid stays in
                       agent_keypair for verifying historical
                       writes.

  hash --table T --payload @file
                       Print sha256(table:canonical-json) of the
                       payload as the op_hash used inside passport
                       envelopes. Same algorithm agent-pipe uses
                       so external integrators can pre-compute.

  public-key <agent>   Print the raw PEM-encoded public key. Use
                       this to ship a key to an external verifier
                       without parsing the agent DB.

Fully local + offline. Uses python3-cryptography (in the Fedora
base image) -- NOT a pip install.

SSOT: every knob (enable, algo, key_dir, rotate_days,
verify_on_read, agents) routes through MIOS_PASSPORT_* env
which userenv.sh sources from mios.toml [passport].

<!-- mios-src:dd5484aad1d2 from usr/libexec/mios/mios-passport:5-75 -->

### The canonical payload-to-sign. Newline-separated 4-tuple --...

The canonical payload-to-sign. Newline-separated 4-tuple --
    deterministic + simple to re-derive from external languages.

    NOTE: any change here breaks every existing passport's
    signature. Add new fields by extending the envelope + bumping
    a `v` field, NOT by mutating this signing payload.

<!-- mios-src:b44f2955bca8 from usr/libexec/mios/mios-passport:210-215 -->

### Verify a passport envelope. Returns (ok, reason). If...

Verify a passport envelope. Returns (ok, reason).

    If `payload_for_hash` is supplied as (table, fields), the
    function additionally re-computes the op_hash and compares it
    to the envelope -- the strongest check (signature + data
    integrity). Without payload_for_hash, only the signature is
    verified (still proves attribution but doesn't bind to data).

<!-- mios-src:e0ae2e701d78 from usr/libexec/mios/mios-passport:242-248 -->

### Exit reflecting the executor's READ-BACK verdict, not just...

Exit reflecting the executor's READ-BACK verdict, not just transport success.
    The Windows OS-control executor returns {ok,verified,reason} (Invoke-TypeText et
    al.): a type/click/window op that did NOT land returns verified:false / ok:false
    in the BODY.

    FAIL CLOSED (operator 2026-06-19/20: "type reported SUCCESS but nothing was
    typed" -- twice). The prior version exited 0 on an unparseable/empty/non-dict/
    keyless body to "not false-fail a transport quirk" -- but THAT is the lie: when
    the verdict cannot be determined the op's success is UNKNOWN, and an unknown is
    NOT a success. A false FAILURE is recoverable (the caller retries / sees real
    state); a false SUCCESS is the fabrication the operator keeps catching. So we
    exit 0 ONLY on an EXPLICIT truthy verdict key; every undeterminable case exits 1
    with a diagnostic. (Launch is ALSO independently re-verified by the window-diff
    in server.py, so failing this closed never silently drops a real launch.)

<!-- mios-src:1c203d89af9c from usr/libexec/mios/mios-pc-control:98-111 -->

### mios-pc-vision -- screenshot + UI-element query -> click...

mios-pc-vision -- screenshot + UI-element query -> click coordinates.

Local-only PC-Control vision grounding. Takes a screenshot path + a
natural-language description of a UI target, calls a local vision
LLM (default qwen3-vl:4b on mios-llm-light :8450), returns JSON:

    {"x": <int>, "y": <int>, "confidence": <0..1>, "reasoning": "..."}

Architecture per /usr/share/mios/docs/agents/PC-CONTROL-LOCAL.md:
  Plan: Hermes (qwen3-coder:30b)
  Ground: this tool (qwen3-vl:4b auxiliary lane)
  Act: mios-pc-control (Win32 SendInput)
  Verify: re-screenshot + ground

USAGE
    mios-pc-vision <screenshot.png> "<element-query>"

EXAMPLE
    mios-pc-control screenshot /tmp/screen.png
    mios-pc-vision /tmp/screen.png "the OK button"
        -> {"x": 814, "y": 562, "confidence": 0.92, "reasoning": "..."}
    mios-pc-control click 814 562

CONFIG (resolved per layered mios.toml [ai] priority):
    [ai]
    vision_grounding_model = "qwen3-vl:4b"
    vision_grounding_endpoint = "http://localhost:8450/v1"

<!-- mios-src:66301497e2b2 from usr/libexec/mios/mios-pc-vision:4-31 -->

### mios-pg-query -- minimal pure-stdlib PostgreSQL client for...

mios-pg-query -- minimal pure-stdlib PostgreSQL client for the MiOS agent
plane. No psql binary, no psycopg, no podman needed: it speaks the v3 wire
protocol directly over a loopback TCP socket.

Why this exists (R15): confined service users such as `mios-ai` (which runs
mios-daemon's directory indexer + the dispatched directory_lookup verb) have
NEITHER a psql binary NOR podman access to exec into the pgvector container --
so `mios-db --pg`'s two existing paths both fail for them, and the agent plane
could not reach pgvector. This is the universal fallback. It relies on the
pgvector pg_hba `host all all 127.0.0.1/32 trust` line (loopback trust), so
there is no auth exchange -- the StartupMessage is answered with
AuthenticationOk directly.

Two modes:
  1. SIMPLE (default, unchanged):  mios-pg-query [-At ...] '<sql>'   (or SQL on
     stdin). Sends the SQL verbatim as one simple-Query ('Q') message. Multiple
     / multi-statement queries are handled. The SQL is run AS-IS -- the caller
     owns its safety (it is a trusted raw-SQL transport / console).
  2. EXTENDED / parameterized (WS-A3):  mios-pg-query --exec-json   reads a JSON
     envelope on stdin and binds the values OUT-OF-BAND via the v3 extended
     query protocol (Parse/Bind/Execute/Sync), so NO value is ever spliced into
     the SQL text (kills SQL-injection). Two envelope shapes:
        {"sql": "... $1 ... $2 ...", "params": [v1, v2, ...]}
        {"statements": [{"sql": "...", "params": [...]}, ...]}  # one txn (atomic)
     Placeholders are $1..$n; params are bound as TEXT format with unspecified
     type (the backend infers from context / an explicit ::cast in the SQL).
     NULL is JSON null; keep integers (e.g. LIMIT) inline in the SQL string
     (int-coerced by the caller) to avoid text->int inference edge cases.

Env:    MIOS_PGVECTOR_HOST (127.0.0.1) MIOS_PG_PORT (5432)
        MIOS_PGVECTOR_USER (mios)      MIOS_PGVECTOR_DB (mios)
        task_70 RLS: MIOS_PGVECTOR_RLS_ENABLE (off) gates a per-connection owner scope;
        the owner comes from `--owner <v>` (or `--owner=<v>`) else MIOS_PG_RLS_OWNER.
        When enabled+owner, an owner-bound `set_config('mios.owner_user', ...)` is
        SET first so the schema RLS policies scope rows to that owner. Default-off /
        no owner -> nothing is emitted (byte-identical; no consumer is locked out).
Output: tab-separated columns, one row per line (psql -At shape) for BOTH modes.
        Multi-row handled; NULLs render as empty. Exit 1 on any backend
        ErrorResponse or unsupported (non-trust) auth.

<!-- mios-src:1bc52436f18c from usr/libexec/mios/mios-pg-query:5-44 -->

### The owner to scope THIS invocation's rows to, or None to...

The owner to scope THIS invocation's rows to, or None to emit NO scope.
    Gated by MIOS_PGVECTOR_RLS_ENABLE (SSOT [pgvector].rls_enable, bridged by userenv.sh),
    the SAME flag the agent-pipe pg path reads -- default-off => None => no change.
    The owner comes from `--owner <v>` (or `--owner=<v>`) else MIOS_PG_RLS_OWNER, so
    the confined consumers (mios-ai / daemon / skills / kg) can scope their reads.
    None whenever RLS is off OR no owner is supplied -> the GUC stays unset -> the
    schema policy is permissive (degrade-open: a confined tool / the daemon is NEVER
    locked out).

<!-- mios-src:7fe933f05ef7 from usr/libexec/mios/mios-pg-query:254-261 -->

### Bind this connection's RLS owner GUC. The owner + the GUC...

Bind this connection's RLS owner GUC. The owner + the GUC name are BOUND via
    the extended protocol ($1/$2 -> Parse/Bind/Execute/Sync) -- NEVER spliced into
    SQL. The CLI uses ONE connection per process invocation (no pooling), so the GUC
    is set SESSION-level (set_config(..., false)) once right after connect and
    naturally spans every following statement of this invocation -- simple OR
    extended -- then dies with the process. (The agent-pipe python path uses
    is_local=true / transaction-local instead, because its connections may be
    reused.) No-op when owner is None (RLS off / no owner) -> byte-identical to
    today. Best-effort: a failed set is noted on stderr but does not abort
    (degrade-open; the schema policy is permissive on an unset GUC).

<!-- mios-src:03a2fc3a62e4 from usr/libexec/mios/mios-pg-query:280-289 -->

### `& <script>` keeps the callee a real script: its own line...

`& <script>` keeps the callee a real script: its own line numbers survive in
error records, and its `exit N` returns here through $LASTEXITCODE instead of
killing the runspace with the format buffer still unflushed.

<!-- mios-src:239dfde246ed from usr/libexec/mios/mios-powershell:81-83 -->

### mios-rag -- agent-pipe RAG over MiOS knowledge, on infra...

mios-rag -- agent-pipe RAG over MiOS knowledge, on infra MiOS already runs:
nomic-embed-text for embeddings + Postgres+pgvector for the vector store. Keeps
RAG in-loop for every agent/sub-agent turn WITHOUT OWUI's blocking pre-pipe
knowledge_search (operator 2026-05-20: "move RAG into the agent-pipe enrich
stage").

Subcommands:
  ingest [--dir D ...]   Chunk + embed the MiOS docs (and any extra dirs) into
                         the pgvector table `mios_rag`. Idempotent: clears +
                         rebuilds. Run on docs change.
  query  <text> [--k N]  Embed the text, vector-search the store, print the
                         top-N {source, text, score} as JSON.

WS-A3 cutover: the legacy DB (:8000) is RETIRED. The vector store is Postgres+
pgvector reached via the pure-python mios-pg-query wire client, and the ingest
INSERT binds source/content/emb as OUT-OF-BAND params (mios-pg-query --exec-json
extended protocol) -- no filesystem-sourced value is ever spliced into SQL.

Config (SSOT-overridable via env):
  MIOS_RAG_EMBED_ENDPOINT  default http://localhost:8450 (mios-llm-light /v1)
  MIOS_RAG_EMBED_MODEL     default nomic-embed-text
  MIOS_RAG_DOCS_DIR        default /usr/share/mios/docs
  MIOS_RAG_CHUNK_CHARS     default 700

<!-- mios-src:e341302c8e22 from usr/libexec/mios/mios-rag:5-28 -->

### mios-remember -- agent-authored durable memory...

mios-remember -- agent-authored durable memory (Letta/MemGPT core tier).

P4.2 (Letta/MemGPT active virtual memory): MiOS already has the PASSIVE tiers
-- per-conversation scratchpad (working), the knowledge table (auto-stored Q+A,
recall), and episodic SKILL.md + viking:// (archival). What was missing is the
ACTIVE / SELF-EDITING tier: the agent (or operator) deliberately WRITING a
durable fact it wants to keep -- Letta's core_memory_append. This shim is that
write half.

DESIGN respects the MiOS "NO context injection" rule ([[CLAUDE.md]]): memory is
TOOL-DRIVEN both ways -- the agent WRITES with this verb and READS with `recall`
(or viking_ls memory). Nothing is auto-prepended to prompts; the model learns
its memory by CALLING tools, not via a pre_llm_call inject.

Stored in Postgres+pgvector `agent_memory`, scope-tagged:
  global         -- durable facts true across all conversations
  agent:<name>   -- specific to one agent
  conversation:<id> -- scoped to one chat thread

WS-A3 cutover: the legacy DB (:8000) is RETIRED. The datastore is Postgres+pgvector,
and EVERY statement is parameterized -- values are bound out-of-band via
`mios-db --pg-json` (-> mios-pg-query extended protocol), never spliced into the
SQL string. The pg column is `mem_key` (this tool's `key`).

USAGE
  mios-remember add "<fact>" [--scope global] [--key <k>] [--source <s>]
  mios-remember list [--scope global] [--limit 30]
  mios-remember forget --key <k>          # delete by key
  mios-remember update --key <k> "<new>"  # core_memory_replace

Output JSON: {ok, ...}.

<!-- mios-src:b8fc21306475 from usr/libexec/mios/mios-remember:5-36 -->

### Run a parameterized statement / atomic batch via `mios-db...

Run a parameterized statement / atomic batch via `mios-db --pg-json`,
    which binds the values OUT-OF-BAND through mios-pg-query's extended protocol
    (no value is ever spliced into the SQL text -> SQL-injection-safe). Returns
    (ok, stdout). Degrade-open: any error -> (False, '') so a DB hiccup never
    breaks a turn.

<!-- mios-src:0cf8876fb590 from usr/libexec/mios/mios-remember:50-54 -->

### The ref set IS the SSOT's [image.sidecars] table, read...

The ref set IS the SSOT's [image.sidecars] table, read through the sanctioned
layered resolver -- not a mirror of it. A mirror silently drifts, and every
drifted ref feeds an image MiOS does not ship into the provenance record.
localhost/ refs are skipped: they are built in-image, so no registry can
resolve them (the same constraint mios.toml documents for firstboot_tokens).

<!-- mios-src:69474cabea8e from usr/libexec/mios/mios-resolve-latest:25-29 -->

### 'MiOS' scheduled-research runner -- the `do` target for...

'MiOS' scheduled-research runner -- the `do` target for schedule rules.

Reads the rule's stored prompt, runs it through the FULL agent-pipe (refine ->
swarm/council research), and posts the result to Discord via mios-discord-send.
This is what makes "do deep research on X every 30 minutes" actually deliver:
the cron-director fires `mios-scheduled-research --rule <name>` each interval.

Honest output: JSON describing what happened (researched + posted / why not) --
never a fabricated success.

<!-- mios-src:ee1c7e723348 from usr/libexec/mios/mios-scheduled-research:4-13 -->

### mios-skills -- Sequential Pattern Mining + cross-agent...

mios-skills -- Sequential Pattern Mining + cross-agent skill catalog CLI.

Phase C.2 of the AgentOS roadmap. Mines the Postgres `tool_call`
table for repeating verb sequences and codifies them as `skill`
rows that EVERY agent in the MiOS stack reads from a single source:

  * mios-agent-pipe (:8700) -- reads via /skills/* REST endpoints
                               AND directly from Postgres.
  * MiOS-Hermes (:8720)     -- pulls /skills/openai-tools at startup
                               so Hermes' OpenAI-compat tool surface
                               advertises promoted skills as callable
                               tools (no Hermes-side hardcoding).
  * MiOS-OpenCode           -- same /skills/openai-tools dump (or
                               direct Postgres read for offline-only
                               OpenCode runs that can't reach
                               agent-pipe).

Skills are typed-verb DAGs -- no English narrative, no per-agent
behaviour, fully parameterized. A skill body looks like:

    {"steps": [
       {"verb": "open_app",     "args": {"name": "$app"}},
       {"verb": "focus_window", "args": {"title": "$app"}},
       {"verb": "pc_type",      "args": {"text": "$body"}},
       {"verb": "pc_key",       "args": {"key": "ctrl+s"}}
     ],
     "params": ["app", "body"]}

That body is the same shape the Phase A.1 planner emits as a DAG --
so a skill is "a DAG the operator has decided to keep". The
parameters are substituted via simple $name token replacement at
run time.

Subcommands:

  mine [--window <hours>] [--min-support <N>] [--min-length <N>]
                            Run the SPM miner against tool_call
                            history. Emits skill rows with
                            source=mined, status=candidate. Prints
                            a per-candidate summary.

  list  [--status candidate|promoted|retired|all]
        [--source mined|operator|import|all]
                            List skill rows.

  show <name>               Print skill body JSON.

  run  <name> [--param key=value ...] [--session <session-id>]
                            Execute a skill against agent-pipe's
                            /skills/run endpoint (so the dispatch
                            goes through the same firewall + taint
                            + audit chain every verb does). Prints
                            per-step result.

  promote <name>            Mark a skill as runnable (status=promoted).
  retire  <name>            Hide a skill (status=retired).
  delete  <name>            Hard-remove the row (auditable rare).

  import [--source <dir>]   Load JSON skill templates from the seed
                            catalog directory (or operator-supplied
                            dir / stdin '-'). Same template shape
                            as a mined skill body; status=operator,
                            source=import.

  export [--name <name>]    Dump skills as NDJSON to stdout. With
                            no --name, dumps everything; with
                            --name, dumps a single skill.

  openai-tools              Print the OpenAI tool-schema dump for
                            all promoted skills (the same JSON
                            /skills/openai-tools serves). Use this
                            to wire Hermes / OpenCode to the
                            catalog from offline scripts.

Fully local: stdlib only (urllib + json + argparse + re + os +
sys + subprocess). Reads/writes Postgres/pgvector via the shared
`mios-db --pg-json` CLI (parameterized $1..$n placeholders bound
out-of-band over the pg v3 extended protocol -- no hand-rolled SQL
escaping, no request data ever spliced into SQL text), and hits
agent-pipe at MIOS_AGENT_PIPE_ENDPOINT for skill runs (so the
firewall / taint chain stays uniform).

SSOT: every knob (min_length, min_support, window_hours,
auto_promote_threshold, ...) reads from MIOS_SKILLS_* env, which
the userenv.sh slot map sources from mios.toml [skills].

<!-- mios-src:594e592c5c62 from usr/libexec/mios/mios-skills:4-89 -->

### Read rows from pgvector as list[dict] via parameterized...

Read rows from pgvector as list[dict] via parameterized mios-db --pg-json.

    `sql_inner` is the inner SELECT (with $1..$n placeholders); it is wrapped in
    json_agg INSIDE the envelope so the values bind out-of-band. Degrade-open ->
    [].

<!-- mios-src:28ee5252d7dc from usr/libexec/mios/mios-skills:117-121 -->

### Normalize verb args to a shape signature -- keys + arg...

Normalize verb args to a shape signature -- keys + arg types
    matter for grouping, but specific values become $-tokens so the
    mined skill becomes a template the operator can re-use across
    targets. Returns the deduplicated shape dict + a value map for
    later parameterization.

<!-- mios-src:bceb1f5a9345 from usr/libexec/mios/mios-skills:171-175 -->

### Pull recent successful tool_call rows for mining...

Pull recent successful tool_call rows for mining (Postgres).

    The legacy duration form (`time::now() - {h}h`) silently matched NOTHING
    on pg, so the miner used to see "no tool_call rows" and the training loop
    starved (operator 2026-06-10 "train MiOS AI"). The old emitted-edge
    exclusion was a legacy graph traversal with no pg analogue -- re-mined
    patterns dedup by name at insert time instead. hours/limit are int()-coerced
    and stay inline (integers don't bind reliably). Degrade-open -> [].

<!-- mios-src:4423273c74f0 from usr/libexec/mios/mios-skills:188-195 -->

### Assemble the skill body. Each step gets the sample args...

Assemble the skill body. Each step gets the sample args lifted
    from the mined session, but string values become $-tokens the
    operator can override at run time.

<!-- mios-src:313a0740412d from usr/libexec/mios/mios-skills:363-365 -->

### Recent success rate of a skill (looked up by name) from the...

Recent success rate of a skill (looked up by name) from the
    skill_invocation.success field. Returns (rate, samples).

    Degrade-open: (1.0, 0) on ANY error / DB miss / no invocation history ->
    NEVER blocks a skill that simply has no recorded runs yet (incl. a
    brand-new mined skill that doesn't exist in the DB at decision time).

    We first resolve the skill's record id by name, then filter
    skill_invocation rows by that ref within WINDOW_HOURS. The skill id is a
    DB-row value, so it binds out-of-band ($1) like every other external value.
    success may be NULL on still-open rows -> we drop None and only score
    closed invocations. Degrade-open -> (1.0, 0).

<!-- mios-src:8e8fe7ac0e5a from usr/libexec/mios/mios-skills:390-401 -->

### A single replay PASSES iff the skill run SUCCEEDED and...

A single replay PASSES iff the skill run SUCCEEDED and neither the Semantic
    Firewall nor the HITL gate intervened. The top-level success=false already
    captures a FATAL block (a blocked step fails the run); the per-step block flags
    additionally catch a NON-fatal block in a try-each skill that recovered on a
    later step -- a firewall/HITL trip is a reliability red flag even when the skill
    still produced an answer. Reads ONLY structured result fields (no text/keyword
    matching): success + the dispatch-emitted firewall_blocked/hitl_blocked markers.

<!-- mios-src:f37af662f6e4 from usr/libexec/mios/mios-skills:529-535 -->

### Replay a skill k times via ``run_once()`` (a 0-arg callable...

Replay a skill k times via ``run_once()`` (a 0-arg callable returning the
    /skills/run envelope, or raising when the replay is unreachable). The gate
    PASSES iff EVERY one of the k replays passes _passk_run_ok -- ONE failure vetoes
    (pass^k: ALL k repeats must succeed). Fail-closed: a replay that raises counts
    as a failed replay. All k are attempted so the message can report the true
    success count. Returns (passed, n_ok, human_message).

<!-- mios-src:07ca09f59540 from usr/libexec/mios/mios-skills:546-551 -->

### P4-full outcome-driven lifecycle

P4-full outcome-driven lifecycle: demote PROMOTED skills whose recent
    success rate is below MIN_SUCCESS_RATE (with >= MIN_SUCCESS_SAMPLES runs) back
    to CANDIDATE (re-mineable, not retired -- a bad week shouldn't bury a good
    skill). --apply actually demotes; default is DRY-RUN (logs intended demotions
    so the rate math is confirmed on real data first). Inert until skill_invocation
    outcomes accumulate. operator 2026-06-10.

<!-- mios-src:daf9450b8243 from usr/libexec/mios/mios-skills:590-595 -->

### mios-suggestion-refresh -- generate MiOS-aware starter...

mios-suggestion-refresh -- generate MiOS-aware starter chips +
write them into OWUI's ui.prompt_suggestions.

OPERATOR DIRECTIVE
  "I WANT MIOS STARTER QUERIES THAT REVOLVE/EVOLVE/ITERATE"

OWUI's built-in ENABLE_SUGGESTION_GENERATION path produces generic
chatbot prompts (and on this stack it wasn't producing them at all).
This shim takes over: gathers MiOS state (system snapshot, recent
kanban, daemon nudges, recent refine intents) and calls the small
refine model with a tight prompt asking for 5 starter chips. The
chips:

  - Reflect what the operator just did / has queued
  - Surface unfinished work (recent unsatisfied verdicts)
  - Rotate over time as the source signals change
  - Are localised by the model based on the input context
    (no hardcoded English here)

RUN AT
  Periodic (every N min, default 10) via mios-daemon's loop, OR
  manually after a state change. Idempotent: writes the new chip
  list verbatim, no diff merge.

OUTPUT
  Updates webui.db's config row -> data.ui.prompt_suggestions
  with the new chip array. Stdout = JSON envelope with the
  chips + the metadata used to generate them (for debug).

EXIT CODES
  0  refreshed
  1  refine model unavailable / OWUI DB missing / parse fail
  2  bad arguments

<!-- mios-src:e1be60027706 from usr/libexec/mios/mios-suggestion-refresh:4-37 -->

### Cheap snapshot of the running stack. Pulls from the...

Cheap snapshot of the running stack. Pulls from the daemon's
    last state.json (fresh as of its last loop tick).

    Excludes `classify` deliberately -- the daemon's journal-tail
    classifier picks up ANY recent error chatter (curl probes, dev
    activity, transient HTTP 4xx) and the suggestion model latches
    onto those, producing debug-flavored chips that don't match the
    operator's actual daily-use needs. Keep launch_failures (real
    operator-visible failures) + satisfaction (real verdicts).

<!-- mios-src:26ddf2309b78 from usr/libexec/mios/mios-suggestion-refresh:80-88 -->

### Read MiOS's REAL capability surface from the SSOT --...

Read MiOS's REAL capability surface from the SSOT -- mios.toml
    [verbs.*] + [recipes.*] (+ /etc overlay) -- so generated chips
    cover the BREADTH of what MiOS can actually do, derived from the
    catalog rather than a hardcoded English list (operator directive:
    no hardcoded English; vast functionality coverage). Returns short
    "<name>: <desc>" descriptors the model turns into natural, localised
    starter chips. Falls back to concrete baseline seeds if the catalog
    can't be read.

<!-- mios-src:c45455ac0512 from usr/libexec/mios/mios-suggestion-refresh:113-120 -->

### Run one SQL statement on pgvector via mios-pg-query...

Run one SQL statement on pgvector via mios-pg-query (pure-python pg
    wire client -- no psql/psycopg/podman). Rows -> lists of string columns
    (psql -At: tab-separated columns, newline-separated rows). Degrade-open
    to [] on any error so a transient DB issue can't blank the chip context.

<!-- mios-src:59e854e63137 from usr/libexec/mios/mios-suggestion-refresh:142-145 -->

### Read recent operator-relevant kanban entries from the...

Read recent operator-relevant kanban entries from the pgvector
    `kanban` table (authoritative; retired the legacy shadow). These
    are real queued tasks the operator opened; ideal chip seeds.

<!-- mios-src:54c74576346e from usr/libexec/mios/mios-suggestion-refresh:158-160 -->

### Pull the last few refine summaries from the pgvector...

Pull the last few refine summaries from the pgvector `event`
    table. Used to bias suggestions away from what the operator JUST
    asked (avoid repetition) and toward complementary follow-ups.

<!-- mios-src:3353081e54f5 from usr/libexec/mios/mios-suggestion-refresh:170-172 -->

### Compose the prompt the refine model sees. The chip space is...

Compose the prompt the refine model sees. The chip space is
    biased toward operator-useful daily actions; transient debug
    chatter is intentionally excluded from the context.

<!-- mios-src:765eccb5a09d from usr/libexec/mios/mios-suggestion-refresh:193-195 -->

### Push the chip list through OWUI's POST /api/v1/configs/...

Push the chip list through OWUI's POST /api/v1/configs/
    suggestions endpoint. The API path is load-bearing -- writing
    DIRECTLY to webui.db sets the value but OWUI's runtime cache
    doesn't see it until the next service restart, so new chats
    keep showing the cached (often empty) chip row. The API
    handler updates both the persistence layer + the in-memory
    config so a chat opened ~5s later sees the fresh chips.
    Each chip is wrapped per OWUI's schema:
      {title: [primary, secondary], content: <text-sent-on-click>}
    We use the chip text for both title[0] and content so a click
    sends the literal phrase into the input.

<!-- mios-src:90c31c773bdb from usr/libexec/mios/mios-suggestion-refresh:361-371 -->

### mios-summarize -- tiered summarisation (the L0/L1/L2 gating...

mios-summarize -- tiered summarisation (the L0/L1/L2 gating primitive).

P5.3 (Hermes v2026.5.28 / OpenViking brief): the ingestion + context-gating
layers need a CHEAP local model that turns a document into nested tiers:

  L0 -- ~100-token ABSTRACT (one-sentence semantic summary; fast nav / cheap
        similarity / directory listing).
  L1 -- ~2000-token OVERVIEW (sections, layout, key points, usage).
  L2 -- the RAW original (returned only on strict demand; here just the
        verbatim text, optionally truncated).

Runs on the CPU light lane (MIOS_SUMMARIZE_ENDPOINT, default the mios-llm-light
:8450 /v1 lane) with a small model (MIOS_SUMMARIZE_MODEL) so it never contends
with the dGPU primary -- exactly the "summarisation VLM for ingestion-side
parsing" the brief calls for. Offline-first: local llama.cpp only, no cloud.

INPUT (one of):
  --text "<inline text>"
  --file <path>            # read a UTF-8 text/markdown file
  (stdin)                  # piped text when neither flag given

USAGE
  mios-summarize --file notes.md
  mios-summarize --text "..." --tiers l0,l1
  echo "Long doc" | mios-summarize --json

OUTPUT (--json default true):
  {ok, source, chars_in, l0, l1, l2_chars, model}
  on failure: {ok:false, error}

<!-- mios-src:dd0b0d2a20f9 from usr/libexec/mios/mios-summarize:4-33 -->

### [bootstrap].bootstrap_repo, NOT the first textual match...

[bootstrap].bootstrap_repo, NOT the first textual match: [urls] declares a
key of the same name holding the clone URL, and it appears earlier in the
file. Matching on text alone resolved the URL, os.path.isfile said no, and
the projection reported "bootstrap repo not found" and skipped -- which is
why the two repositories drifted while a sync tool appeared to run.

<!-- mios-src:cf05c3cf334e from usr/libexec/mios/mios-sync-toml:26-30 -->

### (start, end) index pair for [section]'s OWNED block: the...

(start, end) index pair for [section]'s OWNED block: the '[section]' header line through
    its last key/content line. None if the section is absent.

    The end is the next '[' header (top-level OR sub-table) MINUS the trailing run of comment /
    blank lines immediately above it -- because in this codebase every section is introduced by a
    comment header ABOVE it, so that trailing run belongs to the NEXT section, not this one.
    Including it was the bug that injected the [image.sidecars] header above [ports.lan_firewall]
    and deleted the lan_firewall comments. For [ports] the span stops at [ports.lan_firewall];
    the derived copies' lan_firewall sub-table + its comment header are left untouched.

<!-- mios-src:95c7dba0e8e1 from usr/libexec/mios/mios-sync-toml:65-73 -->

### usr/libexec/mios/mios-sys-env <refresh|get|help> [--json]...

/usr/libexec/mios/mios-sys-env <refresh|get|help> [--json]

Live system/environment probe persisted to pgvector so EVERY agent reads a
fast, always-current snapshot from the shared DB -- the same cache pattern
mios-daemon uses for directory_entry.

Operator 2026-05-23: "mios-os-control should fetch live system apps from a
tool/skill for probing systems/environment live and storing+updating a
mios-sys-env database; ALL agents share global read+write access (via verbs)".

What it stores (single row `sys_env:current`, in sys_env table):
  env   -- the full `mios-env-probe --json` envelope (identity, host HW, stack
           services up/down, loaded models, launchable-app counts).
  apps  -- live launchable inventory, names per category (mios-apps --names
           --category <cat>), capped -- "the live system apps".
  indexed_at / host / source -- provenance.

Subcommands:
  refresh [--json]   WRITE: probe live + upsert sys_env:current. Any agent can
                     call this to UPDATE the shared env DB (sys_env_refresh verb).
  get [--json] [--full]
                     READ: return sys_env:current. COMPACT by default (env +
                     per-category app counts + a small sample) so it stays
                     parseable under the broker's ~6 KB stdout cap; --full emits
                     the complete inventory (CLI/dashboard). If the row is
                     missing, falls back to a live (non-persisted) probe so the
                     read is always useful (sys_env verb).
  help

SSOT: DB endpoint + creds from MIOS_DB_* env (identical to mios-daemon); the
app categories come from mios-apps. No hardcoded literals.

<!-- mios-src:302766ed89cb from usr/libexec/mios/mios-sys-env:4-35 -->

### mios-system-status -- emit a single JSON blob with the live...

mios-system-status -- emit a single JSON blob with the live MiOS
host dashboard data the chat model needs to answer "system status?"
questions WITHOUT fabricating fields.

Backs the `system_status` native MiOS Verb. The model calls one
tool, gets one structured object, and reports verbatim -- no more
"No NVIDIA driver detected" hallucinations from a model assembling
the dashboard out of 4 separate shell-tool outputs.

All probes are best-effort. A missing field comes back as null
(NOT a fabricated default), so the model can correctly say
"GPU info unavailable" instead of inventing one.

Fields:
  cpu         {model, cores, load_1m, load_5m, load_15m, load_pct_of_cores}
  gpu         list of {name, vram_mib, driver}   (nvidia-smi / rocm-smi)
  memory      {total_mib, used_mib, available_mib, swap_total_mib}
  disk        list of {mount, total, used, available, pct}
  services    {failed: [...], active_count, mios: {<name>: <state>}}
  models      list of {model} served on the /v1 lane (mios-llm-light /v1/models)
  uptime_s    int
  ts          float (unix-epoch)

<!-- mios-src:6d81098ea81d from usr/libexec/mios/mios-system-status:4-26 -->

### CPU model name, logical core count, and load average...

CPU model name, logical core count, and load average (1/5/15 min) plus
    a derived load-vs-cores hint. /proc + os only -- no extra deps. Missing
    fields come back null (never a fabricated default), matching the rest of
    this probe. Added 2026-06-04: system_status carried NO cpu field, so a
    "CPU status?" question had nothing to report and the local-state formatter
    mislabelled the GPU block as 'CPU'.

<!-- mios-src:cfa18345df16 from usr/libexec/mios/mios-system-status:106-111 -->

### OS identity -- the field the chat model needs so it STOPS...

OS identity -- the field the chat model needs so it STOPS guessing it runs
    on 'Windows 10' (its base-model prior) when the SOUL says host facts come from
    this tool ONLY. Every value is probed LIVE (no hardcoded OS string) and
    degrades to null on failure, so the model reports 'couldn't determine' instead
    of inventing. On WSL2 the WINDOWS HOST caption is probed best-effort via
    mios-windows ps -> the agent learns the real host (e.g. 'Windows 11 Pro for
    Workstations 10.0.x'), not the NT-10.0 'Windows 10' prior.

<!-- mios-src:9858fd5078de from usr/libexec/mios/mios-system-status:239-245 -->

### mios-sysview -- native system-inspection tool: the SSOT for...

mios-sysview -- native system-inspection tool: the SSOT for the
journalctl / ps / podman command construction that agent-pipe's verbs used
to hardcode INLINE. Operator 2026-05-21: "no hardcodes ... should all be
native tools/skills/recipes ... unless baked in the modelfile or docs".

These ops build their command from TYPED, OPTIONAL args (conditional flags
-- e.g. `-u UNIT` only when a unit is given), which static [recipes.*]
templates can't express. So they live here as a helper TOOL (the same
pattern as mios-system-status), and agent-pipe's arms just delegate to it
-- the command literals are gone from the dispatch code.

Subcommands:
  logs   [--unit U] [--since S] [--lines N] [--level L]   journal (READ)
  proc   [--filter F] [--sort rss|cpu|pid] [--limit N]    processes (READ)
  containers [--name N]                                   podman ps (READ)
  container-restart --name N                              podman restart (WRITE)

<!-- mios-src:1036d3f248b4 from usr/libexec/mios/mios-sysview:4-20 -->

### mios-text-edit -- native text-editor primitive. Replaces...

mios-text-edit -- native text-editor primitive.

Replaces the fragile `pc_type` + `pc_key ctrl+s` chain the
Phase A.1 planner used to emit for "open notepad and type X
then save". Synthetic keystroke editing fails for many reasons
the operator has hit in production:

  * The focused window may not be the editor (race on
    focus_window).
  * Special characters in the body get re-interpreted by the
    target's IME / autocomplete.
  * `ctrl+s` triggers different dialogs depending on app state
    (Save As on a never-saved doc).
  * No way to verify the write landed.

Native file ops bypass all of that: read / write / replace /
insert against the filesystem directly. The agent gets an
exit_code + structured result instead of "did the keypress
sequence work or not". Mirrors Anthropic's text_editor_*
schema shape (view / create / str_replace / insert) so prompt
patterns developed against that ecosystem transfer cleanly.

Subcommands:

  view PATH [--start N] [--end M]
                       If PATH is a directory: list non-hidden
                       entries up to 2 levels deep. If a file:
                       print the file with 1-indexed line
                       numbers; optional --start / --end to
                       restrict the range (inclusive bounds).
                       Defaults to first 200 lines on huge
                       files so the agent doesn't blow its
                       context window.

  create PATH --content @file
                       Create a new file. Fails (exit 1) if
                       PATH already exists -- use str_replace
                       or insert to mutate an existing file.
                       Creates parent directories implicitly.

  str_replace PATH --old @file --new @file
                       Exact-match replace. The old string must
                       occur EXACTLY once in the file; multiple
                       matches or zero matches both fail. This
                       is the "atomic edit" primitive -- pair
                       with `view` to verify the target string
                       exists before the replace.

  insert PATH --line N --content @file
                       Insert content AFTER line N (1-indexed).
                       --line 0 prepends to the file. Idempotent
                       w.r.t. the agent's ability to verify via
                       `view` after the call.

`--content @file` / `--old @file` / `--new @file` arguments
support three forms (same shape as mios-passport / mios-skills):

  @PATH      read from a path
  -          read from stdin (one arg only)
  literal    the raw string

Output is structured JSON with a fixed shape:

  {"ok": true,  "verb": "view",   "path": "...", "result": "...", "bytes": N}
  {"ok": false, "verb": "create", "path": "...", "error": "exists"}

so the agent / dispatch layer parses a stable envelope instead
of free-text. The CAPTURE_JSON: broker protocol picks the
stdout up verbatim.

<!-- mios-src:8db6025d19ab from usr/libexec/mios/mios-text-edit:4-73 -->

### Reject path-traversal + writes to system paths. Returns...

Reject path-traversal + writes to system paths. Returns None
    when path is acceptable; an error string otherwise.

    Resolves the path with os.path.realpath so symlink escapes hit
    the same guard as direct prefix matches. The agent can't smuggle
    `/var/lib/mios/foo` -> `/etc/passwd` via a pre-staged symlink.

<!-- mios-src:c2103d1fe8ae from usr/libexec/mios/mios-text-edit:108-113 -->

### mios-tool-search <query> [--limit N] [--json]...

mios-tool-search <query> [--limit N] [--json]

Natural-language search over the agent-pipe verb catalog. Calls
agent-pipe's /v1/tool-search endpoint, which embeds the query via
nomic-embed-text + cosine-scores against the cached embeddings of
every visible verb (tier=core or common; tier=rare hidden).

Returns top-k {name, sig, desc, tier, score}. Used by the planner
when no listed verb obviously fits an intent -- progressive-disclosure
pattern from RAG-MCP (arXiv 2505.03275): instead of stuffing the full
catalog into every planner turn, plan with a small core set + this
retrieval verb for the rest.

SSOT: mios.toml [verbs.*] is the catalog; this shim is a thin
client. Operator binding 2026-05-19.

<!-- mios-src:f5657aba59af from usr/libexec/mios/mios-tool-search:4-19 -->

### mios-verify-launch -- ask the always-on mios-daemon-agent...

mios-verify-launch -- ask the always-on mios-daemon-agent (the iGPU
daemon-tier brain) whether an app ACTUALLY launched, IN-TURN.

Operator 2026-05-25 (4A): "the iGPU's always-on daemon-agent ... runs the
Definition-of-Done success check; the main agent/sub-agents just delegate and
read the success signal." The daemon already runs a post-hoc launch_verifier
loop (it scans recent chats and records false launch-success claims to
/var/lib/mios/daemon/launch_failures.json). This verb closes the loop
SYNCHRONOUSLY: after an agent fires an OS-control verb (open_app / window /
pc_*), it calls `verify_launch {app}` and gets the daemon's consolidated
verdict -- a live read-only window/process probe (mios-window-active --present,
which never launches anything) PLUS the daemon's own recorded false-success
history for that app. The agent then retries or reports honestly instead of
blind-claiming success.

GLOBAL tool (operator: ALL agents/sub-agents/nodes use ALL tools globally),
reached through the broker like any other verb. Offline-first: if the daemon is
down it returns success=false with an honest error -- it NEVER fabricates a
launched=true.

SSOT: MIOS_DAEMON_AGENT_URL (else built from MIOS_PORTS_DAEMON_AGENT, default
8644 -- matches the daemon's AGENT_PORT / mios.toml [agents.mios-daemon-agent]).

Output JSON: {success, app, launched, verdict, recent_failures, checked_by}.

<!-- mios-src:366be5c499ec from usr/libexec/mios/mios-verify-launch:4-28 -->

### mios-viking -- OpenViking-style viking:// VFS over the...

mios-viking -- OpenViking-style viking:// VFS over the local second brain.

P5.2 (OpenViking brief): a virtual filesystem with HIERARCHICAL TOKEN GATING
(L0 abstract -> L1 overview -> L2 raw) over MiOS's LOCAL knowledge stores --
NO external ByteDance container, NO cloud, fully offline. The agent skims L0
abstracts for navigation, targets a node, reads its L1 overview, and only
fetches the L2 raw detail on strict demand -- so the active context window is
never flooded with full documents.

NAMESPACES (viking://<ns>/...):
  skills     -- the episodic SKILL.md files (/var/lib/mios/ai/skills/episodic)
                written by the self-learn loop (P5.7).
  knowledge  -- the Postgres+pgvector `knowledge` table (every finished Q+A).
  memory     -- the operator AI memory dir (/var/lib/mios/ai/memory), if present.

TIERS:
  l0  -- one-line abstract (cheap nav). For skills/knowledge the stored
         goal/question IS the L0; otherwise mios-summarize generates it.
  l1  -- structured overview (mios-summarize, generated on read).
  l2  -- the raw node content.

USAGE
  mios-viking ls skills [--limit 20]
  mios-viking ls knowledge [--query "..."]      # query => semantic-ish filter
  mios-viking cat skills/<file> [--tier l0|l1|l2]
  mios-viking cat knowledge/<id> --tier l2
  mios-viking find "<query>" [--ns skills,knowledge]   # L0 search across NS

All output JSON: {ok, ...}. Read-only over local stores; never mutates.

<!-- mios-src:44b5ce8663f2 from usr/libexec/mios/mios-viking:4-33 -->

### WS-A3

WS-A3: read rows as list[dict] from the live pgvector store via
    `mios-db --pg-json`, which binds values OUT-OF-BAND ($1..$n) -- the inner
    SELECT uses placeholders, never f-string-spliced values. (The retired
    legacy knowledge-ns transport _db_sql is gone.) Degrade-open -> [].

<!-- mios-src:c4aef534059d from usr/libexec/mios/mios-viking:51-54 -->

### mios-web-extract -- fetch a URL and return its READABLE...

mios-web-extract -- fetch a URL and return its READABLE TEXT.

The web_search verb returns short engine SNIPPETS, which is why the agent
fabricated (e.g. invented a "car" etymology + cited the alphabet page) when a
snippet didn't actually contain the answer (operator 2026-05-23). web_extract
fetches the ACTUAL page text so an agent can GROUND on real content -- or, when
the page does not contain the answer, see that it doesn't and say so.

Offline-first: plain stdlib fetch (no cloud AI), crude HTML->text strip. Pairs
with web_search (search -> pick a URL -> extract its content -> ground).
SSOT: limit/timeout via flags; no endpoints to configure.

<!-- mios-src:92d3b8d59df1 from usr/libexec/mios/mios-web-extract:3-14 -->

### mios-web-search -- native WEB-search verb backend...

mios-web-search -- native WEB-search verb backend (concurrent fan-out).

Queries the LOCAL, self-hosted SearXNG metasearch (offline-first; NO cloud
AI dependency -- Architectural Law 5) and returns clean, citable results so
agents GROUND current-world answers (weather, news, events, prices, facts,
general knowledge) on REAL fetched data instead of fabricating from model
memory.

Operator 2026-05-21: the chat agent invented a weather report -- wrong city
(Port Hope vs. the asked-about Toronto), wrong units (Fahrenheit for a
Canadian user), and made-up event details -- because NO web_search verb
existed; only filesystem search (everything_search / fs_search) did, whose
own descriptions tell the model to "use a WEB search" that was never wired.
SearXNG was already up (:8800) and advertised as the web_search backend.

Operator 2026-05-22: "have web tools shoot off more web queries concurrently".
QUERY FAN-OUT (--fanout K, the industry pattern behind Google AI Mode /
RAG-Fusion / ParallelSearch): expand one query into K diverse sub-queries via
the always-warm micro-LLM, fire them at SearXNG CONCURRENTLY, then merge with
Reciprocal Rank Fusion + URL dedupe. K parallel queries overlap latency (8
parallel ~= 300-500ms vs ~200ms single) and surface evidence one phrasing
misses. SearXNG's own limiter is off + granian workers are bumped so the
local instance absorbs the burst; agent-pipe bounds CROSS-agent concurrency.

SSOT: SearXNG endpoint = $MIOS_URLS_SEARXNG; micro-LLM = $MIOS_MICRO_MODEL /
$MIOS_MICRO_ENDPOINT (same as agent-pipe); fan-out knobs = $MIOS_WEB_FANOUT /
$MIOS_WEB_FANOUT_WORKERS / $MIOS_WEB_RRF_K; the anchor stopword screen =
mios.toml [search].anchor_stopwords ($MIOS_WEB_ANCHOR_STOPWORDS CSV) -- all
rendered from mios.toml. The localhost fallbacks + the documented default
screen are the only literals, matching the unit/SSOT defaults. The query
tokenizer is unicode-aware (CJK/accented scripts tokenize, never to zero).

<!-- mios-src:b205b996c73d from usr/libexec/mios/mios-web-search:4-35 -->

### Resolve a list tunable from SSOT

Resolve a list tunable from SSOT: a CSV env override (rendered from
    mios.toml by the userenv slot map) -> the layered mios.toml [section].key
    (vendor <- /etc <- ~/.config) -> the documented literal default. Keeps this
    standalone CLI's screen lists OUT of code (Architectural Law 7).

<!-- mios-src:e81493903655 from usr/libexec/mios/mios-web-search:84-87 -->

### One SearXNG JSON query. Returns the FULL result list...

One SearXNG JSON query. Returns the FULL result list (caller slices)
    plus instant answers + infoboxes. category='news' targets SearXNG's NEWS
    engines (dated stories) instead of the general web -- operator 2026-05-24: a
    vague 'current global trending' matched the 'Current' banking app + 'electric
    current' (Wikipedia) on general search; the news category returns real dated
    news stories. time_range (day|week|month|year) recency-filters the GENERAL
    web -- operator 2026-05-25: this instance's news ENGINES are IP-blocked so the
    news category returns only stale wikinews; a time_range on GENERAL search
    pulls CURRENT content (CNBC/Reuters/FT/2026 outlooks) and drops the evergreen
    Wikipedia / stale-listicle junk WITHOUT needing the blocked news engines.

<!-- mios-src:95a0141980bd from usr/libexec/mios/mios-web-search:104-113 -->

### mios-win-scan -- Wine-free native Windows app/game...

mios-win-scan -- Wine-free native Windows app/game enumeration off /mnt.

The Windows-side PowerShell scans (Get-StartApps / Get-AppxPackage / Steam
registry walk) only reach the real host through WSL-interop, which is silently
hijacked when a Wine binfmt handler intercepts .exe -- so they run in Wine's
fake Windows and see none of the host's real software. Every source below is a
plain file on the drvfs mount, read directly with ZERO .exe execution.

Output (stdout), one line per app:  <name>|<launch-target>|<category>
  launch-target is whatever `mios-windows launch` resolves on the real host:
  a steam:// / com.epicgames.launcher:// URI, a shell:AppsFolder AUMID, or a
  filesystem path (.lnk / .exe) that the executor Start-Process'es.

<!-- mios-src:10ab4009de18 from usr/libexec/mios/mios-win-scan:5-17 -->

### ADR-0016 D5

ADR-0016 D5: an off-box front door makes the auth controls a precondition.
Degrade OPEN (Law 12) -- a policy default must never brick a boot -- but say so
here, in blade.env and in role.active, so a seat is never silently exposed.

<!-- mios-src:220eb578c99e from usr/libexec/mios/role-apply:141-143 -->

### Law 10 keeps these four out of install.env -- a space...

Law 10 keeps these four out of install.env -- a space, parentheses or a
{} placeholder, or an empty value the resolver drops -- while ExecStart
references each BARE, which the renderer leaves for systemd to expand.
Without a supply here systemd expanded all four to the empty string
(task_3362). `mios-gate protected-refs` fails the build on the general case.

<!-- mios-src:18cec70748ce from usr/share/mios/mios.toml:1056-1060 -->

### task_972. Each entry pins path:KEY=VALUE, not just path:KEY....

task_972. Each entry pins path:KEY=VALUE, not just path:KEY. A key-only register
cannot tell the shipped placeholder from an operator's real password: setting
MIOS_PGVECTOR_PASS in the build environment bakes that value into a 0644 file under
/usr and the key-only gate stayed green. Every value below is a placeholder
and none is a secret; changing any of them is a NEW finding by design.

<!-- mios-src:3fd9bba45331 from usr/share/mios/mios.toml:1653-1657 -->

### Shrink-only, and set to the measured count so the next...

Shrink-only, and set to the measured count so the next untagged file
fails the gate. At 130 against an actual 42 it had 88 files of slack
and could not catch a regression until 88 files had degraded.

<!-- mios-src:e84f0cbbf928 from usr/share/mios/mios.toml:1814-1816 -->

### The scope this registry is measured against. `mios-gate...

The scope this registry is measured against. `mios-gate projection-coverage`
reads these globs, enumerates what they match, and asserts every hit is on
`surfaces` above or itemised on `exempt` below -- the reverse direction that
`check_projection_registry` (forward only) never asked. Single `*`, final
path segment only; a pattern the matcher cannot express is a hard error, not
a silent widening.

<!-- mios-src:4e78848ac35e from usr/share/mios/mios.toml:2203-2208 -->

### Law 10 BARE-SAFE-ENV

Law 10 BARE-SAFE-ENV: /etc/mios/install.env is bare KEY=value, readable by
all three parsers (systemd EnvironmentFile=, bash source, podman --env-file).
A value carrying whitespace, a quote, `$`, a backtick or `#` cannot be written
bare without changing what it means, so system-sync-env.sh skips it.

Skipping SILENTLY is the defect: MIOS_AI_ENDPOINT was dropped this way and
Law 5 routes every agent through it (task_997). These keys are declared, with
the reason each value cannot be bare; ANY other unsafe value now fails the
render instead of vanishing. Shrink-only: max_declared comes down as values
are made bare, never up.

<!-- mios-src:d6cb288e3660 from usr/share/mios/mios.toml:2244-2253 -->

### Law 11 SECRETS-NEVER-IN-ENV

Law 11 SECRETS-NEVER-IN-ENV: secret-bearing environment variable names
that must NEVER leak into world-readable files like /etc/mios/install.env.
min_keys is a floor -- an explicit register shared between postcheck,
system-sync-env.sh, and mios-gate credentials.rs.

<!-- mios-src:8012330753cc from usr/share/mios/mios.toml:2264-2267 -->

### How a floating ref ("latest") becomes a concrete one at...

How a floating ref ("latest") becomes a concrete one at bake time when its
upstream publishes no `latest`: a tag counts only if it matches one of these
release shapes, shapes are tried in order, and the highest version of the
first shape that matches anything wins. Read by mios-bake-plan latest-image /
latest-git. A suffix (-rc1, -amd64, -rootless) never matches, and lowercase
letter series (alpha tags) never match git_shapes.

<!-- mios-src:08d00a808560 from usr/share/mios/mios.toml:7437-7442 -->

### blocks_boot = false is Law 12: enrolment never gates a...

blocks_boot = false is Law 12: enrolment never gates a boot.
federate = "native": peers join via each system's OWN mechanism -- k3s
server/agent join, corosync membership -- automatically, never by hand.

<!-- mios-src:46f29ed120aa from usr/share/mios/mios.toml:9715-9717 -->

### task_974. miosd Check implementations that were never...

task_974. miosd Check implementations that were never written: their `run` takes
`_ctx`, never reads the tree, and returned a constant Verdict::Pass with a
message CLAIMING verification. Containerfile:103 invokes this suite at every
bake, so the bake's own gate was certifying a tree it had not read -- pointed
at a directory that does not exist it reported "55 passed". They now report
Verdict::Skip("NOT IMPLEMENTED: ..."), which is the truth, and are registered
here shrink-only: implement a check and take it off the list; a new stub fails
the gate. Gate: `mios-gate drift-stubs`.
Ceilings that are GENERATED budgets rather than shrink-only ratchets, so
check_ratchet_direction lets them rise. Itemised with a reason on purpose: a
bare key here is indistinguishable from a silent skip, and a skip nobody can
see is how a gate stops being one. An entry naming something that is not a
ceiling is a finding, so a stale exemption cannot lie in wait for a future key
of that name.

A TABLE of key -> reason, deliberately not an array of inline tables: that
shape renders differently in the two resolver twins, and its registered
divergence set is a shrink-only 12 that a thirteenth key breaches
(check_resolver_differential_parity). Law 13 costs a shape here.

<!-- mios-src:a3974980f932 from usr/share/mios/mios.toml:10635-10653 -->

### task_984. Budget keys that MUST exist in [agent_pipe] or...

task_984. Budget keys that MUST exist in [agent_pipe] or [dispatch].

Enumerating the tables catches a key ADDED with no consumer. It cannot catch
a key DELETED, because a deleted key is simply absent from the enumeration --
the set shrinks and nothing is missing from it. The hardcoded nine-name list
this replaced was doing two jobs and only one of them was the defect; this is
the other one, moved out of Rust source and into SSOT where an
operator-tunable registry belongs (Law 7).

Seeded with exactly the nine the old constant named, so the floor is no
weaker than what it replaced. Removing an entry here is removing a guarantee:
do it only when the knob is genuinely retired.

<!-- mios-src:79457e3dfd8e from usr/share/mios/mios.toml:10659-10670 -->

### [agent_pipe]/[dispatch] keys that SSOT declares and NOTHING...

[agent_pipe]/[dispatch] keys that SSOT declares and NOTHING reads.
Shrink-only, and the ceiling may never sit above the measurement. Itemised
rather than a bare count: a count lets one dead key swap for another without
the gate noticing, which is the defect [docs] already carries.

mios-aiplane-lint walks all 128 scalar leaves under both tables. It used to
walk a hardcoded list of nine, so these nine were invisible: an operator
could set any of them and nothing would happen, silently.

Each is a knob with no reader. Wire it or delete it -- do not just move the
ceiling. `reflexion_limit` and `tool_loop_limit` are named by
tools/drift-checks.py, but only as keys it asserts are PRESENT; naming a key
is not consuming it, and that is exactly how they stayed dead.

<!-- mios-src:93be7cd6618b from usr/share/mios/mios.toml:10683-10695 -->

### task_975. Phase scripts present in automation/ that build.sh...

task_975. Phase scripts present in automation/ that build.sh does NOT run.
Shrink-only, and the ceiling may never sit above the measurement.

The register is empty as of task_962 stage 5. It previously held
55-native-build.sh on the reasoning that registering it would put miosd on
PATH -- its `ln -sf "${DEST_DIR}/${bin}" "/usr/bin/${bin}"` is the only such
line in the tree -- and so arm the `command -v miosd` gates in every stage
numbered above 55, producing a split brain inside one bake.

That reasoning read the script instead of measuring whether the line runs.
55-native-build.sh guards its whole body on `command -v cargo`, no [packages]
section installs a Rust toolchain, and no phase dnf-installs one: in a bake
the stage prints its "cargo toolchain not available" warning and does nothing.
The binaries in the image come from the Containerfile's rust-builder stage,
COPYed to /usr/libexec/mios, which is not on PATH -- so no stage, above or
below 55, has ever reached its Rust branch. Registering 55 arms nothing.
It also cannot change which phases run: build.sh selected via the
automation/[0-9][0-9]-*.sh glob (build_catalog_authoritative = false and no
build_phases.json), and that glob already included 55.

<!-- mios-src:df150cc6b600 from usr/share/mios/mios.toml:10769-10787 -->

### Keys the Python and Rust resolvers do not agree on....

Keys the Python and Rust resolvers do not agree on. Shrink-only.
The parity check never ran until the binary was found in target/debug,
so this is the first measurement, not a regression. task_2317 drives it
to zero by making one implementation authoritative.

<!-- mios-src:c3a6640de0d8 from usr/share/mios/mios.toml:10890-10893 -->

### Values that differ for keys both resolvers emit. The...

Values that differ for keys both resolvers emit. The remainder is
Python's repr of nested arrays/tables, which needs preserve_order on
the toml crate to match -- task_2317, with a toolchain that can link.

<!-- mios-src:2ebc31cc089b from usr/share/mios/mios.toml:10895-10897 -->

### Itemised shrink-only register of code files retaining...

Itemised shrink-only register of code files retaining retired ports (task_946 / LAW5-01).
Strictly limited to intentional cleanup routines (e.g. Setup-MiOSLanPortProxy.ps1) and regression test fixtures.
Any unlisted code file under usr/libexec/mios or usr/lib/mios containing a retired port FAILS the gate.

<!-- mios-src:4908eb7a70f4 from usr/share/mios/mios.toml:11028-11030 -->

### Raised from 3032

Raised from 3032: genuine new tracked deliverables this cycle -- the
provider-neutral secret-transport docs/prompts, the Gemini deep-research
prompt, the FOSS-conformant harness (verification_gates.py, the invariants/
SKIP-capable test scripts, .devcontainer/README.md), and the four
automation/01-04-*.sh pipeline stubs mainlined from the mios-sync artifact
bundle. Not itemised in [drift.generated_ceilings] below -- that table is
only for values a tool regenerates from measurement (max_tracked_mb); this
ceiling remains hand-edited and shrink-only going forward from this floor.
GENERATED by tools/native/mios-size-ceiling; do not hand-edit. The valid band
is round(tracked MiB) .. that + tracked_mb_headroom, and a committed value
outside it fails check_size_ceiling. This ceiling is NOT shrink-only, which is
why it is itemised in [drift.generated_ceilings] rather than silently skipped.
The alternative considered and not taken -- exclude usr/share/mios/vendored/
(83% of the measurement, and Law 12 forbids shedding it) and re-baseline down
the way generated globals are excluded from the shell/PowerShell counts -- is
recorded in task_988 with the numbers.

<!-- mios-src:dc968d88eb72 from usr/share/mios/mios.toml:11355-11370 -->

### The allowance, and the only operator-tunable half: how far...

The allowance, and the only operator-tunable half: how far the tree may grow
between regenerations before the gate says anything. 0 makes the ceiling exact
and every MiB boundary a failure, which is the state task_988 was filed about.

<!-- mios-src:a3f01f522faa from usr/share/mios/mios.toml:11372-11374 -->

### invariants/test_invariant_{hw,sec}.sh gained explicit...

invariants/test_invariant_{hw,sec}.sh gained
explicit capability-absence SKIP(2) branches (they
previously silently PASSed or FAILed on a missing
device/daemon) and the create/start modes of
.devcontainer/boot-mios-systems.sh were hardened to fail closed. Real
hand-written glue, not a measurement artifact --
deliberately re-baselined up, not folded, because
shrinking it back would mean deleting the SKIP
branches and reintroducing the false-pass/false-fail
bug they fix.

<!-- mios-src:03d410ce0279 from usr/share/mios/mios.toml:11377-11386 -->

### tooling to port, and 36% of this was test code (task_981)....

tooling to port, and 36% of this was test
code (task_981). Pulled to the measurement,
never left as slack. Merge inheritance it
cannot see: task_983. Raised from 75987:
harness/verification_gates.py was rewritten
to execute invariants/test_*.sh (PASS/SKIP/
FAIL + redaction + fail-closed) instead of
only counting them -- real verification
logic the FOSS harness gate now depends on.

<!-- mios-src:8e1ab0f50e87 from usr/share/mios/mios.toml:11397-11405 -->

### Installed on a runner before the unit tier. A hand-written...

Installed on a runner before the unit tier.

A hand-written list was wrong twice in a row: server.py's import chain reaches
smolagents through mios_gateway_queue and mcp through the module after that,
and each missing name cost a CI round trip to discover. The agent-pipe already
declares what it needs, so the runner installs THAT and the list cannot drift
from the code again.

<!-- mios-src:351d4a832370 from usr/share/mios/mios.toml:11437-11443 -->

### The MiOS product line, named once. Each variant used to be...

The MiOS product line, named once. Each variant used to be a different shape:
an edition entry, its own table, or only a machine name. status is measured:
shipping means built and observed, partial means the machinery runs but not
the whole job, design means specified with no artifact.

<!-- mios-src:9f4d0ac73874 from usr/share/mios/mios.toml:11659-11662 -->

### What it takes for a built artifact to count as real. The...

What it takes for a built artifact to count as real. The verifier walked the
tree, matched nothing and reported success, so a build that produced no file
at all satisfied the gate that guards the push. Every format above names the
glob its own target writes; a format whose globs match nothing is a missing
artifact, and no artifact at all is the loudest failure of the set.

<!-- mios-src:12044cee5de9 from usr/share/mios/mios.toml:11837-11841 -->

### [rust] -- what the Rust layer's green actually covers....

[rust] -- what the Rust layer's green actually covers.

`cargo test --workspace` prints "test result: ok. 0 passed" for a crate with no
tests, which reads exactly like a crate whose tests all passed. Every entry
below is in that state. The ceiling is shrink-only: write a test, remove the
entry, lower the number.

<!-- mios-src:3137ebd37e29 from usr/share/mios/mios.toml:11849-11854 -->
### One page per SOURCE DIRECTORY, matching `_distill_dest`'s...

One page per SOURCE DIRECTORY, matching `_distill_dest`'s grouping --
not one page per source FILE. A per-file destination fans out into
hundreds of tiny tracked pages (one per script), which trips the
legibility ratchet's tracked_files ceiling the moment this bulk
migration is committed, for no documentation benefit: an operator
already navigates the manual by directory, not by original filename.

<!-- mios-src:f789ad687233 from usr/libexec/mios/mios-manual:494-499 -->

### Raised from 3065 (prior raise: 3032->3065, same cycle): 56...

Raised from 3065 (prior raise: 3032->3065, same cycle): 56 new tracked
manual pages produced by resolving task_3403/task_3404 -- the pre-existing,
out-of-scope docs-ratchet and no-duplicate-value-key drift-check failures
flagged onto /goal in the prior session. Fixing docs-ratchet REQUIRES this
ceiling to rise: the gate's own remedy is "harvest narrative comments and
overlong AI-hints into docs/, do not raise the docs ceiling" -- every
harvested page is a NEW tracked file, so the two ratchets are in direct
tension by design and tracked_files is the one built to absorb that
growth. 20 top-level usr/share/doc/mios/manual/*.md pages came from
`mios-manual distill` (Day-N+1 narrative-comment migration, non-destructive,
comments untouched in source) plus one manual `mios-manual harvest --path
usr/share/mios/mios.toml` pass (mios.toml is distill's own
[docs.distill].skip_globs exclusion, so its 23 MIGRATE blocks needed an
explicit --to). 36 usr/share/doc/mios/manual/_harvest/*.md pages came from
`mios-manual harvest --class overlong-hint --apply`, migrating over-cap
AI-hint header prose out of source comments; that command's destination
scheme was fixed in the same change (usr/libexec/mios/mios-manual) to group
by source DIRECTORY like `_distill_dest`, rather than emitting one page per
source FILE -- the original per-file scheme would have added 373 tracked
files instead of 36 for the exact same content. Not itemised in
[drift.generated_ceilings] below -- that table is
only for values a tool regenerates from measurement (max_tracked_mb); this
ceiling remains hand-edited and shrink-only going forward from this floor.
GENERATED by tools/native/mios-size-ceiling; do not hand-edit. The valid band
is round(tracked MiB) .. that + tracked_mb_headroom, and a committed value
outside it fails check_size_ceiling. This ceiling is NOT shrink-only, which is
why it is itemised in [drift.generated_ceilings] rather than silently skipped.
The alternative considered and not taken -- exclude usr/share/mios/vendored/
(83% of the measurement, and Law 12 forbids shedding it) and re-baseline down
the way generated globals are excluded from the shell/PowerShell counts -- is
recorded in task_988 with the numbers.

<!-- mios-src:406e7f65ceb5 from usr/share/mios/mios.toml:11355-11385 -->

### R15

R15: mirror the directory index into pgvector. The directory_entry
    writer bypasses _db_create (datetime literal shaping), so it never hit the
    _pg_insert chokepoint -- this is its dedicated pg path. DELETE the root's
    rows then batch-INSERT (ON CONFLICT(path) update). Runs in dual+postgres
    modes. Degrade-open via _pg_run (mios-db --pg-json).

<!-- mios-src:8f958af7e20c from usr/libexec/mios/mios-daemon:2450-2454 -->

### task_972. Each entry pins path:KEY=VALUE, not just path:KEY....

task_972. Each entry pins path:KEY=VALUE, not just path:KEY. A key-only register
cannot tell the shipped placeholder from an operator's real password: setting
MIOS_PGVECTOR_PASS in the build environment bakes that value into a 0644 file under
/usr and the key-only gate stayed green. Every value below is a placeholder
and none is a secret; changing any of them is a NEW finding by design.

<!-- mios-src:7846f980429c from usr/share/mios/mios.toml:1719-1723 -->

### Where every MiOS image keeps that toolchain, so it can...

Where every MiOS image keeps that toolchain, so it can rebuild its own native
catalog: the channel, components and [build.native.linux] target with its
linker, provisioned by `automation/55-native-build.sh --toolchain` in the image
build. Under /usr because /var and /usr/local reset on an ostree image.
etc/profile.d/mios-env.sh exports RUSTUP_HOME and puts the rustup proxies in
cargo_home/bin first on PATH; CARGO_HOME itself stays per user (~/.cargo), so
registry downloads never need a writable /usr.

<!-- mios-src:b8fc25fdddf4 from usr/share/mios/mios.toml:1882-1888 -->

### Shrink-only, and set to the measured count so the next...

Shrink-only, and set to the measured count so the next untagged file
fails the gate. At 130 against an actual 42 it had 88 files of slack
and could not catch a regression until 88 files had degraded.
Header-free prompts, fixtures, vendored files and generated artifacts are
excluded by the native classifier. The remaining taggable source census
leaves one untagged file; the check fails above AND below this number.

<!-- mios-src:0b817c7d9353 from usr/share/mios/mios.toml:2022-2027 -->

### The ONE local heavy dGPU lane

The ONE local heavy dGPU lane: mios-llm-heavy.container on ${MIOS_PORTS_LLM_HEAVY},
running the engine [ai].heavy_engine selects -- vLLM (PagedAttention + APC,
runtime LoRA) or SGLang (HiCache spills inactive KV to CPU RAM for the native
256k context); the tunables are [ai.vllm] / [ai.sglang]. Either engine serves
"mios-heavy" over OpenAI /v1 and honours tool_choice=required, so the node does
not change when the engine does. health_gate=true => AUTO-JOINS only when
reachable; gated off until a model is baked + the service is enabled.

<!-- mios-src:7747a41cc3a9 from usr/share/mios/mios.toml:2858-2864 -->

### [refactor] -- module-size ratchet for the agent-pipe...

----------------------------------------------------------------------------
[refactor] -- module-size ratchet for the agent-pipe extraction (check 149).
`oversize` is a SHRINK-ONLY debt register; numbers may only go DOWN. See ch54.
Operator ruling: the line ceiling governs SUB-MODULES only -- modules inside a
component's package (submodule_roots). A component's main modules (the
agent-pipe root: server.py, the mios_*.py components and their tests) are
where features fold in, and carry no ceiling.

<!-- mios-src:1abcf6a339cd from usr/share/mios/mios.toml:3040-3046 -->

### Heavy first-boot pulls

Heavy first-boot pulls: the GGUF and vLLM weights and the firstboot-tier
inference images (mios-ai-firstboot), [ai].firstboot_models
(mios-models-firstboot) and [ai].firstboot_bound_images
(mios-bound-images-firstboot). Each reads the LAYERED SSOT, so a cloud or
container deployment turns them off with the [deployment.cloud] host drop-in;
the image is unchanged. false skips without writing a done-sentinel, so
removing the drop-in resumes them at the next run.

<!-- mios-src:c5d801583ea9 from usr/share/mios/mios.toml:6074-6080 -->

### THE engine of the ONE heavy dGPU lane, mios-llm-heavy...

THE engine of the ONE heavy dGPU lane, mios-llm-heavy: "vllm" | "sglang".
mios-gen renders [containers.mios-llm-heavy.engine.<this>] (and the .image
twin) into the lane's Quadlet; the engine tunables are [ai.vllm] / [ai.sglang]
and the per-engine surface settings [lanes.<this>]. Runtime LoRA
([converge.inference].vllm_*) needs "vllm". Env MIOS_AGENT_PIPE_HEAVY_ENGINE
may still set the agent-pipe route to "light" (skip the heavy lane).

<!-- mios-src:1c22fa9e8b24 from usr/share/mios/mios.toml:6140-6145 -->

### Cloud and container deployments of the ONE image: the...

Cloud and container deployments of the ONE image: the devcontainer, Codespaces,
the Claude Code and Codex cloud environments, Cloud Shell and a GCE VM all run
[image].ref. They differ from a booted install by configuration only: every
cloud entry point (.devcontainer/cloud-shell/bootstrap.sh, the devcontainer
lifecycle) writes [deployment.cloud.overlay] as the host drop-in `dropin`, which
the layered resolver merges over the vendor SSOT, turning off the heavy runtime
model pulls, runtime desktop installs and the desktop session.

<!-- mios-src:b3e7328f469d from usr/share/mios/mios.toml:6413-6419 -->

### The ONE development userspace, part of every full MiOS...

The ONE development userspace, part of every full MiOS image: the repos phase
(automation/05-repos.sh) installs it with its declared dependency closure, so
the devcontainer, Codespaces and the cloud environments, which all run
[image].ref (.devcontainer/Containerfile adds only container wiring), carry it
because MiOS does. [ci.fedora].package_sets also provisions the lean CI harness
from it. The core profile selects self-build without these editor extras.

<!-- mios-src:0f41d1c9289a from usr/share/mios/mios.toml:6615-6620 -->

### Ports with no browser-openable URL

Ports with no browser-openable URL: either they serve no page, or their
address is already a key their consumers resolve -- agent_pipe is
MIOS_AI_ENDPOINT, pgvector MIOS_DB_URL, llm_light MIOS_LLM_CPU_ENDPOINT,
hermes MIOS_HERMES_ENDPOINT, crawl4ai MIOS_CRAWL_SERVICE_URL, otelcol_otlp
[observability].otel_endpoint, llm_igpu [nodes.local-igpu].endpoint. headscale
is the mesh control API and rpc_igpu a llama.cpp RPC socket: neither is a page.

<!-- mios-src:b490d1c2283c from usr/share/mios/mios.toml:7608-7613 -->

### Units tools/generate-pod-quadlets.py writes under users/...

Units tools/generate-pod-quadlets.py writes under users/ (podman's user Quadlet
search path) instead of the system directory: they run rootless under each
login user's systemd. Everything not listed is a system unit.

<!-- mios-src:775f64fe6b02 from usr/share/mios/mios.toml:8867-8869 -->

### A USER-scope unit (see [quadlets.scope]): it streams the...

A USER-scope unit (see [quadlets.scope]): it streams the logged-in user's own
desktop, so it runs rootless under that user's systemd, not as root (Law 6).
Capture goes through xdg-desktop-portal ScreenCast + PipeWire (capture=portal);
KMS capture would need host CAP_SYS_ADMIN, which a rootless unit cannot hold.

<!-- mios-src:b206f6f17fba from usr/share/mios/mios.toml:10732-10735 -->

### medicat_sha1 / medicat_md5

medicat_sha1 / medicat_md5 : upstream-published hashes of MediCat.USB.v<medicatver>.7z,
                 checked by installation/MiOS-Field.bat after download (SHA1 preferred,
                 MD5 if SHA1 is empty; both empty -> check skipped). Update them with
                 medicatver; keep them equal to mios-bootstrap's mios.toml [field]. Source
                 for v21.12: the MediCat download page, https://medicatusb.com/#downloads
                 (repo github.com/mon5termatt/medicat-website,
                 src/components/home/downloads.astro @ cd6f924); the MD5 matches upstream
                 github.com/mon5termatt/medicat_installer src/downloads.h + download/cdn.bat
                 @ d588fd3, whose archive size (22994783619) matches MiOS-Field.bat's size gate.

<!-- mios-src:b5384c3a4f7b from usr/share/mios/mios.toml:11115-11123 -->

### hostRequirements of every devcontainer.json above. The...

hostRequirements of every devcontainer.json above. The devcontainer runs
[image].ref itself: ~23 GB compressed and ~49 GB unpacked, and Codespaces'
containerd keeps both, so the pull alone needs ~72 GB before the workspace or a
nested build of the image. Codespaces offers 128 GB of storage only on its
16-core / 64 GB machine, which is also what a rebuild of MiOS inside it needs.
remoteUser is projected from [identity].username: the image's own user.

<!-- mios-src:b4c9aaf7ba49 from usr/share/mios/mios.toml:11232-11237 -->

### task_975. Phase scripts present in automation/ that build.sh...

task_975. Phase scripts present in automation/ that build.sh does NOT run.
Shrink-only, and the ceiling may never sit above the measurement.

The register is empty as of task_962 stage 5. It previously held
55-native-build.sh on the reasoning that registering it would put miosd on
PATH -- its `ln -sf "${DEST_DIR}/${bin}" "/usr/bin/${bin}"` is the only such
line in the tree -- and so arm the `command -v miosd` gates in every stage
numbered above 55, producing a split brain inside one bake.

That reasoning read the script instead of measuring whether the line runs.
This was a historical missing-package condition. [packages.build-toolchain]
now ships Rust/Cargo and the repos phase installs selected build dependencies.
Phase 55 must distinguish complete source checkouts from image bake contexts;
prebuilt native binaries copied by the Containerfile remain the bake producer.
The binaries in the image come from the Containerfile's rust-builder stage,
COPYed to /usr/libexec/mios, which is not on PATH -- so no stage, above or
below 55, has ever reached its Rust branch. Registering 55 arms nothing.
It also cannot change which phases run: build.sh selected via the
automation/[0-9][0-9]-*.sh glob (build_catalog_authoritative = false and no
build_phases.json), and that glob already included 55.

<!-- mios-src:bba7b1b1a922 from usr/share/mios/mios.toml:12139-12158 -->

### Raised to 3409 following approved merges on main...

Raised to 3409 following approved merges on main: task_1018..task_1025, manual corpus,
devcontainer, artifacts, and core OS daemons.
GENERATED by tools/native/mios-size-ceiling; do not hand-edit. The valid band
is round(tracked MiB) .. that + tracked_mb_headroom, and a committed value
outside it fails check_size_ceiling. This ceiling is NOT shrink-only, which is
why it is itemised in [drift.generated_ceilings] rather than silently skipped.
The alternative considered and not taken -- exclude usr/share/mios/vendored/
(83% of the measurement, and Law 12 forbids shedding it) and re-baseline down
the way generated globals are excluded from the shell/PowerShell counts -- is
recorded in task_988 with the numbers.

<!-- mios-src:56b862a6f5c5 from usr/share/mios/mios.toml:12772-12781 -->

### CI-only extras the harness needs beyond that set: sandbox...

CI-only extras the harness needs beyond that set: sandbox tests (bubblewrap),
composefs sealing (mkcomposefs, composefs-info), unit verification
(systemd-analyze), the lint tier's pwsh and the analyzer's .NET runtime, and
test-ukify-stage's real-compiler tier (ukify + the systemd-boot EFI stub; the
image's own kernel is the input). A suite that cannot run its live tier exits
77, which fails unless [ci.tool_skips] registers it: provide the tool here.

<!-- mios-src:6d76e53cc4e6 from usr/share/mios/mios.toml:12864-12869 -->

### Image profiles (ADR-0025): every MiOS image kind is the one...

Image profiles (ADR-0025): every MiOS image kind is the one pipeline run
under a profile. A profile selects phases from [build.phases].list and
sections from [packages]; `extends` unions a parent's selection and `all`
selects everything. `floor` is the profile every other one must contain.
Read by `miosd build --list --profile`; gate: check_profile_integrity.

<!-- mios-src:9ef4afec86f6 from usr/share/mios/mios.toml:13257-13261 -->

### [rust.categories] -- ADR-0021 Function-Named Binary & Crate...

----------------------------------------------------------------------------
[rust.categories] -- ADR-0021 Function-Named Binary & Crate SSOT Registry
----------------------------------------------------------------------------
Maps script domains and existing crates to destination static binaries,
lifecycle roles, install directories, and responsible owners (task_3488).
Ownership gate: mios-gate rust-categories.
Script layer: scope= globs claim the scripts a category owns for porting;
exempt categories (binary = "exempt") carry their reason in description=;
universe= fixes the script roots the gate must account for, and max_unowned
is the shrink-only ceiling for scripts no category claims yet (task_3488).
A replaces= entry is ported-and-deleted evidence: the named script must be
absent from the tree in the same commit that proves parity (ADR-0021).

<!-- mios-src:a43e15e87658 from usr/share/mios/mios.toml:13399-13410 -->
### Agent-pipe port from SSOT (env MIOS_A2A_PORT /...

Agent-pipe port from SSOT (env MIOS_A2A_PORT / MIOS_PORTS_AGENT_PIPE override,
    else [ports].agent_pipe). No literal default -- empty means we cannot honestly
    build the loopback self-URL, so the self-peer is simply skipped (degrade-open).

<!-- mios-src:62bbb33a781f from usr/libexec/mios/mios-a2a-discover:45-47 -->

### MiOS Native AI Metadata Engine. Parses AI comment headers...

MiOS Native AI Metadata Engine.

Parses AI comment headers across all source files, modules, units, and documentation
to produce first-class, machine-readable metadata. Guarantees that AI-hint, AI-related,
AI-functions, and AI-doc headers are recognized as native OS metadata for local agent
discovery, tool routing, and automated pipeline validation.

<!-- mios-src:a4d385807750 from usr/libexec/mios/mios-ai-metadata.py:6-13 -->

### Files that stay header-free are not taggable, rather than...

Files that stay header-free are not taggable, rather than counted against
[ai_tag].max_untagged: a header would leak into an agent-harness prompt, make a
vendored or Law 15 mirrored copy differ from its source, be overwritten by the
generator that owns the file, or corrupt a format with no comment syntax.
Multi-component and (^|/)-anchored, so a code dir that only shares a name
(usr/lib/mios/agents) stays taggable, on root-relative and absolute paths alike.

<!-- mios-src:5816a5b08704 from usr/libexec/mios/mios-ai-tag:46-51 -->

### mios-chain-verify -- walk the MiOS event hash chain and...

mios-chain-verify -- walk the MiOS event hash chain and report the first tamper.

The `event` table is an append-only observability stream; SEC-03 links every row to
its predecessor with a SHA-256 chain (chain_seq / prev_hash / chain_hash) at the
agent-pipe persist chokepoint. This tool independently RE-DERIVES the chain from the
stored rows and reports whether it is intact:

  * reads the chained rows (WHERE chain_hash IS NOT NULL) in chain_seq order via
    `mios-pg-query --exec-json` -- the pure-stdlib pg wire transport every confined
    agent-plane reader uses (no psql / psycopg / podman required);
  * recomputes each link with the SAME canonical_core + sha256 used on write,
    imported from the agent-pipe `mios_audit` module so the algorithm has ONE source
    of truth (no second copy of the crypto to drift);
  * prints `{ok, checked, first_broken_seq}` and EXITS NONZERO on a broken chain.

Exit codes:  0 = chain intact (or empty)   1 = tamper detected (first_broken_seq)
             2 = read / setup failure (cannot reach pg or import the verifier)

Env:  MIOS_PG_QUERY        (default /usr/libexec/mios/mios-pg-query)
      MIOS_AGENT_PIPE_DIR  (default: resolved relative to this CLI)
      plus mios-pg-query's own MIOS_PGVECTOR_HOST / MIOS_PG_PORT / MIOS_PGVECTOR_USER / MIOS_PGVECTOR_DB

<!-- mios-src:63ac53820650 from usr/libexec/mios/mios-chain-verify:5-26 -->

### Known Composefs Magic Numbers

Known Composefs Magic Numbers:
0xd078629a: Standard libcomposefs CFS_MAGIC (little-endian: 9a 62 78 d0)
0x00736663: 'cfs\0' (little-endian: 63 66 73 00)
0x00534643: 'CFS\0' (little-endian: 43 46 53 00)
0x66706d63: 'cmpf'
0x636d7066: 'fpmc'

<!-- mios-src:4af6324cf0e3 from usr/libexec/mios/mios-composefs-validator:23-28 -->

### Linux kernel fsverity_descriptor struct (256 bytes total)...

Linux kernel fsverity_descriptor struct (256 bytes total):
u8 version (1)
u8 hash_algorithm (1 = SHA-256)
u8 log_blocksize (12 = 4096)
u8 salt_size (0)
u32 sig_size (0)
u64 data_size (len(data))
u8 root_hash[64]
u8 salt[32]
u8 reserved[144]

<!-- mios-src:2a97130cfe25 from usr/libexec/mios/mios-composefs-validator:77-86 -->

### MiOS Core Scheduling and SMT Sibling Isolation Utility...

MiOS Core Scheduling and SMT Sibling Isolation Utility (task_802).

Enforces hardware execution isolation across Simultaneous Multi-Threading (SMT)
sibling cores for untrusted subagents and sandboxed processes using the Linux
PR_SCHED_CORE prctl interface (Linux 5.14+).

Prevents cross-SMT speculative execution side-channels (Spectre-v2, MDS, L1TF)
while preserving 100% of Hyper-Threading capacity for trusted system processes.

<!-- mios-src:23a1297b511f from usr/libexec/mios/mios-core-sched:6-14 -->

### mios-crawl -- native CRAWL verb backend (fetch a URL ->...

mios-crawl -- native CRAWL verb backend (fetch a URL -> clean markdown).

Reads ONE web page and returns LLM-ready markdown so agents GROUND on the
ACTUAL page content instead of a search snippet or a fabrication. Thin HTTP
client for the local mios-crawl4ai service (loopback FastAPI) -- the slow
crawl4ai/camoufox import + browser-attach is kept WARM in that service, so
each `crawl` verb call is just a fast loopback POST (same shape as
mios-web-search -> SearXNG).

  --- engine flow (operator directive 2026-05-24) ---
  PRIMARY  : crawl4ai drives the EXISTING local Chrome over the DevTools
             Protocol (ws://127.0.0.1:9222, the ChromeDev flatpak that
             mios-hermes-browser.service keeps up). crawl4ai ATTACHES to
             that browser via BrowserConfig(browser_mode="custom",
             cdp_url=...) -- NO bundled/downloaded Chromium.
  FAIL-RETRY: if the CDP crawl errors / is blocked / returns near-empty
             markdown, the SAME url is retried with Camoufox (stealth
             anti-detect Firefox). Camoufox ships its own patched Firefox.
  HONEST-FAIL: if both engines fail, this prints success:false with an
             error -- it NEVER invents page content.

Companion to mios-web-search: web_search (SearXNG) finds candidate URLs;
crawl reads the chosen one. Agent flow: search -> pick URL -> crawl ->
answer from the fetched markdown.

SSOT (env rendered from mios.toml [crawl] via globals/userenv):
  MIOS_CRAWL_SERVICE_URL  base URL of the local crawl service
                          (default http://127.0.0.1:${MIOS_PORTS_CRAWL4AI:-8810})

Usage:
  mios-crawl <http(s)-url> [--max-chars N] [--camoufox] [--timeout S] [--json]
    --camoufox   force the Camoufox stealth path (skip Chrome CDP). Used by
                 the smoke test to confirm the fail-retry engine works.
    --max-chars  truncate returned markdown (0 = no limit; default 20000)
Output: JSON {success, engine, url, title, markdown, links}

<!-- mios-src:1f9c643d5a38 from usr/libexec/mios/mios-crawl:4-39 -->

### Flatpak cannot see /usr/share/icons from the host....

Flatpak cannot see /usr/share/icons from the host. Materialize the baked
theme in the user icon directory instead of downloading it or linking to
a host-only path. The global override exposes xdg-data/icons read-only.

<!-- mios-src:2e92094b476c from usr/libexec/mios/mios-cursor-ensure:75-77 -->

### Automated network and audio fallback manager with operator...

Automated network and audio fallback manager with operator desktop alert daemon.
Inspects degraded peripheral statuses from /var/log/mios-hardware-degrade.log or live sysfs/proc probes.
Triggers automated network failover (secondary NIC, USB tethering) and dynamic PipeWire null-sink binding
when physical soundcards or primary network interfaces are missing or degraded. Dispatches operator desktop
notifications with driver and firmware remediation guidance.

<!-- mios-src:28f30cd2ae4a from usr/libexec/mios/mios-hardware-fallback:4-10 -->

### 'MiOS' Headscale mesh VPN coordinator first-boot config...

'MiOS' Headscale mesh VPN coordinator first-boot config generator.

Writes /etc/headscale/config.yaml from the [headscale] + [ports] SSOT in
mios.toml (layered: vendor /usr/share/mios/mios.toml < host /etc/mios/mios.toml).

Idempotent + non-destructive: if the config file already exists it does NOTHING
(Headscale state/sqlite remains preserved across runs). Pass --force to regenerate.

<!-- mios-src:50b526d71604 from usr/libexec/mios/mios-headscale-firstboot:4-11 -->

### Validates that target strictly resolves to an IP within the...

Validates that target strictly resolves to an IP within the dedicated
    out-of-band management subnet (10.200.0.0/16) or test loopback (127.0.0.1/8).
    Raises SecurityError on violation.
    Returns (host, port).

<!-- mios-src:ea4bf09307f1 from usr/libexec/mios/mios-ipkvm-manager:92-97 -->

### mios-kg -- Personal Knowledge Graph CLI for the MiOS agent...

mios-kg -- Personal Knowledge Graph CLI for the MiOS agent stack.

Phase C.1 of the AgentOS roadmap. Manages the operator-preference data in
PostgreSQL/pgvector (person / app_install / alias + resolves_to natural-key
edge). The agent-pipe's kg_lookup() helper queries the same tables (JOIN on
app_id) to resolve ambiguous noun phrases to concrete launch targets. Mappings
are PER-OPERATOR data -- this CLI is the interface, never the source. NO
hardcoded English aliases in this codebase. (The legacy BSL 1.1 store is retired.)

Subcommands:
  bootstrap                Create the person row (from $USER /
                           $MIOS_IDENTITY_USERNAME) + ingest mios-apps inventory
                           into app_install rows. Idempotent.
  alias add <phrase> <target>
                           Add an alias. <target> can be:
                             app:<short_name>   (resolves to app_install)
                             "<short_name>"     (bare = app:<short_name>)
  alias rm <phrase>        Remove an alias.
  alias list               List all aliases + their resolves_to.
  lookup <phrase>          Print the lookup result for <phrase>.
  apps                     Print known app_install rows.
  who                      Print the person row.

Fully local: routes all reads + writes through the shared
/usr/libexec/mios/mios-db --pg-json CLI, which binds every value OUT-OF-BAND via
mios-pg-query's extended protocol (no value is spliced into SQL -> injection-
safe). resolves_to / app_install / alias / person tables are defined in
/usr/share/mios/postgres/schema-init.sql.

<!-- mios-src:ad9febc92240 from usr/libexec/mios/mios-kg:4-32 -->

### Verifies that the kernel module carries a valid...

Verifies that the kernel module carries a valid cryptographic signature.
    Per Architectural Invariant 2, MOK signing governs runtime out-of-tree and
    livepatch module signatures, distinct from UKI boot signing.

<!-- mios-src:7ad8e02a589f from usr/libexec/mios/mios-livepatch:31-35 -->

### mios-manual -- corpus ledger and census for the MiOS...

mios-manual -- corpus ledger and census for the MiOS documentation system.

Spec: docs/design/doc-generative-documentation.md sections 3.4 and 5.

Subcommands:
  ledger          rebuild usr/share/mios/reference/manual-corpus.tsv from the tree
  audit           census as JSON (counts by class and reason)
  coverage        the two ratchet numbers, for the gate and for humans
  harvest         move a comment's prose into a doc and record where it landed
  prune           delete a comment that provably landed (the only destructive path)
  landing         verify every pruned comment still lands in a doc
  check-functions verify harvested AI-functions lines against source
  render          splice derived content into MIOS-GEN marker interiors

`--root` selects the repo, and either layout resolves, so the same tools serve
mios.git and mios-bootstrap.git without being copied across (Law 15).

The ledger is the safety mechanism for the whole programme. Deletion of a
comment is only ever permitted once a ledger row proves its knowledge reached a
doc, so the landing columns must survive ordinary code churn: they are carried
forward BY CONTENT HASH, never recomputed. Moving a block within a file, or
reformatting the code around it, keeps its landing record.

Rows for blocks that no longer exist but were pruned are kept as tombstones --
that retention is what lets a gate prove, after the fact, that a comment which
is gone did land somewhere first.

<!-- mios-src:a03aa62539e4 from usr/libexec/mios/mios-manual:5-31 -->

### Verify that every function named in a harvested...

Verify that every function named in a harvested AI-functions line exists in its source.

    Acceptance criteria (task_3482):
    WHEN a harvested AI-functions line names a function absent from its mios-src
    source THE SYSTEM SHALL fail a check naming the doc line and the function.

<!-- mios-src:4a042f129320 from usr/libexec/mios/mios-manual:921-926 -->

### mios-mcp-server -- Model Context Protocol stdio and...

mios-mcp-server -- Model Context Protocol stdio and Streamable HTTP relay.

Exposes MiOS's [verbs.*] catalog (SSOT in mios.toml) as MCP tools so
LOCAL MCP-aware agents can drive MiOS verbs natively.

LOCAL-ONLY by design. Any on-host MCP client using the MiOS local endpoint
may consume the catalog; the server has no dependency on a hosted model.

Protocol framing, version negotiation and HTTP security are supplied by the
upstream FOSS Python MCP SDK v2. It serves 2026-07-28 and legacy peers on the
same endpoint. [mcp].protocol_version is the preferred client revision.
  https://modelcontextprotocol.io/specification

Implements through the SDK:
  server/discover     -- modern capability and version discovery
  initialize         -- legacy handshake for older clients
  tools/list          -- renders [verbs.*] as MCP tool specs (calls
                         agent-pipe /v1/verbs)
  tools/call          -- dispatches via agent-pipe /v1/dispatch which
                         routes through the launcher broker
  resources/list      -- the COMPLETE read-only surface (every verb/script
                         + recipe + skill, promoted AND not) via agent-pipe
                         /v1/resources -- progressive-disclosure discovery
  resources/read      -- fetch one mios:// resource via /v1/resources/read
  ping               -- liveness

The dynamic catalog is private and uncached; clients can re-list after a change.
Prompts remain absent until the MiOS profile declares them.

<!-- mios-src:a2baf8f4aa44 from usr/libexec/mios/mios-mcp-server:4-32 -->

### Keep the last-good catalog warm independent of client...

Keep the last-good catalog warm independent of client connect timing.
    Re-tries faster while the upstream looks unhealthy so the cache re-warms
    promptly once agent-pipe recovers. When the served catalog actually CHANGES
    (e.g. recovers from the SSOT floor / a degraded set back to the full surface),
    log a catalog change. The SDK serves uncached list results, so clients
    re-list against the current catalog instead of keeping a stale session.

<!-- mios-src:ba2829141466 from usr/libexec/mios/mios-mcp-server:353-358 -->

### Per MCP session upstream client, private socket and bounded...

Per MCP session upstream client, private socket and bounded slot policy.

    The SDK owns framing and version negotiation. Its context is entered and
    exited in one owner task so concurrent tool handlers cannot corrupt an
    anyio cancel scope. Terminal access has the caller's OS permissions; a
    tool/path allowlist is not a filesystem sandbox for arbitrary shell code.

<!-- mios-src:bf7309f7c58b from usr/libexec/mios/mios-mcp-server:620-626 -->

### Only our sleeping foreground bash may accept a new...

Only our sleeping foreground bash may accept a new workflow.

        A CLI waiting for input is still occupied. Bash/readline may poll
        instead of n_tty_read; verify its kernel state and terminal ownership.
        Missing or changing process evidence fails closed.

<!-- mios-src:ca64cf4b945f from usr/libexec/mios/mios-mcp-server:1065-1070 -->

### Explicit terminal capabilities for stateless HTTP; stdio...

Explicit terminal capabilities for stateless HTTP; stdio stays implicit.

    Modern MCP HTTP has an application lifespan rather than a client lifespan.
    Never use that application bridge as an HTTP client's terminal. Tokens are
    opaque capabilities, expire after idle time, and are bounded by SSOT.

<!-- mios-src:6ed6f1d6130d from usr/libexec/mios/mios-mcp-server:1231-1236 -->

### mios-owui-bootstrap-admin -- create the first OWUI admin...

mios-owui-bootstrap-admin -- create the first OWUI admin user when
the `user` table is empty, so a fresh install / reinstall doesn't
lock the operator out of Open WebUI.

Resolves identity from layered mios.toml ([identity].username,
[identity].email) and the password from the MiOS password SSOT (see
_read_password): MIOS_OPERATOR_PASSWORD / MIOS_OWUI_ADMIN_PASSWORD ->
mios.toml [identity].default_password -> MIOS_IDENTITY_DEFAULT_PASSWORD -> "mios"
-- the SAME source Forge / Portal / Cockpit / RDP use, so one operator
password works everywhere. A "__random__" override generates + writes a
24-char password to /etc/mios/owui-admin-password (mode 0600 root-only).

Idempotent: skips if any user row exists. Logs to stderr so
mios-hermes-firstboot can capture the output via _log.

Why direct sqlite + bcrypt instead of POST /api/v1/auths/signup:
  * doesn't depend on OWUI being up + responsive at firstboot time
  * doesn't need ENABLE_SIGNUP=True dance (toggle env, restart, post,
    toggle back, restart) which is fragile and slow
  * uses the same bcrypt scheme OWUI does (passlib bcrypt $2b$)

Returns exit 0 on success, no-op skip, or any expected non-fatal path.
Returns exit 2 only on hard infrastructure failures (db missing, no
admin name resolvable). Designed to never block firstboot.

<!-- mios-src:6c99c80a9900 from usr/libexec/mios/mios-owui-bootstrap-admin:4-28 -->

### The MiOS password SSOT, highest precedence first. ONE...

The MiOS password SSOT, highest precedence first. ONE operator password
backs every MiOS admin surface (Forge / Portal / Cockpit / RDP / OWUI):
  1. MIOS_OPERATOR_PASSWORD     -- explicit override (secrets.env / env)
  2. MIOS_OWUI_ADMIN_PASSWORD   -- per-service override (parallels
                                   MIOS_FORGE_ADMIN_PASSWORD)
  3. mios.toml [identity].default_password  -- the canonical SSOT field
  4. MIOS_IDENTITY_DEFAULT_PASSWORD (install.env)  -- shell/systemd bridge of #3
  5. literal "mios"             -- vendor default; the same final fallback
                                   as usr/libexec/mios/forge-firstboot.sh
Labels are what gets logged. Values are read only by _read_password(), so
no log line can carry the credential.

<!-- mios-src:c9fcb2de2628 from usr/libexec/mios/mios-owui-bootstrap-admin:99-109 -->

### Return (tier index, generated) for the first configured...

Return (tier index, generated) for the first configured tier.

    A value of "__random__" at any tier opts INTO a generated 24-char password
    (written to PASSWORD_OUT, mode 0600), mirroring forge's __random__ token.
    We deliberately do NOT silently generate a random password: an operator
    who never sees it can't log in, and it diverges from the rest of the OS.

<!-- mios-src:e25b56a94372 from usr/libexec/mios/mios-owui-bootstrap-admin:124-130 -->

### Record who the admin is and where its password comes from....

Record who the admin is and where its password comes from.

    The password itself is written only when MiOS generated it (__random__):
    that is the one case the operator cannot know it otherwise. A password
    the operator set in the SSOT is never copied into a second file.

<!-- mios-src:a0e63b37fa76 from usr/libexec/mios/mios-owui-bootstrap-admin:154-159 -->

### mios-pg-query -- minimal pure-stdlib PostgreSQL client for...

mios-pg-query -- minimal pure-stdlib PostgreSQL client for the MiOS agent
plane. No psql binary, no psycopg, no podman needed: it speaks the v3 wire
protocol directly over a loopback TCP socket.

Why this exists (R15): confined service users such as `mios-ai` (which runs
mios-daemon's directory indexer + the dispatched directory_lookup verb) have
NEITHER a psql binary NOR podman access to exec into the pgvector container --
so `mios-db --pg`'s two existing paths both fail for them, and the agent plane
could not reach pgvector. This is the universal fallback. It relies on the
pgvector pg_hba `host all all 127.0.0.1/32 trust` line (loopback trust), so
there is no auth exchange -- the StartupMessage is answered with
AuthenticationOk directly.

Two modes:
  1. SIMPLE (default, unchanged):  mios-pg-query [-At ...] '<sql>'   (or SQL on
     stdin). Sends the SQL verbatim as one simple-Query ('Q') message. Multiple
     / multi-statement queries are handled. The SQL is run AS-IS -- the caller
     owns its safety (it is a trusted raw-SQL transport / console).
  2. EXTENDED / parameterized (WS-A3):  mios-pg-query --exec-json   reads a JSON
     envelope on stdin and binds the values OUT-OF-BAND via the v3 extended
     query protocol (Parse/Bind/Execute/Sync), so NO value is ever spliced into
     the SQL text (kills SQL-injection). Two envelope shapes:
        {"sql": "... $1 ... $2 ...", "params": [v1, v2, ...]}
        {"statements": [{"sql": "...", "params": [...]}, ...]}  # one txn (atomic)
     Placeholders are $1..$n; params are bound as TEXT format with unspecified
     type (the backend infers from context / an explicit ::cast in the SQL).
     NULL is JSON null; keep integers (e.g. LIMIT) inline in the SQL string
     (int-coerced by the caller) to avoid text->int inference edge cases.

Env:    MIOS_PGVECTOR_HOST (127.0.0.1) MIOS_PG_PORT (5432)
        MIOS_PGVECTOR_USER (mios)      MIOS_PGVECTOR_DB (mios)
        task_70 RLS: MIOS_PGVECTOR_RLS_ENABLE (off) gates a per-connection owner scope;
        the owner comes from `--owner <v>` (or `--owner=<v>`) else MIOS_PG_RLS_OWNER.
        When enabled+owner, an owner-bound `set_config('mios.owner_user', ...)` is
        SET first so the schema RLS policies scope rows to that owner. Default-off /
        no owner -> nothing is emitted (byte-identical; no consumer is locked out).
Output: tab-separated columns, one row per line (psql -At shape) for BOTH modes.
        Multi-row handled; NULLs render as empty. Exit 1 on any backend
        ErrorResponse or unsupported (non-trust) auth.

<!-- mios-src:f1952ac835aa from usr/libexec/mios/mios-pg-query:5-44 -->

### The owner to scope THIS invocation's rows to, or None to...

The owner to scope THIS invocation's rows to, or None to emit NO scope.
    Gated by MIOS_PGVECTOR_RLS_ENABLE (SSOT [pgvector].rls_enable, bridged by userenv.sh),
    the SAME flag the agent-pipe pg path reads -- default-off => None => no change.
    The owner comes from `--owner <v>` (or `--owner=<v>`) else MIOS_PG_RLS_OWNER, so
    the confined consumers (mios-ai / daemon / skills / kg) can scope their reads.
    None whenever RLS is off OR no owner is supplied -> the GUC stays unset -> the
    schema policy is permissive (degrade-open: a confined tool / the daemon is NEVER
    locked out).

<!-- mios-src:136d7c707d7b from usr/libexec/mios/mios-pg-query:254-261 -->

### MiOS Shadow Dual-Process Candidate Daemon Validator and...

MiOS Shadow Dual-Process Candidate Daemon Validator and Query Mirroring Harness.

Validates candidate daemon builds and patches in a shadow container or isolated
network namespace before promotion to production. Intercepts and mirrors
non-mutating OpenAI-compatible /v1 queries, strictly filters mutating requests,
measures latency differentials, evaluates schema compliance, and gates canary
promotions.

<!-- mios-src:f3c08d190590 from usr/libexec/mios/mios-shadow-test:4-12 -->

### Evaluates whether an HTTP query represents a state-mutating...

Evaluates whether an HTTP query represents a state-mutating operation.
    Enforces the Mutation Guard invariant: under no circumstances may state-altering
    operations be mirrored to candidate instances.

<!-- mios-src:865c7b07d731 from usr/libexec/mios/mios-shadow-test:112-116 -->

### Zero-downtime systemd socket handoff daemon swapper for...

Zero-downtime systemd socket handoff daemon swapper for agent-pipe.

Implements zero-downtime hot swapping of daemon instances (agent-pipe, hermes gateway)
via systemd socket activation (sd_listen_fds / FD 3+), SCM_RIGHTS file descriptor
passing, dual-accept concurrency, graceful SIGUSR1 draining, and automated rollback
upon candidate failure.

<!-- mios-src:ed375451ac88 from usr/libexec/mios/mios-socket-swap:4-10 -->

### Built-in daemon worker used for testing zero-downtime...

Built-in daemon worker used for testing zero-downtime socket handoff.

    Accepts sd_listen_fds, serves HTTP on TCP and Unix domain socket,
    supports SCM_RIGHTS FD handoff over a control socket, and gracefully
    drains connections on SIGUSR1.

<!-- mios-src:5d6059899a87 from usr/libexec/mios/mios-socket-swap:147-152 -->

### MiOS Proactive PID Thermal Daemon & Dynamic Power Cap...

MiOS Proactive PID Thermal Daemon & Dynamic Power Cap Modulator.

Monitors CPU/SoC temperatures via /sys/class/hwmon, GPU temperatures and power draw
via /sys/class/drm or nvidia-smi / NVML, and CPU frequency / Energy Performance
Preference (EPP) via /sys/devices/system/cpu/cpu*/cpufreq/.

Proactively modulates power caps and EPP before silicon thermal throttling thresholds
(e.g., at 80°C instead of hardware 95°C throttle) based on PID temperature error and
rate of temperature rise (dT/dt). Provides hysteresis recovery restoring full performance
profile after temperature remains below 70°C for at least 10 seconds.

<!-- mios-src:7ba655c35584 from usr/libexec/mios/mios-thermald:5-16 -->

### mios-verify-launch -- ask the always-on mios-daemon-agent...

mios-verify-launch -- ask the always-on mios-daemon-agent (the iGPU
daemon-tier brain) whether an app ACTUALLY launched, IN-TURN.

Operator 2026-05-25 (4A): "the iGPU's always-on daemon-agent ... runs the
Definition-of-Done success check; the main agent/sub-agents just delegate and
read the success signal." The daemon already runs a post-hoc launch_verifier
loop (it scans recent chats and records false launch-success claims to
/var/lib/mios/daemon/launch_failures.json). This verb closes the loop
SYNCHRONOUSLY: after an agent fires an OS-control verb (open_app / window /
pc_*), it calls `verify_launch {app}` and gets the daemon's consolidated
verdict -- a live read-only window/process probe (mios-window-active --present,
which never launches anything) PLUS the daemon's own recorded false-success
history for that app. The agent then retries or reports honestly instead of
blind-claiming success.

GLOBAL tool (operator: ALL agents/sub-agents/nodes use ALL tools globally),
reached through the broker like any other verb. Offline-first: if the daemon is
down it returns success=false with an honest error -- it NEVER fabricates a
launched=true.

SSOT: MIOS_DAEMON_AGENT_URL (else built from MIOS_PORTS_DAEMON_AGENT, default
8644 -- matches the daemon's AGENT_PORT / mios.toml [agents.mios-daemon-agent]).

Output JSON: {success, app, launched, verdict, recent_failures, checked_by}.

<!-- mios-src:3e2732a815d1 from usr/libexec/mios/mios-verify-launch:4-28 -->

### mios-web-search -- native WEB-search verb backend...

mios-web-search -- native WEB-search verb backend (concurrent fan-out).

Queries the LOCAL, self-hosted SearXNG metasearch (offline-first; NO cloud
AI dependency -- Architectural Law 5) and returns clean, citable results so
agents GROUND current-world answers (weather, news, events, prices, facts,
general knowledge) on REAL fetched data instead of fabricating from model
memory.

Operator 2026-05-21: the chat agent invented a weather report -- wrong city
(Port Hope vs. the asked-about Toronto), wrong units (Fahrenheit for a
Canadian user), and made-up event details -- because NO web_search verb
existed; only filesystem search (everything_search / fs_search) did, whose
own descriptions tell the model to "use a WEB search" that was never wired.
SearXNG was already up (:8800) and advertised as the web_search backend.

Operator 2026-05-22: "have web tools shoot off more web queries concurrently".
QUERY FAN-OUT (--fanout K, the industry pattern behind Google AI Mode /
RAG-Fusion / ParallelSearch): expand one query into K diverse sub-queries via
the always-warm micro-LLM, fire them at SearXNG CONCURRENTLY, then merge with
Reciprocal Rank Fusion + URL dedupe. K parallel queries overlap latency (8
parallel ~= 300-500ms vs ~200ms single) and surface evidence one phrasing
misses. SearXNG's own limiter is off + granian workers are bumped so the
local instance absorbs the burst; agent-pipe bounds CROSS-agent concurrency.

SSOT: SearXNG endpoint = $MIOS_URLS_SEARXNG; micro-LLM = $MIOS_MICRO_MODEL /
$MIOS_MICRO_ENDPOINT (same as agent-pipe); fan-out knobs = $MIOS_WEB_FANOUT /
$MIOS_WEB_FANOUT_WORKERS / $MIOS_WEB_RRF_K; the anchor stopword screen =
mios.toml [search].anchor_stopwords ($MIOS_WEB_ANCHOR_STOPWORDS CSV) -- all
rendered from mios.toml. The localhost fallbacks + the documented default
screen are the only literals, matching the unit/SSOT defaults. The query
tokenizer is unicode-aware (CJK/accented scripts tokenize, never to zero).

<!-- mios-src:64ba19d2f0dc from usr/libexec/mios/mios-web-search:4-35 -->

### Parses a MAC address in any standard notation into exactly...

Parses a MAC address in any standard notation into exactly 6 bytes.
    Accepts:
      - 'AA:BB:CC:DD:EE:FF'
      - 'aa-bb-cc-dd-ee-ff'
      - 'aabb.ccdd.eeff'
      - 'aabbccddeeff'

<!-- mios-src:64159b194461 from usr/libexec/mios/mios-wol-proxy:55-62 -->

### Parses SecureON password/payload which must resolve to...

Parses SecureON password/payload which must resolve to exactly 6 bytes.
    Accepts:
      - 12 hex characters (e.g. '01:02:03:04:05:06', '01-02-03-04-05-06', '010203040506', '0x010203040506')
      - Exactly 6 ASCII / UTF-8 characters (e.g. 'secret', 'passwd')

<!-- mios-src:c760dac71756 from usr/libexec/mios/mios-wol-proxy:72-77 -->

### Constructs 108-byte WoL Magic Packet with SecureON...

Constructs 108-byte WoL Magic Packet with SecureON password:
      - Header: 6 bytes 0xFF
      - Target MAC: 16 repetitions of 6-byte MAC address (96 bytes)
      - SecureON: 6-byte password / payload
    Total: 6 + 96 + 6 = 108 bytes.

<!-- mios-src:4fc74eba6d00 from usr/libexec/mios/mios-wol-proxy:104-110 -->

### Enforces interface security invariant

Enforces interface security invariant: only private LAN / mesh / loopback / bridge
    interfaces are permitted. Untrusted / public / WAN interfaces are strictly rejected.

<!-- mios-src:dafc849f12dd from usr/libexec/mios/mios-wol-proxy:159-162 -->

### Enforces destination IP security invariant: only private...

Enforces destination IP security invariant: only private LAN / loopback / link-local /
    broadcast addresses are permitted. Public global IP addresses are strictly rejected.

<!-- mios-src:b6013243d18d from usr/libexec/mios/mios-wol-proxy:178-181 -->

### Workflow 4 Verification Test Suite

Workflow 4 Verification Test Suite: Build Context & Artifact Handoff
Tests all 6 task implementations:
1. Syntax typo fix in mios-build-driver:25 ($(date -Iseconds))
2. Staging /etc/mios and invoking materialize-build-ctx.py when MIOS_REPO=/
3. Variable evaluation order in 02-materialize-build-ctx.sh (export before log)
4. materialize-build-ctx.py exit 2 on missing psycopg and dual-write of build_phases.json
5. tools/mios-overlay.sh binary protection in sed normalization
6. Justfile:preflight on-demand cargo build and bootstrap fallback

<!-- mios-src:0d6a396f76c8 from usr/libexec/mios/test_mios_buildctx.py:3-12 -->

### task_3428

task_3428: WHEN mios-mcp.service starts THE SYSTEM SHALL have its MCP port
defined -- by the resolver-rendered /etc/mios/install.env, not a literal.

* the exports the resolver renders from the vendor mios.toml carry
  MIOS_PORTS_MCP / MIOS_PORTS_MCP equal to [ports].mcp;
* mcp-server-runner's preamble, run under exactly that environment (ambient
  MIOS_* scrubbed), resolves MIOS_PORTS_MCP to that value;
* neither the unit nor its [units."mios-mcp.service"] SSOT mirror assigns a
  MIOS_* port literal.

Negative controls: the same environment without the port names must stop
the runner with its named error, and a planted Environment=MIOS_PORTS_MCP=<n>
must be named by the literal check.

<!-- mios-src:3e1552f4708e from usr/libexec/mios/test_mios_mcp_port.py:5-18 -->

### Two-sided verification controls for task_3432. Every expected...

Two-sided verification controls for task_3432.

Every expected value is read from the tree, never written here as a literal:

* the paths a unit writes are the tmpfiles.d-declared directories that its
  Exec* command lines and the scripts they run refer to;
* each such path must be writable under ProtectSystem=strict, through
  ReadWritePaths= or StateDirectory=;
* when a unit claims a path with StateDirectory=, the owner systemd will
  enforce (User=/Group=, root when absent) and StateDirectoryMode= (0755 when
  absent) must equal the owner and mode usr/lib/tmpfiles.d declares for the
  same path. systemd recursively chowns a StateDirectory= to the unit's user
  on every start, so a mismatch re-owns the tmpfiles-declared directory.

Negative controls plant defects in a scratch copy of the files involved and
require a diagnostic naming the unit and path; the real tree is hashed before
and after to show nothing was mutated.

Runtime controls (firstboot across boots, EROFS in the journal) need a booted
systemd host and are not run by this file.

<!-- mios-src:54e523c0a61e from usr/libexec/mios/test_mios_unit_hardening.py:4-24 -->
### 'MiOS' Headscale mesh VPN coordinator first-boot config...

'MiOS' Headscale mesh VPN coordinator first-boot config generator.

Writes /etc/headscale/config.yaml from the [headscale] + [metal.mesh] + [ports] SSOT in
mios.toml (layered: vendor /usr/share/mios/mios.toml < host /etc/mios/mios.toml).

Idempotent + non-destructive: if the config file already exists it does NOTHING
(Headscale state/sqlite remains preserved across runs). Pass --force to regenerate.

<!-- mios-src:a32618333bfe from usr/libexec/mios/mios-headscale-firstboot:4-11 -->
### Units mios-gen pod-quadlets writes under users/ (podman's...

Units mios-gen pod-quadlets writes under users/ (podman's user Quadlet
search path) instead of the system directory: they run rootless under each
login user's systemd. Everything not listed is a system unit.

<!-- mios-src:72159aff486f from usr/share/mios/mios.toml:8876-8878 -->
### [rust.categories] -- ADR-0021 Function-Named Binary & Crate...

----------------------------------------------------------------------------
[rust.categories] -- ADR-0021 Function-Named Binary & Crate SSOT Registry
----------------------------------------------------------------------------
Maps script domains and existing crates to destination static binaries,
lifecycle roles, install directories, and responsible owners (task_3488).
Ownership gate: mios-gate rust-categories.
Owner lanes and install roles are declared once each, in [rust.categories.owners]
and [rust.categories.roles], as lists of categories (every category in exactly
one of each). A role names its [build.native.categories.<role>] table, which
owns the install directory. A destination binary's own crate is implied by
binary=; crates= lists only the other crates folded into it.
Script layer: scope= globs claim the scripts a category owns for porting;
exempt categories (role exempt, no binary) carry their reason in description=;
universe= fixes the script roots the gate must account for, and max_unowned
is the shrink-only ceiling for scripts no category claims yet (task_3488).
A replaces= entry is ported-and-deleted evidence: the named script must be
absent from the tree in the same commit that proves parity (ADR-0021).

<!-- mios-src:d9a9204b4e94 from usr/share/mios/mios.toml:13459-13475 -->
### mios-manual -- corpus ledger and census for the MiOS...

mios-manual -- corpus ledger and census for the MiOS documentation system.

Spec: docs/design/doc-generative-documentation.md sections 3.4 and 5.

Subcommands:
  ledger          rebuild usr/share/mios/reference/manual-corpus.tsv from the tree
  audit           census as JSON (counts by class and reason)
  coverage        the two ratchet numbers, for the gate and for humans
  harvest         move a comment's prose into a doc and record where it landed
  prune           delete a comment that provably landed (the only destructive path)
  landing         verify every pruned comment still lands in a doc
  check-functions verify harvested AI-functions lines against source
  render          splice derived content into MIOS-GEN marker interiors
  rehash          after an in-place rewrite (mios-task migrate-ids), move landed rows to their new hash

`--root` selects the repo, and either layout resolves, so the same tools serve
mios.git and mios-bootstrap.git without being copied across (Law 15).

The ledger is the safety mechanism for the whole programme. Deletion of a
comment is only ever permitted once a ledger row proves its knowledge reached a
doc, so the landing columns must survive ordinary code churn: they are carried
forward BY CONTENT HASH, never recomputed. Moving a block within a file, or
reformatting the code around it, keeps its landing record.

Rows for blocks that no longer exist but were pruned are kept as tombstones --
that retention is what lets a gate prove, after the fact, that a comment which
is gone did land somewhere first.

<!-- mios-src:88e704cf17e5 from usr/libexec/mios/mios-manual:5-32 -->

### Carry landings across a rewrite that changed only the ids...

Carry landings across a rewrite that changed only the ids in comment text.

    `mios-task migrate-ids` rewrites old task ids to typed ones in the comments and in the docs they landed in
    alike. A comment's hash changes with its text, so the ledger, which carries a landing by hash, would lose
    it and `distill` would land the prose a second time. Each changed source is lexed as it was at --from and
    as it is now, and the two block sequences are aligned on their text with every word that holds a digit
    masked (an old id and its typed id mask alike); an aligned pair is one comment, so its old hash moves to
    its new one in the ledger and in every mios-src marker of the docs. A hash two different comments would
    move to is left alone.

<!-- mios-src:29140265cbba from usr/libexec/mios/mios-manual:1473-1482 -->

### AGY task hygiene ratchet over the frozen AGY-TASKS.md....

AGY task hygiene ratchet over the frozen AGY-TASKS.md. Duplicate task IDs make dependencies ambiguous.
The task from which AGY tasks carry the full schema (a typed id once the ids are migrated); a DONE task
is exempt by its marker. schema_also names the tasks created after it that the list files before it,
so their numbers sit below it -- the schema is demanded of them too.

<!-- mios-src:35f44e5ef3ae from usr/share/mios/mios.toml:12312-12315 -->

### The highest task number issued (ADR-0028). Numbers are...

The highest task number issued (ADR-0028). Numbers are never reissued: the list's highest may not fall
below this, so a dropped last task cannot hand its number on. Raising it to the current highest tightens it.

<!-- mios-src:78da468ea451 from usr/share/mios/mios.toml:12352-12353 -->
### ADR-0030

ADR-0030: the 500-file budget, TOTAL (dependencies included). Each tracked file counts in the
FIRST domain of `order` whose glob matches (fnmatch: * also crosses /). max_files only comes DOWN:
a fold lane lowers its domain in the commit that folds; a new file is paid for by a fold in that commit.

<!-- mios-src:ddce63c53e8a from usr/share/mios/mios.toml:12880-12882 -->
