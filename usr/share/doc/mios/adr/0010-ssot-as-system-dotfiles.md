<!-- AI-hint: mios.toml IS the cross-platform system dotfiles-as-code. One SSOT + a [dotfiles.registry.<surface>] map + the generalized mios-theme-render/mios-dotfiles-render engine project EVERY declared dotfile to its real per-platform path (Linux/FHS, Windows+Registry, WSL), drift-gated both sides (Law 8), layered vendor<host<user (Law 1/13), one canonical name per surface (Law 9), no literals (Law 7). Generalizes the LANDED palette+btop projection (mios-theme-render + check 25) to the whole dotfile surface. Read before adding any dotfile, config file, or Install-MiOS* path. -->
<!-- AI-related: usr/libexec/mios/mios-theme-render, usr/libexec/mios/mios-sync-theme, usr/lib/mios/mios_toml.py, tools/lib/userenv.sh, automation/98-drift-checks.sh (check 25), usr/share/mios/mios.toml ([colors] [theme] [appearance] [terminal] [identity] [btop]), usr/share/mios/theme/templates/, Get-MiOS.ps1, usr/share/doc/mios/adr/0005-sovereign-run-off-m-drive.md, usr/share/doc/mios/adr/0008-mios-cat-unified-entry-and-minification.md, usr/share/doc/mios/adr/0009-unified-config-surface.md -->
---
adr: 0010
title: SSOT-as-system-dotfiles — one mios.toml projects every dotfile on every platform
status: accepted
date: 2026-07-16
deciders: [operator, ai-pair]
tags: [dotfiles, ssot, projection, cross-platform, windows, wsl, registry, sovereignty]
laws: [1, 7, 8, 9, 13]
ssot_keys: [dotfiles.registry, colors, theme, appearance, terminal, identity, btop]
related_ws: [WS-DOTFILES, WS-CONFIG]
supersedes: []
superseded_by: []
---

# ADR-0010: SSOT-as-system-dotfiles — one `mios.toml` projects every dotfile on every platform

## Status

Accepted — 2026-07-16 (Laws 1, 7, 8, 9, 13). The decision is in force; it is a
**generalization of a pattern MiOS has already proven end-to-end**, not a
greenfield. Honest DONE-vs-PLANNED split is in Consequences: the palette and the
`[btop]` settings surface are **DONE** (projected + drift-gated this session);
the SSOT `[dotfiles.registry.*]` map and the live-HOME `apply`/`diff` verbs
**landed 2026-07-17** as a byte-preserving transcription of the 8 existing
surfaces — `mios-theme-render` now reads its surface map from `mios.toml`
(fail-loud, exit 3, on an empty registry) and check 25 stays green; the
per-surface `kind` axis + the `json-merge` mode **landed 2026-07-17** (the
`vscode-colors` proof surface splices a MiOS-owned subtree into a foreign
settings.json, foreign keys preserved). The `mios-dotfiles-render` fork
(arbitrary-key `@MIOS:<section>.<key>@` tokens; the `ini-merge`/`registry`
kinds), the Windows runtime gate, and the new `[shell]`/`[editor]`/`[git]`/`[ssh]`
content domains remain **PLANNED** (WS-DOTFILES / task_248). This ADR builds on ADR-0009
(the Portal is the surface that edits `mios.toml`) and carries across every
deployment via ADR-0005 (M: VHDX) and ADR-0008 (MiOS-Repo shadow-config).

## Context

MiOS has already *proved* the operator-defined-SSOT-projection pattern for the
dotfile domain, end to end, for the theme and now the `btop` config:

- `[colors]` (`usr/share/mios/mios.toml` L8537) and `[theme]` (L8923) are rendered
  by `usr/libexec/mios/mios-theme-render` through a `SURFACES` registry into
  concrete artifacts (the btop theme, oh-my-posh, quickshell, fastfetch, the
  app-shell CSS, the terminal OSC fallbacks).
