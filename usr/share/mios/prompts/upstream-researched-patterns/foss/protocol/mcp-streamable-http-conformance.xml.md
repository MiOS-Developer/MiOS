<!-- AI-hint: Primary-source prompt for MCP transport conformance: whether the
     declared protocol revision, the stateless Streamable HTTP relay, the
     legacy SSE client path and the MCP-to-strict-function schema boundary
     match the published specification and the FOSS Python SDK 2.x.
     AI-related: usr/share/mios/mios.toml [mcp], usr/libexec/mios/mios-mcp-server, usr/lib/mios/agent-pipe/mios_mcp_transport.py, usr/lib/mios/agent-pipe/mios_mcp_schema.py, TASKS.jsonl task_3416, .prompts/README.md -->
<context>
MiOS exposes its verb catalog over the Model Context Protocol and consumes
other MCP servers from agent-pipe. The SSOT key `[mcp].protocol_version`
declares one specification revision; the server relays through the FOSS
Python SDK 2.x as a stateless Streamable HTTP endpoint that answers with JSON
rather than an event stream; the client keeps a legacy HTTP+SSE path; and a
converter rewrites every MCP tool `inputSchema` into a strict OpenAI-format
function schema. Task task_3416 (open) asks that each endpoint be inventoried
against the current Streamable HTTP contract, including session metadata,
cancellation and response behavior, and that legacy SSE survive only where a
caller needs it. A research record in the tree still cites an older revision
and line numbers that no longer match. Whether the declared revision, the SDK
release and the SDK options MiOS relies on exist as written are upstream
facts; MiOS must not change its transport or drop a legacy path until they
are settled.
</context>

<role>You are MiOS-McpTransport-Researcher. Verify; do not speculate.</role>

<task>
Answer every question in `<questions>` from the protocol specification
repository, its changelog, and the FOSS Python SDK's source and release
metadata at named tags. Then decide whether MiOS's server, client and schema
boundary conform, and name the exact SSOT keys, files and lines that change.
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- MiOS `usr/share/mios/mios.toml` at `af6de6a`: lines 3580-3581 (`[mcp]`
  `protocol_version = "2026-07-28"`).
- MiOS `usr/libexec/mios/mios-mcp-server` at `af6de6a`: lines 12-19 (claims the
  SDK v2 "serves 2026-07-28 and legacy peers on the same endpoint" and lists
  `server/discover` for modern discovery, `initialize` for legacy peers),
  526-551 (`streamable_http_app(streamable_http_path="/mcp",
  json_response=True, stateless_http=True, host=..., custom_starlette_routes=...)`),
  554-566 (refuses to start below SDK major 2).
- MiOS `usr/lib/mios/agent-pipe/mios_mcp_transport.py` at `af6de6a`: lines 1-7
  (stateless and legacy-handshake peers negotiated by `mcp.Client`), 16-20
  (imports `httpx2`, `mcp.client.sse.sse_client`,
  `mcp.client.streamable_http.streamable_http_client`), 26-30 (revision
  default).
- MiOS `usr/lib/mios/agent-pipe/requirements.txt` at `af6de6a`: lines 10 and 16
  (`httpx2>=2.5.0`, `mcp>=2.1.1,<3`).
- MiOS `usr/lib/mios/agent-pipe/mios_mcp_schema.py` at `af6de6a`: lines 17-24
  (strict conversion: root object, every property required, optional
  properties widened with `null`, `additionalProperties: false`).
- MiOS `TASKS.jsonl` at `af6de6a`: line 3415 (task_3416, open).
- MiOS `docs/research/architecture-gap-audit-2026-09-29.md` at `af6de6a`: line
  10 (cites revision `2025-11-25` at `mios.toml:3516` and server lines 495 and
  554-567 publishing a session header; stale against the lines above).
</provenance>

<inputs>
<specification>{{mcp_specification_repository}}</specification>
<declared_revision>2026-07-28</declared_revision>
<sdk>{{foss_python_mcp_sdk}}</sdk>
<sdk_floor_in_tree>2.1.1</sdk_floor_in_tree>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{run_date_iso8601}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-1  Revision status: is `2026-07-28` a published, final revision of
       the MCP specification? Name the revision before it, and list the
       changelog entries of `2026-07-28` that touch transports,
       initialization, version negotiation, sessions, and per-request
       metadata.
- Q-2  Streamable HTTP server contract under that revision: the HTTP
       methods the endpoint must accept or reject, the required `Accept`
       handling, when the server may answer with a JSON body instead of
       an event stream, the name and required behavior of the protocol-
       version request header (missing, unsupported), and whether a
       session identifier header still exists, with its name and
       lifecycle.
- Q-3  Discovery and handshake: does the revision define a discovery
       method, and is its exact name `server/discover`? How must a server
       that also accepts `initialize` from older peers negotiate when
       client and server prefer different revisions?
- Q-4  Cancellation: the notification a client sends to cancel an in-
       flight request, its parameters, and what a stateless server must
       do on that notification or on client disconnect, including the
       fate of a response already in flight and of a tool call the server
       has started.
- Q-5  Legacy transport: which revision deprecated or removed the two-
       endpoint HTTP+SSE transport, and the specification's backward-
       compatibility guidance for servers and for clients that must still
       reach such peers.
- Q-6  SDK: the current stable 2.x release of the FOSS Python SDK (tag,
       date, commit), the first 2.x release implementing `2026-07-28`,
       and, at that release, whether `streamable_http_app` accepts
       `stateless_http`, `json_response`, `streamable_http_path`, `host`
       and `custom_starlette_routes`; whether `Server` accepts
       `on_list_tools`, `on_call_tool`, `on_list_resources` and
       `on_read_resource`; whether `ReadResourceResult` has `ttlMs` and
       `cacheScope`; and whether `mcp.client.sse.sse_client` and
       `mcp.client.streamable_http.streamable_http_client` exist.
- Q-7  HTTP client dependency: which HTTP client package the SDK 2.x
       declares, and whether a package named `httpx2` exists on the
       Python package index (publisher, source repository, current
       version, license) and is that dependency or a different project.
- Q-8  Schema boundary: the JSON Schema dialect the revision specifies
       for tool `inputSchema` and `outputSchema`, and the documented
       constraints of OpenAI-format strict function schemas. List the
       MCP-permitted keywords that strict mode rejects and how each must
       be rewritten or refused.
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
  method or header names, digests, checksums, release dates, issue or PR
  numbers, or advisory ids. Copy them from the source or mark the question
  `UNVERIFIED`.
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
`<questions>` block appears exactly once in `findings`. Every provenance
line appears once in `provenance_check`. If no repository change is
justified, `mios_changes` is an empty array.

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "mios_mcp_streamable_http_conformance",
      "strict": true,
      "schema": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "run_date",
          "mios_revision",
          "findings",
          "provenance_check",
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
                    "Q-7",
                    "Q-8"
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
          "provenance_check": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": [
                "path",
                "line_at_revision",
                "still_says",
                "note"
              ],
              "properties": {
                "path": {
                  "type": "string",
                  "description": "Repository-relative path from the provenance block."
                },
                "line_at_revision": {
                  "type": "string",
                  "description": "The line or range the provenance block names."
                },
                "still_says": {
                  "type": "boolean",
                  "description": "false when the line has moved or changed since the named revision."
                },
                "note": {
                  "type": "string"
                }
              }
            }
          },
          "decision": {
            "type": "string",
            "enum": [
              "CONFORMANT",
              "ADJUST_SERVER",
              "ADJUST_CLIENT",
              "ADJUST_SERVER_AND_CLIENT",
              "WATCH"
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
