<!-- AI-hint: Prioritised upstream prior-art gap brief for MiOS across six lanes (AI plane, configurator, image pipeline, supply chain, bare metal/fleet, dev/cloud/WSL), with repo evidence and SSOT-shaped fixes. -->
# Upstream prior-art gap brief (UPSTREAM_BRIEF)

- **Run:** 2026-10-08 (UTC). Repository baseline: mios.git `12d8ace4` (integration branch for PR #61).
- **Scope:** where MiOS is behind upstream, diverges from it without a reason, or reinvents something upstream already solved. Topics already covered in July (bootc-image-builder archival, Ventoy shim, the `/v1` backends, MCP gateways, Quickshell, Bluefin and elizaOS) were not repeated. Other docs already cover some ground, so this brief only adds to them:
  - `.research/ssot-engine-prior-art-2026-10.md`: SSOT engine and configurator.
  - `.research/native-installer-bootc-prior-art-2026-10.md`: bootc install legs.
- **Method:** six parallel research lanes:
  1. AI plane
  2. Configurator and SSOT
  3. Image build and ship
  4. Rust tooling and supply chain
  5. Bare metal, edge and fleet
  6. Dev, cloud and WSL hosts

  Each lane read the repository read-only and checked upstream against primary sources (release pages, docs, source at a tag). The lead verified the P0 claims it acted on independently, before changing anything.
- **Evidence rules:**
  - Repository facts cite `path:line` at the baseline.
  - Upstream facts carry a version or date.
  - Claims the lanes could not confirm from a primary source are listed in §7.

> **Operator rulings take precedence (2026-10-09).** The operator answered 20 spec questions; see `.devloop/GOALS.md` → "Operator rulings, 2026-10-09". Where a ruling differs from a recommendation below, the ruling wins:
> - **P0-3 / P0-8:** sidecars are physically baked in the full image only, and cloud/dev images skip the bake (ruling Q1), instead of logically bound images everywhere.
> - **P1-24:** only the tiny LFM2 model is baked (Q2).
> - **P1-4:** the password `mios` is kept but ships expired (Q5).

## 0. Already fixed on the integration branch

These three were verified and fixed in commits `8a2fc64e` and `f8701d7e`, then regenerated and committed:

| Gap | Evidence | Fix |
|---|---|---|
| The heavy lane loaded **random weights** and had **no tool calling**. | `--load-format ${MIOS_*_LOAD_FORMAT:-dummy}` in both engine Execs. vLLM documents `dummy` as random initialisation, for profiling only. AGY commit `30b25b1b` (2026-07-22) also dropped `--enable-auto-tool-choice --tool-call-parser`. | Added `[ai.vllm].load_format` and `[ai.sglang].load_format` = `"auto"`. The vLLM Exec renders the parser from `[ai.vllm].tool_call_parser` again. |
| `docker.io/pgvector/pgvector:latest` **does not exist** (manifest unknown). `pg18` and `pg17` resolve. | `cb872445` floated pgvector off `pg18`. | Restored `pg18`. pgvector floats per PostgreSQL major, and `mios-pgvector-major-upgrade` already migrates PGDATA across `pgNN` tags. |
| The firstboot model was **skipped on every run**. | The entry used `url`/`dest`, but the fetcher reads `source`. Its sha256 was the digest of empty input (`e3b0c442…`), and its Hugging Face repo returns 401/does not exist. | Pointed it at the LiquidAI file `[llamacpp].bake_models` already names, with that file's upstream LFS digest. |

## 1. Three patterns behind most findings

1. **Gates prove that a projection matches the SSOT, never that its consumer accepts it.** This is the "gates that cannot fail" class again, one level up:
   - `policy.json` is rendered byte-identical to an invalid type at a path podman never reads (P0-1).
   - The SBOM gate passes when the SBOM is absent (P1-2).
   - The composefs "seal" hashes an empty input in dry run (P2-2).
   - The configurator's save preview diffs the lossy serializer against itself (P0-6).

   **Fix:** give every `[laws.projection_registry]` row a `consumer` field, and have Law 8 run that consumer's own validator: `podman image trust show`, `quadlet -dryrun`, `systemd-analyze verify`, `nft -c`, the SBOM validator, and a round-trip parse.
2. **Two mechanisms for one job.** Each pair should collapse to one SSOT table and one module.
   - Physical bake **and** logically bound images.
   - Two rechunk definitions.
   - `[security.luks]` **and** `[security.disk_encryption]`, with conflicting PCRs.
   - Two Law 15 mirror tools that disagree: the native `mios-gen bootstrap-sync` (`ports`, `colors`, semantic) versus `mios-sync-toml` (four hard-coded tables, byte-exact).
   - Four WSL exporters and two `.wslconfig` writers.
   - Two policy-mode keys.
3. **Security features that are simulated or shipped off:**
   - Attestation is an HMAC keyed by a public value.
   - The "microVM" sandbox is `time.sleep(0.015)`.
   - The config server sends `Access-Control-Allow-Origin: *`.
   - The prompt-injection defenses default to `off`.
   - The published image bakes the password `mios`.

## 2. P0: blocks shipping, correctness or security

### P0-1: Image signatures are never verified on any host
- **Upstream:**
  - containers/image reads `policy.json` from `$XDG_CONFIG_HOME`, `/etc/containers` and `/usr/share/containers`, **not** `/usr/lib/containers`.
  - Valid types are `insecureAcceptAnything|reject|signedBy|signedBaseLayer|sigstoreSigned`.
  - The `fulcio` block needs `subjectEmail`, so GitHub keyless certificates cannot be expressed.
  - cosign v3 writes OCI-referrer bundles that podman and bootc cannot see. Bluefin shipped "effectively unsigned" from 2026-06 (projectbluefin/common#977), and container-libs#388 is still open.
  - ublue image-template (2026-09) and ucore sign **by digest with a key**, in the legacy bundle format.
- **MiOS:**
  - `usr/lib/containers/policy.json:4` and `mios.toml:2471` say `insecureAcceptEverything`, which is not a valid type.
  - `[security.cosign].policy_mode` is a second key that nothing reads.
  - There is no MiOS public key (`usr/share/pki` is absent).
  - CI signs keyless **by tag** (`mios-ci.yml:381`). Forgejo pushes unsigned (`.forgejo/workflows/build-mios.yml:174`).
  - `mios-gate sigpolicy` checks only that the file matches the SSOT byte for byte.
- **Do:**
  - Replace both keys with a typed `[security.sigstore]` that mirrors containers-policy.json: default plus scopes, each with transport, scope, an enum type, `key_paths` and `signed_identity`. Carry the base image's `ghcr.io/ublue-os` entries forward.
  - `mios-gen cosign-policy` renders to `/etc/containers/policy.json` (as ucore does) and `/usr/share/containers/registries.d/`.
  - Use one MiOS key plus a backup, held as a secret on both publishers. Sign `image@digest` and ship the public keys under `/etc/pki/containers/`.
  - Add a two-sided test in bake-smoke: a signed fixture pulls, an unsigned one is refused.
- **Effort:** M.

### P0-2: The bake re-runs the source-tree gates, so a lint drift kills an hour-long bake
- **Upstream:** ublue image-template and Bluefin run lint as workflow steps and the Containerfile only builds. bootc 1.17.0 (2026-10-06) points `ostree container commit` users to `bootc container lint --fatal-warnings`.
- **MiOS:**
  - `Containerfile:128` runs `miosd drift-check` inside the bake. Phases 97 and 98 are `fatal`, `apply_class="containerfile"` (`mios.toml:12237`).
  - Run 37243270635 failed **inside the bake** on a tree the drift-gate job had already judged.
  - `Containerfile:163` still runs `ostree container commit`.
  - CI runs `ubuntu-24.04`, whose apt podman is 4.9.3. Upstream uses `ubuntu-26.04` with podman 5.7.
  - The last green `mios-ci` on `main` was **2026-08-21**.
- **Do:**
  - Re-class 97 and 98 as `apply_class="source"`, run only by the gate job. `build` already `needs:` it.
  - Keep only image-content assertions (99-postcheck) in the bake.
  - Use `bootc container lint --fatal-warnings`, with a `[build.lint]` skip list.
  - Move CI to `ubuntu-26.04`.
- **Effort:** S.

### P0-3: Bound images use two conflicting mechanisms, which makes the image 23 GB and leaves LBIs inert
- **Upstream:**
  - bootc LBIs live in `/usr/lib/bootc/storage` and are pulled with `IfNotExists` (`boundimage.rs`), so a floating `:latest` is never refreshed.
  - A pull failure fails `bootc upgrade` (`deploy.rs:1231`).
  - GHCR limits are 10 GB per layer and a 10-minute upload timeout.
  - bootc 1.17.0 rejects LBIs on the composefs backend (#2549, tracking #2540).
- **MiOS:**
  - 20+ images are baked physically into `/usr/lib/containers/storage` (`Containerfile:153-158`).
  - Both `storage.conf` files add that store globally.
  - **And** every Quadlet is symlinked into `bound-images.d` (`miosd overlay-bind-images`, `main.rs:1590-1690`), including `localhost/*` refs that no registry serves.
  - The SSOT names a third store (`mios.toml:8145`, `/usr/lib/bootc/storage`).
- **Do:**
  - Publish the `localhost/*` builds as `ghcr.io/mios-dev/*` (`mios-node` and `mios-micro` already are).
  - Make each registry sidecar an LBI whose `Image=` carries the **build-resolved** digest, recorded in the SBOM. ADR-0003 holds: `:latest` stays the intent, the digest is SBOM data. Generate this in `mios-gen pod_quadlets`.
  - Drop the global additional store and the physical bake from the published image.
  - Keep pre-seeding only for offline media (`bootc install --bound-images=stored`).
- **Effort:** L. This is the main lever for image size (see P2-1).

### P0-4: The config server has no auth, sends `Access-Control-Allow-Origin: *`, and serves secrets
- **Upstream:** Jupyter Server 2.x (empty `allow_origin`, Host allowlist, XSRF, per-launch token). Syncthing 2.1 (localhost Host check plus API key). Cockpit 369 (no cross-origin websockets by default).
- **MiOS:**
  - `miosd/src/server.rs:300-304` sends a `*` CORS header.
  - GET returns the full six-tier merge, including `portal.password`, `portal.secret` and `ai.api_key` (`:338-345`).
  - POST writes with no Host, Origin, Content-Type or token check (`:347-385`). A `text/plain` POST needs no preflight.
  - The launcher starts it on a predictable port and `disown`s it (`mios-configurator-launch:47-60`).
  - The Python Portal it replaces **did** require auth (`portal.py:149,1272`), so this is a regression.
- **Do** (in `miosd` `server.rs`):
  - Same-origin only. Use a Host allowlist.
  - POST requires a matching Origin and `application/json`.
  - Use a per-launch 0600 token exchanged for an `HttpOnly; SameSite=Strict` cookie, and honour `[portal].require_login`.
  - Redact secret keys on GET.
  - Add a negative test: a cross-origin `text/plain` POST is refused.
- **Effort:** S–M.

### P0-5: Configurator saves land in a tier no system consumer reads
- **MiOS:**
  - `server.rs:280-297` always writes the server process's own `~/.config` tier. Builds and root services never read that tier.
  - agent-pipe runs as `mios-ai` under `ProtectSystem=strict` without that home in `ReadWritePaths`.
  - `kernel/config.py:466` swallows a failure to load the lower tiers, so a whole document lands in the user tier.
  - This contradicts operator decision Q2 (host tier `/etc/mios/mios.d/`, 2026-10-03).
- **Upstream:** VS Code per-setting `scope` routes each write to a file. Talos applies config through an authenticated API.
- **Do:**
  - Each key declares a target tier (`x-mios-tier`, see P1-14).
  - The server routes key-level patches to it. Host-tier writes go through a root-side `mios-resolve set --tier host` over a `SO_PEERCRED` UNIX socket.
  - Vendor-tier writes are refused.
- **Effort:** M.

### P0-6: Saving through mios.html silently corrupts config
- **MiOS:** `mios.html`'s TOML parser has three defects:
  - It strips `#…` from every value (`:4237`), so `"#282262"` becomes `""`.
  - It splits arrays on commas (`:4266`).
  - It drops `''` items (`:4272`).

  A measured round trip over the vendor file (6,504 keys) dropped 269 keys, changed 277 and added 100.
  - **150 of the changed values are non-empty and would override vendor.** Port category `members` lose their placeholders, which shifts positional allocation. `Volume=` splits at `:ro,Z`. `ExecStart` is truncated.
  - The save preview diffs `emitToml` against `emitToml`, so it can never show the damage.
  - Fields without a `data-type` save as strings.
  - `tools/native/Cargo.toml:51` pins `toml_edit = "0.20"`, which predates TOML 1.1 and `DocumentMut`. The current release is 0.25.x.
- **Do:**
  - The page keeps a JSON view and POSTs key-level patches `[{path, op: set|unset, value}]`.
  - `mios-resolve set` applies them with toml_edit 0.25 (format-preserving, as `cargo add` does).
  - For `file://` (ADR-0008), inline the same Rust core as wasm32, so there is one implementation.
  - **Gate now:** a no-op save must change zero keys.
  - This also corrects GOALS M4: the page lives in immutable `/usr` and cannot be the authority.
- **Effort:** M.

### P0-7: Remote attestation can be forged, and it hands out cluster credentials
- **MiOS:**
  - `usr/libexec/mios/mios-attest-server:100-111` simulates TPM2_Quote with an HMAC keyed by the **public** EK fingerprint.
  - On success it issues hard-coded material (`:395-430`): a fake WireGuard key, a fake Ceph fsid and `10.42.0.1` endpoints, which contradict `[headscale].vnet_cidr`.
  - It listens on hard-coded port 8443 and is `WantedBy=multi-user.target` on controller blades.
- **Upstream:**
  - Flight Control 1.3.1 (2026-09-29): EK chain against TPM-vendor CAs, LAK/LDevID, credential activation, a TPM-bound management certificate.
  - Keylime 7.14.3.
- **Do:**
  - Now: condition the unit off, or list it in `[blades.hazards]`.
  - Then: run real attestation (Keylime verifier and registrar as Quadlets, or Flight Control's activation flow inside `miosd`).
  - Hand out only a short-lived headscale pre-auth key and a k3s join token.
- **Effort:** S to disable, M to replace.

### P0-8: Cloud images exceed the hosted-sandbox disk, and baked sidecars, not weights or apps, are the cause
- **Operator design:** cloud images carry no AI model weights and no desktop flatpaks or apps; the `[deployment.cloud.overlay]` switches off firstboot model pulls and runtime desktop installs. Every MiOS image is otherwise equivalent.
- **Measured** on `ghcr.io/mios-dev/mios:latest` (49 GB unpacked, built 6 weeks before this run):

  | Path | Size | Content |
  |---|---|---|
  | `/usr/lib/containers/storage` | 26 GB | Baked sidecar/app container images; `MIOS_BAKE_BOUND_IMAGES=1` on the publishing build only. |
  | `/usr/share/mios/llamacpp` | 5.8 GB | Light-lane GGUF weights, including `granite-4.1-8b.gguf` (5.0 GB). That conflicts with the design for the cloud images. |
  | `/usr/lib64/rocm` | 2.4 GB | ROCm. |
  | flatpak | 28 KB | No flatpaks, as designed. |

- **Upstream:**
  - Claude Code cloud: about 4 vCPU, 16 GB RAM, **30 GB disk**, and setup cached only within ~5 minutes (code.claude.com/docs/en/cloud-environments).
  - Codex cloud: 8 GiB disk on Plus, **32 GiB** on Pro, Business and Enterprise.
  - Codespaces' large machines fit the image as it is.
- **MiOS:**
  - `bootstrap.sh` pulls the same `[image].ref`.
  - `[deployment.cloud].min_free_gb = 80`.
- **Do:**
  - Make the light-lane weights a firstboot or `Mount=type=image` model artifact (P1-24), so no published image carries weights in `/usr`.
  - Move the 26 GB of sidecars to logically bound images pulled on use (P0-3). That shrinks every image equally (OCI, Hyper-V, ISO, cloud), so they stay equivalent.
  - Derive `min_free_gb` from the measured image instead of the hand-set 80.
- **Effort:** shared with P0-3 and P1-24.

### P0-9: MiOS ships as a Hyper-V image, but nothing builds it, and MiOS-DEV cannot be MiOS on WSL
- **Operator design:** MiOS ships as a Hyper-V image (VHDX) too, alongside OCI, ISO, qcow2 and WSL.
- **MiOS:** the target is declared but not delivered.
  - Declared in `[deployment].target_vhd = true`, `[deploy.formats.vhdx]`, the Justfile `vhdx` recipe (bootc-image-builder `vpc`, then `qemu-img convert` to VHDX), and the variant artifact lists.
  - GPU-PV (dxgkrnl) is covered in `automation/20-hardware.sh`, and the `gpu-pv-detect` unit detects it.
  - No CI job builds the VHDX, on either publisher.
  - `config/artifacts/vhdx.toml` carries `REPLACEME` credentials and a 150 GiB root floor, while `[bootc_install].root_min_gb` is 80.
  - The dev VM is created on the default WSL provider (`build-mios.ps1:2116-2125`), so `build-mios.ps1:5181`'s "bootc switch → MiOS-DEV IS MiOS" cannot happen: Podman 6.0.2 says WSL machines "cannot be updated" with `machine os apply`.
- **Upstream:**
  - Podman machine on Hyper-V runs Fedora CoreOS and supports `podman machine os apply` (`bootc switch`). Podman 6 adds `podman system hyperv-prep` for non-elevated management.
  - bootc-image-builder emits `vhd`, converted to VHDX for Generation 2 VMs. Linux Secure Boot needs the "Microsoft UEFI Certificate Authority" template.
  - GitHub release assets are limited to 2 GiB per file and GHCR to 10 GB per layer, so a full-size VHDX cannot be a single release asset.
- **Do:**
  - Add a native `mios-build artifact vhdx` driven by `[deploy.formats.vhdx]`. Identity comes from `[identity]` or the install answer file, never recipe placeholders. The root floor comes from `[bootc_install]`, and the VM shape (Gen 2, Secure Boot template, vCPU and RAM, dynamic VHDX) from SSOT.
  - Build and boot-test the VHDX in CI (P1-1). Deliver it by building locally from the published OCI digest in MiOS-DEV, not as a single release asset.
  - Add `[bootstrap.dev_vm].provider = "wsl"|"hyperv"`. On Hyper-V, MiOS-DEV is a MiOS VM, with real `bootc` upgrade and rollback, and GPU-PV through dxgkrnl. The WSL distro stays for WSLg.
  - Correct the parity claim in `build-mios.ps1`.
- **Effort:** M–L.

## 3. P1: high leverage

| # | Gap | Upstream reference | MiOS evidence | Fix and home | Effort |
|---|---|---|---|---|---|
| P1-1 | CI never **boots or upgrades** the image (effectively P0 for shipping). | bootc CI runs tmt in nested-virt VMs on hosted `ubuntu-26.04`; bcvk 0.21.0 `ephemeral run-ssh`, `to-disk`. | `tests/bake-smoke.sh` only runs the container. `automation/bcvk-wrapper.sh` is called by nothing. A boot test would catch `lockdown=` set twice plus `module.sig_enforce=1` with unsigned kvmfr. | `boot-test` job: system running, `bootc status`, greenboot green, no rejected modules. `upgrade-test`: N-1 to N via `bootc switch`. Driven by `[testing.boot]`, implemented in `mios-gate`/`mios-build`. Gate publishing on it. | M |
| P1-2 | The SBOM can be **missing or fabricated** while gates stay green. | ucore reusable-build: syft pinned, `validate-sbom.py` fails closed, `oras attach` plus a signed referrer. actions/attest 4.1 `sbom-path`. | `90-generate-sbom.sh` is `set +e` and exits 0 on failure, while marked `fatal`. It runs `curl … main \| sh` at bake (breaks Law 12). Syft is stripped later, so `92-export-sbom.sh` writes a one-package placeholder. `check_sbom_metadata` passes when the SBOM is absent. The test asserts the degrade-open. | `[security.sbom]`. Generate in CI from the pushed digest. `mios-gate sbom` fails closed. Delete the placeholder writer. Attach and sign the SBOM. | M |
| P1-3 | CI is one **serial, fail-stop** job: no cache, two bakes, no timeouts. | bootc `ci.yml` `compute-ci-level`; Swatinem/rust-cache 2.9.2; podman `--cache-to/--cache-from`. | Run 37828005846: a 0.4 s check failed after 9.5 min of setup, and the smoke build ran 4h. `mios-ci.yml` has no `timeout-minutes`. | `tools/ci-suites.py` emits a matrix from `[ci.tiers]` with the SSOT-only tier first. Build natives once and share them as an artifact. On main, smoke-test the build's own image. Timeouts come from `[ci]`. | M |
| P1-4 | The published image bakes the known password **`mios`**. ISO, qcow2 and vhdx carry placeholder credentials. | bootc injects credentials at install; Microsoft's WSL guide forbids password hashes in the tar. | `automation/11-user.sh:88-95` (`openssl passwd -6 'mios'`). `config/artifacts/iso.toml:22-28` (`clearpart --all`, `REPLACEME` hash, placeholder key). The same hash reaches every WSL tar. | Inject at install (`[bootc_install]` SSH-key and credential pointers). Render artifact identity from `[identity]`. Add a gate on `REPLACE` tokens. Treat as **P0 if Cockpit is LAN-reachable.** | S–M |
| P1-5 | **Update channels**, and the offline soft-reboot never applies the update. | uCore `lts`/`stable`/`testing` plus dated tags. `bootc upgrade --apply --soft-reboot=auto`, `--download-only`/`--from-downloaded`. | Only `latest`, version and build tags exist. `50-uupd-installer.sh:353-375` calls a bare `systemctl soft-reboot` with no `/run/nextroot`. | `[image.streams]` (testing = every boot-tested main push; stable = a promoted digest). Use `bootc upgrade --apply --soft-reboot=auto`. Delete the kernel-diff logic. | M |
| P1-6 | Greenboot **`required.d` holds app-plane checks**, so a slow model load rolls back a good OS. | greenboot-rs 0.16.4 (F43+): `required.d` is for OS-level checks. | `40-mios-ai-plane.sh` (60 s probes). `55-mios-db-check.sh`. `10-uki-promote.sh` rewrites boot entries. | Split `[greenboot]` into `required` and `wanted`. Delete `10-uki-promote.sh`. Assert in P1-1. | S |
| P1-7 | **Rechunking** is effectively off and defined twice. | rpm-ostree `build-chunked-oci` reuses layers only against the previous image; chunkah (content-based, `user.component`). | `mios-ci.yml:285-323` pulls no previous image and degrades open. `rechunk.sh` is disabled and sets xattrs **after** rechunking. `rechunk_max_layers` is duplicated. | One recipe in `mios-build`: pull the previous `:latest`, rechunk to the same name, set `user.component` in the bake, fail closed on main. | S–M |
| P1-8 | **MiOS-MODULES** need argv[0] dispatch and one workspace; ADR-0021 says "not a multicall dispatcher". | bootc 1.17.1 `callname_from_argv0` absorbed ostree-ext. Talos `machined` switches on argv[0]. uutils 0.12: one feature per applet, `build.rs` phf map. `clap` `multicall(true)`. | Two workspaces and lockfiles with diverging majors (miette 5/7, thiserror 1/2, syn 1/2/3). The `L+` shims assume argv[0] dispatch that no crate has. `native_build.rs` makes ~2×N cargo calls. No shared release profile. | Merge the workspaces (task_951). Each crate becomes an applet (`command()`, `run()`). Category binaries take applets as features. `mios-gen cargo-manifests` projects features and alias tables from `[rust.categories.*].replaces`. Shared LTO/strip profile. Amend ADR-0021. | L |
| P1-9 | **Renovate** never ran, and its config would auto-merge a cosign v3 break. | Renovate `config:best-practices`; GitHub SHA-pinning policy (2025-08). | No bot PRs. `"automerge": true` globally. `jlumbroso/free-disk-space@main`. `Containerfile:24` hard-codes `rust:slim`. | Install the app. Keep action-digest pinning, drop `docker:pinDigests` (ADR-0003). No automerge for cosign, syft or oras majors. Require SHA pinning. | S |
| P1-10 | No **build provenance** (not SLSA Build L1). | GitHub artifact attestations: L2 alone, L3 from a reusable workflow; free for public repos. | No `attest` step. Signs by tag. Obsolete `COSIGN_EXPERIMENTAL`. | `podman push --digestfile`, then `actions/attest` (push-to-registry). On Forgejo, `cosign attest --type slsaprovenance`. | S |
| P1-11 | Nothing defends the gates mechanically against "cannot fail". | cargo-mutants 27.1 (`--in-diff`); mutmut 3.8. | 5,812 hand-written negative lines. `max_checks_without_negative = 44` kept by hand. `Report.ok` is independent of `findings`. | `cargo mutants --in-diff` on `mios-gate`, `miosd::drift` and `mios-gen`, with a shrink-only missed-mutant ratchet. Derive `ok` from `findings`. Add `consumer` validators (§1). | M |
| P1-12 | **Fleet updates:** no staged rollout, no disruption budget, auto-update off. | `bootc upgrade --download-only`; Flight Control `rolloutPolicy` (BatchSequence, `disruptionBudget`); kured 1.23. | `90-mios.preset:234` disables the update timer. ADR-0016: blades "upgrade independently". No `[fleet]` table. | `[fleet.rollout]` (batches by `[blade]` label, thresholds, windows), implemented in `miosd`. Enforce signatures (P0-1) **before** any automatic path. | M |
| P1-13 | **Zero-touch identity:** a fresh node never joins the mesh. | Proxmox automated install (TOML answer file, `PROXMOX-AIS` label, DHCP/DNS URL, webhook); systemd credentials; headscale 0.29.4 tagged pre-auth keys. | `mios-install` takes only a disk. `mios-tailscale-sync:72-98` builds `tailscale up` and **returns 0 without running it**. | `[install.answer]`, resolved from the MiOS-Repo partition, then an HTTP URL, then interactive. Write the pre-auth key and k3s token to `/etc/credstore.encrypted` during `--skip-finalize`. Mesh join becomes a real unit using `ImportCredential=`. | M |
| P1-14 | No **disk encryption at install**; two tables disagree. | systemd-repart `Encrypt=key-file+tpm2`; Fedora sealed images (2026-04). bootc's bare `tpm2-luks` enrolls no recovery key. | `mios-install` has no LUKS. `[security.luks]` (PCR 7, disabled) versus `[security.disk_encryption]` (PCRs 7 and 14, recovery). | Fold into `[security.disk_encryption]`. Project repart `Encrypt=`. Generate a recovery key at install. FIDO2 as the second token. | M |
| P1-15 | **Agent-pipe dispatch** is unauthenticated, with no Origin or Host check. | MCP 2026-07-28 transport (Origin MUST be validated); MCP Python SDK v2 rebinding protection. | `/v1/dispatch` (`mios_dispatch.py:799`). `api_require_auth = false`. The socket unit has `ListenStream=8700` on all interfaces. | Host/Origin middleware from `[security].allowlist_hosts`. The MCP→pipe hop uses a `SO_PEERCRED` UNIX socket. Render the socket from `[ports]` on loopback. | S–M |
| P1-16 | **Prompt-injection defenses** ship off; the "microVM" is simulated. | Meta Rule of Two (2025-10); CaMeL (2025-03); Fedora `crun-krun` 1.28. | `rule_of_two_mode`, `quarantine_mode` and `principal_bind_mode` = `"off"`. `microvm_sandbox.py:49-55` is a sleep with `is_contained = True`. | Vendor default `audit`, then `enforce`. Delete the simulated module. `[sandbox].runtime = "crun"\|"krun"` rendered into the coderun Quadlet. | S–M |
| P1-17 | The **WSL distro** ignores Microsoft's `.wsl` contract. | "Build a custom distro" (WSL ≥ 2.4.4): gzip tar, `[oobe]`/`[shortcut]`, `--install --from-file`, no kernel, resolv.conf or password hashes; Fedora/Alma `wsl-setup`. | `etc/wsl-distribution.conf` lacks `[oobe]`. `usr/lib/wsl-distribution.conf` uses a schema WSL never reads. Four divergent exporters. | One exporter in `mios-build` writing `mios.wsl`. Render `[wsl2.distribution]`. Add `wsl-setup`. Gate the archive contents. | M |
| P1-18 | **`.wslconfig`** overrides the SSOT; WSL-native settings are reimplemented by hand. | WSL config docs (2026-09-16): which keys go under `[wsl2]`, `[general]` and `[experimental]`; `wsl --manage --move`. | The template puts `[wsl2]` keys under `[experimental]`. `Repair-WslConfig` flips NAT to mirrored against the SSOT. Keepalive scheduled task. Re-import to move. | `[dotfiles.registry.wslconfig]` rendered from `[wsl2]`. `instanceIdleTimeout=-1`. `distributionInstallPath`. Delete both writers. | S |
| P1-19 | **Codespaces** prebuilds can't help: everything runs in postCreate and again on every start. | GitHub prebuilds run only `onCreate`/`updateContent`; `waitFor`. | `devcontainer.json:200-201`. Two `cargo build --release` runs on every start (`boot-mios-systems.sh:105`). | Move setup into onCreate/updateContent, set `waitFor`, add one prebuild on main. Fix both READMEs. | S |
| P1-20 | The **Windows host** is configured imperatively; there is no drift test. | DSC v3.3.0 (2026-09-17, Rust, RPM/DEB too); `winget dscv3` `Microsoft.WinGet/Package`. | ~13 `winget install` sites in `Get-MiOS.ps1`. `dism` features. Duplicated IDs in `[packages.windows]` and `[bootstrap.prereqs]`. | `mios-gen` renders one DSC v3 document. `Get-MiOS.ps1` shrinks to install DSC and run `dsc config set`. `dsc config test` is the Windows drift gate. Remove the duplicate keys. | L (M for a first slice) |
| P1-21 | **Configurator state** has no revision, ownership, unset, schema or secret handling. | Kubernetes SSA `managedFields`; `If-Match`/412; Grafana/VS Code "managed by"; Kairos `print-schema`; schemars 1.2 / jsonschema 0.58; systemd-creds. | No ETag. `user.d` values are frozen into the user file. Comments are destroyed. Secrets are `type="text"` and written 0644. `miosd secret set` exists but is unused. No schema; 652 of 6,504 keys are bound. | GET returns `{effective, provenance, rev}`. POST uses `If-Match` with a per-key three-way merge. Add `unset`. Secret keys from `[security.secret_keys]` are write-only fields stored via `systemd-creds`. `mios-gen schema` plus `mios-resolve validate`. | M–L |
| P1-22 | **Progressive disclosure** as one global "Admin/Extras" switch repeats a pattern upstream is removing. | Home Assistant deprecates its single Advanced-mode switch (blog 2026-05-26; removal in Core 2027.6) in favour of collapsible per-integration sections. VS Code: "Commonly Used", `order`, `@modified`, per-setting reset. | The Home/Advanced split is fixed in markup (`mios.html:858-933`). No modified filter or reset. | Schema `x-mios-ui: {level: essential\|advanced\|internal, group, order}`. `mios-gen configurator` builds Home from `essential` plus per-group collapsible sections. Add `@modified` and per-key reset. **This is how to implement the "Admin settings / Extras" directive.** | M |
| P1-23 | No **reconcile after save**; projections are never validated by upstream tools before activation. | Talos `apply-config --mode … --dry-run`; `systemd.path`; `quadlet -dryrun`. | No post-save apply (G07). No `quadlet -dryrun` anywhere. | Projected `mios-config-apply.path` watching the host tier triggers `mios-gen apply --phase save`. Render to a staging root, validate, then swap. | M |
| P1-24 | **Model provisioning** remainder (after §0). | Podman `type=artifact` mounts (5.6) and `.artifact` Quadlets (5.7, experimental); CNCF ModelPack. | The fetcher reads only the vendor TOML. Its retry timer `mios-models-firstboot.timer` does not exist. `big_ram_model` is not an HF repo id. `test_mios_models.py:122` uses its own fixture, so it cannot catch any of this. | Generalise the working `mios-micro` pattern: models as OCI images, `Mount=type=image`, rendered by `mios-gen`. Move to `.artifact` once bootc supports it. | M |
| P1-25 | **Observability** collector is EOL Jaeger v1. | Jaeger v1 EOL 2025-12-31; OTel semconv 1.37 renamed `gen_ai.system` to `gen_ai.provider.name`. | `mios-otelcol.container:17` `all-in-one:latest`. Spans use the deprecated attribute. | Jaeger v2 or otelcol-contrib. `[observability].genai_semconv`. vLLM `--otlp-traces-endpoint` from the same key. | S–M |

## 4. P2

- **P2-1: Image size discipline.** One 23 GB "full" profile ships to every target.
  - `61-flatpak-bake.sh` installs into `/var`, which the Containerfile then deletes.
  - The Rust toolchain ships in every image.
  - **Do:** publish `mios-core` next to `mios`; move flatpaks to `/usr/share/flatpak/preinstall.d` (Flatpak 1.17+); move the toolchain to the `mios-dev` variant. Combined with P0-3 and P0-8.
- **P2-2: The composefs "seal" is theatre.**
  - `93-composefs-seal.sh` writes a digest into `/tmp` that nothing verifies; in dry run it is the hash of empty input.
  - bootc 1.17 declared the composefs backend stable, but it blocks LBIs (#2540) and has no boot counting.
  - **Do:** keep ostree with composefs `verity` (`77-composefs-verity.sh` is the real part). Delete `93-composefs-seal.sh`. Record `[security.composefs].backend = "ostree"` with the blockers.
- **P2-3: The hand-rolled UKI path is unsigned and bypasses bootc.**
  - `mios-ukify-stage` and `10-uki-promote.sh` write into ostree-owned `/boot/loader`, which is swapped on every deployment.
  - **Do:** delete both. When adopting UKIs, follow Fedora sealed images (sbctl keys, `bootc container ukify`, signed PCR 11).
- **P2-4: PXE hub is an empty matchbox.**
  - `liveiso.py` invents `bootc.install.to-disk=auto` and hard-codes `192.168.1.1:8080`.
  - **Do:** `mios-gen` renders matchbox groups from the blade registry, plus dnsmasq proxyDHCP and iPXE; netboot the small installer, which runs `mios-install`.
- **P2-5: Metal.**
  - `[metal.gpu].assignments` hard-codes `0000:01:00.0`.
  - `mios-metal-vfio-gen` echoes env vars and hides errors.
  - There is no MiOS-guest domain generator.
  - **Do:** use a class selector resolved at boot into `/etc/driverctl.d`; have `mios-unit-gen` render the guest domain with oemStrings credentials; use `bootc install to-disk --via-loopback` once. Evaluate `ucore-minimal` as the host base.
- **P2-6: Static Rust transparency.**
  - **Do:** build through `cargo auditable` (0.7.7; syft and trivy read it). Record rustc and the xwin SDK/CRT versions in the SBOM. Bake the xwin cache into the builder for Law 12.
- **P2-7: Schema versus drift checks.** A JSON Schema can replace about 15–25 *shape* checks, but not regenerate-and-diff checks.
  - **Do:** put the effort into *consumer* schemas (an enum would have caught `insecureAcceptEverything`). Keep CUE and OPA out (Go); regorus is the Rust option if rules-as-data is ever wanted.
- **P2-8: Atomic writes.**
  - `server.rs:293-295` has no fsync, a fixed temp name, umask permissions and no history.
  - **Do:** `NamedTempFile` + `sync_all` + persist + dir fsync at 0600; keep N versions under `/var/lib/mios/config-history`; add `mios-resolve rollback`.
- **P2-9: GPU co-tenancy.**
  - **Do:** use vLLM sleep mode (`/sleep`, `/wake_up`) from the existing admission code; add native KV offload; add a per-lane `device` key rendered into `AddDevice=` (matters for Metal multi-GPU guests).
  - The `mios.toml:6472` claim that only SGLang can reach 256k is stale.
- **P2-10: The devcontainer never boots.**
  - **Do:** add an opt-in "booted" profile (`privileged`, `/sbin/init`, lifecycle gated on `systemctl is-system-running --wait`). Generate a `devcontainer.metadata` LABEL so both devcontainer.json files shrink to a delta.
- **P2-11: GCE runs MiOS as a privileged container on Ubuntu.**
  - **Do:** generate Butane/Ignition auto-rebase from SSOT onto FCOS (`fedora-coreos-stable`, `user-data`), or build the image-builder `gce` type.
- **P2-12: wslc** (WSL containers GA 2026-09-29).
  - `wslc run` has no `--privileged`, `--device` or systemd support.
  - **Do:** keep the podman machine. Pin the Dev Containers engine to podman from the SSOT.

## 5. P3

- **PiKVM/OOB:**
  - task_468 is marked done but has no code.
  - `[management_mesh]` adds a second WireGuard mesh.
  - **Do:** reopen task_468; put PiKVM on headscale as `tag:oob`; add `bmc = {kind, url, credential_ref}` to blades; add `mios-install remote` via Redfish `InsertMedia` or kvmd MSD.
- **A2A card:** `protocolBinding: "OpenAI"` is not an A2A 1.0 binding (`a2a.py:222`). Move it to an extension.
- **Dev Drive:** keep M: as NTFS. WSL's `metadata` mount option is unsupported on ReFS, and WSL file I/O gains nothing. ADR-0005's "missed opportunity" note should say so.

## 6. Checked and not a gap

- **MCP:** 2026-07-28 with `mcp>=2.1.1,<3`, loopback-only.
- **Routing:** prefix caching and routing are adequate for one heavy replica (llm-d and the production stack target multi-replica Kubernetes).
- **Already in place:**
  - The bootc install leg (ADR-0014 and `tools/native/mios-install` with `--source oci-archive:`). It is unverified on hardware, and the bare-metal leg no longer "installs plain Fedora".
  - `bootc container lint` as the final layer.
  - `[bootc_install]` with `bound_images="stored"`.
  - uupd with a fallback timer.
  - The firstboot tier.
  - Gate before build.
- **Toolchain:** cargo-xwin 0.23.1 is current; cargo-zigbuild adds nothing for musl + rust-lld; `cross` is stale (last release 2023-02).
- **sysexts are not the size lever:** they are `/usr`-only and cannot carry kernel modules. LBIs and variants are the lever.

## 7. Not verified from a primary source

- **Signatures and policy:**
  - Whether the `ucore-hci:stable-nvidia` image's own `/etc/containers/policy.json` matches ucore's `cleanup.sh`.
  - Whether public Fulcio trusts a self-hosted Forgejo OIDC issuer.
  - Whether `actions/attest push-to-registry` works against GHCR referrers.
- **Bound images and rechunking:**
  - Whether bootc's bound-image existence check sees `/usr/lib/containers/storage` through the global `storage.conf`.
  - Whether rpm-ostree accepts `--from=containers-storage:` and whether `bootc-base-imagectl` exists in `ucore-hci`.
  - chunkah's current release.
- **vLLM:**
  - The start-up refusal when `max_model_len` exceeds KV capacity (secondary sources only).
  - The spelling of the KV-offload flags.
- **CI and toolchain:**
  - Why run 37828005846's 4h48m smoke build failed (log not retrievable).
  - The stabilisation status of Cargo `trim-paths`.
- **Hosted environments:**
  - Systemd as PID 1 in Codespaces.
  - Whether ghcr.io blob redirects are allowed in Claude cloud sessions.
  - Whether Docker or Podman is usable in Codex cloud.
- **Inferences:**
  - That agent-pipe saves fail under `ProtectSystem=strict` (static reading).
  - That a systemd path unit fires on rename-into-place.
- **Fedora docs** behind Anubis (WSL page, bootc GCP guide, `wsl-setup` spec) were read through mirrors and the AlmaLinux copy.

## 8. Suggested order

1. **Before the next publish:**
   - P0-2: move the gates out of the bake (S).
   - P1-4: stop baking the password (S–M).
   - P0-7: disable attestation (S).
   - P0-4: lock down the config server (S–M).
   - The three fixes in §0 are already done.
2. **Make publishing trustworthy:**
   - P0-1 signing and policy.
   - P1-2 SBOM.
   - P1-10 provenance.
   - P1-9 Renovate.
   - P1-1 boot and upgrade tests.
   - P1-3 CI shape.
3. **Make the image fit:**
   - P0-3 one bound-image mechanism.
   - P1-24 weights out of `/usr`.
   - P2-1 profiles.
   - P1-7 rechunk.
4. **Configurator (GOALS M4):**
   - P0-6, then P0-5, P1-21, P1-22 and P1-23, in that order. Key-level patches through toml_edit are the foundation the rest builds on.
5. **MiOS-MODULES (GOALS M2):**
   - P1-8 workspace merge and argv[0] applets.
   - P1-11 mutation testing on the gate crates.
6. **Hosts:**
   - P0-9 Hyper-V image and MiOS-DEV on Hyper-V.
   - P1-17 to P1-20 (WSL, Codespaces, DSC v3).
7. **Fleet and metal:**
   - P1-12 to P1-14, then P2-4 and P2-5.
