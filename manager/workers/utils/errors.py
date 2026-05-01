# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
DRF exception handler — defaults to the stock DRF behaviour.

The unified setup-error envelope was retired alongside the setup wizard
endpoints (Spec 2 cluster B1, FR-DEL2 / FR-DEL5 / FR-DEL6).  Any path
under ``/api/setup/*`` now resolves to a plain Django 404 because no
URL patterns are registered there; the handler below stays registered
in ``REST_FRAMEWORK['EXCEPTION_HANDLER']`` so the wiring point remains
in place for future error-handling needs but currently delegates
unmodified to DRF's ``exception_handler``.
"""

from rest_framework.views import (
    exception_handler as drf_default_exception_handler,
)


def setup_exception_handler(exc, context):
    """Stock DRF exception handler — kept as a stable wiring point."""
    return drf_default_exception_handler(exc, context)
