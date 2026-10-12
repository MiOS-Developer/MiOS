<!-- AI-hint: Defines the legal, licensing, and research-oriented scope of the MiOS project, serving as the primary source of truth for usage rights and project status for any agent invoking system entry points.
     AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, mios-build-local, mios-agents, mios-bootstrap, mios-llm, mios-agent-claude, mios-agent-gemini, mios-forge, mios-dev -->
# AGREEMENTS.md

> **Read this before running any entry point.** Every script,
> orchestrator, image build, deployment hook, and agent CLI in this
> repository is dictated under the presumption that the runtime
> agreements and license obligations summarized below are acknowledged
> by anyone who invokes them. Invocation of any entry point listed in
> [Section 4](#4-entry-points-that-acknowledge-this-document) is
> treated as acknowledgment.

---

## 1. What 'MiOS' is

'MiOS' is a name written `MiOS` and pronounced **"MyOS"** -- a
contraction of **"My OS" / "My Operating System"**. The capitalization
is a stylistic choice and carries no other meaning. Wherever the
shorthand `MiOS` appears throughout this codebase, in documentation,
in commit messages, in CLI tools, in package metadata, and in the
deployed image's filesystem (e.g. `/usr/share/mios/`,
`/etc/mios/`, the `mios` user account), it refers to **the same
thing**: a research-grade, single-user-oriented Linux operating system
delivered as an OCI image.

The shorthand never refers to a person, an organization, or a
trademark held by a third party.

## 2. Project nature: research, generative

'MiOS' is a **research project**. It is **not** a commercial product,
not a hardened distribution backed by a vendor SLA, not an
appliance-grade OS image, and not an audited reference platform. It
is published openly so that interested readers, students, security
researchers, system administrators learning bootc / image-mode
workflows, and self-hosters can study, reproduce, fork, or adapt the
work for their own purposes.

The codebase is **generative** in the literal sense: it was
synthesized from a small set of seed scripts and manually-curated
documentation, then iteratively expanded by automated tooling and
human review. Its surface area is therefore broader than its
runtime test coverage. Treat every script, postcheck, lint rule,
configuration default, and architectural claim as an artifact under
ongoing review -- correct in the cases that have been exercised, and
**likely to require adjustment** in cases that have not. File issues
or open pull requests when something does not match your environment;
that is the expected mode of contribution.

## 3. Sources, respectfully acknowledged

Every upstream project, specification, vendor, and community whose
work 'MiOS' depends on or refers to is listed -- with a link to its
canonical home -- in [`usr/share/doc/mios/reference/credits.md`](usr/share/doc/mios/reference/credits.md). License terms for
each component are inventoried in [`usr/share/doc/mios/reference/licenses.md`](usr/share/doc/mios/reference/licenses.md). The
'MiOS'-owned source of this repository is licensed under
[`LICENSE`](./LICENSE) (Apache-2.0).

All references and integrations across this codebase are intended to
be respectful of the original projects: their names, conventions,
and license terms are preserved verbatim where they appear, and no
'MiOS' artifact claims authorship of upstream work. The OpenAI
public API specification, in particular, is treated strictly as a
*public-standard reference* the project conforms to via local
OpenAI-compatible runtimes -- 'MiOS' is not affiliated with, endorsed
by, or partnered with OpenAI.

## 4. Entry points that acknowledge this document

Invoking any of the following entry points is treated as
acknowledgment of this document, the Apache-2.0 license at
[`LICENSE`](./LICENSE), the bundled-component licenses inventoried in
[`usr/share/doc/mios/reference/licenses.md`](usr/share/doc/mios/reference/licenses.md), and the attribution registry in
[`usr/share/doc/mios/reference/credits.md`](usr/share/doc/mios/reference/credits.md). Each entry point displays a banner
referencing this file when it starts.

| Entry point | Repo | Purpose |
|---|---|---|
| `just <target>` | mios.git | All build orchestrator targets (`build`, `iso`, `qcow2`, `vhdx`, `wsl2`, `rechunk`, `sbom`, `init-user-space`, `edit`, `show-env`, ...) |
| `./preflight.sh`, `./preflight.ps1` | mios.git | Build prerequisite checks |
| `./build-mios.ps1` | mios.git | Windows build orchestrator (formerly `mios-build-local.ps1`) |
| `./push-to-github.ps1` | mios.git | GHCR push helper |
| `./Get-MiOS.ps1` | mios.git | Image fetcher |
| `./install.sh`, `./install.ps1` | mios.git | System-side installers (Phase-3) |
| `./install-mios-agents.sh` | mios.git | Agent-launcher installer |
| `./build-mios.sh`, `./build-mios.ps1` | mios-bootstrap.git | Canonical user-facing entry points (formerly `install.{sh,ps1}` / `bootstrap.{sh,ps1}`; old names are now redirector stubs) |
| `mios`, `mios-llm`, `mios-agent-claude`, `mios-agent-gemini` | deployed image | Runtime CLI surface |
| `git push http://localhost:3000/<user>/<repo>` | deployed image | Self-hosted Git forge (Forgejo) served by `mios-forge.container`; the locally hosted `.git = ./` pattern lives here |
| `bootc upgrade`, `bootc switch ghcr.io/mios-dev/mios` | deployed image | OS lifecycle commands operating on a 'MiOS' image |

If you do not agree with the terms inventoried in `LICENSE`,
`usr/share/doc/mios/reference/licenses.md`, or `usr/share/doc/mios/reference/credits.md`, do not invoke these entry points.

## 5. Runtime agreements that apply implicitly

