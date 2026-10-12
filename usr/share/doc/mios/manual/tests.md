<!-- AI-hint: Manual pages distilled from the source comments of tests, sanitized, each passage anchored to the comment it came from. -->

# tests

### Read the component lists from SSOT. A missing or empty list...

Read the component lists from SSOT. A missing or empty list is FATAL, not a
fallback: `for x in ${EMPTY}` runs zero iterations and the loop still prints
"OK", so an SSOT edit that dropped a list would turn this whole harness into a
vacuous pass. The old fallbacks also hardcoded paths (Law 7) and capitalised
them ("Usr/..."), so they could never have matched anything anyway.

<!-- mios-src:91e227c14aae from tests/bake-smoke.sh:22-26 -->

### MUST run before anything else. check_ai_manifests_fresh...

MUST run before anything else. check_ai_manifests_fresh compares the
manifests against a fresh walk of automation/ and tools/, and dozens of
the tests below create, mutate and restore files in exactly those trees
(some restore via `echo "$orig" >`, which drops a trailing newline). Run
it last and it grades the wreckage of every preceding test instead of the
committed state.

<!-- mios-src:1a2b377668f5 from tests/drift-gate-negatives.sh:2373-2378 -->

### Verify the ReWOO #E<id> substitution now smart-extracts a...

Verify the ReWOO #E<id> substitution now smart-extracts a single
field instead of pasting the whole upstream JSON blob.

Test cases derived from operator's failure trace where the planner
emitted open_app(name=#En1) and substitution pasted mios_apps's
entire NDJSON output as the arg.

<!-- mios-src:fd04fe58468c from tests/test-ek-smart-extract.py:5-11 -->

### Smoke-test _substitute_ek_refs. Verifies the ReWOO #E<id>...

Smoke-test _substitute_ek_refs.

Verifies the ReWOO #E<id> placeholder substitution across the
shapes a planner might emit:
  * simple string substitution
  * multiple refs in one arg
  * refs to non-existent ids (preserved literal so dispatch errors)
  * non-string args (passed through)

<!-- mios-src:a9fa0f5051af from tests/test-ek-substitution.py:5-13 -->

### Smoke-test the skill engine's expand_from semantics. Calls...

Smoke-test the skill engine's expand_from semantics.

Calls execute_skill('open-url-fallback-chain', ...) with 3 browsers
and a deliberately-bad URL; verifies the engine fanned 1 step into
3 (one per browser) by inspecting the returned `steps` list length.

Exits 0 on PASS, 1 on FAIL.

<!-- mios-src:2cc352692064 from tests/test-expand-from.py:5-12 -->

### Smoke-test the refine chat-promotion guard. Calls...

Smoke-test the refine chat-promotion guard.

Calls refine_intent() with three actionable inputs that a small
refine model has historically misclassified as chat (operator-
flagged trace: 'mios-open-url https://...' returned intent=chat
+ fabricated 'Wikipedia has been opened' confirmation when nothing
was actually executed). Verifies the post-parse guard rewrites
chat -> dispatch.

<!-- mios-src:eeb00086dc8c from tests/test-refine-guard.py:5-13 -->

### Verify the new refine post-parse guards demote...

Verify the new refine post-parse guards demote misclassified
intents to `agent`. Three cases:

  1. Long multi-step prompt -- exact operator-flagged trace:
     "find all of my installed games; research all their ratings,
     review and launch the highest reviewed game I have installed
     for me on my PC". Refine model may emit intent=dispatch (as
     it did in the failure trace); the length guard should promote
     to agent so the planner can decompose.
  2. Short legitimate dispatch -- "open chrome". Should pass
     through as intent=dispatch (length under threshold).
  3. Multi-word arg value -- simulate a refine output via direct
     guard invocation (refine model is non-deterministic, so we
     can't always force it; this case is exercised by calling the
     guard logic directly with a forged envelope).

Live test against the real refine endpoint -- slow (15-30s per
call on CPU).

<!-- mios-src:7f133c2ab83a from tests/test-refine-guards.py:5-23 -->

### Smoke-test reflect_on_step_failure. Calls the reflection...

Smoke-test reflect_on_step_failure.

Calls the reflection helper with a deliberately-bad failed_node
(unknown verb) and verifies the small refine model returns a
correction with a non-empty tool name + rationale.

Live test -- hits the actual refine endpoint -- so it's slow
(15-30s on CPU) but exercises the real path.

<!-- mios-src:d63f63fff4d6 from tests/test-reflection.py:5-13 -->
### An Image= whose variable resolves nowhere used to be...

An Image= whose variable resolves nowhere used to be skipped silently,
which resurfaced as "core image is not referenced by any Quadlet" --
an error naming a different file entirely. The probe tag is deliberately
not MIOS_-prefixed: generate-names-registry harvests every MIOS_* token it
sees, so a MIOS_-named probe writes itself into referenced_names.txt and
the test starts editing the SSOT it guards.

<!-- mios-src:7140d10222ad from tests/drift-gate-negatives.sh:414-419 -->

### neg_gate once contained a literal backslash-n instead of...

_neg_gate once contained a literal backslash-n instead of line
continuations, so the command word became `n` and it returned 127 every
time. Under that, all 61 tests that call it could never detect anything --
`if _neg_gate X; then die` simply never fired -- while their restoration
arms died unconditionally. A helper 61 tests depend on has to be proven
before it is trusted, and proven in BOTH directions.

<!-- mios-src:631badca875f from tests/drift-gate-negatives.sh:2956-2961 -->

### !/usr/bin/env bash AI-hint: Self-contained test harness for...

!/usr/bin/env bash
AI-hint: Self-contained test harness for automation/97-ssot-lint.sh -- builds throwaway fixture trees (a fully-wired key, a both-sides orphan, a userenv-only and a render-only half-orphan) to assert the lint's PASS/FAIL exit codes and orphan detection, then asserts it flags the real known dead key (MIOS_SGLANG_TOOL_PARSER) in the live repo tree.
AI-related: ../97-ssot-lint.sh, ../34-render-quadlets.sh, ../../tools/lib/userenv.sh, ../../usr/share/containers/systemd
AI-functions: _mk_fixture, _expect, main

<!-- mios-src:a64282216d09 from automation/tests/test-97-ssot-lint.sh:1-4 -->
### Linux Clean Worktree Test & Drift Gate Execution

To run the full suite of drift checks and negative gate tests on a clean Linux environment:

