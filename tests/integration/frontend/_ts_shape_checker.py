# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Check a JSON value against a hand-written TypeScript shape (Unreal
sequence chunk 8 contract tests).

The TS source is read as text: ``export interface X { ... }`` (generic
``Page<T>`` included) and ``export type X = ...;`` aliases.  A value
matches a type when it matches one member of its union: ``null``,
``number``, ``string``, ``boolean``, ``true``/``false``, a string literal,
``T[]``, a ``[a, b]`` tuple, an alias, or an interface (every field
without ``?`` present, every present field matching).  Keys the API sends
beyond the interface are allowed: the frontend ignores them.  An
``extends`` base contributes its fields, and ``TsShapes.from_file`` also
loads the files a source imports by relative path (``import { X } from
'./y'``), so an interface defined in another model file resolves.

Module name starts with an underscore so pytest does not collect it.
"""

from __future__ import annotations

import re
from pathlib import Path

_INTERFACE_RE = re.compile(r"export\s+interface\s+(\w+)(?:<\w+>)?\s*(?:extends\s+(\w+)\s*)?\{([^}]*)\}", re.DOTALL)
_IMPORT_RE = re.compile(r"import\s+(?:type\s+)?\{[^}]*\}\s*from\s*['\"](\.{1,2}/[^'\"]+)['\"]")
_ALIAS_RE = re.compile(r"export\s+type\s+(\w+)\s*=\s*([^;]+);", re.DOTALL)
_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_FIELD_RE = re.compile(r"^\s*(\w+)(\?)?\s*:\s*([^;]+);", re.MULTILINE)
_SCALARS = {
    'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    'string': lambda v: isinstance(v, str),
    'boolean': lambda v: isinstance(v, bool),
    'true': lambda v: v is True,
    'false': lambda v: v is False,
    'null': lambda v: v is None,
}


def _split_top(text: str, sep: str) -> list[str]:
    """Split ``text`` at ``sep`` outside brackets."""
    parts, depth, current = [], 0, ''
    for char in text:
        depth += char in '[(<{'
        depth -= char in '])>}'
        if char == sep and depth == 0:
            parts.append(current.strip())
            current = ''
        else:
            current += char
    parts.append(current.strip())
    return [part for part in parts if part]


class TsShapes:
    """The interfaces and aliases of one or more TS sources."""

    def __init__(self, *sources: str):
        self.interfaces: dict[str, dict[str, tuple[str, bool]]] = {}
        self.aliases: dict[str, str] = {}
        self.bases: dict[str, str] = {}
        for source in sources:
            source = _COMMENT_RE.sub('', source)  # a comment may hold braces or semicolons
            for name, base, body in _INTERFACE_RE.findall(source):
                self.interfaces[name] = {
                    m.group(1): (' '.join(m.group(3).split()), bool(m.group(2))) for m in _FIELD_RE.finditer(body)
                }
                if base:
                    self.bases[name] = base
            for name, value in _ALIAS_RE.findall(source):
                self.aliases[name] = ' '.join(value.split())

    @classmethod
    def from_file(cls, path: Path) -> TsShapes:
        """The shapes of ``path`` and of every file it imports by relative path, transitively."""
        sources, seen, todo = [], set(), [Path(path).resolve()]
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            assert current.is_file(), f"TS file {current} not found"
            text = current.read_text(encoding='utf-8')
            sources.append(text)
            todo.extend((current.parent / f"{spec}.ts").resolve() for spec in _IMPORT_RE.findall(text))
        return cls(*sources)

    def fields(self, interface: str) -> dict[str, tuple[str, bool]]:
        """``{field: (ts_type, optional)}``, inherited fields first; fails for an unknown interface."""
        assert interface in self.interfaces, f"TS interface {interface!r} not found"
        base = self.bases.get(interface)
        return {**self.fields(base), **self.interfaces[interface]} if base else self.interfaces[interface]

    def errors(self, value, ts_type: str, path: str = '$', generic: str | None = None) -> list[str]:
        """Why ``value`` does not match ``ts_type`` (empty when it does)."""
        members = _split_top(ts_type, '|')
        found = []
        for member in members:
            problems = self._member_errors(value, member, path, generic)
            if not problems:
                return []
            found.extend(problems)
        return found if len(members) == 1 else [f"{path}: {value!r} is none of {ts_type}"]

    def _member_errors(self, value, member, path, generic):
        member = member.strip()
        if member.startswith('(') and member.endswith(')'):
            return self.errors(value, member[1:-1], path, generic)
        if member == 'T' and generic:
            return self.errors(value, generic, path)
        if member in _SCALARS:
            return [] if _SCALARS[member](value) else [f"{path}: {value!r} is not {member}"]
        if member[0] in '\'"':
            return [] if value == member[1:-1] else [f"{path}: {value!r} is not {member}"]
        if member.endswith('[]'):
            if not isinstance(value, list):
                return [f"{path}: {value!r} is not an array"]
            return [e for i, item in enumerate(value) for e in self.errors(item, member[:-2], f"{path}[{i}]", generic)]
        if member.startswith('['):
            return self._tuple_errors(value, _split_top(member[1:-1], ','), path)
        if member in self.aliases:
            return self.errors(value, self.aliases[member], path, generic)
        name, _, argument = member.partition('<')
        if name == 'Record':
            return self._record_errors(value, _split_top(argument[:-1], ',')[1], path)
        return self.object_errors(value, name, path, argument[:-1] or None)

    def _record_errors(self, value, value_type, path):
        """``Record<string, V>``: an object whose every value is a V (``never``: an empty object)."""
        if not isinstance(value, dict):
            return [f"{path}: {value!r} is not an object (Record)"]
        if value_type == 'never':
            return [] if not value else [f"{path}: {value!r} is not an empty object"]
        return [e for key, item in value.items() for e in self.errors(item, value_type, f"{path}.{key}")]

    def _tuple_errors(self, value, items, path):
        if not isinstance(value, list) or len(value) != len(items):
            return [f"{path}: {value!r} is not a {len(items)}-tuple"]
        return [e for i, (item, ts) in enumerate(zip(value, items)) for e in self.errors(item, ts, f"{path}[{i}]")]

    def object_errors(self, value, interface: str, path: str = '$', generic: str | None = None) -> list[str]:
        """Why ``value`` is not an ``interface`` object (empty when it is)."""
        if not isinstance(value, dict):
            return [f"{path}: {value!r} is not an object ({interface})"]
        problems = []
        for field, (ts_type, optional) in self.fields(interface).items():
            if field not in value:
                if not optional:
                    problems.append(f"{path}.{field}: missing ({interface})")
                continue
            problems.extend(self.errors(value[field], ts_type, f"{path}.{field}", generic))
        return problems
