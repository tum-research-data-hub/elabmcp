"""Write tools: create, update bodies/fields/metadata, image upload.

Parity with elabrmcp (same safety rails: append-by-default body updates, explicit
overwrite confirmation, provenance blocks for traced AI writes).
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Optional

from ..client import normalize_entity_type, parse_id
from ..config import get_config
from ..errors import ElabFTWError
from ..instance import mcp
from ..policy import require
from ..provenance import compose_updated_body, coerce_content_type, merge_metadata_provenance
from ..responses import as_json, match_tags, normalize_tags
from ..validation import one_of, require as require_condition, validate_string_array
from ._common import client, team_caps

IMAGE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
SUPPORTED_IMAGE_MIME = set(IMAGE_MIME_BY_EXT.values())

CREATE_HINT = ("Before calling this tool, discover valid IDs with list_experiment_categories / "
               "list_item_categories and list_experiment_statuses / list_item_statuses — do not put "
               "IDs into the title or body.")


async def _apply_tags(api, entity_type: str, entity_id: int, tags: Any) -> list[str]:
    """Write tags and return what the server actually stored (it reports them as "a|b")."""
    cleaned = validate_string_array(tags, "tags") or []
    if cleaned:
        await api.create(f"{entity_type}/{entity_id}/tags", {"tags": cleaned})
        after = await api.get_json(f"{entity_type}/{entity_id}", cacheable=False)
        stored = normalize_tags((after or {}).get("tags")) if isinstance(after, dict) else []
        matches = match_tags(cleaned, stored)
        return matches or ([] if stored else cleaned)
    return []


async def _force_template_policy(api) -> bool:
    """Team policy force_exp_tpl: plain experiment creation is blocked."""
    try:
        team = await team_caps(api)
        return bool(int(team.get("force_exp_tpl") or 0))
    except Exception:  # noqa: BLE001
        return False


@mcp.tool()
async def create_experiment(title: str = "Untitled", body: Optional[str] = None,
                            category_id: Optional[int] = None, status_id: Optional[int] = None,
                            tags: Optional[list[str]] = None) -> str:
    """Create a new eLabFTW experiment. WRITE OPERATION — requires the create write scope.
    Discover valid category/status IDs first; never embed IDs in the title or body. If the team
    enforces experiment templates (force_exp_tpl), this tool is refused and
    create_experiment_from_template must be used. Returns the new id."""
    require("write_create")
    api = client()
    if await _force_template_policy(api):
        raise ElabFTWError(
            "This team enforces experiment templates (force_exp_tpl=1): plain experiment creation "
            "is blocked. Use create_experiment_from_template instead."
        )
    payload: dict[str, Any] = {"title": title or "Untitled"}
    if body:
        payload["body"] = body
    if category_id is not None:
        payload["category"] = parse_id(category_id, "category_id")
    if status_id is not None:
        payload["status"] = parse_id(status_id, "status_id")
    new_id, created = await api.create("experiments", payload)
    if new_id is None:
        raise ElabFTWError("eLabFTW accepted the create call but returned no Location id.")
    assigned_tags = await _apply_tags(api, "experiments", new_id, tags)
    return as_json({"id": new_id, "title": payload["title"], "category_id": category_id,
                    "status_id": status_id, "tags": assigned_tags, "status": "created"})


@mcp.tool()
async def create_item(title: str = "Untitled", body: Optional[str] = None,
                      category_id: Optional[int] = None, status_id: Optional[int] = None,
                      tags: Optional[list[str]] = None) -> str:
    """Create a new eLabFTW database item (resource). WRITE OPERATION — requires the create write
    scope. Items belong to item categories; discover valid IDs first and never embed IDs in the
    title or body. Returns the new id."""
    require("write_create")
    api = client()
    payload: dict[str, Any] = {"title": title or "Untitled"}
    if body:
        payload["body"] = body
    if category_id is not None:
        payload["category"] = parse_id(category_id, "category_id")
    if status_id is not None:
        payload["status"] = parse_id(status_id, "status_id")
    new_id, created = await api.create("items", payload)
    if new_id is None:
        raise ElabFTWError("eLabFTW accepted the create call but returned no Location id.")
    assigned_tags = await _apply_tags(api, "items", new_id, tags)
    return as_json({"id": new_id, "title": payload["title"], "category_id": category_id,
                    "status_id": status_id, "tags": assigned_tags, "status": "created"})


@mcp.tool()
async def create_experiment_from_template(template_id: int, title: Optional[str] = None,
                                          category_id: Optional[int] = None,
                                          status_id: Optional[int] = None,
                                          tags: Optional[list[str]] = None,
                                          include_steps: bool = True, include_body: bool = True,
                                          trace_id: Optional[str] = None) -> str:
    """Create a new eLabFTW experiment from an experiment template. WRITE OPERATION — requires the
    create write scope. Use list_experiment_templates to find template IDs. include_steps/include_body
    decide whether the template's steps and body are copied. Returns the new id."""
    require("write_create")
    api = client()
    tid = parse_id(template_id, "template_id")
    template = await api.get_json(f"experiments_templates/{tid}")
    if not isinstance(template, dict):
        raise ElabFTWError(f"Template {tid} could not be read.")

    payload: dict[str, Any] = {"title": title or template.get("title") or "Untitled"}
    if category_id is not None:
        payload["category"] = parse_id(category_id, "category_id")
    if status_id is not None:
        payload["status"] = parse_id(status_id, "status_id")

    server_side_copy = include_steps and include_body
    if server_side_copy:
        # Let eLabFTW copy body and steps from the template (order preserved).
        payload["template"] = tid
    else:
        if include_body and template.get("body"):
            payload["body"] = template["body"]
        if include_body and template.get("content_type") is not None:
            payload["content_type"] = template["content_type"]

    if trace_id:
        payload["metadata"] = merge_metadata_provenance(template.get("metadata"), "create_experiment_from_template", trace_id)

    new_id, created = await api.create("experiments", payload)
    if new_id is None:
        raise ElabFTWError("eLabFTW accepted the create call but returned no Location id.")

    copied_steps = 0
    if include_steps and not server_side_copy:
        steps = await api.list_json(f"experiments_templates/{tid}/steps")
        for step in steps:
            if not isinstance(step, dict) or not step.get("body"):
                continue
            step_payload: dict[str, Any] = {"body": step["body"]}
            if step.get("deadline"):
                step_payload["deadline"] = step["deadline"]
            if step.get("deadline_notif") is not None:
                step_payload["deadline_notif"] = step["deadline_notif"]
            await api.create(f"experiments/{new_id}/steps", step_payload)
            copied_steps += 1

    assigned_tags = await _apply_tags(api, "experiments", new_id, tags)
    return as_json({
        "id": new_id,
        "template_id": tid,
        "title": payload["title"],
        "copied_steps": copied_steps if not server_side_copy else "server-side (template)",
        "include_body": include_body,
        "tags": assigned_tags,
        "status": "created",
    })


