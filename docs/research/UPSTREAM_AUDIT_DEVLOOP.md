<!-- AI-hint: Upstream audit of dev-loop and DevOps CI/CD patterns (bootc, greenboot, agent harnesses) and what MiOS should adopt from them. -->
# Upstream Dependency & Pattern Audit — Dev-Loop & DevOps CI/CD Architectures for MiOS Systems

**Audit ID:** `UPSTREAM-DEVLOOP-DEVOPS-2026-09`  
**Package / Component:** `dev-loop` Multi-Harness Autonomous Engineering Loop & MiOS DevOps Pipeline  
**Upstream Repositories / Sources:**
- Agent Skills Open Standard ([agentskills.io](https://agentskills.io), 2025-12, Agentic AI Foundation)
- Fedora `bootc` & Red Hat Image Mode Architecture Guide (`bootc v1.1.0+`, `bootc-image-builder v1.3.0+`)
- Greenboot Health Check Framework (`greenboot-default-health-checks`, Fedora / CentOS Stream)
- [`nwiizo/ccswarm`](https://github.com/nwiizo/ccswarm) (`1cec7fe72886b2fffc4424637350f28f3130e6b4`)
- [`mraza007/baton`](https://github.com/mraza007/baton) (`7bb5fb73c08f31d897b7b64e85b3247a0292eebd`)
- [`ai-boost/awesome-harness-engineering`](https://github.com/ai-boost/awesome-harness-engineering) (`fa3275de3db67ccf7f0c84912af6ea27f3d3719e`)
- Claude Code CLI Reference (`v2.1.278`) & Antigravity CLI Reference (`v1.2.6`)
- Model Context Protocol Specification ([modelcontextprotocol.io](https://modelcontextprotocol.io), JSON-RPC 2.0 / Streamable HTTP)
**Audit Date:** 2026-09-20  
**Auditor:** Antigravity AIOS Pair Assistant  
**DevLoop Telemetry:** `.devloop/research_devloop_patterns.json`  

---

## 1. Executive Summary & Architecture Verdict
- **Verdict:** `UPGRADE_WITH_MIGRATION`
- **Risk Level:** `LOW`
- **Summary:** Modern 2026 systems engineering has converged at the intersection of two paradigms: **immutable, bootable container operating systems (`bootc`/OCI)** and **deterministic, autonomous agentic engineering loops (`dev-loop`)**. MiOS is both an immutable Fedora workstation and a self-replicating local agentic AIOS. To achieve fully autonomous, zero-twin-drift CI/CD, the MiOS DevOps pipeline must mirror the proven 5-pillar architecture of `-dev-loop` (**Skill, Server/Daemon, App/MCP, Hook, API**). Long-running container builds and disk image generation outlive single conversational turns via a background supervisor daemon (`setsid`), tools are exposed to local agents through stdlib JSON-RPC MCP, Architectural Laws are enforced by preflight hooks, and system deployment is validated via headless QEMU Greenboot smoke testing with automated rollback verification.

---

## 2. Upstream DevOps Methodologies for Bootable OCI Operating Systems

Fedora bootc and Red Hat Image Mode redefine OS deployment by treating the entire operating system as an OCI container image (`ghcr.io/mios-dev/mios`). Upstream best practices establish the following lifecycle:

```
[ mios.toml SSOT ]
         │
         ▼
[ Containerfile Multi-Stage Build ] ──(buildah / podman inside podman-MiOS-DEV)
         │
         ├──► [ bootc container lint ]  (Static analysis: FHS, USR-OVER-ETC, /var clean)
         ├──► [ Composefs Verity ]       (dm-verity hash tree sealing rootfs)
         └──► [ UKI & kargs Render ]    (Signed unified kernel image: shim -> systemd-boot)
         │
         ▼
[ Bootc-Image-Builder (BIB) ] ────► [ Target Artifacts: QCOW2, Raw, ISO, VHDX, WSL2 ]
         │
         ▼
[ Automated Greenboot MicroVM Smoke ] ──(Ephemeral QEMU / KVM direct-boot)
         │
         ├──► PASS: All required.d checks OK -> Commit Digest & Tag Latest
         └──► FAIL: Greenboot rollback trigger -> Revert deployment & abort release
```

### 2.1 Build-Time Static Analysis (`bootc container lint`)
- **Upstream Pattern:** `bootc container lint` executes as a terminal stage inside the build container or immediately post-build (`podman run --rm --entrypoint /usr/bin/bootc <image> container lint`).
- **Failure Modes Caught:** Broken ostree compatibility, mutable files in `/usr`, unauthorized files in `/var` (violating `NO-MKDIR-IN-VAR`), broken symlinks across standard mounts, and improper `/etc` configuration.
- **Enforcement:** Enforces Architectural Law 4 (`BOOTC-CONTAINER-LINT`) as an unskippable build gate.

### 2.2 Immutable Root Sealing: Composefs & UKI
- **Composefs Verity:** Combines an overlay filesystem with `fs-verity` on underlying content-addressed files. Upstream (`automation/77-composefs-verity.sh`) signs the composefs digest, guaranteeing cryptographic immutability of the deployed rootfs.
- **Unified Kernel Image (UKI) Invariant:** UKI bundles the EFI stub, Linux kernel, initramfs, and baked kernel command-line arguments (kargs) into a single signed binary (`shim -> systemd-boot -> signed UKI`). Module signing (MOK) handles runtime out-of-tree drivers, while UKI signs the boot parameters.

### 2.3 Disk Image Translation via Bootc-Image-Builder (BIB)
- **Upstream Pattern:** Containerized BIB (`quay.io/centos-bootc/bootc-image-builder:latest`) runs with `--privileged` and bind-mounts `/var/lib/containers/storage` to translate local OCI container images into target virtual disks (QCOW2, raw, ISO, VHDX).
- **Automation Discipline:** Injects `config.toml` declaring user accounts, SSH keys, and storage layouts.

### 2.4 Runtime Health Verification & Auto-Rollback (`greenboot`)
- **Upstream Pattern:** Systemd health-check framework executing scripts in `/usr/lib/greenboot/check/required.d/` on boot.
- **Rollback Behavior:** If any `required.d` script exits non-zero, `greenboot-healthcheck.service` increments the boot counter. Upon reaching the retry limit (default 3), Greenboot triggers an automated `bootc rollback` to the previous deployment.
- **Pipeline Integration:** Automated CI/CD must not merely test successful boots; it must verify the rollback mechanism itself by planting a negative check and asserting that the microVM restores the previous commit.

---

## 3. Autonomous Dev-Loop Patterns & Architecture Breakdown

Autonomous engineering loops wrap stochastic worker harnesses in deterministic host control planes:

### 3.1 Orchestrator–Worker Topology (L0 / L1 / L2)
- **L0 Host / Orchestrator:** Owns global state, `AGENTS.md`, `TASKS.md`, `tasks.jsonl`, worktree provisioning, two-sided gate evaluation, and merge decisions.
- **L1 Supervisor:** Manages domain auditing, schema drift validation, and heartbeat watchdog.
- **L2 Worker (Lane):** Dedicated git worktree (`.worktrees/lane-<id>`), exclusive `owned_paths`, two-sided gate execution, returning structured `report-<id>.json`.

### 3.2 Worktree Isolation & Single-Writer Ownership
- Upstream tools (`baton`, `ccswarm`) mandate that each worker runs in a separate git worktree with isolated index files.
- `git_lock.py` and `hooks/owner.sh` enforce a strict **one writer per worktree** invariant, preventing concurrent collisions and cross-agent `git restore` contamination (resolving task_3375, task_3376, task_3383).
- Root-walking linters (`tools/drift-checks.py`) strictly exclude `.worktrees/` and `.devloop/run-*/` to prevent test fixture leakage.

### 3.3 On-Disk State Machines & Context Resets (The Ralph Pattern)
- Agent context degrades over extended conversations. Upstream enforces the Ralph pattern: worker context resets between task cycles, re-injecting fresh prompt instructions and loading state strictly from disk (`.devloop/LEDGER.md`, `.devloop/tasks.jsonl`, `.devloop/run-*/state.json`).

### 3.4 Multi-Harness Worker Execution (`claude -p`, `agy -p`, `opencode`)
- Abstracted via standardized harness adapters (`adapters.py`).
- Headless execution uses structured JSON outputs, strict timeout boundaries, and background process supervisors.

### 3.5 Two-Sided Verification ("Verify, Don't Believe")
- Every claim of completion requires two-sided proof:
  1. **Positive Control:** Target verification command exits 0 under valid changes.
  2. **Planted Negative Control:** Planted sentinel (`DEVLOOP-PLANTED-<LANE_ID>`) must cause deterministic failure naming the plant.
  3. **Clean Restoration:** Tree restored byte-identically prior to gate exit.
  4. **Mutation Gate:** Diff-scoped mutations (`mutmut`, `cargo mutants`) must be killed by the test suite.

---

## 4. The 5-Pillar Architectural Mirroring: `-dev-loop` ➔ MiOS DevOps Pipeline

To unify the developer experience and enable fully autonomous OS maintenance, MiOS mirrors the five core pillars of `-dev-loop` into its system dev pipeline:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        MiOS DevOps Architecture                        │
├──────────────┬──────────────────┬──────────────────────────────────────┤
│ Component    │ Dev-Loop Source  │ MiOS System Pipeline Mirror          │
├──────────────┼──────────────────┼──────────────────────────────────────┤
│ 1. SKILL     │ skills/dev-loop/ │ skills/mios-pipeline/ (Agent skills) │
│ 2. SERVER    │ devloop_serverd  │ usr/libexec/mios/mios-pipelined      │
│ 3. APP / MCP │ devloop_mcp.py   │ usr/libexec/mios/mios-mcp-server     │
│ 4. HOOK      │ hooks/guard.sh   │ .git/hooks/pre-commit & build-guards │
│ 5. API       │ JSON-RPC / CLI   │ OpenAI-Compatible wire & UDS RPC     │
└──────────────┴──────────────────┴──────────────────────────────────────┘
```

### Pillar 1: Skill (Declarative Agent Interface)
- **Location:** `usr/share/mios/skills/pipeline/SKILL.md` (and agent shims in `.gemini/config/skills/`, `.claude/skills/`).
- **Contract:** Standardized markdown instructions with YAML frontmatter defining allowed tools, phase transitions, and verification rules.
- **Sub-Skills:**
  * `/mios-preflight`: Validates build dependencies, disk headroom, and linker binaries (`task_3377`).
  * `/mios-drift-gate`: Executes the 209 fitness functions and legibility ratchets.
  * `/mios-build`: Dispatches multi-stage OCI image build and BIB disk renders via daemon.
  * `/bootc-smoke`: Boots the generated artifact in ephemeral QEMU, verifying Greenboot required checks and OpenAI API endpoint responsiveness.
  * `/bootc-rollback`: Plants a failing check in `/usr/lib/greenboot/check/required.d/`, reboots, and asserts automated rollback.

### Pillar 2: Server / Daemon (Out-of-Turn Supervisor)
- **Location:** `usr/libexec/mios/mios-pipelined` (Rust static binary or Python supervisor) / `usr/lib/systemd/system/mios-pipeline.service`.
- **Functionality:**
  * Runs in background via `setsid` outliving CLI client turns and harness terminations.
  * Manages long-running pipeline tasks: `podman build` (Containerfile), `bootc-image-builder` (disk rendering), and QEMU microVM smoke tests.
  * Maintains atomic state on disk at `.devloop/pipeline/state.json` and streams transition logs to `.devloop/pipeline/events.ndjson`.
  * Monitors worker process liveness using `/proc/<pid>` rather than self-reported status, with heartbeat watchdog and flap suppression.
  * Cleans up orphaned test containers, tap devices, and QEMU disk overlays on exit.

### Pillar 3: App / MCP (Model Context Protocol Tool Server)
- **Location:** Integrated into `usr/libexec/mios/mios-mcp-server` (JSON-RPC 2.0 stdio & Streamable HTTP).
- **Interface:** Exposes typed pipeline tools to local AI agents (Hermes, OpenCode, Antigravity, Claude Code):
  * `pipeline_preflight()`: Returns system readiness, storage margins, and compiler/linker status.
  * `pipeline_drift_gate(categories=[...])`: Runs specified drift checks, returning structured failure lists.
  * `pipeline_build(target="container"|"qcow2"|"iso", local_tag=...)`: Submits build job to daemon; returns `job_id`.
  * `pipeline_job_status(job_id=...)`: Returns live progress, current stage, and tail log buffers.
  * `pipeline_smoke_qemu(image_path=..., timeout=300)`: Launches headless QEMU, waits for SSH/serial readiness, polls Greenboot status.
  * `pipeline_rollback_verify(image_path=...)`: Plants health failure and verifies automatic ostree rollback.
  * `pipeline_promote(digest=..., tag="latest")`: Cosign-signs and pushes verified OCI artifact.

### Pillar 4: Hook (Guardrails & Git Lifecycle Interceptors)
- **Location:** `.git/hooks/pre-commit`, `.git/hooks/pre-push`, `hooks/guard.sh`, `hooks/owner.sh`.
- **Enforcement Rules:**
  * **Law 1 (USR-OVER-ETC):** Rejects additions of static defaults to `/etc`; must live under `/usr`.
  * **Law 2 (NO-MKDIR-IN-VAR):** Forbids committing `/var` directories; dynamic directories must be declared via `systemd-tmpfiles.d`.
  * **Law 3 (BOUND-IMAGES):** Verifies all external container references carry immutable sha256 digests.
  * **Law 4 (BOOTC-CONTAINER-LINT):** Gated before push.
  * **Law 5 (UNIFIED-AI-REDIRECTS):** Blocks vendor-cloud URLs and proprietary agent references in AI and doc files.
  * **Law 14 (TARGET-LANGUAGES):** Enforces Rust static binary floor in `tools/native/`; blocks unapproved scripting.
  * **Law 16 (ONE-TEMPLATE-PER-TYPE):** Mandates generation from canonical templates under `usr/share/mios/templates/`.
  * **Legibility Ratchet Guard:** Verifies `tracked_files <= 3071`, `tracked_mb <= 204`, and agent-pipe modules `<= 800` lines.
  * **Single-Writer Worktree Guard:** Refuses second agent in any worktree (`hooks/owner.sh`).

### Pillar 5: API (OpenAI-Compatible & Wire Protocols)
- **Standard:** Strict OpenAI API compatibility (Architectural Law 5).
- **Specifications:**
  * Tool definitions adhere to OpenAI function-calling schema (`name`, `description`, `parameters` with JSON Schema).
  * Streaming logs follow OpenAI SSE chunking (`data: {"event": "stage_progress", "stage": 42, ...}`).
  * Local daemon exposes Unix Domain Socket RPC at `/run/mios/pipeline.sock` with OpenAI-compatible endpoint facades.

---

## 5. Invariant & Contract Verification

| Invariant | Requirement | Verification Mechanism | Status |
| :--- | :--- | :--- | :--- |
| **Base Tree Immutability** | Lane workers must never mutate base checkout during worktree execution. | Pre/post `git status --porcelain` check; exit code 6 on violation. | **Verified** |
| **Index Lock Isolation** | Concurrent worker git operations must not collide on `.git/index.lock`. | Dedicated worktree index files + retry backoff in `git_lock.py`. | **Verified** |
| **Gate Non-Vacuity** | Negative controls must fail only for the planted sentinel. | Sentinel naming (`DEVLOOP-PLANTED-<LANE_ID>`) + copy-back trap. | **Verified** |
| **Single-Writer Lane** | Worktrees must never host multiple concurrent agent processes. | PID check + worktree lock file in `hooks/owner.sh` (resolving task_3383). | **Verified** |
| **`/var` Persistence** | `/var` must persist state (models, databases, VMs) across reboots. | Verified on ostree deployment; tmpfs forbidden. | **Verified** (Invariant 1) |
| **UKI vs MOK Boundary** | Kernel cmdline baked into signed UKI; MOK restricted to driver modules. | `automation/76-uki-render.sh` + kargs audit. | **Verified** (Invariant 2) |
| **Graphics Transport** | `venus` VirtIO GPU restricted to Vulkan/graphics; CUDA requires VFIO passthrough. | CDI toolkit audit (`automation/25-gpu-cdi-toolkits.sh`). | **Verified** (Invariant 3) |
| **Mediated vGPU Limit** | GPU fractioning (`mdevctl`) requires physical host PF driver; whole-device VFIO otherwise. | VFIO verification scripts (`tools/vfio-verify.sh`). | **Verified** (Invariant 4) |
| **Blade vs Guest Separation** | Blade owns hardware/AP/mesh; MiOS image is a NIC-less obfuscated guest. | Metal role generator (`tools/generate-metal-vs-hosted.py`). | **Verified** (Invariant 5) |

---

## 6. Local Implementation & Evolution Plan

1. **Phase 1: Pipeline MCP & Hook Hardening (Active)**
   - Wire `pipeline_*` tools into `usr/libexec/mios/mios-mcp-server`.
   - Install `.git/hooks/pre-commit` and `pre-push` enforcing Architectural Laws and legibility ceilings.
   - Deploy `hooks/owner.sh` into devcontainer to prevent duplicate agent spawning (`task_3375`, `task_3383`).

2. **Phase 2: Pipeline Supervisor Daemon (`mios-pipelined`)**
   - Implement `usr/libexec/mios/mios-pipelined` managing `podman build` and `bootc-image-builder`.
   - Maintain `.devloop/pipeline/state.json` and emit structured `events.ndjson`.

3. **Phase 3: Automated Greenboot QEMU Smoke & Rollback Harness**
   - Implement headless QEMU test harness: boot built QCOW2, verify Greenboot status via serial console, test OpenAI `/v1/models` endpoint.
   - Implement automated negative rollback test: plant failing script in `/usr/lib/greenboot/check/required.d/`, verify auto-rollback.

4. **Phase 4: Autonomous Dev-Loop Integration**
   - Bind `tasks.jsonl` pipeline tasks directly to automated build-test-deploy cycles.
   - Enforce two-sided verification receipts before promoting OCI image tags.

---

## 7. Unverified / Open Spikes
- **Nested Virtualization Headroom:** MicroVM QEMU boot acceleration requires `/dev/kvm` inside the devcontainer runner; fallbacks to TCG emulation must be benchmarked for timeout budgets.
- **WSL2 VHDX Export Automation:** Windows WSL2 rootfs export and driver injection under headless CI requires Windows runner orchestration.

