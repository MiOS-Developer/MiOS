<!-- AI-hint: Research spike on why the VS Code edge-to-edge terminal styling kept reverting on remote hosts, and the dotfiles projection fix. -->
# Technical Investigation & Research Spike: Edge-to-edge VS Code terminal keeps reverting

**Spike ID:** `SPIKE-20260924-vscode-custom-css-remote-host`
**Author / Driver:** GitHub Copilot (agent session)
**Status:** CONCLUDED
**Timebox:** ~45 minutes
**Target Completion Date:** 2026-09-24
**Related Epics / Lanes:** `.dotfiles` SSOT / devcontainer theme projection (task_476, task_2709)

---

## 1. Context & Motivation

The MiOS `.dotfiles` SSOT ships `usr/share/mios/themes/code-server-terminal.css`
(edge-to-edge, zero-padding terminal/panel rules) wired in via
`vscode_custom_css.imports` + `vscode_custom_css.policy: true`, applied by the
`be5invis.vscode-custom-css` extension. The user reports this genuinely worked
once, then reverted after a restart/reconnect, repeatedly, across sessions —
even after `tools/sync-dotfiles.py` was wired into `boot-mios-systems.sh` to
re-seed `settings.json` on every container start (commit `d7f65012`).

Unverified assumption going in: that re-seeding `settings.json` on every boot
is sufficient to keep the terminal edge-to-edge. It is not — `settings.json`
only tells the extension *what* to inject; it does not perform the injection.

If the wrong root cause is treated as "already fixed," every future session
will keep observing the same regression and burn agent turns re-diagnosing it.

---

## 2. Research Questions & Hypotheses

| Question ID | Research Question | Hypothesis (Initial Assumption) |
| :--- | :--- | :--- |
| **Q1** | Where does `be5invis.vscode-custom-css` v7.5.1 actually look for the workbench HTML it patches? | Assumed a client-side Electron install path, unreachable from the container. |
| **Q2** | Is this extension installed as a `workspace` (remote) or `ui` (local) extension in this environment? | Unknown — needed to inspect `.vscode-remote/extensions/`. |
| **Q3** | Does the active VS Code Server build even ship the directory structure the extension expects? | Unverified — needed to `find` the actual `out/` tree. |
| **Q4** | Is the revert-on-restart behavior a MiOS bug, or documented upstream extension behavior? | Assumed a MiOS wiring bug; needed to check the extension's own README. |

---

## 3. Experimental Methodology & Prototypes

No synthetic benchmark was needed; this was a live filesystem/source-code
inspection against the *installed* artifacts in this exact container:

- `product.json` of the active build: `/vscode/bin/linux-x64/c718b461a175300ec6f4949c808291d2149f3f8d-insider/product.json`
- Installed extension source: `/home/mios-dev/.vscode-remote/extensions/be5invis.vscode-custom-css-7.5.1/src/extension.js`
- Installed extension docs: `/home/mios-dev/.vscode-remote/extensions/be5invis.vscode-custom-css-7.5.1/README.md`
- Filesystem probes: `find /vscode/bin/linux-x64 -iname 'workbench.html'`, `ls .../out/vs/`

---

## 4. Empirical Findings & Benchmarks

### 4.1 Quantitative Data

| Probe | Result |
| :--- | :--- |
| `find /vscode/bin/linux-x64 -iname 'workbench.html'` (all 7 cached versions) | **0 matches** |
| `ls /vscode/bin/linux-x64/<active-commit>-insider/out/vs/` | `base  platform  workbench` — **no `code/` subtree at all** |
| `product.json` → `serverApplicationName` | `code-server-insiders` (genuine VS Code **Server**, not desktop Electron) |
| `product.json` → `webEndpointUrlTemplate` | `https://{{uuid}}.vscode-cdn.net/{{quality}}/{{commit}}` — workbench assets are served to the client from Microsoft's CDN, not from a static file in this container |
| Extension install location | `/home/mios-dev/.vscode-remote/extensions/be5invis.vscode-custom-css-7.5.1` — i.e. installed into the **remote/workspace extension host**, not the local UI host |
| `package.json` → `extensionKind` | **absent** (no explicit kind pin; defaults to auto-detected, landed as remote here) |

### 4.2 Qualitative Discoveries & Trade-offs

- **Finding 1 (Q1/Q3 — the actual patch-target algorithm, `extension.js:10-47`):**
  `locateWorkbench()` computes `appDir = path.dirname(require.main.filename)`
  (i.e. wherever the *activating* Node process lives), then searches
  `appDir/vs/code/{electron-browser,electron-sandbox}/workbench/{workbench.html,workbench.esm.html,workbench-dev.html,workbench-apc-extension.html}`.
  In this container, `require.main.filename` resolves into
  `/vscode/bin/linux-x64/<commit>-insider/out/...`, whose `out/vs/` tree
  contains **only** `base/`, `platform/`, `workbench/` — there is no
  `vs/code/electron-browser` or `vs/code/electron-sandbox` directory at all.
  `locateWorkbench()` therefore returns `null` unconditionally in this
  environment, `activate()` bails at line 12 (`if (!loc) return;`), and the
  extension can **never** patch anything from inside this remote host — not
  "sometimes reverts," but structurally inert every single time it activates
  here.

