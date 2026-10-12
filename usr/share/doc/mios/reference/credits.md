<!-- AI-hint: Attribution registry documenting all upstream projects, dependencies, and components used in MiOS to provide legal and source-of-truth tracking for the system's foundational substrate.
     AI-related: /usr/share/mios/mios.toml, /usr/share/mios/ai/system.md, /usr/share/mios/ai/v1/models.json, /usr/share/mios/ai/v1/mcp.json, /etc/mios/install.env, mios-dev, mios-ai, mios-mcp, mios-forge, mios-ci -->
# usr/share/doc/mios/reference/credits.md

> Attribution registry for every upstream project, dependency, application,
> repository, file, and pattern that 'MiOS' is built on top of or refers to.
>
> **Project nature, recorded once for the file.** 'MiOS' (pronounced
> *MyOS* -- short for *My OS* / *My Operating System*) is a **research
> project**. It is *generative*: synthesized from a small set of seed
> scripts and manually-curated documentation, then iteratively expanded
> by automated tooling and human review. The names, licenses, and
> conventions of every upstream listed in this file are preserved
> verbatim; 'MiOS' claims no authorship of any upstream work and no
> affiliation with any upstream vendor. Runtime agreements are codified
> in [`AGREEMENTS.md`](../../../../../AGREEMENTS.md); invocation of any entry point
> listed there is treated as acknowledgment of this attribution
> registry, the Apache-2.0 main license at
> [`LICENSE`](../../../../../LICENSE), and the bundled-component licenses inventoried
> in [`usr/share/doc/mios/reference/licenses.md`](usr/share/doc/mios/reference/licenses.md).
>
> **Scope of this file:** *attribution* (what we use and where it came
> from). License terms live in `usr/share/doc/mios/reference/licenses.md`. Citation/source-of-truth
> tracking for the `usr/share/doc/mios` knowledge base lives in
> `usr/share/doc/mios/reference/sources.md`. Per-package RPM accounting lives in
> `usr/share/mios/PACKAGES.md`. Cryptographic SBOMs are emitted per build
> by `automation/90-generate-sbom.sh` (CycloneDX-JSON via syft) and
> attached as a cosign attestation to the OCI image.
>
> **Out of scope (for now):** personal/individual contributor credits.
> Those will be added when the project's contributor list is public.

---

## 1. Foundational substrate

| Project | Role in 'MiOS' | Upstream |
|---|---|---|
| Linux kernel | Bare-metal + virt + container kernel for every deployment shape | <https://www.kernel.org/> |
| systemd | PID 1, units, sysusers.d, tmpfiles.d, generators, journal, logind, networkd, resolved | <https://systemd.io/> -- <https://github.com/systemd/systemd> |
| dracut | initramfs generation (with composefs root-prep hooks) | <https://github.com/dracutdevs/dracut> |
| FHS 3.0 | Filesystem layout convention -- repo root **is** the deployed system root | <https://refspecs.linuxfoundation.org/FHS_3.0/> |
| Linux kernel parameters guide | kargs reference (`usr/lib/bootc/kargs.d/*.toml`) | <https://www.kernel.org/doc/html/latest/admin-guide/kernel-parameters.html> |
| Linux sysctl reference | sysctl tuning (`usr/lib/sysctl.d/*mios*.conf`) | <https://www.kernel.org/doc/Documentation/sysctl/> |

## 2. Image-mode / atomic substrate

| Project | Role | Upstream |
|---|---|---|
| bootc (CNCF Sandbox) | OS-as-OCI-image lifecycle: install, upgrade, switch, kargs, container lint | <https://github.com/bootc-dev/bootc> -- <https://bootc.dev/> |
| ostree / libostree | Content-addressed object store (current bootc backend) | <https://github.com/ostreedev/ostree> -- <https://ostreedev.github.io/ostree/> |
| composefs | EROFS + overlayfs + fs-verity verifiable read-only root (bootc migration target) | <https://github.com/containers/composefs> -- <https://github.com/composefs/composefs> |
| Fedora bootc base images | Fedora-side base for `quay.io/fedora/fedora-bootc:*` | <https://gitlab.com/fedora/bootc/base-images> |
| RHEL image mode (sibling reference) | bootc upstream consumer in enterprise context | <https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/using_image_mode_for_rhel_to_build_deploy_and_manage_operating_systems/index> |

## 3. Base image lineage

'MiOS' is built `FROM ghcr.io/ublue-os/ucore-hci:stable-nvidia` (overridable
via `MIOS_BASE_IMAGE`).

| Layer | Role | Upstream |
|---|---|---|
| Universal Blue (org) | Curated bootc image family on Fedora CoreOS / Fedora Atomic | <https://github.com/ublue-os> -- <https://universal-blue.org/> |
| ucore | CoreOS-style bootc image with batteries (multi-arch, ZFS) | <https://github.com/ublue-os/ucore> |
| **ucore-hci** | Hyperconverged-infrastructure variant (libvirt, KVM, QEMU, VFIO-PCI, virtiofs); the direct base | <https://github.com/ublue-os/ucore> (`ucore-hci` tags) |
| ccos | CentOS-style sibling | <https://github.com/ublue-os/ccos> |
| Bluefin / Aurora / Bazzite | Sibling Universal Blue images (developer / KDE / gaming) | <https://github.com/ublue-os/bluefin> -- <https://github.com/ublue-os/aurora> -- <https://github.com/ublue-os/bazzite> |
| Fedora Project | Underlying distro for `dnf5`, RPM packaging, Anaconda, kernel builds | <https://fedoraproject.org/> |
| Fedora hardening guide | Source for security-stack defaults | <https://docs.fedoraproject.org/en-US/quick-docs/securing-fedora/> |

