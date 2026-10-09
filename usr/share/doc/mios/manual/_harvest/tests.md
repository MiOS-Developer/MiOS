<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### AI-hint

AI-hint: Comprehensive adversarial stress tests for Milestone 1 components in mios-node.
AI-related: src/mios-rs/mios-node/src/crypto.rs, src/mios-rs/mios-node/src/hardware.rs, src/mios-rs/mios-node/src/cgroups.rs, src/mios-rs/mios-node/src/state_sync.rs, src/mios-rs/mios-node/src/watchdog.rs

<!-- mios-src:42afa13e1bba from src/mios-rs/mios-node/tests/adversarial_m1_test.rs:1-2 -->

### AI-hint

AI-hint: Deep adversarial stress tests and property fuzzers for Milestone 2 (task_336 through task_340).
AI-related: src/mios-rs/mios-node/src/scheduler.rs, src/mios-rs/mios-node/src/buffer_pool.rs, src/mios-rs/mios-node/src/capabilities.rs, src/mios-rs/mios-node/src/ble.rs, src/mios-rs/mios-node/src/overlay.rs

<!-- mios-src:dbef77c6e66b from src/mios-rs/mios-node/tests/m2_deep_adversarial_stress_test.rs:1-2 -->

### AI-hint

AI-hint: Comprehensive adversarial integration tests for Milestone 2 (task_336 through task_340).
AI-related: src/mios-rs/mios-node/src/scheduler.rs, src/mios-rs/mios-node/src/buffer_pool.rs, src/mios-rs/mios-node/src/capabilities.rs, src/mios-rs/mios-node/src/ble.rs, src/mios-rs/mios-node/src/overlay.rs

<!-- mios-src:0c059a3f00db from src/mios-rs/mios-node/tests/mesh_m2_adversarial_test.rs:1-2 -->

### AI-hint

AI-hint: Milestone 2 Empirical Challenger Stress & Adversarial Test Suite.
AI-related: src/mios-rs/mios-node/src/scheduler.rs, src/mios-rs/mios-node/src/buffer_pool.rs, src/mios-rs/mios-node/src/capabilities.rs, src/mios-rs/mios-node/src/ble.rs, src/mios-rs/mios-node/src/overlay.rs

<!-- mios-src:56bba13030a2 from src/mios-rs/mios-node/tests/mesh_m2_stress_challenger_test.rs:1-2 -->

### AI-hint

AI-hint: Integration tests for `miosd render-kargs` -- the kernel command line is the highest-consequence projection in the build, so the unmanaged-karg preservation contract is pinned here.
AI-related: src/mios-rs/miosd/src/main.rs, automation/75-kargs-render.sh, usr/lib/bootc/kargs.d, usr/share/mios/mios.toml

<!-- mios-src:2ef1b3ed4d9b from src/mios-rs/miosd/tests/render_kargs.rs:1-2 -->

### !/usr/bin/env bash AI-hint: bash Negative-test harness for...

!/usr/bin/env bash
AI-hint: bash Negative-test harness for the new drift gates. Inject violations, assert they fail, restore, and assert pass.
AI-doc: usr/share/doc/mios/manual/tests.md
AI-convention: Probe values in negative tests must be assembled dynamically (e.g. string concatenation or printf) so literals never match static grep scans in 98-drift-checks.sh.

<!-- mios-src:dc1734205da1 from tests/drift-gate-negatives.sh:1-4 -->

### !/usr/bin/env bash AI-hint: Runs one registered suite tier...

!/usr/bin/env bash
AI-hint: Runs one registered suite tier to completion and reports every failure, so a single run says everything that is wrong.
AI-related: tools/ci-suites.py, usr/share/mios/mios.toml

Sequential steps stop at the first failure, so each red run taught exactly one
thing and the next failure cost another round trip. This runs the whole tier
and reports all of it. Suites come from [ci] in the SSOT, which is also what
check_ci_suite_coverage enforces, so a publisher cannot quietly run a
different set from its sibling.

<!-- mios-src:66212519c452 from tests/run-suites.sh:1-9 -->

### !/usr/bin/env python3 AI-hint: Consolidated unit test suite...

