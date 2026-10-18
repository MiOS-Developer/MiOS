<!-- AI-hint: Consolidate the ~1,500-file script estate into a handful of function-named Rust static binaries (gate/gen/resolve/serve/probe) over a shared mios-ssot crate, ported strangler-style one gate at a time with byte-identical parity plus negative controls, cross-compiled and fully static, vendored offline, never panicking, gated by a shrink-only script-mass ratchet. Read before writing any new generator, gate, verb backend or service, or before choosing a language for one. -->
<!-- AI-related: usr/share/doc/mios/adr/0011-unified-languages-and-file-patterns.md, ROADMAP.md (WS-LANG), usr/share/mios/mios.toml [rust], [laws] id 14, src/mios-rs/, tools/native/, automation/98-drift-checks.sh -->
---
adr: 0021
title: Rust static binary consolidation — a handful of function-named binaries over one SSOT crate
status: accepted
date: 2026-09-16
deciders: [operator, ai-pair]
tags: [rust, languages, consolidation, static-binaries, tooling, gates, tech-debt]
laws: [5, 7, 8, 9, 12, 13, 14, 16]
ssot_keys: ["legibility.max_tooling_python_lines", "legibility.python_ai_plane_prefixes"]
related_ws: [WS-LANG, WS-DEBT, WS-TEMPLATE]
supersedes: [0023]
superseded_by: []
---

# ADR-0021: Rust static binary consolidation — a handful of function-named binaries over one SSOT crate

## Status

accepted — 2026-09-16

Refines ADR-0011 §2 (language-per-domain) rather than superseding it: the
language contract stands, and this ADR fixes the *shape* of the native tier that
ADR-0011 left open. It **reverses one commitment** — ADR-0011 / WS-LANG task_3200
proposed "subcommands `build|drift|verb|resolve|render|cat|scaffold|fmt` → one
`miosd` static musl binary", a multicall design. See Rationale §1.

## Context

