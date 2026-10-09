<!-- AI-hint: Upstream audit behind task_997 -- what systemd, bash and podman each do with the five characters Law 10 forbids, and what the real install.env render actually carries. Measured, not inferred. -->
<!-- AI-related: automation/99-postcheck.sh, usr/libexec/mios/system-sync-env.sh, usr/lib/mios/userenv.sh, TASKS.md task_997, task_991 -->

# Upstream Audit — the three env-file parsers Law 10 exists to reconcile

**Audit ID:** `UPSTREAM-envfile-three-parsers`
**Component:** Law 10 BARE-SAFE-ENV enforcer, `automation/99-postcheck.sh:515-543` (task_997)
**Parsers audited:** systemd `EnvironmentFile=` (v255), `bash source` (5.2.21), podman `--env-file` (undocumented format)
**Producer audited:** `usr/libexec/mios/system-sync-env.sh` → `/etc/mios/install.env`
**Auditor:** research pass, no code changed

---

## 1. Executive summary & recommendation

- **Verdict:** `REFRAME` — the enforcer gap task_997 records is real, but the defect it predicts cannot
  occur, and a **different, live defect** sits behind it.
- **Risk level:** HIGH (Law 5's contract variable is absent from the file 29 consumers read it from)
- **Summary:** task_997 assumes non-bare values reach `install.env` and are then read differently by
  three parsers. **They cannot.** The producer's `emit()` already filters **all six** characters —
  whitespace, `"`, `'`, `$`, backtick, `#` — and the real render is clean, measured at **0**
  occurrences. But `emit()` handles a rejected value by **silently dropping the whole variable** and
  returning 0. `MIOS_AI_ENDPOINT` is dropped on every render. The postcheck then reads that render
  with `2>/dev/null`, discarding the only evidence that anything went missing.

**The enforcer is not weak in the way recorded. It is pointed at a class of defect the producer
already prevents, while the producer's actual failure mode passes all four assertions.**

---

## 2. Upstream contract — what each parser really does

The three-way disagreement Law 10 asserts is **real and confirmed from primary sources**, so the law
is correctly motivated even though its enforcer is aimed wrongly.

### 2.1 systemd `EnvironmentFile=` (v255)

`exec_context_load_environment()` in `src/core/execute.c` calls **`load_env_file()`**, not
`merge_env_file()`. This is the whole answer to the expansion question:

> `merge_env_file_push` … calls `replace_env(value, *env, REPLACE_ENV_USE_ENVIRONMENT|REPLACE_ENV_ALLOW_BRACELESS|REPLACE_ENV_ALLOW_EXTENDED, …)`
> — "this function supports braceful and braceless variable expansions … **unlike other exported
> parsing functions**."
> — `src/basic/env-file.c`, systemd v255

`load_env_file` and `parse_env_file` are those "other exported parsing functions". They do **not**
expand. Corroborated by the man page:

> "Variable expansion is not performed inside the strings, however, specifier expansion is possible.
> The `$` character has no special meaning."
> — `systemd.exec(5)`, *EnvironmentFile=*

But `parse_env_file_internal()`'s state machine **does** strip quotes (`SINGLE_QUOTE_VALUE` /
`DOUBLE_QUOTE_VALUE` return to `PRE_VALUE` without accumulating the delimiters), honours backslash
escapes and line continuation, and treats `#` as a comment **only in the `PRE_KEY` state** — mid-value
`#` is literal.

### 2.2 `bash source` — measured locally, bashrc-free

`env -i bash --noprofile --norc -u -c '. file'`, one character per file:

| Value | probe rc | bash's value |
|---|---|---|
| `K=http://localhost:${D}/v1`, `D` set **first** | 0 | `http://localhost:8700/v1` |
| `K=http://localhost:${D}/v1`, `D` set **last** | 127 | `http://localhost:/v1` |
| `K="quoted"` | 0 | `quoted` |
| `K='quoted'` | 0 | `quoted` |
| `K=two words` | 127 | *(unset — bash ran `words` as a command)* |
| ``K=`id -u` `` | 0 | **`0`** — command substitution **executed** |
| `K=a#b` | 0 | `a#b` |

### 2.3 podman `--env-file` — the format is undocumented

`podman-run(1)` says only: *"Read in a line-delimited file of environment variables."* Nothing about
quoting, expansion or comments. The behaviour is therefore pinned by source and issue history:

