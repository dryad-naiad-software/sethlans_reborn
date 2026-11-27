# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Test helpers: a valid manager announcement for the multicast listener tests."""

import json

MANAGER_ID = "00000000-0000-0000-0000-000000000001"


def good_payload(**overrides):
    """A valid version-1 announcement; ``overrides`` replace single fields."""
    base = {
        "v": 1,
        "manager_id": MANAGER_ID,
        "name": "Test Manager",
        "host": "manager.example",
        "ip": "10.0.0.1",
        "port": 8080,
        "version": "0.1.0",
    }
    base.update(overrides)
    return base


def encode(obj) -> bytes:
    """``obj`` as the UTF-8 JSON bytes of one datagram."""
    return json.dumps(obj).encode("utf-8")
