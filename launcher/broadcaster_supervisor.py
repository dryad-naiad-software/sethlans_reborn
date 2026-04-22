# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Launcher-side MulticastBroadcaster supervision (manager spec Phase 3).

Moved out of the Django app server and into the launcher so the
broadcaster lives with process lifecycle rather than with the WSGI
server. Phase 3 relocated the ownership here; Phase 7 removed the
last Django-side shim. This module is the sole owner.

**Send-only invariant** (FR-9 / restated): ``MulticastBroadcaster`` has
no socket read loop. Its threat model post-move is unchanged — it
emits packets and closes. Because the launcher process holds the TLS
private key file handle and higher privileges, any future change that
adds a network read path requires a security re-review and a
relocation plan.

Input flow: the Django manager, after ``initialize_runtime_state()``
populates ``runtime_state`` (manager_id, cert fingerprint, advertised
IP etc.), atomically writes
``<manager_data_dir>/broadcaster_params.json``. The launcher polls
for that file and, once observed, starts the broadcaster thread
in-process. This file-based IPC keeps the launcher independent of
Django / DB boot timing without requiring a new socket channel.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BROADCASTER_PARAMS_FILENAME = "broadcaster_params.json"


def _load_multicast_broadcaster_cls():
    """Import ``MulticastBroadcaster`` with source/frozen tolerance.

    Broadcaster lives in ``manager/workers/multicast_broadcaster.py``.
    In source mode we add ``manager/`` to ``sys.path`` so the import
    works without changing the launcher's shape. In frozen mode the
    manager bundle ships its own copy; this helper still works because
    the frozen launcher entry point adds the appropriate path at boot.
    """
    project_root = Path(__file__).resolve().parent.parent
    manager_dir = project_root / "manager"
    if manager_dir.is_dir() and str(manager_dir) not in sys.path:
        sys.path.insert(0, str(manager_dir))
    from workers.multicast_broadcaster import MulticastBroadcaster
    return MulticastBroadcaster


def read_broadcaster_params(manager_data_dir: Path) -> Optional[dict]:
    """Read the broadcaster params file; return ``None`` if absent/invalid.

    Safe to call repeatedly from a poll loop. Parse failures are
    logged but never raise — the manager may be in the middle of
    writing, or the file may be missing during a not-yet-up window.
    """
    target = manager_data_dir / BROADCASTER_PARAMS_FILENAME
    if not target.exists():
        return None
    try:
        with open(target, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.debug(
            "Broadcaster params file transiently unreadable at %s",
            target,
            exc_info=True,
        )
        return None


class BroadcasterSupervisor:
    """Own the MulticastBroadcaster thread lifecycle in the launcher.

    Single-use: call :meth:`start_from_params` once the manager has
    published its params file, and :meth:`stop` on shutdown.
    """

    def __init__(self) -> None:
        self._broadcaster = None
        self._lock = threading.Lock()

    def start_from_params(self, params: dict) -> None:
        """Instantiate the broadcaster and start its thread.

        :param params: Mapping as published by
            ``runtime_init._publish_broadcaster_params``. Must contain
            ``manager_id``, ``name``, ``host``, ``ip``, ``port``,
            ``version`` keys.
        :raises RuntimeError: Broadcaster already started or the
            manager params lacked required keys.
        """
        with self._lock:
            if self._broadcaster is not None:
                raise RuntimeError(
                    "BroadcasterSupervisor already started"
                )
            required = ("manager_id", "name", "host", "ip", "port",
                        "version")
            missing = [k for k in required if k not in params]
            if missing:
                raise RuntimeError(
                    f"broadcaster_params.json missing keys: {missing}"
                )
            MulticastBroadcaster = _load_multicast_broadcaster_cls()
            self._broadcaster = MulticastBroadcaster(
                manager_id=params["manager_id"],
                name=params["name"],
                host=params["host"],
                ip=params["ip"],
                port=int(params["port"]),
                version=params["version"],
            )
            self._broadcaster.start()
            logger.info(
                "Launcher started MulticastBroadcaster for manager "
                "%s at %s:%s",
                params["manager_id"], params["ip"], params["port"],
            )

    def stop(self, join_timeout: float = 5.0) -> None:
        """Stop and join the broadcaster thread.

        Follows the spec's 5-second join-timeout contract. If the
        thread doesn't exit in time, we log and return — the launcher
        proceeds with Caddy / child teardown and the daemon thread
        is reaped on process exit (bounded by the launcher's total
        ~20 s shutdown budget).
        """
        with self._lock:
            broadcaster = self._broadcaster
            self._broadcaster = None
        if broadcaster is None:
            return
        try:
            broadcaster.stop()
        except Exception:
            logger.exception(
                "MulticastBroadcaster.stop() raised; continuing "
                "shutdown"
            )
        try:
            broadcaster.join(timeout=join_timeout)
        except Exception:
            logger.exception(
                "MulticastBroadcaster.join() raised; continuing "
                "shutdown"
            )
        if broadcaster.is_alive():
            logger.warning(
                "MulticastBroadcaster did not exit within %.1fs; "
                "leaking (daemon thread, OS-reaped on process exit)",
                join_timeout,
            )

    def is_running(self) -> bool:
        """Return True if the broadcaster thread has been started."""
        with self._lock:
            return self._broadcaster is not None