By using a deployed 'MiOS' image you also accept any third-party
agreements that govern bundled vendor components. The notable ones
are listed verbatim in `usr/share/doc/mios/reference/licenses.md`; non-exhaustive highlights:

- **NVIDIA** -- proprietary GPU drivers and CUDA libraries are
  governed by the NVIDIA Software License.
  [`usr/share/doc/mios/reference/licenses.md`](usr/share/doc/mios/reference/licenses.md) carries the link.
- **Steam** -- if installed, the Steam Subscriber Agreement applies
  on first launch.
- **Microsoft Windows VM guests** -- bring-your-own valid licenses
  for any Windows guests run under libvirt/QEMU.
- **Flathub apps** -- each Flatpak shipped or installed via
  `MIOS_DESKTOP_FLATPAKS` carries its own license metadata.
- **Sigstore-signed images** -- if you install signed image policies
  via `bootc switch --enforce-container-sigpolicy`, you accept the
  Sigstore transparency log and Fulcio identity attestation model.

These are not 'MiOS'-specific terms. They are the terms of the
upstream vendors and projects whose components 'MiOS' integrates;
'MiOS' merely surfaces them at install time.

## 6. Disclaimers

- **No warranty.** Apache-2.0's "AS IS" clause governs the 'MiOS'-
  owned source. No 'MiOS'-shipped component carries a warranty
  beyond what its upstream license already grants (which is, for the
  open-source components, generally none).
- **Research-grade testing.** Continuous integration covers the build
  pipeline, image lint, and postcheck invariants. CI does **not**
  cover full hardware matrix testing, multi-host upgrade drills,
  long-running stability, or production failure modes. Reports of
  what does and does not work on real hardware are welcome.
- **Trademark non-claim.** 'MiOS' is a project shorthand; all
  third-party trademarks (Fedora, Universal Blue, NVIDIA, OpenAI,
  Anthropic, Google, GitHub, Cline, Cursor, Microsoft, ...) belong to
  their respective owners and are referenced solely to identify the
  upstream component or specification they are part of.
- **No data exfiltration.** The deployed image's only outbound
  network calls are: package mirrors during `bootc upgrade`, the
  GHCR registry for image fetch, the user's chosen Quadlet workloads,
  and the user's local AI runtime at `MIOS_AI_ENDPOINT`. There is no
  telemetry channel built into the image. (Operators can verify by
  inspecting `/etc/containers/systemd/`, `/usr/lib/systemd/system/`,
  and the active firewalld policy.)

## 7. Acknowledgment language for entry-point banners

Entry-point scripts print a one-line banner pointing here. The
canonical form is:

```
[mios] By invoking this entry point you acknowledge AGREEMENTS.md
       (Apache-2.0 main + bundled-component licenses in usr/share/doc/mios/reference/licenses.md +
        attribution in usr/share/doc/mios/reference/credits.md). MiOS is a research project.
```

The banner is informational; there is no interactive y/n prompt by
default, since CI and unattended deployments need to run without
console interaction. Operators who want a hard gate can wrap any of
the entry points in `MIOS_REQUIRE_AGREEMENT_ACK=1 ./entrypoint.sh`
and supply the corresponding handler in their wrapper.

## 8. Single canonical user-config dotfile

There is **one** file that holds every user choice (account, hostname,
groups, locale, auth policy, network posture, AI endpoint and model,
desktop session, flatpak picks, base image refs, build args, profile
features, free-form env vars, per-Quadlet enable flags). That file is
`mios.toml`, present in three layers:

| Layer | Path | Owned by | Mutability |
|---|---|---|---|
| Vendor | `/usr/share/mios/mios.toml` | `mios.git` | image-immutable |
| Host | `/etc/mios/mios.toml` | bootstrap (staged) | admin-editable |
| Per-user | `~/.config/mios/mios.toml` | per Linux user | user-editable |

The user's editable canonical copy in the bootstrap repository lives at
`mios-bootstrap.git/mios.toml` (repo root). Bootstrap's `install.sh`
stages it to `/etc/mios/mios.toml` at install time; the per-user copy
is seeded from `/etc/skel/.config/mios/mios.toml` on `useradd -m`.

Every script that needs a value -- in `mios.git`, `mios-bootstrap.git`,
the deployed image's `usr/bin/mios` CLI, the `Justfile`, the entry-point
scripts -- resolves through `tools/lib/userenv.sh` (in `mios.git`),
which deep-merges the three layers in order (vendor → host → user) and
exports `MIOS_*` environment variables plus a verbatim `[env]` table.
Higher layers shadow lower layers field-by-field; user-set fields
supersede defaults.

To change anything globally for your deployment, edit
`mios-bootstrap.git/mios.toml` once and re-run `bootstrap.sh` /
`install.sh`. To change anything just for yourself on a deployed host,
edit `~/.config/mios/mios.toml` and run `just init-user-space` (or
re-source `tools/lib/userenv.sh` in your shell).

## 9. Pointers

- [`LICENSE`](./LICENSE) -- Apache-2.0 main license
- [`usr/share/doc/mios/reference/licenses.md`](usr/share/doc/mios/reference/licenses.md) -- bundled-component license inventory
- [`usr/share/doc/mios/reference/credits.md`](usr/share/doc/mios/reference/credits.md) -- attribution registry (every
  upstream project, dependency, application, repo, file, and pattern)
- [`SECURITY.md`](./SECURITY.md) -- security posture and hardening
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) -- contributor conventions
- [`README.md`](./README.md) -- project overview
