<!-- AI-hint: Chapter 25: Local Search Engine and SearXNG. Explains local container setup and engines configuration. Covers query routing from search tools to SearXNG. Details parsing HTML results into Markdown for LLM ingestion. -->

# Chapter 25: Local Search Engine and SearXNG

> Part VI: Storage, Network & Web Planes of the [MiOS manual](../manual.md).

This chapter covers the documentation for **Local Search Engine and SearXNG** under MiOS.

### <a name="25_searxng_sovereign_search"></a>25.SearXNG Sovereign Search: SearXNG Sovereign Search

> Path Reference: `/usr/share/doc/mios/manual.md#25_searxng_sovereign_search`

#### Overview

Sovereign search services are provided locally by containerized SearXNG.

## Setup
- **Endpoint**: Runs on the `searxng` port.
- **Reachability**: loopback-only, one listener at mios.toml `[ports].searxng` in the host network namespace. Direct Windows LAN forwarding (the LAN rule and the portproxy) and tailnet serving are unsupported.
- **Security**: Disables logging and upstream search tracking.
- **Performance**: Returns results offline or via private search.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="25_agent_search_api_plumbing"></a>25.Agent Search API Plumbing: Agent Search API Plumbing

> Path Reference: `/usr/share/doc/mios/manual.md#25_agent_search_api_plumbing`

#### Overview

Agents execute search queries using SearXNG API endpoints.

## Pipeline
- **API**: Queries local endpoints on the `searxng` port.
- **Authentication**: secured via loopback trust.
- **Integration**: Backs the agent's web search tools.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="25_web_scraping_and_ingest"></a>25.Web Scraping and Ingest: Web Scraping and Ingest

> Path Reference: `/usr/share/doc/mios/manual.md#25_web_scraping_and_ingest`

#### Overview

Parsed search results are transformed into Markdown for inference ingestion.

## Details
- **Scraper**: Grabs target pages from search outputs.
- **Parser**: Formats raw HTML into clean markdown.
- **Gating**: Blocks scripts to prevent cross-site execution.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.
