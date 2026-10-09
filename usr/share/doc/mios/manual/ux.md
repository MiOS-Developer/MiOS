<!-- AI-hint: Manual pages distilled from the source comments of ux, sanitized, each passage anchored to the comment it came from. -->

# ux

### MiOS Audio Feedback Daemon & Harmonic PCM Synthesizer....

MiOS Audio Feedback Daemon & Harmonic PCM Synthesizer.

Provides subtle, non-intrusive auditory cues on task transitions:
- Events: `completed`, `started`, `requires_input`, `warning`, `failed`.
- Backends: PipeWire (`pw-play`), PulseAudio (`paplay`), or pure-Python 16-bit PCM synthesis.
- Generates mathematically crafted soft harmonic chimes with exponential decay.
- CLI synthesis tool to pre-render all system event sound files to `/usr/share/sounds/mios/`.

<!-- mios-src:59a4da56335b from usr/libexec/mios/ux/audio_feedback.py:5-13 -->

### MiOS Screen Lock Manager with Biometric & FIDO2...

MiOS Screen Lock Manager with Biometric & FIDO2 Authentication (task_407).

Manages biometric hardware sensor inspection (fingerprint fprintd, FIDO2/CTAP2 pam_u2f)
and PAM configuration stack generation for swaylock, hyprlock, and gdm-password.
Unconditionally preserves password authentication fallback (auth include system-auth)
to guarantee the operator is never locked out.

<!-- mios-src:a37e9239ccae from usr/libexec/mios/ux/biometric_lock.py:5-12 -->

### MiOS Btop System Monitor Theme Renderer (task_405). Renders...

MiOS Btop System Monitor Theme Renderer (task_405).

Renders btop system monitor theme file (etc/btop/themes/mios.theme) directly
from the mios.toml [colors] SSOT palette.
Ensures terminal system monitoring visually harmonizes with the operating
system color scheme with exact RGB hex mappings.

<!-- mios-src:ae046ec4dc93 from usr/libexec/mios/ux/btop_theme.py:5-12 -->

### MiOS Secret-Redacting Cross-Platform Clipboard Synchronizer...

MiOS Secret-Redacting Cross-Platform Clipboard Synchronizer (task_409).

Synchronizes clipboard buffers between host Wayland/X11 surfaces and guest virtual machines
(via SPICE vdagent, socket bridge, or guest agent) with automated token redaction.
Prevents accidental leakage of operator secrets (API keys, PAT tokens, private keys, AWS credentials)
into untrusted guest virtual machines.

<!-- mios-src:230d1abd1446 from usr/libexec/mios/ux/clipboard_sync.py:5-12 -->

### WS-DIFFCYCLE (task_412): Human-In-The-Loop Interactive Diff...

WS-DIFFCYCLE (task_412): Human-In-The-Loop Interactive Diff Auditor.
Provides terminal CLI ('mios diff audit') and Quickshell ('DiffReview.qml') back-end
for reviewing accrued boot-cycle diffs, approving safe/custom modifications,
rejecting suspicious mutations, and staging approved diffs for autonomous OCI image baking.

<!-- mios-src:17231f75610f from usr/libexec/mios/ux/diff_auditor.py:5-10 -->

### MiOS Editor AI Configuration Projector & Generator (task_404)....

MiOS Editor AI Configuration Projector & Generator (task_404).

Automatically projects IDE configuration settings for VS Code, Cursor, and Continue:
- Routes chat/orchestration requests to http://localhost:8700/v1 (agent-pipe / Hermes).
- Routes code completion and fast tab autocomplete to http://localhost:8500/v1 (mios-llm-light).
- Preconfigures nomic-embed-text for local embeddings without cloud dependencies.
- Guarantees 100% offline pair programming per Architectural Law 5 (UNIFIED-AI-REDIRECTS).

<!-- mios-src:d967224db2ea from usr/libexec/mios/ux/editor_config_gen.py:5-13 -->

### MiOS Fastfetch Configuration Generator. Projects host...

MiOS Fastfetch Configuration Generator.

Projects host hardware, bootc immutable image metadata, local AI inference lanes,
and SSOT color tokens into `config.jsonc` for Fastfetch system banner display:
- Includes OS, Kernel, Host, CPU, GPU, Memory hardware modules.
- Integrates custom MiOS modules: AI Engine, Active Model, Mesh Nodes, Bootc Image.
- Configures ANSI color mappings derived from `mios.toml` [colors].

