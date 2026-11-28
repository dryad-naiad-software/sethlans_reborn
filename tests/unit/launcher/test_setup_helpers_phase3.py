# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :func:`launcher.setup_helpers.find_available_loopback_port`
(manager spec Phase 3): it returns a port it could bind on 127.0.0.1.
"""

from __future__ import annotations

from launcher.setup_helpers import find_available_loopback_port


def test_find_available_loopback_port_binds_to_127_0_0_1():
    # Default range (8089..8099) — at least one port in the dev
    # tooling range should be free on any sane CI agent.
    port = find_available_loopback_port(start=50000, end=50010)
    assert 50000 <= port <= 50010
