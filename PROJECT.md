<!-- AI-hint: Project brief: gateway context budgeting, the Windows low-power iGPU desktop and static Rust consolidation across the Windows/WSL boundary. -->
# Project: MiOS Gateway Context Budgeting, Windows Low-Power iGPU Desktop & Static Rust Consolidation

## Architecture
MiOS local neural gateway, desktop presentation layer, and verification tooling operate across the Windows host and WSL2 container boundaries under Architectural Laws 5 (UNIFIED-AI-REDIRECTS), 7 (NO-HARDCODE), 8 (SSOT-PROJECTION), and 14 (TARGET-LANGUAGES):
- **Gateway Context Budgeting & Ingress Routing (:8700 / MIOS_AI_ENDPOINT)**:
  `usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py` and `vision.py` front all local agent inference. Plain chat requests with `tool_choice: "none"` strip all tool definitions before backend dispatch, ensuring 0 tool tokens in the prompt. When client harnesses (Codex, OpenCode, Claude Code) supply their own tool sets, the gateway detects caller tools (using `_name_is_verb` and tool capacity thresholds) and suppresses redundant `_mios_sel` tool injection. The gateway enforces a dynamic 32,768-token context ceiling (`--ctx-size 32768`), applying tiered principled pruning (`_drop_stale_tool_results`, `plan_compaction`, content truncation) to prevent HTTP 400 errors from backend LLM servers.
- **Windows Low-Power iGPU Desktop & Wallpaper Lifecycle**:
  `usr/share/mios/windows/Set-MiOSWallpaper.ps1`, `MiOS-Wallpaper-Service`, `MiOS-Wallpaper.exe`, and `tools/native/mios-wallpaperd` manage desktop wallpaper execution. Windows DirectX low-power GPU routing (`GpuPreference=1;`) is enforced across `HKCU:\Software\Microsoft\DirectX\UserGpuPreferences` and all hives under `HKEY_USERS` for `MiOS-Wallpaper.exe`, `msedgewebview2.exe`, `mios-wallpaperd.exe`, and `llama-server.exe`, directing 3D rendering to the generic Power Saving / Integrated GPU across all supported architectures (Intel, AMD, Qualcomm/ARM, or virtual adapters) without hardcoding vendor IDs or chipset models. On multi-GPU configurations, the secondary discrete / high-performance GPU maintains strictly 0 MB compute VRAM allocation during wallpaper and loopback inference, cleanly degrading open on single-GPU or CPU-only systems. Color tokens are wired dynamically from `usr/share/mios/mios.toml` `[colors]` into `HKLM\SOFTWARE\MiOS\WallpaperUrl`.
- **Compiled Static Rust Tooling (task_3454 / task_3455)**:
  Candidate leaf verification tools in `usr/libexec/mios` and `automation/` are consolidated into compiled static Rust binaries in `tools/native/` with strangler shims. `mios-hardcode-lint` delivers 100% byte-identical CLI output and exit codes against the Python oracle. `tools/native/mios-service-core` provides shared daemon infrastructure across `mios-agent-relay`, `mios-wallpaperd`, and `mios-launch`, strictly enforcing Architectural Law 5 (0 vendor cloud URLs) and dynamic layered SSOT resolution.
- **Upstream FOSS Research**:
  Controlled research into upstream FOSS patterns for context window budgeting, prompt compression, and DirectX low-power GPU scheduling to inform architectural designs and limits.
- **Standing Gate Certification & Two-Sided Controls**:
  All changes across gateway, wallpaper, and Rust tooling are accompanied by two-sided verification controls (positive pass + negative planted failure). Standing gates (`phase-registry`, `ratchet-direction`, `credential-literals`, `version-literals-ssot`, `signature-policy`), `ci-suites.py --check`, `sync-bootstrap.py --check`, and `sync-generated.sh` are certified clean with 0 unprojected diffs.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | `tool_choice: "none"` Ingress Stripping | Strip tool definitions and tool_choice in `chat.py` so plain chat runs with 0 tool tokens | M1 | R1 / Survey |
