<!-- AI-hint: MiOS Finalization Master Plan. Synthesized from six domain task-lists (runtime wiring, agent-pipe/config resolver, templates/conformance, registry/CI, verbosity/docs/coordination, MiOS-Metal design, deploy plane). Deduped and reconciled; every row traces to a so
     AI-related: usr/share/mios/mios.toml, docs/adr/, AGY-TASKS.md -->
# MiOS Finalization Master Plan

Synthesized from six domain task-lists (runtime wiring, agent-pipe/config resolver, templates/conformance, registry/CI, verbosity/docs/coordination, MiOS-Metal design, deploy plane). Deduped and reconciled; every row traces to a source finding. Owners: **agy** (C:\MiOS), **claude-bootstrap** (C:\mios-bootstrap), **infra** (human/registry/hardware).

---

## 1. Definition of Done

"Finalized MiOS" means **all** of the following hold simultaneously:

- **Build + drift GREEN, end-to-end.** `mios build` completes and `automation/98-drift-checks.sh` (`just drift-gate`) exits 0 on Linux/CI with python3 present — including the new checks (kargs projection, greenboot, quadlet enable-parity, image-name twin-parity, fluff-token lint, AppData-path lint, deploy-plane, impossible/EOL-regression) — and the template-conformance gate (check 46) actually enforces (no SOFT-skip).
- **CI publishers are internally consistent and bit-identical.** `mios-ci.yml` `PUBLISH` value agrees with its capacity comment; GHCR push/sign are event-gated (never on PRs); GitHub and Forgejo both fail-loud on empty `MIOS_IMAGE_NAME`; `ghcr.io/mios-dev/mios` is confirmed **public**.
- **Agent-pipe config resolution is correct.** Every reader resolves via the shared resolver (no raw `MIOS_TOML` opens); user-layer writes persist a delta (vendor defaults never frozen); `db_authoritative` falls back per-section (never an empty agent registry); `to_toml` round-trips datetimes; resolution is memoized; offline tests cover the DB branch; an anti-regression gate prevents re-drift.
- **Image-name resolution never leaks `localhost/mios` into a pull.** `globals.{sh,ps1}` defer to `mios.toml [image]` (task_1121 clobber fixed, both twins), a twin-parity check guards it, and Day-2 switch targets derive from `MIOS_IMAGE_REF`.
- **A deployable MiOS-Cat that installs MiOS (not plain Fedora), offline.** Real validated Fedora Server DVD staged; kickstart runs the FHS overlay unattended (Total-Root-Merge auto-proceeds) from USB-staged sources with the NIC off; a from-scratch VM boots to a MiOS server; the immutable `bootc install --transport oci` leg exists from a non-empty USB-staged seed.
- **The 12 packaged-but-unwired capabilities are resolved.** Each is either SSOT-wired + drift-gated (kargs, TPM/LUKS, chrony, NUT, greenboot gate, quadlet parity) or explicitly removed with a recorded decision (glusterfs, virt-v2v) or scoped-and-annotated (mdevctl: legit mediated-device only, vGPU out of scope).
- **Docs landed + discoverable.** Four concept docs (`container-os-runtime`, `foss-upstream-map`, `image-resolution`, `mios-metal-architecture`) authored, AI-hint-headed, template-conformant, cross-reffed from ROADMAP/architecture; agent-facing docs carry the four architectural corrections (/var persists, MOK≠UKI, venus≠CUDA, multi-vendor GPU via CDI whole-device).
- **MiOS-Metal decisions recorded.** Base posture (minimal-surface full type-1 hypervisor), exclusive GPU arbitration (`[mini.gpu]`), console-less LUKS recovery, Tang-location, nftables authority, and the two "impossible/EOL" writeups (mdev vGPU, gluster) are decided, projected from SSOT, and regression-gated.

---

## 2. Critical Path (ordered spine of blockers)

The P0/P1 spine to a deployable, gap-closed MiOS. Everything below the fold (P2/P3) can proceed once the tree stays green.

