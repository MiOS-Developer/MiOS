<!-- AI-hint: Reference page for the port allocation table and the numbered Architectural Laws. The tables between MIOS-GEN markers are DERIVED from mios.toml by `mios-manual render`; the prose around them is authored and is never rewritten.
     AI-related: usr/share/mios/mios.toml, usr/libexec/mios/mios-manual, tools/render-ports.py, automation/98-drift-checks.sh -->
# Ports and Laws

This page has two kinds of content, and the difference matters if you edit it.

Everything **between** a `MIOS-GEN` marker pair is derived from `mios.toml` and is
rewritten in full by `mios-manual render`. Do not hand-edit inside a pair; change the
SSOT and re-render. Everything **outside** the pairs is authored prose, and `render`
has no code path that can write there.

## Port allocation

Ports are not hand-assigned. Each category in `[ports.categories]` declares a `base` and a
`stride`, and a member's port is derived from its position in the ordered member list.
Adding a service allocates the next port in its band; moving a category's `base` moves the
whole band. A `pinned` entry opts a protocol-fixed port out of that arithmetic -- DNS on 53
is the obvious case, since it cannot float.

<!-- MIOS-GEN:ports -->
| Category | Service | Port |
|---|---|---|
| admin | ssh | 8100 |
| admin | cockpit | 8110 |
| admin | cockpit_link | 8120 |
| agent | agent_pipe | 8700 |
| agent | prefilter | 8710 |
| agent | hermes | 8720 |
| agent | daemon_agent | 8740 |
| agent | model_router | 8750 |
| agent | arbiter | 8760 |
| agent | mcp | 8770 |
| agent | opencode_gateway | 8780 |
| audio | whisper | 8178 |
| audio | piper | 8179 |
| bridge | oscontrol | 8950 |
| cluster | k3s_api | 8450 |
| cluster | ceph_dashboard | 8460 |
| cluster | radosgw | 8470 |
| data | pgvector | 8600 |
| data | pgvector_internal (pinned) | 5432 |
| desktop | rdp | 8300 |
| desktop | ttyd_bash | 8310 |
| desktop | ttyd_powershell | 8320 |
| devtools | code_server | 8900 |
| edge | adguard_ui | 8050 |
| edge | adguard_dns (pinned) | 53 |
| edge | headscale (pinned) | 8085 |
| forge | forge_http | 8400 |
| forge | forge_ssh | 8410 |
| inference | llm_light | 8500 |
| inference | cpu_node | 8510 |
| inference | llm_heavy | 8520 |
| inference | llm_igpu | 8540 |
| inference | rpc_igpu | 8550 |
| node | ai_legacy | 8640 |
| node | field_live_chat | 8642 |
| node | node | 8650 |
| sidecar | guacd | 8560 |
| sidecar | redis | 8565 |
| sidecar | otelcol_otlp | 8575 |
| sidecar | otelcol_ui | 8580 |
| sidecar | pxe_hub_api | 8585 |
| sidecar | chrome_cdp (pinned) | 9222 |
| sidecar | chrome_cdp_worker (pinned) | 9223 |
| webtools | searxng | 8800 |
| webtools | crawl4ai | 8810 |
| webtools | firecrawl | 8820 |
| webtools | searxng_internal (pinned) | 8080 |
| webui | open_webui | 8200 |
| webui | hermes_dashboard | 8210 |
| webui | guacamole_web | 8220 |

<!-- derived from usr/share/mios/mios.toml [ports.categories] -->
<!-- /MIOS-GEN:ports -->

## Architectural Laws

The laws are invariants, not guidelines: each one names the check that enforces it, and a
failing law fails the build. The `id` column is the numbering SSOT -- `mios.toml [laws]` --
so a law's number is stable even as the registry grows.

<!-- MIOS-GEN:laws -->
| # | Law | Applies to | Enforced by |
|---|---|---|---|
| 1 | USR-OVER-ETC | both | `98-drift-checks.sh:check_usr_over_etc` |
| 2 | NO-MKDIR-IN-VAR | both | `98-drift-checks.sh:check_no_mkdir_in_var` |
| 3 | BOUND-IMAGES | bootc | `99-postcheck.sh:BOUND-IMAGES` |
| 4 | BOOTC-CONTAINER-LINT | bootc | `98-drift-checks.sh:check_lint_is_final` |
| 5 | UNIFIED-AI-REDIRECTS | both | `99-postcheck.sh:UNIFIED-AI-REDIRECTS` |
| 6 | UNPRIVILEGED-QUADLETS | bootc | `98-drift-checks.sh:check_quadlet_privilege` |
| 7 | NO-HARDCODE | both | `98-drift-checks.sh:check_no_hardcode` |
| 8 | SSOT-PROJECTION | both | `98-drift-checks.sh:check_projection_registry` |
| 9 | ONE-CANONICAL-NAME | both | `98-drift-checks.sh:check_var_closure` |
| 10 | BARE-SAFE-ENV | both | `99-postcheck.sh:BARE-SAFE-ENV` |
| 11 | SECRETS-NEVER-IN-ENV | bootc | `99-postcheck.sh:SECRETS-NEVER-IN-ENV` |
| 12 | BAKE-NOT-FETCH | both | `98-drift-checks.sh:check_dag_integrity,check_firstboot_degrade_open` |
| 13 | NATIVE-DROPINS | both | `98-drift-checks.sh:check_resolver_twin_parity` |
| 14 | TARGET-LANGUAGES | both | `98-drift-checks.sh:check_target_languages` |
| 15 | DOUBLE-REPO-TRIPLE-CHECK | both | `process:CLAUDE.md/AGENTS.md (both repos); parity via 98-drift-checks.sh checks 22+27` |
| 16 | ONE-TEMPLATE-PER-TYPE | both | `98-drift-checks.sh:check_template_conformance` |

<!-- derived from usr/share/mios/mios.toml [laws].laws -->
<!-- /MIOS-GEN:laws -->
