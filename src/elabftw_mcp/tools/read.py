"""Read tools: listing, fetching, team catalogues, connection/capability info.

Faithful port of the read half of elabrmcp (Marvin Luepke's R implementation):
same parameter names, same validation, same response envelopes.
"""
from __future__ import annotations

from typing import Any, Optional

from ..client import parse_id
from ..instance import mcp
from ..responses import (
    as_json, build_list_response, comment_summary, compact_entity, compact_list,
    step_summary, upload_summary,
)
from ..validation import (
    clamp_limit, normalize_ids_to_csv, normalize_offset, normalize_order, normalize_query,
    normalize_scope, normalize_sort, normalize_state, validate_string_array,
)
from ._common import client, resolve_team_id, safe_fetch, team_caps

LIST_FILTER_DOC = (
    "Search or list {plural}. Returns a structured response with paging metadata, applied filters, "
    "and result records. The first response may be only a SUBSET of all matching entries — check "
    "paging.returned_count vs paging.limit; if they are equal, call again with a higher offset to get "
    "the next page. ROUTING RULE: when you already have concrete IDs or values from prior tool outputs "
    "(category IDs, user IDs, status IDs), you MUST use the dedicated filter arguments — do NOT put "
    "those values into q. Always safe to call; never writes anything."
)
Q_DOC = ("Free-text content search only (title, body, elabid). Do NOT use for known category IDs, "
         "user IDs, status IDs, or tag values — use the dedicated filter arguments for those.")
LIMIT_DOC = "Maximum number of results to return per page (default 25, max 100)."
OFFSET_DOC = ("Pagination offset: number of results to skip (default 0). Increase by returned_count "
              "to get the next page.")
STATE_DOC = ("Comma-separated lifecycle state filter. Allowed values: '1' (normal), '2' (archived), "
             "'3' (deleted). Example: '2,3'.")
ORDER_DOC = ("Sort field. Allowed values: cat, comment, customid, date, id, lastchange, rating, "
             "status, title, user. Example: 'lastchange'.")
SORT_DOC = "Sort direction. Allowed values: 'asc' (ascending) or 'desc' (descending). Example: 'desc'."
TAGS_DOC = "Use for exact tag filtering when tag names are known. Example: tags = ['PCR', '2026']."


async def _list_entities(entity_type: str, *, q=None, extended=None, limit=25, offset=0,
                         category_ids=None, status_ids=None, owner_ids=None, tags=None,
                         scope=None, state=None, order=None, sort=None) -> str:
    api = client()
    params = {
        "q": normalize_query(q),
        "extended": normalize_query(extended),
        "limit": clamp_limit(limit),
        "offset": normalize_offset(offset),
        "category": normalize_ids_to_csv(category_ids),
        "status": normalize_ids_to_csv(status_ids),
        "owner": normalize_ids_to_csv(owner_ids),
        "tags[]": validate_string_array(tags),
        "scope": normalize_scope(scope),
        "state": normalize_state(state),
        "order": normalize_order(order),
        "sort": normalize_sort(sort),
    }
    raw = await api.list_json(entity_type, params=params)
    filters = {
        "q": normalize_query(q), "category_ids": category_ids or [], "status_ids": status_ids or [],
        "owner_ids": owner_ids or [], "tags": tags or [], "scope": scope, "state": state,
        "order": order, "sort": sort,
    }
    if extended:
        filters["extended"] = extended
    return as_json(build_list_response(compact_list(raw), limit=params["limit"],
                                       offset=params["offset"], filters=filters))


async def _get_entity(entity_type: str, entity_id: Any, *, enrich: bool = True) -> dict[str, Any]:
    api = client()
    eid = parse_id(entity_id, "id")
    entity = await api.get_json(f"{entity_type}/{eid}")
    if isinstance(entity, dict):
        entity = {k: v for k, v in entity.items() if k != "body_html"}
    key = {"experiments": "experiment", "items": "item",
           "experiments_templates": "experiment_template", "items_types": "item_type"}.get(
        entity_type, entity_type.rstrip("s"))
    payload: dict[str, Any] = {key: entity}
    if enrich:
        comments = await safe_fetch(api.list_json(f"{entity_type}/{eid}/comments"))
        uploads = await safe_fetch(api.list_json(f"{entity_type}/{eid}/uploads"))
        steps = await safe_fetch(api.list_json(f"{entity_type}/{eid}/steps"))
        outgoing = await safe_fetch(api.list_json(f"{entity_type}/{eid}/experiments_links"))
        incoming = await safe_fetch(api.list_json(f"{entity_type}/{eid}/items_links"))
        payload["comments"] = [comment_summary(c) for c in comments] if isinstance(comments, list) else comments
        payload["uploads"] = [upload_summary(u) for u in uploads] if isinstance(uploads, list) else uploads
        payload["steps"] = [step_summary(s) for s in steps] if isinstance(steps, list) else steps
        payload["links"] = {
            "experiments_links": [compact_entity(l) for l in outgoing] if isinstance(outgoing, list) else outgoing,
            "items_links": [compact_entity(l) for l in incoming] if isinstance(incoming, list) else incoming,
        }
    return payload