async def _update_body(entity_type: str, entity_id: Any, text: str, mode: str,
                       confirm_overwrite: bool, trace_id: Optional[str]) -> str:
    require("write_update")
    config = get_config()
    mode = one_of(mode or "append", ("append", "overwrite"), "mode", default="append") or "append"
    if not text:
        raise ElabFTWError("'text' is required and must not be empty.")
    if mode == "overwrite":
        if not config.features.allow_body_overwrite:
            raise ElabFTWError(
                "mode='overwrite' is disabled by configuration (features.allow_body_overwrite=false).")
        if not confirm_overwrite:
            raise ElabFTWError(
                "mode='overwrite' requires confirm_overwrite=true — it replaces the existing body.")
    api = client()
    eid = parse_id(entity_id)
    existing = await api.get_json(f"{entity_type}/{eid}")
    if not isinstance(existing, dict):
        raise ElabFTWError(f"{entity_type}/{eid} could not be read.")
    content_type = coerce_content_type(existing.get("content_type"))
    new_body = compose_updated_body(existing.get("body"), text, mode, content_type)

    payload: dict[str, Any] = {"body": new_body, "content_type": content_type}
    if trace_id:
        payload["metadata"] = merge_metadata_provenance(existing.get("metadata"), f"update_{entity_type}_body", trace_id)
    await api.patch(f"{entity_type}/{eid}", payload)

    # Read back: an update that silently did nothing must not look like success.
    after = await api.get_json(f"{entity_type}/{eid}", cacheable=False)
    body_after = (after or {}).get("body") if isinstance(after, dict) else None
    preserved = bool(body_after) and (new_body in str(body_after) or str(body_after) == new_body)
    return as_json({
        "entity_type": entity_type,
        "id": eid,
        "mode": mode,
        "content_type_used": content_type,
        "body_length_before": len(str(existing.get("body") or "")),
        "body_length_after": len(str(body_after or "")),
        "body_change_verified": preserved,
        "status": "updated",
    })