## 4. Build / packaging / signing pipeline

| Tool | Role | Upstream |
|---|---|---|
| Containerfile | Single-stage main image build with `ctx` scratch context | OCI Image Spec: <https://github.com/opencontainers/image-spec> |
| Justfile (`just`) | Linux build orchestrator (`just build`, `just rechunk`, `just iso`, ...) | <https://github.com/casey/just> |
| Podman | Build runtime (machine on Windows; native on Linux) | <https://github.com/containers/podman> -- <https://docs.podman.io/> |
| Buildah | OCI image build primitive (Podman backend) | <https://github.com/containers/buildah> |
| Skopeo | Image inspection and registry plumbing | <https://github.com/containers/skopeo> |
| dnf5 | Package manager (`install_weak_deps=False` is the dnf5 spelling) | <https://github.com/rpm-software-management/dnf5> -- <https://dnf5.readthedocs.io/> |
| bootc-image-builder (BIB) | Renders OCI bootc image to `iso`, `qcow2`, `vhd`, `raw`, `wsl2`, etc.; configs in `config/artifacts/{bib,iso,qcow2,vhdx,wsl2}.toml` | <https://github.com/osbuild/bootc-image-builder> -- <https://osbuild.org/docs/bootc/> |
| image-builder-cli (successor under evaluation) | First-class SBOM + cross-arch successor to BIB | <https://github.com/osbuild/image-builder-cli> |
| rechunk (`bootc-base-imagectl rechunk`) | Layer-restructuring for 5--10x smaller `bootc upgrade` deltas | <https://github.com/hhd-dev/rechunk> |
| Anaconda (bootc kickstart) | ISO installer codepath used by `just iso` | <https://fedoramagazine.org/introducing-the-new-bootc-kickstart-command-in-anaconda/> |
| Renovate | Automated digest pinning for `image-versions.yml` and `Containerfile` ARGs | <https://docs.renovatebot.com/> |
| GitHub Actions | CI build/lint/sign/push pipeline | <https://docs.github.com/en/actions> |
| GitHub Container Registry (GHCR) | Image distribution at `ghcr.io/mios-dev/mios` | <https://docs.github.com/packages/working-with-a-github-packages-registry/working-with-the-container-registry> |
| Sigstore / cosign | Keyless OCI image signing + transparency log + attestation predicates | <https://github.com/sigstore/cosign> |
| syft | CycloneDX / SPDX SBOM generation (`automation/90-generate-sbom.sh`) | <https://github.com/anchore/syft> |
| shellcheck | Shell linter (CI gate; SC2038 fatal) | <https://github.com/koalaman/shellcheck> |
| hadolint | Containerfile linter (CI gate) | <https://github.com/hadolint/hadolint> |
| openssl (`passwd -6`) | yescrypt password hashes for BIB-injected accounts | <https://www.openssl.org/> |

## 5. Container runtime + Quadlet

| Component | Role | Upstream |
|---|---|---|
| Podman Quadlet | systemd-native container units (`*.container`, `*.image`, `*.network`, `*.volume`) -- the integration model for every `mios-*` service | <https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html> |
| Container Device Interface (CDI) | Universal device passthrough spec (NVIDIA, AMD, Intel) | <https://github.com/cncf-tags/container-device-interface> |
| containers.conf / storage.conf | Podman client-side defaults (`usr/share/containers/`) | <https://github.com/containers/common> |
| `containers/storage` | Podman/Buildah storage backend | <https://github.com/containers/storage> |
| `containers/image` | Image transport library | <https://github.com/containers/image> |
| `nvidia-container-toolkit` | NVIDIA Podman/CDI integration | <https://github.com/NVIDIA/nvidia-container-toolkit> |

## 6. Local AI runtime (the canonical 'MiOS' AI endpoint)

`MIOS_AI_ENDPOINT` is served by the MiOS inference Quadlet
at `etc/containers/systemd/mios-ai.container`. All other engines below are
listed as **Day-0 portability targets**: 'MiOS' agents resolve through
`MIOS_AI_ENDPOINT` so any of these can be slotted in.