```bash
# Set repository environment variables
export MIOS_DRIFT_ROOT="$(pwd)"
export MIOS_DRIFT_CHECK_ROOT="$(pwd)"
export MIOS_DRIFT_REQUIRE_TOOLS=1

# Run full drift check suite
bash automation/98-drift-checks.sh

# Run negative gate tests
bash tests/drift-gate-negatives.sh
```

Note: A stale installed MiOS on the host machine can fake SSOT-projection drift if `/etc/mios` or `/usr/share/mios` contains un-projected overrides. Always run with `MIOS_DRIFT_ROOT` pointing explicitly to the local workspace root.

<!-- mios-src:agy-1620 from docs/manual/tests.md -->
### Import this before importing `server`. Nine suites each...

Import this before importing `server`.

Nine suites each pointed the import search at the INSTALLED directory. A CI
runner has no such directory, so importing the server raised and every one of
those suites failed -- which is why none was ever wired into a workflow. A
developer machine that does have MiOS installed ran them against the installed
copy instead of the working tree, so the change under test was not the code
being tested.

Resolving from this file's own location fixes both: the repository copy comes
first, and the installed directory stays as a fallback for a suite executed
outside a checkout.

<!-- mios-src:0ab56d83257e from tests/_agentpipe_path.py:3-15 -->

### The suite mutates tracked files and is supposed to put them...

The suite mutates tracked files and is supposed to put them back. A test that
dies between the two leaks its fixture into the tree. Five reached the working
tree in one session -- an injected table in the shipped SQL schema, a root
password in a Ventoy firstboot script, a rewritten cockpit port, a capability
requirement replaced by an injected name, a port entry repeated twice -- and
each one surfaced as some unrelated suite failing, so the cost was paid several
times before anyone read the diff.

Snapshot everything the suite can reach before running, and put back whatever a
test failed to restore. The target list is derived from this file's own source,
so a test that starts touching a new path is covered without anyone updating a
list.

<!-- mios-src:59b3532038ab from tests/drift-gate-negatives.sh:10-21 -->

### 55-bake-quickshell.sh was renumbered to 66-. The whole body...

55-bake-quickshell.sh was renumbered to 66-. The whole body used to sit
inside `if [[ -f ]]`, so once the file moved the test skipped everything
and logged "passed" -- a test that reports success precisely when its
subject is gone. A missing target is now a failure.

<!-- mios-src:5b27437275f6 from tests/drift-gate-negatives.sh:1762-1765 -->

### Subshell

Subshell: die() exits the test, not the suite. One CI run then reports
every failure instead of the first, which is what turned a queue of
latent breakages into one round trip each.

<!-- mios-src:56662eab3f3b from tests/drift-gate-negatives.sh:2935-2937 -->

### REQUIRE_TOOLS is forwarded deliberately

REQUIRE_TOOLS is forwarded deliberately: checks that shell out to a built
binary choose between "skip" and "fail" on it, so a test that cannot set
it cannot exercise the failing path -- the path that matters.
Output is kept, not discarded: "failed after restoration" with no reason
has cost two CI round trips, and die() prints this on the way out.

<!-- mios-src:f84b0295a436 from tests/drift-gate-negatives.sh:2942-2946 -->

### A C-style header in a systemd unit is not a comment: the...

A C-style header in a systemd unit is not a comment: the line is rejected,
and one such line in a WSL config failed a build twenty-nine minutes in.

<!-- mios-src:252855120d3e from tests/drift-gate-negatives.sh:3134-3135 -->

### The previous probe flipped `enabled` from false to true...

The previous probe flipped `enabled` from false to true, but the key has
been true for some time, so the sed matched nothing and the test asserted
against an unmodified tree. The check requires every merge-rule key to
have a table carrying origin_node and logical_ts, so declaring a rule with
no such table is the edit that loses data silently on rejoin (ADR-0017 D5).

<!-- mios-src:52fddbc14295 from tests/drift-gate-negatives.sh:3570-3574 -->

### The old probe moved bootc-fetch-apply-updates.timer aside...

The old probe moved bootc-fetch-apply-updates.timer aside, but that file
ships from an RPM and has never existed in this tree: it moved nothing and
the check "failed" for a reason the test never created -- a broken probe
and a broken check agreeing. The check now asserts the SSOT declares an
updater package and a bake phase wires its timer, so break the wiring.

<!-- mios-src:76296fd5fe57 from tests/drift-gate-negatives.sh:3728-3732 -->
### Adversarial Verification Suite (Challenger 1). Executes...

Adversarial Verification Suite (Challenger 1).

Executes stress tests, edge cases, boundary conditions, fuzzing payloads,
and security attack scenarios across the roadmap modules:
- task_321: MCP Bubblewrap Sandbox Engine
- task_322: HITL Interactive Approval Engine
- task_323: Knowledge Graph Recursive CTE Traversal
- task_324: Contextual Prompt Token Pruning Engine
- task_325: Agent-to-Agent (A2A) Ed25519 Attestation

<!-- mios-src:596496e54e7c from tests/test-adversarial-roadmap.py:4-14 -->

### Adversarial Observation

Adversarial Observation: When operator username contains a colon (e.g. 'admin:ops'),
        token serialization creates extra delimiters, causing validation failure.

<!-- mios-src:8346ccde2a47 from tests/test-adversarial-roadmap.py:219-222 -->

### MiOS Empirical Adversarial Test Harness (Challenger 2)....

MiOS Empirical Adversarial Test Harness (Challenger 2).

Executes stress-testing, boundary attacks, cyclic recursion tests, cryptographic
malleability checks, AST preservation tests, and fuzzing payloads against:
- MCP Bubblewrap Sandbox Engine (task_321 / MCP-01)
- Interactive HITL Permission Escalation & Approval Engine (task_322 / SEC-06)
- Recursive CTE Knowledge Graph Traversal Engine (task_323 / GRAPH-01)
- Contextual Prompt Compression & Token Pruning Engine (task_324 / PROMPT-01)
- A2A Cryptographic Capability Attestation Engine (task_325 / A2A-01)

<!-- mios-src:2d05ccc7a701 from tests/test-empirical-challenger-2.py:4-14 -->

### Adversarial Stress Test Suite for Milestone 1: 1....

Adversarial Stress Test Suite for Milestone 1:
1. Self-Healing Circuit Breaker & Safe Remediation Engine (task_326)
   - Rapid bursts of failures (100 rapid events)
   - Multi-unit isolation & interleaved failure/recovery sequences
   - Circuit breaker window expiration & quarantine timing
   - Invalid / binary / corrupted journal logs
   - Malformed & traversal /usr immutability attack paths
   - Corrupted state JSON recovery and schema validation
   - SafeConfigEditor atomic file operations & error handling

