<!-- AI-hint: Manual pages distilled from the source comments of src, sanitized, each passage anchored to the comment it came from. -->

# src

### Schema-generic configuration container. Owns stable fields...

Schema-generic configuration container. Owns stable fields directly
(`meta`, `identity`, `build`) while storing all dynamic/operator-defined
sections generically in `raw` to prevent recompilation on mios.toml schema changes.

<!-- mios-src:62e97279e9f1 from src/mios-rs/mios-config/src/lib.rs:102-104 -->

### Allocate every `[ports]` value from `[ports.categories]`...

Allocate every `[ports]` value from `[ports.categories]`, in place.

Must run AFTER all layers merge so a factory/OEM default in the vendor
mios.toml, an operator override in /etc/mios/mios.toml, and a user override
in ~/.config all feed the same derivation. `members` is ordered and the order
IS the numbering, so adding or removing a service reallocates the category
without a hand edit. `pinned` entries are protocol contracts (DNS/53) and are
written verbatim.

This OVERRIDES the flat `[ports]` table, which is only a rendered projection
kept for readability -- otherwise a stale vendor literal would silently beat
an operator who retargeted a category base.

<!-- mios-src:43bce08392de from tools/native/mios-resolver/src/ports.rs:5-16 -->

### WSLg window-centering (folded from mios-gui-watch.ps1) --...

WSLg window-centering (folded from mios-gui-watch.ps1) -- runs as a thread inside the host, so
there is NO separate pwsh process and no login terminal flash. WSLg hosts each Linux GUI app as
an msrdc.exe-owned window; many spawn tiny (e.g. 129x113) at random coords and look "not
rendered" on a 4K display. Poll top-level windows; the first time an msrdc window is seen smaller
than the minimum, resize + center it once, then leave it alone (tracked in `adopted`) so the
operator can move/resize freely afterwards.

<!-- mios-src:739e6f2c63a7 from tools/native/mios-wallpaperd/src/guiwatch.rs:1-6 -->

### mios-wallpaperd -- MiOS living-wallpaper + desktop-services...

mios-wallpaperd -- MiOS living-wallpaper + desktop-services daemon (Law 14 / ADR-0011 native tier).

FIRST DRAFT. There is no Rust toolchain on the authoring box, so this is compiled + iterated in
staging by the provisioned Rust (Install-MiosRust). API calls follow the `windows` 0.58 and
`wry`/`tao` conventions but must be verified at first `cargo build`; treat compile errors as the
expected next step, not a surprise.

ONE binary, three roles (dispatched by argv[1]) -- so it can be a single auto-start service with
NO console and NO surfacing window, replacing MiOS-Wallpaper.exe + MiOS-Wallpaper-Service.exe +
mios-gui-watch.ps1 + the MiOSWallpaper/MiOS-GuiWatch Run keys:
  (default | "service")  -> service controller in session 0; launches "host" in the user session.
  "host"                 -> the wallpaper: WebView attached to WorkerW, behind the desktop icons.
  "gui-watch"            -> standalone WSLg window-centering loop (also run as a thread inside host).

<!-- mios-src:0d6d907a715f from tools/native/mios-wallpaperd/src/main.rs:1-13 -->
### Everything a peer needs to converge, tombstones included....

Everything a peer needs to converge, tombstones included.

An LWW-Element-Set converges only if the remove-set travels with the
add-set. Replicating `active_elements` instead means a delete is visible
on the node that made it and nowhere else: the peer never hears that the
key died, keeps its own live copy, and the two disagree forever.

<!-- mios-src:c17a92a65b83 from src/mios-rs/mios-node/src/state_sync.rs:181-186 -->
### Why loading the registry returns an error instead of a...

Why loading the registry returns an error instead of a phase list.

Each variant is a case that used to resolve to a six-phase hardcoded default
and exit 0, which made a build that ran six of seventy-one phases
indistinguishable from a complete one.

<!-- mios-src:5ddc70dbca17 from src/mios-rs/mios-build/src/lib.rs:45-49 -->

### Load the phase list from `[build.phases].list` in...

Load the phase list from `[build.phases].list` in mios.toml.

This used to swallow every failure and return a six-phase hardcoded
registry (Law 7: NO-HARDCODE). Nothing in the tree could tell the two
apart: `miosd build --list` printed six script names and exited 0, and
automation/build.sh took that list verbatim. A build launched from the
wrong working directory -- MIOS_ROOT defaults to "." -- would therefore
run 01, 02, 05, 07, 98, 99 and report BUILD COMPLETE, having skipped the
kernel config, GPU wiring, SELinux, services, the whole AI plane and
finalize. The registry now refuses rather than guesses (task_962).

<!-- mios-src:f6a2fd766598 from src/mios-rs/mios-build/src/lib.rs:102-111 -->

### Every KEY=VALUE on one unit line that could carry a...

Every KEY=VALUE on one unit line that could carry a credential.

`Environment=` is the declarative surface. `--env KEY=VALUE` on an Exec line
is the OTHER one, and scanning only the first measured the wrong property:
mios-agents.service hands its container a password as
`--env [redacted] on an ExecStart continuation, so a
plain literal planted there passed this gate at rc=0 while the identical
literal on an `Environment=` line failed it. Both controls were run.

Bare `-e` is deliberately NOT matched: it collides with ordinary flags such
as `bash -e`, and nothing in the corpus uses it to pass an environment pair.

<!-- mios-src:105787666ef0 from src/mios-rs/mios-gate/src/credentials.rs:59-69 -->

### The register pins the VALUE, not just the key. A key-only...

The register pins the VALUE, not just the key. A key-only register
cannot tell the shipped placeholder from an operator's real
password baked in by a build-environment variable.

<!-- mios-src:41501dcdbbc7 from src/mios-rs/mios-gate/src/credentials.rs:182-184 -->

### A ceiling above the measurement is slack a later regression...

A ceiling above the measurement is slack a later regression can hide in.
The ratchet only bites if the ceiling EQUALS what is actually there, so a
ceiling left high after a conversion is itself the violation.

<!-- mios-src:6ba36b85afbe from src/mios-rs/mios-gate/src/dispatch.rs:190-192 -->

### The part of `99-postcheck.sh` that can actually run...

The part of `99-postcheck.sh` that can actually run: everything before the
first top-level `exit 0`, with whole-comment lines dropped.

Both halves matter, and both were load-bearing. The file's last line was
`# References for laws: item14 item12 item16 item17` -- a comment, after
`exit 0`, naming four refs that exist nowhere else in the repo. A substring
search over the raw bytes matched it and reported four laws enforced.

<!-- mios-src:3aa7a9ab3ece from src/mios-rs/mios-gate/src/laws.rs:22-28 -->

### A comma list carries the file once

A comma list carries the file once: "98-drift-checks.sh:a,b" means
both a and b live in that file. The previous reader split on comma
FIRST and then dropped any piece without a colon, so the second
enforcer of Law 12 was never checked at all.

<!-- mios-src:9f55c05bf5d1 from src/mios-rs/mios-gate/src/laws.rs:97-100 -->

### Split `dir/pre*suf` into (dir, pre, suf). Deliberately a...

Split `dir/pre*suf` into (dir, pre, suf).

Deliberately a single-`*`, final-segment-only matcher rather than a glob
crate: the patterns this consumes are SSOT and a pattern this cannot
express must be REJECTED loudly, not silently matched by something more
permissive than its author meant.

<!-- mios-src:8347ebae7c25 from src/mios-rs/mios-gate/src/projreg.rs:22-27 -->

### anchor the glob against the register...

--- anchor the glob against the register -------------------------------
The coverage assertion below is only as wide as `discovered`, so the
globs are the check's own allowlist and must be anchored to something
outside themselves -- otherwise DELETING a glob shrinks the scope and
the check reports clean over what is left. (That is not hypothetical:
dropping "tools/render-*.py" took the scope from 21 to 17 and still
exited 0, until this block.) The anchor is the register itself: any
directory a glob names is IN scope, so every registry row living in one
of those directories must be matched by some glob.

<!-- mios-src:464a952f3e07 from src/mios-rs/mios-gate/src/projreg.rs:229-237 -->

### Sections whose numeric keys are ratchets even when the key...

Sections whose numeric keys are ratchets even when the key name only
CONTAINS `max_`/`ceiling` rather than starting or ending with it.

This is the predecessor's list minus two entries it could never use.
`check-ratchet-direction.py` compared against `full_key.split(".")[0]`, the
FIRST path component, while listing `build.ratchet` and
`security.privileged_quadlets` -- dotted names the comparison can never
match, since `section` is the text before the first dot. `gates` went the
other way: a real-looking name for a table mios.toml does not have. The test
below now rejects both shapes (task_992).

<!-- mios-src:2822453608dd from src/mios-rs/mios-gate/src/ratchet.rs:12-21 -->

### A TABLE of key -> reason, deliberately not an array of...

A TABLE of key -> reason, deliberately not an array of inline tables: that
shape renders differently in the two resolver twins, and its registered
divergence set is a shrink-only 12 that a thirteenth key breaches
(check_resolver_differential_parity). Law 13 costs a shape here.

<!-- mios-src:f6cf47695617 from src/mios-rs/mios-gate/src/ratchet.rs:96-99 -->

### Reproduce `tools/generate-cosign-policy.py`'s output...

Reproduce `tools/generate-cosign-policy.py`'s output exactly.

That generator emits `json.dumps(policy, indent=2) + "\n"`, which for this
shape is four levels of two-space indent. Written out rather than pulled
through a JSON library so the expected BYTES are visible here: the defect
this check exists to catch was a generator whose `--check` compared parsed
JSON and so could not see that the tracked file was compact while the
writer emitted indented.

<!-- mios-src:6d5b9555b84e from src/mios-rs/mios-gate/src/sigpolicy.rs:20-27 -->

### Whether a `run` body actually consults the tree. Naming the...

Whether a `run` body actually consults the tree. Naming the parameter `ctx`
instead of `_ctx` proves nothing: check_pipeline_numbering read `ctx.in_image`
for an early skip and then returned a constant Pass, so a parameter-name test
classified it as implemented. rustfmt also splits `ctx\n    .root`, so the
comparison is made on a whitespace-stripped copy or it misses real readers.

<!-- mios-src:5a58ac7a6ecd from src/mios-rs/mios-gate/src/stubs.rs:14-18 -->

### 1-based line numbers inside Rust `#[cfg(test)]` items...

1-based line numbers inside Rust `#[cfg(test)]` items; `.rs` only, else empty.

A version literal in a Rust test module is test DATA -- an upstream tag handed
to the code under test -- not this project's shipped identity. The tag-sorting
fixtures in mios-bake-plan need several DIFFERENT versions by construction, so
requiring each to equal the canonical version would make the test assert
nothing. Only NON-canonical literals are ever reported, so a test hardcoding
the real version was never flagged and no coverage is lost.

Brace-matched. Braces inside string literals are not parsed, so an unbalanced
one ends a range EARLY -- scanning more lines, never fewer, so this can
over-flag but never miss a real hardcoded version.

<!-- mios-src:b61695626b31 from src/mios-rs/mios-gate/src/version_literals.rs:54-65 -->

### Fedora release version. No default

Fedora release version. No default: the version belongs to
mios.toml [versions].fedora and the caller passes it, rather than a
literal here going stale beside it (Law 7).

<!-- mios-src:fd1e254d6c26 from src/mios-rs/miosd/src/main.rs:162-164 -->

### Vendored RPM mirror to probe for. Defaults to the system...

