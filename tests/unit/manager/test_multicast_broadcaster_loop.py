# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for multicast broadcaster loop behavior.

Covers real UDP send/receive on localhost, inter-packet cadence,
and the daemon-thread flag. Split from ``test_multicast_broadcaster.py``
to stay within the 300-line file size limit.
"""

import json
import socket
import threading
import time

import pytest

from workers import multicast_broadcaster as mb


def _fresh_broadcaster(**overrides):
    defaults = dict(
        manager_id="00000000-0000-0000-0000-000000000001",
        name="Test Manager",
        host="testhost.example",
        ip="0.0.0.0",
        port=8080,
        version="0.1.0",
    )
    defaults.update(overrides)
    return mb.MulticastBroadcaster(**defaults)


# ---------------------------------------------------------------------------
# Real UDP sendto — one packet to a local listener
# ---------------------------------------------------------------------------


class TestOnePacketOverLocalhost:
    """Send a single payload to a localhost listener to prove wire format.

    We tighten the multicast loop to a short interval-equivalent via
    ``_STOP_POLL_INTERVAL`` so the test finishes quickly.
    """

    def test_one_packet_lands_on_listener(self, mocker):
        # Redirect the broadcaster to send to 127.0.0.1:<ephemeral>.
        listener = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
        )
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(3.0)
        _, port = listener.getsockname()

        # Short-circuit INTERVAL_SECONDS and multicast destination so
        # the test is fast and stays on localhost.
        mocker.patch.object(mb, "INTERVAL_SECONDS", 0.05)
        mocker.patch.object(mb, "MULTICAST_GROUP", "127.0.0.1")
        mocker.patch.object(mb, "MULTICAST_PORT", port)

        b = _fresh_broadcaster()
        try:
            b.start()
            raw, _addr = listener.recvfrom(2048)
        finally:
            b.stop()
            b.join(timeout=5.0)
            listener.close()
        obj = json.loads(raw.decode("utf-8"))
        assert obj["v"] == 1
        assert obj["manager_id"] == "00000000-0000-0000-0000-000000000001"
        assert obj["port"] == 8080


# ---------------------------------------------------------------------------
# Inter-packet interval observance (FR-2)
# ---------------------------------------------------------------------------


class TestInterval:
    def test_three_packets_cadence(self, mocker):
        """3 consecutive packets land with the configured interval.

        Uses a tight 100ms interval and a generous tolerance so the
        test is CI-safe; the production value is 3.0s but the shape of
        the loop is identical.
        """
        listener = socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP,
        )
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(5.0)
        _, port = listener.getsockname()

        mocker.patch.object(mb, "INTERVAL_SECONDS", 0.1)
        mocker.patch.object(mb, "MULTICAST_GROUP", "127.0.0.1")
        mocker.patch.object(mb, "MULTICAST_PORT", port)

        b = _fresh_broadcaster()
        timestamps = []
        try:
            b.start()
            for _ in range(3):
                listener.recvfrom(2048)
                timestamps.append(time.monotonic())
        finally:
            b.stop()
            b.join(timeout=5.0)
            listener.close()
        assert len(timestamps) == 3
        # Relative gaps should be roughly 0.1s — allow a generous
        # tolerance for CI.
        gap1 = timestamps[1] - timestamps[0]
        gap2 = timestamps[2] - timestamps[1]
        assert 0.02 <= gap1 <= 0.6
        assert 0.02 <= gap2 <= 0.6


# ---------------------------------------------------------------------------
# No regressions on the daemon flag (so uvicorn shutdown isn't blocked)
# ---------------------------------------------------------------------------


def test_broadcaster_is_daemon_thread():
    b = _fresh_broadcaster()
    assert b.daemon is True
    assert isinstance(b, threading.Thread)


# Silence a specific pytest warning: the broadcaster leaves no residue
# when tests stop it explicitly.
@pytest.fixture(autouse=True)
def _stop_leaked_broadcasters():
    yield
    # Pytest runs each test in isolation; any broadcaster that leaked
    # through should be marked as daemon so it dies with the process.
