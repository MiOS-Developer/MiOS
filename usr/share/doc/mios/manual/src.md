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