Law 14 (TARGET-LANGUAGES) is a *floor*: new native code is Rust, and what exists
is grandfathered. The standing operator directive in `CLAUDE.md` ("Rust static
binaries, globally … all of it converts") is the *destination*. Nothing between
the two said how the destination is shaped, so the native tier grew in two
directions at once.

Measured against the tree at the time of writing:

| Language | Files | Lines |
|---|---:|---:|
| Python | 1,168 | 197,345 |
| bash | 283 | 41,273 |
| PowerShell | 50 | 24,776 |
| Rust | 101 | 20,437 |

The Rust that exists is fragmented across **two workspaces** — `tools/native/`
(14 crates, 57–1,841 lines each, plus `xtask`) and `src/mios-rs/` (`miosd`
5,260, `mios-node` 5,838, `mios-config` 819, `mios-build`) — and one crate,
`mios-wallpaperd`, exists in **both** with two different implementations
(`tools/native/mios-wallpaperd` has `guiwatch.rs`/`host.rs`/`workerw.rs`;
`src/mios-rs/crates/mios-wallpaperd` is a single `main.rs` and is not even a
workspace member). Only `tools/native` is linted by `check_native_lint`.

The script estate has a latent structure nobody declared: `tools/` alone holds
39 `check-*`, 36 `test_check-*`, 18 `generate-*` and 4 `render-*` scripts. The
names already sort by *function*. Twenty more crates added one at a time would
produce twenty binaries to sign, label, ship and audit, for work that groups
into five verbs.

Three constraints bound any answer. Law 12 (BAKE-NOT-FETCH) forbids network at
bake, so dependencies must be vendored. Law 13 (NATIVE-DROPINS) requires the
`mios_toml.py` and `userenv.sh` resolver twins to agree, so a third reader must
join that contract rather than invent a fourth cascade. Law 16
(ONE-TEMPLATE-PER-TYPE) means these files are scaffolded, not hand-rolled.

## Decision

**1 — Shape.** Six to eight binaries, named by **function**, systemd-style:
`mios-gate`, `mios-gen`, `mios-resolve`, `mios-serve`, `mios-probe`. Lifecycle
(build-time vs. runtime) is expressed by *which* binaries the image ships, not
by a second naming axis. The registry is SSOT: `[rust.categories]`.

**2 — Invocation.** Separate real binaries, **not** a multicall dispatcher.
Legacy call sites keep working through `tmpfiles.d` `L+` symlink shims, the
mechanism the repo already uses (e.g. `L+ /usr/local/bin/mios-sys-env`).

**3 — Location.** One cargo workspace at `src/mios-rs/`. The 14 crates under
`tools/native/` are **absorbed** as modules of the category crates; their
binaries survive as shims. `tools/native/mios-wallpaperd` is the canonical
wallpaper daemon; the orphaned `src/mios-rs/crates/mios-wallpaperd` is deleted.

**4 — Linking.** "Static" means **portable across operating systems**, not
merely self-contained on Fedora. The workspace cross-compiles per target:
`x86_64-unknown-linux-musl` fully static for the image and the initramfs, plus
Windows targets for the host-side surface PowerShell holds today. No binary may
depend on the host's glibc or NSS — so no `getpwnam` in a ported tool; user and
group facts come from SSOT or from reading `/etc/passwd` directly.

**5 — SSOT.** One library crate implements the three-layer cascade and every
binary depends on it. It does not replace the Python and bash readers: putting a
binary dependency into the bash bootstrap path, before the binary is guaranteed
to exist, would trade a parity risk for a boot risk.

> **Corrected after measuring (Law 15).** This decision was written as "create a
> new `mios-ssot` crate, which becomes a third twin". That crate already exists
> under another name. `tools/native/mios-resolver` is 1,841 lines implementing
> exactly this: `layers.rs` builds the tier-major stack (vendor < vendor.d <
> host < host.d < user < user.d), `merge.rs` the overlay, `ports.rs` the
> `[ports.categories]` derivation, `aliases.rs` the canonical-name map, and four
> emitters (shell, PowerShell, JSON, `install.env`). It is *already* graded as
> the third twin by `check_resolver_differential_parity` and
> `tools/check-resolver-twin.py`.
>
> So the work is not to write a resolver; it is that **nothing else uses the one
> we have**. `src/mios-rs/mios-config` (819 lines, depended on only by `miosd`)
> is a *second* Rust reader whose `load_default()` reads the vendor
> `usr/share/mios/mios.toml` and merges `Env::prefixed("MIOS_")` — one layer, no
> `/etc`, no `~/.config`, no `mios.d` fragments, no port derivation. A Rust
> consumer going through it silently disagrees with both other twins. Measured:
> **11 Rust files parse `mios.toml` without the resolver crate.** The decision
> stands; its subject is renamed and its scope is consolidation, not creation.
>
> **And it partly contradicts an accepted decision.** `ADR-0023`
> (originally recorded as `docs/adr/0005-unified-native-resolver.md` and promoted
> under task_958) — formerly invisible because `docs/adr/` was a *second* ADR
> namespace with its own 0004/0005 colliding with the canonical ones, outside
> the ADR template, the ADR index and `check_template_conformance` — decided:
> "Collapse all SSOT resolver surfaces into a single compiled Rust crate:
> `tools/native/mios-resolver`", with a strangler cutover sequence (shell →
> PowerShell → Python → install.env → names registry) and `[migration]` rollback
> toggles. That is *replacement*, which this ADR refines to library consolidation
> (ADR-0023 is now marked superseded by ADR-0021 §5). The reconciliation is task_958.
>
> Measured, and this is the part that matters: `[migration]` declares
> `use_rust_resolver_shell`, `_powershell`, `_python` and `_install_env` all
> **`true`**, and **nothing reads any of them**. The cutover ADR-0005 accepted
> was never wired; the toggles that claim it is live are inert, and so is the
> rollback safety they were the mechanism for. task_959.

