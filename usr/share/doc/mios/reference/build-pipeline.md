<!-- AI-hint: The numbered build pipeline and the Law-6 root Quadlet exceptions, both derived from mios.toml so they cannot go stale. -->

# The build pipeline

<!-- MIOS-GEN:boilerplate:what-mios-is -->
MiOS is one thing built two ways at once: an immutable, `bootc`/OCI-shaped
Fedora workstation -- the whole OS is a single container image, so `bootc
upgrade` behaves like a `git pull` and `bootc rollback` like a Ctrl-Z -- that
is *also* a local, self-hosted, agentic AI operating system.

<!-- derived from usr/share/mios/mios.toml [docs.boilerplate].what-mios-is -->
<!-- /MIOS-GEN:boilerplate:what-mios-is -->

The image is built by a single `Containerfile` that runs every script in
`automation/NN-*.sh` in numeric order. Each script does one thing; the numeric
prefix *is* the execution order. To add a build step you drop a new file next to
its peers — there is no central dispatcher to thread it through.

Two columns below are worth reading carefully:

- **Fatal** — whether a non-zero exit from that phase stops the bake. A
  non-fatal phase is allowed to fail on a host that cannot satisfy it (no GPU,
  no network) without failing the image.
- **Applies** — `containerfile` phases are invoked directly by the
  `Containerfile`; the rest run through `automation/build.sh`.

<!-- MIOS-GEN:pipeline -->
| # | Phase | Script | Fatal | Applies |
|---|---|---|---|---|
| 01 | system-files-overlay | `01-system-files-overlay.sh` | yes | containerfile |
| 02 | materialize-build-ctx | `02-materialize-build-ctx.sh` | yes | universal |
| 04 | local-rpm-mirror | `04-local-rpm-mirror.sh` | yes | universal |
| 05 | repos | `05-repos.sh` | yes | universal |
| 06 | enable-external-repos | `06-enable-external-repos.sh` | yes | universal |
| 07 | kernel | `07-kernel.sh` | yes | universal |
| 10 | locale-theme | `10-locale-theme.sh` | yes | universal |
| 11 | user | `11-user.sh` | yes | universal |
| 12 | hostname | `12-hostname.sh` | yes | universal |
| 13 | accounts-db | `13-accounts-db.sh` | no | universal |
| 14 | podman-machine-compat | `14-podman-machine-compat.sh` | no | universal |
| 15 | freeipa-client | `15-freeipa-client.sh` | no | universal |
| 20 | hardware | `20-hardware.sh` | yes | universal |
| 21 | virt | `21-virt.sh` | yes | universal |
| 22 | akmod-guards | `22-akmod-guards.sh` | no | universal |
| 23 | gpu-passthrough | `23-gpu-passthrough.sh` | yes | universal |
| 24 | cpu-affinity | `24-cpu-affinity.sh` | yes | universal |
| 25 | gpu-cdi-toolkits | `25-gpu-cdi-toolkits.sh` | yes | universal |
| 26 | nvidia-cdi-refresh | `26-nvidia-cdi-refresh.sh` | yes | universal |
| 27 | vm-gating | `27-vm-gating.sh` | no | universal |
| 28 | kdump-config | `28-kdump-config.sh` | no | universal |
| 30 | dns-config | `30-dns-config.sh` | yes | universal |
| 31 | subuid-alloc | `31-subuid-alloc.sh` | yes | universal |
| 33 | generate-quadlets | `33-generate-quadlets.sh` | yes | universal |
| 34 | render-quadlets | `34-render-quadlets.sh` | yes | universal |
| 35 | render-ports | `35-render-ports.sh` | yes | universal |
| 36 | ceph-k3s | `36-ceph-k3s.sh` | no | universal |
| 37 | k3s-selinux | `37-k3s-selinux.sh` | no | universal |
| 38 | selinux | `38-selinux.sh` | yes | universal |
| 39 | moby-engine | `39-moby-engine.sh` | no | universal |
| 40 | fapolicyd-trust | `40-fapolicyd-trust.sh` | yes | universal |
| 41 | services | `41-services.sh` | yes | universal |
| 42 | chrony-render | `42-chrony-render.sh` | yes | universal |
| 43 | nut-render | `43-nut-render.sh` | yes | universal |
| 44 | firewall-ports | `44-firewall-ports.sh` | yes | universal |
| 45 | firewall | `45-firewall.sh` | yes | universal |
| 46 | sshd-port | `46-sshd-port.sh` | yes | universal |
| 47 | init-service | `47-init-service.sh` | yes | universal |
| 48 | mios-dropin-fanout | `48-mios-dropin-fanout.sh` | yes | universal |
| 49 | cosign-policy | `49-cosign-policy.sh` | yes | universal |
| 50 | uupd-installer | `50-uupd-installer.sh` | no | universal |
| 51 | hardening | `51-hardening.sh` | yes | universal |
| 52 | apply-boot-fixes | `52-apply-boot-fixes.sh` | yes | universal |
| 53 | enable-log-copy-service | `53-enable-log-copy-service.sh` | no | universal |
| 55 | native-build | `55-native-build.sh` | yes | universal |
| 56 | fonts | `56-fonts.sh` | yes | universal |
| 54 | bake-coderun-sandbox | `54-bake-coderun-sandbox.sh` | yes | universal |
| 57 | gnome | `57-gnome.sh` | no | universal |
| 58 | gnome-remote-desktop | `58-gnome-remote-desktop.sh` | no | universal |
| 59 | tools | `59-tools.sh` | yes | universal |
| 60 | flatpak-env | `60-flatpak-env.sh` | yes | universal |
| 61 | flatpak-bake | `61-flatpak-bake.sh` | yes | universal |
| 62 | oh-my-posh | `62-oh-my-posh.sh` | no | universal |
| 65 | bake-hyprland | `65-bake-hyprland.sh` | yes | universal |
| 66 | bake-quickshell | `66-bake-quickshell.sh` | yes | universal |
| 67 | bake-surfer | `67-bake-surfer.sh` | no | universal |
| 68 | bake-kvmfr | `68-bake-kvmfr.sh` | no | universal |
| 69 | bake-lookingglass-client | `69-bake-lookingglass-client.sh` | no | universal |
| 72 | hermes-agent | `72-hermes-agent.sh` | yes | universal |
| 73 | model-prep | `73-model-prep.sh` | yes | universal |
| 75 | kargs-render | `75-kargs-render.sh` | yes | universal |
| 76 | uki-render | `76-uki-render.sh` | no | universal |
| 77 | composefs-verity | `77-composefs-verity.sh` | yes | universal |
| 78 | greenboot | `78-greenboot.sh` | yes | universal |
| 79 | boot-config | `79-boot-config.sh` | yes | universal |
| 80 | distribution | `80-distribution.sh` | yes | universal |
| 85 | bake-plan | `85-bake-plan.sh` | yes | universal |
| 86 | oscap-compliance | `86-oscap-compliance.sh` | yes | universal |
| 88 | finalize | `88-finalize.sh` | yes | universal |
| 90 | generate-sbom | `90-generate-sbom.sh` | yes | universal |
| 91 | strip-build-toolchain | `91-strip-build-toolchain.sh` | no | universal |
| 92 | export-sbom | `92-export-sbom.sh` | no | universal |
| 93 | composefs-seal | `93-composefs-seal.sh` | no | bake-only |
| 94 | cleanup | `94-cleanup.sh` | yes | universal |
| 97 | ssot-lint | `97-ssot-lint.sh` | yes | containerfile |
| 98 | drift-checks | `98-drift-checks.sh` | yes | containerfile |
| 99 | postcheck | `99-postcheck.sh` | yes | containerfile |