1. **[P0 · build/drift] Reconcile the resolver env schism** — audit `automation/98-drift-checks.sh` + `57-mios-sys-build.sh` to set `MIOS_VENDOR_TOML` alongside legacy `MIOS_TOML`. **[CLEARED: task_2446; `MIOS_VENDOR_TOML` resolution verified in `usr/lib/mios/mios_toml.py` and `userenv.sh`]**
2. **[P0 · CI] Reconcile `mios-ci.yml` PUBLISH** vs its own capacity-gate comment. **[CLEARED: task_2447; `.github/workflows/mios-ci.yml` updated and verified clean]**
3. **[P0 · deploy] Fix the Fedora ISO 404 stub + validity gate** (G1) and **make the kickstart Total-Root-Merge run unattended** (G2). **[CLEARED: task_2457; deploy plane documentation updated with status warnings; ISO URL & integrity checks added in `installation/mios-install.ps1`]**
4. **[P1 · agent-pipe correctness]** Migrate raw-`MIOS_TOML` consumers + portal readers, fix user-layer delta write, `db_authoritative` per-section fallback, `to_toml` datetime, memoize resolution. **[CLEARED: `check_raw_toml_readers` (check 13 `check_cli_sql_safety`/`check_raw_toml_readers`) in `automation/98-drift-checks.sh` enforces 0 raw opens; unit tests pass]**
5. **[P1 · registry correctness]** task_1121 globals non-clobber, both twins (D3, D4) + twin-parity check (D5); event-gate GHCR push/sign (D2); harden empty-name in GitHub + Forgejo (D7, D8). **[CLEARED: task_2448; `check_globals_parity` in `tools/render-globals.py` and `tools/test_render_globals.py` verified]**
6. **[P1 · infra] Confirm `ghcr.io/mios-dev/mios` is a PUBLIC package** (D12). **[TASK: task_2484]**
7. **[P1 · coordination, session-bound] Relocate the 3 scratchpad artifacts to `docs/design/`** (E1). **[CLEARED: files tracked at `docs/design/mios-finalization-plan.md#log-message-verbosity-audit`, `docs/design/doc-container-runtime.md`, `docs/design/doc-foss-upstream.md`]**
8. **[P1 · deploy] Offline bootstrap source + integrity + real seed + E2E** (G3, G4, G5, G6). **[TASKS: task_2485, task_2486]**
9. **[P1 · templates gate] Fix match-paths + rebaseline** (C1, C2, C3, C4, C5, C7) so check 46 targets real dirs. **[CLEARED: `check_template_conformance` (check 50 `check_template_conformance`) in `automation/98-drift-checks.sh` enforcing with green ratchet]**
10. **[P1 · docs] Author the concept docs** (E7/E8; container-os-runtime carries corrections) and **run the full green gate** (E14). **[CLEARED: task_2451; concept docs landed and unmigrated narrative count ratcheted to 0]**

---

## 3. Phased Plan

Phases are **workstreams by theme**; the per-task `[P#]` tag preserves the source priority and drives ordering within/across phases (the Critical Path above is the true sequence). Done-when is abbreviated; full criteria live in the source findings.

### Phase P0 — Build / Drift / Deploy GREEN (unblock everything)

| task | owner | done-when | drift-impact | source |
|---|---|---|---|---|
| **[P0]** Reconcile `MIOS_TOML` (legacy) vs `MIOS_VENDOR/HOST/USER_TOML` (resolver) across drift harness + build scripts; mark `MIOS_TOML` deprecated | agy | every drift/build site seeding a test toml sets `MIOS_VENDOR_TOML`; `mios build`→drift stays fully green after the consumer migrations | checks 6/11/17 + any MIOS_TOML-seeding check must stay green | B9: env-name schism vs canonical resolver |
| **[P0]** Reconcile `mios-ci.yml` `PUBLISH` literal vs the lines 26-38 capacity-gate comment | agy | value + comment internally consistent; push-to-main build completes with no exit-125; decision recorded | none (red build blocks merges) | D1: PUBLISH:'true' vs intent |
| **[P0]** Fix Fedora Server DVD acquisition in `MiOS-Cat.bat` (dead F40 URL + 262-byte 404 stub); repoint to current mirror-resilient URL + post-download size/checksum gate | claude-bootstrap | staged `Fedora-Server.iso` is a >2GB checksum-valid ISO9660; a 404 body can never masquerade as the ISO | feeds new [cat] deploy gate (G11) | G1: install source is dead |
| **[P0]** Kickstart runs overlay unattended: export `MIOS_FHS_TOTAL_ROOT_MERGE=1` + `MIOS_INSTALL_MODE=fhs` in `%post` (else Total-Root-Merge auto-declines → bare Fedora) | claude-bootstrap | zero-input kickstart completes build-mios.sh Phase-1; installed box shows FHS overlay + SSOT `[identity]` user, not stock Fedora | none | G2: installs plain Fedora root cause |

### Phase P1 — Correctness (agent-pipe + registry + deploy-offline)