@mcp.tool()
async def update_experiment_body(id: int, text: str, mode: str = "append",
                                 confirm_overwrite: bool = False,
                                 trace_id: Optional[str] = None) -> str:
    """Update the body of an existing eLabFTW experiment. WRITE OPERATION — requires the update write
    scope. The existing content_type (HTML=1 / Markdown=2) is preserved. mode='append' (default) keeps
    the original body byte-for-byte and appends the new text; mode='overwrite' replaces it and needs
    both the config flag allow_body_overwrite and confirm_overwrite=true. The result reports a
    read-back verification of the change."""
    return await _update_body("experiments", id, text, mode, confirm_overwrite, trace_id)


@mcp.tool()
async def update_item_body(id: int, text: str, mode: str = "append",
                           confirm_overwrite: bool = False,
                           trace_id: Optional[str] = None) -> str:
    """Update the body of an existing eLabFTW database item. WRITE OPERATION — requires the update
    write scope. mode='append' (default) preserves the original body and appends; mode='overwrite'
    replaces it and needs allow_body_overwrite plus confirm_overwrite=true."""
    return await _update_body("items", id, text, mode, confirm_overwrite, trace_id)


def _parse_json_argument(value: Any, param: str) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except ValueError as exc:
        raise ElabFTWError(f"'{param}' must be valid JSON: {exc}") from exc


def _build_extra_fields(fields: Any) -> dict[str, Any]:
    """Turn the tool's flat field list into eLabFTW 6.x extra_fields (keyed by field name)."""
    if not isinstance(fields, list):
        raise ElabFTWError("'fields' must decode to a JSON array of field objects.")
    built: dict[str, Any] = {}
    for entry in fields:
        if not isinstance(entry, dict):
            raise ElabFTWError("Each entry in 'fields' must be a JSON object.")
        key = entry.get("key") or entry.get("name")
        if not key:
            raise ElabFTWError("Each field object needs a 'key'.")
        field: dict[str, Any] = {"type": entry.get("type") or "text"}
        if "values" in entry and entry["values"] is not None:
            field["value"] = entry["values"]
            field["allow_multi_values"] = True
        elif "value" in entry:
            field["value"] = entry["value"]
        for extra_key in ("allow_multi_values", "group_id", "description", "options"):
            if entry.get(extra_key) is not None:
                field[extra_key] = entry[extra_key]
        built[str(key)] = field
    return built


@mcp.tool()
async def update_entity_metadata(entity_type: str, id: int, fields: str,
                                 groups: Optional[str] = None, mode: str = "merge",
                                 trace_id: Optional[str] = None) -> str:
    """Update the metadata (custom fields) of an eLabFTW experiment, item, experiment template or
    item type. WRITE OPERATION — requires the update write scope. 'fields' is a JSON string with an
    array of field objects ({key, value | values, type, group_id, allow_multi_values, description});
    'groups' optionally defines extra field groups ([{id, name}]). mode='merge' (default) keeps the
    existing metadata, 'overwrite' rebuilds it from the supplied fields. Returns the resulting field
    names and a read-back verification."""
    require("write_metadata")
    api = client()
    etype = normalize_entity_type(entity_type)
    eid = parse_id(id)
    mode = one_of(mode or "merge", ("merge", "overwrite"), "mode", default="merge") or "merge"

    parsed_fields = _parse_json_argument(fields, "fields")
    new_fields = _build_extra_fields(parsed_fields)
    parsed_groups = _parse_json_argument(groups, "groups") if groups else None

    existing = await api.get_json(f"{etype}/{eid}")
    if not isinstance(existing, dict):
        raise ElabFTWError(f"{etype}/{eid} could not be read.")
    metadata = existing.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    extra_fields = dict(metadata.get("extra_fields") or {}) if mode == "merge" else {}
    extra_fields.update(new_fields)
    metadata["extra_fields"] = extra_fields

    if parsed_groups is not None:
        elabftw_section = dict(metadata.get("elabftw") or {})
        elabftw_section["extra_fields_groups"] = parsed_groups
        metadata["elabftw"] = elabftw_section
    if trace_id:
        metadata = merge_metadata_provenance(metadata, "update_entity_metadata", trace_id)

    # eLabFTW stores metadata in a JSON column and expects the JSON *string*:
    # sending a nested object makes the API write the literal "Array" and fail with
    # a MySQL 3140 ("Invalid JSON text ... position 0").
    await api.patch(f"{etype}/{eid}", {"metadata": json.dumps(metadata, ensure_ascii=False)})
    after = await api.get_json(f"{etype}/{eid}", cacheable=False)
    after_meta = (after or {}).get("metadata") if isinstance(after, dict) else None
    if isinstance(after_meta, str):
        try:
            after_meta = json.loads(after_meta)
        except ValueError:
            after_meta = {}
    after_fields = (after_meta or {}).get("extra_fields") or {}
    applied = [k for k in new_fields if k in after_fields]
    return as_json({
        "entity_type": etype,
        "id": eid,
        "mode": mode,
        "fields_applied": applied,
        "fields_missing_after_write": [k for k in new_fields if k not in after_fields],
        "metadata_field_count": len(after_fields),
        "status": "updated",
    })


