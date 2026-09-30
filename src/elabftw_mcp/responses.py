"""Response shaping shared by the tools (parity with elabrmcp's envelopes)."""
from __future__ import annotations

import json
from typing import Any

MAX_TEXT = 60000
STRING_LIMIT = 2000  # long strings are shortened before we ever truncate the envelope


def _shorten_strings(value: Any, limit: int = STRING_LIMIT) -> Any:
    """Recursively shorten oversized string values so the JSON stays valid."""
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:limit]}… [+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {key: _shorten_strings(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_shorten_strings(item, limit) for item in value]
    return value


def as_json(payload: Any, *, indent: int = 2) -> str:
    """Render a tool result as JSON text (what the model reads).

    The result is *always* valid JSON: oversized payloads get their long string
    fields shortened first, and only if that is not enough the envelope is cut —
    which would break parsing, so it is reported explicitly instead.
    """
    text = json.dumps(payload, indent=indent, ensure_ascii=False, default=str)
    if len(text) <= MAX_TEXT:
        return text
    shortened = json.dumps(_shorten_strings(payload), indent=indent, ensure_ascii=False, default=str)
    if len(shortened) <= MAX_TEXT:
        return shortened
    return json.dumps({
        "truncated": True,
        "original_characters": len(text),
        "note": "Response too large even after shortening long text fields — narrow the query, "
                "use pagination, or fetch the entity by id.",
        "preview": shortened[: MAX_TEXT // 2],
    }, indent=indent, ensure_ascii=False)


def build_list_response(results: list[Any], *, limit: int, offset: int,
                        filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Same envelope as elabrmcp: paging + filters_applied + results."""
    count = len(results)
    return {
        "paging": {
            "limit": limit,
            "offset": offset,
            "returned_count": count,
            "has_more_note": (
                "The elabFTW API does not return total counts. If returned_count equals "
                f"limit ({limit}), there may be additional results — call again with "
                f"offset = {offset + count}."
            ),
        },
        "filters_applied": filters or {},
        "results": results,
    }


def normalize_tags(value: Any) -> Any:
    """eLabFTW returns tags either as a list or as a "a|b|c" string — always expose a list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [t for t in (part.strip() for part in value.split("|")) if t]
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            if isinstance(item, dict) and "tag" in item:
                out.append(item["tag"])
            elif isinstance(item, str):
                out.append(item)
            else:
                out.append(item)
        return out
    return value


def match_tags(requested: list[str], stored: Any) -> list[str]:
    """Which requested tags the server stored — eLabFTW canonicalises casing ("tga" -> "TGA")."""
    have = {str(tag).casefold(): tag for tag in normalize_tags(stored)}
    return [have[str(tag).casefold()] for tag in requested if str(tag).casefold() in have]


ENTITY_CORE_FIELDS = (
    "id", "title", "date", "category", "category_title", "status", "status_title",
    "tags", "canread", "canwrite", "rating", "custom_id", "state", "created_at",
)

# Fields that are derived duplicates and are dropped from every response; everything
# else is passed through, because the reference implementation returns whole rows and
# a trimmed projection would silently hide fields a client may rely on.
ENTITY_DROP_FIELDS = ("body_html",)


def compact_entity(entity: dict[str, Any], *, drop_body: bool = False) -> dict[str, Any]:
    """Pass a row through (minus derived duplicates), tags normalised to a list."""
    if not isinstance(entity, dict):
        return {"value": entity}
    out = {k: v for k, v in entity.items() if k not in ENTITY_DROP_FIELDS}
    if drop_body:
        out.pop("body", None)
    if "tags" in out:
        out["tags"] = normalize_tags(out["tags"])
    if "metadata" not in out and out.get("extra_fields"):
        out["metadata"] = out["extra_fields"]
    return out


def compact_list(items: list[Any]) -> list[Any]:
    return [compact_entity(i, drop_body=True) if isinstance(i, dict) else i for i in items]


def step_summary(step: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(step, dict):
        return {"value": step}
    return {k: v for k, v in step.items() if k in ("id", "body", "finished", "finished_time", "deadline",
                                                   "deadline_notif", "ordering", "created_at", "immutable")}


def steps_markdown(steps: list[dict[str, Any]]) -> str:
    lines = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        mark = "x" if step.get("finished") else " "
        body = str(step.get("body", "")).splitlines()[0][:160]
        deadline = f" (deadline {step['deadline']})" if step.get("deadline") else ""
        lines.append(f"- [{mark}] {body}{deadline}")
    return "\n".join(lines) or "(no steps)"


def upload_summary(upload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(upload, dict):
        return {"value": upload}
    return {k: v for k, v in upload.items()
            if k in ("id", "real_name", "long_name", "comment", "filesize", "created_at", "state", "type")}


def comment_summary(comment: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(comment, dict):
        return {"value": comment}
    return {k: v for k, v in comment.items()
            if k in ("id", "comment", "created_at", "modified_at", "userid", "fullname")}