2. Synthetic Training Q&A Data Pipeline (task_327)
   - Secret redactor: nested keys (JSON/YAML/TOML/Env), multi-line keys (RSA/EC/SSH), tokens, bearer auth
   - Secret redactor: multi-word passwords inside quotes
   - Secret redactor: false-positive preservation on standard prose and config keys
   - Hierarchical markdown parser: 6-level deep headers, header level jumping, headers inside code blocks
   - Unclosed code fences, malformed tables, empty sections, unicode/emoji handling
   - Q&A synthesis schema adherence & JSONL single-line validation

<!-- mios-src:d333e27d454b from tests/test-m1-adversarial.py:4-22 -->

### Adversarial Stress Test Suite for Milestone 1 (Challenger...

Adversarial Stress Test Suite for Milestone 1 (Challenger 2):
1. Dynamic Persona Synthesis (task_328 / task_2561)
   - Conflicting multi-domain queries & score balancing across 6 specialized domains
   - Zero-keyword, whitespace, punctuation, and emoji-only inputs
   - Multilingual queries (Chinese, Japanese, French, German, Russian, Arabic)
   - Adversarial prompt injections & canonical law override resistance
   - Boundary confidence thresholds & synthesis idempotency
   - Long-text stress (50,000+ words) & zero degradation

2. Bounded Reflection Loop Convergence (task_329 / task_2562)
   - Identical successive texts (0.0 delta) -> instant diminishing returns exit
   - Sub-5% micro-edits in realistic paragraph -> diminishing returns exit
   - Oscillating / adversarial critiques -> strict max_iteration ceiling enforcement
   - Semantic delta mathematical properties (identity, range [0, 1], high disjoint delta)
   - Configurable max_iterations and min_iterations enforcement
   - Extreme corpus size (10,000+ words) delta calculation performance
   - Critique approval pattern matching & false-positive negation analysis
   - Deliberation state tracking & dictionary serialization integrity

<!-- mios-src:fdaafbbba72a from tests/test-m1-challenger2-adversarial.py:4-23 -->

### Adversarial Stress Test Suite for Milestone 2: 1. Async TCP...

Adversarial Stress Test Suite for Milestone 2:
1. Async TCP Framing & Wire Codec (task_330)
   - Byte-by-byte (1-byte chunk) stream feeding across 50 multi-opcode frames
   - Irregular/randomized chunk slicing across packet boundaries
   - High-concurrency async TCP client/server throughput (30 concurrent clients, 300 frames)
   - Corrupted CRC32 injection across head, middle, and tail of payload
   - Corrupted magic, version, opcode, and underflow rejection
   - Oversized payload length header rejection (> 64MB)
   - Zero-byte payload valid frame roundtrip (CRC32=0)
   - Stream buffer partial frame drainage and resume
   - NodeWireDispatcher error response generation for unhandled opcodes

2. Heartbeat Monitor & Dead-Peer Eviction (task_331)
   - Mathematical boundary precision (0s, 4.999s, 5.0s, 9.999s, 10.0s, 14.999s, 15.0s)
   - Rapid flapping and state churn across 20 peers for 100 timesteps
   - Mass simultaneous eviction of 100 peers in a single sweep
   - Complete listener notification dispatch on mass eviction
   - Clean re-admission after eviction with strike and state reset
   - Local node ID self-filtering rejection
   - Monotonic time jitter / backward timestamp protection
   - Custom threshold configuration lifecycle

<!-- mios-src:2212811d9cc6 from tests/test-m2-adversarial.py:5-27 -->

### Adversarial Stress Test Suite for Milestone 2 / task_332...

Adversarial Stress Test Suite for Milestone 2 / task_332 (Challenger 2):
1. Cryptographic Handshake Adversarial Tests:
   - Exhaustive single-bit and multi-byte signature tampering across Init and Resp packets (all 64 bytes fuzzed).
   - Signature truncation (< 64 bytes) and extension (> 64 bytes) rejection.
   - Forged identity pubkeys and ephemeral pubkeys injection / MITM rejection.
   - Imposter node identity spoofing and unauthorized packet creation.
   - Replay attack resilience and ephemeral key freshness (no key reuse).
   - Key derivation symmetry, directional TX/RX key separation, and anti-reflection guarantee.

2. Wire AEAD Encryption Adversarial Tests:
   - Exhaustive bit-flip fuzzing across all payload ciphertext bytes.
   - Exhaustive bit-flip fuzzing across all 16 bytes of the Poly1305 MAC tag.
   - Ciphertext truncation (< 16 bytes) and partial MAC tag drop handling.
   - AAD / Node ID spoofing and cross-node ciphertext injection rejection.
   - Strict nonce sequence progression, out-of-order packet drop, and wire replay attack prevention.
   - High-volume multi-frame stream stress (1,000 frames) with boundary payload sizes (0B, 1B, 15B, 16B, 17B, 64B, 65B, 64KB).
   - Layered defense validation: Wire CRC32 transport integrity vs Poly1305 cryptographic authenticity.

3. Concurrency & RFC Standards Compliance:
   - Concurrent multi-session thread isolation across 20 distinct mesh nodes.
   - Session renegotiation & zero cross-session decryption leakage.
   - RFC 8439 / RFC 7748 / RFC 5869 cryptographic correctness verification.

<!-- mios-src:d20bb811f899 from tests/test-m2-challenger2-adversarial.py:5-28 -->

### Unit and integration test suite for WS-NODE: Async TCP...

Unit and integration test suite for WS-NODE: Async TCP frame reader, writer actor,
stream buffer management, partial packet chunking, and channel dispatch.

<!-- mios-src:328b88702d66 from tests/test-node-async-net.py:4-7 -->

### Unit test suite for WS-NODE

Unit test suite for WS-NODE: Ed25519 node identity signing/verification, X25519 Diffie-Hellman
key exchange, HKDF-SHA256 session key derivation, ChaCha20-Poly1305 authenticated symmetric payload
encryption, MAC tag validation, tamper detection, and imposter rejection.

<!-- mios-src:8f6cd66980af from tests/test-node-crypto-handshake.py:4-8 -->

### Unit test suite for WS-NODE

Unit test suite for WS-NODE: Heartbeat interval (5s), 3-strike dead peer detection (15s threshold),
degraded status transitions, routing table pruning, eviction event dispatching, and re-admission.

