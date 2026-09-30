"""Hosted mode: multi-user proxy with registration, JWT tokens and tool scope.

Same feature set as the previous deployment (register UI, profile presets, per-token
tool scope, rate limit, audit log, /status) but the MCP endpoint is served by the
SDK's own stateless Streamable-HTTP app, which speaks the 2026-07-28 revision and
older handshakes alike.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.shared.exceptions import MCPError

from ..config import Config, get_config, set_config
from ..credentials import enabled_tools_var, set_credentials
# Importing ..server (not ..instance) matters: it pulls in elabftw_mcp.tools,
# which registers all 41 tools on the shared server instance.
from ..server import build_http_app, mcp, transport_security_settings
from . import register_ui
from .jwt_token import decode_token, encode_token

logger = logging.getLogger("elabftw_mcp.proxy")

# ── Audit ─────────────────────────────────────────────────────────────────────


def audit(event: str, **fields: Any) -> None:
    config = get_config()
    if not config.audit.enabled:
        return
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    try:
        path = config.audit.log_path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # a broken audit path must not break the service
        logger.warning("audit log write failed: %s", exc)


# ── Rate limiting (in-process, per client IP) ─────────────────────────────────


class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = max(1, limit)
        self.window = window
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


# ── Token handling ────────────────────────────────────────────────────────────


def _extract_token(scope: Scope) -> str:
    query = scope.get("query_string", b"")
    if isinstance(query, bytes):
        query = query.decode("latin-1")
    for part in query.split("&"):
        if part.startswith("token="):
            return urllib.parse.unquote_plus(part[len("token="):])
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            raw = value.decode("latin-1")
            if raw.lower().startswith("bearer "):
                return raw[len("bearer "):].strip()
    return ""


def _extract_header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return ""


# The old deployment let a client send X-Write-Scope per request; keep accepting it
# (it can only narrow what the token allows, never widen it).
_WRITE_SCOPES = {"read": "r", "readonly": "r", "r": "r",
                 "hybrid": "h", "h": "h",
                 "full": "f", "f": "f"}
_PROFILE_RANK = {"r": 0, "h": 1, "f": 2}


class MCPTokenMiddleware:
    """Per-request credentials for /mcp; 401 for unreadable or expired tokens.

    Pure ASGI (not BaseHTTPMiddleware) so the context vars actually reach the
    MCP handler.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        token = _extract_token(scope)
        payload = decode_token(token) if token else None
        if token and not payload:
            audit("mcp_invalid_token", token_prefix=token[:12])
            body = json.dumps({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32001, "message": "Invalid or expired token"}}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        if payload:
            profile = payload.get("p") or "r"
            header_scope = _WRITE_SCOPES.get(_extract_header(scope, b"x-write-scope").lower())
            if header_scope and _PROFILE_RANK[header_scope] < _PROFILE_RANK.get(profile, 0):
                audit("mcp_write_scope_narrowed", token_profile=profile, request_scope=header_scope)
                profile = header_scope
            set_credentials(payload["u"], payload["k"], profile, payload.get("t"))
            audit("mcp_request", user=payload.get("p"), tools=len(payload.get("t") or []))
        await self.app(scope, receive, send)


class ToolScopeMiddleware:
    """Enforce the token's tool scope inside the MCP server (both protocol eras)."""

    async def __call__(self, ctx, call_next):
        enabled = enabled_tools_var.get()
        if enabled is None:
            return await call_next(ctx)
        method = getattr(ctx, "method", "")
        params = getattr(ctx, "params", None) or {}
        if method == "tools/call" and params.get("name") not in enabled:
            audit("mcp_tool_blocked", tool=params.get("name"))
            raise MCPError(code=-32601,
                           message=f"Tool {params.get('name')!r} is not enabled for this token")
        result = await call_next(ctx)
        if method == "tools/list" and result is not None:
            tools = getattr(result, "tools", None)
            if tools is not None:
                result.tools = [t for t in tools if t.name in enabled]
            elif isinstance(result, dict) and isinstance(result.get("tools"), list):
                result["tools"] = [t for t in result["tools"]
                                   if (t.get("name") if isinstance(t, dict) else t.name) in enabled]
        return result


# ── Registration helpers ──────────────────────────────────────────────────────


async def _validate_key(base_url: str, api_key: str, verify: bool) -> tuple[int, dict[str, Any]]:
    """(status, info) — 200 means the key works; info carries user + key capability."""
    base = base_url.rstrip("/")
    headers = {"Authorization": api_key}
    async with httpx.AsyncClient(timeout=20.0, verify=verify, follow_redirects=True) as client:
        info_resp = await client.get(f"{base}/api/v2/info", headers=headers)
        if info_resp.status_code != 200:
            return info_resp.status_code, {"detail": info_resp.text[:200]}
        info = info_resp.json() if info_resp.text else {}
        user: dict[str, Any] = {}
        try:
            me = await client.get(f"{base}/api/v2/users/me", headers=headers)
            if me.status_code == 200:
                user = me.json()
        except httpx.HTTPError:
            user = {}
        can_write: int | None = None
        try:
            keys = await client.get(f"{base}/api/v2/apikeys", headers=headers)
            if keys.status_code == 200:
                entries = keys.json()
                if isinstance(entries, list) and len(entries) == 1:
                    can_write = int(entries[0].get("can_write") or 0)
        except (httpx.HTTPError, ValueError, TypeError):
            can_write = None
    return 200, {"instance": info, "user": user or {}, "can_write": can_write}