| Engine | Role | Upstream |
|---|---|---|
| llama-swap | Default served runtime; model-swapping proxy fronting llama.cpp, OpenAI-compatible at `/v1` | <https://github.com/mostlygeek/llama-swap> |
| Ollama | **Not a supported lane.** Credited as an OpenAI-`/v1` compatibility reference only -- the local Ollama lane is RETIRED and `check_dead_lane` in `automation/98-drift-checks.sh` fails the build if it reappears in active config | <https://github.com/ollama/ollama> |
| vLLM | High-throughput server (`vllm serve <model>`) | <https://github.com/vllm-project/vllm> |
| llama.cpp server | CPU/GPU GGUF reference server | <https://github.com/ggerganov/llama.cpp> |
| LM Studio | Desktop OpenAI-compatible server | <https://lmstudio.ai/> |
| LiteLLM | Provider/translation proxy (incl. Responses <-> Chat Completions) | <https://github.com/BerriAI/litellm> |
| OpenRouter | Cloud aggregator if you bring keys | <https://openrouter.ai/> |
| llama.cpp (engine) | Powers GGUF inference inside `mios-llm-light` (llama-swap) | <https://github.com/ggerganov/llama.cpp> |

## 7. OpenAI public API spec & standards (the surface 'MiOS' targets)

CLAUDE.md is a thin pointer to these documents; `usr/share/doc/mios/reference/api.md` tracks the served
subset. Every URL below is the source-of-truth for the named surface.

| Surface | Spec |
|---|---|
| API Reference (root) | <https://platform.openai.com/docs/api-reference> |
| Models catalog | <https://platform.openai.com/docs/models> |
| Responses API | <https://platform.openai.com/docs/api-reference/responses/create> |
| Chat Completions | <https://platform.openai.com/docs/api-reference/chat/create> |
| Function calling / tools | <https://platform.openai.com/docs/guides/function-calling> |
| Structured outputs | <https://platform.openai.com/docs/guides/structured-outputs> |
| Embeddings | <https://platform.openai.com/docs/guides/embeddings> |
| Vector Stores / File Search | <https://platform.openai.com/docs/api-reference/vector-stores> |
| Batch API | <https://platform.openai.com/docs/guides/batch> |
| Evals API | <https://developers.openai.com/api/docs/guides/evals> |
| Fine-tuning (SFT) | <https://platform.openai.com/docs/guides/fine-tuning> |
| Direct Preference Optimization | <https://platform.openai.com/docs/guides/direct-preference-optimization> |
| Realtime / streaming | <https://platform.openai.com/docs/guides/realtime> |
| Audio (TTS / STT) | <https://platform.openai.com/docs/guides/audio> |
| Images | <https://platform.openai.com/docs/api-reference/images> |
| Moderation | <https://platform.openai.com/docs/guides/moderation> |
| OpenAI Cookbook | <https://cookbook.openai.com/> |
| OpenAI Python SDK | <https://github.com/openai/openai-python> |
| OpenAI Node SDK | <https://github.com/openai/openai-node> |
| `tiktoken` (tokenizers) | <https://github.com/openai/tiktoken> |
| Migration guide (Chat Completions -> Responses) | <https://developers.openai.com/api/docs/guides/migrate-to-responses> |

## 8. AI / LLM tooling (referenced for KB ingestion + tools)

| Tool | Role | Upstream |
|---|---|---|
| Model Context Protocol (MCP) | Tool/server protocol used by Responses API and `mios-mcp.service` | <https://modelcontextprotocol.io/> -- <https://github.com/modelcontextprotocol> |
| LangChain | Higher-level orchestration with OpenAI-compatible client | <https://github.com/langchain-ai/langchain> |
| LlamaIndex | RAG framework | <https://github.com/run-llama/llama_index> |
| DSPy | Programmatic prompting / compiler | <https://github.com/stanfordnlp/dspy> |
| Outlines | Constrained generation | <https://github.com/outlines-dev/outlines> |
| xgrammar | vLLM grammar engine | <https://github.com/mlc-ai/xgrammar> |
| axolotl | Fine-tuning trainer (consumes JSONL 'MiOS' ships) | <https://github.com/OpenAccess-AI-Collective/axolotl> |
| trl (Hugging Face) | RLHF / DPO trainer | <https://github.com/huggingface/trl> |
| llama-factory | Fine-tuning toolkit | <https://github.com/hiyouga/LLaMA-Factory> |
| MLX-LM | Apple Silicon trainer/server | <https://github.com/ml-explore/mlx-examples> |
| unsloth | Memory-efficient fine-tuning | <https://github.com/unslothai/unsloth> |

## 9. Vector / RAG datastores

| Store | Upstream |
|---|---|
| pgvector | <https://github.com/pgvector/pgvector> |
| Qdrant | <https://qdrant.tech/> |
| Chroma | <https://www.trychroma.com/> |
| Weaviate | <https://weaviate.io/> |
| Milvus | <https://milvus.io/> |
| LanceDB | <https://lancedb.com/> |
| Faiss | <https://github.com/facebookresearch/faiss> |

## 10. Network / system services

| Component | Role | Upstream |
|---|---|---|
| NetworkManager | Network state daemon + `nm-connection-editor` | <https://networkmanager.dev/> |
| OpenSSH | sshd + ssh client (postcheck enforces version >= 9.6) | <https://www.openssh.com/> |
| chrony | Time sync | <https://chrony-project.org/> |
| firewalld | Zone-based firewall | <https://firewalld.org/> |
| nftables | In-kernel packet filtering | <https://www.netfilter.org/projects/nftables/> |
| Avahi / nss-mdns | mDNS / `.local` resolution | <https://avahi.org/> |
| pipewire | Audio/video routing | <https://pipewire.org/> |
| WirePlumber | pipewire session manager | <https://gitlab.freedesktop.org/pipewire/wireplumber> |
| tuned | System tunable profiles | <https://github.com/redhat-performance/tuned> |
| greenboot | Health-checked boot rollback | <https://github.com/fedora-iot/greenboot> |
| uupd | Unified updater (replaces `bootc-fetch-apply-updates.timer`) | <https://github.com/ublue-os/uupd> |
| bootupd | Unified bootloader updater | <https://github.com/coreos/bootupd> |

