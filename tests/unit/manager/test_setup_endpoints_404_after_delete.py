# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Defense-in-depth replacement for the deleted ``SetupGateMiddleware``
(Spec 2 cluster B1, FR-DEL4).

Once the manager is fully set up, every URL under ``/api/setup/*`` MUST
404 because the URL patterns no longer exist.  These unit-level checks
exercise the URL resolver directly so a future regression that
re-introduces a ``/api/setup/*`` route is caught before integration.
"""

from __future__ import annotations

import pytest
from django.urls import Resolver404, get_resolver

# A representative subset of every URL that pre-existed under the
# deleted ``urls_setup`` module.  Per FR-DEL10 the integration-level
# counterpart of this test exercises each path via the live URL resolver
# from a fully-set-up manager.
SETUP_PATHS = [
    "/api/setup/status/",
    "/api/setup/topology/",
    "/api/setup/network/",
    "/api/setup/database/",
    "/api/setup/admin-user/",
    "/api/setup/worker-password/",
    "/api/setup/ffmpeg/start/",
    "/api/setup/ffmpeg/progress/abc123/",
    "/api/setup/ffmpeg/cancel/",
    "/api/setup/blender/start/",
    "/api/setup/blender/progress/abc123/",
    "/api/setup/blender/cancel/",
    "/api/setup/verify/",
    "/api/setup/summary/",
]


@pytest.mark.parametrize("path", SETUP_PATHS)
def test_setup_path_does_not_resolve(path):
    """The URL resolver MUST raise ``Resolver404`` for every setup path.

    The wizard is now standalone; no manager URL pattern starts with
    ``/api/setup/``.  Any route that resolves here is a regression.
    """
    resolver = get_resolver()
    with pytest.raises(Resolver404):
        resolver.resolve(path)


def test_no_workers_urls_setup_module():
    """``workers.urls_setup`` is gone (FR-DEL3)."""
    with pytest.raises(ImportError):
        import workers.urls_setup  # noqa: F401


def test_no_setup_gate_middleware_module():
    """``sethlans_manager.middleware.setup_gate`` is gone (FR-DEL4)."""
    with pytest.raises(ImportError):
        from sethlans_manager.middleware import setup_gate  # noqa: F401
