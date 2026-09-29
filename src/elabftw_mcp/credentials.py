"""Per-request credentials and session context.

The hosted proxy sets these ContextVars per HTTP request (pure ASGI middleware,
so the values survive into the MCP handler); stdio mode falls back to the
config/environment. This is what makes one process serve many users.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from typing import Optional

base_url_var: ContextVar[Optional[str]] = ContextVar("elabftw_base_url", default=None)
api_key_var: ContextVar[Optional[str]] = ContextVar("elabftw_api_key", default=None)
write_profile_var: ContextVar[Optional[str]] = ContextVar("elabftw_write_profile", default=None)
enabled_tools_var: ContextVar[Optional[list]] = ContextVar("elabftw_enabled_tools", default=None)
trace_id_var: ContextVar[Optional[str]] = ContextVar("elabftw_trace_id", default=None)


def key_fingerprint(api_key: str) -> str:
    """Stable, non-reversible identifier for cache keys and audit lines."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def set_credentials(base_url: str, api_key: str, profile: str | None = None,
                    enabled_tools: list | None = None, trace_id: str | None = None) -> None:
    base_url_var.set(base_url.rstrip("/"))
    api_key_var.set(api_key)
    write_profile_var.set(profile)
    enabled_tools_var.set(enabled_tools)
    trace_id_var.set(trace_id)


def current_trace_id() -> str:
    return trace_id_var.get() or ""
