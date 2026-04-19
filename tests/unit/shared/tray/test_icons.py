# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/icons.py`` (FR-27 / FR-28)."""

from __future__ import annotations

import pytest

from shared.tray import icons

PIL_Image = pytest.importorskip("PIL.Image", reason="Pillow required")


@pytest.fixture(autouse=True)
def _reset_icon_cache():
    icons._CACHE.clear()
    yield
    icons._CACHE.clear()


class TestGetIconSingleAxis:

    def test_manager_only_returns_pil_image(self):
        img = icons.get_icon("running", None)
        assert img is not None
        # PIL Image has a size attribute.
        assert hasattr(img, "size")

    def test_worker_only_returns_pil_image(self):
        img = icons.get_icon(None, "rendering")
        assert img is not None
        assert hasattr(img, "size")

    def test_unknown_manager_state_falls_back_without_raise(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger=icons.logger.name):
            img = icons.get_icon("bogus", None)
        assert img is not None
        assert any(
            "Unknown manager_state" in rec.message
            for rec in caplog.records
        )

    def test_unknown_worker_state_falls_back_without_raise(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger=icons.logger.name):
            img = icons.get_icon(None, "bogus")
        assert img is not None
        assert any(
            "Unknown worker_state" in rec.message
            for rec in caplog.records
        )


class TestGetIconComposite:

    def test_composite_is_64x64(self):
        img = icons.get_icon("running", "rendering")
        assert img.size == (64, 64)

    def test_composite_cached(self):
        first = icons.get_icon("running", "rendering")
        second = icons.get_icon("running", "rendering")
        # Cached — identity match.
        assert first is second

    def test_different_keys_yield_different_entries(self):
        a = icons.get_icon("running", "rendering")
        b = icons.get_icon("error", "rendering")
        assert a is not b


class TestDefaultFallback:

    def test_both_none_returns_blank_image(self):
        img = icons.get_icon(None, None)
        assert img.size == (64, 64)
