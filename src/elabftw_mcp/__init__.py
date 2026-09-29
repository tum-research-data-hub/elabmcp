"""Standalone MCP server for eLabFTW.

Tool surface and domain logic are modelled on Marvin Luepke's elabR / elabrmcp
(https://github.com/MarvinLuepke/elabR) — this package reimplements them in
Python on the MCP Python SDK v2 (protocol revision 2026-07-28).
"""

__version__ = "0.1.0"

USER_AGENT = "elabftw-mcp/0.1.0"


def __getattr__(name):
    """Expose the MCP server instance as ``elabftw_mcp.mcp`` (lazy)."""
    if name == "mcp":
        from .server import mcp

        return mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
