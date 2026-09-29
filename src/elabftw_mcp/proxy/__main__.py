"""Hosted mode entry point (``elabftw-mcp --hosted``)."""
from __future__ import annotations

from ..config import Config, set_config


def run_hosted(config: Config | None = None) -> None:
    import uvicorn

    config = config or Config.load()
    set_config(config)
    from .app import create_app

    app = create_app(config)
    print(f"Hosted eLabFTW MCP on http://{config.server.host}:{config.server.port} "
          f"(register: {config.server.url_prefix}/register)", flush=True)
    uvicorn.run(app, host=config.server.host, port=config.server.port, log_level="info")


def main() -> None:
    run_hosted()


if __name__ == "__main__":
    main()
