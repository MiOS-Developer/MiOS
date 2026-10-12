<!-- AI-hint: Primary-source prompt for RPM scriptlets that call systemd when no
     reachable manager exists: SYSTEMD_OFFLINE semantics, what the systemd RPM
     macros run, how dnf5 reports scriptlet failure, and how to run a
     transaction inside the running manager's context.
     AI-related: automation/build.sh, CONTRIBUTING.md, usr/libexec/mios/install-nvidia-wsl-userland.sh, usr/libexec/mios/automation/35-xrdp-enhanced-session.sh, usr/share/doc/mios/manual/root.md, TASKS.jsonl task_3436, .prompts/README.md -->
<context>
MiOS installs packages in three contexts: the OCI build, the WSL2 dev
machine, and helper scripts run on a booted host. The tree sets
`SYSTEMD_OFFLINE=1` (and in the build `container=podman`) to keep RPM
scriptlets from failing or hanging, and its manual says dnf's exit code is
unreliable on the dev machine because `%post` and trigger scriptlets fail with
"Transport endpoint is not connected". The bootstrap repository's comment
claims the variable survives `sudo`. Task task_3436 (open) asks MiOS either to
run transactions inside the systemd namespace or to set `SYSTEMD_OFFLINE=1`
so scriptlets degrade cleanly, while preserving real transaction failures.
Which choice is correct depends on documented systemd, RPM macro, dnf5 and
sudo behavior; MiOS must not change its package helpers until it is settled.
</context>

<role>You are MiOS-RpmScriptlet-Researcher. Verify; do not speculate.</role>

<task>
Answer every question in `<questions>` from systemd's documentation and
source, the systemd RPM macros shipped by the Fedora release MiOS builds on,
dnf5's documentation and source, and the sudo manual, at named versions. Then
decide which execution context each MiOS install path should use, and name the
exact files and lines that change.
</task>

<provenance>
Raised by, at these revisions (re-read before relying on them):
- MiOS `automation/build.sh` at `af6de6a`: lines 165-166 (`export
  SYSTEMD_OFFLINE=1`, `export container=podman`).
- MiOS `CONTRIBUTING.md` at `af6de6a`: line 114 (both "set automatically by
  Podman; do not override").
- MiOS `usr/libexec/mios/install-nvidia-wsl-userland.sh` at `af6de6a`: line 38;
  `usr/libexec/mios/automation/35-xrdp-enhanced-session.sh` line 22 (dnf
  under `SYSTEMD_OFFLINE=1`).
- MiOS `usr/share/doc/mios/manual/root.md` at `af6de6a`: lines 6709-6718
  (scriptlets fail with "Transport endpoint is not connected" because "there's
  no systemd PID 1 to take daemon-reload"; verify with `rpm -q`).
- MiOS `TASKS.jsonl` at `af6de6a`: line 3435 (task_3436, open).
- mios-bootstrap `build-mios.ps1` at `91dc899`: lines 2847-2877 (dnf5 run
  through `sudo env` with the variable set; line 2865 says the variable
  survives sudo).
</provenance>

<inputs>
<init_system>{{systemd_version_on_base_or_unknown}}</init_system>
<package_manager>{{dnf5_version_or_unknown}}</package_manager>
<distribution_release>{{fedora_release_from_mios_toml_image}}</distribution_release>
<contexts>oci-build, wsl2-dev-machine, booted-host-helper</contexts>
<mios_revision>{{mios_short_sha}}</mios_revision>
<run_date>{{run_date_iso8601}}</run_date>
<prior_report>{{prior_report_or_none}}</prior_report>
</inputs>

<questions>
- Q-1  `SYSTEMD_OFFLINE`: where systemd documents it, which tools honor
       it, the accepted values, the first release that honored it, and
       what `systemctl` does for `enable`, `preset`, `daemon-reload` and
       `restart` when it is set.
- Q-2  Detection without it: how `systemctl` decides whether a system
       manager is running, and the documented behavior and error when a
       manager appears to be running but its bus is unreachable. Is the
       "Transport endpoint is not connected" error consistent with the
       manager being absent, or with it being present but unreachable
       from the caller's namespace?
- Q-3  RPM macros: at the Fedora release MiOS builds on, what the systemd
       macro scriptlets (`%systemd_post`, `%systemd_preun`,
       `%systemd_postun_with_restart`) and the systemd file triggers
       (daemon-reload, tmpfiles, sysusers, hwdb, udev) execute, and
       whether any of them consults `SYSTEMD_OFFLINE` or container
       detection.
- Q-4  dnf5: how dnf5 reports a failing `%post`, `%posttrans` or file-
       trigger scriptlet (warning or transaction error), its effect on
       the exit code, and any documented option that changes it.
- Q-5  Manager context: the documented ways to run a command inside the
       running manager's context (`systemd-run --wait --pipe`, `systemd-
       run --scope`, `machinectl shell`, entering PID 1's namespaces) and
       whether each gives scriptlets a reachable bus; any documented
       restriction under WSL2's systemd.
- Q-6  Container hint: what setting `container=podman` changes in
       `systemd-detect-virt`, `systemctl` and the RPM macros, and whether
       upstream documents setting it by hand outside a container.
- Q-7  sudo: under the default `env_reset` policy, does running `sudo` on
       the `env` utility with a `NAME=value` argument pass `NAME` to the
       final command, and how does that differ from passing `NAME=value`
       to `sudo` directly? Cite the sudo and sudoers manuals at a named
       version.
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
      "name": "mios_rpm_scriptlet_offline_manager",
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
              "OFFLINE_ENV_EVERYWHERE",
              "MANAGER_CONTEXT_EVERYWHERE",
              "BY_CONTEXT",
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