@mcp.tool()
async def list_experiments(q: Optional[str] = None, limit: int = 25, offset: int = 0,
                           category_ids: Optional[list[int]] = None,
                           status_ids: Optional[list[int]] = None,
                           owner_ids: Optional[list[int]] = None, tags: Optional[list[str]] = None,
                           scope: Optional[str] = None, state: Optional[str] = None,
                           order: Optional[str] = None, sort: Optional[str] = None) -> str:
    """Search or list elabFTW experiments. Returns a structured response with paging metadata,
    applied filters, and result records. Check paging.returned_count vs paging.limit to detect
    further pages. Use the dedicated filter arguments for known category/user/status IDs or tags
    instead of putting them into q. Always safe to call; never writes anything."""
    return await _list_entities("experiments", q=q, limit=limit, offset=offset,
                                category_ids=category_ids, status_ids=status_ids,
                                owner_ids=owner_ids, tags=tags, scope=scope, state=state,
                                order=order, sort=sort)


@mcp.tool()
async def list_items(q: Optional[str] = None, limit: int = 25, offset: int = 0,
                     category_ids: Optional[list[int]] = None,
                     status_ids: Optional[list[int]] = None,
                     owner_ids: Optional[list[int]] = None, tags: Optional[list[str]] = None,
                     scope: Optional[str] = None, state: Optional[str] = None,
                     order: Optional[str] = None, sort: Optional[str] = None) -> str:
    """Search or list elabFTW database items (resources). Returns a structured response with paging
    metadata, applied filters, and result records. Check paging.returned_count vs paging.limit to
    detect further pages. Use the dedicated filter arguments for known IDs or tags instead of q.
    Always safe to call; never writes anything."""
    return await _list_entities("items", q=q, limit=limit, offset=offset, category_ids=category_ids,
                                status_ids=status_ids, owner_ids=owner_ids, tags=tags, scope=scope,
                                state=state, order=order, sort=sort)


async def _list_templates(entity_type: str, *, limit=25, offset=0, q=None, tags=None,
                          state=None, order=None, sort=None) -> str:
    api = client()
    params = {
        "limit": clamp_limit(limit),
        "offset": normalize_offset(offset),
        "q": normalize_query(q),
        "tags[]": validate_string_array(tags),
        "state": normalize_state(state),
        "order": normalize_order(order),
        "sort": normalize_sort(sort),
    }
    raw = await api.list_json(entity_type, params=params)
    filters = {"q": normalize_query(q), "tags": tags or [], "state": state, "order": order, "sort": sort}
    return as_json(build_list_response(compact_list(raw), limit=params["limit"],
                                       offset=params["offset"], filters=filters))


@mcp.tool()
async def list_experiment_templates(limit: int = 25, offset: int = 0, q: Optional[str] = None,
                                    tags: Optional[list[str]] = None, state: Optional[str] = None,
                                    order: Optional[str] = None, sort: Optional[str] = None) -> str:
    """List elabFTW experiment templates (/experiments_templates). Returns a structured response with
    paging metadata and result records. Templates are reusable pre-filled starting points for new
    experiments and are DIFFERENT from item types (use list_item_types for those). Use
    get_experiment_template for a single template and create_experiment_from_template to use one.
    Always safe to call; never writes anything."""
    return await _list_templates("experiments_templates", limit=limit, offset=offset, q=q, tags=tags,
                                 state=state, order=order, sort=sort)


@mcp.tool()
async def list_item_types(limit: int = 25, offset: int = 0, q: Optional[str] = None,
                          tags: Optional[list[str]] = None, state: Optional[str] = None,
                          order: Optional[str] = None, sort: Optional[str] = None) -> str:
    """List elabFTW item types (/items_types), also called resource categories. They define the
    category structure and default content for database items and are DIFFERENT from experiment
    templates (use list_experiment_templates). Use get_item_type for a single one. Always safe to
    call; never writes anything."""
    return await _list_templates("items_types", limit=limit, offset=offset, q=q, tags=tags,
                                 state=state, order=order, sort=sort)


@mcp.tool()
async def get_experiment(id: int) -> str:
    """Fetch a single elabFTW experiment by its numeric ID. Returns the full experiment record
    including body, metadata, tags, comments, links and uploads. Always safe to call; never
    writes anything."""
    return as_json(await _get_entity("experiments", id))


@mcp.tool()
async def get_item(id: int) -> str:
    """Fetch a single elabFTW database item (resource) by its numeric ID. Returns the item with
    metadata, tags, comments, links and uploads. Always safe to call; never writes anything."""
    return as_json(await _get_entity("items", id))


@mcp.tool()
async def get_experiment_template(id: int) -> str:
    """Fetch a single elabFTW experiment template by its numeric ID. Returns the full template record
    including body, metadata and steps. Use list_experiment_templates to discover templates and
    create_experiment_from_template to create an experiment from one. Always safe to call; never
    writes anything."""
    return as_json(await _get_entity("experiments_templates", id))