Vendored RPM mirror to probe for. Defaults to the system path;
overridable so the offline branch can be exercised against a
fixture instead of only on a host that happens to have the mirror.

<!-- mios-src:5e6c0ab493af from src/mios-rs/miosd/src/main.rs:170-172 -->

### usr/lib/bootc/kargs.d/01-mios-vfio.toml is NOT wholly...

usr/lib/bootc/kargs.d/01-mios-vfio.toml is NOT wholly generated. It
carries hand-declared kargs that no SSOT key produces --
rd.driver.pre=vfio-pci, which binds vfio-pci in the initramfs before
a GPU driver can claim the card, and kvm-intel.nested=1. The Python
renderer this must match reads the file and strips ONLY the entries
it manages. Starting from an empty list deletes the rest from the
kernel command line while the header still claims the file came
from [kargs].

<!-- mios-src:25feac2b49b4 from src/mios-rs/miosd/src/main.rs:479-486 -->

### Parse the TOML; do NOT scan lines. The line scan this...

Parse the TOML; do NOT scan lines. The line scan this replaced treated any
line containing '=' inside [ports] as a key/value pair, so a COMMENT
became an environment variable name -- including one carrying a backtick
pair, which `bash source` reads as command substitution. install.env is
the file Law 10 (BARE-SAFE-ENV) governs. task_962.

<!-- mios-src:8b835c4baf3a from src/mios-rs/miosd/src/main.rs:615-619 -->

### Collect *.container and *.image at `qdir` and one level...

Collect *.container and *.image at `qdir` and one level below it.

The bash this replaces globs both "${QDIR}/*.container" and
"${QDIR}/*/*.container" (likewise .image). A single read_dir sees only the
first, which silently dropped every Quadlet under a subdirectory -- today
usr/share/containers/systemd/users/mios-coderun-sandbox@.container -- from
/usr/lib/bootc/bound-images.d. An unbound image does not ship with the host
(Law 3: BOUND-IMAGES), and nothing downstream would have said so.

<!-- mios-src:92b254904551 from src/mios-rs/miosd/src/main.rs:900-907 -->

### Parse the TOML; do NOT scan lines. The scan this replaces...

Parse the TOML; do NOT scan lines. The scan this replaces matched any line
starting with "firstboot_tokens" (so "firstboot_tokens_extra" too) and read
only the text after the first '=' on that one line, so a reflow of the
array across lines would yield an EMPTY token set -- and an empty set binds
every image, including the two heavy GPU lanes the register exists to keep
out of the image. Same defect class as render-chrony's silent hardcoded
fallback (task_962).

<!-- mios-src:7ae8c88a7798 from src/mios-rs/miosd/src/main.rs:947-953 -->

### An empty version used to fall back to the literal "44"...

An empty version used to fall back to the literal "44", which would have
written /etc/yum.repos.d/fedora-44.repo on a tree whose SSOT had moved on
-- every package for the whole build coming from the wrong release, under
a filename that looks deliberate. The caller owns the version.

<!-- mios-src:f68f86dc4e5b from src/mios-rs/miosd/src/main.rs:1059-1062 -->

### Harden

Harden: tighten the usbguard config mode, set fapolicyd trust, enable the
three hardening units.

Every write in here used to be `let _ = ...`, and each one was followed by
an unconditional success line -- "[miosd] enabled usbguard.service" printed
whether or not the symlink was created. A hardening step that reports
success it did not achieve is worse than one that fails: the failure is
recoverable, the false report is not visible at all (task_962).

<!-- mios-src:0f436d8ac3e2 from src/mios-rs/miosd/src/main.rs:1145-1152 -->

### Say which of the two happened. This returned a bare Ok(())...

Say which of the two happened. This returned a bare Ok(()), and stage 88
printed "Os-release version projected from SSOT via miosd" on the strength
of that exit code -- so a missing file or an unresolved version reported a
projection that had not occurred. The bash leg it shadows prints nothing
in the same situation, because its success log sits inside the branch
that did the work (task_962).

<!-- mios-src:fc1c041d313b from src/mios-rs/miosd/src/main.rs:1391-1396 -->

### Run one of the repo's generator scripts, resolved against...

Run one of the repo's generator scripts, resolved against MIOS_ROOT.

Four subcommands each carried their own copy of this. Every copy resolved
the script relative to the process working directory, and every copy ended
in an else-branch that printed "... up to date." and returned Ok when the
script was not there -- a claim about an artefact it had never opened. Run
from anywhere but the repo root, `miosd render-uki-cmdline` reported the
kernel cmdline current without reading a single kargs.d fragment, and the
build stage that called it took that for a render (task_962).

An absent generator is now an error naming the root it looked under, so a
wrong MIOS_ROOT fails loudly instead of passing quietly.

<!-- mios-src:9ba4dc892ef6 from src/mios-rs/miosd/src/main.rs:1435-1446 -->

### Parse the TOML; do NOT scan lines. This table happens to be...

Parse the TOML; do NOT scan lines. This table happens to be all
single-line scalars today, so the scan produced the right answer -- but a
comment containing '=' or a multi-line value would break it exactly as it
broke [ports] and [network.ntp]. And unwrap_or_default() meant a
NONEXISTENT manifest rendered four default config files and exited 0.
task_962.

<!-- mios-src:f90502518c8e from src/mios-rs/miosd/src/main.rs:1525-1530 -->

### Parse the TOML; do NOT scan lines. [network.ntp].servers is...

Parse the TOML; do NOT scan lines. [network.ntp].servers is a MULTI-LINE
array, so the line scan this replaced read only `servers = [`, stripped it
to nothing, and silently substituted two hardcoded public NTP hosts --
Law 7, and a sovereignty question on a machine whose SSOT named a pool.
There is no hardcoded fallback now: an absent table yields no servers,
which is what the Python renderer this must match byte-for-byte does.
task_962.

<!-- mios-src:3c3f118fd328 from src/mios-rs/miosd/src/main.rs:1590-1596 -->

### Every scalar leaf under `[agent_pipe]` and `[dispatch]`...

Every scalar leaf under `[agent_pipe]` and `[dispatch]`, read from SSOT.

This was a hardcoded list of nine names. The tables hold 128, so the check
walked 7% of its own subject and announced "all [agent_pipe] budget
variables have code consumers" over the other 93%. Adding an unconsumed key
to either table passed at rc=0, which is the failure the message denies.
It was also a registry of operator-tunable names living in Rust source
rather than in SSOT (Law 7).

<!-- mios-src:f8b83167bd8b from tools/native/mios-aiplane-lint/src/main.rs:6-13 -->

### The directories a budget key can legitimately be consumed...

The directories a budget key can legitimately be consumed from.

This used to be `usr/lib/mios/agent-pipe` alone, which is narrower than the
consumer surface: `[dispatch].gpu_profile` is read by
`usr/libexec/mios/mios-swarm-pack-firstboot` and would have been reported
dead. A false "unconsumed" is as damaging as a missed one -- it sends
someone to delete a key that is load-bearing.

<!-- mios-src:9085473d61d3 from tools/native/mios-aiplane-lint/src/main.rs:85-91 -->

### Source that could read a key

Source that could read a key: Python, shell, Rust, and the extensionless
libexec verbs. Build artefacts and vendored venvs are not source.

Two exclusions are not incidental. This lint's OWN crate is skipped because
it necessarily spells budget keys -- its tests contain them as literals --
and a check that reads itself will find every key it looks for. Test files
are skipped for the same reason one directory out: naming a key is not
consuming it, which is exactly why `reflexion_limit` and `tool_loop_limit`
are on the unconsumed register despite appearing in tools/drift-checks.py.
Verified rather than assumed: the residue is 9 with or without either
exclusion, so neither costs a real consumer.

<!-- mios-src:c3dac2958c7a from tools/native/mios-aiplane-lint/src/main.rs:99-109 -->

### The regression this rewrite exists for

The regression this rewrite exists for: a key added to the table with no
consumer must be reported. Under the hardcoded nine-name list it was not.

<!-- mios-src:7553ab7a1856 from tools/native/mios-aiplane-lint/src/main.rs:423-424 -->

### task_976. The Quadlet scan above skips every `localhost/*`...

task_976. The Quadlet scan above skips every `localhost/*` image, because a
locally built image is not discovered from a Quadlet's Image= line. The
python generator then RE-ADDS every localhost image declared in `core`;
this port never did, so every `localhost/*` entry of `[build.bake].core`
-- the mios-sys and mios-cuda bases and the webtools crawl4ai and
firecrawl images -- was dropped from the plan lists. Law 12: an image
missing from the plan is an image the bake does not carry.

<!-- mios-src:39848e63562c from tools/native/mios-bake-plan/src/main.rs:309-315 -->

### The SSOT exports map every other native consumer resolves...

The SSOT exports map every other native consumer resolves through, with the
process environment overlaid on top so an operator export still wins. The
overlay is explicit here rather than hidden inside the expander, so what
outranks what is visible and testable.

<!-- mios-src:7f2d2d02b6da from tools/native/mios-render-quadlets/src/main.rs:88-91 -->

### A non-empty `MIOS_*` in the process environment outranks...

A non-empty `MIOS_*` in the process environment outranks the SSOT, which is
how an operator override reaches the bake. Only `MIOS_*`: anything else is
not ours to substitute.

<!-- mios-src:0b673a8e75e4 from tools/native/mios-render-quadlets/src/main.rs:122-124 -->

### A unit declaring Environment=/EnvironmentFile= **in...

A unit declaring Environment=/EnvironmentFile= **in `[Service]`** owns its
Exec refs (task_977).

The section is the whole point. In a `.container` Quadlet,
`[Container] Environment=` becomes podman's `--env`: it populates the
environment INSIDE the container and has no bearing on how systemd expands
`${VAR}` in the generated unit's Exec lines. Only `[Service]` gives systemd
something to expand against. Scanning the file without regard to section
therefore granted runtime ownership on the strength of a directive that
cannot confer it -- six shipped units declare env solely in `[Container]`
(mios-k3s, mios-node, mios-guacamole, mios-otelcol, mios-forgejo-runner and
mios-llm-worker@), and each would have had any Exec placeholder left
unbaked, to be expanded at runtime from an environment that never contains
it. Latent only because none of the six currently carries a `${MIOS_*}` on a
runtime-ref directive; mios-k3s.container comes closest, and its placeholder
sits on `Image=`, which is not one.

Every runtime_ref_directive is an `Exec*`, and those live only in
`[Service]`, so requiring the declaration to share that section is enough.

<!-- mios-src:e1ce4f68ecfb from tools/native/mios-render-quadlets/src/main.rs:142-160 -->

### The env layer used to live inside the expander, reading...

The env layer used to live inside the expander, reading std::env::var
directly. Moving it to the caller is only safe if it still outranks the
SSOT, exercised here through the real predicate rather than simulated.

<!-- mios-src:4eeb51e2a63e from tools/native/mios-render-quadlets/src/main.rs:509-511 -->

### An alias carries its canonical key's value, EXCEPT that...

An alias carries its canonical key's value, EXCEPT that
image.sidecars.*_VERSION carries only the tag.

This split was removed once, on the grounds that it made
MIOS_ADGUARD_VERSION "latest" where globals.sh carried the full ref.
That traded a cosmetic disagreement on 22 unconsumed keys for a wrong
value on the one that IS consumed: automation/36-ceph-k3s.sh does
K3S_TAG="${MIOS_K3S_VERSION:-}" and then ${K3S_TAG/-k3s/+k3s}.
common.sh sources userenv.sh BEFORE globals.sh and globals.sh guards
its assignments, so the value comes from userenv.sh -- whose preferred
tier is this binary. Without the split a bake gets
docker.io/rancher/k3s:v1.36.3+k3s1 as a release tag (task_3363).

