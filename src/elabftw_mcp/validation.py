"""Input normalisation — same semantics as Marvin's elabrmcp helpers.

Keeping the validation identical means an existing client prompt keeps working:
invalid values fail loudly with the same wording instead of silently changing
meaning.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from .errors import ElabFTWError

ORDER_FIELDS = ("cat", "comment", "customid", "date", "id", "lastchange", "rating", "status", "title", "user")
SCOPE_ALIASES = {"me": 1, "self": 1, "team": 2, "all": 3, "everything": 3}


def normalize_query(q: Any) -> str | None:
    """NULL / "" / whitespace / "*" / "**" mean "no search"."""
    if q is None:
        return None
    text = str(q).strip()
    if not text or text in ("*", "**"):
        return None
    return text


def normalize_ids_to_csv(ids: Iterable[Any] | None) -> str | None:
    from .client import parse_id_list

    values = parse_id_list(ids)
    return ",".join(str(v) for v in values) if values else None


def validate_string_array(values: Any, param: str = "values") -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    cleaned = [str(v).strip() for v in values if v is not None and str(v).strip()]
    return cleaned or None


def normalize_state(state: Any) -> str | None:
    if state is None:
        return None
    text = str(state).strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split(",")]
    invalid = [p for p in parts if p not in ("1", "2", "3")]
    if invalid:
        raise ElabFTWError(
            "'state' must be a comma-separated string of values from {1, 2, 3} "
            f"(got invalid value(s): {', '.join(invalid)})."
        )
    return ",".join(parts)


def normalize_order(order: Any) -> str | None:
    if order is None:
        return None
    value = str(order).strip().lower()
    if not value:
        return None
    if value not in ORDER_FIELDS:
        raise ElabFTWError(f"'order' must be one of: {', '.join(ORDER_FIELDS)} (got '{value}').")
    return value


def normalize_sort(sort: Any) -> str | None:
    if sort is None:
        return None
    value = str(sort).strip().lower()
    if not value:
        return None
    if value not in ("asc", "desc"):
        raise ElabFTWError(f"'sort' must be 'asc' or 'desc' (got '{value}').")
    return value


def normalize_scope(scope: Any) -> int | None:
    """1 = me, 2 = team, 3 = everything. Unknown values are ignored (as upstream)."""
    if scope is None:
        return None
    if isinstance(scope, bool):
        return None
    if isinstance(scope, int):
        return scope if scope in (1, 2, 3) else None
    text = str(scope).strip().lower()
    if text.isdigit() and int(text) in (1, 2, 3):
        return int(text)
    return SCOPE_ALIASES.get(text)


def clamp_limit(limit: Any, default: int = 25, maximum: int = 100) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def normalize_offset(offset: Any) -> int:
    try:
        return max(0, int(offset))
    except (TypeError, ValueError):
        return 0


def split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ElabFTWError(message)


def one_of(value: str, allowed: Sequence[str], param: str, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text not in allowed:
        raise ElabFTWError(f"'{param}' must be one of: {', '.join(allowed)} (got '{text}').")
    return text
