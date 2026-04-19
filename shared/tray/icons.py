# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Per-state PNG loading and composite generation for the tray icon.

Assets live in ``shared/tray/assets/``.  Composite icons (used in the
``manager+worker`` topology) are generated at runtime: the 64x64
canvas is split diagonally, the manager state rendered upper-left and
the worker state lower-right.  Results are cached in memory.

All icon mutations must happen on the pystray main thread per spec
FR-28; this module returns PIL ``Image`` objects — it does NOT touch
``icon.icon``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - runtime dep
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_CACHE: dict[tuple[Optional[str], Optional[str]], object] = {}

_MANAGER_STATES = {
    "starting", "running", "stopped", "error",
}
_WORKER_STATES = {
    "rendering", "yielding", "yielded", "idle",
}


def _asset(prefix: str, state: str) -> Path:
    return _ASSETS_DIR / f"{prefix}_{state}.png"


def _load(path: Path):
    if Image is None:
        raise RuntimeError("Pillow is required for tray icons")
    if not path.exists():
        logger.warning("Icon asset missing: %s; using blank", path)
        return Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    return Image.open(path).convert("RGBA")


def _composite(manager_img, worker_img):
    """Return a 64x64 image split diagonally.

    Upper-left triangle = manager_img; lower-right = worker_img.
    """
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow is required for tray icons")
    canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    # Upper-left triangle mask.
    ul_mask = Image.new("L", (64, 64), 0)
    draw_ul = ImageDraw.Draw(ul_mask)
    draw_ul.polygon([(0, 0), (64, 0), (0, 64)], fill=255)
    # Lower-right triangle mask.
    lr_mask = Image.new("L", (64, 64), 0)
    draw_lr = ImageDraw.Draw(lr_mask)
    draw_lr.polygon([(64, 0), (64, 64), (0, 64)], fill=255)
    canvas.paste(manager_img, (0, 0), ul_mask)
    canvas.paste(worker_img, (0, 0), lr_mask)
    return canvas


def get_icon(manager_state: Optional[str], worker_state: Optional[str]):
    """Return a PIL ``Image`` for the given pair of states.

    Either argument may be ``None`` for single-topology trays.
    Results are cached by (manager_state, worker_state).
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
        img = _load(_asset("manager", manager_state))
    elif worker_state and not manager_state:
        img = _load(_asset("worker", worker_state))
    elif manager_state and worker_state:
        img = _composite(
            _load(_asset("manager", manager_state)),
            _load(_asset("worker", worker_state)),
        )
    else:
        if Image is None:
            raise RuntimeError("Pillow is required for tray icons")
        img = Image.new("RGBA", (64, 64), (128, 128, 128, 255))
    _CACHE[key] = img
    return img