| task | owner | done-when | drift-impact | source |
|---|---|---|---|---|
| **[P1]** Migrate 4 raw-`MIOS_TOML` consumers (oscontrol L162; routing L83/L135; verbcatalog L67/L610) to `_toml_section` | agy | grep for raw `MIOS_TOML` open in those modules is empty (bar an intentional shim comment); mios.d fragments now visible; existing tests pass | keep checks 6 + 706 green | B1 |
| **[P1]** Migrate `portal.py` readers (L118/L989/L996/L290) to `load_merged()`/`_toml_section`; keep configurator tmp-handoff pointed at rendered snapshot | agy | portal no longer opens `os.environ['MIOS_TOML']` for read except handoff; overrides reflected; test_mios_portal passes | keep check 706 green | B2 |
| **[P1]** User-layer write persists a **delta** not a full merged snapshot (compute parsed MINUS vendor∪host) | agy | saving unchanged config leaves USER toml with only diverging keys; a later vendor-default bump is not masked; regression test added | none (add coverage) | B3 |
| **[P1]** `db_authoritative` reads fall back **per-section** to file TOML (or fail-loud) instead of degrade-closed | agy | seeded-PG-without-`agents`-scope returns operator `[agents.*]` (not just hermes); offline unit test; behavior documented | consider new seeded-fixture check | B4 |
| **[P1]** Add datetime/date/time serialization to `to_toml` (config.py:416); raise on unknown types instead of dropping | agy | `tomllib.loads(to_toml(x))==x` for a datetime key; unknown type raises; test extended | none | B5 |
| **[P1]** Memoize `load_db_config`/`is_db_authoritative`/`load_merged` (+ invalidate on write) to stop per-call PG-connect storm | agy | importing server.py with shadow PG issues ≤1 connect; POST invalidates cache; test asserts memoize+invalidate | none (verify checks 11/17 still pass) | B6 |
| **[P1]** `import Any` in `mios_db_config.get` (L256) — undefined name, latent NameError under `get_type_hints` | agy | `typing.get_type_hints` on `get` succeeds | none | B10 |
| **[P1]** Anti-regression drift-check: fail when an agent-pipe module reads mios.toml via raw `MIOS_TOML`/hardcoded-layer list | agy | greps non-test agent-pipe .py, `_violation()`s with fix hint; green on migrated tree, red on reintroduced raw reader | **new check** in automation/98-drift-checks.sh | B11 |
| **[P1]** task_1121 E1: `globals.sh` non-clobbering `: "${MIOS_IMAGE_NAME:=ghcr.io/mios-dev/mios}"`; gate creds on build-vs-publish, never on NAME | agy | no-creds source leaves NAME=ghcr…/mios and NAME:tag==`MIOS_IMAGE_REF` (consistent pair); localhost only via `MIOS_LOCAL_IMAGE` | keep 41/45 green | D3 |
| **[P1]** task_1121 E4: `globals.ps1` twin parity — drop the creds-driven `localhost/mios` default | agy | ps1 default resolves ghcr regardless of creds (env override wins); sh + ps1 agree on clean no-creds env | none directly | D4 |
| **[P1]** Add twin-parity drift check for `globals.{sh,ps1}` `MIOS_IMAGE_NAME` default (none exists; check-28 covers only ports) | agy | new numbered check fails if twins disagree or either forces localhost; passes on fixed twins | **new check** adjacent to 28/41/45 | D5 |
| **[P1]** Event-gate GHCR push/login/cosign steps off `pull_request` (currently only PUBLISH-gated; fork PRs lack packages:write) | agy | PR build validates+lints, no push/sign; push-to-main still publishes | none (CI correctness) | D2 |
| **[P1]** Harden `mios-ci.yml` compute-tags/login/push against empty/hostless `MIOS_IMAGE_NAME` | agy | steps abort loud on empty/no-`/` name; normal run unaffected | none | D7 |
| **[P1]** Harden Forgejo "mirror to GHCR" step (build-mios.yml:154-165) against empty name | agy | mirror asserts non-empty + registry host before login/tag/push; GHCR_TOKEN skip preserved | none | D8 |
| **[P1] INFRA** Confirm `ghcr.io/mios-dev/mios` is a PUBLIC ghcr package | infra | `skopeo inspect --no-creds` (or anon pull) succeeds; visibility confirmed Public | none (registry) | D12 |
| **[P1]** Fix check-30 description/output strings (L1731/1800/1802) to match its body; drop false "userenv.sh maps cleanly" wording (logic unchanged) | agy | check-30 messages no longer mention userenv.sh; still passes on HEAD | check 30 (description only) | E4 |
| **[P1]** Relocate 3 scratchpad artifacts → `docs/design/mios-finalization-plan.md#log-message-verbosity-audit`, `docs/design/{doc-container-runtime,doc-foss-upstream}.md`; rewrite AGY-TASKS.md refs off AppData paths | claude-bootstrap | files git-tracked; grep 'AppData' in AGY-TASKS.md = 0; refs resolve | none (out of build + check-46 scope) | E1 (session-bound) |
| **[P1]** Offline bootstrap-overlay clone: export `BOOTSTRAP_REPO=file://$BOOT_DEST` in kickstart `%post` (default still hardcodes github.com) | claude-bootstrap | NIC-off kickstart completes Phase-1 step-2 from USB-staged bootstrap; no github clone attempt | none | G3 |
| **[P1]** Integrity validation for staged Linux artifacts (Fedora ISO + seed blobs) in stage/FileChecker flow | claude-bootstrap | staging refuses/flags truncated/zero/HTML ISO or seed; FileChecker validates by size+checksum | none | G4 |
| **[P1] INFRA** Produce real Stage-1 seed blobs on MiOS-DEV (`Build-MiOSSeed.ps1` after a completed `mios build`) | infra | seed dir holds non-empty valid `mios-image.oci.tar` + `mios-rootfs.tar`; New-MiOSISO logs staged seed (Green), not degrade-open | none | G5 |
| **[P1] INFRA** E2E validate bare-metal Linux leg in a UEFI VM (USB→Ventoy→Fedora ISO→kickstart→MiOS) | infra | from-scratch VM installs unattended; first boot shows FHS overlay, SSOT user/groups, libvirt+podman sockets, server plane reachable; logs recorded | none | G6 |