<!-- mios-src:559815f2ca99 from tests/test-node-heartbeat-eviction.py:4-7 -->
### 1. task_336: Stress & Invariant Tests for Work-Stealing...

=========================================================================
1. task_336: Stress & Invariant Tests for Work-Stealing Scheduler
=========================================================================

<!-- mios-src:319eee22cb65 from src/mios-rs/mios-node/tests/mesh_m2_stress_challenger_test.rs:23-25 -->

### 2. task_337: Stress & Invariant Tests for Zero-Copy Buffer Pool

=========================================================================
2. task_337: Stress & Invariant Tests for Zero-Copy Buffer Pool
=========================================================================

<!-- mios-src:dc3d1639e380 from src/mios-rs/mios-node/tests/mesh_m2_stress_challenger_test.rs:170-172 -->

### 1. task_336: Stress & Invariant Tests for Work-Stealing...

-------------------------------------------------------------------------
1. task_336: Stress & Invariant Tests for Work-Stealing Scheduler
-------------------------------------------------------------------------

<!-- mios-src:998443187b25 from tests/test-node-m2-adversarial-challenger.py:59-61 -->

### 2. task_337: Stress & Invariant Tests for Zero-Copy Buffer Pool

-------------------------------------------------------------------------
2. task_337: Stress & Invariant Tests for Zero-Copy Buffer Pool
-------------------------------------------------------------------------

<!-- mios-src:eb3bc6b389eb from tests/test-node-m2-adversarial-challenger.py:155-157 -->
### The registry is whatever mios.toml says it is. This test...

The registry is whatever mios.toml says it is.

This test used to snapshot `default_registry()` -- a six-phase hardcoded
list -- and call it golden, which blessed the very fallback that made a
six-of-seventy-one-phase build look complete. A golden over a constant
proves the constant has not changed, not that the loader works.

<!-- mios-src:4e84b507d438 from src/mios-rs/mios-build/tests/golden_harness.rs:41-46 -->

### The defect this port exists for. A key-only register...

The defect this port exists for. A key-only register grandfathers the KEY,
so an operator's real password baked in by a build-environment variable
reads as the same entry and the gate stays green.

<!-- mios-src:742c96b615ed from src/mios-rs/mios-gate/tests/credentials.rs:57-59 -->

### task_980, and a bug in this gate's first predicate: naming...

task_980, and a bug in this gate's first predicate: naming the parameter `ctx`
instead of `_ctx` proves nothing. check_pipeline_numbering read ctx.in_image
for an early skip and then returned a constant Pass, so a parameter-name test
classified it as implemented and it kept claiming a verdict.

<!-- mios-src:66403965ad91 from src/mios-rs/mios-gate/tests/stubs.rs:80-83 -->

### The regression this file exists for. The shipped drop-in...

The regression this file exists for. The shipped drop-in,
usr/lib/bootc/kargs.d/01-mios-vfio.toml, is NOT wholly generated:
rd.driver.pre=vfio-pci binds vfio-pci in the initramfs before a
GPU driver can claim the card, and kvm-intel.nested=1 enables nested KVM.
Neither comes from any [kargs] key. A renderer that rebuilds the list from
scratch deletes both from the kernel command line, exits 0, and leaves a
header claiming the file came from SSOT.

<!-- mios-src:9e4749c8da2b from src/mios-rs/miosd/tests/render_kargs.rs:53-59 -->

### The old fixture was "set -e" plus an echo and no escape...

The old fixture was "set -e" plus an echo and no escape token. That is
not a Law 12 violation -- there is no egress to fail -- and the gate it
certified only ever tested for the substring. This fixture is the real
thing: an unguarded fetch reached with errexit active.

<!-- mios-src:63b36ce0a21b from tests/drift-gate-negatives.sh:982-985 -->

### The defect

The defect: the scan skipped every tree a consumer lives in, so no input
could make this gate fail. One plant per formerly-excluded tree, because
a single one would not show that the exclusion list is gone rather than
merely shorter.
Split so the literal never appears in this file: the names registry
harvests tracked sources, and a fixture name spelled out here lands in
usr/share/mios/referenced_names.txt as if something referenced it.

<!-- mios-src:88ebac152c52 from tests/drift-gate-negatives.sh:1980-1986 -->

### The bare second enforcer in a comma list inherits its file...

The bare second enforcer in a comma list inherits its file rather than
being dropped: the old reader split on comma first and skipped any piece
without a colon, so Law 12's second target was never checked.

<!-- mios-src:32d7b29b500e from tests/drift-gate-negatives.sh:2068-2070 -->

### task_972

task_972: the half a key-only register could not see. POSTGRES_PASSWORD is
GRANDFATHERED, so changing its value used to read as the same entry and
the gate stayed green while an operator's real password sat in a 0644
file under /usr.

<!-- mios-src:f4a756d53e6e from tests/drift-gate-negatives.sh:4004-4007 -->

### Padding

Padding: registering a table that HAS a consumer must fail -- the
register only shrinks, and an entry that no longer reproduces is debt
already paid. `blades` is read by its own fleet-safety gate.

<!-- mios-src:b77e156b31c0 from tests/drift-gate-negatives.sh:4537-4539 -->

### Automated Acoustic Noise Rejection, VAD Accuracy, and...

Automated Acoustic Noise Rejection, VAD Accuracy, and Wake-Word Trigger Benchmark Suite.

Verifies:
1. >98% accuracy (True Positive Rate) on noisy wake-phrase audio ("Hey MiOS").
2. <0.5% false positive rate on ambient noise, silence, and non-wake speech.
3. Low CPU overhead (<0.1% idle overhead, <0.2% on single core benchmark).
4. Stage 1 (RNNoise Suppressor) noise reduction and spectral estimation.
5. Stage 2 (Silero VAD) speech presence probability and hangover smoothing.
6. Stage 3 (OpenWakeWord Detector) acoustic phoneme sequence matching.
7. Downstream streaming STT session signal callback execution.
8. CLI flags: --status, --json, --process-pcm, --threshold, --mock, --daemon.
9. Systemd user service unit configuration.

<!-- mios-src:2047485d57a2 from tests/test-acoustic-wakeword-pipeline.py:4-17 -->

### MiOS Empirical Adversarial Test Harness (Challenger 1)....

