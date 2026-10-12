<!-- AI-hint: Manual pages distilled from the source comments of installation, sanitized, each passage anchored to the comment it came from. -->

# installation

### Consolidated installer surface

Consolidated installer surface: EVERY install/build target comes WITH the live monitor.
Launch mios mon (the unified TUI) in its own window so the operator watches the whole
pipeline live -- matches MiOS-Cat.bat's ensure_live_monitor. The 'monitor' target itself and
the early-exit special targets (configure/repos/update) never reach here. Suppressed by
MIOS_NO_MONITOR=1 (headless/CI/nested).

<!-- mios-src:609860203db5 from installation/mios-install.ps1:255-259 -->
### Consolidated installer surface

Consolidated installer surface: EVERY install/build target comes WITH the live monitor.
Launch mios mon (the unified TUI) in its own window so the operator watches the whole
pipeline live -- matches MiOS-Field.bat's ensure_live_monitor. The 'monitor' target itself and
the early-exit special targets (configure/repos/update) never reach here. Suppressed by
MIOS_NO_MONITOR=1 (headless/CI/nested).

<!-- mios-src:7e1eecc99228 from installation/mios-install.ps1:300-304 -->

### AI model defaults. These are pre-profile-load vendor...

AI model defaults. These are pre-profile-load vendor fallbacks that
MATCH the SSOT mios.toml [ai] section (model / embed_model); they are
superseded in load_profile_defaults() by the RAM-driven auto-pick
against the [ai.host_thresholds] tier table and then by any explicit
[ai].model operator override.

<!-- mios-src:6da2a59f653a from installation/mios-install.sh:311-315 -->

### Profile resolution. Each layer overlays the one above....

Profile resolution. Each layer overlays the one above. Returned as a
space-separated list of paths (lowest precedence first).

  1. /usr/share/mios/mios.toml           vendor defaults (mios.git)
  2. /usr/share/mios/profile.toml        legacy vendor defaults (mios.git)
  3. <bootstrap-checkout>/mios.toml      user-edit copy at repo root  <-- canonical
  4. <bootstrap-checkout>/etc/mios/profile.toml  legacy user-edit copy
  5. /etc/mios/mios.toml                 host-installed user-edit (re-run)
  6. /etc/mios/profile.toml              legacy host-installed copy

Empty strings in higher layers do NOT override non-empty defaults below
them -- that's how this implements "user-set fields supersede defaults"
without requiring sparse TOML files.

<!-- mios-src:5b8f3fb65228 from installation/mios-install.sh:358-370 -->

### Auto-pick the AI model from detected host RAM against the...

Auto-pick the AI model from detected host RAM against the SSOT
[ai.host_thresholds] tier table: >= big_ram_gb -> big_ram_model,
>= mid_ram_gb -> mid_ram_model, else small_ram_model. mios.toml
documents [ai].model as the operator override that wins over this
pick, so callers apply that override AFTER consulting this function.
Vendor fallbacks mirror the canonical [ai.host_thresholds] values.

<!-- mios-src:068e7e3bb3a0 from installation/mios-install.sh:396-401 -->

### AI model selection (Architectural Law 5). The model lineup...

AI model selection (Architectural Law 5). The model lineup + RAM
thresholds are the SSOT [ai.host_thresholds] tier table. Auto-pick
by detected host RAM first; then let an explicit [ai].model (the
documented operator override) win. embed follows [ai].embed_model.

<!-- mios-src:15aeb790a053 from installation/mios-install.sh:440-443 -->

### Prompts -- the "mios" defaults are baked in; user just hits...

============================================================================
Prompts -- the "mios" defaults are baked in; user just hits Enter to accept,
or stays idle for $MIOS_PROMPT_TIMEOUT seconds (default 90 = 1.5 minutes)
for the prompt to auto-accept the default. Set MIOS_PROMPT_TIMEOUT=0 to
disable the timeout (wait forever); set MIOS_PROMPT_TIMEOUT=1 in CI for
fastest unattended runs.
============================================================================

<!-- mios-src:2c70ad4357de from installation/mios-install.sh:617-623 -->

### 'read' exits with non-zero on EOF or timeout. Either way we...

'read' exits with non-zero on EOF or timeout. Either way we take
the default and emit a one-line note to stderr so the operator
can audit the unattended decision in the install log.

<!-- mios-src:091da86b5721 from installation/mios-install.sh:632-634 -->

### Optional GUI step. Open...

