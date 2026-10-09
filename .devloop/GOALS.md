<!-- AI-hint: Standing MiOS goal: operator objective, measurable stopping conditions with positive and negative evidence, baseline, milestones and non-goals. -->
# MiOS standing goal

Operator objective (verbatim, 2026-10-07; re-anchored 2026-10-08):

> port all platforms shell scripts to rust static binaries for both linux and windows for MiOS;
> code and target containerfile(s) feed from SSOT--edited from mios.html--renders selections from
> file--overlays MiOS git root to root FHS--irm|iex web invoke bootstrap; monitor, debug,
> code--reinstall--ALL until MIOS IS COMPLETED ON CODE AND BOOTSTRAPS/INSTALLS/BUILDS/AND CAN SELF
> HOST/SELF BUILD--all required dependencies, packages etc-etc render from a user defined SSOT FILE

Amendment (operator, 2026-10-08): fold everything into **static Rust binaries** and **minimize the
number of disparate binaries**. Organize code as a few larger **MiOS-MODULES** (multi-call binaries
that own a whole domain) for a more controlled environment. Minimize global directories: prefer
**shallow trees** from MiOS's `.git`/ROOT.

A command finishing is not completion. Each condition below needs a positive control (the check
passes on the real tree or system) and a negative control (a planted defect makes it fail).

## Stopping conditions

| ID | Condition | Evidence that closes it |
|----|-----------|-------------------------|
| SC-1 | **No scripts on product paths.** Every build, install, runtime and bootstrap path runs a static Rust binary. Shell, PowerShell and Python survive only as declared thin shims or as AI-plane model or runtime bindings. Both are listed in a shrink-only SSOT register. | Register count is 0 outside the allowed classes; a drift gate fails on any new unregistered script |
| SC-2 | **Few, static binaries.** Linux artifacts are fully static. Windows artifacts link the CRT statically. Functionality is grouped into MiOS-MODULES (multi-call binaries) rather than one binary per tool. | Static-linkage audit is green for both targets; binary count ≤ an SSOT ceiling that only goes down |
| SC-3 | **SSOT renders everything.** Containerfiles, package sets, install, build, runtime and Windows/Linux variants all derive from layered `mios.toml` (vendor → host → user). | `mios-gen sync --root . && git diff --exit-code` clean; `miosd drift-check` reports PASS for every check with 0 FAIL and 0 MISSING |
| SC-4 | **mios.html is the editor.** Edits in `usr/share/mios/configurator/mios.html` round-trip losslessly into the user SSOT layer, and SSOT changes render back. | Round-trip test with a planted lossy edit (negative) |
| SC-5 | **Overlay.** The image overlays the MiOS git root onto `/` (FHS), and `/` remains a git work tree. | `git -C / status` works in an installed image; drift gates prove the overlay mapping |
| SC-6 | **Literal bootstrap.** Running `irm <Get-MiOS.ps1 URL> \| iex` on Windows performs the full install → build → runtime path with no manual step. | Executed run log, green end to end |
| SC-7 | **Self-host and self-build.** An installed MiOS rebuilds its own OCI image from its own SSOT, through `bootc container lint`. | `mios build` on MiOS reaches lint green |
| SC-8 | **CI green on both publishers.** drift-gate, build, smoke-test and CodeQL (no high or critical alerts) pass on `main`. | GitHub and Forgejo run URLs |
| SC-9 | **Shallow tree.** Fewer top-level entries and directories, and less depth, under the repo root. Each move is lossless, with every consumer updated and gates proving it. | The counts below decrease and never regress (ratchet) |

### Baseline (2026-10-08, PR #61 head + recovered work)
- tracked: 344 `.sh`, 73 `.ps1`/`.psm1`, 947 `.py`, 243 `.rs`
- `usr/{bin,libexec}` extensionless entrypoints: 125 shell, 157 Python
- Rust crates: 32 in `tools/native`, 6 in `src/mios-rs`; about 40 installed binaries
- tree: 3833 tracked files, 85 top-level entries, 668 directories, directory depth 9, file depth 10
  (deepest: `tools/mios-portal-app/app/src/main/java/io/mios/portal/`)
- drift: 15 checks MISSING (ports in progress)

