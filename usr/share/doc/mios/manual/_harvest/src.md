<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### AI-hint

AI-hint: Law-11 extension gate: fails any credential literal baked into a world-readable unit whose exact path:KEY=value is not on the shrink-only register.
AI-related: usr/share/mios/mios.toml, usr/share/containers/systemd, usr/lib/systemd/system, tools/generate-pod-quadlets.py

<!-- mios-src:a983aec5a9ee from src/mios-rs/mios-gate/src/credentials.rs:1-2 -->

### AI-hint

AI-hint: Asserts every path named in an AI-related/AI-doc header or a markdown link resolves in the repository; the corpus is the TRACKED tree, so the verdict never moves with a developer's untracked working copies.
AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

<!-- mios-src:64b788433b3e from src/mios-rs/mios-gate/src/doc_refs.rs:1-2 -->

### AI-hint

AI-hint: Asserts every top-level mios.toml table has an access-shaped consumer or sits in the shrink-only [ssot_tables] register; subscript evidence requires the subscripted value to BE the parsed SSOT.
AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

<!-- mios-src:36932f84546d from src/mios-rs/mios-gate/src/inert_tables.rs:1-2 -->

### AI-hint

AI-hint: Asserts every [laws].enforced_by pointer resolves to live enforcement -- a function definition in the drift script, or a marker in executable text in the postcheck, never a comment.
AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, automation/99-postcheck.sh

<!-- mios-src:232f48b91be4 from src/mios-rs/mios-gate/src/laws.rs:1-2 -->

### AI-hint

AI-hint: Entry point for mios-gate, the native drift-gate binary; dispatches one named check and reports text or an OpenAI-format structured object.
AI-related: src/mios-rs/mios-gate/src/dispatch.rs, automation/98-drift-checks.sh, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md

<!-- mios-src:0c20f6aedc70 from src/mios-rs/mios-gate/src/main.rs:1-2 -->

### AI-hint

AI-hint: Asserts every automation/NN-*.sh on disk is registered in mios.toml [build.phases].list, or is named on the shrink-only unregistered register with a reason.
AI-related: usr/share/mios/mios.toml, automation/build.sh, automation/55-native-build.sh, TASKS.md

<!-- mios-src:9076429c9bff from src/mios-rs/mios-gate/src/phases.rs:1-2 -->

### AI-hint

AI-hint: Asserts the Law 8 projection registry is complete in BOTH directions -- every generator the discovery globs find is registered or itemised exempt, and every registered entry names a check that exists.
AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tools/drift-checks.py, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md

<!-- mios-src:ba5a1bcf5a29 from src/mios-rs/mios-gate/src/projreg.rs:1-2 -->

### AI-hint

AI-hint: Law-9 extension gate: every bare ${MIOS_*} the Quadlet renderer leaves unbaked must be supplied at runtime by the unit's own [Service] Environment= or by the install.env system-sync-env renders.
AI-related: tools/native/mios-render-quadlets/src/main.rs, usr/libexec/mios/system-sync-env.sh, usr/share/mios/mios.toml

<!-- mios-src:5dc2b3d53d97 from src/mios-rs/mios-gate/src/protected_refs.rs:1-2 -->

### AI-hint

AI-hint: Asserts shrink-only ceilings in mios.toml never increase over HEAD, and that a ceiling exempted as a generated budget says so in SSOT rather than by being quietly skipped.
AI-related: usr/share/mios/mios.toml, src/mios-rs/mios-gate/tests/ratchet.rs, automation/98-drift-checks.sh

<!-- mios-src:ba66c07cd7ea from src/mios-rs/mios-gate/src/ratchet.rs:1-2 -->

### AI-hint

AI-hint: Asserts every tracked file carrying a ${MIOS_*} placeholder has an extension the Quadlet renderer actually substitutes, so an omitted extension cannot ship a unit systemd will not parse.
AI-related: usr/share/mios/mios.toml, automation/34-render-quadlets.sh, usr/lib/systemd/system/

