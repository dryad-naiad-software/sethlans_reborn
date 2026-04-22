# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-mode gate middleware.

Responsibilities:

* While setup is incomplete (``.setup_complete`` sentinel absent and no
  superuser), browser routes are redirected to ``/setup/`` and non-
  allowlisted API routes return a unified envelope ``setup_in_progress``.
* Once setup completes, the gate flips to a post-setup role: all
  ``/api/setup/*`` paths return ``setup_complete`` (404) so stale
  wizard clients can't reach legacy endpoints.

Header-based token validation is gone — authentication for setup views
is now session-based (set by ``setup_bootstrap_view``).  See the
``setup-auth-unification`` spec for the new flow.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.http import JsonResponse
from django.shortcuts import redirect

from shared.frozen_paths import get_data_dir, is_frozen
from workers.services.sentinel import read_sentinel

logger = logging.getLogger(__name__)

# Module-level boolean — set once, never reset.  Thread-safe under
# Waitress's threaded WSGI model: the flag monotonically flips False
# → True exactly once per process, so concurrent reads cannot observe
# a torn value.
_setup_complete: bool = False

# Allowed URL prefixes during setup mode.  Bootstrap lives under
# /api/setup/ so the prefix covers it; the bootstrap view carries its
# own anonymous auth contract.
_ALLOWED_PREFIXES = (
    "/setup/",
    "/api/setup/",
    "/api/auth/csrf/",
    "/api/health/",
    # Tray helper's loopback-only status endpoint (tray-helper-unified
    # FR-22a/FR-23) — reachable only on the 127.0.0.1 listener, but
    # the middleware is shared across both listeners so it must be
    # allowlisted here to survive setup mode.
    "/api/status/public/",
    "/static/",
    "/media/",
)


def _get_data_dir() -> Path:
    """Return the manager data directory."""
    if is_frozen():
        return get_data_dir("manager")
    from django.conf import settings
    return settings.BASE_DIR


def _check_sentinel() -> bool:
    """Determine if setup is complete.

    Checks the sentinel file first.  Defense-in-depth: if the sentinel
    is missing but a superuser exists, refuse setup mode and log a
    critical warning.
    """
    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)
    if sentinel is not None and sentinel.get("completed_at"):
        return True

    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            logger.critical(
                "Sentinel missing but superuser exists -- "
                "refusing setup mode."
            )
            return True
    except Exception:
        pass  # DB not ready yet — allow setup mode

    return False


def _envelope_response(code: str, message: str, status: int) -> JsonResponse:
    """Render the unified setup error envelope from middleware.

    Middleware runs outside DRF, so we can't reuse ``setup_error``
    directly without pulling a Response renderer.  JsonResponse with
    the identical shape is sufficient.
    """
    return JsonResponse(
        {"error": {"code": code, "message": message, "details": {}}},
        status=status,
    )


class SetupGateMiddleware:
    """Gate middleware that enforces setup completion.

    Added to ``MIDDLEWARE`` after ``WhiteNoiseMiddleware`` so that
    static assets (Angular's root-served ``/main-*.js``, etc.) bypass
    the gate.  Unknown paths fall through to this middleware.
    """

    def __init__(self, get_response):
        global _setup_complete
        self.get_response = get_response
        if not _setup_complete:
            _setup_complete = _check_sentinel()

    def __call__(self, request):
        global _setup_complete

        # Fast path: setup already complete — but guard /api/setup/*.
        if _setup_complete:
            if request.path.startswith("/api/setup/"):
                return _envelope_response(
                    "setup_complete",
                    "Setup has already completed.",
                    404,
                )
            return self.get_response(request)

        # Re-check sentinel (wizard may have just completed).
        if _check_sentinel():
            _setup_complete = True
            if request.path.startswith("/api/setup/"):
                return _envelope_response(
                    "setup_complete",
                    "Setup has already completed.",
                    404,
                )
            return self.get_response(request)

        # Allow requests to setup-related paths (auth is handled by
        # the view / DRF permission class from here on).
        if any(
            request.path.startswith(p) for p in _ALLOWED_PREFIXES
        ):
            return self.get_response(request)

        # Block API calls with unified envelope.  Spec FR-3 / FR-12a
        # require 403 ``setup_in_progress`` so the Angular interceptor
        # can route to /setup via the envelope code; the old 503 path
        # leaked through as a generic "server unavailable".
        if request.path.startswith("/api/"):
            return _envelope_response(
                "setup_in_progress",
                "Setup has not completed.",
                403,
            )

        # Redirect browser requests to wizard.
        return redirect("/setup/")