- **Quotes are kept**, not stripped — `dquote="abc"` yields a value **including** the quotes
  ([#19565](https://github.com/containers/podman/issues/19565)).
- **No variable expansion** — the literal text is the value.
- **`#` mid-value is literal** — 4.7.0 briefly stripped everything after `#`
  ([#20255](https://github.com/containers/podman/issues/20255): `DB_PASSWORD=thisIs*a#Test` became
  `thisIs*a`), and that was **reverted** by [PR #20256](https://github.com/containers/podman/pull/20256)
  (milestone 4.7, merged 2023-10-05), restoring 4.6.2 behaviour.

**Two-way deprecation check.** The 4.7.0 multiline/quote-stripping work is *not* the current
direction — it was reverted as breaking, and the issue asking for bash-like quote handling is
**closed as not planned and locked**. There is no upstream version to move to that makes podman agree
with systemd. The divergence is permanent and must be designed around.

### 2.4 The resulting matrix

| Value in the file | systemd | bash `source` | podman |
|---|---|---|---|
| `K=${D}/v1` | literal `${D}/v1` | **expands** | literal `${D}/v1` |
| `K="q"` | `q` | `q` | **`"q"`** |
| `K='q'` | `q` | `q` | **`'q'`** |
| `K=two words` | `two words` | **error** | `two words` |
| ``K=`id -u` `` | literal | **executes** | literal |
| `K=a#b` | `a#b` | `a#b` | `a#b` |

Every one of Law 10's five characters has at least one parser that disagrees with the other two.
**The law is correct.** Only `#` is currently unanimous, and that is a post-revert accident, not a
guarantee.

### 2.5 Why this is not theoretical — the same file, both parsers, one unit

`[Container] EnvironmentFile=` maps to **`--env-file`** (podman's parser); `[Service]
EnvironmentFile=` is **systemd's**. Confirmed in `podman-systemd.unit(5)`:

> `EnvironmentFile=/tmp/env` → `--env-file /tmp/env`

`mios-llm-light.container` and `mios-pgvector.container` declare `EnvironmentFile=/etc/mios/install.env`
in **both sections**. The same bytes are parsed by both engines for the same unit. 13 Quadlets and 27
units in total read this file.

---

## 3. What the real render actually contains — measured

task_997 records this as unmeasured because `system-sync-env.sh --dry-run` exits 1 in a build container.
**The cause is a single path lookup**, not an environmental impossibility:

```
Mios-sync-env: resolver /usr/lib/mios/userenv.sh not found
```

The resolver exists in-tree at `usr/lib/mios/userenv.sh`. Repointing `RESOLVER` at the in-tree copy
(script copied to scratch, one `sed`; the repo was not modified) produces the render: **rc=0, 84
lines.**

### 3.1 The producer already enforces Law 10 — completely

`system-sync-env.sh:30`:

```bash
_ENV_UNSAFE='[[:space:]"'"'"'$`#]'
emit() {
    local _k="$1" _v="$2"
    if [[ "$_v" =~ $_ENV_UNSAFE ]]; then
        printf 'mios-sync-env: WARN skip %s (value unsafe for a bare env file)\n' "$_k" >&2
        return 0
    fi
    printf '%s=%s\n' "$_k" "$_v"
}
```

Decoded against each character, the class rejects **space, `"`, `'`, `$`, backtick, `#`** and passes
ordinary text. That is Law 10's five **plus** single-quote — a superset of what the law names and a
strict superset of what the enforcer tests.

**Measured on the real render: `0` values carry any forbidden character.** task_997's central
unmeasured question is now answered, and the answer is the opposite of the assumption: `install.env`
is bare, structurally, by construction.

### 3.2 The real defect — `return 0` on rejection

A rejected value is not repaired, not reported to the caller, and not emitted. **The variable simply
does not exist in the output.** Seven are dropped on every render:

| Dropped key | Pre-emit value | Rejected for |
|---|---|---|
| `MIOS_AI_ENDPOINT` | `http://localhost:${MIOS_PORT_AGENT_PIPE}/v1` | `$` |
| `MIOS_USER_FULLNAME` | `MiOS Operator` | whitespace |
| `MIOS_A2O_LANE_A_ROLE` | `framework + ~80%` | whitespace |
| `MIOS_AI_BACKEND`, `MIOS_A2O_LANE_B_MODEL`, `MIOS_A2O_LANE_B_ROLE`, `MIOS_A2O_CLAUDE_EFFORT_FLAG` | — | — |

`MIOS_AI_ENDPOINT` is **Law 5's single contract variable**. It is referenced by **29** tracked
consumers — `mios-node.service`, `mios-node.container`, `agent-pipe`'s `server.py`, `mios_httpx.py`,
`mios_persona.py`, `mios_gateway_queue.py`, `gateway-agent`, `mios_toml.py` — and it is **absent from
the file they read it from**. Not wrong. Absent.

This is strictly worse than the non-bare value task_997 predicted: a literal `${MIOS_PORT_AGENT_PIPE}`
at least fails loudly at the first API call. An absent variable falls through to whatever default
each of the 29 consumers happens to carry, independently.

### 3.3 The gate cannot see any of it

Replaying all four assertions against the **real** render: every one passes, and passes *honestly* —
the file genuinely is bare, genuinely is `KEY=value`, genuinely is secret-free, genuinely sources
clean under `set -u`. The gate is not lying about the file. **It is silent about the file's
omissions**, and it is reading the render at `99-postcheck.sh:519` as:

```bash
if ! _env_render="$(bash "$_sync_env" --dry-run 2>/dev/null)"; then
```

`2>/dev/null` **discards the seven WARN lines** — the only signal that seven variables, one of them
the Law 5 contract, were dropped. The producer reports the defect on exactly the stream the gate
throws away.

---

## 4. Corrections to the task record

| task_997 claim | Status |
|---|---|
| The enforcer tests 2 of the 5 characters named by the law | **confirmed** — only `="` and the `set -u` probe; no `'`, whitespace, backtick or `#` test |
| The `set -u` probe discriminates on ordering, not bareness | **confirmed** — reproduced exactly (rc=0 dep-first, rc=127 dep-last) |
| `install.env` may carry `$` values | **wrong** — the producer's `emit()` filter makes it impossible; measured 0 |
| Consequence is "Law 5's contract reaching two of three consumers wrong" | **wrong, and understated** — it reaches **none** of them; the variable is not emitted at all |
| Whether the real render can be produced is blocked | **wrong** — blocked only by a resolver path; the render is 84 lines, rc=0 |
| env-baseline's 104 unresolved `${...}` show install.env is unsafe | **no** — env-baseline is a sibling generator with **no** `emit()` filter; it shows the *resolver* emits unexpanded cross-references, which is the upstream cause of the drop, not evidence about install.env |

**New, not previously recorded:** a backtick value is **executed** by the `set -u` probe itself
(``K=`id -u` `` → `K=0`, rc=0). The probe runs the render as code inside the build gate. The producer
filter is what keeps this unreachable today — the gate has no defence of its own.

**New:** a whitespace value that is *not* the last line is masked entirely — `source` returns the last
command's status, so `K=two words` followed by any valid line yields rc=0. task_997 measured the
ordering effect for `$`; it applies to whitespace too, for a different reason.

---

## 5. Recommendation

The enforcer should stop trying to catch characters the producer cannot emit, and start catching the
producer's actual failure. Four changes, in priority order:

1. **Stop discarding the producer's stderr.** Drop `2>/dev/null` at `99-postcheck.sh:519`, capture
   the WARN stream, and **fail the stage on any `WARN skip`**. This alone turns the live
   `MIOS_AI_ENDPOINT` drop from invisible to fatal, and needs no new logic.
2. **Make `emit()` fail instead of returning 0.** A value that cannot be represented in a bare env
   file is a resolver bug, not a value to discard. This is the root fix; it belongs with task_991/the
   resolver's unexpanded cross-references, since `${MIOS_PORT_AGENT_PIPE}` should have been expanded
   before `emit()` ever saw it.
3. **Add a closure assertion:** every `MIOS_*` name a tracked consumer reads from `install.env` must
   be *present* in the render. This is Law 9's referenced-⊆-emitted shape applied to the file rather
   than the resolver, and it is what actually protects the 29 consumers.
4. **Keep the per-character tests, but as negative controls on `emit()`,** not on the render — one
   fixture per character, each asserting the producer *rejects* it. Testing the render for characters
   the producer filters is a gate that cannot fail; the ledger already names that shape as a defect
   class.

Retain the `set -u` probe, but it can no longer stand in for bareness, and it must assert on
`source`'s own return value rather than the last command's (§4) — and should not be the mechanism
that would execute a backtick if the filter ever regressed.

**Acceptance is the drop, not the characters.** A test suite that proves all five characters are
caught would pass today against a render that is already clean, while `MIOS_AI_ENDPOINT` stays
missing.

---

## 6. What remains unverified

- **No podman binary was available** in this environment. Its behaviour is established from the
  vendor issue tracker, the reverted PR and the docs' silence — strong, but not locally reproduced.
  Reproduce §2.4's podman column on a host with podman before relying on the quote row.
- **The render was produced with the in-tree resolver**, not the deployed `/usr/lib/mios/userenv.sh`.
  If the deployed resolver differs, the set of seven dropped keys could differ. Re-measure on a booted
  host.
- **Whether the 29 consumers actually break** when `MIOS_AI_ENDPOINT` is absent was not traced —
  several read it through `mios_toml.py`, which may re-resolve from the TOML cascade and mask the
  drop for the Python plane while leaving `mios-node.service`/`.container` exposed. Worth tracing
  before sizing the fix.
- **systemd's parser was read, not run.** The quote-stripping and `#` claims come from the v255 state
  machine and man page; no unit was loaded to confirm.
- **Law 15 cross-repo check:** `mios-bootstrap.git` has **no** mirrored enforcer and **no** producer
  for `install.env` — its hits are documentation (`variables.md`, `bootstrap_install.md`,
  `user-space.md`), the knowledge graphs, and `build-mios.ps1`. **No cross-repo update is owed**, but
  `usr/share/mios/knowledge/upstream-gaps.json` mentions this surface and should be re-checked if the
  contract changes.
