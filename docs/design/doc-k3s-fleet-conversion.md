<!-- AI-hint: Design (docs only, nothing implemented) for converting every non-hardware MiOS service to k3s across 2-6 Blades with failover, and publishing each as a MagicDNS-named service on the tailnet. Companion to ADR-0031. -->
<!-- AI-related: usr/share/doc/mios/adr/0031-k3s-fleet-conversion-and-tailnet-service-names.md, usr/share/doc/mios/adr/0016-blade-node-topology.md, usr/share/doc/mios/adr/0017-blade-workload-mobility.md, usr/share/mios/mios.toml, docs/design/doc-mios-metal-blade.md, automation/36-ceph-k3s.sh, tools/generate-k3s-manifests.sh -->
# Design: every MiOS service on k3s, with failover and tailnet names

Status: **draft for operator approval. Nothing here is implemented.** Building waits for approval of ADR-0031 and for the image build (PR #70) to go green.

Operator goal (as recorded by the coordinator): conversion and hosting of services as k3s across all Blades/Nodes with failover; all hosted with MagicDNS names across the Tailscale network for all devices; any service can be hosted on any Blade/Node that can serve it.

How to read the evidence: **[tree]** = read in this repository at HEAD; **[reported]** = measured on live Blades by the coordinator, not re-measured here; **[vendor-unverified]** = from a search summary because the vendor page could not be fetched (every tailscale.com page returned HTTP 403 to the fetcher); **[inference]** = my reasoning.

## 0. Findings that shape the design

1. **Most of the AI plane is not a container today.** [tree] `mios-agent-pipe`, `hermes-worker`, `mios-policy-arbiter`, `mios-mcp`, `mios-opencode-gateway`, `mios-agents` and `mios-daemon` are host systemd units running a venv (`/usr/lib/mios/agents/.venv/bin/python3 ... server.py`). They have no OCI image. Moving them needs an image first; that is the largest hidden piece of work.
2. **The k3s that exists is a different shape from the one wanted.** [tree] `mios-k3s.container` runs `k3s server` as a privileged root podman container in `mios-system.pod`, with a sqlite single-node `usr/lib/rancher/k3s/config.yaml`; `automation/36-ceph-k3s.sh` also installs a host `k3s` binary, and `mios-k3s-master.target` requires a host `k3s.service`. Two launch paths exist for one role. [reported] The PS5 runs the container form with the API on 6443 while SSOT `[ports].k3s_api` says 8450.
3. **The generated manifests cannot fail anything over.** [tree] `tools/generate-k3s-manifests.sh` runs `podman kube generate` against live pods, so it emits bare `Pod` objects (no Deployment, no replicas), carries per-run SandboxIDs, and the committed `usr/share/mios/k3s-manifests/mios-ai.yaml` embeds resolved env values that look like tokens. It violates Law 8 (hand-run, non-deterministic) and Law 11. It should be retired, not extended, and the embedded values rotated.
4. **ADR-0016 §15.2 and this goal pull against each other.** It runs three k3s servers as three logical hosts on one Blade so quorum "works on one box". That survives a process crash, not a Blade loss, and puts three etcd WALs on one disk. Fleet HA needs servers on different Blades. This design asks to narrow §15.2 (Q3 below).
5. **Names are already partly designed.** [tree] `[tailscale.serve.routes.*]` publishes per-node `tailscale serve` routes (all disabled by default) via `mios-tailscale-sync`. [reported] A 23-name `[tailscale.services.*]` catalogue exists on branch `mios-tailscale-services` (985978fdb). That ref is not on origin and the commit is not in this clone, so **I could not read it**; section 4 therefore defines the naming mechanism and leaves the catalogue as the future name source.

## 1. Inventory and classification

Sources: `[containers.*]` (28 Quadlets in three pods: `mios-system`, `mios-webtools`, `mios-ai`), `[units.*]` (about 110 native units), `[ports]`, `[blade.planes.*]` (owner=mini for hypervisor, radio, router, mesh, storage, ha; either for orchestrator and ai) and `[blade.requires]`. Three classes:

- **K** = moves to k3s (guest-plane).
- **B** = stays per-Blade on the metal ("the Blade owns the hardware"), unclaimable by a guest archetype.
- **S** = singleton across Blades: exactly one live holder, failover by election, not by replicas.

This table is by service family. The exhaustive per-unit list must be generated, not hand-kept (task A in section 8). Rows marked † I classified from names and unit descriptions without reading each unit; verify when generated.

| Family (members) | Class | Why |
|---|---|---|
| `searxng`, `crawl4ai`, `firecrawl-api`, `firecrawl-worker`, `webtools-redis`, `otelcol`, `piper`, `whisper`†, `open-webui`, `code-server`, `agent-share`, `mcp`†, `opencode-gateway`† | K, stateless (redis, open-webui and code-server hold small PVC state) | No device ownership. Loopback-bound today, so they need Service objects instead of 127.0.0.1. |
| `agent-pipe` (router), `prefilter`, `policy-arbiter`, `model-router`, `daemon-agent`, `agents`† | K, stateless, after an image exists | Pure request plumbing over the OpenAI `/v1` contract. Needs the venv packaged as an OCI image (finding 1). |
| `hermes-worker` (gateway, sessions, tool loop) | K, but split | The gateway can move; `mios-hermes-browser(-worker)` drives a desktop browser via CDP on 9222/9223 and stays B (display/GPU session). |
| `pgvector` | K, stateful (or S while pinned) | Single-writer PostgreSQL. See section 3 for replicate-or-pin. |
| `forge` (Forgejo) | K, stateful | One writer, a data dir. `forgejo-runner` is **B**: it needs DinD/`--privileged`. |
| `llm-light`, `cpu-node` | K, node-affine inference | Moves, but only onto nodes that hold the model files and have RAM headroom (section 3.3). |
| `llm-heavy`, `llm-worker@`, igpu lanes | B first, fronted by a k3s Service; K later | GPU/VFIO/CDI ownership is the Blade's. The pod would need device passthrough from a guest that must not own it (CLAUDE.md invariant; `[blade.requires]` gpu-serving). |
| `ceph`, `radosgw`, ceph-bootstrap | B | Storage plane owner=mini: OSDs/MONs own block devices. k3s consumes Ceph; it does not host it. |
| `headscale` | S, outside k3s | It is the control plane of the mesh k3s runs on; hosting it in k3s is circular. Currently off ([reported]). |
| `adguard` (DNS :53, tailnet nameserver) | B, one per Blade | DNS must answer before the cluster is healthy. Config is SSOT-rendered and stateless, so per-Blade copies need no election. |
| `pxe-hub` (DHCP/TFTP, privileged L2) | S | One router-core holder per uplink (task_931). |
| `ssh-ca` signer | S | CA key lives on one designated Blade, never a seat (`[blade.requires]`). |
| `tailscaled`, `mios-tailscale-sync`, `mios-node` (UDP mesh daemon), `enforcer` (Tetragon), `thermald`, `powerd`, `power-sampler`, `ventulusd`, `fhs-sync`, `wol-proxy`, `attest` | B | Hardware, mesh membership or host-kernel enforcement. |
| `mios-k3s` itself | B | It is the substrate. |
| `guacamole`, `guacd`, `xrdp`/RDP, `sunshine`, `cockpit`, `ttyd-*`, `code-server` host terminal paths | B (guacamole/guacd could move; low value) | Desktop sessions and host consoles reach host sockets and displays. |
| Timers/firstboot/GC units (`*-firstboot`, `podman-gc`, `pgvector-backup`, ...) | B, with k8s CronJob equivalents only where the workload moved | They act on the Blade's own `/var`. |

## 2. Cluster shape

Operator decisions folded in (relayed by the coordinator): one server now and more later; **mios-ps5 and the X570/MiOS-DEV are deliberately cluster members** to exercise failure handling; **tailnet-only** mesh; **cloud nodes are first-class**; the control plane stays on **Tailscale SaaS** (headscale stays off).

**Control plane and quorum.** k3s embedded etcd needs an **odd** number of servers, 3 or more, first server `--cluster-init`, quorum floor(n/2)+1. [vendor: docs.k3s.io/datastore/ha-embedded] Consequences for the path the operator described:

| Servers | Fault tolerance | Notes |
|---|---|---|
| 1 (now) | none | Start it with `--cluster-init` so it can grow (sqlite-to-etcd in-place conversion is **unverified**; if it is not supported, the first server is rebuilt once). If it dies, running pods keep running; nothing reschedules and the API is down. Snapshots (default every 12 h, keep 5) must be copied to another Blade. |
| 2 | **none, and worse than 1**: both must be up to write, so there are two failure points | Not a target state. The "second, more powerful Blade" joins as a **server only as a transient step toward 3**, or as an agent. |
| 3 | one server may fail | The first state with real control-plane fault tolerance. |
| 5 | two may fail | Optional with 6 Blades. |

**Roles per node until storage is proven.** Eligibility to be an etcd server is a *measured gate*, not a label: p99 WAL fsync under 10 ms and the k3s baseline of 50 IOPS under 5 ms latency [vendor: docs.k3s.io/reference/resource-profiling; etcd FAQ via search summary]. A node that fails the gate is an **agent**.

| Node | Role now | Server later |
|---|---|---|
| mios-ps5 [reported: USB 2.0 NVMe, four freezes, 13.9 GB, CPU only] | **Agent** (member from day one). It may run the single server only for an explicit trial **on a datastore placed off the USB device**, with the snapshot copy above; if it freezes, that is the exercise, and rollback is "agent again" | Only after its storage passes the gate |
| X570 / MiOS-DEV (WSL2) | **Agent** member (lifting `ConditionVirtualization=!wsl` for the agent role, see below) | Server candidate if the fsync gate passes on its virtual disk; Windows sleep and reboot make it a poor sole server |
| "More powerful Blade" (when it rejoins) | **First server** if it passes the gate; otherwise it joins as an agent and the gate decides | Yes |
| Cloud nodes | Agent (latency, section 2 below) | No, until a low-latency placement exists |

**Path to 3+ servers:** (1) one server on the best-gated node; (2) the 2nd and 3rd qualifying nodes join as servers *together in one change window* (never settle at 2); (3) each promotion is a failure drill (kill the old server, confirm the API survives) before the next step. The PS5 and X570 become servers only if and when they pass the gate; until then they harden failure handling as agents, which still exercises node loss, eviction and re-placement.

**X570 / MiOS-DEV under WSL2: caveats** (all [inference] or general WSL2 knowledge; the k3s pages I read say nothing about WSL2, and none of this was tested here):
- Needs systemd in the distro and cgroup v2; the WSL2 kernel must provide WireGuard and the netfilter pieces flannel and kube-proxy use.
- SELinux is not available: `selinux: true` in `usr/lib/rancher/k3s/config.yaml` must be false for this node.
- The WSL2 VM stops when idle or when Windows sleeps/hibernates, which looks to the cluster like a node loss; the clock can jump on resume.
- Memory is capped by the Windows-side WSL config, not by the host's RAM; set it from SSOT.
- Storage is an ext4 VHDX behind the Windows file cache: fsync durability and latency are unlike bare NVMe, hence the gate.
- Networking: the node's tailnet identity must be one Tailscale instance (the Windows client or the distro's `tailscaled`, not both advertising it).
- GPU access is via the WSL GPU device, not the Blade's CDI/VFIO path; GPU lanes stay out of k3s there (section 3.3).
- MiOS-DEV is also the build host (`podman machine`, all builds run inside it): k3s workloads must be capped so they cannot starve builds.