**6 — Output contract.** Human text on stdout by default, with today's exit
codes preserved exactly — `0` clean, `1` violations, `2` could-not-run — so the
bash gate, its logs and byte-parity are unchanged. `--format json` is opt-in and
emits an **OpenAI-format structured-output schema**, per the global
schema directive, so the agent plane consumes findings natively.

**7 — Failure policy.** No panics in any gate or generator path:
`clippy::unwrap_used` and `clippy::panic` are denied. Malformed TOML, a missing
file, an unreadable directory each print what failed and where, then exit **2**.
Never exit 0 on an input that could not be read — that is Skip-as-Pass, and the
existing suite already asserts that no check answers a missing subject with a
bare 0.

**8 — Build.** A Containerfile **builder stage** compiles; the final stage
`COPY`s the binaries in, so no Rust toolchain ships in the image. Dependencies
are `cargo vendor`-ed and committed with `Cargo.lock`, and `[rust.dependencies]`
carries a dependency allowlist the gate enforces.

**9 — Migration.** Strangler, **gate by gate**. The bash gate keeps dispatching;
one check at a time it dispatches to the binary instead. Parity is
**byte-identical output on the real tree** — same violations, same order, same
exit code — **and** each planted negative control must still fail *for the
planted reason*. The script is deleted in the **same commit** that proves
parity: no dual-maintenance window, no ambiguity about which is authoritative,
and `git revert` is the rollback.

**10 — Tests.** `cargo test` in-crate for units; the ported gate's negative
controls join `just drift-gate`, so the half that proves a check *can fail*
stays continuous rather than living in a merged PR description.

**11 — Ratchet.** Script mass is shrink-only. This lands as an **extension of
the existing `[legibility]` ratchet**, not a parallel `[rust.script_mass]`
table: `[legibility]` already ceilings `max_shell_lines` and `max_ps_lines`
under `check_legibility_ratchet`, with a negative test behind it. The gap was
Python, so `max_tooling_python_lines` joins them, with the AI plane exempted by
`python_ai_plane_prefixes` because Law 14 keeps it in Python. A second table
would have been a second mechanism to keep honest — and, with nothing reading
it on the day it landed, a fresh entry in the dead-SSOT register this work is
draining.

**12 — Tracking.** WS-LANG is extended; no new workstream. One `T-####` per
category binary, each carrying its own parity DoD.

**13 — Order.** `mios-probe` first (task_947, the `[preflight]` host probe). It is
greenfield, so the first attempt carries no parity risk while the shared crate,
the cross-compile, the builder stage and `--format json` are all being invented
at once.

## Rationale

**1 — Why separate binaries over multicall.** The operator's criterion was
"whichever is more secure, performant and auditable". Multicall wins only on
image size. Separate binaries win on all three: security, because each binary
carries its own SELinux label and file capabilities, where a multicall binary
holds the union of every subcommand's privileges and cannot drop what is linked
in; performance, because of a smaller resident set and no dispatch table; and
auditability, because each capability is one hash, one SBOM row, one
bound-image signature. The symlink shims preserve the strangler property, so no
call site changes on the day a port lands.

**2 — Why function, not lifecycle or domain.** `tools/` already sorted itself
that way — 39 `check-*`, 18 `generate-*` — and the drift-gate already invokes
checks as a uniform verb. A lifecycle axis would cut across that grouping and
force every binary to answer two naming questions instead of one.

**3 — Why a shared SSOT crate rather than per-binary parsing.** Law 13 exists
because two readers of the same cascade drift. Eight readers would drift eight
ways. One crate is one place to be correct, and one place for the twin test to
point at.

**4 — Why byte-identical parity and not "same violation count".** A count can
match while the binary finds different things; the dev-loop taxonomy names that
a Count-Only Ratchet, a check that cannot fail. Output equality plus surviving
negative controls is two-sided: the port must find what the script found, and
must still miss nothing the script caught.

