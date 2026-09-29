# Deployment outline (test server first, production only after review)

The server is **not** touched by this outline. Every step is meant to be run after an explicit
go-ahead, on the test host first, with the old deployment left intact for rollback.

## What runs today

* `unified-researchdata-mcp` on the test host (`researchmcp.duckdns.org`) with the container
  `unified-researchdata-mcp-elabmcp-proxy-1`, a Python/FastAPI shell around an R worker
  (`elabrmcp` on `127.0.0.1:18080`, single-threaded, image ≈ 1.48 GB).
* Caddy (`everse_stack`) publishes `/el` for the hosted variant.

## Target

`elabftw-mcp` (this repo) serves the same tool surface in one Python process, MCP protocol
2026-07-28 stateless plus legacy handshakes, with the same register flow and token scope.

```
elabftw-mcp (image, ~200 MB)  →  /el/register (key + profile + tool scope)
                              →  /el/mcp?token=…   (stateless Streamable HTTP)
                              →  /status
```

## Steps (to be executed in this order, on the test host)

1. **Build and smoke-test locally on the host, without touching the running stack**
   ```bash
   docker build -t elabftw-mcp:0.1.0 /path/to/elabftw-mcp
   docker run --rm -p 127.0.0.1:8082:8081 -e MCP_JWT_SECRET=… elabftw-mcp:0.1.0
   curl -s http://127.0.0.1:8082/status        # protocol + mode
   ```
   Expected: `/status` answers, `/register` renders, the tool list matches this repo's 41 tools.
2. **Register a personal URL against the real eLabFTW instance** through `:8082` and call two
   read tools and one write tool through an MCP client. Nothing else on the host changed yet.
3. **Add the service to compose** next to the existing proxy (new service name, port 8081,
   `MCP_JWT_SECRET` from the existing env, `config.yml` mounted read-only, `logs/` volume).
4. **Switch the Caddy route** for `/el` from the old proxy to the new service; keep the old
   container running until the switch is verified (two curl checks plus one client call).
5. **Verify, then clean up**: `/el/register` 200, legacy `initialize` 200, `/el/mcp` tools/list
   = 41, one write call visible in eLabFTW, audit line present.
6. **Rollback** at any point: point Caddy back at the old proxy, the old container was never
   stopped. Token compatibility: the HMAC payload format is byte-identical to the old one, so
   already-issued personal URLs keep working (both need the same `MCP_JWT_SECRET`).
7. **Production** (`econversion.duckdns.org`) only after the test run is signed off, with the
   same sequence and its own `MCP_JWT_SECRET` decision.

## Must be verified on the host before step 1

* Where the compose file and its env live, and whether `MCP_JWT_SECRET` is shared with the old
  proxy (needed for step 6).
* The Caddy site block that serves `/el` today.
* Whether the AI tools should be enabled on the server, and with which endpoint/model.
* Docker build resources on the host (image build needs ~1 GB free disk).

## Open decisions for the user

1. Should the old R-based proxy be removed after a successful switch, or kept switched off?
2. AI tools on the server: enable with an endpoint, or leave them in placeholder mode?
3. Which host serves as the first target: the test host only, or test plus production in one go?
