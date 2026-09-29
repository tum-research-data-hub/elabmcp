"""AI tools: review, tag/metadata suggestions, AI comment and tag writes.

Response contract matches elabrmcp (trace_id, entity_type, entity_id, status);
the suggestion/review payloads are really model-generated when an AI endpoint is
configured, otherwise the upstream placeholder wording is returned.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .. import ai
from ..client import normalize_entity_type, parse_id
from ..config import get_config
from ..errors import ElabFTWError
from ..instance import mcp
from ..policy import require
from ..responses import as_json, match_tags, normalize_tags
from ..validation import validate_string_array
from ._common import client, safe_fetch

REVIEW_SYSTEM = (
    "You review electronic lab notebook entries (eLabFTW). Judge documentation quality only: "
    "traceability of the method, completeness of parameters, missing safety or context information. "
    "Never invent facts that are not in the entry. Answer with JSON only."
)
REVIEW_USER = """Review this entry and return JSON with exactly these keys:
{"summary": string, "observations": [string], "risks": [string], "suggestions": [string],
 "completeness_score": integer 0-100}

Entry:
<<CONTEXT>>
"""

TAGS_SYSTEM = ("You suggest concise tags for electronic lab notebook entries. Reuse existing tag "
               "vocabulary when it fits. Never invent project names. Answer with JSON only.")
TAGS_USER = """Suggest at most 10 tags for this entry. Return JSON: {"tags": [string, ...]}
Prefer 1-3 word tags, no duplicates, no punctuation.

<<CONTEXT>>
"""

METADATA_SYSTEM = ("You propose structured metadata (custom fields) for electronic lab notebook "
                   "entries. Only propose fields that are clearly supported by the entry content. "
                   "Answer with JSON only.")
METADATA_USER = """Propose at most 10 custom fields for this entry. Return JSON:
{"fields": [{"key": string, "value": string, "type": "text"|"number"|"date"|"url"}]}

