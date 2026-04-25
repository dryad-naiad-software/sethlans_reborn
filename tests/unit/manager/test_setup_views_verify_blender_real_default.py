# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression test for issue #136: ``_check_blender`` must read the
correct attribute (``resolved_version``) from the
``SupportedBlenderVersion`` model.  The prior production code
referenced the non-existent ``.version`` attribute, but unit tests
papered over the bug by stubbing the queryset with a ``MagicMock``
that returned truthy garbage for any attribute access.

The test below builds a *real* ``SupportedBlenderVersion`` row via
the Django ORM (no attribute-access mock) so any typo on the
production side surfaces as an actual ``AttributeError`` instead of
being silently swallowed.
"""

from __future__ import annotations

import pytest

from workers.models import SupportedBlenderVersion
from workers.services.sentinel import write_sentinel
from workers.views.setup_verify_checks import _check_blender


def _write_blender_predownloaded_sentinel(data_dir):
    """Write a sentinel that records the blender_predownloaded gate."""
    write_sentinel(
        data_dir,
        {
            "version": 1,
            "completed_at": None,
            "topology": "manager_worker",
            "checkpoints": ["blender_predownloaded"],
        },
    )


@pytest.mark.django_db
def test_check_blender_happy_path_with_real_default_version(
    tmp_path, mocker,
):
    """Happy path with a real ``SupportedBlenderVersion`` ORM row.

    This test deliberately does NOT mock the
    ``SupportedBlenderVersion`` queryset.  It creates a real DB row
    and lets ``_check_blender`` read ``resolved_version`` directly
    from it — the exact attribute access path that broke in #136.

    Only the on-disk side-effects (filesystem probes and the
    blender subprocess) are mocked, so the production attribute
    name MUST match the model or the test errors out with
    ``AttributeError``.
    """
    # Clean any migration-seeded versions so we control the default.
    SupportedBlenderVersion.objects.all().delete()
    default = SupportedBlenderVersion.objects.create(
        series="4.2",
        resolved_version="4.2.19",
        is_default=True,
    )
    # Sanity check — the field really is resolved_version.
    assert default.resolved_version == "4.2.19"

    _write_blender_predownloaded_sentinel(tmp_path)

    mocker.patch(
        "workers.views.setup_verify_checks.blender_already_installed",
        return_value=True,
    )
    fake_binary = tmp_path / "blender.exe"
    mock_find = mocker.patch(
        "workers.views.setup_verify_checks._find_blender_binary",
        return_value=fake_binary,
    )
    mock_verify = mocker.patch(
        "workers.views.setup_verify_checks.verify_blender_runs",
        return_value="Blender 4.2.19",
    )

    result = _check_blender(tmp_path)

    assert result == {
        "name": "blender",
        "passed": True,
        "error": None,
    }
    mock_find.assert_called_once()
    mock_verify.assert_called_once_with(fake_binary)
