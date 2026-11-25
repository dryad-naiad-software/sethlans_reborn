# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Small typed readers for the submitted form, and the error collector.

Errors are keyed by dotted field names (``admin.username``,
``database.port``) plus ``non_field_errors``; each key holds a list of
messages (FR-WZ-2).
"""
import unicodedata
from typing import Dict, List, Optional

NON_FIELD = "non_field_errors"


class Errors:
    def __init__(self):
        self.by_field: Dict[str, List[str]] = {}

    def add(self, field: str, message: str) -> None:
        self.by_field.setdefault(field, []).append(message)

    def has(self, prefix: str) -> bool:
        """True when ``prefix`` or a field under it (``prefix.x``) has an error."""
        return any(key == prefix or key.startswith(prefix + ".") for key in self.by_field)

    def __bool__(self) -> bool:
        return bool(self.by_field)


def section(data: dict, name: str, errors: Errors) -> dict:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.add(name, "Must be an object.")
        return {}
    return value


def _has_control(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in text)


def text(data: dict, key: str, field: str, errors: Errors, *, required=False, max_length=255,
         strip=True, label="This field") -> Optional[str]:
    """A string without control characters; ``None`` after an error."""
    value = data.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        errors.add(field, f"{label} must be text.")
        return None
    if strip:
        value = value.strip()
    if required and not value:
        errors.add(field, f"{label} is required.")
        return None
    if len(value) > max_length:
        errors.add(field, f"{label} is longer than {max_length} characters.")
        return None
    if _has_control(value):
        errors.add(field, f"{label} contains a control character.")
        return None
    return value


def integer(data: dict, key: str, field: str, errors: Errors, *, low: int, high: int,
            default: Optional[int] = None, label="This field") -> Optional[int]:
    """An int (or a string of digits) within ``low..high``."""
    value = data.get(key)
    if value is None or value == "":
        if default is None:
            errors.add(field, f"{label} is required.")
        return default
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int):
        errors.add(field, f"{label} must be a whole number.")
        return None
    if not low <= value <= high:
        errors.add(field, f"{label} must be between {low} and {high}.")
        return None
    return value


def boolean(data: dict, key: str, field: str, errors: Errors, *, default: bool) -> bool:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        errors.add(field, "Must be true or false.")
        return default
    return value