## 11. Storage / cluster / source-control forge

| Component | Role | Upstream |
|---|---|---|
| Ceph (cephadm) | Distributed storage; admin via `cephadm shell` (containerized) | <https://ceph.io/> -- <https://docs.ceph.com/en/latest/cephadm/> |
| K3s | Lightweight Kubernetes distribution | <https://k3s.io/> -- <https://github.com/k3s-io/k3s> |
| `k3s-selinux` | SELinux policy compiled in-image (`automation/37-k3s-selinux.sh`) | <https://github.com/k3s-io/k3s-selinux> |
| Helm | K8s package manager | <https://helm.sh/> |
| `kubectl` | K8s CLI (symlinked from k3s binary) | <https://kubernetes.io/docs/reference/kubectl/> |
| Pacemaker / Corosync | HA cluster resource manager | <https://clusterlabs.org/pacemaker/> |
| libvirt | VM lifecycle daemon | <https://libvirt.org/> |
| QEMU | Machine emulator + KVM frontend | <https://www.qemu.org/> |
| KVM | Linux kernel hypervisor | <https://www.linux-kvm.org/> |
| virtiofs / virtio-net / virtio-blk | Paravirt host<->guest IO | <https://virtio-fs.gitlab.io/> -- <https://wiki.libvirt.org/Virtio.html> |
| virtio-win | Windows-guest paravirt drivers | <https://github.com/virtio-win/virtio-win-pkg-automation> |
| `virt-viewer` / `virt-manager` | VM consoles + GUI | <https://gitlab.com/virt-viewer/virt-viewer> -- <https://virt-manager.org/> |
| FreeRDP | RDP client used for `mios-guacamole` integration | <https://www.freerdp.com/> |
| Forgejo | Self-hosted Git forge served by `mios-forge.container` (HTTP 3000, git+ssh 2222); SQLite-default; matches MiOS-DEV's resource budget without a separate PostgreSQL | <https://forgejo.org/> -- <https://codeberg.org/forgejo/forgejo> -- governance: Codeberg e.V. (German non-profit), GPLv3+ |
| Forgejo Runner | GitHub-Actions-compatible CI runner that authenticates against `mios-forge` and executes `.github/workflows/` / `.forgejo/workflows/` jobs in ephemeral Podman containers | <https://code.forgejo.org/forgejo/runner> -- <https://forgejo.org/docs/latest/admin/actions/> |
| ActivityPub / ForgeFed | Federation protocol for Forgejo: cross-instance issue, PR, and star without surrendering source-code custody | <https://www.w3.org/TR/activitypub/> -- <https://forgefed.org/> |

## 12. Desktop / graphics

| Component | Role | Upstream |
|---|---|---|
| GNOME (Mutter, GTK, libadwaita) | Default Wayland session | <https://www.gnome.org/> |
| GDM | GNOME Display Manager | <https://gitlab.gnome.org/GNOME/gdm> |
| Cockpit | Web admin panel (postcheck enforces `LoginTo = false` and version >= 361) | <https://cockpit-project.org/> |
| Mesa | OpenGL / Vulkan / EGL userspace | <https://www.mesa3d.org/> |
| Wayland | Display server protocol | <https://wayland.freedesktop.org/> |
| Phosh (optional) | Mobile/touch shell | <https://phosh.mobi/> |
| Flatpak | App sandboxing + Flathub apps | <https://flatpak.org/> -- <https://flathub.org/> |
| Geist Font | UI typography | <https://github.com/vercel/geist-font> |
| `Bazaar` (Flatpak) | App store front-end | <https://github.com/kolunmi/bazaar> |
| `Flatseal` (Flatpak) | Per-app permission editor | <https://github.com/tchx84/Flatseal> |
| `Extension Manager` (Flatpak) | GNOME Shell extensions UI | <https://github.com/mjakeman/extension-manager> |
| GNOME Epiphany | GNOME Web (Flatpak default) | <https://gitlab.gnome.org/GNOME/epiphany> |

## 13. Security stack

