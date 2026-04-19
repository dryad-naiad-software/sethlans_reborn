# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Sethlans-branded binary tray-icon loader.

Two icons live under ``shared/tray/assets/``:

* ``sethlans_idle.png``   — black anvil disc (system idle / not rendering)
* ``sethlans_active.png`` — green anvil disc (manager running OR worker
                            actively rendering / yielding)

``get_icon`` returns the appropriate ``QPixmap`` for a given
``(manager_state, worker_state)`` pair. Either argument may be ``None``
for single-topology trays. Results are cached in memory.

All icon mutations must still happen on the Qt GUI thread per spec
FR-28; this module returns ``QPixmap`` objects and does NOT touch any
tray-icon state directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_CANVAS_SIZE = 64

# Cache key is the literal "active" / "idle" string — there are only
# two possible icons under the binary scheme.
_CACHE: dict[str, QPixmap] = {}

# States that count as "active" (renders the green anvil). Anything
# else — including ``None``, "" or unknown values — falls through to
# the idle icon.
_ACTIVE_MANAGER_STATES = frozenset({"running"})
_ACTIVE_WORKER_STATES = frozenset({"rendering", "yielding"})


def _blank_pixmap() -> QPixmap:
    pm = QPixmap(_CANVAS_SIZE, _CANVAS_SIZE)
    pm.fill(QColor(0, 0, 0, 0))
    return pm


def _load_scaled(path: Path) -> QPixmap:
    if not path.exists():
        logger.warning("Icon asset missing: %s; using blank", path)
        return _blank_pixmap()
    pm = QPixmap(str(path))
    if pm.isNull():
        logger.warning("Icon asset failed to load: %s; using blank", path)
        return _blank_pixmap()
    # IgnoreAspectRatio forces an exact CANVAS_SIZE x CANVAS_SIZE
    # result. The source PNGs are 477x476 (effectively square); the
    # 1-pixel asymmetry would otherwise round to 64x63 under
    # KeepAspectRatio, which downstream tests + the OS tray expect as
    # a perfect square.
    return pm.scaled(
        _CANVAS_SIZE, _CANVAS_SIZE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _is_active(
    manager_state: Optional[str], worker_state: Optional[str],
) -> bool:
    if manager_state in _ACTIVE_MANAGER_STATES:
        return True
    if worker_state in _ACTIVE_WORKER_STATES:
        return True
    return False


def get_icon(
    manager_state: Optional[str], worker_state: Optional[str],
) -> QPixmap:
    """Return the Sethlans tray icon for the given state pair.

    Returns the green "active" anvil iff the manager is running or the
    worker is rendering / yielding. Otherwise returns the black "idle"
    anvil. Either argument may be ``None``; unknown values fall
    through to idle without raising.
    """
    key = "active" if _is_active(manager_state, worker_state) else "idle"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pm = _load_scaled(_ASSETS_DIR / f"sethlans_{key}.png")
    _CACHE[key] = pm
    return pm
