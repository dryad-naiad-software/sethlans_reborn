# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Per-state QPixmap loading and composite generation for the tray icon.

Qt/PySide6-native replacement for :mod:`shared.tray.icons`. Behaviour is
intentionally identical to the Pillow version: assets live under
``shared/tray/assets/``; composite icons for the ``manager+worker``
topology are generated at runtime by splitting a 64x64 canvas diagonally
with the manager rendered in the upper-left triangle and the worker in
the lower-right triangle. Results are cached in memory.

All icon mutations must still happen on the Qt GUI thread per spec
FR-28; this module returns ``QPixmap`` objects and does NOT touch any
tray-icon state directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_CACHE: dict[tuple[Optional[str], Optional[str]], QPixmap] = {}

_MANAGER_STATES = {
    "starting", "running", "stopped", "error",
}
_WORKER_STATES = {
    "rendering", "yielding", "yielded", "idle",
}

_CANVAS_SIZE = 64


def _asset(prefix: str, state: str) -> Path:
    return _ASSETS_DIR / f"{prefix}_{state}.png"


def _blank_pixmap(color: QColor) -> QPixmap:
    pm = QPixmap(_CANVAS_SIZE, _CANVAS_SIZE)
    pm.fill(color)
    return pm


def _load(path: Path) -> QPixmap:
    if not path.exists():
        logger.warning("Icon asset missing: %s; using blank", path)
        return _blank_pixmap(QColor(0, 0, 0, 0))
    pm = QPixmap(str(path))
    if pm.isNull():
        logger.warning("Icon asset failed to load: %s; using blank", path)
        return _blank_pixmap(QColor(0, 0, 0, 0))
    return pm


def _triangle_path(points: list[tuple[float, float]]) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(QPointF(*points[0]))
    for pt in points[1:]:
        path.lineTo(QPointF(*pt))
    path.closeSubpath()
    return path


def _composite(manager_pm: QPixmap, worker_pm: QPixmap) -> QPixmap:
    """Return a 64x64 QPixmap split diagonally.

    Upper-left triangle = ``manager_pm``; lower-right = ``worker_pm``.
    """
    canvas = QPixmap(_CANVAS_SIZE, _CANVAS_SIZE)
    canvas.fill(Qt.GlobalColor.transparent)

    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        ul_path = _triangle_path([(0, 0), (_CANVAS_SIZE, 0), (0, _CANVAS_SIZE)])
        painter.setClipPath(ul_path)
        painter.drawPixmap(0, 0, manager_pm)

        lr_path = _triangle_path(
            [(_CANVAS_SIZE, 0), (_CANVAS_SIZE, _CANVAS_SIZE), (0, _CANVAS_SIZE)]
        )
        painter.setClipPath(lr_path)
        painter.drawPixmap(0, 0, worker_pm)
    finally:
        painter.end()

    return canvas


def get_icon(manager_state: Optional[str], worker_state: Optional[str]) -> QPixmap:
    """Return a 64x64 ``QPixmap`` for the given pair of states.

    Either argument may be ``None`` for single-topology trays.
    Results are cached by ``(manager_state, worker_state)``.
    """
    key = (manager_state, worker_state)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    if manager_state is not None and manager_state not in _MANAGER_STATES:
        logger.warning("Unknown manager_state=%r", manager_state)
        manager_state = "starting"
    if worker_state is not None and worker_state not in _WORKER_STATES:
        logger.warning("Unknown worker_state=%r", worker_state)
        worker_state = "idle"

    if manager_state and not worker_state:
        pm = _load(_asset("manager", manager_state))
    elif worker_state and not manager_state:
        pm = _load(_asset("worker", worker_state))
    elif manager_state and worker_state:
        pm = _composite(
            _load(_asset("manager", manager_state)),
            _load(_asset("worker", worker_state)),
        )
    else:
        pm = _blank_pixmap(QColor(128, 128, 128, 255))

    _CACHE[key] = pm
    return pm