!/usr/bin/env python3
AI-hint: Consolidated unit test suite for MiOS A2A (Agent-to-Agent) federation: Ed25519 cryptographic attestation, mutual capability handshake, and identity-aware delegation (task_3353 / GATECAT-01).
AI-related: usr/libexec/mios/a2a/attestation.py, usr/lib/mios/agent-pipe/mios_a2a_delegation.py

<!-- mios-src:7ca4f0094eef from tests/test-a2a.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Adversarial testing suite...

!/usr/bin/env python3
AI-hint: Adversarial testing suite and empirical challenge harness for task_321..task_325 modules.
AI-related: usr/libexec/mios/mcp/sandbox.py, usr/libexec/mios/sec/approval.py, usr/libexec/mios/graph/traversal.py, usr/libexec/mios/prompt/pruning.py, usr/libexec/mios/a2a/attestation.py

<!-- mios-src:47a9f22f5b81 from tests/test-adversarial-roadmap.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Comprehensive empirical...

!/usr/bin/env python3
AI-hint: Comprehensive empirical adversarial test suite for task_345..task_350 (Database, Storage, Encryption, and Replication).
AI-related: usr/libexec/mios/db/mios-pgvector-optimize.py, usr/libexec/mios/storage/mios-ledger-sync, usr/libexec/mios/storage/mios-cephfs-quota, usr/libexec/mios/db/mios-pg-replica.py, usr/libexec/mios/db/mios-db-doctor.py, usr/libexec/mios/db/mios-db-migrate.py, usr/libexec/mios/sec/mios-luks-rotate

<!-- mios-src:9e9888a93488 from tests/test-adversarial-t401-t406.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Consolidated unit test suite...

!/usr/bin/env python3
AI-hint: Consolidated unit test suite for MiOS Agent Pipe Scheduling domain: continuous batch preemption, quantum slicing, token-bucket quotas, and engine-level priority routing (task_3353 / GATECAT-01).
AI-related: usr/lib/mios/agent-pipe/server.py, usr/lib/mios/agent-pipe/mios_sched.py, usr/lib/mios/agent-pipe/mios_quota.py, usr/lib/mios/agent-pipe/mios_priority_sched.py

<!-- mios-src:6bb83f4db3bc from tests/test-agent-pipe-scheduling.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Law 10 BARE-SAFE-ENV unit...

!/usr/bin/env python3
AI-hint: Law 10 BARE-SAFE-ENV unit test. Drives system-sync-env.sh emit() directly, one case per unsafe character, so the REJECT path is exercised where it can actually fail.
AI-related: usr/libexec/mios/system-sync-env.sh, automation/99-postcheck.sh, usr/share/mios/mios.toml
AI-doc: usr/share/doc/mios/manual/tests.md

<!-- mios-src:43c051f149c5 from tests/test-baresafe-emit.py:1-4 -->

### !/usr/bin/env python3 AI-hint: Automated unit test suite...

!/usr/bin/env python3
AI-hint: Automated unit test suite for MiOS Context & Prompt Processing domain (task_3353 / GATECAT-01).
AI-related: usr/lib/mios/agent-pipe/context_compactor.py, usr/lib/mios/agent-pipe/mios_pipe/context/ctxpack.py, usr/libexec/mios/prompt/pruning.py, usr/lib/mios/agent-pipe/mios_pipe/routing/turn.py

<!-- mios-src:d2b2b8f5c3ca from tests/test-context-processing.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Consolidated database test...

!/usr/bin/env python3
AI-hint: Consolidated database test suite: backup/retention, db doctor/corruption, migrations/rollback, SSOT materialization, PG events, streaming replica, autovacuum tuner, pgvector halfvec HNSW, and pgvector vacuum/reindexing.
AI-related: usr/libexec/mios/db/, usr/lib/mios/ai/pgvector_hnsw.py, tests/test-db.py

<!-- mios-src:1c8779946248 from tests/test-db.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Consolidated unit test suite...

!/usr/bin/env python3
AI-hint: Consolidated unit test suite for MiOS Git Operations domain (AST merge fuzzing, pre-commit lint hooks, and multi-master DAG reconciliation) (task_3353 / GATECAT-01).
AI-related: usr/libexec/mios/git/merge_fuzzer.py, usr/libexec/mios/git/pre_commit.py, usr/libexec/mios/git/reconcile_dag.py

