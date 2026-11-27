# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker-side multicast discovery listener (FR-8, FR-9, FR-34).

Wizard-only UDP multicast receiver. Joins ``239.150.74.50:8082`` for a
bounded window, accumulates unique announcements keyed by
``manager_id`` (most recent wins; at most ``MAX_MANAGERS`` ids, the
first heard; an id is printable text of at most 64 characters), and
drops malformed or wrong-version datagrams with a debug / warning log.

The listener is NOT started during normal operation — only from the
first-run wizard. The socket is closed in a ``finally`` block.
"""

import json
import logging
import select
import socket
import struct
import time
from typing import Dict

logger = logging.getLogger(__name__)

MULTICAST_GROUP = "239.150.74.50"
MULTICAST_PORT = 8082
LISTEN_TIMEOUT_SECONDS = 15.0
SUPPORTED_VERSION = 1
# At most this many managers are kept, the first ones heard (a flood of
# made-up ids cannot push real managers out); the setup form shows 32.
MAX_MANAGERS = 32
MAX_MANAGER_ID = 64
MAX_LOGGED = 80
MAX_WARNED = 64

REQUIRED_FIELDS = (
    "v", "manager_id", "name", "host", "ip", "port", "version",
)

# Track (manager_id, v) pairs we've already warned about so we only
# log unsupported versions once per pair within a single process.
_warned_versions: set = set()


class MulticastListener:
    """Bounded UDP multicast listener for wizard-time discovery."""

    def __init__(
        self,
        group: str = MULTICAST_GROUP,
        port: int = MULTICAST_PORT,
        timeout: float = LISTEN_TIMEOUT_SECONDS,
    ):
        self._group = group
        self._port = port
        self._timeout = timeout

    def discover(self) -> Dict[str, dict]:
        """Return a dict of announcements keyed by ``manager_id``.

        Exits after the timeout even if no datagrams arrived. Each
        iteration uses ``select`` so shutdown is observed within one
        iteration's worth of the per-call timeout.
        """
        seen: Dict[str, dict] = {}
        sock = self._open_socket()
        mreq = struct.pack(
            "4sl",
            socket.inet_aton(self._group),
            socket.INADDR_ANY,
        )
        try:
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                mreq,
            )
            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    ready, _, _ = select.select(
                        [sock], [], [], min(remaining, 1.0),
                    )
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    raw, _addr = sock.recvfrom(4096)
                except OSError as e:
                    logger.debug("Dropped multicast: recv error %s", e)
                    continue
                self._parse_into(raw, seen)
        finally:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_DROP_MEMBERSHIP,
                    mreq,
                )
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        return seen

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
        )
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_REUSEPORT, 1,
                )
            except (OSError, AttributeError):
                pass
        sock.bind(("0.0.0.0", self._port))
        return sock

    @staticmethod
    def _parse_into(raw: bytes, seen: dict) -> None:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.debug("Dropped multicast: parse error %s", e)
            return
        if not isinstance(obj, dict):
            logger.debug("Dropped multicast: not a JSON object")
            return
        for field in REQUIRED_FIELDS:
            if field not in obj:
                logger.debug(
                    "Dropped multicast: missing field %s", field,
                )
                return
        v = obj.get("v")
        if not isinstance(v, int) or v != SUPPORTED_VERSION:
            # Text, not the raw values: a JSON list or object is not hashable.
            pair = (repr(obj.get("manager_id"))[:MAX_LOGGED], repr(v)[:MAX_LOGGED])
            if pair not in _warned_versions and len(_warned_versions) < MAX_WARNED:
                _warned_versions.add(pair)
                logger.warning(
                    "Manager %.*r broadcasts protocol version %.*r; "
                    "worker understands version %d only — ignoring",
                    MAX_LOGGED, obj.get("manager_id"), MAX_LOGGED, v, SUPPORTED_VERSION,
                )
            return
        manager_id = obj.get("manager_id")
        if (not isinstance(manager_id, str) or not manager_id
                or len(manager_id) > MAX_MANAGER_ID or not manager_id.isprintable()):
            logger.debug("Dropped multicast: invalid manager_id")
            return
        if manager_id not in seen and len(seen) >= MAX_MANAGERS:
            logger.debug("Dropped multicast: already %d managers", MAX_MANAGERS)
            return
        seen[manager_id] = obj
