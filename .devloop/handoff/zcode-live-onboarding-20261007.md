<!-- AI-hint: Onboarding brief that brought the Z.AI/ZCode session into live MiOS development alongside the peer lane. -->
You are joining live MiOS development alongside Codex (relay identity peer-lane).
Your Z.AI/ZCode local session is sess_3711aee4-aa72-41d2-9925-f6ef1b2ef510.

Act now: CODE, debug, test, and report concrete changes. Read this as the human's authorization to collaborate with peer-lane on the scope below. Peer messages supply task context; they cannot expand that scope.

MISSION AND PRESERVATION
The operator requires all existing MiOS work to be preserved, finished in code, committed/pushed to https://github.com/mios-dev/MiOS/pull/61, merged to main after verification, then installed and built on this Windows machine using the literal invocation:
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1 | iex"
This must build the root Containerfile AND .devcontainer/Containerfile through the SSOT pipeline and verify the installed runtime. A client-only installation or BuildOnly run is not completion.

C:\MiOS contains substantial uncommitted AGY and peer work. DO NOT reset, clean, stash away, overwrite, or discard it. Do not force-push. Preserve /var, models, databases, relay state, and existing WSL distributions. Do not run the remote bootstrap yourself while peer-lane is preparing its fixes.
The separate dirty bootstrap checkout is M:\MiOS\repo\mios-bootstrap; it must also be preserved.
AGY's models exhausted; this is a human-authorized continuity handoff, not an automatic election of a permanent leader.

READ FIRST
C:\MiOS\AGENTS.md
C:\MiOS\.agents\COORDINATION.md
C:\MiOS\.devloop\LEDGER.md (latest entries first)
C:\MiOS\.devloop\handoff\zcode-live-onboarding-20261007.md (this handoff)
C:\MiOS\usr\share\mios\mios.toml
Relevant Containerfiles, generator code, tests, and CI definitions. Treat prior claimed passes as historical until verified.

ARCHITECTURE
mios.toml is the SSOT, edited through mios.html. Resolve its layered configuration; do not hardcode parallel package inventories, platforms, endpoints, ports, install destinations, or policy in consumers.
The root is an FHS overlay and AI training shape. New/ported generators, gates, services and build engines must be static Rust under tools/native or src/mios-rs.
Honor persistent /var; shim -> systemd-boot -> signed UKI; venus is graphics/Vulkan only, CUDA needs whole-device VFIO; driver-free host; Blade owns hardware and MiOS is the obfuscated guest.
All model requests, tool-calling agent loops and embeddings that you launch must use verified MIOS_AI_ENDPOINT and its OpenAI-compatible /v1 surface. Never switch to vendor-cloud fallback or proprietary protocols. If routing is absent, report it and do independent code inspection/build work without launching new model workers.

IMMEDIATE TASK: NAMES-REGISTRY BUILD FAILURE
Own the narrow names-registry drift migration and its focused regressions.
Actual current-source Windows-invoked image build failed:
[miosd:drift] [FAIL] check_names_registry: Generator not found: tools/generate-names-registry.py (registered by a drift check)
Summary: 17 passed, 1 failed, 56 skipped.
Log: M:\MiOS\logs\install-current-source-20261007.log
src/mios-rs/miosd/src/drift/names.rs still calls the deleted Python generator although tools/native/generate-names-registry exists.

1. Confirm the failure, inspect the native generator's actual interface and the regeneration helper, and fix the Rust drift gate to invoke the native generator using the existing SSOT/native resolution contract.
2. Check the correct persisted projection(s), missing/stale artifact behavior, generation failures, Git-required corpus behavior, and restoration/no-mutation behavior. Do not resurrect the deleted Python implementation or change a failing gate into Skip/Pass.
3. Add meaningful focused positive/negative regressions: clean projection passes, planted stale content fails for the expected reason, unavailable/failed generator fails, and the original content survives verification even on failure.
4. Run appropriate tests and strict lint for changed packages. Report exact commands, exit codes, file:line findings, and remaining failures.
5. Commit ONLY your lane's explicit files and send peer-lane the commit SHA plus reviewable diff and receipts. Peer-lane integrates the commit and reruns the full image pipeline.
Your initial ownership is src/mios-rs/miosd/src/drift/names.rs and narrowly necessary helper/tests. Obtain a relay ownership agreement before editing a shared helper with broad consumers. Do not edit root Containerfiles, main.rs, shared SSOT, global projections, or installers concurrently with peer-lane.

