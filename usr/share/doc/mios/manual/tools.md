<!-- AI-hint: Manual pages distilled from the source comments of tools, sanitized, each passage anchored to the comment it came from. -->

# tools

### tools/audit-version-literals.py -- Audit & inventory...

tools/audit-version-literals.py -- Audit & inventory version tokens across MiOS repo.
Classifies version literals as:
  (a) SSOT-definition
  (b) SSOT-derived/placeholder
  (c) HARDCODED-literal

Emits usr/share/mios/reference/version-literals-audit.tsv

<!-- mios-src:61f303f40e6f from tools/audit-version-literals.py:5-13 -->

### Strip INHERITED MIOS_* before running the bash twin. The...

Strip INHERITED MIOS_* before running the bash twin. The comparison asks
"what does userenv.sh resolve from SSOT?", but bash reports every MIOS_*
in its environment -- so any ambient var the TOML side has no key for
shows up as a mismatch. CI sets MIOS_DRIFT_REQUIRE_TOOLS=1 as a workflow
knob, which failed the check with:
  Var MIOS_DRIFT_REQUIRE_TOOLS: Toml resolved '', Bash resolved '1'
Keep only the tier pointers the resolver needs to find the SSOT layers.

<!-- mios-src:568191b44c9c from tools/check-resolver-twin.py:40-46 -->

### Repo-relative paths git tracks, or None when git is...

Repo-relative paths git tracks, or None when git is unavailable.

    The manifests embed a walk of the tree. Walking the FILESYSTEM makes the
    output a function of the developer's working directory -- local scratch
    files, .bak files and untracked notes all land in root-manifest.json -- so
    a manifest generated on a dev box can never match one regenerated on a
    clean CI checkout, and check_ai_manifests_fresh fails forever. Restricting
    the walk to tracked files makes the artifact reproducible anywhere.

<!-- mios-src:28caf1dca73d from tools/generate-ai-manifest.py:33-41 -->

### Generate the MiOS agent egress firewall (#54). Zero-trust...