- The engine is refreshed globally by `usr/libexec/mios/mios-sync-theme` (the one
  Linux global refresh), resolved through the layered vendor `/usr` < host `/etc`
  < user `~/.config` twin (`usr/lib/mios/mios_toml.py` + `tools/lib/userenv.sh`),
  and CI-gated by `check_theme_projection` — **drift-check 25**
  (`automation/98-drift-checks.sh` L1449) — which regenerates in-memory and
  byte-diffs the committed artifact (exit 1 on drift).
- **Landed this session:** `mios-theme-render` gained a **settings-surface**
  concept — a surface whose registry entry carries a *third* element naming a
  `mios.toml` section. The `[btop]` section (L9137, ~60 keys) now derives the
  **whole** `etc/btop/btop.conf` via `@MIOS:btop_<key>@` tokens the same way the
  color surfaces derive from `[colors]` — one SSOT, unified Linux+Windows, no
  hand-maintained conf. **Drift-check 25 auto-extended** to gate the settings
  surface ("(25) every committed theme + settings surface projects from mios.toml
  [colors]/[btop] SSOT") and is proven green.

That is the proof of concept for exactly *one and a half* dotfile domains (the
palette, plus `btop` as the first non-color settings surface). Every *other*
dotfile is still hand-maintained or projected imperatively: Windows Terminal
`settings.json`, `.gitconfig`, VS Code, shell rc, GTK CSS, ssh — and on Windows
the scattered `Install-MiOS*` bodies in `Get-MiOS.ps1` (TerminalProfile, Fastfetch,
OhMyPoshTheme, GeistFont, BibataCursor, PowerShellProfile). This violates
"no hand-maintained/divergent config anywhere" for the non-`[colors]`/`[btop]`
surfaces, and the hardcoded `SURFACES` dict cannot express a **different real path
per platform** (its tuples are repo-relative Linux build-tree paths only).

## Decision

**`mios.toml` IS the cross-platform system dotfiles-as-code.** Generalize the
proven, color-and-btop-only, Linux-build-tree projection into an SSOT-authored,
per-platform, arbitrary-key projection registry with a live-HOME `apply` and a
two-sided drift-gate — so that *every* declared dotfile on *every* platform is
`render`-generated from one `mios.toml` and `check`-gated, exactly as the palette
and `btop` already are. Concretely:

1. **The projection registry is SSOT-authored — the one new namespace
   `[dotfiles.registry.<surface>]`.** It replaces the hardcoded Python `SURFACES`
   dict, making the map itself operator-editable via the Portal (ADR-0009) and —
   critically — able to target a **different real path per platform**. Each entry
   carries: `kind` (`template` | `json-merge` | `registry` | `command` | `skip`),
   `format` (json/toml/ini/text/css/git-ini), `sources` (the SSOT tables that feed
   substitution), `platforms` (linux/windows/wsl selector), a per-platform
   `target.<os>`, and an optional `condition` (per-deployment guard). This is the
   *map*, not the content.

2. **Existing sections stay the content-of-record — no duplication (Law 9).** Do
   NOT invent a `[dotfiles.colors]` content namespace. `[colors]`, `[theme]`,
   `[appearance]` (L5806), `[terminal]` (L8848), `[identity]` (L37), and `[btop]`
   (L9137) already *are* the dotfile content. The registry *references* them; it
   does not re-home them.

3. **Add the genuinely-missing domains** `[shell]` (aliases/env/rc fragments),
   `[editor]` (VS Code / code-server / nvim keys), `[git]` (git-specific only:
   `default_branch`, `core.editor`, aliases, credential helper — **`user.name`/
   `user.email` reference `[identity]`, never re-stored**, Law 9), and `[ssh]`
   (host blocks + a `secret_ref` indirection; **raw keys never live in the SSOT**).

4. **One engine, one contract, widened.** Generalize `mios-theme-render` (kept as a
   back-compat alias projecting the color+btop subset so check 25 keeps passing)
   into `mios-dotfiles-render`: registry read from `mios_toml.load_merged()`;
   arbitrary-key `@MIOS:<section>.<key>@` tokens (not just palette hex);
   format-aware `merge` that splices the MiOS-owned block into existing JSON/TOML/
   INI while preserving foreign keys (WT/VS Code must never be clobbered);
   per-platform target resolution (`%LOCALAPPDATA%`/`$XDG_CONFIG_HOME`, WSL host
   bridge). Verbs stay identical in spirit — `render` (write), `check`
   (regenerate-in-memory + byte-diff → exit 1), `capture` (self-verifying round-trip
   templatize) — **plus a new `apply`/`diff` that writes to live HOME**
   (`~/.config`, `%USERPROFILE%`, `%LOCALAPPDATA%`), closing the chezmoi-shaped gap
   (today the engine writes only build-tree artifacts + the `/etc/mios/theme/`
   bridge).

5. **Drift-gate both sides (Law 8).** Generalize `check_theme_projection` (check 25)
   → `check_dotfiles_projection`, iterating every `[dotfiles.registry.*]` whose
   template exists and byte-diffing the committed artifact (offline, image-free PR
   gate — same contract, wider scope). Add the missing **Windows runtime half**: a
   PowerShell `Test-MiOSProjection` that reads back each `registry`/`command`/
   `json-merge` sink and diffs against the SSOT projection (precedent: the ports
   check already gates `MIOS_PORT_*` against `[ports]` on the Windows side).

**Render+copy, never symlink** (chezmoi's discipline, not GNU Stow's symlink farm):
MiOS owns the byte content, which is precisely what makes Law-8 drift-gating
possible. Byte-identical re-render (Nix-grade reproducibility) is the contract the
gate compares against.

## Rationale

- **Law 8 (SSOT-PROJECTION) — already demonstrated, now completed.** The palette and
  `btop` prove the exact mechanism (`render`/`check`/`capture` + drift-check 25).
  Extending the same engine to the whole dotfile surface is the literal completion
  of the pattern, not a new one — the same resolver (data plane), the same verbs,
  the same drift contract, widened from color+btop on one platform to the whole
  dotfile surface on three (Linux/FHS, Windows+Registry, WSL).
- **Law 9 (ONE-CANONICAL-NAME).** The registry references the existing content
  sections; every platform *derives* one canonical key. Git identity references
  `[identity]`; the `[theme.cursor_windows]`/`[theme.cursor_linux]` split already
  models a marked OS overlay (sibling keys, not forks).
- **Law 1 / Law 13 (USR-OVER-ETC / NATIVE-DROPINS).** The merge happens once, in
  `mios_toml.py`'s vendor `/usr` < host `/etc` < user `~/.config` cascade (with
  `mios.d/*.toml` drop-ins), so a user override wins on Windows too. On bootc, `/usr`
  is immutable and vendor bakes at build; runtime projects to `/etc` + `~/.config`.
  `/etc` is a bootc 3-way merge (local wins) — the gate must distinguish an
  operator-intended local override from a stale copy that should re-inherit, which
  the resolver's empty-string-never-overrides rule already encodes.
