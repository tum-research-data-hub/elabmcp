"""Link tools: inspect, create, remove and traverse directed links.

eLabFTW's link routes only exist for experiments and items
(``/{entity_type}/{id}/experiments_links`` and ``.../items_links``), so templates
and item types are rejected with a clear message.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..client import Client, normalize_entity_type, parse_id
from ..config import get_config
from ..errors import ElabFTWError
from ..instance import mcp
from ..policy import require
from ..responses import as_json, compact_entity
from ..validation import (
    clamp_limit, normalize_ids_to_csv, normalize_offset, normalize_query, normalize_scope,
    normalize_state, validate_string_array,
)
from ._common import client

LINKABLE = ("experiments", "items")


def _linkable(entity_type: str, param: str) -> str:
    etype = normalize_entity_type(entity_type)
    if etype not in LINKABLE:
        raise ElabFTWError(
            f"{param}={entity_type!r} cannot be linked: the eLabFTW API only supports links "
            f"between experiments and items."
        )
    return etype


def _segment(target_type: str) -> str:
    return "experiments_links" if target_type == "experiments" else "items_links"


def _edge(entry: dict[str, Any], *, direction: str, from_type: str, from_id: Any,
          to_type: str, to_id: Any, include_raw: bool) -> dict[str, Any]:
    link_state = entry.get("link_state") if isinstance(entry, dict) else None
    edge = {
        "from": {"type": from_type, "id": from_id},
        "to": {"type": to_type, "id": to_id},
        "direction": direction,
        "active": True if link_state in (None, 1) else False,
        "link_state": link_state,
        "target": compact_entity(entry, drop_body=True) if isinstance(entry, dict) else entry,
    }
    if include_raw and isinstance(entry, dict):
        edge["raw"] = entry
    return edge


async def _collect_edges(api: Client, entity_type: str, entity_id: int, *,
                         include_outgoing: bool, include_incoming: bool,
                         include_raw: bool) -> list[dict[str, Any]]:
    """Edges of one entity.

    eLabFTW stores a link on exactly one side of the pair, so the two directions
    need two different API calls (verified against 6.0.1):

    * outgoing — the links this entity owns: ``GET /{type}/{id}/experiments_links``
      and ``/items_links``; rows carry ``entityid`` (the other side) plus ``type``.
    * incoming — entities that own a link to this one:
      ``GET /{other_type}?related=<id>&related_origin=<type>``.
    """
    edges: list[dict[str, Any]] = []
    target_types = ("experiments", "items")

    if include_outgoing:
        for target_type in target_types:
            rows = await api.list_json(f"{entity_type}/{entity_id}/{_segment(target_type)}")
            for entry in rows if isinstance(rows, list) else []:
                if not isinstance(entry, dict):
                    continue
                other_id = entry.get("entityid", entry.get("id"))
                other_type = entry.get("type") or target_type
                edges.append(_edge(entry, direction="outgoing", from_type=entity_type,
                                   from_id=entity_id, to_type=other_type,
                                   to_id=other_id, include_raw=include_raw))

    if include_incoming:
        other_type = "items" if entity_type == "experiments" else "experiments"
        rows = await api.list_json(other_type, params={"related": entity_id,
                                                       "related_origin": entity_type,
                                                       "limit": 200})
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict):
                continue
            edges.append(_edge(entry, direction="incoming", from_type=other_type,
                               from_id=entry.get("id"), to_type=entity_type,
                               to_id=entity_id, include_raw=include_raw))
    return edges


@mcp.tool()
async def get_entity_links(entity_type: str, id: int, include_outgoing: bool = True,
                           include_incoming: bool = True, include_raw: bool = False) -> str:
    """Fetch directed link edges of a single eLabFTW experiment or item: outgoing links
    (items_links / experiments_links) and incoming ones (related_items_links /
    related_experiments_links). Each edge carries from {type,id}, to {type,id} and a direction
    label. Read-only."""
    api = client()
    etype = normalize_entity_type(entity_type)
    eid = parse_id(id)
    if not include_outgoing and not include_incoming:
        raise ElabFTWError("Nothing to fetch: set include_outgoing and/or include_incoming to true.")
    edges = await _collect_edges(api, etype, eid, include_outgoing=include_outgoing,
                                include_incoming=include_incoming, include_raw=include_raw)
    outgoing = [e for e in edges if e["direction"] == "outgoing"]
    incoming = [e for e in edges if e["direction"] == "incoming"]
    return as_json({
        "entity": {"type": etype, "id": eid},
        "counts": {"outgoing": len(outgoing), "incoming": len(incoming), "total": len(edges)},
        "edges": edges,
    })


async def _existing_target_ids(api: Client, entity_type: str, entity_id: int,
                               target_type: str) -> set[Any]:
    """IDs this entity already links to.

    The link rows of eLabFTW carry the other side as ``entityid`` (an entity
    payload has no link arrays at all), so the subresource is authoritative.
    """
    rows = await api.list_json(f"{entity_type}/{entity_id}/{_segment(target_type)}")
    ids = set()
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            value = row.get("entityid", row.get("id"))
            if value is not None:
                ids.add(value)
    return ids


async def _link_once(api: Client, from_type: str, from_id: int, to_type: str, to_id: int, *,
                     dry_run: bool, action: str) -> dict[str, Any]:
    """Create or delete one directed link, idempotently."""
    segment = _segment(to_type)
    path = f"{from_type}/{from_id}/{segment}/{to_id}"
    existing = await _existing_target_ids(api, from_type, from_id, to_type)

    if action == "create":
        if to_id in existing:
            return {"from": {"type": from_type, "id": from_id}, "to": {"type": to_type, "id": to_id},
                    "result": "already_linked", "dry_run": dry_run}
        if dry_run:
            return {"from": {"type": from_type, "id": from_id}, "to": {"type": to_type, "id": to_id},
                    "result": "would_link", "dry_run": True}
        await api.create(path, {"action": "create"})
        verified = to_id in await _existing_target_ids(api, from_type, from_id, to_type)
        return {"from": {"type": from_type, "id": from_id}, "to": {"type": to_type, "id": to_id},
                "result": "linked" if verified else "link_not_verified", "dry_run": False,
                "verified": verified}

    if to_id not in existing:
        return {"from": {"type": from_type, "id": from_id}, "to": {"type": to_type, "id": to_id},
                "result": "already_absent", "dry_run": dry_run}
    if dry_run:
        return {"from": {"type": from_type, "id": from_id}, "to": {"type": to_type, "id": to_id},
                "result": "would_unlink", "dry_run": True}
    await api.delete(path)
    verified = to_id not in await _existing_target_ids(api, from_type, from_id, to_type)
    return {"from": {"type": from_type, "id": from_id}, "to": {"type": to_type, "id": to_id},
            "result": "unlinked" if verified else "unlink_not_verified", "dry_run": False,
            "verified": verified}


@mcp.tool()
async def ensure_link(from_type: str, from_id: int, to_type: str, to_id: int,
                      dry_run: bool = True, trace_id: Optional[str] = None) -> str:
    """Idempotently create a directed link between an experiment and an item. WRITE OPERATION when
    dry_run=false — requires the link write scope. dry_run=true (default) only reports what would
    happen. Returns result: linked | already_linked | would_link (verified after writing)."""
    ftype = _linkable(from_type, "from_type")
    ttype = _linkable(to_type, "to_type")
    if dry_run:
        api = client()
        return as_json(await _link_once(api, ftype, parse_id(from_id), ttype, parse_id(to_id),
                                        dry_run=True, action="create"))
    require("write_links")
    api = client()
    result = await _link_once(api, ftype, parse_id(from_id), ttype, parse_id(to_id),
                              dry_run=False, action="create")
    result["trace_id"] = trace_id
    return as_json(result)


@mcp.tool()
async def delete_link(from_type: str, from_id: int, to_type: str, to_id: int,
                      dry_run: bool = True, trace_id: Optional[str] = None) -> str:
    """Idempotently remove a directed link between an experiment and an item. WRITE OPERATION when
    dry_run=false — requires the link write scope. dry_run=true (default) only reports. Returns
    result: unlinked | already_absent | would_unlink."""
    ftype = _linkable(from_type, "from_type")
    ttype = _linkable(to_type, "to_type")
    if dry_run:
        api = client()
        return as_json(await _link_once(api, ftype, parse_id(from_id), ttype, parse_id(to_id),
                                        dry_run=True, action="delete"))
    require("write_links")
    api = client()
    result = await _link_once(api, ftype, parse_id(from_id), ttype, parse_id(to_id),
                              dry_run=False, action="delete")
    result["trace_id"] = trace_id
    return as_json(result)


def _parse_pairs(pairs_json: Any, limit: int) -> list[dict[str, Any]]:
    if isinstance(pairs_json, str):
        try:
            pairs = json.loads(pairs_json)
        except ValueError as exc:
            raise ElabFTWError(f"'pairs_json' must be valid JSON: {exc}") from exc
    else:
        pairs = pairs_json
    if not isinstance(pairs, list) or not pairs:
        raise ElabFTWError("'pairs_json' must decode to a non-empty JSON array of link pairs.")
    if len(pairs) > limit:
        raise ElabFTWError(
            f"Too many pairs: {len(pairs)} given, the configured maximum is {limit} "
            f"(features.bulk_links_max_pairs). Split the request."
        )
    normalised = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ElabFTWError(f"pairs_json[{index}] must be an object with from_type/from_id/to_type/to_id.")
        missing = [k for k in ("from_type", "from_id", "to_type", "to_id") if pair.get(k) in (None, "")]
        if missing:
            raise ElabFTWError(f"pairs_json[{index}] is missing: {', '.join(missing)}.")
        normalised.append(pair)
    return normalised


async def _bulk(pairs_json: Any, *, action: str, dry_run: bool, continue_on_error: bool,
                trace_id: Optional[str]) -> str:
    config = get_config()
    pairs = _parse_pairs(pairs_json, config.features.bulk_links_max_pairs)
    if not dry_run:
        require("write_links")
    api = client()
    results: list[dict[str, Any]] = []
    succeeded = failed = 0
    for pair in pairs:
        entry: dict[str, Any] = {"pair": pair}
        if trace_id:
            entry["trace_id"] = trace_id
        try:
            ftype = _linkable(pair["from_type"], "from_type")
            ttype = _linkable(pair["to_type"], "to_type")
            outcome = await _link_once(api, ftype, parse_id(pair["from_id"]), ttype,
                                       parse_id(pair["to_id"]), dry_run=dry_run, action=action)
            entry.update(outcome)
            succeeded += 1
        except (ElabFTWError, Exception) as exc:  # noqa: BLE001
            entry["result"] = "error"
            entry["error"] = getattr(exc, "message", str(exc))
            failed += 1
            results.append(entry)
            if not continue_on_error:
                break
        results.append(entry) if entry not in results else None
    return as_json({
        "dry_run": dry_run,
        "operation": "ensure" if action == "create" else "delete",
        "total": len(pairs),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    })


@mcp.tool()
async def bulk_ensure_links(pairs_json: str, dry_run: bool = True, continue_on_error: bool = True,
                            trace_id: Optional[str] = None) -> str:
    """Idempotently create several directed links in one call. 'pairs_json' is a JSON array of
    {from_type, from_id, to_type, to_id} objects, at most bulk_links_max_pairs entries. WRITE
    OPERATION when dry_run=false — requires the link write scope. Returns a per-pair result list
    plus a summary."""
    return await _bulk(pairs_json, action="create", dry_run=dry_run,
                       continue_on_error=continue_on_error, trace_id=trace_id)


@mcp.tool()
async def bulk_delete_links(pairs_json: str, dry_run: bool = True, continue_on_error: bool = True,
                            trace_id: Optional[str] = None) -> str:
    """Idempotently remove several directed links in one call. 'pairs_json' is a JSON array of
    {from_type, from_id, to_type, to_id} objects. WRITE OPERATION when dry_run=false — requires the
    link write scope. Returns a per-pair result list plus a summary."""
    return await _bulk(pairs_json, action="delete", dry_run=dry_run,
                       continue_on_error=continue_on_error, trace_id=trace_id)


async def _resolve_candidates(api: Client, entity_type: str, *, q=None, extended=None,
                              limit_candidates=5, offset=0, scope=None, category_ids=None,
                              status_ids=None, owner_ids=None, tags=None) -> list[dict[str, Any]]:
    params = {
        "q": normalize_query(q),
        "extended": normalize_query(extended),
        "limit": clamp_limit(limit_candidates, default=5, maximum=50),
        "offset": normalize_offset(offset),
        "scope": normalize_scope(scope),
        "category": normalize_ids_to_csv(category_ids),
        "status": normalize_ids_to_csv(status_ids),
        "owner": normalize_ids_to_csv(owner_ids),
        "tags[]": validate_string_array(tags),
    }
    raw = await api.list_json(entity_type, params=params)
    return [compact_entity(item, drop_body=True) for item in raw if isinstance(item, dict)]


def _resolution(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "not_found"
    return "unique" if len(candidates) == 1 else "ambiguous"


@mcp.tool()
async def resolve_entity_by_query(entity_type: str, q: Optional[str] = None,
                                  extended: Optional[str] = None, limit_candidates: int = 5,
                                  offset: int = 0, scope: str = "all",
                                  category_ids: Optional[list[int]] = None,
                                  status_ids: Optional[list[int]] = None,
                                  owner_ids: Optional[list[int]] = None,
                                  tags: Optional[list[str]] = None) -> str:
    """Search experiments or items by query and return compact candidates for disambiguation.
    Read-only, always available. Returns status, entity_type, resolution (not_found | unique |
    ambiguous) and the candidate list — use it to find an id before acting on it."""
    api = client()
    etype = _linkable(entity_type, "entity_type")
    candidates = await _resolve_candidates(api, etype, q=q, extended=extended,
                                           limit_candidates=limit_candidates, offset=offset,
                                           scope=scope, category_ids=category_ids,
                                           status_ids=status_ids, owner_ids=owner_ids, tags=tags)
    return as_json({
        "status": "ok",
        "entity_type": etype,
        "resolution": _resolution(candidates),
        "count": len(candidates),
        "candidates": candidates,
        "query": {"q": normalize_query(q), "extended": extended, "scope": scope},
    })


@mcp.tool()
async def ensure_link_by_query(from_type: str, from_id: int, to_type: str,
                               q: Optional[str] = None, extended: Optional[str] = None,
                               limit_candidates: int = 5, offset: int = 0, scope: str = "all",
                               category_ids: Optional[list[int]] = None,
                               status_ids: Optional[list[int]] = None,
                               owner_ids: Optional[list[int]] = None,
                               tags: Optional[list[str]] = None, dry_run: bool = True,
                               on_ambiguous: str = "return_candidates",
                               trace_id: Optional[str] = None) -> str:
    """Resolve the target entity by query and then link it idempotently. Query resolution works for
    experiments and items only. WRITE OPERATION when dry_run=false — requires the link write scope.
    With several matches the tool returns the candidates instead of guessing (on_ambiguous='error'
    makes it fail instead)."""
    ftype = _linkable(from_type, "from_type")
    ttype = _linkable(to_type, "to_type")
    on_ambiguous = (on_ambiguous or "return_candidates").strip().lower()
    if on_ambiguous not in ("return_candidates", "error"):
        raise ElabFTWError("'on_ambiguous' must be 'return_candidates' or 'error'.")
    api = client()
    candidates = await _resolve_candidates(api, ttype, q=q, extended=extended,
                                           limit_candidates=limit_candidates, offset=offset,
                                           scope=scope, category_ids=category_ids,
                                           status_ids=status_ids, owner_ids=owner_ids, tags=tags)
    resolution = _resolution(candidates)
    if resolution == "not_found":
        return as_json({"status": "not_found", "entity_type": ttype, "query": {"q": q},
                        "candidates": [], "link": None})
    if resolution == "ambiguous":
        if on_ambiguous == "error":
            raise ElabFTWError(
                f"{len(candidates)} candidates match {q!r} — narrow the query or pass an explicit id.")
        return as_json({"status": "ambiguous", "entity_type": ttype, "query": {"q": q},
                        "candidates": candidates, "link": None})
    target_id = candidates[0].get("id")
    if dry_run:
        preview = await _link_once(api, ftype, parse_id(from_id), ttype, parse_id(target_id),
                                   dry_run=True, action="create")
        return as_json({"status": "ok", "entity_type": ttype, "resolution": "unique",
                        "candidates": candidates, "link": preview})
    require("write_links")
    outcome = await _link_once(api, ftype, parse_id(from_id), ttype, parse_id(target_id),
                              dry_run=False, action="create")
    outcome["trace_id"] = trace_id
    return as_json({"status": "ok", "entity_type": ttype, "resolution": "unique",
                    "candidates": candidates, "link": outcome})


def _json_list(value: Any, param: str) -> list[Any] | None:
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise ElabFTWError(f"'{param}' must be a JSON array: {exc}") from exc
    if not isinstance(parsed, list):
        raise ElabFTWError(f"'{param}' must decode to a JSON array.")
    return parsed


@mcp.tool()
async def expand_links_network(root_type: str, root_id: int, max_depth: Optional[int] = None,
                               direction: str = "both", max_nodes: Optional[int] = None,
                               max_edges: Optional[int] = None, max_per_node: Optional[int] = None,
                               allowed_types: Optional[str] = None, category_ids: Optional[str] = None,
                               status_ids: Optional[str] = None, title_regex: Optional[str] = None,
                               include_node_summaries: bool = True, include_raw: bool = False) -> str:
    """Build the directed link graph around a root experiment/item via BFS. direction is 'both',
    'outgoing' or 'incoming'; max_depth is capped at 3; node/edge/fan-out caps and optional filters
    (allowed_types, category_ids, status_ids, title_regex as JSON arrays/pattern) keep the graph
    bounded. Returns root, parameters, nodes, edges and truncation flags. Read-only."""
    config = get_config()
    api = client()
    rtype = _linkable(root_type, "root_type")
    rid = parse_id(root_id)
    depth_cap = min(int(max_depth if max_depth is not None else config.features.links_network_default_max_depth), 3)
    nodes_cap = int(max_nodes if max_nodes is not None else config.features.links_network_default_max_nodes)
    edges_cap = int(max_edges if max_edges is not None else config.features.links_network_default_max_edges)
    fan_cap = int(max_per_node if max_per_node is not None else config.features.links_network_default_max_per_node)
    direction = (direction or "both").strip().lower()
    if direction not in ("both", "outgoing", "incoming"):
        raise ElabFTWError("'direction' must be 'both', 'outgoing' or 'incoming'.")
    types_filter = {normalize_entity_type(t) for t in (_json_list(allowed_types, "allowed_types") or [])}
    cat_filter = set(_json_list(category_ids, "category_ids") or [])
    status_filter = set(_json_list(status_ids, "status_ids") or [])
    regex = re.compile(title_regex) if title_regex else None

    visited: dict[tuple[str, int], dict[str, Any]] = {}
    queue: list[tuple[str, int, int]] = [(rtype, rid, 0)]
    edges: list[dict[str, Any]] = []
    truncated_nodes = truncated_edges = False
    summaries: dict[tuple[str, int], dict[str, Any]] = {}

    def accepts(entry: dict[str, Any], etype: str) -> bool:
        if types_filter and etype not in types_filter:
            return False
        if cat_filter and entry.get("category") not in cat_filter:
            return False
        if status_filter and entry.get("status") not in status_filter:
            return False
        if regex and not regex.search(str(entry.get("title") or "")):
            return False
        return True

    while queue:
        etype, eid, level = queue.pop(0)
        if (etype, eid) in visited:
            continue
        if len(visited) >= nodes_cap:
            truncated_nodes = True
            break
        entity = await api.get_json(f"{etype}/{eid}")
        if not isinstance(entity, dict):
            continue
        visited[(etype, eid)] = entity
        if include_node_summaries:
            summaries[(etype, eid)] = compact_entity(entity, drop_body=True)

        async def neighbours(target_type: str, outgoing: bool) -> None:
            """Collect one hop; same endpoints as _collect_edges."""
            nonlocal truncated_edges
            if outgoing:
                rows = await api.list_json(f"{etype}/{eid}/{_segment(target_type)}")
                entries = [dict(r, kind=target_type) for r in (rows or []) if isinstance(r, dict)]
                for entry in entries:
                    entry["id"] = entry.get("entityid", entry.get("id"))
            else:
                rows = await api.list_json(target_type,
                                           params={"related": eid, "related_origin": etype, "limit": 200})
                entries = [dict(r, kind=target_type) for r in (rows or []) if isinstance(r, dict)]
            considered = 0
            for entry in entries:
                if entry.get("id") is None or not accepts(entry, target_type):
                    continue
                considered += 1
                if considered > fan_cap:
                    break
                if len(edges) >= edges_cap:
                    truncated_edges = True
                    return
                if outgoing:
                    edges.append(_edge(entry, direction="outgoing", from_type=etype, from_id=eid,
                                       to_type=target_type, to_id=entry.get("id"),
                                       include_raw=include_raw))
                else:
                    edges.append(_edge(entry, direction="incoming", from_type=target_type,
                                       from_id=entry.get("id"), to_type=etype, to_id=eid,
                                       include_raw=include_raw))
                child = (target_type, int(entry["id"]))
                if level + 1 <= depth_cap and child not in visited:
                    queue.append((child[0], child[1], level + 1))

        if direction in ("both", "outgoing"):
            for target_type in ("experiments", "items"):
                await neighbours(target_type, True)
        if direction in ("both", "incoming"):
            other = "items" if etype == "experiments" else "experiments"
            await neighbours(other, False)

    payload: dict[str, Any] = {
        "root": {"type": rtype, "id": rid},
        "params": {"direction": direction, "max_depth": depth_cap, "max_nodes": nodes_cap,
                   "max_edges": edges_cap, "max_per_node": fan_cap,
                   "allowed_types": sorted(types_filter) or None,
                   "category_ids": sorted(cat_filter) or None,
                   "status_ids": sorted(status_filter) or None,
                   "title_regex": title_regex},
        "node_count": len(visited),
        "edge_count": len(edges),
        "truncated": {"nodes": truncated_nodes, "edges": truncated_edges},
        "edges": edges,
    }
    if include_node_summaries:
        payload["nodes"] = [{"type": t, "id": i, **s} for (t, i), s in summaries.items()]
    return as_json(payload)