Generate the MiOS agent egress firewall (#54).

Zero-trust federation calls for an OUTBOUND firewall: a compromised or misled
agent must not be able to exfiltrate to arbitrary internet hosts. The correct
layer for that is the OS (nftables), scoped to the agent's uid -- an app-level
hook would be incomplete (httpx clients are constructed ad-hoc throughout the
orchestrator). This emits that ruleset from SSOT; the operator applies it.

It is uid-scoped, so it does not disturb other users: `web_search` keeps working
because the agent reaches searxng over loopback, and searxng (a different uid)
reaches the internet.

<!-- mios-src:15b902cf01e4 from tools/generate-egress-firewall.py:5-16 -->

### This used to rmtree repo_root/usr/share/doc/mios/manual...

This used to rmtree repo_root/usr/share/doc/mios/manual unconditionally,
derived from repo_root and ignoring --output entirely: pointing --output at
a scratch file still deleted the in-repo directory. That directory is where
AUTHORED manual prose is meant to live, so the tool could destroy
hand-written content that no generator can reproduce. Clean only a stale
split-page directory that sits beside the file we are actually writing, and
never one holding git-tracked files.

<!-- mios-src:448a427d36d9 from tools/generate-manual.py:28-34 -->

### tools/generate-pipeline-index.py Generates...

tools/generate-pipeline-index.py
Generates usr/share/mios/reference/pipeline-index.tsv from automation/[0-9][0-9]-*.sh scripts.
Enforces 1:1 mapping for stage identity coordinates (ADR-0012, WS-NUMBER task_1577).

<!-- mios-src:f43995ec2b39 from tools/generate-pipeline-index.py:2-6 -->

### Generate MiOS .pod Quadlets from the [pods.*] SSOT (WS-7)....

Generate MiOS .pod Quadlets from the [pods.*] SSOT (WS-7).

A co-resident group -- a set of containers that must share a podman pod (one
network namespace + lifecycle) -- was previously a hand-authored .pod Quadlet
(only mios-webtools). That is drift-prone: the pod's [Unit]/[Pod]/[Install] and
its member list lived only in the file. This projects each [pods.<name>] in
mios.toml to a deterministic <name>.pod under usr/share/containers/systemd/, so:

  * the co-resident group is declared ONCE (SSOT), and
  * tools/generate-k3s-manifests.sh -- which reads the LIVE pods -- projects the
    same workloads to k3s, so the cluster path is one faithful bridge from SSOT.

Each member .container still declares `Pod=<name>.pod` (Quadlet wires the
Wants/After on the pod service); the member list here is the documented SSOT +
fuels a drift check that every declared member exists as a .container.

Pure renderer (render_pod_quadlet) so it unit-tests offline (--selftest), in the
sibling style of the other tools/ generators. Same SSOT -> byte-identical output.

<!-- mios-src:11f8e1c5b95e from tools/generate-pod-quadlets.py:5-23 -->

### [image.sidecars] -- the digest-pinned image SSOT. Consulted...

[image.sidecars] -- the digest-pinned image SSOT. Consulted by
    _sidecar_image() so bare (no-userenv) regeneration renders the committed
    @sha256 instead of the digestless inline fallback (Quadlet digest drift).

<!-- mios-src:a9bc76e9406e from tools/generate-pod-quadlets.py:212-214 -->

### Provision the MiOS agent mTLS keypair + CA (#54)....

Provision the MiOS agent mTLS keypair + CA (#54).

Zero-trust federation needs peers to mutually authenticate. The ed25519 *message*
principal (#60) signs delegations; mTLS authenticates the *transport*. This mints
the PKI for that: a self-signed local CA + an agent leaf certificate (clientAuth +
serverAuth) signed by it. Peers trust each other by exchanging CA certs.

Trust model: self-signed local CA per node is the standard self-hosted default
(point [security.mtls] at an existing org CA to override). The enforcing half --
making the A2A endpoint REQUIRE client certs -- is reverse-proxy deployment
(MiOS terminates TLS at the proxy), documented in security/README.md; this tool
only provisions the credentials.

Idempotent: an existing CA is reused (so peer trust survives re-runs); the agent
leaf is re-issued. Requires `cryptography`. Run where the certs should live.

<!-- mios-src:1655be81ab25 from tools/provision-agent-mtls.py:5-20 -->

### render-globals.py -- generate BOTH globals resolvers from...

render-globals.py -- generate BOTH globals resolvers from the SSOT.

automation/lib/globals.sh and globals.ps1 used to be two divergent hand-typed
registries (~200 literals each) kept in step with mios.toml only by drift
checks. They are now generated in full, directly, under their original names --
every consumer that sources/dot-sources them is untouched, and there is no
`.generated` sidecar and no shim layer.

Only ONE thing cannot be a constant: the version, which is read from a file at
run time. That logic is emitted as a preamble from this generator, so it still
lives in exactly one place.

Usage:
    tools/render-globals.py           # write both resolvers
    tools/render-globals.py --check   # exit 1 if either has drifted

<!-- mios-src:a14177eaae8b from tools/render-globals.py:5-20 -->

### Assign-if-unset. Prefer the idiomatic `: "${VAR:=value}"`...

Assign-if-unset.

    Prefer the idiomatic `: "${VAR:=value}"` -- several drift checks parse that
    exact shape out of this file. It is unusable when the value contains `}`
    (message templates carry `{placeholder}`), which would close the expansion
    early and make the file a syntax error; those fall back to
    `[ -n "${VAR+x}" ] ||`, which has identical already-set-wins semantics.

<!-- mios-src:e7e3421e3913 from tools/render-globals.py:234-241 -->

### The word in `"${VAR:=word}"` is still quote-processed, so a...

The word in `"${VAR:=word}"` is still quote-processed, so a lone ' or "
inside it (e.g. "the operator's phone") starts an unterminated quote.
Only take the idiomatic form when the value is free of every metacharacter.

<!-- mios-src:ea6d3604444b from tools/render-globals.py:243-245 -->

### render-ports.py -- project [ports.categories] onto the flat...

render-ports.py -- project [ports.categories] onto the flat [ports] table.

The categories table is the numbering SSOT: each category owns a `base`, a
`stride` and an ORDERED `members` list, and a member's port is

    base + index_in_members * stride

`pinned` entries (DNS/53) are protocol contracts and are emitted verbatim.

Usage:
    tools/render-ports.py            # rewrite the flat [ports] table in place
    tools/render-ports.py --check    # exit 1 if the flat table has drifted
    tools/render-ports.py --print    # print the derived name=port map

<!-- mios-src:11affdf550ad from tools/render-ports.py:5-18 -->

### Return {port_name: value} derived from [ports.categories]....

Return {port_name: value} derived from [ports.categories].

    Pinned ports are emitted at their literal value; derived ports are
    base + index*stride. stack_id is applied later by the resolver's
    process_val(), not here, so this stays the pre-offset SSOT.

<!-- mios-src:8f8b661954a5 from tools/render-ports.py:40-45 -->

### `${MIOS_PORT_X:-1234}` literals are degrade-open defaults...

`${MIOS_PORT_X:-1234}` literals are degrade-open defaults, but a
    hand-typed one silently goes stale the moment a category base moves. Treat
    them as GENERATED: rewrite every one to its SSOT value.

<!-- mios-src:8d84ef72541b from tools/render-ports.py:192-194 -->

### Resolve the python MiOS itself provisions. Python is a...

Resolve the python MiOS itself provisions.

Python is a DECLARED MiOS dependency, not something to hope for: mios.toml
[apps.winget].pkgs lists "Python.Python.3.14" under "Critical runtime /
toolchain", so every MiOS host has it globally (dnf python3 on Linux, winget
on Windows), and installation/mios-install.ps1 + Reinstall-MiOSDEV.ps1 already
resolve it at %LOCALAPPDATA%\Programs\Python\Python314\python.exe.

The trap: `command -v python` SUCCEEDS on Windows even when it resolves to the
Microsoft Store alias stub, which prints "Python was not found" and exits. The
old probe (`command -v python3 || PY=python`) therefore set PY to the stub, and
every generator below silently did nothing while sync reported success -- the
tree looked synced while the manifests went stale. So probe by EXECUTING, and
try the MiOS-installed interpreter before bare names.

<!-- mios-src:4cf964f45b77 from tools/sync-generated.sh:24-37 -->

### EVERY renderer resolves through the layered resolver, which...

EVERY renderer resolves through the layered resolver, which honours
MIOS_ROOT / MIOS_TOML* from the environment. On a MiOS host (or the MiOS-DEV
container) those are already exported and point at the INSTALLED system, so
an unpinned run silently renders the installed SSOT into this repo's
artefacts -- which is how globals.ps1 ended up carrying 'MiOS User' and
the installed host's `agent_pipe` port. Pin every tier to the tree being synced.

<!-- mios-src:7173c25c3b4b from tools/sync-generated.sh:59-64 -->

### title

title: MiOS Computer Use
author: MiOS
version: 0.1.0
description: |
  ONE Open WebUI Native tool surfacing the MiOS computer-use capability
  (WS-4 P0) as typed tool_calls: the EXISTING desktop control + vision
  grounding verbs (mios-computer-use / mios-pc-control / mios-pc-vision)
  PLUS the new FOSS offline document generation tool (mios-docgen ->
  Pandoc + LibreOffice). The chat model invokes these directly instead of
  indirecting through the generic `terminal:` shell tool.

  This is the LiteCUA Perceptor/Reasoner/Worker pattern assembled from
  MiOS parts (NO Wide-Moat / BSL vendor stack -- see
  concepts/aios-implementation-plan.md section 0-B):
    * Perceive : cu_screenshot  (one-shot capture)
    * Ground   : cu_ground / cu_atspi_query  (AT-SPI first, vision fallback)
    * Act      : cu_click / cu_type / cu_key / cu_key_combo / cu_window_list
    * Produce  : docgen_build / docgen_convert  (author/convert artifacts)

  Dispatch goes through the OPERATOR-side launcher broker (unix socket at
  /run/mios-launcher/launcher.sock), exactly like the sibling mios_verbs
  tool -- so the verbs inherit the operator's WSLg / Wayland session
  (WAYLAND_DISPLAY, DBUS, WSL_INTEROP) that an in-container subprocess
  could never see.

  GATING + SAFETY (binding rule -- default-off / degrade-open):
    * Master valve ENABLED (default True) toggles the whole tool.
    * WRITE_ACTIONS_ENABLED (default FALSE) gates the side-effecting input
      verbs (click/type/key/key_combo). Read-class verbs (screenshot,
      ground, atspi_query, window_list, docgen) work with writes off.
    * mios-docgen itself is independently gated by [computer_use].docgen_enable
      server-side; when off it returns ok=false (this tool surfaces that).
  Every method returns structured JSON the model can reason over, so a
  failure is unambiguous (no "I clicked the button" hallucination over a
  silently-failed call).

requirements:
  pydantic

<!-- mios-src:9cde6af7c1f5 from usr/share/mios/openwebui/tools/mios_computer_use.py:4-43 -->

### Capture the desktop to a PNG (one-shot, read-only)....

Capture the desktop to a PNG (one-shot, read-only). Env-adaptive:
        local Wayland (portal/grim), WSLg via mios-pc-control, or a federated
        remote executor -- the same verb works everywhere.

        :param path: Output PNG path (e.g. /tmp/screen.png). Feed this to
            cu_ground to locate a UI element before clicking.
        :return: JSON {success, stdout, stderr}.

<!-- mios-src:e9d3dc92b109 from usr/share/mios/openwebui/tools/mios_computer_use.py:166-173 -->

### Locate a UI element by natural-language description ->...

Locate a UI element by natural-language description -> click
        coordinates. Read-only. AT-SPI accessibility tree FIRST (deterministic,
        no pixels), local vision model (qwen3-vl / UI-TARS) only as fallback.
        Returns {x, y, confidence}; pass x/y to cu_click.

        :param query: Description of the target (e.g. "the OK button",
            "the search field", "Save toolbar icon").
        :return: JSON {x, y, confidence, reasoning, source}.

<!-- mios-src:9116ea149f9b from usr/share/mios/openwebui/tools/mios_computer_use.py:186-194 -->

### Semantic accessibility-tree lookup (role/name match) ->...

Semantic accessibility-tree lookup (role/name match) -> screen
        coordinates, NO pixels/vision. Read-only. Faster + more reliable than
        cu_ground for standard widgets (buttons, menus, fields).

        :param query: Element role or name substring (e.g. "Cancel", "menu",
            "text").
        :return: JSON {query, matches:[{name, role, x, y, w, h}]}.

<!-- mios-src:1ca2bcdb0f17 from usr/share/mios/openwebui/tools/mios_computer_use.py:202-209 -->

### Click at pixel (x,y) on the desktop. WRITE action -- gated....

Click at pixel (x,y) on the desktop. WRITE action -- gated.
        Usually preceded by cu_ground / cu_atspi_query to find the coords.

        :param x: X pixel offset from top-left.
        :param y: Y pixel offset from top-left.
        :param button: left (default) | right | middle.
        :return: JSON {ok, action, backend} or {success:false,...}.

<!-- mios-src:512d9df90b58 from usr/share/mios/openwebui/tools/mios_computer_use.py:235-242 -->

### Type literal text into the focused surface. WRITE action --...

Type literal text into the focused surface. WRITE action -- gated.
        For editing FILES prefer the text-editor verbs; this is for live UI.

        :param text: The literal text to type.
        :return: JSON {ok, action, backend} or {success:false,...}.

<!-- mios-src:90b462569757 from usr/share/mios/openwebui/tools/mios_computer_use.py:252-257 -->

### Author a NEW document from text content you write, emitting...

Author a NEW document from text content you write, emitting a real
        office artifact (.docx / .pptx / .xlsx / .pdf / .html). The OUTPUT
        FORMAT is inferred from out_path's extension. You write markdown (or
        CSV for spreadsheets); MiOS renders the binary via Pandoc / LibreOffice
        -- fully local, no cloud. For .pptx, separate slides with a line of
        `---`. For .xlsx, set from_fmt="csv" and put CSV rows in content.

        :param out_path: Destination path with the target extension
            (e.g. /tmp/report.docx, /tmp/deck.pptx, /tmp/data.xlsx, /tmp/x.pdf).
        :param content: The source content to render. Markdown for docx/pptx/
            pdf/html; raw CSV rows for xlsx (with from_fmt="csv").
        :param from_fmt: Source format of `content`: markdown (default) |
            plain | html | csv.
        :return: JSON {ok, output, source, target, bytes} or {ok:false, error}.

<!-- mios-src:d768f6ac6470 from usr/share/mios/openwebui/tools/mios_computer_use.py:301-315 -->

### Convert an EXISTING file to another format (e.g. .docx ->...

Convert an EXISTING file to another format (e.g. .docx -> .pdf,
        .md -> .docx, .csv -> .xlsx, .pptx -> .pdf). Fully local via Pandoc /
        LibreOffice headless. Target format inferred from out_path's extension.

        :param in_path: Source file path (must exist on the host).
        :param out_path: Destination path; its extension picks the format.
        :return: JSON {ok, input, output, source, target, bytes} or
            {ok:false, error}.

<!-- mios-src:c6b5be3acfdb from usr/share/mios/openwebui/tools/mios_computer_use.py:336-344 -->
### A ratchet ceiling, or a hard failure if the SSOT has...

A ratchet ceiling, or a hard failure if the SSOT has stopped carrying it.

        Defaulting an absent ceiling to 999999 makes the check unfailable the
        moment a key is renamed, and it keeps reporting PASS while doing so --
        the worst outcome, because it also stops anyone looking. An absent
        ceiling is a broken gate, not an unlimited one.

<!-- mios-src:6052f6ace2c5 from tools/check-comment-ratchet.py:31-37 -->

### Gate

Gate: a hazard that only bites above one node is counted, not discovered.

A MiOS-Metal fleet is 2-6 boxes, so a config that only works standalone is a
defect waiting for the operator to add a peer. Each hazard is detected from the
tree rather than from a hand-list -- multiple archetypes able to stand up a k3s
control plane with no join path, and Pacemaker with fencing disabled -- and
every one must sit in the shrink-only [blades.hazards].accepted register under a
max_accepted ratchet. A NEW hazard fails; an entry that no longer reproduces
fails too, so the register cannot be padded.

<!-- mios-src:601cb0c605d3 from tools/check-fleet-safety.py:5-14 -->

### Keep mios-bootstrap.git in step with mios.git (Law 15)....

Keep mios-bootstrap.git in step with mios.git (Law 15).

Contract and rationale: installation/UNIFY.md.

<!-- mios-src:2d659e36d796 from tools/sync-bootstrap.py:4-7 -->

### A checker whose exit code never varies is not a check....

A checker whose exit code never varies is not a check.

These fixtures assert the tool runs against the real tree and returns an exit
code, then assert the specific invariant it exists to defend.

<!-- mios-src:385add9fd404 from tools/test_check-comment-lex-equivalence.py:5-9 -->

### Only assert the env-ceiling path for tools that actually...

Only assert the env-ceiling path for tools that actually READ those vars.

    Asserting it generically made the fixture fail on tools that never consume
    them -- a test failing for a reason unrelated to the behaviour it names is
    worse than no test, because it trains people to ignore it.

<!-- mios-src:2a391675b742 from tools/test_check-comment-lex-equivalence.py:46-51 -->

### A checker whose exit code never varies is not a check....

A checker whose exit code never varies is not a check.

These fixtures assert the tool runs against the real tree and returns an exit
code, then assert the specific invariant it exists to defend.

<!-- mios-src:385add9fd404 from tools/test_check-comment-ratchet.py:5-9 -->

### Only assert the env-ceiling path for tools that actually...

Only assert the env-ceiling path for tools that actually READ those vars.

    Asserting it generically made the fixture fail on tools that never consume
    them -- a test failing for a reason unrelated to the behaviour it names is
    worse than no test, because it trains people to ignore it.

<!-- mios-src:2a391675b742 from tools/test_check-comment-ratchet.py:46-51 -->

### A checker whose exit code never varies is not a check....

A checker whose exit code never varies is not a check.

These fixtures assert the tool runs against the real tree and returns an exit
code, then assert the specific invariant it exists to defend.

<!-- mios-src:385add9fd404 from tools/test_check-doc-ratchet-monotone.py:5-9 -->

### Only assert the env-ceiling path for tools that actually...

Only assert the env-ceiling path for tools that actually READ those vars.

    Asserting it generically made the fixture fail on tools that never consume
    them -- a test failing for a reason unrelated to the behaviour it names is
    worse than no test, because it trains people to ignore it.

<!-- mios-src:2a391675b742 from tools/test_check-doc-ratchet-monotone.py:46-51 -->

### Tests for the above-one-node hazard gate. Both detectors...

Tests for the above-one-node hazard gate.

Both detectors are covered independently: k3s multi-server needs BOTH two
grantors and a join-less `k3s server`, so a K3S_URL or a single grantor must
clear it, and pacemaker fencing must be read from code and not from comments.
Then every way the register stops measuring -- unsorted, duplicated, an unknown
id that can never retire, an entry that no longer reproduces, a missing ceiling,
a raised ceiling, a ceiling left high. Declaring max_nodes = 1 disarms the
hazards, because standalone is a real deployment and not a loophole.

<!-- mios-src:fc98532d7a4d from tools/test_check-fleet-safety.py:4-13 -->

### The three behaviours the drift gate depends on. An empty...

The three behaviours the drift gate depends on.

An empty launcher table used to render nothing, compare nothing, and report
success while 9 launchers shipped ungoverned -- so "refuses an empty table" is
the fixture that matters most here.

<!-- mios-src:a44982b23533 from tools/test_render-desktop.py:5-10 -->

### What the mirror must not get wrong. Two failure modes are...

What the mirror must not get wrong.

Two failure modes are specific and expensive: silently WRITING when only asked
to report, and appending a duplicate table instead of rewriting one -- the
duplicate-table bug that has made mios.toml unparseable twice in this repo.

<!-- mios-src:54cdb331075a from tools/test_sync-bootstrap.py:5-10 -->

### ADR-0016 D14

ADR-0016 D14: CephFS is a NATIVE service of the Mini platform, on
bare metal. Declaring it `either` would let a scheduler put the
storage plane on a transient OCI image.

<!-- mios-src:60a4a1ba90ee from tools/test_generate-metal-vs-hosted.py:345-347 -->

### One reader for the suite registry, so listing and checking...

One reader for the suite registry, so listing and checking cannot disagree.

Both publishers ask this module what to run. Before it existed each workflow
carried its own hand-written list: GitHub ran fifteen suites, Forgejo ran eight,
and thirteen tracked suites ran nowhere at all -- including nine that failed
only because they pointed at the INSTALLED agent-pipe rather than the tree they
ship from. Nobody noticed, because a suite nobody runs reports nothing.

--check is what keeps that from recurring: a tracked suite must be in a tier, be
matched by a glob entry, or be exempt with a reason. Adding a test file and no
registration turns the gate red.

<!-- mios-src:a0a753bedbfb from tools/ci-suites.py:4-15 -->

### Publisher parity. Registering a tier nobody invokes is the...

Publisher parity. Registering a tier nobody invokes is the same as not
having it, and the two workflows previously ran different sets precisely
because each carried its own list.

<!-- mios-src:9cce0c4717ae from tools/ci-suites.py:113-115 -->

### A file both repos track must be mirrored or explicitly not...

A file both repos track must be mirrored or explicitly not mirrored.

    Thirty-five files were tracked in both repositories; sixteen were declared
    and eighteen of the remaining nineteen had already diverged, silently,
    because nothing required them to be classified at all.

<!-- mios-src:0925ddb9cceb from tools/sync-bootstrap.py:123-128 -->

### A negative test mutates the tree and is supposed to put it...

A negative test mutates the tree and is supposed to put it back.

When one does not, the mutation ships. Three reached the SSOT in a single
session: a capability requirement replaced with an injected name, a port list
with an entry repeated twice, and a threshold key commented out. Between them
they turned five suites red, and the failures pointed at the suites rather than
at the leak, so the cost was paid several times over before anyone looked at the
SSOT diff.

Backup files count too. Copies with a rescue suffix are how a test preserves
the original, and a run that dies before restoring leaves one in the tree.

This scans what git tracks, because an untracked leftover is a local mess while
a tracked one is shipped.

<!-- mios-src:dd8626e7b274 from tools/check-leaked-fixtures.py:4-18 -->

### Native man pages, rendered like every other projection....

Native man pages, rendered like every other projection.

They are ordinary roff files under usr/share/man, so man, apropos and man -k
work with no MiOS-specific command in the path: the mechanism is the one the
operating system already has. The content derives from the same SSOT that
renders ports, globals and launchers, so a verb renamed there renames its page.

No page carries a date. Two builds of one tree must produce identical bytes,
and the NO-HARDCODE law rejects dates in generated text.

<!-- mios-src:ebc40a5a8371 from tools/render-manpages.py:4-13 -->

### A page for a verb that no longer exists answers confidently...

A page for a verb that no longer exists answers confidently and wrongly,
so the renderer owns the whole directory rather than only adding to it.

<!-- mios-src:0842226a2209 from tools/render-manpages.py:182-183 -->
### A crate with no tests passes `cargo test` every time. The...

A crate with no tests passes `cargo test` every time.

The workspace run prints `test result: ok. 0 passed` for each such crate, which
reads exactly like a crate whose tests all passed. Ten crates and roughly 5,600
lines are in that state, miosd alone being 3,550 of them, so the suite's green
says far less than it appears to.

The ceiling is shrink-only: a crate may be registered as untested with a reason,
and the count may only fall.

<!-- mios-src:ed048d889332 from tools/check-rust-test-coverage.py:4-13 -->

### A task without a Verify line is a task anyone can call...

A task without a Verify line is a task anyone can call done.

Every field here exists because its absence let something through. Verify is an
exact command whose failure is the proof; Do NOT names the dodge that would
satisfy the task falsely -- raising a ceiling, wrapping a hint, spelling a check
name in a log line. Dep is checked because a dependency pointing at a
non-existent id is an ordering nobody can follow, and ids are counted because 25
of them are already used twice, which makes every Dep naming one ambiguous.

<!-- mios-src:ac3def8cf78a from tools/check-task-schema.py:4-12 -->

### The id below which the schema is not yet demanded. It lives...

The id below which the schema is not yet demanded. It lives in the SSOT as a
shrink-only ratchet, not as a constant here, so retro-fitting a batch of older
tasks is a measurable step rather than an edit nobody notices. A task marked
DONE is exempt whatever its id: it has already been done, so a Verify line
added now would be one nobody checked.

<!-- mios-src:3c264cead283 from tools/check-task-schema.py:22-26 -->

### `## task_1139 -- Campaign banner` covers every id in the...

`## task_1139 -- Campaign banner` covers every id in the range, so
a Dep naming one of them names a task that exists -- but the banner
plus its individual children is the NORMAL shape, not a collision, so
only individual headings count toward the duplicate ceiling.

<!-- mios-src:dbcbe1d51ba6 from tools/check-task-schema.py:62-65 -->

### Prove the artifact gate can fail. `publish` depends on...

Prove the artifact gate can fail.

`publish` depends on `verify-images` to establish that the artifacts it is
about to push are real. The version that shipped ended on a failure counter
that stays zero when the glob loop matches nothing, so an empty build tree
passed it. A gate that cannot fail is worth less than no gate, because the
pipeline is built as though it were checking something.

So this drives the real verifier three ways -- an empty tree, a complete set of
fixtures, and the same set with one artifact removed -- and fails unless the
verdicts come back reject, accept, reject-naming-the-missing-format. It also
holds the wiring in place: the recipe must delegate here, `publish` must depend
on it, and every format the `all` target builds must declare where its output
lands.

<!-- mios-src:d227112fa354 from tools/check-verify-images.py:4-18 -->

### True for a file that declares itself a machine projection....

True for a file that declares itself a machine projection.

        The shell/PowerShell ceilings exist to drive HAND-WRITTEN glue down as it
        migrates to Rust. automation/lib/globals.{sh,ps1} are rendered in full from
        mios.toml, so they grow whenever the operator declares a config key -- growth
        that cannot be "earned back" except by deleting operator configuration. Counting
        them measured the wrong thing: a [cat] -> [field] rename that added keys pushed
        the PowerShell ceiling over its floor with no hand-written line involved.
        Excluding them LOWERS both floors by ~5.3k lines, so the ratchet binds strictly
        tighter on the code it actually governs.

<!-- mios-src:263ae92f5edd from tools/drift-checks.py:204-214 -->

### One value, one name, ratcheted against the baseline ledger....

One value, one name, ratcheted against the baseline ledger.

    Lifted out of its heredoc in the shell gate: 211 lines that nothing
    could import or lint, where a syntax error surfaced only when the
    check ran.

<!-- mios-src:cec6026d93d8 from tools/drift-checks.py:292-297 -->

### resolve the live environment...

--- resolve the live environment -------------------------------------------
Git Bash cannot resolve an absolute path given as a script argument when
bash.exe is launched from Windows Python: both /usr/share/mios/... and /c/MiOS/...
exit 127 "No such file", because /c is resolved against the MSYS root
rather than the drive. The same file runs when passed RELATIVE to a cwd.
The gate passes an absolute $ROOT, so on Windows this check reported "the
resolver produced no environment" -- a gate that could not run at all,
rather than one that passed or failed.

<!-- mios-src:0b1e45142858 from tools/drift-checks.py:373-380 -->

### Return (current, what-write-mode-would-produce) for path....

Return (current, what-write-mode-would-produce) for path.

        check-mode used to compare only the VALUE, via a tolerant
        minsize\s*=\s*"..." regex, while write-mode ALSO normalised the
        spacing. So a file carrying aligned padding passed --check and was
        still rewritten by the generator: the gate reported in-sync on a file
        the generator would change. A check that does not compare what the
        writer produces cannot detect the drift the writer creates, so both
        modes now derive from this one rendering.

<!-- mios-src:19d72f55f759 from tools/generate-bib-configs.py:49-58 -->

### Verify every deployment format the SSOT declares actually...

Verify every deployment format the SSOT declares actually produced a file.

The gate this replaces walked a glob list and ended on the failure counter, so
a tree with no artifacts in it counted zero failures and returned success --
and `publish` depends on it to prove the artifacts are real before the push.
The required set is now derived from `[deploy.formats]`: every format that
declares output globs must match at least one file, that file must clear the
size floor, and its leading bytes must be the ones its format is defined by.

<!-- mios-src:78492b2882e5 from tools/verify-images.py:4-12 -->
### Scanned for coverage. Anything a runner could reasonably be...

Scanned for coverage. Anything a runner could reasonably be expected to
execute, so a dead suite has to be declared dead rather than merely ignored.

The two fitness-function stages are named because leaving them out was not a
gap, it was a hole: deleting "automation/98-drift-checks.sh" from
[ci.tiers].gate raised no violation here, the gate tier stayed non-empty so
run-suites.sh's zero-suite guard never fired either, and the entire drift gate
stopped running while both reported green.

<!-- mios-src:a4e35d4e1afd from tools/ci-suites.py:17-24 -->

### The shell commands a workflow actually executes. Parity...

The shell commands a workflow actually executes.

    Parity used to be `f"run-suites.sh {tier}" in body`: a raw substring over the
    whole file, comments and disabled steps included. A step commented out, or
    guarded `if: false`, kept satisfying the one check whose entire job is to
    notice that a publisher has quietly stopped running a tier. Only the value of
    a live `run:` key counts now.

<!-- mios-src:04b5f8b3842e from tools/ci-suites.py:76-83 -->

### build_exports() returns the UNEXPANDED map on purpose: it...

build_exports() returns the UNEXPANDED map on purpose: it renders
automation/lib/globals.{sh,ps1}, which bash and PowerShell expand at source
time, and keeping `${MIOS_PORTS_AGENT_PIPE}` live there is what lets an
operator's pre-export propagate. mios-resolver --emit=json is the resolved
view and bakes. Comparing the two directly measured that difference in
representation, not a divergence between the resolvers -- 103 "mismatches"
that were the same 91 values written two correct ways. Both sides are put
in the baked form first, by the same twin the Rust emitter calls, so what
survives is real disagreement about a value.

<!-- mios-src:a3cec1454e43 from tools/drift-checks.py:102-110 -->

### Size the deliverable from the INDEX blobs, not the...

Size the deliverable from the INDEX blobs, not the checkout. .gitattributes
checks *.ps1 out as CRLF on every platform, so the working tree carries
~24 KiB of line-ending expansion the commit does not contain -- and with the
total sitting a few KiB past the 201.5 MiB rounding boundary, that expansion
alone pushed tracked_mb to 202 and held this ratchet red against content
nobody added. Blobs are identical in every clean checkout of a commit.

<!-- mios-src:b895f41ea56b from tools/drift-checks.py:182-187 -->

### ADR-0021. Law 14 keeps the AI plane in Python, so it is...

ADR-0021. Law 14 keeps the AI plane in Python, so it is exempt by
prefix from SSOT rather than by a list baked in here.
A sibling unit test is not tooling to port. Counting them made this
ratchet pull against check_module_test_coverage the same way
max_libexec_verbs did below, and 36% of what it measured was test
code. Floor re-baselined down by what the exclusion removes (task_981).

<!-- mios-src:7c8c73010910 from tools/drift-checks.py:234-239 -->

### A test's OWN name is not evidence that it invokes anything...

A test's OWN name is not evidence that it invokes anything: `test_check_foo`
used to satisfy the gate-invocation search purely because `check_foo` appears
in its definition line, certifying a body that asserts nothing. Search only
what follows the signature.

<!-- mios-src:a3b125a34468 from tools/drift-checks.py:3726-3729 -->

### Every exit that is not a rendered policy is an error. This...

Every exit that is not a rendered policy is an error. This used to wrap
the SSOT read in `except Exception: pass` and carry on with
policy_mode = "insecureAcceptEverything" -- the value that accepts any
signature -- under a header claiming SSOT provenance.

<!-- mios-src:abdc7aec96a3 from tools/generate-cosign-policy.py:7-10 -->

### BYTES, not parsed JSON. The parsed comparison this replaces...

BYTES, not parsed JSON. The parsed comparison this replaces called the
compact tracked file "in sync" with an indented render, so a bake that
ran the generator would rewrite a file every check reported current.
Law 8 asks for regenerate-and-diff; semantic equality is weaker.

<!-- mios-src:1c368f6d4cf4 from tools/generate-cosign-policy.py:39-42 -->

### Census size AND line counts from the INDEX blobs, never the...

Census size AND line counts from the INDEX blobs, never the checkout.
On-disk bytes are not a function of the commit: .gitattributes checks
*.ps1 out as CRLF on every platform, so the tree runs ~24 KiB heavier
than the blobs, and the total sits ~13 KiB from the 201.5 MiB rounding
boundary -- committed and CI-rendered values landed on opposite sides.
Reading the checkout also counts a co-worker's UNCOMMITTED edits into
a committed table. Blobs are identical in every clean checkout of the
same commit, so the gate converges.

<!-- mios-src:ce4d50bb40a1 from tools/roadmap-index.py:269-276 -->

### Size-optimized release for the Windows wallpaper daemon...

Size-optimized release for the Windows wallpaper daemon (its profile
previously sat in the member manifest, where cargo silently ignored
it). lto/strip/panic are workspace-level profile knobs cargo cannot
set per package.

<!-- mios-src:29470f63dacf from tools/native/Cargo.toml:54-57 -->
### Operator ruling

Operator ruling: the ceiling governs SUB-MODULES only -- the packages named by
[refactor].submodule_roots. The agent-pipe root (server.py, the mios_*.py
components) holds main modules, where features fold in. Shims are excluded --
they are ~28 lines of lazy re-export.

<!-- mios-src:f7cb199fb90d from tools/check-testhygiene.py:556-559 -->

### The dev container IS the MiOS image plus container wiring...

The dev container IS the MiOS image plus container wiring, so anything that
would make it a different image is refused: a second stage, a FROM other than
the ARG, an ARG default other than [image].ref, a file brought in from the
build context, or a RUN that installs a package, toolchain or payload. A
component the devcontainer lacks belongs in the OS pipeline instead.

<!-- mios-src:af11408df797 from tools/ci-suites.py:320-324 -->

### The Python twin is mios_toml.emit_exports over a merge it...

The Python twin is mios_toml.emit_exports over a merge it performs ITSELF.
load_merged() with no layers hands the merge to mios-resolver whenever one
is on PATH, and the check would then measure the Rust resolver against
itself. emit_exports also resolves ${MIOS_*} cross-references, which is the
baked form --emit=json prints. (tools/render-globals.py, the old Python
side, was ported to `mios-gen render-globals` in 5776d4ff; importing it
crashed this check on every run since.)

<!-- mios-src:28ef4adc99fb from tools/drift-checks.py:109-115 -->

### Container-side listening ports. This used to hard-code...

Container-side listening ports. This used to hard-code (8080, 3002), the
SearXNG and firecrawl upstream internals; SSOT now names that class
`*_internal`. They may appear as the container side of a mapping or in an
in-container `X=N`, never as a bare host-side `PublishPort=N`.

<!-- mios-src:c32f4d0a96ae from tools/drift-checks.py:2559-2562 -->

### A reviewed eval of non-agent input carries this annotation...

A reviewed eval of non-agent input carries this annotation on the line
DIRECTLY above it -- the remedy the message below prescribes. 5f2aadbf
dropped the exemption and kept the message, so following the remedy no
longer cleared the violation. Twin of eval_safety in
src/mios-rs/miosd/src/drift/security.rs (Law 13).

<!-- mios-src:25efb2748554 from tools/drift-checks.py:2865-2869 -->

### What every devcontainer.json owns, as the resolver emits it...

What every devcontainer.json owns, as the resolver emits it (emit_exports: stack_id offset and
    ${MIOS_*} applied): forwardPorts from each [ports] key of [dotfiles.devcontainer].forward_port_keys,
    in order; containerEnv[n] for each MIOS_* name n of .container_env_keys; remoteUser from
    [identity].username; hostRequirements from .host_requirements. Exit 3 on an absent or empty list,
    a name without a resolved (integer, for a port) value, or an invalid user or host requirement.

<!-- mios-src:138dc3a7f084 from tools/sync-dotfiles.py:77-81 -->

### Merge the CLIENT-PORTABLE subset of the SSOT settings into...

Merge the CLIENT-PORTABLE subset of the SSOT settings into every
    devcontainer.json / *.code-workspace settings block and PRUNE every
    [dotfiles.vscode] desktop-only / User-only / unregistered key already there.
    SSOT keys win on conflict; any surface-only key (installer-specific zenMode.*
    tuning) survives. A devcontainer.json's forwardPorts array, remoteUser,
    hostRequirements and the containerEnv entries in dc_owned are owned. Returns [(label, reason)] -- one line per key and file, so
    --check names exactly what is wrong where.

<!-- mios-src:dde5b5ab080e from tools/sync-dotfiles.py:268-274 -->
### Task-plane drift gates. One module, one subcommand per...

Task-plane drift gates. One module, one subcommand per gate.

The frozen lists name every task by its typed id, task_<number> (ADR-0028: migrate-ids rewrote the old ids).

<!-- mios-src:d84f2b14bb71 from tools/check-tasks.py:5-8 -->
### ADR-0030

ADR-0030: max_tracked_files counts EVERY tracked file, dependencies
included (the operator: "500 disparate files -- TOTAL/OVERALL"), and the
domains below partition the same listing.

<!-- mios-src:adf6366d5571 from tools/drift-checks.py:279-281 -->