<!-- mios-src:a156401edf63 from usr/libexec/mios/ux/fastfetch_gen.py:5-13 -->

### MiOS First-Boot Out-of-Box-Experience (OOBE) Wizard. Guides...

MiOS First-Boot Out-of-Box-Experience (OOBE) Wizard.

Guides the operator through initial system configuration upon first boot:
1. Welcome & System Hardware / Version Inspection
2. Operator Identity & Authentication (password hashing, SSH public keys)
3. Offline Network Setup (Wi-Fi SSID/PSK, SAE/WPA3, Ethernet DHCP)
4. AI Brain Configuration (primary inference lane mios-llm-light, default models, VRAM budget)
5. Finalize & Sentinel Creation (persisting /etc/mios/profile.toml and disabling firstboot unit)

<!-- mios-src:2bc0a4367ae8 from usr/libexec/mios/ux/firstboot_wizard.py:5-14 -->

### MiOS Offline Procedural Focus Audio Synthesizer (task_408)....

MiOS Offline Procedural Focus Audio Synthesizer (task_408).

Procedurally synthesizes 100% offline ambient soundscapes for deep focus programming:
- Pink noise (1/f spectral density via Voss-McCartney algorithm).
- Brown noise (1/f^2 random walk / leaky integrator).
- White noise (uniform stochastic).
- Rain (filtered droplet burst simulation).
- Ocean (slow sinusoidal low-frequency wave modulation).
- Binaural Alpha (10Hz frequency offset for relaxed focus).
- Binaural Theta (6Hz frequency offset for deep flow state).

Pure Python standard library implementation with zero cloud streaming or external dependencies.
Streams directly to PipeWire (pw-play) or exports standard 16-bit PCM WAV files.

<!-- mios-src:b46c26718678 from usr/libexec/mios/ux/focus_audio.py:5-19 -->

### MiOS High-DPI Dynamic Font Size Scaler (task_406). Calculates...

MiOS High-DPI Dynamic Font Size Scaler (task_406).

Calculates and applies optimal typography scaling for High-DPI (4K/Retina) vs
standard (1080p) displays across terminal and desktop surfaces.
Avoids fractional Wayland compositor scaling that causes blurry XWayland rendering;
instead projects crisp integer/point typography metrics into fontconfig and desktop settings.

<!-- mios-src:d9a9604f23d9 from usr/libexec/mios/ux/font_scaler.py:5-12 -->

### MiOS GNOME Shell Top-Panel Extension Manager & Projector...

MiOS GNOME Shell Top-Panel Extension Manager & Projector (task_403).

Generates, validates, and manages the native GNOME Shell extension package:
`/usr/share/gnome-shell/extensions/mios-status@mios-dev.org/`

