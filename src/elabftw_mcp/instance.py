"""The MCP server instance and its HTTP transport.

Runs the MCP Python SDK v2 (protocol revision 2026-07-28, stateless core) and
keeps serving older initialize handshakes from the same app.
"""
from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__, USER_AGENT  # noqa: F401  (USER_AGENT re-exported for callers)
from .config import get_config

# Hostnames allowed by the DNS-rebinding guard when this service serves HTTP.
# The guard matches the Host header exactly, hence the port wildcards.
_ALLOWED_HOSTNAMES = [
    "elabmcp.duckdns.org",
    "researchmcp.duckdns.org",
    "econversion.duckdns.org",
    "researchdata.e-conversion.de",
    "econverse.e-conversion.de",
    "localhost",
    "127.0.0.1",
    "[::1]",
]
ALLOWED_HOSTS = [host for name in _ALLOWED_HOSTNAMES for host in (name, f"{name}:*")]

mcp = MCPServer(get_config().server.name, version=__version__)


def transport_security_settings() -> TransportSecuritySettings:
    return TransportSecuritySettings(allowed_hosts=list(ALLOWED_HOSTS))


def build_http_app(*, stateless: bool = True, host: str = "127.0.0.1",
                   path: str = "/mcp", json_response: bool = False):
    """ASGI app serving Streamable HTTP (2026-07-28 and older revisions)."""
    return mcp.streamable_http_app(
        streamable_http_path=path,
        json_response=json_response,
        stateless_http=stateless,
        host=host,
        transport_security=transport_security_settings(),
    )
