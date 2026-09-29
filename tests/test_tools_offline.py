"""Offline end-to-end: every tool against the in-memory eLabFTW stub.

Also records every HTTP request the tools make (written to ../offline_requests.json)
so `test_api_contract.py` can validate them against the official OpenAPI spec.

    python tests/test_tools_offline.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

RECORDED: list[dict] = []
RESULTS: list[tuple[str, str]] = []
PARSE_ERRORS: list[str] = []


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def check(tool: str, ok: bool, note: str = "") -> None:
    RESULTS.append((tool, "PASS" if ok else "FAIL"))
    print(f"  {'PASS' if ok else 'FAIL'}  {tool}{(' — ' + note) if note else ''}", flush=True)


def parsed(tool: str, text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError as exc:
        PARSE_ERRORS.append(f"{tool}: {exc}")
        return {}


def install_recorder() -> None:
    from elabftw_mcp.client import Client

    original = Client.request

    async def recording_request(self, method, path, **kwargs):
        RECORDED.append({
            "method": method.upper(),
            "path": f"/{path.lstrip('/')}",
            "params": {k: v for k, v in (kwargs.get("params") or {}).items()},
            "body_keys": sorted((kwargs.get("json") or {}).keys()) if isinstance(kwargs.get("json"), dict) else None,
        })
        return await original(self, method, path, **kwargs)

    Client.request = recording_request


async def run(base_url: str) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from elabftw_mcp.config import Config, set_config
    from elabftw_mcp.credentials import set_credentials

    config = Config.load()
    config.elabftw.base_url = base_url
    config.elabftw.api_key = "stub-key"
    config.ai.api_key = ""
    set_config(config)
    set_credentials(base_url, "stub-key", "f", None)

    from elabftw_mcp.tools import ai_tools, links, read, steps, write

    # seed a template (with a step) so the template tools have something to work with
    api = httpx.Client(base_url=f"{base_url}/api/v2", headers={"Authorization": "stub-key"}, timeout=20)
    tpl_id = int(api.post("/experiments_templates", json={"title": "offline-template"}).headers["location"].rsplit("/", 1)[-1])
    api.post(f"/experiments_templates/{tpl_id}/steps", json={"body": "template step"})
    type_id = int(api.post("/items_types", json={"title": "offline-item-type"}).headers["location"].rsplit("/", 1)[-1])
    api.close()

    print("\n-- read tools --")
    payload = parsed("list_experiments", await read.list_experiments(q="offline"))
    check("list_experiments", "paging" in payload and "results" in payload, f"{len(payload.get('results', []))} rows")
    check("list_items", "results" in parsed("list_items", await read.list_items(q="offline")))
    check("list_experiment_templates", len(parsed("list_experiment_templates", await read.list_experiment_templates()).get("results", [])) == 1)
    check("list_item_types", len(parsed("list_item_types", await read.list_item_types()).get("results", [])) == 1)
    check("list_experiment_categories", len(parsed("list_experiment_categories", await read.list_experiment_categories()).get("results", [])) == 2)
    check("list_item_categories", len(parsed("list_item_categories", await read.list_item_categories()).get("results", [])) == 1)
    check("list_experiment_statuses", "results" in parsed("list_experiment_statuses", await read.list_experiment_statuses()))
    check("list_item_statuses", "results" in parsed("list_item_statuses", await read.list_item_statuses()))
    check("refresh_team_caps", bool(parsed("refresh_team_caps", await read.refresh_team_caps()).get("team_caps")))
    info = parsed("get_connection_info", await read.get_connection_info())
    check("get_connection_info",
          info.get("base_url") == base_url and (info.get("user") or {}).get("team") == 29
          and bool(info.get("enabled_write_groups")),
          f"team {(info.get('user') or {}).get('team')}, mode {info.get('server_mode')}, "
          f"groups {len(info.get('enabled_write_groups') or [])}")
    caps = parsed("get_current_user_capabilities", await read.get_current_user_capabilities())
    check("get_current_user_capabilities", caps.get("userid") == 194)

    print("\n-- write tools --")
    created = parsed("create_experiment", await write.create_experiment(
        title="offline-exp", body="seed", tags=["offline"]))
    exp_id = created.get("id")
    check("create_experiment", created.get("status") == "created" and exp_id, f"id {exp_id}")
    item = parsed("create_item", await write.create_item(title="offline-item", category_id=None))
    item_id = item.get("id")
    check("create_item", item.get("status") == "created" and item_id, f"id {item_id}")
    from_tpl = parsed("create_experiment_from_template", await write.create_experiment_from_template(
        template_id=tpl_id, title="offline-from-template"))
    check("create_experiment_from_template", from_tpl.get("status") == "created", f"id {from_tpl.get('id')}")
    body = parsed("update_experiment_body", await write.update_experiment_body(id=exp_id, text="appended", mode="append"))
    check("update_experiment_body", body.get("status") == "updated" and body.get("body_change_verified") is True,
          f"{body.get('body_length_before')}->{body.get('body_length_after')}")
    check("update_item_body", parsed("update_item_body", await write.update_item_body(id=item_id, text="x")).get("status") == "updated")
    fields = parsed("update_entity_fields", await write.update_entity_fields(
        entity_type="experiments", id=exp_id, title="offline-exp-renamed"))
    check("update_entity_fields", fields.get("read_back", {}).get("title") == "offline-exp-renamed")
    meta = parsed("update_entity_metadata", await write.update_entity_metadata(
        entity_type="experiments", id=exp_id, fields=json.dumps([{"key": "Instrument", "value": "S1", "type": "text"}])))
    check("update_entity_metadata", meta.get("status") == "updated" and not meta.get("fields_missing_after_write"),
          f"{meta.get('fields_applied')}")
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    up = parsed("upload_image_from_content", await write.upload_image_from_content(
        entity_type="experiments", id=exp_id, filename="offline.png", content_base64=png, mime_type="image/png"))
    check("upload_image_from_content", up.get("status") == "uploaded", f"{up.get('filesize')} bytes")

    print("\n-- steps --")
    step = parsed("add_step", await steps.add_step(entity_type="experiments", id=exp_id, body="offline step",
                                                   deadline="2027-03-01"))
    sid = step.get("step_id")
    check("add_step", step.get("status") == "created" and sid, f"step {sid}, deadline {step.get('deadline')}")
    listed = parsed("list_steps", await steps.list_steps(entity_type="experiments", id=exp_id))
    check("list_steps", listed.get("total_steps", 0) >= 1, f"{listed.get('total_steps')} steps")
    md = parsed("list_steps (markdown)", await steps.list_steps(entity_type="experiments", id=exp_id, render_markdown=True))
    check("list_steps markdown", "#step-" in str(md.get("markdown", "")))
    upd = parsed("update_step", await steps.update_step(entity_type="experiments", id=exp_id, step_id=sid, body="offline step v2"))
    check("update_step", upd.get("status") == "updated" and upd.get("body") == "offline step v2")
    toggle = parsed("toggle_step", await steps.toggle_step(entity_type="experiments", id=exp_id, step_id=sid, finished=1))
    check("toggle_step", toggle.get("finished") == 1, f"finished={toggle.get('finished')}")
    check("delete_step", parsed("delete_step", await steps.delete_step(
        entity_type="experiments", id=exp_id, step_id=sid)).get("status") == "deleted")

    print("\n-- links --")
    dry = parsed("ensure_link (dry-run)", await links.ensure_link(from_type="experiments", from_id=exp_id,
                                                                 to_type="items", to_id=item_id))
    check("ensure_link dry-run", dry.get("result") == "would_link")
    link = parsed("ensure_link", await links.ensure_link(from_type="experiments", from_id=exp_id,
                                                         to_type="items", to_id=item_id, dry_run=False))
    check("ensure_link", link.get("result") == "linked" and link.get("verified") is True)
    again = parsed("ensure_link (idempotent)", await links.ensure_link(from_type="experiments", from_id=exp_id,
                                                                      to_type="items", to_id=item_id, dry_run=False))
    check("ensure_link idempotent", again.get("result") == "already_linked")
    edges = parsed("get_entity_links", await links.get_entity_links(entity_type="experiments", id=exp_id))
    check("get_entity_links", edges.get("counts", {}).get("outgoing") == 1 and edges["edges"][0]["active"] is True)
    incoming = parsed("get_entity_links (incoming)", await links.get_entity_links(entity_type="items", id=item_id))
    check("get_entity_links incoming", incoming.get("counts", {}).get("incoming") == 1,
          f"{incoming.get('counts')}")
    res = parsed("resolve_entity_by_query", await links.resolve_entity_by_query(entity_type="items", q="offline-item"))
    check("resolve_entity_by_query", res.get("resolution") == "unique", f"{res.get('resolution')}")
    byq = parsed("ensure_link_by_query", await links.ensure_link_by_query(
        from_type="experiments", from_id=exp_id, to_type="items", q="offline-item", dry_run=False))
    check("ensure_link_by_query", byq.get("status") == "ok", f"{byq.get('status')}")
    bulk = parsed("bulk_ensure_links", await links.bulk_ensure_links(
        pairs_json=json.dumps([{"from_type": "experiments", "from_id": exp_id, "to_type": "items", "to_id": item_id}]),
        dry_run=False))
    check("bulk_ensure_links", bulk.get("total") == 1, f"{bulk.get('total')} pair")
    net = parsed("expand_links_network", await links.expand_links_network(root_type="experiments", root_id=exp_id,
                                                                        max_depth=2))
    check("expand_links_network", net.get("node_count", 0) >= 2 and net.get("edge_count", 0) >= 1,
          f"{net.get('node_count')} nodes, {net.get('edge_count')} edges")
    unlink = parsed("delete_link", await links.delete_link(from_type="experiments", from_id=exp_id,
                                                          to_type="items", to_id=item_id, dry_run=False))
    check("delete_link", unlink.get("result") == "unlinked" and unlink.get("verified") is True)
    bulk_del = parsed("bulk_delete_links", await links.bulk_delete_links(
        pairs_json=json.dumps([{"from_type": "experiments", "from_id": exp_id, "to_type": "items", "to_id": item_id}]),
        dry_run=False))
    check("bulk_delete_links", bulk_del.get("total") == 1)

    print("\n-- ai tools (no endpoint configured -> documented fallback) --")
    review = parsed("review_experiment", await ai_tools.review_experiment(id=exp_id))
    check("review_experiment", review.get("status") in ("placeholder", "unavailable") and review.get("trace_id"),
          f"status {review.get('status')}")
    tags_ai = parsed("suggest_tags", await ai_tools.suggest_tags(entity_type="experiments", id=exp_id))
    check("suggest_tags", tags_ai.get("suggestions") == [] and tags_ai.get("status") in ("placeholder", "unavailable"))
    meta_ai = parsed("suggest_metadata", await ai_tools.suggest_metadata(entity_type="experiments", id=exp_id))
    check("suggest_metadata", meta_ai.get("suggestions") == [])
    applied = parsed("apply_tag_suggestions", await ai_tools.apply_tag_suggestions(
        entity_type="experiments", id=exp_id, tags=["offline-ai"]))
    check("apply_tag_suggestions", applied.get("status") in ("ok", "partial"), f"{applied.get('applied')}")
    comment = parsed("add_ai_review_comment", await ai_tools.add_ai_review_comment(
        entity_type="experiments", id=exp_id, comment="offline comment", trace_id="trace-test"))
    check("add_ai_review_comment", bool(comment.get("trace_id")), f"status {comment.get('status')}")

    print("\n-- get by id --")
    check("get_experiment", parsed("get_experiment", await read.get_experiment(id=exp_id)).get("experiment", {}).get("id") == exp_id)
    check("get_item", parsed("get_item", await read.get_item(id=item_id)).get("item", {}).get("id") == item_id)
    check("get_experiment_template", "experiment_template" in parsed("get_experiment_template", await read.get_experiment_template(id=tpl_id)))
    check("get_item_type", "item_type" in parsed("get_item_type", await read.get_item_type(id=type_id)))


def main() -> int:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    stub_log = pathlib.Path(tempfile.mkdtemp(prefix="elabmcp-stub-")) / "stub.log"
    stub = subprocess.Popen(
        [sys.executable, "-u", "-m", "tests.fake_elabftw", "--port", str(port)],
        cwd=str(pathlib.Path(__file__).resolve().parents[1]),
        stdout=stub_log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                httpx.get(f"{base_url}/api/v2/info", timeout=2.0)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        else:
            print("stub did not start"); return 2

        install_recorder()
        asyncio.run(run(base_url))
    finally:
        if stub.poll() is not None:
            print("--- stub exited early, log: ---")
            print(stub_log.read_text(encoding="utf-8", errors="replace")[-2500:])
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:
            stub.kill()

    (ROOT / "offline_requests.json").write_text(json.dumps(RECORDED, indent=1), encoding="utf-8")
    failed = [tool for tool, status in RESULTS if status == "FAIL"]
    print(f"\n=== {len(RESULTS) - len(failed)} PASS / {len(failed)} FAIL (of {len(RESULTS)} tools) ===")
    for tool in failed:
        print("  FAIL", tool)
    for error in PARSE_ERRORS:
        print("  JSON PARSE ERROR:", error)
    print(f"  {len(RECORDED)} HTTP requests recorded for the contract check")
    return 1 if failed or PARSE_ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
