# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``worker/sethlans_worker_agent/multicast_listener.py``.

These tests exercise the listener's parsing logic via the
``MulticastListener._parse_into`` classmethod directly, so the tests
don't need a real multicast socket.  The ``discover()`` method is
covered by a separate socket-option and timeout test that uses
``mocker`` to replace the real socket.

AC coverage:
  * AC-5 — listener exits cleanly after timeout with empty dict
  * AC-6 — malformed datagrams dropped silently
  * FR-9 — duplicate manager_id deduped by most-recent-wins
  * FR-34 — wrong ``v`` warned once per ``(manager_id, v)`` pair
"""

import json
import logging
import socket
import struct

import pytest

from sethlans_worker_agent import multicast_listener as ml


@pytest.fixture(autouse=True)
def _reset_warned_versions():
    """Clear the module-level warn-once set between tests."""
    ml._warned_versions.clear()
    yield
    ml._warned_versions.clear()


def _good_payload(**overrides):
    base = {
        "v": 1,
        "manager_id": "00000000-0000-0000-0000-000000000001",
        "name": "Test Manager",
        "host": "manager.example",
        "ip": "10.0.0.1",
        "port": 8080,
        "version": "0.1.0",
    }
    base.update(overrides)
    return base


def _encode(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


# ---------------------------------------------------------------------------
# Parse path: happy + invalid shapes
# ---------------------------------------------------------------------------


class TestParseInto:
    def test_valid_payload_lands_in_accumulator(self):
        seen = {}
        ml.MulticastListener._parse_into(_encode(_good_payload()), seen)
        assert len(seen) == 1
        assert (
            seen["00000000-0000-0000-0000-000000000001"]["port"] == 8080
        )

    def test_invalid_json_silently_dropped(self):
        seen = {}
        ml.MulticastListener._parse_into(b"not-json-{", seen)
        assert seen == {}

    def test_non_utf8_bytes_dropped(self):
        seen = {}
        ml.MulticastListener._parse_into(b"\xff\xfe\x00", seen)
        assert seen == {}

    def test_non_object_dropped(self):
        seen = {}
        ml.MulticastListener._parse_into(b"[1,2,3]", seen)
        assert seen == {}

    @pytest.mark.parametrize(
        "missing",
        ["v", "manager_id", "name", "host", "ip", "port", "version"],
    )
    def test_missing_required_field_dropped(self, missing):
        seen = {}
        payload = _good_payload()
        del payload[missing]
        ml.MulticastListener._parse_into(_encode(payload), seen)
        assert seen == {}

    def test_empty_manager_id_dropped(self):
        seen = {}
        ml.MulticastListener._parse_into(
            _encode(_good_payload(manager_id="")), seen,
        )
        assert seen == {}

    def test_duplicate_manager_id_most_recent_wins(self):
        seen = {}
        # First announcement.
        ml.MulticastListener._parse_into(
            _encode(_good_payload(port=8080)), seen,
        )
        # Second announcement with the same manager_id but a different
        # port — the listener replaces the previous entry.
        ml.MulticastListener._parse_into(
            _encode(_good_payload(port=9090)), seen,
        )
        assert len(seen) == 1
        assert (
            seen["00000000-0000-0000-0000-000000000001"]["port"] == 9090
        )


# ---------------------------------------------------------------------------
# Version guard (FR-34)
# ---------------------------------------------------------------------------


class TestVersionGuard:
    @pytest.mark.parametrize("bad_v", [0, 2, 99, "1", "invalid"])
    def test_bad_version_dropped(self, bad_v):
        seen = {}
        ml.MulticastListener._parse_into(
            _encode(_good_payload(v=bad_v)), seen,
        )
        assert seen == {}

    def test_warning_logged_once_per_pair(self, mocker):
        """Same (manager_id, v) pair → warning fires exactly once."""
        warning_spy = mocker.patch.object(ml.logger, "warning")
        seen = {}
        bad = _good_payload(v=2)
        ml.MulticastListener._parse_into(_encode(bad), seen)
        ml.MulticastListener._parse_into(_encode(bad), seen)
        ml.MulticastListener._parse_into(_encode(bad), seen)
        assert warning_spy.call_count == 1

    def test_different_pairs_warn_separately(self, mocker):
        warning_spy = mocker.patch.object(ml.logger, "warning")
        seen = {}
        ml.MulticastListener._parse_into(
            _encode(_good_payload(v=2)), seen,
        )
        ml.MulticastListener._parse_into(
            _encode(_good_payload(
                v=3,
                manager_id="00000000-0000-0000-0000-000000000002",
            )), seen,
        )
        assert warning_spy.call_count == 2


# ---------------------------------------------------------------------------
# Discover path: timeout and socket options
# ---------------------------------------------------------------------------


class _FakeSocket:
    """Minimal stand-in for a UDP socket used by discover()."""

    def __init__(self):
        self.setsockopt_calls = []
        self.bound_to = None
        self.closed = False
        self.recv_queue: list = []
        self.select_will_return_ready = True

    def setsockopt(self, level, option, value):
        self.setsockopt_calls.append((level, option, value))

    def bind(self, addr):
        self.bound_to = addr

    def recvfrom(self, bufsize):
        if not self.recv_queue:
            raise BlockingIOError("no data")
        return self.recv_queue.pop(0)

    def close(self):
        self.closed = True


class TestDiscover:
    def test_timeout_returns_empty_dict(self, mocker):
        """Listener exits cleanly with an empty dict when no data arrives."""
        fake = _FakeSocket()
        mocker.patch.object(
            ml, "socket",
            mocker.MagicMock(
                socket=lambda *a, **kw: fake,
                inet_aton=socket.inet_aton,
                IPPROTO_IP=socket.IPPROTO_IP,
                IP_ADD_MEMBERSHIP=socket.IP_ADD_MEMBERSHIP,
                IP_DROP_MEMBERSHIP=socket.IP_DROP_MEMBERSHIP,
                SOL_SOCKET=socket.SOL_SOCKET,
                SO_REUSEADDR=socket.SO_REUSEADDR,
                AF_INET=socket.AF_INET,
                SOCK_DGRAM=socket.SOCK_DGRAM,
                IPPROTO_UDP=socket.IPPROTO_UDP,
                INADDR_ANY=socket.INADDR_ANY,
            ),
        )
        # hasattr(socket, "SO_REUSEPORT") is a module attribute
        # check — leave the real socket module attribute inspectable.
        # Because the patched ``ml.socket`` is a MagicMock,
        # ``hasattr`` on it always returns True.  The listener will
        # attempt SO_REUSEPORT; the fake socket's setsockopt silently
        # accepts the call.
        # Replace select.select to immediately report "no ready".
        mocker.patch(
            "sethlans_worker_agent.multicast_listener.select.select",
            return_value=([], [], []),
        )
        # Drive time forward so ``time.monotonic < deadline`` flips.
        clock = [1000.0]

        def monotonic():
            clock[0] += 0.5
            return clock[0]

        mocker.patch(
            "sethlans_worker_agent.multicast_listener.time.monotonic",
            side_effect=monotonic,
        )
        listener = ml.MulticastListener(timeout=1.0)
        result = listener.discover()
        assert result == {}
        assert fake.closed is True
        # SO_REUSEADDR was set.
        reuseaddr = (
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1,
        )
        assert reuseaddr in fake.setsockopt_calls
        # IP_ADD_MEMBERSHIP with the packed struct.
        mreq = struct.pack(
            "4sl",
            socket.inet_aton(ml.MULTICAST_GROUP),
            socket.INADDR_ANY,
        )
        assert (
            socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq,
        ) in fake.setsockopt_calls

    def test_datagram_parsed_into_result(self, mocker):
        """A single valid datagram lands in the returned dict."""
        fake = _FakeSocket()
        fake.recv_queue.append(
            (_encode(_good_payload()), ("127.0.0.1", 12345)),
        )
        mocker.patch(
            "sethlans_worker_agent.multicast_listener.socket.socket",
            return_value=fake,
        )
        # First call returns "ready", second returns "nothing",
        # which lets the loop exit after one datagram.
        ready_sequence = [([fake], [], []), ([], [], [])]
        mocker.patch(
            "sethlans_worker_agent.multicast_listener.select.select",
            side_effect=ready_sequence + [([], [], [])] * 10,
        )
        clock = [1000.0]

        def monotonic():
            clock[0] += 0.5
            return clock[0]

        mocker.patch(
            "sethlans_worker_agent.multicast_listener.time.monotonic",
            side_effect=monotonic,
        )
        listener = ml.MulticastListener(timeout=2.0)
        result = listener.discover()
        assert len(result) == 1
        assert (
            result["00000000-0000-0000-0000-000000000001"]["v"] == 1
        )


# ---------------------------------------------------------------------------
# Silence the ``logger`` warnings we trigger on purpose
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _quiet_logger():
    logging.getLogger("sethlans_worker_agent.multicast_listener").setLevel(
        logging.CRITICAL,
    )
    yield
