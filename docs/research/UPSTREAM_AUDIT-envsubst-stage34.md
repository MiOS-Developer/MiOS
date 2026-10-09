<!-- AI-hint: Upstream audit behind task_977 -- why stage 34's placeholder substitution must become a parser, not a regex. Measured, not inferred. -->
<!-- AI-related: automation/34-render-quadlets.sh, src/mios-rs/miosd/src/main.rs, TASKS.md task_977, task_997 -->

# Upstream Audit — GNU `envsubst` as stage 34's substitution engine

**Audit ID:** `UPSTREAM-gettext-envsubst-stage34`
**Component:** `automation/34-render-quadlets.sh` placeholder renderer (task_977)
**Installed:** `envsubst (GNU gettext-runtime) 0.21` at `/usr/bin/envsubst`
**Upstream current:** GNU gettext **1.0** (released 2026-01-29; man page dated January 2026)
**Consumers of the rendered output:** systemd 255 unit parser, `/bin/sh`, podman/Quadlet
**Auditor:** research pass, no code changed

---

## 1. Executive summary & recommendation

- **Verdict:** `BLOCK_UPGRADE` — upgrading gettext does **not** fix this. Replace the engine.
- **Risk level:** HIGH (one live silent data defect in the shipped image; one Law 5 breach)
- **Summary:** Every defect task_977 attributes to `envsubst` is **documented upstream behaviour that
  is still present in the newest release**, not a bug in the pinned 0.21. `envsubst`'s contract is
  "substitute `$VAR` and `${VAR}`, nothing else" — it has no escape for a literal `$`, and it
  explicitly does not implement `${VAR:-default}`. MiOS needs both. The conversion is therefore
  correct, but **the existing Rust `run_render_quadlets` is not the fix** — it reproduces the worst
  defect and must not be promoted as-is.

---

## 2. Upstream contract (primary sources)

### 2.1 What `envsubst` does and does not do

> "Shell format strings supported by GNU gettext and the `envsubst` program are strings with
> references to shell variables in the form `$variable` or `${variable}`, but references of the form
> `${variable-default}`, `${variable:-default}`, `${variable=default}`, `${variable:=default}`,
> `${variable+replacement}`, `${variable:+replacement}`, `${variable?ignored}`,
> `${variable:?ignored}` are **not supported**."
> — GNU gettext manual, *sh-format*

> "If a SHELL-FORMAT is given, only those environment variables that are referenced in SHELL-FORMAT
> are substituted; otherwise all environment variables references occurring in standard input are
> substituted."
> — `envsubst(1)`, GNU gettext-runtime 1.0

**No escape for a literal dollar sign is documented in any version.** This is the whole defect
class, and it is by design.

**Two-way deprecation check.** `envsubst` is *not* deprecated and is actively shipped (gettext 1.0,
January 2026). But nothing in the 0.21 → 1.0 range adds default-value support or a `$$` escape. The
behaviour MiOS is hitting is the stable, intended contract. **There is no upstream version to
upgrade to that resolves this.**

### 2.2 What systemd requires — the direct conflict

> "Unless for commands with the special executable prefix `":"`, to pass a literal dollar sign, use
> `"$$"`."
> — `systemd.service(5)`, *Command lines*

> "Variables whose value is not known at expansion time are treated as empty strings."
> — same

So `$$` is **systemd's mandatory escape** for a literal `$`, and `$$` is **meaningless to
`envsubst`**, which sees `$` followed by `$VAR` and expands the inner reference. The two contracts
are in direct, unavoidable conflict. The same file already relies on systemd's sibling escape `%%`
(`TS=$$(date -u +%%Y%%m%%dT%%H%%M%%SZ)`), which corroborates the reading.

---

## 3. Measured behaviour — reproduced, not inferred

All of the following was produced against the **real shipped files** with the **exact 121-name
allowlist** extracted from `automation/34-render-quadlets.sh:50`.

### 3.1 The `$$` mangling is confirmed, and one consequence is worse than recorded

Rendering the shipped `usr/lib/systemd/system/mios-pgvector-backup.service`:

| Source | Rendered by stage 34 | Runtime value in `/bin/sh` |
|---|---|---|
| `case "$$MIOS_PG_BACKUP_ENABLE"` | `case "$true"` | `` (empty) — falls through to enabled |
| `DIR="$$MIOS_PG_BACKUP_DIR"` | `DIR="$/var/lib/mios/backups"` | `$/var/lib/mios/backups` |
| `KEEP="$$MIOS_PG_BACKUP_KEEP"` | `KEEP="$7"` | `7` (guard self-heals) |
| `PORT="$$MIOS_PORT_PGVECTOR"` | `PORT="$8432"` | **`432`** |
| `USR="$$MIOS_PG_USER"` | `USR="$mios"` | `mios` (guard self-heals) |