MiOS Empirical Adversarial Test Harness (Challenger 1).  Adversarially tests and stress-tests: - pgvector Automated VACUUM & Concurrent HNSW Reindexing (task_345) - CephFS Transactional Ledger Replication & Integrity Hashing (task_346) - CephFS Dynamic Quota Enforcement & Subvolume Sizing (task_347) - Ceph RADOS Gateway Quadlet Isolation (task_348) - LUKS2 / dm-crypt Automated Key Rotation & Safety Rollback (task_349) - PostgreSQL Hot-Standby Streaming Replication & Fencing Coordinator (task_350) - Database Corruption Detector & Non-Destructive Repair Engine (task_351) - Database Schema Migration Runner & Rollback Safety (task_356)

<!-- mios-src:ceb5824e0157 from tests/test-adversarial-t401-t406.py:4-4 -->

### Consolidated Agent Pipe Scheduling Domain Test Suite....

Consolidated Agent Pipe Scheduling Domain Test Suite.

Consolidates:
- WS-AI continuous batch preemption and turn scheduling (test-agent-pipe-preempt.py)
- WS-SCHED agent-pipe token-bucket rate limiter and quotas (test-agent-pipe-quota.py)
- Engine-level priority scheduling and gate drain ordering (test-priority-sched.py)

<!-- mios-src:299549cf650d from tests/test-agent-pipe-scheduling.py:4-10 -->

### Automated unit test suite for MiOS Context & Prompt...

Automated unit test suite for MiOS Context & Prompt Processing.

Consolidates:
- Semantic context compaction & invariant retention (test-context-compactor)
- Priority context window packing & needle heuristics (test-context-trim)
- Contextual prompt compression, code syntax preservation & CLI (test-prompt-pruning)
- Chain-of-thought <think> reasoning tag stripping (test-think-stripper)

<!-- mios-src:f6b2a73a4f1f from tests/test-context-processing.py:4-11 -->

### Consolidated Git Operations Domain Test Suite....

Consolidated Git Operations Domain Test Suite.

Consolidates:
- Differential AST Git merge fuzzing and conflict simulation (test-git-merge-fuzzer.py)
- Git pre-commit linter and commit message hook validator (test-git-pre-commit.py)
- Multi-master Git DAG reconciliation and consensus signing (test-git-reconcile.py)

<!-- mios-src:e22b818cc059 from tests/test-git-ops.py:4-10 -->

### Part 2

==============================================================================
Part 2: Reachability Probe & Posture Behavior (ADR-0016 D8)
==============================================================================

<!-- mios-src:3083d6145b4a from tests/test-greenboot-blade.sh:108-110 -->

### Run cargo in the workspace on any platform. These tests...

Run cargo in the workspace on any platform.

    These tests used to shell into a WSL distro by name and cd to /usr/share/mios,
    so they only ever ran on one machine. Skip when there is no toolchain
    rather than letting its absence look like a pass.

<!-- mios-src:6c5474a14772 from tests/test-mios-check-ssot.py:15-20 -->

### Run the validator's own unit tests over its synthetic...

Run the validator's own unit tests over its synthetic fixtures.

        Named for what it does: these are the crate's fixtures, NOT the live
        mios.toml. The old name claimed the shipped SSOT was being validated
        while asserting only on cargo output, which is the kind of gap this
        repo's gates exist to catch.

<!-- mios-src:4476af675502 from tests/test-mios-check-ssot.py:28-34 -->

### Run a cargo command in the workspace, on whatever platform...

Run a cargo command in the workspace, on whatever platform we are on.

    This suite used to shell into a WSL distro by name ("podman-MiOS-DEV") and
    cd to /usr/share/mios, so it could only pass on one developer's Windows box and
    failed outright on any CI runner. Skip -- loudly -- when there is no
    toolchain, so a missing cargo can never read as a passing dispatcher test.

<!-- mios-src:d2c82383b151 from tests/test-mios-cli-dispatcher.py:14-20 -->

### Consolidated Node Mesh Domain Test Suite (WS-NODE Edge...

Consolidated Node Mesh Domain Test Suite (WS-NODE Edge Micro-Mesh).

Combines and preserves 100% test coverage across 4 core networking subsystems:
1. Async TCP framing, stream buffer reassembly, CRC32 checks, and channel dispatch (TestAsyncNetFraming)
2. Mutual Ed25519 identity authentication, X25519 ECDH key exchange, HKDF-SHA256 session derivation, ChaCha20-Poly1305 AEAD wire encryption, and tamper/imposter rejection (TestNodeCryptoHandshake)
3. Heartbeat monitor, 5s intervals, 3-strike dead peer detection (15s eviction), degraded transitions, routing table pruning, and eviction event listeners (TestNodeHeartbeatEviction)
4. 16-byte fixed binary wire protocol framing, big-endian header packing/unpacking, CRC32 verification, opcode dispatch, and payload limits (TestNodeWireProtocol)

<!-- mios-src:a4f01ca75917 from tests/test-node-mesh.py:5-12 -->

### Unit Test Suite for MiOS UID 1000 Enforcement & Systemd...

Unit Test Suite for MiOS UID 1000 Enforcement & Systemd User Session Boundary.
Implements task_909 / task_3142.

<!-- mios-src:bb546d80dab3 from tests/test-uid-enforcement.py:5-8 -->

### test-virtio-pmem-dax-io.py — task_678 WS-VFIO Automated...

test-virtio-pmem-dax-io.py — task_678 WS-VFIO
Automated benchmark suite for virtio-pmem DAX microVM I/O.

In CI (no Cloud-Hypervisor available) all benchmarks run in dry-run / memory
simulation mode:
  - memfd allocation + mmap read simulates the >15 GB/s memory path
  - time.perf_counter timing asserts sub-25ms "boot" (memfd init) latency

On a real MiOS host with Cloud-Hypervisor:
  - Launches 10 sequential VMs, measures boot-to-init latency
  - Runs in-guest fio read benchmark, asserts >15 GB/s
  - Asserts host NVMe write counters unchanged

<!-- mios-src:20b71922d135 from tests/test-virtio-pmem-dax-io.py:5-18 -->

### The preset script is present when the tree carries it....

The preset script is present when the tree carries it.

        `src/autounattend/*` is git-ignored (.gitignore un-ignores the directory
        and then excludes its contents), so this script exists in a developer's
        working tree but never in a clean checkout -- which is why this assertion
        passed locally and failed on every runner. Tracking it is not the fix
        either: it is 56 lines of PowerShell against a shrink-only ps_lines
        ceiling that Law 14 keeps there deliberately. Skip where it cannot
        exist, and say so, rather than assert a file the repo excludes.

<!-- mios-src:f5bf22f169aa from tests/test-windows-driver-pack.py:21-30 -->

