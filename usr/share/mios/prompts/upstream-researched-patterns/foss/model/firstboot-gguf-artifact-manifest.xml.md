<!-- AI-hint: Primary-source prompt for turning a first-boot GGUF manifest entry into a
     verified one: canonical publisher, file, size, sha256 pinned to a repository
     revision, license, and runtime architecture support.
     AI-related: usr/share/mios/mios.toml [ai.firstboot_models], usr/libexec/mios/mios-models-firstboot, usr/share/mios/llamacpp/mios-llm-light.yaml, TASKS.md task_180 task_181 -->
<context>
MiOS provisions model weights on first boot from the SSOT table
`[[ai.firstboot_models]]` (name, url, dest, sha256). The fetcher streams the
download through SHA-256 and discards any file whose digest does not match,
so a wrong digest means the model never installs and a missing digest means
an unverified weight could. The single vendor entry in the tree declares a
micro-lane GGUF whose `sha256` value is the SHA-256 of zero bytes, whose URL
points at a third-party mirror repository while the lane's model map names
the publisher's own repository, and whose URL tracks a moving branch rather
than a revision. The task register says no entry should exist until URLs and
digests are verified. This prompt produces the verified facts, or states
that they cannot be verified.
</context>

<role>You are MiOS-Model-Artifact-Researcher. Verify; do not speculate.</role>

<task>
For the model named in `<inputs>`, identify the canonical publisher and GGUF
repository, then for each candidate GGUF file report the repository
revision, exact filename, byte size, SHA-256, and license, read from the
model hub's own repository metadata (for example the large-file pointer or
tree API) at a pinned revision. Compare the mirror the tree currently names
against the publisher's file. Report runtime support for the model's
architecture in the inference engine the lane runs.
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- MiOS `usr/share/mios/mios.toml` at `a67750d`: lines 11849-11854 (the one
  `[[ai.firstboot_models]]` entry: mirror repository
  `stelterlab/lfm2-700m-GGUF`, branch-tracking URL, `dest` under `/usr`,
  `sha256 = e3b0c442...b855`, which is the digest of an empty input).
- MiOS `usr/share/mios/llamacpp/mios-llm-light.yaml` at `a67750d`: lines
  9-28 (the micro lane serves LFM2-700M and names the publisher repository
  `LiquidAI/LFM2-700M-GGUF`; architecture `lfm2`).
- MiOS `TASKS.md` at `a67750d`: lines 2852 and 2856-2866 (task_180/task_181: the
  vendor list is meant to stay empty until URLs and digests are verified;
  models land in `/var/lib/mios/llamacpp/models`).
- MiOS `usr/libexec/mios/mios-models-firstboot` at `a67750d`: the fetcher
  that enforces the digest.
</provenance>

<inputs>
<model>{{model_name_default_LFM2-700M}}</model>
<lane>{{mios_lane_key_default_micro}}</lane>
<publisher_repo_in_tree>{{publisher_repo_named_by_the_lane_map}}</publisher_repo_in_tree>
<mirror_repo_in_tree>{{mirror_repo_named_by_the_manifest}}</mirror_repo_in_tree>
<quantization_wanted>{{quantization_or_any}}</quantization_wanted>
<inference_engine>{{engine_default_llama.cpp}}</inference_engine>
<engine_version_in_tree>{{engine_image_tag_or_unknown}}</engine_version_in_tree>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{date}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-1  Publisher: which organisation publishes the model, and which GGUF
       repository is its own (not a mirror)? Cite the publisher's model card.
- Q-2  Files: for each GGUF file in that repository at a pinned revision,
       the filename, quantization, byte size and SHA-256, read from hub
       metadata rather than computed from a download.
- Q-3  Mirror: does the mirror repository named in the tree exist, and is
       its file byte-identical (same SHA-256 and size) to a publisher file?
- Q-4  Pinned URL: the download URL form that resolves a file at a fixed
       repository revision instead of a moving branch.
- Q-5  License: the model's license, whether it permits redistribution
       inside an OS image and first-boot download on end-user hosts, and any
       attribution, use or revenue conditions.
- Q-6  Runtime: the first release of the inference engine that supports the
       model's architecture, and whether the engine version in the tree
       includes it.
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
- The empty-input digest in the tree is a placeholder, not evidence. Do not
  repeat it as a finding about any upstream file.
- Never report a SHA-256 you did not read from publisher or hub metadata.
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
      "name": "mios_firstboot_gguf_manifest",
      "strict": true,
      "schema": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "run_date",
          "mios_revision",
          "findings",
          "manifest_entries",
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
                    "Q-6"
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
          "manifest_entries": {
            "type": "array",
            "description": "Proposed [[ai.firstboot_models]] entries built only from VERIFIED findings; empty when none qualify.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": [
                "name",
                "url",
                "repository_revision",
                "size_bytes",
                "sha256",
                "license",
                "evidence_question_ids"
              ],
              "properties": {
                "name": {
                  "type": "string"
                },
                "url": {
                  "type": "string"
                },
                "repository_revision": {
                  "type": "string"
                },
                "size_bytes": {
                  "type": "integer"
                },
                "sha256": {
                  "type": "string",
                  "pattern": "^[0-9a-f]{64}$"
                },
                "license": {
                  "type": "string"
                },
                "evidence_question_ids": {
                  "type": "array",
                  "items": {
                    "type": "string"
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
