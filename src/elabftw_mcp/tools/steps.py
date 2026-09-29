"""Step (task) tools — parity with elabrmcp's handlers_steps."""
from __future__ import annotations

from typing import Any, Optional

from ..client import parse_id
from ..errors import ElabFTWError
from ..instance import mcp
from ..policy import require
from ..responses import as_json, step_summary
from ..validation import one_of
from ._common import client


def _entity(entity_type: str) -> str:
    from ..client import normalize_entity_type

    return normalize_entity_type(entity_type)


def _step_payload(step: Any) -> dict[str, Any]:
    return step if isinstance(step, dict) else {}


async def _fetch_steps(api, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    raw = await api.list_json(f"{entity_type}/{entity_id}/steps")
    return [s for s in raw if isinstance(s, dict)]


def _deep_link(base_url: str, entity_type: str, entity_id: int, step_id: Any) -> str:
    page = "experiments.php" if entity_type.startswith("experiments") else "database.php"
    url = f"{base_url}/{page}?mode=view&id={entity_id}"
    return f"{url}#step-{step_id}" if step_id else url


def _render_markdown(base_url: str, entity_type: str, entity_id: int,
                     steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "*No steps defined.*"
    lines = []
    for step in steps:
        checkbox = "- [x]" if int(step.get("finished") or 0) == 1 else "- [ ]"
        body = str(step.get("body") or "")
        step_id = step.get("id")
        display = f"[{body}]({_deep_link(base_url, entity_type, entity_id, step_id)})" if step_id else body
        lines.append(f"{checkbox} {display}")
    return "\n".join(lines)


@mcp.tool()
async def list_steps(entity_type: str, id: int, render_markdown: bool = False) -> str:
    """List all steps (tasks) attached to an eLabFTW experiment, item, experiment template or item
    type. Returns entity_type, entity_id, total_steps and a steps array (id, body, finished,
    ordering, deadline). With render_markdown=True a 'markdown' field with a GFM checkbox task list
    and deep links to each step is added. Read-only."""
    api = client()
    etype = _entity(entity_type)
    eid = parse_id(id)
    steps = await _fetch_steps(api, etype, eid)
    payload: dict[str, Any] = {
        "entity_type": etype,
        "entity_id": eid,
        "total_steps": len(steps),
        "steps": [step_summary(s) for s in steps],
    }
    if render_markdown:
        payload["markdown"] = _render_markdown(api.base_url, etype, eid, steps)
    return as_json(payload)


async def _read_step(api: Client, etype: str, eid: int, sid: int) -> dict[str, Any]:
    steps = await api.list_json(f"{etype}/{eid}/steps", cacheable=False)
    return next((s for s in (steps or []) if str(s.get("id")) == str(sid)), {})


async def _patch_step(api: Client, etype: str, eid: int, sid: int,
                      payload: dict[str, Any]) -> dict[str, Any]:
    """PATCH a step and return the stored row.

    eLabFTW builds differ (verified on 6.0.1): the OpenAPI spec declares an
    ``action`` field for updates, but the running API answers HTTP 400
    ("Incorrect parameter for steps.") as soon as ``action`` is present next to
    other fields — except ``action: finish``, which toggles the done flag.
    So the spec-shaped payload is tried first and retried without ``action``.
    """
    path = f"{etype}/{eid}/steps/{sid}"
    try:
        await api.patch(path, payload)
    except ElabFTWError as exc:
        if "Incorrect parameter" in str(exc) and len(payload) > 1 and "action" in payload:
            retry = {k: v for k, v in payload.items() if k != "action"}
            if retry:
                await api.patch(path, retry)
            else:
                raise
        else:
            raise
    return await _read_step(api, etype, eid, sid)


@mcp.tool()
async def add_step(entity_type: str, id: int, body: str,
                   deadline: Optional[str] = None, deadline_notif: Optional[int] = None) -> str:
    """Add a new step (task) to an existing eLabFTW experiment, item, experiment template or item
    type. WRITE OPERATION — requires the step write scope. A deadline (date/datetime string) is set
    with a follow-up update because the create endpoint only stores the body. Returns entity_type,
    entity_id, step_id, the stored deadline and status='created'."""
    require("write_steps")
    if not body or not str(body).strip():
        raise ElabFTWError("'body' is required and must be a non-empty string.")
    api = client()
    etype = _entity(entity_type)
    eid = parse_id(id)
    new_id, created = await api.create(f"{etype}/{eid}/steps", {"body": body})
    step_id = new_id or _step_payload(created).get("id")
    stored: dict[str, Any] = {}
    notes: list[str] = []
    if step_id and deadline:
        stored = await _patch_step(api, etype, eid, int(step_id), {"deadline": deadline})
        if not str(stored.get("deadline") or "").startswith(str(deadline)[:10]):
            notes.append("eLabFTW stored a different deadline value — check the date format.")
    if deadline_notif is not None:
        notes.append("deadline_notif cannot be set through the API on this eLabFTW build "
                     "(HTTP 400 'Incorrect parameter for steps'); set the reminder in the UI.")
    payload: dict[str, Any] = {
        "entity_type": etype,
        "entity_id": eid,
        "step_id": step_id,
        "body": body,
        "deadline": (stored or {}).get("deadline"),
        "deadline_notif": (stored or {}).get("deadline_notif"),
        "status": "created",
    }
    if notes:
        payload["notes"] = notes
    return as_json(payload)


@mcp.tool()
async def update_step(entity_type: str, id: int, step_id: int, body: Optional[str] = None,
                      deadline: Optional[str] = None, deadline_notif: Optional[int] = None) -> str:
    """Update an existing step: body and/or deadline. WRITE OPERATION — requires the step write
    scope. At least one of body, deadline, deadline_notif must be provided; deadline_notif is
    reported as unsupported when the API rejects it (see notes)."""
    require("write_steps")
    if body is None and deadline is None and deadline_notif is None:
        raise ElabFTWError("Nothing to update: provide at least one of body, deadline, deadline_notif.")
    if body is not None and not str(body).strip():
        raise ElabFTWError("'body' must be a non-empty string when provided.")
    api = client()
    etype = _entity(entity_type)
    eid = parse_id(id)
    sid = parse_id(step_id, "step_id")

    payload: dict[str, Any] = {"action": "update"}
    if body is not None:
        payload["body"] = body
    if deadline is not None:
        payload["deadline"] = deadline
    if deadline_notif is not None:
        payload["deadline_notif"] = int(deadline_notif)
    notes: list[str] = []
    try:
        stored = await _patch_step(api, etype, eid, sid, payload)
    except ElabFTWError as exc:
        if deadline_notif is not None and "Incorrect parameter" in str(exc):
            notes.append("deadline_notif is not settable through the API on this build — retried "
                         "without it.")
            reduced = {k: v for k, v in payload.items() if k != "deadline_notif"}
            stored = await _patch_step(api, etype, eid, sid, reduced)
        else:
            raise
    return as_json({
        "entity_type": etype,
        "entity_id": eid,
        "step_id": sid,
        "body": stored.get("body"),
        "deadline": stored.get("deadline"),
        "deadline_notif": stored.get("deadline_notif"),
        "status": "updated",
        "notes": notes or None,
    })


@mcp.tool()
async def toggle_step(entity_type: str, id: int, step_id: int,
                      finished: Optional[int] = None) -> str:
    """Mark a step as complete or incomplete. WRITE OPERATION — requires the step write scope.
    finished=1 marks it complete, finished=0 incomplete; when omitted the server-side finish action
    flips the current state."""
    require("write_steps")
    if finished is not None:
        finished = one_of(str(int(finished)), ("0", "1"), "finished")
    api = client()
    etype = _entity(entity_type)
    eid = parse_id(id)
    sid = parse_id(step_id, "step_id")
    notes: list[str] = []
    # `action: finish` toggles and ignores an explicit value (verified on 6.0.1),
    # so the requested state is reached by verifying and flipping again if needed.
    stored = await _patch_step(api, etype, eid, sid, {"action": "finish"})
    current = stored.get("finished")
    if finished is not None and int(current or 0) != int(finished):
        stored = await _patch_step(api, etype, eid, sid, {"action": "finish"})
        current = stored.get("finished")
        notes.append("The API toggles the state instead of setting it — a second toggle was needed.")
        if int(current or 0) != int(finished):
            notes.append(f"Requested finished={finished} could not be reached (now {current}).")
    return as_json({
        "entity_type": etype,
        "entity_id": eid,
        "step_id": sid,
        "finished": current,
        "status": "updated",
        "notes": notes or None,
    })


@mcp.tool()
async def delete_step(entity_type: str, id: int, step_id: int) -> str:
    """Delete a step from an eLabFTW experiment, item, experiment template or item type.
    WRITE OPERATION — requires the step write scope. Returns status='deleted'."""
    require("write_steps")
    api = client()
    etype = _entity(entity_type)
    eid = parse_id(id)
    sid = parse_id(step_id, "step_id")
    await api.delete(f"{etype}/{eid}/steps/{sid}")
    return as_json({"entity_type": etype, "entity_id": eid, "step_id": sid, "status": "deleted"})