**Cloud nodes are first-class, not excluded.** Per the operator: a cloud host is either a **Blade** (hardware that hosts nested MiOS images and nodes) or an **individual full MiOS image** (container, VM, ISO, flashed USB) hosted on a Blade. The design therefore classifies nodes by capability (storage grade, RAM, latency, GPU), never by origin. **Current constraint, an environment blocker and not a design exclusion:** [reported] cloud *sessions* cannot join the tailnet because the egress proxy refuses Tailscale's HTTP upgrade, whereas a cloud VM that has its own network (for example the FCOS node from PR #72, [reported], not read) can join. Until a node can reach the tailnet it is not a member. High cloud-to-home latency makes a cloud node an **agent**, because etcd over WAN is unsupported (doc-mios-metal-blade.md §2.a).

**Network (decided: tailnet only).** Node IPs are the node's tailnet IPv4; `--node-ip <tailscale0 IPv4>` and `--flannel-iface tailscale0` with `--flannel-backend=wireguard-native` [vendor: docs.k3s.io/networking/distributed-multicloud documents wireguard-native with `--node-external-ip`/`--flannel-external-ip`; this exact combination is **unverified**]. It runs WireGuard inside the tailnet's WireGuard: budget the MTU (tailscale0 is 1280 by default, so pod MTU is lower) and CPU on the PS5. The default CIDRs 10.42/16 and 10.43/16 do not overlap tailnet 100.64/10. `--vpn-auth` stays off (experimental, and a second Tailscale instance per node).

