"""CLI entry point.

    elabftw-mcp                       # stdio MCP server (single credential set)
    elabftw-mcp --transport streamable-http --host 0.0.0.0 --port 8081
    elabftw-mcp --hosted              # hosted multi-user proxy (register + JWT)
"""
from __future__ import annotations

import argparse
import os
import sys

from .config import Config, set_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elabftw-mcp")
    parser.add_argument("--config", default=os.environ.get("ELABFTW_MCP_CONFIG", "config.yml"),
                        help="YAML config file (default: config.yml, env ELABFTW_MCP_CONFIG)")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio",
                        help="stdio for local MCP clients, streamable-http to serve MCP over HTTP")
    parser.add_argument("--hosted", action="store_true",
                        help="run the hosted proxy (register UI, JWT tokens, tool scope) instead of a "
                             "plain MCP server — same as `python -m elabftw_mcp.proxy`")
    parser.add_argument("--host", default=os.environ.get("ELABFTW_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ELABFTW_MCP_PORT", "8081")))
    parser.add_argument("--endpoint", default=os.environ.get("ELABFTW_MCP_ENDPOINT", "/mcp"))
    parser.add_argument("--stateful", action="store_true",
                        help="streamable-http with sessions (default: stateless, MCP 2026-07-28)")
    parser.add_argument("--json-response", action="store_true")
    return parser


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    config = Config.load(args.config)
    config.server.host = args.host
    config.server.port = args.port
    set_config(config)

    if args.hosted:
        from .proxy.__main__ import run_hosted

        run_hosted(config)
        return

    from .server import mcp

    if args.transport == "stdio":
        print("Starting elabftw-mcp in stdio mode", file=sys.stderr)
        mcp.run(transport="stdio")
        return

    print(f"Starting elabftw-mcp on http://{args.host}:{args.port}{args.endpoint} "
          f"({'stateful' if args.stateful else 'stateless'} streamable-http)", file=sys.stderr)
    mcp.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path=args.endpoint,
        stateless_http=not args.stateful,
        json_response=args.json_response,
    )


if __name__ == "__main__":
    main()
