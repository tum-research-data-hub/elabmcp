"""Self-contained HMAC tokens for the hosted mode.

Token format: ``urlsafe_base64(json_payload).hex(HMAC-SHA256)`` with
``{"u": base64(base_url), "k": base64(api_key), "p": profile, "t": [tools], "exp": epoch}``.
The format is byte-compatible with the previous deployment, so already-issued
personal URLs keep working after the switch.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

DEFAULT_EXPIRY_DAYS = 30
PROFILES = ("r", "h", "f")


def _get_jwt_secret() -> str:
    secret = os.environ.get("MCP_JWT_SECRET", "")
    if not secret:
        raise RuntimeError(
            "MCP_JWT_SECRET environment variable is not set. Generate one with: "
            'python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
    return secret


def _expiry_days(default: int | None = None) -> int:
    return int(os.environ.get("MCP_TOKEN_EXPIRY_DAYS", default or DEFAULT_EXPIRY_DAYS))


def encode_token(base_url: str, api_key: str, profile: str = "r", *,
                 enabled_tools: list[str] | None = None, secret: str | None = None,
                 expiry_days: int | None = None) -> str:
    secret = secret or _get_jwt_secret()
    payload: dict[str, object] = {
        "u": base64.urlsafe_b64encode(base_url.encode()).decode(),
        "k": base64.urlsafe_b64encode(api_key.encode()).decode(),
        "p": profile if profile in PROFILES else "r",
        "exp": int(time.time()) + _expiry_days(expiry_days) * 86400,
    }
    if enabled_tools is not None:
        payload["t"] = list(enabled_tools)
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def decode_token(token: str, secret: str | None = None) -> dict | None:
    """Verify and decode a token; ``None`` when invalid or expired."""
    try:
        secret = secret or _get_jwt_secret()
    except RuntimeError:
        return None
    try:
        payload_b64, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if time.time() > payload.get("exp", 0):
            return None
        payload["u"] = base64.urlsafe_b64decode(payload["u"]).decode()
        payload["k"] = base64.urlsafe_b64decode(payload["k"]).decode()
        if payload.get("p") not in PROFILES:
            payload["p"] = "r"  # tokens without a profile are read-only, as before
        return payload
    except (ValueError, KeyError, json.JSONDecodeError, Exception):  # noqa: BLE001
        return None