- **Finding 2 (Q2 — where it's installed vs. where it would need to run):**
  The extension was installed under `.vscode-remote/extensions/`, i.e. the
  **remote/workspace extension host** that runs inside this container. It has
  no `extensionKind` declared in its manifest, so its placement is whatever
  VS Code auto-detected. To have any chance of finding a real
  `electron-sandbox/workbench/workbench.html`, it must instead run as a
  **`ui`-kind extension on the connecting local client** (Desktop VS Code),
  which is the only place that directory structure genuinely exists.

- **Finding 3 (Q4 — this is documented upstream behavior, not a MiOS bug):**
  The extension's own README (primary source, installed copy) states,
  verbatim: *"Every time after Visual Studio Code is updated, please
  re-enable Custom CSS"* and *"As this extension modifies Visual Studio Code
  files, it will get disabled with every Visual Studio Code update."* This
  container has **7 separately cached VS Code Server version directories**
  under `/vscode/bin/linux-x64/`, confirming this build channel updates/
  reconnects to fresh commits frequently. Even in the one context where the
  extension *can* function (a local Desktop Electron client, run once with
  write permission to its own install dir, per the README's Linux/macOS
  ownership requirement), each client-side VS Code update ships a pristine
  unpatched `workbench.html`, silently reverting the patch until "Enable
  Custom CSS and JS" is manually re-run and the window reloaded. This is
  exactly the "another agent/restart reverted it" symptom — it is inherent
  to how this extension works, not a defect in the MiOS `.dotfiles` sync.

- **Finding 4 (two-way deprecation check, skill step 3):** the extension is
  still the current, maintained mechanism for this pattern
  (`be5invis/vscode-custom-css`, `preview: true`, engines `^1.93.0`) — there
  is no newer built-in VS Code API for arbitrary workbench CSS injection; it
  remains an unofficial file-patching hack by design, which is why Microsoft
  has never merged an equivalent capability upstream.

---

## 5. Architectural Implications

- **Contract Impact:** none to MiOS schemas/APIs. This is purely an editor
  cosmetic surface.
- **Dependency Footprint:** the fix does not add a dependency; it corrects
  where an existing one (`be5invis.vscode-custom-css`) is expected to run,
  via the standard `remote.extensionKind` override — no new extension needed.
- **Operational Complexity:** the remaining manual step (re-running "Enable
  Custom CSS and JS" + one window reload after every client-side VS Code
  update) is an upstream constraint of this extension family and cannot be
  automated from inside the remote container — there is no `code
  --execute-command` passthrough available on this CLI build (`code --help`
  exposes no such flag), and no local `workbench.html` is reachable from here
  to patch directly.

---

## 6. Final Decision & Actionable Next Steps

**Verdict:** ADOPT (with a documented, permanent upstream limitation)

### Rationale

The revert is not caused by the `.dotfiles`/`sync-dotfiles.py` wiring (which
is correct and now self-healing on every container start). It is caused by
`be5invis.vscode-custom-css` being placed in the **remote** extension host,
where its `locateWorkbench()` algorithm can never find a workbench HTML file
in this VS Code Server build's `out/` tree — and, even when correctly placed
on the local client, by the extension's own documented behavior of being
wiped on every client-side VS Code update. Pin the extension to run as a
`ui`-kind extension via `remote.extensionKind` so it activates against the
connecting local client rather than the inert remote host, and record in the
SSOT that the "Enable Custom CSS and JS" + reload step is a standing,
un-automatable manual action after client updates.

### Action Items

- [x] Task 1: Add `"remote.extensionKind": {"be5invis.vscode-custom-css": ["ui"]}`
      to the `.dotfiles` SSOT VS Code settings so the extension is forced to
      the local/UI extension host on every profile that projects from it.
- [x] Task 2: Document the standing manual step (re-run "Enable Custom CSS
      and JS" + reload window after any client-side VS Code update) in the
      `.devcontainer/README.md` portability contract, so it isn't
      re-diagnosed as a bug in a future session.
- [ ] Task 3: If a genuinely durable fix is later required (no manual step,
      works in a browser-only web client with no local Electron install at
      all), evaluate replacing `vscode_custom_css` with the bundled
      `mios-theme-mobile` color theme's token-level zero-border approach
      (`usr/share/mios/extensions/mios-theme-mobile`) as the sole mechanism,
      since theme `colorCustomizations` apply over the wire without any
      local file patching and are not affected by this limitation — accepting
      that literal internal terminal/xterm *padding* (not just border color)
      is not expressible through theme tokens alone.
