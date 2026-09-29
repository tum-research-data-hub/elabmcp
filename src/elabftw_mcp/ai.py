"""LLM helper for the AI tools.

Marvin's upstream implementation returned bootstrap placeholders; this port keeps
the very same response contract but actually calls a model when an
OpenAI-compatible endpoint is configured (config ``ai`` / env
``ELABFTW_MCP_AI_API_KEY``), and falls back to the placeholder wording when not.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any

import httpx

from .config import get_config
from .errors import ElabFTWError

PLACEHOLDER_NOTE = ("[BOOTSTRAP PLACEHOLDER] No AI endpoint configured "
                    "(set ai.api_key / ELABFTW_MCP_AI_API_KEY). ")


def new_trace_id() -> str:
    """Same shape as upstream: trace-<16 hex chars>."""
    return "trace-" + secrets.token_hex(8)


def ai_available() -> bool:
    config = get_config()
    return bool(config.ai.api_key)


def _endpoint() -> str:
    config = get_config()
    base = (config.ai.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    if not base.endswith("/v1") and "openai.com" in base:
        base = base.rstrip("/")
    return f"{base}/chat/completions"


async def chat(system: str, user: str, *, json_mode: bool = True,
               max_tokens: int | None = None) -> str:
    """One chat completion; returns the assistant text."""
    config = get_config()
    if not config.ai.api_key:
        raise ElabFTWError(PLACEHOLDER_NOTE.strip())
    payload: dict[str, Any] = {
        "model": config.ai.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": config.ai.temperature,
        "max_tokens": max_tokens or config.ai.max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {config.ai.api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        response = await client.post(_endpoint(), headers=headers, json=payload)
        if response.status_code >= 400:
            raise ElabFTWError(
                f"LLM request failed with {response.status_code}: {response.text[:300]}")
        data = response.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ElabFTWError(f"Unexpected LLM response shape: {str(data)[:200]}") from exc


def parse_json_block(text: str) -> Any:
    """Tolerant JSON extraction from a model answer."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except ValueError:
            pass
    raise ElabFTWError(f"Could not parse JSON from the model answer: {text[:200]}")


def entity_context(entity: dict[str, Any], *, extra: dict[str, Any] | None = None,
                   body_limit: int = 6000) -> str:
    """Compact textual context for prompts."""
    parts = [f"Title: {entity.get('title')}"]
    if entity.get("date"):
        parts.append(f"Date: {entity.get('date')}")
    if entity.get("category_title") or entity.get("category"):
        parts.append(f"Category: {entity.get('category_title') or entity.get('category')}")
    if entity.get("tags"):
        parts.append(f"Tags: {', '.join(str(t) for t in entity['tags'])}")
    metadata = entity.get("metadata") or entity.get("extra_fields")
    if metadata:
        fields = metadata.get("extra_fields") if isinstance(metadata, dict) else None
        if fields:
            rendered = json.dumps(fields, ensure_ascii=False)[:1500]
            parts.append(f"Metadata: {rendered}")
    body = str(entity.get("body") or "")
    if body:
        parts.append("Body:\n" + body[:body_limit])
    if extra:
        for key, value in extra.items():
            if value:
                parts.append(f"{key}:\n{json.dumps(value, ensure_ascii=False)[:1500]}")
    return "\n".join(parts)