| Component | Role | Upstream |
|---|---|---|
| SELinux | Mandatory access control | <https://github.com/SELinuxProject/selinux> |
| `selinux-policy-targeted` | Active policy module | <https://github.com/fedora-selinux/selinux-policy> |
| fapolicyd | Application allowlisting | <https://github.com/linux-application-whitelisting/fapolicyd> |
| USBGuard | USB device authorization | <https://usbguard.github.io/> |
| CrowdSec | Behavioral IDS / collaborative blocklists | <https://www.crowdsec.net/> -- <https://github.com/crowdsecurity/crowdsec> |
| AIDE | File integrity monitor | <https://aide.github.io/> |
| OpenSCAP / scap-security-guide | Compliance scanning | <https://www.open-scap.org/> -- <https://github.com/ComplianceAsCode/content> |
| audit (Linux Audit) | auditd / ausearch / aureport | <https://github.com/linux-audit/audit-userspace> |
| libpwquality | Password policy enforcement | <https://github.com/libpwquality/libpwquality> |
| setools-console | SELinux policy analysis | <https://github.com/SELinuxProject/setools> |
| TPM2 (`tpm2-tools`, clevis, clevis-luks) | TPM-bound LUKS unlock | <https://tpm2-software.github.io/> -- <https://github.com/latchset/clevis> |
| SecureBlue | Hardening reference profile + auditing | <https://github.com/secureblue/secureblue> |

## 14. GPU stacks

| Stack | Components | Upstream |
|---|---|---|
| NVIDIA | open kernel modules (Turing+) on `:stable-nvidia`; LTS proprietary 580 on `:stable-nvidia-lts`; `nvidia-container-toolkit`, `nvidia-persistenced`, `nvidia-settings`, CUDA, akmods | <https://www.nvidia.com/> -- <https://github.com/NVIDIA/nvidia-container-toolkit> -- <https://rpmfusion.org/Packaging/KernelModules/Akmods> |
| Intel | Xe / i915 kernel + mesa-vulkan-drivers + `intel-compute-runtime` (oneAPI) | <https://www.intel.com/content/www/us/en/developer/tools/oneapi/overview.html> |
| AMD | amdgpu kernel + Mesa RADV + ROCm/HIP runtime | <https://rocm.docs.amd.com/> |
| Looking Glass | Low-latency VFIO display via shared memory | <https://looking-glass.io/> |
| KVMFR | Looking Glass kernel module (built in-image via `automation/52-bake-kvmfr.sh`) | <https://looking-glass.io/docs/B7/install_kvmfr/> |

## 15. Virtualization / VFIO

| Component | Role | Upstream |
|---|---|---|
| VFIO-PCI | PCI passthrough in-kernel | <https://docs.kernel.org/driver-api/vfio.html> |
| `qemu-device-display-virtio-gpu` | virtio-gpu accelerated display | <https://wiki.qemu.org/Features/VirtIO> |
| Waydroid | Android-in-LXC for Wayland | <https://waydro.id/> -- <https://github.com/waydroid> |

## 16. Gaming / Windows compatibility

| Component | Role | Upstream |
|---|---|---|
| Steam | Game launcher (user-installed via flatpak/repo per profile) | <https://store.steampowered.com/about/> |
| Wine | Windows API translation layer | <https://www.winehq.org/> |
| DXVK | Direct3D -> Vulkan translation | <https://github.com/doitsujin/dxvk> |
| Gamescope | Micro-compositor for game scaling | <https://github.com/ValveSoftware/gamescope> |
| MangoHud | In-game performance overlay | <https://github.com/flightlessmango/MangoHud> |
| `steam-devices` | udev rules for controllers | <https://gitlab.com/evlaV/steam-devices> |

## 17. Knowledge / agent ingestion conventions

| Spec | Role in 'MiOS' | Upstream |
|---|---|---|
| `agents.md` standard | `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` follow this convention | <https://agents.md/> |
| `llms.txt` standard | `llms.txt` and `llms-full.txt` at repo root | <https://llmstxt.org/> |
| Renovate `customManager` regex | `image-versions.yml` digest pinning | <https://docs.renovatebot.com/modules/manager/regex/> |

## 18. Reference / inspiration distros (for design decisions)

| Distro | Why it's referenced | Upstream |
|---|---|---|
| Universal Blue (umbrella) | Direct upstream of the base image; Quadlet-first patterns | <https://github.com/ublue-os> |
| Bluefin / Aurora / Bazzite | Same family, different desktop targets | <https://github.com/ublue-os/bluefin> -- <https://github.com/ublue-os/aurora> -- <https://github.com/ublue-os/bazzite> |
| Fedora Silverblue / Kinoite | Original immutable Fedora workstations (rpm-ostree) | <https://fedoraproject.org/silverblue/> -- <https://fedoraproject.org/kinoite/> |
| CoreOS Layering / rpm-ostree | Substrate for ostree-based atomic upgrades | <https://github.com/coreos/rpm-ostree> |
| SecureBlue | Hardening profile reference | <https://github.com/secureblue/secureblue> |
| Talos | API-driven Kubernetes-only OS (alt path, not chosen) | <https://www.talos.dev/> |
| Flatcar | Container Linux successor (alt path, not chosen) | <https://www.flatcar.org/> |
| NixOS | Declarative comparison (different paradigm) | <https://nixos.org/> |
| Vanilla OS | Image-based Ubuntu derivative (sibling concept) | <https://vanillaos.org/> |

## 19. Patterns / conventions used in 'MiOS'