Optional GUI step. Open /usr/share/mios/configurator/mios.html
in the operator's default browser, stage a writable mios.toml
template at a known path, and wait for the operator to save
before continuing. The HTML uses the File System Access API to
overwrite the staged file in place (no Downloads detour, no "(1)"
suffix). Skipped on headless / unattended runs.

<!-- mios-src:b9a818a76597 from installation/mios-install.sh:673-678 -->

### Stage a writable mios.toml template the configurator can...

Stage a writable mios.toml template the configurator can bind to.
Pick the highest-precedence existing layer; otherwise copy the
repo-shipped template. The operator's browser will overwrite this
path in place via the File System Access API.

<!-- mios-src:784c81ef7cee from installation/mios-install.sh:709-712 -->

### Pass the staging path to the HTML via a query param so the...

Pass the staging path to the HTML via a query param so the banner
shows the operator exactly where to save (use Pick file -> select
this file -> edit -> Save).

<!-- mios-src:48195b5c1444 from installation/mios-install.sh:731-733 -->

### Wait for the operator to finish editing. We don't...

Wait for the operator to finish editing. We don't auto-detect
save (mtime polling is fragile on some filesystems) -- explicit
confirmation is more reliable.

<!-- mios-src:e56612ccbdaa from installation/mios-install.sh:762-764 -->

### Promote the staged file to the per-host layer if the...

Promote the staged file to the per-host layer if the operator
actually saved something. Only the [identity], [ai], [network],
[image] sections are typically edited; secrets stay in install.env.

<!-- mios-src:bfefb492df49 from installation/mios-install.sh:767-769 -->

### Multi-user seeder

============================================================================
Multi-user seeder: copy /etc/skel/.config/<subdir>/* into every existing
user's home for each MiOS-managed config subdirectory. Called from
deploy_system_prompt (after the host /etc/mios/ai/system-prompt.md is in
place) and again from stage_user_profile_artifacts. Idempotent: install(1)
overwrites with current content, mode is enforced.

Subdirs covered:
  - mios/      profile.toml + system-prompt.md (per-user MiOS overlay)
  - aichat/    config.yaml -- Architectural Law 5 default for sigoden/aichat
               and blob42/aichat-ng (both consume the same config path).
============================================================================

<!-- mios-src:4f2d2c4fb217 from installation/mios-install.sh:1054-1065 -->

### Phase-1 + Phase-2: clone mios.git into /, apply bootstrap...

============================================================================
Phase-1 + Phase-2: clone mios.git into /, apply bootstrap overlays, install
packages from PACKAGES.md SSOT, run mios.git/install.sh for system init.
Phase-2 (build) is implicit: on FHS hosts the package install + system-side
init is the equivalent of "build the running system from the merged tree";
on bootc hosts Phase-2 is `bootc switch` to a pre-built image.
============================================================================
============================================================================
mios.toml package helpers (mirroring automation/lib/packages.sh SSOT)
============================================================================

<!-- mios-src:08b6c56b33f0 from installation/mios-install.sh:1129-1138 -->

### Confirm before mutating the host root. 'git init /'...

Confirm before mutating the host root. 'git init /' followed by
'reset --hard FETCH_HEAD' is bold by design (it is the canonical
"MiOS-ify a stock Fedora Server" path) but it overwrites every
file the upstream tree owns. Operators must opt in.

Auto-accept respects MIOS_PROMPT_TIMEOUT (90s default; '0' waits
forever, '1' is the unattended-CI value). Setting
MIOS_FHS_TOTAL_ROOT_MERGE=1 in the environment also bypasses
the prompt for scripted re-runs.

<!-- mios-src:c4263417d6e8 from installation/mios-install.sh:1272-1280 -->

### Make / a first-class, SELF-UPDATING git work tree so `git...

Make / a first-class, SELF-UPDATING git work tree so `git -C / pull` works
on Day-N+ -- "/ IS $ROOT": the deployed root is the SAME git tree the
drift-gate resolves ($ROOT = $(cd automation/.. ) = /), and it pulls its own
updates. `fetch <branch>` + `reset --hard FETCH_HEAD` leaves HEAD on
git-init's default branch with NO upstream, so a bare `git pull` errors
"no tracking information". checkout -B at the CURRENT commit renames/creates
${DEFAULT_BRANCH} with no working-tree change; the config lines wire its
upstream to origin so `git -C / pull --ff-only` fast-forwards mios.git.

<!-- mios-src:e723bdda0f56 from installation/mios-install.sh:1316-1323 -->
