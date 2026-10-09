<!-- AI-hint: mios.toml (the SSOT), mios.html (the configurator), and the MiOS Portal are ONE config surface served on the `agent_pipe` port by agent-pipe — the same single front door that serves the OpenAI /v1 API (ADR-0006). The Portal is the shareable web LINK that bootstraps the whole pipeline (open → configure → deploy); everything reads/writes mios.toml (Laws 7/8). Read before adding any config UI, settings page, or deployment-config path. -->
<!-- AI-related: usr/share/mios/mios.toml [portal], usr/share/mios/configurator/mios.html, usr/share/mios/portal/, usr/lib/mios/agent-pipe/mios_portal.py, usr/lib/mios/agent-pipe/server.py, tools/mios-portal-app/, usr/share/doc/mios/adr/0006-openai-api-only-ai-contract.md, usr/share/doc/mios/adr/0007-governance-model-laws-adrs-spec.md -->
---
adr: 0009
title: Unified config surface — mios.toml ⇄ Portal + configurator + OpenAI /v1, all on the agent_pipe port
status: accepted
date: 2026-07-16
deciders: [operator, ai-pair]
tags: [config, portal, configurator, ssot, front-door, openai, shareable-link, sovereignty]
laws: [5, 7, 8]
ssot_keys: [portal, ports.agent_pipe]
related_ws: [WS-CONFIG, WS-DEPRED]
supersedes: []
superseded_by: []
---

# ADR-0009: Unified config surface — `mios.toml` ⇄ Portal + configurator + OpenAI `/v1`, all on the `agent_pipe` port

## Status
Accepted — 2026-07-16 (Laws 5, 7, 8). The decision is in force; the folding of
`mios.html` into the Portal is PLANNED (WS-CONFIG / task_3235) — see Consequences
for the honest DONE-vs-PLANNED split. This ADR converges with ADR-0006 (the single
OpenAI front door on the `agent_pipe` port) and ADR-0007 (governance): the same
port is now the single **web** front door (Portal + configurator) *and* the single
**API** front door (`/v1`).

## Context

MiOS has three things that all "configure MiOS," and until now they read as three
surfaces:

- **`mios.toml`** — the single source of truth for every operator-tunable value
  (597 KB; resolved through the vendor `/usr` < host `/etc` < user `~/.config`
  cascade). Everything derives from it (Law 8 SSOT-PROJECTION).
- **`mios.html`** — the standalone **configurator** at
  `usr/share/mios/configurator/mios.html`, a web UI over `mios.toml`.
- **the MiOS Portal** — the operator's home page (`[portal]` in `mios.toml`
  L220–232; assets `usr/share/mios/portal/`; backend
  `usr/lib/mios/agent-pipe/mios_portal.py`; served at `GET /` on the `agent_pipe`
  port; with a native Android client at `tools/mios-portal-app/`). It presents
  service tiles, a terminal (xterm.js), login, and the entry to everything MiOS
  runs.

The tension the operator named: the Portal "needs config too." Treating the
configurator and the Portal as separate surfaces means two web UIs, two things to
serve, two things to secure, and an ambiguous answer to "where do I configure
MiOS?" Meanwhile ADR-0006 already collapsed the *API* plane to a single OpenAI
`/v1` front door at agent-pipe (port key `agent_pipe`). The Portal is already
served by that same agent-pipe at `GET /`. So the web surface and the API surface
already live at the same port — they had simply not been *named* as one unified
config surface.

## Decision

**`mios.toml`, `mios.html`, and the MiOS Portal are ONE config surface, served on
the `agent_pipe` port by agent-pipe.** Concretely:

1. **Fold the configurator into the Portal.** `mios.html`
   (`usr/share/mios/configurator/`) becomes a **view within the MiOS Portal**, not
   a separate page. The Portal is configured *through the surface it is* — the
   answer to "the Portal needs config too" is that its configuration is a tile/view
   of itself. The Portal backend (`mios_portal.py`) and the agent-pipe server
   (`server.py`) own the read/write of `mios.toml`.

2. **One port, one front door — web AND API.** The SAME `agent_pipe` port that
   ADR-0006 made the single OpenAI `/v1` **API** front door is now the single
   **web** front door: `GET /` serves the Portal (with the configurator folded in)
   and `/v1/*` serves the OpenAI API. `ports.agent_pipe` is the one addressable
   surface. This is the ADR-0006 convergence: the OpenAI `/v1` contract and the
   config UI share the exact same door.

3. **Everything reads/writes `mios.toml`.** The configurator view, the Portal's
   own settings, and every deployment type's config all flow through the same
   `mios.toml` SSOT (Law 8), referenced by key never by hardcoded literal (Law 7).
   There is no second config store and no config path that bypasses the SSOT.

4. **The Portal is the shareable-LINK front door.** The shareable web link (the
   `agent_pipe` port, or its hosted/MagicDNS equivalent via
   `[portal].public_host`) **is** the MiOS Portal, and it bootstraps the whole
   pipeline: **open → configure → deploy**. The USB `MiOS-Repo` shadow-config
   partition (ADR-0008) is the **offline embodiment** of this same surface. The
   acceptance bar for the whole effort is therefore exactly: **a shareable link +
   a USB disk + a usable computer** → everything else self-contained.