| Pattern | Where it lives in this repo |
|---|---|
| **USR-OVER-ETC** -- vendor config in `/usr/lib/...d/`, `/etc/` is admin-only | `usr/lib/**`, `etc/sysusers.d/cephadm.conf` (override example) |
| **NO-MKDIR-IN-VAR** -- no `/var/` writes at build time | `usr/lib/tmpfiles.d/mios*.conf` (declarations) |
| **BOUND-IMAGES** -- Quadlet images symlinked into `/usr/lib/bootc/bound-images.d/` | `automation/01-system-files-overlay.sh:74-86` |
| **BOOTC-CONTAINER-LINT** -- last `RUN` of Containerfile | `Containerfile` final step |
| **UNIFIED-AI-REDIRECTS** -- single `MIOS_AI_ENDPOINT`; no vendor URLs | `usr/bin/mios`, `etc/mios/ai/`, `etc/containers/systemd/mios-ai.container` |
| **UNPRIVILEGED-QUADLETS** -- `User=`, `Group=`, `Delegate=yes` on every Quadlet | `etc/containers/systemd/mios-*.container` |
| sysusers.d / tmpfiles.d format | `usr/lib/sysusers.d/`, `usr/lib/tmpfiles.d/`, `etc/sysusers.d/` |
| Systemd `ConditionVirtualization=` gating | `usr/lib/systemd/system/*.service.d/*.conf` drop-ins |
| bootc kargs.d flat-array TOML | `usr/lib/bootc/kargs.d/*.toml` |
| Quadlet (.container/.image/.network/.volume) | `etc/containers/systemd/`, `usr/share/containers/systemd/` |
| FHS 3.0 root layout | repo root mirrors `/` |
| `agents.md` agent identity convention | `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.clinerules`, `.cursorrules` |
| `llms.txt` / `llms-full.txt` | repo root |
| OpenAI `/v1` API surface | `$MIOS_AI_ENDPOINT` |
| MCP server registry | `usr/share/mios/ai/v1/mcp.json` |

## 20. Internal repo files referenced as canonical sources

These are 'MiOS'-internal files that other documents and code refer to as
the source of truth for a given concern. When in doubt, these win:

| File | Concern | Path |
|---|---|---|
| `usr/share/mios/ai/INDEX.md` | Architectural laws + API surface index | <https://github.com/mios-dev/MiOS/blob/main/INDEX.md> |
| `usr/share/doc/mios/concepts/architecture.md` | FHS layout + hardware model | <https://github.com/mios-dev/MiOS/blob/main/ARCHITECTURE.md> |
| `usr/share/doc/mios/guides/engineering.md` | Build + lint + shell conventions | <https://github.com/mios-dev/MiOS/blob/main/ENGINEERING.md> |
| `usr/share/doc/mios/guides/self-build.md` | Build modes (just / Windows orchestrator / BIB) | <https://github.com/mios-dev/MiOS/blob/main/SELF-BUILD.md> |
| `usr/share/doc/mios/guides/deploy.md` | Day-2 lifecycle | <https://github.com/mios-dev/MiOS/blob/main/DEPLOY.md> |
| `SECURITY.md` | Security posture and hardening kargs | <https://github.com/mios-dev/MiOS/blob/main/SECURITY.md> |
| `CONTRIBUTING.md` | Contributor conventions | <https://github.com/mios-dev/MiOS/blob/main/CONTRIBUTING.md> |
| `usr/share/doc/mios/reference/licenses.md` | Component license inventory | <https://github.com/mios-dev/MiOS/blob/main/LICENSES.md> |
| `usr/share/doc/mios/reference/sources.md` | KB-grade citation tracking | <https://github.com/mios-dev/MiOS/blob/main/SOURCES.md> |
| `usr/share/doc/mios/reference/api.md` | OpenAI surface trace + Build/Architecture appendix | <https://github.com/mios-dev/MiOS/blob/main/API.md> |
| `CLAUDE.md` | Agent-identity pointer to OpenAI docs + standards | <https://github.com/mios-dev/MiOS/blob/main/CLAUDE.md> |
| `AGENTS.md` / `GEMINI.md` | Sibling agent-identity pointers | <https://github.com/mios-dev/MiOS/blob/main/AGENTS.md> -- <https://github.com/mios-dev/MiOS/blob/main/GEMINI.md> |
| `usr/share/mios/mios.toml` | Single source of truth for every RPM (`[packages.<section>].pkgs`) | <https://github.com/mios-dev/MiOS/blob/main/usr/share/mios/mios.toml> |
| `usr/share/doc/mios/reference/PACKAGES.md` | Human-readable companion documentation for the package surface | <https://github.com/mios-dev/MiOS/blob/main/usr/share/doc/mios/reference/PACKAGES.md> |
| `usr/share/mios/ai/system.md` | Canonical agent system prompt (image-baked) | <https://github.com/mios-dev/MiOS/blob/main/usr/share/mios/ai/system.md> |
| `usr/share/mios/ai/v1/models.json` | Local `/v1/models` catalog | <https://github.com/mios-dev/MiOS/blob/main/usr/share/mios/ai/v1/models.json> |
| `usr/share/mios/ai/v1/mcp.json` | MCP server registry | <https://github.com/mios-dev/MiOS/blob/main/usr/share/mios/ai/v1/mcp.json> |
| `image-versions.yml` | Renovate-tracked base-image digests | <https://github.com/mios-dev/MiOS/blob/main/image-versions.yml> |
| `renovate.json` | Renovate config | <https://github.com/mios-dev/MiOS/blob/main/renovate.json> |
| `.github/workflows/mios-ci.yml` | CI pipeline | <https://github.com/mios-dev/MiOS/blob/main/.github/workflows/mios-ci.yml> |

