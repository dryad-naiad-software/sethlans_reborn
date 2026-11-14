# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The job summary sent to the manager (Unreal foundation FR-UE-14).

``RenderResult.stdout`` (stored as ``last_output``) is this compact
summary, at most 32 KiB, never the UE log: one ``key=value`` line per key,
prefixed ``[sethlans-unreal]``, in insertion order (a key set twice keeps
its first position and the last value). Values have control characters
and newlines removed; ``warnings`` entries are added as ``warning=`` lines.
Tests and the E2E assert on these keys.
"""
import re
import threading
from typing import Dict, Iterable, List

from sethlans_worker_agent.unreal_executor import farm_defaults
from sethlans_worker_agent.unreal_executor.log_validation import LOG_RULES_VERSION

PREFIX = '[sethlans-unreal]'
MAX_BYTES = 32 * 1024
MAX_VALUE = 1024
MAX_WARNINGS = 64
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


def _clean(value) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, float):
        value = f"{value:.1f}"
    return _CONTROL.sub(' ', str(value))[:MAX_VALUE]


class Summary:
    """Ordered ``key=value`` pairs; thread-safe (the job thread is the only writer in practice)."""

    def __init__(self):
        self._lock = threading.Lock()  # leaf lock
        self._values: Dict[str, str] = {}
        self._warnings: List[str] = []
        self.set('farm_defaults_version', farm_defaults.FARM_DEFAULTS_VERSION)
        self.set('log_rules_version', LOG_RULES_VERSION)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._values[key] = _clean(value)

    def update(self, values: Dict[str, object]) -> None:
        for key, value in values.items():
            self.set(key, value)

    def get(self, key: str, default: str = '') -> str:
        with self._lock:
            return self._values.get(key, default)

    def append(self, key: str, value) -> None:
        """Add ``value`` to a comma-separated list value."""
        with self._lock:
            current = self._values.get(key, '')
            item = _clean(value)
            parts = [p for p in current.split(',') if p]
            if item and item not in parts:
                parts.append(item)
            self._values[key] = ','.join(parts)

    def warn(self, text: str) -> None:
        with self._lock:
            if len(self._warnings) < MAX_WARNINGS:
                self._warnings.append(_clean(text))

    def warnings(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._warnings)

    def text(self) -> str:
        with self._lock:
            lines = [f"{PREFIX} {key}={value}" for key, value in self._values.items()]
            lines += [f"{PREFIX} warning={value}" for value in self._warnings]
        out, size = [], 0
        for line in lines:
            size += len(line.encode('utf-8')) + 1
            if size > MAX_BYTES:
                break
            out.append(line)
        return '\n'.join(out)
