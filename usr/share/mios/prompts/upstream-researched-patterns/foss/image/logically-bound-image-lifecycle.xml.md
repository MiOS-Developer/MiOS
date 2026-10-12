<!-- AI-hint: Primary-source prompt for the bootc logically bound image lifecycle:
     when bound images are fetched, which storage podman must read them from,
     what install, upgrade and rollback retain, and how that squares with the
     two different additional image stores MiOS names.
     AI-related: usr/share/mios/mios.toml [build.bake], usr/share/containers/storage.conf, automation/01-system-files-overlay.sh, automation/99-postcheck.sh, TASKS.jsonl task_3413, .prompts/README.md -->
<context>
Law 3 (BOUND-IMAGES) symlinks every Quadlet into
`/usr/lib/bootc/bound-images.d/` so its image ships with the host. Task
task_3413 (in progress) must prove, for every bound unit, that its image stays
available offline before the unit starts across install, upgrade and
rollback, and must inventory the unit-scoped additional image store setting.
The tree names two stores: the bake section and 21 of the 26 shipped
container Quadlets (through a unit-scoped storage option) point at
`/usr/lib/bootc/storage`, while both `storage.conf` layers tell podman to
read `/usr/lib/containers/storage`, described there as the bound-image store
baked into `/usr`. Five container Quadlets carry no unit-scoped option. A
first-boot unit also pulls a separate image tier. Which store bootc fills, when, and how
podman is meant to see it are upstream facts; MiOS must not change either
store or the gate until they are settled.
</context>

<role>You are MiOS-BoundImage-Researcher. Verify; do not speculate.</role>

<task>
Answer every question in `<questions>` from bootc's source, documentation and
release notes and from the container storage library's documentation at
named tags. Then decide which bound-image model MiOS should keep, and name the
exact SSOT keys, files and gates that change.
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- MiOS `usr/share/mios/mios.toml` at `af6de6a`: lines 7750-7752 (`[build.bake]`:
  "Read-only bootc image store for logically bound Quadlets only",
  `additional_image_store = "/usr/lib/bootc/storage"`), 7753 onward (the
  `core` bake list, mostly floating `:latest` tags).
- MiOS `usr/share/containers/storage.conf` at `af6de6a`: lines 9-12
  (`additionalimagestores = ["/usr/lib/containers/storage"]`, called the
  "Logically-bound-image store baked into immutable /usr"); the same value in
  `etc/containers/storage.conf` line 7.
- MiOS `usr/share/containers/systemd/mios-llm-light.container` at `af6de6a`:
  line 19 (`GlobalArgs=--storage-opt=additionalimagestore=/usr/lib/bootc/storage`,
  the unit-scoped form; absent from `mios-llm-heavy.container`,
  `mios-llm-heavy-alt.container` and three `mios-webtools-*` containers).
- MiOS `automation/01-system-files-overlay.sh` at `af6de6a`: lines 121-160
  (symlinks every `.container` and `.image` Quadlet into `bound-images.d`,
  skipping first-boot-tier images).
- MiOS `automation/99-postcheck.sh` at `af6de6a`: lines 437-479 (Quadlet to
  `bound-images.d` coverage) and 606-652 (each symlink must resolve to a baked
  image; skipped when the bake is disabled).
- MiOS `usr/lib/systemd/system/mios-bound-images-firstboot.service` at
  `af6de6a`: lines 1-11 (pulls a first-boot image tier once, degrade-open).
- MiOS `TASKS.jsonl` at `af6de6a`: line 3412 (task_3413, in progress).
</provenance>

<inputs>
<bootc>{{bootc_project}}</bootc>
<bootc_version_on_base>{{bootc_version_or_unknown}}</bootc_version_on_base>
<container_storage>{{container_storage_library}}</container_storage>
<bound_dir>/usr/lib/bootc/bound-images.d</bound_dir>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{run_date_iso8601}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-1  Fetch timing: during which bootc operations (install to disk,
       install to filesystem, install to existing root, switch, upgrade,
       rollback) does bootc fetch the images named by `bound-images.d`;
       from where (registry, or storage embedded in the booted image);
       and does a failed fetch fail the operation? Name the first bootc
       release with this feature and the current stable release.
- Q-2  Accepted entries: which Quadlet file types bootc reads from
       `bound-images.d`, the required form of `Image=` (fully qualified
       name, tag, digest), whether symlinks are required or plain files
       are allowed, how bootc treats variable placeholders in `Image=`,
       and what it does with an entry it cannot parse.
- Q-3  Storage: where bootc stores bound images on the host, whether
       `/usr/lib/bootc/storage` is the documented path and what it points
       to, and the documented way to make podman use it (system-wide
       `additionalimagestores`, or per-unit storage options in the
       Quadlet). State whether `/usr/lib/containers/storage` has any role
       in the bound-image feature.
- Q-4  Images baked into the OCI image: what upstream documents for
       images copied into an additional store inside `/usr` at build time
       (often called physically bound), how they differ from logically
       bound images in update size, offline availability and garbage
       collection, and whether the two can be combined for one image.
- Q-5  Retention: does bootc keep the bound images of the rollback
       deployment, when does it prune unreferenced bound images, and
       which documented command lists or prunes them?
- Q-6  Offline install: what upstream documents for installing a host
       with bound images when the registry is unreachable, including any
       mechanism to pre-populate the bound-image store from install
       media.
- Q-7  Lint: does `bootc container lint` at the current release check
       `bound-images.d` entries, and which lints does it run against
       them?
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
      "name": "mios_logically_bound_image_lifecycle",
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
              "KEEP_LOGICALLY_BOUND",
              "SWITCH_TO_PHYSICALLY_BOUND",
              "HYBRID_BY_TIER",
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
