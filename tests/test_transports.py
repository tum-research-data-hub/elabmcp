"""Transport check: the real MCP client against both server modes (offline stub backend).

    python tests/test_transports.py

* stdio   — spawn `elabftw-mcp --transport stdio`
* HTTP    — spawn `elabftw-mcp --transport streamable-http`, stateless, no handshake
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + extra) if extra else ''}", flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_stub() -> tuple[subprocess.Popen, str]:
    port = free_port()
    log = pathlib.Path(tempfile.mkdtemp(prefix="elabmcp-stub-")) / "stub.log"
    proc = subprocess.Popen([sys.executable, "-u", "-m", "tests.fake_elabftw", "--port", str(port)],
                            cwd=str(ROOT), stdout=log.open("w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(f"{base}/api/v2/info", timeout=2.0).status_code == 200:
                return proc, base
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError("stub did not start")


def env_for(base: str) -> dict[str, str]:
    return {**os.environ, "ELABFTW_BASE_URL": base, "ELABFTW_API_KEY": "stub-key",
            "PYTHONUNBUFFERED": "1"}


async def stdio_checks(base: str) -> None:
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=["-m", "elabftw_mcp", "--transport", "stdio"],
                                   env=env_for(base), cwd=str(ROOT))
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        check(len(tools) == 41, f"stdio: 41 tools (got {len(tools)})")
        result = await client.call_tool("list_experiment_categories", {})
        check('"results"' in result.content[0].text, "stdio: tool call returns real data",
              result.content[0].text[:60])
        created = await client.call_tool("create_experiment", {"title": "transport-stdio"})
        check('"id"' in created.content[0].text, "stdio: write tool works")
        bad = await client.call_tool("get_item_type", {"id": 999999})
        text = " ".join(block.text for block in bad.content if hasattr(block, "text"))
        check(bool(getattr(bad, "is_error", False)) or "error" in text.lower(),
              "stdio: unknown id surfaces a readable error", text[:80])


async def http_checks(base: str) -> None:
    from mcp import Client

    port = free_port()
    log = pathlib.Path(tempfile.mkdtemp(prefix="elabmcp-http-")) / "server.log"
    proc = subprocess.Popen([sys.executable, "-u", "-m", "elabftw_mcp", "--transport", "streamable-http",
                             "--host", "127.0.0.1", "--port", str(port)],
                            env=env_for(base), cwd=str(ROOT),
                            stdout=log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT)
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(80):
            try:
                if httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                         "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                                              "io.modelcontextprotocol/clientCapabilities": {}}}},
                              headers={"Content-Type": "application/json",
                                       "Mcp-Method": "tools/list",
                                       "MCP-Protocol-Version": "2026-07-28"},
                              timeout=5.0).status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        else:
            check(False, "http: server did not come up")
            return

        # modern stateless request without any handshake
        response = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                         "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                                              "io.modelcontextprotocol/clientCapabilities": {}}}},
                              headers={"Content-Type": "application/json", "Mcp-Method": "tools/list",
                                       "MCP-Protocol-Version": "2026-07-28"}, timeout=30.0)
        body = response.json()
        check(len(body["result"]["tools"]) == 41, f"http: stateless tools/list -> {len(body['result']['tools'])} tools")

        legacy = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                                  "clientInfo": {"name": "legacy", "version": "1"}}},
                            headers={"Content-Type": "application/json",
                                     "Accept": "application/json, text/event-stream"}, timeout=30.0)
        check(legacy.status_code == 200 and "protocolVersion" in legacy.text,
              "http: legacy initialize still answered")

        async with Client(url) as client:
            tools = (await client.list_tools()).tools
            check(len(tools) == 41 and client.protocol_version == "2026-07-28",
                  f"http: SDK client negotiates {client.protocol_version}, {len(tools)} tools")
            called = await client.call_tool("list_item_types", {})
            check("results" in called.content[0].text, "http: tool call through the SDK client")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.returncode not in (0, None):
            print("--- server log ---")
            print(log.read_text(encoding="utf-8", errors="replace")[-1500:])


def main() -> int:
    stub, base = start_stub()
    try:
        print("-- stdio transport --")
        asyncio.run(stdio_checks(base))
        print("\n-- stateless HTTP transport --")
        asyncio.run(http_checks(base))
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:
            stub.kill()
    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed ===")
    for label in failed:
        print("  FAILED:", label)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
