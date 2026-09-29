"""Configuration: defaults, YAML file, environment overrides.

Mirrors the flag set of Marvin's ``config.dynamic.yml`` so an existing
deployment can keep its semantics (write scopes, AI features, link limits).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = "config.yml"


@dataclass
class Features:
    write_enabled: bool = True
    ai_review: bool = True
    tag_suggestions: bool = True
    metadata_suggestions: bool = True
    write_comments: bool = True
    write_tags: bool = True
    write_metadata: bool = True
    write_links: bool = True
    write_create: bool = True
    write_update: bool = True
    allow_body_overwrite: bool = True
    write_steps: bool = True
    inventory_tools: bool = True
    write_inventory: bool = True
    compounds_tools: bool = True
    write_compounds: bool = True
    low_level_tools: bool = False
    cache_ttl_seconds: int = 300
    bulk_links_max_pairs: int = 100
    links_network_default_max_depth: int = 2
    links_network_default_max_nodes: int = 200
    links_network_default_max_edges: int = 500
    links_network_default_max_per_node: int = 50


@dataclass
class Ai:
    model: str = "gpt-4o"
    max_tokens: int = 2048
    temperature: float = 0.3
    # OpenAI-compatible endpoint + key; empty key disables the AI tools.
    base_url: str = ""
    api_key: str = ""


@dataclass
class Provenance:
    require_ai_header: bool = True
    ai_header_prefix: str = "[AI-GENERATED]"
    include_trace_id: bool = True
    include_model_name: bool = True


@dataclass
class Audit:
    enabled: bool = True
    log_path: str = "logs/audit.jsonl"
    log_level: str = "info"
    log_writes: bool = True


@dataclass
class Storage:
    tmp_dir: str = "/tmp/elabftw-mcp"
    cache_max_mb: int = 100


@dataclass
class Server:
    name: str = "elabftw"
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8081
    url_prefix: str = "/el"

@dataclass
class ElabFTW:
    base_url: str = ""
    api_key: str = ""
    timeout: float = 30.0
    verify_tls: bool = True


@dataclass
class Config:
    server: Server = field(default_factory=Server)
    elabftw: ElabFTW = field(default_factory=ElabFTW)
    features: Features = field(default_factory=Features)
    ai: Ai = field(default_factory=Ai)
    provenance: Provenance = field(default_factory=Provenance)
    audit: Audit = field(default_factory=Audit)
    storage: Storage = field(default_factory=Storage)

    # ---- construction -------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        """Read a YAML config file (if present) and apply environment overrides."""
        cfg = cls()
        candidate = Path(path or os.environ.get("ELABFTW_MCP_CONFIG", DEFAULT_CONFIG_PATH))
        if candidate.is_file():
            cfg.apply_yaml(yaml.safe_load(candidate.read_text(encoding="utf-8")) or {})
        cfg.apply_env()
        return cfg

    def apply_yaml(self, data: dict[str, Any]) -> None:
        for section in fields(self):
            values = data.get(section.name)
            if not isinstance(values, dict):
                continue
            target = getattr(self, section.name)
            for key, value in values.items():
                if hasattr(target, key):
                    setattr(target, key, value)

    def apply_env(self) -> None:
        """Environment wins over the file — containers configure this way."""
        if os.environ.get("ELABFTW_BASE_URL"):
            self.elabftw.base_url = os.environ["ELABFTW_BASE_URL"].rstrip("/")
        if os.environ.get("ELABFTW_API_KEY"):
            self.elabftw.api_key = os.environ["ELABFTW_API_KEY"]
        if os.environ.get("ELABFTW_MCP_URL_PREFIX"):
            self.server.url_prefix = os.environ["ELABFTW_MCP_URL_PREFIX"].rstrip("/") or ""
        if os.environ.get("ELABFTW_MCP_PORT"):
            self.server.port = int(os.environ["ELABFTW_MCP_PORT"])
        if os.environ.get("ELABFTW_MCP_HOST"):
            self.server.host = os.environ["ELABFTW_MCP_HOST"]
        if os.environ.get("ELABFTW_MCP_CONFIG"):
            self.storage.tmp_dir = os.environ.get("ELABFTW_MCP_TMP_DIR", self.storage.tmp_dir)
        if os.environ.get("ELABFTW_MCP_AUDIT_LOG"):
            self.audit.log_path = os.environ["ELABFTW_MCP_AUDIT_LOG"]
        if os.environ.get("ELABFTW_MCP_WRITE_ENABLED"):
            self.features.write_enabled = os.environ["ELABFTW_MCP_WRITE_ENABLED"].lower() in ("1", "true", "yes")
        if os.environ.get("ELABFTW_MCP_CACHE_TTL"):
            self.features.cache_ttl_seconds = int(os.environ["ELABFTW_MCP_CACHE_TTL"])
        for env_name, attr in (
            ("OPENAI_API_KEY", "api_key"),
            ("ELABFTW_MCP_AI_API_KEY", "api_key"),
            ("OPENAI_BASE_URL", "base_url"),
            ("ELABFTW_MCP_AI_BASE_URL", "base_url"),
            ("ELABFTW_MCP_AI_MODEL", "model"),
        ):
            if os.environ.get(env_name):
                setattr(self.ai, attr, os.environ[env_name])

    # ---- helpers ------------------------------------------------------
    def effective_write_flags(self, profile: str | None) -> dict[str, bool]:
        """Turn a stored profile ("r"/"h"/"f") into the effective write flags."""
        f = self.features
        base = {
            "write_comments": f.write_comments,
            "write_tags": f.write_tags,
            "write_metadata": f.write_metadata,
            "write_links": f.write_links,
            "write_create": f.write_create,
            "write_update": f.write_update,
            "write_steps": f.write_steps,
            "write_inventory": f.write_inventory,
            "write_compounds": f.write_compounds,
        }
        if profile == "r":
            return {k: False for k in base}
        if profile == "h":
            return {
                **{k: False for k in base},
                "write_comments": base["write_comments"],
                "write_tags": base["write_tags"],
                "write_metadata": base["write_metadata"],
            }
        return base

    @property
    def ai_available(self) -> bool:
        return bool(self.ai.api_key)


_CONFIG: "Config | None" = None


def get_config() -> Config:
    """The process-wide config (loaded once from file + environment)."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config.load()
    return _CONFIG


def set_config(config: Config) -> None:
    """Replace the process-wide config (tests, hosted mode, CLI)."""
    global _CONFIG
    _CONFIG = config
