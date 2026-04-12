# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Daemon thread that emits UDP multicast discovery announcements.

The broadcaster runs exactly once per Python process.  It is started
and stopped from the uvicorn ASGI lifespan hooks in
``sethlans_manager/asgi.py`` — NOT from ``run_manager.py``.  That
indirection is what lets the broadcaster be silenced cleanly on
``SIGTERM`` (uvicorn owns signal handling after its event loop starts)
and what keeps it from running in the ``--dev`` reloader parent process.

See spec FR-1 through FR-7 for the lifecycle contract.
"""

import json
import logging
import socket
import struct
import threading

logger = logging.getLogger(__name__)

MULTICAST_GROUP = "239.150.74.50"
MULTICAST_PORT = 8082
INTERVAL_SECONDS = 3.0
PROTOCOL_VERSION = 1

# Granularity of the stop-event polling inside the sleep loop — small
# enough that ``join(timeout=5.0)`` from the lifespan ``on_shutdown`` hook
# always observes the stop in time, large enough that the broadcaster
# spends negligible CPU between ticks.
_STOP_POLL_INTERVAL = 0.5


class MulticastBroadcaster(threading.Thread):
    """Daemon thread that periodically emits discovery announcements."""

    def __init__(
        self,
        manager_id: str,
        name: str,
        host: str,
        ip: str,
        port: int,
        version: str,
    ):
        super().__init__(daemon=True, name="MulticastBroadcaster")
        self._stop_event = threading.Event()
        self._interface_ip = ip or "0.0.0.0"
        # Resolve the broadcast hostname ONCE (NF-4) — ``socket.getfqdn``
        # can make a DNS call and we don't want that on every tick.
        try:
            resolved_host = host or socket.getfqdn()
        except (socket.gaierror, OSError):
            resolved_host = host or socket.gethostname()
        self._payload = self._build_payload(
            manager_id=manager_id,
            name=name,
            host=resolved_host,
            ip=self._interface_ip,
            port=port,
            version=version,
        )

    @staticmethod
    def _build_payload(
        manager_id: str,
        name: str,
        host: str,
        ip: str,
        port: int,
        version: str,
    ) -> bytes:
        """Return the encoded announcement payload.

        ``name`` is truncated to 64 characters to keep the payload well
        under the default Ethernet MTU.
        """
        obj = {
            "v": PROTOCOL_VERSION,
            "manager_id": manager_id,
            "name": (name or "Sethlans Manager")[:64],
            "host": host,
            "ip": ip,
            "port": int(port),
            "version": version or "0.0.0",
        }
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")

    def run(self) -> None:
        sock = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
        )
        try:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_TTL,
                    struct.pack("b", 1),
                )
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_LOOP,
                    1,
                )
                if (
                    self._interface_ip
                    and self._interface_ip != "0.0.0.0"
                ):
                    sock.setsockopt(
                        socket.IPPROTO_IP,
                        socket.IP_MULTICAST_IF,
                        socket.inet_aton(self._interface_ip),
                    )
            except OSError as exc:
                logger.error(
                    "Multicast broadcaster setsockopt failed: %s — "
                    "discovery disabled", exc,
                )
                return

            logger.info(
                "Broadcasting on %s:%d via interface %s every %.1fs",
                MULTICAST_GROUP, MULTICAST_PORT,
                self._interface_ip, INTERVAL_SECONDS,
            )
            self._run_loop(sock)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _run_loop(self, sock: socket.socket) -> None:
        """Emit ``_payload`` every ``INTERVAL_SECONDS`` until stopped.

        The stop event is polled every ``_STOP_POLL_INTERVAL`` seconds so
        shutdown completes well under the 5-second budget in AC-4.
        """
        while not self._stop_event.is_set():
            try:
                sock.sendto(
                    self._payload,
                    (MULTICAST_GROUP, MULTICAST_PORT),
                )
            except OSError as exc:
                logger.warning("Multicast send failed: %s", exc)

            # Wait out the interval in short chunks, exiting early if
            # the stop event fires.
            remaining = INTERVAL_SECONDS
            while remaining > 0 and not self._stop_event.is_set():
                slice_ = min(_STOP_POLL_INTERVAL, remaining)
                if self._stop_event.wait(slice_):
                    break
                remaining -= slice_

    def stop(self) -> None:
        """Signal the loop to exit on its next stop-event check."""
        self._stop_event.set()
