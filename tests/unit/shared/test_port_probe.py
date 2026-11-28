# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`shared.port_probe` (issue #350, FR-LN-3, setup mode's port check)."""

from __future__ import annotations

import socket

import pytest

from shared.port_probe import port_is_free


def _ipv6_loopback_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.parametrize("family, address", [
    pytest.param(socket.AF_INET, "127.0.0.1", id="ipv4-loopback"),
    pytest.param(socket.AF_INET6, "::1", id="ipv6-loopback"),
])
def test_a_loopback_listener_makes_the_any_address_busy(family, address):
    """Issue #350: a listener on 127.0.0.1 or ::1 alone must make the port busy
    for Caddy's any-address listener; when it closes, the port is free again."""
    if family == socket.AF_INET6 and not _ipv6_loopback_available():
        pytest.skip("no IPv6 loopback on this host")
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((address, 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert port_is_free("0.0.0.0", port) is False
        assert port_is_free(address, port) is False
    assert port_is_free("0.0.0.0", port) is True
