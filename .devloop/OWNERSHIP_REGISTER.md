<!-- AI-hint: Exclusive ownership register and workflow boundaries for the agent lanes that develop MiOS core. -->
# MiOS CORE Development — Exclusive Ownership Register & Workflow Boundaries

*Updated: 2026-09-29 per operator correction: `cat` retired; `field` is canonical.*

---

## 🚫 Strictly Reserved to Codex & Single Integration Owner (READ-ONLY for All Lane Workers)

Under no circumstances may any worker subagent modify or stage changes to the following paths:
- `usr/share/mios/mios.toml` (RESERVED: workers submit proposed registrations to the orchestrator; one integration owner applies and regenerates projections after merging)
- `C:\MiOS\usr\libexec\mios\mios-mon.py` (canonical monitor)
- `Get-MiOS.ps1`, `build-mios.ps1` (both Windows and Linux mirrors)
- Windows Terminal profiles/settings, theme assets, Windows launchers, centering, DPI, focus, shortcuts
- Running installer state on `M:\` and running processes (`podman-MiOS-DEV`, WSL distro)
- Bootstrap `mios.toml` and root configuration assets

---

## 📋 The 5 Disjoint MiOS CORE Workflows & Exclusive File Allocations

| Workflow | Task(s) | Domain | Exclusively Owned Files | Forbidden Paths | Dedicated Worktree |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Lane 1** | `task_195` | Lifecycle / Offline Upgrade | `automation/43-uupd-installer.sh`<br>`automation/50-uupd-installer.sh`<br>`usr/share/doc/mios/manual/offline-upgrade.md` | `usr/share/mios/mios.toml`, core runtime, Quadlets, Codex files | `C:\worktrees\wf-t215-uupd`<br>Branch: `devloop/t215-offline-upgrade` |
| **Lane 2** | `task_429` | Agent-Pipe / PSI Monitoring | `usr/lib/mios/agent-pipe/mios_psi.py`<br>`usr/lib/mios/agent-pipe/server.py` (narrow lifecycle hook)<br>`usr/lib/mios/agent-pipe/test_mios_psi.py` | `usr/share/mios/mios.toml`, dispatcher core, router core, non-PSI pipeline logic | `C:\worktrees\wf-t485-psi`<br>Branch: `devloop/t485-psi-monitor` |
| **Lane 3** | `task_802` | Security / Core Scheduling | `usr/libexec/mios/mios-core-sched`<br>`automation/24-cpu-affinity.sh`<br>`tests/test-core-sched.sh` | `usr/share/mios/mios.toml` (submit registrations to orchestrator), UKI scripts, agent-pipe, monitor, installer | `C:\worktrees\wf-t858-coresched`<br>Branch: `devloop/t858-core-sched` |
| **Lane 4** | `task_860`<br>`task_861` | Security / ModSign & Lockdown | `automation/02-uki-bootloader.sh`<br>`etc/cmdline.d/02-security.conf`<br>`usr/lib/bootc/kargs.d/30-security.toml`<br>`tests/test-kernel-module-signature-enforce.sh` | `usr/share/mios/mios.toml` (submit registrations to orchestrator), Core scheduler, CPU affinity, offline upgrade | `C:\worktrees\wf-t916-modsign`<br>Branch: `devloop/t916-t917-modsign` |
| **Lane 5** | `task_239`, `task_3411` | Deploy / MiOS-Field staging and flash | `field/**`<br>`installation/mios-common.ps1`<br>`installation/mios-common.sh`<br>`installation/mios-install.ps1`<br>`installation/mios-install.sh` | `cat/**` (retire), `M:\` live state, MiOS root repo, `Get-MiOS.ps1`, `build-mios.ps1` | `C:\worktrees\codex-t261-recovery`<br>Branch: `codex/t261-recovery` |

---

## 🔒 Architectural Rules & Standing Gates
1. **Isolated Worktrees**: Every lane executes within its own directory under `C:\worktrees\`.
2. **SSOT Reserved Boundary**: `usr/share/mios/mios.toml` is NOT edited by workers. Workers output their proposed phase/tier additions in their report. The orchestrator / integration owner applies them in one atomic commit and runs `bash ./tools/sync-generated.sh`.
3. **Phase Registry & CI Suites Invariants**:
   - Lane 3 proposals: `24-cpu-affinity.sh` to `[build.phases].list`, `test-core-sched.sh` to `[ci.tiers]`.
   - Lane 4 proposals: `02-uki-bootloader.sh` to `[build.phases].list`, `test-kernel-module-signature-enforce.sh` to `[ci.tiers]`.
4. **Clean Exit**: Each lane must pass unit tests and verify self-contained scripts before reporting done.
5. **No Push to Main**: Workers commit strictly to their feature branches; integration to `main` is handled strictly after orchestrator verification against current `main`.
