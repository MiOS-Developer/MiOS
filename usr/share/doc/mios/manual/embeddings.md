<!-- AI-hint: Manual pages distilled from the source comments of embeddings, sanitized, each passage anchored to the comment it came from. -->

# embeddings

### ingest_local.py — Embed chunks.jsonl against any...

ingest_local.py — Embed chunks.jsonl against any OpenAI-API-compatible
/v1/embeddings endpoint (LAW 5) and upsert into pgvector.

Day-0 compatible. Works against:
  - MiOS llm-light       (http://localhost:8642/v1)  ← canonical (LAW 5)
  - Ollama               (http://localhost:11434/v1)
  - vLLM                 (http://localhost:8000/v1)
  - LM Studio            (http://localhost:1234/v1)
  - llama.cpp server     (http://localhost:8080/v1)
  - LiteLLM proxy        (http://localhost:4000/v1)

Env vars (matches MiOS LAW 5: UNIFIED-AI-REDIRECTS):
  MIOS_AI_ENDPOINT    — default http://localhost:8642/v1
  MIOS_AI_KEY         — default empty (local runtime accepts empty key)
  MIOS_AI_EMBED_MODEL — default nomic-embed-text (canonical mios.toml [ai].embed_model)

pgvector:
  MIOS_PGVECTOR_HOST         — default localhost
  MIOS_PORTS_PGVECTOR   — default 5432
  MIOS_PGVECTOR_USER         — default mios
  MIOS_PGVECTOR_PASS         — default mios
  MIOS_PGVECTOR_DB           — default mios
  MIOS_SYS_ENV_TABLE   — default mios_kb (collection name with hyphens -> underscores)

Usage:
  pip install psycopg httpx
  python3 ingest_local.py [path/to/chunks.jsonl]

Default path: ./chunks.jsonl (sibling of this script when shipped under
/var/lib/mios/embeddings/).

<!-- mios-src:598ccb5e9927 from var/lib/mios/embeddings/ingest_local.py:5-36 -->
### ingest_local.py — Embed chunks.jsonl against any...

ingest_local.py — Embed chunks.jsonl against any OpenAI-API-compatible
/v1/embeddings endpoint (LAW 5) and upsert into pgvector.

Day-0 compatible. Works against:
  - MiOS llm-light       (http://localhost:8642/v1)  ← canonical (LAW 5)
  - Ollama               (http://localhost:11434/v1)
  - vLLM                 (http://localhost:8000/v1)
  - LM Studio            (http://localhost:1234/v1)
  - llama.cpp server     (http://localhost:8080/v1)
  - LiteLLM proxy        (http://localhost:4000/v1)

Env vars (matches MiOS LAW 5: UNIFIED-AI-REDIRECTS):
  MIOS_AI_ENDPOINT    — default http://localhost:8642/v1
  MIOS_AI_KEY         — default empty (local runtime accepts empty key)
  MIOS_AI_EMBED_MODEL — default nomic-embed-text (canonical mios.toml [ai].embed_model)

pgvector:
  MIOS_PGVECTOR_HOST         — default localhost
  MIOS_PORTS_PGVECTOR   — default 5432
  MIOS_PGVECTOR_USER         — default mios
  MIOS_PGVECTOR_PASS         — default mios
  MIOS_PGVECTOR_DB           — default mios
  MIOS_SYS_ENV_TABLE   — default mios_kb (collection name with hyphens -> underscores)

Usage:
  pip install psycopg httpx
  python3 ingest_local.py [path/to/chunks.jsonl]

Default path: ./chunks.jsonl (sibling of this script when shipped under
/var/lib/mios/embeddings/).

<!-- mios-src:e119308078c1 from var/lib/mios/embeddings/ingest_local.py:4-35 -->