<!-- mios-src:d6c2eb6ef9c2 from src/mios-rs/mios-gate/src/rendercov.rs:1-2 -->

### AI-hint

AI-hint: Asserts usr/lib/containers/policy.json is byte-identical to what [security.sigstore] projects, the regenerate-and-diff half of Law 8 that this surface never had.
AI-related: usr/share/mios/mios.toml, usr/lib/containers/policy.json, tools/generate-cosign-policy.py, automation/49-cosign-policy.sh

<!-- mios-src:8e2c94407bf0 from src/mios-rs/mios-gate/src/sigpolicy.rs:1-2 -->

### AI-hint

AI-hint: Asserts no hardcoded version literal in automation/, usr/libexec/ or tools/ diverges from the mios.toml [meta].mios_version SSOT; Rust twin of the retired python scanner, consolidated beside doc_refs so one implementation decides one verdict.
AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, src/mios-rs/mios-gate/src/doc_refs.rs

<!-- mios-src:e1619ce0e439 from src/mios-rs/mios-gate/src/version_literals.rs:1-2 -->

### AI-hint

AI-hint: BLE beaconing for offline local mesh bootstrap for mios-node (task_339 / task_2572).
AI-related: src/mios-rs/mios-node/src/crypto.rs, usr/libexec/mios/node/ble.py, tests/test-node.py
! MiOS BLE Beaconing & Offline Local Mesh Bootstrap Engine
!
! Implements GATT service/characteristic definitions for headless edge blades,
! ephemeral X25519 Diffie-Hellman key exchange, HKDF-SHA256 key derivation,
! ChaCha20-Poly1305 AEAD encrypted credential provisioning, and mockable hardware adapter.

<!-- mios-src:aa8b8e02c1ad from src/mios-rs/mios-node/src/ble.rs:1-7 -->

### AI-hint

AI-hint: Zero-copy network buffer pooling for mios-node frames (task_337 / task_2570).
AI-related: src/mios-rs/mios-node/src/net.rs, usr/libexec/mios/node/buffer_pool.py, tests/test-node.py
! MiOS Zero-Copy Network Buffer Pool
!
! Provides bucketed pre-allocation (Small 256B, Medium 4KB, Large 64KB, Huge 1MB),
! RAII auto-recycling via drop guards, bounded memory footprint, zero-copy slicing,
! and allocation telemetry.

<!-- mios-src:2b4d562ce3fe from src/mios-rs/mios-node/src/buffer_pool.rs:1-7 -->

### AI-hint

AI-hint: Edge node capability advertising in Announce frames for mios-node (task_338 / task_2571).
AI-related: src/mios-rs/mios-node/src/protocol.rs, usr/libexec/mios/node/capabilities.py, tests/test-node.py
! MiOS Edge Node Capability Advertising & Telemetry Engine
!
! Encapsulates Opcode 0x02 `NodeAnnounce` payloads with CPU, RAM, GPU/VRAM telemetry,
! execution tiers (Wasm, Native), active mesh transports, hardware interfaces (GPIO/I2C),
! capability probing, and cluster capability registry.

<!-- mios-src:1644dc1657de from src/mios-rs/mios-node/src/capabilities.rs:1-7 -->

### AI-hint

AI-hint: Dynamic CPU Core Pinning and Cgroup v2 limits controller for mios-node workers.
AI-related: src/mios-rs/mios-node/src/node.rs, usr/libexec/mios/node/cgroups.py, tests/test-node.py
! MiOS Dynamic Worker CPU Affinity and Cgroup v2 Controller
! Manages CPU core pinning, cgroup v2 quotas (cpu.max, memory.max), and enforces Core 0 system reservation.

<!-- mios-src:24fe8a96c756 from src/mios-rs/mios-node/src/cgroups.rs:1-4 -->

### AI-hint

