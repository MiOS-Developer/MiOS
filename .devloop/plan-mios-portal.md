<!-- AI-hint: MiOS-Portal plan: research summary, operator questions, resources and risk (2026-10-16). -->
# MiOS-Portal plan: Firefox + Surfer built as one MiOS desktop app (read-only planning phase)

The existing plan is a rewrite. `automation/67-bake-surfer.sh` has never built a browser. It builds the Surfer command-line tool and exits 0 (lines 137-156), and the phase is `fatal=false` (`globals.sh:716`, `mios.toml:14149`). Neither `/usr/lib/mios/webshell` nor `/usr/bin/mios-webshell` exists on this host.

The plan keeps what already works and turns it into one app. The Portal web UI (`portal.py`), the `mios-portal` window and class (`usr/bin/mios-portal`, `Host.qml`, `[desktop.portal]`) and the Zen chrome CSS and prefs (`gecko-userChrome.css.tmpl`, `[browser.zen]`) are reused. The new app is built with Surfer from stock Firefox, packaged into the image, and replaces both the QML Portal window and Flatpak Zen.

I read the sketch (`4-zen-layout.jpg`):
- **Outside the app:** a status bar on top and an auto-hiding dock at the bottom. The shell already owns both.
- **Left:** a "Zen sidebar" with a list of tabs, a collapse arrow, and a "tabs/apps" footer.
- **Centre:** web pages, with arrows showing they grow when the sidebars hide.
- **Right:** a "Chat" panel marked "Hides", with an input-and-send row at the bottom.

Two facts I checked myself:
- `static_linkage.rs:66-86` `load_exceptions` reads `[build.native.linux].exceptions[].binary`, but `mios.toml:2020-2031` has no `exceptions` key.
- `67-bake-surfer.sh` is listed in `MIOS_DOCS_PORT_CLEAN` (`globals.sh:1796`), so renaming the phase has to update that list.

---GOALS---
<!-- AI-hint: MiOS-Portal goal: build Firefox with Surfer as MiOS-Portal, the single MiOS desktop GUI app (Portal plus browser), from latest upstream, packaged into the MiOS image, replacing Zen; stopping conditions with positive and negative evidence, non-goals. -->
# MiOS-Portal goal

Operator objective (verbatim, 2026-10-16):

