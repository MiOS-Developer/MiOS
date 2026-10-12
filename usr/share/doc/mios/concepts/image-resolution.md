# AI-hint: System concepts documentation for the MiOS Image Registry and Name Resolution Architecture.
# MiOS Image Name and Registry Resolution (WS-RELTOP)

## 1. Executive Summary

This document defines the authoritative architecture for resolving OCI image references and registries across all MiOS host environments (Windows, Linux, Xbox). Under the Single Source of Truth (SSOT) design, OCI image name resolution must behave deterministically on fresh, credential-free machines without leaking local development references (e.g. `localhost/mios`) into network pull requests.

## 2. Environment-Specific Resolution Paths

The OCI image reference resolves based on the specific target platform, optimized for local speed on development hosts and air-gapped security on edge devices.

### Windows (Local Build & Consume)
- The Windows build driver (`mios-build-driver`) builds the container locally as `localhost/mios:latest` directly in the local Podman registry.
- Windows environments consume the image strictly from local storage, eliminating unnecessary registry pushes/pulls.
- Local loopbacks and development references remain strictly local and do not leak to external paths.

### Linux (GHCR Deployments)
- A fresh Linux installation relies on a public package registry path for host updates.
- Linux hosts execute `bootc switch` targeting the authoritative upstream image: `ghcr.io/mios-dev/mios:latest`.
- **Registry Public Access Requirement**: To support credential-free `bootc switch` operations on clean Linux installs, the container image package `ghcr.io/mios-dev/mios` must be configured as a **public** repository on GitHub Packages (GHCR).

### Xbox (Offline Provisioning)
- Xbox environments provision fully offline in air-gapped environments.
- Deployment packages are bundled and delivered as a self-contained, pre-baked `oci-archive` (tarball format).
- No network pull is executed during install or update.

## 3. Separation of Credentials and Resolution (task_1121)

Historically, image resolution was coupled to credentials defined in `globals.sh` and `globals.ps1`. This coupling led to config clobbering issues where local credentials would overwrite or fallback incorrectly.

- The credential fallback logic in `globals.sh` and `globals.ps1` is **deprecated and inactive** on all fresh execution paths.
- Canonical entry points do not source the legacy `globals` files for image name resolution.
- Registry hosts and image tags are resolved directly from `mios.toml` `[image]` SSOT properties, ensuring clean, unified configuration.
- Cross-reference: [task_1121 Globals Clobber Fix](file:///C:/MiOS/docs/design/mios-finalization-plan.md).