### Phase P2 — Wiring (12 capabilities + templates + verbosity/docs)

| task | owner | done-when | drift-impact | source |
|---|---|---|---|---|
| **[P1]** Add `[kargs]` SSOT (iommu, vfio_ids, hugepages, isolcpus, nohz_full, rcu_nocbs, THP) + projector `22-kargs-render.sh` rendering `kargs.d/*.toml`; run before `23-uki-render.sh`; `validate-kargs.py` passes | agy | mios.toml has `[kargs]`; render emits kargs.d; validate passes; build wires it | new `check_kargs_projection` | A1 |
| **[P1]** Wire `validate-kargs.py` as `check_kargs_projection` (re-render to tmp, diff vs committed kargs.d) | agy | registered in main(); green on match, red on hand-edit | **adds check** | A2 |
| **[P2]** Populate empty `vfio-pci.ids=` placeholders from `[kargs].vfio_ids`; drop dangling empty line | agy | no kargs.d ships bare `vfio-pci.ids=` | covered by check_kargs_projection | A3 |
| **[P1]** Add `[security.disk_encryption]` SSOT (mode, pcrs, recovery, backend); mirror all 3 mios.toml copies | agy | keys documented inline; root_toml_subset + etc_duplicates green | checks root_toml_subset, etc_duplicates | A4 |
| **[P1]** Ship `mios-luks-enroll.service` + libexec (`systemd-cryptenroll --tpm2` / clevis bind per backend, PCR policy from SSOT, recovery key, no-op w/o TPM/LUKS); add to `90-mios.preset` | agy | unit gated by ConditionSecurity=tpm2; proven no-op on TPM-less VM | check_firstboot_degrade_open | A5 |
| **[P1]** Bootstrap FHS installer creates LUKS2 root + seeds `/etc/mios/luks-enroll.env` when mode≠none (bare-metal path) | claude-bootstrap | installer luksFormats root + writes enroll env; documented | none (installer side) | A6 |
| **[P2] INFRA** Document TPM2 PCR recovery/escrow flow + configurator field @:8640; post-update re-seal procedure; no key material committed | infra | ref doc under usr/share/doc/mios + configurator field; no plaintext key in tree | check_no_hardcode | A7 |
| **[P2]** Wire chrony: `[ntp]`/`[network.ntp]` SSOT → `/etc/chrony.conf`; enable chronyd in preset | agy | NTP sources projected; chronyd enabled; config matches SSOT under a check | new chrony surface (dotfiles parity) | A8 |
| **[P2]** Wire NUT: `[power.ups]` SSOT → ups/upsd/upsmon.conf; enable nut-server/nut-monitor only when a UPS is declared | agy | rendered from SSOT; VM w/o UPS boots NUT inert | check_firstboot_degrade_open | A9 |
| **[P2]** Add `check_greenboot` (healthcheck + set-rollback-trigger symlinked into multi-user.target.wants; required.d execs) | agy | registered; green on tree, red if symlink/exec bit removed | **adds check** | A13 |
| **[P2]** Add `[quadlets.enable]` ↔ container-file parity check (every enabled key maps to a real `*.container` and vice-versa) | agy | fails on enabled-but-missing / file-but-not-enabled; green on tree | extends check_pod_quadlets | A14 |
| **[P1]** Fix `[templates.systemd-unit].match` → `^usr/lib/systemd/system/[\w-]+\.service$` (units live there, not usr/share/mios/systemd) | agy | 77 units walked against the template; old 1-file path abandoned | check 46 | C1 |
| **[P1]** Fix `[templates.quadlet].match` → `^usr/share/containers/systemd/…(container|pod|network|volume|image)$`; reconcile `generated=true` headers | agy | 22 .container + 3 .pod + 1 .network matched; generated quadlets carry AI-hint + section markers | check 46 | C2 |
| **[P1]** Add `[ai_tag].max_unconforming` SSOT key (sibling of `max_untagged`) = rebaselined offender count | agy | key present; tool reads ceiling from SSOT (not hardcoded 0) | check 46 | C3 |
| **[P1]** Rebaseline `conformance-grandfathered.list` from a real Linux run after dir-fixes (~430→~23-40 real offenders) | agy | list = real remaining offenders; from-scratch run reports unconforming==list size | check 46 | C4 |
| **[P1]** Fix template match shadowing (break-on-first-match): narrow broad `[templates.bash]` or pick most-specific template so bash-verb/drift-check/automation-step markers enforce | agy | `mios-foo.sh`→bash-verb; `automation/98-drift-checks.sh`→drift-check `check_` marker | check 46 | C5 |
| **[P2]** De-duplicate identical drift-check / automation-step match regexes | agy | no two `[templates.*]` share a match regex | check 46 | C6 |
| **[P1]** Add extensionless/shebang-aware `bash-tool` template covering usr/libexec/mios + usr/bin executables (mios-new, mios-ai-tag, check-template-conformance) | agy | nonzero match for extensionless mios-* tools; stripping AI-hint flags one | check 46 | C7 |
| **[P2]** Decide `compile-templates.py`: wire as golden round-trip check or delete (referenced nowhere today) | agy | either a check fails on a corrupted template, or file+refs removed | new check or none | C8 |
| **[P2]** Expand `mios-new` to all 19 registered template types (currently 8) | agy | `mios-new <type> <name>` succeeds for every registered template; scaffold lands where its match accepts | none (feeds check 46 via correct paths) | C9 |
| **[P2]** Fix `mios-new` bash-verb scaffold-path vs match mismatch (extensionless dest vs `mios-…\.sh$`) | agy | scaffolded verb matches bash-verb/bash-tool + passes conformance with no rename | check 46 | C10 |
| **[P2]** Verify markdown-doc/roadmap/roadmap-ws/adr templates match live dirs (≥1 checked file each, not all grandfathered) | agy | each template's checked set includes ≥1 live file | check 46 | C11 |
| **[P2] INFRA** Ensure Linux/CI drift run has python3 so check 46 enforces (not SOFT-skip); document advisory off-Linux | infra | CI logs show "all new files conform"; a non-conforming CI branch fails | check 46 | C12 |
| **[P2]** Remove/repurpose dead `REGISTRY`/`IMAGE_NAME` env keys in mios-ci.yml (L24-25; zero references) | agy | keys deleted; grep returns nothing; registry resolved solely from `MIOS_IMAGE_NAME` | none | D9 |
| **[P2]** Converge dual `MIOS_IMAGE_NAME` SSOT onto `mios.toml [image]`; document precedence (userenv/TOML canonical, globals gap-fill) | agy | globals + userenv yield same name for a given toml; comment states TOML canonical | reinforced by D5; keep 41/45 green | D10 |
| **[P2]** Re-open/reclassify task_230: wire a real Day-2 pull/switch to `MIOS_IMAGE_REF` or label the creds block display-only | agy | TASKS.md reflects reality; ≥1 consumer (mios-update/deploy) resolves switch target from `MIOS_IMAGE_REF`, or hardcode justified | none | D11 |
| **[P2]** Negative test: no-creds `common.sh` load does NOT downgrade `MIOS_IMAGE_NAME` to localhost | agy | fails pre-fix, passes post-fix; wired into a CI-run gate | none (new test) | D6 |
| **[P1]** Apply the 25 "inaccurate" verbosity fixes first (verbatim old→new across automation/*.sh); re-confirm each string; skip+note non-matches | agy | 25 lines corrected or skipped-with-reason; `bash -n` clean; report N/N | none | E2 |
| **[P2]** Apply remaining 42 verbosity findings (27 vague + 12 fluff + 3 redundant) across 40 pipeline scripts | agy | applied or skipped-with-reason; bash -n clean; tally across all 67 | none | E3 |
| **[P2]** Add fluff-token drift lint (bans "successfully", bare "Done", "BAKED IN", trailing "!" in operator echoes); allowlist documented residuals | agy | reds an injected `echo "…successfully!"`; green on HEAD post-fixes; in main() | **new check** | E5 |
| **[P2]** Add coordination-hygiene lint: fail if AGY-TASKS.md/TASKS.md contain AppData/Temp or session-id-shaped paths | agy | reds an injected AppData path; green on HEAD after E1; in main() | **new check** | E6 |
| **[P1]** Author `concepts/container-os-runtime.md` (from docs/design/doc-container-runtime.md); AI-hint header; record wire-what-we-ship gap + the 4 corrections (/var persists, MOK≠UKI, venus≠CUDA, multi-vendor via CDI whole-device) | agy | file exists, valid header + sibling structure; gate incl. 46 green | check 46 (verify header) | E7 (+ absorbs A15 corrections) |
| **[P1]** Author `concepts/foss-upstream-map.md` (from docs/design/doc-foss-upstream.md); AI-hint header | agy | file exists w/ header + structure; gate green | check 46 | E8 |
| **[P2]** Author `concepts/image-resolution.md` (WS-RELTOP: no-creds ref resolves everywhere, localhost never leaks; public-ghcr requirement; cross-ref task_1121) | agy | file exists w/ header; states public-ghcr requirement; cross-refs task_1121 | check 46 | E9 / D13 (deduped) |
| **[P3]** Encode the 4 architectural corrections verbatim into AGENTS.md / GEMINI.md + runtime reference | agy | corrections appear verbatim; no doc implies /var tmpfs or MOK==UKI | none | A15 |
| **[P2]** Make Fedora install-source SSOT-driven: add `[cat]` keys (fedora_iso_url/version/checksum); `MiOS-Cat.bat` reads them, default = `[bootstrap.dev_vm].base_image` major | claude-bootstrap | no hardcoded '40'; resolved major == base-image major | adds [cat] keys consumed by G11 | G10 |
| **[P2]** Stage seed oci-archive onto Ventoy USB (`<USB>\ventoy\seed\mios-image.oci.tar`), size/hash validated | claude-bootstrap | USB holds seed at kickstart-referenceable path | none | G8 |
| **[P2]** Reconcile grub menuentry "Deploy MiOS Linux" to apply the kickstart (route through Ventoy kickstart plugin / pass inst.ks), or remove the misleading direct-chainload | claude-bootstrap | branded entry performs the same unattended kickstarted install; no branded entry yields plain Fedora | none | G9 |
| **[P2]** Add deploy-plane drift-check: assert kickstart exports (`MIOS_FHS_TOTAL_ROOT_MERGE=1`, BOOTSTRAP_REPO/MIOS_REPO offline overrides); Fedora major == base_image major; ventoy.json binds ISO↔kickstart | agy | new check reds on missing exports / diverging majors / missing binding; green post G2/G3/G10 | **new deploy-plane check** | G11 |
| **[P1]** Full `just drift-gate` green after all verbosity edits + new lints + docs; record applied/skipped tally in commit | agy | gate exits 0 incl. new lints; bash -n clean; tally recorded | checks 30 + 46 + fluff + AppData lints | E14 |

### Phase P3 — MiOS-Metal design + deploy plane (immutable leg) + long-tail

| task | owner | done-when | drift-impact | source |
|---|---|---|---|---|
| **[P1]** Finalize MiOS-Metal split-plane content + base posture (**minimal-surface full type-1 bootc hypervisor**, not "handful of scripts"); enumerate host vs all-GPU-guest package sets; standards matrix; both impossible writeups → `docs/design/doc-mios-metal.md` | claude-bootstrap | tracked file with Base-posture section, one-line verdict, standards matrix, mdev+gluster writeups; handed to AGY | none | F1 / E10 (deduped) |
| **[P1]** Author `concepts/mios-metal-architecture.md` from the handoff; AI-hint header; cross-ref ROADMAP/architecture/deploy-model/ADRs | agy | committed; check 46 green; cross-refs resolve | check 46 | F2 / E11 (deduped) |
| **[P1]** Write the mdevctl/vGPU **impossible writeup** (vfio-pci XOR mdev; Intel GVT-g removed from mainline; NVIDIA vGPU needs proprietary licensed host driver) — constrain GPU story to CDI/vfio-pci whole-device | agy | "mdevctl vGPU" appears only as a rejected option; container-os-runtime states "whole-device passthrough; mediated vGPU out of scope" | check 46; feeds F11 | F3 |
| **[P2]** Decide mdevctl fate (**reconciles A10**): keep only for legitimate SR-IOV/mdev inventory (annotate SSOT: NOT a vGPU path, ref F3), or remove from `[packages.virt]`. If kept + hardware-justified, wire `mios-mdev-init` gated on `/sys/class/mdev_bus`, distinct from sriov-init | agy | package annotated-or-removed; if wired, no-op on non-mdev hardware; PACKAGES.md regenerates | check_package_registry / SBOM; check_firstboot_degrade_open (if wired) | F4 + A10 (reconciled) |
| **[P1]** Add `[mini.gpu]` SSOT: assign each GPU (PCI/IOMMU id) to exactly one owner; arbitration model (static pin or libvirt detach/reattach); forbid two active guests claiming a device | agy | assignment table + rule present; exclusive-ownership documented; operator picks from config surface | new SSOT block; candidate NO-HARDCODE allowlist for PCI ids | F5 |
| **[P1]** Console-less LUKS recovery for passthrough posture: `video=efifb:off` in mini-host kargs + serial getty + TPM2/FIDO2 primary unlock (optional BMC/SOL) | agy | kargs SSOT includes efifb:off; serial+TPM2 unlock recipe in mini doc | kargs SSOT/lint (via A1) | F6 |
| **[P1]** Tang-location decision: TPM2-sealed LUKS **primary** (unattended boot); clevis+tang **optional off-host fleet policy**; **forbid on-host Tang** | agy | decision recorded; clevis wired to tpm2 pin or annotated "inert, off-host only"; SSOT states on-host Tang prohibited | feeds F11 | F7 |
| **[P2]** Designate single nftables authority: firewalld canonical; libvirt scoped/documented; tailscaled OFF-by-default or firewalld-integrated (no third uncoordinated owner) | agy | mini doc records ownership model; tailscaled gated to prevent rule-flush conflicts | firewall-ports / NO-HARDCODE port checks | F8 |
| **[P2]** Correct the inverted "super-privileged VM" framing everywhere → "all-GPU workload guest (VFIO-isolated)" | agy | grep `super-privileged` across MiOS docs/tasks returns no ref to the GPU guest | none | F9 |
| **[P2]** Gluster EOL writeup + **remove** `glusterfs`/`-fuse`/`-server` from SSOT (Ceph is the storage plane); note 99-cleanup rm is harmless legacy (**reconciles A12**) | agy | 3 packages removed; writeup committed; OCI build green; PACKAGES.md/SBOM regenerate without gluster | check_package_registry / SBOM | F10 + A12 (deduped) |
| **[P3]** Resolve virt-v2v: wire as documented on-demand VM-import entrypoint, or drop from `[packages.virt]` (no packaged-but-dead) | agy | virt-v2v exposed via a documented import path or removed; package registry reflects decision | check_package_registry | A11 |
| **[P2]** Add impossible/EOL regression drift-check: fail on "mdevctl vGPU" claim in docs, reintroduced glusterfs* packages, or on-host Tang binding; negative test per case | agy | check + 3 negative tests; green at HEAD post F3/F7/F10 | **new check** | F11 |
| **[P3]** Wire editions (mios / mios-xbox / mios-xbox-arm) to `[mini.gpu]` assignment SSOT (no hardcoded per-edition device lists) | agy | edition profiles reference `[mini.gpu]` keys; projection verified | SSOT projection / editions consistency | F12 |
| **[P2]** Wire the immutable bootc bare-metal install leg: offline kickstart variant (`ostreecontainer --url=oci-archive:` or `%post bootc install to-disk`) from USB-staged oci-archive, no registry | claude-bootstrap | second Ventoy/kickstart path installs immutable bootc MiOS fully offline; `bootc status` shows MiOS image booted; disk is ostree/immutable | none | G7 |
| **[P3]** Fix stale `MiOS-Cat.bat` `[cat]`-is-"future/task_236" TODO; ensure launcher resolves paths (xbox_builder/build_driver/log_path + fedora keys) from `[cat]` with %~dp0 fallback | claude-bootstrap | no comment claims `[cat]` unbuilt; launcher reads `[cat]` from mios.toml | none | G12 |
| **[P3]** Bootstrap installer/env parity: verify install.sh/install.ps1 + shipped units export `MIOS_VENDOR_TOML` (not legacy), set `MIOS_DB_AUTHORITATIVE` only when seed path present | claude-bootstrap | grep confirms resolver-consistent usage; documented in R-DH audit notes | none (bootstrap tree) | B13 |
| **[P3]** Register check 46 in the drift-check numbering SSOT + tools/README; document conformance gate + fate of compile-templates.py | agy | numbered index includes 46; README describes gate; no dangling tool reference | none (index consistency) | C13 |
| **[P2/P3] Offline sequencing pair** — B7 offline `test_mios_toml.py` + db_config authoritative-fallback tests; B8 unify key-missing semantics into one owner; B12 db_authoritative deployment/seeding contract (infra) | agy / infra | offline tests green; single module owns the authoritative branch; documented first-boot seeding + PG-down fallback verified | B7: consider widening check 11 to usr/lib/mios; B8 keep check 6 green | B7, B8, B12 |
| **[P3] INFRA** Validate mini posture on real dual-GPU + TPM2 hardware (separable IOMMU groups, retained host console, headless TPM2 unlock with GPU passed through) | infra | `mios iommu`/`mios assess` report showing separable groups + successful unattended unlock | none | F13 |

---

## 4. Per-Owner Queue

### AGY batch (C:\MiOS) — the bulk of finalization

- **P0 gates:** reconcile MIOS_TOML env schism (B9); reconcile mios-ci.yml PUBLISH (D1).
- **Agent-pipe correctness (P1):** consumer + portal migrations (B1, B2); delta write (B3); per-section DB fallback (B4); datetime serialize (B5); memoize (B6); `import Any` (B10); anti-regression gate (B11). Then unify semantics (B8), offline tests (B7).
- **Registry (P1):** globals non-clobber both twins (D3, D4); twin-parity check (D5); event-gate push/sign (D2); empty-name hardening GitHub+Forgejo (D7, D8). Then remove dead env keys (D9), converge SSOT (D10), task_230 (D11), negative test (D6).
- **Templates (P1→P2):** match-path fixes (C1, C2), max_unconforming key (C3), rebaseline list (C4), shadowing fix (C5), bash-tool template (C7); then C6, C8, C9, C10, C11, C13.
- **Runtime wiring (P1→P2):** `[kargs]` SSOT + projector + check (A1, A2, A3); `[security.disk_encryption]` + luks-enroll unit (A4, A5); chrony (A8); NUT (A9); greenboot gate (A13); quadlet parity (A14). Then virt-v2v (A11), mdevctl fate (F4/A10), gluster removal (F10/A12), corrections into agent docs (A15).
- **Verbosity/docs (P1→P2):** check-30 strings (E4); 25 inaccurate fixes (E2) then 42 remaining (E3); fluff lint (E5); AppData lint (E6); author container-os-runtime (E7), foss-upstream (E8), image-resolution (E9); cross-ref docs (E12); full green gate + tally (E14).
- **MiOS-Metal (P1→P3):** author mini-architecture doc (F2); mdev vGPU writeup (F3); `[mini.gpu]` arbitration (F5); console-less recovery kargs (F6); Tang decision (F7); nftables authority (F8); super-privileged relabel (F9); impossible/EOL regression check (F11); editions wiring (F12).
- **Deploy (P2):** deploy-plane drift-check (G11).

### Claude / mios-bootstrap batch (C:\mios-bootstrap)

- **P0:** Fedora ISO acquisition + validity gate (G1); unattended Total-Root-Merge kickstart export (G2).
- **P1:** relocate scratchpad artifacts → docs/design/ **(session-bound — do before this session ends)** (E1); offline `BOOTSTRAP_REPO=file://` (G3); integrity validation of staged artifacts (G4); bootstrap installer LUKS2 format + enroll env (A6).
- **P2:** finalize MiOS-Metal split-plane content → docs/design/doc-mios-metal.md (F1/E10); Fedora `[cat]` SSOT keys (G10); stage seed oci-archive onto USB (G8); reconcile grub menuentry↔kickstart (G9).
- **P2/P3:** wire immutable offline bootc-install leg (G7).
- **P3:** move AGY-TASKS.md out of product tree + update .gitignore/tooling (E13); stale `[cat]` TODO fix + launcher path resolution (G12); installer/env resolver-parity audit (B13).

### Infra / human items (registry / hardware / operational)

- **P1 — confirm ghcr package public:** anon `skopeo inspect --no-creds docker://ghcr.io/mios-dev/mios:latest` succeeds; set org package visibility Public (D12). *Blocks every fresh no-creds `bootc switch`.*
- **P1:** produce real Stage-1 seed blobs on MiOS-DEV (G5); E2E VM acceptance test of the bare-metal Linux leg (G6).
- **P2:** ensure Linux/CI drift runner has python3 so check 46 enforces (C12); TPM2 PCR recovery/escrow doc + configurator field (A7); validate db_authoritative first-boot seeding + PG-down fallback contract (B12).
- **P3:** validate mini posture on real dual-GPU + TPM2 hardware — separable IOMMU groups, retained host console, headless TPM2 unlock (F13).

---

### Dedup / reconciliation notes (nothing dropped)

- **mdevctl** — the runtime-wiring "wire mios-mdev-init" (A10) is **subordinated** to the MiOS-Metal vGPU-impossible decision (F3/F4): mdev is kept only for legitimate SR-IOV/mdev inventory (annotated) or removed; vGPU is out of scope. Merged into one P3 task.
- **glusterfs** — runtime-wiring "resolve gluster" (A12) and Mini Finding 8 (F10) are the **same removal**; merged (EOL, Ceph is the storage plane).
- **image-resolution.md** — registry D13 and verbosity E9 are the **same doc**; kept once.
- **mios-metal docs** — verbosity E10/E11 and Mini F1/F2 are the **same content handoff + authoring pair**; kept once each.
- **Four architectural corrections** (/var persists, MOK≠UKI, venus≠CUDA, multi-vendor via CDI whole-device) flow into one place — `container-os-runtime.md` (E7) — with A15 propagating them verbatim to AGENTS.md/GEMINI.md and F9 fixing the "super-privileged" inversion.
- **TPM/LUKS** mechanism (A4/A5/A6) is designed to satisfy the Mini recovery/Tang decisions (F6/F7); `[kargs]` (A1) carries the `video=efifb:off` entry from F6.
- **MIOS_TOML env schism (B9)** is P0 and must land **before** the B1/B2 consumer migrations, or drift goes red.

<a id="log-message-verbosity-audit"></a>

<!-- AI-hint: Summary record of the MiOS Automation Log-Message Verbosity Audit. -->
# MiOS Automation — Log-Message Verbosity Audit (Summary Record)

> Status: Applied & verified across all pipeline automation scripts under `automation/`.

## 1. Scope & Execution
- **Audited Surface:** 67 findings across 40 pipeline scripts under `automation/` and `automation/lib/`.
- **Core Standard:** Technology-grounded, factual logging with strict removal of unverified action claims, decorative punctuation, and marketing hype words (`successfully`, `Done (bare)`, `BAKED IN`, `pure build-up`).
- **Status:** 100% applied and enforced across the codebase.
