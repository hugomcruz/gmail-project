"""Shared utility helpers used across the email-processor app."""

from __future__ import annotations


def is_enabled_flag(value: object) -> bool:
    """Return True when *value* represents an enabled/truthy state.

    Handles bool, None, and common string representations of false
    (``"false"``, ``"0"``, ``"no"``, ``"off"``, empty string).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off", ""}
    return value is not None
