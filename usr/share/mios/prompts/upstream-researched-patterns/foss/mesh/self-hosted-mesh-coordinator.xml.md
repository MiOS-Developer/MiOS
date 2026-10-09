<!-- AI-hint: Primary-source prompt for the self-hosted mesh-VPN coordinator that task_930
     requires on a Blade: features, client compatibility, packaging, and
     behaviour when the coordinator is down.
     AI-related: TASKS.md task_930, usr/share/mios/mios.toml [metal.mesh], .prompts/README.md -->
<context>
MiOS Blades own the hardware and form one HCI mesh VPN. Every Blade is a
member; the coordinator is a singleton, off the data path, and must degrade
open (Law 12): losing it may block new enrolments but must leave established
tunnels up. The planned coordinator is the FOSS control server `headscale`,
and the client is the FOSS `tailscaled`. Today the SSOT names the target and
nothing implements it; the client arrives from a third-party package
repository fetched at build time, which the task register calls a Law 12
problem. Before the coordinator can ship as a gated unit, MiOS needs the
upstream facts below.
</context>

<role>You are MiOS-Mesh-Coordinator-Researcher. Verify; do not speculate.</role>

<task>
Answer every question in `<questions>` for the current stable coordinator
release and the client versions it supports, from upstream source,
documentation, release metadata and package repositories. Where a feature
is absent, say what upstream documents instead (a workaround, a tracking
issue, or nothing).
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- MiOS `TASKS.md` at `a67750d`: lines 10732-10740 (task_930: coordinator as a
  real gated unit, client from a package, established tunnel survives the
  coordinator stopping), 3910 and 3955 (mesh has no transport; coordinator
  is a name only), 4020-4024 (client fetched from a repo drop at build: Law
  12 circularity).
- MiOS `ROADMAP.md` at `a67750d`: line 1187 (singleton coordinator that
  degrades open).
- -dev-loop `docs/research/monitor-relay-spike-2026-09.md` at `26aae54`:
  lines 44 and 170 (the coordinator's feature list at `v0.29.4` leaves Serve
  and Funnel unchecked), 737-742 (operator question QA: what replaces serve,
  public ingress and the identity provider at the swap; the client-side
  `whois` under the self-hosted coordinator is INFERRED, unmeasured), 385-397
  (vendor unit disabled by preset; no package section installs the client).
</provenance>

<inputs>
<coordinator>{{coordinator_name_default_headscale}}</coordinator>
<coordinator_version_read>{{version_or_unknown}}</coordinator_version_read>
<client>{{client_name_default_tailscale}}</client>
<base_os>{{fedora_release_used_by_the_mios_base_image}}</base_os>
<database>{{sqlite_or_postgresql}}</database>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{date}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-1  Release and features: the current stable coordinator release (tag,
       date, commit) and, from its own feature list and source, the status
       of: pre-auth keys, ephemeral nodes, tags, ACL policy, grants and
       app-capabilities, OIDC login, embedded relay (DERP) server, MagicDNS,
       node HTTPS certificates, Serve, and public ingress (Funnel).
- Q-2  Client compatibility: the client version range the coordinator
       release supports, and where upstream states it.
- Q-3  Coordinator outage: what upstream documents about established
       tunnels, relay maps, key expiry and policy changes while the
       coordinator is unreachable. Separate documented behaviour from
       behaviour only a live test can show.
- Q-4  Packaging: which official artifacts exist (RPM, OCI image, static
       binary), their signatures or checksums, and whether the coordinator
       and the client are packaged in the base OS's official repositories
       (name the package, repository and release). This decides whether both
       can be baked into the image from a package rather than fetched.
- Q-5  Identity: does the client's LocalAPI `whois` work against this
       coordinator, and does the mesh identity provider (`tsidp`) support a
       self-hosted coordinator? Cite source, not assumption.
- Q-6  Serve and public-ingress replacement: what upstream documents for
       node TLS certificates and for exposing a node service publicly when
       the coordinator lacks those features.
- Q-7  Unprivileged unit: can the coordinator run as a non-root user in a
       container (listen ports, state directory, capabilities), and which
       database backends the release supports or deprecates.
</questions>

<rules>
- PRIMARY SOURCES ONLY: the upstream project's own source repository at a
  named tag or commit, its own documentation, its release notes and tag
  metadata, the registry or package repository that publishes the artifact
  (tag and digest APIs, repository metadata), standards bodies' texts, and
  advisory databases. A blog post, forum answer, news article, mirror, AI
  summary, or another model's report is NOT a source.
- EXACT VERSIONS: every claim names the version, tag, commit, or digest it
  was read at. "Latest" is not a version; resolve it to a tag and a digest.
- EVERY CLAIM CARRIES a URL, a locator (file:line, section anchor, or API
  field path), a badge (`VERIFIED`, `PARTIALLY_VERIFIED`, `UNVERIFIED`,
  `CONTRADICTED`) and a confidence (`high`, `medium`, `low`). A claim with
  no primary source is `UNVERIFIED` with confidence `low`; say so rather than
  fill the gap.
- NEVER INVENT versions, flags, environment variables, configuration keys,
  policy syntax, digests, checksums, release dates, issue or PR numbers, or
  CVE ids. Copy them from the source or mark the question `UNVERIFIED`.
