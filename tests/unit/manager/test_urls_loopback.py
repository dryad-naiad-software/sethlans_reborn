# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``sethlans_manager.urls_loopback`` (FR-22a)."""

from __future__ import annotations


class TestUrlsLoopbackModule:

    def test_importable(self):
        import sethlans_manager.urls_loopback as mod
        assert mod is not None

    def test_urlpatterns_defined(self):
        from sethlans_manager import urls_loopback
        assert hasattr(urls_loopback, "urlpatterns")
        assert isinstance(urls_loopback.urlpatterns, list)
        assert len(urls_loopback.urlpatterns) >= 1

    def test_only_registers_status_public_path(self):
        from sethlans_manager import urls_loopback
        # The loopback URLconf must be minimal (defense in depth).
        paths = [p.pattern.describe() for p in urls_loopback.urlpatterns]
        joined = " ".join(paths)
        assert "api/status/public/" in joined
        # No other /api/ endpoints registered on the loopback listener.
        # Count entries: exactly one pattern.
        assert len(urls_loopback.urlpatterns) == 1

    def test_pattern_name_is_status_public(self):
        from django.urls import reverse
        # Reverse using the isolated urlconf (reverse accepts urlconf kw).
        assert reverse(
            "status-public",
            urlconf="sethlans_manager.urls_loopback",
        ) == "/api/status/public/"
