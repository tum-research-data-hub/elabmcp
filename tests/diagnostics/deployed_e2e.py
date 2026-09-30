"""End-to-end check of a deployed eLabFTW MCP endpoint.

Registers a write profile token, lists the tools, calls read tools and writes
one experiment, then removes it through the API and checks that it disappears
from the listing. Read-only apart from that single write, which is cleaned up.

Configuration (all optional, the defaults match the test deployment):

    ELABFTW_MCP_URL        endpoint prefix, default https://researchmcp.duckdns.org/el
    ELABFTW_TEST_BASE_URL  the eLabFTW instance to register against
    ELABFTW_TEST_KEY_FILE  file holding the API key, default ../.elab_key

    python tests/diagnostics/deployed_e2e.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re

import httpx

HOST = os.environ.get("ELABFTW_MCP_URL", "https://researchmcp.duckdns.org/el").rstrip("/")
BASE_URL = os.environ.get("ELABFTW_TEST_BASE_URL", "https://elntest.ub.tum.de")
KEY_FILE = pathlib.Path(os.environ.get(
    "ELABFTW_TEST_KEY_FILE", pathlib.Path(__file__).resolve().parents[3] / ".elab_key"))
if not KEY_FILE.exists():
    raise SystemExit(f"no API key file at {KEY_FILE} (set ELABFTW_TEST_KEY_FILE)")
KEY = KEY_FILE.read_text(encoding="utf-8").strip()
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    results.append((bool(ok), label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' | ' + extra) if extra else ''}", flush=True)


def rpc(client: httpx.Client, url: str, method: str, params: dict | None = None) -> dict:
    r = client.post(url, headers={"Content-Type": "application/json",
                                  "Accept": "application/json, text/event-stream",
                                  "MCP-Protocol-Version": "2025-06-18"},
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    for line in r.text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            line = line[6:]
        if line.startswith("{"):
            return json.loads(line)
    return json.loads(r.text)


def text_of(body: dict) -> str:
    try:
        return body["result"]["content"][0]["text"]
    except Exception:
        return json.dumps(body)


with httpx.Client(timeout=120.0) as c:
    c.post(f"{HOST}/register", data={"api_key": KEY, "base_url": BASE_URL})
    r = c.post(f"{HOST}/register", data={"api_key": KEY, "base_url": BASE_URL,
                                         "validated": "1", "profile": "f"})
    token = re.findall(r"token=([A-Za-z0-9_\-\.]+)", r.text)
    check(bool(token), "registration issues a personal URL",
          f"HTTP {r.status_code}, token {len(token[0]) if token else 0} chars")
    if not token:
        raise SystemExit(1)
    url = f"{HOST}/mcp?token={token[0]}"

    names = sorted(t["name"] for t in rpc(c, url, "tools/list")["result"]["tools"])
    check(len(names) == 41, f"tools/list returns {len(names)} tools")

    info = text_of(rpc(c, url, "tools/call", {"name": "get_connection_info", "arguments": {}}))
    check(BASE_URL in info, "get_connection_info reports the registered instance", info[:60])

    body = text_of(rpc(c, url, "tools/call",
                       {"name": "list_experiments", "arguments": {"limit": 2}}))
    check('"paging"' in body or '"results"' in body, "list_experiments answers JSON", body[:50])

    create = next((n for n in names if n.startswith("create_experiment")), None)
    check(create is not None, "a create tool exists", str(create))
    if create:
        made = rpc(c, url, "tools/call", {"name": create, "arguments": {"title": "MCP e2e check"}})
        new_id = None
        try:
            payload = json.loads(text_of(made))
            new_id = payload.get("id") or (payload.get("experiment") or {}).get("id")
        except Exception:
            m = re.search(r"\b(\d{3,6})\b", text_of(made))
            new_id = int(m.group(1)) if m else None
        check(bool(new_id), f"{create} wrote through the endpoint", f"id={new_id}")
        if new_id:
            d = c.delete(f"{BASE_URL}/api/v2/experiments/{new_id}", headers={"Authorization": KEY})
            check(d.status_code in (200, 204), "cleanup through the API", f"HTTP {d.status_code}")
            after = text_of(rpc(c, url, "tools/call",
                                {"name": "list_experiments", "arguments": {"limit": 50}}))
            check(str(new_id) not in after, "the created entry is gone from the listing",
                  f"id {new_id}")

failed = [label for ok, label in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
for label in failed:
    print("  FAILED:", label)
raise SystemExit(1 if failed else 0)
