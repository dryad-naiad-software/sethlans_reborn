# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Relative-path rules for archive entries and overlay files (Unreal
foundation FR-PA-6 rules 1-2, reused by FR-PA-12 and ``.complete.json``).

``normalize_relpath(name, is_dir)`` maps ``\\`` to ``/``, strips a single
trailing ``/`` from a directory entry and rejects (``ValueError`` naming
the rule):

1. an absolute path (``/x``), a drive (``C:``, ``C:x``), a UNC-like path
   (``//x``), or a ``..``, ``.`` or empty component;
2. NUL, a control character or one of ``<>:"|?*``; a component that is a
   Windows reserved device name (with or without an extension), ends in a
   space or ``.``, is longer than 255 characters, or has the 8.3 short-name
   shape ``~\\d``.

``fold_key`` is the case-insensitive identity used for the duplicate and
file/directory-conflict checks (per-character fold, as NTFS compares).
"""
import re
from typing import Tuple

from shared.unreal.unc import fold_component

MAX_COMPONENT = 255
_FORBIDDEN = frozenset('<>:"|?*')
_SHORT_NAME = re.compile(r'~\d')
_DRIVE = re.compile(r'^[A-Za-z]:')
_RESERVED = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    | {f'COM{n}' for n in '123456789¹²³'}
    | {f'LPT{n}' for n in '123456789¹²³'}
)


def _is_reserved(component: str) -> bool:
    stem = component.split('.', 1)[0].rstrip(' ')
    return stem.upper() in _RESERVED


def check_component(component: str) -> None:
    if component in ('', '.', '..'):
        raise ValueError(f"empty, '.' or '..' component (rule 1): {component!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in component):
        raise ValueError("NUL or control character (rule 2)")
    if any(ch in _FORBIDDEN for ch in component):
        raise ValueError(f"component {component!r} contains one of <>:\"|?* (rule 2)")
    if _is_reserved(component):
        raise ValueError(f"component {component!r} is a Windows reserved device name (rule 2)")
    if component.endswith((' ', '.')):
        raise ValueError(f"component {component!r} ends with a space or a dot (rule 2)")
    if len(component) > MAX_COMPONENT:
        raise ValueError(f"component longer than {MAX_COMPONENT} characters (rule 2)")
    if _SHORT_NAME.search(component):
        raise ValueError(f"component {component!r} looks like an 8.3 short name (rule 2)")


def normalize_relpath(name: str, is_dir: bool = False) -> str:
    """The validated ``/``-separated relpath of ``name`` (FR-PA-6 rules 1-2)."""
    if not isinstance(name, str) or not name:
        raise ValueError("empty name (rule 1)")
    if '\x00' in name:
        raise ValueError("NUL in name (rule 2)")
    text = name.replace('\\', '/')
    if is_dir and text.endswith('/'):
        text = text[:-1]
    if text.startswith('/'):
        raise ValueError("absolute or UNC-like path (rule 1)")
    if _DRIVE.match(text):
        raise ValueError("drive-qualified path (rule 1)")
    for component in text.split('/'):
        check_component(component)
    return text


def components(relpath: str) -> Tuple[str, ...]:
    return tuple(relpath.split('/'))


def fold_key(relpath: str) -> Tuple[str, ...]:
    """Case-insensitive identity of a relpath (per component, NTFS-like)."""
    return tuple(fold_component(part) for part in relpath.split('/'))