@mcp.tool()
async def update_entity_fields(entity_type: str, id: int, title: Optional[str] = None,
                               status_id: Optional[int] = None, category_id: Optional[int] = None,
                               trace_id: Optional[str] = None) -> str:
    """Update top-level scalar fields (title, status, category) on an existing eLabFTW experiment or
    item. WRITE OPERATION — requires the update write scope. At least one of title, status_id,
    category_id must be given. Returns the applied fields and a read-back verification."""
    require("write_update")
    if title is None and status_id is None and category_id is None:
        raise ElabFTWError("Nothing to update: provide at least one of title, status_id, category_id.")
    api = client()
    etype = normalize_entity_type(entity_type)
    eid = parse_id(id)

    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if status_id is not None:
        payload["status"] = parse_id(status_id, "status_id")
    if category_id is not None:
        payload["category"] = parse_id(category_id, "category_id")
    if trace_id:
        existing = await api.get_json(f"{etype}/{eid}")
        payload["metadata"] = merge_metadata_provenance(
            (existing or {}).get("metadata") if isinstance(existing, dict) else None,
            "update_entity_fields", trace_id)

    updated = await api.patch(f"{etype}/{eid}", payload)
    after = updated if isinstance(updated, dict) else await api.get_json(f"{etype}/{eid}")
    verified = {}
    if isinstance(after, dict):
        if title is not None:
            verified["title"] = after.get("title")
        if status_id is not None:
            verified["status"] = after.get("status")
        if category_id is not None:
            verified["category"] = after.get("category")
    return as_json({"entity_type": etype, "id": eid, "applied": payload_projection(payload),
                    "read_back": verified, "status": "updated"})


def payload_projection(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k != "metadata"}


@mcp.tool()
async def upload_image_from_content(entity_type: str, id: int, filename: str,
                                    content_base64: str, mime_type: Optional[str] = None,
                                    comment: Optional[str] = None) -> str:
    """Upload a single image attachment to an eLabFTW experiment or item from base64-encoded content.
    WRITE OPERATION — requires the update write scope. Only image/png, image/jpeg, image/gif and
    image/tiff are accepted. Returns the new upload id and size."""
    require("write_update")
    api = client()
    etype = normalize_entity_type(entity_type)
    if etype not in ("experiments", "items", "items_types"):
        raise ElabFTWError("Images can only be attached to experiments, items or item types.")
    eid = parse_id(id)

    lower = (filename or "").lower()
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    if ext not in IMAGE_MIME_BY_EXT:
        raise ElabFTWError(
            f"Unsupported image filename {filename!r}: allowed extensions are "
            f"{', '.join(sorted(IMAGE_MIME_BY_EXT))}.")
    resolved_mime = (mime_type or IMAGE_MIME_BY_EXT[ext]).lower()
    if resolved_mime not in SUPPORTED_IMAGE_MIME:
        raise ElabFTWError(
            f"Unsupported mime_type {resolved_mime!r}: allowed are {', '.join(sorted(SUPPORTED_IMAGE_MIME))}.")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ElabFTWError(f"content_base64 is not valid base64: {exc}") from exc
    if not content:
        raise ElabFTWError("content_base64 decoded to an empty file.")
    magic = {
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"GIF87a": "image/gif",
        b"GIF89a": "image/gif",
        b"II*\x00": "image/tiff",
        b"MM\x00*": "image/tiff",
    }
    detected = next((mime for sig, mime in magic.items() if content.startswith(sig)), None)
    if detected is None:
        raise ElabFTWError("The decoded content does not look like a PNG/JPEG/GIF/TIFF image.")
    if detected != resolved_mime:
        raise ElabFTWError(
            f"Declared mime_type {resolved_mime} does not match the file content ({detected}).")

    new_id, body = await api.upload_file(etype, eid, filename, content, resolved_mime, comment=comment)
    return as_json({
        "entity_type": etype,
        "entity_id": eid,
        "upload_id": new_id,
        "filename": filename,
        "mime_type": resolved_mime,
        "filesize": len(content),
        "status": "uploaded",
    })
