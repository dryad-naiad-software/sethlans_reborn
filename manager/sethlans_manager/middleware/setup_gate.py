# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-mode gate middleware.

When the ``.setup_complete`` sentinel is absent AND no superuser exists,
all requests not matching allowed prefixes are redirected to ``/setup/``
(browser) or receive HTTP 503 (API).  Once setup completes, the
module-level boolean flips to ``True`` and the middleware becomes a
passthrough for the remainder of the process lifetime.

**Setup token validation (FR-L6):** POST requests to ``/api/setup/``
must include a valid ``X-Setup-Token`` header.  The expected token is
read from ``manager.ini [setup] token``.  GET requests to setup
endpoints do not require the token.
"""

import configparser
import logging
from pathlib import Path

from django.http import JsonResponse
from django.shortcuts import redirect

from shared.frozen_paths import get_data_dir, is_frozen
from workers.services.sentinel import read_sentinel

logger = logging.getLogger(__name__)

# Module-level boolean — set once, never reset.  Under ASGI with a
# single uvicorn worker (required during setup), this is safe.
_setup_complete: bool = False

# Allowed URL prefixes during setup mode.
_ALLOWED_PREFIXES = (
    "/setup/",
    "/api/setup/",
    "/static/",
    "/media/",
)


def _get_data_dir() -> Path:
    """Return the manager data directory."""
    if is_frozen():
        return get_data_dir("manager")
    from django.conf import settings
    return settings.BASE_DIR


def _read_setup_token() -> str | None:
    """Read the setup token from ``manager.ini [setup] token``."""
    data_dir = _get_data_dir()
    ini_path = data_dir / "manager.ini"
    if not ini_path.exists():
        return None
    config = configparser.ConfigParser()
    config.read(ini_path)
    return config.get("setup", "token", fallback=None)


def _check_sentinel() -> bool:
    """Determine if setup is complete.

    Checks the sentinel file first.  Defense-in-depth: if the
    sentinel is missing but a superuser exists, refuse setup mode
    and log a critical warning.
    """
    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)
    if sentinel is not None:
        return True

    # Defense-in-depth (FR-G6): sentinel missing but superuser
    # exists means setup was completed and sentinel was deleted.
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


class SetupGateMiddleware:
    """Gate middleware that enforces setup completion.

    Added to ``MIDDLEWARE`` after ``SecurityMiddleware`` and before
    ``WhiteNoiseMiddleware``.
    """

    def __init__(self, get_response):
        global _setup_complete
        self.get_response = get_response
        if not _setup_complete:
            _setup_complete = _check_sentinel()

    def __call__(self, request):
        global _setup_complete

        # Fast path: setup already complete.
        if _setup_complete:
            return self.get_response(request)

        # Re-check sentinel (wizard may have just completed).
        if _check_sentinel():
            _setup_complete = True
            return self.get_response(request)

        # Allow requests to setup-related paths.
        if any(
            request.path.startswith(p) for p in _ALLOWED_PREFIXES
        ):
            return self._validate_setup_token(request)

        # Block API calls with 503.
        if request.path.startswith("/api/"):
            return JsonResponse(
                {"detail": "Setup not complete."}, status=503,
            )

        # Redirect browser requests to wizard.
        return redirect("/setup/")

    def _validate_setup_token(self, request):
        """Validate setup token on POST to /api/setup/ endpoints.

        GET requests pass through without token validation.
        """
        if (
            request.method == "POST"
            and request.path.startswith("/api/setup/")
        ):
            expected = _read_setup_token()
            if expected:
                provided = request.headers.get("X-Setup-Token", "")
                if provided != expected:
                    return JsonResponse(
                        {"detail": "Invalid setup token."},
                        status=403,
                    )
        return self.get_response(request)
