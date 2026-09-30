# eLabFTW MCP

Standalone [Model Context Protocol](https://modelcontextprotocol.io) server for
[eLabFTW](https://www.elabftw.net) — **41 tools**, pure Python, MCP protocol revision
**2026-07-28** (stateless) with older handshakes still served. Single-user (stdio) and
multi-user hosted mode (register page → personal URL with scoped token).

No R runtime, no external tool library: everything talks to the eLabFTW REST API v2 directly.

## Credits

The tool surface, parameter names, validation rules and the safety rails (append-only body
updates, provenance blocks, dry-run link operations) are a faithful port of

> **[Marvin Luepke](https://github.com/MarvinLuepke) — [elabR / elabrmcp](https://github.com/MarvinLuepke/elabR)**

which implemented the same 41 tools in R on top of `mcptools`/`ellmer. That implementation is
the reference this port was written against, and its documented behaviour is what the tests
assert. Thank you — this would have been a much worse server without it.

Also derived from that work: the hosted mode's feature set (register flow, profile presets,
per-token tool scope, audit log), which previously lived in the `unified-researchdata-mcp`
deployment.

## Tools (41)

| Group | Tools |
|---|---|
| Read (13) | `list_experiments`, `list_items`, `list_experiment_templates`, `list_item_types`, `get_experiment`, `get_item`, `get_experiment_template`, `get_item_type`, `list_experiment_categories`, `list_experiment_statuses`, `list_item_categories`, `list_item_statuses` |
| Meta (3) | `get_connection_info`, `get_current_user_capabilities`, `refresh_team_caps` |
| Create / update (7) | `create_experiment`, `create_item`, `create_experiment_from_template`, `update_experiment_body`, `update_item_body`, `update_entity_fields`, `update_entity_metadata` |
| Upload (1) | `upload_image_from_content` |
| Steps (5) | `list_steps`, `add_step`, `update_step`, `toggle_step`, `delete_step` |
| Links (8) | `get_entity_links`, `ensure_link`, `ensure_link_by_query`, `bulk_ensure_links`, `delete_link`, `bulk_delete_links`, `expand_links_network`, `resolve_entity_by_query` |
| AI (5) | `review_experiment`, `suggest_tags`, `suggest_metadata`, `apply_tag_suggestions`, `add_ai_review_comment` |

Write tools honour the profile of the token (`r` read-only, `h` hybrid, `f` full) and the
`features.write_*` switches in the config; destructive operations require an explicit
confirmation argument.

## Install

```bash
pip install .                 # or: uv pip install .
```

Requires Python ≥ 3.10 and an eLabFTW API key (eLabFTW → Account → API keys).

## Local (stdio) usage

```jsonc
{
  "mcpServers": {
    "elabftw": {
      "command": "elabftw-mcp",
      "env": {
        "ELABFTW_BASE_URL": "https://elntest.ub.tum.de",
        "ELABFTW_API_KEY": "your-key"
      }
    }
  }
}
```

Or against any HTTP client: `elabftw-mcp --transport streamable-http --port 8081`
(stateless by default, `--stateful` for session-based clients, `--json-response` if the
client cannot read SSE).

## Hosted (multi-user) usage

```bash
export MCP_JWT_SECRET="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
elabftw-mcp --hosted --host 0.0.0.0 --port 8081
```

* `/register` — enter instance URL + API key, pick a profile and the tools to expose
* `/mcp?token=…` — the MCP endpoint for the issued personal URL
* `/status` — service and protocol info
* audit log (JSONL), registration rate limit, `X-Forwarded-Proto` aware

Tokens are HMAC-signed and carry the instance URL, the API key, the profile and the tool
allow-list; keys are never stored server-side. Requests without a valid token get 401;
tools outside a token's scope are refused with `-32601`.

## Configuration

`config.example.yml` → `config.yml` (path via `ELABFTW_MCP_CONFIG`). Everything can be
overridden by environment variables: `ELABFTW_BASE_URL`, `ELABFTW_API_KEY`,
`ELABFTW_MCP_AI_MODEL`, `ELABFTW_MCP_AI_KEY`, `ELABFTW_MCP_AI_BASE_URL`,
`ELABFTW_MCP_RATE_LIMIT`, `MCP_JWT_SECRET`, `MCP_TOKEN_EXPIRY_DAYS`, `ELABFTW_MCP_AUDIT_LOG`.

```yaml
features:
  write_enabled: true        # master switch for every write tool
  write_create: true         # create_experiment / create_item / …
  write_update: true         # body / field / metadata updates
  allow_body_overwrite: true # otherwise only append mode is allowed
  ai_review: true            # review_experiment etc.
  cache_ttl_seconds: 300     # cache for team catalogues and /users/me
```

## AI tools

`review_experiment`, `suggest_tags` and `suggest_metadata` call an OpenAI-compatible
chat-completions endpoint (`ai.base_url`, `ai.model`, `ai.api_key`). Without a key they
answer with the upstream placeholder wording instead of failing — the response contract
(`trace_id`, `entity_type`, `entity_id`, `suggestions`, `status`) stays identical either way.

## Tests

```bash
python tests/test_api_contract.py     # offline: request shapes vs. the OpenAPI spec
python tests/test_tools_offline.py    # offline: all 41 tools against a stub eLabFTW
python tests/test_transports.py       # offline: stdio + stateless HTTP, real MCP client
python tests/test_ai_tools.py         # offline: AI tools vs. a local LLM stub (no quota spent)
python tests/test_ai_tools.py --live  # live instance + local LLM stub
python tests/test_live_tools.py       # live: all 41 tools against a real instance
python tests/test_proxy_live.py       # live: register flow, scope, protocol eras
```

For a running deployment there are two checks that need nothing but a URL:

```bash
python tests/diagnostics/deployed_e2e.py    # one endpoint: register, 41 tools, read + write + cleanup
python3 tests/diagnostics/service_sweep.py  # the whole host: web hosts, register pages, /el, /dt, /nm
```

Live suites read the key from `../.elab_key` (never printed), tag everything they create as
`elabmcp-test-<timestamp>` and delete it again. `tests/diagnostics/` holds the small probes used
to pin down the API behaviour on a new instance (step fields, link direction, metadata payload).

Status of the last full run (eLabFTW 6.0.1, MCP Python SDK 2.2.0):

| Suite | Result |
|---|---|
| `test_live_tools.py` — every tool against the live instance | **46 / 46 checks PASS**, test data cleaned up |
| `test_tools_offline.py` — every tool against the stub | **45 / 45 PASS** |
| `test_api_contract.py` — requests vs. OpenAPI (83 paths, 104 requests) | **PASS** |
| `test_ai_tools.py` — AI tools against a local OpenAI-compatible stub | **15 / 15 PASS** |
| `test_ai_tools.py --live` — same, but the entry comes from the real instance | **17 / 17 PASS** |
| `test_transports.py` — stdio + stateless HTTP + legacy handshake | **8 / 8 PASS** |
| `test_proxy_live.py` — register, scope, tokens, audit | **13 / 13 PASS** |

## API notes (measured against a running instance, not just the spec)

| Observation | Consequence for the tools |
|---|---|
| `PATCH .../steps/{id}` rejects any `action` value other than `finish` ("Incorrect parameter for steps.") although the 6.0.2 spec declares `action: update` | `update_step` sends the spec-shaped payload first and retries without `action` — works on both behaviours |
| `action: finish` **toggles** and ignores a given `finished` value | `toggle_step(finished=0/1)` verifies the stored state and toggles a second time if needed |
| `deadline` is accepted on a step PATCH, `deadline_notif` is not (HTTP 400) | `add_step` sets the deadline in a follow-up PATCH and reports the stored value; `deadline_notif` is reported as unsupported instead of silently dropped |
| `metadata` must be sent as a **JSON string**; a nested object produces HTTP 500 / MySQL 3140 | `update_entity_metadata` serialises before PATCHing |
| Links are stored on one side only: `/{type}/{id}/experiments_links` lists own links, rows carry `entityid` | outgoing via the subresource, incoming via `?related=<id>&related_origin=<type>` |
| Tags are canonicalised on write (`tga` becomes `TGA` if the team already knows the tag) and the entity payload reports them as a `a|b|c` string | tag read-backs compare case-insensitively and always return a list |
| `DELETE` is a soft delete (`state=3`): the record disappears from listings but `GET` still answers | the live suites verify removal through the listing instead of a 404 |
| Single-entity GETs can be very large (item types with long HTML bodies) | results are always valid JSON: long strings are shortened before anything is truncated |

Response statuses used by the write tools: `created`, `updated`, `deleted`, `uploaded`,
`linked`, `already_linked`, `would_link`, `unlinked`, `already_absent`, `would_unlink`, `ok`,
`partial` — the same vocabulary across all write tools.

## Relation to the previous `/el` deployment

Measured against the running R-based service (`researchmcp.duckdns.org/el`, reproduced by
`tests/diagnostics/compare_with_deployed.py`): the **tool surface is identical** — 41 tools on
both sides, none missing, none extra — and the same entities and ids come back (categories,
statuses and user info 100% identical, `get_experiment` 81%).

Deliberate differences:

| | previous deployment | this server |
|---|---|---|
| answer format | R print output (`list(limit = 2, …)`) | JSON |
| entity rows | whole row | whole row (only `body_html` is dropped, tags normalised to a list of strings) |
| protocol | 2025-06-18 only | 2026-07-28 stateless plus legacy handshakes |
| AI tools | bootstrap placeholders | real LLM calls, same response contract |
| `X-Write-Scope` | forwarded to the worker | accepted, can only narrow the token's profile |
| personal URLs | HMAC(instance, key, profile, tools, expiry) | byte-compatible format, verified with the same `MCP_JWT_SECRET` |

Only `/el` (proxy + R worker) is replaced. `/nm` (NOMAD MCP), `/dt` (DataTagger MCP), the
eLabFTW instance, the databases, Caddy and the two Streamlit apps keep running untouched.

## Documented differences from the upstream R implementation

| Topic | Behaviour here |
|---|---|
| Metadata structure | eLabFTW 6 format (`metadata.extra_fields` keyed by field name, `group_id` per field) instead of the 5.x group-keyed layout |
| Incoming links | Resolved via the documented `?related=<id>&related_origin=<type>` query — eLabFTW stores a link on one side only, so the `related_*` keys of the entity payload do not carry incoming links |
| Linkable types | eLabFTW's link routes exist for experiments and items only; template/item-type link calls fail with a clear message instead of a silent no-op |
| AI tools | Real LLM calls (upstream shipped bootstrap placeholders); same response contract |
| Provenance | Key `elabrmcp_provenance` is kept, `source` reports `elabftw-mcp` |
| Team capability flags | A missing `users_canwrite_*` flag allows the call (the API enforces permissions) instead of denying it |
| Low-level/inventory/compound flags | Present in the config for compatibility; the API is called directly |

## License

MIT, see `LICENSE`.

This server is a rewrite of Marvin Luepke's `elabMCP` R server and its `elabR` library,
both MIT licensed. The tool surface, the response shapes and the runtime quirks documented
below follow those sources; the credits are in the section above.