<<CONTEXT>>
"""


def _entity_payload(entity: dict[str, Any], etype: str, eid: int, trace_id: str) -> dict[str, Any]:
    return {"trace_id": trace_id, "entity_type": etype, "entity_id": eid,
            "title": entity.get("title")}


async def _load(client_, etype: str, eid: int, *, with_steps: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    entity = await client_.get_json(f"{etype}/{eid}")
    if not isinstance(entity, dict):
        raise ElabFTWError(f"{etype}/{eid} could not be read.")
    extra: dict[str, Any] = {}
    if with_steps:
        steps = await safe_fetch(client_.list_json(f"{etype}/{eid}/steps"))
        if isinstance(steps, list):
            extra["Steps"] = [s.get("body") for s in steps if isinstance(s, dict)]
    return entity, extra


@mcp.tool()
async def review_experiment(id: int) -> str:
    """Generate an AI review summary for an eLabFTW experiment (documentation quality, risks,
    suggestions, completeness score). Read-only — writes nothing. Needs an AI endpoint; without one
    the response carries the upstream placeholder marker."""
    config = get_config()
    trace_id = ai.new_trace_id()
    api = client()
    eid = parse_id(id)
    entity, extra = await _load(api, "experiments", eid, with_steps=True)
    payload = _entity_payload(entity, "experiments", eid, trace_id)

    if not config.features.ai_review or not ai.ai_available():
        payload["review"] = (
            f"{ai.PLACEHOLDER_NOTE}AI review for experiment {eid} ('{entity.get('title')}'). "
            f"Trace ID: {trace_id}"
        )
        payload["status"] = "placeholder"
        return as_json(payload)

    context = ai.entity_context(entity, extra=extra)
    answer = await ai.chat(REVIEW_SYSTEM, REVIEW_USER.replace("<<CONTEXT>>", context))
    payload["review"] = ai.parse_json_block(answer)
    payload["status"] = "ok"
    return as_json(payload)


@mcp.tool()
async def suggest_tags(entity_type: str, id: int) -> str:
    """Suggest tags for an eLabFTW experiment or item based on its content. Read-only — writes
    nothing; use apply_tag_suggestions to persist them."""
    config = get_config()
    trace_id = ai.new_trace_id()
    api = client()
    etype = normalize_entity_type(entity_type)
    eid = parse_id(id)
    entity, _ = await _load(api, etype, eid)
    payload = _entity_payload(entity, etype, eid, trace_id)

    if not config.features.tag_suggestions or not ai.ai_available():
        payload["suggestions"] = []
        payload["note"] = (f"{ai.PLACEHOLDER_NOTE}Tag suggestions for {etype} {eid} are not "
                           f"available. Trace ID: {trace_id}")
        payload["status"] = "placeholder"
        return as_json(payload)

    answer = await ai.chat(TAGS_SYSTEM, TAGS_USER.replace("<<CONTEXT>>", ai.entity_context(entity)),
                           json_mode=False)
    parsed = ai.parse_json_block(answer)
    tags = parsed.get("tags") if isinstance(parsed, dict) else parsed
    payload["suggestions"] = validate_string_array(tags) or []
    payload["status"] = "ok"
    return as_json(payload)


@mcp.tool()
async def suggest_metadata(entity_type: str, id: int) -> str:
    """Suggest structured metadata (custom fields) for an eLabFTW experiment or item. Read-only —
    writes nothing; feed the result into update_entity_metadata to persist it."""
    config = get_config()
    trace_id = ai.new_trace_id()
    api = client()
    etype = normalize_entity_type(entity_type)
    eid = parse_id(id)
    entity, _ = await _load(api, etype, eid)
    payload = _entity_payload(entity, etype, eid, trace_id)

    if not config.features.metadata_suggestions or not ai.ai_available():
        payload["suggestions"] = []
        payload["note"] = (f"{ai.PLACEHOLDER_NOTE}Metadata suggestions for {etype} {eid} are not "
                           f"available. Trace ID: {trace_id}")
        payload["status"] = "placeholder"
        return as_json(payload)

    answer = await ai.chat(METADATA_SYSTEM, METADATA_USER.replace("<<CONTEXT>>", ai.entity_context(entity)))
    parsed = ai.parse_json_block(answer)
    fields = parsed.get("fields") if isinstance(parsed, dict) else parsed
    payload["suggestions"] = [f for f in (fields or []) if isinstance(f, dict) and f.get("key")]
    payload["status"] = "ok"
    return as_json(payload)


@mcp.tool()
async def apply_tag_suggestions(entity_type: str, id: int, tags: list[str]) -> str:
    """Persist a list of (suggested) tags to an eLabFTW experiment or item. WRITE OPERATION —
    requires the tag write scope."""
    require("write_tags")
    cleaned = validate_string_array(tags, "tags")
    if not cleaned:
        raise ElabFTWError("'tags' must contain at least one non-empty tag.")
    api = client()
    etype = normalize_entity_type(entity_type)
    eid = parse_id(id)
    await api.create(f"{etype}/{eid}/tags", {"tags": cleaned})
    after = await api.get_json(f"{etype}/{eid}", cacheable=False)
    current = normalize_tags((after or {}).get("tags")) if isinstance(after, dict) else []
    applied = match_tags(cleaned, current)
    return as_json({
        "entity_type": etype,
        "entity_id": eid,
        "tags_requested": cleaned,
        "tags_applied": applied,
        "tags_now": current,
        "status": "ok" if len(applied) == len(cleaned) else "partial",
    })


@mcp.tool()
async def add_ai_review_comment(entity_type: str, id: int, comment: str,
                                trace_id: Optional[str] = None) -> str:
    """Attach an AI-generated review comment to an eLabFTW experiment or item. The provenance header
    ('[AI-GENERATED] | <timestamp> | trace: …') is added automatically. WRITE OPERATION — requires
    the comment write scope."""
    require("write_comments")
    if not comment or not str(comment).strip():
        raise ElabFTWError("'comment' must be a non-empty string.")
    config = get_config()
    api = client()
    etype = normalize_entity_type(entity_type)
    eid = parse_id(id)
    trace = trace_id or ai.new_trace_id()

    prefix = config.provenance.ai_header_prefix or "[AI-GENERATED]"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    trace_part = f" | trace: {trace}" if trace else ""
    header = f"{prefix} | {timestamp}{trace_part}\n\n" if config.provenance.require_ai_header else ""
    new_id, _ = await api.create(f"{etype}/{eid}/comments", {"comment": f"{header}{comment}"})

    after = await safe_fetch(api.list_json(f"{etype}/{eid}/comments"))
    verified = False
    if isinstance(after, list):
        verified = any(isinstance(c, dict) and str(c.get("comment", "")).endswith(str(comment))
                       and prefix in str(c.get("comment", "")) for c in after)
    return as_json({
        "entity_type": etype,
        "entity_id": eid,
        "comment_id": new_id,
        "trace_id": trace,
        "header": header.strip(),
        "verified": verified,
        "status": "ok",
    })
