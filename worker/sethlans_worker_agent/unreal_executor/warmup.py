# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Warm-up marker (Unreal foundation FR-UE-9, FR-UE-10, FR-UE-25; Dom U-5,
U-6, N-12; Conc L4).

The marker ``p/<key16>/warmup/<ov16>/<build>.json`` holds::

    {"warmed": [{"map": ..., "sequence": ...}], "farm_defaults_version": 2,
     "cvars_sha256": "...", "completed_at": ...}

Reads and read-modify-writes happen under the **marker lock**, one
``threading.Lock`` per marker path (the bottom of the FR-PA-10 hierarchy:
nothing else is taken while it is held), with an atomic replace.

A normal job warms up iff its (map, sequence) pair is absent, the
``farm_defaults_version`` differs, or ``cvars_sha256`` differs from the
SHA-256 of the job's canonical merged CVar list; a mismatch replaces the
``warmed`` list. A pilot **always** warms up (FR-UE-25) and records the
pair on success like a normal warm-up. FR-UE-10's retry removes the pair
first. The marker directory sits beside ``tree/`` (the integrity check
walks ``tree/`` only). An unreadable or oversized marker reads as empty.
"""
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import farm_defaults

logger = logging.getLogger(__name__)

NO_OVERLAY_OV16 = '0' * 16
MAX_MARKER_BYTES = 256 * 1024
MAX_PAIRS = 1000

_locks_guard = threading.Lock()  # leaf lock over the dict below
_locks: Dict[str, threading.Lock] = {}


def marker_path(entry_dir, overlay_sha256: str, build: str) -> Path:
    ov16 = overlay_sha256[:16] if overlay_sha256 else NO_OVERLAY_OV16
    return Path(entry_dir) / 'warmup' / ov16 / f"{build}.json"


def _lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def _read(path: Path) -> dict:
    try:
        st = fs_safe.lstat(path)
        if fs_safe.is_reparse(st) or st.st_size > MAX_MARKER_BYTES:
            return {}
        with open(fs_safe.long_path(path), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pairs(data: dict) -> List[dict]:
    raw = data.get('warmed')
    if not isinstance(raw, list):
        return []
    return [{'map': item['map'], 'sequence': item['sequence']} for item in raw
            if isinstance(item, dict) and isinstance(item.get('map'), str) and isinstance(item.get('sequence'), str)]


def _current(data: dict, cvars_sha: str) -> bool:
    return (data.get('farm_defaults_version') == farm_defaults.FARM_DEFAULTS_VERSION
            and data.get('cvars_sha256') == cvars_sha)


def _write(path: Path, data: dict) -> None:
    for parent in (path.parent.parent, path.parent):
        fs_safe.ensure_owned_dir(parent)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        with open(fs_safe.long_path(tmp), 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        os.replace(fs_safe.long_path(tmp), fs_safe.long_path(path))
    finally:
        if fs_safe.lexists(tmp):
            os.unlink(fs_safe.long_path(tmp))


def needs_warmup(path, map_path: str, sequence: str, cvars_sha: str) -> bool:
    """FR-UE-9 "When it runs" for a normal job."""
    path = Path(path)
    with _lock_for(path):
        data = _read(path)
    if not _current(data, cvars_sha):
        return True
    return {'map': map_path, 'sequence': sequence} not in _pairs(data)


def record_success(path, map_path: str, sequence: str, cvars_sha: str, clock=time.time) -> None:
    """Append the pair (replacing a stale list). Failures are logged, never raised."""
    path = Path(path)
    try:
        with _lock_for(path):
            data = _read(path)
            pairs = _pairs(data) if _current(data, cvars_sha) else []
            pair = {'map': map_path, 'sequence': sequence}
            if pair not in pairs:
                pairs.append(pair)
            _write(path, {'warmed': pairs[-MAX_PAIRS:], 'farm_defaults_version': farm_defaults.FARM_DEFAULTS_VERSION,
                          'cvars_sha256': cvars_sha, 'completed_at': clock()})
    except OSError as exc:
        logger.warning("Could not update the warm-up marker %s: %s", path, exc)


def remove_pair(path, map_path: str, sequence: str) -> None:
    """FR-UE-10 step 1; failures are logged, never raised."""
    path = Path(path)
    try:
        with _lock_for(path):
            data = _read(path)
            if not data:
                return
            pairs = [p for p in _pairs(data) if p != {'map': map_path, 'sequence': sequence}]
            _write(path, dict(data, warmed=pairs))
    except OSError as exc:
        logger.warning("Could not update the warm-up marker %s: %s", path, exc)


def decide(path, params, cvars_sha: str) -> Optional[str]:
    """None when the warm-up can be skipped, else why it runs (logged)."""
    if params.is_pilot:
        return 'pilot (always warms up)'
    if needs_warmup(path, params.map, params.sequence, cvars_sha):
        return 'pair not warmed for these farm defaults and CVars'
    return None
