# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Public API for the worker-side JSON config store (FR-24..FR-28).

Exports:
  * ``get_data_dir``           — per-OS data directory (also used for
                                 tools/assets/output paths per FR-24a).
  * ``SYSTEM_CONFIG_PATH``     — Linux system-wide path constant.
  * ``load``                   — merged dict from system + per-user.
  * ``save``                   — full-dict atomic write (rarely used).
  * ``get(dotted_key, default)`` — lock-free nested read.
  * ``set(dotted_key, value)``   — thread-safe AND cross-process-safe
                                   load -> mutate -> save cycle.
  * ``ConfigLockTimeoutError`` — raised when the file lock cannot be
                                 acquired within the timeout.

``set()`` MUST NEVER log argument values — NF-5 / FR-13. Only the
dotted key is logged.
"""

import logging
import threading
from typing import Any, Tuple

from .io import (
    ConfigLockTimeoutError,
    CrossProcessLock,
    atomic_write,
    deep_merge,
    load_system_config,
    read_json_file,
)
from .paths import (
    SYSTEM_CONFIG_PATH,
    get_data_dir,
    lockfile_path,
    user_config_path,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ConfigLockTimeoutError",
    "SYSTEM_CONFIG_PATH",
    "get",
    "get_data_dir",
    "load",
    "save",
    "set",
    "set_many",
]

# In-process lock that covers the full load -> mutate -> save cycle.
_config_lock = threading.Lock()


def load() -> dict:
    """Return the merged config dict (per-user overlays system-wide)."""
    system_cfg = load_system_config()
    user_cfg = read_json_file(user_config_path())
    return deep_merge(system_cfg, user_cfg)


def save(config_dict: dict) -> None:
    """Persist the given dict to the per-user config path atomically."""
    atomic_write(user_config_path(), config_dict)


def _split_dotted(dotted_key: str) -> Tuple[str, ...]:
    if not dotted_key or not isinstance(dotted_key, str):
        raise ValueError("dotted_key must be a non-empty string")
    return tuple(dotted_key.split("."))


def _get_nested(data: dict, parts: Tuple[str, ...], default: Any) -> Any:
    current: Any = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _set_nested(data: dict, parts: Tuple[str, ...], value: Any) -> None:
    current: dict = data
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def get(dotted_key: str, default: Any = None) -> Any:
    """Read a dotted key from the merged config (no lock on reads)."""
    parts = _split_dotted(dotted_key)
    return _get_nested(load(), parts, default)


def set(dotted_key: str, value: Any) -> None:  # noqa: A001 - module API
    """Write a dotted key under the full load -> mutate -> save lock.

    Thread-safe via a process-local mutex and cross-process-safe via a
    file lock on a sidecar ``config.json.lock``. NEVER logs ``value``
    (NF-5) — only the key.
    """
    set_many([(dotted_key, value)])


def set_many(pairs) -> None:
    """Write multiple dotted keys in a single lock-acquire-write cycle.

    Prevents half-configured state on crash — all mutations land in
    one atomic file write. ``pairs`` is an iterable of
    ``(dotted_key, value)`` tuples. Values are NEVER logged (NF-5).
    """
    resolved = [(_split_dotted(k), v) for k, v in pairs]
    keys_only = [k for k, _ in pairs]
    logger.debug("config_store.set_many keys=%s", keys_only)
    with _config_lock:
        with CrossProcessLock(lockfile_path()):
            current = read_json_file(user_config_path())
            for parts, value in resolved:
                _set_nested(current, parts, value)
            atomic_write(user_config_path(), current)
