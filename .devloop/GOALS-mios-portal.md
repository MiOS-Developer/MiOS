<!-- AI-hint: MiOS-Portal goal (Firefox + Surfer built as the combined MiOS desktop GUI app); layout sketch /home/mios/mios-desktop-work-1/design-2026-10-16/4-zen-layout.jpg. -->
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

## Operator decisions (2026-10-16)
- **Patch base:** first inventory what Surfer itself provides (its repo/npm package resources: project template, branding scaffold, patch/import helpers, sample configs, docs). Use those resources where they exist; if Surfer offers nothing usable for a piece, MiOS owns a minimal patch set on stock Firefox (not a fork of Zen's patch set).
- **Profile:** one profile; the Portal is the pinned home/new tab.
- **Chat panel:** one SSOT key, default the Portal chat on agent-pipe (:8700); no Mozilla cloud AI.
- **Zen:** stays installed but stops being default once MiOS-Portal passes MP-8 live; removed and [browser.zen] retired only after operator approval.
- **Defaults taken from the plan:** name "MiOS Portal", appId os.mios.portal, binary/Wayland class mios-portal, profile root ~/.mios-portal; Host.qml kept one release as fallback; Linux only (Windows feasibility note; Android only its URL from SSOT); off-image capped builder (localhost/mios-portal-build), artifact-build spike first with capped full compile as fallback.