## Milestones
- **M0, current:** recover Codex thread 01a116b2 (unified build console, native dashboard, Windows
  tmux profile, native drift checks) and push PR #61 with drift-gate green (0 MISSING) and the
  CodeQL highs fixed. Merge to main once CI is green and the operator confirms.
  - **Status (2026-10-09):**
    - Integration `7aebb7a6` is pushed to PR #61. The companion bootstrap PR is mios-dev/mios-bootstrap#26.
    - Replay of `aa0ab95b`: generated, lint and Rust are green; unit is 415/2; the gate is down to 7 checks.
    - Seven lanes are merged: mcp-aio, gate-ledger, gate-tests, gate-docs, gate-neg, docs-ratchet-1 and gate-reg.
    - Still open:
      - gate-legib: shell_lines is 492 over;
      - value-dup: task_942/task_957;
      - Hyper-V VHDX (P0-9 Phase A).
    - ETA: local replay green in about 3–5 h; GitHub CI green 2–5 h after that, since the smoke bake has not been green since 08-21; then merge on the operator's go-ahead.
- **M0.5, before the merge that publishes `:latest`** (from `docs/research/upstream-prior-art-gaps-2026-10.md`):
  - P0-2: move the source-tree gates (phases 97/98, `miosd drift-check`) out of the Containerfile bake; the bake keeps image-content assertions only.
  - P1-4 (operator ruling Q5): keep the default password `mios`, but ship it **expired**, so the first console or Cockpit login must change it.
  - P0-7: condition off the simulated attestation server.
  - P0-4: lock down `miosd config-server` (same-origin, Host allowlist, per-launch token, no secrets on GET).
- **M1:** run the literal Windows `irm | iex` bootstrap through to a full SSOT-derived install and build (SC-6).
- **M2:** MiOS-MODULES consolidation plan. Map the ~40 binaries into domain modules, extending
  `miosd`'s multi-call pattern, and set the SSOT binary-count ceiling. The operator's rule
  (2026-10-08): new code goes into the component whose domain it is, not into `miosd` by default.
  The existing domain homes are:
  - gates: `mios-gate`
  - generators and projections: `mios-gen`
  - build and the progress ledger: `mios-build`
  - SSOT resolution: `mios-resolver` and `mios-config`
  - node or edge runtime: `mios-node`
  - probes: `mios-probe`
  - daemon and CLI dispatch: `miosd`

  First candidate: the native drift registry (`src/mios-rs/miosd/src/drift/`, 74 checks) belongs to
  `mios-gate`, which already runs `drift-stubs` and `version-literals-ssot`.
- **M3:** script-port burn-down by domain, ratcheted per SC-1.
- **M4:** mios.html ↔ SSOT live round trip (SC-4). Operator research (2026-10-08) recommends
  parent-directory inotify (not file inodes, to survive atomic-rename saves), a debounced persistent
  watcher with content-hash echo suppression,
  Operator rule (2026-10-08): mios.html is THE setup interface, and no operator should have to
  hand-edit or check other files. It must use progressive disclosure: essentials up front, with
  excess toggles and settings behind an **Admin settings / Extras** sub-menu for power users, so
  the default view is not overwhelming. 
  **Research correction (2026-10-08, brief §2 P0-5/P0-6, §3 P1-21/P1-22):**
  - The page lives in immutable `/usr`, so it cannot be the authority.
  - The page keeps a JSON view and POSTs key-level patches `[{path, op: set|unset, value}]`. `mios-resolve set` applies them with toml_edit 0.25 into the tier each key declares (`x-mios-tier`). Host-tier writes go over a `SO_PEERCRED` socket. The same Rust core is inlined as wasm32 for `file://`.
  - The browser-side TOML parser corrupts 150 non-empty values today, so a no-op save must change zero keys.
  - Build the Admin settings / Extras panel from per-key `x-mios-ui.level` (essential | advanced | internal) and group metadata, not from hand-placed markup. Home Assistant is removing its single global Advanced switch in favour of per-group sections.
  - Secrets render as write-only fields stored through systemd-creds.

  Superseded draft design: the TOML script-island in mios.html as authority with
  `data-key` form bindings, `toml_edit` for comment-preserving writes into the host or user layer
  (`/etc/mios/mios.toml`; never `/usr`), write-tmp + fsync + rename + dir-fsync, SSE or
  peer-credential UDS back to the page, and section-scoped last-write-wins on conflict. Per SC-2, build
  it as a `miosd` subcommand (one MiOS-MODULE), not a new `mios-syncd` binary. mios.html already
  POSTs `/portal/config` when embedded and falls back to File System Access or download.
- **M5:** self-build on installed MiOS (SC-7).
- **Cloud/dev hosts and the Hyper-V image (brief P0-8, P0-9):**
  - Cloud images carry no AI weights and no desktop apps; that is the operator's design. Otherwise every image is equivalent.
  - The published image (49 GB unpacked) carries 26 GB of baked sidecar images and 5.8 GB of light GGUFs. Two moves shrink every image equally and fit the 30–32 GB hosted sandboxes:
    - sidecars become logically bound images, pulled on use (P0-3);
    - weights move out of `/usr` (P1-24).
  - MiOS ships as a **Hyper-V image** (VHDX) too:
    - Build it with native `mios-build artifact vhdx` from `[deploy.formats.vhdx]`. Identity comes from SSOT, not placeholders.
    - CI builds and boot-tests it.
    - It is delivered by a local build from the published digest (release assets are capped at 2 GiB).
  - `[bootstrap.dev_vm].provider = "hyperv"` makes MiOS-DEV a MiOS VM with real `bootc` upgrade and rollback. GPU-PV comes through dxgkrnl. The WSL provider cannot `bootc switch`.