**5 — Why delete the script in the same commit.** A fallback switch means two
implementations that must stay in sync — the exact divergence Law 13 was written
about — and an ambiguity about which one is authoritative that outlives whoever
added the switch.

**6 — Why a ratchet now rather than after three ports.** The script count grows
while the migration is young, so a ceiling set later is a worse ceiling. A
shrink-only register also makes a deliberate exception visible instead of silent.

## Consequences

- **Gained:** one toolchain for the native tier; per-capability signing and SBOM
  rows; a single cascade implementation; gates that cannot silently skip; a
  machine-readable findings surface the agent plane can consume; a ratchet that
  makes script growth a build failure rather than a trend.
- **Paid:** a cargo workspace, vendored crate sources and a builder stage become
  part of the build's critical path. Cross-compiling for Windows means the
  host-side surface is built, not interpreted, so a change there needs a
  compile. Fully static rules out NSS, so any tool that wanted `getpwnam` must
  be rewritten to read SSOT or `/etc/passwd`.
- **Reversed:** ADR-0011 §2 / WS-LANG task_3200's single-`miosd`-multicall shape.
  `miosd` remains as a daemon and keeps the subcommands it already serves; it
  does not become the container for the whole native tier.
- **Open:** whether the resolver crate eventually *replaces* the Python and bash
  twins rather than joining them (deferred — it needs the binary to be
  guaranteed present before bash needs it); whether
  `check_resolver_differential_parity`'s "advisory skip" when the binary is not
  built should stay a skip, since a skip reads as a pass; whether the
  `[templates.quadlet]` umbrella and
  `[templates.quadlet-container]` specific type should both be scaffoldable to
  the same destination.
- **Done when:** the `[legibility]` script ceilings have fallen to the point where
  every `check-*`, `generate-*`, `render-*` and `resolve` surface is a binary,
  `tools/native/` holds no crate of its own, and Law 14's "grandfathered"
  clause has nothing left to grandfather.

## Upstream exceptions

The static-linkage rule governs native code MiOS builds from its own workspaces. An upstream
application MiOS ships as built by its upstream is not MiOS native code, and it may link
dynamically only through an explicit, scoped exception in `mios.toml`:

- `[build.native.linux].audit_scopes` names the install trees the gate scans in full, beside
  MiOS's own binaries, without the native-name filter. An ELF in an audit scope is either covered
  by an exception or audited like MiOS code.
- Each `[[build.native.linux.exceptions]]` entry names its `binaries`, its one `scope` (a
  relative path that must be an audit scope) and a `reason`. A listed name is exempt only inside
  its scope (or a staged copy whose path ends in that scope); the same name anywhere else is
  audited. An entry without binaries, a relative scope or a reason exempts nothing and is itself a
  finding.
- `mios-gate static-linkage --census` reports each exempt binary with `exempt_scope` and
  `exempt_reason`, so the exception is visible in the audit output, not only in the SSOT.

The one exception today is MiOS-Portal (`[desktop.portal.gecko]`): Firefox built with Surfer and
installed under `usr/lib/mios/portal` by phase 67. libxul and its helpers link glibc, GTK, Wayland
and NSS/NSPR by design, and the artifact lane ships Mozilla's own compiled libxul. Scope consequences:

- Removing the exception does not drop the binaries from the scan; they fail as dynamic ELFs.
- A Gecko release that adds a new shared object fails the gate until the exception lists it, so
  the scope can only grow by a reviewed SSOT change.
- `/usr/bin/mios-portal` must stay a launcher (script or symlink) rather than a MiOS crate; a
  MiOS-built binary of that name would be audited under the native rules like any other.
- `check_portal_product` (tools/drift-checks.py `portal-product`) fails when the exception stops
  naming the product binary, `libxul.so` or `plugin-container`, loses its reason, or leaves the
  audit scopes.
