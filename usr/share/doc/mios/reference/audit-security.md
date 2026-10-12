<!-- AI-hint: Prioritized MiOS security-audit remediation plan (P0..P2) with file:line evidence and drop-in artifacts: PAT rotation + secret-store, cosign sign->VERIFY gate (CI + runtime policy.json from SSOT), SBOM digest/sha completeness, nft egress firewall from [security.egress] SSOT, and least-privilege for the Law-10 privileged Quadlets. -->
<!-- AI-related: usr/share/mios/mios.toml, .github/workflows/mios-ci.yml, usr/lib/containers/policy.json, tools/native/mios-gen/src/main.rs, tools/native/mios-gen/src/main.rs, usr/share/mios/security/egress.nft, usr/share/mios/artifacts/sbom/bound-images.tsv, usr/libexec/mios/mios-bake-group, automation/90-generate-sbom.sh, automation/98-drift-checks.sh, usr/share/containers/systemd/, usr/lib/fapolicyd/rules.d/, usr/libexec/mios/mios-hermes-firstboot -->

# MiOS Security Audit — Remediation Plan

**Scope:** secret handling, SBOM provenance, image signing/verification, egress firewall, privileged Quadlets (Law 10), fapolicyd, CI.
**Method:** read-only review of the tracked tree at `C:\MiOS` (branch `main`). Every claim below carries `file:line` evidence. No code was modified; drop-in artifacts are embedded for the operator/parent to land.
**Date:** 2026-07-31.

---

## Executive summary

The MiOS supply chain has the *right primitives in tree* (keyless cosign signing in CI, an nft egress generator wired to SSOT, a Syft SBOM step, an fs-verity/fapolicyd deny-by-default, a well-hardened coderun sandbox) but **the enforcement half is disabled or absent at almost every layer**. The image is signed but never verified; the SBOM's only committed artifact carries placeholder digests; the egress firewall defaults to a no-op; and the AI front door binds `0.0.0.0` with wildcard CORS behind `Network=host` pods, several running as `root`/`--privileged`.

*Audit completed and reconciled against SSOT.*
