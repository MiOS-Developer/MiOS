<!-- AI-hint: Index of the tracked, non-secret MiOS prompt catalog and its primary report contract; shipped prompts live under usr/share/mios/prompts/. -->
# MiOS prompt catalog

`.prompts/` is the tracked, non-secret catalog for MiOS prompt contracts. It
does not define a deployment pipeline, runtime endpoint, credential boundary,
or second system identity.

## Primary report contract

The external research and engineering report contract is:

- [`.mios/system-prompt.md`](../.mios/system-prompt.md)
- [`system-prompt.md`](../system-prompt.md) - root-level shortcut to the contract

It covers FOSS and generative patterns across OCI, CI/CD, supply chain,
immutable operating systems, and local AIOS architecture. It also defines the
repository bootstrap order, evidence rules, report schema, authentication
profiles, and required MiOS context for every change submission.

## Shipped prompt contracts

Runtime prompt files are owned by the FHS overlay under
[`usr/share/mios/prompts/`](../usr/share/mios/prompts/). The current FOSS/model
research family is:

- [`upstream-foss-patterns.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/model/upstream-foss-patterns.xml.md)
- [`model-runtime-research.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/model/model-runtime-research.xml.md)
- [`mios-cli-credential-contract.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/model/mios-cli-credential-contract.xml.md)
- [`firstboot-gguf-artifact-manifest.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/model/firstboot-gguf-artifact-manifest.xml.md)

The harvested upstream-question families answer open questions raised by a
named repository path and revision. Each replies with one JSON object that
validates against the strict OpenAI-format `response_format` schema embedded
in the prompt:

- FOSS/mesh:
  - [`userspace-mesh-node-capabilities.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/mesh/userspace-mesh-node-capabilities.xml.md)
  - [`self-hosted-mesh-coordinator.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/mesh/self-hosted-mesh-coordinator.xml.md)
- FOSS/observability:
  - [`tracing-backend-lifecycle.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/observability/tracing-backend-lifecycle.xml.md)
- FOSS/supply-chain:
  - [`telemetry-claim-corroboration.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/supply-chain/telemetry-claim-corroboration.xml.md)
- FOSS/protocol:
  - [`mcp-streamable-http-conformance.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/protocol/mcp-streamable-http-conformance.xml.md)
- FOSS/image:
  - [`logically-bound-image-lifecycle.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/image/logically-bound-image-lifecycle.xml.md)
- FOSS/model:
  - [`model-swap-proxy-lifecycle.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/model/model-swap-proxy-lifecycle.xml.md)
- FOSS/datastore:
  - [`vector-index-version-assurance.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/datastore/vector-index-version-assurance.xml.md)
- FOSS/packaging:
  - [`rpm-scriptlet-offline-manager.xml.md`](../usr/share/mios/prompts/upstream-researched-patterns/foss/packaging/rpm-scriptlet-offline-manager.xml.md)

Use those contracts for focused upstream research. Use
`.mios/system-prompt.md` when the output must be a formal architecture,
operations, OCI, CI/CD, or AIOS report.

## Contract rules

- Keep prompt text FOSS-first, standards-based, reproducible, and safe to
  regenerate from explicit inputs.
- Keep MiOS context in every report and commit submission.
- Cite primary sources, revisions, paths, evidence IDs, and confidence.
- Keep credentials, private identities, decrypted configuration, and runtime
  secrets out of prompts and reports.
- Do not duplicate the canonical runtime identity in this catalog.
- Do not add vendor-cloud URLs or proprietary agent protocols to MiOS prompt
  contracts.