<!-- mios-src:b568ee903d4d from tests/test-git-ops.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Consolidated unit test suite...

!/usr/bin/env python3
AI-hint: Consolidated unit test suite for MiOS Looking Glass & Display domain (task_3353 / GATECAT-01).
AI-related: usr/libexec/mios/display/looking_glass.py, usr/libexec/mios/vfio/setup-looking-glass.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md

<!-- mios-src:2f2a64e76aff from tests/test-looking-glass.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Automated unit test suite...

!/usr/bin/env python3
AI-hint: Automated unit test suite for multi-monitor Looking Glass display geometry and cursor synchronizer.
AI-related: usr/libexec/mios/display/multimonitor_sync.py, usr/share/doc/mios/manual/ch67-discrete-gpu-vfio-looking-glass-and-displays.md

<!-- mios-src:ae1b18dd9f73 from tests/test-multimonitor-sync.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Automated unit test suite...

!/usr/bin/env python3
AI-hint: Automated unit test suite for Task task_525/task_526 Nix Subsystem and Declarative Flake Projection Generator.
AI-related: usr/libexec/mios/config/nix_project.py, usr/share/mios/nix/flake-template.nix, usr/share/mios/nix/nix.conf, usr/lib/tmpfiles.d/50-nix.conf, automation/59-tools.sh

<!-- mios-src:594db94b34ea from tests/test-nix-project.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Consolidated unit and...

!/usr/bin/env python3
AI-hint: Consolidated unit and integration test suite for WS-NODE edge mesh networking: async TCP framing, Ed25519/X25519/ChaCha20-Poly1305 crypto handshake, heartbeat eviction, and binary wire protocol.
AI-related: usr/libexec/mios/node/wire.py, usr/libexec/mios/node/crypto.py, usr/libexec/mios/node/discovery.py, usr/libexec/mios/node/mios-node-wire.py
AI-doc: usr/share/doc/mios/manual/node.md

<!-- mios-src:8af63b296f7f from tests/test-node-mesh.py:1-4 -->

### AI-hint

AI-hint: Tests for task_288: mios_reputation — IntrospecLOO marginal contribution.
AI-related: mios_reputation
AI-functions: _make_contributions, test_records_returned_for_all_peers, test_low_contributor_gets_low_delta, test_eval_count_increments, test_sorted_peers_descending

<!-- mios-src:728319c1eeea from tests/test-reputation.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Consolidated unit test suite...

!/usr/bin/env python3
AI-hint: Consolidated unit test suite for MiOS Storage & Ceph domain (Bcachefs tiering, CephFS provisioning, active-active MDS, and RADOS Gateway).
AI-related: usr/libexec/mios/storage/, usr/libexec/mios/mios-cephfs-provision, usr/share/containers/systemd/mios-radosgw.container

<!-- mios-src:a078ac3e1ad6 from tests/test-storage-ceph.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Unit and regression test...

!/usr/bin/env python3
AI-hint: Unit and regression test suite for test-uid-enforcement functionality.
AI-functions: test_generate_sysusers_remediation, test_audit_user_environment_mock_valid, test_audit_user_environment_mock_system_uid, test_subuid_subgid_parsing, TestUidEnforcement

<!-- mios-src:c1cbe808e13d from tests/test-uid-enforcement.py:1-3 -->

### !/usr/bin/env python3 AI-hint: Consolidated unit test suite...

!/usr/bin/env python3
AI-hint: Consolidated unit test suite for MiOS Windows Unattended domain (autounattend.xml generation, hardware bypass injection, and schema validation) (task_3353 / GATECAT-01).
AI-related: usr/libexec/mios/win/unattend_gen.py, usr/libexec/mios/win/unattend_validate.py, autounattend.xml

<!-- mios-src:d6390d1a9553 from tests/test-unattend.py:1-3 -->

### AI-hint

AI-hint: Unit and regression test suite for test-virtio-pmem-dax-io functionality.
AI-related: mios_microvm
AI-functions: _simulate_memfd_throughput_gbs, test_memfd_init_latency_under_25ms, test_dax_io_throughput_exceeds_15gbs, test_destroy_releases_memfd_no_nvme_writes, _nvme_write_sectors

<!-- mios-src:57e1dd20f13e from tests/test-virtio-pmem-dax-io.py:1-3 -->
