<!-- AI-hint: Derived reference documentation for the numbered MiOS build pipeline phases, derived directly from mios.toml [build.phases]. -->

# MiOS Build Pipeline

This document is derived directly from `usr/share/mios/mios.toml`.

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