AI-hint: Ed25519 mutual handshake, X25519 ECDH key exchange, HKDF-SHA256 key derivation, and ChaCha20-Poly1305 wire AEAD for mios-node.
AI-related: src/mios-rs/mios-node/src/net.rs, src/mios-rs/mios-node/src/protocol.rs, tests/test-node-crypto-handshake.py
! MiOS Node Cryptographic Handshake & Wire Encryption Engine (task_332 / task_2565)
!
! Provides mutual identity authentication using Ed25519 signatures, forward secrecy via X25519
! ephemeral Diffie-Hellman key exchange, HKDF-SHA256 session key derivation, and ChaCha20-Poly1305
! authenticated symmetric frame payload encryption.

<!-- mios-src:5f49f7cb8844 from src/mios-rs/mios-node/src/crypto.rs:1-7 -->

### AI-hint

AI-hint: Dual-tier task execution engine (Wasm sandbox & signed native modules) for mios-node.
AI-related: src/mios-rs/mios-node/src/node.rs
! MiOS Dual-Tier Task Sandboxing & Execution Engine
! Tier 1: WebAssembly / Bytecode Sandboxed Execution Engine with mios_sys_* host API bindings
! Tier 2: Dynamic Native Module Loader with Ed25519 signature checks and architecture verification

<!-- mios-src:825d6f82b0b4 from src/mios-rs/mios-node/src/executor.rs:1-5 -->

### AI-hint

AI-hint: Hardware Abstraction Layer & Wasm host imports for GPIO and I2C with allowlist enforcement.
AI-related: src/mios-rs/mios-node/src/executor.rs, usr/libexec/mios/node/hardware.py, usr/libexec/mios/node/wasm_sandbox.py
! MiOS Edge Node Hardware Abstraction Layer (HAL) & Wasm Sandbox Host Imports
! Enforces strict allowlist permissions for GPIO and I2C interactions from sandboxed Wasm guests.

<!-- mios-src:fec4cd06a04a from src/mios-rs/mios-node/src/hardware.rs:1-4 -->

### AI-hint

AI-hint: Node heartbeat monitor, 3-strike dead peer detection, and routing table eviction for mios-node.
AI-related: src/mios-rs/mios-node/src/node.rs, src/mios-rs/mios-node/src/lib.rs, tests/test-node-heartbeat-eviction.py
! MiOS Node Heartbeat Monitor & Dead-Peer Eviction Engine (task_331 / task_2564)
!
! Enforces the 5s heartbeat interval, 3-strike failure rule (15s eviction threshold),
! degraded status transitions at 2 strikes (10s), lock-free table pruning, and event dispatch.

<!-- mios-src:e61df551315d from src/mios-rs/mios-node/src/heartbeat.rs:1-6 -->

### AI-hint

AI-hint: Async Tokio TCP frame reader, writer, stream buffer manager, and network actor for mios-node.
AI-related: src/mios-rs/mios-node/src/protocol.rs, src/mios-rs/mios-node/src/lib.rs, tests/test-node-async-net.py
! MiOS Async TCP Frame Reader, Writer & Network Actor
!
! Implements high-concurrency, asynchronous TCP stream framing over the 16-byte fixed binary header
! wire protocol (Magic 0x4D49, Version 1, Opcode, NodeID, PayloadLen, CRC32).

<!-- mios-src:d45a7369674b from src/mios-rs/mios-node/src/net.rs:1-6 -->

### AI-hint

AI-hint: Automated fallback to Tailscale and WireGuard overlay when LAN broadcast is partitioned (task_340 / task_2573).
AI-related: src/mios-rs/mios-node/src/heartbeat.rs, usr/libexec/mios/node/overlay.py, tests/test-node.py
! MiOS Multi-Transport Router & LAN Partition Overlay Failover Engine
!
! Implements multi-transport routing across Direct LAN, WireGuard overlay, Tailscale mesh,
! and Direct TCP, featuring 3-strike LAN partition failure detection and asymmetric
! anti-flap recovery hysteresis (120s recovery dwell from `[blade.collapse]`).

<!-- mios-src:f0ae42b193ce from src/mios-rs/mios-node/src/overlay.rs:1-7 -->

