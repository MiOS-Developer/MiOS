<!-- AI-hint: Research for a MiOS "living wallpaper" -- a GPU-accelerated animated mesh-gradient background derived from the SSOT [colors] palette, cross-surface (Windows + Linux GNOME/Wayland + optional Hyprland/Quickshell), with a permissive-license triage (MiOS ships as a distributable OS, so Commons Clause / non-OSI deps are disqualifying). Feeds ROADMAP WS-WBRAND (WBRAND-04..06) / TASKS task_163..task_165. -->
<!-- AI-related: usr/share/mios/mios.toml ([colors]/[branding]/[theme]), ROADMAP.md (WS-WBRAND), usr/share/doc/mios/concepts/multi-agent-orchestration-strategies.md -->

# MiOS Living Wallpaper — Animated GPU Mesh-Gradient from SSOT Palette

**Source:** operator-provided research digest (mesh-gradient libraries → WebGPU on Linux iGPU → Hyprland/Quickshell animated wallpaper), filed "for later."
**MiOS fit:** MiOS already bakes a *static* gradient wallpaper (`Set-MiOSIdentityOffline` renders a `LinearGradientBrush` from `[colors].bg`→`[colors].accent`) and does matugen palette parity on Linux (WBRAND-02). This is the upgrade path: a **GPU-accelerated animated mesh-gradient** derived from the SAME SSOT palette, on every surface. **Later / P3 — not on the current critical path.**

---

## 0. License triage (BINDING — MiOS is a distributable OS)

MiOS ships as an image others install, so any vendored wallpaper engine must be **truly permissive (OSI: MIT / Apache-2.0 / BSD)**. A **Commons Clause** or other "no-sell" rider is **disqualifying** — it forbids redistribution-for-value and is not OSI open source. **Verify each repo's actual LICENSE file before vendoring; do not trust a third-party summary** (licenses are frequently misreported).