_TOOL_CACHE: list[dict[str, str]] | None = None


async def _tools_for_ui() -> list[dict[str, str]]:
    """The tool list offered in the register form — always taken from the live server."""
    global _TOOL_CACHE
    if _TOOL_CACHE is None:
        tools = await mcp.list_tools()
        _TOOL_CACHE = [{"name": t.name} for t in sorted(tools, key=lambda t: t.name)]
    return _TOOL_CACHE


# ── App factory ───────────────────────────────────────────────────────────────


class URLPrefixFixMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code in (301, 302, 307, 308):
            location = response.headers.get("location", "")
            forwarded = request.headers.get("x-forwarded-proto", "")
            if location and not location.startswith("/") and forwarded:
                parsed = urllib.parse.urlparse(location)
                if parsed.scheme and parsed.scheme != forwarded:
                    response.headers["location"] = parsed._replace(scheme=forwarded).geturl()
        return response


def create_app(config: Config | None = None) -> FastAPI:
    config = config or get_config()
    set_config(config)

    limiter = RateLimiter(int(os.environ.get("ELABFTW_MCP_RATE_LIMIT", "10")))
    mcp.middleware.append(ToolScopeMiddleware())

    mcp_http_app = build_http_app(stateless=True, host=config.server.host, path="/mcp")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # FastAPI does not run the lifespan of a mounted app; the MCP session
        # manager must be started explicitly.
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="eLabFTW MCP", lifespan=lifespan)
    app.router.lifespan_context = lifespan
    app.add_middleware(URLPrefixFixMiddleware)
    app.add_middleware(MCPTokenMiddleware)

    prefix = (config.server.url_prefix or "").rstrip("/")

    @app.get("/register")
    async def register_get() -> HTMLResponse:
        return HTMLResponse(register_ui.start_page())

    @app.post("/register")
    async def register_post(request: Request) -> HTMLResponse:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            audit("register_rate_limited", ip=client_ip)
            return HTMLResponse(register_ui.error_page(
                "Too many registration attempts", "Please wait a minute and try again."),
                status_code=429)
        form = await request.form()
        api_key = str(form.get("api_key", "")).strip()
        base_url = str(form.get("base_url", "")).strip()
        if not api_key or not base_url:
            return HTMLResponse(register_ui.error_page("API key and base URL are required."),
                                status_code=400)

        if str(form.get("validated", "")) != "1":
            try:
                status, detail = await _validate_key(base_url, api_key, config.elabftw.verify_tls)
            except httpx.HTTPError as exc:
                return HTMLResponse(register_ui.error_page(
                    "Could not reach the eLabFTW instance", str(exc)), status_code=400)
            if status == 401:
                return HTMLResponse(register_ui.error_page(
                    "The API key was rejected (HTTP 401)", "Check the key in your eLabFTW account."),
                    status_code=401)
            if status == 403:
                return HTMLResponse(register_ui.error_page(
                    "The key is valid but access was denied (HTTP 403)."), status_code=403)
            if status != 200:
                return HTMLResponse(register_ui.error_page(
                    f"eLabFTW answered HTTP {status}", str(detail.get("detail", ""))[:200]),
                    status_code=400)
            audit("register_key_validated", base_url=base_url,
                  user=(detail.get("user") or {}).get("userid"),
                  can_write=detail.get("can_write"))
            return HTMLResponse(register_ui.profile_page(
                base_url, api_key, detail.get("user") or {}, detail.get("can_write"),
                await _tools_for_ui()))

        profile = str(form.get("profile", "r")).strip().lower()
        if profile not in ("r", "h", "f"):
            profile = "r"
        selected = [t for t in form.getlist("tools") if isinstance(t, str) and t]
        # An empty selection or the literal "all" means: expose everything.
        enabled = None if (not selected or selected == ["all"]) else selected
        try:
            token = encode_token(base_url, api_key, profile, enabled_tools=enabled)
        except RuntimeError as exc:
            return HTMLResponse(register_ui.error_page("Server misconfigured", str(exc)),
                                status_code=500)
        forwarded = request.headers.get("x-forwarded-proto", "https")
        host = request.headers.get("host", "localhost")
        personal = f"{forwarded}://{host}{prefix}/mcp?token={token}"
        audit("register_token_issued", base_url=base_url, profile=profile,
              tools="all" if enabled is None else len(enabled))
        expires = int(os.environ.get("MCP_TOKEN_EXPIRY_DAYS", "30"))
        return HTMLResponse(register_ui.success_page(personal, profile, f"{prefix}/register", expires))

    @app.get("/status")
    async def status() -> JSONResponse:
        return JSONResponse({
            "service": "elabftw-mcp",
            "mode": "hosted",
            "protocol": "2026-07-28 (stateless) and older handshakes",
            "mcp_endpoint": f"{prefix}/mcp",
            "ai_available": config.ai_available,
            "write_enabled": config.features.write_enabled,
        })

    @app.get("/register/")
    async def register_slash() -> HTMLResponse:
        return HTMLResponse(register_ui.start_page())

    # Mounted last: /register and /status win, everything else goes to the MCP app.
    app.mount("/", mcp_http_app)
    return app