@mcp.tool()
async def get_item_type(id: int) -> str:
    """Fetch a single elabFTW item type by its numeric ID. Returns the full item type record including
    body, metadata and steps. Use list_item_types to discover item types. Always safe to call; never
    writes anything."""
    return as_json(await _get_entity("items_types", id))


async def _team_catalogue(endpoint: str, team_id: Any) -> str:
    api = client()
    resolved = await resolve_team_id(api, team_id)
    items = await api.list_json(f"teams/{resolved}/{endpoint}")
    return as_json({"team_id": resolved, "count": len(items), "results": items})


@mcp.tool()
async def list_experiment_categories(team_id: str = "current") -> str:
    """List all experiment categories for the active elabFTW team. Specify team_id='current'
    (default) or a numeric team ID. Returns id, title, color and is_default per category — use those
    ids when filtering list_experiments by category. Always safe to call; never writes anything."""
    return await _team_catalogue("experiments_categories", team_id)


@mcp.tool()
async def list_experiment_statuses(team_id: str = "current") -> str:
    """List all experiment statuses for the active eLabFTW team. Specify team_id='current' (default)
    or a numeric team ID. Read-only."""
    return await _team_catalogue("experiments_status", team_id)


@mcp.tool()
async def list_item_categories(team_id: str = "current") -> str:
    """List all item categories (resource categories) for the active eLabFTW team. Specify
    team_id='current' (default) or a numeric team ID. Use the returned ids when filtering list_items
    by category. Always safe to call; never writes anything."""
    return await _team_catalogue("resources_categories", team_id)


@mcp.tool()
async def list_item_statuses(team_id: str = "current") -> str:
    """List all item statuses (resource statuses) for the active eLabFTW team. Specify
    team_id='current' (default) or a numeric team ID. Read-only."""
    return await _team_catalogue("items_status", team_id)


@mcp.tool()
async def get_connection_info() -> str:
    """Return connection and session details for the active server: eLabFTW base URL, authenticated
    user, active team, effective server mode (read-only vs write-enabled), enabled tool groups, and a
    category catalogue (experiments and items) for the active team. Always safe to call; never writes
    anything."""
    from ..config import get_config
    from ..policy import current_profile, effective_flags

    api = client()
    config = get_config()
    me = await safe_fetch(api.get_json("users/me", cacheable=True))
    team_id = (me or {}).get("team") if isinstance(me, dict) else None
    categories: dict[str, Any] = {}
    for label, endpoint in (("experiments", "experiments_categories"), ("items", "resources_categories")):
        if team_id:
            categories[label] = await safe_fetch(api.list_json(f"teams/{team_id}/{endpoint}"))
        else:
            categories[label] = {"unavailable": "no active team"}
    flags = effective_flags()
    enabled_groups = sorted(k[len("write_"):] for k, v in flags.items() if v)
    return as_json({
        "base_url": api.base_url,
        "user": {
            "userid": (me or {}).get("userid") if isinstance(me, dict) else None,
            "fullname": (me or {}).get("fullname") if isinstance(me, dict) else None,
            "team": team_id,
        } if isinstance(me, dict) else me,
        "server_mode": "write_enabled" if flags.get("write_create") or flags.get("write_update") else "readonly",
        "write_profile": current_profile(),
        "enabled_write_groups": enabled_groups,
        "ai_features": {
            "review": config.features.ai_review,
            "tag_suggestions": config.features.tag_suggestions,
            "metadata_suggestions": config.features.metadata_suggestions,
            "ai_available": config.ai_available,
        },
        "scope_context": "team-scoped results (elabFTW API default)",
        "categories": categories,
    })


@mcp.tool()
async def get_current_user_capabilities() -> str:
    """Return a sanitized subset of the current user's capabilities and team roles. Calls /users/me
    and returns only the fields relevant for capability detection: userid, fullname, current team id,
    team memberships (id, name, is_admin, is_owner, is_archived), is_sysadmin,
    can_manage_compounds and can_manage_inventory_locations. Sensitive fields (email, API keys,
    signatures, preferences) are deliberately omitted."""
    api = client()
    me = await api.get_json("users/me", cacheable=True)
    caps = await team_caps(api)
    if not isinstance(me, dict):
        return as_json({"unavailable": "unexpected /users/me response"})
    teams = [
        {k: t.get(k) for k in ("id", "name", "is_admin", "is_owner", "is_archived")}
        for t in (me.get("teams") or []) if isinstance(t, dict)
    ]
    return as_json({
        "userid": me.get("userid"),
        "fullname": me.get("fullname"),
        "current_team": me.get("team"),
        "teams": teams,
        "is_sysadmin": me.get("is_sysadmin"),
        "can_manage_compounds": int(caps.get("can_manage_compounds") or 0),
        "can_manage_inventory_locations": int(caps.get("can_manage_inventory_locations") or 0),
        "team_caps": caps,
    })


@mcp.tool()
async def refresh_team_caps() -> str:
    """Refresh and return the cached team capability flags for the current session. Fetches the
    team record, sanitizes the capability fields used for policy checks, updates the cache and
    returns the sanitized team_caps object. Does not write to eLabFTW resources."""
    api = client()
    caps = await team_caps(api, refresh=True)
    return as_json({"team_caps": caps})