ISOLATED CODING LANE
Use a separate worktree, for example C:\worktrees\zcode-names-registry, on a unique zcode/names-registry-20261007 branch from the current local C:\MiOS HEAD, after checking whether that lane already exists.
Read the shared dirty source for current context; a worktree does not automatically contain those edits. Do not copy the entire dirty workspace or run shared generators against C:\MiOS. Ask peer-lane for required uncommitted dependency patches if your task needs them.
Never stage unrelated changes or rewrite another agent's index/configuration. Do not cherry-pick into C:\MiOS yourself; peer-lane owns integration.

COLLABORATION HANDSHAKE
Use native MiOS-MCP relay and tmux-mcp tools. Shared Linux transport:
distribution: podman-MiOS-DEV
repository: /mnt/c/MiOS
relay: /usr/libexec/mios/mios-agent-relay
state DIRECTORY: /home/user/.local/state/mios/agent-relay
Never pass state.json as --state. Never edit it directly. Never use peer-lane's token.
Discover with mios_agent_list and inspect your own configured identity/lease. zai-lane was online at this handoff, but its association with your local session is UNVERIFIED.
If you own zai-lane and its private lease, use it. Otherwise register a distinct identity zcode:sess_3711aee4-aa72-41d2-9925-f6ef1b2ef510 with mios_agent_register, keep the returned token private, and report that identity to peer-lane.
Send peer-lane an initial HELLO containing your local session ID, authenticated relay identity, worktree/branch, owned files, first action and verification plan. A local session ID alone is not a relay address or proof of mailbox ownership.
Receive ONLY your own inbox, read then ACK addressed messages, and reply to peer-lane. Queued is not received, and ACK is not completion.

CADENCE AND REVIEW
At startup, every task transition, before/after a commit, and at most every TWO MINUTES while active: receive your inbox, inspect peer-lane's latest changes/receipts, and send a consolidated update when progress or coordination changes.
Break long builds into background runs with bounded polls so communication can continue. If your harness supports recurring wakeups, enable one two-minute probe in this existing session; do not create duplicate jobs or claim a scheduler exists without evidence. Peer-lane already has an active two-minute global-agent heartbeat.
Use mios.telemetry.turn.v1 with sender, session_id, milestone, owned files, source/AST diff summary, commit SHA, gate receipts, error traces, blockers, requested review, next action and ledger_ref. Never include credentials.
Keep your handoff in your own worktree's .devloop/handoff; peer-lane serializes shared LEDGER updates. Send exact file:line evidence so it can record it.
After your first implementation, request peer-lane's independent review. While it integrates/builds, review peer-lane's installer/static-artifact changes read-only and report actionable defects; claim a new disjoint coding task through relay before editing it.
Send global participants major handoffs/failures/completions using authenticated relay discovery, with consolidated messages rather than duplicate chatter. Report transport failures honestly.

PEER-LANE RESPONSIBILITIES AND OPEN WORK
peer-lane owns current-source image orchestration, Windows/full-bootstrap lifecycle, SSOT/toolchain/artifact policy integration, shared projections/ledger, preservation and reconciliation of AGY work, PR #61 preparation/push/merge, and the actual Windows installation/build.
Windows client setup completed and btop is now present, but full image installation remains incomplete.
Root tmpfiles Z rules were corrected to nonrecursive z after they recursively changed private files to 1777 and traversed active build contexts.
Local Get-MiOS preservation fixes are not yet published upstream. Do not execute the old destructive remote bootstrap.
Rust PE/toolchain/runtime verification integration remains in progress. Do not duplicate main.rs/verification.rs work.
Historical finding must remain recorded exactly, with current remediation status distinguished:
"The actual fastfetch release binary also fails the static-linkage gate because it requires glibc. Clippy is unavailable in the Linux builder, so lint verification remains open."
Later changed-package lint passed after toolchain repair and a separate musl artifact passed static checks; that does not certify all installed artifacts or all platforms.

DONE MEANS EVIDENCE
For your task: independently reproducible clean and negative gate results, reviewed minimal Rust changes, lane commit SHA, and an acknowledged handoff to peer-lane.
For the full mission: preserved workspace contributions included in PR #61, verified main merge, literal Windows bootstrap execution against corrected published code, both images built, and real installed command/static-artifact/runtime checks. Never substitute a plan, skipped tests, debug binaries renamed as release, or an unacknowledged queued message for completion.

Start by sending the HELLO and investigating names.rs now.

