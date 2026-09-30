"""Live check of the hosted mode against a real eLabFTW instance.

Starts the proxy as a subprocess (like the container does), walks the register
flow with the API key from ../.elab_key, and then speaks MCP through the issued
personal URL — legacy handshake, 2026-07-28 requests, token scope and 401.

    python tests/test_proxy_live.py

The key is read from a file and never printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
KEY_FILE = ROOT / ".elab_key"
BASE_URL = os.environ.get("ELABFTW_TEST_BASE_URL", "https://elntest.ub.tum.de")
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + extra) if extra else ''}", flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def rpc(base: str, method: str, params: dict | None = None, token: str | None = None,
        headers: dict | None = None, modern: bool = True):
    head = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    body_params = dict(params or {})
    if modern:
        head["MCP-Protocol-Version"] = "2026-07-28"
        head["Mcp-Method"] = method
        if method == "tools/call" and params and params.get("name"):
            head["Mcp-Name"] = params["name"]
        body_params["_meta"] = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientInfo": {"name": "live-check", "version": "1"},
                                "io.modelcontextprotocol/clientCapabilities": {}}
    head.update(headers or {})
    url = f"{base}/mcp" + (f"?token={token}" if token else "")
    resp = httpx.post(url, headers=head, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                               "params": body_params}, timeout=90.0)
    body = None
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                body = json.loads(line[5:].strip())
    elif resp.text.strip():
        try:
            body = resp.json()
        except ValueError:
            body = None
    return resp, body


def tool_names(body) -> list[str]:
    return sorted(t["name"] for t in ((body or {}).get("result") or {}).get("tools", []))


def result_text(body) -> str:
    blocks = ((body or {}).get("result") or {}).get("content") or []
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def main() -> int:
    if not KEY_FILE.exists():
        print(f"missing key file {KEY_FILE}", file=sys.stderr)
        return 2
    api_key = KEY_FILE.read_text(encoding="utf-8").strip()

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = pathlib.Path(tempfile.mkdtemp(prefix="elabmcp-proxy-log-")) / "server.log"
    env = {
        **os.environ,
        "MCP_JWT_SECRET": "live-check-secret",
        "MCP_TOKEN_EXPIRY_DAYS": "30",
        "ELABFTW_MCP_CONFIG": str(ROOT / "elabftw-mcp" / "config.example.yml"),
        "ELABFTW_MCP_AUDIT_LOG": str(log_path.parent / "audit.jsonl"),
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "elabftw_mcp", "--hosted", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(ROOT / "elabftw-mcp"), stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT)

    try:
        deadline = time.time() + 45
        up = False
        while time.time() < deadline and proc.poll() is None:
            try:
                if httpx.get(f"{base}/register", timeout=5.0).status_code == 200:
                    up = True
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        if not up:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-2500:])
            check(False, "hosted proxy starts and serves /register")
            return 1
        check(True, "hosted proxy starts and serves /register")

        resp = httpx.get(f"{base}/status", timeout=10.0)
        check(resp.status_code == 200 and resp.json().get("protocol", "").startswith("2026-07-28"),
              "/status reports the modern protocol")

        with httpx.Client(timeout=60.0) as client:
            step1 = client.post(f"{base}/register", data={"api_key": api_key, "base_url": BASE_URL})
            check(step1.status_code == 200 and "Write profile" in step1.text,
                  "register step 1 validates the key and shows the profile page")
            step2 = client.post(f"{base}/register",
                                data={"api_key": api_key, "base_url": BASE_URL, "validated": "1",
                                      "profile": "f", "tools": ["list_experiments", "get_experiment",
                                                                "create_experiment", "apply_tag_suggestions"]})
            tokens = re.findall(r"token=([A-Za-z0-9_\-\.]+)", step2.text)
            check(bool(tokens), "register step 2 issues a personal URL")
            scoped = tokens[0] if tokens else ""

        if not scoped:
            return 1

        print("\n-- token scope --")
        resp, body = rpc(base, "tools/list", token=scoped)
        names = tool_names(body)
        check(names == ["apply_tag_suggestions", "create_experiment", "get_experiment", "list_experiments"],
              f"scoped token sees exactly its tools ({len(names)})")
        resp, body = rpc(base, "tools/call", {"name": "delete_step",
                                              "arguments": {"entity_type": "experiment", "id": 1, "step_id": 1}},
                         token=scoped)
        check((((body or {}).get("error") or {}).get("code")) == -32601,
              "tool outside the scope is refused")

        print("\n-- real tool calls through the proxy --")
        resp, body = rpc(base, "tools/call", {"name": "list_experiments", "arguments": {"limit": 2}},
                         token=scoped)
        text = result_text(body)
        check('"returned_count"' in text, "list_experiments returns data through the token",
              f"{len(text)} bytes")
        resp, body = rpc(base, "tools/call", {"name": "get_experiment",
                                              "arguments": {"id": json.loads(text)["results"][0]["id"]}},
                         token=scoped)
        check("experiment" in json.dumps((body or {}).get("result", {})),
              "get_experiment works with the scoped token")

        print("\n-- full-scope token + legacy handshake --")
        with httpx.Client(timeout=60.0) as client:
            step2 = client.post(f"{base}/register",
                                data={"api_key": api_key, "base_url": BASE_URL, "validated": "1",
                                      "profile": "f"})
            tokens = re.findall(r"token=([A-Za-z0-9_\-\.]+)", step2.text)
            full = tokens[0] if tokens else ""
        init = httpx.post(f"{base}/mcp?token={full}", timeout=30.0,
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json, text/event-stream"},
                          json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                           "clientInfo": {"name": "legacy", "version": "1"}}})
        text = init.text
        check(init.status_code == 200 and "protocolVersion" in text,
              "legacy initialize is served on the same endpoint")
        session_id = init.headers.get("mcp-session-id")
        legacy_headers = {"MCP-Protocol-Version": "2024-11-05"}
        if session_id:
            legacy_headers["Mcp-Session-Id"] = session_id
        resp, body = rpc(base, "tools/list", token=full, headers=legacy_headers, modern=False)
        check(len(tool_names(body)) == 41, f"full token lists all 41 tools (got {len(tool_names(body))})")

        print("\n-- SDK client against the proxy URL --")

        async def sdk_check() -> tuple[int, str, str]:
            from mcp import Client
            async with Client(f"{base}/mcp?token={full}") as client:
                tools = (await client.list_tools()).tools
                result = await client.call_tool("list_experiment_categories", {})
                return len(tools), result.content[0].text[:80], client.protocol_version

        n_tools, snippet, negotiated = asyncio.run(sdk_check())
        check(n_tools == 41 and negotiated == "2026-07-28",
              f"SDK client: {n_tools} tools, negotiated {negotiated}")

        print("-- X-Write-Scope header (compatibility with the old proxy) --")
        write_args = {"from_type": "experiments", "from_id": 999999, "to_type": "items",
                      "to_id": 999998, "dry_run": False}
        resp, body = rpc(base, "tools/call", {"name": "ensure_link", "arguments": write_args},
                         token=full, headers={"X-Write-Scope": "read"})
        text = (result_text(body) or json.dumps(body)).replace(chr(10), " ")
        check("not permitted" in text.lower() and "scope" in text.lower(),
              "X-Write-Scope: read narrows a full token", text[:70])
        resp, body = rpc(base, "tools/call", {"name": "ensure_link", "arguments": write_args},
                         token=full)
        text = (result_text(body) or json.dumps(body)).replace(chr(10), " ")
        check("not permitted" not in text.lower(),
              "without the header the same call reaches eLabFTW", text[:70])

        print("-- auth --")
        resp = httpx.post(f"{base}/mcp?token=bogus", timeout=20.0,
                          headers={"Content-Type": "application/json"},
                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        check(resp.status_code == 401, f"invalid token -> HTTP {resp.status_code}")

        audit_file = pathlib.Path(env["ELABFTW_MCP_AUDIT_LOG"])
        check(audit_file.exists() and "register_token_issued" in audit_file.read_text(encoding="utf-8"),
              "audit log records the registration")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for label in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