### mios-agents.service runs its ExecStartPre...

mios-agents.service runs its ExecStartPre (mios-agents-firstboot.sh) as root,
and that script seeds code-server settings and an extension into
/var/lib/mios/agents -- the container's coder home, owned by uid 1000 per
tmpfiles.d/mios-agents.conf. Created as root, those paths leave code-server
(running as the home's owner) unable to write its own User/ state.

Positive control: seeding a uid-1000 scratch home as root leaves every seeded
path owned 1000:1000. Negative control: the pre-fix seeding (plain install -d,
install and cp as root) is planted in a scratch copy and the same check must
name a root-owned path. The script is sourced -- it returns before its build
step when sourced -- with its home, settings and extension sources pointed at
the scratch fixture.

<!-- mios-src:8cb122940c72 from usr/libexec/mios/test_mios_agents_firstboot.py:5-17 -->

### Two-sided verification controls for Task task_3434. WHEN...

Two-sided verification controls for Task task_3434.

WHEN forgejo/pgvector/psycopg are unavailable THE SYSTEM SHALL order firstboot
seeders after them and surface the degradation instead of silently skipping.

Each check_* function RUNS the code under test against a scratch fixture and
returns the violations it observed. The positive controls require an empty
list from the shipped files; every negative control plants the pre-fix defect
in a scratch copy and requires the SAME function to name it. Nothing reads the
host: scripts run with their host paths rewritten into the scratch directory,
an empty PATH where they would otherwise find host tools, ambient MIOS_*
scrubbed, psycopg shadowed by a module that refuses to import, and pgvector
pointed at a port nobody listens on.

<!-- mios-src:476233784f4e from usr/libexec/mios/test_mios_firstboot_seeders.py:5-18 -->

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

<!-- mios-src:9f41e90a8cce from usr/libexec/mios/test_mios_mcp_port.py:5-18 -->

### CI-only extras the dev image does not carry: sandbox tests...

CI-only extras the dev image does not carry: sandbox tests (bubblewrap),
composefs sealing (mkcomposefs, composefs-info), unit verification
(systemd-analyze), the lint tier's pwsh and the analyzer's .NET runtime, and
test-ukify-stage's real-compiler tier (ukify + the systemd-boot EFI stub; the
image's own kernel is the input). A suite that cannot run its live tier exits
77, which fails unless [ci.tool_skips] registers it: provide the tool here.

<!-- mios-src:da7776b618c7 from usr/share/mios/mios.toml:12330-12335 -->
### names-registry.py is deleted (task_1713); the probe plants...

names-registry.py is deleted (task_1713); the probe plants the idiom in
any discovered generator instead of sed-replacing an idiom the victim
may not carry.

<!-- mios-src:7c2f7be82948 from tests/drift-gate-negatives.sh:4320-4322 -->

### Empirical Adversarial Stress Test Suite for MiOS iGPU...

Empirical Adversarial Stress Test Suite for MiOS iGPU Inference Lane & RPC Compute Fabric.

Executes adversarial challenges across four core dimensions:
  1. Challenge 1: Localhost isolation & binding.
     - Live port 8540 detection & stale service audit.
     - Rejection of non-localhost connections.
     - Closed port handling & socket reconnection resilience.
  2. Challenge 2: Protocol payload stress.
     - Malformed JSON bodies, syntax errors, raw binary garbage.
     - Missing fields (model, messages), empty prompts, empty arrays.
     - Non-existent models, out-of-range temperatures.
     - Server survivability after adversarial fault injection.
  3. Challenge 3: Hardware routing integrity.
     - DirectX UserGpuPreferences registry verification (GpuPreference=1;).
     - Adversarial regex matrix for Vulkan device enumeration (AMD vs NVIDIA).
     - Live Vulkan device enumeration check.
     - RTX 4090 dGPU VRAM isolation check via nvidia-smi.
  4. Challenge 4: RPC fallback & Vulkan cooperative matrix handling.
     - Enforcement of GGML_VK_DISABLE_COOPMAT=1.
     - Vulkan matrix cores verification on AMD Radeon (matrix cores: none).
     - Raw TCP wire protocol fuzzing & malformed RPC handshake.
     - Coordinator --rpc and --tensor-split configuration validation.

<!-- mios-src:274b29b90bec from tests/test-adversarial-igpu-rpc.py:5-27 -->

### Each consumer runs from a copy whose absolute state paths...

Each consumer runs from a copy whose absolute state paths point into a temp
dir, with network and package tools replaced by stubs that log their calls. The
SSOT values come from the real resolver (usr/libexec/mios/mios-toml-get) reading
the checkout's vendor mios.toml plus a host drop-in dir, so the chain under test
is the one a cloud deployment runs: render -> drop-in -> merge -> gate.

<!-- mios-src:3bcdcac19bdb from tests/test-cloud-overlay.py:5-9 -->

### The dev container IS [image].ref: its Containerfile adds...

The dev container IS [image].ref: its Containerfile adds wiring only, so every
    component it used to install by hand must come from the OS image pipeline.

<!-- mios-src:ca1f418e9b60 from tests/test-code-server-bake.py:341-342 -->

### Comprehensive 4-Tier E2E Test Suite for MiOS iGPU Inference...

Comprehensive 4-Tier E2E Test Suite for MiOS iGPU Inference Lane & RPC Compute Fabric.

Tiers:
  Tier 1: Feature Coverage (F1..F7, >=5 tests each = 35 tests)
  Tier 2: Boundary & Corner Cases (F1..F7, >=5 tests each = 35 tests)
  Tier 3: Pairwise Combinatorial Interactions (8 tests)
  Tier 4: Real-World Application Scenarios (5 scenarios)
Total: 83 test cases.

<!-- mios-src:4d9375571593 from tests/test-igpu-rpc-rust-e2e.py:5-13 -->

### F2: Pure Localhost Binding & Law 5 Standardization

--- F2: Pure Localhost Binding & Law 5 Standardization ---

<!-- mios-src:f4e184efc690 from tests/test-igpu-rpc-rust-e2e.py:392-392 -->

### Validates the security invariant that targets must strictly...

Validates the security invariant that targets must strictly reside within
        the dedicated management subnet 10.200.0.0/16 or 127.0.0.1 for testing.
        Public IPs and non-management networks must be unconditionally rejected.

<!-- mios-src:74b768327f19 from tests/test-ipkvm-manager.py:127-131 -->

### Validates authentication enforcement

Validates authentication enforcement: unauthenticated requests without token
        must be rejected unless in dry-run mode with explicit bypass (--allow-unauthenticated).

<!-- mios-src:faa49a38b130 from tests/test-ipkvm-manager.py:173-176 -->

### The path-derived naming (MIOS_<TABLE>_<KEY>) made...

The path-derived naming (MIOS_<TABLE>_<KEY>) made MIOS_URLS_<KEY> the
canonical name and left MIOS_<KEY>_URL an accepted INPUT alias. Decision
1's invariant survives the rename: one address, one name its consumers
read. Both spellings having readers is the second scheme it forbids.

<!-- mios-src:ce6a9eea8e3c from tests/test-offload-overlay.py:171-174 -->

### searxng and forge consumers moved to the canonical...

searxng and forge consumers moved to the canonical spelling; a reader
going back to the alias -- or to a hand-composed address -- fails here.
If this fails, revisit ADR-0016 Decision 1 rather than deleting it.

<!-- mios-src:aa4dafff1bc3 from tests/test-offload-overlay.py:182-184 -->

### Entrypoint shim

Entrypoint shim: delegates to the canonical consolidated suite.

This filename stays because it is an externally referenced entrypoint
(registered in ``[ci.tiers] unit`` in usr/share/mios/mios.toml and mirrored in
usr/share/mios/ai/v1/metadata.json), but ALL refine guard coverage --
including this file's former live chat-promotion smoke cases
("mios-open-url https://www.wikipedia.org", "https://example.com",
"git status") -- now lives once, losslessly, in
``tests/test-refine-guards.py``, which exercises the REAL production
``refine_intent`` (imported from ``usr/lib/mios/agent-pipe/mios_refine.py``)
rather than an inline copy.

Running this shim == running the canonical suite (offline deterministic
guards verified; live integration tests SKIP explicitly unless
``MIOS_REFINE_LIVE_ENDPOINT`` is set).

<!-- mios-src:9e94a75ddae3 from tests/test-refine-guard.py:4-19 -->

### Refine post-parse guard suite (canonical, consolidated)....

Refine post-parse guard suite (canonical, consolidated).

Covers the REAL production guard -- ``refine_intent`` imported from its real
module path (``usr/lib/mios/agent-pipe/mios_refine.py`` re-exporting
``mios_pipe/routing/refine.py``) -- NOT any inline copy of its logic.

Two layers, honestly separated:

* Deterministic offline tests drive the real ``refine_intent`` through a
  canned OpenAI-compatible transport (the stub recipe the upstream unit suite
  ``test_mios_refine.py`` uses), so they need NO live model endpoint. They
  prove positive and negative controls for every guard: chat-promotion for
  actionable text, long-prompt promotion, short-dispatch passthrough,
  wordy-arg (arg-shape) demotion, multi_task shape repair, and
  malformed/failure behavior (backend error, unparseable prose, empty
  content -> None, never a fabricated result).

* Live integration tests (3, named ``test_live_*``) exercise the deployed
  stack through ``server.refine_intent`` (the traced production entrypoint).
  They are OPT-IN via ``MIOS_REFINE_LIVE_ENDPOINT``. When that env var is
  absent they SKIP explicitly -- under pytest as a reported skip, standalone
  as a printed ``[SKIP]`` line that is never counted as a passed check.

Standalone entrypoint (run-suites.sh unit tier)::

    python3 tests/test-refine-guards.py      # pass=N skip=M fail=K summary

History: this file consolidated (losslessly) the former
tests/test-refine-guards.py (3 cases) and tests/test-refine-guard.py
(3 chat-promotion smoke cases). The old case 3 validated an INLINE COPY of
the wordy-arg guard instead of the production function -- that defect is
fixed here by driving the real ``refine_intent`` with the forged envelope.

<!-- mios-src:0c5b20cb9280 from tests/test-refine-guards.py:4-36 -->

### Positive control

Positive control: a binary payload compressed by the production encoder
        must be a genuine Zstandard frame decodable by an INDEPENDENT decoder
        (zstd CLI when installed, otherwise the python zstandard module) and
        must round-trip byte-for-byte.

<!-- mios-src:7a90f183cc31 from tests/test-storage.py:237-242 -->

### Negative control

Negative control: when the located encoder binary exits non-zero, the
        operation must fail with ZstdCompressionError (no silent fallback to a
        fake frame). Uses the real Python interpreter as a failing "zstd" binary:
        it rejects zstd-style flags and exits with status 2.

<!-- mios-src:c7d295e00992 from tests/test-storage.py:387-392 -->

### Negative control

Negative control: corrupted, garbage, and mislabeled (zlib-with-zstd-magic)
        payloads must be rejected by the restore path, and a corrupted remote store
        chunk must fail verify_remote_manifest.

<!-- mios-src:cc942d15ffcc from tests/test-storage.py:427-431 -->

### Harness self-proof

Harness self-proof: a PLANTED invalid frame (exactly the shape the old
        defect produced: zlib bytes behind a zstd magic prefix) MUST fail the
        independent round-trip decoder and the production restore consumer.
        If this test ever passes silently, the round-trip harness is broken.

<!-- mios-src:8cb1ef5b66d7 from tests/test-storage.py:489-494 -->

### Validates magic packet byte construction

Validates magic packet byte construction:
          - assert length == 108 bytes
          - starts with 6x 0xFF (b'\xff' * 6)
          - contains 16x MAC address (mac_bytes * 16)
          - ends with 6x SecureON password payload (secureon_bytes)

<!-- mios-src:d6049d6609aa from tests/test-wol-proxy.py:70-76 -->

### Adversarial Empirical Challenge Suite for MiOS Gateway...

Adversarial Empirical Challenge Suite for MiOS Gateway Context Budgeting & Tool Deduplication.
Empirically stress tests:
- 50,000 token massive prompts (single message, 100 turns, giant system, unicode)
- 193 tools request handling, tool cap suppression, and schema resilience
- tool_choice: "none" stripping with 169 tools (0 tool tokens in backend dispatch)
- Client tools matching MiOS verbs or exceeding DEFAULT_TOOL_CAP suppressing _mios_sel
- Empty and malformed request bodies (HTTP 400 verification and crash resilience)
- Context pruning reducing >32k payloads safely below 32,768 without HTTP 400

<!-- mios-src:ee6262ade3e4 from tests/test_adversarial_gateway_stress.py:3-12 -->

### Adversarial stress harness for mios-hardcode-lint (task_3454...

Adversarial stress harness for mios-hardcode-lint (task_3454 parity and defect detection).

Executes four targeted challenge dimensions:
1. Header crash-risks (stranded BOMs at various offsets, shebang displacements).
2. Date attribution in string literals vs values (multiline, raw, docstrings, markdown URLs).
3. IP address heuristics (IPv4 edge cases, CIDR boundaries, IPv6, port syntaxes).
4. Error behavior & filesystem corner cases (non-existent paths, empty dirs, binary files, syntax errors).

<!-- mios-src:f2ac384f1a43 from tests/test_adversarial_hardcode_lint.py:4-11 -->

### This tree's mios-hardcode-lint, else the installed one. The...

This tree's mios-hardcode-lint, else the installed one.

    The native engine writes release builds under the SSOT target triple
    ([build.native.linux].targets / [build.native.windows].target); a plain
    cargo build writes target/{release,debug}. The native install puts the
    binary on PATH, which is what CI has after its native stage.

<!-- mios-src:fbad487a5a81 from tests/test_adversarial_hardcode_lint.py:39-45 -->

### Step 4

Step 4: ADVERSARIAL INJECTION & HOSTILE STRINGS:
- Injection of Rich markup tags: '[bold red]PWNED[/bold red]', '[[brackets]]', '[/]'
- Injection of Rich closing tags in identity: '[/cyan]pane:bad'
- Unicode and emoji: '💥 rm -rf / ; ⚡ <xml>'
- String PID, 0 PID, negative PID
- Empty cmd and empty identity

<!-- mios-src:545cf678124d from tests/test_adversarial_m2_monitor_tui.py:222-227 -->

### Comprehensive Empirical Adversarial Challenge Suite for...

Comprehensive Empirical Adversarial Challenge Suite for Milestone M3:
R4 Agent-Pipe Gateway Context Budgeting & Tool De-duplication.

Adversarial Stress Test Matrix:
1. Tool Choice 'none' Casing & Edge Cases:
   - 'none', 'NONE', ' None ', 'None', '	
 NONE 
' -> STRIPPED (0 tool tokens)
   - '', None, 'auto', 'AUTO', 'required' -> RETAINED (Negative controls)
   - Object/dict tool_choice: {'type': 'function', 'function': {'name': 'none'}} -> RETAINED
2. Client Supplying 150+ Tools:
   - 175 client tools supplied -> _mios_sel is empty ([])
   - 175 client tools with 35 duplicates -> deduplicated to 140 unique tools, 0 duplicate schemas
3. Collision with MiOS Verbs:
   - Client supplies tools overlapping with MiOS verbs ('run_command', 'read_file', 'app_search')
   - Client supplies only 2 tools matching verbs (< DEFAULT_TOOL_CAP) -> _mios_sel is still suppressed
   - Injected verbs with same name filtered by client_names and seen_names
4. Two-Sided Negative Control:
   - tool_choice: 'auto' does NOT strip tools
   - tool_choice omitted does NOT strip tools
5. Relay and Streaming Relay Ingress:
   - _client_tools_relay and _client_tools_stream_relay strip tools on case-insensitive 'none'

<!-- mios-src:4b277c1d6660 from tests/test_adversarial_m3_challenger.py:3-24 -->

### Comprehensive 4-Tier E2E Test Suite for MiOS Gateway...

Comprehensive 4-Tier E2E Test Suite for MiOS Gateway, Wallpaper & Rust Consolidation.

Tiers:
  Tier 1: Feature Coverage (F1..F13, >=5 tests each = 65 tests)
  Tier 2: Boundary & Corner Cases (B1..B13, >=5 tests each = 65 tests)
  Tier 3: Pairwise Combinatorial Interactions (10 tests)
  Tier 4: Real-World Application Scenarios (5 scenarios)
Total: 145 test cases.

<!-- mios-src:d1639dc8aebb from tests/test_gateway_wallpaper_rust_e2e.py:5-13 -->

### Executable model of the MiOS Gateway Ingress & Token...

Executable model of the MiOS Gateway Ingress & Token Budgeting contract.
    
    Implements F1-F4 specification rules:
    - F1: When tool_choice == 'none', strips tools and tool_choice from payload.
    - F2: Evaluates _has_client_tools as False when tool_choice == 'none'.
    - F3: Suppresses _mios_sel when caller tools >= DEFAULT_TOOL_CAP (24) or caller tools match MiOS verbs.
    - F4: Enforces 32,768 context limit, prunes stale tool results, compacts messages, clamps max_tokens.

<!-- mios-src:b1792f2f9f42 from tests/test_gateway_wallpaper_rust_e2e.py:81-88 -->

### Comprehensive parity and two-sided verification test suite...

Comprehensive parity and two-sided verification test suite for mios-hardcode-lint.

Validates the full behavioral contract of Architectural Law 7 (NO-HARDCODE enforcement):
- CLI invocation, arguments, exit codes, and stdout/stderr formatting.
- Date detection in Python comments, module/function/class docstrings, and string literal prose.
- Legitimate date value exemptions (leading quote, slugs, URLs).
- Header crash-risks (stranded UTF-8 BOM in .ps1, shebang line displacement in .sh).
- Routable IP detection vs private/loopback/CGNAT exemptions.
- Port literal detection vs bracketed/arithmetic/URL exemptions.
- SSOT allowlist integration from usr/share/mios/mios.toml.
- GENERATED file banner exemption.
- Ventoy plaintext credential detection.
- Parity between Python oracle and compiled Rust static binary.

<!-- mios-src:d769f8940ee2 from tests/test_hardcode_lint_parity.py:5-18 -->

### Comprehensive 4-tier E2E test suite for MiOS Native Static...

Comprehensive 4-tier E2E test suite for MiOS Native Static Binaries Hardening and Consolidation.

Tier 1: Feature Coverage (F1..F10, >=5 tests each = 50 tests)
Tier 2: Boundary & Corner Cases (F1..F10, >=5 tests each = 50 tests)
Tier 3: Pairwise Combinatorial Interactions (10 tests)
Tier 4: Real-World Application Scenarios (5 tests)
Total: 115 test cases.

<!-- mios-src:290289b062ae from tests/test_native_static_hardening_e2e.py:5-12 -->
### The fixture sandbox after `migrate-ids`: opaque ids...

The fixture sandbox after `migrate-ids`: opaque ids, numbers, typed labels. K-404 names no task, so the
operator decision maps it (as the real migration does for its dangling tokens).

<!-- mios-src:ac5acea360d7 from tools/native/mios-task/tests/cli.rs:1393-1394 -->
