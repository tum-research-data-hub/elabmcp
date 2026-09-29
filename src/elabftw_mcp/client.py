"""Async eLabFTW REST API v2 client.

One shared HTTP connection pool serves every user; credentials come per request
from the context vars (hosted mode) or from the config (stdio mode). Endpoint
usage follows the official OpenAPI v2 document (reference/openapi-v2-6.0.2.yaml).
"""
from __future__ import annotations

import asyncio
import json as jsonlib
import re
import time
from typing import Any, Iterable, Mapping

import httpx

from . import USER_AGENT
from .config import Config, get_config
from .credentials import api_key_var, base_url_var, key_fingerprint
from .errors import ElabFTWError, api_error

# Canonical API segments per entity type, plus accepted synonyms.
ENTITY_TYPES: dict[str, str] = {
    "experiment": "experiments",
    "experiments": "experiments",
    "exp": "experiments",
    "item": "items",
    "items": "items",
    "resource": "items",
    "resources": "items",
    "database": "items",
    "database_item": "items",
    "template": "experiments_templates",
    "templates": "experiments_templates",
    "experiment_template": "experiments_templates",
    "experiments_templates": "experiments_templates",
    "item_type": "items_types",
    "item_types": "items_types",
    "items_types": "items_types",
    "resource_template": "items_types",
    "resources_template": "items_types",
}

STEPS_ENTITY_TYPES = {"experiments", "items", "experiments_templates", "items_types"}
UPLOAD_ENTITY_TYPES = {"experiments", "items", "items_types"}

_CACHE: dict[str, tuple[float, Any]] = {}
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


def normalize_entity_type(value: str) -> str:
    key = (value or "").strip().lower().replace("-", "_")
    if key not in ENTITY_TYPES:
        raise ElabFTWError(
            f"Unknown entity_type {value!r}. Use one of: experiments, items, "
            f"experiments_templates, items_types."
        )
    return ENTITY_TYPES[key]


def parse_id(value: Any, what: str = "id") -> int:
    """Accept 12, '12' or a URL ending in the id; reject everything else loudly."""
    if isinstance(value, bool):
        raise ElabFTWError(f"Invalid {what}: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    tail = text.rstrip("/").split("/")[-1]
    if tail.isdigit():
        return int(tail)
    raise ElabFTWError(f"Invalid {what} {value!r}: expected a numeric eLabFTW id.")


def parse_id_list(values: Iterable[Any] | None, what: str = "ids") -> list[int]:
    if not values:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    return [parse_id(v, what) for v in values]


async def _http_client(config: Config) -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(config.elabftw.timeout, connect=10.0),
                    verify=config.elabftw.verify_tls,
                    limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
                    follow_redirects=True,
                )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def credentials() -> tuple[str, str]:
    """(base_url, api_key) for the current request, with a readable error."""
    config = get_config()
    base_url = (base_url_var.get() or config.elabftw.base_url or "").rstrip("/")
    api_key = api_key_var.get() or config.elabftw.api_key or ""
    if not base_url:
        raise ElabFTWError(
            "No eLabFTW base URL configured. Set ELABFTW_BASE_URL (stdio) or pass your "
            "instance URL to /register (hosted)."
        )
    if not api_key:
        raise ElabFTWError(
            "No eLabFTW API key configured. Set ELABFTW_API_KEY (stdio) or register with "
            "your personal API key (hosted)."
        )
    return base_url, api_key


class Client:
    """Thin, typed wrapper around the eLabFTW v2 REST API."""

    def __init__(self, base_url: str, api_key: str, *, trace_id: str | None = None) -> None:
        self.config = get_config()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.trace_id = trace_id or ""
        self.fingerprint = key_fingerprint(api_key)

    # ------------------------------------------------------------------
    @classmethod
    def from_context(cls) -> "Client":
        base_url, api_key = credentials()
        from .credentials import current_trace_id

        return cls(base_url, api_key, trace_id=current_trace_id())

    # ------------------------------------------------------------------
    @property
    def api_root(self) -> str:
        return f"{self.base_url}/api/v2"

    def url(self, path: str) -> str:
        return f"{self.api_root}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        cacheable: bool = False,
        retries: int = 2,
    ) -> httpx.Response:
        config = self.config
        url = self.url(path)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None and v != []}
        cache_key = ""
        if cacheable and config.features.cache_ttl_seconds > 0:
            cache_key = jsonlib.dumps([self.fingerprint, method, url, sorted(clean_params.items())], default=str)
            hit = _CACHE.get(cache_key)
            if hit and hit[0] > time.time():
                return hit[1]

        request_headers = {
            "Authorization": self.api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        request_headers.update(headers or {})

        client = await _http_client(config)
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await client.request(
                    method, url, params=clean_params or None, json=json,
                    files=files, data=data, headers=request_headers,
                )
            except httpx.HTTPError as exc:  # connection issues, timeouts
                if attempt <= retries and method.upper() in ("GET", "HEAD"):
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise ElabFTWError(f"Could not reach eLabFTW at {url}: {exc}") from exc

            if response.status_code >= 500 and attempt <= retries and method.upper() in ("GET", "HEAD"):
                await asyncio.sleep(0.5 * attempt)
                continue
            if response.status_code >= 400:
                raise api_error(method.upper(), url, response.status_code, response.text)
            break

        if cache_key:
            _CACHE[cache_key] = (time.time() + config.features.cache_ttl_seconds, response)
        return response

    async def get_json(self, path: str, *, params: Mapping[str, Any] | None = None,
                       cacheable: bool = False) -> Any:
        response = await self.request("GET", path, params=params, cacheable=cacheable)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def list_json(self, path: str, *, params: Mapping[str, Any] | None = None,
                        cacheable: bool = False) -> list[dict[str, Any]]:
        data = await self.get_json(path, params=params, cacheable=cacheable)
        if data is None:
            return []
        if isinstance(data, list):
            return data
        raise ElabFTWError(f"Unexpected response shape from {path}: expected a list.")

    async def create(self, path: str, payload: dict[str, Any] | None = None,
                     *, data: Any = None, files: Any = None) -> tuple[int | None, Any]:
        """POST an entity. eLabFTW answers 201 with the new id in the Location header."""
        response = await self.request("POST", path, json=payload, data=data, files=files)
        new_id: int | None = None
        location = response.headers.get("location", "")
        if location:
            match = re.search(r"/(\d+)/?$", location)
            if match:
                new_id = int(match.group(1))
            else:
                match = re.search(r"/(\d+)(?:\?|$)", location)
                new_id = int(match.group(1)) if match else None
        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = response.text
        return new_id, body

    async def patch(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = await self.request("PATCH", path, json=payload)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def delete(self, path: str, *, params: Mapping[str, Any] | None = None) -> None:
        await self.request("DELETE", path, params=params)

    async def upload_file(self, entity_type: str, entity_id: int, filename: str,
                          content: bytes, mime_type: str = "application/octet-stream",
                          *, comment: str | None = None) -> tuple[int | None, Any]:
        """POST /{entity_type}/{id}/uploads — multipart, field name ``file``."""
        files = {"file": (filename, content, mime_type)}
        data = {"comment": comment} if comment is not None else None
        return await self.create(f"{entity_type}/{entity_id}/uploads", data=data, files=files)

    # ------------------------------------------------------------------
    def cache_clear(self) -> None:
        for key in [k for k in _CACHE if k.startswith(f'["{self.fingerprint}"')]:
            _CACHE.pop(key, None)