Provides:
- metadata.json supporting GNOME Shell 45, 46, 47, 48.
- extension.js implementing an asynchronous PanelMenu.Button with non-blocking Soup.Session
  HTTP polling of the local agent stack (http://127.0.0.1:8700/v1 and http://127.0.0.1:8500/v1).
- stylesheet.css rendered dynamically with colors derived from mios.toml [colors] SSOT.
- Quick-launch dropdown links to Open WebUI, Cockpit, and Code-Server.

<!-- mios-src:ff702ee823c0 from usr/libexec/mios/ux/gnome_extension.py:5-17 -->

### MiOS Living Wallpaper Telemetry Modulator & Shader...

MiOS Living Wallpaper Telemetry Modulator & Shader Renderer.

Dynamically renders and modulates ambient procedural shaders (GLSL & WGSL) based
on real-time host compute load, memory pressure, and local LLM inference velocity:
- Generates GLSL fragment shader with SSOT palette uniforms and telemetry modulation.
- Generates WGSL shader for WebGPU backends.
- Generates standalone interactive HTML5 WebGL canvas living wallpaper preview.
- Telemetry sampler reads CPU/GPU metrics with <0.1% CPU overhead.

<!-- mios-src:4054f21b8c76 from usr/libexec/mios/ux/living_wallpaper.py:5-14 -->

### MiOS Desktop Notification Bridge & Human-in-the-Loop (HITL)...

MiOS Desktop Notification Bridge & Human-in-the-Loop (HITL) Alert Daemon.

Routes agent-pipe / Hermes deliberation milestones, critical errors, and HITL
approval prompts directly to native desktop notification services (org.freedesktop.Notifications):
- Supports severity categories: `low`, `normal`, `critical`.
- Supports actionable buttons: `Approve`, `Reject`, `Inspect`.
- Integrated token bucket rate limiter to prevent notification spam.
- Fallbacks: `notify-send`, `gdbus`, or in-memory structured JSON event log.

<!-- mios-src:b10ae979d71d from usr/libexec/mios/ux/notification_daemon.py:5-14 -->

### MiOS Status Bar AI Telemetry Component & QML Bridge....

MiOS Status Bar AI Telemetry Component & QML Bridge.

Surfaces live LLM inference rates, VRAM allocation, and agent deliberation states
to Quickshell / QML desktop panels and Waybar status bars:
- Surfaces active model identifier (e.g. mios-opencode, Qwen2.5-Coder-7B).
- Measures generation velocity (tokens/sec).
- Tracks GPU VRAM consumption (allocated MB / total MB / %).
- Reflects agent lifecycle states (idle, thinking, tool_calling, deliberating).
- Generates standalone Quickshell / QML component with SSOT palette tokens.

<!-- mios-src:5fc4ba133859 from usr/libexec/mios/ux/status_bar.py:5-15 -->

### MiOS Cross-Platform Theme & Palette Synchronizer....

MiOS Cross-Platform Theme & Palette Synchronizer.

Synchronizes canonical palette tokens from `mios.toml` [colors] directly into:
1. Windows Registry structures (.reg and HKCU winreg API):
   - Personalize (AppsUseLightTheme, SystemUsesLightTheme, ColorPrevalence)
   - DWM (AccentColor, ColorizationColor)
   - Console/MiOS (ColorTable00..ColorTable15 in 0x00BBGGRR DWORD format)
2. GTK 3 CSS stylesheet (@define-color macros)
3. GTK 4 CSS stylesheet (:root CSS variables and window styling)

<!-- mios-src:7c79da2c39fe from usr/libexec/mios/ux/theme_sync.py:5-15 -->

### MiOS Tmux Theme & Status Line Generator. Projects canonical...

MiOS Tmux Theme & Status Line Generator.

Projects canonical palette tokens and styling preferences from `mios.toml` [colors]
and [theme] directly into `.tmux.conf` syntax:
- Pane borders: active (`cursor`), inactive (`muted`).
- Status line: background (`bg`), foreground (`fg`), selection (`accent`).
- Powerline glyph transitions (``, ``, ``, ``).
- Dynamic session and host indicators.

<!-- mios-src:be4aa5930b0f from usr/libexec/mios/ux/tmux_theme.py:5-14 -->

### MiOS Living Wallpaper Occlusion Engine Daemon...

MiOS Living Wallpaper Occlusion Engine Daemon (mios-wallpaperd).

Renders procedural ambient shaders on the desktop background with real-time Wayland
layer-shell occlusion awareness and low-priority Vulkan compute scheduling:
- Throttles rendering to 0 FPS (0.0% GPU load) when desktop is occluded by open windows.
- Resumes full 60 FPS (<2.0% GPU load, nominal 1.8%) when desktop/wallpaper is visible.
- Dispatches compute shaders on low-priority Vulkan compute queue (VK_QUEUE_GLOBAL_PRIORITY_LOW_EXT)
  to ensure AI inference lanes (llama.cpp / vLLM / SGLang) retain 98%+ GPU capacity.
- Serves IPC telemetry uniforms and status over Unix domain socket (/run/user/$UID/mios-wallpaper.sock).
- Provides comprehensive CLI controls (--status, --json, --socket, --set-occluded, --mock, --daemon).

<!-- mios-src:a1a6beda46ac from usr/libexec/mios/ux/wallpaperd.py:5-16 -->

### MiOS Window Manager (Hyprland & Sway) Configuration...

MiOS Window Manager (Hyprland & Sway) Configuration Generator.

Projects keybindings, gaps, border widths, animations, and SSOT palette tokens
into native compositor configuration files:
- Hyprland: `usr/share/mios/hyprland/hyprland.conf`
- Sway: `usr/share/mios/sway/config`
- Live reload trigger via `hyprctl reload` or `swaymsg reload`.
- Synchronizes behavior, workspaces, and shortcuts across both compositors.

<!-- mios-src:a2e9d255e228 from usr/libexec/mios/ux/wm_config_gen.py:5-14 -->
