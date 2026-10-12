<!-- AI-hint: Manual pages distilled from the source comments of drift, sanitized, each passage anchored to the comment it came from. -->

# drift

### Re-render a projection whose generator is a SHELL renderer...

Re-render a projection whose generator is a SHELL renderer and diff it against
what is committed.

`regen_and_diff` above only suits generators that implement `--check` and
report drift through their exit code. The chrony/nut/kargs/composefs
projections have no such mode: `automation/NN-*-render.sh` WRITE their output.
Six checks in this registry were therefore wired to Python generators that do
not exist in the tree (`generate-kargs.py`, `generate-chrony-conf.py`, ...),
and because a missing generator yields `Verdict::Skip`, they never ran and
never said so.

This renders into a scratch directory seeded with the committed artifact,
then compares -- the same shape as the bash gate's kargs check.

<!-- mios-src:888eee4c1089 from src/mios-rs/miosd/src/drift/regen.rs:81-93 -->
### task_982. This used to stat the Justfile and return Pass("BIB...

task_982. This used to stat the Justfile and return Pass("BIB single
config invariant verified") without opening it. Ported from the bash
twin: every config/artifacts/*.toml must parse, and every recipe that
invokes bootc-image-builder must mount EXACTLY ONE /config.toml --
two mounts and the second silently wins.

<!-- mios-src:e2b2b64cc33e from src/mios-rs/miosd/src/drift/deploy.rs:92-96 -->

### task_982. This used to stat two files and return Pass("Deploy...

task_982. This used to stat two files and return Pass("Deploy plane
verified"). Stating that a file EXISTS is not verifying what is in
it. Ported from the bash twin's content assertions -- and unlike the
twin, an absent subject FAILS here rather than printing a WARNING and
carrying on, because these are tracked deliverables.

<!-- mios-src:fd1f10ba2aae from src/mios-rs/miosd/src/drift/deploy.rs:214-218 -->

### task_980. This used to test `p.exists()` and then return...

task_980. This used to test `p.exists()` and then return
Pass("Law enforcers resolution validated clean") -- a claim about a
file it never opened. Touching ctx.root to BUILD a path is not
reading the tree, which is why two successive stub detectors let it
through. CLAUDE.md calls [laws] the canonical registry; this now
checks it.

<!-- mios-src:e2a0d945f8af from src/mios-rs/miosd/src/drift/laws.rs:17-22 -->

### `process:` is a real scheme, not a malformed entry: Law...

`process:` is a real scheme, not a malformed entry: Law 15's
triple-check-before-acting cannot be gated, only followed. It
still has to SAY something, which the emptiness test above covers.

<!-- mios-src:62909a3e7822 from src/mios-rs/miosd/src/drift/laws.rs:93-95 -->

### task_982. This used to test whether a DEBUG BUILD of the...

task_982. This used to test whether a DEBUG BUILD of the generator
existed and, if so, return Pass("Names registry projection matches
SSOT") -- without running it and without comparing anything. If the
binary was absent it skipped instead, so on an ordinary tree the
check was silent and on a developer's tree it lied. Both halves of
Skip-as-Pass in one function.

The projection is produced by tools/generate-names-registry.py, the
same generator sync-generated.sh runs, so regenerate and diff it the
way every other projection check already does.
The generator has no --check mode and the tooling-Python ratchet has
no room to add one (task_981), so compare in Rust: snapshot, render,
diff, restore.

<!-- mios-src:e329f7bbba21 from src/mios-rs/miosd/src/drift/names.rs:19-31 -->

### task_980. This used to read ctx.in_image for the early skip...

task_980. This used to read ctx.in_image for the early skip above and
then return a constant Pass -- "ordinals verified dense" about a tree
it never opened. Naming the parameter `ctx` was the only thing that
made it look implemented. Ported from the bash twin's three assertions.

<!-- mios-src:f38c990e8408 from src/mios-rs/miosd/src/drift/numbering.rs:20-23 -->

### task_982. This function is named regen_and_diff and it does...

task_982. This function is named regen_and_diff and it does not diff: it
runs the generator and then asserts the target EXISTS. That is only sound
when the generator itself compares, i.e. when --check is passed and its
exit status is the verdict. Called without it the generator WRITES --
erasing the very drift the check is looking for, and mutating the tree
from inside a read-only gate. Refuse rather than mislead.

<!-- mios-src:b2bbad5618f1 from src/mios-rs/miosd/src/drift/regen.rs:17-22 -->
### Law 12 scan roots, ported from tools/check-runtime.py...

Law 12 scan roots, ported from tools/check-runtime.py fdo_SCAN_GLOBS
("usr/libexec/mios/*firstboot*", "automation/firstboot/*.sh"): (dir, infix, suffix).
Both roots are the gate's subject, so a missing one fails instead of shrinking it.

<!-- mios-src:bf73b53d7860 from src/mios-rs/miosd/src/drift/boot.rs:36-38 -->

### The legacy check grepped the whole FILE for '|| true' (or...

The legacy check grepped the whole FILE for '|| true' (or set +e / trap / exit 0) and
called that degrade-open: one unrelated cleanup guard certified a script that really
aborted firstboot on an unreachable API. The question is scoped to each egress call.

<!-- mios-src:f7983252994d from src/mios-rs/miosd/src/drift/boot.rs:155-157 -->

### Every value used to come from ${MIOS_CONV_*:-literal}...

Every value used to come from ${MIOS_CONV_*:-literal}, which nothing exports, so the
legacy check graded its own drifted defaults. Read the SSOT only; a missing key fails.

<!-- mios-src:e8c6bf85cf36 from src/mios-rs/miosd/src/drift/converge.rs:87-88 -->

### Every top-level SSOT section must reach the database...

Every top-level SSOT section must reach the database: either listed in the
seeder's `_CANONICAL_SECTIONS` allowlist (what `get_seeded_sections` mirrors
into config_kv) or read by name from the parsed SSOT by a dedicated seeding
path (verbs -> verb, packages -> package_set). The allowlist is checked both
ways: an entry the SSOT no longer has is a rotted name from a dropped table.
The legacy gate hard-coded {verbs, packages} as "handled separately"; here
that set is derived from the seeder's own named reads, so deleting the verb
seeding path un-covers [verbs] instead of leaving it exempt.

<!-- mios-src:a43003e3fb20 from src/mios-rs/miosd/src/drift/db.rs:186-193 -->

### Every [agents.*]/[users.*] principal's max_permission...

Every [agents.*]/[users.*] principal's max_permission ceiling must name a
tier from the SSOT catalog [ai].permission_tiers. As in the legacy gate, an
absent [agents] or [users] table means no such principals, and an absent or
empty max_permission is the documented "no ceiling" ([agents._defaults]);
each principal table is examined either way. A present section that is not
a table fails, and so does a missing or empty catalog (the legacy fell back
to a hard-coded read/write/interactive list).

<!-- mios-src:52f7b86183f9 from src/mios-rs/miosd/src/drift/db.rs:270-276 -->

### In-memory stand-in for the PostgreSQL tables the seeder...

In-memory stand-in for the PostgreSQL tables the seeder writes and the
materializer reads (config_kv, verb, domain_verb). The subject under test IS
the behaviour of the two Python scripts, so they run for real against this
double. INSERT rows are mapped by the statement's own column list, and the
verb SELECT answers in the statement's own column order, so a reordered
column cannot silently shift values.

mios_toml is withheld: every attribute raises. The materializer backfills
any table missing from the DB out of the vendor mios.toml, which is the very
file the result is compared against -- with that path open (as the legacy
gate left it) a scope the seeder dropped was restored from the file and the
round trip could not fail for the defect it exists to catch. With it shut,
whatever the materializer prints came out of the database.

<!-- mios-src:3d1febb7db23 from src/mios-rs/miosd/src/drift/db.rs:387-399 -->

### Seed the SSOT into an in-memory database with...

Seed the SSOT into an in-memory database with seed-db-config.py, materialize
it back with materialize-config-toml.py, and require the result to equal the
SSOT for every config_kv scope, for routing.domains (via domain_verb) and for
[verbs] (via the verb table, field by field). The legacy gate compared a
hand-copied list of eleven scopes. Here the scopes that must round-trip are
every SSOT table the seeder's `_CANONICAL_SECTIONS` allowlists (so a seeder
that silently skips one at runtime is caught) plus any scope it actually
wrote (so nothing seeded goes uncompared).

<!-- mios-src:9898a64683db from src/mios-rs/miosd/src/drift/db.rs:664-671 -->

### EMITTED is what the legacy reads back from `bash -c '....

EMITTED is what the legacy reads back from `bash -c '. userenv.sh; env'`
with every MIOS_* scrubbed from the environment and every tier but the
vendor file pinned away (MIOS_HOST_TOML=/dev/null, *_D=/nonexistent).
userenv.sh evals `mios-resolver --emit=shell` WITHOUT --root, so the
referenced_names.txt pass-through never applies and a key whose value is
empty is not exported: declared-but-empty does not close a reference.
Rust calls that same projection in process instead of running bash, and
reads it over the vendor file alone, never the tree's etc/mios tiers.

<!-- mios-src:53194a548b20 from src/mios-rs/miosd/src/drift/laws.rs:435-442 -->

### The ceiling governs sub-modules only (operator ruling)...

The ceiling governs sub-modules only (operator ruling): modules under a
declared sub-module package. A component's main modules (the agent-pipe
root) are where features fold in, so they carry no line ceiling.

<!-- mios-src:2535381627ae from src/mios-rs/miosd/src/drift/modules.rs:77-79 -->

### task_982. This used to test whether a DEBUG BUILD of the...

task_982. This used to test whether a DEBUG BUILD of the generator
existed and, if so, return Pass("Names registry projection matches
SSOT") -- without running it and without comparing anything. If the
binary was absent it skipped instead, so on an ordinary tree the
check was silent and on a developer's tree it lied. Both halves of
Skip-as-Pass in one function.

task_981/task_953 lineage: the generator was ported to the native
tools/native/generate-names-registry binary and the Python script
strangler-deleted (task_1713); this caller kept the stale .py path
and the python3 interpreter assumption, so Windows-invoked image
builds failed with "Generator not found: tools/generate-names-registry.py".
The native generator writes BOTH projections (names.generated.txt
and referenced_names.txt), so both are snapshotted and restored --
comparing one while leaving the other rewritten was half a verdict.

<!-- mios-src:dd6be916debb from src/mios-rs/miosd/src/drift/names.rs:19-33 -->

### Retired local-lane ports (Law 5) must not survive in...

Retired local-lane ports (Law 5) must not survive in execution-path code.
The roster is the SSOT registry `[docs].retired_ports` and the exemptions are
the itemised `[docs].retired_code_exemptions` paths (exact match), replacing
the legacy hard-coded six ports and eleven basenames. Docstrings, comments
and echo/usage text are prose, not execution, and stay exempt as before.

<!-- mios-src:bc7baa3c58ba from src/mios-rs/miosd/src/drift/ports.rs:128-132 -->

### regen_and_compare_file (python3-interpreted regen for .py...

regen_and_compare_file (python3-interpreted regen for .py generators) was
deleted with the last of its consumers: the names-registry Python generator
was strangler-deleted (task_1713) and its miosd caller moved to
regen_and_compare_native below. Every surviving drift regen check invokes a
native binary.

<!-- mios-src:0c7801a98007 from src/mios-rs/miosd/src/drift/regen.rs:138-142 -->

### The shipped twin must be byte-identical to the...

The shipped twin must be byte-identical to the authoritative one, which
59-tools.sh installs over it at bake. Byte-exact on purpose: both are `*.sh`
under one `.gitattributes` eol rule, so every checkout and bake gives them the
same line endings, and a twin differing only by CR is a hand-copied file whose
CRs would reach bash. Normalising would hide exactly that.

<!-- mios-src:41333ec07eb1 from src/mios-rs/miosd/src/drift/resolver.rs:22-26 -->

### TD-1

TD-1: a shell verb must not `eval` (agent-controlled input becomes code).
TD-2: no file under the verb tree may call os.system() (shell-string exec).
Both scan the whole tree recursively, skipping dot-directories as the legacy
os.walk did. A reviewed eval of non-agent input is accepted only with the
annotation the legacy gate's own remedy text prescribes on the line directly
above it; that exemption was dropped from the legacy code while its message
kept telling authors to add it.

<!-- mios-src:ae766dfcc9dc from src/mios-rs/miosd/src/drift/security.rs:159-165 -->
