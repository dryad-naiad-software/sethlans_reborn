# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/services/checkpoints.py.

Regression guard against accidental string-value drift. The constant
values are the on-disk wire format (sentinel JSON ``checkpoints[]`` and
the frontend's ``setup-step-config.ts:checkpoint`` field). Renaming any
value would invalidate every existing sentinel and break in-flight
installations.
"""

import pytest

from workers.services import checkpoints


@pytest.mark.parametrize("name, value", [
    ("TOPOLOGY_CHOSEN", "topology_chosen"),
    ("NETWORK_CONFIGURED", "network_configured"),
    ("DATABASE_CONFIGURED", "database_configured"),
    ("ADMIN_CREATED", "admin_created"),
    ("WORKER_PASSWORD_SET", "worker_password_set"),
    ("FFMPEG_INSTALLED", "ffmpeg_installed"),
    ("BLENDER_PREDOWNLOADED", "blender_predownloaded"),
    ("VERIFIED", "verified"),
])
def test_checkpoint_constant_value(name, value):
    assert getattr(checkpoints, name) == value, (
        f"Renaming {name}'s value would invalidate every existing "
        f"sentinel on disk."
    )


def test_all_public_constants_are_uppercase_strings():
    public = {
        n: getattr(checkpoints, n)
        for n in dir(checkpoints)
        if n.isupper()
    }
    assert public, "Expected at least one uppercase public constant"
    for name, value in public.items():
        assert isinstance(value, str), f"{name} is not a str"
        assert value, f"{name} is empty"
