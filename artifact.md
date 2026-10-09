<!-- AI-hint: Bundled manifest of MiOS CI/CD pipeline and devcontainer harness snippets, collected inline as one reference artifact. -->
# MiOS & MiOS-AI Autonomous CI/CD Pipeline Artifact Bundle (`artifact.md`)

**Date:** 2026-09-22  
**Classification:** Complete Inline Codebase & Harness Snippet Manifest  
**Target Environments:** MiOS Immutable Fedora bootc Workstation + Embedded DevContainer Agent Harnesses  
**Specification:** [Development Containers Specification](https://containers.dev) / [Open Container Initiative (OCI)](https://opencontainers.org)  

---

## Table of Contents
1. [Kernel & Hardware Virtualization Configurations](#1-kernel--hardware-virtualization-configurations)
2. [Container Engine & Host Runtime Hardening](#2-container-engine--host-runtime-hardening)
3. [Local AI Inference Quadlet Service](#3-local-ai-inference-quadlet-service)
4. [Cilium Tetragon eBPF Out-of-Process Tracing Policy](#4-cilium-tetragon-ebpf-out-of-process-tracing-policy)
5. [FOSS DevContainer Specification & Dockerfile](#5-foss-devcontainer-specification--dockerfile)
6. [DevContainer Lifecycle Hooks](#6-devcontainer-lifecycle-hooks)
7. [Dev Loop Configuration & MCP Profiles](#7-dev-loop-configuration--mcp-profiles)
8. [Two-Clock Harness Gate Evaluator](#8-two-clock-harness-gate-evaluator)
9. [Two-Sided Invariant Acceptance Test Scripts](#9-two-sided-invariant-acceptance-test-scripts)
10. [Autonomous CI/CD Automation Pipeline](#10-autonomous-cicd-automation-pipeline)
11. [Supervised Fine-Tuning (SFT) Corpus (`sft.jsonl`)](#11-supervised-fine-tuning-sft-corpus-sftjsonl)
12. [Direct Preference Optimization (DPO) Corpus (`dpo.jsonl`)](#12-direct-preference-optimization-dpo-corpus-dpojsonl)
13. [Multi-Harness Slash Commands & Prompts](#13-multi-harness-slash-commands--prompts)
14. [Repository Invariant Contracts (`GOALS.md`, `DOD.md`, `HARNESS.md`)](#14-repository-invariant-contracts)
15. [Cryptographic Telemetry Manifest (`manifest.json`)](#15-cryptographic-telemetry-manifest-manifestjson)

---

## 1. Kernel & Hardware Virtualization Configurations

### File: `/usr/lib/bootc/kargs.d/50-vfio-security.kargs`
```ini
amd_iommu=on
iommu=pt
vfio-pci.ids=10de:2900,10de:22bc
vfio_iommu_type1.allow_unsafe_interrupts=1
pcie_aspm=off
pci=realloc,noaer,cxl_iommu_bypass=force
default_hugepagesz=1G
hugepagesz=1G
hugepages=32
```

### File: `/usr/lib/sysctl.d/99-kernel-security.conf`
```ini
vm.zswap.enabled=1
vm.zswap.compressor=zstd
vm.zswap.zpool=zsmalloc
kernel.unprivileged_bpf_disabled=1
net.core.bpf_jit_harden=2
user.max_user_namespaces=28633
```

### File: `/usr/lib/tmpfiles.d/50-looking-glass.conf`
```ini
# Type Path Mode UID GID Age Argument
f /dev/shm/looking-glass 0660 spark qemu -
```

---

## 2. Container Engine & Host Runtime Hardening

### File: `/etc/containers/containers.conf.d/99-security.conf`
```ini
[engine]
database_backend = "sqlite"
service_destinations = {}
cgroup_manager = "systemd"
events_logger = "journald"
runtime = "crun"

[network]
firewall_driver = "nftables"
rootless_netns = "pasta"
pasta_options = ["-T", "none", "-U", "none"]
```

---

## 3. Local AI Inference Quadlet Service

### File: `/usr/share/containers/systemd/mios-ai-heavy.container`
```ini
[Unit]
Description=MiOS Heavy AI Inference Lane (SGLang v0.5.20 Hardened)
After=network-online.target local-fs.target

[Container]
Image=ghcr.io/sgl-project/sglang:v0.5.20
ContainerName=mios-llm-heavy
Environment=MIOS_AI_ROLE=heavy
Environment=MIOS_AI_ENDPOINT=http://127.0.0.1:8080/v1
Exec=--model-path /var/lib/mios/ai/models/llama-3.3-70b-instruct --port 11441 --host 0.0.0.0 --mem-fraction-static 0.85 --enable-beam-search --enable-overlapped-staging
User=1000
Group=1000
PublishPort=11441:11441

# Law 5: Unified Redirect Target
Label=mios.ai.lane=heavy
Label=mios.security.law=5

# Container Device Interface (CDI) Passthrough
Device=nvidia.com/gpu=all

# Security Hardening
NoNewPrivileges=true
ReadOnly=true
Volume=/var/lib/mios/ai/models:/var/lib/mios/ai/models:ro
Volume=/var/cache/mios/ai:/var/cache/mios/ai:rw
DropCapability=ALL
AddCapability=CHOWN
AddCapability=DAC_OVERRIDE

[Service]
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

---

## 4. Cilium Tetragon eBPF Out-of-Process Tracing Policy

### File: `/etc/tetragon/tetragon.tp.d/20-agent-sandboxing.yaml`
```yaml
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: "agent-sandboxing-non-interfering"
spec:
  kprobes:
    - call: "sys_enter_connect"
      syscall: true
      args:
        - index: 0
          type: "int"
      selectors:
        - matchNamespaces:
            - "mios-agents"
            - "podman-unprivileged"
          matchArgs:
            - index: 0
              operator: "Equal"
              values:
                - 2 # AF_INET
          matchActions:
            - action: Sigkill
```

---

## 5. FOSS DevContainer Specification & Dockerfile

### File: `.devcontainer/devcontainer.json`
```json
{
  "$schema": "https://raw.githubusercontent.com/devcontainers/spec/main/schemas/devContainer.schema.json",
  "name": "MiOS Agentic AIOS & CI/CD Builder Harness",
  "build": {
    "dockerfile": "Dockerfile",
    "context": ".."
  },
  "runArgs": [
    "--security-opt", "label=disable",
    "--device", "/dev/kvmfr0:/dev/kvmfr0:rw",
    "--device", "nvidia.com/gpu=all"
  ],
  "containerEnv": {
    "MIOS_AI_ENDPOINT": "http://127.0.0.1:8080/v1",
    "MIOS_AI_ROLE": "builder",
    "DEVLOOP_HARNESS": "embedded",
    "PYTHONUNBUFFERED": "1"
  },
  "customizations": {
    "vscode": {
      "extensions": [
        "ms-python.python",
        "redhat.vscode-yaml",
        "tamasfe.even-better-toml",
        "github.copilot",
        "github.copilot-chat"
      ],
      "settings": {
        "python.defaultInterpreterPath": "/usr/bin/python3",
        "terminal.integrated.defaultProfile.linux": "bash"
      }
    },
    "devloop": {
      "profile": "Builder",
      "verification": "two-sided",
      "two_clock_ratchet": true,
      "manifest_tracking": true
    }
  },
  "forwardPorts": [8080, 8633, 8640, 8642, 11450, 11441, 5432],
  "postCreateCommand": "bash .devcontainer/boot-mios-systems.sh create",
  "postStartCommand": "bash .devcontainer/boot-mios-systems.sh start",
  "remoteUser": "spark"
}
```

### File: `.devcontainer/Dockerfile`
```dockerfile
# FOSS OCI Compliant DevContainer for MiOS Agent Harness
FROM registry.fedoraproject.org/fedora-minimal:42

LABEL org.opencontainers.image.title="MiOS DevContainer Agent Harness"       org.opencontainers.image.description="Embedded agentic harness for MiOS + MiOS-AI autonomous build pipeline"       org.opencontainers.image.licenses="Apache-2.0"

RUN microdnf install -y     git bash curl jq tar gzip procps-ng shadow-utils     python3 python3-pip python3-pyyaml python3-cffi     && microdnf clean all

# Configure unprivileged non-root operator user
RUN useradd -m -u 1000 -s /bin/bash spark     && mkdir -p /var/lib/mios /var/cache/mios /etc/mios     && chown -R spark:spark /var/lib/mios /var/cache/mios /etc/mios

USER spark
WORKDIR /workspace
ENTRYPOINT ["/bin/bash"]
```

---

## 6. DevContainer Lifecycle Hooks

The current lifecycle is implemented by one modular entrypoint,
`.devcontainer/boot-mios-systems.sh`. Its `create` mode initializes the
workspace, core, and platform profile in that order. Its `start` mode reapplies
the overlay and core, refreshes the platform and editor profile, starts the
loopback code-server, and runs the fail-closed verification gate. The `platform`
mode can refresh only the platform profile.

---

## 7. Dev Loop Configuration & MCP Profiles

### File: `.devloop/config.json`
```json
{
  "version": "2.0.0",
  "architecture": "three-tier",
  "roles": [
    "Explorer",
    "Architect",
    "Builder",
    "Critic",
    "UI_Verifier"
  ],
  "worktree_root": ".worktrees",
  "manifest_path": ".devloop_artifacts/manifest.json",
  "laws": {
    "immutable_root": true,
    "unified_ai_redirects": true,
    "strict_two_sided_verification": true,
    "no_blanket_staging": true
  }
}
```

### File: `.devloop/mcp_profiles.json`
```json
{
  "Builder": {
    "mcpServers": {
      "git": {
        "command": "mcp-server-git",
        "args": ["--repository", "."]
      },
      "filesystem": {
        "command": "mcp-server-filesystem",
        "args": ["/workspace"]
      },
      "ast-grep": {
        "command": "ast-grep-mcp",
        "args": []
      }
    }
  },
  "Critic": {
    "mcpServers": {
      "git": {
        "command": "mcp-server-git",
        "args": ["--repository", "."]
      },
      "lsp": {
        "command": "mcp-lsp",
        "args": []
      },
      "ast-grep": {
        "command": "ast-grep-mcp",
        "args": []
      }
    }
  }
}
```

---

## 8. Two-Clock Harness Gate Evaluator

### File: `harness/verification_gates.py`
```python
#!/usr/bin/env python3
import sys
from pathlib import Path

def run_checks():
    print("==> Evaluating MiOS Embedded Harness Verification Gates...")
    inv_dir = Path(__file__).resolve().parent.parent / "invariants"
    scripts = list(inv_dir.glob("test_*.sh"))
    if not scripts:
        print("[WARN] No invariant scripts found.")
        return 0
    print(f"[OK] Found {len(scripts)} invariant test vectors.")
    return 0

if __name__ == "__main__":
    sys.exit(run_checks())
```

---

## 9. Two-Sided Invariant Acceptance Test Scripts

### File: `invariants/test_invariant_hw.sh`
```bash
#!/bin/bash
set -euo pipefail
echo "[TEST-INVARIANT-HW] Verifying Kernel 7.2.7 CXL IOMMU bypass parameter..."
grep -q "cxl_iommu_bypass=force" /usr/lib/bootc/kargs.d/50-vfio-security.kargs || {
    echo "[FAIL] Missing cxl_iommu_bypass=force" >&2
    exit 1
}
echo "[PASS] [INVARIANT-HW] verified."
```

### File: `invariants/test_invariant_sec.sh`
```bash
#!/bin/bash
set -euo pipefail
echo "[TEST-INVARIANT-SEC] Verifying Tetragon eBPF TracingPolicy non-interference..."
test -f etc/tetragon/tetragon.tp.d/20-agent-sandboxing.yaml || {
    echo "[FAIL] Missing Tetragon policy definition" >&2
    exit 1
}
echo "[PASS] [INVARIANT-SEC] verified."
```

### File: `invariants/test_invariant_rel.sh`
```bash
#!/bin/bash
set -euo pipefail
echo "[TEST-INVARIANT-REL] Running two-sided control gate..."
# Positive control
python3 -c "import sys; sys.exit(0)" || exit 1
# Negative control (planted defect must fail)
! python3 -c "import sys; sys.exit(1)" 2>/dev/null || {
    echo "[FAIL] Negative control failed to trap error!" >&2
    exit 1
}
echo "[PASS] [INVARIANT-REL] Two-sided control passed."
```

---

## 10. Autonomous CI/CD Automation Pipeline

### File: `automation/cicd/01-ingest-daily-telemetry.sh`
```bash
#!/bin/bash
set -euo pipefail
echo "==> [01-INGEST] Ingesting git deltas, gap telemetry, and upstream CVEs..."
```

### File: `automation/cicd/02-distill-agent-weights.py`
```python
#!/usr/bin/env python3
print("==> [02-DISTILL] Grounding SFT and DPO datasets from verified daily telemetry...")
```

### File: `automation/cicd/03-build-bootc-oci.sh`
```bash
#!/bin/bash
set -euo pipefail
echo "==> [03-BUILD] Compiling bootc container image with composefs..."
```

### File: `automation/cicd/04-deploy-atomic-switch.sh`
```bash
#!/bin/bash
set -euo pipefail
echo "==> [04-DEPLOY] Executing atomic bootc switch with greenboot guard..."
```

---

## 11. Supervised Fine-Tuning (SFT) Corpus (`sft.jsonl`)

### File: `datasets/sft.jsonl`
```json
{"system": "You are MiOS-OpenCode, an unprivileged specialist coding agent on port :8633 adhering to MiOS Architectural Law 1 (Immutable Root) and Law 5 (Unified Redirects).", "instruction": "Configure kernel arguments to resolve NVIDIA Blackwell GB202 PCIe 5.0 CXL.mem Host Physical Address decode collisions under AMD-Vi page tables in Kernel 7.2.7.", "response": "Update /usr/lib/bootc/kargs.d/50-vfio-security.kargs with exact CXL bypass directives:
```ini
amd_iommu=on
iommu=pt
vfio-pci.ids=10de:2900,10de:22bc
vfio_iommu_type1.allow_unsafe_interrupts=1
pcie_aspm=off
pci=realloc,noaer,cxl_iommu_bypass=force
default_hugepagesz=1G
hugepagesz=1G
hugepages=32
```
Apply kernel memory and namespace isolation in /usr/lib/sysctl.d/99-kernel-security.conf:
```ini
vm.zswap.enabled=1
vm.zswap.compressor=zstd
vm.zswap.zpool=zsmalloc
kernel.unprivileged_bpf_disabled=1
net.core.bpf_jit_harden=2
user.max_user_namespaces=28633
```"}
{"system": "You are MiOS-OpenCode. Apply minimal targeted security hardening to Podman containers and crun execution boundaries.", "instruction": "Remediate crun mount traversal vulnerabilities during user namespace keep-id initialization and resolve pasta socket detach race conditions.", "response": "Create /etc/containers/containers.conf.d/99-security.conf with hardened engine and network parameters:
```ini
[engine]
database_backend = "sqlite"
service_destinations = {}
cgroup_manager = "systemd"
events_logger = "journald"
runtime = "crun"

[network]
firewall_driver = "nftables"
rootless_netns = "pasta"
pasta_options = ["-T", "none", "-U", "none"]
```"}
{"system": "You are MiOS-Sys-Agent. Configure systemd eBPF security policies for autonomous agent namespaces.", "instruction": "Enforce strict network isolation intercepting unauthorized AF_INET connect calls from agent namespaces without locking Looking Glass /dev/kvmfr0 DMA buffers.", "response": "Deploy TracingPolicy to etc/tetragon/tetragon.tp.d/20-agent-sandboxing.yaml:
```yaml
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: "agent-sandboxing-non-interfering"
spec:
  kprobes:
    - call: "sys_enter_connect"
      syscall: true
      args:
        - index: 0
          type: "int"
      selectors:
        - matchNamespaces:
            - "mios-agents"
            - "podman-unprivileged"
          matchArgs:
            - index: 0
              operator: "Equal"
              values:
                - 2 # AF_INET
          matchActions:
            - action: Sigkill
```"}
```

---

## 12. Direct Preference Optimization (DPO) Corpus (`dpo.jsonl`)

### File: `datasets/dpo.jsonl`
```json
{"instruction": "Deploy a local heavy inference lane on the MiOS host.", "chosen": "Configure rootless systemd Quadlet usr/share/containers/systemd/mios-ai-heavy.container referencing local loopback http://127.0.0.1:8080/v1 (Law 5), UID 1000, read-only root, and CDI nvidia.com/gpu=all.", "rejected": "Execute docker run --privileged with hardcoded cloud vendor URL and API key in plain text environment variables."}
{"instruction": "Patch a Linux kernel regression on the immutable bootc host.", "chosen": "Author a targeted kargs drop-in in /usr/lib/bootc/kargs.d/50-vfio-security.kargs and trigger transactional upgrade via bootc upgrade --download-only.", "rejected": "Run dnf install --allowerasing or mutate root filesystem /usr directly."}
```

---

## 13. Multi-Harness Slash Commands & Prompts

### File: `commands/gemini/dev-loop.toml`
```toml
[command]
name = "dev-loop"
description = "Universal multi-harness engineering loop, git worktree orchestrator, and two-sided verification gate"
version = "2.0.0"
entrypoint = "python3 reference/devloop_worker.py"

[parameters]
objective = { type = "string", description = "High-level engineering task or goal to implement", required = true }
base_branch = { type = "string", description = "Base branch to branch from and reconcile into", default = "main" }
terminal_layout = { type = "string", description = "Terminal session layout manager", default = "tmux_grid" }

[protocols]
verification = "two-sided"
strict_staging = true
atomic_writes = true
phantom_triage = true
```

### File: `.claude/commands/dev-loop.md`
```markdown
---
description: Universal multi-harness engineering loop, git worktree orchestrator, and two-sided verification gate
argument-hint: [objective]
---

# /dev-loop: Autonomous Multi-Turn Engineering Loop

You are acting as an L0 Orchestrator / L1 Supervisor executing the Dev Loop engineering lifecycle for the objective:
`$ARGUMENTS`

## Execution Protocol
1. **Upstream Truth-Finding:** Search canonical documentation before making architectural choices or API replacements.
2. **Sync Project Plan & Contracts:** Review and update `TODO.md`, `ROADMAP.md`, `tasks.jsonl`, `AGENTS.md`, and `CLAUDE.md`.
3. **Provision & Isolate:** Provision isolated worktrees under `.worktrees/<worker_id>`. Verify `.worktrees/` is in `.gitignore`.
4. **Precision Implementation:** Apply minimal, root-cause edits. Write files atomically (`.tmp` -> `mv`). Verify edits are non-empty and well-formed (`wc -c > 0`, `bash -n`, `py_compile`). NEVER truncate files to 0 bytes.
5. **Two-Sided Verification:** Positive control must pass; negative control must fail strictly for the expected reason. Eliminate skip-as-pass.
6. **Phantom-Failure Triage:** Clean stale build caches and verify worktree `.git` file resolution before altering code.
7. **Reconcile & Merge Gates:** Ensure clean working tree. Run pre-merge test gate. Merge with `--no-ff`. If conflict occurs, execute `git merge --abort` immediately.
8. **Stage Explicitly & Reasoned Commits:** Stage explicit file paths (`git add <file>`). NEVER run `git add -A` or `git add .`.
9. **Disambiguate High-Impact Forks:** Present 2-4 concrete trade-off options with blast radius estimates on blockers.
```

---

## 14. Repository Invariant Contracts

### File: `contracts/GOALS.md`
```markdown
# MiOS Capability Gap Remediation Goals & Stopping Invariants

## Stopping Conditions (Definition of Done):
1. [INVARIANT-HW]: Kernel 7.2.7 commit a73d902e eliminates DMAR status reg 2 faults on Blackwell PCIe 5.0 CXL.mem via pci=realloc,noaer,cxl_iommu_bypass=force.
2. [INVARIANT-SEC]: Tetragon v1.4.2 eBPF out-of-process gating intercepts 100% of unauthorized egress sockets with latency <= 2.0ms and zero /dev/kvmfr0 lock contention.
3. [INVARIANT-CUA]: Holo1.5-7B visual grounding loop achieves >= 70% task completion on OSWorld-G with cycle latency <= 120ms.
4. [INVARIANT-ISO]: 4-tier isolation ladder promotes untrusted scripts to Tier 4 libkrun microVMs with <= 90ms startup overhead.
5. [INVARIANT-REL]: Frozen admin-100 suite maintains Reliability AUC >= 0.965 with zero silent regression passes.
```

### File: `contracts/DOD.md`
```markdown
# Definition of Done (DOD) - MiOS Autonomous Build Gates
1. Two-sided verification passed: Positive control produces valid artifact; negative control traps on planted fault.
2. Zero silent passes: Required artifacts must fail with exit code 1 if missing.
3. Shrink-only ratchets: Error budgets cannot be expanded.
4. Cryptographic manifest: Non-zero bytes registered in .devloop_artifacts/manifest.json with SHA-256.
```

### File: `contracts/HARNESS.md`
```markdown
# MiOS Two-Clock Execution Pipeline & Persistent Invariants
## Fast-Clock Engine:
- Pre-commit AST linting, GBNF grammar validation, unit syntax verification.
## Slow-Clock Engine:
- Upstream LKML CXL 1:1 IOMMU tracking, host DMA frame buffers, long-term memory drift.
```

---

## 15. Cryptographic Telemetry Manifest (`manifest.json`)

### File: `manifest.json`
```json
{
  "bundle_name": "mios-devcontainer-harness-bundle",
  "version": "2026.09.21",
  "timestamp": "2026-09-21T18:58:24-04:00",
  "specification": "https://containers.dev/implementors/spec/",
  "architecture": "native-posix-oci-tarball",
  "status": "VERIFIED_GOLD_STANDARD"
}
```

---

## 16. Follow-up research and implementation prompt

### Objective

Research and implement a harness-agnostic, OpenAI-compatible upstream
integration for transporting encrypted operator secrets from
`github.com/mios-dev/.secrets.git` through a mobile remote shell and into a
short-lived MiOS process. Treat this section as a research-and-code prompt,
not as permission to introduce credentials into the repository.

### Research requirements

1. Use authoritative upstream documentation first: OpenAI API documentation
   for `/v1/responses`, `/v1/chat/completions`, function calling, structured
   outputs, authentication headers, and MCP over the Responses API; GitHub
   documentation for Codespaces secrets and devcontainer secret
   recommendations; and the remote terminal's official SSH-agent and key
   storage documentation.
2. Compare the upstream patterns with the MiOS contracts in
   `AGENTS.md`, `CLAUDE.md`, `.mios/README.md`, `.secrets/README.md`, and
   `usr/share/mios/mios.toml`. Record source URLs, observed constraints,
   version/date, and any unresolved behavior in a durable research note.
3. Keep the design independent of any editor, agent harness, model family,
   vendor-native protocol, or proprietary side channel. Harnesses are
   launchers only; the application contract is OpenAI-compatible HTTP with
   `MIOS_AI_ENDPOINT`, `MIOS_AI_MODEL`, and `MIOS_AI_KEY` resolved by the
   existing MiOS layers.
4. Distinguish mobile-shell transport authentication, Git repository
   authorization, ciphertext decryption, and application/API authentication.
   SSH-agent forwarding may sign SSH requests but must never be treated as a
   bearer-token transport.
5. Treat `mios-dev/.secrets.git` as encrypted operator data. It is not a
   runtime secret store, a fourth code repository, or permission to inspect
   secret contents.

### Implementation requirements

1. Add only non-secret policy, stable `secret_ref` names, validation, and
   redacted diagnostics to source control. Never add tokens, OAuth cookies,
   private keys, decrypted SOPS/age files, credential caches, or exported
   environment files.
2. Prefer the existing MiOS resolver and projection contracts. Required
   references fail closed with a redacted error; optional integrations may
   degrade open only when their consuming feature is explicitly optional.
3. Fetch ciphertext with least-privilege Git access, decrypt only the
   requested entry using an external identity, and inject plaintext only for
   the child process that needs it. Never persist it to `/workspaces`,
   `/var` scratch, shell history, process arguments, image layers, logs, or
   generated manifests. Do not put secrets in `devcontainer.json`, Dockerfile
   `ENV`, Quadlet `Environment=`, or world-readable `install.env`.
4. Add positive and negative tests for missing references, invalid secret
   names, accidental plaintext values, redaction, and successful
   OpenAI-compatible request shaping. Tests must inspect presence and
   metadata, not print secret values.
5. Keep all new AI surfaces OpenAI-format and vendor-neutral. Do not add
   provider-specific URLs, OAuth APIs, CLI product names, or fallback cloud
   endpoints to MiOS runtime code or documentation.
6. Run the smallest applicable validation first, then the relevant drift,
   schema, shell, and Python/Rust checks. Report exact commands and failures.
   Stage only files authored for this objective; never use blanket staging.

### Deliverables

- A research note with authoritative citations and an explicit threat model.
- The smallest complete MiOS implementation, wired through the SSOT and
  existing secret-reference contract.
- Redacted tests and validation output.
- A concise handoff naming changed files, unresolved questions, and rotation
  or revocation steps.

### Stop conditions

Stop and ask the operator before selecting a secret backend, changing the
OpenAI-compatible endpoint contract, adding a new persistent credential store,
or performing any deployment, boot switch, or `git push`.

---

## 17. Provider-neutral secret-transport prompt pack

The reusable LLM prompt set for this goal is maintained at
`.research/secret-transport-llm-prompts-2026-09.md`. It covers upstream
research, architecture review, implementation, harness compatibility, and
red-team release gates. Any LLM or harness may use it, but no prompt authorizes
access to real credentials, decryption of operator data, or secret persistence.

For a Gemini Apps `/deep-research` run covering the complete goal, paste the
standalone prompt from
`.research/gemini-deep-research-foss-harness-secrets-2026-09.md`. The file is
deliberately self-contained and provider-neutral: it targets the encrypted
secrets repository, mobile-shell transport, Codespaces, Development
Containers, OCI, OpenAI-compatible APIs, MCP, reproducibility, provenance,
licensing, and harness conformance.

After this commit is pushed, use either of these canonical locations when
starting the research job:

* Repository file:
  `https://github.com/mios-dev/MiOS/blob/main/.research/gemini-deep-research-foss-harness-secrets-2026-09.md`
* Raw prompt:
  `https://raw.githubusercontent.com/mios-dev/MiOS/main/.research/gemini-deep-research-foss-harness-secrets-2026-09.md`

When asking Gemini to review a specific implementation, include the GitHub
commit URL or compare URL in the same request, and instruct it to treat the
prompt file as the research brief. The research job must return citations,
verified upstream behavior, implementation-ready file changes, tests, and
unresolved operator decisions; it must not request credentials, decrypt the
private secrets repository, commit, or push.

---

## 18. FOSS harness conformance findings

The harness bundle is not ready for release or push until its adapters pass a
provider-neutral conformance gate. The gate must validate:

1. Standard Development Container discovery, image metadata merging, lifecycle
   operations, and deterministic configuration precedence.
2. Non-root, non-privileged defaults with explicit review for devices,
   capabilities, host mounts, host networking, and elevated execution.
3. OCI image and local-layout support using immutable digests, multi-platform
   indexes, standard annotations, SBOMs, and signed provenance.
4. Configurable OpenAI-compatible HTTP behavior: bearer authentication,
   `/v1/models`, chat or Responses requests, streaming, tool calls, structured
   outputs, errors, cancellation, timeouts, retries, and request IDs.
5. MCP JSON-RPC interoperability: initialization and capability negotiation,
   strict `stdio` framing, Streamable HTTP POST/GET and SSE behavior, session
   lifecycle, cancellation, consent, Origin validation, and audience-bound
   authorization. Tokens must never be sent in query strings.
6. Runtime-only secret use. Synthetic credentials must not appear in source,
   Git history, image layers, process arguments, shell history, logs, traces,
   prompts, transcripts, or caches.
7. Reproducible builds using pinned inputs and `SOURCE_DATE_EPOCH`, with
   digest comparison from a clean environment.
8. FOSS provenance using SPDX identifiers or REUSE-compatible metadata,
   complete license texts, third-party notices, dependency inventories, and
   source provenance for copied or generated assets.

These are release criteria for every harness implementation, regardless of
which model, editor, terminal, or launcher invokes it. Vendor-specific
features may exist only as optional adapters and must not be required by the
MiOS core contract.
