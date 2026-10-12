<!-- AI-hint: Documents the Day-0 first-boot sequence of MiOS-DEV — mios-firstboot provisioning (CDI/libvirtd/GRD), git-root-init turning / into a working tree of the local Forgejo mios.git, mios-ai-firstboot (agent venv + llama.cpp GGUFs), PostgreSQL+pgvector schema init, and bring-up of the local AI plane (mios-llm-light, mios-pgvector, hermes-agent, mios-agent-pipe) — a reference for verifying boot status and troubleshooting failures.
     AI-related: /usr/share/mios/docs/day-0/FIRST-BOOT.md, /usr/share/mios/docs/day-0/BOOTSTRAP.md, mios-dev, mios-bootstrap, mios-firstboot.target, mios-git-root-init.service, mios-ai-firstboot.service, mios-llm-light.service, mios-pgvector.service, hermes-agent.service, mios-agent-pipe.service, mios-doctor -->
<!-- FHS: /usr/share/mios/docs/day-0/FIRST-BOOT.md -->

# Day-0: First Boot of MiOS-DEV

## Purpose

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped
Fedora workstation** (the whole OS is a single container image — boot it,
`bootc upgrade` it like a `git pull`, `bootc rollback` it like a Ctrl-Z) that is
*also* a **local, self-replicating, agentic AI operating system**. The same image
ships GNOME/Wayland, GPU wiring via CDI, KVM/libvirt passthrough, and a k3s+Ceph
cluster path *and* a full local agent stack behind one OpenAI-compatible endpoint.

[`BOOTSTRAP.md`](BOOTSTRAP.md) covers Phase-0/1 — how a bare host becomes
**MiOS-DEV**, the seed build environment. **This document covers what happens the
first time MiOS-DEV actually boots.** First boot is the moment MiOS-DEV becomes a
self-contained build environment *and* a live agentic host: from this point on it
can both produce any number of "next MiOS" OCI images and operate itself through
its own local agent stack.

That dual outcome is why first boot does two distinct kinds of work: it finishes
provisioning the **build/virtualization plane** (CDI, libvirt, the git-backed
self-build root) and it stands up the **AI plane** (the inference lanes, the
unified pgvector datastore, and the agent orchestrators behind one
OpenAI-compatible endpoint). The sections below walk that sequence, how to verify
it, and what to do if a step fails.

## What Happens

1. **`systemd-firstboot`** runs (stock systemd):
   - Sets the hostname and seeds the default user/locale on a fresh deploy.
2. **`mios-firstboot.target`** orchestrates first-boot provisioning of the
   build/virtualization plane, pulling in:
   - `mios-cdi-detect.service` — generates the Container Device Interface
     specs so GPUs (NVIDIA / ROCm / iGPU) are addressable by Quadlets.
   - `mios-libvirtd-setup.service` — KVM/libvirt + virtnetwork bring-up.
   - `mios-grd-setup.service` — remote-desktop plumbing.
3. **`mios-git-root-init.service`** (oneshot) initializes `/` as a **git working
   tree** of `localhost:3000/<user>/mios.git` (the local Forgejo). This is what
   makes the running host self-buildable — the deployed root *is* the repo. The
   sentinel is `/.git`; it short-circuits if Forgejo or the repo aren't reachable
   yet, so re-running on later boots is harmless.
4. **`mios-ai-firstboot.service`** (oneshot) completes the AI setup that wasn't
   baked at build time (overlay-provisioned dev VMs, WSL imports):
   - Builds the shared agent venv (`/usr/lib/mios/agents/.venv`, used by both
     hermes-agent and agent-pipe).
   - Provisions the llama.cpp **GGUF models** under
     `/usr/share/mios/llamacpp/models` (normally baked by
     `automation/38-llamacpp-prep.sh`; otherwise fetched from Hugging Face).
   - Writes `/var/lib/mios/.ai-firstboot-done` **only** when both the venv and
     the GGUFs are present, so a network-less first boot simply retries next time.