- **Law 7 (NO-HARDCODE).** No dotfile value is hand-typed on any platform; secrets
  use `secret_ref` indirection so plaintext keys never enter `mios.toml`.
- **Sovereignty + portability.** The vendor layer travels inside the OCI image
  (reproduced by pinning an image version); only the operator's thin host + user
  overlay travels (small diffable TOML). Carried on the MiOS-Repo shadow-config
  partition (ADR-0008), a synced git repo, or a Portal export — the same overlay
  reconstitutes the same projected dotfiles on every deployment (ADR-0005 M: VHDX,
  bare-metal, VM, WSL, USB, cloud), degrade-open.

**Lineage (explicit).** This is **chezmoi's render+copy discipline** (own the byte
content; not Stow's symlinks) fused with **NixOS/home-manager's declarative
pure-function + atomic-generation** semantics (on the bootc substrate MiOS already
sits on), with layering governed by the **XDG Base Directory Spec** (user tier) and
**bootc/ostree's 3-way `/etc` merge** (host tier). MiOS already *exceeds* chezmoi on
the two axes that matter — a single TOML SSOT and CI-enforced drift-gating — so we
borrow the discipline, not the tool.

## Alternatives considered

- **Adopt chezmoi directly.** Rejected — a second SSOT, not TOML-native, no CI Law-8
  gate. Borrow its render/diff/apply *discipline*, not the tool.