<!-- mios-src:a3bcbad8a235 from tools/native/mios-resolver/src/emit.rs:60-71 -->

### Resolve `${MIOS_*}` that one emitted value makes to...

Resolve `${MIOS_*}` that one emitted value makes to another.

Called by the emitters whose consumer CANNOT expand -- `emit_json` (the
resolved-environment view) and `emit_install_env` (systemd
`EnvironmentFile=` and podman `--env-file`). It is deliberately NOT called
by `build_exports_map`, because `emit_shell` and `emit_ps` render into bash
and PowerShell, which expand at source time: keeping the reference live
there is what makes an operator's pre-exported `MIOS_PORTS_AGENT_PIPE`
propagate into `MIOS_AI_ENDPOINT`. Baking in the shared builder would take
that property away from both generated globals files.

systemd `EnvironmentFile=` and podman `--env-file` have no such expansion,
so an emitted `MIOS_AI_ENDPOINT=http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1`
means two different things depending on who reads it. It is also why
`system-sync-env.sh` DROPPED that variable rather than emitting it: its
filter rejects any value containing `$` (task_997).

Expansion reads a snapshot, so the result does not depend on map order, and
a name that resolves to nothing is left verbatim rather than blanked -- the
caller can then report it instead of shipping an empty string.

<!-- mios-src:2dcb4aa18554 from tools/native/mios-resolver/src/emit.rs:88-107 -->

### After the [env] merge, so an [env] value can both reference...

After the [env] merge, so an [env] value can both reference an
exported key and be referenced by one. Unresolved values still carry
`$` and are dropped by the bare-safe filter below (Law 10) -- which is
exactly how MIOS_AI_ENDPOINT went missing before task_997.

<!-- mios-src:452a61ff2829 from tools/native/mios-resolver/src/emit_install_env.rs:33-36 -->

### shlex_quote single-quotes anything containing `$`, and bash...

shlex_quote single-quotes anything containing `$`, and bash does not
expand inside single quotes -- so a live ${MIOS_*} reference here is
exported as literal text, never as its value. Nor is there anything to
expand against: these lines are sorted alphabetically, not
topologically, so a referent may be defined after its referrer. Unlike
automation/lib/globals.sh, which splices and topologically sorts, this
binding also exports unconditionally, so it never offered the
"pre-exported value wins" property that a live reference would serve.
Resolving here is what makes userenv.sh's native tier agree with its
Python fallback.

<!-- mios-src:9afb7e775ad6 from tools/native/mios-resolver/src/emit_shell.rs:55-64 -->

### Size the deliverable from the INDEX blobs, not the...

Size the deliverable from the INDEX blobs, not the checkout.

`.gitattributes` checks `*.ps1` out as CRLF on every platform, so the
worktree carries line-ending expansion the commit does not contain. The
legibility check measures the same way; measuring differently here would
make the generated ceiling disagree with the gate that reads it.

<!-- mios-src:5e1c2a4843b2 from tools/native/mios-size-ceiling/src/main.rs:22-27 -->
### `mios.toml [profiles]` (ADR-0025): named selections over...

`mios.toml [profiles]` (ADR-0025): named selections over the pipeline's own
phases and package sections. Every error names what is wrong; nothing
resolves to a default selection.

<!-- mios-src:d8b9dea5501d from src/mios-rs/mios-build/src/lib.rs:949-951 -->

### Every KEY=VALUE on one unit line that could carry a...

Every KEY=VALUE on one unit line that could carry a credential.

