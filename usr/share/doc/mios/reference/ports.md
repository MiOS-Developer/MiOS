<!-- AI-hint: Machine-generated reference documentation for MiOS host and container port allocations, derived directly from mios.toml [ports] and [ports.categories]. -->

# MiOS Port Allocations

This document is derived directly from `usr/share/mios/mios.toml`.

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
