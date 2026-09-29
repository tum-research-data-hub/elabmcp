"""Register / status pages for the hosted mode (dark theme, no external assets)."""
from __future__ import annotations

import html
from typing import Any

CSS = """<style>
:root { --bg:#0b0f1a; --card:#131827; --brd:#1f2b40; --fg:#e8edf5; --muted:#8898b4; --acc:#3b82f6; }
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg);
       color:var(--fg); min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
.card { background:var(--card); border:1px solid var(--brd); border-radius:14px; padding:2rem; max-width:640px; width:100%; }
h2 { font-size:1.15rem; font-weight:700; margin-bottom:0.35rem; }
p { font-size:0.85rem; color:var(--muted); margin-bottom:1rem; line-height:1.5; }
label { display:block; font-size:0.82rem; font-weight:600; margin-bottom:0.3rem; color:var(--muted); }
input[type=text],input[type=password] { width:100%; padding:0.6rem 0.75rem; background:#1a2236;
       border:1px solid var(--brd); border-radius:8px; color:var(--fg); font-size:0.9rem; margin-bottom:0.85rem; }
button { width:100%; padding:0.65rem; background:var(--acc); color:#fff; border:none; border-radius:8px;
         font-size:0.9rem; font-weight:600; cursor:pointer; }
.url-box { background:#1a2236; border:1px solid var(--brd); border-radius:8px; padding:0.85rem;
           font-family:monospace; font-size:0.78rem; word-break:break-all; margin:0.85rem 0; }
.preset-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:10px 0 18px; }
.preset-card { display:flex; flex-direction:column; padding:12px; background:#1a2236; border:1.5px solid var(--brd);
               border-radius:8px; cursor:pointer; position:relative; }
.preset-card input { position:absolute; opacity:0; }
.preset-card:has(input:checked) { border-color:var(--acc); background:rgba(59,130,246,0.06); }
.preset-name { font-size:0.82rem; font-weight:600; }
.preset-badge { font-size:0.7rem; color:var(--muted); margin-top:4px; }
details { border:1px solid var(--brd); border-radius:8px; padding:10px 14px; margin-bottom:14px; }
summary { cursor:pointer; font-size:0.82rem; font-weight:600; color:var(--muted); }
.tool-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:6px; margin-top:12px; }
.tool-grid label { display:flex; align-items:center; gap:8px; background:#1a2236; border:1px solid var(--brd);
                   border-radius:6px; padding:6px 8px; font-size:0.75rem; font-weight:400; color:var(--fg); margin:0; }
.err { color:#f87171; font-weight:600; }
.note { font-size:0.75rem; color:var(--muted); margin-top:12px; }
</style>"""


def _page(title: str, body: str) -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)}</title>{CSS}</head><body>{body}</body></html>')


def error_page(message: str, status_hint: str = "") -> str:
    detail = f"<p>{html.escape(status_hint)}</p>" if status_hint else ""
    return _page("Registration failed", f'<div class="card"><h2 class="err">Registration failed</h2>'
                                         f'<p>{html.escape(message)}</p>{detail}'
                                         f'<a href="register" style="color:var(--acc);font-size:0.82rem">'
                                         f'Try again</a></div>')


def start_page() -> str:
    return _page("eLabFTW MCP Registration", """
<div class="card">
  <h2>eLabFTW MCP Registration</h2>
  <p>Enter your eLabFTW instance URL and a personal API key. The key is validated against your
     instance and never stored on the server — it is embedded in your personal URL (30 days).</p>
  <form method="post">
    <label>eLabFTW Base URL</label>
    <input type="text" name="base_url" value="https://elntest.ub.tum.de" required>
    <label>eLabFTW API Key</label>
    <input type="password" name="api_key" placeholder="Paste your API key" required>
    <button type="submit">Validate key</button>
  </form>
  <p class="note">Create a key in eLabFTW under Account → API keys.</p>
</div>""")


def profile_page(base_url: str, api_key: str, user: dict[str, Any], can_write: int | None,
                 tools: list[dict[str, str]], error: str = "") -> str:
    """Step 2: pick a write profile and the tools to expose."""
    key_id = (user or {}).get("userid", "")
    name = html.escape(str((user or {}).get("fullname") or ""))
    if can_write == 1:
        key_hint = ('<span style="color:#34d399">write key detected</span> — full profile preselect')
        default_profile = "f"
    elif can_write == 0:
        key_hint = '<span style="color:#fbbf24">read-only key detected</span> — read profile preselect'
        default_profile = "r"
    else:
        key_hint = ("access level could not be detected (eLabFTW 6 keys carry no key id) — "
                    "pick the profile that matches your key")
        default_profile = "h"

    presets = ""
    for value, title, badge in (("r", "Read-only", "Browse, search, no writes"),
                                ("h", "Hybrid", "Read + comments, tags, metadata"),
                                ("f", "Full", "All writes incl. create/update")):
        checked = "checked" if value == default_profile else ""
        presets += (f'<label class="preset-card"><input type="radio" name="profile" value="{value}" {checked}>'
                    f'<span class="preset-name">{title}</span><span class="preset-badge">{badge}</span></label>')

    tool_boxes = "".join(
        f'<label><input type="checkbox" name="tools" value="{html.escape(t["name"])}" checked>'
        f'{html.escape(t["name"])}</label>' for t in tools)

    error_block = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _page("eLabFTW MCP — choose scope", f"""
<div class="card">
  <h2>Key validated</h2>
  <p>Signed in as <strong>{name}</strong> (user id {html.escape(str(key_id))}). {key_hint}</p>
  {error_block}
  <form method="post">
    <input type="hidden" name="base_url" value="{html.escape(base_url)}">
    <input type="hidden" name="api_key" value="{html.escape(api_key)}">
    <input type="hidden" name="validated" value="1">
    <label>Write profile</label>
    <div class="preset-grid">{presets}</div>
    <details><summary>Tools to expose ({len(tools)})</summary>
      <div class="tool-grid">{tool_boxes}</div>
    </details>
    <button type="submit">Generate my MCP URL</button>
  </form>
</div>""")


def success_page(personal_url: str, profile: str, register_path: str, expires_days: int) -> str:
    labels = {"r": "read-only", "h": "hybrid", "f": "full"}
    return _page("eLabFTW MCP URL", f"""
<div class="card">
  <h2>Your personal MCP URL</h2>
  <p>Profile: <strong>{labels.get(profile, profile)}</strong> — valid for {expires_days} days.</p>
  <div class="url-box">{html.escape(personal_url)}</div>
  <p class="note">Paste this URL into your MCP client (Claude, Cursor, …) or into the chat app.
     Anyone with this URL acts as your eLabFTW user — treat it like a password.</p>
  <p class="note"><a href="{html.escape(register_path)}" style="color:var(--acc)">Register another key</a></p>
</div>""")
