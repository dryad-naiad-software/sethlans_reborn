# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the broadcaster's host name lookup (#344, NF-4).

``socket.getfqdn`` is a reverse DNS lookup with no timeout. The launcher
builds and starts the broadcaster while holding its supervisor lock, so
the lookup runs on the broadcaster thread, once, before the first
announcement; a stop during the lookup sends nothing.
"""

import json
import threading
from types import SimpleNamespace

import pytest

from workers import multicast_broadcaster as mb

# Upper bound for every wait in this file; a passing run never reaches it.
_WAIT = 5.0


def _broadcaster(host):
    return mb.MulticastBroadcaster(
        manager_id="00000000-0000-0000-0000-000000000001",
        name="Test Manager",
        host=host,
        ip="0.0.0.0",
        port=8080,
        version="0.1.0",
    )


@pytest.fixture
def fake_socket(mocker):
    """Stand-in UDP socket recording sockets made and datagrams sent."""
    record = SimpleNamespace(created=0, sent=[], first_send=threading.Event())

    class FakeSocket:
        def __init__(self, *args, **kwargs):
            record.created += 1

        def setsockopt(self, *args, **kwargs):
            pass

        def sendto(self, data, address):
            record.sent.append(data)
            record.first_send.set()

        def close(self):
            pass

    mocker.patch("workers.multicast_broadcaster.socket.socket", FakeSocket)
    return record


@pytest.fixture
def blocking_lookup(mocker):
    """``socket.getfqdn`` that blocks until ``release`` is set."""
    lookup = SimpleNamespace(
        entered=threading.Event(), release=threading.Event(), threads=[],
    )

    def getfqdn(name=""):
        lookup.threads.append(threading.current_thread())
        lookup.entered.set()
        lookup.release.wait(_WAIT)
        return "slow.example"

    mocker.patch("workers.multicast_broadcaster.socket.getfqdn", side_effect=getfqdn)
    yield lookup
    lookup.release.set()


def _finish(broadcaster):
    broadcaster.stop()
    broadcaster.join(timeout=_WAIT)
    assert not broadcaster.is_alive()


def _sent_host(record):
    return json.loads(record.sent[0].decode("utf-8"))["host"]


def test_construction_and_start_do_not_wait_on_lookup(fake_socket, blocking_lookup):
    """#344: building and starting return while the lookup is still blocked."""
    b = _broadcaster(host="")
    try:
        b.start()
        assert blocking_lookup.entered.wait(_WAIT)
        # The lookup runs on the broadcaster thread, never the caller's.
        assert blocking_lookup.threads == [b]
        assert fake_socket.sent == []
        blocking_lookup.release.set()
        assert fake_socket.first_send.wait(_WAIT)
    finally:
        blocking_lookup.release.set()
        _finish(b)


@pytest.mark.parametrize(
    ("lookup", "expected"),
    [
        ({"return_value": "resolved.example"}, "resolved.example"),
        ({"side_effect": OSError("lookup failed")}, "short-name"),
        ({"side_effect": UnicodeError("label too long")}, "short-name"),
    ],
    ids=["fqdn", "oserror-falls-back-to-hostname", "unicodeerror-falls-back-to-hostname"],
)
def test_payload_uses_name_resolved_once(mocker, fake_socket, lookup, expected):
    """#344, NF-4: one lookup on the thread; its name (or gethostname on any lookup error) is sent.

    ``getfqdn`` raises UnicodeError for a name label over 63 characters.
    """
    getfqdn = mocker.patch("workers.multicast_broadcaster.socket.getfqdn", **lookup)
    mocker.patch("workers.multicast_broadcaster.socket.gethostname", return_value="short-name")
    b = _broadcaster(host="")
    try:
        b.start()
        assert fake_socket.first_send.wait(_WAIT)
    finally:
        _finish(b)
    assert _sent_host(fake_socket) == expected
    assert getfqdn.call_count == 1


def test_stop_during_lookup_sends_nothing(fake_socket, blocking_lookup):
    """#344: a stop while the lookup blocks ends the thread with no socket or announcement."""
    b = _broadcaster(host="")
    try:
        b.start()
        assert blocking_lookup.entered.wait(_WAIT)
        b.stop()
        blocking_lookup.release.set()
        b.join(timeout=_WAIT)
        assert not b.is_alive()
    finally:
        blocking_lookup.release.set()
        _finish(b)
    assert fake_socket.created == 0
    assert fake_socket.sent == []


def test_known_host_never_looks_up(mocker, fake_socket):
    """#344, NF-4: a published host is sent as is, with no name lookup."""
    getfqdn = mocker.patch("workers.multicast_broadcaster.socket.getfqdn")
    b = _broadcaster(host="explicit.example")
    try:
        b.start()
        assert fake_socket.first_send.wait(_WAIT)
    finally:
        _finish(b)
    getfqdn.assert_not_called()
    assert _sent_host(fake_socket) == "explicit.example"
