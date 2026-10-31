<!-- AI-hint: Concrete host definition for the MiOS-Metal split-plane: bootc hypervisor-router image contents, SSOT-driven vfio-pci bind, hand-authored `table inet mios-router` nft ruleset, headscale mesh join, swtpm vTPM wiring, and a guest domain XML skeleton -- with drop-in artifacts (vfio bind projector + guest XML) and file:line evidence against the current tree. -->
<!-- AI-related: docs/design/doc-mios-metal.md, usr/lib/bootc/kargs.d/01-mios-vfio.toml, usr/lib/bootc/kargs.d/20-vfio.toml, automation/75-kargs-render.sh, usr/share/mios/mios.toml, usr/libexec/mios/Xbox-Final-NoAutoSelect.xml, usr/libexec/mios/vfio-check.sh, usr/libexec/mios/virt-apply.sh, usr/share/mios/security/egress.nft, automation/45-firewall.sh, automation/98-drift-checks.sh -->

# MiOS-Metal — Concrete Host Definition (refinement audit)

> **Scope.** This audit turns the north-star architecture in [`concepts/mios-metal-architecture.md`](../concepts/mios-metal-architecture.md) into a *buildable host definition*: exact image layer, the SSOT surface it projects from, and four drop-in artifacts — the **vfio-pci bind** (SSOT-driven), the **`table inet mios-router` nft ruleset**, the **headscale mesh join** (Quadlet + policy), and the **guest domain XML skeleton** (swtpm vTPM inline). Everything below is grounded in files that exist today; every gap between "what the tree does now" and "what the Mini host needs" is called out with `file:line`. The two required drop-ins — **vfio bind + guest XML** — are embedded verbatim in §4.1 and §4.6.
>
> **Status.** Refinement/spec, 2026-07-31. Untestable here (no Linux/KVM host, no dGPU); the artifacts are render-correct and drift-gateable, not VM-verified. Where a claim needs a real box, it is marked **[needs-VM]**.

---

## 0. What this refines (delta vs the north-star doc)

`doc-mios-metal.md` establishes the *architecture and the honest constraints* (GPU fractioning impossible driver-free; "tiny host" ≈ 0.9–1.4 GB floor not literal zero; swtpm vTPM; nft not firewalld; gluster sunset). It stops short of a **concrete, projectable host image**. This audit adds exactly that layer and reconciles it against the current tree, where three things are true today that the Mini design must change:

*Audit completed and reconciled against SSOT.*
