# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Parsing, serialising and name checks for ``sethlans.ini`` (stdlib only).

The file holds secrets (database password, hashes), so a parse error
never carries the offending line: ``configparser`` puts the raw line in
its messages, and :func:`parse_settings_bytes` reports only the error
class and the line number (or byte offset for bad UTF-8).
"""
import configparser
from io import StringIO

_BAD_NAME_CHARS = frozenset("[]=:\r\n")


class SettingsFileError(OSError):
    """The settings file exists but cannot be trusted, read or parsed."""


def new_parser() -> configparser.RawConfigParser:
    """An empty parser with the settings file's options (no interpolation)."""
    return configparser.RawConfigParser(interpolation=None)


def describe_parse_error(exc: Exception) -> str:
    """``"<ErrorClass> at line N"`` (or ``at byte N``) without any file content."""
    name = type(exc).__name__
    if isinstance(exc, UnicodeDecodeError):
        return f"{name} at byte {exc.start}"
    line = getattr(exc, "lineno", None)
    if line is None and isinstance(exc, configparser.ParsingError) and exc.errors:
        line = exc.errors[0][0]
    return f"{name} at line {line}" if line else name


def parse_settings_bytes(data: bytes, path) -> configparser.RawConfigParser:
    """Parse ``data`` (UTF-8 INI) read from ``path``; raise a content-free ``SettingsFileError``."""
    parser = new_parser()
    try:
        parser.read_string(data.decode("utf-8"), source=str(path))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise SettingsFileError(f"Cannot parse {path}: {describe_parse_error(exc)}") from None
    return parser


def serialise(parser: configparser.RawConfigParser) -> bytes:
    buffer = StringIO()
    parser.write(buffer)
    return buffer.getvalue().encode("utf-8")


def check_name(kind: str, name: object) -> str:
    """A section or key name that cannot break the INI structure."""
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError(f"Settings {kind} must be a non-empty str without outer spaces: {name!r}")
    if _BAD_NAME_CHARS & set(name) or name[0] in "#;":
        raise ValueError(f"Settings {kind} has a forbidden character: {name!r}")
    return name


def check_value(key: str, value: object) -> str:
    """A value as ``str``; ``None`` and line breaks are rejected."""
    if value is None:
        raise ValueError(f"Settings value for {key!r} is None")
    text = str(value)
    if "\r" in text or "\n" in text:
        raise ValueError(f"Settings value for {key!r} contains a line break")
    return text
