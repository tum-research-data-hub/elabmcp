"""Write-scope policy: what the current token/profile is allowed to do."""
from __future__ import annotations

from .config import get_config
from .credentials import write_profile_var
from .errors import WriteNotAllowed

FLAG_LABELS = {
    "write_comments": "adding comments",
    "write_tags": "changing tags",
    "write_metadata": "changing metadata",
    "write_links": "creating or removing links",
    "write_create": "creating entities",
    "write_update": "updating entities",
    "write_steps": "changing steps",
    "write_inventory": "changing inventory",
    "write_compounds": "changing compounds",
}


def effective_flags() -> dict[str, bool]:
    config = get_config()
    flags = config.effective_write_flags(write_profile_var.get())
    if not config.features.write_enabled:
        return {k: False for k in flags}
    return flags


def current_profile() -> str:
    return write_profile_var.get() or "f"


def is_allowed(flag: str) -> bool:
    return bool(effective_flags().get(flag, False))


def require(flag: str) -> None:
    """Raise a readable error when the current scope forbids this operation."""
    if is_allowed(flag):
        return
    action = FLAG_LABELS.get(flag, flag)
    profile = current_profile()
    raise WriteNotAllowed(
        f"Not permitted: this token/host is not allowed to perform {action} "
        f"(effective write scope profile '{profile}'). Ask for a token with a wider scope."
    )
