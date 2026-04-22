# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for Django TLS-related settings.

Verifies that the transport security settings are correctly applied
in the Django configuration: secure cookies, HSTS, and WSGI
application wiring. These tests read the live Django settings to
confirm the values are active in the test environment.
"""

from django.conf import settings


class TestSecureCookieSettings:
    """Verify secure cookie flags are set for HTTPS-only transport."""

    def test_session_cookie_secure_is_true(self):
        """SESSION_COOKIE_SECURE must be True (FR-16)."""
        assert settings.SESSION_COOKIE_SECURE is True

    def test_csrf_cookie_secure_is_true(self):
        """CSRF_COOKIE_SECURE must be True (FR-17)."""
        assert settings.CSRF_COOKIE_SECURE is True

    def test_session_cookie_httponly_is_true(self):
        """SESSION_COOKIE_HTTPONLY should remain True."""
        assert settings.SESSION_COOKIE_HTTPONLY is True

    def test_session_cookie_samesite_is_lax(self):
        """SESSION_COOKIE_SAMESITE should be Lax."""
        assert settings.SESSION_COOKIE_SAMESITE == 'Lax'


class TestHSTSSettings:
    """Verify HTTP Strict Transport Security headers are configured."""

    def test_hsts_seconds_is_one_year(self):
        """SECURE_HSTS_SECONDS should be 31536000 (1 year)."""
        assert settings.SECURE_HSTS_SECONDS == 31536000

    def test_hsts_include_subdomains_is_false(self):
        """SECURE_HSTS_INCLUDE_SUBDOMAINS should be False.

        This is a LAN appliance, not a public website with subdomains.
        """
        assert settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is False

    def test_hsts_preload_is_false(self):
        """SECURE_HSTS_PRELOAD should be False.

        Self-signed certs cannot be preloaded in browser HSTS lists.
        """
        assert settings.SECURE_HSTS_PRELOAD is False


class TestWSGIConfiguration:
    """Verify the WSGI application is configured for Waitress."""

    def test_wsgi_application_is_set(self):
        """WSGI_APPLICATION must point to the Django WSGI module."""
        assert settings.WSGI_APPLICATION == (
            'sethlans_manager.wsgi.application'
        )

    def test_asgi_application_not_set(self):
        """Phase 7 removed ASGI_APPLICATION."""
        assert not hasattr(settings, 'ASGI_APPLICATION')
