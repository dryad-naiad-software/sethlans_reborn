# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for the container setup-marker tests (issue #301).

Split out so ``test_container_setup_marker.py`` and
``test_container_setup_marker_write.py`` each stay under the 300-line
limit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from launcher import container_setup_marker as sm
from workers.services.sentinel import (
    SENTINEL_FILENAME,
    SENTINEL_VERSION,
    is_setup_complete,
)

LOGGER = "sethlans.container"


def marker_path(folder: Path) -> Path:
    return Path(folder) / SENTINEL_FILENAME


def ours(caplog, level):
    """Records the marker module logged at exactly ``level``."""
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == level]


def assert_completed(folder: Path) -> dict:
    """Assert ``folder`` holds a completed container marker; return it."""
    record = json.loads(marker_path(folder).read_text(encoding="utf-8"))
    assert record["version"] == SENTINEL_VERSION == 1
    assert record["topology"] == sm.CONTAINER_TOPOLOGY == "manager"
    assert record["checkpoints"] == []
    stamp = datetime.fromisoformat(record["completed_at"])
    assert stamp.tzinfo is not None
    assert is_setup_complete(folder) is True
    return record


def leftover_temp_files(folder: Path) -> list:
    return [p.name for p in Path(folder).iterdir() if p.name.startswith(".sentinel_")]
