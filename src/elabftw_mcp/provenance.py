"""Provenance bookkeeping for write operations (parity with elabrmcp).

The block lives inside the entity's metadata under the key
``elabrmcp_provenance`` so existing tooling that knows the key keeps working.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .client import get_config

PROVENANCE_KEY = "elabrmcp_provenance"
SOURCE = "elabftw-mcp"


def merge_metadata_provenance(metadata: Any, operation: str, trace_id: str | None = None,
                              extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add/refresh the provenance block inside a metadata object."""
    config = get_config()
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    existing = metadata.get(PROVENANCE_KEY)
    if not isinstance(existing, dict):
        existing = {}
    new = {
        "source": SOURCE,
        "operation": operation,
        "last_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if trace_id and config.provenance.include_trace_id:
        new["trace_id"] = trace_id
    if config.provenance.include_model_name and config.ai.model:
        new["model"] = config.ai.model
    if extra:
        new.update(extra)
    existing.update(new)
    metadata = dict(metadata)
    metadata[PROVENANCE_KEY] = existing
    return metadata


def body_append_delimiter(content_type: int | None) -> str:
    """Markdown gets a horizontal rule, HTML a whitespace separator (as upstream)."""
    return "\n\n---\n\n" if coerce_content_type(content_type) == 2 else "\n      \n"


def coerce_content_type(value: Any) -> int:
    """1 = HTML, 2 = Markdown (default for LLM-generated text)."""
    try:
        content_type = int(value)
    except (TypeError, ValueError):
        return 2
    return content_type if content_type in (1, 2) else 2


def compose_updated_body(existing_body: Any, new_text: Any, mode: str,
                         content_type: int | None) -> str:
    """Append mode preserves the original body byte for byte; overwrite replaces it."""
    existing = "" if existing_body is None else str(existing_body)
    text = "" if new_text is None else str(new_text)
    if mode == "overwrite":
        return text
    if not existing:
        return text
    return existing + body_append_delimiter(content_type) + text


def ai_header(config_prefix: str | None = None) -> str:
    config = get_config()
    prefix = config_prefix or config.provenance.ai_header_prefix
    return prefix if config.provenance.require_ai_header else ""
