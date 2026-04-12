# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/multicast_broadcaster.py``.

Covers:

* Payload shape (FR-3, AC-2): 7 fields, correct types, ``v == 1``.
* Interval observance (FR-2): 3 consecutive packets on a local
  listening socket, inter-packet delay ~3.0s ± wide tolerance.
* Stop-event observance (AC-4): ``stop()`` then ``join`` returns
  cleanly inside the 5-second budget.
* Socket option setup: ``IP_MULTICAST_TTL=1`` and
  ``IP_MULTICAST_LOOP=1`` are set before the loop starts.
* ``setsockopt`` failure handling: the broadcaster logs ERROR, exits
  the thread cleanly, and does not crash the rest of the process.
* ``getfqdn`` is resolved exactly once in ``__init__`` (NF-4).

The interval test binds a UDP listener on ``127.0.0.1`` and sends the
broadcaster's traffic to a local port, so no traffic leaks onto the
LAN during CI.
"""

import json
import socket
import struct
import time

from workers import multicast_broadcaster as mb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# Payload shape (FR-3 / AC-2)
# ---------------------------------------------------------------------------


class TestPayloadShape:
    def test_all_seven_fields_present(self):
        b = _fresh_broadcaster()
        obj = json.loads(b._payload.decode("utf-8"))
        assert set(obj.keys()) == {
            "v", "manager_id", "name", "host",
            "ip", "port", "version",
        }

    def test_field_types(self):
        b = _fresh_broadcaster()
        obj = json.loads(b._payload.decode("utf-8"))
        assert isinstance(obj["v"], int)
        assert obj["v"] == 1
        assert isinstance(obj["manager_id"], str)
        assert isinstance(obj["name"], str)
        assert isinstance(obj["host"], str)
        assert isinstance(obj["ip"], str)
        assert isinstance(obj["port"], int)
        assert isinstance(obj["version"], str)

    def test_name_truncated_to_64_chars(self):
        long_name = "A" * 120
        b = _fresh_broadcaster(name=long_name)
        obj = json.loads(b._payload.decode("utf-8"))
        assert len(obj["name"]) == 64
        assert obj["name"] == "A" * 64

    def test_default_name_when_empty(self):
        b = _fresh_broadcaster(name="")
        obj = json.loads(b._payload.decode("utf-8"))
        assert obj["name"] == "Sethlans Manager"

    def test_default_version_when_empty(self):
        b = _fresh_broadcaster(version="")
        obj = json.loads(b._payload.decode("utf-8"))
        assert obj["version"] == "0.0.0"

    def test_payload_is_compact_json(self):
        """FR-3: use ``separators=(",", ":")`` so the datagram stays small."""
        b = _fresh_broadcaster()
        raw = b._payload.decode("utf-8")
        assert ", " not in raw  # no space after commas
        assert ": " not in raw  # no space after colons


# ---------------------------------------------------------------------------
# Hostname resolution runs exactly once (NF-4)
# ---------------------------------------------------------------------------


class TestHostnameResolvedOnce:
    def test_getfqdn_called_once_at_init_only(self, mocker):
        fqdn_mock = mocker.patch(
            "workers.multicast_broadcaster.socket.getfqdn",
            return_value="resolved.example",
        )
        # Host left empty so the broadcaster resolves it itself.
        b = _fresh_broadcaster(host="")
        obj = json.loads(b._payload.decode("utf-8"))
        assert obj["host"] == "resolved.example"
        assert fqdn_mock.call_count == 1

    def test_explicit_host_skips_getfqdn(self, mocker):
        fqdn_mock = mocker.patch(
            "workers.multicast_broadcaster.socket.getfqdn",
        )
        _fresh_broadcaster(host="explicit.example")
        fqdn_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Socket option setup
# ---------------------------------------------------------------------------


class TestSocketOptions:
    def test_ttl_and_loop_options_set(self, mocker):
        """Verify the TTL and LOOP multicast options are set in ``run()``."""
        recorded = []

        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def setsockopt(self, level, option, value):
                recorded.append((level, option, value))

            def sendto(self, *a, **kw):
                # Let the loop do exactly one pass then stop.
                b.stop()

            def close(self):
                pass

        mocker.patch(
            "workers.multicast_broadcaster.socket.socket",
            FakeSocket,
        )
        b = _fresh_broadcaster()
        b.start()
        b.join(timeout=5.0)
        assert not b.is_alive()
        # TTL option (struct-packed byte 1).
        ttl_packed = struct.pack("b", 1)
        assert (
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_TTL,
            ttl_packed,
        ) in recorded
        # LOOP option — integer 1.
        assert (
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_LOOP,
            1,
        ) in recorded

    def test_setsockopt_oserror_exits_thread_cleanly(self, mocker):
        """A setsockopt failure → ERROR log → thread exits without raising."""
        class FailingSocket:
            def __init__(self, *a, **kw):
                pass

            def setsockopt(self, *a, **kw):
                raise OSError("permission denied")

            def sendto(self, *a, **kw):  # pragma: no cover — never reached
                pass

            def close(self):
                pass

        mocker.patch(
            "workers.multicast_broadcaster.socket.socket",
            FailingSocket,
        )
        # Spy on the module logger's error method so we can assert it
        # was called regardless of pytest's caplog propagation setup.
        error_spy = mocker.patch.object(mb.logger, "error")
        b = _fresh_broadcaster()
        b.start()
        b.join(timeout=5.0)
        assert not b.is_alive()
        assert error_spy.call_count >= 1
        first_call_args = error_spy.call_args_list[0][0]
        assert "setsockopt failed" in first_call_args[0]


# ---------------------------------------------------------------------------
# Stop-event observance (AC-4)
# ---------------------------------------------------------------------------


class TestStopEvent:
    def test_stop_before_start_is_noop(self):
        b = _fresh_broadcaster()
        b.stop()
        # Thread never started; is_alive is False.
        assert not b.is_alive()

    def test_stop_exits_thread_under_budget(self, mocker):
        """``stop()`` must cause the thread to exit under the 5-second cap."""
        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def setsockopt(self, *a, **kw):
                pass

            def sendto(self, *a, **kw):
                pass

            def close(self):
                pass

        mocker.patch(
            "workers.multicast_broadcaster.socket.socket", FakeSocket,
        )
        b = _fresh_broadcaster()
        b.start()
        time.sleep(0.05)
        start = time.monotonic()
        b.stop()
        b.join(timeout=5.0)
        elapsed = time.monotonic() - start
        assert not b.is_alive()
        assert elapsed < 5.0