## Rationale

- **Law 5 (UNIFIED-AI-REDIRECTS) already gave us one API door; this extends the
  same discipline to the web/config door.** One `agent_pipe` port for `/v1` *and*
  `/` is the natural closure — the Portal was already served there, the
  configurator just hadn't been folded in.
- **Law 8 (SSOT-PROJECTION) makes "one config surface" honest.** If the
  configurator, the Portal settings, and every deployment config all project from
  `mios.toml`, then a single UI over `mios.toml` genuinely *is* the whole config
  surface — there is nothing to configure that lives elsewhere.
- **Law 7 (NO-HARDCODE).** The surface is addressed through `[portal]` /
  `ports.agent_pipe`, never a hardcoded port or URL; `public_host` is the one knob
  for the externally-reachable name.
- **"The Portal needs config too" resolves cleanly.** A configurator that is a
  *view of the Portal* means the Portal configures itself through itself — no
  chicken-and-egg second surface.
- **Shareable-link sovereignty.** A single link that opens onto configure-and-
  deploy, with a USB as its offline twin, is the minimal, self-contained
  distribution unit — the sovereignty story end-to-end.

## Alternatives considered

- **Keep `mios.html` a separate standalone configurator.** Rejected — two web
  surfaces, two things to serve/secure, and an ambiguous "where do I configure
  MiOS?" It also duplicates auth/session with the Portal.
- **Put the config UI on its own port.** Rejected — it re-fragments the front door
  that ADR-0006 deliberately collapsed to one; a second port is a second thing to
  publish, proxy, and secure.
- **Configure the Portal from a separate config file (not `mios.toml`).** Rejected
  — a second config store violates Law 8; the Portal must project from the one
  SSOT like everything else.

## Consequences

Positive:
- One place to configure MiOS; one port to serve, secure, and document (web + API).
- The shareable link and the USB are the same surface online and offline.
- Converges with ADR-0006 (one `/v1` door) and is governed by ADR-0007 (the config
  UI writes `mios.toml`; the laws/conventions it must honor render into the MiOS
  Spec).

DONE vs PLANNED (honest):
- **DONE:** the Portal is served by agent-pipe at `GET /` on the `agent_pipe`
  port (`mios_portal.py` + `server.py`); the OpenAI `/v1` API is served on that
  same port (ADR-0006); `[portal]` config (login, `public_host`, session TTL) is
  in the SSOT; a native Android Portal client exists (`tools/mios-portal-app/`).
- **PLANNED (WS-CONFIG / task_3235):** fold `mios.html`
  (`usr/share/mios/configurator/`) into the Portal as a configurator view so the
  standalone page is retired; wire read/write of `mios.toml` from that view through
  `mios_portal.py`; confirm every deployment type's config flows through it. Until
  that lands, the *decision* is accepted and the port is unified, but the
  configurator is still a separate page.

## Implementation

- `usr/lib/mios/agent-pipe/server.py` — the single `agent_pipe` front door:
  `GET /` (Portal) + `/v1/*` (OpenAI API, ADR-0006).
- `usr/lib/mios/agent-pipe/mios_portal.py` — Portal backend; gains the configurator
  view and the `mios.toml` read/write path.
- `usr/share/mios/portal/` — Portal assets; absorbs the configurator UI.
- `usr/share/mios/configurator/mios.html` — folded into the Portal (retired as a
  standalone page under WS-CONFIG).
- `usr/share/mios/mios.toml [portal]` (L220–232) — the surface's config
  (`public_host`, `require_login`, `user`/`password` inheritance, `session_ttl`);
  `ports.agent_pipe` is the port key.
- `tools/mios-portal-app/` — the Android client points at the same `agent_pipe`
  port.
- Governed by ADR-0006 (front door) and ADR-0007 (the config UI writes `mios.toml`;
  laws are the fitness functions the surface must not violate).

## References

- ADR-0006 (OpenAI-API-only AI contract) — the `agent_pipe` `/v1` front door this
  ADR extends to the web/config surface: `0006-openai-api-only-ai-contract.md`.
- ADR-0007 (Governance model) — laws as fitness functions; the config UI writes the
  SSOT that the MiOS Spec renders: `0007-governance-model-laws-adrs-spec.md`.
- ADR-0008 (MiOS-Cat unified entry point) — the USB `MiOS-Repo` shadow-config
  partition is the offline embodiment of this shareable-link surface:
  `0008-mios-cat-unified-entry-and-minification.md`.
- SSOT: `usr/share/mios/mios.toml [portal]` (L220), `ports.agent_pipe`.
- Surface: `usr/lib/mios/agent-pipe/{server.py,mios_portal.py}`,
  `usr/share/mios/portal/`, `usr/share/mios/configurator/mios.html`,
  `tools/mios-portal-app/`.
- OpenAI `/v1` convergence: the same OpenAI-compatible front door
  (<https://platform.openai.com/docs/api-reference>) serves the API while `GET /`
  serves the Portal — one door.
- MiOS Laws 5/7/8: `usr/share/mios/mios.toml [laws]`, enforced by
  `automation/98-drift-checks.sh` + `automation/99-postcheck.sh`.
