<!-- AI-hint: Explains MiOS's local-only, vision-grounded desktop-automation lane (the "Computer Use" capability) — how screenshot-capture (mios-pc-control / mios-computer-use) is bridged to UI click-coordinates by a local grounding VLM (qwen3-vl:4b on the mios-llm-light lane, or UI-TARS on the gated mios-llm-heavy vLLM lane) and orchestrated by Hermes + opencode, with no cloud APIs. Use to understand how PC/desktop control fits the whole MiOS agent stack.
     AI-related: /usr/libexec/mios/mios-pc-control, /usr/libexec/mios/mios-pc-vision, /usr/libexec/mios/mios-computer-use, /usr/libexec/mios/mios-computer-use-server, /usr/lib/mios/agents/opencode/bin/opencode, /usr/share/mios/llamacpp/mios-llm-light.yaml, mios-pc-control, mios-pc-vision, mios-computer-use, mios-llm-light, mios-llm-heavy-alt, mios-hermes-browser, mios-opencode-gateway -->
# Local-only PC Control: vision-grounded desktop automation under MiOS

**Status:** SHIPPED + EXTENDED. The Windows path (`mios-pc-control` +
`mios-pc-vision`) is live; this proposal is now realised cross-platform AND
federated. The Linux/Wayland peer (`mios-computer-use` + `cu_*` verbs + the dual
MCP/A2A node server `mios-computer-use-server`) and the federation story are
documented in
[`usr/share/doc/mios/concepts/computer-use-federation.md`](../../../doc/mios/concepts/computer-use-federation.md).
The grounding-VLM upgrade path named below is wired: the vision pass targets the
**`mios-llm-light`** lane (port key `llm_light`) serving `qwen3-vl:4b` when its GGUFs are
present, with the gated **`mios-llm-heavy`** (vLLM) lane (port key `vllm`,
served-name `mios-grounding`) as the heavyweight alternative for UI-TARS-class
heads — both VRAM-gated until the dGPU frees. The original research synthesis is
retained below for provenance; engine/port references have been updated to the
current MiOS stack.

## Where this fits in the whole system

MiOS is one thing built two ways at once: an **immutable, bootc/OCI-shaped Fedora
workstation** (the whole OS is a single container image you `bootc upgrade` like a
`git pull` and `bootc rollback` like a Ctrl-Z) that is *also* a **local,
self-hosted, agentic AI operating system**. The same image that ships the
GNOME/Wayland desktop, the NVIDIA+ROCm+iGPU CDI wiring, and the KVM/k3s paths also
ships a complete local agent stack behind one OpenAI-compatible endpoint
(`MIOS_AI_ENDPOINT`).

**PC Control is the capability that lets that agent stack drive the GUI it lives
in** — open apps, click buttons, fill forms, verify results — entirely on local
inference, with no cloud vision API in the loop. It is the "hands and eyes" of the
agent plane:

- The **agent-pipe** (port key `agent_pipe`) orchestrator refines a request, fans it out across
  the council/swarm, and dispatches tool/verb calls.
- **MiOS-Hermes** (port key `hermes`), the OpenAI-compatible gateway and tool-loop agent,
  owns the plan and the desktop tool loop (screenshot → ground → act → verify).
- The **inference lanes** (`mios-llm-light` primary, `mios-llm-heavy`/`-heavy-alt`
  gated) do the actual generation — including the grounding-VLM pass.
- **PostgreSQL + pgvector** (`mios-pgvector`, `:5432`) holds the agent's memory,
  skills, and knowledge; a PC-control skill captured here is reusable across
  sessions.
- **MCP** exposes every verb (including `cu_*`/`pc_*`) as a tool; **A2A** lets a
  remote MiOS desktop be driven as a federated peer.

So the throughline of this doc — *screenshot → grounding VLM → click* — is one leg
of the larger throughline **inference lanes → agent-pipe/Hermes orchestration →
pgvector memory → MCP/A2A federation**. This document captures the research
synthesis behind achieving Anthropic-"Computer Use"-style desktop automation on a
MiOS host using ONLY local models on the in-image inference lanes. No cloud APIs;
no Browserbase / Browser Use / Firecrawl dependencies.

## TL;DR

Hermes is the orchestrator. opencode is the coder. The load-bearing piece is a
**screen-grounding vision LLM** that takes a screenshot and returns clickable
coordinates for UI elements. The cleanest local options:

| Vision model | Size | Native grounding? | How MiOS serves it | Notes |
|---|---|---|---|---|
| **UI-TARS-1.5-7B** (ByteDance, Apache 2.0) | 7B | YES — trained on Win/macOS/Web GUI traces, returns absolute coords directly | gated `mios-llm-heavy` vLLM lane (port key `vllm`, served-name `mios-grounding`) | Purpose-built for PC control; **strongest local option** for the heavy lane |
| **Qwen3-VL** (Alibaba, Apache 2.0) | 2B / 4B / 8B / 32B / 235B | YES — 2D grounding (absolute + relative coords); native "operates PC/mobile GUIs, recognizes elements" | `qwen3-vl:4b` in the `mios-llm-light` mios-llm-light map (port key `llm_light`) | Smaller variants run on the 4090 alongside the everyday models |
| Llama 3.2-Vision | 11B / 90B | NO grounding — general image understanding only | (not baked; opt-in) | Needs Set-of-Mark prompting for grounding; weaker for PC tasks |
| MiniCPM-V / Moondream | 2B–8B | Partial | (not baked) | OCR-strong; coordinate output is unreliable |

**Recommended baseline:** `qwen3-vl:4b` for the grounding pass, served on the
primary `mios-llm-light` lane (port key `llm_light`) — the same llama.cpp engine that already
serves the everyday chat/reasoning models, the `mios-opencode` coder model, and
embeddings (`nomic-embed-text`). **Upgrade path:** the gated `mios-llm-heavy`
vLLM lane for UI-TARS-1.5-7B-class heads (which need vLLM, not llama.cpp) when the
dGPU frees.

## Pieces already in place

* **Action engine — `mios-pc-control`** (`/usr/libexec/mios/mios-pc-control`)
  ships in the MiOS image, driven by `mios-pc-control.ps1` on the Windows side.
  Subcommands: `screenshot`, `click`, `double-click`, `mouse-move`, `type`, `key`,
  `key-combo`, `window-list`, `window-focus`, `window-move`, `window-resize`. All
  Win32 SendInput + GDI capture + EnumWindows via PowerShell. Same surface as
  Anthropic's Computer Use, but Windows-native.