**Relation to the other lanes.** Ceph stays a Blade-plane service (`36-ceph-k3s.sh`, `[ports].ceph_dashboard`); k3s consumes it through ceph-csi only when Ceph is real (task_933). Pacemaker keeps VMs (ADR-0017); no VM moves. `[ports].k3s_api=8450` stays the one API port; the PS5's 6443 is drift to remove.

## 3. Failover by service class

Default k8s timing: node-monitor-grace-period 50 s, and pods on a lost node get `tolerationSeconds=300`, so worst-case reschedule is about 6 minutes. [vendor: kubernetes.io kube-controller-manager reference and taints page] Stateless classes should set `tolerationSeconds` to 30-60 s.

### 3.1 Stateless
Deployment, `replicas >= 2` (1 on a 1-2 Blade fleet), `topologySpreadConstraints` by node, PodDisruptionBudget `minAvailable: 1`. Failover = another replica already serving; the name never moves. Exact meaning of "survives": losing one node leaves traffic served within seconds; it does not mean in-flight requests survive.

### 3.2 Stateful
Each service records **`data = "replicate" | "pin"`** in SSOT (the coordinator's "replicate-or-pin").
- **pgvector** (operator decision: pin plus backups first): **pin** to one node (StatefulSet, one replica, local PV, label-selected) with the nightly `pg_dump` timer (task_3236) **writing to a different Blade**. Failover then means *scripted restore on another node*, not automatic. Replicate once 3 good nodes exist, via **replicate** with PostgreSQL-level replication (an operator such as CloudNativePG: not researched here, [vendor-unverified]) with local PVs, so replication happens above the disk and needs no distributed block storage.
- **forge**: same decision: pin to one node, scheduled backups to another Blade, replicate once 3 good nodes exist.
- **Model stores**: GGUFs are baked into the image (Law 12) at `/usr/share/mios/llamacpp/models`, so every Blade already has them; pods mount them `hostPath` read-only. Nothing to replicate; scheduling just requires the node holds the model.
- **Storage layer choice.** *Not* Longhorn: it duplicates the Ceph plane and its documented per-node footprint (4 vCPU / 4 GiB, minimum 3 nodes; longhorn.io best practices v1.13.0) does not fit a 13.9 GB Blade. *Rook-Ceph* hosts Ceph inside k3s, which contradicts the Blade owning storage. Recommended: **local-path (bundled) now; ceph-csi RBD against the Blade-owned Ceph when task_933 delivers a real multi-MON Ceph with OSDs on SSD**.

### 3.3 GPU and inference lanes
[reported] On the PS5, two lanes loaded the same 8B granite and pushed a 13.9 GB host into swap on USB. Mechanisms to make that impossible by construction:
- **A node advertises extended resource `mios.dev/granite-slot: N`** (N from SSOT per node from `[nodes.*]` and `cpu_node_memory_max`). Each lane Pod requests `1`. The scheduler then cannot place two granite lanes on a node with N=1. This is also what lets `llm-light` and `cpu-node` coexist on large nodes and collapse to one on small ones.
- Pod `resources.requests/limits.memory` come from SSOT lane budgets (Law 7), not literals; limits keep a runaway lane from taking the node.
- Capability labels from `[blade.archetypes]` (`gpu-serving`, `service-plane`) become node labels; selectors reuse `[blade.requires]` (already mapped by `usr/share/mios/dropins/k3s-node-selectors.yaml` per the coordinator; I did not read it).
- Failover for inference = another node with a free slot, or for GPU lanes the existing CPU fallback (`[blade.cpu_fallbacks]`: heavy -> llm-light, same `/v1` contract). A GPU lane stays a per-Blade Quadlet fronted by a headless Service plus EndpointSlice until the GPU can be given to a k3s node without the guest owning hardware.
- Storage tier: no etcd, no swap, no PVs on `usb`-labelled nodes.

## 4. Naming: a MagicDNS name per service

Goal: `https://<name>.<tailnet>.ts.net` works from every tailnet device regardless of which Blade runs the pod. [Tailnet suffix `taildd86d0.ts.net` is from the coordinator, [reported].]

| Option | What it is | Fit |
|---|---|---|
| **A. Tailscale Services (`svc:<name>`)** | A named VIP; hosts advertise it with `tailscale serve --service=svc:name`, an admin (or an auto-approver on a tag) approves each host, grants name `svc:name` as destination; several hosts can advertise one service and clients are routed to an available one. Needs tag-based host identity. GA 2026-02-19; up to 10 services on every plan including Personal. | Best fit for failover: the name is independent of the node. **Limit risk: the catalogue has 23 names vs a documented 10-service cap on at least the Personal plan** (Q7). [vendor-unverified; https://tailscale.com/kb/1552/tailscale-services, https://tailscale.com/blog/services-ga] |
| **B. Tailscale Kubernetes operator** | In-cluster controller creating proxies per Service/Ingress. HA mode: a `ProxyGroup` (type ingress) runs replicas that all advertise one Tailscale Service per exposed k8s Service/Ingress, opted in by the `tailscale.com/proxy-group` annotation; tags default `tag:k8s`, overridable with `tailscale.com/tags`; needs operator >= 1.84; OAuth client with Services, Devices Core and Auth Keys write scopes. [vendor-unverified; https://tailscale.com/docs/kubernetes-operator/concepts/proxygroup] | The natural bridge: k8s Service -> Tailscale Service. Non-HA mode makes one tailnet device per exposed object (many devices, name tied to a proxy pod). |
| **C. Per-node `tailscale serve`** (what `mios-tailscale-sync` does today) | Publishes a route on one node's name. | Name is the node's, so it dies with the node. Correct for B-class (Blade-bound) services only. Serve's port/name limits **not verified**. |

**Decision (operator): stay on Tailscale SaaS and use B publishing A.** K-class services are exposed through ProxyGroup HA Ingress/Services; C is only for B-class services; exactly one publisher per name (a gate).

**Names collapse to at most 10 Tailscale Services**, grouped behind function-named front doors with paths or ports underneath, so the catalogue fits the documented cap (an unverified figure, see section 10). Proposed shape, to be reconciled with the 23-name catalogue once it is readable:

| Front door (`svc:`) | Carries (path or port under it) |
|---|---|
| `mios` | portal, agent-pipe `/v1`, MCP/A2A, hermes gateway, dashboard (path-routed) |
| `mios-chat` | open-webui |
| `mios-search` | searxng, crawl4ai, firecrawl |
| `mios-speech` | whisper, piper |
| `mios-forge` | forgejo HTTP, plus TCP 8410 for git+ssh |
| `mios-code` | code-server |
| `mios-data` | pgvector (TCP, pinned; auth at the database) |
| `mios-obs` | otelcol UI |
| `mios-share` | agent-share |
| `mios-infer` | per-lane `/v1` endpoints (light, cpu, heavy) |

That is 10; any addition needs a grouping, not a new name. Authentication stays at the application, because a tailnet grant is network access, not login.

**Headscale (stays off).** [vendor: github.com/juanfont/headscale CHANGELOG, fetched] Latest release 0.29.7 (2026-10-30). Its features page lists tags, grants and serve; I found no doc or changelog entry for Tailscale Services; third-party notes say unsupported. 0.30.0 (unreleased in the changelog I read) adds OAuth client credentials and the v2 API, described as letting the Kubernetes operator drive Headscale. **So A and B require Tailscale SaaS**, which the operator confirmed. Headscale remains a possible future fallback only (per-node serve plus AdGuard records, no automatic name failover).

**ACL and tags.** [vendor-unverified] Service hosts must be tagged; the existing trust tag is `tag:mios` ([tree] `trusted_tags`). Proposal: host Blades keep `tag:mios`; proxies get `tag:mios-svc` owned by the operator's OAuth client via `tagOwners`; a grant gives `autogroup:member` (or named groups) access to `svc:*`, with `auth_probe_paths`-style authentication staying at the application (Portal `require_auth`), since a tailnet grant is network access, not login. Exact `tagOwners`/`autoApprovers` syntax must be taken from the live policy before building.

**Fit with SSOT.** `[tailscale.services.<name>]` (from the unmerged branch) is the name catalogue: name, port, auth, `placement = "k3s"|"blade"`. `mios-tailscale-sync` keeps running on Blades but publishes only `placement = "blade"` names; k3s workloads are published by the operator from manifests rendered from the same table.

## 5. SSOT, laws and projection

- **One workload spec, two projections** (Laws 7, 8). New SSOT group `[k3s.workloads.<name>]` carries what a Quadlet cannot say: `class` (stateless|stateful|inference), `replicas`, `data`, `storage_class`, `node_selector`, memory/slot budgets, `expose` (name, port, auth), `placement` (`k3s`|`blade`). Image, env, volumes, user and ports continue to come from `[containers.<name>]`, so the Quadlet and the k8s object are generated from the same fields and cannot diverge. `placement` selects which projection is live; a gate fails if one name is live as both on one node.
- **Generator** (Law 14): a Rust `mios-gen k3s` subcommand in `tools/native/mios-gen` emits Deployment, StatefulSet, Service, PDB, PVC, ConfigMap, NetworkPolicy and Tailscale exposure objects deterministically (no SandboxIDs, no resolved secrets). `generate-k3s-manifests.{sh,ps1}` are retired.
- **One template per type** (Law 16): add a `k3s-manifest` template type under `usr/share/mios/templates/`, declared in `[templates.k3s-manifest]`, scaffolded by `mios new`.
- **Drift gate** (Law 8): regenerate-and-diff in `automation/98-drift-checks.sh`; `97-ssot-lint` extended so every `${MIOS_*}` used in a manifest is emitted (Law 9); a check that `[k3s.workloads.*]` names exist in `[containers.*]`/`[units.*]`.
- **Secrets** (Law 11): manifests reference k8s Secrets created at first boot from `/etc/mios/secrets.env`; no value in any manifest.
- **Law 6**: k3s keeps its documented root exception (`mios-k3s.container`); workload pods run non-root (`runAsUser` from `[services.*].uid`), Quadlet `Delegate=yes` is irrelevant inside k3s.
- **Law 3 and image distribution.** Bound images live in podman's store; k3s uses containerd's. Recommended: the bound-images list is the single list; at bake it also emits air-gap tarballs for the **infrastructure set only** (k3s system images, operator, CoreDNS) into `/var/lib/rancher/k3s/agent/images/` (imported on every start, per docs.k3s.io/installation/airgap), and the rest comes through the **embedded registry mirror (Spegel)**, whose k3s flag is documented as experimental and needs TCP 5001 and 6443 between nodes (docs.k3s.io/installation/registry-mirror). Law 12 holds because nothing is fetched at boot. Disk cost of two stores is Q8.
- **Desired state.** Recommended: image-baked manifests applied by k3s auto-deploy, so every server converges on its image version. Skew during a `bootc upgrade` of different Blades means servers briefly disagree; apply order is "one Blade at a time". Flux from the local Forgejo is **rejected for v1** because Forgejo would itself run on the cluster it configures.

## 6. Migration and rollback

Prerequisites (before any move): PR #70 green; operator approves ADR-0031; PS5 and X570 join as agents (server only after the fsync gate); `k3s_api` port drift fixed; one k3s launch path chosen; `!wsl` lifted for the agent role; token-looking values in committed manifests rotated.

| Phase | Move | Rollback |
|---|---|---|
| 0 | Real multi-server k3s on 3 SSD Blades (or 1 server + agents), the operator and ProxyGroup, with **no workloads** | Stop `k3s`; Quadlets untouched |
| 1 | searxng, piper, whisper, otelcol, crawl4ai, firecrawl, redis, agent-share | Flip `placement` back to `blade`; Quadlet re-enables, k8s object deleted |
| 2 | Package the venv services as images (agent-pipe, prefilter, arbiter, mcp, opencode-gateway, daemon-agent) and move them | Same flip |
| 3 | llm-light, cpu-node with slot resources; GPU lanes get a Service front only | Same flip; slot resource removed |
| 4 | open-webui, code-server (PVC) | Same flip; PVC data copied back |
| 5 | forge, then pgvector (pin first, replicate later) | Restore from the pre-move dump; keep the Quadlet's data dir untouched until proven |
| 6 | hermes gateway (after the venv fix, GWY-08 context) | Same flip |

**Coexistence rule:** a name is live on exactly one side per node; the gate enforces it. The Quadlet stays installed but disabled until the failover proof for that service passes, then is removed in a later change. **Failover proof** per service: delete pod; cordon and drain a node; measure name recovery from a second tailnet device; for stateful, restore matches the SSOT data choice. **Power-offs only with the operator.**

## 7. Exit criteria
Every K-class service runs on k3s and answers at `https://<name>.<tailnet>.ts.net` from every tailnet device; losing any single node keeps stateless services up; all manifests are generated with green drift; no service is live as both Quadlet and k8s workload on one node.

## 8. Proposed tasks
A: `mios-gen k3s` projection and template (Law 8/14/16). B: package venv services as images. C: cluster bring-up and membership SSOT (`[k3s.cluster]`). D: tailnet naming (operator, ProxyGroup, ACL). E: stateful strategy and ceph-csi. F: retire `generate-k3s-manifests.*` and scrub committed manifests. Added to `tasks.jsonl` as pending.

## 9. Decisions and open questions

Decided by the operator (relayed by the coordinator): (1) Tailscale SaaS, ProxyGroup HA, headscale off; (2) at most 10 front-door names; (4) tailnet-only mesh with wireguard-native; (5) one server now, grow as a more powerful Blade rejoins, PS5 and X570 included as members; (6) X570/MiOS-DEV in, `!wsl` lifted; (7) pgvector and forge pinned with backups to another Blade first; (9) the +2-file ratchet cost is **not** offset in this PR (standing drain policy, AGENTS.md); (10) cloud nodes are first-class; (11) push approved, PR stays a draft.

Also decided (question UI): **first server** = the best fsync-gated node, PS5 and X570 stay agents until they pass; **fsync gate** = p99 WAL fsync under 10 ms; **image stores** = air-gap tarballs for the infrastructure set plus the embedded registry mirror (Spegel), accepting two stores.

Decided later (question UI, 2026-11-26): **ADR-0016 §15.2 is narrowed**: etcd members sit on different physical Blades, because VMs on one Blade share that Blade's fate; a VM-hosted k3s server is a Pacemaker-managed resource whose failover must finish inside etcd's election timeout or it counts as a lost member. The **10 front-door grouping in section 4 stands as proposed.** ADR-0031 itself stays `proposed` until the operator accepts it.

Nothing else is open at the design level; remaining work is verification of the vendor claims in section 10 and the tasks K3S-01..06.

## 10. Unverified
Every tailscale.com page (403, including the 10-service cap): Services limits and naming, operator versions and `tagOwners`, `tailscale serve` limits. Headscale 0.30.0 status. Rook and CloudNativePG. k3s sqlite-to-etcd conversion and 1-of-3 behaviour. WSL2 specifics (section 2 caveats are inference and untested); PR #72's FCOS node (not read). The 23-name catalogue branch. All live-Blade measurements (reported, not re-measured). `k3s-node-selectors.yaml` and manual ch23 (not read). k3s version at time of writing: v1.37.2+k3s1 (GitHub release 3 Nov), docs pages dated 2026-11-19.