<!-- derived from usr/share/mios/mios.toml [build.phases].list (77 phases) -->
<!-- /MIOS-GEN:pipeline -->

## Root Quadlet exceptions (Law 6)

Law 6 (UNPRIVILEGED-QUADLETS) requires every Quadlet to declare `User=`,
`Group=` and `Delegate=yes`. The units below are the sanctioned exceptions,
almost all because the upstream image they run insists on uid 0. The list is
registry data, not prose: it is derived from the SSOT, so a unit cannot quietly
join it by editing a doc.

<!-- MIOS-GEN:root-exceptions -->
| Quadlet | Runs as root because |
|---|---|
| `mios-ceph.container` | see `[security.privileged_quadlets]` |
| `mios-radosgw.container` | see `[security.privileged_quadlets]` |
| `mios-k3s.container` | see `[security.privileged_quadlets]` |
| `mios-forge.container` | see `[security.privileged_quadlets]` |
| `mios-forgejo-runner.container` | see `[security.privileged_quadlets]` |
| `mios-pxe-hub.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-crawl4ai.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-firecrawl-api.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-firecrawl-worker.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-redis.container` | see `[security.privileged_quadlets]` |
| `mios-llm-heavy.container` | see `[security.privileged_quadlets]` |
| `mios-coderun-sandbox@.container` | see `[security.privileged_quadlets]` |

<!-- derived from usr/share/mios/mios.toml [security.privileged_quadlets].root -->
<!-- /MIOS-GEN:root-exceptions -->

## Cross-refs

- `usr/share/doc/mios/reference/ports-and-laws.md` — the port allocations and the full law registry.
- `usr/share/doc/mios/guides/engineering.md` — the build-pipeline and shell rules in prose.
- `automation/98-drift-checks.sh` — the fitness functions each law is enforced by.