- **Keep the imperative PowerShell `Install-MiOS*` bodies.** Rejected — not
  drift-gated, and it hard-codes Windows/Linux divergence instead of deriving both
  from one key.
- **A `[dotfiles.*]` *content* namespace.** Rejected — it would duplicate `[colors]`
  /`[theme]`/`[btop]` and break Law 9. Register the surfaces; do not re-home the
  content.
- **GNU Stow symlink farm.** Rejected — symlinks can't own byte content, so they
  can't be drift-gated (the whole point of Law 8).

## Consequences

Positive:
- Every declared dotfile on Linux/Windows/WSL becomes `render`-generated from one
  `mios.toml` and `check`-gated both sides; nothing is hand-typed.
- The operator overlay projects the *same* dotfiles on every deployment; the
  shareable link and the USB (ADR-0009/0008) carry the identical payload.
- The Windows resolver becomes a gated third twin (Law 13), closing a real hole.

DONE vs PLANNED (honest):
- **DONE this session:** the palette projects to 6 surfaces via `mios-theme-render`
  + drift-check 25; the `[btop]` settings surface (~60 keys) derives the whole
  `etc/btop/btop.conf` (unified Linux+Windows) and check 25 auto-extended and is
  proven green — the landed proof of concept for this ADR.
- **DONE 2026-07-17 (byte-preserving transcription):** the SSOT
  `[dotfiles.registry.*]` map now expresses all 8 current surfaces (btop,
  oh-my-posh, quickshell, fastfetch, app-shell, term-osc, btop-conf, gitconfig);
  `mios-theme-render` reads its surface map from that namespace instead of a
  hardcoded Python dict (`_load_surfaces()`, element-for-element equal, so
  render/check/capture output is byte-identical and check 25 stays green),
  fail-loud (exit 3) on an empty/absent registry; and the additive live-HOME
  `apply`/`diff` verbs write a rendered surface to its per-platform
  `[.apply.target]` HOME path, backing up any existing file (`.mios-bak.<UTCZ>`,
  raw bytes via `shutil.copy2`) before overwrite and refusing a target that
  resolves outside HOME / through a symlink / with an unexpanded variable. The map
  is now operator-editable via the Portal (ADR-0009); adding a surface is a
  `mios.toml` edit, not a code change.