- **M6:** shallow-tree collapse (SC-9), in batches, after PR #61 is green. Each batch rewrites every
  consumer (code, units, CI, Containerfile, docs and links), re-runs `mios-gen sync` and the full gate,
  and drops no feature. Reference counts below were measured on 2026-10-08 and exclude generated
  indexes.
  - **T1, low blast radius:**
    - merge the duplicate spellings `usr/share/mios/open-webui/` and `openwebui/` (2 files, 14 refs)
    - `tests/templates/golden/` → `tests/golden/templates/` (20 files, 4 refs)
    - `docs/agy/w10-live-boot/` → `docs/w10-live-boot/` (28 files, 5 refs)
    - `usr/share/doc/mios/archive/absorbed-plans-2026-06/` → `archive/` (9 files, 3 refs)
    - `usr/share/mios/prompts/upstream-researched-patterns/foss/` → `prompts/foss-patterns/` (14 files, 7 refs)
    - `images/coderun-sandbox/` → fold into `images/` (2 files, 2 refs)
  - **T2, coupled to M2 (MiOS-MODULES):** unify the two Rust workspaces, `src/mios-rs` (125 files,
    170 refs) and `tools/native` (165 files, 236 refs), into one domain-organized workspace. That also
    takes `src/` from a single-child chain to the module root.
  - **Locked, not moved:**
    - FHS and OS-mandated paths (`etc/`, `usr/lib/*/*.d`, `usr/share/doc`, `cloud.cfg.d`,
      `sshd_config.d`, `rancher/k3s`)
    - harness layouts (`.github/`, `.forgejo/workflows`, `.agents/plugins`, `.claude/commands`)
    - toolchain conventions: the Android/Gradle app (`tools/mios-portal-app`, the depth-9 branch) keeps
      Gradle's `src/main/java/<package>` layout unless it moves to its own repository.
