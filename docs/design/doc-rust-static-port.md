<!-- AI-hint: doc-rust-static-port.md — The engineering plan that implements ADR-0021: converting the entire script corpus (bash / PowerShell / Python) into static Rust binaries, per role and function, in verified sequences. Covers the target binary set, the per-binary porting protocol, the phase order, the parallel SSOT remediation track, and the standing gates each port must pass.
     AI-related: usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md, usr/share/doc/mios/adr/0011-unified-languages-and-file-patterns.md, usr/share/mios/mios.toml, ROADMAP.md, TASKS.md, automation/55-native-build.sh, tools/sync-generated.sh, doc-unified-pipeline.md -->
# doc-rust-static-port.md — The Script→Rust Static Binary Port (ADR-0021 Implementation Plan)

**Status:** proposed (2026-10-06) · **Implements:** ADR-0021 (Rust static binary consolidation),
ADR-0011 (language-per-domain), ADR-0014 (image-native install) · **Task epic:** WS-LANG
(task_951…task_962, task_3488, task_3489, task_3490, task_3496, task_3505; backlog task_1707…task_1742, task_2267) ·
**Owner:** port lane (see `[rust.categories]`, §7.2)

---

## 1. Purpose and scope

ADR-0021 decided *what* the end state is: a refined, canned, minified code base compiled to Rust
static binaries. This document decides *how* to get there: the target binary set, the per-binary
porting protocol, the phase sequences, and the acceptance gates each phase must pass.

**In scope (the converting corpus, measured 2026-10-06):** 1,590 script files, ~381,600 lines —
370 `.sh`, 147 `.ps1`, and ~1,227 Python (1,073 `.py` + **154 extensionless** python executables
in `usr/libexec/mios/` that extension-only censuses miss) — across `mios.git` and
`mios-bootstrap.git`, excluding the exempt planes below.

**Out of scope (exempt by ADR-0011 / `[legibility]`):**

