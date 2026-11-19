# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
macOS card probe (FR-CI-4).

Runs ``/usr/sbin/system_profiler SPDisplaysDataType -json`` (list form, no
shell, 15 s timeout, output capped at 1 MiB) and returns one ``OsCard`` per
GPU it lists, with the GPU's name and whether a display is listed under it.
The cards carry no key: macOS GPUs have no PCI address, and the inventory
forms the ``apple-gpu`` card only when there is exactly one GPU (D9). Any
failure returns ``None`` ("probe unavailable").

The output goes to a temporary file, so the cap bounds memory however much
the tool writes.
"""
import json
import logging
import subprocess
import tempfile
from typing import Optional, Tuple

from sethlans_worker_agent.cards.identity import clean_name
from sethlans_worker_agent.cards.os_probe import OsCard

logger = logging.getLogger(__name__)

COMMAND = ('/usr/sbin/system_profiler', 'SPDisplaysDataType', '-json')
TIMEOUT_SECONDS = 15
OUTPUT_CAP = 1024 * 1024


def _run() -> Optional[bytes]:
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(list(COMMAND), stdout=output, stderr=subprocess.DEVNULL,
                                       stdin=subprocess.DEVNULL, shell=False, timeout=TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("system_profiler could not run: %s", exc)
            return None
        if completed.returncode != 0:
            logger.debug("system_profiler exited with %s.", completed.returncode)
            return None
        output.seek(0)
        data = output.read(OUTPUT_CAP + 1)
    if len(data) > OUTPUT_CAP:
        logger.debug("system_profiler wrote more than %d bytes; ignored.", OUTPUT_CAP)
        return None
    return data


def _gpu(entry) -> Optional[OsCard]:
    if not isinstance(entry, dict):
        return None
    name = clean_name(entry.get('sppci_model') or entry.get('_name') or '')
    displays = entry.get('spdisplays_ndrvs')
    return OsCard(key=None, name=name or 'Apple GPU',
                  display=isinstance(displays, list) and len(displays) > 0)


def parse(data: bytes) -> Optional[Tuple[OsCard, ...]]:
    """The GPUs listed in ``system_profiler`` JSON output, or ``None`` when it is not readable."""
    try:
        document = json.loads(data.decode('utf-8', errors='replace'))
    except (ValueError, RecursionError):  # bad or deeply nested JSON
        return None
    entries = document.get('SPDisplaysDataType') if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return None
    return tuple(card for card in (_gpu(entry) for entry in entries) if card is not None)


def probe() -> Optional[Tuple[OsCard, ...]]:
    """The GPUs ``system_profiler`` lists, in its order; ``None`` on any failure."""
    data = _run()
    return parse(data) if data is not None else None
