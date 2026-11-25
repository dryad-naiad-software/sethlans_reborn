# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Response headers for every setup-mode response (FR-SM-8).

Static files served by WhiteNoise get the same headers through
:func:`add_static_headers`.
"""

SECURITY_HEADERS = (
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)


class SecurityHeadersMiddleware:
    """Frame and sniffing headers everywhere; ``Cache-Control: no-store`` on the API."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Django checks ALLOWED_HOSTS only when something asks for the
        # host; ask first, so a request for another name gets 400.
        request.get_host()
        response = self.get_response(request)
        for name, value in SECURITY_HEADERS:
            response[name] = value
        if request.path.startswith("/api/") or request.path == "/setup/":
            response["Cache-Control"] = "no-store"
        return response


def add_static_headers(headers, path, url) -> None:
    """WhiteNoise ``add_headers_function``: the same frame headers on files."""
    for name, value in SECURITY_HEADERS:
        headers[name] = value