## 21. Bootstrap repo

| File | Concern | Path |
|---|---|---|
| `mios-bootstrap` (repo) | Phase-0 preflight + identity, Phase-1 Total Root Merge, Phase-4 reboot | <https://github.com/mios-dev/mios-bootstrap> |
| `install.sh` / `install.ps1` | Cross-platform installers | <https://github.com/mios-dev/mios-bootstrap/blob/main/install.sh> -- <https://github.com/mios-dev/mios-bootstrap/blob/main/install.ps1> |
| `bootstrap.sh` / `bootstrap.ps1` | First-run bootstrappers | <https://github.com/mios-dev/mios-bootstrap/blob/main/bootstrap.sh> -- <https://github.com/mios-dev/mios-bootstrap/blob/main/bootstrap.ps1> |
| `.env.mios` | User-runtime env defaults (mirrored into MiOS root) | <https://github.com/mios-dev/mios-bootstrap/blob/main/.env.mios> |
| `profile/` | Per-user profile staging templates | <https://github.com/mios-dev/mios-bootstrap/tree/main/profile> |
| `etc/skel/.config/mios/` | User dotfile templates seeded on `useradd -m` | <https://github.com/mios-dev/mios-bootstrap/tree/main/etc/skel> |
| `image-versions.yml` | Mirror of base-image digest pins | <https://github.com/mios-dev/mios-bootstrap/blob/main/image-versions.yml> |

## 22. AI agents used in this project (un-labeled, OpenAI-API-shaped)

> 'MiOS' treats every editor/CLI agent as an *OpenAI-API-compatible client*
> rather than as a vendor brand. The agent-identity files in this repo
> (`CLAUDE.md`, `GEMINI.md`, `AGENTS.md`, `.clinerules`, `.cursorrules`,
> `.github/ai-instructions.md`) exist for tooling discovery only -- their
> filenames are conventions the upstream tools look for; their contents
> are vendor-neutral pointers to the same canonical prompt.
>
> **Architectural Law 5 -- UNIFIED-AI-REDIRECTS.** Every client below
> resolves through `MIOS_AI_ENDPOINT`, an
> OpenAI-public-API-compatible surface served by
> `etc/containers/systemd/mios-ai.container`. Vendor-native URLs
> (`api.openai.com`, `api.anthropic.com`,
> `generativelanguage.googleapis.com`, `api.cline.bot`, `api.cursor.com`,
> `api.githubcopilot.com`, etc.) are forbidden in the deployed image and
> fail audit. Differences between clients are presentation-layer only.
>
> **OpenAI patterns adopted across every client:**
> Chat Completions (`POST /v1/chat/completions`), Responses
> (`POST /v1/responses`), function calling / tool-use schema, structured
> outputs (`response_format: json_schema`, `strict: true`), embeddings
> (`POST /v1/embeddings`), MCP tool invocation, model discovery
> (`GET /v1/models`), JSONL training format, and `Authorization: Bearer ...`
> auth. Each surface is anchored in section 7 above.

The discovery files below are listed by **filename convention** (what the
tool looks for) and **client wiring** (how it resolves to the OpenAI-shaped
endpoint). Vendor names appear only as the upstream link target so a
reader can reach the tool's docs; they are not load-bearing in any
configuration in this repo.

| Discovery file | What looks for it | OpenAI-API client wiring | Upstream docs (link only) |
|---|---|---|---|
| `CLAUDE.md`, `.claude/settings.local.json` | A CLI agent that auto-loads `CLAUDE.md` from cwd | Wrapped via `mios-agent-claude` -- prompt injected; no network setting required because the wrapper exec's the local CLI which is configured against `MIOS_AI_ENDPOINT` | <https://docs.claude.com/en/docs/claude-code/overview> |
| `.github/ai-instructions.md` | Editor assistants that read `.github/` instruction files | Repo-side instructions only; in-image traffic still routes to `MIOS_AI_ENDPOINT` | <https://docs.github.com/en/copilot> |
| `.clinerules` | A VS Code agent that reads `.clinerules` from project root | "OpenAI-Compatible" provider in the agent's settings points at `MIOS_AI_ENDPOINT` | <https://github.com/cline/cline> |
| `.cursorrules` | An editor that reads `.cursorrules` from project root | "OpenAI-compatible" custom-model path set to `MIOS_AI_ENDPOINT` | <https://docs.cursor.com/> |
| `GEMINI.md` | A CLI that auto-loads `GEMINI.md` from cwd | Vendor-CLI's OpenAI-compatibility flag pointed at `MIOS_AI_ENDPOINT`; native vendor endpoint not used in-image | <https://github.com/google-gemini/gemini-cli> |
| `AGENTS.md` (agents.md standard) | Any agents.md-aware client (Codex CLI, etc.) | `OPENAI_BASE_URL` env var overridden to `MIOS_AI_ENDPOINT` | <https://agents.md/> -- <https://github.com/openai/codex> |

