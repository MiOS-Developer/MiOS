<!-- AI-hint: Primary-source prompt for settling which mesh-VPN client features work in
     userspace-networking mode (inbound, serve, funnel, relay through an egress
     proxy, several instances per host, identity-provider role claims).
     AI-related: usr/share/mios/mios.toml, TASKS.md task_930, .prompts/README.md -->
<context>
MiOS joins hosts that cannot create a TUN device (hosted dev sessions,
unprivileged containers, the Windows-side dev VM) to its mesh VPN with the
FOSS client daemon `tailscaled --tun=userspace-networking`, run as an
unprivileged per-instance unit with its own state directory and socket. The
hub publishes a handful of loopback services to the mesh over HTTPS, one
public webhook path, and per-node role claims minted by the mesh identity
provider (`tsidp`). The design that depends on this was written before the
behaviours below were read in upstream source; several are marked unverified
or unmeasured in it. This prompt asks what upstream actually documents and
implements, so the live probe only has to confirm, not discover.
</context>

<role>You are MiOS-Mesh-Client-Researcher. Verify; do not speculate.</role>

<task>
Answer every question in `<questions>` for the current stable release of the
mesh client and of `tsidp`, from their source and documentation. For each,
state the exact flags, environment variables, policy keys or API routes
involved, the first release that supports the behaviour, and whether the
behaviour differs between TUN mode and userspace-networking mode.
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- -dev-loop `docs/research/monitor-relay-spike-2026-09.md` at `26aae54`:
  lines 166-176 (section 3.1: userspace mode, Funnel, inbound all marked
  UNVERIFIED), 180 (client 1.102.4 measured only to login-URL issuance; its
  own relay client through an egress proxy UNVERIFIED), 214-215 and 242
  (role claims through policy app-capability grants UNVERIFIED), 239 (several
  userspace daemons on one host INFERRED), 385-397 (section 9.1 unit shape),
  590 (probe rows a, b, c, d, f, h), 755-770 (honest gaps).
- MiOS `TASKS.md` at `a67750d`: lines 10732-10740 (task_930 mesh VPN, Done-When).
- MiOS `usr/share/mios/mios.toml` at `a67750d`: lines 828-830 and 1922-1923,
  2473 (units and portal settings that already assume the client's serve
  feature and interface address).
Probe row g of that spike concerns a hosted forge endpoint and is out of
scope here.
</provenance>

<inputs>
<client>{{mesh_client_name_default_tailscale}}</client>
<client_version_measured>{{client_version_or_unknown}}</client_version_measured>
<identity_provider>{{idp_name_default_tsidp}}</identity_provider>
<coordinator>{{hosted_or_self_hosted_coordinator}}</coordinator>
<egress_proxy>{{http_connect_proxy_with_tls_reterminating_ca_or_none}}</egress_proxy>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{date}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-a  Inbound: in userspace-networking mode, can a peer open a TCP
       connection to a listener on the node's loopback, and what must the
       node configure for it (serve rules, SOCKS5/HTTP proxy listeners,
       netstack forwarding)? Name the source file that implements it.
- Q-b  Serve: does the client's `serve` HTTPS path mapping to
       `127.0.0.1:<port>` work in userspace mode, including certificate
       provisioning for the node name? List any documented limitation.
- Q-c  Role claims: can `tsidp` issue a non-interactive, audience-bound token
       to a tagged node, with a custom claim added by a policy
       app-capability grant keyed on the caller's tag? Give the exact grant
       syntax, the claim's location in the token, and the release that
       introduced it. State whether a node with no matching grant receives a
       token without the claim or is refused.
- Q-d  Relay through an egress proxy: does the daemon's own relay (DERP)
       client honour an HTTP CONNECT proxy from the environment, and does it
       accept a proxy that re-terminates TLS with a private CA? Name the
       variables and trust-store paths it reads.
- Q-f  Public ingress: does the client's public-ingress feature (Funnel)
       work in userspace mode? State its documented prerequisites and port
       restrictions for the version you cite.
- Q-h  Several daemons per host: can two or more userspace daemons run on one
       host with distinct `--statedir`, `--socket` and listen ports, each
       registering as its own node with its own tags? Name every flag or
       variable that must differ to avoid a collision.
- Q-v  Versions: the current stable client and `tsidp` releases (tag, date,
       commit), and the minimum client version that satisfies Q-a through
       Q-h together.
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
      "name": "mios_mesh_userspace_node_capabilities",
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
                    "Q-a",
                    "Q-b",
                    "Q-c",
                    "Q-d",
                    "Q-f",
                    "Q-h",
                    "Q-v"
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
