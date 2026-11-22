# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The graphics driver version UE logged for its D3D12 adapters (per-machine
memory test D3, FR-MT-V-1).

UE 5.8.4 ``Runtime/D3D12RHI/Private/Windows/WindowsD3D12Device.cpp``
enumerates every D3D12 adapter and logs, per adapter:

* ``:873`` ``Found D3D12 adapter %u: %ls (VendorId: ...`` (the block start;
  ``pilot.FOUND_ADAPTER``);
* ``:904`` ``  Driver Version: %ls (internal:%ls, unified:%ls)`` for every
  adapter that is not WARP; the Microsoft Basic Render Driver logs a
  ``D3D10Warp Version`` line instead and has no driver line;
* after the loop, ``:942`` ``Chosen D3D12 Adapter Id = %u``
  (``pilot.CHOSEN_ADAPTER``).

The value is ``FPlatformMisc::GetGPUDriverInfo`` looked up by adapter name,
so two identical cards log equal lines. A failed lookup logs the literal
``Unknown`` (UE 5.8.4 ``WindowsPlatformMisc.cpp:3799-3801``, the initial
value, and ``:3950-3952``, reset when the name matches more or fewer than one
driver and the lookup falls back to the primary adapter), which is null here.

Rules:

* every ``Found`` adapter is a key of ``driver_versions``; its value is the
  ``internal:`` value of the first ``LogD3D12RHI:   Driver Version:`` line
  after it and before the next ``Found`` line or the ``Chosen`` line, and
  ``None`` when there is none (Basic Render Driver) or the value is
  ``Unknown``;
* a driver line outside a block (before the first ``Found`` line, or after
  the ``Chosen`` line, such as ``LogRHI``'s "RHI Adapter Info" copy) counts
  for nothing;
* an adapter index or a ``Chosen`` line seen again keeps the first one,
  like ``pilot.chosen_adapter``;
* every line is anchored at its start (UE's optional ``[time][frame]``
  prefix, ``pilot._UE_PREFIX``) and values are bounded, so a project ini
  value that UE echoes inside another line never matches; an ``internal:``
  value longer than 40 characters or outside ``[0-9A-Za-z.-]`` does not
  match either, so that adapter has no driver (null) instead of a cut value.

``launch_driver(render_log, warmup_log)`` reads the render launch's log and
the warm-up log only when the render log has no ``Found`` line (logged).
A missing or ``Unknown`` driver is logged at WARNING; the caller reports it
as null (summary ``driver_version=unknown``).
"""
import logging
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from sethlans_worker_agent.unreal_executor.log_tail import iter_log_lines
from sethlans_worker_agent.unreal_executor.pilot import _UE_PREFIX, CHOSEN_ADAPTER, FOUND_ADAPTER

logger = logging.getLogger(__name__)

UNKNOWN_DRIVER = 'Unknown'  # WindowsPlatformMisc.cpp:3799-3801 and :3950-3952 (UE 5.8.4), a failed driver lookup
DRIVER_VERSION = re.compile(_UE_PREFIX + r'LogD3D12RHI:   Driver Version: .{0,200}? '
                            r'\(internal:([0-9A-Za-z.\-]{1,40}), unified:', re.ASCII)
SOURCE_RENDER = 'render'
SOURCE_WARMUP = 'warmup'


@dataclass(frozen=True)
class DriverReading:
    """The chosen adapter's driver for one launch.

    ``version`` is the ``internal:`` value, ``None`` when unknown. ``source`` is the log it came
    from (``render`` or ``warmup``), ``None`` when neither log has a ``Found`` line.
    """
    version: Optional[str]
    source: Optional[str]


def _scan(log_path) -> Tuple[Dict[int, Optional[str]], Optional[int]]:
    """Raw ``internal:`` value per ``Found`` adapter (``Unknown`` kept) and the chosen index.

    Raises ``OSError`` when the log cannot be opened (``iter_log_lines``).
    """
    found: Dict[int, Optional[str]] = {}
    chosen: Optional[int] = None
    block: Optional[int] = None  # the adapter whose block is open; None outside any block
    for line in iter_log_lines(log_path):
        match = FOUND_ADAPTER.search(line)
        if match:
            index = int(match.group(1))
            block = None if index in found else index  # a repeated index keeps its first block
            found.setdefault(index, None)
            continue
        match = CHOSEN_ADAPTER.search(line)
        if match:
            block = None
            if chosen is None:
                chosen = int(match.group(1))
            continue
        match = DRIVER_VERSION.search(line) if block is not None else None
        if match:
            found[block] = match.group(1)
            block = None  # only the first driver line of a block counts
    return found, chosen


def _known(value: Optional[str]) -> Optional[str]:
    return None if value == UNKNOWN_DRIVER else value


def driver_versions(log_path) -> Dict[int, Optional[str]]:
    """Adapter index -> its ``internal:`` driver version, ``None`` without a driver line or for ``Unknown``."""
    found, _ = _scan(log_path)
    return {index: _known(value) for index, value in found.items()}


def chosen_driver(log_path) -> Optional[str]:
    """The ``internal:`` driver version of the adapter UE chose, or None when the log does not say."""
    found, chosen = _scan(log_path)
    return _known(found.get(chosen)) if chosen is not None else None


def _read(log_path, what: str) -> Tuple[Dict[int, Optional[str]], Optional[int]]:
    try:
        return _scan(log_path)
    except OSError as exc:
        logger.warning("Could not read the %s log for the driver version (%s): %s", what, log_path, exc)
        return {}, None


def _why_unknown(found: Dict[int, Optional[str]], chosen: Optional[int]) -> str:
    if not found:
        return "no D3D12 adapter lines in the log"
    if chosen is None:
        return "no 'Chosen D3D12 Adapter Id' line in the log"
    if chosen not in found:
        return f"no 'Found D3D12 adapter {chosen}' line for the chosen adapter"
    if found[chosen] == UNKNOWN_DRIVER:
        return "Unreal logged the driver version as Unknown"
    return f"no driver line for the chosen adapter {chosen}"


def launch_driver(render_log, warmup_log=None) -> DriverReading:
    """FR-MT-V-1: the chosen adapter's driver from the render log, else from the warm-up log."""
    found, chosen = _read(render_log, SOURCE_RENDER)
    source = SOURCE_RENDER if found else None
    if not found and warmup_log is not None:
        logger.info("The render log has no D3D12 adapter lines; reading the driver version from the warm-up log.")
        found, chosen = _read(warmup_log, SOURCE_WARMUP)
        source = SOURCE_WARMUP if found else None
    version = _known(found.get(chosen)) if chosen is not None else None
    if version is None:
        logger.warning("The graphics driver version is unknown (%s); it is reported as null.",
                       _why_unknown(found, chosen))
    return DriverReading(version=version, source=source)