- SEPARATE DOCUMENTED FROM MEASURED: when only a live probe on a real host
  can settle a question, answer what the documents say, set
  `requires_live_measurement` to true, and describe the probe in `unknowns`.
- KEEP MiOS FACTS APART from upstream facts. The provenance block names the
  MiOS lines that raised each question; re-read them at the named revision
  before relying on them, and report any line that has moved.
- MiOS LAW 5: recommendations route AI traffic through `MIOS_AI_ENDPOINT`
  and add no vendor-cloud AI endpoint or vendor agent/product name to a
  shipped MiOS file. Laws 2, 6, 7 and 12 also bind every `mios_changes`
  entry: tmpfiles-declared `/var` paths, unprivileged Quadlets, no literal
  that belongs in `mios.toml`, and bake-not-fetch with degrade-open boot.
- READ ONLY: read published sources, but do not execute commands,
  authenticate to or change any service, download an artifact for use, or
  modify files. Produce a research report only. Never echo a credential,
  auth key, or token; write `[REDACTED]` instead.
</rules>

<output_contract>
Reply with ONE JSON object and nothing else. It must validate against
the strict schema below, sent to the model as the OpenAI-format
`response_format` request parameter. Every `question_id` in the
`<questions>` block appears exactly once in `findings`. If no repository
change is justified, `mios_changes` is an empty array.

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "mios_mesh_self_hosted_coordinator",
      "strict": true,
      "schema": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "run_date",
          "mios_revision",
          "findings",
          "decision",
          "decision_rationale",
          "mios_changes",
          "unknowns"
        ],
        "properties": {
          "run_date": {
            "type": "string",
            "description": "ISO 8601 date the research ran."
          },
          "mios_revision": {
            "type": "string",
            "description": "Short SHA of the MiOS revision the provenance block names."
          },
          "findings": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": [
                "question_id",
                "answer",
                "badge",
                "confidence",
                "requires_live_measurement",
                "evidence"
              ],
              "properties": {
                "question_id": {
                  "type": "string",
                  "enum": [
                    "Q-1",
                    "Q-2",
                    "Q-3",
                    "Q-4",
                    "Q-5",
                    "Q-6",
                    "Q-7"
                  ]
                },
                "answer": {
                  "type": "string"
                },
                "badge": {
                  "type": "string",
                  "enum": [
                    "VERIFIED",
                    "PARTIALLY_VERIFIED",
                    "UNVERIFIED",
                    "CONTRADICTED"
                  ]
                },
                "confidence": {
                  "type": "string",
                  "enum": [
                    "high",
                    "medium",
                    "low"
                  ]
                },
                "requires_live_measurement": {
                  "type": "boolean",
                  "description": "true when documents cannot settle the question and a probe on a real host is still needed."
                },
                "evidence": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "additionalProperties": false,
                    "required": [
                      "claim",
                      "url",
                      "source_kind",
                      "project",
                      "project_version",
                      "revision",
                      "locator",
                      "quote",
                      "badge",
                      "confidence"
                    ],
                    "properties": {
                      "claim": {
                        "type": "string",
                        "description": "One atomic factual claim this source supports or contradicts."
                      },
                      "url": {
                        "type": "string",
                        "description": "Primary-source URL, pinned to a tag or commit where the host allows it."
                      },
                      "source_kind": {
                        "type": "string",
                        "enum": [
                          "upstream_source",
                          "upstream_docs",
                          "release_notes",
                          "release_tag",
                          "registry_api",
                          "package_repository",
                          "specification",
                          "advisory",
                          "license_text"
                        ]
                      },
                      "project": {
                        "type": "string"
                      },
                      "project_version": {
                        "type": "string",
                        "description": "Exact version or tag the source describes; 'unversioned' only for living docs."
                      },
                      "revision": {
                        "type": [
                          "string",
                          "null"
                        ],
                        "description": "Commit SHA or image digest the locator was read at; null only when the host exposes none."
                      },
                      "locator": {
                        "type": "string",
                        "description": "file:line, section anchor, or API field path inside the source."
                      },
                      "quote": {
                        "type": "string",
                        "description": "Short verbatim excerpt (at most 300 characters). Never a secret or token."
                      },
                      "badge": {
                        "type": "string",
                        "enum": [
                          "VERIFIED",
                          "PARTIALLY_VERIFIED",
                          "UNVERIFIED",
                          "CONTRADICTED"
                        ]
                      },
                      "confidence": {
                        "type": "string",
                        "enum": [
                          "high",
                          "medium",
                          "low"
                        ]
                      }
                    }
                  }
                }
              }
            }
          },
          "decision": {
            "type": "string",
            "enum": [
              "ADOPT",
              "ADAPT",
              "WATCH",
              "REJECT"
            ]
          },
          "decision_rationale": {
            "type": "string"
          },
          "mios_changes": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": [
                "path",
                "ssot_key_or_line",
                "change",
                "justified_by"
              ],
              "properties": {
                "path": {
                  "type": "string",
                  "description": "Repository-relative MiOS path."
                },
                "ssot_key_or_line": {
                  "type": "string"
                },
                "change": {
                  "type": "string"
                },
                "justified_by": {
                  "type": "array",
                  "items": {
                    "type": "string"
                  },
                  "description": "Question ids whose VERIFIED findings justify the change."
                }
              }
            }
          },
          "unknowns": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        }
      }
    }
  }
}
```
</output_contract>