5. The **AI plane** comes up (host services + Quadlets, all wanted by
   `multi-user.target`):
   - `mios-llm-light.service` (port key `llm_light`) — the **primary** local inference lane:
     llama.cpp behind the upstream `mios-llm-light` proxy image, multi-model
     auto-swap + KV-cache paging. It serves the everyday models, the
     `mios-opencode` coder model, **and embeddings** (`nomic-embed-text`,
     OpenAI-compat `/v1/embeddings`).
   - `mios-pgvector.service` (:5432) — PostgreSQL + pgvector, the unified
     agent-plane datastore. On **first** init it runs
     `/usr/share/mios/postgres/schema-init.sql` (idempotent DDL incl.
     `CREATE EXTENSION vector`), creating the `agent_memory`, `event`,
     `tool_call`, `session`, `skill`, `scratch`, `knowledge`, `sys_env`,
     `kanban`, `directory_entry`, … tables. This replaces the old
     vector-index-population step — vectors live in pgvector now, embedded via
     `nomic-embed-text` on `mios-llm-light`.
   - `hermes-agent.service` (port key `hermes`) — the OpenAI-compatible agent gateway and
     tool-loop, resolving its backend from `MIOS_AI_ENDPOINT` (Law 5).
   - `mios-agent-pipe.service` (port key `agent_pipe`) — the orchestrator (router + refine +
     council/swarm fan-out + critic/polish) that fronts Hermes for every
     gateway; depends on `hermes-agent` and `mios-pgvector`.
   - The heavy lanes `mios-llm-heavy.service` (port key `vllm`, vLLM) and
     `mios-llm-heavy-alt.service` (port key `sglang`, SGLang) stay **gated/off-by-default** (VRAM)
     until enabled in `mios.toml` and reachable.
6. **Cockpit** lands on **:9090** over TLS with the platform CA.

> Inference engines are named by **function**, not by upstream tool:
> `mios-llm-light` / `mios-llm-heavy` / `mios-llm-heavy-alt`. `mios-llm-light`
> (`ghcr.io/mostlygeek/llama-swap`) and the OpenAI/Ollama-compatible API are
> legitimate upstream references; only the MiOS *unit identity* is `mios-llm-*`.
> There is no longer an `ollama`, `qdrant`, or the legacy datastore service — inference and
> embeddings run on `mios-llm-light`; the agent datastore is pgvector.

## Verifying

```sh
# AI plane reachable on the unified endpoint ($MIOS_AI_ENDPOINT):
mios "hello — confirm you're up"

# Structured host health check (privilege chain, services, GPU, OWUI, etc.):
mios-doctor

# Spot-check the individual planes:
systemctl status mios-llm-light.service mios-pgvector.service \
                 hermes-agent.service mios-agent-pipe.service
curl -s "http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1/models"   # light lane: served models
curl -s "http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1/embeddings" -d '{"model":"nomic-embed-text","input":"ping"}' \
     -H 'content-type: application/json'         # embeddings lane
```

Expect: the git-backed root present (`test -d /.git`), pgvector accepting
connections on :5432, `mios-llm-light` serving its model map, and the
agent-pipe/Hermes pair answering on the unified endpoint.

## First-boot ordering diagram

```
systemd-firstboot.service
        │
        ├──► mios-firstboot.target            (build/virt plane)
        │        ├──► mios-cdi-detect.service       (GPU CDI specs)
        │        ├──► mios-libvirtd-setup.service   (KVM/libvirt)
        │        └──► mios-grd-setup.service        (remote desktop)
        │
        ├──► mios-git-root-init.service        (/ → working tree of mios.git)
        │
        ├──► mios-ai-firstboot.service         (agent venv + llama.cpp GGUFs)
        │
        └──► AI plane (Quadlets + host services)
                ├──► mios-llm-light.service    (port key `llm_light` — primary inference + embeddings)
                ├──► mios-pgvector.service     (:5432  — schema init on first run)
                ├──► hermes-agent.service      (port key `hermes` — OpenAI gateway)
                └──► mios-agent-pipe.service   (port key `agent_pipe` — orchestrator, fronts Hermes)
```