### AI-hint

AI-hint: 16-byte fixed header binary wire protocol parser and generator for mios-node.
AI-related: src/mios-rs/mios-node/src/node.rs
! MiOS Binary Wire Protocol Specification & Framing Engine
! Header Format (16 Bytes Fixed, Big-Endian Network Byte Order):
!
! +-------------------------------------------------------------------+
! | Magic (2B: 0x4D 0x49) | Ver (1B) | MsgType (1B) | NodeID (4B: u32) |
! +-------------------------------------------------------------------+
! | PayloadLen (4B: u32)             | Checksum (4B: u32 CRC32)       |
! +-------------------------------------------------------------------+

<!-- mios-src:13700c7b7fd4 from src/mios-rs/mios-node/src/protocol.rs:1-10 -->

### AI-hint

AI-hint: Task offloading priority queue with work-stealing scheduler for mios-node (task_336 / task_2569).
AI-related: src/mios-rs/mios-node/src/executor.rs, usr/libexec/mios/node/scheduler.py, tests/test-node.py
! MiOS Task Offloading Priority Queue & Work-Stealing Scheduler
!
! Provides prioritized task ingestion (Critical, High, Normal, Low), lock-free/synchronized
! per-worker deques with global injector, locality-aware work stealing, hardware pin invariants,
! and network offload routing.

<!-- mios-src:089951c4bdca from src/mios-rs/mios-node/src/scheduler.rs:1-7 -->

### AI-hint

AI-hint: CRDT LWW-Element-Set state sync engine with append-log persistence for mios-node.
AI-related: src/mios-rs/mios-node/src/node.rs
! MiOS Distributed Lock-Free State Synchronization Engine
! Implements Last-Write-Wins Element-Set (LWW-Element-Set) CRDT, Vector Clock Causality,
! and Disk-Backed Persistence (Snapshot & Append-Only Log)

<!-- mios-src:02eb8d31471a from src/mios-rs/mios-node/src/state_sync.rs:1-5 -->

### AI-hint

AI-hint: Hardware watchdog timer integration (/dev/watchdog) with safe 'V' magic close.
AI-related: src/mios-rs/mios-node/src/node.rs, usr/libexec/mios/node/watchdog.py, tests/test-node.py
! MiOS Hardware Watchdog Supervisor & Device Controller
! Integrates Linux `/dev/watchdog` timer with automatic keepalive pinging, systemd notify fallback,
! and safe magic close ('V' / 0x56) on clean termination.

<!-- mios-src:4191a9a7b4d9 from src/mios-rs/mios-node/src/watchdog.rs:1-5 -->

### AI-hint

AI-hint: Floats bake inputs on their newest upstream release: `latest-image <ref>` and `latest-git <url>` print the concrete ref to fetch, using the release shapes in mios.toml [build.float].
! Registries that publish no `latest` tag, and git projects whose releases are
! tags, still need one concrete ref to fetch. "The biggest tag" is wrong on real
! data: lowercase alpha series sort above uppercase release series, and every
! registry mixes arch- and variant-suffixed tags in with its releases. So a tag
! counts only if it matches a release SHAPE.
!
! Upstream is asked first. A forge's `releases/latest` (GitHub, Forgejo, Gitea)
! is the project's own answer, with pre-releases and drafts already excluded,
! and an image's `org.opencontainers.image.source` label leads back to that
! forge. The release shapes in [build.float] are the fallback for upstreams
! that publish neither. Every resolution says on stderr which one decided.

<!-- mios-src:ca5801c21699 from tools/native/mios-bake-plan/src/latest.rs:1-12 -->

### AI-hint

AI-hint: Emits [legibility].max_tracked_mb as round(tracked MiB) + [legibility].tracked_mb_headroom, so the size budget is generated rather than hand-typed.
AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, src/mios-rs/mios-gate/src/ratchet.rs

<!-- mios-src:ba0d38e62a90 from tools/native/mios-size-ceiling/src/main.rs:1-2 -->
