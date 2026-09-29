# Plan: standalone eLabFTW MCP server (Python, MCP 2026-07-28)

## Ziel

Ein **eigenständiger** MCP-Server für eLabFTW in Python — ohne R, ohne Abhängigkeit von
Marvins Repo zur Laufzeit —, der alle Funktionen abbildet, die heute in
**Marvins `elabrmcp`** (41 Tools) *und* der **gehosteten Variante im unified-researchdata-mcp**
(Register-Flow, JWT, Tool-Scope, Rate-Limit, Audit) stecken. Protokoll: MCP-Revision
**2026-07-28** (stateless) und ältere Handshakes aus derselben App.

Credits: Tool-Fläche und Fachlogik stammen aus **Marvin Luepkes `elabR` / `mcp/elabrmcp`**
(https://github.com/MarvinLuepke/elabR) — wird im README gewürdigt.

## Nicht-Ziele

- Kein Umbau des Test-Servers im Rahmen dieses Plans (alles lokal + GitHub).
- Keine eLabFTW-API-v3-Unterstützung (v2 ist das, was Marvin nutzt und was die Instanzen sprechen).
- Kein Rust.

## Referenzen (lokal, `reference/`)

| Datei | Zweck |
|---|---|
| `reference/openapi-v2-6.0.2.yaml` | offizielles OpenAPI v2 (Tag 6.0.2) — Soll-Zustand für alle Calls |
| `reference/openapi-v2-master.yaml` | Vergleich master |
| `reference/marvin/mcp/elabrmcp/R/*.R` | Fachlogik der 41 Tools (Vorlage) |
| `reference/marvin/elabR/DESCRIPTION` | Paket-Metadaten |
| `contract/elab_tools_contract.json` | tools/list der laufenden Instanz (Name, Beschreibung, inputSchema) |
| `contract/tool-params.json` | Parameter-Kurzliste je Tool |

## Architektur

```
MCP-Client ──HTTP──> FastAPI-App (/mcp = SDK-Streamable-HTTP, stateless)
                     ├─ MCPTokenMiddleware  (JWT → creds + scope, 401)
                     ├─ /register  (2-Schritt-UI, Key-Validierung, Profilwahl)
                     ├─ /status
                     └─ ToolScopeMiddleware (MCP-Server-Middleware: tools/list-Filter, tools/call-Sperre)
                              │
                     elabftw_mcp.client  (httpx async, per-Request-Credentials via ContextVar)
                              │
                        eLabFTW REST API v2
```

Ein Prozess, kein R, keine Subprozesse. stdio-Modus für lokale Clients (ein Credential-Satz),
HTTP-Modus für den gehosteten Betrieb (viele Nutzer, JWT pro Request).

## Layout

```
elabftw-mcp/
├── src/elabftw_mcp/
│   ├── __init__.py           # Version, USER_AGENT, lazy `mcp`
│   ├── __main__.py           # CLI: --transport stdio|streamable-http, --config
│   ├── config.py             # YAML-Config + Feature-Flags (Marvins config.dynamic.yml)
│   ├── credentials.py        # ContextVars: base_url, api_key, write_scope, token
│   ├── client.py             # async HTTP-Client, Fehlerabbildung, Retry, Cache
│   ├── errors.py             # Fehlerklassen + Tool-Fehlertexte
│   ├── models.py             # schlanke Response-Projektionen
│   ├── ai.py                 # LLM-Client (OpenAI-kompatibel) für die 6 AI-Tools
│   ├── server.py             # MCPServer-Instanz, build_http_app(stateless=True)
│   ├── tools/
│   │   ├── read.py           # list_*/get_* (14)
│   │   ├── write.py          # create_*/update_* (10)
│   │   ├── steps.py          # 5 Tools
│   │   ├── links.py          # 8 Tools (inkl. bulk/ensure/expand/resolve)
│   │   ├── meta.py           # caps/connection/info (2)
│   │   └── ai_tools.py       # 6 AI-Tools
│   ├── proxy/
│   │   ├── app.py            # FastAPI: /register, /mcp, /status, Audit, Rate-Limit
│   │   ├── jwt_token.py      # HMAC-Token (kompatibel zum bisherigen Format)
│   │   └── register_ui.py    # HTML der Registerseiten (dunkles Theme wie bisher)
│   └── py.typed
├── tests/
│   ├── stub_elabftw.py       # Stub-API (v2-Form, pk/Location-Quirks)
│   ├── test_tools_offline.py # alle 41 Tools gegen den Stub, Request-Assertions
│   ├── test_swagger_conformance.py # jeder Call gegen die OpenAPI v2 prüfen
│   ├── test_e2e_stdio.py     # echter stdio-Server + SDK-Client
│   ├── test_e2e_http.py      # stateless HTTP + 2026-07-28-Rohrequests
│   ├── test_proxy.py         # Register-Flow, JWT, Scope, Rate-Limit, Audit, 401
│   └── test_live_elabftw.py  # alle 41 Tools live gegen elntest (Key aus Datei)
├── docs/PLAN.md              # dieses Dokument
├── Dockerfile                # python:3.12-slim, kein R
├── docker-compose.yml        # standalone
├── Caddyfile.example
├── config.example.yml
└── README.md                 # inkl. Credits an Marvin Luepke
```

## Funktionsumfang (Parität)

**41 Tools** aus dem Vertrag: read 14 · create/update 10 · steps 5 · links 8 · caps/connection 2 · AI 6.
Signaturen = exakt `contract/elab_tools_contract.json` (Namen, Parameter, Pflichtfelder,
Beschreibungen). Darüber hinaus aus der gehosteten Fassung:

- Register-Flow (2 Schritte, Key-Validierung gegen `/api/v2/info`), Profil-Presets read/hybrid/full
- JWT (HMAC-SHA256, 30 Tage, base_url + api_key + enabled_tools + write_scope)
- Tool-Scope: tools/list gefiltert, tools/call gesperrt (-32601)
- Schreib-Scope-Flags (`effective_write_*`) aus Marvins Config
- Rate-Limit auf /register, Audit-Log (JSONL), /status, URL_PREFIX
- Cache (TTL) für Lese-Aufrufe, Provenance-Header für AI-Schreibvorgänge
- `dry_run`-Semantik bei Link-Bulk-Operationen, `confirm_danger`-Guard bei Löschoperationen

## Teststrategie

1. **Stub-Offline**: jeder Tool-Aufruf gegen einen lokalen eLabFTW-Stub; geprüft wird die
   tatsächliche HTTP-Anfrage (Methode, Pfad, Query, Body) und die Tool-Antwort.
2. **Swagger-Konformität**: jeder vom Client erzeugte Call wird gegen `openapi-v2-6.0.2.yaml`
   validiert (Pfad existiert, Methode erlaubt, Pflicht-Parameter vorhanden) — das ist der
   „nutze die neueste Swagger API"-Teil, maschinell statt per Blick.
3. **E2E-Transport**: stdio + stateless HTTP (2026-07-28-Rohrequests ohne Session).
4. **Proxy**: Register, JWT-Gültigkeit/Ablauf, Scope-Filter, 401, Rate-Limit, Audit-Eintrag.
5. **Live**: alle 41 Tools gegen `elntest.ub.tum.de` (6.0.1) mit Test-Key aus Datei;
   Ressourcen mit Präfix `elabmcp-test-<ts>`, Cleanup, Klassifikation PASS/DOC/FAIL.
   AI-Tools: LLM-Aufrufe nur in minimaler Zahl (Kosten) — Modell/Key konfigurierbar.

## Phasen

- [x] P0 Recon: Vertrag, OpenAPI, R-Quellen, Test-Key
- [ ] P1 Repo-Skeleton + dieses Dokument + Credits
- [ ] P2 Kern: config, credentials, client, errors, server, CLI
- [ ] P3 Tools: read → write → steps → links → meta → AI (41)
- [ ] P4 Proxy: JWT, Register-UI, Scope, Rate-Limit, Audit, /status
- [ ] P5 Tests: Stub, Swagger-Konformität, E2E stdio/HTTP, Proxy
- [ ] P6 Live-Test aller 41 Tools gegen elntest inkl. Cleanup
- [ ] P7 README/Credits, Dockerfile, push nach GitHub

## Risiken

| Risiko | Umgang |
|---|---|
| Fachlogik einzelner Tools (Links-BFS, AI-Prompts) nicht 1:1 klar | R-Quelle lokal lesen, Verhalten im Live-Test nachweisen |
| Live-Test schreibt in TUM-Testinstanz | nur eigene Test-Entities mit Präfix, Cleanup, kein Fremddaten-Zugriff |
| AI-Tools kosten Tokens | minimale Aufrufe, Modell konfigurierbar, Ergebnis im Report ausweisen |
| Parität unklar bei Tools ohne API-Pendant (expand_links_network etc.) | Verhalten aus R-Quelle rekonstruieren und im Test dokumentieren |
