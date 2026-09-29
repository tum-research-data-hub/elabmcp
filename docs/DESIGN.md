# Design notes

## Shape

```
src/elabftw_mcp/
  instance.py      MCP server instance + Streamable-HTTP transport (stateless, host allow-list)
  server.py        public entry: mcp, build_http_app   (imports tools → registers them)
  __main__.py      CLI: stdio | streamable-http | --hosted
  config.py        YAML + env config, feature flags of the old deployment
  credentials.py   per-request context vars (base_url, api_key, profile, tool scope)
  policy.py        effective write flags, require() guard used by every write tool
  client.py        async REST client: pooled connections, error mapping, TTL cache
  validation.py    input normalisation with the upstream wording
  responses.py     list envelope, entity projections, always-valid JSON truncation
  ai.py            OpenAI-compatible LLM helper (placeholder fallback without a key)
  provenance.py    elabrmcp_provenance metadata block
  tools/           41 tools: read, write, steps, links, ai_tools
  proxy/           hosted mode: FastAPI app, register UI, HMAC tokens, audit, scope
```

The tool layer only knows the REST API. Credentials never live in the tools: they come
from context vars that the hosted proxy fills per request, or from the config in stdio mode.

## Why this and not something else

* **Python + MCP SDK v2** — the upstream R server (`elabrmcp` on `mcptools`) pins protocol
  2025-06-18 and cannot be moved forward; the Python SDK speaks 2026-07-28 and still answers
  older handshakes. One stateless endpoint serves both.
* **No R at runtime** — the previous deployment needed R + the elabR package inside the
  container. Here the tools are plain Python; a redeploy is a pip install, and the same
  process serves many users.
* **One process for both modes** — a single credential set (stdio) or many (hosted); the
  tool code is shared, so behaviour cannot drift between deployments.
* **elabR stays the reference** — tool names, parameters, validation rules and safety rails
  are ported 1:1 so existing prompts keep working; the AI tools keep the upstream response
  contract but actually call a model instead of returning a placeholder.

## What was verified, and how

| Layer | Evidence |
|---|---|
| Request shapes | every request matched against the official OpenAPI (`apidoc/v2/openapi.yaml`, 6.0.2, 83 paths) — 103/103 requests conform |
| Tool behaviour | all 41 tools executed against a real eLabFTW instance; each call checked for its payload contract; 46/46 checks pass and every created entity was deleted again |
| Offline | all 41 tools against an in-memory stub (`tests/fake_elabftw.py`), 45/45 |
| Protocol | stdio, stateless HTTP without handshake, legacy `initialize`, SDK client negotiation — 8/8 |
| Hosted mode | register flow with a real key, scoped token (4 tools), refused out-of-scope call, 401 on a broken token, audit entry — 13/13 |
| API details | probes in `tests/diagnostics/` for step fields, link direction and the metadata payload; findings are in the README table |

## Deliberate deviations

See the README tables: the v6 metadata structure, incoming links via `?related=`, links
limited to experiments/items, real LLM calls, provenance `source` string, and the
allow-on-missing team capability check.
