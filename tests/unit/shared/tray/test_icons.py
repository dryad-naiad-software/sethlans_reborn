# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/icons.py``.

The icon scheme is binary: ``sethlans_idle.png`` (black anvil disc)
when the system is dormant, ``sethlans_active.png`` (green anvil
disc) when the manager is running OR a worker is actively
rendering / yielding. Everything else, including ``None``, empty
strings, and unknown values, falls through to idle.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for icons")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtGui import QPixmap  # noqa: E402

from shared.tray import icons  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_icon_cache(qapp):
    """Clear the module-level cache between tests; ensure QGuiApplication exists."""
    icons._CACHE.clear()
    yield
    icons._CACHE.clear()


def _is_active(pm: QPixmap) -> bool:
    """Return True iff ``pm`` is the cached active pixmap.

    Compares object identity against ``icons._CACHE['active']`` so the
    tests don't depend on per-pixel asset details (anti-aliasing,
    transparent corners, exact green hue). ``get_icon`` populates the
    cache lazily, so prime it first.
    """
    icons.get_icon("running", None)  # primes _CACHE['active']
    return pm is icons._CACHE.get("active")


# ---- Active selection -----------------------------------------------------

class TestActiveSelection:

    def test_manager_running_returns_active(self):
        pm = icons.get_icon("running", None)
        assert _is_active(pm)

    @pytest.mark.parametrize("worker_state", ["rendering", "yielding"])
    def test_worker_rendering_or_yielding_returns_active(self, worker_state):
        pm = icons.get_icon(None, worker_state)
        assert _is_active(pm)

    def test_active_when_either_axis_active(self):
        # Manager running + worker idle is still active (manager-up).
        pm = icons.get_icon("running", "idle")
        assert _is_active(pm)
        # Manager error + worker rendering is still active (real work
        # in progress, even if the manager-side reports an error).
        icons._CACHE.clear()
        pm = icons.get_icon("error", "rendering")
        assert _is_active(pm)


# ---- Idle selection -------------------------------------------------------

class TestIdleSelection:

    @pytest.mark.parametrize("manager_state", ["starting", "stopped", "error"])
    def test_inactive_manager_states_return_idle(self, manager_state):
        pm = icons.get_icon(manager_state, None)
        assert not _is_active(pm)

    @pytest.mark.parametrize("worker_state", ["idle", "yielded"])
    def test_inactive_worker_states_return_idle(self, worker_state):
        pm = icons.get_icon(None, worker_state)
        assert not _is_active(pm)

    def test_both_none_returns_idle(self):
        pm = icons.get_icon(None, None)
        assert not _is_active(pm)

    @pytest.mark.parametrize(
        "manager_state, worker_state",
        [("bogus", "bogus"), ("", ""), ("bogus", None), (None, "bogus")],
    )
    def test_unknown_states_silently_fall_through_to_idle(
        self, manager_state, worker_state,
    ):
        pm = icons.get_icon(manager_state, worker_state)
        assert not _is_active(pm)


# ---- Pixmap shape contract ------------------------------------------------

class TestPixmapShape:

    @pytest.mark.parametrize(
        "manager_state, worker_state",
        [
            ("running", None),
            (None, "rendering"),
            ("running", "rendering"),
            ("error", None),
            (None, "idle"),
            (None, None),
        ],
    )
    def test_returns_64x64_qpixmap(self, manager_state, worker_state):
        pm = icons.get_icon(manager_state, worker_state)
        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)

    def test_active_and_idle_pixmaps_differ(self):
        active = icons.get_icon("running", None)
        idle = icons.get_icon("stopped", None)
        # Pixel-level comparison via raw bytes — the active (green) and
        # idle (black) anvil discs are visually distinct, so their
        # 64x64 pixel buffers must differ.
        assert active.toImage().bits().tobytes() != idle.toImage().bits().tobytes()


# ---- Caching --------------------------------------------------------------

class TestCaching:

    def test_same_state_returns_same_object(self):
        first = icons.get_icon("running", "rendering")
        second = icons.get_icon("running", "rendering")
        assert first is second

    def test_distinct_active_inputs_share_one_active_pixmap(self):
        # All "active" inputs collapse to a single cached pixmap —
        # the binary scheme has only two possible icons.
        a = icons.get_icon("running", None)
        b = icons.get_icon(None, "rendering")
        c = icons.get_icon("running", "rendering")
        assert a is b is c

    def test_distinct_idle_inputs_share_one_idle_pixmap(self):
        a = icons.get_icon("stopped", None)
        b = icons.get_icon("error", "yielded")
        c = icons.get_icon(None, None)
        assert a is b is c

    def test_active_and_idle_are_distinct_cache_entries(self):
        active = icons.get_icon("running", None)
        idle = icons.get_icon("stopped", None)
        assert active is not idle


# ---- Missing-asset fallback ----------------------------------------------

class TestMissingAssetFallback:

    def test_missing_asset_logs_and_returns_blank_pixmap(
        self, caplog, monkeypatch, tmp_path,
    ):
        empty_dir = tmp_path / "no_such_assets"
        empty_dir.mkdir()
        monkeypatch.setattr(icons, "_ASSETS_DIR", empty_dir)

        with caplog.at_level(logging.WARNING, logger=icons.logger.name):
            pm = icons.get_icon("running", None)

        assert isinstance(pm, QPixmap)
        assert not pm.isNull()
        assert (pm.width(), pm.height()) == (64, 64)
        # Blank fallback is fully transparent.
        assert pm.toImage().pixelColor(32, 32).alpha() == 0
        assert any(
            "Icon asset missing" in rec.getMessage()
            for rec in caplog.records
        )


# ---- Never raises ---------------------------------------------------------

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
        pm = icons.get_icon(manager_state, worker_state)
        assert isinstance(pm, QPixmap)
        assert (pm.width(), pm.height()) == (64, 64)
