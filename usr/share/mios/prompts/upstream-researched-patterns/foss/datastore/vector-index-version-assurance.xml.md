<!-- AI-hint: Primary-source prompt for the pgvector datastore: what the floating
     image tag resolves to, PostgreSQL major-version data-directory rules,
     extension advisories, and the documented HNSW build, memory, WAL and
     scan parameters MiOS sets or should set.
     AI-related: usr/share/mios/mios.toml [image.sidecars] [containers.mios-pgvector], usr/share/mios/postgres/schema-init.sql, usr/share/mios/prompts/upstream-research.xml.md, TASKS.jsonl task_3417, .prompts/README.md -->
<context>
`mios-pgvector` (port key `pgvector`) is the unified agent datastore: memory,
events, sessions, skills and a `knowledge` table with HNSW vector recall. Its
image floats `latest` while the data directory persists under `/var`, so the
PostgreSQL major version behind the tag decides whether an existing host can
start at all. A shipped research prompt still says pgvector is MiOS's one
deliberate exact-pin exception with a PostgreSQL-major suffix. The HNSW
scan parameters are passed on the server command line from `[pgvector]`
(`MIOS_PG_HNSW_*`); at `af6de6a` their keys sat under `[offline]`, whose
emitted names the Quadlet does not read, and they have since moved back.
Task task_3417 (open) asks for HNSW and WAL sizing from the deployed image
without copying a stale PostgreSQL 16 example. These are upstream facts;
MiOS must not tune or re-pin until they are settled.
</context>

<role>You are MiOS-VectorStore-Researcher. Verify; do not speculate.</role>

<task>
Answer every question in `<questions>` from the pgvector source, changelog and
README, the publisher's image registry, the PostgreSQL documentation for the
major version in use, and advisory databases, at named versions. Then decide
how MiOS should pin the image and which SSOT keys change.
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- MiOS `usr/share/mios/mios.toml` at `af6de6a`: line 7274 (`pgvector` image
  `docker.io/pgvector/pgvector:latest`), 7767 (same tag in the bake list),
  9863-9875 (`[containers.mios-pgvector]`: `PGDATA`, the persistent data
  mount, and `-c hnsw.iterative_scan`, `hnsw.max_scan_tuples`,
  `hnsw.scan_mem_multiplier` from `MIOS_PG_HNSW_*`), 5706-5723 (the
  `hnsw_*` keys and `rls_enable` sit under the `[offline]` header, so the
  resolver emits them as `MIOS_OFFLINE_HNSW_*`; see
  `automation/lib/globals.sh` lines 1730-1732), 8986-8994
  (`[database.pgvector_maintenance]` weekly concurrent reindex).
- MiOS `usr/share/mios/configurator/mios.html` at `af6de6a`: line 2768 (writes
  `pgvector.hnsw_iterative_scan`).
- MiOS `usr/share/mios/postgres/schema-init.sql` at `af6de6a`: lines 45-46,
  88-89, 570-571, 716-717 (`m = 16, ef_construction = 64`), 808-813 (HNSW
  indexes with defaults).
- MiOS `usr/share/mios/prompts/upstream-research.xml.md` at `af6de6a`: lines
  30-31 (pgvector described as an exact pin with a `-pgNN` suffix).
- MiOS `docs/research/architecture-gap-audit-2026-09-29.md` at `af6de6a`: the
  "pgvector version assurance" row (cites an advisory for parallel HNSW
  builds fixed in 0.8.2 and upstream metadata reporting 0.8.6).
- MiOS `TASKS.jsonl` at `af6de6a`: line 3416 (task_3417, open).
</provenance>

<inputs>
<extension>{{vector_extension_default_pgvector}}</extension>
<image_tag_in_tree>latest</image_tag_in_tree>
<postgresql_major_on_host>{{pg_major_or_unknown}}</postgresql_major_on_host>
<extension_version_on_host>{{extversion_or_unknown}}</extension_version_on_host>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{run_date_iso8601}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-1  Release and tag: the current pgvector release (tag, date, commit),
       the publisher image's tag scheme, the PostgreSQL major and
       pgvector version that `latest` resolves to today (digest from the
       registry API), and whether `latest` has ever moved to a new
       PostgreSQL major.
- Q-2  Data directory: what PostgreSQL documents for starting a server on
       a data directory initialized by a different major version, the
       documented upgrade paths (pg_upgrade, dump and restore), and
       pgvector's own upgrade instructions (`ALTER EXTENSION vector
       UPDATE` and any index rebuild it requires).
- Q-3  Advisories: every advisory against pgvector with its id, affected
       range and fixed version, from an advisory database. Confirm or
       contradict the parallel HNSW build advisory and the 0.8.2 and
       0.8.6 versions the audit record cites.
- Q-4  HNSW build: defaults for `m`, `ef_construction` and
       `hnsw.ef_search`; the documented memory behavior of an index build
       (the `maintenance_work_mem` threshold and the notice when the
       graph no longer fits); and the setting that controls parallel
       build workers.
- Q-5  HNSW scans: the values and defaults of `hnsw.iterative_scan`,
       `hnsw.max_scan_tuples` and `hnsw.scan_mem_multiplier`, and the
       first release that introduced each.
- Q-6  WAL and interruption: what pgvector and PostgreSQL document about
       WAL volume for HNSW builds and inserts, any guidance to build the
       index after bulk load, and what an interrupted `CREATE INDEX
       CONCURRENTLY` or `REINDEX CONCURRENTLY` leaves behind and how it
       is cleaned up.
- Q-7  Limits: the maximum dimensions an HNSW index accepts for `vector`,
       `halfvec` and `bit` at the current release.
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
      "name": "mios_vector_index_version_assurance",
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
              "PIN_TAG_WITH_PG_MAJOR",
              "PIN_DIGEST",
              "KEEP_FLOATING",
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
