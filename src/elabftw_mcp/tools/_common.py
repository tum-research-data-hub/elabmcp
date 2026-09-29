"""Helpers shared by the tool modules."""
from __future__ import annotations

import time
from typing import Any

from ..client import Client, parse_id
from ..errors import ElabFTWError

# Small cache for team capabilities / user identity (per credential fingerprint).
_CAPS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CAPS_TTL = 300.0


def client() -> Client:
    return Client.from_context()


async def resolve_team_id(client_: Client, team_id: Any) -> int:
    """'current' (or empty) resolves to the user's active team via /users/me."""
    if team_id is None:
        return await current_team_id(client_)
    text = str(team_id).strip().lower()
    if text in ("", "current", "active", "me"):
        return await current_team_id(client_)
    return parse_id(text, "team_id")


async def current_team_id(client_: Client) -> int:
    me = await client_.get_json("users/me", cacheable=True)
    team = (me or {}).get("team")
    if not team:
        raise ElabFTWError("Could not determine the active team — /users/me returned no team.")
    return int(team)


async def safe_fetch(coro) -> Any:
    """Best-effort enrichment: failures become an inline note, never an error."""
    try:
        return await coro
    except ElabFTWError as exc:
        return {"unavailable": exc.message}
    except Exception as exc:  # noqa: BLE001
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


async def team_caps(client_: Client, *, refresh: bool = False) -> dict[str, Any]:
    """Sanitised capability flags of the user's team (cached, like /*team_caps)."""
    key = f"{client_.fingerprint}:{client_.base_url}"
    if not refresh:
        hit = _CAPS_CACHE.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    data: dict[str, Any] = {}
    try:
        raw = await client_.get_json("teams/current")
        if isinstance(raw, dict):
            data = {
                "id": raw.get("id"),
                "name": raw.get("name"),
                "can_manage_compounds": int(raw.get("can_manage_compounds") or 0),
                "can_manage_inventory_locations": int(raw.get("can_manage_inventory_locations") or 0),
                "visible": raw.get("visible"),
                "is_archived": raw.get("is_archived"),
            }
    except ElabFTWError as exc:
        # /teams/current is not exposed everywhere — fall back to the numeric team id.
        try:
            team_id = await current_team_id(client_)
            raw = await client_.get_json(f"teams/{team_id}")
            if isinstance(raw, dict):
                data = {
                    "id": raw.get("id", team_id),
                    "name": raw.get("name"),
                    "can_manage_compounds": int(raw.get("can_manage_compounds") or 0),
                    "can_manage_inventory_locations": int(raw.get("can_manage_inventory_locations") or 0),
                }
        except ElabFTWError:
            data = {"unavailable": exc.message}
    _CAPS_CACHE[key] = (time.time() + _CAPS_TTL, data)
    return data