* **Linux/Wayland action engine — `mios-computer-use`**
  (`/usr/libexec/mios/mios-computer-use`) is the cross-platform peer. It drives the
  local Wayland session via the RemoteDesktop + Screenshot/ScreenCast portals and
  AT-SPI (self-written evdev/uinput fallback — no ydotool/AGPL). The `cu_*` verbs
  (`cu_screenshot`, `cu_ground`, `cu_atspi_query`, `cu_window_list`, `cu_click`,
  `cu_type`, `cu_key`, `cu_key_combo`) dispatch to it. It is **environment-adaptive**
  (MiOS is ONE bootc image for any hardware): a reachable `executor_endpoint`
  (federation: drive another machine's desktop) wins; else the local Wayland
  session; else WSL2 delegates to `mios-pc-control`. AT-SPI-first grounding avoids
  pixels where possible; the vision VLM is the fallback.
* **Web-via-CDP — `mios-hermes-browser` + Hermes `browser_*` toolset.** Hermes's
  `browser_navigate` / `_snapshot` / `_click` / `_type` / `_vision` already work
  against the local ChromeDev with `--remote-debugging-port=9222` (auto-wired in
  `browser.cdp_url: http://localhost:9222`). It uses the **DOM/aria tree** as the
  primary grounding (cheap, no vision LLM needed) and falls back to
  `browser_vision` only when the page defeats DOM-based interaction (canvas apps,
  captchas, custom-rendered UIs).
* **Operator-side launcher broker — `mios-launcher-daemon`** bridges the WSL
  service-user perm wall so any agent action can reach the operator's WSLg session.
* **`mios-powershell`** + **`mios-windows {ps,cmd,launch}`** for raw shell-out to the
  Windows side.
* **opencode** (host install at `/usr/lib/mios/agents/opencode/bin/opencode`)
  served as a first-class OpenAI `/v1` council peer by
  `mios-opencode-gateway.service` (port key `opencode_gateway`); the orchestrator dispatches code-heavy
  work to it in parallel.

## What's bridged: screenshot → action

The bridge between **screenshot** (we have) and **action** (we have) must not
require Hermes's main reasoning model to be vision-capable — the everyday text
models on `mios-llm-light` are text-only. That bridge is `mios-pc-vision`
(`/usr/libexec/mios/mios-pc-vision`), the grounding shim, backed by a dedicated
vision lane.

### How the grounding lane is served (current)

Because all everyday inference runs on `llama.cpp` behind the
[mios-llm-light](https://github.com/mostlygeek/llama-swap) proxy image, the grounding
model is just another entry in the lane's model map:

* **Config:** [`usr/share/mios/llamacpp/mios-llm-light.yaml`](../../llamacpp/mios-llm-light.yaml)
  carries a `qwen3-vl:4b` entry (with its `--mmproj` projector) that mios-llm-light
  loads on demand. It is INERT until both GGUFs exist under `/models` (the operator
  downloads them; the security classifier blocks the fetch for the build
  assistant). Once present, `cu_ground` / `mios-pc-vision`'s vision fallback
  activates — the endpoint already points at the `llm_light` port.
* **`mios-pc-vision` resolution:** it reads `vision_grounding_endpoint`
  (the `mios-llm-light` lane, port key `llm_light`) and
  `vision_grounding_model` from the layered `mios.toml` resolver — TOML-first, no
  env-var hardcoding.
* **Heavy-lane alternative:** grounding heads that need vLLM rather than llama.cpp
  (UI-TARS-1.5-7B, GUI-Actor, Holo1.5) target the gated `mios-llm-heavy` lane
  at `grounding_endpoint = http://localhost:${MIOS_PORTS_VLLM}/v1`, `grounding_model =
  mios-grounding`. To serve one, set `vllm_bake_model` to the head and
  `vllm_served_name = "mios-grounding"` in the `[ai.vllm]` overlay and enable
  `mios-llm-heavy.service` when VRAM frees.

### The shim contract

```
mios-pc-vision <screenshot.png> "<query>"
  -> calls /v1/chat/completions on the resolved grounding lane with
     [system: "You are a UI grounding model. Given a screenshot +
      query, return the {x, y} click coordinates of the matching
      element."]
  -> returns JSON { "x": ..., "y": ..., "confidence": ..., "reasoning": ... }
```

The agent's loop becomes (Windows path shown; the `cu_*` verbs are the
Linux/Wayland equivalent):

```
mios-pc-control screenshot /tmp/screen.png
mios-pc-vision /tmp/screen.png "the OK button"
  -> {"x": 814, "y": 562, "confidence": 0.92, ...}
mios-pc-control click 814 562
mios-pc-control screenshot /tmp/screen.png    # verify
```

## Hermes + opencode division of labor

With `mios-pc-vision` (and its Linux `cu_ground` peer) in place, the
architecturally clean orchestration is:

| Phase | Owner | Tools |
|---|---|---|
| **Plan**: decompose "open Notepad and type 'hello'" into clicks/keys | Hermes (text reasoner on `mios-llm-light`) | `todo`, `delegate_task`, reasoning |
| **Ground** each step: where IS the Notepad icon? | `mios-pc-vision` / `cu_ground` (grounding VLM on `mios-llm-light`, port key `llm_light`, or the gated vLLM lane, port key `vllm`) | screenshot → vision LLM call |
| **Act**: clicks + keystrokes | `mios-pc-control` (Win32 SendInput) / `mios-computer-use` (portal + AT-SPI) | — |
| **Verify**: screenshot + diff against goal | Hermes + vision grounding | — |
| **Code-gen** when a task wants a script (PowerShell, Python) | opencode `/v1` peer | orchestrator-dispatched (port key `opencode_gateway`) |

For BROWSER tasks (URL nav, form fill, scraping), skip the vision loop entirely —
Hermes's `browser_*` toolset uses DOM/aria references which are deterministic and
don't need a vision LLM. The vision loop is only needed for canvas apps + native
Win32/Wayland GUIs.

## Enabling the grounding lane (operator-side, when ready)

```
# 1. Provision the vision GGUF + projector under /models (operator-fetched):
#      holo1.5-7b.gguf  +  holo1.5-7b-mmproj-q8_0.gguf  (~5.7 GB; H Company Holo1.5-7B,
#      mradermacher/Holo1.5-7B-GGUF -- or the Holo1.5-3B pair for the 8GB low-end)
#    (the qwen3-vl:4b served entry in mios-llm-light.yaml is already wired but INERT
#     until both files exist; the served tag stays "qwen3-vl:4b", backing model is Holo1.5)

# 2. Point mios-pc-vision at the lane via mios.toml [ai] overlay:
#      vision_grounding_model    = "qwen3-vl:4b"
#      vision_grounding_endpoint = "http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1"   # mios-llm-light
#    (or, for a UI-TARS-class head on vLLM, enable mios-llm-heavy and set
#     grounding_endpoint = http://localhost:${MIOS_PORTS_VLLM}/v1 / grounding_model =
#     "mios-grounding")

# 3. Author / refine a pc-control SKILL in pgvector (the agent's skill store)
#    documenting the screenshot -> ground -> click -> verify loop pattern.

# 4. Test against a known target:
#    mios-pc-control screenshot /tmp/desktop.png
#    mios-pc-vision  /tmp/desktop.png "the Start button"
#    mios-pc-control click <x> <y>
#    # Linux/Wayland: cu_screenshot / cu_ground / cu_click
```

## Why local-only is feasible NOW (vs. 12 months ago)

* **UI-TARS-1.5-7B** (ByteDance, Apache 2.0, May 2025) was the first open vision
  model trained specifically on PC GUI traces — before it, local PC control needed
  Set-of-Mark prompting hacks on top of general vision LLMs. Direct coordinate
  output makes the loop efficient.
* **Qwen3-VL 4B** (Alibaba, late 2025) brings native 2D grounding to a
  4B-parameter footprint that fits alongside the everyday models on a single 4090
  — and, crucially, loads on mainline `llama.cpp`, so it serves from the primary
  `mios-llm-light` lane without needing the heavy vLLM lane.
* **Hermes's local browser path** (Playwright-driven local Chromium via CDP)
  eliminates the Browserbase dependency for web tasks; combined with the desktop
  loop above, the entire PC-Control surface stays on-host.

## Architectural invariants (don't violate)

These are the relevant slices of the six MiOS Architectural Laws (USR-OVER-ETC ·
NO-MKDIR-IN-VAR · BOUND-IMAGES · BOOTC-CONTAINER-LINT · UNIFIED-AI-REDIRECTS ·
UNPRIVILEGED-QUADLETS):

* **mios.toml first.** The grounding model + lane endpoint must be declared in
  `mios.toml` (`[ai]` / `[computer_use]`), not hardcoded in scripts. `mios-pc-vision`
  reads its model name + base URL from the layered TOML resolver, not env-var
  defaults.
* **BOUND-IMAGES (Law 3).** New inference images and baked GGUFs go through the
  build pipeline / bake step, not runtime pulls — every Quadlet image ships *inside*
  the OCI image.
* **UNIFIED-AI-REDIRECTS (Law 5).** The grounding lane is reached through a MiOS
  endpoint resolved from config, never a vendor-hardcoded URL; like every agent and
  tool, the surface ultimately resolves to `MIOS_AI_ENDPOINT`.

## References

| # | Source | Notes |
|---|---|---|
| 1 | https://ollama.com/blog/qwen3-vl | Qwen3-VL launch + GUI agent capabilities (the model; MiOS serves the GGUF on llama.cpp, not Ollama) |
| 2 | https://github.com/bytedance/UI-TARS | UI-TARS model; Win/macOS/Web training |
| 3 | https://github.com/bytedance/UI-TARS-desktop | UI-TARS-Desktop reference impl |
| 4 | https://github.com/mostlygeek/llama-swap | The llama.cpp multi-model proxy MiOS uses for the `mios-llm-light` lane (incl. the `qwen3-vl:4b` grounding entry) |
| 5 | https://hermes-agent.nousresearch.com/docs/user-guide/features/browser | Hermes `browser_*` tool surface |
| 6 | https://github.com/NousResearch/hermes-agent/issues/374 | Local browser backend (Playwright) tracking |
| 7 | https://github.com/NousResearch/hermes-agent/issues/6780 | Auto-detect local Chrome CDP (handled in MiOS via `mios-hermes-browser.service`) |
| 8 | https://github.com/anomalyco/opencode/pull/7302 | opencode native browser tools (Playwright; alternative to Hermes `browser_*` path) |
| 9 | https://github.com/microsoft/UFO | Windows-specific reference (Hybrid UIA + Vision); architecturally similar to `mios-pc-control` + `mios-pc-vision` |
| 10 | https://github.com/openinterpreter/open-interpreter | Vision-mode reference; uses screenshot + cloud vision; MiOS replaces with local Qwen3-VL or UI-TARS |
| 11 | https://github.com/vllm-project/vllm | Engine behind the gated `mios-llm-heavy` grounding lane (UI-TARS-class heads need vLLM, not llama.cpp) |
