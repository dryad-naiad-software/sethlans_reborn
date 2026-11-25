# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Response headers for every setup-mode response (FR-SM-8), and the host check.

Django checks ``ALLOWED_HOSTS`` only when something asks for the host, so
the middleware asks first. A host that is not allowed gets a JSON 400
``{"error": "bad_host", ...}`` with the same headers (logged at WARNING),
instead of Django's HTML page. Static files served by WhiteNoise get the
same headers through :func:`add_static_headers`.
"""
import logging

from django.core.exceptions import DisallowedHost
from django.http import JsonResponse

logger = logging.getLogger(__name__)

SECURITY_HEADERS = (
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)
MAX_LOGGED_HOST = 100


def _add_headers(request, response):
    for name, value in SECURITY_HEADERS:
        response[name] = value
    if request.path.startswith("/api/") or request.path == "/setup/":
        response["Cache-Control"] = "no-store"
    return response


class SecurityHeadersMiddleware:
    """Frame and sniffing headers everywhere; ``Cache-Control: no-store`` on the API."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            request.get_host()
        except DisallowedHost:
            host = request.META.get("HTTP_HOST", "")[:MAX_LOGGED_HOST]
            logger.warning("Refused a request for the host %r (not one of this machine's names)", host)
            response = JsonResponse({"error": "bad_host",
                                     "message": "This host name is not one of this machine's names."}, status=400)
            return _add_headers(request, response)
        return _add_headers(request, self.get_response(request))


def add_static_headers(headers, path, url) -> None:
    """WhiteNoise ``add_headers_function``: the same frame headers on files."""
    for name, value in SECURITY_HEADERS:
        headers[name] = value
