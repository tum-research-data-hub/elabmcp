"""Errors surfaced to the MCP client."""
from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class ElabFTWError(ToolError):
    """An eLabFTW API call failed. The message is meant for the model to read."""

    def __init__(self, message: str, *, status: int | None = None, method: str | None = None,
                 url: str | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.method = method
        self.url = url
        self.body = body


class WriteNotAllowed(ToolError):
    """Write attempt while the effective scope is read-only."""


def api_error(method: str, url: str, status: int, detail: str) -> ElabFTWError:
    """Map an HTTP failure to a readable tool error (mirrors Marvin's messages)."""
    snippet = " ".join((detail or "").split())[:300]
    if status == 401:
        hint = "the API key was rejected — check that it is valid and has write permission"
    elif status == 403:
        hint = "the API key user is not allowed to do this (permissions/team scope)"
    elif status == 404:
        hint = "the requested entity does not exist or is not visible to this user"
    elif status == 400:
        hint = "the request was malformed (see the eLabFTW message)"
    else:
        hint = "unexpected eLabFTW response"
    message = f"eLabFTW API error {status} on {method} {url}: {hint}"
    if snippet:
        message += f" — {snippet}"
    return ElabFTWError(message, status=status, method=method, url=url, body=detail)