| F2 | `_has_client_tools` Bypass on `tool_choice: "none"` | Bypass client tools loop in `vision.py` when `tool_choice == "none"` | M1 | R1 / Survey |
| F3 | Client Harness Tool De-duplication | Suppress `_mios_sel` in `vision.py` when caller supplies tools or MiOS verbs | M1 | R1 / Survey |
| F4 | Gateway Context Token Budgeting & Pruning | Align context limit to 32k and apply tiered pruning before dispatch to prevent HTTP 400 | M1 | R1 / Survey |
| F5 | Gateway End-to-End Chat Completion | Verify `/v1/chat/completions` succeeds within context limits without HTTP 400 | M1 | R1 / Survey |
| F6 | DirectX Low-Power Preference (`GpuPreference=1;`) | Enforce `GpuPreference=1;` for wallpaper, webview, and iGPU executables on generic Power Saving GPU | M2 | R2 / Survey |
| F7 | 0 MB Discrete GPU Compute VRAM Isolation | Verify zero compute memory allocation on discrete / high-performance GPU during wallpaper and inference | M2 | R2 / Survey |
| F8 | Living Wallpaper `[colors]` SSOT Binding | Wire `mios.toml` `[colors]` tokens dynamically into `HKLM\SOFTWARE\MiOS\WallpaperUrl` | M2 | R2 / Survey |
| F9 | Rust Leaf Verifier Parity & Strangler Shims | Verify 100% byte parity and strangler shims for `mios-hardcode-lint` and candidate verifiers | M3 | R3 / Survey |
| F10 | Shared Daemon Infrastructure (`mios-service-core`) | Enforce zero cloud URLs and dynamic SSOT resolution across daemons and relays | M3 | R3 / Survey |
| F11 | Upstream FOSS Research & Synthesis | Research upstream FOSS patterns for context token budgeting, compression, and GPU scheduling | M4 | R4 / Survey |
| F12 | Two-Sided Verification Controls | Implement positive and negative controls for all modified components | M5 | R5 / Survey |
| F13 | Standing Gates & Sync Certification | Certify all 5 standing gates, `ci-suites.py`, `sync-bootstrap.py`, and `sync-generated.sh` clean | M5 | R5 / Survey |
| F14 | Comprehensive E2E Test Suite (Tiers 1-4) | Requirement-driven opaque-box test suite published via `TEST_READY.md` | M6 | Dual Track |
| F15 | Adversarial Coverage Hardening (Tier 5) | White-box adversarial testing and coverage gap verification | M6 | Dual Track |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Gateway Context Budgeting & Tool De-duplication | Implement `tool_choice: "none"` stripping, caller tool deduplication, and context pruning in `chat.py` & `vision.py` | none | DONE |
| M2 | Windows Low-Power iGPU Desktop & Wallpaper | Verify `GpuPreference=1;`, 0 MB discrete compute VRAM, and `[colors]` SSOT registry projection | none | DONE |
| M3 | Static Rust Consolidation (task_3454 / task_3455) | Verify 100% parity of `mios-hardcode-lint`, strangler shims, `mios-service-core` shared daemon crate | none | DONE |
| M4 | Upstream FOSS Research & Synthesis | Controlled research and documentation for context budgeting, prompt compression, and DirectX scheduling | none | DONE |
| M5 | Two-Sided Controls & Standing Gates | Two-sided test controls, all 5 standing gates, `ci-suites.py --check`, `sync-bootstrap.py`, `sync-generated.sh` | M1, M2, M3, M4 | DONE |
| M6 | Final E2E Test Suite & Adversarial Hardening | Pass 100% of E2E test suite (Tiers 1-4) and Tier 5 adversarial coverage hardening | M5 | DONE |

## Interface Contracts
### Gateway Ingress ↔ Backend LLM (`chat.py` / `vision.py` ↔ `llama-server`)
- Ingress: OpenAI-standard `/v1/chat/completions` on `http://127.0.0.1:8700`
- `tool_choice: "none"`:
  * Backend dispatch payload contains no `tools` array (or empty `tools: []`) and no `tool_choice` field.
  * Tool token overhead: exactly 0 tokens.
- Caller-supplied tools:
  * If caller tools count >= `DEFAULT_TOOL_CAP` or any tool matches a MiOS verb (`_name_is_verb`), `_mios_sel` is not appended.
  * All tool names are deduplicated.
- Context limit:
  * Effective context: 32,768 tokens (configurable via `MIOS_AGENT_PIPE_TOOL_CTX`).
  * If input tokens exceed budget, apply tiered pruning: stale tool results drop, intermediate message compaction, user input truncation. Backend never receives > 32k tokens.

### Windows Wallpaper ↔ DirectX & Registry
- Registry:
  * `HKLM\SOFTWARE\MiOS\WallpaperUrl`: `file:///C:/Windows/Web/MiOS/living-wallpaper.html?a0=...&a1=...&bg=...&fg=...`
  * `HKLM\SOFTWARE\MiOS\Wallpaper\Enabled`: DWORD `1`
  * `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`: `GpuPreference=1;` for `MiOS-Wallpaper.exe`, `msedgewebview2.exe`, `mios-wallpaperd.exe`, `llama-server.exe`
- Hardware:
  * Power-Saving / Integrated GPU: active 3D shader load on low-power adapter
  * Discrete GPU (multi-GPU setups): strictly 0 MB compute VRAM (degrading open on single-adapter / virtual systems)

### Static Rust Binaries ↔ CLI & Callers
- `mios-hardcode-lint`:
  * Exit code 0 on clean tree; exit code 1 on violations.
  * 100% byte-identical stdout/stderr against Python oracle.
- `mios-service-core`:
  * Dynamic layered SSOT resolution from `usr/share/mios/mios.toml`.
  * Forbidden cloud endpoint rejection (`api.openai.com`, `generativelanguage.googleapis.com`, `api.anthropic.com`).

## Code Layout
- `usr/lib/mios/agent-pipe/mios_pipe/routing/chat.py` — Gateway chat ingress routing & tool_choice handling
- `usr/lib/mios/agent-pipe/mios_pipe/routing/vision.py` — Gateway client-tools loop, tool deduplication, and context pruning
- `usr/lib/mios/agent-pipe/test_mios_chat.py` — Chat ingress unit tests
- `usr/lib/mios/agent-pipe/test_mios_vision.py` — Vision & client-tools unit tests
- `usr/share/mios/windows/Set-MiOSWallpaper.ps1` — Wallpaper setup & DirectX preference script
- `usr/share/mios/windows/mios-igpu-server.ps1` — Windows iGPU & RPC inference script
- `tools/native/mios-hardcode-lint/` — Compiled static Rust hardcode linter
- `tools/native/mios-service-core/` — Shared daemon and SSOT resolver crate
- `tools/native/mios-wallpaperd/` — Native Rust wallpaper daemon
- `tests/test-wallpaper-service.py` — Wallpaper service verification suite
- `tests/test-adversarial-igpu-rpc.py` — iGPU and DirectX preference adversarial tests
- `tests/test_native_static_hardening_e2e.py` — Native static Rust binary E2E test suite
- `tests/test-igpu-rpc-rust-e2e.py` — iGPU, RPC, and Rust consolidation E2E test suite