All of the above are `WantedBy=multi-user.target`. Ordering inside the AI plane
is enforced by `After=`/`Wants=`: `mios-agent-pipe` waits on `hermes-agent` and
`mios-pgvector`; `hermes-agent` and `mios-ai-firstboot` wait on
`mios-llm-light`. The heavy lanes are gated and stay inert until enabled.

## What if Day-0 fails?

- The system still boots to a usable shell and a usable desktop — the AI plane
  is additive, not a boot dependency.
- `mios-doctor` reports the first plane that failed (privilege chain, services,
  GPU plumbing, OWUI registration, …).
- Per-step output:
  ```sh
  journalctl -u mios-firstboot.target -u mios-git-root-init \
             -u mios-ai-firstboot -u mios-llm-light -u mios-pgvector \
             -u hermes-agent -u mios-agent-pipe
  ```
- Re-running is safe (every first-boot unit is idempotent and sentinel-gated):
  ```sh
  systemctl start mios-ai-firstboot.service     # re-pull venv/GGUFs
  systemctl start mios-git-root-init.service    # re-init / as git tree
  ```
- A network-less first boot is expected to leave the venv/GGUFs incomplete; the
  units retry automatically on the next boot once egress is available.

## Build invariants (why the produced image is trustworthy)

MiOS-DEV's purpose is to emit the *next* MiOS image, and that image is only
trustworthy if it obeys the six **Architectural Laws** (the contract that lets
MiOS be both immutable and agentic at once). The build pipeline enforces them, so
the invariant gate refuses to proceed if any of the following are observed:

- A path `./system_files/` exists in either repo's view (the repo root **is** the
  system root — Law structure; no `system_files/` indirection).
- Any file under `usr/libexec/mios/phases/` (or `automation/`) contains literal
  `--squash-all` (strips `ostree.final-diffid`, breaks BIB / atomic deploy) or
  `((` increment style under `set -e` (returns 1 on a zero result).
- Any `usr/lib/bootc/kargs.d/*.toml` parses as containing a `[kargs]` section
  header — kargs files must be a flat top-level array only.
- A `cp -a` writing into `/usr/local` is grep-detectable in any phase
  (`/usr/local` is admin space, not vendor space — Law 1 USR-OVER-ETC).
- `/etc/xrdp/xrdp.ini` references `libxvnc.so` or omits `libxup.so`
  (remote-desktop correctness).

The final, non-negotiable check is **Law 4 (BOOTC-CONTAINER-LINT)**: the last
`RUN` of the `Containerfile` is `bootc container lint`, and a failure there fails
the build. Together with Laws 2–3 (`NO-MKDIR-IN-VAR`, `BOUND-IMAGES`) and Laws
5–6 (`UNIFIED-AI-REDIRECTS`, `UNPRIVILEGED-QUADLETS`), these keep every image
MiOS-DEV produces deterministic, atomic, rollback-safe, and least-privileged.

## Next

First boot is the seam where Day-0 becomes the running system. From here:

- the **build pipeline** (Phase-2 onward) turns this self-buildable root into the
  next OCI image — see the self-build guide;
- the **bootc lifecycle** (`bootc upgrade` / `bootc rollback`) carries that image
  forward on real hosts — see the deploy guide;
- the **AI plane** you just watched come up serves every front-end (OWUI on
  the `open_webui` port, the Discord gateway, the `mios` CLI) through the agent-pipe → Hermes
  orchestration over pgvector memory, with MCP exposing the tool surface and A2A
  federating peer agents.
