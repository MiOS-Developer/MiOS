<!-- AI-hint: ADR-0031 (proposed) decides how non-hardware MiOS services move to k3s across 2-6 Blades with failover, and how each gets a tailnet MagicDNS name; the reasoning, inventory and open questions are in docs/design/doc-k3s-fleet-conversion.md. -->
<!-- AI-related: docs/design/doc-k3s-fleet-conversion.md, usr/share/doc/mios/adr/0016-blade-node-topology.md, usr/share/doc/mios/adr/0017-blade-workload-mobility.md, usr/share/mios/mios.toml, tools/generate-k3s-manifests.sh -->
---
adr: 0031
title: Non-hardware services run on k3s across the Blades, named by Tailscale Services
status: proposed
date: 2026-11-20
deciders: [operator, ai-pair]
tags: [k3s, failover, tailscale, magicdns, ssot-projection]
laws: [3, 6, 7, 8, 11, 12, 14, 16]
ssot_keys: [blade.cluster, blade.placement, blade.planes, tailscale, ports.k3s_api]
related_ws: [WS-BLADE]
supersedes: []
superseded_by: []
---

# ADR-0031: Non-hardware services run on k3s across the Blades, named by Tailscale Services

## Status

proposed — 2026-11-20. An agent never marks this accepted; the operator does. Nothing is built until it is.

## Context

The operator wants every service hosted as k3s across all Blades with failover, each with a MagicDNS name reachable from every tailnet device. Today (tree): services are Quadlets or host venv units, `generate-k3s-manifests.sh` emits inert bare Pods from live podman state, k3s is a single-node sqlite install in a privileged container, and tailnet names are per-node `tailscale serve` routes. ADR-0016 §15.2 places three etcd members on one Blade. The Blade owns radio, mesh, storage, GPU/VFIO and the boot chain, and a guest must not be able to claim them. Measurements from a live Blade (reported, not re-measured): 13.9 GB RAM and a USB 2.0 NVMe root with repeated freezes. Cloud sessions cannot join the tailnet. Full evidence and open questions: `docs/design/doc-k3s-fleet-conversion.md`.

## Decision

1. **Classes.** Each service is K (k3s, guest-plane), B (per-Blade, hardware-bound) or S (singleton with election). Ceph, adguard, tailscaled, headscale, pxe-hub, ssh-ca, the GPU lanes (fronted by a k3s Service at first) and the host consoles are not K.
2. **Cluster.** k3s embedded etcd, three servers on three different Blades with fast storage, remaining Blades as agents; with one or two Blades, one server. Two servers are never run. Storage quality and RAM are node labels and scheduling constraints; a node on USB storage is an agent only. Cloud nodes are not members.
3. **Failover.** Stateless: Deployments, at least two replicas where nodes allow, spread, PDBs, shortened `tolerationSeconds`. Stateful: SSOT records `data = replicate|pin` per service; pgvector and forge start as pin plus backup. Inference: a per-node extended resource `mios.dev/granite-slot` makes two lanes on one small node unschedulable; memory comes from SSOT budgets.
4. **Names.** Tailscale Services (`svc:<name>`) published by the Tailscale Kubernetes operator's HA ProxyGroup for K-class services; per-node `tailscale serve` only for B-class; one publisher per name. This depends on Tailscale SaaS (headscale lacks Services support in what was verified).
5. **Generation.** One `[k3s.workloads.<name>]` SSOT group plus `[containers.<name>]` feed both the Quadlet and the manifests; a Rust `mios-gen k3s` replaces `generate-k3s-manifests.*`; one `k3s-manifest` template; a regenerate-and-diff drift gate; secrets never in manifests.
6. **Images.** The bound-images list also feeds air-gap tarballs for the infrastructure set and the k3s embedded registry mirror for the rest.
7. **Migration.** Stateless first, Quadlet kept disabled until the per-service failover proof passes; a name is live on exactly one side per node.

## Rationale

Embedded etcd is k3s-native and matches ADR-0016's "native pattern" rule; two servers add no fault tolerance. Services give a node-independent name, which per-node serve cannot. Application-level replication avoids a distributed block layer on small Blades; Longhorn duplicates the Ceph plane and is too heavy, and Rook-Ceph would host storage inside the guest plane. Rejected: Flux from the local Forgejo (circular), `podman kube generate` (non-deterministic, embeds secrets), moving headscale into k3s (circular).

## Consequences

Large hidden work: the venv services need OCI images. ADR-0016 §15.2 is narrowed if accepted. The name catalogue may exceed a Services cap on some plans. Several vendor facts are unverified (see the design doc, section 10) and must be checked before building. Done when: every K-class service runs on k3s with a tailnet name, losing one node keeps stateless services up, manifests are generated with green drift, and no service runs as both Quadlet and k8s workload on one node.
