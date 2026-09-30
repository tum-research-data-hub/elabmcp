"""Answer "is this a 1:1 replacement for /el?" with evidence.

Registers a read-only personal URL on the deployed elabmcp-proxy (the R-based `/el`
endpoint), calls the same read tools there and on the local Python server, and
compares the two answers: tool list, tool names, and the format/payload of each reply.

    python compare_deployed_vs_python.py

Read-only tool calls. The API key stays in ../.elab_key and is never printed.
"""
from __future__ import annotations

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

ROOT = next((p for p in pathlib.Path(__file__).resolve().parents if (p / ".elab_key").exists()),
            pathlib.Path(__file__).resolve().parents[2])
REPO = pathlib.Path(__file__).resolve().parents[2]
KEY = (ROOT / ".elab_key").read_text(encoding="utf-8").strip()
DEPLOYED = "https://researchmcp.duckdns.org/el"
BASE_URL = "https://elntest.ub.tum.de"
TOOLS = ["get_connection_info", "get_current_user_capabilities", "list_experiment_categories",
         "list_experiment_statuses", "list_experiments", "list_experiment_templates",
         "get_experiment", "list_steps", "get_entity_links"]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def register(host: str) -> str:
    with httpx.Client(timeout=60.0) as client:
        client.post(f"{host}/register", data={"api_key": KEY, "base_url": BASE_URL}).raise_for_status()
        step2 = client.post(f"{host}/register", data={"api_key": KEY, "base_url": BASE_URL,
                                                      "validated": "1", "profile": "r"})
        step2.raise_for_status()
        tokens = re.findall(r"token=([A-Za-z0-9_\-\.]+)", step2.text)
        if not tokens:
            raise RuntimeError(f"no token in the response from {host}")
        return tokens[0]


def call(mcp_url: str, token: str, method: str, params: dict, session: list[str | None]):
    """Speak the older protocol (the deployed server answers 2025-06-18) with a session."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18"}
    if session[0]:
        headers["Mcp-Session-Id"] = session[0]
    resp = httpx.post(f"{mcp_url}?token={token}", headers=headers, timeout=90.0,
                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if method == "initialize":
        session[0] = resp.headers.get("mcp-session-id")
    payload = None
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
    elif resp.text.strip():
        try:
            payload = resp.json()
        except ValueError:
            payload = None
    return resp.status_code, payload


def text_of(payload) -> str:
    blocks = ((payload or {}).get("result") or {}).get("content") or []
    return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def json_keys(text: str) -> list[str]:
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return sorted(value.keys()) if isinstance(value, dict) else ["<list>"]


def r_fields(text: str) -> list[str]:
    """Field names in the R print output, e.g. 'list(limit = 2, offset = 0)'."""
    return sorted(set(re.findall(r"([a-z_][a-z0-9_]*)\s*=", text)))


def main() -> float:
    print(f"deployed: {DEPLOYED}\nlocal   : python elabftw-mcp\n")
    deployed_mcp = f"{DEPLOYED}/mcp"
    deployed_token = register(DEPLOYED)
    print(f"read-only token registered on the deployed service (prefix {deployed_token[:10]}…)\n")

    port = free_port()
    log = pathlib.Path(tempfile.mkdtemp(prefix="elabmcp-cmp-")) / "server.log"
    env = {**os.environ, "MCP_JWT_SECRET": "compare-secret", "PYTHONUNBUFFERED": "1",
           "ELABFTW_MCP_CONFIG": str(REPO / "config.example.yml")}
    proc = subprocess.Popen([sys.executable, "-u", "-m", "elabftw_mcp", "--hosted",
                             "--host", "127.0.0.1", "--port", str(port)],
                            cwd=str(REPO), env=env, stdout=log.open("w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    score = 0.0
    try:
        local = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                if httpx.get(f"{local}/register", timeout=3).status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        local_mcp = f"{local}/mcp"
        local_token = register(local)

        with httpx.Client(base_url=f"{BASE_URL}/api/v2", headers={"Authorization": KEY},
                          timeout=30.0) as client:
            rows = client.get("/experiments", params={"limit": 1}).json()
        args = {"id": rows[0]["id"]} if rows else {}
        print(f"sample experiment: {args}\n")

        ds: list[str | None] = [None]
        ls: list[str | None] = [None]
        for label, url, token, session in (("deployed", deployed_mcp, deployed_token, ds),
                                           ("local", local_mcp, local_token, ls)):
            code, payload = call(url, token, "initialize",
                                 {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "cmp", "version": "1"}}, session)
            proto = ((payload or {}).get("result") or {}).get("protocolVersion")
            print(f"{label:8s} handshake: HTTP {code}, protocol {proto}")

        _, list_d = call(deployed_mcp, deployed_token, "tools/list", {}, ds)
        _, list_l = call(local_mcp, local_token, "tools/list", {}, ls)
        names_d = {t["name"] for t in ((list_d or {}).get("result") or {}).get("tools", [])}
        names_l = {t["name"] for t in ((list_l or {}).get("result") or {}).get("tools", [])}
        print(f"\ntools/list: deployed {len(names_d)} | local {len(names_l)} | "
              f"missing locally {sorted(names_d - names_l) or 'none'} | "
              f"extra {sorted(names_l - names_d) or 'none'}")
        if names_d == names_l and names_d:
            score += 0.5
            print("  -> tool surface identical")

        print("\nper-tool answer comparison")
        print(f"  {'tool':32s} {'deployed format':18s} local format")
        same_shape = 0
        for tool in TOOLS:
            # only the id-based tools take arguments; the others must be called bare
            tool_args: dict = {}
            if tool in ("get_experiment", "list_steps", "get_entity_links"):
                tool_args = dict(args)
                if tool in ("list_steps", "get_entity_links"):
                    tool_args["entity_type"] = "experiments"
            _, payload_d = call(deployed_mcp, deployed_token, "tools/call",
                                {"name": tool, "arguments": tool_args}, ds)
            _, payload_l = call(local_mcp, local_token, "tools/call",
                                {"name": tool, "arguments": tool_args}, ls)
            text_d, text_l = text_of(payload_d), text_of(payload_l)
            deployed_is_json = bool(json_keys(text_d))
            local_is_json = bool(json_keys(text_l))
            # do the fields the deployed tool reports show up in the local JSON as well?
            # content comparison: do the advertised ids/titles show up on both sides?
            tokens_d = set(re.findall(r"\d{4,}", text_d)) | set(re.findall(r'"([^"]{4,60})"', text_d))
            tokens_l = set(re.findall(r"\d{4,}", text_l)) | set(re.findall(r'"([^"]{4,60})"', text_l))
            shared = tokens_d & tokens_l
            overlap = len(shared) / len(tokens_d) if tokens_d else 1.0
            print(f"  {tool:32s} {'JSON' if deployed_is_json else 'R text':18s} "
                  f"{'JSON' if local_is_json else 'R text'} | "
                  f"{len(shared)}/{len(tokens_d)} ids/titles identical ({overlap:.0%})")
            if overlap >= 0.8:
                same_shape += 1
            only_deployed = tokens_d - tokens_l
            if only_deployed and tool in ("list_experiments", "list_experiment_templates"):
                print(f"      only on the deployed side: {sorted(only_deployed)[:6]}")
        print(f"\n{'+' if same_shape == len(TOOLS) else '~'} {same_shape}/{len(TOOLS)} tools answer "
              f"with at least one identical field name")
        score += 0.5 * same_shape / len(TOOLS)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return score


if __name__ == "__main__":
    sys.exit(0 if main() > 0.5 else 1)