The allowlist is what decides: a name **on** the list gets its `$$` eaten; a name off it survives
intact. That is why the mangling is selective rather than uniform.

**The live defect is `PORT=432`.** `$8` is positional parameter 8 (unset, empty) and `432` is
literal, so the backup connects to port 432 instead of 8432. The `[ -z "$PORT" ]` guard cannot fire
because `432` is non-empty. Verified by executing the rendered command text under `/bin/sh`:

```
DIR=[$/var/lib/mios/backups]   KEEP=[7]   PORT=[432]   USR=[mios]
```

`DIR` is likewise non-empty garbage, so its guard is also dead — backups are written to a directory
literally named `$`.

### 3.2 The nested-default corruption is **not** `envsubst` — and it is environment-dependent

`envsubst` leaves `${MIOS_AI_ENDPOINT:-...}` **completely untouched** (§2.1). The corruption comes
from the **bash pre-loop** at `34-render-quadlets.sh:43`:

```bash
while [[ "$content" =~ \$\{([A-Z_][A-Z0-9_]*):-([^}]*)\} ]]; do
```

`[^}]*` cannot match a nested `}`. This is a property of regular languages, not a tuning mistake —
**no regex can do this**, which is the core architectural finding of this audit.

Against the real `etc/mios/kb.conf.toml:6`, the outcome depends on what else is exported at bake.
All four of these are the same bug:

| Bake environment | Rendered `base_url` | |
|---|---|---|
| nothing set | `"http://localhost:8700/v1"` | correct — **this is why a naive test passes** |
| only `MIOS_PORT_AGENT_PIPE` set | `"http://localhost:8700"` | `/v1` silently **deleted** |
| `MIOS_AI_ENDPOINT` set, no `/v1` | `"http://localhost:8700/v1}"` | stray brace |
| `MIOS_AI_ENDPOINT` set with `/v1` | `"http://localhost:8700/v1/v1}"` | doubled + stray brace |

This reconciles the two different symptoms recorded in task_977 and in commit `e7860820`: **both are
real**, in different environments. The task should record the matrix, not a single symptom.

A well-formed wrong URL is the dangerous case — it fails at the first API call, not at render.
This is Law 5's single endpoint contract.

### 3.3 Compounding risk from task_997

`usr/share/mios/reference/env-baseline.txt` emits `MIOS_AI_ENDPOINT` at line 148 as
`http://localhost:${MIOS_PORT_AGENT_PIPE}/v1`, while `MIOS_PORT_AGENT_PIPE=8700` is emitted at line
**1799** — 1651 lines later. Under `bash source`, the dependant expands **before** its dependency
exists, yielding `http://localhost:/v1`. That feeds row 3 or 4 of the matrix above. The ordering
dependency is worth treating as part of this fix, not separately.

---

## 4. The existing Rust path is not the fix

`src/mios-rs/miosd/src/main.rs:602` carries **the identical defective regex**:

```rust
let re_default = regex::Regex::new(r"\$\{([A-Z_][A-Z0-9_]*):-([^}]*)\}")?;
```

Modelled against the real `kb.conf.toml` line, the Rust path is **worse than bash** on this input,
because it is single-pass (`replace_all` once) where bash loops to convergence:

```
BASH RESULT : base_url    = "http://localhost:8700/v1"        (converges, in the unset case)
RUST RESULT : base_url    = "http://localhost:${MIOS_PORT_AGENT_PIPE:-8700/v1}"
```

An unresolved `${MIOS_...}` shipped verbatim into a config file. Three further gaps:

- **No `$$` handling at all.** It only matches braced `${NAME}` forms, so `$$MIOS_X` is left alone —
  it accidentally gets §3.1 *right*, but by omission rather than by contract. Nothing encodes the
  rule, so the next edit can silently reintroduce it.
- **Silent failure.** `let _ = std::fs::write(...)` discards the error; `unwrap_or_else(|_| caps[0])`
  leaves unresolved placeholders in place with no post-assert.
- **Reads `std::env::var`, not the SSOT exports map**, so it inherits the §3.3 ordering hazard.

