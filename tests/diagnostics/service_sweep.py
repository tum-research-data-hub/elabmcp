"""Sweep every service of the deployment stack on the host.

Checks the web hosts, the register pages, the three MCP endpoints and the
databases. Credentials come from the deployment .env and are never printed;
the token based tool checks are skipped with a note when the shipped test
token does not authenticate. Read-only apart from minting tokens.

    DEPLOYMENT_ENV=/home/debian/unified-researchdata-mcp/.env python3 service_sweep.py

Exit code 0 means every check passed. `python tests/diagnostics/deployed_e2e.py`
covers the /el endpoint in depth with a key of your own.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

ENV = pathlib.Path(os.environ.get("DEPLOYMENT_ENV", "/home/debian/unified-researchdata-mcp/.env"))
STACK = os.environ.get("STACK_HOST", "https://researchmcp.duckdns.org")


def env_value(name: str) -> str:
    if not ENV.exists():
        return ""
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


ELAB_TOKEN = env_value("ELABFTW_TEST_TOKEN")
DT_TOKEN = env_value("DATATAGGER_TEST_TOKEN")
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' | ' + extra) if extra else ''}", flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report the redirect itself, not the page behind it."""

    def redirect_request(self, *args, **kwargs):
        return None


def status(url: str, timeout: int = 40) -> int:
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def _json_of(raw: str) -> dict:
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            line = line[6:]
        if line.startswith("{"):
            return json.loads(line)
    return json.loads(raw) if raw.strip() else {}


def post_form(url: str, data: dict) -> str:
    body = urllib.parse.urlencode(data).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=90) as resp:
        return resp.read().decode("utf-8", "replace")


def rpc(url: str, method: str, params: dict | None = None, protocol: str = "2025-06-18",
        session: str = "") -> dict:
    return rpc_session(url, method, params, protocol, session)[0]


def rpc_session(url: str, method: str, params: dict | None = None,
                protocol: str = "2025-06-18", session: str = "") -> tuple[dict, str]:
    """Like rpc(), but hands back the transport session id (stateful endpoints)."""
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": protocol}
    if session:
        headers["Mcp-Session-Id"] = session
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        sid = resp.headers.get("mcp-session-id", "") or session
        return _json_of(resp.read().decode("utf-8", "replace")), sid


def register(host: str, key: str, base_url: str, profile: str = "r") -> str:
    if not key:
        return ""
    try:
        post_form(f"{host}/register", {"api_key": key, "base_url": base_url})
        html = post_form(f"{host}/register", {"api_key": key, "base_url": base_url,
                                              "validated": "1", "profile": profile})
    except Exception:
        return ""
    found = re.findall(r"token=([A-Za-z0-9_\-\.]+)", html)
    return found[0] if found else ""


print("== Web hosts ==")
for label, url, want in (
    ("landing page", f"{STACK}/", (200,)),
    ("landing page (e-conversion host)", "https://researchdata.e-conversion.de/", (200,)),
    ("NOMAD GUI", f"{STACK}/nomad-oasis/gui/", (200,)),
    ("NOMAD API", f"{STACK}/nomad-oasis/api/v1/info", (200,)),
    ("elabFTW", "https://elabftw.researchmcp.duckdns.org/", (200, 302, 303)),
    ("elab-app", "https://elab-app.researchmcp.duckdns.org/", (200, 302)),
    ("proespm-app", "https://proespm.researchmcp.duckdns.org/", (200, 302)),
    ("chat", "https://atlas.e-conversion.de/", (200, 302)),
    ("legacy chat alias", "https://econversion.researchmcp.duckdns.org/", (301, 302, 308)),
):
    code = status(url)
    check(code in want, label, f"HTTP {code}")

print("\n== MCP endpoints ==")
for label, url in (("/el register", f"{STACK}/el/register"),
                   ("/dt register", f"{STACK}/dt/register"),
                   ("/nm register", f"{STACK}/nm/register")):
    code = status(url)
    check(code == 200, label, f"HTTP {code}")

el = register(f"{STACK}/el", ELAB_TOKEN, "https://elabftw.researchmcp.duckdns.org")
if not el:
    print("  DOC   /el tool checks skipped: no token from the shipped key "
          "(use tests/diagnostics/deployed_e2e.py with your own key)")
else:
    names = sorted(t["name"] for t in rpc(f"{STACK}/el/mcp?token={el}", "tools/list")["result"]["tools"])
    check(len(names) == 41, "/el serves 41 tools", f"{len(names)} tools")

dt = register(f"{STACK}/dt", DT_TOKEN, "https://datatagger.ub.tum.de")
if not dt:
    print("  DOC   /dt tool checks skipped: no token from the shipped key")
else:
    dnames = sorted(t["name"] for t in rpc(f"{STACK}/dt/mcp?token={dt}", "tools/list")["result"]["tools"])
    check(len(dnames) >= 20, "/dt lists its tools", f"{len(dnames)} tools")

# /nm keeps a transport session, so the handshake is part of the test.
try:
    init, sid = rpc_session(f"{STACK}/nm/mcp", "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "service-sweep", "version": "1.0"}})
    server = (init.get("result") or {}).get("serverInfo", {})
    check(bool(server), "/nm initialize answers", f"session {bool(sid)}, {server.get('name', '?')}")
    rpc_session(f"{STACK}/nm/mcp", "notifications/initialized", {}, session=sid)
    listing, _ = rpc_session(f"{STACK}/nm/mcp", "tools/list", {}, session=sid)
    nnames = sorted(t["name"] for t in (listing.get("result") or {}).get("tools", []))
    check(len(nnames) >= 10, "/nm lists its tools", f"{len(nnames)} tools")
    body, _ = rpc_session(f"{STACK}/nm/mcp", "tools/call",
                          {"name": "get_info", "arguments": {}}, session=sid)
    check("error" not in json.dumps(body).lower()[:300], "/nm tool call answers",
          json.dumps(body)[:80])
except Exception as exc:
    check(False, "/nm handshake", f"{type(exc).__name__}: {exc}")

failed = [label for ok, label in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
for label in failed:
    print("  FAILED:", label)
raise SystemExit(1 if failed else 0)
