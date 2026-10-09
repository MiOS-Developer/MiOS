<!-- AI-hint: Architectural Laws and root exception table for MiOS, derived directly from mios.toml [laws] and [security.privileged_quadlets]. -->

# MiOS Architectural Laws

This document is derived directly from `usr/share/mios/mios.toml`.

## Architectural Laws

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

## Root Exception Table (Law 6)

<!-- MIOS-GEN:root-exceptions -->
| Quadlet | Runs as root because |
|---|---|
| `mios-ceph.container` | see `[security.privileged_quadlets]` |
| `mios-radosgw.container` | see `[security.privileged_quadlets]` |
| `mios-k3s.container` | see `[security.privileged_quadlets]` |
| `mios-forge.container` | see `[security.privileged_quadlets]` |
| `mios-forgejo-runner.container` | see `[security.privileged_quadlets]` |
| `mios-pxe-hub.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-crawl4ai.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-firecrawl-api.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-firecrawl-worker.container` | see `[security.privileged_quadlets]` |
| `mios-webtools-redis.container` | see `[security.privileged_quadlets]` |
| `mios-llm-heavy.container` | see `[security.privileged_quadlets]` |
| `mios-coderun-sandbox@.container` | see `[security.privileged_quadlets]` |

<!-- derived from usr/share/mios/mios.toml [security.privileged_quadlets].root -->
<!-- /MIOS-GEN:root-exceptions -->
