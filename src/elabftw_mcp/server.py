"""Public server entry points: the MCP server object and its HTTP transport."""
from __future__ import annotations

from . import tools  # noqa: F401  — import side effect: registers all tools
from .instance import ALLOWED_HOSTS, build_http_app, mcp, transport_security_settings

__all__ = ["mcp", "build_http_app", "transport_security_settings", "ALLOWED_HOSTS"]
