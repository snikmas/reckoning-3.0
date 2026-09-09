"""Validation shared by setup, persisted provider settings, and adapters."""

from __future__ import annotations

import re

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_HTTP_FIELD_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def is_valid_env_name(value: object) -> bool:
    """Return whether *value* is a portable environment-variable name."""
    return isinstance(value, str) and _ENV_NAME.fullmatch(value) is not None


def is_valid_header_name(value: object) -> bool:
    """Return whether *value* is an RFC 9110 HTTP field name."""
    return isinstance(value, str) and _HTTP_FIELD_NAME.fullmatch(value) is not None


def is_safe_header_value(value: object) -> bool:
    """Reject empty and line-breaking values before urllib sees them."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and "\r" not in value
        and "\n" not in value
    )
