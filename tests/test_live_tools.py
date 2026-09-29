"""Live test of all 41 tools against a real eLabFTW instance.

Everything the suite creates carries the prefix ``elabmcp-test-<timestamp>`` and
is deleted again in the teardown. Read-only tools run against existing data.

    python tests/test_live_tools.py [--keep]      # --keep skips the teardown

The API key is read from ../.elab_key and never printed or logged.
Result table: ../live_tools_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import pathlib
import sys
import time
import logging
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from elabftw_mcp.config import Config, set_config  # noqa: E402
from elabftw_mcp.credentials import set_credentials  # noqa: E402
from elabftw_mcp import tools as tool_modules  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
KEY_FILE = ROOT / ".elab_key"
BASE_URL = os.environ.get("ELABFTW_TEST_BASE_URL", "https://elntest.ub.tum.de")
STAMP = time.strftime("%Y%m%d-%H%M%S")
PREFIX = f"elabmcp-test-{STAMP}"

RESULTS: list[dict[str, str]] = []
CREATED: dict[str, list[tuple[str, int]]] = {"experiments": [], "items": [], "experiments_templates": [],
                                             "items_types": []}


def record(tool: str, status: str, note: str = "") -> None:
    RESULTS.append({"tool": tool, "status": status, "note": note[:220]})
    icon = {"PASS": "PASS", "FAIL": "FAIL", "DOC": "DOC "}.get(status, status)
    print(f"  {icon}  {tool}{(' — ' + note[:150]) if note else ''}", flush=True)


def parse(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError:
        return {"_text": text}


async def call(fn, **kwargs):
    """Call a tool function and return (payload_dict, raw_text)."""
    text = await fn(**kwargs)
    if isinstance(text, (list, tuple)):  # CallToolResult-ish
        text = getattr(text[0], "text", str(text[0])) if text else ""
    return parse(str(text)), str(text)


async def sweep_leftovers() -> None:
    """Delete entities from earlier runs (self-healing before and after the suite)."""
    from elabftw_mcp.tools._common import client as _api
    api = _api()
    for term in (PREFIX, "elabmcp-test-", "elabmcp-probe"):
        for etype in ("experiments", "items"):
            try:
                rows = await api.list_json(etype, params={"q": term, "limit": 100})
            except Exception:  # noqa: BLE001
                continue
            for row in rows if isinstance(rows, list) else []:
                rid = row.get("id") if isinstance(row, dict) else None
                if rid:
                    try:
                        await api.delete(f"/{etype}/{rid}")
                    except Exception:  # noqa: BLE001
                        pass


async def expect(tool: str, fn, *, arguments: dict, check=None, ):
    try:
        payload, text = await call(fn, **arguments)
        if check is not None:
            ok, note = check(payload)
            record(tool, "PASS" if ok else "FAIL", note)
            return payload if ok else None
        record(tool, "PASS", f"{len(text)} bytes")
        return payload
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        record(tool, "FAIL", detail)
        print("      " + traceback.format_exc().splitlines()[-2].strip()[:200], flush=True)
        return None


async def skip_documented(tool: str, reason: str) -> None:
    record(tool, "DOC", reason)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="do not delete the created test data")
    ap.add_argument("--only", default="", help="comma separated tool names to run")
    args = ap.parse_args()

    if not KEY_FILE.exists():
        print(f"missing key file: {KEY_FILE}", file=sys.stderr)
        return 2

    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = Config.load()
    config.elabftw.base_url = BASE_URL
    config.elabftw.api_key = KEY_FILE.read_text(encoding="utf-8").strip()
    config.ai.api_key = ""  # AI tools must answer with the documented fallback unless a key is given
    set_config(config)
    set_credentials(BASE_URL, config.elabftw.api_key, "f", None)

    from elabftw_mcp.tools import ai_tools, links, read, steps, write

    only = {n.strip() for n in args.only.split(",") if n.strip()}

    def wanted(name: str) -> bool:
        return not only or name in only

    print(f"\n=== eLabFTW MCP live tool suite — {BASE_URL} — {PREFIX} ===\n")
    print("-- setup --")
    await sweep_leftovers()
    cats = parse(await read.list_experiment_categories())["results"]
    item_cats = parse(await read.list_item_categories())["results"]
    exp = parse(await write.create_experiment(title=f"{PREFIX}-exp", body="live suite seed"))
    exp_id = exp["id"]
    CREATED["experiments"].append(("experiments", exp_id))
    item = parse(await write.create_item(title=f"{PREFIX}-item",
                                         category_id=item_cats[0]["id"] if item_cats else None))
    item_id = item["id"]
    CREATED["items"].append(("items", item_id))
    print(f"  test experiment {exp_id}, test item {item_id}\n")

    print("-- read --")
    await expect("list_experiments", read.list_experiments,
                 arguments={"q": PREFIX, "limit": 5},
                 check=lambda p: (p.get("paging", {}).get("returned_count", 0) >= 1,
                                  f"found {p.get('paging', {}).get('returned_count')}"))
    await expect("list_items", read.list_items, arguments={"q": PREFIX, "limit": 5},
                 check=lambda p: (len(p.get("results", [])) >= 1, f"{len(p.get('results', []))} items"))
    await expect("list_experiment_templates", read.list_experiment_templates,
                 arguments={"limit": 3},
                 check=lambda p: ("results" in p, f"{len(p.get('results', []))} templates"))
    await expect("list_item_types", read.list_item_types, arguments={"limit": 3},
                 check=lambda p: ("results" in p, f"{len(p.get('results', []))} types"))
    await expect("get_experiment", read.get_experiment, arguments={"id": exp_id},
                 check=lambda p: (p.get("experiment", {}).get("id") == exp_id, "enriched payload"))
    await expect("get_item", read.get_item, arguments={"id": item_id},
                 check=lambda p: (p.get("item", {}).get("id") == item_id, "enriched payload"))

    templates = parse(await read.list_experiment_templates(limit=1))["results"]
    if templates:
        await expect("get_experiment_template", read.get_experiment_template,
                     arguments={"id": templates[0]["id"]},
                     check=lambda p: ("experiment_template" in p, f"template {templates[0]['id']}"))
    else:
        await skip_documented("get_experiment_template", "instance has no experiment template")
    types = parse(await read.list_item_types(limit=1))["results"]
    if types:
        await expect("get_item_type", read.get_item_type, arguments={"id": types[0]["id"]},
                     check=lambda p: ("item_type" in p, f"item type {types[0]['id']}"))
    else:
        await skip_documented("get_item_type", "instance has no item type")
    await expect("list_experiment_categories", read.list_experiment_categories,
                 arguments={}, check=lambda p: (len(p["results"]) >= 1, f"{len(cats)} categories"))
    await expect("list_item_categories", read.list_item_categories,
                 arguments={}, check=lambda p: (len(p["results"]) >= 1, f"{len(item_cats)} categories"))
    await expect("list_experiment_statuses", read.list_experiment_statuses,
                 arguments={}, check=lambda p: ("results" in p, ""))
    await expect("list_item_statuses", read.list_item_statuses,
                 arguments={}, check=lambda p: ("results" in p, ""))
    await expect("get_connection_info", read.get_connection_info, arguments={},
                 check=lambda p: (bool(p.get("base_url")), f"team {p.get('active_team')}"))
    await expect("get_current_user_capabilities", read.get_current_user_capabilities, arguments={},
                 check=lambda p: (bool(p.get("userid")), f"user {p.get('userid')}"))
    await expect("refresh_team_caps", read.refresh_team_caps, arguments={},
                 check=lambda p: (bool(p.get("team_caps")), "capability flags refreshed"))

    print("\n-- write --")
    await expect("create_experiment", write.create_experiment,
                 arguments={"title": f"{PREFIX}-exp2", "body": "**created by live suite**",
                            "tags": ["elabmcp-test"]},
                 check=lambda p: (bool(p.get("id")), f"id {p.get('id')}"))
    if isinstance((p := RESULTS[-1]), dict) and p["status"] == "PASS":
        pass
    await expect("create_item", write.create_item,
                 arguments={"title": f"{PREFIX}-item2",
                            "category_id": item_cats[0]["id"] if item_cats else None},
                 check=lambda p: (bool(p.get("id")), f"id {p.get('id')}"))
    if templates:
        tpl = parse(await read.get_experiment_template(templates[0]["id"]))["experiment_template"]
        await expect("create_experiment_from_template", write.create_experiment_from_template,
                     arguments={"template_id": templates[0]["id"], "title": f"{PREFIX}-from-tpl"},
                     check=lambda p: (bool(p.get("id")), f"id {p.get('id')} from template"))
    else:
        await skip_documented("create_experiment_from_template", "instance has no template")

    await expect("update_experiment_body", write.update_experiment_body,
                 arguments={"id": exp_id, "text": "appended by live suite", "mode": "append"},
                 check=lambda p: (p.get("status") == "updated", f"mode {p.get('mode')}, body {p.get('body_length_before')}->{p.get('body_length_after')}"))
    body_after = parse(await read.get_experiment(exp_id))["experiment"]["body"]
    record("  body intact after append",
           "PASS" if "live suite seed" in str(body_after) and "appended by live suite" in str(body_after)
           else "FAIL", "original text still present")
    await expect("update_item_body", write.update_item_body,
                 arguments={"id": item_id, "text": "item body from live suite", "mode": "append"},
                 check=lambda p: (p.get("status") == "updated", f"body now {p.get('body_length_after')} chars"))
    await expect("update_entity_fields", write.update_entity_fields,
                 arguments={"entity_type": "experiments", "id": exp_id, "title": f"{PREFIX}-exp-renamed"},
                 check=lambda p: (p.get("status") == "updated" and p.get("read_back", {}).get("title") == f"{PREFIX}-exp-renamed", "title verified"))
    await expect("update_entity_metadata", write.update_entity_metadata,
                 arguments={"entity_type": "experiments", "id": exp_id,
                            "fields": json.dumps([{"key": "Instrument", "value": "LiveSuite-1",
                                                   "type": "text"}])},
                 check=lambda p: (p.get("status") == "updated" and not p.get("fields_missing_after_write"), f"{len(p.get('fields_applied', []))} fields applied"))
    png = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d494441"
        "5478da63f8cfc0f01f00050005fe2b0f4b0000000049454e44ae426082")).decode()
    await expect("upload_image_from_content", write.upload_image_from_content,
                 arguments={"entity_type": "experiments", "id": exp_id, "filename": f"{PREFIX}.png",
                            "content_base64": png, "mime_type": "image/png",
                            "comment": "live suite upload"},
                 check=lambda p: (p.get("status") == "uploaded", f"{p.get('filesize')} bytes"))

    print("\n-- steps --")
    step = await expect("add_step", steps.add_step,
                        arguments={"entity_type": "experiments", "id": exp_id,
                                   "body": f"{PREFIX} step 1", "deadline": "2027-01-31",
                                   "deadline_notif": None},
                        check=lambda p: (p.get("status") == "created", f"step {p.get('step_id')}"))
    step_id = step.get("step_id") if step else None
    if step_id:
        await expect("list_steps", steps.list_steps,
                     arguments={"entity_type": "experiments", "id": exp_id},
                     check=lambda p: (p.get("total_steps", 0) >= 1, f"{p.get('total_steps')} steps"))
        await expect("list_steps (markdown)", steps.list_steps,
                     arguments={"entity_type": "experiments", "id": exp_id, "render_markdown": True},
                     check=lambda p: ("#step-" in str(p.get("markdown", "")), "deep link rendered"))
        await expect("update_step", steps.update_step,
                     arguments={"entity_type": "experiments", "id": exp_id, "step_id": step_id,
                                "body": f"{PREFIX} step 1 updated"},
                     check=lambda p: (p.get("status") == "updated", "step body replaced"))
        await expect("toggle_step (set)", steps.toggle_step,
                     arguments={"entity_type": "experiments", "id": exp_id, "step_id": step_id,
                                "finished": 1},
                     check=lambda p: (p.get("status") == "updated" and p.get("finished") == 1, "finished=1"))
        await expect("toggle_step (flip)", steps.toggle_step,
                     arguments={"entity_type": "experiments", "id": exp_id, "step_id": step_id},
                     check=lambda p: (p.get("status") == "updated", f"flipped to {p.get('finished')}"))
        await expect("delete_step", steps.delete_step,
                     arguments={"entity_type": "experiments", "id": exp_id, "step_id": step_id},
                     check=lambda p: (p.get("status") == "deleted", "step removed"))
    else:
        for name in ("list_steps", "update_step", "toggle_step", "delete_step"):
            await skip_documented(name, "step creation failed")

    print("\n-- links --")
    await expect("ensure_link", links.ensure_link,
                 arguments={"from_type": "experiments", "from_id": exp_id, "to_type": "items",
                            "to_id": item_id},
                 check=lambda p: (p.get("result") == "would_link", f"dry-run default: {p.get('result')}"))
    await expect("ensure_link (write)", links.ensure_link,
                 arguments={"from_type": "experiments", "from_id": exp_id, "to_type": "items",
                            "to_id": item_id, "dry_run": False},
                 check=lambda p: (p.get("result") == "linked" and p.get("verified") is True,
                                  f"{p.get('result')} verified={p.get('verified')}"))
    await expect("get_entity_links", links.get_entity_links,
                 arguments={"entity_type": "experiments", "id": exp_id},
                 check=lambda p: (p.get("counts", {}).get("outgoing", 0) >= 1, f"{p.get('counts')}"))
    await expect("resolve_entity_by_query", links.resolve_entity_by_query,
                 arguments={"entity_type": "items", "q": f"{PREFIX}-item", "limit_candidates": 5},
                 check=lambda p: (p.get("resolution") in ("unique", "ambiguous"),
                                  f"{p.get('resolution')}, {len(p.get('candidates') or [])} candidates"))
    await expect("ensure_link_by_query", links.ensure_link_by_query,
                 arguments={"from_type": "experiments", "from_id": exp_id, "to_type": "items",
                            "q": f"{PREFIX}-item2", "dry_run": False},
                 check=lambda p: (p.get("status") == "ok", f"{p.get('status')}"))
    await expect("bulk_ensure_links", links.bulk_ensure_links,
                 arguments={"pairs_json": json.dumps([{"from_type": "experiments", "from_id": exp_id,
                                                       "to_type": "items", "to_id": item_id}]),
                            "dry_run": False},
                 check=lambda p: (p.get("total") == 1, f"{p.get('total')} pair(s)"))
    await expect("expand_links_network", links.expand_links_network,
                 arguments={"root_type": "experiments", "root_id": exp_id, "max_depth": 2},
                 check=lambda p: (p.get("node_count", 0) >= 2 and p.get("edge_count", 0) >= 1,
                                  f"{p.get('node_count')} nodes, {p.get('edge_count')} edges"))
    await expect("delete_link", links.delete_link,
                 arguments={"from_type": "experiments", "from_id": exp_id, "to_type": "items",
                            "to_id": item_id, "dry_run": False},
                 check=lambda p: (p.get("result") == "unlinked" and p.get("verified") is True,
                                  f"{p.get('result')}"))
    await expect("bulk_delete_links", links.bulk_delete_links,
                 arguments={"pairs_json": json.dumps([{"from_type": "experiments", "from_id": exp_id,
                                                       "to_type": "items", "to_id": item_id}]),
                            "dry_run": False},
                 check=lambda p: (p.get("total") == 1, f"{p.get('total')} pair(s)"))

    print("\n-- ai --")
    await expect("review_experiment", ai_tools.review_experiment,
                 arguments={"id": exp_id},
                 check=lambda p: (bool(p.get("trace_id")), f"status {p.get('status')}"))
    await expect("suggest_tags", ai_tools.suggest_tags,
                 arguments={"entity_type": "experiments", "id": exp_id},
                 check=lambda p: ("suggestions" in p, f"status {p.get('status')}"))
    await expect("suggest_metadata", ai_tools.suggest_metadata,
                 arguments={"entity_type": "experiments", "id": exp_id},
                 check=lambda p: ("suggestions" in p, f"status {p.get('status')}"))
    await expect("apply_tag_suggestions", ai_tools.apply_tag_suggestions,
                 arguments={"entity_type": "experiments", "id": exp_id,
                            "tags": ["elabmcp-test", "live-suite"]},
                 check=lambda p: (p.get("status") in ("ok", "partial"),
                                  f"{p.get('applied')} tags applied"))
    await expect("add_ai_review_comment", ai_tools.add_ai_review_comment,
                 arguments={"entity_type": "experiments", "id": exp_id,
                            "comment": "Live suite AI review comment."},
                 check=lambda p: (bool(p.get("trace_id")), f"status {p.get('status')}"))

    print("\n-- teardown --")
    if args.keep:
        print("  --keep: test data left in place")
    else:
        from elabftw_mcp.tools._common import client as _api
        for entity_type, entity_id in CREATED["items"] + CREATED["experiments"]:
            try:
                await _api().delete(f"/{entity_type}/{entity_id}")
            except Exception as exc:  # noqa: BLE001
                print(f"  cleanup {entity_type}/{entity_id} failed: {exc}")
        leftover = parse(await read.list_experiments(q=PREFIX, limit=20))["results"]
        extra = [r["id"] for r in leftover if r.get("id")]
        for extra_id in extra:
            try:
                await _api().delete(f"/experiments/{extra_id}")
            except Exception:  # noqa: BLE001
                pass
        print(f"  deleted test entities ({len(CREATED['experiments']) + len(CREATED['items'])} + {len(extra)})")
        remaining = parse(await read.list_experiments(q=PREFIX, limit=5))["paging"]["returned_count"]
        record("cleanup", "PASS" if remaining == 0 else "FAIL", f"{remaining} experiments left")

    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    doc = [r for r in RESULTS if r["status"] == "DOC"]
    print(f"\n=== {passed} PASS / {len(failed)} FAIL / {len(doc)} DOC  (of {len(RESULTS)} checks) ===")
    for item in failed:
        print(f"  FAIL {item['tool']}: {item['note']}")
    for item in doc:
        print(f"  DOC  {item['tool']}: {item['note']}")
    (ROOT / "live_tools_report.json").write_text(
        json.dumps({"base_url": BASE_URL, "prefix": PREFIX, "results": RESULTS}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