**Promoting this binary would ship a new defect while closing an old one.** This is the same shape
the ledger records for `render-ports`, `render-chrony` and `render-nut`: the never-executed Rust
branch had real defects of its own.

---

## 5. Replacement options

| Option | Nested defaults | `$$` escape | Leaves `$NAME` alone | New dep | Verdict |
|---|---|---|---|---|---|
| Upgrade gettext 0.21 → 1.0 | no | no | n/a | none | **rejected** — contract unchanged |
| Keep regex, add passes | **impossible** | patchable | patchable | none | **rejected** — regular languages cannot nest |
| `shellexpand` 3.1.2 | undocumented | **none documented** | **no — expands `$NAME`** | yes | **rejected** |
| Hand-rolled brace-counting parser | yes | yes | yes | **none** | **recommended** |

`shellexpand` is the closest crate and still wrong for this job: it expands the bare `$NAME` form,
which is precisely what must be preserved for systemd runtime references (the 13 correct bare uses
in `mios-agents.service` that resolve via `Environment=`/`EnvironmentFile=`). Its upstream is also
moved (`gitlab.com/ijackson/rust-shellexpand`; the original GitHub repo is unmaintained).

The requirement is not "shell-like expansion". It is a narrow, auditable contract that no
general-purpose crate implements.

---

## 6. Recommendation

Write a **brace-counting recursive-descent expander**, not a regex, with zero new dependencies
(`regex` 1.13.1 is already in both lockfiles but must **not** be used for this). Contract:

1. **Scan for `${` and count braces** to find the true matching `}`. Recurse into the default text
   before resolving the outer reference. This is the only construct that fixes §3.2.
2. **Treat `$$` as an opaque two-character token** consumed verbatim and never inspected — systemd
   owns it (§2.2). Encode it as a rule with a negative control, not as an accident of which forms
   the pattern happens to match.
3. **Never touch the bare `$NAME` form.** Braced `${MIOS_*}` is the only substitution surface;
   everything else belongs to systemd or the shell at runtime.
4. **Resolve from the SSOT exports map**, not `std::env::var` and not a hand-maintained allowlist.
   This dissolves the 121-vs-123 list divergence (Law 9) at its root rather than syncing two lists.
5. **Post-assert and fail the stage** if any `${MIOS_` survives outside a documented no-substitute
   list — the `Image=${MIOS_VERSION_CEPH}` class of breakage is exactly what this catches.
6. **Error loudly on write failure.** No `let _ =`.

**Acceptance is correctness, not byte-parity.** Byte-parity with the current renderer would ship
`PORT=432`. Both renderers are wrong, in different ways, on the same inputs — so parity is not
available as a gate here. The negative controls each need a fixture: a nested default, a `$$`
sequence, a bare `$NAME` that must survive, and an unresolvable `${MIOS_X}` that must fail the
stage.

---

## 7. Corrections to the task record

| task_977 claim | Status |
|---|---|
| `$$` mangling, `case "$true"`, `DIR="$/var/..."`, `KEEP="$7"` | **confirmed** verbatim |
| `PORT="$8432"` | confirmed — and the runtime value is **`432`**, not recorded anywhere |
| nested-default corruption caused by `envsubst` | **wrong** — `envsubst` cannot see `:-` at all; the bash pre-loop at line 43 is the cause |
| a single fixed symptom for `kb.conf.toml` | **incomplete** — four distinct outcomes; see §3.2 matrix |
| `*.socket` omitted by the `find` filter | **already fixed** — `socket` is in `[build.quadlet_render].extensions`; treat as closed |
| two renderers substitute different sets (121 vs 123) | confirmed as a design argument; dissolved by §6.4 rather than repaired |

---

## 8. What remains unverified

- **No bake was run.** Everything here is offline rendering of the shipped files plus `/bin/sh`
  execution of the rendered text.
- **Whether systemd 255 leaves `$8432` literal** was not verified against systemd source; the man
  page is silent on `$` followed by a digit. The shell-level outcome (`432`) is measured and holds
  regardless, since either reading yields a wrong, non-empty port.
- **Which §3.2 row actually ships** depends on the bake environment, which could not be produced
  here (`system-sync-env.sh --dry-run` exits 1 in a build container — the same blocker task_997
  records). Measure this on a host that can render `install.env`.
- `mios-bootstrap.git` was checked for a mirrored renderer (Law 15): **none** — it uses `MIOS_*`
  variables but no `envsubst` and no `:-` expansion loop. No cross-repo update is owed.
