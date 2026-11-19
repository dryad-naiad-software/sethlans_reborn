# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Top-level test fixtures shared across all test stages.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_real_screen_read(monkeypatch):
    """No in-process test reads the real screen marks (DXGI outputs, DRM connectors).

    The worker's card checks and card pages read which card drives a screen
    (``cards.screens``). On a machine whose real card shares a fake card's
    PCI address, the real read would add marks and warnings to any test.
    Here every mark reads as unknown; the screen tests call the real
    ``_platform_marks`` they took at import.
    """
    from sethlans_worker_agent.cards import screens
    monkeypatch.setattr(screens, '_platform_marks', lambda platform_name, inventory: None)
    yield