- **M7: MiOS-Field hypervisor and blade architecture.**
  - **Sources:**
    - design: `docs/design/mios-field-hypervisor.md`;
    - the one master page: the MiOS Topology Atlas, https://claude.ai/artifact/3NcyFYkcsskVpfp4KurUoA;
    - tasks: the M7 epic in `tasks.jsonl`.
  - **Model (operator, 2026-10-09):**
    - L0 is the hardware.
    - L1 is SystemRescue running from RAM: the admin plane.
      - Its interface is the tmux TUI desktop: tmux-os / MiOS-tmux, plus the Python and Rust MiOS TUIs.
      - Remote admins reach it by IP-KVM over the separate `wg-ipkvm` mesh.
      - **GPU mode** (resolves D5):
        - attended, the default: L1 keeps the iGPU for the TUI console, and the seat takes a second
          GPU or an iGPU virtual function;
        - headless: L1 has no local console and is administered by SSH or serial on the admin mesh.
          The iGPU passes to the seat `mios`, the dGPU goes to the dGPU VM, and Looking Glass carries
          frames between them.
    - L2 holds sibling VMs on L1:
      - one or more MiOS VMs, counted by hardware pressure; one of them is the users' graphical seat
        `mios`, with Looking Glass, kvmfr and Sunshine on the seat GPU;
      - `mios-xbox`, with the dGPU over one VFIO hop. It hosts Quadlets through its WSL2 MiOS.
      - Each MiOS VM can run nested MiOS containers: full images under podman `--systemd=always`,
        counted by hardware pressure. They share the VM's GPUs through CDI, which is how a single-dGPU
        host runs several MiOS images.
    - L3 is Quadlets.
      - Every MiOS image is full and equivalent, so any L2 MiOS (a VM, a nested container, or the WSL2
        MiOS) can host any Quadlet.
      - There is exactly one live instance per Quadlet across the fleet, counting VMs and nested
        containers. The other copies are paused standbys that take over on failure.
      - SSOT-listed core services may be promoted from L3 into an L2 image.
    - MiOS self-hosts its forge, build, signing, updates, mesh, storage, orchestration and AI plane.
  - **Hardware floor for an L1 host:**
    - a CPU with an iGPU, in both modes: the L1 console in attended mode, the seat GPU in headless
      mode;
    - an IOMMU that isolates each GPU passed through;
    - a dGPU;
    - in attended mode, a seat GPU: a second discrete GPU, or an SR-IOV virtual function of the iGPU.
  - **Phases.** "+" is the positive control (must pass); "-" is the planted defect (must fail).
    - **F0, decisions.**
      - D1 is resolved: siblings.
      - D2: should L1 come in two flavours, MiOS-Field live (SystemRescue) and MiOS-Metal installed
        (bootc MiOS as its own L1)? The operator has said MiOS can be its own L1; whether both ship,
        and which is the default, is open.
      - D3: should MiOS adopt Tetragon?
      - D5 is resolved by mode: attended (the default) or headless, as in the model above.
      - +: each decision task records the ruling.
      - -: closing a decision task without the ruling as evidence is refused.
    - **F1, L1 image.**
      - AC: `miosd artifact-build field-hypervisor` builds an SRM-customized SystemRescue ISO from a
        new `[field.hypervisor]` table:
        - the version floats above an SSOT floor;
        - the SHA-256 is read from upstream's `.sha256` and recorded in the SBOM;
        - the SRM carries the static Rust admin TUI.
      - +: a QEMU boot test shows kvm and vfio loaded, libvirtd active, the checksum passing and the
        TUI on tty1.
      - -: a tampered ISO fails the checksum; a package removed from `[field.hypervisor]` fails the boot
        probe that names it.
    - **F2, MiOS-Field integration.**
      - AC: the launchers read the version floor from SSOT, and the hypervisor Ventoy entry is rendered
        from SSOT and mirrored to mios-bootstrap (Law 15).
      - +: `mios-gen sync` renders it without drift, and the bootstrap-sync check passes.
      - -: a literal 13.02 in a launcher fails the version-literal gate.
    - **F3, admin plane.**
      - AC: L1 joins `wg-ipkvm` at boot. The TUI and SSH listen only on that interface. IP-KVMs are
        declared per blade.
      - +: from an admin-mesh peer the TUI answers; from the blade mesh it is unreachable.
      - -: a route between the meshes, or an L2 address on `wg-ipkvm`, fails the isolation check.
    - **F4, early VFIO.**
      - AC:
        - a mode key in `[metal]`, beside `dgpumode`, selects attended or headless, and L1 applies it
          at boot;
        - the dGPU and the seat GPU are chosen by class selectors in `[metal.gpu]`. In attended mode
          every selector excludes the iGPU's physical function; in headless mode the iGPU is
          selectable for the seat only;
        - the initcpio hook, the `modprobe.d` softdeps and the kernel arguments are all rendered from
          those selectors.
      - +: in a QEMU test with an emulated device, the device is bound to vfio-pci before its driver
        loads. In attended mode the iGPU keeps its driver; headless renders an iGPU VFIO binding for
        `mios` and no L1 console.
      - -: each of these fails:
        - a PCI address literal in SSOT (`check_metal_vfio`);
        - an attended-mode selector that matches the iGPU;
        - a host with no iGPU, in either mode, failing at install.
    - **F5, L2 MiOS VMs.**
      - AC:
        - `[blade.mediator]` declares the VM shape and the VM-count policy, within
          `[metal].guest_cpu_percent` and `guest_ram_percent`;
        - libvirt domains are generated from SSOT;
        - management binds a `[ports]` key on the blade mesh only;
        - each MiOS VM runs nested MiOS containers from the full image, counted by hardware pressure;
        - the containers share the VM's GPUs through CDI, with per-container VRAM budgets from SSOT.
          The existing keys to build on are `[ai.vllm].gpu_util`, `[ai.sglang].mem_fraction` and
          `[ai.host_thresholds].max_vram_percent`.
      - +: the domains regenerate with no diff; the seat VM boots the published qcow2; two nested
        containers serve GPU lanes on one dGPU within their budgets.
      - -: each of these fails:
        - a VM count whose shape exceeds the guest budget;
        - `0.0.0.0` in a management bind;
        - a nested container started from a non-full image;
        - VRAM budgets on one GPU summing above 100%.
    - **F6, GPU arbiter.**
      - AC:
        - a native state machine (AI → draining → released → gaming → returning);
        - it uses vLLM sleep mode and a vsock request to L1;
        - `/v1` fails over to lanes off the dGPU;
        - the drain covers every GPU user in the VM and in every nested container.
      - +: the full cycle runs green against mocked sysfs, QMP and `/v1`.
      - -: a planted rebind failure must roll back to the AI lane, never leave the dGPU unattached, and
        never attach it to two guests. A nested container still holding a CUDA context must block the
        hand-off and trigger a rollback.
    - **F7, seat sessions.**
      - AC:
        - Looking Glass runs across the siblings through an IVSHMEM file on L1, whose size comes from
          SSOT (kvmfr's hard-coded 128 MB goes);
        - Sunshine runs on the seat GPU;
        - the consoles are served on the blade mesh only.
      - +: a frame-path probe and a Moonlight handshake both succeed.
      - -: a kvmfr size that disagrees with the IVSHMEM size fails the projection; a console bound to
        the admin mesh fails.
    - **F8, `mios-xbox` and L3.**
      - AC:
        - `mios-xbox` is generated as an L2 sibling, with the dGPU, swtpm, OVMF Secure Boot, the Looking
          Glass host app and its WSL2 MiOS;
        - Quadlets are placed across L2s and moved locally;
        - each Quadlet holds a single-live lease, with paused standbys and failover;
        - the promotable-core list is in SSOT.
      - +: the placement renders, and a pause-here / resume-there move completes.
      - -: two live instances of one Quadlet fail the gate, and so does a non-core Quadlet marked for
        promotion.
    - **F9, fleet, mobility, security and self-hosting.**
      - AC:
        - the three-phase flightpath (detach, transit, attach with an architecture check);
        - per-blade egress gateways from `[blade.uplink]`;
        - reconcile tests per data class;
        - the sandbox ladder, with krun from ruling Q18;
        - an offline blade that builds, signs, publishes to its own Forgejo and upgrades every MiOS
          layer.
      - +: a two-node migration resumes on the target.
      - -: a cross-architecture attach is refused; a swapped reconcile rule fails its class test; a WAN
        dependency fails the offline proof.
    - **F10, projection.**
      - AC: the design doc's `[ports]` citations and the Atlas labels are checked against SSOT.
      - +: the gate passes on the tree.
      - -: a wrong port value in the doc fails the gate, naming the key.
  - **Dependencies:**
    - F1 builds on the native artifact path (`miosd artifact-build`, already merged).
    - F5 and F8 depend on the per-format tasks under ruling Q9: qcow2 for the MiOS VMs, MiOS-Xbox for
      `mios-xbox`, WSL for its WSL2 MiOS.
    - F9's sandbox ladder depends on the Q18 krun task.
    - F9's self-hosting proof links the M5 self-build task, the Q4 signing task and the fleet-update
      task rather than duplicating them.
    - F4 and F5 extend the Metal class-selector and domain-generator task (brief P2-5).
- **M8: one tmux TUI desktop in every MiOS image.**
  - **Goal:** tmux is MiOS's graphical desktop for managing and reaching VMs, images, services and
    agents. The same TUI runs inside tmux in every MiOS image:
    - L1 SystemRescue, as the attended console on the iGPU;
    - L2 MiOS VMs and their nested MiOS containers;
    - WSL2, including inside `mios-xbox`;
    - cloud and devcontainer;
    - Windows, in native `tmux.exe`.

    It replaces today's monitors, `mios ai` and the other TUIs.
  - **Sources:**
    - design: `docs/design/doc-tmux-os-integration.md`, section 7;
    - tasks: the M8 epic in `tasks.jsonl`.
  - **Operator rulings, 2026-10-09 (binding):**
    - **R1.** The desktop belongs to `miosd`, as an applet in its multicall binary. This is an explicit
      exception to M2's "new code goes into its domain, not miosd by default": miosd is already the
      `mios` dispatcher and the tmux namespace guard. The ADR-0021 amendment, which plans miosd as a
      thin exec shim, must record this exception.
    - **R2.** Panes render natively in Rust, with ratatui and crossterm, inside tmux. Python
      Textual/Rich and the runtime `pip install` are retired.
  - **Out of scope:** the agent planes stay separate and are not part of the human desktop. They are
    `mios-mcp-server` with its tmux bridge, `mios-shell-session`, and `tmux-mcp`.
  - **Phases.** "+" is the positive control (must pass); "-" is the planted defect (must fail).
    - **U1, SSOT and render.**
      - AC:
        - `[terminal.desktop]`, `.layout`, `.views.<id>` and
          `.env.<sysrescue|vm|wsl|cloud|windows>` own the desktop. They absorb the
          `[mcp.tmux.workspace]` geometry, `[terminal.monitor]`, `[dashboard]` and the
          `[mcp.agents.observation]` UI keys. (`[desktop]` is already GNOME/Flatpak's.)
        - `[terminal.startup]` returns to vendor, and bootstrap's `[terminal.monitor]` schema is
          reconciled (Law 15).
        - One layered loader remains: `mios-resolver`.
        - The runtime render (`mios-gen render-tmux-theme --runtime`) runs in pure Rust, with no
          `theme_sync.py` and no `mios-unit-gen` subprocess.
      - +: the runtime render matches its golden with no `python3` and no `mios-unit-gen` on PATH;
        `mios-toml-get terminal.startup linux` resolves from vendor.
      - -: a key restated in both the old and the new table fails `check_no_duplicate_value_key`; a
        second loader fails the single-loader check.
    - **U2, the miosd desktop applet.**
      - AC:
        - it owns the session and namespace, folding in `mios-terminal` (a path shim remains);
        - it owns the layout engine, moved out of `mios-agent-relay` `workspace()`;
        - it provides a ratatui view framework, static on Linux musl and Windows.
      - +: `mios terminal` attaches the `-L` session through miosd; layout goldens pass per profile;
        views pass TestBackend goldens; the static-linkage audit passes.
      - -: a view that panics below the minimum size fails; a socket directory owned by another user is
        refused.
    - **U3, views.**
      - AC: the views are:
        - clients chooser and agents observer, with the Rust versions primary;
        - system and services, from the `mios-service-core` catalog and probe, with `mios-probe`
          facts replacing fastfetch;
        - build progress, from the `mios-build` ledger;
        - flash;
        - **new:** VMs (L2 VMs, nested MiOS containers, the M7 arbiter state);
        - **new:** Images (bootc and podman, and the testing/stable channels);
        - btop as an optional pane.
      - +: each view renders its fixture golden, and production callers reach the Rust views.
      - -: a planted `fastfetch` call fails; a missing data source shows as unavailable instead of
        crashing; an image channel absent from `[image.streams]` is flagged.
    - **U4, dispatch.**
      - AC: one Rust table routes terminal, ai, agents, mon, dash, mini and btop, on Linux and through
        `mios-launch --dispatch`. It fixes the `ai` conflict: miosd opens Open WebUI while
        `usr/bin/mios` opens the tmux workspace.
      - +: a parity test shows every front door resolving each verb to the same target.
      - -: a planted divergent route fails, naming the verb.
    - **U5, environments.**
      - AC:
        - L1: static binaries and a rendered vendor `mios.toml` on the MiOS-Field data partition, and
          the desktop on tty1. The two drifted autorun copies are reconciled first.
        - L2: the desktop hotkeys call an installed terminal, not alacritty.
        - WSL: no literal `podman-MiOS-DEV`.
        - cloud and devcontainer: the desktop runs without systemd.
        - Windows: `miosd.exe` runs in `tmux.exe` panes, and `host_tmux::stage` is the only
          `.tmux.conf` writer.
        - Blink: it enters through `mios terminal`.
      - +: a per-environment smoke test reaches the desktop.
      - -: each environment's planted defect fails: diverged autorun copies, a literal distro name,
        an extra `.tmux.conf` writer, and a raw `tmux new-session` in Blink.
    - **U6, retirement.**
      - AC: the following are deleted, and listed in `[rust.categories.daemon_meta].replaces` so that
        `check_rust_categories` fails if one returns:
        - the mios-dashboard zipapp and its compile script;
        - `mios-mon.py`, `mios_agent_tui.py` and `mios-ai-terminal`, once their tests are ported;
        - the relay chooser and observer duplicates;
        - the PowerShell monitors and dashboards, and the `mios-native-entry.ps1` routing, changed in
          both repos (Law 15).

        `mios-a2o` adopts the layout engine.
      - +: `check_rust_categories` and `check_bootstrap_sync` pass.
      - -: restoring a retired file fails `check_rust_categories`, naming it.
    - **U7, defects found by the inventory.**
      - `zz-mios-motd` reads `[terminal.startup]`, which vendor lacks (fixed in U1).
      - mios-dashboard has no tracked source (fixed in U6).
      - `[rust.categories.serve]` names `mios-serve`, which no crate builds.
  - **Links:**
    - task_1678 closes with the mios-dashboard retirement.
    - The dispatch table is the desktop subset of the verb-dispatcher task (task_1658).
    - The keybinding fold relates to KEYMAP-01.
    - The mobile-keys and MCP-convergence tasks stay with their agent-plane owners.
    - M7's L1 TUI task builds on U2 and U5.
  - **To confirm with the operator:** U4 routes `ai` to the desktop's AI workspace, as `usr/bin/mios`
    and the design doc already do, and gives Open WebUI its own verb.
- **M9: 500 tracked files, total (ADR-0030).**
  - **Goal:** the operator's cap is "500 disparate files—TOTAL/OVERALL", dependencies included. On 2026-10-09 the tree had 3,654 files after F0.
    - Every domain has a shrink-only `max_files` in `[legibility.file_budget]` and a `target_files`; the targets sum to 405.
    - `max_tracked_files` counts everything and is no longer exempt in `[drift.generated_ceilings]`.
    - No commit adds a file without folding one away in the same commit.
  - **Sources:** `usr/share/doc/mios/adr/0030-source-file-budget.md`; the M9 epic in `tasks.jsonl`.
  - **Every fold lane proves it changed nothing at runtime:** the rendered root-overlay manifest (path + sha256 of every file the build installs) is identical before and after, except for differences the lane names. Each lane moves code in one commit and edits in another, and lowers its domain's `max_files` in the commit that folds.
  - **Phases.** "+" is the positive control (must pass); "-" is the planted defect (must fail).
    - **F0 (landed):** the budget gate, ADR-0030, the epic, and the 9 duplicate `commands/claude` copies deleted.
      - +: `check_legibility_ratchet` passes at 3654/3654.
      - -: one planted file is charged to its domain and to the total.
    - **F1:** projections become build outputs, and exact duplicates and empty markers go. That covers man pages, Quadlets, systemd units, `tmpfiles`/`sysusers`/`sysctl`/`modprobe`, desktop entries, drop-ins and generated indexes. Target: ~3,080.
      - +: the build renders them byte-identically twice.
      - -: a deleted projection that the registry declares fails the render check.
    - **F2:** `etc/profile.d` becomes one file per shell; `kargs.d`/`dracut`/`modprobe` fold; harness shims are rendered at dev setup; the 99 vendored files become one bundle per kind; root misc is cleared. Target: ~2,700.
    - **F3:** tests become one suite per domain. Target: ~2,450.
    - **F4:** docs become manual volumes and one ADR log. Target: ~1,850.
    - **F5:** agent-pipe becomes domain modules, superseding the 800-line module ceiling. Target: ~1,480.
    - **F6:** libexec and bin verbs become multicall MiOS-MODULES. Target: ~980.
    - **F7:** one Rust workspace, and automation phases as data. Target: ~700.
    - **F8:** usr data and tools become SSOT tables and Rust. Target: ≤ 500.
  - **Sequencing with the lanes in flight:** a fold never takes a file that a claimed assignment owns. F3 (tests) waits for the wave-1 and wave-2 lanes to merge, because they add tests.

## Operator rulings, 2026-10-09 (/loop-grill, 20 spec questions)

Each ruling is binding. "+" is the positive test (must pass); "-" is the planted-defect test (must fail).

**Image and distribution**
- **Q1.** Sidecar images are physically baked into the **full** image only (`MIOS_BAKE_BOUND_IMAGES=1`). Cloud and dev images skip the bake. Offline media keep `--bound-images=stored`.
  - AC: the cloud/dev build sets bake=0 from SSOT, and the full build bakes.
  - +: the cloud image has an empty `/usr/lib/containers/storage`.
  - -: plant bake=1 in the cloud profile, and the size gate (Q11) fails.
- **Q2.** Only the tiny LFM2 micro model is baked (~0.7 GB). Every other model is pulled (firstboot / OCI model image). The cloud overlay skips pulls.
  - AC: `/usr/share/mios/llamacpp` ≤ 1 GB.
  - +: an image-content check.
  - -: plant granite in `bake_models`, and the check fails, naming it.
- **Q3.** Channels are `testing` (every boot-tested main push), `stable` (a promoted digest) and immutable dated tags.
  - AC: `[image.streams]` in SSOT, CI tags from it, and `bootc upgrade --tag` documented.
  - +: the CI tag plan includes all three.
  - -: drop a stream from SSOT, and the CI-plan gate fails.
- **Q4.** Key-based cosign, by digest, with one MiOS key plus a backup on both GitHub and Forgejo. `/etc/containers/policy.json` enforces `sigstoreSigned` for `ghcr.io/mios-dev` and carries the base image's ublue entries. Keyless signing stays as an extra signature.
  - +: `podman image trust show` lists the key, and a signed fixture pulls.
  - -: an unsigned fixture is refused.

**Install and security**
- **Q5.** Keep the password `mios`, but ship it expired (`chage -d 0`).
  - +: an image check shows a lastchg of 0 for `[identity].username`.
  - -: plant a non-expired hash, and the check fails.
- **Q6.** Bare-metal install defaults to LUKS with TPM2 plus a generated recovery key. FIDO2 is an optional second token. One SSOT table: fold `[security.luks]` into `[security.disk_encryption]`.
  - +: an install on a loop device yields LUKS2 with a TPM2 token and a recovery keyslot.
  - -: remove the recovery generation, and the install check fails.
- **Q7.** `mios-attest-server` is disabled now (unit conditioned off), and Keylime replaces it later. It may only issue a short-lived headscale pre-auth key and a k3s token.
  - +: the preset/unit check shows it inactive.
  - -: enable it without Keylime, and the gate fails.
- **Q8.** Source-tree drift gates run only in the CI drift-gate job. The bake keeps image-content checks (99-postcheck) and `bootc container lint --fatal-warnings`.
  - +: the Containerfile has no `drift-check`.
  - -: re-add it, and the gate fails.

**Dev, cloud and Windows hosts**
- **Q9.** Build and test **every** MiOS image locally, with tests: OCI, ISO, qcow2, VHDX, raw, WSL, cloud, and MiOS-Xbox. Both dev providers stay supported. This is a phased, filed task series.
  - +: `miosd artifact-build <fmt>` plus `artifact-boot-test` per format.
  - -: a planted boot failure fails that format.
- **Q10.** GPU-PV in a Hyper-V MiOS VM comes from a signed dxgkrnl kmod (MOK, `module.sig_enforce=1`), with the VM GPU partition projected from SSOT.
  - +: `/dev/dxg` exists in the VM.
  - -: an unsigned module is rejected.
- **Q11.** The cloud image must be ≤ 25 GB unpacked, enforced by a CI size gate. Codex Plus (8 GiB) is declared unsupported.
  - +: the measured size is ≤ the SSOT ceiling.
  - -: plant a 1 GB file, and the gate fails at the boundary.
- **Q12.**
  - The Windows host is declarative: `mios-gen` renders one DSC v3 document from SSOT, `Get-MiOS.ps1` installs DSC and runs `dsc config set`, and `dsc config test` is the Windows drift gate.
  - **And MiOS-Xbox** (the MiOS-flavoured Windows) builds to SSOT specs from UUP Dump with DISM, Autounattend and XMLs, using native DISM tooling **on both the Linux and Windows build pipelines**.
  - +: `dsc config test` is clean after set, and the Xbox WIM/ISO builds from UUP Dump on both hosts.
  - -: plant a drifted registry value, and the test fails.

**Configurator and SSOT**
- **Q13.** mios.html → Rust `toml_edit` → mios.toml, through key-level patches as in the research docs (`docs/research/upstream-prior-art-gaps-2026-10.md` P0-6 and `.research/ssot-engine-prior-art-2026-10.md`). The same core runs as wasm for `file://`.
  - +: a no-op save changes 0 keys, and comments survive.
  - -: plant the `#`-strip defect, and the round-trip gate fails, naming `colors.bg`.
- **Q14.** Each key declares the tier it is written to (`x-mios-tier`). System keys go to the host tier over a root-side `SO_PEERCRED` socket. Vendor writes are refused.
  - +: a host-tier key lands in `/etc/mios/mios.d/`.
  - -: a vendor-path write is refused.
- **Q15.** Secrets use systemd-creds (TPM-bound where available), rendered as write-only fields. **Research the best implementation first**, building on `.research/*secrets*`.
  - +: GET never returns a secret value.
  - -: plant a secret into the TOML, and the secret gate fails.
- **Q16.** Disclosure is per key: level `essential|advanced|internal`, plus group and order. Each group has an Admin/Extras collapsible section, plus search, an `@modified` filter and per-key reset. All of it is generated from the SSOT.
  - +: every bound key has a level.
  - -: an unlevelled key fails the configurator gate.

**AI plane, code form and merge**
- **Q17.** Prompt-injection defenses (Rule of Two, quarantine, principal binding) ship in `audit` mode with `provenance_taint = true`, and move to `enforce` after one release.
  - +: the vendor defaults read audit.
  - -: set all of them to off, and the gate fails.
- **Q18.** Agent code runs in a krun microVM where `/dev/kvm` exists, falling back to the hardened crun container. The simulated microVM module is deleted.
  - +: inside the sandbox, the kernel differs from the host.
  - -: force crun with `/dev/kvm` present, and the check flags it.
- **Q19.** MiOS-MODULES: one Rust workspace. Each crate becomes an applet library. A few domain binaries dispatch on argv[0], busybox/bootc style. Amend ADR-0021. Use a shared LTO/strip profile. Privilege separation lives in the systemd units.
  - +: each `L+` shim name runs its applet.
  - -: an unknown argv[0] fails loudly.
- **Q20.** PR #61 merges only when GitHub `drift-gate` **and** `smoke-test` are green, with bootstrap #26 merged first and the operator's final go.

## Non-goals and blast radius
- Do not rewrite history or force-push shared branches. Do not use `git add -A`; other agents share this tree.
- Do not touch other agents' worktrees or branches except to preserve and integrate their work.
- Do not delete legacy files without first proving they are unreferenced. "Dead" files have proven load-bearing before.
- Changes to shared SSOT surfaces must update both `mios.git` and `mios-bootstrap.git` (Law 15).
- Do not weaken a gate to make it pass. Honest red beats lying green.