- **DONE 2026-07-17 (`kind` axis + `json-merge` mode):** each
  `[dotfiles.registry.<surface>]` may declare an optional `kind`
  (`template`|`json-merge`|`ini-merge`|`registry`|`skip`; ABSENT ⇒ `template`, so
  the 8 existing surfaces are byte-identical). `kind` is WRITE-SEMANTICS dispatch
  (`_KINDS = {kind: (derive_fn, live_apply_fn)}`), orthogonal to `section` (the
  token source): `template` renders the whole file as before; `json-merge` splices
  a MiOS-owned JSON subtree onto a foreign JSON doc via a STRING-AWARE JSONC parse
  (`//` inside a value/URL never mangled), a deep merge (owned leaf/array wins,
  arrays replaced wholesale — except at a keyed pointer, see array-merge-by-key
  below — every foreign key byte-preserved), and a base-indent
  `json.dump`. The gate is OFFLINE: a merge surface declares a `fixture.base` +
  `fixture.expected` pair and `check` diffs `derive(owned, fixture.base)` against
  the committed `fixture.expected` (it NEVER reads the operator's live file);
  `apply` merges the owned subtree onto the LIVE settings.json, backing it up
  first. The landed proof is the `vscode-colors` surface (owned
  `workbench.colorCustomizations` + a fixture pair carrying a FOREIGN key that
  survives the merge). Fail-loud preserved: unknown/unimplemented kind or a merge
  surface missing its fixture ⇒ exit 3; an unparseable base ⇒ exit 2, writes
  nothing. `capture` stays template-only. `registry`/`skip` are declared-valid
  but land later.
- **DONE 2026-07-17 (`ini-merge` mode + `gitconfig-live` surface, task_1087):** a
  third `kind` that UPSERTS the MiOS-owned git-config keys (exactly what
  `gitconfig.tmpl` emits: `user.name`/`email`, `core.editor`,
  `init.defaultBranch`, `pull.rebase`, `alias.{co,br,ci,st}`) into a foreign
  `~/.gitconfig`. `_parse_ini` keeps an ORDERED line-model + a
  `{(section,subsection,key)->idx}` index (preserving `#`/`;` comments and
  `[section "subsection"]` quoting; section/key case-insensitive, subsection
  case-sensitive). Per owned key: ABSENT ⇒ seed (append to the section block, or
  a fresh `[section]` at EOF); PRESENT ⇒ replace in place per the write policy.
  Every line NOT in the owned set (`credential`, `user.signingkey`,
  `commit.gpgsign`, `[remote …]`) is byte-preserved; foreign multivar keys are
  left untouched (the owned set is single-valued). The `seed-or-enforce` policy
  (default, safety-first) writes an ABSENT key (seed) and enforces a PRESENT key
  ONLY when the operator explicitly set it in the SSOT
  (`load_merged(sec,key) != load_vendor(sec,key)` AND non-empty), else SKIPs it —
  so a vendor default never stomps an existing foreign value. This is the SAFE
  unblock of the live-`apply` the `gitconfig` (template) surface withheld: the
  new `gitconfig-live` surface (kind=`ini-merge`, reusing `gitconfig.tmpl`
  verbatim, `apply.target` `~/.gitconfig` / `%USERPROFILE%/.gitconfig`) sets only
  the ~9 owned keys instead of clobbering the user's identity/credentials/signing/
  remotes. The gate is OFFLINE via a `fixture.base`/`fixture.expected` pair whose
  base carries FOREIGN `[credential]`/`user.signingkey`/`[remote "origin"]` lines
  that survive the merge. The existing `gitconfig` (template) surface is
  unchanged and byte-identical.
- **DONE 2026-07-17 (array-merge-by-key + `windows-terminal` surface):** a minimal
  extension to the `json-merge` deep merge for foreign docs that keep a COLLECTION
  in an array keyed by a stable field. `_json_deep_merge` now threads a JSON
  pointer; at a pointer listed in `_ARRAY_MERGE_KEYS` (`{"/schemes": "name"}`)
  whose two sides are both lists of objects all carrying the merge key, the lists
  merge BY KEY (foreign element preserved in base order; owned element with the
  same key deep-merges over it; owned-only element appended) instead of the
  default wholesale-replace. Every OTHER pointer — and every element that is a
  non-dict or lacks the key — falls back to the byte-identical wholesale-replace,
  so `vscode-colors` and all prior surfaces are unchanged. The landed proof is the
  `windows-terminal` surface (kind=`json-merge`, Windows-only `apply.target` the WT
  `LocalState/settings.json`): MiOS owns ONLY a `"MiOS"` scheme in the top-level
  `schemes` array (colors from `[colors]` SSOT via `@MIOS:token@`, `black..white`
  from the `ansi_0..15` slots) + `profiles.defaults.colorScheme`; its
  `fixture.base` carries a FOREIGN `"Campbell"` scheme and foreign top-level keys
  (`copyOnSelect`, `launchMode`, `profiles.list`) that all survive the merge.
- **PLANNED (WS-DOTFILES / task_248):** the `mios-dotfiles-render` fork + the
  `check 25 → check_dotfiles_projection` rename (deliberately NOT done here —
  larger, higher-risk, out of scope for the byte-preserving transcription);
  arbitrary-key `@MIOS:<section>.<key>@` tokens; the `registry`/`skip` merge
  kinds; the Windows `Test-MiOSProjection` runtime gate and `Get-MiOS.ps1`
  `Sync-MiOSDotfiles`; a `mios dotfiles` CLI verb; the new
  `[shell]`/`[editor]`/`[git]`/`[ssh]` content domains + the GTK-CSS hole; and
  `secret_ref` indirection for ssh keys/tokens.

Costs:
- Engine-generalization risk, mitigated by the existing `capture` round-trip
  self-verify (a template can never silently diverge from its artifact).
- Windows `apply` needs live-path resolution and the new runtime gate.
- `json-merge` requires a defined ownership boundary (which JSON-pointer subtrees
  MiOS owns vs leaves foreign) so `diff` does not flag user edits as drift.

## Implementation

- `usr/share/mios/mios.toml` — **[DONE]** the `[dotfiles.registry.<surface>]`
  tables (8 surfaces; each `template`/`target`[/`section`] + an optional
  `[.apply.target]`); `[colors]`/`[theme]`/`[appearance]`/`[terminal]`/`[identity]`
  /`[btop]`/`[gitconfig]` stay the content the registry *references* (Law 9).
  **[PLANNED]** filling the `[shell]`/`[editor]`/`[git]`(→`[identity]`)/`[ssh]`
  (`secret_ref`) stubs with content.
- `usr/libexec/mios/mios-theme-render` — the reference projector (palette + the
  `[btop]` settings surface, LANDED). **[DONE 2026-07-17]** its surface map is now
  loaded from `mios.toml [dotfiles.registry.*]` (`_load_surfaces()`, fail-loud on
  empty), and it gained the additive live-HOME `apply`/`diff` verbs (backup +
  HOME-scope/symlink/unresolved-var refusals). **[DONE 2026-07-17]** the per-surface
  `kind` axis (`_KINDS` dispatch) + the `json-merge` derive/apply (string-aware
  JSONC parse, deep-merge preserving foreign keys, `fixture.base`/`fixture.expected`
  offline gate) with the `vscode-colors` proof surface. **[PLANNED]** the
  `mios-dotfiles-render` fork (arbitrary-key tokens; the `ini-merge`/`registry`
  kinds); this file stays the back-compat alias for the color+btop+settings subset.
- `usr/libexec/mios/mios-sync-theme` — the one Linux global refresh; extended to
  call `mios-dotfiles-render render` after the theme bridge.
- `usr/lib/mios/mios_toml.py` + `tools/lib/userenv.sh` — the layered resolver, fed
  to the engine unchanged; the Windows `Get-MiosTomlValue` becomes a gated third
  twin (Law 13).
- `automation/98-drift-checks.sh` — `check_theme_projection` (check 25, already
  auto-extended for `[btop]`) generalizes to `check_dotfiles_projection`; a new
  Windows-side `Test-MiOSProjection` gates registry/JSON/command sinks at runtime.
- `Get-MiOS.ps1` — the scattered `Install-MiOS*` bodies collapse into thin
  registry-driven calls (`Sync-MiOSDotfiles`).
- `usr/bin/mios` — new `mios dotfiles apply/diff/drift` verb + a `[verbs.dotfiles_*]`
  block so the AI plane can call it (the chezmoi `diff`/`status`/`apply` triad).

## References

- ADR-0005 (Sovereign run-off-M:) — the operator overlay carries onto the M: VHDX
  and survives root rebuild: `0005-sovereign-run-off-m-drive.md`.
- ADR-0008 (MiOS-Cat unified entry point) — the USB `MiOS-Repo` shadow-config
  partition is the offline carrier of the dotfiles overlay:
  `0008-mios-cat-unified-entry-and-minification.md`.
- ADR-0009 (Unified config surface) — the Portal is the surface that edits
  `mios.toml`, including the `[dotfiles.registry.*]` map:
  `0009-unified-config-surface.md`.
- LANDED proof: `usr/libexec/mios/mios-theme-render` (settings-surface concept +
  `[btop]` projection), gated by `automation/98-drift-checks.sh` `check_theme_projection`
  (check 25, auto-extended); SSOT `usr/share/mios/mios.toml [btop]` (L9137).
- Lineage: chezmoi (<https://www.chezmoi.io/>) render+copy discipline; NixOS
  home-manager declarative generations; the XDG Base Directory Spec; bootc/ostree
  3-way `/etc` merge.
- MiOS Laws 1/7/8/9/13: `usr/share/mios/mios.toml [laws]`, enforced by
  `automation/98-drift-checks.sh` + `automation/99-postcheck.sh` + `bootc container lint`.