> instead of Zen--should be building the Firefox+Surfer(Zen's backend) to be built as MiOS-Portal(combined)(MiOS-Portal is the MiOS Desktop GUI App for the ecosystem)

> there's existing context in the codebase to refer to

Layout reference: `/home/mios/mios-desktop-work-1/design-2026-10-16/4-zen-layout.jpg`. The status bar on top and the auto-hiding dock belong to the shell. The app has a collapsible left sidebar (tabs, with an apps footer), web pages in the centre that grow when the sidebars hide, and a right Chat panel that hides, with an input and send row.

Start from the existing work. Do not invent a new design:
- the design doc `usr/share/doc/mios/concepts/mios-app-browser-portal-dashboard-design-2026-07-03.md`;
- `automation/67-bake-surfer.sh`, the "MiOS Webshell" bake;
- the Portal server `usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py`;
- the native window `usr/bin/mios-portal` + `usr/share/mios/portal/Host.qml`;
- `[browser.zen]`, `gecko-userChrome.css.tmpl` and `mios-theme-render --web`;
- task_2033 and task_2036, ROADMAP TD-4, and the UISHELL-* tasks.

## Standing rules (operator)

- **Latest upstream, no pins.** The SSOT always pulls the latest upstream: Firefox from product-details, Surfer from npm `latest`, and the matching Firefox release revision. Resolved versions are recorded only in build output (`record_version`). There are no literal version fallbacks; if a lookup fails, the build fails.
- **Everything comes from the layered SSOT** (vendor -> host -> user): brand, appId, binary name, profile root, prefs, policies, start and new-tab URL, AI chat endpoint, ports, palette and opacity. Templates go through the dotfiles registry, with no hardcoded hex or ports.
- **Fixed in MiOS code**, never only on the live host.
- **SSOT writes are atomic** (temp file + rename, check the size first).
- **Native code policy.** Law 14 / ADR-0021 applies to MiOS-built native code. Gecko is an upstream, dynamically linked app and gets an explicit, visible SSOT exception. It must not slip past the name filter silently.
- **Law 12 (bake, don't fetch).** The image build does not fetch or compile Gecko. A separate, memory-capped builder produces an artifact, and the image phase installs it.
- **Resource safety.** Heavy builds run under `systemd-run --scope -p MemoryMax=… -p MemorySwapMax=0` plus a heavy-build lock. Nothing walks `/`.
- **Completion needs both controls.** A command finishing is not completion. Each condition needs a positive control and a negative control: a planted defect must make the check fail.

## Stopping conditions

| ID | Condition | Evidence that closes it |
|----|-----------|-------------------------|
| MP-1 | **One SSOT spec.** A single SSOT table declares MiOS-Portal: identity (name, vendor, appId, binary `mios-portal`, brand), source (Surfer npm package, Firefox product), build lane, profile root, prefs, policies, start/new-tab URL and chat endpoint. `surfer.json`, mozconfig, `policies.json`, `defaults/pref` and the chrome CSS are rendered from it through the registry. The hardcoded `MIOS_SURFER_*` defaults, the `153.0` fallbacks and the hex fallbacks in phase 67 are gone (closes task_2033). | **Positive:** `mios-gen sync` renders every product file, and the conformance and drift gates pass. **Negative:** planting a literal Firefox version, a `#rrggbb` value or a port literal in the product tree or recipe fails the pins, hex-leak (task_2036) or port gate. |
| MP-2 | **Builds from latest upstream.** A fresh build resolves the latest Firefox, Surfer and release revision at build time, runs `surfer download -> import -> build -> package`, and produces a `mios-portal` Linux tarball plus a manifest of resolved versions. | **Positive:** builder log + manifest show versions equal to product-details and npm `latest` on that day. **Negative:** a patch that no longer applies, or a failed version lookup, makes the build exit non-zero (no exit 0 after a warning). |
| MP-3 | **Packaged into the image, fails visibly.** The image phase (renamed from 67-bake-surfer) installs the artifact under `/usr/lib/mios/portal/`. It is `fatal=true`, its `apply_class` matches between the script and the SSOT, it does no network access, and it leaves no build trees in the image. fapolicyd trusts the new path. | **Positive:** the built image contains `/usr/lib/mios/portal/mios-portal`, and the binary runs under fapolicyd enforcing. **Negative:** removing the artifact makes the image build fail; an exec from the old `/usr/lib/mios/webshell` path is denied. |
| MP-4 | **Linkage policy is explicit.** `[[build.native.linux.exceptions]]` names the Gecko binaries (`mios-portal`, `libxul.so`, `plugin-container` and the others) with a reason. The gate's census reports them as exempt, and the drift docs record the scope. | **Positive:** `mios-gate static-linkage --census` lists them as exempt. **Negative:** an unlisted dynamic binary put in the gate's scope fails; removing the exception while the binary is in scope fails. |
| MP-5 | **The sketch layout, from the SSOT.** In the running app: a left vertical tab sidebar with an apps footer, collapsible; the centre content goes edge to edge when the sidebars hide; a right Chat panel that auto-hides with a peek strip and has an input row; compact density; no window controls. The theme is MiOS glass (transparent for Hyprglass), and the palette and opacity come from the SSOT. | **Positive:** a live screenshot plus a DOM/pref probe show each element, with the tab sidebar and the chat panel on opposite sides. **Negative:** setting the chat side or auto-hide key to its opposite value in the SSOT changes the render; a palette change in the SSOT changes the chrome colours after a re-render. |
| MP-6 | **Every Portal feature works inside it.** The home/new tab is the Portal (SSOT URL). Login survives a restart, and the cookie and Bearer token both work. The ttyd terminals over `/portal/term`, Swarm, Services embeds, Apps search, the configurator at `/configure` (including the miosd `?token=` path), `POST /portal/config`, chat, the PWA manifest and the service worker all work. The apps sidebar lists the Portal apps. | **Positive:** an automated probe (Marionette/WebDriver BiDi, or `test_mios_portal*` extended) drives each feature green, and the existing `test_mios_portal*` and `test-http-error-safety` tests pass. **Negative:** a wrong password is rejected; an expired token is rejected after a restart; another site's page cannot read the `mios_portal` cookie. |
| MP-7 | **The chat panel uses the MiOS AI front door.** The chat sidebar provider is the SSOT AI endpoint. No Mozilla cloud AI endpoint is reachable from the defaults (`browser.smartwindow.*` is disabled or redirected), which fixes the Law 5 leak. | **Positive:** a chat round trip lands in the agent-pipe/OWUI logs. **Negative:** a pref/policy dump contains no `mozilla` AI host, and an egress check while chatting shows no connection to Mozilla AI hosts. |
| MP-8 | **One app replaces Zen and the QML host.** `mios-portal` (launcher, `.desktop`, `StartupWMClass`, Wayland `app_id=mios-portal`, single instance, `--url`) runs the Gecko build. It is the default handler for http/https/html (rendered `mimeapps.list`/`xdg-settings`), `[aliases].browser`, `mios-find web`, `[[desktop.apps]]`, the unified `app_types` and the dock pin. Settings and `mios-configurator-launch` open in it. Zen is no longer referenced by any launcher, alias or default. | **Positive:** `xdg-open https://…` and `xdg-settings get default-web-browser` resolve to `mios-portal`; `hyprctl clients` shows class `mios-portal`; a second launch focuses the existing window. **Negative:** a grep gate finds no `mios-zen`/`zen` default anywhere in rendered output; `mios-app-default` writes are atomic, and a planted duplicate `app_types` block fails the lint. |
| MP-9 | **Stale paths are retired and docs are current.** No mios-webshell or `os.mios.webshell` references remain (`wm_config_gen.py`, `hyprland.conf`, fapolicyd, docs). The design doc §8/§12b are revised to describe one combined app. `automation.md`, ROADMAP TD-4 and the UISHELL tasks are reconciled. | **Positive:** the doc-drift and stale-ref gates are green. **Negative:** planting `mios-webshell` in a tracked file fails the stale-ref gate. |
| MP-10 | **In the codebase, gated, merged.** All code is MiOS code, the full gate suite is green, the image is built and booted on this host with MP-3, MP-5, MP-6, MP-7 and MP-8 verified live, and the PR is merged to main. | PR URL, green gates, live probe log |

## Non-goals
- Windows and Android builds of MiOS-Portal in this goal. A Windows feasibility note is the only Windows deliverable; the Android WebView wrapper only gets its `portal_url` rendered from the SSOT.
- A full from-source compile with Zen's release settings (LTO "cross,full" plus PGO). It does not fit the memory caps.
- Forking or vendoring the whole Zen tree (263 patches and about 1,800 files) unless the operator chooses that (Q1).
- Mozilla or Zen trademarks, names or icons in the shipped product.
- Rewriting the Portal server, the configurator or the Quickshell shell (status bar, dock).
- Removing Flatpak Zen before MP-8 passes and the operator approves.
- Flatpak/AppImage packaging, or an in-app updater (the image is the updater: `DisableAppUpdate`).
---END-GOALS---

## Operator questions (each has a recommended default)

1. **Patch base.** Option A: a MiOS-owned minimal Surfer patch set on stock Firefox. Option B: vendor or fork Zen's `desktop` `src/`.
   - **Recommended: A.** It is branding, policies, prefs, a few chrome CSS/JS patches, and Zen's existing selectors ported to Firefox's vertical-tabs sidebar.
   - Firefox 136-158 already provides a left vertical-tab sidebar with expand-on-hover, the `ml.chat` sidebar and compact density.
   - Zen means rebasing 263 C++/JS patches on every Firefox release and rules out artifact builds.
   - Individual Zen JS features stay available as MPL-2.0 files copied with their headers.
2. **Build lane.** Option (i): a Firefox artifact build (frontend only; about 10-15 min, about 4 GB RAM, about 12 GB disk). Option (ii): a capped full compile (about 1-1.5 h at `-j6`, 13 GB cap, 35-45 GB disk).
   - The research disagrees on whether artifact mode works through Surfer's tarball download. One report says it does if `MOZ_ARTIFACT_REVISION` is set to the release-tag commit; the other says it does not, because the tree has no VCS history.
   - **Recommended:** run a spike (mp-04). Use (i) if it works and (ii) as the fallback. Both run off-image in a capped builder, never in the Containerfile.
3. **Naming and identity.**
   - **Recommended:** product "MiOS Portal", appId `os.mios.portal`, binary and Wayland class `mios-portal` (matches the existing Hyprland rule `mios.toml:7572`), profile root `~/.mios-portal`.
   - Retire "MiOS Webshell" and `os.mios.webshell`.
4. **Profile model.** The Portal session lives in the same profile as everyday browsing.
   - **Recommended:** one profile. The origin rules plus the HttpOnly/SameSite cookie already isolate it, and the Portal is pinned as home/new tab.
   - Alternative: a separate `--profile` instance for a dedicated Portal window, which keeps today's Host.qml isolation.
5. **Chat endpoint.** The chat panel points at Open WebUI `:8200`, which today's Zen, the Portal chat and phase 67 all use, or at the Portal's own chat or the agent-pipe front door.
   - **Recommended:** a single SSOT key that defaults to the Portal chat view on agent-pipe `:8700`. That keeps one front door (Law 5) and the same UI in both places.
6. **Zen during the transition.**
   - **Recommended:** keep Flatpak Zen installed but not default until MP-8 passes live, then remove it from the flatpak list and retire `[browser.zen]`, with your approval.
7. **Host.qml fallback.**
   - **Recommended:** keep the QML host only as a launcher fallback for one release cycle (used when the Gecko artifact is absent), then delete it.
8. **Windows and Android.**
   - **Recommended:** Linux only for now. Windows gets a feasibility note, because §8 earlier rejected a 30-40 GB Windows build and Windows artifact builds may change that answer. Android stays a WebView wrapper, with only its URL rendered from the SSOT.
9. **Builder location.**
   - **Recommended:** a `localhost/mios-portal-build` image (57-mios-sys-build style). It runs on `/var/lib/mios-build`, with podman `--memory 14g --memory-swap 14g --cpus 8` plus a `systemd-run` scope `MemoryMax=13G MemorySwapMax=0`, and a new `flock /var/lib/mios-build/heavy.lock` written into MiOS code.
   - The image phase consumes its tarball from the build context.

---TASKS---
{"id":"mp-01","title":"SSOT: add [desktop.portal.gecko] spec (identity, source, lane, profile_root, prefs, policies, home/newtab URL, chat endpoint key) and bake-ref keys resolved at build time; remove MIOS_SURFER_* env defaults and 153.0 fallbacks","stopping_condition":"MP-1","files":["usr/share/mios/mios.toml","automation/lib/globals.sh","automation/lib/globals.ps1","tools/native/mios-bake-plan/src/latest.rs","automation/67-bake-surfer.sh"],"depends_on":[],"acceptance":{"positive":"mios-gen sync renders the new keys into globals; mios-bake-plan resolves the latest Firefox (product-details), the latest Surfer (npm) and the release-tag commit, and prints them to build output only","negative":"a literal Firefox version or Surfer version in the SSOT is rejected by the pins gate; a resolver failure exits non-zero instead of falling back"},"est_hours":4,"risk":"medium"}
{"id":"mp-02","title":"Gates: explicit [[build.native.linux.exceptions]] for Gecko binaries with reason plus documented scope; product tree allowlisted in check_target_languages (patch/js/css/ftl only); phase apply_class script-vs-SSOT drift check; hex-leak gate (task_2036) covering the product tree","stopping_condition":"MP-4","files":["usr/share/mios/mios.toml","src/mios-rs/mios-gate/src/static_linkage.rs","automation/98-drift-checks.sh","src/mios-rs/mios-gate/src/drift/governance.rs","usr/share/doc/mios/adr/"],"depends_on":["mp-01"],"acceptance":{"positive":"mios-gate static-linkage --census lists mios-portal/libxul.so/plugin-container as exempt with reason; drift checks green","negative":"an unlisted dynamic ELF placed in scope fails the gate; a planted .cpp in the product tree fails; a script header apply_class that disagrees with the SSOT fails"},"est_hours":4,"risk":"low"}
{"id":"mp-03","title":"Product tree src/mios-portal/: surfer.json.tmpl, configs/{common,linux}/mozconfig.tmpl (no LTO/PGO, lld, -j from SSOT), MiOS branding dir (no Mozilla/Zen marks), distribution/policies.json.tmpl (Homepage, NewTabPage, DisableAppUpdate, DisableTelemetry, GenerativeAI, Preferences), defaults/pref/mios-portal.js.tmpl; registry entries [dotfiles.registry.portal-*]; MPL headers","stopping_condition":"MP-1","files":["src/mios-rs/../../src/mios-portal/","usr/share/mios/theme/templates/","usr/share/mios/mios.toml"],"depends_on":["mp-01","mp-02"],"acceptance":{"positive":"the registry renders every product file from the SSOT; surfer license-check passes; rendered surfer.json carries the resolved version only in the build copy","negative":"the hex/port/pin gates fail on a planted literal; the build fails when any template variable is missing (no silent default)"},"est_hours":6,"risk":"medium"}
{"id":"mp-04","title":"Spike (capped, off-image): can Surfer produce an artifact build (MOZ_ARTIFACT_REVISION=release-tag commit, or MOZ_ARTIFACT_URL), does --with-app-name=mios-portal rename the artifact executable, and what does mach package name the output; record the lane decision in the SSOT","stopping_condition":"MP-2","files":["src/mios-portal/configs/","usr/share/mios/mios.toml",".devloop/LEDGER.md"],"depends_on":["mp-03"],"acceptance":{"positive":"a documented run (log, peak RSS, disk, wall time) produces a launching mios-portal binary in either lane; the SSOT lane key is set from the result","negative":"the run is aborted by the MemoryMax scope rather than freezing the host (cap verified); a C++ change in artifact mode is detected and refused"},"est_hours":6,"risk":"high"}
{"id":"mp-05","title":"Builder: localhost/mios-portal-build image plus a Justfile recipe; heavy.lock flock helper in MiOS code; systemd-run scope MemoryMax=13G MemorySwapMax=0, podman --memory 14g --memory-swap 14g --cpus 8; MOZ_OBJDIR and ~/.mozbuild under /var/lib/mios-build; distro devel deps from the SSOT (pulseaudio-libs-devel, alsa-lib-devel, dbus-glib-devel); output tarball plus resolved-versions manifest","stopping_condition":"MP-2","files":["Justfile","automation/lib/","tools/","usr/share/mios/mios.toml"],"depends_on":["mp-04"],"acceptance":{"positive":"just portal-build produces dist/mios-portal-<resolved>.linux-x86_64.tar.xz plus a manifest whose versions equal upstream latest","negative":"a planted non-applying patch makes the build exit non-zero; a second concurrent heavy build blocks on heavy.lock"},"est_hours":8,"risk":"high"}
{"id":"mp-06","title":"Replace phase 67-bake-surfer with 67-bake-portal: install the builder tarball into /usr/lib/mios/portal (no network, no compile), fatal=true, apply_class consistent, cleanup; fapolicyd rule retargeted; update MIOS_DOCS_PORT_CLEAN, build_phases.json, automation.md","stopping_condition":"MP-3","files":["automation/67-bake-surfer.sh","automation/67-bake-portal.sh","usr/share/mios/mios.toml","automation/lib/globals.sh","etc/fapolicyd/fapolicyd.rules","usr/share/doc/mios/manual/automation.md","Containerfile"],"depends_on":["mp-05"],"acceptance":{"positive":"the image contains /usr/lib/mios/portal/mios-portal, which execs under fapolicyd enforcing; no surfer or engine trees in any layer","negative":"a missing artifact fails the image build; an exec from /usr/lib/mios/webshell is denied"},"est_hours":4,"risk":"medium"}
{"id":"mp-07","title":"Chrome layout per sketch: port gecko-userChrome.css.tmpl from Zen selectors to Firefox sidebar.revamp (vertical tabs left, expand-on-hover collapse, apps footer); split the chat panel to the right via CSS order or a small browser-sidebar JS patch; auto-hide with peek; compact density; hidden window controls; prefs from the SSOT (sidebar.visibility=expand-on-hover)","stopping_condition":"MP-5","files":["usr/share/mios/theme/templates/gecko-userChrome.css.tmpl","src/mios-portal/src/","usr/share/mios/mios.toml"],"depends_on":["mp-03"],"acceptance":{"positive":"a live screenshot shows tabs left and chat right, the centre growing when both hide, and a peek strip; a DOM probe confirms","negative":"flipping the SSOT chat-side or auto-hide key changes the render; the old Zen-only selectors are absent from the rendered CSS"},"est_hours":10,"risk":"medium"}
{"id":"mp-08","title":"Portal as home: Homepage/NewTabPage policy to the SSOT Portal URL; apps sidebar footer fed by /v1/app-search (bundled WebExtension sidebar_action or pinned tabs); configurator and mios-configurator-launch open in mios-portal; Portal adopts mios-app-shell.css; PWA kept for other clients","stopping_condition":"MP-6","files":["src/mios-portal/","usr/lib/mios/agent-pipe/mios_pipe/routing/portal.py","usr/lib/mios/agent-pipe/mios_pipe/routing/portal_assets.py","usr/libexec/mios/mios-configurator-launch","usr/share/mios/mios.toml"],"depends_on":["mp-06","mp-07"],"acceptance":{"positive":"a Marionette/BiDi probe passes login persisting across a restart, terminals over WS, Swarm, Services embeds, Apps search, /configure with ?token=, config save and status; test_mios_portal* pass","negative":"a wrong password and an expired token are rejected; a cross-origin page cannot read the mios_portal cookie"},"est_hours":10,"risk":"medium"}
{"id":"mp-09","title":"Chat sidebar to the MiOS AI front door: browser.ml.chat provider plus hideLocalhost=false from one SSOT key; disable or redirect browser.smartwindow.* (fixes the Law 5 leak, also on the current Zen/[browser_ai] path); reconcile UISHELL-03","stopping_condition":"MP-7","files":["usr/share/mios/mios.toml","src/mios-portal/distribution/","usr/libexec/mios/mios-theme-render","tasks.jsonl"],"depends_on":["mp-03"],"acceptance":{"positive":"a chat round trip is visible in the agent-pipe/OWUI logs","negative":"the pref dump has no Mozilla AI host; an egress capture during chat shows no Mozilla AI connections"},"est_hours":3,"risk":"low"}
{"id":"mp-10","title":"Theme and desktop integration: mios-theme-render --web targets the SSOT profile_root of mios-portal; glass transparency for Hyprglass with [theme.web] opacity; global menu through host dbusmenu directly (drop the Flatpak LD_LIBRARY_PATH copy for this app); miosd appmenu verified","stopping_condition":"MP-5","files":["usr/libexec/mios/mios-theme-render","usr/share/mios/theme/templates/mios-web.css.tmpl","src/mios-rs/miosd/src/appmenu.rs","usr/share/mios/mios.toml"],"depends_on":["mp-07"],"acceptance":{"positive":"a palette change in the SSOT re-renders chrome colours live; the window is translucent under Hyprglass; the global menu appears in the topbar","negative":"theme-render on a missing profile root fails visibly; no hex literal in rendered output outside the palette"},"est_hours":5,"risk":"medium"}
{"id":"mp-11","title":"Launcher: usr/bin/mios-portal execs the Gecko build (SSOT path), Host.qml fallback only if the artifact is absent; single instance through the remote/hyprctl focus; --url; Wayland app_id mios-portal (MOZ_APP_REMOTINGNAME / --name); [desktop.portal] runner keys updated","stopping_condition":"MP-8","files":["usr/bin/mios-portal","usr/share/mios/mios.toml","usr/share/mios/hyprland/hyprland.lua","usr/share/applications/mios-portal.desktop"],"depends_on":["mp-06"],"acceptance":{"positive":"hyprctl clients shows class mios-portal; a second launch focuses the existing window; --url prints the SSOT URL","negative":"with the artifact removed the launcher falls back and logs it; a wrong class fails the window-rule test"},"est_hours":3,"risk":"low"}
{"id":"mp-12","title":"Default-browser switch from Zen: unify the default-browser keys ([aliases].browser, mios-find web, [[desktop.apps]], the two app_types schemas into one); render mimeapps.list/xdg default; dock_pinned mios-zen -> mios-portal; Sidebar.qml mapping; mios-browser family/flags; make mios-app-default write atomically","stopping_condition":"MP-8","files":["usr/share/mios/mios.toml","usr/libexec/mios/mios-app-default","usr/share/mios/quickshell/Sidebar.qml","tools/native/mios-browser/","usr/share/applications/mios-zen.desktop"],"depends_on":["mp-11"],"acceptance":{"positive":"xdg-open https://… and xdg-settings get default-web-browser resolve to mios-portal; the dock shows mios-portal","negative":"a grep gate finds no zen default in rendered output; a planted duplicate app_types block fails ssot-lint; an interrupted mios-app-default write leaves mios.toml intact"},"est_hours":5,"risk":"medium"}
{"id":"mp-13","title":"Retire webshell and stale references: wm_config_gen.py and hyprland.conf rules/bind, os.mios.webshell, design doc revision (§8 one combined app, §12b, script names 67/65, hyprland.lua), ROADMAP TD-4, ROADMAP task_3235 port, UISHELL-01/02/04 reconciled, LEDGER entry closed; stale-ref gate","stopping_condition":"MP-9","files":["usr/libexec/mios/ux/wm_config_gen.py","usr/share/mios/hyprland/hyprland.conf","usr/share/doc/mios/concepts/mios-app-browser-portal-dashboard-design-2026-07-03.md","ROADMAP.md","TASKS.md","tasks.jsonl",".devloop/LEDGER.md","usr/share/doc/mios/manual/ch89-glass-desktop-and-session-resilience.md"],"depends_on":["mp-06"],"acceptance":{"positive":"doc-drift and stale-ref gates are green","negative":"a planted mios-webshell reference fails the stale-ref gate"},"est_hours":4,"risk":"low"}
{"id":"mp-14","title":"Android portal_url rendered from the SSOT (scope limited to the URL)","stopping_condition":"MP-9","files":["tools/mios-portal-app/res/values/strings.xml","usr/share/mios/mios.toml"],"depends_on":["mp-01"],"acceptance":{"positive":"the rendered strings.xml equals the SSOT URL","negative":"a hand-edited URL is reported as drift"},"est_hours":1,"risk":"low"}
{"id":"mp-15","title":"Image build and live verification on this host: build the image, boot, run the MP-3/5/6/7/8 probes with negative controls; screenshots of the layout","stopping_condition":"MP-10","files":["tests/test-mios-portal-gecko.py","tests/test-theme-live-render.py"],"depends_on":["mp-08","mp-09","mp-10","mp-12","mp-13"],"acceptance":{"positive":"all probes green on the booted image; resolved-versions manifest attached","negative":"each planted defect (wrong palette, chat on the left, missing artifact, Mozilla AI host, zen default) turns its probe red"},"est_hours":6,"risk":"medium"}
{"id":"mp-16","title":"Retire Zen after approval: remove app.zen_browser.zen from the flatpak list, [browser.zen] folded into the gecko table, _zen_global_menu Flatpak path removed, Windows [browser_ai] left as-is pending the Windows note","stopping_condition":"MP-8","files":["usr/share/mios/mios.toml","usr/libexec/mios/mios-theme-render","automation/61-flatpak-bake.sh"],"depends_on":["mp-15"],"acceptance":{"positive":"the image has no Zen flatpak; all gates are green","negative":"the zen grep gate is green with zero hits in Linux rendered output"},"est_hours":2,"risk":"low"}
{"id":"mp-17","title":"Windows feasibility note: artifact-build or repack path for MiOS-Portal on Windows vs the §8 / RESEARCH.md rejection; recommendation only","stopping_condition":"MP-9","files":["usr/share/doc/mios/concepts/mios-app-browser-portal-dashboard-design-2026-07-03.md"],"depends_on":["mp-04"],"acceptance":{"positive":"the note cites measured Linux lane costs and Mozilla Windows artifact support","negative":"no Windows build code lands in this goal"},"est_hours":2,"risk":"low"}
{"id":"mp-18","title":"Commit, PR, merge with the full gate suite green","stopping_condition":"MP-10","files":[],"depends_on":["mp-15","mp-16"],"acceptance":{"positive":"PR merged to main with green CI","negative":"CI fails on any planted gate defect in the PR's test fixtures"},"est_hours":2,"risk":"low"}
---END---

## Resources and risk

**Host and container today**
- The host has a 16-core/32-thread 9950X3D, 46 GB RAM (about 24 GB free when checked) and a 23 GB zram swap.
- `podman-MiOS-DEV` allows 16 GB RAM, 8 CPUs and **4 GB of swap**. It should be recreated with `--memory 14g --memory-swap 14g` so it has no swap.
- `/var/lib/mios-build` has 216 GB free.

**Build lanes (estimates; nothing was built)**

| Lane | Time | Peak RAM | Disk |
|---|---|---|---|
| Artifact build | 10-15 min | ~3-4 GB | ~10-12 GB |
| Full compile, `-j6`, no LTO/PGO, lld, no debug symbols | 65-90 min | 8-10 GB (peaks at the `gkrust` crate and the libxul link) | 35-45 GB (budget 60 GB) |
| Zen release settings (LTO + PGO) | 3 h+ | 20-30 GB+ | — |

- Zen's release settings do not fit and must never be enabled.
- Building into the image would break Law 12 and the `runner_disk_budget_gb=40` limit.

**Memory-cap settings**
- Run the build under `systemd-run --user --scope -p MemoryMax=13G -p MemorySwapMax=0 nice -n10 ionice -c3`.
- In the mozconfig: `MOZ_MAKE_FLAGS=-j6`, `MOZ_PARALLEL_BUILD=6`, `CARGO_BUILD_JOBS=4`, `--disable-lto`, `--enable-linker=lld`, `--disable-debug-symbols`, `--disable-tests`.
- Without wasi-sysroot, add `--without-wasm-sandboxed-libraries`.
- Take the `heavy.lock` flock, which is new code because the convention does not exist yet.
- Set `OOMPolicy=continue` on tmux scopes (see the OOM memory note).

**Main risks**
- **Artifact lane may not work through Surfer.** Surfer's tarball has no VCS history (mp-04 spike). Fallback: the capped full compile.
- **Every Firefox release can break patches.** Mitigation: keep the patch set small and fail loudly on a bad apply.
- **Firefox has no native split of tabs-left and chat-right.** It needs a CSS-order change or a small JS patch.
- **Hidden gate escape.** If `/usr/bin/mios-portal` ever becomes a Rust crate or is placed in `MIOS_STATIC_LINKAGE_DIR`, the gate will scan it. mp-02 documents this.
- **One profile shares Portal and browsing.** See Q4.
- **Law 5 leak already exists.** `browser.smartwindow` defaults point at Mozilla's cloud, today, on Zen too.

**Licence**
- Firefox and Surfer are MPL-2.0. Keep the MPL headers on all `src/` files and run `surfer license-check`. The public MiOS repo publishes the modified sources. Ship the `about:license` text.
- Use no Mozilla or Zen trademarks or icons: MiOS branding directory, no official branding.
- Redistributing Mozilla's artifact libxul is permitted under the MPL.
- Copy any Zen file one at a time with its header intact.