| Surface | Why exempt |
|---|---|
| `usr/lib/mios/agent-pipe/` + `usr/lib/mios/agents/` (~443 py) | The AI plane. Python stays; `[legibility].python_ai_plane_prefixes` exempts it from the tooling ratchet. Only its *perimeter* converts (relay, MCP runner — Phase 5). |
| `automation/NN-*.sh` boundary steps (~66) | Bash is thin glue by contract (`set -euo pipefail` + `main()`); the *drivers* convert, the glue stays. |
| Web Portal / configurator | Bun/TypeScript domain, never privileged `/usr` tools. |
| `.agents/teamwork/`, `scratch/`, session artifacts (147 files) | Not product code. Never port; candidates for exclusion from ratchet measurement. |
| `tests/` (269 files) | Port a test only when it guards a ported surface (its subject's golden master absorbs it). |

## 2. The contract being implemented

### 2.1 End-state shape (ADR-0021)

- Five-to-eight **function-named** binaries over one shared SSOT crate — `mios-gate`, `mios-gen`,
  `mios-resolver`, `mios-serve`, `mios-probe` — plus the already-existing `mios-install`,
  `mios-build`, `mios-task`. **Not** a multicall `miosd`; per task_3505, `miosd` demotes to a thin
  exec shim and its render verbs move into `mios-gen`.
- `tools/native/` is absorbed into `src/mios-rs` as modules (task_951) with `tmpfiles.d L+` symlink
  shims preserving legacy call sites.
- "Static" means **portable across operating systems**: `x86_64-unknown-linux-musl` (static-PIE)
  and `aarch64-unknown-linux-musl` (static, non-PIE until upstream supports PIE) for the image
  and initramfs; `x86_64-pc-windows-gnu` (`crt-static`) replacing the PowerShell host surface.
  No binary may depend on host glibc or NSS — no `getpwnam` in a ported tool; user/group facts
  come from SSOT or from reading `/etc/passwd` directly.
- **Done when** (ADR-0021, verbatim): "the `[legibility]` script ceilings have fallen to the point
  where every `check-*`, `generate-*`, `render-*` and `resolve` surface is a binary,
  `tools/native/` holds no crate of its own, and Law 14's 'grandfathered' clause has nothing left
  to grandfather."

### 2.2 Laws in play

| Law | Constraint on this port |
|---|---|
| 14 TARGET-LANGUAGES | New code is Rust native tier / Python AI / Bun-TS Portal / bash thin-glue. No new C#/Batch/Go; the two grandfathered `.cs` files (`usr/share/mios/windows/MiOS-Launcher.cs`, `MiosServiceTool.cs`) fold into the installer core in Phase 7. |
| 16 ONE-TEMPLATE-PER-TYPE | Every ported crate is born from the crate scaffold template (Phase 1.1), not hand-rolled. |
| 7 NO-HARDCODE | Ported binaries read `mios.toml` via the shared resolver; lifting a hardcoded literal into SSOT happens *during* the port, never after. |
| 8 SSOT-PROJECTION | Generators become `mios-gen` projections; `[unit_projection]` / `[ssot_consumers]` / `[ssot_tables]` registers stay authoritative. |
| 12 BAKE-NOT-FETCH | Vendored deps (`cargo vendor`, committed `Cargo.lock`, `[rust.dependencies]` allowlist); compiled in the Containerfile builder stage; no network at bake; no Rust toolchain in the final image. |
| 13 NATIVE-DROPINS | Units switch `ExecStart` from scripts to binaries via drop-ins, never by editing generated unit bodies. |

### 2.3 The ratchet (shrinks monotonically as ports land)

`[legibility]` in `usr/share/mios/mios.toml` (~line 12611): `max_shell_lines = 54941`,
`max_ps_lines = 27878`, `max_tooling_python_lines = 81188`. Ceilings are regenerated from
measurement by `mios-size-ceiling` after each deletion; **they must only fall**. The
`ratchet-direction` gate enforces this.

## 3. Measured current state (2026-10-06)

### 3.1 The corpus by role

| Role | Volume | Port destination |
|---|---|---|
| CI/gates (`tools/`, `tests/`) | 368 files; `tools/drift-checks.py` 4,791 ln; `automation/98-drift-checks.sh` 4,906 ln | `mios-gate` (Phase 2) |
| Generators/projectors (`tools/generate-*.py`, `render-*.py`, ~20) | part of tools' 69 py | `mios-gen` (Phase 3) |
| System runtime (`usr/libexec/mios/`, 30+ domain subdirs: sec ux ai hw node deploy virt net storage kernel db win git) | ~419 executables incl. the 154 extensionless | daemons → `mios-serve` (Phase 5); one-shots/timers → CLI verbs (Phase 6) |
| Build/pipeline (`automation/` 01–99, `globals.{sh,ps1}` 3.1k ln each) | ~190 files | drivers → `mios-build`/`miosd` verbs (Phase 4/8); glue stays |
| Windows host surface (`usr/share/mios/windows/` ~20 ps1; root `build-mios.ps1` 8,867 ln, `Get-MiOS.ps1` 6,027 ln) | 25+ files | windows-gnu binaries (Phase 4/7) |
| Installer/bootstrap (both repos; `field/autounattend/` 29 files) | ~92 files | `mios-install` (Phase 7) |
| UX/desktop (`ux/` 18 py, profile.d, TUI, `mios-mon.py`) | ~45 files | CLI verbs (Phase 6) |
| Dual-platform `.sh`/`.ps1` mirror pairs | **16 pairs** (8 per repo) | one binary per pair (Phase 4) |

### 3.2 The Rust surface that already exists

`tools/native/` — 27 crates (workspace manifest generated by
`tools/generate-cargo-manifests.py`; version from `[meta].mios_version`). Load-bearing ports
already proven: `mios-resolver` (layered SSOT reader, `--emit shell|powershell|json|install-env`),
`mios-render-quadlets` (replaced the stage-34 envsubst pair), `mios-unit-gen` (systemd/Quadlet
generator, REQUIRED by `tools/sync-generated.sh`), `mios-agent-relay` (2,277-ln service),
`mios-bake-plan`, `mios-hardcode-lint`, `mios-ssot-lint`, `mios-version-check`, `mios-task`,
`mios-toml-get`, `mios-edge-status`, `mios-aiplane-lint`, `mios-template-{compile,conform}`,
`mios-browser`, `mios-install`, `mios-launch` + `mios-wallpaperd` (Windows-only), `mios-node`
+ libs (`mios-service-core`, `mios-ssot-walk`), plus `xtask`.

`src/mios-rs/` — 6 members: `mios-gate` (18 checks: artifact, build-tool-dispatch,
credential-literals, doc-refs-resolve, drift-stubs, image-equivalence, image-freshness,
law-enforcers, no-inert-ssot-tables, phase-registry, profile-integrity, projection-coverage,
protected-refs, ratchet-direction, render-coverage, signature-policy, static-linkage,
version-literals-ssot), `miosd` (meta-CLI + 33-verb `Cli` dispatcher + scaffold + daemon),
`mios-build` (phase registry + `[build.native]` validator + `verify_static_elf`),
`mios-config` (typed resolver), `mios-node`, `mios-probe`.

### 3.3 The pipeline as it works today

`[build.native]` (mios.toml ~line 1881) → `automation/55-native-build.sh`: build host `miosd` →
`miosd native-build-settings --arch` → `rustup target add` → `miosd native-targets --platform
linux` (install plan) → `cargo build --release --locked --target <musl>` →
`miosd native-artifact-check` (static-ELF proof) → staged `install` (honoring
`MIOS_NATIVE_INSTALL_ROOT`). The Containerfile `rust-builder` stage (`rust:slim`,
`cargo fetch --locked`, `/out`) is the only place binaries enter the image. CI
(`.github/workflows/mios-ci.yml`) release-builds `mios-bake-plan` + `mios-gate`/`miosd` and
debug-builds 11 others; runs `tools/sync-generated.sh` (23 steps, native-first with Python
fallback), `cargo fmt --check`, `clippy -D warnings`, tests.

### 3.4 Deficits this plan must close (measured on the live hosts)

1. **The dev runtime executes zero binaries.** In `podman-MiOS-DEV`, `/usr/bin/mios*` are compat
   symlinks into `/usr/libexec/mios` → the repo tree via `/mnt/c`, which contains only scripts.
   `/usr/bin/mios-agent-relay` and `/usr/bin/mios-ssot-lint` dangle; `/usr/bin/mios` resolves to
   the Python entry; `miosd`/`mios-gate`/`mios-probe`/`mios-resolver` are absent entirely.
2. **0 of 156 repo MiOS systemd units exist in the distro** — the service plane (agent-pipe,
   hermes, MCP runner, 22 timers) cannot start; endpoints 8700/11450 are dark; relay state absent.
3. **The Windows cross lane is declared but unimplemented**: `[build.native.windows]` exists;
   `55-native-build.sh` plans `--platform linux` only. Existing `.exe` artifacts were host-built.
4. **No crate-level scaffold template**: `templates/rust` emits a single `.rs` file; nothing
   scaffolds `Cargo.toml` + `src/main.rs` + workspace registration (Law 16 gap).
5. **`[rust.categories]` does not exist** (task_3488): the port plan is tracked only in TASKS.md,
   not in SSOT.
6. No `.cargo/config.toml`; cross-compile flags are ad hoc `RUSTFLAGS` in `55-native-build.sh`.
7. Host-built release ELFs in `tools/native/target/release` are glibc-dynamic and (by design)
   outside the `static-linkage` gate's scan set — dev artifacts are not representative.

## 4. Target architecture

### 4.1 The binary set

| Binary | Category | Installs to | Absorbs |
|---|---|---|---|
| `mios-resolver` (+ lib crate) | cli | `/usr/bin` | `mios_toml.py` (demoted to shim), `userenv.sh` emit path, `globals.*` generation, `/etc/mios/install.env` |
| `mios-gate` | cli | `/usr/bin` | every `check-*`: `tools/drift-checks.py`, `97-ssot-lint.sh`, `98-drift-checks.sh` families |
| `mios-gen` | cli | `/usr/bin` | every `generate-*` / `render-*` projector; `miosd` render verbs (task_3496); `mios-gen apply` re-projects after save and at boot (task_3489) |
| `mios-serve` | services | `/usr/libexec/mios` | non-AI-plane daemons: MCP runner, self-heal, policy-arbiter, account-sync, log-streamer, tmpfs-spill, cron-director, killswitch |
| `mios-probe` | cli | `/usr/bin` | host-readiness probing (exists) |
| `mios-install` | cli | `/usr/bin` | `deploy/baremetal_install.py`, `Get-MiOS.ps1` core, the two grandfathered `.cs` tools |
| `mios-build` | cli | `/usr/bin` | build-driver logic out of `build-mios.ps1` / root PS monoliths |
| `mios-task` | cli | `/usr/bin` | exists (tasks.jsonl, ADR-0028) |
| `miosd` | — | `/usr/bin` | thin exec shim over the above (task_3505); daemon duties move to `mios-serve` |
| `mios-node`, `mios-launch`, `mios-wallpaperd` | daemons/apps | `/usr/libexec/mios` (+expose) | edge node; Windows terminal launcher; Windows wallpaper service |

Shared crates (no bin): `mios-resolver` lib (the ONE SSOT reader), `mios-ssot-walk`,
`mios-service-core`, `mios-config`.

### 4.2 Cross-compile matrix (from `[build.native]`; codify in `.cargo/config.toml` — Phase 1.5)

| Target | Linker | Rustflags | Notes |
|---|---|---|---|
| `x86_64-unknown-linux-musl` | `rust-lld` | `-C target-feature=+crt-static`, static-PIE | image + initramfs |
| `aarch64-unknown-linux-musl` | `rust-lld` | `+crt-static`, non-PIE | until musl+rustc aarch64 static-PIE stabilizes |
| `x86_64-pc-windows-gnu` | `x86_64-w64-mingw32-gcc` | `+crt-static` | host-surface replacement; GUI binaries use `windows_subsystem="windows"` (see `mios-launch`) |

### 4.3 Binary I/O contract (every ported binary obeys)

- Exit codes: `0` clean / `1` violations found / `2` could-not-run. Preserved byte-for-byte from
  the script being replaced.
- CLI: `--root DIR` (repo/image root), regenerate-or-verify `--check` convention,
  `--format json|text` where the JSON is OpenAI-format structured output (the project-wide
  schema law; see `mios-gate --format json`).
- No panics: `clippy::unwrap_used` and `clippy::panic` denied; `#![forbid(unsafe_code)]`.
- Headers: `// AI-hint:` / `// AI-related:` on every file; crate registered in
  `[build.native.categories]` and, after Phase 1.2, `[rust.categories]`.
- No NSS/glibc: user facts from SSOT or direct `/etc/passwd` reads; no `getpwnam`, no `dlopen`.

### 4.4 Install plan and legacy call sites

Deployment categories (`cli` → `/usr/bin` + compat symlinks into `/usr/libexec/mios`;
`services`/`daemons` → `/usr/libexec/mios`, `expose_bin` where needed) are planned by
`miosd native-targets` from SSOT — never hand-installed. Legacy call sites keep working through
`tmpfiles.d L+` symlink shims and the compat-dir mechanism until Phase 8 deletes the last caller.

## 5. The per-binary porting protocol

This is the mandatory sequence for **every** port, no exceptions. It exists because the last
time a port skipped it, the twin-drift incident (doc-unified-pipeline.md §1: the
`34-render-quadlets` renumber left the hand-ported `mios-ssot-lint` opening `15-…` and
red-baked the pipeline at 1m18s) burned a full CI cycle.

1. **Golden master FIRST** (task_1707). Before writing any Rust: capture the script's real-tree
   outputs into `tests/golden/<surface>/`, and **plant negative controls** — malformed inputs
   that must fail *for the planted reason*. Two-sided, per the dev-loop definition of done:
   - *Positive control:* valid input → expected stdout/stderr/exit 0, byte-identical.
   - *Negative control:* each failure mode → the script's exact diagnostic and non-zero exit.
   Run the harness via trycmd-style fixtures so it executes against the script and the binary
   unchanged.
2. **Scaffold from the template** (Phase 1.1): `mios new rust-crate <name>` → `Cargo.toml` +
   `src/main.rs` + workspace registration via `tools/generate-cargo-manifests.py`. Never
   hand-roll a crate.
3. **Implement** to the I/O contract (§4.3), reading all values through `mios-resolver`.
   If the script contained a hardcoded literal (port, color, path), lift it into `mios.toml`
   **in this port**, adding the key to `[ports]`/`[colors]`/etc. and a `[ssot_consumers]`
   entry — do not carry the literal into Rust.
4. **Register**: add the binary to `[build.native.categories]` and `[rust.categories]`
   (owner + `replaces = [...]` script paths).
5. **Parity run**: native vs script on the real tree, diff must be empty (including trailing
   newlines); all planted negative controls fail for the planted reason with identical
   diagnostics.
6. **Wire consumption**: `tools/sync-generated.sh` step flips native-first (Python fallback
   deleted in the same commit); CI gains/updates the `cargo build -p` line; systemd units
   switch `ExecStart` via NATIVE-DROPINS drop-in, not by editing generated bodies.
7. **Standing gates green** (§8): the five `mios-gate` policy checks, `tools/ci-suites.py
   --check`, `tools/sync-bootstrap.py --apply && --check`, `cargo fmt --check`,
   `clippy -D warnings`, workspace tests, `static-linkage` on the musl artifact
   (`miosd native-artifact-check`).
8. **Delete the script(s) in the SAME commit** that proves parity. `git revert` of that commit
   is the defined rollback. Regenerate ceilings via `mios-size-ceiling` and commit the
   fallen `[legibility]` values.
9. **Record**: commit message cites the task ID, the deleted script paths, and before/after
   line counts.

### 5.1 Worked example — porting `tools/generate-cosign-policy.py` (task_1720)

1. Golden master: `tests/golden/README.md` gets a `cosign-policy` console block for the real tree
   (positive: projected policy matches `usr/share/mios/policy/…`; negative: missing
   `[supply_chain.cosign]` key → the script's exact `FATAL` diagnostic, exit 2).
2. `mios new rust-crate mios-cosign-policy` — or fold into `mios-gen` as a subcommand once
   Phase 3 lands its skeleton (preferred after Phase 3: new projectors are `mios-gen` verbs,
   not new crates).
3. Implement reading `[supply_chain.cosign]` via the resolver lib; `--check` regenerates or
   verifies; `--format json` emits findings.
4. Register under `[rust.categories]` with `replaces = ["tools/generate-cosign-policy.py"]`.
5. Parity: byte-diff golden outputs script vs binary on the real tree; negatives fail verbatim.
6. `tools/sync-generated.sh` step flips to `mios-gen cosign-policy` native-only; CI line added.
7. Gates green; delete the `.py`; ceilings fall; commit cites task_1720 + counts.

## 6. The phase plan

Dependency rule: **Phase 1 gates every mass-port phase (2–7)**. Phase 0 is orthogonal and can
run first or in parallel. Within a phase, items are sequenced; the sequence column is the order.

### Phase 0 — Live runtime repair (orthogonal; unblocks dev-loop verification on real hosts)

| # | Action | Acceptance |
|---|---|---|
| 0.1 | Stage musl binaries into the dev distro: run `automation/55-native-build.sh` with `MIOS_NATIVE_INSTALL_ROOT=/` semantics (or the repo-root non-root mode) inside `podman-MiOS-DEV`; `miosd native-targets` plans it | `/usr/bin/miosd`, `mios-gate`, `mios-probe`, `mios-resolver` present as **static ELFs**; the dangling `mios-agent-relay`/`mios-ssot-lint` links resolve |
| 0.2 | Install/link the 156 repo units into the distro unit search path; `daemon-reload` *(confirm-before action per AGENTS.md)* | `systemctl status mios-agent-pipe` finds the unit |
| 0.3 | Start `mios-agent-pipe.socket`/`.service` + relay; heal host→WSL forwarding if needed (`usr/libexec/mios/Heal-MiOSLocalhostForwarding.ps1`) | `curl 127.0.0.1:8700/v1/models` lists models; `/home/user/.local/state/mios/agent-relay/state.json` exists |
| 0.4 | Repair the Windows agent-lane shims under `C:\ProgramData\MiOS\agents\npm\` (all currently `WinError 2`) | dev-loop probe: lanes healthy |

### Phase 1 — Enablers (M; blocks Phases 2–7)

| # | Action | Task | DoD |
|---|---|---|---|
| 1.1 | Crate scaffold template: extend `[templates.rust]` → a `rust-crate` type scaffolding `Cargo.toml` + `src/main.rs` + workspace regen; golden-test it like every template | Law 16 | `mios new rust-crate foo` produces a compiling, gate-passing crate skeleton |
| 1.2 | `[rust.categories]` registry + ownership gate: category → owner → `replaces` script list; a `mios-gate` check flags any script both alive and listed `replaced` (or listed with no owner) | task_3488 | registry in `mios.toml`; gate green on the real tree with the current 33 binaries registered |
| 1.3 | Golden-master trycmd harness: fixture layout under `tests/golden/`, runner wired into CI before any port | task_1707 | harness runs script-or-binary from one fixture set; CI leg exists |
| 1.4 | Resolver cutover: `mios-resolver` becomes the ONE reader; `mios_toml.py` fully demoted to shim; `globals.{sh,ps1}` generated by `render-globals.py` **from `mios-resolver --emit`**; cutover toggles all-true then removed | task_952, task_959 | zero first-class SSOT readers besides the Rust resolver |
| 1.5 | Check in `.cargo/config.toml` codifying the §4.2 matrix; `55-native-build.sh` consumes it instead of ad hoc `RUSTFLAGS` | — | config drives both workspaces; musl + windows targets build from clean |

### Phase 2 — `mios-gate` strangler (XL; task_953)

Port drift checks check-by-check into `mios-gate` (18 already there). **Order matters:**

1. **Equivalence checks first** — every `check_*_equivalence` guarding a script/Rust twin dies
   the moment its twin's parity lands (the twin-drift class of check exists only while both
   sides live). Deleting these early removes the most brittle checks.
2. **Mechanical scanners next** — literal/port/numbering scans (`hardcode`, `ports`,
   `numbering`, `names`) port cheaply onto `mios-ssot-walk` + the resolver.
3. **Semantic/template checks last** — `template-conformance`, `law-enforcers`,
   `aiplane-lint` (already partially native via `mios-aiplane-lint`), which need the most
   fixture depth.

Each check: protocol §5 → flip `tools/drift-checks.py` dispatch to the `mios-gate` subcommand →
delete the Python check body. `automation/98-drift-checks.sh` shrinks to the bash-thin-glue
dispatcher; its final state is deletion when the last check moves (Phase 8).
DoD: `python tools/ci-suites.py --check` census updated; `mios-gate --format json` is the sole
gate entry point; `tools/drift-checks.py` deleted or reduced to a shim.

### Phase 3 — `mios-gen` batch (L; task_954, task_3489)

Stand up `mios-gen` as a verb dispatcher (absorbing `miosd` render verbs per task_3496), then port
projectors in risk order:

1. The four small projectors (task_1729): `generate-names-registry` (Rust exists; delete the
   `.py` fallback — task_1713), `generate-cosign-policy.py` (task_1720, §5.1 worked example),
   `generate-uki-cmdline.py` (task_1721), `generate-egress-firewall.py` (task_1722 — note the
   Rust generator already exists from task_1246; finish parity and delete).
2. `generate-ai-manifest.py` (task_1742) — also deletes the manifest-regeneration Python that
   `tools/manifest.json` / `automation/manifest.json` depend on (they are generated indexes,
   not porting registries).
3. Renderers: `render-manpages.py`, `render-globals.py` (post-1.4 it emits from the resolver),
   `standardize-docs.py`, `roadmap-index.py`, `generate-adr-index.py`, `sync-wiki.py`.
4. Gate/pipeline index generators (task_1728): `generate-gate-index.py` and kin — the "fourth
   numbering namespace" from doc-unified-pipeline; folding them into `mios-gen` completes the
   one-number contract.
5. **`generate-pod-quadlets.py` LAST** (task_1723, XL): highest blast radius (116 `[containers]`
   projections); requires the deepest golden-master set.

Then task_3489: `mios-gen apply` re-projects after configurator save and at boot (Firstboot path).
DoD: every `generate-*`/`render-*` in `tools/` is a `mios-gen` verb; `tools/sync-generated.sh`
steps are native-only.

### Phase 4 — Windows lane + dual-pair collapse (L; task_956, task_2267)

1. **Implement the windows-gnu cross lane**: `miosd native-targets --platform windows` returns
   the install plan from `[build.native.windows]`; `55-native-build.sh` (or its successor
   `mios-build` verb) executes it inside the MiOS-DEV builder; CI gains a windows artifact leg.
   DoD: `mios-launch.exe`/`mios-wallpaperd.exe`/a CLI `.exe` built cross, not host-built.
2. **Collapse the 16 `.sh`/`.ps1` mirror pairs**, in dependency order (each protocol §5 deletes
   BOTH scripts in one commit):
   `automation/lib/globals.*` (dies in 1.4) → `installation/mios-common.*` →
   `installation/mios-install.*` → `bootstrap.*`/`config/bootstrap/bootstrap.*` →
   `seed-merge.*` → `install.*` (bootstrap repo) → `tools/generate-k3s-manifests.*` →
   `build-mios.*` (driver logic → `mios-build` verb; wrapper glue retires in Phase 7) →
   `field/MiOS-Field.*`.
   DoD per pair: one binary, zero mirrors, `[legibility]` falls by the pair's combined lines.
3. **Port the Windows host surface** (`usr/share/mios/windows/*.ps1`, ~20): keepalive, port
   proxy, shortcut updater, AI node launcher (fixing the retired-port violation, §7), wallpaper
   (already `mios-wallpaperd`), MCP setup. task_2267's ratio inversion (Rust > PowerShell "for
   anything that is a program") lands here.
   DoD: `max_ps_lines` ceiling dominated by genuine shell-one-liners only.

### Phase 5 — `mios-serve` daemon tier (L; task_955)

Port non-AI-plane daemons, ordered by dependency value and risk:

1. `mcp-server-runner` (backs `mios-mcp.service`) — highest coordination value; unblocks
   native MiOS-MCP end to end.
2. `mios-daemon`, `ai/self_heal.py --daemon`, `mios-policy-arbiter`, `mios-account-sync`.
3. `log/mios-log-streamer`, `mem/mios-tmpfs-spill`, `mios-cron-director`,
   `net/vpn_killswitch.py`.
4. Timer/oneshot targets flip to binaries in the same pass (`db/mios-backup-pgvector.py`,
   `db/mios-pg-replica.py`, `storage/container_gc.py`, `storage/mios-backup-remote`,
   `net/mdns_mesh.py`, `sec/rotate-quadlet-secrets.py`, `telemetry/log_archiver.py`,
   `db/mios-pgvector-optimize.py`, `vfio/setup-looking-glass.py --verify`).
5. Hardware-adjacent daemons last, with negative controls on absent hardware:
   `hw/powerd.py`, `audio/wakeword.py`, `ux/wallpaperd.py` (Linux twin of the Windows daemon).

The AI plane stays Python: `mios-agent-pipe.service` → `usr/lib/mios/agent-pipe/server.py`,
`hermes-worker.service`, `mios-opencode-gateway.service`, `mios-finetune-serve`,
`mios-embed-backfill` remain on their interpreters; `mios-serve` provides socket activation,
supervision and the relay perimeter (already `mios-agent-relay`) around them.
DoD: `systemd-analyze verify` clean on flipped units; NATIVE-DROPINS respected; every flipped
service's positive control (unit starts, port listens) and negative control (config removed →
fails for the intended reason) pass.

### Phase 6 — `usr/libexec/mios/` domain sweep (XL; the 154 extensionless + ~265 counted)

Sweep the 30+ domain subdirs in risk order, applying §5 per tool (one-shots become `miosd`/verb
CLIs or small binaries; classify first via the `[rust.categories]` registry):

`db/` (small; fixes the 5432 SSOT key §7) → `git/` (5) → `storage/` (9) → `net/` (10) →
`win/` (5) → `deploy/` (11; absorbed into `mios-install`) → `sec/` (30) → `kernel/` (6) →
`virt/` (10) → `node/` (16) → `hw/` (17) → `ai/` non-plane (18) → `ux/` (18 — port
`mios_agent_tui.py` colors from `[colors]` during its port) → root extensions
(`capability-audit.sh` 2,453 ln, `mios-mon.py` TUI, launcher helpers).

DoD: extensionless-python census in `[rust.categories]` reaches zero unowned entries;
`usr/libexec/mios/` contains binaries + the exempt AI plane + bash glue only.

### Phase 7 — Installer core + C# retirement (L)

`mios-install` absorbs `deploy/baremetal_install.py` (already partly done per ADR-0014), then
the `Get-MiOS.ps1` core (6,027 ln) and `field/autounattend/` generators (29 files — the
extensionless `unattend_gen.py`/`driver_slipstream.py` convert; the 26 Pester-era `.ps1`
retire behind them). Fold `MiOS-Launcher.cs` + `MiosServiceTool.cs` into `mios-install`/
`mios-launch` and drop `[laws.target_languages].grandfathered_cs` — the Law 14 grandfather
clause reaches zero. Decompose `build-mios.ps1` (8,867 ln) into `mios-build` verbs; the
PowerShell wrapper reduces to a shim that execs the binary and exits.
DoD: the grandfathered list is empty; `build-mios.ps1` < ~100 lines; Law 14's done-when clause
is satisfiable.

### Phase 8 — Consolidation (M; task_951, task_3505, task_3481)

Absorb `tools/native/*` crates into `src/mios-rs` as modules; keep `tmpfiles.d L+` shims;
`miosd` becomes the thin exec shim (task_3505); `mios-gen apply` owns boot-time re-projection
(task_3489); one config server (task_3490). Delete the compat symlink layer when no caller remains
(task_3481 audits dead PowerShell). Final audit against §2.1's done-when: ceilings at floor,
`tools/native/` empty of crates, every `check/generate/render/resolve` surface a binary.

## 7. Parallel SSOT remediation track (runs alongside, not sequenced)

These are Law 7 debts found during the 2026-10-06 census; fix them **in the port that touches
the file** or immediately, never by carrying literals into Rust:

1. **Retired-port violations:** `usr/share/mios/windows/mios-ai-node.ps1` hardcodes **11450**
   (×12; retired; successor key `llm_light=8500`) — fix in its Phase 4 port.
   `usr/share/mios/windows/mios-tailscale-serve.ps1` hardcodes **8640** (×6) while correctly
   resolving `agent_pipe=8700` at line 70 — fix now.
2. **Missing port keys:** container-internal `5432` (pgvector; hardcoded in
   `usr/libexec/mios/db/mios-db-doctor.py`, `mios-backup-pgvector.py`,
   `mios-pgvector-optimize.py`) and `8080` (searxng internal;
   `usr/lib/mios/gateway-agent/tool_registry.py:60`) need `[ports]` keys — the `guacd=4822`
   precedent. Add keys + `[ssot_consumers]` entries in Phase 6's `db/` pass.
3. **Palette duplication:** `usr/lib/mios/mios_agent_tui.py` carries 22 hex literals with zero
   SSOT reads — its Phase 6 port reads `[colors]` via the resolver.
   `ux/theme_sync.py`'s 65 fallback literals reduce to resolver reads.
4. **Configurator coverage:** expose `[rust.categories]` (and the new port keys) in
   `usr/share/mios/configurator/mios.html`; the emitter already preserves unknown sections, so
   this is UI-only.
5. **`[settings]` does not exist** (recorded to prevent repeated searches): operator knobs are
   intentionally distributed across `[terminal]`, `[theme]`, `[ai]`, etc. Do not invent a
   `[settings]` table; new knobs go to their domain table.

## 8. Standing verification gates (run per port, per phase merge)

```
src/mios-rs/target/release/mios-gate phase-registry      --root C:\MiOS
src/mios-rs/target/release/mios-gate ratchet-direction   --root C:\MiOS
src/mios-rs/target/release/mios-gate credential-literals --root C:\MiOS
src/mios-rs/target/release/mios-gate version-literals-ssot --root C:\MiOS
src/mios-rs/target/release/mios-gate signature-policy    --root C:\MiOS
miosd native-artifact-check (static-linkage, per musl artifact)
python tools/ci-suites.py --check
python tools/sync-bootstrap.py --apply && python tools/sync-bootstrap.py --check
tools/sync-generated.sh (must be native-clean for flipped steps)
cargo fmt --check && cargo clippy -D warnings && cargo test (both workspaces)
```

Plus the per-port two-sided controls (§5.1 fixtures) and, on the dev distro, the Phase 0
acceptance endpoints (8700 models list, relay `state.json`). Phase merges additionally run the
negative-control battery: every planted failure must fail for the planted reason — a gate that
cannot fail is a phantom and is removed, not kept.

## 9. Risks and pitfalls (each with its mitigation)

| Risk | Mitigation |
|---|---|
| Twin drift during strangler (the `mios-ssot-lint` incident) | Golden master before any port; equivalence checks deleted only with their twin; delete-in-same-commit rule |
| musl NSS/DNS gaps (no `getpwnam`, resolver quirks) | §4.3 contract: SSOT + direct `/etc/passwd` reads; no dlopen; `native-artifact-check` per artifact |
| Parity broken by trailing-newline/locale noise | Byte-identical diff including final newline; fixtures pin locale (`C`) and TZ |
| Windows GUI binaries can't print diagnostics | `windows_subsystem="windows"` only where required (`mios-launch` pattern); CLIs stay console-subsystem |
| Vendoring bloat / supply chain | `cargo vendor` + committed `Cargo.lock` + `[rust.dependencies]` allowlist; Law 12 forbids network at bake; builder stage keeps toolchain out of the image |
| Ratchet regression under merge pressure | `ratchet-direction` gate is release-blocking; ceilings regenerate only via `mios-size-ceiling` |
| Breaking the 156-unit service plane while flipping ExecStarts | NATIVE-DROPINS drop-ins (never edit generated bodies); `systemd-analyze verify` per flip; Phase 0 acceptance endpoints re-run per Phase 5 batch |
| Architectural invariant erosion (the Five: `/var` persists; UKI≠MOK; venus≠CUDA; vGPU needs host PF; Blade owns hardware) | Ported tools carry no new hardware/boot assumptions; anything touching boot, GPU or blades inherits the invariant checks already in the drift families |
| Harness neutrality (no vendor agent/product names) | Law 5: binaries, `--format json` schemas, and commit messages reference only OpenAI-compatible surfaces |

## 10. Task mapping

| Phase | Tasks consumed | Tasks produced |
|---|---|---|
| 0 | (handshake findings, 2026-10-06) | live-repair tasks (units/binary staging/agent shims) |
| 1 | task_952, task_959, task_1707, task_3488 | crate-template task; `.cargo/config.toml` task |
| 2 | task_953, task_962 (P0, in_progress) | per-check port tasks |
| 3 | task_954, task_3489, task_3496, task_1713/1080/1081/1082/1083/1088/1089/1102 | — |
| 4 | task_956, task_3481 (dead-PS audit), task_2267 | pair-collapse checklist tasks |
| 5 | task_955 | per-daemon flip tasks |
| 6 | (census-driven) | `[rust.categories]` entries, one per tool |
| 7 | ADR-0014 remainder, C# fold-in | grandfathered_cs removal |
| 8 | task_951, task_3490, task_3505 | done-when audit |

## 11. References

- ADR-0021 — Rust static binary consolidation (the decision this implements):
  `usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md`
- ADR-0011 — unified languages and file patterns: `usr/share/doc/mios/adr/0011-unified-languages-and-file-patterns.md`
- ADR-0014 — image-native install: `usr/share/doc/mios/adr/`
- Thesis (four pillars, designed-vs-observed scope): `usr/share/doc/mios/manual/thesis.md`
- Laws registry: `usr/share/mios/mios.toml` `[laws]` (~line 2289); rendered:
  `usr/share/doc/mios/reference/laws.md`
- Ratchet + language policy: `usr/share/mios/mios.toml` `[legibility]` (~12611), `[rust]` (~13206),
  `[build.native]` (~1881)
- Build lane: `automation/55-native-build.sh`, `Containerfile` (rust-builder stage),
  `tools/sync-generated.sh`, `.github/workflows/mios-ci.yml`
- Twin-drift exemplar: `docs/design/doc-unified-pipeline.md` §1
- Roadmap epic: `ROADMAP.md` WS-LANG (~line 334); registry: `TASKS.md` (WS-LANG block ~2120)
- Field census (2026-10-06, basis of §3): live probes of `podman-MiOS-DEV`, `C:\ProgramData\MiOS`,
  and both repo trees
