# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/qt_icons.py`` (FR-27 / FR-28).

PySide6/QPixmap-based replacement for the legacy Pillow tray icon module.
These tests mirror the structure and invariants of ``test_icons.py`` but
assert on QPixmap semantics (size, nullness, pixel color via ``toImage()``)
instead of PIL ``Image`` attributes.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for qt_icons")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtGui import QColor, QPixmap  # noqa: E402

from shared.tray import qt_icons  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_icon_cache(qapp):
    """Clear the module-level cache between tests; ensure QGuiApplication exists."""
    qt_icons._CACHE.clear()
    yield
    qt_icons._CACHE.clear()


def _pixel(pm: QPixmap, x: int, y: int) -> QColor:
    return pm.toImage().pixelColor(x, y)


class TestSingleTopology:

    def test_manager_only_returns_64x64_qpixmap(self):
        pm = qt_icons.get_icon("running", None)
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert pm.width() == 64
        assert pm.height() == 64

    def test_worker_only_returns_64x64_qpixmap(self):
        pm = qt_icons.get_icon(None, "idle")
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert pm.width() == 64
        assert pm.height() == 64

    @pytest.mark.parametrize("state", ["starting", "running", "stopped", "error"])
    def test_every_manager_state_loads(self, state):
        pm = qt_icons.get_icon(state, None)
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)

    @pytest.mark.parametrize("state", ["rendering", "yielding", "yielded", "idle"])
    def test_every_worker_state_loads(self, state):
        pm = qt_icons.get_icon(None, state)
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)


class TestCompositeTopology:

    def test_composite_is_64x64(self):
        pm = qt_icons.get_icon("running", "rendering")
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)

    def test_composite_differs_from_single_axis_pixmaps(self):
        manager_only = qt_icons.get_icon("running", None)
        worker_only = qt_icons.get_icon(None, "rendering")
        # Clear cache so composite is not mistaken for either single axis.
        qt_icons._CACHE.clear()
        composite = qt_icons.get_icon("running", "rendering")

        # Compare a couple of control pixels — the composite must diverge
        # from at least one of the two single-axis pixmaps somewhere.
        manager_bytes = manager_only.toImage().bits().tobytes()
        worker_bytes = worker_only.toImage().bits().tobytes()
        composite_bytes = composite.toImage().bits().tobytes()
        assert composite_bytes != manager_bytes
        assert composite_bytes != worker_bytes

    def test_diagonal_split_differs_across_quadrants(self):
        pm = qt_icons.get_icon("running", "rendering")
        # Upper-left quadrant pixel should come from the manager asset;
        # lower-right from the worker asset. Avoid asserting exact colors
        # (brittle) — just confirm the two sampled pixels are not identical.
        ul = _pixel(pm, 16, 16)
        lr = _pixel(pm, 48, 48)
        assert ul != lr


class TestBothNoneFallback:

    def test_both_none_returns_opaque_grey(self):
        pm = qt_icons.get_icon(None, None)
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)
        center = _pixel(pm, 32, 32)
        assert center == QColor(128, 128, 128, 255)


class TestUnknownStateFallback:

    def test_unknown_manager_state_logs_and_returns_valid_pixmap(self, caplog):
        with caplog.at_level(logging.WARNING, logger=qt_icons.logger.name):
            pm = qt_icons.get_icon("bogus", None)
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)
        assert any(
            "Unknown manager_state='bogus'" in rec.getMessage()
            for rec in caplog.records
        )

    def test_unknown_worker_state_logs_and_returns_valid_pixmap(self, caplog):
        with caplog.at_level(logging.WARNING, logger=qt_icons.logger.name):
            pm = qt_icons.get_icon(None, "bogus")
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)
        assert any(
            "Unknown worker_state='bogus'" in rec.getMessage()
            for rec in caplog.records
        )

    def test_unknown_both_states_logs_both_and_returns_composite(self, caplog):
        with caplog.at_level(logging.WARNING, logger=qt_icons.logger.name):
            pm = qt_icons.get_icon("bogus", "bogus")
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("Unknown manager_state='bogus'" in m for m in msgs)
        assert any("Unknown worker_state='bogus'" in m for m in msgs)


class TestMissingAssetFallback:

    def test_missing_assets_dir_returns_transparent_pixmap(
        self, caplog, monkeypatch, tmp_path
    ):
        # Point the module at a directory that exists but contains no
        # icon PNGs — forces the `not path.exists()` branch in `_load`.
        empty_dir = tmp_path / "no_such_assets"
        empty_dir.mkdir()
        monkeypatch.setattr(qt_icons, "_ASSETS_DIR", empty_dir)

        with caplog.at_level(logging.WARNING, logger=qt_icons.logger.name):
            pm = qt_icons.get_icon("running", None)

        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)
        # Transparent blank — alpha 0 everywhere.
        assert _pixel(pm, 32, 32).alpha() == 0
        assert any(
            "Icon asset missing" in rec.getMessage()
            for rec in caplog.records
        )

    def test_missing_asset_does_not_raise_for_composite(
        self, monkeypatch, tmp_path
    ):
        empty_dir = tmp_path / "no_such_assets"
        empty_dir.mkdir()
        monkeypatch.setattr(qt_icons, "_ASSETS_DIR", empty_dir)
        # Should not raise — composite path must tolerate blank inputs.
        pm = qt_icons.get_icon("running", "rendering")
        assert isinstance(pm, QPixmap)
        assert (pm.width(), pm.height()) == (64, 64)


class TestCaching:

    def test_same_key_returns_same_object(self):
        first = qt_icons.get_icon("running", "rendering")
        second = qt_icons.get_icon("running", "rendering")
        assert first is second

    def test_single_axis_cached(self):
        first = qt_icons.get_icon("running", None)
        second = qt_icons.get_icon("running", None)
        assert first is second

    def test_different_keys_produce_different_entries(self):
        a = qt_icons.get_icon("running", "rendering")
        b = qt_icons.get_icon("error", "rendering")
        assert a is not b

    def test_none_pair_cached(self):
        first = qt_icons.get_icon(None, None)
        second = qt_icons.get_icon(None, None)
        assert first is second


class TestNeverRaises:

    @pytest.mark.parametrize(
        "manager_state, worker_state",
        [
            ("running", "idle"),
            ("running", "rendering"),
            ("error", "yielded"),
            (None, None),
            ("bogus", "bogus"),
            ("", ""),
            ("running", None),
            (None, "idle"),
            ("", "rendering"),
            ("running", ""),
        ],
    )
    def test_get_icon_never_raises(self, manager_state, worker_state):
        # Must not propagate any exception regardless of input shape.
        pm = qt_icons.get_icon(manager_state, worker_state)
        assert isinstance(pm, QPixmap)
        assert (pm.width(), pm.height()) == (64, 64)
