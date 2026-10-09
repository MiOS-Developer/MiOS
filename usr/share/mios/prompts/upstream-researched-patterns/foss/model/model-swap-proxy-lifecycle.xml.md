<!-- AI-hint: Primary-source prompt for the model-swap proxy behind mios-llm-light:
     queueing and 429 behavior during swaps, child stop and failure handling,
     residency rules, health endpoints, and whether saved inference slots are
     durable across restarts and swaps.
     AI-related: usr/share/mios/llamacpp/mios-llm-light.yaml, usr/share/mios/mios.toml [image.sidecars] [ports], usr/share/containers/systemd/mios-llm-light.container, TASKS.jsonl task_3415, .prompts/README.md -->
<context>
The primary inference lane `mios-llm-light` (port key `llm_light`) runs the
FOSS llama-swap proxy, which starts one llama.cpp `llama-server` child per
requested model. The tree records behavior it attributes to the proxy: a 429
when a request needs a model swapped out while it is busy, a 429 when one
child's slots are exhausted, residency managed by the proxy's "own LRU", and
per-conversation KV paging to disk through `--slot-save-path`. Task task_3415
(open) asks MiOS to capture queueing, health, TTL unload and failure recovery
and to remove any fixed kill interval or durable-KV-cache claim the upstream
configuration does not support. The image floats a moving tag, so the
behavior can change under MiOS without a tree change. These are upstream
facts; MiOS must not change its model map or scheduler policy until they are
settled.
</context>

<role>You are MiOS-ModelSwapProxy-Researcher. Verify; do not speculate.</role>

<task>
Answer every question in `<questions>` from the proxy's source, README,
configuration reference and releases, and from the inference server's source
and documentation, at named tags. Then decide whether the MiOS model map and
its documentation match upstream, and name the exact files and keys that
change.
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- MiOS `usr/share/mios/llamacpp/mios-llm-light.yaml` at `af6de6a`: lines 24-35
  and 100-108 (`ttl: 300`, `--parallel 1`, `--cache-reuse 256`,
  `--slot-save-path /var/lib/mios/llamacpp/slots`), 40-52 (`ttl: -1`),
  122-133 (429 from slot exhaustion; `--parallel 4` for embeddings), 160-180
  (429 when a busy model must be swapped out; `groups.resident` with
  `swap: false`, `exclusive: true`); lines 11-12 and 22 claim 128k context
  and `--np 4` while the commands above pass `--ctx-size 32768` and
  `--parallel 1` (a MiOS inconsistency to report, not to research).
- MiOS `usr/share/mios/mios.toml` at `af6de6a`: line 3141 (residency left to
  "llama-swap's own LRU"), line 7277 (`llm_light` image floats the moving
  `cuda` tag).
- MiOS `usr/share/containers/systemd/mios-llm-light.container` at `af6de6a`:
  lines 18-26 (proxy `Exec`, health check on `/v1/models`).
- MiOS `usr/share/doc/mios/concepts/architecture.md` at `af6de6a`: line 164
  ("per-conversation KV-paging to disk").
- MiOS `TASKS.jsonl` at `af6de6a`: line 3414 (task_3415, open).
</provenance>

<inputs>
<proxy>{{model_swap_proxy_default_llama_swap}}</proxy>
<proxy_image_tag_in_tree>cuda</proxy_image_tag_in_tree>
<inference_server>{{inference_server_default_llama_cpp}}</inference_server>
<lane_port_key>llm_light</lane_port_key>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{run_date_iso8601}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-1  Release and image: the current stable proxy release (tag, date,
       commit), the published image tag scheme, which release and
       inference-server build the moving `cuda` tag resolves to today
       (digest from the registry API), and how often that tag moves.
- Q-2  Configuration schema at that release: confirm or contradict each
       key the MiOS map uses (`models`, `cmd`, `proxy`, `ttl`, `aliases`,
       `groups` with `swap`, `exclusive` and `members`, the `${PORT}`
       macro) and state the meaning of `ttl` values 0, -1 and absent.
       List the documented keys that govern health-check timeout, stop
       command, and per-model concurrency, with defaults.
- Q-3  Swap and queueing: when a request arrives for a model not loaded
       while another model is serving, does the proxy queue, wait,
       reject, or run both? Under which documented conditions does it
       answer 429, and is any wait or queue limit configurable?
- Q-4  Stop and failure: the signal sequence used to stop a child
       (signal, grace period, forced kill) and whether the grace period
       is configurable; what happens when a child never becomes healthy,
       exits unexpectedly, or is killed for memory exhaustion; whether it
       is restarted; and what the client receives.
- Q-5  Residency: does the proxy implement any least-recently-used or
       memory-aware eviction, or only `ttl` expiry and group swap rules?
- Q-6  Endpoints: the documented management and health endpoints at that
       release (for example a running-models list, an unload action,
       upstream pass-through, a health path), and which one reports the
       proxy's own readiness without loading a model.
- Q-7  Saved slots: in the inference server at the bundled build, what
       `--slot-save-path` and the slot save and restore API persist, the
       conditions a saved slot must meet to restore (same model file,
       context size, cache types), whether saved slots survive a server
       restart or a proxy swap, and how `--cache-reuse` relates. State
       whether "durable KV cache" is a documented property.
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
      "name": "mios_model_swap_proxy_lifecycle",
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
              "ADJUST_MODEL_MAP",
              "ADJUST_DOCS",
              "ADJUST_MAP_AND_DOCS",
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