`Environment=` is the declarative surface. `--env KEY=VALUE` on an Exec line
is the OTHER one, and scanning only the first measured the wrong property:
mios-agents.service hands its container a password as
`--env [redacted] on an ExecStart continuation, so a
plain literal planted there passed this gate at rc=0 while the identical
literal on an `Environment=` line failed it. Both controls were run.

Bare `-e` is deliberately NOT matched: it collides with ordinary flags such
as `bash -e`, and nothing in the corpus uses it to pass an environment pair.

<!-- mios-src:da27d54768a3 from src/mios-rs/mios-gate/src/credentials.rs:59-69 -->

### Replaced scripts check

Replaced scripts check: any script listed in `replaces` must NOT
exist on disk (per ADR-0021 / doc-rust-static-port.md: scripts are
deleted in the same commit that proves parity).

<!-- mios-src:ea100eff3936 from src/mios-rs/mios-gate/src/rust_categories.rs:251-253 -->

### Delegation counts

Delegation counts: everything in projections.rs hands ctx to
regen_and_diff, which does the reading. Eleven checks look blind without
this and are not.
Handing the root itself to a callee as an argument is the same delegation:
native_generator::command(&ctx.root, "pod-quadlets", true) runs the
generator's --check over that tree. A path built from it is not
(`ctx.root.join(` never matches "(ctx.root,"), so task_982 still holds.

<!-- mios-src:bfa8fae20d7e from src/mios-rs/mios-gate/src/stubs.rs:21-27 -->

### The resolved six-tier SSOT under `root` (vendor < vendor.d...

The resolved six-tier SSOT under `root` (vendor < vendor.d < host <
host.d < user < user.d), so an operator's [preflight] override is the
threshold probed. A missing vendor file is still a hard stop.

<!-- mios-src:67a3be483fb3 from src/mios-rs/mios-probe/src/probes.rs:7-9 -->

### The observer pane can exit or be closed by the operator....

The observer pane can exit or be closed by the operator. Every later step
joins/breaks it, so a dead id made each window-resized hook fail with
"can't find pane". Rebuild it in this head's storage session.

<!-- mios-src:2cc13590122b from tools/native/mios-agent-relay/src/main.rs:1247-1249 -->

### A recorded anchor pane can be killed (operator closed it...

A recorded anchor pane can be killed (operator closed it, storage session
reaped). Joining onto a dead anchor fails every resize with
"can't find pane"; forget it so it is rebuilt below.

<!-- mios-src:9e6b083d525b from tools/native/mios-agent-relay/src/main.rs:1417-1419 -->

### Peer-lane audit (2026-10-07): without a subject, `--check`...

Peer-lane audit (2026-10-07): without a subject, `--check` used to return
success having compared nothing. A check reads and compares a real artifact
or it fails; `--mock` only pins the metadata the render is built from.

<!-- mios-src:94b4c821298d from tools/native/mios-gen/src/fastfetch.rs:289-291 -->

### Reserved spec key

Reserved spec key: `[<kind>s.<name>.engine]` holds `select` -- a dotted SSOT path
whose string value names the engine -- and one overlay per engine,
`[<kind>s.<name>.engine.<engine>.<Section>]`. One lane spec therefore renders
whichever engine the SSOT selects (the heavy lane: vLLM or SGLang). After
selection the table is replaced by a scalar describing the choice, which the
renderer prints as a header comment and never as a section.

<!-- mios-src:2ebfb5025ac7 from tools/native/mios-gen/src/pod_quadlets.rs:780-785 -->

### Resolve `${MIOS_*}` that one emitted value makes to...

Resolve `${MIOS_*}` that one emitted value makes to another.

Called by the emitters whose consumer CANNOT expand -- `emit_json` (the
resolved-environment view) and `emit_install_env` (systemd
`EnvironmentFile=` and podman `--env-file`). It is deliberately NOT called
by `build_exports_map`, because `emit_shell` and `emit_ps` render into bash
and PowerShell, which expand at source time: keeping the reference live
there is what makes an operator's pre-exported `MIOS_PORTS_AGENT_PIPE`
propagate into `MIOS_AI_ENDPOINT`. Baking in the shared builder would take
that property away from both generated globals files.

systemd `EnvironmentFile=` and podman `--env-file` have no such expansion,
so an emitted `MIOS_AI_ENDPOINT=http://localhost:${MIOS_PORTS_AGENT_PIPE}/v1`
means two different things depending on who reads it. It is also why
`system-sync-env.sh` DROPPED that variable rather than emitting it: its
filter rejects any value containing `$` (task_997).

Expansion reads a snapshot, so the result does not depend on map order, and
a name that resolves to nothing is left verbatim rather than blanked -- the
caller can then report it instead of shipping an empty string.

<!-- mios-src:61df50208b7b from tools/native/mios-resolver/src/emit.rs:127-146 -->

### Parse `paths` lowest precedence first and fold them with...

Parse `paths` lowest precedence first and fold them with the MiOS overlay
rule (`merge::deep_merge`): an empty string never overrides a non-empty
value below it (Law 1). figment's own `merge` has no such rule, so stacking
the tiers as figment providers let `endpoint = ""` in /etc/mios erase the
vendor endpoint for every native reader while mios_toml.py kept it.
A layer that vanished between discovery and reading is skipped, as an
absent tier is; one that cannot be read or parsed is an error naming it.

<!-- mios-src:9d53fb20a545 from tools/native/mios-resolver/src/layers.rs:184-190 -->

### The merged layers as one figment provider, so callers that...

The merged layers as one figment provider, so callers that extract a typed
model or stack `Env` on top keep their API while the tiers merge by Law 1.

<!-- mios-src:7bc367795e06 from tools/native/mios-resolver/src/layers.rs:215-216 -->

### The resolved MIOS_* environment, ${MIOS_*} references...

The resolved MIOS_* environment, ${MIOS_*} references expanded -- the same
values `mios-resolver --emit=json` prints. Native programs read the SSOT
through this at run time instead of carrying their own copies of it.

<!-- mios-src:e95a086f9bcb from tools/native/mios-resolver/src/lib.rs:105-107 -->

### Run-time SSOT lookup for native programs

Run-time SSOT lookup for native programs: the process environment (a
unit's Environment=, the operator) wins, then the resolved six-tier
mios.toml. A name neither provides is an error that names it -- callers
never substitute a compiled-in value.

<!-- mios-src:c30439a4e081 from tools/native/mios-resolver/src/lib.rs:115-118 -->

### A table or array as TOML inline syntax, byte-equal to...

A table or array as TOML inline syntax, byte-equal to mios_toml.py's
_toml_inline: scalar and array keys sorted, then nested-table keys sorted;
a string with a backslash (and no quote or newline) as a literal string.
Rendered here rather than by the toml crate, whose inline layout changed
between releases (newer releases no longer put nested tables last), so the twin
held only in a workspace that happened to lock an older toml.

<!-- mios-src:c0c6529c00d4 from tools/native/mios-resolver/src/walk.rs:56-61 -->

### Transform a TOML value according to MiOS business rules: -...

Transform a TOML value according to MiOS business rules:
- Boolean -> "true" / "false"
- `ports.*` (except `ports.stack_id`) -> `int(v) + stack_offset` (unless port == 53)
- List/Array -> comma-separated string; table or array elements as
  [`toml_inline`]
- Scalar -> string representation

<!-- mios-src:d2d9c71b4fb0 from tools/native/mios-resolver/src/walk.rs:94-99 -->

### Render before publication. Replace legacy copies only when...

Render before publication. Replace legacy copies only when absent or marked
as MiOS-owned; save their exact old bytes before replacing them.
Returns the paths of pre-existing owned files, used to scope live reloads.

<!-- mios-src:17ec795e889f from tools/native/mios-service-core/src/host_tmux.rs:34-36 -->
### A token in a structured field (depends_on, epic, related)...

A token in a structured field (depends_on, epic, related), most specific first: a task's id, a former
id, an operator decision, the task whose title opens with it, then the banner whose range covers it
(so "QCAT-01" names the task titled "QCAT-01: ...", not the "QCAT-01..04" banner).

<!-- mios-src:ebdc2dcbe312 from tools/native/mios-task/src/purge.rs:222-224 -->

### Every token the migration resolves, with the typed id it...

Every token the migration resolves, with the typed id it becomes: task ids, former ids, operator
decisions, title names and the members of every range banner, in the order `structured` prefers them.
`--mapping-out` writes it outside the tree so a sibling repository can apply the same rewrite
(`mios-task rewrite-ids`); the tree itself keeps none of it.

<!-- mios-src:01764e2828d2 from tools/native/mios-task/src/purge.rs:815-818 -->

### `mios-task rewrite-ids --mapping FILE --root DIR`: the tree...

`mios-task rewrite-ids --mapping FILE --root DIR`: the tree rewrite of migrate-ids, from a mapping it wrote,
over a repository that has no task list of its own (mios-bootstrap mirrors MiOS files, Law 15).

<!-- mios-src:c268f4e070c0 from tools/native/mios-task/src/purge.rs:862-863 -->

### Publish an owned worktree migration with byte/mode backups....

Publish an owned worktree migration with byte/mode backups. Each replacement is atomic;
an ordinary write or final validation error restores all touched paths. Backups stay on disk
if restoration fails, so the error names recoverable evidence instead of claiming success.

<!-- mios-src:068a99d42b74 from tools/native/mios-task/src/purge.rs:1064-1066 -->
### Task ids after the purge (ADR-0028): the opaque...

Task ids after the purge (ADR-0028): the opaque task_<ULID>, the typed task_<number>, the legacy record
schema a list not yet migrated is read with, and the token scanner the rewrite and the old-id gate share.

<!-- mios-src:f3fbdd306c6b from tools/native/mios-task/src/record.rs:642-643 -->
### ADR-0030 counts generated files too. The old...

ADR-0030 counts generated files too. The old generated-budget number
measured a different set; compare the immutable baseline tree's TOTAL
instead. Never use the current tree or its proposed ceiling as the base.

<!-- mios-src:38472356d37f from src/mios-rs/mios-gate/src/ratchet.rs:270-272 -->
### ! mios-rdp-priv: the one root component of the native RDP...

! mios-rdp-priv: the one root component of the native RDP server (rdp-native-design.md 10/11).
! Socket-activated per connection (mios-rdp-priv.socket, Accept=yes, SOCK_SEQPACKET): the peer must be
! the operator uid ([remote.rdp].operator_uid). Requests (NUL-separated fields, one per message):
!   AUTH user password peer -> "OK" | "DENY <reason>"   PAM service "mios-rdp" (password-auth, so
!                                pam_faillock applies) + membership of group "mios-rdp"; uid 0 never
!   TLS-KEY                 -> "OK" + SCM_RIGHTS [cert.pem fd, key.pem fd] from [remote.rdp].cert_dir
!   FALLBACK reason         -> writes /run/mios/rdp/fallback and starts xrdp.service (no-block)
! The password is zeroised after use; every AUTH is logged with user, peer and result.
!
! Exception E2 of the static-binary policy: glibc + libpam (PAM modules are dlopen'd, NSS by design).
! Test mode (only when run by hand, never from the socket unit): `mios-rdp-priv --test-auth
! --service S --group G` reads "user\0password" on stdin and prints the decision.

<!-- mios-src:799eb80e4a1b from src/mios-rdp-server/crates/mios-rdp-priv/src/main.rs:1-12 -->

### ! Authentication (rdp-native-design.md 11.3): Client Info...

! Authentication (rdp-native-design.md 11.3): Client Info credentials are checked by the root
! helper mios-rdp-priv over a SOCK_SEQPACKET socket (PAM service "mios-rdp" + membership of the
! managed group "mios-rdp", uid 0 never admitted). Per-source exponential backoff sits on top of
! pam_faillock. The helper also hands over the TLS certificate and key as file descriptors, so the
! key never becomes readable by the operator account.
!
! Unsafe: AF_UNIX SOCK_SEQPACKET + SCM_RIGHTS FFI only.

<!-- mios-src:ee8209c223bf from src/mios-rdp-server/crates/mios-rdp-server/src/auth.rs:1-7 -->

### ! Capture of one Hyprland output with...

! Capture of one Hyprland output with ext-image-copy-capture-v1 into a ring of GBM dmabufs, GPU
! colour conversion (gpu.rs) and hand-over of the newest frame to the encoder (latest wins).
!
! Hyprland 0.56 blits each frame into our dmabuf on the GPU after it rendered the output, so
! there is no CPU readback on its main loop (the shm path stalled it 13-22 ms per frame). It
! completes a capture only when the output rendered, so an idle output costs nothing; it always
! reports full damage, so region damage is computed by the encoder against the last encoded frame.

<!-- mios-src:6bf46dc8c2ce from src/mios-rdp-server/crates/mios-rdp-server/src/capture.rs:1-7 -->

### ! Clipboard (MS-RDPECLIP cliprdr) <-> Wayland selection...

! Clipboard (MS-RDPECLIP cliprdr) <-> Wayland selection through ext-data-control-v1 (no focused
! surface needed). Text (CF_UNICODETEXT <-> text/plain;charset=utf-8), HTML ("HTML Format" <->
! text/html) and images (CF_DIB / registered "PNG" <-> image/png). Lazy both ways: data moves only
! when something pastes. Our own selections carry a marker MIME type and are never echoed back;
! offers marked x-kde-passwordManagerHint are never sent to the client; direction and size are
! SSOT keys ([remote.rdp.redirect].clipboard, clipboard_max_mb).

<!-- mios-src:e1de4f480b95 from src/mios-rdp-server/crates/mios-rdp-server/src/clip.rs:1-6 -->

### ! Client of the display service: `mios-rdp-rfb-bridge...

! Client of the display service: `mios-rdp-rfb-bridge --display-service`, run as a child with
! JSON lines on stdin/stdout. It owns the remote output policy the RFB path already proved
! (headless output, attach below the primary, [desktop.scaling] scale, refresh pacing, workspace
! rule, re-apply after a Hyprland config reload, cursor hand-back, device rules), so the native
! server and the xrdp fallback share one implementation.

<!-- mios-src:b3d6bca75937 from src/mios-rdp-server/crates/mios-rdp-server/src/display.rs:1-5 -->

### ! H.264 encoders behind one trait. Operator decision...

! H.264 encoders behind one trait. Operator decision 2026-10-17: Cisco OpenH264 (software, the
! binary from fedora-cisco-openh264, loaded with dlopen so its patent licence applies), made fast
! with slice threads and damage-aware encoding. No VA-API path is implemented or auto-selected;
! the trait keeps room for one if the operator asks for it later.
!
! Unsafe: the OpenH264 C ABI (vtable calls) only.

<!-- mios-src:b1e9f0496710 from src/mios-rdp-server/crates/mios-rdp-server/src/encode.rs:1-6 -->

### ! EGL (GBM platform, surfaceless, low-priority context) +...

! EGL (GBM platform, surfaceless, low-priority context) + GLES 3 colour conversion of captured
! dmabufs: one draw packs BT.709 I420 (Y plane, then per row [U row | V row]) into an RGBA8 target
! of (Wp/4) x (3H/2) texels, read back through a PBO (Wp*H*3/2 bytes, about 3.4 MB at 1100x2080).
! A second mode writes BGRA for the classic bitmap path (clients without EGFX).
!
! Unsafe: EGL/GL FFI only (rdp-native-design.md 12, exception E1).

<!-- mios-src:9864d9f99ff8 from src/mios-rdp-server/crates/mios-rdp-server/src/gpu.rs:1-6 -->

### ! Remote input into the live Hyprland seat (single seat...

! Remote input into the live Hyprland seat (single seat, rdp-native-design.md 5):
! keyboard through zwp_virtual_keyboard_v1 (xkb keymap of [remote.rdp].keyboard_layout, RDP set-1
! scancodes -> evdev), Unicode text through a second virtual keyboard whose keymap carries the
! needed keysyms (the wtype method), mouse through zwlr_virtual_pointer_v1 bound to the remote
! output (absolute motion can never land on another output), touch and pen through uinput
! devices that are mapped to the remote output (hl.device output = ...) BEFORE they exist.
! Nothing reaches the seat before authentication: the server only builds input after it.

<!-- mios-src:c86cf70da041 from src/mios-rdp-server/crates/mios-rdp-server/src/input.rs:1-7 -->

### ! mios-rdp-server: the MiOS native RDP server...

! mios-rdp-server: the MiOS native RDP server (rdp-native-design.md). IronRDP (default branch)
! for the protocol; MiOS glue for capture (ext-image-copy-capture -> GBM dmabuf -> GLES BT.709
! I420), Cisco OpenH264 (dlopen, slice threads), EGFX AVC420 with damage rects and frame-ack flow
! control, input (virtual pointer/keyboard, uinput touch/pen mapped to the remote output),
! clipboard (ext-data-control) and display control (the bridge's display service).
!
! Usage:
!   mios-rdp-server --config native.json          serve (normally via `mios-rdp native`)
!   mios-rdp-server --self-test [--output NAME]   JSON report: OpenH264, EGL/GBM, Wayland globals
!   mios-rdp-server --bench-encode WxH FRAMES     OpenH264 timing per thread count (synthetic)
!   mios-rdp-server --bench-capture NAME SECONDS  capture + GPU conversion timing on an output

<!-- mios-src:455a55009c2d from src/mios-rdp-server/crates/mios-rdp-server/src/main.rs:1-11 -->

### Latest-frame handoff for the classic bitmap path: the...

Latest-frame handoff for the classic bitmap path: the pipeline thread offers the newest frame,
the connection's update loop takes it. A slow client can never build a backlog here: an offer
displaces (returns) the previously pending frame, so at most one frame is ever queued and it is
always the newest one. Delivery is counted at the consumer side, not here.

<!-- mios-src:e2660085a73f from src/mios-rdp-server/crates/mios-rdp-server/src/pipeline.rs:73-76 -->

### Adaptive bitmap capture rate

Adaptive bitmap capture rate: the bitmap path has no frame acks, so the queue age of delivered
frames (capture ready -> handed to the connection) is the congestion signal. One step per stats
window: -20 % while the p50 queue age is above `slow_us`, +1 Hz once it is below half of it
(hysteresis), bounded to [floor, ceil]. A degenerate `slow_us` = 0 disables stepping entirely.

<!-- mios-src:e7245926751d from src/mios-rdp-server/crates/mios-rdp-server/src/pipeline.rs:407-410 -->

### ! One RDP connection at a time into the operator's live...

! One RDP connection at a time into the operator's live session: the IronRDP display/connection
! hooks, the accept loop (source allow-list before a byte is parsed, ConnectionPolicy Reject while a
! session is live, TCP keepalive + TCP_USER_TIMEOUT so a dead phone is dropped in ~15 s), and the
! per-session lifecycle (display service attach, capture, encode pipeline, input, stats).

<!-- mios-src:1f0c1c318a3b from src/mios-rdp-server/crates/mios-rdp-server/src/session.rs:1-4 -->

### 7. General Law 7 Scan across top-level string fields Empty...

7. General Law 7 Scan across top-level string fields
Empty vendor defaults are declared optional values, not Law 7 violations.
Derive their exact paths from the SSOT instead of another exemption list.

<!-- mios-src:bb1993e90fbb from src/mios-rs/mios-config/src/lib.rs:462-464 -->

### The unit files this tree owns. In a checkout that is what...

The unit files this tree owns.

In a checkout that is what git tracks, or would track (untracked but not
ignored, so a new MiOS unit is scanned before it is added). This repository
is also a live root: on a deployed host usr/lib/systemd/system holds every
RPM's units next to MiOS's, and the libvirt `virtsecretd.service` line
`VIRTSECRETD_ARGS="--timeout 120"` (SECRET inside the daemon's name) read as
a MiOS credential literal. Those files are ignored by .gitignore, which is
how the tree already says they are not its own. Outside a checkout (an
exported tree, a test fixture) every file under the unit directories is the
tree's own, as before.

<!-- mios-src:cb9157bca08e from src/mios-rs/mios-gate/src/credentials.rs:116-126 -->

### rule 7

rule 7: quickshell:/mios: actions are dispatched by a handler we ship; they must be implemented
(action_argv) or explicitly planned, so an unfinished action is visible instead of silently inert

<!-- mios-src:bc51fd0c7bc2 from src/mios-rs/mios-gate/src/keybinds.rs:364-365 -->

### D1: a realized_by row moved onto a chord a compositor-table...

D1: a realized_by row moved onto a chord a compositor-table entry holds for ANOTHER action is reported (the
realized-row exemption used to hide it, and the generator dropped the row's handle). Positive control: the
entry that realizes the row (same exec argv, same hypr: verb) is still one binding with no finding.

<!-- mios-src:7233949c3f78 from src/mios-rs/mios-gate/src/keybinds.rs:1078-1080 -->

### V1 (round-2 verifier): a realized `exec:<prog>` row on a...

V1 (round-2 verifier): a realized `exec:<prog>` row on a chord whose hyprland.lua literal merely mentions the
program (CTRL+ALT+SHIFT+m: the [keybindings.actions] system terminal) is a finding. The literal comes from no
compositor table, so only the Lua check sees it. Controls: the old any-word rule accepted it; the same row on a
free chord with its own dispatch handle, and the terminal row on its literal, are clean.

<!-- mios-src:8ad644c28a8c from src/mios-rs/mios-gate/src/keybinds.rs:1095-1098 -->

### (a) `tmux:key Space` (the menu key, bound by name, not in...

(a) `tmux:key Space` (the menu key, bound by name, not in `bindings`) is a valid client-exact route; a key
that is bound nowhere is still rejected.

<!-- mios-src:f651c4ee4067 from src/mios-rs/mios-gate/src/keybinds.rs:1395-1396 -->

### Bytes, not parsed JSON: a compact or reordered file that...

Bytes, not parsed JSON: a compact or reordered file that means the same
thing is still a hand edit the writer would not produce (Law 8).

<!-- mios-src:a3477b01aa0f from src/mios-rs/mios-gate/src/sigpolicy.rs:69-70 -->

### What the SSOT says the gate audits beyond MiOS's own native...

What the SSOT says the gate audits beyond MiOS's own native workspaces: the exceptions, and the
upstream install trees ([build.native.linux].audit_scopes) that are scanned in full whether or
not an exception covers them, so removing an exception makes its binaries fail instead of
silently leaving the scan.

<!-- mios-src:4a72445328b0 from src/mios-rs/mios-gate/src/static_linkage.rs:75-78 -->

### Exact generated assignments of SSOT values that carry a...

Exact generated assignments of SSOT values that carry a version literal.
Such a value belongs to the named SSOT key -- an upstream pin
([build.native.windows].driver_version) or an upstream release cited in
prose ([windows.xbox.research.sources].vive) -- not to the MiOS release
identity, and the SSOT itself is outside this check's scan. Only these two
generated files may use the assignments, and only byte-exact: a hand-changed
value, or the same text in authored glue, continues through the ordinary
literal check.

<!-- mios-src:856a389fc744 from src/mios-rs/mios-gate/src/version_literals.rs:145-152 -->

### Negative control

Negative control: demonstrates that using active_elements() instead of
replicable_elements() drops tombstones, causing the deleted key to persist
on the partitioned peer and failing convergence.

<!-- mios-src:bbc957fe9b37 from src/mios-rs/mios-node/src/state_sync.rs:541-543 -->

### Subscribe to every group reachable from group 0; returns...

Subscribe to every group reachable from group 0; returns the groups and subscribed ids.
`requested` receives each group id before its Start call is sent, so a caller whose future
is cancelled (snapshot timeout) or that sees an error still knows every group the app may
now hold a subscription for and can End it (End on a group never subscribed is a no-op).

<!-- mios-src:e21cc27bf2db from src/mios-rs/miosd/src/appmenu.rs:451-454 -->

### Hardware watchdog client. Opening a watchdog device ARMS...

Hardware watchdog client. Opening a watchdog device ARMS the board timer, so the device is
used only when explicitly configured (`--watchdog-dev`), the descriptor is held open for the
daemon's lifetime, and dropping the monitor disarms it. The former open/write/close per
tick left the SP5100 TCO armed and hard-reset the host 60 s after any short run (unit tests
in the privileged build container included).

<!-- mios-src:279ce379ac63 from src/mios-rs/miosd/src/daemon.rs:615-619 -->

### A display-mode change the operator has not seen on this...

A display-mode change the operator has not seen on this panel: keep the way back armed. Unless
`miosd framerate confirm-mode` answers within confirm_s, the previous mode comes back.

<!-- mios-src:e2ccbedc49fb from src/mios-rs/miosd/src/framerate.rs:855-856 -->

### Float (Hyprland 0.56 takes 'enable'/'disable'; any other...

Float (Hyprland 0.56 takes 'enable'/'disable'; any other action string toggles), size and
move; `tag` marks the window as managed so a later drag re-tiles it. Super+V floats pass no
tag: a window the operator floated stays floating wherever it is dragged.

<!-- mios-src:f42cc1ef3217 from src/mios-rs/miosd/src/placement.rs:508-510 -->

### Which kind of game a window is. Decided from what the...

Which kind of game a window is. Decided from what the client declares, never from its class or float state
alone: an XWayland window is Fixed when WM_NORMAL_HINTS min == max (Wine non-resizable, SDL without
RESIZABLE), and a transient (WM_TRANSIENT_FOR), modal or non-NORMAL-typed X11 window is a dialog, not a game,
even when its class matches (every Wine window's class is `<app>.exe`).

<!-- mios-src:f338dfbabebb from src/mios-rs/miosd/src/placement.rs:557-560 -->

### Geometry for a game window

Geometry for a game window: the focused output (the operator launched it there; the window's own monitor only
when no output reports focus), centred in its usable area, resizable games at the opening size and fixed-size
games at `fixed` (their declared size; [`mios_resolver::window_opening::game_geometry`]). `managed` tags a
resizable game so a drag tiles it like any placed window; fixed-size games are never tiled (that would resize
them). `pin` adds min_size/max_size = the placed size for a fixed one.

<!-- mios-src:d2fa67b10031 from src/mios-rs/miosd/src/placement.rs:633-637 -->

### Corrections per watched game over its lifetime (refused...

Corrections per watched game over its lifetime (refused remaps + size restores); then the game is left alone
instead of fought (another tool, or a game that insists on its mode).

<!-- mios-src:e4dd96da600f from src/mios-rs/miosd/src/placement.rs:694-695 -->

### A game's own geometry change that is refused: the XWayland...

A game's own geometry change that is refused: the XWayland root (the bounding box of every output, where
Wine/Proton put a borderless "fullscreen" window: recorded Forza Horizon 6 at 3840x1080+0+0 over two outputs)
or a size no single output can hold (wider or taller than every output). Position alone never makes a remap: a
box partly off-screen or straddling two outputs is where the operator (or the game) put it, and miosd cannot
tell who moved it (a keyboard move binding or a released drag leaves no trace), so it stands. Any other size a
game asks for (a windowed resolution change) is its own business. Outputs reporting no size are ignored.

<!-- mios-src:41b783dbb6af from src/mios-rs/miosd/src/placement.rs:718-723 -->

### Which kind of remap [`is_remap`] saw. A drag latch may...

Which kind of remap [`is_remap`] saw. A drag latch may excuse an oversize box (a drag-resize past every output)
but never the exact root: no operator drag lands within 2 px of the XWayland root, a game's remap does.

<!-- mios-src:4aeff04d1fe1 from src/mios-rs/miosd/src/placement.rs:728-729 -->

### A pointer button was down at a poll since the accepted...

A pointer button was down at a poll since the accepted geometry: the change is the operator's drag (or a
drag-resize), not refused as an oversize remap once released (the exact root is refused regardless).
Cleared whenever the accepted geometry is reset or taken anew.

<!-- mios-src:173244b0e94e from src/mios-rs/miosd/src/placement.rs:802-804 -->

### One poll of a watched game

One poll of a watched game: what to do about its current client JSON. `declared` is the current X11 min == max
size (None for Wayland or a client that dropped the hints), `held` whether a pointer button is down (a drag is
never fought), `settle` how long a fixed game's size may disagree with its declaration before it is restored
(Wine re-issues the hints right after the ConfigureNotify of a resize it asked for).

<!-- mios-src:fed3c256379a from src/mios-rs/miosd/src/placement.rs:820-823 -->

### Bounds on remote discovery. The selector runs with the...

Bounds on remote discovery. The selector runs with the terminal in raw mode, so no peer
query may block it: [terminal.sessions] has no dedicated overall-deadline key, so the
deadline for the whole tailnet sweep is ssh_timeout_s (the SSOT connect timeout) plus
PEER_DEADLINE_GRACE for the remote `tmux list-sessions` round trip. Every ssh child still
running at the deadline is killed (its whole process group) and reported as timed out.

<!-- mios-src:eec5e7cc20ee from src/mios-rs/miosd/src/sessions.rs:29-33 -->

### Which launching pane (index, process id) a new main window...

Which launching pane (index, process id) a new main window of process `pid` belongs to: the pane whose
process it is, else -- when that process runs in the supervisor's own unit (`ours`: Wine may hand the
window to another process it started) -- the only launching pane. A ShaderGlass the operator runs
outside the supervisor is never taken.

<!-- mios-src:8ea7ffa3d046 from src/mios-rs/miosd/src/shaderglass.rs:918-921 -->

### A nested session's compositor is not the one the user...

A nested session's compositor is not the one the user manager's portal serves: never import its
environment into, or restart a service of, the operator's session.

<!-- mios-src:6740eab3ea2b from src/mios-rs/miosd/src/shaderglass.rs:992-993 -->

### Whether a pane whose ShaderGlass exited with a failure is...

Whether a pane whose ShaderGlass exited with a failure is relaunched: only when nobody asked for the close
(a crash, an OOM kill, a killed Wine), the pane had mapped (it worked before), the relaunch budget is not
spent and the supervisor is not stopping. A clean exit (File > Exit) is the operator's choice.

<!-- mios-src:8e74197d1394 from src/mios-rs/miosd/src/shaderglass.rs:1251-1253 -->

### Float and pin a pane the static rules missed. The rules...

Float and pin a pane the static rules missed. The rules match the title, and winewayland maps the window
before ShaderGlass titles it, so under the Wayland driver the pane mapped tiled: the layout then owned its
geometry, every placement was ignored and WineCap's client fit fought the tile forever. Empty when the
pane already floats and is pinned.
Whether to (re-)send the float/pin fix to a pane that still reports otherwise: once the last one had the
settle time to land, and at most [`follow::PLACE_RETRIES`] times until the pane reports floating and pinned
(then the budget starts over) -- a pane something else keeps tiling is never re-sent the fix every tick.

<!-- mios-src:b86095f6cfa4 from src/mios-rs/miosd/src/shaderglass.rs:1337-1343 -->

### Save pane `i`'s live ShaderGlass state as its class profile...

Save pane `i`'s live ShaderGlass state as its class profile (WineCap runs "Set as default"; the
operator's own default is put back). Needs a mapped pane whose WineCap has the command channel.

<!-- mios-src:a898b31ad9c6 from src/mios-rs/miosd/src/shaderglass.rs:1534-1535 -->

### The class profile this pane starts with (ShaderGlass loads...

The class profile this pane starts with (ShaderGlass loads its last argument as a profile, after its
default): what the operator last chose for this class, in any session and presentation mode.
Without one, a snapshot of the operator's default taken while no save swaps it ([`state::launch_profile`]).

<!-- mios-src:95fd0bccf166 from src/mios-rs/miosd/src/shaderglass.rs:2056-2058 -->

### A window-level `window-size largest|smallest` overrides the...

A window-level `window-size largest|smallest` overrides the global
`latest` and pins the window to the wrong client (desktop vs phone).
Re-assert `latest`, then re-read the size so this pass lays out for the
client the operator is actually using (resize, rotation, client switch).

<!-- mios-src:6767bb49643a from tools/native/mios-agent-relay/src/main.rs:2795-2798 -->

### MiOS

MiOS: exactly one observer per head. A failed liveness probe (tmux busy,
timeout) used to create another "MiOS AI Agents" window on every
client-resized hook while the old ones kept running, so resizing forked
unbounded observer TUIs (each polling git/agents) and pegged the CPU.
Reuse a live pane already tagged for this head -- preferring the one shown
in the head window, then the recorded one -- and close the duplicates.

<!-- mios-src:62e20cfcdd32 from tools/native/mios-agent-relay/src/main.rs:2818-2823 -->

### The workspace keys (`prefix o` / `prefix f`)...

The workspace keys (`prefix o` / `prefix f`): `--workspace-focus next|%N` and `--workspace-view toggle|compact|auto`
(port of mios-mcp-server `--workspace-focus`/`--workspace-view`). One process per key press, no interpreter, and the
pass records the size it laid out so the hooks its reshaping fires stay no-ops. Outside a managed workspace, focus
cycles the window's panes; view says the pane is not in one.

<!-- mios-src:84619380dab7 from tools/native/mios-agent-relay/src/workspace_hook.rs:225-228 -->

### Emptying is legitimate when the tier held only...

Emptying is legitimate when the tier held only [keybindings] (restore defaults); never when it held anything
else (defence in depth: the invariant above already proves those tables are unchanged).

<!-- mios-src:3a604c92c66a from tools/native/mios-controls/src/apply.rs:32-33 -->

### A vendor/host system-layer button cannot be removed from a...

A vendor/host system-layer button cannot be removed from a higher tier (tables merge per key and an empty
string never shadows a value below it); report that instead of writing an inert override.

<!-- mios-src:92f012b14973 from tools/native/mios-controls/src/model.rs:170-171 -->

### Every connected output already has an operator layout (e.g....

Every connected output already has an operator layout (e.g. saved at setup by
another user or the admin): nothing to ask, just remember that this user was set up.

<!-- mios-src:09b36eab3596 from tools/native/mios-displays/src/main.rs:859-860 -->

### An operator's explicit scale is kept, but on Hyprland's...

An operator's explicit scale is kept, but on Hyprland's terms: snapped onto the 1/120 grid
and down to the nearest scale with an integral logical size (Hyprland refuses any other
and substitutes its own), so `scale` and `scale_120` always describe the same value.

<!-- mios-src:a1e65be4897d from tools/native/mios-gen/src/display_scale.rs:236-238 -->

### Whether a literal compositor `dispatcher` runs `binding`'s...

Whether a literal compositor `dispatcher` runs `binding`'s own action, i.e. the literal bind IS the row's
realization. The rule is shared with the gate (mios_resolver::keybindings::lua_dispatcher_realizes): exactly the
action's [keybindings.global.action_argv], `exec:mios-terminal` = the SSOT terminal action's command, any other
`exec:<prog>` = `prog` alone, and the native dispatchers of the hypr: verbs. A literal that merely mentions the
program (`alacritty -e ... --action system` for `exec:alacritty`) is another action (V1).

<!-- mios-src:75087dcad7fb from tools/native/mios-gen/src/global_bindings.rs:57-61 -->

### C-22

C-22: the literal bind on this chord realizes the row -> no second handle. A literal that runs a different
action would make the row dead (no handle of its own) and hide the collision, so it is an error (D1).
A second, foreign literal beside the realizing one is a collision the gate reports (keybind-collisions);
the row itself is live through its realization.

<!-- mios-src:5ac74f5a395e from tools/native/mios-gen/src/global_bindings.rs:135-138 -->

### Negative control for C-36

Negative control for C-36: with bypass_mod moved off SUPER+CTRL, the same session-scale handle is no longer
an escape, so the property follows the SSOT and is not hard-coded.

<!-- mios-src:6ed5574068be from tools/native/mios-gen/src/global_bindings.rs:587-588 -->

### D1: a realized_by row moved onto a chord the compositor...

D1: a realized_by row moved onto a chord the compositor binds literally for ANOTHER action is an error (it used
to be silently dropped: no dispatch bind, no finding, a dead row). Controls: the vendor projection renders, the
literal that IS the row's realization (same argv) still suppresses the second handle, and an exec: row is
realized by a literal running its program.

<!-- mios-src:c972e733803a from tools/native/mios-gen/src/global_bindings.rs:687-690 -->

### V1 (round-2 verifier): an `exec:<prog>` row moved onto a...

V1 (round-2 verifier): an `exec:<prog>` row moved onto a chord whose literal merely mentions the program among
its words was taken as realized -> no dispatch handle, a dead row, and a key that runs another action. Every
probe the verifier ran goes through the FULL projection (the literals come from [keybindings.actions], the
terminal and launcher keys, not the compositor tables). Positive control: the same row on a free chord gets its
own dispatch handle, and the shipped terminal row stays realized by its literal (no second handle).

<!-- mios-src:fe2dc3dd2e10 from tools/native/mios-gen/src/global_bindings.rs:717-721 -->

### Verifier observation 2

Verifier observation 2: each dispatch row used to declare two section-level locals (d, b), halving the 200-local
budget of the global-input closure. Rows are scoped in do ... end now: 250 rows load in real Lua. Negative
control: the same rows without the scope exceed the limit.

<!-- mios-src:3ff15874e5fc from tools/native/mios-gen/src/global_bindings.rs:745-747 -->

### The toggle overlay

The toggle overlay: plan the target output and add the windows keys the SSOT config does not set explicitly.
An explicit `[config.windows].window_scale` is an operator-fixed scale: pixel_exact is then not sent.
Returns the plan (None: the policy is off, or no output was found).

<!-- mios-src:d763f9e3fc01 from tools/native/mios-gen/src/hypr3d_spawn.rs:417-419 -->

### A Wayland backend configure changes the output size without...

A Wayland backend configure changes the output size without rebuilding its
workspace workarea. Keep the native monitor rule on the actual host viewport;
its prop refresh then recalculates tiled clients, without an initial-mode reset.
scale_mode "fit" keeps the session's logical desktop at width x height whatever the viewport (a PiP shows the
whole desktop scaled down, an enlarged window scales it up); "native" maps viewport pixels 1:1.

<!-- mios-src:493735a25ace from tools/native/mios-gen/src/nested_session.rs:69-73 -->

### A UNIX socket path holds at most 107 bytes. The session's...

A UNIX socket path holds at most 107 bytes. The session's compositor listens at <runtime>/hypr/<signature>/.socket2.sock
(a signature is under 40 bytes) and its tmux at <runtime>/tmux/tmux-<uid>/<socket>, so a session inside a nested
session must not live under that session's own runtime. Returns (runtime, link): the runtime is
<parent>/mios-nested-desktop-<pid> when that keeps every socket in budget, else the same name under the outermost
runtime (the parent path minus its nested-session components), with a link at <parent>/mios-nested-desktop-<pid> so
the parent's tools (session-scale, tests) still find the session there.

<!-- mios-src:eed6bfcfcf8c from tools/native/mios-gen/src/nested_session.rs:184-189 -->

### A preset's output belongs to the targets that use one: a...

A preset's output belongs to the targets that use one: a CLI target change (e.g. the preset's output seat opened
in the room for once) drops it instead of refusing; an explicit --output on such a target is still an error.

<!-- mios-src:fb9d882a5abd from tools/native/mios-gen/src/nested_session.rs:221-222 -->

### Mask real evdev nodes in this process tree and expose only...

Mask real evdev nodes in this process tree and expose only the assigned nodes. A private /tmp (labelled seats by
default) isolates every plugin state file and the legacy room packet as a whole; with the shared /tmp, those get a
per-file mask instead (a mask over a file the host replaces by rename is detached by that rename, see private_tmp).

<!-- mios-src:c9488968f878 from tools/native/mios-gen/src/nested_session.rs:390-392 -->

### XWayland

XWayland: inside bwrap's user namespace the host's root-owned /tmp/.X11-unix reads as uid 65534 and Hyprland refuses
it ("Failed to find a suitable socket for XWayland"), so a nested session had no X11 at all (no Steam/Proton/Wine/
SDL-X11 game). With the shared /tmp an owned 0700 directory takes its place in this process tree only (display numbers
stay unique through the shared /tmp/.X<n>-lock files); a private /tmp carries its own owned .X11-unix. Clients outside
the tree use session.json "x11_display" (DISPLAY=unix:<socket>).

<!-- mios-src:f522fe37f186 from tools/native/mios-gen/src/nested_session.rs:394-398 -->

### [desktop.shaderglass] (`miosd shaderglass`: ShaderGlass...

[desktop.shaderglass] (`miosd shaderglass`: ShaderGlass under Wine as a pinned, click-through,
Hyprglass-decorated live clone over one target window) -> its window rules and keybinds. Absent or
enabled != true: nothing. One mios_section, so a compositor that rejects a rule keeps the session.
Rules, all scoped to the anchored ShaderGlass class/title patterns or the pane's tag:
- the pane: float + pin (pinned floating windows draw after every workspace window and open special
  workspace, so the pane sits above every window and glass pane and below the shell layers);
  no_initial_focus, focus_on_activate = false and suppress_event (never steals focus, never goes
  fullscreen/maximized); no_anim (follows its target without the move animation); its pane tag; and,
  only with exclude_from_captures, no_screen_share (Hyprland 0.56 blacks it out of output captures);
- the glass preset tag: the desktop's one preset ([desktop.hyprland.hyprglass.config].default_preset),
  while Hyprglass is enabled;
- passthrough: windows carrying passthrough_tag get no_focus (hit-testing skips them, so pointer and
  keyboard reach the target underneath); miosd adds the tag to an overlay pane and drops it in setup;
- the tool windows (shader browser, parameters): float, centred and pinned, above the pinned pane.

Self-capture never happens by construction: the pane captures one target window (xdph window capture
renders only that window) and miosd refuses a ShaderGlass window as a target.

<!-- mios-src:d9828f51f8ee from tools/native/mios-gen/src/render.rs:2825-2841 -->

### Per-output monitor rules from...

Per-output monitor rules from [desktop.hyprland.monitors.<output>] (the table
mios-displays saves into a mios.d drop-in). Each entry becomes
`hl.monitor({output = "<output>", ...})` after the catch-all
[desktop.hyprland.monitor] rule, so a named output overrides it. `primary = true`
is not a monitor-rule field: it is returned separately and projects to
cursor.default_monitor. An absent table keeps the single catch-all rule.

<!-- mios-src:8659167074f8 from tools/native/mios-gen/src/render.rs:3065-3070 -->

### The session entry (usr/share/mios/hyprland/session.lua...

The session entry (usr/share/mios/hyprland/session.lua, also the skel user entry). It must
never leave an empty session: when the projection fails (mios-gen error, bad SSOT layer,
non-Lua output) or the chunk errors part way, it falls back to a minimal built-in desktop --
terminal and launcher binds, mios-desktop.target start/stop -- for whatever the chunk did not
already register, and surfaces the reason on stderr and as a compositor notification.

<!-- mios-src:7646a5e6c2b5 from tools/native/mios-gen/src/render.rs:3157-3161 -->

### Session lifecycle first

Session lifecycle first: the hyprland.start hook is what starts mios-desktop.target (shell,
portals, agents), so nothing later in the chunk -- a rejected config key, a plugin that
fails to load -- can take it down with it. session.lua reads MIOS_SESSION.hooks/.binds to
decide what its minimal fallback still has to provide.

<!-- mios-src:b8c563d28b30 from tools/native/mios-gen/src/render.rs:3313-3316 -->

### The controller's virtual keyboard (key: projection) speaks...

The controller's virtual keyboard (key: projection) speaks US keycodes: pin its layout whatever the
operator's kb_layout is, so controller L3 is Ctrl+T on a French keyboard too (C-31).

<!-- mios-src:c217b5933f46 from tools/native/mios-gen/src/render.rs:3410-3411 -->

### Outputs whose scale the operator fixed

Outputs whose scale the operator fixed: [desktop.hyprland.monitors.<output>].scale (the
mios-displays save, e.g. the host tier) and a numeric [remote.rdp].virtual_scale for the
remote display. The policy never auto-scales these; the saved value is their scale.

<!-- mios-src:2057340e9285 from tools/native/mios-gen/src/render.rs:4328-4330 -->

### The shipped SSOT keeps it OFF

The shipped SSOT keeps it OFF: remote and desktop windows and layers use the SAME glass (operator
2026-10-16, "Unify the designs"); a vendor projection that tags any window with another preset fails.

<!-- mios-src:cfbe4737c898 from tools/native/mios-gen/src/render.rs:4773-4774 -->

### Magic lamp ([desktop.hyprland.lamp]) -- lamp lane, additive...

---- Magic lamp ([desktop.hyprland.lamp]) -- lamp lane, additive ---------------------------------------------
Self-contained like mobile_profile: `project` calls it once, right after the mios-wobbly section, because the
lamp lives in that plugin (one compositor mesh backend). It touches no other section and reuses only `lua`.
The plugin registers plugin:mios_lamp:* and hl.plugin.mios_lamp only once Hyprland has loaded it (the config
is evaluated again after plugin loads), so the body waits for hl.plugin.mios_lamp exactly like the hyprbars
section waits for hl.plugin.hyprbars. Precedence rules key off the plugin's anim_tag: a lamp-launched window gets
HyprWindowShade's windowshade_open shader (pass-through) instead of the global open effect, and no hyprbars tab
while the lamp runs (hyprbars draws it rigidly at the real window box, outside the deformed pass).

<!-- mios-src:f66ab0a36ba7 from tools/native/mios-gen/src/render.rs:4839-4846 -->

### `local mios_rate` for every section after it (hyprglass...

`local mios_rate` for every section after it (hyprglass reads mios_rate.glass_fps) plus the compositor cap.
The running tier comes from miosd's state file when its writer still runs (a config reload keeps the live rates
and the modes miosd switched outputs to, so nothing bounces), else from the login plan computed here (`login`:
tuned-ppd base profile, sysfs battery, SSOT unlocked; never gaming at login). The unlocked tier caps nothing and
derives G and W from the local outputs' largest advertised refresh (none known yet at startup: 0 = every output
frame until miosd applies).