| Project | Reported license | MiOS-vendorable? | Notes |
|---|---|---|---|
| `cristicretu/meshgrad` | MIT | ✅ (verify) | Zero-dep CSS mesh-gradient generator; WebGL/CSS, **not WebGPU** |
| `sFrady20/easy-mesh-gradient` | MIT | ✅ (verify) | TS, CSS mesh gradients |
| `JohnnyLeek1/React-Mesh-Gradient` | MIT | ✅ (verify) | React `<MeshGradientRenderer/>`; WebGL |
| `firecmsco/neat` | **MIT + Commons Clause** | ❌ **DISQUALIFIED** | WebGL vertex-shader 3D gradients; the Commons Clause forbids selling/redistribution-for-value — **do not vendor into a shipped OS** |
| `BabylonJS` | Apache-2.0 | ✅ (verify) | Full engine, **native WebGPU** + WebGL fallback; heavy |
| `magetsu002/qs-wallpaper-picker` | MIT | ✅ (verify) | Quickshell/Hyprland wallpaper controller (image/video) |
| `bjarneo/quickshell` | MIT | ✅ (verify) | Quickshell suite w/ a `backgrounds` fluid-shader module |
| custom WGSL/GLSL shader | (author's own) | ✅ | ~40 lines; no dependency, fully MiOS-owned — **preferred** |

**Takeaway:** the CSS/WebGL libs are permissive but not GPU-mesh via WebGPU. `neat` (the nicest-looking) is **out** on license grounds. The **cleanest MiOS path is a tiny self-authored shader** (no third-party license at all) fed by SSOT colors, or BabylonJS (Apache-2.0) if a full engine is wanted.

---

## 1. WebGPU on Linux with AMD/Intel iGPUs — the reality

- Needs modern **Mesa Vulkan drivers** (`mesa-vulkan-drivers`); MiOS already ships `[packages.gpu-mesa]` / `[packages.gpu-intel-compute]` / `[packages.gpu-amd-compute]`.
- Intel Iris Xe+ and AMD Radeon iGPUs translate WebGPU→Vulkan well; older Intel HD falls back to WebGL.
- In stable Linux browsers WebGPU is often flag-gated (`--enable-features=Vulkan,WebGPU`) and can conflict with Wayland — **a browser is the wrong host for a wallpaper anyway.**
- **Better on Linux: render the shader natively** (Qt6 RHI → Vulkan/OpenGL, or `wgpu`/Dawn) instead of a WebGPU-in-browser stack. WebGPU-in-browser is only the right host on the *Windows* side (WebView2/D3D12).

## 2. Implementation paths (per surface)

- **Windows (MiOS-XBOX / MiOS-Win):** a borderless WebView2 or a lightweight D3D/WebGPU canvas at the desktop-background z-order, OR (simplest, most compatible) a pre-rendered looping video set as an animated background. The current static JPG is the floor; the animated layer is the upgrade.
- **Linux GNOME/Wayland (MiOS default):** GNOME has no native shader-wallpaper API. Options: a GLSL shader rendered to a Wayland background layer via a tiny native helper, a `gnome-shell` extension, or an MPV video loop. Keep it optional + gated.
- **Linux Hyprland/Quickshell (optional MiOS desktop profile):** the cleanest native route — a Quickshell `ShaderEffect` (Qt6 QML) whose `fragmentShader` GLSL is compiled to the Vulkan/OpenGL RHI and runs on the iGPU. Community MIT configs (`magetsu002/qs-wallpaper-picker`, `bjarneo/quickshell` `backgrounds`) are references.
- **Universal fallback:** `mpvpaper` (`exec-once` in `hyprland.conf`) or QtMultimedia `MediaPlayer`/`VideoOutput` playing a pre-rendered mesh-gradient loop — zero shader risk, works on any GPU.

## 3. MiOS mapping + design

- **Single SSOT source:** the mesh colors come from `[colors].accent` / `[colors].bg` (+ matugen-derived tints) — the SAME values that drive the static wallpaper, DWM accent, and Linux palette. One palette, one living wallpaper, every surface. No hardcoded colors (Law 7).
- **Degrade-open ladder:** animated GPU shader → static SSOT gradient (today's baked JPG) → solid accent. Auto-drop on old-Intel-HD / no-Vulkan / battery-saver.
- **Gated + off by default:** `[branding].living_wallpaper` (bool) + `[branding].living_wallpaper_mode` (`shader` | `video` | `static`); performance/thermal opt-in (an iGPU shader wallpaper burns power).
- **Prefer self-authored shader** (no third-party license) or Apache-2.0 BabylonJS; **never `neat`** (Commons Clause).

---

## 4. Gap register → ROADMAP / TASKS

| Gap | Roadmap item | Task | Priority |
|---|---|---|---|
| SSOT-driven mesh-gradient shader (self-authored, permissive), cross-surface, degrade-open | WBRAND-04 | task_163 | P3 |
| Linux living wallpaper (GNOME layer helper / optional Quickshell ShaderEffect) | WBRAND-05 | task_164 | P3 |
| Windows animated background (WebView2/D3D canvas or video loop) + `[branding].living_wallpaper` SSOT | WBRAND-06 | task_165 | P3 |

All: SSOT-driven from `[colors]`, gated + off by default, degrade-open to today's static gradient, **permissive-license-only (verify LICENSE; exclude Commons Clause / non-OSI)**.

## 5. Sources (verify before adopting)
Reported MIT: cristicretu/meshgrad, sFrady20/easy-mesh-gradient, JohnnyLeek1/React-Mesh-Gradient, magetsu002/qs-wallpaper-picker, bjarneo/quickshell. Apache-2.0: BabylonJS. **MIT + Commons Clause (disqualified): firecmsco/neat.** WebGPU status: Dawn/Chrome impl-status; Mesa Vulkan on Intel Iris Xe / AMD Radeon. **Confirm each license from the repo's LICENSE file at vendor time.**