Aliasing files that all point to the same canonical prompt:

| Alias / pointer | Purpose |
|---|---|
| `system-prompt.md` | Repo-root pointer to the canonical prompt |
| `AI.md` (mios-bootstrap) | Bootstrap-side AI entry point + path index |
| `usr/share/mios/ai/system.md` | The single canonical agent system prompt (image-baked, deployed from `mios-bootstrap`) |
| `usr/share/mios/ai/v1/models.json` | OpenAI-shaped `/v1/models` catalog the agents discover |
| `usr/share/mios/ai/v1/mcp.json` | MCP server registry the agents call out to |

### 'MiOS'-internal agent surfaces (runtime, not editor-time)

| Surface | Role | Where |
|---|---|---|
| `mios-ai.container` | OpenAI-compatible `/v1` endpoint that all in-image agents call | `etc/containers/systemd/mios-ai.container` |
| `mios-mcp.service` | Local MCP server runtime | `usr/lib/systemd/system/mios-mcp.service`, `usr/libexec/mios/mcp-server-runner` |
| `mios-mcp-init.sh` | MCP pre-flight (sqlite vault + dirs) | `usr/libexec/mios/mcp-init.sh` |
| `usr/bin/mios` | Single CLI entrypoint that resolves `MIOS_AI_ENDPOINT` for every agent | `usr/bin/mios` |
| `mios-llm` | Vendor-neutral OpenAI `/v1/chat/completions` wrapper (`install-mios-agents.sh`) | `/usr/local/bin/mios-llm` |
| `mios-agent-claude`, `mios-agent-gemini` | Thin wrappers that pre-load the canonical system prompt before exec'ing the local CLI binary; no vendor logic beyond the exec | `/usr/local/bin/mios-agent-{claude,gemini}` |
| `mios-forge.container` | Self-hosted Git forge (Forgejo upstream); HTTP `:3000`, git+ssh `:2222`; SQLite-default at `/srv/mios/forge/forgejo.db`; repository bytes at `/srv/mios/forge/git/` | `etc/containers/systemd/mios-forge.container` |

### `USER` variable resolution at build entry

Every `USER` token in this codebase is a *placeholder*, not a hardcoded
identity. Resolution happens at install/build entry:

| Site | Form | Resolved by |
|---|---|---|
| Shell scripts (`*.sh`) | `$USER`, `$HOME`, `~` | The login shell at runtime |
| PowerShell scripts (`*.ps1`) | `$env:USERNAME`, `$env:USERPROFILE` | PowerShell at runtime |
| Markdown aggregates | literal `USER` | The bootstrap installer's sed pass at install entry (`install.sh` / `install.ps1` reads detected username and substitutes) |
| Profile / env templates | `MIOS_IDENTITY_USERNAME`, `MIOS_IDENTITY_HOSTNAME` | `etc/mios/profile.toml` -> `/etc/mios/install.env` -> `~/.config/mios/profile.toml` (three-layer override; highest wins) |

The only other user-related identifiers permitted in the codebase are
the `MiOS` brand and the `mios` default account name; both are project
conventions, not personal identities.

## 23. Where each thing came from (origin summary)

For people scanning quickly:

- **OS substrate (kernel, systemd, dnf5, RPMs):** the Fedora Project, via the
  Universal Blue base image.
- **Atomic upgrade machinery (bootc, ostree, composefs, Podman/Buildah/Skopeo,
  containers/storage, BIB, image-builder-cli):** the `containers/` + `bootc-dev/`
  + `osbuild/` + `ostreedev/` + `composefs/` orgs on GitHub.
- **Image distribution (GHCR, cosign, syft, rechunk):** GitHub, Sigstore,
  Anchore, hhd-dev.
- **Local AI runtime (llama.cpp, llama-swap, vLLM):** independent open-source
  projects -- 'MiOS' deploys llama.cpp behind llama-swap by default; the rest
  are swap-in via `MIOS_AI_ENDPOINT`. Ollama is credited above as an API-compat
  reference only; it is not shipped and its lane is retired.
- **AI surface (the `/v1` API spec):** OpenAI's published reference docs,
  treated as a public/community standard for OpenAI-compatible runtimes.
- **Orchestration patterns (Quadlet, sysusers.d, tmpfiles.d, kargs.d, FHS):**
  systemd / FHS upstream specifications.
- **Cluster + storage (Ceph, K3s, libvirt, QEMU):** their respective
  upstream projects (Ceph Foundation, Rancher/SUSE, the libvirt/KVM/QEMU
  community).
- **Desktop / graphics (GNOME, Mesa, pipewire, GDM):** freedesktop.org +
  GNOME Foundation projects.
- **Hardening (SELinux, fapolicyd, USBGuard, CrowdSec, OpenSCAP, AIDE):**
  per-project upstreams; reference posture cribbed from SecureBlue and the
  Fedora hardening guide.
- **Container GPU (NVIDIA Toolkit, CDI, ROCm, Intel oneAPI):** vendor +
  CNCF tag-runtime working groups.
- **Conventions (agents.md, llms.txt, MCP):** community-defined open
  standards; every MiOS agent-identity file follows them.
