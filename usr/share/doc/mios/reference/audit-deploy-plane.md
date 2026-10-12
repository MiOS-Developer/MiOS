<!-- AI-hint: Audit of the MiOS DEPLOY plane (the least-done area, ~15-25%): traces the OFFLINE immutable-bootc install chain (Justfile oci-archive/BIB -> mios-stage-oci-archive -> tools/install.sh -> field/loopback.cfg + ventoy.json) end-to-end, proves the immutable leg is ORPHANED (MiOS-Cat.bat stages only mutable Fedora), and gives a sequenced plan + drop-in staging bridge / loopback-from-SSOT template / first-boot MOK-UKI enrollment to make one USB offline-install REAL MiOS (bootc/ostree) in every format. -->
<!-- AI-related: field/loopback.cfg, tools/install.sh, https://github.com/mios-dev/mios-bootstrap/blob/main/installation/MiOS-Field.bat, usr/libexec/mios/mios-stage-oci-archive, usr/libexec/mios/mios-build-driver, usr/share/mios/ventoy/ventoy.json, usr/share/mios/ventoy/mios-kickstart.cfg, config/artifacts/bib.toml, config/artifacts/iso.toml, config/artifacts/wsl2.toml, src/mios-rs/mios-build/src/artifacts.rs, Justfile, automation/98-drift-checks.sh, usr/share/mios/mios.toml ([deployment] [deploy.artifacts] [field]), usr/share/doc/mios/adr/0008-mios-cat-unified-entry-and-minification.md, automation/76-uki-render.sh -->

# MiOS DEPLOY-Plane Audit — Offline Immutable Install

**Date:** 2026-07-31 · **Scope:** Deploy plane (build -> artifact -> USB -> target OS).
**Verdict:** Multi-format artifact generation (OCI archive, ISO, QCOW2, VHDX, WSL2) is functional in `Justfile` and `usr/libexec/mios/mios-build-driver`. The delivery bridge via Ventoy and `tools/install.sh` (`bootc install to-disk --transport oci-archive`) requires complete offline staging coordination.

## 1. Verified Architecture & Drift Gates
- Multi-format build: `Justfile` recipes (`oci-archive`, `raw`, `iso`, `qcow2`, `vhdx`, `wsl2`).
- Offline zero-network bootc installer: `tools/install.sh` (`MIOS_INSTALLER_ROLE=bootc-baremetal-disk-installer`).
- Drift Gates: 81 (oci-archive path match), 83 (kickstart bash syntax), 84 (BIB rootfs label), 85 (zero network tokens in install.sh), 86 (installer role uniqueness), 88 (repo partition label match).