<!-- mios-src:9622e1be3dce from tools/native/mios-gen/src/render.rs:5185-5190 -->

### `[desktop.hyprland.hypr3d.config].glass_preset`: the...

`[desktop.hyprland.hypr3d.config].glass_preset`: the hyprglass preset `name` resolved the way hyprglass
resolves it (PluginConfig.cpp resolvePreset*): the default_theme table, the preset, its `inherits` chain
(SSOT presets, then hyprglass' built-in `glass`), hyprglass' config (theme table, then global), then
hyprglass' defaults. Projected as Hypr3D's glass table: every material key, the tint/fresnel/bevel colours
(0xRRGGBBAA), the frost (blur_sigma = 4 x blur_strength x sqrt(blur_iterations): hyprglass' kernel radius
blur_strength x 12 px, sigma radius / 3, blur_iterations H+V passes) and the edge pass (edge_pass,
edge_pass_width from [desktop.hyprland.hyprglass.config]).

<!-- mios-src:199b36c31b6a from tools/native/mios-gen/src/render.rs:5321-5327 -->

### Reviewed MiOS patches from the tree, applied in SSOT order....

Reviewed MiOS patches from the tree, applied in SSOT order. A patch that no longer applies to
the upstream commit fails the build naming the patch and the commit it was tried against.

<!-- mios-src:ca1878dbe606 from tools/native/mios-gen/src/render.rs:7126-7127 -->

### Policy (operator, 2026-10-16): latest upstream, fail...

Policy (operator, 2026-10-16): latest upstream, fail loudly. The SSOT names the
repository only; the build takes its default-branch HEAD, logs the resolved commit,
and the reviewed MiOS patches below are the drift gate.

<!-- mios-src:aa629014029d from tools/native/mios-gen/src/render.rs:7401-7403 -->

### Latest upstream that builds against the INSTALLED...

Latest upstream that builds against the INSTALLED compositor: a plugin repository that
publishes hyprpm.toml commit_pins (Hyprland commit -> plugin commit, the map hyprpm itself
uses; HEAD follows hyprland-git) is moved to the entry for the installed Hyprland release,
whose commit is resolved now from its tag. No hyprpm.toml or no entry keeps HEAD. Nothing is
pinned in the SSOT: the resolved commit is only logged.

<!-- mios-src:a50a099fcf10 from tools/native/mios-gen/src/render.rs:7418-7422 -->

### Inside a nested session (its runtime directory is the...

Inside a nested session (its runtime directory is the session's own): zoom this session; never persist a
nested output's scale into the operator's user tier, which nested sessions share.

<!-- mios-src:d3b9b1e436a1 from tools/native/mios-gen/src/session_scale.rs:202-203 -->

### devtmpfs publishes before udev sets the operator's...

devtmpfs publishes before udev sets the operator's input-group
access. The fixture must wait for usable permissions, not only
node existence (root-only tests otherwise hide this race).

<!-- mios-src:a0ede9a436eb from tools/native/mios-inputd/src/linux.rs:447-449 -->

### Targets for all PiPs on one monitor (same order as input)....

Targets for all PiPs on one monitor (same order as input). `pips` carry each PiP's current POSITION and its
NATURAL size: the size the operator (or the client) chose, never a size the plan squeezed it to. Clamping, the size
magnets and the overfull squeeze always start from the natural size, so a squeezed width can never drift into a
magnet and snap a settled layout on a later tick. Only positions are iterated, to their exact fixed point (stacking
and rail choice depend on the planned positions); passes that cycle return one canonical member of the cycle, which
every member re-plans to. Output is integer geometry, so re-planning the applied output with the same natural sizes
reproduces it exactly.

<!-- mios-src:9bc201a56582 from tools/native/mios-pip-gravity/src/gravity.rs:97-103 -->

### One shared top-to-bottom sequence (operator order) at a...

One shared top-to-bottom sequence (operator order) at a uniform scale, on each PiP's configured rail and
clear of every HUD bar that crosses either flank column.

<!-- mios-src:a359e1dc9603 from tools/native/mios-pip-gravity/src/gravity.rs:218-219 -->

### Keys added after the first shipped [desktop.pip_gravity]...

Keys added after the first shipped [desktop.pip_gravity] table. An SSOT without them (the binary installed before
the SSOT that introduced them) turns that one feature off -- exactly the behaviour before the key existed -- instead of
refusing the whole table (which failed `ExecStartPre=check` and every nested PiP launch). A present but invalid value
is still rejected.

<!-- mios-src:5495b8151446 from tools/native/mios-pip-gravity/src/main.rs:54-57 -->

### The size the operator (or the client) chose: planning input...

The size the operator (or the client) chose: planning input for clamping, size magnets and the squeeze. Only a
resize that is not ours updates it, so a squeezed size never feeds back into the plan (see gravity::plan).

<!-- mios-src:dddff65f26f4 from tools/native/mios-pip-gravity/src/main.rs:193-194 -->

### The last frames this daemon dispatched (newest last). A...

The last frames this daemon dispatched (newest last). A busy compositor applies a frame a tick or more late, so
a window showing ANY of them is ours, not an operator resize (that misread adopted animation frames as natural
sizes and shrank PiPs step by step). They expire FRAME_GRACE after the newest dispatch: a stale frame list made a
much later operator resize to one of those sizes count as ours, so the next tick re-animated it back.

<!-- mios-src:51f794bc2add from tools/native/mios-pip-gravity/src/main.rs:196-199 -->

### Room ownership of one PiP. While the room is active, and...

Room ownership of one PiP. While the room is active, and after it closed until the window has been geometrically
still for `settle_ms` (Hypr3D's close keeps moving and re-rastering windows for many ticks), every move and resize
is the room's: the natural size is never updated, nothing is dispatched, and the PiP plans from its kept flat slot.
Only then does flat placement resume, restoring the kept slot and natural size at once.

<!-- mios-src:260c12054cba from tools/native/mios-pip-gravity/src/main.rs:212-215 -->

### Any maximized/fullscreen window on this output's visible...

Any maximized/fullscreen window on this output's visible workspace: the HUDs auto-hide and the reserved
area collapses, so PiPs keep the last ordinary bounds instead of jumping into the freed space and back.

<!-- mios-src:3acc416e8dd4 from tools/native/mios-pip-gravity/src/main.rs:589-590 -->

### The verifier's live2 trace: after `room active=false`...

The verifier's live2 trace: after `room active=false` Hypr3D's close kept moving and resizing the window for twelve
ticks (218x103 -> 264x125 while it flew back), and the old release on the first flat tick took each frame for an
operator resize.

<!-- mios-src:f40eaa4b1f09 from tools/native/mios-pip-gravity/src/main.rs:663-665 -->

### The bounds PiPs on `monitor` plan against. `fullscreen`: a...

The bounds PiPs on `monitor` plan against. `fullscreen`: a window on the output's visible workspace is
maximized or fullscreen. A changed output geometry always drops the latch (the old bounds no longer fit).

<!-- mios-src:eeb4f7cc9d1a from tools/native/mios-pip-gravity/src/matrix.rs:29-30 -->

### Native stay-on-top. Hyprland draws pinned floating windows...

Native stay-on-top. Hyprland draws pinned floating windows after, and hit-tests them before, every unpinned window
(fullscreen and special workspaces included), so a managed PiP needs only (a) its pin and (b) to sit above the other
PINNED windows that overlap it. The client list is the native z-order (bottom first). Returns the indices of
floating, non-fullscreen managed PiPs that lost their pin, and of pinned PiPs covered by an overlapping pinned
non-PiP window. Unpinned windows never cause a raise (that only churned the z-order and the pointer focus).

<!-- mios-src:8bf9dc32e490 from tools/native/mios-pip-gravity/src/window.rs:62-66 -->

### `base` slowed to `target_hz` by adding blank lines to one...

`base` slowed to `target_hz` by adding blank lines to one vertical porch only (the sync pulse keeps its length;
the pixel clock, the horizontal timing and so the line rate are unchanged). `tolerance_hz` bounds the rounding
error; `limits` (the panel's range descriptor) must contain the target. A line count can only grow, so the result
is never faster than `base`.

<!-- mios-src:3ac118b1a573 from tools/native/mios-resolver/src/edid_timing.rs:289-292 -->

### The extended-vblank MODELINE for Hyprland

The extended-vblank MODELINE for Hyprland: `base` with its clock truncated to whole MHz first (the only clocks a
Hyprland 0.56 modeline can carry, see `Timing::whole_mhz`), then slowed to `target_hz` through the front porch. The
line rate drops by under 1 MHz / htotal (DP-3: 161.566 -> 161.479 kHz, -0.05 %); the refresh is what Hyprland
programs and reports (DP-3 at 90: 332 MHz, vtotal 1794, 90.010 Hz). The clock never rises, so a panel's maximum
pixel clock still holds.

<!-- mios-src:a711076f4b3f from tools/native/mios-resolver/src/edid_timing.rs:318-322 -->

### A verified modeset to a mode the operator has not confirmed...

A verified modeset to a mode the operator has not confirmed on that panel is restored after this many seconds
unless `miosd framerate confirm-mode` confirms it (0 = no confirmation; the baseline mode never needs one).

<!-- mios-src:f5f26e3339f4 from tools/native/mios-resolver/src/framerate.rs:84-85 -->

### The rates of `tier` for outputs whose largest refresh is...

The rates of `tier` for outputs whose largest refresh is `max_hz` (None = unknown). D is the tier's rate capped
at the maximum; the unlocked tier (desktop_hz 0) is the maximum itself and caps nothing. G = D / glass_divisor
and W = D / wallpaper_divisor, rounded down. With an unknown maximum the unlocked tier reports 0 for D, G and W:
"every output frame" (the consumers' uncapped value) until the outputs are known.

<!-- mios-src:21c863de4e0f from tools/native/mios-resolver/src/framerate.rs:330-333 -->

### The compositor holds the wallpaper's frame callbacks to...

The compositor holds the wallpaper's frame callbacks to `wallpaper_fps` (mios-pacing wallpaper_fps set and its
frame hook live): Host.qml then steps once per presented frame instead of on its own timer, so each commit
carries a new step (Qt's layer update otherwise commits every step twice).

<!-- mios-src:5f5543a0d5ca from tools/native/mios-resolver/src/framerate.rs:518-520 -->

### D1: when a literal compositor bind IS a global row's...

---- D1: when a literal compositor bind IS a global row's realization -----------------------------------------------
One rule for mios-gen (render-hyprland suppresses the row's dispatch handle) and the gate (a realized_by row on a
chord the compositor holds for anything else is a finding). A literal realizes a row only when it runs exactly what
mios-inputd would run for the row's action; sharing a word with it (`alacritty -e ... --action system` for
`exec:alacritty`) is another action, and the row would be dead.

<!-- mios-src:ffdb1decfba5 from tools/native/mios-resolver/src/keybindings.rs:150-154 -->

### [desktop.hyprland.placement.games]: game windows...

[desktop.hyprland.placement.games]: game windows (Steam/Proton/Wine/SDL/gamescope) open on the
focused output, centred; resizable ones at the opening size. Two compositor rules carry it: one for
windows Hyprland did not float on its own (resizable: sized like every window) and one that only tags
the non-modal windows it floated. miosd placement decides what those are from what the client declares
(X11 WM_NORMAL_HINTS min == max is a fixed-size game that keeps its size, since a game that never
recreates its swapchain shows black wherever the compositor grows it; WM_TRANSIENT_FOR, modal or a
dialog window type is a dialog and is left alone) and refuses a game's root/oversize remaps.

<!-- mios-src:75dbe6ee6c52 from tools/native/mios-resolver/src/lib.rs:541-547 -->

### miosd refuses an X11 game's own remap to the XWayland root...

miosd refuses an X11 game's own remap to the XWayland root or to a size no single output can hold (puts
it back at the accepted geometry); every other size and every position (partly off-screen, straddling
two outputs: the operator's moves look the same) stands.

<!-- mios-src:0152f3b1631e from tools/native/mios-resolver/src/lib.rs:552-554 -->

### The compositor rules, as tables for `hl.window_rule`....

The compositor rules, as tables for `hl.window_rule`. `size` is the opening size expression
(the same one the general placement rule carries). Emitted after the general rule and before the
SSOT window_rules, so PiP/dialog rules still override them. No rule suppresses ConfigureRequests
(that froze a game's own resolution changes) and the floated-window rule neither centres nor sizes:
it only tags candidates for miosd, which tells a fixed-size game from a dialog.

<!-- mios-src:347636fe65ae from tools/native/mios-resolver/src/lib.rs:619-623 -->

### Where a game window goes inside its output. `usable` is the...

Where a game window goes inside its output. `usable` is the box left after reserved bars and safe
insets, `output` the whole logical output (both x, y, w, h). A resizable game takes `policy` (the
opening geometry); a fixed-size one keeps (w, h): centred in the usable area when it fits, else centred
on the whole output when it fits there, else `oversize` decides. Returns (x, y, w, h, resized).

<!-- mios-src:b7efe3dd70c3 from tools/native/mios-resolver/src/lib.rs:654-657 -->

### ! AI-hint: Pixel side of mios-shot: reads captured shm...

! AI-hint: Pixel side of mios-shot: reads captured shm buffers (XRGB/ARGB/XBGR/ABGR 8888 and the 2101010 variants,
! down-converted to 8 bit), composites any logical rectangle across outputs at one scale (grim's rule: the largest
! scale of the outputs it touches, edges rounded outward; nearest sample at pixel centres, so a region inside one
! output is a pixel-exact crop of its buffer), applies each frame's wl_output transform and y-invert, encodes
! png / jpg / webp (lossless), renders [desktop.screenshot] filename templates (strftime + {mode} {output} {app}) and
! writes files atomically without clobbering (temp file + renameat2 NOREPLACE, "-N" suffix on collision).
! AI-related: usr/share/mios/mios.toml [desktop.screenshot]

<!-- mios-src:34b2420b515c from tools/native/mios-shot/src/main.rs:27-33 -->

### ! AI-hint: Wayland side of mios-shot: binds outputs...

! AI-hint: Wayland side of mios-shot: binds outputs (wl_output + xdg-output logical geometry), captures them or one toplevel
! into shm buffers through ext-image-copy-capture-v1 (falling back to wlr-screencopy-v1 for outputs), shows the captured
! buffers as the frozen frame (one layer-shell overlay surface per output, namespace mios-shot-freeze, no input, viewporter
! to the logical size), and serves the clipboard through ext-data-control-v1. Colour-management unaware on purpose, so
! Hyprland hands back SDR frames; shm only (a still capture needs a CPU copy anyway; dmabuf would need gbm/EGL); never
! opens a cursor session (paint_cursors only).
! AI-related: usr/share/mios/mios.toml [desktop.screenshot]

<!-- mios-src:813637955163 from tools/native/mios-shot/src/main.rs:550-556 -->

### Wait for every pending capture to finish. An output that is...

Wait for every pending capture to finish. An output that is not being drawn (DPMS off, a hidden nested
window, a stalled virtual display) never answers: once one frame is in, the rest get GRACE more, and
whatever has not answered by then is reported failed instead of holding every other output hostage.

<!-- mios-src:faa0a2a967b8 from tools/native/mios-shot/src/main.rs:846-848 -->

### Never read the whole operating system

Never read the whole operating system: candidate_files stays within the repository (or the
root's filesystem), and only regular files a template governs are read, within a size bound.
Reading /proc/kcore or VM images whole once drove this tool to 34 GB and got the operator's
session OOM-killed.

<!-- mios-src:60e94666c341 from tools/native/mios-template-conform/src/main.rs:238-241 -->

### No -M

No -M: tmux 3.7c ignores -C for a menu that handles the mouse (man display-menu; probed: with -M the
menu opens with nothing selected, Enter chooses nothing and Up wraps to the last item, which on the
kill confirmation would be "Yes"). A right-click opens the menu from a mouse binding, so it still
takes clicks there; key-opened menus are driven by arrows, Enter and the shortcut keys.
The -mouse variants, opened by a right-click (or a touch long-press), add -O: the menu stays open when the
button is released and a click or tap chooses. -O, like -M, makes tmux ignore -C, so key openers never
use them.
A right-click menu opens with its top-left corner at the pointer (stock tmux -x M -y M): releasing
the button over an item would choose it, so a quick click or tap must land on the border instead.

<!-- mios-src:2c73d7719d6b from tools/native/mios-unit-gen/src/tmux_os.rs:775-783 -->

### `mios-menu`: the same size- and console-aware opener as...

`mios-menu`: the same size- and console-aware opener as prefix Space and the status-line right-click, by
name (the command prompt, scripts with one client). An alias appends its CLI arguments to the END of its
expansion, so `-c <client>` cannot aim it: run from outside, tmux picks the client. The controller's
`tmux:menu` verb (FINAL.md 3.3) therefore uses the client-exact route
  tmux -L <socket> send-keys -c <client> -K <tmux_prefix> <menu_key>
whose key words the server publishes as @mios-menu-keys, so a caller never re-derives them from the SSOT.

<!-- mios-src:1f7dc5c565f1 from tools/native/mios-unit-gen/src/tmux_os.rs:821-826 -->

### Resolve mouse_any_flag after selecting the clicked pane...

Resolve mouse_any_flag after selecting the clicked pane: the previously active pane would route
another pane's right-click to the wrong owner. Copy and view mode keep the menu (Go to top/bottom).

<!-- mios-src:7f31c86de435 from tools/native/mios-unit-gen/src/tmux_os.rs:853-854 -->

### Float shells die with their parent session, decided and...

Float shells die with their parent session, decided and done inside tmux: the hook spawns no process (no
per-event interpreter or shell). `if-shell -F` finds the float session of the closed one by name, and
`run-shell -C` runs kill-session as a tmux command with its target format-expanded (a plain kill-session
target is not). mios-terminal --float also reaps orphans opportunistically whenever a float opens.

<!-- mios-src:435366cdab6e from tools/native/mios-unit-gen/src/tmux_os.rs:889-892 -->

### The native host's live step rate ([desktop.framerate]...

The native host's live step rate ([desktop.framerate]; operator 2026-10-23: "wallpaper(s) are 1/2 rate globally--
except for Unlocked mode(s)"): `ctl` = the state file miosd framerate writes under $XDG_RUNTIME_DIR, which Host.qml
reads once a second (wallpaper_fps). The URL's fps stays the look's reference rate. Nothing when the policy is
absent or disabled (the host then steps at the reference rate, as before) or for the web host (the HTML's own FPS).

<!-- mios-src:e3da9c17f319 from tools/native/mios-wallpaperd/src/main.rs:514-517 -->
